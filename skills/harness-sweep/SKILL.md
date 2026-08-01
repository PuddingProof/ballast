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
model: fable
effort: medium
context: fork
background: true
---

# harness-sweep — one bundle, one judgment pass, one digest

The job: *"take a look at these postmortem reports and develop the durable principles and high-altitude ideas that improve my effectiveness with claude-code."* One script pass turns the whole corpus into a pre-compressed bundle — enumeration, parsing, chain-stitching, ledger inlining, appendix tables. You do the part it can't: adjudicate. Quality of adjudicated findings outranks runtime here.

**Digest, then stop.** You propose; the invoking session and the user dispose. The sweep advances the watermark and nothing else — it never flips a HARNESS-RECS row, never registers a drift shape, never ships a fix. That seam is the design: adjudication needs the invoking session's full context, and sweeps that fix things are how ledger-closed chains get re-fixed.

## Modes

`--focus` narrows WHICH reports; its value resolves in order: an existing file path → single-report digest; a name matching a registry line → project scope; anything else → topic grep across every registered dir. `--deep <range>` widens HOW FAR: every in-range report enters, swept or not. Flags combine; a bare argument is an implicit `--focus`. Only the default mode advances watermarks — whatever the other modes cover simply re-enters the next incremental sweep (re-extraction is idempotent).

| Invocation | Script call | Corpus | Output |
|---|---|---|---|
| *(no argument)* | `digest` | Reports past each project's watermark + the drift inbox | Triage digest → **stop**; then `advance` |
| `--focus <topic>` | `digest --focus X [--variant …]` | Whole registered corpus, grep hits only | Dossier on one surface; no advance |
| `--focus <project>` | `digest --focus <name>` | That project's unseen reports | Scoped triage digest; no advance |
| `--focus <report-path>` | `digest --focus <path>` | That one report | Reconciled blocks in-chat; no file, no commit, no advance |
| `--deep <range>` | `digest --deep <range>` | Every in-range report, swept or not | Adjudicated theme digest (pipeline below) |
| `--deep <range> --focus <X>` | both flags | The focused slice within the range | Same pipeline over the slice |

## 1. Bundle (script, ~seconds)

`ballast-sweep digest [--focus <val> [--variant <v> …]] [--deep <range>]`

Bare `ballast-sweep` from the Bash tool (the shim resolves python itself). It prints the bundle path and a counts summary; the bundle holds per-report blocks, the chain graph, both ledgers inlined verbatim, the drift inbox, the gist table, and parse health. State home is `$BALLAST_CLAUDE_HOME` if set, else `~/.claude`. Newness is inclusive of the watermark's date — deliberately wider than the nudge hook's strict `>`, and safe because ledger-first reconciliation makes a same-day re-read idempotent.

- **Exit 3** (undated `--deep`): the script printed a per-range count table. Return it in-chat with a one-line recommendation and **stop** — the user re-invokes with an explicit range. Undated deep never runs unattended.
- **Exit 4** (no SWEEP-STATE): seeding, below.
- Any other failure: report what broke, in-chat, and stop. Never hand-assemble a bundle by globbing reports yourself — model-side enumeration transcribes dates wrong and silently pulls stale reports in; removing that class is why the script exists.
- Topic variants are judgment, not retrieval: name the spellings and aliases yourself and pass each as `--variant`.

## 2. Read the bundle — once

One Read call. Don't re-read it per section, and don't open source reports the bundle already parsed. Entries the parser couldn't handle carry `UNPARSED` plus a trimmed raw excerpt — judge what parsed and surface the parse-health lines in the digest. A partial bundle is a working bundle.

## 3. Judge — the altitude mandate

Postmortems are overfit by design: n=1 snapshots of one session's friction. The sweep de-overfits. The finding is the class, never the incident.

- **Prefer subtraction, generalization, and root-cause principles over added mechanism.** Layering mechanism onto mechanism — patches on patches of harness bloat — is the live failure mode this pass exists to reverse. An additive rec must state why removal or simplification can't do the job; if it can't state that, it isn't a rec yet.
- **Reconcile against the inlined ledgers before ranking.** A chain the ledger records as CLOSED / SUPERSEDED / PARKED is reported as such with the row quoted verbatim (rows have no stable IDs — never cite by row number), never re-surfaced as open work.
- **A carried chain is ONE finding with high n**, never n findings. Rank by n and escalation weight, not by recency or bullet count.
- Keep the bundle's uncertainty visible — `UNRESOLVED-HANDLE`, out-of-corpus chain members, `STALE-DIR` entries are reported, never smoothed; and its `handoff=maybe` lines are candidates, not verdicts (the script applies no relevance judgment, so filtering them is your work).
- **Visual findings follow the defect-to-invariant ratchet** (dispositions in the visual-verification-gate skill): the artifact — fixture row, rung-0 assertion, matrix cell, checklist line — IS the rec, not a prose bullet. A prose-only visual rec is still reported, marked `WOULD-REJECT (ratchet shadow): no artifact shape`; nothing is rejected yet, the shadow window is measuring.
- **A cross-repo artifact becomes a dated ratchet ticket** — target project · the escaping report's id · the artifact to add, concrete enough to land unedited in that project's next session. This repo never authors another project's rows; only rung-0 assertions and this repo's own rows are local work.
- **Fan-out is a valve, not a default.** Single-context is the posture at any corpus ≤ ~30 reports — the bundle is pre-compressed for exactly that. Above it you MAY fan out (per-project pre-rank leaves, or parallel chain judgment) and fold the returns yourself: a measured lever for a corpus that genuinely won't fit one pass, never a thoroughness reflex. Record the count in the stat line either way; `dispatches 0` is the expected default.

## 4. Write the digest — model core ≤ ~60 lines

Path: `.claude/harness-sweep/<YYYY-MM-DD>-harness-sweep[-<topic>|-deep].md` in the plugin source repo (git-tracked meta-eval corpus, sibling to `.claude/postmortem/`; it carries project names and source-machine paths, so it belongs in a scrub-exempt session-output area, never in shipped content). Invoked away from that repo → write to `<state-home>/postmortem/` instead and say so.

Model-authored core, in order and hard-capped: **ranked open chains** (n, members, ledger status) → **new ballast handoffs** → **unregistered drift shapes** → **ledger rows needing a disposition**. Density is the quality bar — adjudication-grade content, not coverage padding.

Run `date -u +%FT%TZ` *immediately before* the Write (any earlier under-reports the span) and head the file with:

`**Sweep run:** <UTC> · <mode + args> · <N reports / M projects> · bundle <B>B · dispatches <n> · fable medium`
(`fable medium` written literally — frontmatter-pinned; a self-perceived model can misreport; bundle bytes come from the digest step's counts line.) Then append the script-authored appendix by concatenation — `cat <bundle-dir>/appendix.md >> <digest>` — never retype its tables. A topic dossier's core ends with one extra block: the plugin surfaces to read next (files, hooks, skills).

Then stage the digest by path and commit it in a **standalone** `git commit` — never chained, the commit guard blocks compounds; not a git repo or the commit declined → leave the file and say where it is. Emit the in-chat BLUF (≤10 lines). `--focus <report-path>` stops before all of this: reconciled blocks in-chat, no file, no commit.

## 5. Advance — default mode only

`ballast-sweep advance --manifest <path>` — pass the manifest path the digest step printed, never the newest-manifest default (a concurrent focus/deep run's manifest can land later and take that slot). It owns the SWEEP-STATE write and refuses (exit 2) on a non-default manifest — that refusal is the guard holding, not an error to work around. Zero new reports still advances.

## Deep audit (`--deep <range>`)

Corpus prep is a single `digest --deep <range>` call. The adjudication pipeline then runs as leaves dispatched **from this fork** (general-purpose agents), off the main window:

1. **Ledger probe first**, before any theme exists: every chain with a CLOSED / SUPERSEDED / PARKED row is verify-only downstream, never re-fixed.
2. **Clusterer** — findings → themes, each ESCALATED finding-id landing in exactly one theme. Cluster in-context, or dispatch a **FRESH** agent with the bundle blocks serialized into its prompt — never a fork, which inherits your context and echoes your narration instead of clustering.
3. **Per theme, pipelined (no barrier): placement judge → MANDATORY skeptic.** The judge proposes where the fix lives (plugin skill/hook/agent, global config, project-local, memory, nowhere); the skeptic attacks that placement. Skeptics are not optional — judges systematically over-home: 8 of 13 reference placements were demoted toward narrower or cheaper homes, always that direction. Both ground in **LIVE source**, never the bundle snapshot; a long run races concurrent sessions.
4. **Completeness critic** — every ESCALATED finding-id appears in a theme or in an explicit drop line.
5. Digest = adjudicated themes with surviving placements + the ledger-probe table. Fixes are the invoking session's work, via the plan-handoff protocol — and the disposing session re-verifies each surviving placement against current HEAD before shipping (a fix can land mid-sweep, or between the sweep and the disposition).

## Seeding the registry (exit 4)

Seeding is registry initialization, not a sweep, so it doesn't breach the watermark-free modes' no-write rule — but it needs AskUserQuestion, and this skill always runs as a backgrounded fork, which can't ask. On exit 4: run `ballast-sweep seed --scan`, return its candidates plus the steps below in-chat, and **stop** — the invoking session runs the steps in-line.

1. Two AskUserQuestions — confirm/prune/add projects from the candidates, then the initial watermark posture ((Recommended) backlog = last 7 days / sweep everything (`last=NONE`) / start from now). Put "(Recommended)" in the option's LABEL, not only its description.
2. Write `<state-home>/postmortem/SWEEP-STATE.md`: prose header, one `swept: <ISO-timestamp>` line, then `<name> · <absolute-postmortem-dir> · last=<report-basename|NONE>` per project — absolute forward-slash paths, and the ` · ` separator is a byte contract with the nudge hook. Then re-invoke the sweep.

## Edge cases

| Case | Move |
|---|---|
| Registry dir or a ledger missing | Script marks `STALE-DIR` / an absent ledger; carry the note into the digest, and create a missing ledger empty with its standard header |
| Zero new reports | One-line BLUF; still `advance` |
| Huge first backlog | State the count and offer a bounded range before judging |
| `--focus` looks like a path but doesn't resolve | Say so and ask whether a topic was meant — never grep a path string as a topic |
| Topic grep finds zero hits (`HITS: 0`) | Say so and suggest variant spellings — never pad a dossier |
| `--deep --focus <report-path>` | Degenerate — run the single-report digest, note the ignored `--deep` |

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "The fix is obvious — ship it during the sweep" | The seam exists because adjudication needs the invoking session's full context; sweeps that fix things re-fix ledger-closed chains |
| "Nothing to remove here, so add a rule/hook/step" | An additive rec must first argue why subtraction or generalization can't do the job — the layering IS the failure mode |
| "Update the HARNESS-RECS rows while I'm here" | Dispositions ARE adjudication — the sweep proposes, the session and user dispose |
| "The judge looked confident, skip the skeptic" | 8/13 reference placements were demoted by skeptics; judge-only output is a known upper bound |
| "Big corpus — fan out to be thorough" | The bundle is the compression; fan-out is a measured lever above ~30 reports, never a thoroughness reflex |
| "This visual rec is obviously right — just write the prose bullet" | Prose bullets are what the visual stack already tried at no measured yield; name the artifact shape, or mark it `WOULD-REJECT (ratchet shadow)` |
| "Each carried report is its own finding" | A carried chain is ONE finding with high n — splitting it double-counts evidence and buries the ranking |
