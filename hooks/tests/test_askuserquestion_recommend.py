#!/usr/bin/env python
"""Regression tests for askuserquestion-recommend.py -- the hard-block (exit 2) PreToolUse
hook enforcing "options[0].label must contain '(Recommended)' for every question".

The hook filename is hyphenated (not importable via a normal statement), so each case invokes
it as a subprocess with the current interpreter, feeds a PreToolUse JSON payload on stdin, and
asserts on returncode + stderr text -- matching test_package_install_guard.py's convention.

No BALLAST_CLAUDE_HOME override needed: the hook reads only stdin, no ~/.claude-rooted state.

Decision logic under test (derived from the hook's own code, not from its header prose):
  - malformed JSON stdin                                -> exit 0 (fail open)
  - tool_input.questions missing / not a list / empty    -> exit 0 (fail open)
  - a question that isn't a dict                          -> skipped, doesn't count as bad
  - a question whose options is missing/empty/not a list,
    or whose options[0] isn't a dict                       -> skipped ("let the tool's own
                                                               validation handle it")
  - per question: options[0]["label"] (case-insensitive)
    must contain the substring "(recommended)"            -> else that question is "bad"
  - "(Recommended)" living only in an option's DESCRIPTION
    (not the label) does NOT satisfy the rule              -> still bad
  - any bad question at all                                -> exit 2, stderr names each bad
                                                               question, stdout stays empty
                                                               (no systemMessage on the block
                                                               path -- see file header)
  - all questions fine (or all skipped as malformed)        -> exit 0, silent stdout/stderr
  - the hook does not itself branch on tool_name -- the
    AskUserQuestion scoping is enforced by the settings.json
    matcher, not by this script; a non-AskUserQuestion
    payload fails open here purely because its tool_input
    naturally lacks a "questions" list.

Self-locating + standalone: `python hooks/tests/test_askuserquestion_recommend.py`.
"""
import json
import os
import subprocess
import sys
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "askuserquestion-recommend.py")


def option(label, description=None):
    o = {"label": label}
    if description is not None:
        o["description"] = description
    return o


def question(header, options, q_text=None):
    q = {"header": header, "options": options}
    if q_text is not None:
        q["question"] = q_text
    return q


def run_hook(payload_text):
    """Feed raw text on stdin (lets the malformed-JSON test pass non-JSON directly)."""
    return subprocess.run(
        [sys.executable, HOOK], input=payload_text, capture_output=True, text=True, timeout=30
    )


def run_payload(payload):
    return run_hook(json.dumps(payload))


def ask_payload(questions, tool_name="AskUserQuestion"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"questions": questions},
    }


# =================================================================================================
# (a)/(b): the core label rule -- single question
# =================================================================================================

class CoreLabelRule(unittest.TestCase):
    def test_block_when_no_option_label_carries_recommended(self):
        payload = ask_payload([
            question("Pick one", [option("Option A"), option("Option B")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)
        self.assertIn("Pick one", p.stderr)
        self.assertEqual(p.stdout.strip(), "")  # no systemMessage on the block path

    def test_pass_when_first_question_first_option_label_carries_recommended(self):
        payload = ask_payload([
            question("Pick one", [option("Option A (Recommended)"), option("Option B")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")
        self.assertEqual(p.stderr.strip(), "")

    def test_case_insensitive_match(self):
        payload = ask_payload([
            question("Pick one", [option("Option A (RECOMMENDED)"), option("Option B")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_recommended_not_at_string_start_still_counts(self):
        # The rule is a substring check anywhere in the label, not an exact suffix match.
        payload = ask_payload([
            question("Pick one", [option("(Recommended) Option A"), option("Option B")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)


# =================================================================================================
# (c): placement rule -- label vs description
# =================================================================================================

class LabelVsDescriptionPlacement(unittest.TestCase):
    def test_recommended_only_in_description_still_blocks(self):
        """The code reads ONLY opts[0]["label"] -- a "(Recommended)" living solely in the
        option's description text must NOT satisfy the rule."""
        payload = ask_payload([
            question("Pick one", [
                option("Option A", description="This is the (Recommended) choice"),
                option("Option B"),
            ]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)

    def test_recommended_in_both_label_and_description_passes(self):
        payload = ask_payload([
            question("Pick one", [
                option("Option A (Recommended)", description="This is the (Recommended) choice"),
                option("Option B"),
            ]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)


# =================================================================================================
# (d): multi-question inputs
# =================================================================================================

class MultiQuestion(unittest.TestCase):
    def test_all_questions_marked_passes(self):
        payload = ask_payload([
            question("Q1", [option("A1 (Recommended)"), option("B1")]),
            question("Q2", [option("A2 (Recommended)"), option("B2")]),
            question("Q3", [option("A3 (Recommended)"), option("B3")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_one_of_several_questions_missing_recommended_blocks(self):
        payload = ask_payload([
            question("Q1", [option("A1 (Recommended)"), option("B1")]),
            question("Q2", [option("A2"), option("B2")]),  # missing
            question("Q3", [option("A3 (Recommended)"), option("B3")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 2)
        self.assertIn("Q2", p.stderr)
        self.assertNotIn("#1 ", p.stderr)  # Q1 wasn't bad, shouldn't be named
        self.assertIn("#2", p.stderr)      # bad questions are 1-indexed by position

    def test_every_question_missing_recommended_names_all(self):
        payload = ask_payload([
            question("Q1", [option("A1")]),
            question("Q2", [option("A2")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 2)
        self.assertIn("Q1", p.stderr)
        self.assertIn("Q2", p.stderr)


# =================================================================================================
# (e): fail-open paths -- must NEVER block
# =================================================================================================

class FailOpenPaths(unittest.TestCase):
    def test_malformed_json_stdin_fails_open(self):
        # Governance review item: this internal-error path used to be silent (exit 0, empty
        # stdout). It now announces via systemMessage -- exit code is unchanged.
        p = run_hook("not json{")
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)
        self.assertIn("askuserquestion-recommend", out["systemMessage"])

    def test_empty_stdin_fails_open(self):
        # Empty stdin hits the same unparseable-JSON except branch as "not json{" above.
        p = run_hook("")
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)

    def test_non_dict_json_payload_fails_open(self):
        """Valid JSON that isn't an object (a bare list) crashed the hook with AttributeError ->
        exit 1 before the isinstance guard landed (found by this suite's first sweep, 2026-07-11);
        the fail-open contract requires exit 0. Governance review item: this path now announces
        the internal error via systemMessage."""
        p = run_hook("[]")
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)

    def test_missing_tool_input_fails_open(self):
        p = run_payload({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})
        self.assertEqual(p.returncode, 0)

    def test_missing_questions_field_fails_open(self):
        p = run_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {},
        })
        self.assertEqual(p.returncode, 0)

    def test_questions_not_a_list_fails_open(self):
        p = run_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": "not-a-list"},
        })
        self.assertEqual(p.returncode, 0)

    def test_empty_questions_list_fails_open(self):
        p = run_payload(ask_payload([]))
        self.assertEqual(p.returncode, 0)

    def test_different_tool_name_with_unrelated_shape_fails_open(self):
        """The hook doesn't branch on tool_name at all -- a non-AskUserQuestion call (e.g. Bash)
        naturally lacks a "questions" list in its tool_input, so it fails open via the missing-
        questions path, not via any explicit tool-name check."""
        p = run_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        })
        self.assertEqual(p.returncode, 0)

    def test_question_not_a_dict_is_skipped_not_bad(self):
        payload = ask_payload(["not-a-dict", "also-not-a-dict"])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_question_missing_options_is_skipped_not_bad(self):
        payload = ask_payload([{"header": "Q1"}])  # no "options" key at all
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_question_empty_options_is_skipped_not_bad(self):
        payload = ask_payload([question("Q1", [])])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_question_options_not_a_list_is_skipped_not_bad(self):
        payload = ask_payload([{"header": "Q1", "options": "nope"}])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_question_first_option_not_a_dict_is_skipped_not_bad(self):
        payload = ask_payload([{"header": "Q1", "options": ["not-a-dict"]}])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)

    def test_mixed_malformed_and_good_questions_only_evaluates_good_ones(self):
        payload = ask_payload([
            {"header": "Malformed", "options": []},
            question("Good", [option("A (Recommended)"), option("B")]),
        ])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)


# =================================================================================================
# (f): emission contract -- no systemMessage/stdout on either path (this hook never emits one;
# run.sh's fire ledger is a wrapper-level concern out of this hook's own hermetic test surface).
# =================================================================================================

class EmissionContract(unittest.TestCase):
    def test_block_path_stdout_has_no_system_message_json(self):
        payload = ask_payload([question("Pick one", [option("A"), option("B")])])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("systemMessage", p.stdout)
        self.assertIn("BLOCKED", p.stderr)

    def test_pass_path_is_fully_silent(self):
        payload = ask_payload([question("Pick one", [option("A (Recommended)"), option("B")])])
        p = run_payload(payload)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertEqual(p.stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
