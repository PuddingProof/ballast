"""
shell-guards module (Bash|PowerShell PreToolUse): in a SUB-AGENT, hard-deny any command that
takes ownership of a PROCESS LIFECYCLE. Main session: pure no-op (no decision). Run by
shell-guards.py, which merges its decision with the other guards'.

WHY IT EXISTS. A leaf that stands up its own service is the orphan factory: it detaches a
process nothing will reap (a leaf has no close-out), or foregrounds one, wedges its own command
until the tool timeout, then reaches for `taskkill`/`kill` on a process nobody owns. Only the
orchestrator -- attended, with a ledger and a teardown -- can own a process's whole life.

DENY SURFACE (semantic center: THE PROCESS OUTLIVES THE LEAF OR REACHES OUTSIDE IT). The named
verbs are a starting corpus, not the definition -- extend them as the improvisation set moves;
the test suite's bypass matrix is where new shapes get pinned.
  (a) DETACHMENT -- `tool_input.run_in_background: true` is a FIRST-CLASS signal, read before
      the command string (detachment is frequently nowhere in the command text), plus `nohup`,
      a trailing `&`, `Start-Job`, `Start-Process`, Windows `start`.
  (b) STANDING-ORIGIN LAUNCHES, FOREGROUND INCLUDED -- dev servers, `python -m http.server`,
      `docker compose up`, `make dev`, probe serve/session lifecycle. Foreground counts: in a
      leaf it only wedges the command until timeout, origin still unobtained.
  (c) ALL PROCESS SIGNALLING, WITH NO OWN-CHILD CARVE-OUT -- `kill`, `pkill`, `killall`,
      `taskkill`, `Stop-Process`, `kill-port`, `docker stop`. A stateless guard cannot verify
      ownership, and a foregrounded child dies with the leaf's own command anyway. The
      carve-out is absent BY DESIGN -- do not add one.

EXPLICITLY NOT THE DENY SURFACE: transient port binds inside foreground test/build runs
(`vitest`, `playwright test`, an `npm test` that spawns and reaps an internal server,
`npm run build`), and search/read commands that merely MENTION a verb. Two load-bearing
mechanisms keep those quiet: (1) a segment whose head is a read-only tool contributes nothing
(`grep -r "Stop-Process" src/`, `cat kill.sh`), and (2) bare `kill` matches in COMMAND POSITION
only -- an ordinary English word, an anywhere-match would fire on filenames, flags, and prose.

DOMAIN-NEUTRAL BY DESIGN: the rationale is lifecycle/orphan hygiene, so no leaf pays
domain-flavored friction in a project that has none of that domain. The deny text says *the
orchestrator owns process lifecycle -- report the missing origin/service and finish with what
exists*, and names nothing else.

POSTURE: hard deny (permissionDecision "deny" + exit 0 -- NOT exit 2), sub-agents only, no
shadow window. The compensating control for skipping that window is the false-positive corpus in
`test_process_lifecycle_guard.py` -- not optional, and first-class in rows from projects outside
any one domain.

NO RELEASE VALVE: a genuine false positive costs one leaf round-trip -- the leaf reports it, and
the attended main session runs the command itself. A new false-positive shape gets pinned in the
test suite's corpus.

FAIL-OPEN GUARANTEE. decide() does no I/O; a regex or parse surprise raises into shell-guards.py,
which announces it and skips this guard, so a bug here can never block every Bash/PowerShell call.

CALLER DISCRIMINATION is payload-only (no env-var discriminator exists on the hook path):
`agent_type` / `agent_id`, OR'd -- see hook_payload.is_subagent(). Any parse surprise resolves
False = main session = no-op, failing toward the native permission flow, never toward denying an
attended user. The prefilter in run.sh passes any payload carrying either field name, so leaf
calls always reach this guard.

NAMED RESIDUAL RISKS (accepted, not bugs -- do not "fix" them silently):
  1. `npm start` / `npm run start` are NOT denied: `start` is overloaded across ecosystems (dev
     server, plain entry point, build/test alias) and sits in the false-positive corpus. A leaf
     whose project maps `start` to a dev server slips through; the compensating control is
     orchestrator-owned origins -- the service is handed to the leaf, which then has no reason
     to run `start` at all.
  2. Quoted bodies are stripped before matching, so a verb hidden in a quoted string is
     invisible. `bash -c "npm run dev"` is unwrapped first, but not every wrapper shape is (a
     script the leaf writes then executes, `xargs`, env-var indirection). A lifecycle backstop,
     not a sandbox.
  3. Code reached through a tool's own in-process entry points (a dynamically imported scenario
     or project-authored hook module) never becomes a shell command and is invisible here. Only
     HALF is closed elsewhere: manifest `drive` hooks are skipped under the leaf-mode flag and
     reported as coverage holes, while running an authored scenario file is a live residual by
     design (rung 2 is opt-in and orchestrator-adjudicated -- see the probe skill's mode-states
     reference). Read this guard as neither total coverage nor "closed elsewhere".
  4. Verb corpora are heuristics: a launcher nobody has improvised yet is missed until the
     bypass matrix names it, and an exotic project alias could false-fire (the leaf reports it).
"""

import os
import re

from hook_payload import is_subagent
from shell_text import neutralize

NAME = "process-lifecycle-guard"


# --------------------------------------------------------------------------------------
# Command classification
# --------------------------------------------------------------------------------------
# Segment heads that read/search rather than execute a lifecycle action. A segment whose head
# is one of these contributes NOTHING, which is what keeps the false-positive corpus green:
# `grep -r "Stop-Process" src/`, `cat kill.sh`, `git log --grep=taskkill`. Compound commands
# are split first, so `grep foo && npm run dev` still denies on the second segment.
# Deliberately EXCLUDED: `find` / `fd` -- `find … -exec kill` executes, so they are not read-only.
READ_ONLY_HEADS = frozenset({
    "grep", "rg", "ripgrep", "ag", "ack", "findstr", "select-string", "sls",
    "cat", "bat", "type", "head", "tail", "less", "more", "tac", "nl", "strings",
    "awk", "sed", "cut", "sort", "uniq", "wc", "diff", "jq", "yq",
    "echo", "printf", "write-host", "write-output",
    "ls", "dir", "get-childitem", "gci", "get-content", "gc", "tree", "file", "stat",
    "git", "gh",
})

# Leading words that wrap another command without changing what it is; skipped when finding
# the segment head. `nohup` and `start` are NOT here -- they are detachment signals.
TRANSPARENT_HEADS = frozenset({"sudo", "env", "command", "exec", "time", "nice", "doas"})

# Heads that signal a process. Bare `kill` lives HERE and only here (command position), not in
# SIGNAL_ANY: it is an ordinary word, and an anywhere-match on it fires on `kill.sh`,
# `--kill-others`, and any prose that contains it.
SIGNAL_HEADS = frozenset({
    "kill", "pkill", "killall", "taskkill", "stop-process", "stop-job", "stop-service",
    "fkill", "kill-port",
})

# Distinctive enough to match anywhere in a segment (a bare mention of these is rare, and the
# read-only-head rule already covers the search case).
SIGNAL_ANY = re.compile(
    r"\btaskkill\b"
    r"|\bpkill\b"
    r"|\bkillall\b"
    r"|\bStop-(?:Process|Job|Service)\b"
    r"|\bkill-port\b"
    r"|\bfkill\b"
    r"|\bdocker\s+(?:stop|kill)\b"
    r"|\bpm2\s+(?:stop|delete|kill)\b"
    # Bare `kill` reached through a dispatcher instead of command position -- the two shapes a
    # leaf actually improvises after a pipeline finds the PID (`… | xargs kill -9`,
    # `find … -exec kill`). Anchored to the dispatcher so this stays narrow: an unanchored
    # `kill` is the ordinary-word problem SIGNAL_HEADS exists to avoid.
    r"|\bxargs\b(?:\s+-\S+)*\s+kill\b"
    r"|-exec\s+kill\b",
    re.IGNORECASE,
)

# Detachment shapes that can appear anywhere in a segment (PowerShell cmdlets are commonly
# assigned: `$p = Start-Process ...`). `nohup` / `start` are matched as HEADS instead, so that
# `npm start` -- an explicit false-positive-corpus row -- cannot be read as Windows `start`.
DETACH_ANY = re.compile(r"\bStart-(?:Process|Job|ThreadJob)\b", re.IGNORECASE)
DETACH_HEADS = frozenset({"nohup", "start"})

# A backgrounding `&`: a lone ampersand that is not `&&`, not `2>&1`/`>&2`, not `&>`, and that
# sits at a command boundary (whitespace, `;`, newline, end of string). Evaluated on the whole
# neutralized command BEFORE segment splitting, because the split consumes it.
# A backgrounding `&` TERMINATES a command, so it must be preceded by command text ([^\s;|&(>]
# then optional space): a `&` at string start or straight after a separator is PowerShell's
# call operator (`& "C:\Program Files\App\app.exe" args` -- the documented way to invoke a
# quoted path), which runs FOREGROUND and must not read as detachment.
BACKGROUND_AMP = re.compile(r"[^\s;|&(>]\s*&(?![&>])(?=\s|;|\n|$)")

# Standing-service launches. Applied to a segment with its head resolved, so `^` means command
# position. Foreground counts -- see the header.
LAUNCH = re.compile(
    # JS runner scripts. `dev|serve|preview` only: `start` is a corpus false positive (residual
    # 1) and `watch` collides with the `--watch` flag on ordinary test runs (`npm test --
    # --watch`). The (?<![-\w=]) lookbehind keeps `--serve`-style flags AND `=`-valued flags
    # (`npm audit --omit=dev`) out; the trailing (?![\w-]) keeps `devtools` / `preview-build`
    # from matching.
    r"\b(?:npm|pnpm|yarn|bun)\b[^;&|]{0,80}?(?<![-\w=])(?:run\s+)?(?:dev|serve|preview)(?::[\w.-]+)?(?![\w-])"
    # Bundler / framework dev servers. `vite` excludes `vitest` (no word boundary after "vite"
    # in "vitest") and excludes `vite build`.
    r"|(?<![-\w])vite(?![\w-])(?!\s+build)"
    r"|\bnext\s+dev\b"
    r"|\bnuxt\s+(?:dev|start)\b"
    r"|\bastro\s+dev\b"
    r"|\bng\s+serve\b"
    r"|\bremix\s+dev\b"
    r"|\bgatsby\s+develop\b"
    r"|\bwebpack-dev-server\b|\bwebpack\s+serve\b"
    r"|\bhttp-server\b|\blive-server\b|\bbrowser-sync\b|\bjson-server\b"
    # The `serve` npm package: command position or straight off a runner, never a bare word.
    r"|(?:^|\b(?:npx|bunx|pnpm\s+dlx|yarn\s+dlx)\s+)serve\b"
    # Improvised static/app servers across ecosystems -- domain neutrality means a backend
    # leaf's standing server is denied on the same footing as a frontend one.
    r"|\b(?:python[\d.]*|py)\b[^;&|]{0,40}?-m\s+(?:http\.server|SimpleHTTPServer|uvicorn|flask)\b"
    r"|\buvicorn\b|\bgunicorn\b|\bdaphne\b|\bhypercorn\b|\bwaitress-serve\b"
    r"|\bflask\s+run\b"
    r"|\brails\s+(?:server|s)\b"
    r"|\bphp\s+-S\b|\bphp\s+artisan\s+serve\b"
    r"|\bhugo\s+server\b|\bjekyll\s+serve\b|\bmkdocs\s+serve\b"
    r"|\bdotnet\s+watch\b"
    r"|\bcargo\s+watch\b|\b(?:cargo\s+)?tauri\s+dev\b"
    # `node <something>server<something>.js` -- the hand-rolled server a denied leaf writes next.
    r"|\bnode\s+[\w./\\-]*server[\w.-]*\.[cm]?js\b"
    r"|\bdocker(?:\s+compose|-compose)\s+up\b"
    r"|\bdocker\s+run\b[^;&|]{0,80}?\s-{1,2}(?:d|detach)\b"
    r"|\bmake\s+(?:dev|serve)\b"
    # A capture harness's own lifecycle verbs: a leaf may drive an origin it was handed, never
    # start or stop one.
    r"|\bprobe\.mjs\b[^;&|]{0,80}?\b(?:serve|session)\b"
    r"|\bballast-visual\b[^;&|]{0,80}?\b(?:serve|session)\b",
    re.IGNORECASE,
)

# Literal stripping lives in shell_text.neutralize: it unwraps `bash -c "npm run dev"` before
# quote-stripping so the wrapper cannot launder the verb away (residual 2 covers what it misses).


def head_and_rest(segment):
    """Return (normalized head token, segment text from the head onward).

    Leading env assignments (`FOO=1 npm run dev`) and transparent wrappers (`sudo`, `env`) are
    skipped -- the same env-prefix hole the install guard scans the whole string for. The head
    is basenamed, lowercased and `.exe`-stripped, so an absolute
    `C:\\Windows\\System32\\taskkill.exe` and a bare `taskkill` are one token.

    Shell grouping punctuation is stripped off the token, because the segment splitter does not
    treat it as a separator: `(kill -9 $PID)` would otherwise present the head `(kill`, which
    matches nothing -- and bare `kill`/`nohup`/`start` are deliberately absent from the
    anywhere-regexes, so there is no fallback match to catch it. Stripping is done AFTER the
    env-assignment test on the same token, so an array literal (`files=(*.png)`) still reads as
    an assignment and is skipped rather than presenting `*.png` as a head.
    """
    toks = segment.strip().split()
    i = 0
    while i < len(toks):
        t = toks[i].strip("(){}")
        if not t or re.match(r"^[A-Za-z_]\w*=", t) or t.lower() in TRANSPARENT_HEADS:
            i += 1
            continue
        break
    if i >= len(toks):
        return "", ""
    head = os.path.basename(toks[i].strip("(){}").replace("\\", "/")).lower()
    if head.endswith(".exe"):
        head = head[:-len(".exe")]
    return head, " ".join(toks[i:])


def classify(cmd, background):
    """Return (kind, matched-token) for the first lifecycle action found, else None.

    `background` (tool_input.run_in_background) is checked FIRST and independently of the
    command text: detachment is a TOOL-level property that frequently appears nowhere in the
    command string, and reading it any later would let `run_in_background: true` on an
    innocent-looking command through.
    """
    if background:
        return ("Detached (background) execution", "run_in_background: true")

    s = neutralize(cmd)
    if not s.strip():
        return None

    if BACKGROUND_AMP.search(s):
        return ("Detached (background) execution", "&")

    # Split on every shell/pwsh separator, including `&` (already harvested above). Per-segment
    # evaluation is what lets a read-only head neutralize its OWN segment without covering for
    # a lifecycle verb chained after it.
    for seg in re.split(r"&&|\|\||;|\||\n|&", s):
        head, rest = head_and_rest(seg)
        if not head or head in READ_ONLY_HEADS:
            continue
        m = DETACH_ANY.search(rest)
        if m:
            return ("Detached (background) execution", m.group(0))
        if head in DETACH_HEADS:
            return ("Detached (background) execution", head)
        m = LAUNCH.search(rest)
        if m:
            return ("Standing-service launch", m.group(0))
        if head in SIGNAL_HEADS:
            return ("Process signalling", head)
        m = SIGNAL_ANY.search(rest)
        if m:
            return ("Process signalling", m.group(0))
    return None


# --------------------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------------------
# The leaf-facing half of the deny: what to do INSTEAD. Without a stated escape hatch a blocked
# leaf improvises one (a different runner, a foreground variant, a wrapper) and spins -- the
# exact loop this guard exists to end. The domain-neutral wording is deliberate: every leaf must
# read this as ordinary lifecycle hygiene, not as some other domain's policy. The reason reaches
# the calling model verbatim, so it is written as an instruction to the leaf.
LEAF_ESCAPE = (
    "Sub-agents never start, background, or signal a process — the orchestrator owns lifecycle. "
    "Do not retry or route around this (another runner, a foreground variant, a wrapper, a "
    "background flag): use the origin or service you were handed, or return `blocked` / a named "
    "coverage gap naming what is missing and finish with what exists."
)

BANNER = "⛔ ballast: %s — sub-agent process lifecycle denied (orchestrator-only)" % NAME


def decide(payload):
    """Return a deny {"decision", "reason", "banner"} for a leaf lifecycle action, else None."""
    if not is_subagent(payload):
        return None  # main session: pure no-op

    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}
    cmd = ti.get("command") or ""
    bg = ti.get("run_in_background")
    # Tolerate a string-valued flag ("true"): the two matched tools' payload shapes are not
    # guaranteed identical, and a stringly-typed true must not read as false.
    background = bg is True or (isinstance(bg, str) and bg.strip().lower() == "true")

    hit = classify(str(cmd), background)
    if not hit:
        return None
    kind, token = hit
    token = token.strip()[:60]
    return {
        "decision": "deny",
        "reason": "%s blocked ('%s'): this is a sub-agent. %s" % (kind, token, LEAF_ESCAPE),
        "banner": BANNER,
    }
