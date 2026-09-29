#!/usr/bin/env python
"""hooks/inline-churn-nudge.py -- PostToolUse hook: one soft nudge on a long in-line edit run.

WHY THIS EXISTS: the main session's context window is a recurring cost -- every prior turn's
content is re-sent and re-billed on every subsequent turn -- so context-economy doctrine says a
long, expensive main window should offload mechanical work to subagents rather than carry it
in-line. The watched failure shape: a long, unbroken run of in-line Edit/Write/MultiEdit/
NotebookEdit tool calls in the main session with no subagent dispatch in between, on work that
could plausibly have been delegated.

POSTURE: soft nudge, gated four ways so it stays rare: threshold 20 (chosen from 21.7 days of
run-length data, above the legitimate-run mode), MAIN-session callers only, at most ONCE per
session, and never on a run any leaf-class or ambiguous-class edit has tainted. Every other fire
-- every leaf fire, every sub-threshold fire, every dispatch reset -- is byte-silent.

CALLER DISCRIMINATION is payload-only (`agent_type` OR `agent_id`), ported from visual-arm.py
along with its degraded-mode rule: `main` is granted only on an affirmatively well-formed
main-loop fire, and anything short of that is `ambiguous`, which counts and taints but never
emits. Deviation from visual-arm's version, deliberate: `file_path` is not part of the test here
(a Task/Agent dispatch fire legitimately carries none, and this hook's decision never uses a path).

FAIL-OPEN GUARANTEE: this fires on every single Edit/Write/MultiEdit/NotebookEdit/Task/Agent
call in every session with the plugin enabled -- a crash here is not a one-off, it is a standing
"every edit now throws" bug. The whole body below main()'s stdin read is wrapped in one broad
try/except that always falls through to sys.exit(0). Malformed JSON, a valid-JSON non-dict
payload (isinstance guard -- a bug class this repo's hooks have hit before), and a missing or
unrecognized tool_name are all silent no-ops. That fail-open path stays SILENT rather than
announcing (hooks/CLAUDE.md's announce-on-error convention): a payload that failed to parse is a
payload whose caller class is unknown, and the leaf no-output rule outranks the announce.

ACCEPTED RESIDUALS (by name, deliberate -- not bugs to fix later without re-reading this):
  (a) A leaf fire still counts toward the run. Only the NUDGE discriminates by caller class; a
      leaf edit taints the run instead of being skipped.
  (b) Turn boundaries and user messages do NOT reset the run counter -- deliberate. The failure
      shape this hook watches for (a long stretch of in-line editing with no delegation) can
      span multiple user turns as easily as one; resetting on a turn boundary would hide exactly
      that shape.
  (c) Legitimate long runs -- a tiny-diff series or a deliberate mechanical multi-file sweep --
      will cross 20 and be nudged. That is why the nudge names both readings and closes with
      "carry on", and why it costs at most one line per session.

STATE: <state-home>/.cache/ballast-churn/<session_id> holds `<count> <tainted> <nudged>` (flags
0/1). A legacy bare-integer file parses as the count with both flags false, and ANY unparseable
content reads as a fresh run -- state corruption must never crash an edit. <state-home> is
$BALLAST_CLAUDE_HOME if set, else ~/.claude -- the hermetic-test override convention
(hooks/CLAUDE.md rule); production code paths never set the var.

NO systemMessage ON THE COUNTING PATH (hooks/CLAUDE.md's high-frequency-fire-path exception):
this hook fires on literally every qualifying tool call in a session, and hooks/run.sh's fire
ledger already records every dispatch at the process level -- a systemMessage per counted edit
would be constant, unactionable chatter. The once-per-session nudge DOES carry one, and run.sh
ledgers it like any other emitting fire.
"""
import glob
import json
import os
import sys
import time

# is_subagent lives in the sibling hook_payload module; the hook runs as a script, so its own dir
# is on sys.path already -- inserted explicitly so an importlib load from a test sees it too.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from hook_payload import is_subagent  # noqa: E402

# EDIT-CLASS: in-line tool calls that edit files directly in the main session.
EDIT_CLASS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# DISPATCH-CLASS: subagent-launch tool calls. Both names are covered because harness versions
# differ on which one is live at any given time.
DISPATCH_CLASS = {"Task", "Agent"}

# Run length at which an untainted main-session run earns its one nudge. Picked from 21.7 days of
# run-length distribution data (runs reached 80; ~6 episodes/day cross 20), deliberately above the
# legitimate-run mode.
NUDGE_THRESHOLD = 20

# The injection. Two readings named explicitly, and the benign one gets the last word: a mis-fire
# must cost one ignorable line, never an argument (hooks/CLAUDE.md).
NUDGE_TEXT = (
    "INLINE-EDIT RUN: 20+ in-line edits this session without a dispatch. If this is "
    "implementation churn, hand the rest to a leaf agent (see the plan-handoff skill). A "
    "deliberate mechanical sweep or a run of tiny diffs? Carry on."
)


def state_home():
    # Hermetic-test override convention (hooks/CLAUDE.md rule): BALLAST_CLAUDE_HOME in test
    # envs, ~/.claude in production. Production code paths never set this var.
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def churn_dir():
    d = os.path.join(state_home(), ".cache", "ballast-churn")
    os.makedirs(d, exist_ok=True)
    return d


def prune(cache):
    # Remove anything untouched for 2+ days: a per-session counter file that has gone quiet that
    # long belongs to a session that is over.
    cutoff = time.time() - 2 * 86400
    for f in glob.glob(os.path.join(cache, "*")):
        # Retired shadow-log dataset: the user recycles it themselves, never agent-deleted.
        if os.path.basename(f) == "shadow.log":
            continue
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def read_state(path):
    """(count, tainted, nudged) from `<count> <tainted> <nudged>`.

    MIGRATION: a legacy bare-integer file (the pre-nudge state format) parses as the count with
    both flags false -- the tokens are simply absent. Any other surprise (missing file, empty,
    garbage, a negative/huge value's parse failure) reads as a fresh run: a corrupt state file
    must degrade to "start counting again", never to a crash on every edit."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            parts = f.read().strip().split()
        return (int(parts[0]),
                len(parts) > 1 and parts[1] == "1",
                len(parts) > 2 and parts[2] == "1")
    except Exception:
        return 0, False, False


def write_state(path, count, tainted, nudged):
    with open(path, "w", encoding="utf-8") as f:
        f.write("%d %d %d" % (count, 1 if tainted else 0, 1 if nudged else 0))


def caller_class(payload):
    """'leaf' | 'ambiguous' | 'main' -- the degraded-mode guard around is_subagent().

    'main' is granted only on an affirmatively well-formed main-loop fire: a usable session_id and
    NEITHER discriminator key present in any form (main fires omit them entirely, so a key that is
    present but reads empty is ambiguity, not a main fire). Everything short of that is
    'ambiguous', which counts and taints but never nudges."""
    try:
        if is_subagent(payload):
            return "leaf"
        if "agent_type" in payload or "agent_id" in payload:
            return "ambiguous"
        if not str(payload.get("session_id") or "").strip():
            return "ambiguous"
        return "main"
    except Exception:
        return "ambiguous"


def emit_nudge():
    print(json.dumps({
        "systemMessage": "🌀 ballast: inline-churn-nudge — long in-line edit run, consider dispatching",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": NUDGE_TEXT,
        },
    }))


def handle(d):
    tool_name = d.get("tool_name")
    if not tool_name:
        return  # missing tool_name -> silent no-op
    if tool_name not in EDIT_CLASS and tool_name not in DISPATCH_CLASS:
        return  # anything else -> exit 0 untouched, no state touched

    session_id = d.get("session_id") or "nosession"
    cls = caller_class(d)
    cache = churn_dir()
    prune(cache)
    path = os.path.join(cache, session_id)
    count, tainted, nudged = read_state(path)

    if tool_name in EDIT_CLASS:
        count += 1
        # A non-main edit TAINTS the run: the nudge tells the MAIN loop to delegate, and a run a
        # leaf already did part of is not the shape that advice is about.
        if cls != "main":
            tainted = True
        do_nudge = (cls == "main" and count >= NUDGE_THRESHOLD and not nudged and not tainted)
        if do_nudge:
            nudged = True  # persisted below: once per session, and a reset never clears it
        write_state(path, count, tainted, nudged)
        if do_nudge:
            emit_nudge()  # last: state is durable before the one emitting path runs
        return

    # DISPATCH-CLASS: a dispatch breaks the in-line run, so the counter resets to 0. The taint
    # clears with it (the delegation the nudge asks for HAPPENED; the next run starts clean),
    # while `nudged` deliberately survives -- once per session, not once per run.
    write_state(path, 0, False, nudged)


def main():
    try:
        d = json.loads(sys.stdin.read())
        if isinstance(d, dict):
            handle(d)
        # valid JSON but not an object -> silently do nothing rather than let a .get() call
        # below AttributeError.
    except Exception:
        pass  # malformed JSON / empty stdin / any internal error -> fail open, never crash
    sys.exit(0)  # always exit 0 -- the one emitting path is a soft nudge, never a block


if __name__ == "__main__":
    main()
