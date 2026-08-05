#!/usr/bin/env python
"""Regression tests for hooks/visual_origin_ledger.py -- the visual-stack origin ledger library
and its bin/ CLI (record | list | teardown).

WHAT IS PINNED HERE is the kill decision, from both directions: an entry is reaped ONLY when the
recorded process still verifies (pid + start_time + command-line fingerprint) AND its recording
session's own process is verifiably gone; everything short of that proof prunes or is left alone.
The PID-reuse row is the load-bearing one -- it is the case where a bare-pid implementation would
kill an innocent process.

HERMETIC (hooks/CLAUDE.md rule), by three seams, none of which production ever sets:
  BALLAST_CLAUDE_HOME      -- redirects the ledger dir into a per-test tmp dir.
  BALLAST_VISUAL_PROC_FAKE -- supplies a fabricated process table (JSON file), so no test depends
                              on, or inspects, the real process list.
  BALLAST_VISUAL_KILL_LOG  -- records kills to a file instead of executing them. NOTHING IN THIS
                              SUITE EVER SIGNALS A REAL PROCESS; every "reaped" assertion is an
                              assertion about that log.

Self-locating + standalone: `python hooks/tests/test_visual_origin_ledger.py`.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # hooks/ — one dir up from tests/
sys.path.insert(0, HOOKS)

import visual_origin_ledger as vol  # noqa: E402

SHIM = os.path.join(HOOKS, "..", "bin", "ballast-visual-origin")

# A synthetic session: an origin process, the claude process that started it, and a shell layer.
ORIGIN = {"pid": 4242, "ppid": 9000, "start_time": "2026-07-28T10:00:00.0000000Z",
          "cmdline": "node server.mjs --port 5173"}
OWNER = {"pid": 9000, "ppid": 8000, "start_time": "2026-07-28T09:00:00.0000000Z",
         "cmdline": "claude.exe --model fable"}
SHELL = {"pid": 8000, "ppid": 1, "start_time": "2026-07-28T08:00:00.0000000Z",
         "cmdline": "pwsh.exe"}


class LedgerTestCase(unittest.TestCase):
    """Each test gets a private ledger home, process table, and kill log."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="origin-ledger-test-")
        self._saved = {k: os.environ.get(k) for k in
                       ("BALLAST_CLAUDE_HOME", "BALLAST_VISUAL_PROC_FAKE", "BALLAST_VISUAL_KILL_LOG")}
        self.kill_log = os.path.join(self.tmp, "kills.log")
        self.proc_file = os.path.join(self.tmp, "procs.json")
        os.environ["BALLAST_CLAUDE_HOME"] = self.tmp
        os.environ["BALLAST_VISUAL_KILL_LOG"] = self.kill_log
        os.environ["BALLAST_VISUAL_PROC_FAKE"] = self.proc_file
        self.set_procs([ORIGIN, OWNER, SHELL])

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- helpers ------------------------------------------------------------
    def set_procs(self, procs):
        with open(self.proc_file, "w", encoding="utf-8") as f:
            json.dump(procs, f)

    def kills(self):
        if not os.path.isfile(self.kill_log):
            return []
        with open(self.kill_log, "r", encoding="utf-8") as f:
            return [ln.split()[1] for ln in f.read().splitlines() if ln.strip()]

    def read_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def write_ledger(self, key, entries):
        path = vol.ledger_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
        return path

    def entry(self, **over):
        e = {"pid": ORIGIN["pid"], "start_time": ORIGIN["start_time"],
             "cmdline_fp": vol.cmdline_fp(ORIGIN["cmdline"]),
             "url": "http://127.0.0.1:5173", "out_dir": "/tmp/out",
             "started_at": "2026-07-28T10:00:00Z",
             "owner_pid": OWNER["pid"], "owner_start_time": OWNER["start_time"]}
        e.update(over)
        return e


class SessionKeyTests(LedgerTestCase):
    def test_transcript_stem_is_the_key(self):
        self.assertEqual(vol.session_key("C:/u/p/.claude/projects/x/abc-123.jsonl"), "abc-123")

    def test_posix_and_trailing_separators(self):
        self.assertEqual(vol.session_key("/home/u/.claude/projects/x/def-456.jsonl"), "def-456")

    def test_session_id_fallback(self):
        self.assertEqual(vol.session_key(None, "sess-9"), "sess-9")
        self.assertEqual(vol.session_key("", "sess-9"), "sess-9")

    def test_key_is_filename_safe(self):
        """A key is interpolated into a path, so separators and traversal must not survive."""
        self.assertNotIn("/", vol.session_key(None, "../../etc/passwd"))
        self.assertNotIn("\\", vol.session_key(None, r"..\..\evil"))
        self.assertTrue(vol.ledger_path("../evil").startswith(vol.cache_dir()))

    def test_empty_key_is_not_empty_path(self):
        self.assertEqual(vol.session_key(None, None), "unknown")


class FingerprintTests(LedgerTestCase):
    def test_verified_when_everything_matches(self):
        idx = vol.index_by_pid([ORIGIN, OWNER])
        self.assertTrue(vol.origin_verified(self.entry(), idx))

    def test_pid_reuse_fails_verification(self):
        """Same pid, different process: the whole point of the fingerprint."""
        reused = dict(ORIGIN, start_time="2026-07-28T11:30:00.0000000Z",
                      cmdline="python -m unrelated.tool")
        idx = vol.index_by_pid([reused, OWNER])
        self.assertFalse(vol.origin_verified(self.entry(), idx))

    def test_same_pid_same_cmdline_but_restarted_fails(self):
        """A restarted-but-identical command line is still a DIFFERENT process."""
        idx = vol.index_by_pid([dict(ORIGIN, start_time="2026-07-28T12:00:00.0000000Z"), OWNER])
        self.assertFalse(vol.origin_verified(self.entry(), idx))

    def test_missing_process_fails_verification(self):
        self.assertFalse(vol.origin_verified(self.entry(), vol.index_by_pid([OWNER])))

    def test_unreadable_start_time_never_verifies(self):
        idx = vol.index_by_pid([dict(ORIGIN, start_time=None), OWNER])
        self.assertFalse(vol.origin_verified(self.entry(), idx))

    def test_owner_states(self):
        full = vol.index_by_pid([ORIGIN, OWNER])
        self.assertIs(vol.owner_alive(self.entry(), full), True)
        self.assertIs(vol.owner_alive(self.entry(), vol.index_by_pid([ORIGIN])), False)
        self.assertIs(vol.owner_alive(self.entry(owner_pid=None), full), None)
        reused_owner = vol.index_by_pid([ORIGIN, dict(OWNER, start_time="2026-07-28T13:00:00Z")])
        self.assertIs(vol.owner_alive(self.entry(), reused_owner), False)

    def test_owner_present_but_unreadable_counts_as_alive(self):
        """Fail-safe direction: an unreadable live process must never be called dead."""
        idx = vol.index_by_pid([ORIGIN, dict(OWNER, start_time=None)])
        self.assertIs(vol.owner_alive(self.entry(), idx), True)


class OwnerDetectionTests(LedgerTestCase):
    def test_picks_nearest_long_lived_ancestor(self):
        """The wrapper layers spawned for this command are seconds old; the session process is not."""
        procs = [
            {"pid": 100, "ppid": 200, "start_time": "2026-07-28T10:00:00Z", "cmdline": "python x"},
            {"pid": 200, "ppid": 300, "start_time": "2026-07-28T09:59:59Z", "cmdline": "bash -c"},
            {"pid": 300, "ppid": 400, "start_time": "2026-07-28T09:00:00Z", "cmdline": "claude"},
            {"pid": 400, "ppid": 1, "start_time": "2026-07-28T08:00:00Z", "cmdline": "pwsh"},
        ]
        pid, start = vol.detect_owner(vol.index_by_pid(procs), self_pid=100)
        self.assertEqual((pid, start), (300, "2026-07-28T09:00:00Z"))

    def test_errs_upward_never_downward(self):
        """A very young session yields a HIGHER ancestor (harmless: it outlives the session).
        The forbidden answer is a short-lived wrapper, which would make a live session look dead."""
        procs = [
            {"pid": 100, "ppid": 200, "start_time": "2026-07-28T10:00:00Z", "cmdline": "python x"},
            {"pid": 200, "ppid": 300, "start_time": "2026-07-28T09:59:59Z", "cmdline": "bash -c"},
            {"pid": 300, "ppid": 400, "start_time": "2026-07-28T09:59:55Z", "cmdline": "claude"},
            {"pid": 400, "ppid": 1, "start_time": "2026-07-28T08:00:00Z", "cmdline": "pwsh"},
        ]
        pid, _ = vol.detect_owner(vol.index_by_pid(procs), self_pid=100)
        self.assertEqual(pid, 400)

    def test_anchor_env_var_chooses_the_walk_start(self):
        """The shim hands the CLI an anchor because the Win32 chain dead-ends above a bin/ shim
        on Windows (MSYS fork intermediate). Without the anchor this walk finds nothing."""
        procs = [
            {"pid": 100, "ppid": 55555, "start_time": "2026-07-28T10:00:00Z", "cmdline": "python x"},
            {"pid": 200, "ppid": 300, "start_time": "2026-07-28T09:59:59Z", "cmdline": "bash tool"},
            {"pid": 300, "ppid": 1, "start_time": "2026-07-28T09:00:00Z", "cmdline": "claude"},
        ]
        idx = vol.index_by_pid(procs)
        self.assertEqual(vol.detect_owner(idx, self_pid=100), (None, None))
        os.environ["BALLAST_ORIGIN_ANCHOR_PID"] = "200"
        try:
            self.assertEqual(vol.detect_owner(idx)[0], 300)
        finally:
            os.environ.pop("BALLAST_ORIGIN_ANCHOR_PID", None)

    def test_anchor_is_ignored_when_absent_or_junk(self):
        for value in ("", "not-a-pid", "424242"):
            os.environ["BALLAST_ORIGIN_ANCHOR_PID"] = value
            try:
                self.assertIsNone(vol.detect_owner(vol.index_by_pid([]))[0])
            finally:
                os.environ.pop("BALLAST_ORIGIN_ANCHOR_PID", None)
        self.assertIsNone(vol.anchor_pid())

    def test_broken_chain_yields_unknown(self):
        procs = [{"pid": 100, "ppid": 999, "start_time": "2026-07-28T10:00:00Z", "cmdline": "x"}]
        self.assertEqual(vol.detect_owner(vol.index_by_pid(procs), self_pid=100), (None, None))

    def test_cyclic_chain_terminates(self):
        procs = [
            {"pid": 100, "ppid": 200, "start_time": "2026-07-28T10:00:00Z", "cmdline": "x"},
            {"pid": 200, "ppid": 100, "start_time": "2026-07-28T10:00:00Z", "cmdline": "y"},
        ]
        self.assertEqual(vol.detect_owner(vol.index_by_pid(procs), self_pid=100), (None, None))


class RecordTests(LedgerTestCase):
    def test_record_read_round_trip(self):
        procs = [ORIGIN, OWNER, SHELL, {"pid": os.getpid(), "ppid": OWNER["pid"],
                                        "start_time": "2026-07-28T10:00:00Z", "cmdline": "python t"}]
        self.set_procs(procs)
        entry = vol.record("sess-1", "http://127.0.0.1:5173", "/tmp/out", ORIGIN["pid"])
        self.assertEqual(tuple(entry.keys()), vol.ENTRY_KEYS)
        self.assertEqual(entry["pid"], ORIGIN["pid"])
        self.assertEqual(entry["start_time"], ORIGIN["start_time"])
        self.assertEqual(entry["cmdline_fp"], vol.cmdline_fp(ORIGIN["cmdline"]))
        self.assertEqual(entry["owner_pid"], OWNER["pid"])
        entries, malformed = vol.read_entries(vol.ledger_path("sess-1"))
        self.assertEqual((len(entries), malformed), (1, 0))
        self.assertEqual(entries[0], entry)

    def test_cmdline_fp_is_a_hash_not_the_command_line(self):
        self.set_procs([ORIGIN, OWNER, SHELL])
        entry = vol.record("sess-1", "u", "d", ORIGIN["pid"], owner_pid=OWNER["pid"])
        self.assertNotIn("server.mjs", json.dumps(entry))
        self.assertEqual(len(entry["cmdline_fp"]), 16)

    def test_appends_multiple_origins(self):
        self.set_procs([ORIGIN, OWNER, SHELL])
        vol.record("sess-1", "u1", "d1", ORIGIN["pid"], owner_pid=OWNER["pid"])
        vol.record("sess-1", "u2", "d2", ORIGIN["pid"], owner_pid=OWNER["pid"])
        entries, _ = vol.read_entries(vol.ledger_path("sess-1"))
        self.assertEqual([e["url"] for e in entries], ["u1", "u2"])

    def test_unknown_pid_refuses(self):
        with self.assertRaises(ValueError):
            vol.record("sess-1", "u", "d", 777777)

    def test_unfingerprintable_pid_refuses(self):
        self.set_procs([dict(ORIGIN, cmdline=""), OWNER])
        with self.assertRaises(ValueError):
            vol.record("sess-1", "u", "d", ORIGIN["pid"])

    def test_ledger_is_under_the_overridden_home(self):
        self.assertTrue(vol.ledger_path("k").startswith(self.tmp))


class ReadEntryTests(LedgerTestCase):
    def test_malformed_lines_are_counted_and_dropped(self):
        self.write_ledger("sess-1", [self.entry(), "{not json", "", json.dumps({"pid": 1})])
        entries, malformed = vol.read_entries(vol.ledger_path("sess-1"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(malformed, 2)   # bad JSON + shape-invalid; the blank line is ignored

    def test_absent_file_is_empty_not_an_error(self):
        self.assertEqual(vol.read_entries(vol.ledger_path("nope")), ([], 0))


class WindowsEnumeratorParseTests(unittest.TestCase):
    """The Windows parse half, fed synthetic tool output.

    Every other test here drives the BALLAST_VISUAL_PROC_FAKE seam, which replaces
    enumerate_processes() wholesale -- so on the one platform this ledger actually kills processes
    on, the parsing was covered by nothing. A parse regression fails toward missed-reap (a garbled
    fingerprint simply never verifies), so it would never announce itself.
    """

    def test_cim_collapses_a_single_object_to_one_row(self):
        # ConvertTo-Json emits a bare object, not a 1-element array, when exactly one process
        # matches -- the shape a naive `for d in data` would iterate as dict KEYS.
        out = vol._parse_cim(json.dumps({
            "ProcessId": 4242, "ParentProcessId": 9000,
            "Created": "2026-07-28T10:00:00.0000000Z", "CommandLine": "node server.mjs"}))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["pid"], 4242)
        self.assertEqual(out[0]["cmdline"], "node server.mjs")

    def test_cim_row_with_a_null_created_survives_with_no_start_time(self):
        # The PowerShell side guards CreationDate precisely so the row SURVIVES with a null time.
        # Dropping it here would undo that: a missing owner row reads as "session is dead".
        out = vol._parse_cim(json.dumps([
            {"ProcessId": 9000, "ParentProcessId": 1, "Created": None, "CommandLine": "claude"},
            {"ProcessId": 4242, "ParentProcessId": 9000,
             "Created": "2026-07-28T10:00:00.0000000Z", "CommandLine": "node x.mjs"}]))
        self.assertEqual([p["pid"] for p in out], [9000, 4242])
        self.assertIsNone(out[0]["start_time"])

    def test_cim_row_with_a_null_commandline_keeps_an_empty_string(self):
        out = vol._parse_cim(json.dumps([{"ProcessId": 7, "ParentProcessId": 1,
                                          "Created": "t", "CommandLine": None}]))
        self.assertEqual(out[0]["cmdline"], "")

    def test_cim_empty_result_raises_rather_than_returning_an_empty_table(self):
        # An empty table would read as "every recorded process is dead" -- the module header's
        # stated fail direction makes this a raise, not a [].
        with self.assertRaises(vol.ProcessProbeError):
            vol._parse_cim("[]")

    def test_wmic_rejoins_a_command_line_containing_commas(self):
        # The whole reason the row is parsed from both ends: the four fixed fields sit at known
        # positions and everything between them is the command line, commas included.
        csv = ("Node,CommandLine,CreationDate,ParentProcessId,ProcessId\n"
               "BOX,node serve.mjs --flags a,b,c,20260728100000.000000+000,9000,4242\n")
        out = vol._parse_wmic(csv)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["pid"], 4242)
        self.assertEqual(out[0]["ppid"], 9000)
        self.assertEqual(out[0]["cmdline"], "node serve.mjs --flags a,b,c")

    def test_wmic_skips_the_header_and_short_rows(self):
        csv = ("Node,CommandLine,CreationDate,ParentProcessId,ProcessId\n"
               "\n"
               "BOX,truncated,row\n"
               "BOX,pwsh.exe,20260728080000.000000+000,1,8000\n")
        out = vol._parse_wmic(csv)
        self.assertEqual([p["pid"] for p in out], [8000])

    def test_wmic_empty_result_raises(self):
        with self.assertRaises(vol.ProcessProbeError):
            vol._parse_wmic("Node,CommandLine,CreationDate,ParentProcessId,ProcessId\n")


class SweepTests(LedgerTestCase):
    def test_no_cache_dir_is_a_silent_no_op(self):
        res = vol.sweep()
        self.assertEqual(res["ledgers"], 0)
        self.assertEqual(res["reaped"], [])
        self.assertEqual(self.kills(), [])

    def test_empty_cache_dir_is_a_silent_no_op(self):
        os.makedirs(vol.cache_dir(), exist_ok=True)
        res = vol.sweep()
        self.assertEqual((res["ledgers"], res["pruned"]), (0, 0))
        self.assertEqual(self.kills(), [])

    def test_owner_dead_and_fingerprint_verified_is_reaped(self):
        path = self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([ORIGIN, SHELL])           # owner gone
        res = vol.sweep()
        self.assertEqual([e["pid"] for e in res["reaped"]], [ORIGIN["pid"]])
        self.assertEqual(self.kills(), [str(ORIGIN["pid"])])
        self.assertFalse(os.path.exists(path))    # ledger emptied -> removed

    def test_owner_alive_is_never_touched(self):
        path = self.write_ledger("live-sess", [self.entry()])
        before = self.read_file(path)
        res = vol.sweep()
        self.assertEqual(res["reaped"], [])
        self.assertEqual(res["pruned"], 0)
        self.assertEqual(self.kills(), [])
        self.assertEqual(self.read_file(path), before)

    def test_pid_reuse_prunes_and_never_kills(self):
        """The headline safety case: the pid is live, but it is a DIFFERENT process now."""
        path = self.write_ledger("dead-sess", [self.entry()])
        os.utime(path, (time.time() - 30 * 86400,) * 2)   # stale enough to be cleared
        self.set_procs([dict(ORIGIN, start_time="2026-07-28T20:00:00Z",
                             cmdline="python -m totally.unrelated"), SHELL])
        res = vol.sweep()
        self.assertEqual(self.kills(), [])
        self.assertEqual(res["reaped"], [])
        self.assertEqual(res["pruned"], 1)
        self.assertFalse(os.path.exists(path))

    def test_unknown_owner_is_never_reaped(self):
        path = self.write_ledger("no-owner", [self.entry(owner_pid=None, owner_start_time=None)])
        res = vol.sweep()
        self.assertEqual(self.kills(), [])
        self.assertEqual(res["reaped"], [])
        self.assertTrue(os.path.exists(path))

    def test_malformed_lines_alone_never_kill(self):
        self.write_ledger("dead-sess", ["{oops", "also not json"])
        res = vol.sweep()
        self.assertEqual(self.kills(), [])
        self.assertEqual(res["reaped"], [])

    def test_stale_all_dead_ledger_is_pruned_after_the_age_window(self):
        path = self.write_ledger("dead-sess", ["{oops"])
        os.utime(path, (time.time() - 30 * 86400,) * 2)
        res = vol.sweep()
        self.assertEqual(res["pruned"], 1)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.kills(), [])

    def test_fresh_all_dead_ledger_is_left_for_its_own_session(self):
        path = self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([SHELL])    # origin AND owner gone -> nothing to kill
        vol.sweep()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.kills(), [])

    def test_live_owner_entry_freezes_the_whole_file(self):
        """Concurrency rule: a file with any live-owner row is not rewritten at all, so a
        concurrent append cannot be lost."""
        stale = self.entry(pid=5555, url="dead-one")
        path = self.write_ledger("mixed", [self.entry(), stale])
        res = vol.sweep()
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["pruned"], 0)
        self.assertIn("dead-one", self.read_file(path))

    def test_never_kills_its_own_ancestor_chain(self):
        """A ledger naming this process's own tree must not be able to signal it."""
        me = os.getpid()
        procs = [{"pid": me, "ppid": 700, "start_time": "2026-07-28T10:00:00Z", "cmdline": "python t"},
                 {"pid": 700, "ppid": 1, "start_time": "2026-07-28T09:00:00Z", "cmdline": "claude"}]
        self.set_procs(procs)
        path = self.write_ledger("evil", [self.entry(
            pid=700, start_time="2026-07-28T09:00:00Z", cmdline_fp=vol.cmdline_fp("claude"),
            owner_pid=424242, owner_start_time="2026-07-28T01:00:00Z")])
        res = vol.sweep()
        self.assertEqual(self.kills(), [])
        self.assertEqual(res["reaped"], [])
        self.assertTrue(os.path.exists(path))

    def test_multiple_ledgers_are_handled_independently(self):
        live = self.write_ledger("live-sess", [self.entry()])
        # dead-sess records a second, identically-fingerprinted origin whose owner is absent.
        dead = self.write_ledger("dead-sess", [self.entry(
            pid=4243, owner_pid=31337, owner_start_time="2026-07-28T01:00:00Z")])
        self.set_procs([ORIGIN, OWNER, SHELL,
                        {"pid": 4243, "ppid": 1, "start_time": ORIGIN["start_time"],
                         "cmdline": ORIGIN["cmdline"]}])
        res = vol.sweep()
        self.assertEqual(self.kills(), ["4243"])
        self.assertTrue(os.path.exists(live))
        self.assertFalse(os.path.exists(dead))
        self.assertEqual(res["ledgers"], 2)

    def test_kill_failure_keeps_the_entry_for_a_later_sweep(self):
        path = self.write_ledger("dead-sess", [self.entry()])
        self.set_procs([ORIGIN, SHELL])
        saved = vol.kill_process
        vol.kill_process = lambda pid: False
        try:
            res = vol.sweep()
        finally:
            vol.kill_process = saved
        self.assertEqual(len(res["failed"]), 1)
        self.assertEqual(res["reaped"], [])
        self.assertTrue(os.path.exists(path))

    def test_unreadable_process_table_kills_nothing(self):
        self.write_ledger("dead-sess", [self.entry()])
        os.environ["BALLAST_VISUAL_PROC_FAKE"] = os.path.join(self.tmp, "missing.json")
        with self.assertRaises(vol.ProcessProbeError):
            vol.sweep()
        self.assertEqual(self.kills(), [])


class TeardownTests(LedgerTestCase):
    def test_kills_verified_entries_and_removes_the_ledger(self):
        path = self.write_ledger("sess-1", [self.entry()])
        res = vol.teardown("sess-1")
        self.assertEqual([e["pid"] for e in res["killed"]], [ORIGIN["pid"]])
        self.assertEqual(self.kills(), [str(ORIGIN["pid"])])
        self.assertFalse(os.path.exists(path))

    def test_owner_liveness_is_irrelevant_at_teardown(self):
        """The caller IS the owner; teardown declares the origins finished."""
        self.write_ledger("sess-1", [self.entry(owner_pid=None, owner_start_time=None)])
        res = vol.teardown("sess-1")
        self.assertEqual(len(res["killed"]), 1)

    def test_stale_entry_is_pruned_not_killed(self):
        self.write_ledger("sess-1", [self.entry(pid=6666)])
        res = vol.teardown("sess-1")
        self.assertEqual(res["killed"], [])
        self.assertEqual(res["pruned"], 1)
        self.assertEqual(self.kills(), [])

    def test_unkilled_entry_stays_recorded_for_the_next_sweep(self):
        path = self.write_ledger("sess-1", [self.entry()])
        saved = vol.kill_process
        vol.kill_process = lambda pid: False
        try:
            res = vol.teardown("sess-1")
        finally:
            vol.kill_process = saved
        self.assertEqual(len(res["failed"]), 1)
        self.assertTrue(os.path.exists(path), "a stranded process must stay in the ledger")
        entries, _ = vol.read_entries(path)
        self.assertEqual(len(entries), 1)

    def test_absent_ledger_is_a_no_op(self):
        res = vol.teardown("never-existed")
        self.assertFalse(res["existed"])
        self.assertEqual(self.kills(), [])


class CliTests(LedgerTestCase):
    """The CLI as the orchestrator drives it. Invoked as a subprocess so argv parsing, exit codes,
    and stdout are all exercised end to end."""

    def cli(self, *args):
        env = dict(os.environ)
        return subprocess.run([sys.executable, vol.__file__] + list(args),
                              capture_output=True, text=True, timeout=60, env=env)

    def test_record_list_teardown_cycle(self):
        transcript = "C:/u/.claude/projects/p/abc-123.jsonl"
        r = self.cli("record", "--transcript-path", transcript, "--url", "http://127.0.0.1:5173",
                     "--out-dir", "/tmp/out", "--pid", str(ORIGIN["pid"]),
                     "--owner-pid", str(OWNER["pid"]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("recorded pid 4242", r.stdout)
        self.assertTrue(os.path.isfile(vol.ledger_path("abc-123")))

        r = self.cli("list", "--session-key", "abc-123")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("live", r.stdout)
        self.assertIn("owner alive", r.stdout)

        r = self.cli("teardown", "--transcript-path", transcript)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("killed pid 4242", r.stdout)
        self.assertEqual(self.kills(), [str(ORIGIN["pid"])])
        self.assertFalse(os.path.exists(vol.ledger_path("abc-123")))

    def test_record_of_a_dead_pid_fails_loudly(self):
        r = self.cli("record", "--session-key", "k", "--url", "u", "--out-dir", "d",
                     "--pid", "999999")
        self.assertEqual(r.returncode, 1)
        self.assertIn("record failed", r.stderr)

    def test_missing_key_is_a_usage_error(self):
        r = self.cli("teardown")
        self.assertNotEqual(r.returncode, 0)

    def test_list_all_across_sessions(self):
        self.write_ledger("s1", [self.entry()])
        self.write_ledger("s2", [self.entry(pid=9999)])
        r = self.cli("list", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[s1]", r.stdout)
        self.assertIn("[s2]", r.stdout)
        self.assertIn("stale", r.stdout)


class PinTests(LedgerTestCase):
    """The dispatch pin: frozen context in, re-stated liveness out. Nothing here can kill anything --
    the pin is context, and `--check` is a SHADOW report that must never change an exit code."""

    def out_dir(self):
        d = os.path.join(self.tmp, "vp-out")
        os.makedirs(d, exist_ok=True)
        return d

    def test_pin_round_trip_carries_every_brief_field(self):
        out = self.out_dir()
        vol.write_pin("s1", "http://127.0.0.1:5233", out, states="/p/.claude/visual-states.json",
                      matrix="1440x900@1,390x844@1", settle=250, frontend_root=self.tmp)
        pin = vol.read_pin(out)
        self.assertEqual(pin["origin"], "http://127.0.0.1:5233")
        self.assertEqual(pin["out_dir"], out)
        self.assertEqual(pin["states_manifest"], "/p/.claude/visual-states.json")
        self.assertEqual(pin["matrix"], "1440x900@1,390x844@1")
        self.assertEqual(pin["settle"], 250)
        self.assertTrue(pin["pinned_at"])

    def test_re_pin_overwrites_rather_than_merging(self):
        out = self.out_dir()
        vol.write_pin("s1", "http://a", out, matrix="1440x900@1", frontend_root=self.tmp)
        vol.write_pin("s1", "http://b", out, frontend_root=self.tmp)
        pin = vol.read_pin(out)
        self.assertEqual(pin["origin"], "http://b")
        self.assertIsNone(pin["matrix"])  # a stale field must not survive a re-pin

    def test_demurrage_stamp_finds_the_newest_frontend_file(self):
        root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(root, "node_modules"), exist_ok=True)
        old = os.path.join(root, "old.css")
        new = os.path.join(root, "new.tsx")
        vendored = os.path.join(root, "node_modules", "vendor.js")
        for p in (old, vendored):
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
        os.utime(old, (1, 1))
        os.utime(vendored, (10 ** 9, 10 ** 9))  # far newer, but excluded by the walk
        with open(new, "w", encoding="utf-8") as f:
            f.write("y")
        os.utime(new, (10 ** 8, 10 ** 8))
        path, _ = vol.newest_frontend_mtime(root)
        self.assertEqual(os.path.basename(path), "new.tsx")

    def test_check_flags_evidence_older_than_the_newest_edit(self):
        root = os.path.join(self.tmp, "app2")
        os.makedirs(root, exist_ok=True)
        out = self.out_dir()
        vol.write_pin("s1", "http://127.0.0.1:5233", out, frontend_root=root)
        manifest = os.path.join(out, "manifest.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write("{}")
        os.utime(manifest, (10 ** 8, 10 ** 8))
        edit = os.path.join(root, "late.css")
        with open(edit, "w", encoding="utf-8") as f:
            f.write("x")
        os.utime(edit, (10 ** 8 + 500, 10 ** 8 + 500))
        rows = {r["check"]: r for r in vol.check_pin(out)}
        self.assertFalse(rows["evidence"]["ok"])
        # Fresh evidence is the other direction of the same check.
        os.utime(manifest, (10 ** 8 + 900, 10 ** 8 + 900))
        rows = {r["check"]: r for r in vol.check_pin(out)}
        self.assertTrue(rows["evidence"]["ok"])

    def test_check_verifies_the_origin_against_the_ledger(self):
        out = self.out_dir()
        vol.write_pin("s1", "http://127.0.0.1:5173", out, frontend_root=self.tmp)
        rows = {r["check"]: r for r in vol.check_pin(out)}
        self.assertFalse(rows["origin"]["ok"])          # nothing recorded yet
        self.write_ledger("s1", [self.entry()])
        rows = {r["check"]: r for r in vol.check_pin(out)}
        self.assertTrue(rows["origin"]["ok"])

    def test_check_never_borrows_another_session_s_ledger_row(self):
        # Same URL, another session's ledger: a cross-session match would report "origin verified"
        # for a process THIS session never recorded (and may well have outlived).
        out = self.out_dir()
        vol.write_pin("s1", "http://127.0.0.1:5173", out, frontend_root=self.tmp)
        self.write_ledger("s2", [self.entry()])
        rows = {r["check"]: r for r in vol.check_pin(out)}
        self.assertFalse(rows["origin"]["ok"])

    def test_pin_carries_the_native_cell_and_suppressions_slots(self):
        out = self.out_dir()
        vol.write_pin("s1", "http://127.0.0.1:5233", out, matrix="1440x900@1,390x844@1",
                      native="1440x900@1", suppressions="/p/.claude/vp-suppressions.json",
                      frontend_root=self.tmp)
        pin = vol.read_pin(out)
        self.assertEqual(pin["native"], "1440x900@1")
        self.assertEqual(pin["suppressions"], "/p/.claude/vp-suppressions.json")

    def test_check_of_a_missing_pin_is_a_single_named_row(self):
        rows = vol.check_pin(os.path.join(self.tmp, "nope"))
        self.assertEqual([r["check"] for r in rows], ["pin"])
        self.assertFalse(rows[0]["ok"])

    def test_cli_pin_then_check_is_shadow_only(self):
        out = self.out_dir()
        env = dict(os.environ)

        def cli(*args):
            return subprocess.run([sys.executable, vol.__file__] + list(args),
                                  capture_output=True, text=True, timeout=60, env=env)

        r = cli("pin", "--session-key", "s1", "--url", "http://127.0.0.1:5233", "--out-dir", out,
                "--matrix", "1440x900@1", "--frontend-root", self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pinned", r.stdout)

        r = cli("pin", "--check", "--out-dir", out)
        # The origin is unrecorded, so this run HAS a stale claim -- and still exits 0.
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WOULD-BLOCK", r.stdout)
        self.assertIn("WOULD-BLOCK", self.read_file(vol.shadow_log_path()))

    def test_cli_pin_without_url_is_a_usage_error(self):
        r = subprocess.run([sys.executable, vol.__file__, "pin", "--session-key", "s1",
                            "--out-dir", self.out_dir()],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ))
        self.assertEqual(r.returncode, 2)


class ShimTests(LedgerTestCase):
    """The bin/ shim resolves an interpreter and forwards argv. Skipped where bash is absent."""

    def test_shim_forwards_to_the_cli(self):
        import shutil
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        self.write_ledger("s1", [self.entry()])
        env = dict(os.environ)
        r = subprocess.run([bash, os.path.abspath(SHIM), "list", "--session-key", "s1"],
                           capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[s1]", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
