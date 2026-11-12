"""tidy: proposals only; finds duplicates, uncited rules, core budget overruns, shape and enforcer gaps."""
from __future__ import annotations

import json
import os
from unittest import mock

from ai_sync import core, tidy

from test_ai_sync import Base

HEAD = "<!-- owner-preferences:start (test) -->"


def block(lines):
    return "\n".join([HEAD, "## Owner preferences", "### 最高优先级"] + lines + [core.BLOCK_END])


class TidyTests(Base):
    def _rules(self, lines, extra_cfg=""):
        (self.home / "preferences.md").write_text("# truth\n\n" + block(lines) + "\n")
        if extra_cfg:
            self.write_config(extra_cfg)
        self.cfg = core.load_config(self.home)

    def test_duplicates_unused_shape_and_enforcer(self):
        self._rules([
            "- [R-101] When: sending a first email. Do: no link and no URL. (2026-11-01 · a)",
            "- [R-102] When: sending a first email. Do: no link or URL. (2026-11-02 · a)",
            "- [R-103] When: opening a tab. Do: at most 2 tabs. (2026-11-02 · a)",
            "- [R-104] free text rule (2026-11-02 · a)",
        ])
        core.append_event(self.cfg, agent="codex", kind="note", summary="followed R-103 today")
        (self.home / "rules-enforcement.toml").write_text('[R-103]\njudgment_only = "x"\n')
        rep = tidy.report(self.cfg, globs=[])
        self.assertEqual([(d["a"], d["b"]) for d in rep["duplicates"]], [("R-101", "R-102")])
        self.assertEqual(rep["unused"], ["R-101", "R-102", "R-104"])
        self.assertEqual(rep["out_of_shape"], ["R-104"])
        self.assertEqual(rep["no_enforcer"], ["R-101", "R-102", "R-104"])
        (self.home / "rules-enforcement.toml").write_text(
            '[R-101]\nplanned = "a lint on first emails"\nsize = "S"\nsince = "2026-01-01"\n'
            '[R-102]\nnone = "imported without an enforcer"\n'
            '[R-103]\nenforced_by = ["tests/gone.py"]\n')
        rep = tidy.report(self.cfg, globs=[])
        self.assertEqual(rep["no_enforcer"], ["R-101", "R-102", "R-104"])   # a plan or a legacy none key hides nothing
        text = tidy.render(rep, "2026-11-08")
        self.assertIn("- [ ] R-101 planned S since 2026-01-01 (OVERDUE): a lint on first emails", text)
        self.assertIn("- [ ] R-102 no plan", text)
        self.assertIn("## Enforcer files gone (1)\n", text)
        self.assertIn("- [ ] R-103: tests/gone.py", text)
        self.assertIn("- [ ] merge R-101 and R-102", text)
        self.assertIn("Checked 4 rules", text)

    def test_citations_read_usage_files_of_recent_days(self):
        self._rules(["- [R-101] When: a. Do: b. (2026-11-01 · a)"])
        f = self.t / "u" / "s.jsonl"
        f.parent.mkdir()
        f.write_text('{"text": "per R-101 and R-101"}\n')
        self.assertEqual(tidy.citations(self.cfg, 30, [str(self.t / "u" / "*.jsonl")])["R-101"], 2)
        bulk = self.t / "u" / "slim.jsonl"
        bulk.write_text(" ".join(f"R-{100 + i}" for i in range(tidy.BULK + 1)))
        self.assertEqual(tidy.citations(self.cfg, 30, [str(self.t / "u" / "*.jsonl")])["R-101"], 2)  # bulk file skipped
        bulk.unlink()
        old = os.path.getmtime(f) - 40 * 86400
        os.utime(f, (old, old))
        self.assertNotIn("R-101", tidy.citations(self.cfg, 30, [str(self.t / "u" / "*.jsonl")]))

    def test_core_budget(self):
        self._rules(["- [R-101] When: a. Do: " + "x" * 300 + ". (2026-11-01 · a)"],
                    'core_topics = ["最高优先级"]\ncore_budget_chars = 100\n')
        rep = tidy.report(self.cfg, globs=[])
        self.assertTrue(rep["over_budget"])
        self.assertIn("OVER", tidy.render(rep, "d"))

    def test_daily_writes_once_and_cli(self):
        self._rules(["- [R-101] When: a. Do: b. (2026-11-01 · a)"], 'tidy_usage_globs = []\n')
        p = tidy.daily(self.cfg)
        self.assertTrue(p.exists())
        self.assertIsNone(tidy.daily(self.cfg))
        code, out = self.run_cli("tidy", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["rules"], 1)

    def test_cycle_kick_runs_detached_once_a_day(self):
        self._rules(["- [R-101] When: a. Do: b. (2026-11-01 · a)"], 'tidy_daily = true\n')
        os.environ.pop("AISYNC_NO_TIDY", None)
        try:
            with mock.patch("subprocess.Popen") as po, mock.patch.object(core, "under_test", lambda: False):
                po.return_value.pid = 999999
                self.assertTrue(core.tidy_kick(self.cfg)["started"])
                self.assertFalse(core.tidy_kick(self.cfg)["started"])         # once a day
                self.assertEqual(po.call_count, 1)
                self.assertTrue(po.call_args.kwargs["start_new_session"])
                self.assertEqual(po.call_args.args[0][:3], ["nice", "-n", "15"])
        finally:
            os.environ["AISYNC_NO_TIDY"] = "1"

    def test_kick_never_spawns_by_default_under_a_test_or_while_one_runs(self):
        """EB-074: test runs spawned real-transcript scans that piled up and crashed the Mac."""
        self._rules(["- [R-101] When: a. Do: b. (2026-11-01 · a)"])
        os.environ.pop("AISYNC_NO_TIDY", None)
        try:
            with mock.patch("subprocess.Popen") as po:
                self.assertEqual(core.tidy_kick(self.cfg)["reason"], "tidy_daily is off")
                self.write_config('tidy_daily = true\n')
                self.cfg = core.load_config(self.home)
                self.assertIn("a test", core.tidy_kick(self.cfg)["reason"])   # PYTEST_CURRENT_TEST is set here
                with mock.patch.object(core, "under_test", lambda: False):
                    (self.cfg.state).mkdir(parents=True, exist_ok=True)
                    (self.cfg.state / "tidy.pid").write_text(str(os.getpid()))  # a live pid
                    self.assertEqual(core.tidy_kick(self.cfg)["reason"], "a tidy is still running")
                po.assert_not_called()
        finally:
            os.environ["AISYNC_NO_TIDY"] = "1"

    def test_citations_stream_large_files(self):
        self._rules(["- [R-101] When: a. Do: b. (2026-11-01 · a)"])
        f = self.t / "big.jsonl"
        f.write_text(("x" * 1000 + " R-101\n") * 2000)
        self.assertEqual(tidy.citations(self.cfg, 30, [str(f)])["R-101"], 2000)

    def run_cli(self, *argv):
        import contextlib
        import io
        from ai_sync import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, out.getvalue()
