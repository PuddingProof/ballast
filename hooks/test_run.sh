#!/usr/bin/env bash
# Regression tests for hooks/run.sh -- the single dispatcher every registered hook routes through.
#
# WHY committed: the 5a dispatch-tail rework replaced a plain `exec` with capture-and-forward plus
# a fire ledger, introducing a `ledger()` helper that must (a) resolve BALLAST_CLAUDE_HOME before
# falling back to $HOME/.claude, (b) skip silently if neither is set -- the set -u abort fix: under
# `set -u` a bare `$HOME` reference would ABORT the whole script and eat the dispatched child's
# exit code, silently turning an exit-2 hard block into rc=1-or-worse -- and (c) never change the
# forwarded stdout or exit code. These cases pin all three, plus the ledger's "exactly one line per
# fire, zero lines for a non-fire" contract.
#
# Payloads are DATA on stdin (never executed). Self-locating: finds run.sh next to this file.
# Exit code = number of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$DIR/run.sh"

# Resolve a working Python 3 the same way run.sh's own probe does (run.sh re-resolves internally
# regardless, but exporting here mirrors this dir's other suites' convention and keeps the probe
# consistent if a hook is ever invoked directly rather than through run.sh).
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

# Every temp home this run creates, space-separated -- cleaned up unconditionally on exit so a
# failed assertion (or an interrupted run) never leaves stray dirs behind.
tmp_dirs=""
newtmp() {
  local d
  d="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/run-sh-test.$$.$RANDOM")"
  mkdir -p "$d" 2>/dev/null || true
  tmp_dirs="$tmp_dirs $d"
  printf '%s' "$d"
}
cleanup() { for d in $tmp_dirs; do rm -rf "$d" 2>/dev/null || true; done; }
trap cleanup EXIT

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }
ledger_text() { [ -f "$1" ] && cat "$1" || printf ''; }
ledger_line_count() { [ -f "$1" ] && grep -c . "$1" || printf '0'; }

# askuserquestion-recommend payload whose first option label lacks "(Recommended)" -- the same
# hard-block shape that hook's own suite exercises, reused here to pin exit-2 forwarding through
# the dispatcher rather than re-testing the hook's own decision logic.
BAD_ASK_PAYLOAD='{"tool_input":{"questions":[{"header":"Pick one","options":[{"label":"Foo"},{"label":"Bar"}]}]}}'

# --- 1: firing hook -- stdout forwarded intact, exactly one matching ledger line ---------------
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' \
   && [ "$(ledger_line_count "$log")" = 1 ] && contains "$(ledger_text "$log")" "freehand-mode rc=0 out=yes"; then
  pass "1 firing hook: stdout forwarded + one ledger line"
else
  fail "1 firing hook" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

# --- 2: non-firing -- empty stdout, ledger untouched (no file, or no new line) ------------------
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"hello"}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && [ -z "$out" ] && [ ! -s "$log" ]; then
  pass "2 non-firing: empty stdout, no ledger line"
else
  fail "2 non-firing" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

# --- 3: exit-2 forwarding -- rc propagates, ledger records rc=2 out=no -------------------------
home="$(newtmp)"
out="$(printf '%s' "$BAD_ASK_PAYLOAD" | BALLAST_CLAUDE_HOME="$home" bash "$RUN" askuserquestion-recommend 2>/dev/null)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 2 ] && contains "$(ledger_text "$log")" "askuserquestion-recommend rc=2 out=no"; then
  pass "3 exit-2 forwarding: rc + ledger line"
else
  fail "3 exit-2 forwarding" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 4: unknown hook name -- silent fail-open, no output, no ledger (exits before dispatch) -----
home="$(newtmp)"
out="$(printf '%s' '{}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" not-a-real-hook)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && [ -z "$out" ] && [ ! -s "$log" ]; then
  pass "4 unknown hook name: silent no-op, no ledger"
else
  fail "4 unknown hook name" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

# --- 5: rc survives unset HOME -- pins the set -u abort fix -------------------------------------
# Neither HOME nor BALLAST_CLAUDE_HOME set: ledger() must resolve to "nowhere" and return early
# (no mkdir, no write) rather than dereferencing a bare $HOME under `set -u`, which would abort
# run.sh mid-script and turn this exit-2 block into a broken pipe / non-2 exit instead.
out="$(printf '%s' "$BAD_ASK_PAYLOAD" | env -u HOME -u BALLAST_CLAUDE_HOME bash "$RUN" askuserquestion-recommend 2>/dev/null)"; rc=$?
if [ "$rc" = 2 ]; then
  pass "5 rc survives unset HOME (set -u abort fix)"
else
  fail "5 rc survives unset HOME" "rc=$rc (want 2) out=[$out]"
fi

# --- 6: BALLAST_CLAUDE_HOME takes precedence over HOME ------------------------------------------
home_var="$(newtmp)"
home_env="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env BALLAST_CLAUDE_HOME="$home_var" HOME="$home_env" bash "$RUN" freehand-mode)"; rc=$?
log_var="$home_var/ballast-hook-fires.log"
log_env="$home_env/ballast-hook-fires.log"
if [ "$rc" = 0 ] && [ -s "$log_var" ] && [ ! -s "$log_env" ]; then
  pass "6 BALLAST_CLAUDE_HOME respected over HOME"
else
  fail "6 BALLAST_CLAUDE_HOME respected" "rc=$rc var_log=[$(ledger_text "$log_var")] home_log=[$(ledger_text "$log_env")]"
fi

# --- 7: proj=/sid= fields populated from CLAUDE_PROJECT_DIR / CLAUDE_SESSION_ID -----------------
# A Windows-native backslash path proves the proj basename expansion
# ("${proj##*[/\\]}") strips both / and \, not just POSIX /.
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env BALLAST_CLAUDE_HOME="$home" CLAUDE_PROJECT_DIR='C:\fake\proj\ballast-test' CLAUDE_SESSION_ID='abcd1234-5678-90ab-cdef-1234567890ab' bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "proj=ballast-test sid=abcd1234-5678-90ab-cdef-1234567890ab"; then
  pass "7 proj=/sid= populated (Windows-native backslash basename)"
else
  fail "7 proj=/sid= populated" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 8: proj=/sid= fields present-but-empty when unset, no crash under set -u -------------------
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env -u CLAUDE_PROJECT_DIR -u CLAUDE_SESSION_ID BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "proj= sid="; then
  pass "8 proj=/sid= empty when unset, no crash"
else
  fail "8 proj=/sid= empty when unset" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 9: one-generation size rollover -- oversized log moved to .old, fresh line only ------------
home="$(newtmp)"
log="$home/ballast-hook-fires.log"
old="$log.old"
# Pre-create a >5MB log (filler text -- content is irrelevant, only size matters for the
# rollover threshold) so the very next fire triggers the rollover before it appends.
if ! (yes "filler-line-for-rollover-size" 2>/dev/null | head -c 6000000 > "$log" 2>/dev/null) || [ ! -s "$log" ]; then
  # yes/head unavailable or produced nothing -- dd fallback, translated to ASCII text.
  dd if=/dev/zero bs=1M count=6 2>/dev/null | tr '\0' 'y' > "$log"
fi
out="$(printf '%s' '{"prompt":"autopilot on"}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
if [ "$rc" = 0 ] && [ -f "$old" ] && [ "$(wc -c < "$old")" -gt 5000000 ] \
   && [ "$(ledger_line_count "$log")" = 1 ] \
   && contains "$(ledger_text "$log")" "freehand-mode rc=0 out=yes" \
   && ! contains "$(ledger_text "$log")" "filler-line-for-rollover-size"; then
  pass "9 rollover: oversized log moved to .old, fresh log has only the new line"
else
  fail "9 rollover" "rc=$rc old_exists=$([ -f "$old" ] && echo yes || echo no) old_size=$([ -f "$old" ] && wc -c < "$old" || echo n/a) log=[$(ledger_text "$log")]"
fi

# --- 10: plan-handoff dispatch -- case-map entry + needs_python resolve BALLAST_PYTHON, stdout ---
# forwarded, one ledger line (statusline mode-indicator build, 2026-07-11: plan-handoff.sh now
# shells out to python for session_id extraction + the exec-chip write, so it joined needs_python).
home="$(newtmp)"
out="$(printf '%s' '{"session_id":"abc123def456","tool_name":"ExitPlanMode"}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" plan-handoff)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' \
   && [ "$(ledger_line_count "$log")" = 1 ] && contains "$(ledger_text "$log")" "plan-handoff rc=0 out=yes"; then
  pass "10 plan-handoff dispatch: stdout forwarded + one ledger line"
else
  fail "10 plan-handoff dispatch" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

# --- 11: mode-state-cleanup dispatch -- case-map entry resolves, silent by design (never emits ---
# stdout -- see the hook's own header), so rc=0 with NO ledger line (fired=no, rc=0 -- the ledger
# helper only logs on a fire or a nonzero exit, and this hook is neither by design).
home="$(newtmp)"
out="$(printf '%s' '{"session_id":"abc123def456"}' | BALLAST_CLAUDE_HOME="$home" bash "$RUN" mode-state-cleanup)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && [ -z "$out" ] && [ ! -s "$log" ]; then
  pass "11 mode-state-cleanup dispatch: silent by design, rc=0, no ledger line"
else
  fail "11 mode-state-cleanup dispatch" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

echo
[ "$fails" = 0 ] && echo "ALL run.sh TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
