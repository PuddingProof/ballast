#!/bin/bash
# PostToolUse hook, matcher "ExitPlanMode" (wired in hooks/hooks.json) — plan→execution handoff.
#
# CONTRACT: a successful ExitPlanMode means the user just approved a plan (PostToolUse fires only on
# success, so a rejected plan never reaches here). Inject a one-line pointer to the plan-handoff
# skill, raise the statusline "exec" chip CONFIRMED (approval IS the confirm), and tell the model how
# to lower the chip at hand-back. The renderer's 24h TTL is only the staleness backstop.
#
# POSTURE: soft pointer (additionalContext, never a block) — in-line execution is sometimes right
# (tiny diffs, taste iteration), and the full protocol lives in the skill, not here.
#
# JSON SAFETY: the sid is accepted only if it matches ^[A-Za-z0-9_-]+$, and the text contains no `"`
# or `\`, so printf interpolation always yields valid JSON. Accepted residual: a sid with unexpected
# characters loses the chip, never the pointer.
#
# Fail-open: the mode-state.py write is fail-quiet and never gates the emission. Always exit 0.

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${BALLAST_PYTHON:-python}"

payload="$(cat)"

text='PLAN APPROVED → run the plan-handoff skill before the first edit. You orchestrate, verify, and review; leaf-light / leaf / leaf-hard agents write the code. Frontend or visual work: load visual-probe now. Before hand-back, read the combined diff once as a whole.'
msg='📋 ballast: plan-handoff — protocol pointer injected'

sid="$(printf '%s' "$payload" | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
case "$sid" in
  ''|*[!A-Za-z0-9_-]*) sid="" ;;
esac

if [ -n "$sid" ]; then
  # shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
  $PY "$DIR/../statusline/mode-state.py" raise exec --confirmed --session "$sid" >/dev/null 2>&1 || true
  text="$text When you hand back, lower the exec chip: run \`ballast-mode clear exec --session $sid\`."
  msg="$msg (exec chip raised)"
fi

printf '{"systemMessage":"%s","hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$msg" "$text"
exit 0
