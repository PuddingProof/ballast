#!/usr/bin/env python3
"""mode-state.py -- ballast's session-mode registry CLI.

WHY THIS EXISTS: the statusLine renderer (statusline/render.py) draws a
per-session "chip" (e.g. a solid ✈️ freehand / 📋 plan-handoff indicator) so a
keyword-armed mode or an in-progress plan-handoff exec phase stays glanceable
in the bottom strip instead of scrolling away in a systemMessage. Hooks and
the model itself write to a small per-session state file through this CLI;
render.py only ever *reads* that file. This script owns all writes so the
on-disk format and its atomicity guarantee live in exactly one place.

SECURITY RATIONALE -- validation is a security boundary, not a nicety: both
`mode` and `--session <sid>` are interpolated directly into a filesystem path
under `~/.claude/ballast/modes/` (the session id becomes the filename). This
CLI is *also* on ballast-allow.py's permission self-allow list for the bare
`ballast-mode ...` form, meaning a hook can invoke it with zero human
confirmation. If either argument were allowed to contain path-traversal
characters (forward slash, backslash, `..`) a crafted session id could escape the modes/
directory and write or delete arbitrary files. Both arguments are therefore
validated against a strict fullmatch allowlist *before* any filesystem
operation (including directory creation) is attempted; anything that doesn't
match is rejected with a short stderr line and a non-zero exit, and nothing
is touched on disk.

ATOMICITY CHOICE: render.py may read a session's state file at any moment
(the statusLine hook re-runs after every assistant message, on a debounce).
A writer that truncated-then-wrote in place could hand the renderer a
half-written line mid-repaint. Every write here goes to a fresh temp file in
the same directory (so it's on the same filesystem/volume) and is published
via `os.replace`, which is atomic on POSIX and Windows alike -- the renderer
therefore only ever observes the complete prior file or the complete new
one, never a torn state.

CALLING CONVENTION: hook callers invoke this fail-quiet (`... || true`) --
this CLI's own contract is fail-open in the sense that a malformed call must
never be allowed to block a session, but that suppression happens at the
call site, not in here. This script itself does the opposite: it fails
LOUD (short stderr message, exit 1) on any validation or usage error, because
the model also calls it directly (via bin/ballast-mode) as an ordinary
command, where a clear, visible error is what lets the model self-correct.

STATE FILE FORMAT: `<state-dir>/<sid>`, one line per mode:
    <mode> <status> <epoch>
space-separated, `status` is `pending` or `confirmed`, `epoch` is an int
(seconds since epoch, UTC, from time.time()). A line that doesn't parse
cleanly is dropped silently on read (never crashes a subsequent write) --
the state file is disposable, best-effort, and renderer-only.
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

# Mode names are short, human-authored labels ("freehand", "autopilot",
# "exec", ...); keep them to a conservative filename- and shell-safe charset.
MODE_RE = re.compile(r"[a-z0-9-]{1,32}")

# Session ids are UUIDs (hex digits + hyphens). This charset excludes every
# path-traversal character (`/`, `\`, `.`) by construction -- see the
# SECURITY RATIONALE above. Do not widen this without re-reading that note.
SID_RE = re.compile(r"[0-9a-fA-F-]{8,64}")

STALE_SECONDS = 7 * 24 * 3600  # cleanup GC horizon: 7 days


def fail(message):
    """Print a short, model-visible error and exit 1. Never touches disk."""
    sys.stderr.write("mode-state: {}\n".format(message))
    sys.exit(1)


def validate_mode(mode):
    if not MODE_RE.fullmatch(mode):
        fail("invalid mode {!r} (expected [a-z0-9-]{{1,32}})".format(mode))
    return mode


def validate_sid(sid):
    if not SID_RE.fullmatch(sid):
        fail("invalid session id (expected [0-9a-fA-F-]{{8,64}})")
    return sid


# =================================================================================================
# Paths -- resolved under _claude_home() so tests can redirect every side effect (mirrors the
# BALLAST_CLAUDE_HOME convention used by hooks/commit-review-gate.py and hooks/run.sh).
# =================================================================================================

def _claude_home():
    """Base `~/.claude` dir. Override via BALLAST_CLAUDE_HOME (test-only -- hooks.json and
    bin/ballast-mode never set this, so production always resolves the real `~/.claude`."""
    override = os.environ.get("BALLAST_CLAUDE_HOME")
    return Path(override) if override else (Path.home() / ".claude")


def _state_dir():
    return _claude_home() / "ballast" / "modes"


def _state_path(sid):
    return _state_dir() / sid


# =================================================================================================
# State file read/write
# =================================================================================================

def read_state(path):
    """Return {mode: (status, epoch)}. Missing file -> {}. Malformed lines are dropped
    silently -- a corrupt/garbage line must never crash a subsequent read or write."""
    entries = {}
    if not path.exists():
        return entries
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        mode, status, epoch = parts
        if status not in ("pending", "confirmed"):
            continue
        if not epoch.isdigit():
            continue
        if not MODE_RE.fullmatch(mode):
            continue
        entries[mode] = (status, int(epoch))
    return entries


def write_state(path, entries):
    """Atomic write: tmp file in the same directory, then os.replace onto the target.
    See the ATOMICITY CHOICE note in the module header for why this matters."""
    lines = ["{} {} {}".format(mode, status, epoch) for mode, (status, epoch) in entries.items()]
    content = "\n".join(lines)
    if content:
        content += "\n"
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(d), prefix=".mode-state-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# =================================================================================================
# Subcommand implementations
# =================================================================================================

def do_raise(mode, sid, status):
    path = _state_path(sid)
    entries = read_state(path)
    # A pending raise must never DEMOTE an already-confirmed mode: a caller that re-raises a mode
    # as pending while a genuine grant already stands confirmed must not visibly downgrade a
    # settled chip. The re-arm case still refreshes the epoch, so the TTL clock tracks activity.
    if status == "pending" and mode in entries and entries[mode][0] == "confirmed":
        status = "confirmed"
    entries[mode] = (status, int(time.time()))
    write_state(path, entries)


def do_confirm(mode, sid):
    # "pending -> confirmed; raises confirmed if missing" -- either way the end state is the
    # same (mode present, status confirmed), so this collapses to an unconditional set.
    path = _state_path(sid)
    entries = read_state(path)
    entries[mode] = ("confirmed", int(time.time()))
    write_state(path, entries)


def do_clear(mode, sid, pending_only=False):
    path = _state_path(sid)
    entries = read_state(path)
    if mode in entries:
        # --pending-only is a guarded clear: it removes the entry ONLY if it is still pending, and
        # leaves a CONFIRMED grant untouched -- lets a caller retract its own pending chip without
        # risking a silent revoke of a standing confirmed grant under the same mode. A bare clear
        # (pending_only=False) still deletes either status.
        if pending_only and entries[mode][0] != "pending":
            return  # confirmed grant left standing; exit 0 (idempotent from the caller's view).
        del entries[mode]
        write_state(path, entries)
    # mode not present: idempotent no-op, not an error.


def do_clear_all(sid):
    path = _state_path(sid)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def do_cleanup(sid):
    # Delete this session's own state file...
    path = _state_path(sid)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    # ...then GC any sibling session files that have gone stale (session ended without a
    # clean SessionEnd hook fire, crash, etc.) so ballast/modes/ doesn't grow unbounded.
    _gc_stale(_state_dir())


def _gc_stale(state_dir):
    if not state_dir.exists():
        return
    cutoff = time.time() - STALE_SECONDS
    try:
        entries = list(state_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue  # best-effort GC; a single unremovable file must not abort the sweep


# =================================================================================================
# Argument parsing -- deliberately hand-rolled (not argparse): usage/validation errors must be a
# single short stderr line + exit 1, which argparse's default error() (usage block + exit 2) does
# not give us for free, and the grammar here is small enough that hand-parsing stays clearer.
# =================================================================================================

def _take_option(tokens, name):
    """Remove and return the value following `name` if present (mutates tokens); None if absent."""
    if name in tokens:
        i = tokens.index(name)
        if i + 1 >= len(tokens):
            fail("{} requires a value".format(name))
        value = tokens[i + 1]
        del tokens[i:i + 2]
        return value
    return None


def _take_flag(tokens, name):
    if name in tokens:
        tokens.remove(name)
        return True
    return False


def _cmd_raise(tokens):
    session = _take_option(tokens, "--session")
    pending = _take_flag(tokens, "--pending")
    confirmed = _take_flag(tokens, "--confirmed")
    if pending and confirmed:
        fail("--pending and --confirmed are mutually exclusive")
    if session is None:
        fail("raise: --session <sid> is required")
    if len(tokens) != 1:
        fail("usage: raise <mode> --session <sid> [--pending|--confirmed]")
    mode = validate_mode(tokens[0])
    sid = validate_sid(session)
    do_raise(mode, sid, "confirmed" if confirmed else "pending")


def _cmd_confirm(tokens):
    session = _take_option(tokens, "--session")
    if session is None:
        fail("confirm: --session <sid> is required")
    if len(tokens) != 1:
        fail("usage: confirm <mode> --session <sid>")
    mode = validate_mode(tokens[0])
    sid = validate_sid(session)
    do_confirm(mode, sid)


def _cmd_clear(tokens):
    session = _take_option(tokens, "--session")
    all_flag = _take_flag(tokens, "--all")
    pending_only = _take_flag(tokens, "--pending-only")
    if session is None:
        fail("clear: --session <sid> is required")
    sid = validate_sid(session)
    if all_flag:
        # --pending-only guards a single mode entry's status; it is meaningless against --all
        # (which unlinks the whole session file regardless of status). Reject the combo explicitly
        # rather than silently ignore it -- same fail-loud validation posture as raise's
        # --pending/--confirmed mutual exclusion above.
        if pending_only:
            fail("--pending-only cannot be combined with --all")
        if len(tokens) != 0:
            fail("usage: clear --all --session <sid> (no mode argument with --all)")
        do_clear_all(sid)
    else:
        if len(tokens) != 1:
            fail("usage: clear <mode> --session <sid> [--pending-only]  |  clear --all --session <sid>")
        mode = validate_mode(tokens[0])
        do_clear(mode, sid, pending_only)


def _cmd_cleanup(tokens):
    session = _take_option(tokens, "--session")
    if session is None:
        fail("cleanup: --session <sid> is required")
    if len(tokens) != 0:
        fail("usage: cleanup --session <sid>")
    sid = validate_sid(session)
    do_cleanup(sid)


DISPATCH = {
    "raise": _cmd_raise,
    "confirm": _cmd_confirm,
    "clear": _cmd_clear,
    "cleanup": _cmd_cleanup,
}


def main():
    argv = sys.argv[1:]
    if not argv:
        fail("usage: mode-state.py <raise|confirm|clear|cleanup> ...")
    cmd, rest = argv[0], argv[1:]
    handler = DISPATCH.get(cmd)
    if handler is None:
        fail("unknown subcommand {!r} (expected raise|confirm|clear|cleanup)".format(cmd))
    handler(list(rest))


if __name__ == "__main__":
    main()
