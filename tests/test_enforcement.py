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
        self.assertEqual(rep["unplanned"], ["R-001", "R-002", "R-003"])
        enforcement.put(self.cfg, "R-001", {"enforced_by": [str(self.test_file)], "note": "warn only"})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "a judgment by definition"})
        code, out = self.run_cli("rule", "enforcement", "--json")
        self.assertEqual(code, 1, out)                                      # R-003: none and nobody said what its check is
        self.assertEqual(json.loads(out)["unplanned"], ["R-003"])
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "a test that letters stay on one branch",
                                 "--size", "M")
        self.assertEqual(code, 0, out)
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

    def test_legacy_none_key_counts_as_unplanned(self):
        enforcement.put(self.cfg, "R-003", {"none": "imported without an enforcer"})
        rep = enforcement.report(self.cfg)
        self.assertEqual((rep["none"], rep["unplanned"]), (3, ["R-001", "R-002", "R-003"]))

    def test_enforce_sets_an_existing_rule_and_closes_its_plan(self):
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "a  test on every letter writer", "--size", "s",
                                 "--since", "2026-11-02")
        self.assertEqual(code, 0, out)
        self.assertIn("R-003: none -> none (planned S since 2026-11-02)", out)
        self.assertEqual(self.sidecar()["R-003"],
                         {"planned": "a test on every letter writer", "size": "S", "since": "2026-11-02"})
        code, out = self.run_cli("rule", "enforce", "R-003", "--judgment-only", "nobody wrote it")
        self.assertEqual(code, 2)                                           # a possible check is not a judgment
        self.assertIn("--reason", out)
        code, out = self.run_cli("rule", "enforce", "R-003", "--enforced-by", str(self.test_file), "--note", "warn only")
        self.assertEqual(code, 0, out)
        self.assertIn("R-003: none -> enforced (1 path(s))", out)
        self.assertEqual(self.sidecar()["R-003"], {"enforced_by": [str(self.test_file)], "note": "warn only"})
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "the reply half", "--size", "M")
        self.assertEqual(code, 0, out)                                      # a gap beside the enforcer keeps both
        e = self.sidecar()["R-003"]
        self.assertEqual((e["enforced_by"], e["planned"], e["note"]), ([str(self.test_file)], "the reply half", "warn only"))
        self.assertEqual(enforcement.report(self.cfg)["enforced"], 1)
        code, out = self.run_cli("rule", "enforce", "R-003", "--judgment-only", "x", "--reason", "the check cannot exist")
        self.assertEqual(code, 0, out)
        e = self.sidecar()["R-003"]                                         # the dropped plan and the reason stay on record
        self.assertEqual((e["judgment_only"], sorted(e)), ("x", ["judgment_only", "note"]))
        self.assertIn("the reply half. Dropped", e["note"])

    def test_enforce_refuses_unknown_ids_bad_sizes_and_missing_paths(self):
        for argv in (("R-999", "--judgment-only", "x"), ("R-003",), ("R-003", "--planned", "a check"),
                     ("R-003", "--planned", "a check", "--size", "XL"), ("R-003", "--enforced-by", str(self.t / "nope.py")),
                     ("R-003", "--planned", "a check", "--size", "S", "--since", "yesterday"),
                     ("R-003", "--enforced-by", str(self.test_file), "--judgment-only", "x")):
            code, out = self.run_cli("rule", "enforce", *argv)
            self.assertEqual(code, 2, argv)
        self.assertFalse(enforcement.path(self.cfg).exists())

    def test_enforce_logs_the_id_in_refs_not_in_the_summary(self):
        code, out = self.run_cli("rule", "enforce", "R-003", "-a", "x", "--judgment-only", "a judgment")
        self.assertEqual(code, 0, out)
        ev = core.read_events(self.cfg)[-1]
        self.assertEqual((ev["kind"], ev["refs"]), ("rule_change", {"rule": "R-003"}))
        self.assertNotIn("R-003", ev["summary"])                            # tidy counts IDs in summaries as use

    def test_rule_add_takes_a_planned_check(self):
        code, out = self.add("--planned", "a lint on the status line", "--size", "S")
        self.assertEqual(code, 0, out)
        rid = ruleids.bullet_id(next(l for l in collab.rule_list(self.cfg) if "status line" in l))
        self.assertEqual((self.sidecar()[rid]["planned"], self.sidecar()[rid]["size"]), ("a lint on the status line", "S"))
        code, out = self.add("--planned", "x", "--size", "S", "--judgment-only", "y")
        self.assertEqual(code, 2)

    def test_paths_are_stored_absolute_whatever_the_callers_directory(self):
        import os
        here = os.getcwd()
        link = self.t / "hook.py"
        link.symlink_to(self.test_file)
        try:
            os.chdir(self.t)
            code, out = self.run_cli("rule", "enforce", "R-003", "--enforced-by", "test_claims.py,hook.py")
            self.assertEqual(code, 0, out)
        finally:
            os.chdir(here)
        stored = self.sidecar()["R-003"]["enforced_by"]
        self.assertTrue(all(os.path.isabs(os.path.expanduser(p)) for p in stored), stored)
        self.assertTrue(stored[1].endswith("hook.py"), stored)              # a symlink keeps the name people know
        rep = enforcement.report(self.cfg)
        self.assertEqual((rep["missing_paths"], rep["relative_paths"]), ([], []))

    def test_relative_path_is_reported_and_rebase_repairs_it(self):
        enforcement.put(self.cfg, "R-001", {"enforced_by": ["test_claims.py", "gone.py"]})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-003", {"judgment_only": "j"})
        code, out = self.run_cli("rule", "enforcement")
        self.assertEqual(code, 1)
        self.assertIn("RELATIVE R-001: test_claims.py -> aisync rule enforcement --rebase", out)
        code, out = self.run_cli("rule", "enforcement", "--rebase", str(self.t))
        self.assertIn("rebased 1, still relative 1", out)
        self.assertEqual(self.sidecar()["R-001"]["enforced_by"][1], "gone.py")
        self.assertTrue(self.sidecar()["R-001"]["enforced_by"][0].endswith("/test_claims.py"))
        self.assertEqual(code, 1)                                           # gone.py is still relative

    def test_overdue_plan_fails_the_check_and_says_the_next_step(self):
        from datetime import date
        enforcement.put(self.cfg, "R-001", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-003", {"planned": "a letter lint", "size": "S", "since": "2026-11-02"})
        rep = enforcement.report(self.cfg, today=date(2026, 11, 9))         # day 7 of an S: still in time
        self.assertEqual((rep["ok"], rep["overdue"], rep["planned"][0]["days"]), (True, [], 7))
        rep = enforcement.report(self.cfg, today=date(2026, 11, 10))
        self.assertEqual((rep["ok"], rep["overdue"]), (False, ["R-003"]))
        text = "\n".join(enforcement.lines(rep))
        self.assertIn("OVERDUE R-003 (S, since 2026-11-02, 8 d): a letter lint -> write it, then "
                      "aisync rule enforce R-003 --enforced-by <path>", text)
        self.assertIn("none 1 (planned 1, no plan 0)", text)

    def test_prune_drops_only_archived_ids_and_refuses_an_unreadable_block(self):
        enforcement.put(self.cfg, "R-003", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-700", {"judgment_only": "retired"})
        enforcement.put(self.cfg, "R-701", {"judgment_only": "unknown id"})
        (self.home / ruleids.ARCHIVE).write_text("- [R-700] an old rule · retired: 2026-11-08\n")
        self.assertEqual(enforcement.report(self.cfg)["stale"], ["R-700", "R-701"])
        code, out = self.run_cli("rule", "enforcement", "--prune")
        self.assertIn("pruned 1 entries of retired rules; kept 1 not in the archive: R-701", out)
        self.assertEqual(sorted(self.sidecar()), ["R-003", "R-701"])
        self.prefs.write_text("# truth, block lost\n")
        with self.assertRaises(ValueError):
            enforcement.prune(self.cfg)
        self.assertEqual(sorted(self.sidecar()), ["R-003", "R-701"])

    def test_a_plans_clock_only_runs_moving_it_needs_a_reason(self):
        enforcement.put(self.cfg, "R-003", {"planned": "a letter lint", "size": "S", "since": "2026-10-01"})
        for argv in (("--planned", "a letter lint", "--size", "L"), ("--planned", "a letter lint", "--since", "2026-11-01"),
                     ("--planned", "a letter lint", "--since", "2099-01-01", "--reason", "later"),
                     ("--enforced-by", str(self.test_file), "--planned", "the rest", "--size", "L")):
            code, out = self.run_cli("rule", "enforce", "R-003", *argv)
            self.assertEqual(code, 2, (argv, out))
        self.assertEqual(self.sidecar()["R-003"], {"planned": "a letter lint", "size": "S", "since": "2026-10-01"})
        code, out = self.run_cli("rule", "enforce", "r-003", "--planned", "a sharper letter lint")   # same size, same day
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sidecar()["R-003"], {"planned": "a sharper letter lint", "size": "S", "since": "2026-10-01"})
        code, out = self.run_cli("rule", "enforce", "R-003", "--enforced-by", str(self.test_file), "--planned", "the reply half")
        self.assertEqual(code, 0, out)                                      # a part got its enforcer: the gap keeps its day
        self.assertEqual(self.sidecar()["R-003"]["since"], "2026-10-01")
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "the reply half", "--size", "M",
                                 "--reason", "needs the mail fixtures first")
        self.assertEqual(code, 0, out)
        e = self.sidecar()["R-003"]
        self.assertEqual((e["size"], e["since"]), ("M", "2026-10-01"))
        self.assertIn("Plan moved", e["note"])
        self.assertIn("needs the mail fixtures first", e["note"])
        ev = core.read_events(self.cfg)[-1]                                 # logged with no agent named
        self.assertEqual((ev["agent"], ev["refs"]), ("unknown", {"rule": "R-003"}))
        self.assertIn("needs the mail fixtures first", ev["summary"])

    def test_a_dead_enforcer_becomes_a_plan(self):
        other = self.t / "test_other.py"
        other.write_text("def test_y(): pass\n")
        enforcement.put(self.cfg, "R-003", {"enforced_by": [str(self.test_file), str(other)], "note": "two tests"})
        self.test_file.unlink()
        self.assertIn("--planned", "\n".join(enforcement.lines(enforcement.report(self.cfg))))
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "rewrite the claims test", "--size", "S")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sidecar()["R-003"]["enforced_by"], [str(other)])   # the living one stays
        self.assertEqual(enforcement.report(self.cfg)["missing_paths"], [])
        other.unlink()
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "rewrite both tests")
        self.assertEqual(code, 0, out)
        self.assertIn("R-003: enforced -> none (planned S", out)
        self.assertNotIn("enforced_by", self.sidecar()["R-003"])

    def test_planned_turns_a_judgment_into_a_plan_and_a_dropped_plan_leaves_its_trace(self):
        enforcement.put(self.cfg, "R-003", {"judgment_only": "a checker is on its way"})
        code, out = self.run_cli("rule", "enforce", "R-003", "--planned", "the checker", "--size", "M")
        self.assertEqual(code, 0, out)
        e = self.sidecar()["R-003"]
        self.assertNotIn("judgment_only", e)
        self.assertEqual((e["planned"], e["note"]), ("the checker", "was judgment_only: a checker is on its way"))
        code, out = self.run_cli("rule", "enforce", "R-003", "--judgment-only", "x", "--planned", "y", "--size", "S")
        self.assertEqual(code, 2)
        code, out = self.run_cli("rule", "enforce", "R-003", "--judgment-only", "only a reader can tell",
                                 "--reason", "the checker cannot see intent")
        self.assertEqual(code, 0, out)
        e = self.sidecar()["R-003"]
        self.assertEqual(e["judgment_only"], "only a reader can tell")
        self.assertIn("was planned (M, since", e["note"])
        self.assertIn("the checker cannot see intent", e["note"])

    def test_replacing_a_planned_rule_keeps_its_plan_and_its_day(self):
        enforcement.put(self.cfg, "R-003", {"planned": "a letter lint", "size": "S", "since": "2026-10-01"})
        code, out = self.run_cli("rule", "add", "-a", "x", "-t", "协作", "--replaces", "R-003", "--judgment-only", "j",
                                 "letters live on the mailbox branch, one file each")
        self.assertEqual(code, 2)
        self.assertIn("planned check", out)
        self.assertIn("[R-003]", "\n".join(collab.rule_list(self.cfg)))
        code, out = self.run_cli("rule", "add", "-a", "x", "-t", "协作", "--replaces", "R-003", "--planned", "a letter lint",
                                 "--size", "S", "letters live on the mailbox branch, one file each")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sidecar()["R-004"]["since"], "2026-10-01")

    def test_hand_edited_registry_does_not_crash_or_hide_a_plan(self):
        from datetime import date
        enforcement.put(self.cfg, "R-001", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "j"})
        p = enforcement.path(self.cfg)
        p.write_text(p.read_text() + '[R-003]\nplanned = "a lint"\nsize = "S"\n')            # no since at all
        rep = enforcement.report(self.cfg, today=date(2026, 11, 12))
        self.assertEqual((rep["overdue"], rep["ok"]), (["R-003"], False))
        p.write_text(p.read_text() + "since = 2026-10-01\n")                                   # an unquoted TOML date
        self.assertEqual(enforcement.report(self.cfg, today=date(2026, 11, 12))["planned"][0]["days"], 42)
        code, out = self.run_cli("rule", "enforce", "R-002", "--note", "still a judgment")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sidecar()["R-003"]["since"], "2026-10-01")                        # written back as a string
        enforcement.put(self.cfg, "R-003", {"planned": "a lint", "size": "S", "since": "2099-01-01"})
        self.assertEqual(enforcement.report(self.cfg, today=date(2026, 11, 12))["overdue"], ["R-003"])

    def test_no_rules_read_fails_and_small_path_cases(self):
        import os
        self.prefs.write_text("# truth, block lost\n")
        code, out = self.run_cli("rule", "enforcement")
        self.assertEqual(code, 1)
        self.assertIn("NO RULES READ", out)
        self.assertEqual(enforcement._tilde(str(os.path.expanduser("~")) + "2/z.py"), os.path.expanduser("~") + "2/z.py")
        self.assertEqual(enforcement._tilde(os.path.join(os.path.expanduser("~"), "a.py")), "~/a.py")
        with self.assertRaises(ValueError):
            enforcement.check_args(str(self.t), "")                                             # a directory is no enforcer

    def test_prune_with_json_prints_only_json_on_stdout(self):
        import contextlib, io
        from ai_sync import cli
        enforcement.put(self.cfg, "R-700", {"judgment_only": "retired"})
        (self.home / ruleids.ARCHIVE).write_text("- [R-700] an old rule · retired: 2026-11-08\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main(["rule", "enforcement", "--prune", "--json"])
        self.assertEqual(json.loads(out.getvalue())["stale"], [])
        self.assertIn("pruned 1", err.getvalue())

    def test_a_hub_that_does_not_require_enforcers_is_not_failed_for_unlabelled_rules(self):
        enforcement.put(self.cfg, "R-001", {"judgment_only": "j"})
        enforcement.put(self.cfg, "R-002", {"judgment_only": "j"})
        self.assertFalse(enforcement.report(self.cfg)["ok"])                # R-003 has no plan: strict by default
        self.cfg.require_enforcer = False
        rep = enforcement.report(self.cfg)
        self.assertEqual((rep["ok"], rep["unplanned"]), (True, ["R-003"]))  # still listed, not failed

    def test_sidecar_is_tracked_by_the_hub_git(self):
        self.assertIn(enforcement.FILE, core.TRACKED)

    def test_sidecar_round_trips_quotes_and_unicode(self):
        enforcement.put(self.cfg, "R-010", {"judgment_only": 'Alex 说 "测试级别"\\ ok'})
        enforcement.put(self.cfg, "R-002", {"enforced_by": ["~/a b/c.py"]})
        self.assertEqual(self.sidecar()["R-010"]["judgment_only"], 'Alex 说 "测试级别"\\ ok')
        self.assertEqual(list(self.sidecar()), ["R-002", "R-010"])           # ID order


if __name__ == "__main__":
    unittest.main()
