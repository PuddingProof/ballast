---
name: plan-handoff
description: >-
  Plan→execution handoff protocol — use at the moment a plan or spec is approved and
  implementation is about to begin, BEFORE the first code edit — AND at every unplanned tail
  where new implementation work appears after the planned waves: a post-ship bug wave from
  live verification, an executor that stalled mid-batch, a post-compact correction pass.
when_to_use: >-
  Triggers — ExitPlanMode approval (the plan-handoff hook points here), "implement the plan",
  "go ahead / build it / proceed with the plan", handing a spec or .notes plan to an executor,
  a user bug report on just-shipped work, resuming fixes after /compact, deciding between
  editing in-line vs dispatching executor subagents, picking model/effort for execution
  leaves. Not for planning itself or review fanouts (/code-review); a genuinely tiny diff has
  its own table row — check it rather than assuming.
---

# Plan → execution handoff

**Core rule: once a plan exists, the top-tier main thread orchestrates, verifies, and reviews — it does not type the implementation.** Context added to a long-lived thread is a recurring charge (re-read on every subsequent turn); context in a disposable executor is one-time. The bill is round-trip count × main-context size — which is why compacting doesn't rescue an in-line build phase.

## Pick the execution shape

| Work shape | Execution shape |
|---|---|
| Multi-part / grab-bag plan, verdicts needed between steps | Same-session `plan-executor`-ladder subagents — parallel when steps are independent, sequential when coupled |
| One coherent, fully-specifiable task | Write the plan to `[project]/.notes/<date>-<slug>-plan.md` → fresh cheap session executes it; costs one handoff round-trip, buys a reusable spec and zero orchestrator bloat. Writing that file is YOUR step: native plan mode saves only to `~/.claude/plans/` — promote the approved plan into `.notes/` yourself for the durable, git-tracked record |
| Long/complex build with heavy tool churn, or in-flight judgement calls expected | One background executor agent owning the whole build — `plan-executor` (ladder-tiered) when the plan fully specifies it, a top-tier general agent when in-flight judgement is heavy; the orchestrator plays advisor, fire-and-notify: dispatch and stop — the harness notifies on completion; an unattended dispatch cannot answer an interactive guard (a permission prompt, an AskUserQuestion) — surface those gates before dispatching, or the leaf stalls silently. SendMessage only on signal (a deviation report, a user steer to relay). Exception: completion notifications are lost when a leaf crashes or stalls (server errors, hangs) — for any executor expected to run 15+ minutes, self-schedule a check-in watchdog wakeup so a dead leaf is caught in-session, not hours later by the user against a cold cache. The write/edit churn stays in the executor's disposable context either way |
| **Unplanned tail** — implementation work appearing after the planned waves: a post-ship bug wave from live verification, an executor stalled mid-batch, a post-compact correction pass | The same split as planned work, applied to the tail: root-causing/characterization stays top-tier (diagnosis is judgement); once the fix is specified, the mechanical fix + tests dispatch as an executor batch like any other wave — for a stall, audit what actually landed (don't trust the stalled leaf's narration) AND enumerate the leaf's live background children — an audited-clean leaf can still have a background task mid-write, and a fresh executor dispatched over it races that child (commit-by-path keeps the collision recoverable; it is not a substitute for the check) — then hand a FRESH executor the remaining spec. Docs likewise: the durable-docs gate call and what-the-doc-must-say are yours; file-holding/splicing/formatting is an executor leaf (exception: session-distillation docs — postmortems, design records — whose content IS your context) |
| Tiny diff, or taste iteration that can't be spec'd (visual tuning, wording) | In-line is correct — don't add dispatch ceremony |

The division of labor: cheaper models take execution, editing, and large-file reading; design and judgement — evaluation, verification, adjudication — stay top-tier in the main session. Review/audit fanouts (a `ballast:code-review` run, ultracode, `/integration-gate`) still launch from the orchestrator and tier their leaves per the `subagent-fanout` rules — that calibration runs orthogonal to this protocol, not superseded by it. The orchestrator synthesizes the findings and renders the verdicts; *applying* the accepted fixes is another executor dispatch (tiny-diff exception as above).

## Dispatching an executor

- Prompt = the plan steps verbatim (or the plan file path + step range), the deterministic checks to run, the report-back shape, and a 2–3-sentence big-picture why (the goal, constraints, and design rationale the steps serve) — an executor's comment quality is bounded by the rationale handed over; starved of the why, it writes overfit narration. Scale the why up for a `plan-executor-hard` dispatch — complex batches are where it does the most work. The `plan-executor` agent definition (model+effort pinned per variant) carries the executor discipline: exact execution, deterministic checks only, report deviations instead of improvising.
- Review machine-drafted specs against the live architecture before dispatching them — fan-out-drafted fix specs come back locally correct but codebase-blind. The same discipline covers every factual premise the dispatch prompt asserts (current behavior, "X is already in place", expected state or acceptance bands): verify it against the live system or phrase it conditionally — never assert it from narrative or memory. A stale premise either licenses wrong work or blocks right work; a leaf reporting a premise contradiction has done its job — re-verify, don't override.
- Budget the wave, not just its steps: any long-running or exploratory leaf carries an explicit wall-clock or scope cap in its prompt — a cost band inferred from that leaf type's past runs understates as soon as its input matrix is unbounded, and one uncapped lens is where a wave's spend actually goes. Then take stock when the first wave LANDS (spend so far against the task's worth, remaining scope re-confirmed, said out loud); discovering the budget at the end is the same checkpoint in its expensive form — a forced scope cut, or a session terminated over cost.
- Pure token-swap / rename sweeps: a deterministic script (sed, codemod) beats agents — agents fabricate under mechanical monotony.
- An executor that will author or edit a permanent-tier durable doc (SKILL.md, CLAUDE.md, a hook's injected prompt) hits the doc-write-guard on its first write: run the durable-docs / skill-forge gate and write its attestation *before* dispatching, so the leaf isn't spending its batch re-running a gate whose judgment you already own.
- Hand-authoring a dispatch/Workflow script? Write it to a file and syntax-check it (`node --check`, or the language's equivalent) before launch — an inline script's parse error surfaces as a cryptic failure that reads like a permission/safety block and burns a diagnosis round-trip.

## What never delegates

- Open-ended verification — live-driving the app, exploratory visual checks, co-drive — stays with the orchestrator + user. An executor told to "verify it works" builds ad-hoc harnesses and chases non-bugs; verification is judgement. One structured exception: visual work runs through the `visual-verification-gate` skill end-to-end — loaded at implementation START, not at done-time — which picks the ladder rung itself (glance by default); not an executor improvising verification.
- Review adjudication: executors and finders propose, the top-tier thread (or the user) disposes. A finder's plausible "defensive" fix can silently undo the change under review — re-measure before accepting.

## Companion gates

- Plan shoving edits into an awkward structural fit? That's a plan revision, not an executor improvisation — raise `/refactor-fit` (propose-then-build) before dispatching.
- Multi-executor build about to be called done? That's exactly `/integration-gate`'s trigger — per-leaf checks passing says nothing about the seams.
- Complex implementation → run either gate as its own Opus dispatch (the evaluation/read churn stays out of the main window); simple diff → in-line by the orchestrator is fine.
- Independent gates run concurrently, not as a serial pipeline: once the diff is frozen, a visual review dispatch (via `visual-verification-gate`), a `/code-review` fanout, and an integration-gate dispatch read the same tree and don't feed each other — launch them in one wave and adjudicate the merged findings. Chain only where one gate's input IS another's output (a delta re-verify after fixes).

## Tier & effort per leaf

Effort inherits the session's level silently — set it explicitly per leaf: executors opus medium by default, sonnet medium for light batches, opus high for the hardest fully-specified ones; judge/review leaves opus high/xhigh. The Agent tool has no per-dispatch effort param, so for executor leaves the **agentType is the model+effort selector** (mirroring /code-review's ladder): `plan-executor` = opus medium, the default; `plan-executor-light` = sonnet medium, light mechanical batches; `plan-executor-hard` = opus high, the hardest fully-specified batches (a difficulty pick, not a volume one — long routine laundry lists stay on the default). Workflow leaves take `opts.effort` directly. Full tier calibration: the `subagent-fanout` hook injection.

## Rationalizations

Each row is a watched, costed failure, not a hypothetical (`${CLAUDE_SKILL_DIR}/references/baseline.md`):

| Excuse | Rebuttal |
|---|---|
| "Faster to just edit it myself" | Every in-line edit turn re-reads the whole main context on all later turns; an executor's context is disposable and its return is a compact report |
| "The executor can verify while it's in there" | The watched failure class: unsafe harnesses, non-bug chases, silently-reverted fixes |
| "Compact first, then continue in-line" | Treats the symptom; the next build rounds rebuild the bloat |
| "I'm already mid-tail / in the flow — dispatch ceremony costs more than just finishing it" | The watched n=4 leak: a tail finished in-line re-reads the whole main context on every later turn, exactly like a build phase. The tail is just another wave — root-cause top-tier, dispatch the mechanical remainder |

Main-thread hygiene while orchestrating: carry the plan, dispatch summaries, and verdicts — never file bodies, diffs, or screenshots; hold the orchestrator's context under ~200k tokens.
