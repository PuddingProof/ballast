# ballast ⚓

A verification-first agentic harness for Claude Code: docs discipline, session postmortems, plan-execution handoff, adversarial audits, and secure-by-default guards — packaged as one plugin so a whole working philosophy installs in two commands.

The ballast is the weight in the hull that keeps a ship steady under sail. Same job here: these skills and hooks don't add horsepower, they keep an agentic workflow upright — verified claims, gated docs, reviewed commits, tiered delegation.

## What's inside

**Skills** (invoke as `/ballast:<name>`, or Claude triggers them by description):

| Skill | What it does |
|---|---|
| `session-postmortem` | Single-pass transcript digest → one capped, evidence-cited report (fork, backgrounded) |
| `durable-docs` | The 6-gate for any durable doc write — write the reusable class, not the triggering instance |
| `plan-handoff` | Plan→execution protocol: orchestrate top-tier, dispatch execution to `plan-executor` agents |
| `integration-gate` | Seam-sweep before declaring a multi-part change done |
| `adversarial-audit` | Multi-agent whole-codebase audit: parallel lenses → adversarial verification → triaged fixes (`/ballast:audit`) |
| `adhd` | Parallel divergent ideation under different cognitive frames, for open-ended high-stakes questions *(private build only for now — excluded from the public release pending upstream-license verification)* |
| `critical-analysis` | Session stance of calibrated skepticism for fact-finding: claims (the user's and Claude's own) are hypotheses to verify — affirm what survives scrutiny, correct with specifics, stop when the analysis is done (`/ballast:critical-analysis`, `off` lifts it) |
| `refactor-fit` | Opt-in: weigh structural refactoring deliberately as part of a feature change |
| `freehand` | Canonical home of the delegated-autonomy mode — the standing contract lives in the skill body; invoking it toggles the mode on/off and settles the statusline chip (`/ballast:freehand on\|off`) |
| `skill-forge` | Author, test, benchmark, and trigger-tune skills (bundled eval harness) |
| `visual-probe` | See and drive a real rendered browser UI: viewport/DPI capture matrix, scenario scripts, co-driving; drives a project's declared state-forcing contract (`.claude/visual-states.json`, schema: `skills/visual-probe/references/state-contract.md`) via the bundled `states-from-manifest.mjs` scenario, with per-state marker asserts (`snapshotForced`) |
| `visual-verification-gate` | The end-to-end workflow for frontend/visual work: arms on a frontend-file signal, owns the session's one origin, the in-loop see-and-fix reflex, and the two-rung done gate — a cheap `visual-glance` pass by default, the deeper `visual-reviewer` instrument only on named triggers (new surface, redesign, audit, measured-contrast, state-matrix coverage) |
| `harness-sweep` | Cross-project postmortem sweep, script-driven: one `ballast-sweep` pass turns the whole corpus into a pre-compressed bundle (enumeration, generation-aware parsing, chain graph, ledgers inlined, appendix tables) and the forked skill spends its context adjudicating. Default = incremental triage digest of unseen reports (watermark-based); `--focus <topic\|project\|report-path>` = targeted dossier, project-scoped sweep, or single-report digest; `--deep <range>` = full audit pipeline (cluster → placement judge + adversarial skeptic → completeness critic) over every report in the date range, swept or not, combinable with `--focus`; consumes the HARNESS-RECS and DRIFT-SIGHTINGS ledgers. No companion agents. For plugin-source-repo/hub sessions. Slash form is install-dependent: `/harness-sweep` or `/ballast:harness-sweep` |

**Agents:** `plan-executor` (+ `-light` / `-hard` tier variants) for spec-faithful implementation batches; `visual-glance` (Sonnet) as visual-verification-gate's default rung — cheap, capped, code-blind; `visual-reviewer` (Opus) as its opt-in deep instrument, dispatched only on named triggers — three dispatch modes sized to the change (targeted / full / delta, plus facet dispatch for parallel splits), derives its own worst-corner matrix, consumes a project's state-forcing contract, five-verdict output with scope on the verdict line.

**Hooks** (auto-active once the plugin is enabled):

- *Guards:* `git-commit-guard` (review-before-commit nudges + hard block on chained guarded git ops), `package-install-guard` (supply-chain "ask" on installs / remote-exec), `askuserquestion-recommend` (questions must lead with a recommendation), `doc-write-guard` (durable-doc writes gated through `durable-docs`), `commit-review-gate` (shadow log: computes the review-gate decision on guarded commits and ledgers WOULD-BLOCK — the observation window before any enforce flip).
- *Mode injectors:* `freehand-mode` (keyword sensor for delegated autonomy: short adjudication stub routing a genuine grant to the `freehand` skill — the contract's home — plus a one-line standing-grant mirror on every prompt while the mode is on), `subagent-fanout` (per-tier model calibration on fan-out keywords), `plan-authoring` + `plan-handoff` (executor-ready plans, handoff at approval). `plan-handoff` also raises a per-session statusline chip (see the Statusline section); the freehand skill raises/clears its own chip on grant/revoke.
- *Infra:* `ballast-principles` (the philosophy block, injected at session start), `ballast-allow` (opt-in — inert until you create the `allow-standing-grants` marker, see *User-side configuration*; then auto-allows four of the plugin's own commands and nothing else: `ballast-extract` (read-only except `digest`'s bounded write — fixed filenames into its own `--out` dir), `ballast-sweep` (same bounded output-dir rule; its `advance` subcommand also rewrites the SWEEP-STATE watermark registry it owns, after validating the driving manifest — default-mode only, already-registered projects only, report-basename-shaped watermarks only), the narrowly-bounded `ballast-mode` chip writer, and the `ballast-review` trampoline (which refuses `--fix`)), `mode-state-cleanup` (SessionEnd hygiene for statusline chip state), `harness-sweep-nudge` (SessionStart soft nudge, fires only at the plugin source repo; counts unswept postmortem reports + unregistered drift sightings against the user-home SWEEP-STATE watermark), `inline-churn-nudge` (shadow context-economy observer; logs in-line edit run-lengths between subagent dispatches, never injects — the observation window before a future soft-nudge threshold flip).

## Install

```bash
# 1. Add this repo as a marketplace:
claude plugin marketplace add PuddingProof/ballast

# 2. Install the plugin:
claude plugin install ballast@ballast
```

Or from inside a Claude Code session: `/plugin marketplace add PuddingProof/ballast` then `/plugin install ballast@ballast`.

Or from claude.ai: Settings → Extensions → Add marketplace → `PuddingProof/ballast`.

Claude Code shows a trust warning on install — plugins run real hooks and scripts at your user privilege. That's this plugin's entire point (guards, gates, injectors), but read the inventory it shows you; don't bounce off the prompt.

## Statusline mode chips (optional, one-time per machine)

A `statusLine` row under the composer: `<project dir> · <model> (<effort>) · NN% context` when idle — the model/effort segment answers "which session is this?" long after the TUI's start-of-session banner has scrolled away, and the context figure reddens in steps keyed to *absolute* input tokens (not the percentage it displays), so the first step lands where long-context pricing does rather than at an arbitrary fraction of a window whose size varies by model. Past a ` ▸ `, glanceable per-session chips appear while a ballast mode is active — solid `✈️ freehand` once Claude confirms a genuine grant (either keyword, one chip), `📋 plan-handoff` while an approved plan is executing, `🧠 critical-analysis` while the skepticism stance is held. Chips are keyed by session id, so parallel sessions never bleed into each other; renderer-side TTLs age out anything a session forgot to settle.

Plugins cannot ship the `statusLine` settings key, so enable it once per machine, from any session with ballast enabled:

```bash
ballast-statusline install     # idempotent; backs up settings.json once; hot-reloads, no restart
ballast-statusline uninstall   # removes only ballast's own statusLine
```

It refuses to overwrite a statusLine you configured yourself (`--force` to override).

## User-side configuration

All ballast state and configuration lives in your home dir (`~/.claude/ballast/`), never in the plugin dir (which is replaced on update):

- `~/.claude/ballast/docguard-exclude` — one glob per line (`#` comments allowed); matching paths are excluded from `doc-write-guard`'s gate and nudge entirely. For files your own doc-governance regime owns.
- `~/.claude/ballast/allow-standing-grants` — opt-in marker (create the empty file to enable). With it, `ballast-allow` auto-approves the plugin's own narrowly-matched commands (`ballast-extract`, `ballast-sweep` — whose `advance` subcommand writes the SWEEP-STATE watermark registry behind manifest validation — the `ballast-mode` chip writer, and the review trampoline); without it those commands simply hit the normal permission prompt each time. Tradeoff to weigh: the matcher is strict (full-match, shell metacharacters rejected, single-line only), but this is still a standing auto-approval — leave it off if your posture forbids standing grants.
- `~/.claude/ballast/modes/` — per-session statusline chip state (managed automatically).

## Prerequisites

- **git** (you have it — Claude Code requires it; on Windows, Git for Windows provides the bash that hooks run under)
- **Python 3** — as `python3` or `python` on PATH; the dispatcher probes and verifies at runtime (a Windows Microsoft-Store stub is detected and skipped)
- **Node.js + npm** — only for `visual-probe`; on first use run `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci` in the skill directory (Claude will offer; the install prompt you'll see is `package-install-guard` doing its job)
- A system **Edge or Chrome** for `visual-probe` (it drives your installed browser; no bundled download)

## Updating

Updates are deliberate, snapshot-style: pull a new version with

```bash
claude plugin update ballast
```

If that reports nothing to update — or the Update button in a GUI client (e.g. Claude Desktop) is disabled — your marketplace catalog is stale, not the plugin. Refresh the catalog first, then update:

```bash
claude plugin marketplace update ballast
claude plugin update ballast
```

Versions bump explicitly in `.claude-plugin/plugin.json` per release; each release lands on this public repo as one snapshot commit tagged `vX.Y.Z`. Changes are tracked in [CHANGELOG.md](CHANGELOG.md). Auto-update stays off for third-party marketplaces by default — that's the intended model here.

## Windows notes

- Hooks and `bin/` wrappers run under Git Bash (ships with Git for Windows). Nothing to configure.
- `python3` on a stock Windows box is often the Microsoft Store stub — ballast detects that and falls back to `python` (and `py -3`).

## macOS & Linux notes

- Everything routes through `bash` and a probed `python3`/`python` — no PowerShell dependency anywhere in shipped content.
- `visual-probe` looks for system Edge, then Chrome/Chromium (`microsoft-edge-stable`, `google-chrome`, `chromium`, …). On a minimal Linux VM install one of those first.
- Cross-OS note: this plugin's POSIX branches are code-reviewed and statically verified; live verification on macOS/Linux is still pending. If something misbehaves, an issue report with the exact command is the fastest path.

## Philosophy

The long version installs itself — the `ballast-principles` hook injects the standing philosophy at every session start. The short version:

1. **Claims require verification.** Done means verified; uncertainty is stated, not smoothed over.
2. **Security is a default lens**, not a separate pass — and installs are never silent.
3. **Prefer built-in / lightweight / bespoke** — a heavy dependency must name its tradeoff.
4. **Judgement stays top-tier; mechanical work tiers down.** Plans hand off to executors; orchestrators verify.
5. **Durable docs earn their place** — write the reusable class, gate it, keep it terse.
6. **User rules win.** The harness never fights your standing instructions, preferences, or permission gates — on explicit tension it backs off and surfaces it.

Two plugin-design tenets in the same spirit: **authority is opt-in** — anything that grants or extends standing authority (like `ballast-allow`'s auto-approvals) ships disabled and is enabled deliberately, tradeoffs documented; and **guards fail open, never silently** — a hook's own bug can't brick a session (the action just falls through to Claude Code's native permission layer underneath), and the failure announces itself with a visible one-liner instead of vanishing.

## Developing

Development happens in the private home repo (`ballast-dev`); the public `ballast` repo is a per-release distribution snapshot of it (see [CHANGELOG.md](CHANGELOG.md)).
