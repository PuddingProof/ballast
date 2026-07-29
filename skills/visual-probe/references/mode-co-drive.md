# Mode: `co-drive` — you and the user drive one window

For interactive debugging: the human drives the app by hand, you see and drive the *same* window —
a headed Edge on a CDP port, the inverse of the ephemeral matrix capture.

```
node $P serve start <repo-root>          # FIRST, for a static/localhost frontend — the isolated origin (see mode-serve.md)
node $P session start [url] [--port N]   # open the shared window (either party can launch; --port if 9222 is taken)
node $P session look [--crop S]          # snapshot the CURRENT state, no reload — what the human is looking at
node $P session read [selector]          # structured TEXT: text + control inventory + ready-to-paste selectors + viewport
node $P session do <act> …               # drive it: click|type|press|hold|hover|scroll|nav — the human watches it move
node $P session stop                     # full close-out (kills browser, removes profile) — always, plus `serve stop`
```

## The contract — two drivers, one surface

The shared window is live state both parties mutate: it moves between your turns, and your model of
it goes stale the moment their hands touch it. Start every step from a fresh `look`/`read`, never
from memory:

- **Before driving (or planning a drive)** — the thing you are about to do may already be done by hand; acting unlooked redoes their work.
- **Before asking the human to reproduce, apply, or show anything** — mid-session included: they may already have, and the answer may already be on screen.
- **When verifying** — drive the SHARED window to the state under discussion. A headless pass on your own end defeats the mode's point (both parties seeing the same thing at the same time); headless `shot`/`run` is for solo checks.

`read` is the antidote to a dense dashboard a screenshot cannot disambiguate — it returns
`button: Export [disabled]`, `checkbox: Live tail [checked]`, `Region = "eu-west"` as text. Rule of
thumb: `look`/`--crop` to **see** a widget, `read <selector>` to **know** its values and states.
`do hold <key> <ms>` for held controls (game movement).

## Driving reliably

- **Drive by the `→ role=…[name=/…/i]` selector `read` prints, not a hand-built one.** `read`'s printed name is NOT Playwright's computed accessible name, so an exact `name="Atlas"` silently misses — the emitted case-insensitive regex matches. If one still misses, shorten the regex to a distinctive word.
- **Labels are state-dependent — re-`read` after any route/theme change** (a metric named "Spend" in one theme is "Honey" in another). A missed `do` fails fast (~4s).
- **Multi-step drive across a state change → write a scenario** (`mode-drive.md`), not a chain of `do`s.
- **A full-canvas app (game) has no selectors for `session do`** — co-drive it by attaching a scenario to the live session's CDP port (`run <scenario.mjs> --cdp ws://127.0.0.1:<port>`): coordinate clicks plus the app's state seam, driving the same window the human watches.
- **Inner scroll pane** (an `overflow:auto` container a page-level wheel cannot reach): `do scroll <selector> <px|top|bottom>` scrolls that container directly; bare `do scroll <px>` scrolls the page.
- **A transient overlay (hover tooltip) is timing-coupled:** a human-held hover is only captured if held until `look` — prefer agent-driven `do hover <sel>`, which latches it.
- **After a CSS/JS edit, the authoritative look is a fresh headless `shot`/`run`, or at minimum a reload** — an already-open window keeps rendering its cached sub-resources (the bundled no-store server makes a reload sufficient; assume any other origin is stale even when `index.html` is cache-busted).
- **`look`/`read` print `⚠ DEGENERATE` when the shared window's viewport degrades** (a real long-session facet — e.g. a 599×38 window neither party resized): that is the window, not the app — `session stop`, then a fresh `start`.

Session frames default to the OS temp dir under `visual-probe-out` (`session start`/`stop` wipe
it) — pass `--out <dir>` only to keep frames on purpose.
