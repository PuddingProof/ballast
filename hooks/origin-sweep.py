#!/usr/bin/env python
"""hooks/origin-sweep.py -- SessionStart hook. Reap the visual-stack ORIGIN processes left behind
by sessions that died without running their own teardown.

WHY THIS EXISTS: the visual redesign makes the orchestrator the sole owner of the origin (the one
server process a session starts so visual dispatches can capture a UI), torn down at close-out. A
crashed, killed, or force-quit session never reaches its close-out -- and its detached origin then
survives forever, holding a port and rendering a stale build. That is the exact orphan class the
redesign exists to kill, and no other actor can clean it: the session that would have is gone.

THE LINE THIS CROSSES, NAMED OUT LOUD: this is ballast's FIRST PROCESS-KILLING HOOK. The
destructive REAP half was deliberately parked repo-wide -- `dev-process-nudge.py` ships the
SessionStart-NUDGE half of exactly this problem and states in its own header that it contains no
process-killing, by design. That parking was not squeamishness: the objection was to killing by
process-NAME matching, where a guess about which node.exe is "the dev server" is an unverifiable
heuristic that can take out a user's unrelated work. This hook is not that, and inherits nothing
from it silently. The compensating controls, in full:

  1. LEDGER-SCOPED. Only processes a ballast session recorded ITSELF, in its own per-session
     ledger, are ever candidates. Nothing is discovered by scanning the process table for
     interesting-looking programs. A process nobody declared is invisible to this hook.
  2. DOUBLE FINGERPRINT VERIFICATION. A kill requires BOTH halves:
       (a) the live process at the recorded pid still matches the recorded start_time AND
           command-line fingerprint -- so pid reuse fails the check and is pruned, not killed; and
       (b) the recording session's OWN process (recorded as owner_pid + owner_start_time) is
           verifiably gone -- not a guess about session liveness, the same fingerprint test.
     Either half short of proof means the entry is left alone or pruned. Nothing is killed on a
     maybe.
  3. NEVER PROCESS-NAME MATCHING, EVER. No name, image path, or command-line substring takes part
     in any kill decision -- the parked objection remains honoured in full.
  4. RE-VERIFIED AT THE LAST MOMENT. Candidates are re-checked against a freshly enumerated
     process table immediately before signalling, and this session's own ancestor chain is
     excluded outright, so a corrupt ledger cannot name the running session's process tree.
  5. FAILS TOWARD NOT KILLING. An unreadable process table, an unreadable start time, an
     unidentified owner, or any parse failure all resolve to "leave it alone". The residual is a
     missed reap (the status quo before this hook), never a wrong kill.

POSTURE: soft/informational -- it never blocks anything and ALWAYS exits 0. It emits ONE
systemMessage when it actually did something (reaped and/or pruned) and prints nothing at all
otherwise, which also keeps run.sh's fire ledger honest. No additionalContext: a completed reap
asks nothing of the agent, and injected prose that changes no behaviour is pure recurring cost.

FAIL-OPEN GUARANTEE: the whole body runs under one broad try/except that returns 0. Per the
announce-on-error contract, an internal error still emits a best-effort one-liner (wrapped in its
own try/except so announcing can never change the exit code) naming the site and what was skipped.

NAMED RESIDUAL RISKS (accepted, not bugs):
  1. An entry whose owner could not be identified at record time is NEVER reaped by this hook --
     its liveness is unprovable. Its cleanup paths are the owning session's teardown and the
     stale-file housekeeping prune in the ledger library.
  2. A ledger containing any live-or-unknown-owner entry is not modified at all, so a live
     session's stale rows are its own business. This removes the concurrent-append race outright
     rather than narrowing it.
  3. Windows kills the process TREE (`taskkill /F /T`): an origin is routinely a launcher whose
     real listener is a child, so tree-killing is required for the reap to actually free the port.
     A descendant a user re-parented under the origin dies with it -- accepted, and bounded by the
     fact that the tree root was fingerprint-verified as a recorded origin.
"""
import json
import os
import sys


def _announce(site, skipped):
    """Announce-on-error one-liner (hooks/CLAUDE.md). Wrapped by the caller's try/except so a
    failure to announce can never affect the exit code."""
    try:
        sys.stdout.write(json.dumps({
            "systemMessage": "⚠️ ballast: origin-sweep — internal error (%s), %s"
                             % (site, skipped)
        }) + "\n")
    except Exception:
        pass


def build_message(result):
    """One-line summary, or None when the sweep changed nothing (-> print nothing)."""
    reaped = len(result.get("reaped") or [])
    pruned = result.get("pruned") or 0
    failed = len(result.get("failed") or [])
    if not (reaped or pruned or failed):
        return None
    parts = []
    if reaped:
        parts.append("reaped %d orphaned visual origin%s (owning session verified dead)"
                     % (reaped, "" if reaped == 1 else "s"))
    if failed:
        parts.append("%d could not be killed (left recorded for the next sweep)" % failed)
    if pruned:
        parts.append("pruned %d stale ledger entr%s (nothing killed)"
                     % (pruned, "y" if pruned == 1 else "ies"))
    return "\U0001f9f9 ballast: origin-sweep — " + ", ".join(parts)


def main():
    site = "startup"
    try:
        # The library lives beside this file. Import by path rather than relying on the caller's
        # cwd: run.sh dispatches us with an absolute path, so sys.path[0] is already this dir, but
        # an explicit insert makes that independent of how we were invoked.
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        site = "import"
        import visual_origin_ledger as ledger

        # Zero-cost exit for the overwhelmingly common case: no ledgers on disk means no process
        # enumeration is paid for at all (a SessionStart hook must not tax every session).
        site = "ledger scan"
        if not ledger.list_ledgers():
            return 0

        site = "sweep"
        try:
            result = ledger.sweep()
        except ledger.ProcessProbeError:
            # Could not read the process table -> we cannot prove anything about any entry, so
            # nothing is touched. Announced because a silent skip here would hide the fact that
            # orphan reaping is not running on this box.
            _announce("process enumeration", "no ledger swept and nothing killed")
            return 0

        site = "emit"
        message = build_message(result)
        if message:
            print(json.dumps({"systemMessage": message}))
    except Exception:
        # Fail open: never block or slow a session on this hook's own bug.
        _announce(site, "no ledger swept and nothing killed")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
