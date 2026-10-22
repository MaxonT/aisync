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
        self.assertEqual(meta["version"], "0.1.2")
        self.assertEqual(ai_sync.__version__, meta["version"])
        self.assertEqual(meta["name"], "aisync")
        self.assertEqual(meta["scripts"], {"aisync": "ai_sync.cli:main"})
        self.assertEqual(meta["dependencies"], [])

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
                self.assertTrue(mod in sys.stdlib_module_names or mod == "__future__", f"{path.name}: {mod}")


class PrivacyTests(unittest.TestCase):
    """The repo is public: no personal paths, names, emails or private repo names anywhere.
    The private words are kept as SHA-256 prefixes, so this test does not publish what it guards."""
    PRIVATE = {
        "8bb367a8f3ba87581929", "e63b9641f880cc3335b9", "f15c16b99f82d8201767", "35b70246a19cb878499f",
        "7b9dd7061d0e07a296d2", "b6463d839a2dd880fbf0", "ce62d6913dfc9149ab9b", "b7b5122e0216692a394b",
        "9aebd569f8d256fe366f", "7d941509db4cf57a351b", "1cb8cc6d49bb9a9982fa", "92f9ed99b93513392b02",
        "70f693250fd960f8a205", "d0a6da933dd33d8e975d",
    }
    PATTERN = re.compile(r"/Users/\w")
    EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    PUBLIC_EMAIL = re.compile(r"noreply@|@users\.noreply\.github\.com$|@example\.(com|org)$")

    @staticmethod
    def words(line: str):
        """Every word, and every run of a hyphenated word's parts, with and without hyphens."""
        import hashlib
        for tok in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", line.lower()):
            parts = tok.split("-")
            for i in range(len(parts)):
                for j in range(i + 1, len(parts) + 1):
                    for w in {"-".join(parts[i:j]), "".join(parts[i:j])}:
                        yield w, hashlib.sha256(w.encode()).hexdigest()[:20]

    def scan(self, text: str) -> list[str]:
        found = [w for line in text.splitlines() for w, digest in self.words(line) if digest in self.PRIVATE]
        found += self.PATTERN.findall(text)
        found += [m for m in self.EMAIL.findall(text) if not self.PUBLIC_EMAIL.search(m)]
        return found

    def test_the_scanner_catches_what_it_guards(self):
        self.assertTrue(self.scan("see ~/" + "projects/awesome-" + "list-factory/x"))
        self.assertTrue(self.scan("cd /Users/" + "someone/projects"))
        self.assertTrue(self.scan("mail me: a.b@" + "uni.edu"))
        self.assertFalse(self.scan("Co-authored-by: X <1+X@users.noreply.github.com>; ai-sync hub; noreply@anthropic.com"))

    def test_no_private_strings_in_tree(self):
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or {".git", "__pycache__", "build", "dist"} & set(path.parts) \
                    or path.suffix in (".pyc", ".gif", ".png"):
                continue
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if self.scan(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
