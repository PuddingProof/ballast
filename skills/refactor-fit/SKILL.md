---
name: refactor-fit
description: >-
  Opt-in escalation of the conservative default: weigh structural refactoring deliberately as
  part of adding or modifying a feature in existing code (propose-then-build), so the change
  lands clean instead of bolted on.
when_to_use: >-
  Use when the user invokes /refactor-fit, or explicitly asks for refactoring to be considered
  or put in scope as part of a feature or modification — when they want structural fit weighed
  deliberately rather than the conservative default. Opt-in only; do not self-invoke on
  ordinary implementation requests.
---

# refactor-fit

## Overview
Opt-in escalation of the always-on "be conscious of technical debt" rule in CLAUDE.md. For a requested feature or change, weigh structural refactoring **deliberately** so the feature lands clean instead of bolted on — then build to a scope the user greenlights. Fixes under-refactoring (bolt-on / hardcode / duplication) without re-opening unbounded scope creep.

## When to use
- Invoked as `/refactor-fit`, or when the user asks for refactoring to be in scope / considered for a feature or modification.
- NOT for ordinary requests — the conservative baseline in CLAUDE.md already governs those.
- NOT a post-hoc diff cleanup — that's `/diff-review` and `/simplify`, which react to code already written. This acts on the request + surrounding architecture, before/while you build.

## The flow — produce exactly this
1. **Assess before editing.** Read the architecture the feature touches. Produce a SHORT proposal in two buckets:
   - **Required-for-fit** — the structural change(s) without which the feature would bolt on, hardcode, or duplicate. Recommend doing these.
   - **Optional-adjacent** — debt the feature touches or reveals but doesn't strictly need. List for per-item opt-in.
   - A prior design decision the read shows *partially* superseded (some adopters migrated, some not) is a finding, not background: put its remaining adopters in a bucket — Required-for-fit if the feature touches them, else Optional-adjacent — so the half-migrated state can't pass as settled.
   - Each item is one line: *what · why · blast radius* (small / multi-file / behavior-changing). Lead with your recommendation (BLUF).
2. **Get the greenlight.** The user picks scope in one line. A bare "go" — or any absent/ambiguous pick — means **Required-for-fit only**; cheapness ("costs nothing", "trivial at this scale") is never a greenlight for an Optional item, which ships only on an explicit per-item yes. Don't start editing before this. If no user is available to greenlight (single-shot / automated / autopilot run), still emit the two-bucket proposal as visible output, default to Required-for-fit only, and state in your summary that you defaulted (e.g. "refactor-fit: required-only, no greenlight available — Optional items deferred"). Promoting any Optional item without a user needs an explicit reason in that line.
3. **Build to the greenlit scope** — no more. For any greenlit item that's multi-file or behavior-changing, write a brief plan (or use plan mode) first, and honor any project's plan-mode / flag-first guard.
4. **Close.** Run the project's quality pass on the diff (e.g. `/diff-review`, adjudicate and apply its findings, then `/simplify`) and verify.

## Bounds
Inherits the global bounded tech-debt rule; never overrides project guards. The **Optional-adjacent** bucket is the only place scope widens — and only on a per-item yes. If the assessment reveals a genuinely large architectural rework, say so and propose it as its own task rather than folding it in.
