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
#     gated docs (the f646d0d fix; see is_durable).
#
# WHY a hard gate for the top tier (provenance): the soft-only PostToolUse design was empirically
# defeated -- an agent wrote "gate satisfied by construction" straight past two soft reminders. The
# postmortem chain 2026-06-23 -> 07-07 escalated the miss 5x; a non-blocking nudge cannot force the
# gate, so the permanent tier now BLOCKS pre-write (mirrors askuserquestion-recommend.py's exit-2
# point-of-use enforcement). The soft nudge is retained for the reversible ephemeral tier, where a
# post-write "fix it with a follow-up Edit" reminder is actionable and a block would be overkill.
#
# SCOPE (governance review item): the attestation marker is an authoring-discipline record --
# it self-attests that the durable-docs gate ran THIS session -- not an integrity control. It is
# NOT tamper-evident and makes no NIST SI-7-style independent-reference claim; treat it as "did
# the author pause to run the gate", never as proof a doc wasn't altered out-of-band. A user
# running their OWN doc-governance/integrity regime over paths this guard would otherwise gate
# (e.g. externally-managed governance files they deliberately re-baseline) can permanently
# exclude them via ~/.claude/ballast/docguard-exclude -- one fnmatch glob per line, matched
# case-insensitively against the full normalized path; see load_exclude_patterns() below.
#
# WHY python, not bash (the sibling hooks are bash): the payload's tool_input.file_path is a Windows
# path full of backslashes. Round-tripping that JSON through `printf | jq/python` in bash corrupts the
# \-escapes and bash backslash normalization is unreliable. python parses the JSON and normalizes the
# path natively. Matches the python-hook precedent (askuserquestion-recommend.py, package-install-guard.py).
#
# NOISE CONTROL: PostToolUse de-dupes per (session, file) so it fires at most once per file per session,
# and prunes its marker cache (shared with the gate markers) so it can't grow without bound. The
# PreToolUse gate FAILS OPEN on any internal error -- a broken guard must NEVER brick all file writes --
# but (governance review item) not SILENTLY: every internal-error fail-open path below makes a
# best-effort systemMessage announcement before returning/exiting, each wrapped in its own
# try/except so the announce itself can never change the exit code or crash a path that must stay 0.

import sys, json, os, hashlib, time, glob, fnmatch


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
# R1 fix (postmortem chain 2026-07-09 -> 07-11: 6 false fires, 0 true, across 4 sessions): session-
# OUTPUT areas are not durable-doc AUTHORING surfaces -- .notes/ distillations (working notes, plans,
# feedback seeds) and postmortem dirs (reports + generated registry/ledger state like
# SWEEP-STATE.md) are written by flows that already carry their own discipline (session-postmortem,
# harness-sweep). Excluded FIRST in is_durable, so the "/.claude/*.md" catch-all can't re-catch them.
SESSION_OUTPUT_SEGMENTS = {".notes", "postmortem", "postmortems"}
# Same session-output class, homed under a .claude/ root instead of .notes/: a recurring skill's own
# digest series, sibling to .claude/postmortem/. Matched as a PAIR with ".claude" -- deliberately NOT
# as bare segments -- because a series dir is named after the skill that writes it, and that same name
# is also the shipped skill dir (skills/harness-sweep/), which IS a durable authoring surface and must
# keep nudging. Without this, the "/.claude/*.md" catch-all below re-catches every digest write.
CLAUDE_OUTPUT_SEGMENTS = {"harness-sweep", "changelog-digests"}


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
    # User exclusion list (governance review item): <home_root>/ballast/docguard-exclude, one
    # fnmatch glob per line. Blank lines and lines starting with "#" are ignored; every pattern
    # is backslash->slash normalized and lowercased at load time, mirroring normalize()'s output
    # shape, so matching is genuinely case- and separator-insensitive regardless of
    # fnmatch.fnmatch's OS-dependent normcase behavior (case-sensitive on POSIX, case-insensitive
    # on Windows -- we don't want to depend on that) and of which slash style the user pasted
    # (an Explorer/PowerShell copy is backslashed, and a synced dotfile read on POSIX gets no
    # normcase rescue). utf-8-sig: strips a leading BOM when present (Notepad's default UTF-8
    # flavor), a no-op for BOM-less files -- a BOM left on line 1 would make that pattern
    # silently never match. Fails open on ANY error, including the missing-file case (the
    # normal, expected state for most installs) -- no exclusions, guard stays fully active.
    # Broad try/except, consistent with this file's fail-open-on-own-bugs doctrine; a malformed
    # or binary exclusion file must never itself become a new way to break every doc write.
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
    # Prune markers (gate + dedupe both live here) older than 2 days -- stale sessions won't accrue.
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
            "authoring gate has not run this session. Run the durable-docs skill before writing it -- or "
            "the skill-forge skill for SKILL.md craft (its Step 0 runs the same gate) -- whose completion "
            "writes the attestation marker that unblocks this write. Already ran the gate this session "
            "(or this is a false match)? Manually attest, in the Bash tool: touch ~/.claude/.cache/docguard/gate-%s"
            " -- Running your own doc-governance/integrity regime over this path? Permanently exclude it "
            "via a glob line in ~/.claude/ballast/docguard-exclude."
            % (fp, session_id),
            file=sys.stderr,
        )
        return 2
    except Exception:
        # Fail open -- never brick a write on a guard bug -- but announce it (governance review
        # item): pre_gate's error path used to be silently return 0 with no trace it ever fired.
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
    if already_nudged(d.get("session_id") or "nosession", p):
        return
    reminder = SKILL_REMINDER if base == "skill.md" else REMINDER
    print(json.dumps({
        "systemMessage": "📐 ballast: doc-write-guard — durable-doc altitude reminder injected",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reminder}}))


def main():
    try:
        d = json.loads(sys.stdin.read())
    except Exception:
        # Malformed payload -> never block, never nudge -- but announce (governance review item):
        # this used to be a silent exit 0.
        _announce_error("payload parse", "guard skipped")
        sys.exit(0)
    if not isinstance(d, dict):
        # Valid JSON but not an object -> same fail-open contract (the .get() below would
        # AttributeError -> exit 1; same class as the askuserquestion-recommend 2026-07-11 fix).
        # Announced for the same reason as the malformed-payload branch above (same site tag --
        # both are payload-shape failures caught before event dispatch).
        _announce_error("payload parse", "guard skipped")
        sys.exit(0)
    event = d.get("hook_event_name") or ""
    if event == "PreToolUse":
        sys.exit(pre_gate(d))
    if event == "PostToolUse":
        try:
            post_nudge(d)
        except Exception:
            # An uncaught post_nudge bug used to exit 1 with a traceback (silent to the user,
            # loud only in a log nobody watches) -- wrap it in the same fail-open + announce
            # contract as every other error path in this file (governance review item).
            # No permission-flow clause: post_nudge (PostToolUse) makes no permission decision at
            # all, so claiming one would be false.
            _announce_error("post_nudge", "nudge skipped", permission_flow=False)
    sys.exit(0)


if __name__ == "__main__":
    main()
