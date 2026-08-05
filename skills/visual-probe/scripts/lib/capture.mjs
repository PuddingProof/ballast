// Capture + image-analysis primitives. All image work is done INSIDE the browser (canvas / <img>)
// so the tool stays dependency-free beyond Playwright itself — no sharp, no pixelmatch, no jimp.

import fs from 'fs';
import path from 'path';

// Default capture directory: a namespaced OS-temp dir, NEVER the repo working tree — so frames
// (look/shot/run PNGs + manifest) can't be accidentally git-added or committed. `session start`/`stop`
// wipe it so it doesn't accumulate; pass `--out <dir>` to keep frames somewhere durable on purpose.
// DEFINED in lib/budget.mjs (stdlib-only, so probe.mjs's pre-import bootstrap can resolve the same
// default) and re-exported here, where every existing caller already imports it.
export { DEFAULT_OUT } from './budget.mjs';

// Capture a screenshot at the current cell's NATIVE device resolution.
// If `crop` (a CSS selector) is given, clip to that element's bounding box; else full page.
export async function captureFrame(page, { crop, fullPage = true } = {}) {
  if (crop) {
    const el = await page.$(crop);
    if (!el) throw new Error(`crop selector not found: ${crop}`);
    const box = await el.boundingBox();
    if (!box) throw new Error(`crop selector has no layout box: ${crop}`);
    return await page.screenshot({
      clip: { x: box.x, y: box.y, width: Math.max(1, Math.ceil(box.width)), height: Math.max(1, Math.ceil(box.height)) },
    });
  }
  return await page.screenshot({ fullPage });
}

// Nearest-neighbor magnify a PNG buffer by an integer factor, reusing a browser (no image lib).
//
// WHY: the agent's own image-Read path DOWNSCALES. A sub-pixel rendering defect is invisible in
// the inline thumbnail. Re-rendering the PNG through an <img style="image-rendering:pixelated">
// at N× and re-screenshotting yields a crisp nearest-neighbor blow-up where the defect is obvious.
export async function magnify(browser, pngBuf, factor, outFile) {
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    const b64 = pngBuf.toString('base64');
    await page.setContent(
      `<body style="margin:0;background:#888">
         <img id="m" src="data:image/png;base64,${b64}"
              style="image-rendering:pixelated;display:block">
       </body>`);
    const dims = await page.evaluate((f) => {
      const img = document.getElementById('m');
      img.width = img.naturalWidth * f;
      img.height = img.naturalHeight * f;
      return { w: img.width, h: img.height };
    }, factor);
    // Chromium caps screenshots near ~16k px per side; clamp so huge magnifications don't throw.
    const w = Math.min(dims.w, 16000), h = Math.min(dims.h, 16000);
    await page.setViewportSize({ width: w, height: h });
    const out = await page.screenshot({ clip: { x: 0, y: 0, width: w, height: h } });
    fs.writeFileSync(outFile, out);
    return outFile;
  } finally {
    await ctx.close();
  }
}

// 8×8 average-hash of a PNG, computed in-browser via canvas.
//
// aHash downscales to 8×8 grayscale and thresholds at the mean, so it is SIZE-NORMALIZED:
// two correctly-rendered frames at different device-scale-factors hash SIMILARLY (same layout),
// while a scale-specific rendering defect shifts the hash. That makes cross-cell Hamming distance
// a cheap, honest "these two cells render differently" signal — NOT a pixel-perfect diff.
export async function aHash(browser, pngBuf) {
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    const src = `data:image/png;base64,${pngBuf.toString('base64')}`;
    return await page.evaluate(async (dataUrl) => {
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = dataUrl; });
      const N = 8;
      const c = document.createElement('canvas'); c.width = N; c.height = N;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0, N, N);
      const d = g.getImageData(0, 0, N, N).data;
      const gray = [];
      for (let i = 0; i < d.length; i += 4) gray.push(d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114);
      const mean = gray.reduce((a, b) => a + b, 0) / gray.length;
      return gray.map((v) => (v >= mean ? '1' : '0')).join('');
    }, src);
  } finally {
    await ctx.close();
  }
}

// Write every captured frame, magnify the cropped ones, hash them all, and score cross-cell
// divergence — the manifest's `snapshots` array, built once for BOTH callers: the classic
// shot/run `flush()` (which hands in its dedicated post-processing browser) and the fused verbs
// (which hand in the capture browser they are already holding). One implementation, so the
// divergence arithmetic cannot drift between the two paths.
export async function buildEntries({ browser, snapshots, outDir, magnifyFactor = 8, threshold = 6, baselineLabel, log = () => {} }) {
  const byLabel = {};
  for (const s of snapshots) (byLabel[s.label] ||= []).push(s);

  const out = [];
  for (const [label, snaps] of Object.entries(byLabel)) {
    const entries = [];
    for (const s of snaps) {
      const base = `${label}__${s.cell.label}`.replace(/[^\w.@-]/g, '_');
      const nativeFile = path.join(outDir, `${base}.png`);
      fs.writeFileSync(nativeFile, s.buf);

      // Magnify ONLY cropped snapshots (a full-page 8× blow-up would be enormous and useless).
      let magnified = null;
      if (s.opts?.crop) {
        try { magnified = await magnify(browser, s.buf, magnifyFactor, path.join(outDir, `${base}.x${magnifyFactor}.png`)); }
        catch (e) { log('magnify failed for', base, '-', e.message); }
      }

      let hash = null;
      try { hash = await aHash(browser, s.buf); } catch (e) { log('hash failed for', base, '-', e.message); }

      entries.push({ cell: s.cell.label, dsf: s.cell.dsf, file: nativeFile, magnified, bytes: s.buf.length, aHash: hash });
    }

    // Cross-cell divergence vs the baseline cell (size-normalized aHash Hamming distance).
    const baseEntry = entries.find((e) => e.cell === baselineLabel) || entries[0];
    for (const e of entries) {
      const d = hamming(e.aHash, baseEntry.aHash);
      e.divergenceVsBaseline = Number.isFinite(d) ? d : null;
      e.diverges = e.cell !== baseEntry.cell && Number.isFinite(d) && d > threshold;
    }
    out.push({ label, baseline: baseEntry.cell, entries });
  }
  return out;
}

// Hamming distance between two equal-length bit strings (with a length-mismatch penalty).
export function hamming(a, b) {
  if (!a || !b) return Infinity;
  let d = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) d++;
  return d + Math.abs(a.length - b.length);
}
