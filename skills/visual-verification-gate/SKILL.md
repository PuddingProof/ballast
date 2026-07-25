---
name: visual-verification-gate
description: >-
  Use right before calling frontend work done — fires when completion language ("done", "ship
  it", "hand it back", "looks good", "that's everything", "ready for you") meets a
  frontend-file signal: the session touched .tsx/.jsx/.svelte/.vue/.css/.scss/.html or any
  component/style/template file.
when_to_use: >-
  Use even when the change seems small or purely cosmetic — a one-line style tweak ships a
  visual defect as easily as a rebuild — and even when tests, type checks, and code review are
  all green. Arms ONLY on the frontend signal: a session that touched no frontend files never
  pays for this. Not integration-gate (that owns the seams of a multi-part diff) and not
  visual-probe (that owns how to drive and capture the browser); this skill owns the moment
  visual work is about to be declared done.
---

# Visual Verification Gate

Frontend work is not done until the rendered pixels have been seen — in their worst states — by an eye that wants to find defects. Every proxy in between certifies something else: types certify shapes, tests certify behavior, code review certifies source. None of them ever looks at a frame. The watched failure is exact: rigorous sessions with green tests, code review sweeps, and even real capture matrices still shipped contrast failures, overlapping elements, and clipped text — because for visual work the competence loop (write → run → **read result** → correct) is open by default, and when it closes it runs on self-selected happy-path states while a green proxy check launders into "verified."

**The gate:** two tiers, both mandatory once the session has touched frontend files. Tier 1 is a reflex you run while building; Tier 2 is an independent verdict you obtain before "done." Passing Tier 1 does not waive Tier 2 — in the watched failures the builder *looked* and still shipped, because a builder's own look is confirmation-biased by construction.

## When it arms and fires

- **Arms** only on the frontend signal: the session touched `.tsx`/`.jsx`/`.svelte`/`.vue`/`.css`/`.scss`/`.html` or component/style/template files. Backend and CLI sessions pay nothing — if it didn't arm, stop reading.
- **Fires** when an armed session reaches completion language: done, ship it, hand it back, looks good, that's everything.
- **The tell — treat this as the gate firing:** you are typing a hedged caveat onto a frontend completion ("worth a quick visual check on your end", "you may want to eyeball the dark theme"). That sentence means you have located an unlooked-at surface and are about to ship it to the user's eyes instead of yours. Run the gate on it now.

## Why everything short of the gate is blind — by construction

**Host-side gates cannot see runtime.** Type checks, unit tests, and static code review — at any fan-out size — reason about source, and a visual defect is a property of the *rendered frame*: composited layers, computed styles, actual font metrics, real content widths. A static review of rendering code, however many reviewers you throw at it, examines a different artifact than the one the user sees; a 21-agent static review passed a one-property rendering bug that a single live screenshot caught. The corollary is directional: when a change touches a rendering mechanism, it goes to the live visual gate *fast* — growing the static review is adding blind readers, not coverage.

**The builder's own look cannot gate itself.** It runs on states the builder chose — the ones expected to work — so functional coverage masquerades as visual coverage (a script *opened* every overlay, but the screenshot fired *after they closed*: driven, never seen), and coverage is silently capped by forceability (a state with no seam to force it never appears in any frame, for builder or reviewer — see the state contract in the visual-probe skill's references). The builder's look is necessary hygiene; it is not evidence.

## Tier 1 — the in-loop see-and-fix reflex

While building, close the loop on every visual change *in the same turn you make it*: force the state you just touched — worst content, both themes, any overlay that sits over it, composed together rather than one at a time (the visual-probe skill's "Plan the capture matrix" section owns the how; its state contract owns the forcing vocabulary) — then **look at the capture**, and fix objective defects on the spot: overlap, clipping, contrast, bad wrap. Mid-turn, before the user ever sees them. A visual edit you never rendered is an edit you made blind.

## Tier 2 — the independent gate

Before declaring done, dispatch the visual-reviewer agent with the target, the intent, and any states you know matter — it derives its own adversarial matrix on top of them, which is the point: fresh eyes over states *you didn't pick*. When design intent or tokens exist — from the frontend-design plugin or any spec — pass them as the reviewer's INTENT input: taste authors the tokens, the reviewer enforces them. The reviewer is a long-running leaf that consumes no other gate's output: when other close-out gates are pending on the same frozen diff (a code-review fanout, an integration-gate dispatch), launch it alongside them in one wave and adjudicate the merged findings — queuing it after them buys nothing but wall-clock.

**Size the dispatch to the change — the reviewer's mode input, and yours to set.** A scoped change to one component, drawer, or flyout dispatches a **targeted** review naming that surface (the reviewer composes its worst corner plus a couple of full-window regression cells — a handful of cells, not a 30-permutation sweep); a new surface, redesign, or first contract adoption dispatches **full**; a re-verify after fixes dispatches **delta** with the prior findings. Sizing down is not skipping: a targeted review still composes the worst corner for the surface it covers. The reviewer may escalate scope on evidence (shared token, global style) — never pre-shrink a full-sized change to dodge the cost.

**Fanout — full reviews only, capped at 3.** A full review over multiple routes or a broad checklist may split across 2–3 parallel reviewer dispatches, each assigned a disjoint facet (a route subset, or checklist facets like layout/overflow vs contrast/a11y vs interaction) with the same target and intent. You own the shared origin: start one review-dedicated server (or `probe serve`), pass its URL as each reviewer's Origin input, stop it after they all return — parallel reviewers must never each boot their own. Merge by worst-verdict-governs; concatenate scope lines and union the blind spots — a facet nobody was assigned is a hole, not a pass. Never split a targeted or delta review: dispatch overhead exceeds the win.

Then refuse "done" until the verdict allows it:

- **`pass`** — declare done.
- **`pass_partial`** — declare done only with the verdict's named scope carried **verbatim** into your handoff. A pass over a reduced scope silently widened into "verified" is the false pass this gate exists to kill.
- **`needs_work`** — the authoring split governs: the reviewer owns the evidence-backed findings; you own triage, the fixes, and the done-call. Fix, then **re-dispatch the reviewer for a delta review** — never self-certify your own fix visually, for the same confirmation-bias reason the gate exists.
- **`needs_fixture`** — not a failure to bury and not a pass to round up. It means the project lacks a seam: a state nobody — builder or reviewer — can force into a frame. The missing fixture or marker is *work*: surface it in the handoff as such, citing the state contract (`${CLAUDE_PLUGIN_ROOT}/skills/visual-probe/references/state-contract.md`).
- **`blocked`** — a harness problem. Fix the environment and re-dispatch; a blocked review is not a review.

## Rationalizations — and why each is wrong

Each excuse below is a watched class — said by an agent that then shipped a visual defect.

| The excuse | Why it doesn't hold |
|---|---|
| "Shipped and verified" (off a happy-path capture matrix) | The matrix enumerated the states expected to work. Defects live in the composed corners it never included — a matrix's greenness is capped by its worst-covered cell, not its cell count. |
| "Worth a quick visual check on your end." | You are shipping the risk and outsourcing the look to the user. If you can name the check, you owe the look — that hedge IS the gate firing. |
| "The probe / the tests passed." | Functional green certifies what was *driven*, not what was *seen*. A script opened every overlay and screenshotted after they closed — every assertion passed, no overlay was ever in a frame. |
| "I reviewed the code paths for the styling change." | Static review examines source; the defect lives in the rendered frame. A 21-agent static review passed a one-property rendering bug a single live screenshot caught. Rendering change → live gate, fast. |
| "The matrix covered 8 states × 3 sizes." | One-hot coverage — each axis varied alone. The shipped defects sat in the composed cells (overlay × opposite theme × worst content × small viewport) the matrix never contained. |
| "It rendered fine when I opened it." | Default state, default theme, happy content — the single least likely cell to break. Everything the gate exists for lives in the states you didn't force. |
| "A full review is overkill for this one-line tweak." | Correct — that's what the targeted mode is for. The gate scales down to a handful of worst-corner cells on the touched surface; it never scales to zero. |

## Red flags — you're about to skip the gate

- You're declaring frontend work done with **zero captures in the turn**.
- You're describing what the UI "should look like" — future tense, from source — instead of what a capture shows.
- You're writing a verdictless completion: "the changes are complete," with no reviewer verdict behind it.
- You're treating your own green forced-state run over states *you* chose as if it were the reviewer's derived coverage. Same tool, different matrix — the reviewer composes the worst corner; you enumerated your expectations.
- You're postponing the look to the user's live test. Their first render must not be the first render.

The meta-test, if you're tempted to negotiate: a frontend change nobody adversarial has seen rendered has not been verified — it has been compiled, tested, and *imagined*. The reviewer's verdict is the first time anyone looks at what you built in the states you didn't pick.
