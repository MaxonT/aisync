<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="aisync: one standup for all your AI coding tools"/>

# aisync: one standup for all your AI coding agents

**Claude Code, Codex and Cursor share rules, a journal and file claims, and hear what the others did mid-session.**

[![ci](https://github.com/MaxonT/aisync/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxonT/aisync/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/MaxonT/aisync)](https://github.com/MaxonT/aisync/releases)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![deps](https://img.shields.io/badge/dependencies-0-brightgreen)
![license](https://img.shields.io/badge/license-MIT-green)
[![GitHub stars](https://img.shields.io/github/stars/MaxonT/aisync.svg?style=social)](https://github.com/MaxonT/aisync/stargazers)

**English** · [中文](README.zh-CN.md) · [Website](https://maxont.github.io/aisync/) · [Demo status board](https://maxont.github.io/aisync/board/)

</div>

> [!NOTE]
> **Your AI tools are coworkers who never talk. aisync is their standup.**
> Every tool works out of one local hub (`~/.ai-sync/`, a git repo). Before editing, an agent
> **claims** files, and an overlapping claim exits with code 3, so the agent stops before it collides.
> Every decision, blocker and commit goes into one **hash-chained journal**. **Hooks** tell a
> running Claude Code, Codex or Cursor session what the other tools just did, and stay silent when
> nothing changed.

If aisync keeps your agents from stepping on each other, a star helps other people find it.

<p align="center"><img src="docs/assets/cli.png" alt="Running aisync in a terminal: a gradient AISYNC pixel logo, then every command grouped as start, every session, look around, keep the rules lean, keep it running" width="720"></p>

## Install

**Give it to your AI.** It reads the page, does a dry run first and asks before it changes anything:

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**Or in a terminal.** It installs with uv, pipx or pip and never edits your shell config:

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Needs Python 3.11+ and git, on macOS or Linux. Nothing else: aisync uses only the standard library.

After your first successful command, the CLI asks once: `Star MaxonT/aisync on GitHub? [y/N]`. The default is no, it never asks again, and it stays silent in CI, pipes and scripts. A yes stars the repo through your own `gh` login (or prints the link if `gh` is missing). Set `NO_STAR_PROMPT=1` to skip the question.

## See it

<img src="docs/assets/demo.gif" width="100%" alt="A real terminal: aisync demo prints a hub's status, claude-code claims app/api/refunds.py, codex claims the same file and is refused with OVERLAP and exit 3, codex logs a blocker, claude-code hears it through aisync news, releases the file, and codex's claim then succeeds"/>

<sub>A real terminal run: `aisync demo` (synthetic data), then two tools reach for the same file.
Step by step: [how it works](https://maxont.github.io/aisync/how-it-works/) · the same demo hub as a
[status board](https://maxont.github.io/aisync/board/).</sub>

| | Rules sync tools | aisync |
|---|---|---|
| **Claims** | None: two agents can edit the same file at once | `aisync claim` first; an overlap exits with code 3 and says who holds the files |
| **Journal** | No shared record of who did what | One append-only, hash-chained log of commits, decisions and blockers (`aisync verify`) |
| **Mid-session news** | Rules load when a session starts, then go quiet | Hooks inject what the other tools just did into the running session |

aisync also does the rules part: one rules block copied into every tool's own rule file, synced 3-way, conflicts never overwritten.

**Dogfooded:** 6,700+ journal events from 7 AI tools over 4 weeks.

## Use it

```bash
aisync demo            # a throwaway hub with a synthetic two-week journal
aisync init            # create ~/.ai-sync (or wherever AISYNC_HOME points)
aisync install-hooks   # shows every tool config change as a diff, then asks
aisync watch &         # event-driven sync loop (or run `aisync sync` by hand)
```

What an agent does during a session:

```bash
aisync claim   -a codex -p api -f app/payments/ -m "SDK upgrade"  # 3 = taken
aisync log     -a codex -p api -m "SDK 5.0, retries kept"
aisync rule add -a codex --when "changing an API" --do "add a contract test"
aisync release -a codex -p api
```

| Command | What it does |
|---|---|
| `init` | create the hub: shared rules, journal, config, local git repo |
| `log` | append one event (`dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`) |
| `rule add` / `rule list` | any agent adds a shared rule (`--when/--do/--not`, `--topic`, `--replaces R-012`); every tool's copy gets it on the next sync |
| `rule lint` / `rule rewrite` / `rule enforcement` | rules not in the When/Do/Not shape / rewrite them by ID without losing a fact / which test or hook keeps each rule |
| `tidy` | rules clerk: likely duplicates, rules nobody cited lately, core layer over budget; proposals only, you tick them |
| `rulecheck` | prove a rules cleanup lost no hard fact (paths, numbers, commands, IDs) |
| `instinct` | optional habit list grown from past mistakes, injected into every session |
| `mcp` | serve the hub to any MCP agent over stdio (`pip install 'aisync[mcp]'`) |
| `claim` / `release` / `claims` | who is working on which files right now; overlap = exit 3 |
| `news` | what other tools did since this session last looked |
| `hook <tool>` | entry point for Claude Code, Codex and Cursor hooks (reads hook JSON, never fails) |
| `install-hooks` | wire the hooks, seed the rules block, add the git `Agent:` trailer hook; diff first, asks before writing (`--dry-run`, `--yes`) |
| `status` | health, conflicts, claims, blockers, metrics (exit 1 when something needs you) |
| `board` | write the hub as one static HTML page: claims, blockers, who did what, timeline |
| `report` | read-only self-check: silent tools, commits without an `Agent:` trailer, stale blockers |
| `tail` | latest journal events |
| `verify` | check the hash chain |
| `sync` / `watch` | run one sync cycle / the debounced, event-driven loop |
| `resolve` / `resume` | rule on a rule conflict / clear failure counters after a pause |
| `demo` | the throwaway hub shown above |

## New in 0.2.0: rules as interfaces, loaded in layers

A rules file grows until every session pays for it and nobody reads it. 0.2.0 treats a rule like an API:

- **When / Do / Not.** Each rule is one line with a stable ID: `[R-087] When: <trigger>. Do: <action>. Not: <forbidden>.`
  The why goes to the journal, not the rule. `rule_spec = true` refuses anything else; `rule lint` lists misfits;
  `rule rewrite` reshapes old rules by ID and refuses a rewrite that drops a path, number or command.
- **Layers.** Mark a tool copy `layer = "core"`: it carries `core_topics` in full and every other topic as one line.
  When a prompt or tool call touches a topic's keywords (`[shelf]`), the hook injects that topic once per session.
  On the author's own hub this cut the always-loaded rules from 20,673 to 7,876 tokens; replaying 7 days of
  transcripts, 98.9% of the 189 times a folded rule was cited, it had been injected before the citation.
- **A stable prefix.** `progress_file` moves the live progress table out of the shared page, so the page stays
  byte-identical and prompt caches keep hitting: the next session after a journal event read 61,151 cached tokens
  instead of 13,060.
- **A clerk, not a janitor.** `aisync tidy` proposes (duplicates, uncited rules, budget overruns) and never edits;
  a replaced rule moves to `rules-archive.md` with its ID, nothing is deleted.
- **Any agent.** Three doors: the AGENTS.md page, the CLI, and `aisync mcp` (tools: rules, rule_add, log, claim,
  release, news, status, tidy). The core stays standard-library only; the MCP SDK is the optional `aisync[mcp]` extra.

## How it works

<p align="center"><img src="docs/assets/how-it-works.gif" width="100%" alt="Six steps: tool A claims a file, tool B is refused with exit 3, B logs a blocker, a hook tells A mid-session, A releases, B proceeds"/></p>
<p align="center"><sub><a href="https://maxont.github.io/aisync/how-it-works/">Step through it at your own pace</a> (play, pause, ← →)</sub></p>

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

progress_file = "progress.md"                 # keep agents.md byte-stable; hooks inject progress at session start
rule_spec = true                              # rule add only accepts When/Do/Not rules
require_enforcer = false                      # true: every new rule names --enforced-by or --judgment-only
core_topics = ["Safety", "Meta"]              # topic headings a layered copy carries in full
instincts_path = ""                           # optional habit file injected at session start
tidy_daily = false                            # the watcher starts one `aisync tidy --write` a day
tidy_usage_globs = []                         # files whose R-### mentions count as a rule being used

[shelf]                                       # folded topic -> keywords that pull it into a session
Deploys = ["deploy", "terraform", "rollback"]

[[target]]                                    # files carrying the shared-rules block
label = "claude-code"
path = "~/.claude/CLAUDE.md"
layer = "core"                                # optional: fold non-core topics, inject them on demand
```

To keep `aisync watch` running, start it from your login items, a systemd user unit or a launchd
agent that runs `aisync watch`; create `~/.ai-sync/STOP` to stop it cleanly.

## Standing on the shoulders of

> aisync: AGENTS.md, but for live coordination between AI tools.

| Project | How it shaped aisync |
|---|---|
| [![AGENTS.md stars](https://img.shields.io/github/stars/agentsmd/agents.md?style=social&label=AGENTS.md)](https://github.com/agentsmd/agents.md) | The starting idea: one plain-text instruction file every agent reads, no SDK. aisync keeps that shape for shared rules (one rules block copied into each tool's own AGENTS.md / CLAUDE.md) and adds what a static file cannot say: who is editing what right now, and what changed since the session started. |
| [![Claude Code stars](https://img.shields.io/github/stars/anthropics/claude-code?style=social&label=Claude%20Code)](https://github.com/anthropics/claude-code) | Mid-session news rides on Claude Code hooks: `aisync install-hooks` adds SessionStart, UserPromptSubmit and PostToolUse entries to ~/.claude/settings.json, and `aisync hook claude-code` answers with hookSpecificOutput.additionalContext only when another tool changed something. |
| [![Codex stars](https://img.shields.io/github/stars/openai/codex?style=social&label=Codex)](https://github.com/openai/codex) | Codex reads ~/.codex/AGENTS.md, which is where aisync seeds the shared rules block, and its hooks.json takes the same SessionStart / UserPromptSubmit / PostToolUse events, so `aisync hook codex` reuses the Claude Code reply format. |
| [Cursor](https://cursor.com/docs/agent/hooks) | Cursor hooks (~/.cursor/hooks.json) deliver the rules and progress at sessionStart and the news after each postToolUse through additional_context, because Cursor's User Rules live inside the app instead of a file aisync could sync. |
| [![git stars](https://img.shields.io/github/stars/git/git?style=social&label=git)](https://github.com/git/git) | The journal borrows git's hash chain: every event stores the id of the one before it and its own id is a SHA-256 of that link plus its body, so `aisync verify` spots any edited, lost or reordered line. The hub itself is a local git repo. |

aisync is an independent project, not affiliated with the projects above.

## Development

```bash
python3 -m unittest discover -s tests   # includes wheel -> venv -> demo
```

The images are generated: the terminal GIF is a recorded real run of
[`.github/refine/demo-terminal.json`](.github/refine/demo-terminal.json), the how-it-works animation
comes from [`.github/refine/how-it-works.stages.json`](.github/refine/how-it-works.stages.json), and the
credits above from [`.github/refine/lineage.json`](.github/refine/lineage.json). All demo data is synthetic.

MIT licensed.
