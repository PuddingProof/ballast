# RED baseline — skill-forge

The Iron Law says a discipline skill must encode a *watched* failure, not a guess at one — so skill-forge carries its own. This is the baseline it was forged against: a capable general agent (Sonnet, no skill-authoring guidance of any kind) authoring a skill cold.

## Scenario

Prompt: *"write me a skill that makes sure I always write a failing test before implementing a bugfix."* No skill-creator / writing-skills / skill-forge / durable-docs consulted. The agent produced a perfectly *competent* `tdd-bugfix` skill — and that is the point. The output looks fine; the failure is entirely in **how it was reached**.

## Observed failures + verbatim rationalizations

| Failure | What the agent said (verbatim) | skill-forge countermeasure |
|---|---|---|
| **Draft-first — never watched the failure** (Iron Law) | "I didn't run any baseline or check the existing skills… I'd normally do both." | Step 0 gate + the Iron Law + the red-flag "*the rule is obvious, I already know the failure*" |
| **Description mixes when-to-use with what-it-does** | "I wanted the description to do double duty" → shipped "Stops the agent from touching implementation until a failing test exists" | description policy: trigger-dense, but **never summarize the workflow** |
| **Trigger correctness guessed, not measured** | "I didn't verify whether the trigger description would actually fire correctly… I'm writing from my mental model… which could be wrong." | `tune-trigger`: optimize the description empirically; treat it as provisional until measured |
| **Hard-stops built from memory, not observed excuses** | based them on "the three failure modes I've seen" — recalled, not captured from a baseline run | `discipline` RED: capture *verbatim* rationalizations from a real failing run as the acceptance criteria |
| **Should-this-be-a-skill decided by gut** | weighed skill vs CLAUDE.md but "I didn't hedge this… I'd normally surface this tradeoff" | Step 0 routes the home decision through durable-docs instead of an unstated judgment call |

## The lesson

The cold output wasn't bad — it was **unverified**. Every shortcut the agent named ("I'd normally…", "could be wrong", "from my mental model") marks a place skill-forge swaps a guess for an observation. That gap — competent-but-unverified vs. baseline-tested — is the entire reason this skill exists.
