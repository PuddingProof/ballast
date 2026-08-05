// `measure` — deterministic instruments on demand.
//
// WHY THIS EXISTS: rung 0 (lib/assertions.mjs) is an AUTO-SCAN — it tells you where to look, over a
// whole page, at shutter time. What a reviewer actually needs next is a TARGETED query: "what is the
// real contrast of this label", "do these two boxes overlap", "is this tap target big enough". With
// no verb for that, every review authored its own throwaway measure.mjs/contrast.mjs — the single
// largest waste class in the measured baseline, and one that shipped the same two bugs every time:
//   1. a `color(srgb …)` computed value breaks an rgb()-shaped regex, silently yielding no reading;
//   2. a text rect that overflows an `text-overflow: ellipsis` box is a PHANTOM — the glyphs are not
//      painted there, so overlap arithmetic on it invents collisions that no eye can confirm.
// Both are closed here, once, and pinned by the suite's seeded+clean corpus.
//
// CONTRAST IS MEASURED FROM PIXELS, not from computed style: the element is clipped, re-rendered
// through a canvas, and the composited foreground/background pair is read out of the real image
// (the same in-browser, dependency-free recipe as capture.mjs's aHash). That is robust to gradients,
// backdrop filters, transparency stacks and any color syntax the engine may invent. The declared
// (computed-style) arithmetic is reported ALONGSIDE it as a second arm, never instead of it.
//
// EARN-IN POSTURE (design §4.9): findings from this verb are trusted for filed findings only once
// its seeded+clean corpus suite passes — the same shadow-then-trust ladder rung 0 walked.

import fs from 'fs';
import path from 'path';

export const MEASURE_CHECKS = ['contrast', 'rects', 'fonts', 'targets', 'overflow'];

// AA/AAA thresholds and the small-text floor, named once so the page function and the node side
// cannot drift apart.
export const MEASURE_THRESHOLDS = {
  contrastNormal: 4.5, contrastLarge: 3,
  smallTextPx: 12,        // below this, body text is a legibility finding regardless of contrast
  tightLineHeight: 1.1,   // line-height/font-size ratio below which lines collide
  minTargetPx: 24,        // WCAG 2.5.8 AA minimum hit target
  comfortTargetPx: 44,    // AAA / platform-guideline comfort size (reported, never a finding)
};

// ---------------------------------------------------------------------------------------------
// page side — SELF-CONTAINED (serialized via Function.prototype.toString, like RUNG0_PAGE_FN):
// no closures, no imports, no module-scope references.
// ---------------------------------------------------------------------------------------------
export const MEASURE_PAGE_FN = function collectMeasurements(options) {
  const o = options || {};
  const selectors = Array.isArray(o.selectors) && o.selectors.length ? o.selectors : ['body'];
  const checks = {};
  for (const c of (o.checks || [])) checks[c] = true;
  const TH = o.thresholds || {};
  const MAX_TARGETS = o.maxTargets || 40;
  const CAP = o.perCheckCap || 25;

  const findings = [];
  const counts = {};
  function add(check, selector, description, measured) {
    counts[check] = (counts[check] || 0) + 1;
    if (counts[check] > CAP) return;
    findings.push({ check: check, selector: selector, description: description, measured: measured || {} });
  }

  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el === document.documentElement) return 'html';
    if (el === document.body) return 'body';
    const parts = [];
    let node = el, depth = 0;
    while (node && node.nodeType === 1 && depth < 5) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      let part = String(node.tagName || '').toLowerCase();
      const cls = String((node.getAttribute && node.getAttribute('class')) || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      else if (node.parentElement) {
        const sibs = Array.prototype.filter.call(node.parentElement.children, function (c) { return c.tagName === node.tagName; });
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      if (!node.parentElement || node.parentElement === document.body) break;
      node = node.parentElement; depth++;
    }
    return parts.join(' > ');
  }

  // ---- color arithmetic (the DECLARED arm) --------------------------------------------------
  // Handles rgb()/rgba() AND the modern `color(srgb r g b / a)` form. The second one is the known
  // break: its channels are 0–1 floats and an rgb-shaped regex simply misses, which reads as "no
  // measurement" — indistinguishable from "no problem" to a caller that doesn't check.
  function parseColor(str) {
    if (!str) return null;
    const s = String(str).trim();
    const srgb = s.match(/^color\(\s*srgb\s+([^)]+)\)$/i);
    if (srgb) {
      const p = srgb[1].split(/[\s/]+/).filter(Boolean).map(function (v) {
        return v.indexOf('%') >= 0 ? parseFloat(v) / 100 : parseFloat(v);
      });
      if (p.length < 3 || !isFinite(p[0])) return null;
      return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 && isFinite(p[3]) ? p[3] : 1, syntax: 'color(srgb)' };
    }
    const m = s.match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(function (v) {
      return v.indexOf('%') >= 0 ? parseFloat(v) / 100 : parseFloat(v);
    });
    if (p.length < 3 || !isFinite(p[0])) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 && isFinite(p[3]) ? p[3] : 1, syntax: 'rgb()' };
  }
  function over(top, bottom) {
    const a = top.a;
    return { r: top.r * a + bottom.r * (1 - a), g: top.g * a + bottom.g * (1 - a), b: top.b * a + bottom.b * (1 - a), a: 1 };
  }
  function luminance(c) {
    function ch(v) { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); }
    return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
  }
  function contrastOf(a, b) {
    const l1 = luminance(a), l2 = luminance(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function rgbStr(c) { return 'rgb(' + Math.round(c.r) + ', ' + Math.round(c.g) + ', ' + Math.round(c.b) + ')'; }
  function effectiveBg(el) {
    const stack = [];
    let node = el, imaged = false;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') { imaged = true; break; }
      const c = parseColor(cs.backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 0.999) break; }
      node = node.parentElement;
    }
    if (imaged) return null;
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = stack.length - 1; i >= 0; i--) base = over(stack[i], base);
    return base;
  }

  // ---- geometry -----------------------------------------------------------------------------
  function rectObj(r) {
    return { x: Math.round(r.left * 100) / 100, y: Math.round(r.top * 100) / 100,
      width: Math.round(r.width * 100) / 100, height: Math.round(r.height * 100) / 100,
      right: Math.round(r.right * 100) / 100, bottom: Math.round(r.bottom * 100) / 100 };
  }
  function intersect(a, b) {
    const left = Math.max(a.left, b.left), top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right), bottom = Math.min(a.bottom, b.bottom);
    if (right <= left || bottom <= top) return null;
    return { left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top };
  }
  // Nearest ancestor (inclusive) that CLIPS: the box beyond which this element's ink is not painted.
  function clipBox(el) {
    let box = { left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity };
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      const cs = getComputedStyle(node);
      const clipsX = cs.overflowX === 'hidden' || cs.overflowX === 'clip' || cs.overflowX === 'auto' || cs.overflowX === 'scroll';
      const clipsY = cs.overflowY === 'hidden' || cs.overflowY === 'clip' || cs.overflowY === 'auto' || cs.overflowY === 'scroll';
      if (clipsX || clipsY) {
        const r = node.getBoundingClientRect();
        const hit = intersect(box, { left: clipsX ? r.left : box.left, right: clipsX ? r.right : box.right,
          top: clipsY ? r.top : box.top, bottom: clipsY ? r.bottom : box.bottom });
        if (!hit) return null;
        box = hit;
      }
      node = node.parentElement;
    }
    return box;
  }
  function isEllipsized(el, cs) {
    return cs.textOverflow === 'ellipsis' && el.scrollWidth - el.clientWidth >= 1;
  }
  function hasOwnText(el) {
    const kids = el.childNodes;
    for (let i = 0; i < kids.length; i++) {
      if (kids[i].nodeType === 3 && kids[i].nodeValue && kids[i].nodeValue.trim().length) return true;
    }
    return false;
  }

  // ---- target census ------------------------------------------------------------------------
  const els = [];
  const seen = [];
  for (let si = 0; si < selectors.length; si++) {
    let matched = [];
    try { matched = Array.prototype.slice.call(document.querySelectorAll(selectors[si])); }
    catch (e) { add('selector', selectors[si], 'selector could not be parsed by the browser: ' + e.message, {}); continue; }
    if (!matched.length) add('selector', selectors[si], 'selector matched no element on this page', {});
    for (let i = 0; i < matched.length && els.length < MAX_TARGETS; i++) {
      if (seen.indexOf(matched[i]) >= 0) continue;
      seen.push(matched[i]);
      els.push({ el: matched[i], selector: selectors[si] });
    }
  }

  const targets = [];
  const contrastQueue = [];   // {index, rect} — the node side screenshots these for the PIXEL arm
  for (let i = 0; i < els.length; i++) {
    const el = els[i].el;
    const cs = getComputedStyle(el);
    const raw = el.getBoundingClientRect();
    const clip = clipBox(el);
    const vis = clip ? intersect(raw, clip) : null;
    const size = parseFloat(cs.fontSize) || 16;
    const weightRaw = cs.fontWeight;
    const weight = weightRaw === 'bold' ? 700 : weightRaw === 'normal' ? 400 : (parseInt(weightRaw, 10) || 400);
    const lh = cs.lineHeight === 'normal' ? size * 1.2 : (parseFloat(cs.lineHeight) || size * 1.2);
    const ellipsized = isEllipsized(el, cs);
    const visible = cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) !== 0 && raw.width > 0 && raw.height > 0;

    const t = {
      index: i, selector: els[i].selector, path: selectorFor(el), tag: String(el.tagName || '').toLowerCase(),
      visible: visible, text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
      rect: rectObj(raw),
      // The PAINTED box: the layout rect intersected with every clipping ancestor. This is what an
      // eye can confirm — and the only rect overlap arithmetic may use.
      visualRect: vis ? rectObj(vis) : null,
      ellipsized: ellipsized,
      phantomWidthPx: ellipsized ? Math.round((el.scrollWidth - el.clientWidth) * 100) / 100 : 0,
    };

    if (checks.fonts) {
      t.font = { fontSizePx: Math.round(size * 100) / 100, fontWeight: weight, fontFamily: cs.fontFamily,
        lineHeightPx: Math.round(lh * 100) / 100, lineHeightRatio: Math.round((lh / size) * 100) / 100,
        letterSpacing: cs.letterSpacing, textTransform: cs.textTransform };
      if (visible && hasOwnText(el)) {
        if (size < (TH.smallTextPx || 12)) {
          add('fonts', t.path, 'text renders at ' + t.font.fontSizePx + 'px, below the ' + (TH.smallTextPx || 12) + 'px legibility floor',
            { fontSizePx: t.font.fontSizePx, floorPx: TH.smallTextPx || 12, fontWeight: weight });
        }
        if (t.font.lineHeightRatio < (TH.tightLineHeight || 1.1)) {
          add('fonts', t.path, 'line-height is ' + t.font.lineHeightRatio + '× the font size — lines collide',
            { lineHeightPx: t.font.lineHeightPx, fontSizePx: t.font.fontSizePx, ratio: t.font.lineHeightRatio });
        }
      }
    }

    if (checks.overflow) {
      const ox = cs.overflowX;
      const d = el.scrollWidth - el.clientWidth;
      t.overflow = { overflowX: ox, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowPx: d,
        scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
      if (visible && d >= 2 && (ox === 'hidden' || ox === 'clip' || ox === 'auto' || ox === 'scroll')) {
        add('overflow', t.path, 'content overflows its ' + ox + ' box horizontally by ' + d + 'px',
          { overflowPx: d, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowX: ox });
      }
    }

    if (checks.contrast && visible && hasOwnText(el)) {
      const fg = parseColor(cs.color);
      const bg = effectiveBg(el);
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const need = large ? (TH.contrastLarge || 3) : (TH.contrastNormal || 4.5);
      t.contrast = { required: need, largeText: large, fontSizePx: Math.round(size * 100) / 100, fontWeight: weight,
        declared: null, colorSyntax: fg ? fg.syntax : null };
      if (fg && bg) {
        const text = fg.a >= 0.999 ? fg : over(fg, bg);
        t.contrast.declared = { ratio: Math.round(contrastOf(text, bg) * 100) / 100, color: rgbStr(text), background: rgbStr(bg) };
      }
      // The pixel arm needs a real painted box, in viewport coordinates, that a clip screenshot can
      // reach. Elements scrolled out of view or fully clipped are reported unmeasurable instead.
      if (vis && vis.width >= 2 && vis.height >= 2) {
        contrastQueue.push({ index: i, rect: { x: vis.left, y: vis.top, width: vis.width, height: vis.height } });
      } else {
        t.contrast.pixel = { measurable: false, why: 'no painted box in the viewport (scrolled out or fully clipped)' };
      }
    }

    if (checks.targets) {
      const INTERACTIVE = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=checkbox], [role=switch], [role=tab], [role=menuitem]';
      let hits = [];
      try { hits = Array.prototype.slice.call(el.querySelectorAll(INTERACTIVE), 0, 60); } catch (e) { hits = []; }
      if (el.matches && el.matches(INTERACTIVE)) hits.unshift(el);
      const min = TH.minTargetPx || 24;
      t.targets = [];
      for (let k = 0; k < hits.length; k++) {
        const hcs = getComputedStyle(hits[k]);
        if (hcs.display === 'none' || hcs.visibility === 'hidden') continue;
        const hr = hits[k].getBoundingClientRect();
        if (!(hr.width > 0 && hr.height > 0)) continue;
        const rec = { path: selectorFor(hits[k]), width: Math.round(hr.width * 100) / 100, height: Math.round(hr.height * 100) / 100,
          meetsAA: hr.width >= min && hr.height >= min, meetsComfort: hr.width >= (TH.comfortTargetPx || 44) && hr.height >= (TH.comfortTargetPx || 44) };
        t.targets.push(rec);
        if (!rec.meetsAA) {
          add('targets', rec.path, 'hit target is ' + rec.width + '×' + rec.height + 'px, below the ' + min + '×' + min + 'px minimum',
            { width: rec.width, height: rec.height, minimumPx: min });
        }
      }
    }

    targets.push(t);
  }

  // ---- rects: pairwise overlap on PAINTED boxes only ----------------------------------------
  if (checks.rects) {
    const box = [];
    for (let i = 0; i < targets.length; i++) {
      const t = targets[i];
      if (!t.visible || !t.visualRect) continue;
      box.push({ i: i, r: { left: t.visualRect.x, top: t.visualRect.y, right: t.visualRect.right, bottom: t.visualRect.bottom } });
    }
    for (let i = 0; i < box.length; i++) {
      for (let j = i + 1; j < box.length; j++) {
        const a = targets[box[i].i], b = targets[box[j].i];
        // Nesting is not a collision: an ancestor's box legitimately contains its descendant's.
        if (a.path && b.path && (b.path.indexOf(a.path) === 0 || a.path.indexOf(b.path) === 0)) continue;
        const hit = intersect(box[i].r, box[j].r);
        if (!hit || hit.width <= 1.5 || hit.height <= 1.5) continue;
        add('rects', a.path, 'painted box overlaps ' + b.path + ' by ' + Math.round(hit.width) + '×' + Math.round(hit.height) + 'px',
          { withSelector: b.path, overlapWidth: Math.round(hit.width * 100) / 100, overlapHeight: Math.round(hit.height * 100) / 100,
            note: (a.ellipsized || b.ellipsized) ? 'measured on the clipped painted box, not the overflowing text rect' : '' });
      }
    }
  }

  const de = document.documentElement;
  return {
    url: location.href,
    viewport: { width: de.clientWidth, height: de.clientHeight, dpr: window.devicePixelRatio,
      pageOverflowPx: Math.max(0, de.scrollWidth - de.clientWidth) },
    targets: targets, findings: findings, contrastQueue: contrastQueue,
    capped: Object.keys(counts).filter(function (k) { return counts[k] > CAP; }),
  };
};

// ---------------------------------------------------------------------------------------------
// node side
// ---------------------------------------------------------------------------------------------

// Read the composited foreground/background pair straight out of a rendered clip.
//
// Method: exact-color histogram over the clip. The most frequent color is the background. The
// foreground is the color FURTHEST from it in luminance among colors that clear a small population
// floor — the floor is what excludes antialiasing fringe (numerous in KINDS, thin in any single
// shade) and stray single-pixel artifacts, while staying low enough to find one line of text inside
// a wide block, where the ink is well under 2% of the clip. Both shares are reported so a caller can
// see how solid the reading is.
export async function pixelPair(browser, pngBuf, { minShare = 0.0002, minPixels = 8 } = {}) {
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    const src = `data:image/png;base64,${pngBuf.toString('base64')}`;
    return await page.evaluate(async (arg) => {
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = arg.src; });
      const w = img.naturalWidth, h = img.naturalHeight;
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, w, h).data;
      const counts = new Map();
      const total = w * h;
      for (let i = 0; i < d.length; i += 4) {
        const key = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
        counts.set(key, (counts.get(key) || 0) + 1);
      }
      const lum = (k) => {
        const ch = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
        return 0.2126 * ch((k >> 16) & 255) + 0.7152 * ch((k >> 8) & 255) + 0.0722 * ch(k & 255);
      };
      let bg = null, bgN = 0;
      for (const [k, n] of counts) if (n > bgN) { bg = k; bgN = n; }
      if (bg === null) return null;
      const floor = Math.max(arg.minPixels, Math.floor(total * arg.minShare));
      let fg = null, fgN = 0, best = -1;
      const bgL = lum(bg);
      for (const [k, n] of counts) {
        if (k === bg || n < floor) continue;
        const dist = Math.abs(lum(k) - bgL);
        if (dist > best) { best = dist; fg = k; fgN = n; }
      }
      const str = (k) => `rgb(${(k >> 16) & 255}, ${(k >> 8) & 255}, ${k & 255})`;
      if (fg === null) {
        return { measurable: false, why: 'no second color holds enough pixels to be text (uniform region?)',
          background: str(bg), backgroundShare: Math.round((bgN / total) * 1000) / 1000, distinctColors: counts.size, pixels: total };
      }
      const l1 = lum(fg), l2 = bgL;
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      return { measurable: true, ratio: Math.round(ratio * 100) / 100, color: str(fg), background: str(bg),
        colorShare: Math.round((fgN / total) * 1000) / 1000, backgroundShare: Math.round((bgN / total) * 1000) / 1000,
        distinctColors: counts.size, pixels: total };
    }, { src, minShare, minPixels });
  } finally {
    await ctx.close();
  }
}

// Next `measure-<n>-<pid>.json` in the out dir — measurements accumulate across a review instead of
// overwriting each other, and `n` is the order they were taken in.
//
// The PID SUFFIX is what makes the name safe: scan-max+1 alone is a race — two measures started
// against one out-dir both scan the same directory, both compute the same n, and the second
// overwrites the first's record with no error anywhere. The ordinal stays for readability; the pid
// is what guarantees the writers never collide.
export function nextMeasureFile(outDir) {
  let n = 1;
  try {
    for (const f of fs.readdirSync(outDir)) {
      const m = /^measure-(\d+)(?:-\d+)?\.json$/.exec(f);
      if (m && +m[1] >= n) n = +m[1] + 1;
    }
  } catch { /* dir does not exist yet */ }
  const index = `${n}-${process.pid}`;
  return { file: path.join(outDir, `measure-${index}.json`), index };
}

export function parseChecks(spec) {
  if (!spec || spec === true) return [...MEASURE_CHECKS];
  const asked = String(spec).split(',').map((s) => s.trim()).filter(Boolean);
  const bad = asked.filter((c) => !MEASURE_CHECKS.includes(c));
  if (bad.length) throw new Error(`unknown --checks: ${bad.join(', ')} — pick from ${MEASURE_CHECKS.join(',')}`);
  return asked;
}

export function parseSelectors(opts) {
  const out = [];
  if (opts.selector && opts.selector !== true) out.push(String(opts.selector));
  if (opts.selectors && opts.selectors !== true) out.push(...String(opts.selectors).split(',').map((s) => s.trim()).filter(Boolean));
  return out.length ? [...new Set(out)] : ['body'];
}
