---
name: durable-docs
description: >-
  Write the reusable class, not the triggering instance. Use whenever creating, editing, or
  auditing any DURABLE doc — CLAUDE.md, memory files, README, a spec/design/handoff, postmortem
  recommendations, persistent .notes, or a hook's injected prompt (a SKILL.md's *craft* is
  skill-forge — but whether something should BE a skill and where it lives is still here).
  Catches the chronic failure of freezing a one-off bug, correction, version number, or
  executor decision as a permanent rule.
when_to_use: >-
  Triggers on "add this to CLAUDE.md / memory", "write a spec", "document this", "note this
  for next time", "update the README", proposing a new always/never rule, writing or editing a
  hook prompt, "should this be a skill / where does this live", OR "audit the docs / are the
  docs stale / doc-audit". If you're about to make a doc edit persist past this session, run
  this first.
argument-hint: "[write | edit | audit [<path>] | (omit = infer from context)]"
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

# Documentation discipline

**Core failure this prevents:** writing durable docs at the altitude of the *instance* that triggered them (this bug, this version, this correction) instead of the reusable *class*, and writing them at peak-salience (n=1, before it's testable). Then the user becomes the staleness detector. Don't make him.

**Two reflexes:** before any durable write, run both.

- **Overfit axis.** Strip every proper noun, date, hash, `:NN`, version, and "this time" detail. **Does a transferable instruction survive?** If not, it's an example — not a durable learning or rule. Avoid overfitting feedback — translate into a bigger-picture principle.
- **Altitude axis.** Scan the draft for lines that hard-code a *how* or *where* a downstream **executor** should own — frozen values, pinned mechanics, pre-decided implementation choices. Cut them, or mark them soft ("executor decides").

**Terseness:** every durable line is re-read every session it loads, so it has to earn that cost — cut whatever won't change a future decision, and never scaffold for an edge case judgment already handles cleanly. The shortest version that still steers wins.

## Argument

`$ARGUMENTS` (optional) pins the job; omit it to infer from session context — the file being written/edited, or an explicit "audit the docs" ask.

| Arg | Job |
|---|---|
| (empty) | infer from context |
| `write` | Job 1 — gate a new durable doc |
| `edit` | Job 2 — revise an existing doc, leaving it leaner |
| `audit [<path>]` | Job 3 — read-only drift report (default: this project's durable surface; `<path>` scopes to a file / dir / project) |

---

## The 6-gate (promotion test)

The shared core — run it before any durable **write or edit** (a new doc, or what you're adding to an existing one). Fail any gate → STOP (don't write, or write less). Job 3 runs it *in reverse*. How hard you apply gates 1/2/4/5 scales with the **intended home's** durability tier (gate 6 confirms the home; for a new write, anticipate it from the content's lifespan) — see the *Lifespan & scope* legend for the calibration.

1. **RECURRENCE — evidence, not a veto.** Seen ≥2× across independent sessions is strong proof it generalizes — keep it. `n=1` can still earn a durable rule, but *only* by translating the specific request/failure into a broader *load-bearing principle* (gate 2) — the instance alone never qualifies, and overfit risk is highest here, so judge hard or leave it a checkbox/example.
2. **GENERALITY** — nothing transferable survives the strip → incident color, STOP.
3. **DURABILITY** — still true after the next refactor / version bump / vendor change? Volatile → attach a re-verify gate, route a number to a log/CHANGELOG, or (for a volatile *state-claim* — trial status, live config) demote it to a trigger condition or route it to memory; don't assert it as standing truth.
4. **NON-DUPLICATION** — does a canonical home already hold this? Yes → write a *pointer*, not a copy. A full copy drifts; it's justified only as a deliberate **reinforcement stub** — a one-line pointer where the reminder must fire (e.g. the global CLAUDE.md → this skill). Run this gate by **Grep, not recall**: search the durable surface (CLAUDE.md, `skills/`, hook-injected texts, memory) for the rule's key phrase before writing — recall-based dedup is how one doctrine accreted ~5 homes before a human caught it.
5. **STEERING VALUE** — will a future session *act differently* because this is loaded? If it only adds confidence/color → drop it. Incident reconstructions fail here by default.
6. **HOME** — pick the home by lifespan & scope (next section). CLAUDE.md requires passing all six **and** being needed at session start. A chosen home brings its own law: a target under a dir with its own conventions file (a subdir CLAUDE.md, a dir-scoped rules doc) gets diffed against those rules too — the global gate can pass a draft the local conventions reject (structure, labels, naming), and that mismatch otherwise surfaces one draft too late.

## Lifespan & scope → home

The other shared core — every job uses it: **writing** picks the home, **editing** checks you're still in the right one, **auditing** flags anything in the wrong one.

| The fact is… | Home | Dur. | Rule |
|---|---|---|---|
| Topic-scoped procedure / how-to | **skill or rules file** | 🪨 | Extract; CLAUDE.md keeps a one-line pointer. Trust agent judgment — no edge-case scaffolding. |
| **Global** behavioral rule too narrow to load every session (a tool-quirk guard, a context-specific reminder) | **a contextual hook** (`PostToolUse`/`PreToolUse`/`UserPromptSubmit` `additionalContext`) | 🪨 | Fires only when relevant instead of taxing every session — e.g. git-commit-guard, doc-write-guard. The injected text is itself a durable prompt: keep it principle-altitude. A genuinely cross-cutting rule still belongs in global CLAUDE.md; this is for narrow ones with no project to live in. |
| Durable rule / guard / orientation, scoped to where it applies | **The narrowest CLAUDE.md that covers it** — subdir › project root › global `~/.claude` | 🪨 | Root + global load *every* session; a subdir CLAUDE.md loads only in that subtree — so place each rule at the **narrowest scope that's still correct** (a frontend-only rule → `frontend/CLAUDE.md`, not project root; a cross-project rule → global, not per-project copies). State the rule + the constraint it protects; no narrative or transient token. |
| Human-facing project reference / orientation — README, SCHEMA, `docs/` (for people, not only agents) | **README / SCHEMA / docs** | 🧱 | Tracks the code or structure it documents — revise on real change, not speculatively. Describe shape + intent and point to code/config for the authoritative definition (a schema or count restated here drifts — see the 🔗 row); don't restate what CLAUDE.md already holds. |
| Stable **project-scoped** fact / settled taste / a project code gotcha or error-handling note — narrow, occasionally needed | **memory file** (per-project — there is no global memory) | 🧱 | Facts and pointers, not mechanism walkthroughs; recall-on-relevance beats loading every session. `tile min-width=300 (user taste)`, not a "don't re-pitch" rule. Volatile → re-verify gate. |
| Design intent for a handoff (what + why; hard invariants) | **spec/brainstorm doc** | ⏳ | Goals + invariants only. Seeds (signatures, keybindings, balance numbers, pipeline mechanics) are labelled "executor decides" or pushed to code comments. |
| Dated investigation / audit / incident / proof, in-flight TODO, or a running backlog (feature requests, known issues) | **postmortem / .notes / IDEAS.md** | 🍂 | Where dates, hashes, byte-proofs, "I wasted a cycle", and churning wish-lists belong. Durable docs may cite these as **provenance** ("investigated in …", "backlog in IDEAS.md") — never as **authority**: no "see here for how X works", no pointer to a specific item, since a frozen record and a churning backlog each drift out from under a load-bearing citation. Resolved → mark resolved, don't carry as live state. |
| Lives in code/config/registry/log (versions, counts, schema N, branch lists, allowlists) | **the source file itself** | 🔗 | Never transcribe a live value into prose. "Read the source file / see settings.json / see the project index doc." One copy = zero drift. **Cite `file` (+ a stable symbol or section name when you need precision), never `file:line`** — line numbers drift on every edit. |

**Durability** (the `Dur.` column) doubles as a **gate dial**: scale rigor to the tier — the more permanent, the harder content must clear gates 1/2/4/5 (a 🪨 entry passes all four hard; a 🍂 note barely gates). Gates 3 and 6 are exempt — 3 *is* this axis, 6 is the placement; and lifespan should match the home's tier, so a mismatch is itself a gate-6 fail. 🔗 is orthogonal — a don't-transcribe flag on the source row, not a lifespan tier; route there however volatile the value.

- 🪨 **Bedrock** — standing law, indefinite
- 🧱 **Durable** — long-lived; changes only when the underlying fact or structure does
- ⏳ **Provisional** — live until executed or superseded
- 🍂 **Ephemeral** — volatile scratch or record; never canon

---

## Job 1 — WRITING a new durable doc

Run the 6-gate, then home it (both above). The gate + the homing map *are* the procedure — nothing more. For a permanent-tier home (CLAUDE.md/AGENTS.md, a SKILL.md, `hooks/`, `commands/`), attest the pass **before** the homing write — the write-gate blocks without the marker, and the attestation is a truth claim (the gates actually ran), not bookkeeping: in the Bash tool: `touch ~/.claude/.cache/docguard/gate-${CLAUDE_SESSION_ID}`. If a write still blocks, use the exact command from the block message — its id is authoritative.

---

## Job 2 — EDITING an existing doc

Same 6-gate on what you're adding (don't redefine it — refer up) — but **editing is not append-only.** The existing structure, headers, and naming are a *candidate for revision*, not a fixed frame to slot into:

- **A new item parallel to an existing one usually means both are instances of a class.** Refactor them into the general form (rename the header, parameterize, make it a table/axis) instead of bolting on an asymmetric sibling that silently treats the old one as canonical — generalize to the dimension the addition reveals (today's two values may become N).
- **Fix adjacent items in the same pass** for consistent naming, altitude, and style; **leave it leaner than you found it** (pointer-ify any transient token or incident narrative you touch).
- **Check the home** (map above): is the addition in the right space, or has the doc outgrown it — does a chunk now belong in a subdir CLAUDE.md, a memory, or a pointer?
- Renaming a section or reorganizing is fair game. Only ever *raise* altitude — rule → narrative is backwards.

---

## Job 3 — AUDITING (the user's staleness detector, so he isn't)

**Read-only.** Read every target FRESH from disk before asserting anything about it — your injected/remembered context (CLAUDE.md, memory, a session summary) is a possibly-stale snapshot, not ground truth; a finding based on it rather than the on-disk file is itself a defect. The report is the product — no edits without approval. Scan the durable surface (CLAUDE.md files, `memory/`, rules/, specs, `skills/`, hook-injected prompts) and run the 6-gate *in reverse* — anything that **fails** a gate (incl. gate 6: wrong home/scope per the map) is a finding. Cite `file + symbol` (never line numbers). **A SKILL.md or hook is partly PROCEDURE** — apply the gates to its durable rules/claims, not to legitimately-necessary procedural detail (a fallback path, a subcommand contract); a drift-prone literal inside necessary procedure stays unless a stable alternative exists. Rank the findings and propose one action per finding; let the format serve the user, don't pre-decide it.

Finding classes:
- **DRIFT** — transient token (version / `:NN` / hash / dated-count / "currently N") in durable text → pointer-ify or demote to log.
- **STALE-STATE** — present-tense claim now false; in-flight TODO completed elsewhere; past-dated prediction → flip-to-resolved or delete.
- **CONTRADICTION** — same fact at two confidence levels or two homes disagreeing → surface the pair, propose canonical winner + precedence pointer.
- **DUPLICATION** — the same content copied across ≥2 homes (a full copy, not a pointer) → collapse to one canonical home + pointers. Acceptable *only* as a deliberate **reinforcement stub** where the reminder must fire (e.g. the CLAUDE.md one-liner → this skill); more than that drifts. A principle independently *re-derived* in different words (not copied) is **not** duplication when each home serves a distinct operational purpose — a shared threshold number or shared vocabulary is not a shared home.
- **WRONG-HOME** — durable content in the wrong space per the map (a global rule copied per-project, a subdir-specific rule in root CLAUDE.md, a project fact in always-loaded config), or owned by a *different repo/tool entirely* — a finding about a consumed library/plugin/config recorded in the consuming repo's docs/tracker → move it to its right home; for the cross-repo case, route via a dated handoff to the owning repo — a repo that can't act on a finding just buries it.
- **ALTITUDE/BLOAT** — incident narrative / mechanism walkthrough / per-file inventory / rule-enumeration in CLAUDE.md or memory, or an over-scaffolded skill / prompt body (see *Skills & hook-prompts are durable docs too*) → raise altitude (keep rule, move story to .notes) or pointer-ify the dup.
- **OVERFIT** — an always/never with n=1 provenance that was never lifted to a principle → demote to example (an n=1 *correctly* lifted to a load-bearing rule is fine — gate 1).
- **MISSED-PRINCIPLE** — same correction re-derived in ≥2 projects → promote the *class* to the global home, retire the copies to pointers.

This replaces the manual weekly doc audit. Hand over the findings; let him skim and approve.

---

## Who holds the pen (delegation shape)

The judgement — the gate decision, the homing, what-the-doc-must-say — never delegates. The mechanics do: holding a long file, splicing entries, formatting are executor work — in a plan→execution handoff, dispatch them as an executor leaf instead of carrying file bodies in the orchestrator's context, and fan doc *audits* out like /diff-review. Exception: session-distillation docs (postmortems, design records) whose content *is* the writing session's context — those stay in-line.

---

## Skills & hook-prompts are durable docs too

**Skill _craft_ — baseline-first testing, eval, trigger-tuning — is `skill-forge`'s job; it consults this gate at its Step 0.** What stays here: whether something should be a skill at all + where it lives (the gate), the general doc principles, and the three prompt tests below (which also govern hook injected-prompts — not authored through skill-forge).

A skill (`SKILL.md`) and a hook's injected text (the `additionalContext` it returns) are reusable prompts loaded into *future* sessions — the same 6-gate applies, plus three prompt-specific tests:

- **Zero-context reader.** The future agent has none of this session's memory — write self-contained, no "this time" / "the bug above" references. The incident that motivated a guard is provenance → a postmortem, never the prompt text itself.
- **Principle over case-list.** State the rule + the constraint it protects so the agent generalizes; enumerating cases makes it pattern-match the list instead of reasoning — over-scaffolding actively *degrades* a prompt.
- **Trigger precision.** Fire at the right moments, not all of them. A skill's `description` is its trigger (too broad → self-invokes as noise; too narrow → misses). A hook must fire on the event that lands the nudge *in time* and word itself to no-op gracefully when mis-fired — git-commit-guard fires on the pre-commit inspection, not the commit, and frames its nudge conditionally ("If you're heading toward a commit…"); plan-authoring.sh states the no-op explicitly ("Not authoring a plan, or matched by accident? Ignore this."). Describe the *mechanism*, not the verbatim string — a hook's exact wording is volatile and this example has already gone stale once.

---

## Before / after (altitude made concrete)

**Instance → class**
- ❌ "a hardcoded list in mock.ts was missing snack-bar + Title-Cased names rendered as slugs (2026-06-02)"
- ✅ "Dynamic sets (themes, measures, device variants) must never be hardcoded in fixtures/docs — enumerate the source at runtime; an exception is a codegen candidate, not a maintained list."

**Spec freezes executor decisions → invariant + seed**
- ❌ SPEC.md froze "Stock: start 3, +1/wave, max 5 / combo 5/12 / x1→x8" as constraints (all re-measured later).
- ✅ "Balance constants are SEED values — verify via playtesting. Spec fixes structure + parity, not tuning." (Seeds live as code comments.)

**Adding a sibling → refactor to the axis (don't bolt on)**
- ❌ A "Token-cost breakdown" section exists; you append a parallel "Model-cost breakdown" that duplicates its shape — the old one stays the implicit canonical.
- ✅ Both are *breakdown axes*: one "Cost breakdowns" structure parameterized by axis (token / model / …), extensible to the next axis with no third copy.

---

**Format:** soft-wrap prose — one line per paragraph/bullet; let the editor and renderer wrap. Never fixed-column hard-wrap (it cascades diffs on every edit and fights hand-editing). Tables, fenced code, and YAML folded scalars are exempt.
