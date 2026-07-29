# Mode: `serve` — the session's one origin

```
node $P serve start <dir> [--port N]   # detached loopback static server → prints the URL (port auto-picked)
node $P serve status | stop            # 'stop' at close-out — it outlives the session otherwise
```

`serve start` reaps a DEAD registered server, never a live forgotten one — so close-out is yours,
not the next run's.

## Lifecycle: one origin, main session, handed down

Origin doctrine — start once, record, hand down, tear down — is the visual-verification-gate
skill's setup stage; this body owns the mechanism. Parallel reviewers share that single origin
headlessly, so N instances are never needed, and a leaf never starts, stops, or signals a process:
a caller-provided origin is the caller's to stop.

**Never probe the user's live dev server.** An app under probe fires its own beacons at whatever
origin served it: a tab-close beacon has armed a live server's shutdown and killed it under the
user, twice. Any server with lifecycle/liveness endpoints (an `--idle-stop` script, heartbeat
beacons) is in that hazard family.

The bundled server closes the family by construction: no `/api` (a beacon POST dies as an inert
405), `Cache-Control: no-store` (a long-lived window cannot keep rendering pre-edit CSS/JS),
event-loop concurrency (held keep-alive connections starve a stock `python -m http.server`, not
this), loopback-only, GET/HEAD-only, traversal-guarded.

## Reach-it recipe by frontend

| Frontend | Reach it |
|---|---|
| Static HTML / JS | `file://` the page directly; needs an http origin (a `fetch()` of a data file)? → `serve start`. Point data loads at a synthetic fixture — NEVER one holding real or sensitive data. |
| Web app / SPA (Vite, SvelteKit, …) | The project's own dev server, started here in the main session from the project's EXISTING tooling (its package.json `scripts`, or a local `node_modules/.bin` binary) — never a freshly-fetched tool. |
| Tauri / WebView2 desktop app | Its localhost dev server → `shot http://localhost:<port>/`. For native-IPC states (e.g. `window.__TAURI__`), launch the app with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<N>` and drive it with `run … --cdp ws://127.0.0.1:<N>`. |
| Canvas / WebGL game | `file://` directly; assert via the app's exposed state global (e.g. `window.__STATE__`) or an autoplay / `#smoke` hook. |

Cannot start an origin from what is installed? That is a blocked run and a named gap — report it
rather than spending the run fighting the server, and never install to get past it.
