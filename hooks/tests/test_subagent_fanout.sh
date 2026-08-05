#!/usr/bin/env bash
# Regression tests for subagent-fanout.sh -- the UserPromptSubmit sub-agent
# fanout model-tier-calibration hook.
#
# WHY committed: the hook deliberately scans the RAW stdin payload (no jq
# dependency assumed, so it never parses out .prompt) with a word-boundary
# keyword regex tuned to avoid the IDE-opened-file / path-component footgun
# (see the hook's own header). The 2026-07-10 notification-shell demotion
# rework additionally skips the keyword grep entirely on harness-generated
# turn shells (task-notification / background-resume replays) BEFORE it ever
# runs, mirroring freehand-mode.sh's demotion -- live instance session
# 57b05b62 showed two of three <task-notification> turns re-injecting
# the tier table because the raw payload's keyword grep matched vocabulary
# INSIDE an agent's returned report, not typed user intent. These cases pin
# the keyword boundary rule, the hyphen/space keyword-form matching, and the
# new notification-shell demotion, so none of them can silently regress.
#
# Payloads are DATA on stdin, so running this file never trips the caller's
# own hooks. Self-locating: finds subagent-fanout.sh one directory up (hooks/); runs
# wherever it's checked out. Exit code = # of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/../subagent-fanout.sh"

# subagent-fanout.sh never shells out to python (pure bash + grep on the raw
# payload) -- resolving/exporting BALLAST_PYTHON here is a no-op for this
# hook's own logic, but mirrors this dir's other suites' convention (and the
# same probe loop as run.sh) so the harness stays consistent if the hook ever
# grows a python-shelling step.
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

# check <name> <payload> <want_fire: yes|no> <want_substr-if-fired|->
check() {
  local name="$1" payload="$2" want_fire="$3" want="$4" out rc ok=1 fired=no
  out="$(printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) fired=yes ;; esac
  [ "$fired" = "$want_fire" ] || ok=0
  [ "$want" != "-" ] && { case "$out" in *"$want"*) ;; *) ok=0 ;; esac; }
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s fired=%s\n' "$name" "$rc" "$fired"
  else printf 'FAIL  %-42s rc=%s fired=%s (want fired=%s)\n      out=%s\n' "$name" "$rc" "$fired" "$want_fire" "$out"; fails=$((fails+1)); fi
}

# (1) genuine keyword in prose -> fires, tier-calibration text present.
check "genuine keyword fires"          '{"prompt":"run a code-review on this diff"}' \
                                        yes "tier calibration"

# (2) keyword only as a path/filename component -> boundary rule rejects it.
check "keyword in path, no fire"       '{"prompt":"open skills/code-review/SKILL.md"}' \
                                        no -

# (3) realistic task-notification payload: prompt field STARTS WITH
#     <task-notification> and the body text (agent's own report) contains
#     both "audit" and "subagent" -- must NOT fire even though both keywords
#     are textually present, because the notification-shell demotion exits
#     before the keyword grep ever runs.
check "task-notification shell, no fire" '{"prompt":"<task-notification>Completed the requested audit. Dispatched 3 subagent leaves to cover the review lenses.</task-notification>"}' \
                                        no -

# (4) [SYSTEM NOTIFICATION - NOT USER INPUT] shell containing a keyword ->
#     same demotion, different marker string.
check "system-notification shell, no fire" '{"prompt":"[SYSTEM NOTIFICATION - NOT USER INPUT]\nBackground audit task finished."}' \
                                        no -

# (5) keyword-free prompt -> no fire, no output at all.
check "keyword-free, no fire"          '{"prompt":"please fix the typo on line 12"}' \
                                        no -

# (6) hyphenated/spaced keyword forms -> [ -]? in the regex still matches.
check "spaced keyword form fires"      '{"prompt":"fan out the subagents to cover this"}' \
                                        yes "tier calibration"

# (7) tier-neutral wording (2026-07-28 visual-stack redesign): the injected text must not name
# visual-reviewer as THE visual check -- the ladder (glance by default) picks the rung.
check "tier-neutral visual wording"    '{"prompt":"run a code-review on this diff"}' \
                                        yes "visual review dispatch"

# --- SESSION COOLDOWN (2h TTL marker): the ~5KB table is static, so one injection per session
# covers a run of keyword prompts; a stale marker (older than the TTL) re-arms, which is what
# keeps a long or post-compact session covered. HERMETIC: BALLAST_CLAUDE_HOME redirects the marker
# dir to a throwaway temp home. The sid is grepped from the RAW payload, so a payload WITHOUT a
# session_id has no cooldown at all and fires every time (fail open) -- which is exactly why every
# case above fires unconditionally. ---
HERMETIC_HOME="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/fanout-home.$$")"
mkdir -p "$HERMETIC_HOME"
export BALLAST_CLAUDE_HOME="$HERMETIC_HOME"
FANOUT_MARKERS="$HERMETIC_HOME/.cache/ballast-fanout"

check "cooldown: first keyword fires"  '{"session_id":"fo1","prompt":"run a code-review on this diff"}' \
                                        yes "tier calibration"
if [ -f "$FANOUT_MARKERS/fo1" ]; then printf 'PASS  %-42s marker=exists\n' "cooldown: marker written"
else printf 'FAIL  %-42s marker=absent (want exists)\n' "cooldown: marker written"; fails=$((fails+1)); fi

check "cooldown: second keyword silent" '{"session_id":"fo1","prompt":"another audit please"}' \
                                        no -
check "cooldown: other session fires"  '{"session_id":"fo2","prompt":"another audit please"}' \
                                        yes "tier calibration"

# Age the marker past the 2h TTL -- a stale marker must re-arm the injection. Backdated via
# python's os.utime, NOT `touch -d`/`touch -r`: the relative-date forms are GNU-only, so on
# BSD/macOS they would silently no-op and turn this into a false PASS.
# shellcheck disable=SC2086 -- intentional word-split for a two-word "py -3".
if [ -n "$PY" ]; then
  $PY -c "import os,time,sys; t=time.time()-3*3600; os.utime(sys.argv[1],(t,t))" "$FANOUT_MARKERS/fo1"
  check "cooldown: stale marker re-arms" '{"session_id":"fo1","prompt":"another audit please"}' \
                                          yes "tier calibration"
else
  printf 'SKIP  %-42s (no python to backdate the marker)\n' "cooldown: stale marker re-arms"
fi

# FAIL OPEN: no extractable sid -> no cooldown, every keyword prompt fires.
check "cooldown: no sid fires again"   '{"prompt":"run a code-review on this diff"}' \
                                        yes "tier calibration"
check "cooldown: no sid fires twice"   '{"prompt":"run a code-review on this diff"}' \
                                        yes "tier calibration"

rm -rf "$HERMETIC_HOME" 2>/dev/null

echo
[ "$fails" = 0 ] && echo "ALL subagent-fanout TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
