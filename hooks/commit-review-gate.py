#!/usr/bin/env python
"""commit-review-gate.py -- PreToolUse hook (matcher "Bash|PowerShell"), SHADOW MODE ONLY.

WHAT THIS IS: the enforcement half of "review-gated autonomous commit" -- before an autonomous
`git commit`, ONE of these must hold: (O1) a review launched THIS commit-cycle and covers the
staged work, credited via any of THREE evidence channels -- (a) a review Skill tool_use
(code-review/simplify for code; for a docs/meta commit EITHER durable-docs OR a code review --
see O3's one-way widening), (b) a COMPLETED sidecar review shelled
out via Bash (`ballast-review ...`, or a raw `claude -p "/code-review ..."`) -- native /code-review
went user-invoke-only in CC 2.1.215, so autonomy runs it in a headless sibling session; only a
FINISHED, NON-ERRORED one (tool_result paired, is_error absent) counts, and only for commits whose
O3 route accepts code-review -- which since the O3 settle is BOTH code AND docs/meta commits (a
code-review over a docs-only diff is a HEAVIER review than the docs gate, not a wrong one), a
sidecar being a code-review run by construction, and (c) a user-typed `/code-review` slash-command
echo that lands in the transcript as a `<command-name>` blob rather than a Skill event; (O2) the
staged diff is genuinely trivial (whitespace-only, or a small comment/blank-only change); (O3) is
not a separate OUT -- it just routes WHICH skill counts as "the review": a code commit accepts the
code skills; a docs/meta commit accepts durable-docs UNION the code skills (a full code-review
satisfies a docs commit). One-way ONLY: the code route never accepts durable-docs, since a
docs-gate pass says nothing about code. Evaluated BEFORE all of them: a staged set lying entirely
under `.claude/` or `.notes/` ALLOWs as `session-output` -- deterministic artifacts of flows that
carry their own discipline, committed by standing rule (see _all_session_output).
Full design: `.notes`-equivalent brainstorming spec `2026-06-29-review-gated-autonomous-commit.md`
(re-targeted to ballast paths here; see that doc for the empirical case each rule traces back to).

SHADOW MODE (this build): the gate computes the FULL decision every single qualifying commit, but
NEVER blocks -- it always exits 0 and records the decision to a log file instead of an exit-2
denial. Rationale (from the design doc, load-bearing, do not "helpfully" flip this without reading
it first): a hard gate that's WRONG even once teaches the exact rationalization pattern that killed
every prior attempt at this ("the hook fired after the commit", "obviously a false positive, skip
it"). Shadow reveals a false-block by OBSERVING it in the log, not by CAUSING a blocked commit that
gets worked around. Only after a real observation window with zero false-blocks on the canonical
`--fix` loop and the docs/trivial paths does flipping the one exit-0-vs-exit-2 line become safe.
That flip is explicitly OUT OF SCOPE for this build (see the plan/ship doc).

KILL SWITCH: if `<claude-home>/.commit-gate-off` exists, this hook exits 0 IMMEDIATELY -- before
reading stdin, before any git/transcript work, and WITHOUT writing a log line (an operator reaching
for the kill switch wants zero gate activity, not a silenced-but-still-running gate).

FAIL-OPEN CONTRACT (the hook PROCESS, not the gate DECISION): this fires on every single Bash and
PowerShell call in every session with the plugin enabled, so a crash here is not a one-off -- it is
a standing "every terminal command now throws a traceback" bug. Every code path below either
returns a decision or degrades to a defensive default; the two outermost boundaries (stdin parsing
in main(), and the whole evaluate-a-qualifying-commit() body) are wrapped in broad try/except so
NOTHING here can propagate an exception out of main(). In SHADOW mode the exit code is always 0
regardless of what the wrapped decision logic concludes -- "fail open" and "fail closed on
uncertainty" are two different axes: the PROCESS always fails open (never crashes, never blocks);
the DECISION LOGGED for an uncertain case (e.g. an unreadable transcript) is WOULD-BLOCK, which is
the fail-CLOSED posture the eventual enforce-mode flip will honor. See `_evaluate()`'s docstring.

WHY THIS CAN'T FINGERPRINT THE DIFF OR TIMESTAMP-COMPARE review-vs-edit (the two frames the design
doc's fanout explicitly killed, empirically, on real transcripts):
  - PostToolUse-on-Skill fires at LAUNCH, not completion, so any snapshot taken at that event
    captures PRE-fix bytes.
  - `/code-review --fix` (and its siblings) write their fix-edits as ordinary Edit tool_use events
    MINUTES after the review launched, right before the commit, with no clean re-review event.
    That makes `last_edit_ts > last_review_ts` on the CANONICAL, entirely legitimate loop -- a
    naive "review must be newer than every edit" rule FALSE-BLOCKS the exact commit it must allow.
  - Those `--fix` edits are event-shape-IDENTICAL to a manual post-review edit (both are plain Edit
    tool_use blocks). The ONLY signal that tells them apart is TURN STRUCTURE: review-launch then
    edits then commit, all in one assistant turn with no user message in between, is forgiven
    (the `--fix` shape); review-launch then a NEW user message then edits then commit is NOT (a new
    task snuck in after the review and its edits were never looked at). That structure lives only
    in the transcript, which is why this hook reads it instead of comparing bare timestamps.
  - `core.autocrlf=true` breaks `hash-object(working) == index blob sha` fingerprinting on ordinary
    CRLF files, which is why O2's whitespace check below uses `git diff --cached -w --quiet`
    (semantic, ignores line-ending noise) rather than any content-hash comparison.

STATELESS BY DESIGN (the gate DECISION): no marker files, no per-repo state. Every signal is
re-derived at commit time from (a) git itself and (b) the append-only transcript (+ PreCompact archives). A marker file is
per-repo shared state that a CONCURRENT session can misread as its own review (this user routinely
runs concurrent sessions against the same repo) -- re-deriving from each session's OWN transcript
path is multi-session-correct by construction, with no TTL/rot to manage. The one marker file this
hook does keep (the per-SESSION retry marker, see the retry-dedup section) carries no decision
signal at all -- it only de-duplicates shadow LOG statistics, and being session-keyed it is
likewise unreadable by a concurrent session.

REUSES `skills/session-postmortem/scripts/extract.py` for transcript line-loading (`load_lines`) and the
canonical "is this a genuine user turn" rule (`_user_prompt`) rather than writing a second JSONL
parser -- see the sys.path wiring below. This hook DOES add its own single-pass event walk
(`_scan_source`) for Bash/Skill/Edit tool_use extraction + tool_result pairing, because extract.py
has no existing helper that pairs a Bash tool_use with its tool_result by id (its `invocations()`
only pairs Skill/Agent/Workflow) -- that pairing is what lets this hook tell a COMPLETED prior
commit (has a tool_result) apart from the CURRENT in-flight one (does not have one yet, since
PreToolUse fires before the tool runs), which is exactly what COMMIT_BOUNDARY needs.

TESTABILITY: all gate state (kill-switch, shadow log, optional config, compact-backups scan root)
resolves under `_claude_home()`, which is `~/.claude` in production and overridable via the
BALLAST_CLAUDE_HOME env var for hermetic tests (mirrors the existing BALLAST_PYTHON pattern
run.sh already uses). hooks.json's real invocation never sets that var, so production always
resolves the genuine `~/.claude` -- this exists solely so `test_commit_review_gate.py` can point
every path at a throwaway temp dir instead of writing into the developer's real `~/.claude` state.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- extract.py reuse --------------------------------------------------------------------------
# Path(__file__).parent.parent (hooks/ -> repo root) / skills / session-postmortem / scripts, per
# the plan's explicit instruction -- keeps this hook working from a checked-out plugin root
# regardless of cwd. Updated for the session-postmortem scripts/ convention move (2026-07-22).
_EXTRACT_DIR = Path(__file__).resolve().parent.parent / "skills" / "session-postmortem" / "scripts"
sys.path.insert(0, str(_EXTRACT_DIR))
try:
    import extract  # noqa: E402  -- session-postmortem's transcript-parsing module
except Exception:
    extract = None  # handled defensively at every call site (see _scan_source)


class _TranscriptUnreadable(Exception):
    """Raised when the LIVE transcript_path can't be opened/parsed at all.

    Deliberately distinct from a generic internal error: the design spec calls this out BY NAME
    as the case that becomes fail-CLOSED once enforce mode ships (exit 2), and in shadow mode it
    gets its own log reason (`transcript-unreadable`) rather than folding into `internal-error:*`.
    A missing/corrupt COMPACT-BACKUP archive is NOT this -- backups are best-effort extra signal
    (most sessions never compact), so a bad backup file is silently skipped, not fatal.
    """


# =================================================================================================
# 0. Paths / config -- all resolved under _claude_home() so tests can redirect every side effect.
# =================================================================================================

def _claude_home():
    """Base `~/.claude` dir for kill-switch, shadow log, optional config, compact-backups scan.

    Override via BALLAST_CLAUDE_HOME (test-only -- hooks.json never sets this, so production
    always resolves the real `~/.claude`; Path.home() itself is never monkeypatched).
    """
    override = os.environ.get("BALLAST_CLAUDE_HOME")
    return Path(override) if override else (Path.home() / ".claude")


def _kill_switch_path():
    return _claude_home() / ".commit-gate-off"


def _log_path():
    return _claude_home() / "commit-gate-shadow.log"


def _config_path():
    return _claude_home() / "commit-gate.json"


def _compact_backups_dir():
    return _claude_home() / "compact-backups"


# =================================================================================================
# 1. Command parsing -- "does this qualify as a standalone git commit at all?"
# =================================================================================================

# Sequencers that chain a SECOND command onto this one. Deliberately the SAME three git-commit-
# guard.sh treats as sequencers (&&, ||, ;) -- NOT a bare pipe `|`, and not matched against raw
# text but against a QUOTE-STRIPPED copy (see _strip_quotes) so `-m "build && ship"` doesn't trip
# this on the commit MESSAGE contents.
_SEQ_RE = re.compile(r"&&|\|\||;")

# `git commit`, tolerating the same handful of GLOBAL flags git-commit-guard.sh's own normalizer
# strips (git -c k=v commit / git --no-pager commit / etc.) so a pager-disabled or -c-wedged
# invocation still qualifies. Word-bounded so `git commitment-plan` or `gitcommit` can't match.
# KNOWN LIMITATION (F5): `git -C <quoted-path> commit` is NOT recognized once _strip_quotes has
# blanked the quoted path argument (the `-C \S+` alternative below expects the path token itself,
# but a quoted path collapses to nothing) -- this fails toward NOT evaluating (no false block,
# just a missed evaluation/log line), and is accepted as-is: -C-redirected commits are atypical
# for this harness's autonomous-commit shape and are themselves discouraged here.
_COMMIT_RE = re.compile(
    r"\bgit\s+(?:(?:-c|-C)\s+\S+\s+"
    r"|(?:--git-dir|--work-tree)[=\s]\S+\s+"
    r"|(?:--no-pager|--paginate|-p)\s+)*commit\b"
)

# Short-flag cluster containing 'a' (git commit -a / -am / -av ...) -- the auto-stage flags that
# make `git diff --cached` alone the wrong staged-set source (see _staged_files).
_AUTO_STAGE_RE = re.compile(r"(?<!\S)-[a-zA-Z]{1,6}(?!\S)")


def _strip_quotes(s):
    """Remove $(...) subshells and quoted-string contents so sequencer/flag scanning can't be
    fooled by a commit MESSAGE that happens to contain '&&', '-a', etc. Mirrors the same trick
    package-install-guard.py uses for its verb scan."""
    s = re.sub(r"\$\([\s\S]*?\)", " ", s)
    s = re.sub(r'"[^"]*"', " ", s)
    s = re.sub(r"'[^']*'", " ", s)
    return s


def _looks_like_commit_call(cmd):
    """True if `cmd` invokes `git commit` and isn't a --dry-run (which creates no commit object).
    Used BOTH for the current call (in _is_standalone_commit) and for scanning HISTORICAL Bash
    tool_use commands in the transcript when locating COMMIT_BOUNDARY -- a historical commit call
    doesn't need to be standalone (it already ran), it just needs to have actually committed."""
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    s = _strip_quotes(cmd)
    if not _COMMIT_RE.search(s):
        return False
    if re.search(r"--dry-run\b", s):
        return False
    return True


# --- sidecar review detection (O1 channel b) ---------------------------------------------------
# A sidecar review is a headless sibling `claude` session running the native /code-review engine,
# launched EITHER via the `bin/ballast-review` shim (the sanctioned path) or raw as
# `claude -p "/code-review ..."` typed directly. Native /code-review went user-invoke-only in
# CC 2.1.215, so this Bash-transport is how an autonomous cycle still gets a real review pass.
# The negative lookbehind pins the name to INVOCATION position (start of command, or after a
# space/separator): a path-qualified `bin/ballast-review` or `./ballast-review` is a file MENTION
# (git add, cat, chmod ...), not the sanctioned bare-name launch (see the shim header's bare-name
# contract), and crediting mentions would skew the shadow measurements toward false ALLOWs in any
# repo where the path string appears (this one, constantly). Residual: an unquoted prose mention
# like `echo ballast-review` still matches -- rare enough to accept over a fragile full parse.
_SIDECAR_REVIEW_RE = re.compile(r"(?<![\w/.-])ballast-review\b")
# Raw form: `claude -p "/code-review ..."` (or the long flag, `--print`). The `.{0,3}` spans the
# opening quote char(s) that sit between the flag and `/code-review` (single/double quote,
# possibly none). Matched against the RAW
# command, NOT a _strip_quotes copy -- the `/code-review` payload lives INSIDE the -p quotes, so
# quote-stripping would blank exactly the token we need to see. The leading alternation pins
# `claude` to INVOCATION position (start of a line/command or right after a shell sequencer or
# command substitution): without it, a mere MENTION -- `echo "claude -p /code-review ..."`, a
# heredoc, a commit message -- would credit a review once its tool_result landed (same false-ALLOW
# class the _SIDECAR_REVIEW_RE lookbehind closes). Residual, accepted: because this necessarily
# matches raw text, a sequencer INSIDE a quoted string (`echo "x; claude -p /code-review"`) still
# false-positives, and an env-prefixed launch (`FOO=1 claude -p ...`) is missed -- both rare; the
# sanctioned transport is the shim, this pattern is best-effort coverage of the raw form.
_RAW_CODE_REVIEW_RE = re.compile(
    r"(?:^|[;&|]\s*|\$\(\s*)claude\s+(?:\S+\s+)*(?:-p|--print)\s+.{0,3}/code-review", re.M)


def _looks_like_sidecar_review(cmd):
    """True if `cmd` launches a sidecar native code-review (shim or raw headless claude call).

    Mirrors _looks_like_commit_call's shape. The `ballast-review` shim name is matched against a
    _strip_quotes copy (the bare command name is never itself quoted), while the raw
    `claude -p "/code-review ..."` form is matched against the RAW command (its /code-review payload
    is quoted -- see _RAW_CODE_REVIEW_RE). A match here only records a CANDIDATE; it counts as a
    real review pass in _o1_core only once its tool_result has landed (a COMPLETED sidecar)."""
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    if _SIDECAR_REVIEW_RE.search(_strip_quotes(cmd)):
        return True
    if _RAW_CODE_REVIEW_RE.search(cmd):
        return True
    return False


def _is_standalone_commit(cmd):
    """True only if `cmd` is a SINGLE git-commit invocation with no compound sequencing.

    "Standalone" is deliberate and narrow: git-commit-guard.sh (registered before this hook, same
    matcher block) already hard-blocks (exit 2) any compound that chains a sequencer immediately
    followed by a git command past its per-command guards -- e.g. `git add -A && git commit`,
    `cd x && git commit`. This hook doesn't need to re-solve that; it just needs to recognize a
    commit that ISN'T wrapped in a compound and evaluate exactly that one. A commit chained with
    something else via && / || / ; (in EITHER order) simply doesn't qualify here -- exit 0, no log
    line, no evaluation (see main()): either it was blocked upstream, or it's genuinely not the
    single-purpose commit-review shape this gate is built to reason about.

    Deliberately NOT special-cased here: "pure --amend/reword" (excluded by the design spec's
    step 1). A message-only `--amend --no-edit` with nothing newly staged falls through naturally
    to step 2's empty-staged-diff check (the index already equals HEAD, so `git diff --cached` is
    empty regardless of --amend) -- no separate amend-detection logic needed, and importantly an
    `--amend` that DOES carry new staged content is correctly still gated (amend must not become a
    way to sneak an unreviewed change past the gate under the guise of "just fixing the message").
    """
    stripped = _strip_quotes(cmd)
    if _SEQ_RE.search(stripped):
        return False
    return _looks_like_commit_call(cmd)


def _has_auto_stage_flag(stripped_cmd):
    """True if the command carries -a/-am/--all (git commit's "stage all tracked modifications
    first" flags) -- signals that `git diff --cached` alone may under-report the staged set."""
    if re.search(r"(?<!\S)--all(?!\S)", stripped_cmd):
        return True
    for m in _AUTO_STAGE_RE.finditer(stripped_cmd):
        if "a" in m.group(0)[1:]:
            return True
    return False


# --- pathspec extraction (the by-path / empty-index gap) ----------------------------------------
# `git commit <paths>` records the WORKING-TREE state of exactly those paths regardless of the
# index -- so under this repo's documented commit-by-path hygiene the gate's `git diff --cached`
# staged set is EMPTY (a fully-clean index scores `ALLOW empty-staged-diff` with no evaluation) or
# PARTIAL (a `git add -p`'d index under-scopes; observed: staged=2 while the commit carried 15).
# Recovering the pathspecs lets _staged_files diff HEAD over precisely what the commit will record.

# `commit`-subcommand flags that consume the FOLLOWING token as their value -- that token is then
# NOT a pathspec. A `--flag=value` form carries its value inline and consumes nothing extra.
_COMMIT_VALUE_FLAGS = {
    "-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
    "-t", "--template", "--fixup", "--squash", "--author", "--date", "--cleanup",
    "--pathspec-from-file", "--trailer",
}
# GLOBAL flags git tolerates BEFORE the `commit` subcommand -- the SAME shapes _COMMIT_RE strips.
# Value-consuming (space form): `git -c k=v commit`, `git --git-dir /x commit`. The `=` forms of
# --git-dir/--work-tree are handled by a startswith check (they carry their value inline).
_GLOBAL_VALUE_FLAGS = {"-c", "-C", "--git-dir", "--work-tree"}
_GLOBAL_BARE_FLAGS = {"--no-pager", "--paginate", "-p"}


def _strip_one_quote_layer(tok):
    """Strip ONE layer of surrounding matching quotes from a token. The tokenizer keeps a quoted
    run quoted (so a path with spaces survives as ONE token); the pathspec git actually receives is
    the unquoted content."""
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        return tok[1:-1]
    return tok


def _extract_pathspecs(command):
    """Pathspecs from a `git commit <paths>` call (surrounding quotes stripped), or None when
    parsing is UNCERTAIN. Returning None (or an empty list) falls _staged_files back to its
    existing `--cached` behavior -- a missed evaluation is the accepted failure direction here,
    the same tight-fail posture as the documented `-C` limitation (F5): never guess.

    Tokenized against the RAW command (NOT a _strip_quotes copy) because quoted paths matter --
    a `-m "msg"` message must be recognized as a flag value, and a `"path with spaces"` pathspec
    must survive as a single token.
    """
    if not isinstance(command, str):
        return None
    # A quoted run (single OR double) stays one token; everything else splits on whitespace.
    tokens = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', command)

    # Locate the `git` token, then walk forward to the `commit` subcommand, skipping ONLY the
    # global-flag shapes _COMMIT_RE tolerates. Any OTHER token before `commit` (or no `commit` at
    # all) means we can't confidently locate the subcommand boundary -> None (fall back, never guess).
    i = 0
    while i < len(tokens) and tokens[i] != "git":
        i += 1
    if i >= len(tokens):
        return None
    i += 1  # step past `git`
    while i < len(tokens):
        tok = tokens[i]
        if tok == "commit":
            break
        if tok in _GLOBAL_BARE_FLAGS:
            i += 1
            continue
        if tok in _GLOBAL_VALUE_FLAGS:
            i += 2  # global flag + its value token (space form)
            continue
        if tok.startswith(("--git-dir=", "--work-tree=")):
            i += 1  # `=`-form global flag carries its value inline
            continue
        return None  # unrecognized token before `commit` -- uncertain, bail
    else:
        return None  # walked off the end without seeing `commit`
    i += 1  # step past `commit`

    rest = tokens[i:]
    # Unambiguous case: a bare `--` makes EVERYTHING after it a pathspec (git's own convention).
    if "--" in rest:
        dd = rest.index("--")
        return [_strip_one_quote_layer(t) for t in rest[dd + 1:]]

    # No `--`: walk, skipping flags. A known value-consuming flag ALSO swallows its next token.
    pathspecs = []
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok.startswith("-") and tok != "-":
            name = tok.split("=", 1)[0]
            # `--flag=value` carries its value inline (consume nothing extra); a bare
            # value-consuming flag swallows the FOLLOWING token as its value.
            if "=" not in tok and name in _COMMIT_VALUE_FLAGS:
                j += 2
                continue
            j += 1
            continue
        # A non-flag token is a pathspec. RESIDUAL (accepted, shadow mode): an UNKNOWN value-
        # consuming flag not in _COMMIT_VALUE_FLAGS would leave its value token here to be mis-read
        # as a pathspec. Diffing HEAD over a nonexistent path yields an empty slice -- biasing
        # toward the existing empty/under-scope behavior, never a false BLOCK. Same F5 tight-fail
        # polarity; a full git-flag parse isn't worth the fragility for a shadow-only measurement.
        pathspecs.append(_strip_one_quote_layer(tok))
        j += 1
    return pathspecs


# =================================================================================================
# 2. git plumbing helpers
# =================================================================================================

def _run_bytes(args, cwd):
    """Run a git command, return decoded stdout on success or None on any failure/timeout."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, timeout=5)
        if p.returncode != 0:
            return None
        return p.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


def _run_text(args, cwd):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            return None
        return p.stdout
    except Exception:
        return None


def _int_or_none(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None  # git prints '-' for a binary file's added/deleted counts


def _parse_numstat_z(raw):
    """Parse `git diff ... --numstat -z` output into [(path, added, deleted), ...].

    Handles the common (non-rename) record `added\\tdeleted\\tpath\\0` precisely. Best-effort for
    a rename record (`added\\tdeleted\\t\\0old-path\\0new-path\\0` -- an empty path field followed
    by two more NUL-terminated tokens): takes the NEW path. Not tested against every git version's
    exact rename-record shape (no test in this suite exercises a rename), but any misparse here
    just means that one record is skipped -- safe in a SHADOW-only, observational hook.
    """
    if not raw:
        return []
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    files = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        fields = tok.split("\t")
        if len(fields) >= 3 and fields[2] != "":
            files.append((fields[2], _int_or_none(fields[0]), _int_or_none(fields[1])))
            i += 1
            continue
        if len(fields) >= 2 and fields[-1] == "":
            # Rename lead-in: "added\tdeleted\t" with an empty trailing field -> the next one or
            # two tokens are the old/new paths (NUL-terminated instead of tab-separated).
            added, deleted = fields[0], fields[1]
            i += 1
            if i < len(parts):
                i += 1  # skip the old path
            new_path = parts[i] if i < len(parts) else ""
            if new_path:
                files.append((new_path, _int_or_none(added), _int_or_none(deleted)))
            i += 1
            continue
        i += 1  # unrecognized token shape -- skip defensively
    return files


def _diff_slice_args(base_args, pathspecs, extra):
    """Build a `git diff` argv selecting the gate's staged slice: base (`--cached` or `HEAD`), the
    given extra flags, then a `-- <pathspecs>` suffix when the by-path route supplied any. Keeps
    the O2 whitespace/trivial re-reads reading the SAME slice the staged set came from."""
    args = ["git", "diff"] + list(base_args) + list(extra)
    if pathspecs:
        args += ["--"] + list(pathspecs)
    return args


def _staged_files(cwd, command):
    """The staged set + the diff context selecting it: `(files, (base_args, pathspecs, source))`,
    or None if git itself couldn't answer. `files` is [(path, added, deleted), ...]; `source` names
    the route ('cached' | 'auto-stage' | 'pathspec') for the shadow log; `base_args`/`pathspecs`
    let O2's re-reads (_cached_diff_empty_ignoring_ws, _o2_trivial) query the identical slice.

    Three routes, in PRECEDENCE order:
      1. Auto-stage (-a/-am/--all): `git commit` stages tracked modifications itself at commit
         time -- the UNION of whatever's already in the index PLUS unstaged tracked changes. (F2)
         Use `git diff HEAD` UNCONDITIONALLY when the flag is present (not merely when --cached is
         empty): a PARTIALLY-populated index has a non-empty --cached diff, so the old "only fall
         back when empty" branch never fired and the unstaged half `-a` will commit stayed
         invisible. This branch stays FIRST and is mutually exclusive with route 2 -- `-a` +
         explicit pathspecs is a git USAGE ERROR, so there's nothing to disambiguate. NOTE: O2's
         re-reads keep `--cached` here (base_args stays ("--cached",)) -- byte-identical to prior
         behavior; the pre-existing auto-stage/--cached mismatch is out of scope for this change.
      2. By-path (`git commit <paths>`): records working-tree-vs-HEAD for EXACTLY those paths,
         regardless of the index -- so under commit-by-path hygiene the --cached set is empty or
         partial and under-scopes. Diff `HEAD -- <paths>` to see precisely what the commit records
         (the union of staged + unstaged for those paths).
      3. Default: the index as-staged, `git diff --cached` (unchanged).
    """
    if _has_auto_stage_flag(_strip_quotes(command)):
        raw = _run_bytes(["git", "diff", "HEAD", "--numstat", "-z"], cwd)
        if raw is None:
            return None
        return _parse_numstat_z(raw), (("--cached",), (), "auto-stage")

    pathspecs = _extract_pathspecs(command)
    if pathspecs:  # non-empty list only; empty list / None -> default --cached route below
        raw = _run_bytes(
            ["git", "diff", "HEAD", "--numstat", "-z", "--"] + list(pathspecs), cwd)
        if raw is None:
            return None
        return _parse_numstat_z(raw), (("HEAD",), tuple(pathspecs), "pathspec")

    raw = _run_bytes(["git", "diff", "--cached", "--numstat", "-z"], cwd)
    if raw is None:
        return None
    return _parse_numstat_z(raw), (("--cached",), (), "cached")


def _repo_root(cwd):
    out = _run_text(["git", "rev-parse", "--show-toplevel"], cwd)
    return out.strip() if out else cwd


def _in_merge_or_cherry_pick(cwd):
    """True if MERGE_HEAD or CHERRY_PICK_HEAD exists -- these commits aren't newly-authored edits
    (they're finishing a merge/cherry-pick already vetted by its own flow), so they ALLOW."""
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD"):
        out = _run_text(["git", "rev-parse", "--git-path", name], cwd)
        if not out:
            continue
        p = out.strip()
        path = Path(p) if os.path.isabs(p) else Path(cwd) / p
        if path.is_file():
            return True
    return False


def _cached_diff_empty_ignoring_ws(cwd, base_args, pathspecs):
    """O2a: True if `git diff <slice> -w` reports NO differences -- a pure whitespace / line-
    ending change (the autocrlf arm: CRLF-vs-LF is whitespace to `-w`, so this ALLOWS regardless
    of size, sidestepping the content-hash fingerprinting the design doc found autocrlf breaks).

    Reads the SAME slice the staged set came from (base_args/pathspecs from _staged_files): for the
    by-path route that's `git diff HEAD -w --quiet -- <paths>`; for the default/auto-stage routes
    it's the byte-identical `git diff --cached -w --quiet`."""
    try:
        args = _diff_slice_args(base_args, pathspecs, ["-w", "--quiet"])
        p = subprocess.run(args, cwd=cwd, timeout=5)
        return p.returncode == 0
    except Exception:
        return False  # can't determine -- NOT safely empty; falls through to the churn/comment check


# --- O2b: churn + comment-only body-line check ---------------------------------------------------

# Comment-line prefixes by extension. Deliberately conservative: an unrecognized extension has NO
# entry, and _is_comment_or_blank treats that as "not a comment" (never trivial) -- the design
# spec's O2 threshold is explicitly biased TIGHT ("false-block costs a cheap review; false-trivial
# lets code through unreviewed" is the asymmetric cost this errs against).
_COMMENT_PREFIXES = {
    ".py": ("#",), ".rb": ("#",), ".sh": ("#",), ".bash": ("#",), ".zsh": ("#",),
    ".ps1": ("#",), ".pl": ("#",), ".pm": ("#",), ".yaml": ("#",), ".yml": ("#",),
    ".toml": ("#",), ".r": ("#",), ".jl": ("#",), ".ini": ("#", ";"), ".cfg": ("#", ";"),
    # NOTE (F1): deliberately NO bare "*" prefix here. A leading "*" is ambiguous without block-
    # comment state (it's a comment continuation line inside /* ... */, but it's also valid,
    # dangerous C/C++ syntax on its own -- e.g. `*fnptr = &backdoor;` is a pointer dereference,
    # not a comment). This module doesn't track block-comment state (see the module docstring's
    # asymmetric-cost rationale), so it must pick a side: dropping "*" costs an occasional
    # false-block on a real `/* ... */` continuation line (cheap -- one extra review), while
    # keeping it risks a false-trivial that lets a real code line slip through unreviewed
    # (expensive -- exactly the failure mode O2 exists to prevent). Drop it.
    ".js": ("//", "/*", "*/"), ".jsx": ("//", "/*", "*/"),
    ".ts": ("//", "/*", "*/"), ".tsx": ("//", "/*", "*/"),
    ".c": ("//", "/*", "*/"), ".h": ("//", "/*", "*/"),
    ".cpp": ("//", "/*", "*/"), ".hpp": ("//", "/*", "*/"),
    ".java": ("//", "/*", "*/"), ".go": ("//", "/*", "*/"),
    ".rs": ("//", "/*", "*/"), ".cs": ("//", "/*", "*/"),
    ".php": ("//", "#", "/*", "*/"), ".swift": ("//", "/*", "*/"),
    ".kt": ("//", "/*", "*/"), ".scala": ("//", "/*", "*/"),
    ".css": ("/*", "*/"), ".scss": ("//", "/*", "*/"),
    ".sql": ("--",), ".lua": ("--",),
    ".html": ("<!--",), ".htm": ("<!--",), ".xml": ("<!--",), ".md": ("<!--",),
}


def _is_comment_or_blank(body_line, ext):
    t = body_line.strip()
    if not t:
        return True
    prefixes = _COMMENT_PREFIXES.get(ext)
    if not prefixes:
        return False  # unrecognized syntax -- conservatively NOT a comment
    return t.startswith(prefixes)


def _all_body_lines_trivial(diff_text):
    """Walk a `-U0` unified diff (zero context -- every +/- line IS a changed line) and check that
    every changed body line is blank or a pure comment for its file's extension. `-U0` matters:
    with default context, unchanged neighbor lines would ALSO show as +/- adjacent-hunk noise in
    some git configs -- zero-context guarantees we only ever see genuinely changed lines."""
    current_ext = ""
    for line in diff_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            path = line[4:].strip()
            if path.startswith(("a/", "b/")):
                path = path[2:]
            if path and path != "/dev/null":
                current_ext = os.path.splitext(path)[1].lower()
            continue
        if line.startswith(("diff --git", "index ", "@@", "new file mode", "deleted file mode",
                             "similarity index", "rename from", "rename to", "old mode", "new mode",
                             "Binary files")):
            continue
        if line.startswith("+") or line.startswith("-"):
            if not _is_comment_or_blank(line[1:], current_ext):
                return False
    return True


def _o2_trivial(cwd, staged_files, base_args, pathspecs):
    """O2b: churn <= 6 (added+deleted summed across ALL staged files) AND every changed body line
    is blank/pure-comment for its file's syntax. Returns (is_trivial, churn).

    The `-U0` body-line re-read uses the SAME slice the staged set came from (base_args/pathspecs):
    `git diff HEAD -U0 --no-color -- <paths>` for the by-path route, the byte-identical
    `git diff --cached -U0 --no-color` for the default/auto-stage routes."""
    if any(a is None or d is None for _, a, d in staged_files):
        return False, 0  # a binary file is present -- can't verify comment-only content, don't trivial-pass
    churn = sum((a or 0) + (d or 0) for _, a, d in staged_files)
    if churn > 6:
        return False, churn
    raw = _run_bytes(_diff_slice_args(base_args, pathspecs, ["-U0", "--no-color"]), cwd)
    if raw is None:
        return False, churn  # can't verify -- safe default is NOT trivial
    return _all_body_lines_trivial(raw), churn


# =================================================================================================
# 3. O3 routing -- docs/meta vs code, and the accepted-skill sets (+ optional user config merge)
# =================================================================================================

_DOCS_EXTS = {".md", ".mdx", ".txt", ".rst", ".adoc"}
_DOCS_DIR_SEGMENTS = {"memory", ".notes", "brainstorming"}
_DOCS_BASENAMES = {"claude.md", "agents.md", "ideas.md"}
# Hooks/config are CODE, never docs -- a *.json/*.py/*.sh/*.ps1 change (incl. settings.json) can
# alter BEHAVIOR, so it needs the code-review path even if it happens to live under a docs-ish dir.
_CODE_FORCE_EXTS = {".sh", ".py", ".ps1", ".json"}

_DEFAULT_CODE_SKILLS = {"code-review", "diff-review", "simplify"}
_DEFAULT_DOCS_SKILLS = {"durable-docs"}


def _classify_path(path):
    p = path.replace("\\", "/")
    segs = p.split("/")
    base = segs[-1].lower()
    ext = os.path.splitext(base)[1].lower()
    if ext in _CODE_FORCE_EXTS or base == "settings.json":
        return "code"
    if ext in _DOCS_EXTS:
        return "docs"
    if any(s in _DOCS_DIR_SEGMENTS or s.startswith("brainstorm") for s in segs[:-1]):
        return "docs"
    if base in _DOCS_BASENAMES:
        return "docs"
    return "code"


# Session-OUTPUT areas: deterministic artifacts of a flow that carries its own discipline
# (postmortem reports, harness-sweep digests, `.notes/` working notes), committed by standing rule.
# A staged set made up ENTIRELY of them has no review to wait for, so gating it is pure
# false-positive noise in the shadow dataset -- 307 of the observed WOULD-BLOCKs were docs-only and
# many were exactly this shape. Segment-matched (not prefix-matched) so a nested session-output dir
# counts. Deliberately NARROW: README / CLAUDE.md / docs/ are NOT exempt -- the docs discipline is
# precisely what this gate is here to observe. And `.claude/` is only PARTLY session-output: the
# `.claude/skills/` and `.claude/commands/` subtrees hold shipped, durable content (real code and
# permanent-tier docs), so their presence disqualifies the path -- the exemption covers the
# output-series dirs (postmortem/, per-skill report dirs) and `.notes/` only. The disqualifier is
# ADJACENCY-scoped: it applies only to a segment sitting IMMEDIATELY AFTER `.claude`, because
# `skills`/`commands` are common directory names in their own right -- a coincidental
# `.notes/tool/commands/x.md` is an ordinary working note, not shipped content, and a bare
# segment-anywhere test would wrongly gate it.
_SESSION_OUTPUT_ROOTS = {".claude", ".notes"}
_SESSION_OUTPUT_DISQUALIFIERS = {"skills", "commands"}


def _all_session_output(staged_files):
    """True if EVERY staged path lies under a `.claude/` or `.notes/` segment, with no
    `skills`/`commands` segment sitting DIRECTLY under a `.claude` segment (shipped content under
    `.claude/` is not session output; the same dir name elsewhere in the path is coincidental)."""
    for path, _, _ in staged_files:
        segs = path.replace("\\", "/").split("/")
        if not (set(segs) & _SESSION_OUTPUT_ROOTS):
            return False
        for i, seg in enumerate(segs):
            if i > 0 and seg in _SESSION_OUTPUT_DISQUALIFIERS and segs[i - 1] == ".claude":
                return False
    return bool(staged_files)


def _route(staged_files):
    """ALL staged paths classify docs -> 'docs'; anything else (incl. a docs+code mix) -> 'code'."""
    kinds = {_classify_path(p) for p, _, _ in staged_files}
    return "docs" if kinds == {"docs"} else "code"


def _load_accepted_skills(kind):
    """Default accepted-skill set for `kind` ('code' | 'docs'), merged with an optional user-side
    `<claude-home>/commit-gate.json` `{"code_skills": [...], "docs_skills": [...]}`. Personal skill
    names never ship in this file -- that's exactly what the config exists to let a user layer on
    locally (see the plan's genericity requirement).

    O3 SETTLE (one-way widening): the DOCS route accepts the docs set UNION the DEFAULT code set --
    a full code-review over a docs-only diff is a HEAVIER review than the docs gate, not a wrong
    one; the gate only answers "was this reviewed", review-FLAVOR discipline being the
    git-commit-guard nudge's job. ONE-WAY ONLY: the code route never seeds durable-docs (a
    docs-gate pass says nothing about code). Note the docs route widens with the DEFAULT code set,
    NOT the user's `code_skills`: a user-added custom code skill is their CODE-route customization,
    and auto-widening it onto the docs route on their behalf would overreach -- so docs still merges
    only the user's `docs_skills` key below."""
    if kind == "code":
        accepted = set(_DEFAULT_CODE_SKILLS)
    else:
        accepted = set(_DEFAULT_DOCS_SKILLS) | set(_DEFAULT_CODE_SKILLS)
    cfg_path = _config_path()
    try:
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            key = "code_skills" if kind == "code" else "docs_skills"
            extra = cfg.get(key) if isinstance(cfg, dict) else None
            if isinstance(extra, list):
                accepted.update(s.strip() for s in extra if isinstance(s, str) and s.strip())
    except Exception:
        pass  # a broken user config just means "defaults only" -- never crash the gate over it
    return accepted


def _skill_matches(input_skill, accepted):
    """Match both a bare skill name ('code-review') and a plugin-qualified one
    ('some-plugin:code-review') against the accepted set -- accepted names are always bare."""
    if not input_skill:
        return False
    name = input_skill.strip()
    bare = name.rsplit(":", 1)[-1] if ":" in name else name
    return name in accepted or bare in accepted


# =================================================================================================
# 4. O1 core -- transcript event stream, COMMIT_BOUNDARY, and the turn-structure check
# =================================================================================================

def _abs_norm(p, base):
    """Normalize a path (possibly relative, possibly Windows-backslashed) to a lowercase,
    forward-slash absolute form for path-identity comparisons. Purely lexical (os.path.normpath,
    no filesystem access) so it works for a path that no longer exists (a deleted/renamed file)."""
    pp = p.replace("\\", "/")
    if not (pp[:1] == "/" or re.match(r"^[A-Za-z]:/", pp)):
        pp = base.replace("\\", "/").rstrip("/") + "/" + pp
    return os.path.normpath(pp).replace("\\", "/").lower()


def _scan_source(path):
    """One pass (two, only when a Bash/PowerShell commit call is present -- see below) over a
    transcript .jsonl (live OR a compact-backup archive). Returns:
      commits: [(ts, has_result), ...] for Bash/PowerShell tool_use whose command looks like a git
               commit call (not required to be standalone -- see _looks_like_commit_call).
      skills:  [(ts, skill_name), ...] for Skill tool_use events (input.skill).
      edits:   [(ts, file_path), ...] for Edit/Write/MultiEdit tool_use events.
      users:   [ts, ...] for genuine user turns, via extract.py's canonical `_user_prompt` (so a
               mid-turn steer / slash-command echo counts here exactly as the SSoT parsers count it
               as a real turn -- this hook cares about "did a NEW instruction arrive", and a typed
               steer is exactly that).
      reviews: [(ts, has_result), ...] for Bash/PowerShell tool_use launching a SIDECAR review
               (`ballast-review ...` or a raw `claude -p "/code-review ..."`). Paired to its
               tool_result the same way commits are -- a sidecar counts as a review pass (O1
               channel b) only once COMPLETED (has_result=True); a still-streaming/failed one does
               not, since PreToolUse fires before the tool runs.
      echoes:  [(ts, name), ...] for user-typed slash-command echoes -- a `<command-name>...` blob
               in a user message (O1 channel c). `name` is normalized (leading '/' stripped);
               accepted-set matching is DEFERRED to _o1_core, mirroring how the `skills` channel
               defers matching rather than filtering in the scanner.
      min_ts:  earliest timestamp seen in this file (feeds the COMMIT_BOUNDARY session-start
               fallback when no prior commit exists).

    `has_result` requires a SEPARATE lightweight second pass (only run when at least one commit-
    shaped Bash/PowerShell call was found -- the overwhelmingly common case is zero, so this stays
    a single pass for every non-commit-bearing file) that pairs each candidate's tool_use `id`
    against a later `tool_result.tool_use_id`. This is what tells a COMPLETED prior commit apart
    from the CURRENT in-flight one, which cannot have a tool_result yet (PreToolUse fires before
    the tool runs) -- exactly the distinction COMMIT_BOUNDARY needs and extract.py's own
    `invocations()` doesn't provide (it only pairs Skill/Agent/Workflow, not Bash).
    """
    if extract is None:
        raise RuntimeError("extract.py unavailable")

    commits = []          # [ts, has_result] -- mutated in place by the second pass
    pending_ids = {}       # tool_use_id -> index into commits
    reviews = []          # [ts, has_result] -- sidecar reviews, paired like commits below
    pending_review_ids = {}  # tool_use_id -> index into reviews
    echoes = []           # [(ts, name)] -- user-typed slash-command echoes (matching deferred)
    skills = []
    edits = []
    users = []
    min_ts = None

    for d in extract.load_lines(path):
        if not isinstance(d, dict):
            continue
        ts = d.get("timestamp") or ""
        if ts and (min_ts is None or ts < min_ts):
            min_ts = ts

        try:
            if extract._user_prompt(d) is not None and ts:
                users.append(ts)
        except Exception:
            pass  # a malformed line here just means one fewer counted user turn -- not fatal

        # O1 channel c: a user-typed `/code-review` lands as a user message whose STRING content is
        # a `<command-name>...</command-name>` echo (NOT a Skill tool_use -- native /code-review is
        # user-invoke-only now, so it no longer emits one). Capture the raw command name via
        # extract.py's shared `_CMD_NAME_RE`; the cheap startswith() gates the search, and the
        # normalized name is filtered against the accepted set later in _o1_core.
        try:
            if d.get("type") == "user" and ts:
                umsg = d.get("message")
                ucontent = umsg.get("content") if isinstance(umsg, dict) else None
                if isinstance(ucontent, str) and \
                        ucontent.lstrip().startswith(("<command-name>", "<command-message>")):
                    m = extract._CMD_NAME_RE.search(ucontent)
                    if m:
                        echoes.append((ts, m.group(1).strip().lstrip("/")))
        except Exception:
            pass  # a malformed echo line just means one fewer command-echo signal -- not fatal

        if d.get("type") != "assistant":
            continue
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name = c.get("name", "")
            inp = c.get("input")
            if not isinstance(inp, dict):
                inp = {}
            if name in ("Bash", "PowerShell"):
                cmd = inp.get("command", "")
                if isinstance(cmd, str) and _looks_like_commit_call(cmd):
                    idx = len(commits)
                    commits.append([ts, False])
                    tuid = c.get("id")
                    if tuid:
                        pending_ids[tuid] = idx
                # Independent of the commit check: the same call can't be both, but a sidecar review
                # launch is its own candidate, paired to its tool_result via a SEPARATE pending dict
                # so only a COMPLETED sidecar (O1 channel b) ever credits a review pass.
                if isinstance(cmd, str) and _looks_like_sidecar_review(cmd):
                    ridx = len(reviews)
                    reviews.append([ts, False])
                    rtuid = c.get("id")
                    if rtuid:
                        pending_review_ids[rtuid] = ridx
            elif name == "Skill":
                skill_name = inp.get("skill", "")
                if isinstance(skill_name, str) and skill_name:
                    skills.append((ts, skill_name))
            elif name in ("Edit", "Write", "MultiEdit"):
                fp = inp.get("file_path", "")
                if isinstance(fp, str) and fp:
                    edits.append((ts, fp))

    if pending_ids or pending_review_ids:
        for d in extract.load_lines(path):
            if not isinstance(d, dict) or d.get("type") != "user":
                continue
            msg = d.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    # A tool_result lands for FAILURES too (is_error=True): a git commit rejected
                    # by a pre-commit hook, or ballast-review's own exit-1 rejection paths (usage,
                    # --fix refusal, missing CLI). Those are not completions -- crediting an
                    # errored sidecar would let the shim's rejections fake a review pass, and an
                    # errored commit would wrongly reset the review window (_commit_boundary counts
                    # completed commits only). Accepted residual of that polarity: a commit that
                    # actually LANDED but whose tool call was marked errored (a wrapper's nonzero
                    # tail) is ignored by _commit_boundary, widening the window so a stale review
                    # could credit the next commit. That's the rarer edge -- hook-REJECTED commits
                    # (never landed) are common, and the opposite polarity would let each rejected
                    # attempt reset the window and forgive the retry unreviewed.
                    if c.get("is_error"):
                        continue
                    tuid = c.get("tool_use_id")
                    if tuid in pending_ids:
                        commits[pending_ids[tuid]][1] = True
                    if tuid in pending_review_ids:
                        reviews[pending_review_ids[tuid]][1] = True

    return ([(ts, has) for ts, has in commits], skills, edits, users,
            [(ts, has) for ts, has in reviews], echoes, min_ts)


def _gather_events(transcript_path, session_id):
    """Merge _scan_source across the LIVE transcript + every matching PreCompact archive
    (`<claude-home>/compact-backups/*_<session_id>.jsonl`, the naming convention a user-side
    PreCompact archiver writes) -- so a review that launched before a compaction and
    is no longer in the live transcript still counts (the design spec's "review-before-compaction,
    commit-after -> ALLOW" case). The live transcript is REQUIRED to be readable (its failure is
    the `_TranscriptUnreadable` fail-closed-in-spirit path); a missing/corrupt backup is not fatal,
    since most sessions never compact and backups are best-effort extra signal only.
    """
    sources = [transcript_path]
    try:
        backups_dir = _compact_backups_dir()
        if session_id and backups_dir.is_dir():
            sources.extend(str(p) for p in sorted(backups_dir.glob(f"*_{session_id}.jsonl")))
    except Exception:
        pass

    all_commits, all_skills, all_edits, all_users, all_reviews, all_echoes = [], [], [], [], [], []
    min_ts_overall = None
    for i, src in enumerate(sources):
        try:
            commits, skills, edits, users, reviews, echoes, min_ts = _scan_source(src)
        except (OSError, UnicodeDecodeError):
            if i == 0:
                raise _TranscriptUnreadable(src)
            continue  # a bad backup archive is skipped, not fatal
        all_commits.extend(commits)
        all_skills.extend(skills)
        all_edits.extend(edits)
        all_users.extend(users)
        all_reviews.extend(reviews)
        all_echoes.extend(echoes)
        if min_ts and (min_ts_overall is None or min_ts < min_ts_overall):
            min_ts_overall = min_ts

    return (all_commits, all_skills, all_edits, all_users, all_reviews, all_echoes,
            (min_ts_overall or ""))


def _commit_boundary(all_commits, session_start_ts):
    """ts of the most recent COMPLETED (has_result=True) prior git-commit Bash/PowerShell call, or
    session start if none. The CURRENT in-flight commit (this very hook invocation) can never
    contribute here -- it has no tool_result yet -- so it can't accidentally become its own
    boundary and collapse the review window to nothing."""
    prior = [ts for ts, has_result in all_commits if has_result and ts]
    return max(prior) if prior else session_start_ts


def _o1_core(all_skills, all_edits, all_users, all_reviews, all_echoes, boundary,
             accepted_skills, staged_paths, repo_root):
    """The turn-structure check (design spec step 6 / O1). Window = every event with
    ts > COMMIT_BOUNDARY (no upper bound needed -- nothing in the transcript can postdate "now",
    the moment this PreToolUse hook is running). Requires ALL THREE:
      (a) >=1 qualifying review launch in-window. A launch qualifies via ANY of three evidence
          channels: an accepted-set Skill tool_use (bare or plugin-qualified name); a COMPLETED
          sidecar review (`ballast-review`/`claude -p /code-review`, has_result=True); or a
          user-typed `/code-review` command echo whose normalized name is in the accepted set.
      (b) the LAST such launch is AFTER the FIRST edit to a STAGED path in-window (the review had
          something to look at -- a review launched before any work started proves nothing about
          THIS work).
      (c) no user message arrives strictly after that last qualifying launch with a staged-path
          edit following THAT message before the commit -- i.e. no "new task snuck in after the
          review, and its edits were never re-reviewed". Edits with NO intervening user message
          (the `--fix` shape: launch, then fix-edits land minutes later in the SAME turn) are
          explicitly FORGIVEN here -- that is the canonical false-positive guard this whole hook
          exists to get right; see the module docstring's "WHY THIS CAN'T ... TIMESTAMP-COMPARE".
    Returns (ok, detail_str, last_review_ts).
    """
    staged_norm = {_abs_norm(p, repo_root) for p in staged_paths}

    edits_in_window = sorted((ts, fp) for ts, fp in all_edits if ts and ts > boundary)
    users_in_window = sorted(ts for ts in all_users if ts and ts > boundary)

    # Qualifying review launches, drawn from all three O1 channels and tagged with a channel-
    # distinguishing detail label so the shadow ALLOW line names WHICH channel credited the pass
    # (the orchestrator verifies this live). Accepted-set matching happens HERE for the Skill and
    # echo channels (both defer it out of the scanner); the sidecar channel's only gate is
    # completion (has_result) -- a sidecar is a real /code-review run by construction.
    qualifying = [(ts, name) for ts, name in all_skills
                  if ts and ts > boundary and _skill_matches(name, accepted_skills)]
    # O3 routing applies to the sidecar channel too: a sidecar IS a native code-review run, so it
    # credits wherever a code-review Skill launch would. Since the O3 settle the docs/meta route
    # also accepts the code skills (a code-review is a heavier review than the docs gate), so a
    # completed sidecar now credits docs commits too -- consistent, a sidecar IS a code-review. Still
    # ONE-WAY: the code route never accepts durable-docs (_load_accepted_skills), so gating this
    # branch on code-review's presence in the accepted set is exactly the right condition.
    if _skill_matches("code-review", accepted_skills):
        qualifying += [(ts, "sidecar-review") for ts, has_result in all_reviews
                       if ts and ts > boundary and has_result]
    # Deliberate asymmetry vs the sidecar channel: echoes credit at the KEYSTROKE with no
    # completion evidence, because none exists -- a user-typed slash command expands inline in the
    # main session and leaves no tool_result to pair. Over-credit here is bounded by whose action
    # it is: the echo is the USER explicitly invoking a review, and this gate polices AUTONOMOUS
    # commits; a user who types /code-review and interrupts it has made their own call.
    qualifying += [(ts, "command-echo:%s" % name) for ts, name in all_echoes
                   if ts and ts > boundary and _skill_matches(name, accepted_skills)]
    if not qualifying:
        return False, "no-qualifying-review-launch", None

    last_review_ts, last_review_name = max(qualifying, key=lambda x: x[0])

    staged_edits = [(ts, fp) for ts, fp in edits_in_window if _abs_norm(fp, repo_root) in staged_norm]
    if not staged_edits:
        # A qualifying review launched, but no edit to any STAGED path is visible in-window -- we
        # can't confirm the review had this work to look at. Conservative: needs a review.
        return False, "no-staged-edit-visible-in-window", last_review_ts

    first_staged_edit_ts = min(ts for ts, _ in staged_edits)
    if not (last_review_ts > first_staged_edit_ts):
        return False, "review-not-after-first-staged-edit", last_review_ts

    msgs_after_review = [u for u in users_in_window if u > last_review_ts]
    if msgs_after_review:
        earliest = min(msgs_after_review)
        if any(ts > earliest for ts, _ in staged_edits):
            return False, "user-message-then-edit-after-review", last_review_ts

    return True, last_review_name, last_review_ts


# =================================================================================================
# 5. Top-level evaluation + shadow logging
# =================================================================================================

def _evaluate(command, cwd, transcript_path, session_id):
    """Run gate steps 2-6 (staged set -> merge/cherry-pick -> O2 -> O3 route -> O1) for a command
    already confirmed to be a standalone git commit. Returns (decision, reason, staged_paths) where
    decision is the literal string 'ALLOW' or 'WOULD-BLOCK' and staged_paths is the staged path list
    (None when git couldn't answer) -- the retry-dedup key, kept OUT of the log grammar on purpose.
    Never raises for an ordinary git/transcript condition (those all resolve to a decision);
    `_TranscriptUnreadable` and any other exception are caught by the caller (main()), which is what
    makes THIS function's contract "return a decision or raise", not "return a decision or crash".
    """
    staged_result = _staged_files(cwd, command)
    if staged_result is None:
        return "WOULD-BLOCK", "git-unavailable", None
    staged, (base_args, pathspecs, source) = staged_result
    staged_paths = [p for p, _, _ in staged]
    if not staged:
        return "ALLOW", "empty-staged-diff", staged_paths

    if _all_session_output(staged):
        return "ALLOW", "session-output", staged_paths

    if _in_merge_or_cherry_pick(cwd):
        return "ALLOW", "merge-or-cherry-pick-in-progress", staged_paths

    if _cached_diff_empty_ignoring_ws(cwd, base_args, pathspecs):
        return "ALLOW", "trivial-whitespace-only", staged_paths

    trivial, churn = _o2_trivial(cwd, staged, base_args, pathspecs)
    if trivial:
        return "ALLOW", "trivial-churn=%d-comment-or-blank-only" % churn, staged_paths

    kind = _route(staged)
    accepted = _load_accepted_skills(kind)
    repo_root = _repo_root(cwd)

    (all_commits, all_skills, all_edits, all_users, all_reviews, all_echoes,
     session_start) = _gather_events(transcript_path, session_id)
    boundary = _commit_boundary(all_commits, session_start)

    ok, detail, review_ts = _o1_core(all_skills, all_edits, all_users, all_reviews, all_echoes,
                                     boundary, accepted, staged_paths, repo_root)
    # Observability: name the by-path route in the shadow log so the flip decision can tell an
    # empty/under-scoped --cached read apart from a real by-path evaluation. ONLY the pathspec route
    # appends this -- the default/auto-stage ALLOW/WOULD-BLOCK lines stay BYTE-IDENTICAL (the log is
    # being mined for the enforce-flip; don't churn the existing line shapes).
    src_suffix = " src=pathspec" if source == "pathspec" else ""
    if ok:
        return ("ALLOW", "reviewed skill=%s staged=%d%s" % (detail, len(staged_paths), src_suffix),
                staged_paths)

    uncovered = ",".join(sorted(staged_paths)[:5])
    reason = ("need-review kind=%s accepted=%s reason=%s last_review_ts=%s uncovered=%s%s"
              % (kind, "|".join(sorted(accepted)), detail, review_ts or "-", uncovered, src_suffix))
    return "WOULD-BLOCK", reason, staged_paths


def _sanitize_log_field(s):
    """(F3) Strip embedded CR/LF from a value bound for the shadow log and cap its length.

    `reason` (and, defensively, `session_id`) can echo attacker- or user-controlled substrings --
    e.g. a staged FILENAME with an embedded newline flows into the `uncovered=...` reason text.
    Without this, one hook decision could inject fabricated extra "log lines" (log forgery/
    injection) into what's meant to be an append-only, one-call-one-line audit trail. Replacing
    \\r and \\n with a space and truncating keeps the invariant "one _log() call == exactly one
    physical line" true regardless of what's embedded in the input.
    """
    if not isinstance(s, str):
        s = str(s)
    return s.replace("\r", " ").replace("\n", " ")[:500]


# --- retry dedup (shadow-statistics hygiene, not a decision change) ----------------------------
# A WOULD-BLOCK doesn't stop anything, so the same commit is routinely re-attempted: the observed
# log carried 63 same-session repeat-blocks under 60s apart, each double-logged AND double-ghosted
# (two identical systemMessages for one commit intent). Tagging the repeat `retry=1` keeps the
# shadow statistics honest -- retries stay countable but no longer inflate the block count or the
# user-visible ghost line. STRICTLY additive to the line grammar: `retry=1` rides at END of line,
# after `session=`, so every existing `rc=`/`key=` grepper keeps matching unchanged.
#
# MECHANISM: a per-session marker file holding `<staged-set-hash> <unix-epoch>`, deliberately NOT a
# re-read of the shadow log's own tail. Two reasons, both load-bearing:
#   - the key is a hash of the FULL sorted staged path list, whereas the log's `uncovered=` field
#     caps at 5 paths -- two >5-file staged sets differing only PAST that cap are identical in the
#     log, so a log-derived key would collide and mis-tag a genuine new observation as a retry;
#   - it is decoupled from the log-line grammar, so the log stays a pure append-only audit trail
#     rather than a parsed data source no future field rename may disturb.
# The marker is keyed by session_id, so a concurrent session sharing this repo never reads another
# session's state -- the same multi-session correctness the module docstring demands.
_RETRY_WINDOW_SECONDS = 600
_RETRY_MARKER_MAX_AGE_SECONDS = 2 * 86400


def _retry_marker_dir():
    return _claude_home() / ".cache" / "ballast-gate"


def _retry_marker_path(session_id):
    # session_id is harness-supplied, but it lands in a FILENAME -- sanitize so a surprising value
    # (separators, traversal) can never write outside the cache dir.
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")[:120]
    return _retry_marker_dir() / ("retry-%s" % sid)


def _staged_key(staged_paths):
    """sha1 over the FULL sorted staged path list (see the section comment: the log's capped
    `uncovered=` field is not a usable key)."""
    joined = "\n".join(sorted(staged_paths))
    return hashlib.sha1(joined.encode("utf-8", errors="replace")).hexdigest()


def _prune_retry_markers():
    """Best-effort reap of retry-* markers older than 2 days, so dead sessions don't accrue.
    Never raises -- marker hygiene must not affect the hook's outcome."""
    try:
        cutoff = time.time() - _RETRY_MARKER_MAX_AGE_SECONDS
        for f in _retry_marker_dir().glob("retry-*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _is_retry(staged_paths, session_id):
    """True if this session already recorded a WOULD-BLOCK for the IDENTICAL staged set within
    _RETRY_WINDOW_SECONDS. Fails OPEN (False -> log + ghost as usual) on ANY error or absence:
    a dedup bug must never suppress a genuine first-time observation."""
    if not staged_paths:
        return False  # no stable staged set (git-unavailable, internal-error) -- never dedup it
    try:
        stored_key, stored_epoch = _retry_marker_path(session_id).read_text(
            encoding="utf-8").split()[:2]
        if stored_key != _staged_key(staged_paths):
            return False
        return 0 <= (time.time() - int(stored_epoch)) < _RETRY_WINDOW_SECONDS
    except Exception:
        return False


def _write_retry_marker(staged_paths, session_id):
    """(Re)write this session's marker with the current staged-set hash + epoch. Best-effort: a
    write failure only means the NEXT retry logs un-tagged, never a changed decision."""
    if not staged_paths:
        return
    try:
        _retry_marker_dir().mkdir(parents=True, exist_ok=True)
        _retry_marker_path(session_id).write_text(
            "%s %d" % (_staged_key(staged_paths), int(time.time())), encoding="utf-8")
    except Exception:
        pass
    _prune_retry_markers()


def _log(decision, reason, session_id, suffix=""):
    """Append one line to the shadow log. Never raises -- a logging failure must not change the
    (already-decided, always exit-0-in-shadow) outcome of this hook. `suffix` rides at END of line
    (currently only " retry=1"), leaving the historical field order byte-identical before it."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    reason = _sanitize_log_field(reason)
    session_id = _sanitize_log_field(session_id) if session_id else session_id
    line = "%s %s %s session=%s%s\n" % (ts, decision, reason, session_id or "nosession", suffix)
    try:
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def main():
    # Kill switch FIRST -- before stdin, before any git/transcript work, no log write either way.
    try:
        if _kill_switch_path().exists():
            return 0
    except Exception:
        pass  # a broken kill-switch check must not itself block the gate from running normally

    # (F4) The module docstring's FAIL-OPEN CONTRACT claims "NOTHING here can propagate an
    # exception out of main()". Before this wrap that was true for stdin parsing and for
    # _evaluate()'s own body (each had its own try/except), but every line of glue BETWEEN them --
    # payload-shape checks, _is_standalone_commit, cwd/transcript_path plumbing, and the final
    # _log() call itself -- had no enclosing handler, so a genuinely unanticipated exception there
    # (e.g. a future refactor slipping in a bug) would still crash main() and violate the
    # documented invariant. Wrapping the entire post-kill-switch body closes that gap; the
    # granular handling inside (stdin parsing, _evaluate's _TranscriptUnreadable/Exception split)
    # is unchanged and still runs first -- this outer wrap is a last-resort backstop, not a
    # replacement for it.
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0  # can't read our own input -- fail open, nothing coherent to log against
        if not isinstance(payload, dict):
            return 0

        session_id = payload.get("session_id") or ""
        tool_name = payload.get("tool_name") or ""
        if tool_name not in ("Bash", "PowerShell"):
            return 0  # the hooks.json matcher already scopes this; stay defensive if invoked oddly

        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return 0
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return 0

        if not _is_standalone_commit(command):
            return 0  # not a qualifying commit -- nothing evaluated, nothing logged (see docstring)

        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            cwd = os.getcwd()
        transcript_path = payload.get("transcript_path") or ""

        staged_paths = None
        try:
            decision, reason, staged_paths = _evaluate(command, cwd, transcript_path, session_id)
        except _TranscriptUnreadable:
            decision, reason = "WOULD-BLOCK", "transcript-unreadable"
        except Exception as e:
            # Anything else unexpected (a git plumbing surprise, a transcript shape this hook's own
            # walk didn't anticipate, ...): log conservatively as WOULD-BLOCK -- the fail-CLOSED-on-
            # uncertainty posture the design spec wants on the eventual enforce-mode block path -- so
            # the shadow log still surfaces the gap for review, instead of silently vanishing.
            decision, reason = "WOULD-BLOCK", "internal-error:%s" % type(e).__name__

        retry = False
        if decision == "WOULD-BLOCK":
            # Read BEFORE the rewrite (the marker is the previous attempt's record), then refresh
            # it in EVERY WOULD-BLOCK case so a run of retries keeps sliding the window forward.
            retry = _is_retry(staged_paths, session_id)
            _write_retry_marker(staged_paths, session_id)
        _log(decision, reason, session_id, " retry=1" if retry else "")
        if decision == "WOULD-BLOCK" and not retry:
            # User-visible fire indicator, WOULD-BLOCK only -- an ALLOW is the common case (every
            # reviewed or trivial commit) and printing on every single qualifying commit would be
            # noise; WOULD-BLOCK is the rare, worth-surfacing signal that the eventual enforce-mode
            # flip would have denied this commit. Suppressed on a `retry=1` line (see _is_retry):
            # the same commit re-attempted is one intent, so it gets one ghost. Still shadow:
            # prints, but does not change the exit code below.
            print(json.dumps({
                "systemMessage": "👻 ballast: commit-review-gate (shadow) — WOULD have blocked this commit (logged)"
            }))
        return 0  # SHADOW MODE: always exit 0, regardless of `decision`. Do not change this
                  # without re-reading the rollout section of the design doc first.
    except Exception:
        # Last-resort backstop (F4): whatever this was, it's unanticipated by the handling above.
        # Fail open per the module's FAIL-OPEN CONTRACT -- never let main() itself raise.
        return 0


if __name__ == "__main__":
    sys.exit(main())
