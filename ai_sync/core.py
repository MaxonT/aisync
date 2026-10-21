"""aisync core.

What: the hub every AI coding tool (Claude Code, Codex, Cursor, ...) reads and writes.
Why:  one source of truth for shared rules + one append-only, hash-chained journal, so every
      agent can see what is going on, who did it, when, and which files changed.
How:  a sync *cycle* = harvest (git commits -> journal) -> distribute (shared rule block ->
      each tool's file, 3-way) -> render (current.json + agents.md progress) -> flush (git
      commit, optional push). The watcher (watch.py) runs a cycle on file events, debounced.

Every step is isolated (one failure never blocks the others), every cycle is logged to
state/runs.jsonl, and repeated failures pause pushing / cycles and raise an alert.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BLOCK_START = "<!-- owner-preferences:start"
BLOCK_END = "<!-- owner-preferences:end -->"
PROG_START = "<!-- aisync:progress:start -->"
PROG_END = "<!-- aisync:progress:end -->"
KINDS = ("dev_log", "decision", "blocked", "unblocked", "rule_change", "commit", "note", "claim", "release")
# Only these files go into git; logs/backups/locks stay local (see .gitignore).
TRACKED = ["journal.jsonl", "preferences.md", "agents.md", "config.toml", "state/current.json", ".gitignore"]
RUNS_MAX_BYTES = 5 * 1024 * 1024
BACKUPS_PER_TARGET = 20
SEEN_COMMITS_MAX = 5000
DEFAULT_ROSTER = ("claude-code", "codex", "cursor")


class GitError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def home() -> Path:
    return Path(os.environ.get("AISYNC_HOME") or "~/.ai-sync").expanduser()


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ───────────────────────────── config ─────────────────────────────

@dataclass
class Target:
    label: str
    path: Path


@dataclass
class ProgressTarget:
    label: str
    path: Path
    mode: str = "block"      # "block" = <!-- aisync:progress --> section inside the file; "file" = whole file


@dataclass
class Config:
    root: Path
    debounce_secs: float = 30.0          # quiet time after the last event before a cycle runs
    max_wait_secs: float = 120.0         # a busy stream still syncs at least this often
    min_flush_interval_secs: float = 30.0
    reconcile_secs: float = 600.0        # safety net in case a file event was missed
    git_timeout_secs: float = 20.0
    git_retries: int = 3
    backoff_base_secs: float = 1.0
    fail_threshold: int = 5
    remote: str = ""                     # empty = local-only git
    remote_branch: str = "main"
    targets: list[Target] = field(default_factory=list)
    projects: list[Path] = field(default_factory=list)
    scan_dirs: list[Path] = field(default_factory=list)   # every git repo/worktree directly inside
    rule_sources: list[str] = field(default_factory=list)  # repos whose AGENTS.md "Meta rule" feeds the shared rules
    progress_targets: list[ProgressTarget] = field(default_factory=list)  # files tools auto-load
    roster: list[str] = field(default_factory=lambda: list(DEFAULT_ROSTER))  # tools `report` expects to hear from

    @property
    def state(self) -> Path:
        return self.root / "state"


def project_list(cfg: "Config") -> list[Path]:
    """Explicit projects + repos found in scan_dirs, re-read every cycle (worktrees come and go)."""
    found = list(cfg.projects)
    for d in cfg.scan_dirs:
        if d.is_dir():
            found += sorted(p for p in d.iterdir() if p.is_dir() and (p / ".git").exists())
    seen, out = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


_NUM_KEYS = ("debounce_secs", "max_wait_secs", "min_flush_interval_secs", "reconcile_secs",
             "git_timeout_secs", "git_retries", "backoff_base_secs", "fail_threshold")


def load_config(root: Path | None = None) -> Config:
    root = Path(root) if root else home()
    cfg = Config(root=root)
    path = root / "config.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    for key in _NUM_KEYS + ("remote", "remote_branch"):
        if key in data:
            setattr(cfg, key, data[key])
    cfg.targets = [Target(t["label"], Path(t["path"]).expanduser()) for t in data.get("target", [])]
    cfg.projects = [Path(p["path"]).expanduser() for p in data.get("project", [])]
    cfg.scan_dirs = [Path(p).expanduser() for p in data.get("scan_dirs", [])]
    cfg.rule_sources = list(data.get("rule_sources", []))
    if "roster" in data:
        cfg.roster = [str(r) for r in data["roster"]]
    cfg.progress_targets = [ProgressTarget(t["label"], Path(t["path"]).expanduser(), t.get("mode", "block"))
                            for t in data.get("progress_target", [])]
    return cfg


# ───────────────────────────── small io helpers ─────────────────────────────

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename on the *real* path, so a symlinked target stays a symlink."""
    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    mode = real.stat().st_mode & 0o777 if real.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(cfg: Config):
    """Cross-process lock: every tool may call `aisync log` at the same moment."""
    cfg.state.mkdir(parents=True, exist_ok=True)
    with open(cfg.state / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ───────────────────────────── journal (hash chain) ─────────────────────────────

def _event_id(prev: str, body: dict) -> str:
    body = {k: v for k, v in body.items() if k != "id"}
    canon = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + "\n" + canon).encode()).hexdigest()[:16]


def read_events(cfg: Config) -> list[dict]:
    path = cfg.root / "journal.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            with contextlib.suppress(json.JSONDecodeError):
                out.append(json.loads(line))
    return out


def append_event(cfg: Config, *, agent: str, kind: str, summary: str, project: str = "",
                 files=(), refs: dict | None = None, ts: str | None = None) -> dict:
    agent, summary = (agent or "").strip(), (summary or "").strip()
    if not agent:
        raise ValueError("agent is required (pass --agent or set AISYNC_AGENT)")
    if not summary:
        raise ValueError("summary is required")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    with locked(cfg):
        events = read_events(cfg)
        prev = events[-1].get("id", "genesis") if events else "genesis"
        body = {"ts": ts or now_iso(), "agent": agent, "project": project, "kind": kind,
                "summary": summary[:2000], "files": [str(f) for f in files][:200],
                "refs": refs or {}, "prev": prev}
        body["id"] = _event_id(prev, body)
        with open(cfg.root / "journal.jsonl", "a") as fh:
            fh.write(json.dumps(body, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return body


def verify_chain(cfg: Config) -> list[str]:
    """Empty list = intact. Reports hand edits, lost events and forks."""
    path = cfg.root / "journal.jsonl"
    if not path.exists():
        return []
    problems, prev = [], "genesis"
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {i}: not valid JSON")
            continue
        if ev.get("prev") != prev:
            problems.append(f"line {i}: prev link broken (event lost, reordered or forked)")
        if _event_id(ev.get("prev", ""), ev) != ev.get("id"):
            problems.append(f"line {i}: id mismatch (event edited by hand)")
        prev = ev.get("id", "")
    return problems


# ───────────────────────────── shared-rule block distribution ─────────────────────────────

def extract_block(text: str) -> str | None:
    s = text.find(BLOCK_START)
    if s < 0:
        return None
    e = text.find(BLOCK_END, s)
    return None if e < 0 else text[s:e + len(BLOCK_END)]


def replace_block(text: str, new_block: str) -> str:
    s = text.find(BLOCK_START)
    e = text.find(BLOCK_END, s)
    return text[:s] + new_block + text[e + len(BLOCK_END):]


def _backup(cfg: Config, label: str, text: str) -> None:
    bdir = cfg.root / "backups"
    bdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    (bdir / f"{label}-{stamp}.bak").write_text(text)
    old = sorted(bdir.glob(f"{label}-*.bak"))
    for p in old[:-BACKUPS_PER_TARGET]:
        p.unlink()


def distribute(cfg: Config) -> dict:
    """3-way sync of the owner-preferences block between preferences.md (truth) and targets.

    For each target we remember the hash we last wrote. Then:
      target == truth                     -> in sync
      target == last written              -> target untouched, truth moved: overwrite target
      target edited, truth unchanged, and
        every edited copy agrees          -> fast-forward: adopt the edit into truth
      anything else                       -> conflict: leave files alone, alert the owner
    """
    rep = {"in_sync": [], "updated": [], "adopted": [], "conflicts": [], "missing": [], "errors": []}
    truth_path = cfg.root / "preferences.md"
    st = load_json(cfg.state / "distribute.json", {})
    conflicts = load_json(cfg.state / "conflicts.json", {})
    truth_text = truth_path.read_text()
    truth = extract_block(truth_text)
    if truth is None:
        rep["errors"].append("preferences.md has no owner-preferences block")
        return rep

    blocks: dict[str, tuple[Target, str, str]] = {}
    for t in cfg.targets:
        try:
            text = t.path.read_text()
        except FileNotFoundError:
            rep["missing"].append(t.label)
            continue
        except OSError as ex:
            rep["errors"].append(f"{t.label}: {ex}")
            continue
        b = extract_block(text)
        if b is None:
            rep["missing"].append(t.label)
            continue
        blocks[t.label] = (t, text, b)

    last_truth = st.get("_truth")
    truth_changed = last_truth is not None and _h(truth) != last_truth
    drifted = {lbl for lbl, (_, _, b) in blocks.items()
               if lbl not in conflicts and _h(b) != _h(truth) and _h(b) != st.get(lbl)}

    if drifted:
        variants = {_h(blocks[l][2]) for l in drifted}
        if last_truth is not None and not truth_changed and len(variants) == 1:
            new_block = blocks[sorted(drifted)[0]][2]
            atomic_write(truth_path, replace_block(truth_text, new_block))
            truth = new_block
            rep["adopted"] = sorted(drifted)
            append_event(cfg, agent=",".join(sorted(drifted)), kind="rule_change",
                         summary=f"shared rules edited in the {', '.join(sorted(drifted))} copy; adopted into preferences.md",
                         files=["preferences.md"] + [str(blocks[l][0].path) for l in sorted(drifted)])
        else:
            reason = ("rules changed in preferences.md and in this copy" if truth_changed
                      else "copies were edited differently" if len(variants) > 1
                      else "no sync history for this copy yet")
            for lbl in sorted(drifted):
                conflicts[lbl] = {"path": str(blocks[lbl][0].path), "target_hash": _h(blocks[lbl][2]),
                                  "truth_hash": _h(truth), "ts": now_iso(), "reason": reason}
                rep["conflicts"].append(lbl)

    for lbl, (t, text, b) in blocks.items():
        if lbl in conflicts:
            continue
        if _h(b) == _h(truth):
            rep["in_sync"].append(lbl)
        else:
            _backup(cfg, lbl, text)
            atomic_write(t.path, replace_block(text, truth))
            rep["updated"].append(lbl)
        st[lbl] = _h(truth)
    st["_truth"] = _h(truth)
    save_json(cfg.state / "distribute.json", st)
    save_json(cfg.state / "conflicts.json", conflicts)
    rep["open_conflicts"] = sorted(conflicts)
    return rep


def resolve(cfg: Config, label: str, take: str) -> dict:
    """Owner's ruling on a conflict: keep the truth (overwrite the copy) or take the copy."""
    conflicts = load_json(cfg.state / "conflicts.json", {})
    if label not in conflicts:
        raise ValueError(f"no open conflict for {label!r}")
    target = next((t for t in cfg.targets if t.label == label), None)
    if target is None:
        raise ValueError(f"unknown target {label!r}")
    st = load_json(cfg.state / "distribute.json", {})
    block = extract_block(target.path.read_text())
    if block is None:
        raise ValueError(f"{target.path} lost its owner-preferences block")
    if take == "truth":
        st[label] = _h(block)          # looks "untouched" -> next distribute overwrites it
    elif take == "target":
        truth_path = cfg.root / "preferences.md"
        atomic_write(truth_path, replace_block(truth_path.read_text(), block))
        st[label] = st["_truth"] = _h(block)
        append_event(cfg, agent="owner", kind="rule_change",
                     summary=f"conflict resolved: took the {label} copy as the shared rules",
                     files=["preferences.md", str(target.path)])
    else:
        raise ValueError("take must be 'truth' or 'target'")
    del conflicts[label]
    save_json(cfg.state / "distribute.json", st)
    save_json(cfg.state / "conflicts.json", conflicts)
    return distribute(cfg)


# ───────────────────────────── render (what is going on) ─────────────────────────────

def build_current(events: list[dict], tail: int = 15) -> dict:
    agents, projects, blocked = {}, {}, {}
    for e in events:
        brief = {k: e.get(k) for k in ("ts", "agent", "project", "kind", "summary")}
        agents[e.get("agent", "?")] = brief
        if e.get("project"):
            projects[e["project"]] = brief
        if e.get("kind") == "blocked":
            blocked[e["id"]] = brief
        elif e.get("kind") == "unblocked":
            target = (e.get("refs") or {}).get("blocked_id")
            for bid in [b for b, v in blocked.items() if b == target or (not target and v["project"] == e.get("project"))]:
                del blocked[bid]
    return {"event_count": len(events), "agents": agents, "projects": projects,
            "open_blocked": list(blocked.values()), "tail": [
                {k: e.get(k) for k in ("ts", "agent", "project", "kind", "summary", "files")} for e in events[-tail:]]}


def _short(s: str, n: int = 110) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def render(cfg: Config) -> dict:
    from . import collab
    cur = build_current(read_events(cfg))
    claims = collab.load_claims(cfg)
    hints = collab.report(cfg)["hints"]
    cur["claims"] = claims
    cur_path = cfg.state / "current.json"
    changed = []
    old_cur = load_json(cur_path, {})
    if any(old_cur.get(k) != cur[k] for k in ("tail", "event_count", "claims")):
        save_json(cur_path, dict(cur, generated_at=now_iso()))
        changed.append("state/current.json")

    lines = [PROG_START, f"_Updated {now_iso()} · {cur['event_count']} events · `aisync tail` for more_", "",
             "| agent | last seen | project | what |", "|---|---|---|---|"]
    for a, e in sorted(cur["agents"].items(), key=lambda kv: kv[1]["ts"] or "", reverse=True):
        lines.append(f"| {a} | {e['ts']} | {e['project'] or '-'} | {_short(e['summary']).replace('|', '/')} |")
    if claims:
        lines += ["", "**Working right now** (claims; coordinate before touching these files)"] + [
            f"- {c['agent']} @ {c['project']}: {_short(c['summary'])}"
            f"{' [' + ', '.join(c['files'][:6]) + ']' if c['files'] else ' [whole repo]'} (until {c['expires'][11:16]})"
            for c in claims]
    if cur["open_blocked"]:
        lines += ["", "**Open blockers**"] + [f"- {b['ts']} {b['agent']} @ {b['project'] or '-'}: {_short(b['summary'])}"
                                              for b in cur["open_blocked"]]
    lines += ["", "**Latest**"] + [f"- {e['ts']} · {e['agent']} · {e['kind']} · {e['project'] or '-'}: {_short(e['summary'])}"
                                   for e in reversed(cur["tail"][-8:])]
    if hints:
        lines += ["", "**Self-check hints** (`aisync report`; reported only, never auto-applied)"] + [f"- {h}" for h in hints[:5]]
    lines.append(PROG_END)
    agents_md = cfg.root / "agents.md"
    text = agents_md.read_text() if agents_md.exists() else f"# AI collaboration hub\n\n{PROG_START}\n{PROG_END}\n"
    s, e = text.find(PROG_START), text.find(PROG_END)
    if s < 0 or e < 0:
        text = text.rstrip() + f"\n\n{PROG_START}\n{PROG_END}\n"
        s, e = text.find(PROG_START), text.find(PROG_END)
    old_section = text[s:e + len(PROG_END)]
    # Ignore the timestamp line when deciding whether anything really changed.
    if old_section.split("\n", 2)[2:] != "\n".join(lines).split("\n", 2)[2:]:
        atomic_write(agents_md, text[:s] + "\n".join(lines) + text[e + len(PROG_END):])
        changed.append("agents.md")
    return {"changed": changed, "agents": len(cur["agents"])}


# ───────────────────────────── harvest (git commits -> journal) ─────────────────────────────

_AGENT_HINTS = (("claude", "claude-code"), ("codex", "codex"), ("cursor", "cursor"),
                ("trae", "trae"), ("kiro", "kiro"), ("windsurf", "windsurf"))


def infer_agent(message: str, author: str) -> str:
    """Attribute a commit by its trailers only (subjects that merely mention a tool don't count)."""
    for line in message.splitlines():
        low = line.strip().lower()
        if low.startswith("agent:"):
            return low.split(":", 1)[1].strip() or f"git:{author}"
        if low.startswith(("co-authored-by:", "generated with", "generated-by:", "🤖")):
            for needle, agent in _AGENT_HINTS:
                if needle in low:
                    return agent
    return f"git:{author}"


def _gitdir(project: Path) -> Path | None:
    dot = project / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        line = dot.read_text().strip()
        if line.startswith("gitdir:"):
            p = Path(line.split(":", 1)[1].strip())
            return p if p.is_absolute() else (project / p).resolve()
    return None


def head_log(project: Path) -> Path | None:
    gd = _gitdir(project)
    return gd / "logs" / "HEAD" if gd else None


def _git(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def harvest(cfg: Config) -> dict:
    st = load_json(cfg.state / "harvest.json", {"cursors": {}, "seen": []})
    seen = set(st["seen"])
    rep = {"events": 0, "projects": 0, "errors": []}
    for proj in project_list(cfg):
        if _gitdir(proj) is None:
            continue
        key = str(proj)
        try:
            r = _git(["rev-parse", "HEAD"], proj, cfg.git_timeout_secs)
            if r.returncode != 0:
                continue                      # empty repo
            head = r.stdout.strip()
            rep["projects"] += 1
            last = st["cursors"].get(key)
            if last is None or last == head:
                st["cursors"][key] = head     # first sight: start from now, don't flood history
                continue
            r = _git(["log", "--no-merges", "--max-count=50", "--name-only",
                      "--format=%x1e%H%x1f%an%x1f%aI%x1f%B%x1f", f"{last}..{head}"], proj, cfg.git_timeout_secs)
            if r.returncode != 0:            # cursor commit gone (rebase/gc): restart from HEAD
                st["cursors"][key] = head
                rep["errors"].append(f"{proj.name}: cursor reset ({r.stderr.strip()[:120]})")
                continue
            for rec in reversed([x for x in r.stdout.split("\x1e") if x.strip()]):
                sha, author, date, body, files = (rec.split("\x1f") + [""] * 5)[:5]
                if sha in seen:
                    continue
                subject = body.strip().splitlines()[0] if body.strip() else "(no message)"
                append_event(cfg, agent=infer_agent(body, author), kind="commit", project=proj.name,
                             summary=subject, files=[f for f in files.split("\n") if f.strip()][:50],
                             refs={"commit": sha[:12]}, ts=date)
                seen.add(sha)
                st["seen"].append(sha)
                rep["events"] += 1
            st["cursors"][key] = head
        except (subprocess.TimeoutExpired, OSError) as ex:
            rep["errors"].append(f"{proj.name}: {type(ex).__name__}")   # isolate: next project still runs
    st["seen"] = st["seen"][-SEEN_COMMITS_MAX:]
    save_json(cfg.state / "harvest.json", st)
    return rep


# ───────────────────────────── health, alerts, run log ─────────────────────────────

def load_health(cfg: Config) -> dict:
    return load_json(cfg.state / "health.json", {"push_failures": 0, "push_paused": False,
                                                 "cycle_failures": 0, "cycles_paused": False})


def save_health(cfg: Config, h: dict) -> None:
    save_json(cfg.state / "health.json", h)


def alert(cfg: Config, title: str, message: str) -> None:
    """Never fail silently: ALERT.md + run log + desktop notification (best effort)."""
    cfg.state.mkdir(parents=True, exist_ok=True)
    with open(cfg.state / "ALERT.md", "a") as fh:
        fh.write(f"- {now_iso()} **{title}**: {message}\n")
    log_run(cfg, {"ts": now_iso(), "level": "alert", "title": title, "message": message})
    if os.environ.get("AISYNC_NO_NOTIFY"):
        return
    if sys.platform == "darwin":
        script = f'display notification {json.dumps(message[:200])} with title {json.dumps("aisync: " + title)}'
        cmd = ["osascript", "-e", script]
    else:
        cmd = ["notify-send", f"aisync: {title}", message[:200]]
    with contextlib.suppress(Exception):
        subprocess.run(cmd, capture_output=True, timeout=5)


def log_run(cfg: Config, rec: dict) -> None:
    path = cfg.state / "runs.jsonl"
    cfg.state.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > RUNS_MAX_BYTES:
        os.replace(path, path.with_suffix(".jsonl.1"))
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_runs(cfg: Config, n: int = 50) -> list[dict]:
    path = cfg.state / "runs.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines()[-n:]:
        with contextlib.suppress(json.JSONDecodeError):
            out.append(json.loads(line))
    return out


# ───────────────────────────── git flush ─────────────────────────────

def git_run(cfg: Config, args: list[str], retries: int = 1) -> subprocess.CompletedProcess:
    """Timeout on every call; exponential backoff between attempts."""
    err = ""
    for attempt in range(max(1, retries)):
        try:
            r = _git(args, cfg.root, cfg.git_timeout_secs)
            if r.returncode == 0:
                return r
            err = (r.stderr or r.stdout).strip()[:300]
        except subprocess.TimeoutExpired:
            err = f"timeout after {cfg.git_timeout_secs}s"
        if attempt < retries - 1:
            time.sleep(min(cfg.backoff_base_secs * 2 ** attempt, 60))
    raise GitError(f"git {args[0]}: {err}")


def ensure_repo(cfg: Config) -> None:
    if not (cfg.root / ".git").exists():
        git_run(cfg, ["init", "-q", "-b", "main"])
    # The hub commits on its own; give it a local identity when the machine has none.
    if _git(["config", "user.email"], cfg.root, cfg.git_timeout_secs).returncode != 0:
        git_run(cfg, ["config", "user.name", "aisync"])
        git_run(cfg, ["config", "user.email", "aisync@localhost"])


def flush(cfg: Config) -> dict:
    ensure_repo(cfg)
    files = [f for f in TRACKED if (cfg.root / f).exists()]
    git_run(cfg, ["add", "--", *files])
    staged = _git(["diff", "--cached", "--name-only"], cfg.root, cfg.git_timeout_secs).stdout.split()
    rep = {"committed": None, "files": staged, "pushed": None}
    health = load_health(cfg)
    if staged:
        events = read_events(cfg)
        last_sha = load_json(cfg.state / "flush.json", {}).get("last_event_id")
        ids = [e.get("id") for e in events]
        new = events[ids.index(last_sha) + 1:] if last_sha in ids else events[-20:]
        who = sorted({e.get("agent", "?") for e in new})
        git_run(cfg, ["commit", "-q", "-m", f"chore(aisync): {len(new)} event(s) from {', '.join(who) or 'n/a'}",
                      "-m", "\n".join(f"- {e.get('agent')} {e.get('kind')}: {_short(e.get('summary', ''), 90)}"
                                      for e in new[-30:]) or "state refresh"])
        rep["committed"] = git_run(cfg, ["rev-parse", "--short", "HEAD"]).stdout.strip()
        save_json(cfg.state / "flush.json", {"last_event_id": ids[-1] if ids else None, "ts": now_iso()})
    if cfg.remote and not health.get("push_paused"):
        ahead = _git(["rev-list", "--count", f"{cfg.remote}/{cfg.remote_branch}..HEAD"], cfg.root, cfg.git_timeout_secs)
        if rep["committed"] or ahead.returncode != 0 or ahead.stdout.strip() not in ("", "0"):
            try:
                git_run(cfg, ["push", "-q", cfg.remote, f"HEAD:{cfg.remote_branch}"], retries=cfg.git_retries)
                rep["pushed"] = True
                health.update(push_failures=0, last_push_ok=now_iso())
            except GitError as ex:
                rep["pushed"] = False
                health["push_failures"] = health.get("push_failures", 0) + 1
                health["last_push_error"] = str(ex)
                if health["push_failures"] >= cfg.fail_threshold:
                    health["push_paused"] = True
                    alert(cfg, "push paused", f"{health['push_failures']} pushes failed in a row; local journaling continues. "
                                              f"Fix the remote, then `aisync resume`. Last error: {ex}")
    elif health.get("push_paused"):
        rep["pushed"] = "paused"
    save_health(cfg, health)
    return rep


# ───────────────────────────── the cycle ─────────────────────────────

def run_cycle(cfg: Config, reason: str = "manual") -> dict:
    t0 = time.monotonic()
    rec = {"ts": now_iso(), "reason": reason, "steps": {}}
    from . import collab
    steps = (("harvest", harvest), ("rules", collab.harvest_rules), ("distribute", distribute),
             ("render", render), ("progress", collab.render_progress), ("flush", flush))
    for name, fn in steps:
        s = time.monotonic()
        try:
            rec["steps"][name] = {"ok": True, "result": fn(cfg)}
        except Exception as ex:                       # failure isolation: keep going
            rec["steps"][name] = {"ok": False, "error": f"{type(ex).__name__}: {ex}"[:400]}
        rec["steps"][name]["ms"] = round((time.monotonic() - s) * 1000)
    rec["ok"] = all(s["ok"] for s in rec["steps"].values())
    rec["ms"] = round((time.monotonic() - t0) * 1000)

    health = load_health(cfg)
    dist = rec["steps"]["distribute"].get("result") or {}
    new_conf = sorted(set(dist.get("conflicts", [])))
    if new_conf:
        alert(cfg, "rule conflict", f"shared rules conflict in {', '.join(new_conf)}; "
                                    f"run `aisync status` then `aisync resolve <label> --take truth|target`")
    if rec["ok"]:
        health["cycle_failures"] = 0
        health["last_cycle_ok"] = rec["ts"]
    else:
        health["cycle_failures"] = health.get("cycle_failures", 0) + 1
        health["last_cycle_error"] = {k: v.get("error") for k, v in rec["steps"].items() if not v["ok"]}
        if health["cycle_failures"] >= cfg.fail_threshold and not health.get("cycles_paused"):
            health["cycles_paused"] = True
            alert(cfg, "sync paused", f"{health['cycle_failures']} cycles failed in a row: "
                                      f"{health['last_cycle_error']}. Fix it, then `aisync resume`.")
    save_health(cfg, health)
    log_run(cfg, rec)
    return rec


# ───────────────────────────── status ─────────────────────────────

def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def status(cfg: Config) -> dict:
    events = read_events(cfg)
    health = load_health(cfg)
    runs = [r for r in read_runs(cfg, 200) if "steps" in r]
    porcelain = _git(["status", "--porcelain", "--", *TRACKED], cfg.root, cfg.git_timeout_secs).stdout \
        if (cfg.root / ".git").exists() else ""
    truth = extract_block((cfg.root / "preferences.md").read_text()) if (cfg.root / "preferences.md").exists() else None
    targets = {}
    for t in cfg.targets:
        try:
            b = extract_block(t.path.read_text())
            targets[t.label] = "in sync" if b and truth and _h(b) == _h(truth) else ("no block" if b is None else "differs")
        except OSError:
            targets[t.label] = "missing"
    hb = health.get("watcher_heartbeat_epoch", 0)
    watcher_alive = _pid_alive(health.get("watcher_pid")) and time.time() - hb < max(180, cfg.reconcile_secs * 2)
    failed = [r for r in runs if not r.get("ok")]
    return {
        "home": str(cfg.root),
        "watcher": {"alive": watcher_alive, "pid": health.get("watcher_pid"),
                    "heartbeat": health.get("watcher_heartbeat")},
        "journal": {"events": len(events), "chain_problems": verify_chain(cfg)},
        "agents": build_current(events)["agents"],
        "unflushed_files": [l[3:] for l in porcelain.splitlines()],
        "targets": targets,
        "conflicts": load_json(cfg.state / "conflicts.json", {}),
        "health": {k: v for k, v in health.items() if not k.startswith("watcher_")},
        "metrics": {"cycles": len(runs), "failed": len(failed),
                    "failure_rate": round(len(failed) / len(runs), 3) if runs else 0.0,
                    "avg_ms": round(sum(r.get("ms", 0) for r in runs) / len(runs)) if runs else 0,
                    "commits_harvested": sum((r["steps"].get("harvest", {}).get("result") or {}).get("events", 0) for r in runs),
                    "targets_updated": sum(len((r["steps"].get("distribute", {}).get("result") or {}).get("updated", []))
                                           for r in runs),
                    "last_cycle": runs[-1] if runs else None},
        "alerts_file": str(cfg.state / "ALERT.md") if (cfg.state / "ALERT.md").exists() else None,
    }


def resume(cfg: Config) -> dict:
    h = load_health(cfg)
    h.update(push_failures=0, push_paused=False, cycle_failures=0, cycles_paused=False)
    save_health(cfg, h)
    return h
