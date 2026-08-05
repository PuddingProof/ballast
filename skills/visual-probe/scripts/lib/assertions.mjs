// Rung-0 geometry assertions — deterministic, arithmetic defect pre-location.
//
// WHY: the ladder's cheapest rung. Six defect classes are pure arithmetic over layout and computed
// style, so they never need an eye, an image read, or a token: rect overlap, horizontal overflow of
// a clipping/scrolling box, AA contrast, broken assets, clipped/offscreen elements, and 1–3px
// sibling top-alignment mismatch in a flex/grid row. That last one is calibration-earned: a blind
// reader study found NO reader detects it from a full-page read at any tier, so arithmetic is its
// only cheap owner.
//
// SHADOW POSTURE (deliberate, not a TODO): findings are DATA. They never touch `pass`, never touch
// the process exit code, and never gate a verdict — the field ships shadow-logged so its true
// coverage fraction can be measured against real runs before anything is gated on it.
//
// WHERE IT RUNS: RUNG0_PAGE_FN is evaluated IN PAGE CONTEXT at shutter time (helpers.mjs `snapshot`),
// once per captured state × cell — so a finding is tied to the state that was actually on screen,
// not to whatever the page drifted into afterwards. It is serialized by Playwright via
// Function.prototype.toString, so it must stay SELF-CONTAINED: no module-scope references, no
// closures, no imports. Everything it needs is either a browser global or an argument.
//
// SUPPRESSIONS (frozen schema): a project's state manifest may carry a top-level array
//   suppressions: [{ "assert": "<check-id>", "selector": "<css>", "reason": "<why intended>" }]
// Intended-scroll containers and deliberate overlaps trip these checks routinely; without a
// suppression channel every run would escalate. Matching happens page-side, where a selector can
// actually be resolved: a suppression matches a finding when the finding's element matches the
// selector OR sits inside an element that does (`el.closest`), so a suppression can be written for
// the container without naming every child. A matched finding is KEPT and flagged
// `suppressed: true` with its reason — never dropped, so a suppression can itself be audited.
// A suppression that matches nothing all run is reported back as unused (a stale suppression is its
// own defect class), as is one whose selector the browser cannot parse.

export const RUNG0_CHECKS = ['overlap', 'overflow', 'contrast', 'broken-image', 'offscreen', 'misalignment'];

// AA thresholds (WCAG 2.x): 4.5:1 normal text, 3:1 large text (≥24px, or ≥18.66px at weight ≥700).
export const RUNG0_PAGE_FN = function collectRung0Findings(options) {
  const o = options || {};
  const CAP = o.perCheckCap || 25;
  const MAX_ELEMENTS = o.maxElements || 4000;
  const MAX_CHILDREN = o.maxChildrenPerParent || 24;
  const suppressions = Array.isArray(o.suppressions) ? o.suppressions : [];

  const findings = [];
  const counts = {};
  const overflowCap = {};
  const supCounts = {};          // suppressed findings get their own CAP budget (see add())
  const overflowSuppressed = {};
  const matched = [];
  const invalid = [];

  // ---- element identification -------------------------------------------------------------
  // Short, human-followable CSS-ish path (≤5 hops, stops at an id). It is a LABEL for a reader and
  // a suppression-writing hint — not a guaranteed-unique query string.
  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el === document.documentElement) return 'html';
    if (el === document.body) return 'body';
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 5) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      const tag = String(node.tagName || '').toLowerCase();
      let part = tag;
      const cls = String(node.getAttribute && node.getAttribute('class') || '')
        .trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      else if (node.parentElement) {
        const sibs = Array.prototype.filter.call(node.parentElement.children, function (c) { return c.tagName === node.tagName; });
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      if (!node.parentElement || node.parentElement === document.body) break;
      node = node.parentElement;
      depth++;
    }
    return parts.join(' > ');
  }

  // Matches are reported back as {assert, selector} PAIRS — not as array indices (a scenario may
  // append suppressions after the run starts, and an index would then name the wrong rule) and not
  // as a joined string (a selector can contain any separator you would pick).
  function note(list, s) {
    for (let i = 0; i < list.length; i++) if (list[i].assert === s.assert && list[i].selector === s.selector) return;
    list.push({ assert: s.assert, selector: s.selector });
  }
  function suppressionFor(check, el) {
    for (let i = 0; i < suppressions.length; i++) {
      const s = suppressions[i];
      if (!s || s.assert !== check || !s.selector) continue;
      let hit = false;
      try { hit = !!(el && el.nodeType === 1 && (el.matches(s.selector) || el.closest(s.selector))); }
      catch (e) { note(invalid, s); continue; }
      if (hit) {
        note(matched, s);
        return { reason: s.reason || '', selector: s.selector };
      }
    }
    return null;
  }

  function add(check, el, description, measured) {
    const f = { check: check, selector: selectorFor(el), description: description, measured: measured || {}, suppressed: false };
    const sup = suppressionFor(check, el);
    // Suppressed findings are budgeted SEPARATELY. A declared exception can repeat hundreds of
    // times (a grid of intentionally stacked badges); charging those to the defect budget lets
    // them push genuine unsuppressed findings into the rolled-up overflow bucket, where they
    // lose their selector and description — the cap would then hide exactly what it exists to
    // surface. Both budgets still report their overflow; neither drops silently.
    const bucket = sup ? supCounts : counts;
    bucket[check] = (bucket[check] || 0) + 1;
    if (bucket[check] > CAP) {
      const overflow = sup ? overflowSuppressed : overflowCap;
      overflow[check] = (overflow[check] || 0) + 1;
      return;
    }
    if (sup) { f.suppressed = true; f.suppressionReason = sup.reason; f.suppressedBy = sup.selector; }
    findings.push(f);
  }

  // ---- color arithmetic --------------------------------------------------------------------
  function parseColor(str) {
    if (!str) return null;
    const m = String(str).match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(function (v) {
      return v.indexOf('%') >= 0 ? parseFloat(v) / 100 : parseFloat(v);
    });
    if (p.length < 3 || !isFinite(p[0])) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 && isFinite(p[3]) ? p[3] : 1 };
  }
  function over(top, bottom) { // alpha-composite top onto an opaque bottom
    const a = top.a;
    return { r: top.r * a + bottom.r * (1 - a), g: top.g * a + bottom.g * (1 - a), b: top.b * a + bottom.b * (1 - a), a: 1 };
  }
  function luminance(c) {
    function ch(v) { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); }
    return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
  }
  function contrast(a, b) {
    const l1 = luminance(a), l2 = luminance(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function rgbStr(c) { return 'rgb(' + Math.round(c.r) + ', ' + Math.round(c.g) + ', ' + Math.round(c.b) + ')'; }

  // Effective background BEHIND an element: nearest painted ancestor color, alpha-composited down to
  // the first OPAQUE one in the stack. THREE ways the value is unresolvable, and every one of them
  // SKIPS the element rather than guessing:
  //   • a background-image/gradient anywhere in the stack (rung 2 measures those from pixels);
  //   • an ancestor that paints its own content — canvas, img, svg, video (the face of the page is
  //     that element's pixels, not a CSS color);
  //   • a transparency chain that runs off the top of the document without ever hitting an opaque
  //     color, i.e. the root itself has no background.
  // The last one is why this never falls back to white: a dark-faced app whose page color is painted
  // by a canvas reads as rgb(255,255,255) under that assumption, and every light-on-dark label on it
  // becomes a phantom AA finding (measured: 90+ false findings in a single run).
  const SELF_PAINTING_TAGS = { CANVAS: 1, IMG: 1, SVG: 1, VIDEO: 1, PICTURE: 1, OBJECT: 1, EMBED: 1 };
  function effectiveBg(el) {
    const stack = [];
    let node = el;
    let opaque = null;
    while (node && node.nodeType === 1) {
      if (SELF_PAINTING_TAGS[String(node.tagName || '').toUpperCase()]) return null;
      // Reuse the census's cached CSSStyleDeclaration where there is one (same pattern as
      // clippingAncestor): ancestor chains are shared across siblings, so a fresh
      // getComputedStyle per hop re-costs the same nodes once per descendant.
      const rec = byEl.get(node);
      const cs = rec ? rec.cs : getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parseColor(cs.backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 0.999) { opaque = c; break; } }
      node = node.parentElement;
    }
    if (!opaque) return null; // nothing painted underneath — unresolvable, never assumed white
    let base = opaque;
    for (let i = stack.length - 2; i >= 0; i--) base = over(stack[i], base);
    return base;
  }

  // ---- element census ----------------------------------------------------------------------
  function isVisible(cs, rect) {
    if (!cs || !rect) return false;
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    return rect.width > 0 && rect.height > 0;
  }
  function hasOwnText(el) {
    const kids = el.childNodes;
    for (let i = 0; i < kids.length; i++) {
      if (kids[i].nodeType === 3 && kids[i].nodeValue && kids[i].nodeValue.trim().length) return true;
    }
    return false;
  }

  const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, HEAD: 1, TITLE: 1, META: 1, LINK: 1, OPTION: 1, BR: 1 };
  const raw = document.body ? Array.prototype.slice.call(document.body.querySelectorAll('*'), 0, MAX_ELEMENTS) : [];
  const nodes = [];
  const byEl = new Map();
  for (let i = 0; i < raw.length; i++) {
    const el = raw[i];
    if (SKIP_TAGS[String(el.tagName || '').toUpperCase()]) continue;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const rec = { el: el, cs: cs, rect: rect, vis: isVisible(cs, rect) };
    nodes.push(rec);
    byEl.set(el, rec);
  }

  // ---- (b) overflow: a clipping/scrolling box whose content is wider than it ------------------
  // Horizontal only — vertical overflow is what a page IS. `overflow: visible` boxes are not
  // flagged here (nothing is hidden or trapped); their spill is the offscreen check's business.
  const docEl = document.documentElement;
  const pageOverflow = docEl.scrollWidth - docEl.clientWidth;
  if (pageOverflow >= 2) {
    add('overflow', docEl, 'page scrolls horizontally: scrollWidth ' + docEl.scrollWidth + ' > clientWidth ' + docEl.clientWidth,
      { scrollWidth: docEl.scrollWidth, clientWidth: docEl.clientWidth, overflowPx: pageOverflow, scope: 'document' });
  }
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n.vis) continue;
    const ox = n.cs.overflowX;
    if (ox !== 'hidden' && ox !== 'clip' && ox !== 'auto' && ox !== 'scroll') continue;
    const d = n.el.scrollWidth - n.el.clientWidth;
    if (n.el.clientWidth > 0 && d >= 2) {
      add('overflow', n.el, 'content overflows its ' + ox + ' box horizontally by ' + d + 'px',
        { scrollWidth: n.el.scrollWidth, clientWidth: n.el.clientWidth, overflowPx: d, overflowX: ox });
    }
  }

  // ---- (e) offscreen / clipped ---------------------------------------------------------------
  // Two kinds, one check id: content trapped outside a hidden/clip ancestor (unreachable — no
  // scrollbar will ever reveal it), and content past the document's own horizontal edge.
  function clippingAncestor(el) {
    let node = el.parentElement;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      const rec = byEl.get(node);
      const cs = rec ? rec.cs : getComputedStyle(node);
      if (cs.overflowX === 'hidden' || cs.overflowX === 'clip' || cs.overflowY === 'hidden' || cs.overflowY === 'clip') return node;
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll' || cs.overflowY === 'auto' || cs.overflowY === 'scroll') return null; // scrollable → reachable
      node = node.parentElement;
    }
    return null;
  }
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n.vis) continue;
    const clip = clippingAncestor(n.el);
    if (clip) {
      const cr = (byEl.get(clip) || { rect: clip.getBoundingClientRect() }).rect;
      const outR = n.rect.right - cr.right, outL = cr.left - n.rect.left;
      const outB = n.rect.bottom - cr.bottom, outT = cr.top - n.rect.top;
      const worst = Math.max(outR, outL, outB, outT);
      if (worst > 1) {
        const side = worst === outR ? 'right' : worst === outL ? 'left' : worst === outB ? 'bottom' : 'top';
        add('offscreen', n.el, 'clipped by an overflow-hidden ancestor: ' + Math.round(worst) + 'px past its ' + side + ' edge',
          { kind: 'ancestor-clip', side: side, clippedPx: Math.round(worst * 100) / 100, ancestor: selectorFor(clip) });
      }
    } else if (n.rect.right > docEl.clientWidth + 1 || n.rect.left < -1) {
      const outPx = Math.max(n.rect.right - docEl.clientWidth, -n.rect.left);
      add('offscreen', n.el, 'extends ' + Math.round(outPx) + 'px past the document\'s horizontal edge',
        { kind: 'document-edge', outPx: Math.round(outPx * 100) / 100, left: Math.round(n.rect.left), right: Math.round(n.rect.right), docWidth: docEl.clientWidth });
    }
  }

  // ---- (d) broken assets ---------------------------------------------------------------------
  const imgs = document.images || [];
  for (let i = 0; i < imgs.length; i++) {
    const img = imgs[i];
    const rec = byEl.get(img);
    const ics = rec ? rec.cs : getComputedStyle(img);
    // Gated on display/visibility only, NOT on the rect: a broken image is exactly the case that
    // collapses to a near-zero box, so the normal visibility test would hide the defect.
    if (ics.display === 'none' || ics.visibility === 'hidden') continue;
    const noSrc = !img.getAttribute('src');
    if (noSrc || (img.complete && img.naturalWidth === 0)) {
      add('broken-image', img, noSrc ? 'img has no src attribute' : 'img failed to load (naturalWidth 0): ' + (img.getAttribute('src') || ''),
        { src: img.getAttribute('src') || '', naturalWidth: img.naturalWidth, complete: !!img.complete, alt: img.getAttribute('alt') || '' });
    }
  }

  // ---- (c) AA contrast -----------------------------------------------------------------------
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n.vis || !hasOwnText(n.el)) continue;
    const fg = parseColor(n.cs.color);
    if (!fg || fg.a < 0.1) continue;
    const bg = effectiveBg(n.el);
    if (!bg) continue; // no resolvable painted background — unmeasurable, rung 2's job
    const text = fg.a >= 0.999 ? fg : over(fg, bg);
    const size = parseFloat(n.cs.fontSize) || 16;
    const weightRaw = n.cs.fontWeight;
    const weight = weightRaw === 'bold' ? 700 : weightRaw === 'normal' ? 400 : (parseInt(weightRaw, 10) || 400);
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const ratio = contrast(text, bg);
    if (ratio < need) {
      add('contrast', n.el, 'text contrast ' + (Math.round(ratio * 100) / 100) + ':1 is below the AA ' + need + ':1 floor for ' + (large ? 'large' : 'normal') + ' text',
        { ratio: Math.round(ratio * 100) / 100, required: need, color: rgbStr(text), background: rgbStr(bg), fontSizePx: size, fontWeight: weight, largeText: large });
    }
  }

  // ---- (a) overlap between in-flow siblings ---------------------------------------------------
  // Only static/relative, untransformed, unfloated siblings: absolutely-positioned layering and
  // transforms overlap BY DESIGN, and a float's rect legitimately overlaps the block after it.
  // Inside an <svg>, overlapping boxes are how the picture is DRAWN — a chart's axis, plot and label
  // layers intersect by construction, and scanning them produced dozens of spurious pairs per run
  // (measured: 25+ on chart internals alone). Siblings share a parent, so "both under one <svg>"
  // is exactly "their parent sits in an SVG subtree" — the whole pair scan is skipped there.
  function inSvgSubtree(el) {
    let node = el;
    while (node && node.nodeType === 1) {
      if (String(node.tagName || '').toLowerCase() === 'svg') return true;
      node = node.parentElement;
    }
    return false;
  }
  function overlapEligible(rec) {
    if (!rec.vis) return false;
    const p = rec.cs.position;
    if (p !== 'static' && p !== 'relative') return false;
    if (rec.cs.float && rec.cs.float !== 'none') return false;
    if (rec.cs.transform && rec.cs.transform !== 'none') return false;
    return true;
  }
  const parents = new Set();
  for (let i = 0; i < nodes.length; i++) if (nodes[i].el.parentElement) parents.add(nodes[i].el.parentElement);
  parents.forEach(function (parent) {
    if (inSvgSubtree(parent)) return; // layered vector paint, not a collision
    const kids = [];
    const children = parent.children;
    if (children.length > MAX_CHILDREN) return; // long lists: pair scan is not worth the cost
    for (let i = 0; i < children.length; i++) {
      const rec = byEl.get(children[i]);
      if (rec && overlapEligible(rec)) kids.push(rec);
    }
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].rect, b = kids[j].rect;
        const ow = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oh = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ow > 1.5 && oh > 1.5) {
          add('overlap', kids[i].el, 'overlaps sibling ' + selectorFor(kids[j].el) + ' by ' + Math.round(ow) + '×' + Math.round(oh) + 'px',
            { withSelector: selectorFor(kids[j].el), overlapWidth: Math.round(ow * 100) / 100, overlapHeight: Math.round(oh * 100) / 100 });
        }
      }
    }
  });

  // ---- (f) sibling top-alignment mismatch -----------------------------------------------------
  // The calibration-earned class: two items of the SAME height sharing a flex/grid row whose tops
  // differ by 1–3px. Equal height is what makes it a defect rather than an alignment mode — no
  // reader catches it from a full-page read, and no other check owns it.
  parents.forEach(function (parent) {
    const rec = byEl.get(parent);
    const cs = rec ? rec.cs : getComputedStyle(parent);
    const disp = cs.display;
    const isFlexRow = (disp === 'flex' || disp === 'inline-flex') && /^row/.test(cs.flexDirection || 'row');
    const isGrid = disp === 'grid' || disp === 'inline-grid';
    if (!isFlexRow && !isGrid) return;
    const kids = [];
    const children = parent.children;
    if (children.length > MAX_CHILDREN) return;
    for (let i = 0; i < children.length; i++) {
      const k = byEl.get(children[i]);
      if (k && k.vis && k.rect.height > 0) kids.push(k);
    }
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].rect, b = kids[j].rect;
        const sameRow = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > Math.min(a.height, b.height) * 0.5;
        if (!sameRow) continue;
        if (Math.abs(a.height - b.height) > 0.5) continue; // different heights: alignment mode, not a defect
        const d = Math.abs(a.top - b.top);
        if (d >= 0.75 && d <= 3.5) {
          add('misalignment', kids[i].el, 'sits ' + (Math.round(d * 100) / 100) + 'px off the top of equal-height row sibling ' + selectorFor(kids[j].el),
            { withSelector: selectorFor(kids[j].el), deltaPx: Math.round(d * 100) / 100, heightPx: Math.round(a.height * 100) / 100, container: selectorFor(parent) });
        }
      }
    }
  });

  // Per-check volume cap: a structural defect can repeat on hundreds of elements, and an unbounded
  // list would bury every other class. The overflow count is reported, never silently dropped.
  const keys = Object.keys(overflowCap);
  for (let i = 0; i < keys.length; i++) {
    findings.push({
      check: keys[i], selector: '', capped: true, suppressed: false,
      description: '+' + overflowCap[keys[i]] + ' further ' + keys[i] + ' finding(s) in this state were not enumerated (per-check cap ' + CAP + ')',
      measured: { notEnumerated: overflowCap[keys[i]], cap: CAP },
    });
  }
  const supKeys = Object.keys(overflowSuppressed);
  for (let i = 0; i < supKeys.length; i++) {
    findings.push({
      check: supKeys[i], selector: '', capped: true, suppressed: true, suppressionReason: '', suppressedBy: '',
      description: '+' + overflowSuppressed[supKeys[i]] + ' further SUPPRESSED ' + supKeys[i] + ' finding(s) in this state were not enumerated (per-check cap ' + CAP + ')',
      measured: { notEnumerated: overflowSuppressed[supKeys[i]], cap: CAP },
    });
  }

  return { findings: findings, matchedSuppressions: matched, invalidSuppressions: invalid };
};

// ---------------------------------------------------------------------------------------------
// Node side: aggregate per-state records into the manifest's `rung0` array.
// ---------------------------------------------------------------------------------------------

// Load a suppressions array from a JSON file that is either the bare array or an object with a
// top-level `suppressions` key (the state-manifest shape) — so `--suppressions visual-states.json`
// and `--suppressions supp.json` both work. Malformed input is a hard error: silently probing with
// no suppressions would look like a clean run.
export function readSuppressionsFile(fs, file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const arr = Array.isArray(raw) ? raw : raw && raw.suppressions;
  if (!Array.isArray(arr)) throw new Error(`${file}: expected a JSON array of suppressions, or an object with a "suppressions" array`);
  return arr;
}

// records: [{ label, cell, result: <RUNG0_PAGE_FN return value> }]
// Findings are grouped by check + selector across states; `cells` names every captured
// state×cell the finding appeared in, using the same `<label>__<cell>` stem as the PNG files, so a
// reader can jump straight from a finding to the frame. `measured` is the FIRST occurrence's
// numbers (they vary by cell; the cell list says where to look for the rest).
export function aggregateRung0(records, suppressions = []) {
  const byKey = new Map();
  const matched = [];  // {assert, selector} pairs the page side reported as hit / unparseable
  const invalid = [];
  for (const rec of records) {
    const res = rec && rec.result;
    if (!res || !Array.isArray(res.findings)) continue;
    matched.push(...(res.matchedSuppressions || []));
    invalid.push(...(res.invalidSuppressions || []));
    const stem = `${rec.label}__${rec.cell}`.replace(/[^\w.@-]/g, '_');
    for (const f of res.findings) {
      // A capped ROLLUP row carries no selector (it stands for findings never enumerated), so a key
      // without the cell collapses every state's rollup into the first one seen and silently drops
      // the other cells' counts. Rollups key on the cell; real findings still group across cells.
      const key = f.capped
        ? JSON.stringify([f.check, stem, 'capped', f.description])
        : JSON.stringify([f.check, f.selector, f.description]);
      const hit = byKey.get(key);
      if (hit) { if (!hit.cells.includes(stem)) hit.cells.push(stem); continue; }
      const entry = { check: f.check, selector: f.selector, description: f.description, cells: [stem], measured: f.measured || {}, suppressed: !!f.suppressed };
      if (f.suppressed) { entry.suppressionReason = f.suppressionReason || ''; entry.suppressedBy = f.suppressedBy || ''; }
      if (f.capped) entry.capped = true;
      byKey.set(key, entry);
    }
  }
  const findings = [...byKey.values()].sort((a, b) =>
    (a.check < b.check ? -1 : a.check > b.check ? 1 : 0) || (a.selector < b.selector ? -1 : a.selector > b.selector ? 1 : 0));

  const unused = [];
  for (const s of suppressions) {
    // Field-wise against the pairs the page side reported — never a joined key, because a CSS
    // selector can contain whatever separator character you would have picked.
    const seen = (list) => list.some((m) => m.assert === (s && s.assert) && m.selector === (s && s.selector));
    if (seen(matched)) continue;
    unused.push({
      assert: s && s.assert, selector: s && s.selector, reason: (s && s.reason) || '',
      why: seen(invalid) ? 'selector could not be parsed by the browser'
        : !s || !s.assert || !s.selector ? 'malformed entry — needs both "assert" (a rung-0 check id) and "selector"'
          : 'matched no finding in this run — stale, or the defect it covers is gone',
    });
  }
  return { findings, unusedSuppressions: unused };
}
