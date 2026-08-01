#!/usr/bin/env bash
# Regression tests for freehand-mode.sh -- the UserPromptSubmit delegated-autonomy SENSOR hook.
#
# WHY committed: the v0.8.0 redesign made this hook a keyword SENSOR + state MIRROR, not the mode's
# contract (the contract moved to skills/freehand/SKILL.md, its single canonical home). Several
# regimes must not silently regress:
#   - ARM: a boundary-qualified keyword in the extracted `.prompt` injects a SHORT, STATIC
#     adjudication stub (same text regardless of parse success or session_id -- the hook no longer
#     writes any state on this path). The stub stays high-recall and arms on a MENTION too -- it
#     opens with an ADJUDICATE FIRST gate and routes a GENUINE grant to the freehand skill, so
#     precision is the model's call, not the regex's. The stub carries none of the mode's rules
#     (adoption now needs a skill invocation -- a second gate).
#   - MIRROR: on a NON-arming prompt, a CONFIRMED freehand/autopilot entry in the per-session state
#     file re-injects a one-line MODE MIRROR reminder (compact-survival). Sidless and fallback
#     payloads never mirror; a keyword arm always takes precedence over a mirror.
#   - FALLBACK: python unavailable / JSON parse failure -> raw-payload scan (today's old behavior).
#   - DEMOTION: harness-generated turn shells (task-notifications, background-resume replays) skip
#     BOTH the arm and the mirror.
# These cases pin all of the above plus the pre-existing path/identifier boundary rule. The arm path
# writes no state file at all (a prior revision's pending-chip mechanism was dropped as unwarranted
# complexity -- the confirmed statusline chip is raised by the freehand SKILL, not this hook).
#
# Payloads are DATA on stdin, so running this file never trips the caller's own hooks. Self-locating:
# finds freehand-mode.sh one directory up (hooks/); runs wherever it's checked out. Exit code = # of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/../freehand-mode.sh"
# The relocated contract now lives in the freehand skill body -- the (s2) cases grep it directly.
SKILL="$DIR/../../skills/freehand/SKILL.md"

# Resolve a working Python 3 the same way the run.sh dispatcher does, and export it so the hook's
# JSON-extraction path uses a real interpreter (never a Windows Store stub). run.sh's own needs_python
# case list DOES include freehand-mode (run.sh line ~69); this harness resolves + exports BALLAST_PYTHON
# anyway so the primary (python-available) path is exercised deterministically across machines,
# independent of whatever caller-side export state a given run.sh invocation happens to have.
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

# check <name> <payload> <want_arm: yes|no> <want_substr-if-armed|->
check() {
  local name="$1" payload="$2" want_arm="$3" want="$4" out rc ok=1 armed=no
  out="$(printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) armed=yes ;; esac
  [ "$armed" = "$want_arm" ] || ok=0
  [ "$want" != "-" ] && { case "$out" in *"$want"*) ;; *) ok=0 ;; esac; }
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s armed=%s\n' "$name" "$rc" "$armed"
  else printf 'FAIL  %-42s rc=%s armed=%s (want armed=%s)\n      out=%s\n' "$name" "$rc" "$armed" "$want_arm" "$out"; fails=$((fails+1)); fi
}

# checknostate <name> <payload> <want_arm: yes|no> <want_ctx_substr|-> <not_want_substr|->
# Same as `check`, but ALSO asserts the whole <home>/ballast/modes/ dir stays empty (zero files of
# any name) under an isolated BALLAST_CLAUDE_HOME -- proves the arm path writes no state, on any
# payload shape (clean-parse, malformed, demoted). Optional 5th param asserts a substring is ABSENT
# from the output (e.g. proving the stub carries no "ballast-mode" instruction).
checknostate() {
  local name="$1" payload="$2" want_arm="$3" want_ctx="$4" not_want="${5:--}"
  local home out rc ok=1 armed=no modesdir nfiles=0 f
  home="$(mktemp -d)"
  out="$(export BALLAST_CLAUDE_HOME="$home"; printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) armed=yes ;; esac
  [ "$armed" = "$want_arm" ] || ok=0
  [ "$want_ctx" != "-" ] && { case "$out" in *"$want_ctx"*) ;; *) ok=0 ;; esac; }
  # "-" is the same "don't care" sentinel `check` uses for its substring params (never a real
  # substring to assert absent -- if that were ever needed, note it can't reuse "-").
  [ "$not_want" != "-" ] && { case "$out" in *"$not_want"*) ok=0 ;; esac; }
  modesdir="$home/ballast/modes"
  if [ -d "$modesdir" ]; then
    for f in "$modesdir"/*; do [ -e "$f" ] && nfiles=$((nfiles+1)); done
  fi
  [ "$nfiles" -eq 0 ] || ok=0
  rm -rf "$home"
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s armed=%s files=%s\n' "$name" "$rc" "$armed" "$nfiles"
  else printf 'FAIL  %-42s rc=%s armed=%s files=%s (want armed=%s, 0 files)\n      out=%s\n' \
              "$name" "$rc" "$armed" "$nfiles" "$want_arm" "$out"; fails=$((fails+1)); fi
}

# (a) keyword typed in .prompt -> arms.
check "(a) typed keyword arms"       '{"prompt":"autopilot on, take it from here"}' \
                                  yes "FREEHAND / AUTOPILOT"

# (b) the only keyword sits OUTSIDE .prompt (an ide_opened_file-style field naming this hook, plus
#     an echoed-report-style field quoting "autopilot") -> must NOT arm now that the match is scoped
#     to .prompt (this is the exact false-arm class the precision-model rework fixes).
check "(b) keyword outside .prompt"  '{"prompt":"please check the report","ide_opened_file":"hooks/freehand-mode.sh","prior_report":"session had autopilot mode enabled"}' \
                                  no -

# (c) keyword typed INSIDE quoted sample text within .prompt -> arms (documented accepted residual).
check "(c) quoted-sample residual"   "{\"prompt\":\"my old prompts said 'autopilot ultracode go'\"}" \
                                  yes "FREEHAND / AUTOPILOT"

# (d) no keyword anywhere -> no output at all. This payload ALSO carries no session_id, so it lands
#     in the standing-grant mirror's else branch with an empty sid -> no mirror either (the no-sid ->
#     no-mirror rule; a mirror needs a per-session state file to read, which needs a sid).
check "(d) no keyword, no output"    '{"prompt":"normal request"}' \
                                  no -

# (e) malformed / non-JSON payload, keyword present as prose -> python parse fails, hook falls back
#     to the raw-payload grep (today's old behavior) -> arms.
check "(e) malformed JSON fallback"  '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                  yes "FREEHAND / AUTOPILOT"

# (f) keyword embedded in a path INSIDE .prompt -> boundary rule still rejects it (pre-existing
#     path/identifier exclusion, unaffected by the .prompt-scoping rework).
check "(f) keyword in path, no arm"  '{"prompt":"see path/autopilot/x for details"}' \
                                  no -

# (g) mention-form prompt (talking ABOUT the hook/mode, not invoking it) -> the regex still arms
#     mechanically (want_arm=yes) because it cannot distinguish use from mention; the injected stub
#     must contain the ADJUDICATE FIRST gate that makes this mechanical arm safe (the model is
#     expected to disregard the block on read). Pins the use-vs-mention design.
check "(g) mention-form still arms"  '{"prompt":"the freehand hook is misfiring again, please fix it"}' \
                                  yes "ADJUDICATE FIRST"

# (h) armed output must carry the visible systemMessage fire indicator (reuses payload (a)).
check "(h) systemMessage present"    '{"prompt":"autopilot on, take it from here"}' \
                                  yes "\"systemMessage\""

# (i) notification-shell payload (harness-generated turn, not a typed grant) -> must NOT arm even
#     though the keyword appears in .prompt. Harness-generated turn shells are never a typed grant.
check "(i) notification-shell, no arm" '{"prompt":"[SYSTEM NOTIFICATION - NOT USER INPUT]\nTask finished. The session had autopilot mode granted earlier."}' \
                                  no -

# (j) FALLBACK-PATH notification-shell demotion: malformed JSON (same python-unavailable-shaped
#     path as case (e)) whose raw payload ALSO contains a notification-shell marker plus the
#     keyword -> the demotion is now matched against $match_target (the raw payload in this
#     fallback), so this must NOT arm even though case (e) proves the fallback path arms on a
#     plain keyword. This is the case the 2026-07-10 rework (moving the demotion case statement
#     after the if/else, from $prompt-only to $match_target) exists to cover.
check "(j) fallback: notification-shell, no arm" '{prompt: this is not valid json, [SYSTEM NOTIFICATION - NOT USER INPUT] the session had autopilot mode granted earlier}' \
                                  no -

# (k) control for (j): the fallback path itself is not dead -- a plain malformed-JSON payload with
#     a typed-looking keyword and NO notification marker still arms (this duplicates case (e)'s
#     assertion deliberately, so this file's fallback-path coverage is self-contained and doesn't
#     rely on reading case (e) to know the fallback still works).
check "(k) fallback: plain keyword still arms" '{prompt: this is not valid json, but the user wants freehand mode enabled}' \
                                  yes "FREEHAND / AUTOPILOT"

# =================================================================================================
# NO-STATE coverage: the arm path must leave zero trace in ballast/modes/ regardless of payload
# shape -- clean-parse with a session_id, malformed/fallback, or demoted. Each case runs against an
# isolated `mktemp -d` BALLAST_CLAUDE_HOME (never the real ~/.claude) so a regression that
# re-introduces a state write anywhere on this path is caught here.

# (l) clean-parse arm with a session_id present writes no state file.
checknostate "(l) clean-parse arm writes no state"   '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          yes "FREEHAND / AUTOPILOT"

# (m) no-match payload writes no state file at all -- AND, since it also carries a session_id but no
#     keyword and no confirmed state, produces no mirror line either (an empty modes/ dir here means
#     there was never a confirmed entry to mirror).
checknostate "(m) no-match writes no state"  '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          no -

# (n) demoted notification-shell payload (harness-generated turn) writes no state file, even though
#     session_id IS present and the keyword IS in .prompt -- the demotion exit happens before both
#     the arm branch and the mirror else are ever reached.
checknostate "(n) notification-shell writes no state" \
                                          '{"session_id":"abc123def456","prompt":"[SYSTEM NOTIFICATION - NOT USER INPUT]\nTask finished. The session had autopilot mode granted earlier."}' \
                                          no -

# (o) fallback path (malformed JSON, same shape as case (e)/(k)) emits the static stub JSON --
#     no state file, and no "ballast-mode" instruction string anywhere in the output (the stub
#     never asks the model to write or clear any per-session state).
checknostate "(o) fallback: stub, no state, no ballast-mode string" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "FREEHAND / AUTOPILOT" "ballast-mode"

# (p) SHARED-GATE-SENTENCE GUARD: both a clean-parse arm and a fallback arm carry the same
#     ADJUDICATE FIRST gate sentence -- pins that the (now single-copy) stub text renders it
#     identically regardless of parse path. Pure content assertions (no state-file claim), so
#     these use `check` rather than `checknostate` -- (l)/(m)/(n)/(o) already pin the no-state-
#     write property once; re-deriving it for every content-only case would be redundant.
check "(p) gate sentence: clean-parse" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                  yes "ADJUDICATE FIRST"
check "(p) gate sentence: fallback" \
                                  '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                  yes "ADJUDICATE FIRST"

# (q) CANONICAL HOME: the contract's non-delegable approval-floor sentence and the "QUALITY IS
#     NOT DELEGATED" bullet live in the skill body -- the contract's single canonical home, not the
#     hook. These checks pin their PRESENCE there by grepping skills/freehand/SKILL.md directly, so
#     an edit that drops or relocates either out of the skill is caught here.
checkfile() {
  local name="$1" file="$2" want="$3" ok=1
  if [ -f "$file" ] && grep -qF -- "$want" "$file"; then :; else ok=0; fi
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s\n' "$name"
  else printf 'FAIL  %-42s (want "%s" in %s)\n' "$name" "$want" "$file"; fails=$((fails+1)); fi
}
checkfile "(q) non-delegable relocated to SKILL.md"      "$SKILL" "non-delegable"
checkfile "(q) QUALITY-NOT-DELEGATED in SKILL.md"        "$SKILL" "QUALITY IS NOT DELEGATED"

# (r) INVERSE of (q): the relocated contract text must NOT appear in the hook's injected stub.
#     Pins that the relocation actually removed the text from the hook (not merely copied it into
#     the skill).
checknostate "(r) stub has no non-delegable" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          yes - "non-delegable"

# (s) the "do NOT adopt" gate phrase + the "freehand skill" routing + the "argument `off`" revoke
#     bullet must appear on the stub -- pins that the second-adoption-gate wording and the revoke
#     route render regardless of parse path. Short stable marker phrases (fragments of the
#     byte-pinned do-not-adopt sentence and the revoke bullet) so a wording tweak elsewhere in the
#     paragraph doesn't spuriously trip these while the marker itself is intact. Pure content
#     assertions, same rationale as (p) -- `check`, not `checknostate`.
check "(s) clean-parse: do-NOT-adopt" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                  yes "do NOT adopt"
check "(s) fallback: do-NOT-adopt" \
                                  '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                  yes "do NOT adopt"
check "(s) fallback: routes to freehand skill" \
                                  '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                  yes "freehand skill"
check "(s) clean-parse: revoke bullet" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                  yes 'argument `off`'
check "(s) fallback: revoke bullet" \
                                  '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes 'argument `off`'

# =================================================================================================
# STANDING-GRANT MIRROR coverage (v0.8.0): on a NON-arming prompt with a cleanly-parsed payload +
# session_id, a CONFIRMED freehand/autopilot entry in the per-session state file re-injects a
# one-line MODE MIRROR reminder (compact-survival). checkmirror seeds an isolated state file and
# asserts the mirror's fire/no-fire + substring contract.

# checkmirror <name> <state-content|-> <payload> <want_out: yes|no> <want_substr|-> <not_want_substr|->
# Seeds <home>/ballast/modes/abc123def456 with <state-content> (skip when "-"), runs the hook
# against that isolated BALLAST_CLAUDE_HOME, and asserts: exit 0, whether ANY output was produced
# (want_out), an optional present-substring, and an optional absent-substring. Used both for mirror
# fire cases and for its precedence / no-mirror rules (keyword-armed, malformed, sidless).
checkmirror() {
  local name="$1" state="$2" payload="$3" want_out="$4" want="$5" not_want="${6:--}"
  local home out rc ok=1 have_out=no
  home="$(mktemp -d)"
  if [ "$state" != "-" ]; then
    mkdir -p "$home/ballast/modes"
    printf '%s' "$state" > "$home/ballast/modes/abc123def456"
  fi
  out="$(export BALLAST_CLAUDE_HOME="$home"; printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  [ -n "$out" ] && have_out=yes
  [ "$have_out" = "$want_out" ] || ok=0
  [ "$want" != "-" ] && { case "$out" in *"$want"*) ;; *) ok=0 ;; esac; }
  [ "$not_want" != "-" ] && { case "$out" in *"$not_want"*) ok=0 ;; esac; }
  rm -rf "$home"
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s out=%s\n' "$name" "$rc" "$have_out"
  else printf 'FAIL  %-42s rc=%s out=%s (want out=%s)\n      out=%s\n' "$name" "$rc" "$have_out" "$want_out" "$out"; fails=$((fails+1)); fi
}

# (u) confirmed state + keywordless prompt -> mirror fires; the line names the mode and opens with
#     the MODE MIRROR marker. Because checkmirror seeds under an isolated BALLAST_CLAUDE_HOME, this
#     also proves the mirror honors that env override (never reads the real ~/.claude).
checkmirror "(u) confirmed -> mirror fires" "freehand confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          yes "MODE MIRROR — freehand ON" -

# (v) confirmed state BUT the prompt arms a keyword -> the keyword ARM wins (structural precedence:
#     mirror lives in the else branch), so the output is the adjudication stub, NOT a mirror line.
checkmirror "(v) confirmed+keyword -> arm not mirror" "freehand confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          yes "ADJUDICATE FIRST" "MODE MIRROR"

# (w) two no-mirror edge cases with a seeded confirmed file present:
#     - malformed payload WITH a keyword -> arms the fallback stub (py_rc!=0 -> no sid ->
#       fallback never mirrors); output is the stub, never a MODE MIRROR line.
checkmirror "(w) malformed+seeded -> stub, no mirror" "freehand confirmed 1750000000"$'\n' \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "FREEHAND / AUTOPILOT" "MODE MIRROR"
#     - clean payload with NO session_id and no keyword -> else branch, but empty sid -> no state
#       file is read (the seeded file is keyed on abc123def456, which this payload never names) -> no
#       output at all.
checkmirror "(w) sidless+seeded -> no output" "freehand confirmed 1750000000"$'\n' \
                                          '{"prompt":"normal request"}' \
                                          no - -

# (x) the mirror line carries NO systemMessage (silent by design -- high-frequency fire path;
#     visibility rides on the statusline chip + the run.sh ledger) AND still parses as well-formed
#     JSON.
echo "--- (x) mirror is systemMessage-less + valid JSON ---"
_xhome="$(mktemp -d)"
mkdir -p "$_xhome/ballast/modes"
printf 'freehand confirmed 1750000000\n' > "$_xhome/ballast/modes/abc123def456"
_xout="$(export BALLAST_CLAUDE_HOME="$_xhome"; printf '%s' '{"session_id":"abc123def456","prompt":"normal request"}' | bash "$HOOK")"; _xrc=$?
_xok=1
[ "$_xrc" = 0 ] || _xok=0
case "$_xout" in *"MODE MIRROR"*) ;; *) _xok=0 ;; esac    # must actually BE the mirror line
case "$_xout" in *'"systemMessage"'*) _xok=0 ;; esac       # ...and carry no systemMessage
printf '%s' "$_xout" | $PY -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1 || _xok=0
if [ "$_xok" = 1 ]; then printf 'PASS  %-42s rc=%s\n' "(x) mirror systemMessage-less + JSON" "$_xrc"
else printf 'FAIL  %-42s rc=%s\n      out=%s\n' "(x) mirror systemMessage-less + JSON" "$_xrc" "$_xout"; fails=$((fails+1)); fi
rm -rf "$_xhome"

# (y) BOTH modes confirmed -> the mirror joins them with '/' (grep matches in file order:
#     freehand then autopilot -> "freehand/autopilot").
checkmirror "(y) both confirmed -> joined form" "freehand confirmed 1750000000"$'\n'"autopilot confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          yes "MODE MIRROR — freehand/autopilot ON" -

# (z) a garbage (non-parsing) line ABOVE the confirmed line must not stop the mirror -- the
#     anchored `grep -E '^(freehand|autopilot) confirmed '` skips the garbage and still matches.
checkmirror "(z) garbage-before-confirmed -> fires" "garbage line here"$'\n'"freehand confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          yes "MODE MIRROR — freehand ON" -

# (aa) FORMAT-COUPLING TRIPWIRE: seed the confirmed entry via the REAL writer (mode-state.py confirm)
#      under a hermetic BALLAST_CLAUDE_HOME -- NOT a hand-written state line -- then assert the
#      mirror fires. This pins the hook's anchored grep to whatever on-disk format mode-state.py
#      actually produces: if the writer's line shape drifts (field order, spacing, status token) a
#      hand-seeded fixture would keep passing while production silently broke. It ALSO exercises F1
#      end-to-end -- the mirror now resolves its state-file home the same way mode-state.py does, so
#      the writer and reader must agree on the directory as well as the line format for this to fire.
echo "--- (aa) mirror grep coupled to real writer format ---"
_aahome="$(mktemp -d)"
# shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
(export BALLAST_CLAUDE_HOME="$_aahome"; $PY "$DIR/../../statusline/mode-state.py" confirm freehand --session abc123def456 >/dev/null 2>&1)
_aaout="$(export BALLAST_CLAUDE_HOME="$_aahome"; printf '%s' '{"session_id":"abc123def456","prompt":"normal request"}' | bash "$HOOK")"; _aarc=$?
_aaok=1
[ "$_aarc" = 0 ] || _aaok=0
case "$_aaout" in *"MODE MIRROR — freehand ON"*) ;; *) _aaok=0 ;; esac
if [ "$_aaok" = 1 ]; then printf 'PASS  %-42s rc=%s\n' "(aa) mirror coupled to writer format" "$_aarc"
else printf 'FAIL  %-42s rc=%s\n      out=%s\n' "(aa) mirror coupled to writer format" "$_aarc" "$_aaout"; fails=$((fails+1)); fi
rm -rf "$_aahome"

echo
[ "$fails" = 0 ] && echo "ALL freehand-mode TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
