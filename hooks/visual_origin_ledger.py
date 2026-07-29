#!/usr/bin/env python
"""hooks/visual_origin_ledger.py -- the visual-stack ORIGIN LEDGER: shared library + CLI.

WHAT AN ORIGIN IS: the single long-lived server process ("origin") a main session starts so that
visual dispatches can capture a rendered UI. The orchestrator owns its lifecycle -- starts it once,
records it here, tears it down at close-out. Leaves never start or stop one.

WHY A LEDGER: a crashed or force-killed session never runs its own teardown, so its detached origin
survives forever -- the orphan class the visual redesign exists to kill. A ledger of SELF-DECLARED
entries turns that into a decidable question: a later session can ask "is the process I recorded
still the process I recorded, and is the session that recorded it gone?" -- and only then act.

THIS MODULE IS SHARED CODE ON PURPOSE. ballast's guards deliberately do NOT import each other
(see ballast-allow.py's home_root() note): two independently fail-open guards that merely share a
*convention* are safer duplicated than coupled. This is the opposite shape -- one subsystem's
on-disk state format with two readers (the origin-sweep hook and the bin/ CLI). Duplicating a
kill-decision format across two files is the actual hazard here: a drift between writer and reader
is either a missed reap or, far worse, a wrong kill.

STATE LOCATION (never the plugin dir -- it is replaced wholesale on plugin update):
    <home>/.cache/ballast-visual/origins-<session-key>.jsonl
  <home> = $BALLAST_CLAUDE_HOME (hermetic-test override; production never sets it) else ~/.claude.
  <session-key> = the payload/CLI transcript_path's filename stem (no .jsonl), with the documented
  `session_id` as fallback -- one file per session, so one session's teardown can never touch a
  concurrent session's rows.

ENTRY SCHEMA (JSONL, one object per line, keys exactly):
    pid, start_time, cmdline_fp   -- fingerprint of the ORIGIN process
    url, out_dir, started_at      -- human/operator payload
    owner_pid, owner_start_time   -- fingerprint of the RECORDING SESSION's own claude process
  The owner pair is what makes "the owning session is dead" a *verifiable* test rather than a
  heuristic. `start_time` is an opaque, exactly-comparable string from the platform enumerator
  (never parsed, only compared); `cmdline_fp` is sha256(command line)[:16] -- a hash, so the ledger
  stays compact and does not mirror full command lines (which can carry tokens) into a state file.

FINGERPRINT SEMANTICS (the whole safety argument):
  A pid alone is meaningless -- pids are reused. A process "is" the recorded one only if the live
  process at that pid ALSO matches the recorded start_time and cmdline_fp. Any mismatch means the
  recorded process is gone: the entry is PRUNED and nothing is killed. Process *names* are never
  consulted for any kill decision, ever.

PROBE ORDER for the process table (documented because a wrong answer here decides a kill):
  0. $BALLAST_VISUAL_PROC_FAKE -- hermetic TEST seam only (a JSON file of process dicts).
     Production never sets it; the real branches below run only when it is absent.
  1. Windows: `powershell.exe` + `Get-CimInstance Win32_Process`, selecting ProcessId,
     ParentProcessId, CommandLine and a UTC round-trip ("o") format of CreationDate. This is the
     dependency-free way to get full command lines on Windows (`tasklist` has no such column) and
     matches dev-process-nudge.py's existing enumerator. psutil is NOT assumed anywhere.
  2. Windows: `pwsh` (PowerShell 7) with the same command, for boxes where Windows PowerShell is
     absent or disabled.
  3. Windows: `wmic process get ...` CSV -- legacy fallback only. Verified ABSENT on Windows 11
     26200 (wmic is deprecated/removed in 24H2+), so it exists for older boxes and nothing else.
  4. POSIX: `ps -eo pid=,ppid=,lstart=,args=`. `lstart` is the only widely portable ABSOLUTE start
     time (BSD + GNU); `etime` is elapsed-since-boot-style and changes between calls, so it cannot
     serve as an equality-comparable fingerprint. If lstart is unavailable the probe FAILS rather
     than degrading -- see the fail direction below.
  A probe failure raises ProcessProbeError. It never returns an empty table, because "I could not
  enumerate" and "that process is dead" must never be the same answer.

FAIL DIRECTIONS (each chosen so the failure mode is a missed reap, never a wrong kill):
  - Enumeration unavailable            -> no sweep at all; nothing pruned, nothing killed.
  - Origin fingerprint does not verify -> PRUNE the entry, kill nothing.
  - Owner pid present but its start_time is unreadable -> treated as ALIVE (never reaped).
  - Owner pid absent from the entry (detection failed at record time) -> treated as UNKNOWN, never
    reaped. Named residual: such a ledger is only cleaned by its own teardown, or by the pure
    housekeeping prune below.
  - Housekeeping prune: a ledger file older than LEDGER_STALE_DAYS whose entries ALL fail origin
    verification (so there is provably nothing left to kill) is deleted. This path can never kill.
  - Self-protection: a pid in this process's own ancestor chain is never killed, whatever a
    (corrupt or hostile) ledger says.

ENV CONTRACT (production, not a test seam):
  BALLAST_ORIGIN_ANCHOR_PID -- set by bin/ballast-visual-origin, which measured that the Win32
                              ancestor chain is unrecoverable from this side on Windows. It only
                              chooses where the owner walk starts; see anchor_pid().

TEST SEAMS (both are hermetic-test-only; production sets neither, and both fail SAFE):
  BALLAST_VISUAL_PROC_FAKE -- JSON file: [{"pid":.., "ppid":.., "start_time":"..",
                              "cmdline":".."}, ...] returned verbatim as the process table.
  BALLAST_VISUAL_KILL_LOG  -- when set, kills are APPENDED to that file instead of executed, so a
                              suite can exercise the full reap decision without signalling anything.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# How much older than THIS process an ancestor must be to be taken as the owning session process.
# See detect_owner() for why the direction of this threshold is the safety property.
OWNER_MIN_AGE_S = 20

# Housekeeping-only prune age for a ledger file that provably references no live process.
LEDGER_STALE_DAYS = 7

# Bounds every external enumerator execution. An unbounded probe can HANG instead of failing --
# strictly worse, because a hang stalls the session and makes every fail-open path below it
# unreachable (hooks/CLAUDE.md; the Windows Store python3 alias incident).
PROBE_TIMEOUT_S = 20
KILL_TIMEOUT_S = 15

ENTRY_KEYS = (
    "pid", "start_time", "cmdline_fp",
    "url", "out_dir", "started_at",
    "owner_pid", "owner_start_time",
)

# Guard against an unbounded/cyclic parent chain in a malformed process table.
MAX_ANCESTRY_HOPS = 40

_PS_CIM_COMMAND = (
    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,"
    # The `if ($_.CreationDate)` guard matters: a process whose CreationDate is unreadable would
    # otherwise raise "method call on a null-valued expression" per row. The row must still SURVIVE
    # with a null time -- a missing row would read as "process is dead", and for an owner pid that
    # is exactly the difference between leaving a live session alone and reaping its origin.
    "@{n='Created';e={if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') }}}"
    " | ConvertTo-Json -Compress"
)


class ProcessProbeError(Exception):
    """The process table could not be read. Distinct from "the process is not running" by design."""


# ---------------------------------------------------------------------------
# Paths / keys
# ---------------------------------------------------------------------------

def claude_home():
    """BALLAST_CLAUDE_HOME (hermetic-test override; production never sets it) else ~/.claude.
    Mirrors the resolver in run.sh / commit-review-gate.py -- deliberately never the plugin dir."""
    return os.environ.get("BALLAST_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def cache_dir():
    return os.path.join(claude_home(), ".cache", "ballast-visual")


def sanitize_key(key):
    """Reduce a session key to filename-safe characters.

    The key comes from a transcript filename (already safe) or a session_id (a uuid), but it is
    interpolated into a path, so anything outside [A-Za-z0-9._-] is replaced rather than trusted --
    a key containing a separator or '..' would otherwise choose the file that gets rewritten."""
    out = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (key or ""))
    out = out.strip(".") or "unknown"
    return out[:120]


def session_key(transcript_path=None, session_id=None):
    """Frozen key rule: the transcript_path filename stem (no .jsonl); session_id is the fallback."""
    if transcript_path:
        base = os.path.basename(str(transcript_path).replace("\\", "/").rstrip("/"))
        stem = base[:-6] if base.lower().endswith(".jsonl") else os.path.splitext(base)[0]
        if stem:
            return sanitize_key(stem)
    return sanitize_key(session_id or "")


def ledger_path(key):
    return os.path.join(cache_dir(), "origins-%s.jsonl" % sanitize_key(key))


def list_ledgers():
    """Every origins-*.jsonl in the cache dir. Missing dir -> empty list (a silent no-op upstream)."""
    d = cache_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names
            if n.startswith("origins-") and n.endswith(".jsonl")]


def key_of_ledger(path):
    base = os.path.basename(path)
    return base[len("origins-"):-len(".jsonl")]


# ---------------------------------------------------------------------------
# Process table
# ---------------------------------------------------------------------------

def _normalize_proc(pid, ppid, start_time, cmdline):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        ppid = int(ppid)
    except (TypeError, ValueError):
        ppid = None
    st = start_time if isinstance(start_time, str) and start_time.strip() else None
    return {"pid": pid, "ppid": ppid, "start_time": st, "cmdline": cmdline or ""}


def _run(argv):
    """Bounded capture of an enumerator. utf-8 + errors='replace', NOT text=True: text mode decodes
    with the locale codec (cp1252 on Windows) while the child writes the pipe in the OEM code page,
    so one undecodable byte in one command line would raise and take the whole probe down."""
    return subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                          timeout=PROBE_TIMEOUT_S)


# Parsing is split from execution in both Windows enumerators so the parse half is testable
# without a live process table: every existing test drives the BALLAST_VISUAL_PROC_FAKE seam,
# which bypasses these branches entirely -- leaving the code production actually runs on this
# platform unexercised. A parse bug here fails toward missed-reap (a mismatched fingerprint never
# verifies, so nothing is killed), which is the safe direction and therefore the SILENT one.
def _parse_cim(stdout, source="CIM"):
    data = json.loads(stdout)
    if isinstance(data, dict):
        # ConvertTo-Json collapses a single-element result to a bare object, not a 1-element array.
        data = [data]
    out = []
    for d in data:
        p = _normalize_proc(d.get("ProcessId"), d.get("ParentProcessId"),
                            d.get("Created"), d.get("CommandLine"))
        if p:
            out.append(p)
    if not out:
        raise ProcessProbeError("empty %s result" % source)
    return out


def _windows_cim(exe):
    proc = _run([exe, "-NoProfile", "-NonInteractive", "-Command", _PS_CIM_COMMAND])
    return _parse_cim(proc.stdout, source="CIM result from %s" % exe)


def _parse_wmic(stdout):
    """Header is Node,CommandLine,CreationDate,ParentProcessId,ProcessId (wmic sorts the columns
    alphabetically and prepends Node); a command line containing commas would shred a naive split,
    so the row is parsed from BOTH ends -- the four fixed fields are taken from the known positions
    and everything between is rejoined as the command line."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        cmdline = ",".join(parts[1:-3])
        p = _normalize_proc(parts[-1], parts[-2], parts[-3], cmdline)
        if p:
            out.append(p)
    if not out:
        raise ProcessProbeError("empty wmic result")
    return out


def _windows_wmic():
    """Legacy CSV fallback for boxes predating wmic's removal."""
    proc = _run(["wmic", "process", "get",
                 "CommandLine,CreationDate,ParentProcessId,ProcessId", "/format:csv"])
    return _parse_wmic(proc.stdout)


def _posix_ps():
    proc = _run(["ps", "-eo", "pid=,ppid=,lstart=,args="])
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # lstart renders as five whitespace-separated tokens ("Mon Jul 28 21:27:39 2026"), so
        # maxsplit=7 lands pid, ppid, those five, and keeps the whole command line intact as the
        # eighth field instead of shredding it on its own internal spaces.
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        start = " ".join(parts[2:7])
        p = _normalize_proc(parts[0], parts[1], start, parts[7] if len(parts) > 7 else "")
        if p:
            out.append(p)
    if not out:
        raise ProcessProbeError("empty ps result (no lstart support?)")
    return out


def enumerate_processes():
    """Return the process table as [{pid, ppid, start_time, cmdline}], or raise ProcessProbeError.

    Never returns [] -- see the module header's fail directions: an unreadable table and an empty
    table must not be the same answer, because the empty table would read as "everything is dead"."""
    fake = os.environ.get("BALLAST_VISUAL_PROC_FAKE")
    if fake:
        # An unreadable seam file raises ProcessProbeError like any other failed enumerator --
        # never an empty table, which would read as "every recorded process is dead".
        try:
            with open(fake, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise ProcessProbeError("proc-fake seam unreadable: %s" % exc)
        out = []
        for d in data:
            p = _normalize_proc(d.get("pid"), d.get("ppid"), d.get("start_time"), d.get("cmdline"))
            if p:
                out.append(p)
        return out

    errors = []
    if os.name == "nt":
        probes = [("powershell.exe", lambda: _windows_cim("powershell.exe")),
                  ("pwsh", lambda: _windows_cim("pwsh")),
                  ("wmic", _windows_wmic)]
    else:
        probes = [("ps", _posix_ps)]
    for label, fn in probes:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- any failure just advances the probe order
            errors.append("%s: %s" % (label, exc))
    raise ProcessProbeError("; ".join(errors) or "no enumerator available")


def index_by_pid(procs):
    return {p["pid"]: p for p in procs}


def cmdline_fp(cmdline):
    return hashlib.sha256((cmdline or "").encode("utf-8", "replace")).hexdigest()[:16]


def ancestors_of(index, pid):
    """The pid's ancestor chain (including itself) as a set, bounded against cycles."""
    seen = set()
    cur = pid
    for _ in range(MAX_ANCESTRY_HOPS):
        if cur is None or cur in seen or cur not in index:
            break
        seen.add(cur)
        cur = index[cur].get("ppid")
    return seen


def _parse_iso(value):
    """Best-effort parse of a start_time for AGE comparison only -- never for identity (identity is
    exact string equality). Returns an aware datetime or None; a POSIX `lstart` string parses here
    too via its documented format."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def anchor_pid():
    """The ancestry ANCHOR the bin/ shim hands us, or None.

    WHY IT EXISTS (measured on this box, 8/8): when an MSYS bash spawns a bash SCRIPT -- exactly
    how a bin/ shim is invoked from the Bash tool -- MSYS's fork emulation leaves a transient
    intermediate that has already EXITED before the CLI starts, and Windows keeps no record of a
    dead process's own parent. The Win32 ancestor walk therefore dead-ends one hop above the shim.
    The repair cannot live here: `ps` launched by a NON-MSYS process (our interpreter) is given a
    fresh MSYS namespace containing only itself, so it sees no parentage at all (measured). The
    shim, which IS inside the original MSYS tree, resolves the outermost MSYS ancestor's WINPID
    and exports it here; the walk resumes from there.

    TRUST NOTE: this env var only chooses where the walk STARTS, and so only ever affects which
    process is recorded as the owner. It takes no part in verifying a kill target, and anyone able
    to set it for this process can already run commands as this user."""
    try:
        return int(os.environ.get("BALLAST_ORIGIN_ANCHOR_PID", ""))
    except (TypeError, ValueError):
        return None


def detect_owner(index, self_pid=None):
    """Identify the recording session's own long-lived process, name-free.

    METHOD: walk the ancestor chain from this process (or from the shim's anchor, see anchor_pid)
    and take the FIRST (nearest) ancestor created at least OWNER_MIN_AGE_S before the walk's
    starting process. The layers spawned to run this command -- the bash wrapper(s) and this
    interpreter -- are milliseconds old; the session process that owns them was started when the
    session was. Measured on this box: three bash layers + python all within 0.07s of each other,
    with the session's claude.exe 52 minutes older.

    WHY NOT MATCH THE PROCESS NAME: the reap rule forbids process-name matching outright, and
    owner identity feeds that rule. This walk uses structure (ancestry) and age, never a name.

    WHY THE THRESHOLD DIRECTION IS THE SAFETY PROPERTY: guessing an ancestor TOO HIGH (the shell,
    the terminal) is harmless -- that process outlives the session, so the sweep simply never reaps
    the entry and teardown remains the cleanup path. Guessing TOO LOW (a wrapper that exits with
    this command) would make a LIVE session look dead and get its origin killed. The threshold
    exists solely to make the error direction the harmless one; on failure we return (None, None),
    which is recorded as "owner unknown" and is likewise never reaped.
    """
    if self_pid is None:
        anchor = anchor_pid()
        self_pid = anchor if (anchor is not None and anchor in index) else os.getpid()
    me = index.get(self_pid)
    if not me:
        return (None, None)
    base = _parse_iso(me.get("start_time"))
    if base is None:
        return (None, None)
    cur = me.get("ppid")
    seen = {self_pid}
    for _ in range(MAX_ANCESTRY_HOPS):
        if cur is None or cur in seen or cur not in index:
            return (None, None)
        seen.add(cur)
        node = index[cur]
        started = _parse_iso(node.get("start_time"))
        if started is not None and (base - started).total_seconds() >= OWNER_MIN_AGE_S:
            return (cur, node.get("start_time"))
        cur = node.get("ppid")
    return (None, None)


# ---------------------------------------------------------------------------
# Entry verification -- the reap rule, in two functions
# ---------------------------------------------------------------------------

def origin_verified(entry, index):
    """True only if the live process at entry['pid'] IS the recorded process: same pid, same
    start_time, same cmdline_fp. PID reuse fails here (different start_time and/or command line),
    which is what turns a reuse into a PRUNE instead of a kill."""
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return False
    proc = index.get(pid)
    if not proc:
        return False
    st = proc.get("start_time")
    if not st or st != entry.get("start_time"):
        return False
    return cmdline_fp(proc.get("cmdline")) == entry.get("cmdline_fp")


def owner_alive(entry, index):
    """Tri-state liveness of the RECORDING session's process: True / False / None (unknown).

    Only False permits a reap. Both other answers protect the entry:
      None  -- no owner recorded (detection failed at record time): unprovable, so never reaped.
      True  -- the pid is present. Note this is deliberately generous: a present pid whose
               start_time could not be read counts as ALIVE, because the alternative (calling an
               unreadable live process dead) reaps a running session's origin."""
    raw = entry.get("owner_pid")
    if raw is None:
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    proc = index.get(pid)
    if not proc:
        return False
    st = proc.get("start_time")
    recorded = entry.get("owner_start_time")
    if st and recorded and st != recorded:
        return False  # pid reused by a different process -> the owning session is gone
    return True


def entry_shape_ok(entry):
    return (isinstance(entry, dict)
            and entry.get("pid") is not None
            and isinstance(entry.get("start_time"), str) and entry["start_time"]
            and isinstance(entry.get("cmdline_fp"), str) and entry["cmdline_fp"])


# ---------------------------------------------------------------------------
# Ledger IO
# ---------------------------------------------------------------------------

def read_entries(path):
    """Return (entries, malformed_count). A malformed line is DROPPED, never guessed at: this file
    decides kills, so an unparseable row is treated as absent rather than partially honoured."""
    entries, malformed = [], 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return ([], 0)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not entry_shape_ok(obj):
            malformed += 1
            continue
        entries.append(obj)
    return (entries, malformed)


def write_entries(path, entries):
    """Rewrite (or delete, when empty) a ledger. Written to a temp file then os.replace'd so a
    crash mid-write can never leave a half-line that read_entries would count as malformed."""
    if not entries:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)


def append_entry(path, entry):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Kill
# ---------------------------------------------------------------------------

def kill_process(pid):
    """Terminate a VERIFIED origin. Callers must have re-verified the fingerprint immediately
    before calling -- this function performs no verification of its own.

    BALLAST_VISUAL_KILL_LOG is the hermetic-test seam: when set, the kill is recorded to that file
    and NOT executed. Production never sets it, and the seam can only ever suppress a kill.

    Windows uses `taskkill /F /T`: /T takes the process tree, because an origin is routinely a
    launcher (npm/node wrapper) whose actual listener is a child -- killing only the recorded pid
    would leave the port held, i.e. fail at the one job this has. POSIX kills the process group
    when the target leads one (the detached-origin shape), else the process alone."""
    log = os.environ.get("BALLAST_VISUAL_KILL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("KILL %s\n" % pid)
        return True
    try:
        if os.name == "nt":
            proc = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                  capture_output=True, encoding="utf-8", errors="replace",
                                  timeout=KILL_TIMEOUT_S)
            return proc.returncode == 0
        import signal
        import time as _time

        def signal_it(sig):
            # Prefer the process GROUP when the target leads one -- that is the shape a detached
            # origin takes, and it reaches the children a bare kill would strand.
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, sig)
                    return
            except (AttributeError, OSError):
                pass
            os.kill(pid, sig)

        def gone():
            try:
                os.kill(pid, 0)
                return False
            except OSError:
                return True

        signal_it(signal.SIGTERM)
        # SIGTERM alone is not a kill: a process that ignores it would be dropped from the ledger
        # as "reaped" while still holding its port. Escalate after a short grace period, then
        # report the ACTUAL outcome rather than the fact that a signal was sent.
        for _ in range(8):
            if gone():
                return True
            _time.sleep(0.2)
        signal_it(signal.SIGKILL)
        _time.sleep(0.2)
        return gone()
    except Exception:  # noqa: BLE001 -- a failed kill is reported, never raised at the hook
        return False


# ---------------------------------------------------------------------------
# Operations: record / sweep / teardown / list
# ---------------------------------------------------------------------------

def record(key, url, out_dir, pid, owner_pid=None, index=None):
    """Fingerprint a just-started origin and append it to this session's ledger. Returns the entry.

    Raises ValueError when the pid is not in the process table or has no readable start time /
    command line -- an entry that cannot be fingerprinted is worse than no entry at all, because a
    later sweep would compare against nothing and could only ever prune it."""
    if index is None:
        index = index_by_pid(enumerate_processes())
    pid = int(pid)
    proc = index.get(pid)
    if not proc:
        raise ValueError("pid %s is not running (nothing to record)" % pid)
    if not proc.get("start_time"):
        raise ValueError("pid %s has no readable start time -- cannot fingerprint" % pid)
    if not proc.get("cmdline"):
        raise ValueError("pid %s has no readable command line -- cannot fingerprint" % pid)

    if owner_pid is None:
        o_pid, o_start = detect_owner(index)
    else:
        o_pid = int(owner_pid)
        o_start = (index.get(o_pid) or {}).get("start_time")

    entry = {
        "pid": pid,
        "start_time": proc["start_time"],
        "cmdline_fp": cmdline_fp(proc["cmdline"]),
        "url": url,
        "out_dir": out_dir,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner_pid": o_pid,
        "owner_start_time": o_start,
    }
    append_entry(ledger_path(key), entry)
    return entry


def _stale_file(path):
    try:
        age_days = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)) / 86400.0
    except OSError:
        return False
    return age_days >= LEDGER_STALE_DAYS


def sweep(index=None, fresh_index_fn=None):
    """Apply the reap rule across every ledger in the cache dir. Returns a result dict:
        {"ledgers": n, "reaped": [entry,...], "pruned": n, "failed": [entry,...], "skipped": n}

    Reap requires BOTH halves, per the frozen rule: (a) the origin fingerprint verifies against a
    live process, AND (b) the owner is verifiably dead. Anything else is pruned or left alone.

    CONCURRENCY RULE -- a ledger with ANY live-or-unknown-owner entry is not modified at all. That
    session may be appending to the file right now, and a rewrite would silently drop the line it
    just wrote. "Only dead sessions' ledgers are touched" removes the race instead of narrowing it.
    """
    result = {"ledgers": 0, "reaped": [], "pruned": 0, "failed": [], "skipped": 0}
    paths = list_ledgers()
    if not paths:
        return result  # nothing on disk -> silent no-op, and no process enumeration is paid for

    if index is None:
        index = index_by_pid(enumerate_processes())
    result["ledgers"] = len(paths)

    for path in paths:
        entries, malformed = read_entries(path)
        keep, candidates = [], []
        pruned = malformed
        owner_live_seen = False
        for e in entries:
            if not origin_verified(e, index):
                pruned += 1          # process gone, or pid reused: prune, never kill
                continue
            alive = owner_alive(e, index)
            if alive is False:
                candidates.append(e)
            else:
                owner_live_seen = True   # True or None (unknown) -- both protect the entry
                keep.append(e)

        if owner_live_seen:
            # Owning session may still be running: leave the whole file untouched (see rule above).
            result["skipped"] += 1
            continue

        if not candidates:
            # Nothing reapable in this file (`keep` is necessarily empty here -- any kept entry
            # would have set owner_live_seen). The file provably references no live process, but
            # it is only DELETED once it is also stale by age: a session whose origin merely died
            # a minute ago keeps its own file, and this housekeeping path can never kill anything.
            if _stale_file(path):
                write_entries(path, [])
                result["pruned"] += pruned
            elif pruned:
                result["skipped"] += 1
            continue

        # Re-verify against a FRESH table immediately before signalling: the window between the
        # decision and the kill is where a pid could be recycled, and shrinking it is cheap.
        fresh = index_by_pid((fresh_index_fn or enumerate_processes)())
        self_chain = ancestors_of(fresh, os.getpid())
        survivors, killed = list(keep), []
        for e in candidates:
            if not origin_verified(e, fresh) or owner_alive(e, fresh) is not False:
                pruned += 1
                continue
            if int(e["pid"]) in self_chain:
                # A ledger must never be able to name this session's own process tree.
                survivors.append(e)
                continue
            if kill_process(int(e["pid"])):
                killed.append(e)
            else:
                result["failed"].append(e)
                survivors.append(e)   # keep it so a later sweep retries
        result["reaped"].extend(killed)
        result["pruned"] += pruned
        write_entries(path, survivors)

    return result


def teardown(key, index=None):
    """Close-out path for the CURRENT session: kill every fingerprint-verified entry in this
    session's ledger and remove the file. Owner liveness is deliberately NOT consulted -- the owner
    is the caller, alive by construction; the caller is declaring the origins finished."""
    result = {"path": ledger_path(key), "existed": False, "killed": [], "pruned": 0, "failed": []}
    path = result["path"]
    if not os.path.isfile(path):
        return result
    result["existed"] = True
    entries, malformed = read_entries(path)
    result["pruned"] = malformed
    if entries:
        if index is None:
            index = index_by_pid(enumerate_processes())
        self_chain = ancestors_of(index, os.getpid())
        for e in entries:
            if not origin_verified(e, index):
                result["pruned"] += 1
                continue
            if int(e["pid"]) in self_chain:
                result["failed"].append(e)
                continue
            if kill_process(int(e["pid"])):
                result["killed"].append(e)
            else:
                result["failed"].append(e)
    if result["failed"]:
        # A kill that did not take stays RECORDED rather than being dropped with the file: the
        # session is ending, so its owner is about to be verifiably dead and the next session's
        # sweep becomes the retry. Deleting the ledger here would strand the process forever.
        write_entries(path, result["failed"])
    else:
        try:
            os.remove(path)
        except OSError:
            pass
    return result


def status(key=None, index=None):
    """Every ledger entry (optionally one session's) with its live/stale verdict, for `list`."""
    paths = [ledger_path(key)] if key else list_ledgers()
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        return []
    if index is None:
        index = index_by_pid(enumerate_processes())
    rows = []
    for path in paths:
        entries, _ = read_entries(path)
        for e in entries:
            rows.append({
                "session": key_of_ledger(path),
                "entry": e,
                "origin_verified": origin_verified(e, index),
                "owner_alive": owner_alive(e, index),
            })
    return rows


# ---------------------------------------------------------------------------
# CLI (bin/ballast-visual-origin)
# ---------------------------------------------------------------------------

def _resolve_key(args):
    key = session_key(getattr(args, "transcript_path", None), getattr(args, "session_key", None))
    if not key or key == "unknown":
        raise SystemExit("error: --session-key (or --transcript-path) is required")
    return key


def _describe(entry):
    return "pid %s  %s  out-dir=%s" % (entry.get("pid"), entry.get("url") or "-",
                                       entry.get("out_dir") or "-")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="ballast-visual-origin",
        description="Origin ledger for the visual stack: record, list, and tear down the "
                    "orchestrator-owned origin processes of a session.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_key(p):
        p.add_argument("--session-key", help="session key (transcript filename stem, or session id)")
        p.add_argument("--transcript-path", help="transcript path; its filename stem becomes the key")

    p_rec = sub.add_parser("record", help="record a just-started origin process")
    add_key(p_rec)
    p_rec.add_argument("--url", required=True)
    p_rec.add_argument("--out-dir", required=True)
    p_rec.add_argument("--pid", required=True, type=int)
    p_rec.add_argument("--owner-pid", type=int,
                       help="override owner detection (default: nearest long-lived ancestor)")

    p_list = sub.add_parser("list", help="show ledger entries and their liveness")
    add_key(p_list)
    p_list.add_argument("--all", action="store_true", help="every session's ledger")

    p_down = sub.add_parser("teardown", help="kill this session's verified origins, remove ledger")
    add_key(p_down)

    args = parser.parse_args(argv)

    if args.cmd == "record":
        key = _resolve_key(args)
        try:
            entry = record(key, args.url, args.out_dir, args.pid, owner_pid=args.owner_pid)
        except (ValueError, ProcessProbeError) as exc:
            print("record failed: %s" % exc, file=sys.stderr)
            return 1
        print("recorded %s" % _describe(entry))
        print("  ledger: %s" % ledger_path(key))
        if entry["owner_pid"] is None:
            print("  note: owner process not identified -- the SessionStart sweep will never reap "
                  "this entry; teardown remains its cleanup path.")
        return 0

    if args.cmd == "list":
        key = None if args.all else _resolve_key(args)
        try:
            rows = status(key)
        except ProcessProbeError as exc:
            print("list failed: process table unavailable (%s)" % exc, file=sys.stderr)
            return 1
        if not rows:
            print("no origin ledger entries")
            return 0
        for r in rows:
            owner = {True: "owner alive", False: "owner dead", None: "owner unknown"}[r["owner_alive"]]
            state = "live" if r["origin_verified"] else "stale"
            print("[%s] %s  (%s, %s)" % (r["session"], _describe(r["entry"]), state, owner))
        return 0

    if args.cmd == "teardown":
        key = _resolve_key(args)
        try:
            res = teardown(key)
        except ProcessProbeError as exc:
            print("teardown failed: process table unavailable (%s)" % exc, file=sys.stderr)
            return 1
        if not res["existed"]:
            print("teardown: no ledger for session %s -- nothing to do" % key)
            return 0
        for e in res["killed"]:
            print("killed %s" % _describe(e))
        for e in res["failed"]:
            print("FAILED to kill %s (left in place)" % _describe(e), file=sys.stderr)
        if res["pruned"]:
            print("pruned %d stale entr%s (process already gone)"
                  % (res["pruned"], "y" if res["pruned"] == 1 else "ies"))
        if res["failed"]:
            print("ledger kept (%d unkilled entr%s) for the next session-start sweep: %s"
                  % (len(res["failed"]), "y" if len(res["failed"]) == 1 else "ies", res["path"]))
            return 1
        print("ledger removed: %s" % res["path"])
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
