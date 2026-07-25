# Eval Schemas

Slimmed schema reference for the scripts kept in skill-forge (optimize_description,
benchmark, generate_review, generate_report). Covers only the shapes those scripts
read or write — does not document comparison.json / analysis.json (not forked here).

SOURCE: skill-creator/references/schemas.md (full version)

---

## Eval set JSON (input to optimize_description.py)

The `--eval-set` argument. Not a named file in the schema doc — this is the trigger-eval
format used by the description optimizer.

```json
[
  { "query": "write a new skill for X", "should_trigger": true },
  { "query": "deploy my app to production", "should_trigger": false }
]
```

**Fields:**
- `query` — The user message text sent to `claude -p`.
- `should_trigger` — Whether the skill is expected to be invoked for this query.

---

## evals.json (input to the benchmark runner — for context)

Located at `<skill-dir>/evals/evals.json`. Not read by the forked scripts directly,
but referenced by callers that produce the benchmark directory layout.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's example prompt",
      "expected_output": "Description of expected result",
      "files": ["evals/files/sample1.pdf"],
      "expectations": [
        "The output includes X",
        "The skill used script Y"
      ]
    }
  ]
}
```

**Fields:**
- `skill_name` — Must match the skill's frontmatter `name:`.
- `evals[].id` — Unique integer; used as `eval_id` in grading and benchmark output.
- `evals[].prompt` — Task given to the executor agent.
- `evals[].expectations` — Verifiable statements passed to the eval-grader agent.
- `evals[].files` — Optional input files (relative to skill root).

---

## grading.json (read by benchmark.py + generate_review.py)

Located at `<run-dir>/grading.json`. Written by the eval-grader agent (dispatched per run by the benchmark flow).

```json
{
  "expectations": [
    {
      "text": "The output includes the name 'John Smith'",
      "passed": true,
      "evidence": "Found in transcript: 'Extracted names: John Smith'"
    }
  ],
  "summary": {
    "passed": 2,
    "failed": 1,
    "total": 3,
    "pass_rate": 0.67
  },
  "execution_metrics": {
    "total_tool_calls": 15,
    "output_chars": 12450,
    "errors_encountered": 0
  },
  "timing": {
    "total_duration_seconds": 191.0
  },
  "user_notes_summary": {
    "uncertainties": ["Used 2023 data, may be stale"],
    "needs_review": [],
    "workarounds": ["Fell back to text overlay"]
  }
}
```

**Fields required by benchmark.py:**
- `summary.pass_rate`, `summary.passed`, `summary.failed`, `summary.total`
- `timing.total_duration_seconds` (0 if absent — also checked in sibling `timing.json`)
- `execution_metrics.total_tool_calls`, `.output_chars`, `.errors_encountered`

**Fields required by generate_review.py (viewer):**
- `expectations[].text` and `expectations[].passed` — required
- `expectations[].evidence` — optional, shown as sub-text

---

## eval_metadata.json (read by generate_review.py)

Located at `<eval-dir>/eval_metadata.json` or `<run-dir>/eval_metadata.json`.

```json
{
  "eval_id": 1,
  "prompt": "The task prompt shown in the viewer"
}
```

---

## timing.json (optional; read by benchmark.py)

Located at `<run-dir>/timing.json`. Written by the executor; checked as fallback
when `grading.json` has no `timing` block.

```json
{
  "total_tokens": 84852,
  "total_duration_seconds": 23.3
}
```

---

## benchmark.json (written by benchmark.py, read by generate_review.py viewer)

Located at `<benchmark-dir>/benchmark.json`.

**Critical field names** — the viewer reads these exactly; wrong names = silent zeros:
- `runs[].configuration` (not `config`)
- `runs[].result.pass_rate` (nested under `result`, not top-level)
- `run_summary.<config>.pass_rate.mean` / `.stddev`
- `run_summary.delta.pass_rate` — a `"+0.50"`-style string

```json
{
  "metadata": {
    "skill_name": "my-skill",
    "timestamp": "2026-01-15T10:30:00Z",
    "evals_run": [1, 2, 3],
    "runs_per_configuration": 3
  },
  "runs": [
    {
      "eval_id": 1,
      "configuration": "with_skill",
      "run_number": 1,
      "result": {
        "pass_rate": 0.85,
        "passed": 6,
        "failed": 1,
        "total": 7,
        "time_seconds": 42.5,
        "tokens": 3800,
        "tool_calls": 18,
        "errors": 0
      },
      "expectations": [{"text": "...", "passed": true, "evidence": "..."}],
      "notes": []
    }
  ],
  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": 0.85, "stddev": 0.05, "min": 0.80, "max": 0.90},
      "time_seconds": {"mean": 45.0, "stddev": 12.0, "min": 32.0, "max": 58.0},
      "tokens": {"mean": 3800.0, "stddev": 400.0, "min": 3200.0, "max": 4100.0}
    },
    "without_skill": {
      "pass_rate": {"mean": 0.35, "stddev": 0.08, "min": 0.28, "max": 0.45},
      "time_seconds": {"mean": 32.0, "stddev": 8.0, "min": 24.0, "max": 42.0},
      "tokens": {"mean": 2100.0, "stddev": 300.0, "min": 1800.0, "max": 2500.0}
    },
    "delta": {
      "pass_rate": "+0.50",
      "time_seconds": "+13.0",
      "tokens": "+1700"
    }
  },
  "notes": ["Freeform analyzer observations"]
}
```

---

## optimize_description.py output (written by run_loop, read by generate_report.py)

Not a file with a canonical name — typically written to `results.json` or piped to stdout.

```json
{
  "exit_reason": "all_passed (iteration 2)",
  "original_description": "...",
  "best_description": "...",
  "best_score": "8/10",
  "best_train_score": "6/6",
  "best_test_score": "4/4",
  "final_description": "...",
  "iterations_run": 2,
  "holdout": 0.4,
  "train_size": 6,
  "test_size": 4,
  "history": [
    {
      "iteration": 1,
      "description": "...",
      "train_passed": 4,
      "train_failed": 2,
      "train_total": 6,
      "train_results": [
        {
          "query": "write a new skill",
          "should_trigger": true,
          "trigger_rate": 1.0,
          "triggers": 3,
          "runs": 3,
          "pass": true
        }
      ],
      "test_passed": 3,
      "test_failed": 1,
      "test_total": 4,
      "test_results": [...],
      "passed": 4,
      "failed": 2,
      "total": 6,
      "results": [...]
    }
  ]
}
```

**Notes for generate_report.py:**
- `history[].train_results` (or fallback `history[].results`) — per-query train results
- `history[].test_results` — per-query test results (null if no holdout)
- `results[].triggers` / `results[].runs` — raw counts for the trigger-rate display
- `results[].pass` — bool: did this query pass at the configured trigger threshold?
