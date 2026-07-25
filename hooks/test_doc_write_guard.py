#!/usr/bin/env python
"""Regression tests for doc-write-guard.py -- the two-tier PreToolUse/PostToolUse durable-doc
guard on Write|Edit|MultiEdit.

The hook filename is hyphenated (not importable via a normal statement), so each case invokes
it as a subprocess with the current interpreter, feeds a hook JSON payload on stdin, and asserts
on returncode + stdout/stderr -- matching test_askuserquestion_recommend.py's convention.

HERMETIC (hooks/CLAUDE.md rule): the hook's cache_dir() honors BALLAST_CLAUDE_HOME as a
hermetic-test override. Every test here runs the subprocess with BALLAST_CLAUDE_HOME pointed at
a fresh per-test tmp dir, so gate-attestation and PostToolUse dedupe markers never touch the
real ~/.claude. Tests that care about marker state also pin a distinct session_id.

Decision logic under test (derived from the hook's own code, not from its header prose):
  - PreToolUse: permanent-tier docs (CLAUDE.md/AGENTS.md/PROJECTS.md, skills/**/SKILL.md,
    .claude/**/commands/*.md, hook scripts) BLOCK (exit 2, BLOCKED on stderr) unless a
    gate-<session_id> attestation marker already exists for that session.
  - PostToolUse: ephemeral-tier docs (memory/specs/design segments, README, ideas.md,
    brainstorm*-segment, "handoff" anywhere in the path, any *.md under a /.claude/ dir) SOFT
    NUDGE (stdout JSON with systemMessage + additionalContext) at most once per (session, file).
    Hard-tier paths never nudge on PostToolUse (the gate already owns them).
  - Exclusions apply to BOTH events: .cache/__pycache__/node_modules path segments, and
    .claude/**/plans/*.md (owned by plan-authoring.sh) -- never block, never nudge.
  - R1 fix (2026-07-09 -> 07-11 postmortem chain): SESSION_OUTPUT_SEGMENTS
    ({".notes", "postmortem", "postmortems"}) are excluded FIRST inside is_durable, before the
    "/.claude/*.md" catch-all is even reached -- so a postmortem-dir path under .claude never
    soft-nudges, even though it would otherwise match the catch-all.
  - main() isinstance-dict guard (found by this suite's first sweep, 2026-07-11): valid JSON
    that isn't an object (e.g. a bare list) must fail open (exit 0), not AttributeError/exit 1.
  - USER EXCLUSION LIST (governance review item): <home_root>/ballast/docguard-exclude, one
    fnmatch glob per line, matched case-insensitively against the normalized path. A matching
    line excludes a path from BOTH tiers (no block, no marker written; no nudge either) --
    blank/`#`-comment lines are ignored, and any read/parse error (missing file included)
    fails open to "no exclusions" so the guard stays fully active.

Self-locating + standalone: `python hooks/test_doc_write_guard.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doc-write-guard.py")


def run_hook(payload_text, env):
    return subprocess.run(
        [sys.executable, HOOK], input=payload_text, capture_output=True, text=True, timeout=30, env=env
    )


def run_payload(payload, env):
    return run_hook(json.dumps(payload), env)


def pre_payload(file_path, session_id="s1"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "session_id": session_id,
        "tool_input": {"file_path": file_path},
    }


def post_payload(file_path, session_id="s1"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "session_id": session_id,
        "tool_input": {"file_path": file_path},
    }


class HermeticTestCase(unittest.TestCase):
    """Base class: every test gets its own BALLAST_CLAUDE_HOME tmp dir so gate-attestation and
    PostToolUse dedupe markers can never leak across tests or touch the real ~/.claude."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docguard-test-")
        self.env = dict(os.environ)
        self.env["BALLAST_CLAUDE_HOME"] = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def attest(self, session_id):
        """Drop the gate-<session_id> marker directly -- simulates the durable-docs gate having
        already run this session, without depending on any other hook."""
        cache = os.path.join(self.tmp, ".cache", "docguard")
        os.makedirs(cache, exist_ok=True)
        open(os.path.join(cache, "gate-%s" % session_id), "w").close()

    def write_exclude_bytes(self, data):
        """Write raw bytes to <tmp>/ballast/docguard-exclude -- the user exclusion file the hook
        reads via home_root()/ballast/docguard-exclude. Bytes-level so tests can also exercise
        encoding edge cases (BOM, non-UTF-8 garbage); write_exclude is the text-mode wrapper."""
        d = os.path.join(self.tmp, "ballast")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "docguard-exclude"), "wb") as f:
            f.write(data)

    def write_exclude(self, text):
        """Text-mode convenience wrapper over write_exclude_bytes (single path definition)."""
        self.write_exclude_bytes(text.encode("utf-8"))

    def gate_marker_exists(self, session_id):
        return os.path.exists(os.path.join(self.tmp, ".cache", "docguard", "gate-%s" % session_id))


# =================================================================================================
# PreToolUse HARD gate: blocks permanent-tier docs without an attestation marker.
# =================================================================================================

class HardGateBlocksWithoutAttestation(HermeticTestCase):
    def _assert_blocks(self, file_path):
        p = run_payload(pre_payload(file_path), self.env)
        self.assertEqual(p.returncode, 2, "expected block for %s, got rc=%s stderr=%s" % (file_path, p.returncode, p.stderr))
        self.assertIn("BLOCKED", p.stderr)
        self.assertEqual(p.stdout.strip(), "")

    def test_repo_claude_md_blocks(self):
        self._assert_blocks("C:/proj/CLAUDE.md")

    def test_agents_md_blocks(self):
        self._assert_blocks("C:/proj/AGENTS.md")

    def test_projects_md_blocks(self):
        self._assert_blocks("C:/proj/PROJECTS.md")

    def test_skill_md_under_skills_segment_blocks(self):
        self._assert_blocks("C:/proj/skills/my-skill/SKILL.md")

    def test_claude_commands_md_blocks(self):
        self._assert_blocks("C:/proj/.claude/commands/foo.md")

    def test_hook_py_blocks(self):
        self._assert_blocks("C:/proj/hooks/x.py")

    def test_hook_sh_blocks(self):
        self._assert_blocks("C:/proj/hooks/x.sh")

    def test_hook_ps1_blocks(self):
        self._assert_blocks("C:/proj/hooks/x.ps1")


# =================================================================================================
# PreToolUse HARD gate: attestation marker unblocks, scoped per session_id.
# =================================================================================================

class HardGateAttestationMarker(HermeticTestCase):
    def test_attested_session_allows(self):
        self.attest("s1")
        p = run_payload(pre_payload("C:/proj/CLAUDE.md", session_id="s1"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")
        self.assertEqual(p.stderr.strip(), "")

    def test_attestation_is_scoped_per_session(self):
        # Attesting session s1 must not unblock a write attributed to session s2.
        self.attest("s1")
        p = run_payload(pre_payload("C:/proj/CLAUDE.md", session_id="s2"), self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)


# =================================================================================================
# Exclusions (both events): .cache/__pycache__/node_modules segments, .claude/**/plans/*.md.
# Never block, never nudge -- even for an otherwise-hard or otherwise-durable basename.
# =================================================================================================

class Exclusions(HermeticTestCase):
    def test_cache_segment_excludes_from_hard_gate(self):
        p = run_payload(pre_payload("C:/proj/.cache/CLAUDE.md"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr.strip(), "")

    def test_pycache_segment_excludes_from_hard_gate(self):
        p = run_payload(pre_payload("C:/proj/__pycache__/CLAUDE.md"), self.env)
        self.assertEqual(p.returncode, 0)

    def test_node_modules_segment_excludes_from_hard_gate(self):
        p = run_payload(pre_payload("C:/proj/node_modules/pkg/CLAUDE.md"), self.env)
        self.assertEqual(p.returncode, 0)

    def test_cache_segment_excludes_from_soft_nudge(self):
        p = run_payload(post_payload("C:/proj/.cache/specs/plan.md"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_claude_plans_excluded_from_hard_gate(self):
        # plans/ segment appearing AFTER .claude belongs to plan-authoring.sh -- never blocks,
        # even though the basename would otherwise land in the .claude/commands hard-tier shape.
        p = run_payload(pre_payload("C:/proj/.claude/plans/plan1.md"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr.strip(), "")

    def test_claude_plans_excluded_from_soft_nudge(self):
        # Without the plans exclusion this would match the "/.claude/*.md" catch-all durable rule.
        p = run_payload(post_payload("C:/proj/.claude/plans/plan1.md"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


# =================================================================================================
# USER EXCLUSION LIST (governance review item): <home_root>/ballast/docguard-exclude -- one
# fnmatch glob per line, matched case-insensitively against the normalized path. A matching line
# excludes from BOTH tiers (hard gate: allow, no marker written; soft nudge: silent). Any
# read/parse error -- missing file included -- fails open to "no exclusions" (guard stays active).
# =================================================================================================

class UserExclusionList(HermeticTestCase):
    def test_matching_glob_allows_hard_tier_without_marker(self):
        self.write_exclude("*/governance/claude.md\n")
        p = run_payload(pre_payload("C:/proj/governance/CLAUDE.md", session_id="excl-1"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr.strip(), "")
        # The exclusion bypasses the gate entirely -- it must not fabricate an attestation marker
        # as a side effect (that would silently unblock every OTHER hard-tier write this session).
        self.assertFalse(self.gate_marker_exists("excl-1"))

    def test_non_matching_glob_still_blocks(self):
        self.write_exclude("*/other/claude.md\n")
        p = run_payload(pre_payload("C:/proj/governance/CLAUDE.md", session_id="excl-2"), self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)

    def test_blank_and_comment_lines_ignored_commented_pattern_does_not_exclude(self):
        # A blank line and a #-commented-out copy of the SAME pattern that would otherwise match
        # must both be no-ops -- the write still blocks.
        self.write_exclude("\n# */governance/claude.md\n\n")
        p = run_payload(pre_payload("C:/proj/governance/CLAUDE.md", session_id="excl-3"), self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)

    def test_matching_glob_silences_soft_nudge(self):
        self.write_exclude("*/specs/plan.md\n")
        p = run_payload(post_payload("C:/proj/specs/plan.md", session_id="excl-4"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_utf8_bom_on_first_pattern_still_matches(self):
        # Notepad's default UTF-8 flavor prepends a BOM; utf-8-sig decoding must strip it so the
        # first pattern line still matches (a stray U+FEFF would make it silently never match).
        self.write_exclude_bytes(b"\xef\xbb\xbf*/governance/claude.md\n")
        p = run_payload(pre_payload("C:/proj/governance/CLAUDE.md", session_id="excl-6"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr.strip(), "")
        self.assertFalse(self.gate_marker_exists("excl-6"))

    def test_backslash_style_pattern_matches(self):
        # A Windows-pasted backslash pattern (Explorer/PowerShell copy) must match the hook's
        # forward-slash-normalized path -- patterns are slash-normalized at load, not left to
        # fnmatch's platform-dependent behavior.
        self.write_exclude("C:\\proj\\governance\\*.md\n")
        p = run_payload(pre_payload("C:/proj/governance/CLAUDE.md", session_id="excl-7"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stderr.strip(), "")
        self.assertFalse(self.gate_marker_exists("excl-7"))

    def test_malformed_binary_content_fails_open_still_blocks(self):
        # Invalid UTF-8 bytes -> the read raises inside load_exclude_patterns()'s try/except ->
        # treated as "no exclusions", not as a crash and not as a false exclusion.
        self.write_exclude_bytes(b"\xff\xfe\x00\xd8\xff\xff not valid utf-8 \x80\x81")
        p = run_payload(pre_payload("C:/proj/CLAUDE.md", session_id="excl-5"), self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)
        self.assertFalse(self.gate_marker_exists("excl-5"))


# =================================================================================================
# PostToolUse SOFT nudge: fires for the ephemeral (soft) durable-doc tier.
# =================================================================================================

class SoftNudgeFires(HermeticTestCase):
    def _assert_nudges(self, file_path, session_id="s1"):
        p = run_payload(post_payload(file_path, session_id=session_id), self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertIn("systemMessage", out)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        return out["hookSpecificOutput"]["additionalContext"]

    def test_specs_segment_md_nudges(self):
        self._assert_nudges("C:/proj/specs/plan.md")

    def test_design_segment_md_nudges(self):
        self._assert_nudges("C:/proj/design/notes.md")

    def test_memory_segment_md_nudges(self):
        self._assert_nudges("C:/proj/memory/notes.md")

    def test_digest_series_name_without_dotclaude_still_nudges(self):
        # CLAUDE_OUTPUT_SEGMENTS is PAIR-matched with ".claude" on purpose: a digest dir is named
        # after the skill that writes it, and that same name is also the shipped skill dir. Bare-
        # segment matching would silence durable authoring surfaces that merely share the name.
        self._assert_nudges("C:/proj/harness-sweep/memory/notes.md")

    def test_readme_nudges(self):
        self._assert_nudges("C:/proj/README.md")

    def test_ideas_md_nudges(self):
        self._assert_nudges("C:/proj/ideas.md")

    def test_brainstorm_segment_nudges(self):
        self._assert_nudges("C:/proj/brainstorming/notes.md")

    def test_handoff_in_path_nudges(self):
        self._assert_nudges("C:/proj/notes/handoff-2026.md")

    def test_random_md_under_dotclaude_nudges(self):
        self._assert_nudges("C:/proj/.claude/random-notes.md")


# =================================================================================================
# SKILL_REMINDER vs REMINDER selection: a skill.md that reaches the SOFT path (i.e. is NOT under
# a skills/ segment, so is_hard doesn't claim it first) gets the skill-forge wording; every other
# soft-tier basename gets the generic durable-docs wording.
# =================================================================================================

class SkillReminderSelection(HermeticTestCase):
    def test_skill_md_not_under_skills_segment_gets_skill_reminder(self):
        p = run_payload(post_payload("C:/proj/notes/SKILL.md"), self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("skill-forge", ctx)

    def test_non_skill_soft_tier_path_gets_generic_reminder(self):
        p = run_payload(post_payload("C:/proj/specs/plan.md"), self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("durable-docs", ctx)
        self.assertNotIn("skill-forge", ctx)


# =================================================================================================
# R1 REGRESSION (postmortem chain 2026-07-09 -> 07-11): session-output areas must stay SILENT on
# PostToolUse. SESSION_OUTPUT_SEGMENTS is excluded FIRST inside is_durable -- case (c) specifically
# proves the "/.claude/*.md" catch-all does not re-catch a postmortem-segment path underneath it.
# =================================================================================================

class R1SessionOutputRegression(HermeticTestCase):
    def _assert_silent(self, file_path):
        p = run_payload(post_payload(file_path), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "", "expected silence for %s, got %r" % (file_path, p.stdout))

    def test_notes_dir_report_silent(self):
        self._assert_silent("C:/proj/.notes/2026-01-01-anything.md")

    def test_claude_postmortem_report_silent(self):
        self._assert_silent("C:/proj/.claude/postmortem/2026-01-01-report.md")

    def test_home_claude_postmortem_sweep_state_silent(self):
        # Proves the /.claude/*.md catch-all does NOT re-catch a postmortem-segment path -- this
        # path would match that catch-all rule if SESSION_OUTPUT_SEGMENTS weren't checked first.
        self._assert_silent("C:/Users/tester/.claude/postmortem/SWEEP-STATE.md")

    def test_postmortems_plural_segment_silent(self):
        self._assert_silent("C:/proj/.claude/postmortems/digest.md")

    def test_notes_subfolder_stays_silent(self):
        # Segment matching (not a prefix/depth test) is what makes .notes/ safe to reorganize into
        # subfolders -- an archived note nested one level down must stay exempt.
        self._assert_silent("C:/proj/.notes/archive/2026-01-01-closed-plan.md")

    def test_claude_skill_digest_series_silent(self):
        # CLAUDE_OUTPUT_SEGMENTS: a recurring skill's digest series homed beside .claude/postmortem/.
        # Same catch-all proof as SWEEP-STATE above -- these would match "/.claude/*.md" otherwise.
        self._assert_silent("C:/proj/.claude/harness-sweep/2026-01-01-harness-sweep-deep.md")
        self._assert_silent("C:/proj/.claude/changelog-digests/2026-01-01-v1.0-v1.1.md")


# =================================================================================================
# Hard-tier paths stay silent on PostToolUse (the gate already owns them -- no double nudge), and
# PostToolUse dedupe is keyed by (session_id, normalized file path).
# =================================================================================================

class HardTierSilentOnPostAndDedupe(HermeticTestCase):
    def test_hard_tier_path_silent_on_post_tooluse(self):
        p = run_payload(post_payload("C:/proj/CLAUDE.md"), self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_dedupe_second_post_for_same_session_and_file_is_silent(self):
        first = run_payload(post_payload("C:/proj/specs/plan.md", session_id="dupe-1"), self.env)
        self.assertNotEqual(first.stdout.strip(), "")
        second = run_payload(post_payload("C:/proj/specs/plan.md", session_id="dupe-1"), self.env)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout.strip(), "")

    def test_dedupe_different_file_same_session_still_fires(self):
        first = run_payload(post_payload("C:/proj/specs/plan.md", session_id="dupe-2"), self.env)
        self.assertNotEqual(first.stdout.strip(), "")
        other = run_payload(post_payload("C:/proj/specs/other.md", session_id="dupe-2"), self.env)
        self.assertNotEqual(other.stdout.strip(), "")

    def test_dedupe_different_session_same_file_still_fires(self):
        first = run_payload(post_payload("C:/proj/specs/plan.md", session_id="dupe-3a"), self.env)
        self.assertNotEqual(first.stdout.strip(), "")
        other_session = run_payload(post_payload("C:/proj/specs/plan.md", session_id="dupe-3b"), self.env)
        self.assertNotEqual(other_session.stdout.strip(), "")


# =================================================================================================
# Windows path handling: a backslash-form file_path normalizes the same as forward-slash.
# =================================================================================================

class WindowsPathHandling(HermeticTestCase):
    def test_backslash_path_hard_gate_blocks(self):
        p = run_payload(pre_payload("C:\\proj\\CLAUDE.md"), self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BLOCKED", p.stderr)

    def test_backslash_path_soft_nudge_fires(self):
        p = run_payload(post_payload("C:\\proj\\specs\\x.md"), self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertIn("systemMessage", out)


# =================================================================================================
# Fail-open paths -- must NEVER block, and PostToolUse must never emit on any of these.
# =================================================================================================

class FailOpenPaths(HermeticTestCase):
    def test_malformed_json_fails_open(self):
        # Governance review item: this fail-open path used to be silent (exit 0, empty stdout).
        # It now announces the internal error via systemMessage -- exit code is unchanged.
        p = run_hook("not json{", self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)

    def test_empty_stdin_fails_open(self):
        # Empty stdin hits the SAME malformed-JSON except branch as "not json{" above (json.loads
        # on "" raises) -- so this also announces, matching the sibling test above.
        p = run_hook("", self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)

    def test_list_json_payload_fails_open(self):
        """Valid JSON that isn't an object (a bare list) crashed the hook with AttributeError ->
        exit 1 before the isinstance-dict guard landed; the fail-open contract requires exit 0.
        Governance review item: this path now announces the internal error via systemMessage."""
        p = run_hook("[]", self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)

    def test_missing_file_path_pre_fails_open(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "s1", "tool_input": {}}
        p = run_payload(payload, self.env)
        self.assertEqual(p.returncode, 0)

    def test_missing_file_path_post_fails_open_silently(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": "s1", "tool_input": {}}
        p = run_payload(payload, self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_unknown_hook_event_name_fails_open(self):
        payload = pre_payload("C:/proj/CLAUDE.md")
        payload["hook_event_name"] = "SomeOtherEvent"
        p = run_payload(payload, self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


# =================================================================================================
# ANNOUNCE-ON-ERROR (governance review item): every internal-error fail-open path -- pre_gate's
# except, main()'s malformed/non-dict payload branches (covered above), and post_nudge's new
# try/except wrapper -- makes a best-effort systemMessage announcement before returning/exiting.
# Exit codes are UNCHANGED (still 0 / still fail-open); only the silence is what changed.
# =================================================================================================

class AnnounceOnInternalError(HermeticTestCase):
    def test_pre_gate_exception_announces_and_still_allows(self):
        # tool_input.file_path is an int, not a string -- normalize()'s path.replace() raises
        # AttributeError inside pre_gate's try block, exercising its except-path announce.
        payload = {
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "session_id": "s1", "tool_input": {"file_path": 12345},
        }
        p = run_payload(payload, self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)
        self.assertIn("doc-write-guard", out["systemMessage"])

    def test_post_nudge_exception_announces_and_stays_fail_open(self):
        # Same non-string file_path trigger, routed through PostToolUse instead -- exercises
        # main()'s new try/except around the post_nudge(d) call (previously an uncaught bug here
        # exited 1 with a traceback; must now be exit 0 + an announce).
        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "Write",
            "session_id": "s1", "tool_input": {"file_path": 12345},
        }
        p = run_payload(payload, self.env)
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.strip())
        self.assertIn("systemMessage", out)
        self.assertIn("doc-write-guard", out["systemMessage"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
