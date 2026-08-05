// Invocation accounting — STDLIB ONLY, deliberately.
//
// WHY HERE AND NOT IN THE CLI: the cost driver of a dispatched visual leaf is the number of model
// turns, and each turn is one harness invocation. Prose caps ("at most N calls") were measured to be
// defeated routinely, so the count has to be a fact the harness records rather than a number the
// leaf reports. probe.mjs's bootstrap is the single funnel every verb passes through BEFORE any
// playwright import, so the increment happens there — which means everything in this file must
// import nothing but node stdlib, or the funnel stops being cheap (and `glance --wait` /
// `__serve-child`, which must never load playwright, would drag the bundle in through the side door).
//
// The counter is keyed by the OUT DIR because that is the one input every dispatch brief already
// pins for both agents: `<out>/budget.json` sits beside `manifest.json`, the fused verbs merge it
// into the manifest, and the orchestrator can reject an over-budget verdict mechanically.
//
// Bookkeeping NEVER fails a run: every path here swallows its own errors. A budget file that can't
// be written is a lost measurement, not a lost capture.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Default capture directory: a namespaced OS-temp dir, NEVER the repo working tree — so frames
// (look/shot/run PNGs + manifest) can't be accidentally git-added or committed. Lives here rather
// than in capture.mjs (which re-exports it) so the stdlib-only bootstrap can resolve the same
// default the CLI will use, without importing anything that pulls playwright.
export const DEFAULT_OUT = path.join(os.tmpdir(), 'visual-probe-out');

const BUDGET_NAME = 'budget.json';
const MAX_KEPT = 200; // a long-lived out-dir must not grow an unbounded ledger

export function budgetFile(outDir) {
  return path.join(outDir, BUDGET_NAME);
}

// Resolve `--out DIR` out of a raw argv the same way the CLI's parser will, without building the
// whole option map: same "--key value" shape, and a bare `--out` (no value) falls back to default.
export function resolveOutDir(argv) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] !== '--out') continue;
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) break;
    return path.resolve(next);
  }
  return path.resolve(DEFAULT_OUT);
}

// Append one invocation record. Returns the ledger it wrote, or null if bookkeeping failed.
export function recordInvocation(outDir, verb) {
  try {
    fs.mkdirSync(outDir, { recursive: true });
    const file = budgetFile(outDir);
    const ledger = readBudget(outDir) || { invocations: [] };
    ledger.invocations.push({ verb: verb || '(none)', ts: new Date().toISOString() });
    if (ledger.invocations.length > MAX_KEPT) ledger.invocations = ledger.invocations.slice(-MAX_KEPT);
    fs.writeFileSync(file, JSON.stringify(ledger, null, 2));
    return ledger;
  } catch {
    return null; // never break a capture over accounting
  }
}

// A missing OR corrupt ledger reads as null — corrupt is treated as absent, never a crash.
export function readBudget(outDir) {
  try {
    const parsed = JSON.parse(fs.readFileSync(budgetFile(outDir), 'utf8'));
    if (!parsed || !Array.isArray(parsed.invocations)) return null;
    return parsed;
  } catch {
    return null;
  }
}

// The manifest's `budget` block: the ledger merged with this run's own stage timings.
//
// TWO COUNTS, because a leaf is judged on what ITS dispatch spent, not on what the out-dir has ever
// seen: `invocations` counts only entries at-or-after this run's EPOCH (the dispatch's own window —
// so a waited glance legitimately reads 2: the wait plus the capture), while `invocations_total` is
// the whole ledger against this out-dir. Without the split, a re-used out-dir made every later
// dispatch look over-budget. `verbs` is the ordered verb list behind the total — what makes an
// over-budget count diagnosable rather than just a number. `invocations_total` saturates at
// MAX_KEPT (200): the ledger is trimmed to that tail, so a bigger number is not observable here.
export function budgetBlock(outDir, { launches = 0, stageMs = {}, wallMs = 0, epochMs = null } = {}) {
  const ledger = readBudget(outDir);
  const all = ledger ? ledger.invocations : [];
  const since = Number.isFinite(epochMs) ? all.filter((i) => Date.parse(i.ts) >= epochMs) : all;
  return {
    invocations: since.length,
    invocations_total: all.length,
    verbs: all.map((i) => i.verb),
    launches,
    stage_ms: {
      import: round(stageMs.import), launch: round(stageMs.launch), capture: round(stageMs.capture),
      flush: round(stageMs.flush), compose: round(stageMs.compose),
    },
    wall_ms: round(wallMs),
  };
}

function round(v) { return Number.isFinite(v) ? Math.round(v) : 0; }

// Manifest writes go through a rename so a poller (`glance --wait`) can never read a half-written
// file: the completeness signal is the presence of the `budget` block, and a torn read would either
// fail to parse or look incomplete forever.
//
// The temp name is UNIQUE PER WRITER (pid + random): a fixed `<file>.tmp` is a shared name, so two
// processes writing the same out-dir interleave their bytes in it and the rename publishes the
// mixture — the exact torn read this function exists to prevent.
export function writeManifestAtomic(file, manifest) {
  const tmp = `${file}.${process.pid}.${Math.random().toString(36).slice(2, 8)}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(manifest, null, 2));
  fs.renameSync(tmp, file);
  return file;
}
