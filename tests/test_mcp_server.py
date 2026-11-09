"""mcp_server: the hub's tools as plain functions, plus the MCP wrapper when the SDK is installed."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest

from ai_sync import core, mcp_server

from test_ai_sync import Base
from test_layers import TRUTH


class ToolTests(Base):
    def _truth(self, cfg_extra=""):
        (self.home / "preferences.md").write_text(f"# truth\n\n{TRUTH}\n")
        if cfg_extra:
            self.write_config(cfg_extra)

    def test_rules_core_view_and_topic(self):
        self._truth('core_topics = ["最高优先级"]\n')
        out = mcp_server.rules()
        self.assertIn("R-005", out)
        self.assertNotIn("R-333", out)
        self.assertIn("_Shelved", out)
        self.assertIn("R-333", mcp_server.rules("Deploys"))
        self.assertIn("no topic", mcp_server.rules("nothing"))

    def test_rules_unlayered_gives_everything(self):
        self._truth()
        self.assertIn("R-333", mcp_server.rules())

    def test_log_claim_overlap_release_news_status(self):
        self._truth()
        self.assertTrue(mcp_server.log("codex", "api", "SDK 5.0, retries kept").startswith("logged "))
        self.assertTrue(mcp_server.log("codex", "api", "x", kind="nonsense").startswith("refused"))
        self.assertIn("claimed api", mcp_server.claim("codex", "api", "SDK upgrade", "app/payments/"))
        out = mcp_server.claim("claude-code", "api", "refunds", "app/payments/refunds.py")
        self.assertIn("OVERLAP with codex", out)
        self.assertEqual(mcp_server.release("codex", "api"), "released 1 claim(s)")
        self.assertIn("cursor set", mcp_server.news("cursor", "m1"))
        core.append_event(self.cfg, agent="codex", kind="note", summary="new thing happened")
        self.assertIn("new thing happened", mcp_server.news("cursor", "m1"))
        self.assertEqual(mcp_server.news("cursor", "m1"), "nothing new")
        self.assertIn("claude-code", mcp_server.status())

    def test_rule_add_via_fields_and_refusal(self):
        self._truth()
        out = mcp_server.rule_add("codex", "a contract changes", "ship a contract test", "最高优先级", "owner said",
                                  judgment_only="review")
        self.assertTrue(out.startswith("added R-"), out)
        self.assertIn("When: a contract changes. Do: ship a contract test.", out)
        self.assertTrue(mcp_server.rule_add("codex", "", "x", "最高优先级", "r", judgment_only="r").startswith("refused"))

    def test_tidy_tool(self):
        self._truth('tidy_usage_globs = []\n')
        self.assertIn("Rules tidy", mcp_server.tidy())


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK not installed")
class ServerTests(Base):
    def test_every_tool_is_registered_and_callable(self):
        (self.home / "preferences.md").write_text(f"# truth\n\n{TRUTH}\n")
        server = mcp_server.build()
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertEqual(names, {"rules", "rule_add", "log", "claim", "release", "news", "status", "tidy"})
        res = asyncio.run(server.call_tool("rules", {"topic": "Deploys"}))
        self.assertIn("R-333", str(res))


class MissingSdkTests(unittest.TestCase):
    def test_build_explains_the_extra(self):
        import builtins
        real = builtins.__import__

        def fake(name, *a, **k):
            if name.startswith("mcp"):
                raise ImportError(name)
            return real(name, *a, **k)
        builtins.__import__ = fake
        try:
            with self.assertRaisesRegex(RuntimeError, r"aisync\[mcp\]"):
                mcp_server.build()
        finally:
            builtins.__import__ = real
