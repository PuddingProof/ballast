# Changelog

Consumer-visible changes to the ballast plugin. Changes land under **[Unreleased]** in the same commit that makes them; a release rotates the section into a dated version heading (versions = `.claude-plugin/plugin.json`). Internal dev churn is not tracked here.

## [Unreleased]

## [0.8.4] — 2026-07-28

- Code-comment guidance withdrawn pending a conventions rework: the principles block no longer tells agents to comment generously, plan-executor no longer pins comment density to the surrounding file, and durable-docs no longer routes guard provenance into code comments. Deliberately leaving the question unstated — the executor rule on comment *content* (carry the given why; no fabricated rationale, diff restatement, or step-by-step narration) is unchanged.

- plan-executor ladder restructured around Opus 5: the default `plan-executor` is now Opus at medium effort (was Sonnet high); `plan-executor-light` (Sonnet medium) replaces `-medium`; `plan-executor-hard` (Opus high) replaces `-opus`.
- Hook dispatcher: the resolved Python interpreter is now cached in the user home instead of re-probed on every fire. The probe measured ~1.5 s of a 2.4 s hook fire under load and was re-derived identically four times per Bash tool call — the amplifier behind observed 5 s hook timeouts. The probe's own bound also drops below the hook budget it runs inside, so a hanging candidate can no longer consume the whole budget before the hook is dispatched.
- git-commit-guard: fast-path prefilter — a Bash/PowerShell call that never mentions git no longer starts a Python interpreter just to find that out.
- Fire ledger: the `sid=` field populates again. It logged empty in every line for 15 days — the harness sets `CLAUDE_CODE_SESSION_ID` in hook processes, not `CLAUDE_SESSION_ID`. The same wrong name silently disabled SessionEnd mode-chip cleanup's fallback path, which is fixed with it.

- package-install-guard: installs and remote-execs from a **sub-agent** are now hard-denied instead of prompting — the install contract is the main session's alone, and the deny reason tells the leaf to report the missing tool as a coverage gap rather than retry. Main-session behaviour and the local dev-tool allowlist are unchanged; unknown caller ⇒ ask, as before.
- visual-reviewer: effort high → medium; hard no-install boundary (report the gap, never fetch tooling — even `--version` probes); dev-server startup steered to the project's existing scripts/binaries; bespoke pixel-metrics require a known-positive control frame before their verdict counts; caller wall-clock caps are hard budgets. Checklist B gains two flex-sizing detection cues: a replaced child's synthesized baseline as a cause of alignment drift, and row over-subscription from a `min-width` floor plus `justify-content` going inert once any sibling grows.
- subagent-fanout injection: new NO INSTALLS IN LEAVES and SPEND CHECKPOINT bullets.
- plan-handoff: dispatch bullet on budgeting a wave — an explicit wall-clock/scope cap for any long-running or exploratory leaf, plus a take-stock checkpoint when the first wave lands.
- durable-docs: gate 6 now checks the target dir's own conventions file, not just the global gate.
- skill-forge: new AMBIENT-ABLATION ship check (cut skill lines the ambient stack already injects).
- session-postmortem: the subagent roster now carries each dispatch's own orchestrator-authored label, plus a nested-dispatch marker, so same-agentType rows are finally distinguishable.
- session-postmortem: a session shipping owner-facing work (visual/aesthetic, wording, taste calls) that the owner hasn't adjudicated now carries a pending-owner-verdict annotation on the gauge instead of reading clean.

## [0.8.3] — 2026-07-24

- Public distribution mirror debuts: releases now publish as snapshot commits to the public `PuddingProof/ballast` repo (fresh history, one commit + `vX.Y.Z` tag per release).
- MIT license added.
- README: anonymous-friendly install instructions (claude.ai marketplace add + HTTPS CLI form).
- Commit-review reminder hook: wording now degrades gracefully on installs without the bundled review skill.

## [0.8.2] — 2026-07-24

- Baseline. History before the public split predates this changelog (private tags `ballast--v0.2.2` … `ballast--v0.8.2`).
