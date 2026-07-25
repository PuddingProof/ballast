// Process helpers shared by the detached-lifecycle modules (session.mjs, serve.mjs).

import { execFileSync } from 'child_process';

// Kill a process tree by pid — but only if its command line contains `marker`.
//
// The marker guards every record-file kill path against Windows PID recycling: a recorded pid
// whose process has died can be reassigned to an unrelated process (the user's dev server, a
// build watcher), and killing by number alone would murder it. Passing a string only OUR spawn
// puts on the command line ('__serve-child', the session profile dir) makes the kill precise;
// a dead/recycled/foreign pid is simply left alone. Returns true iff a kill was issued.
export function killTree(pid, marker) {
  if (!pid) return false;

  if (process.platform === 'win32') {
    if (marker) {
      let cmdline = '';
      try {
        cmdline = execFileSync('powershell', ['-NoProfile', '-Command',
          `(Get-CimInstance Win32_Process -Filter "ProcessId=${Number(pid)}").CommandLine`],
          { encoding: 'utf8', timeout: 10000 }) || '';
      } catch { /* query failed — fail SAFE: don't kill what we can't identify */ }
      if (!cmdline.includes(marker)) return false;
    }
    try { execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' }); return true; }
    catch { return false; } // already gone, or denied — callers that must be sure re-verify liveness
  }

  // ---- POSIX (macOS/Linux) ----
  // Same marker-first safety as the win32 branch: confirm THIS pid's command line actually
  // contains our marker before touching anything, so a recycled pid (our process died, the OS
  // handed the number to something unrelated) is left alone rather than killed by mistake.
  if (marker) {
    let cmdline = '';
    try {
      // `ps -o command=` (the trailing `=` suppresses the header row) prints the full command
      // line for one pid on BOTH GNU ps (Linux) and BSD ps (macOS) — unlike /proc/<pid>/cmdline,
      // which is Linux-only and doesn't exist on macOS.
      cmdline = execFileSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8', timeout: 10000 }) || '';
    } catch { /* no such pid, or ps unavailable — fail SAFE: don't kill what we can't identify */ }
    if (!cmdline.includes(marker)) return false;
  }
  // The child was spawned with `detached: true` (session.mjs / serve.mjs), which on POSIX makes
  // it the LEADER of its own process group (pgid === pid) — so signaling the NEGATIVE pid kills
  // the whole group in one call, the POSIX equivalent of taskkill's `/T`. ESRCH means it's already
  // gone (not a failure). Any other error (e.g. the child re-parented itself out of the group, or
  // we lack permission on the group) falls through to a best-effort `pkill -P` walk of direct
  // children before killing the pid itself, so a lingering child isn't silently left running.
  try { process.kill(-pid, 'SIGKILL'); return true; }
  catch (e) {
    if (e?.code === 'ESRCH') return false;
    try { execFileSync('pkill', ['-9', '-P', String(pid)], { stdio: 'ignore' }); } catch { /* no children, or pkill denied/absent */ }
    try { process.kill(pid, 'SIGKILL'); return true; } catch { return false; }
  }
}
