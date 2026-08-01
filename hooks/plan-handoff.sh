#!/bin/bash
# PostToolUse hook, matcher "ExitPlanMode" (wired in hooks/hooks.json) — plan→execution handoff.
#
# THE MOMENT: a plan was just approved and execution is about to begin. This is the decision point
# the keyword-triggered subagent-fanout.sh structurally misses (it fires on USER prompts; plan
# approval is a permission response, not a prompt). PostToolUse fires only on SUCCESSFUL tool
# completion, so a rejected plan never triggers this — exactly the scoping we want, for free.
#
# SOFT BY DESIGN (additionalContext nudge, never a block): in-line execution is sometimes correct
# (tiny diffs; taste iteration that can't be spec'd — postmortem-verified), so a hard gate would be
# wrong. The injected text is a pointer-stub: the full protocol lives in the plan-handoff skill,
# keeping this injection lean (durable-docs: one canonical home, reminders fire point-of-use).
#
# CHIP LIFECYCLE (statusline mode-indicator): a successful ExitPlanMode in PostToolUse literally IS
# the user approving the plan — nothing to adjudicate — so the "exec" chip is raised CONFIRMED
# directly, with no pending stage. The model lowers it at hand-back via the `ballast-mode clear exec
# --session <sid>` instruction appended to the injected text below; the renderer's 24h confirmed-chip
# TTL is only the staleness backstop, never the primary mechanism.
#
# MECHANICS: sibling to subagent-fanout.sh (emit JSON on stdout, always exit 0), plus a payload read
# for the `session_id` the chip write and clear-instruction need. Extraction mirrors
# freehand-mode.sh's (python -c, rc=1 on ANY parse failure incl. "python not found" -> bash rc 127)
# so a clean-but-absent field is distinguishable from a genuine parse failure. JSON emission goes
# through python json.dumps with the handoff text passed via an ENV VAR and the sid via ARGV, never
# interpolated into the python -c source: the text contains literal backticks and the sid is
# payload-controlled, so keeping both out of the source string sidesteps any backtick/quote-escaping
# hazard entirely (same discipline as doc-write-guard.py's env-var-for-content convention).
#
# DEGRADATION LADDER (python unavailable, JSON parse fails, OR sid comes back empty): fall back to
# TODAY'S STATIC HEREDOC VERBATIM — no chip write, no clear-instruction, exactly the pre-chip
# behavior. This means the handoff paragraph is duplicated (bash variable for the dynamic path;
# hardcoded inside the static JSON heredoc for the fallback) — accepted, forced by the
# python-less fallback. The pairing is pinned by hooks/tests/test_plan_handoff.sh asserting both
# paths share the SAME distinctive sentence — keep them textually identical if you edit the wording.
#
# Fail-open contract: the mode-state.py write is fail-quiet (`|| true`) and never gates the JSON
# emission that follows it — a state-write failure (e.g. unwritable state dir) must never cost the
# handoff nudge itself. Always exit 0.

set -u

# Self-located via BASH_SOURCE (mirrors run.sh's own DIR resolution) so statusline/mode-state.py
# resolves correctly regardless of the caller's cwd.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

payload="$(cat)"

PY="${BALLAST_PYTHON:-python}"

# --- HANDOFF TEXT (canonical copy #1 — the dynamic-path source) ------------------------------
# Single-quoted heredoc: backticks and $ are literal, no expansion hazard.
handoff_text="$(cat <<'BALLAST_HANDOFF'
PLAN APPROVED → EXECUTION HANDOFF. The main thread stays top-tier for orchestration, verification, and review — it does not type the implementation. Dispatch the mechanical build steps to `plan-executor` subagents (`-light` / default / `-hard` by batch difficulty); for one coherent fully-specifiable task, a .notes plan artifact → fresh cheap session also works. In-line editing is right only for tiny diffs or taste iteration that can't be spec'd. Executors get plan steps, deterministic checks, and a brief big-picture why (comment quality is bounded by it) — open-ended verification (live-driving, visual) and review adjudication stay top-tier. Plan touches frontend/visual code: load `visual-verification-gate` now, at implementation start. *Full protocol + per-leaf tier/effort: run the plan-handoff skill.*
BALLAST_HANDOFF
)"

# Extract .session_id via python. rc=1 on ANY parse failure (bad JSON, non-dict top level, etc.)
# so the caller below can distinguish a clean-but-absent session_id (rc=0, empty string) from a
# genuine parse failure (rc!=0, including "python not found" -> bash rc 127).
# shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
sid="$($PY -c "
import json, sys
try:
    data = json.load(sys.stdin)
    s = data.get('session_id', '') if isinstance(data, dict) else ''
    sys.stdout.write(s if isinstance(s, str) else '')
except Exception:
    sys.exit(1)
" <<< "$payload" 2>/dev/null)"
py_rc=$?

if [ "$py_rc" -eq 0 ] && [ -n "$sid" ]; then
  # Deterministic arm: plan approval IS the confirm, no pending phase (see CHIP LIFECYCLE above).
  # Fail-quiet -- a state-write failure must never affect the injection that follows.
  # shellcheck disable=SC2086
  $PY "$DIR/../statusline/mode-state.py" raise exec --confirmed --session "$sid" >/dev/null 2>&1 || true

  # Text passed via env var, sid via argv -- see MECHANICS above for why.
  # shellcheck disable=SC2086
  output="$(BALLAST_HANDOFF_TEXT="$handoff_text" $PY -c "
import json, os, sys
text = os.environ.get('BALLAST_HANDOFF_TEXT', '')
sid = sys.argv[1]
text = text + ' When the final wave is verified and you hand back, lower the exec chip: run \`ballast-mode clear exec --session ' + sid + '\`.'
print(json.dumps({
    'systemMessage': '📋 ballast: plan-handoff protocol injected (exec chip raised)',
    'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'additionalContext': text,
    }
}))
" "$sid" 2>/dev/null)"

  if [ -n "$output" ]; then
    printf '%s\n' "$output"
    exit 0
  fi
  # json.dumps step itself failed unexpectedly -- fall through to the static fallback below.
fi

# --- DEGRADATION LADDER: python unavailable / parse failed / empty sid -----------------------
# Today's static heredoc verbatim (canonical copy #2 of the handoff text -- see MECHANICS above).
# No chip, no clear-instruction, no state write.
cat <<'JSON'
{
  "systemMessage": "📋 ballast: plan-handoff protocol injected",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "PLAN APPROVED → EXECUTION HANDOFF. The main thread stays top-tier for orchestration, verification, and review — it does not type the implementation. Dispatch the mechanical build steps to `plan-executor` subagents (`-light` / default / `-hard` by batch difficulty); for one coherent fully-specifiable task, a .notes plan artifact → fresh cheap session also works. In-line editing is right only for tiny diffs or taste iteration that can't be spec'd. Executors get plan steps, deterministic checks, and a brief big-picture why (comment quality is bounded by it) — open-ended verification (live-driving, visual) and review adjudication stay top-tier. Plan touches frontend/visual code: load `visual-verification-gate` now, at implementation start. *Full protocol + per-leaf tier/effort: run the plan-handoff skill.*"
  }
}
JSON
exit 0
