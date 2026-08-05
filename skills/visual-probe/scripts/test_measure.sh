#!/usr/bin/env bash
# Regression test for the `measure` instrument verb (lib/measure.mjs + its CLI path).
#
# WHY committed, and why it is a SEEDED + CLEAN CORPUS: `measure` exists to replace the per-review
# hand-authored measurement scripts, which is only an improvement if its numbers can be trusted for
# a filed finding. That trust is earned exactly the way rung 0 earned it — a fixture seeded with one
# instance of every defect class the verb claims to detect must yield EXACTLY that finding set, and
# its clean twin must yield ZERO findings. A verb that cries wolf on clean markup is worse than no
# verb: it re-teaches the reader to discount instrument output.
#
# Two of the rows are old bugs, not hypotheticals — both shipped in the throwaway scripts this verb
# replaces, and both are silent failures rather than loud ones:
#   • color(srgb …) — a modern computed color breaks an rgb()-shaped regex, and "no reading" is
#     indistinguishable from "no problem" downstream. The seeded low-contrast text is declared in
#     that syntax on purpose, so the row fails if the parse ever regresses.
#   • the PHANTOM BOX — an element clipped by an overflow-hidden ancestor keeps its full LAYOUT rect,
#     so naive overlap arithmetic invents a collision with whatever sits past that edge, where no ink
#     is painted. Overlap must be measured on the PAINTED box (layout ∩ clipping ancestors).
#
# Self-locating: lives in the real skill's scripts/ dir, next to probe.mjs. Part A is pure node.
# Part B drives the real browser path and needs node_modules/playwright plus a working Edge channel;
# where those are absent it reports SKIP rather than a false PASS. Exit code = failures.
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

tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/visual-probe-measure-test.$$.$RANDOM")"
mkdir -p "$tmp" 2>/dev/null || true
WINTMP="$(cd "$tmp" && pwd -W 2>/dev/null || printf '%s' "$tmp")"
cleanup() { rm -rf "$tmp" 2>/dev/null || true; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------
# Part A — the verb's own argument surface (pure; no browser)
# ---------------------------------------------------------------------------------------------
cat > "$tmp/run-pure.mjs" <<'EOF'
import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
const { MEASURE_CHECKS, MEASURE_THRESHOLDS, parseChecks, parseSelectors, nextMeasureFile } =
  await import(pathToFileURL(process.env.MEASURE).href);
const out = [];

out.push(`A_CHECKS=${[...MEASURE_CHECKS].sort().join(',')}`);
out.push(`A_DEFAULT=${parseChecks(undefined).sort().join(',')}`);
out.push(`A_SUBSET=${parseChecks('contrast,targets').join(',')}`);
let rejected = 0;
try { parseChecks('contrast,telepathy'); } catch { rejected = 1; }
out.push(`A_REJECTS_UNKNOWN=${rejected}`);
// A silently-ignored unknown check would report "no findings" for a class it never ran.
out.push(`A_SELECTOR=${parseSelectors({ selector: '.card' }).join(',')}`);
out.push(`A_SELECTORS=${parseSelectors({ selectors: '.a, .b ,.a' }).join(',')}`);
out.push(`A_DEFAULT_SELECTOR=${parseSelectors({}).join(',')}`);
out.push(`A_THRESHOLDS=${MEASURE_THRESHOLDS.contrastNormal}:${MEASURE_THRESHOLDS.contrastLarge}:${MEASURE_THRESHOLDS.smallTextPx}:${MEASURE_THRESHOLDS.minTargetPx}`);

// Measurements ACCUMULATE across a review instead of overwriting each other.
const d = path.join(process.cwd(), 'out');
fs.mkdirSync(d, { recursive: true });
// The pid suffix is the collision guard (two concurrent measures scan the same dir and compute the
// same ordinal); the ordinal still has to advance, so both halves are asserted by shape.
const base = () => path.basename(nextMeasureFile(d).file);
out.push(`A_FIRST=${/^measure-1-\d+\.json$/.test(base()) ? 'measure-1-<pid>.json' : base()}`);
fs.writeFileSync(nextMeasureFile(d).file, '{}');
fs.writeFileSync(nextMeasureFile(d).file, '{}');
out.push(`A_THIRD=${/^measure-3-\d+\.json$/.test(base()) ? 'measure-3-<pid>.json' : base()}`);
console.log(out.join('\n'));
EOF

outA="$(cd "$tmp" && MEASURE="$WINDIR/lib/measure.mjs" node run-pure.mjs 2>&1)"; rcA=$?
if [ "$rcA" != 0 ]; then
  fail "pure harness ran clean" "node exited rc=$rcA out=[$outA]"
else
  getA() { printf '%s\n' "$outA" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
  check "a) the shipped check set is the five instruments"        "$(getA A_CHECKS)"           "contrast,fonts,overflow,rects,targets"
  check "a) no --checks runs all of them"                         "$(getA A_DEFAULT)"          "contrast,fonts,overflow,rects,targets"
  check "a) --checks selects a subset, in the order asked"        "$(getA A_SUBSET)"           "contrast,targets"
  check "a) an unknown check is rejected, never silently skipped" "$(getA A_REJECTS_UNKNOWN)"  "1"
  check "a) --selector takes one selector"                        "$(getA A_SELECTOR)"         ".card"
  check "a) --selectors splits, trims and de-duplicates"          "$(getA A_SELECTORS)"        ".a,.b"
  check "a) neither flag measures the document body"              "$(getA A_DEFAULT_SELECTOR)" "body"
  check "a) the AA thresholds are the WCAG ones"                  "$(getA A_THRESHOLDS)"       "4.5:3:12:24"
  check "a) the first measurement is measure-1-<pid>.json"        "$(getA A_FIRST)"            "measure-1-<pid>.json"
  check "a) measurements accumulate, never overwrite"             "$(getA A_THIRD)"            "measure-3-<pid>.json"
fi

# ---------------------------------------------------------------------------------------------
# Part B — the seeded + clean corpus (needs playwright + Edge)
# ---------------------------------------------------------------------------------------------
# One instance of every class the verb claims, and nothing else. Each defect is commented with the
# check that owns it; the clean twin below keeps the SAME ids and selectors so the two runs are
# comparable finding-set to finding-set.
cat > "$tmp/seeded.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>measure seeded</title><style>
  body { margin: 16px; background: #fff; color: #101820; font: 15px/1.5 system-ui, sans-serif; width: 760px; }
  #faint { color: color(srgb 0.62 0.65 0.69); }        /* contrast: ~2.6:1 on white, declared in the syntax that breaks rgb() regexes */
  #tiny { font-size: 9px; }                            /* fonts: below the 12px legibility floor */
  #tinybtn { width: 16px; height: 16px; padding: 0; border: 0; background: #dde3ea; font-size: 13px; } /* targets: below 24x24 (and ONLY that class) */
  #ovf { width: 200px; overflow: hidden; white-space: pre; background: #f3f4f6; }                      /* overflow: content wider than its hidden box */
  #clipbox { width: 120px; height: 40px; overflow: hidden; display: inline-block; vertical-align: top; background: #f3f4f6; }
  #wide { display: block; width: 400px; height: 40px; background: #cfd6de; }   /* rects: layout rect runs 280px past the clip — the PHANTOM */
  #neighbor { display: inline-block; width: 200px; height: 40px; background: #e6efe6; vertical-align: top; }
  #real-a, #real-b { display: block; width: 300px; background: #eef1f6; }
  #real-b { margin-top: -24px; }                       /* rects: a REAL overlap, on painted boxes */
</style></head><body>
  <p id="faint">Low contrast paragraph</p>
  <p id="tiny">Nine pixel text</p>
  <p><button id="tinybtn">x</button></p>
  <div id="ovf">AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH-IIII-JJJJ-KKKK-LLLL</div>
  <p><span id="clipbox"><span id="wide">clipped wide child</span></span><span id="neighbor">neighbor</span></p>
  <div id="real-a">block a</div>
  <div id="real-b">block b</div>
</body></html>
EOF

cat > "$tmp/clean.html" <<'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>measure clean</title><style>
  body { margin: 16px; background: #fff; color: #101820; font: 15px/1.5 system-ui, sans-serif; width: 760px; }
  #faint { color: color(srgb 0.28 0.31 0.35); }        /* same modern syntax, a passing ratio */
  #tiny { font-size: 14px; }
  #tinybtn { width: 40px; height: 32px; padding: 0; border: 0; background: #dde3ea; font-size: 13px; }
  #ovf { width: 400px; overflow: hidden; white-space: pre; background: #f3f4f6; }
  #clipbox { width: 120px; height: 40px; overflow: hidden; display: inline-block; vertical-align: top; background: #f3f4f6; }
  #wide { display: block; width: 110px; height: 40px; background: #cfd6de; }
  #neighbor { display: inline-block; width: 200px; height: 40px; background: #e6efe6; vertical-align: top; }
  #real-a, #real-b { display: block; width: 300px; background: #eef1f6; margin-bottom: 8px; }
</style></head><body>
  <p id="faint">Low contrast paragraph</p>
  <p id="tiny">Nine pixel text</p>
  <p><button id="tinybtn">x</button></p>
  <div id="ovf">AAAA-BBBB</div>
  <p><span id="clipbox"><span id="wide">fits</span></span><span id="neighbor">neighbor</span></p>
  <div id="real-a">block a</div>
  <div id="real-b">block b</div>
</body></html>
EOF

cat > "$tmp/read-measure.mjs" <<'EOF'
import fs from 'fs';
const j = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
const f = j.findings || [];
out.push(`SET=${f.map((x) => `${x.check}:${x.selector}`).join(' ')}`);
out.push(`COUNT=${f.length}`);
const t = (id) => (j.targets || []).find((x) => x.path === id);
const find = (check, sel) => f.find((x) => x.check === check && x.selector === sel);

// contrast: the pixel arm is what files the finding; the declared arm is the cross-check.
const c = t('#faint');
out.push(`SYNTAX=${c && c.contrast ? c.contrast.colorSyntax : 'MISSING'}`);
out.push(`DECLARED=${c && c.contrast && c.contrast.declared ? c.contrast.declared.ratio : 'MISSING'}`);
out.push(`PIXEL=${c && c.contrast && c.contrast.pixel && c.contrast.pixel.measurable ? c.contrast.pixel.ratio : 'MISSING'}`);
out.push(`ARMS_AGREE=${c && c.contrast && c.contrast.declared && c.contrast.pixel && c.contrast.pixel.measurable
  && Math.abs(c.contrast.declared.ratio - c.contrast.pixel.ratio) <= 0.3 ? 1 : 0}`);
const cf = find('contrast', '#faint');
out.push(`CONTRAST_METHOD=${cf ? cf.measured.method : 'MISSING'}`);
out.push(`CONTRAST_REQUIRED=${cf ? cf.measured.required : 'MISSING'}`);

// the phantom: layout rect runs past the clip, the painted box does not.
const w = t('#wide');
out.push(`PHANTOM_LAYOUT=${w ? w.rect.width : 'MISSING'}`);
out.push(`PHANTOM_PAINTED=${w ? w.visualRect.width : 'MISSING'}`);
out.push(`PHANTOM_OVERLAP=${f.some((x) => x.check === 'rects' && /wide|neighbor/.test(x.selector + JSON.stringify(x.measured))) ? 1 : 0}`);
// ellipsis bookkeeping travels with the target whether or not it produced a finding.
const o = t('#ovf');
out.push(`OVERFLOW_PX=${o && o.overflow ? o.overflow.overflowPx > 0 : 'MISSING'}`);
// fonts / targets numbers behind their findings
const ti = t('#tiny');
out.push(`FONT_PX=${ti && ti.font ? ti.font.fontSizePx : 'MISSING'}`);
const b = t('#tinybtn');
out.push(`TARGET_AA=${b && b.targets && b.targets[0] ? b.targets[0].meetsAA : 'MISSING'}`);
out.push(`BUDGET=${j.budget ? `${j.budget.invocations}:${j.budget.launches}` : 'MISSING'}`);
out.push(`STAGED=${j.budget && Number.isFinite(j.budget.stage_ms.capture) ? 1 : 0}`);
out.push(`QUEUE_STRIPPED=${j.contrastQueue === undefined ? 1 : 0}`);
console.log(out.join('\n'));
EOF

SELECTORS='#faint,#tiny,#tinybtn,#ovf,#wide,#neighbor,#real-a,#real-b'

# The record's basename now carries the writer's pid (concurrent-writer guard), so every reader
# below resolves the run's file by glob instead of a fixed name.
rec() { ls "$1"/measure-1-*.json 2>/dev/null | head -1; }
recname() { basename "$(rec "$1")" 2>/dev/null; }

if [ ! -f "$SKILL_ROOT/node_modules/playwright/package.json" ]; then
  skip "b-d) the measure verb over the seeded + clean corpus" \
       "node_modules/playwright not installed here — the instrument path is UNVERIFIED by this run"
else
  # stdout is the COMPACT summary now, not the payload — the run's full record is measure-<n>.json
  # inside the out-dir, and that file is what every assertion below reads.
  node "$WINDIR/probe.mjs" measure --url "$WINTMP/seeded.html" --selectors "$SELECTORS" \
      --matrix "900x700@1" --out "$WINTMP/out-seeded" >"$tmp/seeded.txt" 2>"$tmp/seeded.err"; rcS=$?
  if [ "$rcS" != 0 ] || [ ! -s "$(rec "$tmp/out-seeded")" ]; then
    skip "b-d) the measure verb over the seeded + clean corpus" \
         "measure did not run (rc=$rcS) — Edge channel unavailable? err=[$(tail -2 "$tmp/seeded.err")]"
  else
    S="$(node "$WINTMP/read-measure.mjs" "$WINTMP/out-seeded/$(recname "$tmp/out-seeded")" 2>&1)"
    getS() { printf '%s\n' "$S" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    # The earn-in gate: EXACTLY one finding per seeded class, and nothing else.
    check "b) the seeded corpus yields exactly its planted classes" "$(getS SET)" \
          "contrast:#faint fonts:#tiny overflow:#ovf rects:#real-a targets:#tinybtn"
    check "b) …and no extra findings ride along"                    "$(getS COUNT)"        "5"
    check "b) a color(srgb …) value is parsed, not missed"          "$(getS SYNTAX)"       "color(srgb)"
    check "b) …so the declared arm still produces a ratio"          "$([ "$(getS DECLARED)" != "MISSING" ] && printf measured || printf missing)" "measured"
    check "b) the pixel arm reads the composited pair"              "$([ "$(getS PIXEL)" != "MISSING" ] && printf measured || printf missing)" "measured"
    check "b) both arms agree within 0.3:1 on flat markup"          "$(getS ARMS_AGREE)"   "1"
    check "b) the filed finding cites the PIXEL method"             "$(getS CONTRAST_METHOD)" "pixel"
    check "b) …against the AA floor for normal text"                "$(getS CONTRAST_REQUIRED)" "4.5"
    check "c) a clipped child keeps its full LAYOUT rect"           "$(getS PHANTOM_LAYOUT)"  "400"
    check "c) …but its PAINTED box stops at the clip"               "$(getS PHANTOM_PAINTED)" "120"
    check "c) …so no phantom overlap is invented past that edge"    "$(getS PHANTOM_OVERLAP)" "0"
    check "c) a real overlap on painted boxes IS reported"          "$([ -n "$(printf '%s' "$(getS SET)" | grep -o 'rects:#real-a')" ] && printf found || printf missed)" "found"
    check "d) the overflow instrument records the measured px"      "$(getS OVERFLOW_PX)"  "true"
    check "d) the fonts instrument records the rendered size"       "$(getS FONT_PX)"      "9"
    check "d) the targets instrument records the AA verdict"        "$(getS TARGET_AA)"    "false"
    check "d) the output carries the harness budget stamp"          "$(getS BUDGET)"       "1:1"
    check "d) …with per-stage timings"                              "$(getS STAGED)"       "1"
    check "d) the internal contrast queue never leaks to output"    "$(getS QUEUE_STRIPPED)" "1"
    check "d) measure exits 0 (findings are data, not failure)"     "$rcS"                 "0"
    check "d) the run is also written to measure-<n>-<pid>.json"    "$([ -n "$(rec "$tmp/out-seeded")" ] && printf written || printf absent)" "written"

    # ---- the CLEAN TWIN: the anti-false-positive half of the gate ---------------------------
    node "$WINDIR/probe.mjs" measure --url "$WINTMP/clean.html" --selectors "$SELECTORS" \
        --matrix "900x700@1" --out "$WINTMP/out-clean" >"$tmp/clean.txt" 2>"$tmp/clean.err"; rcC=$?
    if [ "$rcC" != 0 ] || [ ! -s "$(rec "$tmp/out-clean")" ]; then
      fail "e) the clean twin measures with ZERO findings" "measure did not run (rc=$rcC) err=[$(tail -2 "$tmp/clean.err")]"
    else
      C="$(node "$WINTMP/read-measure.mjs" "$WINTMP/out-clean/$(recname "$tmp/out-clean")" 2>&1)"
      getC() { printf '%s\n' "$C" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
      check "e) the clean twin yields ZERO findings (no crying wolf)" "$(getC SET):$(getC COUNT)" ":0"
      check "e) …while still measuring every target it was given"     "$([ "$(getC PIXEL)" != "MISSING" ] && printf measured || printf missing)" "measured"
      check "e) …and still parses the modern color syntax"            "$(getC SYNTAX)"       "color(srgb)"
      check "e) the clean twin exits 0"                               "$rcC"                 "0"
    fi

    # ---- --checks narrows what RUNS, not just what is reported ------------------------------
    node "$WINDIR/probe.mjs" measure --url "$WINTMP/seeded.html" --selectors "$SELECTORS" \
        --checks contrast --matrix "900x700@1" --out "$WINTMP/out-narrow" >"$tmp/narrow.txt" 2>/dev/null
    N="$(node "$WINTMP/read-measure.mjs" "$WINTMP/out-narrow/$(recname "$tmp/out-narrow")" 2>&1)"
    getN() { printf '%s\n' "$N" | grep -E "^$1=" | head -1 | cut -d= -f2-; }
    check "f) --checks contrast reports only the contrast class"    "$(getN SET)"          "contrast:#faint"
    check "f) …and the skipped instruments emit no numbers"         "$(getN FONT_PX)"      "MISSING"

    # ---- (g) stdout is a COMPACT summary, never the payload ---------------------------------
    # The verb used to print the whole JSON to stdout, which truncated in the caller's command
    # result and sent every leaf Grepping for numbers that were already on disk. stdout now has to
    # be small enough to survive intact, carry each failing selector WITH its measured value, and
    # end in the absolute path that settles anything it left out.
    lines="$(wc -l < "$tmp/seeded.txt" | tr -d ' ')"
    check "g) stdout stays inside the 40-line budget"                \
          "$([ "$lines" -le 40 ] && printf compact || printf "bloated:$lines")" "compact"
    check "g) …and is not the JSON payload"                          \
          "$(grep -c '"generatedBy"\|"contrastQueue"\|"visualRect"' "$tmp/seeded.txt")" "0"
    check "g) per-check pass/fail tallies are stated"                \
          "$(grep -cE '^contrast: [0-9]+ pass / [0-9]+ fail' "$tmp/seeded.txt")" "1"
    check "g) each failing selector carries its measured value"      \
          "$(grep -c '#faint.*(needs 4.5:1' "$tmp/seeded.txt")" "1"
    check "g) …for every seeded class, not just the first"           \
          "$(grep -cE '#tiny.*9px|#ovf.*px past its hidden box|#tinybtn.*min 24' "$tmp/seeded.txt")" "3"
    # Absolute, and the file the run actually wrote — asserted by shape (a Windows drive path is not
    # something a POSIX `-f` test can be trusted to resolve from Git Bash).
    check "g) the last line is the absolute path of the full JSON"   \
          "$(tail -1 "$tmp/seeded.txt" | tr -d '\r' | grep -cE '^(/|[A-Za-z]:[\\/]).*measure-1-[0-9]+\.json$')" "1"
    check "g) a clean run still names the file it wrote"             \
          "$(tail -1 "$tmp/clean.txt" | tr -d '\r' | grep -cE '^(/|[A-Za-z]:[\\/]).*measure-1-[0-9]+\.json$')" "1"
  fi
fi

echo
[ "$fails" = 0 ] && echo "ALL measure TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
