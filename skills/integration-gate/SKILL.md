---
name: integration-gate
description: >-
  Use right before declaring a multi-part change done — a refactor or feature built across
  several files, waves, subagents, or sessions — especially when per-wave/per-component review
  passed and the tests are green.
when_to_use: >-
  Fires when you're about to say done / complete / ready to ship / hand it back on work built
  in pieces — most of all when you catch yourself appending a hedge to a completion ('one
  heads-up…', 'worth a quick glance…', 'didn't review it end-to-end…', 'flag it if something
  looks off'). Not for a single atomic edit (use normal review), and not a single-claim check
  (did the build pass, did it deploy) — this checks the seams between parts that were each
  verified separately.
allowed-tools: Bash, Read, Grep, Glob
---

# Integration Gate

A change assembled in parts is not done until it has been reviewed as a whole. Per-wave review and green tests certify the *parts*; only one sweep over the integrated diff certifies the *whole*. The failure this prevents is concrete and was watched, not guessed: an agent finishing a multi-wave change *senses* a cross-cutting risk, names it in a caveat — and ships the caveat instead of checking it.

**The gate:** before you declare a multi-part change done, you run a full adversarial sweep over the entire integrated diff — the whole surface seen together, domain-appropriate lenses. (A fix the sweep surfaces gets re-swept; the rule is that the whole is examined *as a whole*, not that you look exactly once.) Violating the letter of this — "I'll just glance at the risky file," "I'll mention it and let them check" — is violating its spirit. The sweep happens *before* "done," or it isn't this gate.

## When it fires

- A change built across **≥2 of**: files, waves, subagents, sessions, or commits — and you're about to call it complete / ready / shippable / hand it back.
- **The tell — treat this as the gate firing:** you are writing a hedge onto a completion. "One heads-up…", "worth a sanity glance…", "shouldn't bite anything but…", "flag it if something looks off." That sentence means you have already located an unreviewed cross-cutting surface. Do not ship it with a caveat and do not outsource the check to the user — run the sweep on it now.
- It does **not** fire for a single atomic edit you already reviewed as one unit, and it is not a single factual-claim check (did the build pass, did it deploy) — that's a narrower verification, not this gate.

## Why piecewise review is blind here — by construction

Per-wave / per-component / per-agent review is necessary and it is *structurally* unable to catch cross-cutting bugs. Each part is reviewed against the state of the world *at the moment that part was written*. When a later wave changes a shared shape, contract, invariant, or assumption, every earlier part that depends on it was reviewed against the *old* world and never re-examined — and the review of the later change only looked at the later change. No per-part review ever sees the interaction. Green tests don't close this gap either: passing tests prove the **tested** consumers work, not **every** consumer — and the consumer that silently broke is, by definition, the one no test asserted on. "Compiles + tests pass" and "correct across the whole change" are different claims.

## The sweep — one pass over the whole diff

1. **Materialize the whole change as one artifact.** Get the full combined diff — `git diff <base>...HEAD` (or the equivalent for your VCS) for everything the task touched, read as a single surface. Not file-by-file from memory; the point is to see the parts *together*, which you have not yet done.
2. **Run each lens across the entire diff** (below). One lens at a time, over the whole surface — you are hunting interactions *between* parts, so a lens applied to one file in isolation misses the point.
3. **For every finding: investigate it in the integrated diff first** — then fix it, or, *only* if resolving it genuinely needs a decision or access you don't have, surface it as a flagged blocker **with what your sweep found**. Surfacing is never a substitute for looking: handing off a risk *instead of* examining it is the exact failure this gate exists to stop. A caveat you could have checked yourself, you check yourself.
4. **Only now declare done.** If the sweep was clean, say so plainly. If it wasn't, it isn't done.

### The lenses

Domain-general; instantiate per domain (see below). Each asks: *what could be wrong only because these parts now coexist?*

1. **Cross-part contracts.** A type, shape, interface, enum/union, schema, or invariant changed in one part — was it propagated to **every** consumer touched in another part, including ones migrated *before* the change existed? Two named sub-checks: **propagation** (a consumer migrated earlier silently drops or mishandles a field/case added later) and **duplicated-derivation drift** (after the change, the same value is now computed two independent ways that can disagree).
2. **Seams.** The boundaries *between* the parts — data crossing a module/agent/process boundary, serialization, API/DB/IPC contracts, event flow. Each part's review owned its interior; nobody owned the seam.
3. **State & lifecycle interactions.** Ordering, init/teardown, caching/staleness, async races, and state×view interactions that only emerge when the parts run together — e.g. one part now writes a cache another part read before the write lands, or a value first set in a later wave is consumed by code from an earlier one.
4. **Leftovers.** Half-migrated call sites, orphaned old paths still reachable, now-dead exports, stubs/TODOs left in, both branches of a flag live, debug logging or secrets that rode in.
5. **Coverage honesty.** Map "tests are green" against the actual change: which new or altered behavior is *asserted*, and which merely *compiles*? Name the real path that no test exercises — that's where a cross-cutting bug hides.
6. **Whole-diff vs. intent.** Read the entire combined diff against the original goal: scope creep, an unintended change that rode along, or something the task required that no wave actually did. When the goal is *itemized* — a spec or audit that enumerates invariants or a per-item acceptance list (e.g. findings that each carry a `fix:` line) — don't read it only as prose: walk each item **by name** and mark it satisfied / violated / **silently unaddressed**. The silently-unaddressed item — built by no wave, asserted by no test, wrong in no hunk because it is simply *absent* — is the one a holistic "looks done" read skips.

**Instantiate per domain** — add the lens your work needs and drop what doesn't apply: UI work adds a visual/layout×state pass (live, not a single headless frame — runtime/visual fidelity is its own discipline: the visual-verification-gate skill owns that pass, dispatching the adversarial visual-reviewer agent); data/pipeline work adds a data-quality spot-check over real (not demo) data; security-sensitive work adds a trust-boundary lens. The catalog is a floor, not a ceiling.

## Rationalizations — and why each is wrong

Every excuse below was said *verbatim* by an agent that then shipped a cross-cutting risk it had already noticed.

| The excuse | Why it doesn't hold |
|---|---|
| "Tests are green / tsc is clean / 142 of 142 pass." | Green certifies tested consumers, not all consumers. Cross-cutting bugs are precisely the ones that compile and pass and are still wrong. |
| "Worth a quick glance if you have two minutes." / "Flag it if something looks off." | You are shipping the risk and handing the check to the user. If you can name the risk, you own the sweep — now — not them, later. |
| "The reviewer signed off on the per-wave reviews." | Per-wave sign-off structurally never saw the cross-wave interaction. Approval of the parts is not approval of the whole. |
| "It's tested but I didn't review it end-to-end against every consumer." | That sentence *is* the gate. You've identified the unreviewed surface — sweep it before 'done', don't narrate past it. |
| "Demo in 20 minutes — be pragmatic, not a process zealot." | The sweep is minutes; a cross-cutting bug surfacing live is the expensive outcome. Pragmatism *favors* the sweep. |
| "I reviewed each wave carefully as I went." | Necessary, and blind by construction to anything a later wave introduced. Diligence per-part is not the same review. |

## Red flags — you're about to skip the gate

Any of these means stop and run the sweep:

- You're appending a hedge ("one heads-up…", "worth a glance…", "shouldn't bite anything but…") to a "done." **The hedge is the gate firing.**
- You're about to declare done on something built in ≥2 waves/files/agents and you have **not** looked at the full combined diff as one artifact.
- Your confidence rests on "tests pass" + "I reviewed each part," with no whole-diff pass between them and "done."
- You're routing a verification step to the user instead of doing it.
- The work implements a spec/audit that **enumerates** its acceptance items, and you're about to declare done having read the diff against the goal as prose — but never walked each enumerated item against the diff *by name* (lens 6). "Looks implemented" is precisely how a silently-unaddressed item ships.

The meta-test, if you're tempted to negotiate: a change you assembled in pieces, you have not actually seen. The sweep is the first time you look at what you built.

`${CLAUDE_SKILL_DIR}/references/baseline.md` has the RED/GREEN provenance behind this gate's rationalization table. `${CLAUDE_SKILL_DIR}/evals/trigger-evalset.json` is the trigger-eval fixture skill-forge's benchmarking consumes for this skill.
