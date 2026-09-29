"""
shell-guards module (Bash|PowerShell PreToolUse): gate ANY package install or remote-execute
verb found ANYWHERE in the command string — "ask" in the main session, hard "deny" in a
sub-agent. Run by shell-guards.py, which merges its decision with the other guards'.

It closes two gaps the prefix-based permission matchers in settings.json miss:

  1. Remote execute — `npx`, `npm|pnpm|yarn exec`, `pnpm|yarn dlx`, `bunx`, `bun x`. These
     download-and-RUN registry code without ever "installing", so an `npm install:*` matcher
     never sees them. (This is how the Miasma / Shai-Hulud-class campaigns get first code
     execution.)

  2. Wrapped / env-prefixed installs — e.g. `FOO=1 npm install evil` does NOT start with
     `npm install`, so `Bash(npm install:*)` never matches it and it runs ungated. Scanning the
     whole command (not just the prefix) catches it.

Behaviour, by CALLER (the install contract is an orchestrator responsibility — a leaf the user
isn't watching must never be able to raise a prompt at them):
  - MAIN SESSION, install verb    -> "ask" + a reason that nudges --ignore-scripts for
    unfamiliar/brand-new packages (pre/post-install scripts were wave-1's delivery vector).
  - MAIN SESSION, remote-execute  -> "ask" (typo-squat warning), UNLESS it's one of a few known
    local dev tools the user runs constantly (allowlist below).
  - SUB-AGENT, either verb        -> "deny", with a reason that tells the leaf what to do
    instead (report the missing tool as a scope note / blocked verdict and finish with what
    exists). The allowlist still passes.
  - anything else                 -> None (no decision).

WHY DENY AND NOT ASK FOR A LEAF (an earned escalation — the soft version was empirically
defeated): a leaf's prompt reaches the user with no context they can adjudicate from, and a leaf
that can't resolve its own tooling keeps re-trying until the budget is gone. Denying returns a
fast, legible "you don't install — report the gap" instead of a stall. The prohibition is also
stated in prose (global CLAUDE.md, each agent body); this is its mechanism-level backstop.

Design notes:
  - Verbs are matched on text with heredoc bodies AND quoted strings / $() subshells stripped
    first (shell_text.neutralize), so the words inside an echoed string, a grep pattern, or a
    commit message don't false-trigger.
  - Fails OPEN: an exception in decide() is caught by shell-guards.py, announced, and this guard
    is skipped; the declarative npm/pip ask-rules in settings.json remain as a backstop for the
    common case — this guard is the ADDED layer for the npx / env-prefix holes those rules can't
    express.
  - Treat the allowlist as pruneable: it only suppresses the prompt for these exact local
    dev-tool runs; every real install still asks.
  - CALLER DISCRIMINATION is payload-only (hook_payload.is_subagent): `transcript_path` is always
    the PARENT session's, so only `agent_type` / `agent_id` can tell a leaf apart. If upstream
    renames them this degrades to the ask gate — fail-open by construction.
  - The literal anchors every pattern below needs (install, npm, yarn, bun, npx, add) are listed
    in run.sh's shell-guards prefilter; a new verb that needs none of them must add its anchor
    there, or it is never reached.
"""

import re

from hook_payload import is_subagent
from shell_text import neutralize

NAME = "package-install-guard"

# Package INSTALL across the ecosystems the user actually uses (mirrors the settings.json "ask"
# list, but matched anywhere in the string, not as a prefix).
INSTALL = re.compile(
    r"\b(npm|pnpm|yarn|bun)\s+(install|i|add|ci)\b"
    r"|\bpip[0-9]?\s+install\b"
    r"|\b(python[0-9]?|py)\s+-m\s+pip\s+install\b"
    r"|\buv\s+(add|pip\s+install)\b"
    r"|\bpipx\s+install\b"
    r"|\bcargo\s+install\b"
    r"|\bgo\s+install\b"
    r"|\bgem\s+install\b"
    r"|\b(winget|scoop|choco)\s+install\b"
    r"|\bclaude\s+mcp\s+add\b",
    re.IGNORECASE,
)

# Remote EXECUTE — download-and-run a package without installing it.
EXEC = re.compile(
    r"\bnpx\b"
    r"|\b(npm|pnpm|yarn)\s+exec\b"
    r"|\b(pnpm|yarn)\s+dlx\b"
    r"|\bbunx\b"
    r"|\bbun\s+x\b",
    re.IGNORECASE,
)

# Allowlist — known local dev-tool runs invoked constantly; not worth a prompt.
# Exact tool name after npx, no install subcommand. Prune/extend freely.
ALLOW = re.compile(
    r"\bnpx\s+(--no-install\s+|--prefix\s+\S+\s+)*"
    r"(tsc|svelte-check|playwright|tauri|vite|vitest)\b",
    re.IGNORECASE,
)

# The leaf-facing half of every deny reason: what to do INSTEAD. Without a stated escape hatch a
# blocked leaf improvises one (retry with a different manager, a different flag) and spins — the
# exact loop this guard exists to end. On a deny the reason reaches the model verbatim, so it is
# written as an instruction to the leaf, not a note to the user.
LEAF_ESCAPE = (
    "Sub-agents never install packages or fetch tooling — that is the main-session "
    "orchestrator's call, made once, with the user. Do NOT retry, reword, or route "
    "around this (a different package manager, --yes, a download+run) — every form is "
    "denied. Instead: finish with the tooling that already exists, and report the "
    "missing tool as a named coverage gap or a blocked verdict so the orchestrator "
    "can install it and re-dispatch you."
)

BANNER_DENY = "⛔ ballast: package-install-guard — sub-agent install/exec denied (orchestrator-only)"
BANNER_ASK = "📦 ballast: package-install-guard — install authorization contract engaged"


def _result(decision, reason):
    return {
        "decision": decision,
        "reason": reason,
        "banner": BANNER_DENY if decision == "deny" else BANNER_ASK,
    }


def decide(payload):
    """Return {"decision", "reason", "banner"} for an install / remote-exec verb, else None."""
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}
    cmd = ti.get("command") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        return None

    s = neutralize(cmd)
    inst = INSTALL.search(s)
    exe = EXEC.search(s)
    leaf = is_subagent(payload)

    if inst:
        tok = inst.group(0).strip()
        if leaf:
            return _result("deny", f"Package install ('{tok}') blocked: this is a sub-agent. {LEAF_ESCAPE}")
        return _result(
            "ask",
            f"Package install detected ('{tok}'). Verify the package name and source "
            "before approving. For an unfamiliar or brand-new package, consider adding "
            "--ignore-scripts (blocks pre/post-install scripts, the wave-1 vector) or "
            "waiting a few days for it to be vetted.")
    if exe and not ALLOW.search(s):
        tok = exe.group(0).strip()
        if leaf:
            # npx/dlx/bunx downloads BEFORE it answers, so even `<tool> --version` is remote code
            # execution, not a probe — denied for a leaf like any install.
            return _result(
                "deny",
                f"Remote package execution ('{tok}') blocked: this is a sub-agent. npx/dlx/bunx "
                f"downloads and RUNS registry code, so even a --version probe is a fetch. {LEAF_ESCAPE}")
        return _result(
            "ask",
            f"Remote package execution detected ('{tok}'). npx/dlx/bunx downloads and "
            "RUNS registry code that no install gate sees. Confirm the package name is "
            "exact (typo-squat risk) before approving.")
    return None
