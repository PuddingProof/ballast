---
name: visual-probe
description: >-
  Visual verification for frontend work, main session only: start and own the one session origin, dispatch the visual-glance / visual-reviewer done gate with a printed brief, act on the verdict, tear down — and capture or drive a real rendered browser yourself (shot | drive | states | serve | co-drive | measure | crop) through the bundled pinned-Playwright harness.
when_to_use: >-
  Load when an arming injection says "frontend touched — load the visual workflow skill now", when you are about to edit a component/style/template, dispatch anything that will look at a UI, or call frontend work done; and for "let me see the app", "screenshot it", "does this look right", "why does X look off", "drive / co-drive the app". Never in a subagent: a dispatched leaf gets a printed brief, not this skill. Not for backend or docs work.
argument-hint: "glance | review | shot | drive | states | serve | co-drive | measure | crop | doctor"
allowed-tools: Read, Write, Bash, Glob
---

# visual-probe

Frontend work is done when an eye that wants to find defects has seen the rendered frames — not when types, tests, or code review pass. The harness does every deterministic step; you own the origin and the verdict.

```
P=${CLAUDE_SKILL_DIR}/scripts/probe.mjs      # every command below uses $P; `node $P --help` lists flags
```

## 1. Origin — one per session, yours, once

- **Preflight:** `node $P preflight` (deps-free). Exit 1 → `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci` in `${CLAUDE_SKILL_DIR}`, re-needed after every plugin update; it prompts, so clear it here, attended — never in a leaf.
- **Start it:** static target → `node $P serve start <dir>`; a real app → its own dev-server script. Never probe the user's live dev server (its tab-close beacon has killed one under the user); `mode-serve.md` has the reach-it recipe per frontend type.
- **Record + pin** (Bash tool — `bin/` is not on PowerShell's PATH):
  `ballast-visual-origin record --session-key ${CLAUDE_SESSION_ID} --url <u> --out-dir <session-scoped dir> --pid <pid>`
  `ballast-visual-origin pin --session-key ${CLAUDE_SESSION_ID} --url <u> --out-dir <dir> --native <WxH@1> [--matrix <cells>] [--states <visual-states.json>] [--ready <selector>] [--suppressions <f>] [--settle <ms>]`
  The pin fills every brief; re-pin when a claim changes.

## 2. Done gate — glance by default, reviewer on a named trigger

Dispatch **visual-glance** for ordinary visual work (2 cells for a micro-diff; 6 = 3 viewports × 2 themes by default; an axis you don't shoot is a declared hole). Dispatch **visual-reviewer** only for a **new surface, redesign, audit, measured-AA question, state-matrix/DSF coverage, or a glance `escalate`** — full mode runs as ≤3 parallel facets over the one origin (worst verdict governs, scopes concatenate, blind spots union).

The brief is printed, never typed:
`ballast-visual-origin brief --session-key ${CLAUDE_SESSION_ID} --out-dir <dir> --rung glance|review --intent "<what must be true>" [--holes "<axes not shot — why>"] [--mode targeted|full|delta] [--urls u1,u2]`
Paste its output as the leaf's whole prompt. Warm path (optional): fire the brief's capture line as ONE background Bash first, cap one in flight; the finished capture prints a `DISPATCH … --since` wait line — replace the brief's `Run this first` line with it verbatim, never a hand-composed timestamp.

Skipping the gate is allowed only out loud ("user-adjudicated live", or a named waiver) — never silently.

### Verdicts — what each obliges you

| Verdict | Your response |
|---|---|
| `pass` | Done — carrying every declared hole from the brief, verbatim, into the done-claim. |
| `pass_partial` | Done only with the verdict's named scope carried verbatim into your handoff. |
| `needs_work` | Fix in-line, then resume the SAME leaf with a scoped delta (name it `glance-<slug>-<n>`), or batch several fixes into one delta. Never a fresh full dispatch per micro-fix; never self-certify. |
| `needs_fixture` | The app lacks a seam to force that state: file the fixture/marker as work against `state-contract.md`. Never answer it with a reviewer dispatch. |
| `escalate` | You adjudicate against the trigger list; an unforceable state dressed as escalate is `needs_fixture`. Authorize the reviewer out loud, or act on the glance's evidence and say so. |
| `blocked` | Environment — a missing pin, a dead origin, a brief without a command. Fix it, re-dispatch. |

Interrogating a finding (re-capture, crop, measure) is a bounded leaf's work; a one-command check stays in-line.

## 3. Close-out

`ballast-visual-origin teardown --session-key ${CLAUDE_SESSION_ID}` (`list` first) kills only what this session recorded; also `serve stop` / `session stop` anything you opened. A "failed" background-task notice on teardown is the server exiting — benign. The dead-session sweep reaps what a crashed session left: a backstop, not your close-out.

## 4. Driving it yourself

Read `manifest.json` first — `coverageHoles` (reported, never absorbed), `rung0`, `diverges` — then only magnified crops or contact sheets: a full-frame Read downscales, so it settles composition, never small-text ink or a non-integer-DSF cell. Blind spots a green run never clears: synthetic input ≠ the OS compositor; CDP never sees the host window (Tauri/WebView2 F11, geometry — a manual cell); Chromium says nothing about Firefox; the zone BETWEEN two overlapping breakpoint mechanisms; fixtures vs the real corpus's longest values.

| Verb | Command | Body |
|---|---|---|
| `glance` · `review-capture` | `node $P glance --url <u> [--urls …] [--matrix M] [--states F --skip-drive-hooks] [--ready SEL] [--suppressions F] [--settle MS] [--deadline MS] --out <dir>` — fused: one process, one launch, rung-0 at shutter, contact sheets. `--wait <dir> --since ISO` polls a capture fired ahead (exit 3 = run the verb yourself) | — |
| `measure` · `crop` | `node $P measure --url <u> --selector S --checks contrast,rects,fonts,targets,overflow --out <dir>` · `node $P crop --url <u> --selector S --matrix M [--magnify N] --out <dir>` | — |
| `shot` | `node $P shot <url\|path> --matrix default` | `mode-shot.md` |
| `drive` | `node $P run <scenario.mjs> --url <u>` | `mode-drive.md` |
| `states` | `VISUAL_STATES=<abs> node $P run scenarios/states-from-manifest.mjs --url <u>` | `mode-states.md`; schema in `state-contract.md` |
| `serve` | `node $P serve start <dir>` | `mode-serve.md` |
| `co-drive` | `node $P session start [url]` | `mode-co-drive.md` |
| `doctor` | `node $P doctor` · `node $P selftest` | — |

Exit codes everywhere: 0 normal (a `--deadline` partial flush included — unshot cells are named holes) · 1 blocked · 2 usage · 3 `--wait` timeout. No retries: a failed cell is a hole, never a second attempt. Frames default to the OS temp dir; `--out` keeps them.
