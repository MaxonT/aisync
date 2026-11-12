"""Push-time test gate (presubmit / required check), approved by the owner, 2026-11-02:
"tests passing is the one hard standard; every change updates its tests".

Contract: a repo that ships an executable `check.sh` at its root opts in. `./check.sh --fast` is the quick suite
(aim under ~90 s), `./check.sh` the full one, exit 0 = pass.

What the git `pre-push` hook (`aisync gate pre-push`) does for every ref pushed to refs/heads/main of such a repo:
  1. "fix needs a test": a pushed commit whose subject starts with `fix`, in a range that changes code files but
     no test files, is refused.
  2. The pushed commit is checked out into a temporary worktree (so uncommitted local files cannot make it pass)
     and `./check.sh --fast` runs there with GATE_DIFF_BASE=<remote sha>, so check.sh can run diff-cover on the
     changed lines. A failure refuses the push and prints which tests failed.
Pushes to any other branch, and repos without check.sh, are never blocked. GATE_BYPASS=<reason> lets one push
through; every bypass is appended to state/gate_bypass.jsonl and logged as a `blocked` event.
A sha that passed once is remembered in state/gate_pass/ (so `aisync wt land`, which checks before pushing, does
not pay twice). Nested runs (tests inside check.sh that push to throwaway repos) see GATE_ACTIVE and skip.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import core, landq

ZERO = "0" * 40
MAIN_REF = "refs/heads/main"
CHECK_TIMEOUT = 900
REFUSED = 10            # exit code of `aisync gate pre-push` when it refuses; other failures mean the gate broke
CODE_EXT = {".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".swift",
            ".gd", ".cs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}
_TEST_DIRS = {"tests", "test", "__tests__", "spec"}


# git exports these to hooks; inherited by check.sh they would point its git calls at the pushing repo's
# index or folder instead of the clean checkout under test
_HOOK_GIT_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH"}


def _git(repo, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _HOOK_GIT_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                          env=env)


def is_test_file(path: str) -> bool:
    p = Path(path)
    name = p.name
    return (bool(_TEST_DIRS & set(p.parts[:-1])) or name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or ".spec." in name or name == "conftest.py")


def is_code_file(path: str) -> bool:
    return Path(path).suffix in CODE_EXT and not is_test_file(path)


def fix_without_test(subjects: list[str], files: list[str]) -> list[str]:
    """The `fix` subjects that break the rule; empty when the range is fine."""
    fixes = [s for s in subjects if re.match(r"fix\b", s.strip(), re.I)]
    if not fixes:
        return []
    if any(is_test_file(f) for f in files) or not any(is_code_file(f) for f in files):
        return []
    return fixes


def state_dir() -> Path:
    """Where the gate keeps its pass cache, bypass log, gate logs and landing queue. AISYNC_STATE_DIR overrides it:
    every test suite and demo sets it to a throwaway folder so they never write the live ~/.ai-sync/state
    (GATE_STATE_DIR is the older name, still read)."""
    override = os.environ.get("AISYNC_STATE_DIR") or os.environ.get("GATE_STATE_DIR")
    if override:
        return Path(override)
    try:
        root = core.load_config().root
    except Exception:  # noqa: BLE001 - the gate must still work without an ai-sync config
        root = Path.home() / ".ai-sync"
    return root / "state"


def _passed(sha: str) -> Path:
    return state_dir() / "gate_pass" / sha


def failed_tests(output: str) -> list[str]:
    lines = [ln.strip() for ln in output.splitlines()]
    hits = [ln for ln in lines if re.match(r"(FAILED|ERROR) ", ln) or "Coverage is below" in ln
            or ln.startswith("FAIL:") or ln.startswith("ERROR:") or ln.startswith("GATE-FAIL")]
    return hits[:15] or [ln for ln in lines if ln][-12:]


def check_commit(repo, sha: str, base: str | None = None, timeout: int = CHECK_TIMEOUT) -> tuple[bool, str, float]:
    """Run `./check.sh --fast` on a clean checkout of `sha`. Returns (ok, output, seconds)."""
    tree = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}").stdout.strip()
    if _passed(sha).exists() or (tree and _passed(f"tree-{tree}").exists()):
        return True, "（同一个提交或同一棵文件树之前已通过，跳过重跑）", 0.0
    tmp = Path(tempfile.mkdtemp(prefix="gate-"))
    wt = tmp / "src"
    t0 = time.monotonic()
    try:
        add = _git(repo, "worktree", "add", "--detach", "--quiet", str(wt), sha)
        if add.returncode != 0:
            return False, f"GATE-FAIL 无法检出 {sha[:8]}：{(add.stderr or add.stdout).strip()[-300:]}", 0.0
        if not os.access(wt / "check.sh", os.X_OK):
            return False, "GATE-FAIL check.sh 不可执行（chmod +x check.sh 后提交）", 0.0
        env = {k: v for k, v in os.environ.items() if k not in _HOOK_GIT_ENV and k != "GATE_BYPASS"}
        env["GATE_ACTIVE"] = "1"
        if base:
            env["GATE_DIFF_BASE"] = base
        try:
            r = subprocess.run(["./check.sh", "--fast"], cwd=wt, env=env, capture_output=True, text=True,
                               timeout=timeout)
            ok, out = r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            ok, out = False, f"GATE-FAIL check.sh --fast 超过 {timeout} 秒没跑完"
        secs = time.monotonic() - t0
        if not ok:                                  # the whole output, traceback included, for whoever fixes it
            log = state_dir() / "gate_logs" / f"{sha}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(out)
        if ok:
            rec = json.dumps({"repo": str(repo), "sha": sha, "secs": round(secs, 1),
                              "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
            for key in [sha] + ([f"tree-{tree}"] if tree else []):     # a rebase that changes nothing hits the tree
                p = _passed(key)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(rec)
        return ok, out, secs
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))
        _git(repo, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def _has_check(repo, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}:check.sh").returncode == 0


def _range(repo, remote: str, lsha: str, rsha: str) -> tuple[list[str], str | None]:
    """Commits being pushed (newest first) and the base to diff against."""
    if rsha != ZERO and _git(repo, "cat-file", "-e", f"{rsha}^{{commit}}").returncode == 0:
        revs = _git(repo, "rev-list", f"{rsha}..{lsha}").stdout.split()
        return revs, rsha
    revs = _git(repo, "rev-list", lsha, "--not", f"--remotes={remote}").stdout.split()
    if not revs:
        return [], None
    parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{revs[-1]}^").stdout.strip()
    return revs, parent or None


def _agent() -> str:
    try:
        from . import hooks
        return hooks.detect_agent() or "unknown"
    except Exception:  # noqa: BLE001
        return os.environ.get("AISYNC_AGENT") or "unknown"


def _is_temp_repo(repo: Path) -> bool:
    """A repo in a temp dir is a demo or a test fixture (two 'queue demo seed' lines reached the live log on 11/02)."""
    roots = {tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"}
    real = os.path.realpath(repo)
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in map(os.path.realpath, roots))


def record_bypass(repo: Path, sha: str, reason: str) -> None:
    if _is_temp_repo(repo) and not (os.environ.get("AISYNC_STATE_DIR") or os.environ.get("GATE_STATE_DIR")):
        return      # no state dir of its own: it would write the live log and journal for a repo nobody keeps
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "repo": str(repo),
           "sha": sha, "reason": reason, "agent": _agent()}
    p = state_dir() / "gate_bypass.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        core.append_event(core.load_config(), agent=rec["agent"], kind="blocked", project=repo.name,
                          summary=f"push gate bypassed for {repo.name} {sha[:8]} -> main: {reason}")
    except Exception as ex:  # noqa: BLE001 - the jsonl line is the record; the journal is best effort
        print(f"【推送闸门】跳过已记入 {p}，但 ai-sync 日志没写上：{ex}")


def pre_push(repo, remote: str, lines: list[str], say=print) -> int:
    """The pre-push hook body. Returns the exit code (0 = let the push through)."""
    if os.environ.get("GATE_ACTIVE"):
        return 0
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo)
    rc = 0
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _lref, lsha, rref, rsha = parts
        if rref != MAIN_REF or lsha == ZERO or not _has_check(repo, lsha):
            continue
        reason = os.environ.get("GATE_BYPASS", "").strip()
        if reason:
            record_bypass(repo, lsha, reason)
            say(f"【推送闸门】GATE_BYPASS 生效（{reason}），{repo.name} {lsha[:8]} 未经测试放行，已记录")
            continue
        revs, base = _range(repo, remote, lsha, rsha)
        if not revs:
            continue
        lock = None
        if not landq.held_by_caller(repo, remote):  # wt land already holds the queue for its own push
            lock = landq.Lock(repo, f"git push {repo.name}（{_agent()}）", say=say, remote=remote)
            if not lock.acquire():
                say(f"【推送闸门】{repo.name} 的落地队列等了 {landq.WAIT_MIN} 分钟还没轮到，这次不推")
                rc = 1
                continue
        try:
            moved = _remote_moved(repo, remote, rsha)
            if moved:
                say(f"【推送闸门】拒绝推送 {repo.name} 到 main：远端 main 已经是 {moved[:8]}，不是这次推送基于的版本，"
                    "先 fetch 并 rebase 再推（省掉一次注定被拒的检查）")
                rc = 1
                continue
            if not _check_range(repo, lsha, revs, base, say):
                rc = 1
                continue
            push_pid = os.environ.get("GATE_PUSH_PID", "")
            if lock and push_pid.isdigit():
                landq.hand_to_process(lock, int(push_pid))   # keep the queue until this git push has finished
        finally:
            if lock:
                lock.release()
    return rc


def _remote_moved(repo, remote: str, rsha: str) -> str:
    """The remote main's sha when it is no longer the one this push expects, else ""."""
    r = _git(repo, "ls-remote", remote, MAIN_REF, timeout=60)
    now = (r.stdout.split() or [""])[0] if r.returncode == 0 else ""
    expected = "" if rsha == ZERO else rsha
    return now if r.returncode == 0 and now != expected else ""


def _check_range(repo: Path, lsha: str, revs: list[str], base: str | None, say) -> bool:
    """Fix-needs-a-test, then ./check.sh --fast on a clean checkout. True = this range may land."""
    subjects = [_git(repo, "log", "-1", "--format=%s", r).stdout.strip() for r in revs]
    files = _git(repo, "diff", "--name-only", base, lsha).stdout.split() if base else \
        _git(repo, "show", "--name-only", "--format=", *revs).stdout.split()
    bad = fix_without_test(subjects, files)
    if bad:
        say(f"【推送闸门】拒绝推送 {repo.name} 到 main：修复提交改了代码却没改任何测试（修复必须带测试）："
            + "；".join(bad[:3]))
        return False
    say(f"【推送闸门】{repo.name}：在干净检出的 {lsha[:8]} 上跑 ./check.sh --fast …")
    ok, out, secs = check_commit(repo, lsha, base)
    if ok:
        say(f"【推送闸门】通过，用时 {secs:.0f} 秒")
        return True
    log = state_dir() / "gate_logs" / f"{lsha}.log"
    say(f"【推送闸门】拒绝推送 {repo.name} 到 main：./check.sh --fast 没过（{secs:.0f} 秒）。没过的项：")
    for ln in failed_tests(out):
        say("  " + ln)
    say(f"  完整输出（含 traceback）：{log}")
    say("  修好并带上测试后再推；紧急情况才用 GATE_BYPASS=<原因>（会记录）")
    return False
