# Audit report template — `.claude/adversarial-audit/YYYY-MM-DD-<scope>-audit.md`

Fill top-down; write the BLUF last. Drop any section that didn't happen.

```markdown
# <Project> <scope> audit — YYYY-MM-DD

**Level:** --light · **Scope:** <paths, or base..head> · **Base:** <sha the next audit starts from>

## BLUF
<3–6 lines: what was audited, findings raw → merged → confirmed, what shipped, 🎨 items waiting on the user.>

## Findings
| ID | Sev | Check | Finding | Anchor | Disposition |
|---|---|---|---|---|---|
| F01 | 🔴 | ✅ | <one line> | `file · symbol` | 🚢 c1a2b3 |

<For each 🔴/🟠: **F01 <lens> · ✅** — evidence and failure scenario — fix.>

Severity 🔴 critical · 🟠 high · 🟡 medium · ⚪ nit. Check ✅ confirmed · 🟡ᵛ plausible · ❌ refuted. Disposition 🚢 fixed · 🚫 rejected · 📥 deferred · 🎨 user call.

## Rejected
| ID | Why |
<Every 🚫 with its reason. Future audits treat these as settled.>

## Method
<Lenses and leaf types · verifier count and kill rate · diff-review result on the fixes · commits.>
```

IDEAS.md stub for a 📥 item: `- [ ] **Title** — rationale. Seed: [report](.claude/adversarial-audit/YYYY-MM-DD-<scope>-audit.md) F##`
