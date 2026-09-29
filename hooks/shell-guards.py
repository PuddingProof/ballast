#!/usr/bin/env python
"""shell-guards.py -- the one PreToolUse hook process for the Bash|PowerShell matcher.

WHY ONE PROCESS: every Bash/PowerShell call used to start a separate hook process (and python)
per guard, even when the command could not match any of them; a timed-out PreToolUse hook is
cancelled and the tool proceeds, so under load a supply-chain guard silently went dark. Now
run.sh screens the raw payload with a builtin `case` prefilter (zero forks on a non-match) and
only a possible match starts this process, which runs every guard once and merges the answers.

GUARDS, in order (each a sibling module exposing NAME and decide(payload) -> dict | None, where
the dict is {"decision": "deny"|"ask"|"allow", "reason": str, "banner": str|None}):
  - process_lifecycle_guard -- leaf-only hard deny on starting/backgrounding/signalling a process;
  - package_install_guard   -- install / remote-exec: ask in the main session, deny in a leaf;
  - ballast_allow           -- opt-in self-allow of ballast's own bare shims.

MERGE: any deny wins (the deny reasons joined), else ask, else allow -- the harness's own
multi-hook precedence for permissionDecision. So an allow can never override another guard's ask
(`ballast-extract npm install` asks). One hookSpecificOutput is emitted, plus a systemMessage of
every fired guard's banner joined by ` · `. A single guard's fire is byte-identical to what that
guard emitted as its own hook.

FAIL-OPEN GUARANTEE: exit 0 on every path. A malformed or non-object payload announces
`⚠️ ballast: shell-guards — internal error (payload parse), ...` and decides nothing. Each guard
is imported and called inside its own try block: a guard that fails to import or raises announces
`⚠️ ballast: <NAME> — internal error, skipped` and the others still decide.

ACCEPTED RESIDUAL: one process means a hang in one guard starves all three. The guards are pure
regex plus one stat; none does network, git, or transcript I/O.
"""
import importlib
import json
import os
import sys

HOOK_NAME = "shell-guards"
GUARDS = ("process_lifecycle_guard", "package_install_guard", "ballast_allow")
_RANK = {"deny": 3, "ask": 2, "allow": 1}

# The guard modules are siblings of this file; the hook runs as a script, so its own dir is on
# sys.path already -- inserted explicitly so an importlib load from a test sees the same modules.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def decide_all(payload, guards=GUARDS):
    """Run each guard on the payload and merge. Returns the hook output dict, or None if silent."""
    results, messages = [], []
    for modname in guards:
        name = modname.replace("_", "-")
        try:
            mod = importlib.import_module(modname)
            name = getattr(mod, "NAME", name)
            r = mod.decide(payload)
            if r is not None and r.get("decision") not in _RANK:
                raise ValueError("unknown decision %r" % (r.get("decision"),))
        except Exception:
            messages.append("⚠️ ballast: %s — internal error, skipped" % name)
            continue
        if r is None:
            continue
        results.append(r)
        if r.get("banner"):
            messages.append(r["banner"])

    out = {}
    if messages:
        out["systemMessage"] = " · ".join(messages)
    if results:
        top = max(_RANK[r["decision"]] for r in results)
        winners = [r for r in results if _RANK[r["decision"]] == top]
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            # permissionDecision must sit INSIDE hookSpecificOutput (a top-level copy is inert).
            "permissionDecision": winners[0]["decision"],
            "permissionDecisionReason": "\n".join(r["reason"] for r in winners),
        }
    return out or None


def main():
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            print(json.dumps({
                "systemMessage": "⚠️ ballast: %s — internal error (payload parse), all guards "
                                 "skipped; deferred to native permission flow" % HOOK_NAME,
            }))
            return 0
        out = decide_all(payload)
        if out:
            print(json.dumps(out))
    except Exception:
        # Last-resort fail-open: announce best-effort, never change the exit code.
        try:
            print(json.dumps({
                "systemMessage": "⚠️ ballast: %s — internal error (dispatch), all guards skipped"
                                 % HOOK_NAME,
            }))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
