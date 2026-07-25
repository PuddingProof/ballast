#!/usr/bin/env python3
"""ballast-allow.py -- PreToolUse hook (matcher "Bash|PowerShell"), self-scoped permission allow.

WHY THIS EXISTS: a plugin cannot ship permission-settings entries (Claude Code has no manifest
mechanism for that -- see build spec's portability-mechanism #3). Without SOME allow path, every
single invocation of the plugin's own `ballast-extract` transcript-reader (or `ballast-mode`
session-mode writer, or `ballast-review` sidecar-review shim) would sit behind a manual permission
prompt forever, even though each is narrowly bounded and part of the shipped harness. This hook is
the narrow, self-contained substitute: it recognizes exactly three bare, unmodified invocation
shapes -- `ballast-extract`, `ballast-mode`, and `ballast-review` -- and answers
`permissionDecision: allow` for THAT call only. Everything else -- literally any other command,
including any of the three names wrapped in anything that could smuggle a second command alongside
it -- is left untouched (exit 0 -> defer to Claude Code's normal permission flow, which still
prompts the user as usual).

OPT-IN (governance review item): none of the above is a standing grant the plugin decides on the
user's behalf. A plugin cannot ship permission-settings entries -- and per governance review, it
should not silently self-grant one at the hook layer either, which is exactly what unconditionally
emitting `permissionDecision: allow` amounted to. So this hook now ships INERT by default: it emits
NOTHING for ANY input unless the user has deliberately created
`<home_root>/ballast/allow-standing-grants` (see `_standing_grants_enabled` / `home_root`, mirroring
doc-write-guard.py's home_root() convention; documented in the README as an explicit opt-in step).
Marker absent -> every command, including the three safe shapes above, defers to Claude Code's
normal permission flow like this hook wasn't installed. Marker present -> exactly the behavior
described above. The marker check runs ONCE, before any payload parsing or command matching (the
cheapest possible short-circuit for the common no-marker case), and fails CLOSED on any error --
the inverse of this file's usual fail-open direction, because here the risky output is the allow
decision itself, not a missed one.

SECURITY RATIONALE (why the regexes are this conservative and not looser):
  - This hook runs on EVERY Bash/PowerShell call in EVERY session that has the plugin enabled.
    A bug here is a standing auto-approval hole, not a one-off mistake -- so the match surface is
    kept as small as intent allows, and anything ambiguous is deliberately treated as "not a match"
    (silent pass-through), never as "match, allow" by default.
  - `ballast-extract` reads transcript JSONL and prints markdown to stdout (see
    session-postmortem/scripts/extract.py) for every subcommand except ONE: `bundle --out <dir>`,
    which writes the bulk-dump markdown files + a manifest.json to that dir. No other subcommand
    writes anything, and none makes network calls or runs git operations. `bundle`'s write surface
    is bounded three ways: (a) it only ever writes FIXED filenames it derives itself (`<dump>.md`,
    `manifest.json`) -- the `--out` argument selects a directory, never a filename, so it can't be
    steered into overwriting an arbitrary target file; (b) it refuses to write into a dir that
    already exists, is non-empty, and has no prior `manifest.json` -- so it can't silently clobber
    an unrelated directory's contents, only a dir it created itself or one it already owns from a
    prior run; (c) every byte written is content DERIVED SOLELY from parsing the transcript path
    already on the command line -- nothing in `bundle`'s output surface is attacker-shaped beyond
    what an auto-allowed read-only dump already exposed. Auto-allowing the bare invocation still
    carries far less risk than auto-allowing "any command containing ballast-extract", which is
    why the match is anchored to the ENTIRE command string (re.fullmatch), not merely to substring
    containment.
  - `ballast-mode` (statusline/mode-state.py) DOES write, unlike ballast-extract's read-only
    surface -- but the write surface is deliberately bounded: `mode` and `--session <sid>` are
    both validated there against strict fullmatch allowlists (mode `[a-z0-9-]{1,32}`, sid
    `[0-9a-fA-F-]{8,64}`) BEFORE any filesystem operation, and neither charset can contain `/`,
    `\\`, or `..` -- so a session id can never traverse out of its target directory. Every write
    this CLI can perform lands under `~/.claude/ballast/modes/` and nowhere else. Worst case of a
    bug or a hostile-but-validated argument here is a wrong or missing status-line chip, not an
    arbitrary write. Auto-allowing the bare invocation is bounded by that same bind: it is
    ballast-mode's OWN argument validation, not this hook, that keeps the write surface narrow --
    this hook only ever needs to keep a SECOND command from riding along (see below).
  - Deliberately NOT on this allowlist: `ballast-statusline` (the settings.json installer,
    statusline/install.py). It edits `~/.claude/settings.json` directly -- a much larger blast
    radius than a per-session mode chip -- so it stays behind the normal permission prompt on
    every invocation, bare or not. See also the prefix-hazard note below: `ballast-statusline`
    must never accidentally match via loose handling of the `ballast-mode` pattern.
  - `ballast-review` (bin/ballast-review, the sidecar-review trampoline) has the LARGEST blast
    radius of the three: it spawns a full headless `claude -p` session under the user's own
    account -- a real agent loop with network access, well beyond extract's read-only file access
    or mode's bounded validated writes. Three things keep auto-allowing the bare invocation safe
    despite that: (1) the shim pins the sidecar's prompt to START with the native `/code-review`
    slash command (plus, outside the prompt entirely, optional leading --model/--effort flags
    handed to the claude CLI itself -- session cost dials whose values are still bounded by the
    char class below). State that bound honestly: everything after the level is free-form prose
    that DOES reach the sidecar as the skill's target/context arguments -- the char class stops a
    second SHELL command, never prompt content, so an auto-allowed call can steer the review
    session's text. What bounds that steering surface is (2)/(3) below plus the sidecar's own
    session boundaries: skewed prose can degrade a review's focus, but the sidecar stays
    read-only and its findings are adjudicated by the parent before anything acts on them, so
    steering never silently converts to writes; (2) the
    argument char class below excludes every shell metacharacter, so nothing in the forwarded args
    can escape into a second command or reshape what gets sent to `claude -p`; (3) the shim itself
    rejects `--fix` anywhere in its args, so even the sidecar's native reviewer can't be told to
    edit the tree -- read-only in both transport and destination.
  - A second command can ride along via three vectors, all of which are closed here for all three
    patterns:
      1. inline metacharacters -- ; & | (sequencing/pipe), ` $ (substitution), < > (redirection),
         \\ (escape/line-continuation): excluded from the argument character class.
      2. a raw newline or carriage return -- these ARE shell statement separators, identical in
         effect to ';'. The argument whitespace class is [ \t] (spaces/tabs only, NOT \\s, which
         also matches \\n), AND main() rejects any multi-line command outright before either
         regex is even consulted. So "ballast-extract\\nrm -rf ~" (or ballast-mode's equivalent)
         can never auto-allow.
      3. a trailing-newline match artifact -- avoided by using re.fullmatch rather than .match()
         (with .match(), a `$` anchor is satisfied *before* a final newline).
  - Prefix hazard: all three patterns are matched with re.fullmatch, so a longer command name that
    merely STARTS WITH one of these three names -- `ballast-modes`, `ballast-statusline`,
    `ballast-reviewer`, etc. -- can never match. fullmatch requires the ENTIRE command string to be
    consumed; after the literal name, the only continuation the grammar accepts is [ \t]+
    (whitespace) or end of string, so a bare trailing letter like the `s` in `ballast-modes` (or
    `er` in `ballast-reviewer`) leaves unconsumed input and fails the match. This is inherent to
    using DISTINCT fullmatch patterns per name rather than one that risks blurring the boundary
    (e.g. a naive `ballast-(?:extract|mode|review)s?` alternation) -- it is why this file keeps
    `_SAFE_EXTRACT`, `_SAFE_MODE`, and `_SAFE_REVIEW` as three separate compiled regexes.
  - Anything that doesn't match any of the three narrow shapes (typos, path-qualified invocations, piped
    output, chained commands, multi-line commands, PowerShell syntax) is NOT auto-allowed -- it
    falls through to Claude Code's normal permission handling exactly as if this hook weren't
    installed. That is the fail-closed direction for the ALLOW decision: worst case is an extra
    permission prompt, never a skipped one.

  RESIDUAL (accepted, low risk): the decision keys on the command STRING, not on the resolved
  binary. So `ballast-extract <arbitrary-readable-path>` is auto-allowed (a read of any
  transcript-shaped file, OR -- since `bundle` -- a write of the fixed dump filenames into an
  attacker-chosen EMPTY-or-already-owned directory, bounded as described above), and a hostile
  `ballast-extract`, `ballast-mode`, or `ballast-review` planted earlier on PATH would run under
  the same name. All three are bounded by their own script's contract as described above
  (extract.py read-only except `bundle`'s narrowly-bounded write, mode-state.py's own argument
  validation, ballast-review's --fix rejection + native-command pinning); tightening to a
  path-prefix would break legitimate transcript/backup paths, so it's left as-is.

Input: the hook JSON payload on stdin (Claude Code's standard PreToolUse shape), containing at
least tool_name and tool_input.command (Bash) or tool_input (PowerShell forms vary -- we only
special-case the Bash `command` field; anything else exits 0 untouched).

Output: if the opt-in marker is absent, exit 0 with no output for every input -- unconditionally,
before the command is even matched. If the marker is present: on match, a JSON object on stdout
with hookSpecificOutput.permissionDecision = "allow" and a human-readable reason (Claude Code
surfaces this instead of prompting); on no match OR ANY error, exit 0 with no output -- silent,
fail-open, defers to normal permission flow. The whole decision runs under a broad try/except so a
malformed payload can never crash a hook that fires on every Bash/PowerShell call.
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
    Fails CLOSED (returns False) on ANY error -- the inverse of this file's usual fail-open
    direction, because here the risky output IS the allow decision itself: a bug that made this
    return True by accident would silently reinstate a standing grant nobody asked for. Failing
    closed only ever costs an extra permission prompt, matching this file's existing doctrine
    everywhere else."""
    try:
        return os.path.isfile(os.path.join(home_root(), "ballast", "allow-standing-grants"))
    except Exception:
        return False

# All three matched with re.fullmatch (see _decide) so the ENTIRE command must be a single plain
# invocation of `ballast-extract`, `ballast-mode`, or `ballast-review`, optionally followed by
# plain arguments. The argument character class excludes every metacharacter that would let a
# second command ride along:
#   ; & |        -- command sequencing / backgrounding / piping
#   ` $          -- command substitution
#   < >           -- redirection
#   \            -- escape / line-continuation
# Argument whitespace is limited to spaces and tabs ([ \t]) -- deliberately NOT the \s class,
# because \s also matches newline, and a raw newline is itself a shell statement separator. Any
# multi-line command is additionally rejected in _decide() before any regex is consulted.
#
# THREE SEPARATE compiled patterns (not one alternation) -- deliberate, not merely stylistic: it
# keeps the per-command permissionDecisionReason trivial to select (see _decide), and it keeps
# the prefix-hazard reasoning above (ballast-modes / ballast-statusline / ballast-reviewer can't
# match) obviously true of each pattern in isolation rather than resting on alternation-precedence
# subtlety.
_SAFE_EXTRACT = re.compile(r"ballast-extract(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_MODE = re.compile(r"ballast-mode(?:[ \t]+[^;&|<>`$\\]*)?")
_SAFE_REVIEW = re.compile(r"ballast-review(?:[ \t]+[^;&|<>`$\\]*)?")


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
                "permissionDecisionReason": "ballast's own transcript extractor (bare, unmodified invocation; writes only its own bundle output dir).",
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
    return None


def main():
    try:
        # OPT-IN GATE, checked ONCE before anything else (cheapest short-circuit; this is the
        # common no-marker case for any install that hasn't opted in): marker absent -> emit
        # nothing for this call, full stop, without even parsing the payload. Rationale: an
        # os.path.isfile stat is cheaper than parsing the stdin JSON payload, so checking it first
        # is the cheapest path for the common (no-marker) case; an opted-in install pays one extra
        # stat per call, dwarfed by interpreter startup cost either way.
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
