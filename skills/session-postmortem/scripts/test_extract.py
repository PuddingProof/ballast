"""Unit tests for the generalized session-postmortem extract.py.

Covers only the project-agnostic behaviors that changed during generalization:
  - topic_slug bucketing relative to cwd
  - _looks_like_findings result-shape classifier
  - invocations slash-command capture + findings-shape tagging

Run from the skill directory:  python test_extract.py
"""
import io
import json
import os
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr

import extract


def write_transcript(lines):
    """Write a list of dict entries to a temp .jsonl, return its path."""
    fd, path = tempfile.mkstemp(suffix='.jsonl')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        for d in lines:
            f.write(json.dumps(d) + '\n')
    return path


def assistant_edit(ts, file_path, name='Edit'):
    """Build a transcript entry representing one Edit/Write/MultiEdit tool call."""
    return {
        'type': 'assistant',
        'timestamp': ts,
        'message': {'content': [
            {'type': 'tool_use', 'name': name, 'input': {'file_path': file_path}}
        ]},
    }


def capture(fn, *args):
    """Call fn(*args), return everything it printed to stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


class TopicSlugTest(unittest.TestCase):
    def test_buckets_by_top_folder_relative_to_cwd(self):
        with tempfile.TemporaryDirectory() as proj:
            # 4 of 5 edits under tools/ -> 80% concentration -> slug "tools"
            lines = [
                assistant_edit('2026-05-28T00:00:01Z', os.path.join(proj, 'tools', 'a.py')),
                assistant_edit('2026-05-28T00:00:02Z', os.path.join(proj, 'tools', 'b.py')),
                assistant_edit('2026-05-28T00:00:03Z', os.path.join(proj, 'tools', 'c.py')),
                assistant_edit('2026-05-28T00:00:04Z', os.path.join(proj, 'tools', 'd.py')),
                assistant_edit('2026-05-28T00:00:05Z', os.path.join(proj, 'themes', 'e.json')),
            ]
            tpath = write_transcript(lines)
            old = os.getcwd()
            try:
                os.chdir(proj)
                out = capture(extract.topic_slug, tpath)
            finally:
                os.chdir(old)
                os.remove(tpath)
            self.assertEqual(out.strip(), 'tools')

    def test_mixed_footprint_falls_back_to_general(self):
        with tempfile.TemporaryDirectory() as proj:
            lines = [
                assistant_edit('2026-05-28T00:00:01Z', os.path.join(proj, 'tools', 'a.py')),
                assistant_edit('2026-05-28T00:00:02Z', os.path.join(proj, 'themes', 'b.json')),
                assistant_edit('2026-05-28T00:00:03Z', os.path.join(proj, 'notes', 'c.md')),
            ]
            tpath = write_transcript(lines)
            old = os.getcwd()
            try:
                os.chdir(proj)
                out = capture(extract.topic_slug, tpath)
            finally:
                os.chdir(old)
                os.remove(tpath)
            self.assertEqual(out.strip(), 'general')

    def test_edits_outside_cwd_are_ignored(self):
        with tempfile.TemporaryDirectory() as proj:
            lines = [assistant_edit('2026-05-28T00:00:01Z', 'C:/somewhere/else/x.py')]
            tpath = write_transcript(lines)
            old = os.getcwd()
            try:
                os.chdir(proj)
                out = capture(extract.topic_slug, tpath)
            finally:
                os.chdir(old)
                os.remove(tpath)
            self.assertEqual(out.strip(), 'general')


class BucketHelperTest(unittest.TestCase):
    """Unit tests for _bucket_winner and _bucket_paths (ENG-4 helpers)."""

    def test_bucket_winner_empty_returns_general(self):
        self.assertEqual(extract._bucket_winner(Counter()), 'general')

    def test_bucket_winner_clear_plurality(self):
        self.assertEqual(
            extract._bucket_winner(Counter({'engine': 5, 'tools': 2})), 'engine')

    def test_bucket_winner_tie_returns_general(self):
        self.assertEqual(
            extract._bucket_winner(Counter({'alpha': 3, 'beta': 3})), 'general',
            'tied top two buckets are ambiguous — collapse to general')

    def test_bucket_winner_single_bucket(self):
        self.assertEqual(extract._bucket_winner(Counter({'tools': 1})), 'tools')

    def test_bucket_paths_outside_cwd_skipped(self):
        with tempfile.TemporaryDirectory() as proj:
            counts = extract._bucket_paths(
                ['C:/somewhere/else/x.py'], proj, {'.debug'})
            self.assertEqual(sum(counts.values()), 0,
                             'paths outside cwd contribute nothing')

    def test_bucket_paths_ignored_bucket_skipped(self):
        with tempfile.TemporaryDirectory() as proj:
            counts = extract._bucket_paths(
                [os.path.join(proj, '.debug', 'script.ps1')], proj, {'.debug'})
            self.assertEqual(sum(counts.values()), 0, '.debug is in ignored_buckets')

    def test_bucket_paths_camelcase_slugified(self):
        # CamelCase boundary: OpenMeteoAPI -> open-meteo-api.
        with tempfile.TemporaryDirectory() as proj:
            counts = extract._bucket_paths(
                [os.path.join(proj, 'OpenMeteoAPI', 'client.py')], proj, {'.debug'})
            self.assertIn('open-meteo-api', counts)

    def test_bucket_paths_root_level_file_uses_stem(self):
        # A root-level file uses its stem (len(parts)==1 path).
        with tempfile.TemporaryDirectory() as proj:
            counts = extract._bucket_paths(
                [os.path.join(proj, 'IDEAS.md')], proj, {'.debug'})
            self.assertIn('ideas', counts)


class TopicSlugGitFallbackTest(unittest.TestCase):
    """Tests for the git-diff fallback path in topic_slug (ENG-4).

    `git_files_raw` is the optional newline-separated git-changed-file list, passed as a real
    parameter (E5) rather than read from sys.argv. We capture both stdout (the slug) and stderr
    (the optional fallback note).
    """

    def _run(self, transcript_lines, git_files, proj_dir):
        """Run topic_slug with an explicit git_files_raw; return (stdout.strip(), stderr.strip())."""
        tpath = write_transcript(transcript_lines)
        git_arg = '\n'.join(git_files)  # newline-separated, mirrors real git output
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        old_cwd = os.getcwd()
        try:
            os.chdir(proj_dir)
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                extract.topic_slug(tpath, git_arg)
        finally:
            os.chdir(old_cwd)
            os.remove(tpath)
        return buf_out.getvalue().strip(), buf_err.getvalue().strip()

    def test_git_fallback_overrides_when_no_tool_edits(self):
        """Pure shell session (zero Edit/Write): 10 git files in engine/ -> 'engine'."""
        with tempfile.TemporaryDirectory() as proj:
            git_files = [os.path.join(proj, 'engine', f'f{i}.rs') for i in range(10)]
            slug, note = self._run([], git_files, proj)
            self.assertEqual(slug, 'engine')
            self.assertIn('git-diff fallback', note)
            self.assertIn('10 changed files', note)
            self.assertIn('0 tool edits', note)

    def test_git_fallback_overrides_low_signal_src_bucket(self):
        """Tool edits land in 'src' (low-signal); 8 git files in 'engine/' override."""
        with tempfile.TemporaryDirectory() as proj:
            tool_lines = [
                assistant_edit('2026-06-27T00:00:01Z', os.path.join(proj, 'src', 'a.rs')),
                assistant_edit('2026-06-27T00:00:02Z', os.path.join(proj, 'src', 'b.rs')),
            ]
            git_files = [os.path.join(proj, 'engine', f'f{i}.rs') for i in range(8)]
            slug, note = self._run(tool_lines, git_files, proj)
            self.assertEqual(slug, 'engine', "'src' is low-signal; git bucket should win")
            self.assertIn('git-diff fallback', note)

    def test_git_fallback_does_not_override_representative_tool_slug(self):
        """5 tool edits in 'tools/', 6 git files in 'engine/' — not >=3x gap -> no override."""
        with tempfile.TemporaryDirectory() as proj:
            tool_lines = [
                assistant_edit('2026-06-27T00:00:01Z', os.path.join(proj, 'tools', f'f{i}.py'))
                for i in range(5)
            ]
            # 5 * 3 = 15 > 6, so material_gap=False; tool_slug='tools' not low-signal.
            git_files = [os.path.join(proj, 'engine', f'f{i}.rs') for i in range(6)]
            slug, note = self._run(tool_lines, git_files, proj)
            self.assertEqual(slug, 'tools', 'representative tool slug must not be overridden')
            self.assertEqual(note, '', 'no fallback note when tool slug wins')

    def test_git_fallback_material_gap_triggers_override(self):
        """2 tool edits in 'tools/', 15 git files in 'engine/' — 3x gap -> override."""
        with tempfile.TemporaryDirectory() as proj:
            tool_lines = [
                assistant_edit('2026-06-27T00:00:01Z', os.path.join(proj, 'tools', 'a.py')),
                assistant_edit('2026-06-27T00:00:02Z', os.path.join(proj, 'tools', 'b.py')),
            ]
            # 2 * 3 = 6 < 15 and 15 >= 5 -> material_gap=True
            git_files = [os.path.join(proj, 'engine', f'f{i}.rs') for i in range(15)]
            slug, note = self._run(tool_lines, git_files, proj)
            self.assertEqual(slug, 'engine',
                             'git-changed count 3x+ tool edits -> git bucket wins')
            self.assertIn('git-diff fallback', note)

    def test_git_fallback_no_override_when_git_also_low_signal(self):
        """Zero tool edits + 10 git files all in 'src/' -> git also low-signal -> no override."""
        with tempfile.TemporaryDirectory() as proj:
            git_files = [os.path.join(proj, 'src', f'f{i}.rs') for i in range(10)]
            slug, note = self._run([], git_files, proj)
            self.assertEqual(slug, 'general',
                             "git bucket 'src' is also low-signal; tool result stands")
            self.assertEqual(note, '', 'no fallback note when git cannot improve')

    def test_git_fallback_skipped_when_arg_absent(self):
        """Without argv[3], topic_slug behaves exactly as before (no regression)."""
        with tempfile.TemporaryDirectory() as proj:
            tool_lines = [
                assistant_edit('2026-06-27T00:00:01Z', os.path.join(proj, 'tools', 'a.py')),
            ]
            tpath = write_transcript(tool_lines)
            old_cwd = os.getcwd()
            try:
                os.chdir(proj)
                slug = capture(extract.topic_slug, tpath).strip()
            finally:
                os.chdir(old_cwd)
                os.remove(tpath)
            self.assertEqual(slug, 'tools', 'no git arg -> original tool-edit logic unchanged')

    def test_git_fallback_zero_edits_small_git_count_fires_via_low_signal(self):
        """0 tool edits + just 2 git files in 'engine/' -> override via condition (a): the tool slug
        is 'general' (low-signal), so the >=5-file floor does NOT apply (it only guards a
        representative tool slug). Locks in the deliberate floor-asymmetry the docstring describes."""
        with tempfile.TemporaryDirectory() as proj:
            git_files = [os.path.join(proj, 'engine', f'f{i}.rs') for i in range(2)]
            slug, note = self._run([], git_files, proj)
            self.assertEqual(slug, 'engine', 'zero-edit low-signal slug overridden even with <5 git files')
            self.assertIn('git-diff fallback', note)


class FindingsShapeTest(unittest.TestCase):
    def test_findings_report_detected(self):
        text = (
            "Found 3 issues:\n"
            "1. Critical: SQL injection in db.py:45\n"
            "2. Warning: unvalidated input in api.py:88\n"
        )
        self.assertTrue(extract._looks_like_findings(text))

    def test_launching_skill_not_findings(self):
        self.assertFalse(
            extract._looks_like_findings("Launching skill: superpowers:brainstorming")
        )

    def test_plain_file_mention_not_findings(self):
        # No file:line, no severity vocab, no numbering -> below threshold
        self.assertFalse(
            extract._looks_like_findings("See src/app.py and src/util.py for the details here.")
        )

    def test_short_text_not_findings(self):
        self.assertFalse(extract._looks_like_findings("ok"))

    def test_json_findings_report_detected(self):
        # code-review finder agents emit findings as a JSON array of objects with
        # separate file/line fields (no inline "file.py:45") — must still tag as findings.
        text = (
            'Here are the findings.\n'
            '[\n'
            '  {"file": "tools/theme_render.py", "line": 957, "summary": "Dead code: helpers unused",\n'
            '   "failure_scenario": "135 lines maintained-but-unused"}\n'
            ']'
        )
        self.assertTrue(extract._looks_like_findings(text))

    def test_generic_json_not_findings(self):
        # A non-findings JSON blob with only generic keys stays out of the disposition table.
        self.assertFalse(
            extract._looks_like_findings('{"status": "ok", "count": 3, "name": "render loop"}')
        )


class InvocationsTest(unittest.TestCase):
    def test_slash_command_echo_captured(self):
        lines = [{
            'type': 'user',
            'timestamp': '2026-05-28T00:00:01Z',
            'message': {'content':
                '<command-name>code-review</command-name>\n<command-args>HEAD~2</command-args>'},
        }]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('slash-command: /code-review HEAD~2', out)

    def test_slash_command_leading_slash_not_doubled(self):
        # Some echoes store the name WITH a leading slash, e.g. "/compact" -> must not double it
        lines = [{
            'type': 'user',
            'timestamp': '2026-05-28T00:00:01Z',
            'message': {'content': '<command-name>/compact</command-name>'},
        }]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('slash-command: /compact', out)
        self.assertNotIn('//compact', out)

    def test_compact_summary_slash_echo_not_counted(self):
        # A post-compaction recap (isCompactSummary) quotes prior <command-name> blobs verbatim —
        # it must NOT mint a slash-command row (verified live 2026-07-20: phantom /code-review
        # entries at each compact boundary). A genuine echo alongside it still counts.
        lines = [
            {'type': 'user', 'timestamp': '2026-05-28T00:00:01Z', 'isCompactSummary': True,
             'message': {'content':
                 'This session is being continued…\n<command-name>code-review</command-name>'}},
            {'type': 'user', 'timestamp': '2026-05-28T00:00:02Z',
             'message': {'content': '<command-name>/compact</command-name>'}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertNotIn('slash-command: /code-review', out)
        self.assertIn('slash-command: /compact', out)

    def test_skill_result_tagged_findings_yes(self):
        lines = [
            {'type': 'assistant', 'timestamp': '2026-05-28T00:00:01Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'code-review'}}
             ]}},
            {'type': 'user', 'timestamp': '2026-05-28T00:00:02Z',
             'message': {'content': [
                 {'type': 'tool_result', 'content':
                     "Found issues:\n1. Critical: bug in db.py:45\n2. Warning in api.py:88"}
             ]}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('Skill: code-review', out)
        self.assertIn('findings-shape: yes', out)

    def test_launching_skill_tagged_findings_no(self):
        lines = [
            {'type': 'assistant', 'timestamp': '2026-05-28T00:00:01Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'brainstorming'}}
             ]}},
            {'type': 'user', 'timestamp': '2026-05-28T00:00:02Z',
             'message': {'content': [
                 {'type': 'tool_result', 'content': "Launching skill: superpowers:brainstorming"}
             ]}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('findings-shape: no', out)


class SessionUuidFromPathTest(unittest.TestCase):
    UUID = 'a6241b4e-ac7a-4f67-a656-3a7a7be7c0c0'

    def test_live_transcript_filename(self):
        self.assertEqual(
            extract._session_uuid_from_path(f'C:/x/projects/foo/{self.UUID}.jsonl'),
            self.UUID,
        )

    def test_precompact_archive_filename(self):
        # <timestamp>_<trigger>_<uuid>.jsonl
        self.assertEqual(
            extract._session_uuid_from_path(f'C:/x/compact-backups/20260529-033134_manual_{self.UUID}.jsonl'),
            self.UUID,
        )

    def test_no_uuid_falls_back_to_stem(self):
        self.assertEqual(
            extract._session_uuid_from_path('C:/x/weird-name.jsonl'),
            'weird-name',
        )


# ---------------------------------------------------------------------------
# Prompt-counting regressions (ports of cc-dashboard core/parse.rs tests).
# These are the load-bearing rules the audit found untested: isMeta exclusion,
# toolUseResult-field tool-results, and mid-turn queued_command/prompt steers.
# ---------------------------------------------------------------------------
def user_line(text, ts='2026-06-08T00:00:00Z', **extra):
    """A type:user line carrying a plain text-block content array."""
    d = {'type': 'user', 'timestamp': ts,
         'message': {'content': [{'type': 'text', 'text': text}]}}
    d.update(extra)
    return d


def attachment_line(att_type, command_mode=None, body='steer', ts='2026-06-08T00:00:00Z'):
    att = {'type': att_type}
    if command_mode is not None:
        att['commandMode'] = command_mode
    att['prompt'] = [{'type': 'text', 'text': body}]
    return {'type': 'attachment', 'timestamp': ts, 'attachment': att}


class UserPromptCountingTest(unittest.TestCase):
    def _counts(self, lines):
        tpath = write_transcript(lines)
        try:
            return extract._count_tool_calls(tpath)  # (tools, user_prompts, assistant_responses)
        finally:
            os.remove(tpath)

    def test_ismeta_line_is_not_a_prompt(self):
        # Injected synthetic lines (skill base-dir injection, SessionStart caveat, hook context)
        # carry isMeta:true with plain text content — must NOT count and must NOT be emitted.
        lines = [
            user_line('a real prompt'),
            user_line('Base directory for this skill: C:/x', isMeta=True),
        ]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'isMeta injected line is not a user prompt')
        tpath = write_transcript(lines)
        try:
            out = capture(extract.user_msgs, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('a real prompt', out)
        self.assertNotIn('Base directory for this skill', out)

    def test_tool_result_field_line_is_not_a_prompt(self):
        # A tool-result user line can render content as plain list[text] while carrying the
        # top-level toolUseResult / sourceToolUseID field — the field is the load-bearing signal.
        lines = [
            user_line('real prompt'),
            user_line('tool output text', toolUseResult={'ok': 1}),
            user_line('more output', sourceToolUseID='abc'),
        ]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'toolUseResult/sourceToolUseID lines are not prompts')

    def test_queued_prompt_counts_but_task_notification_does_not(self):
        # Mid-turn steers are attachment/queued_command lines, NOT type:user. commandMode 'prompt'
        # counts; 'task-notification' and a missing mode do not (fail-closed allow-list).
        lines = [
            user_line('start the workflow'),
            attachment_line('queued_command', 'prompt', body='also handle the edge case'),
            attachment_line('queued_command', 'task-notification', body='task done'),
            attachment_line('queued_command', None, body='no mode'),
            attachment_line('todo_reminder', None, body='unrelated'),
        ]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 2, 'only the standalone turn + the commandMode:prompt steer count')

    def test_queued_steer_is_emitted_and_tagged(self):
        lines = [attachment_line('queued_command', 'prompt', body='no, do X instead')]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.user_msgs, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('no, do X instead', out)
        self.assertIn('mid-turn steer', out)

    def test_task_notification_user_line_is_not_a_prompt(self):
        # Background-task notices ALSO appear as plain NON-meta `user` lines carrying
        # origin.kind == "task-notification" (the user-line twin of the attachment form
        # above — both shapes coexist back to 2026-05; port of the cc-dashboard parse.rs
        # fix 5df4845 from the 2026-06-09 census).
        lines = [
            user_line('a real prompt'),
            user_line('<task-notification>workflow done</task-notification>',
                      promptSource='system', origin={'kind': 'task-notification'}),
        ]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'origin.kind=task-notification user line is not a turn')
        tpath = write_transcript(lines)
        try:
            out = capture(extract.user_msgs, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('a real prompt', out)
        self.assertNotIn('workflow done', out, 'task notice must not be emitted as a user msg')

    def test_non_dict_message_user_line_is_not_a_prompt(self):
        # A malformed user line whose `message` is not a dict must not count (restores the
        # guard the old _count_tool_calls had; _is_real_user_msg now owns it for both callers).
        lines = [{'type': 'user', 'timestamp': '2026-06-08T00:00:00Z', 'message': 'oops a string'}]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 0)

    def test_assistant_responses_dedup_by_message_id(self):
        # One API response is logged as multiple content-block lines sharing a message.id;
        # counting DISTINCT ids gives real response count, not the ~2.4x line count.
        def asst(mid, block):
            return {'type': 'assistant', 'timestamp': '2026-06-08T00:00:01Z',
                    'message': {'id': mid, 'model': 'claude-opus-4-8', 'content': [block]}}
        lines = [
            asst('msg_1', {'type': 'thinking', 'thinking': '...'}),
            asst('msg_1', {'type': 'text', 'text': 'ok'}),
            asst('msg_1', {'type': 'tool_use', 'name': 'Read', 'input': {}}),
            asst('msg_2', {'type': 'text', 'text': 'done'}),
        ]
        tools, _, responses = self._counts(lines)
        self.assertEqual(responses, 2, '3 lines of msg_1 + 1 line of msg_2 = 2 responses')
        self.assertEqual(tools['Read'], 1, 'tool_use still counted once')


class InvocationsFifoTest(unittest.TestCase):
    def test_back_to_back_agents_both_emitted(self):
        # Two Agent dispatches in one response = two consecutive assistant lines before any
        # tool_result. A single-slot tracker dropped the first; the FIFO queue keeps both.
        lines = [
            {'type': 'assistant', 'timestamp': '2026-06-08T00:00:01Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Agent', 'id': 'tu_1',
                  'input': {'subagent_type': 'Explore', 'description': 'first agent'}}]}},
            {'type': 'assistant', 'timestamp': '2026-06-08T00:00:02Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Agent', 'id': 'tu_2',
                  'input': {'subagent_type': 'Explore', 'description': 'second agent'}}]}},
            {'type': 'user', 'timestamp': '2026-06-08T00:00:03Z',
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': 'result one'}]}},
            {'type': 'user', 'timestamp': '2026-06-08T00:00:04Z',
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'tu_2', 'content': 'result two'}]}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(out.count('Agent: first agent'), 1, 'first agent must not be clobbered')
        self.assertEqual(out.count('Agent: second agent'), 1)

    def test_workflow_with_null_script_does_not_crash(self):
        # A Workflow tool_use whose `script` key is present but JSON null must not crash the
        # label fallback (inp.get('script','') would return None, and None[:60] raises).
        lines = [
            {'type': 'assistant', 'timestamp': '2026-06-08T00:00:01Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Workflow', 'id': 'tu_wf',
                  'input': {'script': None, 'name': 'my-workflow'}}]}},
            {'type': 'user', 'timestamp': '2026-06-08T00:00:02Z',
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'tu_wf', 'content': 'launched'}]}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)  # must not raise
        finally:
            os.remove(tpath)
        self.assertIn('Workflow: my-workflow', out)

    def test_tool_use_id_pairs_result_to_correct_invocation(self):
        # An interleaved non-tracked tool_result (Read) must not consume a pending Agent.
        lines = [
            {'type': 'assistant', 'timestamp': '2026-06-08T00:00:01Z',
             'message': {'content': [
                 {'type': 'tool_use', 'name': 'Agent', 'id': 'tu_agent',
                  'input': {'description': 'the agent'}},
                 {'type': 'tool_use', 'name': 'Read', 'id': 'tu_read', 'input': {}}]}},
            {'type': 'user', 'timestamp': '2026-06-08T00:00:02Z',
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'tu_read', 'content': 'file contents'},
                 {'type': 'tool_result', 'tool_use_id': 'tu_agent', 'content': 'agent findings'}]}},
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.invocations, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('Agent: the agent', out)
        self.assertIn('agent findings', out)
        self.assertNotIn('file contents', out, 'Read result must not be paired to the Agent')


class StatsTokenSourceTest(unittest.TestCase):
    """§7 is TRANSCRIPT-NATIVE after ENG-1: token volume from message.usage via _usage_tokens /
    _usage_by_model — NO ccusage, NO $ cost. The pricing helpers (_price_for / _estimate_cost /
    _opus_is_legacy) were deleted; their ABSENCE is part of the v2 contract."""

    def test_pricing_helpers_and_ccusage_removed(self):
        # ENG-1 deleted the manual pricing fallback + the ccusage subprocess — guard against revival.
        for name in ('_price_for', '_estimate_cost', '_opus_is_legacy'):
            self.assertFalse(hasattr(extract, name), f'{name} should be gone after ENG-1')
        with open(os.path.join(os.path.dirname(extract.__file__), 'extract.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('subprocess.run', src, 'the ccusage subprocess call must be gone')

    def test_usage_by_model_partitions_and_sums_to_total(self):
        # Load-bearing invariant §7 relies on: sum over models == _usage_tokens total (same dedup).
        lines = [asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10, cc=5, cr=1),
                 asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10, cc=5, cr=1),  # dup id, same usage
                 asst_usage_line('m_2', 'claude-sonnet-4-6', inp=40, out=4, cc=0, cr=2)]
        tpath = write_transcript(lines)
        try:
            by_model = extract._usage_by_model(tpath)
            tok = extract._usage_tokens(tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(by_model['claude-opus-4-8'], 116, 'm_1 counted once (max per id): 100+10+5+1')
        self.assertEqual(by_model['claude-sonnet-4-6'], 46)
        self.assertEqual(sum(by_model.values()), tok['total'], 'per-model sum MUST equal the grand total')

    def test_usage_by_model_first_seen_order_and_drops_zero(self):
        lines = [asst_usage_line('m_1', 'claude-sonnet-4-6', inp=5),
                 asst_usage_line('m_2', 'claude-opus-4-8', inp=9),
                 asst_usage_line('m_3', 'claude-haiku-4-5', inp=0)]  # zero-token model -> dropped
        tpath = write_transcript(lines)
        try:
            by_model = extract._usage_by_model(tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(list(by_model), ['claude-sonnet-4-6', 'claude-opus-4-8'],
                         'first-seen order preserved; zero-token model dropped')

    def test_usage_tokens_honors_sub_window_bounds(self):
        # ENG-1 added start_ts/end_ts so `stats` can bound the token table to a sub-window.
        def asst(ts, inp):
            return {'type': 'assistant', 'timestamp': ts,
                    'message': {'id': ts, 'model': 'claude-opus-4-8',
                                'usage': {'input_tokens': inp, 'output_tokens': 0,
                                          'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}}
        lines = [asst('2026-06-22T01:00:00Z', 100), asst('2026-06-22T05:00:00Z', 7)]
        tpath = write_transcript(lines)
        try:
            full = extract._usage_tokens(tpath)
            windowed = extract._usage_tokens(tpath, '2026-06-22T03:00:00Z', None)
        finally:
            os.remove(tpath)
        self.assertEqual(full['input'], 107)
        self.assertEqual(windowed['input'], 7, 'only the post-03:00 line is in-window')

    def test_stats_output_is_transcript_native_no_cost(self):
        # The §7 body: window + token table + Models line, and crucially NO ccusage/$ artifacts.
        lines = [asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10, cc=5, cr=1)]
        tpath = write_transcript(lines)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                extract.stats(tpath)
        finally:
            os.remove(tpath)
        body = out.getvalue()
        self.assertIn('**Window:**', body)
        self.assertIn('| **Total** | **116** |', body)
        self.assertIn('**Models (main-thread):** opus-4-8 (116)', body)
        for forbidden in ('$', 'ccusage', 'Estimated cost', 'blended'):
            self.assertNotIn(forbidden, body, f'{forbidden!r} must not appear in the transcript-native §7')

    def test_stats_tool_breakdown_reconciles_when_truncated(self):
        """>8 distinct main-thread tools: the top-8 breakdown carries a labeled '+N more ×M'
        remainder so the listed counts reconcile with the stated total (no silent sum-to-less)."""
        counts = {'Edit': 9, 'Read': 8, 'Bash': 7, 'Write': 6, 'Glob': 5,
                  'Grep': 4, 'Task': 3, 'PowerShell': 2, 'WebFetch': 1, 'NotebookEdit': 1}
        blocks, i = [], 0
        for name, c in counts.items():
            for _ in range(c):
                blocks.append((f't{i}', name)); i += 1
        tpath = write_transcript([asst_usage_line('m_1', 'claude-opus-4-8', inp=10, tools=blocks)])
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                extract.stats(tpath)
        finally:
            os.remove(tpath)
        body = out.getvalue()
        self.assertIn(f'**Tool calls (main-thread):** {sum(counts.values())} total', body)  # 46 total
        # 10 distinct tools -> 8 shown + a labeled remainder covering the 2 one-call tools
        self.assertIn('+2 more ×2', body)

    def test_stats_output_includes_subagent_aggregate(self):
        """stats() emits the **Subagent tokens:** aggregate line when a sidecar dir is present.
        Pins the a['tokens'] key in _collect_subagents and the exact format string so a
        future rename or format change fails loudly."""
        import shutil
        d = tempfile.mkdtemp()
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m_main', 'claude-opus-4-8', inp=100, out=10)])
            side = os.path.join(d, 'sess', 'subagents')
            os.makedirs(side)
            with open(os.path.join(side, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Explore'}, f)
            # One assistant usage line: inp=50 + out=5 = 55 total tokens.
            _write_jsonl(os.path.join(side, 'agent-0.jsonl'),
                         [asst_usage_line('a0', 'claude-sonnet-4-6', inp=50, out=5)])
            buf = io.StringIO()
            with redirect_stdout(buf):
                extract.stats(main)
            out = buf.getvalue()
            # Exact format string from stats():
            # f'**Subagent tokens:** {sub_total:,} across {n_agents} agent(s) — per-agent breakdown in §2(c).'
            self.assertIn('**Subagent tokens:** 55 across 1 agent(s)', out,
                          'subagent aggregate line must appear with correct token count and phrasing')
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Transcript-shape gates (ports of the 2026-06-22 cc-user-prompts / cc-dashboard
# parsing fixes): isCompactSummary, isSidechain, entrypoint:sdk-cli, bash-output
# echoes, <command-message>-first ordering, AskUserQuestion answers, autopilot goals,
# and the promptSource guardrail. All keyed on real on-disk shapes verified against
# live transcripts.
# ---------------------------------------------------------------------------
def ask_answer_line(answers, ts='2026-06-08T00:00:00Z'):
    """A type:user AskUserQuestion answer line: rides a toolUseResult(answers, questions) and a
    tool_result content block (verified shape: toolUseResult keys answers/questions/annotations)."""
    return {'type': 'user', 'timestamp': ts,
            'toolUseResult': {'answers': answers, 'questions': [{'question': q} for q in answers]},
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'tu_ask', 'content': 'answered'}]}}


def goal_line(condition, met=False, ts='2026-06-08T00:00:00Z'):
    """A type:attachment autopilot goal line (attachment.type==goal_status; text in .condition)."""
    return {'type': 'attachment', 'timestamp': ts,
            'attachment': {'type': 'goal_status', 'condition': condition, 'met': met, 'sentinel': 'x'}}


class PromptShapeGatesTest(unittest.TestCase):
    def _counts(self, lines):
        tpath = write_transcript(lines)
        try:
            return extract._count_tool_calls(tpath)  # (tools, user_prompts, assistant_responses)
        finally:
            os.remove(tpath)

    def _msgs(self, lines):
        tpath = write_transcript(lines)
        try:
            return capture(extract.user_msgs, tpath)
        finally:
            os.remove(tpath)

    def test_compact_summary_recap_is_not_a_prompt(self):
        # The post-compaction "This session is being continued…" recap is injected as a plain
        # type:user line carrying isCompactSummary:true (NOT isMeta) — slips every other gate.
        lines = [user_line('a real prompt'),
                 user_line('This session is being continued from a previous conversation…',
                           isCompactSummary=True)]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'isCompactSummary recap is not a user turn')
        self.assertNotIn('being continued', self._msgs(lines), 'recap must not be emitted')

    def test_sidechain_replay_is_not_a_prompt(self):
        lines = [user_line('a real prompt'),
                 user_line('replayed subagent dispatch prompt', isSidechain=True)]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'isSidechain replay is not a human turn')

    def test_sdk_cli_eval_probe_is_not_a_prompt(self):
        lines = [user_line('a real prompt'),
                 user_line('GREEN eval probe input', entrypoint='sdk-cli')]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'entrypoint=sdk-cli headless probe is not a human turn')

    def test_bash_output_echo_excluded_but_input_kept(self):
        # !-mode shell OUTPUT (<bash-stdout>/<bash-stderr>) is not a turn; the <bash-input> the
        # user TYPED is a real action and must count.
        lines = [user_line('<bash-stdout>npm test -> 12 passing</bash-stdout>'),
                 user_line('<bash-stderr>warning: deprecated api</bash-stderr>'),
                 user_line('<bash-input>npm test</bash-input>')]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'only the typed <bash-input> counts')

    def test_slash_echo_counts_as_turn_and_renders_clean(self):
        # ENG-5: a /slash echo IS a user turn — both SSoT parsers (cc-user-prompts _slash,
        # cc-dashboard is_real_user_turn) count every slash echo, so we must too. It's rendered to a
        # clean `/name args` (raw <command-*> wrappers, often trailed by a stdout dump, never leak).
        # The <command-message>-first ordering (client-dependent) resolves the same way.
        lines = [
            user_line('<command-name>/compact</command-name>'),                  # bare, pre-slashed
            user_line('<command-name>code-review</command-name>'
                      '<command-args>ultra 42</command-args>'),                   # name + args
            user_line('<command-message>code-review</command-message>'
                      '<command-name>code-review</command-name>'),               # message-first
        ]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 3, 'every slash echo counts as a turn (SSoT parity)')
        out = self._msgs(lines)
        self.assertIn('/compact', out)
        self.assertIn('/code-review ultra 42', out)        # name+args, exactly one leading slash
        self.assertIn('slash-command', out)                # tagged with the marker
        self.assertNotIn('<command-name>', out, 'raw wrapper must not leak into the display')

    def test_prose_mentioning_command_tag_is_not_a_slash_echo(self):
        # The slash classifier keys on a LEADING wrapper — a prose turn that merely mentions the tag
        # mid-sentence is an ordinary turn (counted, emitted verbatim, no slash marker).
        lines = [user_line('please document the <command-name> echo shape')]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1)
        out = self._msgs(lines)
        self.assertIn('please document the', out)
        self.assertNotIn('slash-command', out)

    def test_ask_answer_counts_and_is_formatted(self):
        lines = [ask_answer_line({'Where should it live?': 'global skill'})]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'a non-empty AskUserQuestion answer IS a real turn')
        out = self._msgs(lines)
        self.assertIn('AskUserQuestion answer', out)
        self.assertIn('Where should it live?', out)
        self.assertIn('global skill', out)

    def test_cancelled_ask_answer_is_not_a_prompt(self):
        lines = [ask_answer_line({})]   # cancelled dialog -> empty answers dict
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 0, 'a cancelled AskUserQuestion (answers=={}) is not a turn')

    def test_autopilot_goal_surfaced_deduped_not_counted(self):
        # The goal is logged as met=false / met=true bookends with the same condition. It is shown
        # ONCE in user_msgs (tagged) but NOT counted as a prompt (it is a condition, not a turn).
        cond = 'goals are met when all 4 items ship'
        lines = [user_line('start the work'), goal_line(cond, met=False), goal_line(cond, met=True)]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'goal bookends are not counted; only the real turn is')
        out = self._msgs(lines)
        self.assertEqual(out.count(cond), 1, 'the goal condition is emitted exactly once (deduped)')
        self.assertIn('autopilot goal', out)

    def test_promptsource_sdk_is_still_a_real_turn(self):
        # GUARDRAIL: promptSource is a transport channel, NOT a human-vs-machine signal. A genuine
        # VS Code (promptSource=sdk) turn must still count — gating on it hid ~790 real turns.
        lines = [user_line('a real VS Code prompt', promptSource='sdk')]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 1, 'promptSource=sdk must NOT exclude a genuine turn')

    def test_auto_continuation_goal_echo_excluded(self):
        # A goal-set produces TWO lines: the goal_status (surfaced once as the autopilot goal) AND a
        # synthetic queued_command/prompt echo "Goal set: …" with origin.kind=='auto-continuation'.
        # The echo is harness-generated, not user-typed — it must NOT emit as a (mid-turn steer) nor
        # count. Genuine steers (origin.kind 'human' or absent) still count.
        cond = 'goals are met when all 4 items ship'
        echo = {'type': 'attachment', 'timestamp': '2026-06-08T00:00:01Z',
                'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                               'origin': {'kind': 'auto-continuation'}, 'prompt': f'Goal set: {cond}'}}
        human = {'type': 'attachment', 'timestamp': '2026-06-08T00:00:02Z',
                 'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                                'origin': {'kind': 'human'}, 'prompt': 'no, do X instead'}}
        lines = [user_line('start the work'), goal_line(cond, met=False), echo, human]
        _, prompts, _ = self._counts(lines)
        self.assertEqual(prompts, 2, 'real turn + genuine steer count; auto-continuation echo does not')
        out = self._msgs(lines)
        self.assertEqual(out.count('Goal set:'), 0, 'the synthetic goal echo is not emitted as a steer')
        self.assertIn('no, do X instead', out)
        self.assertIn('autopilot goal', out)


# ---------------------------------------------------------------------------
# Subagent & tooling parsing (§2 "Subagent & Tooling Evaluation"): sidecar walk,
# usage-token dedup-by-message.id, tool block-id dedup, MCP-server grouping.
# ---------------------------------------------------------------------------
def _write_jsonl(path, lines):
    with open(path, 'w', encoding='utf-8') as f:
        for d in lines:
            f.write(json.dumps(d) + '\n')


def asst_usage_line(mid, model, inp=0, out=0, cc=0, cr=0, tools=()):
    """An assistant line carrying message.usage (raw snake_case fields) + optional tool_use blocks.
    `tools` = list of (block_id, tool_name)."""
    return {'type': 'assistant', 'timestamp': '2026-06-22T00:00:00Z',
            'message': {'id': mid, 'model': model,
                'usage': {'input_tokens': inp, 'output_tokens': out,
                          'cache_creation_input_tokens': cc, 'cache_read_input_tokens': cr},
                'content': [{'type': 'tool_use', 'id': tid, 'name': tn, 'input': {}} for tid, tn in tools]}}


class SubagentToolingTest(unittest.TestCase):
    def test_split_mcp(self):
        self.assertEqual(extract._split_mcp('mcp__claude_design__render_preview'),
                         ('claude_design', 'render_preview'))
        self.assertEqual(extract._split_mcp('Read'), (None, 'Read'))
        self.assertEqual(extract._split_mcp('mcp__serveronly'), ('serveronly', ''))

    def test_short_model(self):
        self.assertEqual(extract._short_model('claude-opus-4-8[1m]'), 'opus-4-8')
        self.assertEqual(extract._short_model('claude-sonnet-4-6'), 'sonnet-4-6')
        self.assertEqual(extract._short_model('?'), '?')

    def test_usage_tokens_dedup_by_message_id(self):
        # 3 lines repeat the SAME usage under one message.id (content-block fan-out) -> counted ONCE
        # via max, not summed 3x; a second message.id is a distinct response and adds.
        lines = [asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10),
                 asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10),
                 asst_usage_line('m_1', 'claude-opus-4-8', inp=100, out=10),
                 asst_usage_line('m_2', 'claude-opus-4-8', inp=50, out=5)]
        tpath = write_transcript(lines)
        try:
            tok = extract._usage_tokens(tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(tok['input'], 150, 'm_1 counted once (max per id), not 3x')
        self.assertEqual(tok['output'], 15)
        self.assertEqual(tok['total'], 165)

    def test_tool_histogram_block_id_dedup(self):
        # The same tool_use block id re-logged counts once; distinct ids each count.
        lines = [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'toolu_a', 'name': 'Read', 'input': {}},
                {'type': 'tool_use', 'id': 'toolu_b', 'name': 'Edit', 'input': {}}]}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'toolu_a', 'name': 'Read', 'input': {}}]}},
        ]
        tpath = write_transcript(lines)
        try:
            h = extract._tool_histogram(tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(h['Read'], 1, 'duplicate block id counted once')
        self.assertEqual(h['Edit'], 1)

    def test_subagents_roster_split_and_journal_ignored(self):
        import shutil
        d = tempfile.mkdtemp()
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m_main', 'claude-opus-4-8', inp=1000, out=100,
                                                tools=[('toolu_m', 'Read')])])
            side = os.path.join(d, 'sess', 'subagents', 'workflows', 'wf_1')
            os.makedirs(side)
            with open(os.path.join(side, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Explore'}, f)
            _write_jsonl(os.path.join(side, 'agent-0.jsonl'), [
                asst_usage_line('a0', 'claude-sonnet-4-6', inp=200, out=100,
                                tools=[('toolu_x', 'Grep'), ('toolu_y', 'Read')])])
            _write_jsonl(os.path.join(side, 'journal.jsonl'), [{'type': 'log'}])  # not an agent
            out = capture(extract.subagents, main)
            self.assertIn('Subagents dispatched:** 1', out)
            self.assertIn('1× Explore', out)
            self.assertIn('sonnet-4-6', out)
            self.assertIn('main 1,100 + subagents 300 = 1,400', out)
            tb = capture(extract.tool_breakdown, main)
            self.assertIn('1 main + 2 across subagents', tb)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_subagents_none_when_no_sidecar(self):
        tpath = write_transcript([asst_usage_line('m', 'claude-opus-4-8', inp=1)])
        try:
            out = capture(extract.subagents, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('No subagents dispatched', out)

    def test_mcp_table_in_tool_breakdown(self):
        lines = [{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 't1', 'name': 'mcp__claude_design__render_preview', 'input': {}},
            {'type': 'tool_use', 'id': 't2', 'name': 'mcp__claude_design__list_files', 'input': {}},
            {'type': 'tool_use', 'id': 't3', 'name': 'Read', 'input': {}}]}}]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.tool_breakdown, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('MCP servers used', out)
        self.assertIn('| claude_design | 2 | 2 |', out)

    def test_usage_tokens_keyless_lines_summed(self):
        # Assistant lines with no message.id can't be deduped, so they are SUMMED (not maxed).
        lines = [asst_usage_line(None, 'claude-opus-4-8', inp=100, out=10),
                 asst_usage_line(None, 'claude-opus-4-8', inp=50, out=5)]
        tpath = write_transcript(lines)
        try:
            tok = extract._usage_tokens(tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(tok['input'], 150, 'keyless lines summed (no id to dedup on)')
        self.assertEqual(tok['total'], 165)

    def test_oversized_meta_falls_back_to_agent_type(self):
        # A meta.json over the 64 KB cap is NOT parsed; agent_type falls back to 'agent' (parse.rs
        # parity) — guards a future off-by-one / wrong-constant regression on the cap.
        import shutil
        d = tempfile.mkdtemp()
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m', 'claude-opus-4-8', inp=1)])
            side = os.path.join(d, 'sess', 'subagents')
            os.makedirs(side)
            with open(os.path.join(side, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                f.write('{"agentType": "Explore"' + ' ' * 70000 + '}')   # valid JSON, > 64 KB
            _write_jsonl(os.path.join(side, 'agent-0.jsonl'),
                         [asst_usage_line('a0', 'claude-sonnet-4-6', inp=10)])
            out = capture(extract.subagents, main)
            self.assertIn('1× agent', out, 'oversized meta -> agentType not read; fallback to agent')
            self.assertNotIn('Explore', out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # --- ENG-3: forked/resumed-UUID sidecar merge ---

    def test_session_uuid_chain_collects_fork_uuids(self):
        # Filename UUID is always chain[0]; a distinct canonical sessionId on a replayed line is
        # appended; the bridge-session `cse_*` id and a repeat of the filename UUID are ignored.
        import shutil
        ua = '11111111-1111-4111-8111-111111111111'
        ub = '22222222-2222-4222-8222-222222222222'
        lines = [
            {'type': 'assistant', 'sessionId': ua, 'message': {'id': 'm', 'content': []}},
            {'type': 'user', 'sessionId': ub, 'message': {'content': [{'type': 'text', 'text': 'x'}]}},
            {'type': 'bridge-session', 'sessionId': ua, 'bridgeSessionId': 'cse_01ABC'},  # cse id ignored
        ]
        d = tempfile.mkdtemp()
        tpath = os.path.join(d, ua + '.jsonl')
        _write_jsonl(tpath, lines)
        try:
            self.assertEqual(extract._session_uuid_chain(tpath), [ua, ub])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_session_uuid_chain_single_when_no_fork(self):
        # No sessionId fork (the current CC build) -> chain is just the filename UUID; merge is a no-op.
        import shutil
        ua = '11111111-1111-4111-8111-111111111111'
        d = tempfile.mkdtemp()
        tpath = os.path.join(d, ua + '.jsonl')
        _write_jsonl(tpath, [asst_usage_line('m', 'claude-opus-4-8', inp=1)])
        try:
            self.assertEqual(extract._session_uuid_chain(tpath), [ua])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_subagents_and_tools_merge_forked_uuid_sidecars(self):
        # A forked/resumed UUID splits sidecars across <uuidA>/subagents + <uuidB>/subagents, linked
        # to ONE transcript by sessionId. Both must merge into the roster, token split, and tool count.
        import shutil
        d = tempfile.mkdtemp()
        try:
            ua = '11111111-1111-4111-8111-111111111111'
            ub = '22222222-2222-4222-8222-222222222222'
            main = os.path.join(d, ua + '.jsonl')   # filename UUID = uuidA
            # main thread (sessionId uuidA) + one replayed historical line carrying the OLD uuidB
            _write_jsonl(main, [
                {**asst_usage_line('m_main', 'claude-opus-4-8', inp=1000, out=100,
                                   tools=[('toolu_m', 'Read')]), 'sessionId': ua},
                {'type': 'user', 'sessionId': ub,
                 'message': {'content': [{'type': 'text', 'text': 'replayed'}]}},
            ])
            # sidecar dir A (current UUID)
            sa = os.path.join(d, ua, 'subagents')
            os.makedirs(sa)
            with open(os.path.join(sa, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Explore'}, f)
            _write_jsonl(os.path.join(sa, 'agent-0.jsonl'), [
                asst_usage_line('a0', 'claude-sonnet-4-6', inp=200, out=100,
                                tools=[('toolu_x', 'Grep')])])
            # sidecar dir B (forked/prior UUID) — must ALSO be merged
            sb = os.path.join(d, ub, 'subagents')
            os.makedirs(sb)
            with open(os.path.join(sb, 'agent-1.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'code-review'}, f)
            _write_jsonl(os.path.join(sb, 'agent-1.jsonl'), [
                asst_usage_line('a1', 'claude-haiku-4-5', inp=50, out=25,
                                tools=[('toolu_y', 'Read')])])
            out = capture(extract.subagents, main)
            self.assertIn('Subagents dispatched:** 2', out)
            self.assertIn('Explore', out)
            self.assertIn('code-review', out)
            # token split merges both sidecars: main 1,100 + (300 + 75) = 1,475
            self.assertIn('main 1,100 + subagents 375 = 1,475', out)
            self.assertIn('resume-chain UUIDs', out)
            self.assertIn(ub, out)   # the forked UUID is surfaced in the merge note
            # tool breakdown merges both: main Read(1) + sub Grep(1) + Read(1) = 3
            tb = capture(extract.tool_breakdown, main)
            self.assertIn('1 main + 2 across subagents', tb)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # --- ENG-6: roster display cap (count stays exact) + reparse-point no-follow ---

    def test_subagents_roster_capped_at_12_count_exact(self):
        # 15 dispatched agents -> COUNT stays exact (15) but only the top-12-by-token rows render,
        # plus the "showing top 12 of 15" note. Distinct token spends make the top-12 cut deterministic.
        import shutil
        d = tempfile.mkdtemp()
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m', 'claude-opus-4-8', inp=500000)])
            side = os.path.join(d, 'sess', 'subagents')
            os.makedirs(side)
            for i in range(15):
                with open(os.path.join(side, f'agent-{i}.meta.json'), 'w', encoding='utf-8') as f:
                    json.dump({'agentType': 'Explore'}, f)
                _write_jsonl(os.path.join(side, f'agent-{i}.jsonl'),
                             [asst_usage_line(f'a{i}', 'claude-sonnet-4-6', inp=(i + 1) * 1000)])
            out = capture(extract.subagents, main)
            self.assertIn('Subagents dispatched:** 15', out)         # dispatched COUNT exact
            self.assertEqual(out.count('| Explore | '), 12, 'only 12 roster rows render')
            self.assertIn('showing top 12 of 15', out)
            self.assertIn('| 15,000 |', out, 'highest-spend agent is shown')
            self.assertNotIn('| 1,000 |', out, 'the 3 lowest-spend agents are dropped below the cap')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reparse_point_dir_not_followed(self):
        # A symlinked dir inside subagents/ must NOT be descended — its planted agent stays invisible.
        # Symlink creation needs privilege on Windows; skip (don't fail) when it can't be created.
        import shutil
        d = tempfile.mkdtemp()
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m', 'claude-opus-4-8', inp=1000)])
            side = os.path.join(d, 'sess', 'subagents')
            os.makedirs(side)
            with open(os.path.join(side, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Good'}, f)
            _write_jsonl(os.path.join(side, 'agent-0.jsonl'),
                         [asst_usage_line('a0', 'claude-sonnet-4-6', inp=50)])
            # A planted agent OUTSIDE the tree, reachable only by following a symlinked dir.
            outside = os.path.join(d, 'outside')
            os.makedirs(outside)
            with open(os.path.join(outside, 'agent-9.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Evil'}, f)
            _write_jsonl(os.path.join(outside, 'agent-9.jsonl'),
                         [asst_usage_line('a9', 'claude-opus-4-8', inp=99)])
            try:
                os.symlink(outside, os.path.join(side, 'link'), target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError) as e:
                self.skipTest(f'cannot create symlink (privilege?): {e}')
            self.assertTrue(extract._is_reparse_point(os.path.join(side, 'link')),
                            'the link is detected as a reparse point')
            out = capture(extract.subagents, main)
            self.assertIn('1× Good', out)
            self.assertIn('Subagents dispatched:** 1', out)
            self.assertNotIn('Evil', out, 'a symlinked dir must not be followed into')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_junction_dir_not_followed_windows(self):
        # Windows JUNCTION (mklink /J) needs NO privilege (unlike a symlink) and is the common reparse
        # vector os.path.islink() MISSES — the load-bearing case for _is_reparse_point on this OS, so
        # the no-follow guard gets real green coverage here even when the symlink test self-skips.
        import shutil, subprocess
        import sys as _sys
        if _sys.platform != 'win32':
            self.skipTest('junction (mklink /J) is Windows-only')
        d = tempfile.mkdtemp()
        link = os.path.join(d, 'sess', 'subagents', 'jlink')
        try:
            main = os.path.join(d, 'sess.jsonl')
            _write_jsonl(main, [asst_usage_line('m', 'claude-opus-4-8', inp=1000)])
            side = os.path.join(d, 'sess', 'subagents')
            os.makedirs(side)
            with open(os.path.join(side, 'agent-0.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Good'}, f)
            _write_jsonl(os.path.join(side, 'agent-0.jsonl'),
                         [asst_usage_line('a0', 'claude-sonnet-4-6', inp=50)])
            # A planted agent OUTSIDE the tree, reachable only by descending a junction.
            outside = os.path.join(d, 'outside')
            os.makedirs(outside)
            with open(os.path.join(outside, 'agent-9.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': 'Evil'}, f)
            _write_jsonl(os.path.join(outside, 'agent-9.jsonl'),
                         [asst_usage_line('a9', 'claude-opus-4-8', inp=99)])
            r = subprocess.run(['cmd', '/c', 'mklink', '/J', link, outside],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.isdir(link):
                self.skipTest(f'mklink /J unavailable: {(r.stderr or r.stdout).strip()}')
            self.assertTrue(extract._is_reparse_point(link), 'a junction is a reparse point')
            out = capture(extract.subagents, main)
            self.assertIn('1× Good', out)
            self.assertIn('Subagents dispatched:** 1', out)
            self.assertNotIn('Evil', out, 'a junction must not be followed into')
        finally:
            # Remove the junction itself (os.rmdir on a junction unlinks it, not its target) before
            # rmtree, so cleanup can't traverse it into `outside`.
            try:
                if os.path.isdir(link):
                    os.rmdir(link)
            except OSError:
                pass
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Format-drift canary (ENG-2) — ports of cc-dashboard core/drift.rs's drift tests,
# adapted to the parsed-dict observer + compact-check dual-marker detection.
# ---------------------------------------------------------------------------
class DriftCanaryTest(unittest.TestCase):
    """`_collect_drift` returns the {(kind, value): count} accumulator so assertions read like
    drift.rs's find()."""

    def _drift(self, lines):
        tpath = write_transcript(lines)
        try:
            return extract._collect_drift(tpath)
        finally:
            os.remove(tpath)

    def test_known_shapes_produce_no_drift(self):
        # Every shape here is in a registry — the canary must stay silent.
        lines = [
            user_line('hello'),
            {'type': 'assistant', 'timestamp': '2026-06-21T00:00:01Z',
             'message': {'id': 'm', 'model': 'claude-opus-4-8', 'usage': {'output_tokens': 1}}},
            {'type': 'system', 'subtype': 'turn_duration', 'timestamp': '2026-06-21T00:00:02Z'},
            attachment_line('queued_command', 'prompt', body='steer'),
            user_line('<system-reminder>note</system-reminder>', promptSource='sdk'),
        ]
        self.assertEqual(self._drift(lines), {}, 'every known shape is recognized — no drift')

    def test_novel_line_type_surfaces(self):
        self.assertEqual(self._drift([{'type': 'telepathy', 'timestamp': 'x'}]).get(('line_type', 'telepathy')), 1)

    def test_novel_system_subtype_surfaces(self):
        d = self._drift([{'type': 'system', 'subtype': 'quantum_flush', 'timestamp': 'x'}])
        self.assertEqual(d.get(('system_subtype', 'quantum_flush')), 1)

    def test_scheduled_task_fire_is_known(self):
        # Characterized live 2026-06-27 (the /loop scheduled-wakeup resume notice) and added to the
        # registry — it must now be SILENT, while an unknown sibling subtype still drifts.
        self.assertEqual(self._drift([{'type': 'system', 'subtype': 'scheduled_task_fire', 'timestamp': 'x'}]), {})
        d = self._drift([{'type': 'system', 'subtype': 'scheduled_task_future', 'timestamp': 'x'}])
        self.assertEqual(d.get(('system_subtype', 'scheduled_task_future')), 1)

    def test_pr_link_is_known(self):
        # Characterized live 2026-07-07 (gh/PR sidecar metadata; deliberately no uuid/parentUuid,
        # mirroring production) and added to the registry — must now be SILENT, while an invented
        # sibling line type still drifts.
        self.assertEqual(self._drift([{'type': 'pr-link', 'prNumber': 11,
                                        'prRepository': 'example/repo',
                                        'prUrl': 'https://github.com/example/repo/pull/11',
                                        'sessionId': 's1', 'timestamp': 'x'}]), {})
        d = self._drift([{'type': 'pr-link-v2', 'prNumber': 11, 'timestamp': 'x'}])
        self.assertEqual(d.get(('line_type', 'pr-link-v2')), 1)

    def test_model_refusal_no_fallback_is_known(self):
        # Characterized live 2026-07-07 (no-fallback sibling of model_refusal_fallback) and added
        # to the registry — must now be SILENT, while an invented sibling subtype still drifts.
        self.assertEqual(self._drift([{'type': 'system', 'subtype': 'model_refusal_no_fallback',
                                        'level': 'warning', 'apiRefusalCategory': 'policy',
                                        'apiRefusalExplanation': 'x', 'originalModel': 'claude-opus-4-8',
                                        'refusedUserMessageUuid': 'u1', 'content': '',
                                        'timestamp': 'x'}]), {})
        d = self._drift([{'type': 'system', 'subtype': 'model_refusal_no_retry', 'timestamp': 'x'}])
        self.assertEqual(d.get(('system_subtype', 'model_refusal_no_retry')), 1)

    def test_plan_file_reference_is_known(self):
        # Characterized live 2026-07-07 (post-compact plan-file re-injection, sibling of
        # compact_file_reference) and added to the registry — must now be SILENT, while an invented
        # sibling attachment type still drifts.
        self.assertEqual(self._drift([{'type': 'attachment',
                                        'attachment': {'type': 'plan_file_reference',
                                                       'planFilePath': 'C:/tmp/plan.md',
                                                       'planContent': 'plan text'}}]), {})
        d = self._drift([{'type': 'attachment', 'attachment': {'type': 'plan_file_reference_v2'}}])
        self.assertEqual(d.get(('attachment_type', 'plan_file_reference_v2')), 1)

    def test_hook_non_blocking_error_is_known(self):
        # Characterized live 2026-07-07 (non-blocking sibling of hook_blocking_error, same tool-hook
        # envelope) and added to the registry — must now be SILENT, while an invented sibling
        # attachment type still drifts.
        self.assertEqual(self._drift([{'type': 'attachment',
                                        'attachment': {'type': 'hook_non_blocking_error',
                                                       'hookName': 'lint', 'hookEvent': 'PostToolUse',
                                                       'exitCode': 1, 'toolUseID': 'tu1',
                                                       'command': 'lint.sh', 'stderr': 'warn',
                                                       'stdout': '', 'durationMs': 12}}]), {})
        d = self._drift([{'type': 'attachment', 'attachment': {'type': 'hook_non_blocking_warning'}}])
        self.assertEqual(d.get(('attachment_type', 'hook_non_blocking_warning')), 1)

    def test_novel_attachment_type_and_command_mode_surface(self):
        d = self._drift([
            {'type': 'attachment', 'attachment': {'type': 'holo_reminder'}},
            {'type': 'attachment', 'attachment': {'type': 'queued_command', 'commandMode': 'telepathic-steer'}},
        ])
        self.assertEqual(d.get(('attachment_type', 'holo_reminder')), 1)
        self.assertEqual(d.get(('command_mode', 'telepathic-steer')), 1)
        # queued_command itself is known — only the novel mode drifts, not the attachment type.
        self.assertNotIn(('attachment_type', 'queued_command'), d)

    def test_novel_prompt_source_and_content_tag_surface(self):
        d = self._drift([
            user_line('hi', promptSource='telepathy'),
            user_line('<bash-stdin>echo hi</bash-stdin>'),   # bash-stdin is NOT in the registry
        ])
        self.assertEqual(d.get(('prompt_source', 'telepathy')), 1)
        self.assertEqual(d.get(('user_content_tag', 'bash-stdin')), 1)

    def test_novel_origin_kind_surfaces(self):
        # The extract.py-unique axis drift.rs lacks: a novel origin.kind on a user line.
        d = self._drift([user_line('hi', origin={'kind': 'precognition'})])
        self.assertEqual(d.get(('origin_kind', 'precognition')), 1)
        # The known kinds stay silent.
        self.assertEqual(self._drift([user_line('hi', origin={'kind': 'human'})]), {})

    def test_attachment_nested_origin_kind_surfaces(self):
        # The OTHER origin.kind gate site: `_attachment_prompt` keys on att['origin']['kind'] (it
        # drops the 'auto-continuation' goal echo). A novel kind THERE must drift too — observing
        # only the top-level user origin would miss it (and leave 'auto-continuation' unreachable).
        d = self._drift([{'type': 'attachment',
                          'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                                         'origin': {'kind': 'auto-resumption'}}}])
        self.assertEqual(d.get(('origin_kind', 'auto-resumption')), 1)
        # The known attachment-only kind is now reachable AND silent.
        self.assertEqual(self._drift([{'type': 'attachment',
                          'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                                         'origin': {'kind': 'auto-continuation'}}}]), {})

    def test_novel_user_is_flag_surfaces_but_snapshot_flag_does_not(self):
        # A truthy is*-flag on a USER line outside KNOWN_USER_FLAGS drifts…
        d = self._drift([user_line('hi', isTelepathic=True)])
        self.assertEqual(d.get(('user_is_flag', 'isTelepathic')), 1)
        # …but isSnapshotUpdate rides file-history-snapshot lines, not user lines, so the user-scoped
        # axis never sees it (the live-tree false-positive this scoping was chosen to avoid).
        snap = self._drift([{'type': 'file-history-snapshot', 'isSnapshotUpdate': True,
                             'messageId': 'm', 'snapshot': {}}])
        self.assertEqual(snap, {}, 'file-history-snapshot isSnapshotUpdate is not user-line drift')

    def test_prose_opening_with_angle_bracket_is_not_a_tag(self):
        # '<= 5' / '<3' open with '<' but the next char isn't a letter — prose, not markup.
        d = self._drift([user_line('<= 5 should pass the gate'), user_line('<3 to the team')])
        self.assertFalse(any(k == 'user_content_tag' for k, _ in d), 'prose opening with < is not a tag')

    def test_tool_result_inner_content_tag_does_not_drift(self):
        # A user line carrying a tool_result block SKIPS the content-tag axis (mirrors drift.rs
        # is_tool_result_line), so neither the tool's '<result>…' output nor a co-resident text
        # block opening with an unknown tag mints spurious user_content_tag drift.
        d = self._drift([{'type': 'user', 'timestamp': 'x', 'toolUseResult': {'ok': 1},
                          'message': {'content': [
                              {'type': 'tool_result', 'tool_use_id': 'tu', 'content': '<result>data</result>'},
                              {'type': 'text', 'text': '<novel-tag>x</novel-tag>'}]}}])
        self.assertNotIn(('user_content_tag', 'novel-tag'), d)
        self.assertNotIn(('user_content_tag', 'result'), d)

    def test_counts_accumulate_across_lines(self):
        novel = {'type': 'telepathy', 'timestamp': 'x'}
        self.assertEqual(self._drift([novel, novel, novel]).get(('line_type', 'telepathy')), 3)

    def test_cardinality_cap_is_fail_closed(self):
        # > MAX_DISTINCT distinct novel line types: only the first MAX_DISTINCT are admitted; the
        # overflow is dropped (fail-closed). An already-seen pair still counts past the cap.
        many = [{'type': f'novel-{i}', 'timestamp': 'x'} for i in range(extract.MAX_DISTINCT + 10)]
        acc = self._drift(many)
        self.assertEqual(len(acc), extract.MAX_DISTINCT, 'accumulator is bounded at MAX_DISTINCT')
        acc2 = self._drift(many + [{'type': 'novel-0', 'timestamp': 'x'}])
        self.assertEqual(acc2[('line_type', 'novel-0')], 2, 'an already-seen pair counts past the cap')

    def test_drift_canary_knows_every_censused_production_shape(self):
        # THE TRIPWIRE (port of drift.rs drift_canary_knows_every_censused_production_shape): every
        # shape the real-tree census enumerated must be KNOWN, so the canary stays quiet on real
        # data (a missing entry = false drift). Extend the registry, never delete.
        for t in ['user', 'assistant', 'attachment', 'last-prompt', 'ai-title',
                  'file-history-snapshot', 'permission-mode', 'queue-operation', 'mode', 'started',
                  'result', 'system', 'agent-name', 'bridge-session', 'worktree-state',
                  'custom-title', 'summary', 'pr-link', 'file-history-delta']:
            self.assertIn(t, extract.KNOWN_LINE_TYPES, f'censused line type {t} must be known')
        for s in ['turn_duration', 'away_summary', 'local_command', 'api_error', 'compact_boundary',
                  'bridge_status', 'stop_hook_summary', 'model_refusal_fallback', 'informational',
                  'scheduled_task_fire', 'model_refusal_no_fallback']:
            self.assertIn(s, extract.KNOWN_SYSTEM_SUBTYPES, f'censused system subtype {s} must be known')
        for a in ['skill_listing', 'deferred_tools_delta', 'todo_reminder', 'hook_additional_context',
                  'task_reminder', 'hook_success', 'total_tokens_reminder', 'command_permissions',
                  'queued_command', 'edited_text_file', 'nested_memory', 'file', 'plan_mode_exit',
                  'auto_mode', 'workflow_keyword_request', 'ultra_effort_enter', 'agent_listing_delta',
                  'compact_file_reference', 'plan_mode', 'opened_file_in_ide', 'directory',
                  'date_change', 'invoked_skills', 'ultrathink_effort', 'dynamic_skill',
                  'ultra_effort_exit', 'hook_blocking_error', 'goal_status', 'auto_mode_exit',
                  'hook_cancelled', 'already_read_file', 'plan_mode_reentry', 'selected_lines_in_ide',
                  'plan_file_reference', 'hook_non_blocking_error', 'hook_system_message',
                  'task_status', 'mcp_instructions_delta']:
            self.assertIn(a, extract.KNOWN_ATTACHMENT_TYPES, f'censused attachment type {a} must be known')
        # `mcp_instructions_delta` history: characterized PHANTOM 2026-07-07 (zero structural
        # occurrences then; an assertNotIn pinned the decision) → REAL 2026-07-21 (×3 structural
        # occurrences from MCP connect/disconnect churn, verified 2026-07-22) → registered, pin
        # flipped to the assertIn above. The phantom era is history, not a live rule.
        for m in ['prompt', 'task-notification']:
            self.assertIn(m, extract.KNOWN_COMMAND_MODES)
        for p in ['sdk', 'typed', 'system', 'queued']:
            self.assertIn(p, extract.KNOWN_PROMPT_SOURCES)
        for o in ['task-notification', 'auto-continuation', 'human']:
            self.assertIn(o, extract.KNOWN_ORIGIN_KINDS)
        for c in ['ide_opened_file', 'command-name', 'ide_selection', 'command-message', 'bash-input',
                  'bash-stdout', 'system-reminder', 'local-command-stdout', 'local-command-caveat']:
            self.assertIn(c, extract.KNOWN_USER_CONTENT_TAGS, f'censused content tag {c} must be known')
        for f in ['isSidechain', 'isMeta', 'isCompactSummary', 'isVisibleInTranscriptOnly']:
            self.assertIn(f, extract.KNOWN_USER_FLAGS)
        # The canary's reason to exist: an invented shape is UNKNOWN.
        self.assertNotIn('totally-new-line-type', extract.KNOWN_LINE_TYPES)
        self.assertNotIn('totally-new-attachment', extract.KNOWN_ATTACHMENT_TYPES)


class DriftSubcommandTest(unittest.TestCase):
    """End-to-end `drift()` subcommand tests — redirect_stdout + assertRaises(SystemExit),
    mirroring CompactCheckTest._run. `drift()` always exits 0; the emitted rows (vs. 'no drift')
    are the diagnostic signal."""

    def _run(self, lines):
        tpath = write_transcript(lines)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    extract.drift(tpath)
        finally:
            os.remove(tpath)
        return cm.exception.code, buf.getvalue()

    def test_clean_transcript_prints_no_drift(self):
        # A transcript containing only known shapes → 'no drift' and exit 0.
        code, out = self._run([user_line('hello')])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), 'no drift')

    def test_novel_line_type_emits_tab_row(self):
        # A single unknown `type` emits one `kind<TAB>value<TAB>count` row and exits 0.
        # Pins the tab delimiter, column order, and the 'line_type' kind label.
        code, out = self._run([{'type': 'telepathy', 'timestamp': '2026-06-28T00:00:00Z'}])
        self.assertEqual(code, 0)
        rows = out.strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].split('\t'), ['line_type', 'telepathy', '1'])


class CompactCheckTest(unittest.TestCase):
    """compact_check fires on EITHER compaction shape and keeps exit-1/exit-0 semantics (ENG-2 B)."""

    def _run(self, lines):
        tpath = write_transcript(lines)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    extract.compact_check(tpath)
        finally:
            os.remove(tpath)
        return cm.exception.code, buf.getvalue()

    def test_compact_boundary_system_line_detected(self):
        # The newer structured marker: type==system / subtype==compact_boundary.
        code, out = self._run([user_line('hi'),
                               {'type': 'system', 'subtype': 'compact_boundary',
                                'timestamp': '2026-06-27T00:00:00Z'}])
        self.assertEqual(code, 1, 'compact_boundary system line is a compaction event')
        self.assertIn('compact_boundary', out)

    def test_compact_summary_flag_still_detected(self):
        # The original field-keyed marker still fires (no regression).
        code, out = self._run([user_line('recap', isCompactSummary=True)])
        self.assertEqual(code, 1)
        self.assertIn('isCompactSummary', out)

    def test_no_compaction_exits_zero(self):
        code, _ = self._run([user_line('hi'), user_line('there')])
        self.assertEqual(code, 0, 'a clean transcript exits 0')

    def test_compact_check_both_markers(self):
        # A transcript with BOTH an isCompactSummary user line AND a system/compact_boundary
        # line fires exit 1 and both labels appear in the output — pins the '; '.join
        # combined-marker path through compact_check().
        code, out = self._run([
            user_line('recap', isCompactSummary=True),
            {'type': 'system', 'subtype': 'compact_boundary',
             'timestamp': '2026-06-28T00:00:00Z'},
        ])
        self.assertEqual(code, 1)
        self.assertIn('isCompactSummary', out)
        self.assertIn('compact_boundary', out)


def hook_fire_line(content, ts='2026-07-10T00:00:00Z', hook_name='SomeHook', hook_event='PreToolUse'):
    """A `hook_system_message` attachment line — the per-fire visibility one-liner ballast hooks
    emit (empirically verified shape, see extract.py's `hook_fires` docstring)."""
    return {'type': 'attachment', 'timestamp': ts,
            'attachment': {'type': 'hook_system_message', 'content': content,
                           'hookName': hook_name, 'hookEvent': hook_event, 'toolUseID': 'tu_x'}}


class HookFiresTest(unittest.TestCase):
    def test_no_fires_prints_message(self):
        tpath = write_transcript([user_line('hello')])
        try:
            out = capture(extract.hook_fires, tpath)
        finally:
            os.remove(tpath)
        self.assertEqual(out.strip(), 'no hook fires recorded')

    def test_tallies_by_parsed_hook_name(self):
        # 2 fires of one hook + 1 of another -> correct per-hook counts + total.
        lines = [
            hook_fire_line('⚓ ballast: git-commit-guard — pre-commit reminder(s) injected'),
            hook_fire_line('⚓ ballast: git-commit-guard — pre-commit reminder(s) injected'),
            hook_fire_line('📐 ballast: doc-write-guard — durable-doc altitude reminder injected'),
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.hook_fires, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('git-commit-guard: 2', out)
        self.assertIn('doc-write-guard: 1', out)
        self.assertIn('**Total:** 3', out)

    def test_no_em_dash_falls_back_to_full_line(self):
        # 'ballast: principles loaded' has no em-dash-delimited name segment (real fire, verified
        # live) — D2's fallback: tally by the full content line rather than dropping/crashing.
        tpath = write_transcript([hook_fire_line('⚓ ballast: principles loaded')])
        try:
            out = capture(extract.hook_fires, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('ballast: principles loaded: 1', out)

    def test_plain_double_hyphen_separator_tallies_with_varying_clause(self):
        # E1: '--' is a legitimate name/clause separator alongside the em dash. Two fires of the
        # SAME hook with DIFFERENT clause tails must still tally as one name, not fragment into
        # two fallback keys keyed off the full (varying) line.
        lines = [
            hook_fire_line('⚓ ballast: dev-process-nudge -- 1 process already referencing this project'),
            hook_fire_line('⚓ ballast: dev-process-nudge -- 3 processes already referencing this project'),
        ]
        tpath = write_transcript(lines)
        try:
            out = capture(extract.hook_fires, tpath)
        finally:
            os.remove(tpath)
        self.assertIn('dev-process-nudge: 2', out)
        self.assertIn('**Total:** 2', out)


def _bundle_fixture_lines():
    """A small, self-describing transcript: 4 lines, 2 real user turns, no compaction markers —
    the known ground truth `BundleTest` checks the manifest metrics against."""
    return [
        user_line('first message', ts='2026-07-01T00:00:00Z'),
        {'type': 'assistant', 'timestamp': '2026-07-01T00:00:01Z',
         'message': {'id': 'msg_1', 'model': 'claude-sonnet-5',
                     'content': [{'type': 'text', 'text': 'ok, on it'}]}},
        user_line('second message', ts='2026-07-01T00:00:02Z'),
        {'type': 'assistant', 'timestamp': '2026-07-01T00:00:03Z',
         'message': {'id': 'msg_2', 'model': 'claude-sonnet-5', 'content': [
             {'type': 'tool_use', 'name': 'Edit', 'id': 'tu_1', 'input': {'file_path': 'foo.py'}}]}},
    ]


class BundleTest(unittest.TestCase):
    """`bundle` happy path (D1): all 8 dumps written, manifest parses, statuses ok, metrics sane."""

    def test_bundle_happy_path_all_files_and_manifest(self):
        lines = _bundle_fixture_lines()
        tpath = write_transcript(lines)
        try:
            with tempfile.TemporaryDirectory() as outdir:
                bundle_out = os.path.join(outdir, 'bundle')
                buf = io.StringIO()
                with redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        extract.bundle(tpath, bundle_out)
                self.assertEqual(cm.exception.code, 0)

                manifest_path = os.path.join(bundle_out, 'manifest.json')
                self.assertTrue(os.path.isfile(manifest_path))
                with open(manifest_path, encoding='utf-8') as f:
                    manifest = json.load(f)

                self.assertEqual(manifest['schema'], 1)
                expected_names = ['user-msgs', 'invocations', 'edits', 'assistant-text',
                                   'subagents', 'tool-breakdown', 'stats', 'hook-fires']
                for name in expected_names:
                    self.assertIn(name, manifest['files'], f'{name}.md missing from manifest')
                    self.assertEqual(manifest['files'][name]['status'], 'ok')
                    fp = os.path.join(bundle_out, manifest['files'][name]['file'])
                    self.assertTrue(os.path.isfile(fp), f'{fp} was not written')

                m = manifest['metrics']
                self.assertEqual(m['transcript_lines'], len(lines))
                self.assertEqual(m['user_turns'], 2, 'two real user_line entries, no markers')
                self.assertFalse(m['compacted'])
                self.assertEqual(m['time_window'], ['2026-07-01T00:00:00Z', '2026-07-01T00:00:03Z'])

                # stdout contract: manifest path, one status line per dump, one metrics summary.
                out = buf.getvalue()
                self.assertIn(manifest_path, out)
                self.assertIn('user_turns=2', out)
                self.assertIn('compacted=no', out)
        finally:
            os.remove(tpath)


class BundleBoundedWriteTest(unittest.TestCase):
    """Bounded-write refusal (D1): a non-empty --out dir with no manifest.json is refused; a dir
    the tool already wrote (has manifest.json) is a re-run that succeeds in place."""

    def test_refuses_non_empty_dir_without_manifest(self):
        tpath = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'existing')
                os.makedirs(target)
                with open(os.path.join(target, 'stray.txt'), 'w', encoding='utf-8') as f:
                    f.write('junk')
                buf, errbuf = io.StringIO(), io.StringIO()
                with redirect_stdout(buf), redirect_stderr(errbuf):
                    with self.assertRaises(SystemExit) as cm:
                        extract.bundle(tpath, target)
                self.assertEqual(cm.exception.code, 2)
                self.assertEqual(sorted(os.listdir(target)), ['stray.txt'],
                                  'nothing written on refusal')
        finally:
            os.remove(tpath)

    def test_rerun_with_existing_manifest_succeeds(self):
        tpath = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                buf1 = io.StringIO()
                with redirect_stdout(buf1):
                    with self.assertRaises(SystemExit) as cm1:
                        extract.bundle(tpath, target)
                self.assertEqual(cm1.exception.code, 0)

                # Re-run into the same (now non-empty, manifest.json-bearing) dir succeeds.
                buf2 = io.StringIO()
                with redirect_stdout(buf2):
                    with self.assertRaises(SystemExit) as cm2:
                        extract.bundle(tpath, target)
                self.assertEqual(cm2.exception.code, 0)
        finally:
            os.remove(tpath)


class BundleDumpIsolationTest(unittest.TestCase):
    """Per-dump isolation (D1): one dump raising is recorded as an error and the rest still
    succeed; overall exit code reflects the failure."""

    def test_one_dump_failing_is_isolated(self):
        tpath = write_transcript(_bundle_fixture_lines())
        original = list(extract._BUNDLE_DUMPS)

        def _boom(path):
            raise RuntimeError('simulated dump failure')

        # Patch the shared _BUNDLE_DUMPS LIST in place (not extract.edits) — bundle() looks up
        # this module-level list by name at call time, but each entry already holds a direct
        # function reference captured when the list was built, so reassigning extract.edits alone
        # would not reach it.
        extract._BUNDLE_DUMPS[:] = [(name, _boom if name == 'edits' else fn)
                                     for name, fn in original]
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                buf, errbuf = io.StringIO(), io.StringIO()
                with redirect_stdout(buf), redirect_stderr(errbuf):
                    with self.assertRaises(SystemExit) as cm:
                        extract.bundle(tpath, target)
                self.assertEqual(cm.exception.code, 1, 'one failed dump -> exit 1')

                with open(os.path.join(target, 'manifest.json'), encoding='utf-8') as f:
                    manifest = json.load(f)
                self.assertEqual(manifest['files']['edits']['status'], 'error')
                self.assertIn('simulated dump failure', manifest['files']['edits']['error'])
                for name in ('user-msgs', 'invocations', 'assistant-text', 'subagents',
                             'tool-breakdown', 'stats', 'hook-fires'):
                    self.assertEqual(manifest['files'][name]['status'], 'ok',
                                      f'{name} should be unaffected by the edits failure')
        finally:
            extract._BUNDLE_DUMPS[:] = original
            os.remove(tpath)


class BundleMetricsFailureTest(unittest.TestCase):
    """E2: a metrics-block exception must not strand the dumps already written to disk with no
    manifest.json at all — the manifest still lands, with null metrics + a metrics_error key."""

    def test_metrics_failure_still_writes_manifest_with_null_metrics(self):
        tpath = write_transcript(_bundle_fixture_lines())
        original = extract._count_lines

        def _boom(path):
            raise RuntimeError('simulated metrics failure')

        # Patch the module-level function bundle() calls by bare name (mirrors how
        # BundleDumpIsolationTest patches _BUNDLE_DUMPS in place).
        extract._count_lines = _boom
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                buf, errbuf = io.StringIO(), io.StringIO()
                with redirect_stdout(buf), redirect_stderr(errbuf):
                    with self.assertRaises(SystemExit) as cm:
                        extract.bundle(tpath, target)
                self.assertEqual(cm.exception.code, 1, 'metrics failure -> exit 1')

                manifest_path = os.path.join(target, 'manifest.json')
                self.assertTrue(os.path.isfile(manifest_path),
                                 'manifest must still be written despite the metrics crash')
                with open(manifest_path, encoding='utf-8') as f:
                    manifest = json.load(f)
                self.assertIsNone(manifest['metrics']['transcript_lines'])
                self.assertIsNone(manifest['metrics']['user_turns'])
                self.assertIn('simulated metrics failure', manifest.get('metrics_error', ''))
                # Per-dump statuses are unaffected -- all 8 dumps still ran before the metrics block.
                for name in ('user-msgs', 'invocations', 'edits', 'assistant-text',
                             'subagents', 'tool-breakdown', 'stats', 'hook-fires'):
                    self.assertEqual(manifest['files'][name]['status'], 'ok')
                self.assertIn('simulated metrics failure', errbuf.getvalue())
        finally:
            extract._count_lines = original
            os.remove(tpath)


class BundleStaleDumpFileTest(unittest.TestCase):
    """E3: a re-run's failing dump must not leave the PREVIOUS run's file on disk — the invariant
    is status:"ok" iff <name>.md on disk is from THIS run."""

    def test_failing_dump_removes_stale_file_from_prior_run(self):
        tpath = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                # First run: everything succeeds; pre-seeds the bundle dir with a real manifest
                # AND a real edits.md (the "stale file" this test's second run must clean up).
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm1:
                        extract.bundle(tpath, target)
                self.assertEqual(cm1.exception.code, 0)
                edits_file = os.path.join(target, 'edits.md')
                self.assertTrue(os.path.isfile(edits_file), 'precondition: first run wrote edits.md')

                # Second run: force 'edits' to fail (patch _BUNDLE_DUMPS in place, as the existing
                # isolation test does) — the prior edits.md must not survive under an error status.
                original = list(extract._BUNDLE_DUMPS)

                def _boom(path):
                    raise RuntimeError('simulated dump failure')

                extract._BUNDLE_DUMPS[:] = [(name, _boom if name == 'edits' else fn)
                                             for name, fn in original]
                try:
                    buf2, errbuf2 = io.StringIO(), io.StringIO()
                    with redirect_stdout(buf2), redirect_stderr(errbuf2):
                        with self.assertRaises(SystemExit) as cm2:
                            extract.bundle(tpath, target)
                    self.assertEqual(cm2.exception.code, 1)
                    self.assertFalse(os.path.exists(edits_file),
                                      "prior run's stale edits.md must be removed on this run's failure")
                    with open(os.path.join(target, 'manifest.json'), encoding='utf-8') as f:
                        manifest = json.load(f)
                    self.assertEqual(manifest['files']['edits']['status'], 'error')
                finally:
                    extract._BUNDLE_DUMPS[:] = original
        finally:
            os.remove(tpath)


class BundleManifestIdentityTest(unittest.TestCase):
    """E4: the re-run allowance keys on the existing manifest.json belonging to THIS transcript,
    not merely existing — a foreign or unparsable manifest refuses rather than silently
    overwriting a different transcript's bundle."""

    def test_rerun_same_transcript_succeeds(self):
        tpath = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm1:
                        extract.bundle(tpath, target)
                self.assertEqual(cm1.exception.code, 0)
                # Re-run into the same dir with the SAME transcript succeeds.
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm2:
                        extract.bundle(tpath, target)
                self.assertEqual(cm2.exception.code, 0)
        finally:
            os.remove(tpath)

    def test_rerun_different_transcript_refused(self):
        tpath1 = write_transcript(_bundle_fixture_lines())
        tpath2 = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm1:
                        extract.bundle(tpath1, target)
                self.assertEqual(cm1.exception.code, 0)

                errbuf = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(errbuf):
                    with self.assertRaises(SystemExit) as cm2:
                        extract.bundle(tpath2, target)
                self.assertEqual(cm2.exception.code, 2, 'different transcript -> refuse')
                self.assertIn('different transcript', errbuf.getvalue())
        finally:
            os.remove(tpath1)
            os.remove(tpath2)

    def test_rerun_unparsable_manifest_treated_as_foreign(self):
        tpath = write_transcript(_bundle_fixture_lines())
        try:
            with tempfile.TemporaryDirectory() as outdir:
                target = os.path.join(outdir, 'bundle')
                os.makedirs(target)
                with open(os.path.join(target, 'manifest.json'), 'w', encoding='utf-8') as f:
                    f.write('{not valid json')
                errbuf = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(errbuf):
                    with self.assertRaises(SystemExit) as cm:
                        extract.bundle(tpath, target)
                self.assertEqual(cm.exception.code, 2,
                                  'unparsable manifest -> treated as foreign, refuse')
        finally:
            os.remove(tpath)


if __name__ == '__main__':
    unittest.main(verbosity=2)
