#!/usr/bin/env python
# Global durable-doc guard on Write|Edit|MultiEdit. Registered on BOTH PreToolUse and PostToolUse
# (same matcher in hooks/hooks.json); branches on the payload's hook_event_name.
#
# TWO TIERS:
#   - PreToolUse ATTESTATION GATE (HARD, exit 2) for permanent-class docs -- CLAUDE.md / AGENTS.md,
#     any skills/**/SKILL.md, a .claude commands/*.md, and hook scripts (.py|.sh|.ps1).
#     These are the docs whose bad edits ossify across every future session, so a write is DENIED
#     until the durable-docs authoring gate has run this session (it drops an attestation marker).
#   - PostToolUse SOFT NUDGE for ephemeral-tier durable docs (memory, specs, brainstorm/design,
#     README, ideas.md, the /.claude/*.md catch-all). A post-write "re-check what you just wrote"
#     reminder, fired at most once per (session, file). Session-output dirs (.notes, postmortem/s)
#     are excluded entirely via SESSION_OUTPUT_SEGMENTS -- deterministic session artifacts, not
#     gated docs (see is_durable).
#
# WHY a hard gate for the top tier: a non-blocking nudge cannot force the gate -- the soft-only
# design was empirically defeated, so the permanent tier BLOCKS pre-write (mirrors
# askuserquestion-recommend.py's exit-2 point-of-use enforcement). The soft nudge is retained for
# the reversible ephemeral tier, where "fix it with a follow-up Edit" is actionable.
#
# SCOPE: the attestation marker is an authoring-discipline record -- "did the author pause to run
# the gate" -- not an integrity control. It is NOT tamper-evident and is never proof a doc wasn't
# altered out-of-band. A user running their OWN doc-governance/integrity regime over gated paths
# can permanently exclude them via ~/.claude/ballast/docguard-exclude -- one fnmatch glob per line,
# matched case-insensitively against the full normalized path; see load_exclude_patterns().
#
# WHY python, not bash (the sibling hooks are bash): tool_input.file_path is a Windows path full of
# backslashes, and round-tripping that JSON through `printf | jq/python` in bash corrupts the
# \-escapes. python parses and normalizes the path natively.
#
# NOISE CONTROL: PostToolUse de-dupes per (session, file) AND rate-limits the session as a whole to
# one nudge per BURST_WINDOW_SECONDS, then prunes its marker cache so it can't grow without bound.
# Every internal-error path FAILS OPEN -- a broken guard must NEVER brick all file writes -- but not
# silently: each makes a best-effort systemMessage announcement, wrapped in its own try/except so
# the announce can never change an exit code that must stay 0.

import sys, json, os, hashlib, time, glob, fnmatch

# Soft-nudge burst window: at most one emitted nudge per session per this many seconds. The
# per-(session, file) dedupe below is correct across a whole session but says nothing about a BATCH
# -- an N-file write batch in one turn earned N identical reminders, and one reminder per window
# carries the same steering at 1/N the context. A file suppressed by the window keeps its per-path
# marker UNWRITTEN, so it can still earn its own nudge later in the session, and the suppression is
# recorded to suppressed.log so an audit can tell it apart from "never matched". The window applies
# to the GENERIC reminder only -- the skill-forge reminder is distinct and rare, so it bypasses
# (see post_nudge).
BURST_WINDOW_SECONDS = 180


def _announce_error(site, skipped, permission_flow=True):
    # Best-effort, silent-on-its-own-failure systemMessage so a persistently-crashing guard stays
    # visible instead of quietly going dead every call. Never raises; never affects the exit code.
    # `site` names which of the three fail-open paths fired (pre_gate / payload parse / post_nudge);
    # `permission_flow` is False for post_nudge, which makes no permission decision at all -- only
    # pre_gate (the PreToolUse attestation gate) and the payload-parse paths defer a permission
    # decision, so only those two carry the "deferred to native permission flow" clause.
    msg = "⚠️ ballast: doc-write-guard — internal error (%s), %s" % (site, skipped)
    if permission_flow:
        msg += "; deferred to native permission flow"
    try:
        print(json.dumps({"systemMessage": msg}))
    except Exception:
        pass

# --- Reminder strings (each self-contained + carries a graceful-ignore clause, per durable-docs). ----
REMINDER = ("Writing or editing durable documentation? Run the durable-docs skill. "
            "(Skill-run gated output, or matched by accident? Ignore this.)")
# A SKILL.md is durable, but its CRAFT (baseline-first testing, eval, trigger-tuning) is skill-forge's
# job -- durable-docs still owns whether it should be a skill + where it lives, which skill-forge
# consults at its Step 0. (SKILL.md is hard-tier now, so this only fires if one reaches the soft path.)
SKILL_REMINDER = ("Authoring or editing a SKILL.md? Run the skill-forge skill for the craft "
                  "(baseline-first testing, eval, trigger-tuning); its Step 0 runs the durable-docs gate "
                  "for you. (Skill-run gated output, or matched by accident? Ignore this.)")

# Soft-tier basenames + segments that mark an ephemeral durable-doc area (hard tier is handled separately).
DURABLE_BASENAMES = {"claude.md", "agents.md", "projects.md", "memory.md", "skill.md", "ideas.md"}
DURABLE_SEGMENTS  = {"memory", "specs", "spec", "design", "designs"}
# Session-OUTPUT areas are not durable-doc AUTHORING surfaces -- .notes/ distillations and
# postmortem dirs (reports plus generated registry state like SWEEP-STATE.md) are written by flows
# that carry their own discipline (session-postmortem, harness-sweep). Excluded FIRST in is_durable,
# so the "/.claude/*.md" catch-all can't re-catch them.
SESSION_OUTPUT_SEGMENTS = {".notes", "postmortem", "postmortems"}
# Same session-output class, homed under a .claude/ root instead of .notes/: a recurring skill's own
# digest series, sibling to .claude/postmortem/. Matched as a PAIR with ".claude" -- deliberately NOT
# as bare segments -- because a series dir is named after the skill that writes it, and that same name
# is also the shipped skill dir (skills/harness-sweep/), which IS a durable authoring surface and must
# keep nudging. Without this, the "/.claude/*.md" catch-all below re-catches every digest write.
CLAUDE_OUTPUT_SEGMENTS = {"harness-sweep", "changelog-digests", "adversarial-audit"}


def normalize(path):
    # Windows-safe: backslash -> slash, lowercased. Returns (normalized_path, segments, basename).
    p = path.replace("\\", "/").lower()
    segs = p.split("/")
    return p, segs, segs[-1]


def is_excluded(p, segs):
    # Excluded from BOTH events. plans/ under a .claude dir belongs to plan-authoring.sh (double-fire
    # defect otherwise); the cache/build dirs are never durable docs.
    if ".cache" in segs or "__pycache__" in segs or "node_modules" in segs:
        return True
    if ".claude" in segs and "plans" in segs and segs.index("plans") > segs.index(".claude"):
        return True
    return False


def load_exclude_patterns():
    # User exclusion list: <home_root>/ballast/docguard-exclude, one fnmatch glob per line. Blank
    # lines and "#" comments are ignored. Every pattern is backslash->slash normalized and
    # lowercased at load time, mirroring normalize()'s output shape, so matching never depends on
    # fnmatch.fnmatch's OS-dependent normcase (case-sensitive on POSIX, insensitive on Windows) nor
    # on which slash style the user pasted. utf-8-sig strips a leading BOM (Notepad's default UTF-8
    # flavor) that would otherwise make line 1's pattern silently never match. Fails open on ANY
    # error, including the missing file (the normal state for most installs) -- no exclusions,
    # guard stays fully active; a malformed exclusion file must never break every doc write.
    # No caching: each event path checks user_excluded at most once per (short-lived) process.
    patterns = []
    try:
        path = os.path.join(home_root(), "ballast", "docguard-exclude")
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line.replace("\\", "/").lower())
    except Exception:
        patterns = []  # missing file (the common case) or any read/decode error -> no exclusions
    return patterns


def user_excluded(p):
    # p is the already-normalized (lowercased, forward-slash) path from normalize(). Matched
    # against BOTH tiers (hard gate + soft nudge) -- see pre_gate/post_nudge callsites.
    return any(fnmatch.fnmatch(p, pat) for pat in load_exclude_patterns())


def is_hard(segs, base):
    # Permanent-class docs -> PreToolUse attestation gate.
    if base in {"claude.md", "projects.md", "agents.md"}:
        return True
    if base == "skill.md" and "skills" in segs:            # skills/**/SKILL.md
        return True
    if base.endswith(".md") and "commands" in segs and ".claude" in segs:  # .claude/**/commands/*.md
        return True
    if "hooks" in segs and base.endswith((".py", ".sh", ".ps1")):  # hook scripts carry injected prompts
        return True
    return False


def is_durable(p, segs, base):
    # Soft tier: everything the old is_durable matched. (Hard-tier paths are routed out before this.)
    if any(s in SESSION_OUTPUT_SEGMENTS for s in segs):    # R1: session-output areas never nudge
        return False
    if ".claude" in segs and any(s in CLAUDE_OUTPUT_SEGMENTS for s in segs):   # skill digest series
        return False
    if base in DURABLE_BASENAMES or base.startswith("readme"):
        return True
    if any(s in DURABLE_SEGMENTS or s.startswith("brainstorm") for s in segs):
        return True
    if "handoff" in p:
        return True
    if "/.claude/" in p and base.endswith(".md"):          # any markdown living under a .claude dir
        return True
    if "/hooks/" in p and base.endswith((".py", ".sh")):   # hook scripts (also caught by is_hard)
        return True
    return False


def home_root():
    # BALLAST_CLAUDE_HOME: hermetic-test override (hooks/CLAUDE.md rule) -- production never sets it,
    # so gate/dedupe markers AND the user exclusion file stay under the real ~/.claude. Shared by
    # cache_dir() (below) and load_exclude_patterns() so the two agree on "which .claude" without
    # duplicating the resolution logic.
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def cache_dir():
    d = os.path.join(home_root(), ".cache", "docguard")
    os.makedirs(d, exist_ok=True)
    return d


def prune(cache):
    # Prune markers (gate + dedupe + burst-window `last-*` all live here) older than 2 days --
    # stale sessions won't accrue. Deliberately a bare `*` glob, not a name pattern, so every
    # marker family this dir grows is reaped without a second edit here.
    cutoff = time.time() - 2 * 86400
    for f in glob.glob(os.path.join(cache, "*")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def already_nudged(session_id, norm_path):
    # PostToolUse dedupe marker keyed by (session, file). Any failure -> treat as "not nudged".
    try:
        cache = cache_dir()
        prune(cache)
        key = "%s-%s" % (session_id, hashlib.sha1(norm_path.encode("utf-8")).hexdigest()[:16])
        marker = os.path.join(cache, key)
        if os.path.exists(marker):
            return True
        open(marker, "w").close()
    except Exception:
        pass
    return False


def burst_marker(session_id):
    return os.path.join(cache_dir(), "last-%s" % session_id)


def in_burst_window(session_id):
    # True only if this session EMITTED a nudge less than BURST_WINDOW_SECONDS ago. Any failure
    # (no marker -- the normal first-nudge state -- unreadable dir, clock surprise) reads as False,
    # i.e. fail open to the pre-window behavior: nudge.
    try:
        return (time.time() - os.path.getmtime(burst_marker(session_id))) < BURST_WINDOW_SECONDS
    except Exception:
        return False


def log_suppressed(norm_path):
    # A burst-suppressed fire leaves NO row anywhere: run.sh's fire ledger records the hook PROCESS,
    # not its decision, and a suppressed nudge prints nothing. Without this line an audit cannot
    # tell "suppressed by the window" from "never matched a durable doc" -- two very different
    # answers to "why did the guard stay quiet". Best-effort: an unwritable log changes nothing.
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(os.path.join(cache_dir(), "suppressed.log"), "a", encoding="utf-8") as f:
            f.write("%s suppressed %s\n" % (stamp, norm_path))
    except Exception:
        pass


def touch_burst_marker(session_id):
    # Called ONLY on an actually-emitted nudge -- the window measures emissions, not attempts, so a
    # suppressed file neither refreshes the window nor burns its own per-path marker. Best-effort.
    try:
        open(burst_marker(session_id), "w").close()
    except Exception:
        pass


def pre_gate(d):
    # HARD attestation gate. Returns the exit code (2 = block, 0 = allow). Wrapped fail-open: any
    # internal exception -> 0, so a broken guard can never wedge all file writes.
    try:
        fp = (d.get("tool_input") or {}).get("file_path") or ""
        if not fp:
            return 0
        p, segs, base = normalize(fp)
        # user_excluded LAST: it is the only file-backed check in the chain, so the free
        # structural tests bail out first on the overwhelmingly-common non-durable write --
        # the exclusion file is only ever opened for paths already in the gate's business.
        if is_excluded(p, segs) or not is_hard(segs, base) or user_excluded(p):
            return 0  # excluded (built-in or user glob) / soft / non-durable -> not the gate's business
        session_id = d.get("session_id") or "nosession"
        cache = cache_dir()
        prune(cache)
        if os.path.exists(os.path.join(cache, "gate-%s" % session_id)):
            return 0  # gate already attested this session -> allow silently
        print(
            "BLOCKED -- doc-write-guard: %s is a permanent-tier durable doc (CLAUDE.md / AGENTS.md / "
            "a SKILL.md / a .claude commands/*.md / a hook script), and the durable-doc "
            "authoring gate has not run this session. Run the durable-docs skill -- or the skill-forge "
            "skill for SKILL.md craft, whose Step 0 runs the same gate -- to write the attestation "
            "marker that unblocks this write. Already ran it this session, or this is a false match? "
            "Attest in the Bash tool: touch ~/.claude/.cache/docguard/gate-%s. "
            "Running your own doc-governance/integrity regime over this path? Exclude it with a glob "
            "line in ~/.claude/ballast/docguard-exclude."
            % (fp, session_id),
            file=sys.stderr,
        )
        return 2
    except Exception:
        # Fail open -- never brick a write on a guard bug -- but announced, never silent.
        _announce_error("pre_gate", "write gate skipped")
        return 0


def post_nudge(d):
    # SOFT tier only. Hard tier is handled by the gate (no post-nudge); excluded/non-durable stay silent.
    fp = (d.get("tool_input") or {}).get("file_path") or ""
    if not fp:
        return
    p, segs, base = normalize(fp)
    # Same ordering rationale as pre_gate: the file-backed user_excluded check runs last.
    if is_excluded(p, segs) or is_hard(segs, base) or not is_durable(p, segs, base) or user_excluded(p):
        return
    session_id = d.get("session_id") or "nosession"
    # SKILL_REMINDER-class paths BYPASS the window: the skill-forge steering is a DISTINCT and rare
    # message, and a generic durable-docs reminder emitted seconds earlier must not eat it. Both
    # classes stay subject to the per-path dedupe below, and both touch the marker when they emit.
    is_skill = base == "skill.md"
    # Burst window BEFORE the per-path dedupe on purpose: already_nudged() writes the per-path
    # marker as a side effect, and a file suppressed here has not been nudged about yet.
    # Residual, accepted: the check and touch_burst_marker() are not atomic, so two overlapping
    # fires may both emit -- worst case equals the pre-window behavior of one nudge per file.
    if not is_skill and in_burst_window(session_id):
        log_suppressed(p)
        return
    if already_nudged(session_id, p):
        return
    reminder = SKILL_REMINDER if is_skill else REMINDER
    touch_burst_marker(session_id)
    print(json.dumps({
        "systemMessage": "📐 ballast: doc-write-guard — durable-doc altitude reminder injected",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reminder}}))


def main():
    try:
        d = json.loads(sys.stdin.read())
    except Exception:
        # Malformed payload -> never block, never nudge -- but announced, never silent.
        _announce_error("payload parse", "guard skipped")
        sys.exit(0)
    if not isinstance(d, dict):
        # Valid JSON but not an object -> same fail-open contract (the .get() below would
        # AttributeError -> exit 1). Same site tag: both are pre-dispatch payload-shape failures.
        _announce_error("payload parse", "guard skipped")
        sys.exit(0)
    event = d.get("hook_event_name") or ""
    if event == "PreToolUse":
        sys.exit(pre_gate(d))
    if event == "PostToolUse":
        try:
            post_nudge(d)
        except Exception:
            # Same fail-open + announce contract as every other error path here. No
            # permission-flow clause: PostToolUse makes no permission decision to defer.
            _announce_error("post_nudge", "nudge skipped", permission_flow=False)
    sys.exit(0)


if __name__ == "__main__":
    main()
