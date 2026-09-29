---
name: durable-docs
description: >-
  Check a durable-doc change before it persists: the reusable class, not the triggering instance,
  in its one right home, leaving the doc leaner. Covers CLAUDE.md, memory, README, living specs,
  skill bodies, and hook-injected text. Also audits a doc surface for drift.
when_to_use: >-
  Before writing or committing a durable-doc change ("add this to CLAUDE.md / memory", "document
  this", "note this for next time", a new always/never rule), when writing or editing a SKILL.md,
  agent body, or hook prompt, when deciding where something should live (including whether it
  should be a skill), and on "audit the docs" / "are the docs stale". Not for dated session
  output (postmortems, .notes records).
argument-hint: "[audit [<path>]]"
---

# durable-docs

Every future session that loads a durable doc re-reads it, so each line must change what that session does. Before a durable-doc change persists or commits, check it:

1. **Class, not instance.** Strip the dates, versions, hashes, counts, names, and "this time" details. If no reusable rule survives, it's an example or a record, not a rule: drop it, or leave it in the dated record. A one-off earns a rule only once it's lifted to the principle behind it.
2. **One home.** Grep the durable surface (CLAUDE.md files, skills, hook text, memory) before adding. If the rule already lives somewhere, point there; never copy it. Never transcribe a live value (a version, count, list, or schema): cite the file that holds it.
3. **Right home**, by who needs it and when:
   - every session → the narrowest CLAUDE.md that covers it
   - one task or topic → a skill
   - humans → README or `docs/`
   - a stable fact or taste, needed sometimes → memory
   - dated, in flight, or evidence → `.notes/`, a postmortem, or the backlog. Durable docs cite these as provenance, never as current truth.
4. **Leaner than found.** Read the whole doc first. Write the change into the existing sentences, fix every line it makes redundant or contradictory, and cut what no longer steers or what the code already shows. Never append a block beside the old one. A snapshot doc describes the present and carries no history.

Soft-wrap prose: one line per paragraph or bullet, never hard-wrapped at a fixed column.

## Prompts (skills, agent bodies, hook text)

A prompt is a durable doc, so the four checks apply. Also:
- **Start from one sentence.** State the job in one sentence; add a line only for a failure you have watched happen.
- **Run a no-skill control.** Give the same realistic task to one fresh subagent with the draft and one without. If the outputs match, the draft does nothing: cut it or don't ship it. For a plugin, `claude plugin eval --ablation with-without` automates this, but its runs load no CLAUDE.md or user settings.
- **Numbers and mechanisms bind; prose limits don't.** "Concise" or "a few" reads differently every run: use a number keyed to something measurable, a script, or a hook.
- **A description fires only on what the user asks.** A step meant for the agent's own situation ("about to commit", "about to call it done") belongs in a hook at that moment or in the skill the agent is already running.

## Audit (`audit [<path>]`)

Read-only. The default scope is this project's durable surface; `<path>` narrows it. Read every file fresh from disk, not from what's already in context. Run the four checks in reverse, and report each failure as one finding with one proposed fix: stale, duplicated, contradictory, overfit, wrong home, or bloated. Cite file plus heading or symbol, never line numbers. Rank by how often the text loads. Hand over the list; edit nothing without approval.
