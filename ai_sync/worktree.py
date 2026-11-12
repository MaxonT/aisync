"""One-command worktree route for an interactive Claude Code session (lock fix, 2026-10-31 step 3).

Why: when a repo's main checkout is leased by another session, the guard tells this session to work in its own
      worktree. That used to be four hand-typed git commands. Now it is one command each way.
What: `aisync wt start --key <repo> --session <id>` creates <repo>-wt/<id8> on branch wt/<id8> and prints its path.
      `aisync wt land  --key <repo> --session <id>` rebases the branch on GitHub's main and pushes it there FIRST,
      then fast-forwards the local main to GitHub's main, then removes the worktree, its branch, and its lease.
      The local main never merges anything by itself (2026-11-02: a local merge plus a different merge on GitHub
      split a shared main in two). Repos without an origin remote keep the old local ff-only route.
      A repo with check.sh must pass `./check.sh --fast` on the rebased commit before the push (gate.py); on a
      failure nothing is pushed and the worktree stays. Fetch -> rebase -> check -> push runs inside the repo's
      landing queue (landq.py), so a lander never loses the race to someone who pushed during its check.
Read-only use of the main checkout is never blocked by this module; it only ever writes to its own worktree
      and to the landing step.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import core, gate, guard, landq


def path_for(key: str, session: str) -> Path:
    return Path(f"{key}-wt") / session[:8]


def branch_for(session: str) -> str:
    return f"wt/{session[:8]}"


# git stash is one list shared by every worktree of a repo: on 2026-11-02 one session popped another's WIP
NO_STASH = "do not use git stash; commit WIP on your wt branch instead（stash 是所有 worktree 共用的，会被别的会话弹走）"

SHARED_IGNORED = ("state", ".venv")   # git-ignored runtime folders symlinked from the main checkout (same as jobs/bg_worktree.py)
_LEFTOVER_OK = ("state", ".venv", "state/", ".venv/")


def _git(repo, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def start(key: str, session: str) -> tuple[bool, str]:
    """Create (or reuse) the session's worktree. Returns (ok, path or error)."""
    wt, br = path_for(key, session), branch_for(session)
    if wt.exists():
        return True, str(wt)
    if not (Path(key) / ".git").exists():
        return False, f"{key} 不是 git 仓库根目录"
    wt.parent.mkdir(parents=True, exist_ok=True)
    has_branch = _git(key, "show-ref", "--verify", "--quiet", f"refs/heads/{br}").returncode == 0
    base = "HEAD"
    if _has_origin(key) and _git(key, "fetch", "--quiet", "origin").returncode == 0 \
            and _git(key, "rev-parse", "--verify", "--quiet", "origin/main").returncode == 0:
        base = "origin/main"   # start from GitHub's truth, not from a local main that may lag or have split
    args = ["worktree", "add", str(wt), br] if has_branch else ["worktree", "add", "-b", br, str(wt), base]
    r = _git(key, *args)
    if r.returncode == 0:
        for item in SHARED_IGNORED:
            src, dst = Path(key) / item, wt / item
            if src.exists() and not dst.exists() and not dst.is_symlink():
                dst.symlink_to(src)
    return (r.returncode == 0, str(wt) if r.returncode == 0 else (r.stderr or r.stdout).strip()[-300:])


def _has_origin(repo) -> bool:
    return _git(repo, "remote", "get-url", "origin").returncode == 0


def _cleanup(key: str, wt: Path, br: str, cfg: core.Config | None) -> None:
    _git(key, "worktree", "remove", "--force", str(wt))
    _git(key, "branch", "-D", br)
    guard.release(cfg or core.load_config(), key=str(wt))   # its own lease, if any


def _follow_origin(key: str) -> str:
    """Fast-forward the local main to origin/main; never merge, never reset. Returns a note for the message."""
    if _git(key, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
        return "；本机不在 main 上，未跟进"
    _git(key, "fetch", "--quiet", "origin")
    if _git(key, "merge", "--ff-only", "origin/main").returncode == 0:
        return "，本机 main 已跟上 GitHub"
    return "；本机 main 无法快进到 GitHub（分叉或有冲突的未提交改动），GitHub 已是最新，本机需要单独修"


def land(key: str, session: str, cfg: core.Config | None = None, attempts: int = 3) -> tuple[bool, str]:
    """Land the session's branch on GitHub's main, then let the local main follow. Returns (ok, message).
    ok is True only when the commits are on origin/main (or there was nothing to land)."""
    wt, br = path_for(key, session), branch_for(session)
    if not wt.exists():
        return False, f"没有 worktree：{wt}（先运行 aisync wt start）"
    dirty = [ln for ln in _git(wt, "status", "--porcelain").stdout.splitlines() if ln[3:].strip() not in _LEFTOVER_OK]
    if dirty:
        return False, f"{wt} 还有未提交的改动：先在里面 commit，再落地"
    if not _has_origin(key):
        return _land_local(key, wt, br, cfg)
    lock = landq.Lock(key, f"wt land {Path(key).name} {session[:8]}")
    if not lock.acquire():                      # merge queue: fetch -> rebase -> check -> push, one lander at a time
        return False, f"落地队列排了 {landq.WAIT_MIN} 分钟还没轮到，未落地，worktree 保留在 {wt}"
    try:
        return _land_queued(key, wt, br, cfg, attempts, lock)
    finally:
        lock.release()


def _land_queued(key: str, wt: Path, br: str, cfg: core.Config | None, attempts: int,
                 lock: "landq.Lock") -> tuple[bool, str]:
    for _ in range(attempts):
        if _git(wt, "fetch", "--quiet", "origin").returncode != 0:
            return False, f"连不上 GitHub，未落地，worktree 保留在 {wt}"
        ahead = _git(wt, "rev-list", "--count", f"origin/main..{br}").stdout.strip()
        if ahead in ("", "0"):
            note = _follow_origin(key)
            _cleanup(key, wt, br, cfg)
            return True, "没有新提交，只清理" + note
        rb = _git(wt, "rebase", "origin/main")
        if rb.returncode != 0:
            _git(wt, "rebase", "--abort")
            return False, f"与 GitHub 上的 main 冲突，未落地，worktree 保留在 {wt}：{(rb.stderr or rb.stdout).strip()[-200:]}"
        refused = _gate(wt)
        if refused:
            return False, refused
        push = subprocess.run(["git", "-C", str(wt), "push", "origin", "HEAD:main"], capture_output=True, text=True,
                              timeout=600, env={**os.environ, landq.TOKEN_ENV: lock.token})   # its hook skips the queue
        if push.returncode == 0:
            note = _follow_origin(key)
            _cleanup(key, wt, br, cfg)
            return True, f"已落地 {ahead} 个提交，已推送到 GitHub" + note
    return False, f"推送 {attempts} 次都被拒（GitHub 上的 main 一直在变或拒收），未落地，worktree 保留在 {wt}"


def _gate(wt: Path) -> str:
    """Run the repo's `check.sh --fast` on the rebased HEAD before it may reach main (push gate, 2026-11-02).
    Returns "" when it passed or the repo has no check.sh, else the refusal message (the worktree is kept)."""
    sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    if _git(wt, "cat-file", "-e", f"{sha}:check.sh").returncode != 0:
        return ""
    base = _git(wt, "rev-parse", "--verify", "--quiet", "origin/main").stdout.strip() or None
    ok, out, secs = gate.check_commit(wt, sha, base)
    if ok:
        return ""
    lines = "；".join(gate.failed_tests(out)[:5])
    log = gate.state_dir() / "gate_logs" / f"{sha}.log"
    return f"check.sh --fast 没过（{secs:.0f} 秒），未落地，worktree 保留在 {wt}：{lines}；完整输出：{log}"


def _land_local(key: str, wt: Path, br: str, cfg: core.Config | None) -> tuple[bool, str]:
    """Old route for a repo with no origin remote: ff-only merge into the local main (rebasing once)."""
    base = _git(key, "rev-parse", "HEAD").stdout.strip()
    ahead = _git(key, "rev-list", "--count", f"{base}..{br}").stdout.strip()
    if ahead in ("", "0"):
        _cleanup(key, wt, br, cfg)
        return True, "没有新提交，只清理"
    refused = _gate(wt)
    if refused:
        return False, refused
    ok = _git(key, "merge", "--ff-only", br).returncode == 0
    if not ok:
        rb = _git(wt, "rebase", "main")
        if rb.returncode != 0:
            _git(wt, "rebase", "--abort")
            return False, f"与 main 冲突，未落地，worktree 保留在 {wt}：{(rb.stderr or rb.stdout).strip()[-200:]}"
        ok = _git(key, "merge", "--ff-only", br).returncode == 0
    if not ok:
        return False, f"无法快进 main，未落地，worktree 保留在 {wt}"
    _cleanup(key, wt, br, cfg)
    return True, f"已落地 {ahead} 个提交"
