"""Tests for hooks: mid-session news, per-tool hook output, installers, git Agent trailer.
Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_sync import cli, collab, core, hooks

from test_ai_sync import Base, _git


class NewsTests(Base):
    def test_first_call_sets_cursor_then_reports_only_others(self):
        core.append_event(self.cfg, agent="trae", kind="dev_log", summary="old work")
        first = hooks.news(self.cfg, "claude-code", "s1")
        self.assertTrue(first["first"])
        self.assertEqual(first["events"], [])
        core.append_event(self.cfg, agent="cursor", kind="dev_log", summary="cursor fixed x", project="p", files=["x.py"])
        core.append_event(self.cfg, agent="claude-code", kind="dev_log", summary="my own work")
        rep = hooks.news(self.cfg, "claude-code", "s1")
        self.assertEqual([e["summary"] for e in rep["events"]], ["cursor fixed x"])
        text = hooks.news_text(rep)
        self.assertIn("cursor dev_log @ p: cursor fixed x [x.py]", text)
        self.assertEqual(hooks.news(self.cfg, "claude-code", "s1")["events"], [])   # cursor moved on

    def test_sessions_have_separate_cursors(self):
        hooks.news(self.cfg, "codex", "a")
        core.append_event(self.cfg, agent="trae", kind="note", summary="t1")
        hooks.news(self.cfg, "codex", "b")                          # b starts after t1
        core.append_event(self.cfg, agent="trae", kind="note", summary="t2")
        self.assertEqual([e["summary"] for e in hooks.news(self.cfg, "codex", "a")["events"]], ["t1", "t2"])
        self.assertEqual([e["summary"] for e in hooks.news(self.cfg, "codex", "b")["events"]], ["t2"])

    def test_new_shared_rule_is_announced(self):
        self.write_config()
        hooks.news(self.cfg, "codex", "s")
        p = self.home / "preferences.md"
        p.write_text(p.read_text().replace("- rule A", "- rule A\n- rule B: always test"))
        rep = hooks.news(self.cfg, "codex", "s")
        self.assertEqual(rep["new_rules"], ["- rule B: always test"])
        self.assertIn("NEW shared rule", hooks.news_text(rep))

    def test_nothing_new_means_empty_text(self):
        hooks.news(self.cfg, "codex", "s")
        self.assertEqual(hooks.news_text(hooks.news(self.cfg, "codex", "s")), "")

    def test_journal_reset_does_not_crash(self):
        for i in range(3):
            core.append_event(self.cfg, agent="trae", kind="note", summary=f"e{i}")
        hooks.news(self.cfg, "codex", "s")
        (self.home / "journal.jsonl").write_text("")
        self.assertEqual(hooks.news(self.cfg, "codex", "s")["events"], [])

    def test_long_news_is_capped(self):
        hooks.news(self.cfg, "codex", "s")
        for i in range(hooks.NEWS_MAX_EVENTS + 5):
            core.append_event(self.cfg, agent="trae", kind="note", summary=f"e{i}")
        text = hooks.news_text(hooks.news(self.cfg, "codex", "s"))
        self.assertIn("(+5 older", text)


class HandleTests(Base):
    def test_claude_code_session_start_silent_then_prompt_injects(self):
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "x"}, self.cfg), "")
        core.append_event(self.cfg, agent="trae", kind="dev_log", summary="trae shipped promote")
        out = json.loads(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "x"}, self.cfg))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("trae shipped promote", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "x"}, self.cfg), "")

    def test_post_tool_use_injects_mid_task(self):
        hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "t"}, self.cfg)
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "PostToolUse", "session_id": "t"}, self.cfg), "")
        core.append_event(self.cfg, agent="codex", kind="dev_log", summary="codex changed z.py mid-task")
        out = json.loads(hooks.handle("claude-code", {"hook_event_name": "PostToolUse", "session_id": "t"}, self.cfg))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("codex changed z.py", out["hookSpecificOutput"]["additionalContext"])
        # the next prompt does not repeat what a tool call already delivered
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "t"}, self.cfg), "")

    def test_codex_uses_same_schema(self):
        hooks.handle("codex", {"hook_event_name": "SessionStart", "session_id": "c"}, self.cfg)
        core.append_event(self.cfg, agent="cursor", kind="note", summary="hello codex")
        out = json.loads(hooks.handle("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "c"}, self.cfg))
        self.assertIn("hello codex", out["hookSpecificOutput"]["additionalContext"])

    def test_cursor_session_start_gets_rules_progress_and_env(self):
        core.append_event(self.cfg, agent="codex", kind="dev_log", summary="codex did y", project="p")
        out = json.loads(hooks.handle("cursor", {"hook_event_name": "sessionStart", "conversation_id": "k"}, self.cfg))
        self.assertEqual(out["env"], {"AISYNC_AGENT": "cursor"})
        self.assertIn("- rule A", out["additional_context"])
        self.assertIn("codex did y", out["additional_context"])
        self.assertNotIn("<!--", out["additional_context"])
        self.assertEqual(hooks.handle("cursor", {"hook_event_name": "postToolUse", "conversation_id": "k"}, self.cfg), "")
        core.append_event(self.cfg, agent="trae", kind="note", summary="mid-session news")
        out = json.loads(hooks.handle("cursor", {"hook_event_name": "postToolUse", "conversation_id": "k"}, self.cfg))
        self.assertIn("mid-session news", out["additional_context"])

    def test_failure_is_silent_and_logged(self):
        (self.home / "preferences.md").unlink()
        self.assertEqual(hooks.handle("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "z"}, self.cfg), "")
        rec = json.loads((self.cfg.state / "hooks.jsonl").read_text().splitlines()[-1])
        self.assertFalse(rec["ok"])
        self.assertIn("FileNotFoundError", rec["error"])
        self.assertEqual(hooks.hook_stats(self.cfg)["codex"]["errors"], 1)

    def test_cli_hook_reads_stdin_and_always_exits_0(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("not json")), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["hook", "codex"]), 0)
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["hook", "claude-code"]), 0)

    def test_cli_news(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["news", "--agent", "windsurf", "--session", "n"]), 0)
            core.append_event(self.cfg, agent="trae", kind="note", summary="for windsurf")
            self.assertEqual(cli.main(["news", "--agent", "windsurf", "--session", "n"]), 0)
        self.assertIn("cursor set", out.getvalue())
        self.assertIn("for windsurf", out.getvalue())


class DetectTests(unittest.TestCase):
    def test_order(self):
        self.assertEqual(hooks.detect_agent({"AISYNC_AGENT": "trae", "CLAUDECODE": "1"}), "trae")
        self.assertEqual(hooks.detect_agent({"CLAUDECODE": "1", "__CFBundleIdentifier": "com.todesktop.230313mzl4w4u92"}),
                         "claude-code")
        self.assertEqual(hooks.detect_agent({"CODEX_SHELL": "1"}), "codex")
        self.assertEqual(hooks.detect_agent({"SOME_EDITOR": "1"}), "")
        self.assertEqual(hooks.detect_agent({"__CFBundleIdentifier": "com.trae.app"}), "trae")
        self.assertEqual(hooks.detect_agent({"__CFBundleIdentifier": "com.apple.Terminal"}), "")


class InstallTests(Base):
    def test_claude_codex_cursor_install_is_idempotent_and_keeps_existing(self):
        home = self.t / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(json.dumps(
            {"model": "x", "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
        self.assertEqual(hooks.install_claude(home, "/bin/aisync"), ["SessionStart", "UserPromptSubmit", "PostToolUse"])
        self.assertEqual(hooks.install_claude(home, "/bin/aisync"), [])
        data = json.loads((home / ".claude" / "settings.json").read_text())
        self.assertEqual(data["model"], "x")
        cmds = [h["command"] for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertEqual(cmds, ["other", "/bin/aisync hook claude-code"])
        self.assertTrue(list((home / ".claude").glob("settings.json.bak-aisync-*")))
        self.assertEqual(hooks.install_codex(home, "/bin/aisync"), ["SessionStart", "UserPromptSubmit", "PostToolUse"])
        self.assertEqual(hooks.install_codex(home, "/bin/aisync"), [])
        self.assertEqual(hooks.install_cursor(home, "/bin/aisync"), ["sessionStart", "postToolUse"])
        self.assertEqual(hooks.install_cursor(home, "/bin/aisync"), [])
        cur = json.loads((home / ".cursor" / "hooks.json").read_text())
        self.assertEqual(cur["version"], 1)
        self.assertEqual(cur["hooks"]["sessionStart"][0]["command"], "/bin/aisync hook cursor")

    def test_seed_block_then_distribute_keeps_trae_in_sync(self):
        trae = self.t / "trae_rule.md"
        trae.write_text("pointer line\n")
        self.assertTrue(hooks.seed_block(self.cfg, trae))
        self.assertFalse(hooks.seed_block(self.cfg, trae))
        self.assertTrue(trae.read_text().startswith("pointer line\n\n<!-- owner-preferences:start"))
        self.write_config(f'[[target]]\nlabel = "trae"\npath = "{trae}"\n')
        core.run_cycle(self.cfg, "t")
        self.set_truth("rule Z")
        core.run_cycle(self.cfg, "t")
        self.assertIn("rule Z", trae.read_text())
        self.assertIn("pointer line", trae.read_text())


class GitHookTests(Base):
    def setUp(self):
        super().setUp()
        self.hookdir = self.t / "githooks"
        hooks.write_git_hooks(self.hookdir)
        _git(self.proj, "config", "core.hooksPath", str(self.hookdir))

    def commit(self, msg, env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("AISYNC_AGENT", "CLAUDECODE", "CODEX_SHELL", "CODEX_THREAD_ID",
                            "CURSOR_AGENT", "__CFBundleIdentifier")}
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@x", **env_extra})
        (self.proj / "x.txt").write_text(msg)
        subprocess.run(["git", "add", "x.txt"], cwd=self.proj, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.proj, check=True, env=env)
        return subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.proj, capture_output=True,
                              text=True).stdout

    def test_ai_commit_gets_trailer(self):
        self.assertIn("\n\nAgent: claude-code", self.commit("feat: a", {"CLAUDECODE": "1"}))
        self.assertIn("Agent: trae", self.commit("feat: b", {"AISYNC_AGENT": "trae"}))
        self.assertIn("Agent: codex", self.commit("feat: c", {"CODEX_SHELL": "1"}))

    def test_existing_trailer_and_owner_commit_untouched(self):
        msg = self.commit("feat: d\n\nAgent: cursor", {"CLAUDECODE": "1"})
        self.assertEqual(msg.count("Agent:"), 1)
        self.assertIn("Agent: cursor", msg)
        self.assertNotIn("Agent:", self.commit("owner by hand", {}))

    def test_repo_local_hook_still_runs(self):
        local = self.proj / ".git" / "hooks" / "commit-msg"
        local.write_text("#!/bin/sh\necho 'Local-Hook: yes' >> \"$1\"\n")
        local.chmod(0o755)
        msg = self.commit("feat: e", {"CLAUDECODE": "1"})
        self.assertIn("Agent: claude-code", msg)
        self.assertIn("Local-Hook: yes", msg)
        bad = self.proj / ".git" / "hooks" / "pre-commit"
        bad.write_text("#!/bin/sh\nexit 1\n")
        bad.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.commit("feat: f", {})

    def test_install_git_refuses_foreign_hooks_path(self):
        fake_home = self.t / "fakehome"
        fake_home.mkdir()
        env = {"HOME": str(fake_home), "XDG_CONFIG_HOME": str(fake_home / ".config")}
        with mock.patch.dict(os.environ, env):
            r = hooks.install_git(self.cfg)
            self.assertTrue(r["set"])
            self.assertFalse(hooks.install_git(self.cfg)["set"])          # already ours: no change
            subprocess.run(["git", "config", "--global", "core.hooksPath", "/elsewhere"], check=True)
            self.assertIn("already points", hooks.install_git(self.cfg)["error"])


if __name__ == "__main__":
    unittest.main()
