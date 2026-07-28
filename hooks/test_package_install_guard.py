#!/usr/bin/env python
"""Regression tests for package-install-guard.py.

The hook filename is hyphenated (not importable), so each case invokes it as a
subprocess with the current interpreter, feeds a PreToolUse JSON payload on
stdin, and asserts on the emitted decision:

  - an install / remote-exec verb in REAL command position -> permissionDecision "ask"
  - the same verb from a SUB-AGENT payload (agent_type and/or agent_id present)
    -> permissionDecision "deny"
  - a verb inside quoted text OR a heredoc body -> silent pass (no output)
  - allowlisted local dev-tool runs -> silent pass (both callers)
  - a malformed payload -> silent pass, exit 0 (fail open)

The heredoc cases pin R2 (2026-07-08): a `git commit -F - <<'EOF' ... EOF`
message that merely DESCRIBES an install must not false-fire the gate — the hole
this report's own commit tripped.

Self-locating + standalone: `python hooks/test_package_install_guard.py`.
"""
import json
import os
import subprocess
import sys
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "package-install-guard.py")


def _run(command, **caller):
    """Invoke the hook. Extra kwargs (agent_type / agent_id) become payload fields —
    absent = the main loop, present = a sub-agent, mirroring the live payload shape."""
    payload = json.dumps(dict(
        {"tool_name": "Bash", "tool_input": {"command": command}}, **caller
    ))
    return subprocess.run(
        [sys.executable, HOOK], input=payload, capture_output=True, text=True
    )


def _decision(proc):
    """The permissionDecision string, or None if the hook stayed silent."""
    out = proc.stdout.strip()
    if not out:
        return None
    return json.loads(out).get("hookSpecificOutput", {}).get("permissionDecision")


class InstallVerbFires(unittest.TestCase):
    def test_pip_install(self):
        self.assertEqual(_decision(_run("pip install requests")), "ask")

    def test_npm_install(self):
        self.assertEqual(_decision(_run("npm install left-pad")), "ask")

    def test_env_prefixed_install(self):
        # the wrapped/env-prefix hole a `Bash(npm install:*)` prefix matcher misses
        self.assertEqual(_decision(_run("FOO=1 npm install evil")), "ask")

    def test_python_m_pip(self):
        self.assertEqual(_decision(_run("python -m pip install foo")), "ask")


class RemoteExecFires(unittest.TestCase):
    def test_npx(self):
        self.assertEqual(_decision(_run("npx create-react-app x")), "ask")

    def test_allowlisted_npx_silent(self):
        self.assertIsNone(_decision(_run("npx playwright test")))


class SubagentGetsDeniedNotAsked(unittest.TestCase):
    """A leaf never raises an install prompt at a user who can't see its context —
    the ask gate is main-session-only. Both discriminator fields are pinned
    independently: either alone must be sufficient (they arrive in different
    combinations — Task leaf, main-thread persona, forked query)."""

    def test_install_from_task_subagent(self):
        self.assertEqual(
            _decision(_run("npm install vite", agent_type="visual-reviewer", agent_id="ag_123")),
            "deny",
        )

    def test_install_with_agent_type_only(self):
        self.assertEqual(_decision(_run("pip install requests", agent_type="general-purpose")), "deny")

    def test_install_with_agent_id_only(self):
        # a forked query — still not the main loop, still can't own an install
        self.assertEqual(_decision(_run("pip install requests", agent_id="ag_456")), "deny")

    def test_remote_exec_from_subagent(self):
        self.assertEqual(_decision(_run("npx tsx --version", agent_type="visual-reviewer")), "deny")

    def test_deny_reason_names_the_escape_hatch(self):
        # a blocked leaf with no stated alternative improvises one and spins — the
        # reason text reaching the model must carry the report-the-gap instruction
        out = json.loads(_run("npm install vite", agent_type="visual-reviewer").stdout)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("coverage gap", reason)
        self.assertIn("Do NOT retry", reason)

    def test_allowlisted_npx_still_silent_for_subagent(self):
        # the allowlist is caller-independent: these are local binaries the leaf
        # is expected to drive, not a fetch
        self.assertIsNone(_decision(_run("npx playwright test", agent_type="visual-reviewer")))

    def test_benign_subagent_command_silent(self):
        self.assertIsNone(_decision(_run("ls -la", agent_type="visual-reviewer")))

    def test_empty_agent_fields_read_as_main_loop(self):
        # empty strings are not a sub-agent signal — degrade to ask, never deny
        self.assertEqual(_decision(_run("pip install requests", agent_type="", agent_id="")), "ask")


class NeutralizedTextDoesNotFire(unittest.TestCase):
    def test_quoted_commit_message(self):
        self.assertIsNone(_decision(_run('git commit -m "note: npm install stuff earlier"')))

    def test_single_quoted(self):
        self.assertIsNone(_decision(_run("echo 'pip install foo'")))

    def test_heredoc_body_quoted_delim(self):
        # R2: the exact shape this report's -F heredoc commit tripped on
        cmd = "git commit -F - <<'EOF'\nExplain: a leaf ran pip install pyyaml.\nEOF"
        self.assertIsNone(_decision(_run(cmd)))

    def test_heredoc_body_bare_delim(self):
        cmd = "cat <<EOF\nnpm install whatever\nEOF"
        self.assertIsNone(_decision(_run(cmd)))

    def test_heredoc_dash_indented_delim(self):
        cmd = "cat <<-END\n\tcargo install ripgrep\n\tEND"
        self.assertIsNone(_decision(_run(cmd)))


class FailOpenAndBenign(unittest.TestCase):
    def test_malformed_payload(self):
        # Governance review item: the fail-open path is announce-on-error, not silent -- exit
        # code is unchanged (still 0), but a systemMessage now marks the internal error visible.
        proc = subprocess.run(
            [sys.executable, HOOK], input="not json", capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertIn("systemMessage", out)
        self.assertIn("package-install-guard", out["systemMessage"])

    def test_benign_command(self):
        self.assertIsNone(_decision(_run("ls -la && git status")))

    def test_real_install_after_heredoc_still_fires(self):
        # the heredoc strip must not over-consume and swallow a REAL install verb
        # sitting AFTER the closing delimiter
        cmd = "cat <<'EOF'\njust text\nEOF\npip install realpackage"
        self.assertEqual(_decision(_run(cmd)), "ask")


if __name__ == "__main__":
    unittest.main()
