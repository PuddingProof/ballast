"""Run the eval + improve loop until all train queries pass or max iterations reached.

Forks run_loop.py (orchestrator) + run_eval.py (trigger evaluator) from skill-creator.
The description optimizer: train/test split, 3-rep trigger-rate eval, propose-improve
loop, picks best description by held-out test score.

SOURCE: skill-creator/scripts/run_loop.py + run_eval.py
WINDOWS-ADAPTED:
  - Removed `select.select()` / `os.read()` non-blocking I/O from run_single_query —
    select.select() on Windows only works on sockets, NOT file handles (subprocess pipes).
    Replaced with a threading-based approach: a reader thread drains stdout into a queue
    while the main thread polls the process with a timeout.
  - Removed nohup / & / /dev/null (were never present in these scripts but mentioned as
    a class of bash-ism to watch for — confirmed none exist here).
  - CLAUDECODE env-var stripping preserved (needed on Windows too when nested inside
    a running Claude Code session).
  - ProcessPoolExecutor used for parallel eval workers — works on Windows but requires
    the if __name__ == '__main__': guard (present in main()) and picklable callables.
    run_single_query is a module-level function so it IS picklable. However on Windows,
    ProcessPoolExecutor spawns new interpreter processes via 'spawn' (not 'fork'),
    which means each worker re-imports this module. That's fine — no side effects at
    import time.
  - webbrowser.open() used for the live report — works on Windows (opens default browser).
  - tempfile.gettempdir() returns a Windows temp path (e.g. C:/Users/.../AppData/Local/Temp).
    NOTE: forward slashes in this docstring are deliberate — a literal backslash path here
    (C:\\Users...) makes Python read \\U as a truncated unicode escape and the module won't import.
    Path.write_text / read_text used throughout with explicit encoding="utf-8".
  - Model is a required CLI arg (--model), never hardcoded. Caller passes the session
    model id (e.g. claude-sonnet-4-5).
  - 'python' used (not 'python3') in any subprocess calls via the generate_report module.
UNVERIFIED:
  - The `claude -p` subprocess in run_single_query: requires `claude` CLI on PATH and
    authenticated. Works inside a Claude Code session (inherits session auth). Has NOT
    been end-to-end tested on Windows — a live test is needed to confirm the stream-JSON
    parsing logic fires correctly, especially the early-exit path.
  - ProcessPoolExecutor on Windows with many workers (default 10): if Claude Code's
    parent process has low handle limits, spawning 10 child interpreters may be slow or
    hit limits. Reduce --num-workers if you see hangs or OSError: [WinError 87].
  - Non-blocking stdout read: the threading reader works but adds ~1 process thread per
    concurrent query. For the default 3 runs x N queries this is fine; at very high
    concurrency monitor memory.
"""

import argparse
import json
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from scripts.generate_report import generate_html
from scripts.improve_description import improve_description
from scripts.utils import parse_skill_md


# ---------------------------------------------------------------------------
# Project-root detection (from run_eval.py)
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    """Find the project root by walking up from cwd looking for a .claude/ directory.

    Mirrors how Claude Code discovers its project root so that any command file
    we create in .claude/commands/ is seen by `claude -p`.
    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".claude").is_dir():
            return parent
    # Fall back to cwd if no .claude/ found
    return current


# ---------------------------------------------------------------------------
# Single-query trigger evaluator (from run_eval.py, Windows-adapted)
# ---------------------------------------------------------------------------

def _drain_stdout(proc_stdout, line_queue: queue.Queue) -> None:
    """Reader thread: drain proc stdout line-by-line into a queue.

    Runs in a daemon thread so it dies when the main thread exits.
    Puts None as a sentinel when the stream closes.
    """
    try:
        for raw_line in proc_stdout:
            line_queue.put(raw_line)
    finally:
        line_queue.put(None)  # sentinel: stream exhausted


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
) -> bool:
    """Run a single `claude -p` query and return whether the skill was triggered.

    Creates a temporary command file in .claude/commands/ so the skill appears
    in Claude's available_skills list, then runs `claude -p` with stream-JSON
    output and inspects the event stream for a Skill or Read tool call that
    names our command file.

    Windows adaptation: replaced select.select() + os.read() (not supported on
    Windows pipe handles) with a daemon reader thread + queue.Queue with timeouts.
    """
    unique_id = uuid.uuid4().hex[:8]
    clean_name = f"{skill_name}-skill-{unique_id}"
    project_commands_dir = Path(project_root) / ".claude" / "commands"
    command_file = project_commands_dir / f"{clean_name}.md"

    try:
        project_commands_dir.mkdir(parents=True, exist_ok=True)

        # Use YAML block scalar for the description to avoid quoting issues
        indented_desc = "\n  ".join(skill_description.split("\n"))
        command_content = (
            f"---\n"
            f"description: |\n"
            f"  {indented_desc}\n"
            f"---\n\n"
            f"# {skill_name}\n\n"
            f"This skill handles: {skill_description}\n"
        )
        command_file.write_text(command_content, encoding="utf-8")

        cmd = [
            "claude",
            "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if model:
            cmd.extend(["--model", model])

        # Strip CLAUDECODE so that nesting `claude -p` inside a Claude Code session
        # doesn't hit the interactive-terminal guard.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=project_root,
            env=env,
            # On Windows, text=False gives us bytes; we decode per line below.
            text=False,
        )

        # --- Windows-safe non-blocking read via reader thread + queue ---
        line_queue: queue.Queue = queue.Queue()
        reader = threading.Thread(
            target=_drain_stdout,
            args=(process.stdout, line_queue),
            daemon=True,  # dies with parent — no cleanup needed
        )
        reader.start()

        triggered = False
        start_time = time.time()
        # Track streaming tool-use state for early detection
        pending_tool_name = None
        accumulated_json = ""

        try:
            while True:
                elapsed = time.time() - start_time
                remaining = timeout - elapsed
                if remaining <= 0:
                    break  # timeout reached

                try:
                    # Block at most 1 second so we can check the timeout loop
                    raw_line = line_queue.get(timeout=min(1.0, remaining))
                except queue.Empty:
                    # No line yet; check if process has exited
                    if process.poll() is not None:
                        break
                    continue

                if raw_line is None:
                    # Sentinel: stream closed
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # --- Early detection via streaming events ---
                if event.get("type") == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type", "")

                    if se_type == "content_block_start":
                        cb = se.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            tool_name = cb.get("name", "")
                            if tool_name in ("Skill", "Read"):
                                # Potential trigger — start accumulating the tool input JSON
                                pending_tool_name = tool_name
                                accumulated_json = ""
                            else:
                                # First tool is something else — skill was not triggered
                                return False

                    elif se_type == "content_block_delta" and pending_tool_name:
                        delta = se.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            accumulated_json += delta.get("partial_json", "")
                            # Early exit: we've already seen our unique command name
                            if clean_name in accumulated_json:
                                return True

                    elif se_type in ("content_block_stop", "message_stop"):
                        if pending_tool_name:
                            return clean_name in accumulated_json
                        if se_type == "message_stop":
                            return False

                # --- Fallback: full assistant message (non-streaming path) ---
                elif event.get("type") == "assistant":
                    message = event.get("message", {})
                    for content_item in message.get("content", []):
                        if content_item.get("type") != "tool_use":
                            continue
                        tool_name = content_item.get("name", "")
                        tool_input = content_item.get("input", {})
                        if tool_name == "Skill" and clean_name in tool_input.get("skill", ""):
                            triggered = True
                        elif tool_name == "Read" and clean_name in tool_input.get("file_path", ""):
                            triggered = True
                        return triggered

                elif event.get("type") == "result":
                    return triggered

        finally:
            # Ensure subprocess is dead on any exit path (normal, exception, timeout)
            if process.poll() is None:
                process.kill()
                process.wait()

        return triggered

    finally:
        # Always clean up the temporary command file
        if command_file.exists():
            command_file.unlink()


# ---------------------------------------------------------------------------
# Batch evaluator (from run_eval.py)
# ---------------------------------------------------------------------------

def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    project_root: Path,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: str | None = None,
) -> dict:
    """Run the full eval set in parallel and return results.

    Uses ProcessPoolExecutor so each `claude -p` call is fully isolated.
    On Windows this uses 'spawn' semantics — see module-level WINDOWS-ADAPTED note.
    """
    results = []

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    description,
                    timeout,
                    str(project_root),
                    model,
                )
                future_to_info[future] = (item, run_idx)

        # Accumulate trigger counts per query across repeated runs
        query_triggers: dict[str, list[bool]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            if query not in query_triggers:
                query_triggers[query] = []
            try:
                query_triggers[query].append(future.result())
            except Exception as e:
                print(f"Warning: query failed: {e}", file=sys.stderr)
                query_triggers[query].append(False)

    # Build per-query result dicts with pass/fail determination
    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        # Pass = correct direction at the threshold
        if should_trigger:
            did_pass = trigger_rate >= trigger_threshold
        else:
            did_pass = trigger_rate < trigger_threshold
        results.append({
            "query": query,
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": sum(triggers),
            "runs": len(triggers),
            "pass": did_pass,
        })

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


# ---------------------------------------------------------------------------
# Train / test split (from run_loop.py)
# ---------------------------------------------------------------------------

def split_eval_set(
    eval_set: list[dict],
    holdout: float,
    seed: int = 42,
) -> tuple[list[dict], list[dict]]:
    """Split eval set into train and test sets, stratified by should_trigger.

    Stratification ensures both splits have roughly the same positive/negative ratio.
    """
    random.seed(seed)

    trigger = [e for e in eval_set if e["should_trigger"]]
    no_trigger = [e for e in eval_set if not e["should_trigger"]]

    random.shuffle(trigger)
    random.shuffle(no_trigger)

    # At least one sample per class in the test set
    n_trigger_test = max(1, int(len(trigger) * holdout))
    n_no_trigger_test = max(1, int(len(no_trigger) * holdout))

    test_set = trigger[:n_trigger_test] + no_trigger[:n_no_trigger_test]
    train_set = trigger[n_trigger_test:] + no_trigger[n_no_trigger_test:]

    return train_set, test_set


# ---------------------------------------------------------------------------
# Main optimization loop (from run_loop.py)
# ---------------------------------------------------------------------------

def run_loop(
    eval_set: list[dict],
    skill_path: Path,
    description_override: str | None,
    num_workers: int,
    timeout: int,
    max_iterations: int,
    runs_per_query: int,
    trigger_threshold: float,
    holdout: float,
    model: str,
    verbose: bool,
    live_report_path: Path | None = None,
    log_dir: Path | None = None,
) -> dict:
    """Run the eval + improvement loop, returning the best description found.

    Algorithm:
      1. Optionally split eval_set into train (seen by optimizer) + test (held-out).
      2. Each iteration: run_eval on all queries in parallel, split results back.
      3. If all train queries pass, stop early.
      4. Otherwise call improve_description (which calls `claude -p`) on train failures.
      5. After all iterations, pick the description with the best held-out test score.

    Returns a dict suitable for generate_html() / JSON output.
    """
    project_root = find_project_root()
    name, original_description, content = parse_skill_md(skill_path)
    current_description = description_override or original_description

    # Optionally hold out a stratified test split to detect overfitting
    if holdout > 0:
        train_set, test_set = split_eval_set(eval_set, holdout)
        if verbose:
            print(
                f"Split: {len(train_set)} train, {len(test_set)} test (holdout={holdout})",
                file=sys.stderr,
            )
    else:
        train_set = eval_set
        test_set = []

    history = []
    exit_reason = "unknown"

    for iteration in range(1, max_iterations + 1):
        if verbose:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"Iteration {iteration}/{max_iterations}", file=sys.stderr)
            print(f"Description: {current_description}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)

        # Evaluate train + test in one batched parallel pass for efficiency
        all_queries = train_set + test_set
        t0 = time.time()
        all_results = run_eval(
            eval_set=all_queries,
            skill_name=name,
            description=current_description,
            num_workers=num_workers,
            timeout=timeout,
            project_root=project_root,
            runs_per_query=runs_per_query,
            trigger_threshold=trigger_threshold,
            model=model,
        )
        eval_elapsed = time.time() - t0

        # Re-split results back into train / test by query membership
        train_queries_set = {q["query"] for q in train_set}
        train_result_list = [r for r in all_results["results"] if r["query"] in train_queries_set]
        test_result_list = [r for r in all_results["results"] if r["query"] not in train_queries_set]

        train_passed = sum(1 for r in train_result_list if r["pass"])
        train_total = len(train_result_list)
        train_summary = {
            "passed": train_passed,
            "failed": train_total - train_passed,
            "total": train_total,
        }
        train_results = {"results": train_result_list, "summary": train_summary}

        if test_set:
            test_passed = sum(1 for r in test_result_list if r["pass"])
            test_total = len(test_result_list)
            test_summary = {
                "passed": test_passed,
                "failed": test_total - test_passed,
                "total": test_total,
            }
            test_results = {"results": test_result_list, "summary": test_summary}
        else:
            test_results = None
            test_summary = None

        # Record this iteration's full state in history
        history.append({
            "iteration": iteration,
            "description": current_description,
            "train_passed": train_summary["passed"],
            "train_failed": train_summary["failed"],
            "train_total": train_summary["total"],
            "train_results": train_results["results"],
            "test_passed": test_summary["passed"] if test_summary else None,
            "test_failed": test_summary["failed"] if test_summary else None,
            "test_total": test_summary["total"] if test_summary else None,
            "test_results": test_results["results"] if test_results else None,
            # Backward-compat keys expected by generate_html
            "passed": train_summary["passed"],
            "failed": train_summary["failed"],
            "total": train_summary["total"],
            "results": train_results["results"],
        })

        # Write a live auto-refreshing HTML report after each iteration
        if live_report_path:
            partial_output = {
                "original_description": original_description,
                "best_description": current_description,
                "best_score": "in progress",
                "iterations_run": len(history),
                "holdout": holdout,
                "train_size": len(train_set),
                "test_size": len(test_set),
                "history": history,
            }
            live_report_path.write_text(
                generate_html(partial_output, auto_refresh=True, skill_name=name),
                encoding="utf-8",
            )

        if verbose:
            def print_eval_stats(label: str, results: list[dict], elapsed: float) -> None:
                """Print precision / recall / accuracy for a result set."""
                pos = [r for r in results if r["should_trigger"]]
                neg = [r for r in results if not r["should_trigger"]]
                tp = sum(r["triggers"] for r in pos)
                pos_runs = sum(r["runs"] for r in pos)
                fn = pos_runs - tp
                fp = sum(r["triggers"] for r in neg)
                neg_runs = sum(r["runs"] for r in neg)
                tn = neg_runs - fp
                total = tp + tn + fp + fn
                precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
                accuracy = (tp + tn) / total if total > 0 else 0.0
                print(
                    f"{label}: {tp+tn}/{total} correct, "
                    f"precision={precision:.0%} recall={recall:.0%} accuracy={accuracy:.0%} "
                    f"({elapsed:.1f}s)",
                    file=sys.stderr,
                )
                for r in results:
                    status = "PASS" if r["pass"] else "FAIL"
                    rate_str = f"{r['triggers']}/{r['runs']}"
                    print(
                        f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:60]}",
                        file=sys.stderr,
                    )

            print_eval_stats("Train", train_results["results"], eval_elapsed)
            if test_summary:
                print_eval_stats("Test ", test_results["results"], 0)

        # Early stop: all train queries passed
        if train_summary["failed"] == 0:
            exit_reason = f"all_passed (iteration {iteration})"
            if verbose:
                print(f"\nAll train queries passed on iteration {iteration}!", file=sys.stderr)
            break

        if iteration == max_iterations:
            exit_reason = f"max_iterations ({max_iterations})"
            if verbose:
                print(f"\nMax iterations reached ({max_iterations}).", file=sys.stderr)
            break

        # --- Propose an improved description based on train failures ---
        if verbose:
            print("\nImproving description...", file=sys.stderr)

        t0 = time.time()
        # Strip test scores from history before passing to improve_description
        # so the optimizer can't "see" the held-out scores and overfit to them.
        blinded_history = [
            {k: v for k, v in h.items() if not k.startswith("test_")}
            for h in history
        ]
        new_description = improve_description(
            skill_name=name,
            skill_content=content,
            current_description=current_description,
            eval_results=train_results,
            history=blinded_history,
            model=model,
            log_dir=log_dir,
            iteration=iteration,
        )
        improve_elapsed = time.time() - t0

        if verbose:
            print(f"Proposed ({improve_elapsed:.1f}s): {new_description}", file=sys.stderr)

        current_description = new_description

    # Pick the best iteration: prefer held-out test score; fall back to train score
    if test_set:
        best = max(history, key=lambda h: h["test_passed"] or 0)
        best_score = f"{best['test_passed']}/{best['test_total']}"
    else:
        best = max(history, key=lambda h: h["train_passed"])
        best_score = f"{best['train_passed']}/{best['train_total']}"

    if verbose:
        print(f"\nExit reason: {exit_reason}", file=sys.stderr)
        print(f"Best score: {best_score} (iteration {best['iteration']})", file=sys.stderr)

    return {
        "exit_reason": exit_reason,
        "original_description": original_description,
        "best_description": best["description"],
        "best_score": best_score,
        "best_train_score": f"{best['train_passed']}/{best['train_total']}",
        "best_test_score": (
            f"{best['test_passed']}/{best['test_total']}" if test_set else None
        ),
        "final_description": current_description,
        "iterations_run": len(history),
        "holdout": holdout,
        "train_size": len(train_set),
        "test_size": len(test_set),
        "history": history,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Optimize a skill description via eval + improve loop"
    )
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory (must contain SKILL.md)")
    parser.add_argument("--description", default=None, help="Override starting description (default: from SKILL.md)")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel claude -p workers")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per query in seconds")
    parser.add_argument("--max-iterations", type=int, default=5, help="Max improvement iterations")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Runs per query (averaged for trigger rate)")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Min trigger rate to count as 'triggered'")
    parser.add_argument("--holdout", type=float, default=0.4, help="Fraction held out for test (0 to disable)")
    parser.add_argument(
        "--model", required=True,
        help="Model for improve_description calls, e.g. claude-sonnet-4-5. "
             "Also passed to claude -p for eval queries unless --eval-model overrides.",
    )
    parser.add_argument(
        "--eval-model", default=None,
        help="Model for claude -p eval queries (defaults to --model). "
             "Use a cheaper/faster model for evals if desired.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-iteration stats to stderr")
    parser.add_argument(
        "--report", default="auto",
        help="HTML report path ('auto' = temp file, 'none' = disabled)",
    )
    parser.add_argument(
        "--results-dir", default=None,
        help="Save results.json + report.html + logs/ to a timestamped subdir here",
    )
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, _, _ = parse_skill_md(skill_path)

    # Resolve the effective eval model (falls back to --model)
    eval_model = args.eval_model or args.model

    # --- Set up the live HTML report ---
    if args.report != "none":
        if args.report == "auto":
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            live_report_path = (
                Path(tempfile.gettempdir())
                / f"skill_description_report_{skill_path.name}_{timestamp}.html"
            )
        else:
            live_report_path = Path(args.report)
        # Write a placeholder so the browser has something to load immediately
        live_report_path.write_text(
            "<html><body><h1>Starting optimization loop...</h1>"
            "<meta http-equiv='refresh' content='5'></body></html>",
            encoding="utf-8",
        )
        webbrowser.open(str(live_report_path))
    else:
        live_report_path = None

    # --- Set up results directory ---
    if args.results_dir:
        timestamp = time.strftime("%Y-%m-%d_%H%M%S")
        results_dir = Path(args.results_dir) / timestamp
        results_dir.mkdir(parents=True, exist_ok=True)
    else:
        results_dir = None

    log_dir = results_dir / "logs" if results_dir else None

    # --- Run the loop ---
    output = run_loop(
        eval_set=eval_set,
        skill_path=skill_path,
        description_override=args.description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        max_iterations=args.max_iterations,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        holdout=args.holdout,
        model=args.model,
        verbose=args.verbose,
        live_report_path=live_report_path,
        log_dir=log_dir,
    )

    # --- Emit outputs ---
    json_output = json.dumps(output, indent=2)
    print(json_output)

    if results_dir:
        (results_dir / "results.json").write_text(json_output, encoding="utf-8")

    if live_report_path:
        # Rewrite without the auto-refresh meta tag for the final static version
        live_report_path.write_text(
            generate_html(output, auto_refresh=False, skill_name=name),
            encoding="utf-8",
        )
        print(f"\nReport: {live_report_path}", file=sys.stderr)

    if results_dir and live_report_path:
        (results_dir / "report.html").write_text(
            generate_html(output, auto_refresh=False, skill_name=name),
            encoding="utf-8",
        )

    if results_dir:
        print(f"Results saved to: {results_dir}", file=sys.stderr)


# ProcessPoolExecutor on Windows requires this guard — workers are spawned
# (not forked), so the module is re-imported in each worker process.
# Without the guard, each worker would re-invoke main() on import.
if __name__ == "__main__":
    main()
