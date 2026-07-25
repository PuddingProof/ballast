#!/usr/bin/env bash
# statusline/render.sh -- this is the exact command Claude Code's settings.json `statusLine`
# key points at (wired by statusline/install.py). Claude Code invokes this file on every
# statusLine refresh, feeds the statusLine JSON payload on stdin, and takes this process's
# FIRST STDOUT LINE as the entire status row -- so this shim has exactly three jobs:
#   1. Resolve a real, working python3 (never assume `python` vs `python3` exists, and never
#      trust a name to actually BE an interpreter -- Windows ships a `python3` App-Execution-Alias
#      stub that's on PATH but HANGS INDEFINITELY when executed non-interactively, not merely
#      exits non-zero). Same probe loop as bin/ballast-extract, kept in lockstep with it
#      deliberately -- see that file's comments for the "py -3" array-split and path-resolution
#      rationale.
#   2. exec that interpreter on render.py, passing stdin through untouched (render.py reads
#      the JSON payload itself; this shim never parses or touches it).
#   3. If NO interpreter resolves at all: print NOTHING and exit 0. Fail-quiet, not fail-loud
#      -- an error string burned into the status bar on every single refresh is a much worse
#      UI regression than a blank reserved row, and there is no stderr channel a user would
#      ever see from a statusLine command anyway.

set -euo pipefail

# Resolve our own directory even if invoked via a relative path or symlink, so the render.py
# reference below is robust regardless of caller cwd.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Interpreter resolution: never assume `python` vs `python3` exists, and never trust a name on
# PATH to be a real interpreter. Probe candidates in order and verify each ACTUALLY runs.
#
# "py -3" is a two-word candidate (the Windows py launcher with a version flag) -- kept as an array
# element (not a single string) so it splits into ["py", "-3"] without eval or word-splitting hacks.
#
# The specific hazard: `.../AppData/Local/Microsoft/WindowsApps/python3` is a Windows
# App-Execution-Alias. Executed non-interactively it does NOT exit non-zero -- it HANGS
# INDEFINITELY, because the App Installer redirector blocks on a Microsoft Store UI that a headless
# shell can never satisfy. A hang is strictly worse than a failure: it wedges the caller forever
# instead of falling through -- and for THIS file a hang means the statusline command never returns
# on every single refresh.
#
# Two COMPLEMENTARY defenses -- neither is sufficient alone:
#   1. TIMEOUT -- bounds every probe execution, so any hanging candidate loses the race instead of
#      wedging us. `timeout` is coreutils: present in Git Bash and on Linux, absent on stock macOS
#      or a stripped image. Where it is absent, defense 2 is what keeps the alias from ever being
#      executed -- which is why these are complementary, not redundant.
#   2. TWO-PASS path RESOLUTION -- pass 1 resolves each candidate to the first PATH hit that is NOT
#      an alias and probes that absolute path; pass 2 retries by bare name only if nothing real
#      resolved, so a genuine Store-installed Python still works. Resolving rather than skipping is
#      the point: the common Windows PATH layout puts the alias AHEAD of a real install, so
#      skipping the whole candidate on that signal would step over a working interpreter sitting
#      directly behind the stub.
if command -v timeout >/dev/null 2>&1; then PROBE=(timeout 5); else PROBE=(); fi

PY=()
for pass in skip-stubs allow-stubs; do
  for candidate in "python3" "python" "py -3"; do
    # Split the candidate on whitespace into an array via read (no eval) -- "py -3" is a
    # two-word candidate (the Windows py launcher with a version flag) and must land in PY as
    # ["py", "-3"], not the single literal string "py -3".
    read -r -a cand_parts <<<"$candidate"
    if [ "$pass" = "skip-stubs" ]; then
      # `type -aP` lists EVERY PATH hit, not just the first -- that's what lets us reach a real
      # install shadowed by an alias. -i because Windows paths are case-insensitive and PATH
      # echoes them as spelled.
      real="$(type -aP "${cand_parts[0]}" 2>/dev/null | grep -iv windowsapps | head -1 || true)"
      [ -z "$real" ] && continue
      cand_parts[0]="$real"
    fi
    # ${PROBE[@]+...} so an empty PROBE array expands to nothing under `set -u`.
    if ${PROBE[@]+"${PROBE[@]}"} "${cand_parts[@]}" -c 'import sys' >/dev/null 2>&1; then
      PY=("${cand_parts[@]}")
      break 2
    fi
  done
done

if [ "${#PY[@]}" -eq 0 ]; then
  # No working Python 3 found -- fail-quiet per this file's header contract.
  exit 0
fi

# exec (not a subshell call) so this shim doesn't linger as an extra process layer, and so
# render.py's exit code propagates untouched (it always exits 0 by its own contract anyway).
exec "${PY[@]}" "$DIR/render.py"
