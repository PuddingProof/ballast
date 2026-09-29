#!/usr/bin/env python3
"""ballast-statusline install/uninstall/status — per-machine statusLine enabler.

Background: Claude Code plugins cannot ship the `statusLine` settings key
(doc-verified — only `subagentStatusLine` is plugin-shippable), so wiring the
ballast statusline renderer up requires a one-time edit to this machine's
user-level settings.json. settings.json hot-reloads, so `install` takes
effect without a restart. The plugin directory is replaced wholesale on each
plugin update, but its filesystem PATH is stable across updates on a given
machine, so baking an absolute command path in at install time is correct
and does not need to be re-baked on every update.

Fail-safe posture: this is a user-invoked CLI (not a hook), so human-facing
messages on stdout/stderr are fine and expected. It must never overwrite a
settings.json it cannot fully understand — an unparseable or non-object
settings.json is always a refusal, never a best-effort merge.

Settings path resolution: `<home>/settings.json`, where home is
BALLAST_CLAUDE_HOME if set (hermetic-test override, mirrors the convention
used by run.sh), else `~/.claude`.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def get_home():
    override = os.environ.get("BALLAST_CLAUDE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def get_settings_path():
    return get_home() / "settings.json"


def get_render_path():
    """Absolute, forward-slash path to statusline/render.sh, derived from
    this file's own on-disk location (plugin root = parent of statusline/).
    Forward slashes are used unconditionally so the baked command string is
    bash-friendly even on Windows, where bash (Git Bash) is the interpreter
    that will actually run it."""
    plugin_root = Path(__file__).resolve().parent.parent
    render_path = plugin_root / "statusline" / "render.sh"
    return str(render_path).replace("\\", "/")


def render_command_string():
    return 'bash "{}"'.format(get_render_path())


def _atomic_write_json(path, data):
    """Write JSON atomically: tmp file in the same directory, then
    os.replace — never leaves a torn/partial settings.json on disk even if
    the process is interrupted mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_settings(settings_path):
    """Returns (raw_text_or_None, settings_dict_or_None, error_message_or_None).

    raw_text is None if the file doesn't exist. error_message is set if the
    file exists but could not be parsed as a JSON object — callers must
    refuse to write in that case rather than guess."""
    if not settings_path.exists():
        return None, {}, None
    try:
        raw = settings_path.read_text(encoding="utf-8")
    except OSError as e:
        return None, None, "cannot read {}: {}".format(settings_path, e)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return raw, None, "{} contains invalid JSON ({}); refusing to modify.".format(
            settings_path, e
        )
    if not isinstance(parsed, dict):
        return raw, None, "{} does not contain a JSON object at the top level; refusing to modify.".format(
            settings_path
        )
    return raw, parsed, None


def _existing_command(settings):
    existing = settings.get("statusLine")
    if isinstance(existing, dict):
        return existing, existing.get("command", "")
    return existing, ""


def cmd_install(args):
    settings_path = get_settings_path()
    render_path = get_render_path()
    command_str = render_command_string()

    raw, settings, error = _load_settings(settings_path)
    if error is not None:
        print("ballast-statusline: {}".format(error), file=sys.stderr)
        return 1

    existing, existing_command = _existing_command(settings)
    is_ours = existing is not None and render_path in existing_command

    if existing is not None and not is_ours:
        if not args.force:
            print(
                "ballast-statusline: a statusLine is already configured in "
                "{}: {!r}\nRefusing to overwrite (use --force to override)."
                .format(settings_path, existing),
                file=sys.stderr,
            )
            return 1
        # --force: fall through and overwrite the foreign statusLine.
    elif is_ours:
        print("ballast-statusline: already installed ({})".format(settings_path))
        return 0

    # About to perform a modifying write. Back up the pre-install content
    # exactly once — never clobber a pre-existing backup, since that backup
    # holds the true pre-ballast state (e.g. from a --force run over a
    # foreign statusLine that itself already has a stale .ballast-bak).
    # `raw is not None` (not a fresh exists() check): raw is non-None iff the file existed at
    # LOAD time. Re-probing the filesystem here would race a concurrent settings.json writer
    # (Claude Code itself rewrites settings.json at runtime) into backing up `None`.
    if raw is not None:
        backup_path = settings_path.with_name(settings_path.name + ".ballast-bak")
        if not backup_path.exists():
            backup_path.write_text(raw, encoding="utf-8")

    settings["statusLine"] = {"type": "command", "command": command_str}
    _atomic_write_json(settings_path, settings)
    print(
        "ballast-statusline: installed ({})\n  command: {}".format(
            settings_path, command_str
        )
    )
    return 0


def cmd_uninstall(args):
    settings_path = get_settings_path()
    render_path = get_render_path()

    if not settings_path.exists():
        print(
            "ballast-statusline: no settings file at {}; nothing to uninstall"
            .format(settings_path)
        )
        return 0

    raw, settings, error = _load_settings(settings_path)
    if error is not None:
        print("ballast-statusline: {}".format(error), file=sys.stderr)
        return 1

    existing, existing_command = _existing_command(settings)
    if existing is None:
        print(
            "ballast-statusline: no statusLine configured in {}; nothing to uninstall"
            .format(settings_path)
        )
        return 0

    if render_path not in existing_command:
        print(
            "ballast-statusline: statusLine in {} is not ours ({!r}); "
            "leaving it untouched.".format(settings_path, existing),
            file=sys.stderr,
        )
        return 1

    del settings["statusLine"]
    _atomic_write_json(settings_path, settings)
    print("ballast-statusline: uninstalled ({})".format(settings_path))
    return 0


def cmd_status(args):
    settings_path = get_settings_path()
    render_path = get_render_path()

    if not settings_path.exists():
        print("ballast-statusline: not installed (no settings file at {})".format(settings_path))
        return 0

    raw, settings, error = _load_settings(settings_path)
    if error is not None:
        print("ballast-statusline: {}".format(error), file=sys.stderr)
        return 1

    existing, existing_command = _existing_command(settings)
    if existing is None:
        print("ballast-statusline: not installed (no statusLine key in {})".format(settings_path))
        return 0

    if render_path in existing_command:
        print(
            "ballast-statusline: installed ({})\n  command: {}".format(
                settings_path, existing_command
            )
        )
    else:
        print(
            "ballast-statusline: foreign statusLine present in {}: {!r}"
            .format(settings_path, existing)
        )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ballast-statusline",
        description="Enable/disable the ballast statusLine renderer in this "
        "machine's Claude Code settings.json.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Enable the ballast statusLine.")
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing non-ballast statusLine.",
    )
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser(
        "uninstall", help="Remove the ballast statusLine (only if it is the one installed)."
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_status = sub.add_parser("status", help="Report current statusLine configuration state.")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
