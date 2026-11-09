"""layers: the desk and the shelf. A layered target loads only the core topics; the rest are fetched on demand.

What: a [[target]] with `layer = "core"` gets the rules block with the topics named in `core_topics` in full and
      every other topic folded to one line that says how to get it. The prompt hook (`aisync hook`) injects a
      folded topic the first time a prompt or a tool call in that session touches one of its keywords.
Why:  2026-11-08: the always-loaded rules were 20,673 tokens, two thirds of it about one area at a time
      (deploys, docs, posts, scheduled jobs); a long block also dilutes attention on the rules that matter.
How:  `core_topics` and `[shelf]` (topic prefix -> keywords) live in config.toml. A folded copy that someone
      edits is merged back with the truth's folded sections first, so adopting the edit never deletes rules.
"""
from __future__ import annotations

import re

from . import core

FOLD = "_Shelved: {n} rules, injected when your work touches {kw}. Read them now: `aisync rule list --topic \"{topic}\"`._"


def sections(block: str) -> tuple[list[str], list[tuple[str, list[str]]], list[str]]:
    """(head lines, [(### heading, body lines)], tail lines) of a rules block."""
    lines = block.split("\n")
    head, secs, tail = [], [], []
    cur = None
    for l in lines:
        if l.startswith(core.BLOCK_END):
            tail.append(l)
            cur = None
            continue
        if tail:
            tail.append(l)
        elif l.startswith("### "):
            cur = (l, [])
            secs.append(cur)
        elif cur is None:
            head.append(l)
        else:
            cur[1].append(l)
    return head, secs, tail


def topic(heading: str) -> str:
    return heading[4:].strip()


def is_core(name: str, core_topics: list[str]) -> bool:
    return any(name.startswith(c) for c in core_topics)


def keywords(name: str, shelf: dict[str, list[str]]) -> list[str]:
    """Configured keywords for a topic, else the words of its heading (split on punctuation)."""
    for prefix, kws in shelf.items():
        if name.startswith(prefix):
            return [k for k in kws if k]
    return [w for w in re.split(r"[\s、，,（）()/与和]+", name) if len(w) >= 2]


def core_view(truth: str, core_topics: list[str], shelf: dict[str, list[str]]) -> str:
    """The block a layered target carries: core topics in full, every other topic folded to one line."""
    head, secs, tail = sections(truth)
    out = list(head)
    for h, body in secs:
        name = topic(h)
        out.append(h)
        if is_core(name, core_topics):
            out += body
        else:
            n = sum(1 for l in body if l.startswith("- "))
            kw = ", ".join(keywords(name, shelf)[:8]) or name
            out += [FOLD.format(n=n, kw=kw, topic=name.split("（")[0]), ""]
    return "\n".join(out + tail)


def merge_back(copy: str, truth: str, core_topics: list[str]) -> str:
    """An edited layered copy as a full block: its own core sections, the truth's folded ones."""
    _, tsecs, _ = sections(truth)
    full = {topic(h): body for h, body in tsecs}
    head, secs, tail = sections(copy)
    out = list(head)
    for h, body in secs:
        name = topic(h)
        out.append(h)
        out += body if is_core(name, core_topics) else full.get(name, body)
    return "\n".join(out + tail)


def topic_rules(truth: str, prefix: str) -> list[str]:
    """The rule lines of the topics whose heading starts with prefix."""
    _, secs, _ = sections(truth)
    return [l for h, body in secs if topic(h).startswith(prefix) for l in body if l.startswith("- ")]


def matches(text: str, truth: str, core_topics: list[str], shelf: dict[str, list[str]]) -> list[str]:
    """Folded topics whose keywords appear in text (case-insensitive), in block order."""
    low = (text or "").lower()
    if not low:
        return []
    _, secs, _ = sections(truth)
    hit = []
    for h, _ in secs:
        name = topic(h)
        if is_core(name, core_topics):
            continue
        if any(_has(low, k.lower()) for k in keywords(name, shelf)):
            hit.append(name)
    return hit


def _has(low: str, kw: str) -> bool:
    """ASCII keywords match whole words ('pr' is not in 'prompt'); others (Chinese) match as substrings."""
    if kw.isascii() and re.match(r"^[a-z0-9]", kw):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low) is not None
    return kw in low
