"""progress_file: live progress leaves agents.md so the always-loaded file stays byte-stable (prompt cache).

2026-11-08 measurement: one journal event changed the progress table inside agents.md, which CLAUDE.md
imports, and the next session read 13,060 cached tokens instead of 63,524.
"""
from __future__ import annotations

import json

from ai_sync import core, hooks

from test_ai_sync import Base


class ProgressSplitTests(Base):
    def _on(self):
        self.write_config('progress_file = "progress.md"\n')
        self.cfg = core.load_config(self.home)

    def test_agents_md_stays_identical_while_events_arrive(self):
        self._on()
        core.render(self.cfg)
        agents = (self.home / "agents.md").read_text()
        self.assertNotIn(core.PROG_START, agents)
        self.assertIn("Live progress", agents)
        core.append_event(self.cfg, agent="codex", kind="dev_log", summary="fixed the parser", project="p")
        rep = core.render(self.cfg)
        self.assertEqual((self.home / "agents.md").read_text(), agents)          # byte-identical
        self.assertIn("progress.md", rep["changed"])
        self.assertIn("fixed the parser", (self.home / "progress.md").read_text())

    def test_session_start_hook_injects_progress(self):
        self._on()
        core.append_event(self.cfg, agent="codex", kind="blocked", summary="waiting on a token", project="p")
        core.render(self.cfg)
        out = hooks.handle("claude-code", {"hook_event_name": "SessionStart", "session_id": "s1"}, self.cfg)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Live progress", ctx)
        self.assertIn("waiting on a token", ctx)
        out2 = hooks.handle("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.cfg)
        self.assertNotIn("Live progress", out2)                                  # only at start

    def test_default_keeps_the_block_in_agents_md(self):
        core.render(self.cfg)
        self.assertIn(core.PROG_START, (self.home / "agents.md").read_text())
        self.assertEqual(core.progress_now(self.cfg), "")

    def test_absolute_path_and_unreadable_file(self):
        self.write_config(f'progress_file = "{self.t / "elsewhere" / "p.md"}"\n')
        self.cfg = core.load_config(self.home)
        self.assertEqual(core.progress_now(self.cfg), "")                         # not written yet: empty, no raise
        (self.t / "elsewhere").mkdir()
        core.render(self.cfg)
        self.assertTrue((self.t / "elsewhere" / "p.md").exists())


class PointerTests(Base):
    def test_duplicate_pointers_collapse_to_one_and_stay_stable(self):
        self.write_config('progress_file = "progress.md"\n')
        self.cfg = core.load_config(self.home)
        core.render(self.cfg)
        agents = self.home / "agents.md"
        one = agents.read_text()
        ptr = next(l for l in one.splitlines() if l.startswith("_Live progress"))
        agents.write_text(one.replace(ptr, ptr + "\n\n" + ptr + "\n\n" + ptr) + f"\n{core.PROG_START}\nold\n{core.PROG_END}\n")
        core.render(self.cfg)
        text = agents.read_text()
        self.assertEqual(text.count(ptr), 1)
        self.assertNotIn(core.PROG_START, text)
        core.render(self.cfg)
        self.assertEqual(agents.read_text(), text)
