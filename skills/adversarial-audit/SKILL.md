---
name: adversarial-audit
description: >-
  Adversarial audit of a codebase — parallel review lenses, refutation-framed checks on every
  finding, fixes landed as checkpointed commits, and a dated report in .claude/adversarial-audit/.
  `--light` (default), `--medium`, or `--hard`. This is an expensive skill by design.
when_to_use: >-
  The user asks for an audit, a multi-lens or "fresh eyes" review of the whole project or an
  area, a foundation check, or types /adversarial-audit or /audit. Not for one diff or PR
  (diff-review), a docs-only staleness pass, or cross-project postmortem triage (harness-sweep).
argument-hint: "[--light|--medium|--hard] [--fix-all|--report-only] [<path | area | steering>]"
allowed-tools: Read, Glob, Grep, Bash, PowerShell, Agent, Skill, Write, Edit
---

# Adversarial audit

**You are the end-to-end orchestrator for a comprehensive project audit.**

Skeptical eyes on the code: lenses find defects, someone told to refute each one checks it, you fix what holds, and a report records every decision. You own scope, lens choice, verdicts, and the report; leaves do the reading and the edits.

## Arguments

- **Level** — `--light` (default), `--medium`, `--hard`. The user's flag is the ceiling; never raise it yourself.
- **Fix scope** — by default the fix phase ships every 🔴 and 🟠, plus the 🟡 and ⚪ whose fix is local (≤3 files, no design fork); the rest become 📥 stubs. Arguments or steering move the line:
  - Steering instructions ("fix the nits too", "security fixes only").
  - `--fix-all` ships every survivor, except those that require decision forks or heavy future work.
  - `--report-only` ships **none.**
- **Other** — a path or area narrows the scope; nothing given defaults to the whole project. Anything else is steering (a focus, an excluded lens, an operating constraint) and binds.

## Levels

| | lens leaves | checking findings | review of the fixes |
|---|---|---|---|
| `--light` | ≤4 | you read each cited line | diff-review `--medium` |
| `--medium` | ≤6 | ≤3 verifier leaves for 🔴/🟠, batched by area; you check the rest | diff-review `--medium` |
| `--hard` | ≤8 | ≤6 verifier leaves covering every finding, batched by area | diff-review `--hard` |

The caps are ceilings, not targets — run the lenses the scope needs. Before the first dispatch, print one line: scope (files, LOC), each lens with its leaf type, the verifier cap. Leaves run as `leaf-light` (recall and checklist lenses) or `leaf` (correctness, security, architecture, verifiers); a Fable leaf only where you can name the reasoning step that needs it.

## 1 — Ground

Dispatch 1-2 explore agents to digest project structure and documentation, or do it yourself in-line for a very simple repo. Once grounded, **state your initial impressions of baseline project quality.** Note any general feedback and improvement opportunities — educated guesses are fine before step 2. An already mature, well-hardened codebase may warrant tighter scope — if this is the case, then say so and use smaller fanouts.

## 2 — Find

Lens menu: correctness · security · architecture (special cases stacked on shared code; a design decision only partly migrated — flag its remaining adopters) · simplification and dead code · efficiency · tests (what is asserted vs merely run) · docs vs code (documented intent is a claim to check, not a defense) · conventions. When they apply: UI and accessibility · output data (audit what the program produces, not only its code) · live runtime (via the visual-probe skill) · dependencies · cross-platform.

Each lens brief carries its scope slice, the bar (nothing a linter or typechecker catches, no style nits without a written rule, every finding names a concrete failure or cost), and the schema `file · anchor · summary · failure scenario · severity 🔴🟠🟡⚪`. A lens that returns placeholder output failed — re-run it or cover that angle yourself; it is not a clean bill.

## 3 — Check

Merge duplicates (same defect, same mechanism). Checks are framed to refute: ✅ confirmed (names the trigger, quotes the line) · 🟡ᵛ plausible (says what would confirm it) · ❌ refuted (quotes the line that disproves it). Before a 🔴 goes in the report, re-read its cited source yourself.

Then open the report at `.claude/adversarial-audit/YYYY-MM-DD-<scope>-audit.md` (sandbox blocks `.claude/`? write `./<scope>-audit.md` and say so) with the merged table and its checks; step 4 updates dispositions per batch, so an interrupted run resumes from the file.

## 4 — Fix

Give each survivor one disposition:
- **🚢 fix** — batch by area into self-contained specs and dispatch them to leaf agents; each spec carries the finding's failure scenario as the regression test the leaf adds where one can exist, so the test proves the fix. Commit each batch by path once its tests pass. A fix that still needs design judgement stays with you or goes to `leaf-hard`.
- **🚫 reject** — with the reason: correct as is, false premise, or cost out of proportion. "Pre-existing" is never the reason. By themselves, regressions and decision forks are not sufficient reasons for rejection; consider whether *defer* or *user call* may be appropriate.
- **📥 defer** — survivors outside the fix scope become a stub in the project's backlog file (IDEAS.md, BACKLOG.md) linking the finding.
- **🎨 user call** — taste and design questions go in the report for the user. They never block the run.

Then close out: run diff-review at the level's depth over the whole fix range and land what holds as one more batch. Last, read the combined diff once against the confirmed findings and mark each ID fixed, rejected, deferred, or missing — a finding dropped in a batch reshuffle only shows up here.

## 5 — Report

Complete the report per `${CLAUDE_SKILL_DIR}/references/report-template.md`: BLUF last, `Base:` the commit your last fix landed on. Mark items this audit resolved in older reports and the stub file, then commit the report and stubs by path.
