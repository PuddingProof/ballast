#!/usr/bin/env python
"""hooks/inline-churn-nudge.py -- PostToolUse hook, SHADOW MODE ONLY (context-economy observer).

WHY THIS EXISTS: the main session's context window is a recurring cost -- every prior turn's
content is re-sent and re-billed on every subsequent turn -- so context-economy doctrine says a
long, expensive main window should offload mechanical work to subagents rather than carry it
in-line. The watched failure shape: a long, unbroken run of in-line Edit/Write/MultiEdit/
NotebookEdit tool calls in the main session with no subagent dispatch in between, on work that
could plausibly have been delegated. V1 does not judge any single run -- it only counts, so a
later flip decision can pick a threshold from real evidence instead of a guessed number.

SHADOW-FIRST POSTURE (modeled on commit-review-gate.py's shadow-then-flip playbook -- see that
file's header for the fuller rationale this mirrors): this hook NEVER injects
additionalContext, NEVER emits systemMessage, and ALWAYS exits 0 -- it only appends observations
to a log file. FLIP CONDITION: after roughly a 2-week observation window, read
<state-home>/.cache/ballast-churn/shadow.log's run-length distribution, pick a soft-nudge
threshold ABOVE the legitimate-run mode, and only then add an injected one-liner nudge (see
docs/BACKLOG.md, 2026-07-11 entry -- that wording will need the durable-docs gate at that
point). Exactly like commit-review-gate, a threshold picked before any observation risks
false-nudging the very legitimate-long-run shape the tiny-diff/mechanical-sweep exception
exists for; shadow mode reveals that by OBSERVING it in the log, not by nudging on it live.

FAIL-OPEN GUARANTEE: this fires on every single Edit/Write/MultiEdit/NotebookEdit/Task/Agent
call in every session with the plugin enabled -- a crash here is not a one-off, it is a standing
"every edit now throws" bug. The whole body below main()'s stdin read is wrapped in one broad
try/except that always falls through to sys.exit(0). Malformed JSON, a valid-JSON non-dict
payload (isinstance guard -- this exact bug class was fixed twice elsewhere in this repo today;
see doc-write-guard.py's main() and its test_list_json_payload_fails_open regression), and a
missing or unrecognized tool_name are all silent no-ops.

ACCEPTED RESIDUALS (by name, deliberate -- not bugs to fix later without re-reading this):
  (a) Subagent sessions may fire this same hook under their OWN session_id -- v1 does not
      distinguish main-session activity from leaf-session activity. If leaf noise turns out to
      matter, the shadow data itself will show it (leaf-session counters would carry a visibly
      different distribution than main-session ones); v1 makes no attempt to filter it out.
  (b) Turn boundaries and user messages do NOT reset the run counter -- deliberate. The failure
      shape this hook watches for (a long stretch of in-line editing with no delegation) can
      span multiple user turns as easily as one; resetting on a turn boundary would hide exactly
      that shape from the data.
  (c) Legitimate long runs -- the kind a tiny-diff / mechanical-multi-file-sweep exception
      already carves out elsewhere in this repo's review culture -- will show up in the
      shadow.log distribution too. That is not a false positive to suppress; it is the baseline
      the eventual threshold must sit above, which is the whole reason to measure before
      nudging instead of guessing a number.

STATE: <state-home>/.cache/ballast-churn/<session_id> holds the current run counter as a bare
integer. <state-home> is $BALLAST_CLAUDE_HOME if set, else ~/.claude -- the same hermetic-test
override convention as doc-write-guard.cache_dir() (hooks/CLAUDE.md rule); production code paths
never set the var. shadow.log lives in the same directory and is the accumulating dataset this
hook exists to build -- it is explicitly excluded from the 2-day prune sweep below (unlike a
per-session marker, it must survive an inactivity gap without losing history).

NO systemMessage (hooks/CLAUDE.md's high-frequency-fire-path exception): this hook fires on
literally every qualifying tool call in a session, and hooks/run.sh's fire ledger already
records every dispatch of this hook at the process level -- a systemMessage on every 5th edit
would be constant, unactionable chatter for a hook that (in v1) makes no decision a user could
act on anyway. Fires stay auditable via shadow.log itself, which is the whole point of this build.
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


def read_counter(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0  # missing/corrupt counter file -> treat as a fresh run


def write_counter(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


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


def handle(d):
    tool_name = d.get("tool_name")
    if not tool_name:
        return  # missing tool_name -> silent no-op
    if tool_name not in EDIT_CLASS and tool_name not in DISPATCH_CLASS:
        return  # anything else -> exit 0 untouched, no state touched

    session_id = d.get("session_id") or "nosession"
    cache = churn_dir()
    prune(cache)
    path = os.path.join(cache, session_id)
    count = read_counter(path)
    proj = proj_name()
    sid8 = short_sid(session_id)

    if tool_name in EDIT_CLASS:
        count += 1
        write_counter(path, count)
        if count % 5 == 0:
            log_line(cache, "%s proj=%s sid=%s run=%d" % (now_iso(), proj, sid8, count))
        return

    # DISPATCH-CLASS: log a reset line only if the run was long enough to matter (>=3), but
    # always reset the counter to 0 -- a dispatch breaks the in-line run either way.
    if count >= 3:
        log_line(cache, "%s proj=%s sid=%s reset run=%d via=%s" % (now_iso(), proj, sid8, count, tool_name))
    write_counter(path, 0)


def main():
    try:
        d = json.loads(sys.stdin.read())
        if isinstance(d, dict):
            handle(d)
        # valid JSON but not an object -> same fail-open contract as doc-write-guard.py's
        # main(): silently do nothing rather than let a .get() call below AttributeError.
    except Exception:
        pass  # malformed JSON / empty stdin / any internal error -> fail open, never crash
    sys.exit(0)  # v1 is pure shadow: always exit 0, no stdout, no stderr, no systemMessage


if __name__ == "__main__":
    main()
