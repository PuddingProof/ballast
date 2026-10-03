# Audit report template — `.claude/adversarial-audit/YYYY-MM-DD-<scope>-audit.md`

Opened at step 3 with the Findings table; the rest fills in as it happens, BLUF last. Drop any section that didn't happen.

```markdown
# <Project> <scope> audit — YYYY-MM-DD

**Level:** --light · **Scope:** <paths, or base..head> · **Base:** <sha the fixes landed on>

## BLUF
<3–6 lines: high-altitude impressions and overall feedback on the codebase, what was audited, and what shipped.>

## Findings
| ID | Sev | Check | Finding | Anchor | Disposition |
|---|---|---|---|---|---|
| F01 | 🔴 | ✅ | <one line> | `file · symbol` | 🚢 c1a2b3 |

<For each 🔴/🟠: **F01 <lens> · ✅** — evidence and failure scenario — fix.>

Severity 🔴 critical · 🟠 high · 🟡 medium · ⚪ nit. Check ✅ confirmed · 🟡ᵛ plausible · ❌ refuted. Disposition 🚢 fixed · 🚫 rejected · 📥 deferred · 🎨 user call.

## Rejected
| ID | Why |
<Every 🚫 with its reason. Future audits treat these as settled.>

## Commits
| Checkpoint | Commit | Scope | Gate |
|---|---|---|---|
| A — <area> | c1a2b3 | <paths> | <tests · review> |

## Method
<Lenses and leaf types · verifier count and kill rate · diff-review result on the fixes.>
```

If BACKLOG.md, IDEAS.md, or similar files exist, follow project conventions to add stubs for 📥 items.
