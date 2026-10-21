"""aisync watcher: event-triggered + debounced sync (no fixed 10-second polling).

What: waits on file events (kqueue on macOS/BSD, a cheap stat poll elsewhere) for the hub files, every tool's rule file and every
      project's .git/logs/HEAD; runs one sync cycle after the stream goes quiet.
Why:  an idle system costs nothing; a burst of edits becomes one commit, not dozens.
How:  trailing debounce (`debounce_secs` after the last event), capped by `max_wait_secs` so a
      busy stream still syncs, never faster than `min_flush_interval_secs`. Every
      `reconcile_secs` a safety cycle runs in case an event was missed (it commits nothing
      when nothing changed). Watches are re-opened every wake, so editors that save by
      atomic rename are still followed. Create `<home>/STOP` to exit cleanly.
"""
from __future__ import annotations

import os
import select
import time

from . import core

O_EVTONLY = getattr(os, "O_EVTONLY", 0x8000)
HEARTBEAT_SECS = 60


def watch_paths(cfg: core.Config) -> list[str]:
    paths = [cfg.root / n for n in ("journal.jsonl", "preferences.md", "agents.md", "config.toml")]
    paths += [t.path for t in cfg.targets]
    paths += [p for p in (core.head_log(proj) for proj in core.project_list(cfg)) if p]
    paths += [d for d in cfg.scan_dirs]   # a new worktree/clone appearing is an event too
    from . import collab
    paths += collab.rule_source_files(cfg)  # a rule written into a home-base AGENTS.md is an event
    return [os.path.realpath(p) for p in paths if os.path.exists(p)]


def snapshot(paths: list[str]) -> dict:
    out = {}
    for p in paths:
        try:
            s = os.stat(p)
            out[p] = (s.st_mtime_ns, s.st_size, s.st_ino)
        except OSError:
            out[p] = None
    return out


def _wait_events(paths: list[str], timeout: float) -> bool:
    """Block until one of `paths` changes or `timeout` passes. True = something fired."""
    if not hasattr(select, "kqueue"):                 # non-macOS fallback: cheap stat poll
        before = snapshot(paths)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(min(2.0, max(0.05, end - time.monotonic())))
            if snapshot(paths) != before:
                return True
        return False
    kq = select.kqueue()
    fds, changes = [], []
    flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_DELETE | \
        select.KQ_NOTE_RENAME | select.KQ_NOTE_ATTRIB
    try:
        for p in paths:
            try:
                fd = os.open(p, O_EVTONLY)
            except OSError:
                continue
            fds.append(fd)
            changes.append(select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                                         flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags))
        return bool(kq.control(changes, 64, max(0.0, timeout)))
    finally:
        for fd in fds:
            os.close(fd)
        kq.close()


def _beat(cfg: core.Config, state: str) -> None:
    h = core.load_health(cfg)
    h.update(watcher_pid=os.getpid(), watcher_heartbeat=core.now_iso(),
             watcher_heartbeat_epoch=time.time(), watcher_state=state)
    core.save_health(cfg, h)


def run_watch(cfg: core.Config, max_cycles: int | None = None, max_seconds: float | None = None) -> int:
    started = time.monotonic()
    cycles = 0

    def cycle(reason: str) -> None:
        nonlocal cycles
        if core.load_health(cfg).get("cycles_paused"):
            core.log_run(cfg, {"ts": core.now_iso(), "level": "info", "message": f"cycle skipped ({reason}): paused"})
        else:
            core.run_cycle(cfg, reason)
        cycles += 1

    _beat(cfg, "starting")
    cycle("startup")
    paths = watch_paths(cfg)
    snap = snapshot(paths)
    first_evt = last_evt = None
    last_cycle = last_beat = time.monotonic()

    while True:
        now = time.monotonic()
        if (cfg.root / "STOP").exists():
            _beat(cfg, "stopped")
            return 0
        if max_cycles is not None and cycles >= max_cycles:
            return 0
        if max_seconds is not None and now - started >= max_seconds:
            return 0

        if last_evt is not None:
            due = min(last_evt + cfg.debounce_secs, first_evt + cfg.max_wait_secs,)
            due = max(due, last_cycle + cfg.min_flush_interval_secs)
            timeout = due - now
        else:
            timeout = min(cfg.reconcile_secs - (now - last_cycle), HEARTBEAT_SECS)
        if max_seconds is not None:
            timeout = min(timeout, max_seconds - (now - started))

        if timeout > 0 and _wait_events(paths, timeout):
            paths = watch_paths(cfg)              # pick up new/renamed files
            cur = snapshot(paths)
            if cur != snap:
                t = time.monotonic()
                first_evt = first_evt or t
                last_evt = t
                snap = cur

        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_SECS:
            _beat(cfg, "paused" if core.load_health(cfg).get("cycles_paused") else "watching")
            last_beat = now
        if last_evt is not None:
            due = max(min(last_evt + cfg.debounce_secs, first_evt + cfg.max_wait_secs),
                      last_cycle + cfg.min_flush_interval_secs)
            if now >= due:
                cycle("event")
                first_evt = last_evt = None
                last_cycle = time.monotonic()
                paths = watch_paths(cfg)
                snap = snapshot(paths)        # our own writes during the cycle are not new events
        elif now - last_cycle >= cfg.reconcile_secs:
            cycle("reconcile")
            last_cycle = time.monotonic()
            paths = watch_paths(cfg)
            snap = snapshot(paths)
