---
name: visual-probe
description: >-
  The main session's instrument panel for a real rendered browser UI: capture how a frontend
  ACTUALLY renders (never inferred from code) across window sizes / DPI / device-scale, drive an
  interactive UI and assert on its state, or co-drive a shared headed window with the user. Runs a
  bespoke pinned-Playwright harness on demand — no standing daemon. Six mode paths
  (shot | drive | states | serve | co-drive | doctor), each with its own reference body.
when_to_use: >-
  Load in the MAIN session when you are about to run the harness yourself: "let me see the app",
  "screenshot it", "does this look right", "check the layout / rendering", "why does X look broken
  / off / misaligned", "drive the game", "co-drive the app / I'll drive by hand while you watch",
  "debug the UI interactively". NOT for a subagent — a dispatched leaf never loads this skill: it
  is handed an origin URL, an out-dir, and the harness contract in its dispatch brief (the
  visual-verification-gate skill owns that brief), and it owns no process lifecycle. Not for
  non-visual code edits, backend logic, or documentation tasks.
argument-hint: "shot | drive | states | serve | co-drive | doctor"
allowed-tools: Read, Write, Bash, Glob
---

# visual-probe

On-demand visual-verification harness: drives headless system Edge across a fidelity matrix,
captures native-resolution frames, optionally drives scripted input and asserts on a page-state
seam, then exits. No standing server.

```
P=${CLAUDE_SKILL_DIR}/scripts/probe.mjs      # every command here and in the mode bodies uses $P
node $P --help                               # full flag reference
```

The engine pin lives in the tool's `package.json` — `preflight` checks readiness, `doctor` checks
the env; don't restate the version.

**This panel is the main session's.** You run the harness. A dispatched leaf never loads this skill
and owns no process lifecycle — it gets an origin URL, an out-dir, and the harness contract from
the visual-verification-gate skill's dispatch brief. **Never name this probe in prose alone:** a
leaf told to "check how it looks" improvises — driving whatever browser is already installed, by
whatever script it writes (two live incidents, 2026-07-16). No guard catches that; only handing it
the brief does.

## Preflight — first use, and before ANY dispatch

`node $P preflight` is dependency-free and safe anywhere (no npm, no network, no prompts): exit 0 =
ready; exit 1 = node_modules missing/stale — re-armed by every plugin update, since node_modules is
never vendored. Clearing it means `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci` in the skill dir,
which fires the package-install-guard prompt: an interactive gate only an attended session can
answer (by design, not a bug). So clear it here, then `doctor` — and clear it *before* dispatching
any browser-capable leaf, because an unattended leaf cannot answer an interactive guard and must
never try.

## The origin is yours

Origin doctrine — one per session, main-session-owned, recorded, torn down — is the
visual-verification-gate skill's setup stage; follow it there. The instrument-side hazard is this
skill's: **never probe the user's live dev server** — an app under probe fires lifecycle beacons at
whatever origin served it, and a tab-close beacon has killed a live server under the user. The
bundled isolated server closes that family by construction; the `serve` mode body has the details.

## Reading a capture — the load-bearing discipline

Your image-Read path downscales, hiding sub-pixel defects in full-frame thumbnails. So: read
`manifest.json` first — cells, `diverges:true` flags, and `coverageHoles` (states the run could not
force) — then read ONLY the `.xN.png` magnified crops of divergent or suspect cells. Never judge
pixels from a full-frame `.png`, and never read a frame merely because you captured it. A coverage
hole blocks a clean verdict; it is reported, never absorbed.

## What this harness cannot observe

A clean pass covers only what the method can see — name the blind spots instead of over-claiming:
- Synthetic input (CDP click/type) exercises a different path than the OS compositor — real-input-only defects survive a green probe.
- CDP sees the document, never the host OS window — native window behavior (F11/fullscreen, geometry, decorations; e.g. a Tauri/WebView2 shell) is invisible and unsafe to drive synthetically: a manual-verify cell.
- One engine's rendering says nothing about another's; a Chromium capture cannot clear a bug the user sees in Firefox.
- When two sizing/breakpoint mechanisms overlap, probe the zone BETWEEN their thresholds, not just each named checkpoint.
- Fixtures exercising guards + happy paths still miss the real corpus's typical-longest values — pull real worst-case data in before calling a text-layout control clean.

## Mode paths

Read the mode body before running the mode; each is self-contained. Bodies live in
`${CLAUDE_SKILL_DIR}/references/`.

| Mode | Entry command | Body |
|---|---|---|
| `shot` | `node $P shot <url or path> --matrix default` | `mode-shot.md` — matrix planning, crops, CDP attach |
| `drive` | `node $P run <scenario.mjs> --url <url>` | `mode-drive.md` — scenario API, templates, asserts |
| `states` | `VISUAL_STATES=<abs> node $P run scenarios/states-from-manifest.mjs` | `mode-states.md` — manifest-driven capture, `--skip-drive-hooks`; manifest schema in `state-contract.md` |
| `serve` | `node $P serve start <dir>` | `mode-serve.md` — the session origin, reach-it recipe per frontend type |
| `co-drive` | `node $P session start [url]` | `mode-co-drive.md` — shared-window contract, drive discipline |
| `doctor` | `node $P doctor` · `node $P selftest` | Preflight above. `doctor` = env self-check (Edge channel, pinned version, non-integer DSF); `selftest` = harness regression guard (role→selector mapping + capture) |

Capture frames land in the OS temp dir (`$TMPDIR`/`%TEMP%`) under `visual-probe-out` by default
(out of any repo tree) — pass `--out <dir>` to keep them, and to hand them to a dispatch.
