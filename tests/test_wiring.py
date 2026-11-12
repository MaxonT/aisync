"""Tests for the opt-in wiring of gate (push-time test gate) and web-inbox into the public aisync.

What: the git pre-push hook carries the gate only when config `gate_hook = true`; web-inbox polling is off by
      default (`web_inbox_secs = 0`); the `gate` and `web-inbox` commands exist and print help.
Why:  both features touch every repo on the machine (git hooks) or every watch cycle (timer). Off by default
      means installing this release changes nothing until the owner turns a feature on.
Run:  python3 -m unittest discover -s tests   (or pytest tests/test_wiring.py)
"""
from __future__ import annotations

import io
import os
import contextlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_sync import cli, core, hooks

ROOT = Path(__file__).resolve().parents[1]


class PrePushHookTests(unittest.TestCase):
    def test_default_pre_push_chains_only(self):
        body = hooks._hook_body("pre-push")
        self.assertNotIn("gate pre-push", body)
        self.assertIn("local_hook=", body)          # still chains to the repo's own hook

    def test_gate_flag_puts_the_gate_into_pre_push(self):
        body = hooks._hook_body("pre-push", gate=True)
        self.assertIn("gate pre-push", body)
        self.assertIn("check.sh", body)
        self.assertIn("exit 1", body)

    def test_gate_flag_does_not_touch_other_hooks(self):
        self.assertEqual(hooks._hook_body("post-commit", gate=True), hooks._hook_body("post-commit"))
        self.assertEqual(hooks._hook_body("commit-msg", gate=True), hooks._hook_body("commit-msg"))

    def test_write_git_hooks_default_is_chain_only(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            hooks.write_git_hooks(dest)
            self.assertNotIn("gate pre-push", (dest / "pre-push").read_text())
            hooks.write_git_hooks(dest, gate=True)
            self.assertIn("gate pre-push", (dest / "pre-push").read_text())


class ConfigDefaultsTests(unittest.TestCase):
    def test_new_features_are_off_by_default(self):
        cfg = core.Config(root=Path("/nonexistent-aisync-hub"))
        self.assertFalse(cfg.gate_hook)
        self.assertEqual(cfg.web_inbox_secs, 0.0)
        self.assertEqual(cfg.web_inbox_branch, "web-inbox")
        self.assertEqual(cfg.web_inbox_remote, "origin")

    def test_config_file_turns_gate_hook_on(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config.toml").write_text('gate_hook = true\nweb_inbox_secs = 30\n')
            cfg = core.load_config(root)
            self.assertTrue(cfg.gate_hook)
            self.assertEqual(cfg.web_inbox_secs, 30.0)


class CommandTests(unittest.TestCase):
    def test_web_inbox_cli_without_a_branch_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"AISYNC_HOME": td}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["web-inbox", "--no-fetch", "--json"])
            self.assertIn(code, (0, 1))            # no web-inbox branch yet: exit 1 (fetch/read failed), never a crash

    def test_gate_check_on_a_non_repo_refuses(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"AISYNC_HOME": td}):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["gate", "check", "--repo", td])
            self.assertEqual(code, 2)
            self.assertIn("cannot resolve", err.getvalue())

    def _help(self, *argv):
        return subprocess.run([sys.executable, "-m", "ai_sync", *argv, "--help"], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_gate_help(self):
        r = self._help("gate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pre-push", r.stdout)

    def test_web_inbox_help(self):
        r = self._help("web-inbox")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--no-fetch", r.stdout)

    def test_top_level_help_mentions_both_commands(self):
        r = subprocess.run([sys.executable, "-m", "ai_sync", "--help"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("gate", "web-inbox"):
            self.assertIn(name, r.stdout)


if __name__ == "__main__":
    unittest.main()
