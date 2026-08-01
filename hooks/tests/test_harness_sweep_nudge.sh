#!/usr/bin/env bash
# Regression tests for harness-sweep-nudge.sh -- the SessionStart self-prompting sweep nudge.
#
# WHY committed: this hook layers three fiddly pieces that have each regressed in sibling hooks
# before -- the source-repo sentinel gate (a new pattern, first hook to use it), the SWEEP-STATE.md
# " · " parameter-expansion parse (byte-exact separator, same regression class as other ballast
# parsers that assumed locale-safe field splitting), and the `grep -cv` rc-1-on-zero-matches trap
# under `set -u` (the same class ballast-principles.sh and run.sh's ledger() carefully avoid). Every
# case below pins one specific behavior so a future "obvious" edit can't silently break it.
#
# Hermetic: every fixture lives under mktemp -d (never the real ~/.claude or a real project dir).
# BALLAST_CLAUDE_HOME and CLAUDE_PROJECT_DIR are ALWAYS explicitly overridden via `env` for every
# case that matters (only case (e) deliberately unsets HOME/BALLAST_CLAUDE_HOME/USERPROFILE too --
# that's the point of that case, and it still can't touch anything real because with nothing to
# resolve, the hook has nowhere to read from). Self-locating via BASH_SOURCE; exit code = fail count.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/../harness-sweep-nudge.sh"

# Resolve a working Python 3 the same way run.sh's dispatcher does, and export it so the hook's
# JSON-emit step uses a real interpreter (never the Windows Store stub). Individual `env` calls
# below can still override this per-case (see case p, the fail-open-on-bad-python pin).
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
    if $PROBE $run -c "import sys" >/dev/null 2>&1; then PY="$run"; break 2; fi
  done
done
export BALLAST_PYTHON="$PY"

# --- Middle-dot separator literal ----------------------------------------------------------
# The exact 3-byte " · " literal (space, U+00B7 MIDDLE DOT, space) the hook's registry-line
# parser matches on. Defined once here so every fixture builder below uses the identical bytes
# the hook itself scans for.
MID=" · "

# --- Temp-dir bookkeeping + cleanup ---------------------------------------------------------
TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]:-}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

mk_home() {
  # A fresh BALLAST_CLAUDE_HOME fixture: postmortem/ subdir exists, no SWEEP-STATE.md yet.
  local h
  h="$(mktemp -d)"
  TMP_DIRS+=("$h")
  mkdir -p "$h/postmortem"
  printf '%s' "$h"
}

mk_projdir() {
  # A fresh project postmortem dir (what a registry line's <dir> field points at).
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  printf '%s' "$d"
}

state_line() {
  # $1 = project name, $2 = absolute postmortem dir, $3 = watermark basename or NONE
  printf '%s%s%s%s%s' "$1" "$MID" "$2" "$MID" "last=$3"
}

write_state() {
  # $1 = target SWEEP-STATE.md path, remaining args = pre-built registry lines (or malformed
  # prose lines, for case m). Always includes the prose header + swept: line real SWEEP-STATE.md
  # files carry, so the parser's "skip lines without the literal" behavior is exercised for real.
  local f="$1"; shift
  {
    echo "# SWEEP-STATE.md (fixture) -- registry of swept postmortem dirs. One project per line:"
    echo "# <name> · <absolute-postmortem-dir> · last=<report-basename|NONE>"
    echo "swept: 2026-07-11T00:00:00Z"
    local line
    for line in "$@"; do
      printf '%s\n' "$line"
    done
  } > "$f"
}

touch_report() {
  # $1 = project dir, $2 = report basename (must match YYYY-MM-DD-*.md to be a candidate)
  : > "$1/$2"
}

# --- Source-repo sentinel fixtures ----------------------------------------------------------
SRC_REPO="$(mktemp -d)"; TMP_DIRS+=("$SRC_REPO")
mkdir -p "$SRC_REPO/.claude-plugin" "$SRC_REPO/dev"
: > "$SRC_REPO/.claude-plugin/marketplace.json"
: > "$SRC_REPO/dev/check.sh"

NON_SRC="$(mktemp -d)"; TMP_DIRS+=("$NON_SRC")   # neither sentinel

PARTIAL_SRC="$(mktemp -d)"; TMP_DIRS+=("$PARTIAL_SRC")   # marketplace.json only, no dev/check.sh
mkdir -p "$PARTIAL_SRC/.claude-plugin"
: > "$PARTIAL_SRC/.claude-plugin/marketplace.json"

fails=0

# check <name> <expect_exit> <want|EMPTY|-> <forbid|-> -- <env-assignments-and--u-flags...>
check() {
  local name="$1" xexit="$2" want="$3" forbid="$4"; shift 4
  local out rc ok=1
  out="$(env "$@" bash "$HOOK" 2>/dev/null)"
  rc=$?
  [ "$rc" = "$xexit" ] || ok=0
  if [ "$want" = "EMPTY" ]; then
    [ -z "$out" ] || ok=0
  elif [ "$want" != "-" ]; then
    case "$out" in *"$want"*) ;; *) ok=0 ;; esac
  fi
  if [ "$forbid" != "-" ]; then
    case "$out" in *"$forbid"*) ok=0 ;; esac
  fi
  if [ "$ok" = 1 ]; then
    printf 'PASS  %-52s rc=%s\n' "$name" "$rc"
  else
    printf 'FAIL  %-52s rc=%s (want %s)\n      out=%s\n' "$name" "$rc" "$xexit" "$out"
    fails=$((fails+1))
  fi
}

# =============================================================================================
# (a) no CLAUDE_PROJECT_DIR -> rc0 zero bytes
# =============================================================================================
HOME_A="$(mk_home)"
write_state "$HOME_A/postmortem/SWEEP-STATE.md" "$(state_line alpha "$(mk_projdir)" NONE)"
check "(a) no CLAUDE_PROJECT_DIR -> rc0 zero bytes" 0 EMPTY - \
  -u CLAUDE_PROJECT_DIR "BALLAST_CLAUDE_HOME=$HOME_A"

# =============================================================================================
# (b) non-source repo -> silent (even with real unswept signal waiting)
# =============================================================================================
HOME_B="$(mk_home)"
PDIR_B="$(mk_projdir)"
touch_report "$PDIR_B" "2026-07-10-fixture-topic-aaaaaaaa.md"
write_state "$HOME_B/postmortem/SWEEP-STATE.md" "$(state_line alpha "$PDIR_B" NONE)"
check "(b) non-source repo -> silent" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$NON_SRC" "BALLAST_CLAUDE_HOME=$HOME_B"

# =============================================================================================
# (c) marketplace.json alone insufficient -- both sentinels required
# =============================================================================================
check "(c) marketplace.json alone insufficient" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$PARTIAL_SRC" "BALLAST_CLAUDE_HOME=$HOME_B"

# =============================================================================================
# (d) no SWEEP-STATE.md -> silent
# =============================================================================================
HOME_D="$(mk_home)"   # postmortem/ dir exists, SWEEP-STATE.md deliberately never written
check "(d) no SWEEP-STATE -> silent" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_D"

# =============================================================================================
# (e) HOME + BALLAST_CLAUDE_HOME + USERPROFILE all unset -> silent (set -u safety, no abort)
# =============================================================================================
check "(e) HOME+override unset -> silent, no set-u abort" 0 EMPTY - \
  -u HOME -u BALLAST_CLAUDE_HOME -u USERPROFILE "CLAUDE_PROJECT_DIR=$SRC_REPO"

# =============================================================================================
# (f) zero new reports -> ZERO BYTES (non-fire, not just "no message")
# =============================================================================================
HOME_F="$(mk_home)"
PDIR_F="$(mk_projdir)"
touch_report "$PDIR_F" "2026-07-05-fixture-alpha-11111111.md"
write_state "$HOME_F/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_F" "2026-07-05-fixture-alpha-11111111.md")"
check "(f) zero new -> zero bytes" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_F"

# =============================================================================================
# (g) 2 reports > watermark -> JSON with header + count 2
# =============================================================================================
HOME_G="$(mk_home)"
PDIR_G="$(mk_projdir)"
touch_report "$PDIR_G" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_G" "2026-07-06-fixture-alpha-22222222.md"
touch_report "$PDIR_G" "2026-07-07-fixture-alpha-33333333.md"
write_state "$HOME_G/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_G" "2026-07-05-fixture-alpha-11111111.md")"
check "(g) 2 new -> header + count 2" 0 "2 new reports across 1 project" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_G"
# json.dumps escapes non-ASCII by default (ensure_ascii=True, same as ballast-principles.sh's
# emit), so the anchor emoji and em dash come through as ⚓ / — in the raw JSON bytes --
# check the literal ASCII portion of the systemMessage prefix instead of the raw glyphs.
check "(g) 2 new -> systemMessage anchor present" 0 "ballast: harness-sweep" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_G"

# =============================================================================================
# (h) new across alpha+beta -> 2 projects named, hookEventName SessionStart present
# =============================================================================================
HOME_H="$(mk_home)"
PDIR_H_A="$(mk_projdir)"; PDIR_H_B="$(mk_projdir)"
touch_report "$PDIR_H_A" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_H_A" "2026-07-06-fixture-alpha-22222222.md"
touch_report "$PDIR_H_B" "2026-07-05-fixture-beta-33333333.md"
touch_report "$PDIR_H_B" "2026-07-06-fixture-beta-44444444.md"
write_state "$HOME_H/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_H_A" "2026-07-05-fixture-alpha-11111111.md")" \
  "$(state_line beta "$PDIR_H_B" "2026-07-05-fixture-beta-33333333.md")"
check "(h) two projects -> count reflects 2 projects" 0 "across 2 projects" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_H"
check "(h) hookEventName SessionStart present" 0 "SessionStart" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_H"

# =============================================================================================
# (i) last=NONE counts all 3
# =============================================================================================
HOME_I="$(mk_home)"
PDIR_I="$(mk_projdir)"
touch_report "$PDIR_I" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_I" "2026-07-06-fixture-alpha-22222222.md"
touch_report "$PDIR_I" "2026-07-07-fixture-alpha-33333333.md"
write_state "$HOME_I/postmortem/SWEEP-STATE.md" "$(state_line alpha "$PDIR_I" NONE)"
check "(i) last=NONE counts all 3" 0 "3 new reports" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_I"

# =============================================================================================
# (j) same-day strict `>` -- pins the asymmetry with the skill's inclusive watermark
# =============================================================================================
HOME_J="$(mk_home)"
PDIR_J="$(mk_projdir)"
touch_report "$PDIR_J" "2026-07-11-fixture-mmm-00000000.md"   # the watermark file itself
touch_report "$PDIR_J" "2026-07-11-fixture-aaa-00000001.md"   # same day, lexically BEFORE -> not counted
touch_report "$PDIR_J" "2026-07-11-fixture-zzz-00000002.md"   # same day, lexically AFTER -> counted
write_state "$HOME_J/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_J" "2026-07-11-fixture-mmm-00000000.md")"
check "(j) same-day strict > counts only the lexically-later file" 0 "1 new report" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_J"

# =============================================================================================
# (k) non-report .md files (HARNESS-RECS.md, SWEEP-STATE.md) in a registered dir -> not counted
# =============================================================================================
HOME_K="$(mk_home)"
PDIR_K="$(mk_projdir)"
touch_report "$PDIR_K" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_K" "2026-07-06-fixture-alpha-22222222.md"   # the one real new report
: > "$PDIR_K/HARNESS-RECS.md"
: > "$PDIR_K/SWEEP-STATE.md"
write_state "$HOME_K/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_K" "2026-07-05-fixture-alpha-11111111.md")"
check "(k) non-report .md files not counted" 0 "1 new report" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_K"

# =============================================================================================
# (l) dead registry dir skipped, live project still counted
# =============================================================================================
HOME_L="$(mk_home)"
DEAD_DIR="$(mk_projdir)"; rm -rf "$DEAD_DIR"   # registered but no longer exists on disk
PDIR_L="$(mk_projdir)"
touch_report "$PDIR_L" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_L" "2026-07-06-fixture-alpha-22222222.md"
write_state "$HOME_L/postmortem/SWEEP-STATE.md" \
  "$(state_line dead "$DEAD_DIR" NONE)" \
  "$(state_line alpha "$PDIR_L" "2026-07-05-fixture-alpha-11111111.md")"
check "(l) dead dir skipped, live project counted" 0 "1 new report" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_L"

# =============================================================================================
# (m) malformed registry line (no " · ") ignored -- doesn't crash the parse of the good line
# =============================================================================================
HOME_M="$(mk_home)"
PDIR_M="$(mk_projdir)"
touch_report "$PDIR_M" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_M" "2026-07-06-fixture-alpha-22222222.md"
write_state "$HOME_M/postmortem/SWEEP-STATE.md" \
  "this line has no middle-dot separator and must be skipped silently" \
  "$(state_line alpha "$PDIR_M" "2026-07-05-fixture-alpha-11111111.md")"
check "(m) malformed line ignored, good line still parsed" 0 "1 new report" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_M"

# =============================================================================================
# (n) drift-only fires with drift clause (zero new reports otherwise)
# =============================================================================================
HOME_N="$(mk_home)"
PDIR_N="$(mk_projdir)"
touch_report "$PDIR_N" "2026-07-05-fixture-alpha-11111111.md"
write_state "$HOME_N/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_N" "2026-07-05-fixture-alpha-11111111.md")"
{
  # NOTE: deliberately avoid the substring "registered" in these two lines -- "unregistered"
  # itself contains "registered" and would silently get excluded by the hook's `grep -cv
  # registered` filter, defeating the point of this fixture (caught by an earlier test run).
  echo "2026-07-09 · open fixture sighting one, needs triage"
  echo "2026-07-10 · open fixture sighting two, needs triage"
} > "$HOME_N/postmortem/DRIFT-SIGHTINGS.md"
check "(n) drift-only fires with drift clause" 0 "2 unregistered drift sightings" "new report" \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_N"

# =============================================================================================
# (o) sighting line containing "registered" -> silent with 0 new (pins the grep -cv guard)
# =============================================================================================
HOME_O="$(mk_home)"
PDIR_O="$(mk_projdir)"
touch_report "$PDIR_O" "2026-07-05-fixture-alpha-11111111.md"
write_state "$HOME_O/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_O" "2026-07-05-fixture-alpha-11111111.md")"
{
  echo "2026-07-09 · fixture sighting, already registered"
  echo "2026-07-10 · another fixture sighting, registered"
} > "$HOME_O/postmortem/DRIFT-SIGHTINGS.md"
check "(o) all-registered sightings -> silent (grep -cv rc-1-on-zero guard)" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_O"

# =============================================================================================
# (p) BALLAST_PYTHON=/nonexistent + 1 new -> rc0 zero bytes (fail open, not a crash)
# =============================================================================================
HOME_P="$(mk_home)"
PDIR_P="$(mk_projdir)"
touch_report "$PDIR_P" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_P" "2026-07-06-fixture-alpha-22222222.md"
write_state "$HOME_P/postmortem/SWEEP-STATE.md" \
  "$(state_line alpha "$PDIR_P" "2026-07-05-fixture-alpha-11111111.md")"
check "(p) unresolvable BALLAST_PYTHON -> fail open, zero bytes" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_P" \
  "BALLAST_PYTHON=/nonexistent/nope-python-xyz"

# =============================================================================================
# (q) additionalContext carries the "Ignore this" no-op clause
# =============================================================================================
check "(q) additionalContext carries the Ignore-this clause" 0 "Ignore this" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_G"

# =============================================================================================
# (r) output parses as valid JSON via $PY -c json.loads
# =============================================================================================
if [ -n "$PY" ]; then
  json_out="$(env "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_G" bash "$HOOK" 2>/dev/null)"
  # shellcheck disable=SC2086
  if printf '%s' "$json_out" | $PY -c 'import json, sys; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
    printf 'PASS  %-52s rc=0\n' "(r) output parses via json.loads"
  else
    printf 'FAIL  %-52s (invalid JSON)\n      out=%s\n' "(r) output parses via json.loads" "$json_out"
    fails=$((fails+1))
  fi
else
  printf 'FAIL  %-52s (no python resolved to verify with)\n' "(r) output parses via json.loads"
  fails=$((fails+1))
fi

# =============================================================================================
# (s) CRLF registry file -- pins the `${line%$'\r'}` strip
# =============================================================================================
HOME_S="$(mk_home)"
PDIR_S="$(mk_projdir)"
touch_report "$PDIR_S" "2026-07-05-fixture-alpha-11111111.md"
touch_report "$PDIR_S" "2026-07-06-fixture-alpha-22222222.md"
{
  printf '# SWEEP-STATE.md (fixture) -- registry of swept postmortem dirs. One project per line:\r\n'
  printf '# <name> · <absolute-postmortem-dir> · last=<report-basename|NONE>\r\n'
  printf 'swept: 2026-07-11T00:00:00Z\r\n'
  printf '%s\r\n' "$(state_line alpha "$PDIR_S" NONE)"
} > "$HOME_S/postmortem/SWEEP-STATE.md"
check "(s) CRLF registry file -> fires, count 2 (r-strip pin)" 0 "2 new reports" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_S"

# =============================================================================================
# (t) no trailing newline on final registry line -- pins the `|| [ -n "$line" ]` guard
# =============================================================================================
HOME_T="$(mk_home)"
PDIR_T="$(mk_projdir)"
touch_report "$PDIR_T" "2026-07-05-fixture-alpha-11111111.md"
{
  echo "# SWEEP-STATE.md (fixture) -- registry of swept postmortem dirs. One project per line:"
  echo "# <name> · <absolute-postmortem-dir> · last=<report-basename|NONE>"
  echo "swept: 2026-07-11T00:00:00Z"
  printf '%s' "$(state_line alpha "$PDIR_T" NONE)"   # no trailing newline -- deliberate
} > "$HOME_T/postmortem/SWEEP-STATE.md"
check "(t) no trailing newline on final line -> fires, count 1 (no-newline-guard pin)" 0 "1 new report" - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_T"

# =============================================================================================
# (u) four-field future registry line -- pins today's 3-field-canonical parse (non-crashing,
# not "supported"): pdir still resolves to field 2, but wm becomes the LAST " · "-delimited
# field ("extra=field", no "last=" prefix to strip). With one report "2026-07-10-...md" present,
# lexical "2..." > "e..." is FALSE in C collation, so the hook stays silent (rc0, zero bytes).
# This is the actual current behavior, not a claim of 4-field support.
# =============================================================================================
HOME_U="$(mk_home)"
PDIR_U="$(mk_projdir)"
touch_report "$PDIR_U" "2026-07-10-fixture-alpha-11111111.md"
write_state "$HOME_U/postmortem/SWEEP-STATE.md" \
  "alpha${MID}${PDIR_U}${MID}last=NONE${MID}extra=field"
check "(u) four-field line -> silent (pins current 3-field-canonical parse)" 0 EMPTY - \
  "CLAUDE_PROJECT_DIR=$SRC_REPO" "BALLAST_CLAUDE_HOME=$HOME_U"

echo
[ "$fails" = 0 ] && echo "ALL harness-sweep-nudge TESTS PASS (21/21)" || echo "$fails FAILURES"
exit "$fails"
