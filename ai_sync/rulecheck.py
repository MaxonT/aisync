"""rulecheck: prove a tidy of the shared rules lost no hard fact.

A tidy may regroup, merge and shorten rules, but every hard fact of every old rule
(path, command, number with a unit, clock time, E/T/KPI-AMD id, URL, quoted words)
must still be in the current rules, or in the superseded list with the rule that
replaced it. One missing fact fails the check.

Superseded entries live under a heading that contains 「已取代」 (or "Superseded"), or, since
2026-11-02, in rules-archive.md beside the rules file; each entry must name its replacement with
「被 … 取代」 or "superseded by". A rule ID ([R-087]) of the old rules must still be on a current
rule or in the archive: IDs never disappear.

Non-vacuous: run() prints how many old rules, current rules and retired entries it read and fails
when one is 0 or fell by more than half since the last run (ai_sync/nonvacuous.py); 0 retired entries means the
retired rules moved and this check would compare against nothing. --accept-drop "<reason>" accepts a deliberate drop.
"""
from __future__ import annotations

import re
from pathlib import Path

START = "<!-- owner-preferences:start"
END = "<!-- owner-preferences:end -->"

# Real aisync subcommands: "aisync launch" in prose ("the aisync launch") is not a command.
_SUBCMDS = ("init|log|rule|claim|release|claims|report|status|tail|news|hook|install-hooks"
            "|sync|watch|verify|resolve|rulecheck|resume|demo|board|mcp|tidy|instinct")

_FACTS = [
    re.compile(r"`([^`\n]{2,})`"),                              # code and commands in backticks
    re.compile(r"(?<![\w])(~?/[A-Za-z0-9_.\-@]+(?:/[A-Za-z0-9_.\-@]+)+/?|~/[A-Za-z0-9_.\-@]+)"),  # paths: ~/x or two+ ASCII segments
    re.compile(r"\b([\w.\-]+\.(?:py|md|json|toml|ts|jsonl|yaml|yml|plist|sh|svg|png|pdf))\b"),
    re.compile(r"\b(E\d{3})\b"),                                 # correction ids
    re.compile(r"\b(EB-\d{3})\b"),
    re.compile(r"\b(T-\d{8}-[\w-]+?)(?=[\s，。,;；)）]|$)"),       # ticket ids
    re.compile(r"\b(KPI-AMD-\d{3}|XP-AMD-\d{3})\b"),
    re.compile(r"(https?://[^\s)）>]+)"),
    re.compile(r"\b(\d{1,2}:\d{2})\b"),                          # clock times
    re.compile(r"\b(\d+(?:\.\d+)?\s?(?:min|minutes|h|hours|days|MB|GB|%|分钟|小时|天|周|个|条|次|题|封|秒|s))(?![\w])"),
    re.compile(r"(aisync (?:" + _SUBCMDS + r")(?: [a-z][\w-]*)?)\b"),  # aisync subcommands (real ones only)
    re.compile(r"(?<![\w/.~-])([a-z][a-z0-9]*(?:[_-][a-z0-9]+)+)(?![\w/-])"),  # job, field and tool names
]

# "CORRECTION to the 2026-10-15 09:04 github1 naming rule", "Supersedes the 2026-10-23 11:44 borrow-first rule":
# pointers to another rule, which carries its own facts; a tidy drops this history narration.
_XREF = re.compile(r"(?i)\b(?:correction to|follow-up to|adds to|exception to|supersedes)\s+the\s+[^.;]{0,120}?\b(?:rule|entry)\b"
                   r"|\bthe\s+(?:\d{4}-\d{2}-\d{2}\s+)?\d{1,2}:\d{2}\b[^.;]{0,80}?\b(?:rule|entry)\b")

_RID = re.compile(r"^\[(R-\d{3,})\]\s*")
ARCHIVE = "rules-archive.md"

_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?(?: [A-Z]{2,4})? \([^)]*\):\s*")


def body(rule: str) -> str:
    """The rule itself: without the 'date time TZ (agent, project):' header and the trailing Reason."""
    rule = _XREF.sub("", _HEAD.sub("", _RID.sub("", rule)))
    i = rule.rfind(" Reason:")
    return rule[:i] if i != -1 else rule


def block(text: str) -> str:
    """The owner-preferences block if markers exist, else the whole text."""
    i, j = text.find(START), text.find(END)
    return text[i:j] if i != -1 and j != -1 else text


def rules(text: str) -> list[str]:
    """Top-level bullet rules of the block (continuation lines joined)."""
    out: list[str] = []
    for line in block(text).splitlines():
        if line.startswith("- "):
            out.append(line[2:].strip())
        elif out and line.startswith("  ") and line.strip():
            out[-1] += " " + line.strip()
    return out


def facts(rule: str) -> set[str]:
    found: set[str] = set()
    rule = body(rule)
    for rx in _FACTS:
        for m in rx.finditer(rule):
            f = m.group(1).strip().rstrip(".,;:。，；")
            if len(f) >= 2:
                found.add(f)
    return found


def split_superseded(text: str) -> tuple[str, list[str]]:
    """(current rules text, superseded entries)."""
    lines = block(text).splitlines()
    cur: list[str] = []
    sup: list[str] = []
    in_sup = False
    for line in lines:
        if line.startswith("#"):
            in_sup = ("已取代" in line) or ("superseded" in line.lower())
            if in_sup:
                continue
        (sup if in_sup else cur).append(line)
    entries = rules("\n".join(sup))
    return "\n".join(cur), entries


def _names_replacement(entry: str) -> bool:
    return bool(re.search(r"被.+取代|superseded by", entry, re.I))


def check(before: str, after: str, archive: str = "") -> list[dict]:
    """Missing facts: [{rule, fact}] for every hard fact of `before` that `after` (plus the archive) lost."""
    current, sup = split_superseded(after)
    sup += rules(archive)
    sup_ok = [e for e in sup if _names_replacement(e)]
    kept_ids = {m.group(1) for r in rules(current) + sup if (m := _RID.match(r))}
    missing = []
    for r in rules(before):
        m = _RID.match(r)
        if m and m.group(1) not in kept_ids:
            missing.append({"rule": r[:120], "fact": m.group(1)})
        for f in sorted(facts(r)):
            if f in current:
                continue
            if any(f in e for e in sup_ok):
                continue
            missing.append({"rule": r[:120], "fact": f})
    return missing


def counted(before: str, after: str, archive: str = "") -> dict[str, int]:
    """What check() really compared: old rules, current rules, retired entries (superseded section plus archive)."""
    current, sup = split_superseded(after)
    return {"old rules": len(rules(before)), "current rules": len(rules(current)), "retired entries": len(sup) + len(rules(archive))}


def run(before_path: str, after_path: str, archive_path: str | None = None, accept_drop: str | None = None) -> int:
    """archive_path defaults to rules-archive.md next to the after file, when it exists."""
    from . import nonvacuous
    after_p = Path(after_path).expanduser()
    arch = Path(archive_path).expanduser() if archive_path else after_p.parent / ARCHIVE
    before = Path(before_path).expanduser().read_text(encoding="utf-8")
    after = after_p.read_text(encoding="utf-8")
    archive = arch.read_text(encoding="utf-8") if arch.exists() else ""
    miss = check(before, after, archive)
    for m in miss:
        print(f"MISSING {m['fact']!r}  ← {m['rule']}")
    counts = counted(before, after, archive)
    vacuous = nonvacuous.guard("rulecheck", counts, accept_drop, nonvacuous.state_file(after_p.parent))
    for v in vacuous:
        print(f"VACUOUS {v}")
    print(f"rulecheck: {counts['old rules']} old rules, {len(miss)} missing facts -> {'FAIL' if miss or vacuous else 'OK'}")
    print(nonvacuous.summary(counts))
    return 1 if miss or vacuous else 0
