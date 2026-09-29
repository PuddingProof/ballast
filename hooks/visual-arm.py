#!/usr/bin/env python
"""
PostToolUse hook (Write|Edit|MultiEdit): the first FRONTEND file touch of a session arms the
visual workflow skill -- once, in the MAIN LOOP, with a one-line additionalContext injection.

WHY IT EXISTS. A done-time trigger fires too late: by the time anyone thinks "visual check", the
frontend work was already built blind. Arming moves the load to the first frontend touch,
deterministically, instead of hoping a model remembers.

WHY MAIN-LOOP-ONLY IS LOAD-BEARING (the one rule not to soften). Executors do the editing, so the
first frontend touch is usually a LEAF's -- and an injection there lands in an agent forbidden to
act on it (it cannot own the session's origin, route the ladder, or run the done gate) while
burning the once-per-session trigger. So a leaf touch records a PENDING-ARM flag and emits NOTHING
(no stdout at all, not even a systemMessage); the next qualifying main-loop fire drains it as the
injection. The state file is this hook's audit record in place of the ledger row a silent fire
cannot produce.

CALLER DISCRIMINATION is payload-only, ported from process_lifecycle_guard.py (`agent_type` OR
`agent_id`). Verified fire-side on PostToolUse (CC 2.1.220): both fields present on leaf fires,
absent on main-loop fires, and `session_id` is PARENT-STABLE on a leaf fire -- which is why
session_id is the ledger key. Never key on `prompt_id` (it rotates per turn).

DEGRADED MODE IS DESIGNED, and needs its own guard because is_subagent() fails toward False
(= "main loop"), the WRONG direction here: for the install/lifecycle guards False means "defer to
the attended user", but for arming it means "inject", and an ambiguous payload must never inject.
So injecting requires the payload to AFFIRMATIVELY look like a well-formed main-loop fire --
  * the payload is a dict carrying a non-empty `session_id` (the ledger key), AND
  * `tool_input` is a dict carrying a non-empty string `file_path`, AND
  * neither discriminator key is present in any form.
A discriminator key PRESENT but reading empty/odd is ambiguity, not a main-loop fire (main fires
omit the keys entirely) -- it resolves to RECORD. Any other shortfall resolves to RECORD if a
frontend file and a usable key are both determinable, else to silence. The asymmetry is the point:
a missed injection costs one turn of latency, a leaf injection burns the session's trigger.

POSTURE: soft nudge (exit 0 + additionalContext), once per session, worded to no-op gracefully
on a mis-fire. Never blocks anything; PostToolUse cannot.

FRONTEND PATTERN (test-pinned in test_visual_arm.py, positives AND negatives).
  * EXTENSIONS ARE DECISIVE: .tsx .jsx .svelte .vue .css .scss .html .astro -- any path.
    Deliberately no directory exclusions (`coverage/index.html`, `docs/*.html` DO arm): such a
    list is a maintenance treadmill with no ceiling, a mis-fire costs one ignorable line, and the
    injection's own "matched by accident? Ignore this." is the compensating control. Pinned as a
    positive test row so it reads as a decision, not an oversight.
  * PATH HEURISTIC (narrow, secondary): a component/style/template PATH SEGMENT plus a script
    extension (.ts .js .mjs .cjs), for styling-adjacent files whose extension says nothing
    (`src/components/Button.ts` holding a styled-component, `ui/theme.ts`). Segment match only
    (`src/parse-components.ts` does not arm), extension-gated too (`docs/components/overview.md`
    does not arm).

STATE: <ballast-home>/.cache/ballast-arm/arm-<session-key>.json, one file per session, keyed by
`session_id`. Never the plugin dir (replaced wholesale on update); BALLAST_CLAUDE_HOME is the
hermetic-test override, never set in production. Records the MEASUREMENT DENOMINATOR -- `armed_at`,
`armed_via` (direct|drain), per-class fire counts -- so a later sweep can compute armed-sessions
against sessions that actually loaded the workflow skill. Files older than 7 days are pruned on any
dir touch.

FAIL-OPEN GUARANTEE. Every path is wrapped; a malformed payload, an unreadable/unwritable state
dir, or a regex surprise exits 0. Fail-open is ANNOUNCED (systemMessage, wrapped in its own
try/except, never affecting the exit code) except on a LEAF fire, where the no-output rule wins.

NAMED RESIDUALS (accepted; do not silently "fix"):
  1. Leaf fires produce no fires-ledger row (run.sh ledgers non-empty stdout) -- deliberate, per
     the main-loop-only rule. The state file's counters are the substitute audit trail.
  2. The extension list omits .less/.sass/.styl/.pcss. A project using them arms only via the
     path heuristic (their files usually sit under styles/).
  3. Non-visual frontend edits (a .ts config under ui/, a .css comment) arm the session and cost
     one ignorable injected line. Recall is preferred over precision here by design.
"""

import json
import os
import re
import sys
import time

# is_subagent lives in the sibling hook_payload module; the hook runs as a script, so its own dir
# is on sys.path already -- inserted explicitly so an importlib load from a test sees it too.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from hook_payload import is_subagent  # noqa: E402

HOOK_NAME = "visual-arm"

STATE_DIRNAME = "ballast-arm"
PRUNE_AFTER_SECONDS = 7 * 86400

# --- frontend pattern (pinned in the suite; see the header for the boundary rationale) --------
# Decisive extensions: presence of one of these arms regardless of where the file lives.
FRONTEND_EXT = re.compile(r"\.(?:tsx|jsx|svelte|vue|css|scss|html|astro)$", re.IGNORECASE)
# Component/style/template path SEGMENT -- anchored to `/` on both sides (or string start) so it
# is a directory, not a substring of a filename (`src/parse-components.ts` must not match).
STYLED_SEGMENT = re.compile(
    r"(?:^|/)(?:components?|styles?|stylesheets|templates?|partials|pages|views|layouts"
    r"|widgets|ui|themes?)/",
    re.IGNORECASE,
)
# Script extensions the segment heuristic is allowed to promote. Kept to the JS/TS family: the
# heuristic's whole job is styling-adjacent CODE (styled-components, theme tokens, template
# builders), and widening it turns `pages/index.md` or `ui/icon.svg` into arming events.
STYLED_EXT = re.compile(r"\.(?:ts|js|mjs|cjs)$", re.IGNORECASE)


def _announce_error(site, skipped):
    """Best-effort announce for a fail-open path (announce-on-error contract, hooks/CLAUDE.md).

    Silent on its own failure; never raises; never affects the exit code. NOT called on leaf
    fires -- the no-output rule outranks the announce contract there (header, residual 1)."""
    try:
        print(json.dumps({
            "systemMessage": "⚠️ ballast: %s — internal error (%s), %s"
                             % (HOOK_NAME, site, skipped),
        }))
    except Exception:
        pass


# --------------------------------------------------------------------------------------
# Caller discrimination
# --------------------------------------------------------------------------------------
def caller_class(payload, file_path, key):
    """'leaf' | 'ambiguous' | 'main' -- the degraded-mode guard around is_subagent().

    'main' is granted only on an AFFIRMATIVELY well-formed main-loop fire (see the header's
    DEGRADED MODE block): a usable ledger key, a real file_path, and neither discriminator key
    present in any form. Everything short of that is 'ambiguous', which records and never
    injects.
    """
    try:
        if is_subagent(payload):
            return "leaf"
        # A discriminator key PRESENT but reading empty is ambiguity, not a main-loop fire: B0a
        # observed main fires omitting both keys entirely.
        if "agent_type" in payload or "agent_id" in payload:
            return "ambiguous"
        if not key or not file_path:
            return "ambiguous"
        return "main"
    except Exception:
        return "ambiguous"


# --------------------------------------------------------------------------------------
# Frontend detection
# --------------------------------------------------------------------------------------
def is_frontend(file_path):
    """True when this path is a frontend/styling surface. See the header's FRONTEND PATTERN."""
    try:
        p = str(file_path or "").replace("\\", "/").strip()
        if not p:
            return False
        if FRONTEND_EXT.search(p):
            return True
        return bool(STYLED_SEGMENT.search(p) and STYLED_EXT.search(p))
    except Exception:
        return False


# --------------------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------------------
def home_root():
    # BALLAST_CLAUDE_HOME is the hermetic-test override (hooks/CLAUDE.md); production never sets
    # it. NEVER the plugin dir -- it is replaced wholesale on plugin update (standing repo rule).
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def state_dir():
    return os.path.join(home_root(), ".cache", STATE_DIRNAME)


def session_key(payload):
    """`session_id`, sanitized to a filename-safe charset.

    A leaf PostToolUse fire carries the PARENT session's session_id, so a leaf's pending-arm
    record and the main loop's drain land in the same file. `transcript_path`'s stem is the
    measured-equivalent fallback; a payload field is never a trusted path component, hence the
    sanitize.

    Deliberately NO `or "nosession"` fallback, unlike the origin ledger: it only needs *a*
    filename and a shared one is harmless, while a shared arm-state file would leak one
    session's armed flag into another's. Here an empty key is a hard stop -- main() refuses to record or inject without one.
    """
    key = ""
    try:
        key = str(payload.get("session_id") or "").strip()
        if not key:
            base = os.path.basename(str(payload.get("transcript_path") or "").replace("\\", "/"))
            if base.endswith(".jsonl"):
                base = base[:-len(".jsonl")]
            key = base.strip()
    except Exception:
        key = ""
    return re.sub(r"[^A-Za-z0-9._-]", "_", key)


def state_path(key):
    return os.path.join(state_dir(), "arm-%s.json" % key)


def prune_state(d):
    # Age-prune on dir touch. Best-effort -- an unremovable file must never break the read/write
    # below.
    cutoff = time.time() - PRUNE_AFTER_SECONDS
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        f = os.path.join(d, name)
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def new_state(key):
    return {
        "session_id": key,
        "created_at": time.time(),
        "armed": False,
        "armed_at": None,
        "armed_via": None,       # "direct" (main-loop frontend touch) | "drain" (leaf-recorded)
        "pending_arm": False,
        "pending_arm_at": None,
        "pending_arm_via": None,  # "leaf" | "ambiguous"
        # Measurement denominator: enough to compute armed-sessions and to see how the arm was
        # reached, without a second ledger. Counts STATE-TOUCHING fires only -- the cheap exit in
        # main() drops non-frontend leaf/ambiguous fires before any IO, deliberately: this hook
        # fires on every edit in every session and a counter is not worth a write per edit.
        "fires": {"total": 0, "main": 0, "leaf": 0, "ambiguous": 0, "frontend": 0},
    }


def load_state(key):
    """Read this session's state, or a fresh one. Any surprise -> a fresh dict (which fails toward
    recording, never toward injecting: a fresh state is never `armed`)."""
    path = state_path(key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return new_state(key)
        base = new_state(key)
        base.update(data)
        if not isinstance(base.get("fires"), dict):
            base["fires"] = new_state(key)["fires"]
        return base
    except Exception:
        return new_state(key)


def save_state(key, state, prune=True):
    d = state_dir()
    os.makedirs(d, exist_ok=True)
    # prune=False on the hot path (an already-armed session bumping counters on every edit): the
    # age-prune only has to run occasionally, and a full listdir+getmtime over every session's
    # state file on every Write/Edit is the one cost this hook can actually be felt through.
    if prune:
        prune_state(d)
    tmp = state_path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, state_path(key))


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
# The injection. Two short lines by design -- it recurs in every cache re-read of this session's
# context, so length is a standing cost. The opening clause is verbatim what the workflow skill's
# when_to_use anticipates ("an arming injection says ..."), so the two texts must move together.
# The trailing sentence is the hooks/CLAUDE.md mis-fire rule: a false arm must cost one ignorable
# line, never an argument.
INJECTION = (
    "frontend touched — load the visual workflow skill now (ballast:visual-probe): it owns the "
    "session origin and the done gate. Not doing frontend work, or matched by accident? Ignore this."
)


def emit_arm(via):
    print(json.dumps({
        "systemMessage": "🎯 ballast: %s — frontend touched, visual workflow skill armed (%s)"
                         % (HOOK_NAME, via),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": INJECTION,
        },
    }))


# --------------------------------------------------------------------------------------
def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _announce_error("payload parse", "arming check skipped")
        return 0
    if not isinstance(payload, dict):
        # Valid JSON that is not an object: same fail-open contract (.get() below would raise).
        _announce_error("payload parse", "arming check skipped")
        return 0

    leaf = False
    try:
        ti = payload.get("tool_input")
        if not isinstance(ti, dict):
            ti = {}
        fp = ti.get("file_path")
        file_path = fp.strip() if isinstance(fp, str) else ""

        key = session_key(payload)
        cls = caller_class(payload, file_path, key)
        leaf = cls == "leaf"
        frontend = is_frontend(file_path)

        # Nothing this fire could ever do: not a frontend touch, and no pending flag could exist
        # to drain in a context that may not inject. Cheap exit before any state IO -- this hook
        # fires on EVERY Write/Edit/MultiEdit in every session, most of which touch no frontend.
        if not frontend and cls != "main":
            return 0

        if not key:
            # No usable ledger key: nothing can be recorded and nothing may be injected (an
            # unkeyed injection could not be marked armed, so it would repeat every fire).
            if not leaf:
                _announce_error("session key", "arming check skipped (no session_id)")
            return 0

        # Fast path: a main-loop fire on a NON-frontend file can only ever matter as a drain, and
        # a drain needs an existing state file (the pending flag lives there). No file -> nothing
        # to drain and nothing the denominator needs -- skip the read+write+prune this hook would
        # otherwise pay on every edit of every non-frontend session.
        if cls == "main" and not frontend and not os.path.isfile(state_path(key)):
            return 0

        state = load_state(key)
        fires = state["fires"]
        fires["total"] = fires.get("total", 0) + 1
        fires[cls] = fires.get(cls, 0) + 1
        if frontend:
            fires["frontend"] = fires.get("frontend", 0) + 1

        if state.get("armed"):
            save_state(key, state, prune=False)   # counters only -- armed is otherwise a no-op
            return 0

        if cls == "main" and (frontend or state.get("pending_arm")):
            # Direct when this very fire is the frontend touch; drain when an earlier leaf (or a
            # degraded fire) recorded one and this main-loop fire is the first chance to spend it.
            via = "direct" if frontend else "drain"
            state["armed"] = True
            state["armed_at"] = time.time()
            state["armed_via"] = via
            state["pending_arm"] = False
            save_state(key, state)
            emit_arm(via)
            return 0

        if frontend and not state.get("pending_arm"):
            # Leaf or ambiguous fire on a frontend file: record, never inject.
            state["pending_arm"] = True
            state["pending_arm_at"] = time.time()
            state["pending_arm_via"] = cls
        save_state(key, state)
    except Exception:
        # Fail open on this hook's own bugs -- announced, unless we are (or may be) in a leaf,
        # where the no-output rule wins.
        if not leaf:
            _announce_error("arm", "arming check skipped")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
