# The state-forcing contract

Visual coverage is capped by *forceability*: a UI state with no seam to drive it into existence is invisible to both the builder and the reviewer — it can never appear in a screenshot, so no amount of looking will ever catch its defects. Worse, a state that can silently *end* (an overlay that closes, a toast that expires) yields a frame of the WRONG state unless capture asserts the intended state still held at shutter time. The contract closes both gaps: a declarative `visual-states.json` manifest names the forceable states and the selector that proves each one is on screen, and `cannotForce` records the states that have no seam yet so a reviewer can refuse to certify them.

A project drops the manifest at `<project>/.claude/visual-states.json`; the bundled `states-from-manifest.mjs` scenario reads it, and the adversarial visual-reviewer agent (dispatched before "done" by the visual-verification-gate skill) consumes the same file.

## The manifest

```jsonc
{
  "baseUrl": "http://localhost:5173",   // ADVISORY only — the invoker owns its origin (probe `serve start`, or its own dev instance) and overrides via --url. Never point the probe at the user's live server (origin rule, SKILL.md).
  "routes": { "home": "/", "detail": "/item" },  // named entry points; the route name is the FIRST segment of every snapshot label
  "readySignal": "[data-app-ready]",    // selector that must HOLD before any capture — the global default marker
  "axes": {
    "content":  { "param": "fixture", "values": ["default", "stress", "empty", "error", "multi"] },
    "theme":    { "param": "theme",   "values": ["light", "dark"] },
    "overlay":  { "flags": {
      "settings": { "param": "settings", "value": "1", "marker": "[data-overlay='settings']" },
      "detail":   { "param": "d", "value": "first-item", "kind": "query", "marker": "[data-overlay='detail']" }
    } },
    "viewport": { "outOfBand": true, "native": "1920x1080", "values": ["1920x1080", "390x844"] }  // consumed by the INVOKER, never the scenario; `native` = the exact ship resolution(s) — a MANDATORY cell
  },
  "cannotForce": [ { "axis": "content", "value": "error", "reason": "no error fixture yet" } ],
  "verifies": "synthetic UI over typed fixtures; NOT real-backend data correctness"
}
```

**The four axes are reserved *semantics*, not param names.** `content`, `theme`, `overlay`, `viewport` are the coverage dimensions the reviewer reasons over; each project maps its OWN URL param names onto them (`fixture`/`theme`/`settings` above are examples, not a required vocabulary), so an existing fixture-URL scheme harmonizes with no rename churn. A project without one of these dimensions simply omits it. And the four are a floor, not a ceiling: any OTHER key under `axes` with the same `values` + `param`/`drive` shape (a second theme-like dimension such as `appTheme`, a `face` variant) is a project-custom param axis, enumerated by `states-from-manifest.mjs` exactly like `content`/`theme` — only `overlay` (flag semantics, below) and axes marked `outOfBand: true` (invoker-consumed, like `viewport`) are handled specially.

- **`baseUrl`** is advisory. The invoker establishes an origin it OWNS and passes it as `--url`, which overrides `baseUrl`; the manifest value only documents the app's normal dev port.
- **`routes`** are named entry points. The route name leads every snapshot label, so a state reads as `home__stress__dark__settings` — route first, then the varied axis values.
- **`readySignal`** is the global default marker: the selector every capture waits on before the shutter fires, so a baseline screenshot can never land on a half-mounted shell.
- **`content` / `theme`** axes carry a `param` and a list of plain-string `values`. These are states a single URL param locks in and holds for the life of the page, so they need no per-state marker.
- **`overlay`** is a map of named `flags`, each an object: `param` + `value` compose the URL, `kind` is `"query"` (default) or `"hash"` for fragment-seam apps, and `marker` is the selector that must HOLD at capture (see below). Overlay-class states are the ones that can silently close.
- **`viewport`** is `outOfBand: true` — its `values` are consumed by the *invoker* (fed to `--matrix` / the reviewer's breakpoints), never by the scenario, because viewport is a launch-time fidelity dimension, not a URL state. Absent, the reviewer falls back to its own labeled default breakpoint list. **`native`** (a string, or an array for multi-monitor targets) declares the EXACT resolution(s) the app really ships at — true native fullscreen, not the maximized work-area a dev window gives you; an off-by-window-chrome height hides overflow defects that only exist at the real size. A declared `native` is a **mandatory cell**: a reviewer must include it (it is the default primary breakpoint), and no nearby size substitutes for it.
- **`cannotForce`** is a list of `{axis, value, reason}` objects — the machine-readable coverage holes (below).
- **`verifies`** is a one-line scope disclaimer: it states what a green run over this manifest does and does not attest (synthetic fixtures, not real-backend data correctness), so a pass is never over-read.

## Per-state markers

A `marker` is a selector that must be **visible on screen** when the frame is captured (the wait is for visibility, stricter than mere DOM presence — an attached-but-hidden marker does not count as held), and it is *app instrumentation* — a `data-` attribute the app renders when the state is actually on screen. `readySignal` is the global default marker; any state whose visibility can silently end (overlays, modals, transient panels) overrides it with its own marker. This is exactly what the `snapshotForced` primitive asserts: without a per-state marker it has nothing to check, and the "screenshot fired after the overlay closed" false-pass stays open.

The rule that keeps markers honest: **a state with no such attribute in the app yet does not get an invented marker — it goes in `cannotForce`.** A marker that cannot actually hold is worse than none; it launders an unforceable state into a green cell. Plain URL-param states (theme, fixture) need no marker at all, because the param holds the state for the whole page life.

## `cannotForce` — authoritative coverage holes

Each `cannotForce` entry declares a state the project cannot yet drive: the missing fixture, the un-instrumented overlay, the seam that does not exist. The manifest scenario **skips** those states and **lists them in its run output**, so a hole is never silently absent from a matrix. An adversarial reviewer treats `cannotForce` (and any state missing the marker instrumentation it would need) as an authoritative hole that BLOCKS a clean pass — the correct verdict is "cannot certify this cell," not a quiet pass over the states that happened to be forceable. Coverage holes are surfaced, never absorbed.

## The `drive` escape hatch

**`drive` replaces the forcing mechanism — the URL param composition — never the state list.** For states that lack an addressable URL seam — theme held in `localStorage`, an overlay pushed onto `history.state`, anything behind an interaction rather than an address — an axis adds a `drive` hook alongside its normal state list: it keeps its `values` (or its `flags`, each still carrying its own `marker`), and the hook composes the state some other way:

```jsonc
"theme":   { "values": ["light", "dark"], "drive": "./visual-states.drive.mjs#setTheme" },
"overlay": { "flags": {
  "settings": { "drive": "./visual-states.drive.mjs#openSettings", "marker": "[data-overlay='settings']" }
} }
```

The path is resolved **relative to the manifest file**; `#` names the export. The hook is invoked **once per state**, after base navigation, so it runs against an already-loaded page and drives it the rest of the way into the state. Its signature is `(page, h, stateName)`, where `stateName` is the value being forced (content/theme axes) or the flag name (overlay). A driven overlay-class state still declares its `marker` — the drive forces the state, the marker proves it held. One honesty caveat: a driven content/theme state is asserted only against the global `readySignal` (there is no per-value marker slot), which held before the drive ran — so the drive hook itself should assert the value actually applied (e.g. `h.expect` on a DOM reflection like a `data-theme` attribute). Declarative for the common URL case, executable only where the app genuinely has no addressable seam.

## Enumeration and the reviewer-derivation rule

**Axis `values` are ordered default first, worst last** — the one-hot pass varies against the first-listed defaults, and the all-worst composed state and the reviewer's own derivation both take the LAST-listed value of each axis; a project encodes its own severity judgement by ordering its values accordingly.

**Default enumeration** (per route): the baseline state, then each axis value varied *one-hot* against the first-listed defaults of the other axes, then **one all-worst composed state** — worst content combined with the non-default theme and each overlay in turn. The full cross-product is gated behind `VISUAL_STATES_FULL=1`, because the one-hot slice plus the composed worst-corner catches most defects at a fraction of the cell count.

One trap worth naming: when an axis's worst (last-listed) value sits in `cannotForce`, the default all-worst composed state is skipped along with it — default enumeration then never produces the effective worst corner at all. Reach it with `VISUAL_STATES_FULL=1` or a hand-composed state at the worst *forceable* value; a skipped composed state is not composed-corner coverage.

**The reviewer does not inherit that named happy-path matrix.** An adversarial reviewer derives its own matrix by picking the *worst* value on every axis and composing them **simultaneously** — overlay open, opposite theme, empty/error content, smallest viewport, all at once — because the corner where several stressors coincide is where layout, contrast, and clipping bugs actually live. The manifest's enumerated states are a floor the builder ships against, not a ceiling the reviewer accepts.
