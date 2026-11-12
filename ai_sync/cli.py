"""`aisync` command line. Run `aisync -h` or see README.md."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, board, collab, core, demo, hooks, ui, watch, webinbox

DEFAULT_TARGETS = [("claude-code", "~/.claude/CLAUDE.md"),
                   ("codex", "~/.codex/AGENTS.md")]

GITIGNORE = "backups/\ngithooks/\nstate/*\n!state/current.json\nSTOP\n"

STARTER_BLOCK = """<!-- owner-preferences:start (shared by every AI tool; edit it in the hub's preferences.md) -->
- Before editing a repo other agents may touch, run `aisync claim`; exit code 3 means someone else holds those files.
- After each unit of work, log one line with `aisync log`.
- Every commit ends with an `Agent: <tool>` trailer.
<!-- owner-preferences:end -->"""

AGENTS_MD = """# Context hub (aisync)

Shared entry point for every AI coding tool on this machine (Claude Code, Codex, Cursor, ...).
Everything lives in `{hub}/`, a local git repo: each change is a commit, so nothing is lost.

## Protocol (every agent, every tool)
1. **Start of a session**: read this page (who is doing what). More: `aisync tail -n 30`.
   **Mid-session**: Claude Code, Codex and Cursor get what other tools did injected by hooks (`aisync hook`).
   Other tools: run `aisync news --agent <tool>` before each new unit of work. Commits get `Agent: <tool>`
   added by the global git hook when it can tell which tool you are; still write it yourself.
2. **The human states a rule for all projects** ("from now on we always ..."): record it at once:
   `aisync rule add --agent <tool> --project <repo> --when "<trigger>" --do "<action>" [--not "<forbidden>"] --reason "why"`
   It goes into the shared rules and every tool picks it up; you do not need to tell the other tools.
   Search first (`aisync rule list`); if a rule already covers it, replace it with `--replaces R-087` (its ID or a piece
   of its text) instead of adding a second one; the old one moves to `rules-archive.md`. Every rule keeps a stable
   ID like `[R-087]`. Say what enforces it: `--enforced-by <test or hook path>` or `--judgment-only "<why>"`.
   A rule is an interface, not an essay: `When: <trigger>. Do: <action>. Not: <forbidden>.`; the why goes in
   `--reason` (journal only). With `rule_spec = true` any other shape is refused; `aisync rule lint` lists misfits.
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
        if getattr(a, "topic", None) and not a.archived:
            from . import layers
            truth = core.extract_block((cfg.root / "preferences.md").read_text()) or ""
            lines = layers.topic_rules(truth, a.topic)
            for line in lines:
                print(line)
            return 0 if lines else 1
        for line in collab.rule_list(cfg, archived=a.archived):
            print(line)
        return 0
    if a.action == "enforcement":
        from . import enforcement
        rep = enforcement.report(cfg)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print(f"rules {rep['total']}: enforced {rep['enforced']}, judgment_only {rep['judgment_only']}, "
                  f"none {rep['none']}")
            print("meta  " + ", ".join(f"{k} {v}" for k, v in rep["meta"].items()))
            for rid in rep["meta_none"]:
                print(f"META NONE {rid}: say what enforces it in {enforcement.path(cfg)}")
            for m in rep["missing_paths"]:
                print(f"MISSING {m['id']}: {m['path']}")
        return 0 if rep["ok"] else 1
    if a.action == "lint":
        from . import rulespec
        rep = rulespec.lint(collab.rule_list(cfg))
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print(f"rules {rep['rules']}: interface shape {rep['fit']}, not yet {len(rep['bad'])}")
            for b in rep["bad"]:
                print(f"  {b['id'] or '(no id)'}: {'; '.join(b['problems'])}")
        return 0 if rep["rules"] and not rep["bad"] else 1
    if a.action == "rewrite":
        from . import rulespec
        if not a.file:
            print("error: rule rewrite --file <json {\"R-333\": \"When: ... Do: ...\"}>", file=sys.stderr)
            return 2
        rep = rulespec.rewrite(cfg, json.loads(Path(a.file).read_text()),
                               agent=a.agent or os.environ.get("AISYNC_AGENT", "") or "unknown")
        for rid, p in rep["problems"].items():
            print(f"REFUSED {rid}: {'; '.join(p)}")
        for m in rep["missing"]:
            print(f"LOST FACT {m['fact']!r} from: {m['rule']}")
        for rid, d in rep.get("dropped", {}).items():
            print(f"dropped from {rid}: " + "; ".join(f"{k} ({r})" for k, r in d.items()))
        print(f"rewritten {len(rep['rewritten'])}: {', '.join(rep['rewritten'])}")
        return 0 if not rep["problems"] and not rep["missing"] else 1
    if getattr(a, "when", None) or getattr(a, "do", None):
        from . import rulespec
        try:
            a.text = [rulespec.compose(a.when or "", a.do or "", a.not_ or "")]
        except ValueError as ex:
            print(f"error: {ex}", file=sys.stderr)
            return 2
    if not a.text:
        print("error: give the rule text, e.g. aisync rule add --agent trae \"always ...\"", file=sys.stderr)
        return 2
    try:
        rep = collab.rule_add(cfg, agent=a.agent or os.environ.get("AISYNC_AGENT", ""), text=" ".join(a.text),
                              reason=a.reason or "", project=a.project or "",
                              topic=a.topic or "", replaces=a.replaces or "",
                              enforced_by=a.enforced_by or "", judgment_only=a.judgment_only or "")
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    if rep["added"]:
        print(f"rule {rep.get('id')} added; every tool gets it on the next sync (about 30 s):\n{rep['rule']}")
        if rep.get("moved"):
            print(f"moved to rules-archive.md:\n{rep['moved']}")
    else:
        print("an equal rule already exists; nothing changed")
    return 0


def _paint(text: str, code: str, stream=None) -> str:
    """ANSI colour only for a human at a terminal; pipes, hooks and NO_COLOR get plain text."""
    stream = stream or sys.stdout
    return f"\x1b[{code}m{text}\x1b[0m" if stream.isatty() and not os.environ.get("NO_COLOR") else text


def _file_list(files) -> str:
    return f"[{', '.join(files)}]" if files else "[whole repo]"


def cmd_instinct(a) -> int:
    from . import instincts
    cfg = core.load_config()
    if a.action == "list":
        text, lines, err = instincts.read(cfg)
        if err:
            print(f"error: instincts file unreadable: {err}", file=sys.stderr)
            return 1
        print("\n".join(lines))
        return 0
    if not a.text or not a.eb:
        print("error: aisync instinct add \"one sentence\" --from EB-xxx --agent <tool>", file=sys.stderr)
        return 2
    try:
        rep = instincts.add(cfg, agent=a.agent or os.environ.get("AISYNC_AGENT", ""), text=" ".join(a.text), eb=a.eb)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    print(f"added: {rep['line']}\nfile: {rep['path']} (journal {rep['event']})\n"
          "the file is only written, not committed: commit it yourself")
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
    if not rep["overlaps"]:
        print(_paint("claimed", "32") + f" {c['project']} {_file_list(c['files'])} for {c['agent']} until {c['expires'][11:16]}")
        return 0
    for o in rep["overlaps"]:
        print(_paint("OVERLAP:", "1;31", sys.stderr) + f" {o['agent']} is working on {o['project']} {_file_list(o['files'])} "
              f"until {o['expires'][11:16]}: {o['summary']}", file=sys.stderr)
    others = ", ".join(sorted({o["agent"] for o in rep["overlaps"]}))
    print(f"stop and coordinate with {others}, or pick other files (exit 3). "
          f"Your claim is noted, so {others} can see you are waiting.", file=sys.stderr)
    return 3


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
        print(_paint("CONFLICT", "1;31") + f" {label}: {c['reason']} ({collab._tilde(c['path'])}) "
              f"-> aisync resolve {label} --take truth|target")
    for tool, hs in sorted(hooks.hook_stats(cfg).items()):
        print(f"hook     {tool:<12} {hs['calls']} calls/24h, {hs['injected']} injected, {hs['errors']} errors, "
              f"avg {hs['avg_ms']} ms, last {_clock(hs['last'])}")
    claims = collab.load_claims(cfg)
    for c in claims:
        print(f"claim    {c['agent']:<12} {c['project']} {_file_list(c['files'])} until {c['expires'][11:16]}: "
              f"{core._short(c['summary'], 60)}")
    for b in build_open_blocked(cfg):
        print(_paint("BLOCKED ", "1;33") + f" {b['agent']:<12} {b['project'] or '-'}: {core._short(b['summary'], 70)} "
              f"(since {_clock(b['ts'])})")
    wi = s["web_inbox"]
    if wi.get("every_secs"):
        print(f"web-in   every {wi['every_secs']:g}s, {wi.get('imported_total', 0)} imported, last ok "
              f"{wi.get('last_ok') or '-'}, failures in a row {wi.get('failures', 0)}"
              f"{'  last error: ' + wi['last_error'] if wi.get('failures') else ''}")
    if s["alerts_file"]:
        print(f"alerts   see {collab._tilde(s['alerts_file'])}")
    print("agents")
    for agent, e in sorted(s["agents"].items(), key=lambda kv: kv[1]["ts"] or "", reverse=True):
        print(f"  {agent:<14} {_clock(e['ts']):<11}  {e['project'] or '-'}: {core._short(e['summary'], 70)}")


def _clock(ts: str | None) -> str:
    """'2026-10-21T06:42:55+00:00' -> '10-21 06:42': enough for a human scanning a status page."""
    return f"{ts[5:10]} {ts[11:16]}" if ts and len(ts) >= 16 else (ts or "-")


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


def cmd_guard(a) -> int:
    """PreToolUse/SessionEnd hook: one Claude Code session per repo. Reads hook JSON; always exits 0."""
    from . import guard
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    out = guard.handle(payload if isinstance(payload, dict) else {})
    if out:
        print(out)
    return 0


def cmd_lease(a) -> int:
    from . import guard
    cfg = core.load_config()
    if a.action == "status":
        print(guard.status_text(cfg))
        return 0
    if not (a.key or a.session):
        print("error: release needs --key or --session", file=sys.stderr)
        return 2
    gone = guard.release(cfg, key=a.key or "", session=a.session or "")
    for k in gone:
        core.append_event(cfg, agent=a.agent or "claude-code", kind="release", project=Path(k).name,
                          summary=f"session lease released by hand: {k}")
    print(f"released {len(gone)} lease(s)")
    return 0


def cmd_wt(a) -> int:
    """`aisync wt start|land --key <repo> --session <id>`: a session's own worktree, one command each way.
    `land` pushes to main only with --yes; without it, it prints what would land and pushes nothing."""
    from . import worktree
    if not (a.key and a.session):
        print("error: wt needs --key <repo root> and --session <id>", file=sys.stderr)
        return 2
    if a.action == "start":
        ok, out = worktree.start(a.key, a.session)
        print(out)                                  # stdout stays the bare path, so `cd "$(aisync wt start ...)"` works
        if ok:
            print(worktree.NO_STASH, file=sys.stderr)
        return 0 if ok else 1
    if not a.yes:
        wt = worktree.path_for(a.key, a.session)
        if not wt.exists():
            print(f"no worktree at {wt} (run aisync wt start first)", file=sys.stderr)
            return 1
        print(f"dry run, nothing pushed: {wt} (branch {worktree.branch_for(a.session)}) would land on main. "
              f"Re-run with --yes to land.")
        return 0
    ok, msg = worktree.land(a.key, a.session)
    print(msg)
    return 0 if ok else 1


def cmd_gate(a) -> int:
    """`aisync gate pre-push <remote> <url>` (called by the git hook, refs on stdin) or `aisync gate check`."""
    from . import gate
    if a.action == "pre-push":
        rc = gate.pre_push(Path.cwd(), a.remote or "origin", sys.stdin.read().splitlines(),
                           say=lambda m: print(m, file=sys.stderr))
        return gate.REFUSED if rc else 0   # the hook tells a refusal (10) from the gate itself crashing
    sha = subprocess.run(["git", "-C", a.repo, "rev-parse", a.sha], capture_output=True, text=True).stdout.strip()
    if not sha:
        print(f"error: cannot resolve {a.sha} in {a.repo}", file=sys.stderr)
        return 2
    ok, out, secs = gate.check_commit(Path(a.repo), sha)
    print(out if not ok else f"passed in {secs:.0f}s")
    return 0 if ok else 1


def cmd_web_inbox(a) -> int:
    cfg = core.load_config()
    rec = webinbox.poll(cfg, do_fetch=not a.no_fetch)
    if a.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    elif rec["ok"]:
        print(f"web-inbox: {rec['lines']} line(s), imported {len(rec['imported'])}, already {rec['already']}, "
              f"rejected {len(rec['rejected'])} ({rec['ms']} ms)")
        for i in rec["imported"]:
            print(f"  logged {i['id']} ({i.get('agent', webinbox.AGENT)}, web_id {i['web_id']})")
        for m in rec["kind_mapped"]:
            print(f"  kind mapped: {m}")
        for why in rec["rejected"]:
            print(f"  rejected: {why}", file=sys.stderr)
    else:
        print(f"web-inbox FAILED ({rec['failures']} in a row): {rec['error']}", file=sys.stderr)
    return 0 if rec["ok"] else 1


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
    print(ui.banner(ui.style_for(sys.stdout), __version__) + "\n")
    print(f"demo hub with a synthetic two-week journal: {root}\n")
    print_status(core.status(cfg), cfg)
    print(f"\nexplore it (your real hub is untouched):\n  export AISYNC_HOME={root}\n"
          "  aisync tail -n 15; aisync claims; aisync report; aisync verify; aisync board")
    return 0


def cmd_board(a) -> int:
    cfg = core.load_config()
    if not (cfg.root / "journal.jsonl").exists():
        print(f"error: no hub at {cfg.root} (run `aisync init`, or `aisync demo` to try one)", file=sys.stderr)
        return 2
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write(out, board.render(cfg))
    print(f"wrote {out}: open it in a browser (it shows journal summaries and file names; read it before sharing)")
    return 0


def cmd_mcp(a) -> int:
    from . import mcp_server
    return mcp_server.main()


def cmd_tidy(a) -> int:
    from . import tidy
    cfg = core.load_config()
    rep = tidy.report(cfg)
    day = f"{__import__('datetime').datetime.now().astimezone():%Y-%m-%d}"
    if a.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        text = tidy.render(rep, day)
        if a.write:
            p = cfg.root / "tidy" / f"{day}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            core.atomic_write(p, text)
            print(f"written {p}")
        else:
            print(text, end="")
    return 0 if rep["rules"] else 1


def cmd_rulecheck(args) -> int:
    from . import rulecheck
    after = args.after or str(Path(os.environ.get("AISYNC_HOME", "~/.ai-sync")).expanduser() / "preferences.md")
    return rulecheck.run(args.before, after, args.archive, args.accept_drop)


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


class _Root(argparse.ArgumentParser):
    """The top-level parser: its --help is the logo screen, not argparse's default."""

    def format_help(self):
        commands = {}
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                commands = {a.dest: a.help or "" for a in action._choices_actions}
        return ui.help_screen(ui.style_for(sys.stdout), __version__, commands) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = _Root(prog="aisync", description="Advanced context management for all your AI coding tools.")
    p.add_argument("--version", action="version", version=f"aisync {__version__}")
    sub = p.add_subparsers(dest="cmd", parser_class=argparse.ArgumentParser)

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

    s = sub.add_parser("rule", help="shared rules: `rule add --when .. --do .. [--not ..]` (any agent, applies to all), `rule list`, `rule lint`")
    # real subcommands, so the rule text may come after the flags on Python 3.11 too (one positional
    # list after an `action` positional only parses intermixed on 3.12+)
    rsub = s.add_subparsers(dest="action", required=True)
    for action, ahelp in (("add", "add a shared rule every tool follows"), ("list", "print the shared rules"),
                          ("enforcement", "which test, hook or checker keeps each rule"),
                          ("lint", "rules not in the When/Do/Not shape"),
                          ("rewrite", "rewrite rules by ID from a JSON file, refusing lost facts")):
        r = rsub.add_parser(action, help=ahelp)
        r.add_argument("text", nargs="*")
        r.add_argument("--file", help="rewrite: JSON file mapping rule IDs to their new When/Do/Not text")
        r.add_argument("--when", help="add: the trigger (with --do and optional --not the rule is built as an interface)")
        r.add_argument("--do", help="add: the action the rule asks for")
        r.add_argument("--not", dest="not_", help="add: what the rule forbids")
        r.add_argument("--agent", "-a")
        r.add_argument("--project", "-p")
        r.add_argument("--reason", "-r")
        r.add_argument("--topic", "-t", help="topic heading the rule goes under (required once rules are grouped)")
        r.add_argument("--replaces", help="ID (R-087) or a piece of the text of the current rule this one replaces; "
                                          "it moves to rules-archive.md")
        r.add_argument("--archived", action="store_true", help="with `list`: the retired rules from rules-archive.md")
        r.add_argument("--enforced-by", help="add: comma-separated paths of the test, hook or checker that enforces it")
        r.add_argument("--judgment-only", help="add: why nothing in code can check the rule (instead of --enforced-by)")
        r.add_argument("--json", action="store_true", help="with `enforcement` or `lint`: machine-readable output")
        r.set_defaults(fn=cmd_rule)

    s = sub.add_parser("instinct", help="habits grown from past mistakes: `instinct list`, `instinct add \"...\" --from EB-xxx`")
    isub = s.add_subparsers(dest="action", required=True)
    for action in ("add", "list"):
        r = isub.add_parser(action, help="add a habit" if action == "add" else "print the habit list")
        r.add_argument("text", nargs="*")
        r.add_argument("--from", dest="eb", help="add: the EB id in the error log this habit comes from (required)")
        r.add_argument("--agent", "-a")
        r.set_defaults(fn=cmd_instinct)

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
    s.add_argument("--accept-drop", metavar="REASON", default=None,
                   help="accept a deliberate drop (over half) in what status counted; the reason is logged")
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

    s = sub.add_parser("guard", help="Claude Code PreToolUse/SessionEnd hook: one session per repo (stdin JSON)")
    s.set_defaults(fn=cmd_guard)

    s = sub.add_parser("lease", help="session leases held by the guard: status, or release one")
    s.add_argument("action", choices=("status", "release"))
    s.add_argument("--key", help="repo root (or file) path as shown by `lease status`")
    s.add_argument("--session", help="session id (prefix not accepted)")
    s.add_argument("--agent", "-a")
    s.set_defaults(fn=cmd_lease)

    s = sub.add_parser("wt", help="this session's own worktree: `wt start` opens it, `wt land` lands it (needs --yes)")
    s.add_argument("action", choices=("start", "land"))
    s.add_argument("--key", help="repo root whose main checkout is leased")
    s.add_argument("--session", help="your Claude Code session id (full id)")
    s.add_argument("--yes", action="store_true", help="land: really push to main (without it, a dry run)")
    s.set_defaults(fn=cmd_wt)

    s = sub.add_parser("gate", help="push-time test gate: `gate pre-push` (git hook) or `gate check` a commit")
    s.add_argument("action", choices=("pre-push", "check"))
    s.add_argument("remote", nargs="?", help="pre-push: remote name (from git)")
    s.add_argument("url", nargs="?", help="pre-push: remote url (from git)")
    s.add_argument("--repo", default=".", help="check: repo path")
    s.add_argument("--sha", default="HEAD", help="check: commit to test on a clean checkout")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("web-inbox", help="import web-Claude events from the web-inbox branch now (read-only fetch)")
    s.add_argument("--no-fetch", action="store_true", help="use the last fetched copy, no network")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_web_inbox)

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

    s = sub.add_parser("board", help="write the hub as one static HTML status board (claims, blockers, timeline)")
    s.add_argument("--out", "-o", default="board.html", help="output file (default: board.html)")
    s.set_defaults(fn=cmd_board)

    s = sub.add_parser("verify", help="check the journal hash chain")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("resolve", help="your ruling on a shared-rule conflict")
    s.add_argument("label")
    s.add_argument("--take", required=True, choices=("truth", "target"))
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("mcp", help="serve the hub to any MCP agent over stdio (needs: pip install 'aisync[mcp]')")
    s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("tidy", help="rules clerk: likely duplicates, rules not cited lately, core budget, shape and enforcer gaps (proposals only)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--write", action="store_true", help="write <hub>/tidy/<date>.md instead of printing")
    s.set_defaults(fn=cmd_tidy)
    s = sub.add_parser("rulecheck", help="tidy check: every hard fact of the old rules is still current or superseded (exit 1 if not)")
    s.add_argument("before", help="rules file before the tidy (e.g. git show <rev>:preferences.md > before.md)")
    s.add_argument("after", nargs="?", default=None, help="rules file after the tidy (default: the hub preferences.md)")
    s.add_argument("--archive", default=None, help="retired-rules file (default: rules-archive.md next to the after file)")
    s.add_argument("--accept-drop", metavar="REASON", default=None,
                   help="accept a deliberate drop (over half) in the rules compared; the reason is logged")
    s.set_defaults(fn=cmd_rulecheck)

    s = sub.add_parser("resume", help="clear failure counters after fixing a paused sync")
    s.set_defaults(fn=cmd_resume)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:  # bare `aisync`: the front screen instead of a usage error
        parser.print_help()
        return 0
    code = args.fn(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
