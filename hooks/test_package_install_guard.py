#!/usr/bin/env python
"""Regression tests for package-install-guard.py.

The hook filename is hyphenated (not importable), so each case invokes it as a
subprocess with the current interpreter, feeds a PreToolUse JSON payload on
stdin, and asserts on the emitted decision:

  - an install / remote-exec verb in REAL command position -> permissionDecision "ask"
  - a verb inside quoted text OR a heredoc body -> silent pass (no output)
  - allowlisted local dev-tool runs -> silent pass
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


def _run(command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
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
