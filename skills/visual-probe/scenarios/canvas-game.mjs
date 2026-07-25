// TEMPLATE — canvas game over file:// or localhost (e.g. invaders / KAPLAY).
//
// DOM/a11y tooling is BLIND inside a <canvas> (one element = one paint). The honest read is the
// VERIFICATION SEAM: the app exposes window.__GAME_STATE__ / window.__STATE__, and we assert on
// state DELTAS under scripted input, plus capture the canvas across the fidelity matrix.
//
// Run (from the visual-probe skill directory): node scripts/probe.mjs run <this> --url file:///path/to/index.html
//   (a fresh in-memory context per matrix cell defeats the file:// paint cache automatically)
export default async (page, h) => {
  await h.goto();                                  // navigate to --url
  await page.locator('body').focus();              // canvas games listen on document/body
  await h.snapshot('initial', { crop: 'canvas' }); // captured + magnified per matrix cell

  const before = await h.state('window.__GAME_STATE__ ?? window.__STATE__');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Space');
  const after = await h.state('window.__GAME_STATE__ ?? window.__STATE__');

  await h.expect(
    () => after,
    (s) => s != null && JSON.stringify(s) !== JSON.stringify(before),
    'game state changed after scripted input (proves the input loop is live, not a hung frame)',
  );
  await h.snapshot('after-input', { crop: 'canvas' });
};
