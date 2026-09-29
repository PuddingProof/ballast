#!/usr/bin/env python3
"""Regression tests for statusline/render.py -- the ballast statusLine row renderer.

Every scenario drives render.py end-to-end via subprocess (matching the convention used by
test_commit_review_gate.py / test_ballast_allow.py): real stdin JSON in, real stdout captured,
so the test exercises the ACTUAL fail-quiet contract (exit code, exact bytes on stdout) rather
than internal function calls that could pass while the process-level contract silently breaks.

Hermetic by construction: every test points BALLAST_CLAUDE_HOME at a throwaway temp dir (see
render.py's `_claude_home()`), so nothing here ever reads or writes the developer's real
~/.claude/ballast/modes/ state.

Runs with plain stdlib unittest (no pytest dependency), per hooks/CLAUDE.md's test-pairing
convention, so dev/check.sh can invoke it with the same resolved interpreter used elsewhere.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RENDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render.py")

# ANSI SGR fragments, kept identical to render.py's own building blocks so assertions check
# the REAL escape sequences rather than an approximation of them.
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"


def fg(rgb):
    r, g, b = rgb
    return "\x1b[38;2;%d;%d;%dm" % (r, g, b)


GRAY = (135, 135, 135)
WHITE = (235, 235, 235)
RED = (255, 95, 95)
STEP_500K = (202, 113, 113)
STEP_200K = (162, 126, 126)
UNKNOWN_GRAY = (200, 200, 200)
FREEHAND_RGB = (95, 215, 255)
EXEC_RGB = (255, 150, 50)

# Emoji literals -- verified codepoint-for-codepoint against render.py's MODE_STYLES table
# (U+2708 AIRPLANE + U+FE0F for freehand/autopilot; U+1F4CB CLIPBOARD for exec) so a drifted
# emoji in either file fails a test instead of silently diverging.
FREEHAND_EMOJI = "\u2708\uFE0F"
EXEC_EMOJI = "\U0001F4CB"

# Display LABELS, pinned separately from the mode keys they're registered under -- `autopilot`
# renders under freehand's label and `exec` under the user-facing protocol name, so a label
# change has to be a deliberate test edit rather than a silent drift.
FREEHAND_LABEL = "freehand"
EXEC_LABEL = "plan-handoff"


class RenderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.state_dir = self.home / "ballast" / "modes"
        self.env = dict(os.environ)
        self.env["BALLAST_CLAUDE_HOME"] = str(self.home)

    def write_state(self, session_id, lines):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines) + ("\n" if lines else "")
        (self.state_dir / session_id).write_text(text, encoding="utf-8")

    def run_render(self, stdin_text, env=None):
        return subprocess.run(
            [sys.executable, RENDER],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env if env is not None else self.env,
        )

    def payload(self, session_id="sess-1", project_dir="/home/user/ballast", pct=None, cwd=None,
                tokens=0):
        """`pct` is the DISPLAYED value; `tokens` drives the COLOR -- independent inputs by
        design (see render.py's CTX_TOKEN_THRESHOLDS). tokens defaults to 0 so a test that only
        cares about the displayed percentage gets the gray/no-warning color."""
        d = {"session_id": session_id, "workspace": {"project_dir": project_dir}}
        if cwd is not None:
            d["cwd"] = cwd
        if pct is not None:
            d["context_window"] = {"used_percentage": pct, "total_input_tokens": tokens}
        return json.dumps(d)

    # -----------------------------------------------------------------------------------
    # Idle baseline
    # -----------------------------------------------------------------------------------

    def test_idle_baseline_no_state_file(self):
        """No chip state at all -> just the dir + ctx% baseline, single line."""
        proc = self.run_render(self.payload(pct=42))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.count("\n"), 1)
        line = proc.stdout.rstrip("\n")
        self.assertIn("ballast", line)
        self.assertIn("42% context", line)
        self.assertIn(fg(GRAY), line)
        self.assertNotIn(fg(RED), line)
        self.assertNotIn("▸", line)  # no chips -> no ' ▸ ' separator at all

    def test_missing_context_window_dir_only(self):
        """Dir-only payload -> the name in explicit WHITE (promoted above the gray peers;
        unstyled text gets re-muted by the TUI, so the color must be explicit)."""
        payload = json.dumps({"session_id": "sess-x", "workspace": {"project_dir": "/a/b/ballast"}})
        proc = self.run_render(payload)
        self.assertEqual(proc.returncode, 0)
        line = proc.stdout.rstrip("\n")
        self.assertEqual(line, fg(WHITE) + "ballast" + RESET)

    def test_cwd_fallback_when_no_project_dir(self):
        payload = json.dumps({"session_id": "sess-y", "cwd": "/some/path/myproj"})
        proc = self.run_render(payload)
        line = proc.stdout.rstrip("\n")
        self.assertIn("myproj", line)

    def test_float_percentage_rendered_as_integer(self):
        proc = self.run_render(self.payload(pct=42.9))
        line = proc.stdout.rstrip("\n")
        # "render as integer percent" -- no fractional digits should ever reach the row.
        self.assertNotIn(".", line.split(" · ", 1)[1].split("%", 1)[0])

    # -----------------------------------------------------------------------------------
    # ctx color thresholds: keyed on ABSOLUTE TOKENS, not the displayed percentage.
    # -----------------------------------------------------------------------------------

    def test_ctx_gray_below_200k(self):
        line = self.run_render(self.payload(pct=18, tokens=199_999)).stdout.rstrip("\n")
        self.assertIn("18% context", line)
        self.assertIn(fg(GRAY), line)
        self.assertNotIn(fg(STEP_200K), line)

    def test_ctx_step_at_200k(self):
        """The long-context pricing cliff -- the threshold the whole scheme exists for."""
        line = self.run_render(self.payload(pct=20, tokens=200_000)).stdout.rstrip("\n")
        self.assertIn(fg(STEP_200K), line)

    def test_ctx_step_at_500k(self):
        line = self.run_render(self.payload(pct=50, tokens=500_000)).stdout.rstrip("\n")
        self.assertIn(fg(STEP_500K), line)

    def test_ctx_red_at_800k(self):
        line = self.run_render(self.payload(pct=80, tokens=800_000)).stdout.rstrip("\n")
        self.assertIn(fg(RED), line)

    def test_ctx_color_ignores_percentage(self):
        """A high PERCENTAGE on a small window must not warn: 100% of a 200k window is still only
        200k tokens. Pins that the color reads tokens and never the percentage -- the coupling
        that made a percentage threshold silently model-dependent."""
        line = self.run_render(self.payload(pct=99, tokens=150_000)).stdout.rstrip("\n")
        self.assertIn("99% context", line)
        self.assertNotIn(fg(RED), line)
        self.assertNotIn(fg(STEP_200K), line)
        self.assertNotIn(fg(STEP_500K), line)

    def test_ctx_tokens_derived_when_count_absent(self):
        """No total_input_tokens -> reconstruct from percentage x window size, so the color still
        tracks tokens instead of silently degrading to no warning at all."""
        payload = json.dumps({"session_id": "sess-derived", "workspace": {"project_dir": "/a/b"},
                              "context_window": {"used_percentage": 60,
                                                 "context_window_size": 1_000_000}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn(fg(STEP_500K), line)  # 60% of 1M = 600k

    def test_ctx_unknown_tokens_render_gray(self):
        """Neither a count nor a derivable pair -> gray. A wrong warning is worse than none."""
        payload = json.dumps({"session_id": "sess-notok", "workspace": {"project_dir": "/a/b"},
                              "context_window": {"used_percentage": 95}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn(fg(GRAY), line)
        self.assertNotIn(fg(RED), line)

    # -----------------------------------------------------------------------------------
    # model + effort segment (session identity, between dir and ctx%)
    # -----------------------------------------------------------------------------------

    def test_model_and_effort_between_dir_and_ctx(self):
        payload = json.dumps({"session_id": "s", "workspace": {"project_dir": "/a/ballast"},
                              "model": {"display_name": "Opus 5"}, "effort": {"level": "high"},
                              "context_window": {"used_percentage": 22, "total_input_tokens": 1}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn("ballast", line)
        self.assertIn("Opus 5 (high)", line)  # effort parenthesized ONTO the name, not a peer
        self.assertLess(line.index("ballast"), line.index("Opus 5"))
        self.assertLess(line.index("Opus 5"), line.index("22% context"))

    def test_model_without_effort_omits_the_segment(self):
        """`effort` is absent on models that lack reasoning effort -- render the name alone
        rather than a placeholder standing in for a real absence."""
        payload = json.dumps({"session_id": "s", "workspace": {"project_dir": "/a/ballast"},
                              "model": {"display_name": "Opus 5"}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn("Opus 5", line)
        self.assertTrue(line.endswith("Opus 5" + RESET), line)

    def test_display_name_is_never_transformed(self):
        """Verbatim, whatever its shape -- the vendor-prefixed generation renders as-is. Pins
        that no prefix-strip or space-removal ever creeps in."""
        payload = json.dumps({"session_id": "s", "workspace": {"project_dir": "/a/b"},
                              "model": {"display_name": "Claude 3.5 Sonnet"}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn("Claude 3.5 Sonnet", line)

    def test_overlong_model_name_truncates(self):
        """An unknown-shape long name clips predictably instead of blowing out the row."""
        payload = json.dumps({"session_id": "s", "workspace": {"project_dir": "/a/b"},
                              "model": {"display_name": "A" * 60}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertNotIn("A" * 60, line)
        self.assertIn("…", line)

    def test_missing_model_segment_absent_entirely(self):
        """No model key -> no empty segment and no doubled separator."""
        payload = json.dumps({"session_id": "s", "workspace": {"project_dir": "/a/ballast"},
                              "context_window": {"used_percentage": 5, "total_input_tokens": 1}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertNotIn(" ·  · ", line)
        self.assertEqual(line.count(" · "), 1)

    # -----------------------------------------------------------------------------------
    # Chips: pending / confirmed / multi / unknown-mode fallback
    # -----------------------------------------------------------------------------------

    def test_pending_chip_dim(self):
        sid = "sess-pending"
        now = int(time.time())
        self.write_state(sid, ["freehand pending %d" % now])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        expected_chip = DIM + fg(FREEHAND_RGB) + "⋯ " + FREEHAND_LABEL + "?" + RESET
        self.assertIn(expected_chip, line)
        self.assertIn(" ▸ ", line)
        # Chips render to the RIGHT of the baseline, past the ▸ boundary.
        self.assertGreater(line.index(expected_chip), line.index(" ▸ "))
        # Pending render must NOT include the solid emoji/bold-label form.
        self.assertNotIn(FREEHAND_EMOJI + " " + BOLD, line)

    def test_confirmed_chip_solid(self):
        sid = "sess-confirmed"
        now = int(time.time())
        self.write_state(sid, ["exec confirmed %d" % now])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        expected_chip = EXEC_EMOJI + " " + BOLD + fg(EXEC_RGB) + EXEC_LABEL + RESET
        self.assertIn(expected_chip, line)
        self.assertIn(" ▸ ", line)

    def test_multi_chip_ordering_and_separator(self):
        """Display order is MODE_STYLES declaration order, NOT state-file arrival order -- so
        this writes exec FIRST and still expects freehand to render first. Arrival order would
        make a chip's slot depend on session history; declaration order makes the layout
        identical everywhere."""
        sid = "sess-multi"
        now = int(time.time())
        self.write_state(sid, [
            "exec pending %d" % now,
            "freehand confirmed %d" % now,
        ])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        freehand_chip = FREEHAND_EMOJI + " " + BOLD + fg(FREEHAND_RGB) + FREEHAND_LABEL + RESET
        exec_chip = DIM + fg(EXEC_RGB) + "⋯ " + EXEC_LABEL + "?" + RESET
        self.assertIn(" ▸ " + freehand_chip + " · " + exec_chip, line)
        self.assertTrue(line.endswith(exec_chip))  # chips are the tail of the row now

    def test_unknown_modes_sort_after_registered_ones(self):
        """An unregistered mode has no declared rank, so it trails every registered chip
        regardless of where it sits in the state file."""
        sid = "sess-order-unknown"
        now = int(time.time())
        self.write_state(sid, [
            "zzz-unknown confirmed %d" % now,
            "exec confirmed %d" % now,
        ])
        line = self.run_render(self.payload(session_id=sid, pct=10)).stdout.rstrip("\n")
        self.assertLess(line.index(EXEC_LABEL), line.index("zzz-unknown"))

    def test_shared_label_renders_one_chip(self):
        """Both autonomy keys armed (two keywords, neither cleared) must still render ONE chip --
        they name the same mode, and two identical chips read as a rendering bug."""
        sid = "sess-both-autonomy"
        now = int(time.time())
        self.write_state(sid, [
            "autopilot confirmed %d" % now,
            "freehand confirmed %d" % now,
        ])
        line = self.run_render(self.payload(session_id=sid, pct=10)).stdout.rstrip("\n")
        self.assertEqual(line.count(FREEHAND_LABEL), 1)
        self.assertNotIn(" · ", line.split(" ▸ ", 1)[1])  # a single chip -> no chip separator

    def test_shared_label_confirmed_outranks_pending(self):
        """The two autonomy keys can hold DIFFERENT statuses: a mere-mention re-arm raises a
        PENDING chip under whichever keyword the prompt used, which need not be the key the grant
        was confirmed under. The one surviving chip must show the CONFIRMED grant -- collapsing to
        the pending form would visibly demote a standing grant until its TTL reaped it."""
        sid = "sess-mixed-autonomy"
        now = int(time.time())
        self.write_state(sid, [
            "autopilot confirmed %d" % now,
            "freehand pending %d" % now,   # mention-arm on the other key, not yet settled
        ])
        line = self.run_render(self.payload(session_id=sid, pct=10)).stdout.rstrip("\n")
        solid = FREEHAND_EMOJI + " " + BOLD + fg(FREEHAND_RGB) + FREEHAND_LABEL + RESET
        self.assertIn(solid, line)
        self.assertNotIn("⋯", line)  # no pending form anywhere on the row
        self.assertEqual(line.count(FREEHAND_LABEL), 1)

    def test_chips_without_baseline_have_no_dangling_separator(self):
        """Degenerate payload -- no project dir AND no context % -- must render the chips alone.
        The ▸ is a BOUNDARY between baseline and chips; with no baseline there is nothing to
        bound, and a leading ' ▸ ' would read as a rendering glitch."""
        sid = "sess-no-baseline"
        now = int(time.time())
        self.write_state(sid, ["freehand confirmed %d" % now])
        payload = json.dumps({"session_id": sid, "workspace": {"project_dir": ""}})
        line = self.run_render(payload).stdout.rstrip("\n")
        self.assertIn(FREEHAND_LABEL, line)
        self.assertNotIn("▸", line)

    def test_autopilot_renders_under_the_freehand_label(self):
        """`autopilot` and `freehand` are two state keys for ONE mode, so the chip must read
        "freehand" whichever keyword armed it -- the row answers "which mode is on", not "which
        synonym turned it on". Asserted on both statuses, since each has its own render path."""
        sid = "sess-autopilot"
        now = int(time.time())
        self.write_state(sid, ["autopilot confirmed %d" % now])
        line = self.run_render(self.payload(session_id=sid, pct=10)).stdout.rstrip("\n")
        self.assertIn(FREEHAND_EMOJI + " " + BOLD + fg(FREEHAND_RGB) + FREEHAND_LABEL + RESET, line)
        self.assertNotIn("autopilot", line)

        self.write_state(sid, ["autopilot pending %d" % now])
        line = self.run_render(self.payload(session_id=sid, pct=10)).stdout.rstrip("\n")
        self.assertIn(DIM + fg(FREEHAND_RGB) + "⋯ " + FREEHAND_LABEL + "?" + RESET, line)
        self.assertNotIn("autopilot", line)

    def test_unknown_mode_generic_fallback(self):
        sid = "sess-unknown"
        now = int(time.time())
        self.write_state(sid, ["mystery confirmed %d" % now])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        expected_chip = "● " + BOLD + fg(UNKNOWN_GRAY) + "mystery" + RESET
        self.assertIn(expected_chip, line)

    def test_unknown_mode_pending_still_renders(self):
        """The registry must be general: an unrecognized mode still renders (dimmed,
        generic-gray) instead of being silently dropped."""
        sid = "sess-unknown-pending"
        now = int(time.time())
        self.write_state(sid, ["newthing pending %d" % now])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        expected_chip = DIM + fg(UNKNOWN_GRAY) + "⋯ newthing?" + RESET
        self.assertIn(expected_chip, line)

    # -----------------------------------------------------------------------------------
    # TTL staleness: pending > 45 min dropped, confirmed > 24 h dropped, fresh kept.
    # -----------------------------------------------------------------------------------

    def test_ttl_stale_pending_dropped(self):
        sid = "sess-stale-pending"
        old = int(time.time()) - (46 * 60)
        self.write_state(sid, ["freehand pending %d" % old])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        self.assertNotIn("freehand", line)
        self.assertNotIn(" ▸ ", line)

    def test_ttl_fresh_pending_kept(self):
        sid = "sess-fresh-pending"
        recent = int(time.time()) - (44 * 60)
        self.write_state(sid, ["freehand pending %d" % recent])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        self.assertIn("freehand?", line)

    def test_ttl_stale_confirmed_dropped(self):
        sid = "sess-stale-confirmed"
        old = int(time.time()) - (25 * 3600)
        self.write_state(sid, ["exec confirmed %d" % old])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        self.assertNotIn(EXEC_LABEL, line)
        self.assertNotIn(" ▸ ", line)

    def test_ttl_fresh_confirmed_kept(self):
        sid = "sess-fresh-confirmed"
        recent = int(time.time()) - (23 * 3600)
        self.write_state(sid, ["exec confirmed %d" % recent])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        self.assertIn(EXEC_LABEL, line)

    # -----------------------------------------------------------------------------------
    # Corrupt / malformed state and stdin -- fail-quiet contract.
    # -----------------------------------------------------------------------------------

    def test_corrupt_state_file_baseline_only(self):
        sid = "sess-corrupt"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Invalid UTF-8 bytes -- a genuinely corrupt file, not just a malformed line.
        (self.state_dir / sid).write_bytes(b"\xff\xfe not valid utf-8 at all\n")
        proc = self.run_render(self.payload(session_id=sid, pct=42))
        self.assertEqual(proc.returncode, 0)
        line = proc.stdout.rstrip("\n")
        self.assertNotIn(" ▸ ", line)
        self.assertIn("42% context", line)

    def test_malformed_lines_skipped_silently(self):
        sid = "sess-malformed"
        now = int(time.time())
        self.write_state(sid, [
            "freehand",                        # too few fields
            "freehand notastatus %d" % now,     # bad status
            "freehand pending notanepoch",      # bad epoch
            "exec confirmed %d" % now,          # the one good line
        ])
        proc = self.run_render(self.payload(session_id=sid, pct=10))
        line = proc.stdout.rstrip("\n")
        self.assertIn(EXEC_LABEL, line)
        self.assertNotIn("freehand", line)

    def test_missing_state_file_baseline_only(self):
        proc = self.run_render(self.payload(session_id="sess-nofile", pct=5))
        line = proc.stdout.rstrip("\n")
        self.assertNotIn(" ▸ ", line)

    def test_corrupt_stdin_empty_output(self):
        proc = self.run_render("{not valid json at all")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_empty_stdin_empty_output(self):
        proc = self.run_render("")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_json_scalar_not_object_empty_output(self):
        proc = self.run_render("42")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    # -----------------------------------------------------------------------------------
    # Single-line output guarantee.
    # -----------------------------------------------------------------------------------

    def test_output_is_single_line(self):
        sid = "sess-single-line"
        now = int(time.time())
        self.write_state(sid, [
            "freehand confirmed %d" % now,
            "exec pending %d" % now,
        ])
        proc = self.run_render(self.payload(session_id=sid, pct=95))
        self.assertEqual(proc.stdout.count("\n"), 1)
        self.assertTrue(proc.stdout.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
