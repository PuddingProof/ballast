#!/usr/bin/env python3
"""Render a hook's injected additionalContext as readable text.

A UserPromptSubmit hook stores its injected text as a single JSON string with
literal \\n escapes — unreadable in the raw .sh, and gone from terminal
scrollback the moment it fires. This runs the hook with a triggering payload,
parses its JSON stdout, and prints the additionalContext with real line breaks.

It's a no-drift view: the hook stays the single source of truth, so what you
read here is exactly what gets injected — no separate copy to keep in sync.

Usage (works from PowerShell or bash):
    python ~/.claude/hooks/render-hook-context.py <hook.sh> [trigger text...]

    python ~/.claude/hooks/render-hook-context.py hooks/subagent-fanout.sh
    python ~/.claude/hooks/render-hook-context.py hooks/freehand-mode.sh autopilot

Want a markdown copy to open in an editor? Just redirect:
    python ~/.claude/hooks/render-hook-context.py hooks/subagent-fanout.sh > view.md
"""
import json
import os
import shutil
import subprocess
import sys


def find_bash():
    """Locate bash to run the .sh hook. bash is on PATH under Git Bash, but NOT
    under PowerShell/cmd — so fall back to the standard Git-for-Windows install
    locations before giving up."""
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: render-hook-context.py <hook.sh> [trigger text...]")
    hook = os.path.abspath(sys.argv[1])
    bash = find_bash()
    if not bash:
        sys.exit("error: couldn't find bash (needed to run .sh hooks). Install "
                 "Git for Windows, or run this from a Git Bash shell.")
    # Payload is fed on stdin exactly as Claude Code would. A plain string is
    # enough to trip keyword-grep hooks; the default packs several common
    # triggers so most hooks fire without the caller naming the right keyword.
    trigger = " ".join(sys.argv[2:]) or "ultracode autopilot fanout code-review deep-research"
    proc = subprocess.run([bash, hook], input=trigger,
                          capture_output=True, text=True, timeout=30)
    out = proc.stdout.strip()
    if not out:
        sys.exit(f"(no output — payload {trigger!r} did not trigger {hook})")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        sys.exit(f"hook output is not valid JSON: {e}\n---\n{out}")
    # additionalContext may sit at top level (canonical for UserPromptSubmit)
    # or under the hookSpecificOutput wrapper — accept either.
    ctx = (data.get("additionalContext")
           or data.get("hookSpecificOutput", {}).get("additionalContext"))
    if ctx is None:
        sys.exit("no additionalContext found:\n" + json.dumps(data, indent=2))
    print(ctx)


if __name__ == "__main__":
    main()
