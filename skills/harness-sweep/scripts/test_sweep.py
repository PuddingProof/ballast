#!/usr/bin/env python
"""Regression tests for sweep.py -- the harness-sweep corpus engine.

Two properties this suite exists to pin:

  1. THE PRODUCER/CONSUMER CONTRACT. session-postmortem writes the reports; this parser reads
     them. That coupling has already rotted once (v5 reports broke the prompt-encoded extractor
     the agent version used). `V5RoundTrip` builds a report shaped EXACTLY per
     session-postmortem's SKILL.md report spec and asserts it parses with zero UNPARSED — so a
     producer-side schema change fails here, in dev/check.sh, instead of silently degrading a
     live sweep.

  2. THE SWEEP-STATE BYTE CONTRACT. .claude/hooks/harness-sweep-nudge.sh parses the registry with bash
     parameter expansion on the literal " · " separator. `Advance` re-parses the rewritten file
     with a Python replication of that exact expansion logic, so a formatting "improvement" in
     `advance` can't silently blind the nudge hook.

Every fixture here is SYNTHETIC. Real report content must never land under skills/ — dev/check.sh
check 2 scrubs that tree for personal paths and project codenames.

Hermetic: each test runs with BALLAST_CLAUDE_HOME pointed at a fresh tmp dir, so no real
SWEEP-STATE, ledger, or watermark is ever read or written.

Stdlib unittest only (no pytest dep). Self-locating: imports sweep.py from its own directory, so
`cd skills/harness-sweep/scripts && python test_sweep.py` works wherever the plugin is checked out.
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweep  # noqa: E402

SEP = ' · '
NUL = '\x00'


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data)


def hook_registry_parse(line):
    """Byte-for-byte replication of .claude/hooks/harness-sweep-nudge.sh step 3, in full:
        line="${line%$'\\r'}"; case "$line" in *" · "*) ;; *) continue ;; esac
        rest="${line#* · }"; pdir="${rest%% · *}"; wm="${rest##* · }"; wm="${wm#last=}"
        [ -d "$pdir" ] || continue
    The `-d` check is part of the parse, not an afterthought: it is what makes the hook tolerate
    a prose header that quotes the format (and therefore contains the separator). Dropping it
    here would test a hook that doesn't exist.
    Returns (pdir, watermark) or None when the hook would skip the line."""
    if line.endswith('\r'):
        line = line[:-1]
    if SEP not in line:
        return None
    rest = line.split(SEP, 1)[1]
    pdir = rest.split(SEP)[0]
    wm = rest.split(SEP)[-1]
    if wm.startswith('last='):
        wm = wm[len('last='):]
    if not os.path.isdir(pdir):
        return None
    return pdir, wm


# ---------------------------------------------------------------------------
# Synthetic fixtures — one per schema generation, plus the anomaly cases the live
# corpus census turned up (each is a real shape seen on disk, rebuilt from scratch).
# ---------------------------------------------------------------------------

V0 = """# Session Post-Mortem — synthetic v0 topic

**Schema version:** v0
**Generated:** 2026-01-02
**Transcript:** 00000000-0000-0000-0000-0000000000a1

## 1. Mid-Implementation Catches

- Something was caught.

## 5. Cross-Session Patterns

None yet.

## 6. Recommended Actions

- [ ] **Refine custom skill** (widget-tool): teach it the second code path.
  - **Evidence**: §1 the caught thing.
  - **Expected impact**: fewer repeats.
- [x] **Update memory**: recorded the widget rule.
  - **Evidence**: §2 roster.
- [ ] **Brainstorming**: include an ergonomics dimension in option forks.
  - **Evidence**: §4 the pivot.

## 7. Session Stats

**User prompts:** 3
"""

V1 = """# Session Post-Mortem — synthetic v1 topic

**Schema version:** v1
**Generated:** 2026-01-03
**Transcript:** 00000000-0000-0000-0000-0000000000b2

## 1. Mid-Implementation Catches

- One catch.

## 5. Cross-Session Patterns

- **P1** — a pattern with no id grammar yet.

## 6. Recommended Actions

**Closed mid-session:**

- [x] **Add tracker entry** — captured the backlog items.
  - **Evidence**: §3.

**Open:**

- [ ] **Refine custom skill** — handle the forked-session sidecar merge.
  - **Evidence**: §2(a) fork-split note.
  - **Expected impact**: complete rosters on forked sessions.

## 7. Session Stats
"""

V2 = """# Session Post-Mortem — synthetic v2 topic

**Schema version:** v2
**Generated:** 2026-01-04
**Mode:** normal
**Transcript:** 00000000-0000-0000-0000-0000000000c3

---

**Session:** \U0001F7E1 — A productive ship with one friction: the option fork omitted an axis.

**Top actions (BLUF):**
- Formalize the roster grouping [→R1]

## 1. Mid-Implementation Catches

- One catch.

## 5. Cross-Session Patterns

**P1 ESTABLISHED — adversarial review catches what solo authoring misses.** Citations: `aaaaaaa1`, `aaaaaaa2`; **+1 this session**.
- **`P2`** — scope items drift across compaction boundaries. 3 occurrences.

Prose that is not a pattern line.

## 6. Recommended Actions

- [ ] **`R1` Refine custom skill** — formalize the roster phase-grouping for very large sessions.
  - **Evidence**: §2(a) — 29 invocation lines this session.
  - **Expected impact**: large rosters stay scannable.
- [ ] **`R2` CLAUDE.md update** \U0001F534 **ESCALATED** — pin the label convention. `↳ carried from `deadbeef#R4` (2×, open since 2026-01-01)`.
  - **Evidence**: §4 U1.
- [x] **`R3` Update existing memory** — already applied this session.
  - **Evidence**: §1 C3.

## 7. Session Stats
"""

# v4 carries four live anomalies at once: a `**Session:**` STAT line preceding the `**Session:**`
# VERDICT line, a suffixed section heading, an HTML-entity heading, and a duplicated `## 7.`.
V4 = """# Session Post-Mortem — synthetic v4 topic

**Schema version:** v4
**Session:** `0000000d` · 2026-01-05T10:00:00Z → 11:00:00Z (1h)
**Generated:** 2026-01-05
**Transcript:** 00000000-0000-0000-0000-0000000000d4

**Session:** \U0001F534 A run that missed its objective; two corrections were needed.

## 1. Mid-Implementation Catches

- One catch.

## 2. Skill &amp; Agent Usage — and the extra verdict

- roster line.

## 5. Cross-Session Patterns

- **P1** — the recurring class, 2 occurrences.

## 6. Recommended Actions

- [ ] **R1 New memory entry**: route plugin-shipped surface recs to the owning plugin repo.
  - **Evidence**: §5 P1.
- [ ] ↗ SUPERSEDED by `beefcafe#R9` — **`R2` Refine custom skill** (cross-repo) ↳ carried from `0000000a#R2` (2×, open since 2026-01-01) — dormant: the mark-semantics check.
- [x] **`R3` Adjust hook** — shipped this session.

## 7. Session Stats

## 7. Session Stats & Tool Fingerprint

**User prompts:** 4

## Addendum (post-hoc, added later)

Free-form tail section appended after the canonical sections.
"""

# Shaped EXACTLY per skills/session-postmortem/SKILL.md's report spec (the producer contract).
V5 = """# Post-mortem — widgetproj — 2026-01-06 (000000e5)
**Session:** 00000000-0000-0000-0000-0000000000e5 · 229 lines · 15m 23s wall
**Models:** model-x: 65 in / 49,028 out, ~$8.56 · total ~$8.56
**Invocation:** (none) · digest 12,335B
**Postmortem run:** 2026-01-06T00:46:30Z · digest→draft 0m 17s · opus medium · schema v5

## Verdict — \U0001F7E2
- One typed prompt in, one committed artifact out.
- Zero user corrections, one recoverable tool error.

## Narrative
The session did the thing it was asked to do, then stopped.

## Steering & corrections
No user steering occurred.

## Friction & failures
- One encoding error, resolved on the next call.

## Inventory
| Kind | Fired | Notes |
|---|---|---|
| Skills | none | — |

## Signals
- [ ] R1 — the id resolver should fail loudly instead of falling back to another session.
- [ ] R2 — slug derivation should skip dot-directories. Route to the owning plugin repo.
- [x] R3 — already applied this session.
"""

UNKNOWN_GEN = """# Something else entirely

No schema marker at all.

## Notes

- [ ] a checkbox in a section this parser has no grammar for.
"""


class Base(unittest.TestCase):
    """Every test gets its own BALLAST_CLAUDE_HOME + synthetic project dirs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sweep-test-')
        self.home = os.path.join(self.tmp, 'home')
        self.pm = os.path.join(self.home, 'postmortem')
        os.makedirs(self.pm)
        self._old_env = os.environ.get('BALLAST_CLAUDE_HOME')
        os.environ['BALLAST_CLAUDE_HOME'] = self.home

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop('BALLAST_CLAUDE_HOME', None)
        else:
            os.environ['BALLAST_CLAUDE_HOME'] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- fixture helpers ---------------------------------------------------

    def project(self, name):
        d = os.path.join(self.tmp, name, '.claude', 'postmortem')
        os.makedirs(d, exist_ok=True)
        return d

    def report(self, directory, basename, body):
        path = os.path.join(directory, basename)
        write(path, body)
        return path

    def state(self, rows, header=True, swept='2026-01-01T00:00:00-0400', eol='\n'):
        lines = []
        if header:
            lines.append('# SWEEP-STATE — harness-sweep watermark registry')
            lines.append('')
            # The prose header quotes the format, so it CONTAINS the separator. It must never be
            # mistaken for a registry row (that bug reported the header as a STALE-DIR).
            lines.append('> One registry line per project: `<name>' + SEP + '<dir>' + SEP
                         + 'last=<basename|NONE>`. Lines without the separator are ignored.')
            lines.append('')
        if swept:
            lines.append('swept: ' + swept)
            lines.append('')
        lines.extend(rows)
        write(os.path.join(self.pm, 'SWEEP-STATE.md'), eol.join(lines) + eol)

    def row(self, name, directory, watermark):
        return '%s%s%s%slast=%s' % (name, SEP, directory.replace('\\', '/'), SEP, watermark)

    def ledgers(self, recs='', drift=''):
        write(os.path.join(self.pm, 'HARNESS-RECS.md'), recs)
        write(os.path.join(self.pm, 'DRIFT-SIGHTINGS.md'), drift)

    # --- runner ------------------------------------------------------------

    def run_cmd(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sweep.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def digest_to(self, extra=()):
        out_dir = os.path.join(self.tmp, 'out%d' % len(os.listdir(self.tmp)))
        rc, out, err = self.run_cmd(['digest', '--out', out_dir] + list(extra))
        return rc, out, err, out_dir

    def bundle_of(self, out_dir):
        with open(os.path.join(out_dir, 'bundle.md'), encoding='utf-8') as fh:
            return fh.read()

    def manifest_of(self, out_dir):
        with open(os.path.join(out_dir, 'manifest.json'), encoding='utf-8') as fh:
            return json.load(fh)


# ---------------------------------------------------------------------------
# Generation detection
# ---------------------------------------------------------------------------

class Generations(Base):

    def _gen(self, basename, body):
        d = self.project('p')
        return sweep.parse_report(self.report(d, basename, body), 'p')['generation']

    def test_v0(self):
        self.assertEqual(self._gen('2026-01-02-x-000000a1.md', V0), 'v0')

    def test_v1(self):
        self.assertEqual(self._gen('2026-01-03-x-000000b2.md', V1), 'v1')

    def test_v2(self):
        self.assertEqual(self._gen('2026-01-04-x-000000c3.md', V2), 'v2')

    def test_v4(self):
        self.assertEqual(self._gen('2026-01-05-x-000000d4.md', V4), 'v4')

    def test_v5_has_no_schema_version_field(self):
        # v5 dropped the header field entirely; detection falls to the title + run-line token.
        self.assertNotIn('**Schema version:**', V5)
        self.assertEqual(self._gen('2026-01-06-x-000000e5.md', V5), 'v5')

    def test_unknown_generation_is_flagged_not_guessed(self):
        d = self.project('p')
        rep = sweep.parse_report(self.report(d, '2026-01-07-x-000000f6.md', UNKNOWN_GEN), 'p')
        self.assertEqual(rep['generation'], '?')
        self.assertTrue(any('unknown schema generation' in u for u in rep['unparsed']))


# ---------------------------------------------------------------------------
# Per-generation extraction
# ---------------------------------------------------------------------------

class ExtractV0V1(Base):

    def test_v0_positional_ids_and_fields(self):
        d = self.project('p')
        rep = sweep.parse_report(self.report(d, '2026-01-02-x-000000a1.md', V0), 'p')
        self.assertEqual([r['id'] for r in rep['recs']], [1, 2, 3])
        self.assertTrue(all(r['positional'] for r in rep['recs']))
        self.assertEqual([r['closed'] for r in rep['recs']], [False, True, False])
        self.assertEqual(rep['recs'][0]['category'], 'Refine custom skill')
        self.assertIn('the caught thing', rep['recs'][0]['evidence'])
        # No `**Session:** <glyph>` line exists before v2 — verdict must be NONE, not invented.
        self.assertIsNone(rep['verdict'])
        self.assertEqual(rep['gist'], 'synthetic v0 topic')
        self.assertEqual(rep['unparsed'], [])

    def test_v0_closed_recs_are_kept_but_excluded_from_open_block(self):
        d = self.project('p')
        rep = sweep.parse_report(self.report(d, '2026-01-02-x-000000a1.md', V0), 'p')
        block = '\n'.join(sweep.render_report_block(rep))
        self.assertIn('R1 [Refine custom skill]', block)
        self.assertNotIn('Update memory', block)      # the [x] rec

    def test_v1_positional_across_subheaders(self):
        d = self.project('p')
        rep = sweep.parse_report(self.report(d, '2026-01-03-x-000000b2.md', V1), 'p')
        # The `**Closed mid-session:**` / `**Open:**` prose sub-headers must not break numbering.
        self.assertEqual([r['id'] for r in rep['recs']], [1, 2])
        self.assertEqual(rep['recs'][0]['closed'], True)
        self.assertEqual(rep['recs'][1]['closed'], False)
        self.assertEqual(rep['unparsed'], [])


class ExtractV2(Base):

    def setUp(self):
        super().setUp()
        d = self.project('p')
        self.rep = sweep.parse_report(self.report(d, '2026-01-04-x-000000c3.md', V2), 'p')

    def test_verdict_and_gist(self):
        self.assertEqual(self.rep['verdict'], '\U0001F7E1')
        self.assertTrue(self.rep['gist'].startswith('A productive ship'))

    def test_backtick_ids(self):
        self.assertEqual([r['id'] for r in self.rep['recs']], [1, 2, 3])
        self.assertFalse(any(r['positional'] for r in self.rep['recs']))
        self.assertEqual(self.rep['recs'][0]['category'], 'Refine custom skill')

    def test_status_and_carry_captured_verbatim(self):
        r2 = self.rep['recs'][1]
        self.assertIn('ESCALATED', r2['status'])
        self.assertIn('carried from', r2['status'])
        self.assertEqual(r2['carried_from'], ['deadbeef#R4'])

    def test_open_rec_defaults_to_open_status(self):
        self.assertEqual(self.rep['recs'][0]['status'], 'open')
        self.assertEqual(self.rep['recs'][0]['carried_from'], [])

    def test_patterns(self):
        self.assertEqual(len(self.rep['patterns']), 2)
        self.assertIn('P1 ESTABLISHED', self.rep['patterns'][0])
        self.assertNotIn('Prose that is not a pattern line.', ' '.join(self.rep['patterns']))

    def test_clean(self):
        self.assertEqual(self.rep['unparsed'], [])


class ExtractV4Anomalies(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.rep = sweep.parse_report(self.report(self.d, '2026-01-05-x-000000d4.md', V4), 'p')

    def test_verdict_line_is_the_glyph_one_not_the_stat_one(self):
        # One live v4 report uses `**Session:**` twice: a session-id stat line AND the verdict.
        self.assertEqual(self.rep['verdict'], '\U0001F534')
        self.assertTrue(self.rep['gist'].startswith('A run that missed'))

    def test_entity_and_suffixed_heading_do_not_break_sectioning(self):
        self.assertIn('HTML entities', ' '.join(self.rep['anomalies']))
        self.assertEqual(len(self.rep['recs']), 3)   # §6 still located

    def test_duplicated_heading_tolerated(self):
        # `## 7.` appears twice back-to-back; §6's body must stop at the FIRST one, so the recs
        # section is neither truncated early nor swallowed by the duplicate.
        self.assertEqual([r['id'] for r in self.rep['recs']], [1, 2, 3])

    def test_post_hoc_tail_section_is_not_parsed_as_recs(self):
        joined = ' '.join(r['text'] for r in self.rep['recs'])
        self.assertNotIn('Free-form tail section', joined)

    def test_bare_bold_id_form(self):
        self.assertEqual(self.rep['recs'][0]['id'], 1)
        self.assertEqual(self.rep['recs'][0]['category'], 'New memory entry')

    def test_id_after_a_leading_status_clause(self):
        # `- [ ] ↗ SUPERSEDED by `x#R9` — **`R2` Refine custom skill** …` — the id is real, it
        # just isn't at position 0. It must be read as R2, with the clause kept as status.
        r2 = self.rep['recs'][1]
        self.assertEqual(r2['id'], 2)
        self.assertEqual(r2['category'], 'Refine custom skill')
        self.assertIn('SUPERSEDED', r2['status'])
        self.assertIn('dormant', r2['status'])
        # Both the carried-from origin AND the supersede target are chain edges — over-return is
        # the contract (the model filters; the script applies no relevance judgment).
        self.assertEqual(r2['carried_from'], ['0000000a#R2', 'beefcafe#R9'])
        self.assertEqual(self.rep['unparsed'], [])

    def test_handoff_phrase_detected_over_returned(self):
        self.assertIn('owning plugin repo', self.rep['recs'][0]['handoff'])

    def test_raw_nul_byte_survives_and_is_reported(self):
        path = os.path.join(self.d, '2026-01-08-nul-000000a9.md')
        write_bytes(path, V4.replace('One catch.', 'One' + NUL + ' catch.').encode('utf-8'))
        rep = sweep.parse_report(path, 'p')
        self.assertIn('raw NUL byte', ' '.join(rep['anomalies']))
        self.assertEqual(len(rep['recs']), 3)   # the NUL must not derail parsing either


class V5RoundTrip(Base):
    """THE PRODUCER/CONSUMER CONTRACT TEST — see the module docstring."""

    def setUp(self):
        super().setUp()
        d = self.project('p')
        self.rep = sweep.parse_report(self.report(d, '2026-01-06-x-000000e5.md', V5), 'p')

    def test_zero_unparsed(self):
        self.assertEqual(self.rep['unparsed'], [], 'a spec-shaped v5 report must parse cleanly')

    def test_verdict_from_heading_and_gist_from_first_bullet(self):
        self.assertEqual(self.rep['verdict'], '\U0001F7E2')
        self.assertEqual(self.rep['gist'], 'One typed prompt in, one committed artifact out.')

    def test_session_stat_line_is_not_mistaken_for_a_verdict(self):
        # v5 reuses `**Session:**` for the session-id stat line; it carries no gauge glyph.
        self.assertNotIn('229 lines', self.rep['gist'])

    def test_bare_signal_ids(self):
        self.assertEqual([r['id'] for r in self.rep['recs']], [1, 2, 3])
        self.assertEqual([r['closed'] for r in self.rep['recs']], [False, False, True])
        self.assertEqual(self.rep['recs'][0]['category'], '—')
        self.assertIn('fail loudly', self.rep['recs'][0]['text'])

    def test_no_patterns_section_is_not_an_error(self):
        self.assertEqual(self.rep['patterns'], [])

    def test_handoff_flagged(self):
        self.assertIn('owning plugin repo', self.rep['recs'][1]['handoff'])

    def test_block_render_shape(self):
        block = '\n'.join(sweep.render_report_block(self.rep))
        for field in ('VERDICT:', 'GIST:', 'OPEN-RECS:', 'PATTERNS:', 'DRIFT: NONE', 'HANDOFFS:'):
            self.assertIn(field, block)
        self.assertTrue(block.startswith('### 000000e5 — 2026-01-06-x-000000e5.md (p) [v5]'))


class UnparsedNeverDrops(Base):

    def test_unknown_shape_embeds_raw_section(self):
        d = self.project('p')
        body = V2.replace('- [ ] **`R1` Refine custom skill** — formalize the roster '
                          'phase-grouping for very large sessions.',
                          '- a bullet in a rec format this parser does not know')
        body = body.replace('- [ ] **`R2`', '- **`R2`').replace('- [x] **`R3`', '- **`R3`')
        rep = sweep.parse_report(self.report(d, '2026-01-09-x-000000ab.md', body), 'p')
        self.assertEqual(rep['recs'], [])
        self.assertTrue(any('top-level list items' in u for u in rep['unparsed']))
        block = '\n'.join(sweep.render_report_block(rep))
        self.assertIn('RAW-SECTION', block)
        self.assertIn('a bullet in a rec format this parser does not know', block)

    def test_prose_only_recs_section_is_valid_not_unparsed(self):
        # "_No actions recommended this session._" is what a clean run writes — flagging it would
        # drown the roster in noise.
        d = self.project('p')
        body = V2.split('## 6. Recommended Actions')[0] + \
            '## 6. Recommended Actions\n\n_No actions recommended this session._\n\n## 7. Stats\n'
        rep = sweep.parse_report(self.report(d, '2026-01-10-x-000000ac.md', body), 'p')
        self.assertEqual(rep['recs'], [])
        self.assertEqual(rep['unparsed'], [])


# ---------------------------------------------------------------------------
# Registry / watermark enumeration
# ---------------------------------------------------------------------------

class Watermarks(Base):

    def test_inclusive_date_rule(self):
        names = ['2026-01-01-a-00000001.md', '2026-01-02-a-00000002.md',
                 '2026-01-02-b-00000003.md', '2026-01-03-a-00000004.md']
        # Deliberately wider than the nudge hook's strict `>`: the watermark's OWN day re-enters.
        self.assertEqual(sweep.unseen(names, '2026-01-02-a-00000002.md'),
                         ['2026-01-02-a-00000002.md', '2026-01-02-b-00000003.md',
                          '2026-01-03-a-00000004.md'])

    def test_none_watermark_takes_everything(self):
        names = ['2026-01-01-a-00000001.md']
        self.assertEqual(sweep.unseen(names, 'NONE'), names)
        self.assertEqual(sweep.unseen(names, ''), names)

    def test_non_report_files_excluded(self):
        d = self.project('p')
        for n in ('2026-01-01-a-00000001.md', 'SWEEP-STATE.md', 'HARNESS-RECS.md', 'notes.txt'):
            write(os.path.join(d, n), 'x')
        self.assertEqual(sweep.list_reports(d), ['2026-01-01-a-00000001.md'])

    def test_missing_dir_returns_none_for_stale_detection(self):
        self.assertIsNone(sweep.list_reports(os.path.join(self.tmp, 'nope')))

    def test_prose_header_containing_the_separator_is_not_a_registry_row(self):
        d = self.project('p')
        self.report(d, '2026-01-04-x-000000c3.md', V2)
        self.state([self.row('p', d, 'NONE')])
        _lines, entries = sweep.parse_state(
            sweep.read_text(os.path.join(self.pm, 'SWEEP-STATE.md')))
        self.assertEqual([e['name'] for e in entries], ['p'])

    def test_crlf_registry_line_parses(self):
        d = self.project('p')
        self.report(d, '2026-01-04-x-000000c3.md', V2)
        self.state([self.row('p', d, 'NONE')], eol='\r\n')
        _lines, entries = sweep.parse_state(
            sweep.read_text(os.path.join(self.pm, 'SWEEP-STATE.md')))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['watermark'], 'NONE')   # not "NONE\r"
        self.assertEqual(entries[0]['dir'], d.replace('\\', '/'))

    def test_stale_dir_recorded_in_bundle_and_manifest(self):
        d = self.project('p')
        self.report(d, '2026-01-04-x-000000c3.md', V2)
        self.state([self.row('p', d, 'NONE'),
                    self.row('gone', os.path.join(self.tmp, 'gone'), 'NONE')])
        self.ledgers()
        rc, out, err, out_dir = self.digest_to()
        self.assertEqual(rc, 0, err)
        self.assertIn('STALE-DIR', self.bundle_of(out_dir))
        self.assertEqual(self.manifest_of(out_dir)['counts']['stale_dirs'], 1)


class MissingState(Base):

    def test_digest_exits_4_with_a_seeding_message(self):
        rc, out, err = self.run_cmd(['digest'])
        self.assertEqual(rc, 4)
        self.assertIn('seed', err)

    def test_state_home_env_override_is_honored(self):
        # Nothing under the real ~/.claude may be consulted: the error must name the tmp home.
        rc, out, err = self.run_cmd(['digest'])
        self.assertIn(self.home.replace('\\', '/').split('/')[-1], err.replace('\\', '/'))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

class Modes(Base):

    def setUp(self):
        super().setUp()
        self.pa = self.project('alpha')
        self.pb = self.project('beta')
        self.a1 = self.report(self.pa, '2026-01-02-x-000000a1.md', V0)
        self.a2 = self.report(self.pa, '2026-01-04-x-000000c3.md', V2)
        self.b1 = self.report(self.pb, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('alpha', self.pa, '2026-01-04-x-000000c3.md'),
                    self.row('beta', self.pb, 'NONE')])
        self.ledgers()

    def test_default_mode_respects_watermarks(self):
        rc, out, err, out_dir = self.digest_to()
        self.assertEqual(rc, 0, err)
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'default')
        self.assertEqual(m['projects']['alpha']['enumerated'], ['2026-01-04-x-000000c3.md'])
        self.assertEqual(m['projects']['beta']['enumerated'], ['2026-01-06-x-000000e5.md'])
        self.assertEqual(m['counts']['reports'], 2)
        self.assertIn('mode=default reports=2', out)

    def test_focus_existing_path_wins_over_topic(self):
        rc, out, err, out_dir = self.digest_to(['--focus', self.a1])
        self.assertEqual(rc, 0, err)
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'focus-path')
        self.assertEqual(m['counts']['reports'], 1)
        self.assertEqual(m['projects']['alpha']['enumerated'], ['2026-01-02-x-000000a1.md'])

    def test_focus_registry_name_is_project_scope(self):
        rc, out, err, out_dir = self.digest_to(['--focus', 'alpha'])
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'focus-project')
        # Project scope is watermark-based, like the default.
        self.assertEqual(m['projects']['alpha']['enumerated'], ['2026-01-04-x-000000c3.md'])

    def test_focus_topic_greps_whole_corpus_watermark_free(self):
        rc, out, err, out_dir = self.digest_to(['--focus', 'ergonomics dimension'])
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'focus-topic')
        # The v0 report is BEHIND alpha's watermark and still enters — topic focus ignores it.
        self.assertEqual(m['projects']['alpha']['enumerated'], ['2026-01-02-x-000000a1.md'])

    def test_variants_widen_the_topic_grep(self):
        rc, out, err, out_dir = self.digest_to(
            ['--focus', 'nonexistent-term', '--variant', 'slug derivation'])
        m = self.manifest_of(out_dir)
        self.assertEqual(m['counts']['reports'], 1)
        self.assertEqual(m['variants'], ['slug derivation'])

    def test_zero_hits_still_writes_a_bundle_and_exits_0(self):
        rc, out, err, out_dir = self.digest_to(['--focus', 'zzz-no-such-topic-zzz'])
        self.assertEqual(rc, 0, err)
        self.assertIn('HITS: 0', self.bundle_of(out_dir))

    def test_deep_range_grammar_days(self):
        today = datetime.date(2026, 1, 10)
        self.assertEqual(sweep.parse_deep('7', today=today), ('2026-01-03', '2026-01-10'))

    def test_deep_range_grammar_explicit(self):
        self.assertEqual(sweep.parse_deep('2026-01-01..2026-01-05'),
                         ('2026-01-01', '2026-01-05'))

    def test_deep_range_grammar_rejects_garbage(self):
        self.assertIsNone(sweep.parse_deep('last tuesday'))
        rc, out, err, out_dir = self.digest_to(['--deep', 'last tuesday'])
        self.assertEqual(rc, 2)

    def test_deep_is_watermark_free(self):
        rc, out, err, out_dir = self.digest_to(['--deep', '2026-01-01..2026-01-05'])
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'deep')
        self.assertEqual(sorted(m['projects']['alpha']['enumerated']),
                         ['2026-01-02-x-000000a1.md', '2026-01-04-x-000000c3.md'])
        self.assertNotIn('beta', m['projects'])       # out of range

    def test_deep_combines_with_focus_as_an_intersection(self):
        rc, out, err, out_dir = self.digest_to(
            ['--deep', '2026-01-01..2026-01-03', '--focus', 'alpha'])
        m = self.manifest_of(out_dir)
        self.assertEqual(m['mode'], 'deep+focus-project')
        self.assertEqual(m['counts']['reports'], 0)   # alpha's unseen slice starts 01-04

    def test_undated_deep_prints_a_count_table_and_exits_3(self):
        rc, out, err, out_dir = self.digest_to(['--deep'])
        self.assertEqual(rc, 3)
        self.assertIn('DEEP-RANGE COUNTS', out)
        self.assertIn('all', out)
        self.assertFalse(os.path.exists(os.path.join(out_dir, 'bundle.md')))


# ---------------------------------------------------------------------------
# advance — the SWEEP-STATE byte contract
# ---------------------------------------------------------------------------

class Advance(Base):

    def setUp(self):
        super().setUp()
        self.pa = self.project('alpha')
        self.report(self.pa, '2026-01-02-x-000000a1.md', V0)
        self.report(self.pa, '2026-01-04-x-000000c3.md', V2)
        self.pb = self.project('beta')
        self.report(self.pb, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('alpha', self.pa, '2026-01-02-x-000000a1.md'),
                    self.row('beta', self.pb, 'NONE'),
                    'a hand-written note with no separator at all'])
        self.ledgers()
        self.state_file = os.path.join(self.pm, 'SWEEP-STATE.md')
        self.before = sweep.read_text(self.state_file)

    def advance(self, out_dir=None):
        argv = ['advance']
        if out_dir:
            argv += ['--manifest', os.path.join(out_dir, 'manifest.json')]
        return self.run_cmd(argv)

    def test_watermarks_move_to_the_lexically_greatest_enumerated_basename(self):
        _rc, _o, _e, out_dir = self.digest_to()
        rc, out, err = self.advance(out_dir)
        self.assertEqual(rc, 0, err)
        _lines, entries = sweep.parse_state(sweep.read_text(self.state_file))
        wm = {e['name']: e['watermark'] for e in entries}
        self.assertEqual(wm['alpha'], '2026-01-04-x-000000c3.md')
        self.assertEqual(wm['beta'], '2026-01-06-x-000000e5.md')

    def test_nudge_hook_parameter_expansion_still_works_byte_for_byte(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.advance(out_dir)
        with open(self.state_file, 'rb') as fh:
            raw = fh.read()
        self.assertNotIn(b'\r', raw)                       # LF endings
        self.assertTrue(raw.endswith(b'\n'))               # final newline
        self.assertIn(SEP.encode('utf-8'), raw)            # separator bytes intact
        parsed = [hook_registry_parse(ln) for ln in raw.decode('utf-8').split('\n')]
        rows = [p for p in parsed if p]
        self.assertEqual([r[1] for r in rows],
                         ['2026-01-04-x-000000c3.md', '2026-01-06-x-000000e5.md'])
        self.assertEqual(rows[0][0], self.pa.replace('\\', '/'))

    def test_non_registry_lines_preserved_byte_for_byte(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.advance(out_dir)
        after = sweep.read_text(self.state_file).split('\n')
        for line in self.before.split('\n'):
            if line.startswith('swept:') or line.startswith('alpha' + SEP) \
                    or line.startswith('beta' + SEP):
                continue
            if line:
                self.assertIn(line, after, 'lost or rewrote a non-registry line: %r' % line)

    def test_swept_line_rewritten_with_a_utc_offset_stamp(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.advance(out_dir)
        swept = [ln for ln in sweep.read_text(self.state_file).split('\n')
                 if ln.startswith('swept:')]
        self.assertEqual(len(swept), 1)
        stamp = swept[0].split('swept:', 1)[1].strip()
        datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S%z')   # raises if the shape drifted

    def test_refuses_a_focus_manifest(self):
        _rc, _o, _e, out_dir = self.digest_to(['--focus', 'alpha'])
        rc, out, err = self.advance(out_dir)
        self.assertEqual(rc, 2)
        self.assertIn('refusing to advance', err)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_refuses_a_deep_manifest(self):
        _rc, _o, _e, out_dir = self.digest_to(['--deep', '2026-01-01..2026-01-09'])
        rc, out, err = self.advance(out_dir)
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_no_manifest_at_all_is_a_usage_error_not_a_write(self):
        rc, out, err = self.advance()
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_defaults_to_the_newest_manifest_under_the_cache_dir(self):
        rc, out, err = self.run_cmd(['digest'])       # no --out: lands in <home>/.cache/...
        self.assertEqual(rc, 0, err)
        rc, out, err = self.advance()
        self.assertEqual(rc, 0, err)
        _lines, entries = sweep.parse_state(sweep.read_text(self.state_file))
        self.assertEqual({e['name']: e['watermark'] for e in entries}['beta'],
                         '2026-01-06-x-000000e5.md')


# ---------------------------------------------------------------------------
# Chain graph
# ---------------------------------------------------------------------------

CHAIN_A = """# Session Post-Mortem — chain a

**Schema version:** v3
**Generated:** 2026-01-11

## 6. Recommended Actions

- [ ] **`R1` Refine custom skill** — the recurring thing. ↳ carried from `cafebabe#R2` (2×).
- [ ] **`R2` CLAUDE.md update** \U0001F534 **ESCALATED** — unrelated chain root.

## 7. Session Stats
"""

CHAIN_B = """# Session Post-Mortem — chain b

**Schema version:** v3
**Generated:** 2026-01-12

## 6. Recommended Actions

- [ ] **`R1` Refine custom skill** — same class again. ↳ carried from `0000ba11#R1` (3×).

## 7. Session Stats
"""

LEDGER = ("# HARNESS-RECS\n\n"
          "| Chain (representative ids) | Disposition | Where / when |\n"
          "|---|---|---|\n"
          "| The recurring thing (`cafebabe#R2 → 0000ba11#R1`) | CLOSED — shipped | "
          "somewhere, 2026-01-13. Do not re-carry. |\n"
          "| An unrelated row with no handles | PARKED | nowhere |\n")


class Chains(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-11-a-0000ba11.md', CHAIN_A)
        self.report(self.d, '2026-01-12-b-0000ba12.md', CHAIN_B)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers(recs=LEDGER, drift='')

    def chains(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.bundle = self.bundle_of(out_dir)
        return self.bundle

    def test_union_joins_reports_through_a_shared_handle(self):
        bundle = self.chains()
        chain_section = bundle.split('## Chain graph')[1].split('## Ledgers')[0]
        # 0000ba11#R1 is BOTH chain-a's carried-from target and chain-b's own handle, so the two
        # reports' recs plus the out-of-corpus cafebabe#R2 must land in one chain.
        self.assertIn('0000ba11#R1', chain_section)
        self.assertIn('0000ba12#R1', chain_section)
        self.assertIn('cafebabe#R2 (out-of-corpus)', chain_section)

    def test_unrelated_single_member_chain_is_not_emitted(self):
        chain_section = self.chains().split('## Chain graph')[1].split('## Ledgers')[0]
        self.assertNotIn('0000ba11#R2', chain_section)

    def test_matched_ledger_row_is_quoted_verbatim(self):
        chain_section = self.chains().split('## Chain graph')[1].split('## Ledgers')[0]
        row = [ln for ln in LEDGER.split('\n') if 'The recurring thing' in ln][0]
        self.assertIn(row, chain_section)
        self.assertNotIn('An unrelated row with no handles', chain_section)

    def test_members_ordered_chronologically(self):
        chain_section = self.chains().split('## Chain graph')[1].split('## Ledgers')[0]
        self.assertLess(chain_section.index('0000ba11#R1'), chain_section.index('0000ba12#R1'))

    def test_escalation_flag_surfaced(self):
        bundle = self.chains()
        self.assertIn('ESCALATED', bundle)


# ---------------------------------------------------------------------------
# Bundle / ledger / drift assembly
# ---------------------------------------------------------------------------

class BundleAssembly(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers(
            recs=LEDGER,
            drift=('# DRIFT-SIGHTINGS\n\n'
                   '2026-01-05' + SEP + 'projx' + SEP + 'kind=shape-one ×1\n'
                   '2026-01-06' + SEP + 'projx' + SEP + 'kind=shape-two ×2 [registered]\n'
                   '2026-01-07 (first 01-02)' + SEP + 'projx' + SEP + 'kind=shape-three ×9\n'
                   '2026-01-07' + SEP + 'projx' + SEP + 'kind=shape-four ×1 — unregistered\n'))

    def test_ledgers_inlined_verbatim(self):
        _rc, _o, _e, out_dir = self.digest_to()
        bundle = self.bundle_of(out_dir)
        for line in LEDGER.strip().split('\n'):
            self.assertIn(line, bundle)

    def test_drift_inbox_lists_only_unregistered_sightings(self):
        _rc, _o, _e, out_dir = self.digest_to()
        inbox = self.bundle_of(out_dir).split('## Drift inbox')[1].split('## Gist table')[0]
        self.assertIn('shape-one', inbox)
        self.assertNotIn('shape-two', inbox)
        self.assertIn('shape-three', inbox, 'a (first MM-DD) date form is still a live line')
        self.assertIn('shape-four', inbox, 'prose "unregistered" is not the [registered] tag')

    def test_gist_table_rows(self):
        _rc, _o, _e, out_dir = self.digest_to()
        table = self.bundle_of(out_dir).split('## Gist table')[1].split('## Parse health')[0]
        self.assertIn('| p `000000e5` |', table)

    def test_parse_health_reports_per_generation_rates(self):
        _rc, _o, _e, out_dir = self.digest_to()
        health = self.bundle_of(out_dir).split('## Parse health')[1]
        self.assertIn('v5: 1 report(s), 1 clean (100%)', health)

    def test_missing_ledgers_are_reported_not_fatal(self):
        os.remove(os.path.join(self.pm, 'HARNESS-RECS.md'))
        rc, out, err, out_dir = self.digest_to()
        self.assertEqual(rc, 0, err)
        self.assertIn('(missing at', self.bundle_of(out_dir))


# ---------------------------------------------------------------------------
# seed / audit
# ---------------------------------------------------------------------------

class SeedAndAudit(Base):

    def test_seed_scan_lists_candidates_and_writes_nothing(self):
        root = os.path.join(self.tmp, 'projects', 'mine')
        d = os.path.join(root, '.claude', 'postmortem')
        os.makedirs(d)
        write(os.path.join(d, '2026-01-04-x-000000c3.md'), V2)
        sib = os.path.join(self.tmp, 'projects', 'other', '.claude', 'postmortem')
        os.makedirs(sib)
        write(os.path.join(sib, '2026-01-06-x-000000e5.md'), V5)
        write(os.path.join(self.pm, '2026-01-02-x-000000a1.md'), V0)

        rc, out, err = self.run_cmd(['seed', '--scan', '--project-root', root])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.count('CANDIDATES:'), 3)      # hub + project + sibling
        self.assertIn('1 date-prefixed reports', out)
        self.assertIn('newest: 2026-01-06-x-000000e5.md', out)
        self.assertFalse(os.path.exists(os.path.join(self.pm, 'SWEEP-STATE.md')))

    def test_audit_covers_the_whole_corpus_watermark_free(self):
        d = self.project('p')
        self.report(d, '2026-01-02-x-000000a1.md', V0)
        self.report(d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('p', d, '2026-01-06-x-000000e5.md')])
        rc, out, err = self.run_cmd(['audit'])
        self.assertEqual(rc, 0, err)
        self.assertIn('AUDIT — 2 report(s)', out)
        self.assertIn('UNPARSED: 0', out)
        self.assertIn('v0', out)
        self.assertIn('v5', out)

    def test_audit_without_state_exits_4(self):
        rc, out, err = self.run_cmd(['audit'])
        self.assertEqual(rc, 4)


# ---------------------------------------------------------------------------
# Appendix contract — the skill concatenates this file onto its digest verbatim
# ---------------------------------------------------------------------------

class Appendix(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers(recs=LEDGER, drift='2026-01-05' + SEP + 'projx' + SEP + 'kind=shape ×1\n')

    def appendix_of(self, out_dir):
        with open(os.path.join(out_dir, 'appendix.md'), encoding='utf-8') as fh:
            return fh.read()

    def test_written_beside_the_bundle(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.assertTrue(os.path.isfile(os.path.join(out_dir, 'appendix.md')))

    def test_holds_exactly_the_three_digest_tail_sections(self):
        _rc, _o, _e, out_dir = self.digest_to()
        text = self.appendix_of(out_dir)
        self.assertEqual([ln for ln in text.split('\n') if ln.startswith('## ')],
                         ['## Chain graph', '## Gist table', '## Parse health'])

    def test_excludes_the_model_reading_input(self):
        # Ledgers and the drift inbox are what the model READS; re-emitting them into the
        # committed digest would duplicate two files that already have canonical homes.
        text = self.appendix_of(self.digest_to()[3])
        self.assertNotIn('## Ledgers', text)
        self.assertNotIn('## Drift inbox', text)
        self.assertNotIn('An unrelated row with no handles', text)   # a ledger BODY line
        self.assertNotIn('kind=shape', text)                          # a drift-inbox line

    def test_sections_are_byte_identical_to_the_bundle_copies(self):
        _rc, _o, _e, out_dir = self.digest_to()
        bundle, appendix = self.bundle_of(out_dir), self.appendix_of(out_dir)
        for head, nxt in (('## Chain graph', '## Ledgers'),
                          ('## Gist table', '## Parse health')):
            self.assertIn(bundle.split(head)[1].split(nxt)[0].rstrip(), appendix)


class DigestOutputContract(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers()

    def test_stdout_names_bundle_appendix_and_manifest(self):
        rc, out, err, out_dir = self.digest_to()
        self.assertEqual(rc, 0, err)
        lines = out.strip().split('\n')
        self.assertEqual(lines[0], os.path.join(out_dir, 'bundle.md').replace('\\', '/'))
        self.assertTrue(lines[1].startswith('appendix: '))
        self.assertTrue(lines[2].startswith('manifest: '))
        # Step 5 passes this path to `advance` explicitly rather than trusting newest-manifest.
        self.assertTrue(os.path.isfile(lines[2].split('manifest: ', 1)[1]))

    def test_counts_line_carries_bundle_bytes_for_the_stat_line(self):
        rc, out, err, out_dir = self.digest_to()
        counts = [ln for ln in out.split('\n') if ln.startswith('mode=')][0]
        actual = os.path.getsize(os.path.join(out_dir, 'bundle.md'))
        self.assertIn('bundle=%dB' % actual, counts)

    def test_manifest_records_bundle_bytes_and_appendix(self):
        _rc, _o, _e, out_dir = self.digest_to()
        m = self.manifest_of(out_dir)
        self.assertEqual(m['bundle_bytes'], os.path.getsize(os.path.join(out_dir, 'bundle.md')))
        self.assertTrue(m['appendix'].endswith('appendix.md'))


class OutDirGuard(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers()

    def test_refuses_a_non_empty_dir_with_no_manifest(self):
        out_dir = os.path.join(self.tmp, 'occupied')
        os.makedirs(out_dir)
        write(os.path.join(out_dir, 'someones-notes.md'), 'do not clobber me')
        rc, out, err = self.run_cmd(['digest', '--out', out_dir])
        self.assertEqual(rc, 2)
        self.assertIn('no manifest.json', err)
        self.assertEqual(sweep.read_text(os.path.join(out_dir, 'someones-notes.md')),
                         'do not clobber me')

    def test_refuses_a_dir_holding_a_different_sweep_subject(self):
        out_dir = os.path.join(self.tmp, 'shared')
        rc, _o, err = self.run_cmd(['digest', '--out', out_dir])
        self.assertEqual(rc, 0, err)
        rc, _o, err = self.run_cmd(['digest', '--focus', 'p', '--out', out_dir])
        self.assertEqual(rc, 2)
        self.assertIn('different sweep subject', err)

    def test_re_running_the_same_sweep_into_its_own_dir_is_allowed(self):
        out_dir = os.path.join(self.tmp, 'mine')
        self.assertEqual(self.run_cmd(['digest', '--out', out_dir])[0], 0)
        self.assertEqual(self.run_cmd(['digest', '--out', out_dir])[0], 0)

    def test_refuses_an_out_path_that_is_a_file(self):
        out_path = os.path.join(self.tmp, 'a-file')
        write(out_path, 'x')
        rc, _o, err = self.run_cmd(['digest', '--out', out_path])
        self.assertEqual(rc, 2)
        self.assertIn('not a directory', err)


# ---------------------------------------------------------------------------
# advance — input validation on manifest content
# ---------------------------------------------------------------------------

class AdvanceValidation(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('alpha')
        self.report(self.d, '2026-01-06-x-000000e5.md', V5)
        self.state([self.row('alpha', self.d, 'NONE')])
        self.ledgers()
        self.state_file = os.path.join(self.pm, 'SWEEP-STATE.md')
        _rc, _o, _e, self.out_dir = self.digest_to()
        self.manifest_path = os.path.join(self.out_dir, 'manifest.json')
        self.before = sweep.read_text(self.state_file)

    def rewrite_manifest(self, mutate):
        with open(self.manifest_path, encoding='utf-8') as fh:
            m = json.load(fh)
        mutate(m)
        with open(self.manifest_path, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(m, fh, indent=2, ensure_ascii=False)

    def advance(self):
        return self.run_cmd(['advance', '--manifest', self.manifest_path])

    def test_valid_manifest_advances(self):
        rc, out, err = self.advance()
        self.assertEqual(rc, 0, err)
        _l, entries = sweep.parse_state(sweep.read_text(self.state_file))
        self.assertEqual(entries[0]['watermark'], '2026-01-06-x-000000e5.md')

    def test_basename_carrying_the_registry_separator_is_refused(self):
        # Interpolated unchecked, this would turn one registry row into a four-field line the
        # nudge hook then reads with the wrong watermark.
        self.rewrite_manifest(lambda m: m['projects']['alpha'].__setitem__(
            'enumerated', ['2026-01-06-x-000000e5.md' + SEP + 'last=evil.md']))
        rc, out, err = self.advance()
        self.assertEqual(rc, 2)
        self.assertIn('malformed report basename', err)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_basename_carrying_a_newline_is_refused(self):
        self.rewrite_manifest(lambda m: m['projects']['alpha'].__setitem__(
            'enumerated', ['2026-01-06-x-000000e5.md\nbeta' + SEP + 'x' + SEP + 'last=y.md']))
        rc, out, err = self.advance()
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_basename_not_matching_the_report_shape_is_refused(self):
        self.rewrite_manifest(
            lambda m: m['projects']['alpha'].__setitem__('enumerated', ['notes.txt']))
        rc, out, err = self.advance()
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_one_bad_basename_refuses_the_whole_run(self):
        self.rewrite_manifest(lambda m: m['projects']['alpha'].__setitem__(
            'enumerated', ['2026-01-06-x-000000e5.md', 'nope']))
        self.assertEqual(self.advance()[0], 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_unregistered_project_is_skipped_never_added_as_a_row(self):
        def add_unknown(m):
            m['projects']['ghost'] = {'dir': '/tmp/ghost', 'enumerated': ['2026-01-09-g-0000000f.md'],
                                      'watermark': None}
        self.rewrite_manifest(add_unknown)
        rc, out, err = self.advance()
        self.assertEqual(rc, 0, err)
        self.assertIn("skipped 'ghost'", out)
        _l, entries = sweep.parse_state(sweep.read_text(self.state_file))
        self.assertEqual([e['name'] for e in entries], ['alpha'])
        self.assertNotIn('ghost', sweep.read_text(self.state_file))

    def test_non_dict_manifest_is_refused(self):
        write(self.manifest_path, '["not", "an", "object"]')
        rc, out, err = self.advance()
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_unparseable_manifest_is_refused(self):
        write(self.manifest_path, '{ this is not json')
        self.assertEqual(self.advance()[0], 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_default_resolution_only_looks_under_the_tool_cache(self):
        # A manifest.json sitting elsewhere in the state home must not be picked up by the
        # no-argument form; only <home>/.cache/ballast/sweep/ is searched.
        stray = os.path.join(self.home, 'elsewhere', 'manifest.json')
        os.makedirs(os.path.dirname(stray), exist_ok=True)
        shutil.copy(self.manifest_path, stray)
        rc, out, err = self.run_cmd(['advance'])
        self.assertEqual(rc, 2)
        self.assertEqual(sweep.read_text(self.state_file), self.before)

    def test_valid_watermark_predicate(self):
        self.assertTrue(sweep.valid_watermark('2026-01-06-x-000000e5.md'))
        for bad in ('notes.txt', '2026-01-06-x.md' + SEP + 'y', 'a\nb', '2026-01-06.md', '', None):
            self.assertFalse(sweep.valid_watermark(bad), bad)


# ---------------------------------------------------------------------------
# Citation-hub guard + prose-handle edges + disposition vocabulary
# ---------------------------------------------------------------------------

def hub_report(pair_count):
    """A sweep-style report citing one handle from each of `pair_count` disjoint chains."""
    recs = '\n'.join(
        '- [ ] **`R%d` Refine custom skill** — reconciled. ↳ carried from `aaaa%04d#R1` (2×).'
        % (i, i) for i in range(1, pair_count + 1))
    return ("# Session Post-Mortem — sweep-style reconciliation\n\n"
            "**Schema version:** v3\n**Generated:** 2026-01-20\n\n"
            "## 6. Recommended Actions\n\n" + recs + "\n\n## 7. Session Stats\n")


def pair_ledger(pair_count):
    rows = '\n'.join('| chain %d (`aaaa%04d#R1 → bbbb%04d#R1`) | OPEN | somewhere |'
                     % (i, i, i) for i in range(1, pair_count + 1))
    return "# HARNESS-RECS\n\n| Chain | Disposition | Where |\n|---|---|---|\n" + rows + "\n"


PROSE_SWEEP = """# Session Post-Mortem — prior-sweep accounting only

**Schema version:** v3
**Generated:** 2026-01-21

## 6. Recommended Actions

**Prior-open-recs sweep** (2 priors):

- `cccc0001#R1` → **flipped `[x]`** — superseded by `cccc0002#R1`, shipped that session.

_No new actions recommended this session._

## 7. Session Stats
"""

PARKED_REC = """# Session Post-Mortem — parked chain

**Schema version:** v3
**Generated:** 2026-01-22

## 6. Recommended Actions

- [ ] **`R1` Adjust hook** ↗ **PARKED** — waiting on the owner. ↳ carried from `dddd0001#R1` (3×).

## 7. Session Stats
"""


class CitationHubGuard(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.state([self.row('p', self.d, 'NONE')])

    def graph(self):
        _rc, _o, _e, out_dir = self.digest_to()
        bundle = self.bundle_of(out_dir)
        return bundle.split('## Chain graph')[1].split('## Ledgers')[0], out_dir

    def test_hub_citing_eight_disjoint_chains_does_not_merge_them(self):
        self.report(self.d, '2026-01-20-hub-0000ffff.md', hub_report(8))
        self.ledgers(recs=pair_ledger(8))
        graph, out_dir = self.graph()
        self.assertIn('CITATION-HUB: 0000ffff cites 8 chains', graph)
        # The eight ledger pairs survive as eight separate two-member chains.
        self.assertEqual(graph.count('- chain n=2'), 8)
        self.assertNotIn('- chain n=1', graph)
        for n in range(3, 20):
            self.assertNotIn('- chain n=%d' % n, graph)
        self.assertEqual(self.manifest_of(out_dir)['counts']['citation_hubs'], 1)

    def test_hub_refs_name_the_withheld_chains(self):
        self.report(self.d, '2026-01-20-hub-0000ffff.md', hub_report(8))
        self.ledgers(recs=pair_ledger(8))
        graph, _out = self.graph()
        hub_block = graph.split('CITATION-HUB')[1]
        for i in range(1, 9):
            self.assertIn('aaaa%04d#R1' % i, hub_block)

    def test_below_threshold_still_merges(self):
        # Five cited chains is ordinary carry evidence, not an index — the guard must not fire.
        self.report(self.d, '2026-01-20-hub-0000ffff.md', hub_report(5))
        self.ledgers(recs=pair_ledger(5))
        graph, out_dir = self.graph()
        self.assertNotIn('CITATION-HUB', graph)
        self.assertIn('- chain n=', graph)
        self.assertEqual(self.manifest_of(out_dir)['counts']['citation_hubs'], 0)

    def test_many_citations_into_one_chain_is_not_a_hub(self):
        # Eight edges, but all into a single component: edge count alone must not trip it.
        self.report(self.d, '2026-01-20-hub-0000ffff.md', hub_report(8))
        one_chain = "# HARNESS-RECS\n\n| Chain | D | W |\n|---|---|---|\n| all one (`%s`) | OPEN | x |\n" \
            % ' → '.join('aaaa%04d#R1' % i for i in range(1, 9))
        self.ledgers(recs=one_chain)
        graph, _out = self.graph()
        self.assertNotIn('CITATION-HUB', graph)

    def test_threshold_is_a_named_constant(self):
        self.assertEqual(sweep.CITATION_HUB_THRESHOLD, 6)


class ProseHandleEdges(Base):

    def setUp(self):
        super().setUp()
        self.d = self.project('p')
        self.report(self.d, '2026-01-21-prose-0000aaaa.md', PROSE_SWEEP)
        self.state([self.row('p', self.d, 'NONE')])
        self.ledgers()

    def test_handles_in_an_unparsed_section_still_become_chain_edges(self):
        _rc, _o, _e, out_dir = self.digest_to()
        bundle = self.bundle_of(out_dir)
        graph = bundle.split('## Chain graph')[1].split('## Ledgers')[0]
        # No rec parsed and no ledger row exists — this edge can only come from prose scanning.
        self.assertIn('cccc0001#R1', graph)
        self.assertIn('cccc0002#R1', graph)
        self.assertIn('- chain n=2', graph)

    def test_the_report_is_still_reported_as_unparsed(self):
        _rc, _o, _e, out_dir = self.digest_to()
        self.assertIn('top-level list items', self.bundle_of(out_dir))

    def test_handles_are_grouped_by_line_not_by_section(self):
        # Two bullets naming unrelated chains must not merge just by sharing a section.
        two = PROSE_SWEEP.replace(
            '_No new actions recommended this session._',
            '- `eeee0001#R1` → **flipped `[x]`** — closed by `eeee0002#R1`.')
        d2 = self.project('q')
        self.report(d2, '2026-01-21-prose-0000bbbb.md', two)
        self.state([self.row('q', d2, 'NONE')])
        _rc, _o, _e, out_dir = self.digest_to()
        graph = self.bundle_of(out_dir).split('## Chain graph')[1].split('## Ledgers')[0]
        self.assertEqual(graph.count('- chain n=2'), 2)


class DispositionVocabulary(Base):

    def test_parked_is_in_the_single_source_of_truth(self):
        self.assertIn('PARKED', sweep.DISPOSITION_TOKENS)

    def test_disposition_flags_reads_the_vocabulary(self):
        self.assertEqual(sweep.disposition_flags('↗ PARKED — waiting'), ['PARKED'])
        self.assertEqual(sweep.disposition_flags('🔴 ESCALATED … ↗ SUPERSEDED by x'),
                         ['ESCALATED', 'SUPERSEDED'])
        self.assertEqual(sweep.disposition_flags('nothing here'), [])

    def test_parked_rec_renders_a_flag_in_the_chain_graph(self):
        d = self.project('p')
        self.report(d, '2026-01-22-parked-0000cccc.md', PARKED_REC)
        self.state([self.row('p', d, 'NONE')])
        self.ledgers()
        _rc, _o, _e, out_dir = self.digest_to()
        graph = self.bundle_of(out_dir).split('## Chain graph')[1].split('## Ledgers')[0]
        self.assertIn('[PARKED]', graph)

    def test_ledger_row_disposition_also_flags_the_chain(self):
        d = self.project('p')
        self.report(d, '2026-01-22-parked-0000cccc.md',
                    PARKED_REC.replace('↗ **PARKED** — waiting on the owner. ', ''))
        self.state([self.row('p', d, 'NONE')])
        self.ledgers(recs="# HARNESS-RECS\n\n| Chain | D | W |\n|---|---|---|\n"
                          "| the thing (`dddd0001#R1`) | PARKED — user-held | nowhere |\n")
        _rc, _o, _e, out_dir = self.digest_to()
        graph = self.bundle_of(out_dir).split('## Chain graph')[1].split('## Ledgers')[0]
        self.assertIn('[PARKED]', graph)


# ---------------------------------------------------------------------------
# Focus-path attribution + v5 section census
# ---------------------------------------------------------------------------

class FocusPathAttribution(Base):

    def test_a_sibling_sharing_a_name_prefix_cannot_claim_the_report(self):
        short = os.path.join(self.tmp, 'app', '.claude', 'postmortem')
        long_ = os.path.join(self.tmp, 'app-legacy', '.claude', 'postmortem')
        os.makedirs(short)
        os.makedirs(long_)
        write(os.path.join(short, '2026-01-04-x-000000c3.md'), V2)
        target = os.path.join(long_, '2026-01-06-x-000000e5.md')
        write(target, V5)
        # `app` is listed FIRST, so a bare prefix match would win before `applegacy` is reached.
        self.state([self.row('app', short, 'NONE'), self.row('applegacy', long_, 'NONE')])
        self.ledgers()
        rc, out, err, out_dir = self.digest_to(['--focus', target])
        self.assertEqual(rc, 0, err)
        self.assertEqual(list(self.manifest_of(out_dir)['projects']), ['applegacy'])

    def test_a_report_outside_every_registered_dir_is_ad_hoc(self):
        d = self.project('p')
        self.state([self.row('p', d, 'NONE')])
        self.ledgers()
        outside = os.path.join(self.tmp, 'loose', '2026-01-06-x-000000e5.md')
        write(outside, V5)
        _rc, _o, _e, out_dir = self.digest_to(['--focus', outside])
        self.assertEqual(list(self.manifest_of(out_dir)['projects']), ['ad-hoc'])


class V5SectionCensus(Base):

    def test_missing_canonical_section_is_an_anomaly_not_an_unparsed(self):
        d = self.project('p')
        trimmed = V5.replace('## Inventory\n| Kind | Fired | Notes |\n|---|---|---|\n'
                             '| Skills | none | — |\n\n', '')
        self.assertNotIn('## Inventory', trimmed)
        rep = sweep.parse_report(self.report(d, '2026-01-06-x-000000e5.md', trimmed), 'p')
        self.assertEqual(rep['unparsed'], [])
        self.assertIn('missing canonical section(s): inventory', ' '.join(rep['anomalies']))

    def test_complete_v5_report_has_no_section_anomaly(self):
        d = self.project('p')
        rep = sweep.parse_report(self.report(d, '2026-01-06-x-000000e5.md', V5), 'p')
        self.assertNotIn('missing canonical section', ' '.join(rep['anomalies']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
