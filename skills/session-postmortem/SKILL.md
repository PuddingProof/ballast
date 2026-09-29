---
name: session-postmortem
description: Produce a structured, evidence-cited post-mortem report of a Claude Code session — how it went, where the user had to steer, what tooling fired — from a deterministic transcript digest, in one pass.
when_to_use: Use when wrapping up or closing out a session, before /compact at a natural boundary, or on any "retro", "post-mortem", "how did this session go", "session review" request — or when the user types /session-postmortem. Also for a PAST session via --id. A session self-state wrap — not docs-authoring or skill-craft (durable-docs), and not the cross-project sweep (harness-sweep consumes these reports; this skill produces one).
argument-hint: "[--id <session-id>] [--focus <topic>]"
model: opus
effort: medium
context: fork
background: true
---

# session-postmortem — one transcript, one pass, one report

The whole job: *"look at this transcript and tell me how the session went"* — made fast, consistent, and evidence-tied. One deterministic script pass turns the transcript into a size-capped digest; you read it once and write one capped report. No leaf agents, no priors sweep, no tier ladder, no fixes applied. Speed is a design constraint: the run should finish in ~2 minutes; every added step, re-read, or line over cap spends it.

You are the session's adjudicator, not its advocate: the report grades how the session *went* — including how well the assistant worked — from what the transcript shows, never from how the work felt from inside. The digest outranks your memory of the session; your inherited context is background, useful mainly to decode ambiguous digest lines.

## 1. Digest (script, ~seconds)

```
ballast-extract digest [--id <session-id>] [--focus <topic>]
```

Bare `ballast-extract` from the Bash tool (the shim resolves python itself). No `--id` → the current session via env, falling back to the newest transcript for this project. Output lands in `~/.claude/.cache/ballast/digest/<session-id>/` — the command prints the digest path; `digest.md` is everything: manifest, all user turns verbatim (`U<n>`, kind-tagged), a one-liner timeline (assistant snippets, tool calls, dispatches, compact boundaries, interrupts, idle gaps), inventory tables (tools/skills/agents/hooks/commands with counts and errors), files touched + commits, and script-detected anomalies.

- Script fails → say so in the report header and write a context-only post-mortem from your inherited context (mark it `digest: unavailable`); never silently skip the run.
- `--focus <topic>` is echoed into the manifest; it narrows §Steering and §Friction below to the topic — the rest stays summary-level.

## 2. Read `digest.md` — once

One Read call. Large digests are already budget-capped by the script; do not open the raw transcript, and do not re-read the digest per section. While reading, mark the U-turns where the user *redirected* rather than *requested* — that distinction is the core of the report.

## 3. Write the report — hard cap 90 lines

Path: `.claude/postmortem/YYYY-MM-DD-<suggested_slug>-<sid8>.md` (slug and sid from the manifest; create the dir if missing). If that Write is denied (sandboxed or non-interactive sessions block `.claude/` writes), write the same report to `./postmortem-report.md` at the repo root instead and say so — never re-draft it into your reply. Run one `date -u +%FT%TZ` *immediately before* the report Write — it stamps the digest→draft span against the manifest's `generated_utc`, so running it any earlier under-reports.

Structure (caps are per-section maximums, not targets — an uneventful session's report should run well under them):

```
# Post-mortem — <project> — YYYY-MM-DD (<sid8>)
**Session:** <session-id> · <transcript_lines> lines · <wall> wall
**Models:** <model: in/out tokens, ~$cost each> · total ~$<total>
**Invocation:** <args or "(none)"> · digest <digest_bytes>B
**Postmortem run:** <generated_utc> · digest→draft <Xm Ys — exact, the date call minus generated_utc> · opus medium (write literally — frontmatter-pinned; your self-perceived model can misreport) · schema v<manifest "schema" value>

## Verdict — <🟢|🟡|🔴>            [≤5 bullets]
## Narrative                       [≤12 lines]
## Steering & corrections          [≤20 lines]
## Friction & failures             [≤12 lines]
## Inventory                       [≤15 lines]
## Signals                         [≤8 one-liners]
```

- **Verdict** — gauge + TL;DR: what was attempted, what shipped, the headline friction. 🟢 clean / 🟡 shipped with friction / 🔴 objective missed or damage done.
- **Narrative** — the session's phases in plain prose. What the user asked for, what actually happened, how it ended.
- **Steering & corrections** — the adjudication core. Every point where the user had to redirect, correct, interrupt, or re-explain: what they typed (cite `U<n>`), what it reveals (wrong assumption, ignored instruction, missed trigger, drift). A session with none: say so in one line — that IS the finding.
- **Friction & failures** — tool errors, dead ends, retries, permission stalls, guard/hook events, API hiccups; from the timeline and anomalies sections, each with its marker.
- **Inventory** — condensed table: skills / agents / hooks / slash commands / MCP that fired, with counts — plus the judgment layer the script can't do: anything that fired uselessly, or should have fired and didn't.
- **Signals** — recommendations as checkbox one-liners `- [ ] R<n> — <signal>`, **proposed-only** (no fixes applied, no docs written, no memory edits — harness-sweep and the user dispose). This report shape is a parse contract: harness-sweep's script consumes it, and the round-trip fixture in `skills/harness-sweep/scripts/test_sweep.py` pins it — a shape change here must update that fixture in the same commit. No signal is a valid outcome; don't invent one per section.
- **Drift duty (conditional):** if the digest's ANOMALIES lists unknown transcript shapes, append one sighting line to `~/.claude/postmortem/DRIFT-SIGHTINGS.md` (format per that file's header; bump an existing unregistered line rather than duplicating) and add a one-line drift footnote after Signals. No unknown shapes → skip both, silently.

Evidence rule: every claim in Steering and Friction carries a `U<n>` or timeline cite. No cite, no claim — move it to Narrative as color or drop it.

## 4. Commit

Stage the report by path, then commit it in a **standalone** `git commit` (never chained — the commit guard blocks compounds). Message: `Postmortem: <one-line session summary> (<sid8>) -- <gauge>`. Not a git repo, or the commit is declined → leave the file and say where it is.

## Past sessions (`--id`)

Same flow; the digest resolves the given id across project dirs — full UUID or any unique prefix (a report's sid8 pastes as-is). An unresolvable or ambiguous `--id` fails loudly rather than falling back to another session: report the script's error and stop; don't guess at ids. Your inherited context is about the *current* session — for a past session it is noise: adjudicate purely from the digest.
