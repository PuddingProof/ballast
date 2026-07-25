---
name: session-postmortem
description: >-
  Use when wrapping up or closing out a Claude Code session — produces a structured,
  evidence-tied session post-mortem (all recommendations are proposed by default; pass --fix
  to auto-apply a scoped grant, memory / idea-tracker classes only).
when_to_use: >-
  Trigger before /compact, at session close, on a "retro"/"post-mortem"/"how did this session
  go" request, or when the user types /session-postmortem. A session self-state wrap — not
  docs-authoring or skill-craft (those are durable-docs / skill-forge).
argument-hint: "[--id <transcript-uuid>] [--commits <N | hash..HEAD>] [--focus <instructions>] [--fix [memory,ideas]]"
allowed-tools: Read, Glob, Bash, Write, Edit, Task
---

# Session Post-Mortem

Produce a structured markdown report evaluating the current Claude Code session. The goal is a self-improving harness and workflow loop: extract evidence from the session's artifacts and propose concrete improvements to CLAUDE.md, project memory, custom skills, hooks, or an idea tracker — always evidence-tied.

This skill runs as an **orchestrator + parallel-leaf split** to keep the expensive session model off the bulk material. The session model is the **orchestrator**: it resolves the transcript, runs the cheap gating/probe checks, bounds commits, probes the trackers, reads project context (CLAUDE.md, MEMORY.md, recent edits) for homing, then runs ONE inline `ballast-extract bundle` extraction — all dumps written to disk, not context — and picks a scaling **tier** from the bundle metrics. At STANDARD/HEAVY it dispatches TWO leaves in parallel: **`postmortem-extractor`** (sonnet) digests the bundle files, **`postmortem-priors-scout`** (haiku) retrieves from the prior reports. At LIGHT it dispatches neither — it reads the (small) bundle files itself. The orchestrator authors the judgment sections and assembles the report — the **Authoring split** table (under *Workflow*) is the single source for which sections are whose, and why.

This skill is **project-agnostic**: everything project-specific is derived from the current working directory and the session transcript.

**Resolve the git root before any `git` step — don't assume cwd is the repo.** Run `git rev-parse --show-toplevel`; if it errors (cwd isn't a git repo — e.g. a meta/umbrella project whose work-product repo lives elsewhere, like `~/.claude`), `cd` into the actual repo for every git command (commit window, diffs, idea/spec probes). Report and `.claude/<…>` probe paths still resolve relative to cwd.

## Argument

`$ARGUMENTS` controls scope:

| Argument | Meaning |
|---|---|
| (empty) | Default — analyze the current session transcript and all commits within its time window |
| `--id <transcript-uuid>` | Analyze a specific transcript (rebuilding a missed report, a historical session). Short-circuits self-discovery — see "Locating the transcript" |
| `--commits <N>` or `--commits <hash>..HEAD` | Last N commits, or an explicit range; transcript window inferred to cover them |
| `--focus <instructions>` | Adjust analytical focus per the freeform instructions |
| `--fix [<grant>]` | Auto-apply grant: a comma-list of the rec homes eligible for auto-application — `memory` (project memory), `ideas` (idea-tracker / IDEAS). Bare `--fix` = `memory,ideas`. **Absent (the default): nothing is auto-applied — every rec is proposed.** CLAUDE.md / hooks / settings / code are never grantable (step 5). |

Flags are canonical and compose (e.g. `--id … --focus …`). The bare legacy forms — an integer, a
`<hash>..HEAD` range, a bare transcript UUID — are syntactically unambiguous and still parse as
their flagged equivalents; any other bare freeform text reads as `--focus`.

## Before starting

Read these from disk before doing anything else:

- `CLAUDE.md` (project root) — project conventions.
- The auto-memory index — the `memory/MEMORY.md` that sits **beside the resolved transcript file** (see "Locating the transcript"): `…/projects/<encoded-cwd>/<uuid>.jsonl` → `…/projects/<encoded-cwd>/memory/MEMORY.md`. **Derive it from the resolved transcript path — never hardcode a project** (it always sits beside that same transcript, under its own encoded-cwd directory — never a different project's). Lives outside the project, so it isn't always auto-loaded — read it if present.
- `${CLAUDE_SKILL_DIR}/references/CHANGELOG.md` — the always-current index of the report schema's live version + change history.

These ground the orchestrator's judgment sections (§6 home-selection, §1's forward-looking note) in real project context — which is why those sections stay on the session model, not the extractor (see the *Authoring split* table). Read them before dispatching.

## Locating the transcript

**If the argument carries `--id <transcript-uuid>` (or a bare UUID), skip `resolve`** — glob
`**/<uuid>.jsonl` under `~/.claude/projects/` and use it directly (`resolve` self-discovers the
*current* session, so it would silently override a supplied UUID). Otherwise —

**Primary path — let `scripts/extract.py` self-resolve.** At skill load, `${CLAUDE_SESSION_ID}` already holds the current session's UUID; the script independently reads that same value from its own `CLAUDE_CODE_SESSION_ID` env-var fallback (unchanged) to find this session's transcript and backups deterministically (no cwd-encoding, no mtime guessing, case-insensitive to the project-dir name):

```
ballast-extract resolve
```

It prints `LIVE:` (the transcript path) and `ARCHIVES:` (any PreCompact backups for this session). Use `LIVE:` as `<transcript>` for the steps below; use the archives in step 2 if the session was compacted.

**Fallbacks** if `resolve` can't help (env var unset, or you're analyzing a different / historical session). Transcripts live under `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`, where `<encoded-cwd>` is the absolute project path with every non-alphanumeric char replaced by `-` (e.g. `c:\Users\alice\Projects\myapp` → `c--Users-alice-Projects-myapp`):

1. **UUID known from context** — glob it directly: Glob tool pattern `**/<session-uuid>.jsonl` rooted at `~/.claude/projects/`, or `ls ~/.claude/projects/*/<session-uuid>.jsonl`.
2. **UUID unknown** — encode the cwd and take the newest in that dir: `ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1`.
3. **Neither resolves** — `ls -lat ~/.claude/projects/*/*.jsonl | head -5`, pick newest by mtime, and **note the guess caveat** in metadata.

**Never fabricate a path** — if nothing resolves, exit with a hint to list available transcripts.

## Extraction helper

All transcript parsing goes through one script, invoked by the bare `ballast-extract` command (the plugin ships it in `bin/`, which is on PATH as a stable literal for any Bash tool call while ballast is enabled):

```
ballast-extract <subcommand> <transcript-path> [args]
```

**Permission step, opt-in-dependent.** Because `ballast-extract` is invoked as a bare command (not a path), the plugin's `ballast-allow.py` PreToolUse hook CAN auto-allow it: any plain `ballast-extract <args>` invocation — a single command, no chaining (`;`/`&&`/`|`), no substitution, no redirection — self-allows with reason "ballast's own transcript extractor". But this only fires if the user has opted in by creating `~/.claude/ballast/allow-standing-grants` (README "User-side configuration"); the hook is inert by default. Without that marker, expect a normal permission prompt on these calls — the skill still works, just with prompts.

**Forward slashes in every path argument** (transcript path, `bundle --out <dir>`). `ballast-allow.py`'s argument char class excludes backslash, so a Windows-style `C:\…\x.jsonl` silently drops the call to a manual permission prompt even with the marker set. Pass `C:/…/x.jsonl`.

Subcommands:

| Subcommand | Purpose | Output / exit |
|---|---|---|
| `resolve` | Self-discover this session's transcript + archives from its own `$CLAUDE_CODE_SESSION_ID` env-var read (same UUID as `${CLAUDE_SESSION_ID}`; no path arg) | Prints `UUID:` / `LIVE:` / `ARCHIVES:` |
| `compact-check` | Detect compaction markers (`isCompactSummary` recap OR a `system`/`compact_boundary` line) | Exit 1 if compacted (prints which marker fired); exit 0 otherwise |
| `drift` | Format-drift canary — tally any transcript shape outside scripts/extract.py's known registries | Prints `kind  value  count` rows; `no drift` when clean; always exit 0 |
| `time-window` | Get transcript timestamp range | Prints `<min-iso> <max-iso>` |
| `user-msgs` | Real user turns + AskUserQuestion answers + `(slash-command)` + `(mid-turn steer)` + `(autopilot goal)`, chronologically | Counts `/slash` echoes as turns (SSoT parity, rendered `/name args`); skips tool_results, isMeta/compact/sidechain/sdk-cli, bash-output echoes, task-notifications, auto-continuation goal echoes |
| `invocations` | Skill/Agent calls + their tool_result, tagged `findings-shape: yes\|no`; plus user-typed `slash-command` lines | One block per invocation, blank-line separated |
| `edits` | Edit/Write/MultiEdit calls | One per line: `<ts> <type> <basename> (<full-path>)` |
| `assistant-text` | Assistant text blocks chronologically | Useful for spotting catches/reasoning |
| `subagents` | Per-subagent sidecar roster (agentType/model/tokens/tools) + main-vs-subagent token split | Markdown block; **pass the LIVE transcript** (sidecars live beside it) |
| `tool-breakdown` | Tool fingerprint: main-vs-subagent call split + MCP-server grouping | Markdown; MCP table only when an `mcp__` tool fired (**LIVE transcript**) |
| `topic-slug [<git-files>]` | Heuristic topic slug from edit footprint; optional 3rd arg: newline-separated `git diff --name-only` list used as a fallback when tool-edit signal is low (`general`/`src`/`src-tauri` bucket or materially fewer tool edits than changed files) | Single word; falls back to `general`; stderr note when git files drive the slug |
| `line-count` | Sanity check | Single integer |
| `stats [start-iso] [end-iso]` | Session Stats footer — counts + transcript-native `message.usage` token table + per-model split + subagent aggregate (no ccusage, no $ cost) | The §7 BODY (header-less — starts at `**Window:**`; the template owns the `## 7.` heading) |
| `hook-fires` | Guard-hook fire tally from the transcript's `hook_system_message` attachments (each ballast conditional hook's user-visible one-liner) | Markdown block — one line per distinct hook name + count, plus a total; `no hook fires recorded` when none |
| `bundle <transcript> --out <dir>` | One-shot bulk extraction: runs all eight dumps (`user-msgs`, `invocations`, `edits`, `assistant-text`, `subagents`, `tool-breakdown`, `stats`, `hook-fires`) in ONE process, writing each to `<dir>/<name>.md` plus `<dir>/manifest.json` (metrics: `transcript_lines` / `user_turns` / `time_window` / `compacted`); refuses a non-empty `<dir>` it didn't create (no `manifest.json`) | Per-dump status lines + a metrics one-liner (`lines=N user_turns=N compacted=yes\|no window=<min>..<max>`) on stdout; exit 1 if any dump failed, exit 2 on the bounded-write refusal |

Always invoke via the bare `ballast-extract` command (the block above). Do NOT write inline Python heredocs — they trigger permission prompts, and `ballast-extract` is already self-allowed.

## Workflow

**Tiering.** The orchestrator (this session model) runs every CHEAP `scripts/extract.py` call inline — `resolve`, `line-count`, `compact-check`, `drift`, `time-window` (steps 1-3), and `topic-slug` (step 10, an edit-FOOTPRINT scan only, not a full text dump). Batch the post-`resolve` cheap calls into ONE parallel Bash wave (a single message, multiple tool calls), not serial round-trips. Then `bundle` runs inline (step 4, one Bash call): all eight bulk dumps land in files on disk — cheap on context — and any python-env failure surfaces HERE, interactively fixable, before any dispatch. Only the compact bundle metrics + the report leaves' digests reach the session model.

**Bundle home (D3).** Write the bundle to your **session scratchpad** directory (provided in the system prompt) → `<scratchpad>/postmortem-bundle/`. When no scratchpad is available, fall back to `~/.claude/ballast/tmp/<uuid8>/` and delete it after the report commits (step 6 of *After writing the report*). **Never** the plugin dir (it is replaced on update). All paths passed to `ballast-extract` use forward slashes (the allow-hook char class excludes backslash).

**Scaling tiers (TUNING SURFACE).** Pick the tier top-down, first match wins, from the bundle metrics one-liner (step 4) + step-3 in-scope commit count:

| Tier | Condition | Leaves | Report target |
|---|---|---|---|
| HEAVY | `transcript_lines > 2500` OR `compacted` OR multi-purpose window (unrelated in-window commits) | A (may split in two) + B | per-item targets |
| LIGHT | `transcript_lines < 400` AND `user_turns < 8` AND in-scope commits ≤ 2 | none — orchestrator reads the bundle files directly | ≤ 80 lines |
| STANDARD | otherwise | A + B | per-item targets (as today) |

*Thresholds are deliberately tunable* — starting points calibrated against the 2026-07 measurement corpus (the plugin source repo's `.notes/`); adjust the numbers in this table after live runs. **LIGHT is a designed inline path**, selected by this table *before* dispatch: the orchestrator reads the (small) bundle files itself, dispatches no leaves, holds the report to ≤ 80 lines (§2 collapses to roster + one-line notes; §5 only on a direct prior match — grep priors' headers + `- [ ]` lines inline instead of dispatching the scout). It does NOT breach the fail-closed dispatch guard, which targets *silent fallback* in STANDARD/HEAVY. **HEAVY**: leaf A may split into two bundle-file groups — narrative (`user-msgs`/`assistant-text`/`edits` → §1/§3/§4) and tooling (`invocations`/`subagents`/diffs → §2) — plus leaf B, unchanged.

**Authoring split (single source — every per-section note points here).** The leaves *draft from the bundle and the priors*; the orchestrator *authors the judgment* — it alone holds the CLAUDE.md / MEMORY / prior-report context those sections need, which is exactly why they aren't delegated — then *assembles* the report:

| Authored by | Sections |
|---|---|
| **postmortem-extractor** (sonnet — bundle digest) | §1 raw catches · §2(a) roster + per-subagent model/token attribution + `ballast-tooling` raw material + Mode grant(s) · §2(b) dispositions (from the pre-generated diff files) · §2(c) usage gaps · §3 · §4; plus the finding index C/D/G/F/U |
| **postmortem-priors-scout** (haiku — priors retrieval) | each prior report's still-open `- [ ]` §6 recs · raw §1/§4/§5 pattern-candidate citations (retrieval only — no interpretation, no ledger reads) |
| **orchestrator** (session model — judgment + splice) | header BLUF block (Mode · summary · gauge · top-actions) · §1's forward-looking note · §2(a) value glyphs + the delegated-autonomy verdict · §5 authored from the scout's raw material (P ids assigned here) · §6 recs + the prior-open-recs sweep (R ids) · §7 verbatim splice of `stats.md` + `tool-breakdown.md` straight from the bundle |

**First-run agent setup / fail-closed guard.** Both leaves ship with the plugin as flat `agents/postmortem-extractor.md` and `agents/postmortem-priors-scout.md` (plugin agents dir — loaded at session start, available in every project while ballast is enabled), so they're normally already loaded; just dispatch them by name. At **STANDARD/HEAVY**, if either agent fails to resolve — e.g. ballast was only just enabled this session, before its roster loaded — **fail closed**: surface the problem rather than silently running the bulk digest inline (which defeats the cost split), and restart the session so the plugin's agents load before relying on dispatch again. LIGHT's inline path is *designed* (selected by the tier table above), not a fallback — so it never breaches this guard.

### 1. Transcript freshness + size sanity

```
ballast-extract line-count <transcript>
```

If under 50 lines → exit with one-liner ("session too short for meaningful report").

(Steps 1-3 are all cheap inline calls. After `resolve`, batch `line-count` / `compact-check` / `drift` / `time-window` into ONE parallel Bash wave — a single message with multiple tool calls — rather than serial round-trips.)

### 2. Compact awareness (run FIRST after sanity)

```
ballast-extract compact-check <transcript>
```

**Exit 0** — no compaction; proceed on the live transcript.

**Exit 1** — the session was compacted (the printed timestamp is the boundary), but this does **not** mean lost detail: compaction only summarizes the model's in-context window, while the on-disk `projects/<…>.jsonl` is **append-only** — it still holds every pre-compact turn (verified on this CC build: the live file is a strict superset of all PreCompact archives). So **stay on the live transcript as the primary, full-fidelity source for every step below**; just surface a top-of-report note that the session was compacted (live history is complete). The PreCompact archive (archives written by a user-side PreCompact archiver, if configured, to `~/.claude/compact-backups/<ts>_<trigger>_<session>.jsonl`, listed by `resolve` under `ARCHIVES:`) is **fallback insurance only** — make it primary *solely* if the live file is missing/unreadable (then note "recovered from archive `<path>`" in metadata).

⚠️ **Re-verify if CC's transcript behavior changes** — append-only is Claude-Code-version-dependent. If the live file's earliest turn ever sits *after* a compaction boundary, the on-disk file was truncated → fall back to the archive as primary and flag it. (Consequence of the append-only guarantee: running `/session-postmortem` before `/compact` is never required — full history survives on disk.)

**Format-drift canary (cheap, inline — run alongside compact-check).** Also run `ballast-extract drift <transcript>`. It tallies any transcript shape outside scripts/extract.py's known registries (line type / system subtype / attachment type / commandMode / promptSource / origin.kind / leading content-tag / user is*-flag). `no drift` is the common case — do nothing. ANY rows: emit the report's **drift footnote** (tail position — see the template) and append one sighting line to `~/.claude/postmortem/DRIFT-SIGHTINGS.md` (`<date> · <project> · <kind>=<value> ×N`, create the file if absent) — new shapes appear constantly and are rarely notable, and registration is always ballast-repo work (extend-never-delete), so sightings accumulate there for a ballast session to batch-register instead of spamming per-project §6 recs. A §6 rec (and a header caveat) is warranted ONLY when the drift demonstrably affects THIS report — a turn/usage-bearing shape mis-counting §7, or a shape that blocked extraction. Pure diagnostic; it never blocks.

### 3. Time window + commits

```
ballast-extract time-window <transcript>
```

Then bound git log:

```
git log --since="<min-iso>" --until="<max-iso>" --pretty=format:"%h|%ad|%s" --date=iso-strict
```

For the `--commits <hash>..HEAD` form, use `git log <hash>..HEAD --pretty=format:"%h|%ad|%s"` directly. For `--commits <N>`, use `git log -<N>`.

**Multi-purpose session caveat**: long-running sessions sometimes contain unrelated work whose commits fall in the same time window. When a spec is identified (step 7 below), cross-reference each in-window commit against the spec's "Files" table:

```
git diff-tree --no-commit-id --name-only -r <hash>
```

In the report metadata, distinguish:
- **Commits in scope (this session's work)**: commits that touched at least one file in the spec's Files table OR no spec exists
- **Commits in window (unrelated)**: commits in the time window that touched no spec-listed files

Format: `**Commits in scope:** 2 of 3 in window (`<hash1>`, `<hash3>` — touched spec files; `<hash2>` was unrelated)`. This avoids the count-of-N-but-only-M-relevant ambiguity that misleads at a glance.

### 4. Bundle + tier pick

Run the one-shot bulk extraction inline (one Bash call, forward-slash paths):

```
ballast-extract bundle <live-transcript> --out <bundle-dir>
```

`<bundle-dir>` is the bundle home (D3): `<session-scratchpad>/postmortem-bundle/`, or the `~/.claude/ballast/tmp/<uuid8>/` fallback. It writes all eight dump files + `manifest.json` and prints per-dump status lines and a metrics one-liner. Read the **tier** from that metrics one-liner (`transcript_lines` / `user_turns` / `compacted`) + the step-3 in-scope commit count against the **Scaling tiers** table above. Pass the **live** transcript (append-only, full-fidelity even when compacted; the PreCompact archive is used as `<live-transcript>` only in the rare live-missing fallback — the subagent sidecars live beside the live file, so `subagents` / `tool-breakdown` need it).

On a per-dump error (bundle exits 1), fix it interactively — python-env problems surface HERE, before any dispatch — then re-run `bundle` into the SAME dir (its `manifest.json` marks the re-run case, overwrite in place). Or proceed with the intact dumps and note the gap in report metadata.

### 5. Diff pre-generation (drives §2(b))

Grep `<bundle-dir>/invocations.md` for blocks tagged `findings-shape: yes`. For each hit, create `<bundle-dir>/diffs/` (`mkdir -p`) and emit a bounded `git diff` (invocation timestamp → last in-scope hash, same bounds rule as before) to a file:

```
git diff <invocation-ts-bound>..<last-in-scope-hash> > <bundle-dir>/diffs/diff-<n>.txt
```

Pass those file paths to leaf A — it reads the diffs, never runs git. Usually 0–3 files; **zero `findings-shape: yes` invocations → no `diffs/` dir, §2(b) collapses.** (The inline-Skills `findings-shape: no` gotcha — real analysis in `assistant-text` near the invocation timestamp — is covered in §2(b).)

### 6. Idea tracker probe (drives §3)

Probe for an idea tracker or backlog document, which may vary by project-specific conventions:
1. `IDEAS.md` (project root)
2. `notes/IDEAS.md` · `docs/IDEAS.md` · `TODO.md`
3. a project-root `*-backlog.md` (some projects keep a backlog doc instead of an `IDEAS.md`)

If found, diff it across the session's commit range:

```
git diff <first-hash>~..<last-hash> -- <ideas-path>
```

(or `git diff -- <ideas-path>` for uncommitted state). If **no tracker is found**, note "no idea tracker detected" in §3 and fall back to conversational "later / v2 / future / out of scope" mentions only.

**No-tracker nudge (evidence-gated, decided at §6 authoring).** A no-tracker project leaks its deferrals into conversational memory. Emit **one** §6 `Add idea-tracker entry` rec proposing a lightweight `IDEAS.md` (Evidence: the untracked §3 items + the prior no-tracker report) **only when all hold**: no tracker was detected · **≥1 prior consulted report** (step 8) also recorded "none detected" (so the project is active, not first-ever) · §3 lists **≥2 named deferrals** · §3 names no existing tracker substitute. Otherwise skip — don't nudge an empty-§3 session, a first-ever session, or a project whose §3 points to its own backlog (e.g. a meta-project using a `docs/*-backlog.md`). One-time: if a prior report already holds this rec open, the §6 prior-open-recs sweep carries it forward rather than re-emitting.

### 7. Spec / plan probe (topic anchor + cross-reference)

Probe for spec directory, which may vary by project-specific conventions:
1. `.claude/brainstorm/`
2. `.claude/brainstorming/`
3. `docs/superpowers/specs/`
4. `.claude/specs/`
5. `.notes/`

If found, list specs added/modified in the session window:

```
git log --since=<min-iso> --until=<max-iso> --diff-filter=AM --name-only --pretty=format:"" -- <spec-dir> | sort -u
```

The most-modified spec is the topic anchor (overrides the heuristic `topic-slug`). If **no spec dir exists**, fall back to the edit-heuristic slug with no spec cross-reference.

**Plan-promotion check (orchestrator judgment).** If the session executed an approved plan that exists only outside the repo (native plan mode saves to `~/.claude/plans/`; or the plan lived only in-context) AND the plan is decision-bearing — it records design choices, tradeoffs, or reversals a future session or postmortem would otherwise re-derive — promote it now: write it to the project's spec home from the probe list above (default `.notes/<date>-<slug>-plan.md`). Most sessions need no promotion — a plan whose full effect is legible in the diff and report is not decision-bearing. Record the call either way via the `Plan promotion:` metadata line (template).

### 8. Prior post-mortem reports (drives §5 AND the §6 prior-open-recs sweep)

```
ls -lat .claude/postmortem/*.md | head -10
```

Read the **most-recent ≤8 reports** for this project (by report sequence — **no wall-clock age gate**, so an idle/stable project resumed later still gets its recent history), **plus any older report _relevant_ to this session — its topic-slug OR its `Spec/plan referenced:` path (step 7) matches this session's** (relevance, not just recency — a *return-to-failed-work* session's original prior is often >8 reports back, and a renamed feature drifts the slug while the spec path still matches, so spec-path is the higher-precision signal; both are a cheap grep over prior-report headers — no transcript archaeology), to a **hard ceiling of 12 total**. Skip §5 entirely if fewer than 2 priors are in that set. These same priors are the input to the **`postmortem-priors-scout`** leaf (step 9), which returns each prior's still-open `- [ ]` recs (file + `R<n>` + one-line text) plus raw §1/§4/§5 pattern-candidate citations — feeding both the **§6 prior-open-recs sweep** (orchestrator flips/carries/drops each — see §6's living-tracker lifecycle) and the orchestrator-authored §5. At LIGHT no scout is dispatched — grep the priors' headers + `- [ ]` lines inline instead.

**Harness-scoped recs consult a wider ledger:** for a prior open rec whose target is the harness itself (a plugin skill/hook/agent, or global config), probe `~/.claude/postmortem/HARNESS-RECS.md` — a user-side ledger of cross-project chain dispositions. A chain the ledger records as closed / superseded / parked is marked **resolved-by-supersession** (cite the ledger line), not re-carried: project-local reports cannot otherwise see closures that happened in the harness's own repo.

### 9. Dispatch the leaves (STANDARD/HEAVY)

**At LIGHT, do NOT dispatch** — the orchestrator reads the (small) bundle files directly (roster + one-line notes, §5 only on a direct prior match). For STANDARD/HEAVY, dispatch **both leaves in ONE message, in parallel** (single message, two Task calls) — steps 4–8 have already produced every input:

- **`postmortem-extractor`** (sonnet) — pass: the **bundle dir + manifest path**, the **in-scope vs in-window-unrelated commit lists** (with first/last in-scope hash), the **spec path**, the **idea-tracker diff** (step 6's diff text or a path to it; "none" if no tracker — the extractor has no git access and cannot derive it), the **pre-generated diff file paths** (step 5), and the **compacted flag**. Do NOT pass the prior-report paths, CLAUDE.md, or MEMORY.md — those stay with the orchestrator (§6 homing, §1's forward-looking note) or go to the scout. It returns §1 · §2(a) roster (with inline per-subagent model/token attribution + main-vs-subagent split line) · §2(b) dispositions (from the diff FILES — it never runs git) · §2(c) gaps · §3 · §4, the Mode grant(s) (`autopilot`/`freehand`/`ultracode` — typed in `user-msgs`, set via a command's stdout, or hook-injected; else `normal`), and the finding index C/D/G/F/U.
- **`postmortem-priors-scout`** (haiku) — pass: the **≤12 prior-report paths** (step 8) and nothing else. It returns each prior's still-open `- [ ]` §6 recs + raw §1/§4/§5 pattern-candidate citations. Retrieval only.

Neither leaf runs any command — both are read-only (extractor: `Read, Glob` over the bundle files + diffs; scout: `Read, Glob, Grep` over the prior reports). The bundle already holds every dump (`subagents.md` / `tool-breakdown.md` included), so the extractor never invokes `ballast-extract` or git. (The per-section letter scheme lives once in the extractor's *Finding index*; `R#`/`P#` stay the orchestrator's, assigned in §6/§5.)

**Partial-tolerance (SR-6).** Each leaf ends its return with a `SECTIONS-RETURNED:` line listing what it produced. A missing section → re-dispatch THAT leaf for **only** the missing sections against the SAME bundle dir (never re-extract; never rewrite a complete report as if partial). A dead/failed leaf → re-dispatch once with the same inputs; still failing → note the gap in report metadata rather than silently inlining the bulk digest (the fail-closed guard). Hold both digests for assembly; cite their IDs in §5/§6.

**§7 stats splice (from the bundle, no leaf).** §7 is assembled by the orchestrator directly from the bundle: read `<bundle-dir>/stats.md` then `<bundle-dir>/tool-breakdown.md` and splice BOTH verbatim under the §7 heading ("Session Stats & Tool Fingerprint") — do NOT rephrase or reformat. Both were produced by `bundle` on the **live** transcript (full fidelity, retains all turns even post-compaction; `subagents`/`tool-breakdown` resolve the sidecars because they run on the live path). The token table reconciles with §2(a)'s per-subagent split (same source). No $ cost tracking — a sibling parser project owns cost analysis.

### 10. Topic slug + filename

Assemble the git-changed-files list from the in-scope commit range (step 3; omit when no commits are in scope):

```
git diff --name-only <first-hash>~..<last-hash>
```

Then invoke (pass the changed-files list as a quoted 3rd arg):

```
ballast-extract topic-slug <transcript> "$changed"
```

`$changed` holds the `git diff --name-only` output (newline-separated, **quoted** so it arrives as one argv element) — in PowerShell: `$changed = git diff --name-only <first-hash>~..<last-hash>`; in Bash: `changed=$(git diff --name-only <first-hash>~..<last-hash>)`. The script falls back to git filenames only when the tool-edit footprint is low-signal (`general`/`src`/`src-tauri` bucket or materially fewer tool edits than changed files) — patching the blind spot where a bulk PowerShell/Bash change leaves no Edit/Write trace. Stderr carries a one-line note when git files drive the slug; stdout is always a single word.

**Override the script result if** a spec was added/modified during the session (step 7) — use that spec's slug (strip date prefix and `-design` suffix). The spec is the strongest topic anchor.

**If the script returns a generic source bucket** (`src`, `src-tauri`, `general`) and no spec anchor applies, derive the slug from the in-scope commit subject instead (the topic-bearing words, hyphenated) — the path heuristic is unhelpful for backend-only or cross-cutting sessions.

**Filename date**: use the date of the **last commit in scope** (from §3's `git log`, most recent commit's date in `YYYY-MM-DD`, local time). For sessions with no commits in scope, fall back to today.

Final filename:

```
.claude/postmortem/YYYY-MM-DD-<topic-slug>-<uuid8>.md
```

Where `<uuid8>` = first 8 chars of the session transcript UUID.

## Report path & idempotency

Create `.claude/postmortem/` (under the project root) if it doesn't exist. If the target file already exists:
1. Read it first
2. Preserve any content below a `<!-- USER NOTES BELOW -->` marker line
3. Regenerate everything above the marker
4. If no marker exists, just overwrite

## Report structure

Output exactly these 7 sections in order. Use the Write tool to create/overwrite the report file.

The header's **`**Schema version:**`** marks the report *format* it was produced with (the live value is in `${CLAUDE_SKILL_DIR}/references/report-template.md`; `${CLAUDE_SKILL_DIR}/references/CHANGELOG.md` tracks the history), so a stale-format report is spottable at a glance. Bump it — and record the change in `${CLAUDE_SKILL_DIR}/references/CHANGELOG.md` — when the report's **section structure** changes (a `##` section added/removed/renamed, a header field added/removed/renamed, or a field's machine-readable format changed); do NOT bump for prose rewording. Reports with no version line predate the system and read as `v0`.

**Lockstep sites — update together on a structure / finding-index change** (so a bump is a checklist, not silent drift — the gap that let a stale finding-index survive a past edit): the template in `${CLAUDE_SKILL_DIR}/references/report-template.md` (+ the at-a-glance section map under SKILL.md's `## Report structure`) · **both leaf agents' return contracts** — the extractor's *What you return* + *Finding index* and the priors-scout's return shape · the small extractor-generated token-sets (§1 mechanism · §4 label · §2(b) disposition · the Mode keyword set) · the §7 stats-splice contract (now file-based: bundle `stats.md`/`tool-breakdown.md` ⇄ step-9 splice ⇄ template §7; the table *format* itself is owned by `scripts/extract.py`'s `stats`) · the SR-6 partial-tolerance contract (the `SECTIONS-RETURNED:` line + missing-section re-dispatch rule — stated at step 9 and restated in both leaf agents' bodies). Those token-sets stay duplicated by design — the extractor must *generate* them, and a pointer it can't resolve can't generate from. Everything else single-sources: the finding-index letters live once (at the extractor's *Finding index*) and SKILL.md points.

The full fillable skeleton — every section's exact fields, shape, and per-section authoring cues — lives in **[`${CLAUDE_SKILL_DIR}/references/report-template.md`](${CLAUDE_SKILL_DIR}/references/report-template.md)**. **Read that file now and follow it verbatim; do NOT reconstruct the report structure from memory.** (It is also the self-contained hand-off for a future report-writer subagent.)

The seven sections at a glance (schema **v4**) — the template file is authoritative for each:
1. **Mid-Implementation Catches** (`C<n>`) — caught + fixed before final verification.
2. **Subagent & Tooling Evaluation** — **(a) Roster & ballast tooling**: roster lines (value-glyph slot) with inline per-subagent `— <model> · <tokens> tok · <calls> calls` attribution + a main-vs-subagent token-split line, then a `ballast-tooling` subsection (skills/agents/guard-hook fires this session, and — iff Mode includes an autonomy grant — the compressed delegated-autonomy verdict) · **(b) Disposition** `D<n>` · **(c) Usage-gap** `G<n>`.
3. **Deferred Follow-ups** (`F<n>`) — later / v2 / out-of-scope items.
4. **Post-Implementation User Corrections** (`U<n>`).
5. **Cross-Session Patterns** (`P<n>`) — orchestrator-assigned from the scout's raw citations.
6. **Recommended Actions** (`R<n>`) — evidence-tied living-tracker checkboxes.
7. **Session Stats & Tool Fingerprint** — bundle `stats.md` then `tool-breakdown.md`, both verbatim, then the conditional drift footnote.

## After writing the report

1. **Assemble + author the orchestrator sections + terseness pass.** Splice the leaves' digests (extractor + scout) together with the orchestrator's judgment sections (*Authoring split*), author **§5** from the scout's raw pattern-candidate citations (assign the `P<n>` ids here; the ≥2-occurrence bar and shape live in the template), splice **§7** from the bundle (step 9), and author the **header BLUF block**:
   - **Mode** — every mode GRANT the extractor reports, across two independent axes: autonomy (`autopilot`/`freehand`) → the §2(a) autonomy-verdict trigger, and fanout (`ultracode`, not an autonomy grant). A grant is evidence of *effective* mode, not just a typed keyword — three transcript-visible signal classes count: (1) a keyword typed in `user-msgs`; (2) a mode set via a command's own stdout (e.g. bare `/effort` → "Set effort level to ultracode…"); (3) the harness's own injected autonomy context (the freehand-mode hook's leading `FREEHAND / AUTOPILOT` marker line — never the hook's per-prompt `MODE MIRROR` standing reminder, which evidences an already-standing grant, not a fresh one). Report every signal that fired, annotating non-typed ones with provenance (e.g. `autopilot — hook-armed 14:15Z, quoted-sample text`); a hook injection contradicted by every other signal (no typed keyword, no delegation intent in the user's framing) is a FALSE-ARM — note it on the Mode line without flipping the mode; `normal` requires no genuine grant on either axis (a noted false-arm doesn't disqualify it).
   - **Session summary** — 1–4 sentences (scaling with complexity) of what the transcript actually covered, incl. adjacent issues / drift / prior-session misses raised ad hoc. This IS the human-facing summary — authored once, written into the report (it replaces the old console-only print).
   - **Overall gauge** 🟢/🟡/🔴 — a **judgment roll-up of the verdicts you already wrote, NOT a computed self-catch%/reversal score** (a clean-by-counters session can still be a repudiation): 🟢 clean (high self-catch, no non-subjective §4 correction, no reversal, contract honored) · 🟡 friction (a non-subjective §4 correction OR a §2(c) usage-gap OR a same-session reversal — quality held, something needed rework) · 🔴 repudiation (an architectural/deferred reversal OR a deliverable-shape rejection — the expensive class the §2(a) autonomy verdict names). On a two-level toss-up, let the §2(a) autonomy verdict + the presence of §4 structural/missed-req corrections decide. Cap at these 3 levels.
   - **Top actions BLUF** — a pointer index (rec title + `[→R<n>]`) into §6; no rec text, no second checkbox.
   Run the §6 **prior-open-recs sweep + escalation** here too (it needs the priors). Then hold the whole report to the durable-docs terseness bar: cut every line that won't change a future decision — incident narration, hedges, restated findings; tighten §1/§4 toward one line — a **per-item target, not a per-section ceiling**: a many-finding session earns proportionally more length (one *informative* line per real item — track the item count, not token spend or duration), a thin one stays near the structural minimum. Confirm §6 recs are class-level. Trim, don't pad. (The report is a durable artifact — read later by §5; it generates durable-doc edits.)
2. **Self-check against report invariants** (cheap, before any output or commit; fix any failure first):
   - `**Schema version:**` line present and current (live value in `${CLAUDE_SKILL_DIR}/references/report-template.md`; CHANGELOG tracks bumps).
   - Header BLUF present: `Mode`, the `Session:` gauge + summary, and a Top-actions BLUF whose `[→R<n>]` pointers each resolve to a real §6 rec (or "none recommended").
   - Every §6 rec carries a stable `R<n>` id + an `Evidence:` citation (§6 Discipline: "Recommendations that can't cite evidence are dropped").
   - Every non-subjective §4 item is reflected in §6 OR carries an explicit "no action because…" note (§6 Discipline).
   - The §2(a) `ballast-tooling` autonomy verdict present iff Mode *includes* an autonomy grant `autopilot` or `freehand` (`ultracode` alone never triggers it — a co-present `ultracode` doesn't suppress it either; omit only when neither autonomy keyword fired — `normal`, or `ultracode` alone); if priors exist, the prior-open-recs sweep ran (each prior open rec flipped / carried / dropped).
   - LIGHT-tier report ≤ ~80 lines (§2 collapsed to roster + one-line notes).
   - No padded sections: §1–§5 with nothing to report say so in ≤1 line (Common mistakes: "Padding empty sections").
   - **Length tracks substance**: if trimming a line would make two genuinely different catches/corrections indistinguishable, it earns its place; conversely a thin session isn't expanded past its real findings.
   This verifies the contract the rest of the file *states* — point at those rules, don't re-derive them.
3. Print the relative report path so the user can find it.
4. Print a short pointer — the gauge + the top 1–2 actions (the in-report **Session** summary already holds the narrative; don't re-author a separate console summary).
5. **Apply per the `--fix` grant; propose everything else** (the §6 *Auto-apply boundary*). **With no `--fix` (the default), auto-apply nothing — every rec is emitted `- [ ]`, proposed.** For each §6 rec under a grant, act by its home:
   - **Auto-apply** a rec whose home class the grant names — `memory` (project memory) or `ideas` (idea-tracker / IDEAS) — *only* when it clears the durable-docs 6-gate and is clearly warranted: make the edit, then flip its `- [ ]` to `- [x]` (citing the edit; step 6 handles any commit). These are append-style, reversible, data-tier homes — the only classes a grant can ever name. **One carve-out:** a rec to *adopt* a tracker where none exists — the no-tracker nudge — stays **propose-only** (`- [ ]`) even under a grant; first-time file creation is a setup act, not a reversible append into an existing data tier. **A second carve-out:** a rec whose content is a root-cause *diagnosis* from an investigation still open at session end stays **propose-only** — an unconfirmed diagnosis is a volatile claim, not a settled fact, and auto-applying it freezes a hypothesis as memory; apply only once a later session (or the user) confirms it.
   - **Propose only** — leave `- [ ]`, await explicit direction — for every rec touching **CLAUDE.md, a hook, `settings.json`, or code** (behavior / permissions / executable surface); these classes are **never grantable via `--fix`** — no argument value covers them. A plugin-shipped hook or skill additionally routes cross-repo per the template's plugin-provenance rule — a consuming project cannot edit it. The skill never edits these on its own initiative.
   - Flipping `[x]` a rec already implemented this session (by you or the user) is the close-the-loop case (§6 *Within-session close*), not new application.
   When a rec's home or warrant is unclear, leave it proposed.
6. **Commit the report by path** — it is the skill's own deterministic output; leaving it uncommitted strands a closed-session artifact for the user to clean up later. Three preconditions first. **Runtime stamp:** append the one-line `**Postmortem run:**` stat to §7 (format owned by the template) — one inline `date -u` call, delta'd against the invocation timestamp already sitting in the bundle's `user-msgs.md` (the skill's own slash-command entry). No agent, no dispatch — the computation is smaller than any dispatch overhead; stamping here (post-`--fix`, pre-commit) puts the stat inside the committed report and consistently excludes the commit + wrap-up tail. **Scrub:** the report quotes user turns verbatim — scan the report content itself for secrets, tokens, and PII before committing (redact or flag; don't rely on the commit-time guard reminder alone). **Standing rules win:** the auto-commit never overrides the user's or project's own rules — if a standing instruction (a CLAUDE.md) prohibits autonomous commits, or permission config gates `git commit` to deny/ask, defer to it: leave the report written-but-uncommitted (or let the native ask fire) and say so, rather than routing around the gate. Then: resolve the git root, confirm the branch, then `git add` + `git commit` **by path** the report, plus: any same-session §6 checkbox flips, the **prior-report back-flips from the sweep** (those touch OTHER report files — add each by its own path), and any **repo-tracked auto-applied edits from step 5** — an idea-tracker / IDEAS file (add by path). Commit *only* those paths — never `-A` — so a concurrent session's WIP stays untouched. The report quotes user turns, so the normal **secret-scan** / `git-commit-guard` reminder applies. (A memory rec written in step 5 lives under `~/.claude/`, outside the project repo and untracked by default → not committed by the skill; memory versioning is the user's own setup.) **Bundle cleanup:** once the report has committed, if the bundle used the `~/.claude/ballast/tmp/<uuid8>/` fallback home (no scratchpad was available), delete that dir — the scratchpad home needs no cleanup (it dies with the session).
7. **Fold same-session reactions, then re-commit.** If the user reacts to the report in-session — requests for action, pushback on wording, an issue noticed only *after* invoking (so it never reached the transcript or the report), or commentary — fold it in **without being asked each time**: a reaction about a specific finding edits that section (or annotates it); freeform/meta commentary goes under `<!-- USER NOTES BELOW -->`; a requested checkbox flip follows §6's living-tracker `[x]` rule. Then re-commit the report by path through step 6's hygiene, batching a reaction into **one** follow-up commit rather than committing per message.

## Edge cases

| Scenario | Behavior |
|---|---|
| Transcript file missing | Exit with hint to list available transcripts via `ls -lat` |
| Session UUID not in context | Fall back to cwd-encoded newest, then mtime-newest with a noted caveat — never fabricate |
| Session was compacted | Stay on the **live** transcript — it's append-only, so it retains full pre-compact history; surface a compact note. Use a PreCompact archive as primary only if the live file is missing/unreadable |
| `--commits <N>` larger than commit count | Analyze all available commits, note count discrepancy in metadata |
| `--commits <hash>..HEAD` resolves to 0 commits | Analyze conversation only; metadata says "no commits in scope"; filename date falls back to today |
| No findings-producing invocations | No `findings-shape: yes` blocks → no `diffs/` dir emitted; §2(b) collapses — roster-only; §2(c) may note a verification gap if §4 has misses |
| No subagents dispatched | §2(a) per-subagent attribution collapses to one line; §7's tool fingerprint shows the main-thread split only |
| `bundle` reports a per-dump error (exit 1) | Proceed with the intact dumps + a report-metadata note — OR fix the env and re-run `bundle` into the SAME dir (its `manifest.json` marker allows the overwrite) |
| Leaf dies / returns partial mid-dispatch | Re-dispatch once against the SAME bundle dir (never re-extract); still failing → note the gap in metadata rather than inlining the digest (fail-closed guard). A missing section → re-dispatch only that section (SR-6) |
| No scratchpad dir available | Bundle falls back to `~/.claude/ballast/tmp/<uuid8>/`; delete it after the report commits |
| Session under 50 transcript lines | Exit with one-liner — too short for meaningful report |
| `.claude/postmortem/` doesn't exist | Create it on first write |
| Report file exists with same name | Preserve content below `<!-- USER NOTES BELOW -->`, regenerate above |
| First-ever post-mortem | Skip §5 (no priors); §6 still works from current-session evidence |
| No idea tracker / no spec dir | §3 notes "no idea tracker detected"; topic anchor falls back to the edit heuristic |

## Common mistakes

- **Padding empty sections.** If §1-§5 have nothing to report, say so in one line — don't fabricate findings to fill space.
- **Recommending bug fixes.** §6 is for workflow/documentation improvements, not "fix the unfixed finding from §2." A workflow rec would be "refine the review check that missed this class," not "fix this specific instance."
- **Applying a recommendation outside the grant.** Auto-apply requires an explicit `--fix` grant and is limited to the memory / idea-tracker classes that clear the durable-docs gate (§6 *Auto-apply boundary*); with no `--fix`, apply nothing. Never edit CLAUDE.md, a hook, `settings.json`, or code on the skill's own initiative — those classes are ungrantable and stay proposed.
- **Bounding commits with mismatched timezones.** Transcript timestamps are UTC (Z suffix). Use `--date=iso-strict` on git log.
- **Wrapping, aliasing, or prefixing `ballast-extract`.** Invoke it bare and alone — `ballast-extract <subcommand> <transcript>`, no `python`/`py` prefix, no shell chaining (`;`/`&&`/`|`), no substitution or redirection. This includes `bundle`: its dumps land in files via the `--out <dir>` flag, NOT via a shell `>` redirect (which would break the self-allow match). `ballast-allow.py`'s self-allow match is a strict regex on that exact single-command shape; wrapping it in anything else drops back to a normal permission prompt.
- **Backslash paths to `ballast-extract`.** Every path argument (transcript, `--out <dir>`) uses forward slashes — `ballast-allow.py`'s argument char class excludes backslash, so a `C:\…` path silently drops to a manual prompt even with the self-allow marker set.
- **Forcing a §2(b) disposition table for non-findings invocations.** Only build the table when the `invocations` block is tagged `findings-shape: yes` (or you can see findings in `assistant-text` for an inline skill). Exploratory `Explore`/`general-purpose` agents and brainstorming/planning skills stay in the roster only.
