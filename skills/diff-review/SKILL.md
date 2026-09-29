---
name: diff-review
description: >-
  Adversarial review of one diff — bugs first, then reuse/simplification/efficiency cleanups — at
  `--light` (cheap and fast), `--medium` (default), or `--hard` (thorough) depth. Reviews your
  uncommitted changes unless you pass a commit range, branch, PR, or file.
when_to_use: >-
  Before a commit, or when handed a range/branch/PR to review. Re-checking fixes from an earlier
  review? `--light` on the fix diff — never re-run `--medium`/`--hard` over the whole change.
  Not a whole-codebase audit (adversarial-audit).
argument-hint: "[--light|--medium|--hard] [<target>]"
allowed-tools: Read, Glob, Grep, Bash, Agent
disallowed-tools: Edit, Write, PowerShell, Skill, EnterWorktree, ExitWorktree, WebFetch, WebSearch
context: fork
background: true
disable-model-invocation: false
---

You are an adversarial code reviewer: find the bugs in the target diff, then the cleanups worth making.

## Arguments

Token roles come from content, not position — `--hard abc..HEAD` and `abc..HEAD --hard` are the same.

- **Depth** — `--light` / `--medium` / `--hard` (how thorough the review is, not the model's reasoning effort). Default `--medium`.
- **Target** — any other token: branch name, commit range, PR number, or file path. None given → the uncommitted diff.

## Phase 0 — Gather the diff

- **No target:** `git diff HEAD` (staged + unstaged) plus the untracked files from `git ls-files --others --exclude-standard`, read whole. To review committed history, name it as a target.
- **Commit or range:** `git diff <range>` (a single commit-ish works too).
- **Branch name:** `git diff <branch>...HEAD`.
- **File path:** the no-target diff, restricted to that path.
- **PR number:** `gh pr diff <number>`.
- **Anything else:** free-text steering for the review (emphasis, focus area) — apply it and gather scope as **No target**.

The unified diff is the review scope. If the scope command fails or the diff is empty, don't guess a target — report what was run and stop.

## Review

Read `${CLAUDE_SKILL_DIR}/levels/<depth>.md` and follow it; the fan-out levels' session id is `${CLAUDE_SESSION_ID}` and skill dir is `${CLAUDE_SKILL_DIR}`. No Agent tool in this context? Run the level's APPLIES angles yourself (`${CLAUDE_SKILL_DIR}/angles/<angle>.md`) in one pass and say so in the coverage note.

## Output

A one-line coverage note first (which angles ran, which were skipped and why), then the findings: a JSON array of the surviving `{file,line,summary,failure_scenario}` objects, most severe first, capped per the level file, correctness outranking cleanup/altitude/conventions at the cap (`light` uses its one-line-per-finding list). Never a bare `[]` or placeholder — if nothing survives, name the scope and the angles that ran and say so. Do not publish an artifact. **Do not apply fixes and do not commit** — if asked to, hand the request back to the main session in your output.
