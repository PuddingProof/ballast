#!/bin/bash
# SessionEnd hook (wired in hooks/hooks.json) — statusline mode-state hygiene.
#
# WHY THIS EXISTS: freehand-mode.sh, plan-handoff.sh, and the mode-owning skills write per-session
# "chip" state under ~/.claude/ballast/modes/<sid> (via statusline/mode-state.py) for the statusLine
# renderer. That state is scoped to one session, and nothing else deletes it — the renderer's TTL is
# a *display* backstop, not a disk one — so an abrupt end (crash, /clear, closed terminal) would
# strand it forever. This hook unlinks the just-ended session's file; mode-state.py's `cleanup`
# subcommand piggybacks a GC sweep of any OTHER session's file older than 7 days, keeping
# ballast/modes/ bounded even across sessions that never fire SessionEnd cleanly.
#
# SID RESOLUTION, TWO-TIER: extract `.session_id` from the payload via $BALLAST_PYTHON (same
# pattern as plan-handoff.sh / freehand-mode.sh: python -c, rc=1 on ANY parse failure); on failure
# fall back to `CLAUDE_CODE_SESSION_ID` — the name the harness actually sets in a hook process
# (`CLAUDE_SESSION_ID` is NOT set there; it is a load-time plugin-loader substitution for skill and
# agent bodies, kept below only as a harmless nested fallback). Neither resolving is legitimate too:
# silent no-op, not an error.
#
# NO STDOUT, EVER: the deliberate exception to hooks/CLAUDE.md's "every conditional fire is
# user-visible" rule. SessionEnd fires as the session closes — there is no terminal left to read a
# systemMessage in, and this is unconditional best-effort hygiene, not a decision to override. With
# no stdout and no nonzero exit, a normal run is invisible in run.sh's fire ledger too; nothing here
# is worth auditing after the fact.
#
# FAIL-OPEN, EVERYWHERE: the mode-state.py call is fail-quiet (`|| true`) so a bad sid, an
# unwritable state dir, or a missing/broken mode-state.py never surfaces as an error, and this
# script always exits 0 — SessionEnd is no moment to block or delay on housekeeping.

set -u

# Self-located via BASH_SOURCE (mirrors run.sh's own DIR resolution and plan-handoff.sh) so
# statusline/mode-state.py resolves correctly regardless of the caller's cwd.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

payload="$(cat)"

PY="${BALLAST_PYTHON:-python}"

# Extract .session_id via python (same pattern as plan-handoff.sh). rc=1 on ANY parse failure so
# the caller below can tell a genuine parse failure from a clean-but-absent field and fall back to
# the env var deliberately rather than by accident.
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

if [ "$py_rc" -ne 0 ] || [ -z "$sid" ]; then
  # Python unavailable, payload unparseable, or session_id absent from the payload -- fall back to
  # the CLAUDE_CODE_SESSION_ID env var (see SID RESOLUTION above for why the legacy name was wrong).
  sid="${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
fi

if [ -n "$sid" ]; then
  # Fail-quiet: mode-state.py validates sid itself (rejects anything malformed) and this call must
  # never affect the exit code below either way.
  # shellcheck disable=SC2086
  $PY "$DIR/../statusline/mode-state.py" cleanup --session "$sid" >/dev/null 2>&1 || true
fi

# No sid resolved from either source: silent no-op, not an error (see SID RESOLUTION above).
exit 0
