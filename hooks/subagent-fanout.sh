#!/bin/bash
# UserPromptSubmit hook — sub-agent fanout model-tier allocation.
#
# When the user's prompt signals a sub-agent fanout (ultracode, adhd,
# deep-research, code-review, audit / adversarial-audit, fan-out, subagents,
# parallel agents), inject the
# detailed per-tier task->model calibration as additionalContext. This keeps the
# heavyweight table OUT of always-loaded CLAUDE.md (which carries only a one-line
# reflex) and surfaces it exactly when a dispatch is on the table.
#
# Sibling to freehand-mode.sh and uses the same mechanics: match the raw stdin
# payload (jq isn't guaranteed on this box, so we don't JSON-parse out .prompt),
# emit JSON on stdout via hookSpecificOutput.additionalContext, always exit 0.
# No match -> no output -> no injection. Soft + fast (pure bash + grep, ~ms).
#
# Coverage is best-effort by design: this fires on USER prompts, so a fanout the
# model initiates on its own (a bare Agent/Task call with no keyword in the
# prompt) won't trigger it — that case is covered by the one-line reflex in
# CLAUDE.md, which is always loaded. It also demotes harness-generated
# notification-shell turns (task-notification / background-resume replays)
# before the keyword grep below, since those carry agent/harness OUTPUT, not
# typed user intent — see the NOTIFICATION-SHELL DEMOTION block. A mis-fire is low-harm: the injected text
# tells the reader to ignore it if no dispatch is happening. So unlike
# freehand-mode.sh (where a false arm changes behavior a lot) we bias slightly
# toward recall over precision.
#
# Trigger precision vs the IDE-opened-file footgun: Claude Code folds the
# "currently viewing" path (<ide_opened_file>...</ide_opened_file>) into the
# payload, so a file like commands/code-review.md or hooks/subagent-fanout.sh
# would otherwise match its own name on every prompt. We defend with the word
# boundaries below: a leading boundary that rejects identifier chars, and a
# TRAILING boundary that rejects path/extension continuations (/, \, ., -). So a
# slash command ("/code-review", "/deep-research") still fires (trailing is a
# space/EOL), while a path component (".../code-review.md", "skills/code-review/")
# does not (trailing is "." or "/"). "workflow" is deliberately NOT a trigger:
# it's too common in ordinary prose; the always-loaded CLAUDE.md reflex covers
# workflow dispatches, and "ultracode" catches the Workflow opt-in keyword.

payload="$(cat)"

# NOTIFICATION-SHELL DEMOTION: Claude Code (v2.1.x) delivers background-agent
# completion notifications as synthetic user turns through UserPromptSubmit --
# harness-generated turns land in this raw payload but carry agent/harness
# OUTPUT (a dispatched sub-agent's own returned report text), not user intent.
# Live instance 2026-07-10 (session 57b05b62): two of three
# <task-notification> turns contained "audit"/"subagent" in the agent's
# returned report and re-injected the tier table; the third had no keyword
# and correctly didn't fire. Demote both marker shells before the keyword
# grep below, mirroring freehand-mode.sh's NOTIFICATION-SHELL DEMOTION.
# Marker strings are harness-version-volatile by nature (accepted) -- the
# injected text's "ignore if not dispatching" line is the version-proof
# backstop if they drift.
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
  cat <<'JSON'
{
  "systemMessage": "🪜 ballast: subagent-fanout — tier calibration injected",
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "SUB-AGENT FANOUT — pick a model tier per leaf task. (Keyword-triggered; ignore if not dispatching.)\n\nTier each LEAF task by its hardest reasoning step, not its size or pipeline phase (one phase can mix tiers — e.g. Sonnet finders + an Opus judge). Keep the orchestrator/synthesizer top-tier; tier DOWN only the leaf work, scaling effort to difficulty. Span the full spread of families (Haiku -> Sonnet -> Opus, and any higher top-tier model if available) — cost tables differ wildly, so put each leaf on the cheapest family that clears its bar; when torn, round UP for judgement (a wrong cheap result costs redo), DOWN for mechanical.\n\n- TOP TIER (Opus, and any higher-tier model if available): judgement, design, planning, orchestration, open-ended / creative work, ambiguous or underspecified tasks (infer intent, spot gaps a spec missed, know when to ask), synthesis / adjudication of conflicting findings, hard audits (security, contradictions). Opus is the default; reach for a higher tier only on the hardest / most open-ended tasks (a deliberate call, not a blanket upgrade).\n- SONNET: well-specified execution (clear what / where / how), code comprehension (tracing, dependency-mapping, \"how does X work\"), broad-recall review finders, checklist reviews, drafting to an outline. The workhorse once a spec or plan exists.\n- HAIKU (fast, cheap): pattern-mechanical edits across files, retrieval (locate / grep-and-report), structured extraction / classification, boilerplate / asset loops, large-file / dir scans. Spec it tightly, keep judgement and ambiguity off it, verify output (cheap to run != cheap if wrong). Chronically UNDER-picked in practice: when a leaf is genuinely mechanical with a tight spec and checkable output, default DOWN to Haiku and ask what Sonnet would add (usually nothing) — but don't stretch it to ambiguous work, where rework eats the savings.\n\n- PARALLELISM BY DEFAULT: independent leaves launch concurrently (one message carrying multiple dispatches, or a workflow wave) — serialize only when one leaf's output is another's input. Habitual one-at-a-time dispatch is the observed failure mode: N sequential leaves cost ~N× the wall-clock of one wave. Independent GATES parallelize too — a visual-reviewer pass belongs alongside a code-review or integration-gate dispatch over the same frozen diff, not queued after it.\n- SIZE THE FANOUT: pick lens COUNT by the diff's shape, not by thoroughness reflex -- a narrow or declarative diff takes a few targeted lenses; only a large multi-file change earns the full breadth. Never stack redundant review passes over identical content in one growing context (a watched 5-pass over-review burned ~$80 re-reading itself); prefer a fresh short-context pass over an Nth inline re-read.\n- SPEND CHECKPOINT: on a long multi-wave fanout, when the first wave lands, take stock — tokens spent vs. the task's worth, remaining scope re-confirmed, said out loud — discovering the budget at the END (a forced scope cut, a terminated session) is the expensive form of this checkpoint.\n- DISPATCH PREMISES: a factual premise in a dispatch prompt that licenses or blocks the leaf's work (current code state, \"X is already handled\", expected bands) must be verified against the live system first or phrased conditionally — never asserted from narrative or memory; a leaf reporting a premise contradiction is a stop-and-reverify signal, not noise to improvise past.\n- OUTPUT INTEGRITY: a fanned-out review/audit lens that returns placeholder or non-substantive output is a FAILED lens, not coverage — re-dispatch it or verify that angle yourself; never let it read as 'that angle is clean'. And a leaf's quantitative claim over real data (counts, distributions, 'N% of records') must cite the exact command + its output backing it — an unbacked number is unverified, and a wrong one then self-refutes in the finding instead of nearly driving a bad call.\n- NO INSTALLS IN LEAVES: a package install is the user's call, made once in the main session — an install verb inside a leaf only fires an unreviewable permission prompt from a context the user can't see into. Put the prohibition IN the dispatch prompt (or the shared brief, so later waves inherit it) and state the escape hatch, or leaves improvise one: report the missing tool as a scope note and finish with what's available. Scoping this to 'executors' is how it gets dropped — read-only review and probe leaves reach for tooling just as readily, and 'npx <tool> --version' is remote code execution, not a probe.\n- AGENTTYPE TIER FLOOR: a custom agentType carries its OWN model frontmatter, so a leaf's effective tier is min(the tier you intended, the agentType's default) — set opts.model explicitly on a judgement-heavy leaf that runs on a custom agentType, or its default silently tiers it down.\n- EFFORT INHERITS SILENTLY: a leaf runs at the SESSION's effort level unless set — pin it per leaf via agent-frontmatter effort (the Agent/Task tool itself has no per-dispatch effort param). Executors opus medium by default, sonnet medium for light batches, opus high for the hardest fully-specified ones; xhigh reserved for judge/review leaves. Effort is a Sonnet/Opus-tier lever only — Haiku has no effort parameter, so never add a decorative effort line to a Haiku-pinned agent.\n- CONTEXT ECONOMY: route bulk reads/edits and groundwork scans through leaves returning compact reports — a long run of in-line edits on complex work is the tell it's executor-shaped; tiny diffs and judgement calls stay in-line."
  }
}
JSON
fi
exit 0
