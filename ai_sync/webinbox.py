"""web-inbox importer: claude.ai (web) Claude events -> the local journal.

What: the web Claude cannot run `aisync log`, so it appends JSON lines to `events.jsonl` on the
      `web-inbox` branch of the hub's GitHub repo. This module fetches that branch and logs every
      line whose `web_id` was not imported yet, as the web agent the line names (`claude-web` or
      `chatgpt`; anything else falls back to `claude-web` with refs.web_agent kept), with the original
      project / kind / summary (same validation and hash chain as `aisync log`). A kind ai-sync
      does not know (e.g. "milestone") is logged as `note` with refs.web_kind, never dropped.
Why:  web-Claude work shows up in ai-sync within ~30 s instead of going through the mailbox.
How:  `git fetch` into a private ref (refs/aisync/web-inbox) so main, the working tree and
      origin/* are never touched, and the branch itself is only read. Imported web_ids live in
      state/web_inbox.json; the journal's refs.web_id is also checked, so a lost state file
      never causes duplicates. Fetch failures back off exponentially (30 s .. reconcile_secs)
      and raise one alert after `fail_threshold` failures in a row; success resets both.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import subprocess
import time

from . import core

AGENT = "claude-web"                                   # default when a line names no known web agent
WEB_AGENTS = ("claude-web", "chatgpt")                 # web tools that write the inbox; local agents cannot be claimed
REF = "refs/aisync/web-inbox"
FILE = "events.jsonl"
SEEN_MAX = 20000


def _state_path(cfg: core.Config):
    return cfg.state / "web_inbox.json"


def load_state(cfg: core.Config) -> dict:
    return core.load_json(_state_path(cfg), {"seen": {}, "rejected": {}, "failures": 0, "runs": 0,
                                             "imported_total": 0})


@contextlib.contextmanager
def _import_lock(cfg: core.Config):
    """The watcher and a manual `aisync web-inbox` must not import the same line twice."""
    cfg.state.mkdir(parents=True, exist_ok=True)
    with open(cfg.state / ".web_inbox.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def fetch(cfg: core.Config) -> None:
    """Read-only: fetch the branch into our own ref. Retries with backoff (core.git_run)."""
    core.ensure_repo(cfg)
    core.git_run(cfg, ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", cfg.web_inbox_remote,
                       f"+refs/heads/{cfg.web_inbox_branch}:{REF}"], retries=cfg.git_retries)


def read_lines(cfg: core.Config) -> list[str]:
    r = core._git(["show", f"{REF}:{FILE}"], cfg.root, cfg.git_timeout_secs)
    if r.returncode != 0:
        raise core.GitError(f"git show {REF}:{FILE}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.splitlines()


def _parse(line: str) -> tuple[dict | None, str]:
    """-> (event, "") or (None, reason)."""
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        return None, "not JSON"
    if not isinstance(e, dict):
        return None, "not an object"
    if not str(e.get("web_id") or "").strip():
        return None, "no web_id"
    if not str(e.get("summary") or "").strip():
        return None, "empty summary"
    return e, ""


def import_lines(cfg: core.Config, lines: list[str]) -> dict:
    """Log every not-yet-imported line. Returns per-run counts (also the metrics record)."""
    rep = {"lines": 0, "imported": [], "already": 0, "rejected": [], "kind_mapped": []}
    with _import_lock(cfg):
        st = load_state(cfg)
        seen = dict(st.get("seen", {}))
        for ev in core.read_events(cfg):                       # belt and braces: the journal itself
            wid = (ev.get("refs") or {}).get("web_id")
            if wid and wid not in seen:
                seen[wid] = ev.get("id", "")
        for line in lines:
            if not line.strip():
                continue
            rep["lines"] += 1
            e, why = _parse(line)
            if e is None:
                bad = st.setdefault("rejected", {})
                if core._h(line) not in bad:                   # report a bad line once, not every 30 s
                    bad[core._h(line)] = why
                    rep["rejected"].append(why)
                continue
            wid = str(e["web_id"]).strip()
            if wid in seen:
                rep["already"] += 1
                continue
            raw_refs = e.get("refs")
            refs = {k: str(v) for k, v in raw_refs.items()} if isinstance(raw_refs, dict) else {}
            if isinstance(raw_refs, list) and raw_refs:        # ChatGPT writes refs as a list: keep it, do not drop it
                refs["web_refs"] = " | ".join(str(r) for r in raw_refs)
            agent = str(e.get("agent") or "").strip()
            if agent not in WEB_AGENTS:
                if agent:
                    refs["web_agent"] = agent
                agent = AGENT
            refs.update(web_id=wid, web_ts=str(e.get("ts", "")))
            kind = e.get("kind") or "note"
            if kind not in core.KINDS:                         # e.g. "milestone": keep the event, log it as note
                refs["web_kind"] = str(kind)
                rep["kind_mapped"].append(f"{wid}: {kind} -> note")
                kind = "note"
            files = e.get("files") if isinstance(e.get("files"), list) else []
            new = core.append_event(cfg, agent=agent, kind=kind, summary=str(e["summary"]),
                                    project=str(e.get("project") or ""), files=files, refs=refs)
            seen[wid] = new["id"]
            rep["imported"].append({"web_id": wid, "id": new["id"], "agent": agent})
            # save after every line: a crash mid-run never re-imports what is already logged
            st["seen"] = dict(list(seen.items())[-SEEN_MAX:])
            core.save_json(_state_path(cfg), st)
        st["seen"] = dict(list(seen.items())[-SEEN_MAX:])
        st["imported_total"] = st.get("imported_total", 0) + len(rep["imported"])
        core.save_json(_state_path(cfg), st)
    return rep


def poll(cfg: core.Config, do_fetch: bool = True) -> dict:
    """One import run: fetch + import + metrics/health. Never raises (the watcher calls it)."""
    t0 = time.monotonic()
    rec = {"ts": core.now_iso(), "web_inbox": True, "ok": True}
    try:
        if do_fetch:
            fetch(cfg)
        rec.update(import_lines(cfg, read_lines(cfg)))
    except (core.GitError, OSError, subprocess.SubprocessError, ValueError) as ex:
        rec.update(ok=False, error=f"{type(ex).__name__}: {ex}"[:400])
    rec["ms"] = round((time.monotonic() - t0) * 1000)

    st = load_state(cfg)
    st["runs"] = st.get("runs", 0) + 1
    st["last_run"] = rec["ts"]
    if rec["ok"]:
        st.update(failures=0, last_ok=rec["ts"], alerted=False)
    else:
        st["failures"] = st.get("failures", 0) + 1
        st["last_error"] = rec["error"]
        if st["failures"] >= cfg.fail_threshold and not st.get("alerted"):
            st["alerted"] = True
            core.alert(cfg, "web-inbox import failing",
                       f"{st['failures']} fetches in a row failed: {rec['error']}. Still retrying with backoff; "
                       f"run `aisync web-inbox` to see the error.")
    core.save_json(_state_path(cfg), st)
    rec["failures"] = st["failures"]
    # quiet runs (nothing new, no error) are not logged: 2880 identical lines a day help nobody
    if not rec["ok"] or rec.get("imported") or rec.get("rejected"):
        core.log_run(cfg, {**rec, "level": "info" if rec["ok"] else "error"})
    return rec


def next_delay(cfg: core.Config) -> float:
    """Seconds until the next poll: web_inbox_secs, doubled per consecutive failure, capped."""
    fails = load_state(cfg).get("failures", 0)
    return min(cfg.web_inbox_secs * 2 ** min(fails, 10), max(cfg.web_inbox_secs, cfg.reconcile_secs))
