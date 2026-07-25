// TEMPLATE — a single static HTML file with no dev server. The harness serves it directly via
// file:// (a fresh context per cell avoids the paint cache). Pure rendering-fidelity check, no drive.
//
// Run (from the visual-probe skill directory): node scripts/probe.mjs run <this> --url ./path/to/page.html
//   (a bare path is normalized to a file:// URL automatically)
export default async (page, h) => {
  await h.goto();
  await h.snapshot('full');                          // full-page frame per cell
  await h.snapshot('hero', { crop: 'main, .hero, header, body > :first-child' }); // magnified crop of the top region
};
