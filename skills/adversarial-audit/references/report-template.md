# Audit report template — `.notes/YYYY-MM-DD-<scope>-audit.md`

The report is a live tracker during the audit and the frozen record after it. Fill sections top-down; the BLUF is written LAST (conciseness pass over the whole doc first). Adapt sections to the audit — drop what didn't occur, don't pad.

```markdown
# <Project> <scope> audit — YYYY-MM-DD

## BLUF
<3–8 lines, written last: what was audited, headline numbers (findings raw → canonical → confirmed), what shipped, what needs the user's eyes (🎨 items), overall verdict.>

## Legend
Severity: 🔴 critical · 🟠 high · 🟡 medium · ⚪ nit
Verifier verdict: ✅ confirmed · 🟡ᵛ plausible · ❌ refuted
Disposition: 🚢 shipped · ◐ partial · 🚫 rejected (reason given) · 📥 deferred → IDEAS.md · 🎨 user call
⚔️ = contradicts documented design intent (intent is a claim, not a defense)

## Master checklist
| ID | Sev | Verdict | Finding | Anchor | Disposition |
|---|---|---|---|---|---|
| F01 | 🔴 | ✅ | <one line> | `file · symbol` | 🚢 c1a2b3 |

## Findings
### 🔴 Critical
**F01 <lens> · <effort est.> · ✅** — <title>
<anchor> — <evidence / failure scenario> — fix: <what was done or proposed>

### 🟠 High … 🟡 Medium … ⚪ Nits
<same shape; nits may be table-only>

## Rejected (with reasoning)
| ID | Finding | Why rejected |
<every 🚫 gets an explicit correctness/false-premise/disproportionate-cost reason — never a bare veto>

## Deferred
<📥 items: one line each + the IDEAS.md stub link. Audit noise that isn't genuine future work stays here and does NOT go to IDEAS.md.>

## Methodology
<lenses run + model tier each · agent counts · verify kill rate · counter-review lane result · pre-registration diff (what the orchestrator predicted vs what fanout found) · lenses that failed and were re-dispatched>

## Commits
| Checkpoint | Commit | Scope | Gate |
|---|---|---|---|
| Batch A — <area> | <hash> | <paths> | tests ✅ |
```

IDEAS.md stub shape for 📥 items:
`- [ ] <emoji> **Title** — rationale. Seed: [report](.notes/YYYY-MM-DD-<scope>-audit.md) (finding F##, surfaced YYYY-MM-DD)`
