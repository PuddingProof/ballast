// BUNDLED scenario (not a copy-me template) — drives every state named in a project's
// state-forcing contract manifest (`visual-states.json`; full schema + worked example in
// references/state-contract.md). For each route × enumerated state it composes the target URL
// (or runs the state's `drive` hook), navigates via `h.goto()` — never a bare `page.goto` — and
// captures via `h.snapshotForced`, which WAITS for the state's marker before the shutter fires.
// This is what closes the "screenshot fired after the overlay closed" false-pass: a state that
// never renders (or renders and silently ends) fails its assert instead of laundering into a
// green cell.
//
// Manifest discovery: the `VISUAL_STATES` env var (absolute path) wins; otherwise
// `./.claude/visual-states.json` resolved from `process.cwd()` — invoke from the project root, or
// always set `VISUAL_STATES` (from the skill dir the default would resolve inside the plugin,
// never the caller's project).
//
// Run (from the visual-probe skill directory):
//   VISUAL_STATES=/abs/path/to/visual-states.json node scripts/probe.mjs run scenarios/states-from-manifest.mjs [--url http://origin] [--matrix M]
//   (or cd to the project root and omit VISUAL_STATES if .claude/visual-states.json lives there)
//
// `--url` (h.url) overrides the manifest's advisory `baseUrl` — the origin is handed in by the
// caller that owns it (the orchestrator's session origin), never resolved here.
//
// Leaf mode — `--skip-drive-hooks` (or `VISUAL_STATES_SKIP_DRIVE_HOOKS=1`): a manifest `drive` hook
// is PROJECT-authored code this scenario would dynamically import and run inside the probe's own
// node process — an arbitrary-code entry point on the default capture path. With the flag set the
// hook is never imported, every state that needs one is SKIPPED (capturing it unforced would
// capture the wrong state and launder it into a green cell), and each skipped state is reported as
// an unforceable coverage hole — see `coverageHoles` below.
//
// Enumeration policy (default, per route): the "param axes" are every manifest axis EXCEPT
// `overlay` (the flag axis, enumerated separately below) and any `outOfBand` axis like viewport
// (invoker-consumed, never URL-composed here) — content, theme, or any other key (appTheme,
// face, …), each in manifest order. Enumeration is the baseline state (every param axis at its
// default), then each param axis value one-hot against the first-listed ("default") value of
// every other param axis, then each overlay flag alone, then one all-worst composed state per
// overlay flag — every param axis at its worst value × that one overlay (no overlay axis at all →
// a single composed state with no overlay component). "Worst" / "non-default" = the LAST-listed
// value on an axis — values are ordered default-first, worst-last as a CONTRACT convention
// (references/state-contract.md), not merely this scenario's interpretation. Full cross-product
// (every param axis's values crossed × {no overlay, each overlay flag in turn}) only behind
// `VISUAL_STATES_FULL=1` — still one overlay at a time, never multiple overlays composed
// simultaneously (not evidenced anywhere in the contract).
//
// A manifest's top-level `suppressions` array ({assert, selector, reason}) is forwarded to the
// harness's rung-0 geometry assertions, which flag the matching findings as intended rather than
// dropping them (see scripts/lib/assertions.mjs).
//
// `cannotForce` entries ({axis,value,reason}) are authoritative holes: any generated state that
// would force one of them is skipped and named on stderr — never silently attempted, never
// silently absent.
//
// Snapshot label: route name first, then the axis values that differ from default, `__`-joined
// (e.g. `home__stress__dark__settings`) — the cell suffix is appended by the harness itself.

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

const log = (...a) => console.error('[states-from-manifest]', ...a);

function loadManifest() {
  const manifestPath = process.env.VISUAL_STATES
    ? path.resolve(process.env.VISUAL_STATES)
    : path.resolve(process.cwd(), '.claude/visual-states.json');
  if (!fs.existsSync(manifestPath)) {
    throw new Error(
      `no visual-states.json found at ${manifestPath} — set VISUAL_STATES to an absolute path, ` +
      `or run from a project root that has .claude/visual-states.json`,
    );
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (!manifest.readySignal) throw new Error(`${manifestPath}: missing "readySignal" (the global default marker)`);
  if (!manifest.routes || !Object.keys(manifest.routes).length) throw new Error(`${manifestPath}: missing/empty "routes"`);
  return { manifest, manifestDir: path.dirname(manifestPath) };
}

// Overlay flags may be declared per-name (`flags: {name: {...}}`) or, per state-contract.md, as a
// single whole-axis `drive` hook with no names at all — normalize both into one {name: entry} map
// so the rest of the enumeration logic never cares which form the manifest used.
function overlayFlags(overlayAxis) {
  if (!overlayAxis) return {};
  if (overlayAxis.flags) return overlayAxis.flags;
  if (overlayAxis.drive) {
    const name = overlayAxis.drive.split('#')[1] || 'overlay';
    return { [name]: { drive: overlayAxis.drive, marker: overlayAxis.marker } };
  }
  return {};
}

// a param axis (content, theme, or any other) → a {param,value,kind} URL part, or a {drive,value}
// drive-call descriptor.
function axisPart(axisDef, value) {
  if (!axisDef) return null;
  if (axisDef.drive) return { drive: axisDef.drive, value };
  return { param: axisDef.param, value, kind: 'query' };
}

function overlayPart(flag, name) {
  if (flag.drive) return { drive: flag.drive, value: name, marker: flag.marker };
  return { param: flag.param, value: flag.value, kind: flag.kind || 'query', marker: flag.marker };
}

// Compose one state's target URL via the URL API only (never string concat — a caller's --url may
// already carry a path/query). `parts`: [{param,value,kind}].
function buildUrl(base, routePath, parts) {
  const u = new URL(routePath, base);
  for (const p of parts) {
    if (p.kind === 'hash') {
      const hp = new URLSearchParams(u.hash.replace(/^#/, ''));
      hp.set(p.param, p.value);
      u.hash = hp.toString();
    } else {
      u.searchParams.set(p.param, p.value);
    }
  }
  return u.toString();
}

// Build the enumerated state list for ONE route. Each state: {labelParts, marker, urlParts,
// driveCalls, forces} — `forces` is the [{axis,value}] list used to test against `cannotForce`.
function enumerateStates(axes, readySignal, full) {
  const flags = overlayFlags(axes.overlay);
  const overlayNames = Object.keys(flags);

  // Param axes = every manifest axis except `overlay` (the flag axis) and any `outOfBand` axis
  // (viewport — consumed by the invoker, never URL-composed here), in manifest insertion order.
  // Each keeps the standard {values, param|drive, kind?} shape, values ordered default-first
  // (values[0]) / worst-last (values[len-1]).
  const paramAxes = Object.keys(axes)
    .filter((key) => key !== 'overlay' && !axes[key]?.outOfBand)
    .map((key) => ({ key, def: axes[key] }));

  const defaultVal = (def) => def?.values?.[0];
  const worstVal = (def) => def?.values?.[def.values.length - 1];
  const getVal = (axisVals, key) => (axisVals instanceof Map ? axisVals.get(key) : axisVals[key]);

  // axisVals: Map|object of axisKey → value (undefined allowed). Label lists each param axis value
  // that differs from that axis's default (in axis order), then the overlay name; urlParts /
  // driveCalls compose every param axis with a defined value, then the overlay.
  function makeState({ axisVals, overlayName, forces }) {
    const labelParts = [];
    for (const { key, def } of paramAxes) {
      const v = getVal(axisVals, key);
      if (v !== undefined && v !== defaultVal(def)) labelParts.push(v);
    }
    if (overlayName) labelParts.push(overlayName);

    const marker = overlayName ? (flags[overlayName].marker || readySignal) : readySignal;
    const urlParts = [];
    const driveCalls = [];

    for (const { key, def } of paramAxes) {
      const v = getVal(axisVals, key);
      if (v === undefined) continue;
      const p = axisPart(def, v);
      if (p.drive) driveCalls.push({ spec: p.drive, stateName: v });
      else urlParts.push(p);
    }
    if (overlayName) {
      const p = overlayPart(flags[overlayName], overlayName);
      if (p.drive) driveCalls.push({ spec: p.drive, stateName: overlayName });
      else urlParts.push(p);
    }

    return { labelParts, marker, urlParts, driveCalls, forces };
  }

  const states = [];

  if (full) {
    // Full cross-product IS the enumeration in this mode (superset of baseline/one-hot/composed):
    // every param axis's full value list crossed over one another (first axis outermost) ×
    // {no overlay, each overlay flag in turn}. The product runs over EVERY param axis.
    const overlayChoices = [undefined, ...overlayNames];
    let combos = [[]];
    for (const { key, def } of paramAxes) {
      const next = [];
      for (const combo of combos) {
        for (const v of def.values) next.push([...combo, { key, value: v }]);
      }
      combos = next;
    }
    for (const combo of combos) {
      for (const ov of overlayChoices) {
        const axisVals = new Map(combo.map(({ key, value }) => [key, value]));
        const forces = combo.map(({ key, value }) => ({ axis: key, value }));
        if (ov) forces.push({ axis: 'overlay', value: ov });
        states.push(makeState({ axisVals, overlayName: ov, forces }));
      }
    }
    return states;
  }

  // Dedupe guard, load-bearing for the composed pass below: with ≤1 param axis — or any
  // single-value axis — the all-worst point coincides with a state the baseline/one-hot passes
  // already emitted (a single 2-value axis's composed state IS its one-hot; an overlay-only
  // manifest's composed state IS the overlay one-hot). An undeduped collision makes the scenario
  // navigate and snapshotForced the same label twice, the second capture clobbering the first.
  // Key = every param-axis value in axis order + overlay name; baseline/one-hot are mutually
  // distinct by construction, so the guard only ever drops redundant composed states.
  const seen = new Set();
  const pushUnique = ({ axisVals, overlayName, forces }) => {
    const key = JSON.stringify([paramAxes.map(({ key: k }) => axisVals.get(k)), overlayName ?? null]);
    if (seen.has(key)) return;
    seen.add(key);
    states.push(makeState({ axisVals, overlayName, forces }));
  };

  // baseline — every param axis explicit at its default value. forces stays empty (identical to
  // the pre-generalization output): baseline is never a cannotForce candidate.
  const baselineVals = new Map(paramAxes.map(({ key, def }) => [key, defaultVal(def)]));
  pushUnique({ axisVals: baselineVals, forces: [] });

  // one-hot: each param axis's non-default values in turn (against the other axes' defaults), in
  // axis order, then each overlay flag alone.
  for (const { key: varKey, def: varDef } of paramAxes) {
    for (const v of varDef.values.slice(1)) {
      const axisVals = new Map(paramAxes.map(({ key, def }) => [key, key === varKey ? v : defaultVal(def)]));
      pushUnique({ axisVals, forces: [{ axis: varKey, value: v }] });
    }
  }
  for (const name of overlayNames) {
    const axisVals = new Map(paramAxes.map(({ key, def }) => [key, defaultVal(def)]));
    pushUnique({ axisVals, overlayName: name, forces: [{ axis: 'overlay', value: name }] });
  }

  // one all-worst composed state per overlay flag (every param axis at its worst value × that
  // overlay); no overlay axis at all → a single composed state with no overlay component. The
  // pushUnique guard silently drops any composed point the passes above already covered.
  const worstVals = new Map(paramAxes.map(({ key, def }) => [key, worstVal(def)]));
  const worstForces = paramAxes.map(({ key, def }) => ({ axis: key, value: worstVal(def) }));
  if (overlayNames.length === 0) {
    pushUnique({ axisVals: worstVals, forces: worstForces });
  } else {
    for (const name of overlayNames) {
      pushUnique({
        axisVals: worstVals, overlayName: name,
        forces: [...worstForces, { axis: 'overlay', value: name }],
      });
    }
  }

  return states;
}

// Exported for tests (test_states_enum.sh); the default export below is the scenario itself.
export { enumerateStates, overlayFlags };

// Coverage holes this run could not force. The harness (lib/cli.mjs) copies this array into
// manifest.json's `coverageHoles`, so a hole is DATA in the output a reader already reads first —
// not a stderr line they can miss. Entries: {label, kind: 'cannotForce'|'drive-hook-skipped',
// reason}. Module-level because the scenario runs once per matrix cell; addHole dedupes by
// label+kind so a hole is reported once per run, not once per cell.
export const coverageHoles = [];

// Rung-0 suppressions declared by the project manifest (frozen schema: a top-level
// `suppressions: [{assert, selector, reason}]`). Module-level and repopulated at the start of every
// run for the same reason coverageHoles is module-level — the harness holds a LIVE reference to
// this array and reads it at shutter time, after this scenario has loaded the manifest. Declare
// suppressions in the manifest, never mid-run: a rule appended after the first capture cannot
// suppress the findings that capture already produced.
export const suppressions = [];

// The manifest's `verifies` scope disclaimer, republished per run so the harness can carry it into
// its own output manifest — a leaf quotes it verbatim rather than over-reading a green run. Same
// module-level live-reference shape as the two arrays above, for the same reason.
export const meta = { verifies: null };

const holeKeys = new Set();
function addHole(hole) {
  const key = `${hole.label}|${hole.kind}`;
  if (holeKeys.has(key)) return;
  holeKeys.add(key);
  coverageHoles.push(hole);
}

export default async (page, h) => {
  const { manifest, manifestDir } = loadManifest();
  const full = process.env.VISUAL_STATES_FULL === '1';
  const base = h.url || manifest.baseUrl;
  if (!base) throw new Error('no origin to probe — pass --url or set "baseUrl" in the manifest');

  // Both output channels reset per run, for the same reason: production imports this module once
  // per process, but anything that invokes it twice (a batch mode, a test harness reusing the
  // import) would otherwise carry a prior manifest's holes into this run's manifest.json as
  // findings nothing in this run produced.
  suppressions.length = 0;
  for (const s of manifest.suppressions || []) suppressions.push(s);
  meta.verifies = manifest.verifies || null;
  coverageHoles.length = 0;
  holeKeys.clear();

  const skipDriveHooks = process.env.VISUAL_STATES_SKIP_DRIVE_HOOKS === '1';
  const cannotForceList = manifest.cannotForce || [];
  const cannotForce = new Set(cannotForceList.map((e) => `${e.axis}:${e.value}`));
  const driveCache = new Map();
  async function resolveDrive(spec) {
    if (driveCache.has(spec)) return driveCache.get(spec);
    const [rel, exportName] = spec.split('#');
    const mod = await import(pathToFileURL(path.resolve(manifestDir, rel)).href);
    const fn = mod[exportName];
    if (typeof fn !== 'function') throw new Error(`drive export "${exportName}" not found in ${rel} (manifest ${manifestDir})`);
    driveCache.set(spec, fn);
    return fn;
  }

  const skipped = [];
  const driveSkipped = [];
  // Route-independent, so enumerate once — not per route.
  const states = enumerateStates(manifest.axes || {}, manifest.readySignal, full);
  for (const [routeName, routePath] of Object.entries(manifest.routes)) {
    for (const state of states) {
      const label = [routeName, ...state.labelParts].join('__');

      const blocked = state.forces.find((f) => cannotForce.has(`${f.axis}:${f.value}`));
      if (blocked) {
        skipped.push({ label, axis: blocked.axis, value: blocked.value });
        const declared = cannotForceList.find((e) => e.axis === blocked.axis && e.value === blocked.value);
        addHole({
          label, kind: 'cannotForce',
          reason: `${blocked.axis}:${blocked.value} — ${declared?.reason || 'declared unforceable'}`,
        });
        continue;
      }

      if (skipDriveHooks && state.driveCalls.length) {
        const specs = [...new Set(state.driveCalls.map((dc) => dc.spec))].join(', ');
        driveSkipped.push({ label, specs });
        addHole({
          label, kind: 'drive-hook-skipped',
          reason: `needs manifest drive hook(s) ${specs} — not imported (--skip-drive-hooks)`,
        });
        continue;
      }

      const target = buildUrl(base, routePath, state.urlParts);
      await h.goto(target);
      for (const dc of state.driveCalls) {
        const fn = await resolveDrive(dc.spec);
        await fn(page, h, dc.stateName);
      }
      // `settle` (the run's --settle, 0 by default) rides through to the shutter: the marker proves
      // the state exists, but a project whose entrance animation or theme crossfade completes after
      // readySignal would otherwise be captured mid-transition. Per state, not once per run.
      await h.snapshotForced(label, { marker: state.marker, settle: h.settle });
    }
  }

  if (skipped.length) {
    log(`skipped ${skipped.length} cannotForce state(s):`,
      skipped.map((s) => `${s.label} (${s.axis}:${s.value})`).join(', '));
  }
  if (driveSkipped.length) {
    log(`--skip-drive-hooks: skipped ${driveSkipped.length} drive-hook state(s):`,
      driveSkipped.map((s) => `${s.label} (${s.specs})`).join(', '));
  }
};
