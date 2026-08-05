# visual-probe

Bespoke, on-demand **visual-verification harness** for Claude Code. Lets an agent (or you) *see* how a frontend actually renders and *drive* it interactively, without a single screenshot lying about it.

- **No MCP server, no daemon, nothing standing.** A pinned Playwright *library* + a thin CLI. It launches, runs, captures, tears the browser down, and exits — invoked like any other command.
- **No browser download.** Drives the system **Edge** (`channel:msedge`, headless) — the same Chromium engine Tauri/WebView2 apps render with. `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` at install.
- **Fidelity-matrix first.** Captures across viewport × device-scale-factor (incl. a non-integer scale and a DPI≠1) because a single clean-integer-scale screenshot masks boundary-tie blit defects.

Why a library and not the official Playwright **MCP plugin**: the plugin ships `npx @playwright/mcp@latest` (unpinned, auto-updating, always-on, with an RCE-equivalent `browser_run_code_unsafe` in its default toolset). This harness is the zero-standing-surface alternative — pinned, on-demand, with no model-facing code-execution tool.

## Setup

```bash
cd skills/visual-probe
node scripts/probe.mjs preflight                       # deps-free readiness check — safe unattended; exit 1 = install needed
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci      # installs the pinned playwright only (no browsers)
node scripts/probe.mjs doctor                          # self-check: Edge channel, pin, non-integer DSF capture
```

`node_modules` is never vendored in the plugin, so this `npm ci` is a **first-run step, and re-needed after every plugin update** — `package-install-guard` will prompt before it runs; that's by design, and it makes the install an **interactive gate only an attended session can clear**. Before any unattended/background dispatch that assumes the probe works, run `preflight` (and the `npm ci`, if it fails) attended first; an unattended agent whose preflight fails must fail fast and report its output — never run `npm ci` itself.

The engine version is pinned exactly in `package.json` + `package-lock.json`. Bump it only deliberately, re-run `doctor`, and re-audit for CVEs (`npm audit`). Edge auto-updates; if it ever outruns the pinned Playwright's CDP support, `doctor` surfaces it.

## Commands

`node scripts/probe.mjs --help` lists every flag. The shape:

| Command | Use |
|---|---|
| `glance --url <base> [--urls u1,u2] [--matrix M] [--states F --skip-drive-hooks] [--deadline MS] [--epoch ISO] --out DIR` | FUSED cheap rung: preflight + viewport-clamped capture + rung-0 + contact sheets, one process, one browser launch. `glance --wait <dir> [--since ISO]` polls for a capture fired ahead of a dispatch instead (exit 3 = run it yourself). |
| `review-capture --url <base> [--urls …] [--states F] [--matrix M] [--group RE] [--no-dsf-triad] --out DIR` | FUSED deep rung: full-page frames, the state sweep, console/pageerror/requestfailed listeners per cell, the DSF triad at the native cell, sheets grouped per route×theme |
| `measure --url <u> [--selector S \| --selectors S1,S2] [--checks contrast,rects,fonts,targets,overflow] --out DIR` | deterministic instruments — composited-pixel contrast, painted-box overlap, font sizes, hit targets, overflow: compact summary to stdout, full record in `measure-<n>-<pid>.json` |
| `crop --url <u> --selector S [--matrix M] [--magnify N] --out DIR` | post-hoc magnified evidence for one region; MERGES into the out-dir's manifest under `crops` (a failure lands in `cropHoles`, never coverage) |
| `doctor` | environment self-check |
| `selftest` | regression guard `doctor` doesn't cover: the `read.mjs` role→selector mapping round-trips + capture. Run after an Edge/Playwright/Claude-Code update. |
| `shot <url\|path> [--matrix M] [--crop sel] [--magnify N] [--out DIR]` | navigate + capture the fidelity matrix. "Just show me how it renders." |
| `run <scenario.mjs> [--url <url>] [--matrix M] [--crop sel] [--out DIR] [--cdp ws://…] [--headed] [--allow-remote]` | run a scenario per matrix cell; failed asserts → non-zero exit |
| `serve start <dir> [--port N]` · `serve status` · `serve stop` | bundled **isolated static server** (detached; loopback-only, `Cache-Control: no-store`, GET/HEAD-only, no `/api`, traversal-guarded) — the origin a probe/co-drive loads a static frontend from, **never the user's live dev server** (the app's own lifecycle beacons can arm a live server's idle-stop and kill it; held keep-alive connections wedge a stock `python -m http.server`; a caching origin leaves a long-lived window rendering pre-edit CSS/JS). Port auto-picked; `start` reaps a stale record itself. |

`--matrix` accepts a preset (`default` 5-cell, `quick` 2-cell, `desktop`) or an inline `WxH@DSF,WxH@DSF`. Bare paths are normalized to `file://`. Remote URLs are refused unless `--allow-remote` (fail-closed).

## Reading a result — the one discipline that matters

Your image-Read path **downscales**; a sub-pixel defect is invisible in a full-frame thumbnail. So:

1. Read `manifest.json` (in `--out`) first — each cell's label, `dsf`, `bytes`, capture files, and which cells `diverges:true`.
2. Read only the `.xN.png` **magnified native-resolution crops** of divergent cells — never the full-frame `.png` to judge pixels.
3. For an **animated** canvas, cross-cell divergence Δ is partly *temporal* (different frame per cell), not only scale fidelity — the magnified crop is ground truth, not the Δ alone.

## Scenarios

`scenarios/` holds copy-and-edit templates (`canvas-game.mjs`, `localhost-spa.mjs`, `static-html.mjs`). A scenario default-exports `async (page, h) => {…}` and does only **navigate → drive → assert**; the harness owns launch/teardown, the matrix loop, capture, magnify, hashing, manifest, and exit code. `h.goto()`, `h.state('<expr>')` (the page-state seam), `h.read(selector)` (structured text+control inventory), `h.expect(getter, pred, msg)`, `h.snapshot(label, {crop})`.

## Tier-1: driving a live Tauri WebView2

Bare Edge has no `window.__TAURI__`/IPC. For IPC-coupled states, launch the app with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<N>` and attach: `run <scenario> --cdp ws://127.0.0.1:<N>`. (DSF matrix is disabled in attach mode — single live cell.)

## Live session — shared browser (you + Claude co-drive)

The inverse of the stateless capture: a long-lived, **headed** Edge that the human drives by hand and Claude drives over CDP — *one* window, both ways (you see Claude's actions; Claude `look`s at the state you produced). Launched detached (it outlives the launcher) with a dedicated profile (never the daily browser).

```bash
node scripts/probe.mjs session start [url] [--port N]   # open the shared window (--port if 9222 is taken)
node scripts/probe.mjs session look             # Claude snapshots the CURRENT state — no reload
node scripts/probe.mjs session read [selector]  # structured TEXT: visible text + control inventory (name/value/state)
node scripts/probe.mjs session do <action> …    # Claude drives it; the human watches
node scripts/probe.mjs session stop             # close-out
```

`read` returns text, not pixels — `button: Export [disabled]`, `checkbox: Live tail [checked]`, `Region = "eu-west"` — to disambiguate a dense dashboard a screenshot can't resolve. It also emits a **ready-to-paste `→ role=…[name=/…/i]` selector per control** (the printed name ≠ Playwright's computed a11y name, so a hand-built exact selector silently misses — drive by the emitted regex one) and the **live viewport** (`W×H @ DSF×`, so a co-driven resize/zoom is confirmable numerically). (Scenarios get the inventory via `h.read(selector)`.)

`do` actions: `click <sel>`, `type <sel> <text>`, `press <key>`, `hold <key> <ms>` (held controls — game movement, etc.), `hover <sel>`, `scroll [sel] <px|top|bottom>` (with a selector → scrolls that inner container; without → the page), `nav <url>`. A missed selector fails fast (~4s, `--timeout` to override) with a hint to use the emitted regex name. Either party can run `start`; once the debug port is up, Claude attaches identically.

### Lifecycle & cleanup — the close-out

`session stop` is the full close-out: it kills the Edge process tree, removes the session file, **and removes the dedicated profile** — nothing accumulates between sessions (verified: 0 orphan processes after stop). Notes:
- **Closed the window with the X instead of `stop`?** No harm — the next `session start` detects the stale record (dead port), reaps it, and proceeds.
- **The profile is ephemeral** (the OS temp dir's `visual-probe-profile`, recreated per session) — logins don't persist; that's the no-buildup tradeoff.
- **Capture output is temp + auto-wiped.** `look`/`shot`/`run` write PNGs into `--out` (default: the OS temp dir's — `$TMPDIR`/`%TEMP%` — `visual-probe-out`, **never the repo tree** — so a frame can't be git-added by accident); `session start`/`stop` clear it. Pass `--out <dir>` only to keep frames on purpose.

## Security posture

Pinned + lockfile (never `npx`/`@latest`); system Edge so no unsigned binary download; fresh **in-memory** context per cell (no persistent profile / credentialed session); **fail-closed local-origin** guard; **no standing daemon** (the optional `serve` server is on-demand and explicitly torn down — `serve stop` at close-out; it's loopback-only, GET/HEAD-only, traversal-guarded lexically AND by realpath, so an in-root symlink can't escape) and **no model-facing code-exec tool** — the only JS that runs is the agent-authored, reviewable scenario. The process runs as the user (can read `~/.claude`), so the local-origin default and scenario reviewability are the trust boundary — same trust as any Bash call.

Agent trigger + serve recipes live in the `visual-probe` skill (`skills/visual-probe/`).
