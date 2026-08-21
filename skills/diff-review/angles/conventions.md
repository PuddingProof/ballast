APPLIES: a CLAUDE.md file governs one of the changed files (skip otherwise)

Find every CLAUDE.md that governs the changed code: the user-level ~/.claude/CLAUDE.md, the repo-root CLAUDE.md, and any CLAUDE.md or CLAUDE.local.md in a directory above a changed file (each one applies only to files at or below its own directory). Read the ones that exist, then check the diff against their rules for clear violations.

A finding needs two quotes: the rule, and the line that breaks it. Without both it's an opinion, not a finding — skip anything that rests on taste or on what the doc "probably meant". Give the CLAUDE.md path alongside the quoted rule so the report can point at it. No governing CLAUDE.md means nothing to report.
