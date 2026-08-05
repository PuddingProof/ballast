// FUSED verbs — `glance`, `review-capture`, `crop`.
//
// WHY THEY EXIST: a dispatched visual leaf's cost is its TURN COUNT, and every separate harness
// invocation is a turn (measured: 70–91% of a leaf's wall-clock is inter-turn model latency, and the
// harness itself is 8–15%). The classic flow spends three invocations and three browser launches on
// what is one question — preflight, capture, compose. These verbs collapse that into ONE process
// with ONE browser launch: the capture loop, the magnify/hash post-processing and the contact-sheet
// re-screenshot all run on the same browser handle.
//
// The single-launch rule is hard-gated on NOT being in CDP attach mode. That separation is not
// stylistic: post-processing on a CDP-attached app would drive the user's live browser. Fused verbs
// therefore refuse `--cdp` outright rather than silently degrading to two launches.
//
// FAILURE DOCTRINE (design §3.1, no-retry): a cell that fails is a NAMED HOLE carrying its error
// string — never a second attempt, because a retry spiral costs more wall-clock than the evidence is
// worth. `--deadline MS` hard-stops the cell loop and flushes what exists, with every unreached cell
// named as a hole. `blocked` is reserved for ZERO captures: partial evidence is still evidence, and
// a leaf must be able to tell "I saw 4 of 6 cells" from "I saw nothing".
//
// Holes are RENDERED, not merely recorded: each one becomes a visually-distinct placeholder tile in
// the sheet the model actually reads (lib/mosaic.mjs), and placeholder tiles are never counted as
// captured cells anywhere in this file's tallies.

import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { cellObj, parseMatrix } from './matrix.mjs';
import { guardUrl } from './urlguard.mjs';
import { buildEntries } from './capture.mjs';
import { makeHelper } from './helpers.mjs';
import { aggregateRung0 } from './assertions.mjs';
import { sourcesFromManifest, placeholderSource, renderSheets } from './compose.mjs';
import { budgetBlock, writeManifestAtomic } from './budget.mjs';
import { MEASURE_PAGE_FN, MEASURE_THRESHOLDS, pixelPair, nextMeasureFile, parseChecks, parseSelectors } from './measure.mjs';

// How many rung-0 rows per check kind the MANIFEST inlines. The complete set always lands in
// `<out>/rung0.json`; this is purely about keeping the reading surface inside one Read.
export const RUNG0_MANIFEST_CAP = 12;

const MANIFEST_NOTE =
  'READ THIS FIRST, then the sheet PNGs in `sheets` — they are the reading surface; the native ' +
  'frames are for targeted re-reads only. `coverageHoles` lists cells this run could not capture ' +
  '(each is also RENDERED into the sheet as a labeled placeholder tile — placeholder tiles are NOT ' +
  'captured cells and never count as coverage). `rung0` carries deterministic geometry findings ' +
  '(overlap / overflow / contrast / broken-image / offscreen / misalignment) per `<label>__<cell>`; ' +
  'entries with `suppressed:true` were declared intended by the project. `rung0` here is the TOP ' +
  RUNG0_MANIFEST_CAP + ' per check kind — ' +
  '`rung0Overflow` names any kind that was trimmed, and the complete list is written beside this ' +
  'file as `rung0.json` (read it only when the trimmed head is not enough). `budget` is harness-stamped: ' +
  '`invocations` is THIS dispatch\'s probe calls (`invocations_total` is the out-dir\'s whole ledger) — ' +
  'echo the dispatch-scoped one in the verdict. `viewportClamped:true` means each frame stops at the ' +
  'viewport, so a cell\'s `belowFoldPx` (in `cellGeometry`) is page height this run never saw — scope, ' +
  'not a defect; name it. `deadline_hit:true` means the run was cut short and the remaining cells are ' +
  'named holes. `cropHoles` are failed crops: they make THAT question indeterminate and never demote ' +
  'capture coverage. `blocked` is non-null only when NOTHING was captured.';

// ---------------------------------------------------------------------------------------------
// shared helpers
// ---------------------------------------------------------------------------------------------

// A route label for a URL: the last meaningful path segment (+ query), sanitized to the same
// character class the frame filenames use. It becomes the snapshot label, so it shows up in the
// sheet captions — a reader should be able to name the route from the tile alone.
export function routeLabel(u) {
  let s = 'route';
  try {
    const p = new URL(u);
    const seg = decodeURIComponent(p.pathname).split('/').filter(Boolean).pop() || 'root';
    s = seg.replace(/\.[a-z0-9]+$/i, '') || 'root';
    if (p.search) s += `-${p.search.slice(1)}`;
  } catch { /* not a parseable URL — keep the fallback */ }
  return (s.replace(/[^\w.@-]+/g, '-').replace(/^-+|-+$/g, '') || 'route').slice(0, 48);
}

// ---- manifest slimming -----------------------------------------------------------------------
// One structural defect repeats across hundreds of elements, and inlining every row is what pushed
// manifests past what a single Read returns (measured: 1600-line manifests cost a second offset-Read
// on every run). The manifest keeps a ranked HEAD per check kind; the full list is written beside it
// and the trim is named in `rung0Overflow`, so nothing disappears silently.

const SELECTOR_MAX = 120;

// Middle-ellipsis: a deep selector's HEAD (where the page region lives) and its TAIL (the element
// itself) both carry meaning — trimming from either end alone throws away half the identification.
export function middleEllipsis(s, max = SELECTOR_MAX) {
  const str = String(s == null ? '' : s);
  if (str.length <= max) return str;
  const head = Math.ceil((max - 1) / 2);
  return `${str.slice(0, head)}…${str.slice(str.length - (max - 1 - head))}`;
}

// Ranking magnitude within a kind — how bad this instance is, in that check's own units.
function rung0Magnitude(f) {
  const m = f.measured || {};
  switch (f.check) {
    case 'contrast': return Number.isFinite(m.ratio) ? (m.required || 4.5) - m.ratio : 0;
    case 'overlap': return (m.overlapWidth || 0) * (m.overlapHeight || 0);
    case 'overflow': return m.overflowPx || 0;
    case 'offscreen': return m.clippedPx || m.outPx || 0;
    case 'misalignment': return m.deltaPx || 0;
    default: return 1;   // broken-image (and any future class): one severity, nothing to rank by
  }
}

// Rolled-up cap rows rank FIRST: each stands for findings that were never enumerated at all, so
// dropping one loses strictly more information than dropping any single row it summarizes. Then
// live findings by magnitude, then suppressed ones (already declared intended).
export function slimRung0(findings, cap = RUNG0_MANIFEST_CAP) {
  const byKind = new Map();
  for (const f of findings) {
    if (!byKind.has(f.check)) byKind.set(f.check, []);
    byKind.get(f.check).push(f);
  }
  const kept = [];
  const overflow = {};
  for (const [kind, list] of byKind) {
    list.sort((a, b) => (b.capped ? 1 : 0) - (a.capped ? 1 : 0)
      || (a.suppressed ? 1 : 0) - (b.suppressed ? 1 : 0)
      || rung0Magnitude(b) - rung0Magnitude(a));
    if (list.length > cap) overflow[kind] = list.length - cap;
    for (const f of list.slice(0, cap)) kept.push({ ...f, selector: middleEllipsis(f.selector) });
  }
  return { kept, overflow };
}

function resolveTargets(ctx) {
  const { opts, positional } = ctx;
  const raw = [];
  if (positional[0]) raw.push(positional[0]);
  if (opts.url && opts.url !== true) raw.push(String(opts.url));
  if (opts.urls && opts.urls !== true) raw.push(...String(opts.urls).split(',').map((s) => s.trim()).filter(Boolean));
  if (!raw.length) throw new Error('no target — pass --url <base> (and optionally --urls <u1,u2,…>)');

  const seen = new Set();
  const targets = [];
  for (const u of raw) {
    const url = guardUrl(ctx.resolveTarget(u), opts['allow-remote']);
    if (seen.has(url)) continue;
    seen.add(url);
    // Re-check after each bump: a single suffix attempt can collide again (three routes whose stems
    // sanitize alike), and two targets sharing a label merge into one snapshot in the manifest.
    const stem = routeLabel(url);
    let label = stem;
    for (let n = 2; targets.some((t) => t.label === label); n++) label = `${stem}-${n}`;
    targets.push({ label, url });
  }
  return targets;
}

// The DSF fidelity triad, added at the NATIVE breakpoint only (the matrix's first cell). Non-integer
// scales are the load-bearing ones — a clean 2.0× is exactly the scale that hides boundary-tie blit
// defects (lib/matrix.mjs) — so the triad is 1.0 / 1.5 / 2.0 of one viewport, not a second full sweep.
function withDsfTriad(cells, factors = [1.5, 2]) {
  const base = cells[0];
  if (!base) return cells;
  const out = [...cells];
  for (const dsf of factors) {
    if (out.some((c) => c.width === base.width && c.height === base.height && c.dsf === dsf)) continue;
    out.push({ label: `${base.label}@${dsf}`, width: base.width, height: base.height, dsf });
  }
  return out;
}

// The bundled state scenario, loaded only when --states asks for it (its import pulls project-shaped
// config handling that a plain URL sweep has no use for).
async function loadStatesScenario(ctx) {
  const manifest = path.resolve(String(ctx.opts.states));
  if (!fs.existsSync(manifest)) throw new Error(`--states manifest not found: ${manifest}`);
  process.env.VISUAL_STATES = manifest;
  if (ctx.opts['skip-drive-hooks']) process.env.VISUAL_STATES_SKIP_DRIVE_HOOKS = '1';
  const mod = await import(new URL('../../scenarios/states-from-manifest.mjs', import.meta.url).href);
  if (typeof mod.default !== 'function') throw new Error('states scenario must default-export async (page, h) => {}');
  return mod;
}

// `--epoch ISO` — the dispatch window this run belongs to (the gate stamps the same value into the
// leaf's brief). Absent, the run IS its own epoch: process start.
function parseEpoch(opts, t0) {
  if (opts.epoch === undefined) return t0;
  const parsed = opts.epoch === true ? NaN : Date.parse(String(opts.epoch));
  if (!Number.isFinite(parsed)) {
    throw new Error(`--epoch expects an ISO timestamp (got ${opts.epoch === true ? '(no value)' : opts.epoch})`);
  }
  return parsed;
}

function parseDeadline(opts) {
  if (opts.deadline === undefined) return null;
  const n = Number(opts.deadline);
  if (opts.deadline === true || !Number.isFinite(n) || n <= 0) {
    throw new Error(`--deadline expects a positive integer of milliseconds (got ${opts.deadline === true ? '(no value)' : opts.deadline})`);
  }
  return Math.round(n);
}

// ---------------------------------------------------------------------------------------------
// the fused capture → post-process → compose → manifest pipeline
// ---------------------------------------------------------------------------------------------
async function fusedRun(ctx, cfg) {
  const { opts, OUT, EDGE, TIMEOUT, MAGNIFY, THRESHOLD, SETTLE, RUNG0_ON, flagSuppressions, log, timing } = ctx;
  const t0 = timing.t0 || Date.now();
  const stage = { import: timing.importMs || 0, launch: 0, capture: 0, flush: 0, compose: 0 };

  // The one launch is only sound off-CDP; refuse rather than silently splitting into two browsers.
  if (opts.cdp) throw new Error(`${cfg.verb} does not support --cdp (the single-browser fusion would drive the attached app); use 'run' for CDP work`);

  fs.mkdirSync(OUT, { recursive: true });
  const targets = resolveTargets(ctx);
  let cells = parseMatrix(opts.matrix).map(cellObj);
  if (cfg.dsfTriad && !opts['no-dsf-triad']) cells = withDsfTriad(cells);
  const deadlineMs = parseDeadline(opts);
  const deadlineAt = deadlineMs ? t0 + deadlineMs : null;
  const epochMs = parseEpoch(opts, t0);

  const scenarioModule = opts.states && opts.states !== true ? await loadStatesScenario(ctx) : null;
  const scenario = scenarioModule ? scenarioModule.default : null;
  // A state sweep enumerates its OWN routes out of the manifest, so running it once per --urls
  // target would re-shoot every state under a colliding label. The sweep belongs to the base --url;
  // extra targets are captured as plain cells beside it.
  if (scenario && targets.length > 1) {
    log(`--states sweeps the base --url only (${targets[0].url}); the ${targets.length - 1} extra --urls target(s) are captured as plain cells`);
  }

  const snapshots = [];
  const asserts = [];
  const rung0Records = [];
  const holes = [];            // {label, cell, kind, reason} — never absorbed, always rendered
  const consoleFindings = [];
  const collector = {
    addSnapshot: (s) => snapshots.push(s),
    addAssert: (a) => asserts.push(a),
    addRung0: (r) => rung0Records.push(r),
  };
  const suppressionsNow = () => [
    ...flagSuppressions,
    ...(Array.isArray(scenarioModule?.suppressions) ? scenarioModule.suppressions : []),
  ];
  const rung0 = { enabled: RUNG0_ON, suppressions: suppressionsNow };

  // One job = one (cell × target) capture unit. Flattening first is what makes the deadline honest:
  // the unreached tail is a known list of named holes, not "whatever the loop didn't get to".
  const jobs = [];
  for (const cell of cells) for (const target of targets) jobs.push({ cell, target });

  const tLaunch = Date.now();
  const browser = await chromium.launch(EDGE);
  stage.launch = Date.now() - tLaunch;
  let launches = 1;
  let deadlineHit = false;

  try {
    const tCap = Date.now();
    for (let i = 0; i < jobs.length; i++) {
      const { cell, target } = jobs[i];
      if (deadlineAt && Date.now() >= deadlineAt) {
        deadlineHit = true;
        for (const rest of jobs.slice(i)) {
          holes.push({ label: rest.target.label, cell: rest.cell.label, kind: 'deadline',
            reason: `--deadline ${deadlineMs}ms reached before this cell was captured` });
        }
        break;
      }
      const budgetLeft = deadlineAt ? Math.max(1000, deadlineAt - Date.now()) : TIMEOUT;
      const pageCtx = await browser.newContext({
        viewport: { width: cell.width, height: cell.height },
        deviceScaleFactor: cell.dsf,
      });
      try {
        const page = await pageCtx.newPage();
        page.setDefaultTimeout(Math.min(TIMEOUT, budgetLeft));
        // Console/pageerror/requestfailed listeners live on the PER-CELL page, so a finding is tied
        // to the cell that produced it — and so a review no longer needs a hand-authored scenario
        // just to notice that the app is throwing.
        if (cfg.consoleCapture) attachConsole(page, target.label, cell.label, consoleFindings);
        const h = makeHelper({
          page, cell, url: target.url, allowRemote: opts['allow-remote'],
          collector, rung0, settle: SETTLE, viewportOnly: cfg.viewportOnly,
        });
        if (scenario && target === targets[0]) await scenario(page, h);
        else {
          await page.goto(target.url, { waitUntil: 'load' });
          await h.dwell();
          await h.snapshot(target.label, {});
        }
      } catch (e) {
        // NO RETRY: the failure becomes evidence, immediately.
        holes.push({ label: target.label, cell: cell.label, kind: 'capture-error', reason: e.message });
      } finally { await pageCtx.close(); }
    }
    stage.capture = Date.now() - tCap;

    // A scenario declares states it could not force by exporting `coverageHoles` — same channel,
    // same rendering, so an unforceable state and a timed-out cell read alike to the leaf.
    for (const c of (Array.isArray(scenarioModule?.coverageHoles) ? scenarioModule.coverageHoles : [])) {
      holes.push({ label: c.label, cell: c.cell || '', kind: c.kind || 'unforced', reason: c.reason || c.detail || '' });
    }

    const tFlush = Date.now();
    const built = await buildEntries({
      browser, snapshots, outDir: OUT, magnifyFactor: MAGNIFY, threshold: THRESHOLD,
      baselineLabel: opts.baseline || cells[0]?.label, log,
    });
    stage.flush = Date.now() - tFlush;

    const { findings: rung0Findings, unusedSuppressions } = aggregateRung0(rung0Records, suppressionsNow());
    const { kept: rung0Kept, overflow: rung0Overflow } = slimRung0(rung0Findings);
    // The sidecar is written whenever there is anything to carry — a leaf that needs a trimmed row's
    // full selector or measured block must never have to re-run the capture to get it.
    let rung0File = null;
    if (rung0Findings.length) {
      rung0File = 'rung0.json';
      fs.writeFileSync(path.join(OUT, rung0File),
        JSON.stringify({ findings: rung0Findings, unusedSuppressions }, null, 2));
    }

    // Per-cell below-the-fold page height, measured at each shutter. A viewport-clamped rung only
    // ever saw the first screenful, and a page taller than the cell is SCOPE the reader must state —
    // silence here reads as "the whole page looked fine". Max across the cell's targets/states: the
    // tallest state is the one that bounds what went unseen.
    const belowFold = new Map();
    for (const s of snapshots) {
      if (!(s.belowFoldPx > 0)) continue;
      belowFold.set(s.cell.label, Math.max(belowFold.get(s.cell.label) || 0, s.belowFoldPx));
    }

    const manifest = {
      verb: cfg.verb, target: targets[0].url, targets: targets.map((t) => ({ label: t.label, url: t.url })),
      generatedBy: 'visual-probe',
      // The capture's START, not its write: `--wait --since <epoch>` accepts a manifest only when the
      // capture behind it BEGAN inside the dispatch window. Stamping the write instead would accept a
      // sweep that started against the pre-fix build and merely finished after the dispatch.
      generatedAt: new Date(t0).toISOString(),
      cells: cells.map((c) => c.label),
      cellGeometry: cells.map((c) => ({
        label: c.label, width: c.width, height: c.height, dsf: c.dsf,
        ...(belowFold.get(c.label) ? { belowFoldPx: belowFold.get(c.label) } : {}),
      })),
      viewportClamped: !!cfg.viewportOnly,
      // The state manifest's own scope disclaimer, carried through verbatim so a leaf's blind-spot
      // line states what the project says a green run does NOT attest.
      ...(scenarioModule?.meta?.verifies ? { verifies: scenarioModule.meta.verifies } : {}),
      magnify: MAGNIFY, divergenceThreshold: THRESHOLD,
      snapshots: built, asserts,
      coverageHoles: holes,
      console: consoleFindings, consoleOverflow: consoleFindings.overflow || 0,
      rung0: rung0Kept, rung0Overflow, rung0File, rung0Total: rung0Findings.length,
      rung0UnusedSuppressions: unusedSuppressions,
      sheets: [], sheetTally: { sheets: 0, tiles: 0, placeholders: 0 },
      deadline_hit: deadlineHit,
      blocked: null,
      pass: asserts.every((a) => a.pass),
      note: MANIFEST_NOTE,
    };

    if (!built.length) {
      // Zero captures — the one condition that earns `blocked`.
      manifest.blocked = {
        reason: holes.length ? holes[0].kind : 'no-capture',
        detail: holes.length ? holes[0].reason : 'no cell produced a frame',
      };
      manifest.pass = false;
      manifest.budget = budgetBlock(OUT, { launches, stageMs: stage, wallMs: Date.now() - t0, epochMs });
      const file = writeManifestAtomic(path.join(OUT, 'manifest.json'), manifest);
      log(`BLOCKED — ${manifest.blocked.reason}: ${manifest.blocked.detail}`);
      console.log(file);
      process.exitCode = 1;
      return;
    }

    // ---- compose on the SAME browser: no second spawn, no second launch --------------------
    // A prior cycle's sheets are deleted first. Sheet names are derived from the GROUP, so a run over
    // a narrower scope leaves the wider run's `mosaic-*.png` on disk — and the leaf reads whatever
    // `sheets[]` and the dir contain, i.e. last cycle's evidence beside this one's.
    const tCompose = Date.now();
    try {
      for (const f of fs.readdirSync(OUT)) {
        if (/^mosaic.*\.png$/i.test(f)) fs.unlinkSync(path.join(OUT, f));
      }
    } catch (e) { log('could not clear previous sheets —', e.message); }
    const groupOf = cfg.groupOf || null;
    const groupRe = opts.group && opts.group !== true ? new RegExp(opts.group) : null;
    const { sources, skipped } = sourcesFromManifest(manifest, OUT, { groupRe, groupOf });
    for (const hole of holes) {
      const geo = cells.find((c) => c.label === hole.cell);
      sources.push(placeholderSource({
        name: `${hole.label} · ${hole.cell || hole.kind}`, reason: `${hole.kind}: ${hole.reason}`.slice(0, 160),
        group: groupRe ? 'ungrouped' : (groupOf ? groupOf(hole.label, hole.cell) : ''),
        vw: geo?.width || 0, vh: geo?.height || 0,
      }));
    }
    let composed = { sheets: [], tally: { sheets: 0, tiles: 0, placeholders: 0 } };
    try {
      composed = await renderSheets({
        browser, dir: OUT, sources,
        tileH: +(opts['tile-height'] || 400), maxSide: +(opts['max-side'] || 1568),
        cropContent: !!opts['crop-content'], timeout: TIMEOUT, log,
      });
    } catch (e) {
      // Compose is the reading surface, not the evidence: a sheet failure must not throw away the
      // frames that were already captured.
      log('compose failed —', e.message, '— native frames are still on disk');
      holes.push({ label: 'sheets', cell: '', kind: 'compose-error', reason: e.message });
    }
    stage.compose = Date.now() - tCompose;

    manifest.sheets = composed.sheets.map((s) => s.file);
    manifest.sheetDetail = composed.sheets;
    manifest.sheetTally = composed.tally;
    if (skipped.length) manifest.composeSkipped = skipped;
    manifest.budget = budgetBlock(OUT, { launches, stageMs: stage, wallMs: Date.now() - t0, epochMs });

    const file = writeManifestAtomic(path.join(OUT, 'manifest.json'), manifest);
    summarize(log, manifest, RUNG0_ON, rung0Findings);
    console.log(file);
    process.exitCode = 0;
  } finally {
    await browser.close();
  }
}

// Console entry text is PAGE-CONTROLLED: an app (or anything it loaded) can log megabytes, and every
// byte lands in the reviewer's context. Both bounds are deliberate — 300 chars keeps the message
// class and its first frame legible, 50 entries keeps the channel a signal rather than a flood, and
// the overflow count says how much was dropped so silence is never mistaken for a quiet page.
export const CONSOLE_TEXT_MAX = 300;
export const CONSOLE_MAX_ENTRIES = 50;

function attachConsole(page, label, cell, sink) {
  const push = (kind, text, url) => {
    if (sink.length >= CONSOLE_MAX_ENTRIES) { sink.overflow = (sink.overflow || 0) + 1; return; }
    sink.push({ label, cell, kind, text: String(text || '').slice(0, CONSOLE_TEXT_MAX), url: String(url || '').slice(0, CONSOLE_TEXT_MAX) });
  };
  page.on('console', (m) => {
    const t = m.type();
    if (t !== 'error' && t !== 'warning') return;
    let loc = '';
    try { loc = m.location()?.url || ''; } catch { /* no location */ }
    push(t === 'error' ? 'console-error' : 'console-warning', m.text(), loc);
  });
  page.on('pageerror', (e) => push('pageerror', e?.message || String(e), ''));
  page.on('requestfailed', (r) => {
    let why = '';
    try { why = r.failure()?.errorText || 'request failed'; } catch { why = 'request failed'; }
    push('requestfailed', `${why}: ${r.url()}`, r.url());
  });
}

// `allRung0` is the UNSLIMMED set: the stderr tally must report what the run found, not what the
// manifest chose to inline.
function summarize(log, m, rung0On, allRung0 = m.rung0) {
  const frames = m.snapshots.reduce((n, s) => n + s.entries.length, 0);
  log(`cells=${m.cells.length} frames=${frames} sheets=${m.sheets.length} tiles=${m.sheetTally.tiles} holes=${m.coverageHoles.length}${m.deadline_hit ? ' DEADLINE-HIT' : ''}`);
  if (m.coverageHoles.length) log(`COVERAGE HOLES (${m.coverageHoles.length}) — rendered as placeholder tiles:`,
    m.coverageHoles.map((c) => `${c.label}/${c.cell || '-'} [${c.kind}]`).join(', '));
  if (rung0On) {
    const live = allRung0.filter((f) => !f.suppressed);
    const trimmed = Object.entries(m.rung0Overflow || {}).map(([k, n]) => `${k} +${n}`).join(', ');
    log(`rung0: ${live.length} finding(s)${allRung0.length - live.length ? ` (+${allRung0.length - live.length} suppressed)` : ''} — advisory, does not affect pass`
      + (trimmed ? ` · manifest inlines the top ${RUNG0_MANIFEST_CAP}/kind (${trimmed} in rung0.json)` : ''));
  }
  if (m.console.length) log(`console: ${m.console.length} entr(ies)${m.consoleOverflow ? ` (+${m.consoleOverflow} beyond the cap)` : ''} — see manifest.console`);
  const b = m.budget;
  log(`budget: invocations=${b.invocations} launches=${b.launches} wall=${b.wall_ms}ms ` +
    `[import ${b.stage_ms.import} · launch ${b.stage_ms.launch} · capture ${b.stage_ms.capture} · flush ${b.stage_ms.flush} · compose ${b.stage_ms.compose}]`);
  for (const s of m.sheets) log(`sheet ${s}`);
}

// ---------------------------------------------------------------------------------------------
// the verbs
// ---------------------------------------------------------------------------------------------

// `glance` — the cheap rung. Viewport-clamped captures of a pinned cell set, one sheet set, one
// launch. No full-page slicing: a glance answers "does this screen look right", and 6–8 clamped
// cells pack into 1–2 sheets a model can read in a single turn. Console capture stays OFF here by
// design — the glance's evidence surface is the sheet plus rung-0 geometry; console findings belong
// to the review rung, which has the turns to act on them.
export async function runGlance(ctx) {
  await fusedRun(ctx, { verb: 'glance', viewportOnly: true, dsfTriad: false, consoleCapture: false });
}

// `review-capture` — the deep rung. Full-page frames, the state sweep, the DSF triad at the native
// breakpoint, console listeners per cell, sheets grouped per route×theme.
export async function runReviewCapture(ctx) {
  await fusedRun(ctx, {
    verb: 'review-capture', viewportOnly: false, dsfTriad: true, consoleCapture: true,
    // Route×theme grouping: the route (the snapshot label's own stem) plus the theme token wherever
    // the state label carries one — lifted OUT of the route so `home` and `home__dark` group as
    // `home` and `home-dark`, not as two unrelated routes. The non-native fidelity cells get their
    // own band: comparing a 2.0× frame against a 1.0× one on the same sheet is the misread the
    // fidelity matrix exists to prevent.
    groupOf: (label, cell) => {
      const s = String(label);
      const hit = /(^|[^a-z])(light|dark)([^a-z]|$)/i.exec(s);
      const theme = hit ? hit[2].toLowerCase() : '';
      let route = s.split(/[·|]/)[0];
      if (theme) route = route.replace(new RegExp(`[_-]*${theme}[_-]*`, 'i'), '');
      route = route.replace(/[^\w.@-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '') || 'route';
      const dsf = /@([\d.]+)$/.exec(String(cell));
      const hi = dsf && +dsf[1] !== 1 ? '-dsf' : '';
      return `${route}${theme ? `-${theme}` : ''}${hi}`;
    },
  });
}

// One measured value per failing selector, in that check's own units — the number a reader would
// otherwise go hunting for in the JSON.
function measuredValue(f) {
  const m = f.measured || {};
  switch (f.check) {
    case 'contrast': return `${m.ratio}:1 (needs ${m.required}:1${m.method ? `, ${m.method}` : ''})`;
    case 'fonts': return m.fontSizePx !== undefined ? `${m.fontSizePx}px (floor ${m.floorPx}px)`
      : `line-height ${m.ratio}× font size`;
    case 'overflow': return `${m.overflowPx}px past its ${m.overflowX} box`;
    case 'targets': return `${m.width}×${m.height}px (min ${m.minimumPx}×${m.minimumPx})`;
    case 'rects': return `overlaps ${m.withSelector} by ${m.overlapWidth}×${m.overlapHeight}px`;
    default: return f.description;
  }
}

// The COMPACT stdout for `measure`: per-check tallies, one line per failing selector with its
// number, and the absolute path of the full JSON. Printing the whole payload here is what truncated
// the leaf's command result and sent it Grepping for numbers that were already on disk — the
// summary answers the question, and the path settles anything it doesn't.
export function measureSummary(payload, file, maxLines = 40) {
  const findings = payload.findings || [];
  const targets = payload.targets || [];
  const evaluated = {
    contrast: targets.filter((t) => t.contrast).length,
    fonts: targets.filter((t) => t.font).length,
    overflow: targets.filter((t) => t.overflow).length,
    rects: targets.filter((t) => t.visible && t.visualRect).length,
    targets: targets.reduce((n, t) => n + (t.targets ? t.targets.length : 0), 0),
  };
  const lines = [`measure ${payload.cell.label} · ${targets.length} target(s) · ${findings.length} finding(s)`];
  const checks = payload.checks || [];
  for (const check of checks) {
    const hits = findings.filter((f) => f.check === check);
    const n = evaluated[check] || 0;
    lines.push(`${check}: ${Math.max(0, n - hits.length)} pass / ${hits.length} fail (of ${n})`);
    for (const f of hits) lines.push(`  ${middleEllipsis(f.selector)} — ${measuredValue(f)}`);
  }
  // A selector that matched nothing or would not parse belongs to no requested check, and it is the
  // reason a run measures nothing — it must never be the thing the line budget drops.
  for (const f of findings.filter((f) => !checks.includes(f.check))) {
    lines.push(`${f.check}: ${middleEllipsis(f.selector)} — ${f.description}`);
  }
  const body = lines.length > maxLines - 1
    ? [...lines.slice(0, maxLines - 2), `… +${lines.length - (maxLines - 2)} more line(s) — full detail in the JSON below`]
    : lines;
  return [...body, file].join('\n');
}

// `measure` — the instrument verb. One launch, one page, every requested check in one evaluate,
// plus the PIXEL contrast arm (clip → canvas → composited fg/bg readout) for the text targets that
// have a painted box. stdout is the COMPACT summary above plus the path; the full record is
// `<out>/measure-<n>.json`, which is what a filed finding cites.
export async function runMeasure(ctx) {
  const { opts, OUT, EDGE, TIMEOUT, SETTLE, log, timing } = ctx;
  const t0 = timing.t0 || Date.now();
  const stage = { import: timing.importMs || 0, launch: 0, capture: 0, flush: 0, compose: 0 };
  if (opts.cdp) throw new Error('measure does not support --cdp (it needs its own viewport); use `run` with a scenario for CDP work');

  fs.mkdirSync(OUT, { recursive: true });
  const targets = resolveTargets(ctx);
  if (targets.length > 1) throw new Error('measure takes ONE target — pass a single --url (use several measure calls for several routes)');
  const checks = parseChecks(opts.checks);
  const selectors = parseSelectors(opts);
  const cell = parseMatrix(opts.matrix).map(cellObj)[0];

  const tLaunch = Date.now();
  const browser = await chromium.launch(EDGE);
  stage.launch = Date.now() - tLaunch;
  try {
    const tCap = Date.now();
    const pageCtx = await browser.newContext({ viewport: { width: cell.width, height: cell.height }, deviceScaleFactor: cell.dsf });
    let result;
    try {
      const page = await pageCtx.newPage();
      page.setDefaultTimeout(TIMEOUT);
      await page.goto(targets[0].url, { waitUntil: 'load' });
      if (SETTLE > 0) await page.waitForTimeout(SETTLE);
      try { await page.evaluate(async () => { await document.fonts?.ready; }); } catch { /* fonts API absent */ }
      result = await page.evaluate(MEASURE_PAGE_FN, {
        selectors, checks, thresholds: MEASURE_THRESHOLDS, maxTargets: +(opts['max-targets'] || 40),
      });

      // ---- the PIXEL contrast arm ---------------------------------------------------------
      // Clipped to the element's painted box, clamped into the viewport (a clip that leaves the
      // viewport is a Playwright error, and a half-offscreen label is still worth measuring).
      const vw = result.viewport.width, vh = result.viewport.height;
      for (const q of (result.contrastQueue || []).slice(0, +(opts['max-contrast'] || 12))) {
        const t = result.targets[q.index];
        const x = Math.max(0, Math.min(q.rect.x, vw - 1));
        const y = Math.max(0, Math.min(q.rect.y, vh - 1));
        const width = Math.max(1, Math.min(q.rect.width - (x - q.rect.x), vw - x));
        const height = Math.max(1, Math.min(q.rect.height - (y - q.rect.y), vh - y));
        try {
          const buf = await page.screenshot({ clip: { x, y, width, height } });
          const pair = await pixelPair(browser, buf);
          t.contrast.pixel = pair || { measurable: false, why: 'pixel readout returned nothing' };
          if (pair && pair.measurable && pair.ratio < t.contrast.required) {
            result.findings.push({
              check: 'contrast', selector: t.path,
              description: `measured text contrast ${pair.ratio}:1 is below the AA ${t.contrast.required}:1 floor for ${t.contrast.largeText ? 'large' : 'normal'} text`,
              measured: { ratio: pair.ratio, required: t.contrast.required, color: pair.color, background: pair.background,
                declaredRatio: t.contrast.declared ? t.contrast.declared.ratio : null, colorSyntax: t.contrast.colorSyntax,
                fontSizePx: t.contrast.fontSizePx, fontWeight: t.contrast.fontWeight, method: 'pixel' },
            });
          }
        } catch (e) {
          t.contrast.pixel = { measurable: false, why: `clip capture failed: ${e.message}` };
        }
      }
    } finally { await pageCtx.close(); }
    stage.capture = Date.now() - tCap;

    // Stable ordering: a suite (and a reader) compares finding SETS, not evaluation order.
    result.findings.sort((a, b) => (a.check < b.check ? -1 : a.check > b.check ? 1 : 0)
      || (a.selector < b.selector ? -1 : a.selector > b.selector ? 1 : 0)
      || (a.description < b.description ? -1 : a.description > b.description ? 1 : 0));
    delete result.contrastQueue;

    const { file, index } = nextMeasureFile(OUT);
    const payload = {
      verb: 'measure', generatedBy: 'visual-probe', target: targets[0].url, selectors, checks,
      cell: { label: cell.label, width: cell.width, height: cell.height, dsf: cell.dsf },
      ...result,
      budget: budgetBlock(OUT, { launches: 1, stageMs: stage, wallMs: Date.now() - t0, epochMs: parseEpoch(opts, t0) }),
      note: 'Deterministic instrument output. `findings` is the machine verdict per check; `targets` carries the raw ' +
        'numbers behind it. Contrast has TWO arms: `pixel` (composited, read out of the rendered clip — authoritative) ' +
        'and `declared` (computed-style arithmetic — a cross-check that goes null on gradients/images). A finding is ' +
        'filed from the PIXEL arm only. `visualRect` is the painted box (layout rect ∩ clipping ancestors): overlap is ' +
        'measured on it, never on a text rect that overflows an ellipsized box.',
    };
    fs.writeFileSync(file, JSON.stringify(payload, null, 2));
    log(`measure-${index}: ${result.findings.length} finding(s) over ${result.targets.length} target(s) — ${file}`);
    console.log(measureSummary(payload, path.resolve(file)));
    process.exitCode = 0;
  } finally {
    await browser.close();
  }
}

// `crop` — targeted magnified recapture of ONE selector, post-hoc over an existing out-dir. The
// standalone form of what `--crop` did inside a capture run: a leaf that finds a suspect tile in a
// sheet crops it without re-running the whole sweep.
//
// It MERGES into the out-dir's manifest under `crops` rather than rewriting it: the glance manifest
// beside it is the run's evidence, and clobbering it to record a crop would destroy the thing the
// crop was taken to corroborate.
export async function runCrop(ctx) {
  const { opts, OUT, EDGE, TIMEOUT, MAGNIFY, THRESHOLD, SETTLE, log, timing } = ctx;
  const t0 = timing.t0 || Date.now();
  const stage = { import: timing.importMs || 0, launch: 0, capture: 0, flush: 0, compose: 0 };
  if (opts.cdp) throw new Error('crop does not support --cdp; use `session look --crop` against a live window');
  const selector = opts.selector && opts.selector !== true ? String(opts.selector) : null;
  if (!selector) throw new Error('crop requires --selector <css> (the region to magnify)');

  fs.mkdirSync(OUT, { recursive: true });
  const targets = resolveTargets(ctx);
  const cells = parseMatrix(opts.matrix).map(cellObj);
  const label = `crop-${selector.replace(/[^\w.@-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'sel'}`;

  const snapshots = [];
  const holes = [];
  const collector = { addSnapshot: (s) => snapshots.push(s), addAssert: () => {}, addRung0: () => {} };

  const tLaunch = Date.now();
  const browser = await chromium.launch(EDGE);
  stage.launch = Date.now() - tLaunch;
  try {
    const tCap = Date.now();
    for (const cell of cells) {
      for (const target of targets) {
        const pageCtx = await browser.newContext({
          viewport: { width: cell.width, height: cell.height }, deviceScaleFactor: cell.dsf,
        });
        try {
          const page = await pageCtx.newPage();
          page.setDefaultTimeout(TIMEOUT);
          const h = makeHelper({ page, cell, url: target.url, allowRemote: opts['allow-remote'], collector, rung0: { enabled: false }, settle: SETTLE });
          await page.goto(target.url, { waitUntil: 'load' });
          await h.dwell();
          // A post-hoc crop routinely names something BELOW THE FOLD (the leaf saw it in a sheet
          // tile, which is a whole scrolled frame). A clip outside the viewport is a screenshot
          // error, so bring it into view first and let the crop's box be measured after the scroll.
          try { await page.locator(selector).first().scrollIntoViewIfNeeded({ timeout: TIMEOUT }); }
          catch { /* not scrollable / already visible — captureFrame reports a genuinely absent selector */ }
          await h.snapshot(`${label}__${target.label}`, { crop: selector });
        } catch (e) {
          holes.push({ label, cell: cell.label, kind: 'crop-error', reason: e.message });
        } finally { await pageCtx.close(); }
      }
    }
    stage.capture = Date.now() - tCap;

    const tFlush = Date.now();
    const built = await buildEntries({
      browser, snapshots, outDir: OUT, magnifyFactor: MAGNIFY, threshold: THRESHOLD,
      baselineLabel: cells[0]?.label, log,
    });
    stage.flush = Date.now() - tFlush;

    const file = path.join(OUT, 'manifest.json');
    const freshManifest = () => ({
      verb: 'crop', generatedBy: 'visual-probe', generatedAt: new Date(t0).toISOString(),
      target: targets[0].url, cells: cells.map((c) => c.label),
      cellGeometry: cells.map((c) => ({ label: c.label, width: c.width, height: c.height, dsf: c.dsf })),
      snapshots: [], asserts: [], coverageHoles: [], cropHoles: [], console: [],
      rung0: [], rung0Overflow: {}, rung0File: null, rung0Total: 0, rung0UnusedSuppressions: [],
      sheets: [], sheetTally: { sheets: 0, tiles: 0, placeholders: 0 }, deadline_hit: false, blocked: null,
      pass: true, note: MANIFEST_NOTE,
    });
    const readManifest = () => {
      try {
        const m = JSON.parse(fs.readFileSync(file, 'utf8'));
        if (m && m.generatedBy) return m;
      } catch { /* absent or unreadable — this crop starts the manifest */ }
      return freshManifest();
    };
    // The append is read-modify-write against a file a concurrent capture may be rewriting. Re-read
    // immediately before the write and compare `generatedAt`: if a newer manifest landed under us,
    // re-apply this crop's rows onto THAT one instead of publishing our stale base over it.
    const applyCrop = (base) => {
      const crops = Array.isArray(base.crops) ? base.crops : [];
      for (const s of built) {
        for (const e of s.entries) {
          crops.push({ selector, label: s.label, cell: e.cell, dsf: e.dsf, file: e.file, magnified: e.magnified, magnify: MAGNIFY });
        }
      }
      base.crops = crops;
      // A failed crop is THIS question's indeterminacy, never a coverage hole: coverageHoles blocks a
      // clean pass on the capture, and a missing selector says nothing about the cells that WERE shot.
      if (holes.length) base.cropHoles = [...(base.cropHoles || []), ...holes];
      base.budget = budgetBlock(OUT, { launches: 1, stageMs: stage, wallMs: Date.now() - t0, epochMs: parseEpoch(opts, t0) });
      return base;
    };

    const base = readManifest();
    let manifest = applyCrop(base);
    const current = readManifest();
    if (current.generatedAt !== base.generatedAt) {
      log('manifest changed under this crop — re-applying onto the newer capture');
      manifest = applyCrop(current);
    }
    writeManifestAtomic(file, manifest);

    const crops = manifest.crops;
    const fresh = crops.slice(crops.length - built.reduce((n, s) => n + s.entries.length, 0));
    for (const c of fresh) log(`crop ${c.cell} → ${c.magnified || c.file}`);
    if (holes.length) log(`crop holes (${holes.length}) — this crop's question is indeterminate, the capture's coverage is untouched:`,
      holes.map((h) => `${h.cell}: ${h.reason}`).join(' | '));
    console.log(JSON.stringify({ selector, crops: fresh, cropHoles: holes, manifest: file, budget: manifest.budget }, null, 2));
    process.exitCode = fresh.length ? 0 : 1;
  } finally {
    await browser.close();
  }
}
