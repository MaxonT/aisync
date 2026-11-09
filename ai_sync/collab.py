"""aisync collaboration layer: shared rules any agent can update, claims, progress fan-out, report.

What: the pieces that make the hub the one core every agent follows.
  - rule_add: any agent (Claude Code, Codex, Cursor, ...) adds a shared rule with one command; it
    lands in preferences.md (the truth) and the next cycle copies it to every tool.
  - harvest_rules: new timestamped entries in the "## Meta rule" section of the AGENTS.md of each
    repo listed in `rule_sources` are imported automatically, so a rule an agent wrote there (and
    forgot to `aisync rule add`) still reaches every tool.
  - claims: "I am working on these files of this repo now", with a TTL; overlaps warn.
  - render_progress: the rules pointer + live progress written into files each tool auto-loads
    (e.g. a block in ~/.codex/AGENTS.md), so an agent sees it without being told to look.
  - report: read-only self-check (unattributed commits, silent tools, missing projects, stale
    blockers, failures). It only reports; it never changes a rule by itself.
Why:  one shared rule set that any agent can update and every agent obeys.
How:  every function here is called from core.run_cycle (isolated steps) or from the CLI.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import core, ruleids

RULES_HEAD = "### Rules added by agents (via aisync, newest last)"
META_HEAD = "## Meta rule"
_ENTRY = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2} [A-Z]{2,5}):\s*(.*)$")
CLAIM_TTL_MIN = 120


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _tilde(p: Path | str) -> str:
    return str(p).replace(str(Path.home()), "~", 1)


# ───────────────────────────── shared rules ─────────────────────────────

def rule_lines(block: str) -> list[str]:
    return [l for l in block.splitlines() if l.startswith("- ")]


def _insert_rule(cfg: core.Config, bullet: str, key_text: str) -> str:
    """Append one bullet, with the next free ID, inside the owner-preferences block.
    Returns the bullet as written, or "" if an equal rule exists."""
    path = cfg.root / "preferences.md"
    text = path.read_text()
    block = core.extract_block(text)
    if block is None:
        raise ValueError("preferences.md has no owner-preferences block")
    key = _norm(key_text)[:60]
    if key and any(key in _norm(l) for l in rule_lines(block)):
        return ""
    bullet = ruleids.with_id(bullet, ruleids.next_id(cfg))
    body = block[: -len(core.BLOCK_END)].rstrip("\n")
    if RULES_HEAD not in body:
        body += f"\n\n{RULES_HEAD}\n"
    new_block = body + "\n" + bullet + "\n" + core.BLOCK_END
    core.atomic_write(path, core.replace_block(text, new_block))
    return bullet


SUPERSEDED_HEAD = "### 已取代"


def topics(block: str) -> list[str]:
    """Topic headings of a tidied rules block (### headings other than 已取代 and the legacy list)."""
    return [l[4:].strip() for l in block.splitlines()
            if l.startswith("### ") and l.strip() != SUPERSEDED_HEAD and l.strip() != RULES_HEAD]


def _sentences(text: str) -> int:
    """Sentence ends are . ! ? followed by a space or the end, and 。！？ anywhere. A dot inside a file name or date
    (x.env, x_autopilot.py, 2026-10-27) and semicolons do not end a sentence (2026-10-27: three false rejections)."""
    return len([x for x in re.split(r"(?<=[.!?])(?=\s|$)\s*|(?<=[。！？])\s*", text) if x.strip()])


def _insert_topic_rule(cfg: core.Config, topic: str, bullet: str, key_text: str, replaces: str, new_name: str) -> dict:
    """Put a rule at the top of its topic with the next free ID; the rule it replaces (given by ID like R-087,
    or by a piece of its text) moves to rules-archive.md with its retired date and the new rule's ID."""
    path = cfg.root / "preferences.md"
    text = path.read_text()
    block = core.extract_block(text)
    if block is None:
        raise ValueError("preferences.md has no owner-preferences block")
    names = topics(block)
    hit = [t for t in names if topic.lower() in t.lower()]
    if len(hit) != 1:
        raise ValueError(f"--topic must match exactly one topic; topics: {' | '.join(names)}")
    lines = block.splitlines()
    span = None
    if replaces:
        rid = ruleids.is_id(replaces)
        cur = [i for i, l in enumerate(lines) if l.startswith("- ") and not _in_superseded(lines, i)
               and (ruleids.bullet_id(l) == rid if rid else replaces in l)]
        if len(cur) != 1:
            what = f"rule {rid}" if rid else "current rule"
            raise ValueError(f"--replaces must match exactly one {what} (matched {len(cur)})")
        i = cur[0]
        end = i + 1
        while end < len(lines) and lines[end].startswith("  ") and lines[end].strip():
            end += 1
        span = (i, end)
    # Duplicate check: only current rules count, minus the one being replaced. A revision usually
    # keeps the old rule's opening words, and 已取代 lines are history, not rules.
    key = _norm(key_text)[:60]
    live = [l for k, l in enumerate(lines) if l.startswith("- ") and not _in_superseded(lines, k)
            and not (span and span[0] <= k < span[1])]
    if key and any(key in _norm(l) for l in live):
        return {"added": False, "moved": None}
    new_id = ruleids.next_id(cfg)
    bullet = ruleids.with_id(bullet, new_id)
    moved = None
    if span:
        i, end = span
        old = " ".join(x.strip() for x in lines[i:end])
        if not ruleids.bullet_id(old):              # a rule written before IDs existed gets one on the way out
            old = ruleids.with_id(old, ruleids.next_id(cfg))
        day = f"{datetime.now().astimezone():%Y-%m-%d}"
        moved = ruleids.archive_line(old, day, new_id, note=f" 被 {new_id}「{new_name}」取代（{day}）")
        del lines[i:end]
    h = lines.index(f"### {hit[0]}")
    lines.insert(h + 1, bullet)
    if moved:
        ruleids.archive_add(cfg, [moved])           # history first: a crash in between never loses the old text
    core.atomic_write(path, core.replace_block(text, "\n".join(lines)))
    return {"added": True, "moved": moved, "id": new_id, "bullet": bullet}


def _in_superseded(lines: list[str], i: int) -> bool:
    for l in reversed(lines[:i]):
        if l.startswith("### "):
            return l.strip() == SUPERSEDED_HEAD
    return False


def rule_add(cfg: core.Config, *, agent: str, text: str, reason: str = "", project: str = "",
             topic: str = "", replaces: str = "", enforced_by: str = "", judgment_only: str = "") -> dict:
    """Add a shared rule.

    Once preferences.md is grouped into topics, the writing standard applies:
    pick a topic, write only the current rule in at most three sentences, end with the source tag,
    and move the rule it replaces (by ID or text) into rules-archive.md in the same write. Every new rule
    gets the next free ID. The reason goes to the journal only.
    Before the tidy (no topic headings) it appends to the legacy list as before.
    A new rule may name what enforces it (enforced_by paths or a judgment_only reason; required with
    `require_enforcer = true`); it is written to rules-enforcement.toml under the new ID, never into the rules block.
    """
    from . import enforcement
    agent, text = (agent or "").strip(), " ".join((text or "").split())
    if not agent:
        raise ValueError("agent is required (pass --agent or set AISYNC_AGENT)")
    if len(text) < 8:
        raise ValueError("rule text is too short")
    entry = (enforcement.check_args(enforced_by, judgment_only)
             if cfg.require_enforcer or enforced_by or judgment_only else None)   # optional unless require_enforcer
    who = agent + (f", {project}" if project else "")
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    if topics(block):
        if not topic:
            raise ValueError(f"--topic is required; topics: {' | '.join(topics(block))}")
        if _sentences(text) > 3 and not cfg.rule_spec:
            raise ValueError("a rule is at most three sentences; put the why in --reason")
        if cfg.rule_spec:
            from . import rulespec
            bad = rulespec.problems(text, need_id=False, need_tag=False)
            if bad:
                raise ValueError("rule is not an interface (" + "; ".join(bad) + "); write "
                                 "'When: <trigger>. Do: <action>. Not: <forbidden>.' or pass --when/--do/--not")
        bullet = f"- {text.rstrip()} ({datetime.now().astimezone():%Y-%m-%d} · {who})"
        with core.locked(cfg):
            rep = _insert_topic_rule(cfg, topic, bullet, text, replaces, text[:30])
        added = rep["added"]
        bullet = rep.get("bullet", bullet)
    else:
        bullet = f"- {_stamp()} ({who}): {text}" + (f" Reason: {' '.join(reason.split())}" if reason.strip() else "")
        with core.locked(cfg):
            written = _insert_rule(cfg, bullet, text)
        added, bullet, rep = bool(written), written or bullet, {"moved": None}
    if added and entry is not None:
        with core.locked(cfg):
            enforcement.put(cfg, ruleids.bullet_id(bullet), entry)
    if added:
        why = f" Reason: {' '.join(reason.split())}" if reason.strip() and topics(block) else ""
        core.append_event(cfg, agent=agent, kind="rule_change", project=project,
                          summary=f"shared rule added: [{ruleids.bullet_id(bullet)}] {text}{why}"
                                  + (f" (replaces [{ruleids.bullet_id(rep['moved'])}])" if rep.get("moved") else ""),
                          files=["preferences.md"] + ([ruleids.ARCHIVE] if rep.get("moved") else []))
    return {"added": added, "rule": bullet, "moved": rep.get("moved"), "id": ruleids.bullet_id(bullet) if added else None}


def rule_list(cfg: core.Config, archived: bool = False) -> list[str]:
    """Current rules (each starts with its ID), or the retired ones from rules-archive.md."""
    if archived:
        return ruleids.archive_entries(cfg)
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    return rule_lines(block)


# ───────────────────────────── rules written in repo AGENTS.md ─────────────────────────────

def rule_source_files(cfg: core.Config) -> list[Path]:
    """AGENTS.md of every checkout/worktree of each rule-source repo (main checkout + *.wt-*)."""
    out = []
    for proj in core.project_list(cfg):
        for repo in cfg.rule_sources:
            if proj.name == repo or proj.name.startswith(repo + "."):
                if (proj / "AGENTS.md").exists():
                    out.append(proj / "AGENTS.md")
    return out


def parse_meta_entries(text: str) -> list[tuple[str, str]]:
    """(timestamp, full text) for each `- YYYY-MM-DD HH:MM TZ: ...` bullet under '## Meta rule'."""
    entries, inside, cur = [], False, None
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith(META_HEAD)
            if cur:
                entries.append(cur)
                cur = None
            continue
        if not inside:
            continue
        m = _ENTRY.match(line)
        if m:
            if cur:
                entries.append(cur)
            cur = [m.group(1), m.group(2)]
        elif cur and line.startswith("  ") and line.strip():
            cur[1] += " " + line.strip()
        elif cur and not line.strip():
            entries.append(cur)
            cur = None
    if cur:
        entries.append(cur)
    return [(ts, " ".join(t.split())) for ts, t in entries]


def _first_sentence(text: str, n: int = 220) -> str:
    m = re.match(r"(.+?[.;。])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= n else s[: n - 1] + "…"


def harvest_rules(cfg: core.Config) -> dict:
    """Import NEW meta-rule entries. A repo seen for the first time only records what exists."""
    st = core.load_json(cfg.state / "rule_sources.json", {"repos": [], "seen": []})
    seen, repos = set(st["seen"]), set(st["repos"])
    rep = {"files": 0, "imported": [], "errors": []}
    first_sight = set()
    for path in rule_source_files(cfg):
        repo = path.parent.name.split(".")[0]
        try:
            entries = parse_meta_entries(path.read_text())
        except OSError as ex:
            rep["errors"].append(f"{path.parent.name}: {type(ex).__name__}")
            continue
        rep["files"] += 1
        if repo not in repos:
            first_sight.add(repo)
        for ts, text in entries:
            key = core._h(ts + _norm(text)[:40])
            if key in seen:
                continue
            seen.add(key)
            if repo in repos:          # an entry that appeared after we started watching
                summary = _first_sentence(text)
                main = path.parent.parent / repo          # worktrees come and go; point at the main checkout
                src = main / "AGENTS.md" if (main / "AGENTS.md").exists() else path
                bullet = f"- {ts} (from {repo} AGENTS.md): {summary} Full text: `{_tilde(src)}`."
                with core.locked(cfg):
                    added = _insert_rule(cfg, bullet, summary)
                    if added:                  # imports are never blocked; they count as none until someone says
                        from . import enforcement
                        enforcement.put(cfg, ruleids.bullet_id(added),
                                        {"none": f"imported from {repo} AGENTS.md without an enforcer"})
                if added:
                    core.append_event(cfg, agent=f"repo:{repo}", kind="rule_change", project=repo,
                                      summary=f"shared rule imported from {repo} AGENTS.md: {summary}",
                                      files=["preferences.md"])
                    rep["imported"].append(summary)
    repos |= first_sight
    core.save_json(cfg.state / "rule_sources.json", {"repos": sorted(repos), "seen": sorted(seen)})
    return rep


# ───────────────────────────── claims (who is working where, now) ─────────────────────────────

def _now() -> datetime:
    return datetime.now().astimezone()


def load_claims(cfg: core.Config) -> list[dict]:
    now = _now()
    out = []
    for c in core.load_json(cfg.state / "claims.json", []):
        try:
            if datetime.fromisoformat(c["expires"]) > now:
                out.append(c)
        except (KeyError, ValueError):
            continue
    return out


def _overlaps(a: dict, b: dict) -> bool:
    if a["project"] != b["project"] or a["agent"] == b["agent"]:
        return False
    fa, fb = set(a.get("files") or []), set(b.get("files") or [])
    if not fa or not fb:                         # a claim on the whole repo overlaps everything
        return True
    return bool(fa & fb) or any(x.rstrip("/") + "/" in y or y.rstrip("/") + "/" in x for x in fa for y in fb)


def find_overlaps(cfg: core.Config, agent: str, project: str, files) -> list[dict]:
    probe = {"agent": agent, "project": project, "files": list(files or [])}
    return [c for c in load_claims(cfg) if _overlaps(probe, c)]


def claim(cfg: core.Config, *, agent: str, project: str, summary: str, files=(), ttl_min: int = CLAIM_TTL_MIN) -> dict:
    if not (agent and project and summary):
        raise ValueError("claim needs --agent, --project and --summary")
    files = [str(f) for f in files]
    with core.locked(cfg):
        claims = [c for c in load_claims(cfg) if not (c["agent"] == agent and c["project"] == project)]
        new = {"agent": agent, "project": project, "files": files, "summary": summary.strip(),
               "since": core.now_iso(), "expires": (_now() + timedelta(minutes=ttl_min)).isoformat(timespec="seconds")}
        overlaps = [c for c in claims if _overlaps(new, c)]
        claims.append(new)
        core.save_json(cfg.state / "claims.json", claims)
    core.append_event(cfg, agent=agent, kind="claim", project=project, summary=f"claimed: {summary}", files=files,
                      refs={"overlap_with": ",".join(sorted({c['agent'] for c in overlaps}))} if overlaps else {})
    return {"claim": new, "overlaps": overlaps}


def release(cfg: core.Config, *, agent: str, project: str = "") -> list[dict]:
    with core.locked(cfg):
        claims = load_claims(cfg)
        gone = [c for c in claims if c["agent"] == agent and (not project or c["project"] == project)]
        core.save_json(cfg.state / "claims.json", [c for c in claims if c not in gone])
    for c in gone:
        core.append_event(cfg, agent=agent, kind="release", project=c["project"], summary=f"released: {c['summary']}")
    return gone


# ───────────────────────────── self-check report (read-only) ─────────────────────────────

def report(cfg: core.Config, days: int = 7) -> dict:
    """Counts + plain hints. Never acts on them: too little data makes auto-tuning learn noise."""
    since = _now() - timedelta(days=days)
    events = []
    for e in core.read_events(cfg):
        try:
            if datetime.fromisoformat(e["ts"]) >= since:
                events.append(e)
        except (KeyError, ValueError):
            continue
    per_agent, unattributed, no_project, commits_by = {}, {}, 0, {}
    for e in events:
        a = e.get("agent", "?")
        per_agent.setdefault(a, {}).setdefault(e.get("kind", "?"), 0)
        per_agent[a][e.get("kind", "?")] += 1
        if e.get("kind") == "commit":
            commits_by[a] = commits_by.get(a, 0) + 1
            if a.startswith("git:"):
                unattributed[a[4:]] = unattributed.get(a[4:], 0) + 1
        elif not e.get("project") and not a.startswith("repo:"):
            no_project += 1
    runs = [r for r in core.read_runs(cfg, 500) if "steps" in r]
    failed = sum(1 for r in runs if not r.get("ok"))
    cur = core.build_current(core.read_events(cfg))
    stale = [b for b in cur["open_blocked"] if b.get("ts") and datetime.fromisoformat(b["ts"]) < _now() - timedelta(hours=24)]
    hints = []
    for author, n in sorted(unattributed.items(), key=lambda kv: -kv[1]):
        hints.append(f"{n} commit(s) by git author '{author}' name no tool: add an `Agent: <tool>` trailer to commits")
    for tool in cfg.roster:
        if tool not in per_agent:
            hints.append(f"{tool} logged nothing in {days} days: it may not be reading {_tilde(cfg.root / 'agents.md')}")
        elif per_agent[tool].get("commit") and not any(k for k in per_agent[tool] if k not in ("commit", "claim", "release")):
            hints.append(f"{tool} committed but never ran `aisync log`")
    if no_project:
        hints.append(f"{no_project} event(s) without --project")
    if stale:
        hints.append(f"{len(stale)} blocker(s) open for more than 24 h")
    if failed:
        hints.append(f"{failed} of {len(runs)} sync cycles failed: see `aisync status`")
    conf = core.load_json(cfg.state / "conflicts.json", {})
    if conf:
        hints.append(f"rule conflict open in {', '.join(conf)}: `aisync resolve`")
    return {"days": days, "events": len(events), "per_agent": per_agent, "unattributed_commits": unattributed,
            "events_without_project": no_project, "stale_blockers": len(stale), "cycles": len(runs),
            "failed_cycles": failed, "open_conflicts": sorted(conf), "rules": len(rule_list(cfg)), "hints": hints}


# ───────────────────────────── progress fan-out to files tools auto-load ─────────────────────────────

def progress_text(cfg: core.Config, cur: dict, claims: list[dict], hints: list[str]) -> str:
    lines = [core.PROG_START,
             f"## Live progress of all AI tools (aisync, auto-generated {core.now_iso()}, do not edit)",
             "",
             f"Shared rules: `{_tilde(cfg.root / 'preferences.md')}` (every agent follows them; change one with "
             f"`aisync rule add --agent <tool> \"...\"`). Full hub page: `{_tilde(cfg.root / 'agents.md')}`.", ""]
    if claims:
        lines.append("**Working right now** (do not edit these files without coordinating)")
        lines += [f"- {c['agent']} @ {c['project']}: {core._short(c['summary'], 90)}"
                  f"{' [' + ', '.join(c['files'][:6]) + ']' if c['files'] else ' [whole repo]'} until {c['expires'][11:16]}"
                  for c in claims]
        lines.append("")
    lines.append("**Last seen per agent**")
    for a, e in sorted(cur["agents"].items(), key=lambda kv: kv[1]["ts"] or "", reverse=True)[:8]:
        lines.append(f"- {a} {e['ts']} · {e['project'] or '-'}: {core._short(e['summary'], 100)}")
    lines += ["", "**Latest**"] + [f"- {e['ts']} · {e['agent']} · {e['kind']} · {e['project'] or '-'}: {core._short(e['summary'], 100)}"
                                   for e in reversed(cur["tail"][-6:])]
    if hints:
        lines += ["", "**Hints from the self-check** (`aisync report`)"] + [f"- {h}" for h in hints[:4]]
    lines.append(core.PROG_END)
    return "\n".join(lines)


def _body(section: str) -> list[str]:
    """Section minus its timestamped header line, for change detection."""
    return [l for l in section.splitlines() if "auto-generated" not in l]


def render_progress(cfg: core.Config) -> dict:
    cur = core.build_current(core.read_events(cfg))
    section = progress_text(cfg, cur, load_claims(cfg), report(cfg)["hints"])
    rep = {"updated": [], "errors": []}
    for t in cfg.progress_targets:
        try:
            if t.mode == "file":
                old = t.path.read_text() if t.path.exists() else ""
                if _body(old) != _body(section + "\n"):
                    core.atomic_write(t.path, section + "\n")
                    rep["updated"].append(t.label)
                continue
            text = t.path.read_text()
            s, e = text.find(core.PROG_START), text.find(core.PROG_END)
            if s < 0 or e < 0:
                new = text.rstrip("\n") + "\n\n" + section + "\n"
            else:
                if _body(text[s:e + len(core.PROG_END)]) == _body(section):
                    continue
                new = text[:s] + section + text[e + len(core.PROG_END):]
            core.atomic_write(t.path, new)
            rep["updated"].append(t.label)
        except OSError as ex:
            rep["errors"].append(f"{t.label}: {type(ex).__name__}")   # one bad target never blocks the rest
    return rep
