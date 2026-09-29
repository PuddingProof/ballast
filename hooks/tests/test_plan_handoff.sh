#!/usr/bin/env bash
# Regression tests for hooks/plan-handoff.sh -- the PostToolUse/ExitPlanMode plan-handoff pointer:
# one text path, the sid grep-extracted in bash, the statusline "exec" chip raised CONFIRMED on a
# valid sid, and a clear-instruction appended to the injected text.
#
# WHAT THESE CASES PIN (every case's stdout must also parse as JSON):
#   1. valid payload + session_id -> pointer + clear-instruction AND a confirmed "exec" line written
#      to the session's mode-state file.
#   2. payload with no session_id -> pointer present, no ballast-mode string, no state file.
#   3. a sid containing `"` -> pointer present, no chip clause (the sid is rejected, never
#      interpolated).
#   4. state dir unwritable -> the mode-state write fails silently but the hook still emits valid
#      JSON and exits 0 (fail-quiet contract).
#   5. the distinctive pointer sentence and the frontend/visual clause are present.
#
# Hermetic via BALLAST_CLAUDE_HOME (mode-state.py's own override) so no test ever touches the real
# ~/.claude/ballast/modes/. Payloads are DATA on stdin (never executed). Self-locating: finds
# plan-handoff.sh one directory up (hooks/). Exit code = number of failures.

set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/../plan-handoff.sh"

# Resolve a working Python 3 the same way run.sh's own probe does.
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
      # ${c%% *} = the bare command word ("py" for the two-word "py -3" candidate). `type -aP`
      # lists EVERY PATH hit, not just the first, so a real install shadowed by the alias is
      # still reachable; -i because Windows paths are case-insensitive.
      real="$(type -aP "${c%% *}" 2>/dev/null | grep -iv windowsapps | head -1 || true)"
      [ -z "$real" ] && continue
      # Swap the resolved absolute path in for the command word, keeping any trailing args
      # (the "-3" of "py -3").
      case "$c" in *" "*) run="$real ${c#* }" ;; *) run="$real" ;; esac
    fi
    # shellcheck disable=SC2086 -- intentional word-split for "py -3" and the optional timeout.
    if $PROBE $run -c 'import sys' >/dev/null 2>&1; then PY="$run"; break 2; fi
  done
done
export BALLAST_PYTHON="$PY"

fails=0

tmp_dirs=""
newtmp() {
  local d
  d="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/plan-handoff-test.$$.$RANDOM")"
  mkdir -p "$d" 2>/dev/null || true
  tmp_dirs="$tmp_dirs $d"
  printf '%s' "$d"
}
cleanup() { for d in $tmp_dirs; do rm -rf "$d" 2>/dev/null || true; done; }
trap cleanup EXIT

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

is_json() { printf '%s' "$1" | $PY -c 'import json,sys; json.load(sys.stdin)' >/dev/null 2>&1; }

DISTINCTIVE='run the plan-handoff skill before the first edit'
# The frontend/visual pointer clause: points plan-touches at the visual skill by name, at
# implementation START -- not the reviewer agent, not done-time.
VISUAL_CLAUSE='load visual-probe now'

SID="abc123def456"

# --- 1: valid payload + session_id -> pointer + clear-instruction + confirmed exec chip written ---
home="$(newtmp)"
payload="{\"session_id\":\"$SID\",\"tool_name\":\"ExitPlanMode\"}"
out="$(printf '%s' "$payload" | BALLAST_CLAUDE_HOME="$home" bash "$HOOK")"; rc=$?
statefile="$home/ballast/modes/$SID"
if [ "$rc" = 0 ] && is_json "$out" && contains "$out" '"additionalContext"' \
   && contains "$out" "ballast-mode clear exec --session $SID" \
   && [ -f "$statefile" ] && contains "$(cat "$statefile" 2>/dev/null)" "exec confirmed"; then
  pass "1 valid payload: pointer + clear-instruction + confirmed state file"
else
  fail "1 valid payload" "rc=$rc out=[$out] statefile=[$([ -f "$statefile" ] && cat "$statefile" || echo MISSING)]"
fi

# --- 2: payload without session_id -> pointer present, no ballast-mode string, no state file -----
home="$(newtmp)"
out="$(printf '%s' '{"tool_name":"ExitPlanMode"}' | BALLAST_CLAUDE_HOME="$home" bash "$HOOK")"; rc=$?
if [ "$rc" = 0 ] && is_json "$out" && contains "$out" "$DISTINCTIVE" \
   && ! contains "$out" "ballast-mode" \
   && [ ! -d "$home/ballast/modes" -o -z "$(ls -A "$home/ballast/modes" 2>/dev/null)" ]; then
  pass "2 no session_id: pointer present, no chip string, no state file"
else
  fail "2 no session_id" "rc=$rc out=[$out]"
fi

# --- 3: sid containing a double quote -> pointer present, no chip clause, valid JSON --------------
home="$(newtmp)"
out="$(printf '%s' '{"session_id":"ab\"cd","tool_name":"ExitPlanMode"}' | BALLAST_CLAUDE_HOME="$home" bash "$HOOK")"; rc=$?
if [ "$rc" = 0 ] && is_json "$out" && contains "$out" "$DISTINCTIVE" && ! contains "$out" "ballast-mode"; then
  pass "3 sid with a quote: pointer present, no chip clause, valid JSON"
else
  fail "3 sid with a quote" "rc=$rc out=[$out]"
fi

# --- 4: state dir unwritable -> mode-state write fails silently, hook still emits valid JSON ----
# A plain FILE at the path the state dir needs to occupy blocks mkdir(parents=True) inside
# mode-state.py regardless of OS/permission-bit semantics (portable even on Windows/Git Bash,
# where chmod-based unwritability is unreliable).
home="$(newtmp)"
: > "$home/ballast"   # blocks mkdir "$home/ballast/modes"
payload="{\"session_id\":\"$SID\",\"tool_name\":\"ExitPlanMode\"}"
out="$(printf '%s' "$payload" | BALLAST_CLAUDE_HOME="$home" bash "$HOOK")"; rc=$?
if [ "$rc" = 0 ] && is_json "$out" && contains "$out" "ballast-mode clear exec --session $SID"; then
  pass "4 state dir unwritable: still emits valid JSON, exit 0"
else
  fail "4 state dir unwritable" "rc=$rc out=[$out]"
fi

# --- 5: pointer sentence and frontend/visual clause present ---------------------------------------
if contains "$out" "$DISTINCTIVE" && contains "$out" "$VISUAL_CLAUSE"; then
  pass "5 pointer + visual clause present"
else
  fail "5 pointer + visual clause" "out=[$out]"
fi

echo
[ "$fails" = 0 ] && echo "ALL plan-handoff.sh TESTS PASS" || echo "$fails FAILURES"

# =================================================================================================
# hooks/mode-state-cleanup.sh -- SessionEnd hook. Deletes the ending session's own mode-state file.
# Co-located here (small, sibling hook, same statusline-mode-indicator build) rather than a
# separate one-case file.
# =================================================================================================
CLEANUP_HOOK="$DIR/../mode-state-cleanup.sh"

# --- 6: payload with session_id -> state file deleted, no stdout, exit 0 ------------------------
home="$(newtmp)"
mkdir -p "$home/ballast/modes"
printf 'exec confirmed 1700000000\n' > "$home/ballast/modes/$SID"
out="$(printf '%s' "{\"session_id\":\"$SID\"}" | BALLAST_CLAUDE_HOME="$home" bash "$CLEANUP_HOOK")"; rc=$?
if [ "$rc" = 0 ] && [ -z "$out" ] && [ ! -f "$home/ballast/modes/$SID" ]; then
  pass "6 cleanup: payload session_id -> state file deleted, no stdout, exit 0"
else
  fail "6 cleanup: payload session_id" "rc=$rc out=[$out] file_exists=$([ -f "$home/ballast/modes/$SID" ] && echo yes || echo no)"
fi

# --- 7: payload without sid + CLAUDE_CODE_SESSION_ID env set -> env fallback used ---------------
# ENV NAME (corrected 2026-07-25): this exported CLAUDE_SESSION_ID, which the harness never sets in
# a hook process -- so it green-lit a fallback that could not fire in production, leaving SessionEnd
# cleanup silently no-op whenever the payload path was unavailable (stranding the statusline mode
# chip until TTL). Check 7b pins the precedence so a revert cannot pass silently.
home="$(newtmp)"
mkdir -p "$home/ballast/modes"
printf 'exec confirmed 1700000000\n' > "$home/ballast/modes/$SID"
out="$(printf '%s' '{}' | BALLAST_CLAUDE_HOME="$home" CLAUDE_CODE_SESSION_ID="$SID" bash "$CLEANUP_HOOK")"; rc=$?
if [ "$rc" = 0 ] && [ -z "$out" ] && [ ! -f "$home/ballast/modes/$SID" ]; then
  pass "7 cleanup: env CLAUDE_CODE_SESSION_ID fallback used"
else
  fail "7 cleanup: env fallback" "rc=$rc out=[$out] file_exists=$([ -f "$home/ballast/modes/$SID" ] && echo yes || echo no)"
fi

# --- 7b: PRECEDENCE -- CLAUDE_CODE_SESSION_ID wins over the legacy CLAUDE_SESSION_ID -------------
# Both names exported, pointing at DIFFERENT session files: only the CLAUDE_CODE_SESSION_ID one may
# be deleted. A revert to reading the legacy name flips this to FAIL instead of passing quietly.
home="$(newtmp)"
mkdir -p "$home/ballast/modes"
LEGACY_SID="legacy-0000-1111-2222-333344445555"
printf 'exec confirmed 1700000000\n' > "$home/ballast/modes/$SID"
printf 'exec confirmed 1700000000\n' > "$home/ballast/modes/$LEGACY_SID"
out="$(printf '%s' '{}' | BALLAST_CLAUDE_HOME="$home" CLAUDE_CODE_SESSION_ID="$SID" CLAUDE_SESSION_ID="$LEGACY_SID" bash "$CLEANUP_HOOK")"; rc=$?
if [ "$rc" = 0 ] && [ ! -f "$home/ballast/modes/$SID" ] && [ -f "$home/ballast/modes/$LEGACY_SID" ]; then
  pass "7b cleanup: CLAUDE_CODE_SESSION_ID takes precedence over legacy name"
else
  fail "7b cleanup: precedence" "rc=$rc code_file=$([ -f "$home/ballast/modes/$SID" ] && echo present || echo deleted) legacy_file=$([ -f "$home/ballast/modes/$LEGACY_SID" ] && echo present || echo deleted)"
fi

# --- 8: neither payload sid nor env sid -> silent no-op, exit 0 ---------------------------------
home="$(newtmp)"
mkdir -p "$home/ballast/modes"
printf 'exec confirmed 1700000000\n' > "$home/ballast/modes/$SID"
out="$(printf '%s' '{}' | BALLAST_CLAUDE_HOME="$home" env -u CLAUDE_SESSION_ID -u CLAUDE_CODE_SESSION_ID bash "$CLEANUP_HOOK")"; rc=$?
if [ "$rc" = 0 ] && [ -z "$out" ] && [ -f "$home/ballast/modes/$SID" ]; then
  pass "8 cleanup: no sid anywhere -> silent no-op, exit 0, file untouched"
else
  fail "8 cleanup: no sid anywhere" "rc=$rc out=[$out] file_exists=$([ -f "$home/ballast/modes/$SID" ] && echo yes || echo no)"
fi

echo
[ "$fails" = 0 ] && echo "ALL plan-handoff.sh + mode-state-cleanup.sh TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
