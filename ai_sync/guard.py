"""ai-sync guard: one Claude Code session per repo at a time, enforced by a PreToolUse hook.

What: the first Claude Code session that edits a repo takes a lease on it; any OTHER session that tries
      to edit or run a write command in that repo is denied, with who holds it and how to hand off.
      The lease is refreshed on every edit, expires LEASE_TTL_MIN after the holder's last edit, and is
      dropped when the holder session ends (SessionEnd hook).
Why:  2026-10-23 two Claude Code sessions worked the same ticket (X autopilot) without seeing each other
      and nearly posted the same reply twice. Claims (`aisync claim`) are voluntary and keyed by agent
      name, and every session is "claude-code", so sessions never saw each other's claims.
How:  `aisync guard` reads the hook JSON on stdin. Key = the git root of the touched path (a worktree is
      its own key, so worktree sessions never block each other); outside git, the file itself.
      Shared append-only places (mailbox, ai-sync hub, scratch, tmp) are exempt. Any internal error
      fails open: a bug in the guard must never stop work.
Headless: a scripted background run (`claude -p`, env AISYNC_HEADLESS set) takes over a lease whose holder has
      not edited for HEADLESS_TAKEOVER_MIN minutes, so an idle interactive window never blocks the hourly
      mail-ticket for the full LEASE_TTL_MIN (T-20261025-ROBUST 5). The takeover is logged in guard.jsonl.
Undo: remove the `aisync guard` entries from ~/.claude/settings.json (PreToolUse, SessionEnd).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import core

LEASE_TTL_MIN = 45
STALE_IDLE_MIN = 1   # decided 2026-11-01: no waiting; a holder with no work and no live process is released at once
HEADLESS_TAKEOVER_MIN = int(os.environ.get("AISYNC_HEADLESS_TAKEOVER_MIN", "15"))
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_DEFAULT_EXEMPT = ("~/.ai-sync", "~/.claude/projects",
                   "~/Library/Application Support/Claude", "~/Library/Logs", "/tmp", "/private/tmp",
                   "/private/var/folders", "/var/folders", "/dev")
# Bash commands that change files or git state. Read-only commands (ls, cat, git status/log/diff) pass.
# git's own options before the subcommand (git -C d, -c k=v, --no-pager, --git-dir d): a write behind any of them is still a write.
_GIT_VALUE = r"(?:\"[^\"]*\"|'[^']*'|\S+)"
_GIT_OPTS = rf"(?:(?:-C|-c|--git-dir|--work-tree|--namespace|--exec-path)\s+{_GIT_VALUE}\s+|--?[\w-]+(?:={_GIT_VALUE})?\s+)*"
_MUTATING = re.compile(
    rf"\bgit\s+{_GIT_OPTS}(?:commit|push|pull|merge|rebase|reset|checkout|switch|restore|add|rm|mv|"
    r"stash|cherry-pick|revert|tag|am|apply|clean)\b"
    r"|\bsed\s+-i|\bperl\s+-p?i|\brm\s|\bmv\s|\bcp\s|\btee\b|\btouch\s|\bmkdir\s|\bpatch\b"
    r"|(?:^|[^<>&0-9])>>?\s*(?!/dev/null)(?!&)\S")


def exempt_roots() -> list[Path]:
    raw = os.environ.get("AISYNC_LEASE_EXEMPT")
    items = raw.split(":") if raw is not None else list(_DEFAULT_EXEMPT)
    return [Path(os.path.expanduser(p)).resolve() for p in items if p]


def _within(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def key_for(path: str | Path, cwd: str = "") -> str | None:
    """The lease key for a path: its git root, else the path itself; None when exempt."""
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    p = p.resolve()
    if any(_within(p, r) for r in exempt_roots()):
        return None
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return str(d)
    return str(p)


# Landing a finished worktree branch into a held repo: git refuses a fast-forward that would overwrite the
# holder's uncommitted files, and a push only sends commits, so these never clobber the holder's work.
_LANDING = re.compile(rf"^\s*git\s+{_GIT_OPTS}(?:merge\s+--ff-only|pull\s+--ff-only|push)\b")
_SEGMENT = re.compile(r"&&|\|\||;|\n")


_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _is_git_c(words: list[str], i: int) -> bool:
    """words[i] is git's own -C: only git's own options sit between it and the word git."""
    j = i - 1
    while j >= 0 and not words[j].endswith("git"):
        if j > 0 and words[j - 1] in _GIT_VALUE_OPTS:
            j -= 2
        elif words[j].startswith("-"):
            j -= 1
        else:
            return False
    return j >= 0


def _seg_dirs(words: list[str]) -> list[str]:
    return [words[i + 1].rstrip(";&|") for i, w in enumerate(words[:-1])
            if w == "cd" or (w == "-C" and _is_git_c(words, i))]


def bash_targets(command: str, cwd: str) -> list[str]:
    """Directories a mutating Bash command works in, per segment: its `cd X` / `git -C X`, else the last
    `cd` before it, else the cwd. Landing segments (git merge --ff-only, pull --ff-only, push) lock nothing."""
    if not _MUTATING.search(command or ""):
        return []
    dirs, here = [], cwd
    for seg in _SEGMENT.split(command):
        try:
            words = shlex.split(seg, posix=True)
        except ValueError:
            words = seg.split()
        own = _seg_dirs(words)
        if _MUTATING.search(seg) and not _LANDING.search(seg):
            gitc = [d for d in own if not (words and words[0] == "cd")]
            dirs += gitc or ([here] if here else [])
        if words and words[0] == "cd" and own:
            d = os.path.expanduser(own[0])
            here = d if os.path.isabs(d) else str(Path(here or ".") / d)
    return dirs


def worktree_path(key: str, session: str) -> str:
    return f"{key}-wt/{session[:8]}"


# ───────────────────────────── lease store ─────────────────────────────

def _path(cfg: core.Config) -> Path:
    return cfg.state / "leases.json"


def _now() -> datetime:
    return datetime.now().astimezone()


def load(cfg: core.Config) -> dict:
    now, out = _now(), {}
    for k, v in core.load_json(_path(cfg), {}).items():
        try:
            if datetime.fromisoformat(v["expires"]) > now:
                out[k] = v
        except (KeyError, TypeError, ValueError):
            continue
    return out


def headless() -> bool:
    """True inside a scripted background run (the parent sets AISYNC_HEADLESS for its child)."""
    return bool(os.environ.get("AISYNC_HEADLESS"))


def _holder_done(cur: dict, key: str, now: datetime) -> bool:
    """True when the holder has no work left: idle for STALE_IDLE_MIN minutes, and either its working folder is gone
    or it has no uncommitted change of its own. Such a lease is released at once, without asking anyone."""
    try:
        idle = now - datetime.fromisoformat(cur["last"])
    except (KeyError, TypeError, ValueError):
        return False
    if idle < timedelta(minutes=STALE_IDLE_MIN):
        return False
    cwd = cur.get("cwd") or ""
    if cwd and not Path(cwd).exists():
        return True
    if _scope_open(key, cur):          # uncommitted work of its own (or state unreadable): keep
        return False
    return _holder_alive(cwd) is False


def _holder_alive(cwd: str, tries: int = 3) -> bool | None:
    """Is a Claude Code process still working in `cwd`? True / False, or None when every read method failed.
    Reads several ways and retries (decided 2026-11-01: a failed read is read again, never taken as a verdict)."""
    if not cwd:
        return False
    import time as _t
    for _ in range(tries):
        for method in (_lsof_cwds, _ps_cwds):
            found = method()
            if found is None:
                continue
            return any(c == cwd or c.startswith(cwd.rstrip("/") + "/") for c in found)
        _t.sleep(0.5)
    return None


def _lsof_cwds() -> list[str] | None:
    import subprocess
    try:
        out = subprocess.run(["lsof", "-a", "-c", "claude", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [ln[1:] for ln in out.splitlines() if ln.startswith("n")]


def _ps_cwds() -> list[str] | None:
    import subprocess
    try:
        pids = subprocess.run(["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=10).stdout.split()
        out: list[str] = []
        for pid in pids:
            r = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=10).stdout
            out += [ln[1:] for ln in r.splitlines() if ln.startswith("n")]
        return out
    except (OSError, subprocess.SubprocessError):
        return None


def acquire(cfg: core.Config, session: str, key: str, cwd: str = "", ttl_min: int = LEASE_TTL_MIN,
            takeover_idle_min: int | None = None, touched: str = ""):
    """Take or refresh the lease. Returns (True, lease) or (False, the other session's lease).
    takeover_idle_min: take another session's lease when its last edit is at least that old."""
    with core.locked(cfg):
        leases = load(cfg)
        cur = leases.get(key)
        now = _now()
        if cur and cur["session"] != session:
            try:
                idle = now - datetime.fromisoformat(cur["last"])
            except (KeyError, TypeError, ValueError):
                idle = timedelta(0)
            stale = _holder_done(cur, key, now)   # decided 2026-11-01: a holder with no work left never keeps the lock
            if not stale and (takeover_idle_min is None or idle < timedelta(minutes=takeover_idle_min)):
                return False, cur
            if stale:
                _log(cfg, session=session[:8], event="stale_takeover", key=key, holder=cur["session"][:8],
                     idle_min=int(idle.total_seconds() // 60), cwd=cur.get("cwd", ""))
            cur = {"took_over_from": cur["session"]}
        same = bool(cur) and cur.get("session") == session
        files = set(cur.get("files", [])) if same else set()
        if touched:
            files.add(touched)
        # Dirty lines that already existed when this lease began: someone else's work, not this session's.
        baseline = cur.get("baseline") if same else (_status_lines(key) if (Path(key) / ".git").exists() else [])
        lease = {"session": session, "since": (cur or {}).get("since") or now.isoformat(timespec="seconds"),
                 **({"took_over_from": cur["took_over_from"]} if cur and "took_over_from" in cur else {}),
                 "last": now.isoformat(timespec="seconds"), "cwd": cwd, "files": sorted(files),
                 "baseline": baseline,
                 "expires": (now + timedelta(minutes=ttl_min)).isoformat(timespec="seconds")}
        leases[key] = lease
        core.save_json(_path(cfg), leases)
        return True, lease


def release(cfg: core.Config, *, session: str = "", key: str = "") -> list[str]:
    key = str(Path(os.path.expanduser(key)).resolve()) if key else ""
    with core.locked(cfg):
        leases = load(cfg)
        gone = [k for k, v in leases.items() if (key and k == key) or (session and v["session"] == session)]
        for k in gone:
            leases.pop(k)
        core.save_json(_path(cfg), leases)
    return gone


# ───────────────────────────── hook entry ─────────────────────────────

def _hm(iso: str) -> str:
    return iso[11:16] if len(iso) >= 16 else iso


def _dirty(key: str, files: list[str]) -> bool:
    """Uncommitted changes in `files` (or anywhere in the repo when none were recorded). Unknown counts as dirty."""
    try:
        r = subprocess.run(["git", "-C", key, "status", "--porcelain", "--"] + (files or ["."]),
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or bool(r.stdout.strip())


def _status_lines(key: str) -> list[str] | None:
    """`git status --porcelain` for the whole repo, or None when it cannot be read."""
    try:
        r = subprocess.run(["git", "-C", key, "status", "--porcelain"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def _scope_open(key: str, v: dict) -> bool:
    """True while the lease still guards uncommitted work of its session.
    With recorded files (Edit/Write): open while any of them is dirty.
    Bash-only lease (no recorded files): open only while the repo has dirty lines that were NOT dirty when the
    lease began (`baseline`), so a colleague's unrelated uncommitted change never pins the lock. Unknown = open."""
    files = v.get("files") or []
    if files:
        return _dirty(key, files)
    now = _status_lines(key)
    if now is None:
        return True
    return bool(set(now) - set(v.get("baseline") or []))


def release_finished(cfg: core.Config, *, session: str) -> list[str]:
    """Scope end (Stop hook): drop this session's leases whose work is all committed.
    A lease with edits still uncommitted stays until its TTL, so an unfinished change is never handed over."""
    with core.locked(cfg):
        leases = load(cfg)
        gone = [k for k, v in leases.items() if v.get("session") == session and not _scope_open(k, v)]
        for k in gone:
            leases.pop(k)
        if gone:
            core.save_json(_path(cfg), leases)
    return gone


def deny_reason(key: str, lease: dict, session: str = "") -> str:
    msg = (f"【会话隔离】{key} 正被另一个 Claude Code 会话 {lease['session'][:8]} 占用"
           f"（{_hm(lease['since'])} 起，最近一次改动 {_hm(lease['last'])}，没人再改的话 {_hm(lease['expires'])} 自动释放）。"
           f"只读不受影响：看文件、grep/rg、git status/log/diff/show 照常可以用，只拦改动。")
    if session and (Path(key) / ".git").exists():
        wt = worktree_path(key, session)
        return msg + (f"不用等：要改，就一条命令开自己的 worktree：`aisync wt start --key '{key}' --session {session}`，"
                      f"然后所有改动、测试、commit 都写在打印出来的路径下（它是单独的锁，{wt}）。"
                      f"没做完的改动在 worktree 的分支上提交，不要用 git stash（所有 worktree 共用一个 stash）。"
                      f"做完一条命令落地并清理：`aisync wt land --key '{key}' --session {session}`。"
                      f"只有确认那个会话已经结束，才可以运行 `aisync lease release --key '{key}'` 并告诉用户。")
    return msg + ("本会话不要改这里：换别的仓库或文件做；要交接就用 ListAgents 找到那个会话，用 SendMessage 商量。"
                  f"只有确认那个会话已经结束，才可以运行 `aisync lease release --key '{key}'` 并告诉用户。")


def _log(cfg: core.Config, **rec) -> None:
    """One line per decision that touched a key, in state/guard.jsonl (`aisync lease status` reads it)."""
    try:
        path = cfg.state / "guard.jsonl"
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(path.with_suffix(".jsonl.1"))
        with open(path, "a") as fh:
            fh.write(json.dumps({"ts": core.now_iso(), **rec}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def handle(payload: dict, cfg: core.Config | None = None) -> str:
    """Return the hook's stdout ('' = allow). Never raises."""
    try:
        event = payload.get("hook_event_name") or ""
        session = str(payload.get("session_id") or "")
        if not session:
            return ""
        cfg = cfg or core.load_config()
        if event == "SessionEnd":
            gone = release(cfg, session=session)
            if gone:
                _log(cfg, session=session[:8], event="SessionEnd", released=gone)
            return ""
        if event == "Stop":
            gone = release_finished(cfg, session=session)
            if gone:
                _log(cfg, session=session[:8], event="Stop", released=gone, reason="scope-end")
            return ""
        if event != "PreToolUse":
            return ""
        tool, inp, cwd = payload.get("tool_name") or "", payload.get("tool_input") or {}, payload.get("cwd") or ""
        if tool in EDIT_TOOLS:
            paths = [inp.get("file_path") or inp.get("notebook_path") or ""]
        elif tool == "Bash":
            paths = bash_targets(str(inp.get("command") or ""), cwd)
        else:
            return ""
        for p in filter(None, paths):
            key = key_for(p, cwd)
            if not key or (tool == "Bash" and not (Path(key) / ".git").exists()):
                continue                         # Bash outside a repo: too coarse to lock a folder
            bg = headless()
            touched = str(Path(p) if Path(p).is_absolute() else Path(cwd or key) / p) if tool in EDIT_TOOLS else ""
            ok, lease = acquire(cfg, session, key, cwd, takeover_idle_min=HEADLESS_TAKEOVER_MIN if bg else None,
                                touched=touched)
            took = lease.get("took_over_from") if ok and lease.get("since") == lease.get("last") else None
            _log(cfg, session=session[:8], tool=tool, key=key,
                 decision=("takeover" if took else "allow") if ok else "deny",
                 holder=(took or lease["session"])[:8], **({"headless": True} if bg else {}))
            if not ok:
                reason = deny_reason(key, lease, session)
                if bg:
                    reason += (f"（后台任务：在信箱 log.md 写一行「<时间> | claude-code | blocked | <单号> | "
                               f"被前台会话 {lease['session'][:8]} 挡住 {key}」，这一项留着不勾，先做别的；"
                               f"它闲置满 {HEADLESS_TAKEOVER_MIN} 分钟后后台会自动接过来）")
                return json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": reason}}, ensure_ascii=False)
        return ""
    except Exception:  # fail open
        return ""


def status_text(cfg: core.Config) -> str:
    leases = load(cfg)
    if not leases:
        return "no active session leases"
    return "\n".join(f"{v['session'][:8]}  {k}  since {_hm(v['since'])}  last {_hm(v['last'])}  until {_hm(v['expires'])}"
                     for k, v in sorted(leases.items(), key=lambda kv: kv[1]["last"]))
