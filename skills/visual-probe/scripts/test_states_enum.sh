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
# It also pins the `--settle MS` post-ready dwell, the flag that lets a Write-less glance leaf honor
# a project whose entrance animation / theme crossfade completes AFTER readySignal (without it, the
# leaf can only escalate). Two properties, both cheap and browser-free: (h) the scenario hands the
# run's settle through to EVERY state's capture (a hardcoded 0 would look identical in any single
# run), and (i) the helper's shutter path dwells BETWEEN the marker wait and the screenshot — order
# is the whole point, since a dwell before the marker or after the shutter buys nothing.
#
# It also pins leaf mode: a manifest `drive` hook is PROJECT-authored code this scenario would
# dynamically import into the probe's own node process, so (e) --skip-drive-hooks must not import
# it at all (proved by an import-time sentinel file that never appears), (f) every state that
# needed one must surface as a named coverage hole instead of vanishing, and (g) the control run
# without the flag DOES import it — otherwise (e)'s evidence would be a fixture that never runs.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. No npm deps — it
# imports the scenario's pure `enumerateStates` export by file URL, and drives its default export
# against a stub `h` (no browser). scenarios/ stays at the skill root (declared family folder, one
# level up from scripts/). Exit code = failures.
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

# ---------------------------------------------------------------------------------------------
# (e)(f)(g) leaf mode — --skip-drive-hooks / VISUAL_STATES_SKIP_DRIVE_HOOKS=1
# ---------------------------------------------------------------------------------------------
# Two DISTINCT hook modules (not one reused): node caches a module URL after first import, so a
# single fixture would make the control run's sentinel unprovable.
for v in skip ctl; do
  cat > "$tmp/drive-$v.mjs" <<EOF
// Import-time sentinel — the file exists iff this module was IMPORTED (stronger than "called").
import fs from 'fs';
fs.writeFileSync(new URL('./sentinel-$v.txt', import.meta.url), 'imported');
export const openSettings = async () => {};
EOF
  cat > "$tmp/manifest-$v.json" <<EOF
{
  "routes": { "home": "/" },
  "readySignal": "[data-ready]",
  "axes": {
    "content": { "values": ["a", "b"], "param": "c" },
    "overlay": { "flags": { "settings": { "drive": "./drive-$v.mjs#openSettings", "marker": "[data-overlay]" } } }
  }
}
EOF
done

# Drives the scenario's DEFAULT export against a stub `h` — no browser, no network: `goto` is a
# no-op and `snapshotForced` just records the label, so what remains under test is exactly the
# skip/report decision.
cat > "$tmp/run-skip.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
const mod = await import(pathToFileURL(process.env.SCENARIO).href);
const out = [];
const stub = () => {
  const labels = [];
  return { labels, h: { url: 'http://127.0.0.1:1/', async goto() {}, async snapshotForced(l) { labels.push(l); } } };
};
const run = async (manifest) => {
  process.env.VISUAL_STATES = path.resolve(process.cwd(), manifest);
  const s = stub();
  let err = '';
  try { await mod.default({}, s.h); } catch (e) { err = e.message; }
  return { err, labels: s.labels };
};

// (e) leaf mode: the hook is never imported and its states are skipped, not captured unforced.
process.env.VISUAL_STATES_SKIP_DRIVE_HOOKS = '1';
const skip = await run('manifest-skip.json');
out.push(`E_ERR=${skip.err}`);
out.push(`E_SENTINEL=${fs.existsSync('sentinel-skip.txt') ? 1 : 0}`);
out.push(`E_CAPTURED=${skip.labels.join(',')}`);

// (f) the skipped states are reported as named holes on the exported list the harness copies into
// manifest.json — never silently absorbed.
const holes = mod.coverageHoles;
out.push(`F_HOLES=${holes.length}`);
out.push(`F_KIND=${holes.length > 0 && holes.every((x) => x.kind === 'drive-hook-skipped') ? 1 : 0}`);
out.push(`F_LABELS=${holes.map((x) => x.label).sort().join(',')}`);
out.push(`F_REASON=${holes.every((x) => /drive-skip\.mjs#openSettings/.test(x.reason)) ? 1 : 0}`);

// (g) control: without the flag the same manifest shape DOES import the hook and captures those
// states — and adds no holes.
delete process.env.VISUAL_STATES_SKIP_DRIVE_HOOKS;
const ctl = await run('manifest-ctl.json');
out.push(`G_ERR=${ctl.err}`);
out.push(`G_SENTINEL=${fs.existsSync('sentinel-ctl.txt') ? 1 : 0}`);
out.push(`G_CAPTURED=${ctl.labels.join(',')}`);
out.push(`G_HOLES=${mod.coverageHoles.length}`);

console.log(out.join('\n'));
EOF

out2="$(cd "$tmp" && SCENARIO="$SCENARIO" node run-skip.mjs 2>/dev/null)"; rc2=$?
if [ "$rc2" != 0 ]; then
  fail "skip-drive-hooks harness ran clean" "node exited rc=$rc2 out=[$out2]"
else
  get2() { printf '%s\n' "$out2" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "e) leaf-mode run raises no error"                              "$(get2 E_ERR)"      ""
  check "e) drive hook is NEVER imported (no import-time sentinel)"     "$(get2 E_SENTINEL)" "0"
  check "e) only hook-free states are captured"                         "$(get2 E_CAPTURED)" "home,home__b"
  check "f) both hook-needing states reported as coverage holes"        "$(get2 F_HOLES)"    "2"
  check "f) holes carry the drive-hook-skipped kind"                    "$(get2 F_KIND)"     "1"
  check "f) holes name the states, not just a count"                    "$(get2 F_LABELS)"   "home__b__settings,home__settings"
  check "f) hole reason names the hook that would force the state"      "$(get2 F_REASON)"   "1"
  check "g) control run raises no error"                                "$(get2 G_ERR)"      ""
  check "g) control WITHOUT the flag does import the hook (sentinel)"   "$(get2 G_SENTINEL)" "1"
  check "g) control captures every state, hook-driven included"         "$(get2 G_CAPTURED)" "home,home__b,home__settings,home__b__settings"
  # 0, not 2: the scenario resets coverageHoles per run, so the control's count is its OWN. The
  # earlier leaf-mode run's two holes no longer leak across an in-process second invocation.
  check "g) control adds no coverage holes"                             "$(get2 G_HOLES)"    "0"
fi

# ---------------------------------------------------------------------------------------------
# (h)(i) --settle MS — the post-ready dwell
# ---------------------------------------------------------------------------------------------
cat > "$tmp/manifest-settle.json" <<'EOF'
{
  "routes": { "home": "/" },
  "readySignal": "[data-ready]",
  "axes": { "content": { "values": ["a", "b"], "param": "c" } }
}
EOF

cat > "$tmp/run-settle.mjs" <<'EOF'
import path from 'path';
import { pathToFileURL } from 'url';
const mod = await import(pathToFileURL(process.env.SCENARIO).href);
const { makeHelper } = await import(pathToFileURL(path.resolve(process.env.LIBDIR, 'helpers.mjs')).href);
const out = [];

// (h) scenario pass-through: whatever `h.settle` carries reaches every state's capture opts.
process.env.VISUAL_STATES = path.resolve(process.cwd(), 'manifest-settle.json');
const runScenario = async (settle) => {
  const seen = [];
  const h = { url: 'http://127.0.0.1:1/', settle, async goto() {}, async snapshotForced(l, o) { seen.push(o?.settle); } };
  await mod.default({}, h);
  return seen;
};
const withSettle = await runScenario(6500);
out.push(`H_COUNT=${withSettle.length}`);
out.push(`H_ALL=${withSettle.length > 0 && withSettle.every((v) => v === 6500) ? 1 : 0}`);
// Control: an unset settle must not materialize a number — proves the value comes from `h`.
out.push(`H_CTL=${(await runScenario(undefined)).every((v) => v === undefined) ? 1 : 0}`);

// (i) helper ordering: marker wait -> dwell(MS) -> shutter. Fake page, no browser.
const fakePage = (calls) => ({
  async waitForSelector() { calls.push('marker'); return {}; },
  async waitForTimeout(ms) { calls.push(`dwell:${ms}`); },
  async evaluate() { return {}; },
  async screenshot() { calls.push('shutter'); return Buffer.alloc(1); },
});
const drive = async ({ settle, perCall }) => {
  const calls = [];
  const collector = { addSnapshot() {}, addAssert() {}, addRung0() {} };
  const h = makeHelper({ page: fakePage(calls), cell: { label: 'c1' }, collector, rung0: { enabled: false }, settle });
  await h.snapshotForced('s', perCall === undefined ? { marker: '[x]' } : { marker: '[x]', settle: perCall });
  return calls.join('>');
};
out.push(`I_ORDER=${await drive({ settle: 250 })}`);
out.push(`I_ZERO=${await drive({ settle: 0 })}`);
out.push(`I_PERCALL=${await drive({ settle: 0, perCall: 40 })}`);

console.log(out.join('\n'));
EOF

out3="$(cd "$tmp" && SCENARIO="$SCENARIO" LIBDIR="$WINDIR/lib" node run-settle.mjs 2>&1)"; rc3=$?
if [ "$rc3" != 0 ]; then
  fail "settle harness ran clean" "node exited rc=$rc3 out=[$out3]"
else
  get3() { printf '%s\n' "$out3" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "h) every enumerated state is captured (2)"                     "$(get3 H_COUNT)"   "2"
  check "h) scenario hands --settle through to every state"             "$(get3 H_ALL)"     "1"
  check "h) an unset settle stays unset (value comes from h, not code)" "$(get3 H_CTL)"     "1"
  check "i) dwell fires BETWEEN the marker wait and the shutter"        "$(get3 I_ORDER)"   "marker>dwell:250>shutter"
  check "i) settle 0 adds no dwell at all"                              "$(get3 I_ZERO)"    "marker>shutter"
  check "i) a per-capture settle overrides the run default"             "$(get3 I_PERCALL)" "marker>dwell:40>shutter"
fi

echo
[ "$fails" = 0 ] && echo "ALL states-enum TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
