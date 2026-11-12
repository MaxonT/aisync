"""rulespec: every shared rule is an interface, not an essay (owner decision 2026-11-08).

A rule bullet has a fixed shape:

    - [R-333] When: any deploy step. Do: the AI runs it step by step and checks after each. Not: a background run. (2026-11-02 · claude-code, ops-jobs)

An optional leading `Meta rule.` marks the few rules that rank above topic rules (R-005;
enforcement.py counts them by that prefix). `When:` (the trigger) and `Do:` (the action) are required, `Not:` (what it forbids) is optional, in that
order. The body (between the ID and the source tag) stays under MAX_BODY characters: why, history and full
quotes go to the journal (`--reason`) and the archive, and the source tag points there. What enforces a rule
lives in rules-enforcement.toml, not in the bullet. With `rule_spec = true` in config.toml, `aisync rule add`
refuses a rule in any other shape; `aisync rule lint` lists every rule that is not in this shape yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_BODY = 420          # characters between the ID and the source tag; 360 forced meaning loss on merged rules (2026-11-08)

_ID = re.compile(r"^\[(R-\d{3,})\]\s*")
_TAG = re.compile(r"\s*(\((?:\d{4}-\d{2}-\d{2}|owner)[^()]*(?:\([^()]*\)[^()]*)*\))\s*$")
_META = re.compile(r"^Meta rule[.:]?\s+")
_FIELD = re.compile(r"(?:(?<=\s)|^)(When|Do|Not):\s*")
_SELF_RANK = re.compile(r"highest|top priority|最高优先级", re.I)   # R-358: a rule does not rank itself
_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"|「[^」]*」|‘[^’]*’|'[^']*'")


@dataclass
class Spec:
    id: str
    when: str
    do: str
    not_: str
    tag: str
    meta: bool = False


def split(rule: str) -> tuple[str, str, str]:
    """(id, body, tag) of a rule line without its leading '- '."""
    rule = rule.strip()
    if rule.startswith("- "):
        rule = rule[2:]
    m = _ID.match(rule)
    rid = m.group(1) if m else ""
    rest = rule[m.end():] if m else rule
    t = _TAG.search(rest)
    tag = t.group(1) if t else ""
    body = rest[:t.start()] if t else rest
    return rid, body.strip(), tag


def fields(body: str) -> dict[str, str] | None:
    """{'When': .., 'Do': .., 'Not': ..} when the labels appear once each, in order; else None."""
    body = _META.sub("", body, count=1)
    hits = list(_FIELD.finditer(body))
    names = [h.group(1) for h in hits]
    if names not in (["When", "Do"], ["When", "Do", "Not"]) or hits[0].start() != 0:
        return None
    out = {}
    for i, h in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        out[h.group(1)] = body[h.end():end].strip()
    return out


def parse(rule: str) -> Spec | None:
    rid, body, tag = split(rule)
    f = fields(body)
    if f is None:
        return None
    return Spec(rid, f["When"], f["Do"], f.get("Not", ""), tag, bool(_META.match(body)))


def problems(rule: str, need_id: bool = True, need_tag: bool = True) -> list[str]:
    """What keeps a rule from the interface shape; [] when it fits."""
    rid, body, tag = split(rule)
    out = []
    if need_id and not rid:
        out.append("no [R-###] id")
    f = fields(body)
    if f is None:
        out.append("not in the shape 'When: ... Do: ... [Not: ...]'")
    else:
        for k in ("When", "Do"):
            if not f[k].strip(" .。;；"):
                out.append(f"{k}: is empty")
    if _SELF_RANK.search(_QUOTED.sub(" ", body)):
        out.append("calls itself highest / TOP PRIORITY / 最高优先级 (R-358): say what it does; quoted words do not count")
    if len(body) > MAX_BODY:
        out.append(f"body is {len(body)} characters, over {MAX_BODY}: keep what to do, move the why to --reason")
    if need_tag and not tag:
        out.append("no source tag '(YYYY-MM-DD · agent, project)'")
    return out


def compose(when: str, do: str, not_: str = "") -> str:
    """Rule text from the three fields, each ending in one full stop."""
    def end(s: str) -> str:
        s = " ".join(s.split()).rstrip(" .。;；")
        return s + "."
    if not when.strip() or not do.strip():
        raise ValueError("--when and --do are both required")
    text = f"When: {end(when)} Do: {end(do)}"
    return text + (f" Not: {end(not_)}" if not_.strip() else "")


def lint(lines: list[str]) -> dict:
    """Count rules in and out of the shape; `bad` lists each one that is out, with its problems."""
    bad = []
    for r in lines:
        p = problems(r)
        if p:
            bad.append({"id": split(r)[0], "problems": p, "rule": r[:100]})
    return {"rules": len(lines), "fit": len(lines) - len(bad), "bad": bad}


def rewrite(cfg, bodies: dict[str, str], agent: str, why: str = "interface rewrite") -> dict:
    """Swap the body of each rule ID in `bodies` for its new interface text, keeping the ID and the source tag.

    The old bullet goes to the top of rules-archive.md with 被 <same id> 取代. Stricter than rulecheck (which
    accepts any fact still in the archive, and the archive holds the old text): every hard fact of the old rule
    (rulecheck.facts) must be in its new text, unless the value is {"text": ..., "drop": {fact: reason}} and
    names it, so history (when the owner said it) can go but a path, number or command cannot slip away.
    Nothing is written when a new body is not in the interface shape, an ID is unknown or a fact is lost.
    Returns {"rewritten": [...], "problems": {...}, "missing": [...], "dropped": {...}}.
    """
    from datetime import datetime
    from . import core, rulecheck, ruleids
    p = cfg.root / "preferences.md"
    day = f"{datetime.now().astimezone():%Y-%m-%d}"
    with core.locked(cfg):
        before = p.read_text()
        lines = before.splitlines()
        where = {split(l)[0]: i for i, l in enumerate(lines) if l.startswith("- [R-")}
        probs, drops = {}, {}
        norm = {}
        for rid, v in bodies.items():
            if isinstance(v, dict):
                norm[rid] = str(v.get("text", ""))
                drops[rid] = {str(k): str(r) for k, r in (v.get("drop") or {}).items()}
            else:
                norm[rid] = str(v)
        bodies = norm
        for rid, body in bodies.items():
            if rid not in where:
                probs[rid] = ["unknown rule id"]
                continue
            bad = problems(body, need_id=False, need_tag=False)
            if bad:
                probs[rid] = bad
        if probs:
            return {"rewritten": [], "problems": probs, "missing": [], "dropped": {}}
        missing = []
        for rid, body in bodies.items():
            old = lines[where[rid]]
            for f in sorted(rulecheck.facts(split(old)[1])):     # the body only: the source tag is kept as is
                if f not in body and f not in drops.get(rid, {}):
                    missing.append({"rule": old[:120], "fact": f})
        if missing:
            return {"rewritten": [], "problems": {}, "missing": missing, "dropped": {}}
        archived, done = [], []
        for rid, body in bodies.items():
            i = where[rid]
            old = lines[i]
            tag = split(old)[2]
            new = f"- [{rid}] {' '.join(body.split())} {tag}".rstrip()
            if new == old:
                continue
            lines[i] = new
            archived.append(ruleids.archive_line(old, day, rid, f" 被 {rid}「{body[:24]}」取代（{day}，{why}）"))
            done.append(rid)
        after = "\n".join(lines) + ("\n" if before.endswith("\n") else "")
        arch = ruleids.archive_path(cfg)
        arch_text = "\n".join(archived) + "\n" + (arch.read_text() if arch.exists() else "")
        missing = rulecheck.check(before, after, arch_text)
        if missing:
            return {"rewritten": [], "problems": {}, "missing": missing, "dropped": {}}
        if done:
            ruleids.archive_add(cfg, archived)
            core.atomic_write(p, after)
    if done:
        core.append_event(cfg, agent=agent, kind="rule_change", project="ai-sync",
                          summary=f"{why}: {len(done)} rule(s) rewritten as When/Do/Not ({', '.join(done[:12])}"
                                  + (" …" if len(done) > 12 else "") + "); old text in rules-archive.md",
                          files=["preferences.md", ruleids.ARCHIVE])
        for rid, d in drops.items():
            if d and rid in done:
                core.append_event(cfg, agent=agent, kind="note", project="ai-sync",
                                  summary=f"{rid} interface rewrite dropped: " + "; ".join(f"{k} ({r})" for k, r in d.items()))
    return {"rewritten": done, "problems": {}, "missing": [], "dropped": {k: v for k, v in drops.items() if v}}
