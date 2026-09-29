"""Shared hook-payload helpers: caller discrimination.

Pure functions, no I/O at import. Imported by package_install_guard.py, process_lifecycle_guard.py,
inline-churn-nudge.py, and visual-arm.py -- the single home of is_subagent().
"""


def is_subagent(payload):
    """True when this fire originates anywhere other than the main loop.

    Two payload fields carry the origin (binary-verified on PreToolUse, CC v2.1.220; re-verified
    fire-side on PostToolUse, same version):
      - `agent_type` — the subagent_type string of a dispatched agent.
      - `agent_id`   — ABSENT on the main loop, present on any nested agent at any depth
                       (a 3rd-layer leaf looks like a 1st).

    Deliberately the OR, not the AND: both present = a real Task sub-agent; agent_type only =
    a main-thread agent persona; agent_id only = a forked query. All three are contexts the
    user is not watching.

    Any parse surprise (a non-string field, a mangled payload) resolves False = main session.
    That is the safe direction for the shell guards (ask gate / no-op, never denying an attended
    user) but the wrong one for arming or nudging, which is why visual-arm and inline-churn-nudge
    wrap this in their own caller_class() degraded-mode guard. Both fields are
    harness-version-volatile: if upstream renames them the predicate goes False everywhere.
    """
    try:
        return bool(
            str(payload.get("agent_type") or "").strip()
            or str(payload.get("agent_id") or "").strip()
        )
    except Exception:
        return False
