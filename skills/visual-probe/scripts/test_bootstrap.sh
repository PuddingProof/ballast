#!/usr/bin/env bash
# Regression tests for visual-probe's probe.mjs bootstrap + preflight gate.
#
# WHY committed: probe.mjs used to have a top-level static `import … from 'playwright'`, so ANY
# invocation with node_modules absent (the normal state right after a plugin update — node_modules
# is never vendored) died with a raw ERR_MODULE_NOT_FOUND, with no non-interactive way to check
# readiness first. This pins the fix: (1) preflight succeeds when node_modules/playwright is
# present in the real skill dir, (2) a node_modules-absent invocation fails FAST with a friendly,
# actionable message — never the raw ESM error, stdout kept clean — and this holds even when lib/
# isn't present at all (proving the bootstrap never touches lib/*.mjs before the readiness check
# passes), (2b) a wrong-version install flips preflight to version-mismatch, and (3) selftest
# still passes end to end.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Exit code = number of
# failures. package.json / node_modules stay one level up, at the skill root (npm resolution
# anchor) — SKILL_ROOT derives that, mirroring probe.mjs's own SKILL_DIR computation.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$DIR/.." && pwd)"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }
skip() { printf 'SKIP  %s -- %s\n' "$1" "$2"; }
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

# Probe node availability rather than assuming — suite is meaningless without it: hard failure.
if ! command -v node >/dev/null 2>&1; then
  fail "0 node availability" "'node' not found on PATH -- cannot run any of these tests"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

tmp_dirs=""
newtmp() {
  local d
  d="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-boot-test.$$.$RANDOM")"
  mkdir -p "$d" 2>/dev/null || true
  tmp_dirs="$tmp_dirs $d"
  printf '%s' "$d"
}
cleanup() { for d in $tmp_dirs; do rm -rf "$d" 2>/dev/null || true; done; }
trap cleanup EXIT

REAL_HAS_PLAYWRIGHT=0
[ -f "$SKILL_ROOT/node_modules/playwright/package.json" ] && REAL_HAS_PLAYWRIGHT=1

# --- 1: preflight exits 0 in the real skill dir, when node_modules/playwright is present --------
if [ "$REAL_HAS_PLAYWRIGHT" = 1 ]; then
  out="$(cd "$DIR" && node probe.mjs preflight)"; rc=$?
  if [ "$rc" = 0 ] && contains "$out" '"ready":true'; then
    pass "1 preflight ready in real skill dir (node_modules present)"
  else
    fail "1 preflight ready in real skill dir" "rc=$rc out=[$out]"
  fi
else
  skip "1 preflight ready in real skill dir" "node_modules/playwright not installed here (expected right after a plugin update; not a regression by itself)"
fi

# --- 2: temp-dir copy of ONLY probe.mjs + package.json, NO node_modules, NO lib/ -----------------
# Deliberately do NOT copy lib/: if the bootstrap ever accidentally imported it before the
# readiness check, this would fail with a DIFFERENT error ("cannot find ./lib/cli.mjs") instead of
# the intended friendly NOT-READY message -- so this also proves zero premature imports.
# Mirrors the real skill's nested layout (package.json at root, probe.mjs one level down in
# scripts/) since probe.mjs's own SKILL_DIR computation walks up two dirname()s from its own path.
tmp="$(newtmp)"
mkdir -p "$tmp/scripts"
cp "$DIR/probe.mjs" "$tmp/scripts/probe.mjs"
cp "$SKILL_ROOT/package.json" "$tmp/package.json"

out1="$(cd "$tmp/scripts" && node probe.mjs preflight)"; rc1=$?
out2="$(cd "$tmp/scripts" && node probe.mjs shot 2>&1)"; rc2=$?
# A failed DELEGATING command must keep stdout clean (its stdout contract belongs to the command
# it never reached, e.g. shot's manifest path) — failure rides the exit code + stderr only.
out2_stdout="$(cd "$tmp/scripts" && node probe.mjs shot 2>/dev/null)"

if [ "$rc1" = 1 ] && contains "$out1" '"ready":false' && contains "$out1" '"reason":"missing"' \
   && [ "$rc2" = 1 ] && contains "$out2" 'NOT READY' \
   && ! contains "$out2" 'ERR_MODULE_NOT_FOUND' \
   && [ -z "$out2_stdout" ]; then
  pass "2 missing node_modules: preflight exit 1 + delegating path fails fast, friendly, stdout-clean"
else
  fail "2 missing node_modules behavior" "rc1=$rc1 out1=[$out1] rc2=$rc2 out2=[$out2] out2_stdout=[$out2_stdout]"
fi

# --- 2b: version-mismatch branch — the state every plugin update re-arms ------------------------
# A fake node_modules/playwright at a wrong version must flip preflight to reason:"version-mismatch"
# (pins the exact-pin string-equality logic; see checkReadiness()).
tmpv="$(newtmp)"
mkdir -p "$tmpv/scripts"
cp "$DIR/probe.mjs" "$tmpv/scripts/probe.mjs"
cp "$SKILL_ROOT/package.json" "$tmpv/package.json"
mkdir -p "$tmpv/node_modules/playwright"
printf '{"name":"playwright","version":"0.0.0"}\n' > "$tmpv/node_modules/playwright/package.json"

outv="$(cd "$tmpv/scripts" && node probe.mjs preflight)"; rcv=$?
if [ "$rcv" = 1 ] && contains "$outv" '"reason":"version-mismatch"' && contains "$outv" '"installed":"0.0.0"'; then
  pass "2b stale node_modules: preflight exit 1 with reason version-mismatch"
else
  fail "2b version-mismatch behavior" "rc=$rcv out=[$outv]"
fi

# --- 3: selftest still passes (only meaningful once node_modules/playwright + real Edge exist) ---
if [ "$REAL_HAS_PLAYWRIGHT" = 1 ]; then
  out="$(cd "$DIR" && node probe.mjs selftest 2>&1)"; rc=$?
  if [ "$rc" = 0 ]; then
    pass "3 selftest still passes"
  else
    fail "3 selftest still passes" "rc=$rc out=[$out]"
  fi
else
  skip "3 selftest still passes" "node_modules/playwright not installed here"
fi

echo
[ "$fails" = 0 ] && echo "ALL visual-probe bootstrap TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
