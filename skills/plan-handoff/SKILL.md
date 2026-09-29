---
name: plan-handoff
description: >-
  Plan→execution protocol: once a plan is approved, the main thread orchestrates, verifies, and
  reviews while leaf agents write the code — execution shape, the dispatch brief, and the
  close-out before hand-back.
when_to_use: >-
  At plan approval (the plan-handoff hook points here), on "implement the plan" / "go ahead /
  build it", and at every unplanned tail where implementation work reappears after the planned
  waves: a bug wave from live verification, a stalled leaf, a post-compact correction pass. Not
  for planning itself or review fan-outs.
---

# Plan → execution handoff

Once a plan exists, the main thread orchestrates, verifies, and reviews — it does not type the implementation. Everything in the main window is re-read on every later turn; a leaf's context is paid for once. Compacting doesn't rescue an in-line build: the next rounds rebuild the bloat.

## Pick the shape

| Work | Shape |
|---|---|
| Tiny diff, or taste iteration that can't be specified (visual tuning, wording) | In-line |
| Multi-part plan | Leaves in this session — parallel when independent, sequential when coupled. A long build can go to one background leaf that owns it: dispatch, then stop; the harness notifies you |
| One coherent, fully specified task | Save the plan in the project (native plan mode writes only to `~/.claude/plans/`) and hand it to a fresh, cheaper session |
| **Unplanned tail** — a bug wave after shipping, a stalled leaf, fixes after a compact | The same split: diagnose top-tier, dispatch the specified fix. For a stall, check what actually landed and that the stalled leaf has no background task still writing, then give the remainder to a fresh leaf |

## Dispatch

Pick the leaf type by the batch's hardest step: `leaf-light` (Sonnet) for mechanical or well-specified batches, `leaf` (Opus, the default), `leaf-hard` (Opus, high effort) for the hardest fully specified ones — difficulty, not volume. A batch that still needs design judgement is not ready to dispatch.

Each brief carries:
- the plan steps (or the plan file path and step range), the checks to run, and the report shape;
- two or three sentences of why — the goal and constraints behind the steps. A leaf's comments are only as good as the why it is given;
- premises checked against the live code, or phrased as conditions. A leaf that reports a contradicted premise has done its job: re-check, don't override. Specs drafted by a fan-out come back locally right and blind to the rest of the codebase — review them against it first;
- a time or scope cap on any long or exploratory leaf.

When the first wave lands, say what it cost against what the task is worth, and confirm the remaining scope. Pure renames and token swaps go to a script (sed, a codemod), not a leaf — agents invent things under mechanical monotony.

## What stays with you

- Open-ended verification — driving the app, looking at the UI. A leaf told to "verify it works" builds ad-hoc harnesses and chases non-bugs. Visual work runs through the visual-probe skill, loaded when implementation starts.
- Review verdicts. Reviewers propose, you decide; a reviewer's plausible "defensive" fix can quietly undo the change. Applying the accepted fixes is another dispatch.
- Commits.

Keep the main window to the plan, dispatch summaries, and verdicts — no file bodies, diffs, or screenshots.

## Close-out

Before you hand back, read the combined diff (`git diff <base>..HEAD`) once, as a whole:
- walk each plan item by name — built, or silently missing?
- what one batch changed that another batch still assumes;
- leftovers: half-migrated call sites, stale comments, pointer TODOs a leaf left for you.

A caveat you are about to hand back is a check you haven't run — run it. Review gates over the frozen diff (diff-review, a visual check) launch together, not one after another.
