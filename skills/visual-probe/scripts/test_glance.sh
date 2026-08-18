#!/usr/bin/env bash
# Regression test for the FUSED glance path: lib/fused.mjs + lib/budget.mjs + the mosaic's
# placeholder tiles + probe.mjs's stdlib-only `--wait` / `__serve-child` entries.
#
# WHY committed: the glance rung exists to answer a visual question in ~4 model turns, and four
# properties are what make that true rather than aspirational.
#   1. ONE BROWSER LAUNCH per invocation. Capture, magnify/hash and the contact-sheet re-screenshot
#      used to cost three launches across two processes; the fusion is the saving, and a silently
#      re-added second launch would give it all back with no visible symptom. The manifest's launch
#      counter is the assertion.
#   2. A PLACEHOLDER IS NOT A CELL. Coverage holes are rendered INTO the sheet so the gap is in the
#      image the model reads — which immediately creates the risk that a hole gets counted as
#      coverage somewhere. It must be countable nowhere: the tally splits tiles from placeholders,
#      and the sheet marks them structurally (class, not just a caption).
#   3. THE DEADLINE FLUSHES. A hard stop that threw away the frames it already had would be strictly
#      worse than no deadline: partial evidence is still evidence, so unreached cells become named
#      holes, the manifest is still written, and the run still exits 0. `blocked` is reserved for
#      ZERO captures — the one case where a leaf must not file a verdict about the app.
#   4. `--wait` AND `__serve-child` NEVER IMPORT PLAYWRIGHT. Part B proves that by running them in a
#      tree that HAS NO node_modules AND NO lib/cli.mjs: if either path ever reached the CLI, the
#      import would fail loudly instead of passing.
#   5. A WAIT RESOLVES ONLY ON THIS DISPATCH'S EVIDENCE. A previous cycle's manifest is complete in
#      every way the poll can see, so without the epoch check (`--since`, and the 120s grace when
#      it is absent) a leaf reviews the build its dispatch was meant to replace — a silent false
#      pass. The budget block splits the same way: `invocations` is the dispatch's own count,
#      `invocations_total` the out-dir's whole ledger.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Parts A and B are pure
# node (no browser, no node_modules). Part C drives the real capture path and needs
# node_modules/playwright plus a working Edge channel; where those are absent it reports SKIP rather
# than a false PASS. Exit code = failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Windows (Git Bash) node needs a Windows-style path for pathToFileURL / file:// targets; `pwd -W`
# yields it, and we fall back to the POSIX path where it doesn't exist (Linux/macOS).
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

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-glance-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
WINTMP="$(cd "$tmp" && pwd -W 2>/dev/null || printf '%s' "$tmp")"
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------
# Part A — placeholder tiles + budget accounting (pure; no browser)
# ---------------------------------------------------------------------------------------------
cat > "$tmp/run-pure.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
const lib = process.env.LIBDIR;
const { planTiles, planSheets, sheetHtml, tallyTiles } = await import(pathToFileURL(path.join(lib, 'mosaic.mjs')).href);
const { recordInvocation, readBudget, budgetBlock, resolveOutDir, writeManifestAtomic, DEFAULT_OUT } =
  await import(pathToFileURL(path.join(lib, 'budget.mjs')).href);
const out = [];

// (a) a hole yields exactly ONE tile, marked as a placeholder and carrying its reason.
const ph = planTiles({ placeholder: true, name: 'home · 1280x800@1', reason: 'deadline: cut short', vw: 1280, vh: 800 }, { tileH: 400 });
out.push(`A_TILES=${ph.length}`);
out.push(`A_FLAG=${ph[0].placeholder === true ? 1 : 0}`);
out.push(`A_ASPECT=${ph[0].w}x${ph[0].h}`);
out.push(`A_CAPTION=${ph[0].caption}`);

// (b) the tally counts captured tiles and placeholders SEPARATELY — the one rule that keeps a hole
// from being read as coverage. Two real frames (3 segments each) + two holes.
const frame = { name: 'home', href: 'f.png', pngW: 1440, pngH: 2600, vw: 1440, vh: 900, dsf: 1 };
const sheets = planSheets([
  { ...frame, name: 'a' }, { ...frame, name: 'b' },
  { placeholder: true, name: 'c', reason: 'capture-error: boom', vw: 1440, vh: 900 },
  { placeholder: true, name: 'd', reason: 'deadline', vw: 1440, vh: 900 },
], { tileH: 400, maxSide: 1568 });
const t = tallyTiles(sheets);
out.push(`B_TILES=${t.tiles}`);
out.push(`B_PLACEHOLDERS=${t.placeholders}`);
out.push(`B_TOTAL_DIVIDED=${t.tiles + t.placeholders === sheets.reduce((n, s) => n + s.tiles.length, 0) ? 1 : 0}`);
out.push(`B_WITHIN=${sheets.every((s) => s.width <= 1568 && s.height <= 1568) ? 1 : 0}`);

// (c) the rendered sheet marks a placeholder STRUCTURALLY (its own class + its reason as ink), and
// never draws a frame behind it — a caption alone would be invisible to a downscaled read.
const html = sheets.map((s, i) => sheetHtml(s, { title: `ph-${i}` })).join('\n');
out.push(`C_CLASS=${(html.match(/class="t ph"/g) || []).length}`);
// Twice per hole, deliberately: as INK inside the tile (a downscaled read still shows it) and again
// in the caption strip below it (which a crop of the tile alone would lose).
out.push(`C_NOT_CAPTURED=${(html.match(/class="pht">NOT CAPTURED/g) || []).length}:${(html.match(/NOT CAPTURED/g) || []).length}`);
out.push(`C_REASON=${/capture-error: boom/.test(html) ? 1 : 0}`);
const phDiv = /<div class="t ph"[^>]*>/.exec(html);
out.push(`C_NO_BACKGROUND=${phDiv && !/background-image/.test(phDiv[0]) ? 1 : 0}`);

// (d) budget accounting: cumulative per out-dir, corrupt ledger reads as absent, never throws.
const bdir = path.join(process.cwd(), 'bud');
recordInvocation(bdir, 'glance');
recordInvocation(bdir, 'crop');
out.push(`D_COUNT=${readBudget(bdir).invocations.length}`);
out.push(`D_VERBS=${readBudget(bdir).invocations.map((i) => i.verb).join(',')}`);
out.push(`D_FILE=${fs.existsSync(path.join(bdir, 'budget.json')) ? 1 : 0}`);
fs.writeFileSync(path.join(bdir, 'budget.json'), '{not json');
out.push(`D_CORRUPT_READS_NULL=${readBudget(bdir) === null ? 1 : 0}`);
recordInvocation(bdir, 'glance');
out.push(`D_CORRUPT_RECOVERS=${readBudget(bdir).invocations.length}`);
const blk = budgetBlock(bdir, { launches: 1, stageMs: { import: 10.4, launch: 20, capture: 30, flush: 40, compose: 50 }, wallMs: 150.6 });
out.push(`D_BLOCK=${blk.invocations}:${blk.launches}:${blk.stage_ms.import}:${blk.wall_ms}`);
out.push(`D_STAGES=${Object.keys(blk.stage_ms).join(',')}`);

// (d2) the EPOCH split: a re-used out-dir's older calls must not count against this dispatch.
const edir = path.join(process.cwd(), 'epoch');
recordInvocation(edir, 'glance');       // "last cycle"
const epochMs = Date.now() + 1;
await new Promise((r) => setTimeout(r, 5));
recordInvocation(edir, 'glance');       // this dispatch: the capture
recordInvocation(edir, 'glance');       // this dispatch: the --wait
const eb = budgetBlock(edir, { epochMs });
out.push(`D2_SCOPED=${eb.invocations}`);
out.push(`D2_TOTAL=${eb.invocations_total}`);
out.push(`D2_NO_EPOCH=${budgetBlock(edir, {}).invocations}`);

// (e) --out resolution mirrors the CLI's parser, and a bare --out falls back to the default dir.
out.push(`E_OUT=${resolveOutDir(['glance', '--out', 'somewhere', '--url', 'x']) === path.resolve('somewhere') ? 1 : 0}`);
out.push(`E_DEFAULT=${resolveOutDir(['glance', '--url', 'x']) === path.resolve(DEFAULT_OUT) ? 1 : 0}`);
out.push(`E_BARE=${resolveOutDir(['glance', '--out']) === path.resolve(DEFAULT_OUT) ? 1 : 0}`);

// (f) the manifest write is atomic (rename), so a poller never sees a torn file — and the scratch
// name is UNIQUE PER WRITER, since a shared `<file>.tmp` lets two processes interleave bytes into
// the very file the rename then publishes.
const mf = path.join(process.cwd(), 'm.json');
writeManifestAtomic(mf, { budget: { invocations: 1 } });
out.push(`F_WRITTEN=${JSON.parse(fs.readFileSync(mf, 'utf8')).budget.invocations}`);
out.push(`F_NO_TMP=${fs.readdirSync(path.dirname(mf)).some((f) => f.endsWith('.tmp')) ? 0 : 1}`);
const tmpNames = new Set();
for (let i = 0; i < 3; i++) {
  const seen = [];
  const realWrite = fs.writeFileSync;
  fs.writeFileSync = (f, ...rest) => { seen.push(String(f)); return realWrite(f, ...rest); };
  try { writeManifestAtomic(mf, { budget: { invocations: i } }); } finally { fs.writeFileSync = realWrite; }
  tmpNames.add(seen[0]);
}
out.push(`F_TMP_UNIQUE=${tmpNames.size}`);
out.push(`F_TMP_SHAPE=${[...tmpNames].every((f) => f.endsWith('.tmp') && f !== `${mf}.tmp`) ? 1 : 0}`);

console.log(out.join('\n'));
EOF

outA="$(cd "$tmp" && LIBDIR="$WINDIR/lib" node run-pure.mjs 2>&1)"; rcA=$?
if [ "$rcA" != 0 ]; then
  fail "pure harness ran clean" "node exited rc=$rcA out=[$outA]"
else
  getA() { printf '%s\n' "$outA" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "a) a coverage hole plans exactly one placeholder tile"        "$(getA A_TILES)"          "1"
  check "a) the tile is flagged as a placeholder, not a frame"         "$(getA A_FLAG)"           "1"
  check "a) it takes the missing cell's aspect at tile height"         "$(getA A_ASPECT)"         "640x400"
  check "a) its caption names the cell and says NOT CAPTURED"          "$(getA A_CAPTION)"        "home · 1280x800@1 · NOT CAPTURED"
  check "b) the tally counts captured tiles without the placeholders"  "$(getA B_TILES)"          "6"
  check "b) placeholders are counted separately, never dropped"        "$(getA B_PLACEHOLDERS)"   "2"
  check "b) the two counts partition every tile on the sheets"         "$(getA B_TOTAL_DIVIDED)"  "1"
  check "b) placeholders respect the same sheet size ceiling"          "$(getA B_WITHIN)"         "1"
  check "c) a placeholder renders with its own structural class"       "$(getA C_CLASS)"          "2"
  check "c) it carries NOT CAPTURED as ink AND as a caption"           "$(getA C_NOT_CAPTURED)"   "2:4"
  check "c) it carries the hole's reason into the image"               "$(getA C_REASON)"         "1"
  check "c) no frame is painted behind a placeholder"                  "$(getA C_NO_BACKGROUND)"  "1"
  check "d) invocations accumulate per out-dir across verbs"           "$(getA D_COUNT)"          "2"
  check "d) the ledger records which verbs were spent"                 "$(getA D_VERBS)"          "glance,crop"
  check "d) the ledger lives beside the manifest as budget.json"       "$(getA D_FILE)"           "1"
  check "d) a corrupt ledger reads as absent, never a crash"           "$(getA D_CORRUPT_READS_NULL)" "1"
  check "d) a corrupt ledger is rebuilt by the next invocation"        "$(getA D_CORRUPT_RECOVERS)"   "1"
  check "d) the manifest block carries counts and rounded timings"     "$(getA D_BLOCK)"          "1:1:10:151"
  check "d) every stage is stamped, so wall-clock is attributable"     "$(getA D_STAGES)"         "import,launch,capture,flush,compose"
  check "e) --out is resolved the same way the CLI parses it"          "$(getA E_OUT)"            "1"
  check "e) no --out keys the budget to the default out-dir"           "$(getA E_DEFAULT)"        "1"
  check "e) a bare --out falls back rather than keying on a flag"      "$(getA E_BARE)"           "1"
  check "d) the epoch scopes invocations to THIS dispatch"             "$(getA D2_SCOPED)"        "2"
  check "d) …while the cumulative total stays visible"                 "$(getA D2_TOTAL)"         "3"
  check "d) …and no epoch still means the whole ledger"                "$(getA D2_NO_EPOCH)"      "3"
  check "f) the manifest is written through a rename"                  "$(getA F_WRITTEN)"        "1"
  check "f) no temp file survives the write"                           "$(getA F_NO_TMP)"         "1"
  check "f) each writer renames from its OWN temp name"                "$(getA F_TMP_UNIQUE)"     "3"
  check "f) …never the shared <file>.tmp two writers would share"      "$(getA F_TMP_SHAPE)"      "1"
fi

# ---------------------------------------------------------------------------------------------
# Part B — the stdlib-only entries, proved in a tree with NO node_modules and NO lib/cli.mjs
# ---------------------------------------------------------------------------------------------
# The poison: lib/cli.mjs is the only module that imports playwright, and it is deliberately absent
# here. Any path that reaches it dies with ERR_MODULE_NOT_FOUND instead of behaving — so a passing
# check IS the proof that the path never imported it.
poison="$tmp/poison"
mkdir -p "$poison/scripts/lib"
cp "$DIR/probe.mjs" "$poison/scripts/probe.mjs"
cp "$DIR/lib/budget.mjs" "$DIR/lib/serve.mjs" "$DIR/lib/proc.mjs" "$poison/scripts/lib/"
cp "$SKILL_ROOT/package.json" "$poison/package.json"
WINPOISON="$(cd "$poison" && pwd -W 2>/dev/null || printf '%s' "$poison")"
mkdir -p "$poison/waitdir"

# (g) no manifest yet -> ONE machine line on stdout, exit 3, bounded by --timeout (never a hang).
wt_start="$(date +%s)"
g_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --timeout 700 2>"$tmp/wait.err")"; rcG=$?
wt_end="$(date +%s)"
check "g) a missing manifest resolves to the fallback signal"        "$g_stdout"  "WAIT_TIMEOUT"
check "g) …with the dedicated exit code the caller branches on"     "$rcG"       "3"
check "g) …bounded by --timeout, never a hang"                      "$([ "$((wt_end - wt_start))" -le 20 ] && printf bounded || printf hung)" "bounded"
check "g) the human explanation goes to stderr, not stdout"         "$(grep -c 'run the fused verb yourself' "$tmp/wait.err")" "1"

# (h) a complete manifest (the capture-ahead hit) -> the manifest path on stdout, exit 0.
# `generatedAt` is NOW: freshness is part of completeness since the epoch contract landed.
manifest_at() {
  printf '%s' "{\"verb\":\"glance\",\"generatedAt\":\"$1\",\"snapshots\":[],\"budget\":{\"invocations\":1,\"launches\":1,\"stage_ms\":{},\"wall_ms\":10}}" \
    > "$poison/waitdir/manifest.json"
}
now_iso="$(node -e "console.log(new Date().toISOString())")"
old_iso="$(node -e "console.log(new Date(Date.now()-3600000).toISOString())")"
manifest_at "$now_iso"
h_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --timeout 700 2>/dev/null)"; rcH=$?
check "h) a complete manifest resolves immediately, exit 0"         "$rcH"  "0"
check "h) …and stdout is the manifest path"                         "$(basename "$h_stdout")"  "manifest.json"

# (h3) THE EPOCH ARM. Same complete manifest, an hour old: a wait dispatched now must NOT resolve on
# it — that is a leaf reviewing the build its dispatch was meant to replace.
manifest_at "$old_iso"
h3_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --timeout 600 2>"$tmp/stale.err")"; rcH3=$?
check "h3) evidence older than the grace window is not accepted"     "$h3_stdout:$rcH3"  "WAIT_TIMEOUT:3"
check "h3) …and the timeout line names it as stale, not missing"     "$(grep -c 'older than the dispatch epoch' "$tmp/stale.err")" "1"

# --since pins the dispatch epoch explicitly: the same manifest passes an earlier one and fails a
# later one, which is the whole RMA case (a resume re-states a new epoch over the same out-dir).
manifest_at "$now_iso"
since_before="$(node -e "console.log(new Date(Date.now()-600000).toISOString())")"
since_after="$(node -e "console.log(new Date(Date.now()+600000).toISOString())")"
h4_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --since "$since_before" --timeout 700 2>/dev/null)"; rcH4=$?
check "h4) --since accepts a manifest generated after the epoch"     "$([ "$rcH4" = 0 ] && basename "$h4_stdout")"  "manifest.json"
h5_since="$(node -e "console.log(new Date(Date.now()+10000).toISOString())")"
h5_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --since "$h5_since" --timeout 600 2>/dev/null)"; rcH5=$?
check "h5) …and rejects one generated before it"                     "$h5_stdout:$rcH5"  "WAIT_TIMEOUT:3"
# A far-future epoch can never be satisfied by existing evidence: fail fast as usage, never a doomed
# full-timeout poll — the measured live failure mode was a hand-composed epoch minutes ahead of the
# clock, burning 180s per cycle on captures that took 2s.
h5b_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --since "$since_after" --timeout 600 2>"$tmp/future.err")"; rcH5b=$?
check "h5b) a far-future --since fails fast, never a doomed poll"    "$rcH5b"  "2"
check "h5b) …and the error names the future epoch"                   "$(grep -c 'in the future' "$tmp/future.err")" "1"
h6_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --since "not-a-date" --timeout 600 2>"$tmp/since.err")"; rcH6=$?
check "h6) a malformed --since is usage (2), never a silent grace"   "$rcH6"  "2"

# A manifest with NO generatedAt predates the contract: it cannot prove freshness, so it is stale.
printf '%s' '{"verb":"glance","snapshots":[],"budget":{"invocations":1,"launches":1,"stage_ms":{},"wall_ms":10}}' > "$poison/waitdir/manifest.json"
h7_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --timeout 600 2>/dev/null)"; rcH7=$?
check "h7) a manifest with no generatedAt cannot prove freshness"    "$h7_stdout:$rcH7"  "WAIT_TIMEOUT:3"

# (h2) an INCOMPLETE manifest (no budget block) is not a manifest yet — completeness is the budget
# stamp, which the fused verbs write last, so a half-finished capture can never resolve a wait.
printf '%s' '{"verb":"glance","snapshots":[]}' > "$poison/waitdir/manifest.json"
h2_stdout="$(cd "$poison/scripts" && node probe.mjs glance --wait "$WINPOISON/waitdir" --timeout 600 2>/dev/null)"; rcH2=$?
check "h2) a manifest without the budget stamp is not complete"      "$h2_stdout:$rcH2"  "WAIT_TIMEOUT:3"

# (i) the budget ledger tracked those waits — accounting reaches even the stdlib-only path.
check "i) --wait invocations are counted against the waited dir"     \
      "$(node -e "const l=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).invocations;console.log([...new Set(l.map(i=>i.verb))].join(',')+':'+(l.length>=8?'all':l.length))" "$poison/waitdir/budget.json" 2>&1)" \
      "glance:all"

# (j) the hidden serve child reaches lib/serve.mjs without the CLI (its own stdlib-only entry).
j_err="$(cd "$poison/scripts" && node probe.mjs __serve-child 2>&1 >/dev/null)"; rcJ=$?
check "j) __serve-child runs without importing the playwright CLI"   \
      "$([ "$rcJ" = 1 ] && printf rejected || printf "rc=$rcJ"):$(printf '%s' "$j_err" | grep -c 'serve-child. bad args')" "rejected:1"
check "j) …and never surfaces a module-resolution error"             "$(printf '%s' "$j_err" | grep -c 'ERR_MODULE_NOT_FOUND')" "0"

# ---------------------------------------------------------------------------------------------
# Part C — the real fused run (needs playwright + Edge)
# ---------------------------------------------------------------------------------------------
# A page taller than any cell's viewport: the glance's viewport clamp is only observable against a
# frame that WOULD have been longer, and a full-page capture here would be ~3x the viewport height.
cat > "$tmp/tall.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>tall</title><style>
  html, body { margin: 0; background: #fff; }
  body { font: 14px/1.5 system-ui, sans-serif; color: #1a1a1a; }
  .band { height: 300px; border-bottom: 1px solid #d8dee6; padding: 12px; }
</style></head><body>
  <div class="band">one</div><div class="band">two</div><div class="band">three</div>
  <div class="band">four</div><div class="band">five</div><div class="band">six</div>
</body></html>
EOF

cat > "$tmp/read-glance.mjs" <<'EOF'
import fs from 'fs';
const m = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
const frames = (m.snapshots || []).reduce((n, s) => n + s.entries.length, 0);
out.push(`VERB=${m.verb}`);
out.push(`GENERATED_AT=${Number.isFinite(Date.parse(m.generatedAt)) ? 1 : 0}`);
out.push(`LAUNCHES=${m.budget.launches}`);
out.push(`INVOCATIONS=${m.budget.invocations}`);
out.push(`INVOCATIONS_TOTAL=${m.budget.invocations_total}`);
out.push(`BELOW_FOLD=${(m.cellGeometry || []).map((c) => `${c.label}:${c.belowFoldPx || 0}`).join(',')}`);
out.push(`STAGES_NUMERIC=${['import', 'launch', 'capture', 'flush', 'compose'].every((k) => Number.isFinite(m.budget.stage_ms[k])) ? 1 : 0}`);
out.push(`WALL_POSITIVE=${m.budget.wall_ms > 0 ? 1 : 0}`);
out.push(`FRAMES=${frames}`);
out.push(`SHEETS=${m.sheets.length}`);
out.push(`SHEETS_ON_DISK=${m.sheets.every((f) => fs.existsSync(f)) ? 1 : 0}`);
out.push(`TILES=${m.sheetTally.tiles}`);
out.push(`PLACEHOLDERS=${m.sheetTally.placeholders}`);
out.push(`HOLES=${(m.coverageHoles || []).length}`);
out.push(`HOLE_KINDS=${[...new Set((m.coverageHoles || []).map((h) => h.kind))].sort().join(',')}`);
out.push(`HOLE_CELLS=${(m.coverageHoles || []).map((h) => h.cell).join(',')}`);
out.push(`HOLE_REASONED=${(m.coverageHoles || []).every((h) => h.reason && h.reason.length > 3) ? 1 : 0}`);
out.push(`DEADLINE_HIT=${m.deadline_hit}`);
out.push(`BLOCKED=${m.blocked ? m.blocked.reason : 'null'}`);
out.push(`CLAMPED=${m.viewportClamped}`);
// The clamp, measured off the real PNG: height must be the cell viewport x dsf, not the page height.
const dims = (f) => { const b = fs.readFileSync(f); return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) }; };
const geo = new Map((m.cellGeometry || []).map((c) => [c.label, c]));
out.push(`FRAME_DIMS=${(m.snapshots || []).flatMap((s) => s.entries.map((e) => {
  const g = geo.get(e.cell); const d = dims(e.file);
  return `${d.w}x${d.h}@${Math.round(g.width * g.dsf)}x${Math.round(g.height * g.dsf)}`;
})).join(',')}`);
console.log(out.join('\n'));
EOF

if [ ! -f "$SKILL_ROOT/node_modules/playwright/package.json" ]; then
  skip "k-n) the fused glance over a real page" \
       "node_modules/playwright not installed here — the fused capture path is UNVERIFIED by this run"
else
  g_out="$(node "$WINDIR/probe.mjs" glance --url "$WINTMP/tall.html" --matrix "800x600@1,801x601@1.5" \
      --out "$WINTMP/out-glance" 2>&1)"; rcK=$?
  if [ "$rcK" != 0 ] || [ ! -f "$tmp/out-glance/manifest.json" ]; then
    skip "k-n) the fused glance over a real page" \
         "capture did not run (rc=$rcK) — Edge channel unavailable? out=[$(printf '%s' "$g_out" | tail -2)]"
  else
    K="$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-glance/manifest.json" 2>&1)"
    getK() { printf '%s\n' "$K" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "k) capture + post-processing + compose run on ONE launch"  "$(getK LAUNCHES)"        "1"
    check "k) every matrix cell produced a frame"                     "$(getK FRAMES)"          "2"
    check "k) the sheets are composed in the same invocation"         "$(getK SHEETS)"          "1"
    check "k) every sheet the manifest names is on disk"              "$(getK SHEETS_ON_DISK)"  "1"
    check "k) a clean run has no holes and no placeholder tiles"      "$(getK HOLES):$(getK PLACEHOLDERS)" "0:0"
    check "k) the tally counts one tile per captured cell"            "$(getK TILES)"           "2"
    check "k) the run is not blocked and not deadline-cut"            "$(getK BLOCKED):$(getK DEADLINE_HIT)" "null:false"
    check "k) every stage is timed and the wall-clock is real"        "$(getK STAGES_NUMERIC):$(getK WALL_POSITIVE)" "1:1"
    check "k) captures are VIEWPORT-CLAMPED on a 1800px-tall page"    "$(getK FRAME_DIMS)"      "800x600@800x600,1202x902@1202x902"
    check "k) the manifest declares the clamp it applied"             "$(getK CLAMPED)"         "true"
    # The clamp's SCOPE: a 1800px page seen through a 600px viewport leaves the rest unseen, and the
    # per-cell number is what makes a leaf state that instead of implying it read the whole page.
    check "k) …and stamps the below-fold px it never saw, per cell"   "$(getK BELOW_FOLD)"      "cell0-800x600@1:1350,cell1-801x601@1.5:1349"
    check "k) the manifest carries a parseable generatedAt stamp"     "$(getK GENERATED_AT)"    "1"
    check "k) the glance exits 0"                                     "$rcK"                    "0"

    # ---- (l) a second run against the SAME out-dir: the ledger is cumulative -----------------
    node "$WINDIR/probe.mjs" glance --url "$WINTMP/tall.html" --matrix "800x600@1" \
        --out "$WINTMP/out-glance" >/dev/null 2>&1
    L="$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-glance/manifest.json" 2>&1)"
    getL() { printf '%s\n' "$L" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "l) the ledger total accumulates across runs on one out-dir" "$(getK INVOCATIONS_TOTAL):$(getL INVOCATIONS_TOTAL)" "1:2"
    check "l) …while each run's own count stays dispatch-scoped"      "$(getL INVOCATIONS)"     "1"
    check "l) the second run still launches exactly one browser"      "$(getL LAUNCHES)"        "1"

    # ---- (l2) a narrower re-run must not leave the wider run's sheets behind -----------------
    # Sheet names derive from the GROUP, so an orphan from a prior cycle survives by name — and the
    # leaf reads the dir it was handed, i.e. last cycle's evidence beside this one's.
    touch "$tmp/out-glance/mosaic-orphan-from-last-cycle.png"
    node "$WINDIR/probe.mjs" glance --url "$WINTMP/tall.html" --matrix "800x600@1" \
        --out "$WINTMP/out-glance" >/dev/null 2>&1
    check "l2) a fused run clears stale mosaics before composing"     \
          "$(ls "$tmp/out-glance" | grep -c 'mosaic-orphan-from-last-cycle')" "0"
    check "l2) …and its own sheets are all on disk"                   \
          "$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-glance/manifest.json" | grep -E '^SHEETS_ON_DISK=' | cut -d= -f2)" "1"

    # ---- (m) --deadline: partial flush, named holes, rendered placeholders, exit 0 -----------
    # 1ms is unreachable by construction: the browser launch alone outlives it, so every cell is a
    # deadline hole and the run must still produce a manifest (and, having captured nothing, must
    # say `blocked` rather than pretend to a verdict).
    d_out="$(node "$WINDIR/probe.mjs" glance --url "$WINTMP/tall.html" --matrix "800x600@1,801x601@1.5,375x667@2" \
        --deadline 1 --out "$WINTMP/out-dead0" 2>&1)"; rcM0=$?
    if [ ! -f "$tmp/out-dead0/manifest.json" ]; then
      fail "m) an unreachable deadline still writes a manifest" "no manifest.json — out=[$(printf '%s' "$d_out" | tail -2)]"
    else
      M0="$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-dead0/manifest.json" 2>&1)"
      getM0() { printf '%s\n' "$M0" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
      check "m) an unreachable deadline names every cell as a hole"   "$(getM0 HOLES):$(getM0 HOLE_KINDS)" "3:deadline"
      check "m) …with zero captures, that is BLOCKED, not a verdict"  "$(getM0 BLOCKED)"        "deadline"
      check "m) …and blocked exits 1 so the caller cannot miss it"    "$rcM0"                   "1"
    fi

    # A deadline that lands mid-sweep: some cells captured, the rest named — the partial-flush case
    # the doctrine actually exists for. Sized off the clean run's own capture stage so the timing is
    # derived, not guessed: half of what the full sweep needed.
    cap_ms="$(node -e "console.log(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).budget.stage_ms.capture)" "$tmp/out-glance/manifest.json" 2>/dev/null)"
    pre_ms="$(node -e "const b=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).budget;console.log(b.wall_ms-b.stage_ms.capture-b.stage_ms.flush-b.stage_ms.compose)" "$tmp/out-glance/manifest.json" 2>/dev/null)"
    dl=$(( ${pre_ms:-800} + ${cap_ms:-600} / 2 ))
    m_out="$(node "$WINDIR/probe.mjs" glance --url "$WINTMP/tall.html" --matrix "800x600@1,801x601@1.5,802x602@1,803x603@1,804x604@1,805x605@1" \
        --deadline "$dl" --out "$WINTMP/out-dead" 2>&1)"; rcM=$?
    if [ ! -f "$tmp/out-dead/manifest.json" ]; then
      fail "m) a mid-sweep deadline flushes what it captured" "no manifest.json — out=[$(printf '%s' "$m_out" | tail -2)]"
    else
      M="$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-dead/manifest.json" 2>&1)"
      getM() { printf '%s\n' "$M" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
      if [ "$(getM BLOCKED)" != "null" ]; then
        skip "m) a mid-sweep deadline keeps its frames" "this box captured nothing inside ${dl}ms — timing-derived bound was still too tight"
      else
        check "m) the deadline is recorded as hit"                    "$(getM DEADLINE_HIT)"    "true"
        check "m) captured cells survive the cut"                     "$([ "$(getM FRAMES)" -ge 1 ] && printf kept || printf lost)" "kept"
        check "m) unreached cells become named, reasoned holes"       "$([ "$(getM HOLES)" -ge 1 ] && printf named || printf silent):$(getM HOLE_REASONED)" "named:1"
        check "m) every hole is rendered as a placeholder tile"       "$([ "$(getM PLACEHOLDERS)" = "$(getM HOLES)" ] && printf all || printf "$(getM PLACEHOLDERS)of$(getM HOLES)")" "all"
        check "m) placeholders are excluded from the captured tally"  "$([ "$(getM TILES)" = "$(getM FRAMES)" ] && printf excluded || printf "$(getM TILES)vs$(getM FRAMES)")" "excluded"
        check "m) a partial flush is evidence, so it exits 0"         "$rcM"                    "0"
      fi
    fi

    # ---- (n) zero capture on a bad target = blocked, with the failure named ------------------
    n_out="$(node "$WINDIR/probe.mjs" glance --url "$WINTMP/does-not-exist.html" --matrix "800x600@1" \
        --out "$WINTMP/out-blocked" 2>&1)"; rcN=$?
    if [ ! -f "$tmp/out-blocked/manifest.json" ]; then
      fail "n) a failing target still writes a blocked manifest" "no manifest.json — out=[$(printf '%s' "$n_out" | tail -2)]"
    else
      N="$(node "$WINTMP/read-glance.mjs" "$WINTMP/out-blocked/manifest.json" 2>&1)"
      getN() { printf '%s\n' "$N" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
      check "n) a cell that fails becomes a hole, never a retry"      "$(getN HOLES):$(getN HOLE_KINDS)" "1:capture-error"
      check "n) zero captures is BLOCKED with the error named"        "$(getN BLOCKED)"         "capture-error"
      check "n) blocked exits 1"                                      "$rcN"                    "1"
      check "n) the hole carries the failure reason verbatim"         "$(getN HOLE_REASONED)"   "1"
    fi
  fi
fi

echo
[ "$fails" = 0 ] && echo "ALL fused-glance TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
