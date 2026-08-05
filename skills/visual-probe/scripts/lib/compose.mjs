// Contact-sheet composition — the layer between the mosaic's pure layout math (lib/mosaic.mjs) and
// its two callers: the standalone `compose <out-dir>` command (reads a finished capture off disk)
// and the fused verbs (compose in the same process, on the browser they are already holding).
//
// Extracted so BOTH callers slice, pack, render and re-screenshot through one code path: the sheet a
// leaf reads must not depend on which verb produced it.

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
import { pngSize, planSheets, sheetHtml, measureContentBox, tallyTiles } from './mosaic.mjs';

// Turn a finished capture manifest into composable sources. Cell geometry comes from the manifest's
// own record first, then a WxH@DSF cell label, then the frame is SKIPPED LOUDLY — a guessed aspect
// would silently mis-slice every segment of it.
export function sourcesFromManifest(m, dir, { groupRe = null, groupOf = null } = {}) {
  const geo = new Map((m.cellGeometry || []).map((c) => [c.label, c]));
  const sources = [];
  const skipped = [];
  for (const snap of m.snapshots || []) {
    for (const e of snap.entries || []) {
      const name = `${snap.label} · ${e.cell}`;
      const file = path.isAbsolute(e.file) ? e.file : path.join(dir, e.file);
      if (!fs.existsSync(file)) { skipped.push({ name, reason: `frame not on disk: ${file}` }); continue; }
      let g = geo.get(e.cell);
      if (!g) { const mm = /(\d+)x(\d+)@([\d.]+)/.exec(e.cell); if (mm) g = { width: +mm[1], height: +mm[2], dsf: +mm[3] }; }
      if (!g || !(g.width > 0) || !(g.height > 0)) {
        skipped.push({ name, reason: `no viewport geometry for cell "${e.cell}" (no cellGeometry in the manifest and the label is not WxH@DSF)` });
        continue;
      }
      const dims = pngSize(fs.readFileSync(file));
      const hit = groupRe ? groupRe.exec(name) : null;
      sources.push({
        group: groupRe ? (hit ? (hit[1] ?? hit[0]) : 'ungrouped') : (groupOf ? groupOf(snap.label, e.cell) : ''),
        name, file, href: encodeURI(path.relative(dir, file).split(path.sep).join('/')),
        pngW: dims.width, pngH: dims.height,
        vw: g.width, vh: g.height, dsf: g.dsf || 1,
      });
    }
  }
  return { sources, skipped };
}

// A coverage hole as a composable source: one labeled placeholder tile, grouped alongside the
// frames it sits between so the gap lands where the reader is already looking.
export function placeholderSource({ name, reason, group = '', vw = 0, vh = 0 }) {
  return { placeholder: true, name, reason, group, vw, vh };
}

// Plan, render and re-screenshot the sheets for one set of sources (already grouped). Returns the
// per-sheet records plus a tally that counts CAPTURED tiles and placeholders separately.
export async function renderSheets({ browser, dir, sources, tileH = 400, maxSide = 1568, cropContent = false, timeout = 30000, log = () => {} }) {
  const out = [];
  const planned = [];

  if (cropContent) {
    for (const s of sources) {
      if (s.placeholder) continue;
      try {
        const box = await measureContentBox(browser, fs.readFileSync(s.file));
        if (box) s.crop = box;
      } catch (e) { log('content-crop measure failed for', s.name, '-', e.message); }
    }
  }

  for (const grp of [...new Set(sources.map((s) => s.group))]) {
    const sheets = planSheets(sources.filter((s) => s.group === grp), { tileH, maxSide, group: grp });
    planned.push(...sheets);
    for (const sheet of sheets) {
      const stem = ['mosaic', grp, sheets.length > 1 ? String(sheet.index + 1) : '']
        .filter(Boolean).join('-').replace(/[^\w.@-]/g, '_');
      const htmlFile = path.join(dir, `${stem}.html`);
      fs.writeFileSync(htmlFile, sheetHtml(sheet, { title: stem }));
      const ctx = await browser.newContext({ viewport: { width: sheet.width, height: sheet.height }, deviceScaleFactor: 1 });
      try {
        const page = await ctx.newPage();
        page.setDefaultTimeout(timeout);
        await page.goto(pathToFileURL(htmlFile).href, { waitUntil: 'load' });
        // The tiles paint from CSS backgrounds, which give no per-image handle; the sheet
        // carries hidden <img> preloads of the same sources purely so this wait is real.
        await page.evaluate(async () => {
          await Promise.all(Array.from(document.images).map((i) => (i.complete ? null : new Promise((r) => { i.onload = i.onerror = r; }))));
        });
        // Same rename discipline as the manifest, and for the same reader: a sheet is the surface a
        // leaf Reads, and a poller (or a leaf whose --wait just resolved) must never open one
        // mid-write. Unique temp name per writer, so two processes can't share the scratch file.
        const pngFile = path.join(dir, `${stem}.png`);
        const pngTmp = `${pngFile}.${process.pid}.${Math.random().toString(36).slice(2, 8)}.tmp`;
        fs.writeFileSync(pngTmp, await page.screenshot({ clip: { x: 0, y: 0, width: sheet.width, height: sheet.height } }));
        fs.renameSync(pngTmp, pngFile);
        const t = tallyTiles([sheet]);
        out.push({
          group: grp || null, sheet: sheet.index + 1, of: sheets.length, file: pngFile, html: htmlFile,
          width: sheet.width, height: sheet.height, tiles: t.tiles, placeholders: t.placeholders,
        });
      } finally { await ctx.close(); }
    }
  }

  return { sheets: out, tally: tallyTiles(planned) };
}
