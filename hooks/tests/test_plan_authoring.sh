#!/usr/bin/env bash
# Regression tests for plan-authoring.sh -- the PostToolUse nudge that injects executor-ready
# batching conventions while a plan file is still being drafted.
#
# WHY committed: the hook greps the RAW stdin payload (no jq, no JSON parsing -- decoding Windows
# paths is the backslash hazard that pushed doc-write-guard to python), so its trigger is a
# path-shape regex that has to tolerate BOTH slash styles and the JSON-escaped double backslash.
# It also carries a 2h session cooldown, because plan drafting is many successive edits to one
# file and the conventions text is static. These cases pin the path matching, the cooldown
# (set / suppress / per-session scope / TTL re-arm), and the fail-open when no session_id can be
# extracted, so none of them can silently regress.
#
# Payloads are DATA on stdin, so running this file never trips the caller's own hooks.
# Self-locating: finds plan-authoring.sh one directory up (hooks/); runs wherever it's checked
# out. HERMETIC: BALLAST_CLAUDE_HOME redirects the cooldown marker dir to a throwaway temp home,
# never the developer's real ~/.claude. Exit code = # of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/../plan-authoring.sh"

# plan-authoring.sh never shells out to python (pure bash + grep on the raw payload) -- resolving
# PY here is for the TEST's own marker-backdating step (below), and mirrors this dir's other
# suites' probe loop.
# The specific hazard: a Windows App-Execution-Alias stub (`.../WindowsApps/python3`) sits on
# PATH but does NOT merely exit non-zero when executed non-interactively -- it HANGS INDEFINITELY
# (the App Installer redirector blocks on a Microsoft Store UI a headless shell can never
# satisfy), wedging the caller instead of falling through. Two COMPLEMENTARY defenses: TIMEOUT
# (bounds every probe execution so a hang loses the race) and TWO-PASS path RESOLUTION (pass 1
# resolves each candidate to the first PATH hit that is NOT an alias and probes THAT absolute
# path -- resolving past the alias rather than skipping the whole candidate, since the common
# Windows PATH layout puts the alias AHEAD of a real install; pass 2 retries by bare name only if
# nothing real resolved). Where timeout is absent, resolution is what keeps the alias unexecuted.
if command -v timeout >/dev/null 2>&1; then PROBE="timeout 5"; else PROBE=""; fi
PY=""
for pass in skip-stubs allow-stubs; do
  for c in python3 python "py -3"; do
    run="$c"
    if [ "$pass" = "skip-stubs" ]; then
      real="$(type -aP "${c%% *}" 2>/dev/null | grep -iv windowsapps | head -1 || true)"
      [ -z "$real" ] && continue
      case "$c" in *" "*) run="$real ${c#* }" ;; *) run="$real" ;; esac
    fi
    # shellcheck disable=SC2086 -- intentional word-split for "py -3" and the optional timeout.
    if $PROBE $run -c 'import sys' >/dev/null 2>&1; then PY="$run"; break 2; fi
  done
done
export BALLAST_PYTHON="$PY"

HERMETIC_HOME="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/planauth-home.$$")"
mkdir -p "$HERMETIC_HOME"
export BALLAST_CLAUDE_HOME="$HERMETIC_HOME"
MARKERS="$HERMETIC_HOME/.cache/ballast-plan"

fails=0

# check <name> <payload> <want_fire: yes|no> <want_substr-if-fired|->
check() {
  local name="$1" payload="$2" want_fire="$3" want="$4" out rc ok=1 fired=no
  out="$(printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) fired=yes ;; esac
  [ "$fired" = "$want_fire" ] || ok=0
  [ "$want" != "-" ] && { case "$out" in *"$want"*) ;; *) ok=0 ;; esac; }
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s fired=%s\n' "$name" "$rc" "$fired"
  else printf 'FAIL  %-42s rc=%s fired=%s (want fired=%s)\n      out=%s\n' "$name" "$rc" "$fired" "$want_fire" "$out"; fails=$((fails+1)); fi
}

# check_marker <name> <sid> <exists|absent>
check_marker() {
  local name="$1" sid="$2" want="$3" got=absent
  [ -f "$MARKERS/$sid" ] && got=exists
  if [ "$got" = "$want" ]; then printf 'PASS  %-42s marker=%s\n' "$name" "$got"
  else printf 'FAIL  %-42s marker=%s (want %s)\n' "$name" "$got" "$want"; fails=$((fails+1)); fi
}

# P <session_id> <file_path> -- a PostToolUse Write payload. The file_path is written with the
# JSON-escaped double backslash a real Windows payload carries.
P()  { printf '{"tool_name":"Write","session_id":"%s","tool_input":{"file_path":"%s"}}' "$1" "$2"; }
PNS(){ printf '{"tool_name":"Write","tool_input":{"file_path":"%s"}}' "$1"; }

# --- path matching: both slash styles, and a non-plan path must stay silent ---
check "forward-slash plans path fires" "$(PNS 'C:/Users/x/.claude/plans/slug.md')" \
                                        yes "PLAN FILE IN PROGRESS"
check "escaped-backslash path fires"   "$(PNS 'C:\\Users\\x\\.claude\\plans\\slug.md')" \
                                        yes "PLAN FILE IN PROGRESS"
check "non-plan path stays silent"     "$(PNS 'C:/Users/x/src/main.py')"      no -
check "plans dir outside .claude quiet" "$(PNS 'C:/Users/x/docs/plans/slug.md')" no -

# --- injected content: the batching conventions the hook exists to deliver ---
check "names the executor ladder"      "$(PNS 'C:/x/.claude/plans/p.md')"     yes "plan-executor ladder"
check "carries the ignore clause"      "$(PNS 'C:/x/.claude/plans/p.md')"     yes "matched by accident"
check "carries systemMessage"          "$(PNS 'C:/x/.claude/plans/p.md')"     yes "systemMessage"

# --- SESSION COOLDOWN (2h TTL marker): plan drafting is many successive edits to the same file
# and the conventions text is static, so one injection per session covers the drafting run. ---
check_marker "cooldown: no marker up front" pa1 absent
check "cooldown: first write fires"    "$(P pa1 'C:/x/.claude/plans/p.md')"   yes "PLAN FILE IN PROGRESS"
check_marker "cooldown: marker written" pa1 exists
check "cooldown: second write silent"  "$(P pa1 'C:/x/.claude/plans/p.md')"   no -
check "cooldown: other plan file silent" "$(P pa1 'C:/x/.claude/plans/other.md')" no -
check "cooldown: other session fires"  "$(P pa2 'C:/x/.claude/plans/p.md')"   yes "PLAN FILE IN PROGRESS"

# Age the marker past the 2h TTL -- a stale marker must re-arm (a long or post-compact session).
# Backdated via python's os.utime, NOT `touch -d`/`touch -r`: the relative-date forms are
# GNU-only, so on BSD/macOS they would silently no-op and turn this into a false PASS.
# shellcheck disable=SC2086 -- intentional word-split for a two-word "py -3".
if [ -n "$PY" ]; then
  $PY -c "import os,time,sys; t=time.time()-3*3600; os.utime(sys.argv[1],(t,t))" "$MARKERS/pa1"
  check "cooldown: stale marker re-arms" "$(P pa1 'C:/x/.claude/plans/p.md')" yes "PLAN FILE IN PROGRESS"
else
  printf 'SKIP  %-42s (no python to backdate the marker)\n' "cooldown: stale marker re-arms"
fi

# FAIL OPEN: no extractable sid -> no cooldown, every plan write fires (the pre-cooldown behavior).
check "cooldown: no sid fires again"   "$(PNS 'C:/x/.claude/plans/p.md')"     yes "PLAN FILE IN PROGRESS"
check "cooldown: no sid fires twice"   "$(PNS 'C:/x/.claude/plans/p.md')"     yes "PLAN FILE IN PROGRESS"

rm -rf "$HERMETIC_HOME" 2>/dev/null

echo
[ "$fails" = 0 ] && echo "ALL plan-authoring TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
