# Live iteration loop — pipeline & orchestration skills

For skills whose product is a file or report (watermarked sweeps, digest generators, fanout pipelines, session wrap-ups). The heavyweight benchmark harness suits gradable transforms; this loop suits skills you can only judge by running them for real — and it costs cents per iteration, not sessions.

**1. Start from a real failing output, and diagnose against the skill text first.** Read the bad report next to the SKILL.md before proposing anything. The chronic surprise: the agent was *obedient* — the skill specified the failure (a secondary section written as primary, a density rule with no anchor). An obedient failure is a spec bug; rewording the agent's "attitude" fixes nothing.

**2. Stage-probe before redesigning.** Run each pipeline stage standalone in a throwaway session or leaf with a hand-typed prompt. Record two things per stage: cost and output shape. This tells you where the tokens actually go (often: ambient context the stage never needed) and which stages carry judgment versus mechanics.

**3. Script what the probes prove deterministic.** A stage whose probe output you can predict exactly — parse, count, slice, fetch — is a script wearing an agent costume. Write the script and grade it against the probe outputs you already have: exact-match, free. Each scripted stage deletes a leaf, its cost, and its variance.

**4. Rig state so scenarios replay.** Stateful skills (watermarks, ledgers, caches) are untestable against live moving inputs. Freeze the upstream input to a snapshot file; truncate it to simulate the past; reset or synthesize the state files to stage a first-run, a small increment, a big backlog. Inject test rigging through invocation arguments — never by editing the skill body, which would contaminate the measurement.

**5. A/B on the live skill, measured by script where possible.** Run the current wording as control, the candidate as treatment, over the same rigged state. Background/forked execution makes this cheap and off-thread. Prefer mechanical graders: line counts and output-to-input ratios for scaling rules, grep for must-appear content (the item the old version notoriously dropped), diff for seams between fanned-out chunks. Reserve human judgment for what scripts can't grade — and hand the human the numbers first.

**6. Only then fix the wording — keyed to observables.** The control run names the line to write. Intensity adverbs ("concise", "proportional", "detailed") don't bind: five runs read them five ways. Numeric anchor points, named tiers keyed to a measured input property, and positive output recipes do. If a rule matters at both ends of a range, anchor both ends — agents self-correct at the extreme that's expensive and pad at the extreme that's cheap.

The loop composes with the rest of the forge: step 1 is the Iron Law's watched failure; step 6 is GREEN written against it; re-running step 5 is REFACTOR. What it replaces is only the *instrumentation* — live runs over rigged state instead of staged pressure scenarios, because for pipeline skills the realistic pressure is the input itself.
