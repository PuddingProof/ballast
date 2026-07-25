// Shared live browser session — the co-presence surface.
//
// Unlike the rest of visual-probe (stateless capture-and-teardown), a SESSION is a long-lived,
// HEADED Edge launched with a remote-debugging port: ONE window that the human drives by hand and
// Claude drives over CDP, each seeing the other's actions live. Key properties:
//   • Launched DETACHED so the window outlives the `session start` process (the surface persists).
//   • A DEDICATED profile — never the user's daily browser (credential-exposure trap).
//   • `look`/`do` reconnect over CDP via a small session file; `browser.close()` on a connectOverCDP
//     handle only DETACHES the CDP client — it does NOT kill the shared window (that's `stop`).
//
// Either party can run `session start` — whoever's shell can surface a window on the user's desktop.
// Once the debug port is up, Claude attaches identically regardless of who launched it.

import { spawn, execFileSync } from 'child_process';
import { killTree } from './proc.mjs';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { chromium } from 'playwright';
import { captureFrame, DEFAULT_OUT } from './capture.mjs';
import { guardUrl } from './urlguard.mjs';
import { readStructure } from './read.mjs';
import { EDGE_PRIVACY_ARGS } from './edge-privacy.mjs';

const SESSION_FILE = path.join(os.tmpdir(), 'visual-probe.session.json');
const PROFILE_DIR = path.join(os.tmpdir(), 'visual-probe-profile'); // dedicated, isolated from the daily browser

// Locate a system browser executable to launch DIRECTLY (spawn(), not Playwright's launch() —
// launch() has no detached+unref long-lived-window mode, which the shared co-drive session needs).
// Because the browser is only ever driven afterward over CDP (connectOverCDP doesn't care which
// engine answers the debug port), Edge and Chrome are interchangeable here — so this searches Edge
// first (its profile/sync knobs are already tuned in edge-privacy.mjs) then falls back to Chrome on
// every OS, rather than failing just because Edge isn't installed.
//
// Platform chain (checked in order, first hit wins):
//   win32:  two standard Edge Program Files paths        -> two standard Chrome Program Files paths
//   darwin: the standard Edge.app bundle path             -> the standard Chrome.app bundle path
//   linux:  Edge on PATH (microsoft-edge-stable|microsoft-edge|msedge)
//                                                          -> Chrome/Chromium on PATH (google-chrome|chromium|chromium-browser)
// A miss all the way down throws a single error naming exactly what was probed, so a user without
// either browser installed (or with it in a nonstandard location) gets an actionable message
// instead of a bare ENOENT from spawn().
function edgeExe() {
  const platform = process.platform;

  const EDGE_PATHS = {
    win32: [
      'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
      'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    ],
    darwin: [
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    ],
  };
  for (const c of EDGE_PATHS[platform] || []) if (fs.existsSync(c)) return c;
  // linux Edge installs are PATH-only (no fixed bundle path the way macOS/Windows have) — try each
  // package/binary name a distro might use, in likely-first order.
  if (platform === 'linux') {
    const onPath = resolveOnPath(['microsoft-edge-stable', 'microsoft-edge', 'msedge']);
    if (onPath) return onPath;
  }

  // No Edge found — fall back to Chrome/Chromium so the session still works on a box that only has
  // that installed.
  const CHROME_PATHS = {
    win32: [
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
      'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    ],
    darwin: [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ],
  };
  for (const c of CHROME_PATHS[platform] || []) if (fs.existsSync(c)) return c;
  if (platform === 'linux') {
    const onPath = resolveOnPath(['google-chrome', 'chromium', 'chromium-browser']);
    if (onPath) return onPath;
  }

  throw new Error(
    `no system browser found for the shared session — checked Edge (${platform === 'linux'
      ? 'microsoft-edge-stable/microsoft-edge/msedge on PATH'
      : 'standard install path'}) then Chrome (${platform === 'linux'
      ? 'google-chrome/chromium/chromium-browser on PATH'
      : 'standard install path'}) on ${platform}. Install one of them, or adjust edgeExe() in lib/session.mjs.`
  );
}

// Resolve the first of `names` found on PATH via the OS-native lookup (`where` on Windows, `which`
// elsewhere) — synchronous and dependency-free, no need to shell out through `command -v`.
function resolveOnPath(names) {
  const lookup = process.platform === 'win32' ? 'where' : 'which';
  for (const name of names) {
    try {
      const out = execFileSync(lookup, [name], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      const first = out.split(/\r?\n/)[0].trim(); // `where` can print multiple matches, one per line
      if (first) return first;
    } catch { /* not found on PATH — try the next candidate name */ }
  }
  return null;
}

// Poll the CDP endpoint until the browser is ready to accept connections.
async function waitForEndpoint(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1000) });
      if (r.ok) return await r.json();
    } catch (e) { lastErr = e; }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`debug endpoint :${port} did not come up in ${timeoutMs}ms (${lastErr?.message || 'no response'})`);
}

function loadSession(file = SESSION_FILE) {
  if (!fs.existsSync(file)) throw new Error('no live session — run: probe session start [url]');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

// Live viewport AS THE PAGE SEES IT. A CDP-attached page reports null from page.viewportSize()
// (Playwright didn't set the viewport — the real window did), so read innerWidth/Height/DPR directly.
// Device px ≈ w·dsf × h·dsf — the number co-driving resize/zoom needs to confirm a change numerically.
async function pageViewport(page) {
  try {
    return await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight, dsf: window.devicePixelRatio }));
  } catch { return { w: 0, h: 0, dsf: 0 }; }
}

// The page the human is actually looking at: first real (http/https/file) tab, skipping about:blank /
// devtools. Exported so the stateless `run --cdp` attach path picks the same page (not a blank tab).
export function firstRealPage(browser) {
  for (const ctx of browser.contexts())
    for (const p of ctx.pages())
      if (/^(https?|file):/i.test(p.url())) return p;
  const ctx = browser.contexts()[0];
  if (ctx?.pages()[0]) return ctx.pages()[0];
  throw new Error('no page found in the attached browser');
}

// Connect to the live session over CDP, hand the human's current page to `work`, and ALWAYS detach.
// browser.close() on a connectOverCDP handle only drops the CDP client — the shared window stays open
// (only `stop` kills it). Centralizing the connect→pick→finally-detach means no session verb (now or
// a future one) can forget the teardown — the rule-of-three this extracts from look/read/do.
async function withLivePage(work, { file = SESSION_FILE } = {}) {
  const s = loadSession(file);
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${s.port}`);
  try {
    return await work(firstRealPage(browser), s);
  } finally {
    await browser.close();
  }
}

// ---- start: launch the shared headed browser, detached ----
export async function startSession({ url, port = 9222, allowRemote = false, file = SESSION_FILE, profile = PROFILE_DIR } = {}) {
  if (fs.existsSync(file)) {
    const prev = JSON.parse(fs.readFileSync(file, 'utf8'));
    // Is it actually still alive, or stale (e.g. the window was closed with the X, not `session stop`)?
    let alive = false;
    try { alive = (await fetch(`http://127.0.0.1:${prev.port}/json/version`, { signal: AbortSignal.timeout(800) })).ok; } catch { /* dead */ }
    if (alive) throw new Error(`a live session is already on :${prev.port} — run 'probe session stop' first, or look/do against it`);
    // stale record: reap any zombie process + the file, then continue (don't wedge future sessions).
    // Marker-guarded (the profile path is on Edge's command line) so a RECYCLED pid — Windows
    // reassigning the dead session's number to an unrelated process — is left alone, not killed.
    killTree(prev.pid, prev.profile || PROFILE_DIR);
    fs.rmSync(file, { force: true });
  }
  // ephemeral profile: wipe any leftover (a stop may not delete it while Edge still held locks) so
  // every session starts clean and the profile never accumulates. Safe here — no Edge holds it now.
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* absent or still locked */ }
  // also clear stale capture output so a prior session's look-*.png frames don't accumulate in temp
  // or get mistaken for the current state. (The dir is OS-temp, never the repo tree.) Assumes no
  // stateless `run`/`shot` is concurrently writing DEFAULT_OUT — true for single-user CLI; pass
  // `--out` to isolate a capture that must run alongside a session.
  try { fs.rmSync(DEFAULT_OUT, { recursive: true, force: true }); } catch { /* absent or locked */ }
  const target = url ? guardUrl(url, allowRemote) : 'about:blank';
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check',
    // Keep this Edge OFF the user's account: no implicit OS-account sign-in, no sync pulling their
    // history/passwords/bookmarks into the throwaway profile. See edge-privacy.mjs — the dedicated
    // profile alone does NOT prevent this (Edge signs it in anyway).
    ...EDGE_PRIVACY_ARGS,
    target,
  ];
  // detached + unref => the window outlives this launcher process (the shared surface persists)
  const child = spawn(edgeExe(), args, { detached: true, stdio: 'ignore' });
  child.unref();
  const version = await waitForEndpoint(port);
  const rec = { pid: child.pid, port, url: target, profile, startedAt: new Date().toISOString(), browser: version.Browser };
  fs.writeFileSync(file, JSON.stringify(rec, null, 2));
  return rec;
}

// ---- look: snapshot the CURRENT live state (no navigation, no reload) ----
export async function lookSession({ out = DEFAULT_OUT, crop, file = SESSION_FILE } = {}) {
  fs.mkdirSync(out, { recursive: true });
  return withLivePage(async (page) => {
    // viewport (what's on screen) — NOT fullPage — to match what the human is looking at
    const buf = await captureFrame(page, { crop, fullPage: false });
    const outPng = path.join(out, `look-${Date.now()}.png`);
    fs.writeFileSync(outPng, buf);
    const title = await page.title().catch(() => '');
    const viewport = await pageViewport(page); // so resize/zoom is confirmable numerically, not just by eye
    return { file: outPng, url: page.url(), title, viewport, bytes: buf.length };
  }, { file });
}

// ---- read: structured text + control inventory of the live page (disambiguates a screenshot) ----
export async function readSession({ selector = 'body', file = SESSION_FILE } = {}) {
  return withLivePage(async (page) => {
    const r = await readStructure(page, selector);
    const viewport = await pageViewport(page);
    return { ...r, viewport, url: page.url(), title: await page.title().catch(() => '') };
  }, { file });
}

// scroll [selector] <amount|top|bottom>
//   With a SELECTOR: scroll THAT container directly (el.scrollBy) — the fix for an inner overflow:auto
//   pane that a page-level wheel can't reach (a bare wheel dispatches at the viewport origin (0,0),
//   the page's top-left — often not over the target pane). No selector: wheel over the viewport
//   CENTER (not 0,0) so a real wheel event lands on content and wheel-listening UIs still respond.
//   The first arg is a selector whenever it isn't itself an amount, so `scroll .pane` (no amount) and
//   `scroll .pane bottom` BOTH target the container. `top`/`bottom` = scroll to that end.
async function scrollAction(page, args) {
  const isAmount = (a) => a === 'top' || a === 'bottom' || /^-?\d+$/.test(a || '');
  let selector, amount;
  if (args[0] !== undefined && !isAmount(args[0])) { selector = args[0]; amount = args[1] ?? '400'; }
  else { amount = args[0] ?? '400'; }
  const n = Number(amount);
  const dy = amount === 'bottom' ? 1e9 : amount === 'top' ? -1e9 : (Number.isFinite(n) ? n : 400); // 1e9 clamps to the scroll extent; isFinite preserves an explicit 0
  if (selector) {
    await page.locator(selector).first().evaluate((el, d) => { el.scrollBy(0, d); }, dy);
  } else {
    const vp = await pageViewport(page); // reuse the shared viewport read; we only need w/h to aim the wheel
    await page.mouse.move(Math.floor(vp.w / 2), Math.floor(vp.h / 2));
    await page.mouse.wheel(0, dy);
  }
}

// ---- do: drive the shared window (no teardown); the human watches it happen ----
export async function doSession({ action, args = [], allowRemote = false, timeout = 4000, file = SESSION_FILE } = {}) {
  return withLivePage(async (page) => {
    page.setDefaultTimeout(timeout); // SHORT by default: a missed selector should fail fast in a smoke loop, not hang 15s (override with --timeout)
    try {
      switch (action) {
        case 'click':  await page.click(args[0]); break;
        case 'type':   await page.fill(args[0], args.slice(1).join(' ')); break;
        case 'press':  await page.keyboard.press(args[0]); break;
        case 'hover':  await page.hover(args[0]); break;
        case 'scroll': await scrollAction(page, args); break;
        // held key: down → wait <ms> → up. Needed for held-state controls (game movement, drag-style) that
        // a single press (instant down+up) is too brief to actuate. e.g. `do hold d 500` = move right ~0.5s.
        case 'hold':   await page.keyboard.down(args[0]); await page.waitForTimeout(Number(args[1]) || 500); await page.keyboard.up(args[0]); break;
        case 'nav':    await page.goto(guardUrl(args[0], allowRemote), { waitUntil: 'load' }); break;
        default: throw new Error(`unknown action "${action}" — use click|type|press|hold|hover|scroll|nav`);
      }
      return { ok: true, action, url: page.url() };
    } catch (e) {
      // A missed-OR-unactionable target is the #1 friction. An element may not resolve (wrong/exact
      // selector) OR resolve but not be actionable (covered by an overlay, disabled/readonly) — both
      // surface as a Timeout, so the hint names both fixes rather than only the selector one.
      const SELECTOR_ACTIONS = new Set(['click', 'type', 'hover', 'scroll']);
      const msg = e?.message || String(e);
      if (SELECTOR_ACTIONS.has(action) && /Timeout|waiting for|not found|no element|resolve|editable|intercept/i.test(msg))
        throw new Error(`${action} couldn't resolve or act on "${args[0]}" within ${timeout}ms — prefer a regex name from \`session read\` (e.g. role=tab[name=/Top/i]); re-read after a route/theme change (labels are state-dependent); or the element may be covered/disabled — \`look\` to check. (${msg.split('\n')[0]})`);
      throw e;
    }
  }, { file });
}

// ---- stop: close the shared window + clear the session ----
export async function stopSession({ file = SESSION_FILE, keepProfile = false } = {}) {
  const s = loadSession(file);
  // kill the Edge process tree — a dedicated-profile instance is independently killable; the
  // profile-path marker keeps a recycled pid (dead Edge, number reused elsewhere) safe from us
  killTree(s.pid, s.profile || PROFILE_DIR);
  fs.rmSync(file, { force: true });
  // remove the dedicated profile so nothing accumulates. Edge holds file locks for a beat after
  // taskkill, so retry through that window. (A guaranteed wipe ALSO runs at the next `session start`,
  // so the profile never accumulates across sessions even if this loses the race here.)
  let profileRemoved = true;
  if (!keepProfile && s.profile) profileRemoved = await removeDir(s.profile);
  // clear capture output too (transient look-*.png frames the agent has already read) — best-effort,
  // harmless if it loses the lock race since the next `session start` wipes it again.
  try { fs.rmSync(DEFAULT_OUT, { recursive: true, force: true }); } catch { /* leave it */ }
  return { stopped: s.pid, port: s.port, profileRemoved };
}

// rm a dir, retrying through the brief window where a just-killed Edge still holds file locks.
async function removeDir(dir, attempts = 8, delayMs = 300) {
  for (let i = 0; i < attempts; i++) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* locked */ }
    if (!fs.existsSync(dir)) return true;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return !fs.existsSync(dir);
}

export { SESSION_FILE };
