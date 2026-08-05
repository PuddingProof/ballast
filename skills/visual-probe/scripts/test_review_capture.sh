#!/usr/bin/env bash
# Regression test for the `review-capture` and `crop` verbs (lib/fused.mjs).
#
# WHY committed: review-capture is the deep rung's single invocation, and three of its properties
# are the reason the rung stops needing hand-authored scenarios and extra turns.
#   1. CONSOLE LISTENERS ARE BUILT IN, per cell. Every review used to either author a scenario just
#      to attach them or file a verdict with a standing "console not checked" exception. They must
#      FIRE on a page that throws and stay SILENT on one that doesn't — a listener that reports
#      nothing is indistinguishable from a clean page, which is exactly the false pass being closed.
#   2. THE DSF TRIAD IS NATIVE-ONLY. Non-integer scales are load-bearing (a clean 2.0x is the scale
#      that HIDES boundary-tie blit defects), but paying them at every breakpoint would multiply the
#      sweep. The triad is added at the matrix's first cell and nowhere else — pinned from both
#      sides, since a triad that silently spread would quietly restore the cost the fusion saved.
#   3. SHEETS GROUP PER ROUTE (and per fidelity band), so a reader compares like with like instead
#      of scrolling a mixed sheet.
# `crop` is pinned for two properties above its golden path: it MERGES into the out-dir's manifest
# (the evidence beside it is the capture the crop was taken to corroborate — clobbering that
# manifest would destroy the thing the crop exists to support), and a FAILED crop records as a
# `cropHoles` row, never a `coverageHoles` one: coverage holes block a clean pass on the capture,
# and a selector this crop could not find says nothing about the cells that were shot.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Everything here drives
# the real browser path and needs node_modules/playwright plus a working Edge channel; where those
# are absent it reports SKIP rather than a false PASS. Exit code = failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WINDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || printf '%s' "$DIR")"
SKILL_ROOT="$(cd "$DIR/.." && pwd)"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; fails=$((fails + 1)); }
skip() { printf 'SKIP  %s\n      %s\n' "$1" "$2"; }
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected [$3], got [$2]"; fi; }

if ! command -v node >/dev/null 2>&1; then
  fail "0 node availability" "'node' not found on PATH -- cannot run this test"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-review-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
WINTMP="$(cd "$tmp" && pwd -W 2>/dev/null || printf '%s' "$tmp")"
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# A page that is broken in the three ways a browser reports at runtime, and its quiet twin. Both are
# several screenfuls tall so the full-page capture (review-capture does NOT clamp to the viewport,
# unlike glance) is observable in the frame dimensions.
cat > "$tmp/noisy.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>noisy</title><style>
  html, body { margin: 0; background: #fff; }
  body { font: 14px/1.5 system-ui, sans-serif; color: #1a1a1a; }
  .band { height: 300px; border-bottom: 1px solid #d8dee6; padding: 12px; }
</style></head><body>
  <div class="band">one <img src="missing-asset.png" alt="broken"></div>
  <div class="band">two</div><div class="band">three</div><div class="band" id="target">four</div>
  <script>
    console.error('deliberate console error from the fixture');
    throw new Error('deliberate uncaught error from the fixture');
  </script>
</body></html>
EOF

cat > "$tmp/quiet.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>quiet</title><style>
  html, body { margin: 0; background: #fff; }
  body { font: 14px/1.5 system-ui, sans-serif; color: #1a1a1a; }
  .band { height: 300px; border-bottom: 1px solid #d8dee6; padding: 12px; }
</style></head><body>
  <div class="band">one</div><div class="band">two</div><div class="band">three</div><div class="band" id="target">four</div>
</body></html>
EOF

cat > "$tmp/read-rc.mjs" <<'EOF'
import fs from 'fs';
const m = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
out.push(`VERB=${m.verb}`);
out.push(`CELLS=${(m.cells || []).join(',')}`);
out.push(`CELL_COUNT=${(m.cells || []).length}`);
out.push(`LAUNCHES=${m.budget.launches}`);
out.push(`FRAMES=${(m.snapshots || []).reduce((n, s) => n + s.entries.length, 0)}`);
out.push(`LABELS=${(m.snapshots || []).map((s) => s.label).sort().join(',')}`);
out.push(`SHEETS=${(m.sheets || []).map((f) => f.replace(/^.*[\\/]/, '')).sort().join(',')}`);
out.push(`SHEETS_ON_DISK=${(m.sheets || []).every((f) => fs.existsSync(f)) ? 1 : 0}`);
out.push(`CONSOLE_COUNT=${(m.console || []).length}`);
out.push(`CONSOLE_KINDS=${[...new Set((m.console || []).map((c) => c.kind))].sort().join(',')}`);
out.push(`CONSOLE_CELLS=${[...new Set((m.console || []).map((c) => c.cell))].length}`);
out.push(`CONSOLE_TEXT=${(m.console || []).some((c) => /deliberate uncaught/.test(c.text)) ? 1 : 0}`);
out.push(`CROPS=${(m.crops || []).length}`);
out.push(`CROP_HOLES=${(m.cropHoles || []).map((h) => h.kind).join(',')}`);
out.push(`COVERAGE_HOLES=${(m.coverageHoles || []).length}`);
out.push(`CONSOLE_MAXLEN=${Math.max(0, ...(m.console || []).map((c) => c.text.length))}`);
out.push(`CONSOLE_OVERFLOW=${m.consoleOverflow || 0}`);
out.push(`CROP_FILES=${(m.crops || []).every((c) => c.magnified && fs.existsSync(c.magnified)) ? 1 : 0}`);
out.push(`CROP_SELECTORS=${[...new Set((m.crops || []).map((c) => c.selector))].join(',')}`);
// Full-page: the frame must be TALLER than the cell viewport on a multi-screenful page.
const geo = new Map((m.cellGeometry || []).map((c) => [c.label, c]));
const dims = (f) => { const b = fs.readFileSync(f); return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) }; };
out.push(`FULLPAGE=${(m.snapshots || []).flatMap((s) => s.entries).every((e) => {
  const g = geo.get(e.cell); return dims(e.file).h > g.height * g.dsf + 1;
}) ? 1 : 0}`);
out.push(`CLAMPED=${m.viewportClamped}`);
console.log(out.join('\n'));
EOF

if [ ! -f "$SKILL_ROOT/node_modules/playwright/package.json" ]; then
  skip "a-e) review-capture + crop over real pages" \
       "node_modules/playwright not installed here — the fused review path is UNVERIFIED by this run"
  echo
  echo "0 FAILURES"
  exit 0
fi

# ---------------------------------------------------------------------------------------------
# (a) the noisy page: console listeners fire, per cell; the DSF triad lands at the native cell only
# ---------------------------------------------------------------------------------------------
a_out="$(node "$WINDIR/probe.mjs" review-capture --url "$WINTMP/noisy.html" --matrix "800x600@1,375x667@1" \
    --out "$WINTMP/out-noisy" 2>&1)"; rcA=$?
if [ "$rcA" != 0 ] || [ ! -f "$tmp/out-noisy/manifest.json" ]; then
  skip "a-e) review-capture + crop over real pages" \
       "review-capture did not run (rc=$rcA) — Edge channel unavailable? out=[$(printf '%s' "$a_out" | tail -2)]"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi

A="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-noisy/manifest.json" 2>&1)"
getA() { printf '%s\n' "$A" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
check "a) the fidelity triad is added at the NATIVE cell only"      "$(getA CELLS)" \
      "cell0-800x600@1,cell1-375x667@1,cell0-800x600@1@1.5,cell0-800x600@1@2"
check "a) …so a two-cell matrix sweeps four cells, not six"         "$(getA CELL_COUNT)"      "4"
check "a) every cell still runs on ONE browser launch"              "$(getA LAUNCHES)"        "1"
check "a) every cell produced a frame"                              "$(getA FRAMES)"          "4"
check "a) console/pageerror/requestfailed all report"               "$(getA CONSOLE_KINDS)"   "console-error,pageerror,requestfailed"
check "a) …the uncaught error's own text is carried"                "$(getA CONSOLE_TEXT)"    "1"
check "a) …and listeners are per-cell, not attached once"           "$(getA CONSOLE_CELLS)"   "4"
check "a) review frames are FULL-PAGE, not viewport-clamped"        "$(getA FULLPAGE):$(getA CLAMPED)" "1:false"
check "a) the review exits 0"                                       "$rcA"                    "0"

# ---------------------------------------------------------------------------------------------
# (b) the quiet twin: the listeners stay silent — a fire-only test would pass on a stuck listener
# ---------------------------------------------------------------------------------------------
node "$WINDIR/probe.mjs" review-capture --url "$WINTMP/quiet.html" --matrix "800x600@1" \
    --no-dsf-triad --out "$WINTMP/out-quiet" >/dev/null 2>&1; rcB=$?
if [ ! -f "$tmp/out-quiet/manifest.json" ]; then
  fail "b) the quiet twin captures cleanly" "no manifest.json"
else
  B="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-quiet/manifest.json" 2>&1)"
  getB() { printf '%s\n' "$B" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "b) a clean page reports ZERO console entries"              "$(getB CONSOLE_COUNT)"   "0"
  check "b) --no-dsf-triad sweeps exactly the matrix given"         "$(getB CELLS)"           "cell0-800x600@1"
  check "b) the quiet twin exits 0"                                 "$rcB"                    "0"
fi

# ---------------------------------------------------------------------------------------------
# (c) several routes in ONE invocation, sheets grouped per route (and per fidelity band)
# ---------------------------------------------------------------------------------------------
node "$WINDIR/probe.mjs" review-capture --url "$WINTMP/quiet.html" --urls "$WINTMP/noisy.html" \
    --matrix "800x600@1" --out "$WINTMP/out-routes" >/dev/null 2>&1
if [ ! -f "$tmp/out-routes/manifest.json" ]; then
  fail "c) several routes sweep in one invocation" "no manifest.json"
else
  C="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-routes/manifest.json" 2>&1)"
  getC() { printf '%s\n' "$C" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "c) each route is captured under its own label"             "$(getC LABELS)"          "noisy,quiet"
  check "c) …across the whole cell set, on one launch"              "$(getC FRAMES):$(getC LAUNCHES)" "6:1"
  check "c) sheets are grouped per route and fidelity band"         "$(getC SHEETS)" \
        "mosaic-noisy-dsf.png,mosaic-noisy.png,mosaic-quiet-dsf.png,mosaic-quiet.png"
  check "c) every grouped sheet is on disk"                         "$(getC SHEETS_ON_DISK)"  "1"
fi

# ---------------------------------------------------------------------------------------------
# (d) crop: the golden path, post-hoc, MERGING into the capture manifest beside it
# ---------------------------------------------------------------------------------------------
d_out="$(node "$WINDIR/probe.mjs" crop --url "$WINTMP/noisy.html" --selector "#target" \
    --matrix "800x600@1" --magnify 4 --out "$WINTMP/out-noisy" 2>&1)"; rcD=$?
D="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-noisy/manifest.json" 2>&1)"
getD() { printf '%s\n' "$D" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
check "d) the crop is recorded in the manifest"                     "$(getD CROPS)"           "1"
check "d) …with its magnified PNG on disk"                          "$(getD CROP_FILES)"      "1"
check "d) …naming the selector it magnified"                        "$(getD CROP_SELECTORS)"  "#target"
check "d) the capture it corroborates is left intact"               "$(getD FRAMES):$(getD CELL_COUNT)" "4:4"
check "d) …including the sheets already composed"                   "$(getD SHEETS_ON_DISK)"  "1"
check "d) the console findings survive the merge"                   "$(getD CONSOLE_KINDS)"   "console-error,pageerror,requestfailed"
check "d) crop exits 0"                                             "$rcD"                    "0"

# A second crop ACCUMULATES rather than replacing the first — a review takes several.
node "$WINDIR/probe.mjs" crop --url "$WINTMP/noisy.html" --selector "body > .band" \
    --matrix "800x600@1" --magnify 4 --out "$WINTMP/out-noisy" >/dev/null 2>&1
D2="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-noisy/manifest.json" 2>&1)"
getD2() { printf '%s\n' "$D2" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
check "d) a second crop accumulates beside the first"               "$(getD2 CROPS)"          "2"
check "d) …each keeping its own selector"                           "$(getD2 CROP_SELECTORS)" "#target,body > .band"

# ---------------------------------------------------------------------------------------------
# (e) a crop of a selector that isn't there is a named hole, not a crash and not a silent pass
# ---------------------------------------------------------------------------------------------
e_out="$(node "$WINDIR/probe.mjs" crop --url "$WINTMP/quiet.html" --selector "#nope" \
    --matrix "800x600@1" --out "$WINTMP/out-crop-miss" 2>&1)"; rcE=$?
E="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-crop-miss/manifest.json" 2>&1)"
getE() { printf '%s\n' "$E" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
check "e) a missing crop selector becomes a named crop hole"        "$(getE CROP_HOLES)"      "crop-error"
check "e) …and never demotes the capture's coverage"                "$(getE COVERAGE_HOLES)"  "0"
check "e) …and exits non-zero, since nothing was magnified"         "$rcE"                    "1"

# The same rule over a REAL capture: a failed crop beside a clean glance must leave that manifest's
# coverage untouched — otherwise one bad selector turns a passing capture into a blocked one.
node "$WINDIR/probe.mjs" crop --url "$WINTMP/quiet.html" --selector "#nope" \
    --matrix "800x600@1" --out "$WINTMP/out-quiet" >/dev/null 2>&1
E2="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-quiet/manifest.json" 2>&1)"
getE2() { printf '%s\n' "$E2" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
check "e) a failed crop over a clean capture keeps it hole-free"    "$(getE2 COVERAGE_HOLES):$(getE2 CROP_HOLES)" "0:crop-error"
check "e) …and the capture's own frames survive the merge"          "$(getE2 FRAMES)"         "1"

# ---------------------------------------------------------------------------------------------
# (f) a STATE SWEEP through the same fused verb: theme is a route's axis, not a harness dimension,
#     so the grouping has to lift the theme token OUT of the state label — otherwise `home` and
#     `home__dark` land as two unrelated routes and the reader loses the only comparison that
#     matters for a theme change.
# ---------------------------------------------------------------------------------------------
cat > "$tmp/app.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>state fixture</title><style>
  html,body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#fff;color:#12202e;height:100%}
  body.dark{background:#101820;color:#e8eef5}
</style></head><body>
  <div style="padding:24px"><h1>State fixture</h1><p id="ready">Ready marker</p></div>
  <script>if (new URLSearchParams(location.search).get('theme') === 'dark') document.body.classList.add('dark');</script>
</body></html>
EOF
cat > "$tmp/visual-states.json" <<'EOF'
{
  "readySignal": "#ready",
  "routes": { "home": "app.html" },
  "axes": { "theme": { "param": "theme", "values": ["light", "dark"] } }
}
EOF

node "$WINDIR/probe.mjs" review-capture --url "file:///$WINTMP/app.html" --states "$WINTMP/visual-states.json" \
    --skip-drive-hooks --matrix "800x600@1" --out "$WINTMP/out-states" >/dev/null 2>&1; rcF=$?
if [ ! -f "$tmp/out-states/manifest.json" ]; then
  fail "f) a state sweep runs through the fused verb" "no manifest.json (rc=$rcF)"
else
  F="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-states/manifest.json" 2>&1)"
  getF() { printf '%s\n' "$F" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "f) every enumerated state is captured, on one launch"       "$(getF LABELS):$(getF LAUNCHES)" "home,home__dark:1"
  check "f) …across the native cell and its fidelity triad"          "$(getF FRAMES)"          "6"
  check "f) sheets group per route×theme, theme lifted out"          "$(getF SHEETS)" \
        "mosaic-home-dark-dsf.png,mosaic-home-dark.png,mosaic-home-dsf.png,mosaic-home.png"
  check "f) the state sweep exits 0"                                 "$rcF"                    "0"
fi

# ---------------------------------------------------------------------------------------------
# (g) the console channel is PAGE-CONTROLLED text — it is bounded on both axes before it ever
#     reaches a reviewer's context: 300 chars per entry, 50 entries, and an overflow count so a
#     flood is visible as a number instead of silently truncated evidence.
# ---------------------------------------------------------------------------------------------
cat > "$tmp/flood.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>flood</title></head><body>
  <div style="height:200px">flood</div>
  <script>
    console.error('X'.repeat(5000));
    for (let i = 0; i < 80; i++) console.error('flood entry ' + i);
  </script>
</body></html>
EOF
node "$WINDIR/probe.mjs" review-capture --url "$WINTMP/flood.html" --matrix "800x600@1" \
    --no-dsf-triad --out "$WINTMP/out-flood" >/dev/null 2>&1
if [ ! -f "$tmp/out-flood/manifest.json" ]; then
  fail "g) a flooding page still captures" "no manifest.json"
else
  G="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-flood/manifest.json" 2>&1)"
  getG() { printf '%s\n' "$G" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "g) each console entry is truncated to 300 chars"           "$(getG CONSOLE_MAXLEN)"  "300"
  check "g) …the channel caps at 50 entries"                        "$(getG CONSOLE_COUNT)"   "50"
  check "g) …and the overflow is counted, never silently dropped"   \
        "$([ "$(getG CONSOLE_OVERFLOW)" -gt 0 ] && printf counted || printf silent)" "counted"
fi

# ---------------------------------------------------------------------------------------------
# (h) --states with extra --urls: the state sweep belongs to the BASE url only. Running it per
#     target re-shoots every state under a colliding label, and two targets sharing a label merge
#     into one snapshot — the sweep's evidence would silently overwrite itself.
# ---------------------------------------------------------------------------------------------
node "$WINDIR/probe.mjs" review-capture --url "file:///$WINTMP/app.html" --urls "$WINTMP/quiet.html" \
    --states "$WINTMP/visual-states.json" --skip-drive-hooks --matrix "800x600@1" \
    --no-dsf-triad --out "$WINTMP/out-states-urls" >/dev/null 2>&1; rcH=$?
if [ ! -f "$tmp/out-states-urls/manifest.json" ]; then
  fail "h) --states alongside --urls runs" "no manifest.json (rc=$rcH)"
else
  H="$(node "$WINTMP/read-rc.mjs" "$WINTMP/out-states-urls/manifest.json" 2>&1)"
  getH() { printf '%s\n' "$H" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "h) the sweep runs once, and the extra url is a plain cell"  "$(getH LABELS)"          "home,home__dark,quiet"
  check "h) …with one frame each, no duplicated state labels"       "$(getH FRAMES)"          "3"
  check "h) …on one browser launch, exit 0"                         "$(getH LAUNCHES):$rcH"   "1:0"
fi

echo
[ "$fails" = 0 ] && echo "ALL review-capture TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
