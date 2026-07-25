#!/usr/bin/env python
"""Regression tests for inline-churn-nudge.py -- the PostToolUse SHADOW-MODE context-economy
observer that logs how long the main session's runs of in-line Edit/Write/MultiEdit/
NotebookEdit tool calls get between subagent (Task/Agent) dispatches.

The hook filename is hyphenated (not importable via a normal statement), so each case invokes
it as a subprocess with the current interpreter, feeds a hook JSON payload on stdin, and asserts
on returncode + stdout/stderr -- matching test_doc_write_guard.py's convention.

HERMETIC (hooks/CLAUDE.md rule): the hook's state_home() honors BALLAST_CLAUDE_HOME as a
hermetic-test override. Every test here runs the subprocess with BALLAST_CLAUDE_HOME pointed at
a fresh per-test tmp dir, so per-session counters and shadow.log never touch the real ~/.claude.

Decision logic under test (derived from the hook's own code, not from its header prose):
  - EDIT-CLASS (Edit/Write/MultiEdit/NotebookEdit) increments a per-session counter file at
    <state-home>/.cache/ballast-churn/<session_id>, persisted across calls.
  - Every exact multiple-of-5 crossing appends one line to shadow.log carrying the correct
    run=<count>.
  - DISPATCH-CLASS (Task/Agent) resets the counter to 0 unconditionally; it additionally
    appends a "reset run=<count> via=<tool_name>" line to shadow.log, but ONLY when the run
    being reset was >= 3 (a shorter run resets silently).
  - Different session_ids track fully independent counters.
  - v1 is PURE SHADOW: stdout and stderr are empty on every single call, and the process always
    exits 0 -- including every fail-open path (malformed JSON, empty stdin, a non-dict JSON
    payload, a missing tool_name, an unrecognized tool_name).
  - prune() reaps counter files untouched for 2+ days, but explicitly never reaps shadow.log
    itself (the accumulating dataset).

Self-locating + standalone: `python hooks/test_inline_churn_nudge.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inline-churn-nudge.py")


def run_hook(payload_text, env):
    return subprocess.run(
        [sys.executable, HOOK], input=payload_text, capture_output=True, text=True, timeout=30, env=env
    )


def run_payload(payload, env):
    return run_hook(json.dumps(payload), env)


def edit_payload(session_id="s1", tool_name="Edit"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_input": {"file_path": "C:/proj/x.py"},
    }


def dispatch_payload(session_id="s1", tool_name="Task"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_input": {},
    }


class HermeticTestCase(unittest.TestCase):
    """Base class: every test gets its own BALLAST_CLAUDE_HOME tmp dir so counters and
    shadow.log can never leak across tests or touch the real ~/.claude."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="churn-test-")
        self.env = dict(os.environ)
        self.env["BALLAST_CLAUDE_HOME"] = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cache_dir(self):
        return os.path.join(self.tmp, ".cache", "ballast-churn")

    def counter_path(self, session_id):
        return os.path.join(self.cache_dir(), session_id)

    def read_counter(self, session_id):
        with open(self.counter_path(session_id), "r", encoding="utf-8") as f:
            return int(f.read().strip())

    def shadow_log_lines(self):
        path = os.path.join(self.cache_dir(), "shadow.log")
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln]

    def _assert_silent_zero(self, proc):
        self.assertEqual(proc.returncode, 0, "expected exit 0, got %s stderr=%r" % (proc.returncode, proc.stderr))
        self.assertEqual(proc.stdout, "", "expected empty stdout, got %r" % proc.stdout)
        self.assertEqual(proc.stderr, "", "expected empty stderr, got %r" % proc.stderr)


# =================================================================================================
# Output silence: every single code path must produce zero stdout and zero stderr, exit 0.
# =================================================================================================

class OutputAlwaysSilent(HermeticTestCase):
    def test_edit_class_silent(self):
        self._assert_silent_zero(run_payload(edit_payload(), self.env))

    def test_dispatch_class_silent(self):
        self._assert_silent_zero(run_payload(dispatch_payload(), self.env))

    def test_fifth_edit_crossing_still_silent(self):
        for _ in range(5):
            p = run_payload(edit_payload(session_id="silent5"), self.env)
            self._assert_silent_zero(p)
        # A shadow.log line was written, but the HOOK PROCESS itself never emits anything.
        self.assertEqual(len(self.shadow_log_lines()), 1)

    def test_reset_with_long_run_still_silent(self):
        for _ in range(3):
            run_payload(edit_payload(session_id="silentreset"), self.env)
        p = run_payload(dispatch_payload(session_id="silentreset"), self.env)
        self._assert_silent_zero(p)


# =================================================================================================
# EDIT-CLASS counting: increments persist per session_id across separate subprocess calls.
# =================================================================================================

class EditClassCounting(HermeticTestCase):
    def test_single_edit_increments_to_one(self):
        run_payload(edit_payload(session_id="inc1"), self.env)
        self.assertEqual(self.read_counter("inc1"), 1)

    def test_counter_persists_across_calls(self):
        for _ in range(4):
            run_payload(edit_payload(session_id="inc4"), self.env)
        self.assertEqual(self.read_counter("inc4"), 4)

    def test_all_edit_class_tool_names_increment(self):
        # Edit, Write, MultiEdit, NotebookEdit all count toward the same run.
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            run_payload(edit_payload(session_id="alltools", tool_name=tool), self.env)
        self.assertEqual(self.read_counter("alltools"), 4)

    def test_different_session_ids_track_independently(self):
        for _ in range(3):
            run_payload(edit_payload(session_id="sidA"), self.env)
        for _ in range(2):
            run_payload(edit_payload(session_id="sidB"), self.env)
        self.assertEqual(self.read_counter("sidA"), 3)
        self.assertEqual(self.read_counter("sidB"), 2)


# =================================================================================================
# Multiple-of-5 crossings append a correctly-valued shadow.log line.
# =================================================================================================

class MultipleOfFiveLogging(HermeticTestCase):
    def test_no_log_line_before_fifth_edit(self):
        for _ in range(4):
            run_payload(edit_payload(session_id="pre5"), self.env)
        self.assertEqual(self.shadow_log_lines(), [])

    def test_fifth_edit_logs_run_5(self):
        for _ in range(5):
            run_payload(edit_payload(session_id="hit5"), self.env)
        lines = self.shadow_log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("run=5", lines[0])
        self.assertIn("sid=hit5", lines[0])  # "hit5" is under 8 chars so the full id appears verbatim
        self.assertNotIn("reset", lines[0])

    def test_tenth_edit_appends_second_line_with_run_10(self):
        for _ in range(10):
            run_payload(edit_payload(session_id="hit10"), self.env)
        lines = self.shadow_log_lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("run=5", lines[0])
        self.assertIn("run=10", lines[1])

    def test_session_id_truncated_to_first_eight_chars(self):
        long_sid = "abcdefghijklmnop"
        for _ in range(5):
            run_payload(edit_payload(session_id=long_sid), self.env)
        lines = self.shadow_log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("sid=abcdefgh", lines[0])
        self.assertNotIn("sid=abcdefghi", lines[0])

    def test_log_line_carries_proj_field(self):
        for _ in range(5):
            run_payload(edit_payload(session_id="proj-chk"), self.env)  # exactly 8 chars, no truncation surprises
        lines = self.shadow_log_lines()
        self.assertRegex(lines[0], r"^\S+ proj=\S+ sid=proj-chk run=5$")


# =================================================================================================
# DISPATCH-CLASS reset behavior: always resets to 0; logs a "reset" line only when run >= 3.
# =================================================================================================

class DispatchClassReset(HermeticTestCase):
    def test_dispatch_with_run_ge_3_logs_reset_line(self):
        for _ in range(3):
            run_payload(edit_payload(session_id="reset3"), self.env)
        run_payload(dispatch_payload(session_id="reset3", tool_name="Task"), self.env)
        lines = self.shadow_log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("reset run=3 via=Task", lines[0])
        self.assertEqual(self.read_counter("reset3"), 0)

    def test_dispatch_with_run_below_3_resets_silently(self):
        for _ in range(2):
            run_payload(edit_payload(session_id="reset2"), self.env)
        run_payload(dispatch_payload(session_id="reset2", tool_name="Agent"), self.env)
        self.assertEqual(self.shadow_log_lines(), [])  # no reset line -- run was too short
        self.assertEqual(self.read_counter("reset2"), 0)  # but still reset

    def test_dispatch_with_zero_run_resets_silently(self):
        run_payload(dispatch_payload(session_id="reset0", tool_name="Task"), self.env)
        self.assertEqual(self.shadow_log_lines(), [])
        self.assertEqual(self.read_counter("reset0"), 0)

    def test_agent_tool_name_treated_same_as_task(self):
        for _ in range(4):
            run_payload(edit_payload(session_id="agentreset"), self.env)
        run_payload(dispatch_payload(session_id="agentreset", tool_name="Agent"), self.env)
        lines = self.shadow_log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("via=Agent", lines[0])

    def test_run_continues_after_a_short_reset(self):
        # Reset below 3, then edit again -- the new run starts fresh from 1, not carried over.
        run_payload(edit_payload(session_id="continue"), self.env)
        run_payload(dispatch_payload(session_id="continue"), self.env)
        run_payload(edit_payload(session_id="continue"), self.env)
        self.assertEqual(self.read_counter("continue"), 1)


# =================================================================================================
# Fail-open paths -- must NEVER block/crash, and must NEVER emit anything, on any malformed or
# unrecognized input.
# =================================================================================================

class FailOpenPaths(HermeticTestCase):
    def test_malformed_json_fails_open_silently(self):
        self._assert_silent_zero(run_hook("not json{", self.env))

    def test_empty_stdin_fails_open_silently(self):
        self._assert_silent_zero(run_hook("", self.env))

    def test_list_json_payload_fails_open_silently(self):
        # Valid JSON that isn't an object -- the isinstance guard must prevent an AttributeError
        # (the exact bug class fixed twice elsewhere in this repo today).
        self._assert_silent_zero(run_hook("[]", self.env))

    def test_missing_tool_name_fails_open_silently(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_input": {}}
        self._assert_silent_zero(run_payload(payload, self.env))

    def test_unknown_tool_name_fails_open_silently_and_untouched(self):
        payload = edit_payload(session_id="unknowntool", tool_name="Bash")
        self._assert_silent_zero(run_payload(payload, self.env))
        # An unrecognized tool must not create ANY state -- no counter file at all.
        self.assertFalse(os.path.exists(self.counter_path("unknowntool")))

    def test_missing_session_id_falls_back_and_stays_silent(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {}}
        self._assert_silent_zero(run_payload(payload, self.env))


# =================================================================================================
# prune(): counter files untouched for 2+ days are reaped on the next touch, but shadow.log is
# NEVER reaped even if it is equally stale (it is the accumulating dataset).
# =================================================================================================

class PruneOldFiles(HermeticTestCase):
    def _make_stale(self, path, days=3):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("1")
        stale_time = time.time() - days * 86400
        os.utime(path, (stale_time, stale_time))

    def test_stale_counter_file_is_pruned_on_next_touch(self):
        stale_path = self.counter_path("stale-session")
        self._make_stale(stale_path, days=3)
        self.assertTrue(os.path.exists(stale_path))
        # Any EDIT-CLASS/DISPATCH-CLASS touch (from a DIFFERENT session) triggers prune().
        run_payload(edit_payload(session_id="fresh-session"), self.env)
        self.assertFalse(os.path.exists(stale_path), "stale counter file should have been pruned")

    def test_fresh_counter_file_survives_prune(self):
        run_payload(edit_payload(session_id="keep-me"), self.env)
        fresh_path = self.counter_path("keep-me")
        self.assertTrue(os.path.exists(fresh_path))
        run_payload(edit_payload(session_id="other-session"), self.env)
        self.assertTrue(os.path.exists(fresh_path), "a file touched moments ago must not be pruned")

    def test_stale_shadow_log_is_never_pruned(self):
        # Build up a real shadow.log entry, then age the FILE itself (not its content) past the
        # 2-day cutoff, and confirm a later touch does not delete it.
        for _ in range(5):
            run_payload(edit_payload(session_id="logbuilder"), self.env)
        log_path = os.path.join(self.cache_dir(), "shadow.log")
        self.assertTrue(os.path.exists(log_path))
        stale_time = time.time() - 3 * 86400
        os.utime(log_path, (stale_time, stale_time))
        run_payload(edit_payload(session_id="another-touch"), self.env)
        self.assertTrue(os.path.exists(log_path), "shadow.log must survive prune regardless of its own mtime")
        # Its prior content must also still be there -- prune must not truncate it either.
        with open(log_path, "r", encoding="utf-8") as f:
            self.assertIn("run=5", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
