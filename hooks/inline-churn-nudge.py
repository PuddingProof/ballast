#!/usr/bin/env python
"""hooks/inline-churn-nudge.py -- PostToolUse hook: shadow log + one data-backed live soft nudge.

WHY THIS EXISTS: the main session's context window is a recurring cost -- every prior turn's
content is re-sent and re-billed on every subsequent turn -- so context-economy doctrine says a
long, expensive main window should offload mechanical work to subagents rather than carry it
in-line. The watched failure shape: a long, unbroken run of in-line Edit/Write/MultiEdit/
NotebookEdit tool calls in the main session with no subagent dispatch in between, on work that
could plausibly have been delegated.

POSTURE: the shadow log is UNCHANGED and keeps accumulating (its line formats are the dataset's
schema -- do not churn them). Layered on top is a SOFT NUDGE, gated four ways so it stays rare:
threshold 20 (chosen from 21.7 days of run-length distribution data, above the legitimate-run
mode), MAIN-session callers only, at most ONCE per session, and never on a run any leaf-class or
ambiguous-class edit has tainted. Everything else -- every leaf fire, every sub-threshold fire,
every dispatch reset -- stays byte-silent as before. The shadow-then-flip playbook this followed
is commit-review-gate.py's; the threshold is what the observation window bought.

CALLER DISCRIMINATION is payload-only (`agent_type` OR `agent_id`), ported from visual-arm.py
along with its degraded-mode rule: `main` is granted only on an affirmatively well-formed
main-loop fire, and anything short of that is `ambiguous`, which counts and taints but never
emits. Deviation from visual-arm's version, deliberate: `file_path` is not part of the test here
(a Task/Agent dispatch fire legitimately carries none, and this hook's decision never uses a path).

FAIL-OPEN GUARANTEE: this fires on every single Edit/Write/MultiEdit/NotebookEdit/Task/Agent
call in every session with the plugin enabled -- a crash here is not a one-off, it is a standing
"every edit now throws" bug. The whole body below main()'s stdin read is wrapped in one broad
try/except that always falls through to sys.exit(0). Malformed JSON, a valid-JSON non-dict
payload (isinstance guard -- this exact bug class was fixed twice elsewhere in this repo; see
doc-write-guard.py's main() and its test_list_json_payload_fails_open regression), and a missing
or unrecognized tool_name are all silent no-ops. That fail-open path stays SILENT rather than
announcing (hooks/CLAUDE.md's announce-on-error convention): a payload that failed to parse is a
payload whose caller class is unknown, and the leaf no-output rule outranks the announce.

ACCEPTED RESIDUALS (by name, deliberate -- not bugs to fix later without re-reading this):
  (a) A leaf fire still counts toward the run and still logs. Only the NUDGE discriminates by
      caller class; the dataset deliberately keeps recording every in-line edit, whoever made it.
  (b) Turn boundaries and user messages do NOT reset the run counter -- deliberate. The failure
      shape this hook watches for (a long stretch of in-line editing with no delegation) can
      span multiple user turns as easily as one; resetting on a turn boundary would hide exactly
      that shape from the data.
  (c) Legitimate long runs -- a tiny-diff series or a deliberate mechanical multi-file sweep --
      will cross 20 and be nudged. That is why the nudge names both readings and closes with
      "carry on", and why it costs at most one line per session.

STATE: <state-home>/.cache/ballast-churn/<session_id> holds `<count> <tainted> <nudged>` (flags
0/1). A legacy bare-integer file parses as the count with both flags false, and ANY unparseable
content reads as a fresh run -- state corruption must never crash an edit. <state-home> is
$BALLAST_CLAUDE_HOME if set, else ~/.claude -- the same hermetic-test override convention as
doc-write-guard.cache_dir() (hooks/CLAUDE.md rule); production code paths never set the var.
shadow.log lives in the same directory and is the accumulating dataset this hook exists to build
-- it is explicitly excluded from the 2-day prune sweep below (unlike a per-session marker, it
must survive an inactivity gap without losing history).

NO systemMessage ON THE COUNTING PATH (hooks/CLAUDE.md's high-frequency-fire-path exception):
this hook fires on literally every qualifying tool call in a session, and hooks/run.sh's fire
ledger already records every dispatch at the process level -- a systemMessage on every 5th edit
would be constant, unactionable chatter. The once-per-session nudge DOES carry one, and run.sh
ledgers it like any other emitting fire.
"""
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

# EDIT-CLASS: in-line tool calls that edit files directly in the main session.
EDIT_CLASS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# DISPATCH-CLASS: subagent-launch tool calls. Both names are covered because harness versions
# differ on which one is live at any given time.
DISPATCH_CLASS = {"Task", "Agent"}

# Run length at which an untainted main-session run earns its one nudge. Picked from 21.7 days of
# shadow.log distribution data (runs reached 80; ~6 episodes/day cross 20), deliberately above the
# legitimate-run mode -- the whole point of the observation window.
NUDGE_THRESHOLD = 20

# The injection. Two readings named explicitly, and the benign one gets the last word: a mis-fire
# must cost one ignorable line, never an argument (hooks/CLAUDE.md).
NUDGE_TEXT = (
    "INLINE-EDIT RUN: 20+ consecutive in-line edits this session without a dispatch. If this is "
    "implementation churn, it is executor-shaped — batch the remaining work to a plan-executor "
    "leaf (see the plan-handoff skill); the write/edit churn belongs in a disposable context, not "
    "the main window. A deliberate mechanical sweep or a tiny-diff series? Carry on."
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
    # Mirrors doc-write-guard.prune(): remove anything untouched for 2+ days -- EXCEPT
    # shadow.log, the accumulating dataset this hook exists to build. A per-session counter
    # file going stale after 2 quiet days is expected and fine to reap (that session is over);
    # shadow.log going stale must NOT cost it its history, or a quiet weekend mid-collection
    # would silently truncate the observation window.
    cutoff = time.time() - 2 * 86400
    for f in glob.glob(os.path.join(cache, "*")):
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


def log_line(cache, line):
    with open(os.path.join(cache, "shadow.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proj_name():
    # basename of cwd, stripping either slash style so a Windows-native cwd and a Git-Bash
    # forward-slash cwd both resolve the same project name.
    return os.path.basename(os.getcwd().rstrip("/\\")) or "unknown"


def short_sid(session_id):
    return (session_id or "")[:8] or "nosession"


def is_subagent(payload):
    """True when this fire originates anywhere other than the main loop.

    Ported verbatim from visual-arm.py (itself ported from process-lifecycle-guard.py). The OR,
    not the AND: agent_type only is a main-thread agent persona, agent_id only is a forked query,
    and neither is a context the user is watching. Fails toward False -- the wrong direction for a
    nudge, which is why callers use caller_class() rather than this predicate alone."""
    try:
        return bool(
            str(payload.get("agent_type") or "").strip()
            or str(payload.get("agent_id") or "").strip()
        )
    except Exception:
        return False


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
    proj = proj_name()
    sid8 = short_sid(session_id)

    if tool_name in EDIT_CLASS:
        count += 1
        # A non-main edit TAINTS the run: the nudge tells the MAIN loop to delegate, and a run a
        # leaf already did part of is not the shape that advice is about. (The count and the log
        # line are unaffected -- the dataset records every in-line edit regardless of caller.)
        if cls != "main":
            tainted = True
        do_nudge = (cls == "main" and count >= NUDGE_THRESHOLD and not nudged and not tainted)
        if do_nudge:
            nudged = True  # persisted below: once per session, and a reset never clears it
        write_state(path, count, tainted, nudged)
        if count % 5 == 0:
            log_line(cache, "%s proj=%s sid=%s run=%d" % (now_iso(), proj, sid8, count))
        if do_nudge:
            emit_nudge()  # last: state and log are durable before the one emitting path runs
        return

    # DISPATCH-CLASS: log a reset line only if the run was long enough to matter (>=3), but
    # always reset the counter to 0 -- a dispatch breaks the in-line run either way. The taint
    # clears with it (the delegation the nudge asks for HAPPENED; the next run starts clean),
    # while `nudged` deliberately survives -- once per session, not once per run.
    if count >= 3:
        log_line(cache, "%s proj=%s sid=%s reset run=%d via=%s" % (now_iso(), proj, sid8, count, tool_name))
    write_state(path, 0, False, nudged)


def main():
    try:
        d = json.loads(sys.stdin.read())
        if isinstance(d, dict):
            handle(d)
        # valid JSON but not an object -> same fail-open contract as doc-write-guard.py's
        # main(): silently do nothing rather than let a .get() call below AttributeError.
    except Exception:
        pass  # malformed JSON / empty stdin / any internal error -> fail open, never crash
    sys.exit(0)  # always exit 0 -- the one emitting path is a soft nudge, never a block


if __name__ == "__main__":
    main()
