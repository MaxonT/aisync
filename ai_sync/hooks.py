"""aisync hooks: keep every running agent posted mid-session, and attribute every commit.

What: three gaps the session-start rules page cannot close on its own.
  - news: what the OTHER tools did since this session last looked (new events, new shared rules).
    `aisync hook <tool>` is wired into each tool's own hook system, so a running Claude Code, Codex
    or Cursor session gets the news injected into its context without asking. Nothing new = no
    output, so a quiet hub costs zero tokens.
  - commit trailer: a global git `commit-msg` hook adds `Agent: <tool>` when the commit comes from
    an AI tool (detected from the environment the tool's shell inherits). Repo-local hooks still
    run: every hook name chains to `.git/hooks/<name>`.
  - delivery: Cursor gets the shared rules + progress from its sessionStart hook (its User Rules
    live inside the app); file-based tools get the rules block seeded into their rule file.
Why:  people run several AI tools at once; one of them changing something must reach the others.
How:  every hook call is fail-open (never blocks the tool, always exits 0), has its own cursor
      file per session, and is logged to state/hooks.jsonl so `aisync status` can show it.
      Installing is two-phase: `plan_install` computes every change as a diff, nothing is written
      until the user says yes (`aisync install-hooks` asks; `--dry-run` only shows).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import collab, core

NEWS_MAX_EVENTS = 12
CURSOR_TTL_DAYS = 7
HOOK_LOG_MAX_BYTES = 2 * 1024 * 1024
HOOK_TIMEOUT_SECS = 5
# SessionStart sets the cursor; UserPromptSubmit and PostToolUse (after every tool call, so a long
# task hears news too) inject. Same events for Claude Code and Codex.
NESTED_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse")

# Which tool is committing? First match wins; explicit beats inferred, a nested tool beats its host
# (Claude Code started inside Cursor's terminal has both CLAUDECODE and Cursor's bundle id).
DETECT = (
    ("AISYNC_AGENT", None, None),
    ("CLAUDECODE", "1", "claude-code"),
    ("CODEX_SHELL", "1", "codex"),
    ("CODEX_THREAD_ID", None, "codex"),
    ("CURSOR_AGENT", "1", "cursor"),
    ("__CFBundleIdentifier", "com.openai.codex", "codex"),
    ("__CFBundleIdentifier", "com.todesktop.230313mzl4w4u92", "cursor"),
    ("__CFBundleIdentifier", "com.trae.app", "trae"),
)


def detect_agent(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    for key, want, agent in DETECT:
        val = env.get(key, "")
        if val and (want is None or val == want):
            return agent or val.strip()
    return ""


# ───────────────────────────── news since this session last looked ─────────────────────────────

def _cursor_path(cfg: core.Config, agent: str, session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{agent}-{session or 'default'}")[:120]
    return cfg.state / "cursors" / f"{safe}.json"


def _rules(cfg: core.Config) -> list[str]:
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    return collab.rule_lines(block)


def _prune_cursors(cfg: core.Config) -> None:
    cutoff = time.time() - CURSOR_TTL_DAYS * 86400
    for p in (cfg.state / "cursors").glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def news(cfg: core.Config, agent: str, session: str = "", init_only: bool = False) -> dict:
    """New events from other agents + new shared rules since this session's cursor.

    The first call of a session only sets the cursor (the session already loaded the full
    progress page at start). `init_only` does the same on purpose (SessionStart hooks).
    """
    events = core.read_events(cfg)
    rules = _rules(cfg)
    path = _cursor_path(cfg, agent, session)
    cur = core.load_json(path, None)
    fresh, new_rules = [], []
    if cur is not None and not init_only:
        seen = cur.get("seen", 0)
        if seen > len(events):              # journal was reset: start over, report nothing old
            seen = len(events)
        fresh = [e for e in events[seen:] if e.get("agent") != agent]
        old = set(cur.get("rules", []))
        new_rules = [r for r in rules if r not in old]
    path.parent.mkdir(parents=True, exist_ok=True)
    core.save_json(path, {"seen": len(events), "rules": rules, "ts": core.now_iso(), "agent": agent})
    _prune_cursors(cfg)
    return {"events": fresh, "new_rules": new_rules, "first": cur is None}


def news_text(rep: dict) -> str:
    if not rep["events"] and not rep["new_rules"]:
        return ""
    out = ["[aisync] While you were working, other AI tools changed things:"]
    if rep["new_rules"]:
        out.append("NEW shared rule(s), follow them from now on:")
        out += [core._short(r, 600) for r in rep["new_rules"]]
    if rep["events"]:
        shown = rep["events"][-NEWS_MAX_EVENTS:]
        for e in shown:
            files = f" [{', '.join(e.get('files', [])[:4])}]" if e.get("files") else ""
            out.append(f"- {e.get('ts', '')[11:16]} {e.get('agent')} {e.get('kind')} "
                       f"@ {e.get('project') or '-'}: {core._short(e.get('summary', ''), 160)}{files}")
        if len(rep["events"]) > len(shown):
            out.append(f"(+{len(rep['events']) - len(shown)} older: `aisync tail -n 30`)")
    out.append("If any of this touches files you are editing, coordinate (`aisync claims`) before going on.")
    return "\n".join(out)


def brief_text(cfg: core.Config) -> str:
    """Everything a tool without file-based rules needs at session start: rules + live progress."""
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    block = "\n".join(l for l in block.splitlines() if not l.startswith("<!--"))
    cur = core.build_current(core.read_events(cfg))
    prog = collab.progress_text(cfg, cur, collab.load_claims(cfg), collab.report(cfg)["hints"])
    prog = "\n".join(l for l in prog.splitlines() if not l.startswith("<!--"))
    return (f"[aisync] Shared rules (every AI tool follows them; source {collab._tilde(cfg.root / 'preferences.md')}):\n"
            f"{block.strip()}\n\n{prog.strip()}\n\n"
            "Protocol: claim before editing (`aisync claim`), log after each unit of work "
            "(`aisync log --agent cursor --project <repo> ...`), every commit ends with `Agent: cursor`.")


# ───────────────────────────── `aisync hook <tool>`: one entry for every tool ─────────────────────────────

def _log_hook(cfg: core.Config, rec: dict) -> None:
    path = cfg.state / "hooks.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > HOOK_LOG_MAX_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
        with open(path, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def handle(tool: str, payload: dict, cfg: core.Config | None = None) -> str:
    """Return what the tool's hook must print on stdout ('' = nothing). Never raises."""
    t0 = time.monotonic()
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    session = str(payload.get("session_id") or payload.get("conversation_id") or payload.get("sessionId") or "")
    rec = {"ts": core.now_iso(), "tool": tool, "event": event, "session": session[:40], "ok": True}
    out = ""
    try:
        cfg = cfg or core.load_config()
        start = event in ("SessionStart", "sessionStart")
        rep = news(cfg, tool, session, init_only=start)
        rec["events"], rec["rules"] = len(rep["events"]), len(rep["new_rules"])
        if tool == "cursor":
            if start:
                out = json.dumps({"additional_context": brief_text(cfg), "env": {"AISYNC_AGENT": "cursor"}},
                                 ensure_ascii=False)
            elif (text := news_text(rep)):
                out = json.dumps({"additional_context": text}, ensure_ascii=False)
        elif not start and (text := news_text(rep)):       # claude-code, codex: same schema
            out = json.dumps({"hookSpecificOutput": {"hookEventName": event or "UserPromptSubmit",
                                                     "additionalContext": text}}, ensure_ascii=False)
    except Exception as ex:                                 # fail open: a hook must never break the tool
        rec.update(ok=False, error=f"{type(ex).__name__}: {ex}"[:300])
        out = ""
    rec["chars"], rec["ms"] = len(out), int((time.monotonic() - t0) * 1000)
    if cfg is not None:
        _log_hook(cfg, rec)
    return out


def hook_stats(cfg: core.Config, hours: int = 24) -> dict:
    path = cfg.state / "hooks.jsonl"
    since = datetime.now().astimezone() - timedelta(hours=hours)
    per: dict[str, dict] = {}
    if not path.exists():
        return per
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
            if datetime.fromisoformat(r["ts"]) < since:
                continue
        except (ValueError, KeyError):
            continue
        s = per.setdefault(r.get("tool", "?"), {"calls": 0, "errors": 0, "injected": 0, "ms": 0, "last": ""})
        s["calls"] += 1
        s["errors"] += 0 if r.get("ok") else 1
        s["injected"] += 1 if r.get("chars") else 0
        s["ms"] += r.get("ms", 0)
        s["last"] = r["ts"]
    for s in per.values():
        s["avg_ms"] = s.pop("ms") // max(s["calls"], 1)
    return per


# ───────────────────────────── installers ─────────────────────────────

def aisync_bin() -> str:
    cand = Path(sys.executable).parent / "aisync"
    if cand.exists():
        return str(cand)
    return shutil.which("aisync") or "aisync"


@dataclass
class Change:
    """One planned edit to a file outside the hub. Nothing is written until `apply()`."""
    tool: str
    path: Path
    old: str
    new: str
    note: str
    apply: Callable[[], None]

    def diff(self) -> str:
        shown = collab._tilde(self.path)
        return "".join(difflib.unified_diff(self.old.splitlines(True), self.new.splitlines(True),
                                            fromfile=f"{shown} (now)", tofile=f"{shown} (after)"))


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _save_with_backup(path: Path, text: str) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak-aisync-{datetime.now():%Y%m%d-%H%M%S}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write(path, text)


def _plan_json(tool: str, path: Path, default: dict, mutate: Callable[[dict], list[str]]) -> Change | None:
    old = _read(path)
    data = json.loads(old) if old.strip() else dict(default)   # invalid JSON raises: never clobber it
    added = mutate(data)
    if not added:
        return None
    new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return Change(tool, path, old, new, "add hooks: " + ", ".join(added), lambda: _save_with_backup(path, new))


def _add_nested(hooks: dict, event: str, command: str) -> bool:
    """Claude Code / Codex schema: {event: [{hooks: [{type, command, timeout}]}]}. Idempotent."""
    groups = hooks.setdefault(event, [])
    if any(h.get("command") == command for g in groups for h in g.get("hooks", [])):
        return False
    groups.append({"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECS}]})
    return True


def plan_claude(home: Path, bin_: str) -> Change | None:
    cmd = f"{bin_} hook claude-code"
    return _plan_json("claude-code", home / ".claude" / "settings.json", {},
                      lambda d: [e for e in NESTED_EVENTS if _add_nested(d.setdefault("hooks", {}), e, cmd)])


def plan_codex(home: Path, bin_: str) -> Change | None:
    cmd = f"{bin_} hook codex"
    return _plan_json("codex", home / ".codex" / "hooks.json", {},
                      lambda d: [e for e in NESTED_EVENTS if _add_nested(d.setdefault("hooks", {}), e, cmd)])


def plan_cursor(home: Path, bin_: str) -> Change | None:
    cmd = f"{bin_} hook cursor"

    def mutate(data: dict) -> list[str]:
        data.setdefault("version", 1)
        hooks, added = data.setdefault("hooks", {}), []
        for event in ("sessionStart", "postToolUse"):
            lst = hooks.setdefault(event, [])
            if not any(h.get("command") == cmd for h in lst):
                lst.append({"command": cmd, "timeout": HOOK_TIMEOUT_SECS})
                added.append(event)
        return added
    return _plan_json("cursor", home / ".cursor" / "hooks.json", {"version": 1}, mutate)


def plan_seed_block(cfg: core.Config, label: str, path: Path) -> Change | None:
    """Put the shared-rules block into a target file that has none yet, so distribute can keep it in sync."""
    old = _read(path)
    if core.BLOCK_START in old:
        return None
    truth = core.extract_block((cfg.root / "preferences.md").read_text())
    if truth is None:
        raise ValueError("preferences.md has no owner-preferences block")
    new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + truth + "\n"
    return Change(label, path, old, new, "seed the shared-rules block", lambda: core.atomic_write(path, new))


def _apply(change: Change | None) -> list[str]:
    if change is None:
        return []
    change.apply()
    return change.note.split(": ", 1)[-1].split(", ")


def install_claude(home: Path, bin_: str) -> list[str]:
    return _apply(plan_claude(home, bin_))


def install_codex(home: Path, bin_: str) -> list[str]:
    return _apply(plan_codex(home, bin_))


def install_cursor(home: Path, bin_: str) -> list[str]:
    return _apply(plan_cursor(home, bin_))


def seed_block(cfg: core.Config, path: Path) -> bool:
    return bool(_apply(plan_seed_block(cfg, "rules", path)))


# Git hook names that get a chaining stub, so a repo's own .git/hooks/* keep working.
GIT_HOOKS = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
             "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
             "pre-push", "post-rewrite", "pre-auto-gc", "push-to-checkout", "reference-transaction")

_CHAIN = """
# chain to the repo's own hook (core.hooksPath would otherwise hide it)
local_hook="$(git rev-parse --git-common-dir 2>/dev/null)/hooks/$(basename "$0")"
if [ -x "$local_hook" ]; then exec "$local_hook" "$@"; fi
exit 0
"""

_COMMIT_MSG = """# aisync: add `Agent: <tool>` when an AI tool makes the commit; human commits stay untouched.
msg="$1"
agent="$({detect})"
if [ -n "$agent" ] && [ -f "$msg" ] && grep -qvE '^[[:space:]]*(#|$)' "$msg" \\
   && ! grep -qi '^agent:' "$msg"; then
  git interpret-trailers --in-place --trailer "Agent: $agent" "$msg" 2>/dev/null \\
    || printf '\\nAgent: %s\\n' "$agent" >> "$msg"
fi
"""


def _detect_sh() -> str:
    parts = []
    for key, want, agent in DETECT:
        if agent is None:
            parts.append(f'if [ -n "${{{key}:-}}" ]; then echo "${key}"')
        elif want is None:
            parts.append(f'elif [ -n "${{{key}:-}}" ]; then echo {agent}')
        else:
            parts.append(f'elif [ "${{{key}:-}}" = "{want}" ]; then echo {agent}')
    return "; ".join(parts) + "; fi"


def _hook_body(name: str) -> str:
    body = "#!/bin/sh\n# generated by `aisync install-hooks`; do not edit (re-run it instead)\n"
    if name == "commit-msg":
        body += _COMMIT_MSG.format(detect=_detect_sh())
    return body + _CHAIN


def write_git_hooks(dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name in GIT_HOOKS:
        body = _hook_body(name)
        p = dest / name
        if not p.exists() or p.read_text() != body:
            p.write_text(body)
            written.append(name)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return written


def _git_hooks_path() -> str:
    return subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"],
                          capture_output=True, text=True).stdout.strip()


def plan_git(cfg: core.Config) -> Change | None:
    """Global `core.hooksPath` -> <hub>/githooks. Refuses to take over a hooksPath set by something else."""
    dest = cfg.root / "githooks"
    cur = _git_hooks_path()
    if cur and Path(cur).expanduser() != dest:
        raise ValueError(f"core.hooksPath already points at {cur}; not overriding it")
    stale = [n for n in GIT_HOOKS if not (dest / n).exists()]
    if cur and not stale and (dest / "commit-msg").read_text() == _hook_body("commit-msg"):
        return None

    def apply() -> None:
        write_git_hooks(dest)
        if not cur:
            subprocess.run(["git", "config", "--global", "core.hooksPath", str(dest)], check=True)
    return Change("git", Path("~/.gitconfig").expanduser(),
                  f"[core]\n\thooksPath = {cur or '(unset)'}\n", f"[core]\n\thooksPath = {dest}\n",
                  f"global core.hooksPath -> {collab._tilde(dest)} ({len(GIT_HOOKS)} chaining hook stubs)", apply)


def install_git(cfg: core.Config) -> dict:
    try:
        change = plan_git(cfg)
    except ValueError as ex:
        return {"written": [], "hooksPath": _git_hooks_path(), "set": False, "error": str(ex)}
    dest = cfg.root / "githooks"
    was_set = bool(_git_hooks_path())
    written = write_git_hooks(dest)
    if change is not None:
        change.apply()
    return {"written": written, "hooksPath": str(dest), "set": not was_set}


INSTALL_STEPS = ("claude-code", "codex", "cursor", "rules", "git")


def plan_install(cfg: core.Config, home: Path, bin_: str, want=INSTALL_STEPS) -> tuple[list[Change], list[str]]:
    """Every change `install-hooks` would make, and the steps that cannot be done. Writes nothing."""
    changes, errors = [], []
    planners = {"claude-code": lambda: [plan_claude(home, bin_)],
                "codex": lambda: [plan_codex(home, bin_)],
                "cursor": lambda: [plan_cursor(home, bin_)],
                "rules": lambda: [plan_seed_block(cfg, t.label, t.path) for t in cfg.targets],
                "git": lambda: [plan_git(cfg)]}
    for step in INSTALL_STEPS:
        if step not in want:
            continue
        try:
            changes += [c for c in planners[step]() if c is not None]
        except (OSError, ValueError) as ex:
            errors.append(f"{step}: {ex}")
    return changes, errors
