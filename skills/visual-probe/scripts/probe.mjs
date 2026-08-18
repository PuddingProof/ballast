#!/usr/bin/env node
// visual-probe — stdlib-only BOOTSTRAP + readiness gate for the real CLI (lib/cli.mjs).
//
// WHY SPLIT: this is the bin entrypoint and the first code to run after a plugin update, when
// node_modules is absent (never vendored — re-armed every update). A top-level static
// `import 'playwright'` hoists ahead of all code, so every subcommand — even --help — used to die
// with a raw ERR_MODULE_NOT_FOUND, and the documented fix (npm ci) trips the interactive
// package-install-guard prompt, which deadlocks an unattended session. So: stdlib-only imports,
// a side-effect-free `preflight` subcommand safe to run unattended, the same check ahead of every
// other subcommand (friendly fail-fast replaces the raw ESM error), and a DYNAMIC import of the
// real CLI only after the check passes (dynamic imports evaluate at runtime — not hoisted).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Process t0 — the anchor for every wall-clock number the fused verbs stamp into a manifest. Taken
// here, in the bootstrap, so it covers node's own boot and the playwright import, not just the part
// of the run that happens after the CLI is loaded.
const T0 = Date.now();

// Budget accounting lives in lib/budget.mjs (stdlib-only) and is loaded DYNAMICALLY, tolerating
// absence. Two reasons, both load-bearing: a static import would hoist ahead of everything and make
// a partial/damaged plugin copy die with a raw ESM error instead of this file's friendly NOT-READY
// line (the exact regression the bootstrap split exists to prevent — pinned by test_bootstrap.sh's
// no-lib/ copy), and accounting must never be the reason a capture fails. A lost count is a lost
// measurement; a raw stack trace is a lost session.
async function budgetLib() {
  try { return await import('./lib/budget.mjs'); } catch { return null; }
}

// This file lives in scripts/, one level below the skill root (package.json, node_modules/ stay at
// the skill root as the npm resolution anchor) — resolve from import.meta.url, not process.cwd(),
// so any caller cwd works.
const SKILL_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// Readiness = node_modules/playwright present AND its version === the exact pin in package.json.
// Exact-pin repo (no ^/~): plain string equality is correct; revisit only if the pin style changes.
function checkReadiness() {
  // The one attended-fix instruction, built once — three failure branches share it so a future
  // rewording can't drift between them.
  const attendedFix = `In ${SKILL_DIR}, run this ATTENDED (it will trip the permission-guard prompt): PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci`;

  // The skill's OWN package.json can be missing/corrupt too (partial plugin update, hand edit) —
  // npm ci can't fix that; a plugin reinstall can. Guarded so even this degrades to a friendly
  // NOT-READY instead of the raw stack trace the bootstrap exists to eliminate.
  let pinned;
  try {
    pinned = JSON.parse(fs.readFileSync(path.join(SKILL_DIR, 'package.json'), 'utf8')).dependencies.playwright;
    if (!pinned) throw new Error('no dependencies.playwright pin');
  } catch (e) {
    return { ready: false, reason: 'skill-damaged', pinned: null, installed: null, skillDir: SKILL_DIR,
      fix: `the skill's own package.json is unreadable (${e.message}) — the plugin copy looks damaged; reinstall or update the ballast plugin.` };
  }

  const installedPkgPath = path.join(SKILL_DIR, 'node_modules', 'playwright', 'package.json');
  if (!fs.existsSync(installedPkgPath)) {
    return { ready: false, reason: 'missing', pinned, installed: null, skillDir: SKILL_DIR,
      fix: `node_modules/playwright not found. ${attendedFix}` };
  }
  let installed;
  try { installed = JSON.parse(fs.readFileSync(installedPkgPath, 'utf8')).version; }
  catch (e) {
    return { ready: false, reason: 'corrupt', pinned, installed: null, skillDir: SKILL_DIR,
      fix: `node_modules/playwright/package.json unreadable (${e.message}). ${attendedFix}` };
  }
  if (installed !== pinned) {
    return { ready: false, reason: 'version-mismatch', pinned, installed, skillDir: SKILL_DIR,
      fix: `installed playwright ${installed} != pinned ${pinned}. ${attendedFix}` };
  }
  return { ready: true, pinned, installed, skillDir: SKILL_DIR };
}

// stderr = human one-liner, always. stdout gets the machine-parseable JSON line ONLY when the
// caller asked for machine-readable state (`preflight`): a failed delegating command keeps stdout
// clean, because its stdout contract belongs to the command it never reached (e.g. shot's
// manifest path) — exit code + stderr carry the failure there.
function reportReadiness(r, emitJson = true) {
  if (r.ready) console.error(`[visual-probe] ready — playwright ${r.installed} (pinned ${r.pinned})`);
  else console.error(`[visual-probe] NOT READY (${r.reason}) — ${r.fix}`);
  if (emitJson) console.log(JSON.stringify(r));
}

const HELP = `visual-probe — on-demand visual-verification harness (pinned Playwright library; no MCP/daemon)

Usage:
  node probe.mjs glance --url <base> [--urls u1,u2,…] [--matrix M] [--states F] [--skip-drive-hooks]
                      [--settle MS] [--deadline MS] [--suppressions F] [--epoch ISO] --out DIR
                      (FUSED: preflight + viewport-clamped capture + rung-0 + contact sheets, ONE browser)
  node probe.mjs glance --wait <out-dir> [--since ISO] [--timeout MS]   (stdlib-only poll for a capture-ahead manifest; exit 3 = not ready, run it yourself)
  node probe.mjs review-capture --url <base> [--urls u1,u2,…] [--states F] [--matrix M] [--no-dsf-triad]
                      [--group RE] [--settle MS] [--deadline MS] [--epoch ISO] [--skip-drive-hooks] --out DIR
                      (FUSED, deep: full-page frames + state sweep + console listeners + grouped sheets)
  node probe.mjs measure --url <u> [--selector S | --selectors S1,S2] [--checks contrast,rects,fonts,targets,overflow]
                      [--matrix M] [--settle MS] --out DIR      (deterministic instruments -> compact summary on stdout + measure-<n>-<pid>.json)
  node probe.mjs crop --url <u> --selector S [--matrix M] [--magnify N] --out DIR
                      (magnified region PNGs; MERGES into the out-dir's manifest under "crops")
  node probe.mjs preflight
  node probe.mjs doctor
  node probe.mjs selftest
  node probe.mjs shot <url|path> [--matrix M] [--crop SEL] [--magnify N] [--out DIR] [--allow-remote] [--settle MS]
  node probe.mjs run  <scenario.mjs> [--url <url>] [--matrix M] [--crop SEL] [--magnify N] [--out DIR]
                      [--baseline CELL] [--diff-threshold T] [--headed] [--allow-remote] [--cdp WS] [--timeout MS] [--settle MS]
  VISUAL_STATES=/abs/visual-states.json node probe.mjs run scenarios/states-from-manifest.mjs [--url <url>]
                      (drives every state in a project's state-forcing contract manifest; see below)
  node probe.mjs compose <out-dir> [--group RE] [--crop-content] [--tile-height N] [--max-side N]
                      (contact-sheet mosaic of a finished capture dir; re-screenshotted by this harness)
  node probe.mjs serve start <dir> [--port N]                      (detached ISOLATED static server — loopback, no-store, GET/HEAD-only; port auto-picked)
  node probe.mjs serve status | stop                               (find / kill it; 'start' reaps a stale one itself)
  node probe.mjs session start [url] [--port N] [--allow-remote]   (open the shared headed window)
  node probe.mjs session look [--crop SEL] [--out DIR]             (snapshot the current live state)
  node probe.mjs session read [selector]                          (structured TEXT + ready-to-paste role= selectors + viewport)
  node probe.mjs session do <click|type|press|hold|hover|scroll|nav> <args…>   (drive the shared window; "hold <key> <ms>" = held movement; "scroll [sel] <amt|top|bottom>")
  node probe.mjs session stop                                     (close it)

Commands:
  glance   the cheap rung, FUSED into one process and ONE browser launch: readiness check, a
           viewport-clamped capture of every matrix cell (no full-page slicing), rung-0 geometry
           assertions at each shutter, and the contact sheets — all reusing the same browser.
           A failed cell is a NAMED HOLE carrying its error (never a retry), and every hole is
           RENDERED into the sheet as a labeled placeholder tile; placeholder tiles are never counted
           as captured cells. --deadline MS hard-stops the cell loop and flushes a partial manifest
           whose unreached cells are holes. "blocked" is reserved for ZERO captures. The manifest
           carries sheets[], generatedAt, budget{invocations,invocations_total,launches,stage_ms,wall_ms}
           and deadline_hit. With --states, the state sweep runs against --url ONLY; any --urls extras
           are captured as plain cells (a sweep per target would duplicate every state label).
  review-capture  the deep rung: same fusion at matrix scale — full-page frames, the state sweep
           (--states), console/pageerror/requestfailed listeners attached per cell, the DSF fidelity
           triad added at the NATIVE breakpoint only (--no-dsf-triad opts out), sheets grouped per
           route×theme (--group RE overrides).
  measure  deterministic instruments on named selectors: contrast (measured from composited PIXELS,
           with the computed-style arithmetic reported alongside), rects (overlap on the PAINTED box,
           so an ellipsized text rect never invents a collision), fonts, targets (hit-target size),
           overflow. A COMPACT per-check summary to stdout (the full record goes to
           <out>/measure-<n>-<pid>.json — measurements accumulate; n is the order they were taken in,
           the pid keeps two concurrent writers off one filename). One viewport per call: --matrix's
           FIRST cell, default 1280x800@1.
  crop     magnified recapture of one selector, post-hoc; merges into the out-dir manifest's "crops".
  preflight  stdlib-only readiness check: node_modules/playwright installed at the pinned version?
             Safe unattended (no network, no npm, no side effects). Run after a plugin update / before
             dispatch if unsure the tool is ready.
  doctor   env self-check (Edge channel, pinned version, non-integer DSF capture)
  selftest regression guard doctor doesn't cover: read.mjs role→selector mapping round-trips + capture
  shot     navigate + capture the fidelity matrix (no interaction)
  run      run a scenario (default-exports async (page, h) => {}) per matrix cell; failed asserts -> non-zero exit
           scenarios/states-from-manifest.mjs is bundled: point it at a project's visual-states.json
           (the state-forcing contract; see references/state-contract.md) via the VISUAL_STATES env
           var (absolute path) — or run from the project root, where it defaults to
           ./.claude/visual-states.json. It drives every enumerated state (one-hot + an all-worst
           composed state per overlay flag — one per route when no overlay axis) and asserts each state's marker held via
           h.snapshotForced before capturing. VISUAL_STATES_FULL=1 runs the full axis cross-product
           instead of the default one-hot + composed-worst slice. --skip-drive-hooks (leaf mode)
           never imports a manifest drive hook; states needing one are reported as coverageHoles.
  compose  slice every frame in an out-dir into viewport-height segments and lay them out as one
           contact sheet, re-screenshotted by this same harness (no image dependency). Sheets stay
           under --max-side (default 1568 — the agent image-Read downscale ceiling); frames that
           don't fit produce MORE sheets, never a bigger one. --group RE splits sheets by the first
           capture group of RE against "<label> · <cell>" (e.g. --group "(light|dark)"), and
           --crop-content trims empty side margins. The mosaic is an overflow ROUTER: per tile,
           clear or escalate — read an escalated cell at full resolution before any verdict.
  serve    bundled isolated static server — the ONLY origin a co-drive should load (never the user's
           live dev server: the app's own lifecycle beacons can arm its shutdown). Hazard-proof by
           construction: no /api (beacon POST -> inert 405), Cache-Control: no-store (no stale CSS/JS),
           event-loop concurrency (immune to the held-keep-alive wedge), loopback + GET/HEAD only.
  session  a shared live browser that you AND Claude both drive (headed, persistent, CDP-attached)

Flags:
  --matrix            preset (default | quick | desktop) or inline "WxH@DSF,WxH@DSF"   [default: default]
  --crop SEL          clip+magnify one element (CSS selector)
  --magnify N         nearest-neighbor magnify factor for cropped snapshots            [default: 8]
  --out DIR           output dir: manifest.json + native .png + .xN.png crops          [default: OS temp dir's ($TMPDIR/%TEMP%) visual-probe-out]
  --cdp WS            attach to a live browser / WebView2 over CDP (Tier-1; DSF matrix disabled)
  --allow-remote      permit non-local URLs (fail-closed local-origin guard otherwise)
  --headed            run headed (final-confirm pass)
  --baseline CELL     divergence baseline cell label                                   [default: first cell]
  --diff-threshold T  aHash Hamming distance above which a cell is flagged divergent    [default: 6]
  --timeout MS        per-action timeout                              [default: 30000 run/shot; 4000 session do]
  --settle MS         post-ready dwell before each shutter — for a project animation/crossfade that
                      completes AFTER load / readySignal. Per capture (per state), not per run  [default: 0]
  --suppressions F    JSON file of rung-0 suppressions (a bare array, or a state manifest carrying
                      a top-level "suppressions": [{assert, selector, reason}])
  --no-rung0          skip the in-page geometry assertions (on by default; findings are advisory)
  --urls u1,u2        extra routes for a fused verb (each becomes its own labelled snapshot)
  --states F          absolute path to a project's visual-states.json (fused verbs run the bundled
                      state scenario over it; pair with --skip-drive-hooks in a dispatched leaf).
                      The sweep runs against --url only; --urls extras stay plain cells
  --deadline MS       fused verbs only: hard wall-clock stop. Unreached cells become named holes and
                      the partial manifest is still written (exit 0)                  [default: none]
  --epoch ISO         fused verbs: the dispatch epoch this run belongs to. budget.invocations counts
                      only calls at-or-after it; invocations_total stays cumulative [default: process start]
  --wait DIR          fused verbs only: poll DIR for a complete manifest instead of capturing.
                      Exit 0 + manifest path, or a single WAIT_TIMEOUT line + exit 3   [--timeout: 90000]
  --since ISO         --wait only: accept a manifest only if its capture started at or after this
                      dispatch epoch; older evidence keeps polling  [default: wait start − 120s]
  --selector S        measure/crop: the element to measure or magnify
  --selectors S1,S2   measure: several selectors in one invocation
  --checks LIST       measure: any of contrast,rects,fonts,targets,overflow           [default: all]
  --no-dsf-triad      review-capture: skip the 1.5×/2.0× fidelity cells at the native breakpoint

Budget: every invocation is counted in <out>/budget.json (keyed by --out) and merged into the
manifest's "budget" block — "invocations" is this dispatch's own count (see --epoch),
"invocations_total" the whole ledger. A dispatched leaf echoes the dispatch-scoped number, and the
caller can reject an over-budget verdict mechanically.

Output: read manifest.json FIRST, then read ONLY the .xN.png magnified crops of cells flagged diverges:true.
manifest.rung0 carries deterministic geometry findings (overlap / overflow / contrast / broken-image /
offscreen / misalignment) that pre-locate defects before any image read — SHADOW-LOGGED advisory data:
it never affects \`pass\` or the exit code.`;

// Verbs whose contract is "a manifest exists at the end, whatever happened": their failures must be
// reported IN the manifest (a dispatched leaf reads the manifest, not this process's stderr), so a
// not-ready environment writes a `blocked` manifest instead of dying with a bare exit 1.
const FUSED_VERBS = new Set(['glance', 'review-capture']);

// Fallback freshness window for a `--wait` with no `--since`: evidence whose capture started more
// than this before the wait began belongs to an earlier cycle.
const WAIT_GRACE_MS = 120000;

// stdlib-only flag read for the bootstrap — same "--key value" shape as the CLI's parser.
function flagValue(args, name) {
  for (let i = 0; i < args.length; i++) {
    if (args[i] !== name) continue;
    const next = args[i + 1];
    return next === undefined || next.startsWith('--') ? true : next;
  }
  return undefined;
}

// `glance --wait <out-dir>` — the capture-ahead handshake. The gate may fire the real capture in the
// background at dispatch time; the leaf then overlaps its own spawn with that capture by waiting on
// the manifest instead of launching a second one.
//
// STDLIB ONLY, AND BEFORE THE READINESS GATE, DELIBERATELY: this path must never pay the multi-MB
// playwright import (the whole point is that it is nearly free), and it must resolve even in an
// environment where the real capture could not run — a clean "not ready, run the fallback" beats a
// wait that only ends in a timeout.
//
// COMPLETENESS is the presence of the `budget` block: the fused verbs write their manifest through a
// rename, so a poller can never see a torn file, and `budget` is stamped last.
//
// FRESHNESS is the DISPATCH EPOCH: a prior cycle's manifest sitting in the out-dir is complete in
// every way this poll can see, so a wait with no epoch resolved instantly against stale evidence and
// the leaf reviewed the build it was dispatched to replace. `--since <ISO>` (the gate stamps the
// dispatch's own epoch into the brief) accepts only a manifest whose capture STARTED at or after it;
// anything older keeps polling to the timeout, which is the same exit-3 "run it yourself" path a
// missing manifest takes. With no `--since`, a 120s grace before the wait began stands in — long
// enough for a capture-ahead fired at dispatch, short enough to reject last cycle's leftovers. A
// manifest with no `generatedAt` at all predates this contract and is treated as stale.
async function waitForManifest(dir, timeoutMs, sinceMs) {
  const file = path.join(dir, 'manifest.json');
  const deadline = Date.now() + timeoutMs;
  let sawStale = null;
  for (;;) {
    try {
      const m = JSON.parse(fs.readFileSync(file, 'utf8'));
      const at = m && m.generatedAt ? Date.parse(m.generatedAt) : NaN;
      const fresh = Number.isFinite(at) && at >= sinceMs;
      if (m && m.budget && typeof m.budget === 'object' && !fresh) sawStale = m.generatedAt || '(no generatedAt)';
      if (m && m.budget && typeof m.budget === 'object' && fresh) {
        console.error(`[visual-probe] manifest ready after ${Date.now() - T0}ms of waiting — ${file}`);
        console.log(file);
        return 0;
      }
    } catch { /* absent, or mid-write */ }
    if (Date.now() >= deadline) {
      // ONE machine line, exit 3: distinct from every other exit code so the caller can branch on it
      // mechanically and run the fused verb itself. Never a hang — that is the whole contract.
      console.error(`[visual-probe] no complete manifest in ${dir} after ${timeoutMs}ms`
        + (sawStale ? ` (one was there, generated ${sawStale} — older than the dispatch epoch ${new Date(sinceMs).toISOString()})` : '')
        + ' — run the fused verb yourself');
      console.log('WAIT_TIMEOUT');
      return 3;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

// A not-ready environment still owes a fused verb's caller a manifest — with the failing check
// verbatim, so the leaf can report `blocked` with a real reason instead of "the command failed".
function writeBlockedManifest(B, outDir, verb, r) {
  if (!B) return; // no budget lib => damaged copy; stderr already carries the failing check
  try {
    fs.mkdirSync(outDir, { recursive: true });
    B.writeManifestAtomic(path.join(outDir, 'manifest.json'), {
      verb, generatedBy: 'visual-probe', generatedAt: new Date(T0).toISOString(), target: null,
      cells: [], cellGeometry: [], snapshots: [], asserts: [],
      coverageHoles: [{ label: '(every cell)', cell: '', kind: 'blocked', reason: r.fix }],
      console: [], rung0: [], rung0UnusedSuppressions: [],
      sheets: [], sheetTally: { sheets: 0, tiles: 0, placeholders: 0 },
      deadline_hit: false,
      blocked: { reason: r.reason, detail: r.fix, check: 'preflight', pinned: r.pinned, installed: r.installed },
      pass: false,
      budget: B.budgetBlock(outDir, { launches: 0, stageMs: {}, wallMs: Date.now() - T0, epochMs: T0 }),
      note: 'BLOCKED before any capture: the harness environment failed its readiness check (see `blocked`). ' +
        'Nothing was captured — this is not a verdict about the app. Report `blocked` with the detail verbatim.',
    });
  } catch { /* bookkeeping must never mask the real failure */ }
}

const argv = process.argv.slice(2);
const cmd = argv[0];

if (cmd === 'help' || cmd === '--help' || cmd === '-h' || cmd === undefined) {
  console.log(HELP);
  process.exit(0);
} else if (cmd === '__serve-child') {
  // hidden: the detached static server spawned by `serve start`. Dispatched HERE rather than in
  // lib/cli.mjs so it never imports playwright — a file server has no use for a browser bundle, and
  // the child paid the whole parse on every start.
  const { runServeChild } = await import('./lib/serve.mjs');
  runServeChild({ root: argv[1], port: +argv[2] });
} else {
  // Budget accounting: EVERY verb invocation is counted, keyed by the out-dir the caller named, in
  // the one place no entry point can bypass (see lib/budget.mjs for why the count is harness-side).
  const B = await budgetLib();
  const waitTarget = flagValue(argv, '--wait');
  const outDir = typeof waitTarget === 'string' ? path.resolve(waitTarget)
    : B ? B.resolveOutDir(argv) : path.resolve(String(flagValue(argv, '--out') ?? '.'));
  if (B) B.recordInvocation(outDir, cmd);

  if (waitTarget !== undefined && FUSED_VERBS.has(cmd)) {
    const t = flagValue(argv, '--timeout');
    const timeoutMs = typeof t === 'string' && Number.isFinite(+t) && +t > 0 ? Math.round(+t) : 90000;
    const since = flagValue(argv, '--since');
    let sinceMs = T0 - WAIT_GRACE_MS;
    if (since !== undefined) {
      const parsed = typeof since === 'string' ? Date.parse(since) : NaN;
      if (!Number.isFinite(parsed)) {
        // Usage (exit 2), never a silent fallback: a mistyped epoch that quietly degraded to the
        // grace window would accept exactly the stale evidence --since exists to reject.
        console.error(`[visual-probe] --since expects an ISO timestamp (got ${since === true ? '(no value)' : since})`);
        process.exit(2);
      }
      sinceMs = parsed;
      // Fail fast on an impossible epoch: a --since meaningfully in the future can never be
      // satisfied by existing evidence — the observed failure mode is a hand-composed timestamp
      // minutes ahead of the clock, burning the full timeout on a doomed poll. 15s of skew
      // tolerance covers a capture-ahead racing this wait; beyond that the epoch is wrong.
      const aheadMs = sinceMs - Date.now();
      if (aheadMs > 15000) {
        console.error(`[visual-probe] --since is ${Math.round(aheadMs / 1000)}s in the future — no existing capture can satisfy it. `
          + 'Copy the --since value from the capture\'s DISPATCH line instead of composing a timestamp.');
        process.exit(2);
      }
    }
    process.exit(await waitForManifest(outDir, timeoutMs, sinceMs));
  } else if (cmd === 'preflight') {
    const r = checkReadiness();
    reportReadiness(r);
    process.exit(r.ready ? 0 : 1);
  } else {
    const r = checkReadiness();
    if (!r.ready) {
      reportReadiness(r, false);
      if (FUSED_VERBS.has(cmd)) writeBlockedManifest(B, outDir, cmd, r);
      process.exit(1);
    }
    try {
      const tImport = Date.now();
      const cli = await import('./lib/cli.mjs');
      await cli.main(argv, { t0: T0, importMs: Date.now() - tImport });
    } catch (e) {
      // Defense in depth: preflight vets playwright, not lib/cli.mjs's own integrity (syntax error,
      // missing lib file) — those still get a friendly line, never a raw stack trace.
      console.error('[visual-probe] ERROR:', e.message);
      process.exit(1);
    }
  }
}
