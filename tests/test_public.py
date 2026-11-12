"""Tests for what the public release adds: starter init, `aisync demo`, the install-hooks consent flow,
the version, and a privacy scan of the tree. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import ai_sync
from ai_sync import cli, core, demo, hooks

from test_ai_sync import Base

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    patch = mock.patch("sys.stdin", stdin) if stdin is not None else contextlib.nullcontext()
    with patch, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class Tty(io.StringIO):
    def isatty(self):
        return True


class InitTests(Base):
    def test_init_without_seed_uses_starter_rules_and_touches_no_tool_file(self):
        fresh = self.t / "fresh"
        os.environ["AISYNC_HOME"] = str(fresh)
        tool_file = self.t / "CLAUDE.md"
        tool_file.write_text("# my rules\n")
        code, out = run_cli("init", "--target", f"claude-code={tool_file}")
        self.assertEqual(code, 0, out)
        self.assertIn("aisync claim", (fresh / "preferences.md").read_text())
        self.assertEqual(tool_file.read_text(), "# my rules\n")          # only install-hooks seeds it, after asking
        cfg = core.load_config(fresh)
        self.assertEqual(cfg.scan_dirs, [])
        self.assertEqual(cfg.rule_sources, [])
        self.assertEqual(cfg.roster, list(core.DEFAULT_ROSTER))
        self.assertEqual(cfg.progress_targets, [])
        self.assertIn(str(fresh).replace(str(Path.home()), "~", 1), (fresh / "agents.md").read_text())
        self.assertIn("install-hooks", out)

    def test_roster_is_configurable(self):
        self.write_config('roster = ["aider"]\n')
        self.assertEqual(self.cfg.roster, ["aider"])
        code, out = run_cli("report")
        self.assertIn("aider logged nothing", out)
        self.assertNotIn("cursor logged nothing", out)


class DemoTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "demo"
        self._env = mock.patch.dict(os.environ, {"AISYNC_HOME": str(Path(self.tmp.name) / "real-hub"),
                                                 "GIT_CONFIG_GLOBAL": str(Path(self.tmp.name) / "gitconfig"),
                                                 "GIT_CONFIG_NOSYSTEM": "1"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def test_demo_builds_a_realistic_hub_and_prints_status(self):
        code, out = run_cli("demo", "--dir", str(self.dir))
        self.assertEqual(code, 0, out)
        self.assertFalse((Path(self.tmp.name) / "real-hub").exists())    # the real hub is untouched
        cfg = core.load_config(self.dir)
        events = core.read_events(cfg)
        self.assertEqual(core.verify_chain(cfg), [])
        self.assertGreaterEqual(len(events), 60)
        self.assertEqual({e["agent"] for e in events} >= set(demo.TOOLS), True)
        ts = [e["ts"] for e in events]
        self.assertEqual(ts, sorted(ts))
        days = {core.datetime.fromisoformat(t).date() for t in ts}
        self.assertEqual((max(days) - min(days)).days, 13)                 # two calendar weeks
        self.assertGreaterEqual(len(days), 13)                             # with activity on (almost) every day
        kinds = {e["kind"] for e in events}
        self.assertTrue({"commit", "decision", "blocked", "unblocked", "rule_change", "claim", "release"} <= kinds)
        unblocked = [e for e in events if e["kind"] == "unblocked"]
        self.assertTrue(all(e["refs"]["blocked_id"] in {b["id"] for b in events} for e in unblocked))
        self.assertTrue(any(e["refs"].get("overlap_with") for e in events if e["kind"] == "claim"))
        self.assertIn("CONFLICT codex", out)
        self.assertIn("BLOCKED  gemini-cli", out)
        self.assertEqual(out.count("claim    "), 3)
        self.assertIn("chain intact", out)
        self.assertIn(f"export AISYNC_HOME={self.dir}", out)

    def test_demo_hub_answers_every_read_command(self):
        demo.build(self.dir)
        os.environ["AISYNC_HOME"] = str(self.dir)
        self.assertEqual(run_cli("verify")[0], 0)
        self.assertEqual(run_cli("status")[0], 1)                          # the conflict needs attention
        self.assertIn("codex", run_cli("claims")[1] + run_cli("tail", "-n", "40")[1])
        self.assertIn("'alex'", run_cli("report")[1])
        self.assertEqual(run_cli("resolve", "codex", "--take", "target")[0], 0)
        self.assertEqual(run_cli("status")[0], 0)

    def test_demo_refuses_an_existing_hub(self):
        demo.build(self.dir)
        self.assertEqual(run_cli("demo", "--dir", str(self.dir))[0], 2)


class InstallHooksCliTests(Base):
    def setUp(self):
        super().setUp()
        self.fake_home = self.t / "home"
        self.fake_home.mkdir()
        (self.fake_home / ".claude").mkdir()
        self.settings = self.fake_home / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({"model": "x"}))
        self.b.write_text("no block yet\n")
        patches = [mock.patch.object(Path, "home", return_value=self.fake_home),
                   mock.patch.object(hooks, "aisync_bin", return_value="/bin/aisync"),
                   mock.patch.dict(os.environ, {"HOME": str(self.fake_home)})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self):
        return {p: p.read_text() for p in self.fake_home.rglob("*") if p.is_file()} | {self.b: self.b.read_text()}

    def test_dry_run_shows_diff_and_writes_nothing(self):
        before = self.snapshot()
        code, out = run_cli("install-hooks", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("+++ ~/.claude/settings.json (after)", out)
        self.assertIn('/bin/aisync hook claude-code', out)
        self.assertIn("== cursor: add hooks: sessionStart, postToolUse", out)
        self.assertIn("== b: seed the shared-rules block", out)
        self.assertIn("hooksPath", out)
        self.assertIn("nothing was written", out)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home / "githooks").exists())

    def test_without_a_terminal_it_refuses_unless_yes(self):
        before = self.snapshot()
        code, out = run_cli("install-hooks", "--only", "claude-code", stdin=io.StringIO(""))
        self.assertEqual(code, 1)
        self.assertIn("--yes", out)
        self.assertEqual(self.snapshot(), before)

    def test_answer_no_writes_nothing_and_yes_applies(self):
        before = self.snapshot()
        code, out = run_cli("install-hooks", "--only", "claude-code,rules", stdin=Tty("n\n"))
        self.assertEqual(code, 1)
        self.assertEqual(self.snapshot(), before)
        code, out = run_cli("install-hooks", "--only", "claude-code,rules", stdin=Tty("y\n"))
        self.assertEqual(code, 0, out)
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "x")
        self.assertIn("SessionStart", data["hooks"])
        self.assertIn("rule A", self.b.read_text())
        self.assertTrue(list((self.fake_home / ".claude").glob("settings.json.bak-aisync-*")))
        code, out = run_cli("install-hooks", "--only", "claude-code,rules", "--dry-run")
        self.assertIn("nothing to change", out)

    def test_yes_flag_installs_git_hooks_path(self):
        code, out = run_cli("install-hooks", "--only", "git", "--yes")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.home / "githooks" / "commit-msg").exists())
        self.assertIn("nothing to change", run_cli("install-hooks", "--only", "git", "--dry-run")[1])

    def test_unknown_step_and_broken_json(self):
        self.assertEqual(run_cli("install-hooks", "--only", "trae")[0], 2)
        self.settings.write_text("{broken")
        before = self.snapshot()
        code, out = run_cli("install-hooks", "--only", "claude-code", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("cannot do claude-code", out)
        self.assertEqual(self.snapshot(), before)


class ReleaseTests(unittest.TestCase):
    def test_version_and_console_script(self):
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(meta["version"], "0.2.5")
        self.assertEqual(ai_sync.__version__, meta["version"])
        self.assertEqual(meta["name"], "aisync")
        self.assertEqual(meta["scripts"], {"aisync": "ai_sync.cli:main"})
        self.assertEqual(meta["dependencies"], [])
        self.assertEqual(meta["optional-dependencies"], {"mcp": ["mcp>=2.3,<3"]})

    def test_every_command_has_a_cli_test(self):
        commands = cli.build_parser()._subparsers._group_actions[0].choices
        self.assertTrue({"init", "log", "rule", "claim", "release", "claims", "news", "hook", "install-hooks",
                         "status", "report", "tail", "verify", "sync", "watch", "demo"} <= set(commands))
        tests = "".join(p.read_text() for p in (ROOT / "tests").glob("test_*.py"))
        for name in commands:
            self.assertRegex(tests, rf'(run_cli|cli\.main)\(\[?"{re.escape(name)}"', name)

    def test_stdlib_only(self):
        import sys
        for path in (ROOT / "ai_sync").glob("*.py"):
            for mod in re.findall(r"^\s*(?:from|import) ([a-zA-Z_]\w*)", path.read_text(), re.M):
                optional = path.name == "mcp_server.py" and mod == "mcp"     # the `aisync[mcp]` extra, imported lazily
                self.assertTrue(mod in sys.stdlib_module_names or mod == "__future__" or optional, f"{path.name}: {mod}")

    def test_mcp_without_the_sdk_says_how_to_install_it(self):
        with mock.patch.dict("sys.modules", {"mcp": None, "mcp.server": None, "mcp.server.mcpserver": None}):
            code, out = run_cli("mcp")
        self.assertEqual(code, 2)
        self.assertIn("pip install 'aisync[mcp]'", out)


class RulecheckCliTests(Base):
    def test_rulecheck_passes_when_every_fact_survives(self):
        before = self.t / "before.md"
        after = self.t / "after.md"
        block = "<!-- owner-preferences:start -->\n- [R-001] run the suite in 120 s (2026-11-01 · a)\n<!-- owner-preferences:end -->\n"
        before.write_text(block)
        after.write_text(block)
        archive = self.t / "rules-archive.md"
        archive.write_text("- [R-000] run the suite in 300 s (2026-10-01 · a) 被 R-001「run the suite」取代（2026-11-01）\n")
        code, out = run_cli("rulecheck", str(before), str(after), "--archive", str(archive))
        self.assertEqual(code, 0, out)
        self.assertIn("0 missing facts", out)


class PrivacyTests(unittest.TestCase):
    """The repo is public: no personal paths, names, emails or private repo names anywhere.
    One scanner for the tests, the release check and the port: tools/privacy_scan.py (hashed word list)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "tools" / "privacy_scan.py")
        cls.ps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.ps)

    def test_the_scanner_catches_what_it_guards(self):
        self.assertTrue(self.ps.scan_text("see ~/" + "projects/awesome-" + "list-factory/x"))
        self.assertTrue(self.ps.scan_text("cd /Users/" + "someone/projects"))
        self.assertTrue(self.ps.scan_text("mail me: a.b@" + "uni.edu"))
        self.assertFalse(self.ps.scan_text("Co-authored-by: X <1+X@users.noreply.github.com>; ai-sync hub; noreply@anthropic.com"))

    def test_history_skips_a_host_rewritten_committer_but_not_an_author(self):
        import subprocess, tempfile
        name = "Ti" + "ger"                                   # a guarded word, split so this file stays clean
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            git = lambda *a, **e: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True,
                                                 env={**env, **e})
            git("init", "-q")
            git("commit", "-q", "--allow-empty", "-m", "base", GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@example.com",
                GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@example.com")
            git("tag", "v0")
            git("commit", "-q", "--allow-empty", "-m", "rebased on the host", GIT_AUTHOR_NAME="a",
                GIT_AUTHOR_EMAIL="a@example.com", GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL="a@example.com")
            self.assertEqual(self.ps.scan_files(self.ps.history_items(Path(d))), [])
            git("commit", "-q", "--allow-empty", "-m", "local", GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL="a@example.com",
                GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL="a@example.com")
            self.assertTrue(self.ps.scan_files(self.ps.history_items(Path(d))))

    def test_no_private_strings_in_tree(self):
        self.assertEqual(self.ps.scan_files(self.ps.tree_items(ROOT)), [])


if __name__ == "__main__":
    unittest.main()
