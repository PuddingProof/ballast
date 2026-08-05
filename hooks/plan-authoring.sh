#!/bin/bash
# PostToolUse hook, matcher "Write|Edit|MultiEdit" (shares the doc-write-guard.py matcher block in
# hooks/hooks.json) — plan AUTHORING nudge, the timing companion to plan-handoff.sh.
#
# THE GAP THIS CLOSES: plan-handoff.sh fires at ExitPlanMode approval, but executor-ready batching
# (step grouping, per-batch agentType/effort, sequencing constraints) is an AUTHORING-time concern —
# by approval the plan's shape is fixed. Native plan mode materializes the plan under
# ~/.claude/plans/, so a write there is the earliest deterministic signal, while batching guidance
# can still change it. The two hooks compose: authoring nudge (shape the batches) -> approval nudge
# (dispatch them).
#
# MECHANICS: sibling to subagent-fanout.sh — grep the raw stdin payload (no jq, no JSON parsing;
# decoding paths is the Windows-backslash hazard that pushed doc-write-guard to python), emit
# additionalContext JSON on stdout, always exit 0. The file_path arrives JSON-escaped
# ("C:\\Users\\...\\.claude\\plans\\slug.md") or with forward slashes; [\\/]{1,2} matches one "/"
# or the escaped "\\" either side. Soft nudge, recall over precision like subagent-fanout.sh: a
# false fire (a plans/ path inside written CONTENT) is low-harm — the injected text says to ignore it.

payload="$(cat)"

if printf '%s' "$payload" | grep -iqE '\.claude[\\/]{1,2}plans[\\/]{1,2}'; then
  # SESSION COOLDOWN (pattern shared with subagent-fanout.sh): plan drafting is many successive
  # edits to the same file, and the conventions text is static -- one injection per session
  # covers the drafting run. TTL 2h re-arms a long or post-compact session. FAIL OPEN: no sid /
  # no home dir -> fire every time.
  sid="$(printf '%s' "$payload" | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
  home_dir="${BALLAST_CLAUDE_HOME:-${HOME:+$HOME/.claude}}"
  if [ -n "$sid" ] && [ -n "$home_dir" ]; then
    marker="$home_dir/.cache/ballast-plan/$sid"
    # Suppressed matches leave no fire-ledger row -- record them (see subagent-fanout.sh).
    if [ -n "$(find "$marker" -mmin -120 2>/dev/null)" ]; then
      printf '%s suppressed sid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$sid" >> "$home_dir/.cache/ballast-plan/suppressed.log" 2>/dev/null
      exit 0
    fi
    { mkdir -p "$home_dir/.cache/ballast-plan" && touch "$marker"
      find "$home_dir/.cache/ballast-plan" -type f -mmin +2880 -exec rm -f {} + ; } 2>/dev/null
  fi
  cat <<'JSON'
{
  "systemMessage": "📝 ballast: plan-authoring conventions injected",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "PLAN FILE IN PROGRESS — batching is an authoring-time concern; by approval the plan's shape is fixed. While drafting, make the plan executor-ready: group mechanical implementation steps into dispatchable batches, pin each batch's agentType (plan-executor ladder: -light / default / -hard) and its sequencing/parallelism constraints, and keep every batch fully specified — an executor stops-and-reports on ambiguity, it never redesigns. On approval the plan-handoff protocol dispatches these batches as written. Batch count scales with task complexity — a straightforward task may be a single batch. (Not authoring a plan, or matched by accident? Ignore this.)"
  }
}
JSON
fi
exit 0
