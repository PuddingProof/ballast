#!/usr/bin/env bash
# harness-sweep-nudge.sh -- SessionStart hook. Self-prompting nudge for the cross-project
# postmortem sweep loop: without this, "go sweep the harness reports" only happens when the
# user remembers to say so by hand. This hook counts unseen reports against the user's
# SWEEP-STATE.md registry (seeded/maintained by the harness-sweep skill) and, if there's
# unswept signal, injects a one-line reminder pointing at that skill. It never extracts,
# reconciles, or reads report bodies itself -- purely a cheap count-and-nudge.
#
# FAIL-OPEN GUARANTEE: every early-exit path below is `exit 0` with zero stdout. A missing
# CLAUDE_PROJECT_DIR, a missing state file, an unresolvable python, a malformed registry line --
# none of these may ever block or degrade session start. Worst case is a dead nudge (no reminder
# this session), never a broken one.
#
# POSTURE: soft nudge (systemMessage + additionalContext), never a block -- there is no
# correctness or safety property to enforce here, just a reminder that costs nothing to ignore.
# The additionalContext explicitly says "Ignore this" for exactly that reason (durable-docs
# gate: injected text must no-op gracefully on a non-match / mis-fire / not-relevant-this-session).
#
# SOURCE-REPO SENTINEL (first hook to gate on this): this nudge is only meaningful in the
# ballast SOURCE repo (the harness-owning project where harness-sweep lives and where the
# hub-dir/ballast registry entries actually get triaged) -- an installed consumer copy of the
# plugin has no dev/ directory (dev/ never ships; see repo CLAUDE.md "Shipped-content rules")
# and no reason to see this reminder. `.claude-plugin/marketplace.json` alone is not a strong
# enough signal (a consumer project could plausibly vendor or reference plugin manifests), so
# we additionally require `dev/check.sh` -- the co-presence of BOTH uniquely identifies "this is
# the ballast source checkout, not merely a project with the plugin installed."
#
# NAMED RESIDUAL RISKS (accepted, not bugs, both pinned by the test suite so a future edit can't
# silently change either): (1) the strict lexical `>` same-day miss documented at step 3;
# (2) CLAUDE_PROJECT_DIR unset -> step 1 exits silently, a dead nudge, no fallback probe added.

set -u

# --- 1. Source-repo gate -----------------------------------------------------------------
# See "SOURCE-REPO SENTINEL" above for why both files are required together.
proj="${CLAUDE_PROJECT_DIR:-}"
[ -n "$proj" ] || exit 0
[ -f "$proj/.claude-plugin/marketplace.json" ] && [ -f "$proj/dev/check.sh" ] || exit 0

# --- 2. State-home resolver ---------------------------------------------------------------
# Verbatim copy of run.sh's ledger() resolver: BALLAST_CLAUDE_HOME (hermetic test override,
# same var the fire-ledger and commit-review-gate suites use) takes precedence over
# $HOME/.claude (production). If neither resolves to a non-empty dir, there is nowhere the
# registry could live -- exit silently rather than guessing a path.
dir="${BALLAST_CLAUDE_HOME:-}"
[ -z "$dir" ] && dir="${HOME:+$HOME/.claude}"
[ -n "$dir" ] || exit 0
state="$dir/postmortem/SWEEP-STATE.md"
[ -f "$state" ] || exit 0

# --- 3. Count unseen reports per registry line (strict lexical >) -------------------------
# Registry line format (written by the harness-sweep skill, never this hook):
#   <name> · <absolute-postmortem-dir> · last=<report-basename|NONE>
# The separator is the exact 3-byte literal " · " (space, U+00B7 MIDDLE DOT as UTF-8, space).
# We match on it byte-for-byte via a `case` glob rather than a locale-sensitive tool (e.g. awk
# field-splitting, which can behave differently under a non-UTF-8 locale) -- `case` pattern
# matching on a shell string is a pure byte comparison, so this is locale-immune by construction.
#
# Once a line contains the literal, pure parameter-expansion (no fork, no external tool) pulls the
# three fields out of it -- shortest-match strip from the front for <dir>, longest-match strips for
# the watermark, then the literal "last=" prefix off (leaving "<wm>" or "NONE").
total=0
projects=0
# Harden against hand-edited registry files (SKILL.md invites hand-editing SWEEP-STATE.md):
# `|| [ -n "$line" ]` keeps a final registry line without a trailing newline from being
# silently dropped (plain `while read` skips a no-newline last line). The `${line%$'\r'}`
# strip guards a Windows editor saving CRLF -- without it, a trailing '\r' survives onto `wm`
# ("NONE\r" != "NONE"), so a `last=NONE` registry line would silently count zero reports (a
# dead nudge) -- the exact hazard dev/render-context.py's fixture-writer comment documents for
# its own LF-forcing. Both hazards are pinned by the test suite.
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in
    *" · "*) ;;
    *) continue ;;   # malformed / prose / header line -- skip silently (no " · " literal present)
  esac
  rest="${line#* · }"
  pdir="${rest%% · *}"
  wm="${rest##* · }"
  wm="${wm#last=}"

  [ -d "$pdir" ] || continue   # dead/moved project dir -- skip this registry line entirely

  n=0
  # Candidate files: date-prefixed report basenames only (YYYY-MM-DD-*.md). This glob shape
  # deliberately EXCLUDES non-report .md files that legitimately live in the same postmortem
  # dir -- SWEEP-STATE.md itself, HARNESS-RECS.md, DRIFT-SIGHTINGS.md -- none of which start
  # with a 4-digit year, so none of them match `[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md`.
  for f in "$pdir"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md; do
    [ -e "$f" ] || continue   # glob didn't match anything real -- literal pattern with no expansion
    b="${f##*/}"
    # "NONE" watermark means "never swept" -- everything counts. Otherwise strict lexical `>`:
    # because report basenames are YYYY-MM-DD-<slug>-<uuid8>.md (lexical order == chronological
    # order for the date-prefix), this correctly orders across different dates. Its accepted gap
    # (residual risk #1 above) is SAME-day: two files sharing a date prefix compare by slug/uuid
    # text, not by which was written first, so a same-day file lexically "before" the watermark's
    # slug is silently NOT counted even though it's actually a new, unseen report. Cheap and
    # backstopped by the skill's inclusive mode -- not fixed here on purpose.
    { [ "$wm" = "NONE" ] || [ "$b" \> "$wm" ]; } && n=$((n+1))
  done
  [ "$n" -gt 0 ] && { total=$((total+n)); projects=$((projects+1)); }
done < "$state"

# --- 4. Drift inbox: date-led lines not yet marked registered ------------------------------
# DRIFT-SIGHTINGS.md lines look like "YYYY-MM-DD · <sighting text> ... [registered]" once
# triaged. We count date-led lines that do NOT contain "registered" anywhere.
#
# `grep -cv` returns exit 1 when ZERO lines are selected -- a valid "0 unregistered sightings"
# answer, not an error. `|| true` keeps that from being misread downstream as a failure, and
# `drift="${drift:-0}"` defaults an empty substitution (e.g. the file vanished after the `-f`
# check). Keep both.
drift=0
sight="$dir/postmortem/DRIFT-SIGHTINGS.md"
[ -f "$sight" ] && drift="$(grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2} · ' "$sight" 2>/dev/null | grep -cv 'registered' || true)"
drift="${drift:-0}"

# --- 5. Zero signal => zero bytes, exit 0 --------------------------------------------------
# run.sh's fire ledger only logs a line when stdout is non-empty (or rc != 0) -- so emitting
# nothing here is also how we keep a quiet session OUT of the fire ledger, not just out of the
# user's face. Both total and drift must be zero for us to stay silent.
[ "$total" -gt 0 ] || [ "$drift" -gt 0 ] || exit 0

# --- 6. Emit -- mirrors ballast-principles.sh's JSON-emit pattern exactly ------------------
# PY resolution and the UNQUOTED $PY word-split follow ballast-principles.sh verbatim: run.sh
# may resolve BALLAST_PYTHON to the two-word "py -3" (Windows py-launcher), which must
# word-split into `py` `-3` as separate argv words here. Quoting "$PY" would instead look for a
# single program literally named "py -3" and fail -- the exact bug class that left the
# principles injection silently empty on some Windows boxes.
PY="${BALLAST_PYTHON:-python}"

# shellcheck disable=SC2086
output="$($PY -c "
import json, sys

total, projects, drift = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])

def plural(n, noun):
    return '%d %s%s' % (n, noun, '' if n == 1 else 's')

parts = []
if total > 0:
    parts.append('%s across %s' % (plural(total, 'new report'), plural(projects, 'project')))
if drift > 0:
    parts.append(plural(drift, 'unregistered drift sighting'))
summary = '; '.join(parts)

additional_context = (
    'Harness-sweep nudge: %s awaiting triage. If harness work is on this session\'s agenda, '
    'run the harness-sweep skill to extract them and reconcile against the ledgers into a '
    'triage digest. Not planning harness work this session? Ignore this.' % summary
)

print(json.dumps({
    'systemMessage': '⚓ ballast: harness-sweep — %s' % summary,
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': additional_context,
    }
}))
" "$total" "$projects" "$drift" 2>/dev/null)"

if [ -z "$output" ]; then
  # Python unavailable or errored -- fail open, no nudge this session.
  exit 0
fi

printf '%s\n' "$output"
exit 0
