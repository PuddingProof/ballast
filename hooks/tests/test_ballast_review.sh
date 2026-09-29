#!/usr/bin/env bash
# Regression tests for bin/ballast-review -- the headless sidecar trampoline that restores
# autonomous review-before-commit from when native /code-review was user-invoke-only (Claude Code
# 2.1.215; since re-opened behind a feature flag). Every case here goes through the BALLAST_REVIEW_ECHO=1 test seam, which prints the
# exact command line the shim would exec instead of actually spawning a headless `claude -p`
# session -- so this suite is fast, free, and needs no live account.
#
# WHY committed: the shim is the ENTIRE trust boundary for what gets forwarded to a headless
# session running under the user's own credentials. A regression here (e.g. --fix silently
# forwarded, or usage validation skipped) reopens the exact autonomy gap this batch closes. Pins:
# (1) exact command construction (`ballast-review <args>` -> `claude -p /code-review <args>`
# verbatim), (2) the no-args usage error, (3) the --fix read-only-invariant rejection anywhere in
# the args, (4) the --model/--effort leading-flag passthrough to the claude CLI (never the prompt).
#
# Self-locating: finds bin/ballast-review relative to this file (hooks/tests/ -> ../../bin/). Exit code =
# number of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM="$DIR/../../bin/ballast-review"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }

if [ ! -f "$SHIM" ]; then
  fail "0 shim exists" "bin/ballast-review not found at $SHIM"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

# --- 1: exact prompt construction, single word of context -----------------------------------
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" high abc123..HEAD context)"; rc=$?
if [ "$rc" = 0 ] && [ "$out" = "claude -p /code-review high abc123..HEAD context" ]; then
  pass "1 prompt construction: level + range + one context word"
else
  fail "1 prompt construction" "rc=$rc out=[$out]"
fi

# --- 2: exact prompt construction, multi-word free-form context brief ------------------------
# This is the exact invocation the plan's Verification section pins -- multiple context words
# after the range must all ride through untouched, in order, space-joined.
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" high abc123..HEAD two-phase shutdown fix)"; rc=$?
if [ "$rc" = 0 ] && [ "$out" = "claude -p /code-review high abc123..HEAD two-phase shutdown fix" ]; then
  pass "2 prompt construction: level + range + multi-word context brief"
else
  fail "2 prompt construction (multi-word)" "rc=$rc out=[$out]"
fi

# --- 3: bare level only (no target, no context) -----------------------------------------------
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" low)"; rc=$?
if [ "$rc" = 0 ] && [ "$out" = "claude -p /code-review low" ]; then
  pass "3 prompt construction: bare level only"
else
  fail "3 prompt construction (bare level)" "rc=$rc out=[$out]"
fi

# --- 4: no-args usage error -- nothing to review, must fail fast with a usage message on stderr
out_err="$(bash "$SHIM" 2>&1 1>/dev/null)"; out_stdout="$(bash "$SHIM" 2>/dev/null)"; rc=$?
if [ "$rc" = 1 ] && [ -z "$out_stdout" ]; then
  case "$out_err" in
    *usage*) pass "4 no-args: usage error on stderr, exit 1, stdout clean" ;;
    *) fail "4 no-args usage" "stderr missing 'usage': [$out_err]" ;;
  esac
else
  fail "4 no-args usage" "rc=$rc stdout=[$out_stdout] stderr=[$out_err]"
fi

# --- 5: --fix rejected as the sole arg -- the read-only invariant ------------------------------
out="$(bash "$SHIM" --fix 2>&1)"; rc=$?
if [ "$rc" = 1 ] && case "$out" in *"read-only"*) true ;; *) false ;; esac; then
  pass "5 --fix rejected (sole arg)"
else
  fail "5 --fix rejected (sole arg)" "rc=$rc out=[$out]"
fi

# --- 6: --fix rejected anywhere in the args, not just as $1 ------------------------------------
# Native /code-review accepts --fix positioned after the level/target, so the shim must scan the
# whole arg list rather than only checking $1.
out="$(bash "$SHIM" low --fix 2>&1)"; rc=$?
if [ "$rc" = 1 ] && case "$out" in *"read-only"*) true ;; *) false ;; esac; then
  pass "6 --fix rejected (trailing position)"
else
  fail "6 --fix rejected (trailing position)" "rc=$rc out=[$out]"
fi

# --- 7: --fix rejection fires BEFORE the echo seam would otherwise print a prompt --------------
# Proves the read-only guard isn't accidentally bypassable via the test seam.
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" high --fix 2>&1)"; rc=$?
if [ "$rc" = 1 ] && case "$out" in *"/code-review"*) false ;; *) true ;; esac; then
  pass "7 --fix rejected even under BALLAST_REVIEW_ECHO=1"
else
  fail "7 --fix rejected under echo seam" "rc=$rc out=[$out]"
fi

# --- 8: --model/--effort leading flags pass through to the claude CLI, not the prompt ----------
# The flags must land BEFORE -p in the exec'd argv, and the /code-review prompt must not contain
# them -- they steer the sidecar session's model/effort (cost-scaling), never the review itself.
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" --model opus --effort medium low HEAD~3..HEAD brief)"; rc=$?
if [ "$rc" = 0 ] && [ "$out" = "claude --model opus --effort medium -p /code-review low HEAD~3..HEAD brief" ]; then
  pass "8 --model/--effort passthrough ahead of -p"
else
  fail "8 flag passthrough" "rc=$rc out=[$out]"
fi

# --- 9: a recognized flag with no value is a usage error, not a silent drop --------------------
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" --model 2>&1)"; rc=$?
if [ "$rc" = 1 ] && case "$out" in *"requires a value"*) true ;; *) false ;; esac; then
  pass "9 dangling --model rejected"
else
  fail "9 dangling --model" "rc=$rc out=[$out]"
fi

# --- 10: --fix still rejected after leading flags (the read-only scan runs on the REMAINING args)
out="$(BALLAST_REVIEW_ECHO=1 bash "$SHIM" --model opus low --fix 2>&1)"; rc=$?
if [ "$rc" = 1 ] && case "$out" in *"read-only"*) true ;; *) false ;; esac; then
  pass "10 --fix rejected after leading flags"
else
  fail "10 --fix after flags" "rc=$rc out=[$out]"
fi

# --- 11: static pin -- MSYS handling must stay content-scoped, never tree-global ----------------
# The shim once exported MSYS_NO_PATHCONV=1, which inherits into the whole sidecar process tree and
# bricked it (run.sh hands python an MSYS-form /c/... path and NEEDS argv conversion; with it
# disabled every hook errored and PreToolUse errors fail closed, blocking all sidecar shell tools).
# The fix is MSYS2_ARG_CONV_EXCL="/code-review" -- scoped by content, harmless to inherit. Pin both
# directions so the regression can't sneak back in a refactor.
# Pin the export STATEMENTS, not mere mentions -- the shim's comments legitimately name
# MSYS_NO_PATHCONV while explaining why it must not be used.
if grep -qE '^[[:space:]]*export[[:space:]]+MSYS_NO_PATHCONV' "$SHIM"; then
  fail "11 no MSYS_NO_PATHCONV export" "shim reintroduces the tree-global pathconv kill (sidecar-bricking regression)"
elif grep -qE '^[[:space:]]*export[[:space:]]+MSYS2_ARG_CONV_EXCL' "$SHIM"; then
  pass "11 MSYS handling is content-scoped (ARG_CONV_EXCL, not NO_PATHCONV)"
else
  fail "11 MSYS guard present" "shim has no MSYS arg-conversion guard at all -- /code-review will be mangled under Git Bash"
fi

echo
[ "$fails" = 0 ] && echo "ALL ballast-review TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
