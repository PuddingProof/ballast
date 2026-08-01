#!/usr/bin/env python
"""Regression tests for visual-arm.py (spec R3 deterministic arming).

The hook filename is hyphenated (not importable), so each case invokes it as a subprocess with
the current interpreter, feeds a PostToolUse JSON payload on stdin, and asserts on the emitted
JSON plus the on-disk session state.

Three things carry the weight here, and none is decoration:

  - THE FRONTEND PATTERN (positive AND negative rows). It is the hook's entire trigger surface;
    an unpinned regex drifts silently. The negatives pin the two boundaries the heuristic is
    narrow on purpose about: SEGMENT-not-substring (`src/parse-components.ts` stays silent) and
    the script-extension gate (`docs/components/overview.md` stays silent). The
    `coverage/index.html` row is pinned POSITIVE on purpose -- extensions are decisive, with no
    directory exclusion list; see the hook header.
  - MAIN-LOOP-ONLY + PENDING-ARM DRAIN. The load-bearing R3 rule: a leaf fire never emits
    anything (an injection into a leaf burns the once-per-session trigger), it records; the next
    main-loop fire -- on ANY matched tool, frontend or not -- drains the record as the injection.
  - THE DEGRADED-MODE RESOLUTION. is_subagent() fails toward False, which for arming means
    "inject" -- the wrong direction. So an ambiguous fire (a discriminator key present but
    empty, a missing session_id, a missing/malformed tool_input) must RECORD, never inject.
    Both halves are pinned below.

HERMETIC (hooks/CLAUDE.md rule): every invocation sets BALLAST_CLAUDE_HOME to a temp dir, so the
session state directory is never the real ~/.claude. Production never sets that var.

Self-locating + standalone: `python hooks/tests/test_visual_arm.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "visual-arm.py")

SESSION = "sess-abc-123"
TRANSCRIPT = "/home/u/.claude/projects/proj/sess-abc-123.jsonl"
# B0a: a leaf PostToolUse fire carries BOTH discriminator fields and the PARENT's session_id.
LEAF = {"agent_type": "plan-executor", "agent_id": "ag_1"}

# The clause the rebuilt workflow skill's when_to_use anticipates verbatim. If this literal moves,
# the skill's trigger text moves with it.
ARM_CLAUSE = "frontend touched — load the visual workflow skill now"


class ArmCase(unittest.TestCase):
    """Base: a temp BALLAST_CLAUDE_HOME per test, plus the run/assert helpers."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ballast-arm-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    # -- state helpers -----------------------------------------------------------------
    def state_dir(self):
        return os.path.join(self.home, ".cache", "ballast-arm")

    def state_file(self, key=SESSION):
        return os.path.join(self.state_dir(), "arm-%s.json" % key)

    def state(self, key=SESSION):
        with open(self.state_file(key), "r", encoding="utf-8") as fh:
            return json.load(fh)

    # -- invocation --------------------------------------------------------------------
    def run_hook(self, file_path="src/App.tsx", tool="Edit", caller=None,
                 session=SESSION, transcript=TRANSCRIPT, tool_input=None, raw=None):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": file_path} if tool_input is None else tool_input,
            "transcript_path": transcript,
        }
        if session is not None:
            payload["session_id"] = session
        payload.update(caller or {})
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        return subprocess.run(
            [sys.executable, HOOK],
            input=raw if raw is not None else json.dumps(payload),
            capture_output=True, text=True, env=env,
        )

    def out(self, proc):
        """Parsed stdout JSON, or None when the hook stayed silent."""
        self.assertEqual(proc.returncode, 0, "hook must always exit 0: %s" % proc.stderr)
        s = proc.stdout.strip()
        return json.loads(s) if s else None

    def injected(self, proc):
        o = self.out(proc)
        if o is None:
            return None
        return o.get("hookSpecificOutput", {}).get("additionalContext")

    def assertArmed(self, **kw):
        proc = self.run_hook(**kw)
        ctx = self.injected(proc)
        self.assertIsNotNone(ctx, "expected an arming injection for %r, got %r" % (kw, proc.stdout))
        self.assertIn(ARM_CLAUSE, ctx)
        return proc

    def assertSilent(self, **kw):
        proc = self.run_hook(**kw)
        self.assertIsNone(self.out(proc),
                          "expected silence for %r, got %r" % (kw, proc.stdout))
        return proc


# ======================================================================================
# 1. The frontend pattern
# ======================================================================================
class FrontendPatternPositives(ArmCase):
    """Every decisive extension, plus the path heuristic. One main-loop fire each: an injection
    means the path was classified frontend."""

    def _arms(self, path):
        # Fresh home per assertion: arming is once-per-session, so rows must not share state.
        self.setUp()
        self.assertArmed(file_path=path)

    def test_decisive_extensions(self):
        for p in ["src/App.tsx", "src/App.jsx", "src/Card.svelte", "src/Card.vue",
                  "src/main.css", "styles/_tokens.scss", "public/index.html",
                  "src/pages/about.astro"]:
            with self.subTest(path=p):
                self._arms(p)

    def test_windows_backslash_path(self):
        # Payload paths on this platform are backslashed; normalization is not optional.
        self._arms(r"C:\Users\x\proj\src\components\Button.tsx")

    def test_extensions_are_decisive_anywhere(self):
        # PINNED DECISION, not an oversight: no directory exclusion list. A generated report's
        # .html arms the session, and the injection's ignore-if-misfired line is the control.
        self._arms("coverage/lcov-report/index.html")
        self._arms("docs/architecture.html")

    def test_path_heuristic_promotes_script_files(self):
        # The heuristic's reason to exist: a styled-component / theme file whose extension says
        # nothing about the frontend.
        for p in ["src/components/Button.ts", "app/ui/theme.ts", "src/styles/tokens.js",
                  "web/templates/render.mjs", "src/layouts/Shell.ts", "src/views/list.cjs",
                  "site/partials/nav.js", "src/widgets/chart.ts", "app/pages/home.ts",
                  "src/component/Card.ts", "src/style/reset.ts"]:
            with self.subTest(path=p):
                self._arms(p)


class FrontendPatternNegatives(ArmCase):
    """Rows that must NOT arm. A main-loop fire on each: silence means not-frontend."""

    def test_ordinary_backend_and_config_files(self):
        for p in ["src/server.ts", "hooks/run.sh", "README.md", "package.json",
                  "src/lib/util.ts", "tests/api_test.py", "Cargo.toml", ".gitignore"]:
            with self.subTest(path=p):
                self.assertSilent(file_path=p)

    def test_segment_not_substring(self):
        # The heuristic matches a path SEGMENT. A filename that merely contains the word does not
        # arm -- this is why the regex is anchored to `/` on both sides.
        for p in ["src/parse-components.ts", "src/ui-helpers.ts", "src/theme-utils.ts",
                  "lib/pagestore.ts"]:
            with self.subTest(path=p):
                self.assertSilent(file_path=p)

    def test_segment_without_script_extension(self):
        # The extension gate on the heuristic: prose and assets under a component/style dir are
        # not frontend *edits* in the sense that needs a visual workflow.
        for p in ["docs/components/overview.md", "src/components/README.md",
                  "src/ui/icon.svg", "src/styles/notes.txt", "src/pages/data.json"]:
            with self.subTest(path=p):
                self.assertSilent(file_path=p)

    def test_missing_or_empty_file_path(self):
        self.assertSilent(file_path="")
        self.assertSilent(tool_input={})


# ======================================================================================
# 2. Once-per-session arming in the main loop
# ======================================================================================
class MainLoopArming(ArmCase):

    def test_first_touch_injects_once_and_records_instrumentation(self):
        self.assertArmed(file_path="src/App.tsx")
        st = self.state()
        self.assertTrue(st["armed"])
        self.assertEqual(st["armed_via"], "direct")
        self.assertIsInstance(st["armed_at"], float)
        self.assertFalse(st["pending_arm"])
        self.assertEqual(st["fires"]["main"], 1)
        self.assertEqual(st["fires"]["frontend"], 1)

    def test_second_frontend_fire_is_a_pure_no_op(self):
        self.assertArmed(file_path="src/App.tsx")
        self.assertSilent(file_path="src/Other.tsx")
        st = self.state()
        self.assertEqual(st["armed_via"], "direct")     # unchanged
        self.assertEqual(st["fires"]["total"], 2)       # still counted (the denominator)

    def test_state_lives_under_ballast_home_never_the_plugin_dir(self):
        self.assertArmed(file_path="src/main.css")
        self.assertTrue(os.path.isfile(self.state_file()))

    def test_systemmessage_accompanies_the_injection(self):
        proc = self.assertArmed(file_path="src/App.tsx")
        self.assertIn("ballast: visual-arm", self.out(proc)["systemMessage"])


# ======================================================================================
# 3. Leaf record + main-loop drain (the load-bearing R3 rule)
# ======================================================================================
class LeafRecordAndDrain(ArmCase):

    def test_leaf_frontend_touch_records_silently(self):
        self.assertSilent(file_path="src/App.tsx", caller=LEAF)
        st = self.state()
        self.assertTrue(st["pending_arm"])
        self.assertEqual(st["pending_arm_via"], "leaf")
        self.assertFalse(st["armed"])
        self.assertEqual(st["fires"]["leaf"], 1)

    def test_leaf_emits_nothing_at_all_not_even_stderr_noise(self):
        proc = self.run_hook(file_path="src/App.tsx", caller=LEAF)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(proc.returncode, 0)

    def test_agent_type_only_and_agent_id_only_both_read_as_leaf(self):
        for caller in ({"agent_type": "visual-glance"}, {"agent_id": "ag_9"}):
            with self.subTest(caller=caller):
                self.setUp()
                self.assertSilent(file_path="src/App.tsx", caller=caller)
                self.assertTrue(self.state()["pending_arm"])

    def test_next_main_loop_fire_on_a_NON_frontend_file_drains_the_record(self):
        # The drain is what makes the leaf path work: the main loop's next matched tool call
        # spends the pending flag, whatever file it touched.
        self.assertSilent(file_path="src/components/Button.tsx", caller=LEAF)
        proc = self.assertArmed(file_path="src/server.ts")
        self.assertIn(ARM_CLAUSE, self.injected(proc))
        st = self.state()
        self.assertTrue(st["armed"])
        self.assertEqual(st["armed_via"], "drain")
        self.assertFalse(st["pending_arm"])

    def test_main_loop_frontend_fire_with_pending_records_as_direct(self):
        self.assertSilent(file_path="src/App.tsx", caller=LEAF)
        self.assertArmed(file_path="src/Other.tsx")
        self.assertEqual(self.state()["armed_via"], "direct")

    def test_leaf_never_injects_even_with_pending_arm_already_set(self):
        self.assertSilent(file_path="src/App.tsx", caller=LEAF)
        self.assertSilent(file_path="src/Other.tsx", caller=LEAF)
        self.assertSilent(file_path="src/server.ts", caller=LEAF)
        self.assertFalse(self.state()["armed"])

    def test_leaf_fire_after_arming_is_silent(self):
        self.assertArmed(file_path="src/App.tsx")
        self.assertSilent(file_path="src/Other.tsx", caller=LEAF)

    def test_leaf_uses_the_parent_session_key(self):
        # B0a's frozen fact, pinned: the leaf's record and the main loop's drain must land in ONE
        # file. If a future CC gave leaves their own session_id, this test goes red and the drain
        # architecture needs revisiting -- that is the point of pinning it.
        self.assertSilent(file_path="src/App.tsx", caller=LEAF)
        self.assertEqual(sorted(os.listdir(self.state_dir())), ["arm-%s.json" % SESSION])


# ======================================================================================
# 4. Degraded mode: ambiguity records, it never injects
# ======================================================================================
class DegradedMode(ArmCase):
    """is_subagent() fails toward False = "main loop". For the lifecycle/install guards that is
    the safe direction (defer to the attended user); for ARMING it is the unsafe one (inject).
    The hook's resolution: injecting requires an affirmatively well-formed main-loop payload.
    These rows pin that resolution from both sides."""

    def test_discriminator_key_present_but_empty_is_ambiguous_not_main(self):
        # The exact shape is_subagent() alone would misread: the key exists (so this is not the
        # observed main-loop shape) but reads empty.
        for caller in ({"agent_type": ""}, {"agent_id": None},
                       {"agent_type": "", "agent_id": ""}):
            with self.subTest(caller=caller):
                self.setUp()
                self.assertSilent(file_path="src/App.tsx", caller=caller)
                st = self.state()
                self.assertTrue(st["pending_arm"], "ambiguous fire must RECORD")
                self.assertEqual(st["pending_arm_via"], "ambiguous")
                self.assertFalse(st["armed"], "ambiguous fire must never inject")

    def test_ambiguous_record_drains_on_a_clean_main_loop_fire(self):
        # Degraded mode costs one turn of latency, not the trigger.
        self.assertSilent(file_path="src/App.tsx", caller={"agent_type": ""})
        self.assertArmed(file_path="notes.txt")
        self.assertEqual(self.state()["armed_via"], "drain")

    def test_missing_session_id_never_injects(self):
        # No ledger key -> an injection could not be marked armed, so it would repeat forever.
        proc = self.run_hook(file_path="src/App.tsx", session=None, transcript="")
        o = self.out(proc)
        self.assertIsNone((o or {}).get("hookSpecificOutput"))
        self.assertFalse(os.path.isdir(self.state_dir()))

    def test_missing_session_id_falls_back_to_the_transcript_stem(self):
        # Documented-equivalent key (B0a: transcript stem == session_id).
        self.assertArmed(file_path="src/App.tsx", session=None)
        self.assertTrue(os.path.isfile(self.state_file("sess-abc-123")))

    def test_malformed_tool_input_never_injects(self):
        for ti in ("not-a-dict", [], {"file_path": 42}):
            with self.subTest(tool_input=ti):
                self.setUp()
                self.assertSilent(tool_input=ti)
                self.assertFalse(os.path.isdir(self.state_dir()))

    def test_session_key_is_sanitized_before_becoming_a_path(self):
        # A payload field is not a trusted path component. The property that matters is that no
        # SEPARATOR survives -- `.` stays in the allowed charset (same charset as
        # process-lifecycle-guard's valve key), so a literal `..` may remain in the name, but
        # without a separator it can never leave the state dir.
        self.assertArmed(file_path="src/App.tsx", session="../../evil/../x y")
        names = os.listdir(self.state_dir())
        self.assertEqual(len(names), 1)
        self.assertNotIn("/", names[0])
        self.assertNotIn("\\", names[0])
        self.assertTrue(os.path.samefile(
            os.path.dirname(os.path.join(self.state_dir(), names[0])), self.state_dir()))


# ======================================================================================
# 5. Fail-open, announced
# ======================================================================================
class FailOpen(ArmCase):

    def test_malformed_payload_announces_and_exits_zero(self):
        proc = self.run_hook(raw="{not json")
        o = self.out(proc)
        self.assertIn("internal error", o["systemMessage"])
        self.assertIsNone(o.get("hookSpecificOutput"), "an error path must never inject")

    def test_json_that_is_not_an_object_announces_and_exits_zero(self):
        proc = self.run_hook(raw="[1, 2, 3]")
        self.assertIn("internal error", self.out(proc)["systemMessage"])

    def test_empty_stdin_announces_and_exits_zero(self):
        proc = self.run_hook(raw="")
        self.assertIn("internal error", self.out(proc)["systemMessage"])

    def test_unwritable_state_dir_fails_toward_silence_not_injection(self):
        # A file where the state dir should be: makedirs raises, the broad except catches it, and
        # the fire ends announced-and-silent rather than injecting an un-recordable arm.
        os.makedirs(os.path.join(self.home, ".cache"), exist_ok=True)
        open(self.state_dir(), "w").close()
        proc = self.run_hook(file_path="src/App.tsx")
        o = self.out(proc)
        self.assertIsNone((o or {}).get("hookSpecificOutput"))


# ======================================================================================
# 6. Age-prune
# ======================================================================================
class AgePrune(ArmCase):

    def test_stale_state_files_are_pruned_on_dir_touch(self):
        os.makedirs(self.state_dir(), exist_ok=True)
        stale = os.path.join(self.state_dir(), "arm-old-session.json")
        with open(stale, "w", encoding="utf-8") as fh:
            json.dump({"armed": True}, fh)
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))

        self.assertArmed(file_path="src/App.tsx")
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(self.state_file()))

    def test_recent_state_files_survive(self):
        os.makedirs(self.state_dir(), exist_ok=True)
        fresh = os.path.join(self.state_dir(), "arm-other-session.json")
        with open(fresh, "w", encoding="utf-8") as fh:
            json.dump({"armed": True}, fh)
        old = time.time() - 2 * 86400
        os.utime(fresh, (old, old))

        self.assertArmed(file_path="src/App.tsx")
        self.assertTrue(os.path.exists(fresh))


if __name__ == "__main__":
    unittest.main(verbosity=2)
