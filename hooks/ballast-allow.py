#!/usr/bin/env python3
"""ballast-allow.py -- PreToolUse hook (matcher "Bash|PowerShell"), self-scoped permission allow.

A plugin cannot ship permission-settings entries, so every invocation of the plugin's own
`ballast-extract`, `ballast-mode`, `ballast-review`, and `ballast-sweep` shims would otherwise sit
behind a manual permission prompt forever. This hook is the narrow substitute: it recognizes
exactly those four bare, unmodified invocation shapes and answers `permissionDecision: allow` for
THAT call only. Anything else -- including any of the four names wrapped in something that could
smuggle a second command alongside it -- is left untouched.

OPT-IN GATE (invariant): the hook ships INERT and emits NOTHING for ANY input unless the user has
deliberately created `<home_root>/ballast/allow-standing-grants` -- a plugin does not self-grant a
standing permission on the user's behalf. The marker check runs ONCE, before any payload parsing or
matching, and fails CLOSED on any error: the inverse of this file's usual fail-open direction,
because here the risky output IS the allow decision.

TRUST BOUNDARY. This hook only ever keeps a SECOND command from riding along; what each shim may do
is bounded by that script's OWN contract, not by anything here:
  - `ballast-extract` (session-postmortem/scripts/extract.py): read-only except `digest`, which
    writes only the fixed filenames `digest.md` + `manifest.json`, and only into a dir it created
    or already owns (`--out` selects a directory, never a filename). No network, no git.
  - `ballast-mode` (statusline/mode-state.py): validates `mode` (`[a-z0-9-]{1,32}`) and
    `--session <sid>` (`[0-9a-fA-F-]{8,64}`) against strict fullmatch allowlists before any
    filesystem op -- neither charset can contain `/`, `\\`, or `..` -- and every write lands under
    `~/.claude/ballast/modes/`. Worst case is a wrong status-line chip, not an arbitrary write.
  - `ballast-review` (bin/ballast-review): the largest blast radius -- it spawns a headless
    `claude -p` session. It pins the prompt to START with the native `/code-review` command and
    rejects `--fix` anywhere in its args. Residual, stated honestly: free-form prose after the
    level DOES reach the sidecar as skill arguments, so an auto-allowed call can steer the review's
    text -- but the sidecar stays read-only and its findings are adjudicated by the parent, so
    steering never converts to writes.
  - `ballast-sweep` (skills/harness-sweep/scripts/sweep.py): writes the fixed `bundle.md` +
    `manifest.json` under a dir it derives itself, plus `advance`'s rewrite of the
    `<home>/postmortem/SWEEP-STATE.md` watermark registry it owns. That one write is manifest-
    driven from disk, so sweep.py validates first: default-mode manifests only, projects already
    present in SWEEP-STATE only (never adding a row), every watermark fullmatching the
    report-basename shape with neither a newline nor the ` · ` field separator; any violation
    refuses the whole run. No network, no git, no shelling out.
  - Deliberately NOT allowlisted: `ballast-statusline` (statusline/install.py) edits
    `~/.claude/settings.json` directly, so it stays behind the normal prompt, bare or not.

REGEX INVARIANTS (this fires on every Bash/PowerShell call, so a bug here is a standing
auto-approval hole -- do not loosen; anything ambiguous must read as "not a match"):
  - re.fullmatch, never .match or substring containment: the ENTIRE command must be one plain
    invocation. This also closes the prefix hazard -- `ballast-modes`, `ballast-statusline`,
    `ballast-reviewer`, `ballast-sweeper` leave unconsumed input and cannot match -- and avoids the
    trailing-newline artifact `.match()` allows (a `$` anchor is satisfied before a final newline).
  - FOUR separate compiled patterns, not one alternation: a naive
    `ballast-(?:extract|mode|review|sweep)s?` would blur that prefix boundary, and per-pattern
    matching keeps reason selection trivial.
  - The argument character class excludes every metacharacter that could carry a second command:
    `; & |` (sequencing/pipe), `` ` `` and `$` (substitution), `< >` (redirection), `\\` (escape).
  - Argument whitespace is `[ \t]`, deliberately NOT `\\s`: `\\s` matches newline, and a raw
    newline or CR is itself a shell statement separator. _decide() additionally rejects any
    multi-line command before either regex is consulted.
  - Everything that doesn't match (typos, path-qualified invocations, pipes, chains, PowerShell
    syntax) falls through to the normal permission flow. Worst case is an extra prompt, never a
    skipped one.

RESIDUAL (accepted): the decision keys on the command STRING, not the resolved binary -- so
`ballast-extract <any-readable-path>` is allowed, and a hostile shim planted earlier on PATH would
run under the same name. Each is bounded by its own contract above; tightening to a path prefix
would break legitimate transcript/backup paths.

Input: the PreToolUse JSON payload on stdin. Only the Bash tool's `tool_input.command` is
special-cased; anything else exits 0 untouched.

Output: marker absent -> exit 0 with no output, unconditionally, before the command is matched.
Marker present -> on match, the allow JSON on stdout (Claude Code surfaces the reason instead of
prompting); on no match OR ANY error, exit 0 with no output -- fail-open, deferring to the normal
permission flow. The whole decision runs under a broad try/except so a malformed payload can never
crash a hook that fires on every Bash/PowerShell call.
"""
import json
import os
import re
import sys


def home_root():
    # BALLAST_CLAUDE_HOME: hermetic-test override (hooks/CLAUDE.md rule) -- production never sets
    # it, so the opt-in marker below resolves against the real ~/.claude. Mirrors
    # doc-write-guard.py's home_root() convention (kept in sync deliberately, not shared code --
    # these are two independently fail-open/fail-closed hooks and shouldn't import each other).
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def _standing_grants_enabled():
    """True only if the user deliberately opted in via <home_root>/ballast/allow-standing-grants.
    Fails CLOSED on ANY error: a bug that returned True by accident would silently reinstate a
    standing grant nobody asked for, while failing closed only ever costs an extra prompt."""
    try:
        return os.path.isfile(os.path.join(home_root(), "ballast", "allow-standing-grants"))
    except Exception:
        return False

# Four separate patterns, applied with re.fullmatch (see _decide) so the ENTIRE command must be one
# plain invocation plus plain arguments. The argument character class excludes every metacharacter
# that would let a second command ride along:
#   ; & |        -- command sequencing / backgrounding / piping
#   ` $          -- command substitution
#   < >          -- redirection
#   \            -- escape / line-continuation
# Argument whitespace is [ \t], NOT \s (which also matches newline, a shell statement separator);
# _decide() rejects multi-line commands outright before any regex runs. See the header's REGEX
# INVARIANTS for why this is four patterns and not one alternation.
_SAFE_EXTRACT = re.compile(r"ballast-extract(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_MODE = re.compile(r"ballast-mode(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_REVIEW = re.compile(r"ballast-review(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_SWEEP = re.compile(r"ballast-sweep(?:[ \t]+[^;&|<>`$\\]*)?")


def _decide(payload):
    """Return an allow-dict to print, or None to defer silently. Pure/total on any dict input."""
    if payload.get("tool_name", "") != "Bash":
        # Only the Bash tool exposes the plain `command` string this hook understands. PowerShell's
        # tool_input shape isn't special-cased -- bare `ballast-extract` isn't a valid PowerShell
        # invocation anyway (no bin/ PATH injection there), so there's nothing safe to auto-allow.
        return None

    # `or {}` (not merely a .get default): an explicit JSON `tool_input: null` returns None from
    # .get(), and None.get(...) would raise -- breaking the documented fail-open contract. Coerce
    # any non-dict tool_input (null, list, string) to {} so we defer silently instead of crashing.
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

    if _SAFE_EXTRACT.fullmatch(cmd):
        # No systemMessage here (deliberate skip): this fires dozens of times per postmortem
        # extraction run, so a visible line each would be noise -- hooks.json's own statusMessage
        # already surfaces the allow. It still hits run.sh's fire ledger like every other fire.
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "ballast's own transcript extractor (bare, unmodified invocation; writes only its own digest output dir).",
            }
        }
    if _SAFE_MODE.fullmatch(cmd):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "ballast's own statusline mode-state writer (bare, unmodified invocation; "
                    "writes only validated per-session chip state under ~/.claude/ballast/modes/)."
                ),
            }
        }
    if _SAFE_REVIEW.fullmatch(cmd):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "ballast's own sidecar-review trampoline (bare, unmodified invocation; pins its "
                    "prompt to the native /code-review slash command and refuses --fix)."
                ),
            }
        }
    if _SAFE_SWEEP.fullmatch(cmd):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "ballast's own postmortem-corpus engine (bare, unmodified invocation; writes "
                    "only its own sweep bundle dir and, on `advance`, the SWEEP-STATE watermark "
                    "registry it owns)."
                ),
            }
        }
    return None


def main():
    try:
        # OPT-IN GATE, checked ONCE before anything else: marker absent -> emit nothing for this
        # call without even parsing the payload. First because a stat is cheaper than parsing the
        # stdin JSON, and no-marker is the common case for an install that hasn't opted in.
        if not _standing_grants_enabled():
            return 0
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        decision = _decide(payload)
        if decision is not None:
            print(json.dumps(decision))
    except Exception:
        # Any error at all -- malformed payload, unexpected shape -- fails OPEN: exit 0, emit
        # nothing, defer to Claude Code's normal permission prompt. A standing per-call hook must
        # never brick a session, and the fail-open direction here only ever ADDS a prompt.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
