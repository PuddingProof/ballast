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

Output: read manifest.json FIRST, then read ONLY the .xN.png magnified crops of cells flagged diverges:true.
manifest.rung0 carries deterministic geometry findings (overlap / overflow / contrast / broken-image /
offscreen / misalignment) that pre-locate defects before any image read — SHADOW-LOGGED advisory data:
it never affects \`pass\` or the exit code.`;

const argv = process.argv.slice(2);
const cmd = argv[0];

if (cmd === 'help' || cmd === '--help' || cmd === '-h' || cmd === undefined) {
  console.log(HELP);
  process.exit(0);
} else if (cmd === 'preflight') {
  const r = checkReadiness();
  reportReadiness(r);
  process.exit(r.ready ? 0 : 1);
} else {
  const r = checkReadiness();
  if (!r.ready) { reportReadiness(r, false); process.exit(1); }
  try {
    const cli = await import('./lib/cli.mjs');
    await cli.main(argv);
  } catch (e) {
    // Defense in depth: preflight vets playwright, not lib/cli.mjs's own integrity (syntax error,
    // missing lib file) — those still get a friendly line, never a raw stack trace.
    console.error('[visual-probe] ERROR:', e.message);
    process.exit(1);
  }
}
