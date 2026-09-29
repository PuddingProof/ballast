#!/usr/bin/env python
"""hooks/dev-process-nudge.py -- SessionStart hook. Nudge when a process from a PRIOR session is
still running and references THIS project's directory.

WHY THIS EXISTS: two live incidents on 2026-07-19 (~20 min lost each) traced to a leftover dev
process/exe from an earlier session -- a dev server or built binary that outlived its session and
either held a port / lock the new session's launch then collided with, or split the app's state
across two concurrently-running instances. Both are the "concurrent dev instances racing over
shared window-state" facet HARNESS-RECS row 28 un-parks on. This hook ships the SessionStart-NUDGE
half of a deliberately split design: it only SURFACES the leftover process at session start so the
session can account for it before launching. The destructive SessionEnd-REAP half (killing a
survivor at close-out) remains PARKED by design -- this file contains NO process-killing.

POSTURE: soft nudge. It emits systemMessage + additionalContext when it finds a match and does
nothing otherwise; it never blocks, and it ALWAYS exits 0. There is no correctness/safety property
to enforce here -- a leftover process is a heads-up, not a violation -- so the injected wording is
worded to no-op gracefully on a false positive ("Expected? Ignore this.").

FAIL-OPEN GUARANTEE: the entire body of main() runs inside one broad try/except that falls through
to `return 0` (exit 0) with no stdout on ANY internal error -- a missing/empty CLAUDE_PROJECT_DIR,
a process-enumeration failure or timeout, malformed enumerator output, a JSON-encode error. A
SessionStart hook that crashes or stalls degrades every new session; a dead nudge is always
preferable to a broken one.

NAMED RESIDUAL RISKS (accepted, not bugs):
  1. cmdline-only matching: a process is matched only if its command line contains the project
     dir. A process linked to the project ONLY by its cwd (dir never appears on its command line)
     is missed. Reading per-process cwd portably (esp. on Windows) is far more expensive/fragile
     than the command-line scan, and the command line catches the common dev-server / built-exe
     case (the launch path names the project). Accepted miss, not fixed here.
  2. Name-based editor excludelist is incomplete: EXCLUDE_NAMES filters editors/shells/terminals
     that legitimately keep a project open across sessions, but it is a fixed name list -- a
     differently-named editor slips through as a false positive. The soft posture absorbs this:
     the nudge explicitly tells the reader to ignore an expected editor/persistent server.
  3. Substring matching is prefix-blind: project C:/proj/app also matches a process referencing a
     sibling C:/proj/app-extras (superstring path). A boundary-aware match would miss legitimate
     forms (quoting, trailing separators, embedded flags), so the loose match + soft posture wins.
  4. macOS `ps -o comm=` may return a full path containing spaces; split(None, 2) then shifts
     fields so the exclude-key sees a fragment. The match itself survives (args still lands the
     project dir in the scanned text) -- worst case is a false-positive nudge on an editor.
"""
import json
import os
import subprocess
import sys

# Names (basename, lowercased, ".exe" stripped) that legitimately reference a project across
# sessions and are NOT the leftover-dev-process signal: editors, shells, terminals, the OS shell,
# and this CLI itself. Matching one of these suppresses the nudge for that process.
#
# Deliberately ABSENT: node / python / cargo / vite / and other dev-runtime names. A lingering dev
# SERVER is exactly the signal this hook exists to surface -- excluding its runtime would blind the
# hook to the incident it was built for. Only genuinely-expected-to-persist tools are excluded.
EXCLUDE_NAMES = frozenset({
    "code", "code - insiders", "devenv", "explorer", "notepad", "notepad++",
    "powershell", "pwsh", "cmd", "bash", "sh", "zsh", "conhost",
    "windowsterminal", "wt", "claude", "searchindexer",
})

# Cap on how many matched processes the injected context enumerates -- the count in systemMessage
# still reflects the true total; the list is just bounded so a pathological match set can't bloat
# the injected prompt.
MAX_LISTED = 8
CMDLINE_TRUNC = 120

GUIDANCE = (
    "Leftover from a prior session and referencing this project: reuse or stop each deliberately "
    "before launching a dev instance or a build. Expected (your editor, an intentionally "
    "persistent app)? Ignore this."
)


def enumerate_processes():
    """Return a list of {"pid":..., "name":..., "cmdline":...} for every running process.

    BALLAST_DEV_PROCESS_FAKE is a hermetic-TEST seam only: when set, it names a JSON file holding
    a list of process dicts, which is returned verbatim (normalized to the common shape). This lets
    the suite exercise match/emit logic without ever touching live process enumeration. PRODUCTION
    NEVER SETS THIS VAR -- the real OS branches below run only when it is absent.
    """
    fake = os.environ.get("BALLAST_DEV_PROCESS_FAKE")
    if fake:
        with open(fake, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            {"pid": p.get("pid"), "name": p.get("name") or "", "cmdline": p.get("cmdline") or ""}
            for p in data
        ]

    if os.name == "nt":
        # Win32_Process via CIM is the portable, dependency-free way to get every process's full
        # command line on Windows (the plain `tasklist` has no command-line column). ConvertTo-Json
        # collapses a single-process result to a bare object rather than a 1-element array, so the
        # single-object case is wrapped below. -NoProfile/-NonInteractive keep it fast and unattended.
        proc = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine "
                "| ConvertTo-Json -Compress",
            ],
            # utf-8 + errors="replace", NOT text=True: text mode decodes with the locale codec
            # (cp1252 on Windows) while powershell.exe writes the pipe in the OEM code page -- one
            # process with an undecodable byte in its CommandLine would raise UnicodeDecodeError
            # and silently kill the whole nudge via the fail-open. ConvertTo-Json \\uXXXX-escapes
            # non-ASCII anyway, and the project-dir needle we match is taken from the local path,
            # so replacement characters can only ever mangle text we were not matching on.
            capture_output=True, encoding="utf-8", errors="replace", timeout=8,
        )
        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "pid": d.get("ProcessId"),
                "name": d.get("Name") or "",
                "cmdline": d.get("CommandLine") or "",
            }
            for d in data
        ]

    # POSIX. A single `-o` format arg of comma-separated, "="-suffixed headers is the portable
    # ps form: the trailing "=" on each column suppresses the header row (so there is no header
    # line to skip), and `pid=,comm=,args=` is understood by both BSD and GNU ps. `args` is last
    # because it contains embedded spaces -- split(None, 2) (maxsplit=2) keeps the whole command
    # line intact in the third field instead of shredding it on its internal whitespace.
    proc = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True, encoding="utf-8", errors="replace", timeout=8,
    )
    procs = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        procs.append({
            "pid": parts[0],
            "name": parts[1],
            "cmdline": parts[2] if len(parts) >= 3 else "",
        })
    return procs


def _proc_name_key(name):
    """basename, lowercased, ".exe" stripped -- the form compared against EXCLUDE_NAMES."""
    base = os.path.basename((name or "").replace("\\", "/").rstrip("/"))
    base = base.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def match_processes(project_dir, procs):
    """Return the processes whose command line references project_dir and whose name is not excluded.

    A process matches iff BOTH hold:
      (1) its cmdline contains project_dir under EITHER separator form -- we build both the
          forward-slash and backslash variants so a Git-Bash-style forward-slash project dir still
          matches a native-Windows backslash command line (and vice versa). Comparison is
          case-insensitive on Windows (os.name == "nt"), where paths are not case-sensitive.
      (2) its name (basename, lowercased, ".exe" stripped) is NOT in EXCLUDE_NAMES.
    """
    ci = os.name == "nt"
    variants = [project_dir.replace("\\", "/"), project_dir.replace("/", "\\")]
    if ci:
        variants = [v.lower() for v in variants]

    # Self/parent-pid exclusion: when the plugin root sits inside the project dir (a
    # directory-source install, as here), THIS hook's own `python .../hooks/dev-process-nudge.py`
    # invocation -- and the run.sh bash wrapper that spawned it -- both have the project dir on
    # their own command line, so without this they self-match every single run (4x live-confirmed).
    # String compare, not int: the CIM path yields ints for pid, but the `ps` fallback and the
    # BALLAST_DEV_PROCESS_FAKE test seam both yield strings, so normalize to string on both sides.
    own_pids = {str(os.getpid()), str(os.getppid())}

    matches = []
    for p in procs:
        if str(p.get("pid")) in own_pids:
            continue
        cmdline = p.get("cmdline") or ""
        hay = cmdline.lower() if ci else cmdline
        if not any(v and v in hay for v in variants):
            continue
        if _proc_name_key(p.get("name")) in EXCLUDE_NAMES:
            continue
        matches.append(p)
    return matches


def build_output(matches):
    """Build the hook's JSON payload (a dict) for a non-empty match set."""
    n = len(matches)
    noun = "process" if n == 1 else "processes"
    listed = []
    for p in matches[:MAX_LISTED]:
        cmdline = p.get("cmdline") or ""
        if len(cmdline) > CMDLINE_TRUNC:
            # Ellipsis marks the cut -- without it a truncated path (e.g.
            # "...hooks/dev-process-nudg") silently reads as complete.
            cmdline = cmdline[:CMDLINE_TRUNC] + "…"
        listed.append("PID %s %s -- %s" % (p.get("pid"), p.get("name"), cmdline))
    additional_context = "\n".join(listed) + "\n\n" + GUIDANCE
    return {
        "systemMessage": "\U0001f526 ballast: dev-process-nudge -- %d %s already referencing this project"
        % (n, noun),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        },
    }


def main():
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if not project_dir:
            return 0  # no project context -> nothing to compare against; stay silent
        procs = enumerate_processes()
        matches = match_processes(project_dir, procs)
        if not matches:
            return 0  # zero matches -> print nothing (keeps run.sh's fire ledger clean)
        print(json.dumps(build_output(matches)))
    except Exception:
        # Fail open: any internal error -> exit 0 with no output. build_output() is fully
        # constructed before print(), so a JSON-encode failure emits nothing rather than a
        # half-line.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
