---
name: freehand
description: >-
  Delegated-autonomy mode (freehand/autopilot): holds the standing contract and toggles the
  mode on or off for the session.
when_to_use: >-
  Use when the user turns the mode on or off: "/freehand on|off", "/ballast:freehand on|off",
  "turn autopilot on", "freehand mode is on", "autopilot off". A plain declarative grant or
  revoke counts even with no hook injection (a message sent mid-turn fires no hook). Also when
  the freehand-mode hook's stub routes a genuine grant here. NOT for prose that merely
  discusses the mode.
argument-hint: "[on|off]"
allowed-tools: Bash
---

# Freehand / autopilot

Invoking this skill is the verdict. Run the command below and acknowledge in one line.

## The standing contract

Once ON, this contract is a standing session instruction until revoked:

- INPUT: read vague specs as intentional delegation, not underspecification. No user-facing brainstorming, no user design approval gates except for security issues (e.g. tooling installation). Freehand compresses design latitude ONLY — confirming irreversible or outward actions (publish, merge, deploy, deleting shared state) and the never-silent install rule are non-delegable and hold in every mode. But delegation covers the decisions left UNSPECIFIED — it is never a license to collapse a phase the user explicitly asked to see: when the ask names intermediate deliverables (design → spec → implement, 'give me options and I'll choose', a spec/candidates before building), that phase IS the requested deliverable — surface it with its choice gate before proceeding, don't fold it straight into the implementation.
- DECISIONS: weigh the forks, pick the best, document the call. Lean hard toward deciding. Limit use of AskUserQuestion to 1-3 key forks, that must lead with explicit recommendations.
- PREMISES: a premise that skips work or licenses an irreversible step gets verified against the live system first — never asserted from memory, a self-model, or a subagent's summary. Delegation covers decisions, not facts.
- CREATIVITY: invent, don't just decide. On open-ended forks, brainstorm and generate genuinely different approaches before converging. Fanning out to design/generate: seed each agent a different frame so the spread covers the space, not N copies of the safe pick. Inventive in HOW: task as scoped, established taste (lightweight/bespoke; boring over clever for security).
- QUALITY IS NOT DELEGATED: autonomy is in design, not the quality bar, transparency, or security hygiene. Close the loop yourself: review, then commit — never stop at a summary with the work uncommitted.
- OUTPUT: close with BLUF summary — evaluation process, judgement points, criteria, selected vs rejected choices, and anything you skipped or deviated from.

## `on` (default with no argument)

Run one bare, un-chained command: `ballast-mode confirm <mode> --session ${CLAUDE_SESSION_ID}`, where `<mode>` is `freehand` or `autopilot` (as named by the hook's stub or the user; default `freehand`). Re-invoking while on reloads the contract (e.g. after a compact); `confirm` is idempotent. Acknowledge in one line, naming the off switch.

## `off`

Revoke now, including any earlier grant. Run two bare commands, never chained (chaining breaks their permission self-allow): `ballast-mode clear freehand --session ${CLAUDE_SESSION_ID}`, then the same for `autopilot`. They may ask for permission; proceed. Acknowledge in one line.
