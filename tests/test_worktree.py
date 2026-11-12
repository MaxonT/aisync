"""Tests for ai_sync/worktree.py: `aisync wt start` / `aisync wt land` (lock fix, step 3).
Run: python3 -m unittest discover -s tests -p 'test_worktree.py'"""
from __future__ import annotations

from pathlib import Path

from ai_sync import worktree

from test_ai_sync import Base, _git
import subprocess


def out(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout

SID = "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb"


class WorktreeTest(Base):
    def setUp(self):
        super().setUp()
        self.key = str(self.proj.resolve())

    def test_start_commit_land_cleans_up(self):
        ok, path = worktree.start(self.key, SID)
        self.assertTrue(ok, path)
        self.assertEqual(Path(path), worktree.path_for(self.key, SID))
        self.assertTrue(Path(path).is_dir())
        (Path(path) / "w.txt").write_text("from the session\n")
        _git(path, "add", "w.txt")
        _git(path, "commit", "-q", "-m", "work")
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertTrue(ok, msg)
        self.assertIn("已落地 1 个提交", msg)
        self.assertEqual((self.proj / "w.txt").read_text(), "from the session\n")
        self.assertFalse(Path(path).exists())
        self.assertNotIn("wt/abcdef12", out(self.proj, "branch", "--list"))

    def test_start_twice_reuses_the_same_worktree(self):
        ok1, p1 = worktree.start(self.key, SID)
        ok2, p2 = worktree.start(self.key, SID)
        self.assertTrue(ok1 and ok2)
        self.assertEqual(p1, p2)

    def test_land_refuses_uncommitted_edits(self):
        ok, path = worktree.start(self.key, SID)
        (Path(path) / "left.txt").write_text("not committed yet\n")
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertFalse(ok)
        self.assertIn("未提交", msg)
        self.assertTrue(Path(path).exists())                 # nothing removed while work is uncommitted

    def test_land_without_worktree_says_so(self):
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertFalse(ok)
        self.assertIn("没有 worktree", msg)

    def test_read_only_use_of_the_main_checkout_is_unaffected(self):
        worktree.start(self.key, SID)
        self.assertTrue((self.proj / ".git").exists())
        self.assertEqual(out(self.proj, "status", "--porcelain").strip(), "")


    def test_cli_start_prints_the_path_and_says_never_stash(self):
        import contextlib
        import io
        from ai_sync import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["wt", "start", "--key", self.key, "--session", SID])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), str(worktree.path_for(self.key, SID)))   # stdout: path only
        self.assertIn("do not use git stash; commit WIP on your wt branch instead", err.getvalue())


class WorktreeOriginTest(Base):
    """2026-11-02: land must put the commits on GitHub's main first, and the local main only fast-forwards."""

    def setUp(self):
        super().setUp()
        self.key = str(self.proj.resolve())
        self.remote = self.t / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        _git(self.key, "branch", "-M", "main")
        _git(self.key, "remote", "add", "origin", str(self.remote))
        _git(self.key, "push", "-q", "-u", "origin", "main")
        self.other = self.t / "other-clone"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.other)], check=True)
        _git(self.other, "config", "user.email", "t@example.com")
        _git(self.other, "config", "user.name", "t")

    def _commit_in(self, repo, name, text):
        (Path(repo) / name).write_text(text)
        _git(repo, "add", name)
        _git(repo, "commit", "-q", "-m", name)

    def test_land_pushes_to_origin_and_local_main_follows(self):
        ok, path = worktree.start(self.key, SID)
        self._commit_in(path, "w.txt", "from the session\n")
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertTrue(ok, msg)
        self.assertIn("已推送到 GitHub", msg)
        self.assertEqual(out(self.remote, "rev-parse", "main"), out(self.proj, "rev-parse", "main"))
        self.assertFalse(Path(path).exists())

    def test_origin_moved_meanwhile_lands_without_splitting_local_main(self):
        ok, path = worktree.start(self.key, SID)
        self._commit_in(path, "w.txt", "from the session\n")
        self._commit_in(self.other, "o.txt", "landed elsewhere first\n")
        _git(self.other, "push", "-q", "origin", "main")
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertTrue(ok, msg)
        local, remote = out(self.proj, "rev-parse", "main"), out(self.remote, "rev-parse", "main")
        self.assertEqual(local, remote)                                   # no split: local is exactly GitHub's main
        self.assertTrue((self.proj / "o.txt").exists() and (self.proj / "w.txt").exists())

    def test_push_rejected_keeps_worktree_and_reports_failure(self):
        ok, path = worktree.start(self.key, SID)
        self._commit_in(path, "w.txt", "from the session\n")
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        before = out(self.proj, "rev-parse", "main")
        ok, msg = worktree.land(self.key, SID, self.cfg)
        self.assertFalse(ok, msg)
        self.assertIn("未落地", msg)
        self.assertTrue(Path(path).exists())                              # work is kept
        self.assertEqual(out(self.proj, "rev-parse", "main"), before)     # local main untouched

    def test_start_branches_from_origin_main_not_a_lagging_local_main(self):
        self._commit_in(self.other, "o.txt", "newer on GitHub\n")
        _git(self.other, "push", "-q", "origin", "main")
        ok, path = worktree.start(self.key, SID)
        self.assertTrue(ok, path)
        self.assertTrue((Path(path) / "o.txt").exists())
