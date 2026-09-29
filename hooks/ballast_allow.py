"""ballast_allow.py -- shell-guards module (Bash|PowerShell PreToolUse), self-scoped permission allow.

A plugin cannot ship permission-settings entries, so every invocation of the plugin's own
`ballast-extract`, `ballast-mode`, and `ballast-sweep` shims would otherwise sit behind a manual
permission prompt forever. This guard is the narrow substitute: it recognizes exactly those three
bare, unmodified invocation shapes and answers `allow` for THAT call only. Anything else --
including any of the three names wrapped in something that could smuggle a second command
alongside it -- gets no decision. Run by shell-guards.py, where any deny or ask from another guard
outranks this allow.

OPT-IN GATE (invariant): the guard ships INERT and decides NOTHING for ANY input unless the user
has deliberately created `<home_root>/ballast/allow-standing-grants` -- a plugin does not
self-grant a standing permission on the user's behalf. The marker check runs first in decide(),
before any payload field is read, and fails CLOSED on any error: the inverse of the usual
fail-open direction, because here the risky output IS the allow decision.

TRUST BOUNDARY. This guard only ever keeps a SECOND command from riding along; what each shim may
do is bounded by that script's OWN contract, not by anything here:
  - `ballast-extract` (session-postmortem/scripts/extract.py): read-only except `digest`, which
    writes only the fixed filenames `digest.md` + `manifest.json`, and only into a dir it created
    or already owns (`--out` selects a directory, never a filename). No network, no git.
  - `ballast-mode` (statusline/mode-state.py): validates `mode` (`[a-z0-9-]{1,32}`) and
    `--session <sid>` (`[0-9a-fA-F-]{8,64}`) against strict fullmatch allowlists before any
    filesystem op -- neither charset can contain `/`, `\\`, or `..` -- and every write lands under
    `~/.claude/ballast/modes/`. Worst case is a wrong status-line chip, not an arbitrary write.
  - `ballast-sweep` (skills/harness-sweep/scripts/sweep.py): writes the fixed `bundle.md` +
    `manifest.json` under a dir it derives itself, plus `advance`'s rewrite of the
    `<home>/postmortem/SWEEP-STATE.md` watermark registry it owns. That one write is manifest-
    driven from disk, so sweep.py validates first: default-mode manifests only, projects already
    present in SWEEP-STATE only (never adding a row), every watermark fullmatching the
    report-basename shape with neither a newline nor the ` · ` field separator; any violation
    refuses the whole run. No network, no git, no shelling out.
  - Deliberately NOT allowlisted: `ballast-statusline` (statusline/install.py) edits
    `~/.claude/settings.json` directly, so it stays behind the normal prompt, bare or not.

REGEX INVARIANTS (this runs on every matching Bash/PowerShell call, so a bug here is a standing
auto-approval hole -- do not loosen; anything ambiguous must read as "not a match"):
  - re.fullmatch, never .match or substring containment: the ENTIRE command must be one plain
    invocation. This also closes the prefix hazard -- `ballast-modes`, `ballast-statusline`,
    `ballast-sweeper` leave unconsumed input and cannot match -- and avoids the trailing-newline
    artifact `.match()` allows (a `$` anchor is satisfied before a final newline).
  - THREE separate compiled patterns, not one alternation: a naive
    `ballast-(?:extract|mode|sweep)s?` would blur that prefix boundary, and per-pattern matching
    keeps reason selection trivial.
  - The argument character class excludes every metacharacter that could carry a second command:
    `; & |` (sequencing/pipe), `` ` `` and `$` (substitution), `< >` (redirection), `\\` (escape).
  - Argument whitespace is `[ \t]`, deliberately NOT `\\s`: `\\s` matches newline, and a raw
    newline or CR is itself a shell statement separator. decide() additionally rejects any
    multi-line command before any regex is consulted.
  - Everything that doesn't match (typos, path-qualified invocations, pipes, chains, PowerShell
    syntax) falls through to the normal permission flow. Worst case is an extra prompt, never a
    skipped one.
  - Every shim name starts with `ballast-`: that literal is this guard's anchor in run.sh's
    shell-guards prefilter.

RESIDUAL (accepted): the decision keys on the command STRING, not the resolved binary -- so
`ballast-extract <any-readable-path>` is allowed, and a hostile shim planted earlier on PATH would
run under the same name. Each is bounded by its own contract above; tightening to a path prefix
would break legitimate transcript/backup paths.

Output: marker absent -> None, unconditionally, before the command is read. Marker present -> on
match, {"decision": "allow", "reason": ..., "banner": None}; on no match, None. No banner
(deliberate): this fires dozens of times per postmortem extraction run, so a visible line each
would be noise -- hooks.json's statusMessage already surfaces the call, and run.sh still ledgers
it. An exception here is caught by shell-guards.py, announced, and skipped -- which, like every
non-match, only ever ADDS a prompt.
"""
import os
import re

NAME = "ballast-allow"


def home_root():
    # BALLAST_CLAUDE_HOME: hermetic-test override (hooks/CLAUDE.md rule) -- production never sets
    # it, so the opt-in marker below resolves against the real ~/.claude.
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def _standing_grants_enabled():
    """True only if the user deliberately opted in via <home_root>/ballast/allow-standing-grants.
    Fails CLOSED on ANY error: a bug that returned True by accident would silently reinstate a
    standing grant nobody asked for, while failing closed only ever costs an extra prompt."""
    try:
        return os.path.isfile(os.path.join(home_root(), "ballast", "allow-standing-grants"))
    except Exception:
        return False


# Three separate patterns, applied with re.fullmatch (see decide) so the ENTIRE command must be one
# plain invocation plus plain arguments. The argument character class excludes every metacharacter
# that would let a second command ride along:
#   ; & |        -- command sequencing / backgrounding / piping
#   ` $          -- command substitution
#   < >          -- redirection
#   \            -- escape / line-continuation
# Argument whitespace is [ \t], NOT \s (which also matches newline, a shell statement separator);
# decide() rejects multi-line commands outright before any regex runs. See the header's REGEX
# INVARIANTS for why this is three patterns and not one alternation.
_SAFE_EXTRACT = re.compile(r"ballast-extract(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_MODE = re.compile(r"ballast-mode(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_SWEEP = re.compile(r"ballast-sweep(?:[ \t]+[^;&|<>`$\\]*)?")

_REASONS = (
    (_SAFE_EXTRACT,
     "ballast's own transcript extractor (bare, unmodified invocation; writes only its own digest "
     "output dir)."),
    (_SAFE_MODE,
     "ballast's own statusline mode-state writer (bare, unmodified invocation; writes only "
     "validated per-session chip state under ~/.claude/ballast/modes/)."),
    (_SAFE_SWEEP,
     "ballast's own postmortem-corpus engine (bare, unmodified invocation; writes only its own "
     "sweep bundle dir and, on `advance`, the SWEEP-STATE watermark registry it owns)."),
)


def decide(payload):
    """Return an allow {"decision", "reason", "banner"}, or None to defer. Total on any dict."""
    # OPT-IN GATE, checked before anything else: marker absent -> no decision without reading the
    # payload at all.
    if not _standing_grants_enabled():
        return None

    if payload.get("tool_name", "") != "Bash":
        # Only the Bash tool exposes the plain `command` string this guard understands.
        # PowerShell's tool_input shape isn't special-cased -- bare `ballast-extract` isn't a
        # valid PowerShell invocation anyway (no bin/ PATH injection there).
        return None

    # `or {}` (not merely a .get default): an explicit JSON `tool_input: null` returns None from
    # .get(). Any non-dict tool_input (null, list, string) defers instead of raising.
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        return None

    cmd = command.strip()  # trims only the ends; an embedded separator survives to be caught below

    # A raw newline or CR anywhere in the command is a shell statement separator (same as ';').
    # Reject multi-line commands outright so a second statement can't ride along after the bare name.
    if "\n" in cmd or "\r" in cmd:
        return None

    for pattern, reason in _REASONS:
        if pattern.fullmatch(cmd):
            return {"decision": "allow", "reason": reason, "banner": None}
    return None
