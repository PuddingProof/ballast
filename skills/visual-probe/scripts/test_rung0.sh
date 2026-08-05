#!/usr/bin/env bash
# Regression test for the rung-0 geometry assertions (lib/assertions.mjs + its capture-time seam).
#
# WHY committed: rung 0 is the ladder's free rung — six defect classes that are pure arithmetic over
# layout and computed style, emitted as data before any image is read. Two properties have to hold
# or it is worse than nothing. (1) Each class must actually FIRE on the defect it owns and stay
# QUIET on clean markup: a check that cries wolf makes every run escalate, and the misalignment
# check especially has no other owner (a blind-reader calibration found no reader detects a 1-3px
# row mismatch from a full-page read at any tier). (2) The findings must stay SHADOW-LOGGED — data
# only. A run over a page seeded with every defect class must still exit 0 with pass=true; the day
# that changes, every consumer of this harness starts failing on advisory output.
#
# It also pins the frozen suppression schema: matching is by check-id + selector, a matched finding
# is KEPT and flagged (never dropped, so it stays auditable), a suppression written for a container
# covers findings on its descendants, and a suppression that matches nothing is reported as unused —
# a stale suppression is its own defect class.
#
# And it pins the documented off-switch. `--no-rung0` is offered to callers who want the capture
# without the assertions; a switch that is quietly ignored — a renamed opt key, an inverted or
# hardcoded RUNG0_ON — is indistinguishable from a working one from inside any single run, so the
# assertion has to be the CONTRAST: the same seeded fixture fires without the flag and is silent
# with it, while still capturing its frame.
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Part A (aggregation and
# suppression bookkeeping) is pure node. Part B drives the REAL capture path end to end and needs
# node_modules/playwright plus a working Edge channel; where those are absent it reports SKIP rather
# than a false PASS — the page-side checks are then UNVERIFIED, so run this on a ready skill dir.
# Exit code = failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Windows (Git Bash) node needs a Windows-style path for pathToFileURL / file:// targets; `pwd -W`
# yields it, and we fall back to the POSIX path where it doesn't exist (Linux/macOS).
WINDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || printf '%s' "$DIR")"
SKILL_ROOT="$(cd "$DIR/.." && pwd)"
ASSERTIONS="$WINDIR/lib/assertions.mjs"

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

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-rung0-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
WINTMP="$(cd "$tmp" && pwd -W 2>/dev/null || printf '%s' "$tmp")"
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------
# Part A — aggregation + suppression bookkeeping (pure; no browser)
# ---------------------------------------------------------------------------------------------
cat > "$tmp/run-agg.mjs" <<'EOF'
import fs from 'fs';
import { pathToFileURL } from 'url';
const { aggregateRung0, readSuppressionsFile, RUNG0_CHECKS } = await import(pathToFileURL(process.env.ASSERTIONS).href);
const out = [];

const finding = (over) => ({ check: 'overflow', selector: '#box', description: 'content overflows its hidden box horizontally by 40px', measured: { overflowPx: 40 }, suppressed: false, ...over });

// The same defect in two cells is ONE finding that names both cells — not two findings.
const agg = aggregateRung0([
  { label: 'home', cell: 'baseline-1.0', result: { findings: [finding()], matchedSuppressions: [], invalidSuppressions: [] } },
  { label: 'home', cell: 'hidpi-3.0', result: { findings: [finding()], matchedSuppressions: [], invalidSuppressions: [] } },
], []);
out.push(`A_COUNT=${agg.findings.length}`);
out.push(`A_CELLS=${agg.findings[0].cells.join(',')}`);

// Present-and-empty on a clean run: the field is a contract, not an optional extra.
const empty = aggregateRung0([{ label: 'home', cell: 'baseline-1.0', result: { findings: [], matchedSuppressions: [], invalidSuppressions: [] } }], []);
out.push(`A_EMPTY=${Array.isArray(empty.findings) ? empty.findings.length : 'MISSING'}`);

// Unused-suppression bookkeeping: matched by key, unmatched reported, unparseable named as such.
const supps = [
  { assert: 'overflow', selector: '#box', reason: 'intended scroll region' },
  { assert: 'contrast', selector: '#gone', reason: 'stale' },
  { assert: 'overlap', selector: ':::bad', reason: 'unparseable' },
];
const used = aggregateRung0([{ label: 'home', cell: 'baseline-1.0',
  result: { findings: [finding({ suppressed: true, suppressionReason: 'intended scroll region', suppressedBy: '#box' })],
    matchedSuppressions: [{ assert: 'overflow', selector: '#box' }],
    invalidSuppressions: [{ assert: 'overlap', selector: ':::bad' }] } }], supps);
out.push(`A_KEPT=${used.findings.length}:${used.findings[0].suppressed}`);
out.push(`A_UNUSED=${used.unusedSuppressions.map((u) => u.assert + ':' + u.selector).join(',')}`);
out.push(`A_WHY_INVALID=${/parse/.test(used.unusedSuppressions.find((u) => u.selector === ':::bad').why) ? 1 : 0}`);

// The file loader accepts a bare array AND a state manifest carrying a top-level suppressions key.
fs.writeFileSync('bare.json', JSON.stringify(supps));
fs.writeFileSync('manifest.json', JSON.stringify({ routes: {}, suppressions: supps }));
out.push(`A_FILE_BARE=${readSuppressionsFile(fs, 'bare.json').length}`);
out.push(`A_FILE_MANIFEST=${readSuppressionsFile(fs, 'manifest.json').length}`);
fs.writeFileSync('bad.json', JSON.stringify({ nope: 1 }));
let rejected = 0;
try { readSuppressionsFile(fs, 'bad.json'); } catch { rejected = 1; }
out.push(`A_FILE_REJECTS=${rejected}`);
out.push(`A_CHECK_IDS=${[...RUNG0_CHECKS].sort().join(',')}`);
console.log(out.join('\n'));
EOF

outA="$(cd "$tmp" && ASSERTIONS="$ASSERTIONS" node run-agg.mjs 2>&1)"; rcA=$?
if [ "$rcA" != 0 ]; then
  fail "aggregation harness ran clean" "node exited rc=$rcA out=[$outA]"
else
  getA() { printf '%s\n' "$outA" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "a) one defect across two cells aggregates to one finding"   "$(getA A_COUNT)"        "1"
  check "a) the finding names every cell it appeared in"             "$(getA A_CELLS)"        "home__baseline-1.0,home__hidpi-3.0"
  check "a) a clean run yields an empty findings array, not absence" "$(getA A_EMPTY)"        "0"
  check "a) a suppressed finding is kept and flagged, never dropped" "$(getA A_KEPT)"         "1:true"
  check "a) only the unmatched suppressions are reported unused"     "$(getA A_UNUSED)"       "contrast:#gone,overlap::::bad"
  check "a) an unparseable suppression selector says so"             "$(getA A_WHY_INVALID)"  "1"
  check "a) suppressions load from a bare array file"                "$(getA A_FILE_BARE)"    "3"
  check "a) suppressions load from a state manifest's own key"       "$(getA A_FILE_MANIFEST)" "3"
  check "a) a malformed suppressions file is rejected, not ignored"  "$(getA A_FILE_REJECTS)" "1"
  check "a) the shipped check-id set is the six rung-0 classes"      "$(getA A_CHECK_IDS)"    "broken-image,contrast,misalignment,offscreen,overflow,overlap"
fi

# ---------------------------------------------------------------------------------------------
# Part B — the real capture path over seeded vs clean fixtures (needs playwright + Edge)
# ---------------------------------------------------------------------------------------------
# Every seeded defect is deliberate; the comments name the class each one owns.
cat > "$tmp/seeded.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>seeded</title><style>
  body { margin: 16px; background: #fff; color: #1a1a1a; font: 14px/1.4 system-ui, sans-serif; width: 700px; }
  .row { display: flex; gap: 12px; align-items: flex-start; }
  .btn { height: 40px; padding: 0 16px; background: #e9edf2; border-radius: 6px; }
  .btn.ghost { position: relative; top: 1px; }            /* (f) misalignment: 1px off an equal-height row sibling */
  .stack .a, .stack .b { background: #dde3ea; padding: 8px; }
  .stack .b { margin-top: -20px; }                        /* (a) overlap: sibling rects intersect */
  #overflow-box { width: 240px; overflow: hidden; white-space: pre; background: #f3f4f6; }
  #clip-box { width: 100px; height: 40px; overflow: hidden; background: #f3f4f6; }
  #clipped-child { width: 400px; height: 30px; background: #cfd6de; }
  .faint { color: #9a9fa6; }                              /* (c) contrast: ~2.6:1 on white */
</style></head><body>
  <div class="row"><div class="btn">Start free trial</div><div class="btn ghost">Book a demo</div></div>
  <div class="stack"><div class="a">first block</div><div class="b">second block</div></div>
  <div id="overflow-box">AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH-IIII-JJJJ-KKKK-LLLL-MMMM-NNNN-OOOO</div>
  <div id="clip-box"><div id="clipped-child">clipped</div></div>       <!-- (e) offscreen: trapped past a hidden edge -->
  <p class="faint">Signal, not noise.</p>
  <img src="does-not-exist.png" alt="partner badge" width="96" height="28">  <!-- (d) broken asset -->
</body></html>
EOF

cat > "$tmp/clean.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>clean</title><style>
  body { margin: 16px; background: #fff; color: #1a1a1a; font: 14px/1.4 system-ui, sans-serif; width: 700px; }
  .row { display: flex; gap: 12px; align-items: flex-start; }
  .btn { height: 40px; padding: 0 16px; background: #e9edf2; border-radius: 6px; }
  .card { background: #f3f4f6; padding: 8px; margin-bottom: 8px; color: #333a42; }
</style></head><body>
  <div class="row"><div class="btn">Start free trial</div><div class="btn">Book a demo</div></div>
  <div class="card">first block</div>
  <div class="card">second block</div>
  <p>Signal, not noise.</p>
  <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" alt="ok" width="96" height="28">
</body></html>
EOF

cat > "$tmp/supp.json" <<'EOF'
[
  { "assert": "overflow", "selector": "#overflow-box", "reason": "intended single-line code strip" },
  { "assert": "offscreen", "selector": "#clip-box", "reason": "intended clipped preview — suppression on the container covers its children" },
  { "assert": "contrast", "selector": "#does-not-exist", "reason": "stale rule, kept to prove unused-reporting" }
]
EOF

cat > "$tmp/read-manifest.mjs" <<'EOF'
import fs from 'fs';
const m = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const r = m.rung0;
const out = [];
out.push(`FIELD=${Array.isArray(r) ? 1 : 0}`);
out.push(`PASS=${m.pass}`);
out.push(`COUNT=${r.length}`);
out.push(`CHECKS=${[...new Set(r.map((f) => f.check))].sort().join(',')}`);
out.push(`CELLS=${[...new Set(r.flatMap((f) => f.cells))].join(',')}`);
const byId = (sel, check) => r.find((f) => f.selector === sel && f.check === check);
const ovf = byId('#overflow-box', 'overflow');
out.push(`SUPPRESSED=${ovf ? `${ovf.suppressed}:${ovf.suppressionReason}` : 'MISSING'}`);
const clipped = byId('#clipped-child', 'offscreen');
out.push(`ANCESTOR_SUPPRESSED=${clipped ? clipped.suppressed : 'MISSING'}`);
const sibling = byId('#clip-box', 'overflow');
out.push(`SIBLING_LIVE=${sibling ? (sibling.suppressed ? 0 : 1) : 'MISSING'}`);
out.push(`UNUSED=${(m.rung0UnusedSuppressions || []).map((u) => u.assert + ':' + u.selector).join(',')}`);
out.push(`UNUSED_FIELD=${Array.isArray(m.rung0UnusedSuppressions) ? 1 : 0}`);
out.push(`GEOMETRY=${(m.cellGeometry || []).map((c) => `${c.width}x${c.height}@${c.dsf}`).join(',')}`);
out.push(`FRAMES=${(m.snapshots || []).reduce((n, s) => n + (s.entries || []).length, 0)}`);
console.log(out.join('\n'));
EOF

if [ ! -f "$SKILL_ROOT/node_modules/playwright/package.json" ]; then
  skip "b) rung-0 fires on seeded markup and stays quiet on clean markup" \
       "node_modules/playwright not installed here — the page-side checks are UNVERIFIED by this run"
else
  seeded_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/seeded.html" --matrix "800x600@1" \
      --out "$WINTMP/out-seeded" --suppressions "$WINTMP/supp.json" 2>&1)"; rcS=$?
  if [ "$rcS" != 0 ] || [ ! -f "$tmp/out-seeded/manifest.json" ]; then
    skip "b) rung-0 fires on seeded markup" "capture did not run (rc=$rcS) — Edge channel unavailable? out=[$(printf '%s' "$seeded_out" | tail -2)]"
  else
    S="$(node "$WINTMP/read-manifest.mjs" "$WINTMP/out-seeded/manifest.json" 2>&1)"
    getS() { printf '%s\n' "$S" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "b) manifest carries a rung0 array"                            "$(getS FIELD)"    "1"
    check "b) every rung-0 class fires on the page seeded with it"       "$(getS CHECKS)"   "broken-image,contrast,misalignment,offscreen,overflow,overlap"
    check "b) findings are SHADOW-LOGGED: pass survives a seeded page"   "$(getS PASS)"     "true"
    check "b) a seeded page still exits 0 (advisory, never gating)"      "$rcS"             "0"
    check "b) findings name the state__cell frame stem"                  "$(getS CELLS)"    "shot__cell0-800x600@1"
    check "b) a matched suppression flags the finding with its reason"   "$(getS SUPPRESSED)" "true:intended single-line code strip"
    check "b) a container's suppression covers a finding on its child"   "$(getS ANCESTOR_SUPPRESSED)" "true"
    check "b) suppression is selective — the sibling finding stays live" "$(getS SIBLING_LIVE)" "1"
    check "b) a suppression that matched nothing is reported unused"     "$(getS UNUSED)"   "contrast:#does-not-exist"
    check "b) cell viewport geometry is recorded for the compositor"     "$(getS GEOMETRY)" "800x600@1"

    # ---- --no-rung0 over the SAME seeded fixture: the off-switch, asserted as a contrast ------
    nr_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/seeded.html" --matrix "800x600@1" \
        --out "$WINTMP/out-norung0" --no-rung0 2>&1)"; rcN=$?
    if [ "$rcN" != 0 ] || [ ! -f "$tmp/out-norung0/manifest.json" ]; then
      skip "b) --no-rung0 turns the geometry assertions off" \
           "capture did not run (rc=$rcN) — out=[$(printf '%s' "$nr_out" | tail -2)]"
    else
      N="$(node "$WINTMP/read-manifest.mjs" "$WINTMP/out-norung0/manifest.json" 2>&1)"
      getN() { printf '%s\n' "$N" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
      # left of the colon: the same page WITH the rung on (above) — the flag's effect is the delta.
      check "b) --no-rung0 silences findings the same page fires without it" \
            "$([ "$(getS COUNT)" -gt 0 ] && printf fires || printf silent):$(getN COUNT)" "fires:0"
      check "b) --no-rung0 skips the assertions, not the capture"          "$(getN FRAMES)"   "1"
      check "b) the rung0 array is still present when the rung is off"     "$(getN FIELD)"    "1"
      check "b) a run with the rung off still passes and exits 0"          "$(getN PASS):$rcN" "true:0"
      check "b) the rung-0 stderr summary goes quiet with the rung"        \
            "$([ "$(printf '%s' "$seeded_out" | grep -c 'rung0:')" -gt 0 ] && printf logged || printf none):$(printf '%s' "$nr_out" | grep -c 'rung0:')" \
            "logged:0"
    fi
  fi

  clean_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/clean.html" --matrix "800x600@1" \
      --out "$WINTMP/out-clean" 2>&1)"; rcC=$?
  if [ "$rcC" != 0 ] || [ ! -f "$tmp/out-clean/manifest.json" ]; then
    skip "b) rung-0 stays quiet on clean markup" "capture did not run (rc=$rcC) — Edge channel unavailable? out=[$(printf '%s' "$clean_out" | tail -2)]"
  else
    C="$(node "$WINTMP/read-manifest.mjs" "$WINTMP/out-clean/manifest.json" 2>&1)"
    getC() { printf '%s\n' "$C" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "b) clean markup produces ZERO findings (no crying wolf)"      "$(getC COUNT)"       "0"
    check "b) the rung0 array is present even when empty"                "$(getC FIELD)"       "1"
    check "b) the unused-suppressions array is present even when empty"  "$(getC UNUSED_FIELD)" "1"
    check "b) a clean run passes"                                        "$(getC PASS)"        "true"
  fi

  # ---- (c) the suppression budget is separate from the defect budget -------------------------
  # A declared exception can repeat far past the per-check cap (a grid of intentionally stacked
  # badges). If suppressed findings were charged to the same budget, they would evict genuine ones
  # into the rolled-up cap bucket — where a finding loses its selector and description, which is
  # the one thing the cap exists NOT to do. 30 suppressed overlaps > CAP (25), plus one real one.
  {
    printf '%s' '<!DOCTYPE html><html><head><meta charset="utf-8"><title>budget</title><style>
      body { margin: 16px; background: #fff; color: #1a1a1a; font: 14px/1.4 system-ui, sans-serif; width: 700px; }
      .pair > * { background: #dde3ea; padding: 6px; }
      .pair > *:last-child { margin-top: -18px; }
      #real .a, #real .b { background: #e9edf2; padding: 6px; }
      #real .b { margin-top: -18px; }
    </style></head><body><div id="grid">'
    i=0; while [ "$i" -lt 30 ]; do printf '<div class="pair"><div>a%s</div><div>b%s</div></div>' "$i" "$i"; i=$((i + 1)); done
    printf '%s' '</div><div id="real"><div class="a">genuine</div><div class="b">defect</div></div></body></html>'
  } > "$tmp/budget.html"

  cat > "$tmp/budget-supp.json" <<'EOF'
[ { "assert": "overlap", "selector": "#grid", "reason": "intentional stacked grid" } ]
EOF

  cat > "$tmp/read-budget.mjs" <<'EOF'
import fs from 'fs';
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).rung0;
const real = r.filter((f) => !f.suppressed && !f.capped && f.check === 'overlap');
console.log(`REAL_ENUMERATED=${real.length ? 1 : 0}`);
console.log(`REAL_NAMED=${real.some((f) => /#real/.test(f.selector)) ? 1 : 0}`);
console.log(`SUPPRESSED_CAPPED=${r.some((f) => f.capped && f.suppressed) ? 1 : 0}`);
EOF

# The FUSED verbs slim the manifest's rung0 array (`shot` above keeps it whole). 20 DISTINCT
# low-contrast paragraphs, each a shade lighter than the last: distinct selectors so they aggregate
# to 20 rows rather than one, and distinct ratios so the ranking has something to rank.
{
  printf '%s' '<!DOCTYPE html><html><head><meta charset="utf-8"><title>slim</title><style>
    body { margin: 16px; background: #fff; font: 14px/1.4 system-ui, sans-serif; width: 700px; }
  </style></head><body>'
  i=0; while [ "$i" -lt 20 ]; do
    g=$((140 + i))
    printf '<p id="c%s" style="color: rgb(%s, %s, %s)">faint line %s</p>' "$i" "$g" "$g" "$g" "$i"
    i=$((i + 1))
  done
  printf '%s' '</body></html>'
} > "$tmp/slim.html"

cat > "$tmp/read-slim.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
const m = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const perKind = m.rung0.reduce((a, f) => (a[f.check] = (a[f.check] || 0) + 1, a), {});
console.log(`KIND_MAX=${Math.max(0, ...Object.values(perKind))}`);
console.log(`OVERFLOW_NAMED=${(m.rung0Overflow || {}).contrast > 0 ? 1 : 0}`);
// Ranked by magnitude: the WORST ratio on the page must be one the manifest inlines, and the best
// of the 20 must be one it trimmed — a cap that kept an arbitrary slice would fail this pair.
const ratios = m.rung0.filter((f) => f.check === 'contrast').map((f) => f.measured.ratio);
console.log(`WORST_INLINED=${Math.min(...ratios) < 2.9 ? 1 : 0}`);
console.log(`BEST_TRIMMED=${Math.max(...ratios) < 3.1 ? 1 : 0}`);
const trimmed = Object.values(m.rung0Overflow || {}).reduce((a, b) => a + b, 0);
const side = path.join(path.dirname(process.argv[2]), m.rung0File || 'rung0.json');
const full = fs.existsSync(side) ? JSON.parse(fs.readFileSync(side, 'utf8')).findings : [];
console.log(`SIDECAR_COMPLETE=${full.length === m.rung0.length + trimmed ? 1 : 0}`);
console.log(`SIDECAR_TOTAL=${full.length === m.rung0Total ? 1 : 0}`);
console.log(`SELECTOR_MAX=${Math.max(0, ...m.rung0.map((f) => f.selector.length))}`);
EOF

  budget_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/budget.html" --matrix "800x600@1" \
      --out "$WINTMP/out-budget" --suppressions "$WINTMP/budget-supp.json" 2>&1)"; rcB=$?
  if [ "$rcB" != 0 ] || [ ! -f "$tmp/out-budget/manifest.json" ]; then
    skip "c) suppressed findings never evict a genuine one" "capture did not run (rc=$rcB) — out=[$(printf '%s' "$budget_out" | tail -2)]"
  else
    B="$(node "$WINTMP/read-budget.mjs" "$WINTMP/out-budget/manifest.json" 2>&1)"
    getB() { printf '%s\n' "$B" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "c) the genuine overlap survives 30 suppressed ones"           "$(getB REAL_ENUMERATED)"   "1"
    check "c) it keeps its selector (not rolled into the cap bucket)"    "$(getB REAL_NAMED)"        "1"
    check "c) the suppressed overflow is reported, never dropped"        "$(getB SUPPRESSED_CAPPED)" "1"
  fi

  # ---- (c2) the FUSED manifest slims the finding set -------------------------------------------
  # A 1600-line manifest costs a second offset-Read on every leaf run, so the fused verbs inline a
  # ranked HEAD per check kind and write the complete list to rung0.json. The cap must never be what
  # hides evidence: it keeps the WORST instances, names what it trimmed, and the trimmed rows stay
  # readable on disk.
  slim_out="$(node "$WINDIR/probe.mjs" glance --url "$WINTMP/slim.html" --matrix "800x600@1" \
      --out "$WINTMP/out-slim" 2>&1)"; rcSl=$?
  if [ "$rcSl" != 0 ] || [ ! -f "$tmp/out-slim/manifest.json" ]; then
    skip "c2) the fused manifest inlines a bounded, named head" "glance did not run (rc=$rcSl) — out=[$(printf '%s' "$slim_out" | tail -2)]"
  else
    L="$(node "$WINTMP/read-slim.mjs" "$WINTMP/out-slim/manifest.json" 2>&1)"
    getL() { printf '%s\n' "$L" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "c2) the manifest inlines at most 12 rows per check kind"      \
          "$([ "$(getL KIND_MAX)" -le 12 ] && printf bounded || printf "over:$(getL KIND_MAX)")" "bounded"
    check "c2) …and names the kind it trimmed"                           "$(getL OVERFLOW_NAMED)"   "1"
    check "c2) the worst instance on the page is one it kept"            "$(getL WORST_INLINED)"    "1"
    check "c2) …and the mildest is one it trimmed (ranked, not sliced)"  "$(getL BEST_TRIMMED)"     "1"
    check "c2) rung0.json carries every row the manifest left out"       "$(getL SIDECAR_COMPLETE)" "1"
    check "c2) …and agrees with the manifest's stated total"             "$(getL SIDECAR_TOTAL)"    "1"
    check "c2) inlined selectors are truncated to 120 chars"             \
          "$([ "$(getL SELECTOR_MAX)" -le 120 ] && printf bounded || printf "over:$(getL SELECTOR_MAX)")" "bounded"
  fi

  # ---- (d) rung-0 noise classes: an unresolvable background, and layered SVG paint -----------
  # Both were measured false-positive floods, and both are SKIPS rather than guesses:
  #   • a page whose face is painted by a canvas has NO resolvable CSS background behind its text.
  #     Assuming white there reported 90+ phantom AA failures per run on a dark UI. The same page
  #     carries one genuine low-contrast element on a painted solid, so the fix is asserted as a
  #     CONTRAST — the check must go quiet on the unresolvable stack and still fire on the solid one.
  #   • boxes under one <svg> overlap BY CONSTRUCTION (axis, plot and label layers); scanning them
  #     produced 25+ spurious pairs per run on chart internals alone.
  cat > "$tmp/noise.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>noise</title><style>
  html, body { margin: 0; color: #e6e9ef; font: 14px/1.4 system-ui, sans-serif; }  /* no background anywhere up the chain */
  #face { position: fixed; left: 0; top: 0; width: 100%; height: 100%; z-index: -1; }
  .layer { position: relative; padding: 24px; }
  #solid { background: #ffffff; color: #9a9fa6; padding: 8px; }   /* ~2.6:1 on a PAINTED solid — must still fire */
</style></head><body>
  <canvas id="face" width="800" height="600"></canvas>
  <div class="layer">
    <p id="darktext">Light text over a canvas-painted face</p>
    <svg id="chart" width="300" height="120"><g>
      <rect x="10" y="10" width="200" height="60" fill="#4a6fa5"></rect>
      <rect x="60" y="30" width="200" height="60" fill="#d08a3e"></rect>
    </g></svg>
  </div>
  <div id="solid">Low contrast on a solid background</div>
  <script>
    var g = document.getElementById('face').getContext('2d');
    g.fillStyle = '#12161d'; g.fillRect(0, 0, 800, 600);
  </script>
</body></html>
EOF

  cat > "$tmp/read-noise.mjs" <<'EOF'
import fs from 'fs';
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).rung0.filter((f) => !f.suppressed);
const of = (c) => r.filter((f) => f.check === c);
console.log(`CONTRAST_SET=${of('contrast').map((f) => f.selector).join(',')}`);
console.log(`OVERLAP_COUNT=${of('overlap').length}`);
EOF

  noise_out="$(node "$WINDIR/probe.mjs" shot "$WINTMP/noise.html" --matrix "800x600@1" \
      --out "$WINTMP/out-noise" 2>&1)"; rcNo=$?
  if [ "$rcNo" != 0 ] || [ ! -f "$tmp/out-noise/manifest.json" ]; then
    skip "d) rung-0 stays quiet on unresolvable backgrounds and SVG layers" \
         "capture did not run (rc=$rcNo) — out=[$(printf '%s' "$noise_out" | tail -2)]"
  else
    D="$(node "$WINTMP/read-noise.mjs" "$WINTMP/out-noise/manifest.json" 2>&1)"
    getD() { printf '%s\n' "$D" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "d) a canvas-faced page yields NO contrast findings…"          \
          "$(printf '%s' "$(getD CONTRAST_SET)" | grep -c 'darktext')" "0"
    check "d) …while the genuine solid-bg failure still fires"           "$(getD CONTRAST_SET)"  "#solid"
    check "d) two rects under one <svg> are not an overlap"              "$(getD OVERLAP_COUNT)" "0"
  fi
fi

echo
[ "$fails" = 0 ] && echo "ALL rung-0 TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
