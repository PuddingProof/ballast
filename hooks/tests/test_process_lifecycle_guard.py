#!/usr/bin/env python
"""Regression tests for process_lifecycle_guard.py, run through the shell-guards.py dispatcher.

Each case invokes `hooks/shell-guards.py` as a subprocess with the current interpreter, feeds a
PreToolUse JSON payload on stdin, and asserts on the merged decision (a lifecycle-only fire is
byte-identical to the old standalone hook's output).

Two corpora carry the weight, and neither is decoration:

  - BYPASS MATRIX — the improvisation set a denied leaf reaches for next (a different runner,
    a foreground variant, a wrapper, a background flag), across both shells, including the
    detachment case that lives ONLY in `tool_input.run_in_background` with an innocent command
    string. A guard whose deny surface is semantic is only as good as this list; new shapes get
    pinned here first.
  - FALSE-POSITIVE CORPUS — this is the compensating control for shipping a hard deny with no
    shadow window, so non-frontend rows are FIRST-CLASS: a backend test leaf, a CLI-project
    leaf, search commands that merely mention the verbs, foreground `vitest` / `playwright
    test`, and `npm start`. Every row must stay silent.

`npm start` disposition (pinned deliberately, not by omission): NOT denied. `start` is
overloaded — dev server, plain entry point, build/test alias — and the design spec places it in
the false-positive corpus, so the guard trades that coverage for corpus cleanliness. See the
hook header's residual 1.

HERMETIC (hooks/CLAUDE.md rule): every invocation sets BALLAST_CLAUDE_HOME to a temp dir, so the
dispatcher's ballast_allow opt-in marker is never read from the real ~/.claude. Production never
sets that var.

Self-locating + standalone: `python hooks/tests/test_process_lifecycle_guard.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shell-guards.py")

TRANSCRIPT = "/home/u/.claude/projects/proj/abc-123.jsonl"
LEAF = {"agent_type": "some-agent", "agent_id": "ag_1"}

# The deny reason's leaf-facing half, pinned verbatim: it is the whole escape hatch.
LEAF_ESCAPE = (
    "Sub-agents never start, background, or signal a process — the orchestrator owns lifecycle. "
    "Do not retry or route around this (another runner, a foreground variant, a wrapper, a "
    "background flag): use the origin or service you were handed, or return `blocked` / a named "
    "coverage gap naming what is missing and finish with what exists."
)


class GuardCase(unittest.TestCase):
    """Base: a temp BALLAST_CLAUDE_HOME per test, plus the run/assert helpers."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ballast-lifecycle-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def run_hook(self, command="", tool="Bash", background=None, caller=LEAF,
                 transcript=TRANSCRIPT, raw=None):
        tool_input = {"command": command}
        if background is not None:
            tool_input["run_in_background"] = background
        payload = dict({
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "transcript_path": transcript,
            "session_id": "sess-1",
        }, **(caller or {}))
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        return subprocess.run(
            [sys.executable, HOOK],
            input=raw if raw is not None else json.dumps(payload),
            capture_output=True, text=True, env=env,
        )

    def out(self, proc):
        """Parsed stdout JSON, or None when the hook stayed silent."""
        self.assertEqual(proc.returncode, 0, "guard must always exit 0: %s" % proc.stderr)
        s = proc.stdout.strip()
        return json.loads(s) if s else None

    def decision(self, proc):
        o = self.out(proc)
        return None if o is None else o.get("hookSpecificOutput", {}).get("permissionDecision")

    def assertDenied(self, **kw):
        proc = self.run_hook(**kw)
        self.assertEqual(self.decision(proc), "deny",
                         "expected deny for %r, got %r" % (kw, proc.stdout))

    def assertSilent(self, **kw):
        proc = self.run_hook(**kw)
        self.assertIsNone(self.out(proc),
                          "expected silence for %r, got %r" % (kw, proc.stdout))


class BypassMatrixDetachment(GuardCase):
    """(a) Detachment — the orphan class."""

    def test_run_in_background_flag_alone(self):
        # The load-bearing row: detachment lives ONLY in the tool payload, command string is
        # innocent. A command-text-only guard misses every one of these.
        self.assertDenied(command="ls -la", background=True)

    def test_run_in_background_flag_powershell(self):
        self.assertDenied(command="Get-ChildItem", tool="PowerShell", background=True)

    def test_run_in_background_string_true(self):
        self.assertDenied(command="echo hi", background="true")

    def test_trailing_ampersand(self):
        self.assertDenied(command="npm run dev &")

    def test_ampersand_on_innocent_command(self):
        self.assertDenied(command="./mytool --loop &")

    def test_nohup(self):
        self.assertDenied(command="nohup python app.py &")

    def test_nohup_without_ampersand(self):
        self.assertDenied(command="nohup ./run-service.sh")

    def test_windows_start(self):
        self.assertDenied(command='start "" npm run dev')

    def test_start_job(self):
        self.assertDenied(command="Start-Job -ScriptBlock { npm run dev }", tool="PowerShell")

    def test_start_process(self):
        self.assertDenied(command='Start-Process -FilePath node -ArgumentList "server.js"',
                          tool="PowerShell")

    def test_start_process_assigned_to_variable(self):
        self.assertDenied(command="$p = Start-Process node -NoNewWindow", tool="PowerShell")

    def test_shell_wrapper_does_not_launder_the_verb(self):
        # Quoted bodies are stripped, so a wrapper's payload has to be unwrapped first or the
        # verb disappears. (Residual 2 names what this does NOT close.)
        self.assertDenied(command='bash -c "npm run dev"')

    def test_powershell_command_wrapper(self):
        self.assertDenied(command='powershell -NoProfile -Command "Start-Process node"',
                          tool="PowerShell")


class BypassMatrixLaunch(GuardCase):
    """(b) Standing-origin launches — FOREGROUND INCLUDED."""

    def test_npm_run_dev(self):
        self.assertDenied(command="npm run dev")

    def test_pnpm_dev(self):
        self.assertDenied(command="pnpm dev")

    def test_pnpm_filtered_dev(self):
        self.assertDenied(command="pnpm --filter web dev")

    def test_yarn_dev(self):
        self.assertDenied(command="yarn dev")

    def test_bun_dev(self):
        self.assertDenied(command="bun run dev")

    def test_npm_run_serve(self):
        self.assertDenied(command="npm run serve")

    def test_vite(self):
        self.assertDenied(command="npx vite --port 5173")

    def test_next_dev(self):
        self.assertDenied(command="npx next dev")

    def test_ng_serve(self):
        self.assertDenied(command="ng serve --port 4200")

    def test_http_server(self):
        self.assertDenied(command="http-server ./dist -p 8080")

    def test_serve_package(self):
        self.assertDenied(command="npx serve dist")

    def test_python_http_server(self):
        self.assertDenied(command="python -m http.server 8000")

    def test_python3_http_server(self):
        self.assertDenied(command="python3 -m http.server")

    def test_uvicorn(self):
        # Non-frontend standing service: domain neutrality cuts both ways.
        self.assertDenied(command="uvicorn app.main:app --port 8000")

    def test_php_builtin_server(self):
        self.assertDenied(command="php -S localhost:8000 -t public")

    def test_docker_compose_up(self):
        self.assertDenied(command="docker compose up")

    def test_docker_compose_hyphen_detached(self):
        self.assertDenied(command="docker-compose up -d")

    def test_make_dev(self):
        self.assertDenied(command="make dev")

    def test_hand_rolled_node_server(self):
        self.assertDenied(command="node scripts/dev-server.js")

    def test_env_prefixed_launch(self):
        # The env-prefix hole a prefix matcher misses (install-guard precedent).
        self.assertDenied(command="PORT=3000 npm run dev")

    def test_launch_chained_after_a_search(self):
        # A read-only head neutralizes its OWN segment only — never the chain after it.
        self.assertDenied(command='grep -r "dev" package.json && npm run dev')

    def test_probe_serve_lifecycle(self):
        self.assertDenied(command="node skills/visual-probe/scripts/probe.mjs serve start ./dist")

    def test_probe_session_lifecycle(self):
        self.assertDenied(command="node /plugin/scripts/probe.mjs session stop")


class BypassMatrixSignalling(GuardCase):
    """(c) All process signalling — no own-child carve-out."""

    def test_kill(self):
        self.assertDenied(command="kill -9 12345")

    def test_pkill(self):
        self.assertDenied(command="pkill -f vite")

    def test_killall(self):
        self.assertDenied(command="killall node")

    def test_taskkill(self):
        self.assertDenied(command="taskkill /F /IM node.exe")

    def test_taskkill_absolute_path(self):
        self.assertDenied(command="C:\\Windows\\System32\\taskkill.exe /F /PID 4242")

    # Shell grouping punctuation is not a segment separator, so the head token carries it. Bare
    # `kill`/`nohup`/`start` are absent from the anywhere-regexes by design, leaving the head
    # comparison as the only match — strip the punctuation or the wrapper launders the verb.
    def test_kill_in_subshell(self):
        self.assertDenied(command="(kill -9 12345)")

    def test_pkill_in_brace_group(self):
        self.assertDenied(command="{ pkill -f vite; }")

    def test_taskkill_in_spaced_subshell(self):
        self.assertDenied(command="( taskkill /F /IM node.exe )")

    def test_stop_process(self):
        self.assertDenied(command="Stop-Process -Name node -Force", tool="PowerShell")

    def test_stop_process_piped(self):
        self.assertDenied(command="Get-Process node | Stop-Process -Force", tool="PowerShell")

    def test_kill_port_improvisation(self):
        self.assertDenied(command="npx kill-port 5173")

    def test_docker_stop(self):
        self.assertDenied(command="docker stop web")

    def test_xargs_kill_pipeline(self):
        # The shape a leaf reaches for once a pipeline has found the PID.
        self.assertDenied(command="ps aux | grep vite | awk '{print $2}' | xargs kill -9")

    def test_find_exec_kill(self):
        self.assertDenied(command="find /proc -name cmdline -exec kill {} \\;")

    def test_kill_own_child_still_denied(self):
        # Explicitly pinned: there is NO own-child carve-out, by design. If this ever starts
        # passing, someone added one — read the hook header before "fixing" the test.
        self.assertDenied(command="kill $!")


class FalsePositiveCorpus(GuardCase):
    """Must stay SILENT. The compensating control for a hard deny with no shadow window —
    non-frontend rows are first-class here, not an afterthought."""

    # --- backend / CLI-project leaves (no frontend anywhere in the project) ---------------
    def test_pytest(self):
        self.assertSilent(command="pytest -q tests/")

    def test_python_m_pytest(self):
        self.assertSilent(command="python -m pytest tests/")

    def test_cargo_test(self):
        self.assertSilent(command="cargo test --all")

    def test_cargo_run_cli(self):
        self.assertSilent(command="cargo run -- --port 8080 --dry-run")

    def test_go_build(self):
        self.assertSilent(command="go build ./...")

    def test_dotnet_build(self):
        self.assertSilent(command="dotnet build -c Release")

    def test_cli_tool_with_serve_flag(self):
        self.assertSilent(command="./target/debug/mytool --serve-config ./cfg.toml")

    def test_repo_check_script(self):
        self.assertSilent(command="bash dev/check.sh")

    def test_powershell_call_operator(self):
        # PowerShell's call operator (the documented way to invoke a quoted path) runs
        # FOREGROUND: a leading `&` is not detachment — only a command-TERMINATING `&` is.
        self.assertSilent(command='& "C:\\Program Files\\App\\app.exe" --input data.json',
                          tool="PowerShell")

    def test_powershell_call_operator_after_separator(self):
        self.assertSilent(command='cd C:\\proj; & "C:\\Tools\\lint.exe" src',
                          tool="PowerShell")

    def test_npm_audit_omit_dev(self):
        # `=`-valued flags: `dev` here is a dependency group, not a dev server.
        self.assertSilent(command="npm audit --omit=dev")

    def test_npm_ls_include_dev(self):
        self.assertSilent(command="npm ls --include=dev --depth=0")

    def test_make_test(self):
        self.assertSilent(command="make test")

    def test_docker_build(self):
        self.assertSilent(command="docker build -t app .")

    # --- foreground test/build runs that transiently bind a port -------------------------
    def test_npm_test(self):
        self.assertSilent(command="npm test")

    def test_npm_test_watch_flag(self):
        # `--watch` is a flag on a test run, not a dev server.
        self.assertSilent(command="npm test -- --watch")

    def test_npm_run_build(self):
        self.assertSilent(command="npm run build")

    def test_vite_build(self):
        self.assertSilent(command="npx vite build")

    def test_vitest(self):
        # `vitest` must not match the `vite` dev-server pattern.
        self.assertSilent(command="npx vitest run --reporter=dot")

    def test_playwright_test(self):
        self.assertSilent(command="npx playwright test --project=chromium")

    def test_npm_run_test_e2e(self):
        self.assertSilent(command="npm run test:e2e")

    def test_npm_start(self):
        # Documented disposition — see this file's docstring and the hook header's residual 1.
        self.assertSilent(command="npm start")

    def test_npm_run_start(self):
        self.assertSilent(command="npm run start")

    # --- search / read commands that merely MENTION the verbs ----------------------------
    def test_grep_mentioning_stop_process(self):
        self.assertSilent(command='grep -r "Stop-Process" src/')

    def test_grep_unquoted_kill_verb(self):
        self.assertSilent(command="rg -n taskkill hooks/")

    def test_reading_a_file_named_kill(self):
        self.assertSilent(command="cat kill.sh")

    def test_bash_array_literal_is_not_a_head(self):
        # The grouping-punctuation strip must not turn `files=(*.png)` into the head `*.png` —
        # the env-assignment test still has to win on the same token.
        self.assertSilent(command="files=(*.png) && ls -la")

    def test_select_string_powershell(self):
        self.assertSilent(command='Select-String -Pattern "Stop-Process" -Path *.py',
                          tool="PowerShell")

    def test_get_content_kill_script(self):
        self.assertSilent(command="Get-Content ./scripts/kill.sh", tool="PowerShell")

    def test_get_process_is_read_only(self):
        self.assertSilent(command="Get-Process node", tool="PowerShell")

    def test_echoed_verb(self):
        self.assertSilent(command='echo "npm run dev"')

    def test_commit_message_mentioning_lifecycle(self):
        self.assertSilent(command='git commit -m "fix: stop the dev server at teardown"')

    def test_heredoc_report_body(self):
        self.assertSilent(
            command="cat <<'EOF'\nThe leaf could not run npm run dev; reported as blocked.\nEOF")

    def test_benign_compound(self):
        self.assertSilent(command="ls -la && git status")

    def test_background_flag_false(self):
        self.assertSilent(command="npm test", background=False)

    def test_redirect_is_not_a_background_amp(self):
        self.assertSilent(command="npm test > out.log 2>&1")


class MainSessionIsNoOp(GuardCase):
    """No discriminator = main loop = pure no-op. The guard is a leaf-only mechanism; the
    attended session owns process lifecycle and must never be nagged about exercising it."""

    def test_launch_in_main_session(self):
        self.assertSilent(command="npm run dev", caller={})

    def test_kill_in_main_session(self):
        self.assertSilent(command="taskkill /F /IM node.exe", caller={})

    def test_background_flag_in_main_session(self):
        self.assertSilent(command="npm run dev", background=True, caller={})

    def test_empty_discriminators_read_as_main_loop(self):
        # Fail toward ask, exactly like the install guard: empty strings are not a leaf signal.
        self.assertSilent(command="npm run dev", caller={"agent_type": "", "agent_id": ""})

    def test_agent_type_only_is_a_leaf(self):
        self.assertDenied(command="npm run dev", caller={"agent_type": "general-purpose"})

    def test_agent_id_only_is_a_leaf(self):
        # A forked query — not the main loop, so still not a lifecycle owner.
        self.assertDenied(command="npm run dev", caller={"agent_id": "ag_9"})


class DenyTextContract(GuardCase):
    """The reason string reaches the model verbatim; it is the whole escape hatch."""

    def reason(self, **kw):
        o = self.out(self.run_hook(**kw))
        return o["hookSpecificOutput"]["permissionDecisionReason"]

    def test_pins_the_full_deny_text(self):
        self.assertEqual(self.reason(command="npm run dev"),
                         "Standing-service launch blocked ('npm run dev'): this is a sub-agent. "
                         + LEAF_ESCAPE)

    def test_names_no_path_or_valve(self):
        # There is no release valve: the leaf reports a false positive, it is handed no lever.
        r = self.reason(command="npm run dev")
        self.assertNotIn("valve", r)
        self.assertNotIn(self.home, r)

    def test_is_domain_neutral(self):
        # A leaf in a project with no frontend must read this as ordinary lifecycle hygiene.
        r = self.reason(command="npm run dev").lower()
        for word in ("visual", "frontend", "screenshot", "browser", "css", "review"):
            self.assertNotIn(word, r)

    def test_systemmessage_present_for_ledger(self):
        o = self.out(self.run_hook(command="npm run dev"))
        self.assertIn("process-lifecycle-guard", o["systemMessage"])


class FailOpen(GuardCase):
    """A guard bug must never block every Bash/PowerShell call — and never silently."""

    def _announced(self, proc):
        self.assertEqual(proc.returncode, 0)
        o = json.loads(proc.stdout.strip())
        self.assertIn("systemMessage", o)
        self.assertIn("shell-guards", o["systemMessage"])
        self.assertNotIn("hookSpecificOutput", o)

    def test_malformed_payload(self):
        self._announced(self.run_hook(raw="not json"))

    def test_non_object_payload(self):
        self._announced(self.run_hook(raw='"just a string"'))

    def test_empty_stdin(self):
        self._announced(self.run_hook(raw=""))

    def test_missing_tool_input(self):
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        proc = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"hook_event_name": "PreToolUse", "agent_id": "ag_1"}),
            capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_non_dict_tool_input(self):
        env = dict(os.environ, BALLAST_CLAUDE_HOME=self.home)
        proc = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"tool_input": "oops", "agent_id": "ag_1"}),
            capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_missing_transcript_path_still_denies(self):
        # The guard reads no session state: a sparse leaf payload still denies.
        proc = self.run_hook(command="npm run dev", transcript="")
        self.assertEqual(self.decision(proc), "deny")


if __name__ == "__main__":
    unittest.main()
