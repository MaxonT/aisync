"""Stable rule IDs and the retired-rules archive (2026-11-02, owner: version management for the shared rules).

What: every active bullet of the owner-preferences block starts with an ID like [R-001]. IDs come from
      one counter and are never reused. A replaced rule leaves the block and goes to rules-archive.md
      beside preferences.md with its ID, full text and source tag, then
      "· retired: <date> · replaced_by: <ID>" (unknown when the record does not say).
Why:  rules can be named and replaced by ID, the history stays out of every tool's always-loaded copy,
      and the history file keeps who replaced whom.
How:  normalize() runs every cycle (core.run_cycle step "ids"): it numbers bullets that have no ID (a hand
      edit adopted from a tool copy, an AGENTS.md import) and moves any "### 已取代" section into the archive.
      collab.rule_add uses next_id() and archive_add() for new and replaced rules.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import core

ARCHIVE = "rules-archive.md"
SUPERSEDED_HEAD = "### 已取代"
_ID = re.compile(r"\[R-(\d{3,})\]")
_BULLET_ID = re.compile(r"^- \[(R-\d{3,})\] ")
_ID_ONLY = re.compile(r"^\[?(R-\d{3,})\]?$", re.I)
_META = re.compile(r" · retired: (\S+) · replaced_by: (\S+)$")
ARCHIVE_HEAD = """# Shared rules archive (retired rules)

Rules retired from the owner-preferences block of ~/.ai-sync/preferences.md, newest first. Each keeps its
ID (IDs are never reused), full text and source tag, then `· retired: <date> · replaced_by: <ID>`
(unknown when the old record does not say). Written by `aisync rule add --replaces`; read by
`aisync rule list --archived` and `aisync rulecheck`. Not loaded by any tool; the rules in force are only
the ones in preferences.md.
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", text.lower())


def archive_path(cfg: core.Config) -> Path:
    return cfg.root / ARCHIVE


def is_id(text: str) -> str | None:
    """'R-087' or '[r-087]' -> 'R-087'; anything else -> None."""
    m = _ID_ONLY.match(text.strip())
    return m.group(1).upper() if m else None


def bullet_id(line: str) -> str | None:
    m = _BULLET_ID.match(line)
    return m.group(1) if m else None


def with_id(bullet: str, rid: str) -> str:
    """'- text' -> '- [R-001] text'."""
    return f"- [{rid}] {bullet[2:]}"


def archive_entries(cfg: core.Config) -> list[str]:
    p = archive_path(cfg)
    return [l for l in p.read_text().splitlines() if l.startswith("- ")] if p.exists() else []


def next_id(cfg: core.Config) -> str:
    """Next free ID: above every ID in preferences.md, the archive and the counter. Call under core.locked."""
    seen = [0]
    for p in (cfg.root / "preferences.md", archive_path(cfg)):
        if p.exists():
            seen += [int(n) for n in _ID.findall(p.read_text())]
    st = core.load_json(cfg.state / "rule_ids.json", {})
    n = max(max(seen) + 1, int(st.get("next", 1)))
    core.save_json(cfg.state / "rule_ids.json", {"next": n + 1, "ts": core.now_iso()})
    return f"R-{n:03d}"


def archive_line(old_bullet: str, retired: str, replaced_by: str, note: str = "") -> str:
    """The archive entry: the old bullet as it was, an optional 被…取代 note, then the metadata."""
    return f"{old_bullet.rstrip()}{note} · retired: {retired or 'unknown'} · replaced_by: {replaced_by or 'unknown'}"


def archive_add(cfg: core.Config, entries: list[str]) -> None:
    """Put entries at the top of the archive (newest first), in the given order. Call under core.locked."""
    if not entries:
        return
    p = archive_path(cfg)
    lines = p.read_text().splitlines() if p.exists() else ARCHIVE_HEAD.splitlines() + [""]
    at = next((i for i, l in enumerate(lines) if l.startswith("- ")), len(lines))
    lines[at:at] = entries
    core.atomic_write(p, "\n".join(lines) + "\n")


def _retired_date(entry: str) -> str:
    m = re.search(r"取代（(\d{4}-\d{2}-\d{2})", entry)
    return m.group(1) if m else ""


def _replacement_key(entry: str) -> str:
    """The words inside 被「…」取代 (nested 「」 allowed), up to the first ；."""
    end = entry.rfind("」取代")
    start = entry.rfind("被「", 0, end) if end != -1 else -1
    if start == -1:
        return ""
    return entry[start + 2:end].split("；")[0].rstrip("…")


def _find(key: str, lines: list[str], skip: str = "") -> str:
    """ID of the one line containing key (normalized); '' when none or several."""
    k = _norm(key)
    if len(k) < 6:
        return ""
    hits = {bullet_id(l) for l in lines if k in _norm(l) and bullet_id(l) and bullet_id(l) != skip}
    return hits.pop() if len(hits) == 1 else ""


def normalize(cfg: core.Config) -> dict:
    """Number bullets without an ID; move a '### 已取代' section into the archive. Idempotent."""
    path = cfg.root / "preferences.md"
    rep = {"numbered": [], "archived": []}
    with core.locked(cfg):
        text = path.read_text()
        block = core.extract_block(text)
        if block is None:
            return rep
        lines = block.split("\n")
        retired: list[str] = []
        span = core._superseded_span(lines)
        if span:
            s, e = span
            retired = [l for l in lines[s + 1:e] if l.startswith("- ")]
            while s > 0 and lines[s - 1].strip() == "":
                s -= 1
            del lines[s:e]
        for i, l in enumerate(lines):
            if l.startswith("- ") and not bullet_id(l):
                lines[i] = with_id(l, next_id(cfg))
                rep["numbered"].append(bullet_id(lines[i]))
        numbered = [with_id(l, next_id(cfg)) if not bullet_id(l) else l for l in retired]
        active = [l for l in lines if bullet_id(l)]
        entries = []
        for l in numbered:
            key = _replacement_key(l)
            by = _find(key, active) or _find(key, numbered + archive_entries(cfg), skip=bullet_id(l)) if key else ""
            entries.append(archive_line(l, _retired_date(l), by))
            rep["archived"].append(bullet_id(l))
        if not rep["numbered"] and not entries:
            return rep
        archive_add(cfg, entries)       # history first: a crash in between never loses a rule's text
        core.atomic_write(path, core.replace_block(text, "\n".join(lines)))
    if rep["numbered"] or rep["archived"]:
        core.append_event(cfg, agent="ai-sync", kind="rule_change",
                          summary=f"rule ids: numbered {len(rep['numbered'])} rule(s), "
                                  f"moved {len(rep['archived'])} retired rule(s) to {ARCHIVE}",
                          files=["preferences.md", ARCHIVE])
    return rep


def parse_meta(entry: str) -> tuple[str, str]:
    """(retired, replaced_by) of an archive entry; ('', '') when it has no metadata."""
    m = _META.search(entry)
    return (m.group(1), m.group(2)) if m else ("", "")
