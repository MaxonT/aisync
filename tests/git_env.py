"""Tests never inherit git's hook variables (core.bare incident, 2026-11-02).

git exports GIT_DIR (from a linked worktree: <repo>/.git/worktrees/<name>), GIT_INDEX_FILE and friends to its
hooks. A suite run from a hook (pre-commit, the pre-push gate) inherited them, and a test's `git init <tmp>`
re-initialised the pushing repo instead, writing core.bare=true into its config. scrub() removes them from
os.environ for the whole session; it runs when this module is imported, before any test.
"""
import os

HOOK_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_QUARANTINE_PATH",
                "GIT_NAMESPACE")


def scrub(env=None):
    env = os.environ if env is None else env
    for key in HOOK_GIT_ENV:
        env.pop(key, None)
    return env


scrub()

os.environ["AISYNC_UNDER_TEST"] = "1"   # watch.restart() refuses to execv the test runner (unittest too)
