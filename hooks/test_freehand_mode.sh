#!/usr/bin/env bash
# Regression tests for freehand-mode.sh -- the UserPromptSubmit delegated-autonomy SENSOR hook.
#
# WHY committed: the v0.8.0 redesign made this hook a keyword SENSOR + state MIRROR, not the mode's
# contract (the contract moved to skills/freehand/SKILL.md, its single canonical home). Several
# regimes must not silently regress:
#   - ARM: a boundary-qualified keyword in the extracted `.prompt` injects a SHORT adjudication stub
#     (dynamic path when a session_id is present -> raises a pending chip; hand-escaped static
#     fallback otherwise). The stub stays high-recall and arms on a MENTION too -- it opens with an
#     ADJUDICATE FIRST gate and routes a GENUINE grant to the freehand skill, so precision is the
#     model's call, not the regex's. The stub carries none of the mode's rules (adoption now needs a
#     skill invocation -- a second gate).
#   - MIRROR: on a NON-arming prompt, a CONFIRMED freehand/autopilot entry in the per-session state
#     file re-injects a one-line MODE MIRROR reminder (compact-survival). Pending-only, sidless, and
#     fallback paths never mirror; a keyword arm always takes precedence over a mirror.
#   - FALLBACK: python unavailable / JSON parse failure -> raw-payload scan (today's old behavior).
#   - DEMOTION: harness-generated turn shells (task-notifications, background-resume replays) skip
#     BOTH the arm and the mirror.
# These cases pin all of the above plus the pre-existing path/identifier boundary rule and the
# status-line chip's raise / settle-instruction / no-leak contract.
#
# Payloads are DATA on stdin, so running this file never trips the caller's own hooks. Self-locating:
# finds freehand-mode.sh next to this file; runs wherever it's checked out. Exit code = # of failures.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/freehand-mode.sh"
# The relocated contract now lives in the freehand skill body -- the (s2) cases grep it directly.
SKILL="$DIR/../skills/freehand/SKILL.md"

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
# STATUS-LINE CHIP coverage (2026-07-11 hybrid two-phase rework): the hook raises a per-session
# PENDING chip via statusline/mode-state.py on the DYNAMIC path (cleanly-parsed payload + non-empty
# session_id), and MUST leave no state-file trace on every other path (no-match, demoted, fallback).
# Two helpers below, both hermetic per-call via a fresh `mktemp -d` BALLAST_CLAUDE_HOME (never the
# real ~/.claude), so state-file assertions can't leak between cases or collide with a developer's
# real chip state.

# checkchip <name> <payload> <sid> <want_arm: yes|no> <want_ctx_substr|-> <want_state: yes|no> <want_state_substr|-> [not_want_substr|-]
# Runs the hook against an ISOLATED state home, then asserts armed-ness + an additionalContext
# substring (as `check` does) PLUS whether <home>/ballast/modes/<sid> exists and (optionally)
# contains a substring. The optional 8th param asserts a substring is ABSENT from the output (added
# for v0.8.0: pins that the confirm command / the relocated contract text are GONE from the stub).
checkchip() {
  local name="$1" payload="$2" sid="$3" want_arm="$4" want_ctx="$5" want_state="$6" want_state_sub="${7:-}" not_want="${8:--}"
  local home out rc ok=1 armed=no have_state=no statefile
  home="$(mktemp -d)"
  out="$(export BALLAST_CLAUDE_HOME="$home"; printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) armed=yes ;; esac
  [ "$armed" = "$want_arm" ] || ok=0
  [ "$want_ctx" != "-" ] && { case "$out" in *"$want_ctx"*) ;; *) ok=0 ;; esac; }
  [ "$not_want" != "-" ] && { case "$out" in *"$not_want"*) ok=0 ;; esac; }
  statefile="$home/ballast/modes/$sid"
  [ -f "$statefile" ] && have_state=yes
  [ "$have_state" = "$want_state" ] || ok=0
  if [ "$ok" = 1 ] && [ "$want_state" = yes ] && [ -n "$want_state_sub" ]; then
    case "$(cat "$statefile" 2>/dev/null)" in *"$want_state_sub"*) ;; *) ok=0 ;; esac
  fi
  rm -rf "$home"
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s rc=%s armed=%s state=%s\n' "$name" "$rc" "$armed" "$have_state"
  else printf 'FAIL  %-42s rc=%s armed=%s state=%s (want armed=%s state=%s)\n      out=%s\n' \
              "$name" "$rc" "$armed" "$have_state" "$want_arm" "$want_state" "$out"; fails=$((fails+1)); fi
}

# checknostate <name> <payload> <want_arm: yes|no> <want_ctx_substr|-> <not_want_substr|->
# Same isolated-home shape as checkchip, but asserts the WHOLE <home>/ballast/modes/ dir stays
# empty (zero files of any name) -- used where there may be no sid to check one specific filename
# against (fallback / malformed-payload cases), and optionally that a substring is ABSENT from the
# output (e.g. proving the static fallback stub carries no "ballast-mode" chip instruction).
checknostate() {
  local name="$1" payload="$2" want_arm="$3" want_ctx="$4" not_want="${5:--}"
  local home out rc ok=1 armed=no modesdir nfiles=0 f
  home="$(mktemp -d)"
  out="$(export BALLAST_CLAUDE_HOME="$home"; printf '%s' "$payload" | bash "$HOOK")"; rc=$?
  [ "$rc" = 0 ] || ok=0
  case "$out" in *'"additionalContext"'*) armed=yes ;; esac
  [ "$armed" = "$want_arm" ] || ok=0
  [ "$want_ctx" != "-" ] && { case "$out" in *"$want_ctx"*) ;; *) ok=0 ;; esac; }
  # "-" is the same "don't care" sentinel `check`/`checkchip` use for their substring params
  # (never a real substring to assert absent -- if that were ever needed, note it can't reuse "-").
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

# (l) dynamic path arms AND writes a pending state entry (mode-state.py's "<mode> <status> <epoch>" line).
checkchip "(l) arm writes pending state"     '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes "FREEHAND / AUTOPILOT" yes "autopilot pending"

# (m) v0.8.0 CHANGED: the dynamic stub routes a genuine grant to the freehand SKILL (the confirm
#     command moved into the skill's `on` path, which recovers the sid from ${CLAUDE_SESSION_ID}),
#     so the stub now names "freehand skill" and no longer carries a `ballast-mode confirm` command.
#     It DOES still carry the sid-qualified `ballast-mode clear` instruction for the mere-mention
#     settle (the model has no other way to recover its own session id -- see header comment). The
#     8th checkchip param asserts `ballast-mode confirm` is absent.
checkchip "(m) dynamic routes to freehand skill" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes "freehand skill" yes "autopilot pending"
checkchip "(m) dynamic clear cmd, no confirm" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes "ballast-mode clear autopilot --pending-only --session abc123def456" yes "autopilot pending" "ballast-mode confirm"

# (n) `freehand` keyword -> mode label "freehand" (not hardcoded to autopilot elsewhere in this
#     suite). The settle instruction is now the CLEAR command (confirm moved to the skill).
checkchip "(n) freehand keyword -> label freehand" '{"session_id":"abc123def456","prompt":"go freehand on this"}' \
                                          "abc123def456" yes "ballast-mode clear freehand --pending-only --session abc123def456" yes "freehand pending"

# (o) path-embedded-first: a boundary-qualified "freehand" sits INSIDE a path earlier in the prompt
#     (this hook's own filename), followed by a genuine "autopilot" mention -- the label must come
#     from the first BOUNDARY-QUALIFIED match, not a bare substring grab that would steal "freehand"
#     from the path text. Asserting the CLEAR command names "autopilot" (not "freehand") pins this.
checkchip "(o) path-embedded-first -> label autopilot" \
                                          '{"session_id":"abc123def456","prompt":"look at freehand-mode.sh then go autopilot"}' \
                                          "abc123def456" yes "ballast-mode clear autopilot --pending-only --session abc123def456" yes "autopilot pending"
# (o) also pins the CONFIRM-side label: the grant bullet names the resolved mode, restoring the
# confirm-side coverage the pre-v0.8.0 `ballast-mode confirm <mode>` string used to give. Single
# quotes keep the literal backticks out of bash command substitution.
checkchip "(o) grant bullet names autopilot" \
                                          '{"session_id":"abc123def456","prompt":"look at freehand-mode.sh then go autopilot"}' \
                                          "abc123def456" yes 'for the `autopilot` grant' yes "autopilot pending"

# (p) no-match payload writes no state file at all -- AND, since it also carries a session_id but no
#     keyword and no confirmed state, produces no mirror line either (an empty modes/ dir here means
#     there was never a confirmed entry to mirror).
checknostate "(p) no-match writes no state"  '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          no - -

# (q) demoted notification-shell payload (harness-generated turn) writes no state file, even though
#     session_id IS present and the keyword IS in .prompt -- the demotion exit happens before both
#     the chip-raising branch and the mirror else are ever reached.
checknostate "(q) notification-shell writes no state" \
                                          '{"session_id":"abc123def456","prompt":"[SYSTEM NOTIFICATION - NOT USER INPUT]\nTask finished. The session had autopilot mode granted earlier."}' \
                                          no - -

# (r) fallback path (malformed JSON, same shape as case (e)/(k)) emits the static stub JSON --
#     no state file, and no "ballast-mode" chip-instruction string anywhere in the output (proves
#     this is the static, chip-less stub, not a dynamic emission that merely failed to raise a chip).
checknostate "(r) fallback: static stub, no state, no chip string" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "FREEHAND / AUTOPILOT" "ballast-mode"

# (s1) SHARED-GATE-SENTENCE GUARD: the anti-drift trip-wire for the accepted stub-text duplication
#      (see the "STUB TEXT PAIRING" comments in freehand-mode.sh). The dynamic path and the static
#      fallback path each carry their OWN copy of the adjudication stub -- this pins that both copies
#      still open with the same ADJUDICATE FIRST gate sentence, so a future edit to one copy that
#      silently drops or rewords the gate in only one place gets caught here.
checkchip "(s1) gate sentence: dynamic" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes "ADJUDICATE FIRST" yes "autopilot pending"
checknostate "(s1) gate sentence: fallback" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "ADJUDICATE FIRST" -

# (s2) CANONICAL HOME: the contract's non-delegable approval-floor sentence and the "QUALITY IS
#      NOT DELEGATED" bullet live in the skill body -- the contract's single canonical home, not the
#      hook. These checks pin their PRESENCE there by grepping skills/freehand/SKILL.md directly, so
#      an edit that drops or relocates either out of the skill is caught here.
checkfile() {
  local name="$1" file="$2" want="$3" ok=1
  if [ -f "$file" ] && grep -qF -- "$want" "$file"; then :; else ok=0; fi
  if [ "$ok" = 1 ]; then printf 'PASS  %-42s\n' "$name"
  else printf 'FAIL  %-42s (want "%s" in %s)\n' "$name" "$want" "$file"; fails=$((fails+1)); fi
}
checkfile "(s2) non-delegable relocated to SKILL.md"      "$SKILL" "non-delegable"
checkfile "(s2) QUALITY-NOT-DELEGATED in SKILL.md"        "$SKILL" "QUALITY IS NOT DELEGATED"

# (s3) INVERSE of (s2): the relocated contract text must NOT appear in the hook's injected stub on
#      EITHER path -- the stub is short and rules-free by design. Pins that the relocation actually
#      removed the text from the hook (not merely copied it into the skill).
checkchip "(s3) dynamic has no non-delegable" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes - yes "autopilot pending" "non-delegable"
checknostate "(s3) fallback has no non-delegable" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes - "non-delegable"

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

# (v) PENDING-only state (not a standing grant) -> no mirror. Only a CONFIRMED entry mirrors.
checkmirror "(v) pending-only -> no mirror" "freehand pending 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          no - -

# (w) confirmed state BUT the prompt arms a keyword -> the keyword ARM wins (structural precedence:
#     mirror lives in the else branch), so the output is the adjudication stub, NOT a mirror line.
checkmirror "(w) confirmed+keyword -> arm not mirror" "freehand confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          yes "ADJUDICATE FIRST" "MODE MIRROR"

# (x) two no-mirror edge cases with a seeded confirmed file present:
#     - malformed payload WITH a keyword -> arms the static fallback stub (py_rc!=0 -> no sid ->
#       fallback never mirrors); output is the stub, never a MODE MIRROR line.
checkmirror "(x) malformed+seeded -> stub, no mirror" "freehand confirmed 1750000000"$'\n' \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "FREEHAND / AUTOPILOT" "MODE MIRROR"
#     - clean payload with NO session_id and no keyword -> else branch, but empty sid -> no state
#       file is read (the seeded file is keyed on abc123def456, which this payload never names) -> no
#       output at all.
checkmirror "(x) sidless+seeded -> no output" "freehand confirmed 1750000000"$'\n' \
                                          '{"prompt":"normal request"}' \
                                          no - -

# (y) the mirror line carries NO systemMessage (silent by design -- high-frequency fire path;
#     visibility rides on the statusline chip + the run.sh ledger) AND still parses as well-formed
#     JSON. Same bespoke-check shape as (t) below.
echo "--- (y) mirror is systemMessage-less + valid JSON ---"
_yhome="$(mktemp -d)"
mkdir -p "$_yhome/ballast/modes"
printf 'freehand confirmed 1750000000\n' > "$_yhome/ballast/modes/abc123def456"
_yout="$(export BALLAST_CLAUDE_HOME="$_yhome"; printf '%s' '{"session_id":"abc123def456","prompt":"normal request"}' | bash "$HOOK")"; _yrc=$?
_yok=1
[ "$_yrc" = 0 ] || _yok=0
case "$_yout" in *"MODE MIRROR"*) ;; *) _yok=0 ;; esac    # must actually BE the mirror line
case "$_yout" in *'"systemMessage"'*) _yok=0 ;; esac       # ...and carry no systemMessage
printf '%s' "$_yout" | $PY -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1 || _yok=0
if [ "$_yok" = 1 ]; then printf 'PASS  %-42s rc=%s\n' "(y) mirror systemMessage-less + JSON" "$_yrc"
else printf 'FAIL  %-42s rc=%s\n      out=%s\n' "(y) mirror systemMessage-less + JSON" "$_yrc" "$_yout"; fails=$((fails+1)); fi
rm -rf "$_yhome"

# (z) BOTH modes confirmed -> the mirror joins them with '/' (grep matches in file order:
#     freehand then autopilot -> "freehand/autopilot").
checkmirror "(z) both confirmed -> joined form" "freehand confirmed 1750000000"$'\n'"autopilot confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          yes "MODE MIRROR — freehand/autopilot ON" -

# (aa) the "do NOT adopt" gate phrase + the "freehand skill" routing + the "argument `off`" revoke
#      bullet must appear on BOTH stub paths (dynamic + fallback) -- pins that the second-adoption-
#      gate wording and the revoke route render wherever the stub does. "do NOT adopt" and
#      "argument `off`" are pinned as SHORT STABLE MARKER PHRASES (fragments of the byte-pinned
#      do-not-adopt sentence and the revoke bullet) so a wording tweak elsewhere in the paragraph
#      doesn't spuriously trip these while the marker itself is intact. Single quotes on the
#      backtick-bearing revoke phrase keep bash from command-substituting it.
checkchip "(aa) dynamic: do-NOT-adopt" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes "do NOT adopt" yes "autopilot pending"
checknostate "(aa) fallback: do-NOT-adopt" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "do NOT adopt" -
checknostate "(aa) fallback: routes to freehand skill" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes "freehand skill" -
checkchip "(aa) dynamic: revoke bullet" '{"session_id":"abc123def456","prompt":"go autopilot on this"}' \
                                          "abc123def456" yes 'argument `off`' yes "autopilot pending"
checknostate "(aa) fallback: revoke bullet" \
                                          '{prompt: this is not valid json, but the user wants autopilot mode enabled}' \
                                          yes 'argument `off`' -

# (bb) a garbage (non-parsing) line ABOVE the confirmed line must not stop the mirror -- the
#      anchored `grep -E '^(freehand|autopilot) confirmed '` skips the garbage and still matches.
checkmirror "(bb) garbage-before-confirmed -> fires" "garbage line here"$'\n'"freehand confirmed 1750000000"$'\n' \
                                          '{"session_id":"abc123def456","prompt":"normal request"}' \
                                          yes "MODE MIRROR — freehand ON" -

# (cc) FORMAT-COUPLING TRIPWIRE: seed the confirmed entry via the REAL writer (mode-state.py confirm)
#      under a hermetic BALLAST_CLAUDE_HOME -- NOT a hand-written state line -- then assert the
#      mirror fires. This pins the hook's anchored grep to whatever on-disk format mode-state.py
#      actually produces: if the writer's line shape drifts (field order, spacing, status token) a
#      hand-seeded fixture would keep passing while production silently broke. It ALSO exercises F1
#      end-to-end -- the mirror now resolves its state-file home the same way mode-state.py does, so
#      the writer and reader must agree on the directory as well as the line format for this to fire.
echo "--- (cc) mirror grep coupled to real writer format ---"
_cchome="$(mktemp -d)"
# shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
(export BALLAST_CLAUDE_HOME="$_cchome"; $PY "$DIR/../statusline/mode-state.py" confirm freehand --session abc123def456 >/dev/null 2>&1)
_ccout="$(export BALLAST_CLAUDE_HOME="$_cchome"; printf '%s' '{"session_id":"abc123def456","prompt":"normal request"}' | bash "$HOOK")"; _ccrc=$?
_ccok=1
[ "$_ccrc" = 0 ] || _ccok=0
case "$_ccout" in *"MODE MIRROR — freehand ON"*) ;; *) _ccok=0 ;; esac
if [ "$_ccok" = 1 ]; then printf 'PASS  %-42s rc=%s\n' "(cc) mirror coupled to writer format" "$_ccrc"
else printf 'FAIL  %-42s rc=%s\n      out=%s\n' "(cc) mirror coupled to writer format" "$_ccrc" "$_ccout"; fails=$((fails+1)); fi
rm -rf "$_cchome"

# (t) STATE-WRITE FAILURE IS NON-FATAL: point BALLAST_CLAUDE_HOME at a path that cannot possibly
#     become a directory (a plain FILE sits where mode-state.py would need to mkdir "ballast/modes"
#     underneath it) -- mode-state.py's write_state() lets that mkdir failure propagate as an
#     uncaught exception, and freehand-mode.sh's raise call is `... || true` specifically so that
#     failure can never surface. The hook must still emit well-formed JSON and exit 0.
echo "--- (t) state-write failure is non-fatal ---"
_badparent="$(mktemp -d)"
_badhome="$_badparent/not-a-dir"
: > "$_badhome"   # a FILE at the path mode-state.py needs as a DIRECTORY -- forces its mkdir to fail
_out="$(export BALLAST_CLAUDE_HOME="$_badhome"; printf '%s' '{"session_id":"abc123def456","prompt":"go autopilot on this"}' | bash "$HOOK")"; _rc=$?
_ok=1
[ "$_rc" = 0 ] || _ok=0
case "$_out" in *'"additionalContext"'*) ;; *) _ok=0 ;; esac
printf '%s' "$_out" | $PY -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1 || _ok=0
if [ "$_ok" = 1 ]; then printf 'PASS  %-42s rc=%s\n' "(t) state-write failure non-fatal" "$_rc"
else printf 'FAIL  %-42s rc=%s\n      out=%s\n' "(t) state-write failure non-fatal" "$_rc" "$_out"; fails=$((fails+1)); fi
rm -rf "$_badparent"

echo
[ "$fails" = 0 ] && echo "ALL freehand-mode TESTS PASS" || echo "$fails FAILURES"
exit "$fails"
