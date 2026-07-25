// TEMPLATE — a localhost dev server or static site (e.g. a Vite/SvelteKit dev server, or a
// static HTML build). DOM tooling works here, so assert on real elements.
//
// Run (from the visual-probe skill directory): node scripts/probe.mjs run <this> --url http://localhost:PORT/
export default async (page, h) => {
  await h.goto();
  await page.waitForLoadState('networkidle');
  await h.snapshot('loaded');                        // full-page frame per matrix cell

  // Sanity assert: the app actually rendered content (not a blank/error shell).
  await h.expect(
    () => page.evaluate(() => document.body.innerText.trim().length),
    (len) => len > 0,
    'page rendered non-empty body text',
  );

  // --- example DRIVE (uncomment + adapt) ---
  // await page.getByRole('button', { name: 'Settings' }).click();
  // await h.snapshot('settings-open', { crop: '#settings-panel' });   // cropped → magnified crop emitted
};
