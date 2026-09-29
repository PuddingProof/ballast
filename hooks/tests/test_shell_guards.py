#!/usr/bin/env python
"""Tests for shell-guards.py (the one Bash|PowerShell guard process) and run.sh's prefilter.

  (a) Merge precedence: any deny wins (every deny reason kept), else ask, else allow -- an allow
      never overrides another guard's ask (`ballast-extract npm install` asks).
  (b) A guard that fails to import, raises, or returns a bad decision is announced and skipped;
      the other guards still decide.
  (c) SUPERSET: every positive fixture in the three guard suites (test_package_install_guard,
      test_process_lifecycle_guard, test_ballast_allow) gets through run.sh's builtin prefilter.
      The fixtures are harvested by running those suites with subprocess stubbed out, then kept
      if shell-guards.py decides something for them -- so a new positive fixture is covered here
      without being listed twice.
  (d) Plain commands (`ls -la`, `git status`, `python x.py`) exit inside run.sh without starting
      python, next to a known-positive control that must start it.

(a) and (b) call shell-guards.py directly. (c) and (d) run
`bash "${BALLAST_RUN_SH:-hooks/run.sh}" shell-guards` with BALLAST_PYTHON pointed at a sentinel
script that records each call, so a temp copy of run.sh can be tested before it is swapped in.

HERMETIC: BALLAST_CLAUDE_HOME is a temp dir everywhere (ballast_allow's opt-in marker, run.sh's
fire ledger). Self-locating + standalone: `python hooks/tests/test_shell_guards.py`.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.dirname(HERE)
HOOK = os.path.join(HOOKS, "shell-guards.py")
RUN_SH = os.environ.get("BALLAST_RUN_SH") or os.path.join(HOOKS, "run.sh")
SUITES = ("test_package_install_guard.py", "test_process_lifecycle_guard.py", "test_ballast_allow.py")
LEAF = {"agent_type": "some-agent", "agent_id": "ag_1"}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sg = _load(HOOK, "shell_guards")   # hyphenated filename: load by path


def _payload(command, tool="Bash", background=None, **caller):
    ti = {"command": command}
    if background is not None:
        ti["run_in_background"] = background
    return dict({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti}, **caller)


def _opt_in_home():
    """A temp BALLAST_CLAUDE_HOME with ballast_allow's opt-in marker present."""
    home = tempfile.mkdtemp(prefix="ballast-shell-guards-test-")
    os.makedirs(os.path.join(home, "ballast"))
    open(os.path.join(home, "ballast", "allow-standing-grants"), "w").close()
    return home


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.home = _opt_in_home()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)

    def run_hook(self, payload):
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        s = proc.stdout.strip()
        return json.loads(s) if s else None

    def in_process(self, payload, guards=sg.GUARDS):
        old = os.environ.get("BALLAST_CLAUDE_HOME")
        os.environ["BALLAST_CLAUDE_HOME"] = self.home
        try:
            return sg.decide_all(payload, guards)
        finally:
            if old is None:
                del os.environ["BALLAST_CLAUDE_HOME"]
            else:
                os.environ["BALLAST_CLAUDE_HOME"] = old


# Stub guard modules for (a)/(b): written to a temp dir on sys.path for the test run.
FAKE_GUARDS = {
    "sgtest_allow": 'NAME = "sgtest-allow"\ndef decide(p):\n    return {"decision": "allow", "reason": "A", "banner": None}\n',
    "sgtest_ask": 'NAME = "sgtest-ask"\ndef decide(p):\n    return {"decision": "ask", "reason": "Q", "banner": "ask-banner"}\n',
    "sgtest_deny": 'NAME = "sgtest-deny"\ndef decide(p):\n    return {"decision": "deny", "reason": "D1", "banner": "deny-banner-1"}\n',
    "sgtest_deny2": 'NAME = "sgtest-deny2"\ndef decide(p):\n    return {"decision": "deny", "reason": "D2", "banner": "deny-banner-2"}\n',
    "sgtest_boom": 'NAME = "sgtest-boom"\ndef decide(p):\n    raise RuntimeError("boom")\n',
    "sgtest_weird": 'NAME = "sgtest-weird"\ndef decide(p):\n    return {"decision": "maybe", "reason": "?", "banner": None}\n',
    "sgtest_bad_import": 'raise ImportError("broken module")\n',
}


class FakeGuardCase(HomeCase):
    @classmethod
    def setUpClass(cls):
        cls.fake_dir = tempfile.mkdtemp(prefix="ballast-shell-guards-fakes-")
        for name, body in FAKE_GUARDS.items():
            with open(os.path.join(cls.fake_dir, name + ".py"), "w", encoding="utf-8") as f:
                f.write(body)
        sys.path.insert(0, cls.fake_dir)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.fake_dir)
        for name in FAKE_GUARDS:
            sys.modules.pop(name, None)
        shutil.rmtree(cls.fake_dir, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# (a) merge precedence
# ---------------------------------------------------------------------------------------------
class MergePrecedence(FakeGuardCase):
    def test_deny_beats_ask_beats_allow(self):
        out = self.in_process(_payload("x"), ("sgtest_allow", "sgtest_ask", "sgtest_deny"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "D1")

    def test_ask_beats_allow(self):
        out = self.in_process(_payload("x"), ("sgtest_allow", "sgtest_ask"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "Q")

    def test_every_deny_reason_and_banner_is_kept(self):
        out = self.in_process(_payload("x"), ("sgtest_deny", "sgtest_ask", "sgtest_deny2"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "D1\nD2")
        self.assertEqual(out["systemMessage"], "deny-banner-1 · ask-banner · deny-banner-2")

    def test_ballast_extract_npm_install_asks(self):
        # The live allow guard must not wave through an install riding its bare-shim shape.
        out = self.run_hook(_payload("ballast-extract npm install"))
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "ask")
        self.assertIn("Package install detected", hso["permissionDecisionReason"])
        self.assertNotIn("transcript extractor", hso["permissionDecisionReason"])

    def test_leaf_deny_beats_allow(self):
        out = self.run_hook(_payload("ballast-sweep digest", background=True, **LEAF))
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("postmortem-corpus", hso["permissionDecisionReason"])

    def test_two_live_denies_keep_both_reasons(self):
        out = self.run_hook(_payload("npx next dev", **LEAF))
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("Standing-service launch blocked", hso["permissionDecisionReason"])
        self.assertIn("Remote package execution ('npx') blocked", hso["permissionDecisionReason"])
        self.assertIn("process-lifecycle-guard", out["systemMessage"])
        self.assertIn("package-install-guard", out["systemMessage"])

    def test_no_guard_decides_is_silent(self):
        self.assertIsNone(self.run_hook(_payload("ls -la")))


# ---------------------------------------------------------------------------------------------
# (b) a broken guard is announced and skipped
# ---------------------------------------------------------------------------------------------
class BrokenGuardIsContained(FakeGuardCase):
    def assert_contained(self, broken, name):
        out = self.in_process(_payload("npm install foo"), (broken, "package_install_guard"))
        self.assertIn("⚠️ ballast: %s — internal error, skipped" % name, out["systemMessage"])
        self.assertIn("package-install-guard", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_raising_guard(self):
        self.assert_contained("sgtest_boom", "sgtest-boom")

    def test_guard_that_fails_to_import(self):
        self.assert_contained("sgtest_bad_import", "sgtest-bad-import")

    def test_missing_guard_module(self):
        self.assert_contained("sgtest_no_such_module", "sgtest-no-such-module")

    def test_unknown_decision_value(self):
        self.assert_contained("sgtest_weird", "sgtest-weird")

    def test_malformed_payload_is_announced_and_exits_0(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json",
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertIn("shell-guards", out["systemMessage"])
        self.assertNotIn("hookSpecificOutput", out)


# ---------------------------------------------------------------------------------------------
# (c) / (d) run.sh prefilter, observed through a sentinel BALLAST_PYTHON
# ---------------------------------------------------------------------------------------------
def harvest_positive_payloads(home):
    """Every stdin payload the three guard suites send, kept if shell-guards decides on it."""
    raw = []

    class Recorder:
        @staticmethod
        def run(args, input=None, **kw):
            if isinstance(input, str):
                raw.append(input)
            return subprocess.CompletedProcess(args, 0, "", "")

    for f in SUITES:
        mod = _load(os.path.join(HERE, f), "sgharvest_" + f[:-3])
        mod.subprocess = Recorder
        unittest.defaultTestLoader.loadTestsFromModule(mod).run(unittest.TestResult())

    old = os.environ.get("BALLAST_CLAUDE_HOME")
    os.environ["BALLAST_CLAUDE_HOME"] = home
    try:
        positives = []
        for s in dict.fromkeys(raw):
            try:
                p = json.loads(s)
            except ValueError:
                continue
            if isinstance(p, dict) and "hookSpecificOutput" in (sg.decide_all(p) or {}):
                positives.append(s)
        return positives
    finally:
        if old is None:
            del os.environ["BALLAST_CLAUDE_HOME"]
        else:
            os.environ["BALLAST_CLAUDE_HOME"] = old


class RunShPrefilter(HomeCase):
    def setUp(self):
        super().setUp()
        self.bash = shutil.which("bash")
        self.assertIsNotNone(self.bash, "bash not found on PATH")
        self.log = os.path.join(self.home, "sentinel.log").replace("\\", "/")
        sentinel = os.path.join(self.home, "sentinel-python").replace("\\", "/")
        with open(sentinel, "w", encoding="utf-8", newline="\n") as f:
            f.write('#!/bin/sh\nprintf x >> "%s"\n' % self.log)
        os.chmod(sentinel, 0o755)
        self.env["BALLAST_PYTHON"] = sentinel

    def reaches_python(self, payload_text):
        if os.path.exists(self.log):
            os.remove(self.log)
        subprocess.run([self.bash, RUN_SH.replace("\\", "/"), "shell-guards"], input=payload_text,
                       capture_output=True, text=True, env=self.env, timeout=30)
        return os.path.exists(self.log)

    def test_c_every_positive_fixture_passes_the_prefilter(self):
        positives = harvest_positive_payloads(self.home)
        self.assertGreater(len(positives), 50, "harvest found too few positive fixtures")
        misses = [p for p in positives if not self.reaches_python(p)]
        if misses:
            self.fail("%d/%d positive fixtures never reached python; first: %s"
                      % (len(misses), len(positives), misses[:3]))

    def test_d_plain_commands_never_start_python(self):
        self.assertTrue(self.reaches_python(json.dumps(_payload("npm install foo"))),
                        "control: a matching command must reach python -- is shell-guards "
                        "registered in run.sh?")
        for cmd in ("ls -la", "git status", "python x.py"):
            self.assertFalse(self.reaches_python(json.dumps(_payload(cmd))),
                             "%r started python" % cmd)


if __name__ == "__main__":
    unittest.main()
