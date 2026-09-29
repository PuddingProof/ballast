# ballast ⚓

A verification-first harness for Claude Code: skills, agents, and hooks that keep an agentic workflow honest — verified claims, reviewed diffs, lean docs, safe installs. One plugin, two install commands.

A ship's ballast adds no speed; it keeps the hull upright. Same job here.

## What's inside

**Skills** (run as `/ballast:<name>`, or Claude picks them up from their descriptions):

- `session-postmortem` — one evidence-cited report on how a session went, from a transcript digest
- `diff-review` — adversarial review of one diff at `--light` / `--medium` / `--hard` depth; reports, never edits
- `adversarial-audit` — multi-angle audit of a codebase, findings checked and fixed in commits (alias `/ballast:audit`)
- `durable-docs` — checks a durable-doc change: the reusable class, in one home, kept lean
- `plan-handoff` — once a plan is approved, the main session orchestrates and dispatches execution to `leaf` agents
- `refactor-fit` — opt-in: propose the refactoring a feature needs, then build to the scope you approve
- `freehand` — turns delegated-autonomy mode on or off (`/ballast:freehand on|off`) and holds its contract
- `visual-probe` — visual checks for frontend work: runs the dev server, captures and drives a real browser
- `harness-sweep` — sweeps postmortem reports across your projects into one triaged digest (for the harness repo)

**Agents:**

- `leaf` (+ `leaf-light` / `leaf-hard`) — role-neutral leaves with pinned model and effort; the brief sets the job
- `visual-glance` — cheap, fast visual check; the default done gate for UI work
- `visual-reviewer` — deeper visual review, used only when a change calls for it
- `diff-finder` / `diff-verifier` — read-only leaves that diff-review dispatches to find and confirm issues

**Hooks** (active once the plugin is enabled):

- `ballast-principles` — injects the standing principles at session start
- `shell-guards` — one Bash/PowerShell guard: asks before installs, blocks risky leaf process control, opt-in allows
- `askuserquestion-recommend` — questions to you must lead with a recommended option
- `diff-review-cap` — caps how many leaves one diff review can dispatch
- `freehand-mode` — spots autonomy keywords and routes a real grant to the `freehand` skill
- `plan-handoff` — on plan approval, points at the `plan-handoff` skill and raises a statusline chip
- `visual-arm` — the first frontend edit in a session loads `visual-probe`
- `inline-churn-nudge` — one soft nudge when the main session makes a long run of edits without delegating
- `dev-process-nudge` — at session start, flags leftover dev processes from an earlier session
- `origin-sweep` — at session start, stops dev servers that crashed sessions left running
- `mode-state-cleanup` — at session end, clears that session's statusline chip state

## Install

```bash
claude plugin marketplace add PuddingProof/ballast
claude plugin install ballast@ballast
```

Inside a session: `/plugin marketplace add PuddingProof/ballast`, then `/plugin install ballast@ballast`. From claude.ai: Settings → Extensions → Add marketplace → `PuddingProof/ballast`.

Claude Code shows a trust warning on install: plugins run real hooks and scripts with your user's privileges. That is how this plugin works, so read the inventory it shows you before you accept.

## Prerequisites

- **git** — Claude Code needs it anyway; on Windows, Git for Windows supplies the bash that hooks run under.
- **Python 3** — as `python3` or `python` on PATH; ballast finds a working one and skips the Windows Store stub.
- **Node.js + npm** — only for `visual-probe`. On first use, run `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci` in the skill folder (Claude offers; expect an install prompt).
- **Edge or Chrome** installed — `visual-probe` drives your system browser; nothing extra is downloaded.

On macOS and Linux everything runs through `bash` and Python; `visual-probe` looks for Edge, then Chrome or Chromium. The non-Windows paths are code-reviewed but not yet tested live — please report issues with the exact command.

## Statusline (optional, once per machine)

Shows `<project> · <model> (<effort>) · NN% context` under the prompt box, plus a chip while a mode is active (`✈️ freehand`, `📋 plan-handoff`). Plugins can't set `statusLine` themselves, so turn it on once:

```bash
ballast-statusline install     # safe to re-run; backs up settings.json once
ballast-statusline uninstall   # removes only ballast's statusLine
```

It won't replace a statusLine you set yourself unless you pass `--force`.

## Your settings

ballast keeps its state in `~/.claude/ballast/`, never in the plugin folder (updates replace that folder).

- `allow-standing-grants` — create this empty file to let `shell-guards` auto-approve the plugin's own `ballast-extract`, `ballast-sweep`, and `ballast-mode` commands. The match is strict, but it is still a standing approval: leave it off if you don't want those.
- `modes/` — per-session statusline chip state, managed for you.

## Updating

```bash
claude plugin update ballast
```

If that finds nothing (or a GUI's Update button is greyed out), the marketplace catalog is stale. Refresh it first:

```bash
claude plugin marketplace update ballast
claude plugin update ballast
```

Each release is one tagged snapshot (`vX.Y.Z`); see [CHANGELOG.md](CHANGELOG.md).

## Philosophy

The full text arrives with the `ballast-principles` hook each session. In short:

1. **Claims need verification.** Done means verified; say plainly what isn't.
2. **Security is a default lens**, not a separate pass, and installs are never silent.
3. **Prefer built-in, lightweight, or bespoke**; a heavy dependency names its tradeoff.
4. **Judgement stays with the orchestrator; execution goes to leaves.** The orchestrator verifies and reviews.
5. **Durable docs earn their place:** the reusable class, in one home, kept lean.

Two design rules follow: **authority is opt-in** (anything that grants standing permission ships off), and **guards fail open, loudly** (a hook's own bug can't brick a session, and it says so when it fails).
