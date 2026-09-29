#!/usr/bin/env python
"""Regression tests for inline-churn-nudge.py -- the PostToolUse context-economy hook that counts
runs of in-line Edit/Write/MultiEdit/NotebookEdit tool calls between subagent (Task/Agent)
dispatches, and nudges once per session when a MAIN-session run crosses 20 untainted.

The hook filename is hyphenated (not importable via a normal statement), so each case invokes
it as a subprocess with the current interpreter, feeds a hook JSON payload on stdin, and asserts
on returncode + stdout/stderr.

HERMETIC (hooks/CLAUDE.md rule): the hook's state_home() honors BALLAST_CLAUDE_HOME as a
hermetic-test override. Every test here runs the subprocess with BALLAST_CLAUDE_HOME pointed at
a fresh per-test tmp dir, so per-session counters never touch the real ~/.claude.

Decision logic under test (derived from the hook's own code, not from its header prose):
  - EDIT-CLASS (Edit/Write/MultiEdit/NotebookEdit) increments a per-session state file at
    <state-home>/.cache/ballast-churn/<session_id>, persisted across calls. The file holds
    "<count> <tainted> <nudged>"; a LEGACY bare-integer file must parse as the count with both
    flags false, and unparseable content must read as a fresh run.
  - DISPATCH-CLASS (Task/Agent) resets the counter to 0 unconditionally. A reset also clears
    `tainted`, but never `nudged` (once per session, not once per run).
  - Different session_ids track fully independent counters.
  - The ONE emitting path is the nudge: a MAIN-class edit taking the count to >= 20, on a run no
    leaf/ambiguous edit has tainted, once per session. Everything else is byte-silent (stdout AND
    stderr empty, exit 0) -- including every fail-open path (malformed JSON, empty stdin, a
    non-dict JSON payload, a missing tool_name, an unrecognized tool_name) and EVERY leaf fire.
  - prune() reaps counter files untouched for 2+ days.

Self-locating + standalone: `python hooks/tests/test_inline_churn_nudge.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inline-churn-nudge.py")


def run_hook(payload_text, env):
    return subprocess.run(
        [sys.executable, HOOK], input=payload_text, capture_output=True, text=True, timeout=30, env=env
    )


def run_payload(payload, env):
    return run_hook(json.dumps(payload), env)


def edit_payload(session_id="s1", tool_name="Edit"):
    # No agent_type/agent_id keys AT ALL -- that total absence is what makes a fire MAIN-class
    # (the hook's affirmative-main rule, ported from visual-arm.py).
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_input": {"file_path": "C:/proj/x.py"},
    }


def leaf_edit_payload(session_id="s1", tool_name="Edit"):
    p = edit_payload(session_id=session_id, tool_name=tool_name)
    p["agent_type"] = "plan-executor"
    p["agent_id"] = "leaf-1"
    return p


def ambiguous_edit_payload(session_id="s1", tool_name="Edit"):
    # Discriminator key PRESENT but empty -> ambiguity, not a main fire.
    p = edit_payload(session_id=session_id, tool_name=tool_name)
    p["agent_type"] = ""
    return p


def dispatch_payload(session_id="s1", tool_name="Task"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_input": {},
    }


class HermeticTestCase(unittest.TestCase):
    """Base class: every test gets its own BALLAST_CLAUDE_HOME tmp dir so counters can never
    leak across tests or touch the real ~/.claude."""

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

    def read_state(self, session_id):
        """(count, tainted, nudged) as persisted -- the state file is `<count> <tainted> <nudged>`."""
        with open(self.counter_path(session_id), "r", encoding="utf-8") as f:
            parts = f.read().strip().split()
        return int(parts[0]), parts[1] == "1", parts[2] == "1"

    def read_counter(self, session_id):
        return self.read_state(session_id)[0]

    def write_raw_state(self, session_id, text):
        """Seed a state file verbatim -- used for the legacy bare-integer migration case."""
        os.makedirs(self.cache_dir(), exist_ok=True)
        with open(self.counter_path(session_id), "w", encoding="utf-8") as f:
            f.write(text)

    def _assert_silent_zero(self, proc):
        self.assertEqual(proc.returncode, 0, "expected exit 0, got %s stderr=%r" % (proc.returncode, proc.stderr))
        self.assertEqual(proc.stdout, "", "expected empty stdout, got %r" % proc.stdout)
        self.assertEqual(proc.stderr, "", "expected empty stderr, got %r" % proc.stderr)


# =================================================================================================
# Output silence: every code path EXCEPT the once-per-session threshold nudge (see ThresholdNudge
# below) must produce zero stdout and zero stderr, exit 0.
# =================================================================================================

class OutputAlwaysSilent(HermeticTestCase):
    def test_edit_class_silent(self):
        self._assert_silent_zero(run_payload(edit_payload(), self.env))

    def test_dispatch_class_silent(self):
        self._assert_silent_zero(run_payload(dispatch_payload(), self.env))

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
# DISPATCH-CLASS reset behavior: always resets to 0.
# =================================================================================================

class DispatchClassReset(HermeticTestCase):
    def test_task_dispatch_resets_the_run(self):
        for _ in range(3):
            run_payload(edit_payload(session_id="reset3"), self.env)
        run_payload(dispatch_payload(session_id="reset3", tool_name="Task"), self.env)
        self.assertEqual(self.read_counter("reset3"), 0)

    def test_dispatch_with_zero_run_resets(self):
        run_payload(dispatch_payload(session_id="reset0", tool_name="Task"), self.env)
        self.assertEqual(self.read_counter("reset0"), 0)

    def test_agent_tool_name_treated_same_as_task(self):
        for _ in range(4):
            run_payload(edit_payload(session_id="agentreset"), self.env)
        run_payload(dispatch_payload(session_id="agentreset", tool_name="Agent"), self.env)
        self.assertEqual(self.read_counter("agentreset"), 0)

    def test_run_continues_after_a_short_reset(self):
        # Reset below 3, then edit again -- the new run starts fresh from 1, not carried over.
        run_payload(edit_payload(session_id="continue"), self.env)
        run_payload(dispatch_payload(session_id="continue"), self.env)
        run_payload(edit_payload(session_id="continue"), self.env)
        self.assertEqual(self.read_counter("continue"), 1)


# =================================================================================================
# State-file format + legacy migration: "<count> <tainted> <nudged>", with a bare integer (the
# pre-nudge format) parsing as the count with both flags false.
# =================================================================================================

class StateFileFormat(HermeticTestCase):
    def test_state_file_carries_count_and_flags(self):
        run_payload(edit_payload(session_id="fmt1"), self.env)
        self.assertEqual(self.read_state("fmt1"), (1, False, False))

    def test_leaf_edit_sets_tainted_flag(self):
        run_payload(leaf_edit_payload(session_id="fmt2"), self.env)
        self.assertEqual(self.read_state("fmt2"), (1, True, False))

    def test_legacy_bare_integer_parses_as_count(self):
        self.write_raw_state("legacy", "7")
        run_payload(edit_payload(session_id="legacy"), self.env)
        self.assertEqual(self.read_state("legacy"), (8, False, False))

    def test_garbage_state_reads_as_a_fresh_run(self):
        self.write_raw_state("garbage", "not-a-number at all")
        p = run_payload(edit_payload(session_id="garbage"), self.env)
        self._assert_silent_zero(p)
        self.assertEqual(self.read_state("garbage"), (1, False, False))


# =================================================================================================
# The one emitting path: a MAIN-class edit crossing NUDGE_THRESHOLD (20) on an untainted run,
# once per session. Everything else stays byte-silent.
# =================================================================================================

NUDGE_TEXT = (
    "INLINE-EDIT RUN: 20+ in-line edits this session without a dispatch. If this is "
    "implementation churn, hand the rest to a leaf agent (see the plan-handoff skill). A "
    "deliberate mechanical sweep or a run of tiny diffs? Carry on."
)


class ThresholdNudge(HermeticTestCase):
    def edits(self, n, session_id, payload_fn=edit_payload):
        """Fire n edit-class events, returning the LAST process (the one under assertion)."""
        proc = None
        for _ in range(n):
            proc = run_payload(payload_fn(session_id=session_id), self.env)
        return proc

    def assert_nudged(self, proc):
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "", "the nudge path must not write stderr")
        out = json.loads(proc.stdout)
        self.assertIn("inline-churn-nudge", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], NUDGE_TEXT)

    def test_silent_below_threshold(self):
        self._assert_silent_zero(self.edits(19, "under"))
        self.assertEqual(self.read_state("under"), (19, False, False))

    def test_twentieth_main_edit_nudges(self):
        self.edits(19, "cross")
        self.assert_nudged(self.edits(1, "cross"))
        self.assertEqual(self.read_state("cross"), (20, False, True))

    def test_nudge_fires_only_once_per_session(self):
        self.edits(20, "once")           # nudged here
        for _ in range(20):              # 21..40
            self._assert_silent_zero(self.edits(1, "once"))

    def test_a_leaf_edit_taints_the_run_and_suppresses_the_nudge(self):
        run_payload(leaf_edit_payload(session_id="tainted"), self.env)
        self._assert_silent_zero(self.edits(25, "tainted"))
        count, tainted, nudged = self.read_state("tainted")
        self.assertEqual((count, tainted, nudged), (26, True, False))

    def test_an_ambiguous_edit_taints_the_run_too(self):
        run_payload(ambiguous_edit_payload(session_id="amb"), self.env)
        self._assert_silent_zero(self.edits(25, "amb"))
        self.assertTrue(self.read_state("amb")[1])

    def test_dispatch_reset_clears_the_taint(self):
        run_payload(leaf_edit_payload(session_id="cleared"), self.env)
        run_payload(dispatch_payload(session_id="cleared"), self.env)
        self.assertEqual(self.read_state("cleared"), (0, False, False))
        self.edits(19, "cleared")
        self.assert_nudged(self.edits(1, "cleared"))

    def test_nudged_survives_a_dispatch_reset(self):
        # Once per SESSION, not once per run: a fresh 20-run after a dispatch must stay silent.
        self.edits(20, "persist")
        run_payload(dispatch_payload(session_id="persist"), self.env)
        self.assertEqual(self.read_state("persist"), (0, False, True))
        self._assert_silent_zero(self.edits(20, "persist"))

    def test_leaf_edits_never_emit_however_long_the_run(self):
        for _ in range(25):
            self._assert_silent_zero(run_payload(leaf_edit_payload(session_id="leafrun"), self.env))

    def test_missing_session_id_is_ambiguous_and_never_nudges(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {}}
        for _ in range(25):
            self._assert_silent_zero(run_payload(payload, self.env))

    def test_legacy_state_file_can_carry_a_run_over_the_threshold(self):
        self.write_raw_state("legacy20", "19")
        self.assert_nudged(self.edits(1, "legacy20"))

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
        # (a bug class this repo's hooks have hit before).
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
# prune(): counter files untouched for 2+ days are reaped on the next touch.
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

    def test_retired_shadow_log_is_never_pruned(self):
        # The retired shadow-log dataset is user-recycled, never agent-deleted, however old.
        shadow_path = self.counter_path("shadow.log")
        self._make_stale(shadow_path, days=30)
        run_payload(edit_payload(session_id="fresh-session"), self.env)
        self.assertTrue(os.path.exists(shadow_path), "prune must leave the retired shadow.log alone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
