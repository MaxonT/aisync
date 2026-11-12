"""Exact behavior of `aisync wt` (mutation run 2026-11-02: 240 of 595 mutants survived).
test_worktree.py runs the happy paths on real git. Here the git calls are recorded: landing is the step that can move
main, so which command runs, in which folder, in which order, is the thing to pin."""
from __future__ import annotations

import os
import subprocess
import unittest
import unittest.mock as um
from pathlib import Path

from ai_sync import core, guard, landq, worktree

from test_ai_sync import Base, _git

SID = "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb"
WT = Path("/r-wt/abcdef12")
BR = "wt/abcdef12"


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Script:
    """Stands in for worktree._git: replies by the longest matching command prefix, records every call."""

    def __init__(self):
        self.replies, self.calls = {}, []

    def on(self, *cmd, rc=0, out="", err="", then=None):
        self.replies[cmd] = [done(rc, out, err)] + ([then] if then else [])
        return self

    def __call__(self, repo, *args, timeout=120):
        self.calls.append((str(repo), *args))
        for n in range(len(args), 0, -1):
            got = self.replies.get(args[:n])
            if got:
                return got.pop(0) if len(got) > 1 else got[0]
        return done()


class GitHelperTest(unittest.TestCase):
    def test_git_exact_call(self):
        with um.patch.object(subprocess, "run", return_value=done()) as run:
            worktree._git(Path("/r"), "status", "--short")
            worktree._git("/r", "x", timeout=5)
        self.assertEqual(run.call_args_list, [
            um.call(["git", "-C", "/r", "status", "--short"], capture_output=True, text=True, timeout=120),
            um.call(["git", "-C", "/r", "x"], capture_output=True, text=True, timeout=5)])

    def test_names(self):
        self.assertEqual(worktree.path_for("/r", SID), WT)
        self.assertEqual(worktree.branch_for(SID), BR)


class CleanupTest(Base):
    def test_cleanup_removes_worktree_branch_and_only_its_own_lease(self):
        script = Script()
        with um.patch.object(worktree, "_git", script), um.patch.object(guard, "release") as rel:
            worktree._cleanup("/r", WT, BR, self.cfg)
        self.assertEqual(script.calls, [("/r", "worktree", "remove", "--force", str(WT)), ("/r", "branch", "-D", BR)])
        rel.assert_called_once_with(self.cfg, key=str(WT))

    def test_cleanup_without_cfg_loads_it(self):
        with um.patch.object(worktree, "_git", Script()), um.patch.object(guard, "release") as rel, \
                um.patch.object(core, "load_config", return_value="CFG"):
            worktree._cleanup("/r", WT, BR, None)
        self.assertEqual(rel.call_args.args, ("CFG",))


class FollowOriginTest(unittest.TestCase):
    def run_it(self, script):
        with um.patch.object(worktree, "_git", script):
            return worktree._follow_origin("/r")

    def test_not_on_main_touches_nothing(self):
        s = Script().on("rev-parse", "--abbrev-ref", "HEAD", out="wt/x\n")
        self.assertEqual(self.run_it(s), "；本机不在 main 上，未跟进")
        self.assertEqual(s.calls, [("/r", "rev-parse", "--abbrev-ref", "HEAD")])

    def test_on_main_fetches_then_fast_forwards_only(self):
        s = Script().on("rev-parse", "--abbrev-ref", "HEAD", out="main\n")
        self.assertEqual(self.run_it(s), "，本机 main 已跟上 GitHub")
        self.assertEqual(s.calls, [("/r", "rev-parse", "--abbrev-ref", "HEAD"), ("/r", "fetch", "--quiet", "origin"),
                                   ("/r", "merge", "--ff-only", "origin/main")])

    def test_cannot_fast_forward_says_so_and_never_merges_otherwise(self):
        s = Script().on("rev-parse", "--abbrev-ref", "HEAD", out="main\n").on("merge", rc=1)
        self.assertEqual(self.run_it(s), "；本机 main 无法快进到 GitHub（分叉或有冲突的未提交改动），GitHub 已是最新，本机需要单独修")
        self.assertEqual(s.calls[-1], ("/r", "merge", "--ff-only", "origin/main"))


class GateTest(unittest.TestCase):
    def test_no_check_sh_passes_without_running_anything(self):
        s = Script().on("rev-parse", "HEAD", out="SHA\n").on("cat-file", rc=128)
        with um.patch.object(worktree, "_git", s), um.patch.object(worktree.gate, "check_commit") as chk:
            self.assertEqual(worktree._gate(WT), "")
        self.assertEqual(s.calls, [(str(WT), "rev-parse", "HEAD"), (str(WT), "cat-file", "-e", "SHA:check.sh")])
        chk.assert_not_called()

    def test_check_runs_on_the_rebased_commit_against_origin_main(self):
        s = Script().on("rev-parse", "HEAD", out="SHA\n").on("rev-parse", "--verify", out="BASE\n")
        with um.patch.object(worktree, "_git", s), um.patch.object(worktree.gate, "check_commit", return_value=(True, "", 1.0)) as chk:
            self.assertEqual(worktree._gate(WT), "")
        chk.assert_called_once_with(WT, "SHA", "BASE")
        self.assertEqual(s.calls[2], (str(WT), "rev-parse", "--verify", "--quiet", "origin/main"))

    def test_no_origin_main_means_no_base(self):
        s = Script().on("rev-parse", "HEAD", out="SHA\n").on("rev-parse", "--verify", rc=1)
        with um.patch.object(worktree, "_git", s), um.patch.object(worktree.gate, "check_commit", return_value=(True, "", 1.0)) as chk:
            worktree._gate(WT)
        chk.assert_called_once_with(WT, "SHA", None)

    def test_refusal_names_the_failures_seconds_and_log(self):
        s = Script().on("rev-parse", "HEAD", out="SHA\n").on("rev-parse", "--verify", out="BASE\n")
        names = [f"t{i}" for i in range(7)]
        with um.patch.object(worktree, "_git", s), um.patch.object(worktree.gate, "check_commit", return_value=(False, "OUTPUT", 12.4)), \
                um.patch.object(worktree.gate, "failed_tests", return_value=names) as failed, \
                um.patch.object(worktree.gate, "state_dir", return_value=Path("/st")):
            msg = worktree._gate(WT)
        failed.assert_called_once_with("OUTPUT")
        self.assertEqual(msg, f"check.sh --fast 没过（12 秒），未落地，worktree 保留在 {WT}：t0；t1；t2；t3；t4；完整输出：/st/gate_logs/SHA.log")


class LandQueuedTest(Base):
    """fetch -> rebase -> check -> push, then follow and clean. Any refusal keeps the worktree."""

    def setUp(self):
        super().setUp()
        self.pushes = [done()]

    def run_it(self, script, gate="", attempts=3):
        lock = um.Mock(token="TOK")
        with um.patch.object(worktree, "_git", script), um.patch.object(worktree, "_gate", return_value=gate) as g, \
                um.patch.object(worktree, "_cleanup") as clean, \
                um.patch.object(worktree, "_follow_origin", return_value="<跟上>") as follow, \
                um.patch.object(subprocess, "run", side_effect=self.pushes) as push:
            got = worktree._land_queued("/r", WT, BR, self.cfg, attempts, lock)
        return got, g, clean, follow, push

    def base(self, ahead="2"):
        return Script().on("rev-list", out=ahead + "\n")

    def test_success_exact_sequence(self):
        s = self.base()
        got, g, clean, follow, push = self.run_it(s)
        self.assertEqual(got, (True, "已落地 2 个提交，已推送到 GitHub<跟上>"))
        self.assertEqual(s.calls, [(str(WT), "fetch", "--quiet", "origin"),
                                   (str(WT), "rev-list", "--count", f"origin/main..{BR}"),
                                   (str(WT), "rebase", "origin/main")])
        g.assert_called_once_with(WT)
        self.assertEqual(push.call_args.args, (["git", "-C", str(WT), "push", "origin", "HEAD:main"],))
        kw = push.call_args.kwargs
        self.assertEqual((kw["capture_output"], kw["text"], kw["timeout"]), (True, True, 600))
        self.assertEqual(kw["env"][landq.TOKEN_ENV], "TOK")                   # its hook must skip the queue it already holds
        self.assertEqual(kw["env"]["PATH"], os.environ["PATH"])               # and the rest of the environment is kept
        follow.assert_called_once_with("/r")
        clean.assert_called_once_with("/r", WT, BR, self.cfg)

    def test_fetch_failure_changes_nothing(self):
        s = Script().on("fetch", rc=1)
        got, _, clean, follow, push = self.run_it(s)
        self.assertEqual(got, (False, f"连不上 GitHub，未落地，worktree 保留在 {WT}"))
        self.assertEqual(len(s.calls), 1)
        clean.assert_not_called()
        follow.assert_not_called()
        push.assert_not_called()

    def test_nothing_ahead_only_cleans(self):
        for ahead in ("0", ""):
            s = self.base(ahead)
            got, _, clean, follow, push = self.run_it(s)
            self.assertEqual(got, (True, "没有新提交，只清理<跟上>"))
            clean.assert_called_once_with("/r", WT, BR, self.cfg)
            follow.assert_called_once_with("/r")
            push.assert_not_called()
            self.assertNotIn("rebase", [c[1] for c in s.calls])

    def test_rebase_conflict_aborts_and_keeps_the_worktree(self):
        s = self.base().on("rebase", "origin/main", rc=1, err="e" * 300 + "\n")
        got, g, clean, follow, push = self.run_it(s)
        self.assertEqual(got, (False, f"与 GitHub 上的 main 冲突，未落地，worktree 保留在 {WT}：{'e' * 200}"))
        self.assertEqual(s.calls[-1], (str(WT), "rebase", "--abort"))
        g.assert_not_called()
        clean.assert_not_called()
        push.assert_not_called()
        follow.assert_not_called()
        s = self.base().on("rebase", "origin/main", rc=1, out="from stdout\n")
        got, *_ = self.run_it(s)
        self.assertIn("：from stdout", got[1])                               # no stderr: stdout says why

    def test_gate_refusal_stops_before_the_push(self):
        got, _, clean, follow, push = self.run_it(self.base(), gate="没过")
        self.assertEqual(got, (False, "没过"))
        push.assert_not_called()
        clean.assert_not_called()
        follow.assert_not_called()

    def test_rejected_push_retries_from_the_fetch_then_gives_up(self):
        self.pushes = [done(1), done(1)]
        s = self.base()
        got, _, clean, follow, push = self.run_it(s, attempts=2)
        self.assertEqual(got, (False, f"推送 2 次都被拒（GitHub 上的 main 一直在变或拒收），未落地，worktree 保留在 {WT}"))
        self.assertEqual(push.call_count, 2)
        self.assertEqual([c[1] for c in s.calls], ["fetch", "rev-list", "rebase"] * 2)
        clean.assert_not_called()
        follow.assert_not_called()

    def test_second_try_can_succeed(self):
        self.pushes = [done(1), done()]
        got, *_ = self.run_it(self.base())
        self.assertEqual(got, (True, "已落地 2 个提交，已推送到 GitHub<跟上>"))


class LandLocalTest(Base):
    def run_it(self, script, gate=""):
        with um.patch.object(worktree, "_git", script), um.patch.object(worktree, "_gate", return_value=gate) as g, \
                um.patch.object(worktree, "_cleanup") as clean:
            return worktree._land_local("/r", WT, BR, self.cfg), g, clean

    def script(self, ahead="3"):
        return Script().on("rev-parse", "HEAD", out="BASE\n").on("rev-list", out=ahead + "\n")

    def test_clean_fast_forward(self):
        s = self.script()
        got, g, clean = self.run_it(s)
        self.assertEqual(got, (True, "已落地 3 个提交"))
        self.assertEqual(s.calls, [("/r", "rev-parse", "HEAD"), ("/r", "rev-list", "--count", f"BASE..{BR}"),
                                   ("/r", "merge", "--ff-only", BR)])
        g.assert_called_once_with(WT)
        clean.assert_called_once_with("/r", WT, BR, self.cfg)

    def test_nothing_ahead_only_cleans(self):
        for ahead in ("0", ""):
            got, g, clean = self.run_it(self.script(ahead))
            self.assertEqual(got, (True, "没有新提交，只清理"))
            clean.assert_called_once_with("/r", WT, BR, self.cfg)
            g.assert_not_called()

    def test_gate_refusal_merges_nothing(self):
        s = self.script()
        got, _, clean = self.run_it(s, gate="没过")
        self.assertEqual(got, (False, "没过"))
        self.assertNotIn("merge", [c[1] for c in s.calls])
        clean.assert_not_called()

    def test_main_moved_rebases_once_then_fast_forwards(self):
        s = self.script().on("merge", rc=1, then=done(0))
        got, _, clean = self.run_it(s)
        self.assertEqual(got, (True, "已落地 3 个提交"))
        self.assertEqual(s.calls[2:], [("/r", "merge", "--ff-only", BR), (str(WT), "rebase", "main"), ("/r", "merge", "--ff-only", BR)])
        clean.assert_called_once_with("/r", WT, BR, self.cfg)

    def test_rebase_conflict_aborts_and_keeps_the_worktree(self):
        s = self.script().on("merge", rc=1).on("rebase", "main", rc=1, err="c" * 300)
        got, _, clean = self.run_it(s)
        self.assertEqual(got, (False, f"与 main 冲突，未落地，worktree 保留在 {WT}：{'c' * 200}"))
        self.assertEqual(s.calls[-1], (str(WT), "rebase", "--abort"))
        clean.assert_not_called()
        s = self.script().on("merge", rc=1).on("rebase", "main", rc=1, out="from stdout")
        self.assertIn("：from stdout", self.run_it(s)[0][1])

    def test_still_no_fast_forward_after_the_rebase(self):
        s = self.script().on("merge", rc=1)
        got, _, clean = self.run_it(s)
        self.assertEqual(got, (False, f"无法快进 main，未落地，worktree 保留在 {WT}"))
        clean.assert_not_called()


class LandTest(Base):
    def setUp(self):
        super().setUp()
        self.key = str(self.proj.resolve())

    def test_messages_when_there_is_no_worktree_or_dirty_work(self):
        wt = worktree.path_for(self.key, SID)
        self.assertEqual(worktree.land(self.key, SID, self.cfg), (False, f"没有 worktree：{wt}（先运行 aisync wt start）"))
        worktree.start(self.key, SID)
        (wt / "left.txt").write_text("x")
        self.assertEqual(worktree.land(self.key, SID, self.cfg), (False, f"{wt} 还有未提交的改动：先在里面 commit，再落地"))

    def test_only_the_shared_symlinks_may_be_left_over(self):
        wt = worktree.path_for(self.key, SID)
        worktree.start(self.key, SID)
        for name in ("state", ".venv"):
            (wt / name).symlink_to(self.t)
        self.assertEqual(worktree.land(self.key, SID, self.cfg), (True, "没有新提交，只清理"))

    def test_no_origin_goes_the_local_way(self):
        worktree.start(self.key, SID)
        with um.patch.object(worktree, "_land_local", return_value=(True, "local")) as local, \
                um.patch.object(landq, "Lock") as lock:
            self.assertEqual(worktree.land(self.key, SID, self.cfg), (True, "local"))
        local.assert_called_once_with(self.key, worktree.path_for(self.key, SID), worktree.branch_for(SID), self.cfg)
        lock.assert_not_called()

    def test_queue_is_held_around_the_landing_and_always_released(self):
        worktree.start(self.key, SID)
        _git(self.key, "remote", "add", "origin", str(self.t / "nowhere"))
        wt, br = worktree.path_for(self.key, SID), worktree.branch_for(SID)
        with um.patch.object(landq, "Lock") as lock, um.patch.object(worktree, "_land_queued", return_value=(True, "q")) as q:
            lock.return_value.acquire.return_value = True
            self.assertEqual(worktree.land(self.key, SID, self.cfg, attempts=7), (True, "q"))
        lock.assert_called_once_with(self.key, f"wt land {Path(self.key).name} abcdef12")
        q.assert_called_once_with(self.key, wt, br, self.cfg, 7, lock.return_value)
        lock.return_value.release.assert_called_once_with()
        with um.patch.object(landq, "Lock") as lock, um.patch.object(worktree, "_land_queued", side_effect=RuntimeError("x")) as q:
            lock.return_value.acquire.return_value = True
            with self.assertRaises(RuntimeError):
                worktree.land(self.key, SID, self.cfg)
        lock.return_value.release.assert_called_once_with()
        with um.patch.object(landq, "Lock") as lock, um.patch.object(worktree, "_land_queued") as q:
            lock.return_value.acquire.return_value = False
            got = worktree.land(self.key, SID, self.cfg)
        self.assertEqual(got, (False, f"落地队列排了 {landq.WAIT_MIN} 分钟还没轮到，未落地，worktree 保留在 {wt}"))
        q.assert_not_called()
        lock.return_value.release.assert_not_called()


class StartTest(Base):
    def setUp(self):
        super().setUp()
        self.key = str(self.proj.resolve())

    def test_not_a_repo(self):
        d = self.t / "plain"
        d.mkdir()
        self.assertEqual(worktree.start(str(d), SID), (False, f"{d} 不是 git 仓库根目录"))
        self.assertFalse((self.t / "plain-wt").exists())

    def test_existing_worktree_is_returned_without_git(self):
        wt = worktree.path_for(self.key, SID)
        wt.mkdir(parents=True)
        with um.patch.object(worktree, "_git") as g:
            self.assertEqual(worktree.start(self.key, SID), (True, str(wt)))
        g.assert_not_called()

    def test_no_origin_branches_from_head_exact_commands(self):
        s = Script().on("show-ref", rc=1).on("remote", rc=1)
        with um.patch.object(worktree, "_git", s):
            got = worktree.start(self.key, SID)
        wt = worktree.path_for(self.key, SID)
        self.assertEqual(got, (True, str(wt)))
        self.assertTrue(wt.parent.is_dir())
        self.assertEqual(s.calls, [(self.key, "show-ref", "--verify", "--quiet", f"refs/heads/{worktree.branch_for(SID)}"),
                                   (self.key, "remote", "get-url", "origin"),
                                   (self.key, "worktree", "add", "-b", worktree.branch_for(SID), str(wt), "HEAD")])

    def test_origin_reachable_branches_from_origin_main(self):
        s = Script().on("show-ref", rc=1)
        with um.patch.object(worktree, "_git", s):
            worktree.start(self.key, SID)
        self.assertEqual(s.calls[1:4], [(self.key, "remote", "get-url", "origin"), (self.key, "fetch", "--quiet", "origin"),
                                        (self.key, "rev-parse", "--verify", "--quiet", "origin/main")])
        self.assertEqual(s.calls[4][-1], "origin/main")

    def test_failed_fetch_or_no_origin_main_falls_back_to_head(self):
        for s in (Script().on("show-ref", rc=1).on("fetch", rc=1), Script().on("show-ref", rc=1).on("rev-parse", rc=1)):
            with um.patch.object(worktree, "_git", s):
                worktree.start(self.key, SID)
            self.assertEqual(s.calls[-1][-1], "HEAD")

    def test_existing_branch_is_reused_not_recreated(self):
        s = Script().on("remote", rc=1)
        wt = worktree.path_for(self.key, SID)
        with um.patch.object(worktree, "_git", s):
            worktree.start(self.key, SID)
        self.assertEqual(s.calls[-1], (self.key, "worktree", "add", str(wt), worktree.branch_for(SID)))

    def test_add_failure_reports_the_tail_of_the_error(self):
        wt = worktree.path_for(self.key, SID)
        s = Script().on("worktree", rc=1, err="x" * 400 + "\n").on("remote", rc=1)
        with um.patch.object(worktree, "_git", s):
            self.assertEqual(worktree.start(self.key, SID), (False, "x" * 300))
        s = Script().on("worktree", rc=1, out="from stdout\n").on("remote", rc=1)
        with um.patch.object(worktree, "_git", s):
            self.assertEqual(worktree.start(self.key, SID), (False, "from stdout"))
        self.assertFalse(wt.exists())

    def test_shared_folders_link_to_the_main_checkout_and_real_ones_win(self):
        (self.proj / "state").mkdir()
        (self.proj / ".venv").mkdir()
        wt = worktree.path_for(self.key, SID)
        s = Script().on("remote", rc=1)

        def add(repo, *args, timeout=120):
            if args[:2] == ("worktree", "add"):
                wt.mkdir(parents=True)
                (wt / ".venv").mkdir()                       # the branch has its own
            return s(repo, *args)
        with um.patch.object(worktree, "_git", add):
            self.assertEqual(worktree.start(self.key, SID), (True, str(wt)))
        self.assertEqual(os.readlink(wt / "state"), str(Path(self.key) / "state"))
        self.assertFalse((wt / ".venv").is_symlink())

    def test_missing_shared_folder_gets_no_dangling_link(self):
        wt = worktree.path_for(self.key, SID)
        worktree.start(self.key, SID)
        self.assertFalse((wt / "state").is_symlink())
        self.assertFalse((wt / ".venv").is_symlink())
