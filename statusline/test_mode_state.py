#!/usr/bin/env python3
"""Hermetic tests for statusline/mode-state.py.

stdlib unittest only (no pytest dep, per hooks/CLAUDE.md test conventions).
mode-state.py has a hyphenated filename (not import-able as a module), so
every test drives it as a subprocess via sys.executable -- the same way a
real caller (bin/ballast-mode, or a hook shelling to $BALLAST_PYTHON) would.

Every test sets BALLAST_CLAUDE_HOME to a fresh tempdir so this suite never
reads or writes the developer's real ~/.claude state (mirrors the hermetic
convention in hooks/test_commit_review_gate.py).
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "mode-state.py"


def run_cli(args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        env=env,
        capture_output=True,
        text=True,
    )


class ModeStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = dict(os.environ)
        self.env["BALLAST_CLAUDE_HOME"] = self._tmp.name
        # A syntactically-valid session id: hex digits + hyphens, len 8-64.
        self.sid = "aabbccdd-1234-5678-9abc-def012345678"

    # -- helpers -----------------------------------------------------------

    def state_dir(self):
        return Path(self._tmp.name) / "ballast" / "modes"

    def state_file(self, sid=None):
        return self.state_dir() / (sid or self.sid)

    def read_lines(self, sid=None):
        p = self.state_file(sid)
        if not p.exists():
            return []
        return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def assert_rejected(self, args):
        """Run args, assert exit 1 + non-empty stderr + nothing created on disk."""
        before = set(self.state_dir().glob("*")) if self.state_dir().exists() else set()
        r = run_cli(args, self.env)
        self.assertEqual(r.returncode, 1, "stdout={!r} stderr={!r}".format(r.stdout, r.stderr))
        self.assertTrue(r.stderr.strip(), "expected a short stderr message")
        self.assertEqual(r.stdout, "", "stdout must stay empty on a validation failure")
        after = set(self.state_dir().glob("*")) if self.state_dir().exists() else set()
        self.assertEqual(before, after, "validation failure must not touch disk")

    # -- raise ---------------------------------------------------------------

    def test_raise_creates_pending_entry(self):
        r = run_cli(["raise", "freehand", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.read_lines()
        self.assertEqual(len(lines), 1)
        mode, status, epoch = lines[0].split()
        self.assertEqual(mode, "freehand")
        self.assertEqual(status, "pending")
        self.assertTrue(epoch.isdigit())

    def test_raise_confirmed_flag(self):
        r = run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        mode, status, epoch = self.read_lines()[0].split()
        self.assertEqual(status, "confirmed")

    def test_pending_raise_never_demotes_confirmed(self):
        # The keyword hook re-arms (raise pending) on every matching prompt, including mere
        # mentions while a genuine grant stands -- a pending raise over an existing confirmed
        # entry must keep it confirmed (observed live 2026-07-11), only refreshing the epoch.
        run_cli(["raise", "autopilot", "--session", self.sid, "--confirmed"], self.env)
        r = run_cli(["raise", "autopilot", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        mode, status, epoch = self.read_lines()[0].split()
        self.assertEqual(status, "confirmed", "pending re-arm demoted a confirmed chip")

    # -- confirm ---------------------------------------------------------------

    def test_confirm_flips_pending_to_confirmed(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        r = run_cli(["confirm", "freehand", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.read_lines()
        self.assertEqual(len(lines), 1)
        mode, status, epoch = lines[0].split()
        self.assertEqual(status, "confirmed")

    def test_confirm_on_missing_mode_raises_confirmed(self):
        r = run_cli(["confirm", "autopilot", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.read_lines()
        self.assertEqual(len(lines), 1)
        mode, status, epoch = lines[0].split()
        self.assertEqual(mode, "autopilot")
        self.assertEqual(status, "confirmed")

    # -- clear ---------------------------------------------------------------

    def test_clear_removes_one_mode_leaving_others(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        r = run_cli(["clear", "freehand", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        modes = [l.split()[0] for l in self.read_lines()]
        self.assertEqual(modes, ["exec"])

    def test_clear_nonexistent_mode_is_idempotent_noop(self):
        run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        r = run_cli(["clear", "freehand", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        modes = [l.split()[0] for l in self.read_lines()]
        self.assertEqual(modes, ["exec"])

    def test_clear_all(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        r = run_cli(["clear", "--all", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_lines(), [])

    # -- clear --pending-only (F4: guarded clear, never revokes a standing confirmed grant) -------

    def test_clear_pending_only_removes_pending(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)  # pending
        r = run_cli(["clear", "freehand", "--pending-only", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l.split()[0] for l in self.read_lines()], [],
                         "a pending entry should be cleared by --pending-only")

    def test_clear_pending_only_preserves_confirmed(self):
        # The seam this flag exists for: a mere-mention settle must NOT retract a standing grant.
        run_cli(["confirm", "freehand", "--session", self.sid], self.env)  # confirmed
        r = run_cli(["clear", "freehand", "--pending-only", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        mode, status, epoch = self.read_lines()[0].split()
        self.assertEqual((mode, status), ("freehand", "confirmed"),
                         "--pending-only must leave a confirmed grant untouched")

    def test_bare_clear_still_deletes_confirmed(self):
        # Pin the pre-existing behavior: a bare clear (no --pending-only) removes a confirmed entry.
        run_cli(["confirm", "freehand", "--session", self.sid], self.env)  # confirmed
        r = run_cli(["clear", "freehand", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l.split()[0] for l in self.read_lines()], [],
                         "a bare clear must still delete a confirmed entry")

    def test_clear_pending_only_with_all_is_rejected(self):
        run_cli(["confirm", "freehand", "--session", self.sid], self.env)
        self.assert_rejected(["clear", "--all", "--pending-only", "--session", self.sid])

    # -- cleanup ---------------------------------------------------------------

    def test_cleanup_deletes_session_file(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        self.assertTrue(self.state_file().exists())
        r = run_cli(["cleanup", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.state_file().exists())

    def test_cleanup_gc_old_sibling_but_keeps_fresh(self):
        old_sid = "11223344-5566-7788-99aa-bbccddeeff00"
        fresh_sid = "22334455-6677-8899-aabb-ccddeeff0011"
        run_cli(["raise", "freehand", "--session", old_sid], self.env)
        run_cli(["raise", "freehand", "--session", fresh_sid], self.env)

        old_file = self.state_file(old_sid)
        fresh_file = self.state_file(fresh_sid)
        self.assertTrue(old_file.exists())
        self.assertTrue(fresh_file.exists())

        stale_time = time.time() - (8 * 24 * 3600)  # > 7-day GC horizon
        os.utime(old_file, (stale_time, stale_time))

        r = run_cli(["cleanup", "--session", self.sid], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old_file.exists(), "stale (>7d) sibling should be GC'd")
        self.assertTrue(fresh_file.exists(), "fresh sibling should survive GC")

    # -- per-session keying ---------------------------------------------------------------

    def test_per_session_keying(self):
        sid2 = "99887766-5544-3322-1100-ffeeddccbbaa"
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        run_cli(["raise", "exec", "--session", sid2, "--confirmed"], self.env)
        lines1 = self.read_lines()
        lines2 = self.read_lines(sid2)
        self.assertEqual(len(lines1), 1)
        self.assertEqual(len(lines2), 1)
        self.assertEqual(lines1[0].split()[0], "freehand")
        self.assertEqual(lines2[0].split()[0], "exec")

    # -- validation: mode ---------------------------------------------------------------

    def test_validation_rejects_bad_mode_chars(self):
        self.assert_rejected(["raise", "free_hand!", "--session", self.sid])

    def test_validation_rejects_uppercase_mode(self):
        self.assert_rejected(["raise", "Freehand", "--session", self.sid])

    def test_validation_rejects_overlong_mode(self):
        self.assert_rejected(["raise", "a" * 33, "--session", self.sid])

    # -- validation: session id (path-traversal boundary) ---------------------------------

    def test_validation_rejects_sid_traversal_unix_style(self):
        self.assert_rejected(["raise", "freehand", "--session", "../evil"])

    def test_validation_rejects_sid_traversal_windows_style(self):
        self.assert_rejected(["raise", "freehand", "--session", "..\\evil"])

    def test_validation_rejects_sid_with_forward_slash(self):
        self.assert_rejected(["raise", "freehand", "--session", "abcd1234/ef"])

    def test_validation_rejects_sid_with_backslash(self):
        self.assert_rejected(["raise", "freehand", "--session", "abcd1234\\ef"])

    def test_validation_rejects_sid_with_dotdot(self):
        self.assert_rejected(["raise", "freehand", "--session", "abcdef12..3456"])

    def test_validation_rejects_empty_sid(self):
        self.assert_rejected(["raise", "freehand", "--session", ""])

    def test_validation_rejects_nonhex_sid_chars(self):
        # 8 chars satisfies the length bound but g/h/i/j/k/l/m/n aren't hex digits.
        self.assert_rejected(["raise", "freehand", "--session", "ghijklmn"])

    # -- atomicity ---------------------------------------------------------------

    def test_atomicity_smoke_no_partial_content_or_leftover_tmp(self):
        run_cli(["raise", "freehand", "--session", self.sid], self.env)
        r = run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        content = self.state_file().read_text(encoding="utf-8")
        for line in content.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            self.assertEqual(len(parts), 3, "line must be fully-formed, never partial: {!r}".format(line))
        leftover = [p.name for p in self.state_dir().iterdir() if p.name != self.sid]
        self.assertEqual(leftover, [], "no tmp files should remain after a successful write")

    # -- malformed existing state file ---------------------------------------------------------------

    def test_malformed_existing_state_file_does_not_crash_next_raise(self):
        d = self.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = self.state_file()
        p.write_text("this is not a valid line at all\nfreehand pending 12345\n", encoding="utf-8")

        r = run_cli(["raise", "exec", "--session", self.sid, "--confirmed"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)

        modes = {l.split()[0] for l in self.read_lines()}
        self.assertIn("exec", modes, "the new raise must still land")
        self.assertIn("freehand", modes, "the pre-existing valid line must be preserved")
        self.assertEqual(len(modes), 2, "the garbage line must be dropped, not crash or duplicate")


if __name__ == "__main__":
    unittest.main()
