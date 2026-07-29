// Mosaic compositor — many captured frames onto one contact sheet, for the overflow-router read.
//
// THE BINDING CONSTRAINT — read this before changing any number here: an agent's image-Read path
// DOWNSCALES anything whose longest side exceeds ~1568px. A contact sheet that ignores that ceiling
// gets crushed on the way in and loses exactly the per-tile signal it was built to carry, silently.
// So the sheet is planned to fit inside `maxSide` (default 1568) in BOTH dimensions, and a set of
// frames that cannot fit produces MORE sheets — never one oversized sheet.
//
// MECHANISM (calibration-validated, dependency-free): each capture is sliced into viewport-height
// segments purely by CSS `background-position` — no image-processing library, no new dependency.
// Tile height is fixed (`tileH`); tile width preserves the cell's viewport aspect, so one tile is
// exactly one scaled viewport screenful, and the reader can reason about what a user would see
// without scrolling. The sheet HTML is re-screenshotted by the same Playwright harness that took
// the frames, so there is one rendering path, not two.
//
// Two fixes the calibration run earned, both load-bearing:
//   1. TRIM THE FINAL TILE to the page's actual remainder. A padded blank final tile reads as a
//      blank region of the app and caused a false escalation — the reader cannot tell "page ended"
//      from "content failed to render".
//   2. OPTIONAL CONTENT CROP (`measureContentBox`) — a centered layout on a wide viewport spends
//      most of the tile on empty margin, which is the same as throwing away resolution.
//
// ROLE (frozen by the same calibration): the mosaic is an overflow ROUTER, not a verdict surface —
// per tile it may only clear or escalate, and an escalated cell is read at full resolution before
// any verdict. That discipline lives with the caller; this module only builds the sheet.

// PNG dimensions straight from the IHDR chunk (stdlib only — the file's first 24 bytes).
export function pngSize(buf) {
  if (!buf || buf.length < 24) throw new Error('not a PNG (file shorter than a PNG header)');
  const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  for (let i = 0; i < 8; i++) if (buf[i] !== sig[i]) throw new Error('not a PNG (bad signature)');
  if (buf.toString('latin1', 12, 16) !== 'IHDR') throw new Error('not a PNG (first chunk is not IHDR)');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

// Slice ONE capture into segment tiles. See the header for the two calibration fixes.
// src: {name, href, pngW, pngH, vw, vh, dsf, crop?: {x, width}} — vw/vh are the cell's CSS viewport,
// crop is a horizontal content window in PNG pixels.
export function planTiles(src, { tileH = 400, minSegment = 24 } = {}) {
  const dsf = src.dsf > 0 ? src.dsf : 1;
  const srcX = src.crop ? src.crop.x : 0;
  const srcW = src.crop ? src.crop.width : src.pngW;
  const cssW = srcW / dsf;
  const vh = src.vh > 0 ? src.vh : cssW; // square fallback: unknown viewport height is a caller bug
  let tileW = Math.max(1, Math.round(tileH * (cssW / vh)));
  const scale = tileW / srcW;
  const scaledH = src.pngH * scale;

  let nSeg = Math.max(1, Math.ceil(scaledH / tileH));
  let lastH = Math.round(scaledH - (nSeg - 1) * tileH);
  // A sliver final tile is noise; fold it into the previous segment rather than emitting a 3px
  // strip (and never pad it out to full height — that is the blank-tile false escalation).
  if (nSeg > 1 && lastH < minSegment) { nSeg -= 1; lastH = tileH + lastH; }
  lastH = Math.max(1, lastH);

  const tiles = [];
  for (let i = 0; i < nSeg; i++) {
    tiles.push({
      name: src.name,
      href: src.href,
      w: tileW,
      h: i === nSeg - 1 ? lastH : tileH,
      bgW: Math.round(src.pngW * scale),   // background-size width; with a crop it exceeds the tile
      bgX: -Math.round(srcX * scale),
      bgY: -(i * tileH),
      caption: `${src.name} · seg ${i + 1}/${nSeg}`,
    });
  }
  return tiles;
}

// Pack segment tiles into sheets that respect `maxSide` on both axes. Tiles are placed at absolute
// coordinates by the planner (not left to flex reflow) so the plan IS the rendered geometry and can
// be asserted without a browser.
export function planSheets(sources, { tileH = 400, gap = 8, pad = 8, maxSide = 1568, captionH = 14, group = '' } = {}) {
  const budget = maxSide - 2 * pad;
  const all = [];
  for (const src of sources) {
    let t = planTiles(src, { tileH });
    if (t.length && t[0].w > budget) {
      // One screenful wider than the whole sheet: shrink the tile uniformly (this keeps the
      // one-tile-is-one-screenful invariant; it only costs resolution).
      const f = budget / t[0].w;
      t = planTiles(src, { tileH: Math.max(1, Math.floor(tileH * f)) });
    }
    all.push(...t);
  }

  const sheets = [];
  let sheet = null, row = [], rowW = 0, y = pad;
  const closeRow = () => {
    if (!row.length) return;
    const rowH = Math.max(...row.map((t) => t.h)) + captionH;
    if (sheet && y + rowH + pad > maxSide) { sheets.push(sheet); sheet = null; }
    if (!sheet) { sheet = { index: sheets.length, group, width: 0, height: 0, tiles: [] }; y = pad; }
    let x = pad;
    for (const t of row) { sheet.tiles.push({ ...t, x, y }); x += t.w + gap; }
    sheet.width = Math.max(sheet.width, x - gap + pad);
    y += rowH + gap;
    sheet.height = y - gap + pad;
    row = []; rowW = 0;
  };
  for (const t of all) {
    const need = (rowW ? gap : 0) + t.w;
    if (rowW && rowW + need > budget) closeRow();
    // NOT `rowW += need`: closeRow() may have just reset rowW to 0, and the gap term has to be
    // re-evaluated against the row this tile actually lands in.
    row.push(t); rowW += (rowW ? gap : 0) + t.w;
  }
  closeRow();
  if (sheet) sheets.push(sheet);
  return sheets;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// Render one planned sheet. Hidden <img> preloads exist so the harness can wait on a deterministic
// signal — CSS background images alone give no per-image load handle to await.
export function sheetHtml(sheet, { title = 'mosaic', captionH = 14, background = '#7a7a7a' } = {}) {
  const srcs = [...new Set(sheet.tiles.map((t) => t.href))];
  const tiles = sheet.tiles.map((t) => (
    `<div class="t" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;` +
    `background-image:url('${esc(t.href)}');background-size:${t.bgW}px auto;` +
    `background-position:${t.bgX}px ${t.bgY}px"></div>` +
    `<div class="c" style="left:${t.x}px;top:${t.y + t.h}px;width:${t.w}px">${esc(t.caption)}</div>`
  )).join('\n');
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
  html,body { margin:0; padding:0; background:${background}; }
  body { width:${sheet.width}px; height:${sheet.height}px; position:relative;
         font:11px/1.2 Consolas,'DejaVu Sans Mono',monospace; }
  .t { position:absolute; background-repeat:no-repeat; background-color:#fff; outline:1px solid #2a2a2a; }
  .c { position:absolute; height:${captionH}px; color:#fff; overflow:hidden; white-space:nowrap; }
  #preload { position:absolute; left:-9999px; top:0; }
</style></head><body>
${tiles}
<div id="preload">${srcs.map((s) => `<img src="${esc(s)}">`).join('')}</div>
</body></html>`;
}

// Horizontal content window of a capture, measured in-browser (canvas pixel scan — same
// no-dependency posture as capture.mjs). Returns {x, width} in PNG pixels, or null when the frame
// already fills its width. Horizontal ONLY: trimming empty side margins buys tile resolution,
// while trimming vertically would shift segment boundaries away from real viewport scrolls.
export async function measureContentBox(browser, pngBuf, { tolerance = 12, padding = 8, sampleWidth = 800 } = {}) {
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    const dataUrl = `data:image/png;base64,${pngBuf.toString('base64')}`;
    return await page.evaluate(async (arg) => {
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = arg.dataUrl; });
      const scale = Math.min(1, arg.sampleWidth / img.naturalWidth);
      const w = Math.max(1, Math.round(img.naturalWidth * scale));
      const h = Math.max(1, Math.round(img.naturalHeight * scale));
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(img, 0, 0, w, h);
      const d = g.getImageData(0, 0, w, h).data;
      const at = (x, y) => { const i = (y * w + x) * 4; return [d[i], d[i + 1], d[i + 2]]; };
      const bg = at(0, 0);
      const step = Math.max(1, Math.floor(h / 200));
      let minX = -1, maxX = -1;
      for (let x = 0; x < w; x++) {
        let content = false;
        for (let y = 0; y < h; y += step) {
          const p = at(x, y);
          if (Math.abs(p[0] - bg[0]) > arg.tol || Math.abs(p[1] - bg[1]) > arg.tol || Math.abs(p[2] - bg[2]) > arg.tol) { content = true; break; }
        }
        if (content) { if (minX < 0) minX = x; maxX = x; }
      }
      if (minX < 0 || (minX === 0 && maxX === w - 1)) return null;
      const inv = 1 / scale;
      const x0 = Math.max(0, Math.round(minX * inv) - arg.padding);
      const x1 = Math.min(img.naturalWidth, Math.round((maxX + 1) * inv) + arg.padding);
      if (x1 - x0 >= img.naturalWidth) return null;
      return { x: x0, width: Math.max(1, x1 - x0) };
    }, { dataUrl, tol: tolerance, padding, sampleWidth });
  } finally {
    await ctx.close();
  }
}
