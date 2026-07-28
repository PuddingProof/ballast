#!/usr/bin/env python
"""
session-postmortem extraction helpers.

All subcommands take a transcript .jsonl path as the first arg.
Output is plain text on stdout for easy consumption by the skill.

Usage:
    python extract.py <subcommand> <transcript-path> [args]

Subcommands:
    resolve             Print this session's live transcript + PreCompact archive paths
                        (from $CLAUDE_CODE_SESSION_ID; takes NO transcript-path arg)
    compact-check       Exit code 0 = no compaction; 1 = compaction detected
                        (isCompactSummary recap OR a system/compact_boundary marker)
    time-window         Print "<min-iso> <max-iso>" timestamps
    user-msgs           Print real user messages chronologically (turns + AskUserQuestion answers
                        + /slash echoes + mid-turn steers + autopilot goals; skips tool_results,
                        isMeta/compact/sidechain/sdk-cli injections, bash-output echoes, task-notes)
    invocations         Print Skill/Agent/Workflow tool invocations with their results
    edits               Print Edit/Write/MultiEdit tool calls (timestamp, type, basename)
    assistant-text      Print assistant text blocks chronologically
    subagents           Per-subagent roster (agentType/dispatch label/model/tokens/tools, nested
                        dispatches marked) + main-vs-subagent token split, from the
                        <uuid>/subagents/ sidecars (pass the LIVE transcript)
    tool-breakdown      Tool fingerprint: main-vs-subagent call split + MCP-server table (LIVE transcript)
    topic-slug          Derive a topic slug from edit footprint (basename + folder analysis);
                        optional argv[3]: newline-separated git diff --name-only list used as a
                        fallback when tool-edit signal is low (general/src/src-tauri bucket or
                        materially fewer tool edits than git-changed files)
    line-count          Print number of jsonl lines (sanity check)
    stats               §7 Session-Stats body (header-less): window + activity counts + a
                        transcript-native message.usage token table + per-model split + a subagent
                        aggregate. No ccusage, no $ cost (cc-dashboard owns cost). Optional
                        argv[3]/[4]: ISO start/end to bound to a sub-window.
    drift               Format-drift canary: tally any transcript shape OUTSIDE the known
                        registries (line-type / system-subtype / attachment-type / commandMode /
                        promptSource / origin.kind / leading content-tag / user is*-flag).
                        Prints "kind<TAB>value<TAB>count" rows sorted; "no drift" when clean; exit 0
    hook-fires          Tally hook_system_message attachment lines (each a ballast conditional
                        hook's per-fire visibility one-liner) by hook name; markdown block, or
                        "no hook fires recorded" when none
    bundle              Run ALL bulk dumps (user-msgs, invocations, edits, assistant-text,
                        subagents, tool-breakdown, stats, hook-fires) in ONE process, writing each
                        to <out>/<name>.md plus a manifest.json (metrics + per-dump status).
                        Requires --out <dir>:
                            python extract.py bundle <transcript> --out <dir>
"""
import json
import os
import re
import stat
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


# Conservative result-shape detection for the §2 disposition table.
# A tool_result is treated as a "findings report" only when at least TWO distinct
# signal categories are present — biased toward leaving ambiguous results in the
# roster (§2a) rather than forcing a disposition table (§2b).
_SEVERITY_RE = re.compile(
    r'\b(critical|severity|issue|issues|warning|warnings|suggestion|suggestions|'
    r'finding|findings|vulnerab\w*|defect|defects)\b',
    re.IGNORECASE,
)
_FILELINE_RE = re.compile(r'\b[\w./\\-]+\.[A-Za-z0-9]+:\d+\b')   # foo.py:45, src/bar.ts:12
_NUMBERED_RE = re.compile(r'^\s*\d+[.)]\s+\S', re.MULTILINE)      # "1. ...", "2) ..."
_TABLE_RE = re.compile(r'\|\s*(#|severity|file|finding|line)\b', re.IGNORECASE)  # md table header
# JSON-structured findings (e.g. code-review finder agents emit an array of
# {"file","line","summary","failure_scenario",...} objects — separate fields, no
# inline "file.py:45"). Curated finding-specific keys so generic JSON doesn't match.
_JSON_KEY_RE = re.compile(
    r'"(file|line|severity|summary|finding|findings|message|failure_scenario|suggestion)"\s*:',
    re.IGNORECASE,
)


def _looks_like_findings(text):
    """Return True if a tool_result text resembles a structured findings report.

    Conservative: requires >=2 distinct signal categories (severity vocabulary,
    file:line references, numbered list items, findings-style table header, or
    JSON finding-keys). A JSON findings array (>=3 distinct finding-specific keys)
    is sufficient on its own. When unsure, returns False so the invocation stays
    in the roster.
    """
    if not text or len(text.strip()) < 40:
        return False
    # Strong, self-sufficient signal: a JSON findings report (file + line + summary…).
    json_keys = {m.group(1).lower() for m in _JSON_KEY_RE.finditer(text)}
    if len(json_keys) >= 3:
        return True
    signals = 0
    if _SEVERITY_RE.search(text):
        signals += 1
    if _FILELINE_RE.search(text):
        signals += 1
    if _NUMBERED_RE.search(text):
        signals += 1
    if _TABLE_RE.search(text):
        signals += 1
    if len(json_keys) >= 2:   # weaker JSON signal contributes one category
        signals += 1
    return signals >= 2


def _compact_markers(path):
    """Return (summaries, boundaries) — timestamp lists for the two compaction-event shapes Claude
    Code emits (both coexist):
      - `isCompactSummary == true` — the injected "This session is being continued…" recap that
        rides a plain type=="user" line (the original, field-keyed marker; `_is_real_user_msg`
        gates the same field);
      - a `type == "system"` line with `subtype == "compact_boundary"` — the newer STRUCTURED
        boundary marker CC writes at the compaction point.
    Extracted so `compact_check` (exit-code + printed markers) and bundle's `compacted` boolean
    metric (no exit-code semantics) share one detection pass instead of two copies drifting.
    """
    summaries = []   # isCompactSummary recap timestamps
    boundaries = []  # system/compact_boundary timestamps
    for d in load_lines(path):
        if d.get('isCompactSummary') is True:
            summaries.append(d.get('timestamp', ''))
        # `elif` is safe: the recap is a user line and the boundary is a system line — a single
        # line is never both, so this can't double-count one event.
        elif d.get('type') == 'system' and d.get('subtype') == 'compact_boundary':
            boundaries.append(d.get('timestamp', ''))
    return summaries, boundaries


def compact_check(path):
    """Exit 1 if a compaction marker is found; otherwise exit 0. Print whichever marker(s) fired.

    Either shape from `_compact_markers` is sufficient. Emitting both timestamp lists lets the
    skill recover the boundary from whichever form this transcript used.
    """
    summaries, boundaries = _compact_markers(path)
    if summaries or boundaries:
        parts = []
        if summaries:
            parts.append(f"isCompactSummary at: {', '.join(summaries)}")
        if boundaries:
            parts.append(f"compact_boundary at: {', '.join(boundaries)}")
        print('COMPACTED — ' + '; '.join(parts))
        sys.exit(1)
    sys.exit(0)


def _time_window(path):
    """Return (min_ts, max_ts) across all entries' timestamps, or (None, None) if none are
    present. Shared by the `time-window` subcommand and bundle's manifest metrics."""
    timestamps = [d.get('timestamp', '') for d in load_lines(path)]
    timestamps = [t for t in timestamps if t]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def time_window(path):
    """Print min and max timestamps across all entries."""
    lo, hi = _time_window(path)
    if lo is None:
        print("ERROR: no timestamps found")
        sys.exit(1)
    print(f"{lo} {hi}")


def _text_from_content(content):
    """Flatten a message/attachment content value (str | list-of-blocks | None) to plain text."""
    if isinstance(content, list):
        return ' '.join(c.get('text', '') if isinstance(c, dict) else str(c) for c in content)
    if content is None:
        return ''
    return str(content)


def _ask_answer(d):
    """If `d` is an AskUserQuestion ANSWER turn, return its non-empty answers dict; else None.

    When the user answers an AskUserQuestion by selecting option(s), Claude Code logs a
    `type=="user"` line whose top-level `toolUseResult` carries `answers` (a {question:
    selection} dict) plus `questions`. The line also rides a `tool_result` content block, so the
    generic tool-result gates in `_is_real_user_msg` would drop it — but it IS a genuine user turn
    (the option the user chose, exactly the §4 signal a postmortem wants), so it is admitted before
    those gates. A CANCELLED dialog logs `answers == {}` (falsy) and is correctly NOT admitted.
    Mirrors cc-dashboard parse.rs::is_ask_answer and the cc-user-prompts engine's 4th positive
    user-shape (verified on real transcripts: toolUseResult keys `answers`/`questions`/`annotations`).
    """
    if d.get('type') != 'user':
        return None
    tur = d.get('toolUseResult')
    if isinstance(tur, dict):
        ans = tur.get('answers')
        if isinstance(ans, dict) and ans:
            return ans
    return None


def _format_answers(ans):
    """Render an AskUserQuestion answers dict as one terse line for user_msgs."""
    return '[AskUserQuestion answer] ' + '; '.join(f'{q} → {sel}' for q, sel in ans.items())


# Slash-command echo parsing — shared by `_user_prompt` (ENG-5: a /slash IS a user turn, mirroring
# the cc-user-prompts engine `_from_user`/`_slash` + cc-dashboard `parse.rs::is_real_user_turn` SSoT,
# which both count every slash echo) and `invocations` (the §2(a) roster). The leading
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


def _is_real_user_msg(d):
    """Return True only for a genuine user turn (standalone prose OR an AskUserQuestion answer).

    Mirrors the canonical rule in cc-dashboard's core/parse.rs + the cc-user-prompts engine.
    A real turn is a `type=="user"` line that is NOT:
      - a synthetic / injected line: `isMeta == true` (slash expansions, the SessionStart caveat,
        hook-injected context, skill base-dir injections);
      - a post-compaction recap: `isCompactSummary == true` (the "This session is being
        continued…" block injected as a plain `type=="user"` line — it is NOT isMeta-flagged, so
        it slips every other gate; verified present directly in projects/ transcripts, not just
        compact-backups/; `compact_check` reads the same field);
      - a sidechain replay: `isSidechain == true` (a dispatched subagent prompt replayed as the
        subagent's first user turn — lives in the subagents/ sidecars, ~0 in the main transcript,
        but gated anyway as cheap insurance against an inlined / future-format one);
      - a headless eval probe: `entrypoint == "sdk-cli"` (skill-forge / skill-creator `claude -p`
        RED/GREEN runs — `type=="user"` but harness-authored, not the person);
      - a tool result: a top-level `toolUseResult` / `sourceToolUseID` field or a `tool_result`
        content block — EXCEPT an AskUserQuestion answer (`_ask_answer`), which rides a
        toolUseResult yet IS a real turn and is admitted before that gate;
      - a background-task notice: `origin.kind == "task-notification"` (a NON-meta user line, the
        user-line twin of the attachment form in `_attachment_prompt`; present since ~2026-05);
      - a `!`-mode shell OUTPUT echo (a `<local-command…>` wrapper, `<bash-stdout>` / `<bash-stderr>`).
        The `<bash-input>` the user TYPED is deliberately NOT excluded — it is a real user action.
        (A `<command-name>` / `<command-message>` SLASH echo is NOT excluded either — ENG-5: a /slash
        IS a user turn, counted by both SSoT parsers; `_user_prompt` renders it to a clean `/name
        args`. Filtering slash echoes here on substance would just re-create the §7-vs-dashboard drift.)

    GUARDRAIL: `promptSource` ("typed" / "sdk" / "system" / "queued") is a TRANSPORT channel, NOT a
    human-vs-machine signal — never gate on it. Treating `sdk` as non-human hid ~790 genuine VS Code
    turns in the sibling parser; the human axis is `entrypoint` + structural origin, never promptSource.

    Takes the FULL line dict so it can apply these line-level gates. Mid-turn queued steers and
    autopilot goals are a separate `type=="attachment"` shape — see `_attachment_prompt`.
    """
    if d.get('isMeta') is True:
        return False
    if d.get('isCompactSummary') is True:
        return False
    if d.get('isSidechain') is True:
        return False
    if d.get('entrypoint') == 'sdk-cli':
        return False
    if _ask_answer(d) is not None:
        return True   # answer rides a toolUseResult but IS a real turn — admit before the gate below
    if 'toolUseResult' in d or 'sourceToolUseID' in d:
        return False
    origin = d.get('origin')
    if isinstance(origin, dict) and origin.get('kind') == 'task-notification':
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
    # Output-echo gates only. A <command-name>/<command-message> SLASH echo is NOT dropped here —
    # ENG-5: the SSoT (cc-user-prompts `_slash`, cc-dashboard `is_real_user_turn`) counts every slash
    # echo as a real turn, so we must too; `_user_prompt` renders it to a clean `/name args`. These
    # three are pure machine output the SSoT also drops (`leads_with_output_tag`).
    if text.startswith(('<local-command', '<bash-stdout', '<bash-stderr')):
        return False
    return True


def _attachment_prompt(d):
    """If `d` is a user-authored attachment prompt, return (text, marker); otherwise None.

    Two `type=="attachment"` shapes carry real user input (a fail-CLOSED allow-list — there are
    many attachment subtypes and only these are the person speaking):
      - `attachment.type == "queued_command"` with `commandMode == "prompt"` → a mid-turn STEER
        typed while the agent worked (the high-signal "no, do X instead" §4 correction). Body in
        `attachment.prompt`. EXCLUDED: `task-notification` / any other commandMode, AND a synthetic
        `origin.kind == "auto-continuation"` echo — the harness's "Goal set: …" reply to a goal-set,
        which is NOT user-typed and would otherwise be emitted as a spurious steer AND counted,
        duplicating the goal already surfaced via `goal_status` (verified: across the corpus only the
        "Goal set:" echoes carry that origin.kind; genuine steers have `human` or no origin).
      - `attachment.type == "goal_status"` → an autonomous-mode GOAL/condition the user set in
        autopilot (text in `attachment.condition`). Logged as met=false / met=true bookends that
        repeat the same condition, so `user_msgs` dedups by condition. High-signal input for the
        §2(a) autonomy verdict
        (verified on a real autopilot session: attachment keys `condition`/`met`/`sentinel`/`type`).

    Returns the marker so callers can tag the line and apply per-kind policy: steers count as
    prompts; goals are display-only (see `_count_tool_calls`) since the bookends would double-count.
    """
    if d.get('type') != 'attachment':
        return None
    att = d.get('attachment')
    if not isinstance(att, dict):
        return None
    # `(att.get('origin') or {})` — fail closed if `origin` is explicitly JSON-null, mirroring the
    # `isinstance(origin, dict)` task-notification gate in `_is_real_user_msg`.
    if att.get('type') == 'queued_command' and att.get('commandMode') == 'prompt' \
            and (att.get('origin') or {}).get('kind') != 'auto-continuation':
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
    a standalone `type=="user"` turn, an AskUserQuestion answer, a `/slash`-command echo, a mid-turn
    `type=="attachment"` queued steer, and an autopilot goal — so `user_msgs` and `_count_tool_calls`
    never drift on what counts. `marker` is None for a standalone prose turn / answer, else a label
    ('slash-command' | 'mid-turn steer' | 'autopilot goal') the callers use to tag the line and apply
    counting policy. A 'slash-command' counts as a turn (SSoT parity, ENG-5); a goal does not.
    """
    t = d.get('type')
    if t == 'user':
        if not _is_real_user_msg(d):
            return None
        ans = _ask_answer(d)
        if ans is not None:
            return _format_answers(ans), None
        msg = d.get('message', {})
        content = msg.get('content', '') if isinstance(msg, dict) else ''
        text = _text_from_content(content)
        slash = _slash_echo(text)                      # ENG-5: /slash echo → clean `/name args`
        if slash is not None:
            return slash, 'slash-command'              # a real turn (SSoT parity), display-cleaned
        return text, None
    if t == 'attachment':
        return _attachment_prompt(d)
    return None


def _user_msg_entries(path):
    """Yield (ts, marker, text) for every entry `user_msgs` prints, in the same order/inclusion.

    Extracted so `bundle`'s `user_turns` metric can count from the EXACT same iteration `user_msgs`
    renders from (D1: "user_turns MUST equal the number of entries user-msgs emits") rather than a
    separately-maintained approximation that could drift from it.
    """
    seen_goals = set()
    for d in load_lines(path):
        p = _user_prompt(d)
        if p is None:
            continue
        text, marker = p
        if marker == 'autopilot goal':
            key = text.strip()
            if key in seen_goals:   # met=false / met=true bookends repeat the same condition
                continue
            seen_goals.add(key)
        yield d.get('timestamp', ''), marker, text


def user_msgs(path):
    """Print real user messages with timestamps, in chronological order.

    Covers every shape `_user_prompt` recognizes: standalone `type=="user"` turns,
    AskUserQuestion answers, `(slash-command)` echoes, mid-turn `(mid-turn steer)` prompts, and
    `(autopilot goal)` conditions — the last deduped by condition text since the goal is logged as
    met=false / met=true bookends. Markered shapes are exactly the high-signal signals a §4 / §2(a)
    post-mortem cares about (steers → §4 corrections; goals → the §2(a) autonomy verdict). (All shapes
    resolved by `_user_prompt`, entries enumerated by `_user_msg_entries`.)
    """
    for ts, marker, text in _user_msg_entries(path):
        suffix = f' ({marker})' if marker else ''
        print(f'--- {ts}{suffix} ---')
        print(text.strip())
        print()


def invocations(path):
    """Print Skill/Agent/Workflow invocations + their tool_result, plus user-typed slash commands.

    - Model-invoked Skill/Agent/Workflow calls are paired with their tool_result. Each block
      header is tagged `findings-shape: yes|no` (see _looks_like_findings) so the skill knows
      whether to build a §2 disposition table for it. (A Workflow pairs with its launch
      tool_result "Workflow launched in background…"; its FINDINGS arrive later when the
      background task completes — read the `assistant-text` near the task-notification
      timestamp for them, NOT a user message: task-notifications are `attachment` lines, not
      user turns, and aren't emitted by any subcommand.)
    - User-typed slash commands (e.g. `/code-review`) appear as `<command-name>` echoes in user
      messages with no paired tool_result; they are emitted as standalone `slash-command` lines
      so §2's roster captures user-invoked skills too.

    Note: inline Skills often return only "Launching skill: ..." here — their actual analysis
    appears in later assistant-text near the invocation timestamp.
    """
    # Slash-echo regexes are module-level (`_CMD_NAME_RE`/`_CMD_ARGS_RE`) — shared with `_slash_echo`
    # so the roster here and the user-turn count in `_user_prompt` parse the same shape (ENG-5).
    # FIFO queue of (ts, name, label, args, tool_use_id) for invocations awaiting a tool_result.
    # A single API response can emit MULTIPLE Skill/Agent/Workflow tool_use blocks (e.g. parallel
    # Agent dispatch), logged as consecutive assistant lines BEFORE any tool_result — a single-slot
    # tracker would let each overwrite the previous and silently drop all but the last (~50% of
    # Agent dispatches in multi-agent sessions). We queue them and pair each tool_result with its
    # invocation by tool_use_id (exact), falling back to FIFO order for legacy lines that lack one.
    pending = []
    for d in load_lines(path):
        t = d.get('type')
        ts = d.get('timestamp', '')
        msg = d.get('message', {})
        if not isinstance(msg, dict):
            continue
        content = msg.get('content', [])

        # (b) User-typed slash-command echoes (content may be a string or a list of blocks).
        # A compact-summary recap (`isCompactSummary`) QUOTES prior `<command-name>` blobs verbatim,
        # so without this gate every compact boundary re-mints phantom slash-command rows for
        # commands typed pre-compact (verified live 2026-07-20: two phantom `/code-review` entries at
        # exactly the two compact-boundary timestamps). `_is_real_user_msg` already drops the recap
        # from user-turn counting; mirror that gate here.
        if t == 'user' and d.get('isCompactSummary') is not True:
            text_blob = _text_from_content(content)
            for m in _CMD_NAME_RE.finditer(text_blob):
                # Some command echoes already include the leading slash (e.g. "/compact"),
                # others don't ("code-review") — normalize so we always print exactly one.
                name = m.group(1).strip().lstrip('/')
                am = _CMD_ARGS_RE.search(text_blob)
                cargs = am.group(1).strip() if am else ''
                suffix = f' {cargs}' if cargs else ''
                print(f'=== [{ts}] slash-command: /{name}{suffix} ===')
                print('(user-typed command echo — no tool_result; analysis follows in assistant-text)')
                print()

        if not isinstance(content, list):
            continue
        # (a) Model-invoked Skill/Agent/Workflow calls paired with their tool_result.
        for c in content:
            if not isinstance(c, dict):
                continue
            if t == 'assistant' and c.get('type') == 'tool_use':
                name = c.get('name', '')
                if name in ('Skill', 'Agent', 'Workflow'):
                    inp = c.get('input', {})
                    if not isinstance(inp, dict):
                        inp = {}
                    # Workflow's real name lives in its script's meta block (not the tool input),
                    # so fall back to name/description, then to the script's first line.
                    label = (inp.get('skill', '') or inp.get('name', '') or inp.get('description', '')
                             or (inp.get('script') or '')[:60].split('\n')[0].strip())
                    args = inp.get('args', '') or inp.get('subagent_type', '')
                    pending.append((ts, name, label, args, c.get('id')))
            elif t == 'user' and c.get('type') == 'tool_result' and pending:
                tuid = c.get('tool_use_id')
                if tuid is not None:
                    # Exact pairing; if this result isn't for a tracked invocation (it's a
                    # Read/Bash/etc. result interleaved in the same user message), skip it.
                    idx = next((i for i, p in enumerate(pending) if p[4] == tuid), None)
                    if idx is None:
                        continue
                else:
                    idx = 0  # legacy line with no tool_use_id → best-effort FIFO
                inv_ts, inv_name, inv_label, inv_args, _ = pending.pop(idx)
                rc = _text_from_content(c.get('content', '')).strip()
                shape = 'yes' if _looks_like_findings(rc) else 'no'
                print(f'=== [{inv_ts}] {inv_name}: {inv_label} ({inv_args}) | findings-shape: {shape} ===')
                print(rc)
                print()


def edits(path):
    """Print Edit/Write/MultiEdit tool calls in chronological order."""
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        msg = d.get('message', {})
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get('type') != 'tool_use':
                continue
            name = c.get('name', '')
            if name not in ('Edit', 'Write', 'MultiEdit'):
                continue
            fp = c.get('input', {}).get('file_path', '')
            ts = d.get('timestamp', '')
            print(f'{ts} {name} {os.path.basename(fp)} ({fp})')


def assistant_text(path):
    """Print assistant text blocks chronologically (excludes tool calls). Useful for spotting catches/reasoning."""
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        msg = d.get('message', {})
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get('type') != 'text':
                continue
            text = c.get('text', '').strip()
            if len(text) < 20:
                continue
            ts = d.get('timestamp', '')
            print(f'--- {ts} ---')
            print(text)
            print()


def _bucket_winner(counts):
    """Return the plurality bucket from a Counter, or 'general' if empty or tied.

    A tie for the most-edited bucket is genuinely ambiguous — no single topic.
    Shared by both the tool-edit and git-files branches of topic_slug so the two
    passes can't diverge on tie-breaking.
    """
    if not counts:
        return 'general'
    ranked = counts.most_common()
    top, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return 'general'
    return top


def _bucket_paths(paths, cwd, ignored_buckets):
    """Count slugified first-segment buckets for a list of file paths relative to cwd.

    Shared by the tool-edit and git-files branches of topic_slug:
    - Paths outside cwd (different Windows drive, or a ../-prefixed relpath) are skipped.
    - Paths whose first segment falls in ignored_buckets are skipped.
    - Root-level files use their stem; deeper paths use the first directory segment.
    - CamelCase / PascalCase boundaries are hyphenated BEFORE lowercasing so acronym
      runs survive intact (e.g. OpenMeteoAPI -> open-meteo-api, not open-meteo-a-p-i).

    Returns a Counter of slugified bucket names.
    """
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
        first = parts[0]
        # Root-level file (e.g., IDEAS.md) — use its stem; subdir paths use the dir name.
        raw = os.path.splitext(first)[0] if len(parts) == 1 else first
        # Slugify BEFORE lowercasing so camelCase/PascalCase boundaries survive (e.g.
        # OpenMeteoAPI -> open-meteo-api, TURZX -> turzx). The split keeps acronym runs
        # together (no "a-p-i"); a previous version slugified an already-lowercased string,
        # so the camelCase rule never fired.
        bucket = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '-', raw).lower()
        if bucket in ignored_buckets:
            continue
        counts[bucket] += 1
    return counts


def topic_slug(path, git_files_raw=None):
    """Derive a topic slug from edit footprint, bucketed by the first path segment
    relative to the current working directory (the project root — the skill runs
    inline from there).

    Strategy:
    1. For each Edit/Write/MultiEdit, compute the file path relative to cwd.
    2. Bucket by the first path segment (top-level folder, or the file's stem for
       a root-level file like IDEAS.md). Edits outside cwd, and edits in throwaway
       scratch dirs (.debug/), are skipped.
    3. Anchor on the dominant (plurality) bucket — the most-edited top-level area.
       Only fall back to "general" when the top two buckets tie (genuinely
       ambiguous) or there are no qualifying edits.

    Git-files fallback (optional `git_files_raw` param; CLI: 3rd positional arg): a
    newline-separated list of changed filenames (e.g. `git diff --name-only` output) supplied by
    the SKILL.md caller from the session's in-scope commit range. It patches the blind spot (ENG-4)
    where a bulk PowerShell/Bash operation (WriteAllText, Rename-Item, a shell script) rewrites many
    files WITHOUT leaving Edit/Write/MultiEdit traces in the transcript — so the tool
    footprint under-counts and the slug collapses to `general`. The override fires only when:
      (a) the tool-edit bucket is in the low-signal set {general, src, src-tauri} — generic
          structural paths that aggregate everything rather than name the topic, OR
      (b) git changed strictly >3x as many files as tool edits touched, AND >=5 git files are
          present (the >=5 floor guards against flipping a representative, non-low-signal tool
          slug on just a handful of git files);
    AND the git bucket is NOT itself low-signal (no improvement -> no override).
    The pure-shell, zero-tool-edit session fires through (a) — its tool slug is `general`, which is
    low-signal — independent of the (b) floor.
    Stdout stays a single word; stderr carries a one-line note when git files drive the
    slug, so the caller knows the fallback fired without breaking the stdout contract.

    This is a heuristic — the skill body may override it from a brainstorm spec match.
    """
    cwd = os.path.abspath(os.getcwd())
    # Throwaway scratch dirs never represent the session topic; excluding them keeps
    # a few debug-script edits from diluting the real work down to "general".
    ignored_buckets = {'.debug'}
    # Low-signal tool-edit buckets: 'src'/'src-tauri' are common monorepo layouts where
    # EVERY file sits under src/, so the bucket is unhelpful as a slug; 'general' is already
    # the no-qualifying-edits fallback. Mirrors SKILL.md step 9's "generic source bucket" list.
    LOW_SIGNAL_BUCKETS = {'general', 'src', 'src-tauri'}

    # --- Tool-edit paths from the transcript ---
    tool_paths = []
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        msg = d.get('message', {})
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get('type') != 'tool_use':
                continue
            if c.get('name') not in ('Edit', 'Write', 'MultiEdit'):
                continue
            fp = c.get('input', {}).get('file_path', '')
            if fp:
                tool_paths.append(fp)

    tool_counts = _bucket_paths(tool_paths, cwd, ignored_buckets)
    tool_total = sum(tool_counts.values())   # qualifying tool edits (for the gap check)
    tool_slug = _bucket_winner(tool_counts)

    # --- Git-files fallback: git_files_raw is an optional newline-separated list of changed
    # file paths (`git diff --name-only` output), fed by the SKILL.md caller from the
    # session's commit range (main() forwards the CLI's 3rd positional arg here; bundle() calls
    # this directly with a real parameter, no argv involved). ---
    # strip() handles CRLF endings from Windows git output (a trailing \r breaks relpath).
    git_files = [f.strip() for f in (git_files_raw or '').splitlines() if f.strip()]

    if git_files:
        git_counts = _bucket_paths(git_files, cwd, ignored_buckets)
        git_slug = _bucket_winner(git_counts)
        # (a) Low-signal tool bucket — a generic structural path, not topic-bearing.
        low_signal = tool_slug in LOW_SIGNAL_BUCKETS
        # (b) Material gap — git changed STRICTLY >3x as many files as tool edits touched. The
        #     >=5-file floor avoids flipping a representative (non-low-signal) tool slug on a few
        #     git files; `tool_total * 3 < len(git_files)` is the strict >3x test and is safe at
        #     tool_total == 0 (0 < any positive). The zero-edit session is handled by (a), not here.
        material_gap = len(git_files) >= 5 and tool_total * 3 < len(git_files)
        if (low_signal or material_gap) and git_slug not in LOW_SIGNAL_BUCKETS:
            print(git_slug)
            print(f'(git-diff fallback: {len(git_files)} changed files, {tool_total} tool edits)',
                  file=sys.stderr)
            return

    print(tool_slug)


def _count_lines(path):
    """Return the transcript's jsonl line count. Shared by `line_count` and bundle's
    `transcript_lines` metric."""
    with open(path, encoding='utf-8') as f:
        return sum(1 for _ in f)


def line_count(path):
    """Print transcript line count for sanity check."""
    print(_count_lines(path))


# ENG-1 (schema v2): the ccusage dependency AND the manual pricing fallback (_price_for /
# _estimate_cost / _opus_is_legacy + the per-model rate tables) were REMOVED. §7 is now
# transcript-native — token VOLUME from message.usage (`_usage_tokens` / `_usage_by_model`, the
# same source §2(c) uses, so §7 reconciles with §2(c) instead of ccusage's session-dependent
# sidecar rollup), with NO blended $ cost estimate. cc-dashboard owns cost analysis. This also
# dropped the `shell=True` ccusage subprocess (and its UUID-interpolation guard) entirely.


# Canonical UUID shape: 8-4-4-4-12 hex.
_UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def _session_uuid_from_path(path):
    """Extract the session UUID from a transcript filename (used for the resume-chain merge and
    sidecar-dir resolution; the former ccusage `--id` use was removed with ENG-1).

    Live transcripts are "<uuid>.jsonl"; PreCompact archives (written by a user-side
    PreCompact archiver, if configured) are "<timestamp>_<trigger>_<uuid>.jsonl".
    Match the trailing canonical UUID in either form; fall back to the bare stem
    if no UUID is present (best effort).
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _UUID_RE.search(stem)
    return m.group(0) if m else stem


def _count_tool_calls(path, start_ts=None, end_ts=None):
    """Return (tool_use Counter, user_prompts, assistant_responses) within an optional sub-window.

    - user_prompts uses the canonical rule: genuine user turns + AskUserQuestion answers + `/slash`
      echoes (ENG-5: SSoT parity — both sibling parsers count them) (excludes tool-results, isMeta /
      isCompactSummary / isSidechain / sdk-cli injections, bash-OUTPUT echoes, task-notifications)
      PLUS mid-turn queued_command/prompt steers. Autopilot GOALS are
      surfaced by `user_msgs` but NOT counted here — they repeat as met=false/met=true bookends and
      are success conditions, not conversational turns. See `_is_real_user_msg` / `_attachment_prompt`.
    - assistant_responses counts DISTINCT assistant `message.id` values — a single API response is
      logged as ~2.4 content-block lines, so counting lines would overstate real model responses
      (it would be "assistant lines", not "turns"). Keyless lines (no id) are counted individually.

    Optional start_ts / end_ts (ISO strings) bound counts to a sub-window.
    """
    tools = Counter()
    user_prompts = 0
    response_ids = set()
    keyless_responses = 0
    for d in load_lines(path):
        ts = d.get('timestamp', '')
        if start_ts and ts < start_ts:
            continue
        if end_ts and ts >= end_ts:
            continue
        p = _user_prompt(d)
        if p is not None:
            if p[1] != 'autopilot goal':   # goals are display-only signal (dup bookends), not turns
                user_prompts += 1
            continue
        if d.get('type') == 'assistant':
            msg = d.get('message', {})
            if not isinstance(msg, dict):
                continue
            mid = msg.get('id')
            if mid:
                response_ids.add(mid)
            else:
                keyless_responses += 1
            content = msg.get('content', [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'tool_use':
                        tools[c.get('name', '?')] += 1
    return tools, user_prompts, len(response_ids) + keyless_responses


def stats(path, start_ts=None, end_ts=None):
    """Emit a markdown-ready Session stats footer — TRANSCRIPT-NATIVE (no ccusage, no $ cost).

    Token VOLUME comes from `message.usage` via `_usage_tokens` / `_usage_by_model` (deduped by
    message.id), the SAME source §2(c) uses — so §7 reconciles with §2(c) instead of ccusage's
    session-dependent sidecar rollup (which excluded subagents on a forked-UUID session but
    included them otherwise — the v1 inconsistency ENG-1 removes). No billed cost — cc-dashboard
    owns cost analysis.

    Optional `start_ts` (CLI: 3rd positional arg): ISO start timestamp to bound the analysis to a
    sub-window (e.g. "just the post-mortem run"); `end_ts` (CLI: 4th arg): ISO end. The activity
    counts AND the token table honor the bound. main() forwards the CLI's extra positional args
    here; bundle() calls this directly with no sub-window (dumps always run against the full
    live transcript).
    """
    # Transcript-derived activity counts (sub-window-bounded if args provided)
    tools, user_prompts, assistant_responses = _count_tool_calls(path, start_ts, end_ts)

    # Time window from transcript
    timestamps = [d.get('timestamp', '') for d in load_lines(path)]
    timestamps = [t for t in timestamps if t]
    if not timestamps:
        print('_No timestamps in transcript._')
        return
    session_start = min(timestamps)
    session_end = max(timestamps)
    # Window to display: sub-window if bounded, else full session
    display_start = start_ts or session_start
    display_end = end_ts or session_end

    # Output markdown footer — the §7 BODY only, NO `## 7. Session Stats` heading. The report
    # template (and the extractor's §7 return section) own that heading; emitting it here too put
    # TWO `## 7.` headers in the extractor's digest, forcing the orchestrator to dedup at assembly
    # (caught on the v1 maiden run). The body starts at the window line.
    if start_ts or end_ts:
        print(f'**Sub-window:** {display_start} → {display_end}  _(of full session {session_start} → {session_end})_')
    else:
        print(f'**Window:** {session_start} → {session_end}')
    print(f'**User prompts:** {user_prompts}')
    print(f'**Assistant responses:** {assistant_responses}')
    # "main-thread" scope label disambiguates from §2(b)'s combined main+subagent total (the
    # §7 activity counts are main-transcript-only; §2(b)/(c) cover the subagents).
    print(f'**Tool calls (main-thread):** {sum(tools.values())} total')
    if tools:
        # Top 8 by call volume, with a labeled remainder so the breakdown reconciles with the
        # stated total above (a silently-truncated list summing to less than the total reads as a bug).
        shown = tools.most_common(8)
        top = ', '.join(f'{n}×{c}' for n, c in shown)
        rest = sum(tools.values()) - sum(c for _, c in shown)
        if rest:
            top += f', +{len(tools) - 8} more ×{rest}'
        print(f'  - {top}')
    print()

    # Token volume — main-thread message.usage, deduped by message.id (== what §2(c) sums for main).
    tok = _usage_tokens(path, start_ts, end_ts)
    print('**Token usage** (main-thread, raw message.usage volume):')
    print()
    print('| Type | Tokens |')
    print('|---|---:|')
    print(f'| Input | {tok["input"]:,} |')
    print(f'| Output | {tok["output"]:,} |')
    print(f'| Cache creation | {tok["cache_create"]:,} |')
    print(f'| Cache read | {tok["cache_read"]:,} |')
    print(f'| **Total** | **{tok["total"]:,}** |')
    print()

    # Per-model attribution — transcript-native (message.model × message.usage), NOT a ccusage
    # artifact: it always was sourced from the transcript, so it survives the ccusage removal intact.
    by_model = _usage_by_model(path, start_ts, end_ts)
    if by_model:
        models_str = ', '.join(f'{_short_model(m)} ({t:,})' for m, t in by_model.items())
        print(f'**Models (main-thread):** {models_str}')
        print()

    # Subagent aggregate — the fanout volume §2(c) details per-agent. Resolves when stats is run on
    # the LIVE transcript (sidecars live beside it via `_subagent_dirs`); naturally empty for a
    # compact-archive path. One bridge line; the per-agent table stays in §2(c) (no duplication).
    sub_total = 0
    n_agents = 0
    for sidedir in _subagent_dirs(path):
        for a in _collect_subagents(sidedir):
            sub_total += a['tokens']
            n_agents += 1
    if n_agents:
        print(f'**Subagent tokens:** {sub_total:,} across {n_agents} agent(s) — per-agent breakdown in §2(c).')
        print()

    print('_Token counts are raw message.usage volume (main thread), deduped by message.id — not '
          'billed cost. Subagent split is in §2(c). Billed cost analysis → cc-dashboard._')


# ---------------------------------------------------------------------------
# Subagent & tooling parsing (§2 "Subagent & Tooling Evaluation").
#
# A session that dispatches subagents (Agent tool / Workflow fan-out) gets a sibling
# sidecar dir beside the LIVE transcript: `<uuid>/subagents/**` holding each dispatched
# agent's own transcript (`agent-<id>.jsonl`) + an `agent-<id>.meta.json` (the agentType),
# nested under `workflows/wf_<id>/` for Workflow fan-outs. extract.py historically only ever
# opened the single main transcript, so subagent token spend + tool calls were invisible to
# §2 (and pre-ENG-1 were rolled into §7's ccusage total only when ccusage happened to find the
# sidecars — the session-dependent inconsistency §7 now sidesteps). These helpers port the read half of
# cc-dashboard's `collect_nested_activity` / `collect_subagents` (core/parse.rs, tools.rs):
# content-free (names/types/token-counts only, never an agent's prompt or description), with
# the two correct dedup keys — message.id for usage tokens, tool_use block id (`toolu_*`) for
# tool counts (the 2026-06-11 cc-dashboard finding: sidecars never re-log, so block-id dedup is
# 0% overcount while message-id dedup would UNDERcount tools).
#
# Two further parities (ENG-6): the sidecar walk REFUSES to follow reparse points (symlinks /
# junctions) via `_is_reparse_point` + a no-follow scandir walk (mirroring discovery.rs / parse.rs
# `file_type()` no-follow), and the DISPLAYED roster is capped at `_AGENT_SAMPLE_CAP` (12,
# cc-dashboard's agent_sample_cap) while the dispatched COUNT + token split stay exact over all
# agents. A forked/resumed UUID's sidecars are merged across the resume chain (ENG-3, `_subagent_dirs`).
# ---------------------------------------------------------------------------

# Display cap for the subagent roster TABLE — mirrors cc-dashboard's `agent_sample_cap` (mod.rs).
# The dispatched COUNT, the by_type tally, and the main-vs-subagent token split stay EXACT over all
# agents; only the rendered rows are sampled (top-N by token spend) once the roster exceeds this.
_AGENT_SAMPLE_CAP = 12


def _is_reparse_point(path):
    """True if `path` is a symlink OR (Windows) a junction / mount point — ANY reparse point.

    `os.path.islink()` returns False for Windows JUNCTIONS (the common `mklink /J` / Drive-junction
    case), so it MISSES the reparse points a stray (or planted) link could use to redirect a sidecar
    read outside the trusted ~/.claude/projects tree. We read the no-follow `lstat` and test the
    Windows reparse-point file attribute — which catches junctions AND symlinks; both
    `st_file_attributes` and `stat.FILE_ATTRIBUTE_REPARSE_POINT` exist on Python 3.8+ Windows builds —
    falling back to the POSIX symlink-mode bit. Fails CLOSED: an unstattable path is treated as a
    reparse point and refused. Mirrors cc-dashboard discovery.rs / parse.rs, which refuse to traverse
    a reparse point via each dir-entry's own no-follow `file_type()`.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True  # can't stat it -> untrusted, refuse (fail closed)
    # Windows: the reparse-point bit covers BOTH junctions and symlinks (islink only catches symlinks).
    # On POSIX `st_file_attributes` / the constant are absent -> getattr yields 0 and this is a no-op.
    attrs = getattr(st, 'st_file_attributes', 0)
    if attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        return True
    return stat.S_ISLNK(st.st_mode)  # POSIX fallback: a plain symlink


def _walk_sidecar_files(root, suffix):
    """Return sorted paths of files under `root` whose name ends with `suffix`, at any depth,
    WITHOUT following reparse points (symlinks / junctions).

    The no-follow analogue of `glob.glob('**/*', recursive=True)`, which FOLLOWS symlinked dirs —
    and `os.walk` wouldn't prune Windows junctions either (they aren't symlinks). Manual `os.scandir`
    recursion: a reparse-point directory is pruned (never descended) and a reparse-point file is
    skipped, so a planted `agent-*.meta.json` / `agent-*.jsonl` link can't redirect a read out of the
    projects tree. Sorted to preserve the previous `sorted(glob(...))` deterministic order. Mirrors
    cc-dashboard's per-dir-entry no-follow `file_type()` discipline (discovery.rs / parse.rs).
    """
    results = []

    def _recurse(d):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            # Refuse symlinks AND Windows junctions outright; `follow_symlinks=False` also classifies
            # the entry by ITSELF (not its target), so a reparse-point dir reads as a non-dir here.
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
    """The subagents/ sidecar dir for a main transcript: `<uuid>.jsonl` -> `<uuid>/subagents/`.

    Sidecars live beside the LIVE transcript (`projects/<proj>/<uuid>/subagents/`). For a compact
    ARCHIVE path (in compact-backups/) this dir won't exist — pass the LIVE transcript to the
    `subagents` / `tool-breakdown` subcommands so the sidecars resolve.
    """
    return os.path.splitext(path)[0] + os.sep + 'subagents'


def _session_uuid_chain(path):
    """The resume-chain UUIDs for a transcript: the filename UUID plus any distinct top-level
    `sessionId` values its lines carry. A session that forks/resumes its UUID mid-run can keep a
    PRIOR UUID on replayed historical lines, so the SET of sessionIds present IS the chain.

    Scoped STRICTLY to this transcript (we read only its own lines, never the sibling files), so an
    unrelated session is never swept in. Filename UUID first, then any others in first-seen order.
    `sessionId` is filtered through `_UUID_RE.fullmatch`, which drops the `bridge-session` line's
    `bridgeSessionId` (a `cse_*` cloud id, not a local sidecar UUID) and any malformed value.

    EMPIRICAL (2026-06-27 audit of 400 local transcripts — 364 live + 36 compact-backups): every
    line's sessionId equals the filename UUID; no fork is present on disk in this CC build, so this
    returns a single UUID and the sidecar merge below is a no-op today. It is a forward-compatible
    guard: if a future build retains the OLD sessionId on resume-replayed lines, the set becomes the
    real chain and `_subagent_dirs` recovers the otherwise-under-counted agents.
    """
    chain = [_session_uuid_from_path(path)]
    for d in load_lines(path):
        if not isinstance(d, dict):
            continue
        sid = d.get('sessionId')
        # Canonical UUIDs only, and only ones not already in the chain (the filename UUID + the
        # vast majority of lines repeat it; a fork would contribute the extra prior UUID(s)).
        if isinstance(sid, str) and sid not in chain and _UUID_RE.fullmatch(sid):
            chain.append(sid)
    return chain


def _subagent_dirs(path):
    """Every EXISTING `<uuid>/subagents` sidecar dir for this transcript's resume chain.

    A forked/resumed UUID splits sidecars across `<old-uuid>/subagents/` + `<new-uuid>/subagents/`;
    this resolves both. The chain UUIDs come from `_session_uuid_chain` (this transcript's own lines
    only — never a glob of `projects/<proj>/`, so no unrelated session's sidecars leak in). Returns
    the existing dirs in chain order (filename UUID first), de-duplicated.

    The no-fork case yields exactly the single legacy `_subagent_dir(path)` dir, so callers behave
    identically to before. (An archive path still yields the legacy result: its stem-derived primary
    dir doesn't exist, and a single-sessionId archive adds no further chain UUIDs — pass the LIVE
    transcript to actually resolve sidecars, as the subcommand docs already require.)
    """
    parent = os.path.dirname(path)
    dirs = []
    # chain[0] is the filename UUID — reuse the canonical single-dir helper so the sidecar-location
    # convention (`<uuid>.jsonl` -> `<uuid>/subagents`) is defined in exactly one place.
    primary = _subagent_dir(path)
    if os.path.isdir(primary):
        dirs.append(primary)
    # Any further chain UUIDs (a prior/forked session) resolve to sibling dirs in the same
    # projects/<proj>/ folder. Strictly the chain — never a wildcard over the dir.
    for u in _session_uuid_chain(path)[1:]:
        d = os.path.join(parent, u, 'subagents')
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _usage_tokens(path, start_ts=None, end_ts=None):
    """Sum message.usage tokens across a transcript's assistant lines, deduped by message.id.

    One API response logs as several content-block lines that can repeat the SAME usage, so we take
    the MAX per (message.id, field) — a response is counted once, never summed across its lines
    (mirrors ccusage / cc-dashboard dedup). Reads the RAW transcript usage fields (`input_tokens`,
    `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) — NOT ccusage's
    camelCase. Returns {input, output, cache_create, cache_read, total} (raw volume, not billed cost).

    Optional start_ts / end_ts (ISO strings) bound the sum to a sub-window (used by `stats` for the
    "just the post-mortem run" case); unbounded by default, so the §2(c) / subagent callers that
    pass only `path` are unchanged.
    """
    fields = (('input', 'input_tokens'), ('output', 'output_tokens'),
              ('cache_create', 'cache_creation_input_tokens'), ('cache_read', 'cache_read_input_tokens'))
    per_id = {}
    keyless = {'input': 0, 'output': 0, 'cache_create': 0, 'cache_read': 0}
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        ts = d.get('timestamp', '')
        if start_ts and ts < start_ts:
            continue
        if end_ts and ts >= end_ts:
            continue
        msg = d.get('message')
        if not isinstance(msg, dict):
            continue
        u = msg.get('usage')
        if not isinstance(u, dict):
            continue
        mid = msg.get('id')
        target = per_id.setdefault(mid, {'input': 0, 'output': 0, 'cache_create': 0, 'cache_read': 0}) \
            if mid else keyless
        for k, raw in fields:
            v = u.get(raw, 0) or 0
            if mid:
                if v > target[k]:
                    target[k] = v
            else:
                target[k] += v
    agg = dict(keyless)
    for slot in per_id.values():
        for k in agg:
            agg[k] += slot[k]
    agg['total'] = sum(agg[k] for k in ('input', 'output', 'cache_create', 'cache_read'))
    return agg


def _usage_by_model(path, start_ts=None, end_ts=None):
    """Per-model token volume {model_id: total_tokens} from message.model × message.usage, deduped
    by message.id (a response logs as several lines repeating the same usage — take MAX per
    (id, field), then sum each response's four fields under its model). Models in first-seen order;
    zero-token models dropped.

    Transcript-native — this per-model attribution always came from the transcript, NOT ccusage, so
    it survives the ENG-1 ccusage removal. By construction `sum(by_model.values())` ==
    `_usage_tokens(path, start_ts, end_ts)['total']` for the same bounds (same dedup, just partitioned
    by model), which §7 relies on and a unit test pins.
    """
    fields = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
    per_id = {}   # message.id -> {'model': str, <field>: max-seen}
    keyless = {}  # model -> running token total for id-less assistant lines
    order = []    # model ids in first-seen order (preserves display order)
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        ts = d.get('timestamp', '')
        if start_ts and ts < start_ts:
            continue
        if end_ts and ts >= end_ts:
            continue
        msg = d.get('message')
        if not isinstance(msg, dict):
            continue
        u = msg.get('usage')
        if not isinstance(u, dict):
            continue
        model = msg.get('model') or '?'
        if model not in order:
            order.append(model)
        mid = msg.get('id')
        if mid:
            slot = per_id.setdefault(mid, {'model': model, **{f: 0 for f in fields}})
            for f in fields:
                v = u.get(f, 0) or 0
                if v > slot[f]:
                    slot[f] = v
        else:
            keyless[model] = keyless.get(model, 0) + sum((u.get(f, 0) or 0) for f in fields)
    by_model = {m: 0 for m in order}
    for slot in per_id.values():
        by_model[slot['model']] = by_model.get(slot['model'], 0) + sum(slot[f] for f in fields)
    for m, t in keyless.items():
        by_model[m] = by_model.get(m, 0) + t
    return {m: t for m, t in by_model.items() if t}


def _tool_histogram(path, seen=None):
    """Counter of tool_use names across a transcript's assistant lines, deduped by tool_use block id.

    block-id dedup (NOT message.id) is correct for tools: one response logs many distinct calls on
    separate lines. `seen`, if given, is a shared block-id set so a main+subagent sweep never
    double-counts a block id across files (block ids are unique across transcripts, so this is
    belt-and-suspenders).
    """
    seen = seen if seen is not None else set()
    tools = Counter()
    for d in load_lines(path):
        if d.get('type') != 'assistant':
            continue
        msg = d.get('message', {})
        if not isinstance(msg, dict):
            continue
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for c in content:
            if isinstance(c, dict) and c.get('type') == 'tool_use':
                bid = c.get('id')
                if bid is not None:
                    if bid in seen:
                        continue
                    seen.add(bid)
                tools[c.get('name', '?')] += 1
    return tools


def _first_assistant_model(path):
    """First assistant `message.model` in a transcript (the agent's model tier); '?' if none."""
    for d in load_lines(path):
        if d.get('type') == 'assistant':
            msg = d.get('message')
            if isinstance(msg, dict) and msg.get('model'):
                return msg['model']
    return '?'


def _short_model(m):
    """Trim a model id to its family-version for display: `claude-opus-4-8[1m]` -> `opus-4-8`."""
    if not m or m == '?':
        return '?'
    return m.replace('claude-', '').split('[')[0]


def _sanitize_label(desc):
    """Collapse a raw `description` string into one safe markdown-table cell.

    Whitespace/newlines collapse to single spaces (a multi-line description would otherwise break
    the table's one-row-per-line shape); `|` is replaced with `/` rather than backslash-escaped —
    the label is a short 3-5 word dispatch phrase, not prose where a literal pipe is meaningful, so
    a clean substitution reads better than escape noise. Truncated to 60 chars with a trailing `…`
    so one long label can't blow out the table's width.
    """
    s = re.sub(r'\s+', ' ', desc).strip().replace('|', '/')
    if len(s) > 60:
        s = s[:60].rstrip() + '…'
    return s


def _collect_subagents(sidedir):
    """Walk a subagents/ dir -> per-agent dicts {agent_type, model, tokens, tool_count, tools,
    label, depth}.

    Each agent is an `agent-*.meta.json` paired with its `agent-*.jsonl` transcript, at any depth
    (incl. `workflows/wf_*/`). Reads `agentType`, `description`, and `spawnDepth` from the meta.
    `description` is the ORCHESTRATOR's own 3-5 word dispatch label (e.g. "Extract rain-proof 07-23
    reports A") — NOT the agent's prompt or first-user-turn body, which stay unread; that half of
    cc-dashboard's allow-list discipline (never read what the DISPATCHED agent said or was told to
    do beyond this label) is unchanged. The label is deliberately surfaced despite the previous
    stricter posture: a roster keyed on agentType alone made a real cost diagnosis impossible (12×
    plan-executor rows were indistinguishable), the label is orchestrator-authored metadata rather
    than user content or file content, and a postmortem report already quotes the session's own
    turns verbatim — so the marginal disclosure is nil against the diagnostic value of finally being
    able to tell dispatches apart. `journal.jsonl` and other non-agent files have no meta.json and
    are skipped. The meta is size-capped at 64 KB (parse.rs parity). The walk REFUSES to follow
    reparse points (symlinks / junctions) via `_walk_sidecar_files`, and re-checks the DERIVED
    `.jsonl` sibling before opening it, so a planted link can't redirect a read outside the trusted
    ~/.claude/projects tree (parse.rs no-follow parity). Returns ALL agents UNCAPPED — the display
    cap (`_AGENT_SAMPLE_CAP`) is applied in `subagents()` so the dispatched count + token split stay
    exact.
    """
    agents = []
    for meta_path in _walk_sidecar_files(sidedir, '.meta.json'):
        agent_type = 'agent'
        label = ''
        depth = 1
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
                    # bool is an int subclass in Python — exclude it explicitly so `"spawnDepth":
                    # true` doesn't silently pass the isinstance check. Absent/garbage (missing key,
                    # non-int, <=0) falls back to depth 1 (top-level dispatch).
                    sd = m.get('spawnDepth')
                    if isinstance(sd, int) and not isinstance(sd, bool) and sd > 0:
                        depth = sd
        except (OSError, json.JSONDecodeError):
            pass
        jsonl = meta_path[:-len('.meta.json')] + '.jsonl'
        tokens, tools, model = {'total': 0}, Counter(), '?'
        # The .jsonl is a DERIVED sibling path (not a walked dir-entry), so re-check it isn't a
        # planted reparse point before opening — mirrors parse.rs first_model_in_file's symlink guard.
        if os.path.exists(jsonl) and not _is_reparse_point(jsonl):
            tokens = _usage_tokens(jsonl)
            tools = _tool_histogram(jsonl)
            model = _first_assistant_model(jsonl)
        agents.append({'agent_type': agent_type, 'model': model, 'label': label, 'depth': depth,
                       'tokens': tokens['total'], 'tool_count': sum(tools.values()), 'tools': tools})
    return agents


def _split_mcp(name):
    """`mcp__server__tool` -> ('server', 'tool'); a non-MCP tool -> (None, name). Mirrors
    tools.rs::split_mcp: strip the `mcp__` prefix, split on the FIRST `__`."""
    if name.startswith('mcp__'):
        server, sep, tool = name[len('mcp__'):].partition('__')
        return (server, tool if sep else '')
    return (None, name)


def subagents(path):
    """Print the per-subagent roster (agentType, dispatch label, model, tokens, tool calls) + the
    main-vs-subagent token split — the fanout spend §7's main-thread token table breaks out via its
    subagent-aggregate line. The dispatch label (from meta's `description`, see `_collect_subagents`)
    disambiguates rows that would otherwise all share the same agentType (e.g. N× plan-executor); a
    nested dispatch (`spawnDepth` > 1) is marked inline on the agent-type cell. Pass the LIVE
    transcript (sidecars live beside it). Merges every sidecar dir in the session's resume chain
    (`_subagent_dirs`, ENG-3) so a forked/resumed UUID doesn't under-count agents, and caps the
    DISPLAYED roster at `_AGENT_SAMPLE_CAP` (ENG-6) while the count + token split stay exact. Prints
    a one-liner when the session dispatched none."""
    # ENG-3: a session that forks/resumes its UUID mid-run splits its sidecars across
    # `<old-uuid>/subagents/` + `<new-uuid>/subagents/`. Merge every resume-chain dir; each agent's
    # meta/jsonl pair is unique to its dir, so the rosters simply concatenate (no dedup needed).
    dirs = _subagent_dirs(path)
    agents = []
    for d in dirs:
        agents.extend(_collect_subagents(d))
    if not agents:
        print('_No subagents dispatched (no sidecar dir for this transcript)._')
        return
    agents.sort(key=lambda a: a['tokens'], reverse=True)
    main_tokens = _usage_tokens(path)['total']
    sub_total = sum(a['tokens'] for a in agents)
    combined = main_tokens + sub_total
    share = (sub_total / combined * 100) if combined else 0
    by_type = Counter(a['agent_type'] for a in agents)
    print(f'**Subagents dispatched:** {len(agents)} ('
          + ', '.join(f'{n}× {t}' for t, n in by_type.most_common()) + ')')
    print(f'**Token split:** main {main_tokens:,} + subagents {sub_total:,} = {combined:,} '
          f'({share:.0f}% in subagents)')
    print('_Raw transcript usage-token volume (deduped by message.id); not billed cost. §7\'s token '
          'table is the SAME main-thread source, so this split reconciles with it; cost analysis → cc-dashboard._')
    # When the chain merged more than one sidecar dir, surface the UUIDs (dir = <parent>/<uuid>/subagents)
    # so the reader knows a fork was stitched back together — otherwise the count would look unexplained.
    if len(dirs) > 1:
        merged = ', '.join(os.path.basename(os.path.dirname(d)) for d in dirs)
        print(f'_Merged subagent sidecars from {len(dirs)} resume-chain UUIDs: {merged} '
              '(session forked/resumed its UUID mid-run)._')
    print()
    print('| Agent type | Dispatch label | Model | Tokens | Tool calls | Top tools |')
    print('|---|---|---|---:|---:|---|')
    # ENG-6: cap the DISPLAYED roster at _AGENT_SAMPLE_CAP rows (top-N by token spend; `agents` is
    # already sorted desc) — matches cc-dashboard's agent_sample_cap. The dispatched count, by_type
    # tally, and token split above all stay computed over ALL agents; only the table is sampled, and
    # only when the roster actually exceeds the cap.
    for a in agents[:_AGENT_SAMPLE_CAP]:
        top = ', '.join(f'{n}×{c}' for n, c in a['tools'].most_common(4)) or '—'
        # A nested dispatch (an agent spawned BY a subagent, not directly by the orchestrator) is
        # otherwise invisible in this roster — mark it inline on the agent-type cell (↳ + depth)
        # rather than adding a whole extra column, to keep the table narrow.
        agent_col = f"↳ {a['agent_type']} (depth {a['depth']})" if a['depth'] > 1 else a['agent_type']
        label = a['label'] or '—'
        print(f"| {agent_col} | {label} | {_short_model(a['model'])} | {a['tokens']:,} | {a['tool_count']} | {top} |")
    if len(agents) > _AGENT_SAMPLE_CAP:
        print(f'_(showing top {_AGENT_SAMPLE_CAP} of {len(agents)} by token spend; '
              f'the count + token split above cover all.)_')


def tool_breakdown(path):
    """Print the session's tool fingerprint: a main-vs-subagent split of tool-call counts and an
    MCP-server table (server / distinct tools / calls) when any `mcp__` tool fired. Pass the LIVE
    transcript so the sidecars resolve. Walks every sidecar dir in the resume chain (`_subagent_dirs`,
    ENG-3) so a forked/resumed UUID doesn't under-count. Complements §7's flat tool counter with
    attribution."""
    seen = set()
    main_tools = _tool_histogram(path, seen)
    sub_tools = Counter()
    # ENG-3 + ENG-6: merge across ALL resume-chain sidecar dirs via a no-follow walk (refuses
    # symlinks/junctions; replaces glob('**/*.jsonl'), which would follow a symlinked dir). The shared
    # `seen` block-id set spans every file, so a block id is counted once even across dirs. journal.jsonl
    # is a log, not an agent transcript, so it's skipped.
    for sidedir in _subagent_dirs(path):
        for jsonl in _walk_sidecar_files(sidedir, '.jsonl'):
            if os.path.basename(jsonl) == 'journal.jsonl':
                continue
            sub_tools.update(_tool_histogram(jsonl, seen))
    combined = main_tools + sub_tools
    n_main, n_sub = sum(main_tools.values()), sum(sub_tools.values())
    print(f'**Tool calls:** {n_main + n_sub} total — {n_main} main + {n_sub} across subagents')
    if combined:
        # Top 10 with a labeled remainder so the list reconciles with the total above.
        shown = combined.most_common(10)
        line = ', '.join(f'{n}×{c}' for n, c in shown)
        rest = sum(combined.values()) - sum(c for _, c in shown)
        if rest:
            line += f', +{len(combined) - 10} more ×{rest}'
        print('  - ' + line)
    servers = {}
    for name, c in combined.items():
        server, tool = _split_mcp(name)
        if server is not None:
            s = servers.setdefault(server, {'tools': set(), 'calls': 0})
            s['tools'].add(tool)
            s['calls'] += c
    if servers:
        print()
        print('**MCP servers used:**')
        print()
        print('| Server | Distinct tools | Calls |')
        print('|---|---:|---:|')
        for server in sorted(servers, key=lambda s: servers[s]['calls'], reverse=True):
            s = servers[server]
            print(f'| {server} | {len(s["tools"])} | {s["calls"]} |')


# `ballast: <hook-name> <sep> <clause>` up to the FIRST separator, where <sep> is either an em
# dash or a plain '--' (at least one hook emits '--' with a variable clause tail, e.g.
# "ballast: dev-process-nudge -- 1 process already referencing this project" — without the '--'
# alternative each distinct clause fragmented into its own fallback tally key instead of counting
# under the hook name). Some real fires have no separator at all (e.g. "⚓ ballast: principles
# loaded") — that's the genuine D2 fallback case (tally by the full line), not a regex bug to chase.
_HOOK_FIRE_NAME_RE = re.compile(r'ballast:\s*(.+?)\s*(?:—|--)')


def _hook_fire_name(content):
    """Extract the `<name>` from a hook_system_message content line's `ballast: <name> — <clause>`
    segment, or None if that segment isn't present (caller falls back to the full line)."""
    if not isinstance(content, str):
        return None
    m = _HOOK_FIRE_NAME_RE.search(content)
    return m.group(1).strip() if m else None


def hook_fires(path):
    """Tally `hook_system_message` attachment lines (each a ballast conditional hook's per-fire
    visibility one-liner — `{content, hookName, hookEvent, toolUseID}`, characterized live
    2026-07-10, registered in KNOWN_ATTACHMENT_TYPES below) by hook name.

    `content` is the user-visible message, typically `<emoji> ballast: <hook-name> — <clause>`.
    When it matches that shape, `<hook-name>` is the tally key; otherwise the full content line is
    (fail-soft: an unexpected shape is still counted under its own text rather than dropped or
    crashing the dump — see `_hook_fire_name`). Prints 'no hook fires recorded' when the transcript
    has none. This reads an already-registered attachment type — it does not touch the KNOWN_*
    drift registries below.
    """
    counts = Counter()
    for d in load_lines(path):
        if d.get('type') != 'attachment':
            continue
        att = d.get('attachment')
        if not isinstance(att, dict) or att.get('type') != 'hook_system_message':
            continue
        content = att.get('content')
        if not isinstance(content, str) or not content.strip():
            continue
        name = _hook_fire_name(content) or content.strip()
        counts[name] += 1
    if not counts:
        print('no hook fires recorded')
        return
    print('**Hook fires:**')
    print()
    for name, c in counts.most_common():
        print(f'- {name}: {c}')
    print(f'- **Total:** {sum(counts.values())}')


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
    # already skips it; not turn/usage-bearing. Likely future v3 input for inventorying hook fires
    # per session. Added per extend-never-delete.
    'hook_system_message',
    # `task_status` — characterized live 2026-07-22 (sighted 2026-07-20): a background-task status
    # record — `{taskId, taskType, description, status, deltaSummary, outputFilePath}`. Sibling of
    # the task-notification shapes the parser already gates (origin.kind on user lines, commandMode
    # on queued_command), but a distinct attachment type; carrier is `type="attachment"` so turn
    # counting already skips it. Added per extend-never-delete.
    'task_status',
    # `mcp_instructions_delta` — REAL as of 2026-07-21 (registered 2026-07-22): MCP server
    # connect/disconnect churn emits `{addedBlocks, addedNames, removedNames}` (verified ×3 in one
    # transcript, claude-in-chrome attach/detach). The 2026-07-07 PHANTOM classification (zero
    # structural occurrences, every hit prompt-text) was correct for its corpus but is obsolete —
    # the shape now occurs structurally; the tripwire's assertNotIn pin flipped with it. Carrier is
    # `type="attachment"`, not turn/usage-bearing. Added per extend-never-delete.
    'mcp_instructions_delta',
})
KNOWN_COMMAND_MODES = frozenset({'prompt', 'task-notification'})
KNOWN_PROMPT_SOURCES = frozenset({'typed', 'sdk', 'system', 'queued'})
# origin.kind axis — extract.py uniquely branches on it (`_is_real_user_msg` drops task-notification
# user lines; `_attachment_prompt` drops the auto-continuation goal echo), an axis drift.rs has no
# equivalent for. A novel origin.kind would silently slip those gates.
KNOWN_ORIGIN_KINDS = frozenset({'task-notification', 'auto-continuation', 'human'})
# Leading content-tag NAMES the parser recognizes (without the angle brackets). Unlike drift.rs's
# string-only raw scan, the parsed-dict approach below ALSO sees array-form content tags (`ide_*`),
# so they are included here (closes drift.rs's documented array blind spot rather than inheriting it).
KNOWN_USER_CONTENT_TAGS = frozenset({
    'command-name', 'command-message', 'bash-input', 'bash-stdout', 'bash-stderr',
    'local-command-stdout', 'local-command-caveat', 'system-reminder', 'task-notification',
    'ide_opened_file', 'ide_selection', 'ide_diagnostics',
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
    """Tally a novel origin.kind from EITHER origin site extract.py gates on — the top-level
    `origin` on user lines (`_is_real_user_msg` drops kind=='task-notification') AND the
    attachment-nested `attachment.origin` (`_attachment_prompt` drops kind=='auto-continuation').
    Observing only one site would let a novel kind on the OTHER slip its gate AND the canary — the
    silent miscount the canary exists to surface (and would leave 'auto-continuation', an
    attachment-only kind, unreachable in the registry)."""
    if isinstance(origin, dict):
        ok = origin.get('kind')
        if isinstance(ok, str) and ok not in KNOWN_ORIGIN_KINDS:
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
        # open with '<…' (an MCP tool returning '<result>…'), which would mint spurious drift. The
        # parsed-dict flatten makes this belt-and-suspenders (a tool_result block has no `text` key),
        # but keeping the explicit guard matches drift.rs and covers the co-resident-prose case.
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


def drift(path):
    """Format-drift canary: print a per-axis tally of every transcript shape OUTSIDE the known
    registries, one `kind<TAB>value<TAB>count` row, sorted by kind asc / count desc / value asc.
    Pure diagnostic — ALWAYS exits 0; the presence of rows (anything other than the literal
    'no drift') is the signal the orchestrator keys the report caveat off. Prints 'no drift' when
    clean so a human running it sees an explicit all-clear."""
    acc = _collect_drift(path)
    rows = sorted(acc.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))
    if not rows:
        print('no drift')
        sys.exit(0)
    for (kind, value), count in rows:
        print(f'{kind}\t{value}\t{count}')
    sys.exit(0)


# SR-1 (D1): the eight bulk dumps `bundle` runs in one process, in the order they're written to
# `<out>/<name>.md` and reported on stdout. Kept as one ordered list rather than reusing
# SUBCOMMANDS so bundle's dump set can't silently drift if a future non-bulk subcommand (e.g.
# `topic-slug`, which takes an extra argv) is added to that dict.
_BUNDLE_DUMPS = [
    ('user-msgs', user_msgs),
    ('invocations', invocations),
    ('edits', edits),
    ('assistant-text', assistant_text),
    ('subagents', subagents),
    ('tool-breakdown', tool_breakdown),
    ('stats', stats),
    ('hook-fires', hook_fires),
]


def bundle(path, out_dir):
    """Run every dump in `_BUNDLE_DUMPS` once against `path`, writing each to
    `<out_dir>/<name>.md`, plus a `manifest.json` (D1 — SR-1 one-shot bundle extraction). The
    orchestrator runs this ONE inline invocation instead of ~7 serial `ballast-extract` round-trips
    and dispatches leaves against the resulting files, never re-invoking extract.py per dump.

    Bounded-write rule: `out_dir` is created (parents ok) if it doesn't exist. If it exists, is
    non-empty, and has no `manifest.json`, refuse (exit 2) rather than write into a directory this
    tool didn't create. A prior `manifest.json` marks the re-run case — but the re-run allowance
    keys on that manifest belonging to THIS transcript, not merely existing: its `transcript` field
    is parsed and compared to `os.path.abspath(path)` (a parse failure counts as a mismatch); on
    mismatch, refuse (exit 2) rather than silently overwrite a different transcript's bundle dir
    with a stale `diffs/` subdir left mismatched to the new report. A matching manifest overwrites
    in place (the SR-6 partial-redispatch path reuses the same bundle dir).

    Per-dump isolation: each dump runs in its own try/except; a failure is recorded as
    `status: "error"` (with the exception text) in the manifest, and the loop continues — one
    broken dump never blocks the other seven. Invariant: `status: "ok"` iff `<out_dir>/<name>.md`
    on disk is from THIS run — on failure, any file a PREVIOUS run left at that path is
    best-effort deleted so file state never contradicts the manifest.

    The metrics block (transcript_lines/user_turns/time_window/compacted) runs in its own
    try/except, independent of the per-dump loop above: on failure the manifest is still written,
    with `metrics` present but null-valued and a `metrics_error` key carrying the exception text,
    so a crash here never strands dumps already on disk with no manifest.json (which would trip
    the bounded-write refusal on the next run with no recovery path). Exit 0 iff every dump AND the
    metrics block succeeded, else exit 1 (with a one-line stderr summary of what failed).

    stdout on success (D1): the manifest path, one status line per dump, then a final one-line
    metrics summary — the orchestrator picks its scaling tier from THIS tool result, without
    needing to read the manifest file itself.
    """
    import contextlib
    import datetime
    import io

    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        print(f'--out path exists and is not a directory: {out_dir}', file=sys.stderr)
        sys.exit(2)
    manifest_path = os.path.join(out_dir, 'manifest.json')
    if os.path.isdir(out_dir):
        existing = os.listdir(out_dir)
        if existing and 'manifest.json' not in existing:
            print(f'refusing to write into a non-empty dir with no manifest.json: {out_dir} '
                  f'(pass an empty/new dir, or a dir this tool already wrote)', file=sys.stderr)
            sys.exit(2)
        if 'manifest.json' in existing:
            # E4: a prior manifest.json only licenses the re-run if it belongs to THIS
            # transcript — otherwise this would silently overwrite a different transcript's
            # bundle. Tolerate a parse failure by treating it as foreign (refuse) rather than
            # risking a false "same transcript" match.
            try:
                with open(manifest_path, encoding='utf-8') as f:
                    prior_transcript = json.load(f).get('transcript')
            except (OSError, ValueError):
                prior_transcript = None
            if prior_transcript != os.path.abspath(path):
                print(f'{out_dir} already holds a bundle for a different transcript '
                      f'({prior_transcript!r}) — pass a fresh --out', file=sys.stderr)
                sys.exit(2)
    else:
        os.makedirs(out_dir, exist_ok=True)

    files_meta = {}
    status_lines = []
    failed = []
    for name, func in _BUNDLE_DUMPS:
        buf = io.StringIO()
        dump_file = os.path.join(out_dir, f'{name}.md')
        try:
            with contextlib.redirect_stdout(buf):
                func(path)
            text = buf.getvalue()
            with open(dump_file, 'w', encoding='utf-8') as f:
                f.write(text)
            lines = text.count('\n')
            files_meta[name] = {'file': f'{name}.md', 'lines': lines, 'status': 'ok'}
            status_lines.append(f'{name}: ok ({lines} lines)')
        except Exception as e:
            failed.append(name)
            # E3: keep file state honest with the manifest — a PREVIOUS run's file must not
            # survive under an "error" status, since a consumer honoring the manifest would
            # otherwise pick up good-looking-but-unaccounted data. Best-effort: a delete failure
            # here must not mask the original dump failure.
            try:
                if os.path.exists(dump_file):
                    os.remove(dump_file)
            except OSError:
                pass
            files_meta[name] = {'file': f'{name}.md', 'lines': 0, 'status': 'error', 'error': str(e)}
            status_lines.append(f'{name}: error — {e}')

    # Metrics computed once here (independent of per-dump success/failure above) and shared
    # between the manifest and the final stdout summary line — D1's shared-counting-path
    # requirement for user_turns (must equal what `user-msgs` itself emits). E2: guarded so a
    # failure here still lets the manifest land (see docstring) instead of stranding the dumps
    # already written above with no manifest.json at all.
    metrics_error = None
    try:
        transcript_lines = _count_lines(path)
        user_turns = sum(1 for _ in _user_msg_entries(path))
        lo, hi = _time_window(path)
        summaries, boundaries = _compact_markers(path)
        compacted = bool(summaries or boundaries)
        metrics = {
            'transcript_lines': transcript_lines,
            'user_turns': user_turns,
            'time_window': [lo or '', hi or ''],
            'compacted': compacted,
        }
    except Exception as e:
        metrics_error = str(e)
        metrics = {
            'transcript_lines': None,
            'user_turns': None,
            'time_window': None,
            'compacted': None,
        }

    manifest = {
        'schema': 1,
        'transcript': os.path.abspath(path),
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'files': files_meta,
        'metrics': metrics,
    }
    if metrics_error is not None:
        manifest['metrics_error'] = metrics_error
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    print(manifest_path)
    for line in status_lines:
        print(line)
    if metrics_error is not None:
        print(f'metrics unavailable: {metrics_error}')
    else:
        print(f'lines={transcript_lines} user_turns={user_turns} '
              f'compacted={"yes" if compacted else "no"} window={lo or "?"}..{hi or "?"}')

    if failed:
        print(f'{len(failed)} dump(s) failed: {", ".join(failed)}', file=sys.stderr)
    if metrics_error is not None:
        print(f'metrics computation failed: {metrics_error}', file=sys.stderr)
    if failed or metrics_error is not None:
        sys.exit(1)
    sys.exit(0)


def resolve():
    """Print the current session's live transcript path + any PreCompact archives.

    Resolves the session UUID from the CLAUDE_CODE_SESSION_ID env var (set by Claude
    Code), so the skill can find "its own" transcript and backups deterministically —
    no UUID guessing, no cwd-encoding. Takes NO transcript-path argument.

    Output (stdout):
        UUID: <uuid>
        LIVE: <path or (not found)>
        ARCHIVES (oldest->newest): one indented path per line, or "ARCHIVES: (none)"
    """
    import glob as _glob
    uuid = os.environ.get('CLAUDE_CODE_SESSION_ID', '').strip()
    if not uuid:
        print('ERROR: CLAUDE_CODE_SESSION_ID not set — fall back to the manual transcript search',
              file=sys.stderr)
        sys.exit(1)
    home = os.path.expanduser('~')
    live = _glob.glob(os.path.join(home, '.claude', 'projects', '*', f'{uuid}.jsonl'))
    # Archive filenames are "<timestamp>_<trigger>_<uuid>.jsonl"; the timestamp prefix
    # sorts chronologically, so a plain sort gives oldest->newest.
    archives = sorted(_glob.glob(os.path.join(home, '.claude', 'compact-backups', f'*_{uuid}.jsonl')))
    print(f'UUID: {uuid}')
    print(f'LIVE: {live[0] if live else "(not found)"}')
    if archives:
        print('ARCHIVES (oldest->newest):')
        for a in archives:
            print(f'  {a}')
    else:
        print('ARCHIVES: (none)')


SUBCOMMANDS = {
    'compact-check': compact_check,
    'time-window': time_window,
    'user-msgs': user_msgs,
    'invocations': invocations,
    'edits': edits,
    'assistant-text': assistant_text,
    'subagents': subagents,
    'tool-breakdown': tool_breakdown,
    'topic-slug': topic_slug,
    'line-count': line_count,
    'stats': stats,
    'drift': drift,
    'hook-fires': hook_fires,
    # 'bundle' is deliberately NOT in this dict — it takes an extra `--out <dir>` argument that
    # this single-arg `SUBCOMMANDS[sub](path)` dispatch can't express, so main() special-cases it
    # the same way it already special-cases `resolve` (no transcript-path arg).
}


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sub = sys.argv[1]
    # `resolve` takes no transcript-path arg — it discovers paths from the env var.
    if sub == 'resolve':
        resolve()
        return
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    path = sys.argv[2]

    # `bundle` is deliberately not in SUBCOMMANDS (see the dict's comment above) — let it through
    # the unknown-subcommand check here so it reaches its own dispatch below.
    if sub != 'bundle' and sub not in SUBCOMMANDS:
        print(f'Unknown subcommand: {sub}', file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    # Shared existence check (E5): both the bundle branch and the generic dispatch below need
    # it — hoisted once above the branching rather than duplicated per branch.
    if not os.path.exists(path):
        print(f'Transcript not found: {path}', file=sys.stderr)
        sys.exit(2)

    if sub == 'bundle':
        # `bundle` needs a second positional-ish arg (`--out <dir>`) that the generic
        # SUBCOMMANDS[sub](path) dispatch below can't carry — parsed here, mirroring `resolve`'s
        # special-casing above.
        out_dir = None
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == '--out' and i + 1 < len(args):
                out_dir = args[i + 1]
                i += 2
            else:
                i += 1
        if not out_dir:
            print('bundle requires --out <dir>', file=sys.stderr)
            sys.exit(2)
        bundle(path, out_dir)
        return

    if sub in ('topic-slug', 'stats'):
        # These two accept optional extra args (E5: through real parameters, not sys.argv reads
        # inside the functions themselves) — forward the CLI's extras through positionally.
        SUBCOMMANDS[sub](path, *sys.argv[3:])
        return

    SUBCOMMANDS[sub](path)


if __name__ == '__main__':
    main()
