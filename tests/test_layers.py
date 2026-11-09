"""layers: a layered target carries core topics in full, folds the rest, and gets them injected on demand."""
from __future__ import annotations

import json

from ai_sync import core, hooks, layers

from test_ai_sync import Base

HEAD = "<!-- owner-preferences:start (test) -->"
TRUTH = "\n".join([
    HEAD, "## Owner preferences", "",
    "### 最高优先级",
    "- [R-005] When: rules conflict. Do: safety first. (2026-11-02 · a)",
    "",
    "### Deploys 发布",
    "- [R-333] When: any Deploys step. Do: drive the browser live. (2026-11-02 · a)",
    "- [R-345] When: opening a tab. Do: at most 2 tabs. (2026-11-06 · a)",
    "",
    "### Notion 与文档",
    "- [R-071] When: notifying Alex. Do: use Notion. (2026-10-26 · a)",
    "",
    core.BLOCK_END,
])
CORE = ["最高优先级"]
SHELF = {"Deploys": ["deploys", "terraform", "发布"], "Notion": ["notion", "日报"]}


class ViewTests(Base):
    def test_core_view_folds_other_topics(self):
        v = layers.core_view(TRUTH, CORE, SHELF)
        self.assertIn("R-005", v)
        self.assertNotIn("R-333", v)
        self.assertIn("_Shelved: 2 rules, injected when your work touches deploys, terraform, 发布.", v)
        self.assertIn('`aisync rule list --topic "Deploys 发布"`', v)
        self.assertTrue(v.endswith(core.BLOCK_END))

    def test_merge_back_keeps_folded_rules_and_core_edits(self):
        v = layers.core_view(TRUTH, CORE, SHELF).replace("Do: safety first.", "Do: safety lines first.")
        full = layers.merge_back(v, TRUTH, CORE)
        self.assertIn("Do: safety lines first.", full)
        self.assertIn("R-333", full)
        self.assertIn("R-071", full)
        self.assertNotIn("_Shelved", full)
        self.assertEqual(layers.merge_back(layers.core_view(TRUTH, CORE, SHELF), TRUTH, CORE), TRUTH)

    def test_matches_and_default_keywords(self):
        self.assertEqual(layers.matches("open Terraform and apply", TRUTH, CORE, SHELF), ["Deploys 发布"])
        self.assertEqual(layers.matches("write it into Notion", TRUTH, CORE, {}), ["Notion 与文档"])   # heading word
        self.assertEqual(layers.matches("rules conflict", TRUTH, CORE, SHELF), [])
        self.assertEqual(layers.matches("", TRUTH, CORE, SHELF), [])
        self.assertEqual(layers.matches("the deployser prompt", TRUTH, CORE, SHELF), [])   # whole words only
        self.assertEqual(layers.matches("去发布一下", TRUTH, CORE, SHELF), ["Deploys 发布"])    # Chinese: substring


class LayeredTargetTests(Base):
    def _setup(self):
        (self.home / "preferences.md").write_text(f"# truth\n\n{TRUTH}\n")
        self.a.write_text(f"# A\n\n{TRUTH}\n")
        self.b.write_text(f"{TRUTH}\n")
        core.distribute(self.cfg)                       # sync history exists before layering is switched on
        cfg = (self.home / "config.toml").read_text()
        cfg = cfg.replace('label = "a"\n', 'label = "a"\nlayer = "core"\n')
        cfg = 'core_topics = ["最高优先级"]\n[shelf]\nDeploys = ["deploys", "terraform"]\n\n' + cfg
        # TOML: [shelf] would swallow the keys after it, so put the tables last
        top, rest = cfg.split("[shelf]\n", 1)
        shelf_lines, after = rest.split("\n\n", 1)
        (self.home / "config.toml").write_text(top + after + "\n[shelf]\n" + shelf_lines + "\n")
        self.cfg = core.load_config(self.home)
        self.assertEqual(self.cfg.core_topics, ["最高优先级"])
        self.assertEqual([t.layer for t in self.cfg.targets], ["core", ""])

    def test_distribute_folds_layered_target_only(self):
        self._setup()
        core.distribute(self.cfg)
        self.assertNotIn("R-333", self.a.read_text())
        self.assertIn("_Shelved", self.a.read_text())
        self.assertIn("R-333", self.b.read_text())
        rep = core.distribute(self.cfg)
        self.assertEqual(sorted(rep["in_sync"]), ["a", "b"])               # stable, no ping-pong

    def test_edit_in_layered_copy_is_adopted_without_losing_shelved_rules(self):
        self._setup()
        core.distribute(self.cfg)
        self.a.write_text(self.a.read_text().replace("Do: safety first.", "Do: safety lines first."))
        rep = core.distribute(self.cfg)
        self.assertEqual(rep["adopted"], ["a"])
        truth = (self.home / "preferences.md").read_text()
        self.assertIn("Do: safety lines first.", truth)
        self.assertIn("R-333", truth)
        self.assertIn("R-071", truth)
        self.assertIn("Do: safety lines first.", self.b.read_text())

    def test_hook_injects_a_shelved_topic_once_per_session(self):
        self._setup()
        p = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "继续 Deploys，打开 terraform"}
        hooks.handle("a", {"hook_event_name": "SessionStart", "session_id": "s1"}, self.cfg)
        ctx = json.loads(hooks.handle("a", p, self.cfg))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Shelved rules now in play (Deploys 发布, 2 rules", ctx)
        self.assertIn("R-345", ctx)
        again = hooks.handle("a", p, self.cfg)
        self.assertNotIn("Shelved rules", again)                                # once per session
        other = hooks.handle("a", dict(p, session_id="s2"), self.cfg)
        self.assertIn("Shelved rules", other)                                  # a new session gets it again
        tool = {"hook_event_name": "PostToolUse", "session_id": "s3", "tool_name": "Bash",
                "tool_input": {"command": "python3 deploys_tabwatch.py"}}
        self.assertIn("Deploys 发布", hooks.handle("a", tool, self.cfg))          # tool input counts too
        self.assertEqual(hooks.handle("b", dict(p, session_id="s4"), self.cfg), "")   # target b is not layered

    def test_rule_list_topic(self):
        self._setup()
        code, out = self.run_cli("rule", "list", "--topic", "Deploys")
        self.assertEqual(code, 0)
        self.assertIn("R-333", out)
        self.assertNotIn("R-005", out)
        code, _ = self.run_cli("rule", "list", "--topic", "nothing")
        self.assertEqual(code, 1)

    def run_cli(self, *argv):
        import contextlib
        import io
        from ai_sync import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()
