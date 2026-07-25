---
name: visual-probe
description: >-
  See and DRIVE a real rendered browser UI on demand — when you need to LOOK at how a frontend
  actually renders (not infer it from code), check rendering across window sizes / DPI /
  device-scale, catch a visual defect a single screenshot hides, or click/type/scripted-drive
  to exercise an interactive UI and assert on its state. Runs a bespoke pinned-Playwright
  harness on demand (headless for capture; a headed shared window for co-driving; a bundled
  isolated static server so a probe never touches the user's live dev server) — no standing
  daemon.
when_to_use: >-
  Use whenever the user says things like "let me see the app", "screenshot it", "does this
  look right", "check the layout / rendering", "why does X look broken / off / misaligned",
  "test the UI", "drive the game", "co-drive the app / I'll drive by hand while you watch",
  "debug the UI interactively", or wants visual verification of any web / Tauri / canvas
  frontend (HTML, SvelteKit/Vite, KAPLAY, etc.). NOT for non-visual code edits, backend logic,
  or documentation tasks.
allowed-tools: Read, Write, Bash, Glob
---

# visual-probe

On-demand visual-verification harness: drives headless system Edge across a fidelity matrix, captures native-resolution frames, optionally drives scripted input and asserts on a page-state seam, then exits. No standing server. Tool: `${CLAUDE_SKILL_DIR}/`; full flags: `node ${CLAUDE_SKILL_DIR}/scripts/probe.mjs --help`. The engine pin lives in the tool's `package.json` — `preflight` checks readiness, `doctor` checks the env; don't restate the version.

**Readiness gate — check before use, and BEFORE any unattended dispatch.** `preflight` is dependency-free and safe anywhere (no npm, no network, no prompts): exit 0 = ready; exit 1 = node_modules missing/stale — re-armed by every plugin update, since node_modules is never vendored. Clearing it means `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci` in the skill dir, which fires the package-install-guard prompt — an interactive gate only an ATTENDED session can answer (by design, not a bug):
- Attended? Run the npm ci yourself, then `doctor`.
- Dispatching a background/unattended/scheduled agent that will use the probe? Preflight — and clear the gate if armed — in the attended session FIRST; an unattended leaf cannot answer an interactive guard (the plan-handoff skill's rule).
- You ARE the unattended leaf and preflight fails? Fail fast and report its output. NEVER run npm ci unattended — it stalls the session on a prompt nobody can answer.
- Dispatching any OTHER browser-capable leaf (not the visual-reviewer agent, which carries the contract in its own definition)? Front-load the harness contract in the dispatch prompt — probe entry point with preflight-first fail-fast, and the origin rule (headless, own/bundled server, never the user's live dev instance). Never name the probe in prose alone: a leaf briefed by prose improvises installs and drives whatever browser it finds (two live incidents, 2026-07-16).

## Reading a capture — the load-bearing discipline

Your image-Read path downscales, hiding sub-pixel defects in full-frame thumbnails. So: read `manifest.json` first (it lists cells + which are `diverges:true`), then read ONLY the `.xN.png` magnified crops of divergent cells — never the full-frame `.png` to judge pixels.

## Plan the capture matrix

Before calling a frontend verified, enumerate the cells you will capture — a green matrix is only as strong as its worst-covered cell, and the defects that ship are the ones no cell ever forced. The adversarial cells are the composed corners, not the one-hot slice: every overlay/modal OPEN, crossed with each theme, crossed with worst/empty/error content, at the viewport extremes — composed *simultaneously*, because a bug that needs three stressors at once never shows when you vary one axis at a time. Derive that worst corner deliberately rather than stumbling into it; the blind spots below then bound what even a full matrix can attest.

The mechanical path is the state-forcing contract: a project's `visual-states.json` declares its forceable states and the marker that proves each held, and the bundled `${CLAUDE_SKILL_DIR}/scenarios/states-from-manifest.mjs` enumerates and captures them (manifest discovered via the `VISUAL_STATES` env var, or `./.claude/visual-states.json` from the project root). See `${CLAUDE_SKILL_DIR}/references/state-contract.md` for the schema, the `cannotForce` coverage-hole semantics, and the reviewer's worst-cell derivation rule. A state the app cannot force belongs in `cannotForce`, where it reads as an authoritative hole — not an absent cell that quietly passes.

**`--cdp` attach trades enumeration for the real compositor.** Attaching to a running browser (`--cdp`) captures the true compositor's RENDERING — the one thing a fresh headless launch cannot reach — but input stays synthetic (the synthetic-input blind spot below survives attach), and attach is single-cell: it sees only the state that window is already in. The states×fidelity product exists only in fresh-launch enumeration, so use attach to confirm one real-compositor cell, never to cover the matrix.

## What this harness cannot observe

A clean probe pass covers only what the method can see — name the blind spots instead of over-claiming:
- Synthetic input (CDP click/type) exercises a different path than the OS compositor — real-input-only defects survive a green probe.
- CDP sees the document, never the host OS window — native window behavior (F11/fullscreen transitions, geometry, decorations; e.g. a Tauri/WebView2 shell) is invisible to the probe and unsafe to drive synthetically; it stays a manual-verify cell.
- One engine's rendering says nothing about another's; a Chromium capture cannot clear a bug the user sees in Firefox.
- When two sizing/breakpoint mechanisms overlap, probe the interaction zone BETWEEN their thresholds, not just each named checkpoint.
- Fixtures that exercise guards + happy paths still miss the real corpus's typical-longest values — pull real worst-case data into the fixture before calling a text-layout control clean.

## Commands

```
P=${CLAUDE_SKILL_DIR}/scripts/probe.mjs
node $P preflight                                # readiness gate (deps-free, safe unattended) — before first use / any dispatch
node $P doctor                                   # env self-check
node $P selftest                                 # regression guard: role→selector mapping + capture
node $P shot <url|path> --matrix default         # SEE: navigate + fidelity matrix
node $P run  <scenario.mjs> [--url <url>]        # DRIVE: per-cell, asserts → exit code
```

Capture frames land in the OS temp dir (`$TMPDIR`/`%TEMP%`) under `visual-probe-out` by default (out of any repo tree) — pass `--out <dir>` only to keep them.

`--crop <selector>` captures+magnifies one region. `--matrix quick` = 2-cell fast pass.

## Serve recipe

| Frontend | Reach it |
|---|---|
| Tauri / WebView2 desktop app | localhost dev server → `shot http://localhost:<port>/`. For native-IPC states (e.g. `window.__TAURI__`), launch the app with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<N>` and `run … --cdp ws://127.0.0.1:<N>`. |
| Web app / SPA (Vite, SvelteKit, …) | `shot http://localhost:<port>/` against the running dev server. |
| Canvas / WebGL game | `file://` directly; assert via the app's exposed state global (e.g. `window.__STATE__`) or an autoplay / `#smoke` hook. |
| Static HTML / JS | `file://` the page; needs an http origin (`fetch()` of a data file)? → the bundled `serve start` (below). Point data loads at a synthetic fixture — NEVER one holding real/sensitive data. |

**The bundled isolated server** — the co-drive origin for any static frontend, and the reach whenever one needs an http origin:

```
node $P serve start <dir> [--port N]   # detached loopback static server → prints the URL (port auto-picked)
node $P serve status | stop            # always `stop` at close-out — it outlives the session otherwise ('start' reaps a DEAD one, not a live forgotten one)
```

A probe must load from an origin YOU own. Never point one at a live server that has lifecycle/liveness endpoints (an `--idle-stop` serve.py, heartbeat beacons): the app under probe fires its own beacons at whatever origin served it — a tab-close beacon has armed a live server's shutdown and killed it under the user, twice. The bundled server closes the whole hazard family by construction: no `/api` (a beacon POST dies as an inert 405), `Cache-Control: no-store` (a long-lived window can't keep rendering pre-edit CSS/JS), concurrent (held keep-alive connections starve a stock `python -m http.server` — measured 9/20 dropped — but not this), loopback-only + traversal-guarded. An app that genuinely needs a dev server (Vite transform, API routes)? Start your OWN instance on a spare port — still never the user's.

## Driving

Copy a template from `${CLAUDE_SKILL_DIR}/scenarios/`, edit only navigate→drive→assert, `run` it. Full Playwright `page` is available; `h.state('<expr>')` reads the seam, `h.read(selector)` returns the structured text+control inventory, `h.expect(getter, pred, msg)` sets the exit code, `h.snapshot(label, {crop})` captures the cell, and `h.snapshotForced(label, {marker, ...snapshotOpts})` waits for the marker selector to HOLD before capturing — a marker that never held is a failed assert and a non-zero exit, closing the "screenshot fired after the overlay closed" false-pass.

The bundled `${CLAUDE_SKILL_DIR}/scenarios/states-from-manifest.mjs` needs no editing: it reads a project's `visual-states.json` (discovered via the `VISUAL_STATES` env var, or `./.claude/visual-states.json` from the project root), enumerates each declared state, and `snapshotForced`s it against the state's marker — the mechanical path for the capture matrix above (schema + semantics in `${CLAUDE_SKILL_DIR}/references/state-contract.md`).

For an **animated** canvas, cross-cell divergence Δ is partly temporal (a different frame per cell), not only scale — the magnified crop is ground truth, not the Δ.

## Live session (you + Claude co-drive)

For interactive debugging — the human drives the app by hand, Claude sees and drives the *same* window — use the persistent session (a headed Edge on a CDP port; the inverse of the ephemeral matrix capture):

```
P=${CLAUDE_SKILL_DIR}/scripts/probe.mjs
node $P serve start <repo-root>          # FIRST, for a static/localhost frontend: the isolated origin (see Serve recipe) — a co-drive never loads the user's live server
node $P session start [url] [--port N]   # open the shared window (either party can launch; --port if 9222 is taken)
node $P session look [--crop S]  # snapshot the CURRENT state, no reload — what the human is looking at
node $P session read [selector]  # structured TEXT: text + control inventory + ready-to-paste selectors + viewport
node $P session do <act> …       # drive it: click|type|press|hold|hover|scroll|nav — the human watches it move
node $P session stop             # full close-out (kills browser, removes profile) — always call when done (+ `serve stop` if you started one)
```

**The co-drive contract — two drivers, one surface.** The shared window is live state both parties mutate: like any surface a second actor owns concurrently, it moves between your turns, and your model of it goes stale the moment their hands touch it. Start every step from a fresh `look`/`read` of the current state, never from memory:
- **Before driving (or planning a drive)** — the thing you're about to do may already be done by hand; acting unlooked redoes their work.
- **Before asking the human to reproduce/apply/show anything** — mid-session included: they may already have, and the answer may already be on screen.
- **When verifying** — drive the SHARED window to the state under discussion; a headless pass on your own end defeats the mode's point (both parties seeing the same thing at the same time). Headless `shot`/`run` is for solo/CI-style checks.

`read` is the antidote to a dense dashboard a screenshot can't disambiguate — it returns `button: Export [disabled]`, `checkbox: Live tail [checked]`, `Region = "eu-west"` as text. Rule of thumb: `look`/`--crop` to **see** a widget, `read <selector>` to **know** its values/states. `do hold <key> <ms>` for held controls (game movement).

**Driving reliably (smoke-loop discipline):**
- **Drive by the `→ role=…[name=/…/i]` selector `read` prints, not a hand-built one.** `read`'s printed name is NOT Playwright's computed accessible name, so an exact `name="Atlas"` silently misses — the emitted case-insensitive regex matches. If one still misses, shorten the regex to a distinctive word.
- **Labels are state-dependent — re-`read` after any route/theme change** (a metric named "Spend" in one theme is "Honey" in another). A missed `do` fails fast (~4s) and the error points back here.
- **Multi-step drive across a state change → write a `run <scenario.mjs>`**, not a chain of `do`s — real `await`/assert per step beats a chain where one failure cascades into the rest.
- **A full-canvas app (game) has no selectors for `session do`** — co-drive it by attaching a scenario to the live session's CDP port (`run <scenario.mjs> --cdp ws://127.0.0.1:<port>`): coordinate clicks + the app's state seam, driving the same window the human watches.
- **Inner scroll pane** (an `overflow:auto` container a page-level wheel can't reach): `do scroll <selector> <px|top|bottom>` scrolls that container directly; bare `do scroll <px>` scrolls the page.
- **A transient overlay (hover tooltip) is timing-coupled:** a human-held hover is only captured if held until `look` — prefer agent-driven `do hover <sel>`, which latches it.
- **After a CSS/JS edit, the authoritative look is a fresh headless `shot`/`run`, or at minimum a reload** — an already-open window keeps rendering its cached sub-resources (served by the bundled no-store server, a reload picks up everything; any other origin, assume stale even when `index.html` is cache-busted).
- **`look`/`read` print `⚠ DEGENERATE` when the shared window's viewport degrades** (a real long-session facet — e.g. a 599×38 window neither party resized): that's the window, not the app — `session stop`, then a fresh `start`.

Capture frames default to the OS temp dir (`$TMPDIR`/`%TEMP%`) under `visual-probe-out` (out of any repo tree; `session start`/`stop` wipe it) — pass `--out <dir>` only to keep frames on purpose.
