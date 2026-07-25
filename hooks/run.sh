#!/usr/bin/env bash
# hooks/run.sh -- ballast's single dispatcher for every registered Claude Code hook.
#
# WHY a dispatcher instead of wiring hooks.json straight at each script: hooks.json's exec-form
# command is a fixed string ("bash", args [...]) baked in at plugin-load time -- it can't itself
# branch on OS or probe for a working Python. Routing every entry through this one script gives us
# ONE place to (a) resolve an interpreter once per hook invocation, (b) fail open if anything about
# the environment is unexpected, and (c) record a fire ledger, instead of scattering that logic
# across nine hook scripts.
#
# Usage: hooks.json invokes `bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" <hook-name>` for every
# registered event. stdin (the hook's JSON payload from Claude Code) passes through untouched --
# this script never reads or buffers it; the dispatched child inherits the same stdin fd.
#
# DISPATCH: the child hook is CAPTURED (command substitution), not exec'd, so its stdout can be
# forwarded unchanged AND its exit code inspected for the fire ledger below -- stderr still passes
# straight through uncaptured (a child's own stderr fd, unbuffered), and the ledger write is a
# pure side effect: it never alters what gets forwarded or the exit code that follows.
#
# Fail-open contract: ANY unresolvable state here (unknown hook name, no working Python for a
# .py hook) exits 0 silently. A guard/reminder hook that crashes or blocks the user's session on
# an environment quirk is worse than a guard that occasionally no-ops -- these are safety-net
# hooks, not the product itself.

set -u

# MSYS/Git-Bash hardening: if ANY ancestor exported MSYS_NO_PATHCONV (or a broad
# MSYS2_ARG_CONV_EXCL), it inherits process-tree-wide and every native spawn below breaks -- the
# dispatch hands python an MSYS-form path (/c/Users/...) and relies on Git Bash's argv conversion
# to make it C:\Users\...; with conversion disabled python gets C:\c\... and every hook errors.
# PreToolUse hook errors fail CLOSED in Claude Code (live-confirmed: a sidecar session with an
# inherited NO_PATHCONV had ALL shell tools blocked), which violates this dispatcher's fail-open
# contract. Hooks must never depend on a parent's MSYS conversion settings -- restore defaults.
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

# Directory this script lives in, resolved via BASH_SOURCE so it works regardless of the caller's
# cwd (hooks.json invokes us with a full ${CLAUDE_PLUGIN_ROOT}-substituted path, but resolving our
# own location defensively costs nothing and avoids relying on that substitution being exact).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# arg1 = the logical hook name from hooks.json (e.g. "git-commit-guard", "doc-write-guard").
name="${1:-}"

# Name -> filename map for exactly the hooks registered in hooks.json. Deliberately a static list
# (not a directory scan) so an unregistered or stray script in this directory is never silently
# invoked -- the map IS the registration surface, hooks.json just refers to keys in it.
case "$name" in
  ballast-principles)            file="ballast-principles.sh" ;;
  freehand-mode)                 file="freehand-mode.sh" ;;
  subagent-fanout)                file="subagent-fanout.sh" ;;
  askuserquestion-recommend)      file="askuserquestion-recommend.py" ;;
  package-install-guard)          file="package-install-guard.py" ;;
  git-commit-guard)                file="git-commit-guard.sh" ;;
  commit-review-gate)               file="commit-review-gate.py" ;;
  ballast-allow)                   file="ballast-allow.py" ;;
  doc-write-guard)                 file="doc-write-guard.py" ;;
  plan-authoring)                  file="plan-authoring.sh" ;;
  plan-handoff)                     file="plan-handoff.sh" ;;
  mode-state-cleanup)               file="mode-state-cleanup.sh" ;;
  harness-sweep-nudge)              file="harness-sweep-nudge.sh" ;;
  inline-churn-nudge)               file="inline-churn-nudge.py" ;;
  dev-process-nudge)                file="dev-process-nudge.py" ;;
  *)
    # Unknown hook name -- fail open silently (see contract above).
    exit 0
    ;;
esac

target="$DIR/$file"

# Does this hook need a Python interpreter? Every .py hook does (it IS Python), and six .sh hooks
# shell out to Python internally: git-commit-guard.sh (JSON-emit step), ballast-principles.sh
# (JSON escaping of the injected text), freehand-mode.sh (.prompt extraction for the
# prompt-scoped keyword match -- without a resolved interpreter it silently degrades to the old
# raw-payload scan, the exact false-arm class the rework killed), harness-sweep-nudge.sh
# (JSON-emit step, same pattern as ballast-principles.sh), plan-handoff.sh (session_id extraction
# + the statusline exec-chip write + JSON-emit step -- degrades to its own static heredoc
# fallback without a resolved interpreter), and mode-state-cleanup.sh (session_id extraction +
# the statusline state-file cleanup call -- degrades to the CLAUDE_SESSION_ID env-var fallback).
# Resolve ONCE here for exactly those, and export it so the .sh hooks reuse the SAME verified
# interpreter instead of each falling back to a bare `python` (which on Windows may be the
# Microsoft-Store stub). Hooks that need no Python (subagent-fanout, plan-authoring,
# askuserquestion-recommend is .py so covered) skip the probe entirely and pay zero
# interpreter-resolution cost.
#
# WHY resolve before the .sh/.py split (this is the fix for the original bug): the export used to
# live only inside the *.py) branch, so the two Python-shelling .sh hooks never received it and
# always fell back to bare `python`. On a py-launcher-only / Store-stub Windows box that meant the
# SessionStart principles block and the commit-review nudge silently produced nothing every session.
needs_python=0
case "$file" in *.py) needs_python=1 ;; esac
case "$name" in git-commit-guard|ballast-principles|freehand-mode|harness-sweep-nudge|plan-handoff|mode-state-cleanup) needs_python=1 ;; esac

if [ "$needs_python" -eq 1 ]; then
  # Resolve a WORKING Python 3, not just a Python-shaped name on PATH. `python3`, `python`, and
  # `py -3` all exist as bare names on some OS/installer combination, but a name existing is not
  # proof it runs -- notably Windows ships a `python3`/`python` App-Execution-Alias that resolves
  # on PATH and is not an interpreter at all.
  #
  # That alias does NOT merely exit non-zero: executed non-interactively it HANGS INDEFINITELY
  # (the App Installer redirector waits on a Microsoft Store UI no headless shell can satisfy),
  # wedging every hook fire instead of falling through -- and making the fail-open path below
  # unreachable. Two COMPLEMENTARY defenses:
  #   1. TIMEOUT -- bound every probe run, so any hanging candidate loses the race. `timeout` is
  #      coreutils (Git Bash + Linux; absent on stock macOS or a stripped image). Where it is
  #      absent, defense 2 is what keeps the alias from being executed at all.
  #   2. TWO-PASS path RESOLUTION -- pass 1 resolves each candidate to the first PATH hit that is
  #      NOT an alias and probes that absolute path; pass 2 retries by bare name only if nothing
  #      real resolved, so a genuine Store-installed Python still works. Resolving rather than
  #      skipping matters: the common Windows PATH layout puts the alias AHEAD of a real install,
  #      so skipping the candidate outright would step over a working interpreter behind it.
  if command -v timeout >/dev/null 2>&1; then PROBE="timeout 5"; else PROBE=""; fi
  PY=""
  for pass in skip-stubs allow-stubs; do
    for c in python3 python "py -3"; do
      run="$c"
      if [ "$pass" = "skip-stubs" ]; then
        # ${c%% *} = the bare command word ("py" for the two-word "py -3" candidate). `type -aP`
        # lists EVERY PATH hit, not just the first, so a real install shadowed by the alias is
        # still reachable; -i because Windows paths are case-insensitive.
        real="$(type -aP "${c%% *}" 2>/dev/null | grep -iv windowsapps | head -1 || true)"
        [ -z "$real" ] && continue
        # Swap the resolved absolute path in for the command word, keeping any trailing args
        # (the "-3" of "py -3"). BALLAST_PYTHON then carries the resolved path, not a bare name.
        case "$c" in *" "*) run="$real ${c#* }" ;; *) run="$real" ;; esac
      fi
      # shellcheck disable=SC2086 -- intentional word-split for "py -3" and the optional timeout.
      if $PROBE $run -c "import sys" >/dev/null 2>&1; then PY="$run"; break 2; fi
    done
  done
  # Export for BOTH dispatch branches. If none resolved, leave BALLAST_PYTHON unset: a .py hook then
  # fails open (exit 0 below), and a .sh hook falls back to its own bare-`python` default -- either
  # way the session is never bricked over a missing interpreter.
  [ -n "$PY" ] && export BALLAST_PYTHON="$PY"
fi

# FIRE LEDGER: best-effort audit line for every fire (output emitted, or a
# non-zero exit such as an exit-2 block) at <ledger-home>/ballast-hook-fires.log.
# Hooks fire silently in the product UI unless they emit systemMessage; this
# ledger is the retrospective record ("did X fire last week?") that silent-fire
# audits and postmortems can grep. Never logs non-fires; any logging failure is
# swallowed -- the ledger must never affect hook behavior or exit codes.
# Ledger home resolves BALLAST_CLAUDE_HOME (hermetic-test override, mirroring
# commit-review-gate.py) -> $HOME/.claude (production) -> nowhere (HOME unset:
# skip silently -- under `set -u` a bare $HOME would ABORT the script and eat
# the child's exit code, e.g. converting an exit-2 hard block into a dead guard).
# Each line also carries two trailing fields (appended at the END so existing
# greppers of the rc=/out= prefix keep working): proj= (basename of
# CLAUDE_PROJECT_DIR, stripped via pure parameter expansion -- no fork -- of
# both / and \ so it resolves correctly on Windows-native paths too) and sid=
# (CLAUDE_SESSION_ID verbatim -- the full UUID, which IS the transcript
# filename under ~/.claude/projects/<slug>/). Both env vars are documented as
# set in hook processes (code.claude.com/docs/en/hooks -- Environment
# Variables); the `:-` fallbacks keep `set -u` safe and simply log empty
# fields on older harness versions that don't set them.
# One-generation size rollover keeps the log bounded: once it exceeds ~5MB
# (years of fires at observed rates) the current file is moved to
# ballast-hook-fires.log.old (a single prior generation, not a numbered
# series) before the new line is appended -- an audit convenience, not a
# record of legal weight.
# $1 = rc, $2 = out tag (yes / no / skip-no-python).
ledger() {
  local dir="${BALLAST_CLAUDE_HOME:-}"
  [ -z "$dir" ] && dir="${HOME:+$HOME/.claude}"
  [ -n "$dir" ] || return 0
  local log="$dir/ballast-hook-fires.log"
  local proj="${CLAUDE_PROJECT_DIR:-}"
  proj="${proj##*[/\\]}"   # basename via pure expansion -- no fork, strips both / and \ (Windows-native paths)
  { [ -d "$dir" ] || mkdir -p "$dir"     # [ -d ] is a builtin: no fork on the common already-exists path
    [ -f "$log" ] && [ "$(wc -c < "$log")" -gt 5242880 ] && mv -f "$log" "$log.old"
    printf '%s %s rc=%s out=%s proj=%s sid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$name" "$1" "$2" \
      "$proj" "${CLAUDE_SESSION_ID:-}" >> "$log"; } 2>/dev/null || true
}

out=""
rc=0
case "$file" in
  *.sh)
    # Shell hooks run under bash (a Claude Code prerequisite on every OS -- Git Bash on Windows).
    # They inherit BALLAST_PYTHON from above if they shell out to Python.
    out="$(bash "$target")"; rc=$?
    ;;
  *.py)
    if [ -z "${BALLAST_PYTHON:-}" ]; then
      # No working Python for a Python hook -- fail open (see contract above), but LEDGER the
      # skip: a whole session of silently dead .py hooks is the exact degradation class the
      # ledger exists to catch, and an early exit here would otherwise leave zero evidence.
      ledger 0 skip-no-python
      exit 0
    fi
    # shellcheck disable=SC2086 -- intentional word-split for the two-word "py -3" candidate.
    out="$(${BALLAST_PYTHON} "$target")"; rc=$?
    ;;
esac

# Forward the hook's stdout untouched (Claude Code parses it as hook JSON), computing the
# fired flag ONCE so forwarding and ledgering can never desync.
fired=no
[ -n "$out" ] && { printf '%s\n' "$out"; fired=yes; }

if [ "$fired" = yes ] || [ "$rc" -ne 0 ]; then
  ledger "$rc" "$fired"
fi
exit "$rc"
