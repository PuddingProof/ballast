#!/usr/bin/env bash
# Regression tests for git-commit-guard.sh -- the pre-commit review/safety hook.
#
# WHY committed: this hook's cross-OS word-boundary logic is subtle. GNU \b is zero-width and
# matches every word transition; the portable POSIX classes that replace it (for BSD/macOS) CONSUME
# a char and only matched space-or-start until an audit caught the gap. These cases pin both the
# behavior and the two fixes (F1 abutted-sequencer normalization, F2 no false hard block on the
# raw-payload fallback) so neither can silently regress.
#
# Payloads are DATA on stdin (never executed), so running this file does not trip the caller's own
# git hooks. Self-locating: finds git-commit-guard.sh one directory up (hooks/); runs wherever it's checked
# out. Exit code = number of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$DIR/../git-commit-guard.sh"

# Resolve a working Python 3 the same way the run.sh dispatcher does, and export it so the guard's
# JSON-emit / extraction path uses a real interpreter (never the Windows Store stub).
# The specific hazard: a Windows App-Execution-Alias stub (`.../WindowsApps/python3`) sits on
# PATH but does NOT merely exit non-zero when executed non-interactively -- it HANGS INDEFINITELY
# (the App Installer redirector blocks on a Microsoft Store UI a headless shell can never
# satisfy), wedging the caller instead of falling through. Two COMPLEMENTARY defenses: TIMEOUT
# (bounds every probe execution so a hang loses the race) and TWO-PASS path RESOLUTION (pass 1
# resolves each candidate to the first PATH hit that is NOT an alias and probes THAT absolute
# path -- resolving past the alias rather than skipping the whole candidate, since the common
# Windows PATH layout puts the alias AHEAD of a real install; pass 2 retries by bare name only if
# nothing real resolved). Where timeout is absent, resolution is what keeps the alias unexecuted.
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
      # (the "-3" of "py -3").
      case "$c" in *" "*) run="$real ${c#* }" ;; *) run="$real" ;; esac
    fi
    # shellcheck disable=SC2086 -- intentional word-split for "py -3" and the optional timeout.
    if $PROBE $run -c 'import sys' >/dev/null 2>&1; then PY="$run"; break 2; fi
  done
done
export BALLAST_PYTHON="$PY"

fails=0
tmperr="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/gcg.$$.err")"

# check <name> <payload> <expect_exit> <want_substr|-> <forbid_substr|->
check() {
  local name="$1" payload="$2" xexit="$3" want="$4" forbid="$5" out rc err blob ok=1
  out="$(printf '%s' "$payload" | bash "$GUARD" 2>"$tmperr")"; rc=$?
  err="$(cat "$tmperr")"; blob="$out$err"
  [ "$rc" = "$xexit" ] || ok=0
  [ "$want" != "-" ] && { case "$blob" in *"$want"*) ;; *) ok=0 ;; esac; }
  [ "$forbid" != "-" ] && { case "$blob" in *"$forbid"*) ok=0 ;; esac; }
  if [ "$ok" = 1 ]; then printf 'PASS  %-38s rc=%s\n' "$name" "$rc"
  else printf 'FAIL  %-38s rc=%s (want %s)\n      out=%s\n      err=%s\n' "$name" "$rc" "$xexit" "$out" "$err"; fails=$((fails+1)); fi
}
P()  { printf '{"tool_name":"Bash","tool_input":{"command":"%s"}}' "$1"; }
PD() { printf '{"tool_name":"Bash","tool_input":{"command":"%s","description":"%s"}}' "$1" "$2"; }

# --- core behavior ---
check "benign ls"              "$(P 'ls -la')"                        0 -                       REMINDER
check "git status only"        "$(P 'git status')"                    0 -                       REMINDER
check "git diff -> nudge"      "$(P 'git diff')"                      0 "review pass"           -
check "nudge names code-review skill" "$(P 'git diff')"               0 "ballast:code-review"    -
check "nudge carries systemMessage" "$(P 'git diff')"                 0 "systemMessage"          -
check "git log -> nudge"       "$(P 'git log --oneline')"             0 "review pass"           -
check "pager-flag diff (norm)" "$(P 'git -c core.pager=cat diff')"    0 "review pass"           -
check "digit is not git"       "$(P 'echo digit -c foo diff')"        0 -                       REMINDER
check "git pushx not push"     "$(P 'git pushx')"                     0 -                       REMINDER
check "push -> off-site"       "$(P 'git push origin main')"          0 "off-site"              -
check "amend -> caution"       "$(P 'git commit --amend')"            0 "amend or reset"        -
check "bulk add -> caution"    "$(P 'git add -A')"                    0 "bulk staging"          -
check "reset --hard caution"   "$(P 'git reset --hard HEAD~1')"       0 "amend or reset"        -
check "standalone commit"      "$(P 'git commit -m fix')"            0 -                       REMINDER
check "integration prose"      "$(P 'git diff')"                      0 "integration-gate skill" "Skill("

# --- hard block (exit 2) ---
check "HARD add&&commit"       "$(P 'git add -A && git commit -m x')" 2 "BLOCKED (compound"     -
check "HARD cd&&commit"        "$(P 'cd foo && git commit -m x')"     2 "BLOCKED (compound"     -
check "benign compound flows"  "$(P 'git status && git diff')"        0 "review pass"           BLOCKED

# --- F1 regression: sequencer abutted with NO space + wedged global flag must still hard-block ---
check "F1 abutted &&git flag"  "$(P 'true&&git --no-pager commit -m x')" 2 "BLOCKED (compound"  -
check "F1 abutted ;git -c"     "$(P 'x;git -c core.pager=cat commit')"   2 "BLOCKED (compound"  -

# --- F2 regression: empty command + a description mentioning a compound git op must NOT hard-block ---
check "F2 empty cmd + desc"    "$(PD '' 'cleanup and git commit -m x')"  0 -                    BLOCKED

# --- F4 regression: `git add .dotpath` (leading-dot file/dir) must NOT match bulk-`git add .` ---
# The bulk-add pattern is `\.` followed by space-or-end, so it matches the literal current-dir
# arg (`git add .`) but NOT a dotfile/dotdir path (`git add .gitignore`, `git add .github/x`).
check "F4 dotdir add no block" "$(P 'cd x && git add .github/ci.yml')"   0 -                    BLOCKED
check "F4 literal . blocks"    "$(P 'cd x && git add .')"                 2 "BLOCKED (compound"  -
check "F4 dotfile no bulk-msg" "$(P 'git add .gitignore')"               0 -                    "bulk staging"
check "F4 literal . bulk-msg"  "$(P 'git add .')"                        0 "bulk staging"        -

# --- B4-1 regression: -m/--message argument VALUES are excluded from the compound-sequencer scan,
# so a commit message that merely quotes "&&"/";" text doesn't false-block, while a REAL chain
# outside the quotes (not part of the message value) still hard-blocks. ---
check "msg quotes && no block" "$(P 'git commit -m \"docs: mention && git push reminder\"')" 0 -                   BLOCKED
check "add&&commit -m still blk" "$(P 'git add x && git commit -m \"y\"')"                    2 "BLOCKED (compound" -
check "msg quotes ; no block"  "$(P 'git commit -m \"a; git push b\"')"                        0 -                   BLOCKED
check "real chain after -m blk" "$(P 'git commit -m \"x\" && git push')"                       2 "BLOCKED (compound" -

# --- C1b regression: BALLAST_SIDECAR_REVIEW=1 (inherited from bin/ballast-review by the sidecar
# session) silently suppresses the review nudge -- the reviewer must not be told to review itself.
# Suppression is SCOPED to the nudges: the exit-2 compound hard block must stay live in sidecars
# (it helps enforce the sidecar's read-only intent). NOTE these cases stay LAST in the file: a
# `VAR=1 fn` assignment prefix on a shell FUNCTION persists after the call (POSIX), so the var
# leaks into any later check -- keep sidecar-var cases at the tail. ---
BALLAST_SIDECAR_REVIEW=1 check "sidecar suppresses nudge" "$(P 'git diff')" 0 - "REMINDER"
BALLAST_SIDECAR_REVIEW=1 check "sidecar keeps hard block" "$(P 'git add -A && git commit -m x')" 2 "BLOCKED (compound" -

rm -f "$tmperr" 2>/dev/null
echo
[ "$fails" = 0 ] && echo "ALL git-commit-guard TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
