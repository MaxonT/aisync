"""What enforces each shared rule (a test beats an agent's judgment).

What: ~/.ai-sync/rules-enforcement.toml, keyed by rule ID, says for every rule either
        enforced_by = ["<path>", ...]   a test, hook or checker that catches a break of the rule
        judgment_only = "<reason>"      nothing in code checks it, and why
      or neither (= none). A none rule should still say what its check would be:
        planned = "<the check>"  size = "S|M|L"  since = "YYYY-MM-DD"
      An enforced rule may carry the same three keys for a gap its enforcer leaves. `note` says how far
      the enforcer reaches. It is a sidecar on purpose: the rules block is loaded into every session, this file is not.
Why:  a rule that only an agent's judgment keeps is weaker than one a test keeps; the count makes that visible.
      A none rule with no plan in this file had no way to close (16 sat for ten days, 2026-11-12): the plan lived
      in a markdown file nobody read, and no command could record an enforcer once someone wrote it.
How:  `aisync rule add` must name one of the three (collab.rule_add); AGENTS.md imports are recorded as none.
      `aisync rule enforce R-### ...` sets the entry of an existing rule. Paths are stored absolute (with ~), so
      the answer does not depend on the reader's directory.
      `aisync rule enforcement [--json]` counts active rules and prints one next action per open line; it exits 1
      when a Meta rule is none, a path is gone or relative, a none rule has no plan, a plan is past its due
      days (S 7, M 30, L 90), or no rule was read at all. A plan's clock only runs: moving it later needs a reason.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import date
from pathlib import Path

from . import collab, core, ruleids

FILE = "rules-enforcement.toml"
HEAD = """# What enforces each shared rule, keyed by rule ID (see ~/.ai-sync/preferences.md).
# enforced_by = paths of the test, hook or checker that catches a break of the rule (each must exist, absolute or ~);
# judgment_only = why only an agent's judgment keeps it; neither = none: a check is possible and not written yet.
# planned = what that check would be, size = S, M or L, since = the day it was planned (also allowed beside
# enforced_by, for a gap the enforcer leaves). note = how far the enforcer reaches.
# Written by `aisync rule add` and `aisync rule enforce`; counted by `aisync rule enforcement`.
"""
_META = re.compile(r"^- \[R-\d{3,}\] Meta rule\b")
SIZES = {"S": 7, "M": 30, "L": 90}          # days a planned check may stay unwritten before it counts as overdue
_KEYS = ("enforced_by", "judgment_only", "note", "planned", "size", "since")


def path(cfg: core.Config) -> Path:
    return cfg.root / FILE


def load(cfg: core.Config) -> dict:
    p = path(cfg)
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _num(rid: str) -> int:
    return int(rid.split("-")[1]) if re.fullmatch(r"R-\d+", rid) else 1 << 30


def save(cfg: core.Config, data: dict) -> None:
    """Whole-file rewrite, entries in ID order. Values are strings or lists of strings (JSON escapes are valid TOML)."""
    out = [HEAD]
    for rid in sorted(data, key=_num):
        out.append(f"[{rid}]")
        for k, v in data[rid].items():
            if isinstance(v, list):
                v = "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
            else:                               # a hand-edited bare date or number is written back as a string
                v = json.dumps(v if isinstance(v, str) else str(v), ensure_ascii=False)
            out.append(f"{k} = {v}")
        out.append("")
    core.atomic_write(path(cfg), "\n".join(out))


def _tilde(p: str) -> str:
    p, home = str(p), str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def _abs(p: str) -> str:
    """Absolute, ~ for home. abspath, not resolve: a hook path that is a symlink stays the path people know."""
    return _tilde(os.path.abspath(os.path.expanduser(str(p))))


def is_relative(p: str) -> bool:
    return not os.path.isabs(os.path.expanduser(str(p)))


def missing(paths: list[str]) -> list[str]:
    return [p for p in paths if not Path(p).expanduser().exists()]


def _paths(enforced_by: str) -> list[str]:
    paths = [p.strip() for p in (enforced_by or "").split(",") if p.strip()]
    gone = [p for p in paths if not Path(p).expanduser().is_file()]
    if gone:
        raise ValueError(f"--enforced-by path does not exist (or is not a file): {', '.join(gone)}")
    return [_abs(p) for p in paths]


def _plan(planned: str, size: str, since: str = "") -> dict:
    size = (size or "").strip().upper()
    if size not in SIZES:
        raise ValueError("--planned needs --size S, M or L (S under 30 min, M a few hours, L a day or more)")
    since = (since or "").strip() or date.today().isoformat()
    try:
        day = date.fromisoformat(since)
    except ValueError:
        raise ValueError(f"--since must be YYYY-MM-DD, got {since!r}") from None
    if day > date.today():
        raise ValueError(f"--since {since} is in the future; a plan starts the day it was made")
    return {"planned": " ".join(planned.split()), "size": size, "since": since}


def check_args(enforced_by: str, judgment_only: str, planned: str = "", size: str = "") -> dict:
    """The sidecar entry for a new rule, or ValueError unless exactly one of the three is given and it is valid."""
    reason = " ".join((judgment_only or "").split())
    plan = " ".join((planned or "").split())
    given = [bool((enforced_by or "").strip()), bool(reason), bool(plan)]
    if sum(given) != 1:
        raise ValueError("say what enforces the rule: --enforced-by <path>[,<path>] (a test, hook or checker), "
                         "--judgment-only \"<why nothing in code can check it>\", or "
                         "--planned \"<the check to write>\" --size S|M|L")
    if given[0]:
        return {"enforced_by": _paths(enforced_by)}
    if given[1]:
        return {"judgment_only": reason}
    return _plan(plan, size)


def kind(entry: dict) -> str:
    return "enforced" if entry.get("enforced_by") else "judgment_only" if entry.get("judgment_only") else "none"


def put(cfg: core.Config, rid: str, entry: dict) -> None:
    """Set one rule's entry. Call under core.locked."""
    data = load(cfg)
    data[rid] = entry
    save(cfg, data)


def active_ids(cfg: core.Config) -> list[str]:
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    return [rid for rid in (ruleids.bullet_id(l) for l in collab.rule_lines(block)) if rid]


def set_entry(cfg: core.Config, rid: str, *, enforced_by: str = "", judgment_only: str = "", planned: str = "",
              size: str = "", since: str = "", note: str = "", reason: str = "") -> dict:
    """Set the enforcement of an existing rule. Returns {"id", "before", "after", "entry"}.

    --enforced-by replaces the paths and closes the plan (pass --planned with it to keep a gap on record).
    --planned alone records the check to write. It also turns a judgment_only rule into a planned one (a check is
    possible after all) and drops enforcer files that are gone, so a dead enforcer becomes a plan.
    The clock of a plan only runs: a later --since or a larger --size on a rule that already has a plan needs
    --reason, and so does --judgment-only on such a rule (a possible check is not a judgment). The old plan and
    the reason stay in `note`.
    """
    rid = (rid or "").strip().upper()
    reason_j = " ".join((judgment_only or "").split())
    plan = " ".join((planned or "").split())
    note = " ".join((note or "").split())
    why = " ".join((reason or "").split())
    paths_in = (enforced_by or "").strip()
    if sum(map(bool, (paths_in, reason_j))) > 1 or (reason_j and plan):
        raise ValueError("give one of --enforced-by, --judgment-only, --planned (--planned may go with --enforced-by)")
    if not (paths_in or reason_j or plan or note):
        raise ValueError("nothing to set: give --enforced-by, --judgment-only, --planned (with --size) or --note")
    with core.locked(cfg):
        if rid not in active_ids(cfg):
            raise ValueError(f"{rid or '(no id)'} is not a current rule (see `aisync rule list`)")
        data = load(cfg)
        old = {k: (v if isinstance(v, (str, list)) else str(v)) for k, v in (data.get(rid) or {}).items()}
        new = {k: old[k] for k in _KEYS if k in old}
        was_plan = (f"({old.get('size', '?')}, since {old.get('since', '?')}): {old['planned']}" if old.get("planned") else "")
        if paths_in:
            new["enforced_by"] = _paths(paths_in)
            new.pop("judgment_only", None)
            if not plan:
                for k in ("planned", "size", "since"):
                    new.pop(k, None)
        elif reason_j:
            if was_plan and not why:
                raise ValueError(f"{rid} has a planned check {was_plan[:80]}; a possible check is not a judgment. "
                                 "Write the check, or pass --reason \"<why no check can exist after all>\"")
            new = {"judgment_only": reason_j}
            if was_plan:
                new["note"] = f"was planned {was_plan}. Dropped {date.today().isoformat()}: {why}"
            elif old.get("note") and not old.get("enforced_by"):
                new["note"] = old["note"]
        if plan:
            if new.get("judgment_only"):                     # a check is possible after all: no longer a judgment
                new.setdefault("note", f"was judgment_only: {new['judgment_only']}")
                del new["judgment_only"]
            if not paths_in and new.get("enforced_by"):      # a file that is gone enforces nothing
                alive = [p for p in new["enforced_by"] if not missing([p])]
                if alive:
                    new["enforced_by"] = alive
                else:
                    del new["enforced_by"]
            p = _plan(plan, size or old.get("size", ""), since or (old.get("since", "") if was_plan else ""))
            if was_plan:
                slower = (SIZES[p["size"]] > SIZES.get(old.get("size", ""), 0) or p["since"] > str(old.get("since", "")))
                if slower and not why:
                    raise ValueError(f"{rid} is already planned {was_plan[:80]}; a larger --size or a later --since "
                                     "moves its due day, so it needs --reason \"<why>\"")
                if slower:
                    new["note"] = (f"{new['note']} " if new.get("note") else "") + \
                        f"Plan moved {date.today().isoformat()} from {old.get('size', '?')} since {old.get('since', '?')}: {why}"
            new.update(p)
        if note:
            new["note"] = note if not new.get("note") or new.get("note") == old.get("note") else f"{note} {new['note']}"
        data[rid] = new
        save(cfg, data)
    return {"id": rid, "before": kind(old), "after": kind(new), "entry": new}


def rebase(cfg: core.Config, root: str) -> dict:
    """Make relative enforced_by paths absolute against `root`, for those that exist there. One-time repair of
    entries written when the path was stored as typed."""
    base = Path(root).expanduser()
    fixed, left = [], []
    with core.locked(cfg):
        data = load(cfg)
        for rid, e in data.items():
            new = []
            for p in e.get("enforced_by") or []:
                if is_relative(p) and (base / p).exists():
                    new.append(_abs(str(base / p)))
                    fixed.append({"id": rid, "path": p, "now": new[-1]})
                else:
                    if is_relative(p):
                        left.append({"id": rid, "path": p})
                    new.append(p)
            if e.get("enforced_by"):
                e["enforced_by"] = new
        if fixed:
            save(cfg, data)
    return {"fixed": fixed, "left": left}


def prune(cfg: core.Config) -> dict:
    """Drop entries of rules that are no longer current. Only an ID found in rules-archive.md goes (the rule is
    provably retired); with no current rule read at all it refuses, so a rules block that failed to parse cannot
    empty the registry. Returns {"removed": [...], "kept": [...]} (kept = not current, not in the archive)."""
    with core.locked(cfg):
        active = set(active_ids(cfg))
        if not active:
            raise ValueError("refusing to prune: no current rules were read from preferences.md")
        data = load(cfg)
        arch = cfg.root / ruleids.ARCHIVE
        archived = set(re.findall(r"\[(R-\d{3,})\]", arch.read_text())) if arch.exists() else set()
        gone = [rid for rid in data if rid not in active and rid in archived]
        kept = [rid for rid in data if rid not in active and rid not in archived]
        for rid in gone:
            del data[rid]
        if gone:
            save(cfg, data)
    return {"removed": sorted(gone, key=_num), "kept": sorted(kept, key=_num)}


def report(cfg: core.Config, today: date | None = None) -> dict:
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    data = load(cfg)
    today = today or date.today()
    rep = {"total": 0, "enforced": 0, "judgment_only": 0, "none": 0, "meta_none": [], "missing_paths": [],
           "meta": {}, "relative_paths": [], "planned": [], "overdue": [], "unplanned": []}
    seen = set()
    for line in collab.rule_lines(block):
        rid = ruleids.bullet_id(line)
        if not rid:
            continue
        seen.add(rid)
        e = data.get(rid, {})
        k = kind(e)
        rep["total"] += 1
        rep[k] += 1
        for p in e.get("enforced_by") or []:
            if is_relative(p):
                rep["relative_paths"].append({"id": rid, "path": p})
            elif missing([p]):
                rep["missing_paths"].append({"id": rid, "path": p})
        if e.get("planned"):
            try:
                days = (today - date.fromisoformat(str(e.get("since", "")))).days
            except ValueError:
                days = -1
            size = e.get("size") if e.get("size") in SIZES else "M"
            late = days < 0 or days > SIZES[size]        # no date, a bad date or a future one counts as overdue
            row = {"id": rid, "check": str(e["planned"]), "size": size, "since": str(e.get("since", "")),
                   "days": max(days, 0), "overdue": late, "kind": k}
            rep["planned"].append(row)
            if row["overdue"]:
                rep["overdue"].append(rid)
        elif k == "none":
            rep["unplanned"].append(rid)
        if _META.match(line):
            rep["meta"][rid] = k
            if k == "none":
                rep["meta_none"].append(rid)
    rep["stale"] = sorted((rid for rid in data if rid not in seen), key=_num)
    # a hub that does not require enforcers (the public default) is not failed for rules nobody labelled
    strict = bool(getattr(cfg, "require_enforcer", True))
    rep["ok"] = not (rep["meta_none"] or rep["missing_paths"] or rep["relative_paths"]
                     or (strict and (rep["unplanned"] or rep["overdue"] or not rep["total"])))
    return rep


def lines(rep: dict, registry: str = FILE) -> list[str]:
    """The report as text: counts, then one line per open item with its next action."""
    out = (["NO RULES READ: preferences.md has no rules block, or it is empty"] if not rep["total"] else [])
    out += [f"rules {rep['total']}: enforced {rep['enforced']}, judgment_only {rep['judgment_only']}, "
           f"none {rep['none']} (planned {sum(1 for p in rep['planned'] if p['kind'] == 'none')}, "
           f"no plan {len(rep['unplanned'])})",
           "meta  " + ", ".join(f"{k} {v}" for k, v in rep["meta"].items())]
    out += [f"META NONE {rid}: say what enforces it -> aisync rule enforce {rid} --enforced-by <path>"
            for rid in rep["meta_none"]]
    out += [f"MISSING {m['id']}: {m['path']} -> aisync rule enforce {m['id']} --enforced-by <a path that exists>, "
            f"or --planned \"<the check to rewrite>\" --size S|M|L"
            for m in rep["missing_paths"]]
    out += [f"RELATIVE {m['id']}: {m['path']} -> aisync rule enforcement --rebase <the repo it lives in>"
            for m in rep["relative_paths"]]
    for p in rep["planned"]:
        tag = "OVERDUE" if p["overdue"] else "PLANNED"
        gap = " (gap beside its enforcer)" if p["kind"] != "none" else ""
        out.append(f"{tag} {p['id']} ({p['size']}, since {p['since']}, {p['days']} d){gap}: {p['check']}"
                   + (f" -> write it, then aisync rule enforce {p['id']} --enforced-by <path>" if p["overdue"] else ""))
    out += [f"NO PLAN {rid} -> aisync rule enforce {rid} --planned \"<the check>\" --size S|M|L"
            for rid in rep["unplanned"]]
    if rep["stale"]:
        out.append(f"STALE {len(rep['stale'])} entries of retired rules -> aisync rule enforcement --prune")
    return out
