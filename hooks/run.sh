#!/usr/bin/env bash
# hooks/run.sh -- ballast's single dispatcher for every registered Claude Code hook.
#
# WHY a dispatcher: hooks.json's command string is fixed at plugin-load time and can't branch on
# OS or probe for a working Python. One script gives one place to resolve an interpreter, fail
# open, and record the fire ledger. Usage: `bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" <hook-name>`;
# stdin (the hook JSON payload) is never read or buffered here -- the child inherits the same fd.
#
# DISPATCH: the child is CAPTURED (command substitution), not exec'd, so stdout can be forwarded
# unchanged AND the exit code inspected for the ledger; stderr passes straight through. The ledger
# write is a pure side effect -- it never alters what is forwarded or the exit code.
#
# Fail-open contract: ANY unresolvable state here (unknown hook name, no working Python for a .py
# hook) exits 0 silently -- a guard that bricks a session on an environment quirk is worse than
# one that occasionally no-ops.

set -u

# MSYS/Git-Bash hardening: an ancestor's exported MSYS_NO_PATHCONV / MSYS2_ARG_CONV_EXCL disables
# the argv path conversion the python dispatch relies on, erroring every hook -- and PreToolUse
# hook errors fail CLOSED in Claude Code, violating the fail-open contract. Hooks must never
# depend on a parent's MSYS conversion settings -- restore defaults.
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

# Own directory via BASH_SOURCE -- independent of caller cwd and of hooks.json's path substitution.
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
  process-lifecycle-guard)          file="process-lifecycle-guard.py" ;;
  origin-sweep)                     file="origin-sweep.py" ;;
  visual-arm)                       file="visual-arm.py" ;;
  *)
    # Unknown hook name -- fail open silently (see contract above).
    exit 0
    ;;
esac

target="$DIR/$file"

# Ballast's per-user state dir, resolved ONCE for both consumers (interpreter cache, fire ledger)
# so they can never drift: BALLAST_CLAUDE_HOME (hermetic-test override) -> $HOME/.claude -> empty
# = "no state dir", every caller skips silently (under `set -u` a bare $HOME would ABORT the
# script and eat the child's exit code, e.g. turning an exit-2 block into a dead guard).
# NEVER the plugin dir: replaced wholesale on plugin update. Sets a global rather than printing:
# a `$(...)` capture would fork a subshell on EVERY fire, defeating the fork-cutting below.
BALLAST_HOME_DIR=""
resolve_ballast_home() {
  BALLAST_HOME_DIR="${BALLAST_CLAUDE_HOME:-}"
  [ -z "$BALLAST_HOME_DIR" ] && BALLAST_HOME_DIR="${HOME:+$HOME/.claude}"
  return 0   # the [ -z ] test above is the last command; without this a set home would "fail"
}
resolve_ballast_home

# Which hooks need a Python interpreter: every .py hook, plus the .sh hooks that shell out to
# Python internally -- a new Python-shelling .sh hook MUST join the case below, or it silently
# falls back to bare `python` (on Windows possibly the Microsoft-Store stub) and degrades to its
# own lesser fallback. Resolve ONCE and EXPORT so .sh hooks reuse the SAME verified interpreter;
# the resolution must stay BEFORE the .sh/.py dispatch split (confined to the *.py branch, the
# .sh hooks never receive it). Hooks needing no Python skip the probe entirely.
needs_python=0
case "$file" in *.py) needs_python=1 ;; esac
case "$name" in git-commit-guard|ballast-principles|freehand-mode|harness-sweep-nudge|plan-handoff|mode-state-cleanup) needs_python=1 ;; esac

if [ "$needs_python" -eq 1 ]; then
  # ===== THREE-TIER INTERPRETER RESOLUTION (fast paths in front of the cold probe) ==============
  # The cold probe is expensive (up to three candidate executions plus per-candidate forks,
  # re-derived identically four times per Bash tool call under the shared Bash|PowerShell
  # matcher) -- cache the constant instead of re-deriving it. Tiers, cheapest first: (1) an
  # interpreter already exported into this process tree, (2) the on-disk cache written by a
  # previous cold probe, (3) the cold probe itself.
  #
  # VALIDITY TEST IS `[ -x ]` ONLY -- no TTL/stat/date: it is a fork-free builtin; the cache only
  # ever holds a probe-VERIFIED interpreter (written after `-c "import sys"` succeeds, so a Store
  # alias-stub can never be cached); and a moved/uninstalled interpreter SELF-HEALS by failing
  # `[ -x ]` into the cold probe. ACCEPTED RESIDUAL: an interpreter BROKEN IN PLACE at its cached
  # path keeps being served (recovery: delete <ballast-home>/ballast-python) -- do not "fix" this
  # with a TTL or re-probe.
  # FAIL OPEN THROUGHOUT: every cache read/write is best-effort and degrades to the cold probe.
  # TRUST BOUNDARY: the cached value is EXECUTED, but the same ~/.claude tree already holds
  # settings.json (which can name arbitrary hook commands), so the cache adds no new reach -- it
  # just must stay in ballast's own user-home state dir, never anywhere world-writable.
  PY=""

  # --- TIER 1: inherited env (a parent in this process tree already resolved one). Test the
  # FIRST WORD only -- BALLAST_PYTHON may carry args ("<path>/py.exe -3").
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

  # --- TIER 3: the cold probe. Only reached when neither fast path yielded a still-executable
  # interpreter; its result is written back to the cache below.
  if [ -z "$PY" ]; then
  # Resolve a WORKING Python 3, not just a Python-shaped name on PATH: Windows ships a
  # `python3`/`python` App-Execution-Alias that resolves on PATH but is no interpreter -- and
  # executed non-interactively it HANGS INDEFINITELY (waits on a Store UI), not merely exiting
  # non-zero, wedging every fire and making the fail-open path below unreachable. Two
  # COMPLEMENTARY defenses:
  #   1. TIMEOUT -- bound every probe run so a hanging candidate loses the race. `timeout` is
  #      coreutils (absent on stock macOS); where absent, defense 2 keeps the alias unexecuted.
  #   2. TWO-PASS path RESOLUTION -- pass 1 probes the first PATH hit that is NOT an alias
  #      (resolving, not skipping: the alias commonly sits AHEAD of a real install); pass 2
  #      retries bare names so a genuine Store-installed Python still works.
  # PROBE BOUND vs HOOK BUDGET: the bound must sit strictly BELOW this dispatcher group's
  # hooks.json timeout (5s for Bash|PowerShell) -- at an equal bound one hanging candidate eats
  # the entire budget before the child is even dispatched. A real interpreter starts in well
  # under 700ms even saturated, so 2s keeps headroom; re-check if the hooks.json timeout drops.
  if command -v timeout >/dev/null 2>&1; then PROBE="timeout 2"; else PROBE=""; fi
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
    # Cache the probe result so the NEXT fire takes tier 2. ATOMIC: temp file in the SAME
    # directory (same filesystem, `mv` = rename), then rename over the target -- a concurrent
    # fire reads old or new, never half-written; `$$` scopes the temp name so parallel fires
    # cannot collide. Only a SUCCESSFUL probe is ever written: tier 2's `[ -x ]` cannot tell a
    # Store stub from a real interpreter -- the probe-verified-only invariant is what makes that
    # cheap test safe. FAIL OPEN: the whole block is best-effort; an unwritable home degrades to
    # "probe every time", never aborts dispatch.
    # PATH-VALUED RESULTS ONLY: the allow-stubs pass yields BARE names, which `[ -x ]` rejects on
    # every read -- caching one would mean cold probe PLUS a pointless write every fire, strictly
    # worse than no cache. Deliberately NOT solved by letting tier 2 accept names via
    # `command -v`: PATH can change between fires, so a name that probed clean once could later
    # resolve to the hanging Store alias.
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
# non-zero exit) at <ledger-home>/ballast-hook-fires.log. Hooks fire silently
# in the product UI unless they emit systemMessage; the ledger is the
# retrospective record silent-fire audits and postmortems grep. Never logs
# non-fires; any logging failure is swallowed -- the ledger must never affect
# hook behavior or exit codes. Home resolves via resolve_ballast_home() above.
# Two trailing fields ride at the END of each line (so rc=/out= greppers keep
# working): proj= (basename of CLAUDE_PROJECT_DIR via pure parameter expansion,
# stripping both / and \) and sid= (the session UUID, which IS the transcript
# filename under ~/.claude/projects/<slug>/).
# SID ENV NAME: hook processes get CLAUDE_CODE_SESSION_ID, NOT
# CLAUDE_SESSION_ID -- do NOT "restore" the old name; `${CLAUDE_SESSION_ID}` in
# skill/agent bodies is a DIFFERENT mechanism (plugin-loader substitution at
# content-LOAD time) and never reaches a hook's env. The nested `:-` default
# keeps the legacy name as a harmless fallback, `set -u`-safe, no fork.
# One-generation size rollover (~5MB -> .log.old) keeps the log bounded.
# $1 = rc, $2 = out tag (yes / no / skip-no-python).
ledger() {
  # Shared home resolution (see resolve_ballast_home); empty = no state dir, return early.
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
