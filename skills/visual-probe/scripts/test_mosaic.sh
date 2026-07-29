#!/usr/bin/env bash
# Regression test for the mosaic compositor: its LAYOUT MATH (lib/mosaic.mjs) and the `compose`
# COMMAND that drives it (lib/cli.mjs).
#
# WHY committed: the sheet's whole value is that a reader sees real per-tile signal, and two numbers
# decide whether it does. (1) The sheet must stay under the image-Read downscale ceiling on BOTH
# axes — a sheet that overflows it gets crushed on the way in and silently loses the signal it was
# built to carry, so overflow must produce MORE sheets, never a bigger one. (2) The final segment
# tile must be trimmed to the page's actual remainder: the calibration run's padded blank final tile
# read as a blank region of the app and caused a false escalation. Both are pure arithmetic, so they
# are pinned in Part A without a browser — the re-screenshot round-trip adds no evidence about math.
#
# Part C pins the layer ABOVE the math: `compose <out-dir>` is what a dispatched review leaf is told
# to run, so a break there surfaces as an opaque runtime error mid-dispatch, with no one in the loop
# to debug it. What it owns and the math does not: reading a finished capture manifest, resolving
# each cell's viewport geometry (the manifest's own record first, a WxH@DSF label second), the
# per-group sheet split, sheet naming, the content-crop measure pass, and — the one that decides
# whether a gap is visible or silent — REPORTING a frame it cannot use instead of dropping it.
# That layer only exists end to end, so Part C drives the real capture path and needs
# node_modules/playwright plus a working Edge channel; where those are absent it reports SKIP rather
# than a false PASS.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Exit code = failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Windows (Git Bash) node needs a Windows-style path for pathToFileURL / file:// targets; `pwd -W`
# yields it, and we fall back to the POSIX path on platforms without it (Linux/macOS node resolves
# those natively).
WINDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || printf '%s' "$DIR")"
SKILL_ROOT="$(cd "$DIR/.." && pwd)"
MOSAIC="$WINDIR/lib/mosaic.mjs"

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

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-mosaic-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
WINTMP="$(cd "$tmp" && pwd -W 2>/dev/null || printf '%s' "$tmp")"
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------
# Part A (checks a–g) — layout math and sheet rendering (pure; no browser)
# ---------------------------------------------------------------------------------------------
cat > "$tmp/run.mjs" <<'EOF'
import fs from 'fs';
import { pathToFileURL } from 'url';
const { pngSize, planTiles, planSheets, sheetHtml } = await import(pathToFileURL(process.env.MOSAIC).href);
const out = [];

// (a) PNG dimensions from the IHDR chunk — the compositor reads frame sizes off disk with no
// image dependency, so a wrong parse would mis-slice every segment.
const ihdr = Buffer.alloc(24);
Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(ihdr, 0);
ihdr.writeUInt32BE(13, 8); ihdr.write('IHDR', 12); ihdr.writeUInt32BE(1440, 16); ihdr.writeUInt32BE(2600, 20);
fs.writeFileSync('frame.png', ihdr);
const d = pngSize(fs.readFileSync('frame.png'));
out.push(`A_DIMS=${d.width}x${d.height}`);
fs.writeFileSync('not.png', Buffer.from('this is not a png at all, not even close'));
let rejected = 0;
try { pngSize(fs.readFileSync('not.png')); } catch { rejected = 1; }
out.push(`A_REJECTS_NON_PNG=${rejected}`);

// (b) segmentation: a 1440x2600 capture of a 1440x900 viewport at dsf 1.
// tileW = 400*1440/900 = 640 -> scale 0.4444 -> scaled height 1155.6 -> 3 segments, last one short.
const src = { name: 'home', href: 'frame.png', pngW: 1440, pngH: 2600, vw: 1440, vh: 900, dsf: 1 };
const t = planTiles(src, { tileH: 400 });
out.push(`B_COUNT=${t.length}`);
out.push(`B_WIDTHS=${[...new Set(t.map((x) => x.w))].join(',')}`);
out.push(`B_HEIGHTS=${t.map((x) => x.h).join(',')}`);
out.push(`B_BGY=${t.map((x) => x.bgY).join(',')}`);
out.push(`B_CAPTION=${t[2].caption}`);

// (c) final-tile trim: the last tile is the page REMAINDER, never a full tile padded with blank.
out.push(`C_LAST_IS_TRIMMED=${t[t.length - 1].h < 400 ? 1 : 0}`);
// exact multiple -> no trim, no phantom extra segment
const exact = planTiles({ ...src, pngH: 1800 }, { tileH: 400 });
out.push(`C_EXACT=${exact.length}:${exact[exact.length - 1].h}`);
// sliver remainder -> folded into the previous tile rather than emitted as a 2px strip
const sliver = planTiles({ ...src, pngH: 1805 }, { tileH: 400 });
out.push(`C_SLIVER=${sliver.length}:${sliver[sliver.length - 1].h}`);

// (d) content crop: the tile shows only the cropped window, at the cropped aspect.
const cropped = planTiles({ ...src, crop: { x: 220, width: 1000 } }, { tileH: 400 });
out.push(`D_W=${cropped[0].w}`);
out.push(`D_BGW=${cropped[0].bgW}`);
out.push(`D_BGX=${cropped[0].bgX}`);

// (e) sheet budget: the ceiling holds on BOTH axes, and overflow makes more sheets.
const many = Array.from({ length: 6 }, (_, i) => ({ ...src, name: `cell${i}` }));
const sheets = planSheets(many, { tileH: 400, maxSide: 1568 });
out.push(`E_SHEETS=${sheets.length > 1 ? 'multi' : 'single'}`);
out.push(`E_TILES=${sheets.reduce((n, s) => n + s.tiles.length, 0)}`);
out.push(`E_WITHIN=${sheets.every((s) => s.width <= 1568 && s.height <= 1568) ? 1 : 0}`);
out.push(`E_INSIDE=${sheets.every((s) => s.tiles.every((x) => x.x >= 0 && x.y >= 0 && x.x + x.w <= s.width && x.y + x.h <= s.height)) ? 1 : 0}`);
const one = planSheets([src], { tileH: 400, maxSide: 1568 });
out.push(`E_ONE=${one.length}:${one[0].width}x${one[0].height}`);

// (f) a tile wider than the whole sheet shrinks instead of overflowing it.
const wide = planSheets([{ ...src, pngW: 3000, pngH: 1000, vw: 3000, vh: 500 }], { tileH: 400, maxSide: 1568 });
out.push(`F_FITS=${wide.every((s) => s.width <= 1568 && s.tiles.every((x) => x.w <= 1568)) ? 1 : 0}`);

// (g) rendered sheet carries one background-positioned tile per segment + the preload handles the
// re-screenshot waits on (CSS backgrounds expose no load event of their own).
const html = sheetHtml(one[0], { title: 'mosaic-test' });
out.push(`G_TILES=${(html.match(/background-position/g) || []).length}`);
out.push(`G_PRELOAD=${(html.match(/<img src=/g) || []).length}`);
out.push(`G_SIZE=${html.includes(`width:${one[0].width}px`) && html.includes(`height:${one[0].height}px`) ? 1 : 0}`);

console.log(out.join('\n'));
EOF

out="$(cd "$tmp" && MOSAIC="$MOSAIC" node run.mjs 2>&1)"; rc=$?
if [ "$rc" != 0 ]; then
  fail "node harness ran clean" "node exited rc=$rc out=[$out]"
  echo
  echo "$fails FAILURES"
  exit "$fails"
fi
get() { printf '%s\n' "$out" | grep -E "^$1=" | head -1 | cut -d= -f2-; }

check "a) PNG dimensions read from the IHDR chunk"                  "$(get A_DIMS)"             "1440x2600"
check "a) a non-PNG file is rejected, not silently mis-parsed"      "$(get A_REJECTS_NON_PNG)"  "1"
check "b) a 2.9-screenful page slices into 3 segments"              "$(get B_COUNT)"            "3"
check "b) every tile is one viewport wide at the cell's aspect"     "$(get B_WIDTHS)"           "640"
check "b) segment heights are full,full,remainder"                  "$(get B_HEIGHTS)"          "400,400,356"
check "b) each segment steps one tile height down the frame"        "$(get B_BGY)"              "0,-400,-800"
check "b) captions name the cell and the segment position"          "$(get B_CAPTION)"          "home · seg 3/3"
check "c) the final tile is trimmed, never blank-padded"            "$(get C_LAST_IS_TRIMMED)"  "1"
check "c) an exact multiple emits no phantom final tile"            "$(get C_EXACT)"            "2:400"
check "c) a sliver remainder folds into the previous tile"          "$(get C_SLIVER)"           "2:402"
check "d) content crop narrows the tile to the cropped aspect"      "$(get D_W)"                "444"
check "d) content crop scales the frame past the tile width"        "$(get D_BGW)"              "639"
check "d) content crop offsets the frame by the crop origin"        "$(get D_BGX)"              "-98"
check "e) an over-budget frame set splits into several sheets"      "$(get E_SHEETS)"           "multi"
check "e) splitting loses no tiles"                                 "$(get E_TILES)"            "18"
check "e) every sheet stays under the 1568px read ceiling"          "$(get E_WITHIN)"           "1"
check "e) every tile lands inside its sheet"                        "$(get E_INSIDE)"           "1"
check "e) one frame's segments wrap into one packed sheet"          "$(get E_ONE)"              "1:1304x808"
check "f) a tile wider than the sheet shrinks to fit"               "$(get F_FITS)"             "1"
check "g) the sheet renders one positioned tile per segment"        "$(get G_TILES)"            "3"
check "g) the sheet preloads its frames so the shot can wait"       "$(get G_PRELOAD)"          "1"
check "g) the sheet body is sized to the planned geometry"          "$(get G_SIZE)"             "1"

# ---------------------------------------------------------------------------------------------
# Part C (checks h–k) — the `compose` command over a REAL capture dir (needs playwright + Edge)
# ---------------------------------------------------------------------------------------------
# The fixture: a 300px column centered in the viewport and 1400px tall, so every captured frame is
# several screenfuls deep (segmentation is exercised, not just asserted in the abstract) and carries
# wide empty side margins (so --crop-content has something real to measure and trim).
cat > "$tmp/page.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>compose fixture</title><style>
  html, body { margin: 0; background: #fff; }
  body { font: 14px/1.4 system-ui, sans-serif; color: #1a1a1a; }
  .col { width: 300px; margin: 0 auto; height: 1400px; }
  .band { height: 330px; background: #d8dee6; margin-bottom: 20px; }
</style></head><body>
  <div class="col">
    <div class="band">one</div><div class="band">two</div><div class="band">three</div><div class="band">four</div>
  </div>
</body></html>
EOF

# Mutated copies of a finished capture dir — each isolates one geometry-resolution branch a healthy
# dir never reaches. Built from the capture BEFORE any compose run, so no sheet output leaks in.
# `file` is rewritten to a bare name where a copy must read its OWN frames (the harness records
# absolute paths, which would otherwise point back at the original dir).
cat > "$tmp/mutate.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
const root = process.argv[2];
const load = (d) => JSON.parse(fs.readFileSync(path.join(root, d, 'manifest.json'), 'utf8'));
const save = (d, m) => fs.writeFileSync(path.join(root, d, 'manifest.json'), JSON.stringify(m, null, 2));

// (1) out-skip: frame 2's cell label is not resolvable geometry, frame 3's PNG is gone from disk.
const skip = load('out-skip');
const es = skip.snapshots[0].entries;
for (const e of es) e.file = path.basename(e.file);
es[1].cell = 'mobile';
fs.unlinkSync(path.join(root, 'out-skip', es[2].file));
save('out-skip', skip);

// (2) out-geo: no cellGeometry at all — geometry must fall back to the WxH@DSF cell label.
const geo = load('out-geo');
delete geo.cellGeometry;
save('out-geo', geo);

// (3) out-pref: cellGeometry DISAGREES with the label (a 300px viewport vs the label's 600px), on
// one frame only. Whichever source wins is legible in the tile width, so precedence is observable.
const pref = load('out-pref');
pref.snapshots[0].entries = pref.snapshots[0].entries.slice(0, 1);
pref.snapshots[0].entries[0].file = path.basename(pref.snapshots[0].entries[0].file);
pref.cellGeometry = [{ label: 'cell0-800x600@1', width: 800, height: 300, dsf: 1 }];
save('out-pref', pref);
EOF

# Reads one compose run's stdout JSON plus the sheet HTML/PNG it points at — the rendered sheet is
# the ground truth for what actually landed, not the run's own summary counts.
cat > "$tmp/read-compose.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
const j = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sheets = j.sheets || [];
const skipped = j.skipped || [];
const out = [];
const html = (s) => fs.readFileSync(s.html, 'utf8');
const captions = (s) => [...html(s).matchAll(/<div class="c"[^>]*>([^<]*)<\/div>/g)].map((m) => m[1]);
const pngDims = (f) => { const b = fs.readFileSync(f); return `${b.readUInt32BE(16)}x${b.readUInt32BE(20)}`; };
const tiles = sheets.reduce((n, s) => n + s.tiles, 0);

out.push(`SHEETS=${sheets.length}`);
out.push(`NAMES=${sheets.map((s) => path.basename(s.file)).join(',')}`);
out.push(`GROUPS=${sheets.map((s) => String(s.group)).join(',')}`);
out.push(`GEOM=${sheets.map((s) => `${s.width}x${s.height}`).join(',')}`);
out.push(`TILES=${tiles}`);
out.push(`PER_SHEET_TILES=${sheets.map((s) => s.tiles).join(',')}`);
// Every reported sheet is on disk AND its PNG really is the geometry the run claimed.
out.push(`ON_DISK=${sheets.every((s) => fs.existsSync(s.file) && pngDims(s.file) === `${s.width}x${s.height}`) ? 1 : 0}`);

const caps = sheets.flatMap(captions);
out.push(`TILES_RENDERED=${caps.length === tiles ? 1 : 0}`);
const runs = new Map();
for (const c of caps) {
  const m = /^(.+) · seg (\d+)\/(\d+)$/.exec(c);
  if (!m) continue;
  const r = runs.get(m[1]) || { n: +m[3], seen: new Set() };
  r.seen.add(+m[2]);
  runs.set(m[1], r);
}
const names = [...runs.keys()].sort();
out.push(`FRAMES=${names.join('|')}`);
out.push(`SEG_RUNS=${names.map((n) => `${runs.get(n).seen.size}/${runs.get(n).n}`).join(',')}`);
out.push(`SHEET_FRAMES=${sheets.map((s) => [...new Set(captions(s).map((c) => c.replace(/ · seg \d+\/\d+$/, '')))].join(';')).join(',')}`);
// Tile div geometry: `width:Wpx;height:Hpx;` is a tile — the caption div carries no height.
const tileW = sheets.flatMap((s) => [...html(s).matchAll(/width:(\d+)px;height:\d+px;/g)].map((m) => +m[1]));
out.push(`MAX_TILE_W=${tileW.length ? Math.max(...tileW) : 'NONE'}`);
out.push(`TILES_SHIFTED_X=${sheets.flatMap((s) => [...html(s).matchAll(/background-position:(-?\d+)px/g)].map((m) => +m[1])).filter((v) => v < 0).length}`);
out.push(`SKIPPED=${skipped.map((s) => s.name).join('|')}`);
out.push(`SKIP_REASONS=${skipped.map((s) => (/frame not on disk/.test(s.reason) ? 'missing' : /no viewport geometry/.test(s.reason) ? 'geometry' : 'other')).join(',')}`);
console.log(out.join('\n'));
EOF

# One compose run → its parsed shape. stdout is the run's JSON contract; stderr is the human log,
# kept for the failure message. A run that dies emits COMPOSE_RC so the checks fail on empty values.
compose_run() {
  tag="$1"; shift
  node "$WINDIR/probe.mjs" compose "$@" > "$tmp/compose-$tag.json" 2> "$tmp/compose-$tag.err"
  rc=$?
  if [ "$rc" != 0 ] || [ ! -s "$tmp/compose-$tag.json" ]; then
    printf 'COMPOSE_RC=%s err=%s\n' "$rc" "$(tail -2 "$tmp/compose-$tag.err" 2>/dev/null)"
    return 0
  fi
  node "$WINTMP/read-compose.mjs" "$WINTMP/compose-$tag.json" 2>&1
}

if [ ! -f "$SKILL_ROOT/node_modules/playwright/package.json" ]; then
  skip "h-k) the compose command over a real capture dir" \
       "node_modules/playwright not installed here — the compose CLI path is UNVERIFIED by this run"
else
  # Three cells differing in viewport HEIGHT and WIDTH: tile aspect is derived per cell, so
  # identical cells would let a geometry mix-up pass unnoticed.
  shot_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/page.html" --matrix "800x600@1,800x400@1,600x600@1" \
      --out "$WINTMP/out-compose" 2>&1)"; rcShot=$?
  if [ "$rcShot" != 0 ] || [ ! -f "$tmp/out-compose/manifest.json" ]; then
    skip "h-k) the compose command over a real capture dir" \
         "capture did not run (rc=$rcShot) — Edge channel unavailable? out=[$(printf '%s' "$shot_out" | tail -2)]"
  else
    cp -r "$tmp/out-compose" "$tmp/out-skip"
    cp -r "$tmp/out-compose" "$tmp/out-geo"
    mkdir -p "$tmp/out-pref"
    cp "$tmp/out-compose/manifest.json" "$tmp/out-compose/shot__cell0-800x600@1.png" "$tmp/out-pref/"
    node "$WINTMP/mutate.mjs" "$WINTMP" || fail "h) the mutated fixtures build" "mutate.mjs failed"

    # ---- (h) the default run: a finished capture dir in, contact sheets out -------------------
    H="$(compose_run a "$WINTMP/out-compose")"
    getH() { printf '%s\n' "$H" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "h) every captured frame reaches the sheet"                   "$(getH FRAMES)" \
          "shot · cell0-800x600@1|shot · cell1-800x400@1|shot · cell2-600x600@1"
    check "h) each frame contributes its COMPLETE segment run"          "$(getH SEG_RUNS)"        "3/3,4/4,3/3"
    check "h) the reported tile count is the count really rendered"     "$(getH TILES_RENDERED)"  "1"
    check "h) the tile total is every frame's segments, none lost"      "$(getH TILES)"           "10"
    check "h) an over-budget set spills into a second sheet"            "$(getH PER_SHEET_TILES)" "5,5"
    check "h) multi-sheet output indexes every sheet's filename"        "$(getH NAMES)"           "mosaic-1.png,mosaic-2.png"
    check "h) each sheet PNG exists at the geometry the run reported"   "$(getH ON_DISK)"         "1"
    check "h) a healthy capture dir skips nothing"                      "$(getH SKIPPED)"         ""
    check "h) tiles are drawn unshifted when no crop was measured"      "$(getH TILES_SHIFTED_X)" "0"

    # ---- (i) --group: one sheet set per group, plus a bucket for the misses -------------------
    I="$(compose_run g "$WINTMP/out-compose" --group "(cell0|cell1)")"
    getI() { printf '%s\n' "$I" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "i) --group splits the sheets by its first capture group"     "$(getI GROUPS)"          "cell0,cell1,ungrouped"
    check "i) a frame the regex misses lands in the ungrouped bucket"   "$(getI SHEET_FRAMES)" \
          "shot · cell0-800x600@1,shot · cell1-800x400@1,shot · cell2-600x600@1"
    check "i) each group's sheet is named for its group, unindexed"     "$(getI NAMES)" \
          "mosaic-cell0.png,mosaic-cell1.png,mosaic-ungrouped.png"
    check "i) grouping re-cuts the sheets and loses no tile"            "$(getI TILES)"           "10"
    check "i) each group sheet PNG exists at its reported geometry"     "$(getI ON_DISK)"         "1"

    # ---- (j) --crop-content: the measured trim reaches the rendered sheet ---------------------
    # The fixture's content column is 300px wide inside an 800px frame: measureContentBox finds it
    # and pads 8px a side, so the tile narrows to that 316px window instead of spending most of its
    # width on empty margin. Read against (h)'s untrimmed run over the same frames.
    J="$(compose_run c "$WINTMP/out-compose" --crop-content)"
    getJ() { printf '%s\n' "$J" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "j) untrimmed, the widest tile is the full frame width"       "$(getH MAX_TILE_W)"      "800"
    check "j) --crop-content narrows tiles to the measured content box" "$(getJ MAX_TILE_W)"      "316"
    check "j) every tile is offset by its crop origin, not left at 0"   "$(getJ TILES_SHIFTED_X)" "10"
    check "j) trimming margins costs no tile and no segment"            "$(getJ SEG_RUNS)"        "3/3,4/4,3/3"
    check "j) the narrower tiles repack into one unindexed sheet"       "$(getJ NAMES)"           "mosaic.png"
    check "j) the trimmed sheet PNG exists at its reported geometry"    "$(getJ ON_DISK)"         "1"

    # ---- (k) geometry resolution + skipped-frame bookkeeping ----------------------------------
    K="$(compose_run s "$WINTMP/out-skip")"
    getK() { printf '%s\n' "$K" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "k) an unusable frame is REPORTED skipped, never dropped"     "$(getK SKIPPED)" \
          "shot · mobile|shot · cell2-600x600@1"
    check "k) each skip names its own cause (geometry vs missing file)" "$(getK SKIP_REASONS)"    "geometry,missing"
    check "k) the usable frame still composes past its skipped peers"   "$(getK FRAMES)"          "shot · cell0-800x600@1"
    check "k) a relative frame path resolves against the out-dir"       "$(getK TILES)"           "3"

    G="$(compose_run e "$WINTMP/out-geo")"
    getG() { printf '%s\n' "$G" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "k) a WxH@DSF label stands in for absent cellGeometry"        "$(getG GEOM)"            "$(getH GEOM)"
    check "k) the label fallback slices identically, nothing skipped"   "$(getG SEG_RUNS):$(getG SKIPPED)" "3/3,4/4,3/3:"

    # cellGeometry says the viewport was 300 tall (→ 1067px tiles); the label says 600 (→ 533px).
    P="$(compose_run p "$WINTMP/out-pref")"
    getP() { printf '%s\n' "$P" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "k) cellGeometry wins over a disagreeing WxH@DSF label"       "$(getP MAX_TILE_W)"      "1067"
  fi
fi

echo
[ "$fails" = 0 ] && echo "ALL mosaic TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
