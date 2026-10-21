"""aisync board: the hub as one static HTML page (who did what, open claims, blockers, rules, timeline).

What: `render(cfg)` turns a hub into a self-contained HTML page: no scripts, no fonts or images
      fetched, light and dark mode. Sections: right now (claims, overlaps, blockers, rule conflicts),
      who did what (per tool), blocked -> unblocked history, shared rules, and the journal timeline.
Why:  `aisync status` is for a terminal; a page is easier to scan, to keep open, or to show someone.
How:  `aisync board --out board.html` (default: board.html in the current directory). The page
      contains journal summaries and file names, so read it before you publish it anywhere. A hub
      built by `aisync demo` carries a DEMO marker and its board says it is synthetic data.
"""
from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from datetime import datetime, timedelta

from . import __version__, collab, core

DEMO_MARKER = "DEMO"
TIMELINE_MAX = 80
# readable on both the light and the dark background
AGENT_COLORS = ("#d6336c", "#1c7ed6", "#2b8a3e", "#e8590c", "#7048e8", "#0c8599", "#a61e4d", "#5c940d")
KIND_LABELS = {"dev_log": "log", "decision": "decision", "blocked": "blocked", "unblocked": "unblocked",
               "rule_change": "rule", "commit": "commit", "note": "note", "claim": "claim", "release": "release"}

CSS = """
:root{--bg:#fbf8f3;--card:#fff;--ink:#2a2522;--muted:#6d655f;--line:#e7dfd5;--soft:#f4eee5;--accent:#d6336c;
--warn:#c92a2a;--warnbg:#fff0f0;--ok:#2b8a3e;--okbg:#ebfbee}
@media (prefers-color-scheme:dark){:root{--bg:#161418;--card:#211e25;--ink:#eee9e4;--muted:#a69da0;--line:#38333d;
--soft:#1c1a20;--accent:#f06595;--warn:#ff8787;--warnbg:#2c1a1d;--ok:#69db7c;--okbg:#18261c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);line-height:1.55;font-size:15px;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
a{color:var(--accent)}
nav a{margin-right:16px;font-size:14px;text-decoration:none}
h1{font-size:clamp(26px,4.5vw,38px);line-height:1.2;margin:18px 0 4px}
h2{font-size:20px;margin:38px 0 10px}
.lead{color:var(--muted);margin:0 0 18px}
.ribbon{border:2px dashed var(--accent);border-radius:14px;padding:10px 16px;margin:16px 0;background:var(--card)}
.ribbon b{color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.stat .n{font-size:26px;font-weight:700;line-height:1.1}
.stat .l{color:var(--muted);font-size:13px}
.stat.bad .n{color:var(--warn)}
.stat.good .n{color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:4px 16px;margin:10px 0;overflow-x:auto}
table{width:100%;border-collapse:collapse}
td,th{text-align:left;vertical-align:top;padding:9px 8px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}
th{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em;background:var(--soft);
padding:1px 5px;border-radius:5px;overflow-wrap:anywhere}
.who{display:inline-block;font-weight:700;white-space:nowrap}
.who::before{content:"";display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:var(--c)}
.kind{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;background:var(--soft);color:var(--muted);
white-space:nowrap}
.kind.blocked,.flag{background:var(--warnbg);color:var(--warn);font-weight:700}
.kind.unblocked{background:var(--okbg);color:var(--ok);font-weight:700}
.kind.decision,.kind.rule_change{color:var(--accent);font-weight:600}
.flag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px}
.muted{color:var(--muted)}
.t{white-space:nowrap;color:var(--muted);font-variant-numeric:tabular-nums}
.agents{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.agent{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:14px;padding:12px 14px}
.agent .last{margin:6px 0;font-size:14px}
.day{margin:22px 0 4px;font-weight:700}
.empty{color:var(--muted);padding:10px 0}
ul.rules{margin:0;padding:8px 0 8px 18px}
ul.rules li{margin:6px 0}
footer{margin-top:48px;color:var(--muted);font-size:13px}
table.tl{table-layout:fixed}
table.tl th:nth-child(1){width:64px}table.tl th:nth-child(2){width:132px}
table.tl th:nth-child(3){width:96px}table.tl th:nth-child(4){width:136px}
@media (max-width:640px){.hide-s{display:none}table.tl th:nth-child(2){width:110px}}
"""


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _color(agent: str) -> str:
    return AGENT_COLORS[int(hashlib.sha256(agent.encode()).hexdigest(), 16) % len(AGENT_COLORS)]


def _who(agent: str) -> str:
    return f'<span class="who" style="--c:{_color(agent)}">{_e(agent)}</span>'


def _files(files, n: int = 4) -> str:
    files = list(files or [])
    if not files:
        return '<span class="muted">whole repo</span>'
    more = f' <span class="muted">+{len(files) - n} more</span>' if len(files) > n else ""
    return " ".join(f"<code>{_e(f)}</code>" for f in files[:n]) + more


def _when(ts: str) -> str:
    """Absolute times only: the page is a snapshot and may be read days later."""
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return _e(ts)
    return f"{t:%b} {t.day} {t:%H:%M}"


def _table(head: list[str], rows: list[list[str]], empty: str, hide: tuple[int, ...] = (), kind: str = "") -> str:
    if not rows:
        return f'<div class="card"><div class="empty">{_e(empty)}</div></div>'
    cls = lambda i: ' class="hide-s"' if i in hide else ""
    th = "".join(f"<th{cls(i)}>{_e(h)}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    table_cls = f' class="{kind}"' if kind else ""
    return f'<div class="card"><table{table_cls}><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _rule_html(rule: str) -> str:
    text = _e(rule.lstrip("- "))
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r" (Reason:.*)$", r' <span class="muted">\1</span>', text)


def _blocked_history(events: list[dict]) -> list[tuple[dict, dict | None]]:
    """Every blocked event with the unblocked event that closed it (None while still open)."""
    opened: dict[str, dict] = {}
    pairs: list[tuple[dict, dict | None]] = []
    for e in events:
        if e.get("kind") == "blocked":
            opened[e["id"]] = e
        elif e.get("kind") == "unblocked":
            target = (e.get("refs") or {}).get("blocked_id")
            for bid in [b for b, v in opened.items() if b == target or (not target and v.get("project") == e.get("project"))]:
                pairs.append((opened.pop(bid), e))
    pairs += [(b, None) for b in opened.values()]
    return sorted(pairs, key=lambda p: p[0]["ts"], reverse=True)


def _duration(a: str, b: str) -> str:
    try:
        mins = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)
    except (TypeError, ValueError):
        return ""
    return f"{mins} min" if mins < 120 else f"{mins // 60} h {mins % 60} min"


def render(cfg: core.Config, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    demo = (cfg.root / DEMO_MARKER).exists()
    events = core.read_events(cfg)
    problems = core.verify_chain(cfg)
    claims = collab.load_claims(cfg)
    conflicts = core.load_json(cfg.state / "conflicts.json", {})
    history = _blocked_history(events)
    open_blocked = [b for b, u in history if u is None]
    rules = collab.rule_list(cfg) if (cfg.root / "preferences.md").exists() else []
    since = now - timedelta(days=14)
    recent = [e for e in events if e.get("ts", "") >= since.isoformat(timespec="seconds")]
    tools = sorted({e.get("agent", "?") for e in recent if not str(e.get("agent", "")).startswith("git:")})
    days = (now - datetime.fromisoformat(events[0]["ts"])).days + 1 if events else 0

    out = ['<!doctype html>',
           '<!--\nWhat: an aisync status board, rendered by `aisync board`.\n'
           'Why: one static page that shows who did what, open claims and blockers.\n'
           'How: open it in a browser; regenerate with `aisync board --out FILE`.\n-->',
           '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>{"aisync demo board (synthetic data)" if demo else "aisync status board"}</title>',
           '<meta name="description" content="aisync status board: who did what, open claims, blockers and shared rules '
           'across AI coding tools.">',
           f'<style>{CSS}</style></head><body><div class="wrap">']
    if demo:
        out.append('<nav><a href="../">Home</a><a href="../install/">Install</a>'
                   '<a href="https://github.com/MaxonT/aisync">GitHub</a></nav>')
    out.append(f'<h1>{"Status board: a demo hub" if demo else "aisync status board"}</h1>')
    out.append(f'<p class="lead">What {len(tools)} AI tools did over the last {min(days, 14)} days, '
               'who holds which files right now, and what is blocked. '
               f'Snapshot taken {now:%b} {now.day}, {now:%Y %H:%M %Z} by <code>aisync board</code>.</p>')
    if demo:
        out.append('<div class="ribbon"><b>Synthetic demo data.</b> Built by <code>aisync demo</code>: every tool '
                   'session, repo, commit and person on this page is made up. Run <code>aisync demo</code> to build '
                   'the same hub on your machine, then <code>aisync board</code> to render this page from it.</div>')

    def stat(n, label, tone=""):
        return f'<div class="stat {tone}"><div class="n">{_e(n)}</div><div class="l">{_e(label)}</div></div>'
    overlap_pairs = [(a, b) for i, a in enumerate(claims) for b in claims[i + 1:] if collab._overlaps(a, b)]
    out.append('<div class="stats">' + "".join([
        stat(len(events), "journal events"),
        stat("intact" if not problems else "BROKEN", "hash chain", "good" if not problems else "bad"),
        stat(len(tools), "tools active, 14 days"),
        stat(len(claims), "open claims", "bad" if overlap_pairs else ""),
        stat(len(open_blocked), "open blockers", "bad" if open_blocked else "good"),
        stat(len(conflicts), "rule conflicts", "bad" if conflicts else "good"),
    ]) + "</div>")

    out.append("<h2>Right now</h2>")
    overlapping = {id(c) for pair in overlap_pairs for c in pair}
    rows = []
    for c in sorted(claims, key=lambda c: c["since"]):
        flag = ""
        if id(c) in overlapping:
            others = sorted({b["agent"] for a, b in overlap_pairs if a is c} | {a["agent"] for a, b in overlap_pairs if b is c})
            flag = f' <span class="flag">overlaps {_e(", ".join(others))}</span>'
        rows.append([_who(c["agent"]), _e(c["project"]), _files(c["files"]),
                     _e(c["summary"]) + flag, f'<span class="t">until {_e(c["expires"][11:16])}</span>'])
    out.append(_table(["Tool", "Repo", "Files", "Doing", "Expires"], rows, "No open claims.", hide=(1, 2)))
    if overlap_pairs:
        out.append('<p class="muted">Overlapping claims are what <code>aisync claim</code> refuses with exit code 3: '
                   'the second tool is told who holds the files before it edits them.</p>')
    rows = [[_who(b["agent"]), _e(b.get("project") or "-"), _e(b["summary"]),
             f'<span class="t">{_e(_when(b["ts"]))}</span>'] for b in open_blocked]
    out.append(_table(["Blocked", "Repo", "Waiting for", "Since"], rows, "Nothing is blocked.", hide=(1,)))
    if conflicts:
        rows = [[_e(label), _e(c.get("reason", "")), f"<code>aisync resolve {_e(label)} --take truth|target</code>"]
                for label, c in sorted(conflicts.items())]
        out.append(_table(["Rule copy", "Conflict", "Fix"], rows, ""))

    out.append("<h2>Who did what</h2>")
    per = {}
    for e in recent:
        per.setdefault(e.get("agent", "?"), []).append(e)
    cards = []
    for agent, evs in sorted(per.items(), key=lambda kv: kv[1][-1]["ts"], reverse=True):
        kinds = Counter(e.get("kind") for e in evs)
        counts = ", ".join(f"{n} {KIND_LABELS.get(k, k)}{'s' if n > 1 and k not in ('blocked', 'unblocked') else ''}"
                           for k, n in kinds.most_common(4))
        last = evs[-1]
        repos = ", ".join(sorted({e.get("project") for e in evs if e.get("project")}))
        cards.append(f'<div class="agent" style="--c:{_color(agent)}">{_who(agent)}'
                     f'<div class="last">{_e(core._short(last["summary"], 110))}</div>'
                     f'<div class="muted">last {_e(_when(last["ts"]))} · {_e(repos or "-")}</div>'
                     f'<div class="muted">{_e(counts)}</div></div>')
    out.append(f'<div class="agents">{"".join(cards)}</div>' if cards else
               '<div class="card"><div class="empty">No events in the last 14 days.</div></div>')

    out.append("<h2>Blocked and unblocked</h2>")
    rows = []
    for b, u in history:
        if u:
            outcome = (f'<span class="kind unblocked">unblocked</span> by {_who(u["agent"])} after '
                       f'{_e(_duration(b["ts"], u["ts"]))}: {_e(u["summary"])}')
        else:
            outcome = '<span class="kind blocked">still open</span>'
        rows.append([f'<span class="t">{_e(_when(b["ts"]))}</span>', _who(b["agent"]), _e(b["summary"]), outcome])
    out.append(_table(["When", "Tool", "Blocked on", "Outcome"], rows, "No blockers logged.", hide=(0,)))

    out.append("<h2>Shared rules</h2>")
    out.append('<div class="card"><ul class="rules">' + "".join(f"<li>{_rule_html(r)}</li>" for r in rules)
               + "</ul></div>" if rules else '<div class="card"><div class="empty">No shared rules yet.</div></div>')

    out.append("<h2>Journal, newest first</h2>")
    shown = events[::-1][:TIMELINE_MAX]
    day = None
    rows: list[list[str]] = []
    blocks = []
    for e in shown:
        d = e["ts"][:10]
        if d != day:
            if rows:
                blocks.append(_table(["Time", "Tool", "Kind", "Repo", "What"], rows, "", hide=(3,), kind="tl"))
                rows = []
            day = d
            try:
                label = datetime.fromisoformat(e["ts"]).strftime("%A, %B ") + str(int(d[8:]))
            except ValueError:
                label = d
            blocks.append(f'<div class="day">{_e(label)}</div>')
        files = f'<div>{_files(e.get("files"), 3)}</div>' if e.get("files") else ""
        rows.append([f'<span class="t">{_e(e["ts"][11:16])}</span>', _who(e.get("agent", "?")),
                     f'<span class="kind {_e(e.get("kind"))}">{_e(KIND_LABELS.get(e.get("kind"), e.get("kind")))}</span>',
                     _e(e.get("project") or "-"), _e(e.get("summary")) + files])
    if rows:
        blocks.append(_table(["Time", "Tool", "Kind", "Repo", "What"], rows, "", hide=(3,), kind="tl"))
    out += blocks or ['<div class="card"><div class="empty">The journal is empty.</div></div>']
    if len(events) > TIMELINE_MAX:
        out.append(f'<p class="muted">{len(events) - TIMELINE_MAX} older events: <code>aisync tail -n {len(events)}</code></p>')

    out.append(f'<footer>aisync {_e(__version__)} · {len(events)} events · chain '
               f'{"intact" if not problems else "BROKEN"} · MIT · '
               '<a href="https://github.com/MaxonT/aisync">github.com/MaxonT/aisync</a></footer>')
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"
