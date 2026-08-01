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
# Payloads are DATA on stdin (never executed). Self-locating: finds run.sh one directory up (hooks/).
# Exit code = number of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$DIR/../run.sh"

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

# --- 7: proj=/sid= fields populated from CLAUDE_PROJECT_DIR / CLAUDE_CODE_SESSION_ID ------------
# A Windows-native backslash path proves the proj basename expansion
# ("${proj##*[/\\]}") strips both / and \, not just POSIX /.
# ENV NAME (corrected 2026-07-25): this check used to export CLAUDE_SESSION_ID -- a name the
# harness NEVER sets in a hook process. It therefore validated the plumbing against a name
# production never receives: self-confirming, green for 15 days while the real ledger's sid= field
# was empty in 5,439/5,439 lines. A test that supplies its own wrong premise cannot fail. The
# discrimination is pinned by check 8b below, which is the part that makes this one meaningful.
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env BALLAST_CLAUDE_HOME="$home" CLAUDE_PROJECT_DIR='C:\fake\proj\ballast-test' CLAUDE_CODE_SESSION_ID='abcd1234-5678-90ab-cdef-1234567890ab' bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "proj=ballast-test sid=abcd1234-5678-90ab-cdef-1234567890ab"; then
  pass "7 proj=/sid= populated from CLAUDE_CODE_SESSION_ID (Windows-native backslash basename)"
else
  fail "7 proj=/sid= populated" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 8: proj=/sid= fields present-but-empty when unset, no crash under set -u -------------------
# BOTH session-id names unset, so the nested `:-` default chain bottoms out empty rather than
# aborting under `set -u`.
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env -u CLAUDE_PROJECT_DIR -u CLAUDE_SESSION_ID -u CLAUDE_CODE_SESSION_ID BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "proj= sid="; then
  pass "8 proj=/sid= empty when unset, no crash"
else
  fail "8 proj=/sid= empty when unset" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 8b: PRECEDENCE -- CLAUDE_CODE_SESSION_ID wins over the legacy CLAUDE_SESSION_ID ------------
# This is the check that makes the rename non-silent, and it exists because of the 15-day
# empty-sid bug: check 7 alone would go green again if the code reverted to reading ONLY the legacy
# name (the test would simply export whatever the code reads). Here BOTH names are exported with
# DISTINCT values, so the ledger line proves WHICH one the code consulted -- a revert to the legacy
# name flips this to FAIL immediately.
# Note the deliberate asymmetry with the original spec, which asked that a legacy-only environment
# log an EMPTY sid: that is unreachable by construction once run.sh keeps the legacy name as a
# nested `:-` fallback (adjudicated 2026-07-25). Precedence is the assertion that actually pins the
# regression class the empty-check was aiming at.
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env BALLAST_CLAUDE_HOME="$home" CLAUDE_CODE_SESSION_ID='real-11111111-1111-1111-1111-111111111111' CLAUDE_SESSION_ID='legacy-22222222-2222-2222-2222-222222222222' bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "sid=real-11111111-1111-1111-1111-111111111111" \
   && ! contains "$(ledger_text "$log")" "legacy-22222222"; then
  pass "8b sid= precedence: CLAUDE_CODE_SESSION_ID wins over legacy CLAUDE_SESSION_ID"
else
  fail "8b sid= precedence" "rc=$rc log=[$(ledger_text "$log")]"
fi

# --- 8c: legacy-only environment still resolves via the documented fallback ---------------------
# Complements 8b: the nested `:-` default is deliberate (a future harness setting only the old name
# should still ledger a usable sid), so pin that it works rather than leaving it untested.
home="$(newtmp)"
out="$(printf '%s' '{"prompt":"autopilot on"}' | env -u CLAUDE_CODE_SESSION_ID BALLAST_CLAUDE_HOME="$home" CLAUDE_SESSION_ID='legacy-33333333-3333-3333-3333-333333333333' bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$(ledger_text "$log")" "sid=legacy-33333333-3333-3333-3333-333333333333"; then
  pass "8c sid= legacy-name fallback still resolves"
else
  fail "8c sid= legacy fallback" "rc=$rc log=[$(ledger_text "$log")]"
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

# =================================================================================================
# INTERPRETER CACHE (12-16) -- run.sh's three-tier BALLAST_PYTHON resolution.
#
# WHY: the cold probe EXECUTES up to three candidates plus per-candidate `type -aP`/`grep`/`head`
# forks, measured at ~1.5s of a 2.4s hook fire under load and re-derived identically four times per
# Bash tool call. Tier 1 (inherited env) and tier 2 (on-disk cache at <home>/ballast-python) exist
# to skip it; these cases pin that the fast paths are taken, that an INVALID cached value can never
# be served, and that every cache failure mode degrades to a working dispatch rather than breaking
# the session.
#
# HERMETIC, TWICE OVER: BALLAST_CLAUDE_HOME points the cache at a temp dir (never the real
# ~/.claude), and each case runs with `env -u BALLAST_PYTHON` because THIS SUITE exports
# BALLAST_PYTHON at the top -- without the unset, tier 1 would short-circuit every run and these
# cases would silently test nothing at all. (Case 15 is the deliberate exception: it tests tier 1.)
CACHE_PAYLOAD='{"prompt":"autopilot on"}'
cache_file_of() { printf '%s' "$1/ballast-python"; }

# --- 12: COLD run writes the cache file with a usable interpreter -------------------------------
home="$(newtmp)"
cache="$(cache_file_of "$home")"
out="$(printf '%s' "$CACHE_PAYLOAD" | env -u BALLAST_PYTHON BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
cached_val=""
[ -f "$cache" ] && { read -r cached_val < "$cache"; } 2>/dev/null
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' \
   && [ -n "$cached_val" ] && [ -x "${cached_val%% *}" ]; then
  pass "12 cold run writes cache file with an executable interpreter"
else
  fail "12 cold run writes cache" "rc=$rc cached=[$cached_val] out_len=${#out}"
fi

# --- 13: WARM run with a valid cached value still dispatches correctly --------------------------
# Pre-seed the cache with the suite's own verified interpreter so tier 2 is what serves this run.
home="$(newtmp)"
cache="$(cache_file_of "$home")"
printf '%s\n' "$BALLAST_PYTHON" > "$cache"
out="$(printf '%s' "$CACHE_PAYLOAD" | env -u BALLAST_PYTHON BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
log="$home/ballast-hook-fires.log"
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' \
   && contains "$(ledger_text "$log")" "freehand-mode rc=0 out=yes"; then
  pass "13 warm run with valid cached value dispatches correctly"
else
  fail "13 warm run dispatches" "rc=$rc out=[$out] log=[$(ledger_text "$log")]"
fi

# --- 14: STALE cache (nonexistent path) is rejected, run succeeds, value is replaced ------------
# The self-heal path: `[ -x ]` fails on a moved/uninstalled interpreter, tier 3 re-probes, and the
# probe's write-back overwrites the bad line. Without this, a stale cache would wedge every hook.
home="$(newtmp)"
cache="$(cache_file_of "$home")"
printf '%s\n' "/definitely/not/a/real/python-$$" > "$cache"
out="$(printf '%s' "$CACHE_PAYLOAD" | env -u BALLAST_PYTHON BALLAST_CLAUDE_HOME="$home" bash "$RUN" freehand-mode)"; rc=$?
cached_val=""
[ -f "$cache" ] && { read -r cached_val < "$cache"; } 2>/dev/null
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' \
   && [ -n "$cached_val" ] && [ -x "${cached_val%% *}" ] \
   && ! contains "$cached_val" "/definitely/not/a/real/python"; then
  pass "14 stale cache rejected, dispatch succeeds, stale value replaced"
else
  fail "14 stale cache rejected" "rc=$rc cached=[$cached_val] out_len=${#out}"
fi

# --- 15: a pre-exported VALID BALLAST_PYTHON is honored (tier 1) --------------------------------
# Discriminating assertion: tier 1 must short-circuit BEFORE the cache, so no cache file is written
# at all. If the tiers were ever reordered (or tier 1 dropped), this run would cold-probe and leave
# a cache file behind -- flipping this check to FAIL.
home="$(newtmp)"
cache="$(cache_file_of "$home")"
out="$(printf '%s' "$CACHE_PAYLOAD" | env BALLAST_CLAUDE_HOME="$home" BALLAST_PYTHON="$BALLAST_PYTHON" bash "$RUN" freehand-mode)"; rc=$?
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"' && [ ! -f "$cache" ]; then
  pass "15 pre-exported BALLAST_PYTHON honored (tier 1, no cache write)"
else
  fail "15 tier-1 inherited env honored" "rc=$rc cache_exists=$([ -f "$cache" ] && echo yes || echo no) out_len=${#out}"
fi

# --- 16: an UNWRITABLE ledger home never breaks dispatch (fail-open) ----------------------------
# BALLAST_CLAUDE_HOME points at a regular FILE, so both `mkdir -p` and the cache write must fail.
# The hook still has to resolve an interpreter and dispatch normally -- a broken cache location is
# a performance regression at worst, never a dead hook.
home_parent="$(newtmp)"
notadir="$home_parent/i-am-a-file"
printf 'not a directory\n' > "$notadir"
out="$(printf '%s' "$CACHE_PAYLOAD" | env -u BALLAST_PYTHON BALLAST_CLAUDE_HOME="$notadir" bash "$RUN" freehand-mode)"; rc=$?
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"'; then
  pass "16 unwritable ledger home: dispatch still succeeds (fail-open)"
else
  fail "16 unwritable ledger home" "rc=$rc out=[$out]"
fi

# --- 16b: NO home at all (HOME and BALLAST_CLAUDE_HOME both unset) still dispatches --------------
# The other half of fail-open: with no state dir resolvable the cache is skipped entirely and the
# cold probe must still run. Pairs with check 5, which pins the same condition for the ledger.
out="$(printf '%s' "$CACHE_PAYLOAD" | env -u BALLAST_PYTHON -u HOME -u BALLAST_CLAUDE_HOME bash "$RUN" freehand-mode)"; rc=$?
if [ "$rc" = 0 ] && contains "$out" '"additionalContext"'; then
  pass "16b no resolvable home: cache skipped, dispatch still succeeds"
else
  fail "16b no resolvable home" "rc=$rc out=[$out]"
fi

echo
[ "$fails" = 0 ] && echo "ALL run.sh TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
