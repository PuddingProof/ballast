---
name: refactor-fit
description: >-
  Opt-in: before building a feature or change in existing code, propose the structural
  refactoring it needs to land cleanly, then build to the scope the user greenlights.
when_to_use: >-
  The user invokes /refactor-fit or asks for refactoring to be weighed or put in scope as part
  of a feature or change. Opt-in only — never self-invoke on ordinary implementation requests.
---

# refactor-fit

1. **Assess before editing.** Read the architecture the change touches and propose, recommendation first, in two buckets — one line per item: *what · why · blast radius* (small / multi-file / behavior-changing).
   - **Required for fit** — without it the change bolts on, hardcodes, or duplicates. Recommend these.
   - **Optional, adjacent** — debt the change touches or reveals but doesn't need.
   A design decision the read shows only partly migrated (some adopters moved, some not) is a finding: put its remaining adopters in a bucket.
2. **Get the greenlight.** A bare "go", or no clear pick, means required-only; an optional item ships only on its own explicit yes. No user to ask (autopilot, a single-shot run)? Show the proposal anyway, build required-only, and say so in your summary.
3. **Build to the greenlit scope, no more.** A multi-file or behavior-changing item gets a short plan first.
