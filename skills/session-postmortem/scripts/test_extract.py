"""Unit tests for the session-postmortem v5 extract.py engine.

Standalone + self-locating — both of these must pass:
    python skills/session-postmortem/scripts/test_extract.py
    (cd skills/session-postmortem/scripts && python test_extract.py)

Coverage: the drift-canary census tripwire (the extend-never-delete pin), the v4 user-turn gate
(`load_lines` / `_user_prompt` / `_CMD_NAME_RE`), the six user-turn kinds, and the
digest's contracts (sections, manifest, budget ceiling, bounded-write refusal, usage dedup,
fail-open sections, transcript resolution).
"""
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # standalone-runnable from anywhere

import extract  # noqa: E402


TS = '2026-07-28T14:%02d:00.000Z'


def write_transcript(lines, path=None):
    """Write a list of dict entries to a .jsonl (temp file unless `path` is given); return the path."""
    if path is None:
        fd, path = tempfile.mkstemp(suffix='.jsonl')
        os.close(fd)
    with open(path, 'w', encoding='utf-8') as f:
        for d in lines:
            f.write(json.dumps(d) + '\n')
    return path


def user_line(text, ts=TS % 0, **kw):
    d = {'type': 'user', 'timestamp': ts, 'cwd': 'C:\\proj',
         'sessionId': '11111111-2222-3333-4444-555555555555',
         'message': {'role': 'user', 'content': text}}
    d.update(kw)
    return d


def assistant_line(blocks, ts=TS % 1, mid='msg_1', usage=None, model='claude-opus-4-8'):
    msg = {'role': 'assistant', 'id': mid, 'model': model, 'content': blocks}
    if usage is not None:
        msg['usage'] = usage
    return {'type': 'assistant', 'timestamp': ts, 'cwd': 'C:\\proj', 'message': msg}


def tool_use(name, inp, tuid='tu_1'):
    return {'type': 'tool_use', 'id': tuid, 'name': name, 'input': inp}


def tool_result(content, tuid='tu_1', ts=TS % 2, is_error=False):
    block = {'type': 'tool_result', 'tool_use_id': tuid, 'content': content}
    if is_error:
        block['is_error'] = True
    return {'type': 'user', 'timestamp': ts, 'cwd': 'C:\\proj',
            'toolUseResult': {'stdout': ''},
            'message': {'role': 'user', 'content': [block]}}


def usage(inp=100, out=200, cc=300, cr=400):
    return {'input_tokens': inp, 'output_tokens': out,
            'cache_creation_input_tokens': cc, 'cache_read_input_tokens': cr,
            'cache_creation': {'ephemeral_5m_input_tokens': cc, 'ephemeral_1h_input_tokens': 0}}


def run_digest(**kw):
    """Call extract.digest(**kw), swallow its SystemExit, return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            extract.digest(**kw)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def read_digest(out_dir):
    with open(os.path.join(out_dir, 'digest.md'), encoding='utf-8') as f:
        text = f.read()
    with open(os.path.join(out_dir, 'manifest.json'), encoding='utf-8') as f:
        return text, json.load(f)


# ---------------------------------------------------------------------------
# 1-2. Drift canary — the census tripwire + registry integrity
# ---------------------------------------------------------------------------

class DriftCanaryTest(unittest.TestCase):
    def _drift(self, lines):
        acc = {}
        for d in lines:
            extract._observe_drift(acc, d)
        return acc

    def test_drift_canary_knows_every_censused_production_shape(self):
        # THE TRIPWIRE (port of drift.rs drift_canary_knows_every_censused_production_shape): every
        # shape the real-tree census enumerated must be KNOWN, so the canary stays quiet on real
        # data (a missing entry = false drift). Extend the registry, never delete.
        for t in ['user', 'assistant', 'attachment', 'last-prompt', 'ai-title',
                  'file-history-snapshot', 'permission-mode', 'queue-operation', 'mode', 'started',
                  'result', 'system', 'agent-name', 'bridge-session', 'worktree-state',
                  'custom-title', 'summary', 'pr-link', 'file-history-delta',
                  # sighted live 2026-08-05 → 2026-09-23, registered 2026-09-23
                  'atis-latch', 'cost-state', 'relocated']:
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
                  'task_status', 'mcp_instructions_delta',
                  # sighted live 2026-07-30 (v5 bench run, rain-proof transcript): Read token-cap banner
                  'read_truncation_notice',
                  # 2026-09-23 census (575 transcripts): injected nudges, context snapshots, telemetry
                  'batching_reminder_sent', 'bash_output_audience_note', 'silent_turn_reminder',
                  'prompt_snapshot', 'date', 'model', 'environment', 'instructions',
                  'session_context', 'deferred_tools_record', 'credential_org',
                  'remote_session_change', 'thinking_drop', 'inlined_image_paths',
                  'structured_output']:
            self.assertIn(a, extract.KNOWN_ATTACHMENT_TYPES, f'censused attachment type {a} must be known')
        # `mcp_instructions_delta` history: characterized PHANTOM 2026-07-07 (zero structural
        # occurrences then; an assertNotIn pinned the decision) → REAL 2026-07-21 (×3 structural
        # occurrences from MCP connect/disconnect churn, verified 2026-07-22) → registered, pin
        # flipped to the assertIn above. The phantom era is history, not a live rule.
        for m in ['prompt', 'task-notification']:
            self.assertIn(m, extract.KNOWN_COMMAND_MODES)
        for p in ['sdk', 'typed', 'system', 'queued']:
            self.assertIn(p, extract.KNOWN_PROMPT_SOURCES)
        for o in ['task-notification', 'auto-continuation', 'human', 'peer', 'coordinator']:
            self.assertIn(o, extract.KNOWN_ORIGIN_KINDS)
        for c in ['ide_opened_file', 'command-name', 'ide_selection', 'command-message', 'bash-input',
                  'bash-stdout', 'system-reminder', 'local-command-stdout', 'local-command-caveat',
                  'pasted_content']:
            self.assertIn(c, extract.KNOWN_USER_CONTENT_TAGS, f'censused content tag {c} must be known')
        for f in ['isSidechain', 'isMeta', 'isCompactSummary', 'isVisibleInTranscriptOnly']:
            self.assertIn(f, extract.KNOWN_USER_FLAGS)
        # The canary's reason to exist: an invented shape is UNKNOWN.
        self.assertNotIn('totally-new-line-type', extract.KNOWN_LINE_TYPES)
        self.assertNotIn('totally-new-attachment', extract.KNOWN_ATTACHMENT_TYPES)

    def test_registries_are_immutable_frozensets(self):
        for name in ('KNOWN_LINE_TYPES', 'KNOWN_SYSTEM_SUBTYPES', 'KNOWN_ATTACHMENT_TYPES',
                     'KNOWN_COMMAND_MODES', 'KNOWN_PROMPT_SOURCES', 'KNOWN_ORIGIN_KINDS',
                     'KNOWN_USER_CONTENT_TAGS', 'KNOWN_USER_FLAGS'):
            self.assertIsInstance(getattr(extract, name), frozenset, f'{name} must be a frozenset')

    def test_max_distinct_is_the_fail_closed_bound(self):
        self.assertEqual(extract.MAX_DISTINCT, 64)
        many = [{'type': f'novel-{i}', 'timestamp': 'x'} for i in range(extract.MAX_DISTINCT + 10)]
        acc = self._drift(many)
        self.assertEqual(len(acc), extract.MAX_DISTINCT, 'accumulator is bounded at MAX_DISTINCT')
        acc2 = self._drift(many + [{'type': 'novel-0', 'timestamp': 'x'}])
        self.assertEqual(acc2[('line_type', 'novel-0')], 2, 'an already-seen pair counts past the cap')

    def test_novel_shapes_surface_and_known_ones_stay_quiet(self):
        self.assertEqual(self._drift([{'type': 'telepathy', 'timestamp': 'x'}]).get(
            ('line_type', 'telepathy')), 1)
        self.assertEqual(self._drift([user_line('hello')]), {}, 'a clean user line is not drift')
        d = self._drift([{'type': 'attachment', 'attachment': {'type': 'queued_command',
                                                               'commandMode': 'prompt',
                                                               'origin': {'kind': 'auto-resumption'}}}])
        self.assertEqual(d.get(('origin_kind', 'auto-resumption')), 1)
        # Every origin the human-origin gate drops must surface — including malformed shapes.
        for origin, value in (('garbage', '<non-dict>'), ({'kind': 5}, '<non-str-kind>')):
            self.assertEqual(self._drift([user_line('x', origin=origin)]).get(
                ('origin_kind', value)), 1, value)
        self.assertEqual(self._drift([user_line('x', origin={'kind': 'human'}),
                                      user_line('y', origin=None)]), {})
        self.assertEqual(self._drift([user_line('<= 5 should pass')]), {},
                         'prose opening with < is not a tag')

    def test_drift_subcommand_prints_rows_and_exits_zero(self):
        path = write_transcript([user_line('hi'), {'type': 'telepathy', 'timestamp': 'x'}])
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    extract.drift(path)
        finally:
            os.remove(path)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip().split('\t'), ['line_type', 'telepathy', '1'])


# ---------------------------------------------------------------------------
# 3. User-turn gate — `load_lines`, `_user_prompt`, `_CMD_NAME_RE` (ported verbatim from v4)
# ---------------------------------------------------------------------------

class CompatSurfaceTest(unittest.TestCase):
    def test_load_lines_skips_malformed(self):
        fd, path = tempfile.mkstemp(suffix='.jsonl')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'user'}) + '\n')
            f.write('{not json\n')
            f.write(json.dumps({'type': 'assistant'}) + '\n')
        try:
            got = list(extract.load_lines(path))
        finally:
            os.remove(path)
        self.assertEqual([d['type'] for d in got], ['user', 'assistant'])

    def test_user_prompt_admits_a_typed_turn(self):
        p = extract._user_prompt(user_line('do the thing'))
        self.assertIsNotNone(p)
        self.assertEqual(p, ('do the thing', None))

    def test_user_prompt_drops_the_non_turns(self):
        cases = {
            'isMeta': user_line('injected', isMeta=True),
            'sidechain': user_line('replayed', isSidechain=True),
            'compact summary': user_line('This session is being continued…', isCompactSummary=True),
            'tool_result': tool_result('output'),
            'task-notification': user_line('bg task done', origin={'kind': 'task-notification'}),
            'novel machine origin': user_line('from elsewhere', origin={'kind': 'telepathy'}),
            'bash output echo': user_line('<bash-stdout>hi</bash-stdout>'),
        }
        for label, line in cases.items():
            self.assertIsNone(extract._user_prompt(line), f'{label} is not a user turn')

    def test_sdk_cli_entrypoint_is_not_a_machine_signal(self):
        # `claude remote-control` sessions log entrypoint "sdk-cli", like a headless `claude -p` run;
        # gating on it dropped every turn of a phone-started session.
        cases = {
            'remote-control prompt': user_line('fix the tooltip', entrypoint='sdk-cli',
                                               origin={'kind': 'human'}, turnOrigin='human',
                                               promptSource='sdk'),
            'origin-less slash echo': user_line('<command-name>/model</command-name>',
                                                entrypoint='sdk-cli'),
            'headless prompt': user_line('eval probe', entrypoint='sdk-cli', turnOrigin='sdk'),
        }
        for label, line in cases.items():
            self.assertIsNotNone(extract._user_prompt(line), f'{label} is a user turn')
        answer = user_line('', entrypoint='sdk-cli', toolUseResult={'answers': {'Q?': 'A'}},
                           message={'role': 'user', 'content': [
                               {'type': 'tool_result', 'tool_use_id': 'tu_a', 'content': 'ok'}]})
        self.assertEqual(extract._user_turn(answer)[0], 'ask_answer')

    def test_cmd_name_re_group_one_is_the_command_name(self):
        m = extract._CMD_NAME_RE.search('<command-name>/code-review</command-name>')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), '/code-review')


# ---------------------------------------------------------------------------
# 4. User-turn kinds — the kind ladder layered on the ported gate
# ---------------------------------------------------------------------------

class UserTurnKindTest(unittest.TestCase):
    def test_typed_turn_is_verbatim(self):
        self.assertEqual(extract._user_turn(user_line('ship it')), ('typed', 'ship it'))

    def test_typed_turn_strips_injected_spans_only(self):
        line = user_line('<system-reminder>injected</system-reminder>real prose here')
        kind, text = extract._user_turn(line)
        self.assertEqual(kind, 'typed')
        self.assertEqual(text, 'real prose here')

    def test_bash_input_is_re_prefixed(self):
        kind, text = extract._user_turn(user_line('<bash-input>git status</bash-input>'))
        self.assertEqual((kind, text), ('bash_input', '!git status'))

    def test_slash_command_renders_name_and_args(self):
        raw = '<command-name>code-review</command-name><command-args>high</command-args>'
        self.assertEqual(extract._user_turn(user_line(raw)), ('slash_command', '/code-review high'))

    def test_local_command_system_line_is_a_slash_turn(self):
        sysline = lambda content, **kw: {'type': 'system', 'subtype': 'local_command',  # noqa: E731
                                         'timestamp': TS % 3, 'content': content, **kw}
        raw = '<command-name>/context</command-name>\n<command-message>context</command-message>'
        self.assertEqual(extract._user_turn(sysline(raw)), ('slash_command', '/context'))
        self.assertIsNone(extract._user_turn(sysline('<local-command-stdout>ok</local-command-stdout>')))
        self.assertIsNone(extract._user_turn(sysline(raw, isMeta=True)))

    def test_queued_steer_via_attachment(self):
        att = {'type': 'attachment', 'timestamp': TS % 3,
               'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                              'prompt': 'no, do X instead'}}
        self.assertEqual(extract._user_turn(att), ('queued_steer', 'no, do X instead'))

    def test_queued_steer_gate_admits_only_human_origin(self):
        # A subagent hand-back / cross-session send / orchestrator steer arrives as a
        # commandMode=="prompt" queued_command with a non-human origin — never the user speaking.
        steer = lambda origin: {'type': 'attachment', 'timestamp': TS % 3,  # noqa: E731
                                'attachment': {'type': 'queued_command', 'commandMode': 'prompt',
                                               'prompt': 'body', 'origin': origin}}
        for origin in (None, {'kind': 'human'}):
            self.assertEqual(extract._user_turn(steer(origin)), ('queued_steer', 'body'), origin)
        for kind in ('peer', 'coordinator', 'auto-continuation', 'telepathy'):
            self.assertIsNone(extract._user_turn(steer({'kind': kind})), kind)
        self.assertIsNone(extract._user_turn(steer('garbage')), 'non-dict origin fails closed')

    def test_queued_steer_via_prompt_source(self):
        kind, _ = extract._user_turn(user_line('type-ahead', promptSource='queued'))
        self.assertEqual(kind, 'queued_steer')

    def test_rejection_note_keeps_only_the_typed_reason(self):
        def rejected(said, tur="Error: The user doesn't want to proceed with this tool use."):
            text = ("The user doesn't want to proceed with this tool use. The tool use was rejected. "
                    "To tell you how to proceed, the user said:\n" + said +
                    "\n\nNote: The user's next message may contain a correction or preference.")
            line = tool_result(text, is_error=True)
            line['toolUseResult'] = tur
            return line
        self.assertEqual(extract._user_turn(rejected('start the dev server first')),
                         ('rejection_note', 'start the dev server first'))
        clarify = rejected('The user wants to clarify these questions.\n    Questions asked: …')
        self.assertIsNone(extract._user_turn(clarify), 'the chat-about-this template is not typed')
        self.assertIsNone(extract._user_turn(rejected('x', tur={'type': 'text'})),
                          'a dict toolUseResult quoting the phrase is a tool output')
        failed = tool_result('Exit code 1\nTo tell you how to proceed, the user said:\nquoted',
                             is_error=True)
        failed['toolUseResult'] = 'Error: Exit code 1'
        self.assertIsNone(extract._user_turn(failed), 'a failed command quoting the phrase')

    def test_ask_answer_renders_question_and_choice(self):
        line = user_line('', toolUseResult={
            'answers': {'Which model?': 'Opus'},
            'questions': [{'question': 'Which model?',
                           'options': [{'label': 'Opus'}, {'label': 'Sonnet'}]}]},
            message={'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'tu_a', 'content': 'ok'}]})
        kind, text = extract._user_turn(line)
        self.assertEqual(kind, 'ask_answer')
        self.assertIn('Q: Which model?', text)
        self.assertIn('A: Opus', text)

    def test_goal_is_deduped_across_its_bookends(self):
        goal = lambda met: {'type': 'attachment', 'timestamp': TS % 4,   # noqa: E731
                            'attachment': {'type': 'goal_status', 'met': met,
                                           'condition': 'all tests pass'}}
        self.assertEqual(extract._user_turn(goal(False)), ('goal', 'all tests pass'))
        path = write_transcript([goal(False), goal(True)])
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(scan['kinds']['goal'], 1, 'met=false/met=true bookends are one goal')

    def test_all_six_kinds_are_reachable_in_one_scan(self):
        lines = [
            user_line('typed prose', ts=TS % 0),
            user_line('<bash-input>ls</bash-input>', ts=TS % 1),
            user_line('<command-name>compact</command-name>', ts=TS % 2),
            user_line('steered', ts=TS % 3, promptSource='queued'),
            {'type': 'attachment', 'timestamp': TS % 4,
             'attachment': {'type': 'goal_status', 'condition': 'green build'}},
            user_line('', ts=TS % 5, toolUseResult={'answers': {'Q?': 'A'}},
                      message={'role': 'user', 'content': [
                          {'type': 'tool_result', 'tool_use_id': 't', 'content': 'x'}]}),
        ]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(dict(scan['kinds']),
                         {'typed': 1, 'bash_input': 1, 'slash_command': 1, 'queued_steer': 1,
                          'goal': 1, 'ask_answer': 1})


# ---------------------------------------------------------------------------
# 5. Digest end-to-end
# ---------------------------------------------------------------------------

def synthetic_session(n_extra_tools=6):
    """A ~30-record transcript exercising every section: user turns of several kinds, assistant
    text, tool calls (ok + ERR), an Agent dispatch, a Skill call, edits, a git commit, a hook fire,
    a compact boundary, and an interruption."""
    lines = [
        user_line('first ask: build the thing', ts=TS % 0),
        assistant_line([{'type': 'text', 'text': 'Starting on it now, here is the plan.'}],
                       ts=TS % 1, mid='m1', usage=usage()),
        assistant_line([tool_use('Read', {'file_path': 'C:\\proj\\src\\a.py'}, 'tu_read')],
                       ts=TS % 1, mid='m1', usage=usage()),
        tool_result('file contents', 'tu_read', ts=TS % 2),
        assistant_line([tool_use('Edit', {'file_path': 'C:\\proj\\src\\a.py'}, 'tu_edit')],
                       ts=TS % 2, mid='m2', usage=usage(50, 60, 70, 80)),
        tool_result('edited', 'tu_edit', ts=TS % 3),
        assistant_line([tool_use('Write', {'file_path': 'C:\\proj\\src\\b.py'}, 'tu_write')],
                       ts=TS % 3, mid='m3'),
        tool_result('<tool_use_error>disk full</tool_use_error>', 'tu_write', ts=TS % 4,
                    is_error=True),
        user_line('<command-name>code-review</command-name>', ts=TS % 5),
        assistant_line([tool_use('Agent', {'subagent_type': 'plan-executor',
                                           'prompt': 'do the batch'}, 'tu_agent')], ts=TS % 5,
                       mid='m4'),
        tool_result('agent done', 'tu_agent', ts=TS % 6),
        assistant_line([tool_use('Skill', {'skill': 'ballast:code-review', 'args': 'high'},
                                 'tu_skill')], ts=TS % 6, mid='m5'),
        tool_result('skill launched', 'tu_skill', ts=TS % 7),
        assistant_line([tool_use('Bash', {'command': 'git commit -m "x"'}, 'tu_git')], ts=TS % 7,
                       mid='m6'),
        tool_result('[main 1a2b3c4] engine: land the digest\n 2 files changed', 'tu_git',
                    ts=TS % 8),
        {'type': 'attachment', 'timestamp': TS % 8,
         'attachment': {'type': 'hook_system_message', 'content': '⚓ ballast: doc-write-guard — ok',
                        'hookName': 'H', 'hookEvent': 'PreToolUse'}},
        assistant_line([tool_use('mcp__gmail__search', {'query': 'inbox'}, 'tu_mcp')], ts=TS % 9,
                       mid='m7'),
        tool_result('3 results', 'tu_mcp', ts=TS % 10),
        {'type': 'system', 'subtype': 'compact_boundary', 'timestamp': TS % 11},
        user_line('second ask: now verify', ts=TS % 12),
        assistant_line([tool_use('Bash', {'command': 'pytest -q'}, 'tu_test')], ts=TS % 12,
                       mid='m8'),
        tool_result('[Request interrupted by user for tool use]', 'tu_test', ts=TS % 13),
        user_line('steer mid-flight', ts=TS % 14, promptSource='queued'),
        assistant_line([{'type': 'text', 'text': 'Understood — switching approach.'}], ts=TS % 15,
                       mid='m9', usage=usage(10, 20, 30, 40)),
    ]
    for i in range(n_extra_tools):
        lines.append(assistant_line([tool_use('Grep', {'pattern': f'needle{i}'}, f'tu_g{i}')],
                                    ts=TS % (16 + i), mid=f'mg{i}'))
        lines.append(tool_result('match', f'tu_g{i}', ts=TS % (16 + i)))
    return lines


class DigestTest(unittest.TestCase):
    def test_digest_writes_both_files_with_every_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            code, stdout, _ = run_digest(out_dir=out, transcript=tpath)
            self.assertEqual(code, 0, 'a clean digest exits 0')
            text, m = read_digest(out)
            for section in ('## SESSION', '## USER TURNS', '## TIMELINE', '## INVENTORY',
                            '## FILES TOUCHED', '## ANOMALIES'):
                self.assertIn(section, text, f'{section} must be present')
            self.assertNotIn('[section unavailable', text)
            for field in ('schema', 'transcript', 'session_id', 'project_dir', 'generated_utc',
                          'transcript_lines', 'transcript_bytes', 'first_ts', 'last_ts',
                          'wall_minutes', 'user_turns', 'assistant_responses', 'tool_calls_total',
                          'subagents', 'compact_boundaries', 'models', 'total_est_cost_usd',
                          'suggested_slug', 'git_window', 'truncation', 'focus',
                          'digest_generation_seconds', 'resolved_via'):
                self.assertIn(field, m, f'manifest must carry {field}')
            self.assertEqual(m['schema'], 5)
            self.assertEqual(m['truncation']['digest_bytes'],
                             os.path.getsize(os.path.join(out, 'digest.md')),
                             'reported digest_bytes must equal the file on disk')
            self.assertEqual(m['git_window'], {'since': m['first_ts'], 'until': m['last_ts']})

    def test_zero_user_turns_is_flagged_as_a_parser_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = [user_line('bg task done', origin={'kind': 'task-notification'}),
                     assistant_line([{'type': 'text', 'text': 'noted'}], usage=usage())]
            tpath = write_transcript(lines, os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath)
            self.assertIn('**No user turns captured** across 1 responses', read_digest(out)[0])
            tpath = write_transcript(synthetic_session(), os.path.join(tmp, 's.jsonl'))
            run_digest(out_dir=out, transcript=tpath)
            self.assertNotIn('No user turns captured', read_digest(out)[0])

    def test_u_numbering_is_consistent_between_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath)
            text, m = read_digest(out)
            turns = text.split('## USER TURNS', 1)[1].split('## TIMELINE', 1)[0]
            timeline = text.split('## TIMELINE', 1)[1].split('## INVENTORY', 1)[0]
            headers = [ln for ln in turns.splitlines() if ln.startswith('### U')]
            self.assertEqual(len(headers), m['user_turns']['total'])
            self.assertEqual([h.split()[1] for h in headers],
                             [f'U{i + 1}' for i in range(len(headers))])
            for i in range(len(headers)):
                self.assertIn(f'U{i + 1} (', timeline, 'every user turn is marked in the timeline')

    def test_session_facts_and_inventory_reflect_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath)
            text, m = read_digest(out)
            self.assertEqual(m['user_turns']['total'], 4)
            self.assertEqual(m['compact_boundaries'], 1)
            self.assertGreater(m['tool_calls_total'], 6)
            self.assertIn('ballast:code-review', text)          # skills inventory
            self.assertIn('plan-executor', text)                # agent dispatch
            self.assertIn('gmail', text)                        # MCP server table
            self.assertIn('doc-write-guard', text)              # hook fire
            self.assertIn('a.py', text)                         # files touched
            self.assertIn('--- COMPACT ---', text)
            self.assertIn('[interrupted]', text)
            self.assertIn('(ERR)', text)

    def test_focus_is_echoed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(2), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath, focus='budget algorithm')
            text, m = read_digest(out)
            self.assertEqual(m['focus'], 'budget algorithm')
            self.assertIn('budget algorithm', text)

    def test_oversized_record_is_counted_from_an_unrendered_payload(self):
        # The real shape: a PDF read's 12 MB rides `toolUseResult.file.base64`, which no section
        # renders — a rendered-content measure would report zero on the very records worth flagging.
        rec = tool_result('PDF file read: report.pdf', 'tu_pdf')
        rec['toolUseResult'] = {'file': {'base64': 'A' * (extract._OVERSIZE_CHARS + 10)}}
        path = write_transcript([user_line('read it'), rec])
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(scan['oversized'], 1)

    def test_idle_gap_marker(self):
        lines = [user_line('start', ts='2026-07-28T10:00:00.000Z'),
                 assistant_line([{'type': 'text', 'text': 'working on it right now'}],
                                ts='2026-07-28T10:00:10.000Z'),
                 user_line('back', ts='2026-07-28T12:30:00.000Z')]
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(lines, os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath)
            text, _ = read_digest(out)
            self.assertIn('[idle ~149m]', text)


# ---------------------------------------------------------------------------
# 6. Budget ceiling — the protected section survives the squeeze
# ---------------------------------------------------------------------------

class BudgetTest(unittest.TestCase):
    def _bloated(self):
        """Three long user prompts plus a long tail of tool calls carrying multi-KB args and
        multi-KB results — the shape that blows a small budget."""
        lines = []
        for i in range(3):
            lines.append(user_line(f'PROMPT{i} ' + ('long user reasoning. ' * 400), ts=TS % i))
        for i in range(120):
            lines.append(assistant_line(
                [{'type': 'text', 'text': 'assistant narration ' * 200}], ts=TS % (i % 60),
                mid=f'mt{i}'))
            lines.append(assistant_line(
                [tool_use('Bash', {'command': f'echo {i} ' + ('x' * 4000)}, f'tu_{i}')],
                ts=TS % (i % 60), mid=f'mb{i}'))
            lines.append(tool_result('R' * 200000, f'tu_{i}', ts=TS % (i % 60)))
        return lines

    def test_digest_stays_under_the_hard_ceiling(self):
        budget = 20000
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(self._bloated(), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            code, _, _ = run_digest(out_dir=out, transcript=tpath, budget=budget)
            self.assertEqual(code, 0)
            text, m = read_digest(out)
            self.assertLessEqual(m['truncation']['digest_bytes'], int(budget * 1.25),
                                 'digest must land under the 1.25x hard ceiling')
            self.assertGreater(m['truncation']['records_truncated'], 0,
                               'the timeline elision must be reported')
            # USER TURNS is protected: every prompt still renders at or above the 1024 floor.
            turns = text.split('## USER TURNS', 1)[1].split('## TIMELINE', 1)[0]
            bodies = turns.split('### U')[1:]
            self.assertEqual(len(bodies), 3, 'no user turn is dropped')
            for b in bodies:
                self.assertGreaterEqual(len(b), 1024, 'user prompts never cut below the floor')
                self.assertIn('elided', b)

    def test_user_section_is_not_degraded_when_it_is_not_the_bloat(self):
        # A big budget with the same transcript: the user cap never leaves 4096, so a prompt that
        # fits at 4096 is NOT elided (the ladder only touches the protected section under pressure).
        with tempfile.TemporaryDirectory() as tmp:
            lines = [user_line('short and sweet', ts=TS % 0)] + self._bloated()[3:40]
            tpath = write_transcript(lines, os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath, budget=200000)
            text, m = read_digest(out)
            self.assertEqual(m['truncation']['user_prompts_truncated'], 0)
            self.assertIn('short and sweet', text)


# ---------------------------------------------------------------------------
# 7. Bounded-write guard
# ---------------------------------------------------------------------------

class BoundedWriteTest(unittest.TestCase):
    def test_refuses_a_non_empty_dir_with_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(1), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            os.makedirs(out)
            with open(os.path.join(out, 'someone-elses.md'), 'w', encoding='utf-8') as f:
                f.write('data')
            code, _, err = run_digest(out_dir=out, transcript=tpath)
            self.assertEqual(code, 2)
            self.assertIn('refusing to write', err)

    def test_refuses_a_dir_holding_another_transcripts_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_transcript(synthetic_session(1), os.path.join(tmp, 'a.jsonl'))
            b = write_transcript(synthetic_session(1), os.path.join(tmp, 'b.jsonl'))
            out = os.path.join(tmp, 'out')
            self.assertEqual(run_digest(out_dir=out, transcript=a)[0], 0)
            code, _, err = run_digest(out_dir=out, transcript=b)
            self.assertEqual(code, 2)
            self.assertIn('different transcript', err)

    def test_re_run_on_the_same_transcript_overwrites_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_transcript(synthetic_session(1), os.path.join(tmp, 'a.jsonl'))
            out = os.path.join(tmp, 'out')
            self.assertEqual(run_digest(out_dir=out, transcript=a)[0], 0)
            self.assertEqual(run_digest(out_dir=out, transcript=a)[0], 0)

    def test_default_out_dir_is_per_session_under_the_user_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(1), os.path.join(tmp, 's.jsonl'))
            expected = os.path.join(os.path.expanduser('~'), '.claude', '.cache', 'ballast',
                                    'digest', 's')
            code, stdout, _ = run_digest(transcript=tpath)
            try:
                self.assertEqual(code, 0)
                self.assertTrue(stdout.startswith(expected), stdout)
            finally:
                for f in ('digest.md', 'manifest.json'):
                    p = os.path.join(expected, f)
                    if os.path.exists(p):
                        os.remove(p)
                if os.path.isdir(expected):
                    os.rmdir(expected)


# ---------------------------------------------------------------------------
# 8. Usage dedup + pricing
# ---------------------------------------------------------------------------

class UsageTest(unittest.TestCase):
    def test_two_records_sharing_a_message_id_are_counted_once(self):
        u = usage(1000, 2000, 3000, 4000)
        lines = [assistant_line([{'type': 'text', 'text': 'part one of the response'}],
                                mid='same', usage=u),
                 assistant_line([tool_use('Read', {'file_path': 'x'}, 'tu_x')], mid='same', usage=u)]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        tok = scan['models']['claude-opus-4-8']
        self.assertEqual((tok['input'], tok['output'], tok['cache_create'], tok['cache_read']),
                         (1000, 2000, 3000, 4000), 'one response, counted once')
        self.assertEqual(scan['assistant_responses'], 1, 'distinct message.id == one response')

    def test_pricing_families_and_the_unknown_model_null(self):
        self.assertEqual(extract._price_for('claude-opus-4-8[1m]')[:2], (5.0, 25.0))
        self.assertEqual(extract._price_for('claude-opus-4-1-20250805')[:2], (15.0, 75.0))
        self.assertEqual(extract._price_for('claude-opus-5-5'), (4.0, 20.0, 5.0, 0.2))
        self.assertEqual(extract._price_for('claude-opus-5')[:2], (5.0, 25.0))
        self.assertEqual(extract._price_for('claude-fable-5')[:2], (10.0, 50.0))
        self.assertEqual(extract._price_for('claude-sonnet-4-5')[:2], (3.0, 15.0))
        self.assertEqual(extract._price_for('claude-haiku-4-5')[:2], (1.0, 5.0))
        self.assertIsNone(extract._price_for('gpt-9-turbo'), 'an unknown family is unpriced')
        tok = {'input': 1_000_000, 'output': 0, 'cache_read': 0, 'cc_5m': 0, 'cc_1h': 0}
        self.assertAlmostEqual(extract._cost_usd('claude-opus-4-8', tok), 5.0)
        self.assertIsNone(extract._cost_usd('gpt-9-turbo', tok))

    def test_unknown_model_keeps_tokens_but_reports_null_cost(self):
        lines = [assistant_line([{'type': 'text', 'text': 'hello from the future model'}],
                                mid='m1', usage=usage(), model='claude-nova-9')]
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(lines, os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            run_digest(out_dir=out, transcript=tpath)
            _, m = read_digest(out)
            self.assertEqual(m['models']['claude-nova-9']['input'], 100)
            self.assertIsNone(m['models']['claude-nova-9']['est_cost_usd'])
            self.assertEqual(m['total_est_cost_usd'], 0)


# ---------------------------------------------------------------------------
# 9. Fail-open sections
# ---------------------------------------------------------------------------

class FailOpenTest(unittest.TestCase):
    def test_a_raising_section_degrades_to_a_marker(self):
        original = extract._sec_inventory

        def boom(scan, caps):
            raise RuntimeError('synthetic section failure')

        extract._sec_inventory = boom
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tpath = write_transcript(synthetic_session(2), os.path.join(tmp, 's.jsonl'))
                out = os.path.join(tmp, 'out')
                code, _, _ = run_digest(out_dir=out, transcript=tpath)
                text, _ = read_digest(out)
        finally:
            extract._sec_inventory = original
        self.assertEqual(code, 0, 'a broken section never fails the run')
        self.assertIn('[section unavailable: synthetic section failure]', text)
        for section in ('## SESSION', '## USER TURNS', '## TIMELINE', '## FILES TOUCHED',
                        '## ANOMALIES'):
            self.assertIn(section, text, f'{section} survives a sibling failure')
        self.assertIn('first ask: build the thing', text, 'the other sections keep their content')

    def test_scan_survives_non_string_tool_inputs(self):
        lines = [user_line('go'), assistant_line([
            tool_use('Agent', {'subagent_type': {'x': 1}, 'model': ['m']}, 'tu_a'),
            tool_use('Skill', {'skill': ['s'], 'args': {'a': 1}}, 'tu_s'),
            tool_use('Edit', {'file_path': 7}, 'tu_e'),
            tool_use('Bash', 'not-a-dict', 'tu_b')])]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(scan['agent_dispatch'], {'?': 1})
        self.assertEqual(scan['skills'], {'Skill': 1})
        self.assertEqual(scan['tools']['Bash'], 1)


# ---------------------------------------------------------------------------
# 10. Transcript resolution — env id, cwd fallback, and `--id` as a path
# ---------------------------------------------------------------------------

class SuggestedSlugTest(unittest.TestCase):
    ROOT = 'C:\\proj' if os.name == 'nt' else '/proj'

    def slug(self, *rels):
        return extract._suggested_slug([os.path.join(self.ROOT, r) for r in rels], self.ROOT)

    def test_dot_dirs_descend_to_a_content_bearing_segment(self):
        self.assertEqual(self.slug('.claude/harness-sweep/dossier.md'), 'harness-sweep')

    def test_pass_through_layout_dirs_descend(self):
        self.assertEqual(self.slug('src/components/Foo.tsx', 'src/components/Bar.tsx'),
                         'components')

    def test_ignored_dir_on_the_descent_kills_the_path(self):
        self.assertEqual(self.slug('.debug/shot.png', '.debug/shot2.png'), 'general')

    def test_bare_dotfile_carries_no_topic(self):
        self.assertEqual(self.slug('.gitignore'), 'general')

    def test_root_file_still_uses_its_stem(self):
        self.assertEqual(self.slug('CHANGELOG.md'), 'changelog')


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop('CLAUDE_CODE_SESSION_ID', None)

    def tearDown(self):
        if self._saved is not None:
            os.environ['CLAUDE_CODE_SESSION_ID'] = self._saved

    def test_slug_encoding_matches_the_real_projects_dir_shape(self):
        self.assertEqual(extract.encode_cwd_to_slug('C:\\Users\\dev\\Projects\\ballast'),
                         'C--Users-dev-Projects-ballast')

    def test_env_session_id_resolves_the_live_transcript(self):
        uuid = '11111111-2222-3333-4444-555555555555'
        with tempfile.TemporaryDirectory() as home:
            pdir = os.path.join(home, '.claude', 'projects', 'C--proj')
            os.makedirs(pdir)
            live = write_transcript([user_line('hi')], os.path.join(pdir, uuid + '.jsonl'))
            r = extract._resolve_transcript(session_id=uuid, home=home, cwd='C:\\other')
            self.assertEqual(r['via'], 'env')
            self.assertEqual(os.path.abspath(r['live']), os.path.abspath(live))

    def test_cwd_fallback_picks_the_newest_jsonl_in_the_slug_dir(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            pdir = os.path.join(home, '.claude', 'projects', extract.encode_cwd_to_slug(cwd))
            os.makedirs(pdir)
            older = write_transcript([user_line('old')], os.path.join(pdir, 'a.jsonl'))
            newer = write_transcript([user_line('new')], os.path.join(pdir, 'b.jsonl'))
            os.utime(older, (time.time() - 600, time.time() - 600))
            r = extract._resolve_transcript(home=home, cwd=cwd)
            self.assertEqual(r['via'], 'cwd_newest')
            self.assertEqual(os.path.abspath(r['live']), os.path.abspath(newer))
            self.assertEqual(r['uuid'], 'b', 'a non-UUID stem falls back to the bare stem')

    def test_unresolvable_returns_no_live_path(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            r = extract._resolve_transcript(home=home, cwd=cwd)
            self.assertIsNone(r['live'])
            self.assertIsNone(r['via'])

    def test_explicit_id_resolves_a_unique_prefix(self):
        uuid = '11111111-2222-3333-4444-555555555555'
        with tempfile.TemporaryDirectory() as home:
            pdir = os.path.join(home, '.claude', 'projects', 'C--proj')
            os.makedirs(pdir)
            live = write_transcript([user_line('hi')], os.path.join(pdir, uuid + '.jsonl'))
            r = extract._resolve_transcript(session_id='11111111', home=home, cwd='C:\\other')
            self.assertEqual(r['via'], 'id_prefix')
            self.assertEqual(os.path.abspath(r['live']), os.path.abspath(live))
            self.assertEqual(r['uuid'], uuid, 'prefix hit widens to the full UUID')

    def test_explicit_id_miss_errors_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            pdir = os.path.join(home, '.claude', 'projects', extract.encode_cwd_to_slug(cwd))
            os.makedirs(pdir)
            write_transcript([user_line('decoy')], os.path.join(pdir, 'a.jsonl'))
            r = extract._resolve_transcript(session_id='deadbeef', home=home, cwd=cwd)
            self.assertIsNone(r['live'], 'an explicit id must never fall back to cwd_newest')
            self.assertIn('deadbeef', r['error'])

    def test_explicit_id_ambiguous_prefix_errors(self):
        with tempfile.TemporaryDirectory() as home:
            pdir = os.path.join(home, '.claude', 'projects', 'C--proj')
            os.makedirs(pdir)
            for tail in ('aaaa', 'bbbb'):
                write_transcript([user_line('x')], os.path.join(
                    pdir, f'11111111-2222-3333-4444-55555555{tail}.jsonl'))
            r = extract._resolve_transcript(session_id='11111111', home=home, cwd='C:\\other')
            self.assertIsNone(r['live'])
            self.assertIn('ambiguous', r['error'])

    def test_id_flag_accepts_a_transcript_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(synthetic_session(1), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            code, _, _ = run_digest(out_dir=out, session_id=tpath)
            self.assertEqual(code, 0, '--id may carry a .jsonl path instead of a session id')
            _, m = read_digest(out)
            self.assertEqual(m['resolved_via'], 'explicit')
            self.assertEqual(m['transcript'], os.path.abspath(tpath))


# ---------------------------------------------------------------------------
# 11. Review-batch regressions — pairing, raw-command git detection, prose cleaning,
#     oversize-probe starvation, and the shared `_cut` renderer
# ---------------------------------------------------------------------------

class PairingRegressionTest(unittest.TestCase):
    def _ask_lines(self):
        """An AskUserQuestion tool_use whose ANSWER line is both a real user turn and the
        tool_result that closes it — the line that used to `continue` before pairing."""
        return [
            assistant_line([tool_use('AskUserQuestion',
                                     {'questions': [{'question': 'Which model?'}]}, 'tu_ask')],
                           ts=TS % 1, mid='m1'),
            user_line('', ts=TS % 2,
                      toolUseResult={'answers': {'Which model?': 'Opus'},
                                     'questions': [{'question': 'Which model?'}]},
                      message={'role': 'user', 'content': [
                          {'type': 'tool_result', 'tool_use_id': 'tu_ask', 'content': 'ok'}]}),
        ]

    def test_ask_answer_line_pairs_its_pending_tool_use(self):
        path = write_transcript(self._ask_lines())
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(scan['unpaired_tools'], 0,
                         'an ask-answer line closes its AskUserQuestion tool_use')
        self.assertEqual(scan['kinds']['ask_answer'], 1, 'it is still counted as a user turn')
        rows = [ev for ev in scan['events'] if ev.get('n2') == 'AskUserQuestion']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get('done'), 'the timeline row must read ok, not ?')

    def test_ask_answer_pairing_shows_ok_in_the_rendered_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = write_transcript(self._ask_lines(), os.path.join(tmp, 's.jsonl'))
            out = os.path.join(tmp, 'out')
            self.assertEqual(run_digest(out_dir=out, transcript=tpath)[0], 0)
            text, _ = read_digest(out)
            timeline = text.split('## TIMELINE', 1)[1].split('## INVENTORY', 1)[0]
            row = [ln for ln in timeline.splitlines() if 'AskUserQuestion' in ln]
            self.assertEqual(len(row), 1, timeline)
            self.assertIn('(ok)', row[0])
            self.assertNotIn('**Tool calls with no recorded result:**', text)

    def test_ordinary_tool_result_still_pairs(self):
        lines = [assistant_line([tool_use('Read', {'file_path': 'x.py'}, 'tu_r')], mid='m1'),
                 tool_result('contents', 'tu_r')]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(scan['unpaired_tools'], 0)


class GitFootprintTest(unittest.TestCase):
    """FILES TOUCHED comes from git over the session window, not from tool-call scraping."""

    def _git(self, repo, *args):
        subprocess.run(['git', '-C', repo, *args], check=True, capture_output=True)

    def _repo_with_commit(self, repo, subject):
        self._git(repo, 'init', '-q')
        for name in ('a.py', 'b.py'):
            with open(os.path.join(repo, name), 'w') as f:
                f.write('x\n')
        self._git(repo, 'add', 'a.py')
        # `-q` prints nothing -- the shape the old Bash-output scrape missed.
        self._git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', subject)

    @unittest.skipUnless(shutil.which('git'), 'git not on PATH')
    def test_window_commits_and_the_uncommitted_tree(self):
        with tempfile.TemporaryDirectory() as repo:
            self._repo_with_commit(repo, 'engine: land the batch')
            now = datetime.datetime.now(datetime.timezone.utc)
            iso = lambda d: d.strftime('%Y-%m-%dT%H:%M:%S.000Z')  # noqa: E731
            lines = [user_line('go', ts=iso(now - datetime.timedelta(minutes=5)), cwd=repo),
                     user_line('done', ts=iso(now + datetime.timedelta(minutes=5)), cwd=repo)]
            path = write_transcript(lines)
            try:
                scan = extract._scan(path)
            finally:
                os.remove(path)
            g = scan['git']
            self.assertEqual([subj for _, subj in g['commits']], ['engine: land the batch'])
            self.assertEqual(dict(g['files']), {'a.py': 1})
            self.assertEqual(g['dirty'], {'b.py'})
            section = extract._sec_files(scan, {})
            self.assertIn('engine: land the batch', section)
            self.assertIn('| b.py | 0 | yes |', section)

    @unittest.skipUnless(shutil.which('git'), 'git not on PATH')
    def test_commits_outside_the_window_are_excluded(self):
        with tempfile.TemporaryDirectory() as repo:
            self._repo_with_commit(repo, 'old')
            g = extract._git_footprint(repo, '2020-01-01T00:00:00.000Z', '2020-01-01T01:00:00.000Z')
            self.assertEqual((g['commits'], dict(g['files'])), ([], {}))

    def test_no_repo_omits_the_footprint_without_crashing(self):
        with tempfile.TemporaryDirectory() as not_a_repo:
            self.assertIsNone(extract._git_footprint(not_a_repo, TS % 0, TS % 9))
        self.assertIsNone(extract._git_footprint('', TS % 0, TS % 9))
        path = write_transcript([user_line('hi')])
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertIsNone(scan['git'])
        self.assertIn('Omitted', extract._sec_files(scan, {}))

    def test_missing_git_binary_fails_open(self):
        original = extract.subprocess.run

        def no_git(*a, **kw):
            raise FileNotFoundError('git')

        extract.subprocess.run = no_git
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertIsNone(extract._git_footprint(d, TS % 0, TS % 9))
        finally:
            extract.subprocess.run = original


class HookBlockTallyTest(unittest.TestCase):
    """A PreToolUse hard block is tallied in the hook inventory from BOTH real shapes."""

    def test_both_block_shapes_are_tallied(self):
        run_sh = ('PreToolUse:Bash hook error: [bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" '
                  'git-commit-guard]: BLOCKED (compound bypass): x')
        legacy = 'PreToolUse:Edit hook error: [python ~/.claude/hooks/doc-write-guard.py]: BLOCKED'
        bare = 'PreToolUse:Bash hook error: Detached (background) execution blocked'
        lines = [assistant_line([tool_use('Bash', {'command': 'x'}, 'tu_1'),
                                 tool_use('Edit', {'file_path': 'y'}, 'tu_2'),
                                 tool_use('Bash', {'command': 'z'}, 'tu_3'),
                                 tool_use('Bash', {'command': 'w'}, 'tu_4')], mid='m1'),
                 tool_result(run_sh, 'tu_1', is_error=True),
                 tool_result(legacy, 'tu_2', is_error=True),
                 tool_result(bare, 'tu_3', is_error=True),
                 tool_result(run_sh, 'tu_4'),        # not is_error: output that merely quotes one
                 {'type': 'attachment', 'timestamp': TS % 3,
                  'attachment': {'type': 'hook_blocking_error', 'hookName': 'PreToolUse:Bash',
                                 'hookEvent': 'PreToolUse', 'toolUseID': 'tu_9',
                                 'blockingError': {'blockingError': 'Compound command detected',
                                                   'command': 'Checking for compound commands...'}}}]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(dict(scan['hooks']), {'git-commit-guard (block)': 1,
                                               'doc-write-guard (block)': 1,
                                               'PreToolUse:Bash (block)': 2})

    def test_merged_banner_message_tallies_every_named_guard(self):
        # shell-guards joins its guards' banners with ` · ` into one systemMessage.
        merged = ('⚠️ ballast: process-lifecycle-guard — x · '
                  '📦 ballast: package-install-guard — y')
        lines = [{'type': 'attachment', 'timestamp': TS % 3,
                  'attachment': {'type': 'hook_system_message', 'content': merged,
                                 'hookName': 'H', 'hookEvent': 'PreToolUse'}}]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual(dict(scan['hooks']), {'process-lifecycle-guard': 1,
                                               'package-install-guard': 1})


class AssistantShapeTest(unittest.TestCase):
    def test_plain_string_assistant_content_is_text(self):
        path = write_transcript([assistant_line('a stored plain-string reply')])
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual([e['s'] for e in scan['events'] if e['k'] == 'text'],
                         ['a stored plain-string reply'])

    def test_subagent_handback_message_is_the_leafs_report(self):
        # Census pin (2026-09-23): 258 sidecars on CLI 2.1.276-2.1.281 carry `SubagentHandback`
        # with input {message}, then a success tool_result, then a short end_turn text; drift-silent.
        lines = [assistant_line([tool_use('SubagentHandback', {'message': 'report: all green'},
                                          'tu_hb')], mid='m1'),
                 tool_result('{"success":true,"message":"Report delivered"}', 'tu_hb'),
                 assistant_line([{'type': 'text', 'text': 'Done.'}], ts=TS % 3, mid='m2')]
        path = write_transcript(lines)
        try:
            scan = extract._scan(path)
        finally:
            os.remove(path)
        self.assertEqual([e['s'] for e in scan['events'] if e['k'] == 'text'],
                         ['[handback] report: all green', 'Done.'])
        self.assertEqual(scan['tools']['SubagentHandback'], 1)
        self.assertEqual(scan['unpaired_tools'], 0)
        self.assertEqual(scan['drift'], {})


class CleanProseRegressionTest(unittest.TestCase):
    def test_back_to_back_leading_ide_elements_are_all_stripped(self):
        raw = ('<ide_opened_file>The user opened a.py</ide_opened_file>'
               '<ide_selection>lines 1-4</ide_selection>real prompt')
        self.assertEqual(extract._clean_prose(raw), 'real prompt')
        kind, text = extract._user_turn(user_line(raw))
        self.assertEqual((kind, text), ('typed', 'real prompt'))

    def test_a_single_leading_element_and_plain_prose_are_unchanged_in_behavior(self):
        self.assertEqual(extract._clean_prose('<ide_selection>x</ide_selection>hi'), 'hi')
        self.assertEqual(extract._clean_prose('just prose'), 'just prose')


class OversizeProbeRegressionTest(unittest.TestCase):
    def test_oversized_string_among_the_earliest_keys_of_a_wide_record(self):
        # The LIFO stack pops the LAST-pushed entries first, so in a record wider than the node
        # budget the earliest keys were never reached. Strings must be tested at extend time.
        rec = {'big': 'A' * (extract._OVERSIZE_CHARS + 10)}
        rec.update({f'k{i:05d}': i for i in range(2500)})
        self.assertTrue(extract._max_str_len(rec, extract._OVERSIZE_CHARS))

    def test_a_wide_record_with_no_oversized_string_is_not_flagged(self):
        rec = {f'k{i:05d}': 'small' for i in range(2500)}
        self.assertFalse(extract._max_str_len(rec, extract._OVERSIZE_CHARS))

    def test_the_deep_real_shapes_still_hit(self):
        deep = {'toolUseResult': {'file': {'base64': 'A' * (extract._OVERSIZE_CHARS + 1)}}}
        self.assertTrue(extract._max_str_len(deep, extract._OVERSIZE_CHARS))
        self.assertFalse(extract._max_str_len({'a': {'b': {'c': {'d': {'e': {'f': 'A' * 99999}}}}}},
                                              extract._OVERSIZE_CHARS),
                         'beyond the depth bound stays unreachable')


class CutRoutingTest(unittest.TestCase):
    def test_tool_arg_marks_its_truncation(self):
        arg = extract._tool_arg({'command': 'y' * 500})
        self.assertEqual(len(arg), 401)
        self.assertTrue(arg.endswith('…'), 'a cut arg must carry the truncation marker')

    def test_tool_arg_collapses_whitespace_and_keeps_short_args_intact(self):
        self.assertEqual(extract._tool_arg({'command': '  git   status\n'}), 'git status')
        self.assertEqual(extract._tool_arg({}), '')

    def test_sanitize_label_still_bars_pipes_and_caps_at_60(self):
        self.assertEqual(extract._sanitize_label('a | b\n c'), 'a / b c')
        s = extract._sanitize_label('z' * 100)
        self.assertEqual(len(s), 61)
        self.assertTrue(s.endswith('…'))


class SimplificationTest(unittest.TestCase):
    def test_user_cap_max_is_derived_from_the_ladder(self):
        self.assertEqual(extract._USER_CAP_MAX, extract._LADDER[0][2])

    def test_dead_constants_are_gone(self):
        self.assertFalse(hasattr(extract, '_DISPATCH_TOOLS'))
        self.assertFalse(hasattr(extract, 'SUBCOMMANDS'))

    def _main(self, argv):
        saved = sys.argv
        out, err = io.StringIO(), io.StringIO()
        code = None
        try:
            sys.argv = argv
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    extract.main()
                except SystemExit as e:
                    code = e.code
        finally:
            sys.argv = saved
        return code, out.getvalue(), err.getvalue()

    def test_main_drift_path_and_error_exit_codes(self):
        path = write_transcript([user_line('hi')])
        try:
            self.assertEqual(self._main(['extract.py', 'drift', path])[0], 0)
            self.assertEqual(self._main(['extract.py', 'drift'])[0], 2, 'missing path exits 2')
            self.assertEqual(self._main(['extract.py', 'drift', path + '.nope'])[0], 2)
            self.assertEqual(self._main(['extract.py', 'bundle', path])[0], 2, 'unknown sub exits 2')
            self.assertEqual(self._main(['extract.py'])[0], 2)
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
