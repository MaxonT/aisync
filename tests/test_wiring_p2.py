"""Tests for the opt-in wiring of guard (one Claude Code session per repo), lease and wt into the public aisync.

What: the guard's Claude Code entries appear only when config `guard_hook = true`; the guard hook fails open on
      bad input; `lease status` runs; `wt land` pushes to main only with --yes.
Why:  the guard can deny edits and wt land pushes to main, so both default to doing nothing.
Run:  python3 -m unittest discover -s tests -p 'test_wiring_p2.py'
"""
from __future__ import annotations

import contextlib
import io
import subprocess
from pathlib import Path
from unittest import mock

from ai_sync import cli, core, hooks, worktree

from test_ai_sync import Base, _git

SID = "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb"
BIN = "/opt/test/aisync"


def out(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def run_cli(*argv, stdin: str | None = None):
    so, se = io.StringIO(), io.StringIO()
    patch = mock.patch("sys.stdin", io.StringIO(stdin)) if stdin is not None else contextlib.nullcontext()
    with patch, contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        code = cli.main(list(argv))
    return code, so.getvalue() + se.getvalue()


class GuardOptInTests(Base):
    def test_claude_plan_has_no_guard_entries_by_default(self):
        change = hooks.plan_claude(self.t, BIN)
        text = change.diff() if change else ""
        self.assertIn("hook claude-code", text)            # the news hook is still planned
        self.assertNotIn(f"{BIN} guard", text)

    def test_opt_in_adds_pre_tool_use_and_session_end(self):
        change = hooks.plan_claude(self.t, BIN, guard=True)
        text = change.diff()
        self.assertIn(f"{BIN} guard", text)
        self.assertIn("PreToolUse", text)
        self.assertIn("SessionEnd", text)
        self.assertIn(hooks.GUARD_MATCHER, text)

    def test_config_flag_reaches_the_install_plan(self):
        (self.home / "config.toml").write_text("guard_hook = true\n")
        cfg = core.load_config(self.home)
        changes, _errors = hooks.plan_install(cfg, self.t, BIN, want=("claude-code",))
        self.assertTrue(any(f"{BIN} guard" in c.diff() for c in changes))


class GuardHookTests(Base):
    def test_guard_hook_fails_open_on_garbage_input(self):
        code, _ = run_cli("guard", stdin="{not json")
        self.assertEqual(code, 0)

    def test_guard_hook_with_no_input_exits_zero(self):
        code, _ = run_cli("guard", stdin="")
        self.assertEqual(code, 0)


class LeaseCliTests(Base):
    def test_lease_status_runs_on_an_empty_hub(self):
        code, text = run_cli("lease", "status")
        self.assertEqual(code, 0, text)

    def test_lease_release_needs_a_key_or_session(self):
        code, text = run_cli("lease", "release")
        self.assertEqual(code, 2)
        self.assertIn("--key or --session", text)


class WtLandConfirmationTests(Base):
    def setUp(self):
        super().setUp()
        self.key = str(self.proj.resolve())
        self.remote = self.t / "remote.git"                  # a bare origin, so land takes the GitHub-main path
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        _git(self.proj, "remote", "add", "origin", str(self.remote))
        _git(self.proj, "push", "-q", "origin", "HEAD:main")

    def _session_commit(self):
        ok, path = worktree.start(self.key, SID)
        self.assertTrue(ok, path)
        (Path(path) / "w.txt").write_text("from the session\n")
        _git(path, "add", "w.txt")
        _git(path, "commit", "-q", "-m", "work")
        return path

    def test_wt_land_without_yes_pushes_nothing(self):
        path = self._session_commit()
        before = out(self.remote, "rev-parse", "main")
        code, text = run_cli("wt", "land", "--key", self.key, "--session", SID)
        self.assertEqual(code, 0, text)
        self.assertIn("dry run, nothing pushed", text)
        self.assertEqual(out(self.remote, "rev-parse", "main"), before)     # remote main did not move
        self.assertTrue(Path(path).exists())                                # worktree kept

    def test_wt_land_with_yes_lands_on_main(self):
        self._session_commit()
        before = out(self.remote, "rev-parse", "main")
        code, text = run_cli("wt", "land", "--key", self.key, "--session", SID, "--yes")
        self.assertEqual(code, 0, text)
        self.assertNotEqual(out(self.remote, "rev-parse", "main"), before)

    def test_wt_land_without_a_worktree_is_an_error_and_pushes_nothing(self):
        before = out(self.remote, "rev-parse", "main")
        code, text = run_cli("wt", "land", "--key", self.key, "--session", SID)
        self.assertEqual(code, 1)
        self.assertIn("no worktree", text)
        self.assertEqual(out(self.remote, "rev-parse", "main"), before)
