"""Stable rule IDs and the retired-rules archive (2026-11-02). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import unittest

from ai_sync import collab, core, ruleids, rulecheck

from test_collab import CollabBase

GROUPED = "\n".join([
    "<!-- owner-preferences:start (shared) -->",
    "## Owner preferences",
    "### 协作与派单",
    "- letters live on the mailbox branch only (2026-10-24 · x)",
    "- old rule about letters uses inbox (2026-10-18 · x)",
    "### 定时任务",
    "- jobs answer four questions in ~/projects/ops-jobs/HANDOFF.md (2026-10-14 · y)",
    "",
    "### 已取代",
    "- jobs ran at 06:45 (2026-10-10 · y) 被「jobs answer four questions」取代（2026-10-14）",
    "- letters went to ~/old/inbox (2026-10-01 · x) 被「letters live on the mailbox」取代（2026-10-24 10:00，owner）",
    "- something nobody wrote down (2026-09-30 · z) 被「a rule that no longer exists anywhere」取代",
    core.BLOCK_END])


class RuleIdTests(CollabBase):
    def setUp(self):
        super().setUp()
        self.prefs = self.home / "preferences.md"
        self.prefs.write_text(f"# truth\n\n{GROUPED}\n")
        self.archive = self.home / ruleids.ARCHIVE
        self.before = self.prefs.read_text()

    def block(self) -> str:
        return core.extract_block(self.prefs.read_text())

    def test_normalize_numbers_rules_in_file_order_and_moves_history_to_archive(self):
        rep = ruleids.normalize(self.cfg)
        self.assertEqual(rep["numbered"], ["R-001", "R-002", "R-003"])
        self.assertEqual(rep["archived"], ["R-004", "R-005", "R-006"])
        blk = self.block()
        self.assertNotIn("已取代", blk)
        self.assertEqual([ruleids.bullet_id(l) for l in collab.rule_lines(blk)], ["R-001", "R-002", "R-003"])
        self.assertTrue(blk.splitlines()[-2].startswith("- [R-003] jobs answer"))   # no blank tail left behind
        arch = ruleids.archive_entries(self.cfg)
        self.assertEqual(len(arch), 3)
        # full text and source tag kept, plus retired date and the rule that replaced it
        self.assertTrue(arch[0].startswith("- [R-004] jobs ran at 06:45 (2026-10-10 · y) 被「jobs answer four questions」取代"))
        self.assertEqual(ruleids.parse_meta(arch[0]), ("2026-10-14", "R-003"))
        self.assertEqual(ruleids.parse_meta(arch[1]), ("2026-10-24", "R-001"))
        self.assertEqual(ruleids.parse_meta(arch[2]), ("unknown", "unknown"))
        self.assertEqual(ruleids.normalize(self.cfg), {"numbered": [], "archived": []})   # idempotent
        self.assertEqual(len(ruleids.archive_entries(self.cfg)), 3)

    def test_rulecheck_reads_the_archive_and_finds_no_lost_fact(self):
        ruleids.normalize(self.cfg)
        b = self.t / "before.md"
        b.write_text(self.before)
        self.assertEqual(rulecheck.run(str(b), str(self.prefs)), 0)        # archive found next to preferences.md
        self.assertEqual(rulecheck.run(str(b), str(self.prefs), str(self.t / "none.md")), 1)  # without it: lost

    def test_rulecheck_fails_when_an_id_disappears(self):
        ruleids.normalize(self.cfg)
        before = self.prefs.read_text()
        after = before.replace("[R-002] ", "")
        miss = rulecheck.check(before, after, self.archive.read_text())
        self.assertEqual([m["fact"] for m in miss], ["R-002"])

    def test_rule_add_gets_next_free_id_and_ids_are_never_reused(self):
        ruleids.normalize(self.cfg)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "-t", "定时", "jobs say what they leave and who checks it")
        self.assertEqual(code, 0, out)
        self.assertIn("rule R-007 added", out)
        i = self.block().splitlines().index("### 定时任务")
        self.assertTrue(self.block().splitlines()[i + 1].startswith("- [R-007] jobs say what they leave"))
        # retire the newest rule by ID, then delete the archive's top ID by hand: the counter still moves on
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "-t", "定时", "--replaces", "R-007",
                                 "jobs say what output they leave, who checks it and how to undo it")
        self.assertEqual(code, 0, out)
        self.assertIn("rule R-008 added", out)
        self.archive.write_text(self.archive.read_text().replace("[R-007]", "[gone]"))
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "codex", "-t", "协作", "letters carry a status line")
        self.assertIn("rule R-009 added", out)

    def test_replaces_by_id_writes_archive_with_retired_date_and_replaced_by(self):
        ruleids.normalize(self.cfg)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "r-002",
                                 "letters never go to a shared inbox folder")
        self.assertEqual(code, 0, out)
        self.assertNotIn("letters uses inbox", self.block())
        top = ruleids.archive_entries(self.cfg)[0]          # newest first
        self.assertTrue(top.startswith("- [R-002] old rule about letters uses inbox (2026-10-18 · x) 被 R-007「"))
        retired, by = ruleids.parse_meta(top)
        self.assertEqual(by, "R-007")
        self.assertRegex(retired, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("replaces [R-002]", core.read_events(self.cfg)[-1]["summary"])

    def test_replaces_by_text_still_works_and_bad_id_is_refused(self):
        ruleids.normalize(self.cfg)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "R-999", "letters use one file each")
        self.assertEqual(code, 2)
        self.assertIn("rule R-999 (matched 0)", out)
        code, out = self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "--replaces", "letters uses inbox",
                                 "letters use one file each")
        self.assertEqual(code, 0, out)
        self.assertEqual(ruleids.parse_meta(ruleids.archive_entries(self.cfg)[0])[1], "R-007")

    def test_rule_list_shows_ids_and_archived(self):
        ruleids.normalize(self.cfg)
        code, out = self.run_cli("rule", "list")
        self.assertEqual(code, 0)
        self.assertIn("- [R-001] letters live on the mailbox branch only", out)
        self.assertNotIn("jobs ran at 06:45", out)
        code, out = self.run_cli("rule", "list", "--archived")
        self.assertIn("- [R-004] jobs ran at 06:45", out)

    def test_cycle_numbers_and_tool_copies_carry_ids(self):
        cfg_path = self.home / "config.toml"
        cfg_path.write_text(cfg_path.read_text().replace('label = "a"\n', 'label = "a"\nslim = true\n'))
        self.cfg = core.load_config()
        self.a.write_text(f"# A head\n\n{core.strip_superseded(GROUPED)}\n\nA tail\n")
        self.b.write_text(f"{GROUPED}\n")
        self.assertEqual(core.distribute(self.cfg)["conflicts"], [])          # the hub as it was before IDs
        rec = core.run_cycle(self.cfg, "first sync after the migration")
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["steps"]["distribute"]["result"]["conflicts"], [])
        for copy in (self.a, self.b):
            text = copy.read_text()
            self.assertIn("- [R-001] letters live on the mailbox branch only", text)
            self.assertNotIn("已取代", text)
        self.assertIn("A tail", self.a.read_text())
        # a hand edit that adds a rule without an ID is adopted, then numbered on the next cycle
        self.b.write_text(self.b.read_text().replace("### 定时任务\n", "### 定时任务\n- jobs log one line per run (2026-11-02 · z)\n"))
        core.run_cycle(self.cfg, "adopt")
        core.run_cycle(self.cfg, "number")
        self.assertIn("- [R-007] jobs log one line per run", self.block())
        self.assertIn("- [R-007] jobs log one line per run", self.a.read_text())

    def test_copy_without_sync_history_that_only_lacks_ids_is_overwritten_not_a_conflict(self):
        ruleids.normalize(self.cfg)
        bare = core.extract_block(self.prefs.read_text()).replace("[R-001] ", "").replace("[R-002] ", "")
        self.a.write_text(f"# A head\n\n{bare}\n")
        self.b.write_text(f"{bare}\n")
        rep = core.distribute(self.cfg)
        self.assertEqual(rep["conflicts"], [])
        self.assertIn("- [R-001] letters live", self.a.read_text())

    def test_news_hook_does_not_report_a_rule_that_only_gained_its_id(self):
        from ai_sync import hooks
        hooks.news(self.cfg, "codex", "s1")                       # cursor set before the numbering
        ruleids.normalize(self.cfg)
        self.assertEqual(hooks.news(self.cfg, "codex", "s1")["new_rules"], [])
        self.run_cli("rule", "add", "--judgment-only", "test rule", "-a", "x", "-t", "协作", "letters carry a status line")
        self.assertEqual(len(hooks.news(self.cfg, "codex", "s1")["new_rules"]), 1)

    def test_archive_is_tracked_by_the_hub_git(self):
        self.assertIn(ruleids.ARCHIVE, core.TRACKED)

    def test_meta_rule_import_gets_an_id(self):
        core.run_cycle(self.cfg, "first sight")
        lab_agents = self.lab / "AGENTS.md"
        lab_agents.write_text(lab_agents.read_text().replace(
            "## Docs", "- 2026-11-02 03:00 CST: every repo ships a CLI. Reason: x.\n\n## Docs"))
        collab.harvest_rules(self.cfg)
        line = next(l for l in collab.rule_list(self.cfg) if "every repo ships a CLI" in l)
        self.assertRegex(line, r"^- \[R-\d{3}\] 2026-11-02 03:00 CST \(from team-handbook AGENTS.md\)")


if __name__ == "__main__":
    unittest.main()
