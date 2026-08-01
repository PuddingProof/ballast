#!/usr/bin/env python
"""
PreToolUse guard (Bash|PowerShell): gate ANY package install or remote-execute
verb found ANYWHERE in the command string — "ask" in the main session, hard
"deny" in a sub-agent.

It closes two gaps the prefix-based permission matchers in settings.json miss:

  1. Remote execute — `npx`, `npm|pnpm|yarn exec`, `pnpm|yarn dlx`, `bunx`,
     `bun x`. These download-and-RUN registry code without ever "installing",
     so an `npm install:*` matcher never sees them. (This is how the Miasma /
     Shai-Hulud-class campaigns get first code execution.)

  2. Wrapped / env-prefixed installs — e.g. `FOO=1 npm install evil` does NOT
     start with `npm install`, so `Bash(npm install:*)` never matches it and it
     runs ungated. Scanning the whole command (not just the prefix) catches it.

Behaviour, by CALLER (the install contract is an orchestrator responsibility —
a leaf the user isn't watching must never be able to raise a prompt at them):
  - MAIN SESSION, install verb    -> permissionDecision "ask" + a reason that
    nudges --ignore-scripts for unfamiliar/brand-new packages (pre/post-install
    scripts were wave-1's delivery vector).
  - MAIN SESSION, remote-execute  -> "ask" (typo-squat warning), UNLESS it's one
    of a few known local dev tools the user runs constantly (allowlist below).
  - SUB-AGENT, either verb        -> "deny", with a reason that tells the leaf
    what to do instead (report the missing tool as a scope note / blocked
    verdict and finish with what exists). The allowlist still passes.
  - anything else                 -> silent pass-through (exit 0, no output).

WHY DENY AND NOT ASK FOR A LEAF (an earned escalation — the soft version was
empirically defeated): a leaf's prompt reaches the user with no context they can
adjudicate from, and a leaf that can't resolve its own tooling keeps re-trying
until the budget is gone. Denying returns a fast, legible "you don't install —
report the gap" instead of a stall. The prohibition is already stated in prose
(global CLAUDE.md, the fanout injection, each agent body); this is its
mechanism-level backstop.

Design notes:
  - Verbs are matched on text with heredoc bodies AND quoted strings / $()
    subshells stripped first, so the words inside an echoed string, a grep
    pattern, or a commit message -- whether quoted (`-m "npm install ..."`) or
    fed through a `git commit -F - <<EOF ... EOF` heredoc -- don't false-trigger.
    (The quote-strip mirrors block-compound-commands.sh.)
  - Fails OPEN (exit 0) on any parse error: a hook bug must never block every
    Bash/PowerShell call, and the declarative npm/pip ask-rules in settings.json
    remain as a backstop for the common case -- this hook is the ADDED layer for
    the npx / env-prefix holes those rules can't express. Not SILENT, though:
    before exiting 0 it makes a best-effort systemMessage announcement, wrapped
    in its own try/except so it can never change the exit code.
  - Treat the allowlist as pruneable: it only suppresses the prompt for these
    exact local dev-tool runs; every real install still asks.
  - CALLER DISCRIMINATION is payload-only: there is no env-var discriminator on
    the hook path and `transcript_path` is always the PARENT session's, leaving
    two binary-verified payload fields (v2.1.220), `agent_type` and `agent_id` —
    see is_subagent() for the predicate and its caveats. Both are
    harness-version-volatile: if upstream renames them the predicate goes False
    and this degrades to the pre-existing ask gate -- fail-open by construction,
    with the prose prohibition as the backstop.
"""

import sys
import json
import re

# --- read the hook payload ------------------------------------------------
try:
    payload = json.load(sys.stdin)
except Exception:
    # Fail open — never block on a malformed payload. Announce-on-error: best-effort, wrapped in
    # its own try/except so the announce can never change the exit code -- a crashed guard must
    # stay visible instead of silently going dead every call.
    try:
        print(json.dumps({
            "systemMessage": "⚠️ ballast: package-install-guard — internal error, guard skipped; install checks deferred to native permission flow",
        }))
    except Exception:
        pass
    sys.exit(0)

cmd = (payload.get("tool_input", {}) or {}).get("command", "") or ""
if not cmd.strip():
    sys.exit(0)

# --- strip heredoc bodies + quoted strings + $() subshells so verbs inside
#     literals / message bodies don't fire -----------------------------------
s = cmd
# Shell wrappers whose quoted body IS the real command, unwrapped BEFORE the quote strippers
# below -- otherwise `bash -c "npm install evil-pkg"` has its whole body erased as a quoted
# literal and the install verb never reaches INSTALL/EXEC.  Back-ported from
# process-lifecycle-guard.py, which hit this laundering class first for its own verb set.
UNWRAP = re.compile(
    r"\b(?:bash|sh|zsh|dash|pwsh|powershell(?:\.exe)?|cmd(?:\.exe)?)\b"
    r"[^'\"]{0,40}?\s(?:-c|-Command|/c|/C)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
s = UNWRAP.sub(lambda m: " " + m.group(2) + " ", s)
# Heredoc bodies (<<DELIM ... DELIM, incl. <<'DELIM' / <<"DELIM" / <<-DELIM) are
# raw text, not command -- a `git commit -F - <<'EOF' ... EOF` message that only
# DESCRIBES an install must not fire the gate. Stripped FIRST: a quoted delimiter
# (<<'EOF') would otherwise be mangled by the quote strippers below, breaking the
# closing-delimiter match. The closing delimiter is matched at line start
# (MULTILINE); with no closing line the pattern simply doesn't match and the body
# is left as-is (fail-safe).
s = re.sub(
    r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1.*?^[ \t]*\2[ \t]*$",
    "",
    s,
    flags=re.DOTALL | re.MULTILINE,
)
s = re.sub(r'""".*?"""', "", s, flags=re.DOTALL)   # triple-quoted
s = re.sub(r"\$\(.*?\)", "", s, flags=re.DOTALL)    # $(...) subshells
s = re.sub(r'"[^"]*"', "", s)                        # "double"
s = re.sub(r"'[^']*'", "", s)                        # 'single'

# --- the verb patterns ----------------------------------------------------
# Package INSTALL across the ecosystems the user actually uses (mirrors the
# settings.json "ask" list, but matched anywhere in the string, not as a prefix).
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


def is_subagent() -> bool:
    """True when this Bash call originates anywhere other than the main loop.

    Two payload fields carry the origin (binary-verified, CC v2.1.220):
      - `agent_type` — the subagent_type string of a dispatched agent.
      - `agent_id`   — ABSENT on the main loop, present on any nested agent,
                       at any depth (a 3rd-layer leaf looks like a 1st).

    Deliberately the OR, not the AND: both present = a real Task sub-agent;
    agent_type only = a main-thread agent persona; agent_id only = a forked
    query. All three are contexts the user isn't watching, which is exactly the
    set that must not raise an install prompt.

    Any parse surprise (a non-string field, a mangled payload) resolves False,
    degrading to the ask gate rather than denying the main session -- fail-open
    in the direction that can't brick a session.
    """
    try:
        return bool(
            str(payload.get("agent_type") or "").strip()
            or str(payload.get("agent_id") or "").strip()
        )
    except Exception:
        return False


def emit(decision: str, reason: str) -> None:
    """Print the PreToolUse decision JSON ("ask" prompts the user; "deny" blocks).

    `permissionDecision` must sit INSIDE hookSpecificOutput (a top-level copy is
    inert), and on a deny the reason is delivered verbatim to the calling model
    -- so it's written as an instruction to the leaf, not a note to the user.
    """
    banner = (
        "⛔ ballast: package-install-guard — sub-agent install/exec denied (orchestrator-only)"
        if decision == "deny"
        else "📦 ballast: package-install-guard — install authorization contract engaged"
    )
    print(json.dumps({
        "systemMessage": banner,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))


# The leaf-facing half of every deny reason: what to do INSTEAD. Without a stated
# escape hatch a blocked leaf improvises one (retry with a different manager, a
# different flag) and spins -- the exact loop this guard exists to end.
LEAF_ESCAPE = (
    "Sub-agents never install packages or fetch tooling — that is the main-session "
    "orchestrator's call, made once, with the user. Do NOT retry, reword, or route "
    "around this (a different package manager, --yes, a download+run) — every form is "
    "denied. Instead: finish with the tooling that already exists, and report the "
    "missing tool as a named coverage gap or a blocked verdict so the orchestrator "
    "can install it and re-dispatch you."
)

inst = INSTALL.search(s)
exe = EXEC.search(s)
leaf = is_subagent()

if inst:
    tok = inst.group(0).strip()
    if leaf:
        emit("deny", f"Package install ('{tok}') blocked: this is a sub-agent. {LEAF_ESCAPE}")
    else:
        emit("ask",
             f"Package install detected ('{tok}'). Verify the package name and source "
             "before approving. For an unfamiliar or brand-new package, consider adding "
             "--ignore-scripts (blocks pre/post-install scripts, the wave-1 vector) or "
             "waiting a few days for it to be vetted.")
elif exe and not ALLOW.search(s):
    tok = exe.group(0).strip()
    if leaf:
        # npx/dlx/bunx downloads BEFORE it answers, so even `<tool> --version` is
        # remote code execution, not a probe -- denied for a leaf like any install.
        emit("deny",
             f"Remote package execution ('{tok}') blocked: this is a sub-agent. npx/dlx/bunx "
             f"downloads and RUNS registry code, so even a --version probe is a fetch. {LEAF_ESCAPE}")
    else:
        emit("ask",
             f"Remote package execution detected ('{tok}'). npx/dlx/bunx downloads and "
             "RUNS registry code that no install gate sees. Confirm the package name is "
             "exact (typo-squat risk) before approving.")

# else: silent pass-through
sys.exit(0)
