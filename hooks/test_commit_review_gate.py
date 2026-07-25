#!/usr/bin/env python3
"""Regression tests for commit-review-gate.py -- the SHADOW-mode review-gated-commit hook.

Implements the design spec's "Test matrix" section verbatim (see
`2026-06-29-review-gated-autonomous-commit.md`), re-targeted to this hook. Every scenario is
driven end-to-end: a real temp git repo (so staged-diff / numstat / -w / MERGE_HEAD behavior is
the REAL git binary's, not a mock) plus a synthetic transcript .jsonl fixture, invoked through the
hook via subprocess (matching test_ballast_allow.py's convention -- the hyphenated filename can't
be `import`ed directly anyway).

Hermetic by construction: every test redirects the hook's `~/.claude`-rooted state (kill-switch,
shadow log, optional config, compact-backups scan root) into an isolated temp dir via the
BALLAST_CLAUDE_HOME env var (see commit-review-gate.py's `_claude_home()`) -- nothing here ever
touches the developer's real ~/.claude/commit-gate-shadow.log or compact-backups/.

Runs with plain stdlib unittest (no pytest dependency), so dev/check.sh can invoke it with the
same resolved interpreter it already uses for the extractor and ballast-allow suites.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commit-review-gate.py")

# Fixed base instant + integer-second offsets -> deterministic, monotonically increasing ISO8601
# timestamps that string-sort exactly like real transcript timestamps (the hook compares them as
# plain strings, mirroring extract.py's own convention).
_BASE = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def ts(n):
    return (_BASE + timedelta(seconds=n)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------------------------------------------------------------------------------------------
# Transcript-line builders -- just enough of Claude Code's real JSONL shape for the hook's own
# _scan_source walk (assistant tool_use blocks, paired user tool_result blocks, plain user turns).
# --------------------------------------------------------------------------------------------

def genesis(n=0, text="start the task"):
    """A plain user turn used as every fixture's first line -- gives COMMIT_BOUNDARY's
    session-start fallback a timestamp STRICTLY BEFORE the first real edit/review, so ts(10)+
    events are correctly `> boundary` rather than accidentally equal to it."""
    return user_message(n, text)


def assistant_tool_use(n, name, tool_input, tool_id=None):
    return {
        "type": "assistant",
        "timestamp": ts(n),
        "message": {
            "id": "msg_%d" % n,
            "content": [{"type": "tool_use", "id": tool_id or ("toolu_%d" % n),
                         "name": name, "input": tool_input}],
        },
    }


def edit_event(n, file_path):
    return assistant_tool_use(n, "Edit", {"file_path": file_path})


def skill_event(n, skill_name):
    return assistant_tool_use(n, "Skill", {"skill": skill_name})


def bash_event(n, command, tool_id=None):
    return assistant_tool_use(n, "Bash", {"command": command}, tool_id=tool_id)


def tool_result(n, tool_use_id, content_text="ok", is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content_text}
    if is_error:
        block["is_error"] = True
    return {
        "type": "user",
        "timestamp": ts(n),
        "message": {"content": [block]},
    }


def user_message(n, text):
    return {"type": "user", "timestamp": ts(n), "message": {"content": text}}


def write_transcript(path, lines):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


# --------------------------------------------------------------------------------------------
# git repo helpers -- a REAL temp repo per test so staged-diff/numstat/-w/MERGE_HEAD are exercised
# against the actual git binary, not reimplemented logic.
# --------------------------------------------------------------------------------------------

def run_git(args, cwd, check=True):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, check=check)


def init_repo(repo_dir):
    run_git(["init", "-q"], repo_dir)
    run_git(["config", "user.email", "test@example.com"], repo_dir)
    run_git(["config", "user.name", "Test"], repo_dir)
    run_git(["config", "commit.gpgsign", "false"], repo_dir)
    # Deliberately pinned regardless of the host's global gitconfig -- the whitespace/CRLF test
    # needs raw CRLF bytes to actually land in the index, which autocrlf=true would silently
    # normalize away on `git add` (verified empirically: with the host's real autocrlf=true, a
    # CRLF-only rewrite produces an EMPTY numstat, not a whitespace-only one).
    run_git(["config", "core.autocrlf", "false"], repo_dir)
    (Path(repo_dir) / "README.md").write_text("init\n", encoding="utf-8", newline="")
    run_git(["add", "README.md"], repo_dir)
    run_git(["commit", "-q", "-m", "init"], repo_dir)


def write_and_stage(repo_dir, relpath, content):
    """Write `content` with EXACT bytes (newline="" disables Python's universal-newline
    translation, so a literal '\\r\\n' in `content` survives to disk unmodified) and `git add` it."""
    p = Path(repo_dir) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    run_git(["add", relpath], repo_dir)
    return str(p)


def commit_now(repo_dir, message):
    run_git(["commit", "-q", "-m", message], repo_dir)


# --------------------------------------------------------------------------------------------
# Hook invocation
# --------------------------------------------------------------------------------------------

def run_hook(payload, claude_home):
    env = os.environ.copy()
    env["BALLAST_CLAUDE_HOME"] = str(claude_home)
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                        capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout, p.stderr


def log_lines(claude_home):
    log = Path(claude_home) / "commit-gate-shadow.log"
    if not log.is_file():
        return []
    return [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_hook_module():
    """Import commit-review-gate.py as an in-process module (F3's _log unit test needs to call
    `_log` directly -- the hyphenated filename can't be `import`ed via a normal statement, hence
    importlib.util's spec-from-path loading instead of the subprocess route every other test uses).
    """
    spec = importlib.util.spec_from_file_location("commit_review_gate_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GateTestCase(unittest.TestCase):
    """Common fixture: an isolated git repo + an isolated BALLAST_CLAUDE_HOME per test."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="cgate-repo-")
        self.home = tempfile.mkdtemp(prefix="cgate-home-")
        self.session_id = "test-" + uuid.uuid4().hex
        self.transcript = os.path.join(self.home, "live", "%s.jsonl" % self.session_id)
        init_repo(self.repo)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def payload(self, command='git commit -m "wip"', transcript_path=None):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": self.session_id,
            "transcript_path": self.transcript if transcript_path is None else transcript_path,
            "cwd": self.repo,
        }

    def run_hook(self, **kwargs):
        return run_hook(self.payload(**kwargs), self.home)

    def last_log(self):
        lines = log_lines(self.home)
        return lines[-1] if lines else None


# =================================================================================================
# Kill switch
# =================================================================================================

class KillSwitch(GateTestCase):
    def test_kill_switch_skips_evaluation_entirely(self):
        # Non-trivial staged change with zero review -- would WOULD-BLOCK if evaluated.
        write_and_stage(self.repo, "app.py", "x = 1\ny = 2\nz = 3\nw = 4\nv = 5\nu = 6\nt = 7\n")
        (Path(self.home) / ".commit-gate-off").touch()
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertEqual(log_lines(self.home), [], "kill switch must write NO log line")


# =================================================================================================
# Standalone-commit parsing / non-qualifying commands
# =================================================================================================

class NotAQualifyingCommit(GateTestCase):
    def test_non_git_command_no_evaluation(self):
        rc, out, err = self.run_hook(command="ls -la", transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertEqual(log_lines(self.home), [])

    def test_compound_add_and_commit_not_standalone_still_blocked_upstream(self):
        """Spec bullet: 'compound add && commit -> still blocked upstream.' git-commit-guard.sh
        (registered before this hook) hard-blocks this compound outright; this gate independently
        treats a compound (sequencer anywhere) as non-qualifying -- no evaluation, no log line."""
        rc, out, err = self.run_hook(command='git add -A && git commit -m "x"',
                                      transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertEqual(log_lines(self.home), [])

    def test_dry_run_not_a_qualifying_commit(self):
        rc, out, err = self.run_hook(command="git commit --dry-run",
                                      transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertEqual(log_lines(self.home), [])


# =================================================================================================
# Steps 2-3: staged set / merge-cherry-pick
# =================================================================================================

class EmptyAndMergeState(GateTestCase):
    def test_empty_staged_diff_allow(self):
        """Spec bullet: 'merge/empty staged -> ALLOW' (empty half)."""
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())
        self.assertIn("empty-staged-diff", self.last_log())
        # ALLOW is the common case (every reviewed/trivial commit) -- main() only prints the
        # systemMessage fire indicator on WOULD-BLOCK, so stdout must stay silent here.
        self.assertNotIn('"systemMessage"', out)

    def test_merge_head_present_allow(self):
        """Spec bullet: 'merge/empty staged -> ALLOW' (merge half). Real code staged (would
        WOULD-BLOCK on its own) but MERGE_HEAD short-circuits to ALLOW before O1 ever runs --
        transcript_path is bogus on purpose to prove O1 is never reached."""
        write_and_stage(self.repo, "app.py",
                         "x = 1\ny = 2\nz = 3\nw = 4\nv = 5\nu = 6\nt = 7\ns = 8\n")
        (Path(self.repo) / ".git" / "MERGE_HEAD").write_text("deadbeef\n", encoding="utf-8")
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())
        self.assertIn("merge-or-cherry-pick", self.last_log())


# =================================================================================================
# Step 4: O2 trivial (whitespace, and churn+comment-only)
# =================================================================================================

class O2Trivial(GateTestCase):
    def test_whitespace_crlf_only_allow(self):
        """Spec bullet: 'whitespace/CRLF-only -> allow (autocrlf arm)'. Raw bytes DO differ (every
        line gets a trailing \\r) but `git diff --cached -w` reports none -- must ALLOW without
        ever consulting the (bogus) transcript."""
        write_and_stage(self.repo, "app.py", "line1\nline2\nline3\n")
        commit_now(self.repo, "add app.py")
        write_and_stage(self.repo, "app.py", "line1\r\nline2\r\nline3\r\n")
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())
        self.assertIn("trivial-whitespace-only", self.last_log())

    def test_trivial_comment_only_allow(self):
        """Spec bullet: 'trivial-comment -> allow'. A single added comment line, churn=1."""
        write_and_stage(self.repo, "app.py", "def add(a, b):\n    return a + b\n")
        commit_now(self.repo, "add app.py")
        write_and_stage(self.repo, "app.py",
                         "def add(a, b):\n    # TODO: check overflow\n    return a + b\n")
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())
        self.assertIn("trivial-churn", self.last_log())

    def test_one_line_real_logic_change_no_review_would_block(self):
        """Spec bullet: 'one-line REAL logic change, no review -> BLOCK (content scan sees a
        non-comment line; count alone must not pass it).'"""
        write_and_stage(self.repo, "app.py", "def add(a, b):\n    return a + b\n")
        commit_now(self.repo, "add app.py")
        write_and_stage(self.repo, "app.py", "def add(a, b):\n    return a - b\n")
        write_transcript(self.transcript, [genesis()])  # no Skill launch anywhere
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_50_line_no_review_would_block(self):
        """Spec bullet: '50-line code, no review -> BLOCK (named skill+paths).'"""
        lines = "\n".join("v%d = %d" % (i, i) for i in range(50)) + "\n"
        write_and_stage(self.repo, "app.py", lines)
        write_transcript(self.transcript, [genesis()])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("WOULD-BLOCK", last)
        self.assertIn("code-review", last)  # names the accepted skill(s)
        self.assertIn("app.py", last)       # names the uncovered path
        # systemMessage fire indicator: WOULD-BLOCK is the rare, worth-surfacing signal, so main()
        # prints a systemMessage JSON line on stdout even though the hook still shadow-exits 0.
        self.assertIn('"systemMessage"', out)

    def test_c_file_star_prefix_line_is_not_trivial_would_block(self):
        """F1 regression: a bare '*'-leading line in a .c file must NOT classify as comment-or-
        blank. `*fnptr = &backdoor;` is a pointer dereference, not a comment-continuation line --
        churn=1 (a single added line, well under the <=6 threshold) would have been wrongly
        trivial-passed by the old bare '*' comment prefix. Without a review, this must WOULD-BLOCK."""
        write_and_stage(self.repo, "app.c", "int main(void) {\n    return 0;\n}\n")
        commit_now(self.repo, "add app.c")
        write_and_stage(self.repo, "app.c",
                         "int main(void) {\n    *fnptr = &backdoor;\n    return 0;\n}\n")
        write_transcript(self.transcript, [genesis()])  # no review anywhere
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_six_added_comment_lines_at_churn_boundary_allow(self):
        """F6(b): churn boundary, trivial side. Exactly 6 added pure-comment lines (churn == 6,
        the O2 threshold's inclusive edge) must ALLOW without any review."""
        write_and_stage(self.repo, "app.py", "def add(a, b):\n    return a + b\n")
        commit_now(self.repo, "add app.py")
        write_and_stage(
            self.repo, "app.py",
            "# c1\n# c2\n# c3\n# c4\n# c5\n# c6\ndef add(a, b):\n    return a + b\n")
        rc, out, err = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("trivial-churn=6", last)

    def test_seven_added_comment_lines_past_churn_boundary_would_block(self):
        """F6(b): churn boundary, non-trivial side. One more comment line (churn == 7) falls past
        O2's threshold and, absent a review, must WOULD-BLOCK."""
        write_and_stage(self.repo, "app.py", "def add(a, b):\n    return a + b\n")
        commit_now(self.repo, "add app.py")
        write_and_stage(
            self.repo, "app.py",
            "# c1\n# c2\n# c3\n# c4\n# c5\n# c6\n# c7\ndef add(a, b):\n    return a + b\n")
        write_transcript(self.transcript, [genesis()])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())


# =================================================================================================
# Step 2 (F2): -a/-am must see the UNION of staged + unstaged tracked changes
# =================================================================================================

class AutoStageUnion(GateTestCase):
    def test_dash_am_sees_unstaged_tracked_change_would_block(self):
        """F2 regression: `git commit -am` stages the UNION of what's already indexed plus
        unstaged tracked changes -- not one or the other. A staged 1-line comment change in file A
        alone would be trivial, but an unstaged 10-line real logic change in tracked file B is
        also about to be committed (that's what -a does). The old rule ('only fall back to
        `git diff HEAD` when `--cached` is EMPTY') missed this because A's staged hunk already made
        `--cached` non-empty, so B's unstaged half never got looked at. Must WOULD-BLOCK, naming B."""
        write_and_stage(self.repo, "a.py", "def a():\n    return 1\n")
        write_and_stage(self.repo, "b.py", "def b():\n    return 1\n")
        commit_now(self.repo, "add a and b")

        # Stage a trivial 1-line comment-only change to A.
        write_and_stage(self.repo, "a.py", "def a():\n    # trivial comment\n    return 1\n")

        # Leave a real, UNSTAGED 10-line logic change to tracked file B (no `git add` for b.py).
        b_lines = "\n".join("def b%d():\n    return %d" % (i, i) for i in range(10)) + "\n"
        (Path(self.repo) / "b.py").write_text(b_lines, encoding="utf-8", newline="")

        write_transcript(self.transcript, [genesis()])  # no review anywhere
        rc, out, err = self.run_hook(command='git commit -am "x"')
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("WOULD-BLOCK", last)
        self.assertIn("b.py", last)  # B's unstaged change must be visible to the gate


# =================================================================================================
# Step 5: O3 docs/meta routing (docs = durable-docs; hooks/config forced to CODE)
# =================================================================================================

class O3Routing(GateTestCase):
    def test_docs_with_durable_docs_review_allow(self):
        edit_path = write_and_stage(
            self.repo, "notes/design.md",
            "# Design\n\nSome longer prose describing the change in enough detail that it is not "
            "whitespace-only and not a tiny comment-only diff by any measure at all.\n"
        )
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            skill_event(20, "durable-docs"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_docs_no_review_would_block(self):
        edit_path = write_and_stage(
            self.repo, "notes/design.md",
            "# Design\n\nSome longer prose describing the change in enough detail that it is not "
            "whitespace-only and not a tiny comment-only diff by any measure at all.\n"
        )
        write_transcript(self.transcript, [genesis(), edit_event(10, edit_path)])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_sh_file_only_durable_docs_would_block(self):
        """Spec bullet: '*.sh/settings.json with ONLY durable-docs -> BLOCK (config is code).'"""
        edit_path = write_and_stage(
            self.repo, "hooks/foo.sh",
            "#!/bin/bash\n" + "\n".join('echo "line %d"' % i for i in range(20)) + "\n"
        )
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "durable-docs"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_settings_json_only_durable_docs_would_block(self):
        edit_path = write_and_stage(
            self.repo, "settings.json",
            json.dumps({"k%d" % i: i for i in range(20)}, indent=2) + "\n"
        )
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "durable-docs"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())


# =================================================================================================
# Skill-acceptance matching: plugin-qualified names (F6a) and user config merge (F6c)
# =================================================================================================

_REAL_CODE = "\n".join("def f%d():\n    return %d" % (i, i) for i in range(8)) + "\n"


class SkillAcceptanceMatching(GateTestCase):
    def test_plugin_qualified_code_review_skill_allow(self):
        """F6(a): a plugin-qualified skill name ('ballast:code-review') must satisfy the accepted
        set the same as the bare name ('code-review') on the reviewed-code path."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "ballast:code-review"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_user_config_custom_code_skill_allow(self):
        """F6(c): a user-side `<claude-home>/commit-gate.json` `{"code_skills": ["my-review"]}`
        merges into the accepted set -- a "my-review" Skill launch then satisfies the code path."""
        cfg_path = Path(self.home) / "commit-gate.json"
        cfg_path.write_text(json.dumps({"code_skills": ["my-review"]}), encoding="utf-8")
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "my-review"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())


# =================================================================================================
# Step 6: O1 turn-structure core -- the canonical false-positive guard + its siblings
# =================================================================================================

class O1TurnStructure(GateTestCase):
    def test_canonical_fix_guard_allow(self):
        """THE canonical FP guard: edit -> /code-review --fix launch -> fix-edits land MINUTES
        later (same turn, no user message in between) -> commit. MUST ALLOW -- a naive
        last_edit_ts > last_review_ts timestamp rule would wrongly block this exact case."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            skill_event(20, "code-review"),
            edit_event(300, edit_path),   # --fix rewriting bytes minutes later, same turn
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_reviewed_then_allow(self):
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "code-review"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_review_then_more_edits_same_turn_allow(self):
        """Spec bullet: 'review-then-more-edits SAME turn -> ALLOW (documented forgiven leak).'"""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            skill_event(20, "simplify"),
            edit_event(30, edit_path),
            edit_event(40, edit_path),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_ultracode_internal_review_only_would_block(self):
        """Spec bullet: 'ultracode-internal review only -> BLOCK (sidecar, not main transcript).'
        No Skill event anywhere in the (only) transcript this hook scans -- a review that only
        ever ran inside a dispatched subagent's own sidecar transcript is invisible here BY
        CONSTRUCTION (this hook never reads <uuid>/subagents/**), which is exactly the point:
        adversarial review inside a fanout is additive quality, not a substitute for this gate."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [genesis(), edit_event(10, edit_path)])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_review_before_compaction_commit_after_allow(self):
        """Spec bullet: 'review-before-compaction, commit-after -> ALLOW (archive scan).' The
        review + edit live ONLY in a compact-backup archive (as if the live transcript had already
        been rotated past them by compaction); the live transcript is present but has nothing
        useful in it. Must still ALLOW by merging the archive's events in."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        backup_dir = Path(self.home) / "compact-backups"
        backup_path = backup_dir / ("20260708-120000_manual_%s.jsonl" % self.session_id)
        write_transcript(backup_path, [
            genesis(), edit_event(10, edit_path), skill_event(20, "code-review"),
        ])
        write_transcript(self.transcript, [])  # live transcript: empty (post-compaction, no new events)
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_multi_cycle_user_message_then_edit_would_block(self):
        """Spec bullet: 'edit->review->commit->[user msg]->edit->commit2 no new review -> BLOCK.'
        A REAL first commit is made (establishing COMMIT_BOUNDARY for the second evaluation) so
        the first cycle's review (which predates that boundary) can't leak into the second."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        commit_now(self.repo, "first")  # cycle 1's real commit -- sets up history for cycle 2

        more_code = _REAL_CODE + "def extra():\n    return 99\n"
        write_and_stage(self.repo, "app.py", more_code)  # cycle 2's staged change

        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            skill_event(20, "code-review"),
            bash_event(30, 'git commit -m "first"', tool_id="toolu_commit1"),
            tool_result(31, "toolu_commit1", "[main abc1234] first"),
            user_message(40, "also add the extra() helper"),
            edit_event(50, edit_path),
        ])
        rc, out, err = self.run_hook(command='git commit -m "second"')
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_review_before_first_staged_edit_would_block(self):
        """F6(d): O1 condition (b) requires the LAST qualifying review launch to be AFTER the
        FIRST edit to a staged path in-window -- a review that launched before any work started
        proves nothing about the work actually being committed. Review-then-edit (reversed from
        the canonical fix-guard order) must WOULD-BLOCK with the specific 'review-not-after-
        first-staged-edit' reason."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            skill_event(10, "code-review"),  # review launches FIRST...
            edit_event(20, edit_path),        # ...then the staged-path edit happens
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("WOULD-BLOCK", last)
        self.assertIn("review-not-after-first-staged-edit", last)


# =================================================================================================
# O1 channels b & c: sidecar review (ballast-review / claude -p /code-review) + command-echo
# =================================================================================================

class O1SidecarAndEchoChannels(GateTestCase):
    def test_completed_ballast_review_sidecar_allow(self):
        """O1 channel b: a `ballast-review` Bash launch WITH its tool_result (a COMPLETED sidecar)
        credits the review pass. Edit-then-review order satisfies condition (b), same as a Skill
        launch would. The ALLOW reason names the sidecar channel."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "ballast-review high HEAD~1..HEAD two-phase shutdown fix",
                       tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1", "no findings"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("sidecar", last)  # channel-distinguishing reason text

    def test_ballast_review_without_tool_result_not_credited(self):
        """O1 channel b: a `ballast-review` launch WITHOUT a paired tool_result is a
        still-streaming or failed sidecar, not a completed review -- it must NOT credit, so an
        otherwise-unreviewed staged change WOULD-BLOCK."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "ballast-review high", tool_id="toolu_rev1"),
            # deliberately NO tool_result for toolu_rev1
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_path_qualified_mention_not_credited(self):
        """O1 channel b precision: a command that merely MENTIONS the shim file by path
        (`git add bin/ballast-review`) is not a sidecar launch -- the sanctioned invocation is the
        bare name at command position (the shim's bare-name contract). Crediting mentions would
        skew shadow measurements toward false ALLOWs in any repo where the path string appears."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "git add bin/ballast-review", tool_id="toolu_add1"),
            tool_result(21, "toolu_add1", ""),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_completed_raw_claude_p_code_review_allow(self):
        """O1 channel b: the raw headless form `claude -p "/code-review high"` (the `/code-review`
        payload quoted inside -p) is detected against the RAW command and, once COMPLETED, credits
        the review pass identically to the shim."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, 'claude -p "/code-review high"', tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1", "review complete"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("sidecar", last)

    def test_completed_raw_claude_print_long_flag_allow(self):
        """O1 channel b recall: the long-flag raw form `claude --print "/code-review ..."` is the
        variant people type by hand; it must credit identically to `-p`."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, 'claude --print "/code-review high"', tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1", "review complete"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("sidecar", last)

    def test_user_command_echo_code_review_allow(self):
        """O1 channel c: a user-typed `/code-review` lands as a `<command-name>` echo in a user
        message (NOT a Skill event, since native /code-review is user-invoke-only now). Its
        normalized name ('code-review') is in the accepted code set, so it credits the pass. The
        echo's own user turn is AT the review ts (not strictly after), so condition (c) is fine."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            user_message(20, "<command-name>/code-review</command-name>"
                             "<command-message>code-review</command-message>"
                             "<command-args>high</command-args>"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("command-echo", last)  # channel-distinguishing reason text

    def test_user_command_echo_non_accepted_command_not_credited(self):
        """O1 channel c: an echo of a NON-review command (`/status`) normalizes to 'status', which
        is not in the accepted set -- it must NOT credit, so the staged change WOULD-BLOCK (proves
        the echo channel still filters against the accepted set, exactly as the Skill channel does)."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            user_message(20, "<command-name>/status</command-name>"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_errored_sidecar_result_not_credited(self):
        """O1 channel b: a tool_result lands for FAILURES too (is_error=True) -- e.g. the shim's
        own exit-1 rejection paths (usage, --fix refusal, missing CLI). An errored sidecar is not
        a completed review; crediting it would let `ballast-review --fix ...` (rejected, never
        launched) fake a review pass."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "ballast-review --fix high", tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1",
                        "ballast-review: sidecar reviews are read-only", is_error=True),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_sidecar_credits_docs_commit_after_o3_settle(self):
        """O1 channel b x O3 SETTLE (one-way widening): a sidecar IS a code-review run, and since
        the docs route now accepts the code skills (a code-review is a HEAVIER review than the docs
        gate), a completed code-review sidecar credits a docs-only commit too. Was WOULD-BLOCK
        before the settle -- the intended side effect of Change 2."""
        edit_path = write_and_stage(self.repo, "notes/design.md", "# Design\n\nprose only\n")
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "ballast-review high docs sweep", tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1", "no findings"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("sidecar", last)

    def test_raw_claude_p_mention_not_credited(self):
        """O1 channel b precision: the raw form must match only at INVOCATION position -- a quoted
        MENTION of the command (`echo "claude -p /code-review high"`) is not a launch and must not
        credit once its tool_result lands."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, 'echo "claude -p /code-review high"', tool_id="toolu_echo1"),
            tool_result(21, "toolu_echo1", "claude -p /code-review high"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())

    def test_sidecar_review_before_commit_boundary_not_credited(self):
        """O1 window: a COMPLETED sidecar review that predates the COMMIT_BOUNDARY (a real prior
        commit) belongs to the previous cycle and must NOT leak into the current evaluation. A
        first real commit sets the boundary; the cycle-2 staged change has no review after it, so
        it WOULD-BLOCK despite the earlier sidecar."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        commit_now(self.repo, "first")  # cycle 1's real commit -- sets up history for cycle 2

        more_code = _REAL_CODE + "def extra():\n    return 99\n"
        write_and_stage(self.repo, "app.py", more_code)  # cycle 2's staged change

        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, edit_path),
            bash_event(20, "ballast-review high", tool_id="toolu_rev1"),
            tool_result(21, "toolu_rev1", "no findings"),          # sidecar completes in cycle 1...
            bash_event(30, 'git commit -m "first"', tool_id="toolu_commit1"),
            tool_result(31, "toolu_commit1", "[main abc1234] first"),  # ...sets COMMIT_BOUNDARY here
            edit_event(50, edit_path),                             # cycle 2 edit, no new review
        ])
        rc, out, err = self.run_hook(command='git commit -m "second"')
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())


# =================================================================================================
# Change 1: pathspec-aware staged set (`git commit <paths>` / empty-index / partial-staging gap)
# =================================================================================================

def track_and_modify(repo_dir, relpath, initial, modified):
    """Commit `initial` for `relpath` (so it's tracked and the index == HEAD -- the "empty index"
    a commit-by-path session presents), then rewrite it to `modified` on disk WITHOUT `git add`
    (a working-tree-only change, exactly what `git commit <path>` records). Returns abs path."""
    p = write_and_stage(repo_dir, relpath, initial)
    commit_now(repo_dir, "add %s" % relpath)
    Path(p).write_text(modified, encoding="utf-8", newline="")
    return p


_MORE_CODE = _REAL_CODE + "def extra():\n    return 99\n"


class ExtractPathspecsUnit(unittest.TestCase):
    """Direct unit tests of _extract_pathspecs -- the parser cases (quoting / value-flag skipping /
    `--` separator) whose CORRECTNESS is exact-list, not observable end-to-end (a bogus pathspec
    just contributes an empty diff slice, so a staged-count assertion can't discriminate it)."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_hook_module()

    def test_quoted_pathspec_with_space(self):
        # Spec test 2: a quoted path containing a space survives as ONE pathspec (surrounding
        # quotes stripped) -- if the tokenizer split on the space this would be two bogus paths.
        self.assertEqual(self.mod._extract_pathspecs('git commit "my dir/app.py" -m "wip"'),
                         ["my dir/app.py"])

    def test_message_value_flag_not_a_pathspec(self):
        # Spec test 3: `-m` consumes its following token as the message; only foo.py is a pathspec.
        self.assertEqual(self.mod._extract_pathspecs('git commit -m "add foo" foo.py'),
                         ["foo.py"])

    def test_double_dash_separator(self):
        # Spec test 4: everything after a bare `--` is unambiguously a pathspec.
        self.assertEqual(self.mod._extract_pathspecs('git commit -m x -- a.py b.py'),
                         ["a.py", "b.py"])

    def test_message_only_no_pathspecs(self):
        self.assertEqual(self.mod._extract_pathspecs('git commit -m "just a message"'), [])

    def test_no_commit_token_returns_none(self):
        self.assertIsNone(self.mod._extract_pathspecs("git status"))

    def test_global_flags_before_commit_skipped(self):
        # The same global-flag shapes _COMMIT_RE tolerates are walked past to reach `commit`.
        self.assertEqual(
            self.mod._extract_pathspecs("git --no-pager -c core.x=y commit -m z app.py"),
            ["app.py"])

    def test_equals_form_value_flag_consumes_nothing_extra(self):
        # `--message=...` carries its value inline -- the NEXT token is still a pathspec.
        self.assertEqual(self.mod._extract_pathspecs("git commit --message=wip app.py"),
                         ["app.py"])


class PathspecStagedSet(GateTestCase):
    def test_by_path_empty_index_evaluates_with_pathspec_src(self):
        """Spec test 1: `git commit foo.py bar.py -F msg.txt` over an EMPTY index with real
        working-tree changes -> the gate EVALUATES (not the no-op `empty-staged-diff` a plain
        --cached read would score), sees BOTH files (staged=2), and the reason carries
        `src=pathspec`. A review makes it ALLOW so both the staged count and the src tag are
        visible in one line."""
        foo = track_and_modify(self.repo, "foo.py", _REAL_CODE, _MORE_CODE)
        bar = track_and_modify(self.repo, "bar.py", _REAL_CODE, _MORE_CODE)
        write_transcript(self.transcript, [
            genesis(),
            edit_event(10, foo),
            edit_event(11, bar),
            skill_event(20, "code-review"),
        ])
        rc, out, err = self.run_hook(command="git commit foo.py bar.py -F msg.txt")
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertNotIn("empty-staged-diff", last)  # the gap this change closes
        self.assertIn("staged=2", last)
        self.assertIn("src=pathspec", last)

    def test_by_path_quoted_space_path_evaluated_end_to_end(self):
        """Spec test 2 (end-to-end): a quoted pathspec with a space threads through as a single
        argv element to `git diff HEAD -- "my dir/app.py"`. If it had split, git would match
        nothing and the gate would score empty-staged-diff -- so ALLOW/staged=1 IS the discriminator."""
        edit_path = track_and_modify(self.repo, "my dir/app.py", _REAL_CODE, _MORE_CODE)
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "code-review"),
        ])
        rc, out, err = self.run_hook(command='git commit "my dir/app.py" -m "wip"')
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertNotIn("empty-staged-diff", last)
        self.assertIn("staged=1", last)
        self.assertIn("src=pathspec", last)

    def test_no_pathspec_empty_index_still_empty_staged_allow(self):
        """Spec test 5: no pathspecs (`git commit -F msg.txt`, its file arg consumed by -F) with
        nothing staged keeps the existing `ALLOW empty-staged-diff` -- the pathspec route must NOT
        fire when there are no pathspecs, and this line must stay byte-identical (no src tag)."""
        write_transcript(self.transcript, [genesis()])
        rc, out, err = self.run_hook(command="git commit -F msg.txt")
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("ALLOW", last)
        self.assertIn("empty-staged-diff", last)
        self.assertNotIn("src=pathspec", last)

    def test_partial_staging_pathspec_sees_both_staged_and_unstaged(self):
        """Spec test 8 (the under-scope fix): index holds A only; the commit names A and B, with B
        modified but UNSTAGED. `git commit A B` records the working-tree state of both, so the gate
        must see BOTH (a plain --cached read would show only A and under-scope). No review -> the
        WOULD-BLOCK reason names both uncovered paths and carries src=pathspec."""
        a = write_and_stage(self.repo, "a.py", _REAL_CODE)
        b = write_and_stage(self.repo, "b.py", _REAL_CODE)
        commit_now(self.repo, "add a and b")
        (Path(self.repo) / "a.py").write_text(_MORE_CODE, encoding="utf-8", newline="")
        (Path(self.repo) / "b.py").write_text(_MORE_CODE, encoding="utf-8", newline="")
        run_git(["add", "a.py"], self.repo)  # stage A only; B stays a working-tree-only change
        write_transcript(self.transcript, [genesis()])  # no review anywhere
        rc, out, err = self.run_hook(command="git commit a.py b.py")
        self.assertEqual(rc, 0)
        last = self.last_log()
        self.assertIn("WOULD-BLOCK", last)
        self.assertIn("a.py", last)
        self.assertIn("b.py", last)  # B's unstaged half must be visible
        self.assertIn("src=pathspec", last)


# =================================================================================================
# Change 2: one-way docs-route acceptance (docs accepts the code set; code never accepts docs)
# =================================================================================================

class OneWayDocsAcceptance(GateTestCase):
    def test_docs_routed_plugin_qualified_code_review_allow(self):
        """Spec test 6: a docs-only commit is satisfied by a code review (one-way widening -- a
        full code-review is a heavier review than the docs gate). A PLUGIN-QUALIFIED
        `ballast:code-review` Skill launch credits it (the prefix-strip + the widening together)."""
        edit_path = write_and_stage(
            self.repo, "notes/design.md",
            "# Design\n\nSome longer prose describing the change in enough detail that it is not "
            "whitespace-only and not a tiny comment-only diff by any measure at all.\n")
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "ballast:code-review"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", self.last_log())

    def test_code_routed_durable_docs_only_would_block(self):
        """Spec test 7 (the asymmetry): the widening is ONE-WAY. A CODE commit reviewed ONLY by
        durable-docs must still WOULD-BLOCK -- a docs-gate pass says nothing about code."""
        edit_path = write_and_stage(self.repo, "app.py", _REAL_CODE)
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "durable-docs"),
        ])
        rc, out, err = self.run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())


# =================================================================================================
# Transcript-unreadable fail path (fail-closed in spirit; shadow still exits 0)
# =================================================================================================

class TranscriptUnreadable(GateTestCase):
    def test_unreadable_transcript_would_block_log_exit_zero(self):
        write_and_stage(self.repo, "app.py", _REAL_CODE)
        rc, out, err = self.run_hook(transcript_path=os.path.join(self.home, "no-such-file.jsonl"))
        self.assertEqual(rc, 0)
        self.assertIn("WOULD-BLOCK", self.last_log())
        self.assertIn("transcript-unreadable", self.last_log())


# =================================================================================================
# F3: shadow-log injection guard (_log sanitizes embedded CR/LF; one call == one physical line)
# =================================================================================================

class LogInjectionGuard(GateTestCase):
    def test_log_reason_with_embedded_newline_yields_exactly_one_line(self):
        """F3 regression. A crafted reason string (as would occur if a staged filename with an
        embedded newline flowed into the 'uncovered=...' reason text -- Windows forbids a literal
        newline in a filename, so this exercises `_log` directly rather than via a real staged
        file) must not fracture the append-only shadow log into extra fabricated lines. Also
        exercises session_id sanitization."""
        mod = load_hook_module()
        os.environ["BALLAST_CLAUDE_HOME"] = self.home
        try:
            before = log_lines(self.home)
            malicious_reason = (
                "need-review uncovered=evil.py\n"
                "2026-01-01T00:00:00.000Z ALLOW forged-entry session=attacker\r\n"
                "trailing"
            )
            mod._log("WOULD-BLOCK", malicious_reason, "sess\nid\rwith-newline")
            after = log_lines(self.home)
        finally:
            os.environ.pop("BALLAST_CLAUDE_HOME", None)

        self.assertEqual(len(after), len(before) + 1, "exactly one new line must be appended")
        new_line = after[-1]
        self.assertNotIn("\n", new_line)
        self.assertNotIn("\r", new_line)
        self.assertIn("forged-entry", new_line)  # content preserved, just de-linified
        self.assertIn("trailing", new_line)


# =================================================================================================
# Shadow contract: EVERY path exits 0, no matter what was decided
# =================================================================================================

class ShadowContract(GateTestCase):
    def test_every_scenario_exits_zero(self):
        scenarios = []

        # 1) malformed stdin
        env = os.environ.copy()
        env["BALLAST_CLAUDE_HOME"] = self.home
        p = subprocess.run([sys.executable, HOOK], input="not json{",
                            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, "malformed stdin must still exit 0")

        # 2) non-dict payload
        p = subprocess.run([sys.executable, HOOK], input="[]",
                            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, "non-dict payload must still exit 0")

        # 3) empty staged (ALLOW)
        rc, _, _ = self.run_hook(transcript_path="/does/not/exist.jsonl")
        self.assertEqual(rc, 0)

        # 4) unreadable transcript on a real qualifying commit (WOULD-BLOCK)
        write_and_stage(self.repo, "app.py", _REAL_CODE)
        rc, _, _ = self.run_hook(transcript_path=os.path.join(self.home, "missing.jsonl"))
        self.assertEqual(rc, 0)

        # 5) reviewed commit (ALLOW)
        edit_path = str(Path(self.repo) / "app.py")
        write_transcript(self.transcript, [
            genesis(), edit_event(10, edit_path), skill_event(20, "code-review"),
        ])
        rc, _, _ = self.run_hook()
        self.assertEqual(rc, 0)

        # 6) kill switch engaged
        (Path(self.home) / ".commit-gate-off").touch()
        rc, _, _ = self.run_hook()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
