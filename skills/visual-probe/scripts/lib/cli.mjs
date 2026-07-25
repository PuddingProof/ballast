// visual-probe — real CLI body. Reached only via probe.mjs's DYNAMIC import, after the bootstrap's
// readiness check passes. Full command/flag reference lives in probe.mjs's HELP text — don't duplicate.

import { chromium } from 'playwright';
import { pathToFileURL } from 'url';
import fs from 'fs';
import path from 'path';
import { parseMatrix, cellObj } from './matrix.mjs';
import { guardUrl } from './urlguard.mjs';
import { magnify, aHash, hamming, DEFAULT_OUT } from './capture.mjs';
import { makeHelper } from './helpers.mjs';
import { startSession, lookSession, readSession, doSession, stopSession, firstRealPage } from './session.mjs';
import { runServeChild, startServe, statusServe, stopServe } from './serve.mjs';
import { runSelfTest } from './selftest.mjs';
import { EDGE_NO_SYNC } from './edge-privacy.mjs';

export async function main(argv) {
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

  const log = (...a) => console.error('[visual-probe]', ...a); // stderr; stdout reserved for the manifest path

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
  async function runCells({ scenario, target, matrix, crop }) {
    fs.mkdirSync(OUT, { recursive: true });
    const cells = matrix.map(cellObj);
    let effectiveCells = cells; // CDP attach mode uses a single live cell, not the matrix
    const baselineLabel = opts.baseline || cells[0].label;

    const snapshots = []; // {label, cell, buf, opts}
    const asserts = [];   // {cell, msg, value, pass}
    const collector = { addSnapshot: (s) => snapshots.push(s), addAssert: (a) => asserts.push(a) };

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
        const h = makeHelper({ page, cell, url: target, allowRemote: opts['allow-remote'], collector });
        if (scenario) await scenario(page, h);
        else { await page.goto(target ?? page.url(), { waitUntil: 'load' }); await h.snapshot('shot', { crop }); }
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
          const h = makeHelper({ page, cell, url: target, allowRemote: opts['allow-remote'], collector });
          try {
            if (scenario) await scenario(page, h);
            else { await page.goto(target, { waitUntil: 'load' }); await h.snapshot('shot', { crop }); }
          } finally { await ctx.close(); }
        }
      }

      await flush({ snapshots, asserts, cells: effectiveCells, baselineLabel, target });
    } finally {
      await browser.close(); // for connectOverCDP this detaches without killing the live app
    }
  }

  // ---------- flush: write frames, magnify cropped ones, hash, score divergence, emit manifest ----------
  async function flush({ snapshots, asserts, cells, baselineLabel, target }) {
    // A dedicated ephemeral Edge for image post-processing — keeps magnify/hash off any CDP-attached app.
    const imgBrowser = await chromium.launch(EDGE);
    try {
      const byLabel = {};
      for (const s of snapshots) (byLabel[s.label] ||= []).push(s);

      const manifest = {
        target, generatedBy: 'visual-probe',
        cells: cells.map((c) => c.label),
        magnify: MAGNIFY, divergenceThreshold: THRESHOLD,
        snapshots: [], asserts, pass: true,
        note: 'READ manifest first, then read ONLY the .xN.png magnified crops of cells flagged `diverges:true` — never the inline full-frame thumbnails (the agent Read path downscales and hides sub-pixel defects).',
      };

      for (const [label, snaps] of Object.entries(byLabel)) {
        const entries = [];
        for (const s of snaps) {
          const base = `${label}__${s.cell.label}`.replace(/[^\w.@-]/g, '_');
          const nativeFile = path.join(OUT, `${base}.png`);
          fs.writeFileSync(nativeFile, s.buf);

          // Magnify ONLY cropped snapshots (a full-page 8× blow-up would be enormous and useless).
          let magnified = null;
          if (s.opts?.crop) {
            try { magnified = await magnify(imgBrowser, s.buf, MAGNIFY, path.join(OUT, `${base}.x${MAGNIFY}.png`)); }
            catch (e) { log('magnify failed for', base, '-', e.message); }
          }

          let hash = null;
          try { hash = await aHash(imgBrowser, s.buf); } catch (e) { log('hash failed for', base, '-', e.message); }

          entries.push({ cell: s.cell.label, dsf: s.cell.dsf, file: nativeFile, magnified, bytes: s.buf.length, aHash: hash });
        }

        // Cross-cell divergence vs the baseline cell (size-normalized aHash Hamming distance).
        const baseEntry = entries.find((e) => e.cell === baselineLabel) || entries[0];
        for (const e of entries) {
          const d = hamming(e.aHash, baseEntry.aHash);
          e.divergenceVsBaseline = Number.isFinite(d) ? d : null;
          e.diverges = e.cell !== baseEntry.cell && Number.isFinite(d) && d > THRESHOLD;
        }
        manifest.snapshots.push({ label, baseline: baseEntry.cell, entries });
      }

      manifest.pass = asserts.every((a) => a.pass);
      const manifestFile = path.join(OUT, 'manifest.json');
      fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2));

      // Human-readable summary → stderr; the manifest path → stdout (for scripting).
      const diverged = manifest.snapshots.flatMap((s) =>
        s.entries.filter((e) => e.diverges).map((e) => `${s.label}:${e.cell}(Δ${e.divergenceVsBaseline})`));
      log(`cells=${cells.length} snapshots=${manifest.snapshots.length} asserts=${asserts.length} pass=${manifest.pass}`);
      if (diverged.length) log('DIVERGENT cells — inspect their .x' + MAGNIFY + '.png crops:', diverged.join(', '));
      const failed = asserts.filter((a) => !a.pass);
      if (failed.length) log('FAILED asserts:', failed.map((a) => `[${a.cell}] ${a.msg} (got ${JSON.stringify(a.value)})`).join(' | '));
      console.log(manifestFile);
      process.exitCode = manifest.pass ? 0 : 1;
    } finally {
      await imgBrowser.close();
    }
  }

  try {
    if (cmd === 'doctor') {
      await doctor();
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
      const mod = await import(pathToFileURL(path.resolve(positional[0])).href);
      if (typeof mod.default !== 'function') throw new Error('scenario must default-export  async (page, h) => {}');
      const target = opts.url ? guardUrl(resolveTarget(opts.url), opts['allow-remote']) : undefined;
      await runCells({ scenario: mod.default, target, matrix: parseMatrix(opts.matrix), crop: opts.crop });
    } else if (cmd === '__serve-child') {
      // hidden: the detached server process spawned by `serve start` (see lib/serve.mjs)
      runServeChild({ root: positional[0], port: +positional[1] });
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
      log('usage: probe.mjs <doctor | shot <url> | run <scenario.mjs> | serve … | session …> [flags] — see file header or --help');
      process.exit(2);
    }
  } catch (e) {
    log('ERROR:', e.message);
    process.exit(1);
  }
}
