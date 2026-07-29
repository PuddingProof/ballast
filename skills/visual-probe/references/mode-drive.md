# Mode: `drive` — script it, assert on it

```
node $P run <scenario.mjs> [--url <url>] [--matrix M] [--crop SEL]
```

Copy a template from the skill's `scenarios/` dir, edit only navigate→drive→assert, then `run` it.
The scenario is the only agent-authored part; browser launch/teardown, the per-cell context at the
right device-scale-factor, capture, magnify, hashing, the manifest, and the exit code all belong to
the harness. A failed assert is a non-zero exit — that is the signal to branch on.

## Scenario API — complete; never excavate `lib/` to rediscover it

A scenario is `export default async (page, h) => {…}`:

| Call | Does |
|---|---|
| `h.goto(url?)` | navigate (defaults to `--url`), through the fail-closed local-origin guard |
| `h.state('<js-expr>')` | evaluate in page context — the verification seam (and the only honest read inside a `<canvas>`) |
| `h.read(sel?)` | structured text + interactive-control inventory of a subtree |
| `h.expect(getter, predicate, msg)` | record an assert; any failure → non-zero exit. Never throws on a failed predicate |
| `h.snapshot(label, {crop?, fullPage?})` | capture this cell |
| `h.snapshotForced(label, {marker, …})` | wait for `marker` to be VISIBLE, then capture — a marker that never held is a failed assert |
| `h.page` | the full Playwright `Page`, for arbitrary drive logic |

`h.snapshotForced` is what closes the "screenshot fired after the overlay closed" false-pass: a
state that never rendered, or rendered and silently ended, fails its assert instead of laundering
into a green cell.

## Capability boundary

The documented seams are capture, crop/magnify, matrix, scenario drive, the calls above, and exit
codes. Console/network logs, request interception, `prefers-reduced-motion`/RTL emulation, and
animation-freezing are reachable only through scenario JS on the `page` object — e.g.
`page.on('console')`, `page.emulateMedia({reducedMotion:'reduce'})`, injecting `*{animation:none}`.
A capability you cannot reach is a reported coverage gap: never a silent skip.

## Measure, don't eyeball

Contrast ratios, font sizes, rect overlaps, hit-target sizes, and overflow come from scenario-side
measurement (`h.state` over `getComputedStyle` / `getBoundingClientRect` / canvas pixel sampling),
not from looking at an image. The crop confirms a defect is visible; the number proves it.

**Multi-step drive across a state change belongs in a scenario**, not a chain of `session do`
calls — real `await`/assert per step beats a chain where one failure cascades into the rest.
