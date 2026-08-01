#!/usr/bin/env python3
"""statusline/render.py -- renders the ballast statusLine row (the settings.json `statusLine`
command target, invoked indirectly through render.sh).

CONTRACT (spec: .notes/archive/2026-07-09-statusline-mode-indicator-spec.md +
.notes/archive/2026-07-11-statusline-mode-indicator-plan.md):
  - Reads ONE JSON object on stdin: Claude Code's statusLine payload. Fields used:
    `session_id`, `workspace.project_dir` (fallback: top-level `cwd` field, then empty),
    `model.display_name` + `effort.level` (session identity), and
    `context_window.used_percentage` (the value shown) plus `.total_input_tokens` (its
    color -- see CTX_TOKEN_THRESHOLDS).
  - Reads ballast mode-registry state from `<state-dir>/<session_id>`, where
    state-dir = $BALLAST_CLAUDE_HOME (test-only override) or ~/.claude, plus `ballast/modes/`.
    That file is OWNED by a sibling component (mode-state.py) -- this module only ever
    reads/parses it, NEVER writes it. Format: one `<mode> <status> <epoch>` line per mode;
    malformed lines are skipped silently (a bad line is never a reason to blank the row).
  - Writes exactly ONE line to stdout: the ` · `-joined baseline segments (dir, model +
    effort, ctx%) + ` ▸ ` + chips (if any, TTL-filtered). Every segment is independently
    omittable; absent ones simply don't join.

FAIL-QUIET IS THE SPEC, NOT A NICETY: this renders directly into the terminal status bar on
every statusLine refresh (per assistant message / permission-mode change / compact, debounced
300ms). An empty stdout line is invisible -- the reserved row just stays blank -- but a
traceback or a stray stderr line becomes a permanent, glaring UI regression that reappears on
every single refresh until someone notices and reverts the plugin. So: unparseable/empty
stdin -> print nothing, exit 0; missing/corrupt state file -> baseline only (a bad chip file
must never take down the baseline too); the ENTIRE body of main() runs under one broad
except -- any exception at all means "print nothing, exit 0", never a stderr dump into the
status bar. Python 3 stdlib only (no third-party import to ever fail).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

# -------------------------------------------------------------------------------------------
# ANSI 24-bit ("truecolor") SGR building blocks. Kept as tiny composable pieces rather than
# baked into each string literal so the style map below stays the single source of color/emoji
# truth (see MODE_STYLES).
# -------------------------------------------------------------------------------------------
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"


def _fg(rgb):
    r, g, b = rgb
    return "\x1b[38;2;%d;%d;%dm" % (r, g, b)


GRAY = (135, 135, 135)     # baseline dim gray (dir, model+effort, low-context ctx token)
RED = (255, 95, 95)        # ctx token color at the top threshold

# The ctx token's color tracks ABSOLUTE TOKENS, not the percentage it displays. The quantity
# worth reacting to is a token count -- Anthropic's long-context pricing tier starts above 200k
# input tokens -- and that cliff sits at a different percentage on every window size (20% of a
# 1M window, 100% of a 200k one), so a percentage threshold would silently change meaning with
# the model. The steps interpolate GRAY -> RED, deliberately skipping amber: the row sits
# directly above the native permission-mode line, whose auto mode is gold.
# Ordered high -> low; _ctx_color takes the first match.
CTX_TOKEN_THRESHOLDS = (
    (800_000, RED),
    (500_000, (202, 113, 113)),
    (200_000, (162, 126, 126)),   # long-context pricing tier begins here
)
UNKNOWN_GRAY = (200, 200, 200)  # neutral fallback color for a mode not in MODE_STYLES

# Defensive width cap on the model's display name (see _extract_model). Observed values are far
# under this -- "Opus 5" is 6 chars, the native contract's "Claude 3.5 Sonnet" example is 17 --
# but no registry of possible names exists, so this bounds an unknown rather than a known.
MODEL_NAME_MAX = 18

# Staleness hygiene (renderer-side only -- mode-state.py never deletes on its own clock;
# GC of old session files is that component's `cleanup` subcommand, a separate concern from
# "should THIS refresh still show a chip that's gotten old").
PENDING_TTL_SECONDS = 45 * 60
CONFIRMED_TTL_SECONDS = 24 * 60 * 60

# CHIP PALETTE. Two constraints bound every pick here, and neither is visible from this file
# alone -- record the reasoning rather than re-deriving it each time a chip is added:
#   1. Don't collide with the NATIVE permission-mode colors rendered directly beneath this row
#      (auto mode gold, plan mode blue, accept-edits purple). A chip in a near-native hue reads
#      as a native indicator that has somehow escaped its line. This is what ruled out the
#      obvious violet rgb(175,135,255) -- native `effortUltra` kin, but too close to the
#      accept-edits purple sitting one row down.
#   2. Stay bright enough to survive SGR 2. Pending chips render dimmed (see _render_chip), so a
#      dark or low-luminance color that looks fine solid can go nearly invisible pending.
# The stdin payload carries no terminal-theme signal (checked against the native statusLine
# contract), so these are hardcoded for a dark background by assumption -- there is no way to
# adapt at render time.
# Unclaimed and vetted against both constraints, for whatever chip comes next:
# green rgb(95,215,135) and coral rgb(255,120,120).
CYAN = (95, 215, 255)      # autonomy chip -- cool counterpart to the warm exec orange
MAGENTA = (255, 135, 215)  # critical-analysis -- max hue separation from both chips above

# The autonomy chip, defined ONCE and bound to both of its registry keys below. Escaped rather
# than written as a literal glyph, matching the ANSI constants above -- nothing in this file then
# depends on the host interpreter having read the source as UTF-8.
_AUTONOMY_CHIP = ("\u2708\uFE0F", "freehand", CYAN)

# Mode -> (emoji, label, rgb). This is the ENTIRE per-mode presentation surface: a future
# writer (postmortem-pending, etc.) needs exactly one new dict entry, no other code change --
# see _render_chip's fallback branch below for why an entry not in this map still renders
# instead of being dropped (the registry stays general on purpose).
#
# LABEL IS NOT THE MODE KEY. The label names the mode as the USER knows it, not the internal
# state key: `exec` displays as "plan-handoff" (the protocol they invoked), and the two autonomy
# keys share one label (below). Both the pending and the confirmed render go through the label
# (see _render_chip), so a chip's two forms can never name different things.
MODE_STYLES = {
    # Two keys, ONE style object: `freehand` and `autopilot` are distinct STATE keys (the hook
    # records which keyword armed the grant, and its mere-mention clear instruction names that
    # same key back) but they are one and the same MODE -- sharing the tuple makes it impossible
    # for the two spellings to drift visually apart.
    "freehand": _AUTONOMY_CHIP,
    "autopilot": _AUTONOMY_CHIP,
    "critical-analysis": ("\U0001F9E0", "critical-analysis", MAGENTA),
    # Transient phases last: DECLARATION ORDER IS DISPLAY ORDER (see _chip_sort_key), so putting
    # the short-lived phase chips after the standing stances keeps churn at the row's right edge
    # and leaves the stance chips at fixed positions. Reordering the row = reordering these lines.
    "exec": ("\U0001F4CB", "plan-handoff", (255, 150, 50)),
}

# Declaration rank, derived once at import. Dicts preserve insertion order, so the literal above
# is the single source of both styling AND layout -- no second list to keep in sync.
_MODE_ORDER = {mode: i for i, mode in enumerate(MODE_STYLES)}


def _claude_home():
    """Base `~/.claude` dir. Override via BALLAST_CLAUDE_HOME (test-only -- render.sh's real
    invocation never sets this, so production always resolves the genuine ~/.claude; mirrors
    the same convention used by commit-review-gate.py / run.sh / bin/ballast-extract)."""
    override = os.environ.get("BALLAST_CLAUDE_HOME")
    return Path(override) if override else (Path.home() / ".claude")


def _state_dir():
    return _claude_home() / "ballast" / "modes"


def _basename(path):
    """basename that understands BOTH separators regardless of host OS.

    `os.path.basename` only understands the host's native separator (ntpath vs posixpath),
    but `workspace.project_dir` reflects the machine Claude Code itself is running on, which
    need not match the separator convention of the Python interpreter render.sh happened to
    resolve (e.g. Git Bash's python on Windows can still see backslash-free forward-slash
    paths, or vice versa) -- so split on either separator explicitly instead of trusting
    os.path.
    """
    s = str(path).rstrip("/\\")
    if not s:
        return ""
    return re.split(r"[\\/]+", s)[-1]


def _load_chips(session_id):
    """Read and TTL-filter `<state-dir>/<session_id>`. Returns a list of (mode, status)
    tuples in file order; render() then sorts them into display order (see _chip_sort_key).

    Missing file, unreadable file, or no session_id at all -> empty list (never an exception,
    per the fail-quiet contract; a state-file problem must degrade to "no chips", not to
    "no output at all" -- the baseline still has to render).
    """
    if not session_id:
        return []
    path = _state_dir() / session_id
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        # Missing file, permission error, or invalid-encoding bytes (a genuinely CORRUPT
        # file, not just a malformed line) -- any of these degrades to "no chips", never to
        # an exception that would blank the whole row. Broad on purpose: OSError covers
        # missing/permission, but a bad UTF-8 byte raises UnicodeDecodeError (a ValueError
        # subclass, not an OSError), and this contract cares about "baseline still renders"
        # more than about narrowing the exception type.
        return []

    now = time.time()
    chips = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            continue  # malformed -- skip silently, per contract
        mode, status, epoch_str = parts
        if status not in ("pending", "confirmed"):
            continue
        try:
            epoch = int(epoch_str)
        except ValueError:
            continue
        age = now - epoch
        if status == "pending" and age > PENDING_TTL_SECONDS:
            continue
        if status == "confirmed" and age > CONFIRMED_TTL_SECONDS:
            continue
        chips.append((mode, status))
    return chips


def _chip_style(mode):
    """(emoji, label, rgb) for a mode -- registered style, else the generic fallback.

    Unregistered mode: generic bullet, and the raw mode name AS the label -- lowercased to match
    the registered chips' casing so a new writer that forgets its MODE_STYLES entry still renders
    in-house rather than shouting in a style nothing else uses.
    """
    return MODE_STYLES.get(mode) or ("●", mode.lower(), UNKNOWN_GRAY)


def _render_chip(mode, status):
    emoji, label, rgb = _chip_style(mode)

    if status == "confirmed":
        # Solid: emoji + bold colored label. A style may register an empty emoji (glyph-less
        # chip); joining on the space rather than hardcoding it keeps that from emitting a
        # stray leading space that reads as a rendering glitch.
        prefix = "%s " % emoji if emoji else ""
        return "%s%s%s%s%s" % (prefix, BOLD, _fg(rgb), label, RESET)
    # Pending: dim -- no emoji, just "<label>?" in a dimmed version of the mode's color (SGR 2
    # dim + the 24-bit fg), per spec. Uses the LABEL, not the mode key, so the pending and
    # confirmed forms of a chip always name the same thing (a pending `autopilot` reads
    # "freehand?", matching the "freehand" it settles into).
    return "%s%s⋯ %s?%s" % (DIM, _fg(rgb), label, RESET)


def _ctx_color(tokens):
    """Color for the ctx token, keyed on absolute tokens (see CTX_TOKEN_THRESHOLDS).

    An unknown token count colors as GRAY rather than guessing a warmer step: the row is glanced
    at, not read, so a wrong warning is worse than a missing one.
    """
    if tokens is None:
        return GRAY
    for floor, rgb in CTX_TOKEN_THRESHOLDS:
        if tokens >= floor:
            return rgb
    return GRAY


def _extract_project_dir(payload):
    workspace = payload.get("workspace")
    project_dir = ""
    if isinstance(workspace, dict):
        project_dir = workspace.get("project_dir") or ""
    if not project_dir:
        project_dir = payload.get("cwd") or ""
    return project_dir


def _number(value):
    """value if it is a real number, else None. `bool` is an int subclass in Python, so it is
    excluded explicitly -- a stray `true` in the payload must not read as 1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _extract_pct(payload):
    cw = payload.get("context_window")
    if not isinstance(cw, dict):
        return None
    used = _number(cw.get("used_percentage"))
    return None if used is None else int(round(used))


def _extract_tokens(payload):
    """Tokens currently occupying the context window, or None.

    Prefers the reported count. Falls back to reconstructing it from the percentage and the
    window size so the color still tracks the right quantity on a payload that omits the raw
    count -- the displayed percentage and the color would otherwise disagree about the same
    context.
    """
    cw = payload.get("context_window")
    if not isinstance(cw, dict):
        return None
    used = _number(cw.get("total_input_tokens"))
    if used is not None:
        return int(used)
    pct = _number(cw.get("used_percentage"))
    size = _number(cw.get("context_window_size"))
    if pct is None or size is None:
        return None
    return int(round(size * pct / 100.0))


def _extract_model(payload):
    """`<display name> · <effort>` for the session, or "" when the payload carries neither.

    display_name is used VERBATIM -- no prefix-stripping, no space removal. Its shape is not
    stable across model generations (the native contract's own example, "Claude 3.5 Sonnet",
    puts the vendor first and the version mid-string, where today's "Opus 5" has no vendor and
    a trailing version), so any transform tuned to one shape quietly mangles another. An
    over-long name is TRUNCATED instead: truncation degrades predictably, a regex does not.

    `effort` is documented as present only on models that support reasoning effort, so the
    segment degrades to the name alone rather than printing a placeholder for a real absence.
    """
    model = payload.get("model")
    name = model.get("display_name") if isinstance(model, dict) else None
    name = name.strip() if isinstance(name, str) else ""
    if len(name) > MODEL_NAME_MAX:
        name = name[:MODEL_NAME_MAX - 1].rstrip() + "…"

    effort = payload.get("effort")
    level = effort.get("level") if isinstance(effort, dict) else None
    level = level.strip() if isinstance(level, str) else ""

    # Parenthesize the effort onto the name rather than joining with the baseline's ` · `
    # separator: effort is an attribute OF the model, not a peer segment beside it, and reusing
    # the segment separator made it read as one.
    if name and level:
        return "%s (%s)" % (name, level)
    return name or level


def _render_baseline(payload):
    # Displayed value stays the percentage; only its COLOR keys on the absolute token count.
    color = _ctx_color(_extract_tokens(payload))
    pct = _extract_pct(payload)

    # Built as a list rather than a branch cascade: dir / model+effort / ctx% are each
    # independently absent-able, so an if-chain needs 2^3 cases to stay correct while the only
    # thing that actually differs between them is where the separators land. Appending in
    # display order and joining once makes a new segment a one-line change.
    segments = []
    dirname = _basename(_extract_project_dir(payload))
    if dirname:
        segments.append("%s%s%s" % (_fg(GRAY), dirname, RESET))
    model = _extract_model(payload)
    if model:
        segments.append("%s%s%s" % (_fg(GRAY), model, RESET))
    if pct is not None:
        segments.append("%s%d%% context%s" % (_fg(color), pct, RESET))
    return " · ".join(segments)


def _chip_sort_key(entry):
    """Registered modes sort by MODE_STYLES declaration order; unregistered ones trail them.

    State-file order is arrival order, which makes a chip's position depend on session history:
    the same two modes land in different slots depending on which was raised first, and a
    clear-then-re-raise silently moves a chip to the far end. Sorting by declaration order makes
    the layout identical in every session, so a chip's position is itself recognizable. Unknown
    modes share the trailing rank and Python's stable sort keeps them in file order among
    themselves.
    """
    mode, _status = entry
    return _MODE_ORDER.get(mode, len(_MODE_ORDER))


def render(payload):
    session_id = payload.get("session_id") or ""
    chip_entries = sorted(_load_chips(session_id), key=_chip_sort_key)
    baseline = _render_baseline(payload)

    # Chips sit to the RIGHT of the baseline, past a ` ▸ ` boundary marker. The baseline is the
    # always-present part of the row, so anchoring it at a fixed left edge keeps the dir and ctx%
    # from shifting sideways every time a chip is raised or settled; the chips then grow rightward
    # into empty space. The ▸ points at them, reading as "…and these modes are live".
    #
    # One chip per LABEL, not per mode key. `freehand` and `autopilot` are distinct keys sharing
    # a label, so a session that armed both (two keywords, two raises, neither cleared) would
    # otherwise render the same chip twice. The sort decides the surviving chip's POSITION (first
    # occurrence in declaration order); CONFIRMED decides its STATUS -- see below.
    label_order = []
    by_label = {}
    for mode, status in chip_entries:
        label = _chip_style(mode)[1]
        if label not in by_label:
            label_order.append(label)
            by_label[label] = (mode, status)
        elif status == "confirmed" and by_label[label][1] != "confirmed":
            # CONFIRMED OUTRANKS PENDING within a label. The two keys can legitimately hold
            # DIFFERENT statuses at once (`autopilot confirmed` + `freehand pending`) if a caller
            # raises one key pending while the other is already confirmed. Taking the first entry
            # regardless of status would dim a standing grant down to "⋯ freehand?" until the
            # pending TTL reaped it -- exactly the demote mode-state.py's do_raise already refuses
            # to perform within a single key.
            by_label[label] = (mode, status)
    chip_strs = [_render_chip(*by_label[label]) for label in label_order]
    if not chip_strs:
        return baseline
    chips = " · ".join(chip_strs)
    if not baseline:
        # Degenerate payload (no project dir AND no context %) -- emit the chips alone rather
        # than a dangling ` ▸ ` separating them from nothing.
        return chips
    return baseline + " ▸ " + chips


def main():
    try:
        # Read/write raw UTF-8 bytes directly through the .buffer handles rather than
        # trusting sys.stdin/sys.stdout's TEXT encoding. On Windows, a redirected
        # stdin/stdout defaults to the active console codepage (e.g. cp1252), NOT UTF-8,
        # unless PYTHONUTF8/PYTHONIOENCODING happen to be set -- and this row is full of
        # multi-byte characters (emoji, middle dot, the ' ▸ ' triangle). Decoding/encoding
        # explicitly as UTF-8 here makes the output correct regardless of the caller's
        # locale, console codepage, or Python version defaults.
        raw_bytes = sys.stdin.buffer.read()
        raw = raw_bytes.decode("utf-8")
        if not raw or not raw.strip():
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return
        line = render(payload)
        if not line:
            return
        # Belt-and-suspenders: guarantee a single output line even if a crafted/odd input
        # field smuggled in a literal newline -- the statusLine contract is strictly "first
        # stdout line is the whole row", so any embedded newline must never reach stdout.
        line = line.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        # Fail-quiet: any exception anywhere above means print nothing, exit 0. Never let a
        # traceback land in the status bar.
        return


if __name__ == "__main__":
    main()
