---
name: freehand
description: >-
  Canonical home of ballast's delegated-autonomy mode (freehand/autopilot): the full
  standing contract lives in this skill's body, and invoking the skill toggles the mode
  on/off for the session (settling the per-session statusline chip).
when_to_use: >-
  Use when (1) the user invokes it as a slash command — "/freehand on", "/freehand off",
  "/ballast:freehand on|off" — or states the toggle directly: "turn freehand mode on/off",
  "turn autopilot on/off", "freehand mode is on", "autopilot off" (a declarative
  grant/revoke counts even with no hook injection alongside it — a message spliced into an
  in-flight turn arrives with no hook fire); or (2) the freehand-mode hook's injected stub
  directed you here after you adjudicated a keyword arm as a genuine grant. NOT for prose
  that merely discusses the mode: use-vs-mention adjudication of an ambiguous keyword
  belongs to the freehand-mode hook, and this skill never self-invokes on a discussion of
  the mode.
argument-hint: "[on|off]"
allowed-tools: Bash
---

# Freehand / autopilot — standing contract + toggle

The command IS the verdict — adjudication is already done, by the slash command, the
declarative statement, or the hook's stub routing. Be brisk: one bare `ballast-mode` call
plus a one-line acknowledgment, no diagnostics, no extras. While the mode is ON, the hook
mirrors a one-line standing reminder on every subsequent prompt; `off` stops it.

## The standing contract

Once ON, this contract is a standing session instruction until revoked:

- INPUT: read vague specs as intentional delegation, not underspecification. No user-facing brainstorming, no user design approval gates except for security issues (e.g. tooling installation). Freehand compresses design latitude ONLY — the per-action approval floor of the principles' 'Irreversible & outward actions' contract (and its never-silent install rule) is non-delegable and holds in every mode. But delegation covers the decisions left UNSPECIFIED — it is never a license to collapse a phase the user explicitly asked to see: when the ask names intermediate deliverables (design → spec → implement, 'give me options and I'll choose', a spec/candidates before building), that phase IS the requested deliverable — surface it with its choice gate before proceeding, don't fold it straight into the implementation.
- DECISIONS: weigh the forks, pick the best, document the call. Lean hard toward deciding. Limit use of AskUserQuestion to 1-3 key forks, that must lead with explicit recommendations.
- PREMISES: a premise that skips work or licenses an irreversible step gets verified against the live system first — never asserted from memory, a self-model, or a subagent's summary. Delegation covers decisions, not facts.
- CREATIVITY: invent, don't just decide. On open-ended forks, brainstorm and generate genuinely different approaches before converging. Fanning out to design/generate: seed each agent a different frame so the spread covers the space, not N copies of the safe pick. Inventive in HOW: task as scoped, established taste (lightweight/bespoke; boring over clever for security).
- QUALITY IS NOT DELEGATED: respect review and verification requirements. Autonomy is in design, not the quality bar, transparency, or security hygiene.
- OUTPUT: close with BLUF summary — evaluation process, judgement points, criteria, selected vs rejected choices.

## `on` (also the default with no argument)

- The mode is granted for the rest of the session — the contract above is now in force.
- Run ONE bare, un-chained command: `ballast-mode confirm <mode> --session
  ${CLAUDE_SESSION_ID}`, where `<mode>` is the granted mode, lowercase (`freehand` or
  `autopilot`): the mode named by the hook's stub or MODE MIRROR line when present, else the
  keyword the user used; default `freehand`.
- Re-invocation while already ON is a contract reload (e.g. post-compact, prompted by the
  hook's MODE MIRROR line) — `confirm` is idempotent: re-run it and re-acknowledge.
- Acknowledge in one line, naming the off switch.

## `off`

- The grant is revoked now — drop the contract, including one granted earlier in the
  session.
- Run exactly two bare commands (never chained — chaining a command breaks its permission
  self-allow): `ballast-mode clear freehand --session ${CLAUDE_SESSION_ID}`, then the same
  for `autopilot` (`clear` is an idempotent no-op when a mode is absent). These may
  permission-prompt without the allow-standing-grants marker — expected, proceed through
  the prompt.
- Acknowledge in one line.
