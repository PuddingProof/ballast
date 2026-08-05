# Changelog

Consumer-visible changes to the ballast plugin. Changes land under **[Unreleased]** in the same commit that makes them; a release rotates the section into a dated version heading (versions = `.claude-plugin/plugin.json`). Internal dev churn is not tracked here. **Bullets are terse one-liners — what changed, not why; rationale lives in the commit message** (style anchor: Claude Code's own CHANGELOG).

## [Unreleased]

## [0.9.2] — 2026-08-05

Visual-stack v2: the harness does the deterministic work in single fused invocations; the agents run fixed few-turn protocols over composed evidence.

- Added `probe.mjs glance`: preflight + viewport-clamped capture + rung-0 assertions + contact-sheet compose + budget stamp in ONE process and ONE browser launch
- Added `probe.mjs review-capture`: matrix sweep with console/pageerror/requestfailed listeners built in and sheets grouped per route×theme — retires the hand-authored console pass
- Added `probe.mjs measure`: deterministic contrast/rects/fonts/targets/overflow JSON — retires per-run hand-authored measurement scenarios
- Added `probe.mjs crop`: post-hoc magnified evidence for a named selector
- Added `probe.mjs glance --wait <out-dir>`: stdlib-only poll for a capture fired ahead of a dispatch; exit 3 means run the verb yourself
- Added `--deadline MS`: a hard stop flushes a partial manifest with the unshot cells as named holes; `blocked` now means zero capture only
- Coverage holes render into contact sheets as labeled placeholder tiles, and are never counted as captured cells
- The fused verbs and `crop` stamp `budget` (invocation counts, per-stage and total wall-clock) into the manifest, keyed by `--out`
- `budget` splits `invocations` (this dispatch's calls, scoped by the new `--epoch ISO`) from `invocations_total` (the out-dir's whole ledger)
- `glance --wait` gains `--since <ISO>`: a manifest generated before the dispatch epoch keeps polling instead of resolving stale (fused manifests now stamp `generatedAt`); with no `--since`, a 120s grace stands in
- Fused verbs clear a previous cycle's `mosaic*.png` before composing, and contact sheets are written through a rename like the manifest
- Fused manifests stamp per-cell `belowFoldPx` — page height a viewport-clamped run never saw is named, not silent
- `--states` with extra `--urls` sweeps states against the base `--url` only; the extra targets are captured as plain cells
- A failed `crop` records in `cropHoles[]` instead of `coverageHoles[]`: it makes that one question indeterminate, never a coverage gap
- `review-capture` truncates each console entry to 300 chars, caps the channel at 50 entries, and counts the overflow
- `review-capture` carries a state manifest's `verifies` scope disclaimer into its own manifest
- No-retry doctrine across the harness: a failed capture resolves to a named hole, never a second attempt
- rung-0 contrast now SKIPS any element whose effective background cannot be resolved to a painted solid (canvas/img/svg/gradient ancestor, or a root with no background) instead of assuming white
- rung-0 overlap no longer flags pairs sharing an `<svg>` ancestor — layered vector paint is not a collision
- Fused manifests inline the top 12 rung-0 rows per check kind with selectors truncated to 120 chars; the full list lands in `<out>/rung0.json` and `rung0Overflow` names what was trimmed
- `probe.mjs measure` prints a compact per-check summary plus the full JSON's path to stdout instead of the whole payload
- visual-glance: rewritten to a fixed four-turn protocol reading contact sheets; adds a `needs_fixture` verdict earned only by a manifest `cannotForce` hit
- visual-reviewer: rewritten around the fused verbs and instruments — mode budgets, facet-split as full mode's default shape, no Write grant
- visual-verification-gate: session dispatch pin, shadow dispatcher preflight, capture-ahead, and RMA-scoped re-verify in the canonical verdict table
- Added `ballast-visual-origin pin`: freezes origin/out-dir/state-manifest/matrix/native-cell/suppressions/settle plus a newest-frontend-edit stamp into `vp-context.json`; `pin --check` re-stats it and logs WOULD-BLOCK without ever blocking
- `pin --check` verifies the origin inside the pin's OWN session ledger — another session serving the same URL no longer validates a dead pin
- Visual dispatch briefs carry a dispatch epoch, declared holes, the native cell and a suppressions slot; both rungs carry declared holes into their verdict scope
- statusline: project tag now renders near-white (was dim gray) — promoted above the gray model/ctx segments
- adversarial-audit: reports now land in `.claude/adversarial-audit/` (was `.notes`); Phase 0 still reads legacy `.notes` audit reports
- plan-handoff: the fresh-session execution row points at the project's own plan location instead of hardcoding `.notes`
- git-commit-guard: the review nudge now fires once per commit cycle instead of on every `git diff`/`git log`
- subagent-fanout and plan-authoring: injections now fire once per 2h per session
- doc-write-guard: soft nudges rate-limited to one per 3 minutes per session (SKILL.md reminders exempt)
- cooldown-suppressed hook fires are recorded to per-hook `suppressed.log` audit trails
- commit-review-gate (shadow): session-output-only commits (`.claude`, `.notes`) now ALLOW, and same-session retries are tagged `retry=1` with no repeat ghost
- inline-churn-nudge: now nudges live at 20+ consecutive main-session in-line edits, once per session

## [0.9.1] — 2026-07-31

- Hooks prose pass: injected texts trimmed across the board (subagent-fanout −17%, git-commit-guard review nudge, ballast-principles, plan-authoring, doc-write-guard/askuserquestion stderr) — same constraints, fewer standing tokens
- harness-sweep v2: full redesign — deterministic `ballast-sweep` corpus engine (parses all report schema generations v0–v5), forked one-context judgment pass pinned to fable/medium, digest quality over runtime; retires the harness-sweep-extractor + harness-sweep-scout agents
- Hook tests moved from hooks/ into hooks/tests/
- session-postmortem v5: full redesign — single-pass digest script, forked one-context report, ~3× faster; retires postmortem-extractor + postmortem-priors-scout agents and the --fix flag
- session-postmortem: `--id` accepts a unique prefix (a report's sid8); an unresolvable explicit id fails loudly instead of silently falling back to the newest cwd transcript
- session-postmortem: `suggested_slug` descends past dot-dirs (`.claude/`) and pass-through layout dirs (`src/`) to a content-bearing path segment
- freehand-mode: dropped the pending statusline chip and its settle instruction; the keyword-arm stub and systemMessage are now static (the confirmed chip, raised by the freehand skill on a genuine grant, is unchanged)

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
