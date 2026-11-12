"""Exact behavior of the session guard (mutation run 2026-11-02: 349 of 865 mutants not caught).
test_guard.py checks that the right session is allowed or denied. These pin what a lease holds, what a decision logs,
what a deny says, and how the Claude process read behaves, with the clock and the host held still."""
from __future__ import annotations

import json
import os
import subprocess
import unittest.mock as um
from datetime import datetime, timedelta
from pathlib import Path

from ai_sync import guard

from test_ai_sync import Base, _git

NOW = datetime.fromisoformat("2026-11-02T10:00:00-06:00")
LONG = "abcdefgh-1234"          # 8-char prefix abcdefgh
OTHER = "zyxwvuts-9876"


def iso(minutes=0):
    return (NOW + timedelta(minutes=minutes)).isoformat(timespec="seconds")


class Frozen(Base):
    def setUp(self):
        super().setUp()
        os.environ["AISYNC_LEASE_EXEMPT"] = str(self.t / "shared")
        (self.t / "shared").mkdir()
        self.addCleanup(os.environ.pop, "AISYNC_LEASE_EXEMPT", None)
        os.environ.pop("AISYNC_HEADLESS", None)
        for name, value in (("_now", NOW), ("_holder_alive", False)):
            p = um.patch.object(guard, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.key = str(self.proj.resolve())

    def leases(self):
        return json.loads((self.home / "state" / "leases.json").read_text())

    def put(self, **over):
        v = {"session": OTHER, "since": iso(-30), "last": iso(-30), "cwd": str(self.t / "gone"), "files": [],
             "baseline": [], "expires": iso(30), **over}
        (self.home / "state").mkdir(exist_ok=True)
        (self.home / "state" / "leases.json").write_text(json.dumps({self.key: v}))
        return v

    def log(self):
        p = self.home / "state" / "guard.jsonl"
        if not p.exists():
            return []
        return [{k: v for k, v in json.loads(ln).items() if k != "ts"} for ln in p.read_text().splitlines()]


class AcquireTest(Frozen):
    def test_new_lease_has_every_field(self):
        (self.proj / "dirty.txt").write_text("someone else's")
        ok, lease = guard.acquire(self.cfg, LONG, self.key, "/some/cwd", touched="/a/b.py")
        self.assertTrue(ok)
        want = {"session": LONG, "since": iso(), "last": iso(), "cwd": "/some/cwd", "files": ["/a/b.py"],
                "baseline": ["?? dirty.txt"], "expires": iso(45)}
        self.assertEqual(lease, want)
        self.assertEqual(self.leases(), {self.key: want})

    def test_ttl_and_defaults(self):
        _, lease = guard.acquire(self.cfg, LONG, self.key, ttl_min=5)
        self.assertEqual((lease["expires"], lease["cwd"], lease["files"]), (iso(5), "", []))
        self.assertNotIn("took_over_from", lease)

    def test_folder_outside_git_has_an_empty_baseline(self):
        d = self.t / "plain"
        d.mkdir()
        _, lease = guard.acquire(self.cfg, LONG, str(d))
        self.assertEqual(lease["baseline"], [])

    def test_refresh_keeps_since_and_baseline_and_merges_files_sorted(self):
        guard.acquire(self.cfg, LONG, self.key, touched="/z.py")
        (self.proj / "mine.txt").write_text("after the lease began")
        with um.patch.object(guard, "_now", return_value=NOW + timedelta(minutes=10)):
            ok, lease = guard.acquire(self.cfg, LONG, self.key, "/c2", touched="/a.py")
        self.assertTrue(ok)
        self.assertEqual(lease, {"session": LONG, "since": iso(), "last": iso(10), "cwd": "/c2", "files": ["/a.py", "/z.py"],
                                 "baseline": [], "expires": iso(55)})
        _, lease = guard.acquire(self.cfg, LONG, self.key)            # no file this time: the list stays
        self.assertEqual(lease["files"], ["/a.py", "/z.py"])

    def test_other_session_is_refused_and_the_lease_file_is_unchanged(self):
        (self.proj / "x.txt").write_text("holder's uncommitted edit")
        held = self.put(cwd=str(self.t), files=[str(self.proj / "x.txt")])
        ok, lease = guard.acquire(self.cfg, LONG, self.key)
        self.assertEqual((ok, lease), (False, held))
        self.assertEqual(self.leases(), {self.key: held})
        self.assertEqual(self.log(), [])

    def _busy(self, idle_min):
        (self.proj / "x.txt").write_text("holder's uncommitted edit")
        return self.put(last=iso(-idle_min), cwd=str(self.t), files=[str(self.proj / "x.txt")])

    def test_takeover_boundary_is_at_least_the_given_idle(self):
        self._busy(14)
        self.assertFalse(guard.acquire(self.cfg, LONG, self.key, takeover_idle_min=15)[0])
        self._busy(15)
        ok, lease = guard.acquire(self.cfg, LONG, self.key, "/c", takeover_idle_min=15, touched="/t")
        self.assertTrue(ok)
        self.assertEqual(lease, {"session": LONG, "since": iso(), "took_over_from": OTHER, "last": iso(), "cwd": "/c",
                                 "files": ["/t"], "baseline": [" M x.txt"], "expires": iso(45)})
        self.assertEqual(self.log(), [])                               # a timed takeover of a busy holder is not "stale"

    def test_unreadable_last_edit_counts_as_not_idle(self):
        (self.proj / "x.txt").write_text("holder's uncommitted edit")
        self.put(last="not a time", cwd=str(self.t), files=[str(self.proj / "x.txt")])
        self.assertFalse(guard.acquire(self.cfg, LONG, self.key, takeover_idle_min=15)[0])
        self.assertTrue(guard.acquire(self.cfg, LONG, self.key, takeover_idle_min=0)[0])

    def test_stale_holder_is_taken_over_and_logged(self):
        self.put(last=iso(-90))                                        # its folder is gone
        ok, lease = guard.acquire(self.cfg, LONG, self.key, "/c")
        self.assertTrue(ok)
        self.assertEqual(lease["took_over_from"], OTHER)
        self.assertEqual(self.log(), [{"session": "abcdefgh", "event": "stale_takeover", "key": self.key, "holder": "zyxwvuts",
                                       "idle_min": 90, "cwd": str(self.t / "gone")}])


class HoldersTest(Frozen):
    def test_holder_done_boundary(self):
        gone = str(self.t / "gone")
        self.assertFalse(guard._holder_done({"last": iso(), "cwd": gone}, self.key, NOW + timedelta(seconds=59)))
        self.assertTrue(guard._holder_done({"last": iso(-1), "cwd": gone}, self.key, NOW))     # exactly one minute
        self.assertTrue(guard._holder_done({"last": iso(-1)}, self.key, NOW))                  # no folder, clean repo, nobody alive

    def test_holder_done_only_when_nobody_is_known_alive(self):
        cur = {"last": iso(-5), "cwd": str(self.t)}
        guard._holder_alive.return_value = None                      # every read failed
        self.assertFalse(guard._holder_done(cur, self.key, NOW))
        guard._holder_alive.return_value = True
        self.assertFalse(guard._holder_done(cur, self.key, NOW))
        guard._holder_alive.return_value = False
        self.assertTrue(guard._holder_done(cur, self.key, NOW))
        guard._holder_alive.assert_called_with(str(self.t))

    def test_load_keeps_only_live_well_formed_leases(self):
        (self.home / "state").mkdir(exist_ok=True)
        good = {"session": "g", "expires": iso(1)}
        (self.home / "state" / "leases.json").write_text(json.dumps({
            "good": good, "ended": {"expires": iso(0)}, "past": {"expires": iso(-1)}, "bad": {"expires": "x"},
            "none": {"expires": None}, "nokey": {"session": "s"}}))
        self.assertEqual(guard.load(self.cfg), {"good": good})
        self.assertEqual(guard._path(self.cfg), self.home / "state" / "leases.json")

    def test_release_resolves_tilde_and_needs_a_match(self):
        with um.patch.dict(os.environ, {"HOME": str(self.t)}):
            self.put()
            self.assertEqual(guard.release(self.cfg, key="~/proj"), [self.key])
        self.assertEqual(self.leases(), {})
        self.put()
        self.assertEqual(guard.release(self.cfg), [])                 # neither key nor session: frees nothing
        self.assertEqual(list(self.leases()), [self.key])

    def test_release_finished_only_drops_this_sessions_clean_leases_and_saves(self):
        held = self.put(session=LONG)
        self.assertEqual(guard.release_finished(self.cfg, session="someone-else"), [])
        self.assertEqual(self.leases(), {self.key: held})
        self.assertEqual(guard.release_finished(self.cfg, session=LONG), [self.key])
        self.assertEqual(self.leases(), {})

    def test_scope_open_with_a_missing_baseline_counts_every_dirty_line(self):
        (self.proj / "new.txt").write_text("x")
        self.assertTrue(guard._scope_open(self.key, {}))
        self.assertTrue(guard._scope_open(self.key, {"baseline": None}))
        self.assertFalse(guard._scope_open(self.key, {"baseline": ["?? new.txt"]}))
        self.assertTrue(guard._scope_open(str(self.t / "not-a-repo"), {}))              # unknown stays open


class GitReadsTest(Frozen):
    def test_dirty_looks_at_the_files_given(self):
        (self.proj / "a.txt").write_text("1")
        _git(self.proj, "add", "a.txt")
        _git(self.proj, "commit", "-q", "-m", "a")
        (self.proj / "b.txt").write_text("untracked")
        self.assertFalse(guard._dirty(self.key, [str(self.proj / "a.txt")]))
        self.assertTrue(guard._dirty(self.key, [str(self.proj / "b.txt")]))
        self.assertTrue(guard._dirty(self.key, []))                  # no record: the whole repo counts
        self.assertTrue(guard._dirty(str(self.t / "not-a-repo"), []))

    def test_dirty_and_status_exact_calls_and_failures(self):
        calls = []

        def fake(argv, **k):
            calls.append((argv, k))
            return subprocess.CompletedProcess(argv, 0, " M a\n?? b\n", "")
        opts = {"capture_output": True, "text": True, "timeout": 15}
        with um.patch.object(guard.subprocess, "run", fake):
            self.assertTrue(guard._dirty("K", ["f1", "f2"]))
            self.assertTrue(guard._dirty("K", []))
            self.assertEqual(guard._status_lines("K"), [" M a", "?? b"])
        self.assertEqual(calls, [(["git", "-C", "K", "status", "--porcelain", "--", "f1", "f2"], opts),
                                 (["git", "-C", "K", "status", "--porcelain", "--", "."], opts),
                                 (["git", "-C", "K", "status", "--porcelain"], opts)])
        with um.patch.object(guard.subprocess, "run", side_effect=OSError("no git")):
            self.assertTrue(guard._dirty("K", []))
            self.assertIsNone(guard._status_lines("K"))
        with um.patch.object(guard.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 15)):
            self.assertTrue(guard._dirty("K", []))
            self.assertIsNone(guard._status_lines("K"))
        with um.patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, "x\n", "")):
            self.assertTrue(guard._dirty("K", []))
            self.assertIsNone(guard._status_lines("K"))
        with um.patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "  \n", "")):
            self.assertFalse(guard._dirty("K", []))


class ProcessReadTest(Base):
    """Who is alive is read from lsof, then pgrep plus lsof, and a failed read is never a verdict."""

    def test_lsof_cwds_exact(self):
        with um.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "p12\nfcwd\nn/a b\nn/c\n", "")) as run:
            self.assertEqual(guard._lsof_cwds(), ["/a b", "/c"])
        run.assert_called_once_with(["lsof", "-a", "-c", "claude", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=15)
        for boom in (OSError("no lsof"), subprocess.TimeoutExpired("lsof", 15)):
            with um.patch.object(subprocess, "run", side_effect=boom):
                self.assertIsNone(guard._lsof_cwds())

    def test_ps_cwds_exact(self):
        replies = iter([subprocess.CompletedProcess([], 0, "11\n22\n", ""),
                        subprocess.CompletedProcess([], 0, "p11\nn/one\n", ""),
                        subprocess.CompletedProcess([], 0, "p22\nfcwd\nn/two\n", "")])
        with um.patch.object(subprocess, "run", side_effect=lambda *a, **k: next(replies)) as run:
            self.assertEqual(guard._ps_cwds(), ["/one", "/two"])
        self.assertEqual(run.call_args_list, [
            um.call(["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=10),
            um.call(["lsof", "-a", "-p", "11", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=10),
            um.call(["lsof", "-a", "-p", "22", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=10)])
        with um.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(guard._ps_cwds(), [])                    # nobody running
        for boom in (OSError("x"), subprocess.TimeoutExpired("pgrep", 10)):
            with um.patch.object(subprocess, "run", side_effect=boom):
                self.assertIsNone(guard._ps_cwds())

    def _alive(self, cwd, lsof, ps, **kw):
        with um.patch.object(guard, "_lsof_cwds", side_effect=lsof), um.patch.object(guard, "_ps_cwds", side_effect=ps), \
                um.patch("time.sleep") as sleep:
            return guard._holder_alive(cwd, **kw), sleep

    def test_holder_alive_decisions(self):
        self.assertIs(guard._holder_alive(""), False)                 # nothing to look for
        self.assertIs(self._alive("/w", [["/w"]], [])[0], True)
        self.assertIs(self._alive("/w", [["/w/sub/dir"]], [])[0], True)
        self.assertIs(self._alive("/w/", [["/w/sub"]], [])[0], True)
        self.assertIs(self._alive("/w", [["/wx", "/other"]], [])[0], False)     # a sibling is not inside
        self.assertIs(self._alive("/w", [[]], [])[0], False)

    def test_holder_alive_falls_back_and_retries_before_giving_up(self):
        self.assertIs(self._alive("/w", [None], [["/w"]])[0], True)   # lsof failed: pgrep route decides
        got, sleep = self._alive("/w", [None, None, None], [None, None, None])
        self.assertIsNone(got)
        self.assertEqual(sleep.call_args_list, [um.call(0.5)] * 3)
        got, sleep = self._alive("/w", [None, ["/w"]], [None])         # second round reads fine
        self.assertIs(got, True)
        self.assertEqual(sleep.call_count, 1)
        got, sleep = self._alive("/w", [None], [None], tries=1)
        self.assertIsNone(got)
        self.assertEqual(sleep.call_count, 1)


class MessagesTest(Frozen):
    def test_hm(self):
        self.assertEqual(guard._hm("2026-11-02T09:05:01-06:00"), "09:05")
        self.assertEqual(guard._hm("2026-11-02T09:05"), "09:05")
        self.assertEqual(guard._hm("2026-11-02T09:0"), "2026-11-02T09:0")     # too short to cut

    def test_status_text(self):
        self.assertEqual(guard.status_text(self.cfg), "no active session leases")
        (self.home / "state").mkdir(exist_ok=True)
        (self.home / "state" / "leases.json").write_text(json.dumps({
            "/k2": {"session": OTHER, "since": "2026-11-02T01:01:00", "last": "2026-11-02T03:03:00", "expires": iso(30)},
            "/k1": {"session": LONG, "since": "2026-11-02T02:02:00", "last": "2026-11-02T02:30:00", "expires": iso(30)}}))
        self.assertEqual(guard.status_text(self.cfg),
                         "abcdefgh  /k1  since 02:02  last 02:30  until 10:30\nzyxwvuts  /k2  since 01:01  last 03:03  until 10:30")

    LEASE = {"session": OTHER, "since": "2026-11-02T01:01:00", "last": "2026-11-02T02:02:00", "expires": "2026-11-02T03:03:00"}

    def head(self, key):
        return (f"【会话隔离】{key} 正被另一个 Claude Code 会话 zyxwvuts 占用（01:01 起，最近一次改动 02:02，没人再改的话 03:03 自动释放）。"
                "只读不受影响：看文件、grep/rg、git status/log/diff/show 照常可以用，只拦改动。")

    def test_deny_reason_in_a_repo_gives_the_worktree_route(self):
        k = self.key
        self.assertEqual(guard.deny_reason(k, self.LEASE, LONG), self.head(k) + (
            f"不用等：要改，就一条命令开自己的 worktree：`aisync wt start --key '{k}' --session {LONG}`，"
            f"然后所有改动、测试、commit 都写在打印出来的路径下（它是单独的锁，{k}-wt/abcdefgh）。"
            "没做完的改动在 worktree 的分支上提交，不要用 git stash（所有 worktree 共用一个 stash）。"
            f"做完一条命令落地并清理：`aisync wt land --key '{k}' --session {LONG}`。"
            f"只有确认那个会话已经结束，才可以运行 `aisync lease release --key '{k}'` 并告诉用户。"))

    def test_deny_reason_without_a_session_or_outside_git_says_hand_off(self):
        tail = ("本会话不要改这里：换别的仓库或文件做；要交接就用 ListAgents 找到那个会话，用 SendMessage 商量。"
                "只有确认那个会话已经结束，才可以运行 `aisync lease release --key '{k}'` 并告诉用户。")
        self.assertEqual(guard.deny_reason(self.key, self.LEASE), self.head(self.key) + tail.format(k=self.key))
        plain = str(self.t / "plain.txt")
        self.assertEqual(guard.deny_reason(plain, self.LEASE, LONG), self.head(plain) + tail.format(k=plain))
        self.assertEqual(guard.worktree_path("/r", LONG), "/r-wt/abcdefgh")

    def test_log_line_rollover_and_failure(self):
        path = self.home / "state" / "guard.jsonl"
        (self.home / "state").mkdir(exist_ok=True)
        guard._log(self.cfg, a="第一")
        raw = path.read_text()
        self.assertTrue(raw.endswith("}\n") and "第一" in raw)
        rec = json.loads(raw)
        self.assertEqual(list(rec), ["ts", "a"])
        self.assertIn("T", rec["ts"])
        guard._log(self.cfg, a=2)
        self.assertEqual(len(path.read_text().splitlines()), 2)
        rolled = path.with_suffix(".jsonl.1")
        path.write_bytes(b"x" * (2 * 1024 * 1024))                   # exactly the limit: stays
        guard._log(self.cfg, a=3)
        self.assertFalse(rolled.exists())
        guard._log(self.cfg, a=4)                                    # now over it: moved aside, new file
        self.assertTrue(rolled.exists() and rolled.stat().st_size > 2 * 1024 * 1024)
        self.assertEqual(json.loads(path.read_text())["a"], 4)
        path.unlink()
        path.mkdir()                                                 # can't be opened for append
        guard._log(self.cfg, a=5)                                    # a log failure never stops the guard


class ExemptAndKeyTest(Base):
    def test_exempt_roots(self):
        with um.patch.dict(os.environ, {"AISYNC_LEASE_EXEMPT": f"{self.t}::~/x", "HOME": str(self.t)}):
            self.assertEqual(guard.exempt_roots(), [self.t.resolve(), (self.t / "x").resolve()])
        with um.patch.dict(os.environ, {"AISYNC_LEASE_EXEMPT": ""}):
            self.assertEqual(guard.exempt_roots(), [])                # an empty setting means none, not the default
        env = {k: v for k, v in os.environ.items() if k != "AISYNC_LEASE_EXEMPT"}
        with um.patch.dict(os.environ, {**env, "HOME": str(self.t)}, clear=True):
            roots = guard.exempt_roots()
        self.assertIn((self.t / ".ai-sync").resolve(), roots)
        self.assertIn(Path("/tmp").resolve(), roots)
        self.assertEqual(len(roots), len(guard._DEFAULT_EXEMPT))

    def test_key_for_each_way_of_naming_a_path(self):
        key = str(self.proj.resolve())
        with um.patch.dict(os.environ, {"AISYNC_LEASE_EXEMPT": str(self.t / "shared"), "HOME": str(self.t)}):
            (self.t / "shared").mkdir()
            (self.proj / "sub/deep").mkdir(parents=True)
            self.assertEqual(guard.key_for(self.proj / "sub/deep/f.py"), key)
            self.assertEqual(guard.key_for("sub/deep/f.py", str(self.proj)), key)
            self.assertEqual(guard.key_for(self.proj), key)                       # the root itself
            self.assertEqual(guard.key_for("~/proj/x.txt"), key)
            self.assertIsNone(guard.key_for(self.t / "shared"))                   # the exempt folder itself
            self.assertIsNone(guard.key_for("shared/f", str(self.t)))
            plain = (self.t / "plain.txt").resolve()
            self.assertEqual(guard.key_for(plain), str(plain))                    # outside git: the file itself
            cwd = os.getcwd()
            os.chdir(self.proj / "sub")
            try:
                self.assertEqual(guard.key_for("deep/f.py"), key)                 # no cwd given: the process's own
            finally:
                os.chdir(cwd)

    def test_bash_targets_table(self):
        with um.patch.dict(os.environ, {"HOME": "/h"}):
            table = [
                ("ls -la", "/w", []),
                ("git status", "/w", []),
                ("", "/w", []),
                ("git add x", "/w", ["/w"]),
                ("git add x", "", []),                                            # nowhere known: nothing to lock
                ("git -C /a commit -m x", "/w", ["/a"]),
                ("/usr/bin/git -C /a add x", "/w", ["/a"]),
                ("tar -C /a -xf f > out", "/w", ["/w"]),                          # -C of another tool is not a target
                ("cd /a && touch f", "/w", ["/a"]),
                ("cd /a; touch f; cd /b; rm g", "/w", ["/a", "/b"]),
                ("cd sub && touch f", "/w", ["/w/sub"]),
                ("cd sub && touch f", "", ["sub"]),
                ("cd ~/x && touch f", "/w", ["/h/x"]),
                ("cd /a > log", "/w", ["/w"]),                                    # the cd segment itself writes where it started
                ("cd", "/w", []),
                ("git -C /a push && git -C /a merge --ff-only b", "/w", []),      # landing locks nothing
                ("git pull --ff-only; git push origin main", "/w", []),
                ("git -C /a push; git -C /b add x", "/w", ["/b"]),
                ("echo 'it is > broken", "/w", ["/w"]),                           # a quote that never closes is still read
                ("git -C '/a b' add x", "/w", ["/a b"]),                          # 11/02 bug: a quoted path with a space read as no write
                ('git -C "/a b" commit -m x', "/w", ["/a b"]),
                ("git -C '/a b' push", "/w", []),
                ("git -C /a merge x", "/w", ["/a"]),                              # a real merge is not a landing
                ("git --no-pager commit -m x", "/w", ["/w"]),                     # 11/02 bug: git's own options hid the write
                ("git -c a=b commit -m x", "/w", ["/w"]),
                ("git -c 'a=b c' add x", "/w", ["/w"]),
                ("git -c a=b -C /a add x", "/w", ["/a"]),                         # and -C behind them is still the target
                ("git --no-pager -C '/a b' add x", "/w", ["/a b"]),
                ("git --git-dir /g add x", "/w", ["/w"]),
                ("git -c a=b status", "/w", []),                                  # reads stay free
                ("git --no-pager log", "/w", []),
                ("git --no-pager push", "/w", []),                                # landing stays free
                ("git -c a=b -C /a push", "/w", []),
                ("git merge --ff-only x; git merge y", "/w", ["/w"]),
            ]
            for command, cwd, want in table:
                self.assertEqual(guard.bash_targets(command, cwd), want, command)

    def test_seg_dirs(self):
        self.assertEqual(guard._seg_dirs(["cd", "/a;"]), ["/a"])
        self.assertEqual(guard._seg_dirs(["git", "-C", "/a&", "add"]), ["/a"])
        self.assertEqual(guard._seg_dirs(["git", "x", "-C", "/a"]), [])       # -C of a subcommand is not git's own
        self.assertEqual(guard._seg_dirs(["git", "-c", "a=b", "-C", "/a"]), ["/a"])
        self.assertEqual(guard._seg_dirs(["git", "--no-pager", "-C", "/a"]), ["/a"])
        self.assertEqual(guard._seg_dirs(["git", "-C", "/a", "-C", "/b"]), ["/a", "/b"])
        self.assertEqual(guard._seg_dirs(["-C", "/a"]), [])                   # nothing before it
        self.assertEqual(guard._seg_dirs(["cd"]), [])
        self.assertEqual(guard._seg_dirs(["git", "-C"]), [])
        self.assertEqual(guard._seg_dirs(["x", "cd", "/a", "git", "-C", "/b"]), ["/a", "/b"])


class HandleTest(Frozen):
    def pre(self, session, tool, cwd="", **inp):
        return {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool, "cwd": cwd, "tool_input": inp}

    def held(self):
        (self.proj / "x.txt").write_text("holder's uncommitted edit")
        return self.put(cwd=str(self.t), files=[str(self.proj / "x.txt")], last=iso(-5))

    def test_allow_records_the_edited_file_and_logs(self):
        f = str(self.proj / "a.py")
        self.assertEqual(guard.handle(self.pre(LONG, "Edit", str(self.t), file_path=f), self.cfg), "")
        lease = self.leases()[self.key]
        self.assertEqual((lease["session"], lease["files"], lease["cwd"]), (LONG, [f], str(self.t)))
        self.assertEqual(self.log(), [{"session": "abcdefgh", "tool": "Edit", "key": self.key, "decision": "allow", "holder": "abcdefgh"}])

    def test_every_edit_tool_and_path_key(self):
        for tool, field in (("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            guard.handle(self.pre(LONG + tool, tool, **{field: str(self.proj / "a.py")}), self.cfg)
            self.assertEqual(self.leases()[self.key]["session"], LONG + tool)
            guard.release(self.cfg, session=LONG + tool)

    def test_relative_edit_is_recorded_under_the_cwd_or_the_repo(self):
        guard.handle(self.pre(LONG, "Edit", str(self.proj), file_path="a.py"), self.cfg)
        self.assertEqual(self.leases()[self.key]["files"], [str(self.proj / "a.py")])

    def test_bash_records_no_file_and_only_inside_a_repo(self):
        guard.handle(self.pre(LONG, "Bash", str(self.proj), command="touch a"), self.cfg)
        self.assertEqual(self.leases()[self.key]["files"], [])
        guard.release(self.cfg, session=LONG)
        plain = self.t / "plain"
        plain.mkdir()
        self.assertEqual(guard.handle(self.pre(LONG, "Bash", str(plain), command="touch a"), self.cfg), "")
        self.assertEqual(self.leases(), {})
        self.assertEqual(guard.handle(self.pre(LONG, "Edit", file_path=str(plain / "a")), self.cfg), "")
        self.assertIn(str((plain / "a").resolve()), self.leases())      # an Edit outside git does lock its own file

    def test_things_that_are_not_edits_change_nothing(self):
        for payload in ({"hook_event_name": "PreToolUse", "session_id": LONG},
                        self.pre(LONG, "Read", file_path=str(self.proj / "a")),
                        self.pre(LONG, "Edit", file_path=""),
                        self.pre(LONG, "Edit"),
                        self.pre(LONG, "Bash", str(self.proj)),
                        self.pre(LONG, "Bash", str(self.proj), command="ls"),
                        {"hook_event_name": "PostToolUse", "session_id": LONG, "tool_name": "Edit", "tool_input": {"file_path": str(self.proj / "a")}},
                        {"session_id": LONG, "tool_name": "Edit", "tool_input": {"file_path": str(self.proj / "a")}}):
            self.assertEqual(guard.handle(payload, self.cfg), "")
        self.assertFalse((self.home / "state" / "leases.json").exists())
        self.assertEqual(self.log(), [])
        self.assertEqual(guard.handle(self.pre("", "Edit", file_path=str(self.proj / "a")), self.cfg), "")

    def test_no_cfg_given_loads_the_configured_one(self):
        guard.handle(self.pre(LONG, "Edit", file_path=str(self.proj / "a")))
        self.assertEqual(self.leases()[self.key]["session"], LONG)

    def test_deny_output_is_exact_and_stops_at_the_first_held_repo(self):
        held = self.held()
        out = guard.handle(self.pre(LONG, "Edit", str(self.t), file_path=str(self.proj / "b.py")), self.cfg)
        self.assertIn("【会话隔离】", out)                                 # readable Chinese, not \uXXXX
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": guard.deny_reason(self.key, held, LONG)}})
        self.assertEqual(self.log(), [{"session": "abcdefgh", "tool": "Edit", "key": self.key, "decision": "deny", "holder": "zyxwvuts"}])
        self.assertEqual(self.leases()[self.key], held)

    def test_all_targets_of_one_command_are_taken_in_order(self):
        second = self.t / "second"
        second.mkdir()
        _git(second, "init", "-q", "-b", "main")
        guard.handle(self.pre(LONG, "Bash", str(self.t), command=f"git -C {self.proj} add x; git -C {second} add y"), self.cfg)
        self.assertEqual(sorted(self.leases()), sorted([self.key, str(second.resolve())]))

    def test_headless_deny_says_what_to_do_and_interactive_does_not(self):
        held = self.held()
        with um.patch.dict(os.environ, {"AISYNC_HEADLESS": "/s"}):
            self.assertTrue(guard.headless())
            out = guard.handle(self.pre(LONG, "Edit", file_path=str(self.proj / "b")), self.cfg)
        self.assertFalse(guard.headless())
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(reason, guard.deny_reason(self.key, held, LONG) + (
            f"（后台任务：在信箱 log.md 写一行「<时间> | claude-code | blocked | <单号> | 被前台会话 zyxwvuts 挡住 {self.key}」，"
            f"这一项留着不勾，先做别的；它闲置满 {guard.HEADLESS_TAKEOVER_MIN} 分钟后后台会自动接过来）"))
        self.assertEqual(self.log()[-1], {"session": "abcdefgh", "tool": "Edit", "key": self.key, "decision": "deny",
                                          "holder": "zyxwvuts", "headless": True})

    def test_headless_takeover_is_logged_as_takeover(self):
        self.held()
        self.put(cwd=str(self.t), files=[str(self.proj / "x.txt")], last=iso(-guard.HEADLESS_TAKEOVER_MIN))
        with um.patch.dict(os.environ, {"AISYNC_HEADLESS": "/s"}):
            self.assertEqual(guard.handle(self.pre(LONG, "Edit", file_path=str(self.proj / "b")), self.cfg), "")
        self.assertEqual(self.log(), [{"session": "abcdefgh", "tool": "Edit", "key": self.key, "decision": "takeover",
                                       "holder": "zyxwvuts", "headless": True}])

    def test_interactive_session_gets_no_timed_takeover(self):
        self.held()
        self.put(cwd=str(self.t), files=[str(self.proj / "x.txt")], last=iso(-44))
        self.assertIn("deny", guard.handle(self.pre(LONG, "Edit", file_path=str(self.proj / "b")), self.cfg))

    def test_session_end_and_stop_log_only_when_they_released(self):
        end = {"hook_event_name": "SessionEnd", "session_id": LONG}
        stop = {"hook_event_name": "Stop", "session_id": LONG}
        self.assertEqual((guard.handle(end, self.cfg), guard.handle(stop, self.cfg)), ("", ""))
        self.assertEqual(self.log(), [])
        self.put(session=LONG)
        self.assertEqual(guard.handle(stop, self.cfg), "")
        self.assertEqual(self.log(), [{"session": "abcdefgh", "event": "Stop", "released": [self.key], "reason": "scope-end"}])
        self.put(session=LONG, files=[str(self.proj / "x.txt")])
        (self.proj / "x.txt").write_text("uncommitted")
        guard.handle(stop, self.cfg)
        self.assertEqual(list(self.leases()), [self.key])               # unfinished work keeps its lease at Stop...
        guard.handle(end, self.cfg)
        self.assertEqual(self.leases(), {})                             # ...and SessionEnd drops it anyway
        self.assertEqual(self.log()[-1], {"session": "abcdefgh", "event": "SessionEnd", "released": [self.key]})

    def test_any_internal_error_fails_open(self):
        with um.patch.object(guard, "acquire", side_effect=RuntimeError("bug")):
            self.assertEqual(guard.handle(self.pre(LONG, "Edit", file_path=str(self.proj / "a")), self.cfg), "")
