---
name: harness-sweep
description: >-
  Cross-project postmortem triage for the harness-owning repo: sweeps every registered project's .claude/postmortem reports and the HARNESS-RECS / DRIFT-SIGHTINGS ledgers into one adjudicated digest. Default = unseen reports; `--focus` = one topic, project, or report; `--deep <range>` = re-audit a date range.
when_to_use: >-
  Use when the user types "/harness-sweep" or asks to sweep the postmortems, triage across projects, check the drift inbox, or build a dossier on a skill, hook, or agent. Not session-postmortem (that writes one session's report; this reads them all). A costly fork: run only on explicit request.
argument-hint: "[--focus <topic|project|report-path>] [--deep <last N days | range>]"
model: fable
effort: medium
context: fork
background: true
---

# harness-sweep — one bundle, one judgment pass, one digest

The job: read the postmortem corpus and find the durable principles and high-altitude fixes that make the harness better. A script turns the corpus into one pre-compressed bundle; you adjudicate. Quality of findings outranks runtime.

**Digest, then stop.** You propose; the invoking session and the user dispose. The sweep advances the watermark and nothing else: it never edits a HARNESS-RECS row, registers a drift shape, or ships a fix. Sweeps that fixed things are how ledger-closed chains got re-fixed.

## Modes

`--focus <value>` narrows which reports: an existing file path → that one report; a registry project name → that project's unseen reports; anything else → a topic grep across the whole corpus. `--deep <range>` re-enters every report in the range, swept or not. Flags combine; a bare argument means `--focus`. Only the default mode advances the watermark.

| Invocation | Output |
|---|---|
| *(none)* | Triage digest file, then advance |
| `--focus <topic>` | Dossier file, ending with the plugin surfaces to read next |
| `--focus <project>` | Scoped digest file |
| `--focus <report-path>` | Reconciled blocks in chat only: no file, no commit |
| `--deep <range>` | Digest file with chains grouped into themes |

## 1. Bundle

`ballast-sweep digest [--focus <val> [--variant <v> …]] [--deep <range>]`, bare, from the Bash tool. It prints the bundle path and a counts line. For a topic, name the spellings and aliases yourself and pass each as `--variant`.

- Exit 3 (undated `--deep`): return the per-range count table with a one-line recommendation, and stop.
- Exit 4 (no registry): see Seeding.
- Any other failure: report it and stop. Never assemble a bundle by globbing reports yourself; model-side enumeration has shifted dates and pulled stale reports in before.

## 2. Read the bundle once

One Read. Don't open source reports the bundle already parsed. An `UNPARSED` entry carries a raw excerpt: judge what parsed and report parse health.

## 3. Judge

Postmortems are overfit by design: one session's friction each. The finding is the class, never the incident.

- Prefer subtraction, generalization, and root-cause principles over added mechanism. An additive rec must say why removal or simplification can't do the job.
- Place each fix at the narrowest, cheapest home that works: a plugin skill, hook, or agent; global config; the project; memory; or nowhere. Placement judges over-home (8 of 13 reference placements were demoted toward narrower homes). Check the live source before proposing a fix: it may already exist.
- Reconcile against the inlined ledgers before ranking. A chain the ledger marks CLOSED, SUPERSEDED, or PARKED is reported that way with its row quoted verbatim (rows have no IDs), never re-opened.
- A carried chain is one finding with a high n. Rank by n and escalation, not recency.
- Newness includes the watermark's own date, so a few swept reports re-enter: reconcile them, don't re-count them.
- Every escalated rec lands in a finding or in one explicit "dropped" line.
- Report the bundle's gaps as they are: `UNPARSED`, `STALE-DIR`, chain members outside the corpus slice. `HANDOFFS:` lines are phrase matches, not verdicts.
- Visual recs follow the defect-to-invariant ratchet: the rec is the artifact (fixture row, assertion, matrix cell, checklist line). List a prose-only visual rec as `WOULD-REJECT (ratchet shadow): no artifact shape`.
- A fix that belongs in another repo becomes a dated ratchet ticket: target project · report id · the artifact to add, concrete enough to land unedited. This repo never writes another project's rows.
- Work in one context. Fan out only if the bundle genuinely won't fit, and record the dispatch count.

## 4. Write the digest

Path: `.claude/harness-sweep/<YYYY-MM-DD>-harness-sweep[-<topic>|-deep].md` in the plugin source repo. It names projects and local paths, so it stays in this scrub-exempt area. Away from that repo, write to `<state-home>/postmortem/` (`$BALLAST_CLAUDE_HOME`, else `~/.claude`) and say so.

The model-written core is ≤ ~60 lines, in this order: ranked open chains (n, members, ledger status) → new ballast handoffs → unregistered drift shapes → ledger rows needing a disposition. Density is the bar.

Run `date -u +%FT%TZ` right before the Write and head the file with:

`**Sweep run:** <UTC> · <mode + args> · <N reports / M projects> · bundle <B>B · dispatches <n> · fable medium`

Write `fable medium` literally: it is pinned in frontmatter, and a self-perceived model can misreport. Bundle bytes come from the counts line. Then append the script's appendix with `cat <bundle-dir>/appendix.md >> <digest>`; never retype it.

Commit the digest by path in a standalone `git commit`. If there is no repo or the commit is declined, leave the file and say where it is. End with a ≤10-line BLUF in chat.

## 5. Advance (default mode only)

`ballast-sweep advance --manifest <path>`, using the manifest path the digest step printed (a concurrent run's manifest can take the newest slot). It refuses a non-default manifest with exit 2; that is the guard working. Zero new reports still advances.

## Seeding (exit 4)

A backgrounded fork can't ask the user, so seeding runs in the invoking session. Run `ballast-sweep seed --scan`, return its candidates with these steps, and stop:

1. Ask twice with AskUserQuestion: confirm, prune, or add projects; then pick the starting watermark (backlog = last 7 days, recommended / everything, `last=NONE` / from now).
2. Write `<state-home>/postmortem/SWEEP-STATE.md`: a prose header, one `swept: <ISO-timestamp>` line, then one `<name> · <absolute-forward-slash-postmortem-dir> · last=<report-basename|NONE>` line per project. The ` · ` separator is a byte contract with the nudge hook. Then re-invoke the sweep.
