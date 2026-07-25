// Self-test: a repeatable regression guard for the behaviors `doctor` does NOT cover — the
// role→selector mapping in read.mjs, basic capture, and the states-from-manifest scenario's
// manifest round-trip. Run it after an Edge / Playwright / Claude-Code update to catch SILENT
// drift (e.g. Playwright's implicit-role table shifting under the `role=…[name]` selectors `read`
// emits, which would make every emitted selector miss without any error). No live session, no
// network — headless `setContent` / `file://` fixtures only. Exits 0 iff every check passes.
//
// The fixture deliberately includes the verified divergence cases (a <summary>, file/hidden inputs, a
// size>1 select, a datalist-bound input): each emitted `role=…` selector must resolve back to its
// element, and <summary>/hidden (no implicit ARIA role) must emit NO selector rather than a wrong one.

import fs from 'fs';
import os from 'os';
import path from 'path';
import { pathToFileURL } from 'url';
import { chromium } from 'playwright';
import { readStructure } from './read.mjs';
import { makeHelper } from './helpers.mjs';
import { EDGE_NO_SYNC } from './edge-privacy.mjs';

const FIXTURE = `<body>
  <details><summary>Advanced options</summary><p>x</p></details>
  <label for="up">Upload</label><input id="up" type="file">
  <input type="hidden" name="csrf" value="xyz">
  <select size="4" aria-label="Servers"><option>a</option><option>b</option></select>
  <input type="search" list="cmds" aria-label="Command"><datalist id="cmds"><option>build</option></datalist>
  <button>Export</button>
  <a href="#docs">Docs</a>
  <input type="checkbox" id="lt"><label for="lt">Live tail</label>
</body>`;

export async function runSelfTest({ channel = 'msedge' } = {}) {
  const results = [];
  const browser = await chromium.launch({ channel, headless: true, args: EDGE_NO_SYNC });
  try {
    const page = await (await browser.newContext()).newPage();

    // (1) capture produces a non-empty PNG at a clipped region
    await page.setContent('<body style="margin:0"><div style="width:40px;height:40px;background:#000"></div></body>');
    const buf = await page.screenshot({ clip: { x: 0, y: 0, width: 40, height: 40 } });
    results.push({ name: 'capture', ok: buf.length > 0, detail: `${buf.length} bytes` });

    // (2) read → every emitted role= selector round-trips; <summary>/hidden correctly emit none
    await page.setContent(FIXTURE);
    const r = await readStructure(page, 'body');
    let resolved = 0;
    const bad = [];
    for (const line of r.controls) {
      const sel = line.split('  →  ')[1];
      const expectNoSel = /^disclosure:|^hidden:/.test(line); // null-role cases — a selector here would be wrong
      if (sel) {
        const n = await page.locator(sel).count().catch(() => 0);
        if (n >= 1 && !expectNoSel) resolved++;
        else bad.push(`${line}  [resolves=${n}${expectNoSel ? ', expected NO selector' : ''}]`);
      } else if (!expectNoSel) {
        bad.push(`${line}  [missing selector]`);
      }
    }
    results.push({ name: 'read-selectors', ok: bad.length === 0, detail: bad.length ? `BAD: ${bad.join(' | ')}` : `${resolved} resolve, summary/hidden correctly bare` });

    // (3) states-from-manifest round trip: a temp-dir manifest + a file:// fixture page drive the
    // REAL scenario module (states-from-manifest.mjs) end to end through its public entry point —
    // manifest discovery (VISUAL_STATES env var), URL composition for a param axis and an overlay
    // flag, the marker wait (h.snapshotForced), the cannotForce skip, and the `drive` escape hatch
    // (references/state-contract.md) for a per-flag driven overlay. No network: file:// keeps this
    // check's no-network charter.
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'visual-probe-selftest-'));
    try {
      const fixtureFile = path.join(tmpDir, 'fixture.html');
      fs.writeFileSync(fixtureFile, `<!doctype html>
<body data-app-ready style="margin:0;min-height:20px">
<div data-overlay="panel" hidden style="width:10px;height:10px"></div>
<script>
  const p = new URLSearchParams(location.search);
  document.body.dataset.theme = p.get('theme') || 'light';
  if (p.get('settings') === '1') {
    const d = document.createElement('div');
    d.setAttribute('data-overlay', 'settings');
    d.style.cssText = 'width:10px;height:10px';
    document.body.appendChild(d);
  }
</script>
</body>`);

      // Drive module for the `drive` escape hatch: reveals the fixture's pre-existing hidden
      // `[data-overlay='panel']` element directly (no addressable URL seam) — exercises the same
      // path an app would use for an overlay driven by interaction rather than a query param.
      const driveFile = path.join(tmpDir, 'visual-states.drive.mjs');
      fs.writeFileSync(driveFile, `export async function openPanel(page, h, stateName) {
  await page.evaluate(() => {
    const el = document.querySelector("[data-overlay='panel']");
    if (el) el.hidden = false;
  });
}
`);

      const manifestFile = path.join(tmpDir, 'visual-states.json');
      fs.writeFileSync(manifestFile, JSON.stringify({
        baseUrl: pathToFileURL(fixtureFile).href,
        routes: { home: '' },
        readySignal: '[data-app-ready]',
        axes: {
          theme: { param: 'theme', values: ['light', 'dark'] },
          overlay: { flags: {
            settings: { param: 'settings', value: '1', marker: "[data-overlay='settings']" },
            panel: { drive: './visual-states.drive.mjs#openPanel', marker: "[data-overlay='panel']" },
          } },
        },
        // deliberately blocks BOTH the theme one-hot state AND the all-worst composed states
        // (worst theme = last-listed = 'dark') — exercises the skip path on two different callers.
        cannotForce: [{ axis: 'theme', value: 'dark', reason: 'selftest probe — no dark fixture' }],
      }, null, 2));

      const prevEnv = process.env.VISUAL_STATES;
      process.env.VISUAL_STATES = manifestFile;
      try {
        const scenario = (await import('../../scenarios/states-from-manifest.mjs')).default;
        const page3 = await (await browser.newContext()).newPage();
        const snaps = []; const manifestAsserts = [];
        const collector = { addSnapshot: (s) => snaps.push(s), addAssert: (a) => manifestAsserts.push(a) };
        const h = makeHelper({ page: page3, cell: { label: 'selftest' }, url: undefined, allowRemote: false, collector });
        await scenario(page3, h);

        // baseline (home) + one-hot overlay settings (home__settings) + one-hot driven overlay
        // panel (home__panel) survive; one-hot theme (dark) and the all-worst composed states for
        // both overlay flags (also worst-theme dark) are cannotForce-blocked.
        const labels = snaps.map((s) => s.label).sort();
        const expectLabels = ['home', 'home__panel', 'home__settings'];
        const labelsOk = JSON.stringify(labels) === JSON.stringify(expectLabels);
        const assertsOk = manifestAsserts.length === 3 && manifestAsserts.every((a) => a.pass);
        results.push({
          name: 'states-from-manifest',
          ok: labelsOk && assertsOk,
          detail: labelsOk && assertsOk
            ? `${snaps.length} states captured (${labels.join(', ')}), ${manifestAsserts.length} marker asserts all held (incl. 1 driven via the \`drive\` escape hatch), cannotForce skip verified`
            : `labels=${JSON.stringify(labels)} asserts=${JSON.stringify(manifestAsserts)}`,
        });
      } finally {
        if (prevEnv === undefined) delete process.env.VISUAL_STATES; else process.env.VISUAL_STATES = prevEnv;
      }
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  } finally {
    await browser.close();
  }
  return { ok: results.every((c) => c.ok), results };
}
