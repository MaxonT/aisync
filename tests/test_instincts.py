"""Tests for instincts: the habit list injected by the hook, `aisync instinct list|add`. Temp dirs only."""
from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from ai_sync import cli, core, hooks, instincts

from test_ai_sync import Base


class InstinctBase(Base):
    def setUp(self):
        super().setUp()
        self.inst = self.t / "ops" / "instincts.md"
        self.errs = self.t / "ops" / "error-bank.md"
        self.inst.parent.mkdir()
        self.inst.write_text("# instincts\n\n1. check the dead routes first（EB-001）\n2. verify each box（EB-002）\n")
        self.errs.write_text("# error bank\n\n" + "".join(f"### {i} some title\nbody\n\n" for i in ("EB-001", "EB-002", "EB-003")))
        self.write_config(f'instincts_path = "{self.inst}"\nerrors_path = "{self.errs}"')

    def ctx(self, out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def run_cli(self, *argv):
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = cli.main(list(argv))
        return rc, o.getvalue(), e.getvalue()


class HookTests(InstinctBase):
    def test_session_start_injects_instincts(self):
        out = hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "s1"}, self.cfg)
        text = self.ctx(out)
        self.assertIn("1. check the dead routes first（EB-001）", text)
        self.assertIn("2. verify each box（EB-002）", text)

    def test_prompt_injects_only_new_lines_once(self):
        hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "s1"}, self.cfg)
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.cfg), "")
        instincts.add(self.cfg, agent="claude-code", text="stop at the base case", eb="EB-003")
        text = self.ctx(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.cfg))
        self.assertIn("NEW instinct", text)
        self.assertIn("3. stop at the base case（EB-003）", text)
        self.assertNotIn("check the dead routes", text)
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.cfg), "")

    def test_unreadable_file_injects_one_line_and_never_fails(self):
        self.inst.unlink()
        text = self.ctx(hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "s2"}, self.cfg))
        self.assertIn("instincts file unreadable:", text)
        self.assertEqual(len(text.split("\n\n")[0].splitlines()), 1)     # the instincts part
        # the same failure on a later prompt is not repeated
        self.assertEqual(hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "s2"}, self.cfg), "")

    def test_cursor_session_start_includes_instincts(self):
        out = json.loads(hooks.handle("cursor", {"hook_event_name": "sessionStart", "session_id": "c"}, self.cfg))
        self.assertIn("EB-002", out["additional_context"])

    def test_hook_does_not_run_git(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("git")):
            hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "s3"}, self.cfg)


class CliTests(InstinctBase):
    def test_list(self):
        rc, out, _ = self.run_cli("instinct", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)

    def test_add_writes_line_and_journal(self):
        rc, out, _ = self.run_cli("instinct", "add", "keep it small", "--from", "EB-003", "--agent", "claude-code")
        self.assertEqual(rc, 0)
        self.assertIn("commit", out)
        self.assertTrue(self.inst.read_text().endswith("3. keep it small（EB-003）\n"))
        ev = core.read_events(self.cfg)[-1]
        self.assertEqual((ev["agent"], ev["refs"]), ("claude-code", {"eb": "EB-003"}))

    def test_add_rejects_unknown_or_missing_eb(self):
        before = self.inst.read_text()
        self.assertNotEqual(self.run_cli("instinct", "add", "x", "--from", "EB-999", "--agent", "a")[0], 0)
        self.assertNotEqual(self.run_cli("instinct", "add", "x", "--agent", "a")[0], 0)
        self.assertNotEqual(self.run_cli("instinct", "add", "x", "--from", "bogus", "--agent", "a")[0], 0)
        self.assertEqual(self.inst.read_text(), before)

    def test_add_checks_the_learn_error_bank(self):
        rc, _, _ = self.run_cli("instinct", "add", "found one", "--from", "EB-002", "--agent", "a")
        self.assertEqual(rc, 0)
        before = self.inst.read_text()
        self.assertNotEqual(self.run_cli("instinct", "add", "nope", "--from", "EB-404", "--agent", "a")[0], 0)
        self.assertEqual(self.inst.read_text(), before)

    def test_add_without_error_bank_fails(self):
        self.errs.unlink()
        self.assertNotEqual(self.run_cli("instinct", "add", "x", "--from", "EB-001", "--agent", "a")[0], 0)

    def test_list_unreadable_exit_nonzero(self):
        self.inst.unlink()
        self.assertEqual(self.run_cli("instinct", "list")[0], 1)


class GateTests(InstinctBase):
    """2026-11-08: one EB-059 habit was added 9 times and a git conflict was committed into the file."""

    def test_exact_and_reworded_repeats_are_refused(self):
        with self.assertRaisesRegex(ValueError, "already on the list"):
            instincts.add(self.cfg, agent="codex", text="check the dead routes first", eb="EB-003")
        with self.assertRaisesRegex(ValueError, "already on the list"):
            instincts.add(self.cfg, agent="codex", text="verify each box twice", eb="EB-002")
        rep = instincts.add(self.cfg, agent="codex", text="log the exit code to a file first", eb="EB-003")
        self.assertTrue(rep["line"].startswith("3. "))

    def test_at_most_two_habits_per_mistake(self):
        instincts.add(self.cfg, agent="codex", text="read the scheduler table before moving a file", eb="EB-001")
        with self.assertRaisesRegex(ValueError, "EB-001 already has 2 habits"):
            instincts.add(self.cfg, agent="codex", text="close the tab once a company is confirmed", eb="EB-001")

    def test_conflict_markers_block_adding(self):
        self.inst.write_text(self.inst.read_text() + "<<<<<<< Updated upstream\n3. a（EB-003）\n=======\n>>>>>>> Stashed\n")
        with self.assertRaisesRegex(ValueError, "conflict markers"):
            instincts.add(self.cfg, agent="codex", text="something new entirely", eb="EB-003")
