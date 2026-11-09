"""Non-vacuous checkers: a checker that checked nothing must fail, never pass.

Once a sync checker read retired rules from a section that had just moved into rules-archive.md, found 0
and said ok. `aisync rulecheck` and `aisync status` now count what they compared and call guard(), which returns
problems when a count is 0, or fell by more than half since the last recorded run. A deliberate drop is accepted once
with a reason that is logged (`--accept-drop "<reason>"`, or `python3 -m ai_sync.nonvacuous accept <checker> --reason R`).
A zero is never accepted.

State: <hub>/state/checker_counts.json and checker_drops.jsonl (the hub's state/ is git-ignored);
AISYNC_CHECK_COUNTS points the counts file elsewhere.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

def state_file(home: Path | None = None) -> Path:
    if os.environ.get("AISYNC_CHECK_COUNTS"):
        return Path(os.environ["AISYNC_CHECK_COUNTS"])
    home = home or Path(os.environ.get("AISYNC_HOME", "~/.ai-sync")).expanduser()
    return home / "state" / "checker_counts.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")   # temp + rename: a crash never leaves half a file
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def summary(counts: dict[str, int]) -> str:
    """One line for the checker to print, e.g. checked: rules 120, archive 14."""
    return "checked: " + ", ".join(f"{k} {v}" for k, v in counts.items())


def guard(checker: str, counts: dict[str, int], accept_drop: str | None = None, state: Path | None = None) -> list[str]:
    """Problems (one per bad count). Good counts are remembered; a refused drop keeps the old baseline,
    so the checker keeps failing until the drop is fixed or accepted."""
    path = state or state_file()
    data = _load(path)
    last = data.get("counts", {}).get(checker, {})
    pending = data.get("accept", {}).get(checker)
    reason = accept_drop or (pending or {}).get("reason")
    probs, drops = [], []
    for name, n in counts.items():
        if n <= 0:
            probs.append(f"{checker}: {name} checked nothing (0), so the check proved nothing; what it checks may have moved or be read from the wrong place")
            continue
        before = last.get(name)
        if isinstance(before, int) and n * 2 < before:
            drops.append((name, before, n))
    for name, before, n in drops:
        if not reason:
            probs.append(f"{checker}: {name} checked only {n}, last time {before}: more than half fewer; "
                         f"if that is on purpose pass --accept-drop \"why\", or run python3 -m ai_sync.nonvacuous accept {checker} --reason \"why\"")
    if probs:
        return probs
    if drops:
        with path.with_name("checker_drops.jsonl").open("a", encoding="utf-8") as f:
            for name, before, n in drops:
                f.write(json.dumps({"ts": _now(), "checker": checker, "count": name, "from": before, "to": n,
                                    "reason": reason}, ensure_ascii=False) + "\n")
    data.setdefault("counts", {})[checker] = {**{k: v for k, v in last.items() if k not in counts and k != "_at"},
                                              **counts, "_at": _now()}
    if pending:
        data["accept"].pop(checker, None)
    _save(path, data)
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Non-vacuous checker counts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    acc = sub.add_parser("accept", help="accept the next drop of one checker, with a reason")
    acc.add_argument("checker")
    acc.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    path = state_file()
    data = _load(path)
    if a.cmd == "show":
        print(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    if not a.reason.strip():
        print("the reason must not be empty")
        return 2
    data.setdefault("accept", {})[a.checker] = {"reason": a.reason.strip(), "ts": _now()}
    _save(path, data)
    print(f"{a.checker}: the next drop is accepted (reason: {a.reason.strip()}) and logged in {path.with_name('checker_drops.jsonl')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
