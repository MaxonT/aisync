"""What enforces each shared rule (a test beats an agent's judgment).

What: ~/.ai-sync/rules-enforcement.toml, keyed by rule ID, says for every rule either
        enforced_by = ["<path>", ...]   a test, hook or checker that catches a break of the rule
        judgment_only = "<reason>"      nothing in code checks it, and why
      or nothing (= none). An optional note says how far the enforcer reaches.
      It is a sidecar on purpose: the rules block is loaded into every session, this file is not.
Why:  a rule that only an agent's judgment keeps is weaker than one a test keeps; the count makes that visible.
How:  `aisync rule add` must name one of the two (collab.rule_add); AGENTS.md imports are recorded as none.
      `aisync rule enforcement [--json]` counts active rules and exits 1 when a Meta rule is none or a path is gone.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from . import collab, core, ruleids

FILE = "rules-enforcement.toml"
HEAD = """# What enforces each shared rule, keyed by rule ID (see ~/.ai-sync/preferences.md).
# enforced_by = paths of the test, hook or checker that catches a break of the rule (each must exist);
# judgment_only = why only an agent's judgment keeps it; no entry or `none` = nobody has said yet.
# note = how far the enforcer reaches. Written by `aisync rule add`; counted by `aisync rule enforcement`.
"""
_META = re.compile(r"^- \[R-\d{3,}\] Meta rule\b")


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
            else:
                v = json.dumps(v, ensure_ascii=False)
            out.append(f"{k} = {v}")
        out.append("")
    core.atomic_write(path(cfg), "\n".join(out))


def _tilde(p: str) -> str:
    return str(p).replace(str(Path.home()), "~", 1)


def missing(paths: list[str]) -> list[str]:
    return [p for p in paths if not Path(p).expanduser().exists()]


def check_args(enforced_by: str, judgment_only: str) -> dict:
    """The sidecar entry for a new rule, or ValueError when neither or both are given or a path is missing."""
    paths = [p.strip() for p in (enforced_by or "").split(",") if p.strip()]
    reason = " ".join((judgment_only or "").split())
    if bool(paths) == bool(reason):
        raise ValueError("say what enforces the rule: --enforced-by <path>[,<path>] (a test, hook or checker) "
                         "or --judgment-only \"<why nothing in code can check it>\"")
    if paths:
        gone = missing(paths)
        if gone:
            raise ValueError(f"--enforced-by path does not exist: {', '.join(gone)}")
        return {"enforced_by": [_tilde(str(Path(p).expanduser())) for p in paths]}
    return {"judgment_only": reason}


def put(cfg: core.Config, rid: str, entry: dict) -> None:
    """Set one rule's entry. Call under core.locked."""
    data = load(cfg)
    data[rid] = entry
    save(cfg, data)


def report(cfg: core.Config) -> dict:
    block = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
    data = load(cfg)
    rep = {"total": 0, "enforced": 0, "judgment_only": 0, "none": 0, "meta_none": [], "missing_paths": [],
           "meta": {}}
    for line in collab.rule_lines(block):
        rid = ruleids.bullet_id(line)
        if not rid:
            continue
        e = data.get(rid, {})
        kind = "enforced" if e.get("enforced_by") else "judgment_only" if e.get("judgment_only") else "none"
        rep["total"] += 1
        rep[kind] += 1
        rep["missing_paths"] += [{"id": rid, "path": p} for p in missing(e.get("enforced_by") or [])]
        if _META.match(line):
            rep["meta"][rid] = kind
            if kind == "none":
                rep["meta_none"].append(rid)
    rep["ok"] = not rep["meta_none"] and not rep["missing_paths"]
    return rep
