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

# Ballast's per-user state directory, resolved ONCE for every consumer in this script (the
# interpreter cache immediately below, and the fire ledger further down). Factored into one helper
# so the two can never drift on WHERE state lives: BALLAST_CLAUDE_HOME (hermetic-test override,
# mirroring commit-review-gate.py) -> $HOME/.claude (production) -> empty (HOME unset: every caller
# treats an empty value as "no state dir" and skips silently -- under `set -u` a bare $HOME
# reference would ABORT this script and eat the dispatched child's exit code, e.g. converting an
# exit-2 hard block into a dead guard).
# NEVER the plugin dir: it is replaced wholesale on plugin update (standing repo rule).
# Sets a global rather than printing, deliberately: a `$(...)` capture would fork a subshell on
# EVERY hook fire, and cutting per-fire forks is the entire point of the caching work below.
BALLAST_HOME_DIR=""
resolve_ballast_home() {
  BALLAST_HOME_DIR="${BALLAST_CLAUDE_HOME:-}"
  [ -z "$BALLAST_HOME_DIR" ] && BALLAST_HOME_DIR="${HOME:+$HOME/.claude}"
  return 0   # the [ -z ] test above is the last command; without this a set home would "fail"
}
resolve_ballast_home

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
  # ===== THREE-TIER INTERPRETER RESOLUTION (fast paths in front of the cold probe) ==============
  # WHY: the cold probe below is expensive -- up to three candidate EXECUTIONS, each a process
  # start, plus `type -aP`/`grep`/`head` forks per candidate. Measured at ~1.5s of a 2.4s hook fire
  # under load, and re-derived IDENTICALLY four times per Bash tool call (four hooks share the
  # `Bash|PowerShell` matcher). That per-fire waste is what turns a normal multi-session load spike
  # into 5s hook timeouts, so the fix is to stop re-deriving a constant -- not to chase a slow hook.
  # Tiers, cheapest first: (1) an interpreter already exported into this process tree, (2) the
  # on-disk cache written by a previous cold probe, (3) the cold probe itself (unchanged).
  #
  # VALIDITY TEST IS `[ -x ]` ONLY -- no TTL, no `stat`, no `date`. Three reasons, all load-bearing:
  #   - `[ -x ]` is a shell BUILTIN: zero forks. A mtime/TTL check would cost the very fork this
  #     change exists to eliminate, and would buy nothing the points below don't already cover.
  #   - The cache can only ever hold a probe-VERIFIED interpreter (tier 3 writes it after the
  #     `-c "import sys"` execution succeeds), so a Windows Store alias-stub can never be cached.
  #   - A moved or uninstalled interpreter SELF-HEALS: it fails `[ -x ]`, falls through to the cold
  #     probe, and the probe rewrites the file.
  # ACCEPTED RESIDUAL, by name: an interpreter that still EXISTS at the cached path but has been
  # BROKEN IN PLACE (a bad in-place upgrade) keeps being served from cache, because `[ -x ]` still
  # passes. Recovery is deleting <ballast-home>/ballast-python. Judged acceptable against paying a
  # second of probe cost on every single hook fire -- do not "fix" this with a TTL or a re-probe.
  #
  # FAIL OPEN THROUGHOUT: every cache read/write is best-effort and degrades to the cold probe. A
  # missing, read-only, or unwritable home directory must never abort dispatch.
  # TRUST BOUNDARY: the cached value is later EXECUTED, so anyone who can write this file chooses
  # the interpreter every hook runs. That is not a new privilege -- the same ~/.claude tree already
  # holds settings.json, which can name arbitrary hook commands outright -- so the cache adds no
  # reach an attacker at that boundary lacks. It does mean the file must stay inside ballast's own
  # user-home state dir and never move somewhere world-writable.
  PY=""

  # --- TIER 1: inherited env. A re-entrant fire (a hook that itself triggers hooks, or any parent
  # that already resolved one) used to re-probe from scratch because this value was ignored. Test
  # the FIRST WORD only -- BALLAST_PYTHON may carry args ("<path>/py.exe -3").
  if [ -n "${BALLAST_PYTHON:-}" ] && [ -x "${BALLAST_PYTHON%% *}" ]; then
    PY="${BALLAST_PYTHON}"
  fi

  # --- TIER 2: on-disk cache, in ballast's per-user home (never the plugin dir -- replaced on
  # update). Read with the `read` BUILTIN, not `head`, so the warm path stays fork-free.
  py_cache=""
  [ -n "$BALLAST_HOME_DIR" ] && py_cache="$BALLAST_HOME_DIR/ballast-python"
  if [ -z "$PY" ] && [ -n "$py_cache" ] && [ -f "$py_cache" ]; then
    cached=""
    # `|| true`: `read` returns non-zero on a last line with no trailing newline (and on an
    # unreadable file) -- neither is an error here, the value is validated below regardless.
    { read -r cached < "$py_cache"; } 2>/dev/null || true
    if [ -n "$cached" ] && [ -x "${cached%% *}" ]; then PY="$cached"; fi
  fi

  # --- TIER 3: the cold probe (unchanged behavior). Only reached when neither fast path yielded a
  # still-executable interpreter; its result is written back to the cache below.
  if [ -z "$PY" ]; then
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
  #
  # PROBE BOUND vs HOOK BUDGET: the bound must sit strictly BELOW the hooks.json timeout of the
  # group this dispatcher runs inside (5s for the Bash|PowerShell hooks). At an EQUAL bound -- what
  # this was -- a single hanging candidate (the Store alias that hangs on exec, the real 2026-07-20
  # failure) deterministically consumes the ENTIRE hook budget before the child hook is even
  # dispatched, so the fail-open path below never gets to run. A real interpreter starts in well
  # under 700ms even on a saturated box, so 2s keeps ample headroom and leaves the rest of the
  # budget for the actual hook. Re-check this bound if the hooks.json timeout ever drops.
  if command -v timeout >/dev/null 2>&1; then PROBE="timeout 2"; else PROBE=""; fi
  # No `PY=""` reset here: tier 3 is entered only when PY is already empty (see the [ -z "$PY" ]
  # guard above), and re-clearing it would read as though the tiers above could be clobbered.
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
    # Cache the probe result so the NEXT fire takes tier 2 and never pays for this again. ATOMIC:
    # write a temp file in the SAME directory (same filesystem, so `mv` is a rename, not a copy)
    # then rename over the target -- a concurrent fire reads either the old value or the new one,
    # never a half-written line. `$$` scopes the temp name to this process so parallel fires (four
    # hooks share the Bash|PowerShell matcher) cannot collide on it.
    # Only a SUCCESSFUL probe is ever written: caching a failure would mean caching an unverified
    # candidate, and tier 2's `[ -x ]` test cannot tell a Store stub from a real interpreter -- the
    # probe-verified-only invariant is what makes that cheap test safe.
    # FAIL OPEN: the whole block is best-effort (`2>/dev/null || true`, exit status discarded) --
    # an absent, read-only, or unwritable home degrades to "probe every time", never aborts dispatch.
    # PATH-VALUED RESULTS ONLY. The skip-stubs pass resolves candidates to an absolute path, but the
    # allow-stubs pass leaves them as BARE names ("python3", "py -3"). Tier 2 validates with
    # `[ -x ]`, which tests a filesystem path and can never accept a bare name -- so caching one
    # would produce a file that is rejected on every read, meaning: cold probe every fire AND a
    # temp-write + rename every fire, strictly worse than not caching at all, on exactly the
    # Store-Python boxes this probe exists for. Skip the write instead (they keep today's behavior,
    # minus the pointless I/O). Deliberately NOT solved by letting tier 2 accept bare names via
    # `command -v`: PATH can change between fires, so a name that probed clean once could later
    # resolve to the hanging Store alias -- the exact failure the two-pass probe was built to dodge.
    case "${PY%% *}" in */*) py_cacheable=1 ;; *) py_cacheable=0 ;; esac
    if [ -n "$PY" ] && [ -n "$py_cache" ] && [ "$py_cacheable" -eq 1 ]; then
      { [ -d "$BALLAST_HOME_DIR" ] || mkdir -p "$BALLAST_HOME_DIR"
        py_tmp="$py_cache.$$.tmp"
        printf '%s\n' "$PY" > "$py_tmp" && mv -f "$py_tmp" "$py_cache" || rm -f "$py_tmp"
      } 2>/dev/null || true
    fi
  fi
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
# (the session UUID verbatim, which IS the transcript filename under
# ~/.claude/projects/<slug>/).
# SID ENV NAME -- corrected 2026-07-25 after this field logged EMPTY in
# 5,439/5,439 lines for 15 days: the harness sets CLAUDE_CODE_SESSION_ID in
# hook processes, NOT CLAUDE_SESSION_ID. Verified two ways: the v2.1.220
# binary's hook-child env builder sets CLAUDE_CODE_SESSION_ID, and a live env
# dump from a hook process shows CLAUDE_SESSION_ID absent. Do NOT "restore" the
# old name -- the `${CLAUDE_SESSION_ID}` seen in skill/agent bodies is a
# DIFFERENT mechanism (plugin-loader substitution at content-LOAD time, see
# docs/frontmatter.md), is correct there, and never reaches a hook's env.
# The nested `:-` default keeps the legacy name as a harmless fallback should a
# future harness set it, and both `:-` layers keep `set -u` safe -- pure
# parameter expansion, no fork.
# CLAUDE_PROJECT_DIR is documented as set in hook processes
# (code.claude.com/docs/en/hooks -- Environment Variables); its `:-` fallback
# logs an empty field on older harness versions that don't set it.
# One-generation size rollover keeps the log bounded: once it exceeds ~5MB
# (years of fires at observed rates) the current file is moved to
# ballast-hook-fires.log.old (a single prior generation, not a numbered
# series) before the new line is appended -- an audit convenience, not a
# record of legal weight.
# $1 = rc, $2 = out tag (yes / no / skip-no-python).
ledger() {
  # Home resolution is shared with the interpreter cache via resolve_ballast_home() above -- see
  # that helper for the BALLAST_CLAUDE_HOME -> $HOME/.claude -> empty ladder and the set -u
  # rationale. An empty value means "no state dir": return early rather than writing anywhere.
  local dir="$BALLAST_HOME_DIR"
  [ -n "$dir" ] || return 0
  local log="$dir/ballast-hook-fires.log"
  local proj="${CLAUDE_PROJECT_DIR:-}"
  proj="${proj##*[/\\]}"   # basename via pure expansion -- no fork, strips both / and \ (Windows-native paths)
  { [ -d "$dir" ] || mkdir -p "$dir"     # [ -d ] is a builtin: no fork on the common already-exists path
    [ -f "$log" ] && [ "$(wc -c < "$log")" -gt 5242880 ] && mv -f "$log" "$log.old"
    printf '%s %s rc=%s out=%s proj=%s sid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$name" "$1" "$2" \
      "$proj" "${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}" >> "$log"; } 2>/dev/null || true
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
