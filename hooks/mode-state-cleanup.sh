#!/bin/bash
# SessionEnd hook (wired in hooks/hooks.json) — statusline mode-state hygiene.
#
# WHY THIS EXISTS: hooks/freehand-mode.sh, hooks/plan-handoff.sh, and the mode-owning skills write
# per-session "chip" state under ~/.claude/ballast/modes/<sid> (via statusline/mode-state.py) so the
# statusLine renderer can draw a glanceable per-mode indicator (✈️ freehand, 📋 plan-handoff, 🧠
# critical-analysis — the registry lives in statusline/render.py). That state is scoped to one session and
# has no reason to survive it — a session that ends abruptly (crash, /clear, closed terminal) would
# otherwise leave a stale file behind forever, since nothing else deletes it (the renderer's TTL is
# a *display* backstop, not a disk one). This hook deletes the just-ended session's own state file,
# and mode-state.py's `cleanup` subcommand piggybacks a GC sweep of any OTHER session's file older
# than 7 days — so ballast/modes/ stays bounded even across sessions that never fire SessionEnd
# cleanly.
#
# SID RESOLUTION, TWO-TIER: extract `.session_id` from the payload via $BALLAST_PYTHON first (same
# extraction pattern as plan-handoff.sh / freehand-mode.sh: python -c, rc=1 on ANY parse failure).
# If that fails (no python, bad JSON, missing field), fall back to the `CLAUDE_CODE_SESSION_ID`
# env var — the name the harness actually sets in hook processes — so this hook degrades gracefully
# to a zero-dependency path instead of doing nothing just because python was unavailable. Neither
# resolving is a legitimate outcome too (payload unparseable AND env unset) -- silent no-op, not an
# error.
# ENV NAME, corrected 2026-07-25: this read `CLAUDE_SESSION_ID`, which the harness has NEVER set in
# a hook process (verified against the v2.1.220 binary's hook-child env builder and a live env dump
# from a hook). Because this is the FALLBACK behind the payload extraction, the bug was invisible
# on the happy path but silently no-op'd SessionEnd cleanup whenever the payload path was
# unavailable — stranding the statusline mode chip until the renderer's TTL aged it out. The nested
# `:-` keeps the legacy name as a harmless fallback if a future harness sets it. NOT related to the
# `${CLAUDE_SESSION_ID}` substitution used in skill/agent bodies: that is a load-time plugin-loader
# mechanism (docs/frontmatter.md), correct as-is, and never reaches a hook's environment.
#
# NO STDOUT, EVER: every other conditional-fire hook in this repo emits a systemMessage so a fire
# is never silent (see hooks/CLAUDE.md's "every conditional fire is user-visible" rule) -- this
# hook is the deliberate exception. SessionEnd fires as the session is closing; there is no
# terminal left to read a systemMessage in, and nothing here is a decision the user could act on or
# override (it's unconditional best-effort hygiene, not a guard). The fire ledger in run.sh still
# records it via the rc-nonzero / stdout-nonempty check -- since this hook never produces stdout,
# it ledgers only on the rare nonzero exit, which never happens here (see below) -- so a normal run
# is intentionally invisible everywhere, including the ledger. That's fine: nothing here is worth
# auditing after the fact.
#
# FAIL-OPEN, EVERYWHERE: the mode-state.py call itself is fail-quiet (`|| true`) so a bad sid, an
# unwritable state dir, or mode-state.py being missing/broken never surfaces as an error. This
# script always exits 0 regardless of what happened above -- SessionEnd is not a moment to ever
# block or delay on cleanup housekeeping.

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
