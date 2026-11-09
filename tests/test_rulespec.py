"""rulespec: shared rules as interfaces (When / Do / Not), owner decision 2026-11-08."""
from __future__ import annotations

import json
import unittest

from ai_sync import core, rulespec

from test_collab import TopicRuleTests

GOOD = ("- [R-333] When: any Deploys step. Do: the AI drives the browser step by step and says each step. "
        "Not: a background run. (2026-11-02 · claude-code, ops-jobs)")


class ParseTests(unittest.TestCase):
    def test_good_rule_parses_into_fields(self):
        s = rulespec.parse(GOOD)
        self.assertEqual(s.id, "R-333")
        self.assertEqual(s.when, "any Deploys step.")
        self.assertTrue(s.do.startswith("the AI drives"))
        self.assertEqual(s.not_, "a background run.")
        self.assertEqual(s.tag, "(2026-11-02 · claude-code, ops-jobs)")
        self.assertEqual(rulespec.problems(GOOD), [])

    def test_not_is_optional_and_owner_tag_counts(self):
        r = "- [R-094] When: replying to the owner. Do: write in Chinese; code stays English. (owner · hand-written)"
        self.assertEqual(rulespec.problems(r), [])
        self.assertEqual(rulespec.parse(r).not_, "")

    def test_meta_prefix_is_allowed_and_counted_by_enforcement(self):
        r = "- [R-007] Meta rule. When: a task repeats. Do: hand it to a worker. (2026-10-31 · claude-code, x)"
        self.assertEqual(rulespec.problems(r), [])
        self.assertTrue(rulespec.parse(r).meta)
        self.assertFalse(rulespec.parse(GOOD).meta)
        from ai_sync import enforcement
        self.assertTrue(enforcement._META.match(r))

    def test_nested_parentheses_in_tag(self):
        r = "- [R-001] When: x happens. Do: y. (2026-10-12 · claude-code, ai-sync; follow-up (E127) 2026-10-13)"
        self.assertEqual(rulespec.problems(r), [])

    def test_free_text_and_wrong_order_are_rejected(self):
        self.assertTrue(any("not in the shape" in p for p in rulespec.problems("- [R-100] always do x (2026-10-12 · a)")))
        self.assertTrue(rulespec.problems("- [R-100] Do: y. When: x. (2026-10-12 · a)"))
        self.assertTrue(rulespec.problems("- [R-100] When: x. Do: y. When: z. (2026-10-12 · a)"))

    def test_empty_field_long_body_missing_id_and_tag(self):
        self.assertIn("Do: is empty", rulespec.problems("- [R-100] When: x happens. Do: . (2026-10-12 · a)"))
        long = "- [R-100] When: x. Do: " + "y" * rulespec.MAX_BODY + ". (2026-10-12 · a)"
        self.assertTrue(any("over" in p for p in rulespec.problems(long)))
        p = rulespec.problems("When: x happens. Do: y now.")
        self.assertIn("no [R-###] id", p)
        self.assertTrue(any("source tag" in q for q in p))
        self.assertEqual(rulespec.problems("When: x happens. Do: y now.", need_id=False, need_tag=False), [])

    def test_compose_and_lint_counts(self):
        self.assertEqual(rulespec.compose("x happens", "do y.", "z"), "When: x happens. Do: do y. Not: z.")
        self.assertEqual(rulespec.compose("x", "y"), "When: x. Do: y.")
        with self.assertRaises(ValueError):
            rulespec.compose("", "y")
        rep = rulespec.lint([GOOD[2:], "[R-002] free text (2026-10-12 · a)"])
        self.assertEqual((rep["rules"], rep["fit"], len(rep["bad"])), (2, 1, 1))
        self.assertEqual(rep["bad"][0]["id"], "R-002")


class SpecModeTests(TopicRuleTests):
    def _spec_on(self):
        self._grouped()
        p = self.home / "config.toml"
        p.write_text("rule_spec = true\n" + p.read_text())
        self.cfg = core.load_config(self.home)

    def test_spec_mode_refuses_free_text_and_takes_fields(self):
        self._spec_on()
        code, out = self.run_cli("rule", "add", "--judgment-only", "t", "-a", "codex", "-t", "协作",
                                 "always check duplicates before sending a task")
        self.assertEqual(code, 2)
        self.assertIn("not an interface", out)
        code, out = self.run_cli("rule", "add", "--judgment-only", "t", "-a", "codex", "-t", "协作",
                                 "--when", "before sending a task", "--do", "search open tasks for duplicates",
                                 "--not", "a second letter on the same topic")
        self.assertEqual(code, 0, out)
        line = next(l for l in core.extract_block((self.home / "preferences.md").read_text()).splitlines()
                    if "search open tasks" in l)
        self.assertEqual(rulespec.problems(line), [])

    def test_fields_need_both_when_and_do(self):
        self._spec_on()
        code, out = self.run_cli("rule", "add", "--judgment-only", "t", "-a", "x", "-t", "协作", "--do", "y")
        self.assertEqual(code, 2)
        self.assertIn("--when and --do", out)

    def test_default_mode_keeps_free_text(self):
        self._grouped()
        code, out = self.run_cli("rule", "add", "--judgment-only", "t", "-a", "x", "-t", "协作", "free text rule here")
        self.assertEqual(code, 0, out)

    def test_lint_exit_codes(self):
        self._grouped()
        code, out = self.run_cli("rule", "lint", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fit"], 0)
        p = self.home / "preferences.md"
        text = p.read_text()
        for old in ("old rule about letters uses inbox", "jobs answer four questions"):
            text = text.replace(f"- {old} (", f"- [R-9{len(old)}] When: a test runs. Do: {old}. (")
        p.write_text(text)
        code, out = self.run_cli("rule", "lint")
        self.assertEqual(code, 0, out)
        self.assertIn("interface shape 2, not yet 0", out)


if __name__ == "__main__":
    unittest.main()


class RewriteTests(TopicRuleTests):
    def _ids(self):
        self._grouped()
        p = self.home / "preferences.md"
        p.write_text(p.read_text().replace("- old rule about letters uses inbox (", "- [R-101] old rule about letters uses inbox ~/team-inbox/inbox (")
                     .replace("- jobs answer four questions (", "- [R-102] jobs answer four questions (")
                     .replace("(2026-10-14 · y)", "(2026-10-14 · y)"))

    def _write(self, data: dict):
        f = self.t / "rw.json"
        f.write_text(json.dumps(data, ensure_ascii=False))
        return self.run_cli("rule", "rewrite", "-a", "claude-code", "--file", str(f))

    def test_rewrite_keeps_id_tag_and_archives_old_text(self):
        self._ids()
        code, out = self._write({"R-101": "When: writing a letter. Do: put it in ~/team-inbox/inbox."})
        self.assertEqual(code, 0, out)
        blk = core.extract_block((self.home / "preferences.md").read_text())
        line = next(l for l in blk.splitlines() if l.startswith("- [R-101]"))
        self.assertEqual(line, "- [R-101] When: writing a letter. Do: put it in ~/team-inbox/inbox. (2026-10-18 · x)")
        arch = (self.home / "rules-archive.md").read_text()
        self.assertIn("old rule about letters uses inbox", arch)
        self.assertIn("被 R-101「When: writing a letter. 」取代", arch)
        self.assertIn("rewritten as When/Do/Not", core.read_events(self.cfg)[-1]["summary"])

    def test_rewrite_refuses_lost_fact_bad_shape_and_unknown_id(self):
        self._ids()
        before = (self.home / "preferences.md").read_text()
        code, out = self._write({"R-101": "When: writing a letter. Do: put it in the inbox."})  # path dropped
        self.assertEqual(code, 1)
        self.assertIn("LOST FACT", out)
        self.assertIn("~/team-inbox/inbox", out)
        code, out = self._write({"R-101": "put letters in the inbox ~/team-inbox/inbox", "R-999": "When: a. Do: b."})
        self.assertEqual(code, 1)
        self.assertIn("REFUSED R-101", out)
        self.assertIn("REFUSED R-999: unknown rule id", out)
        self.assertEqual((self.home / "preferences.md").read_text(), before)   # nothing written
        self.assertFalse((self.home / "rules-archive.md").exists() and "When: writing" in (self.home / "rules-archive.md").read_text())

    def test_named_drop_is_allowed_and_logged(self):
        self._ids()
        code, out = self._write({"R-101": {"text": "When: writing a letter. Do: put it in the inbox.",
                                            "drop": {"~/team-inbox/inbox": "path moved, test"}}})
        self.assertEqual(code, 0, out)
        self.assertIn("dropped from R-101: ~/team-inbox/inbox (path moved, test)", out)
        self.assertIn("dropped", core.read_events(self.cfg)[-1]["summary"])

    def test_rewrite_needs_file(self):
        self._ids()
        code, out = self.run_cli("rule", "rewrite", "-a", "x")
        self.assertEqual(code, 2)
