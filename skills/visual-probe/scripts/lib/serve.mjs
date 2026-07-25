// Bundled ISOLATED static server — the hazard-proof origin for probes and co-drive.
//
// Exists because pointing a probe at the user's LIVE dev server is a recurring hazard family
// (5 distinct facets across sessions, see the skill's serve section):
//   • beacon-kill — the app under probe fires its own lifecycle beacons (e.g. a tab-close
//     POST /api/leaving) at whatever origin served it; against a live server with --idle-stop
//     semantics that ARMS a shutdown and kills the server under the user (happened twice).
//   • keep-alive wedge — a persistent headed Edge holds keep-alive connections that starve a
//     stock `python -m http.server` (measured: 9/20 requests dropped under 8 held sockets).
//   • stale sub-resource cache — a long-lived window keeps rendering pre-edit CSS/JS even when
//     index.html is cache-busted, mimicking "the fix didn't apply".
//
// Design — each hazard is closed by CONSTRUCTION, not by per-session discipline:
//   • GET/HEAD only, no /api routes → a lifecycle beacon POST gets an inert 405; there is no
//     shutdown to arm.
//   • Node event-loop concurrency → held keep-alive connections can't starve other requests.
//   • Cache-Control: no-store on every response → a reload always picks up edited CSS/JS.
//   • Loopback bind + traversal guard (lexical AND realpath — a symlink/junction inside the
//     root that points outside it is refused, not followed) → nothing off-box, nothing
//     outside the served root.
//
// Lifecycle mirrors session.mjs: `serve start` spawns a DETACHED child (this file's runServeChild
// via the hidden `__serve-child` command) so the server outlives the launcher; a record file in
// OS-temp lets start/status/stop find it. The record is CLAIMED atomically ('wx') before the
// spawn so two concurrent starts can't both proceed and orphan a server; a stale record (killed
// out-of-band, or corrupt) is reaped on the next start. The child's stdout/stderr append to a
// log file in OS-temp — a child that dies at startup (e.g. EADDRINUSE on a pinned --port)
// leaves its reason there instead of vanishing behind stdio:'ignore'.

import http from 'http';
import net from 'net';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { killTree } from './proc.mjs';

const SERVE_FILE = path.join(os.tmpdir(), 'visual-probe.serve.json');
const LOG_FILE = path.join(os.tmpdir(), 'visual-probe.serve.log');
const CHILD_MARKER = '__serve-child'; // on the child's command line — the killTree precision guard
const HEALTH_PATH = '/__visual-probe'; // identifies OUR server on the port (vs someone else's)
const HEALTH_BODY = 'visual-probe-serve';

// Every response carries no-store — the transport-level fix for the stale-CSS/JS facet.
const NO_STORE = { 'Cache-Control': 'no-store, must-revalidate', 'Expires': '0' };

const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json', '.map': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp', '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.wasm': 'application/wasm',
};

// ---- the server itself (runs in the detached child; console.* lands in LOG_FILE) ----
export function runServeChild({ root, port }) {
  if (!root || !fs.existsSync(root) || !Number.isFinite(port) || port <= 0) {
    console.error(`[serve-child] bad args: root=${root} port=${port}`);
    process.exit(1);
  }
  // realpath'd once: the anchor both traversal checks (lexical + per-request realpath) compare
  // against. Also normalizes a drive-root target (C:\ already ends in the sep).
  const rootReal = fs.realpathSync(root);
  const rootPrefix = rootReal.endsWith(path.sep) ? rootReal : rootReal + path.sep;
  const inRoot = (p) => p === rootReal || p.startsWith(rootPrefix);

  const server = http.createServer((req, res) => {
    // GET/HEAD only: the app's lifecycle/liveness beacons (POSTs) are structurally inert here.
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, NO_STORE); res.end('method not allowed'); return; }
    let url;
    try { url = new URL(req.url, 'http://localhost'); } catch { res.writeHead(400, NO_STORE); res.end('bad request'); return; }
    let pathname;
    try { pathname = decodeURIComponent(url.pathname); } catch { res.writeHead(400, NO_STORE); res.end('bad request'); return; }
    if (pathname === HEALTH_PATH) { res.writeHead(200, { ...NO_STORE, 'Content-Type': 'text/plain' }); res.end(HEALTH_BODY); return; }

    // Lexical containment first (cheap), then realpath containment after the file resolves —
    // the second check is what refuses a symlink/junction escaping the root.
    let fp = path.resolve(rootReal, '.' + pathname);
    if (!inRoot(fp)) { res.writeHead(403, NO_STORE); res.end('forbidden'); return; }
    let st = fs.existsSync(fp) ? fs.statSync(fp) : null;
    if (st?.isDirectory()) {
      // Redirect /sub → /sub/ so the page's RELATIVE sub-resource URLs resolve against the
      // directory, not its parent — serving index.html verbatim at /sub 404s its ./style.css,
      // a false "broken page" from the transport (the exact class this server exists to kill).
      if (!pathname.endsWith('/')) { res.writeHead(301, { ...NO_STORE, Location: `${url.pathname}/${url.search}` }); res.end(); return; }
      fp = path.join(fp, 'index.html');
      st = fs.existsSync(fp) ? fs.statSync(fp) : null;
    }
    if (!st?.isFile()) { res.writeHead(404, NO_STORE); res.end('not found'); return; }
    let real;
    try { real = fs.realpathSync(fp); } catch { res.writeHead(404, NO_STORE); res.end('not found'); return; }
    if (!inRoot(real)) { console.error(`[serve-child] refused symlink escape: ${pathname} → ${real}`); res.writeHead(403, NO_STORE); res.end('forbidden'); return; }

    res.writeHead(200, {
      ...NO_STORE,
      'Content-Type': MIME[path.extname(real).toLowerCase()] || 'application/octet-stream',
      'Content-Length': st.size,
    });
    if (req.method === 'HEAD') { res.end(); return; }
    fs.createReadStream(real).pipe(res);
  });
  // Without this, a listen failure (EADDRINUSE on a pinned --port) is an unhandled 'error' that
  // kills the child with no trace; with it, the reason lands in the log the launcher points at.
  server.on('error', (e) => { console.error(`[serve-child] server error: ${e.message}`); process.exit(1); });
  server.listen(port, '127.0.0.1', () => {
    console.error(`[serve-child] ${new Date().toISOString()} listening on http://127.0.0.1:${port}/ (root ${rootReal})`);
  });
  return server;
}

// ---- launcher-side lifecycle (start / status / stop) ----

// Ask the OS for a free port by binding :0 and releasing it. The brief release→respawn race is
// acceptable on a single-user dev box; pass --port to pin one deliberately.
function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

// Health = OUR server answering on the port (body-checked, so a foreign server reads as not-ours).
async function health(port, timeoutMs = 800) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}${HEALTH_PATH}`, { signal: AbortSignal.timeout(timeoutMs) });
    return r.ok && (await r.text()) === HEALTH_BODY;
  } catch { return false; }
}

// A missing or CORRUPT record reads as null — corrupt is treated as stale, never a crash.
function readRecord(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

export async function startServe({ dir, port = 0, file = SERVE_FILE } = {}) {
  // No default root: silently serving the launcher's cwd (dir omitted, or an empty shell var)
  // could expose a far broader tree than intended — fail fast instead.
  if (!dir) throw new Error('serve start requires a directory: probe.mjs serve start <dir>');
  const root = path.resolve(dir);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) throw new Error(`serve root is not a directory: ${root}`);
  if (!Number.isFinite(port) || port < 0) throw new Error(`bad --port: ${port}`);

  const prev = readRecord(file);
  if (prev && await health(prev.port)) throw new Error(`an isolated server is already up on :${prev.port} (root ${prev.root}) — use it, or 'serve stop' first`);
  if (prev) killTree(prev.pid, CHILD_MARKER); // stale (or corrupt-pid) record: marker-guarded reap
  fs.rmSync(file, { force: true });

  const p = port || await freePort();
  // CLAIM the record atomically before spawning — two concurrent starts race on 'wx' and the
  // loser stops here, instead of both spawning and one server being silently orphaned.
  const rec = { pid: null, port: p, root, url: `http://127.0.0.1:${p}/`, startedAt: new Date().toISOString() };
  try { fs.writeFileSync(file, JSON.stringify(rec, null, 2), { flag: 'wx' }); }
  catch (e) {
    if (e.code === 'EEXIST') throw new Error("another 'serve start' is racing this one — retry in a moment (or use its server)");
    throw e;
  }

  const probe = fileURLToPath(new URL('../probe.mjs', import.meta.url));
  // detached => the server outlives this launcher (same pattern as session start); stdout/stderr
  // append to LOG_FILE (truncated per start) so a startup death is diagnosable, not invisible.
  const logFd = fs.openSync(LOG_FILE, 'w');
  const child = spawn(process.execPath, [probe, CHILD_MARKER, root, String(p)], { detached: true, stdio: ['ignore', logFd, logFd] });
  child.unref();
  fs.closeSync(logFd);

  const deadline = Date.now() + 8000;
  let up = false;
  while (Date.now() < deadline && !(up = await health(p))) await new Promise((r) => setTimeout(r, 150));
  if (!up) {
    killTree(child.pid, CHILD_MARKER);
    fs.rmSync(file, { force: true });
    let tail = ''; try { tail = fs.readFileSync(LOG_FILE, 'utf8').trim().split('\n').pop() || ''; } catch { /* no log */ }
    throw new Error(`isolated server did not come up on :${p} within 8s${tail ? ` — child log: ${tail}` : ''} (full log: ${LOG_FILE})`);
  }

  rec.pid = child.pid;
  fs.writeFileSync(file, JSON.stringify(rec, null, 2)); // finalize the claim with the real pid
  return rec;
}

export async function statusServe({ file = SERVE_FILE } = {}) {
  const rec = readRecord(file);
  if (!rec) return { up: false };
  return { ...rec, up: await health(rec.port) };
}

export async function stopServe({ file = SERVE_FILE } = {}) {
  const rec = readRecord(file);
  if (!rec) throw new Error('no isolated server record — nothing to stop');
  killTree(rec.pid, CHILD_MARKER);
  // Verify it's actually down before declaring success — a denied taskkill must not report
  // "stopped" and drop the record while the server keeps listening untracked.
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline && await health(rec.port)) await new Promise((r) => setTimeout(r, 200));
  if (await health(rec.port)) throw new Error(`server on :${rec.port} is still answering after kill (pid ${rec.pid}) — stop it manually; record kept`);
  fs.rmSync(file, { force: true });
  return { stopped: rec.pid, port: rec.port, root: rec.root };
}

export { SERVE_FILE, LOG_FILE };
