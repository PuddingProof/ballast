#!/usr/bin/env python
"""Regression tests for hooks/origin-sweep.py -- the SessionStart hook that reaps visual-stack
ORIGIN processes left behind by sessions that died before their own teardown.

The hook filename is hyphenated (not importable via a normal statement), so each case invokes it
as a subprocess with the current interpreter, feeds a SessionStart payload on stdin, and asserts on
returncode + stdout -- matching test_dev_process_nudge.py / test_inline_churn_nudge.py convention.

WHAT IS PINNED: the hook's OWN contract, not the library's reap rule (that lives in
test_visual_origin_ledger.py) -- silence when there is nothing to say, exactly one systemMessage
when it acted, announce-on-error on an unreadable process table, and exit 0 on every path
including malformed input.

HERMETIC (hooks/CLAUDE.md rule) by the same three seams the library documents -- and critically
BALLAST_VISUAL_KILL_LOG, so NO TEST HERE EVER SIGNALS A REAL PROCESS: every reap assertion is an
assertion about that log file.

Self-locating + standalone: `python hooks/test_origin_sweep.py`.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "origin-sweep.py")
sys.path.insert(0, HERE)

import visual_origin_ledger as vol  # noqa: E402

ORIGIN = {"pid": 4242, "ppid": 9000, "start_time": "2026-07-28T10:00:00.0000000Z",
          "cmdline": "node server.mjs --port 5173"}
OWNER = {"pid": 9000, "ppid": 8000, "start_time": "2026-07-28T09:00:00.0000000Z",
         "cmdline": "claude.exe --model fable"}
SHELL = {"pid": 8000, "ppid": 1, "start_time": "2026-07-28T08:00:00.0000000Z",
         "cmdline": "pwsh.exe"}


class HookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="origin-sweep-test-")
        self.kill_log = os.path.join(self.tmp, "kills.log")
        self.proc_file = os.path.join(self.tmp, "procs.json")
        self.env = dict(os.environ)
        self.env["BALLAST_CLAUDE_HOME"] = self.tmp
        self.env["BALLAST_VISUAL_KILL_LOG"] = self.kill_log
        self.env["BALLAST_VISUAL_PROC_FAKE"] = self.proc_file
        self._saved_home = os.environ.get("BALLAST_CLAUDE_HOME")
        os.environ["BALLAST_CLAUDE_HOME"] = self.tmp   # so vol.ledger_path() resolves into tmp too
        self.set_procs([ORIGIN, OWNER, SHELL])

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("BALLAST_CLAUDE_HOME", None)
        else:
            os.environ["BALLAST_CLAUDE_HOME"] = self._saved_home

    # -- helpers ------------------------------------------------------------
    def set_procs(self, procs):
        with open(self.proc_file, "w", encoding="utf-8") as f:
            json.dump(procs, f)

    def kills(self):
        if not os.path.isfile(self.kill_log):
            return []
        with open(self.kill_log, "r", encoding="utf-8") as f:
            return [ln.split()[1] for ln in f.read().splitlines() if ln.strip()]

    def entry(self, **over):
        e = {"pid": ORIGIN["pid"], "start_time": ORIGIN["start_time"],
             "cmdline_fp": vol.cmdline_fp(ORIGIN["cmdline"]),
             "url": "http://127.0.0.1:5173", "out_dir": "/tmp/out",
             "started_at": "2026-07-28T10:00:00Z",
             "owner_pid": OWNER["pid"], "owner_start_time": OWNER["start_time"]}
        e.update(over)
        return e

    def write_ledger(self, key, entries):
        path = vol.ledger_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
        return path

    def run_hook(self, payload=None):
        text = json.dumps(payload if payload is not None else {
            "hook_event_name": "SessionStart", "source": "startup",
            "session_id": "s-1", "transcript_path": "C:/u/.claude/projects/p/abc-123.jsonl",
        })
        return subprocess.run([sys.executable, HOOK], input=text, capture_output=True,
                              text=True, timeout=90, env=self.env)

    def message(self, result):
        self.assertTrue(result.stdout.strip(), "expected a systemMessage, got no output")
        payload = json.loads(result.stdout)
        return payload["systemMessage"]


class SilenceTests(HookTestCase):
    def test_absent_cache_dir_is_silent(self):
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.kills(), [])

    def test_no_ledgers_costs_no_process_enumeration(self):
        """The zero-cost claim in the hook's header, pinned as behavior rather than prose.

        A poisoned proc seam raises ProcessProbeError, which the hook ANNOUNCES. So with no
        ledgers on disk, silence proves the enumeration was never attempted -- the property a
        future edit that hoists the probe above the ledger scan would break while still passing
        every other silence test here.
        """
        os.remove(self.proc_file)
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_a_poisoned_seam_DOES_announce_once_a_ledger_exists(self):
        # The control for the test above: same poisoned seam, one ledger -> the announce fires.
        # Without this, silence above could mean "announce is broken" rather than "never probed".
        self.write_ledger("dead-sess", [self.entry()])
        os.remove(self.proc_file)
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)
        self.assertIn("process enumeration", r.stdout)

    def test_empty_cache_dir_is_silent(self):
        os.makedirs(vol.cache_dir(), exist_ok=True)
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_live_session_ledger_is_silent_and_untouched(self):
        path = self.write_ledger("other-live", [self.entry()])
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.kills(), [])

    def test_recent_all_dead_ledger_is_silent(self):
        """Nothing to kill and not yet stale -> no message, no housekeeping noise."""
        self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([SHELL])
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.kills(), [])


class ReapTests(HookTestCase):
    def test_orphan_is_reaped_and_announced_once(self):
        path = self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([ORIGIN, SHELL])            # owning session gone
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)
        msg = self.message(r)
        self.assertIn("ballast: origin-sweep", msg)
        self.assertIn("reaped 1 orphaned visual origin", msg)
        self.assertEqual(self.kills(), [str(ORIGIN["pid"])])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)

    def test_output_is_a_single_json_object_with_only_systemmessage(self):
        self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([ORIGIN, SHELL])
        payload = json.loads(self.run_hook().stdout)
        self.assertEqual(list(payload.keys()), ["systemMessage"])

    def test_pid_reuse_is_never_killed(self):
        """The hook-level restatement of the library's headline safety case."""
        self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([dict(ORIGIN, start_time="2026-07-28T22:00:00Z", cmdline="python other"),
                        SHELL])
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.kills(), [])

    def test_stale_ledger_prune_is_announced_without_a_kill(self):
        path = self.write_ledger("dead-sess", ["{not json at all"])
        os.utime(path, (time.time() - 30 * 86400,) * 2)
        r = self.run_hook()
        msg = self.message(r)
        self.assertIn("pruned 1 stale ledger entry", msg)
        self.assertIn("nothing killed", msg)
        self.assertEqual(self.kills(), [])
        self.assertFalse(os.path.exists(path))

    def test_unknown_owner_entry_is_never_reaped(self):
        path = self.write_ledger("no-owner", [self.entry(owner_pid=None, owner_start_time=None)])
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.kills(), [])
        self.assertTrue(os.path.exists(path))


class FailOpenTests(HookTestCase):
    def test_unreadable_process_table_announces_and_kills_nothing(self):
        self.write_ledger("dead-sess", [self.entry()])
        self.env["BALLAST_VISUAL_PROC_FAKE"] = os.path.join(self.tmp, "missing.json")
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)
        msg = self.message(r)
        self.assertIn("internal error (process enumeration)", msg)
        self.assertIn("nothing killed", msg)
        self.assertEqual(self.kills(), [])

    def test_malformed_stdin_exits_zero(self):
        r = subprocess.run([sys.executable, HOOK], input="{not json",
                           capture_output=True, text=True, timeout=90, env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_empty_stdin_exits_zero(self):
        r = subprocess.run([sys.executable, HOOK], input="", capture_output=True,
                           text=True, timeout=90, env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_payload_without_transcript_path_still_sweeps(self):
        """The sweep is payload-independent -- it scans every ledger, not this session's."""
        self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([ORIGIN, SHELL])
        r = self.run_hook({"hook_event_name": "SessionStart"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("reaped 1", self.message(r))

    def test_unwritable_cache_dir_never_crashes(self):
        """A ledger path that is a directory (a corrupt state dir) must fail open, not raise."""
        os.makedirs(os.path.join(vol.cache_dir(), "origins-weird.jsonl"), exist_ok=True)
        r = self.run_hook()
        self.assertEqual(r.returncode, 0)


class MessageShapeTests(unittest.TestCase):
    """build_message() is pure, so it is exercised directly for the plural/compound wording."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("origin_sweep_hook", HOOK)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_nothing_done_is_none(self):
        self.assertIsNone(self.mod.build_message({"reaped": [], "pruned": 0, "failed": []}))

    def test_singular_and_plural(self):
        one = self.mod.build_message({"reaped": [{}], "pruned": 0, "failed": []})
        two = self.mod.build_message({"reaped": [{}, {}], "pruned": 0, "failed": []})
        self.assertIn("1 orphaned visual origin (", one)
        self.assertIn("2 orphaned visual origins (", two)

    def test_compound_message_names_both_halves(self):
        msg = self.mod.build_message({"reaped": [{}], "pruned": 2, "failed": []})
        self.assertIn("reaped 1", msg)
        self.assertIn("pruned 2 stale ledger entries", msg)

    def test_failed_kill_is_surfaced(self):
        msg = self.mod.build_message({"reaped": [], "pruned": 0, "failed": [{}]})
        self.assertIn("could not be killed", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
