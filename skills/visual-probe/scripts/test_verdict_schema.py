#!/usr/bin/env python
"""String-pin: the two visual leaves' verdict blocks, and the gate's coupling to them.

WHY PINNED: a dispatched leaf's final message IS its return value, and the orchestrator reads it by
HEADING. A renamed or dropped heading breaks that contract silently — the leaf still answers, the
caller still reads prose, and a hole or a budget overrun stops being machine-locatable. The verdict
ENUM is pinned for the same reason from the other side: the gate's response table is keyed on it, so
a verdict either rung can emit but the table does not name has no defined obligation.

This pins literals, not quality: it fails when a heading or a verdict word moves, which is exactly
when the visual-probe skill's table and both agent bodies have to move together.

Self-locating, stdlib only. Run: python skills/visual-probe/scripts/test_verdict_schema.py
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GLANCE = os.path.join(ROOT, "agents", "visual-glance.md")
REVIEWER = os.path.join(ROOT, "agents", "visual-reviewer.md")
GATE = os.path.join(ROOT, "skills", "visual-probe", "SKILL.md")

# The block headings each body must carry, in the order a reader meets them.
GLANCE_HEADINGS = ["VERDICT:", "SCOPE:", "INTENT:", "FINDINGS", "RUNG-0", "HOLES"]
REVIEWER_HEADINGS = ["VERDICT:", "SCOPE:", "INTENT:", "BLIND SPOTS:", "FINDINGS", "DELTA"]

GLANCE_VERDICTS = ["pass", "needs_work", "needs_fixture", "escalate", "blocked"]
REVIEWER_VERDICTS = ["pass", "pass_partial", "needs_work", "needs_fixture", "blocked"]


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fenced_blocks(text):
    return re.findall(r"```\n(.*?)```", text, re.S)


def verdict_block(text, first_heading="VERDICT:"):
    for block in fenced_blocks(text):
        if block.lstrip().startswith(first_heading):
            return block
    return None


class VerdictBlockSchema(unittest.TestCase):
    def test_glance_block_carries_every_heading_in_order(self):
        block = verdict_block(read(GLANCE))
        self.assertIsNotNone(block, "visual-glance.md has no fenced VERDICT block")
        pos = [block.find(h) for h in GLANCE_HEADINGS]
        for h, i in zip(GLANCE_HEADINGS, pos):
            self.assertGreaterEqual(i, 0, "glance verdict block lost the %r heading" % h)
        self.assertEqual(pos, sorted(pos), "glance verdict headings are out of order: %s" % pos)

    def test_reviewer_block_carries_every_heading_in_order(self):
        block = verdict_block(read(REVIEWER))
        self.assertIsNotNone(block, "visual-reviewer.md has no fenced VERDICT block")
        pos = [block.find(h) for h in REVIEWER_HEADINGS]
        for h, i in zip(REVIEWER_HEADINGS, pos):
            self.assertGreaterEqual(i, 0, "reviewer verdict block lost the %r heading" % h)
        self.assertEqual(pos, sorted(pos), "reviewer verdict headings are out of order: %s" % pos)

    def test_each_rung_states_its_own_enum(self):
        # The enums live in the bodies as earning criteria and in the gate as obligations; a verdict
        # one side knows and the other doesn't is a leaf answer with no defined response.
        glance, reviewer = read(GLANCE), read(REVIEWER)
        for v in GLANCE_VERDICTS:
            self.assertIn("`%s`" % v, glance, "visual-glance.md no longer names the %r verdict" % v)
        for v in REVIEWER_VERDICTS:
            self.assertIn("`%s`" % v, reviewer, "visual-reviewer.md no longer names the %r verdict" % v)

    def test_the_gate_table_answers_every_verdict_both_rungs_emit(self):
        gate = read(GATE)
        rows = set(re.findall(r"^\| `([a-z_]+)` \|", gate, re.M))
        for v in set(GLANCE_VERDICTS) | set(REVIEWER_VERDICTS):
            self.assertIn(v, rows, "the gate's verdict table has no row for %r" % v)

    def test_scope_line_carries_the_budget_echo(self):
        # Cost is checkable only if it lands on the line the caller already reads.
        for path in (GLANCE, REVIEWER):
            block = verdict_block(read(path))
            scope = [ln for ln in block.splitlines() if ln.startswith("SCOPE:")]
            self.assertTrue(scope, "%s: no SCOPE line" % os.path.basename(path))
            self.assertIn("budget:", scope[0], "%s: SCOPE line dropped the budget echo" % os.path.basename(path))
            self.assertIn("<invocations>", scope[0],
                          "%s: SCOPE line no longer echoes the dispatch-scoped invocation count"
                          % os.path.basename(path))

    def test_glance_scope_names_the_viewport_clamp(self):
        # The glance is viewport-scoped by design; below-fold page is unseen SCOPE, and silence there
        # reads as "the whole page was fine".
        block = verdict_block(read(GLANCE))
        scope = [ln for ln in block.splitlines() if ln.startswith("SCOPE:")][0]
        self.assertIn("viewport-clamped", scope)
        self.assertIn("below-fold unseen", scope)


if __name__ == "__main__":
    unittest.main(verbosity=2)
