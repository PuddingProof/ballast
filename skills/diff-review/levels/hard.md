`hard · ≤10 angles × 8 · verify (recall) · sweep · ≤15 findings`

You're optimizing for **recall**: a missed bug ships, so lean toward flagging more. Check each angle below against the diff and dispatch one finder per angle that fits — never the whole roster by default. On a close call, run the angle anyway. If two angles flag the same line for different reasons, keep both.

| angle | APPLIES when | model |
|---|---|---|
| linewise | always | strongest |
| removed | the diff deletes or replaces lines | strongest |
| callers | a changed function/symbol has callers outside the diff | strongest |
| pitfalls | the diff touches a footgun-prone language with the relevant constructs | strongest |
| wrapper | the diff adds or modifies a type that wraps or delegates to another | strongest |
| reuse | the diff adds new logic or helpers | sonnet |
| simplification | the diff adds code | sonnet |
| efficiency | the diff adds computation, I/O, loops, or startup/hot-path work | sonnet |
| altitude | any diff that changes code | sonnet |
| conventions | a CLAUDE.md governs a changed file | sonnet |
| sweep | hard only, after verify — one finder over the verified list, gaps only | strongest |
| verifiers | every surviving candidate | strongest |

`strongest` = the top of the Agent tool's `model` list (fable, else opus). Caps — ≤8 candidates/finder, ≤15 findings.

1. Dispatch one **diff-finder** agent per APPLIES angle in a single message — Agent params `description: finder:<angle>`, `model` per the table, prompt = `depth`, `angle`, `cap`, `scope` (the Phase 0 diff command), `repo`, optional `steering`. Each Agent result carries an `agentId`. No `diff-finder` agent type? Use `general-purpose` with the same prompt plus: read `<skill dir>/angles/<angle>.md` and apply it; read-only; return a JSON array of `{file,line,summary,failure_scenario}`; end with `LEAF-DONE`.
2. Collect every dispatched id in the same turn with foreground Bash calls (`timeout: 600000`), at most 6 ids per call and as many calls as it takes: `ballast-await --session <session id> --ids <a,b,c> --timeout 540` (no shim on PATH: `python <skill dir>/scripts/await_leaves.py`, same arguments). These calls ARE the wait — a result that arrives any other way doesn't count, and no turn ends with ids outstanding.
3. Re-run the await once with the printed `PENDING:` ids; anything still pending, or tagged `(error)`, is named in the report as degraded coverage for that angle — do not re-dispatch.
4. Dedup candidates by line/mechanism, keeping the most concrete failure scenario, then dispatch one **diff-verifier** agent per survivor (`description: verify:<file>:<line>`, `model` per the table, prompt = `depth`, `scope`, `repo`, `candidate`; no `diff-verifier` type? `general-purpose` with the same prompt plus: read the cited line and its function; read-only; return `VERDICT: CONFIRMED|PLAUSIBLE|REFUTED — <reason quoting the line>` then `LEAF-DONE`) and collect the same way.
5. Keep CONFIRMED and PLAUSIBLE verdicts, rank most severe first, cap as above. Then dispatch the `sweep` angle as one more finder with the verified list as `steering`, verify its candidates the same way, and fold survivors in.
