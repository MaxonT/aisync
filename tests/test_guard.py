"""Tests for the session guard: one Claude Code session per repo. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import unittest
import io
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

from ai_sync import cli, core, guard

from test_ai_sync import Base, _git


def pre(session, tool, cwd, **inp):
    return {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool, "cwd": cwd, "tool_input": inp}


class GuardTest(Base):
    def setUp(self):
        super().setUp()
        os.environ["AISYNC_LEASE_EXEMPT"] = str(self.t / "shared")
        (self.t / "shared").mkdir()
        # T-20261101-71: whether a Claude process is alive in a folder is read from the machine (lsof/pgrep). On
        # a busy machine that read times out or sees a live window, and the idle-holder test failed in the full
        # run only. GuardTest decides everything else, so the host is not asked: no live holder process here.
        import unittest.mock as um
        patcher = um.patch.object(guard, "_holder_alive", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = str(self.proj / "x.txt")

    def tearDown(self):
        os.environ.pop("AISYNC_LEASE_EXEMPT", None)
        super().tearDown()

    def decide(self, payload):
        out = guard.handle(payload, self.cfg)
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_second_session_is_denied_first_keeps_working(self):
        self.assertIsNone(self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f)))
        d = self.decide(pre("sess-B", "Write", str(self.t), file_path=str(self.proj / "new.py")))
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertIn("sess-A"[:8], d["permissionDecisionReason"])
        self.assertIn(str(self.proj), d["permissionDecisionReason"])
        self.assertIsNone(self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f)))

    def test_bash_write_in_held_repo_denied_read_allowed(self):
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        self.assertEqual(self.decide(pre("sess-B", "Bash", str(self.t), command=f"cd {self.proj} && git commit -m x"))
                         ["permissionDecision"], "deny")
        self.assertEqual(self.decide(pre("sess-B", "Bash", str(self.t), command=f"git -C {self.proj} reset --hard"))
                         ["permissionDecision"], "deny")
        self.assertEqual(self.decide(pre("sess-B", "Bash", str(self.proj), command="echo hi > out.txt"))
                         ["permissionDecision"], "deny")
        for ro in (f"cd {self.proj} && git status && git log -3", f"ls {self.proj} 2>/dev/null",
                   f"cat {self.f} > /dev/null"):
            self.assertIsNone(self.decide(pre("sess-B", "Bash", str(self.proj), command=ro)), ro)

    def test_read_only_commands_pass_in_a_held_repo(self):
        """decided 2026-10-31: a held repo stays readable. Every read-only command below must pass while sess-A holds it."""
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        reads = [f"git -C {self.proj} diff", f"git -C {self.proj} show HEAD --stat", f"git -C {self.proj} branch --list",
                 f"git -C {self.proj} worktree list", f"git -C {self.proj} log --oneline -5",
                 f"git -C {self.proj} status --porcelain", f"rg TODO {self.proj}", f"grep -rn x {self.proj}",
                 f"sed -n 1,5p {self.f}", f"head -3 {self.f}", f"wc -l {self.f}", f"find {self.proj} -name '*.py'",
                 f"git -C {self.proj} fetch --dry-run origin", f"tail -n 3 {self.f}"]
        for ro in reads:
            self.assertIsNone(self.decide(pre("sess-B", "Bash", str(self.t), command=ro)), ro)
        # the tools that only read are not gated at all
        for tool in ("Read", "Grep", "Glob"):
            self.assertIsNone(self.decide(pre("sess-B", tool, str(self.t), file_path=self.f)), tool)

    def test_deny_message_says_reads_still_work(self):
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        d = self.decide(pre("sess-B", "Write", str(self.t), file_path=str(self.proj / "new.py")))
        self.assertIn("只读不受影响", d["permissionDecisionReason"])

    def test_bash_outside_any_repo_never_locks(self):
        out = self.t / "plain"
        out.mkdir()
        self.assertIsNone(self.decide(pre("sess-A", "Bash", str(out), command="touch a")))
        self.assertIsNone(self.decide(pre("sess-B", "Bash", str(out), command="touch b")))

    def test_worktree_is_its_own_key(self):
        wt = self.t / "wt"
        _git(self.proj, "worktree", "add", "-q", str(wt), "-b", "side")
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=str(wt / "x.txt"))))

    def test_deny_names_a_worktree_route_and_landing_passes(self):
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        d = self.decide(pre("sess-B-123456", "Edit", "", file_path=str(self.proj / "y.txt")))
        wt = guard.worktree_path(str(self.proj.resolve()), "sess-B-123456")
        self.assertIn("aisync wt start", d["permissionDecisionReason"])
        self.assertIn("aisync wt land", d["permissionDecisionReason"])
        self.assertIn("不要用 git stash", d["permissionDecisionReason"])     # the stash is shared by every worktree
        # the worktree the message names is free to edit
        _git(self.proj, "worktree", "add", "-q", wt, "-b", "wt/sess-B-1")
        self.assertIsNone(self.decide(pre("sess-B-123456", "Edit", "", file_path=wt + "/y.txt")))
        # landing into the held repo passes; any other write there stays denied
        land = f"git -C '{wt}' rebase main && git -C '{self.proj}' merge --ff-only wt/sess-B-1 && git -C '{self.proj}' push"
        self.assertIsNone(self.decide(pre("sess-B-123456", "Bash", str(self.t), command=land)))
        mixed = f"git -C '{self.proj}' merge --ff-only wt/x && git -C '{self.proj}' commit -m y"
        self.assertEqual(self.decide(pre("sess-B-123456", "Bash", str(self.t), command=mixed))["permissionDecision"], "deny")
        self.assertEqual(self.decide(pre("sess-B-123456", "Bash", str(self.t), command=f"git -C '{self.proj}' merge wt/x"))
                         ["permissionDecision"], "deny")

    def test_bash_targets_follow_cd_per_segment(self):
        self.assertEqual(guard.bash_targets("cd /r && sed -i s/a/b/ f", "/c"), ["/r"])
        self.assertEqual(guard.bash_targets("touch a; cd /r && git status", "/c"), ["/c"])
        self.assertEqual(guard.bash_targets("git -C /a commit -m x && git -C /b add .", "/c"), ["/a", "/b"])
        self.assertEqual(guard.bash_targets("git -C /a push", "/c"), [])

    def test_exempt_shared_places(self):
        p = str(self.t / "shared" / "log.md")
        guard.handle(pre("sess-A", "Edit", "", file_path=p), self.cfg)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=p)))

    def test_stop_releases_once_the_edit_is_committed(self):
        """RAII-style scope: the lease lives while this session has uncommitted edits, then ends at Stop."""
        self.f = str(self.proj / "x.txt")
        Path(self.f).write_text("one\n")
        _git(self.proj, "add", "x.txt")
        _git(self.proj, "commit", "-q", "-m", "base")
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        Path(self.f).write_text("two\n")
        guard.handle({"hook_event_name": "Stop", "session_id": "sess-A"}, self.cfg)
        self.assertIsNotNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))   # still dirty: kept
        _git(self.proj, "commit", "-qam", "edit")
        guard.handle({"hook_event_name": "Stop", "session_id": "sess-A"}, self.cfg)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))      # committed: released

    def _base_commit(self):
        Path(self.f).write_text("one\n")
        _git(self.proj, "add", "x.txt")
        _git(self.proj, "commit", "-q", "-m", "base")

    def test_bash_only_lease_ignores_colleague_dirty_file(self):
        """2026-10-31: a Bash-only holder that moved to its worktree stayed locked because an unrelated dirty file
        in the main checkout counted as 'uncommitted work'. Dirty lines that existed before the lease do not count."""
        self._base_commit()
        (self.proj / "other.txt").write_text("someone else's change\n")          # untracked, not this session's
        guard.handle(pre("sess-A", "Bash", str(self.proj), command="touch mine.txt"), self.cfg)
        (self.proj / "mine.txt").write_text("mine\n")       # what the approved Bash command does
        _git(self.proj, "add", "mine.txt")
        _git(self.proj, "commit", "-q", "-m", "mine")
        guard.handle({"hook_event_name": "Stop", "session_id": "sess-A"}, self.cfg)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))   # released

    def test_bash_only_lease_keeps_its_own_uncommitted_file(self):
        self._base_commit()
        (self.proj / "other.txt").write_text("someone else's change\n")
        guard.handle(pre("sess-A", "Bash", str(self.proj), command="touch mine.txt"), self.cfg)
        (self.proj / "mine.txt").write_text("mine\n")
        guard.handle({"hook_event_name": "Stop", "session_id": "sess-A"}, self.cfg)   # mine.txt still uncommitted
        self.assertIsNotNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))

    def test_session_end_releases(self):
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        guard.handle({"hook_event_name": "SessionEnd", "session_id": "sess-A"}, self.cfg)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))

    def _age_lease(self, minutes):
        leases = core.load_json(guard._path(self.cfg), {})
        for v in leases.values():
            v["last"] = (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        core.save_json(guard._path(self.cfg), leases)

    def test_headless_takes_over_idle_interactive_lease(self):
        """T-20261025-ROBUST 5: a background run waits only 15 idle minutes, not the full 45."""
        self.assertIsNone(self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f)))
        os.environ["AISYNC_HEADLESS"] = str(self.t)
        try:
            d = self.decide(pre("bg-run", "Edit", str(self.t), file_path=self.f))
            self.assertEqual(d["permissionDecision"], "deny")          # holder edited just now
            self.assertIn("log.md", d["permissionDecisionReason"])
            self._age_lease(16)
            self.assertIsNone(self.decide(pre("bg-run", "Edit", str(self.t), file_path=self.f)))
        finally:
            os.environ.pop("AISYNC_HEADLESS", None)
        log = (self.cfg.state / "guard.jsonl").read_text()
        self.assertIn('"decision": "takeover"', log)
        d = self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f))   # now the window is the one waiting
        self.assertEqual(d["permissionDecision"], "deny")

    def test_interactive_never_takes_over_while_holder_has_uncommitted_work(self):
        Path(self.f).write_text("unfinished\n", encoding="utf-8")          # sess-A's own uncommitted change
        self.assertIsNone(self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f)))
        self._age_lease(30)
        d = self.decide(pre("sess-B", "Edit", str(self.t), file_path=self.f))
        self.assertEqual(d["permissionDecision"], "deny")

    def test_idle_holder_with_no_work_is_released_without_asking(self):
        # decided 2026-11-01: no work left = the lock is released by itself, nobody has to be asked
        self.assertIsNone(self.decide(pre("sess-A", "Edit", str(self.t), file_path=self.f)))
        self._age_lease(30)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", str(self.t), file_path=self.f)))

    def test_lease_expires(self):
        guard.acquire(self.cfg, "sess-A", str(self.proj), ttl_min=45)
        leases = core.load_json(self.cfg.state / "leases.json", {})
        leases[str(self.proj)]["expires"] = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        core.save_json(self.cfg.state / "leases.json", leases)
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))

    def test_fail_open_and_no_session(self):
        self.assertEqual(guard.handle({"hook_event_name": "PreToolUse", "tool_name": "Edit"}, self.cfg), "")
        self.assertEqual(guard.handle(pre("s", "Edit", "", file_path=None), self.cfg), "")
        self.assertEqual(guard.handle(pre("s", "Read", "", file_path=self.f), self.cfg), "")

    def test_cli_lease_status_and_release(self):
        guard.handle(pre("sess-A", "Edit", "", file_path=self.f), self.cfg)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["lease", "status"])
            cli.main(["lease", "release", "--key", str(self.proj)])
        self.assertIn(str(self.proj), buf.getvalue())
        self.assertIn("released 1", buf.getvalue())
        self.assertIsNone(self.decide(pre("sess-B", "Edit", "", file_path=self.f)))


class ReleaseTest(Base):
    """Mutation testing (11/02) showed release() could free every lease, or skip saving, and no test noticed."""

    def _two(self):
        a, b = self.t / "repo-a", self.t / "repo-b"
        a.mkdir(); b.mkdir()
        guard.acquire(self.cfg, "sess-A", str(a.resolve()), ttl_min=45)
        guard.acquire(self.cfg, "sess-B", str(b.resolve()), ttl_min=45)
        return str(a.resolve()), str(b.resolve())

    def test_release_by_key_frees_only_that_key_and_is_saved(self):
        a, b = self._two()
        self.assertEqual(guard.release(self.cfg, key=a), [a])
        left = guard.load(self.cfg)
        self.assertNotIn(a, left)
        self.assertIn(b, left)

    def test_release_by_session_frees_only_that_session_and_is_saved(self):
        a, b = self._two()
        self.assertEqual(guard.release(self.cfg, session="sess-B"), [b])
        left = guard.load(self.cfg)
        self.assertIn(a, left)
        self.assertNotIn(b, left)

    def test_release_with_nothing_matching_frees_nothing(self):
        a, b = self._two()
        self.assertEqual(guard.release(self.cfg, key=str(self.t / "elsewhere")), [])
        self.assertEqual(set(guard.load(self.cfg)), {a, b})


class StaleLeaseTest(unittest.TestCase):
    """decided 2026-11-01: a holder with no work left (folder gone, or idle with nothing uncommitted) never keeps the lock."""

    def test_holder_whose_folder_is_gone_is_released_at_once(self):
        now = datetime(2026, 11, 1, 15, 56)
        cur = {"session": "cf8e1dc2aaaa", "last": (now - timedelta(minutes=20)).isoformat(), "cwd": "/no/such/worktree/xyz",
               "files": []}
        self.assertTrue(guard._holder_done(cur, "/no/such/repo/xyz", now))

    def test_recent_holder_is_not_released(self):
        now = datetime(2026, 11, 1, 15, 56)
        cur = {"session": "cf8e1dc2aaaa", "last": (now - timedelta(seconds=30)).isoformat(), "cwd": "/no/such/worktree/xyz"}
        self.assertFalse(guard._holder_done(cur, "/no/such/repo/xyz", now))

    def test_corrupt_or_missing_timestamp_never_releases(self):
        now = datetime(2026, 11, 1, 15, 56)
        self.assertFalse(guard._holder_done({"session": "x", "cwd": "/no/such"}, "/no/such/repo", now))
        self.assertFalse(guard._holder_done({"session": "x", "last": "not-a-time", "cwd": "/no/such"}, "/no/such/repo", now))
        self.assertFalse(guard._holder_done({}, "/no/such/repo", now))

    def test_unknown_state_stays_conservative(self):
        # cwd empty and no repo on disk: the state cannot be read, so the lease is NOT released (unknown = still open)
        now = datetime(2026, 11, 1, 15, 56)
        cur = {"session": "x", "last": (now - timedelta(minutes=16)).isoformat(), "cwd": "", "files": []}
        self.assertFalse(guard._holder_done(cur, "/no/such/repo", now))

    def test_no_work_and_no_live_process_releases_after_one_minute(self):
        import subprocess
        import tempfile
        import unittest.mock as um
        now = datetime(2026, 11, 1, 15, 56)
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)                       # a clean, readable repo
            cur = {"session": "x", "last": (now - timedelta(minutes=2)).isoformat(), "cwd": d, "files": []}
            with um.patch.object(guard, "_holder_alive", return_value=False):
                self.assertTrue(guard._holder_done(cur, d, now))
            with um.patch.object(guard, "_holder_alive", return_value=True):
                self.assertFalse(guard._holder_done(cur, d, now))
            with um.patch.object(guard, "_holder_alive", return_value=None):      # every read failed: keep, do not guess
                self.assertFalse(guard._holder_done(cur, d, now))

