`medium · ≤6 angles (≤6 candidates each) · self-verify, no verifier leaves · ≤8 findings`

You're optimizing for **precision**: only surface findings a maintainer would act on. Run each angle below that fits — never the whole roster by default; on a close call, skip it. If more than 6 fit, drop the weakest-fitting cleanup angles first. Run angles yourself unless the diff is too large for one context (guide: ~150 added code lines) or the table names a stronger model than yours (see your `<env>` block); verify in-context. Say which ran where in the coverage note.

| angle | APPLIES when | model |
|---|---|---|
| linewise | always | opus |
| removed | the diff deletes or replaces lines | opus |
| callers | a changed function/symbol has callers outside the diff | opus |
| reuse | the diff adds new logic or helpers | sonnet |
| simplification | the diff adds code | sonnet |
| efficiency | the diff adds computation, I/O, loops, or startup/hot-path work | sonnet |
| altitude | any diff that changes code | sonnet |
| conventions | a CLAUDE.md governs a changed file | sonnet |

Caps — ≤6 angles, ≤6 candidates/finder, ≤8 findings.

1. Dispatch one **diff-finder** agent per angle you send out, in a single message — Agent params `description: finder:<angle>`, `model` per the table, prompt = `depth`, `angle`, `cap`, `scope` (the Phase 0 diff command), `repo`, optional `steering`. Each Agent result carries an `agentId`. No `diff-finder` agent type? Use `general-purpose` with the same prompt plus: read `<skill dir>/angles/<angle>.md` and apply it; read-only; return a JSON array of `{file,line,summary,failure_scenario}`; end with `LEAF-DONE`.
2. If you dispatched, collect every id in the same turn with foreground Bash calls (`timeout: 600000`), at most 6 ids per call and as many calls as it takes: `ballast-await --session <session id> --ids <a,b,c> --timeout 540` (no shim on PATH: `python <skill dir>/scripts/await_leaves.py`, same arguments). These calls ARE the wait — a result that arrives any other way doesn't count, and no turn ends with ids outstanding.
3. Re-run the await once with the printed `PENDING:` ids; anything still pending, or tagged `(error)`, is named in the report as degraded coverage for that angle — do not re-dispatch.
4. Dedup candidates by line/mechanism, keeping the most concrete failure scenario, then verify each survivor yourself: read the cited line and its function and rule CONFIRMED / PLAUSIBLE / REFUTED.
5. Keep CONFIRMED and PLAUSIBLE verdicts, rank most severe first, cap as above.
