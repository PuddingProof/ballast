#!/usr/bin/env python
"""Regression tests for diff-review-cap.py.

The hook filename is hyphenated (not importable), so each case invokes it as a subprocess with
the current interpreter, feeds a PreToolUse JSON payload on stdin, and asserts on the emitted
decision. Pins: the Opus ceiling (never consumes a slot), the depth-aware per-kind caps from
the hook's CAPS table, per-fork keying, the concurrent-claim race (the reason slots are claimed
with O_EXCL), and fail-open on malformed input or an unwritable state dir.

HERMETIC (hooks/CLAUDE.md rule): every invocation sets BALLAST_CLAUDE_HOME to a temp dir, so the
slot state is never the real ~/.claude. Production never sets that var.

Self-locating + standalone: `python hooks/tests/test_diff_review_cap.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "diff-review-cap.py")

VERIFIER = "ballast:diff-verifier"
FINDER = "ballast:diff-finder"


def prompt_for(depth):
    return "Review the diff.\ndepth: %s\n" % depth if depth else "Review the diff."


class CapCase(unittest.TestCase):
    """Base: a temp BALLAST_CLAUDE_HOME per test, plus the run/assert helpers."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ballast-diff-review-cap-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def state_root(self):
        return os.path.join(self.home, ".cache", "ballast-diff-review-cap")

    def payload(self, subagent_type=VERIFIER, model=None, depth="hard", prompt=None,
                session="sess-1", agent_id="fork-1"):
        ti = {"subagent_type": subagent_type,
              "prompt": prompt if prompt is not None else prompt_for(depth)}
        if model is not None:
            ti["model"] = model
        p = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": ti,
             "session_id": session}
        if agent_id is not None:
            p["agent_id"] = agent_id
        return p

    def run_raw(self, raw, home=None):
        env = dict(os.environ, BALLAST_CLAUDE_HOME=home or self.home)
        return subprocess.run([sys.executable, HOOK], input=raw, capture_output=True,
                              text=True, env=env)

    def run_hook(self, **kw):
        return self.run_raw(json.dumps(self.payload(**kw)))

    def out(self, proc):
        """Parsed stdout JSON, or None when the hook stayed silent."""
        self.assertEqual(proc.returncode, 0, "hook must always exit 0: %s" % proc.stderr)
        s = proc.stdout.strip()
        return json.loads(s) if s else None

    def decision(self, proc):
        o = self.out(proc)
        return None if o is None else o.get("hookSpecificOutput", {}).get("permissionDecision")

    def reason(self, proc):
        return self.out(proc)["hookSpecificOutput"]["permissionDecisionReason"]

    def assertSilent(self, **kw):
        proc = self.run_hook(**kw)
        self.assertIsNone(self.out(proc), "expected silence for %r, got %r" % (kw, proc.stdout))

    def assertDenied(self, **kw):
        proc = self.run_hook(**kw)
        self.assertEqual(self.decision(proc), "deny",
                         "expected deny for %r, got %r" % (kw, proc.stdout))
        return proc


class NonTarget(CapCase):
    def test_non_target_type_silent_no_state(self):
        for t in ("general-purpose", "Explore", "ballast:plan-executor", "diff-verifier-x", ""):
            self.assertSilent(subagent_type=t, model="fable")
        self.assertFalse(os.path.exists(self.state_root()))

    def test_finder_no_model_allowed(self):
        self.assertSilent(subagent_type=FINDER)


class VerifierCap(CapCase):
    def test_hard_slots_1_to_4_then_deny(self):
        for _ in range(4):
            self.assertSilent()
        proc = self.assertDenied()
        self.assertIn("self-verified", self.reason(proc))
        self.assertIn("yourself in-context", self.reason(proc))
        self.assertIn("verifier cap (4, hard)", self.out(proc)["systemMessage"])

    def test_separate_agent_id_fresh_budget(self):
        for _ in range(4):
            self.assertSilent(agent_id="fork-a")
        self.assertDenied(agent_id="fork-a")
        self.assertSilent(agent_id="fork-b")

    def test_separate_session_fresh_budget(self):
        for _ in range(4):
            self.assertSilent(session="s-a")
        self.assertDenied(session="s-a")
        self.assertSilent(session="s-b")

    def test_absent_agent_id_is_main_key_and_counted(self):
        for _ in range(4):
            self.assertSilent(agent_id=None)
        self.assertDenied(agent_id=None)
        self.assertTrue(os.path.isdir(os.path.join(self.state_root(), "sess-1-main", "verifier")))

    def test_namespaced_and_bare_share_budget(self):
        self.assertSilent(subagent_type="ballast:diff-verifier")
        self.assertSilent(subagent_type="diff-verifier")
        self.assertSilent(subagent_type="ballast:diff-verifier")
        self.assertSilent(subagent_type="diff-verifier")
        self.assertDenied(subagent_type="diff-verifier")
        self.assertDenied(subagent_type="ballast:diff-verifier")

    def test_medium_verifier_denied_first_no_state(self):
        proc = self.assertDenied(depth="medium")
        self.assertIn("--medium", self.reason(proc))
        self.assertIn("no verifier leaves", self.reason(proc))
        self.assertIn("no verifier leaves at --medium", self.out(proc)["systemMessage"])
        self.assertFalse(os.path.exists(self.state_root()))

    def test_light_verifier_denied(self):
        self.assertDenied(depth="light")
        self.assertFalse(os.path.exists(self.state_root()))

    def test_concurrent_fires_admit_exactly_cap(self):
        # The fork dispatches verifiers in one parallel message: 8 simultaneous fires for one
        # key must yield exactly 4 allows -- a read-increment-write counter would over-admit.
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        raw = json.dumps(self.payload())
        procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env) for _ in range(8)]
        # Feed every stdin before awaiting any, so the claims genuinely overlap.
        for p in procs:
            p.stdin.write(raw)
            p.stdin.close()
        results = [(p.stdout.read(), p.stderr.read()) for p in procs]
        for p in procs:
            p.wait()
            p.stdout.close()
            p.stderr.close()
        for p in procs:
            self.assertEqual(p.returncode, 0)
        allows = sum(1 for so, _ in results if not so.strip())
        denies = sum(1 for so, _ in results if so.strip() and json.loads(so)
                     ["hookSpecificOutput"]["permissionDecision"] == "deny")
        self.assertEqual((allows, denies), (4, 4))


class FinderCap(CapCase):
    def test_light_finder_denied(self):
        proc = self.assertDenied(subagent_type=FINDER, depth="light")
        self.assertIn("--light", self.reason(proc))
        self.assertFalse(os.path.exists(self.state_root()))

    def test_medium_finders_1_to_6_then_deny(self):
        for _ in range(6):
            self.assertSilent(subagent_type=FINDER, depth="medium")
        proc = self.assertDenied(subagent_type=FINDER, depth="medium")
        self.assertIn("at most 6 finder leaves", self.reason(proc))
        self.assertIn("finder cap (6, medium) reached", self.out(proc)["systemMessage"])

    def test_hard_finders_1_to_11_then_deny(self):
        for _ in range(11):
            self.assertSilent(subagent_type=FINDER, depth="hard")
        proc = self.assertDenied(subagent_type=FINDER, depth="hard")
        self.assertIn("at most 11 finder leaves", self.reason(proc))

    def test_finder_and_verifier_budgets_independent(self):
        for _ in range(4):
            self.assertSilent(subagent_type=VERIFIER)
        self.assertDenied(subagent_type=VERIFIER)
        for _ in range(11):
            self.assertSilent(subagent_type=FINDER)
        self.assertDenied(subagent_type=FINDER)


class DepthParse(CapCase):
    """Parsed depth observed through the finder cap: medium allows 6, hard 11, light 0."""

    def fill_count(self, prompt, fork):
        n = 0
        while n < 20:
            proc = self.run_hook(subagent_type=FINDER, prompt=prompt, agent_id=fork)
            if self.decision(proc) == "deny":
                return n
            n += 1
        return n

    def test_variants(self):
        cases = [
            ("depth: medium", 6),
            ('{"depth": "hard"}', 11),
            ("depth=light", 0),
            ("DEPTH: Medium", 6),
            ("no depth field here", 11),
            ("depth: medium\n... later depth: hard", 6),  # first match wins
            ("give it an in-depth hard look\ndepth: medium", 6),  # word boundary: prose skipped
            ("depth: --medium", 6),  # the level files' own spelling
            ("**depth**: medium", 6),
            ("- depth: `medium`", 6),
            ("`depth`: `light`", 0),
        ]
        for i, (prompt, want) in enumerate(cases):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.fill_count(prompt, "fork-%d" % i), want)


class OpusCeiling(CapCase):
    def test_fable_denied_for_finder_and_verifier(self):
        for t in (FINDER, VERIFIER, "diff-finder", "diff-verifier"):
            for m in ("fable", "FABLE", "claude-fable-5-1", " Fable "):
                with self.subTest(t=t, m=m):
                    proc = self.assertDenied(subagent_type=t, model=m)
                    self.assertIn('model: "opus"', self.reason(proc))
                    self.assertIn("opus ceiling", self.out(proc)["systemMessage"])

    def test_fable_does_not_consume_slot(self):
        for _ in range(5):
            self.assertDenied(model="fable")
        for _ in range(4):
            self.assertSilent()
        self.assertDenied()

    def test_fable_checked_before_depth_cap(self):
        proc = self.assertDenied(model="fable", depth="medium")
        self.assertIn("opus", self.reason(proc))

    def test_other_models_allowed(self):
        for m in ("opus", "sonnet", None, ""):
            with self.subTest(m=m):
                self.assertSilent(subagent_type=FINDER, model=m)


class FailOpen(CapCase):
    def assertFailOpen(self, proc):
        self.assertEqual(proc.returncode, 0)
        o = self.out(proc)
        self.assertIsNotNone(o, "fail-open must be announced")
        self.assertNotIn("hookSpecificOutput", o)
        self.assertIn("internal error", o["systemMessage"])

    def test_malformed_json(self):
        self.assertFailOpen(self.run_raw("{not json"))

    def test_json_list(self):
        self.assertFailOpen(self.run_raw("[]"))

    def test_tool_input_non_dict(self):
        self.assertFailOpen(self.run_raw(json.dumps({"tool_input": "diff-verifier"})))

    def test_unwritable_state_dir_allows(self):
        blocker = os.path.join(self.home, "not-a-dir")
        open(blocker, "w").close()
        proc = self.run_raw(json.dumps(self.payload()), home=blocker)
        self.assertFailOpen(proc)


if __name__ == "__main__":
    unittest.main()
