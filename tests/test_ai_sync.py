"""Tests for ai-sync. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ai_sync import cli, core, watch

BLOCK = "<!-- owner-preferences:start (shared) -->\n- rule A\n<!-- owner-preferences:end -->"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


def _append_many(home, n):
    os.environ["AISYNC_HOME"] = home
    cfg = core.load_config()
    for i in range(n):
        core.append_event(cfg, agent=f"p{os.getpid()}", kind="dev_log", summary=f"e{i}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "hub"
        self.home.mkdir()
        os.environ["AISYNC_HOME"] = str(self.home)
        os.environ["AISYNC_NO_NOTIFY"] = "1"
        # never read the machine's global git config (its core.hooksPath would add Agent trailers)
        self._git_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.t / "gitconfig-global")
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.a = self.t / "a.md"
        self.b = self.t / "b.md"
        self.a.write_text(f"# A head\n\n{BLOCK}\n\nA tail\n")
        self.b.write_text(f"{BLOCK}\n")
        (self.home / "preferences.md").write_text(f"# truth\n\n{BLOCK}\n")
        (self.home / "journal.jsonl").write_text("")
        self.proj = self.t / "proj"
        self.proj.mkdir()
        _git(self.proj, "init", "-q", "-b", "main")
        (self.proj / "x.txt").write_text("1")
        _git(self.proj, "add", "x.txt")
        _git(self.proj, "commit", "-q", "-m", "first")
        self.write_config()
        self.cfg = core.load_config()

    def write_config(self, extra=""):
        (self.home / "config.toml").write_text(
            f'debounce_secs = 0.2\nmax_wait_secs = 1\nmin_flush_interval_secs = 0\nreconcile_secs = 600\n'
            f'git_retries = 2\nbackoff_base_secs = 0.01\nfail_threshold = 2\n{extra}\n'
            f'[[target]]\nlabel = "a"\npath = "{self.a}"\n\n[[target]]\nlabel = "b"\npath = "{self.b}"\n\n'
            f'[[project]]\npath = "{self.proj}"\n')
        self.cfg = core.load_config()

    def tearDown(self):
        for k, v in self._git_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def set_truth(self, rule):
        p = self.home / "preferences.md"
        p.write_text(core.replace_block(p.read_text(), BLOCK.replace("rule A", rule)))

    def set_copy(self, path, rule):
        path.write_text(core.replace_block(path.read_text(), BLOCK.replace("rule A", rule)))


class JournalTests(Base):
    def test_append_chain_and_verify(self):
        e1 = core.append_event(self.cfg, agent="cursor", kind="dev_log", summary="one", files=["a.py"])
        e2 = core.append_event(self.cfg, agent="codex", kind="decision", summary="two")
        self.assertEqual(e1["prev"], "genesis")
        self.assertEqual(e2["prev"], e1["id"])
        self.assertEqual(core.verify_chain(self.cfg), [])

    def test_hand_edit_is_detected(self):
        core.append_event(self.cfg, agent="cursor", kind="dev_log", summary="one")
        j = self.home / "journal.jsonl"
        j.write_text(j.read_text().replace('"one"', '"ONE"'))
        self.assertTrue(any("edited by hand" in p for p in core.verify_chain(self.cfg)))

    def test_lost_event_is_detected(self):
        for s in ("a", "b", "c"):
            core.append_event(self.cfg, agent="x", kind="note", summary=s)
        j = self.home / "journal.jsonl"
        lines = j.read_text().splitlines()
        j.write_text("\n".join([lines[0], lines[2]]) + "\n")
        self.assertTrue(any("prev link broken" in p for p in core.verify_chain(self.cfg)))

    def test_validation(self):
        with self.assertRaises(ValueError):
            core.append_event(self.cfg, agent="", kind="dev_log", summary="x")
        with self.assertRaises(ValueError):
            core.append_event(self.cfg, agent="a", kind="bogus", summary="x")
        with self.assertRaises(ValueError):
            core.append_event(self.cfg, agent="a", kind="note", summary="  ")

    def test_concurrent_writers_keep_chain_intact(self):
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_append_many, args=(str(self.home), 15)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
        self.assertEqual(len(core.read_events(self.cfg)), 60)
        self.assertEqual(core.verify_chain(self.cfg), [])


class DistributeTests(Base):
    def test_first_run_in_sync(self):
        rep = core.distribute(self.cfg)
        self.assertEqual(sorted(rep["in_sync"]), ["a", "b"])

    def test_truth_change_propagates_and_keeps_rest_of_file(self):
        core.distribute(self.cfg)
        self.set_truth("rule B")
        rep = core.distribute(self.cfg)
        self.assertEqual(sorted(rep["updated"]), ["a", "b"])
        self.assertIn("rule B", self.a.read_text())
        self.assertTrue(self.a.read_text().startswith("# A head") and "A tail" in self.a.read_text())
        self.assertTrue(list((self.home / "backups").glob("a-*.bak")))

    def test_copy_edit_fast_forwards_into_truth_and_other_copies(self):
        core.distribute(self.cfg)
        self.set_copy(self.b, "rule from codex")
        rep = core.distribute(self.cfg)
        self.assertEqual(rep["adopted"], ["b"])
        self.assertIn("rule from codex", (self.home / "preferences.md").read_text())
        self.assertIn("rule from codex", self.a.read_text())
        ev = core.read_events(self.cfg)[-1]
        self.assertEqual((ev["kind"], ev["agent"]), ("rule_change", "b"))

    def test_both_sides_changed_is_a_conflict_and_nothing_is_overwritten(self):
        core.distribute(self.cfg)
        self.set_truth("rule truth")
        self.set_copy(self.b, "rule copy")
        rep = core.distribute(self.cfg)
        self.assertEqual(rep["conflicts"], ["b"])
        self.assertIn("rule copy", self.b.read_text())
        self.assertIn("rule truth", self.a.read_text())
        self.assertIn("b", core.load_json(self.cfg.state / "conflicts.json", {}))

    def test_two_copies_edited_differently_is_a_conflict(self):
        core.distribute(self.cfg)
        self.set_copy(self.a, "rule x")
        self.set_copy(self.b, "rule y")
        rep = core.distribute(self.cfg)
        self.assertEqual(sorted(rep["conflicts"]), ["a", "b"])
        self.assertIn("rule A", (self.home / "preferences.md").read_text())

    def test_resolve_take_target_and_take_truth(self):
        core.distribute(self.cfg)
        self.set_truth("rule truth")
        self.set_copy(self.b, "rule copy")
        core.distribute(self.cfg)
        rep = core.resolve(self.cfg, "b", "target")
        self.assertIn("rule copy", (self.home / "preferences.md").read_text())
        self.assertIn("rule copy", self.a.read_text())
        self.assertEqual(rep["open_conflicts"], [])
        # now the other ruling
        self.set_truth("rule t2")
        self.set_copy(self.a, "rule c2")
        core.distribute(self.cfg)
        core.resolve(self.cfg, "a", "truth")
        self.assertIn("rule t2", self.a.read_text())

    def test_missing_target_and_block(self):
        self.b.write_text("no markers here\n")
        self.a.unlink()
        rep = core.distribute(self.cfg)
        self.assertEqual(sorted(rep["missing"]), ["a", "b"])

    def test_symlinked_target_stays_symlink(self):
        real = self.t / "real.md"
        real.write_text(f"{BLOCK}\n")
        self.b.unlink()
        self.b.symlink_to(real)
        core.distribute(self.cfg)
        self.set_truth("rule S")
        core.distribute(self.cfg)
        self.assertTrue(self.b.is_symlink())
        self.assertIn("rule S", real.read_text())


class RenderHarvestTests(Base):
    def test_render_progress_and_blockers(self):
        (self.home / "agents.md").write_text(f"# hub\n\n{core.PROG_START}\n{core.PROG_END}\n")
        core.append_event(self.cfg, agent="cursor", kind="blocked", project="p", summary="waiting on API key")
        core.append_event(self.cfg, agent="trae", kind="dev_log", project="q", summary="did a | thing")
        core.render(self.cfg)
        text = (self.home / "agents.md").read_text()
        self.assertIn("waiting on API key", text)
        self.assertIn("did a / thing", text)
        core.append_event(self.cfg, agent="cursor", kind="unblocked", project="p", summary="got it")
        core.render(self.cfg)
        self.assertEqual(json.loads((self.cfg.state / "current.json").read_text())["open_blocked"], [])

    def test_render_is_stable_when_nothing_changed(self):
        core.append_event(self.cfg, agent="cursor", kind="note", summary="x")
        core.render(self.cfg)
        self.assertEqual(core.render(self.cfg)["changed"], [])

    def test_harvest_new_commits_with_agent_trailer_and_dedupe(self):
        self.assertEqual(core.harvest(self.cfg)["events"], 0)   # first sight: cursor only
        (self.proj / "y.txt").write_text("2")
        _git(self.proj, "add", "y.txt")
        _git(self.proj, "commit", "-q", "-m", "feat: add y", "-m", "Co-Authored-By: Claude <noreply@anthropic.com>")
        (self.proj / "z.txt").write_text("3")
        _git(self.proj, "add", "z.txt")
        _git(self.proj, "commit", "-q", "-m", "fix: mention kiro in subject only")
        self.assertEqual(core.harvest(self.cfg)["events"], 2)
        evs = core.read_events(self.cfg)
        self.assertEqual([(e["agent"], e["files"]) for e in evs],
                         [("claude-code", ["y.txt"]), ("git:t", ["z.txt"])])
        self.assertEqual(core.harvest(self.cfg)["events"], 0)

    def test_scan_dirs_picks_up_new_worktree(self):
        self.write_config(f'scan_dirs = ["{self.t}"]')
        core.harvest(self.cfg)
        wt = self.t / "proj.wt-x"
        _git(self.proj, "worktree", "add", "-q", "-b", "x", str(wt))
        self.assertIn(wt, core.project_list(self.cfg))
        core.harvest(self.cfg)                       # first sight of the worktree
        (wt / "w.txt").write_text("w")
        _git(wt, "add", "w.txt")
        _git(wt, "commit", "-q", "-m", "work in worktree", "-m", "Agent: trae")
        self.assertEqual(core.harvest(self.cfg)["events"], 1)
        ev = core.read_events(self.cfg)[-1]
        self.assertEqual((ev["agent"], ev["project"]), ("trae", "proj.wt-x"))
        self.assertTrue(any(p.endswith("logs/HEAD") and "worktrees" in p for p in watch.watch_paths(self.cfg)))

    def test_infer_agent(self):
        self.assertEqual(core.infer_agent("x\n\nAgent: cursor", "me"), "cursor")
        self.assertEqual(core.infer_agent("x\n\nCo-authored-by: Codex <c@x>", "me"), "codex")
        self.assertEqual(core.infer_agent("Use trae for this", "me"), "git:me")


class FlushTests(Base):
    def test_flush_commits_only_when_changed(self):
        core.append_event(self.cfg, agent="cursor", kind="note", summary="hello")
        r1 = core.flush(self.cfg)
        self.assertTrue(r1["committed"])
        self.assertIsNone(core.flush(self.cfg)["committed"])
        log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.home, capture_output=True, text=True).stdout
        self.assertIn("cursor note: hello", log)

    def test_push_to_bare_remote(self):
        bare = self.t / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        core.ensure_repo(self.cfg)
        _git(self.home, "remote", "add", "origin", str(bare))
        self.write_config('remote = "origin"')
        core.append_event(self.cfg, agent="x", kind="note", summary="push me")
        self.assertTrue(core.flush(self.cfg)["pushed"])

    def test_push_failures_pause_and_alert_then_resume(self):
        core.ensure_repo(self.cfg)
        _git(self.home, "remote", "add", "origin", str(self.t / "nope.git"))
        self.write_config('remote = "origin"')
        for i in range(2):
            core.append_event(self.cfg, agent="x", kind="note", summary=f"e{i}")
            self.assertFalse(core.flush(self.cfg)["pushed"])
        h = core.load_health(self.cfg)
        self.assertTrue(h["push_paused"])
        self.assertIn("push paused", (self.cfg.state / "ALERT.md").read_text())
        self.assertEqual(core.flush(self.cfg)["pushed"], "paused")
        core.resume(self.cfg)
        self.assertFalse(core.load_health(self.cfg)["push_paused"])

    def test_cycle_isolates_failures_and_pauses_after_threshold(self):
        (self.home / "preferences.md").write_text("no block")   # distribute will error-report
        os.chmod(self.home / "journal.jsonl", 0o644)
        broken = core.Config(**{**self.cfg.__dict__, "targets": self.cfg.targets})
        orig = core.render
        core.render = lambda cfg: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            for _ in range(2):
                rec = core.run_cycle(broken)
                self.assertFalse(rec["ok"])
                self.assertTrue(rec["steps"]["flush"]["ok"])            # later step still ran
        finally:
            core.render = orig
        self.assertTrue(core.load_health(self.cfg)["cycles_paused"])
        self.assertIn("sync paused", (self.cfg.state / "ALERT.md").read_text())


class WatchTests(Base):
    def test_event_triggers_one_debounced_cycle(self):
        core.distribute(self.cfg)
        th = threading.Thread(target=watch.run_watch, args=(self.cfg,), kwargs={"max_cycles": 2, "max_seconds": 10})
        th.start()
        time.sleep(0.5)
        for i in range(3):                                    # a burst -> one cycle
            core.append_event(self.cfg, agent="cursor", kind="note", summary=f"burst {i}")
            time.sleep(0.05)
        th.join(10)
        self.assertFalse(th.is_alive())
        runs = [r for r in core.read_runs(self.cfg) if "steps" in r]
        self.assertEqual([r["reason"] for r in runs], ["startup", "event"])
        log = subprocess.run(["git", "log", "--format=%s"], cwd=self.home, capture_output=True, text=True).stdout
        self.assertIn("3 event(s) from cursor", log)

    def test_idle_watch_does_nothing_and_stop_file_exits(self):
        (self.home / "STOP").write_text("")
        self.assertEqual(watch.run_watch(self.cfg, max_seconds=3), 0)
        runs = [r for r in core.read_runs(self.cfg) if "steps" in r]
        self.assertEqual(len(runs), 1)                        # startup only

    def test_watch_paths_include_targets_and_head_logs(self):
        paths = watch.watch_paths(self.cfg)
        self.assertIn(os.path.realpath(self.a), paths)
        self.assertTrue(any(p.endswith("logs/HEAD") for p in paths))


class CliTests(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_init_creates_hub_and_refuses_twice(self):
        os.environ["AISYNC_HOME"] = str(self.t / "fresh")
        seed = self.t / "seed.md"
        seed.write_text(f"x\n{BLOCK}\n")
        code, out = self.run_cli("init", "--seed-from", str(seed), "--projects-dir", str(self.t),
                                 "--target", f"a={self.a}", "--target", f"b={self.b}")
        self.assertEqual(code, 0, out)
        self.assertIn(f'path = "{self.a}"', (self.t / "fresh" / "config.toml").read_text())
        fresh = self.t / "fresh"
        for f in ("agents.md", "preferences.md", "config.toml", "journal.jsonl", ".gitignore", ".git"):
            self.assertTrue((fresh / f).exists(), f)
        self.assertEqual(core.load_config(fresh).scan_dirs, [self.t])
        self.assertEqual(self.run_cli("init", "--seed-from", str(seed))[0], 1)

    def test_init_without_block_fails(self):
        os.environ["AISYNC_HOME"] = str(self.t / "fresh2")
        seed = self.t / "seed.md"
        seed.write_text("nothing")
        self.assertEqual(self.run_cli("init", "--seed-from", str(seed), "--target", f"a={self.a}")[0], 2)

    def test_log_tail_verify_sync_status_resume(self):
        code, out = self.run_cli("log", "-a", "trae", "-m", "built page", "-p", "lab", "-f", "a.py,b.py", "--ref", "pr=3")
        self.assertEqual(code, 0, out)
        self.assertIn("built page", self.run_cli("tail")[1])
        self.assertEqual(self.run_cli("verify")[0], 0)
        self.assertEqual(self.run_cli("sync")[0], 0)
        code, out = self.run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("trae", out)
        code, out = self.run_cli("status", "--json")
        self.assertEqual(json.loads(out)["journal"]["events"], 1)
        self.assertEqual(self.run_cli("resume")[0], 0)

    def test_log_requires_agent(self):
        os.environ.pop("AISYNC_AGENT", None)
        self.assertEqual(self.run_cli("log", "-m", "x")[0], 2)

    def test_status_flags_conflict_and_resolve(self):
        core.distribute(self.cfg)
        self.set_truth("rule t")
        self.set_copy(self.b, "rule c")
        self.run_cli("sync")
        code, out = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT b", out)
        self.assertEqual(self.run_cli("resolve", "b", "--take", "truth")[0], 0)
        self.assertEqual(self.run_cli("resolve", "b", "--take", "truth")[0], 2)

    def test_watch_command(self):
        (self.home / "STOP").write_text("")
        self.assertEqual(self.run_cli("watch", "--max-seconds", "2")[0], 0)

    def test_version(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--version")


if __name__ == "__main__":
    unittest.main()
