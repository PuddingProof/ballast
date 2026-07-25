// Structured read — return an element subtree as TEXT instead of pixels: its visible text plus an
// inventory of interactive controls (role · accessible name · value · state). Disambiguates a dense
// dashboard where a screenshot is hard to parse — the agent reads `button: Save`,
// `textbox: Email = "foo@bar"`, `checkbox: Remember [checked]` rather than guessing from a cramped PNG.
//
// All extraction happens in-page (one evaluate) so the tool stays dependency-free beyond Playwright.

export async function readStructure(page, selector = 'body', { maxText = 4000, maxControls = 80 } = {}) {
  const el = await page.$(selector);
  if (!el) throw new Error(`selector not found: ${selector}`);
  const data = await el.evaluate((node, limits) => {
    const { maxText, maxControls } = limits;
    const clean = (s) => (s || '').replace(/[ \t ]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
    // Best-effort DISPLAY role: explicit ARIA role, else a sensible tag default (shows the input's
    // type — e.g. "text", "search" — which is friendlier to read than the bare ARIA role).
    const roleOf = (e) =>
      e.getAttribute('role') ||
      ({ BUTTON: 'button', A: 'link', INPUT: e.type || 'input', SELECT: 'select', TEXTAREA: 'textbox', SUMMARY: 'disclosure' }[e.tagName] ||
        e.tagName.toLowerCase());
    // ARIA role usable by Playwright's `role=` selector engine — DISTINCT from the display role
    // above (e.g. a text input displays as "text" but its ARIA role is "textbox"). null when there
    // is no clean mapping, in which case no selector is suggested for that control.
    // Mappings verified against Playwright 1.61.1's implicit-role table — a WRONG role can't be
    // rescued by "shorten the regex" (the doc's advice for a name miss), so emit null over a guess.
    const ariaRoleOf = (e) => {
      const explicit = e.getAttribute('role');
      if (explicit) return explicit.trim().split(/\s+/)[0]; // role may list fallbacks ("button link") — PW uses the first token
      switch (e.tagName) {
        case 'BUTTON': return 'button';
        case 'A': return e.hasAttribute('href') ? 'link' : null;
        case 'SELECT': return e.multiple || e.size > 1 ? 'listbox' : 'combobox'; // size>1 is a listbox even without `multiple`
        case 'TEXTAREA': return 'textbox';
        case 'SUMMARY': return null; // PW assigns <summary> NO implicit role — no selector beats a never-resolving `role=button`
        case 'INPUT': {
          const t = (e.type || 'text').toLowerCase();
          if (t === 'hidden') return null;                                                       // no role in the a11y tree
          if (e.list && ['text', 'search', 'email', 'tel', 'url'].includes(t)) return 'combobox'; // a <datalist>-bound text input is a combobox
          return ({ checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button', reset: 'button',
            image: 'button', file: 'button', range: 'slider', number: 'spinbutton', search: 'searchbox' }[t] || 'textbox');
        }
        default: return null;
      }
    };
    // Escape regex metacharacters (incl. `/`) so a control name is safe inside a `/…/i` literal.
    const reEsc = (s) => (s || '').replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
    // Accessible name: aria-label, then an associated <label> (for= or wrapping, with the control's
    // own text/options stripped so a <select>'s options don't bleed in), then placeholder/title/text.
    const nameOf = (e) => {
      const aria = e.getAttribute('aria-label');
      if (aria) return clean(aria).slice(0, 80);
      let lbl = '';
      if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) lbl = l.innerText; }
      if (!lbl) { const w = e.closest('label'); if (w) lbl = (w.innerText || '').replace(e.innerText || '', ''); }
      lbl = clean(lbl);
      if (lbl) return lbl.slice(0, 80);
      return clean(e.getAttribute('title') || e.getAttribute('placeholder') || e.alt || e.innerText || e.value || '').slice(0, 80);
    };

    const SEL =
      'button, a[href], input, select, textarea, summary, [role=button], [role=link], [role=checkbox], [role=switch], [role=tab], [role=menuitem], [role=combobox]';
    const all = Array.from(node.querySelectorAll(SEL));
    const controls = all.slice(0, maxControls).map((e) => {
      const r = roleOf(e), n = nameOf(e);
      let state = '';
      if (e.type === 'checkbox' || e.type === 'radio') state += e.checked ? ' [checked]' : ' [unchecked]';
      const ac = e.getAttribute('aria-checked'); if (ac) state += ` [${ac}]`;
      if (e.disabled || e.getAttribute('aria-disabled') === 'true') state += ' [disabled]';
      const ax = e.getAttribute('aria-expanded'); if (ax) state += ` [expanded=${ax}]`;
      const val = e.value != null && e.value !== '' && e.type !== 'checkbox' && e.type !== 'radio' ? ` = "${String(e.value).slice(0, 80)}"` : '';
      // Ready-to-paste Playwright selector. This is the #1 friction fix: the printed name is NOT
      // Playwright's computed accessible name, so a hand-built EXACT selector (role=radio[name="Atlas"])
      // silently misses — a case-insensitive REGEX on the name matches robustly. Emitted only when both
      // an ARIA role and a name resolve; if it ever misses, shorten the regex to a distinctive word.
      const ar = ariaRoleOf(e);
      const nm = n.replace(/\s+/g, ' ').trim();
      const sel = ar && nm ? `  →  role=${ar}[name=/${reEsc(nm)}/i]` : '';
      return `${r}: ${n}${val}${state}${sel}`.trim();
    });

    return { text: clean(node.innerText).slice(0, maxText), controls, controlCount: all.length };
  }, { maxText, maxControls });
  return { selector, ...data };
}
