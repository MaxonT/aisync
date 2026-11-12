"""tidy: the clerk that keeps the shared rules small. It proposes; the owner ticks; nothing changes by itself.

What: one report with four lists: rules that say the same thing (When and Do both close), rules nobody cited in
      `unused_days`, the core layer over its budget, and rules out of shape or with no enforcer on record
      (each with its planned check, or `no plan`), plus enforcer files that are gone.
Why:  rules only grow (221 by 2026-11-08); a slim by hand happens once, a clerk keeps it small (owner 2026-11-08).
How:  pure reads, no model, no network. Citations come from the journal plus files matched by `tidy_usage_globs`
      (default: Claude Code transcripts). The report goes to <hub>/tidy/<date>.md with checkboxes, written once a day
      by the watch cycle (R-045: a daily report with to-dos, even when empty); `aisync tidy` prints it now.
"""
from __future__ import annotations

import difflib
import glob
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import core, rulespec

SAME = 0.72              # When and Do both at least this close: a likely duplicate
UNUSED_DAYS = 30
CORE_BUDGET_CHARS = 26000  # the core layer's rule text; about 7,000 tokens at the 2026-11-08 mix
DEFAULT_GLOBS = ["~/.claude/projects/*/*.jsonl"]
_RID = re.compile(r"\bR-\d{3,}\b")
BULK = 40                # a file naming more distinct rule IDs than this is rule maintenance, not use


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[`'\"()（）,，.;；:：]", " ", s.lower()).split())


def duplicates(rules: list[str]) -> list[dict]:
    specs = [(r, rulespec.parse(r)) for r in rules]
    specs = [(r, s) for r, s in specs if s]
    out = []
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            a, b = specs[i][1], specs[j][1]
            w = difflib.SequenceMatcher(None, _norm(a.when), _norm(b.when)).ratio()
            if w < SAME:
                continue
            d = difflib.SequenceMatcher(None, _norm(a.do), _norm(b.do)).ratio()
            if d >= SAME:
                out.append({"a": a.id, "b": b.id, "when": round(w, 2), "do": round(d, 2)})
    return out


def citations(cfg: core.Config, days: int, globs: list[str] | None = None) -> dict[str, int]:
    """Rule ID -> times cited in the journal and the usage files of the last `days` days."""
    since = time.time() - days * 86400
    cut = datetime.fromtimestamp(since).astimezone().isoformat()
    n: dict[str, int] = {}
    for e in core.read_events(cfg):
        if e.get("ts", "") >= cut:
            for rid in _RID.findall(e.get("summary", "")):
                n[rid] = n.get(rid, 0) + 1
    for g in globs if globs is not None else cfg.tidy_usage_globs:
        for f in glob.glob(os.path.expanduser(g)):
            try:
                if os.path.getmtime(f) < since:
                    continue
                counts: dict[str, int] = {}
                with open(f, errors="ignore") as fh:      # line by line: one transcript can be hundreds of MB
                    for line in fh:
                        for rid in _RID.findall(line):
                            counts[rid] = counts.get(rid, 0) + 1
                if len(counts) > BULK:              # a slim or rewrite session cites every rule: not evidence of use
                    continue
                for rid, k in counts.items():
                    n[rid] = n.get(rid, 0) + k
            except OSError:
                continue
    return n


def report(cfg: core.Config, globs: list[str] | None = None, days: int = UNUSED_DAYS) -> dict:
    from . import collab, enforcement, layers
    truth = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    rules = collab.rule_lines(truth)
    ids = [rulespec.split(r)[0] for r in rules]
    cited = citations(cfg, days, globs)
    unused = [rid for rid in ids if rid and not cited.get(rid)]
    core_chars = 0
    if cfg.core_topics:
        _, secs, _ = layers.sections(truth)
        core_chars = sum(len(l) for h, body in secs if layers.is_core(layers.topic(h), cfg.core_topics)
                         for l in body if l.startswith("- "))
    budget = int(cfg.core_budget_chars or CORE_BUDGET_CHARS)
    enf = enforcement.report(cfg)           # one reading of the registry for both commands
    plans = {p["id"]: p for p in enf["planned"] if p["kind"] == "none"}
    no_enforcer = [rid for rid in ids if rid and (rid in plans or rid in enf["unplanned"])]
    lint = rulespec.lint(rules)
    return {"rules": len(rules), "duplicates": duplicates(rules), "unused": unused, "unused_days": days,
            "core_chars": core_chars, "core_budget": budget, "over_budget": bool(cfg.core_topics) and core_chars > budget,
            "out_of_shape": [b["id"] for b in lint["bad"]], "no_enforcer": no_enforcer, "plans": plans,
            "enforcer_gone": enf["missing_paths"] + enf["relative_paths"]}


def render(rep: dict, day: str) -> str:
    lines = [f"# Rules tidy {day}", "",
             f"Checked {rep['rules']} rules. Proposals only: tick a box, then apply it with `aisync rule rewrite` "
             "or `aisync rule add --replaces` (old text goes to rules-archive.md).", ""]
    lines += [f"## Likely duplicates ({len(rep['duplicates'])})"]
    lines += [f"- [ ] merge {d['a']} and {d['b']} (When {d['when']}, Do {d['do']})" for d in rep["duplicates"]] or ["- none"]
    lines += ["", f"## Not cited in {rep['unused_days']} days ({len(rep['unused'])})",
              "Candidates to move to a folded topic or retire; a rule that guards a rare event can stay."]
    lines += [f"- [ ] {rid}" for rid in rep["unused"]] or ["- none"]
    lines += ["", "## Core layer budget"]
    if rep["core_budget"] and rep["core_chars"]:
        mark = "OVER" if rep["over_budget"] else "ok"
        lines.append(f"- {mark}: {rep['core_chars']} of {rep['core_budget']} characters"
                     + (" -> [ ] fold or merge core rules" if rep["over_budget"] else ""))
    else:
        lines.append("- no layered target")
    lines += ["", f"## Out of the When/Do/Not shape ({len(rep['out_of_shape'])})"]
    lines += [f"- [ ] {rid or '(no id)'}" for rid in rep["out_of_shape"]] or ["- none"]
    plans = rep.get("plans", {})
    lines += ["", f"## No enforcer on record ({len(rep['no_enforcer'])})",
              "A check is possible and not written. Write it, then `aisync rule enforce R-### --enforced-by <path>`; "
              "a rule with no plan first gets `aisync rule enforce R-### --planned \"<the check>\" --size S|M|L`."]
    for rid in rep["no_enforcer"]:
        p = plans.get(rid)
        lines.append(f"- [ ] {rid} planned {p['size']} since {p['since']}{' (OVERDUE)' if p['overdue'] else ''}: {p['check']}"
                     if p else f"- [ ] {rid} no plan")
    if not rep["no_enforcer"]:
        lines.append("- none")
    gone = rep.get("enforcer_gone", [])
    lines += ["", f"## Enforcer files gone ({len(gone)})",
              "The registry names a file that is not there. `aisync rule enforcement` prints the fix for each."]
    lines += [f"- [ ] {g['id']}: {g['path']}" for g in gone] or ["- none"]
    return "\n".join(lines) + "\n"


def daily(cfg: core.Config, globs: list[str] | None = None) -> Path | None:
    """Write today's report once (the watch cycle calls this); None when it already exists."""
    day = f"{datetime.now().astimezone():%Y-%m-%d}"
    p = cfg.root / "tidy" / f"{day}.md"
    if p.exists():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write(p, render(report(cfg, globs), day))
    return p
