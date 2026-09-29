#!/usr/bin/env python
"""String-pin: the dispatch brief `ballast-visual-origin brief` prints, and its couplings to the pin,
the harness, and the prose that refers to it.

WHY PINNED: the brief is the ONLY contract a dispatched leaf gets -- it loads no skill. It is printed
from the pin rather than typed because every hand-filled brief that went wrong (no runnable command,
a relative URL resolved as a file path, a shorthand run literally) cost a `blocked` round trip. Each
slot exists because a leaf that had to discover the value spent turns on it, or because a missing
slot produced a wrong answer that still looked right:

  Epoch             a re-used out-dir holds a previous cycle's complete manifest; the capture's own
                    printed DISPATCH line carries the epoch, copied, never hand-composed.
  Declared holes    an axis the caller chose not to shoot is a hole; unstated, it is a silent pass.
  Native cell       the surface's real size, judged there and not at a nearby cell.
  --ready           a cell captured before the app is ready reads as a false needs_work / blocked.

Every flag the printed command hands the leaf must exist in the harness, so a rename on either side
fails here instead of in a live dispatch.

Self-locating, stdlib only. Run: python skills/visual-probe/scripts/test_brief_template.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SKILL = os.path.join(ROOT, "skills", "visual-probe", "SKILL.md")
LEDGER = os.path.join(ROOT, "hooks", "visual_origin_ledger.py")
PROBE = os.path.join(ROOT, "skills", "visual-probe", "scripts", "probe.mjs")

# Every slot the printed brief must carry, as the literal the leaf bodies refer to.
SLOTS = [
    "Origin (REQUIRED):",
    "Out-dir:",
    "Epoch:",
    "Target + intent:",
    "Declared holes (not shot):",
    "Native cell:",
    "Run this first",
    "Expected budget:",
    "Exit codes:",
]

# Harness flags the printed command hands the leaf (with every pin field set), each of which must
# exist in the harness.
FLAGS = ["--url", "--matrix", "--states", "--skip-drive-hooks", "--suppressions", "--settle",
         "--out", "--ready"]


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class PrintedBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brief-template-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ, BALLAST_CLAUDE_HOME=self.tmp)
        self.out = os.path.join(self.tmp, "vp-out")
        r = self.cli("pin", "--session-key", "s1", "--url", "http://127.0.0.1:53079",
                     "--out-dir", self.out, "--matrix", "1440x900@1,390x844@1",
                     "--native", "1440x900@1", "--states", os.path.join(self.tmp, "visual-states.json"),
                     "--suppressions", os.path.join(self.tmp, "vp-suppressions.json"),
                     "--settle", "800", "--ready", "[data-app-ready]")
        self.assertEqual(r.returncode, 0, r.stderr)

    def cli(self, *args):
        return subprocess.run([sys.executable, LEDGER] + list(args), capture_output=True,
                              encoding="utf-8", timeout=60, env=self.env)

    def brief(self, rung="glance", *extra):
        r = self.cli("brief", "--out-dir", self.out, "--rung", rung, "--intent", "the hero fits", *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_slot_is_printed(self):
        text = self.brief()
        for slot in SLOTS:
            self.assertIn(slot, text, "the printed brief lost the %r slot" % slot)

    def test_every_flag_it_hands_the_leaf_exists_in_the_harness(self):
        text, probe = self.brief(), read(PROBE)
        for flag in FLAGS:
            self.assertIn(flag + " ", text, "the printed command no longer passes %s" % flag)
            self.assertIn(flag, probe, "%s is in the brief but not in the harness" % flag)

    def test_the_command_runs_the_bundled_harness_by_absolute_quoted_path(self):
        self.assertTrue(os.path.isfile(PROBE))
        text = self.brief()
        self.assertIn('  node "%s" glance --url ' % PROBE, text)
        self.assertTrue(os.path.isabs(PROBE))

    def test_the_review_rung_runs_review_capture(self):
        self.assertIn('" review-capture --url ', self.brief("review"))

    def test_the_epoch_is_copied_never_composed(self):
        text = self.brief()
        self.assertIn("DISPATCH", text)
        self.assertNotIn("--epoch", text, "the brief re-grew a hand-filled --epoch slot")

    def test_the_budget_echo_names_the_dispatch_scoped_count(self):
        self.assertIn("invocations_total", self.brief(),
                      "the brief no longer distinguishes this dispatch's count from the out-dir's history")

    def test_no_pin_is_a_usage_error(self):
        r = self.cli("brief", "--out-dir", os.path.join(self.tmp, "nope"), "--rung", "glance",
                     "--intent", "x")
        self.assertEqual(r.returncode, 2)


class ProseCouplings(unittest.TestCase):
    """The skill and agent bodies that refer to the printed brief."""

    def test_the_skill_keeps_the_copied_epoch_rule(self):
        skill = read(SKILL)
        for literal in ("--since", "DISPATCH", "hand-composed"):
            self.assertIn(literal, skill, "the visual-probe skill lost %r" % literal)

    def test_declared_holes_bind_the_pass_row_and_both_leaves(self):
        # A declared hole is only worth stating if it survives into the done-claim.
        pass_row = [ln for ln in read(SKILL).splitlines() if ln.startswith("| `pass` |")]
        self.assertTrue(pass_row, "the verdict table lost its `pass` row")
        self.assertIn("declared hole", pass_row[0].lower(),
                      "the `pass` row no longer carries declared holes into the done-claim")
        for body in ("visual-glance.md", "visual-reviewer.md"):
            text = read(os.path.join(ROOT, "agents", body))
            self.assertIn("declared", text.lower(),
                          "%s no longer takes the brief's declared holes into its scope" % body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
