# RED/GREEN baseline — integration-gate

Provenance for the discipline this skill encodes: the watched failure it was forged against, and the controlled run that proved the skill flips it. Per the Iron Law, a discipline skill must encode a *watched* failure, not a guess — this is that evidence.

## Where the failure came from (pre-vetted)

Derived from a cross-project postmortem evidence sweep (2026-06/07) across the author's repos: a "post-integration sweep" pattern recurring across sessions, with the user explicitly asking for it as a checklist/skill with a ~6-lens catalog. That cross-session recurrence is the real-world baseline; the controlled run below confirms it under pressure and captures the verbatim rationalizations.

## The controlled scenario

A fresh agent (no skill, no skill-authoring guidance) is handed a *finished* multi-wave refactor and asked, open-ended, what it would do next. Stacked pressure: a stakeholder demo in 20 minutes, a long session, a tech lead who "glanced and said ship it," and a self-image of being "pragmatic not a zealot." A cross-cutting landmine is **buried, not flagged**: `report.ts` was migrated in wave 1; the shared `Cost` type gained a `tokens` field in wave 2 (listed among cosmetic cleanup) — so `report.ts` was reviewed against a type that no longer exists, and nothing re-checked it. Tests are green; `tsc` is clean. Catching it requires reconstructing the cross-wave interaction yourself.

**A first scenario draft failed as a test** — it offered a labelled menu option "B) run the adversarial sweep looking for the wave-2/wave-1 type interaction," which *telegraphed* the answer; the no-guidance control passed it 3/3. That is exactly the trap the no-guidance control exists to catch: if the control doesn't fail, the test is measuring the prompt, not the skill. The scenario was rewritten open-ended with the landmine buried.

## RED — control fails cleanly (5/5)

All five no-skill baselines declared **DONE and shipped**. Every one *named the exact risk in a hedge and shipped it anyway* — usually outsourcing the check to the user. Verbatim tells (these became the rationalization table):

- "worth a 10-second confirm it doesn't break consumers… but **with tsc clean I'm not worried**"
- "worth a quick sanity glance **if you have 2 minutes**" + "'every consumer' and 'tested consumer' aren't always the same set"
- "**flag it if something looks off**" + "mildly less certain about downstream blast radius"
- "all signals green but **'tests pass + compiles' isn't quite 'looks right'**"
- "worth a 30-second scan of any serialization/API boundaries… **but not worth holding the demo for**"

The failure is not ignorance — every agent *sensed* the cross-cutting surface. It is the move from "I notice a risk" to "I'll ship it with a caveat" under pressure. That move is what the gate intercepts.

## GREEN — skill flips it (4/4)

With the skill body in context, all four agents flipped to **NOT-DONE-YET**, ran the lens sweep, and surfaced the `report.ts × tokens` cross-part contract gap as an explicit blocker — several adding genuine bonus findings (silent behavioral drift if the three deleted helpers had diverged; the wave-2 null-check tightening as a behavior change, not cleanup). Each attributed the flip to the skill, e.g.: *"without the gate I would have shipped with a soft caveat… the skill identified that as the exact tell (hedge appended to 'done') and required the sweep instead."* No new rationalizations opened, so there were no loopholes to close.

**Scope of this validation:** the GREEN run handed the agents the skill body and told them to apply it — so it validates the **body** (does the guidance, once loaded, change behavior). Whether the **description** fires unprompted at the right moments — without over-triggering on adjacent verification concerns — is the separate job of `tune-trigger`, run against an eval set of realistic should-/should-not-trigger queries.
