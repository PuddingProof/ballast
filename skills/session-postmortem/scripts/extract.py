#!/usr/bin/env python
"""session-postmortem extraction engine (v5).

ONE deterministic pass over a session transcript emits ONE size-capped digest a single
context can consume directly — replacing the v4 multi-dump bundle + agent fanout.

Usage:
    python extract.py resolve
    python extract.py drift <transcript-path>
    python extract.py digest [--out DIR] [--id SESSION_ID] [--transcript PATH]
                             [--budget BYTES] [--focus TOPIC]

Subcommands:
    resolve   Print this session's live transcript + PreCompact archive paths. Resolves from
              $CLAUDE_CODE_SESSION_ID; falls back to the newest *.jsonl in the cwd-derived
              projects/<slug>/ dir. Takes NO transcript-path arg.
    drift     Format-drift canary: tally any transcript shape OUTSIDE the known registries
              (line-type / system-subtype / attachment-type / commandMode / promptSource /
              origin.kind / leading content-tag / user is*-flag). Prints "kind<TAB>value<TAB>count"
              rows sorted; "no drift" when clean; always exits 0.
    digest    Single-pass digest: writes <out>/digest.md + <out>/manifest.json. --out defaults to
              ~/.claude/.cache/ballast/digest/<session_id>/.
"""
import datetime
import glob
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter

# Force UTF-8 stdout so unicode characters in transcripts (em-dash, arrows, etc.) don't crash on Windows cp1252
sys.stdout.reconfigure(encoding='utf-8')


def load_lines(path):
    """Yield parsed JSON entries from a transcript .jsonl, silently skipping malformed lines."""
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _text_from_content(content):
    """Flatten a message/attachment content value (str | list-of-blocks | None) to plain text."""
    if isinstance(content, list):
        return ' '.join(c.get('text', '') if isinstance(c, dict) else str(c) for c in content)
    if content is None:
        return ''
    return str(content)


# ---------------------------------------------------------------------------
# User-turn gate. `load_lines`, `_user_prompt`, and `_CMD_NAME_RE` descend from v4;
# test_extract.py pins their admit/drop decisions.
# ---------------------------------------------------------------------------

def _ask_answer(d):
    """If `d` is an AskUserQuestion ANSWER turn, return its non-empty answers dict; else None.

    When the user answers an AskUserQuestion by selecting option(s), Claude Code logs a
    `type=="user"` line whose top-level `toolUseResult` carries `answers` (a {question:
    selection} dict) plus `questions`. The line also rides a `tool_result` content block, so the
    generic tool-result gates in `_is_real_user_msg` would drop it — but it IS a genuine user turn
    (the option the user chose), so it is admitted before those gates. A CANCELLED dialog logs
    `answers == {}` (falsy) and is correctly NOT admitted. Mirrors cc-dashboard
    parse.rs::is_ask_answer and the cc-user-prompts engine's 4th positive user-shape.
    """
    if d.get('type') != 'user':
        return None
    tur = d.get('toolUseResult')
    if isinstance(tur, dict):
        ans = tur.get('answers')
        if isinstance(ans, dict) and ans:
            return ans
    return None


# A rejected tool call with a typed reason: the rejection result opens with `_REJECT_LEAD`, and the
# typed text sits between `_REJECT_SAID` and the "Note: The user's next message…" trailer.
# AskUserQuestion's "chat about this" button fills the same slot with fixed harness text, not typed.
_REJECT_LEAD = "The user doesn't want to proceed with this tool use."
_REJECT_SAID = 'To tell you how to proceed, the user said:\n'
_REJECT_TRAILER = "\n\nNote: The user's next message"
_REJECT_TEMPLATE = 'The user wants to clarify these questions.'


def _rejection_note(d):
    """If `d` is a tool call the user rejected WITH a typed reason, return that reason; else None.

    Shape: a `type=="user"` line whose `toolUseResult` is the rejection error string and whose
    is_error `tool_result` block OPENS with `_REJECT_LEAD` and carries the typed text after
    `_REJECT_SAID`. Anchoring on the lead-in keeps a failed command whose output merely quotes the
    phrase out; a dict toolUseResult never matches; a bare rejection has no reason."""
    if d.get('type') != 'user' or not isinstance(d.get('toolUseResult'), str):
        return None
    msg = d.get('message')
    content = msg.get('content') if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    for c in content:
        if isinstance(c, dict) and c.get('type') == 'tool_result' and c.get('is_error'):
            text = _text_from_content(c.get('content'))
            if not text.startswith(_REJECT_LEAD):
                continue
            _, sep, said = text.partition(_REJECT_SAID)
            said = said.split(_REJECT_TRAILER, 1)[0].strip()
            if sep and said and not said.startswith(_REJECT_TEMPLATE):
                return said
    return None


def _format_answers(ans):
    """Render an AskUserQuestion answers dict as one terse line."""
    return '[AskUserQuestion answer] ' + '; '.join(f'{q} → {sel}' for q, sel in ans.items())


# Slash-command echo parsing — shared by `_user_prompt` (ENG-5: a /slash IS a user turn, mirroring
# the cc-user-prompts engine `_from_user`/`_slash` + cc-dashboard `parse.rs::is_real_user_turn` SSoT,
# which both count every slash echo) and the digest's slash-command inventory. The leading
# <command-name> / <command-message> ordering varies by client, so search for either; <command-args>
# is optional.
_CMD_NAME_RE = re.compile(r'<command-name>\s*([^<]+?)\s*</command-name>')
_CMD_ARGS_RE = re.compile(r'<command-args>\s*([^<]*?)\s*</command-args>')


def _slash_echo(text):
    """If `text` is a user-typed slash-command echo, return its clean `/name args` form, else None.

    A slash echo is a real user turn (the person invoked a command) — the SSoT parsers count it — but
    its raw text is a noisy <command-name>/<command-message>/<command-args> blob (often trailed by a
    big <local-command-stdout> dump), so we render just the command + args for display. The name may
    already carry a leading slash ('/compact') or not ('code-review') — normalize to exactly one.
    Returns None for normal prose (the common case), so the caller emits the turn verbatim.
    """
    t = text.lstrip()
    if not t.startswith(('<command-name>', '<command-message>')):
        return None
    m = _CMD_NAME_RE.search(t)
    name = m.group(1).strip().lstrip('/') if m else 'command'
    am = _CMD_ARGS_RE.search(t)
    cargs = am.group(1).strip() if am else ''
    return f'/{name}' + (f' {cargs}' if cargs else '')


def _is_human_origin(origin):
    """True when a line's `origin` marks the person, not the machine — the ONE origin gate both user
    shapes share (`_is_real_user_msg` on user lines, `_attachment_prompt` on queued steers). A fail-
    CLOSED allow-list: only an absent/null origin or `kind == "human"` passes. Every other kind is
    machine-delivered text riding a user shape — task notices, the goal auto-continuation echo, and
    the peer/coordinator agent-messages (subagent hand-backs, cross-session sends, orchestrator
    steers into a leaf) that arrive as `commandMode == "prompt"` steers. A novel kind is DROPPED
    rather than miscounted as the user speaking; the drift canary's origin axis surfaces it."""
    if origin is None:
        return True
    return isinstance(origin, dict) and origin.get('kind') in (None, 'human')


def _is_real_user_msg(d):
    """Return True only for a genuine user turn (standalone prose OR an AskUserQuestion answer).

    Mirrors the canonical rule in cc-dashboard's core/parse.rs + the cc-user-prompts engine, with
    two known divergences the transcript-parser convergence port resolves: the origin gate here is
    an allow-list (`_is_human_origin`) where the siblings deny only `task-notification`, so they
    still count peer/coordinator agent-messages as turns; and there is no `entrypoint == "sdk-cli"`
    gate, which drops every turn of a `claude remote-control` session (logged as `sdk-cli`, like a
    headless `claude -p` run — whose prompt now counts as its one turn). A real turn is a
    `type=="user"` line that is NOT:
      - a synthetic / injected line: `isMeta == true` (slash expansions, the SessionStart caveat,
        hook-injected context, skill base-dir injections);
      - a post-compaction recap: `isCompactSummary == true` (the "This session is being
        continued…" block injected as a plain `type=="user"` line — it is NOT isMeta-flagged, so
        it slips every other gate);
      - a sidechain replay: `isSidechain == true` (a dispatched subagent prompt replayed as the
        subagent's first user turn — lives in the subagents/ sidecars);
      - a tool result: a top-level `toolUseResult` / `sourceToolUseID` field or a `tool_result`
        content block — EXCEPT an AskUserQuestion answer (`_ask_answer`) or a typed rejection reason
        (`_rejection_note`), which ride a toolUseResult yet ARE real turns, admitted before that gate;
      - machine-origin text: any `origin.kind` but "human" (`_is_human_origin` — e.g. a NON-meta
        `task-notification` background-task notice);
      - a `!`-mode shell OUTPUT echo (a `<local-command…>` wrapper, `<bash-stdout>` / `<bash-stderr>`).
        The `<bash-input>` the user TYPED is deliberately NOT excluded — it is a real user action.
        (A `<command-name>` / `<command-message>` SLASH echo is NOT excluded either — ENG-5: a /slash
        IS a user turn, counted by both SSoT parsers.)

    GUARDRAIL: `promptSource` ("typed" / "sdk" / "system" / "queued") is a TRANSPORT channel, NOT a
    human-vs-machine signal — never gate on it. Treating `sdk` as non-human hid ~790 genuine VS Code
    turns in the sibling parser; the human axis is structural origin, never promptSource or entrypoint.
    """
    if d.get('isMeta') is True:
        return False
    if d.get('isCompactSummary') is True:
        return False
    if d.get('isSidechain') is True:
        return False
    if _ask_answer(d) is not None or _rejection_note(d) is not None:
        return True   # rides a toolUseResult but IS a real turn — admit before the gate below
    if 'toolUseResult' in d or 'sourceToolUseID' in d:
        return False
    if not _is_human_origin(d.get('origin')):
        return False
    msg = d.get('message')
    if not isinstance(msg, dict):
        return False  # malformed/missing message — not a real turn
    content = msg.get('content', '')
    if isinstance(content, list) and any(
        isinstance(c, dict) and c.get('type') == 'tool_result' for c in content
    ):
        return False
    text = _text_from_content(content).strip()
    # Output-echo gates only — pure machine output the SSoT also drops (`leads_with_output_tag`).
    if text.startswith(('<local-command', '<bash-stdout', '<bash-stderr')):
        return False
    return True


def _attachment_prompt(d):
    """If `d` is a user-authored attachment prompt, return (text, marker); otherwise None.

    Two `type=="attachment"` shapes carry real user input (a fail-CLOSED allow-list — there are
    many attachment subtypes and only these are the person speaking):
      - `attachment.type == "queued_command"` with `commandMode == "prompt"` → a mid-turn STEER
        typed while the agent worked. Body in `attachment.prompt`. EXCLUDED: `task-notification` /
        any other commandMode, AND any non-human origin (`_is_human_origin`) — the harness's "Goal
        set: …" auto-continuation echo (would duplicate the goal `goal_status` already surfaces)
        and peer/coordinator agent-messages such as a subagent's hand-back report.
      - `attachment.type == "goal_status"` → an autonomous-mode GOAL/condition the user set in
        autopilot (text in `attachment.condition`). Logged as met=false / met=true bookends that
        repeat the same condition, so callers dedup by condition.

    Returns the marker so callers can tag the line and apply per-kind policy.
    """
    if d.get('type') != 'attachment':
        return None
    att = d.get('attachment')
    if not isinstance(att, dict):
        return None
    if att.get('type') == 'queued_command' and att.get('commandMode') == 'prompt' \
            and _is_human_origin(att.get('origin')):
        body = _text_from_content(att.get('prompt', ''))
        return (body, 'mid-turn steer') if body.strip() else None
    if att.get('type') == 'goal_status':
        cond = att.get('condition')
        if isinstance(cond, str) and cond.strip():
            return cond, 'autopilot goal'
    return None


def _user_prompt(d):
    """Return (text, marker) if `d` is a prompt the user sent, else None.

    The single source of truth for "did the user say something here", unifying the user shapes —
    a standalone `type=="user"` turn, an AskUserQuestion answer, a typed tool-rejection reason, a
    `/slash`-command echo (on a user line, or a `system`/`local_command` line for some built-ins
    such as `/context` and `/feedback`), a mid-turn `type=="attachment"` queued steer, and an
    autopilot goal.
    `marker` is None for a standalone prose turn / answer, else a label ('rejection note' |
    'slash-command' | 'mid-turn steer' | 'autopilot goal').
    """
    t = d.get('type')
    if t == 'user':
        if not _is_real_user_msg(d):
            return None
        ans = _ask_answer(d)
        if ans is not None:
            return _format_answers(ans), None
        note = _rejection_note(d)
        if note is not None:
            return note, 'rejection note'
        msg = d.get('message', {})
        content = msg.get('content', '') if isinstance(msg, dict) else ''
        text = _text_from_content(content)
        slash = _slash_echo(text)                      # ENG-5: /slash echo → clean `/name args`
        if slash is not None:
            return slash, 'slash-command'              # a real turn (SSoT parity), display-cleaned
        return text, None
    if t == 'attachment':
        return _attachment_prompt(d)
    if t == 'system' and d.get('subtype') == 'local_command' and d.get('isMeta') is not True \
            and d.get('isSidechain') is not True:
        slash = _slash_echo(_text_from_content(d.get('content')))  # its output lines return None
        return (slash, 'slash-command') if slash is not None else None
    return None


# ---------------------------------------------------------------------------
# User-turn KIND ladder (ported from the cc-user-prompts engine) layered ON TOP of the gate above:
# the gate decides IS-a-turn, the ladder decides WHICH KIND and renders the displayed text. Kinds:
# typed | bash_input | queued_steer | slash_command | ask_answer | rejection_note | goal.
# ---------------------------------------------------------------------------

# `!`-mode shell command the user typed, wrapped <bash-input …>cmd</bash-input>.
_BASH_INPUT_RE = re.compile(r'<bash-input[^>]*>(.*?)</bash-input>', re.DOTALL)
# A leading IDE editor-context element, stripped off a single-string content block while keeping
# any trailing user prose.
_IDE_ELEMENT_RE = re.compile(r'^\s*<ide_\w+\b[^>]*>.*?</ide_\w+>\s*', re.DOTALL)
_IDE_CONTEXT_TAGS = ('<ide_opened_file', '<ide_selection', '<ide_diagnostics')
# Harness-injected <system-reminder> context rides a user line; strip the span(s), keeping only the
# human's co-resident prose. BOTH patterns anchor the optional leading whitespace as [^\S\n]? (at
# most ONE horizontal space), NOT \s* — a \s* prefix is a ReDoS multi-anchor (O(n²) on a rejected
# tag after a whitespace run). Closed spans first, then a dangling/truncated OPEN token.
_SYSTEM_REMINDER_RE = re.compile(r'[^\S\n]?<system-reminder\b.*?</system-reminder>\s*', re.DOTALL)
_SYSTEM_REMINDER_OPEN_RE = re.compile(r'[^\S\n]?<system-reminder\b.*$', re.DOTALL)


def _strip_system_reminders(text):
    """Remove harness-injected <system-reminder> context, keeping only the human's co-resident
    prose. Handles closed spans, a dangling/truncated open token, and the leftover whitespace."""
    if '<system-reminder' not in text:
        return text
    text = _SYSTEM_REMINDER_RE.sub(' ', text)
    if '<system-reminder' in text:                 # an unclosed/dangling open token
        text = _SYSTEM_REMINDER_OPEN_RE.sub('', text)
    return text


def _is_context_block(c):
    """True for a harness-injected editor-context block (not the user's prose)."""
    return (isinstance(c, dict) and c.get('type') == 'text'
            and c.get('text', '').lstrip().startswith(_IDE_CONTEXT_TAGS))


def _clean_prose(text):
    """Strip injected spans from an already-flat prose string. Reminder-strip FIRST: the IDE
    pattern anchors on a LEADING element, so a leading reminder would otherwise hide it.

    The IDE strip LOOPS: a single editor context can inject several elements back to back
    (`<ide_opened_file>…</ide_opened_file><ide_selection>…</ide_selection>real prompt`), and a
    one-shot strip leaks the rest as literal XML into the VERBATIM user-turn display. Bounded at
    10 iterations — real injections are 1-3 elements, and the bound keeps a pathological string
    from turning the strip super-linear."""
    text = _strip_system_reminders(text)
    for _ in range(10):
        stripped = _IDE_ELEMENT_RE.sub('', text, count=1)
        if stripped == text:
            break
        text = stripped
    return text.strip()


def _clean_content(content):
    """Displayed (cleaned) form of a user line's content: harness-injected <system-reminder> spans
    and ide_* editor context removed, everything the human typed kept. Blocks join on newlines
    (distinct blocks are distinct lines), unlike the gate's space-joining `_text_from_content`."""
    if isinstance(content, list):
        parts = [c.get('text', '') if isinstance(c, dict) else str(c)
                 for c in content if not _is_context_block(c)]
        return _strip_system_reminders('\n'.join(p for p in parts if p)).strip()
    if content is None:
        return ''
    return _clean_prose(str(content))


def _render_ask(d, ans):
    """Render an AskUserQuestion answer turn as `Q: …` / `A: …` pairs, in the dialog's own question
    order (extra answer keys, if any, trail it)."""
    tur = d.get('toolUseResult')
    questions = tur.get('questions') if isinstance(tur, dict) else None
    order = [q.get('question', '') for q in questions
             if isinstance(q, dict)] if isinstance(questions, list) else []
    lines = []
    for k in dict.fromkeys([*order, *ans]):
        if k in ans:
            lines.append(f'Q: {k}\nA: {ans[k]}')
    return '\n'.join(lines) or _format_answers(ans)


def _user_turn(d):
    """Return (kind, displayed_text) for a genuine user turn, else None.

    Gate = `_user_prompt`; this only classifies + cleans what the gate
    already admitted, so the turn SET here is exactly the gate's.
    """
    p = _user_prompt(d)
    if p is None:
        return None
    text, marker = p
    if marker == 'autopilot goal':
        return 'goal', _clean_prose(text)
    if marker == 'mid-turn steer':
        return 'queued_steer', _clean_prose(text)
    if marker == 'slash-command':
        return 'slash_command', text          # already rendered to `/name args` by the gate
    if marker == 'rejection note':
        return 'rejection_note', _clean_prose(text)
    ans = _ask_answer(d)
    if ans is not None:
        return 'ask_answer', _render_ask(d, ans)
    msg = d.get('message')
    disp = _clean_content(msg.get('content') if isinstance(msg, dict) else None)
    if disp.startswith('<bash-input'):
        m = _BASH_INPUT_RE.search(disp)
        cmd = (m.group(1) if m else '').strip()
        return 'bash_input', '!' + cmd        # re-prefix `!` to mirror what was typed
    if d.get('promptSource') == 'queued':     # type-ahead steer that landed on a user line
        return 'queued_steer', disp
    return 'typed', disp


# ---------------------------------------------------------------------------
# Format-drift canary (`drift` subcommand) — the parsing twin of any allow-list discriminator
# that silently absorbs an unknown value. Every discriminator
# extract.py branches on is a fixed allow-list ending in a silent no-op, so a NOVEL line
# type / system subtype / attachment type / commandMode / promptSource / origin.kind /
# leading content-tag / is*-flag is absorbed with ZERO signal — the exact blind spot that
# admitted the compaction-summary + <bash-stdout> miscounts. This observer tallies every
# shape OUTSIDE the known registries so drift is observable the day a new CC format ships.
#
# CONTENT-FREE: only short category LABELS are read (a type / subtype / attachment-type /
# commandMode / promptSource / origin.kind / leading content-tag NAME / is*-flag NAME) —
# never a message or tool body. Ported from cc-dashboard core/drift.rs (the 2026-06-21
# census) + cc-user-prompts engine/extract.py (KNOWN_USER_FLAGS). EXTEND these registries
# (never delete) as new shapes are characterized — the same discipline as the model canary;
# `DriftCanaryTest.test_drift_canary_knows_every_censused_production_shape` pins the census.
# ADAPTED: extract.py already json.loads each line (load_lines), so we observe on the PARSED
# dict and reuse `_text_from_content`, rather than drift.rs's raw-line substring scan.
# ---------------------------------------------------------------------------

KNOWN_LINE_TYPES = frozenset({
    'user', 'assistant', 'system', 'attachment', 'ai-title', 'custom-title', 'last-prompt',
    'permission-mode', 'mode', 'queue-operation', 'file-history-snapshot', 'started', 'result',
    'agent-name', 'bridge-session', 'worktree-state',
    # `summary` — pre-registered (compaction summaries surface as a `summary` line type in long
    # sessions per the data-model note); not in the 2026-06-21 census, so it can't false-fire today.
    'summary',
    # `pr-link` — characterized live 2026-07-07: a gh/PR sidecar metadata record emitted alongside
    # gh-driven sessions — `{prNumber, prRepository, prUrl, sessionId, timestamp}`. Uniquely carries
    # no `uuid`/`parentUuid`/`message` (not turn-bearing); harmless because extract.py has no
    # line-level uuid access (all UUID logic is filename/sessionId-based) and all timestamp reads
    # are `d.get('timestamp','')`. Added per extend-never-delete.
    'pr-link',
    'file-history-delta',  # Added per extend-never-delete.
    # Characterized live 2026-09-23 — session-metadata records, none turn/usage-bearing, no axis reads
    # them: `atis-latch` {atis, sessionId}; `cost-state` {totalCostUSD, total*Duration,
    # totalLines*, startTime, modelUsage, …} (the CLI's running cost ledger); `relocated`
    # {sessionId, relocatedCwd}, 1:1 with `worktree-state` on worktree-isolated turns.
    'atis-latch', 'cost-state', 'relocated',
})
KNOWN_SYSTEM_SUBTYPES = frozenset({
    'model_refusal_fallback', 'turn_duration', 'away_summary', 'local_command', 'api_error',
    'compact_boundary', 'bridge_status', 'stop_hook_summary', 'informational',
    # `scheduled_task_fire` — characterized live 2026-06-27: a benign type=="system" wake notice
    # (content "Claude resuming /loop wakeup (…)") the scheduled-task/`/loop` feature emits on resume.
    # Postdates the 2026-06-21 census; not turn/usage-bearing. Added per extend-never-delete.
    'scheduled_task_fire',
    # `model_refusal_no_fallback` — characterized live 2026-07-07: no-fallback sibling of the
    # censused `model_refusal_fallback` — a `level=warning` classifier-refusal system line with
    # `apiRefusalCategory`, `apiRefusalExplanation`, `originalModel`, `refusedUserMessageUuid`;
    # content is empty. Not turn-bearing. Added per extend-never-delete.
    'model_refusal_no_fallback',
})
KNOWN_ATTACHMENT_TYPES = frozenset({
    'queued_command', 'goal_status', 'hook_additional_context', 'hook_success', 'hook_cancelled',
    'task_reminder', 'total_tokens_reminder', 'skill_listing', 'invoked_skills', 'dynamic_skill',
    'deferred_tools_delta', 'plan_mode_exit', 'plan_mode', 'plan_mode_reentry', 'auto_mode',
    'auto_mode_exit', 'ultra_effort_enter', 'ultra_effort_exit', 'ultrathink_effort',
    'workflow_keyword_request', 'command_permissions', 'todo_reminder', 'hook_blocking_error',
    'agent_listing_delta', 'edited_text_file', 'nested_memory', 'file', 'directory',
    'already_read_file', 'compact_file_reference', 'opened_file_in_ide', 'selected_lines_in_ide',
    'date_change',
    # `plan_file_reference` — characterized live 2026-07-07: post-compact plan-file re-injection,
    # sibling of the censused `compact_file_reference` — `{planFilePath, planContent}`. Re-injected
    # text, not a user turn; carrier is `type="attachment"` so turn counting already skips it.
    # Added per extend-never-delete.
    'plan_file_reference',
    # `hook_non_blocking_error` — characterized live 2026-07-07: non-blocking sibling of the censused
    # `hook_blocking_error`/`hook_success`/`hook_cancelled` — same tool-hook envelope:
    # `{hookName, hookEvent, exitCode, toolUseID, command, stderr, stdout, durationMs}`. No counting
    # branch needed. Added per extend-never-delete.
    'hook_non_blocking_error',
    # `hook_system_message` — characterized live 2026-07-10: the attachment record for a hook's own
    # `systemMessage` one-liner (the per-fire visibility line ballast hooks emit) —
    # `{content, hookName, hookEvent, toolUseID}`. Carrier is `type="attachment"` so turn counting
    # already skips it; not turn/usage-bearing. Consumed by the digest's hook inventory.
    # Added per extend-never-delete.
    'hook_system_message',
    # `task_status` — characterized live 2026-07-22 (sighted 2026-07-20): a background-task status
    # record — `{taskId, taskType, description, status, deltaSummary, outputFilePath}`. Sibling of
    # the task-notification shapes the parser already gates (origin.kind on user lines, commandMode
    # on queued_command), but a distinct attachment type; carrier is `type="attachment"` so turn
    # counting already skips it. Added per extend-never-delete.
    'task_status',
    # `read_truncation_notice` — characterized live 2026-07-30 (v5 bench, self-detected by the first
    # v5 digest run): the banner injected when a Read hits the token cap —
    # `{banner, toolUseID}`, banner text carries file/lines/cap and next-page guidance. Carrier is
    # `type="attachment"` so turn counting already skips it; digest surfaces truncated reads via the
    # anomalies section, not as a turn. Added per extend-never-delete.
    'read_truncation_notice',
    # `mcp_instructions_delta` — REAL as of 2026-07-21 (registered 2026-07-22): MCP server
    # connect/disconnect churn emits `{addedBlocks, addedNames, removedNames}` (verified ×3 in one
    # transcript, claude-in-chrome attach/detach). The 2026-07-07 PHANTOM classification (zero
    # structural occurrences, every hit prompt-text) was correct for its corpus but is obsolete —
    # the shape now occurs structurally; the tripwire's assertNotIn pin flipped with it. Carrier is
    # `type="attachment"`, not turn/usage-bearing. Added per extend-never-delete.
    'mcp_instructions_delta',
    # Characterized live 2026-09-23 (575-transcript census) — all injected context or telemetry,
    # carrier `type="attachment"` so turn counting already skips them; no counting branch needed.
    # Harness nudges: `batching_reminder_sent` {text, model}, `bash_output_audience_note`
    # {toolUseID}, `silent_turn_reminder` {text}.
    'batching_reminder_sent', 'bash_output_audience_note', 'silent_turn_reminder',
    # Session-context snapshots, re-emitted at session start / compact / model switch:
    # `prompt_snapshot` {systemPrompt}, `date` {date}, `model` {identity, text}, `environment`
    # {snapshot}, `instructions` {files}, `session_context` {context}, `deferred_tools_record`
    # {entries, nameOnlyAnnouncements}, `credential_org` {organizationUuid},
    # `remote_session_change` {url, commit, pr, sendUserFileHint, managedCommit, managedPr}.
    'prompt_snapshot', 'date', 'model', 'environment', 'instructions', 'session_context',
    'deferred_tools_record', 'credential_org', 'remote_session_change',
    # Per-request records: `thinking_drop` {requestId, model, newlyDropped, blockHashes, …} (thinking
    # blocks elided from the resent context), `inlined_image_paths` {paths}, `structured_output`
    # {data, toolUseID} (a workflow agent's schema'd result, sidechain-only).
    'thinking_drop', 'inlined_image_paths', 'structured_output',
})
KNOWN_COMMAND_MODES = frozenset({'prompt', 'task-notification'})
KNOWN_PROMPT_SOURCES = frozenset({'typed', 'sdk', 'system', 'queued'})
# origin.kind axis — extract.py uniquely branches on it (`_is_human_origin`, shared by both user
# shapes), an axis drift.rs has no equivalent for. The gate is an allow-list, so a novel kind is
# dropped rather than miscounted — the canary is what makes that drop visible. `peer` / `coordinator`
# (characterized 2026-09-23): agent-messages — subagent hand-backs, cross-session sends, and
# orchestrator steers into a leaf — carried as isMeta user lines or `commandMode == "prompt"` steers.
KNOWN_ORIGIN_KINDS = frozenset({
    'task-notification', 'auto-continuation', 'human', 'peer', 'coordinator',
})
# Leading content-tag NAMES the parser recognizes (without the angle brackets). Unlike drift.rs's
# string-only raw scan, the parsed-dict approach below ALSO sees array-form content tags (`ide_*`),
# so they are included here (closes drift.rs's documented array blind spot rather than inheriting it).
KNOWN_USER_CONTENT_TAGS = frozenset({
    'command-name', 'command-message', 'bash-input', 'bash-stdout', 'bash-stderr',
    'local-command-stdout', 'local-command-caveat', 'system-reminder', 'task-notification',
    'ide_opened_file', 'ide_selection', 'ide_diagnostics',
    'pasted_content',  # the CLI's large-paste wrapper on a typed turn — still the user speaking
})
# Marker flags on USER lines the parser recognizes — the gates (isSidechain/isMeta/isCompactSummary)
# plus a benign marker it ignores (isVisibleInTranscriptOnly). A truthy is*-flag on a user line
# outside this set is drift. Ported from cc-user-prompts KNOWN_USER_FLAGS. This axis is deliberately
# USER-line-only: `isSnapshotUpdate` rides file-history-snapshot lines (verified on the live tree),
# is not a user-turn marker, and must NOT count as drift — scoping to user lines excludes it cleanly.
KNOWN_USER_FLAGS = frozenset({
    'isSidechain', 'isMeta', 'isCompactSummary', 'isVisibleInTranscriptOnly',
})

# Distinct (kind, value) pairs kept per run before the fail-closed cardinality bound trips — a
# pathological transcript can't blow up the accumulator (real drift is a handful of values).
MAX_DISTINCT = 64

# A user line's leading content-tag NAME ('<ide_selection …' -> 'ide_selection'); the first char
# after '<' MUST be a letter, so prose that merely opens with '<' ('<= 5', '<3') is not a tag.
# Mirrors drift.rs leading_content_tag / cc-user-prompts leading_tag.
_LEADING_TAG_RE = re.compile(r'^<([A-Za-z][\w:-]*)')


def _leading_content_tag(content):
    """The XML-ish tag NAME a user message's flattened content OPENS with, or None when it doesn't
    lead with a tag. Content-free: only the short tag NAME is read, never the surrounding prose.
    Reuses `_text_from_content` to flatten (str | list-of-blocks | None); lstrips first so a leading
    empty/image block (which `_text_from_content` joins as a leading space) doesn't hide the tag — a
    deliberate, safe divergence from cc-user-prompts (lstrip only ever drops leading whitespace, and
    the letter-first rule still rejects '<= 5' / '<3' as prose)."""
    text = _text_from_content(content).lstrip()
    m = _LEADING_TAG_RE.match(text)
    return m.group(1) if m else None


def _bump(acc, kind, value):
    """Tally one (kind, value) drift pair into `acc`, fail-closed on cardinality. An already-seen
    pair counts freely; only the FIRST sighting of a NEW pair is gated by MAX_DISTINCT — so a
    hostile/garbled transcript can't grow the map without bound (mirrors drift.rs DriftAcc::bump)."""
    key = (kind, value)
    if key in acc or len(acc) < MAX_DISTINCT:
        acc[key] = acc.get(key, 0) + 1


def _observe_origin(acc, origin):
    """Tally a novel origin.kind from EITHER origin site `_is_human_origin` gates — the top-level
    `origin` on user lines AND the attachment-nested `attachment.origin` on queued steers.
    Observing only one site would let a novel kind on the OTHER drop out of the turn count with no
    signal — the silent miscount the canary exists to surface (and would leave attachment-only
    kinds such as 'auto-continuation' unreachable in the registry)."""
    if origin is None:
        return
    if not isinstance(origin, dict):
        _bump(acc, 'origin_kind', '<non-dict>')    # the gate drops it — so the canary must see it
        return
    ok = origin.get('kind')
    if ok is not None and not isinstance(ok, str):
        _bump(acc, 'origin_kind', '<non-str-kind>')
    elif isinstance(ok, str) and ok not in KNOWN_ORIGIN_KINDS:
        _bump(acc, 'origin_kind', ok)


def _observe_drift(acc, d):
    """Tally one parsed transcript line's discriminator values that fall OUTSIDE the known
    registries. Runs on every line, independent of turn classification. Mirrors
    cc-dashboard core/drift.rs::DriftAcc.observe, on the already-parsed dict."""
    t = d.get('type')
    if not isinstance(t, str):
        return
    if t not in KNOWN_LINE_TYPES:
        _bump(acc, 'line_type', t)
    if t == 'system':
        st = d.get('subtype')
        if isinstance(st, str) and st not in KNOWN_SYSTEM_SUBTYPES:
            _bump(acc, 'system_subtype', st)
    elif t == 'attachment':
        att = d.get('attachment')
        if isinstance(att, dict):
            at = att.get('type')
            if isinstance(at, str) and at not in KNOWN_ATTACHMENT_TYPES:
                _bump(acc, 'attachment_type', at)
            # commandMode gates the queued-prompt turn count (a novel one is a silent UNDERcount).
            # Observed whenever PRESENT — not only under queued_command — so a future mode riding a
            # different attachment type still surfaces here (only queued_command carries one today).
            cm = att.get('commandMode')
            if isinstance(cm, str) and cm not in KNOWN_COMMAND_MODES:
                _bump(acc, 'command_mode', cm)
            # Attachment-NESTED origin.kind — the gate site `_attachment_prompt` keys on. Observed
            # via the shared `_observe_origin` so the two gates + canary can't drift (see its doc).
            _observe_origin(acc, att.get('origin'))
    elif t == 'user':
        # is*-flag axis — USER lines only (see KNOWN_USER_FLAGS: excludes file-history-snapshot's
        # isSnapshotUpdate). A truthy is*-flag outside the known set is drift.
        for k, v in d.items():
            if k.startswith('is') and v is True and k not in KNOWN_USER_FLAGS:
                _bump(acc, 'user_is_flag', k)
        ps = d.get('promptSource')
        if isinstance(ps, str) and ps not in KNOWN_PROMPT_SOURCES:
            _bump(acc, 'prompt_source', ps)
        # Top-level origin.kind — the gate site `_is_real_user_msg` keys on (same observer as the
        # attachment-nested site above, so the two gates + canary stay in lockstep).
        _observe_origin(acc, d.get('origin'))
        # Leading content-tag axis. GUARD (mirrors drift.rs is_tool_result_line): SKIP this axis on a
        # user line carrying a tool_result block — its inner OUTPUT or a co-resident text block can
        # open with '<…' (an MCP tool returning '<result>…'), which would mint spurious drift.
        msg = d.get('message')
        content = msg.get('content') if isinstance(msg, dict) else None
        if not (isinstance(content, list) and any(
                isinstance(c, dict) and c.get('type') == 'tool_result' for c in content)):
            tag = _leading_content_tag(content)
            if tag is not None and tag not in KNOWN_USER_CONTENT_TAGS:
                _bump(acc, 'user_content_tag', tag)


def _collect_drift(path):
    """Build the {(kind, value): count} drift accumulator for a transcript (the testable core of
    `drift`). Empty dict == the registries stayed quiet (the healthy, common case)."""
    acc = {}
    for d in load_lines(path):
        _observe_drift(acc, d)
    return acc


def _drift_rows(acc):
    """Sort a drift accumulator into display rows: kind asc / count desc / value asc."""
    return sorted(acc.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))


def drift(path):
    """Format-drift canary: print a per-axis tally of every transcript shape OUTSIDE the known
    registries, one `kind<TAB>value<TAB>count` row, sorted by kind asc / count desc / value asc.
    Pure diagnostic — ALWAYS exits 0; the presence of rows (anything other than the literal
    'no drift') is the signal the orchestrator keys the report caveat off. Prints 'no drift' when
    clean so a human running it sees an explicit all-clear."""
    rows = _drift_rows(_collect_drift(path))
    if not rows:
        print('no drift')
        sys.exit(0)
    for (kind, value), count in rows:
        print(f'{kind}\t{value}\t{count}')
    sys.exit(0)


# ---------------------------------------------------------------------------
# Transcript resolution (`resolve`)
# ---------------------------------------------------------------------------

# Canonical UUID shape: 8-4-4-4-12 hex.
_UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

# Claude Code names each session's transcript dir ~/.claude/projects/<slug>/, where <slug> is the
# session-LAUNCH cwd with every path-structural character flattened to a single '-' (drive colon,
# BOTH separators, '.', and spaces). FORWARD-ONLY: the encoding is many-to-one, so a slug can never
# be reversed to a path — we only encode a real cwd and compare. Ported from the cc-user-prompts
# engine (corpus-verified against real cwd<->slug pairs).
_SLUG_FLATTEN_RE = re.compile(r'[:.\\/ ]')


def encode_cwd_to_slug(cwd):
    """Flatten a cwd to its Claude Code projects/ dir slug (see _SLUG_FLATTEN_RE)."""
    return _SLUG_FLATTEN_RE.sub('-', cwd or '')


def _session_uuid_from_path(path):
    """Extract the session UUID from a transcript filename.

    Live transcripts are "<uuid>.jsonl"; PreCompact archives (written by a user-side PreCompact
    archiver, if configured) are "<timestamp>_<trigger>_<uuid>.jsonl". Match the trailing canonical
    UUID in either form; fall back to the bare stem if no UUID is present (best effort).
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _UUID_RE.search(stem)
    return m.group(0) if m else stem


def _resolve_transcript(session_id=None, home=None, cwd=None):
    """Resolve this session's transcript. Returns {uuid, live, archives, via, error}.

    Deterministic paths, in order:
      1. `via == 'env'` — the session UUID (explicit `--id` or $CLAUDE_CODE_SESSION_ID) names
         `~/.claude/projects/*/<uuid>.jsonl` exactly.
      2. `via == 'id_prefix'` — explicit `--id` only: unique-prefix match on the same tree
         (reports print the 8-char short id, so that is what a user pastes back).
      3. `via == 'cwd_newest'` — no explicit `--id`: derive the project dir from cwd via
         `encode_cwd_to_slug` and take the newest-mtime `*.jsonl` directly in it.
    An explicit `--id` that matches nothing (or more than one session) never falls back —
    `live` stays None and `error` carries the message, because silently digesting a
    different session is worse than failing. `home` / `cwd` are parameters (not reads)
    so resolution is testable against a fake tree.
    """
    home = home or os.path.expanduser('~')
    explicit = bool((session_id or '').strip())
    uuid = (session_id or os.environ.get('CLAUDE_CODE_SESSION_ID', '')).strip()
    projects = os.path.join(home, '.claude', 'projects')
    live, via, error = None, None, None
    if uuid:
        hits = sorted(glob.glob(os.path.join(projects, '*', glob.escape(uuid) + '.jsonl')))
        if hits:
            live, via = hits[0], 'env'
        elif explicit:
            hits = sorted(glob.glob(os.path.join(projects, '*', glob.escape(uuid) + '*.jsonl')))
            if len(hits) == 1:
                live, via = hits[0], 'id_prefix'
                uuid = _session_uuid_from_path(live)
            elif not hits:
                error = (f"--id '{uuid}' matched no transcript under {projects} "
                         "(explicit ids never fall back to another session)")
            else:
                error = (f"--id '{uuid}' is ambiguous — {len(hits)} matches: "
                         + ', '.join(os.path.basename(h) for h in hits))
    if live is None and not explicit:
        pdir = os.path.join(projects, encode_cwd_to_slug(os.path.abspath(cwd or os.getcwd())))
        cands = glob.glob(os.path.join(pdir, '*.jsonl'))
        if cands:
            live, via = max(cands, key=os.path.getmtime), 'cwd_newest'
            if not uuid:
                uuid = _session_uuid_from_path(live)
    # Archive filenames are "<timestamp>_<trigger>_<uuid>.jsonl"; the timestamp prefix sorts
    # chronologically, so a plain sort gives oldest->newest.
    archives = sorted(glob.glob(os.path.join(home, '.claude', 'compact-backups',
                                             f'*_{uuid}.jsonl'))) if uuid else []
    return {'uuid': uuid, 'live': live, 'archives': archives, 'via': via, 'error': error}


def resolve():
    """Print the current session's live transcript path + any PreCompact archives.

    Output (stdout):
        UUID: <uuid>
        VIA: env | cwd_newest
        LIVE: <path or (not found)>
        ARCHIVES (oldest->newest): one indented path per line, or "ARCHIVES: (none)"
    """
    r = _resolve_transcript()
    if r['live'] is None:
        print('ERROR: no transcript found (CLAUDE_CODE_SESSION_ID unset/unresolvable and no '
              'session .jsonl under the cwd-derived projects dir)', file=sys.stderr)
        sys.exit(1)
    print(f"UUID: {r['uuid']}")
    print(f"VIA: {r['via']}")
    print(f"LIVE: {r['live']}")
    if r['archives']:
        print('ARCHIVES (oldest->newest):')
        for a in r['archives']:
            print(f'  {a}')
    else:
        print('ARCHIVES: (none)')


# ---------------------------------------------------------------------------
# Subagent sidecar roster — a session that dispatches subagents gets a sibling sidecar dir beside
# the LIVE transcript: `<uuid>/subagents/**` holding each agent's own transcript (`agent-<id>.jsonl`)
# + an `agent-<id>.meta.json` (agentType/description/spawnDepth), nested under `workflows/wf_<id>/`
# for Workflow fan-outs. Content-free (names/types/token counts only). The walk REFUSES to follow
# reparse points (symlinks / junctions), mirroring cc-dashboard discovery.rs / parse.rs.
# ---------------------------------------------------------------------------

# Display cap for the subagent roster TABLE — mirrors cc-dashboard's `agent_sample_cap`. The
# dispatched COUNT and the token totals stay EXACT over all agents; only rendered rows are sampled.
_AGENT_SAMPLE_CAP = 12


def _is_reparse_point(path):
    """True if `path` is a symlink OR (Windows) a junction / mount point — ANY reparse point.

    `os.path.islink()` returns False for Windows JUNCTIONS, so it MISSES the reparse points a stray
    (or planted) link could use to redirect a sidecar read outside the trusted ~/.claude/projects
    tree. Fails CLOSED: an unstattable path is treated as a reparse point and refused.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True  # can't stat it -> untrusted, refuse (fail closed)
    attrs = getattr(st, 'st_file_attributes', 0)
    if attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        return True
    return stat.S_ISLNK(st.st_mode)  # POSIX fallback: a plain symlink


def _walk_sidecar_files(root, suffix):
    """Sorted paths of files under `root` ending with `suffix`, at any depth, WITHOUT following
    reparse points (symlinks / junctions) — `glob('**/*')` follows symlinked dirs and `os.walk`
    wouldn't prune Windows junctions either."""
    results = []

    def _recurse(d):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if _is_reparse_point(e.path):
                continue
            if e.is_dir(follow_symlinks=False):
                _recurse(e.path)
            elif e.is_file(follow_symlinks=False) and e.name.endswith(suffix):
                results.append(e.path)

    if os.path.isdir(root) and not _is_reparse_point(root):
        _recurse(root)
    return sorted(results)


def _subagent_dir(path):
    """The subagents/ sidecar dir for a main transcript: `<uuid>.jsonl` -> `<uuid>/subagents/`."""
    return os.path.splitext(path)[0] + os.sep + 'subagents'


def _subagent_dirs(path, chain=()):
    """Every EXISTING `<uuid>/subagents` sidecar dir for this transcript's resume chain.

    A forked/resumed UUID splits sidecars across `<old-uuid>/subagents/` + `<new-uuid>/subagents/`.
    `chain` is the set of sessionIds seen ON THIS transcript's own lines (collected by the digest's
    single pass — never a glob of projects/<proj>/, so no unrelated session's sidecars leak in).
    """
    parent = os.path.dirname(path)
    dirs = []
    primary = _subagent_dir(path)
    if os.path.isdir(primary):
        dirs.append(primary)
    for u in chain:
        d = os.path.join(parent, u, 'subagents')
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _sanitize_label(desc):
    """Collapse a raw `description` string into one safe markdown-table cell (whitespace collapsed,
    `|` -> `/`, 60-char cap) — the orchestrator-authored dispatch label, never the agent's prompt."""
    return _cut(desc.replace('|', '/'), 60)


def _short_model(m):
    """Trim a model id to its family-version for display: `claude-opus-4-8[1m]` -> `opus-4-8`."""
    if not m or m == '?':
        return '?'
    return m.replace('claude-', '').split('[')[0]


def _split_mcp(name):
    """`mcp__server__tool` -> ('server', 'tool'); a non-MCP tool -> (None, name). Mirrors
    tools.rs::split_mcp: strip the `mcp__` prefix, split on the FIRST `__`."""
    if name.startswith('mcp__'):
        server, sep, tool = name[len('mcp__'):].partition('__')
        return (server, tool if sep else '')
    return (None, name)


_USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                 'cache_read_input_tokens')


def _scan_sidecar(jsonl):
    """ONE pass over a subagent sidecar transcript -> (total_tokens, tool Counter, model).

    Usage is deduped by message.id (MAX per field — a response logs as several lines repeating the
    same usage); tool calls are deduped by tool_use BLOCK id (sidecars never re-log, and message-id
    dedup would UNDERcount tools). Three separate passes in v4; one here — sidecar bytes dominate
    the digest's wall time on fanout-heavy sessions.
    """
    per_id, keyless, tools, seen = {}, 0, Counter(), set()
    model = '?'
    for d in load_lines(jsonl):
        if d.get('type') != 'assistant':
            continue
        msg = d.get('message')
        if not isinstance(msg, dict):
            continue
        if model == '?' and msg.get('model'):
            model = msg['model']
        u = msg.get('usage')
        if isinstance(u, dict):
            vals = [u.get(f, 0) or 0 for f in _USAGE_FIELDS]
            mid = msg.get('id')
            if mid:
                slot = per_id.setdefault(mid, [0, 0, 0, 0])
                for i, v in enumerate(vals):
                    if v > slot[i]:
                        slot[i] = v
            else:
                keyless += sum(vals)
        content = msg.get('content')
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    bid = c.get('id')
                    if bid is not None:
                        if bid in seen:
                            continue
                        seen.add(bid)
                    tools[c.get('name', '?')] += 1
    return keyless + sum(sum(s) for s in per_id.values()), tools, model


def _collect_subagents(sidedir):
    """Walk a subagents/ dir -> per-agent dicts {agent_type, model, tokens, tool_count, label, depth}.

    Each agent is an `agent-*.meta.json` paired with its `agent-*.jsonl`, at any depth (incl.
    `workflows/wf_*/`). `description` is the ORCHESTRATOR's own dispatch label — the agent's prompt
    and first user turn stay unread. The meta is size-capped at 64 KB (parse.rs parity); the DERIVED
    `.jsonl` sibling is re-checked for reparse points before opening. Returns ALL agents UNCAPPED.
    """
    agents = []
    for meta_path in _walk_sidecar_files(sidedir, '.meta.json'):
        agent_type, label, depth = 'agent', '', 1
        try:
            if os.path.getsize(meta_path) <= 65536:
                with open(meta_path, encoding='utf-8') as f:
                    m = json.load(f)
                if isinstance(m, dict):
                    if isinstance(m.get('agentType'), str) and m['agentType'].strip():
                        agent_type = m['agentType'].strip()
                    desc = m.get('description')
                    if isinstance(desc, str) and desc.strip():
                        label = _sanitize_label(desc)
                    # bool is an int subclass — exclude it so `"spawnDepth": true` can't pass.
                    sd = m.get('spawnDepth')
                    if isinstance(sd, int) and not isinstance(sd, bool) and sd > 0:
                        depth = sd
        except (OSError, json.JSONDecodeError):
            pass
        jsonl = meta_path[:-len('.meta.json')] + '.jsonl'
        tokens, tools, model = 0, Counter(), '?'
        if os.path.exists(jsonl) and not _is_reparse_point(jsonl):
            tokens, tools, model = _scan_sidecar(jsonl)
        agents.append({'agent_type': agent_type, 'model': model, 'label': label, 'depth': depth,
                       'tokens': tokens, 'tool_count': sum(tools.values())})
    return agents


# ---------------------------------------------------------------------------
# Pricing — a small offline table ported from cc-dashboard core/pricing.rs (which mirrors ccusage).
# APPROXIMATE, ccusage-aligned snapshot 2026-09: prices drift, and this deliberately SKIPS the
# >200k tiering and the fast-speed multiplier (both no-ops on standard Claude Code data). USD per
# MILLION tokens: (input, output, cache_write_5m, cache_read); 1-hour cache writes bill at 2x base
# input. An UNKNOWN model still has its tokens counted — its cost is reported as null, never guessed.
# ---------------------------------------------------------------------------

_CACHE_1H_INPUT_MULT = 2.0


def _price_for(model):
    """(input, output, cache_write_5m, cache_read) USD per million tokens, or None if no family
    branch claims this id (the drift/new-launch case — the caller reports a null cost)."""
    m = (model or '').lower().replace('.', '-').replace('@', '-').split('[')[0]
    if 'synthetic' in m:
        return (0.0, 0.0, 0.0, 0.0)
    if 'fable' in m or 'mythos' in m:
        return (10.0, 50.0, 12.5, 1.0)
    if 'opus' in m:
        # Opus 5.5 is its own $4/$20 SKU (CC 2.1.280 changelog); cache write keeps the 1.25x-input
        # ratio every other row uses. Cache read is $0.20 as published: 0.05x input, deliberately
        # off the 0.1x ratio of the other rows.
        if re.search(r'opus-5-5(?!\d)', m):
            return (4.0, 20.0, 5.0, 0.2)
        # Legacy Opus (3, and the original 4.0/4.1 generation) is $15/$75; everything else Opus is
        # the modern $5/$25 SKU. Legacy is the CLOSED set so a future Opus minor prices as modern.
        legacy = '3-opus' in m or re.search(r'opus-4-[01](?!\d)', m) is not None
        return (15.0, 75.0, 18.75, 1.5) if legacy else (5.0, 25.0, 6.25, 0.5)
    if 'haiku' in m:
        if 'haiku-3-5' in m or '3-5-haiku' in m:
            return (0.8, 4.0, 1.0, 0.08)
        if 'haiku-3' in m or '3-haiku' in m:
            return (0.25, 1.25, 0.3, 0.03)
        return (1.0, 5.0, 1.25, 0.1)
    if 'sonnet' in m:
        return (3.0, 15.0, 3.75, 0.3)
    return None


def _cost_usd(model, tok):
    """Estimated USD for one model's deduped token totals, or None for an unpriced model.
    `tok` keys: input, output, cache_read, cc_5m, cc_1h."""
    p = _price_for(model)
    if p is None:
        return None
    inp, out, cw5, cr = p
    return (tok['input'] * inp + tok['output'] * out + tok['cache_read'] * cr
            + tok['cc_5m'] * cw5 + tok['cc_1h'] * inp * _CACHE_1H_INPUT_MULT) / 1_000_000.0


# ---------------------------------------------------------------------------
# Topic slug (v4 `topic-slug` logic, reused internally for manifest `suggested_slug`)
# ---------------------------------------------------------------------------

# Pass-through layout dirs: common roots where EVERY file sits under them ('src/…'), so the
# segment carries layout, not topic — descend past them, like dot-dirs, to a content-bearing one.
_PASS_THROUGH_DIRS = {'src', 'src-tauri'}


def _bucket_winner(counts):
    """The plurality bucket from a Counter, or 'general' if empty or tied (a tie is genuinely
    ambiguous — no single topic)."""
    if not counts:
        return 'general'
    ranked = counts.most_common()
    top, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return 'general'
    return top


def _bucket_paths(paths, cwd, ignored_buckets):
    """Count slugified first-content-bearing-segment buckets for file paths relative to cwd.
    Container segments — dot-dirs (`.claude/`, `.github/`) and `_PASS_THROUGH_DIRS` — carry
    layout, not topic, so they are descended past; a path through an ignored dir, outside cwd
    (different drive, or ../-prefixed), or with no content-bearing segment (a bare dotfile) is
    skipped. A filename segment uses its stem. CamelCase boundaries are hyphenated BEFORE
    lowercasing so acronym runs survive intact (OpenMeteoAPI -> open-meteo-api, not
    open-meteo-a-p-i)."""
    counts = Counter()
    for fp in paths:
        if not fp:
            continue
        try:
            rel = os.path.relpath(os.path.abspath(fp), cwd)
        except ValueError:
            continue  # different Windows drive — outside the project
        rel = rel.replace('\\', '/')
        if rel == '..' or rel.startswith('../'):
            continue  # outside the project root
        parts = [p for p in rel.split('/') if p and p != '.']
        if not parts:
            continue
        i = 0
        while i < len(parts) - 1 and (parts[i].startswith('.')
                                      or parts[i].lower() in _PASS_THROUGH_DIRS):
            if parts[i].lower() in ignored_buckets:
                i = -1
                break
            i += 1
        first = parts[i] if i >= 0 else ''
        if not first or first.startswith('.'):
            continue  # ignored dir on the descent, or nothing but dot-segments
        raw = os.path.splitext(first)[0] if i == len(parts) - 1 else first
        bucket = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '-', raw).lower()
        if bucket in ignored_buckets:
            continue
        counts[bucket] += 1
    return counts


def _suggested_slug(paths, project_dir):
    """Derive a topic slug from the session's git footprint, bucketed by the first
    content-bearing path segment relative to the project root (the last record's cwd — the digest may run from anywhere, so it
    does NOT read os.getcwd() like the v4 subcommand did). Throwaway scratch dirs never represent
    the topic. A heuristic: the skill body may override it."""
    if not project_dir:
        return 'general'
    return _bucket_winner(_bucket_paths(paths, os.path.abspath(project_dir), {'.debug'}))


# ---------------------------------------------------------------------------
# Digest — the single scan pass + budgeted rendering
# ---------------------------------------------------------------------------

# `ballast: <hook-name> <sep> <clause>` up to the FIRST separator, where <sep> is an em dash or a
# plain '--' (at least one hook emits '--' with a variable clause tail). Some real fires have no
# separator at all ("⚓ ballast: principles loaded") — that's the genuine fallback case (tally by
# the full line), not a regex bug to chase.
_HOOK_FIRE_NAME_RE = re.compile(r'ballast:\s*(.+?)\s*(?:—|--)')
# A PreToolUse hard block (hook exit 2) surfaces as an is_error tool_result, NOT a
# `hook_blocking_error` attachment: `<Event>:<Tool> hook error: [<hook command>]: <reason>`. A
# structured deny carries no `[<command>]` segment, so the prefix is the only name available.
_HOOK_BLOCK_RE = re.compile(r'^(\w+:\S+) hook error: (?:\[(.*?)\]: )?')
# The hook's name inside its command: the `run.sh <name>` dispatcher arg, else a script's stem.
_HOOK_CMD_NAME_RE = re.compile(r'run\.sh"?\s+([\w.-]+)|([\w-]+)\.(?:sh|py)(?![\w.])')
# The harness's interrupt marker, on a tool_result or as its own user line.
_INTERRUPT_MARK = 'Request interrupted by user'
# A record whose largest single content payload exceeds this is "oversized" (an ANOMALIES signal:
# one Read/Bash result that alone dominates a context window). Measured on content length, not the
# raw line — `load_lines` yields parsed dicts only.
_OVERSIZE_CHARS = 50000
# Bounds of the oversize probe (`_max_str_len`). Depth 5 is what the two real shapes need:
# `toolUseResult.file.base64` (a PDF read) and `message.content[].source.data` (a pasted image).
# The node budget caps how many CONTAINERS the probe descends into, so a pathological record can't
# turn the scan super-linear.
_OVERSIZE_DEPTH = 5
_OVERSIZE_NODES = 2000
# Per-side retention for a very long user prompt. >= 2x the 4096 per-prompt cap ceiling, so the
# rendered head+tail elision is always computed from retained text, never from a lossy clip.
_CLIP_KEEP = 8192
# Timeline degradation ladder: (assistant snippet cap, tool-arg head cap, user per-prompt cap).
# User cap degrades LAST and only when USER TURNS alone exceeds 40% of budget (see _build_digest);
# 1024 is the floor — the section is the adjudication source and is never cut below it.
_LADDER = ((280, 160, 4096), (120, 160, 4096), (120, 80, 4096), (120, 80, 2048), (120, 80, 1024))
# The ladder's UNDEGRADED user cap — derived, so the two can never drift apart.
_USER_CAP_MAX = _LADDER[0][2]
_IDLE_GAP_SEC = 15 * 60


def _parse_ts(ts):
    """ISO timestamp -> epoch seconds (float), or None. Tolerates the trailing 'Z' and sub-second
    precision beyond microseconds (not every Python build's fromisoformat accepts either)."""
    if not ts or not isinstance(ts, str):
        return None
    t = ts.strip().replace('Z', '+00:00')
    m = re.match(r'^(.*\.\d{6})\d*(.*)$', t)
    if m:
        t = m.group(1) + m.group(2)
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _hhmm(ts):
    """'HH:MM' (UTC) from an ISO timestamp, or '--:--'."""
    if isinstance(ts, str) and len(ts) >= 16 and ts[10:11] == 'T':
        return ts[11:16]
    return '--:--'


def _cut(s, cap):
    """One-line, cap-length rendering of `s` (whitespace collapsed, ellipsis when cut)."""
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s if len(s) <= cap else s[:cap].rstrip() + '…'


def _clip(text):
    """(head, tail, total_len) — a bounded retention of a user prompt. Retains 2x the cap ceiling
    per side so a multi-megabyte paste can't hold the scan's memory hostage while every renderable
    cap still cuts from real text."""
    n = len(text)
    if n <= 2 * _CLIP_KEEP:
        return text, '', n
    return text[:_CLIP_KEEP], text[-_CLIP_KEEP:], n


def _render_clip(clip, cap):
    """Render a clipped prompt at `cap` chars: verbatim when it fits, else middle-elided
    head+tail with an explicit `[... elided N chars ...]` marker. Returns (text, was_elided)."""
    head, tail, n = clip
    if n <= cap:
        return head, False
    h = cap * 3 // 4
    t = cap - h
    tail_src = tail if tail else head
    return f'{head[:h]}\n\n[... elided {n - h - t} chars ...]\n\n{tail_src[-t:]}', True


def _max_str_len(obj, threshold):
    """True if any string nested up to `_OVERSIZE_DEPTH` levels inside `obj` is longer than
    `threshold`.

    The oversized-record probe. It walks the PARSED record (never re-serializes it — that would
    cost a copy of the transcript) and short-circuits on the first hit, with a node budget so a
    pathological record can't turn the scan super-linear.

    Strings are length-tested AT EXTEND TIME and only CONTAINERS go on the stack: pushing strings
    onto a LIFO stack let the node budget starve them in a wide record (the last-pushed keys pop
    first, so an oversized payload under an early key of a 2000+-key record was never reached).
    The budget now bounds container descents, which is what the super-linear guard was ever about.
    """
    if isinstance(obj, str):
        return len(obj) > threshold
    stack, budget = [(obj, 0)], _OVERSIZE_NODES
    while stack and budget > 0:
        o, d = stack.pop()
        budget -= 1
        if d >= _OVERSIZE_DEPTH:
            continue
        if isinstance(o, dict):
            values = o.values()
        elif isinstance(o, list):
            values = o[:64]
        else:
            continue
        for v in values:
            if isinstance(v, str):
                if len(v) > threshold:
                    return True
            elif isinstance(v, (dict, list)):
                stack.append((v, d + 1))
    return False


def _result_text(content, limit=4096):
    """Bounded flatten of a tool_result content value — only the head matters (error heads,
    interrupt markers, git-commit output), and the whole payload can be megabytes."""
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        parts, n = [], 0
        for c in content:
            s = c.get('text', '') if isinstance(c, dict) else str(c)
            if not isinstance(s, str):
                continue
            parts.append(s[:limit])
            n += len(s)
            if n >= limit:
                break
        return ' '.join(parts)[:limit]
    return '' if content is None else str(content)[:limit]


_TOOL_ARG_KEYS = ('command', 'file_path', 'notebook_path', 'pattern', 'path', 'url', 'query',
                  'prompt', 'args', 'description', 'skill', 'name', 'script')


def _tool_arg(inp):
    """The one representative argument of a tool call (first populated string among the known arg
    keys), else a compact key list — enough to tell two calls of the same tool apart."""
    if not isinstance(inp, dict):
        return ''
    v = _first_str(inp, *_TOOL_ARG_KEYS)
    return _cut(v, 400) if v else ', '.join(sorted(inp)[:6])


def _first_str(inp, *keys):
    """First non-blank string among `keys` in a tool-input dict, else None. Tool inputs in the wild
    carry non-string values, and one used as a Counter key or rendered as text would crash the scan."""
    for k in keys:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _hook_fire_names(content):
    """Every `<name>` from a hook_system_message's `ballast: <name> — <clause>` segments, in order;
    empty when none (caller falls back to the full line). One message can carry several banners
    joined by ` · ` (shell-guards merges its guards' messages), and each names a fire."""
    if not isinstance(content, str):
        return []
    return [m.group(1).strip() for m in _HOOK_FIRE_NAME_RE.finditer(content)]


def _hook_block_name(command, fallback):
    """Inventory key for one hook hard block: `<hook-name> (block)`, the name read from the hook's
    command when it has one, else `fallback` (the `<Event>:<Tool>` label)."""
    m = _HOOK_CMD_NAME_RE.search(command) if isinstance(command, str) else None
    return f'{(m.group(1) or m.group(2)) if m else fallback} (block)'


_GIT_TIMEOUT_SEC = 10


def _git(repo, *args):
    """stdout of one read-only git command run in `repo`, or None on ANY failure (no git binary,
    not a repo, timeout, non-zero exit) — the footprint fails open. Optional locks and fsmonitor
    are off so reading a repo never writes its index or spawns its configured helpers."""
    try:
        r = subprocess.run(['git', '--no-optional-locks', '-c', 'core.quotepath=off',
                            '-c', 'core.fsmonitor=false', '-C', repo, *args],
                           capture_output=True, timeout=_GIT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def _git_footprint(cwd, first_ts, last_ts):
    """The session's footprint read from git rather than scraped from tool calls (shell-driven
    edits and `git commit -q` leave no parseable tool trace): commits in the session's window
    (first → last transcript timestamp) in the cwd's repo, the files those commits name, plus the
    uncommitted tree. Returns {root, commits: [(sha, subject)], files: Counter, dirty: set} with
    repo-relative paths, or None when git or the repo is unavailable (the section is omitted)."""
    a, b = _parse_ts(first_ts), _parse_ts(last_ts)
    if not cwd or a is None or b is None or not os.path.isdir(cwd):
        return None
    root = (_git(cwd, 'rev-parse', '--show-toplevel') or '').strip()
    if not root:
        return None
    log = _git(root, 'log', '--reverse', f'--since=@{int(a)}', f'--until=@{int(b) + 1}',
               '--format=%x1e%h%x09%s', '--name-only')
    status = _git(root, 'status', '--porcelain', '-z')
    if log is None or status is None:
        return None
    commits, files = [], Counter()
    for rec in log.split('\x1e')[1:]:
        head, _, names = rec.partition('\n')
        sha, _, subject = head.partition('\t')
        commits.append((sha, _cut(subject, 100)))
        for n in names.splitlines():
            if n.strip():
                files[n.strip()] += 1
    dirty, entries, i = set(), status.split('\0'), 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        if e[0] in 'RC':
            i += 1  # a rename/copy's next -z entry is its origin path
        dirty.add(e[3:])
    return {'root': root, 'commits': commits, 'files': files, 'dirty': dirty}


def _scan(path):
    """THE single pass. Walks the transcript once and returns every fact the digest renders.

    Everything bounded on the way in: assistant snippets and tool args are stored at their maximum
    renderable cap, tool results are read head-only, user prompts are clipped to `_CLIP_KEEP` per
    side. So peak memory tracks the number of records, never the transcript's bytes.
    """
    s = {
        'transcript': os.path.abspath(path), 'lines': 0, 'first_ts': '', 'last_ts': '',
        'users': [], 'events': [], 'kinds': Counter(), 'tools': Counter(), 'tool_errors': Counter(),
        'skills': Counter(), 'slash': Counter(), 'hooks': Counter(), 'agent_dispatch': Counter(),
        'mcp': {}, 'api_errors': [], 'interrupts': [],
        'oversized': 0, 'compact_boundaries': 0, 'assistant_ids': set(), 'assistant_keyless': 0,
        'usage': {}, 'usage_keyless': {}, 'drift': {}, 'chain': [], 'session_id': '',
        'project_dir': '',
    }
    events, pending, seen_goals = s['events'], {}, set()
    per_id = s['usage']            # message.id -> {'model': str, field: max-seen}
    cur_user = 0

    for d in load_lines(path):
        if not isinstance(d, dict):
            continue
        s['lines'] += 1
        ts = d.get('timestamp', '')
        if isinstance(ts, str) and ts:
            if not s['first_ts'] or ts < s['first_ts']:
                s['first_ts'] = ts
            if ts > s['last_ts']:
                s['last_ts'] = ts
        _observe_drift(s['drift'], d)
        # One oversize probe per RECORD (not per rendered block): the payload that matters — a 12 MB
        # PDF read, a pasted image set — rides fields the digest never renders, so measuring only
        # rendered content would report zero on exactly the records worth flagging.
        if _max_str_len(d, _OVERSIZE_CHARS):
            s['oversized'] += 1
        sid = d.get('sessionId')
        if isinstance(sid, str) and _UUID_RE.fullmatch(sid) and sid not in s['chain']:
            s['chain'].append(sid)
        cwd = d.get('cwd')
        if isinstance(cwd, str) and cwd:
            s['project_dir'] = cwd

        # Tool RESULTS pair BEFORE turn classification: one user line can be BOTH a genuine turn and
        # the result that closes a pending tool_use — an AskUserQuestion ANSWER rides a tool_result
        # block. Classifying first and `continue`ing left that tool_use pending forever, so its
        # timeline row read `?` and it inflated `unpaired_tools` (a false ANOMALIES entry). Ordinary
        # tool-result lines (which the turn gate drops) are unaffected: they pair here instead of at
        # the tail of the loop, and nothing else in the pass depends on the order.
        if d.get('type') == 'user':
            rmsg = d.get('message')
            rcontent = rmsg.get('content') if isinstance(rmsg, dict) else None
            if isinstance(rcontent, list):
                for c in rcontent:
                    if not isinstance(c, dict) or c.get('type') != 'tool_result':
                        continue
                    head = _result_text(c.get('content'))
                    ev = pending.pop(c.get('tool_use_id'), None)
                    is_err = bool(c.get('is_error')) or head.lstrip().startswith('<tool_use_error>')
                    if ev is not None:
                        ev['done'] = True
                        if is_err:
                            ev['err'] = _cut(head, 120)
                            s['tool_errors'][ev.get('n2', '?')] += 1
                    elif is_err:
                        s['tool_errors']['?'] += 1
                    if is_err:
                        hb = _HOOK_BLOCK_RE.match(head)
                        if hb:
                            s['hooks'][_hook_block_name(hb.group(2), hb.group(1))] += 1
                    if _INTERRUPT_MARK in head[:400]:
                        s['interrupts'].append(cur_user)
                        events.append({'k': 'interrupt', 'ep': _parse_ts(ts)})

        turn = _user_turn(d)
        if turn is not None:
            kind, text = turn
            if kind == 'goal':
                key = text.strip()
                if key in seen_goals:      # met=false / met=true bookends repeat the condition
                    continue
                seen_goals.add(key)
            cur_user += 1
            s['users'].append({'n': cur_user, 'ts': ts, 'kind': kind, 'clip': _clip(text)})
            s['kinds'][kind] += 1
            if kind == 'slash_command':
                s['slash'][text.split()[0] if text.split() else text] += 1
            events.append({'k': 'user', 't': _hhmm(ts), 'ep': _parse_ts(ts), 'n': cur_user,
                           'kind': kind, 'prev': _cut(text, 100)})
            if _INTERRUPT_MARK in text[:200]:
                s['interrupts'].append(cur_user)
                events.append({'k': 'interrupt', 'ep': _parse_ts(ts)})
            continue

        t = d.get('type')
        if d.get('isCompactSummary') is True:
            s['compact_boundaries'] += 1
            events.append({'k': 'compact', 'ep': _parse_ts(ts)})
            continue
        if t == 'system':
            st = d.get('subtype')
            if st == 'compact_boundary':
                s['compact_boundaries'] += 1
                events.append({'k': 'compact', 'ep': _parse_ts(ts)})
            elif st == 'api_error':
                s['api_errors'].append(_cut(_text_from_content(d.get('content')), 160)
                                       or 'api_error')
            continue
        if t == 'attachment':
            att = d.get('attachment')
            if isinstance(att, dict) and att.get('type') == 'hook_system_message':
                content = att.get('content')
                if isinstance(content, str) and content.strip():
                    for name in _hook_fire_names(content) or [content.strip()]:
                        s['hooks'][name] += 1
            elif isinstance(att, dict) and att.get('type') == 'hook_blocking_error':
                # The attachment-shaped block (seen from project-local hooks); its paired
                # tool_result carries only the reason, so the tool_result tally above can't double it.
                be = att.get('blockingError')
                s['hooks'][_hook_block_name(be.get('command') if isinstance(be, dict) else None,
                                            _first_str(att, 'hookName') or 'hook')] += 1
            continue

        msg = d.get('message')
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')

        if t == 'assistant':
            mid = msg.get('id')
            if mid:
                s['assistant_ids'].add(mid)
            else:
                s['assistant_keyless'] += 1
            u = msg.get('usage')
            if isinstance(u, dict):
                cc = u.get('cache_creation')
                cc5 = cc.get('ephemeral_5m_input_tokens', 0) or 0 if isinstance(cc, dict) else 0
                cc1h = cc.get('ephemeral_1h_input_tokens', 0) or 0 if isinstance(cc, dict) else 0
                if not isinstance(cc, dict):
                    # No TTL breakdown (older records) — price the whole write at the 5m rate.
                    cc5 = u.get('cache_creation_input_tokens', 0) or 0
                vals = {'input': u.get('input_tokens', 0) or 0,
                        'output': u.get('output_tokens', 0) or 0,
                        'cache_create': u.get('cache_creation_input_tokens', 0) or 0,
                        'cache_read': u.get('cache_read_input_tokens', 0) or 0,
                        'cc_5m': cc5, 'cc_1h': cc1h}
                model = msg.get('model') or '?'
                if mid:
                    # Dedup: one API response logs as several content-block lines repeating the
                    # SAME usage — take the MAX per (message.id, field), never the sum.
                    slot = per_id.setdefault(mid, {'model': model, **{k: 0 for k in vals}})
                    for k, v in vals.items():
                        if v > slot[k]:
                            slot[k] = v
                else:
                    slot = s['usage_keyless'].setdefault(model, {k: 0 for k in vals})
                    for k, v in vals.items():
                        slot[k] += v
            if isinstance(content, str):
                # Plain-string assistant content exists in the wild (CC 2.1.277/2.1.281 fixes); it
                # is one text block.
                content = [{'type': 'text', 'text': content}]
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                ctype = c.get('type')
                if ctype == 'text':
                    txt = c.get('text', '')
                    if txt.strip():
                        if txt.lstrip()[:40].startswith('API Error'):
                            s['api_errors'].append(_cut(txt, 160))
                        events.append({'k': 'text', 't': _hhmm(ts), 'ep': _parse_ts(ts),
                                       's': _cut(txt, _LADDER[0][0])})
                elif ctype == 'tool_use':
                    name = c.get('name', '?')
                    inp = c.get('input')
                    if not isinstance(inp, dict):
                        inp = {}
                    s['tools'][name] += 1
                    server, tool = _split_mcp(name)
                    if server is not None:
                        m = s['mcp'].setdefault(server, {'tools': set(), 'calls': 0})
                        m['tools'].add(tool)
                        m['calls'] += 1
                    arg = _tool_arg(inp)
                    if name == 'SubagentHandback':
                        # An auto-mode leaf's report rides this call's `input.message`, not its
                        # trailing end_turn text — render it as the leaf's text.
                        report = _first_str(inp, 'message')
                        if report:
                            events.append({'k': 'text', 't': _hhmm(ts), 'ep': _parse_ts(ts),
                                           's': _cut('[handback] ' + report, _LADDER[0][0])})
                        continue
                    if name in ('Agent', 'Task'):
                        atype = _first_str(inp, 'subagent_type', 'name') or '?'
                        s['agent_dispatch'][atype] += 1
                        model = inp.get('model')
                        ev = {'k': 'agent', 't': _hhmm(ts), 'ep': _parse_ts(ts),
                              'n2': f'{atype}|{model}' if isinstance(model, str) and model else atype,
                              'a': arg}
                    elif name in ('Skill', 'Workflow'):
                        label = _first_str(inp, 'skill', 'name') or name
                        s['skills'][label] += 1
                        ev = {'k': 'skill', 't': _hhmm(ts), 'ep': _parse_ts(ts), 'n2': label,
                              'a': _first_str(inp, 'args') or arg}
                    else:
                        ev = {'k': 'tool', 't': _hhmm(ts), 'ep': _parse_ts(ts), 'n2': name,
                              'a': arg, 'done': False, 'err': None}
                    events.append(ev)
                    bid = c.get('id')
                    if bid is not None:
                        pending[bid] = ev
            continue

    # Idle gaps — a post-pass over the ordered events, so a >15 min hole in the session reads as a
    # break rather than as two adjacent lines pretending to be consecutive work.
    with_idle, prev = [], None
    for ev in events:
        ep = ev.get('ep')
        if ep is not None and prev is not None and ep - prev > _IDLE_GAP_SEC:
            with_idle.append({'k': 'idle', 'm': int((ep - prev) // 60)})
        if ep is not None:
            prev = ep
        with_idle.append(ev)
    s['events'] = with_idle
    s['session_id'] = _session_uuid_from_path(path)
    s['assistant_responses'] = len(s['assistant_ids']) + s['assistant_keyless']
    s['transcript_bytes'] = os.path.getsize(path) if os.path.exists(path) else 0
    g = s['git'] = _git_footprint(s['project_dir'], s['first_ts'], s['last_ts'])
    s['suggested_slug'] = _suggested_slug(
        [os.path.join(g['root'], p) for p in set(g['files']) | g['dirty']] if g else [],
        s['project_dir'])
    s['unpaired_tools'] = len(pending)

    # Subagent sidecars (own files, own passes) — the v4 `subagents` roster, folded into INVENTORY.
    agents = []
    for sidedir in _subagent_dirs(path, [u for u in s['chain'] if u != s['session_id']]):
        agents.extend(_collect_subagents(sidedir))
    s['agents'] = agents
    s['subagent_tokens'] = sum(a['tokens'] for a in agents)

    # Per-model totals, deduped by message.id (MAX per field) then summed under each model.
    models = {}
    for slot in per_id.values():
        m = models.setdefault(slot['model'], {k: 0 for k in
                                              ('input', 'output', 'cache_create', 'cache_read',
                                               'cc_5m', 'cc_1h')})
        for k in m:
            m[k] += slot[k]
    for model, slot in s['usage_keyless'].items():
        m = models.setdefault(model, {k: 0 for k in slot})
        for k in slot:
            m[k] += slot[k]
    s['models'] = models
    return s


# --- section renderers (module-level so a failure in one is isolated — and so a test can force
# --- one to raise and assert the fail-open marker) ------------------------------------------

def _model_rows(scan):
    """[(model, tokens_dict, est_cost_usd|None)] sorted by total tokens desc."""
    rows = []
    for model, tok in scan['models'].items():
        total = tok['input'] + tok['output'] + tok['cache_create'] + tok['cache_read']
        rows.append((model, tok, total, _cost_usd(model, tok)))
    rows.sort(key=lambda r: -r[2])
    return rows


def _sec_session(scan, caps):
    total_cost = sum(c for _, _, _, c in _model_rows(scan) if c is not None)
    dur = ''
    a, b = _parse_ts(scan['first_ts']), _parse_ts(scan['last_ts'])
    if a is not None and b is not None:
        mins = int((b - a) // 60)
        dur = f' ({mins // 60}h {mins % 60}m)'
    models = ', '.join(f'{_short_model(m)} {t:,} tok'
                       + (f' ~${c:.2f}' if c is not None else ' (unpriced)')
                       for m, _, t, c in _model_rows(scan)[:4]) or '—'
    kinds = ', '.join(f'{k} {n}' for k, n in scan['kinds'].most_common()) or '—'
    drift_rows = _drift_rows(scan['drift'])
    project = os.path.basename((scan['project_dir'] or '').rstrip('\\/')) or '?'
    drift_note = 'none' if not drift_rows else f'{len(drift_rows)} unknown shape(s) — see ANOMALIES'
    lines = [
        f"- **Project:** {project} (`{scan['project_dir'] or '?'}`)",
        f"- **Transcript:** `{scan['transcript']}` — {scan['lines']:,} lines, "
        f"{scan['transcript_bytes'] / 1048576:.1f} MB",
        f"- **Window (UTC):** {scan['first_ts'] or '?'} → {scan['last_ts'] or '?'}{dur}",
        f'- **Models:** {models}',
        f'- **Est. cost:** ~${total_cost:.2f} (approximate, ccusage-aligned snapshot)',
        f"- **User turns:** {len(scan['users'])} ({kinds})",
        f"- **Assistant responses:** {scan['assistant_responses']}",
        f"- **Tool calls:** {sum(scan['tools'].values())} "
        f"({sum(scan['tool_errors'].values())} errors)",
        f"- **Subagents:** {len(scan['agents'])} dispatched, {scan['subagent_tokens']:,} tokens",
        f"- **Compact boundaries:** {scan['compact_boundaries']}",
        f"- **Suggested slug:** {scan['suggested_slug']}",
        f'- **Drift:** {drift_note}',
    ]
    if caps.get('focus'):
        lines.append(f"- **Focus:** {caps['focus']}")
    return '\n'.join(lines)


def _sec_user_turns(scan, caps):
    """VERBATIM and protected: the adjudication source. Harness-injected <system-reminder>/ide_*
    spans are stripped from the DISPLAYED text; nothing the user typed is paraphrased or dropped."""
    cap = caps['user']
    out = ['_Verbatim user input — the adjudication source. Injected harness spans '
           '(<system-reminder>, ide_*) stripped; nothing else altered._', '']
    elided = 0
    for u in scan['users']:
        text, was = _render_clip(u['clip'], cap)
        elided += 1 if was else 0
        out.append(f"### U{u['n']} [{_hhmm(u['ts'])}] ({u['kind']})")
        out.append('')
        out.append(text if text.strip() else '_(empty)_')
        out.append('')
    caps['stats']['user_prompts_truncated'] = elided
    if not scan['users']:
        out.append('_No user turns in this transcript._')
    return '\n'.join(out)


def _event_line(ev, caps):
    k = ev['k']
    if k == 'compact':
        return '--- COMPACT ---'
    if k == 'idle':
        return f"[idle ~{ev['m']}m]"
    if k == 'interrupt':
        return '[interrupted]'
    t = ev.get('t', '--:--')
    if k == 'user':
        return f"[{t}] U{ev['n']} ({ev['kind']}) {ev['prev']}"
    if k == 'text':
        return f"[{t}] {_cut(ev['s'], caps['assistant'])}"
    if k == 'agent':
        return f"[{t}] [Agent: {ev['n2']} → \"{_cut(ev['a'], caps['tool_arg'])}\"]"
    if k == 'skill':
        return f"[{t}] [Skill: {ev['n2']} {_cut(ev['a'], caps['tool_arg'])}]".rstrip()
    status = 'ERR' if ev.get('err') else ('ok' if ev.get('done') else '?')
    line = f"[{t}] [Tool: {ev['n2']} → {_cut(ev['a'], caps['tool_arg'])} ({status})]"
    if ev.get('err'):
        line += f" {ev['err']}"
    return line


def _sec_timeline(scan, caps):
    lines = [_event_line(ev, caps) for ev in scan['events']]
    body = '\n'.join(lines)
    caps['stats']['records_truncated'] = 0
    limit = caps.get('timeline_max')
    if limit is not None and len(body.encode('utf-8')) > limit:
        # Last resort after the cap ladder: keep the session's opening and its ending (where the
        # verdict-bearing work lives) and elide the middle, reporting the count in `truncation`.
        keep = max(10, limit // 100)
        head_n, tail_n = keep * 3 // 5, keep - keep * 3 // 5
        while head_n + tail_n < len(lines):
            head = lines[:head_n]
            tail = lines[len(lines) - tail_n:]
            dropped = len(lines) - head_n - tail_n
            body = '\n'.join(head + [f'[... {dropped} timeline events elided ...]'] + tail)
            if len(body.encode('utf-8')) <= limit:
                break
            head_n, tail_n = head_n * 2 // 3, tail_n * 2 // 3
            if head_n + tail_n < 4:
                body = f'[... {len(lines)} timeline events elided ...]'
                dropped = len(lines)
                break
        caps['stats']['records_truncated'] = max(0, len(lines) - head_n - tail_n)
    return '```\n' + body + '\n```' if body else '_No events._'


def _table(header, rows, cap=25):
    """A markdown table with a labeled remainder so a truncated list still reconciles."""
    if not rows:
        return '_none_'
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join(['---'] * len(header)) + '|']
    for r in rows[:cap]:
        out.append('| ' + ' | '.join(str(x) for x in r) + ' |')
    if len(rows) > cap:
        out.append(f'| _+{len(rows) - cap} more_ | ' + ' | '.join([''] * (len(header) - 1)) + ' |')
    return '\n'.join(out)


def _sec_inventory(scan, caps):
    parts = ['**Tools**', '', _table(['Tool', 'Calls', 'Errors'],
                                     [(n, c, scan['tool_errors'].get(n, 0))
                                      for n, c in scan['tools'].most_common()], cap=30), '']
    parts += ['**Skills invoked**', '',
              _table(['Skill', 'Count'], scan['skills'].most_common()), '']
    agent_rows = {}
    for a in scan['agents']:
        key = (a['agent_type'], _short_model(a['model']))
        slot = agent_rows.setdefault(key, [0, 0])
        slot[0] += 1
        slot[1] += a['tokens']
    rows = sorted(([k[0], k[1], v[0], f'{v[1]:,}'] for k, v in agent_rows.items()),
                  key=lambda r: -int(r[3].replace(',', '')))
    parts += ['**Agents dispatched** '
              f"(main-transcript dispatch calls: {sum(scan['agent_dispatch'].values())})", '',
              _table(['Agent type', 'Model', 'Count', 'Tokens'], rows, cap=_AGENT_SAMPLE_CAP), '']
    parts += ['**MCP tools**', '',
              _table(['Server', 'Distinct tools', 'Calls'],
                     sorted(((srv, len(v['tools']), v['calls']) for srv, v in scan['mcp'].items()),
                            key=lambda r: -r[2])), '']
    parts += ['**Hooks observed**', '',
              _table(['Hook', 'Fires'], scan['hooks'].most_common()), '']
    parts += ['**Slash commands**', '',
              _table(['Command', 'Count'], scan['slash'].most_common()), '']
    return '\n'.join(parts)


def _sec_files(scan, caps):
    g = scan['git']
    if g is None:
        return '_Omitted: no git, or the session cwd is not a git repo._'
    paths = sorted(set(g['files']) | g['dirty'], key=lambda p: (-g['files'].get(p, 0), p))
    rows = [(p, g['files'].get(p, 0), 'yes' if p in g['dirty'] else '') for p in paths]
    parts = [f"_From git in `{g['root']}`: commits in the session window, plus the uncommitted "
             'tree (which may predate the session)._', '',
             _table(['File', 'Commits', 'Uncommitted'], rows, cap=60), '']
    if g['commits']:
        parts += ['**Commits**', '', _table(['SHA', 'Subject'], g['commits'], cap=40)]
    else:
        parts += ['**Commits:** _none in the session window_']
    return '\n'.join(parts)


def _sec_anomalies(scan, caps):
    parts = []
    if not scan['users'] and scan['assistant_ids']:
        parts += [f"**No user turns captured** across {len(scan['assistant_ids'])} responses — "
                  f"almost certainly a user-turn parser gap, not a silent session; report it.", '']
    if scan['tool_errors']:
        parts += ['**Tool errors**', '',
                  _table(['Tool', 'Errors'], scan['tool_errors'].most_common()), '']
    else:
        parts += ['**Tool errors:** none', '']
    if scan['interrupts']:
        refs = ', '.join(f'U{n}' for n in scan['interrupts'][:40])
        parts += [f"**Interruptions:** {len(scan['interrupts'])} (after {refs})", '']
    else:
        parts += ['**Interruptions:** none', '']
    if scan['api_errors']:
        parts += [f"**API/stream errors:** {len(scan['api_errors'])}", '']
        parts += ['- ' + e for e in scan['api_errors'][:10]] + ['']
    else:
        parts += ['**API/stream errors:** none', '']
    rows = _drift_rows(scan['drift'])
    if rows:
        parts += ['**Unknown transcript shapes (drift)**', '',
                  _table(['Axis', 'Value', 'Count'], [(k, v, c) for (k, v), c in rows]), '']
    else:
        parts += ['**Unknown transcript shapes (drift):** none', '']
    parts += [f"**Oversized records** (a field >{_OVERSIZE_CHARS:,} chars — a PDF/image payload or "
              f"a giant tool result): {scan['oversized']}"]
    if scan['unpaired_tools']:
        parts += ['', f"**Tool calls with no recorded result:** {scan['unpaired_tools']}"]
    return '\n'.join(parts)


_SECTIONS = (
    ('SESSION', '_sec_session'),
    ('USER TURNS', '_sec_user_turns'),
    ('TIMELINE', '_sec_timeline'),
    ('INVENTORY', '_sec_inventory'),
    ('FILES TOUCHED', '_sec_files'),
    ('ANOMALIES', '_sec_anomalies'),
)


def _render_body(scan, caps):
    """Render every section. FAIL-OPEN: a section generator that raises degrades to a
    `[section unavailable: …]` marker — a broken section never costs the other five, and never
    a non-zero exit. Dispatch goes through globals() so a test can force one to raise."""
    out = []
    for title, fname in _SECTIONS:
        try:
            body = globals()[fname](scan, caps)
        except Exception as e:                      # noqa: BLE001 — fail-open is the contract
            body = f'[section unavailable: {e}]'
        out.append(f'## {title}\n\n{body}\n')
    return '\n'.join(out)


def _compose(manifest, body, slug):
    """digest.md = title + a fenced copy of the manifest + the section bodies."""
    return (f'# Session digest — {slug}\n\n```json\n'
            + json.dumps(manifest, indent=2, sort_keys=False, default=str)
            + '\n```\n\n' + body)


def _build_digest(scan, budget, manifest):
    """Two-pass budgeted render. Pass 1 measures at default caps; pass 2 walks the degradation
    ladder (assistant snippet → tool-arg head → user per-prompt cap LAST, and only when USER TURNS
    alone exceeds 40% of budget). If the file still exceeds the 1.25x hard ceiling after the ladder,
    the TIMELINE is middle-elided to fit — USER TURNS is never cut below its 1024-char floor, and
    overflow past the ceiling is allowed for that section alone."""
    ceiling = int(budget * 1.25)
    user_full = None
    text, used = None, None
    for a_cap, t_cap, u_cap in _LADDER:
        caps = {'assistant': a_cap, 'tool_arg': t_cap, 'user': u_cap, 'timeline_max': None,
                'focus': manifest.get('focus'), 'stats': {}}
        if u_cap < _USER_CAP_MAX:
            if user_full is None:
                probe = dict(caps, user=_USER_CAP_MAX, stats={})
                user_full = len(_sec_user_turns(scan, probe).encode('utf-8'))
            if user_full <= budget * 0.40:
                continue   # the protected section isn't the bloat — don't degrade it
        text, used = _finalize(scan, caps, manifest), caps
        if len(text.encode('utf-8')) <= budget:
            break
    size = len(text.encode('utf-8'))
    if size > ceiling:
        timeline = len(_sec_timeline(scan, used).encode('utf-8'))
        used['timeline_max'] = max(1000, budget - (size - timeline))
        text = _finalize(scan, used, manifest)
    manifest['truncation'].update({
        'budget': budget,
        'records_truncated': used['stats'].get('records_truncated', 0),
        'user_prompts_truncated': used['stats'].get('user_prompts_truncated', 0),
    })
    return _finalize(scan, used, manifest), used


def _finalize(scan, caps, manifest):
    """Render body + the fenced manifest copy, converging `truncation.digest_bytes` on the file's
    own byte count (the value is inside the file it measures — a 3-step fixpoint settles it) and
    writing the settled count back onto `manifest` so digest.md and manifest.json agree."""
    body = _render_body(scan, caps)
    trunc = manifest.setdefault('truncation', {})
    text = _compose(manifest, body, scan['suggested_slug'])
    for _ in range(3):
        n = len(text.encode('utf-8'))
        if trunc.get('digest_bytes') == n:
            break
        trunc['digest_bytes'] = n
        text = _compose(manifest, body, scan['suggested_slug'])
    return text


def _manifest(scan, focus, resolved_via, elapsed):
    models = {}
    total_cost = 0.0
    for model, tok, _, cost in _model_rows(scan):
        models[model] = {'input': tok['input'], 'output': tok['output'],
                         'cache_read': tok['cache_read'], 'cache_create': tok['cache_create'],
                         'est_cost_usd': round(cost, 4) if cost is not None else None}
        if cost is not None:
            total_cost += cost
    a, b = _parse_ts(scan['first_ts']), _parse_ts(scan['last_ts'])
    return {
        'schema': 5,
        'transcript': scan['transcript'],
        'session_id': scan['session_id'],
        'project_dir': scan['project_dir'],
        'resolved_via': resolved_via,
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'transcript_lines': scan['lines'],
        'transcript_bytes': scan['transcript_bytes'],
        'first_ts': scan['first_ts'],
        'last_ts': scan['last_ts'],
        'wall_minutes': round((b - a) / 60.0, 1) if (a is not None and b is not None) else None,
        'user_turns': {'total': len(scan['users']), **dict(scan['kinds'])},
        'assistant_responses': scan['assistant_responses'],
        'tool_calls_total': sum(scan['tools'].values()),
        'subagents': {'count': len(scan['agents']), 'tokens': scan['subagent_tokens']},
        'compact_boundaries': scan['compact_boundaries'],
        'models': models,
        'total_est_cost_usd': round(total_cost, 4),
        'suggested_slug': scan['suggested_slug'],
        'git_window': {'since': scan['first_ts'], 'until': scan['last_ts']},
        'truncation': {'budget': 0, 'digest_bytes': 0, 'records_truncated': 0,
                       'user_prompts_truncated': 0},
        'focus': focus,
        'digest_generation_seconds': elapsed,
    }


def _guard_out_dir(out_dir, transcript):
    """Bounded-write rule (ported from v4 `bundle`): create `out_dir` if absent; refuse (exit 2) a
    non-empty dir with no manifest.json, or one whose manifest names a DIFFERENT transcript — never
    silently overwrite another session's digest. A parse failure counts as a mismatch."""
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        print(f'--out path exists and is not a directory: {out_dir}', file=sys.stderr)
        sys.exit(2)
    if os.path.isdir(out_dir):
        existing = os.listdir(out_dir)
        if existing and 'manifest.json' not in existing:
            print(f'refusing to write into a non-empty dir with no manifest.json: {out_dir} '
                  f'(pass an empty/new dir, or a dir this tool already wrote)', file=sys.stderr)
            sys.exit(2)
        if 'manifest.json' in existing:
            try:
                with open(os.path.join(out_dir, 'manifest.json'), encoding='utf-8') as f:
                    prior = json.load(f).get('transcript')
            except (OSError, ValueError):
                prior = None
            if prior != os.path.abspath(transcript):
                print(f'{out_dir} already holds a digest for a different transcript ({prior!r}) '
                      f'— pass a fresh --out', file=sys.stderr)
                sys.exit(2)
    else:
        os.makedirs(out_dir, exist_ok=True)


def digest(out_dir=None, session_id=None, transcript=None, budget=120000, focus=None):
    """One-pass session digest -> <out>/digest.md + <out>/manifest.json. Exit 0 on success.

    Transcript resolution: `--transcript` wins; else the `resolve` logic (session id / env —
    an explicit `--id` accepts a unique prefix and fails loudly rather than falling back;
    cwd-newest only when no id was given). `--out` defaults to a per-session cache dir, so the bounded-write
    guard never trips in normal use while still protecting an explicit shared dir.
    """
    import time
    t0 = time.perf_counter()
    # `--id` doubles as a transcript path: one documented flag serves both the live-session case and
    # a bench/scratch run against a copied .jsonl, so the skill body needs no second flag.
    if not transcript and session_id and (session_id.endswith('.jsonl')
                                          or os.path.isfile(session_id)):
        transcript, session_id = session_id, None
    if transcript:
        path, via = transcript, 'explicit'
    else:
        r = _resolve_transcript(session_id)
        path, via = r['live'], r['via']
        if path is None:
            print(r['error'] or 'could not resolve a transcript (no CLAUDE_CODE_SESSION_ID, '
                  'no --id/--transcript, and no session .jsonl under the cwd-derived projects dir)',
                  file=sys.stderr)
            sys.exit(2)
    if not os.path.exists(path):
        print(f'Transcript not found: {path}', file=sys.stderr)
        sys.exit(2)
    if not out_dir:
        out_dir = os.path.join(os.path.expanduser('~'), '.claude', '.cache', 'ballast', 'digest',
                               _session_uuid_from_path(path))
    _guard_out_dir(out_dir, path)

    scan = _scan(path)
    manifest = _manifest(scan, focus, via, None)
    text, caps = _build_digest(scan, budget, manifest)
    # Stamp the elapsed time and re-render once so the FENCED copy in digest.md is byte-identical
    # to manifest.json — the fixpoint in `_finalize` re-settles `digest_bytes` around the change.
    manifest['digest_generation_seconds'] = round(time.perf_counter() - t0, 3)
    text = _finalize(scan, caps, manifest)
    digest_path = os.path.join(out_dir, 'digest.md')
    with open(digest_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, default=str)
    print(digest_path)
    print(f"lines={scan['lines']} user_turns={len(scan['users'])} "
          f"digest_bytes={manifest['truncation']['digest_bytes']} budget={budget} "
          f"seconds={manifest['digest_generation_seconds']}")
    sys.exit(0)


def main():
    # Three subcommands, each with its own argument shape: `resolve` takes none, `digest` takes
    # flags only, `drift` takes exactly a transcript path.
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sub = sys.argv[1]
    if sub == 'resolve':
        resolve()
        return
    if sub == 'digest':
        args, kw = sys.argv[2:], {}
        flags = {'--out': 'out_dir', '--id': 'session_id', '--transcript': 'transcript',
                 '--budget': 'budget', '--focus': 'focus'}
        i = 0
        while i < len(args):
            key = flags.get(args[i])
            if key and i + 1 < len(args):
                kw[key] = args[i + 1]
                i += 2
            else:
                print(f'Unknown or incomplete digest argument: {args[i]}', file=sys.stderr)
                sys.exit(2)
        if 'budget' in kw:
            try:
                kw['budget'] = int(kw['budget'])
            except ValueError:
                print('--budget must be an integer byte count', file=sys.stderr)
                sys.exit(2)
        digest(**kw)
        return
    if sub != 'drift' or len(sys.argv) < 3:
        print(f'Unknown subcommand: {sub}' if sub != 'drift' else 'missing transcript path',
              file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    path = sys.argv[2]
    if not os.path.exists(path):
        print(f'Transcript not found: {path}', file=sys.stderr)
        sys.exit(2)
    drift(path)


if __name__ == '__main__':
    main()
