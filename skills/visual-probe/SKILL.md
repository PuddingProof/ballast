---
name: visual-probe
description: >-
  The main session's instrument panel for a real rendered browser UI: capture how a frontend
  ACTUALLY renders (never inferred from code) across window sizes / DPI / device-scale, drive an
  interactive UI and assert on its state, or co-drive a shared headed window with the user. Runs a
  bespoke pinned-Playwright harness on demand — no standing daemon. Fused one-shot verbs
  (glance | review-capture) and deterministic instruments (measure | crop) alongside the
  interactive mode paths (shot | drive | states | serve | co-drive | doctor).
when_to_use: >-
  Load in the MAIN session when you are about to run the harness yourself: "let me see the app",
  "screenshot it", "does this look right", "check the layout / rendering", "why does X look broken
  / off / misaligned", "drive the game", "co-drive the app / I'll drive by hand while you watch",
  "debug the UI interactively". NOT for a subagent — a dispatched leaf never loads this skill: it
  is handed an origin URL, an out-dir, and the harness contract in its dispatch brief (the
  visual-verification-gate skill owns that brief), and it owns no process lifecycle. Not for
  non-visual code edits, backend logic, or documentation tasks.
argument-hint: "glance | review-capture | measure | crop | shot | drive | states | serve | co-drive | doctor"
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
hole blocks a clean verdict; it is reported, never absorbed. The fused verbs compose their cells
into contact sheets (`sheets[]`) that carry the same rule one level up: a sheet settles composition
and hierarchy, never small-text ink or a non-integer-DSF cell — those escalate to `crop`/`measure`.
A hole renders INTO the sheet as a labeled placeholder tile, which is never a captured cell.

## What this harness cannot observe

A clean pass covers only what the method can see — name the blind spots instead of over-claiming:
- Synthetic input (CDP click/type) exercises a different path than the OS compositor — real-input-only defects survive a green probe.
- CDP sees the document, never the host OS window — native window behavior (F11/fullscreen, geometry, decorations; e.g. a Tauri/WebView2 shell) is invisible and unsafe to drive synthetically: a manual-verify cell.
- One engine's rendering says nothing about another's; a Chromium capture cannot clear a bug the user sees in Firefox.
- When two sizing/breakpoint mechanisms overlap, probe the zone BETWEEN their thresholds, not just each named checkpoint.
- Fixtures exercising guards + happy paths still miss the real corpus's typical-longest values — pull real worst-case data in before calling a text-layout control clean.

## Fused verbs and instruments — one process, one browser launch

These do the whole job in a single invocation: preflight, capture, in-page assertions, composition,
and a budget stamp. They are what a dispatch brief hands a leaf (the gate skill templates the brief;
this table is the orchestrator's own copy). They REJECT `--cdp` — the reuse gate is what makes one
launch safe. The fused verbs and `crop` stamp `budget{invocations, invocations_total, verbs,
launches, stage_ms, wall_ms}` into the manifest, keyed by `--out`, so a claimed cost is checkable
rather than self-reported: `invocations` counts ONE dispatch's calls (scoped by `--epoch`),
`invocations_total` the out-dir's whole ledger. With `--states`, the sweep runs against `--url` only
— extra `--urls` are captured as plain cells.

| Verb | Command |
|---|---|
| `glance` | `node $P glance --url <base> [--urls u1,u2] [--matrix M] [--states F --skip-drive-hooks] [--suppressions F] [--settle MS] [--deadline MS] [--epoch ISO] [--baseline CELL] --out <dir>` — viewport-clamped cells, rung-0 at shutter, contact sheets |
| `glance --wait` | `node $P glance --wait <out-dir> [--since ISO] [--timeout MS]` — stdlib-only poll for a capture fired ahead of a dispatch; `--since` is the dispatch epoch, so evidence older than it keeps polling instead of resolving stale; exit 3 = not ready, run the full verb instead. Valid on `review-capture` output too |
| `review-capture` | `node $P review-capture --url <base> [--urls …] [--states F] [--matrix M] [--group RE] [--no-dsf-triad] [--settle MS] [--deadline MS] --out <dir>` — matrix sweep with console/pageerror/requestfailed listeners attached per context, DSF triad at native, sheets grouped per route×theme |
| `measure` | `node $P measure --url <u> [--selector S \| --selectors S1,S2] [--checks contrast,rects,fonts,targets,overflow] --out <dir>` — compact per-check summary to stdout, full record in `measure-<n>.json`; composited-pixel contrast, so it survives gradients and translucency |
| `crop` | `node $P crop --url <u> --selector S [--matrix M] [--magnify N] --out <dir>` — magnified evidence for one region, post-hoc |

Exit codes are uniform: **0** = normal, including a `--deadline` partial flush (the unshot cells
become named `coverageHoles`, and partial evidence is still evidence); **1** = blocked, meaning zero
capture — the fused verbs still write a blocked manifest (`measure`/`crop` have none to write); **2** = usage; **3** = `--wait` timeout. There are no
retries anywhere: a failed cell is a named hole, never a second attempt.

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
