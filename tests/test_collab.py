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

from ai_sync import cli, collab, core, ruleids

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
        self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "cursor", "always write tests first")
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "Always write tests first!")
        self.assertEqual(code, 0)
        self.assertIn("already exists", out)
        self.assertEqual(sum("tests first" in l for l in collab.rule_list(self.cfg)), 1)
        self.assertEqual(self.run_cli("rule", "add", "--judgment-only", "test rule", "always do things")[0], 2)    # no agent
        self.assertEqual(self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "short")[0], 2)

    def test_rule_list(self):
        self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "cursor", "never commit generated files")
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
        self.assertEqual(list(rec["steps"]), ["harvest", "rules", "ids", "distribute", "render", "progress", "tidy", "flush"])
        self.assertTrue(rec["ok"], rec)


if __name__ == "__main__":
    unittest.main()


class TopicRuleTests(CollabBase):
    """once rules are grouped by topic, rule add follows the writing standard."""

    def _grouped(self):
        p = self.home / "preferences.md"
        text = p.read_text()
        blk = core.extract_block(text)
        head = blk.splitlines()[0]
        new = "\n".join([head, "## Owner preferences", "### 协作与派单", "- old rule about letters uses inbox (2026-10-18 · x)",
                         "### 定时任务", "- jobs answer four questions (2026-10-14 · y)", core.BLOCK_END])
        p.write_text(core.replace_block(text, new))

    def test_topic_required_and_placed_newest_first(self):
        self._grouped()
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "always check duplicates before sending a task")
        self.assertEqual(code, 2)
        self.assertIn("协作与派单", out)                       # tells which topics exist
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "-p", "team-inbox", "-t", "协作",
                                 "always check duplicates before sending a task", "-r", "owner said")
        self.assertEqual(code, 0, out)
        blk = core.extract_block((self.home / "preferences.md").read_text()).splitlines()
        i = blk.index("### 协作与派单")
        self.assertRegex(blk[i + 1], r"^- \[R-\d{3}\] always check duplicates before sending a task \(")
        self.assertIn("· codex, team-inbox)", blk[i + 1])
        self.assertNotIn("Reason", "\n".join(blk))              # the why goes to the journal
        self.assertIn("owner said", core.read_events(self.cfg)[-1]["summary"])

    def test_replaces_moves_old_rule_and_limits_sentences(self):
        self._grouped()
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "One. Two. Three. Four.")
        self.assertEqual(code, 2)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "letters uses inbox",
                                 "letters live on the mailbox branch only")
        self.assertEqual(code, 0, out)
        blk = core.extract_block((self.home / "preferences.md").read_text())
        self.assertNotIn("已取代", blk)                                  # history is no longer in the block
        self.assertNotIn("letters uses inbox", blk)
        sup = (self.home / "rules-archive.md").read_text()
        self.assertIn("letters uses inbox", sup)
        new_id = ruleids.bullet_id(next(l for l in blk.splitlines() if "mailbox branch only" in l))
        self.assertIn(f"被 {new_id}「letters live on the mailbox br」取代", sup)
        self.assertIn(f"replaced_by: {new_id}", sup)
        from ai_sync import rulecheck
        self.assertEqual(rulecheck.check(blk, blk, sup), [])


class RuleDedupTests(TopicRuleTests):
    """--replaces was refused as 'an equal rule already exists'."""

    OLD = ("The goal of every learning pack is that Alex can explain the thing clearly to other people, "
           "so each pack lists every point in one line (2026-10-26 · claude-code, team-handbook)")
    NEW = ("The goal of every learning pack is that Alex can explain the thing clearly in technical "
           "communication, so each pack lists every point in one line and ends with a jargon table")

    def _with_old(self):
        self._grouped()
        p = self.home / "preferences.md"
        p.write_text(p.read_text().replace("### 定时任务", "- " + self.OLD + "\n### 定时任务"))

    def test_replace_keeps_same_opening_words(self):
        self._with_old()
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "explain the thing clearly to other people",
                                 self.NEW)
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"rule R-\d{3} added")
        cur = core.extract_block((self.home / "preferences.md").read_text())
        sup = (self.home / "rules-archive.md").read_text()
        self.assertIn("in technical communication", cur)
        self.assertNotIn("to other people", cur)
        self.assertIn("to other people", sup)

    def test_superseded_line_does_not_block_a_new_rule(self):
        self._with_old()
        self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "explain the thing clearly to other people", self.NEW)
        # a later revision whose opening words match only lines already in 已取代 and the one it replaces
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "in technical communication",
                                 self.NEW.replace("jargon table", "jargon table and two questions"))
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"rule R-\d{3} added")

    def test_true_duplicate_still_refused(self):
        self._with_old()
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "定时", self.OLD.split(" (2026")[0])
        self.assertIn("an equal rule already exists", out)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "定时", "--replaces", "jobs answer four questions",
                                 self.OLD.split(" (2026")[0])
        self.assertIn("an equal rule already exists", out)   # equals a different current rule


def test_sentence_count_ignores_dots_in_names_and_semicolons():
    from ai_sync.collab import _sentences
    assert _sentences("Keys are in ~/.config/awesome-factory/x.env and jobs/x_autopilot.py reads them; verified 2026-10-27.") == 1
    assert _sentences("One. Two! Three? Four.") == 4
    assert _sentences("第一句。第二句！第三句") == 3
