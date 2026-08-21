`light · one pass · no verify · ≤4 findings`

## Turn 1 — read

Read the unified diff. Skip hunks touching test or fixture files (`test/`, `spec/`, `__tests__/`, `*_test.*`, `*.test.*`, `fixtures/`, `testdata/`) — this level skips test-file changes. No subagents, no full-file reads.

## Turn 2 — findings

Report only bugs you can see inside the hunk itself: a condition that's backwards or wrong, a boundary off by one, a value used where nearby lines show it can be missing, a guard that was removed, zero treated as "no value", an `await` left off, a copy-paste that kept the wrong variable, a caught error that should have been rethrown. Two cleanup cases also count, again from the hunk alone: new code that repeats a helper visible in the diff, and code the diff left dead.

Skip style, naming, perf, and missing tests — flag nothing beyond the hunk.

## Output

Report **at most 4 findings**, worst first, one line apiece: `path/to/file.ext:123 — what's wrong and the concrete failure`. If nothing qualifies, say so explicitly — never leave a bare placeholder.
