---
name: lean-spec
description: >-
  Spec-writing guidance for agentic work: the prompt an agent builds from, holding intent, key
  requirements, and load-bearing constraints, with the implementation plan left to the agent.
when_to_use: >-
  Writing, tightening, or revising a build spec or implementation prompt for a Claude Code
  session, including turning a design brainstorm into one. Not for living project docs
  (architecture docs, canon specs), which are durable-docs, or for implementation plans.
---

# Lean spec

A spec holds the theory of the work: why it exists and what must stay true. Treat specs as product design documents: high-altitude intent, key requirements, and load-bearing constraints only. Specs cover the "what" and "why" — leave the "how" for implementation planning.

## Structure

- **Motivation and intent.** The core problem, objective statement, and who it serves, in a few sentences at high altitude.
- **Key requirements.** The behaviors that must exist and what "done" looks like.
- **Constraints.** Invariants, interfaces, security boundaries, data, and performance limits.

## Rules

1. **Every line must change what gets built.** If removing a line changes nothing, remove it.
    - Phrase rules positively ("every write is logged"). A prohibition earns its line only when it blocks a failure agents have actually shown.
2. **Describe observable outcomes and key goals.**
    - Execution details are bloat. The implementer chooses files, functions, and steps unless a constraint fixes one in the spec.
    - State what is, not how we got here. A decision appears as its current result; history, alternatives considered, and churn stay out.
3. **Aim for one page or less.**
    - A straightforward request can be as short as a single paragraph and a handful of key requirements.
    - Longer means unnecessary detail or bloat has leaked in, so cut aggressively. When a large build needs a bigger spec, split it into separate documents.

## Revising an existing spec

Trim or restructure in place: cut provenance, plans, hedges, and duplicated requirements; keep the author's wording wherever it is already lean.
