#!/bin/bash
# UserPromptSubmit hook — sub-agent fanout model-tier calibration. SOFT: on a fanout
# keyword (ultracode, adhd, deep-research, code-review, audit / adversarial-audit,
# fan-out, subagents, parallel agents) inject the per-tier task->model table as
# additionalContext; no match -> no output -> no injection; always exit 0. Keeps the
# heavyweight table OUT of always-loaded CLAUDE.md (which carries only a one-line reflex)
# and surfaces it exactly when a dispatch is on the table.
#
# Mechanics, shared with freehand-mode.sh: match the raw stdin payload (jq isn't
# guaranteed on this box, so we don't JSON-parse out .prompt), emit JSON on stdout via
# hookSpecificOutput.additionalContext. Fast: pure bash + grep, ~ms.
#
# Recall over precision, deliberately. This fires on USER prompts, so a model-initiated
# fanout (a bare Agent/Task call with no keyword) won't trigger it — that case is covered
# by the always-loaded CLAUDE.md reflex. A mis-fire is low-harm because the injected text
# opens with an ignore-if-not-dispatching clause; keep that clause through any edit — it
# is also the version-proof backstop if the demotion markers below drift.
#
# Trigger precision vs the IDE-opened-file footgun: Claude Code folds the "currently
# viewing" path (<ide_opened_file>...</ide_opened_file>) into the payload, so a bare
# keyword match would fire on commands/code-review.md or this file's own name every
# prompt. The word boundaries below defend it: a leading boundary that rejects identifier
# chars, and a TRAILING boundary that rejects path/extension continuations (/, \, ., -) —
# so "/code-review" still fires (trailing is space/EOL) while ".../code-review.md" or
# "skills/code-review/" does not. "workflow" is deliberately NOT a trigger: too common in
# ordinary prose; the CLAUDE.md reflex covers workflow dispatches, and "ultracode" catches
# the Workflow opt-in keyword.

payload="$(cat)"

# NOTIFICATION-SHELL DEMOTION: Claude Code delivers background-agent completion
# notifications as synthetic user turns through UserPromptSubmit -- they land in
# this raw payload but carry agent/harness OUTPUT (a sub-agent's own returned
# report text), not user intent, and have been observed re-injecting this table
# off a report's own words. Demote both marker shells before the keyword grep
# below, mirroring freehand-mode.sh. Marker strings are harness-version-volatile
# by nature (accepted).
# ACCEPTED RESIDUAL: this also demotes a TYPED prompt that merely QUOTES one
# of these marker strings (rare; the user can rephrase rather than quote
# harness marker text).
case "$payload" in
  *"[SYSTEM NOTIFICATION - NOT USER INPUT]"*|*"<task-notification>"*) exit 0 ;;
esac

# leading  (^|[^[:alnum:]_-])         -> start, or any non-identifier char (incl /, \, space, "(")
# keywords                            -> distinctive fanout vocab; [ -]? allows spaced or hyphenated forms
# trailing ([^[:alnum:]_/\.-]|$)      -> end, or a non-(path/ext) char; rejects /, \, ., - so paths/filenames don't match
if printf '%s' "$payload" | grep -iqE '(^|[^[:alnum:]_-])(ultracode|adhd|deep[ -]?research|code[ -]?review|(adversarial[ -]?)?audits?|fan[ -]?out|sub[ -]?agents?|parallel[ -]?agents?)([^[:alnum:]_/\.-]|$)'; then
  # SESSION COOLDOWN: the table below is ~5KB and its content is static -- re-injecting it on
  # every keyword prompt (audit data: fanout vocab recurs across a session's prompts) taxes
  # context with no new information. One marker per session, TTL 2h, so a LONG session (or a
  # post-compact window, where the earlier injection was summarized away) re-arms. FAIL OPEN:
  # no sid / no home dir -> no cooldown, fire every time (the pre-cooldown behavior).
  sid="$(printf '%s' "$payload" | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
  home_dir="${BALLAST_CLAUDE_HOME:-${HOME:+$HOME/.claude}}"
  if [ -n "$sid" ] && [ -n "$home_dir" ]; then
    marker="$home_dir/.cache/ballast-fanout/$sid"
    # `find -mmin -120` prints the marker only if it exists AND is fresher than the TTL.
    # A suppressed match leaves no fire-ledger row (no output), so record it here -- audits must
    # be able to tell "suppressed by cooldown" from "keyword never matched".
    if [ -n "$(find "$marker" -mmin -120 2>/dev/null)" ]; then
      printf '%s suppressed sid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$sid" >> "$home_dir/.cache/ballast-fanout/suppressed.log" 2>/dev/null
      exit 0
    fi
    { mkdir -p "$home_dir/.cache/ballast-fanout" && touch "$marker"
      find "$home_dir/.cache/ballast-fanout" -type f -mmin +2880 -exec rm -f {} + ; } 2>/dev/null
  fi
  cat <<'JSON'
{
  "systemMessage": "🪜 ballast: subagent-fanout — tier calibration injected",
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "SUB-AGENT FANOUT — per-leaf tier calibration. (Keyword-triggered; ignore if not dispatching.)\n\nTier each LEAF by its hardest reasoning step, not its size or pipeline phase (one phase can mix tiers — e.g. Sonnet finders + an Opus judge). Orchestrator/synthesizer stays top-tier; tier DOWN only leaf work. Span the full family spread (Haiku -> Sonnet -> Opus, and any higher top-tier model if available): cheapest family that clears the leaf's bar; when torn, round UP for judgement (a wrong cheap result costs redo), DOWN for mechanical.\n\n- TOP TIER (Opus, and any higher-tier model if available): judgement, design, planning, orchestration, open-ended / creative work, ambiguous or underspecified tasks (infer intent, spot gaps a spec missed, know when to ask), synthesis / adjudication of conflicting findings, hard audits (security, contradictions). Opus is the default; go higher only on the hardest / most open-ended leaves.\n- SONNET: well-specified execution (clear what / where / how), code comprehension (tracing, dependency-mapping, \"how does X work\"), broad-recall review finders, checklist reviews, drafting to an outline. The workhorse once a spec or plan exists.\n- HAIKU (fast, cheap): pattern-mechanical edits across files, retrieval (locate / grep-and-report), structured extraction / classification, boilerplate / asset loops, large-file / dir scans. Spec it tightly, keep judgement and ambiguity off it, verify output. Chronically UNDER-picked: on a mechanical leaf with a tight spec and checkable output, default DOWN to Haiku and ask what Sonnet would add — but don't stretch it to ambiguous work, where rework eats the savings.\n\n- PARALLELISM BY DEFAULT: independent leaves launch concurrently (one message carrying multiple dispatches, or a workflow wave); serialize only when one leaf's output is another's input. Independent GATES parallelize too — a visual review dispatch belongs alongside a code-review or integration-gate dispatch over the same frozen diff, not queued after it.\n- SIZE THE FANOUT: pick lens COUNT by the diff's shape, not by thoroughness reflex -- a narrow or declarative diff takes a few targeted lenses; only a large multi-file change earns full breadth. Never stack redundant review passes over identical content in one growing context; a fresh short-context pass beats an Nth inline re-read.\n- SPEND CHECKPOINT: on a long multi-wave fanout, take stock out loud when the first wave lands — tokens spent vs. the task's worth, remaining scope re-confirmed. Discovering the budget at the END costs a forced scope cut.\n- DISPATCH PREMISES: a factual premise that licenses or blocks the leaf's work (current code state, \"X is already handled\", expected bands) is verified against the live system first or phrased conditionally, never asserted from narrative or memory; a leaf reporting a premise contradiction is a stop-and-reverify signal, not noise to improvise past.\n- OUTPUT INTEGRITY: a lens returning placeholder or non-substantive output is a FAILED lens, not coverage — re-dispatch it or verify that angle yourself; never let it read as 'that angle is clean'. A leaf's quantitative claim over real data (counts, distributions, 'N% of records') must cite the exact command + output backing it.\n- NO INSTALLS IN LEAVES: a package install is the user's call, made once in the main session — an install verb inside a leaf fires an unreviewable permission prompt from a context the user can't see into. Put the prohibition IN the dispatch prompt (or the shared brief, so later waves inherit it) with the escape hatch stated, or leaves improvise one: report the missing tool as a scope note and finish with what's available. It binds review and probe leaves too, not just executors — 'npx <tool> --version' is remote code execution, not a probe.\n- AGENTTYPE TIER FLOOR: a custom agentType carries its OWN model frontmatter, so a leaf's effective tier is min(the tier you intended, the agentType's default) — set opts.model explicitly on a judgement-heavy leaf running a custom agentType.\n- EFFORT INHERITS SILENTLY: a leaf runs at the SESSION's effort level unless pinned per leaf via agent-frontmatter effort (the Agent/Task tool has no per-dispatch effort param). Executors opus medium by default, sonnet medium for light batches, opus high for the hardest fully-specified ones; xhigh reserved for judge/review leaves. Haiku has no effort parameter — never add a decorative effort line to a Haiku-pinned agent.\n- CONTEXT ECONOMY (see the principles block): route bulk reads/edits and groundwork scans through leaves returning compact reports — a long run of in-line edits on complex work is the tell it's executor-shaped; tiny diffs and judgement calls stay in-line."
  }
}
JSON
fi
exit 0
