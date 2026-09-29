---
name: adversarial-audit
description: >-
  Adversarial audit of a codebase — parallel review lenses, refutation-framed checks on every
  finding, fixes landed as checkpointed commits, and a dated report in .claude/adversarial-audit/.
  `--light` (default), `--medium`, or `--hard`; by default it covers what changed since the last
  audit. This is an expensive skill by design.
when_to_use: >-
  The user asks for an audit, a multi-lens or "fresh eyes" review of the whole project or an
  area, a foundation check, or types /adversarial-audit or /audit. Not for one diff or PR
  (diff-review), a docs-only staleness pass, or cross-project postmortem triage (harness-sweep).
argument-hint: "[--light|--medium|--hard] [--report-only] [<path | area | all>] [<steering>]"
allowed-tools: Read, Glob, Grep, Bash, PowerShell, Agent, Skill, Write, Edit
---

# Adversarial audit

**You are the end-to-end orchestrator for a comprehensive project audit.**

Skeptical eyes on the code: lenses find defects, someone told to refute each one checks it, you fix what holds, and a report records every decision. You own scope, lens choice, verdicts, and the report; leaves do the reading and the edits.

## Arguments

- **Level** — `--light` (default), `--medium`, `--hard`. The user's flag is the ceiling; never raise it yourself.
- **Scope** — a path or area narrows it; `all` means the whole tree. None given: the files changed since the `Base:` commit in the newest report under `.claude/adversarial-audit/` (the whole tree if there is none). Anything else is steering — a focus, an excluded lens — and binds.
- `--report-only` — stop after the report; no fixes.

## Levels

| | lens leaves | checking findings | review of the fixes |
|---|---|---|---|
| `--light` | ≤4 | you read each cited line | diff-review `--medium` |
| `--medium` | ≤6 | ≤3 verifier leaves for 🔴/🟠, batched by area; you check the rest | diff-review `--medium` |
| `--hard` | ≤8 | ≤6 verifier leaves covering every finding, batched by area | diff-review `--hard` |

The caps are ceilings, not targets — run the lenses the scope needs. Before the first dispatch, print one line: scope (files, LOC), each lens with its leaf type, the verifier cap. Leaves run as `leaf-light` (recall and checklist lenses) or `leaf` (correctness, security, architecture, verifiers) — never on Fable.

## 1 — Ground

One `leaf-light` reads the prior audit reports (`.claude/adversarial-audit/`, plus older `*audit*.md` under `.notes/`) and returns every rejected finding and by-design ruling quoted verbatim with its source, plus a census of the scope: files, LOC by area, test and build commands. Pin those rulings into every lens and verifier brief — a rejected item proposed again is this audit's defect.

## 2 — Find

Lens menu: correctness · security · architecture (special cases stacked on shared code; a design decision only partly migrated — flag its remaining adopters) · simplification and dead code · efficiency · tests (what is asserted vs merely run) · docs vs code (documented intent is a claim to check, not a defense) · conventions. When they apply: UI and accessibility · output data (audit what the program produces, not only its code) · live runtime (via the visual-probe skill) · dependencies · cross-platform.

Each lens brief carries its scope slice, the pinned rulings, the bar (nothing a linter or typechecker catches, no style nits without a written rule, every finding names a concrete failure or cost), and the schema `file · anchor · summary · failure scenario · severity 🔴🟠🟡⚪`. A lens that returns placeholder output failed — re-run it or cover that angle yourself; it is not a clean bill.

## 3 — Check

Merge duplicates (same defect, same mechanism). Checks are framed to refute: ✅ confirmed (names the trigger, quotes the line) · 🟡ᵛ plausible (says what would confirm it) · ❌ refuted (quotes the line that disproves it). Before a 🔴 goes in the report, re-read its cited source yourself.

## 4 — Fix

Give each survivor one disposition:
- **🚢 fix** — batch by area into self-contained specs and dispatch them to leaf agents; commit each batch by path once its tests pass. A fix that still needs design judgement stays with you or goes to `leaf-hard`.
- **🚫 reject** — with the reason: correct as is, false premise, or cost out of proportion. "Pre-existing" is never the reason. By themselves, regressions and decision forks are not sufficient reasons for rejection; consider whether *defer* or *user call* may be appropriate.
- **📥 defer** — real future work becomes an IDEAS.md or BACKLOG.md stub linking the finding.
- **🎨 user call** — taste and design questions go in the report for the user. They never block the run.

Then close out: run diff-review at the level's depth over the whole fix range and land what holds as one more batch. Last, read the combined diff once against the confirmed findings and mark each ID fixed, rejected, deferred, or missing — a finding dropped in a batch reshuffle only shows up here.

## 5 — Report

Write `.claude/adversarial-audit/YYYY-MM-DD-<scope>-audit.md` per `${CLAUDE_SKILL_DIR}/references/report-template.md` (sandbox blocks `.claude/`? write `./<scope>-audit.md` and say so). Its `Base:` is the commit your last fix landed on — the next audit starts there. Mark items this audit resolved in older reports and IDEAS.md, then commit the report and stubs by path.
