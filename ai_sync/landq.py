"""Per-repo landing queue for main (merge queue, 2026-11-02).

Why: with a fast suite of 1-3 minutes, pushers to a busy main (background jobs) lost the race again and again: each one
rebased, checked, and found main had moved during its own check (nonvacuous lost 5 times in a row).
What: one lock per repo, ~/.ai-sync/state/land/<repo-key>.lock, an flock on that file plus a JSON line naming the
holder (pid, session, since, token). `aisync wt land` holds it across fetch -> rebase -> check -> push; the pre-push
gate holds it for a plain `git push` to main from the check until that `git push` process exits. Waiters print
"排队中，前面是 <holder>" and wait up to WAIT_MIN. The kernel drops an flock when its holder dies; a holder still
alive after STALE_MIN is taken over (its lock file is unlinked, the next one starts on a fresh file).
A push made by the holder itself (wt land's own `git push`) carries GATE_LAND_TOKEN, so its hook does not queue
behind its own lock.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

WAIT_MIN = 20
STALE_MIN = 15
TOKEN_ENV = "GATE_LAND_TOKEN"


def lock_dir() -> Path:
    from . import gate
    return gate.state_dir() / "land"


def repo_key(repo, remote: str = "origin") -> str:
    """One key per place main lives: the remote's URL, so every clone and worktree pushing to the same GitHub repo
    queues together (git@example.com:owner/repo.git and https://example.com/owner/repo share one key). Without that remote: the
    repository itself, shared by its main checkout and all its worktrees."""
    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", remote], capture_output=True,
                         text=True).stdout.strip()
    if not url and ("/" in remote or ":" in remote):
        url = remote                            # git push <url> passes the URL itself
    if url:
        m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
        local = url.startswith(("/", ".", "file://")) or os.path.exists(os.path.join(str(repo), url))
        name = os.path.realpath(os.path.join(str(repo), url.removeprefix("file://"))) if local or not m \
            else f"{m.group(1)}/{m.group(2)}"
    else:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True).stdout.strip() or str(repo)
        name = os.path.realpath(common[:-5] if common.endswith("/.git") else common)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _holder(path: Path) -> dict:
    try:
        return json.loads(path.read_text() or "{}")
    except (OSError, ValueError):
        return {}


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def describe(h: dict) -> str:
    return (f"{h.get('who') or '?'}（pid {h.get('pid')}，{str(h.get('since', '?'))[11:19]} 起）" if h else "未知持有者")


class Lock:
    """Context manager. `fd` stays open while held; release() closes it (which drops the flock)."""

    def __init__(self, repo, who: str, wait_min: float = WAIT_MIN, stale_min: float = STALE_MIN, say=None,
                 poll: float = 1.0, remote: str = "origin"):
        self.path = lock_dir() / f"{repo_key(repo, remote)}.lock"
        self.who, self.wait_min, self.stale_min, self.poll = who, wait_min, stale_min, poll
        self.say = say or (lambda m: print(m, file=sys.stderr))
        self.fd: int | None = None
        self.token = uuid.uuid4().hex

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline, told = time.monotonic() + self.wait_min * 60, ""
        while True:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                h = _holder(self.path)
                if self._stale(h):
                    self.say(f"【落地队列】{describe(h)} 持锁超过 {self.stale_min:g} 分钟，接管")
                    try:
                        self.path.unlink()      # the old holder keeps a lock on a file nobody opens any more
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    self.say(f"【落地队列】排队超过 {self.wait_min:g} 分钟，放弃；前面是 {describe(h)}")
                    return False
                if describe(h) != told:
                    told = describe(h)
                    self.say(f"【落地队列】排队中，前面是 {told}")
                time.sleep(self.poll)
                continue
            if not os.path.exists(self.path) or os.fstat(fd).st_ino != os.stat(self.path).st_ino:
                os.close(fd)                    # taken over and replaced between open and flock: try the new file
                continue
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"who": self.who, "pid": os.getpid(), "since": _now().isoformat(timespec="seconds"),
                                     "token": self.token}).encode())
            self.fd = fd
            return True

    def _stale(self, h: dict) -> bool:
        try:
            since = dt.datetime.fromisoformat(h["since"])
        except (KeyError, ValueError, TypeError):
            return False                        # unknown age: keep waiting, the deadline still applies
        return (_now() - since).total_seconds() > self.stale_min * 60

    def set_pid(self, pid: int) -> None:
        """Name a different process as the holder (the `git push` the gate keeps the lock for)."""
        if self.fd is not None:
            h = _holder(self.path)
            h["pid"] = pid
            os.ftruncate(self.fd, 0)
            os.lseek(self.fd, 0, 0)
            os.write(self.fd, json.dumps(h).encode())

    def release(self) -> None:
        if self.fd is not None:
            try:
                if _holder(self.path).get("token") == self.token:
                    os.ftruncate(self.fd, 0)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"landing queue for {self.path.name}: waited {self.wait_min:g} min")
        return self

    def __exit__(self, *exc):
        self.release()


def held_by_caller(repo, remote: str = "origin") -> bool:
    """True when this process runs on behalf of the current holder (its token is in our environment)."""
    tok = os.environ.get(TOKEN_ENV)
    return bool(tok) and _holder(lock_dir() / f"{repo_key(repo, remote)}.lock").get("token") == tok


def hand_to_process(lock: Lock, pid: int, max_min: float = 10) -> None:
    """Keep the flock until `pid` (the `git push` that runs our hook) exits, then let it go: a detached child
    inherits the lock's file descriptor and polls the pid; the caller's own copy is closed."""
    if lock.fd is None:
        return
    lock.set_pid(pid)
    code = ("import os,sys,time\n"
            "fd,pid,end=int(sys.argv[1]),int(sys.argv[2]),time.monotonic()+float(sys.argv[3])*60\n"
            "while time.monotonic()<end:\n"
            "    try: os.kill(pid,0)\n"
            "    except OSError: break\n"
            "    time.sleep(0.5)\n"
            "os.ftruncate(fd,0); os.close(fd)\n")
    subprocess.Popen([sys.executable, "-c", code, str(lock.fd), str(pid), str(max_min)], pass_fds=(lock.fd,),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    os.close(lock.fd)
    lock.fd = None
