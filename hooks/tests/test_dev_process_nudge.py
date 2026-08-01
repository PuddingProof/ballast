#!/usr/bin/env python
"""Regression tests for dev-process-nudge.py -- the SessionStart soft-nudge that surfaces a
process left running from a PRIOR session which still references THIS project's directory.

The hook filename is hyphenated (not importable via a normal statement), so it is loaded via
importlib.util.spec_from_file_location and its pure functions / main() are exercised directly.

HERMETIC (hooks/CLAUDE.md rule): every test drives the hook through the BALLAST_DEV_PROCESS_FAKE
seam -- a JSON file of synthetic process records -- plus a temp CLAUDE_PROJECT_DIR. Live process
enumeration (the powershell/ps branches of enumerate_processes) is NEVER touched here; the seam
short-circuits before them, exactly as documented in the hook (production never sets the var).

Self-locating + standalone: `python hooks/tests/test_dev_process_nudge.py`.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev-process-nudge.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("dev_process_nudge", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


def write_fake(procs):
    """Write a synthetic process list to a temp JSON file, return its path (caller owns cleanup)."""
    fd, path = tempfile.mkstemp(prefix="dev-proc-fake-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(procs, f)
    return path


def write_raw(text):
    """Write raw (possibly malformed) text to a temp file, return its path."""
    fd, path = tempfile.mkstemp(prefix="dev-proc-raw-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


@contextlib.contextmanager
def env(**overrides):
    """Apply env overrides (value None deletes the key), restore os.environ on exit."""
    saved = dict(os.environ)
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def call_main(**overrides):
    """Run mod.main() under the given env, capturing stdout. Returns (rc, stdout_text)."""
    buf = io.StringIO()
    with env(**overrides), contextlib.redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()


class DevProcessNudge(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            try:
                os.remove(p)
            except OSError:
                pass

    def fake(self, procs):
        p = write_fake(procs)
        self._paths.append(p)
        return p

    def raw(self, text):
        p = write_raw(text)
        self._paths.append(p)
        return p

    # (a) No CLAUDE_PROJECT_DIR -> nothing to compare against; silent, exit 0.
    def test_no_project_dir_no_output(self):
        # A fake process list IS provided, proving the silence is due to the missing project dir,
        # not an empty enumeration.
        fake = self.fake([{"pid": 1, "name": "node.exe", "cmdline": r"C:\proj\ballast\x node"}])
        rc, out = call_main(CLAUDE_PROJECT_DIR=None, BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    # (b) The only project-path match is an excluded editor -> suppressed; no output.
    def test_excluded_editor_only_no_output(self):
        fake = self.fake([
            {"pid": 10, "name": "Code.exe", "cmdline": r"C:\proj\ballast --open"},
            {"pid": 11, "name": "chrome.exe", "cmdline": r"C:\somewhere\else --unrelated"},
        ])
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    # (c) node.exe cmdline holds the backslash-form project dir while CLAUDE_PROJECT_DIR is
    #     forward-slash -> cross-separator match; emits JSON carrying the count and the PID.
    def test_node_backslash_cmdline_matches_forward_slash_project(self):
        fake = self.fake([
            {"pid": 4242, "name": "node.exe", "cmdline": r"C:\proj\ballast\node_modules\.bin\vite"},
        ])
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("dev-process-nudge", payload["systemMessage"])
        self.assertIn("1", payload["systemMessage"])  # the count
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("4242", payload["hookSpecificOutput"]["additionalContext"])  # the PID

    # (d) More than 8 matches -> additionalContext lists exactly 8, count reflects the true total.
    def test_more_than_eight_matches_lists_exactly_eight(self):
        procs = [
            {"pid": 5000 + i, "name": "node.exe", "cmdline": r"C:\proj\ballast\srv-%d" % i}
            for i in range(11)
        ]
        fake = self.fake(procs)
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        listed = [
            ln for ln in payload["hookSpecificOutput"]["additionalContext"].splitlines()
            if ln.startswith("PID ")
        ]
        self.assertEqual(len(listed), 8)
        self.assertIn("11", payload["systemMessage"])  # true total, not the capped list length

    # (g) Self-exclusion: a fake proc carrying THIS test process's own pid (as the seam delivers
    #     it, i.e. an int -- exercising the string-compare normalization) and a matching cmdline
    #     must NOT be listed, even though it would otherwise match on cmdline alone.
    def test_self_pid_excluded_even_with_matching_cmdline(self):
        fake = self.fake([
            {"pid": os.getpid(), "name": "python.exe", "cmdline": r"C:\proj\ballast\hooks\dev-process-nudge.py"},
            {"pid": 9999, "name": "node.exe", "cmdline": r"C:\proj\ballast\node_modules\.bin\vite"},
        ])
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("9999", payload["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn(str(os.getpid()), payload["hookSpecificOutput"]["additionalContext"])
        self.assertIn("1", payload["systemMessage"])  # only the non-self match counted

    # (h) A cmdline longer than CMDLINE_TRUNC (120) is sliced and the cut is marked with an
    #     ellipsis, so a truncated path never reads as complete.
    def test_long_cmdline_truncated_with_ellipsis(self):
        long_cmdline = r"C:\proj\ballast\node_modules\.bin\vite" + "x" * 100
        fake = self.fake([{"pid": 6161, "name": "node.exe", "cmdline": long_cmdline}])
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=fake)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        listed = [
            ln for ln in payload["hookSpecificOutput"]["additionalContext"].splitlines()
            if ln.startswith("PID ")
        ]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0].endswith("…"))
        self.assertNotIn(long_cmdline, listed[0])  # confirms it was actually truncated

    # (e) Malformed fake JSON -> enumerate_processes raises, main() fails open silently, exit 0.
    def test_malformed_fake_json_fails_open_silently(self):
        bad = self.raw("not json{{{")
        rc, out = call_main(CLAUDE_PROJECT_DIR="C:/proj/ballast", BALLAST_DEV_PROCESS_FAKE=bad)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    # (f) Subprocess path -- the hook run as its own process must exit 0 in BOTH the signal and
    #     no-signal cases (this is how run.sh actually invokes it).
    def _run_subprocess(self, extra_env):
        e = dict(os.environ)
        e.update(extra_env)
        return subprocess.run(
            [sys.executable, HOOK], capture_output=True, text=True, timeout=30, env=e
        )

    def test_subprocess_no_signal_exits_zero_no_output(self):
        e = dict(os.environ)
        e.pop("CLAUDE_PROJECT_DIR", None)
        fake = self.fake([{"pid": 1, "name": "node.exe", "cmdline": "unrelated"}])
        e["BALLAST_DEV_PROCESS_FAKE"] = fake
        proc = subprocess.run(
            [sys.executable, HOOK], capture_output=True, text=True, timeout=30, env=e
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_subprocess_signal_exits_zero_with_json(self):
        fake = self.fake([
            {"pid": 777, "name": "node.exe", "cmdline": r"C:\proj\ballast\node_modules\.bin\vite"},
        ])
        proc = self._run_subprocess({
            "CLAUDE_PROJECT_DIR": "C:/proj/ballast",
            "BALLAST_DEV_PROCESS_FAKE": fake,
        })
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("777", payload["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
