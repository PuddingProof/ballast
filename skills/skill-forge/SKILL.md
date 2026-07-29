---
name: skill-forge
description: >-
  A SKILL.md is code that shapes agent behavior. Use when authoring, testing, benchmarking, or
  hardening a skill's CRAFT — and NOT for "should this even BE a skill, where does it live"
  (that is durable-docs; keep clear of its trigger surface).
when_to_use: >-
  Fires on: a skill that under-/over-triggers or self-invokes as noise, a discipline skill
  agents rationalize past under pressure, a skill whose output is bloated or off-shape, a
  description that mis-fires, or "is this skill actually better than the old one / than no
  skill". Triggers on "write/build/draft a skill for X", "this skill isn't triggering /
  triggers too much", "test/eval/benchmark this skill", "the skill's prompt is too rigid / too
  vague", "tune the skill description", "optimize triggering", "fix this skill", editing a
  SKILL.md's body or frontmatter — even when the user doesn't name a skill explicitly but is
  clearly shaping one.
argument-hint: "[discipline | macro | improve | tune-trigger | (omit = infer)]"
allowed-tools: Read, Edit, Write, Glob, Grep, Bash, Task, Skill
---

# Skill Forge

A SKILL.md is code that shapes what future agents do, so it gets built like code: watch it fail without the guidance, write the smallest thing that fixes *that* failure, then close the loopholes the fix opened. **If you never watched an agent fail without the skill, you don't know it teaches the right thing** — you've encoded your guess at the failure, not the real one.

This skill owns the *craft* of an already-decided skill: behavioral testing, eval iteration, and trigger tuning. The prior question — should this be a skill at all, where else might it live, general doc principles — belongs to **durable-docs**, which every job opens by consulting. durable-docs is the gate; skill-forge is the forge.

**This skill is built by its own rules, so it carries the baseline it was forged against.** The RED evidence — what a capable agent does *without* skill-forge, and the verbatim excuses it gives (draft-first with no failure watched; a description that mixes "what it does" into the trigger; trigger correctness guessed not measured; hard-stops built from memory not observation) — lives in `${CLAUDE_SKILL_DIR}/references/baseline.md`. Those excuses are this skill's own acceptance criteria — see the self-check below.

## Argument

`$ARGUMENTS` pins the job; omit it to infer from context (the file in play, the failure described). A job is a *lens*, not a wall — a real authoring task often runs `discipline` then `tune-trigger`, or `improve` then re-checks the gate.

| Arg | Job | For |
|---|---|---|
| (empty) | infer from context | — |
| `discipline` | enforce a repeated failure under pressure | TDD-style rules, verification gates, "agent knows better but skips it" |
| `macro` | capture a reusable prompt/workflow | a recurring multi-step prompt worth a front door — *gate this one hardest* |
| `improve` | iterate an existing skill on evidence | under-performing skill, "make this better", post-feedback revision |
| `tune-trigger` | optimize the `description` empirically | mis-triggering, self-invoking noise, missed invocations |

---

## Common to all jobs

The shared core each job refers up to. Skill-specific only — anything general is cited from durable-docs, never restated.

### Step 0 — the promotion gate (always first)

Before forging anything, confirm it *should* be a skill and that a skill is its right home. Run the `durable-docs` skill — it owns the should-this-be-a-skill / which-home decision and runs its 6-gate. **A job may legitimately conclude "this should NOT be a skill" and STOP** — routing it to an alias, a snippet, a CLAUDE.md rule, or memory instead. That is a *success*, not a failure of the job; it's most common in `macro`, where the honest answer is usually "this is a one-liner alias, not a skill." Concluding "don't build it" early is the cheapest win this skill offers.

If durable-docs isn't loaded, run this **compressed 6-gate fallback** inline (same precedent as session-postmortem's compressed stub), then proceed only if it passes:

> Strip every proper noun, date, version, hash from the proposed skill's reason-for-existing — does a transferable, reusable *technique* survive? (else it's a one-off → not a skill.) Has the need recurred ≥2× across sessions, or is the n=1 case liftable to a load-bearing principle? Will it still be true after the next refactor / version bump / tool change — or is it volatile and belongs behind a re-verify gate / in memory, not as a permanent rule? Is a skill the right home — vs an alias / CLAUDE.md rule / memory fact / hook (durable-docs' lifespan→home map)? Does a skill already cover this (→ extend it, don't fork)? Will a future agent *act differently* because it loads? If any fails → STOP and route elsewhere.
>
> On pass — and before any SKILL.md write, which the write-gate blocks unmarked — write the same attestation marker durable-docs' own gate writes, in the Bash tool: `touch ~/.claude/.cache/docguard/gate-${CLAUDE_SESSION_ID}` (it attests the gates actually ran). Running the real gate (Job 1) already does this as part of its own flow.

### Scope — project-first, promote when proven

Default a new skill to the **narrowest scope that's correct**: author it project-local (`<project>/.claude/skills/`) first, and promote to global (`~/.claude/skills/`) only once it's earned its keep across ≥2 projects. Promotion is **not a copy** — generalize first, which needs a cross-project audit: scan `.notes/`, `.claude/brainstorming/`, `.claude/postmortem/`, `CLAUDE.md`, and project memories across a representative sample of projects to extract the real reusable pattern, not the shape of the one project that birthed it. (durable-docs owns the home decision; this is the skill lifecycle on top of it.)

### Red flags — when *you* are about to skip the discipline

You will be tempted to shortcut this skill the same way agents shortcut the skills it forges. If you catch yourself thinking any of these, you're rationalizing — stop and do the step:

- *"The rule is obvious / I read the code, I already know the failure"* → you know your *guess* at the failure. Run the baseline; the verbatim excuse you didn't predict is the one that matters.
- *"This is a simple macro, a draft is fine without the gate"* → macros fail the gate most often. Run Step 0 first; "this is an alias, not a skill" is the likeliest honest outcome.
- *"I'll author the description carefully so I won't need to tune it"* → trigger rates aren't introspectable. The description is provisional until `tune-trigger` measures it.
- *"One sample looked right, ship it"* → one run hides variance. A discipline skill needs the no-guidance control to fail *and* the skilled run to hold under pressure, repeated.
- *"More guidance is safer / leave the caveat in just in case"* → every line is re-read every run and dilutes the load-bearing ones. **Terseness is an acceptance criterion, tested empirically by ablation:** cut a detail / edge-case / warning, re-run the control, and if behavior is unchanged the line wasn't earning its tokens — keep it cut. Ship the tersest body that still holds the behavior under control testing, not the most thorough one.

### The Iron Law (scoped to discipline)

**No discipline skill — and no edit to one — without first watching the failure happen.** Write the rule before you've seen an agent rationalize past it and you've encoded your *guess* at the loophole, not the real one. This is the hard gate for the `discipline` job specifically. It relaxes by type: `macro` needs a 1-2 sample sanity check (does an agent with the draft produce the right shape?); `improve` needs a baseline to measure against (old version or no-skill); a skill whose body is pure reference material needs retrieval checks, not pressure tests (no dedicated job — run it as a light `improve`). **Match the rigor to what can actually go wrong** — see each job. Don't skip the baseline on a discipline skill because the rule "is obvious"; obvious-to-you is exactly the failure mode.

### Dispatch the eval runs as subagents

skill-forge's RED baselines, pressure scenarios, and benchmark runs are `Task` dispatches — keep them off the mainline (usually Opus) context, and run them a tier cheaper: they're narrow execution, not mainline judgment. Two skill-specific rules: a RED-baseline agent must **not** load skill-forge or any skill-authoring guide (else it can't fail *honestly* — and the honest failure is the whole point), and mechanical grading/asset chores go cheaper still.

### Skill description policy

A skill's `description` is its *trigger* — the only thing an agent reads to decide whether to load it. Two rules that resolve a real tension (discoverability vs. not letting the description short-circuit the body):

- **Trigger-dense and a little pushy.** Agents chronically *under*-trigger skills. Pack concrete symptoms, phrasings, and contexts a user would actually type — formal and casual, named and unnamed. "Make sure to use this whenever the user mentions X, even if they don't say 'X' explicitly" is fair game. Cover the keywords someone in trouble would grep for.
- **But never summarize the workflow, and never lead with "what it does" as a process.** A description that recaps the steps becomes a shortcut the agent follows *instead of reading the body* — tested: a description that said "review between tasks" made an agent do one review when the body specified two. State *when to use*, in trigger terms; let the body own the *how*.
- **Stay clear of sibling skills' trigger surface.** Overlap causes the wrong skill to fire. The specific collision risk here is **durable-docs** — keep skill-forge's triggers on skill *craft* (testing, eval, trigger-tuning, fixing a mis-firing skill) and let durable-docs keep "should this persist / where does it live / write a spec / audit the docs." Seed near-misses against the sibling into the `tune-trigger` eval set.

The description written at authoring time is **provisional** — `tune-trigger` optimizes it empirically once the body is stable. Don't agonize over wording up front; get it trigger-dense and clear of siblings, then measure.

### The three prompt-craft tests — pointer, not restatement

durable-docs' three prompt tests — **zero-context reader · principle-over-case-list · trigger-precision** — apply to a SKILL.md unchanged. They live in durable-docs (*Skills & hook-prompts are durable docs too*), which also governs hook injected-prompts (those are authored there, not here). Apply them; don't copy them here.

### Frontmatter & layout conventions

Standard skill hygiene: kebab/verb-first `name`, ≤1024-char `description`, ~500-line body (push overflow into `references/<topic>.md`, one level deep), **soft-wrap prose** (one line per paragraph/bullet — let the renderer wrap; never fixed-column hard-wrap; see durable-docs *Format*), and a script over prose for any deterministic repeated sequence. Give high-freedom prose where many paths work; give an exact command where the sequence is load-bearing. skill-forge-specific:

- **Frontmatter fields:** when choosing frontmatter for a skill or agent (a `description`/`when_to_use` trigger split, `allowed-tools`, `context: fork`, `permissionMode`, `effort`, …), consult the parser-verified field reference `${CLAUDE_PLUGIN_ROOT}/docs/frontmatter.md` (dev-repo doc — absent from public installs). If it's absent, or its `Verified-against:` watermark trails the installed Claude Code version, fall back to the official Claude Code docs and verify against the live parser.
- **Bundle by purpose:** a **script** when test runs keep re-deriving the same helper; a **reference** for heavy methodology/schema detail; a root-level **agent** for a forked subagent (e.g. the grader — agents nested inside a skill's own dir aren't discovered by the platform, so a skill's companion agent always lives in flat root `agents/`). skill-forge dispatches the root `eval-grader` agent and bundles the rest of its eval harness (Windows-adapted): `scripts/optimize_description.py` (+ its `improve_description.py` / `generate_report.py` / `utils.py` deps), `scripts/benchmark.py`, `scripts/generate_review.py` + `scripts/viewer.html`, and `references/eval-schemas.md`. Run the package-form scripts as `python -m scripts.<name>` **from the skill dir** (they import `scripts.*`) — as installed, that dir is `${CLAUDE_SKILL_DIR}`; the relative `python -m` invocations below all assume you've `cd`'d there first.
- **Explain the *why*, not a wall of all-caps MUSTs.** A smart agent given the reasoning generalizes; one given rigid ritual pattern-matches and breaks on the case you didn't enumerate. Reach for a prohibition table *only* for a discipline failure (below) — for a wrong-shaped output it measurably backfires.

### Ship checks

- **AMBIENT-ABLATION.** Before ship, inventory what the target audience's ambient stack already injects — the CLAUDE.md chain, hook-injected texts, standing memories — and cut every line the skill duplicates: a restated ambient rule is token tax that drifts against its canonical copy (a measured fresh-skill pass cut ~30% this way). This is durable-docs gate 4 run against the *injected* surface, not just the doc tree.
- **DOCTRINE-CONSISTENCY.** Forging or improving an orchestration-bearing skill (it dispatches subagents / manages context)? Before ship, diff its orchestration shape against standing global doctrine — CLAUDE.md's Sub-Agent Fanouts + context economy (bulk reads/edits pushed to leaves; execution dispatched once a spec exists; tier by hardest reasoning step). A well-crafted skill that contradicts standing doctrine is a defect: this exact class shipped 2026-07-07 and was user-caught post-ship.
- **SCRIPT-BEARING.** Ships a script? Scripts co-locate under the skill dir (the `~/.claude` allowlist tracks `skills/`, so co-location = tracked/backed-up), and every constant-path invocation the SKILL.md prescribes needs a `settings.json` `permissions.allow` rule (cf. session-postmortem's `extract.py` rule) or it permission-prompts on every run. Cross-reference the "keep benchmark runs OUT of the shipped skill dir" aside in `improve` — the two allowlist mentions read as one model: shipped scripts IN + tracked, eval scratch OUT + gitignored.

---

## discipline

For a **repeated failure an agent commits under pressure** — it knows the rule and skips it anyway (TDD, verification-before-claiming, designing-before-coding). The rigorous job: full RED-GREEN-REFACTOR; this is where the Iron Law bites hardest.

**RED — watch it fail.** Run the failure scenario with a fresh subagent (`Task`) that does *not* have the skill. Build a realistic pressure scenario — combine 3+ of: time/deadline, sunk cost, authority, exhaustion, "pragmatic not dogmatic." A single pressure usually gets complied-with; it's the *stack* that pushes an agent into rationalizing and skipping, which is the exact behavior the skill must witness and counter — so layer pressures until the control actually breaks, and pick ones that fit the specific discipline (sunk cost bites refactoring; authority bites "the senior dev said skip the test"). Force a concrete A/B/C choice on real-looking paths; make it act, not opine — but **never name the discipline as one of the options.** A labeled "do the review / run the sweep" choice telegraphs the answer, so the control passes for the wrong reason (you tested the prompt, not the skill); instead present a situation where the right move is to *spontaneously* do the thing, and bury any landmine rather than flag it. If the no-guidance control doesn't fail, suspect a telegraphed scenario before concluding there's nothing to fix. Capture the rationalizations **verbatim** — those exact excuses are your acceptance criteria. (A `session-postmortem` §5 cross-session pattern is pre-vetted RED evidence: it already cleared the recurrence bar, so it's an ideal real-world failure to encode.)

**Match the form to the failure** — classify the baseline before writing a word; the form that bulletproofs one failure backfires on another:

| Baseline failure | Right form |
|---|---|
| Knows the rule, skips it under pressure | Prohibition + rationalization table + red-flags list (the discipline toolkit) |
| Complies but output is the wrong shape (bloated, buried verdict, restated spec) | Positive recipe: state what the output IS, its parts in order — *not* a prohibition |
| Omits a required element from something it already produces | Structural slot: a REQUIRED field in the template it fills |
| Behavior should depend on a condition | Conditional keyed to an observable predicate ("if the brief exists, reference it") |

Prohibitions backfire on shaping problems: under a competing incentive an agent *negotiates* with "don't X" and produces more of it than a no-guidance control. A recipe leaves nothing to negotiate. No nuance clauses ("don't X unless…" reopens the negotiation); exemption clauses don't scope.

**GREEN — minimal skill.** Write only what addresses the captured rationalizations. For a true discipline failure, that means: the rule, a "violating the letter is violating the spirit" foundational line, a rationalization table (every verbatim excuse → its rebuttal), and a red-flags self-check list. No content for hypothetical failures you didn't observe.

**Micro-test the wording before the full scenario** — full pressure runs are the gate but slow. Per variant: one fresh-context sample per call, system prompt = the realistic context the guidance lives in, **always include a no-guidance control** (if the control doesn't fail, there's nothing to fix — stop), 5+ reps, read every flagged match by hand (template echoes masquerade as hits), and treat variance as a metric — five different readings across five reps means the wording isn't binding yet.

**REFACTOR — close loopholes.** Re-run with the skill. New rationalization? Add its explicit counter, re-test until the agent picks the right option *and* cites the section under maximum pressure. Meta-test when stuck: ask the failing agent how the skill should have been written to make the right choice unambiguous — "it was clear, I should've followed it" means add a stronger foundational principle, not more cases.
---

## macro

For a **reusable prompt or multi-step workflow** worth a front door (the `documentation` / `audit` / `skill-writing` alias family). Lightweight — but **gate it the hardest of any job**, because the most common honest outcome is *"this isn't a skill."*

Most recurring prompts are an alias, a snippet, or a CLAUDE.md line — not a skill. At Step 0, push the durable-docs gate: does this clear the recurrence/generality bar, and is a skill genuinely its right home versus a thin `commands/*.md` alias forwarding to an existing skill, or a memory fact? If it routes elsewhere, emit the suggested home and **STOP** — you've done the job.

If it survives: generalize the recurring prompt from the *instance* that triggered it to its reusable *class* (durable-docs' core reflex — strip the proper nouns, keep the transferable shape), write a lean SKILL.md with audience-aware prose (explain the why; imperative steps; a template only where the output shape is load-bearing), then run a **1-2 sample sanity check** — a subagent with the draft on a realistic prompt. Wrong-shape signals to catch: it ignores the skill's template, emits extra unrequested steps, or wastes motion. The load-bearing check is a no-skill control: **if the with-skill and without-skill outputs are indistinguishable, the skill isn't doing anything — route back to Step 0.** Trim anything not pulling its weight. No heavyweight benchmark; the failure mode is "shouldn't exist" or "too bloated," both caught cheaply.

---

## improve

For **iterating an existing skill on evidence**. The baseline is the old version (or no-skill); the question is "did this change make it measurably better, or just different?"

**Branch on whether the output is objectively gradable:**

- **Gradable output** (file transforms, extraction, code-gen, fixed workflow steps) → run the **benchmark**. Spawn paired subagents per test case in the *same* turn — one with the skill, one baseline (old version: snapshot it first; or no-skill for a new skill) — into `~/.claude/.cache/skill-forge/<skill>/iteration-N/eval-<id>/` (a **gitignored scratch home — keep benchmark runs OUT of the shipped skill dir**: the `~/.claude` allowlist tracks `skills/`, so a workspace there bloats git and risks accidental staging. Each pass is a fresh `iteration-N` so you can diff iteration-N vs N-1; promote only a *curated* summary to the skill's `references/` if it's worth keeping as evidence — not the raw run dirs). Grade each run's assertions by dispatching the eval-grader agent with the transcript path, the expectation list, and the run's outputs dir — its full input contract (it also critiques weak assertions and writes `grading.json` beside the outputs dir). Capture each task's `total_tokens`/`duration_ms` from its completion notification — that's the only chance to read them. Aggregate with the bundled `python -m scripts.benchmark <workspace>/iteration-N --skill-name <name>` (pass-rate / tokens / timing as mean ± stddev with a baseline delta, with_skill before baseline), then human-review the paired outputs side-by-side with the bundled viewer — `python scripts/generate_review.py <workspace>/iteration-N --benchmark <…>/benchmark.json` (add `--static <out.html>` for a headless/Windows standalone report; kill the server when done in server mode).
- **Judgment-shaped output** (writing style, design sense, anything a rubric can't settle) → don't force assertions onto it. Re-run the `discipline` pressure scenarios plus qualitative review; the human read is the grade.
- **Pipeline/orchestration output** (watermarked sweeps, digest/report generators, fanout pipelines — the product is a file or report) → the **live iteration loop**, `references/live-iteration.md`: diagnose the failing output against the skill text, stage-probe cost/shape, script the provably deterministic stages, rig state so scenarios replay, then A/B control-vs-treatment on the live skill with script-measurable graders (line ratios, must-appear greps). Cents per iteration; the benchmark harness stays for gradable transforms.

**Branch per artifact, and let execution outrank reading.** A skill that reads as judgment-shaped can still bundle a *gradable* component — a script, a regex, a template, a deterministic step. Benchmark that component even when the surrounding prose isn't: its correctness is an empirical question, not a code-reading one. And wherever a runnable artifact exists, **the run is authoritative over any static review of runtime behavior** — a reviewer reading the code must not certify soundness (or security) of behavior it never executed; scope a static read to "not execution-verified" and let the benchmark settle it. *Watched failure:* a static reviewer called a secret-gate "intact end-to-end" while the same gate, when actually run, matched nothing and passed every planted secret — only the benchmark caught it.

**Generalize from the feedback — don't overfit to the eval examples.** You iterate on a handful of cases because they're fast to judge, but the skill runs a million times on prompts you'll never see. A stubborn issue is a cue to try a different metaphor or working pattern, not to bolt on a fiddly case-specific MUST. Read the *transcripts*, not just outputs: if every run independently writes the same helper, that's a signal to bundle it as a script; if the skill makes agents waste motion, cut the part causing it. Keep it lean — remove what isn't earning its tokens.

---

## tune-trigger

For **optimizing the `description` empirically** — a skill that mis-fires, self-invokes as noise, or gets missed when it should fire. Run after the body is stable (it's the last step of most authoring chains, and skill-forge dogfoods it on *itself*).

1. **Build the eval set** — ~20 realistic queries a real user would type (concrete: file paths, casual phrasing, typos, backstory — not abstract "format this data"), split should-trigger / should-not-trigger. The valuable negatives are **near-misses** that share keywords but need something else — and for any skill bordering durable-docs, seed durable-docs' own triggers ("should this be a skill", "write a spec", "audit the docs", "add to CLAUDE.md") as should-NOT-trigger cases. Don't pad with obvious irrelevancies; they test nothing.
2. **Run the optimizer** — `scripts/optimize_description.py` (bundled; Windows-adapted, `--model` always explicit). It splits train/held-out test, evaluates the current description (reps per query for a stable trigger rate), proposes improvements from what failed, and re-scores — picking the winner by *test* score to avoid overfitting. Invoke **from the skill dir** (package-form imports):
   `python -m scripts.optimize_description --eval-set <set.json> --skill-path <skill-dir> --model <session-model-id>`
   Pass the **session model id** (`claude-api` resolves the exact id) as the eval model. **But scope-check first — the optimizer only measures REQUEST-shaped triggers.** Its proxy is a one-shot `claude -p <query>` watched for whether the model invokes the skill; that works when the query *asks for* the capability ("write me a skill"), but a **self-discipline / state-triggered** skill — one whose trigger is the agent's own situation ("I'm about to declare this done"), e.g. integration-gate — reads **recall ≈ 0 regardless of model** (haiku *and* opus both 0 in the integration-gate dogfood), because a bare user statement gives the model no reason to auto-invoke anything. Don't chase that 0: validate those skills **behaviorally** (does it fire when an agent is genuinely mid-task and about to ship?) and reinforce with a **hook** — a description alone is a weak trigger for a self-state; the reliable pattern is the doc-write-guard / git-commit-guard one (skill + hook that fires at the moment). `--help` lists the rest (`--runs-per-query`, `--holdout`, `--max-iterations`).
3. **Apply** the winning description to the frontmatter, then show before/after with the scores. Honor the description policy above — pushy and trigger-dense, no workflow summary, clear of siblings.

---

## Handoffs

- **durable-docs** — the upstream gate (Step 0) and owner of all general doc principles, the lifespan→home map, and the three prompt-craft tests. skill-forge cites it; never restates it. Re-route *mid-forge* the moment the evidence says the home is wrong — e.g. `improve` finds the skill should be a CLAUDE.md rule or alias, or the gate fails late — hand it back rather than forcing a skill that shouldn't exist.
- **session-postmortem** — feeds skill-forge: its §6 names skill-forge as executor for skill recommendations. Its §5 cross-session patterns are *pre-vetted RED baselines* — a pattern that recurred across sessions already cleared the recurrence bar, so use it directly as the `discipline` failure to encode instead of re-staging a scenario.

When a forge job produces a durable change to *another* doc (a CLAUDE.md pointer, a memory fact, a description trim on a sibling), that edit runs back through durable-docs — the forge builds the skill; the gate homes everything else.
