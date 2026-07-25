#!/bin/bash
# UserPromptSubmit hook — delegated-autonomy ("freehand" / "autopilot") mode SENSOR.
#
# This hook is a keyword SENSOR + state MIRROR, not the mode's contract. Two jobs:
#   1. ARM: when the user's typed prompt contains the whole word `freehand` or
#      `autopilot` (case-insensitive), inject a SHORT adjudication stub as
#      additionalContext. The stub carries none of the mode's rules — it tells the
#      model to decide use-vs-mention and, on a genuine grant, to run the freehand
#      skill (which loads the actual contract and settles the chip).
#   2. MIRROR: when NO keyword is present but this session already has a confirmed
#      freehand/autopilot grant on file, inject a one-line standing-grant reminder
#      each prompt so the grant survives a /compact or a long scroll-off.
#
# The full standing contract lives in skills/freehand/SKILL.md — the single
# canonical home. It USED to live here, duplicated across two heredocs (dynamic +
# static), a standing drift hazard guarded only by an anti-drift trip-wire test.
# Moving the contract into the skill body collapses it to one copy AND adds a
# second adoption gate: a keyword arm no longer injects the rules, so adopting the
# mode now requires the model to actively invoke the skill — a mechanical arm on a
# mention can no longer, by itself, put a contract in front of the model.
#
# Orthogonal to `ultracode` (multi-agent thoroughness): both are plain keywords;
# honor whichever are present. The two words are deliberately rare in normal
# prose. We match them only when bounded by NON path/identifier characters (not
# plain `grep -w`) so the keyword embedded in a filename/path doesn't trigger —
# see the caveat below for why that matters.
#
# PRECISION MODEL: the trigger is matched against the extracted `.prompt` field
# ONLY, not the whole stdin payload — parsed via $BALLAST_PYTHON (mirrors
# ballast-principles.sh's `PY="${BALLAST_PYTHON:-python}"` + unquoted-`$PY`
# word-split convention). Claude Code folds editor context (e.g. VS Code's
# <ide_opened_file>...path...</ide_opened_file>) and echoed prior-report text
# into that same stdin payload; scanning the whole blob armed the mode on a
# turn with ZERO typed keywords (live instance 2026-07-08: the hook armed on a
# postmortem turn whose only "autopilot"/"freehand" occurrence was an echoed
# report quoting an earlier grant). Scoping the match to `.prompt` kills that
# false-arm class.
#
# USE-VS-MENTION: the regex ARMS on any prose occurrence of the keyword, but it
# cannot tell USING the word ("autopilot on") from TALKING ABOUT it ("the
# freehand hook is misfiring") — a semantic call no trigger pattern can make,
# and every attempt to encode it as regex exclusions is an overfit band-aid
# (live instance 2026-07-08: the mode armed on a complaint ABOUT this hook,
# post-0.2.0). So the trigger deliberately stays high-recall and precision
# moves to the one component that can judge it: the injected stub OPENS with an
# adjudication gate telling the model to route a genuine grant to the freehand
# skill and to disregard the whole block on a mere mention (quoted samples,
# debugging this hook, discussing the mode). A mechanical arm on a mention is
# therefore expected and harmless BY DESIGN — the regression to watch for is
# the model adopting the mode despite the gate, not the regex matching. Adoption
# now clears a SECOND gate on top of the model's judgment: the stub carries no
# contract, so adopting the mode requires the model to actively invoke the
# freehand skill — a mechanical arm alone can no longer put the rules in context.
# (Subsumes the old ACCEPTED RESIDUAL note: quoted-sample text is now just one
# mention class the gate handles.)
#
# VISIBILITY: every keyword ARM also emits a `systemMessage` one-liner (shown to
# the user in the terminal; supported on all hook events, combinable with
# additionalContext) so an arm is never silent — the user can spot and override
# a wrong adjudication immediately. The standing-grant MIRROR is the deliberate
# exception: it fires on EVERY prompt while the mode is on, so a per-prompt
# systemMessage would be terminal spam. Per hooks/CLAUDE.md's high-frequency-
# fire-path exception it ships NO systemMessage; visibility is carried instead by
# the solid statusline chip (always on-screen while the grant stands) and by
# run.sh's non-empty-stdout fire detection, which still ledgers every mirror fire
# to ~/.claude/ballast-hook-fires.log.
#
# STATUS-LINE CHIP (two-phase, hybrid truth): on the DYNAMIC arm path (see
# EXTRACTION below — it requires a cleanly-parsed payload AND a non-empty
# session_id) this hook raises a PENDING per-session chip via
# statusline/mode-state.py ("raise <mode> --session <sid>") the instant the
# keyword pattern matches — before any adjudication has happened. That chip is
# then SETTLED by whichever adjudication path the model takes:
#   - Genuine grant: the freehand skill's `on` path runs `ballast-mode confirm`,
#     promoting the chip to solid. The confirm instruction is therefore NOT
#     carried in the stub text — it lives in the skill, which recovers the
#     session id from `${CLAUDE_SESSION_ID}` (load-time substitution; see
#     docs/frontmatter.md), not from a model-typed shell.
#   - Mere mention: the stub asks the model to clear the pending chip itself via
#     `ballast-mode clear <mode> --pending-only --session <sid>`. The
#     `--pending-only` guard is load-bearing: it lets that clear remove ONLY this
#     arm's pending chip and never a CONFIRMED grant already standing from an
#     earlier genuine grant this same session — so a mention adjudicated while the
#     mode is legitimately on can't silently revoke the standing grant (the
#     silent-grant-loss seam the fix wave closed). sid is embedded LITERALLY into
#     that clear instruction (never left for the model to fill in from its own
#     environment) because CLAUDE_SESSION_ID is documented as present in HOOK
#     processes but is ABSENT from the model's own Bash-tool-typed environment —
#     there is no other way for the model to recover it later in the turn. (The
#     skill side has no such problem: its `${CLAUDE_SESSION_ID}` substitutes at
#     content-load time inside the plugin loader, not in a model-typed shell.)
# dim/pending is the visual cue for "not yet judged," the same way the stub's
# ADJUDICATE FIRST gate is the textual cue. The chip goes live a beat before the
# judgment that decides whether it should have fired at all. If it is never
# settled (context runs out mid-turn, the turn ends without the follow-up call, a
# crash), the chip is not left dangling forever: statusline/render.py's own TTL
# (pending entries older than 45 minutes are ignored) is the independent backstop
# that ages it out.
#
# STATE-WRITE POSTURE: the `raise` call below is fail-quiet (`... || true`,
# its output discarded) — a state-write failure (disk full, permissions, a
# corrupt ~/.claude/ballast/modes tree) must never affect whether the stub
# text itself gets injected. The chip is a glanceable nicety layered on top of
# the arm, never a precondition for it.
#
# STANDING-GRANT MIRROR (the else branch of the keyword match): a keyword arm
# only fires on the prompt that carries the keyword, but a grant is STANDING — it
# holds for the rest of the session after the prompt that established it. Two ways
# that in-context grant is lost: a /compact that drops the skill's contract text,
# or a long session where the grant scrolls out of the recent window. So on every
# prompt that does NOT arm a keyword, this hook reads the per-session state file
# for a CONFIRMED freehand/autopilot entry and, if present, injects a one-line
# reminder that the mode is on plus how to reload the contract (re-invoke the
# skill). Fire condition is the SAME as the dynamic arm path — py_rc==0 AND a
# non-empty sid; with no sid there is no per-session state file to read, so the
# fallback/raw-payload path never mirrors. Precedence is structural: the mirror
# lives in the else branch, so a keyword arm on the same prompt always wins (the
# stub is the richer signal). NOTIFICATION-SHELL DEMOTION (below) exits before
# BOTH the arm and the mirror BY DESIGN — a synthetic/notification turn is never a
# grant and must neither stub nor mirror. The read is READ-ONLY and fail-quiet
# (missing/unreadable file → no output, never an error). ACCEPTED RESIDUAL / TTL
# HONESTY: the state file is the SINGLE truth source for whether a grant stands.
# render.py's 24h confirmed-chip TTL is DISPLAY-ONLY hygiene, NOT a grant lifetime
# — past 24h the visible chip may age out while the grant legitimately still
# stands, and the mirror keeps firing off the state file (correctly, by design).
# So the mirror does NOT gate on the chip's display TTL; a confirmed entry that
# out-lives its writing session is bounded instead by the SessionEnd
# mode-state-cleanup unlink of the session file plus mode-state.py's 7-day GC of
# stale sibling files — never by render.py's TTL. The mirror is DELIBERATELY
# systemMessage-less — see VISIBILITY above.
#
# FAIL-OPEN: if no working Python is resolvable (bare `python` fails, or
# BALLAST_PYTHON isn't exported by the caller) OR the payload fails to parse as
# JSON, fall back to the CURRENT raw-payload grep (today's behavior, same
# false-arm exposure described above) — never block, always exit 0. The same
# fallback also covers a cleanly-parsed payload that simply has no
# session_id: without a session id there is no per-session state file to raise
# a chip against OR to mirror, so that case degrades to the static, chip-less,
# mirror-less path. The standing-grant mirror is purely additive and
# independently fail-quiet: its state-file read is `2>/dev/null` and any
# missing/unreadable file simply yields no mirror line, never an error.
#
# Output mechanism mirrors pre-commit-review-reminder.sh: JSON on stdout via
# hookSpecificOutput.additionalContext (a bare echo would only show in transcript
# mode). No match -> no output -> no injection. Always exits 0 (never blocks a
# prompt). Soft + fast (bash + a python extraction step + grep) since it runs
# on every submit.
#
# Caveat (fallback path only): when python is unavailable or parsing fails, the
# trigger is matched against the WHOLE stdin payload, NOT just the typed prompt
# — so the IDE-opened-file / echoed-report false-arm described above can still
# fire in that degraded path. The boundary class below excludes alnum/_/-//\ on
# both sides, so a keyword inside a path/identifier (freehand-mode.sh,
# path/autopilot/x, freehand_mode) is rejected while real prose ("go freehand.",
# "freehand mode", "(autopilot)") still matches.

set -u

# Resolve our own directory via BASH_SOURCE (mirrors hooks/run.sh and bin/ballast-extract) so the
# mode-state.py reference below is robust regardless of caller cwd. Deliberately NOT relying on
# bin/ being on PATH or on CLAUDE_PLUGIN_ROOT being set: neither is guaranteed inside a hook child
# process, whereas BASH_SOURCE self-location has no such caveat.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

payload="$(cat)"

PY="${BALLAST_PYTHON:-python}"

# Extract THREE fields, one per output line: session_id (line 1), the resolved claude-home
# (line 2), and .prompt (line 3 onward — the prompt may itself contain newlines, so everything
# from the second line boundary on is prompt text verbatim; the bash side below splits with
# `head -n1` / `head -n2|tail -n1` / `tail -n +3`). Exits 1 on ANY parse failure (bad JSON,
# "python not found" -> bash rc 127, etc.) so the caller below can distinguish a clean-but-empty
# prompt (rc=0, empty string -- e.g. a prompt-less or non-object payload) from a genuine parse
# failure (rc!=0) and pick the fallback deliberately rather than by accident. session_id is
# extracted with the exact same isinstance-guarded shape as prompt.
#
# F1 (shared-home resolution): the claude-home is computed HERE, in the same python that already
# runs on every clean-parse fire, EXACTLY like statusline/mode-state.py's _claude_home()
# (BALLAST_CLAUDE_HOME override, else Path.home()/.claude). The standing-grant mirror below reads
# the state file from this python-resolved home rather than from bash's own $HOME/.claude, so the
# reader and mode-state.py (the WRITER) resolve the file to the identical directory -- see the F1
# note at the mirror's statefile assignment for why the old bash-$HOME-vs-python-home split was a
# real divergence on Windows.
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
  # F1: py_home is line 2 -- the claude-home the extraction python resolved EXACTLY like
  # mode-state.py's _claude_home(). The standing-grant mirror below roots its state-file read at
  # "$py_home/..." so that reader and the writer (mode-state.py) resolve the identical directory.
  py_home="$(printf '%s' "$extracted" | head -n2 | tail -n1)"
  prompt="$(printf '%s' "$extracted" | tail -n +3)"
  match_target="$prompt"
else
  # Python unavailable or JSON parse failed -- fail open to the raw-payload scan
  # (today's behavior; see the fallback-path caveat above). No session_id is
  # available in this path either, which is exactly why the dynamic
  # chip-raising branch AND the standing-grant mirror below additionally
  # require a clean parse. py_home is unused here (the mirror never runs without a sid) but is
  # set empty so `set -u` can never trip if a future edit references it on this path.
  sid=""
  py_home=""
  match_target="$payload"
fi

# F2(b): clean-parse eligibility, computed ONCE. A cleanly-parsed payload (py_rc==0) that carried
# a real session_id is the shared precondition for BOTH the dynamic chip/stub path and the
# standing-grant mirror below -- with no sid there is no per-session state file to raise a chip
# against OR to read for a mirror. Deriving it once here keeps the two branches from drifting apart.
dyn_ok=no
[ "$py_rc" -eq 0 ] && [ -n "$sid" ] && dyn_ok=yes

# NOTIFICATION-SHELL DEMOTION: harness-generated turns (task-notifications,
# background-resume replays) land in .prompt but are never a typed grant --
# 4 postmortem-pinned false-arms. Marker strings are harness-version-volatile
# by nature; the adjudication gate in the injected stub is the version-proof
# backstop if they drift. Matched against $match_target (not $prompt directly)
# so this ALSO covers the python-unavailable fallback path: match_target there
# is the raw stdin payload rather than the extracted .prompt field, but the
# raw payload still contains the .prompt text verbatim, so the markers are
# present in it either way. This exit precedes BOTH the keyword-arm branch and
# the standing-grant mirror in its else -- a synthetic turn is never a grant, so
# it must neither stub nor mirror.
# ACCEPTED RESIDUAL: this also demotes a TYPED prompt that merely QUOTES one of these marker
# strings (no arm, no gate shown) -- e.g. pasting a notification excerpt while discussing this
# hook. Accepted as the rarer direction: a user actually granting freehand/autopilot can just
# re-type the grant plainly, without quoting harness marker text.
case "$match_target" in
  *"[SYSTEM NOTIFICATION - NOT USER INPUT]"*|*"<task-notification>"*) exit 0 ;;
esac

# (^|non-word) KEYWORD (non-word|$) — "non-word" excludes path/identifier chars
# so embedded-in-filename matches (the IDE-opened-file footgun) don't fire.
if printf '%s' "$match_target" | grep -iqE '(^|[^[:alnum:]_/\-])(freehand|autopilot)([^[:alnum:]_/\-]|$)'; then
  # Populated by the dynamic path below; if it stays empty (path not eligible, or the emit step
  # itself failed), the static fallback heredoc at the bottom fires instead -- an armed keyword
  # must ALWAYS produce an injection, whichever path renders it.
  emitted=""
  if [ "$dyn_ok" = yes ]; then
    # ============================================================================================
    # DYNAMIC PATH -- sid is known (a cleanly-parsed payload carried a real session_id), so we can
    # identify + raise a per-session status-line chip alongside the injected stub.
    # ============================================================================================

    # MODE LABEL -- the double-grep is deliberate, not redundant. The first grep -oiE reuses the
    # EXACT SAME boundary-qualified pattern used for the arm decision above, so the label comes
    # from the first GENUINE keyword occurrence (bounded by non-identifier chars) rather than a
    # bare substring grab -- otherwise a path-embedded hit earlier in the prompt (e.g. this very
    # file's name, "hooks/freehand-mode.sh") could steal the label away from a real "autopilot"
    # mention appearing later in the same prompt. `head -n1` keeps only that first match
    # (including its boundary characters); the second grep -oiE then strips those boundary
    # characters back off, leaving just the bare keyword; `tr` lowercases it (mode-state.py's
    # MODE_RE expects lowercase).
    mode="$(printf '%s' "$match_target" \
      | grep -oiE '(^|[^[:alnum:]_/\-])(freehand|autopilot)([^[:alnum:]_/\-]|$)' \
      | head -n1 \
      | grep -oiE '(freehand|autopilot)' \
      | tr '[:upper:]' '[:lower:]')"

    # Raise the PENDING chip, fail-quiet: a state-write failure must never affect whether the
    # stub text below gets injected. See the STATE-WRITE POSTURE header note above.
    # shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
    $PY "$DIR/../statusline/mode-state.py" raise "$mode" --session "$sid" >/dev/null 2>&1 || true

    # ============================================================================================
    # STUB TEXT PAIRING (site 1 of 2 -- see the matching comment at the static fallback heredoc
    # below, in the else branch of the arm): this is the SAME short adjudication-stub prose as the
    # static fallback JSON's additionalContext, plus one appended MODE CHIP paragraph the fallback
    # intentionally omits (no python there -> no chip was ever raised -> nothing to settle).
    # Carried here as plain text (not JSON-escaped) because this path builds its JSON through
    # python's json.dumps below rather than hand-escaping. EDIT BOTH COPIES TOGETHER or the two
    # paths drift apart -- test_freehand_mode.sh's "gate sentence present" case is the anti-drift
    # trip-wire: it asserts the ADJUDICATE FIRST opening sentence appears in BOTH this dynamic
    # output and the static fallback output (it cannot assert full-text equality, since this copy
    # legitimately carries the extra MODE CHIP paragraph). The mode's actual rules are NOT here in
    # either copy -- they live in skills/freehand/SKILL.md, loaded only when the model invokes the
    # skill on a genuine grant.
    #
    # <mode> and <sid> below are literal placeholder tokens, substituted by the python step
    # further down (via argv-provided mode/sid) -- kept as plain text here so this whole block can
    # travel through an environment variable untouched, with no shell interpolation of its
    # contents (the paragraph contains backticks; see the json.dumps rationale below).
    # ============================================================================================
    stub_text="$(cat <<'STUB'
FREEHAND / AUTOPILOT — keyword-armed injection. ADJUDICATE FIRST: a pattern matched the word 'freehand' or 'autopilot' in the typed prompt; a pattern cannot tell USING the keyword from MENTIONING it, so that call is yours.

Freehand/autopilot is ballast's delegated-autonomy mode: a standing session grant handing Claude the design decisions the user left unspecified. The full standing contract lives in the freehand skill body — do NOT adopt the mode from this stub; the contract is not in force until the skill loads it. (If a genuine grant is already standing this session, its contract remains in force — this arm changes nothing.)

- Genuine grant (the user is invoking delegated autonomy): run the freehand skill with argument `on` for the `<mode>` grant — the skill loads the contract and settles the chip.
- Revoke (the user is turning the mode off): run the freehand skill with argument `off` — it clears every standing autonomy grant.
- Mere mention (discussing or debugging this hook or the mode itself, quoting a past prompt, naming a file): a false-arm — disregard this block, do not adopt or announce the mode, and proceed with the request as written.
- If genuinely ambiguous, say in one clause which reading you took.

MODE CHIP: a pending status-line chip was raised for this arm. A genuine grant or a revoke settles it via the skill; on a mere mention, settle it yourself: run `ballast-mode clear <mode> --pending-only --session <sid>` (bare, un-chained — `--pending-only` clears only this arm's pending chip and never touches a standing confirmed grant).
STUB
    )"

    sysmsg_base="✈️ ballast: freehand/autopilot keyword armed — Claude adjudicates use-vs-mention (a mention is disregarded)"

    # Emit via python json.dumps rather than a bash heredoc: the appended MODE CHIP paragraph
    # contains backticks (`ballast-mode clear ...`), and interpolating backtick-bearing text
    # into an UNQUOTED heredoc is a command-substitution hazard (bash would try to execute the
    # backtick-quoted text as a command) -- the same hazard class that pushed doc-write-guard to
    # python for its own dynamic JSON emission. Stub text and the base systemMessage travel
    # via environment variables (arbitrary prose, handled entirely by json.dumps -- no
    # shell-escaping needed) and mode/sid travel via argv (short, already-validated-shaped
    # tokens); python does the <mode>/<sid> placeholder substitution itself, so nothing here ever
    # round-trips through bash string interpolation into a heredoc body. The python source below
    # is plain ASCII on purpose (no literal emoji/arrow characters in the SOURCE) -- all non-ASCII
    # text is DATA carried in via the env vars, sidestepping any question of how the interpreter's
    # own source-decoding treats a piped-in script.
    # Captured (not streamed) so an unexpected emit failure falls through to the static JSON
    # below instead of silently dropping the injection -- the same output-capture ladder
    # plan-handoff.sh uses. Residual on that failure path: the pending chip was already raised
    # above and its settle instruction is lost with the dynamic text; render.py's 45-min
    # pending TTL is what ages the orphaned chip out.
    # shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
    emitted="$(BALLAST_FREEHAND_TEXT="$stub_text" BALLAST_FREEHAND_SYSMSG="$sysmsg_base" $PY - "$mode" "$sid" 2>/dev/null <<'PY'
import json, os, sys

mode, sid = sys.argv[1], sys.argv[2]
text = os.environ.get("BALLAST_FREEHAND_TEXT", "").replace("<mode>", mode).replace("<sid>", sid)
sysmsg = os.environ.get("BALLAST_FREEHAND_SYSMSG", "") + " (chip pending)"

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": text,
    },
    "systemMessage": sysmsg,
}))
PY
)"
  fi
  if [ -n "$emitted" ]; then
    printf '%s\n' "$emitted"
  else
    # ============================================================================================
    # FALLBACK PATH -- python unavailable, JSON parse failed, session_id absent from an
    # otherwise-clean payload, or the dynamic emit above unexpectedly produced nothing: the static
    # adjudication-stub heredoc, hand-JSON-escaped. No chip instruction, no state write: without a
    # resolvable session_id there is no per-session file to raise a chip against, so the injected
    # stub can promise nothing about a chip that was never raised. See the fallback-path caveat in
    # the header comment above for the raw-payload-scan exposure this path shares with the plain
    # match-target fallback.
    #
    # STUB TEXT PAIRING (site 2 of 2 -- see the matching comment at the dynamic stub_text heredoc
    # above): this JSON-escaped text is the SAME short adjudication-stub prose as the dynamic
    # path's stub_text, minus the MODE CHIP paragraph (never raised in this path, so never
    # promised). EDIT BOTH COPIES TOGETHER or the two paths drift apart --
    # test_freehand_mode.sh's "gate sentence present" case pins the ADJUDICATE FIRST opening
    # sentence in both outputs as the anti-drift trip-wire.
    # ============================================================================================
    cat <<'JSON'
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "FREEHAND / AUTOPILOT — keyword-armed injection. ADJUDICATE FIRST: a pattern matched the word 'freehand' or 'autopilot' in the typed prompt; a pattern cannot tell USING the keyword from MENTIONING it, so that call is yours.\n\nFreehand/autopilot is ballast's delegated-autonomy mode: a standing session grant handing Claude the design decisions the user left unspecified. The full standing contract lives in the freehand skill body — do NOT adopt the mode from this stub; the contract is not in force until the skill loads it. (If a genuine grant is already standing this session, its contract remains in force — this arm changes nothing.)\n\n- Genuine grant (the user is invoking delegated autonomy): run the freehand skill with argument `on` for the invoked mode — the skill loads the contract and settles the chip.\n- Revoke (the user is turning the mode off): run the freehand skill with argument `off` — it clears every standing autonomy grant.\n- Mere mention (discussing or debugging this hook or the mode itself, quoting a past prompt, naming a file): a false-arm — disregard this block, do not adopt or announce the mode, and proceed with the request as written.\n- If genuinely ambiguous, say in one clause which reading you took."
  },
  "systemMessage": "✈️ ballast: freehand/autopilot keyword armed — Claude adjudicates use-vs-mention (a mention is disregarded)"
}
JSON
  fi
else
  # ==============================================================================================
  # STANDING-GRANT MIRROR (else branch: no keyword armed this prompt). If this session already
  # carries a CONFIRMED freehand/autopilot grant, re-inject a one-line reminder so the grant
  # survives a /compact or a long scroll-off. See the STANDING-GRANT MIRROR header note for the
  # full rationale (precedence, demotion-precedes, cleanup/GC-bounded staleness, silent-by-design).
  #
  # Fire condition is the shared clean-parse eligibility computed once above (dyn_ok): a
  # cleanly-parsed payload AND a non-empty sid -- with no sid there is no per-session state file to
  # read, so the python-unavailable fallback path never mirrors.
  # ==============================================================================================
  if [ "$dyn_ok" = yes ]; then
    # F1 (shared-home resolution): read from "$py_home" -- the claude-home the extraction python
    # resolved EXACTLY like mode-state.py's _claude_home() -- NOT bash's own $HOME/.claude. This
    # kills a real Windows divergence: MSYS bash's $HOME can spell the home directory differently
    # from Python's Path.home(), so a grant mode-state.py WROTE under the python home could be
    # invisible to a bash reader rooted at $HOME/.claude. Reader and writer now resolve identically.
    # BALLAST_CLAUDE_HOME still redirects BOTH (the extraction python honors it too) at a hermetic
    # temp home in the suite; production sets neither, so both land on the real ~/.claude.
    statefile="$py_home/ballast/modes/$sid"

    # F2(a): gate the whole grep pipeline on the state file existing. On the no-grant hot path
    # (the common case -- most sessions never grant the mode) there is no state file, so this skips
    # the grep|cut|tr fork chain entirely; only a session that actually carries a grant pays for it.
    if [ -f "$statefile" ]; then
      # Anchored grep for confirmed entries only (pending chips are NOT a standing grant, so they
      # do not mirror). `cut -f1` takes the bare mode name off each matched "<mode> confirmed
      # <epoch>" line; `tr '\n' '/'` joins multiple confirmed modes with a '/'. Fail-quiet
      # 2>/dev/null: an unreadable file yields empty and no mirror line. Read-only -- never writes.
      modes_on="$(grep -E '^(freehand|autopilot) confirmed ' "$statefile" 2>/dev/null | cut -d' ' -f1 | tr '\n' '/')"
      modes_on="${modes_on%/}"
      if [ -n "$modes_on" ]; then
        # modes_on is grep-constrained to the literals freehand/autopilot joined by '/', so it is
        # JSON-safe by construction -- no escaping needed for this printf'd JSON. The
        # additionalContext deliberately OPENS with "MODE MIRROR —", NOT "FREEHAND / AUTOPILOT", so
        # the postmortem extractor never multi-counts one standing grant as a fresh arm on every
        # prompt. No systemMessage BY DESIGN (high-frequency fire path; see VISIBILITY header note).
        # F2(c): the reload verb "argument on" is a LITERAL in the format string now (no more
        # "'on'" quote-spliced printf argument); both %s are modes_on -- the mode name(s) on.
        printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"MODE MIRROR — %s ON (standing session grant). The contract is the freehand skill body — if it is not in your context (e.g. after a compact), re-run the freehand skill with argument on for %s. Otherwise no action needed."}}\n' "$modes_on" "$modes_on"
      fi
    fi
  fi
fi
exit 0
