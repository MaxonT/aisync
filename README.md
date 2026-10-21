# aisync

> What: one standup for all your AI coding tools. Claude Code, Codex and Cursor share rules, a journal and file claims, and hear what the others did mid-session.
> Why: when several agents work on the same repos, none of them knows what the others just changed.
> How: `pip install` it, run `aisync demo` to see a hub after two weeks of use, then `aisync init` and `aisync install-hooks`.

[![ci](https://github.com/MaxonT/aisync/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxonT/aisync/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![deps](https://img.shields.io/badge/dependencies-0-brightgreen)
![license](https://img.shields.io/badge/license-MIT-green)

> [!NOTE]
> **The core idea.** Every tool reads and writes one local hub (`~/.ai-sync/`, a git repo).
> Rules live in one file and are copied into each tool's own rule file. Work is logged to an
> append-only, **hash-chained journal** (`aisync verify` catches any hand edit). Before touching
> files an agent **claims** them; an overlapping claim exits with code **3**, so the agent stops
> and coordinates. **Hooks** inject "what the other tools did since you last looked" into a
> running Claude Code, Codex or Cursor session, and stay silent when nothing changed.

If this saves your agents from stepping on each other, a star helps other people find it.

## Install

**Give it to your AI** (it reads the page, does a dry run first and asks before changing anything):

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**Or in a terminal** (installs with uv, pipx or pip; never edits shell config):

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Python 3.11+, macOS or Linux, no dependencies beyond the standard library and `git`.

## See it first: `aisync demo`

`aisync demo` builds a throwaway hub in a temp dir with a synthetic two-week journal: five tools
(claude-code, codex, cursor, windsurf, gemini-cli) on three made-up repos, with overlapping claims,
a blocker that got unblocked, shared rules added along the way and a real rule conflict.
Your own hub and tool configs are never touched.

```text
$ aisync demo
hub      /tmp/aisync-demo-k2x8/
watcher  NOT running (heartbeat -)
journal  66 events, chain intact
targets  claude-code: in sync, codex: differs, windsurf: in sync
metrics  183 cycles, failure rate 1.1%, avg 544 ms, 27 commits harvested, 2 rule copies updated
CONFLICT codex: rules changed in preferences.md and in this copy -> aisync resolve codex --take truth|target
hook     claude-code  64 calls/24h, 10 injected, 0 errors, avg 28 ms
claim    cursor       storefront-web [src/pages/checkout/] until 16:05: show refund credit on checkout summary
claim    claude-code  storefront-web [src/pages/checkout/Summary.tsx] until 16:15: wire /quote credit lines into Summary
BLOCKED  gemini-cli   infra: prod plan needs a read-only role on the billing account; waiting for access
agents
  claude-code    storefront-web: claimed: wire /quote credit lines into Summary
  codex          checkout-api: test: refund webhook end-to-end in the sandbox
  ...
```

## Use it

```bash
aisync init                                   # create ~/.ai-sync (override with AISYNC_HOME)
aisync install-hooks                          # shows a diff of every tool config it would change, then asks
aisync watch &                                # event-driven sync loop (or run `aisync sync` by hand)
```

What an agent does during a session:

```bash
aisync claim  -a codex -p checkout-api -f app/payments/ -m "SDK upgrade"   # exit 3 = someone holds it
aisync log    -a codex -p checkout-api -k dev_log -m "SDK 5.0, retries kept" -f app/payments/provider.py
aisync rule add -a codex "every API change ships with a contract test" --reason "two schema breaks"
aisync release -a codex -p checkout-api
```

| Command | What it does |
|---|---|
| `init` | create the hub: shared rules, journal, config, local git repo |
| `log` | append one event (`dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`) |
| `rule add` / `rule list` | any agent adds a shared rule; every tool's copy gets it on the next sync |
| `claim` / `release` / `claims` | who is working on which files right now; overlap = exit 3 |
| `news` | what other tools did since this session last looked |
| `hook <tool>` | entry point for Claude Code, Codex and Cursor hooks (reads hook JSON, never fails) |
| `install-hooks` | wire the hooks, seed the rules block, add the git `Agent:` trailer hook; diff first, asks before writing (`--dry-run`, `--yes`) |
| `status` | health, conflicts, claims, blockers, metrics (exit 1 when something needs you) |
| `report` | read-only self-check: silent tools, commits without an `Agent:` trailer, stale blockers |
| `tail` | latest journal events |
| `verify` | check the hash chain |
| `sync` / `watch` | run one sync cycle / the debounced, event-driven loop |
| `resolve` / `resume` | rule a rule conflict / clear failure counters after a pause |
| `demo` | the throwaway hub above |

## How it works

A sync cycle is `harvest` (new git commits in watched repos become journal events, attributed by
their `Agent:` trailer) → `rules` (new entries in repos you list in `rule_sources`) → `distribute`
(3-way sync of the rules block into each tool file) → `render` (progress page) → `flush` (commit the
hub, optional push). Each step is isolated: one failure never blocks the others.

The rules block is synced 3-way. If only the truth changed, copies are overwritten (with a backup).
If one copy was edited and nobody else changed anything, the edit is adopted everywhere. If both
sides changed, nothing is overwritten: it becomes a conflict for you to resolve.

Reliability and observability: timeouts and retries with backoff on every git call, failure
isolation per step and per project, auto-pause after `fail_threshold` failures in a row with an
`ALERT.md` and a desktop notification, a per-cycle run log (`state/runs.jsonl`), hook call stats and
`aisync status` for all of it.

## Configuration

`~/.ai-sync/config.toml` (set `AISYNC_HOME` to put the hub anywhere):

```toml
roster = ["claude-code", "codex", "cursor"]   # tools `report` expects to hear from
scan_dirs = ["~/projects"]                    # every git repo inside is watched
rule_sources = []                             # repos whose AGENTS.md "## Meta rule" feeds shared rules
remote = ""                                   # e.g. "origin" to also push the hub

[[target]]                                    # files carrying the shared-rules block
label = "claude-code"
path = "~/.claude/CLAUDE.md"
```

To keep `aisync watch` running, start it from your login items, a systemd user unit or a launchd
agent that runs `aisync watch`; create `~/.ai-sync/STOP` to stop it cleanly.

## Development

```bash
python3 -m unittest discover -s tests     # includes build wheel -> fresh venv -> aisync demo + verify
```

MIT licensed.
