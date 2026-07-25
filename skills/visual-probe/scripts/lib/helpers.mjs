// The `h` helper handed to a scenario for ONE matrix cell.
//
// A scenario is the agent-authored part and only ever does navigate → drive → assert. Everything
// hard (browser launch/teardown, the per-cell context at the right device-scale-factor, capture,
// magnify, hashing, manifest, exit code) is owned by the harness — the scenario surface is tiny
// and hard to misuse. `h.snapshot()`, `h.expect()`, and `h.snapshotForced()` record into a shared
// collector that the harness flushes after every cell has run.

import { captureFrame } from './capture.mjs';
import { guardUrl } from './urlguard.mjs';
import { readStructure } from './read.mjs';

export function makeHelper({ page, cell, url, allowRemote, collector }) {
  const h = {
    url,                 // the resolved --url (may be undefined; scenario can pass an explicit target)
    page,                // the live Playwright Page — full API available for arbitrary drive logic
    cell: cell.label,

    // Navigate (defaults to --url). Routed through the fail-closed local-origin guard.
    async goto(target = url) {
      if (!target) throw new Error('no URL — pass --url <target> or call h.goto(explicitUrl)');
      await page.goto(guardUrl(target, allowRemote), { waitUntil: 'load' });
      return page;
    },

    // The VERIFICATION SEAM: read a JS expression in page context.
    //   await h.state('window.__GAME_STATE__.score')
    // For canvas apps (DOM/a11y tooling is blind inside a <canvas>), this is the only honest read.
    async state(expr) { return await page.evaluate(expr); },

    // Structured read: visible text + interactive-control inventory of a subtree (text, not pixels).
    // Use to assert on content/controls, or when a screenshot is too dense to read reliably.
    async read(selector = 'body') { return await readStructure(page, selector); },

    // Capture the current frame for THIS cell under `label`. opts: { crop: <selector>, fullPage }.
    // A cropped snapshot is the one that gets magnified later (full-page magnify would be huge).
    async snapshot(label, opts = {}) {
      try { await page.evaluate(async () => { await document.fonts?.ready; }); } catch { /* fonts API absent */ }
      const buf = await captureFrame(page, opts);
      collector.addSnapshot({ label, cell, buf, opts });
    },

    // Assert on a value the scenario reads. Records pass/fail; ANY failure → non-zero process exit
    // (the CI-grade signal the agent branches on). Never throws on a failed predicate.
    async expect(getter, predicate, msg) {
      let value, pass;
      try { value = await getter(); pass = !!predicate(value); }
      catch (e) { value = `ERROR: ${e.message}`; pass = false; }
      collector.addAssert({ cell: cell.label, msg, value, pass });
      return pass;
    },

    // STATE-FORCING CONTRACT seam (references/state-contract.md): wait for a per-state MARKER
    // selector to hold — bounded by the cell's default timeout (page.setDefaultTimeout, set once
    // per cell by runCells, cli.mjs) — record the wait's outcome via the `expect` seam above, then
    // capture via the normal `snapshot` path with the remaining opts (crop, fullPage, …) passed
    // through untouched. This is what closes the "screenshot fired after the overlay closed"
    // false-pass: a marker that never holds is a failed assert, and a failed assert is a non-zero
    // process exit (the collector already wires that — nothing extra to plumb here).
    async snapshotForced(label, { marker, ...snapshotOpts } = {}) {
      const held = await h.expect(
        () => page.waitForSelector(marker, { state: 'visible' }).then(() => true),
        (v) => v === true,
        `marker held: ${marker}`,
      );
      await h.snapshot(label, snapshotOpts);
      return held;
    },
  };
  return h;
}
