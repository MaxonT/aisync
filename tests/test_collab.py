"""Tests for the collaboration layer: shared rules any agent can add, AGENTS.md rule import,
claims, progress fan-out, self-check report. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path

from ai_sync import cli, collab, core

from test_ai_sync import Base, _git

META = """# Repo

## Meta rule: global rules live here

Whenever the owner states a rule ...

- 2026-10-10 19:51 CDT: every repo has a CONTRIBUTING.md. Reason: consistency.
- 2026-10-12 03:02 CDT: reliability + observability for every project. Reason:
  the owner asked for it.

## Docs
"""


class CollabBase(Base):
    def setUp(self):
        super().setUp()
        self.lab = self.t / "repos" / "team-handbook"
        self.lab.mkdir(parents=True)
        _git(self.lab, "init", "-q", "-b", "main")
        (self.lab / "AGENTS.md").write_text(META)
        _git(self.lab, "add", "AGENTS.md")
        _git(self.lab, "commit", "-q", "-m", "agents")
        self.prog_file = self.t / "notes" / "aisync-progress.md"
        self.prog_block = self.t / "codex.md"
        self.prog_block.write_text("# codex\n\nkeep me\n")
        self.write_config(
            f'scan_dirs = ["{self.t / "repos"}"]\nrule_sources = ["team-handbook"]\n'
            f'[[progress_target]]\nlabel = "notes"\npath = "{self.prog_file}"\nmode = "file"\n'
            f'[[progress_target]]\nlabel = "codex"\npath = "{self.prog_block}"\nmode = "block"\n')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()


class RuleTests(CollabBase):
    def test_rule_add_reaches_every_tool_copy(self):
        core.run_cycle(self.cfg, "baseline")                 # the real hub has synced before
        code, out = self.run_cli("rule", "add", "--agent", "trae", "--project", "team-handbook",
                                 "always write tests first", "--reason", "owner said so")
        self.assertEqual(code, 0, out)
        truth = (self.home / "preferences.md").read_text()
        self.assertIn("(trae, team-handbook): always write tests first Reason: owner said so", truth)
        self.assertIn(collab.RULES_HEAD, core.extract_block(truth))
        rec = core.run_cycle(self.cfg, "test")
        self.assertTrue(rec["ok"], rec)
        for copy in (self.a, self.b):                       # the other tools' files
            self.assertIn("always write tests first", copy.read_text())
        self.assertIn("A tail", self.a.read_text())          # rest of the file untouched
        kinds = [e["kind"] for e in core.read_events(self.cfg)]
        self.assertIn("rule_change", kinds)

    def test_rule_add_dedupes_and_validates(self):
        self.run_cli("rule", "add", "-a", "cursor", "always write tests first")
        code, out = self.run_cli("rule", "add", "-a", "codex", "Always write tests first!")
        self.assertEqual(code, 0)
        self.assertIn("already exists", out)
        self.assertEqual(sum("tests first" in l for l in collab.rule_list(self.cfg)), 1)
        self.assertEqual(self.run_cli("rule", "add", "always do things")[0], 2)    # no agent
        self.assertEqual(self.run_cli("rule", "add", "-a", "x", "short")[0], 2)

    def test_rule_list(self):
        self.run_cli("rule", "add", "-a", "cursor", "never commit generated files")
        code, out = self.run_cli("rule", "list")
        self.assertEqual(code, 0)
        self.assertIn("rule A", out)
        self.assertIn("never commit generated files", out)


class MetaRuleImportTests(CollabBase):
    def test_parse_meta_entries_joins_continuation_lines(self):
        e = collab.parse_meta_entries(META)
        self.assertEqual([ts for ts, _ in e], ["2026-10-10 19:51 CDT", "2026-10-12 03:02 CDT"])
        self.assertTrue(e[1][1].endswith("the owner asked for it."))

    def test_first_sight_imports_nothing_then_new_entries_are_imported(self):
        rep = collab.harvest_rules(self.cfg)
        self.assertEqual(rep["imported"], [])               # existing entries are not flooded in
        before = (self.home / "preferences.md").read_text()
        text = (self.lab / "AGENTS.md").read_text().replace(
            "\n## Docs", "- 2026-10-12 08:30 CDT: every repo ships a CHANGELOG. Reason: owner.\n\n## Docs")
        (self.lab / "AGENTS.md").write_text(text)
        rep = collab.harvest_rules(self.cfg)
        self.assertEqual(len(rep["imported"]), 1)
        after = (self.home / "preferences.md").read_text()
        self.assertNotEqual(before, after)
        self.assertIn("(from team-handbook AGENTS.md): every repo ships a CHANGELOG.", after)
        self.assertEqual(collab.harvest_rules(self.cfg)["imported"], [])     # idempotent

    def test_worktree_checkouts_count_as_the_same_repo(self):
        wt = self.t / "repos" / "team-handbook.wt-x"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /nowhere\n")
        (wt / "AGENTS.md").write_text(META)
        files = collab.rule_source_files(self.cfg)
        self.assertEqual({p.parent.name for p in files}, {"team-handbook", "team-handbook.wt-x"})
        collab.harvest_rules(self.cfg)                       # first sight
        (wt / "AGENTS.md").write_text(META.replace("\n## Docs", "- 2026-10-12 09:00 CDT: from a worktree. Reason: x.\n\n## Docs"))
        collab.harvest_rules(self.cfg)
        self.assertIn(f"Full text: `{collab._tilde(self.lab / 'AGENTS.md')}`", (self.home / "preferences.md").read_text())


class ClaimTests(CollabBase):
    def test_claim_overlap_release(self):
        code, _ = self.run_cli("claim", "-a", "cursor", "-p", "webapp", "-f", "src/", "-m", "refactor src")
        self.assertEqual(code, 0)
        code, out = self.run_cli("claim", "-a", "trae", "-p", "webapp", "-f", "src/app.py", "-m", "fix bug")
        self.assertEqual(code, 3)
        self.assertIn("OVERLAP: cursor", out)
        self.assertEqual(self.run_cli("claim", "-a", "codex", "-p", "webapp", "-f", "docs/x.md", "-m", "docs")[0], 0)
        self.assertEqual(self.run_cli("claim", "-a", "codex", "-p", "other", "-m", "whole repo")[0], 0)
        code, out = self.run_cli("claims")
        self.assertIn("cursor", out)
        self.assertIn("[whole repo]", out)
        self.assertEqual(self.run_cli("release", "-a", "cursor")[0], 0)
        self.assertNotIn("cursor", self.run_cli("claims")[1])

    def test_expired_claims_disappear(self):
        collab.claim(self.cfg, agent="cursor", project="p", summary="s", ttl_min=-1)
        self.assertEqual(collab.load_claims(self.cfg), [])

    def test_log_warns_on_claimed_files(self):
        self.run_cli("claim", "-a", "cursor", "-p", "webapp", "-f", "a.py", "-m", "x")
        code, out = self.run_cli("log", "-a", "trae", "-p", "webapp", "-f", "a.py", "-m", "edited a.py")
        self.assertEqual(code, 0)
        self.assertIn("warning: cursor has claimed webapp", out)

    def test_claims_show_in_agents_md(self):
        collab.claim(self.cfg, agent="cursor", project="webapp", summary="refactor", files=["src/"])
        core.run_cycle(self.cfg, "test")
        self.assertIn("Working right now", (self.home / "agents.md").read_text())


class ProgressAndReportTests(CollabBase):
    def test_progress_written_to_file_and_block_targets(self):
        core.append_event(self.cfg, agent="trae", kind="dev_log", project="lab", summary="did a thing")
        rec = core.run_cycle(self.cfg, "test")
        self.assertTrue(rec["steps"]["progress"]["ok"], rec)
        self.assertIn("did a thing", self.prog_file.read_text())
        text = self.prog_block.read_text()
        self.assertIn("keep me", text)
        self.assertIn("did a thing", text)
        self.assertEqual(text.count(core.PROG_START), 1)
        rep = collab.render_progress(self.cfg)               # unchanged -> no rewrite
        self.assertEqual(rep["updated"], [])

    def test_one_bad_progress_target_does_not_block_others(self):
        self.prog_block.unlink()
        rep = collab.render_progress(self.cfg)
        self.assertIn("notes", rep["updated"])
        self.assertTrue(rep["errors"])

    def test_report_hints(self):
        core.append_event(self.cfg, agent="git:The Archer", kind="commit", project="lab", summary="c")
        core.append_event(self.cfg, agent="trae", kind="dev_log", summary="no project")
        code, out = self.run_cli("report")
        self.assertEqual(code, 0)
        self.assertIn("'The Archer'", out)
        self.assertIn("cursor logged nothing", out)
        self.assertIn("without --project", out)
        r = json.loads(self.run_cli("report", "--json")[1])
        self.assertEqual(r["unattributed_commits"], {"The Archer": 1})

    def test_cycle_has_all_steps(self):
        rec = core.run_cycle(self.cfg, "test")
        self.assertEqual(list(rec["steps"]), ["harvest", "rules", "distribute", "render", "progress", "flush"])
        self.assertTrue(rec["ok"], rec)


if __name__ == "__main__":
    unittest.main()
