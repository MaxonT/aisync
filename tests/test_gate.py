"""Tests for ai_sync/gate.py, the push-time test gate (2026-11-02), and its use in `aisync wt land`.
Run: python3 -m unittest discover -s tests -p 'test_gate.py'"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from ai_sync import gate, hooks

from test_ai_sync import Base, _git

SID = "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb"
PASS = "#!/bin/sh\necho 'all good'\nexit 0\n"
FAIL = "#!/bin/sh\necho 'FAILED tests/test_x.py::test_red - assert 1 == 2'\nexit 1\n"


def out(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class RuleTest(Base):
    def test_test_and_code_files(self):
        self.assertTrue(gate.is_test_file("tests/test_a.py"))
        self.assertTrue(gate.is_test_file("graph/sysgraph.test.ts"))
        self.assertTrue(gate.is_test_file("pkg/a_test.py"))
        self.assertFalse(gate.is_test_file("jobs/a.py"))
        self.assertTrue(gate.is_code_file("jobs/a.py"))
        self.assertFalse(gate.is_code_file("README.md"))
        self.assertFalse(gate.is_code_file("tests/test_a.py"))

    def test_fix_without_test(self):
        self.assertEqual(gate.fix_without_test(["fix(x): y"], ["jobs/a.py"]), ["fix(x): y"])
        self.assertEqual(gate.fix_without_test(["Fix: y"], ["jobs/a.py"]), ["Fix: y"])
        self.assertEqual(gate.fix_without_test(["fix(x): y"], ["jobs/a.py", "tests/test_a.py"]), [])
        self.assertEqual(gate.fix_without_test(["fix(docs): typo"], ["README.md"]), [])   # no code changed
        self.assertEqual(gate.fix_without_test(["feat: y"], ["jobs/a.py"]), [])
        self.assertEqual(gate.fix_without_test(["fixture data"], ["jobs/a.py"]), [])        # word, not prefix

    def test_failed_tests_picks_failure_lines(self):
        txt = "....\nFAILED tests/t.py::a - boom\nERROR tests/t.py::b\nFailure. Coverage is below 60%.\n"
        got = gate.failed_tests(txt)
        self.assertEqual(len(got), 3)
        self.assertIn("FAILED tests/t.py::a - boom", got)


class GateRepoTest(Base):
    """A repo with an origin bare remote, like GitHub; the gate is called the way the hook calls it."""

    def setUp(self):
        super().setUp()
        os.environ.pop("GATE_ACTIVE", None)
        os.environ.pop("GATE_BYPASS", None)
        self.remote = self.t / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        _git(self.proj, "remote", "add", "origin", str(self.remote))
        self._commit("check.sh", PASS, "add check.sh")
        _git(self.proj, "push", "-q", "-u", "origin", "main")
        self.said: list[str] = []

    def tearDown(self):
        os.environ.pop("GATE_BYPASS", None)
        super().tearDown()

    def _commit(self, name, text, msg, repo=None):
        repo = repo or self.proj
        p = Path(repo) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        p.chmod(0o755)
        _git(repo, "add", name)
        _git(repo, "commit", "-q", "-m", msg)
        return out(repo, "rev-parse", "HEAD")

    def _push_lines(self, ref="refs/heads/main"):
        sha, rsha = out(self.proj, "rev-parse", "HEAD"), out(self.remote, "rev-parse", "main")
        return [f"refs/heads/main {sha} {ref} {rsha}"]

    def _gate(self, ref="refs/heads/main"):
        return gate.pre_push(self.proj, "origin", self._push_lines(ref), say=self.said.append)

    def test_red_check_refuses_push_to_main_and_names_the_test(self):
        self._commit("check.sh", FAIL, "feat: break it")
        self.assertEqual(self._gate(), 1)
        joined = "\n".join(self.said)
        self.assertIn("拒绝推送", joined)
        self.assertIn("tests/test_x.py::test_red", joined)
        log = self.home / "state" / "gate_logs" / f"{out(self.proj, 'rev-parse', 'HEAD')}.log"
        self.assertIn("assert 1 == 2", log.read_text())                       # full output kept for a traceback
        self.assertEqual(out(self.proj, "worktree", "list").count("\n"), 0)    # temp worktree removed

    def test_green_check_lets_push_through_and_remembers_the_sha(self):
        sha = self._commit("a.py", "x = 1\n", "feat: a")
        self.assertEqual(self._gate(), 0, self.said)
        self.assertTrue((self.home / "state" / "gate_pass" / sha).exists())
        self.assertIn("通过", "\n".join(self.said))

    def test_other_branches_are_never_blocked(self):
        self._commit("check.sh", FAIL, "feat: break it")
        self.assertEqual(self._gate("refs/heads/wip"), 0)
        self.assertEqual(self.said, [])

    def test_repo_without_check_sh_is_not_blocked(self):
        _git(self.proj, "rm", "-q", "check.sh")
        _git(self.proj, "commit", "-q", "-m", "chore: drop check")
        self.assertEqual(self._gate(), 0)

    def test_uncommitted_local_files_cannot_make_it_pass(self):
        self._commit("check.sh", FAIL, "feat: break it")
        (self.proj / "check.sh").write_text(PASS)                # local edit, not in the pushed commit
        self.assertEqual(self._gate(), 1)

    def test_fix_without_a_test_is_refused(self):
        self._commit("jobs/a.py", "x = 2\n", "fix(a): off by one")
        self.assertEqual(self._gate(), 1)
        self.assertIn("没改任何测试", "\n".join(self.said))

    def test_fix_with_a_test_passes(self):
        self._commit("jobs/a.py", "x = 2\n", "fix(a): off by one")
        self._commit("tests/test_a.py", "def test_a():\n    pass\n", "test(a): cover it")
        self.assertEqual(self._gate(), 0, self.said)

    def test_bypass_is_recorded_and_logged(self):
        self._commit("check.sh", FAIL, "feat: break it")
        os.environ["GATE_BYPASS"] = "prod down, hotfix"
        self.assertEqual(self._gate(), 0)
        rec = json.loads((self.home / "state" / "gate_bypass.jsonl").read_text().splitlines()[-1])
        self.assertEqual(rec["reason"], "prod down, hotfix")
        ev = [json.loads(ln) for ln in (self.home / "journal.jsonl").read_text().splitlines() if ln.strip()]
        self.assertTrue(any(e.get("kind") == "blocked" and "bypass" in e.get("summary", "") for e in ev))

    def test_state_dir_override_takes_the_bypass_log_and_pass_cache(self):
        other = self.t / "elsewhere"
        os.environ["AISYNC_STATE_DIR"] = str(other)
        try:
            sha = self._commit("a.py", "x = 1\n", "feat: a")
            self.assertEqual(self._gate(), 0, self.said)
            self.assertTrue((other / "gate_pass" / sha).exists())
            gate.record_bypass(self.proj, sha, "demo")
            self.assertTrue((other / "gate_bypass.jsonl").exists())
        finally:
            os.environ["AISYNC_STATE_DIR"] = str(self.home / "state")
        self.assertFalse((self.home / "state" / "gate_pass" / sha).exists())

    def test_temp_repo_bypass_writes_no_log_or_journal_without_its_own_state_dir(self):
        fake_home = self.t / "fake-home"
        saved = {k: os.environ.pop(k, None) for k in ("AISYNC_STATE_DIR", "GATE_STATE_DIR")}
        calls = []
        with mock.patch.object(gate, "state_dir", return_value=fake_home), \
                mock.patch.object(gate.core, "append_event", side_effect=lambda *a, **k: calls.append(k)):
            try:
                gate.record_bypass(Path(tempfile.gettempdir()) / "demo" / "a", "abc123", "queue demo seed")
                self.assertFalse((fake_home / "gate_bypass.jsonl").exists())
                self.assertEqual(calls, [])
                gate.record_bypass(Path.home() / "projects" / "real", "abc123", "prod down")   # a real repo always logs
                self.assertIn("prod down", (fake_home / "gate_bypass.jsonl").read_text())
                self.assertEqual(len(calls), 1)
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_temp_repo_paths_are_recognised(self):
        self.assertTrue(gate._is_temp_repo(Path(tempfile.gettempdir()) / "x"))
        self.assertTrue(gate._is_temp_repo(Path("/private/var/folders/9n/T/tmp.a/a")))
        self.assertTrue(gate._is_temp_repo(Path("/tmp/x")))
        self.assertFalse(gate._is_temp_repo(Path.home() / "projects" / "some-repo"))

    def test_nested_run_inside_check_sh_skips(self):
        self._commit("check.sh", FAIL, "feat: break it")
        os.environ["GATE_ACTIVE"] = "1"
        try:
            self.assertEqual(self._gate(), 0)
        finally:
            os.environ.pop("GATE_ACTIVE", None)

    def test_git_env_from_the_hook_does_not_leak_into_check_sh(self):
        self._commit("check.sh", '#!/bin/sh\ngit status --porcelain >/dev/null || { echo GATE-FAIL git; exit 1; }\n'
                     '[ -z "$GIT_INDEX_FILE" ] || { echo GATE-FAIL leaked; exit 1; }\n', "feat: git in check")
        os.environ["GIT_INDEX_FILE"] = str(self.t / "nope" / "index")
        try:
            self.assertEqual(self._gate(), 0, self.said)
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)

    def test_push_from_a_linked_worktree_never_makes_the_repo_bare(self):
        """2026-11-02 04:21: two repos got core.bare=true. A push from a linked worktree hands
        the hook GIT_DIR=<repo>/.git/worktrees/<name>; check.sh inherited it, a test ran `git init <tmp>`, and
        git re-initialised that worktree's gitdir instead, writing core.bare=true into the repo's config."""
        hookdir = self.t / "githooks"
        shim = self.t / "aisync-shim"
        src = Path(__file__).resolve().parents[1]
        shim.write_text(f'#!/bin/sh\nPYTHONPATH="{src}" exec "{sys.executable}" -m ai_sync "$@"\n')
        shim.chmod(0o755)
        hooks.write_git_hooks(hookdir, gate=True, bin_=str(shim))
        _git(self.proj, "config", "core.hooksPath", str(hookdir))
        self._commit("check.sh", '#!/bin/sh\nd="$(mktemp -d)"; git init -q "$d/fixture" || exit 1\n'
                     '[ -z "$GIT_DIR" ] || { echo GATE-FAIL GIT_DIR leaked; exit 1; }\n', "feat: tests make repos")
        linked = self.t / "linked"
        _git(self.proj, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
        r = subprocess.run(["git", "-C", str(linked), "push", "-q", "origin", "HEAD:main"],
                           capture_output=True, text=True)
        bare = subprocess.run(["git", "-C", str(self.proj), "config", "--get", "core.bare"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(bare, "false")                                           # the damage, checked first
        self.assertEqual(r.returncode, 0, r.stderr)
        self._assert_not_bare(linked)

    def _assert_not_bare(self, *trees):
        for tree in (self.proj, *trees):
            r = subprocess.run(["git", "-C", str(tree), "rev-parse", "--is-bare-repository"],
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "false"), f"{tree}: {r.stderr}")

    def test_hook_run_with_git_dir_in_env_never_writes_the_repo_config(self):
        """The hook body called directly with GIT_DIR (and GIT_INDEX_FILE) in the environment, the way git
        calls it from a linked worktree: the repo's config is byte-for-byte unchanged afterwards."""
        self._commit("check.sh", '#!/bin/sh\nd="$(mktemp -d)"; git init -q "$d/f" && git -C "$d/f" config x.y 1\n',
                     "feat: tests make repos")
        linked = self.t / "linked2"
        _git(self.proj, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
        gitdir = out(linked, "rev-parse", "--absolute-git-dir")
        config = self.proj / ".git" / "config"
        before = config.read_text()
        lines = self._push_lines()                                                # read before GIT_DIR is set
        os.environ["GIT_DIR"], os.environ["GIT_INDEX_FILE"] = gitdir, str(Path(gitdir) / "index")
        try:
            rc = gate.pre_push(linked, "origin", lines, say=self.said.append)
        finally:
            os.environ.pop("GIT_DIR", None)
            os.environ.pop("GIT_INDEX_FILE", None)
        self.assertEqual(config.read_text(), before)
        self.assertEqual(rc, 0, self.said)
        self.assertIn("通过", "\n".join(self.said))                              # the check really ran
        self.assertFalse((Path(gitdir) / "config").exists())                    # nothing written per worktree
        self._assert_not_bare(linked)
        self.assertEqual(subprocess.run(["git", "-C", str(self.proj), "status", "--porcelain"]).returncode, 0)

    def test_check_sh_gets_the_diff_base(self):
        base = out(self.remote, "rev-parse", "main")
        self._commit("check.sh", '#!/bin/sh\n[ "$GATE_DIFF_BASE" = "' + base + '" ] || { echo GATE-FAIL base; exit 1; }\n',
                     "feat: check base")
        self.assertEqual(self._gate(), 0, self.said)

    def test_real_git_push_runs_the_generated_hook(self):
        """End to end: the generated pre-push hook refuses a red push and lets a green one through."""
        hookdir = self.t / "githooks"
        shim = self.t / "aisync-shim"
        src = Path(__file__).resolve().parents[1]
        shim.write_text(f'#!/bin/sh\nPYTHONPATH="{src}" exec "{sys.executable}" -m ai_sync "$@"\n')
        shim.chmod(0o755)
        hooks.write_git_hooks(hookdir, gate=True, bin_=str(shim))
        _git(self.proj, "config", "core.hooksPath", str(hookdir))
        before = out(self.remote, "rev-parse", "main")
        self._commit("check.sh", FAIL, "feat: break it")
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:main"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("拒绝推送", r.stderr)
        self.assertEqual(out(self.remote, "rev-parse", "main"), before)          # main untouched
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:wip"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)                               # other branch goes through
        self._commit("check.sh", PASS, "feat: fix it properly")
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:main"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out(self.remote, "rev-parse", "main"), out(self.proj, "rev-parse", "HEAD"))

    def test_generated_hook_chains_to_the_repo_hook_with_stdin(self):
        hookdir = self.t / "githooks"
        hooks.write_git_hooks(hookdir, gate=True, bin_=str(self.t / "missing-aisync"))
        _git(self.proj, "config", "core.hooksPath", str(hookdir))
        local = self.proj / ".git" / "hooks" / "pre-push"
        seen = self.t / "seen.txt"
        local.write_text(f'#!/bin/sh\ncat > "{seen}"\nexit 0\n')
        local.chmod(0o755)
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:wip"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs/heads/wip", seen.read_text())


    def test_a_broken_gate_holds_only_main_pushes_of_check_repos(self):
        hookdir = self.t / "githooks"
        broken = self.t / "aisync-broken"
        broken.write_text("#!/bin/sh\necho 'Traceback: ImportError' >&2\nexit 1\n")
        broken.chmod(0o755)
        hooks.write_git_hooks(hookdir, gate=True, bin_=str(broken))
        _git(self.proj, "config", "core.hooksPath", str(hookdir))
        self._commit("a.py", "x = 1\n", "feat: a")
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:wip"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)                               # other branches still move
        r = subprocess.run(["git", "-C", str(self.proj), "push", "-q", "origin", "HEAD:main"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the gate itself failed", r.stderr)

    def test_cli_exit_code_is_refused_on_red(self):
        self._commit("check.sh", FAIL, "feat: break it")
        src = Path(__file__).resolve().parents[1]
        r = subprocess.run([sys.executable, "-m", "ai_sync", "gate", "pre-push", "origin", "x"],
                           input="\n".join(self._push_lines()) + "\n", capture_output=True, text=True,
                           cwd=self.proj, env={**os.environ, "PYTHONPATH": str(src)})
        self.assertEqual(r.returncode, gate.REFUSED, r.stderr)

