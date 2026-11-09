"""Every rule says what enforces it (2026-11-02). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import tomllib
import unittest

from ai_sync import collab, core, enforcement, ruleids

from test_collab import CollabBase

GROUPED = "\n".join([
    "<!-- owner-preferences:start (shared) -->",
    "## Owner preferences",
    "### 最高优先级",
    "- [R-001] Meta rule: claims are checked before they are made (2026-10-27 · x)",
    "- [R-002] Meta rule: decide in the relative execution mode (2026-10-27 · x)",
    "### 协作与派单",
    "- [R-003] letters live on the mailbox branch only (2026-10-24 · x)",
    core.BLOCK_END])


class EnforcementTests(CollabBase):
    def setUp(self):
        super().setUp()
        cfg_file = self.home / "config.toml"                 # off by default; these tests are about the strict mode
        cfg_file.write_text("require_enforcer = true\n" + cfg_file.read_text())
        self.cfg = core.load_config()
        self.prefs = self.home / "preferences.md"
        self.prefs.write_text(f"# truth\n\n{GROUPED}\n")
        self.test_file = self.t / "test_claims.py"
        self.test_file.write_text("def test_x(): pass\n")

    def sidecar(self) -> dict:
        return tomllib.loads(enforcement.path(self.cfg).read_text())

    def add(self, *extra):
        return self.run_cli("rule", "add", "-a", "x", "-t", "协作", *extra, "letters carry a status line")

    def test_without_require_enforcer_a_rule_needs_no_enforcer(self):
        cfg_file = self.home / "config.toml"
        cfg_file.write_text(cfg_file.read_text().replace("require_enforcer = true\n", ""))
        self.cfg = core.load_config()
        self.assertEqual(self.add()[0], 0)
        self.assertFalse(enforcement.path(self.cfg).exists())

    def test_rule_add_without_an_enforcer_is_refused(self):
        code, out = self.add()
        self.assertEqual(code, 2)
        self.assertIn("--enforced-by", out)
        self.assertNotIn("status line", self.prefs.read_text())
        code, out = self.add("--enforced-by", str(self.test_file), "--judgment-only", "both")   # not both
        self.assertEqual(code, 2)

    def test_nonexistent_path_is_refused(self):
        code, out = self.add("--enforced-by", f"{self.test_file},{self.t / 'nope.py'}")
        self.assertEqual(code, 2)
        self.assertIn("nope.py", out)
        self.assertFalse(enforcement.path(self.cfg).exists())
        self.assertNotIn("status line", self.prefs.read_text())

    def test_valid_path_adds_the_rule_and_writes_the_sidecar(self):
        code, out = self.add("--enforced-by", str(self.test_file))
        self.assertEqual(code, 0, out)
        rid = ruleids.bullet_id(next(l for l in collab.rule_list(self.cfg) if "status line" in l))
        self.assertEqual(self.sidecar()[rid], {"enforced_by": [str(self.test_file)]})
        self.assertNotIn("enforced_by", self.prefs.read_text())            # nothing added to the loaded block

    def test_replaces_also_needs_an_enforcer_and_judgment_only_is_recorded(self):
        code, out = self.run_cli("rule", "add", "-a", "x", "-t", "协作", "--replaces", "R-003",
                                 "letters live on the mailbox branch, one file each")
        self.assertEqual(code, 2)
        code, out = self.run_cli("rule", "add", "-a", "x", "-t", "协作", "--replaces", "R-003",
                                 "--judgment-only", "where a letter goes is decided by the writer",
                                 "letters live on the mailbox branch, one file each")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sidecar()["R-004"], {"judgment_only": "where a letter goes is decided by the writer"})

    def test_enforcement_counts_and_exit_codes(self):
        code, out = self.run_cli("rule", "enforcement", "--json")
        rep = json.loads(out)
        self.assertEqual(code, 1)                                           # two Meta rules with none
        for key in ("total", "enforced", "judgment_only", "none", "meta_none", "missing_paths"):
            self.assertIn(key, rep)
        self.assertEqual((rep["total"], rep["enforced"], rep["judgment_only"], rep["none"]), (3, 0, 0, 3))
        self.assertEqual(rep["meta_none"], ["R-001", "R-002"])
        enforcement.put(self.cfg, "R-001", {"enforced_by": [str(self.test_file)], "note": "warn only"})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "a judgment by definition"})
        code, out = self.run_cli("rule", "enforcement", "--json")
        rep = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertEqual((rep["enforced"], rep["judgment_only"], rep["none"], rep["meta_none"]), (1, 1, 1, []))
        self.test_file.unlink()                                             # an enforcer that is gone fails the check
        code, out = self.run_cli("rule", "enforcement")
        self.assertEqual(code, 1)
        self.assertIn(f"MISSING R-001: {self.test_file}", out)

    def test_meta_rule_import_is_recorded_as_none_not_blocked(self):
        core.run_cycle(self.cfg, "first sight")
        agents = self.lab / "AGENTS.md"
        agents.write_text(agents.read_text().replace(
            "## Docs", "- 2026-11-02 03:00 CST: every repo ships a CLI. Reason: x.\n\n## Docs"))
        self.assertEqual(len(collab.harvest_rules(self.cfg)["imported"]), 1)
        rid = ruleids.bullet_id(next(l for l in collab.rule_list(self.cfg) if "ships a CLI" in l))
        self.assertIn("none", self.sidecar()[rid])
        self.assertEqual(enforcement.report(self.cfg)["none"], 4)

    def test_sidecar_is_tracked_by_the_hub_git(self):
        self.assertIn(enforcement.FILE, core.TRACKED)

    def test_sidecar_round_trips_quotes_and_unicode(self):
        enforcement.put(self.cfg, "R-010", {"judgment_only": 'Alex 说 "测试级别"\\ ok'})
        enforcement.put(self.cfg, "R-002", {"enforced_by": ["~/a b/c.py"]})
        self.assertEqual(self.sidecar()["R-010"]["judgment_only"], 'Alex 说 "测试级别"\\ ok')
        self.assertEqual(list(self.sidecar()), ["R-002", "R-010"])           # ID order


if __name__ == "__main__":
    unittest.main()
