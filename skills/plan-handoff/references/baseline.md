# RED baseline — the watched failures this skill encodes

Per skill-forge, a discipline skill is forged against observed failure, not a guess. This skill's RED evidence is pre-vetted cross-session postmortem data, derived from a cross-project postmortem evidence sweep (2026-06/07) across the author's repos. Cited as provenance, not authority — the postmortem files are the primary record.

## The failure class

A top-tier model finishes a plan, then implements it in-line in the main session — or the session is manually switched to a cheaper model for the build with verification/review still ahead. Both shapes bloat one long-lived context whose cache re-read bills on every subsequent turn.

## Captured failures (approach B — mid-session switch for execution)

*(Author illustrative data — figures and quotes below are drawn from the author's own sessions, not reproducible or verifiable from a fresh install; the transferable lesson is the failure class in the skill body, not the specific numbers.)*

- A postmortem-tracked session: plan (top-tier model) → `/compact` + `/model sonnet` → execution. ~65M tokens / ~$42 for a 6-file diff; Sonnet's unsupervised open-ended verification built a focus-stealing native-input harness and chased a non-bug. Postmortem verdict: "switching doesn't save tokens; round-trip count does"; addendum prescribes in-line Sonnet subagents or a fresh session and to "avoid the single-session /model-switch shape".
- Another postmortem-tracked session: ~$82 / 103M tokens; 4 manual compactions; 5 stacked inline review passes; 66% of tokens were main-thread cache-read. User: "I don't trust Sonnet for verification and validation."

## Counter-evidence (approach A — in-line dispatch, main stays top-tier)

- A postmortem-tracked session: top-tier plan → Sonnet executor subagent → 8-angle review → zero post-verification corrections.
- Another: 6 disposable Sonnet cluster executors, 74% of tokens in subagent contexts, clean ship — but the orchestrator still crossed its 200k context target, hence the hygiene line in the skill.
- Further sessions across the sweep: tier-matched fanouts, clean, zero user corrections.

## Counter-evidence (approach C — plan artifact → fresh session)

- Sessions from the sweep: plan in .notes → fresh executor session, clean ("an over-specified plan is low-cost for a capable executor"); and a solo-executed design handoff (Sonnet), zero reversals. Source of the shape discriminator: grab-bag multi-verdict → same-session orchestration; one coherent fully-specifiable task → plan-artifact handoff.

## Supporting findings encoded in the skill body

- Effort inherits silently; Agent tool has no per-dispatch effort param (docs-verified 2026-07-06; agent-frontmatter `effort:` and Workflow `opts.effort` are the override points).
- Fan-out-drafted specs are "locally correct but codebase-blind" (one sweep session's batch: 5 of 8 overruled).
- Haiku/Sonnet mechanical sweeps fabricated cross-refs (a sweep session's engine audit) → deterministic scripts for token-swaps.
- Benign switches: a `/model sonnet` switch for the ship tail; tier-up for postmortems.

## Unplanned-tail evidence (n=4)

- Chain `a797b5f9#G1 → 3e3a07ff#G1/C4 → 54509377#P3 → 031e4738#G1` (post-ship wave, stall tail, post-compact corrections): the same in-line-build failure class recurs whenever new implementation work appears after the planned waves end — a post-ship bug wave from live verification, an executor stalled mid-batch, a post-compact correction pass. User: "Perhaps my follow-ups or bug fixes from my own live verification also need to be a sub-agent dispatch?"

## Trigger validation note

State-triggered discipline class: description-based recall is ≈0 by design (skill-forge tune-trigger scope note), so the reliable trigger is the `plan-handoff.sh` PostToolUse(ExitPlanMode) hook pointer plus the CLAUDE.md fanout clause. Validated behaviorally at forge time (executor-dispatch sanity run), not via the description optimizer.
