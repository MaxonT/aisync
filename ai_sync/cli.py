"""`aisync` command line. Run `aisync -h` or see README.md."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, collab, core, demo, hooks, watch

DEFAULT_TARGETS = [("claude-code", "~/.claude/CLAUDE.md"),
                   ("codex", "~/.codex/AGENTS.md")]

GITIGNORE = "backups/\ngithooks/\nstate/*\n!state/current.json\nSTOP\n"

STARTER_BLOCK = """<!-- owner-preferences:start (shared by every AI tool; edit it in the hub's preferences.md) -->
- Before editing a repo other agents may touch, run `aisync claim`; exit code 3 means someone else holds those files.
- After each unit of work, log one line with `aisync log`.
- Every commit ends with an `Agent: <tool>` trailer.
<!-- owner-preferences:end -->"""

AGENTS_MD = """# AI collaboration hub (aisync)

Shared entry point for every AI coding tool on this machine (Claude Code, Codex, Cursor, ...).
Everything lives in `{hub}/`, a local git repo: each change is a commit, so nothing is lost.

## Protocol (every agent, every tool)
1. **Start of a session**: read this page (who is doing what). More: `aisync tail -n 30`.
   **Mid-session**: Claude Code, Codex and Cursor get what other tools did injected by hooks (`aisync hook`).
   Other tools: run `aisync news --agent <tool>` before each new unit of work.
2. **The human states a rule for all projects** ("from now on we always ..."): record it at once:
   `aisync rule add --agent <tool> --project <repo> "the rule, one sentence" --reason "why"`
   It goes into the shared rules and every tool picks it up; you do not need to tell the other tools.
3. **Before editing a repo** that other agents may be working in: `aisync claim --agent <tool> --project <repo>
   --files a.py,docs/ -m "what you are doing"` (exit 3 = someone else holds those files: coordinate or pick
   other work). `aisync release --agent <tool> --project <repo>` when done (claims expire after 2 h anyway).
4. **After a unit of work** (a fix, a decision, a blocker), log one line:
   `aisync log --agent <tool> --project <repo> --kind dev_log --summary "what changed and why" --files a.py,b.md`
   Kinds: `dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`.
   Git commits in the watched projects are harvested automatically; every commit carries an `Agent: <tool>` trailer.
5. **Editing rules by hand**: edit only `{hub}/preferences.md` (the `owner-preferences` block). The copies in each
   tool's own file are rewritten automatically. If you edit a copy instead, it is adopted when nobody else
   changed the rules meanwhile; otherwise it becomes a conflict that only the human resolves (`aisync resolve`).
6. Never hand-edit `journal.jsonl`: it is hash-chained and `aisync verify` flags any edit.

Each repo's own `AGENTS.md` / `CLAUDE.md` still applies on top of this page and wins where it is stricter.

## Current progress (auto-generated, do not edit)
<!-- aisync:progress:start -->
<!-- aisync:progress:end -->
"""


def _config_text(scan_dir: Path | None, targets: list[tuple[str, str]]) -> str:
    out = ["# aisync config. Times are seconds. See README.md for every key.",
           "debounce_secs = 30           # quiet time after the last change before a sync",
           "max_wait_secs = 120          # a non-stop stream of changes still syncs this often",
           "min_flush_interval_secs = 30",
           "reconcile_secs = 600         # safety-net cycle in case a file event was missed",
           "git_timeout_secs = 20",
           "git_retries = 3              # with exponential backoff",
           "fail_threshold = 5           # consecutive failures before pausing + alerting",
           'remote = ""                  # e.g. "origin" to also push; empty = local git only',
           'remote_branch = "main"',
           "# tools `aisync report` expects to hear from",
           f"roster = {json.dumps(list(core.DEFAULT_ROSTER))}",
           "# every git repo / worktree directly inside these dirs is watched; re-scanned each cycle",
           f"scan_dirs = {json.dumps([collab._tilde(scan_dir)] if scan_dir else [])}",
           '# add one-off repos elsewhere with:  [[project]]  path = "~/somewhere/repo"',
           "# repos whose AGENTS.md '## Meta rule' section feeds new shared rules, e.g. [\"my-handbook\"]",
           "rule_sources = []", "",
           "# files carrying the shared-rules block; `aisync install-hooks` seeds the block (after asking)"]
    for label, path in targets:
        out += ["[[target]]", f'label = "{label}"', f'path = "{path}"', ""]
    out += ["# live progress can also be written into files a tool loads on its own, e.g.",
            "# [[progress_target]]", '# label = "codex"', '# path = "~/.codex/AGENTS.md"',
            '# mode = "block"            # "block" = a section inside the file, "file" = the whole file', ""]
    return "\n".join(out)


def cmd_init(a) -> int:
    root = core.home()
    if (root / "journal.jsonl").exists() and not a.force:
        print(f"already initialised: {root} (use --force to fill in missing files)")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(exist_ok=True)
    block = STARTER_BLOCK
    if a.seed_from:
        seed = Path(a.seed_from).expanduser()
        block = core.extract_block(seed.read_text()) if seed.exists() else None
        if block is None:
            print(f"no owner-preferences block found in {seed}", file=sys.stderr)
            return 2
    projects_dir = Path(a.projects_dir).expanduser() if a.projects_dir else None
    try:
        targets = [tuple(t.split("=", 1)) for t in a.target] if a.target else DEFAULT_TARGETS
        targets = [(l.strip(), p.strip()) for l, p in targets]
    except ValueError:
        print("--target must be label=path", file=sys.stderr)
        return 2
    files = {"config.toml": _config_text(projects_dir, targets), ".gitignore": GITIGNORE,
             "agents.md": AGENTS_MD.format(hub=collab._tilde(root)),
             "preferences.md": "# Shared rules (source of truth)\n\n"
                               "Edit the block below; aisync copies it into every tool's own file.\n\n" + block + "\n",
             "journal.jsonl": ""}
    for name, text in files.items():
        if not (root / name).exists():
            (root / name).write_text(text)
    cfg = core.load_config(root)
    core.ensure_repo(cfg)
    n_proj = len(core.project_list(cfg))
    core.append_event(cfg, agent=os.environ.get("AISYNC_AGENT") or hooks.detect_agent() or "human", kind="note",
                      summary=f"aisync hub initialised ({len(cfg.targets)} rule targets, {n_proj} projects)",
                      files=list(files))
    rec = core.run_cycle(cfg, "init")
    print(f"initialised {root}: {len(cfg.targets)} targets, {n_proj} projects, cycle ok={rec['ok']}")
    print("next: `aisync install-hooks` (shows every change first and asks before writing)")
    return 0 if rec["ok"] else 1


def _default_project() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        return Path(r.stdout.strip()).name if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def cmd_log(a) -> int:
    refs = dict(r.split("=", 1) for r in a.ref or [] if "=" in r)
    files = [f.strip() for f in (a.files or "").split(",") if f.strip()]
    try:
        ev = core.append_event(core.load_config(), agent=a.agent or os.environ.get("AISYNC_AGENT", ""),
                               kind=a.kind, summary=a.summary, project=a.project or _default_project(),
                               files=files, refs=refs)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    print(f"logged {ev['id']} ({ev['agent']} {ev['kind']} @ {ev['project'] or '-'})")
    if files and ev["project"]:
        for c in collab.find_overlaps(core.load_config(), ev["agent"], ev["project"], files):
            print(f"warning: {c['agent']} has claimed {c['project']} ({c['summary']}) until {c['expires'][11:16]}",
                  file=sys.stderr)
    return 0


def cmd_rule(a) -> int:
    cfg = core.load_config()
    if a.action == "list":
        for line in collab.rule_list(cfg):
            print(line)
        return 0
    if not a.text:
        print("error: give the rule text, e.g. aisync rule add --agent trae \"always ...\"", file=sys.stderr)
        return 2
    try:
        rep = collab.rule_add(cfg, agent=a.agent or os.environ.get("AISYNC_AGENT", ""), text=" ".join(a.text),
                              reason=a.reason or "", project=a.project or "")
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    if rep["added"]:
        print(f"rule added; every tool gets it on the next sync (about 30 s):\n{rep['rule']}")
    else:
        print("an equal rule already exists; nothing changed")
    return 0


def cmd_claim(a) -> int:
    files = [f.strip() for f in (a.files or "").split(",") if f.strip()]
    try:
        rep = collab.claim(core.load_config(), agent=a.agent or os.environ.get("AISYNC_AGENT", ""),
                           project=a.project or _default_project(), summary=a.summary, files=files, ttl_min=a.ttl)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    c = rep["claim"]
    print(f"claimed {c['project']} {c['files'] or '[whole repo]'} for {c['agent']} until {c['expires'][11:16]}")
    for o in rep["overlaps"]:
        print(f"OVERLAP: {o['agent']} is working on {o['project']} {o['files'] or '[whole repo]'}: {o['summary']} "
              f"(until {o['expires'][11:16]}). Coordinate or pick other files.", file=sys.stderr)
    return 3 if rep["overlaps"] else 0


def cmd_release(a) -> int:
    gone = collab.release(core.load_config(), agent=a.agent or os.environ.get("AISYNC_AGENT", ""), project=a.project or "")
    print(f"released {len(gone)} claim(s)")
    return 0


def cmd_claims(a) -> int:
    claims = collab.load_claims(core.load_config())
    for c in claims:
        print(f"{c['agent']:<12} {c['project']:<24} until {c['expires'][11:16]}  [{', '.join(c['files']) or 'whole repo'}]  {c['summary']}")
    if not claims:
        print("no active claims")
    return 0


def cmd_report(a) -> int:
    r = collab.report(core.load_config(), days=a.days)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"last {r['days']} days: {r['events']} events, {r['rules']} shared rules, {r['cycles']} cycles "
          f"({r['failed_cycles']} failed)")
    for agent, kinds in sorted(r["per_agent"].items()):
        print(f"  {agent:<22} " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    print("hints (reported only; nothing is changed automatically):" if r["hints"] else "no hints: all agents look healthy")
    for h in r["hints"]:
        print(f"  - {h}")
    return 0


def print_status(s: dict, cfg: core.Config) -> None:
    w, j, m, h = s["watcher"], s["journal"], s["metrics"], s["health"]
    print(f"hub      {s['home']}")
    print(f"watcher  {'running pid ' + str(w['pid']) if w['alive'] else 'NOT running'} (heartbeat {w['heartbeat'] or '-'})")
    print(f"journal  {j['events']} events, chain {'intact' if not j['chain_problems'] else 'BROKEN: ' + '; '.join(j['chain_problems'][:3])}")
    print("targets  " + (", ".join(f"{k}: {v}" for k, v in s["targets"].items()) or "none"))
    print(f"git      {len(s['unflushed_files'])} unflushed file(s); push failures {h.get('push_failures', 0)}"
          f"{' (PAUSED)' if h.get('push_paused') else ''}; cycle failures {h.get('cycle_failures', 0)}"
          f"{' (PAUSED)' if h.get('cycles_paused') else ''}")
    print(f"metrics  {m['cycles']} cycles, failure rate {m['failure_rate']:.1%}, avg {m['avg_ms']} ms, "
          f"{m['commits_harvested']} commits harvested, {m['targets_updated']} rule copies updated")
    for label, c in s["conflicts"].items():
        print(f"CONFLICT {label}: {c['reason']} ({c['path']}) -> aisync resolve {label} --take truth|target")
    for tool, hs in sorted(hooks.hook_stats(cfg).items()):
        print(f"hook     {tool:<12} {hs['calls']} calls/24h, {hs['injected']} injected, {hs['errors']} errors, "
              f"avg {hs['avg_ms']} ms, last {hs['last']}")
    claims = collab.load_claims(cfg)
    for c in claims:
        print(f"claim    {c['agent']:<12} {c['project']} [{', '.join(c['files']) or 'whole repo'}] until {c['expires'][11:16]}: "
              f"{core._short(c['summary'], 60)}")
    for b in build_open_blocked(cfg):
        print(f"BLOCKED  {b['agent']:<12} {b['project'] or '-'}: {core._short(b['summary'], 70)} (since {b['ts'][:16]})")
    if s["alerts_file"]:
        print(f"alerts   see {s['alerts_file']}")
    print("agents")
    for agent, e in sorted(s["agents"].items(), key=lambda kv: kv[1]["ts"] or "", reverse=True):
        print(f"  {agent:<14} {e['ts']}  {e['project'] or '-'}: {core._short(e['summary'], 70)}")


def build_open_blocked(cfg: core.Config) -> list[dict]:
    return core.build_current(core.read_events(cfg))["open_blocked"]


def cmd_status(a) -> int:
    cfg = core.load_config()
    s = core.status(cfg)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print_status(s, cfg)
    bad = s["conflicts"] or s["journal"]["chain_problems"] or s["health"].get("push_paused") or s["health"].get("cycles_paused")
    return 1 if bad else 0


def cmd_tail(a) -> int:
    for e in core.read_events(core.load_config())[-a.n:]:
        files = f"  [{', '.join(e.get('files', [])[:5])}]" if e.get("files") else ""
        print(f"{e['ts']}  {e['agent']:<12} {e['kind']:<11} {e.get('project') or '-'}: {e['summary']}{files}")
    return 0


def cmd_news(a) -> int:
    agent = a.agent or os.environ.get("AISYNC_AGENT", "") or hooks.detect_agent()
    if not agent:
        print("error: pass --agent (or set AISYNC_AGENT)", file=sys.stderr)
        return 2
    rep = hooks.news(core.load_config(), agent, a.session or "")
    text = hooks.news_text(rep)
    print(text or ("cursor set; next call shows what changed after now" if rep["first"] else "nothing new"))
    return 0


def cmd_hook(a) -> int:
    """Called by a tool's own hook system. Reads the hook JSON on stdin; always exits 0."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    if a.event:
        payload.setdefault("hook_event_name", a.event)
    out = hooks.handle(a.tool, payload if isinstance(payload, dict) else {})
    if out:
        print(out)
    return 0


def cmd_install_hooks(a) -> int:
    """Two phases: show every change as a diff, then write only after a yes (or --yes)."""
    cfg = core.load_config()
    want = [w.strip() for w in a.only.split(",")] if a.only else list(hooks.INSTALL_STEPS)
    unknown = [w for w in want if w not in hooks.INSTALL_STEPS]
    if unknown:
        print(f"error: unknown step(s) {', '.join(unknown)}; choose from {', '.join(hooks.INSTALL_STEPS)}", file=sys.stderr)
        return 2
    changes, errors = hooks.plan_install(cfg, Path.home(), hooks.aisync_bin(), want)
    for c in changes:
        print(f"== {c.tool}: {c.note}")
        print(c.diff() or f"(no text diff: {c.path})")
    for e in errors:
        print(f"cannot do {e}", file=sys.stderr)
    if not changes:
        print("nothing to change: everything is already installed")
        return 1 if errors else 0
    if a.dry_run:
        print(f"dry-run: {len(changes)} change(s) shown above, nothing was written")
        return 1 if errors else 0
    if not a.yes:
        if not sys.stdin.isatty():
            print("no terminal to ask: re-run with --yes to apply the changes above", file=sys.stderr)
            return 1
        if input(f"apply these {len(changes)} change(s)? (a backup of each JSON file is kept) [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing written")
            return 1
    bad = len(errors)
    for c in changes:
        try:
            c.apply()
            print(f"{c.tool:<12} done: {c.note}")
        except (OSError, ValueError, subprocess.CalledProcessError) as ex:
            bad += 1
            print(f"{c.tool:<12} FAILED: {ex}", file=sys.stderr)
    return 1 if bad else 0


def cmd_sync(a) -> int:
    rec = core.run_cycle(core.load_config(), "manual")
    print(json.dumps(rec, ensure_ascii=False, indent=2) if a.json else
          f"cycle ok={rec['ok']} in {rec['ms']} ms: " + ", ".join(
              f"{k}={'ok' if v['ok'] else 'FAIL ' + v['error']}" for k, v in rec["steps"].items()))
    return 0 if rec["ok"] else 1


def cmd_watch(a) -> int:
    return watch.run_watch(core.load_config(), max_cycles=a.max_cycles, max_seconds=a.max_seconds)


def cmd_demo(a) -> int:
    try:
        root = demo.build(Path(a.dir).expanduser() if a.dir else None)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    cfg = core.load_config(root)
    print(f"demo hub with a synthetic two-week journal: {root}\n")
    print_status(core.status(cfg), cfg)
    print(f"\nexplore it (your real hub is untouched):\n  export AISYNC_HOME={root}\n"
          "  aisync tail -n 15; aisync claims; aisync report; aisync verify")
    return 0


def cmd_verify(a) -> int:
    problems = core.verify_chain(core.load_config())
    print("journal chain intact" if not problems else "\n".join(problems))
    return 0 if not problems else 1


def cmd_resolve(a) -> int:
    try:
        rep = core.resolve(core.load_config(), a.label, a.take)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    print(f"resolved {a.label} (took {a.take}); updated: {rep['updated'] or 'none'}; open conflicts: {rep['open_conflicts'] or 'none'}")
    return 0


def cmd_resume(a) -> int:
    core.resume(core.load_config())
    print("failure counters cleared; pushing and cycles resumed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aisync", description="One standup for all your AI coding tools.")
    p.add_argument("--version", action="version", version=f"aisync {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the hub (default ~/.ai-sync, override with AISYNC_HOME)")
    s.add_argument("--seed-from", help="file whose owner-preferences block becomes the shared rules (default: a starter block)")
    s.add_argument("--projects-dir", help="watch every git repo directly inside this dir")
    s.add_argument("--target", action="append",
                   help="label=path of a file carrying the rules block (repeatable; default: Claude Code, Codex)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("log", help="append one event to the journal")
    s.add_argument("--summary", "-m", required=True)
    s.add_argument("--agent", "-a", help="claude-code|codex|cursor|... (or env AISYNC_AGENT)")
    s.add_argument("--kind", "-k", default="dev_log", choices=core.KINDS)
    s.add_argument("--project", "-p", help="default: name of the current git repo")
    s.add_argument("--files", "-f", help="comma-separated paths")
    s.add_argument("--ref", action="append", help="key=value, e.g. pr=45 (repeatable)")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("rule", help="shared rules: `rule add \"...\"` (any agent, applies to all) or `rule list`")
    # real subcommands, so the rule text may come after the flags on Python 3.11 too
    rsub = s.add_subparsers(dest="action", required=True)
    r = rsub.add_parser("add", help="add a shared rule every tool follows")
    r.add_argument("text", nargs="*")
    r.add_argument("--agent", "-a")
    r.add_argument("--project", "-p")
    r.add_argument("--reason", "-r")
    r.set_defaults(fn=cmd_rule)
    r = rsub.add_parser("list", help="print the shared rules")
    r.set_defaults(fn=cmd_rule)

    s = sub.add_parser("claim", help="say you are working on files of a repo now (exit 3 = overlap)")
    s.add_argument("--summary", "-m", required=True)
    s.add_argument("--agent", "-a")
    s.add_argument("--project", "-p")
    s.add_argument("--files", "-f", help="comma-separated; omit = whole repo")
    s.add_argument("--ttl", type=int, default=collab.CLAIM_TTL_MIN, help="minutes (default 120)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("release", help="drop your claims (one project, or all)")
    s.add_argument("--agent", "-a")
    s.add_argument("--project", "-p")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("claims", help="who is working where right now")
    s.set_defaults(fn=cmd_claims)

    s = sub.add_parser("report", help="self-check: who logs, unattributed commits, failures (read-only hints)")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("status", help="health, per-agent activity, conflicts, metrics (exit 1 if attention needed)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("tail", help="latest journal events")
    s.add_argument("-n", type=int, default=20)
    s.set_defaults(fn=cmd_tail)

    s = sub.add_parser("news", help="what other tools did since this session last looked (first call sets the cursor)")
    s.add_argument("--agent", "-a")
    s.add_argument("--session", "-s", help="any stable id for this chat/session")
    s.set_defaults(fn=cmd_news)

    s = sub.add_parser("hook", help="entry point for a tool's hook system (reads hook JSON on stdin, never fails)")
    s.add_argument("tool", choices=("claude-code", "codex", "cursor"))
    s.add_argument("--event", help="hook event name when the tool does not send one")
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("install-hooks", help="wire news hooks into Claude Code/Codex/Cursor, rules blocks, git Agent "
                                             "trailer (shows a diff and asks first)")
    s.add_argument("--only", help="comma list of " + ",".join(hooks.INSTALL_STEPS))
    s.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
    s.add_argument("--yes", "-y", action="store_true", help="apply without asking")
    s.set_defaults(fn=cmd_install_hooks)

    s = sub.add_parser("sync", help="run one cycle now (harvest, distribute, render, flush)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("watch", help="event-driven, debounced sync loop (run it as a login service)")
    s.add_argument("--max-cycles", type=int)
    s.add_argument("--max-seconds", type=float)
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("demo", help="build a throwaway hub with a synthetic two-week journal and show its status")
    s.add_argument("--dir", help="where to build it (default: a new temp dir)")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("verify", help="check the journal hash chain")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("resolve", help="your ruling on a shared-rule conflict")
    s.add_argument("label")
    s.add_argument("--take", required=True, choices=("truth", "target"))
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("resume", help="clear failure counters after fixing a paused sync")
    s.set_defaults(fn=cmd_resume)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
