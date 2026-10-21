"""Tests for `aisync board`: the static HTML status board. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_sync import board, collab, core, demo

from test_ai_sync import Base
from test_public import run_cli


class BoardOnRealHubTests(Base):
    def test_board_shows_claims_blockers_rules_and_timeline(self):
        core.append_event(self.cfg, agent="codex", kind="blocked", project="api", summary="needs the staging key")
        core.append_event(self.cfg, agent="cursor", kind="dev_log", project="web", summary="cart <drawer> & focus",
                          files=["src/Cart.tsx"])
        collab.claim(self.cfg, agent="codex", project="web", summary="rename props", files=["src/Cart.tsx"])
        collab.claim(self.cfg, agent="cursor", project="web", summary="cart polish", files=["src/"])
        out = self.t / "site" / "board.html"
        code, text = run_cli("board", "--out", str(out))
        self.assertEqual(code, 0, text)
        page = out.read_text()
        self.assertIn("aisync status board", page)
        self.assertNotIn("Synthetic demo data", page)                   # only a demo hub says that
        self.assertIn("overlaps cursor", page)
        self.assertIn("overlaps codex", page)
        self.assertIn("needs the staging key", page)
        self.assertIn("still open", page)
        self.assertIn("cart &lt;drawer&gt; &amp; focus", page)          # journal text is escaped
        self.assertIn("rule A", page)
        self.assertIn("chain\nintact", page.replace(" intact", "\nintact"))

    def test_board_is_self_contained(self):
        out = self.t / "board.html"
        self.assertEqual(run_cli("board", "-o", str(out))[0], 0)
        page = out.read_text()
        self.assertNotIn("<script", page)
        self.assertEqual(re.findall(r'(?:src|href)="(https?:[^"]+)"', page),
                         ["https://github.com/MaxonT/aisync"])           # nothing fetched, one outbound link
        self.assertIn("No open claims.", page)
        self.assertIn("Nothing is blocked.", page)

    def test_board_needs_a_hub(self):
        os.environ["AISYNC_HOME"] = str(self.t / "nowhere")
        code, out = run_cli("board", "--out", str(self.t / "b.html"))
        self.assertEqual(code, 2)
        self.assertIn("aisync demo", out)
        self.assertFalse((self.t / "b.html").exists())


class BoardOnDemoHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "demo"
        self._env = mock.patch.dict(os.environ, {"AISYNC_HOME": str(self.dir), "AISYNC_NO_NOTIFY": "1",
                                                 "GIT_CONFIG_GLOBAL": str(Path(self.tmp.name) / "gitconfig"),
                                                 "GIT_CONFIG_NOSYSTEM": "1"})
        self._env.start()
        demo.build(self.dir)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def test_demo_board_is_labelled_synthetic_and_tells_the_story(self):
        self.assertTrue((self.dir / board.DEMO_MARKER).exists())
        page = board.render(core.load_config(self.dir))
        self.assertIn("Synthetic demo data.", page)
        self.assertIn("aisync demo board (synthetic data)", page)
        for tool in demo.TOOLS:
            self.assertIn(f">{tool}</span>", page)
        self.assertIn("overlaps claude-code", page)                     # cursor and claude-code on Summary.tsx
        self.assertIn("unblocked</span> by", page)                      # the CI sandbox key pair
        self.assertIn("still open", page)                               # the billing-account blocker
        self.assertIn("aisync resolve codex", page)                     # the rule conflict
        self.assertIn("never log full card numbers", page)
        self.assertNotIn(str(self.dir), page)                           # no local paths on the page


if __name__ == "__main__":
    unittest.main()
