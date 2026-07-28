# Session Post-Mortem — report template

The authoritative fillable skeleton for a `session-postmortem` report — every section's exact fields, shape, and per-section authoring cues. Pointed to from `SKILL.md`'s **`## Report structure`** (which owns the schema-bump rule + the lockstep-sites discipline; `references/CHANGELOG.md` owns schema history). Output **exactly** these 7 sections, in order, in this structure: fill the `<...>` placeholders; keep the field labels and `##` headings verbatim. The orchestrator authors the judgment sections (the `Session:` gauge, §2(a)'s value glyphs + the delegated-autonomy verdict, §5 from the priors-scout's raw citations with orchestrator-assigned `P` ids, and §6) — see the *Authoring split* in SKILL.md; a future report-writer subagent is handed this file plus the extractor digest and that context.

```markdown
# Session Post-Mortem — <topic>

**Schema version:** v4
**Generated:** YYYY-MM-DD
**Mode:** <all granted modes, space-joined; non-typed grants annotated with provenance> — two INDEPENDENT axes: autonomy (`autopilot`/`freehand`) → the §2(a) ballast-tooling autonomy-verdict trigger · fanout (`ultracode`); a grant counts however it arrived — typed keyword, command stdout, or hook-injected context — or `normal` if no genuine grant fired on either axis (a hook false-arm is noted inline, not a mode flip)
**Transcript:** <uuid>
**Commits in scope:** <N of M in window> (`<first-hash>..<last-hash>`) OR "no commits"
  — when M > N, list which hashes are unrelated and what they were about
**Spec/plan referenced:** `<path>` OR "none" (probed `.claude/brainstorm/`, `.claude/brainstorming/`, `docs/superpowers/specs/`, `.claude/specs/`, `.notes/`)
[**Plan promotion:** promoted → `<path>` | not needed — <one-clause reason> — orchestrator-authored judgment call (SKILL.md step 7); omitted entirely when the session executed no approved plan]
**Idea tracker:** `<path>` OR "none detected"
**Prior reports consulted:** <count> (<oldest-date> .. <newest-date>) OR "none"

[⚠️ Compact note if applicable — "session compacted N× — live transcript retains full pre-compact history (append-only), analyzed in full" OR, only in the fallback where the live file was missing, "recovered from archive `<path>`"]
[⚠️ Transcript-resolution caveat if the session was guessed by mtime]
[⚠️ Format-drift caveat here ONLY if the drift demonstrably affects this report (a turn/usage-bearing shape mis-counting §7, or blocked extraction) — that case also earns a §6 rec. An ordinary new-shape sighting is NOT a header caveat or a rec: it goes in the tail drift footnote (below) + `~/.claude/postmortem/DRIFT-SIGHTINGS.md`.]

---

**Session:** <🟢|🟡|🔴 gauge> — <1–4 sentence summary of what the transcript actually covered, scaling with complexity: the focus AND the adjacent issues / drift / prior-session misses raised ad hoc that the topic slug and section structure miss>

**Top actions (BLUF):**
- <rec title> [→R1]
- <rec title> [→R2]

_(the BLUF lines are a POINTER index into §6 — title + stable id only, no rec text or checkbox; §6 is the authority + living tracker. "None recommended this session." if §6 is empty.)_

> **Indicators** — value 🟢 healthy/load-bearing · 🟡 marginal · 🔴 problem/wasted (per §2(a) roster item). The **Session:** line is the same three glyphs at whole-report scope, but a *judgment* roll-up — NOT a mechanical average of the item glyphs (rubric in *After writing the report*, step 1). §6 status: `[ ]` open · `[x]` done (+ resolving commit) · `🔴 ESCALATED` (carried open ≥2 reports **with fresh evidence** — a new occurrence or live exercise in the carrying session; a bare keepalive carry does not count) · `— dormant` (carried with no fresh evidence; never escalates) · `↗ SUPERSEDED` (fix evolved — points to the current rec).

## 1. Mid-Implementation Catches

What got caught and fixed BEFORE final verification. Each item leads with its stable id and a
**normalized mechanism token** (pick ONE consistent rendering — `[self-caught]`/`[user-flagged]`
then the how — so §5 can mine §1 across reports), then the one-liner:
- **`C<n>` `[self-caught|user-flagged]` `<mechanism>`** — `<one-line issue + how it was resolved>`
  - `<mechanism>` ∈ {subagent-finding, log, edit-re-read, user-mid-stream, test, review} — extend only for a genuinely new how.

For clean items (sessions with no real catches), add a **forward-looking note** ONLY if anchored
to specific evidence — an existing memory entry, prior post-mortem report, or known pattern in
CLAUDE.md. Cite the source. Do NOT speculate from nothing. _(orchestrator-authored — the raw catches above are the extractor's; see Authoring split.)_

Close the section with a one-line take: high self-catch ratio = healthy; high user-flagged
ratio = something the agent should have caught and didn't (feeds §6).

## 2. Subagent & Tooling Evaluation

Driven by the bundle's extract files — `invocations.md` (model-invoked `Skill`/`Agent`/`Workflow`
calls + user-typed `/slash` commands), `subagents.md` (the per-agent sidecar roster with token/tier
attribution), and `tool-breakdown.md` (the session's tool fingerprint + MCP-server grouping — spliced
in §7) — cross-referenced with `assistant-text` for inline skills. A `Workflow` (multi-agent
orchestration — the main review/analysis vehicle under ultracode) shows in the roster via its launch
message; its actual findings arrive later when the background task completes — read the
**`assistant-text`** near the task-notification timestamp for them (the task-notification itself
arrives as an `attachment` line or a non-meta `user` line tagged `origin.kind: "task-notification"` —
never a human turn, and not emitted by any subcommand), so use that assistant-text for the §2(b)
disposition table.

### (a) Roster & ballast tooling

Every skill/agent invocation this session, one line each, **led by a value glyph** (the shared
legend's health axis — the per-item evaluation the raw roster lacks):

- `<🟢|🟡|🔴>` `<name>` (at HH:MM, model-invoked | user-typed) — <purpose> → <one-line outcome>

The glyph is the header legend's value axis, read for a roster line as: 🟢 = caught something / did
real work · 🔴 = wasted or mis-tiered. Orchestrator-added in assembly (see Authoring split); keep
the verdict to the glyph, never a sentence. Include
everything: plugin skills
(`superpowers:brainstorming`, `code-review`, `code-simplifier`), subagents (`Explore`,
`general-purpose`), multi-agent `Workflow` runs, and user-typed slash commands. This is the usage
fingerprint of the session.

**Subagent roster lines carry inline attribution** — append `— <model> · <tokens> tok · <calls> calls`
(source: the bundle's `subagents.md`, the per-agent sidecar roster: agentType · dispatch label ·
model tier · token volume · tool calls, nested dispatches marked). The dispatch label is what
disambiguates same-agentType rows (e.g. N× plan-executor) — fold it into the roster line's `<purpose>`
slot rather than dropping it. Close the roster with one **main-vs-subagent token-split line** (the
fanout spend §7 breaks out as its subagent-aggregate line — the two reconcile, same transcript-native
source). Collapses to no attribution / no split line when no subagents ran (`subagents.md` says so).

Then a **ballast-tooling** sub-block: the full ballast roster the session touched — skills, agents,
and guard-hook fires (source: the bundle's `hook-fires.md`, one line per hook with its fire count).
_(Future home of the backlogged hook-effectiveness facet.)_

**Delegated-autonomy verdict** — _(orchestrator-authored — the extractor only reports the Mode
grant(s); see Authoring split.)_ Include this iff the header **`Mode:`** *includes* an autonomy
GRANT — `autopilot` or `freehand`, however it arrived (typed keyword, command stdout, or the
freehand-mode hook's own injected `FREEHAND / AUTOPILOT` marker line — the hook ships from the
ballast plugin, not this project; never the hook's per-prompt `MODE MIRROR` standing reminder,
which evidences an already-standing grant, not a fresh one) — **independent of a co-present
`ultracode`** (fanout is an orthogonal axis, not an autonomy
grant, so it never suppresses this verdict). Omit only when no autonomy grant fired (`normal`, or
`ultracode` alone). (Gating on the deterministic Mode field avoids re-deriving the verdict from a raw
keyword/text scan.) If the grant was **late** (it appears mid-session, not turn 1) or **non-typed**
(command-stdout or hook-injected), note the grant time and provenance in the opening line.

**Then pick a path — compress the settled case, expand only on deviation:**

→ **DEVIATION path (full rubric).** Fire it if **ANY** of these holds — reversal count alone does
NOT decide it (a 0-reversal session with a wrong-but-unreversed call still gets the full write-up):
- **≥1 reversal** the user made of an autonomous call (cross-ref §4) — *same-session* OR an
  *identified deferred* one (this session undoing a **prior** session's call, often surfacing while
  testing unrelated work — a real reversal the in-session count misses; never certify a streak
  "settled" on same-session evidence alone).
- **A flawed premise** named: an autonomous call justified by a stated constraint ("did X not Y
  because Y needs heavyweight path Z") whose constraint isn't actually TRUE (a lighter path already
  exists). A decision can be unreversed yet wrong — flag any premise you can't confirm.
- **An unspent checkpoint** on a task-defining fork (the mode permits one question for a high-cost
  50/50 fork; a fork that should have surfaced a choosable artifact but didn't).
- **A contract element missed** (BLUF summary not delivered; brainstorming wrongly run/skipped;
  quality verification skipped — the autonomy is in design choices, not the quality bar).
  For each, characterize it (taste/cosmetic vs architectural — the expensive class the mode risks).

→ **CLEAN path (1–2 lines).** None of the above → don't re-derive the standing verdict:
- line 1 — the one-line verdict (delegation ≥ an iterative spec? — the standing conclusion, stated once);
- line 2 — `Premium: <main/subagent token split + per-agent tiers from the roster attribution above> — <tier-match in one phrase>`.
  If no prior-session cross-check was available, append `Deferred reversals not yet checked.`

Both paths keep the verdict + premium (§5 mines them for the cross-session trend).

### (b) Disposition table — findings-producing invocations only

For each invocation whose `invocations.md` block is tagged `findings-shape: yes` (a structured
findings report — code review, audit, lint-style output), build a disposition table:

| # | Severity | File:line | Finding | Disposition | Reasoning |
|---|---|---|---|---|---|
| 1 | issue | foo.py:45 | Brief description | fixed | (or quoted user dismissal) |
| 2 | suggestion | bar.py:12 | Brief description | dismissed | "user said: not worth churn" |

**Heavyweight disposition**: the diffs arrive as **pre-generated files in the bundle**
(`diffs/diff-<n>.txt`, one per findings-shape invocation — each bounded invocation-timestamp → last
in-scope commit, emitted by the orchestrator; this section never runs git). For each finding, read its
diff file: changed in a way that addresses it → **fixed**; changed but not addressed → **partial**;
untouched → **dismissed** (quote the dismissal reason) or **deferred** (if explicitly punted).

For inline skills whose result was only "Launching skill: …" (tagged `findings-shape: no`) but
which you can see produced findings in `assistant-text`, build the table from that assistant-text
analysis near the invocation timestamp.

Skip the table for invocations with no findings — the roster line in (a) is enough. Zero
findings-shape invocations → no diffs dir, and this subsection collapses.

### (c) Usage-gap note

Skills that arguably *should* have run but didn't — anchored to concrete §4 evidence only; lead each
with its `G<n>` id. E.g. a missed-requirement correction in §4 in a session where no review/verification
skill ran, or a spec gap where brainstorming was skipped. Evidence-tied; no speculation. If nothing
qualifies, omit this subsection.

## 3. Deferred Follow-ups

Lead each item with its `F<n>` id (so §6 can cite `§3 F1`):
- New idea-tracker entries added this session (from the step-6 idea-tracker git diff), if a tracker exists
- Conversational mentions of "later," "v2," "future," "out of scope" — quote briefly with the in-context reason
- Items explicitly listed under "Out of scope" / "Future" in any spec/plan touched this session
- If no idea tracker was detected, say so here.

## 4. Post-Implementation User Corrections

User messages after the verification step that requested changes. Each leads with its stable id and
**ONE normalized label** (a single consistent token, so §5/§6 can mine §4 across reports), then a
lightweight description:
- **`U<n>` `[subjective | missed-req | spec-ambiguity | structural]`** — <description> + the verbatim user quote
  - *subjective* = taste/preference · *missed-req* = spec gap · *spec-ambiguity* = interpretation diverged · *structural* = real bug or pattern miss.

The non-subjective ones (missed-req / spec-ambiguity / structural) feed §6.

## 5. Cross-Session Patterns

(Skip this section entirely if fewer than 2 prior reports are in scope — see step 8 for the selection rule.)

Recurring friction across recent post-mortems. Each pattern leads with its stable id (`P<n>`):
- One-line description of the recurring issue
- Frequency: "in N of last M reports"
- Citations: list the prior report filenames showing it

**Require ≥2 occurrences** in the consulted reports to flag a pattern (durable-docs gate 1, RECURRENCE). A
single instance is not a pattern — flagging it as one inflates noise.

**Compress the settled ones.** A pattern with ≥3 prior occurrences and a "no action / installed
practice" conclusion says nothing new each time — collapse it to one line:
`P<n> ESTABLISHED — <[[memory-key]] or prior-report citations>; +1 this session.` Reserve full
narration for patterns that are new, escalating, stalled, or driving a §6 rec (those need the anchor);
a sub-threshold n=1 observation is one inline sentence, not a full entry.

## 6. Recommended Actions

_(Orchestrator-authored — the judgment tier; see Authoring split. Cite the stable finding ids in each Evidence line, e.g. `Evidence: §1 C2, §4 U1, §5 P1` (the per-section letter scheme is the *Finding index* — `C/D/G/F/U` from the extractor leaf, `P` orchestrator-assigned in §5); §6 recs get an **`R<n>`**, assigned here. The extractor returns each §4 correction's label WITH its verbatim quote, so sanity-check a borderline label against the quote before deciding whether a non-subjective item drives a rec here.)_

Concrete suggestions tied to evidence in earlier sections, emitted as `- [ ]` / `- [x]` checkboxes.

**Auto-apply boundary.** The skill may *apply* a recommendation only under an explicit **`--fix` grant** naming its home class (no `--fix` — the default — applies nothing), only when that home is **project memory** or the **idea-tracker / IDEAS** — append-style, reversible, data-tier — and only when the rec clears the durable-docs 6-gate; it is then applied and flipped `- [x]`. (Carve-out: a rec to *adopt* a tracker where none exists — the no-tracker nudge — stays proposed `- [ ]`; first-time file creation is a setup act, not a reversible append.) (Commit differs by home — see After-writing step 6: an idea-tracker edit is in the project repo → committed with the report; **project memory lives under `~/.claude/`, untracked by default → written, not committed by the skill**.) Every rec touching **CLAUDE.md, a hook, `settings.json`, or code** (behavior / permissions / executable surface) is **proposed only** — emitted `- [ ]`, never edited by the skill, awaiting explicit direction. (Applying within the bounded class is the autonomous close-out; the older blanket "never touch another file" rule is retired in its favor.)

The checkboxes are a **living tracker with a cross-session lifecycle**. Each rec carries a stable
**`R<n>`** id — its citation handle, pointed at by the header BLUF and by a later report's carry-forward.

- **Within-session close.** When a rec is implemented this session — on the user's direction or in a
  follow-up turn — flip its `- [ ]` to `- [x]` **as part of that same work** (note the resolving
  file/commit). Closing a *completed* item is recording work already done — distinct from the bounded
  auto-apply above, and always fine; the user shouldn't have to ask. (Pre-check `[x]` likewise for any
  gap closed mid-session *before* the report ran.)
- **Prior-open-recs sweep.** At §6 authoring, the priors-scout hands you every still-open `- [ ]` rec
  from the **Prior reports consulted**. Disposition each: **flip `[x]`** in that prior (lightweight
  back-edit — a checkbox flip or one-line pointer, never a prose rewrite; with the resolving commit)
  if it shipped; **carry it forward** into this §6 with an incremented count
  (`↳ carried from <report>#R<k> (N×, open since <date>)`) if still open + relevant; or **drop** it
  with a one-line "obsolete/superseded because…". **Shipped = the rec's deliverable EXISTS, not the
  practice happened** — flip `[x]` only when the rec's own named artifact is now present (the memory
  entry written, the hook clause added, the skill refined); a rec whose advocated *practice* was
  merely followed once this session, without its artifact created, is **carried** (note the practice
  was validated), never closed — a false `[x]` back-edits a permanent "done" into another report. The back-edits commit by path with the report
  (After-writing step 6). Skip the sweep for a short session whose own recs all closed in-session —
  don't manufacture a carried ledger.
- **Escalate the stuck ones.** Escalate (🔴) only when a rec has been carried **open across ≥2
  reports with FRESH evidence** — a new occurrence or live exercise in the carrying session, not a
  bare keepalive carry — mark it `🔴 ESCALATED`; a skill-refinement one names **skill-forge**
  executor (this restores parity with §5, which counts occurrences, not carries). A carry with **no
  fresh evidence** annotates the chain `— dormant` (glyph legend above) and never escalates — no new
  glyph, no counter, just the annotation. Still open across **≥3** reports *with* fresh evidence each
  time = per-session nudging isn't closing it → route to a coordinated **audit** pass instead of
  re-recommending it identically. A chain gone cold (dormant, no fresh evidence) uses the **existing
  drop disposition** — dormancy alone is not itself a new state. **Carve-out:** a waiting-on-owner
  cross-repo handoff is dormant **by design** — it never escalates AND never drops on dormancy alone;
  its disposition comes only from the `~/.claude/postmortem/HARNESS-RECS.md` ledger.
- **Supersede, don't rewrite (closure isn't binary).** When a recurring problem's right fix changes
  shape across reports, restate the rec in its evolved form under a fresh `R<n>` with
  `supersedes: <prior-report>#R<k>`; the prior's checkbox gets a `↗ SUPERSEDED by <current>#R<n>`
  back-flip. The evolving narrative stays forward; priors keep only a pointer — so a naive `[x]`
  never falsely claims the original text shipped as written.

- [ ] **`R<n>` <Category>**: <one-line proposed change>
  - **Evidence**: §<n> finding #<id> [+ cross-session pattern in `<prior-report>` if relevant]
  - **Expected impact**: <one line>

Categories: `CLAUDE.md update` | `New memory entry` | `Update existing memory` | `Refine custom skill` | `Update path-scoped rule` | `Adjust hook` | `Add idea-tracker entry` | `Brainstorming / spec template enhancement`

These are the routing *vocabulary* only; the home-selection *rationale* (lifespan & scope → which home) is owned by the `durable-docs` skill's lifespan→home map. Name the home; don't re-derive the criteria here.

**Plugin-provenance routing:** before writing an `Adjust hook` / `Refine custom skill` rec, check where the surface ships from — a hook/skill/agent delivered by a plugin (injected text naming the plugin, a `${CLAUDE_PLUGIN_ROOT}` path, or no matching file under this project's `.claude/`) is NOT editable from this project. Word such a rec as a cross-repo handoff — "route to the owning plugin repo (ballast)" — never as a local edit; it is always propose-only, and its chain status lives in `~/.claude/postmortem/HARNESS-RECS.md` (see the prior-open-recs ledger rule). A rec worded as if the consuming project could edit a plugin-shipped hook is a mis-homed rec.

A `Refine custom skill` or new-skill rec names **skill-forge** as executor — it owns skill craft (baseline-first testing, eval, trigger-tuning). A §5 cross-session pattern is pre-vetted RED-baseline evidence for it (already cleared the recurrence bar).

The last category covers spec-gap signals — items the brainstorming or spec-writing process should
have caught upfront but didn't. These don't fit cleanly under "skill" or "CLAUDE.md" and historically
got shoehorned into the wrong category.

**Discipline:**
- **Gate every recommendation through the `durable-docs` 6-gate before emitting it** (recurrence · generality · durability · non-duplication · steering · home): drop or demote-to-example anything that fails, route transient values (versions / counts / hashes / `file:line`) to pointers, and name its home via the lifespan→home map. §6 *produces* durable-doc edits, so the altitude reflex must fire here.
  - *Compressed stub, if durable-docs isn't loaded:* strip every proper noun / date / hash / version — does a transferable rule survive? `n=1` = example unless lifted to a load-bearing principle. Canonical home already holds it? → pointer, not copy. Phrase each rec as a reusable **class**, not the triggering instance.
- Every recommendation MUST cite at least one earlier-section finding
- Recommendations that can't cite evidence are dropped
- One discrete change per checkbox — no sweeping refactors. (A rec to *generalize* two parallel rules into one class is still one change — durable-docs Job 2.)
- Do NOT recommend "fix bug X in file Y" type items — those are session-internal work, not workflow improvements. This skill is meta, not bug-tracking.
- **Every non-subjective §4 item must be either reflected in §6 OR explicitly explained why no action is warranted.** Subjective tweaks need no justification. Missed-requirement, structural, and spec-gap items either drive a §6 recommendation or get a one-line "no action because…" note. This prevents real signals from getting silently dropped.
- If §1-§5 surface no patterns worth acting on, output `_No actions recommended this session._` rather than padding.

## 7. Session Stats & Tool Fingerprint

[Paste the `stats` output verbatim as this section's BODY (starts at `**Window:**`; do NOT rephrase or reformat) — see step 9 for the contract.]

Then splice the bundle's `tool-breakdown.md` verbatim (already markdown; read by the orchestrator
directly from the bundle file — no leaf touches it): the **main-vs-subagent call
split** (e.g. "133 total — 59 main + 74 across subagents") + the conditional **MCP-server table**
(server / distinct tools / calls, present only when an `mcp__` tool fired). The flat per-tool totals
live in the `stats` splice above; `tool-breakdown.md` adds the attribution they can't show.

Then the report-run stat — one line, stamped by the orchestrator immediately before the commit
(mechanics: SKILL.md After-writing step 6; by convention it excludes the commit + wrap-up tail, so
runs stay comparable):

**Postmortem run:** <Xm Ys> (invocation <HH:MM:SSZ> → pre-commit <HH:MM:SSZ>) · tier <LIGHT|STANDARD|HEAVY> · leaf wave <longest-leaf duration, or "none" at LIGHT>

[_Drift footnote, only if `extract.py drift` was non-empty: "canary sighted `<kind>=<value>` (×N) — logged to `~/.claude/postmortem/DRIFT-SIGHTINGS.md` for ballast batch-registration; this report's counts unaffected." Tail-tier by design — an ordinary new shape is routine and earns no header caveat and no §6 rec (see the header-caveat clause for the exception)._]

<!-- USER NOTES BELOW -->
```
