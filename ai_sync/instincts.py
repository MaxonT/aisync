"""instincts: the habits that grew out of past mistakes, injected into every session by the hook.

What: an optional habit file (`instincts_path` in config.toml) with one numbered line per habit, each
      pointing back at an EB-id in an optional error log (`errors_path`, headings "### EB-NNN title").
      `aisync hook` injects the whole file at SessionStart and only new lines afterwards.
Why:  a lesson file nobody reads does nothing; injected, every new session starts with it.
How:  both paths are off by default (empty). Reads are plain file reads, no git, no scanning. Adding a line
      only writes the file and the journal; committing is the caller's job.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import core

LINE_RE = re.compile(r"^\s*(\d+)\.\s+\S")
EB_RE = re.compile(r"^EB-\d+$")
HEAD_RE = re.compile(r"^#{1,6}\s*(EB-\d+)\b", re.M)
CONFLICT_RE = re.compile(r"^(<<<<<<<|=======$|>>>>>>>)", re.M)
SAME = 0.8           # a new habit this close to an existing one is a repeat (one habit was once added 9 times)
SAME_EB = 0.6        # with the same EB id, this close is enough (0.45 flagged two unrelated short habits)
MAX_PER_EB = 2       # one mistake grows at most two habits; a third means sharpen one of them


def _habit(line: str) -> str:
    """'12. text（EB-001）' -> 'text'."""
    return re.sub(r"（EB-\d+）|\(EB-\d+\)", "", re.sub(r"^\s*\d+\.\s+", "", line)).strip(" 。.")


def repeat_of(lines: list[str], text: str, eb: str) -> str:
    """The existing habit line `text` repeats, or ''."""
    new = _habit(text)
    for l in lines:
        r = difflib.SequenceMatcher(None, _habit(l), new).ratio()
        if r >= SAME or (eb and eb in l and r >= SAME_EB):
            return l
    return ""


def instincts_path(cfg: core.Config) -> Path | None:
    return Path(cfg.instincts_path).expanduser() if cfg.instincts_path else None


def errors_path(cfg: core.Config) -> Path | None:
    return Path(cfg.errors_path).expanduser() if cfg.errors_path else None


def read(cfg: core.Config) -> tuple[str, list[str], str]:
    """(full text, numbered habit lines, error). error is '' when the file was read or the feature is off."""
    p = instincts_path(cfg)
    if p is None:
        return "", [], ""
    try:
        text = p.read_text()
    except OSError as ex:
        return "", [], f"{p}: {type(ex).__name__}"
    return text, [l.rstrip() for l in text.splitlines() if LINE_RE.match(l)], ""


def inject_text(text: str, err: str, new: list[str] | None = None) -> str:
    if err:
        return f"[aisync] instincts file unreadable: {err}"
    if new is not None:
        return ("[aisync] NEW instinct(s) added to the habit list, follow them from now on:\n" + "\n".join(new)) if new else ""
    if not text.strip():
        return ""
    return ("[aisync] Instincts (habits grown from past mistakes; each line points at an EB id in the error bank; "
            "source: instincts.md; after each step add one with `aisync instinct add \"...\" --from EB-xxx`):\n"
            + text.strip())


def error_ids(cfg: core.Config) -> set[str] | None:
    """EB ids in the error log; None when no error log is configured (then any EB-nnn is accepted)."""
    p = errors_path(cfg)
    return None if p is None else set(HEAD_RE.findall(p.read_text()))


def add(cfg: core.Config, *, agent: str, text: str, eb: str) -> dict:
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("give the habit as one sentence")
    if not EB_RE.match(eb or ""):
        raise ValueError(f"--from must look like EB-023, got {eb!r}")
    try:
        known = error_ids(cfg)
    except OSError as ex:
        raise ValueError(f"cannot read the error bank {errors_path(cfg)}: {type(ex).__name__}") from ex
    if known is not None and eb not in known:
        raise ValueError(f"{eb} is not in {errors_path(cfg)}; record the mistake there first")
    p = instincts_path(cfg)
    if p is None:
        raise ValueError("no habit file configured: set instincts_path in config.toml")
    try:
        old = p.read_text()
    except OSError as ex:
        raise ValueError(f"cannot read {p}: {type(ex).__name__}") from ex
    if CONFLICT_RE.search(old):
        raise ValueError(f"{p} has unresolved git conflict markers; resolve them before adding a habit")
    dup = repeat_of([l for l in old.splitlines() if LINE_RE.match(l)], text, eb)
    if dup:
        raise ValueError(f"this habit is already on the list: {dup.strip()}; sharpen that line instead of adding a copy")
    same_eb = [l for l in old.splitlines() if LINE_RE.match(l) and (f"{eb}）" in l or f"({eb})" in l)]
    if len(same_eb) >= MAX_PER_EB:
        raise ValueError(f"{eb} already has {len(same_eb)} habits (at most {MAX_PER_EB}); sharpen one of them instead")
    nums = [int(m.group(1)) for l in old.splitlines() if (m := LINE_RE.match(l))]
    line = f"{max(nums, default=0) + 1}. {text}（{eb}）"
    core.atomic_write(p, old.rstrip("\n") + "\n" + line + "\n")
    ev = core.append_event(cfg, agent=agent, kind="note", project="instincts", files=[str(p)],
                           summary=f"instinct added: {line}", refs={"eb": eb})
    return {"line": line, "path": str(p), "event": ev["id"]}
