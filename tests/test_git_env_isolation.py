"""The suite never lets git's hook variables reach a test (core.bare incident, 2026-11-02): a test run with
GIT_DIR pointing at another repo's linked-worktree gitdir does `git init <tmp>`, and that repo's config stays
byte-identical."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import git_env

HERE = Path(__file__).resolve()


def _git(*args, **kw):
    env = git_env.scrub(dict(os.environ))
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args], check=True,
                          capture_output=True, text=True, env=env, **kw)


class GitEnvIsolationTest(unittest.TestCase):
    def test_git_init_makes_its_own_repo(self):
        """Run on its own by the test below, inside a pytest started with GIT_DIR set."""
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", str(Path(d) / "fixture")], check=True)
            self.assertTrue((Path(d) / "fixture" / ".git").is_dir())

    def test_hook_git_dir_never_reaches_a_test(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake"
            _git("init", "-q", "-b", "main", str(fake))
            _git("-C", str(fake), "commit", "-q", "--allow-empty", "-m", "x")
            _git("-C", str(fake), "worktree", "add", "-q", "--detach", str(Path(d) / "linked"), "HEAD")
            gitdir = fake / ".git" / "worktrees" / "linked"
            before = (fake / ".git" / "config").read_bytes()
            env = {**os.environ, "GIT_DIR": str(gitdir), "GIT_INDEX_FILE": str(gitdir / "index"),
                   "PYTEST_ADDOPTS": ""}
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                                f"{HERE}::GitEnvIsolationTest::test_git_init_makes_its_own_repo"],
                               cwd=HERE.parent.parent, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual((fake / ".git" / "config").read_bytes(), before)   # the damage, checked first
            self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
            self.assertFalse((gitdir / "config").exists())

    def test_scrub_removes_every_hook_variable(self):
        env = {k: "x" for k in git_env.HOOK_GIT_ENV}
        env["GIT_AUTHOR_NAME"] = "kept"
        self.assertEqual(git_env.scrub(env), {"GIT_AUTHOR_NAME": "kept"})


if __name__ == "__main__":
    unittest.main()
