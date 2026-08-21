#!/usr/bin/env python
"""Hermetic tests for await_leaves.py — synthetic session uuids / leaf ids only
(the repo scrub rejects personal paths), a temp dir as BALLAST_CLAUDE_HOME, and
--timeout 0 so the poll loop makes a single pass and never sleeps."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "await_leaves.py")
BARE = object()
SESSION = "11111111-1111-1111-1111-111111111111"


def assistant(text, stop_reason=None, error=False):
    msg = {"content": [{"type": "text", "text": text}]}
    if stop_reason:
        msg["stop_reason"] = stop_reason
    rec = {"type": "assistant", "message": msg, "timestamp": "2026-01-01T00:00:00.000Z"}
    if error:
        rec["isApiErrorMessage"] = True
    return rec


def tool_use():
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}}


class AwaitLeavesTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def leaf(self, leaf_id, records, meta=None, proj="proj-a",
             session=SESSION, subdir=None, partial=None):
        d = os.path.join(self.home, "projects", proj, session, "subagents")
        if subdir:
            d = os.path.join(d, subdir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "agent-%s.jsonl" % leaf_id), "w",
                  encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
            if partial is not None:
                fh.write(partial)
        if meta is not None:
            with open(os.path.join(d, "agent-%s.meta.json" % leaf_id), "w",
                      encoding="utf-8") as fh:
                json.dump(meta, fh)

    def run_await(self, ids, session=SESSION, timeout="0"):
        # session=None omits the flag; session=BARE passes `--session` with no value.
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        flag = [] if session is None else ["--session"] + ([] if session is BARE else [session])
        out = subprocess.run(
            [sys.executable, SCRIPT] + flag + ["--ids", ids, "--timeout", timeout],
            capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_sentinel_done(self):
        self.leaf("a1", [assistant("candidates found\nLEAF-DONE")],
                  meta={"agentType": "general-purpose", "description": "finder:a"})
        out = self.run_await("a1")
        self.assertIn("## finder:a (a1)", out)
        self.assertIn("candidates found", out)
        self.assertIn("PENDING: none", out)

    def test_end_turn_without_sentinel(self):
        self.leaf("b2", [assistant("done reviewing", stop_reason="end_turn")])
        out = self.run_await("b2")
        self.assertIn("## b2 (b2)", out)
        self.assertIn("done reviewing", out)
        self.assertIn("PENDING: none", out)

    def test_api_error_tagged(self):
        self.leaf("c3", [assistant("upstream 529", error=True)])
        out = self.run_await("c3")
        self.assertIn("## c3 (c3) (error)", out)
        self.assertIn("PENDING: none", out)

    def test_partial_then_done(self):
        # A complete non-done record plus a half-written trailing line -> pending.
        self.leaf("d4", [tool_use()], partial='{"type":"assist')
        out = self.run_await("d4")
        self.assertNotIn("## d4", out)
        self.assertIn("PENDING: d4", out)
        # The line completes into a done record -> done.
        self.leaf("d4", [tool_use(), assistant("verdict", stop_reason="end_turn")])
        out = self.run_await("d4")
        self.assertIn("## d4 (d4)", out)
        self.assertIn("PENDING: none", out)

    def test_missing_file_pending_at_deadline(self):
        out = self.run_await("e5")
        self.assertIn("PENDING: e5", out)

    def test_wide_glob_fallback(self):
        # Sidecar under a DIFFERENT session uuid and nested workflow dir; the
        # narrow glob (searched under SESSION) misses, the wide glob finds it.
        self.leaf("f6", [assistant("found it\nLEAF-DONE")],
                  session="99999999-9999-9999-9999-999999999999",
                  subdir=os.path.join("workflows", "wf_x"))
        out = self.run_await("f6")
        self.assertIn("## f6 (f6)", out)
        self.assertIn("found it", out)
        self.assertIn("PENDING: none", out)

    def test_missing_session_resolves_via_wide_glob(self):
        # An empty, bare (`--session --ids ...`, the unsubstituted-${CLAUDE_SESSION_ID}
        # shape), or omitted --session skips the narrow glob; a sidecar under a
        # different session uuid still resolves via the wide glob.
        self.leaf("k0", [assistant("found blind\nLEAF-DONE")],
                  session="99999999-9999-9999-9999-999999999999")
        for session in ("", BARE, None):
            out = self.run_await("k0", session=session)
            self.assertIn("## k0 (k0)", out)
            self.assertIn("found blind", out)
            self.assertIn("PENDING: none", out)

    def test_header_fallback_without_meta(self):
        # No meta -> id label.
        self.leaf("g7", [assistant("x", stop_reason="end_turn")])
        self.assertIn("## g7 (g7)", self.run_await("g7"))
        # Partial meta (agentType only) -> agentType label.
        self.leaf("h8", [assistant("y", stop_reason="end_turn")],
                  meta={"agentType": "diff-finder", "spawnDepth": 2})
        self.assertIn("## diff-finder (h8)", self.run_await("h8"))

    def test_non_ascii_round_trip(self):
        text = "café ✓ 日本語 finding\nLEAF-DONE"
        self.leaf("i9", [assistant(text)], meta={"description": "finder:é"})
        out = self.run_await("i9")
        self.assertIn("café ✓ 日本語 finding", out)
        self.assertIn("## finder:é (i9)", out)

    def test_error_empty_content_done(self):
        # An error record is done even with no content blocks -> done + (error).
        rec = {"type": "assistant", "message": {"content": []}, "isApiErrorMessage": True}
        self.leaf("m1", [rec])
        out = self.run_await("m1")
        self.assertIn("## m1 (m1) (error)", out)
        self.assertIn("PENDING: none", out)

    def test_trailing_system_record_done(self):
        # A done assistant record followed by a trailing system line -> done.
        self.leaf("n2", [assistant("verdict", stop_reason="end_turn"),
                         {"type": "system", "subtype": "turn_duration"}])
        out = self.run_await("n2")
        self.assertIn("## n2 (n2)", out)
        self.assertIn("verdict", out)
        self.assertIn("PENDING: none", out)

    def test_symlink_outside_root_not_resolved(self):
        # A sidecar symlinked outside the projects tree must not resolve (containment).
        outside = tempfile.mkdtemp()
        target = os.path.join(outside, "external.jsonl")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(assistant("leaked", stop_reason="end_turn")) + "\n")
        d = os.path.join(self.home, "projects", "proj-a", SESSION, "subagents")
        os.makedirs(d, exist_ok=True)
        try:
            os.symlink(target, os.path.join(d, "agent-p3.jsonl"))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks unavailable on this platform")
        out = self.run_await("p3")
        self.assertIn("PENDING: p3", out)
        self.assertNotIn("leaked", out)

    def test_large_file_tail_read(self):
        # >64KiB sidecar whose final record is done -> done via the tail read.
        big = tool_use()
        big["message"]["content"][0]["input"] = {"pad": "z" * 70000}
        self.leaf("q4", [big, assistant("final verdict", stop_reason="end_turn")])
        out = self.run_await("q4")
        self.assertIn("## q4 (q4)", out)
        self.assertIn("final verdict", out)
        self.assertIn("PENDING: none", out)


if __name__ == "__main__":
    unittest.main()
