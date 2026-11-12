"""Tests for the web-inbox importer. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import threading
import unittest

from ai_sync import cli, core, watch, webinbox
from test_ai_sync import Base, _git


def _line(wid, kind="decision", project="project-a", summary=None, agent="claude-web", refs=None):
    return json.dumps({"ts": "2026-10-18T04:50:53-05:00", "agent": agent, "project": project,
                       "kind": kind, "summary": summary or f"summary {wid}", "files": [], "refs": refs or {},
                       "web_id": wid}, ensure_ascii=False)


class WebInboxBase(Base):
    def setUp(self):
        super().setUp()
        self.remote = self.t / "remote.git"
        _git(self.t, "init", "-q", "--bare", str(self.remote))
        self.web = self.t / "web"                      # plays the web Claude: writes the branch
        _git(self.t, "clone", "-q", str(self.remote), str(self.web))
        _git(self.web, "checkout", "-q", "-b", "web-inbox")
        self.lines = []
        self.write_config(f'web_inbox_secs = 0.2\nweb_inbox_remote = "{self.remote}"\n')

    def push_lines(self, *lines):
        self.lines += lines
        (self.web / "events.jsonl").write_text("".join(l + "\n" for l in self.lines))
        _git(self.web, "add", "events.jsonl")
        _git(self.web, "commit", "-q", "-m", f"{len(self.lines)} lines")
        _git(self.web, "push", "-q", "origin", "web-inbox")

    def web_events(self):
        return [e for e in core.read_events(self.cfg) if e["agent"] == "claude-web"]


class ImportTests(WebInboxBase):
    def test_imports_each_line_once_with_original_fields(self):
        self.push_lines(_line("a1"), _line("b2", kind="rule_change", summary="【偏好】中文 ok"),
                        _line("c3", project="ai-sync"))
        rec = webinbox.poll(self.cfg)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(len(rec["imported"]), 3)
        ev = self.web_events()
        self.assertEqual([e["refs"]["web_id"] for e in ev], ["a1", "b2", "c3"])
        self.assertEqual(ev[1]["kind"], "rule_change")
        self.assertEqual(ev[1]["summary"], "【偏好】中文 ok")
        self.assertEqual(ev[2]["project"], "ai-sync")
        self.assertEqual(ev[0]["refs"]["web_ts"], "2026-10-18T04:50:53-05:00")
        self.assertEqual(core.verify_chain(self.cfg), [])

        again = webinbox.poll(self.cfg)
        self.assertEqual((len(again["imported"]), again["already"]), (0, 3))
        self.push_lines(_line("d4"))
        self.assertEqual([i["web_id"] for i in webinbox.poll(self.cfg)["imported"]], ["d4"])
        self.assertEqual(len(self.web_events()), 4)

    def test_chatgpt_lines_keep_their_agent_and_list_refs(self):
        self.push_lines(_line("g1", agent="chatgpt", refs=["mailbox:91761a1", "correction:E118"]), _line("w1"))
        rec = webinbox.poll(self.cfg)
        self.assertEqual([(i["web_id"], i["agent"]) for i in rec["imported"]], [("g1", "chatgpt"), ("w1", "claude-web")])
        g = [e for e in core.read_events(self.cfg) if e["agent"] == "chatgpt"]
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["refs"]["web_refs"], "mailbox:91761a1 | correction:E118")
        self.assertEqual(len(self.web_events()), 1)
        self.assertEqual(webinbox.poll(self.cfg)["already"], 2)          # dedup by web_id unchanged

    def test_unknown_or_local_agent_falls_back_to_claude_web(self):
        self.push_lines(_line("x1", agent="claude-code"), _line("x2", agent=""))
        webinbox.poll(self.cfg)
        ev = self.web_events()
        self.assertEqual([e["refs"]["web_id"] for e in ev], ["x1", "x2"])
        self.assertEqual(ev[0]["refs"]["web_agent"], "claude-code")      # a web line cannot pose as a local agent
        self.assertNotIn("web_agent", ev[1]["refs"])

    def test_unknown_kind_is_kept_as_note(self):
        self.push_lines(_line("m1", kind="milestone"))
        rec = webinbox.poll(self.cfg)
        self.assertEqual(rec["kind_mapped"], ["m1: milestone -> note"])
        ev = self.web_events()[0]
        self.assertEqual((ev["kind"], ev["refs"]["web_kind"]), ("note", "milestone"))

    def test_lost_state_file_does_not_duplicate(self):
        self.push_lines(_line("a1"), _line("b2"))
        webinbox.poll(self.cfg)
        (self.cfg.state / "web_inbox.json").unlink()
        rec = webinbox.poll(self.cfg)
        self.assertEqual((len(rec["imported"]), rec["already"]), (0, 2))
        self.assertEqual(len(self.web_events()), 2)

    def test_bad_lines_rejected_once_and_good_ones_still_imported(self):
        self.push_lines("not json", _line("e1", summary=" "), json.dumps({"summary": "no id", "kind": "note"}),
                        _line("ok1"))
        rec = webinbox.poll(self.cfg)
        self.assertEqual([i["web_id"] for i in rec["imported"]], ["ok1"])
        self.assertEqual(len(rec["rejected"]), 3)
        self.assertEqual(webinbox.poll(self.cfg)["rejected"], [])     # not re-reported every poll
        self.assertEqual(core.verify_chain(self.cfg), [])

    def test_branch_is_only_read_and_main_untouched(self):
        self.push_lines(_line("a1"))
        core.ensure_repo(self.cfg)
        before_remote = subprocess.run(["git", "rev-parse", "web-inbox"], cwd=self.remote,
                                       capture_output=True, text=True).stdout
        webinbox.poll(self.cfg)
        after_remote = subprocess.run(["git", "rev-parse", "web-inbox"], cwd=self.remote,
                                      capture_output=True, text=True).stdout
        self.assertEqual(before_remote, after_remote)
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=self.home,
                              capture_output=True, text=True).stdout.split()
        self.assertEqual([r for r in refs if "web-inbox" in r], [webinbox.REF])   # no origin/*, no local branch
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=self.home,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "main")
        self.assertFalse((self.home / "events.jsonl").exists())             # working tree untouched

    def test_concurrent_polls_do_not_duplicate(self):
        self.push_lines(*[_line(f"w{i}") for i in range(10)])
        webinbox.fetch(self.cfg)
        threads = [threading.Thread(target=webinbox.poll, args=(self.cfg, False)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.web_events()), 10)


class FailureTests(WebInboxBase):
    def test_fetch_failure_counts_backs_off_alerts_once_then_recovers(self):
        self.write_config(f'web_inbox_secs = 1\nweb_inbox_remote = "{self.t / "nope.git"}"\n')
        for n in (1, 2, 3):
            rec = webinbox.poll(self.cfg)
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["failures"], n)
        self.assertEqual(webinbox.next_delay(self.cfg), 8)            # 1 s doubled per failure
        alerts = (self.cfg.state / "ALERT.md").read_text()
        self.assertEqual(alerts.count("web-inbox import failing"), 1)  # fail_threshold = 2, alerted once
        self.assertTrue(any(r.get("web_inbox") and not r["ok"] for r in core.read_runs(self.cfg)))

        self.push_lines(_line("a1"))
        self.write_config(f'web_inbox_secs = 1\nweb_inbox_remote = "{self.remote}"\n')
        rec = webinbox.poll(self.cfg)
        self.assertTrue(rec["ok"])
        self.assertEqual(webinbox.load_state(self.cfg)["failures"], 0)
        self.assertEqual(webinbox.next_delay(self.cfg), 1)

    def test_missing_branch_is_an_error_not_a_crash(self):
        rec = webinbox.poll(self.cfg)
        self.assertFalse(rec["ok"])
        self.assertIn("GitError", rec["error"])


class WatchAndCliTests(WebInboxBase):
    def test_watcher_polls_and_flushes_imported_events(self):
        self.push_lines(_line("a1"), _line("b2"))
        watch.run_watch(self.cfg, max_seconds=4)
        self.assertEqual(len(self.web_events()), 2)
        committed = subprocess.run(["git", "show", "HEAD:journal.jsonl"], cwd=self.home,
                                   capture_output=True, text=True).stdout
        self.assertIn('"web_id": "b2"', committed)

    def test_watcher_ignores_inbox_when_off(self):
        self.push_lines(_line("a1"))
        self.write_config()
        watch.run_watch(self.cfg, max_seconds=1)
        self.assertEqual(self.web_events(), [])

    def test_cli_web_inbox_and_status(self):
        self.push_lines(_line("a1"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["web-inbox"]), 0)
        self.assertIn("imported 1", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["status"])
        self.assertIn("web-in   every 0.2s, 1 imported", out.getvalue())

    def test_cli_failure_exit_code(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["web-inbox"]), 1)
        self.assertIn("web-inbox FAILED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
