#!/usr/bin/env python
"""String-pin: the gate's dispatch-brief template, and its couplings to the pin and the harness.

WHY PINNED: the brief is the ONLY contract a dispatched leaf gets — it loads no skill. Every slot in
it exists because a leaf that had to discover the value instead spent turns on it, or because a
missing slot produced a wrong answer that still looked right:

  Epoch / --since   a re-used out-dir always holds a previous cycle's complete manifest, so a wait
                    with no epoch resolves on it instantly and the leaf reviews the build the
                    dispatch was meant to replace.
  --epoch           the same window on the capture side, so the budget echo counts THIS dispatch.
  Declared holes    an axis the caller chose not to shoot is a hole; unstated, it is a silent pass.
  Native cell       the surface's real size, judged there and not at a nearby cell.
  --suppressions    declared-intended rung-0 findings, for a project with no state manifest.

Each slot is also pinned against the pin writer (`hooks/visual_origin_ledger.py`) and the harness
flag it fills, so a rename on either side fails here instead of in a live dispatch.

Self-locating, stdlib only. Run: python skills/visual-verification-gate/scripts/test_brief_template.py
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GATE = os.path.join(ROOT, "skills", "visual-verification-gate", "SKILL.md")
LEDGER = os.path.join(ROOT, "hooks", "visual_origin_ledger.py")
PROBE = os.path.join(ROOT, "skills", "visual-probe", "scripts", "probe.mjs")

# Every slot the template must carry, as the literal a filled brief keeps.
SLOTS = [
    "Origin (REQUIRED):",
    "Out-dir:",
    "Epoch:",
    "Target + intent:",
    "Declared holes (not shot):",
    "Native cell:",
    "Expected budget:",
    "Exit codes:",
]

# Harness flags the template hands the leaf, each of which must exist in the harness.
FLAGS = ["--url", "--matrix", "--states", "--skip-drive-hooks", "--suppressions", "--settle",
         "--epoch", "--out", "--wait", "--since", "--timeout"]

# Pin fields the brief is filled from — the template's `<pin.x>` placeholders.
PIN_FIELDS = ["origin", "out_dir", "matrix", "native", "suppressions", "states_manifest", "settle"]


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def brief_template(gate_text):
    for block in re.findall(r"```\n(.*?)```", gate_text, re.S):
        if block.lstrip().startswith("Origin (REQUIRED):"):
            return block
    return None


class BriefTemplate(unittest.TestCase):
    def setUp(self):
        self.gate = read(GATE)
        self.brief = brief_template(self.gate)
        self.assertIsNotNone(self.brief, "the gate skill has no fenced dispatch-brief block")

    def test_every_slot_is_present(self):
        for slot in SLOTS:
            self.assertIn(slot, self.brief, "the brief template lost the %r slot" % slot)

    def test_every_flag_it_hands_the_leaf_exists_in_the_harness(self):
        probe = read(PROBE)
        for flag in FLAGS:
            self.assertIn(flag, self.brief, "the brief template no longer passes %s" % flag)
            self.assertIn(flag, probe, "%s is in the brief but not in the harness" % flag)

    def test_the_epoch_threads_through_both_commands(self):
        # One dispatch window, stamped on the capture and re-asserted by the wait: either half alone
        # leaves the other free to resolve on last cycle's evidence.
        self.assertIn("--epoch", self.brief)
        self.assertRegex(self.brief, r"--wait <pin\.out_dir> --since")
        self.assertIn("Epoch:", self.brief)
        self.assertIn("--since", self.gate.split("## The dispatch brief")[0],
                      "the capture-ahead stage no longer states the --since half of the epoch")

    def test_every_pin_placeholder_is_a_field_the_pin_writer_emits(self):
        used = set(re.findall(r"<pin\.([a-z_]+)>", self.brief))
        self.assertTrue(used, "the brief no longer templates from the pin")
        ledger = read(LEDGER)
        for field in used:
            self.assertIn('"%s"' % field, ledger,
                          "the brief fills <pin.%s>, which the pin writer does not emit" % field)
        for field in ("origin", "out_dir", "matrix", "native", "suppressions"):
            self.assertIn(field, used, "the brief stopped filling <pin.%s>" % field)

    def test_the_pin_command_offers_every_field_the_brief_fills(self):
        setup = self.gate.split("## 3.")[0]
        for flag in ("--native", "--suppressions", "--matrix", "--states", "--settle"):
            self.assertIn(flag, setup, "the setup stage's pin command no longer offers %s" % flag)

    def test_declared_holes_bind_both_rungs_and_the_pass_row(self):
        # A declared hole is only worth stating if it survives into the done-claim.
        self.assertIn("Declared holes (not shot):", self.brief)
        pass_row = [ln for ln in self.gate.splitlines() if ln.startswith("| `pass` |")]
        self.assertTrue(pass_row, "the verdict table lost its `pass` row")
        self.assertIn("declared hole", pass_row[0].lower(),
                      "the `pass` row no longer carries declared holes into the done-claim")
        for body in ("visual-glance.md", "visual-reviewer.md"):
            text = read(os.path.join(ROOT, "agents", body))
            self.assertIn("declared", text.lower(),
                          "%s no longer takes the brief's declared holes into its scope" % body)

    def test_the_budget_echo_names_the_dispatch_scoped_count(self):
        self.assertIn("invocations_total", self.brief,
                      "the brief no longer distinguishes this dispatch's count from the out-dir's history")


if __name__ == "__main__":
    unittest.main(verbosity=2)
