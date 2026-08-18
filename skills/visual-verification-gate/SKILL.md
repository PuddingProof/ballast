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

## 2. Setup — one origin, one pin, main session, once

Lazily, at first need, and never again: **one origin per session, started here**. A dispatched leaf is hard-denied process lifecycle at the root — it starts, backgrounds, and kills nothing — so an origin it wasn't handed is a `blocked` verdict, not its problem to solve.

- **Start it.** Static target → the probe skill's bundled isolated server (`serve` mode). A real app → the project's OWN dev-server script, from its existing tooling. Never point at the user's live dev server; probe's serve body owns that hazard and the reach-it recipe per frontend type.
- **Record it** the moment it is up, so close-out and the dead-session sweep can both find it: `ballast-visual-origin record --session-key ${CLAUDE_SESSION_ID} --url <url> --out-dir <dir> --pid <pid>`
- **Pin the dispatch context, once**, so no leaf ever rediscovers it: `ballast-visual-origin pin --session-key ${CLAUDE_SESSION_ID} --url <url> --out-dir <dir> [--states <visual-states.json>] [--matrix <cells>] [--native <WxH@1>] [--suppressions <file>] [--settle <ms>]`. It writes `vp-context.json` into the out-dir and stamps the newest frontend-source mtime. `--native` is the surface's real size and leads every matrix; a project with no state manifest can still pin a suppressions file so declared-intended rung-0 findings don't re-surface each run. **Every brief below is filled from that pin** — leaf discovery is the single largest measured waste in this workflow, and templating deletes it by construction. Re-pin whenever a claim changes; a re-pin overwrites wholesale.
- **Session-scope the out-dir.** A shared temp dir is how one session's teardown wipes a concurrent session's evidence.
- **Clear preflight before the first dispatch** (probe's `preflight`), and fold every tooling/server permission into **at most one** authorization ask, attended, here — an unattended leaf cannot answer an interactive gate and must never try. OS-level dialogs (firewall, per-command prompts) are an accepted residual.

## 3. Build loop — capture, look, fix

At loop boundaries — not per keystroke, not once at the end — force the state you just touched: worst content, both themes, any overlay over it, **composed together** rather than one axis at a time (probe's `shot` and state-contract bodies own the how). Then *look at the capture* and fix objective defects in the same turn: overlap, clipping, contrast, bad wrap. A visual edit you never rendered is an edit you made blind — and your own look is hygiene, not evidence: it runs on the states you expected to work, so it never stands in for the gate below.

## 4. Done gate — the ladder

**Default: the glance.** Dispatch the visual-glance agent — a small worst-case cell set, a fixed four-turn protocol, minutes. That is the check for ordinary visual work: a tweak, a component, a fix, a re-verify. **Escalate to the instrument** (the visual-reviewer agent) only on a named trigger: **new surface, redesign, audit, a measured-AA question, state-matrix/DSF coverage, or a glance that returns `escalate`.** No trigger matches → no instrument run. Both rungs get the dispatch brief below.

**Scope the cell set to the change.** A micro-diff earns 2 cells, an ordinary change the 6-cell default (3 viewports × 2 themes); an axis you don't shoot is a named hole in the brief, never a silent omission.

**Preflight the dispatch** (shadow — it reports, it never blocks): `ballast-visual-origin pin --check --out-dir <dir>` re-stats the pin's claims — origin still verified in the ledger, out-dir present, and evidence newer than the newest frontend edit. A `WOULD-BLOCK` line means a claim went stale: refresh the pin, or carry the gap into the dispatch by name.

**Capture ahead (the warm path).** You MAY fire the brief's exact capture command as ONE background Bash at dispatch time — a main-session lifecycle carve-out, cap **one in flight per session**, available to both rungs. The finished capture prints a `DISPATCH —` wait line; the brief carries that line verbatim, overlapping capture with agent spawn. If the wait times out (exit 3) the leaf runs the full command itself: worst case equals the cold path, minus the overlap.

**Copy the epoch — never compose it.** The epoch is the capture's own start stamp: a finished capture prints a `DISPATCH` line whose `--since` is its manifest's `generatedAt`; paste that value into the brief's `Epoch:` line and wait command. A hand-composed timestamp is the measured failure mode — an epoch even seconds in the future rejects every existing manifest and burns the wait's full timeout (the harness now exits 2 on a far-future `--since`). Cold dispatch (no capture-ahead): the leaf's own capture is its own epoch — no timestamp anywhere. A re-used out-dir always holds a previous cycle's complete manifest; the epoch is what keeps a leaf from reviewing the build the dispatch was meant to replace. An RMA resume re-runs capture-ahead (seconds) and pastes the NEW printed line, for the same reason.

### Verdicts — canonical here

Glance emits `pass | needs_work | needs_fixture | escalate | blocked`; the instrument emits `pass | pass_partial | needs_work | needs_fixture | blocked`. The agent bodies carry only their per-verdict *earning criteria*; what a verdict obliges **you** lives here alone:

| Verdict | Your response |
|---|---|
| `pass` | Declare done — carrying any **declared hole** you wrote into the brief **verbatim** into the done-claim. You chose not to shoot those axes; a pass over them is a claim nobody made. |
| `pass_partial` | Declare done only with the verdict's named scope carried **verbatim** into your handoff. A reduced scope silently widened into "verified" is the false pass this workflow exists to kill. |
| `needs_work` | Fix in-line, then **RMA**: resume the SAME leaf via a scoped delta ticket under a cycle-unique name (`glance-<slug>-<n>`), or batch several fixes into ONE delta. Never a fresh full dispatch per micro-fix, and never self-certify. |
| `needs_fixture` | The project lacks a seam — a state nobody, builder or reviewer, can force into a frame. Surface the missing fixture or marker as *work* (the ratchet's shape 1), citing the probe skill's state contract. **Never answer it with a reviewer dispatch** — the deeper rung cannot force what the app doesn't expose either. |
| `blocked` | An environment problem (no usable origin, failed preflight, missing tooling, a stale pin). Fix the environment — regenerating the pin is cheap — and re-dispatch; a blocked run is not a review. |
| `escalate` | **You adjudicate**, against the trigger list above. Authorize the instrument run out loud, naming its cost. An unforceable state dressed as `escalate` is reclassified to `needs_fixture` and routed there instead. No trigger matches → act on the evidence the glance already gave you and say so. |

Parallel instrument facets (full mode's default shape, cap 3, all sharing the one origin): worst verdict governs, scope lines concatenate, blind spots union — never split targeted/delta runs.

**Diagnosing a verdict is leaf work.** A finding that needs interrogation — a re-capture under other conditions, deterministic sampling, native-density crops — goes to a bounded follow-up leaf carrying the frames, the expectation, and the method; you adjudicate its report. Run in-line, that churn multiplies main-loop turns against a full context window — the workflow's dominant observed cost. A check of a command or two stays in-line; there, dispatch ceremony costs more than it saves.

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

Every visual dispatch carries this, filled from the pin. Change nothing else.

```
Origin (REQUIRED): <pin.origin> — already running and main-session-owned. Use it; never second-guess
  it as "the user's live server". Start, stop, and signal nothing. No usable origin → return
  `blocked` naming it, and stop.
Out-dir: <pin.out_dir> — the command below already writes there; the frames must survive for me.
Epoch: <pasted from the capture's DISPATCH line | SELF-EPOCH (cold: your own capture is the epoch)> —
  never hand-composed. Evidence generated before it belongs to a previous cycle.
Target + intent: <routes / surface> — <one line of what must be true in the render>.
Declared holes (not shot): <axes — why | NONE> — mine, not yours: carry each into your scope line
  verbatim, exactly like a `coverageHoles[]` entry.
Native cell: <pin.native> — the matrix's FIRST cell is the surface's native size; judge it there, and
  never substitute a nearby cell for it.
Run this first, exactly as written:
  node ${CLAUDE_PLUGIN_ROOT}/skills/visual-probe/scripts/probe.mjs <glance|review-capture> \
    --url <pin.origin> --matrix <pin.matrix> [--urls <u1,u2>] [--states <pin.states_manifest>
    --skip-drive-hooks] [--suppressions <pin.suppressions>] [--settle <pin.settle>]
    --out <pin.out_dir>
  (capture-ahead fired instead → paste its printed DISPATCH line:
    `… probe.mjs glance --wait <pin.out_dir> --since <printed> --timeout <ms>`)
Expected budget: <N> invocations · 1 browser launch · ~<M>s. Echo the manifest's `budget` block in
  your verdict as it stands — `invocations` is THIS dispatch's count (a waited capture legitimately
  reports 2); `invocations_total` is the out-dir's history and is not your spend.
Exit codes: 0 = normal, INCLUDING a `--deadline` partial flush (its holes are data, not failure) ·
  1 = blocked, return the output verbatim · 2 = the command above is malformed, report it as blocked
  · 3 = the capture-ahead isn't coming, so run the full command yourself, once.
Read manifest.json before any image: every `coverageHoles[]` entry rides your verdict's scope and
  blocks a clean pass, and a placeholder tile in a sheet is a rendered hole, never a cell you read.
  `cropHoles[]` is different: a failed crop makes THAT question indeterminate, never a coverage gap.
  Manifest-driven capture always carries `--skip-drive-hooks` — project drive hooks are never
  imported into a leaf's process, and states skipped that way appear as `drive-hook-skipped` holes.
You install nothing (no npm/pip/npx/dlx/bunx — the download precedes the answer) and never start,
  background, or kill a process. Missing tooling/service/origin = `blocked` or a named coverage gap:
  report it and finish with what exists. Do not load the visual-probe skill; this brief is your
  contract.
```

A leaf reporting a lifecycle-guard false positive is reporting, not asking — it is denied every route around the guard on purpose. If its report holds up, the attended main session may `touch <home>/.cache/ballast-lifecycle/valve-<session-key>` and re-dispatch it; a leaf never creates that valve.

## Three rationalizations, all watched

- *"The tests / the probe passed."* Functional green certifies what was **driven**, not what was **seen** — a script can open every overlay and screenshot after they close.
- *"I reviewed the code paths for the styling change."* Static review examines source; the defect is a property of the rendered frame. More static readers is more blind readers.
- *"A full review is overkill for this one-line tweak."* Correct — that is what the glance is for. The ladder scales down to a handful of cells; it never scales to zero.
