// Fidelity matrix — a single frame captured across (viewport size × device-scale-factor).
//
// WHY THIS EXISTS: a single screenshot at a clean integer scale (e.g. 2.0× the internal res)
// LIES. It is exactly the scale that masks boundary-tie blit defects — the SQUAWKVADERS
// failure mode where text mangling and HUD overlap shipped to the user because every pre-ship
// capture happened at 2.0×. The cure is to capture across several scales INCLUDING at least
// one NON-INTEGER scale and at least one DPI≠1, then compare. Each cell below is chosen to
// stress a different rendering-math path; do not trim the non-integer/odd-dimension cells —
// they are the load-bearing ones.

export const PRESETS = {
  // label             W      H    DSF
  default: [
    ['baseline-1.0',  1280,  800, 1.0],   // reference / desktop @1x
    ['integer-2.0',   1280,  800, 2.0],   // the clean integer scale that LIES (looks fine, hides defects)
    ['noninteger-1.5',1281,  801, 1.5],   // non-integer scale + ODD dims = boundary-tie blit territory
    ['dpi-1.25',       800,  600, 1.25],  // DPI ≠ 1, non-integer, smaller viewport
    ['hidpi-3.0',      375,  667, 3.0],   // high-DPI / mobile-ish
  ],
  quick: [
    ['baseline-1.0',  1280,  800, 1.0],
    ['noninteger-1.5',1281,  801, 1.5],   // the cheapest pair that still exercises the boundary-tie path
  ],
  desktop: [
    ['hd-1.0',        1280,  720, 1.0],
    ['fhd-1.0',       1920, 1080, 1.0],
    ['fhd-1.5',       1920, 1080, 1.5],
    ['wide-2.0',      2560, 1440, 2.0],
  ],
};

// Parse a matrix argument: a preset name, or an inline "WxH@DSF,WxH@DSF,..." spec.
export function parseMatrix(spec) {
  if (!spec || spec === true) return PRESETS.default;
  if (PRESETS[spec]) return PRESETS[spec];
  return spec.split(',').map((s, i) => {
    const m = s.trim().match(/^(\d+)x(\d+)@([\d.]+)$/);
    if (!m) throw new Error(`bad matrix cell "${s}" — expected WxH@DSF (e.g. 1281x801@1.5)`);
    return [`cell${i}-${m[1]}x${m[2]}@${m[3]}`, +m[1], +m[2], +m[3]];
  });
}

// [label, w, h, dsf] tuple → {label, width, height, dsf}
export function cellObj([label, width, height, dsf]) {
  return { label, width, height, dsf };
}
