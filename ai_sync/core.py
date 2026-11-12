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
import re
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
TRACKED = ["journal.jsonl", "preferences.md", "rules-archive.md", "rules-enforcement.toml", "agents.md", "config.toml", "state/current.json",
           ".gitignore"]
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
    slim: bool = False   # the copy omits the "### 已取代" history section (every session pays for it)
    layer: str = ""      # "core": only core_topics in full, other topics folded and injected on demand (layers.py)


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
    web_inbox_secs: float = 0.0          # poll the web-inbox branch this often; 0 = off (webinbox.py)
    web_inbox_remote: str = "origin"
    web_inbox_branch: str = "web-inbox"
    gate_hook: bool = False              # install the push-time test gate in the git pre-push hook (gate.py); off by default
    guard_hook: bool = False             # wire the one-session-per-repo guard (guard.py) into Claude Code; off by default
    instincts_path: str = ""             # optional habit list the hooks inject (one line each); empty = off
    errors_path: str = ""                # optional error log the habits cite; empty = off
    rule_spec: bool = False              # rule add refuses a rule not in the When/Do/Not shape (rulespec.py)
    require_enforcer: bool = False       # rule add refuses a rule that names no --enforced-by / --judgment-only
    core_topics: list[str] = field(default_factory=list)       # topic prefixes a layered target carries in full
    shelf: dict = field(default_factory=dict)                   # topic prefix -> keywords that pull it in (layers.py)
    tidy_usage_globs: list[str] = field(default_factory=lambda: list(TIDY_GLOBS))  # files whose R-### mentions count as use
    core_budget_chars: int = 26000       # tidy flags the core layer above this many characters of rule text
    tidy_daily: bool = False             # the watch cycle starts one tidy a day; off unless the hub config says so
    progress_file: str = ""              # write live progress here (hub-relative) instead of into agents.md, which then
                                         # never changes and stays cacheable; hooks inject it at session start

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


TIDY_GLOBS: tuple[str, ...] = ()     # no transcript scan unless config sets tidy_usage_globs (e.g. "~/.claude/projects/*/*.jsonl")

_NUM_KEYS = ("debounce_secs", "max_wait_secs", "min_flush_interval_secs", "reconcile_secs",
             "git_timeout_secs", "git_retries", "backoff_base_secs", "fail_threshold", "web_inbox_secs",
             "core_budget_chars")


def load_config(root: Path | None = None) -> Config:
    root = Path(root) if root else home()
    cfg = Config(root=root)
    path = root / "config.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    for key in _NUM_KEYS + ("remote", "remote_branch", "web_inbox_remote", "web_inbox_branch",
                            "instincts_path", "errors_path", "progress_file"):
        if key in data:
            setattr(cfg, key, data[key])
    cfg.targets = [Target(t["label"], Path(t["path"]).expanduser(), bool(t.get("slim", False)), str(t.get("layer", "")))
                   for t in data.get("target", [])]
    cfg.core_topics = list(data.get("core_topics", []))
    cfg.shelf = {k: list(v) for k, v in data.get("shelf", {}).items()}
    cfg.tidy_daily = bool(data.get("tidy_daily", False))
    cfg.gate_hook = bool(data.get("gate_hook", False))
    cfg.guard_hook = bool(data.get("guard_hook", False))
    if "tidy_usage_globs" in data:
        cfg.tidy_usage_globs = list(data["tidy_usage_globs"])
    cfg.projects = [Path(p["path"]).expanduser() for p in data.get("project", [])]
    cfg.scan_dirs = [Path(p).expanduser() for p in data.get("scan_dirs", [])]
    cfg.rule_sources = list(data.get("rule_sources", []))
    if "roster" in data:
        cfg.roster = [str(r) for r in data["roster"]]
    cfg.rule_spec = bool(data.get("rule_spec", False))
    cfg.require_enforcer = bool(data.get("require_enforcer", False))
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


SUPERSEDED_HEAD = "### 已取代"


def _superseded_span(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) of the 已取代 section inside a block: from its heading to the next heading or the block end."""
    start = next((i for i, l in enumerate(lines) if l.strip() == SUPERSEDED_HEAD), None)
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(("### ", "## ")) or lines[j].startswith(BLOCK_END):
            return start, j
    return start, len(lines)


def strip_superseded(block: str) -> str:
    """The rules block without its 已取代 section. That section is history, not rules; the truth keeps it, the
    copies every session loads do not (it was 30 KB of every session's context)."""
    lines = block.split("\n")
    span = _superseded_span(lines)
    if span is None:
        return block
    s, e = span
    head = lines[:s]
    while head and head[-1].strip() == "":
        head.pop()
    tail = lines[e:]
    if tail and tail[0].startswith(("### ", "## ")):
        head.append("")
    return "\n".join(head + tail)


def merge_back(slim_block: str, truth_block: str) -> str:
    """A slim copy edited by hand, with the truth's 已取代 section put back (so adopting the edit never deletes history)."""
    t_lines = truth_block.split("\n")
    span = _superseded_span(t_lines)
    if span is None:
        return slim_block
    s, e = span
    if _superseded_span(slim_block.split("\n")) is not None:
        return slim_block
    while s > 0 and t_lines[s - 1].strip() == "":
        s -= 1
    section = t_lines[s:e]
    lines = slim_block.split("\n")
    at = next((i for i, l in enumerate(lines) if l.startswith(BLOCK_END)), len(lines))
    return "\n".join(lines[:at] + section + lines[at:])


_RULE_ID = re.compile(r"^- \[R-\d{3,}\] ", re.M)


def _drop_ids(block: str) -> str:
    """The block without rule IDs ([R-001]); a copy that differs from the truth only there was not edited."""
    return _RULE_ID.sub("- ", block)


def _want(t: "Target", truth: str, cfg: "Config | None" = None) -> str:
    want = strip_superseded(truth) if t.slim else truth
    if t.layer == "core" and cfg is not None and cfg.core_topics:
        from . import layers
        want = layers.core_view(want, cfg.core_topics, cfg.shelf)
    return want


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
    drifted = {lbl for lbl, (t, _, b) in blocks.items()
               if lbl not in conflicts and _h(b) != _h(_want(t, truth, cfg)) and _h(b) != st.get(lbl)
               and _drop_ids(b) != _drop_ids(_want(t, truth, cfg))}   # only rule IDs differ: not an edit, overwrite

    def full_view(lbl: str) -> str:   # a slim copy compared as if it still carried the history section
        t, _, b = blocks[lbl]
        if t.layer == "core" and cfg.core_topics:   # a layered copy: put the truth's folded topics back first
            from . import layers
            b = layers.merge_back(b, truth, cfg.core_topics)
        return merge_back(b, truth) if t.slim else b

    if drifted:
        variants = {_h(full_view(l)) for l in drifted}
        if last_truth is not None and not truth_changed and len(variants) == 1:
            new_block = full_view(sorted(drifted)[0])
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
        want = _want(t, truth, cfg)
        if _h(b) == _h(want):
            rep["in_sync"].append(lbl)
        else:
            _backup(cfg, lbl, text)
            atomic_write(t.path, replace_block(text, want))
            rep["updated"].append(lbl)
        st[lbl] = _h(want)
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
        new_truth = merge_back(block, extract_block(truth_path.read_text()) or block) if target.slim else block
        atomic_write(truth_path, replace_block(truth_path.read_text(), new_truth))
        st[label] = _h(block)
        st["_truth"] = _h(new_truth)
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


BLOCKER_DETAIL_DAYS = 7
BLOCKER_DETAIL_MAX = 6    # every session loads agents.md, so only the latest few blockers in full


def _split_blockers(blockers: list[dict], now: "datetime | None" = None) -> tuple[list[dict], list[dict]]:
    """(blockers from the last BLOCKER_DETAIL_DAYS days, older ones); unparsable times count as recent."""
    from datetime import datetime, timedelta
    now = now or datetime.now().astimezone()
    recent, old = [], []
    for b in blockers:
        try:
            ts = datetime.fromisoformat(b["ts"])
            ts = ts if ts.tzinfo else ts.astimezone()
            (old if now - ts > timedelta(days=BLOCKER_DETAIL_DAYS) else recent).append(b)
        except (KeyError, TypeError, ValueError):
            recent.append(b)
    return recent, old


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
        recent, old = _split_blockers(cur["open_blocked"])
        shown = recent[-BLOCKER_DETAIL_MAX:]
        lines += ["", "**Open blockers**"] + [f"- {b['ts']} {b['agent']} @ {b['project'] or '-'}: {_short(b['summary'])}"
                                              for b in shown]
        hidden = len(recent) - len(shown) + len(old)
        if hidden:  # older ones only as a count, details via aisync tail
            lines.append(f"- {hidden} more open (older than {BLOCKER_DETAIL_DAYS} days, or beyond the latest {BLOCKER_DETAIL_MAX}); full list: `open_blocked` in `~/.ai-sync/state/current.json`")
    lines += ["", "**Latest**"] + [f"- {e['ts']} · {e['agent']} · {e['kind']} · {e['project'] or '-'}: {_short(e['summary'])}"
                                   for e in reversed(cur["tail"][-8:])]
    if hints:
        lines += ["", "**Self-check hints** (`aisync report`; reported only, never auto-applied)"] + [f"- {h}" for h in hints[:5]]
    lines.append(PROG_END)
    if cfg.progress_file:
        return _render_split(cfg, lines, changed, cur)
    agents_md = cfg.root / "agents.md"
    text = agents_md.read_text() if agents_md.exists() else f"# Context hub\n\n{PROG_START}\n{PROG_END}\n"
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


PROG_POINTER = ("_Live progress (who is doing what, claims, blockers, latest events) is not in this file, so the file "
                "never changes and stays in the prompt cache (2026-11-08: one journal event made every new session "
                "rewrite ~50k cached tokens). Hooks inject it at session start; otherwise read `{path}` or run `aisync status`._")


def progress_path(cfg: Config) -> Path:
    return Path(cfg.progress_file).expanduser() if cfg.progress_file.startswith(("/", "~")) else cfg.root / cfg.progress_file


def _render_split(cfg: Config, lines: list[str], changed: list[str], cur: dict) -> dict:
    """progress_file mode: the table goes to its own file; agents.md keeps only a fixed pointer."""
    p = progress_path(cfg)
    body = "\n".join(l for l in lines if l not in (PROG_START, PROG_END)) + "\n"
    old = p.read_text() if p.exists() else ""
    if old.split("\n", 1)[1:] != body.split("\n", 1)[1:]:            # ignore the timestamp line
        atomic_write(p, body)
        changed.append(str(p.name))
    agents_md = cfg.root / "agents.md"
    text = agents_md.read_text() if agents_md.exists() else "# Context hub\n"
    pointer = PROG_POINTER.format(path=str(p).replace(str(Path.home()), "~"))
    s, e = text.find(PROG_START), text.find(PROG_END)
    body = (text[:s].rstrip() + "\n\n" + pointer + "\n" + text[e + len(PROG_END):].lstrip("\n")) if s >= 0 and e >= 0 else text
    # exactly one pointer: an old writer re-adding the block left one more pointer each time (4 by 2026-11-08)
    head, sep, tail = body.partition(pointer)
    new = (head + sep + re.sub(r"\n*" + re.escape(pointer), "", tail)) if sep else body.rstrip() + "\n\n" + pointer + "\n"
    if new != text:
        atomic_write(agents_md, new)
        changed.append("agents.md")
    return {"changed": changed, "agents": len(cur["agents"])}


def progress_now(cfg: Config) -> str:
    """The live progress text for a hook to inject ('' when progress_file is off or unreadable)."""
    if not cfg.progress_file:
        return ""
    try:
        return progress_path(cfg).read_text().strip()
    except OSError:
        return ""


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

STALE_LOCK_SECS = 600     # git holds a lock for seconds; a lock this old was left by a crash (twice on 2026-11-08)
_LOCK_RE = re.compile(r"Unable to create '([^']+\.lock)': File exists")


def clear_stale_lock(err: str) -> bool:
    """Remove the lock git complains about when it is older than STALE_LOCK_SECS; True when one was removed."""
    m = _LOCK_RE.search(err or "")
    if not m:
        return False
    p = Path(m.group(1))
    try:
        if time.time() - p.stat().st_mtime < STALE_LOCK_SECS:
            return False
        p.unlink()
    except OSError:
        return False
    return True


def git_run(cfg: Config, args: list[str], retries: int = 1) -> subprocess.CompletedProcess:
    """Timeout on every call; exponential backoff between attempts."""
    err = ""
    for attempt in range(max(1, retries)):
        try:
            r = _git(args, cfg.root, cfg.git_timeout_secs)
            if r.returncode == 0:
                return r
            err = (r.stderr or r.stdout).strip()[:300]
            if clear_stale_lock(err):              # a crash left the lock: drop it and try again at once
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
    from . import collab, ruleids
    steps = (("harvest", harvest), ("rules", collab.harvest_rules), ("ids", ruleids.normalize), ("distribute", distribute),
             ("render", render), ("progress", collab.render_progress), ("tidy", tidy_kick),
             ("flush", flush))
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



def under_test() -> bool:
    """pytest, plain `python -m unittest`, or a test-launched child (AISYNC_UNDER_TEST); a real hub process imports none."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("AISYNC_UNDER_TEST")) or "unittest" in sys.modules


def tidy_kick(cfg: Config) -> dict:
    """Once a day, start `aisync tidy --write` detached (it scans weeks of transcripts; the cycle never waits, R-177)."""
    # 2026-11-08 EB-074: tests (test_wheel runs `aisync sync` in a temp hub) each spawned a scan of 11 GB of real
    # transcripts; they piled up past 16 GB RAM and the full swap, and the Mac panicked. Four guards now:
    if not cfg.tidy_daily:
        return {"started": False, "reason": "tidy_daily is off"}
    if os.environ.get("AISYNC_NO_TIDY") or under_test():
        return {"started": False, "reason": "disabled here (AISYNC_NO_TIDY or a test)"}
    day = f"{datetime.now().astimezone():%Y-%m-%d}"
    if (cfg.root / "tidy" / f"{day}.md").exists():
        return {"started": False, "reason": "today's report exists"}
    mark = cfg.state / "tidy-started"
    if mark.exists() and mark.read_text().strip() == day:
        return {"started": False, "reason": "already started today"}
    pidf = cfg.state / "tidy.pid"
    try:
        if pidf.exists() and _pid_alive(int(pidf.read_text().strip() or 0)):
            return {"started": False, "reason": "a tidy is still running"}
    except ValueError:
        pass
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(day)
    p = subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "ai_sync", "tidy", "--write"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "AISYNC_HOME": str(cfg.root)})
    pidf.write_text(str(p.pid))
    return {"started": True}


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
        "web_inbox": {k: v for k, v in load_json(cfg.state / "web_inbox.json", {}).items()
                      if k not in ("seen", "rejected")} | {"every_secs": cfg.web_inbox_secs},
        # non-vacuous: what this status really looked at; 0 of any is a failure in `aisync status`
        "checked": {"journal events": len(events), "rule copies": len(targets),
                    "rule block lines": sum(1 for ln in (truth or "").splitlines()[1:-1] if ln.strip())},
    }


def resume(cfg: Config) -> dict:
    h = load_health(cfg)
    h.update(push_failures=0, push_paused=False, cycle_failures=0, cycles_paused=False)
    save_health(cfg, h)
    return h
