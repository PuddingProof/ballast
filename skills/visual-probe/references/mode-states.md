# Mode: `states` — drive a project's declared state matrix

```
VISUAL_STATES=/abs/path/visual-states.json node $P run scenarios/states-from-manifest.mjs --url <origin>
```

The bundled `scenarios/states-from-manifest.mjs` needs no editing. It reads a project's
`visual-states.json` (the state-forcing contract — schema, semantics, and a worked example in
`state-contract.md`, beside this file), enumerates each declared state, composes its URL or runs
its `drive` hook, and `snapshotForced`s it against the state's marker.

Manifest discovery: `VISUAL_STATES` (absolute path) wins; otherwise `./.claude/visual-states.json`
resolved from the cwd — so set the env var unless you are invoking from the project root.
`--url` overrides the manifest's advisory `baseUrl`: the origin is the one you own.

**Enumeration** (per route): the baseline, each axis value one-hot against the other axes'
defaults, each overlay flag alone, then one all-worst composed state per overlay flag. Values are
ordered default-first / worst-last by contract. `VISUAL_STATES_FULL=1` runs the full
cross-product instead.

**No manifest at all** is itself a finding: forceability is undeclared, so state coverage is
heuristic — say so rather than passing quietly.

## Coverage holes are output, not omissions

Two classes of state are skipped rather than captured, and both are reported in `manifest.json`'s
`coverageHoles` (and on stderr):

- **`cannotForce`** — the project declared the state undriveable (missing fixture, un-instrumented
  overlay). Authoritative: it blocks a clean pass; the missing seam is the deliverable.
- **`drive-hook-skipped`** — the state needed a manifest `drive` hook that was not run (below).

Capturing a state you could not force would photograph the *wrong* state and launder it into a
green cell, so skipping is correct — provided the hole is named. It always is.

A marker proves the state *exists*, not that it has finished animating in. Where the project's
transition completes after `readySignal`, pass `--settle MS`: each state dwells that long after its
marker holds, before the shutter.

## `--skip-drive-hooks` — the leaf-mode flag

A manifest `drive` hook is **project-authored code** that this scenario dynamically imports and
runs inside the probe's own node process — an arbitrary-code entry point sitting on the default
capture path. Any dispatch to an unattended leaf mandates the flag:

```
node $P run scenarios/states-from-manifest.mjs --url <origin> --skip-drive-hooks
```

(equivalently `VISUAL_STATES_SKIP_DRIVE_HOOKS=1`). With it set, no hook is imported at all, and
every state that needed one is skipped and reported as a `drive-hook-skipped` coverage hole. The
cost is real and visible: a manifest whose theme axis is drive-driven loses every themed cell,
including the baseline — narrower attested coverage, named in the output, instead of a leaf
executing project code in-process.

**Residual, stated plainly:** this closes the manifest-hook entry point only. `probe run` of a
hand-authored scenario still imports whatever file it is pointed at, so scenario authoring stays
a main-session capability.
