"""Basic CLI entry points: -v/-V/--version, `version`, `help [command]`, `update` (no install, just the
right upgrade command) and `doctor` (exit 1 when something is wrong). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_sync import __version__, cli, update


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class VersionFlagTests(unittest.TestCase):
    def test_short_and_long_version_flags_print_the_version(self):
        for flag in ("-v", "-V", "--version"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as ctx:
                run_cli(flag)
            self.assertEqual(ctx.exception.code, 0)

    def test_version_subcommand_text(self):
        code, text = run_cli("version")
        self.assertEqual((code, text.strip()), (0, f"aisync {__version__}"))


class HelpCommandTests(unittest.TestCase):
    def test_help_lists_every_basic_command(self):
        code, text = run_cli("help")
        self.assertEqual(code, 0)
        for name in ("version", "help", "update", "doctor"):
            self.assertIn(name, text)

    def test_help_topic_shows_that_commands_options(self):
        code, text = run_cli("help", "log")
        self.assertEqual(code, 0)
        self.assertIn("--summary", text)
        self.assertIn("usage: aisync log", text)

    def test_unknown_topic_is_an_error_with_exit_2(self):
        code, text = run_cli("help", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no such command: nope", text)


class UpdateTests(unittest.TestCase):
    def test_parse_version_accepts_plain_and_v_prefix_only(self):
        self.assertEqual(update.parse_version("v0.2.5"), (0, 2, 5))
        self.assertEqual(update.parse_version("1.10"), (1, 10))
        self.assertIsNone(update.parse_version("latest"))
        self.assertIsNone(update.parse_version(""))

    def test_up_to_date_and_newer_and_offline(self):
        code, msg = update.check("0.2.5", lambda: "v0.2.5", "pip")
        self.assertEqual(code, 0)
        self.assertIn("up to date", msg)
        code, msg = update.check("0.2.5", lambda: "v0.10.0", "pip")   # numeric, not string, compare
        self.assertIn("0.2.5 -> v0.10.0 is available", msg)
        code, msg = update.check("0.2.5", lambda: (_ for _ in ()).throw(OSError("offline")), "pip")
        self.assertEqual(code, 1)
        self.assertIn("could not check for updates", msg)

    def test_unreadable_release_tag_is_reported_not_guessed(self):
        code, msg = update.check("0.2.5", lambda: "nightly", "pip")
        self.assertEqual(code, 1)
        self.assertIn("could not read the latest release", msg)

    def test_upgrade_command_follows_install_method(self):
        self.assertEqual(update.install_method("/srv/env/.local/share/uv/tools/aisync/lib/aisync/__init__.py"), "uv")
        self.assertEqual(update.install_method("/srv/env/.local/pipx/venvs/aisync/lib/x.py"), "pipx")
        self.assertEqual(update.install_method("/srv/env/.local/share/aisync/venv/lib/x.py"), "installer")
        self.assertEqual(update.install_method("/opt/homebrew/Cellar/aisync/0.2.5/x.py"), "brew")
        self.assertEqual(update.install_method("/usr/lib/python3/site-packages/aisync/x.py"), "pip")
        _, msg = update.check("0.2.5", lambda: "v0.3.0", "brew")
        self.assertIn("brew upgrade aisync", msg)

    def test_update_command_never_installs(self):
        with mock.patch("subprocess.run") as run_mock, mock.patch("os.system") as system_mock:
            with mock.patch.object(update, "fetch_latest_tag", return_value="v9.9.9"):
                code, _ = run_cli("update")
        self.assertEqual(code, 0)
        run_mock.assert_not_called()
        system_mock.assert_not_called()


class DoctorTests(unittest.TestCase):
    def test_doctor_fails_when_hub_folder_is_missing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AISYNC_HOME": str(Path(d) / "none")}):
            code, text = run_cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("does not exist (run aisync init)", text)
        self.assertIn("python", text)

    def test_doctor_passes_on_a_fresh_hub(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AISYNC_HOME": d}):
            code, text = run_cli("doctor")
        self.assertEqual(code, 0, text)
        self.assertIn("journal chain intact", text)
        self.assertNotIn("FAIL", text)


if __name__ == "__main__":
    unittest.main()


class ReleaseWorkflowTests(unittest.TestCase):
    """The PyPI job must publish only after the GitHub Release, by Trusted Publishing (no stored token).
    Plain text checks, so the test needs no YAML library (the project stays stdlib-only)."""

    def test_pypi_job_uses_trusted_publishing_after_release(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/release.yml").read_text()
        job = text[text.index("  publish-pypi:"):]
        self.assertIn("needs: release", job)
        self.assertIn("id-token: write", job)
        self.assertIn("pypa/gh-action-pypi-publish@release/v1", job)
        self.assertNotIn("password", job)
        self.assertNotIn("PYPI_TOKEN", job)
