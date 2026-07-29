---
name: harness-sweep
description: >-
  Cross-project postmortem triage for the harness-owning repo (plugin source repo or harness
  hub) — sweep every registered project's .claude/postmortem reports plus the HARNESS-RECS and
  DRIFT-SIGHTINGS ledgers. Default = triage digest of all unseen reports (watermark-based);
  `--focus` = targeted dossier, project-scoped sweep, or single-report digest; `--deep <range>`
  = full audit of every in-range report, swept or not; flags combine.
when_to_use: >-
  Use when the user types "/harness-sweep", says "sweep the postmortems", "what's new across
  projects", "cross-project harness triage", "check the drift inbox", "run the harness audit",
  asks for a "dossier on <skill/hook/agent>", or hands a report path to "digest" — or when the
  harness-sweep-nudge SessionStart line reports unswept reports or drift sightings. NOT
  session-postmortem (that PRODUCES one session's report; this CONSUMES them across projects),
  not adversarial-audit (codebase audits). Fan-out expense — invoke on explicit request or the
  nudge, never on a passing mention.
argument-hint: "[--focus <topic|project|report-path>] [--deep <last N days | range>]"
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Task, AskUserQuestion
---

# harness-sweep

**Digest, then stop.** The sweep discovers, extracts, and reconciles postmortem signal against the ledgers; adjudication, ledger dispositions, and fixes belong to the invoking session and the user. The sweep advances the watermark — it never flips a HARNESS-RECS row, never registers a drift shape, never ships a fix. This seam is the design: extraction is mechanical and cheap, adjudication needs the full top-tier context of whoever invoked you.

## State & registry

`<state-home>/postmortem/SWEEP-STATE.md`, where `<state-home>` = `$BALLAST_CLAUDE_HOME` if set, else `~/.claude`. Prose header, one `swept: <ISO-timestamp>` line, then one registry line per project:

```
<name> · <absolute-postmortem-dir> · last=<report-basename|NONE>
```

Paths are absolute with forward slashes (drive-letter form on Windows, e.g. `C:/Users/you/Projects/app/.claude/postmortem`); normalize backslashes when writing. Lines without ` · ` are ignored, so the file stays hand-editable. Candidate reports = files matching the `YYYY-MM-DD-*.md` date-prefix pattern — this excludes the ledgers and SWEEP-STATE itself where they share the hub dir.

Newness is deliberately asymmetric between the hook and this skill: the harness-sweep-nudge hook counts strictly-greater basenames (cheap; a missed same-day nudge is harmless), while the sweep includes every report whose 10-char date prefix is ≥ the watermark's date. Re-reading an already-swept same-day file is safe — ledger-first reconciliation makes re-extraction idempotent — and the over-inclusion is bounded to one day. Reports renamed or backfilled with older dates escape the default mode; accepted, because topic focus and `--deep` are watermark-free — nothing is permanently invisible (suspect backfill → run those).

Watermark duty: seeding initializes the `swept:` line when it writes the file; after that, only the default mode advances each swept project's `last=` and `swept:`, at digest-write time — never earlier, and never in a `--focus` or `--deep` run. (`swept:` is informational; the nudge hook derives newness from the `last=` lines.)

## First run — seeding the registry (interactive; never dispatch unattended)

No SWEEP-STATE? Seed it in-session, whatever mode was invoked — the seed write (registry lines + initial `swept:`) is registry initialization, not a sweep, so it doesn't breach the watermark-free modes' no-write rule. Seeding requires AskUserQuestion — put "(Recommended)" in the recommended option's LABEL, not only its description (ballast's askuserquestion-recommend hook exit-2s a bare label) — so a background or executor leaf would stall silently; surface this before any unattended dispatch.

1. Dispatch the scout to gather candidates: the state-home's own `postmortem/` dir (the hub — include it if it holds date-prefixed reports), the current project's `.claude/postmortem/`, and siblings via `<parent-of-project-root>/*/.claude/postmortem` globbing.
2. AskUserQuestion: confirm, prune, or add projects.
3. AskUserQuestion: initial watermark posture — (Recommended) backlog = the last 7 days / sweep everything (`last=NONE`) / start from now.
4. Write the file, then proceed with the invoked mode.

## Modes

Two orthogonal flags — a bare argument is an implicit `--focus`. `--focus` narrows WHICH reports; its value resolves in order: an existing file → single-report digest; a name matching a SWEEP-STATE registry line → project scope; anything else → topic. `--deep` widens HOW FAR: every report whose date prefix falls in the range enters, swept or not, and the full adjudication pipeline runs. Topic focus is corpus-wide by nature (grep is the filter — cheap, extraction only touches hits); project focus is watermark-based like the default.

| Invocation | Mode | Corpus | Output |
|---|---|---|---|
| *(no argument)* | Incremental sweep | Reports past each project's watermark, plus the DRIFT-SIGHTINGS inbox | Triage digest → **stop**; advance watermarks |
| `--focus <topic>` | Targeted dossier | Whole registered corpus — grep-first, extract hits only | Focused dossier on one surface/topic |
| `--focus <project>` | Project-scoped sweep | That project's unseen reports only | Scoped triage digest; watermarks untouched |
| `--focus <report-path>` | Single-report digest | That one report | Reconciled blocks in-chat; no digest file, no watermark touch |
| `--deep [<range>]` | Full audit pipeline | Every report in the range, swept or not; undated → quote budget options first | Adjudicated theme digest; fixes land later via the plan-handoff protocol |
| `--deep <range> --focus <X>` | Scoped deep audit | The focused slice within the range | Full pipeline over the slice |

Watermark writes belong to the default alone — no `--focus` or `--deep` run advances `last=` or `swept:`; whatever they cover simply re-enters the next incremental sweep (re-extraction is idempotent).

## Shared spine (every mode)

- **Ledger-first.** Read `<state-home>/postmortem/HARNESS-RECS.md` and `DRIFT-SIGHTINGS.md` BEFORE dispatching any leaf. A chain the ledger records as CLOSED / SUPERSEDED / PARKED is reported as such (cite the row) — never re-surfaced as open work.
- **A carried chain is ONE finding with high n**, never n findings. Join chains via the extractors' CHAIN-STITCHES blocks; count carries as weight.
- **Extraction leaves are the harness-sweep-extractor agent** (Sonnet; Read/Glob/Grep only — it cannot trip permission prompts mid-fanout). Everything judgment-shaped — clustering, placement, skepticism, ranking, adjudication — stays with you, top-tier.
- **Corpus retrieval is the harness-sweep-scout agent** (Haiku; Read/Glob/Grep only, same no-prompt posture) — watermark enumeration, topic greps, and seeding candidate gathering dispatch to it and return as compact lists, keeping raw glob/grep output off the main window. Neither leaf ever reads the ledgers.
- **Visual findings follow the defect-to-invariant ratchet** (dispositions in the visual-verification-gate skill): the artifact — fixture row, rung-0 assertion, matrix cell, per-fixture checklist line — IS the rec, not a prose bullet. Shadow-first: a prose-only visual rec is still reported, marked `WOULD-REJECT (ratchet shadow): no artifact shape`. Nothing is rejected yet — the shadow window exists to measure what fraction of real findings are expressible before enforcing.
- **A cross-repo artifact becomes a ratchet ticket.** Fixture rows and matrix cells live in the *target* project's manifest, and this repo never authors another project's rows — so the digest carries a dated ticket instead (target project · the escape's report id · the artifact to add, concrete enough for that project's next session to land unedited). Only rung-0 assertions and this repo's own rows are local work. Delivery and any ledger row are the invoking session's, per the propose-only seam.
- **Output = a digest file + an in-chat BLUF (≤10 lines).** Digest home: the plugin source repo's `.claude/harness-sweep/<YYYY-MM-DD>-harness-sweep[-<topic>|-deep].md` (git-tracked meta-eval corpus, sibling to `.claude/postmortem/`; it will contain project names and source-machine paths, so it belongs in a scrub-exempt session-output area, never in shipped content). Invoked away from the source repo → write to `<state-home>/postmortem/` instead and say so.

## Workflow — incremental sweep (default)

1. Resolve SWEEP-STATE (seed if absent). Dispatch the scout to enumerate new reports per project (inclusive date rule). Spot-check the returned basenames before dispatching extractors — every one must resolve on disk (Glob any that look odd); a transcription-mangled date silently pulls stale, already-swept reports into the corpus.
2. Read both ledgers yourself — this is reconciliation context the leaves must not duplicate.
3. Dispatch one harness-sweep-extractor per new report (batch 2-3 per leaf if the backlog is large).
4. Reconcile: join carried chains (CHAIN-STITCHES), drop ledger-closed chains (cite the row), collect ballast-handoff recs, ESCALATED items, and unregistered drift sightings.
5. Write the digest: open chains ranked by escalation weight → new ballast handoffs → unregistered drift shapes → ledger rows that need a disposition → one-line gists of swept sessions.
6. Advance watermarks + `swept:`, emit the BLUF, and **stop** — adjudication is the invoking session's next move, not this skill's.

`--focus <project>` runs this same workflow restricted to that project's registry line, minus step 6's watermark/`swept:` advance (the default mode's alone) — its reports simply re-enter the next full sweep.

## Workflow — targeted dossier (`--focus <topic>`)

1. Name the topic's spelling/name variants yourself (that's judgment), then dispatch the scout to grep them across every registered postmortem dir; check the topic against the ledgers you already read. No watermark.
2. Dispatch extractors over hit reports only, passing the topic; leaves return topic-scoped blocks plus TOPIC-MISSES near-misses.
3. Dossier shape: chain timeline (oldest → newest, with n-counts and 🔴 markers) → ledger dispositions already recorded → open residuals → the plugin surfaces to read next (files, hooks, skills). Zero hits → say so plainly and suggest spelling/name variants; do not pad.

## Workflow — single-report digest (`--focus <report-path>`)

The "a concurrent session just closed and its report landed mid-session" shape: the argument resolves to one existing report file, so grepping the corpus for it as a topic would be a misroute.

1. Read both ledgers yourself — even one report needs disposition context (the first live run's lead rec was already ledger-CLOSED).
2. Dispatch ONE extractor with the report path, no topic.
3. Return the reconciled blocks in-chat — ledger-recorded chains cited as such — and stop. No digest file, no watermark or `swept:` touch; a registered project's report still enters the next incremental sweep normally (re-extraction is idempotent).

## Workflow — deep audit (`--deep [<range>]`)

The heavyweight periodic form. The date range IS the corpus bound and the cost lever: every report whose date prefix falls in the range enters, swept or not. No range given → have the scout count reports per candidate range, then AskUserQuestion with 2-3 quoted options — finder count ≈ ceil(reports ÷ 4); calibration anchor (measured, 2026-07-11 live run): 103 reports ≈ 53 agents ≈ ~23M tokens end-to-end. That question also means an undated `--deep` can never run unattended — surface it before any background dispatch. Combined with `--focus`, the same pipeline runs over only the focused slice within the range (topic grep or project filter first). Corpus size sets the fan-out, never thoroughness reflex.

1. **Ledger probe first** (named stage, before any theme exists): every chain with a CLOSED / SUPERSEDED / PARKED row is verify-only downstream — never re-fixed.
2. Extractors over the in-range corpus (the scout enumerates the roster), ~4 reports per leaf.
3. One top-tier clusterer: findings → themes (each ESCALATED finding-id must land in exactly one theme). Cluster inline, or dispatch a FRESH agent with the extractor blocks serialized into its prompt — never a `fork`, which inherits your context and can echo your narration instead of clustering (watched: 477K tokens, zero output).
4. Per theme, pipelined (no barrier): a top-tier placement judge proposes where the fix lives (plugin skill/hook/agent, global config, project-local, memory, nowhere) — then a MANDATORY adversarial overfit skeptic attacks that placement. Skeptics exist because judges systematically over-home: in the reference run 8 of 13 placements were demoted toward narrower or cheaper homes, always that direction. A judge-only pipeline ships upper bounds. Judges and skeptics ground in LIVE source, never the extractors' snapshot — a long run races concurrent sessions (a watched skeptic caught a fix that shipped mid-sweep), and the invoking session re-verifies against HEAD at adjudication.
5. One completeness critic: cross-check every ESCALATED finding-id against the themes — each appears in a theme or in an explicit drop line. (The reference run's critic caught an escalated finding that had silently fallen out.)
6. Digest = adjudicated themes with surviving placements + the ledger-probe table. Fixes are the invoking session's work, via the plan-handoff protocol.

## Authoring split

| Leaf agents (extractor Sonnet · scout Haiku) | Orchestrator (you) |
|---|---|
| Extractor: per-report blocks — GIST, OPEN-RECS, PATTERNS, DRIFT, HANDOFFS; CHAIN-STITCHES tail | Ledger reconciliation, chain joining, ranking, digest, BLUF, watermark write |
| Scout: enumeration lists, topic-grep hits, seeding candidates — retrieval only, no interpretation | Topic-variant naming, clusterer/judge/skeptic/critic prompts in `--deep` |
| Neither leaf reads the ledgers (disposition context is yours) | |

## Context economy — what stays inline, deliberately

The lean-orchestrator rule has three deliberate exceptions here; don't "optimize" them into leaves:

- **Ledger reads stay with you.** Disposition context is the sweep's core judgment input — content that IS your context, not bulk to offload. Growth valve: if the ledgers ever reach hundreds of lines, grep the rows matching the swept projects/chains yourself instead of whole-file reads (the leaves' no-ledger rule still holds).
- **Digest, BLUF, and watermark writes stay with you.** The digest is your synthesis — delegating the write ships the full content to a leaf anyway and adds a round trip; the watermark is a two-line edit to a tiny file.
- **No post-extraction aggregator leaf.** Extractor returns are already in your window — a chain-joining leaf can't un-spend them, and routing blocks through files instead would give extractors Write access and break their no-permission-prompt tool posture.

## Edge cases

| Case | Move |
|---|---|
| No SWEEP-STATE | Seed interactively (above) |
| Registry dir missing on disk | Skip it; note the stale entry in the digest |
| Zero new reports | One-line BLUF; still advance `swept:` |
| Huge first backlog | Offer to bound (e.g. last 14 days) before dispatching |
| Ledgers missing | Create empty with their standard headers; note it |
| `--focus` value looks like a path but doesn't resolve | Say so and ask whether it was meant as a topic — never grep a path string as a topic |
| `--deep --focus <report-path>` | Degenerate — run the single-report digest and note the ignored `--deep` |

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "The fix is obvious — ship it during the sweep" | The seam exists because adjudication needs the invoking session's full context; sweeps that fix things are how ledger-closed chains get re-fixed |
| "The judge looked confident, skip the skeptic" | 8/13 reference-run placements were demoted by skeptics; judge-only output is a known upper bound |
| "Update the HARNESS-RECS rows while I'm here" | Dispositions ARE adjudication — the sweep proposes, the session and user dispose |
| "Big corpus, add more finders to be thorough" | Finder count scales with report count (~4 per leaf), never with thoroughness reflex |
| "This visual rec is obviously right — just write the prose bullet" | Prose bullets are what the visual stack already tried, at no measured yield; name the artifact shape, or mark the rec `WOULD-REJECT (ratchet shadow)` and let the shadow window count it |
| "Each carried report is its own finding" | A carried chain is ONE finding with high n — splitting it double-counts evidence and buries the ranking |
