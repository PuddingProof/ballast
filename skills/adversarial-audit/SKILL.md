---
name: adversarial-audit
description: >-
  Orchestrate a comprehensive multi-agent adversarial audit of an entire codebase — parallel
  review lenses (broad + deep), per-finding adversarial verification, triaged fixes with
  checkpointed commits, and a dated .notes report with disposition tracking.
when_to_use: >-
  Use when the user asks for a comprehensive / periodic / ground-up codebase audit, an
  adversarial or multi-lens review of the whole project, "fresh senior eyes on the code", a
  foundation-check on a young project, or types /adversarial-audit or /audit — even when they
  don't say "audit" explicitly (e.g. "time has come for a full review of everything, frontend
  and backend"). NOT for reviewing one diff/PR (native /code-review or a project pr-review
  skill), the seams of a single multi-part change (integration-gate), a docs-only staleness
  pass (durable-docs audit), or cross-project postmortem triage (harness-sweep).
argument-hint: "[quick | standard | deep] [--report-only] [focus:<area or lens> ...]"
allowed-tools: Read, Glob, Grep, Bash, PowerShell, Task, Skill, Write, Edit, AskUserQuestion
---

# Adversarial codebase audit

Mimic a panel of senior engineers putting fresh, skeptical eyes on the whole project: independent finder lenses fan out in parallel, every finding faces an adversarial verifier told to refute it, survivors get adjudicated into explicit dispositions, fixes land as checkpointed commits, and a second differently-framed review lane checks the audit's own work. You are the orchestrator — lens selection, adjudication, synthesis, and final review are yours and stay top-tier; only the leaf work tiers down. Your context is also the audit's scarcest resource: push bulk file reads, command output, and code edits down to leaves and consume their compact returns — read raw source yourself only where a phase says the read is load-bearing (the 🔴 re-read, adjudicating a disputed finding).

**Right-size ruthlessly.** Depth scales to the codebase and the ask, not to enthusiasm — stacked redundant passes over the same material burn tokens without new findings; prefer fresh short-context leaves over an Nth in-context re-read. If the marginal lens is returning only duplicates, stop adding lenses.

## Depth & flags

| Arg | Shape |
|---|---|
| `quick` | 4–6 lenses · verify only 🔴/🟠 candidates · no counter-review lane · report may be terminal-only |
| `standard` (default) | 8–10 lenses · verify every finding · counter-review over the cumulative diff · `.notes` report |
| `deep` | 10–13 lenses incl. conditional ones · verify every finding · counter-review + convergence re-audit of fixed areas · `.notes` report |

`--report-only` stops after the Phase 5 report with no fixes applied; otherwise fixing is the default. `focus:` pins extra weight (a dedicated lens + verifier attention) on the named areas or lenses.

## Phase 0 — Ground

Do this before the Phase 1 lens fanout; skipping it is how audits re-propose vetoed ideas and mis-scale. Steps 1–3 are cheap-leaf reads: dispatch them to a Haiku/Sonnet grounding leaf (usually one; split only if the material is large) and consume its compact grounding pack instead of reading the sources yourself. Steps 4–5 are your judgement.

1. **Standing decisions** — the leaf reads prior audit reports (`.notes/*audit*.md` and `.notes/archive/*audit*.md` — a closed report moves to the archive, and its vetoes still bind), `IDEAS.md`, and recent postmortems, and returns every veto and by-design ruling **quoted verbatim with its source** — a paraphrase loses the exact ruling you must pin. Pin them into every finder and verifier prompt: a declined item re-proposed is a defect of *this* audit.
2. **Census** — deterministic commands (file counts, LOC by area, entry points, test surface), returned as the numbers plus the commands that produced them, so lens scoping and findings carry real magnitudes. The leaf also notes the stack, test/build commands, and any project-local audit or review skills — compose with those rather than duplicating them.
3. **Commit context** — a digest of recent commit messages, handed to every reviewer; a reviewer without them misclassifies intentional guards as defects.
4. **Pre-register** — yours, never a leaf's: jot your own predicted top findings before any results return, then diff against the fanout output to catch anchoring and recall gaps.
5. **Pick lenses & tiers** from the menu below, scaled to depth and `focus:`.

## Phase 1 — Find (lens fanout)

Tier each lens by its hardest reasoning step: top-tier models for security / architecture / correctness-judgement lenses, Sonnet for broad-recall and checklist lenses, Haiku only for tightly-spec'd mechanical scans. Pin model and effort explicitly on every leaf.

**Core lenses** (most audits): correctness/logic · security (OWASP classes, secrets, trust boundaries) · architecture & altitude (special cases layered on shared infrastructure = fix not deep enough; a design decision found *partially* superseded — some adopters migrated, some not — flags its remaining adopters for re-evaluation) · simplification / dead code / duplication · efficiency · tests (coverage honesty — exercised vs merely compiled) · docs-skeptic (challenge CLAUDE.md / README / comments / memories against the code; documented intent is a claim to verify, not a defense) · conventions & consistency.

**Conditional lenses**: a11y + UX (UI projects) · **artifact inspection** — audit the program's *output data*, not just its code: diff what it counts, not just what it fixed · **live runtime** — drive the real product (the visual-reviewer agent for frontend UI/UX, or visual-probe directly); code lenses systematically miss what only shows in a live render · dependency/CVE · parsing & data integrity · cross-platform.

Every finder prompt carries: its scope + census slice, the standing decisions, the commit context, the false-positive bar (don't flag what a linter/typechecker would catch — run those tools once instead; no style-only nits absent a written rule; every finding needs a concrete failure scenario or cost), and a fixed output schema — `file · anchor · summary · failure scenario/cost · severity 🔴🟠🟡⚪`.

A lens returning placeholder or non-substantive output is a FAILED lens, not coverage — re-dispatch it or take that angle yourself. A quantitative claim must cite the exact command and output backing it.

## Phase 2 — Verify (adversarial)

Consolidate across lenses first (same defect + same mechanism → one canonical finding; you adjudicate conflicts). Then one independent verifier per finding (or per file/anchor cluster), tier-matched to the finding's difficulty, prompted to REFUTE it. Verdicts: ✅ CONFIRMED (names the triggering input/state, quotes the line) · 🟡ᵛ PLAUSIBLE (mechanism real, trigger uncertain — states what would confirm it) · ❌ REFUTED (quotes the line that proves it wrong). Expect a 10–25% kill rate; 0% means the verifiers weren't adversarial.

Before publishing any 🔴 finding, personally re-read the cited source — finders misread files, and a critical claim ships under your signature.

## Phase 3 — Triage & fix

Adjudicate every survivor into exactly one disposition — this judgement is yours, never a leaf's:

- **🚢 Fix** — you own the judgement (fix approach, design forks, batch boundaries); executor leaves own the file edits, keeping the reads and diffs out of your context. Batch survivors by area into self-contained specs (what/where/how + test gate) and dispatch each batch to the plan-executor ladder (light/default/hard by difficulty) — inline dispatch prompt or a written plan.md, whichever carries the spec cleanly. An implementation that still needs judgement mid-edit goes to a general top-tier leaf instead (executors execute, they never redesign), not back to you. Land each batch as checkpointed, path-scoped, test-gated commits before the next. Balance the dispatch cost — each leaf rebuilds context from its handoff alone, so batch by shared files/area rather than per-finding, and a trivial edit in a file you already hold is cheaper done yourself than dispatched. Mirrored obligations (twin regexes, paired constants, mirrored call-sites) must each be verified *at their own site* — a pass on the easy twin proves nothing about the other.
- **🚫 Reject** — always with explicit reasoning in the report (current code is correct, false premise, disproportionate complexity for the gain). "Pre-existing" and "non-exploitable" are deprioritizers, never standing skip reasons.
- **📥 Defer** — genuine future work becomes an IDEAS.md stub with provenance (report link + finding ID); audit noise stays in the report only.
- **🎨 Ask** — taste and design-judgement calls (visual, UX, API shape) go to the user via AskUserQuestion, with rendered previews when visual; never auto-resolve subjective calls.

## Phase 4 — Counter-review

The audit's own fixes are unreviewed code. At standard+ depth a second, differently-framed lane over the cumulative diff is mandatory — it reliably catches half-done fixes and self-introduced regressions, including comments that falsely claim safety. Run a `ballast:code-review high [range]` pass (the model-invocable fork of native /code-review, which is user-invoke-only) if this install ships it, else a fresh finder set framed differently from Phase 1, then `integration-gate` across the whole change set, then live verification when the diff has a runtime surface. At `deep`, finish with a narrow convergence re-audit of the fixed areas — "N new findings, none meaningful" is the exit condition.

A green mechanical sweep can lie (a tool that silently no-ops still prints nothing) — prefer deterministic scripts for residual checks and spot-check any zero-result before trusting it.

## Phase 5 — Report & close the loop

Write the dated report at `.notes/YYYY-MM-DD-<scope>-audit.md` per `${CLAUDE_SKILL_DIR}/references/report-template.md` (legend, master checklist, findings by severity with dispositions and reasoning, methodology, commit table). Maintain it as the live tracker throughout the audit; as the very last step run a conciseness pass and put a BLUF at the top for the user's morning read.

Close the loop so the audit doesn't rot: flip items this audit resolved in prior audit docs and IDEAS.md with `**Resolved:**` notes, record vetoes where future runs will re-read them, and route any durable-doc change the audit motivated (a CLAUDE.md rule, a memory) through `durable-docs`. Commit the report and stubs by path at the end.
