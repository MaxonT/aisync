"""Tests for ai_sync/landq.py, the per-repo landing queue (merge queue, 2026-11-02), and its use by wt land and the
pre-push gate. Run: python3 -m unittest discover -s tests -p 'test_landq.py'"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ai_sync import gate, landq

from test_ai_sync import Base, _git

SID1 = "aaaaaaaa-1111-2222-3333-444444444444"
SID2 = "bbbbbbbb-1111-2222-3333-444444444444"


def out(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class LockTest(Base):
    def test_waiter_says_who_is_ahead_and_gives_up_at_the_deadline(self):
        said = []
        with landq.Lock(self.proj, "first lander"):
            second = landq.Lock(self.proj, "second", wait_min=0.02, poll=0.05, say=said.append)
            self.assertFalse(second.acquire())
        self.assertTrue(any("排队中，前面是 first lander" in m for m in said), said)
        self.assertTrue(landq.Lock(self.proj, "third", wait_min=0.02, poll=0.05).acquire())   # free again

    def test_worktrees_of_one_repo_share_one_queue(self):
        wt = self.t / "linked"
        _git(self.proj, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
        self.assertEqual(landq.repo_key(self.proj), landq.repo_key(wt))

    def test_clones_pushing_to_the_same_github_repo_share_one_queue(self):
        other = self.t / "other-clone"
        _git(self.t, "init", "-q", str(other))
        _git(self.proj, "remote", "add", "origin", "git@example.com:owner/repo.git")
        _git(other, "remote", "add", "origin", "https://example.com/owner/repo.git")
        self.assertEqual(landq.repo_key(self.proj), landq.repo_key(other))
        self.assertEqual(landq.repo_key(self.proj), "example.com_owner_repo")
        self.assertEqual(landq.repo_key(other, "https://example.com/owner/repo"), "example.com_owner_repo")

    def test_a_lock_older_than_the_stale_limit_is_taken_over(self):
        said = []
        old = landq.Lock(self.proj, "stuck lander")
        self.assertTrue(old.acquire())
        h = json.loads(old.path.read_text())
        h["since"] = (dt.datetime.now().astimezone() - dt.timedelta(minutes=20)).isoformat(timespec="seconds")
        os.ftruncate(old.fd, 0)
        os.lseek(old.fd, 0, 0)
        os.write(old.fd, json.dumps(h).encode())
        new = landq.Lock(self.proj, "next lander", wait_min=0.05, poll=0.05, say=said.append)
        self.assertTrue(new.acquire())
        self.assertTrue(any("接管" in m for m in said), said)
        old.release()                                   # the old holder letting go does not free the new lock
        self.assertFalse(landq.Lock(self.proj, "x", wait_min=0.01, poll=0.05).acquire())
        new.release()

    def test_a_dead_holder_frees_the_queue_at_once(self):
        path = landq.lock_dir() / f"{landq.repo_key(self.proj)}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        code = ("import fcntl,os,sys,time\nfd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT)\n"
                "fcntl.flock(fd,fcntl.LOCK_EX)\nprint('held',flush=True)\ntime.sleep(60)\n")
        p = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        self.assertFalse(landq.Lock(self.proj, "x", wait_min=0.01, poll=0.05).acquire())
        p.kill()
        p.wait()
        self.assertTrue(landq.Lock(self.proj, "x", wait_min=0.05, poll=0.05).acquire())


class QueueRepoTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("GATE_ACTIVE", None)
        self.key = str(self.proj.resolve())
        self.remote = self.t / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        _git(self.key, "remote", "add", "origin", str(self.remote))
        self.marks = self.t / "marks"
        self.marks.mkdir()
        # mkdir is atomic: a second check running at the same time fails, so overlapping landings would show
        self._commit(self.proj, "check.sh",
                     f'#!/bin/sh\nmkdir "{self.marks}/running" || {{ echo GATE-FAIL overlap; exit 1; }}\n'
                     f'echo x >> "{self.marks}/runs"\nsleep 1\nrmdir "{self.marks}/running"\n', "feat: check")
        _git(self.key, "push", "-q", "-u", "origin", "main")

    def _commit(self, repo, name, text, msg):
        p = Path(repo) / name
        p.write_text(text)
        p.chmod(0o755)
        _git(repo, "add", name)
        _git(repo, "commit", "-q", "-m", msg)

    def test_same_tree_after_a_rebase_skips_the_check(self):
        sha = out(self.proj, "rev-parse", "HEAD")
        ok, _out, _secs = gate.check_commit(self.proj, sha)
        self.assertTrue(ok)
        _git(self.proj, "commit", "-q", "--allow-empty", "-m", "chore: same tree, new sha")
        sha2 = out(self.proj, "rev-parse", "HEAD")
        ok, msg, secs = gate.check_commit(self.proj, sha2)
        self.assertTrue(ok)
        self.assertIn("文件树", msg)
        self.assertEqual(len((self.marks / "runs").read_text().split()), 1)   # ran once, the second was a hit

    def test_gate_keeps_the_queue_until_the_git_push_exits(self):
        self._commit(self.proj, "a.txt", "a", "feat: a")
        sha, rsha = out(self.proj, "rev-parse", "HEAD"), out(self.remote, "rev-parse", "main")
        pusher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        os.environ["GATE_PUSH_PID"] = str(pusher.pid)
        try:
            rc = gate.pre_push(self.proj, "origin", [f"refs/heads/main {sha} refs/heads/main {rsha}"], say=lambda m: None)
        finally:
            os.environ.pop("GATE_PUSH_PID", None)
        self.assertEqual(rc, 0)
        self.assertFalse(landq.Lock(self.proj, "next", wait_min=0.02, poll=0.05).acquire())   # still held for the push
        pusher.kill()
        pusher.wait()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not landq.Lock(self.proj, "next", wait_min=0.01, poll=0.05).acquire():
            time.sleep(0.2)
        self.assertLess(time.monotonic(), deadline)                                 # freed once the push ended

    def test_push_based_on_an_old_main_is_refused_before_checking(self):
        self._commit(self.proj, "a.txt", "a", "feat: a")
        sha, old = out(self.proj, "rev-parse", "HEAD"), out(self.remote, "rev-parse", "main")
        other = self.t / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True)
        self._commit(other, "o.txt", "o", "feat: o")
        _git(other, "push", "-q", "origin", "HEAD:main")
        said = []
        rc = gate.pre_push(self.proj, "origin", [f"refs/heads/main {sha} refs/heads/main {old}"], say=said.append)
        self.assertEqual(rc, 1)
        self.assertIn("远端 main 已经是", "\n".join(said))
        self.assertFalse((self.marks / "runs").exists())                          # no check was spent on it
