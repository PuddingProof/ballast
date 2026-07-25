"""Aggregate individual run results into benchmark summary statistics.

Slim fork of aggregate_benchmark.py — keeps pass_rate / time / tokens
(mean ± stddev, with_skill-before-baseline ordering). Drops the comparator/
analyzer agent paths.

SOURCE: skill-creator/scripts/aggregate_benchmark.py
WINDOWS-ADAPTED:
  - open() calls use explicit encoding="utf-8" (Python on Windows defaults to
    the system code page for file I/O, which is typically cp1252 — specifying
    utf-8 avoids mojibake in skill names / evidence strings).
  - Path separators handled via pathlib throughout (no hardcoded slashes).
UNVERIFIED:
  - Directory glob ordering: sorted() on Path.glob() results is deterministic
    on all platforms but the sort key is lexicographic (eval-1, eval-10, eval-2
    sorts as 1, 10, 2). The eval_id integer extracted from the dir name fixes
    numeric display order; the glob sort only affects processing order, which
    doesn't matter for statistics.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def calculate_stats(values: list[float]) -> dict:
    """Return mean, stddev (sample), min, max for a list of floats."""
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}

    n = len(values)
    mean = sum(values) / n

    # Sample stddev (n-1 denominator) — appropriate when the runs are a sample
    # of a larger possible run space, not a complete population.
    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0

    return {
        "mean": round(mean, 4),
        "stddev": round(stddev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


# ---------------------------------------------------------------------------
# Run loader — supports both workspace and legacy directory layouts
# ---------------------------------------------------------------------------

def load_run_results(benchmark_dir: Path) -> dict:
    """Load all run results from a benchmark directory.

    Supports two layouts:

    Workspace layout (skill-creator iterations):
        <benchmark_dir>/
        └── eval-N/
            ├── with_skill/run-1/grading.json
            └── without_skill/run-1/grading.json

    Legacy layout (runs/ subdirectory):
        <benchmark_dir>/runs/eval-N/...

    Returns a dict keyed by config name (e.g. "with_skill", "without_skill"),
    each containing a list of per-run result dicts.
    """
    # Detect layout
    runs_dir = benchmark_dir / "runs"
    if runs_dir.exists():
        search_dir = runs_dir
    elif list(benchmark_dir.glob("eval-*")):
        search_dir = benchmark_dir
    else:
        print(
            f"No eval directories found in {benchmark_dir} or {benchmark_dir / 'runs'}",
            file=sys.stderr,
        )
        return {}

    results: dict[str, list] = {}

    for eval_idx, eval_dir in enumerate(sorted(search_dir.glob("eval-*"))):
        # Try to get eval_id from eval_metadata.json; fall back to directory name
        metadata_path = eval_dir / "eval_metadata.json"
        if metadata_path.exists():
            try:
                eval_id = json.loads(metadata_path.read_text(encoding="utf-8")).get(
                    "eval_id", eval_idx
                )
            except (json.JSONDecodeError, OSError):
                eval_id = eval_idx
        else:
            try:
                eval_id = int(eval_dir.name.split("-")[1])
            except (ValueError, IndexError):
                eval_id = eval_idx

        # Config dirs are any subdirectories that contain run-N/ children
        for config_dir in sorted(eval_dir.iterdir()):
            if not config_dir.is_dir():
                continue
            if not list(config_dir.glob("run-*")):
                continue  # not a config directory

            config = config_dir.name
            if config not in results:
                results[config] = []

            for run_dir in sorted(config_dir.glob("run-*")):
                try:
                    run_number = int(run_dir.name.split("-")[1])
                except (ValueError, IndexError):
                    run_number = 0

                grading_file = run_dir / "grading.json"
                if not grading_file.exists():
                    print(f"Warning: grading.json not found in {run_dir}", file=sys.stderr)
                    continue

                try:
                    grading = json.loads(grading_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON in {grading_file}: {e}", file=sys.stderr)
                    continue

                # Core metrics
                result = {
                    "eval_id": eval_id,
                    "run_number": run_number,
                    "pass_rate": grading.get("summary", {}).get("pass_rate", 0.0),
                    "passed": grading.get("summary", {}).get("passed", 0),
                    "failed": grading.get("summary", {}).get("failed", 0),
                    "total": grading.get("summary", {}).get("total", 0),
                }

                # Timing — check grading.json first, then sibling timing.json
                timing = grading.get("timing", {})
                result["time_seconds"] = timing.get("total_duration_seconds", 0.0)
                timing_file = run_dir / "timing.json"
                if result["time_seconds"] == 0.0 and timing_file.exists():
                    try:
                        timing_data = json.loads(timing_file.read_text(encoding="utf-8"))
                        result["time_seconds"] = timing_data.get("total_duration_seconds", 0.0)
                        result["tokens"] = timing_data.get("total_tokens", 0)
                    except (json.JSONDecodeError, OSError):
                        pass

                # Execution metrics
                metrics = grading.get("execution_metrics", {})
                result["tool_calls"] = metrics.get("total_tool_calls", 0)
                if not result.get("tokens"):
                    result["tokens"] = metrics.get("output_chars", 0)
                result["errors"] = metrics.get("errors_encountered", 0)

                # Expectations (viewer needs text, passed, evidence)
                raw_expectations = grading.get("expectations", [])
                for exp in raw_expectations:
                    if "text" not in exp or "passed" not in exp:
                        print(
                            f"Warning: expectation in {grading_file} missing required fields "
                            f"(text, passed): {exp}",
                            file=sys.stderr,
                        )
                result["expectations"] = raw_expectations

                # User notes from the eval-grader agent
                notes_summary = grading.get("user_notes_summary", {})
                notes: list[str] = []
                notes.extend(notes_summary.get("uncertainties", []))
                notes.extend(notes_summary.get("needs_review", []))
                notes.extend(notes_summary.get("workarounds", []))
                result["notes"] = notes

                results[config].append(result)

    return results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_results(results: dict) -> dict:
    """Compute mean ± stddev for pass_rate, time_seconds, tokens per config.

    Ordering: "with_skill" config is listed first (before "without_skill" or
    any baseline) so the delta is always primary - baseline (positive = improvement).
    """
    # Sort configs so with_skill always comes first — makes delta sign intuitive
    all_configs = list(results.keys())
    ordered = sorted(
        all_configs,
        key=lambda c: (0 if "with" in c else 1, c),  # with_* first, then lexicographic
    )

    run_summary = {}
    for config in ordered:
        runs = results.get(config, [])
        if not runs:
            run_summary[config] = {
                "pass_rate": {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0},
                "time_seconds": {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0},
                "tokens": {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0},
            }
            continue

        run_summary[config] = {
            "pass_rate": calculate_stats([r["pass_rate"] for r in runs]),
            "time_seconds": calculate_stats([r["time_seconds"] for r in runs]),
            "tokens": calculate_stats([float(r.get("tokens", 0)) for r in runs]),
        }

    # Delta: first config minus second (with_skill - without_skill = positive is good)
    if len(ordered) >= 2:
        primary = run_summary[ordered[0]]
        baseline = run_summary[ordered[1]]
    elif len(ordered) == 1:
        primary = run_summary[ordered[0]]
        baseline = {}
    else:
        primary = {}
        baseline = {}

    delta_pass_rate = (
        primary.get("pass_rate", {}).get("mean", 0)
        - baseline.get("pass_rate", {}).get("mean", 0)
    )
    delta_time = (
        primary.get("time_seconds", {}).get("mean", 0)
        - baseline.get("time_seconds", {}).get("mean", 0)
    )
    delta_tokens = (
        primary.get("tokens", {}).get("mean", 0)
        - baseline.get("tokens", {}).get("mean", 0)
    )

    run_summary["delta"] = {
        "pass_rate": f"{delta_pass_rate:+.2f}",
        "time_seconds": f"{delta_time:+.1f}",
        "tokens": f"{delta_tokens:+.0f}",
    }

    return run_summary


# ---------------------------------------------------------------------------
# Full benchmark generation
# ---------------------------------------------------------------------------

def generate_benchmark(
    benchmark_dir: Path,
    skill_name: str = "",
    skill_path: str = "",
) -> dict:
    """Load runs, aggregate stats, and build a benchmark.json-shaped dict."""
    results = load_run_results(benchmark_dir)
    run_summary = aggregate_results(results)

    # Flatten runs for the 'runs' array in benchmark.json
    runs = []
    for config, config_runs in results.items():
        for result in config_runs:
            runs.append({
                "eval_id": result["eval_id"],
                "configuration": config,
                "run_number": result["run_number"],
                "result": {
                    "pass_rate": result["pass_rate"],
                    "passed": result["passed"],
                    "failed": result["failed"],
                    "total": result["total"],
                    "time_seconds": result["time_seconds"],
                    "tokens": result.get("tokens", 0),
                    "tool_calls": result.get("tool_calls", 0),
                    "errors": result.get("errors", 0),
                },
                "expectations": result["expectations"],
                "notes": result["notes"],
            })

    eval_ids = sorted({
        r["eval_id"]
        for config_runs in results.values()
        for r in config_runs
    })

    return {
        "metadata": {
            "skill_name": skill_name or "<skill-name>",
            "skill_path": skill_path or "<path/to/skill>",
            "executor_model": "<model-name>",
            "analyzer_model": "<model-name>",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evals_run": eval_ids,
            "runs_per_configuration": 3,  # informational; update if you use a different count
        },
        "runs": runs,
        "run_summary": run_summary,
        "notes": [],  # populated by analyzer (not forked here)
    }


def generate_markdown(benchmark: dict) -> str:
    """Render a human-readable Markdown summary from benchmark data."""
    metadata = benchmark["metadata"]
    run_summary = benchmark["run_summary"]

    configs = [k for k in run_summary if k != "delta"]
    config_a = configs[0] if len(configs) >= 1 else "config_a"
    config_b = configs[1] if len(configs) >= 2 else "config_b"
    label_a = config_a.replace("_", " ").title()
    label_b = config_b.replace("_", " ").title()

    lines = [
        f"# Skill Benchmark: {metadata['skill_name']}",
        "",
        f"**Model**: {metadata['executor_model']}",
        f"**Date**: {metadata['timestamp']}",
        (
            f"**Evals**: {', '.join(map(str, metadata['evals_run']))} "
            f"({metadata['runs_per_configuration']} runs each per configuration)"
        ),
        "",
        "## Summary",
        "",
        f"| Metric | {label_a} | {label_b} | Delta |",
        "|--------|------------|---------------|-------|",
    ]

    a_summary = run_summary.get(config_a, {})
    b_summary = run_summary.get(config_b, {})
    delta = run_summary.get("delta", {})

    a_pr = a_summary.get("pass_rate", {})
    b_pr = b_summary.get("pass_rate", {})
    lines.append(
        f"| Pass Rate | {a_pr.get('mean', 0)*100:.0f}% ± {a_pr.get('stddev', 0)*100:.0f}% "
        f"| {b_pr.get('mean', 0)*100:.0f}% ± {b_pr.get('stddev', 0)*100:.0f}% "
        f"| {delta.get('pass_rate', '—')} |"
    )

    a_time = a_summary.get("time_seconds", {})
    b_time = b_summary.get("time_seconds", {})
    lines.append(
        f"| Time | {a_time.get('mean', 0):.1f}s ± {a_time.get('stddev', 0):.1f}s "
        f"| {b_time.get('mean', 0):.1f}s ± {b_time.get('stddev', 0):.1f}s "
        f"| {delta.get('time_seconds', '—')}s |"
    )

    a_tokens = a_summary.get("tokens", {})
    b_tokens = b_summary.get("tokens", {})
    lines.append(
        f"| Tokens | {a_tokens.get('mean', 0):.0f} ± {a_tokens.get('stddev', 0):.0f} "
        f"| {b_tokens.get('mean', 0):.0f} ± {b_tokens.get('stddev', 0):.0f} "
        f"| {delta.get('tokens', '—')} |"
    )

    if benchmark.get("notes"):
        lines.extend(["", "## Notes", ""])
        for note in benchmark["notes"]:
            lines.append(f"- {note}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate benchmark run results into summary statistics"
    )
    parser.add_argument("benchmark_dir", type=Path, help="Path to benchmark directory")
    parser.add_argument("--skill-name", default="", help="Name of the skill being benchmarked")
    parser.add_argument("--skill-path", default="", help="Path to the skill being benchmarked")
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output path for benchmark.json (default: <benchmark_dir>/benchmark.json)",
    )
    args = parser.parse_args()

    if not args.benchmark_dir.exists():
        print(f"Directory not found: {args.benchmark_dir}", file=sys.stderr)
        sys.exit(1)

    benchmark = generate_benchmark(args.benchmark_dir, args.skill_name, args.skill_path)

    output_json = args.output or (args.benchmark_dir / "benchmark.json")
    output_md = output_json.with_suffix(".md")

    # Write benchmark.json
    output_json.write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    print(f"Generated: {output_json}")

    # Write benchmark.md
    output_md.write_text(generate_markdown(benchmark), encoding="utf-8")
    print(f"Generated: {output_md}")

    # Console summary
    run_summary = benchmark["run_summary"]
    configs = [k for k in run_summary if k != "delta"]
    delta = run_summary.get("delta", {})
    print("\nSummary:")
    for config in configs:
        pr = run_summary[config]["pass_rate"]["mean"]
        label = config.replace("_", " ").title()
        print(f"  {label}: {pr*100:.1f}% pass rate")
    print(f"  Delta: {delta.get('pass_rate', '—')}")


if __name__ == "__main__":
    main()
