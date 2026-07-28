#!/bin/bash
# PostToolUse hook, matcher "Write|Edit|MultiEdit" (shares the doc-write-guard.py matcher block in
# hooks/hooks.json) — plan AUTHORING nudge, the timing companion to plan-handoff.sh.
#
# THE GAP THIS CLOSES: plan-handoff.sh fires at ExitPlanMode approval, but executor-ready batching
# (step grouping, per-batch agentType/effort, sequencing constraints) is an AUTHORING-time concern —
# by approval the plan is already shaped. Native plan mode materializes the plan as a file under
# ~/.claude/plans/, so a write there is the earliest deterministic signal that a plan is taking
# shape — mid-authoring, while batching guidance can still change it. The two hooks compose:
# authoring nudge (shape the batches) -> approval nudge (dispatch them).
#
# MECHANICS: sibling to subagent-fanout.sh — grep the raw stdin payload (no jq, no JSON parsing;
# decoding paths is the Windows-backslash hazard that pushed doc-write-guard to python), emit
# additionalContext JSON on stdout, always exit 0. The file_path arrives JSON-escaped
# ("C:\\Users\\...\\.claude\\plans\\slug.md") or with forward slashes; [\\/]{1,2} matches one "/"
# or the escaped "\\" either side. A false fire (a plans/ path mentioned inside written CONTENT
# rather than the target path) is low-harm: the injected text tells the reader to ignore it —
# same recall-over-precision bias as subagent-fanout.sh.

payload="$(cat)"

if printf '%s' "$payload" | grep -iqE '\.claude[\\/]{1,2}plans[\\/]{1,2}'; then
  cat <<'JSON'
{
  "systemMessage": "📝 ballast: plan-authoring conventions injected",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "PLAN FILE IN PROGRESS — batching is an authoring-time concern; by approval the plan's shape is fixed. While drafting, make the plan executor-ready: group mechanical implementation steps into dispatchable batches, pin each batch's agentType (plan-executor ladder: -light / default / -hard) and its sequencing/parallelism constraints, and keep every batch fully specified — an executor stops-and-reports on ambiguity, it never redesigns. On approval the plan-handoff protocol dispatches these batches as written. Batch count scales with task complexity — a straightforward task may be a single batch. This framework is an efficiency play: it keeps the main session's context lean for orchestration and judgement, and delegates execution churn to cheaper agents. (Not authoring a plan, or matched by accident? Ignore this.)"
  }
}
JSON
fi
exit 0
