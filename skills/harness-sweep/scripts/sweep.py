#!/usr/bin/env python
"""harness-sweep corpus engine (v2).

Everything deterministic in the cross-project postmortem sweep — enumeration, watermark
arithmetic, per-report parsing, chain stitching, ledger inlining, watermark advance — runs
here so the model spends its context on adjudication instead of transcription. A script
structurally cannot mis-transcribe a basename; a model demonstrably can (the v1 scout
returned five month-shifted dates and silently pulled stale reports into a sweep).

Usage:
    python sweep.py digest [--focus VAL] [--variant V ...] [--deep RANGE] [--out DIR]
    python sweep.py advance [--manifest PATH]
    python sweep.py seed --scan [--project-root DIR]
    python sweep.py audit

Subcommands:
    digest    Enumerate + parse the corpus, write <out>/bundle.md + <out>/appendix.md +
              <out>/manifest.json, print those paths and a one-line counts summary. <out>
              defaults to <home>/.cache/ballast/sweep/<UTC-timestamp>/. appendix.md is the
              script-authored digest tail (chain graph, gist table, parse health) the skill
              concatenates onto its digest verbatim.
    advance   Rewrite SWEEP-STATE from a digest manifest: each swept project's `last=` moves
              to its lexically greatest enumerated basename, `swept:` to now. DEFAULT-MODE
              MANIFESTS ONLY (a --focus/--deep run must never move a watermark), registered
              projects only, and every basename is validated before it reaches the file.
    seed      `--scan`: print candidate postmortem dirs. Scan-only — writing SWEEP-STATE is
              the in-session agent's job, after the user confirms the registry.
    audit     Dev/bench: parse the ENTIRE registered corpus (no watermark), print
              per-generation parse rates and every UNPARSED/anomaly line. Read-only.

Exit codes:
    0 ok · 2 usage error / refused advance · 3 undated --deep (count table printed, the
    caller must re-invoke with an explicit range) · 4 SWEEP-STATE missing (seed first).

CONTRACTS THIS FILE IS BOUND BY:
  - STATE HOME is $BALLAST_CLAUDE_HOME, else ~/.claude. Unlike session-postmortem's
    extract.py (which resolves ~ directly), the whole harness-sweep ecosystem — the
    harness-sweep-nudge hook included — honors this env var, and the test suite depends on
    it to keep real watermarks/ledgers untouched.
  - SWEEP-STATE bytes: `<name> · <dir> · last=<basename|NONE>`, separator the literal 3
    bytes space + U+00B7 + space. .claude/hooks/harness-sweep-nudge.sh parses it with bash
    parameter expansion on that literal, so `advance` preserves it exactly, emits LF
    endings with a final newline, and copies every non-registry line through byte-for-byte.
  - INCLUSIVE watermark rule: a report is new when its 10-char date prefix is >= the
    watermark basename's date prefix. Deliberately wider than the nudge hook's strict `>`
    (see that hook's residual risk #1) — re-reading a same-day report is idempotent, and
    the wider rule is the backstop for what the cheap hook check under-counts.
  - PARSER: evidence-preserving, never silently dropping. A report the generation's shape
    doesn't explain gets an `UNPARSED:` note plus its raw recommendations section embedded
    (trimmed), so the model can read the primary source instead of trusting this parser.
  - INPUT VALIDATION: everything this tool reads — reports, ledgers, SWEEP-STATE, and its own
    manifests — is a file on disk that a concurrent session, a hand-edit, or a stale run may
    have shaped. The only path that WRITES outside its own output dir is `advance`, so that is
    where validation is strictest: registered projects only, report-basename-shaped watermarks
    only, and a whole-run refusal rather than a partial write on any violation.
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    # Windows consoles default to cp1252; every glyph in a verdict line would crash the run.
    sys.stdout.reconfigure(encoding='utf-8')

SEP = ' \u00b7 '                       # the registry separator, byte-for-byte
REPORT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}-.*\.md$')
SID8_RE = re.compile(r'^[0-9a-f]{8}$')
VERDICT_GLYPHS = '\U0001F7E2\U0001F7E1\U0001F534'   # green / yellow / red circles

# A citation handle is `<sid8>#R<n>`; older reports cite the whole basename
# (`2026-07-04-slug-af21d945.md#R7`), so the optional `.md` is part of the grammar. The
# lookbehind stops a longer hex run from yielding a bogus 8-char tail.
HANDLE_RE = re.compile(r'(?<![0-9a-fA-F])([0-9a-f]{8})(?:\.md)?#R(\d+)')

BALLAST_HANDOFF_PHRASES = ('route to', 'owning plugin repo', 'ballast')

# ---------------------------------------------------------------------------
# Disposition vocabulary \u2014 SINGLE SOURCE OF TRUTH. Both the verbatim status capture
# (STATUS_PATTERNS, below) and the chain-graph flag rollup (build_chains) read from this tuple,
# so registering a new disposition token is a one-line change here. Order is display order.
# ---------------------------------------------------------------------------
DISPOSITION_TOKENS = ('ESCALATED', 'SUPERSEDED', 'CLOSED', 'DROPPED', 'PARKED', 'dormant')

# Tokens that appear behind the \u2197 "moved elsewhere" glyph in v2-v4 recs.
_ARROW_TOKENS = '|'.join(t for t in DISPOSITION_TOKENS if t.isupper() and t != 'ESCALATED')

# Status / carry notations captured VERBATIM into a rec's `status:` line. Over-capture is
# correct here: the model filters, the script never judges relevance.
STATUS_PATTERNS = [
    re.compile(r'\U0001F534\s*\*{0,2}ESCALATED\*{0,2}[^\n]*'),
    re.compile(r'\u2197\s*\*{0,2}(?:%s)\*{0,2}[^\n]*' % _ARROW_TOKENS),
    # PARKED and dormant also occur bare (ledger-style dispositions quoted into a rec), so they
    # get glyph-free patterns of their own rather than riding the \u2197 form.
    re.compile(r'\u2014\s*dormant[^\n]*'),
    re.compile(r'\*{0,2}PARKED\*{0,2}[^\n]*'),
    re.compile(r'\u21b3\s*carried from[^\n]*'),
]


def disposition_flags(text):
    """Every disposition token present in `text`, in vocabulary order. Used for both rec status
    strings and matched ledger rows so the two can't drift apart."""
    return [t for t in DISPOSITION_TOKENS if t in (text or '')]


MAX_RAW_SECTION_LINES = 40    # cap on an UNPARSED report's embedded raw section
MAX_REC_CHARS = 600
MAX_GIST_CHARS = 400

# Above this many otherwise-disjoint chains, one report's citations are treated as an INDEX of
# the corpus rather than as evidence of a single carried chain, and its edges are withheld from
# the union (see build_chains). Without the guard a single sweep-style report that cites every
# prior chain collapses them all into one meaningless mega-chain \u2014 observed live at n=22.
CITATION_HUB_THRESHOLD = 6


# ---------------------------------------------------------------------------
# State home + registry
# ---------------------------------------------------------------------------

def home_root():
    # Same name, same idiom as hooks/run.sh: BALLAST_CLAUDE_HOME is the
    # hermetic-test override (production never sets it), else the real ~/.claude. Kept in sync
    # deliberately rather than shared, since each of these ships standalone.
    home = os.environ.get('BALLAST_CLAUDE_HOME')
    if home:
        return home
    return os.path.join(os.path.expanduser('~'), '.claude')


def state_paths(home=None):
    home = home or home_root()
    pm = os.path.join(home, 'postmortem')
    return {
        'home': home,
        'postmortem': pm,
        'state': os.path.join(pm, 'SWEEP-STATE.md'),
        'recs': os.path.join(pm, 'HARNESS-RECS.md'),
        'drift': os.path.join(pm, 'DRIFT-SIGHTINGS.md'),
        'cache': os.path.join(home, '.cache', 'ballast', 'sweep'),
    }


def read_text(path):
    """Every corpus read goes through here: errors='replace' so a byte-damaged report
    degrades to a mojibake character instead of an exception, and a raw NUL (one live
    report has one) survives to be reported as an anomaly rather than vanishing."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def parse_state(text):
    """Return (lines, entries). `lines` are the file's lines with any trailing CR stripped
    but otherwise byte-identical — `advance` writes them back untouched. `entries` are the
    registry lines: dicts with idx/name/dir/watermark."""
    lines = [ln[:-1] if ln.endswith('\r') else ln for ln in text.split('\n')]
    if lines and lines[-1] == '':
        lines.pop()                     # the file's final newline, re-added on write
    entries = []
    for idx, line in enumerate(lines):
        # The separator alone is NOT a sufficient discriminator: the file's own prose header
        # QUOTES the registry format, so it contains " · " too — more than once. (The nudge hook
        # survives that only because its `[ -d "$pdir" ]` check drops the header silently; we
        # would otherwise report it as a STALE-DIR.) A registry row is therefore pinned to the
        # documented shape: exactly three separator-delimited fields, a `last=` final field, and
        # not a markdown quote/heading line.
        fields = line.split(SEP)
        if len(fields) != 3 or not fields[2].strip().startswith('last='):
            continue
        if line.lstrip().startswith(('>', '#')):
            continue
        entries.append({'idx': idx, 'name': fields[0].strip(), 'dir': fields[1].strip(),
                        'watermark': fields[2].strip()[len('last='):]})
    return lines, entries


def list_reports(directory):
    """Sorted date-prefixed report basenames, or None when the dir is gone (STALE-DIR)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    return sorted(n for n in names if REPORT_RE.match(n))


def build_listings(entries):
    """dir -> listing, built ONCE per run and threaded through every consumer (stale-dir
    detection, mode selection, sid8 index, topic grep, deep counts). Enumerating a registered
    dir five times per digest was pure duplicated I/O, and two enumerations of the same dir at
    different moments could disagree if a concurrent session lands a report mid-run."""
    listings = {}
    for e in entries:
        if e['dir'] not in listings:
            listings[e['dir']] = list_reports(e['dir'])
    return listings


def unseen(names, watermark):
    """Inclusive date rule — see the module header's watermark contract."""
    if not watermark or watermark == 'NONE':
        return list(names)
    cut = watermark[:10]
    return [n for n in names if n[:10] >= cut]


def sid8_of(basename):
    stem = basename[:-3] if basename.endswith('.md') else basename
    tail = stem[-8:].lower()
    return tail if SID8_RE.match(tail) else '-' * 8


# ---------------------------------------------------------------------------
# Report parser — generation-aware, evidence-preserving
# ---------------------------------------------------------------------------

SCHEMA_RE = re.compile(r'^\*\*Schema version:\*\*\s*v(\d+)', re.M)
V5_TITLE_RE = re.compile(r'^# Post-mortem \u2014 ', re.M)
V5_RUN_RE = re.compile(r'^\*\*Postmortem run:\*\*.*schema v5', re.M)

# v5 section names, lowercased prefixes (headings carry suffixes: "## Verdict — 🟢").
V5_SECTIONS = ('verdict', 'narrative', 'steering', 'friction', 'inventory', 'signals')

CHECKBOX_RE = re.compile(r'^(\s*)[-*]\s+\[([ xX])\]\s*(.*)$')
# Leading decoration before a rec's label: status glyphs, strikethrough, stray backticks.
# Captured verbatim (it IS status text), then stripped so the ID regexes see the label.
DECOR_RE = re.compile(r'^([\s`~\U0001F534\u2197\u2705\U0001F7E2\U0001F7E1\u26a0\ufe0f]*)(.*)$', re.S)
BOLD_ID_RE = re.compile(r'^\*\*\s*`?R(\d+)`?\s*(.*?)\*\*[\s:\u2014-]*(.*)$', re.S)
BARE_ID_RE = re.compile(r'^`?R(\d+)`?\s*[\u2014:-]?\s*(.*)$', re.S)
BOLD_NOID_RE = re.compile(r'^\*\*(.*?)\*\*[\s:\u2014-]*(.*)$', re.S)
# Some v2-v4 recs open with a whole status CLAUSE before the label — `- [ ] ↗ SUPERSEDED by
# `x#R4` — **`R3` Refine custom skill** …`. The bold ID is still there, just not at position 0,
# so a second pass looks for it anywhere; everything left of it becomes status text.
INNER_ID_RE = re.compile(r'\*\*\s*`?R(\d+)`?\s*(.*?)\*\*[\s:\u2014-]*')
LIST_ITEM_RE = re.compile(r'^\s{0,3}(?:[-*]\s|\d+\.\s)')
EVIDENCE_RE = re.compile(r'\*\*Evidence\*\*\s*:?\s*(.*)$', re.S)
PATTERN_LINE_RE = re.compile(r'^\s*(?:[-*]\s*)?(?:\*\*|_)?\s*`?P\d+`?\b')


def oneline(text, cap):
    # Deliberately mirrors extract.py's `_cut` rather than importing it: skills ship standalone,
    # and a cross-skill import would couple two independently-versioned engines.
    out = ' '.join(text.split())
    if len(out) > cap:
        out = out[:cap - 1].rstrip() + '\u2026'
    return out


def detect_generation(text):
    m = SCHEMA_RE.search(text)
    if m:
        return 'v' + m.group(1)
    if V5_RUN_RE.search(text) or V5_TITLE_RE.search(text):
        return 'v5'
    return '?'


def _headings(lines):
    """(line-index, normalized-title) for every `## ` heading. HTML entities are unescaped
    first — one live report's §2 heading is literally `## 2. Subagent &amp; Tooling
    Evaluation`, and a raw match would miss it."""
    out = []
    for i, ln in enumerate(lines):
        m = re.match(r'^##\s+(.*)$', html.unescape(ln))
        if m:
            out.append((i, m.group(1).strip()))
    return out


def _bodies(lines, heads):
    """Map heading-index -> body lines (up to the next `## ` heading)."""
    bodies = []
    for n, (i, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        bodies.append((title, lines[i + 1:end]))
    return bodies


def _pick_section(bodies, predicate):
    """Among headings matching `predicate`, take the one with the most non-blank body lines.
    One live report duplicates a section heading back-to-back, leaving the first copy empty;
    picking by content rather than by first-match keeps the real section. Returns
    (title, body, duplicate_count)."""
    hits = [(t, b) for (t, b) in bodies if predicate(t)]
    if not hits:
        return None, None, 0
    best = max(hits, key=lambda tb: sum(1 for ln in tb[1] if ln.strip()))
    return best[0], best[1], len(hits) - 1


def _numbered(idx):
    return lambda title: bool(re.match(r'^%d\s*\.' % idx, title))


def _named(name):
    return lambda title: title.lower().startswith(name)


def _rec_blocks(body):
    """Split a recommendations section body into (checkbox_line, [continuation lines]).
    A continuation is any blank line or any non-blank line indented deeper than the
    checkbox — which is exactly how sub-bullets (`  - **Evidence**: …`) nest, and how a
    following flush-left paragraph (`**Prior-open-recs sweep** …`) ends the block."""
    blocks = []
    i = 0
    while i < len(body):
        m = CHECKBOX_RE.match(body[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        sub = []
        j = i + 1
        while j < len(body):
            ln = body[j]
            if not ln.strip():
                sub.append(ln)
                j += 1
                continue
            if CHECKBOX_RE.match(ln) and len(CHECKBOX_RE.match(ln).group(1)) <= indent:
                break
            if len(ln) - len(ln.lstrip()) <= indent:
                break
            sub.append(ln)
            j += 1
        while sub and not sub[-1].strip():
            sub.pop()
        blocks.append((m, sub))
        i = j
    return blocks


def _parse_recs(body, generation, own_sid8):
    """Return (recs, notes). Positional numbering (R1..Rn over every checkbox line, closed
    ones included) is the fallback whenever a line carries no stable ID — v0/v1 have none
    by construction, and a handful of v2-v4 lines lost theirs to a status-glyph rewrite."""
    recs = []
    notes = []
    positional = generation in ('v0', 'v1', '?')
    for pos, (m, sub) in enumerate(_rec_blocks(body), start=1):
        checked = m.group(2).lower() == 'x'
        head = m.group(3)
        decor_m = DECOR_RE.match(head)
        decor, label = decor_m.group(1), decor_m.group(2)

        rid = category = None
        text = label
        lead_status = ''
        bm = BOLD_ID_RE.match(label)
        bare = BARE_ID_RE.match(label)
        if bm:
            rid, category, text = int(bm.group(1)), bm.group(2).strip(), bm.group(3)
        elif bare and generation == 'v5':
            rid, category, text = int(bare.group(1)), '', bare.group(2)
        else:
            inner = INNER_ID_RE.search(label)
            if inner:
                rid, category = int(inner.group(1)), inner.group(2).strip()
                lead_status = label[:inner.start()].strip()
                text = label[inner.end():]
            else:
                nb = BOLD_NOID_RE.match(label)
                if nb:
                    category, text = nb.group(1).strip(), nb.group(2)

        if rid is None:
            rid = pos
            if not positional:
                notes.append('rec at position %d carries no `R<n>` id (numbered positionally)'
                             % pos)

        block_text = '\n'.join([head] + sub)
        status_bits = []
        if decor.strip():
            status_bits.append(decor.strip())
        if lead_status:
            status_bits.append(oneline(lead_status, 200))
        for pat in STATUS_PATTERNS:
            for hit in pat.findall(block_text):
                status_bits.append(oneline(hit if isinstance(hit, str) else hit[0], 200))
        evidence = ''
        for ln in sub:
            em = EVIDENCE_RE.search(ln)
            if em:
                evidence = oneline(em.group(1), 300)
                break

        handles = ['%s#R%s' % (h, n) for h, n in HANDLE_RE.findall(block_text)
                   if not (h == own_sid8 and int(n) == rid)]
        low = block_text.lower()
        handoff = [p for p in BALLAST_HANDOFF_PHRASES if p in low]

        recs.append({
            'id': rid,
            'positional': positional or rid == pos and bm is None,
            'closed': checked,
            'category': category or '\u2014',
            'text': oneline(text, MAX_REC_CHARS) or oneline(label, MAX_REC_CHARS),
            'status': ' | '.join(dict.fromkeys(status_bits)) or ('done' if checked else 'open'),
            'carried_from': sorted(set(handles)),
            'evidence': evidence or '\u2014',
            'handoff': handoff,
        })
    return recs, notes


def _verdict_v24(lines):
    """v2-v4 verdict = the `**Session:**` line whose text starts with a gauge glyph. One
    live v4 report ALSO uses `**Session:**` as a session-id stat line, so match on the
    glyph rather than on the first occurrence."""
    for ln in lines:
        m = re.match(r'^\*\*Session:\*\*\s*(.*)$', ln)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and rest[0] in VERDICT_GLYPHS:
            gist = rest[1:].lstrip().lstrip('\u2014-').strip()
            return rest[0], gist
    return None, ''


def parse_report(path, project, basename=None, text=None):
    # `text` lets a caller that already read the file (the topic grep) hand the bytes over
    # instead of forcing a second read of every hit.
    basename = basename or os.path.basename(path)
    if text is None:
        text = read_text(path)
    lines = text.split('\n')
    generation = detect_generation(text)
    sid8 = sid8_of(basename)
    anomalies = []
    unparsed = []

    if '\x00' in text:
        anomalies.append('raw NUL byte in source (preserved)')
    if '&amp;' in text or '&lt;' in text:
        anomalies.append('HTML entities in source (headings unescaped before matching)')

    heads = _headings(lines)
    bodies = _bodies(lines, heads)

    verdict, gist = None, ''
    patterns = []
    if generation == 'v5':
        vtitle, vbody, vdup = _pick_section(bodies, _named('verdict'))
        if vtitle:
            gm = re.search(r'[%s]' % VERDICT_GLYPHS, vtitle)
            verdict = gm.group(0) if gm else None
            for ln in vbody:
                if ln.strip().startswith('-'):
                    gist = oneline(ln.strip().lstrip('- '), MAX_GIST_CHARS)
                    break
        rtitle, rbody, rdup = _pick_section(bodies, _named('signals'))
        if rdup:
            anomalies.append('duplicated `## Signals` heading (richest copy used)')
        # A v5 report is SPEC'd to carry all six sections. A missing one is a producer-side
        # deviation worth reporting, but it is not a parse failure — the sections this consumer
        # actually reads are Verdict and Signals, and both are checked separately.
        missing = [s for s in V5_SECTIONS
                   if not any(t.lower().startswith(s) for (t, _b) in bodies)]
        if missing:
            anomalies.append('v5 report missing canonical section(s): %s' % ', '.join(missing))
    else:
        verdict, raw_gist = _verdict_v24(lines)
        gist = oneline(raw_gist, MAX_GIST_CHARS)
        if not gist:
            tm = re.search(r'^#\s+(?:Session Post-Mortem|Post-mortem)\s*\u2014\s*(.*)$',
                           text, re.M)
            gist = oneline(tm.group(1), MAX_GIST_CHARS) if tm else oneline(basename, MAX_GIST_CHARS)
        ptitle, pbody, _ = _pick_section(bodies, _numbered(5))
        if pbody:
            patterns = [oneline(ln, 300) for ln in pbody if PATTERN_LINE_RE.match(ln)]
        rtitle, rbody, rdup = _pick_section(bodies, _numbered(6))
        if rdup:
            anomalies.append('duplicated `## 6.` heading (richest copy used)')

    if rbody is None:
        unparsed.append('no recommendations section found for generation %s' % generation)
        recs, notes = [], []
        raw_section = []
    else:
        recs, notes = _parse_recs(rbody, generation, sid8)
        unparsed.extend(notes)
        raw_section = [ln for ln in rbody]
        # Zero recs is a VALID outcome — "_No actions recommended this session._" is the shape a
        # clean run writes, and flagging it would drown the roster. What is NOT valid is a section
        # carrying top-level list items the checkbox grammar didn't consume: that's the signature
        # of a rec format this parser doesn't know, and it must surface with its raw text.
        if not recs and any(LIST_ITEM_RE.match(ln) for ln in rbody):
            unparsed.append('recommendations section has top-level list items but no parseable '
                            'checkbox recs')

    if generation == '?':
        unparsed.insert(0, 'unknown schema generation (best-effort parse)')

    # PROSE HANDLES: a section the checkbox grammar couldn't read is usually the prior-sweep
    # accounting shape, whose bullets still carry real `sid8#Rn` citations. Losing them would
    # silently under-count the chain graph on exactly the reports that reconcile the most
    # chains. Grouped BY LINE — co-occurrence on one line means one chain, the same rule the
    # ledger rows use — and fed to the graph behind the citation-hub guard.
    prose_handle_lines = []
    if unparsed and raw_section:
        for ln in raw_section:
            found = ['%s#R%s' % (h, n) for h, n in HANDLE_RE.findall(ln)]
            if len(found) >= 2:
                prose_handle_lines.append(sorted(set(found)))
            elif len(found) == 1:
                prose_handle_lines.append(found)

    return {
        'path': path.replace('\\', '/'),
        'basename': basename,
        'project': project,
        'sid8': sid8,
        'date': basename[:10],
        'generation': generation,
        'verdict': verdict,
        'gist': gist,
        'recs': recs,
        'patterns': patterns,
        'anomalies': anomalies,
        'unparsed': unparsed,
        'raw_section': raw_section,
        'prose_handle_lines': prose_handle_lines,
    }


# ---------------------------------------------------------------------------
# Chain graph — union-find over citation handles
# ---------------------------------------------------------------------------

class UnionFind(object):
    def __init__(self):
        self.parent = {}

    def add(self, x):
        self.parent.setdefault(x, x)

    def find(self, x):
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self):
        out = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return out


LEDGER_HANDLE_RE = re.compile(r'`([^`]*)`')


def _ledger_edges(recs_ledger_text):
    """(row_text, [handles]) for every HARNESS-RECS line quoting at least one citation handle.
    Rows have no stable ids, so a matched row is later quoted VERBATIM, never cited by number."""
    rows = []
    for line in (recs_ledger_text or '').split('\n'):
        handles = []
        for quoted in LEDGER_HANDLE_RE.findall(line):
            handles.extend('%s#R%s' % (h, n) for h, n in HANDLE_RE.findall(quoted))
        if handles:
            rows.append((line.rstrip(), handles))
    return rows


def _report_edges(reports):
    """Per-report edge groups: (sid8, [(a, b), …]). Two sources — a rec's own handle unioned
    with each handle it cites, and the by-line prose handles recovered from a section the rec
    grammar couldn't read."""
    groups = []
    for rep in reports:
        edges = []
        for rec in rep['recs']:
            own = '%s#R%d' % (rep['sid8'], rec['id'])
            for other in rec['carried_from']:
                edges.append((own, other))
        for line_handles in rep['prose_handle_lines']:
            for other in line_handles[1:]:
                edges.append((line_handles[0], other))
        groups.append((rep['sid8'], edges))
    return groups


def build_chains(reports, recs_ledger_text, sid8_index):
    """Union-find over citation handles, with a CITATION-HUB guard.

    A report that cites more than CITATION_HUB_THRESHOLD otherwise-disjoint chains is an INDEX
    of the corpus (a sweep-style reconciliation report), not evidence that those chains are one
    chain. Merging through it produced a single meaningless n=22 chain on the live corpus. Such
    a report's edges are withheld from the union and it is reported separately, so the
    information is surfaced rather than silently dropped or silently merged.

    Hub detection is leave-one-out over the full graph, computed once (a second hub's edges are
    still present while the first is being tested). That's a deliberate simplification: it can
    only under-report hubs, never over-report them, and the guard is a coarse bound anyway."""
    meta = {}       # handle -> report provenance + disposition flags
    for rep in reports:
        for rec in rep['recs']:
            own = '%s#R%d' % (rep['sid8'], rec['id'])
            info = meta.setdefault(own, {'basename': rep['basename'], 'project': rep['project'],
                                         'date': rep['date'], 'flags': set()})
            info['flags'].update(disposition_flags(rec['status']))

    ledger_rows = _ledger_edges(recs_ledger_text)
    report_groups = _report_edges(reports)

    nodes = set(meta)
    for _row, handles in ledger_rows:
        nodes.update(handles)
    for _sid, edges in report_groups:
        for a, b in edges:
            nodes.add(a)
            nodes.add(b)

    def build(skip_sids):
        uf = UnionFind()
        for n in nodes:
            uf.add(n)
        for _row, handles in ledger_rows:
            for h in handles[1:]:
                uf.union(handles[0], h)
        for sid, edges in report_groups:
            if sid in skip_sids:
                continue
            for a, b in edges:
                uf.union(a, b)
        return uf

    hubs = []
    hub_sids = set()
    for sid, edges in report_groups:
        if len(edges) <= CITATION_HUB_THRESHOLD:
            continue                      # cannot reach the threshold; skip the expensive rebuild
        uf_without = build({sid})
        # Count only EXTERNAL endpoints: a report's own R-handles are singletons it introduced,
        # so counting them would make every multi-rec report look like a hub.
        roots = {}
        for a, b in edges:
            for h in (a, b):
                if h.startswith(sid + '#'):
                    continue
                roots.setdefault(uf_without.find(h), h)
        if len(roots) > CITATION_HUB_THRESHOLD:
            hub_sids.add(sid)
            hubs.append({'sid8': sid, 'k': len(roots), 'refs': sorted(roots.values())})

    uf = build(hub_sids)

    handle_rows = {}
    for row, handles in ledger_rows:
        for h in handles:
            handle_rows.setdefault(h, set()).add(row)

    def sort_key(h):
        sid = h.split('#')[0]
        info = meta.get(h) or sid8_index.get(sid)
        return (info or {}).get('date', '9999-99-99'), h

    chains = []
    for _root, members in uf.groups().items():
        if len(members) < 2:
            continue
        rows = sorted({r for m in members for r in handle_rows.get(m, ())})
        ordered = sorted(members, key=sort_key)
        rendered = []
        flags = set()
        for h in ordered:
            sid = h.split('#')[0]
            info = meta.get(h)
            if info:
                flags |= info['flags']
                rendered.append('%s (%s %s)' % (h, info['project'], info['date']))
            elif sid in sid8_index:
                known = sid8_index[sid]
                rendered.append('%s (%s %s, not in this corpus slice)'
                                % (h, known['project'], known['date']))
            else:
                rendered.append('%s (out-of-corpus)' % h)
        for row in rows:
            flags.update(disposition_flags(row))
        chains.append({'members': rendered, 'n': len(members),
                       'flags': [t for t in DISPOSITION_TOKENS if t in flags],
                       'ledger_rows': rows, 'key': sort_key(ordered[0])})
    chains.sort(key=lambda c: (-c['n'], c['key']))
    hubs.sort(key=lambda h: (-h['k'], h['sid8']))
    return chains, hubs


# ---------------------------------------------------------------------------
# Bundle rendering
# ---------------------------------------------------------------------------

def render_report_block(rep):
    out = []
    out.append('### %s \u2014 %s (%s) [%s]'
               % (rep['sid8'], rep['basename'], rep['project'], rep['generation']))
    out.append('VERDICT: %s' % (rep['verdict'] or 'NONE'))
    out.append('GIST: %s' % (rep['gist'] or '\u2014'))
    open_recs = [r for r in rep['recs'] if not r['closed']]
    if open_recs:
        out.append('OPEN-RECS:')
        for r in open_recs:
            out.append('- R%d [%s] %s' % (r['id'], r['category'], r['text']))
            out.append('  status: %s' % r['status'])
            out.append('  carried-from: %s'
                       % (', '.join(r['carried_from']) if r['carried_from'] else 'origin'))
            out.append('  evidence: %s' % r['evidence'])
    else:
        out.append('OPEN-RECS: NONE')
    out.append('PATTERNS: %s' % ('NONE' if not rep['patterns'] else ''))
    for p in rep['patterns']:
        out.append('- %s' % p)
    handoffs = ['R%d (%s)' % (r['id'], ', '.join(r['handoff']))
                for r in rep['recs'] if r['handoff'] and not r['closed']]
    out.append('DRIFT: NONE')
    out.append('HANDOFFS: %s' % ('; '.join(handoffs) if handoffs else 'NONE'))
    if rep['anomalies']:
        out.append('ANOMALIES: %s' % '; '.join(rep['anomalies']))
    if rep['unparsed']:
        out.append('UNPARSED: %s' % '; '.join(rep['unparsed']))
        raw = rep['raw_section'][:MAX_RAW_SECTION_LINES]
        if raw:
            out.append('RAW-SECTION (trimmed to %d lines):' % MAX_RAW_SECTION_LINES)
            out.append('```')
            out.extend(raw)
            if len(rep['raw_section']) > MAX_RAW_SECTION_LINES:
                out.append('\u2026 (%d more lines)'
                           % (len(rep['raw_section']) - MAX_RAW_SECTION_LINES))
            out.append('```')
    return out


def render_chain_graph(ctx):
    lines = ['## Chain graph', '']
    if not ctx['chains']:
        lines.append('No multi-member chains in this slice.')
    for chain in ctx['chains']:
        lines.append('- chain n=%d%s' % (chain['n'],
                                         (' [%s]' % ', '.join(chain['flags'])) if chain['flags'] else ''))
        for member in chain['members']:
            lines.append('  - %s' % member)
        for row in chain['ledger_rows']:
            lines.append('  - HARNESS-RECS row (verbatim): %s' % row)
    for hub in ctx['hubs']:
        lines.append('- CITATION-HUB: %s cites %d chains (edges withheld — an index of the '
                     'corpus, not one carried chain)' % (hub['sid8'], hub['k']))
        for ref in hub['refs']:
            lines.append('  - %s' % ref)
    lines.append('')
    return lines


def render_gist_table(ctx):
    lines = ['## Gist table', '', '| Report | Gist |', '|---|---|']
    for rep in ctx['reports']:
        key = '%s `%s`' % (rep['project'], rep['sid8'])
        gist = (rep['gist'] or '—').replace('|', '\\|')
        lines.append('| %s | %s %s |' % (key, rep['verdict'] or '—', gist))
    lines.append('')
    return lines


def render_parse_health(ctx):
    lines = ['## Parse health', '']
    for gen, count in sorted(ctx['generations'].items()):
        ok = ctx['gen_clean'].get(gen, 0)
        rate = (100.0 * ok / count) if count else 0.0
        lines.append('- %s: %d report(s), %d clean (%.0f%%)' % (gen, count, ok, rate))
    if ctx['unparsed_roster']:
        lines.append('- UNPARSED roster:')
        lines.extend('  - %s' % ln for ln in ctx['unparsed_roster'])
    else:
        lines.append('- UNPARSED roster: none')
    if ctx['stale_dirs']:
        lines.extend('- STALE-DIR: %s' % s for s in ctx['stale_dirs'])
    if ctx['anomaly_roster']:
        lines.append('- anomalies:')
        lines.extend('  - %s' % ln for ln in ctx['anomaly_roster'])
    lines.append('')
    return lines


def render_appendix(ctx):
    """The script-authored tail of the digest, in concatenation order. The skill appends this
    file to its digest verbatim (`cat <bundle-dir>/appendix.md >> <digest>`) instead of
    re-extracting sections out of bundle.md by heading — a coupling that breaks the moment a
    heading is renamed. Ledgers and the drift inbox are deliberately EXCLUDED: they are the
    model's reading input, not digest content."""
    lines = []
    lines.extend(render_chain_graph(ctx))
    lines.extend(render_gist_table(ctx))
    lines.extend(render_parse_health(ctx))
    return '\n'.join(lines)


def render_bundle(ctx):
    reports = ctx['reports']
    lines = []
    lines.append('# Sweep bundle')
    lines.append('')
    lines.append('- mode: %s' % ctx['mode'])
    lines.append('- params: %s' % ctx['params'])
    lines.append('- generated: %s' % ctx['generated_utc'])
    lines.append('- reports: %d across %d project(s)' % (len(reports), ctx['project_count']))
    lines.append('- open recs: %d · chains: %d · unparsed: %d · stale dirs: %d'
                 % (ctx['open_recs'], len(ctx['chains']), ctx['unparsed_count'],
                    len(ctx['stale_dirs'])))
    lines.append('- generations: %s'
                 % (', '.join('%s=%d' % kv for kv in sorted(ctx['generations'].items())) or 'none'))
    if ctx.get('hits_marker'):
        lines.append('')
        lines.append('HITS: 0')
        lines.append('')
        lines.append('No report matched the focus terms %s across the registered corpus.'
                     % ctx['params'])
    lines.append('')

    for rep in reports:
        lines.extend(render_report_block(rep))
        lines.append('')

    lines.extend(render_chain_graph(ctx))

    lines.append('## Ledgers')
    lines.append('')
    for label, path in (('HARNESS-RECS.md', ctx['recs_path']), ('DRIFT-SIGHTINGS.md', ctx['drift_path'])):
        lines.append('### %s' % label)
        lines.append('')
        body = ctx['ledgers'].get(label)
        if body is None:
            lines.append('(missing at %s)' % path)
        else:
            lines.append('```')
            lines.extend(body.split('\n'))
            lines.append('```')
        lines.append('')

    lines.append('## Drift inbox')
    lines.append('')
    if ctx['drift_active']:
        lines.extend('- %s' % ln for ln in ctx['drift_active'])
    else:
        lines.append('No unregistered sightings.')
    lines.append('')

    lines.extend(render_gist_table(ctx))
    lines.extend(render_parse_health(ctx))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def parse_deep(value, today=None):
    """`N` -> [today-N days, today]; `YYYY-MM-DD..YYYY-MM-DD` -> inclusive both ends."""
    today = today or datetime.date.today()
    if re.match(r'^\d+$', value):
        start = today - datetime.timedelta(days=int(value))
        return start.isoformat(), today.isoformat()
    m = re.match(r'^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$', value)
    if m:
        return m.group(1), m.group(2)
    return None


def deep_counts(entries, listings, ranges=(7, 14, 30, 60), today=None):
    today = today or datetime.date.today()
    rows = []
    all_names = []
    for e in entries:
        names = listings.get(e['dir'])
        if names:
            all_names.extend(names)
    for n in ranges:
        start = (today - datetime.timedelta(days=n)).isoformat()
        rows.append(('last %d days' % n, sum(1 for b in all_names if b[:10] >= start)))
    rows.append(('all', len(all_names)))
    return rows


def topic_hits(entries, terms, listings):
    """Case-insensitive substring grep across every registered dir. Deliberately dumb: the
    script never decides relevance, it only narrows the corpus (see the no-relevance-filter
    rule — filtering is the model's job over the finished bundle).

    Returns (project, dir, basename, text) — the text it already read comes back with the hit so
    the parser doesn't re-read every matching file."""
    lowered = [t.lower() for t in terms if t]
    out = []
    for e in entries:
        names = listings.get(e['dir'])
        if names is None:
            continue
        for b in names:
            body = read_text(os.path.join(e['dir'], b))
            low = body.lower()
            if any(t in low for t in lowered):
                out.append((e['name'], e['dir'], b, body))
    return out


def build_sid8_index(entries, listings):
    index = {}
    for e in entries:
        for b in listings.get(e['dir']) or []:
            index[sid8_of(b)] = {'project': e['name'], 'date': b[:10], 'basename': b}
    return index


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def load_state_or_die(paths):
    if not os.path.isfile(paths['state']):
        sys.stderr.write(
            'sweep: no SWEEP-STATE at %s — run `sweep.py seed --scan` and write the registry '
            'in-session (with the user confirming the project list) before sweeping.\n'
            % paths['state'])
        return None
    return parse_state(read_text(paths['state']))


def guard_out_dir(out_dir, subject):
    """Bounded-write rule, ported from extract.py's `_guard_out_dir`: create `out_dir` if
    absent; refuse a non-empty dir that holds no manifest.json, or one whose manifest names a
    different sweep subject — never silently overwrite another run's bundle. A parse failure
    counts as a mismatch. Returns an error string, or None when the dir is safe to write.

    Only an explicit `--out` reaches this: the default cache dir is timestamped per run and
    therefore always fresh."""
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        return '--out path exists and is not a directory: %s' % out_dir
    if not os.path.isdir(out_dir):
        return None
    existing = os.listdir(out_dir)
    if not existing:
        return None
    if 'manifest.json' not in existing:
        return ('refusing to write into a non-empty dir with no manifest.json: %s '
                '(pass an empty/new dir, or a dir this tool already wrote)' % out_dir)
    try:
        with open(os.path.join(out_dir, 'manifest.json'), encoding='utf-8') as fh:
            prior = json.load(fh).get('sweep_subject')
    except (OSError, ValueError):
        prior = None
    if prior != subject:
        return ('%s already holds a bundle for a different sweep subject (%r) — pass a fresh '
                '--out' % (out_dir, prior))
    return None


def cmd_digest(args):
    paths = state_paths()
    loaded = load_state_or_die(paths)
    if loaded is None:
        return 4
    _lines, entries = loaded
    listings = build_listings(entries)

    if args.deep is not None and args.deep == '':
        rows = deep_counts(entries, listings)
        print('DEEP-RANGE COUNTS (undated --deep never runs unattended — re-invoke with a range):')
        for label, count in rows:
            print('  %-14s %d report(s)' % (label, count))
        print('Range grammar: --deep <N days> | --deep YYYY-MM-DD..YYYY-MM-DD')
        return 3

    deep_range = None
    if args.deep:
        deep_range = parse_deep(args.deep)
        if deep_range is None:
            sys.stderr.write('sweep: unparseable --deep range %r (want N or '
                             'YYYY-MM-DD..YYYY-MM-DD)\n' % args.deep)
            return 2

    by_name = {e['name']: e for e in entries}
    stale_dirs = [e['name'] + SEP + e['dir'] for e in entries if listings.get(e['dir']) is None]

    # --- focus resolution: path -> project -> topic (same order as the skill's grammar) ---
    mode = 'default'
    params = '(none)'
    selection = []          # (project, dir, basename, pre-read text or None)
    hits_marker = False
    if args.focus:
        if os.path.isfile(args.focus):
            mode = 'focus-path'
            params = args.focus
            target = os.path.abspath(args.focus).replace('\\', '/')
            project = 'ad-hoc'
            for e in entries:
                # Compare against the dir WITH a trailing slash: a bare prefix test lets a
                # sibling whose name merely starts with a registered project's name (…/app vs
                # …/app-legacy) claim the report and mis-attribute it in the digest.
                base = os.path.abspath(e['dir']).replace('\\', '/').rstrip('/') + '/'
                if target.lower().startswith(base.lower()):
                    project = e['name']
                    break
            selection = [(project, os.path.dirname(target), os.path.basename(target), None)]
        elif args.focus in by_name:
            mode = 'focus-project'
            params = args.focus
            e = by_name[args.focus]
            names = listings.get(e['dir'])
            selection = [(e['name'], e['dir'], b, None)
                         for b in unseen(names or [], e['watermark'])]
        else:
            mode = 'focus-topic'
            terms = [args.focus] + list(args.variant or [])
            params = 'terms=%s' % ', '.join(terms)
            selection = topic_hits(entries, terms, listings)
            if not selection:
                hits_marker = True
    else:
        for e in entries:
            names = listings.get(e['dir'])
            if names is None:
                continue
            selection.extend((e['name'], e['dir'], b, None)
                             for b in unseen(names, e['watermark']))

    if deep_range:
        start, end = deep_range
        if mode == 'default':
            mode = 'deep'
            selection = []
            for e in entries:
                names = listings.get(e['dir'])
                if names is None:
                    continue
                selection.extend((e['name'], e['dir'], b, None) for b in names)
        else:
            mode = 'deep+' + mode
        selection = [s for s in selection if start <= s[2][:10] <= end]
        params = ('%s; deep=%s..%s' % (params, start, end)) if params != '(none)' \
            else 'deep=%s..%s' % (start, end)

    selection.sort(key=lambda s: (s[2], s[0]))

    reports = [parse_report(os.path.join(d, b), p, b, text) for (p, d, b, text) in selection]

    ledgers = {}
    for label, key in (('HARNESS-RECS.md', 'recs'), ('DRIFT-SIGHTINGS.md', 'drift')):
        ledgers[label] = read_text(paths[key]) if os.path.isfile(paths[key]) else None
    drift_active = []
    if ledgers['DRIFT-SIGHTINGS.md']:
        for ln in ledgers['DRIFT-SIGHTINGS.md'].split('\n'):
            # Same contract as the nudge hook: a date-led line (plain ` · ` or `(first MM-DD)` form)
            # is live until tagged `[registered]` — a bare substring would match "unregistered".
            if re.match(r'^\d{4}-\d{2}-\d{2} ', ln) and '[registered]' not in ln:
                drift_active.append(ln.strip())

    sid8_index = build_sid8_index(entries, listings)
    chains, hubs = build_chains(reports, ledgers['HARNESS-RECS.md'], sid8_index)

    generations, gen_clean = {}, {}
    for rep in reports:
        generations[rep['generation']] = generations.get(rep['generation'], 0) + 1
        if not rep['unparsed']:
            gen_clean[rep['generation']] = gen_clean.get(rep['generation'], 0) + 1

    unparsed_roster = ['%s (%s): %s' % (r['basename'], r['generation'], '; '.join(r['unparsed']))
                       for r in reports if r['unparsed']]
    anomaly_roster = ['%s: %s' % (r['basename'], '; '.join(r['anomalies']))
                      for r in reports if r['anomalies']]

    generated_utc = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    # A run's IDENTITY for the bounded-write guard: same home + same mode + same params means
    # a re-run of the same sweep (safe to overwrite); anything else is a different subject.
    sweep_subject = '%s::%s::%s' % (paths['home'].replace('\\', '/'), mode, params)
    if args.out:
        problem = guard_out_dir(args.out, sweep_subject)
        if problem:
            sys.stderr.write('sweep: %s\n' % problem)
            return 2
        out_dir = args.out
    else:
        out_dir = os.path.join(
            paths['cache'],
            datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    os.makedirs(out_dir, exist_ok=True)

    ctx = {
        'mode': mode,
        'params': params,
        'generated_utc': generated_utc,
        'reports': reports,
        'project_count': len({r['project'] for r in reports}),
        'open_recs': sum(1 for r in reports for rec in r['recs'] if not rec['closed']),
        'chains': chains,
        'hubs': hubs,
        'unparsed_count': len(unparsed_roster),
        'unparsed_roster': unparsed_roster,
        'anomaly_roster': anomaly_roster,
        'stale_dirs': stale_dirs,
        'generations': generations,
        'gen_clean': gen_clean,
        'ledgers': ledgers,
        'recs_path': paths['recs'],
        'drift_path': paths['drift'],
        'drift_active': drift_active,
        'hits_marker': hits_marker,
    }
    bundle_path = os.path.join(out_dir, 'bundle.md')
    bundle_text = render_bundle(ctx)
    with open(bundle_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(bundle_text)
    appendix_path = os.path.join(out_dir, 'appendix.md')
    with open(appendix_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(render_appendix(ctx))

    projects_manifest = {}
    for (p, d, b, _text) in selection:
        entry = projects_manifest.setdefault(p, {'dir': d.replace('\\', '/'), 'enumerated': [],
                                                 'watermark': (by_name.get(p) or {}).get('watermark')})
        entry['enumerated'].append(b)
    for entry in projects_manifest.values():
        entry['enumerated'].sort()
        entry['newest'] = entry['enumerated'][-1] if entry['enumerated'] else None

    bundle_bytes = len(bundle_text.encode('utf-8'))
    manifest = {
        'generated_utc': generated_utc,
        'mode': mode,
        'sweep_subject': sweep_subject,
        'focus': args.focus,
        'variants': list(args.variant or []),
        'deep': args.deep,
        'deep_range': list(deep_range) if deep_range else None,
        'state_home': paths['home'].replace('\\', '/'),
        'sweep_state': paths['state'].replace('\\', '/'),
        'projects': projects_manifest,
        'counts': {
            'reports': len(reports),
            'projects': len(projects_manifest),
            'open_recs': ctx['open_recs'],
            'chains': len(chains),
            'citation_hubs': len(hubs),
            'unparsed': len(unparsed_roster),
            'stale_dirs': len(stale_dirs),
            'drift_active': len(drift_active),
        },
        'generations': generations,
        'stale_dirs': stale_dirs,
        'bundle': bundle_path.replace('\\', '/'),
        'bundle_bytes': bundle_bytes,
        'appendix': appendix_path.replace('\\', '/'),
    }
    manifest_path = os.path.join(out_dir, 'manifest.json')
    with open(manifest_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write('\n')

    # Line 1 stays the bare bundle path (the skill's step-1 contract). The labelled lines carry
    # the two paths the later steps need by name — step 4 concatenates the appendix, step 5
    # passes the manifest explicitly rather than trusting the newest-manifest default.
    print(bundle_path.replace('\\', '/'))
    print('appendix: %s' % appendix_path.replace('\\', '/'))
    print('manifest: %s' % manifest_path.replace('\\', '/'))
    print('mode=%s reports=%d projects=%d open-recs=%d chains=%d hubs=%d unparsed=%d '
          'stale-dirs=%d drift=%d bundle=%dB'
          % (mode, len(reports), len(projects_manifest), ctx['open_recs'], len(chains),
             len(hubs), len(unparsed_roster), len(stale_dirs), len(drift_active), bundle_bytes))
    return 0


def newest_manifest(cache_dir):
    candidates = glob.glob(os.path.join(cache_dir, '*', 'manifest.json'))
    if not candidates:
        return None
    return max(candidates, key=lambda p: (os.path.getmtime(p), p))


def valid_watermark(value):
    """A watermark must be a report basename and nothing else. The manifest is a file on disk,
    so its contents are INPUT, not a trusted internal value: a basename carrying a newline or
    the ` · ` separator would restructure SWEEP-STATE when interpolated into a registry row —
    splitting one row into two, or inventing extra fields the nudge hook then misreads."""
    return (isinstance(value, str)
            and REPORT_RE.match(value) is not None
            and '\n' not in value and '\r' not in value and SEP not in value)


def cmd_advance(args):
    paths = state_paths()
    if args.manifest:
        manifest_path = args.manifest
    else:
        # With no explicit --manifest, resolve ONLY inside the cache dir this tool writes.
        # Widening the search would let any manifest.json elsewhere on disk steer the write.
        manifest_path = newest_manifest(paths['cache'])
    if not manifest_path or not os.path.isfile(manifest_path):
        sys.stderr.write('sweep: no manifest found (looked under %s) — run `digest` first\n'
                         % paths['cache'])
        return 2
    try:
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = json.load(fh)
        if not isinstance(manifest, dict):
            raise ValueError('manifest is not a JSON object')
    except (OSError, ValueError) as exc:
        sys.stderr.write('sweep: unreadable manifest %s (%s)\n' % (manifest_path, exc))
        return 2
    if manifest.get('mode') != 'default':
        sys.stderr.write('sweep: refusing to advance from a %r manifest — watermark writes '
                         'belong to the default mode alone\n' % manifest.get('mode'))
        return 2

    loaded = load_state_or_die(paths)
    if loaded is None:
        return 4
    lines, entries = loaded
    registered = {e['name'] for e in entries}

    projects = manifest.get('projects')
    if not isinstance(projects, dict):
        sys.stderr.write('sweep: manifest has no projects map — refusing to advance\n')
        return 2

    advanced = {}
    skipped = []
    for name, info in projects.items():
        enumerated = (info or {}).get('enumerated') if isinstance(info, dict) else None
        if not enumerated:
            continue
        bad = [b for b in enumerated if not valid_watermark(b)]
        if bad:
            # Whole-run refusal, not a per-row skip: a manifest carrying one malformed basename
            # is not a manifest this tool wrote, so nothing in it is trustworthy.
            sys.stderr.write('sweep: manifest %s contains a malformed report basename for '
                             'project %r (%r) — refusing to advance\n'
                             % (manifest_path, name, bad[0]))
            return 2
        if name not in registered:
            # Advance NEVER creates a registry row. A project the registry doesn't list is
            # reported and skipped; adding it is a deliberate hand-edit, per the file's own
            # hand-editable contract.
            skipped.append(name)
            continue
        advanced[name] = max(enumerated)

    out = list(lines)
    changed = []
    for e in entries:
        new_wm = advanced.get(e['name'])
        if not new_wm or new_wm == e['watermark']:
            continue
        out[e['idx']] = '%s%s%s%slast=%s' % (e['name'], SEP, e['dir'], SEP, new_wm)
        changed.append('%s: %s -> %s' % (e['name'], e['watermark'], new_wm))

    stamp = datetime.datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
    swept_written = False
    for i, line in enumerate(out):
        if line.startswith('swept:'):
            out[i] = 'swept: %s' % stamp
            swept_written = True
            break
    if not swept_written:
        out.append('swept: %s' % stamp)

    with open(paths['state'], 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(out) + '\n')

    print('SWEEP-STATE advanced: %d project(s), swept: %s' % (len(changed), stamp))
    for line in changed:
        print('  %s' % line)
    for name in sorted(skipped):
        print('  skipped %r — not a registered project (advance never adds a row)' % name)
    return 0


def cmd_seed(args):
    paths = state_paths()
    roots = [paths['postmortem']]
    project_root = args.project_root or os.getcwd()
    roots.append(os.path.join(project_root, '.claude', 'postmortem'))
    parent = os.path.dirname(os.path.abspath(project_root))
    roots.extend(sorted(glob.glob(os.path.join(parent, '*', '.claude', 'postmortem'))))

    seen = set()
    for d in roots:
        norm = os.path.abspath(d).replace('\\', '/')
        if norm in seen:
            continue
        seen.add(norm)
        names = list_reports(d)
        if not names:
            continue
        print('CANDIDATES: %s%s%d date-prefixed reports%snewest: %s'
              % (norm, SEP, len(names), SEP, names[-1]))
    print('Scan-only. Write SWEEP-STATE in-session after the user confirms the registry.')
    return 0


def cmd_audit(args):
    paths = state_paths()
    loaded = load_state_or_die(paths)
    if loaded is None:
        return 4
    _lines, entries = loaded
    listings = build_listings(entries)

    generations, clean, unparsed_lines, anomaly_lines, stale = {}, {}, [], [], []
    total = 0
    for e in entries:
        names = listings.get(e['dir'])
        if names is None:
            stale.append('%s%s%s' % (e['name'], SEP, e['dir']))
            continue
        for b in names:
            rep = parse_report(os.path.join(e['dir'], b), e['name'], b)
            total += 1
            g = rep['generation']
            generations[g] = generations.get(g, 0) + 1
            if not rep['unparsed']:
                clean[g] = clean.get(g, 0) + 1
            else:
                unparsed_lines.append('%s [%s] %s: %s'
                                      % (e['name'], g, b, '; '.join(rep['unparsed'])))
            if rep['anomalies']:
                anomaly_lines.append('%s [%s] %s: %s'
                                     % (e['name'], g, b, '; '.join(rep['anomalies'])))

    print('AUDIT — %d report(s) across %d registered dir(s)' % (total, len(entries)))
    for g in sorted(generations):
        ok = clean.get(g, 0)
        print('  %-4s %4d report(s)  %4d clean  %5.1f%%'
              % (g, generations[g], ok, 100.0 * ok / generations[g]))
    print('  TOTAL     %4d report(s)  %4d clean  %5.1f%%'
          % (total, sum(clean.values()), (100.0 * sum(clean.values()) / total) if total else 0.0))
    print('UNPARSED: %d' % len(unparsed_lines))
    for line in unparsed_lines:
        print('  %s' % line)
    print('ANOMALIES: %d' % len(anomaly_lines))
    for line in anomaly_lines:
        print('  %s' % line)
    print('STALE-DIRS: %d' % len(stale))
    for line in stale:
        print('  %s' % line)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sweep.py', description='harness-sweep corpus engine')
    sub = parser.add_subparsers(dest='cmd')

    p_digest = sub.add_parser('digest')
    p_digest.add_argument('--focus')
    p_digest.add_argument('--variant', action='append')
    # nargs='?'/const='' so a bare `--deep` is distinguishable from no --deep at all: the
    # bare form is the interactivity demotion (print the count table, exit 3).
    p_digest.add_argument('--deep', nargs='?', const='')
    p_digest.add_argument('--out')
    p_digest.set_defaults(func=cmd_digest)

    p_adv = sub.add_parser('advance')
    p_adv.add_argument('--manifest')
    p_adv.set_defaults(func=cmd_advance)

    p_seed = sub.add_parser('seed')
    p_seed.add_argument('--scan', action='store_true')
    p_seed.add_argument('--project-root')
    p_seed.set_defaults(func=cmd_seed)

    p_audit = sub.add_parser('audit')
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
