#!/usr/bin/env bash
# Regression test for states-from-manifest.mjs axis enumeration.
#
# WHY committed: enumerateStates used to hard-code exactly content/theme/overlay, silently dropping
# any other manifest axis (appTheme, face, …) from every generated state. This pins the
# generalization: (a) a legacy {content, theme, overlay} manifest still enumerates identically,
# (b) a custom-axis value is no longer silently dropped, (c) the full cross-product runs over
# EVERY param axis (not just content/theme), and (d) the composed all-worst pass dedupes against
# baseline/one-hot states (≤1 param axis or single-value axes used to double-snapshot a label).
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. No npm deps — it
# imports the scenario's pure `enumerateStates` export by file URL, drives no browser. scenarios/
# stays at the skill root (declared family folder, one level up from scripts/). Exit code = failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Windows (Git Bash) node needs a Windows-style path for pathToFileURL; `pwd -W` yields it, and we
# fall back to the POSIX path on platforms without it (Linux/macOS node resolves those natively).
WINDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || printf '%s' "$DIR")"
SCENARIO="$WINDIR/../scenarios/states-from-manifest.mjs"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected [$3], got [$2]"; fi; }

# Probe node availability rather than assuming — suite is meaningless without it: hard failure.
if ! command -v node >/dev/null 2>&1; then
  fail "0 node availability" "'node' not found on PATH -- cannot run this test"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-enum-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# Inline ESM harness: import enumerateStates by file URL from the real scenario, emit KEY=VALUE
# facts for the bash asserts below to check.
cat > "$tmp/run.mjs" <<'EOF'
import { pathToFileURL } from 'url';
const { enumerateStates } = await import(pathToFileURL(process.env.SCENARIO).href);
const out = [];

// (a) legacy {content, theme, overlay} manifest — the backward-compat floor.
const legacy = {
  content: { values: ['a', 'b'], param: 'c' },
  theme: { values: ['t1', 't2'], param: 't' },
  overlay: { flags: { glow: { param: 'g', value: '1' } } },
};
const a = enumerateStates(legacy, 'ready', false);
// 1 baseline + 1 content one-hot + 1 theme one-hot + 1 overlay one-hot + 1 composed-worst = 5.
out.push(`A_COUNT=${a.length}`);
out.push(`A_BASELINE_EMPTY=${a[0].labelParts.length === 0 ? 1 : 0}`);
const composed = a[a.length - 1].labelParts;
const composedOk = composed.includes('b') && composed.includes('t2') && composed.includes('glow');
out.push(`A_COMPOSED_OK=${composedOk ? 1 : 0}`);

// (b) custom axes {content, appTheme, face} — the silent-drop regression this fix kills.
const custom = {
  content: { values: ['a', 'b'], param: 'c' },
  appTheme: { values: ['light', 'dark'], param: 'at' },
  face: { values: ['round', 'square'], param: 'f' },
};
const b = enumerateStates(custom, 'ready', false);
const seen = new Set();
for (const s of b) {
  for (const lp of s.labelParts) seen.add(lp);
  for (const up of s.urlParts) seen.add(up.value);
}
out.push(`B_DARK=${seen.has('dark') ? 1 : 0}`);
out.push(`B_SQUARE=${seen.has('square') ? 1 : 0}`);

// (c) full cross-product: two 2-value param axes × (no overlay + 1 flag) = 2*2*2 = 8.
const full = {
  content: { values: ['a', 'b'], param: 'c' },
  theme: { values: ['t1', 't2'], param: 't' },
  overlay: { flags: { glow: { param: 'g', value: '1' } } },
};
out.push(`C_COUNT=${enumerateStates(full, 'ready', true).length}`);

// (d) composed-state dedupe — with ≤1 param axis the all-worst point coincides with an
// already-emitted state; an undeduped enumeration snapshots the same label twice.
// Single 2-value axis, no overlay: baseline + one-hot 'b' (composed == one-hot, dropped) = 2.
out.push(`D_SINGLE_AXIS=${enumerateStates({ content: { values: ['a', 'b'], param: 'c' } }, 'ready', false).length}`);
// Overlay-only: baseline + glow one-hot (composed-with-glow == glow one-hot, dropped) = 2.
out.push(`D_OVERLAY_ONLY=${enumerateStates({ overlay: { flags: { glow: { param: 'g', value: '1' } } } }, 'ready', false).length}`);
// Single-VALUE axis: worst == default, so one-hot is empty and composed == baseline = 1.
out.push(`D_SINGLE_VALUE=${enumerateStates({ content: { values: ['only'], param: 'c' } }, 'ready', false).length}`);

console.log(out.join('\n'));
EOF

out="$(cd "$tmp" && SCENARIO="$SCENARIO" node run.mjs 2>&1)"; rc=$?
if [ "$rc" != 0 ]; then
  fail "node harness ran clean" "node exited rc=$rc out=[$out]"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

get() { printf '%s\n' "$out" | grep -E "^$1=" | head -1 | cut -d= -f2; }

check "a) legacy manifest emits exactly 5 states"                    "$(get A_COUNT)"          "5"
check "a) baseline label parts empty"                                "$(get A_BASELINE_EMPTY)" "1"
check "a) composed state carries content b + theme t2 + overlay glow" "$(get A_COMPOSED_OK)"   "1"
check "b) custom axis value 'dark' is enumerated (no silent drop)"    "$(get B_DARK)"          "1"
check "b) custom axis value 'square' is enumerated (no silent drop)"  "$(get B_SQUARE)"        "1"
check "c) full=true 2x2 param axes x (no-overlay + 1 flag) = 8 states" "$(get C_COUNT)"        "8"
check "d) single 2-value axis dedupes composed into one-hot (2 states)" "$(get D_SINGLE_AXIS)"  "2"
check "d) overlay-only manifest dedupes composed (2 states)"            "$(get D_OVERLAY_ONLY)" "2"
check "d) single-value axis collapses to baseline only (1 state)"       "$(get D_SINGLE_VALUE)" "1"

echo
[ "$fails" = 0 ] && echo "ALL states-enum TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
