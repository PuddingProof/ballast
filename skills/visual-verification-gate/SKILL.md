---
name: visual-verification-gate
description: >-
  The end-to-end workflow for any session with visual changes: arming, the one session origin the
  main session owns, the in-loop see-and-fix reflex, the two-rung done gate (a cheap glance by
  default, the deep instrument only on named triggers), verdict semantics, the leaf dispatch brief,
  and close-out. Load it EARLY — at the first frontend touch, not at "done".
when_to_use: >-
  Load the moment a session acquires visual work: an arming injection says "frontend touched — load
  the visual workflow skill now"; you are about to edit or plan a component/style/template change;
  you are about to dispatch anything that will look at a UI; or you are about to call frontend work
  done. Also on the tell — you are typing a hedged caveat onto a frontend completion ("worth a quick
  visual check on your end"), which IS this gate firing. A main-session skill: it decides which
  check runs, who starts what, and what a verdict obliges. Not visual-probe (that owns driving and
  capturing a browser, and a dispatched leaf never loads it), not integration-gate (the seams of a
  multi-part diff). Armed only by frontend work — a session that touches none never pays for this,
  and a load with no visual work in sight is a mis-fire: say so in a line and move on.
---

# Visual Verification Gate

Frontend work is not done until the rendered pixels have been seen, in their worst states, by an eye that wants to find defects. Types certify shapes, tests certify behavior, code review certifies source — none of them ever looks at a frame.

## 1. Arm — you are here early, on purpose

You load at the *first frontend touch*, not at "done" — everything below assumes edits are still ahead of you. Loaded late? Skip to the done gate, running setup first if no origin exists. What never waits: catching yourself writing "worth a quick visual check on your end" means you have located an unlooked-at surface and are about to ship it to the user's eyes instead of yours. Look now. Same tell, no hedge: a completion written with zero captures taken this turn and no verdict to cite — the UI described in the future tense, from the source you edited rather than from a frame.

## 2. Setup — one origin, main session, once

Lazily, at first need, and never again: **one origin per session, started here**. A dispatched leaf is hard-denied process lifecycle at the root — it starts, backgrounds, and kills nothing — so an origin it wasn't handed is a `blocked` verdict, not its problem to solve.

- **Start it.** Static target → the probe skill's bundled isolated server (`serve` mode). A real app → the project's OWN dev-server script, from its existing tooling. Never point at the user's live dev server; probe's serve body owns that hazard and the reach-it recipe per frontend type.
- **Record it** the moment it is up, so close-out and the dead-session sweep can both find it: `ballast-visual-origin record --session-key ${CLAUDE_SESSION_ID} --url <url> --out-dir <dir> --pid <pid>`
- **Session-scope the out-dir.** A shared temp dir is how one session's teardown wipes a concurrent session's evidence.
- **Clear preflight before the first dispatch** (probe's `preflight`), and fold every tooling/server permission into **at most one** authorization ask, attended, here — an unattended leaf cannot answer an interactive gate and must never try. OS-level dialogs (firewall, per-command prompts) are an accepted residual.

## 3. Build loop — capture, look, fix

At loop boundaries — not per keystroke, not once at the end — force the state you just touched: worst content, both themes, any overlay over it, **composed together** rather than one axis at a time (probe's `shot` and state-contract bodies own the how). Then *look at the capture* and fix objective defects in the same turn: overlap, clipping, contrast, bad wrap. A visual edit you never rendered is an edit you made blind — and your own look is hygiene, not evidence: it runs on the states you expected to work, so it never stands in for the gate below.

## 4. Done gate — the ladder

**Default: the glance.** Dispatch the visual-glance agent — a small worst-case cell set, bounded reads, minutes. That is the check for ordinary visual work: a tweak, a component, a fix, a re-verify. **Escalate to the instrument** (the visual-reviewer agent) only on a named trigger: **new surface, redesign, audit, a measured-AA question, state-matrix/DSF coverage, or a glance that returns `escalate`.** No trigger matches → no instrument run. Both rungs get the dispatch brief below, and a re-verify is the same rung again, carrying the prior findings.

### Verdicts — canonical here

Glance emits `pass | needs_work | escalate | blocked`; the instrument emits `pass | pass_partial | needs_work | needs_fixture | blocked`. An unforceable state the glance finds rides its `escalate`/`blocked` reason. The agent bodies carry only their per-verdict *earning criteria*; what a verdict obliges **you** lives here alone:

| Verdict | Your response |
|---|---|
| `pass` | Declare done. |
| `pass_partial` | Declare done only with the verdict's named scope carried **verbatim** into your handoff. A reduced scope silently widened into "verified" is the false pass this workflow exists to kill. |
| `needs_work` | Fix, then delta re-verify — never self-certify. |
| `needs_fixture` | The project lacks a seam — a state nobody, builder or reviewer, can force into a frame. Surface the missing fixture or marker as *work*, citing the probe skill's state contract. |
| `blocked` | An environment problem (no usable origin, failed preflight, missing tooling). Fix the environment and re-dispatch — a blocked run is not a review. |
| `escalate` | **You adjudicate**, against the trigger list above. Authorize the instrument run out loud, naming its cost — an orchestrator decision, neither a silent default nor a mandatory user ping. No trigger matches → act on the evidence the glance already gave you and say so. |

Parallel instrument facets (full mode only, cap 3, all sharing the one origin): worst verdict governs, scope lines concatenate, blind spots union — never split targeted/delta runs.

**Diagnosing a verdict is leaf work.** A finding that needs interrogation — a re-capture under other conditions, deterministic color sampling, native-density crops — goes to a bounded follow-up leaf carrying the frames, the expectation, and the method; you adjudicate its report. Run in-line, that churn multiplies main-loop turns against a full context window — the workflow's dominant observed cost. A check of a command or two stays in-line; there, dispatch ceremony costs more than it saves.

## 5. Close-out

Before the postmortem is committed: `ballast-visual-origin teardown --session-key ${CLAUDE_SESSION_ID}` (`list` first to see what this session owns) — it kills only fingerprint-verified entries this session recorded, then clears the ledger. Stop any co-drive/probe session you opened too. A dead-session sweep reaps what a crashed session left behind: a backstop, never your reason to skip teardown.

## When a defect escapes — the ratchet

A defect that shipped, or one a review missed, earns a **machine-checkable artifact, never another prose bullet** — prose is exactly what already failed to move the defect rate. Take the first shape that expresses it:

1. **Fixture row** — a worst-case row in the project's state manifest: the cell nobody thought to force.
2. **Rung-0 assertion** — the defect is geometry, contrast, or a missing asset.
3. **Matrix cell** — the matrix is cost-budgeted, so adding one means justifying it or evicting one.
4. **Per-fixture checklist line** — taste-class findings only, loaded when that fixture is captured. Quota'd: each must say why no shape above expresses it.

A new prose *rule* is legal only for a genuinely new failure **class**, through the durable-docs gate. Every artifact names its provenance (the postmortem report id), so the prose it subsumes can later be **deleted** — instruction surface has to go net-negative, not just flat.

## The dispatch brief

Every visual dispatch carries this. Fill the angle-bracket fields; change nothing else.

```
Origin (REQUIRED): <url> — already running and main-session-owned. Use it. Start, stop, and signal
  nothing; leave it running at close-out. No usable origin → return `blocked` naming it, and stop.
Out-dir: <abs path> — pass `--out <that dir>` so the frames survive for me.
Target + intent: <routes / surface> — <one line of what it must be>. Small-text color questions are
  not full-frame-adjudicable: answer them from a native-density crop or deterministic sampling, or
  report them indeterminate.
Settle: <ms> (optional — project post-ready animation/crossfade window; pass `--settle <ms>`)
Harness: node ${CLAUDE_PLUGIN_ROOT}/skills/visual-probe/scripts/probe.mjs — run `preflight` first
  (exit 1 → `blocked` with its output verbatim). Manifest-driven capture MUST pass
  `--skip-drive-hooks`; states skipped that way land in manifest.json's `coverageHoles` as
  `drive-hook-skipped` — read manifest.json before any frame, and carry every hole in your verdict
  rather than absorbing it. Do not load the visual-probe skill; this brief is your contract.
You never install anything (no npm/pip/npx/dlx/bunx — the download precedes the answer) and never
  start, background, or kill a process. Missing tooling/service/origin = `blocked` or a named
  coverage gap: report it and finish with what exists.
```

A leaf reporting a lifecycle-guard false positive is reporting, not asking — it is denied every route around the guard on purpose. If its report holds up, the attended main session may `touch <home>/.cache/ballast-lifecycle/valve-<session-key>` and re-dispatch it; a leaf never creates that valve.

## Three rationalizations, all watched

- *"The tests / the probe passed."* Functional green certifies what was **driven**, not what was **seen** — a script can open every overlay and screenshot after they close.
- *"I reviewed the code paths for the styling change."* Static review examines source; the defect is a property of the rendered frame. More static readers is more blind readers.
- *"A full review is overkill for this one-line tweak."* Correct — that is what the glance is for. The ladder scales down to a handful of cells; it never scales to zero.
