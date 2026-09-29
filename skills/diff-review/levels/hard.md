`hard · ≤10 angles (≤8 candidates each) · ≤4 verifier leaves (recall) · sweep · ≤15 findings`

You're optimizing for **recall**: a missed bug ships, so lean toward flagging more. Run each angle below that fits — never the whole roster by default; on a close call, run it. Leaves by default (fresh contexts buy recall); run an angle yourself only on a small diff (guide: under ~60 added code lines) and never when the table names a stronger model than yours (see your `<env>` block). Say which ran where in the coverage note. If two angles flag the same line for different reasons, keep both.

| angle | APPLIES when | model |
|---|---|---|
| linewise | always | opus |
| removed | the diff deletes or replaces lines | opus |
| callers | a changed function/symbol has callers outside the diff | opus |
| pitfalls | the diff touches a footgun-prone language with the relevant constructs | opus |
| wrapper | the diff adds or modifies a type that wraps or delegates to another | opus |
| reuse | the diff adds new logic or helpers | sonnet |
| simplification | the diff adds code | sonnet |
| efficiency | the diff adds computation, I/O, loops, or startup/hot-path work | sonnet |
| altitude | any diff that changes code | sonnet |
| conventions | a CLAUDE.md governs a changed file | sonnet |
| sweep | hard only, after verify — one finder over the verified list, gaps only | opus |
| verifiers | every surviving candidate, batched | opus |

Leaves cap at opus — never pass `fable`. Caps — ≤8 candidates/finder, ≤4 verifier leaves, ≤15 findings.

1. Dispatch one **diff-finder** agent per angle you send out, in a single message — Agent params `description: finder:<angle>`, `model` per the table, prompt = `depth`, `angle`, `cap`, `scope` (the Phase 0 diff command), `repo`, optional `steering`. Each Agent result carries an `agentId`. No `diff-finder` agent type? Use `general-purpose` with the same prompt plus: read `<skill dir>/angles/<angle>.md` and apply it; read-only; return a JSON array of `{file,line,summary,failure_scenario}`; end with `LEAF-DONE`.
2. If you dispatched, collect every id in the same turn with foreground Bash calls (`timeout: 600000`), at most 6 ids per call and as many calls as it takes: `ballast-await --session <session id> --ids <a,b,c> --timeout 540` (no shim on PATH: `python <skill dir>/scripts/await_leaves.py`, same arguments). These calls ARE the wait — a result that arrives any other way doesn't count, and no turn ends with ids outstanding.
3. Re-run the await once with the printed `PENDING:` ids; anything still pending, or tagged `(error)`, is named in the report as degraded coverage for that angle — do not re-dispatch.
4. Dedup candidates by line/mechanism, keeping the most concrete failure scenario, then group the survivors by file or function into batches of 2–4 related candidates — at most 4 batches; past 16 survivors, grow the batches, never the leaf count. Dispatch one **diff-verifier** agent per batch (`description: verify:<file>`, `model` per the table, prompt = `depth`, `scope`, `repo`, `candidates` as a JSON array; no `diff-verifier` type? `general-purpose` with the same prompt plus: read each cited line and its function; read-only; return one `VERDICT: <file>:<line> CONFIRMED|PLAUSIBLE|REFUTED — <reason quoting the line>` per candidate, then `LEAF-DONE`) and collect the same way.
5. Keep CONFIRMED and PLAUSIBLE verdicts, rank most severe first, cap as above. Then dispatch the `sweep` angle as one more finder with the verified list as `steering`, verify its candidates yourself, and fold survivors in.
