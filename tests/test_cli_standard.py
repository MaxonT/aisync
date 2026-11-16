"""The standard CLI surface every aisync release must have (see the cli-standard-commands skill):
version flags, help, version, update, doctor, completion. Every subcommand must also be in the completion
scripts and in the README command tables, so a new command cannot ship invisible."""
import contextlib
import io
import re
import unittest
from pathlib import Path

from ai_sync import cli

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ["version", "help", "update", "doctor", "completion"]


def names():
    return cli._command_names()


class StandardSurfaceTests(unittest.TestCase):
    def test_standard_commands_exist(self):
        for name in STANDARD:
            self.assertIn(name, names())

    def test_every_command_is_in_all_three_completion_scripts(self):
        for shell in ("bash", "zsh", "fish"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["completion", shell]), 0)
            text = out.getvalue()
            for name in names():
                self.assertIn(name, text, f"{name} missing from {shell} completion")

    def test_every_command_is_in_both_readme_tables(self):
        for readme in ("README.md", "README.zh-CN.md"):
            text = (ROOT / readme).read_text()
            for name in names():
                if name in ("help", "version"):      # listed together with the version flags
                    continue
                rows = [l for l in text.splitlines() if l.startswith("|")]
                self.assertTrue(any(re.search(rf"`{re.escape(name)}[` /]", l) for l in rows), f"{name} not in {readme}")

    def test_version_flags_all_print_the_version(self):
        for flag in ("-v", "-V", "--version"):
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main([flag])
            self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
