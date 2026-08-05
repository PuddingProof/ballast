// visual-probe — real CLI body. Reached only via probe.mjs's DYNAMIC import, after the bootstrap's
// readiness check passes. Full command/flag reference lives in probe.mjs's HELP text — don't duplicate.

import { chromium } from 'playwright';
import { pathToFileURL } from 'url';
import fs from 'fs';
import path from 'path';
import { parseMatrix, cellObj } from './matrix.mjs';
import { guardUrl } from './urlguard.mjs';
import { buildEntries, DEFAULT_OUT } from './capture.mjs';
import { makeHelper } from './helpers.mjs';
import { aggregateRung0, readSuppressionsFile } from './assertions.mjs';
import { sourcesFromManifest, renderSheets } from './compose.mjs';
import { startSession, lookSession, readSession, doSession, stopSession, firstRealPage } from './session.mjs';
import { startServe, statusServe, stopServe } from './serve.mjs';
import { runSelfTest } from './selftest.mjs';
import { EDGE_NO_SYNC } from './edge-privacy.mjs';
import { runGlance, runReviewCapture, runMeasure, runCrop } from './fused.mjs';

// `timing` comes from the bootstrap (probe.mjs): the process's own t0 and the ms spent importing
// this module. The fused verbs stamp both into the manifest's budget block, so a leaf's wall-clock
// is attributable to stages instead of reconstructed from transcripts after the fact.
export async function main(argv, timing = {}) {
  const cmd = argv[0];
  const positional = [];
  const opts = {};
  for (let i = 1; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) opts[key] = true;
      else { opts[key] = next; i++; }
    } else positional.push(a);
  }

  // system Edge → no binary download. EDGE_NO_SYNC keeps even these short-lived capture launches off the
  // user's account (distinct switches — safe alongside Playwright's own --disable-features). See edge-privacy.mjs.
  const EDGE = { channel: 'msedge', headless: !opts.headed, args: EDGE_NO_SYNC };
  const OUT = path.resolve(opts.out || DEFAULT_OUT); // default: OS-temp, never the repo tree (--out to keep frames durably)
  const MAGNIFY = +(opts.magnify || 8);
  const TIMEOUT = +(opts.timeout || 30000);
  const THRESHOLD = +(opts['diff-threshold'] || 6);

  // --settle MS: a dwell between "the page is ready" and the shutter, for a project whose entrance
  // animation / theme crossfade completes AFTER load (shot) or after the state's readySignal
  // (manifest scenario). PER capture/state, not once per invocation — every cell pays it, because
  // each cell renders the transition again in a fresh context. Validated rather than coerced: a
  // bare `--settle` (no value) parses as `true` above, and a silent 0 would look like the flag
  // worked while capturing mid-transition — exactly the false-pass the flag exists to close.
  const SETTLE = (() => {
    if (opts.settle === undefined) return 0;
    const n = Number(opts.settle);
    if (opts.settle === true || !Number.isFinite(n) || n < 0) {
      throw new Error(`--settle expects a non-negative integer of milliseconds (got ${opts.settle === true ? '(no value)' : opts.settle})`);
    }
    return Math.round(n);
  })();

  const log = (...a) => console.error('[visual-probe]', ...a); // stderr; stdout reserved for the manifest path

  // Rung-0 geometry assertions run on by default (they are ~free and SHADOW-LOGGED — see
  // lib/assertions.mjs). `--suppressions <file>` takes the bare array or a state manifest with a
  // top-level `suppressions` key; a scenario may declare its own on top (merged, flag first).
  const RUNG0_ON = !opts['no-rung0'];
  const flagSuppressions = opts.suppressions && opts.suppressions !== true
    ? readSuppressionsFile(fs, path.resolve(opts.suppressions)) : [];

  // A bare target may be a URL or a filesystem path → normalize a path to a file:// URL.
  function resolveTarget(t) {
    if (!t) return t;
    if (/^(https?|file):/i.test(t)) return t;
    return pathToFileURL(path.resolve(t)).href;
  }

  // Format a live viewport for look/read output: CSS px @ device-scale-factor (≈ device px). Co-driving
  // resize/zoom is a core use case, so the number is in every look/read header to confirm a change.
  // Flags a DEGENERATE viewport: the shared window has degraded over a long session (observed facet:
  // a 599×38 window neither party resized) — that's the window, not the app; restart the session.
  function vpStr(v) {
    if (!v || !v.w) return 'viewport ?';
    const s = `viewport ${v.w}×${v.h} @ ${v.dsf}× (≈ ${Math.round(v.w * v.dsf)}×${Math.round(v.h * v.dsf)} device px)`;
    if (v.w < 200 || v.h < 120) return `${s}  ⚠ DEGENERATE viewport — the shared window has degraded (known long-session facet, not the app): 'session stop' then a fresh 'start'`;
    return s;
  }

  // ---------- doctor ----------
  async function doctor() {
    const pkg = JSON.parse(fs.readFileSync(new URL('../../package.json', import.meta.url)));
    log('pinned playwright =', pkg.dependencies.playwright);
    const t0 = Date.now();
    const browser = await chromium.launch(EDGE);
    let okDpr = false, dpr = null;
    try {
      const ctx = await browser.newContext({ viewport: { width: 400, height: 300 }, deviceScaleFactor: 1.5 });
      const page = await ctx.newPage();
      await page.setContent('<body style="margin:0"><div style="width:50px;height:50px;background:#000"></div></body>');
      dpr = await page.evaluate(() => window.devicePixelRatio);
      const buf = await page.screenshot({ clip: { x: 0, y: 0, width: 50, height: 50 } });
      okDpr = Math.abs(dpr - 1.5) < 1e-6 && buf.length > 0;
      log(`edge launched headless in ${Date.now() - t0}ms; devicePixelRatio=${dpr} (expect 1.5); 50css-px clip => ${buf.length} bytes (≈75×75 device px)`);
    } finally { await browser.close(); }
    console.log(JSON.stringify({ ok: okDpr, playwright: pkg.dependencies.playwright, devicePixelRatio: dpr }));
    process.exit(okDpr ? 0 : 1);
  }

  // ---------- core runner ----------
  async function runCells({ scenario, scenarioModule, target, matrix, crop }) {
    fs.mkdirSync(OUT, { recursive: true });
    const cells = matrix.map(cellObj);
    let effectiveCells = cells; // CDP attach mode uses a single live cell, not the matrix
    const baselineLabel = opts.baseline || cells[0].label;

    const snapshots = []; // {label, cell, buf, opts}
    const asserts = [];   // {cell, msg, value, pass}
    const rung0Records = []; // {label, cell, result} — one per captured state × cell
    const collector = {
      addSnapshot: (s) => snapshots.push(s),
      addAssert: (a) => asserts.push(a),
      addRung0: (r) => rung0Records.push(r),
    };
    // Live view of the suppression list: a scenario (states-from-manifest) only learns its
    // project's suppressions once it has read the manifest, i.e. after the first cell starts.
    const suppressionsNow = () => [
      ...flagSuppressions,
      ...(Array.isArray(scenarioModule?.suppressions) ? scenarioModule.suppressions : []),
    ];
    const rung0 = { enabled: RUNG0_ON, suppressions: suppressionsNow };

    // Drive browser: either launch system Edge, or attach to a live app over CDP (Tier-1).
    const usingCdp = !!opts.cdp;
    const browser = usingCdp ? await chromium.connectOverCDP(opts.cdp) : await chromium.launch(EDGE);

    try {
      if (usingCdp) {
        // Attach mode: drive the live app's existing page. Device-scale-factor cannot be changed on a
        // live context, so the fidelity matrix does NOT apply here — this mode is for driving real
        // WebView2/__TAURI__ states, not for cross-scale rendering checks.
        log('CDP attach — fidelity matrix disabled (cannot set DSF on a live context); single live cell');
        // pick the REAL app page (skip about:blank/devtools), same logic the session path uses — a naive
        // pages()[0] can grab a blank/devtools tab and screenshot the wrong thing on attach.
        const page = firstRealPage(browser);
        page.setDefaultTimeout(TIMEOUT);
        const cell = { label: 'cdp-live', width: 0, height: 0, dsf: 0 };
        effectiveCells = [cell];
        const h = makeHelper({ page, cell, url: target, allowRemote: opts['allow-remote'], collector, rung0, settle: SETTLE });
        if (scenario) await scenario(page, h);
        else { await page.goto(target ?? page.url(), { waitUntil: 'load' }); await h.dwell(); await h.snapshot('shot', { crop }); }
      } else {
        // Matrix mode: a FRESH in-memory context per cell at its {viewport, deviceScaleFactor}.
        // Fresh context per cell also defeats the headless file:// paint-cache trap.
        for (const cell of cells) {
          const ctx = await browser.newContext({
            viewport: { width: cell.width, height: cell.height },
            deviceScaleFactor: cell.dsf,
          });
          const page = await ctx.newPage();
          page.setDefaultTimeout(TIMEOUT);
          const h = makeHelper({ page, cell, url: target, allowRemote: opts['allow-remote'], collector, rung0, settle: SETTLE });
          try {
            if (scenario) await scenario(page, h);
            else { await page.goto(target, { waitUntil: 'load' }); await h.dwell(); await h.snapshot('shot', { crop }); }
          } finally { await ctx.close(); }
        }
      }

      await flush({ snapshots, asserts, rung0Records, suppressions: suppressionsNow(), cells: effectiveCells, baselineLabel, target, scenarioModule });
    } finally {
      await browser.close(); // for connectOverCDP this detaches without killing the live app
    }
  }

  // ---------- flush: write frames, magnify cropped ones, hash, score divergence, emit manifest ----------
  async function flush({ snapshots, asserts, rung0Records = [], suppressions = [], cells, baselineLabel, target, scenarioModule }) {
    // A dedicated ephemeral Edge for image post-processing — keeps magnify/hash off any CDP-attached app.
    const imgBrowser = await chromium.launch(EDGE);
    try {
      // A scenario declares states it could not force by exporting `coverageHoles` — surfaced here
      // as manifest data (never stderr-only), because a hole absorbed silently reads as a pass.
      const coverageHoles = Array.isArray(scenarioModule?.coverageHoles) ? scenarioModule.coverageHoles : [];

      // Rung-0 geometry findings — always present, never gating (see lib/assertions.mjs).
      const { findings: rung0, unusedSuppressions } = aggregateRung0(rung0Records, suppressions);

      const manifest = {
        target, generatedBy: 'visual-probe',
        cells: cells.map((c) => c.label),
        // Per-cell viewport geometry: what `compose` needs to slice a full-page frame into true
        // viewport screenfuls (the cell LABEL is not a parseable source for preset matrices).
        cellGeometry: cells.map((c) => ({ label: c.label, width: c.width, height: c.height, dsf: c.dsf })),
        magnify: MAGNIFY, divergenceThreshold: THRESHOLD,
        snapshots: [], asserts, coverageHoles, rung0, rung0UnusedSuppressions: unusedSuppressions, pass: true,
        note: 'READ manifest first, then read ONLY the .xN.png magnified crops of cells flagged `diverges:true` — never the inline full-frame thumbnails (the agent Read path downscales and hides sub-pixel defects). `coverageHoles` lists states this run could not force: they are not asserts and do not affect `pass`, but they block a clean verdict. `rung0` lists deterministic geometry findings (overlap / overflow / contrast / broken-image / offscreen / misalignment) per `<label>__<cell>`: SHADOW-LOGGED advisory data — it does not affect `pass` — pre-locating where to look; entries with `suppressed:true` were declared intended by the project. `rung0UnusedSuppressions` lists declared suppressions that matched nothing (stale, or malformed).',
      };

      manifest.snapshots = await buildEntries({
        browser: imgBrowser, snapshots, outDir: OUT,
        magnifyFactor: MAGNIFY, threshold: THRESHOLD, baselineLabel, log,
      });

      manifest.pass = asserts.every((a) => a.pass);
      const manifestFile = path.join(OUT, 'manifest.json');
      fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2));

      // Human-readable summary → stderr; the manifest path → stdout (for scripting).
      const diverged = manifest.snapshots.flatMap((s) =>
        s.entries.filter((e) => e.diverges).map((e) => `${s.label}:${e.cell}(Δ${e.divergenceVsBaseline})`));
      log(`cells=${cells.length} snapshots=${manifest.snapshots.length} asserts=${asserts.length} pass=${manifest.pass}`);
      if (coverageHoles.length) log(`COVERAGE HOLES (${coverageHoles.length}) — unforced states, see manifest.coverageHoles:`,
        coverageHoles.map((c) => `${c.label} [${c.kind}]`).join(', '));
      if (RUNG0_ON) {
        const live = rung0.filter((f) => !f.suppressed);
        const byCheck = {};
        for (const f of live) byCheck[f.check] = (byCheck[f.check] || 0) + 1;
        log(`rung0: ${live.length} finding(s)${rung0.length - live.length ? ` (+${rung0.length - live.length} suppressed)` : ''} — advisory, does not affect pass`,
          live.length ? `— ${Object.entries(byCheck).map(([k, v]) => `${k}×${v}`).join(', ')}` : '');
        if (unusedSuppressions.length) log(`rung0: ${unusedSuppressions.length} declared suppression(s) matched nothing — see manifest.rung0UnusedSuppressions`);
      }
      if (diverged.length) log('DIVERGENT cells — inspect their .x' + MAGNIFY + '.png crops:', diverged.join(', '));
      const failed = asserts.filter((a) => !a.pass);
      if (failed.length) log('FAILED asserts:', failed.map((a) => `[${a.cell}] ${a.msg} (got ${JSON.stringify(a.value)})`).join(' | '));
      console.log(manifestFile);
      process.exitCode = manifest.pass ? 0 : 1;
    } finally {
      await imgBrowser.close();
    }
  }

  // ---------- compose: contact-sheet mosaic over an out-dir ----------
  // Reads a finished capture dir (manifest.json + native PNGs), slices every frame into viewport
  // segments (lib/mosaic.mjs) and re-screenshots the sheet with THIS harness — one rendering path,
  // no image dependency. Sheets are sized under the vision pipeline's downscale ceiling; a set that
  // does not fit yields more sheets, never a bigger one.
  async function compose() {
    const dir = path.resolve(positional[0] || OUT);
    const manifestFile = path.join(dir, 'manifest.json');
    if (!fs.existsSync(manifestFile)) throw new Error(`no manifest.json in ${dir} — compose reads a finished capture out-dir`);
    const m = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
    const groupRe = opts.group && opts.group !== true ? new RegExp(opts.group) : null;
    const { sources, skipped } = sourcesFromManifest(m, dir, { groupRe });
    if (!sources.length) throw new Error(`no composable frames in ${dir}${skipped.length ? ` — ${skipped.length} skipped: ${skipped[0].reason}` : ''}`);

    const browser = await chromium.launch(EDGE);
    let out = [];
    try {
      ({ sheets: out } = await renderSheets({
        browser, dir, sources,
        tileH: +(opts['tile-height'] || 400), maxSide: +(opts['max-side'] || 1568),
        cropContent: !!opts['crop-content'], timeout: TIMEOUT, log,
      }));
    } finally { await browser.close(); }

    for (const s of out) log(`sheet ${s.file} — ${s.width}×${s.height}, ${s.tiles} tile(s)${s.group ? ` [${s.group}]` : ''}`);
    for (const s of skipped) log('SKIPPED', s.name, '-', s.reason);
    log('the mosaic is a ROUTER, not a verdict: per tile clear-or-escalate, and read the escalated cell at full resolution before any verdict.');
    console.log(JSON.stringify({ sheets: out, skipped }, null, 2));
  }

  // Everything the fused verbs need from this parser, handed over as one context object — they own
  // their own pipeline (lib/fused.mjs) but must not re-derive the flag semantics that every other
  // verb already agreed on (settle validation, out-dir resolution, the Edge launch options).
  const ctx = {
    opts, positional, OUT, EDGE, MAGNIFY, TIMEOUT, THRESHOLD, SETTLE, RUNG0_ON,
    flagSuppressions, log, timing, resolveTarget,
  };

  try {
    if (cmd === 'doctor') {
      await doctor();
    } else if (cmd === 'glance') {
      await runGlance(ctx);
    } else if (cmd === 'review-capture') {
      await runReviewCapture(ctx);
    } else if (cmd === 'measure') {
      await runMeasure(ctx);
    } else if (cmd === 'crop') {
      await runCrop(ctx);
    } else if (cmd === 'selftest') {
      const r = await runSelfTest({ channel: EDGE.channel });
      for (const c of r.results) log(`${c.ok ? 'PASS' : 'FAIL'}  ${c.name} — ${c.detail}`);
      console.log(JSON.stringify({ ok: r.ok, checks: Object.fromEntries(r.results.map((c) => [c.name, c.ok])) }));
      process.exit(r.ok ? 0 : 1);
    } else if (cmd === 'shot') {
      const target = guardUrl(resolveTarget(positional[0]), opts['allow-remote']);
      await runCells({ scenario: null, target, matrix: parseMatrix(opts.matrix), crop: opts.crop });
    } else if (cmd === 'run') {
      if (!positional[0]) throw new Error('run requires a scenario file: probe.mjs run <scenario.mjs> --url <target>');
      // Leaf mode: normalize the flag into the env the bundled manifest scenario already reads its
      // config from (VISUAL_STATES*), so the flag and the env var are one switch, not two.
      if (opts['skip-drive-hooks']) process.env.VISUAL_STATES_SKIP_DRIVE_HOOKS = '1';
      const mod = await import(pathToFileURL(path.resolve(positional[0])).href);
      if (typeof mod.default !== 'function') throw new Error('scenario must default-export  async (page, h) => {}');
      const target = opts.url ? guardUrl(resolveTarget(opts.url), opts['allow-remote']) : undefined;
      await runCells({ scenario: mod.default, scenarioModule: mod, target, matrix: parseMatrix(opts.matrix), crop: opts.crop });
    } else if (cmd === 'compose') {
      await compose();
    } else if (cmd === 'serve') {
      const sub = positional[0];
      if (sub === 'start') {
        const rec = await startServe({ dir: positional[1], port: opts.port ? +opts.port : 0 });
        log(`isolated server UP — ${rec.url} (root ${rec.root}, pid ${rec.pid}) — loopback, no-store, GET/HEAD-only, no /api`);
        console.log(JSON.stringify(rec));
      } else if (sub === 'status') {
        const r = await statusServe({});
        log(r.up ? `isolated server UP — ${r.url} (root ${r.root}, pid ${r.pid})` : 'no isolated server running');
        console.log(JSON.stringify(r));
      } else if (sub === 'stop') {
        const r = await stopServe({});
        log(`isolated server stopped (pid ${r.stopped}, :${r.port})`);
      } else {
        log('usage: probe.mjs serve <start <dir> [--port N] | status | stop>');
        process.exit(2);
      }
    } else if (cmd === 'session') {
      const sub = positional[0];
      if (sub === 'start') {
        const rec = await startSession({
          url: positional[1] ? resolveTarget(positional[1]) : undefined,
          port: opts.port ? +opts.port : 9222,
          allowRemote: opts['allow-remote'],
        });
        log(`shared session UP — pid ${rec.pid}, CDP http://127.0.0.1:${rec.port}, ${rec.browser}`);
        log('the headed window is the shared surface: you drive it by hand; Claude uses `session look` / `session do`.');
        console.log(JSON.stringify(rec));
      } else if (sub === 'look') {
        const r = await lookSession({ out: OUT, crop: opts.crop });
        log(`look: "${r.title || '(untitled)'}" @ ${r.url} — ${vpStr(r.viewport)}`);
        console.log(r.file);
      } else if (sub === 'read') {
        const r = await readSession({ selector: positional[1] });
        log(`read "${r.title || '(untitled)'}" @ ${r.url} — ${vpStr(r.viewport)} — ${r.controlCount} control(s) under "${r.selector}"`);
        const more = r.controls.length < r.controlCount ? `\n…(+${r.controlCount - r.controls.length} more controls)` : '';
        console.log(`# ${r.selector} @ ${r.url}  (${vpStr(r.viewport)})\n\n## text\n${r.text}\n\n## controls (${r.controlCount})\n${r.controls.join('\n')}${more}`);
      } else if (sub === 'do') {
        const r = await doSession({ action: positional[1], args: positional.slice(2), allowRemote: opts['allow-remote'], timeout: Number.isFinite(+opts.timeout) ? +opts.timeout : undefined });
        log(`did ${r.action} → now at ${r.url}`);
      } else if (sub === 'stop') {
        const r = await stopSession({});
        log(`session stopped (pid ${r.stopped}, :${r.port})${r.profileRemoved ? '' : ' — profile still locked, cleared on next start'}`);
      } else {
        log('usage: probe.mjs session <start [url] | look | do <action> <args…> | stop>');
        process.exit(2);
      }
    } else {
      log('usage: probe.mjs <glance | review-capture | measure | crop | doctor | shot <url> | run <scenario.mjs> | compose <out-dir> | serve … | session …> [flags] — see file header or --help');
      process.exit(2);
    }
  } catch (e) {
    log('ERROR:', e.message);
    process.exit(1);
  }
}
