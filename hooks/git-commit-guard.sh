#!/bin/bash
# Global PreToolUse hook on the Bash AND PowerShell tools (matcher "Bash|PowerShell", wired in
# ballast's hooks/hooks.json). Mostly SOFT (reminders, exit 0) -- plus ONE hard block (exit 2): a
# compound command that chains a rule-guarded git op (commit/push/reset/rebase/bulk-add) past the
# per-command guards. That compound bypass is the root cause of the "reminder fires after the commit"
# class of bug -- e.g. `git commit && git log` delivers the nudge bundled with the commit's result.
#
# WHY a hook and not a settings `ask` rule: a permission rule can only gate yes/no -- it
# cannot tell Claude to *do* something. The whole point of this hook is to INJECT the
# "run a review pass" instruction. A yes/no commit gate, if ever wanted, belongs in `ask`.
#
# WHY it triggers on `git diff` / `git log`, NOT `git commit`: a non-blocking PreToolUse
# hook's additionalContext is delivered together with the triggering command's RESULT.
# Hang it on `git commit` and the reminder arrives AFTER the commit already ran -- useless
# (that was the long-standing bug: the reminder "fired after the commit"). Hang it on the
# pre-commit INSPECTION Claude runs first (`git diff` / `git log`) and the reminder lands
# while Claude is still pre-commit -- in time to actually review before committing.
#
# Jobs:
#   1) REVIEW NUDGE on `git diff` / `git log` -- run /code-review --fix + /simplify (or the
#      project's own review skill) before committing; confirm only intended files staged.
#   2) SHARED-REPO STAGING SAFETY on bulk `git add -A/-u/.`, `--amend`, history `reset`.
#   3) OFF-SITE PUSH reminder on `git push`.
#
# Output is JSON hookSpecificOutput.additionalContext (plain stdout only shows in
# transcript mode, invisible in normal conversation). Always exit 0 -- never blocks.
#
# Robustness: matches the operation anywhere in the command (covers compound
# `git add -A && git commit`). Command extraction prefers jq, falls back to the dispatcher-verified
# Python (BALLAST_PYTHON), then the raw payload; JSON emission uses that same Python.
#
# Cross-OS: written to run under bash on Windows (Git Bash), macOS, and Linux. It uses NO GNU-only
# regex extensions -- word boundaries are POSIX character classes (see $L/$R below), not `\b`, which
# BSD grep/sed (macOS) treat as a literal 'b'. A \b here would make the guard silently inert on macOS.

payload="$(cat)"

# Extract tool_input.command (present for Bash and PowerShell tools) so a `description`
# mentioning git can't cause a false reminder. Prefer jq; else the dispatcher-verified Python
# (BALLAST_PYTHON, exported by run.sh -- a working Python 3, never the Windows Store stub). If
# NEITHER is available, fall back to scanning the raw payload. That last fallback is the fix for a
# silent-no-op bug: the old code hardcoded bare `python`, so on a box with no jq and a stubbed
# `python` the command came back empty and the WHOLE guard (including the exit-2 hard block) went
# dead. Scanning the raw payload over-matches slightly (a git mention in a description) but
# fail-safe (more guarding) beats fail-silent for a safety hook.
extracted=1
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null)"
else
  # shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
  cmd="$(printf '%s' "$payload" | ${BALLAST_PYTHON:-python} -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)"
fi
# If extraction produced nothing (empty/absent command, or no jq AND no working Python), scan the raw
# payload so the SOFT nudges still fire -- but mark it UNEXTRACTED. The exit-2 HARD BLOCK is then
# skipped (below): in the raw JSON we can't tell a real command from a `description` field, and a
# false hard block would wrongly REJECT a legitimate call -- strictly worse than a missed soft nudge.
if [ -z "$cmd" ]; then cmd="$payload"; extracted=0; fi

# Cheap early-out: nothing git-ish -> stay silent.
case "$cmd" in
  *"git "*) ;;
  *) exit 0 ;;
esac

# Normalize away git's GLOBAL flags so every per-op grep below (which assumes `git diff` / `git commit`
# adjacency) still matches when the caller wedges a global flag between `git` and the subcommand --
# e.g. `git -c core.pager=cat diff`, `git --no-pager log`, `git -C <path> commit`. WHY this matters:
# the pager-disabling inspection form `git -c core.pager=cat diff` is extremely common, and without
# this it silently evades the review nudge (observed: a whole session's commits never nudged), and the
# same gap would let `git -c x commit` slip the commit/push guards. Collapse the global-flag run to a
# bare `git ` ONCE, here, so the fix is single-point and can't drift across the individual checks.
# Leading boundary is `(^|[^[:alnum:]_])` -- the SAME class as $L below, NOT just space-or-start, so a
# sequencer abutted with no space (`x&&git -c y commit`) still normalizes and can't slip the hard
# block. (GNU \b matched any word transition incl. `&git`; space-or-start missed it.) `\1` preserves
# the matched edge char.
cmd="$(printf '%s' "$cmd" | sed -E 's/(^|[^[:alnum:]_])git[[:space:]]+((-c|-C)[[:space:]]+[^[:space:]]+[[:space:]]+|(--git-dir|--work-tree)[=[:space:]][^[:space:]]+[[:space:]]+|(--no-pager|--paginate|-p)[[:space:]]+)+/\1git /g')"

# Portable word boundaries. GNU grep/sed honor \b, but BSD (macOS) grep/sed do NOT -- there \b is a
# literal 'b', so a \b-anchored pattern silently never matches and this guard goes inert on macOS.
# These explicit POSIX classes behave like \b (word char = [[:alnum:]_]) on GNU, BSD, and busybox.
# NOTE: unlike zero-width \b, they CONSUME one character, so a trailing boundary is dropped wherever
# the pattern already requires a following separator (e.g. `git add` + [[:space:]]+ / ` +`).
L='(^|[^[:alnum:]_])'
R='([^[:alnum:]_]|$)'

# Strip commit-MESSAGE argument VALUES (-m '...' / -m "..." / --message=... / --message "...") before
# the compound-sequencer scan below, so a message that merely QUOTES shell metacharacters (e.g. a
# message whose text is "... && git push ...") can't false-trip the hard block -- only real chained
# commands outside message content should. Scoped narrowly: this stripped copy feeds ONLY the
# hard-block scan immediately below; every other check in this file (review nudge, staging/amend/reset
# caution, off-site push) still sees the full, unmodified $cmd. POSIX ERE only (no \b, no non-greedy
# operators) per this file's cross-OS commitment -- the negated-class patterns (one for single-quoted,
# one for double-quoted values) match up to the next matching quote without needing a non-greedy `.*?`.
cmd_noargmsg="$(printf '%s' "$cmd" | sed -E \
  -e "s/(^|[[:space:]])(-m|--message)(=|[[:space:]]+)'[^']*'//g" \
  -e "s/(^|[[:space:]])(-m|--message)(=|[[:space:]]+)\"[^\"]*\"//g")"

# ---- HARD BLOCK (the one non-soft job): a sequencing compound that chains a rule-guarded git op
#      past the per-command guards -- `git add -A && git commit`, `git commit && git log`,
#      `cd x && git commit`, etc. SURGICAL: fires ONLY when a sequencer (&& / || / ;) is immediately
#      followed by a `git ` command AND a guarded op (commit/push/reset-history/rebase/bulk-add) is
#      present. So benign compounds (`cd && ls`, `git status && git diff`) flow free, and a `&&`/`;`
#      inside a commit MESSAGE doesn't trip it (the trigger requires `<sep> git ...`, not `<sep> word`,
#      and the message-value strip above removes any `&&`/`;`/`git` text that lives inside a `-m`/
#      `--message` argument value before this scan ever sees it).
#      Forces guarded ops onto their own line so the per-command guard + working-dir persistence behave.
#      exit 2 => Claude Code blocks the call and feeds stderr back; redo as separate commands.
if [ "$extracted" = 1 ] \
   && printf '%s' "$cmd_noargmsg" | grep -qE '(&&|\|\||;)[[:space:]]*git[[:space:]]' \
   && printf '%s' "$cmd_noargmsg" | grep -qE "${L}git commit${R}|${L}git push${R}|${L}git rebase${R}|${L}git reset${R}.*(--hard|--soft|--mixed|--keep)|${L}git add[[:space:]]+(-A${R}|--all${R}|-u${R}|--update${R}|\.([[:space:]]|\$))"; then
  echo "BLOCKED (compound bypass): a rule-guarded git op (commit / push / reset --hard|soft|mixed / rebase / bulk add -A|-u|.) is chained inside a compound command, which slips it past the per-command git guard. Re-run it as its OWN command -- the working dir persists between calls, so put 'cd' on a separate line and commit by path. Benign compounds are fine; only guarded ops must stand alone." >&2
  exit 2
fi

msgs=()

# 1) REVIEW NUDGE -- fires on the pre-commit inspection Claude runs first, so it arrives
#    in time. Conditional wording: harmless if Claude is diffing for a non-commit reason.
inspecting=0
{ printf '%s' "$cmd" | grep -qE "${L}git diff${R}" || printf '%s' "$cmd" | grep -qE "${L}git log${R}"; } && inspecting=1
# ...but NOT when the SAME command also commits. A compound like `git commit && git log` (commit, then
# show the resulting log) would otherwise re-fire the nudge together with the commit's result -- i.e.
# AFTER the commit already ran. That is the exact "fires after the commit" bug sneaking back in via a
# post-commit diff/log on the same line. The review nudge is only useful on a STANDALONE pre-commit
# inspection, so a command that commits suppresses it.
printf '%s' "$cmd" | grep -qE "${L}git commit${R}" && inspecting=0
# Sidecar suppression -- retained for the now-DORMANT bin/ballast-review fallback shim. The PRIMARY
# review path is the inline ballast:code-review skill, which needs no suppression: it runs in the
# main session (not a sibling headless one), so nudging it is not recursion-shaped. But if the shim
# is ever run manually it still exports BALLAST_SIDECAR_REVIEW=1 into its headless session's hook
# processes, and that session must not be nudged to review itself (observed firing 6x per sidecar on
# the reviewer's own git diff/log calls) -- so suppress the pre-commit nudges there. Everything ABOVE
# this line stays live on purpose: the compound-chain exit-2 hard block and the amend/reset/push
# cautions ENFORCE the shim's read-only intent if it ever drifts toward a write -- suppressing the
# whole guard would disable them.
if [ "$inspecting" -eq 1 ] && [ "${BALLAST_SIDECAR_REVIEW:-}" != "1" ]; then
  # `ballast:code-review` below keeps its namespace prefix ON PURPOSE -- it disambiguates from
  # NATIVE /code-review (a real name collision; native is the disable-model-invocation one that
  # bounces the model). Don't prose-ify it per skills/CLAUDE.md's refer-in-prose convention: that
  # is for skills whose install form varies, but this fork always ships as the namespaced plugin skill
  # -- ON INSTALLS THAT HAVE IT: the public-mirror build excludes the skill (IP-gated until its
  # rewrite), so the nudge text carries an explicit without-it branch instead of pointing every
  # public consumer at a void. The mention is adjudicated in dev/publish.sh's
  # REFERENCE_WARN_ALLOWLIST -- graceful-degrade wording here and there must stay in sync.
  msgs+=("If you're heading toward a commit: run a review pass FIRST. Native /code-review is user-invoke-only now -- invoke the ballast:code-review skill if this install ships it (runs the native-style multi-angle fan-out inline, in this session): \`ballast:code-review <low|medium|high|xhigh|max> [commit-range]\`; installs without it run their own review flow at equivalent depth. Scale the level to the diff, and at a multi-commit checkpoint pass the cumulative range (e.g. \`<base>..HEAD\`) at a heavier level. Add \`--fix\` to apply high-confidence fixes directly, or adjudicate the findings and apply them yourself -- a correct finding is not skippable just for being pre-existing or non-exploitable -- fix if cheap, else log to IDEAS/backlog. Then /simplify on the pending changes (or use this project's own review skills and verification rules). Your own audit / visual / diff-reread passes can justify LOWERING the review level and narrowing scope, but never REPLACE the review pass itself. Docs/meta changes run the durable-docs skill instead. Confirm via git status that only intended files are staged (a concurrent Claude session may share this index). Skip only if the diff is trivial (comments/one-liners), or already reviewed this session.")
  # integration-gate reinforcement: a self-discipline skill's description is a weak auto-trigger, so
  # fire it at the reliable pre-commit moment for multi-part work. Named as prose (not literal Skill()
  # syntax) so it resolves whether the skill is installed personally or as a namespaced plugin skill.
  msgs+=("Multi-part change (built across multiple files / waves / subagents)? Invoke the integration-gate skill to sweep the whole combined diff for cross-cutting bugs before committing -- skip for a single-file edit.")
fi

# 2) SHARED-REPO STAGING HAZARDS -- on the dangerous command itself.
printf '%s' "$cmd" | grep -qE "${L}git add[[:space:]]+(-A${R}|--all${R}|-u${R}|--update${R}|\.([[:space:]]|\$))" \
  && msgs+=("Shared-repo caution: another Claude session may share this index -- avoid bulk staging (git add -A/-u/.); stage only the files you intend, by path.")

{ printf '%s' "$cmd" | grep -qE "${L}git commit${R}.*--amend" \
  || printf '%s' "$cmd" | grep -qE "${L}git reset${R}.*(--hard|--soft|--mixed|--keep)" \
  || printf '%s' "$cmd" | grep -qE "${L}git reset[[:space:]]+(HEAD|@)[~^0-9]"; } \
  && msgs+=("Shared-repo caution: do not --amend or reset a branch a second session may share -- it hijacks their commit. Make a NEW commit instead.")

# 3) OFF-SITE PUSH.
printf '%s' "$cmd" | grep -qE "${L}git push${R}" \
  && msgs+=("git push publishes these commits off-site -- confirm the target repo is PRIVATE and the pushed diff has no secrets, PII, or tokens before pushing.")

[ "${#msgs[@]}" -eq 0 ] && exit 0

# Join fragments and emit as JSON additionalContext (Python handles escaping). Uses the same
# dispatcher-verified interpreter as extraction above; word-split for a two-word "py -3".
joined=""
for m in "${msgs[@]}"; do
  if [ -z "$joined" ]; then joined="$m"; else joined="$joined | $m"; fi
done

# shellcheck disable=SC2086 -- intentional word-split for a two-word BALLAST_PYTHON ("py -3").
${BALLAST_PYTHON:-python} - "$joined" <<'PY'
import json, sys
print(json.dumps({"systemMessage": "\U0001F9F7 ballast: git-commit-guard — pre-commit reminder(s) injected",
                   "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "additionalContext": "REMINDER: " + sys.argv[1]}}))
PY
exit 0
