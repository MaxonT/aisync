"""mcp_server: ai-sync as an MCP server, so any agent that speaks MCP can use the hub without hooks or file edits.

What: `aisync mcp` serves eight tools over stdio: rules, rule_add, log, claim, release, news, status, tidy.
Why:  owner 2026-11-08: any AI agent should be able to use ai-sync at a glance, not only the tools we wrote
      adapters for. The three universal doors are AGENTS.md (a [[target]] file), the CLI, and this server.
How:  the tool functions below are plain Python over collab/core (tested without MCP installed); build() wraps
      them with the official SDK's MCPServer (`pip install 'aisync[mcp]'`, mcp>=2.3). Nothing here prints to
      stdout outside the protocol.
"""
from __future__ import annotations

from . import collab, core

INSTRUCTIONS = (
    "ai-sync: shared rules, a hash-chained journal and file claims for every AI agent on this machine. "
    "Start with `rules` (core rules; pass a topic to read a folded one) and `news`. Before editing files other agents "
    "may touch, `claim` them (overlaps come back so you can stop); `release` when done. After each unit of work, "
    "`log` one line. When the owner states a rule for all AIs, `rule_add` it as When/Do/Not."
)


def _cfg() -> core.Config:
    return core.load_config()


def rules(topic: str = "") -> str:
    """The shared rules: the core layer (or all rules when nothing is layered); with topic, that topic's rules."""
    from . import layers
    cfg = _cfg()
    truth = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    if topic:
        found = layers.topic_rules(truth, topic)
        return "\n".join(found) if found else f"no topic starts with {topic!r}"
    if cfg.core_topics:
        return layers.core_view(core.strip_superseded(truth), cfg.core_topics, cfg.shelf)
    return core.strip_superseded(truth)


def rule_add(agent: str, when: str, do: str, topic: str, reason: str, not_: str = "", project: str = "",
             enforced_by: str = "", judgment_only: str = "", replaces: str = "", planned: str = "",
             size: str = "") -> str:
    """Add a shared rule in the When/Do/Not shape. Needs enforced_by (paths), judgment_only (why no code can), or
    planned (the check to write) with size S, M or L."""
    from . import rulespec
    try:
        rep = collab.rule_add(_cfg(), agent=agent, text=rulespec.compose(when, do, not_), reason=reason,
                              project=project, topic=topic, replaces=replaces, enforced_by=enforced_by,
                              judgment_only=judgment_only, planned=planned, size=size)
    except ValueError as ex:
        return f"refused: {ex}"
    return f"added {rep['id']}: {rep['rule']}" if rep["added"] else "an equal rule already exists; nothing changed"


def log(agent: str, project: str, summary: str, kind: str = "dev_log", files: str = "") -> str:
    """Append one event to the journal (kinds: dev_log, decision, blocked, unblocked, rule_change, note)."""
    try:
        ev = core.append_event(_cfg(), agent=agent, kind=kind, project=project, summary=summary,
                               files=[f.strip() for f in files.split(",") if f.strip()])
    except ValueError as ex:
        return f"refused: {ex}"
    return f"logged {ev['id']}"


def claim(agent: str, project: str, summary: str, files: str = "") -> str:
    """Claim files (comma-separated; empty = whole repo) before editing. Overlaps with other agents come back."""
    try:
        rep = collab.claim(_cfg(), agent=agent, project=project, summary=summary,
                           files=[f.strip() for f in files.split(",") if f.strip()])
    except ValueError as ex:
        return f"refused: {ex}"
    c = rep["claim"]
    msg = f"claimed {project} {c['files'] or '[whole repo]'} until {c['expires'][11:16]}"
    if rep["overlaps"]:
        msg += "; OVERLAP with " + ", ".join(f"{o['agent']} ({o['summary']})" for o in rep["overlaps"]) + ": coordinate or pick other work"
    return msg


def release(agent: str, project: str = "") -> str:
    """Release your claims (all of them, or one project's)."""
    gone = collab.release(_cfg(), agent=agent, project=project)
    return f"released {len(gone)} claim(s)"


def news(agent: str, session: str = "mcp") -> str:
    """What other agents did and which rules changed since this session last asked ('' = nothing new)."""
    from . import hooks
    rep = hooks.news(_cfg(), agent, session)
    if rep["first"]:
        return "cursor set; ask again later for news (call status for the current picture)"
    return hooks.news_text(rep) or "nothing new"


def status() -> str:
    """Live progress: who is doing what, claims, open blockers, latest events."""
    cfg = _cfg()
    prog = core.progress_now(cfg)
    if prog:
        return prog
    cur = core.build_current(core.read_events(cfg))
    return collab.progress_text(cfg, cur, collab.load_claims(cfg), collab.report(cfg)["hints"])


def tidy() -> str:
    """The rules clerk's proposals (duplicates, rules not cited lately, core budget, gaps). Proposals only."""
    from datetime import datetime
    from . import tidy as t
    cfg = _cfg()
    return t.render(t.report(cfg), f"{datetime.now().astimezone():%Y-%m-%d}")


TOOLS = (rules, rule_add, log, claim, release, news, status, tidy)


def build():
    """The MCPServer with every tool registered (needs `pip install 'aisync[mcp]'`)."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as ex:                      # mcp missing or a 1.x install
        raise RuntimeError("the MCP server needs the official SDK: pip install 'aisync[mcp]' (mcp>=2.3)") from ex
    server = MCPServer("aisync", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool(name=fn.__name__.rstrip("_"), description=fn.__doc__)(fn)
    return server


def main() -> int:
    try:
        server = build()
    except RuntimeError as ex:
        import sys
        print(f"error: {ex}", file=sys.stderr)
        return 2
    server.run("stdio")
    return 0
