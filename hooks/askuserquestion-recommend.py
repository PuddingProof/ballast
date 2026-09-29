#!/usr/bin/env python
# Global PreToolUse hook on the AskUserQuestion tool (matcher "AskUserQuestion" in hooks.json).
#
# HARD enforcement (exit 2) of the standing rule: every AskUserQuestion must LEAD with the recommended
# option AND mark it "(Recommended)" in the option LABEL the user reads -- not only in prose / the
# question preamble / an option's description.
#
# WHY exit 2 (block), not a soft reminder: a non-blocking PreToolUse hook's additionalContext is
# delivered TOGETHER WITH the tool's result -- and for AskUserQuestion the "result" is the user's
# ANSWER, so the reminder would arrive after the question was already shown and answered, useless
# for the call it is meant to fix. exit 2 instead DENIES the malformed call and feeds stderr back,
# so the agent fixes the label and re-asks BEFORE the user ever sees it. The soft prose rule did
# not land; this is point-of-use enforcement.
#
# RULE (per question): options[0].label must contain "(recommended)" (case-insensitive). The
# harness-added "Other" option is NOT in tool_input, so only the agent's own 2-4 options are checked.
#
# FAIL OPEN on any parse / shape surprise (unreadable stdin, missing questions, odd schema): a
# convention nudge must NEVER wedge the ability to ask a question. If the AskUserQuestion payload
# shape changes upstream, this hook stops enforcing rather than blocking every question. Not
# silently, though: the genuine internal-error paths (unparseable stdin, a non-dict payload) make a
# best-effort systemMessage announcement before exiting 0, wrapped in its own try/except so the
# announce can never change the exit code. The "not the shape we validate" early-outs
# (missing/empty/malformed questions -- routine on every non-AskUserQuestion call) stay silent:
# those aren't errors, they're the hook correctly recognizing it has nothing to check.

import sys
import json


def _announce_error(site):
    # Best-effort, silent-on-its-own-failure systemMessage so a persistently-crashing guard stays
    # visible instead of quietly going dead every call. Never raises; never affects the exit code.
    # No permission-flow clause on either site: this hook never emits a permissionDecision (it
    # either exits 2 to block a malformed AskUserQuestion call or exits 0 silently) -- a permission
    # decision was never in play, so claiming one was "deferred" would be false.
    try:
        print(json.dumps({
            "systemMessage": "⚠️ ballast: askuserquestion-recommend — internal error (%s), recommendation check skipped" % site,
        }))
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _announce_error("stdin parse")  # unparseable stdin -> fail open, but announce the internal error
        sys.exit(0)
    if not isinstance(payload, dict):
        # Valid JSON but not an object (e.g. a bare list) -> same fail-open contract: .get() on a
        # non-dict would AttributeError -> exit 1, the one payload shape the try above can't cover.
        _announce_error("non-dict payload")
        sys.exit(0)

    questions = (payload.get("tool_input") or {}).get("questions")
    if not isinstance(questions, list) or not questions:
        sys.exit(0)  # not the shape we validate (routine, not an error) -> fail open, silent

    bad = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        opts = q.get("options")
        if not isinstance(opts, list) or not opts or not isinstance(opts[0], dict):
            continue  # malformed question -> let the tool's own validation handle it
        label = str(opts[0].get("label", ""))
        if "(recommended)" not in label.lower():
            hdr = str(q.get("header") or q.get("question") or f"#{i + 1}")[:50]
            bad.append(f'#{i + 1} "{hdr}"')

    if bad:
        # No systemMessage here (deliberate skip): this is the exit-2 hard-block path, and its
        # stderr text is already shown to the user by Claude Code -- a systemMessage would just
        # duplicate it. Still hits run.sh's fire ledger (a non-zero exit counts as a fire) like
        # every other fire.
        print(
            'BLOCKED -- AskUserQuestion: the first option\'s LABEL must carry "(Recommended)" -- the '
            "label the user reads, not only prose / the preamble / an option's description. "
            "Missing it on: " + ", ".join(bad) + ". "
            'Fix: put your recommended choice FIRST and append " (Recommended)" to its label, then '
            "re-ask. (No clear lean? Pick the closest and mark it -- every question wants a "
            "recommendation.)",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
