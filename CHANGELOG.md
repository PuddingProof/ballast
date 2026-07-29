# Changelog

Consumer-visible changes to the ballast plugin. Changes land under **[Unreleased]** in the same commit that makes them; a release rotates the section into a dated version heading (versions = `.claude-plugin/plugin.json`). Internal dev churn is not tracked here. **Bullets are terse one-liners — what changed, not why; rationale lives in the commit message** (style anchor: Claude Code's own CHANGELOG).

## [Unreleased]

## [0.9.0] — 2026-07-29

Visual-stack redesign: a two-rung review ladder with a cheap default, root-enforced process-lifecycle hygiene, orchestrator-owned origins, and one workflow skill as source of truth.

- Added `visual-glance` agent: cheap code-blind default rung, hard-capped (≤10 image reads / ≤20 tool calls / ≤10 min wall-clock), origin required; `visual-reviewer` becomes the opt-in escalation rung
- Rebuilt `visual-verification-gate` as the workflow owner: loads at the first frontend touch (not at "done"), five stages, canonical verdict table, copy-paste leaf dispatch brief
- Added `process-lifecycle-guard` hook: sub-agents are hard-denied process detachment, standing-service launches, and all signalling, with a main-session release valve
- Added visual-origin ledger + `origin-sweep` SessionStart hook: sessions record the origins they start; the sweep reaps only fingerprint-verified orphans of verifiably dead sessions
- Added `visual-arm` PostToolUse hook: the first frontend-file touch injects a one-line load prompt for the workflow skill (main loop only, once per session)
- Added rung-0 deterministic in-page geometry assertions (shadow-logged), with a manifest `suppressions` channel
- Added `probe.mjs compose`: contact-sheet compositor — the glance's overflow router
- Added `--settle MS` capture flag: post-ready dwell for animations/crossfades that finish after load/readySignal
- Added `--skip-drive-hooks` (mandatory in leaf briefs): project drive hooks are never imported; skipped states report in the always-present `coverageHoles`
- Restructured visual-probe SKILL.md into per-mode paths (`shot | drive | states | serve | co-drive | doctor`); deleted every self-serve-origin instruction
- visual-reviewer: Origin and out-dir are now required inputs; dropped "Use PROACTIVELY"; matrix doctrine deduped to `state-contract.md`
- visual-glance: small-text ink color is never adjudicated from a full-frame read — crop or report indeterminate (stated at the dispatch-brief level too)
- visual-verification-gate: diagnosing a verdict (re-capture, sampling, crops) is dispatched leaf work, not in-line churn
- Fixed package-install-guard: quoted shell-wrapper bodies (`bash -c "npm install …"`) no longer evade the gate
- Fixed process-lifecycle-guard: grouping punctuation (`(kill -9 …)`, `{ pkill … }`) no longer hides the verb
- Defect-to-invariant ratchet (shadow-first): an escaped visual defect earns a machine-checkable artifact, never another prose bullet; harness-sweep logs `WOULD-REJECT (ratchet shadow)` on prose-only visual recs
- skill-forge: live iteration loop for pipeline skills (`references/live-iteration.md`)
- Conciseness pass across the redesigned stack (bodies −3%, hook-injected texts −5–29%); pointer sync in plan-handoff, subagent-fanout, README
- Fixed visual-glance/visual-reviewer frontmatter: an unquoted `: ` broke the YAML parse, so both agents loaded with empty metadata (model/effort pins silently dropped)

## [0.8.4] — 2026-07-28

- Withdrew code-comment guidance pending a conventions rework (the comment-*content* rule is unchanged)
- Restructured the plan-executor ladder around Opus 5: default = Opus medium; `-light` (Sonnet medium) replaces `-medium`; `-hard` (Opus high) replaces `-opus`
- Hook dispatcher: the resolved Python interpreter is cached instead of re-probed per fire — fixes the observed 5 s hook timeouts
- git-commit-guard: fast-path prefilter — non-git commands no longer start a Python interpreter
- Fixed the fire ledger's empty `sid=` field (wrong env-var name); the same fix restores SessionEnd mode-chip cleanup's fallback
- package-install-guard: installs/remote-execs from a sub-agent are hard-denied instead of prompting (main-session behaviour unchanged)
- visual-reviewer: effort high → medium; hard no-install boundary; dev-server startup steered to project scripts; pixel-metrics require a known-positive control frame; two new flex-sizing detection cues
- subagent-fanout injection: new NO INSTALLS IN LEAVES and SPEND CHECKPOINT bullets
- plan-handoff: wave budgeting — wall-clock/scope caps for long-running leaves plus a first-wave checkpoint
- durable-docs: gate 6 also checks the target dir's own conventions file
- skill-forge: new AMBIENT-ABLATION ship check
- session-postmortem: subagent roster rows carry the orchestrator's own dispatch labels + a nested-dispatch marker
- session-postmortem: owner-facing work the owner hasn't adjudicated gets a pending-owner-verdict annotation instead of reading clean

## [0.8.3] — 2026-07-24

- Public distribution mirror debuts: releases now publish as snapshot commits to the public `PuddingProof/ballast` repo (fresh history, one commit + `vX.Y.Z` tag per release).
- MIT license added.
- README: anonymous-friendly install instructions (claude.ai marketplace add + HTTPS CLI form).
- Commit-review reminder hook: wording now degrades gracefully on installs without the bundled review skill.

## [0.8.2] — 2026-07-24

- Baseline. History before the public split predates this changelog (private tags `ballast--v0.2.2` … `ballast--v0.8.2`).
