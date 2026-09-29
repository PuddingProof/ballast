#!/usr/bin/env python
"""
PreToolUse guard (Agent|Task): caps diff-review's leaf spend. Two rules, in order:
  1. OPUS CEILING -- a diff-finder / diff-verifier dispatch with a `fable` model is denied; the
     leaf is re-dispatched on opus. Never consumes a slot.
  2. DEPTH-AWARE LEAF CAPS -- per review fork (key = session_id + caller agent_id) and per leaf
     kind, at most CAPS[kind][depth] dispatches; `depth` is parsed from the leaf prompt. Past the
     cap the dispatch is denied and the fork does that work itself, in-context.
Every other agent type: pure no-op (exit 0, no output, no state touched).

WHY IT EXISTS. A --hard review fanned out ~19 verifier leaves, each on Fable, and repeated that
across fix loops. The prose caps in `skills/diff-review/levels/` are the soft layer; this is the
harness backstop. Main-session callers are NOT exempt -- the cap is about spend, whoever
dispatches.

POSTURE: hard deny (permissionDecision "deny" + exit 0) for both finders and verifiers, no
shadow window. Justified because a false positive costs only in-context work in the fork (a
self-verify, or an angle run in-context) -- a cheap, visible degradation -- and the soft layer
already exists in the skill prose.

FAIL-OPEN GUARANTEE. Every path is wrapped: a malformed payload, a non-dict tool_input, an
unwritable state dir -> exit 0 with no decision (a state failure ALLOWS, never denies). The
fail-open is announced (systemMessage), never silent; the announce can never change the exit code.

NAMED RESIDUAL RISKS (accepted, not bugs):
  (a) A general-purpose fallback finder/verifier is not counted. The fallback only runs when the
      plugin's agent types are absent -- in which case this hook is absent too.
  (b) The caps are per fork, so an orchestrator re-running a review gets a fresh budget. The
      diff-review SKILL.md when_to_use rule against full re-runs is the control for that.
  (c) Depth is parsed from prompt text; a missing or unrecognized depth falls back to the hard
      caps (the loosest), so a parse miss never blocks more than --hard would.
"""

import sys
import json
import os
import re
import glob
import shutil
import time

HOOK_NAME = "diff-review-cap"

# The one home of the cap numbers: leaf kind -> depth -> max dispatches per review fork.
# hard finders = 10 angles + 1 sweep.
CAPS = {
    "diff-verifier": {"light": 0, "medium": 0, "hard": 4},
    "diff-finder": {"light": 0, "medium": 6, "hard": 11},
}
DEFAULT_DEPTH = "hard"  # loosest caps: a parse miss never blocks more than --hard would

# Per-kind slot subdir under the fork key; also the short name in the systemMessage.
KIND_DIR = {"diff-verifier": "verifier", "diff-finder": "finder"}

# Tolerant of `depth: medium`, `"depth": "hard"`, `depth=light`, `DEPTH: Medium`, `depth: --medium`,
# `**depth**: medium`, ``depth: `hard` ``: the word, any run of quote/colon/equals/space/backtick/
# asterisk/hyphen characters, then the level. The level files spell depths as `--medium`, so the
# separator set must admit `--` -- a miss falls back to the LOOSER hard caps, silently lifting
# the medium ones. First match wins. The lookbehind
# (no word char or hyphen) keeps prose like "an in-depth hard look" in steering text from reading
# as a depth field -- a plain \b would not, since a hyphen is itself a word boundary.
DEPTH_RE = re.compile(r"(?<![\w-])depth[\"'\s:=`*-]*(light|medium|hard)\b", re.IGNORECASE)


def _announce_error(site, skipped):
    """Best-effort announce for a fail-open path (announce-on-error contract, hooks/CLAUDE.md).

    Silent on its own failure; never raises; never affects the exit code."""
    try:
        print(json.dumps({
            "systemMessage": "⚠️ ballast: %s — internal error (%s), %s; dispatch allowed"
                             % (HOOK_NAME, site, skipped),
        }))
    except Exception:
        pass


def home_root():
    # BALLAST_CLAUDE_HOME is the hermetic-test override (hooks/CLAUDE.md rule); production
    # never sets it. NEVER the plugin dir -- that is replaced wholesale on plugin update.
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def state_root():
    return os.path.join(home_root(), ".cache", "ballast-diff-review-cap")


def parse_depth(prompt):
    m = DEPTH_RE.search(str(prompt or ""))
    return m.group(1).lower() if m else DEFAULT_DEPTH


def _sanitize(value, fallback):
    # Payload fields are concatenated into a path -- not trusted path components.
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "").strip())[:80]
    return s or fallback


def fork_key(payload):
    # agent_id is the CALLER (the diff-review fork); absent = the main loop.
    return "%s-%s" % (_sanitize(payload.get("session_id"), "nosession"),
                      _sanitize(payload.get("agent_id"), "main"))


def prune_keys(root):
    # Age-prune key dirs older than 2 days (same cutoff as the sibling guards). Best-effort:
    # an unremovable dir must never break the claim below.
    cutoff = time.time() - 2 * 86400
    for d in glob.glob(os.path.join(root, "*")):
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def claim_slot(key, kind, cap):
    """True if a slot was claimed, False if all `cap` slots for this fork+kind are taken.

    The O_CREAT|O_EXCL claim is LOAD-BEARING: the fork dispatches its leaves in one parallel
    message, so these hooks fire concurrently, and a read-increment-write counter would race and
    over-admit. Exclusive create is atomic per slot file, so exactly `cap` fires win.
    Raises on any state-dir failure -- the caller fails OPEN."""
    root = state_root()
    try:
        if os.path.isdir(root):
            prune_keys(root)
    except Exception:
        pass
    d = os.path.join(root, key, KIND_DIR[kind])
    os.makedirs(d, exist_ok=True)
    for n in range(1, cap + 1):
        try:
            fd = os.open(os.path.join(d, "slot-%d" % n), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return True
    return False


def emit_deny(system_clause, reason):
    print(json.dumps({
        "systemMessage": "⛔ ballast: %s — %s" % (HOOK_NAME, system_clause),
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            # permissionDecision must sit INSIDE hookSpecificOutput (a top-level copy is inert),
            # and the reason is delivered verbatim to the calling model -- an instruction to it.
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }))


FABLE_REASON = (
    "diff-review leaves cap at opus: a fable model is not allowed for %s. Re-dispatch the same "
    "leaf with model: \"opus\" (or omit model -- the agent pins opus)."
)

# Every cap reason closes off the improvisation routes before naming the in-context fallback.
NO_ROUTE = "Do NOT retry, reword, or dispatch a different agent type instead. "


def cap_reason(kind, depth, cap):
    if kind == "diff-verifier":
        if cap == 0:
            return ("`--%s` dispatches no verifier leaves. %sVerify candidates yourself "
                    "in-context." % (depth, NO_ROUTE))
        return ("diff-review `--%s` allows at most %d diff-verifier leaves per review and this "
                "fork has used them. %sVerify the remaining candidates yourself in-context and "
                "name them as self-verified in the coverage note." % (depth, cap, NO_ROUTE))
    if cap == 0:
        return ("`--%s` is a single in-context pass with no finder leaves. %sRun it yourself."
                % (depth, NO_ROUTE))
    return ("`--%s` allows at most %d finder leaves per review and this fork has used them. "
            "%sRun any remaining angle yourself in-context and name it as in-context in the "
            "coverage note." % (depth, cap, NO_ROUTE))


def cap_clause(kind, depth, cap):
    fallback = ("remaining candidates self-verified" if kind == "diff-verifier"
                else "remaining angles in-context")
    if cap == 0:
        return "no %s leaves at --%s, %s" % (KIND_DIR[kind], depth, fallback.replace("remaining ", ""))
    return "%s cap (%d, %s) reached, %s" % (KIND_DIR[kind], cap, depth, fallback)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _announce_error("payload parse", "cap check skipped")
        return 0
    if not isinstance(payload, dict):
        _announce_error("payload parse", "cap check skipped")
        return 0

    try:
        ti = payload.get("tool_input")
        if not isinstance(ti, dict):
            _announce_error("tool_input parse", "cap check skipped")
            return 0
        kind = str(ti.get("subagent_type") or "").strip().split(":")[-1].strip()
        if kind not in CAPS:
            return 0  # not a diff-review leaf: pure no-op

        if "fable" in str(ti.get("model") or "").strip().lower():
            emit_deny("fable leaf denied (opus ceiling)", FABLE_REASON % kind)
            return 0

        depth = parse_depth(ti.get("prompt"))
        cap = CAPS[kind][depth]
        if cap == 0:
            # Nothing to count at this depth: deny without touching state.
            emit_deny(cap_clause(kind, depth, cap), cap_reason(kind, depth, cap))
            return 0
    except Exception:
        _announce_error("classify", "cap check skipped")
        return 0

    try:
        claimed = claim_slot(fork_key(payload), kind, cap)
    except Exception:
        # State-dir failure fails OPEN: allow the dispatch, announced.
        _announce_error("state dir", "leaf cap skipped")
        return 0
    if claimed:
        # Allowed: no output. High-frequency path; run.sh still ledgers the fire.
        return 0
    emit_deny(cap_clause(kind, depth, cap), cap_reason(kind, depth, cap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
