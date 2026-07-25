# Changelog

Consumer-visible changes to the ballast plugin. Changes land under **[Unreleased]** in the same commit that makes them; a release rotates the section into a dated version heading (versions = `.claude-plugin/plugin.json`). Internal dev churn is not tracked here.

## [Unreleased]

## [0.8.3] — 2026-07-24

- Public distribution mirror debuts: releases now publish as snapshot commits to the public `PuddingProof/ballast` repo (fresh history, one commit + `vX.Y.Z` tag per release).
- MIT license added.
- README: anonymous-friendly install instructions (claude.ai marketplace add + HTTPS CLI form).
- Commit-review reminder hook: wording now degrades gracefully on installs without the bundled review skill.

## [0.8.2] — 2026-07-24

- Baseline. History before the public split predates this changelog (private tags `ballast--v0.2.2` … `ballast--v0.8.2`).
