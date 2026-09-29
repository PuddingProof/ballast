#!/bin/bash
# UserPromptSubmit hook — delegated-autonomy ("freehand" / "autopilot") mode SENSOR.
# POSTURE: soft (additionalContext + systemMessage), never blocks, always exits 0. Runs on every
# submit, so it stays cheap: bash + one python extraction step + grep.
#
# Two jobs:
#   1. ARM — the typed prompt contains the whole word `freehand`/`autopilot` (case-insensitive):
#      inject a SHORT adjudication stub. The stub carries NONE of the mode's rules; it tells the
#      model to decide use-vs-mention and, on a genuine grant, to run the freehand skill.
#   2. MIRROR — no keyword this prompt, but the session already has a CONFIRMED grant on file:
#      inject a one-line reminder so the standing grant survives a /compact or a scroll-off.
#
# INVARIANT — the standing contract lives in skills/freehand/SKILL.md, never here. Keeping the
# rules out of the stub is a second adoption gate: a mechanical arm cannot by itself put a
# contract in force; adoption requires the model to actively invoke the skill (which also raises
# the confirmed statusline chip via `ballast-mode confirm`, a mechanism this hook never touches).
#
# USE-VS-MENTION: the pattern arms on any prose occurrence but cannot tell USING the keyword
# ("autopilot on") from TALKING ABOUT it ("the freehand hook is misfiring") — a semantic call no
# trigger pattern can make, and encoding it as pattern exclusions is an overfit band-aid. So the
# trigger deliberately stays high-recall and precision moves to the one component that can judge
# it: the stub OPENS with an adjudication gate. A mechanical arm on a mention is therefore
# expected and harmless BY DESIGN — the regression to watch for is the model adopting the mode
# despite the gate, not the pattern matching.
#
# PRECISION MODEL: the trigger matches the extracted `.prompt` field ONLY, not the whole stdin
# payload — Claude Code folds editor context (e.g. <ide_opened_file>...path...</ide_opened_file>)
# and echoed prior-report text into that payload, which armed the mode on turns carrying ZERO
# typed keywords. The boundary class additionally excludes alnum/_/-//\ on both sides (not plain
# `grep -w`) so a keyword inside a path or identifier (freehand-mode.sh, path/autopilot/x) is
# rejected while real prose ("go freehand.", "(autopilot)") still matches. Orthogonal to
# `ultracode`: both are plain keywords, honor whichever are present.
#
# SLASH-COMMAND FORM: a second alternation arms on `/freehand …` / `/ballast:freehand …` at a
# line start or after whitespace — the path exclusion above otherwise swallows exactly this shape
# (`/` is an excluded boundary char), and an inline-typed slash command is the mode's own
# invocation syntax, the STRONGEST grant signal there is. Suppressing it inverts the design's
# error direction: a false ARM is harmless by construction (the stub adjudicates), a false
# SILENCE loses a real grant until the user notices. The right boundary rejects `-` and `/`, so
# a slash-led filename (`/freehand-mode.sh`) or deeper path (`/freehand/x`) stays quiet; a path
# with a non-space char before its slash (hooks/freehand-mode.sh, C:/freehand.sh) is rejected
# by the left boundary. `.` is deliberately NOT rejected — `/freehand.` ending a sentence is a
# real grant shape and silencing it is the costly direction, so `/freehand.sh` false-arms as an
# accepted, stub-adjudicated leniency.
#
# MENTION MARKER: inline-code (backtick) spans are replaced with a single space in the scan
# target before the keyword match — a backtick-quoted keyword is the user's EXPLICIT typed
# mention syntax. This is the one sanctioned recall-layer rejection of a typed shape: it
# encodes declared syntax with fixed semantics, not a heuristic guess at intent (those stay in
# the stub), and it ships with failing-shape tests. The replacement is a SPACE, never empty:
# deleting a span outright fuses its neighbors into one token, silencing a keyword with a
# zero-whitespace span beside it ("read`f.py`freehand on") — the costly direction. Line-scoped
# (sed is line-based), balanced pairs only. A LONE stray backtick strips nothing (no pair to
# close); the accepted residual is a stray backtick FOLLOWED by a closed span on the same line
# — sed pairs across the gap and deletes the prose between, a real grant included — re-type
# the grant plainly, exactly like the notification-marker residual above.
#
# VISIBILITY: every ARM emits a systemMessage so an arm is never silent and the user can override
# a wrong adjudication immediately. The MIRROR is the deliberate exception — it fires on EVERY
# prompt while the mode is on, so a per-prompt systemMessage would be terminal spam (hooks/
# CLAUDE.md's high-frequency-fire-path exception); its visibility is carried by the statusline
# chip and by run.sh's non-empty-stdout fire ledger, which still records every mirror fire.
#
# MIRROR CONTRACT: fires only on a cleanly-parsed payload WITH a non-empty sid (no sid → no
# per-session state file → no mirror). Precedence is structural — it lives in the else branch, so
# a keyword arm on the same prompt always wins — and the notification-shell demotion exits before
# BOTH. The state-file read is READ-ONLY and fail-quiet. TTL HONESTY: the state file is the SINGLE
# truth source for whether a grant stands; render.py's 24h confirmed-chip TTL is DISPLAY-ONLY
# hygiene, not a grant lifetime, so the mirror must never gate on it. Staleness is bounded instead
# by the SessionEnd mode-state-cleanup unlink plus mode-state.py's 7-day GC of sibling files.
#
# FAIL-OPEN: with no resolvable Python or an unparseable payload, the keyword match degrades to a
# grep over the RAW stdin payload (the wider false-arm exposure described under PRECISION MODEL)
# and the mirror is skipped; nothing errors. Output is JSON on stdout via
# hookSpecificOutput.additionalContext (a bare echo would only show in transcript mode); no match
# -> no output -> no injection.

set -u

# Self-located via BASH_SOURCE (mirrors run.sh, bin/ballast-extract) so paths resolve regardless
# of caller cwd. Deliberately NOT PATH or CLAUDE_PLUGIN_ROOT: neither is guaranteed in a hook child.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

payload="$(cat)"

PY="${BALLAST_PYTHON:-python}"

# Extract THREE fields, one per output line: session_id (line 1), the resolved claude-home
# (line 2), and .prompt (line 3 onward -- the prompt may itself contain newlines, so everything
# past the second line boundary is prompt text verbatim; the bash side splits with `head -n1` /
# `head -n2|tail -n1` / `tail -n +3`). Exits 1 on ANY parse failure (bad JSON, "python not found"
# -> bash rc 127) so the caller can distinguish a clean-but-empty prompt (rc=0, empty string) from
# a genuine parse failure and pick the fallback deliberately rather than by accident.
#
# The claude-home is resolved HERE, exactly like statusline/mode-state.py's _claude_home()
# (BALLAST_CLAUDE_HOME override, else Path.home()/.claude), so the mirror's reader and
# mode-state.py (the WRITER) land on the identical directory -- see the mirror's statefile
# assignment for the Windows divergence this closes.
# shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
extracted="$($PY -c "
import json, os, sys
from pathlib import Path
try:
    data = json.load(sys.stdin)
    sid = data.get('session_id', '') if isinstance(data, dict) else ''
    p = data.get('prompt', '') if isinstance(data, dict) else ''
    override = os.environ.get('BALLAST_CLAUDE_HOME')
    home = str(Path(override)) if override else str(Path.home() / '.claude')
    sys.stdout.write((sid if isinstance(sid, str) else '') + '\n' + home + '\n' + (p if isinstance(p, str) else ''))
except Exception:
    sys.exit(1)
" <<< "$payload" 2>/dev/null)"
py_rc=$?

if [ "$py_rc" -eq 0 ]; then
  sid="$(printf '%s' "$extracted" | head -n1)"
  # Line 2: the claude-home the extraction python resolved like mode-state.py's _claude_home().
  py_home="$(printf '%s' "$extracted" | head -n2 | tail -n1)"
  prompt="$(printf '%s' "$extracted" | tail -n +3)"
  match_target="$prompt"
else
  # Python unavailable or JSON parse failed -- fail open to the raw-payload scan (see FAIL-OPEN
  # above). Forcing sid="" here is also what makes the mirror's single non-empty-sid check cover
  # "never mirror without a clean parse". py_home is unused on this path but set for `set -u`.
  sid=""
  py_home=""
  match_target="$payload"
fi

# NOTIFICATION-SHELL DEMOTION: harness-generated turns (task-notifications, background-resume
# replays, and agent output delivered as a turn -- subagent hand-backs in <agent-message from=...>,
# <cross-session-message from=...>) land in .prompt but are never a typed grant. Marker strings
# are harness-version-volatile by nature; the stub's adjudication gate is the version-proof
# backstop if they drift.
# Matched against $match_target so this also covers the fallback path (the raw payload contains
# the .prompt text verbatim either way). This exit precedes BOTH the arm and the mirror -- a
# synthetic turn is never a grant, so it must neither stub nor mirror.
# ACCEPTED RESIDUAL: it also demotes a TYPED prompt that merely QUOTES a marker string (no arm,
# no gate shown). Accepted as the rarer direction -- a real grant can be re-typed plainly.
case "$match_target" in
  *"[SYSTEM NOTIFICATION - NOT USER INPUT]"*|*"<task-notification>"*|*"<agent-message from="*|*"<cross-session-message from="*) exit 0 ;;
esac

# Backtick-quoted spans are mentions by declared convention (see MENTION MARKER above) — replace
# them with a space before the match so `freehand` / `/freehand on` inside inline code never
# arms while adjacent prose keeps its word boundary. If sed itself fails, fall back to the
# unstripped text: fail open toward arming.
scan_target="$(printf '%s' "$match_target" | sed 's/`[^`]*`/ /g')" || scan_target="$match_target"

# Two alternations: (^|non-word) KEYWORD (non-word|$) — "non-word" excludes path/identifier
# chars so embedded-in-filename matches (the IDE-opened-file footgun) don't fire — OR the
# slash-command form (see SLASH-COMMAND FORM above), which the first alternation's boundary
# exclusion would otherwise silence.
if printf '%s' "$scan_target" | grep -iqE '(^|[^[:alnum:]_/\-])(freehand|autopilot)([^[:alnum:]_/\-]|$)|(^|[[:space:]])/(ballast:)?(freehand|autopilot)([^[:alnum:]_/\-]|$)'; then
  # ARM OUTPUT IS STATIC: the same text on every arm, regardless of whether the payload parsed or
  # carried a sid -- no python step, no state write. Keep it that way.
  cat <<'JSON'
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "FREEHAND / AUTOPILOT keyword matched. ADJUDICATE FIRST: a pattern can't tell using the word from mentioning it.\n- Genuine grant: run the freehand skill with argument `on`; do NOT adopt the mode from this note, the contract loads with the skill.\n- Revoke: run the freehand skill with argument `off`.\n- Mention (discussing the mode or this hook, quoting, a filename): ignore this and proceed. A mention never revokes a standing grant.\nIf ambiguous, say in one clause which reading you took."
  },
  "systemMessage": "✈️ ballast: freehand/autopilot keyword armed — Claude adjudicates use-vs-mention"
}
JSON
else
  # STANDING-GRANT MIRROR (else branch: no keyword armed this prompt) -- see MIRROR CONTRACT in
  # the header. Fire condition is the non-empty sid alone, which implies a clean parse.
  if [ -n "$sid" ]; then
    # Root the read at "$py_home", NOT bash's own $HOME/.claude: MSYS bash's $HOME can spell the
    # home directory differently from Python's Path.home(), so a grant mode-state.py WROTE could
    # be invisible to a bash reader rooted at $HOME. BALLAST_CLAUDE_HOME redirects both.
    statefile="$py_home/ballast/modes/$sid"

    # Gate the pipeline on the file existing: the no-grant hot path (most sessions) then skips the
    # grep|cut|tr fork chain entirely -- only a session carrying a grant pays for it.
    if [ -f "$statefile" ]; then
      # Anchored grep for confirmed entries only. `cut -f1` takes the bare mode name off each
      # "<mode> confirmed <epoch>" line; `tr '\n' '/'` joins multiple confirmed modes with '/'.
      # Fail-quiet 2>/dev/null: an unreadable file yields empty and no mirror line. Read-only.
      modes_on="$(grep -E '^(freehand|autopilot) confirmed ' "$statefile" 2>/dev/null | cut -d' ' -f1 | tr '\n' '/')"
      modes_on="${modes_on%/}"
      if [ -n "$modes_on" ]; then
        # modes_on is grep-constrained to the literals freehand/autopilot joined by '/', so it is
        # JSON-safe by construction -- no escaping needed for this printf'd JSON. The
        # additionalContext must OPEN with "MODE MIRROR —", NOT "FREEHAND / AUTOPILOT", so the
        # postmortem extractor never multi-counts one standing grant as a fresh arm every prompt.
        # No systemMessage BY DESIGN -- see VISIBILITY in the header.
        printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"MODE MIRROR — %s ON. If the freehand skill'\''s contract is not in your context (e.g. after a compact), re-run the freehand skill with argument on."}}\n' "$modes_on"
      fi
    fi
  fi
fi
exit 0
