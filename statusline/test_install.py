#!/usr/bin/env python3
"""Hermetic tests for statusline/install.py.

Stdlib unittest only (no pytest dep, per hooks/CLAUDE.md test conventions).
Every test runs install.py as a real subprocess (sys.executable) with
BALLAST_CLAUDE_HOME pointed at a scratch tempdir, so the real
~/.claude/settings.json on this machine is never touched.

Run directly: `python statusline/test_install.py`
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
INSTALL_PY = THIS_DIR / "install.py"


class InstallTestBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="ballast-statusline-test-"))
        self.settings_path = self.home / "settings.json"
        self.backup_path = self.home / "settings.json.ballast-bak"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env["BALLAST_CLAUDE_HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(INSTALL_PY)] + list(args),
            capture_output=True,
            text=True,
            env=env,
        )

    def write_settings(self, obj_or_text):
        if isinstance(obj_or_text, str):
            self.settings_path.write_text(obj_or_text, encoding="utf-8")
        else:
            self.settings_path.write_text(
                json.dumps(obj_or_text, indent=2), encoding="utf-8"
            )

    def read_settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))


class TestFreshInstall(InstallTestBase):
    def test_fresh_install_creates_settings_with_statusline(self):
        self.assertFalse(self.settings_path.exists())
        result = self.run_cli("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.settings_path.exists())
        settings = self.read_settings()
        self.assertIn("statusLine", settings)
        self.assertEqual(settings["statusLine"]["type"], "command")
        self.assertIn("render.sh", settings["statusLine"]["command"])

    def test_fresh_install_no_backup_taken(self):
        # No pre-existing settings.json means there is nothing to back up.
        self.run_cli("install")
        self.assertFalse(self.backup_path.exists())


class TestPreservesExistingKeys(InstallTestBase):
    def test_unrelated_keys_survive_semantically(self):
        original = {
            "nested": {"a": [1, 2, 3], "b": {"c": True, "d": None}},
            "arr": [1, "two", 3.0],
            "flag": False,
            "topLevel": "value",
        }
        self.write_settings(original)
        result = self.run_cli("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = self.read_settings()
        self.assertEqual(settings["nested"], original["nested"])
        self.assertEqual(settings["arr"], original["arr"])
        self.assertEqual(settings["flag"], original["flag"])
        self.assertEqual(settings["topLevel"], original["topLevel"])
        self.assertIn("statusLine", settings)


class TestBackupBehavior(InstallTestBase):
    def test_backup_created_once_with_preinstall_content(self):
        original = {"foo": "bar", "unrelated": {"nested": 1}}
        self.write_settings(original)
        result = self.run_cli("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.backup_path.exists())
        backup_content = json.loads(self.backup_path.read_text(encoding="utf-8"))
        self.assertEqual(backup_content, original)
        self.assertNotIn("statusLine", backup_content)

    def test_backup_not_overwritten_on_reinstall(self):
        original = {"foo": "bar"}
        self.write_settings(original)
        first = self.run_cli("install")
        self.assertEqual(first.returncode, 0, first.stderr)
        backup_text_after_first = self.backup_path.read_text(encoding="utf-8")

        second = self.run_cli("install")
        self.assertEqual(second.returncode, 0, second.stderr)
        backup_text_after_second = self.backup_path.read_text(encoding="utf-8")
        self.assertEqual(backup_text_after_first, backup_text_after_second)


class TestIdempotentReinstall(InstallTestBase):
    def test_reinstall_when_already_installed_is_noop(self):
        first = self.run_cli("install")
        self.assertEqual(first.returncode, 0, first.stderr)
        content_after_first = self.settings_path.read_text(encoding="utf-8")

        second = self.run_cli("install")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already installed", second.stdout.lower())
        content_after_second = self.settings_path.read_text(encoding="utf-8")
        self.assertEqual(content_after_first, content_after_second)


class TestForeignStatusLine(InstallTestBase):
    def setUp(self):
        super().setUp()
        self.foreign = {
            "statusLine": {"type": "command", "command": "echo hi"},
            "other": 1,
        }
        self.write_settings(self.foreign)

    def test_install_refuses_foreign_statusline(self):
        before = self.settings_path.read_text(encoding="utf-8")
        result = self.run_cli("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("echo hi", result.stderr)
        after = self.settings_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertFalse(self.backup_path.exists())

    def test_install_force_overwrites_foreign(self):
        result = self.run_cli("install", "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = self.read_settings()
        self.assertIn("render.sh", settings["statusLine"]["command"])
        self.assertEqual(settings["other"], 1)
        # The pre-force content (including the foreign statusLine) is what
        # gets backed up.
        self.assertTrue(self.backup_path.exists())
        backup_content = json.loads(self.backup_path.read_text(encoding="utf-8"))
        self.assertEqual(backup_content, self.foreign)


class TestUnparseableSettings(InstallTestBase):
    def test_install_refuses_unparseable_json(self):
        garbage = "{ this is not valid json ,,, "
        self.write_settings(garbage)
        result = self.run_cli("install")
        self.assertEqual(result.returncode, 1)
        self.assertNotEqual(result.stderr.strip(), "")
        after = self.settings_path.read_text(encoding="utf-8")
        self.assertEqual(after, garbage)
        self.assertFalse(self.backup_path.exists())


class TestUninstall(InstallTestBase):
    def test_uninstall_removes_ours_preserves_other_keys(self):
        self.run_cli("install")
        settings = self.read_settings()
        settings["untouched"] = {"deep": [1, 2]}
        self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        result = self.run_cli("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.read_settings()
        self.assertNotIn("statusLine", after)
        self.assertEqual(after["untouched"], {"deep": [1, 2]})

    def test_uninstall_foreign_statusline_refuses(self):
        foreign = {"statusLine": {"type": "command", "command": "echo hi"}}
        self.write_settings(foreign)
        before = self.settings_path.read_text(encoding="utf-8")
        result = self.run_cli("uninstall")
        self.assertEqual(result.returncode, 1)
        after = self.settings_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_uninstall_when_absent_succeeds(self):
        self.assertFalse(self.settings_path.exists())
        result = self.run_cli("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uninstall_when_no_statusline_key_succeeds(self):
        self.write_settings({"foo": "bar"})
        result = self.run_cli("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.read_settings()
        self.assertEqual(after, {"foo": "bar"})


class TestStatus(InstallTestBase):
    def test_status_not_installed_no_file(self):
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout.lower())

    def test_status_not_installed_no_key(self):
        self.write_settings({"foo": "bar"})
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout.lower())

    def test_status_installed(self):
        self.run_cli("install")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed", result.stdout.lower())
        self.assertIn("render.sh", result.stdout)

    def test_status_foreign(self):
        self.write_settings({"statusLine": {"type": "command", "command": "echo hi"}})
        result = self.run_cli("status")
        self.assertIn("foreign", result.stdout.lower())


class TestBakedPathShape(InstallTestBase):
    def test_command_uses_forward_slashes_and_points_at_render_sh(self):
        self.run_cli("install")
        settings = self.read_settings()
        command = settings["statusLine"]["command"]
        self.assertNotIn("\\", command)
        self.assertIn("statusline/render.sh", command)
        # Shape check only — render.sh itself is a sibling batch's
        # deliverable and may not exist yet on disk at test time.
        self.assertTrue(command.startswith('bash "'))
        self.assertTrue(command.endswith('render.sh"'))


if __name__ == "__main__":
    unittest.main()
