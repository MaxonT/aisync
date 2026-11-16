<!-- translated from README.md; the English README is the source of truth -->
[English](README.md) · [中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)





<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="aisync: Multi-Agent Context Management, Coordination & Observability"/>

# aisync: マルチエージェントのコンテキスト管理・連携・可観測性

**複数のAIコーディングエージェントを使う開発者のための、ローカルの共有ハブです。適切なコンテキストを共有し、並行作業を調整し、何が起きたかを検証できます。**

aisync は**コア部分がエージェント非依存**です。Claude Code、Codex、Cursor 向けのネイティブフックを備え、CLI、MCP サーバー、共有指示ファイルを通じてハブを他のエージェントにも公開します。この3つのネイティブ連携は、aisync を使えるエージェントの範囲を制限するものではありません。

[![ci](https://github.com/MaxonT/aisync/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxonT/aisync/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/MaxonT/aisync)](https://github.com/MaxonT/aisync/releases)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![deps](https://img.shields.io/badge/dependencies-0-brightgreen)
![license](https://img.shields.io/badge/license-MIT-green)
[![PyPI](https://img.shields.io/pypi/v/aisync-cli?label=PyPI&logo=pypi&logoColor=white)](https://pypi.org/project/aisync-cli/)
[![npm](https://img.shields.io/npm/v/%40mingyang2026%2Faisync-cli?label=npm&logo=npm&logoColor=white)](https://www.npmjs.com/package/@mingyang2026/aisync-cli)
[![Homebrew](https://img.shields.io/badge/Homebrew-maxont%2Ftap-FBB040?logo=homebrew&logoColor=white)](https://github.com/MaxonT/homebrew-tap)
[![GHCR](https://img.shields.io/badge/GHCR-container-2496ED?logo=docker&logoColor=white)](https://github.com/MaxonT/aisync/pkgs/container/aisync)
[![GitHub stars](https://img.shields.io/github/stars/MaxonT/aisync.svg?style=social)](https://github.com/MaxonT/aisync/stargazers)

[English](README.md) · **日本語** · [中文](README.zh-CN.md) · [Website](https://maxont.github.io/aisync/) · [Demo status board](https://maxont.github.io/aisync/board/)

</div>

> [!NOTE]
> **複数のコーディングエージェントが、ばらばらのルール、衝突するファイル所有権、途切れた履歴を抱えたまま作業する必要はありません。**
> aisync はマルチエージェントのワークフロー向けに設計された、**ローカルで検証可能なハブ**（`~/.ai-sync/`、git リポジトリ）です。
>
> - **コンテキスト管理:** ルールを共有し、3方向同期します。コアとなるコンテキストは最初に読み込み、関連するコンテキストはオンデマンドで注入します。フックが使える場合はセッション中の更新にも対応します。
> - **マルチエージェントの連携:** ファイルの claim を宣言し、重複する claim を検出します（終了コード 3）。所有権を解放し、ブロッカーや他のエージェントの活動を表示します。
> - **可観測性と検証:** ジャーナル、ステータスレポート、ボードを通じて、エージェントの活動、ブロッカー、健全性を確認できます。`aisync verify` はジャーナルのハッシュチェーンを検査して改ざんを検出します。
>
> ファイルの claim は**協調型**であり、OS が強制するロックではありません。エージェントは claim の仕組みを使う必要があります。ハッシュチェーンの検証は記録されたイベントの変更を検出しますが、claim されたすべての操作が実際に行われたことを証明するものではありません。

## 対象ユーザー

**2つ以上の AI コーディングエージェント**を、1つまたは複数のリポジトリにまたがって使っている開発者向けです。特にエージェントが並行して動く場合、タスクを引き継ぐ場合、決定事項やブロッカーの共有記録が必要な場合に役立ちます。aisync は別のコーディングエージェントでもタスクオーケストレーターでもありません。エージェントが利用できる**共有コンテキストと連携のレイヤー**です。

## 見てみる

<div align="center">

### The Agent Swarm Floor

<img width="705" height="875" alt="cover" src="https://github.com/user-attachments/assets/cee23378-2379-49a5-960b-ac63764bb95f" />



https://github.com/user-attachments/assets/f741ef6f-0c10-46f4-8c94-769c6e02d30e




## 連携: ネイティブフックは互換性の境界ではない

| 連携方式 | 利用できるツール | 期待できること |
|---|---|---|
| **ネイティブフック** | Claude Code、Codex、Cursor | 対応するフックイベントでのコンテキストとニュースの自動配信。加えて、aisync の連携・ジャーナルコマンドへのアクセス |
| **MCP サーバー** | MCP 対応エージェント | ルールの参照・追加、ログ記録、claim、解放、ニュース、ステータス、tidy を公開。エージェントがツールを呼び出す必要がある |
| **CLI** | シェルコマンドを実行できるエージェント | `aisync claim`、`aisync news`、`aisync log`、`aisync verify` などハブの操作に直接アクセス |
| **共有指示ファイル** | 対応する指示ファイルを読むエージェント | 常設のルールとコンテキストのみ。セッション中の自動ニュースや file claim の強制は行わない |

**互換性は自動連携と同じではありません。** ネイティブフックを持たないエージェントの場合、機能はそのエージェントの MCP/CLI の使い方と設定に依存します。aisync はすべての編集を自動的に傍受するわけではなく、すべてのエージェントが file claim を守ることを保証するわけでもありません。

<p align="center"><img src="docs/assets/cli.png" alt="Running aisync in a terminal: a gradient AISYNC pixel logo, then every command grouped as start, every session, look around, keep the rules lean, keep it running" width="720"></p>

## インストール

**AI に任せる。** ページを読み、まずドライランを行い、変更前に確認を求めます:

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**ターミナルから。** uv、pipx、pip のいずれかでインストールし、シェルの設定ファイルは一切編集しません:

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Python 3.11 以上と git が必要です（macOS または Linux）。それ以外は不要です。aisync は標準ライブラリのみを使用します。

**📦 パッケージマネージャーから。** コマンド名は引き続き `aisync` です:

```bash
pipx install aisync-cli           # 🐍 PyPI (or: uv tool install aisync-cli)
npx @mingyang2026/aisync-cli --version   # npm 0.2.9
brew install maxont/tap/aisync    # 🍺 Homebrew tap (Python 3.14 first)
docker run --rm ghcr.io/maxont/aisync --version   # 🐳 GHCR image
```


### 2つのエージェントが1つのファイルを奪い合う

<img src="docs/assets/demo.gif" width="100%" alt="A real terminal: aisync demo prints a hub's status, claude-code claims app/api/refunds.py, codex claims the same file and is refused with OVERLAP and exit 3, codex logs a blocker, claude-code hears it through aisync news, releases the file, and codex's claim then succeeds"/>

<sub>実際のターミナルでの実行: `aisync demo`（合成データ）の後、2つのツールが同じファイルに手を伸ばします。
手順ごとの説明は [how it works](https://maxont.github.io/aisync/how-it-works/) を、同じデモハブのステータスボードは
[status board](https://maxont.github.io/aisync/board/) を参照してください。</sub>

</div>

| マルチエージェントの要件 | aisync が提供する機能 |
|---|---|
| **コンテキスト管理** | 3方向同期されるルール、階層的なルール読み込み、フック経由の関連ニュース配信 |
| **マルチエージェントの連携** | 協調型のファイル claim と解放、重複検出（終了コード 3）、ブロッカーと活動の更新 |
| **可観測性と検証** | ハッシュチェーンのイベントジャーナル、`aisync verify`、`status`、`report`、`tail`、静的な `board` |

aisync はルールの部分も担当します。1つのルールブロックを各ツール自身のルールファイルにコピーし、3方向で同期します。競合が起きても上書きはしません。

**実運用済み:** 作者本人のハブで 6,700 件以上のジャーナルイベント。

## AGENTS.md だけではダメなのか？

リポジトリの常設指示には AGENTS.md を使い続けてください。aisync はそのそばで動き、1つのファイルではカバーできない部分を担当します。エージェントが作業する間に変化するコンテキストです。

| | AGENTS.md | aisync |
|---|---|---|
| 適用範囲 | リポジトリで作業するエージェントへの常設指示 | 設定された複数のエージェントとリポジトリにまたがる共有ハブ |
| 読まれるタイミング | セッション開始時 | 開始時に加え、フックによりセッション中にも |
| 誰が何を編集しているか | 対象外 | 協調型の claim。重複すると呼び出し元に終了コード 3 を返す |
| 改ざんの証拠 | ファイル形式では提供されない | ハッシュチェーンのジャーナル。`aisync verify` が記録済みイベントの改変を検出 |
| セッションごとのコンテキスト | ファイル全体を毎回読み込む | トリガーされたルールのみ: 常時読み込みルールは 20,673 → 7,876 トークン* |

<sub>\* 1人の作者のハブで1回測定（2026-11-08）。階層的な読み込みの前後で、常時読み込みルールのトークン数を比較したものです。1台のマシンでの値であり、ベンチマークではありません。お使いの環境では数値が異なります。`aisync tidy` は、お使いのルール予算と、どこからも参照されていないルールを報告します。</sub>

## 使ってみる

```bash
aisync demo            # a throwaway hub with a synthetic two-week journal
aisync init            # create ~/.ai-sync (or wherever AISYNC_HOME points)
aisync install-hooks   # shows every tool config change as a diff, then asks
aisync watch &         # event-driven sync loop (or run `aisync sync` by hand)
```

セッション中にエージェントが行うこと:

```bash
aisync claim   -a codex -p api -f app/payments/ -m "SDK upgrade"  # 3 = taken
aisync log     -a codex -p api -m "SDK 5.0, retries kept"
aisync rule add -a codex --when "changing an API" --do "add a contract test"
aisync release -a codex -p api
```

| コマンド | 機能 |
|---|---|
| `init` | ハブを作成: 共有ルール、ジャーナル、設定、ローカルの git リポジトリ |
| `log` | イベントを1件追加（`dev_log`、`decision`、`blocked`、`unblocked`、`rule_change`、`note`） |
| `rule add` / `rule list` | どのエージェントも共有ルールを追加できる（`--when/--do/--not`、`--topic`、`--replaces R-012`）。各ツールのコピーは次の同期で反映される |
| `rule lint` / `rule rewrite` / `rule enforcement` | When/Do/Not の形式に合わないルールの検出 / ID を指定して事実を失わずに書き直す / 各ルールをどのテストやフックが守っているかの一覧 |
| `rule enforce` | 既存ルールを何が守っているかを宣言する: `rule enforce R-012 --enforced-by tests/test_x.py`、`--judgment-only "<理由>"`、または可能だがまだ書かれていないチェックには `--planned "<書くべきチェック>" --size S\|M\|L`。`rule enforcement` は未完了の項目をすべて次の作業とともに一覧にする: 消えた enforcer ファイル、期限を過ぎた計画（S 7日、M 30日、L 90日）、計画のないルール。計画の期限は進むだけで戻らない。後ろ倒し、サイズの拡大、judgment-only への切り替えには `--reason` が必要で、記録に残る |
| `tidy` | ルールの事務担当: 重複の可能性があるルール、最近参照されていないルール、コア層の予算超過を検出。提案のみで、採用は人が選ぶ |
| `rulecheck` | ルールの整理で、パス、数値、コマンド、ID などの重要な事実が失われていないことを証明する |
| `instinct` | 過去のミスから育つ任意の習慣リスト。すべてのセッションに注入される |
| `gate` | push 時のテストゲート: `gate check` はコミットに対して `./check.sh --fast` を実行する。git の pre-push フックは `gate pre-push` を呼び出す（オプトイン。下記参照） |
| `web-inbox` | `web-inbox` ブランチから web-Claude のイベントをすぐに取り込む（`--no-fetch` は最後に取得したコピーを使う） |
| `guard` | Claude Code のフック: リポジトリごとに1セッションのみ。別のセッションがリポジトリを保持している場合は編集を拒否する（オプトイン。下記参照） |
| `lease` | どのセッションがどのリポジトリを保持しているか（`lease status`）、または手動で解放する（`lease release --key`） |
| `wt` | セッション専用の git worktree: `wt start` で開く。`wt land` は main に取り込むもので、`--yes` が必要（なければドライラン） |
| `mcp` | stdio 経由で任意の MCP エージェントにハブを提供する（`pip install 'aisync-cli[mcp]'`） |
| `claim` / `release` / `claims` | 今どのファイルを誰が作業しているか。重複 = 終了コード 3 |
| `news` | このセッションが最後に確認してから、他のツールが行ったこと |
| `hook <tool>` | Claude Code、Codex、Cursor のフックの入口（フックの JSON を読み、決して失敗しない） |
| `install-hooks` | フックを接続し、ルールブロックを用意し、git の `Agent:` トレーラーのフックを追加する。書き込み前に差分を表示して確認を求める（`--dry-run`、`--yes`） |
| `status` | 健全性、競合、claim、ブロッカー、メトリクス（何かが対応を必要とする場合は終了コード 1） |
| `board` | ハブを1枚の静的な HTML ページとして書き出す: claim、ブロッカー、誰が何をしたか、タイムライン |
| `report` | 読み取り専用の自己チェック: 沈黙しているツール、`Agent:` トレーラーのないコミット、古くなったブロッカー |
| `tail` | 最新のジャーナルイベント |
| `verify` | ハッシュチェーンを検査する |
| `sync` / `watch` | 同期を1サイクル実行 / デバウンス付きのイベント駆動ループを実行 |
| `resolve` / `resume` | ルールの競合を裁定する / 一時停止の後に失敗カウンターをクリアする |
| `demo` | 上で示した使い捨てのハブ |
| `version` / `-v` / `-V` / `--version` | バージョンを表示 |
| `help [command]` | この画面、または1つのコマンドのオプション（`aisync help log`） |
| `update` | GitHub で新しいリリースを確認し、アップグレードするコマンドを表示する（uv、pipx、install.sh、brew、pip）。自分で勝手にインストールはしない |
| `doctor` | python、git、ハブのフォルダ、ジャーナルのチェーンを確認（いずれかが失敗すると終了コード 1） |
| `completion` | bash、zsh、fish 用のシェル補完スクリプトを出力する: `eval "$(aisync completion zsh)"` |

## 0.2.0 の新機能: インターフェースとしてのルール、階層的に読み込み

ルールファイルは大きくなりすぎると、すべてのセッションがその分のコストを払い、誰も読まなくなります。0.2.0 ではルールを API のように扱います:

- **When / Do / Not。** 各ルールは安定した ID を持つ1行です: `[R-087] When: <trigger>. Do: <action>. Not: <forbidden>.`
  理由はルールではなくジャーナルに記録します。`rule_spec = true` はそれ以外を拒否します。`rule lint` は形式に合わないものを一覧にします。
  `rule rewrite` は ID を指定して古いルールの形を整え、パス、数値、コマンドを落とす書き換えは拒否します。
- **階層。** ツールのコピーに `layer = "core"` を付けると、`core_topics` は全文を含み、それ以外のトピックは1行ずつになります。
  プロンプトやツール呼び出しがトピックのキーワード（`[shelf]`）に触れると、そのトピックはセッション中に1回注入されます。
  作者本人のハブでは、常時読み込みルールを 20,673 から 7,876 トークンに減らしました。
- **安定したプレフィックス。** `progress_file` はライブの進捗表を共有ページの外に出すため、ページはバイト単位で同一のままとなり、プロンプトキャッシュが効き続けます。
- **事務担当であって、掃除係ではない。** `aisync tidy` は提案します（重複、参照されていないルール、予算超過）。編集はしません。
  置き換えられたルールは ID を付けて `rules-archive.md` に移されます。何も削除されません。
- **どのエージェントでも。** 3つの入口があります: AGENTS.md のページ、CLI、`aisync mcp`（ツール: rules、rule_add、log、claim、release、news、status、tidy）。コアは標準ライブラリのみで動き、MCP SDK はオプションの `aisync-cli[mcp]` extra です。

## 仕組み

<p align="center"><img src="docs/assets/how-it-works.gif" width="100%" alt="Six steps: tool A claims a file, tool B is refused with exit 3, B logs a blocker, a hook tells A mid-session, A releases, B proceeds"/></p>
<p align="center"><sub><a href="https://maxont.github.io/aisync/how-it-works/">自分のペースで順に見る</a>（再生、一時停止、← →）</sub></p>

同期の1サイクルは、`harvest`（監視対象リポジトリの新しい git コミットをジャーナルのイベントにする。`Agent:` トレーラーで帰属させる）→ `rules`（`rule_sources` に登録したリポジトリの新しいエントリ）→ `distribute`（ルールブロックを各ツールのファイルへ3方向同期）→ `render`（進捗ページ）→ `flush`（ハブをコミットする。任意で push）です。各ステップは独立しており、1つの失敗が他をブロックすることはありません。

ルールブロックは3方向で同期されます。真実の側だけが変わった場合は、コピーを上書きします（バックアップ付き）。1つのコピーだけが編集され、他に変更がなければ、その編集はすべての場所に採用されます。両側が変わった場合は、何も上書きせず、あなたが解決すべき競合として扱います。

信頼性と可観測性: すべての git 呼び出しにタイムアウトとバックオフ付きのリトライ、ステップごと・プロジェクトごとの障害分離、`fail_threshold` 回連続で失敗すると自動停止（`ALERT.md` とデスクトップ通知）、サイクルごとの実行ログ（`state/runs.jsonl`）、フックの呼び出し統計。これらはすべて `aisync status` で確認できます。

## 設定

`~/.ai-sync/config.toml`（ハブの場所を変えるには `AISYNC_HOME` を設定）:

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
gate_hook = false                            # opt-in: the git pre-push hook runs the test gate (gate.py)
guard_hook = false                           # opt-in: Claude Code runs the one-session-per-repo guard (guard.py)
web_inbox_secs = 0                           # 0 = off; else poll the web-inbox branch this often

[shelf]                                       # folded topic -> keywords that pull it into a session
Deploys = ["deploy", "terraform", "rollback"]

[[target]]                                    # files carrying the shared-rules block
label = "claude-code"
path = "~/.claude/CLAUDE.md"
layer = "core"                                # optional: fold non-core topics, inject them on demand
```

`aisync watch` を常時動かすには、ログイン項目、systemd のユーザーユニット、または `aisync watch` を実行する launchd エージェントから起動してください。`~/.ai-sync/STOP` を作成すると、きれいに停止します。

**オプトイン機能（すべてデフォルトでオフ）。** `gate_hook`、`guard_hook`、`web_inbox_secs` はハブの外の動作を変えるため、設定するまで何も有効になりません。guard は編集を拒否することがあります。セッションがロックアウトされた場合は `aisync lease status` を実行し、保持しているセッションが終了していれば `aisync lease release --key <repo>` を実行してください。guard を取り除くには、`~/.claude/settings.json` から、コマンドが ` guard` で終わる `PreToolUse` と `SessionEnd` のエントリを削除します。

## 先人の肩の上に立って

> aisync：マルチエージェントのコンテキスト管理・連携・可観測性。AGENTS.md が出発点でした。

| プロジェクト | aisync への影響 |
|---|---|
| [![AGENTS.md stars](https://img.shields.io/github/stars/agentsmd/agents.md?style=social&label=AGENTS.md)](https://github.com/agentsmd/agents.md) | 出発点となった発想: すべてのエージェントが読む1つのプレーンテキストの指示ファイル。SDK は不要。aisync は共有ルールでこの形を保ちます（1つのルールブロックを各ツールの AGENTS.md / CLAUDE.md にコピー）。そのうえで、静的なファイルでは表せないことを追加します: 今誰が何を編集しているか、セッション開始以降に何が変わったか。 |
| [![Claude Code stars](https://img.shields.io/github/stars/anthropics/claude-code?style=social&label=Claude%20Code)](https://github.com/anthropics/claude-code) | セッション中のニュースは Claude Code のフックに乗ります。`aisync install-hooks` は ~/.claude/settings.json に SessionStart、UserPromptSubmit、PostToolUse のエントリを追加し、`aisync hook claude-code` は他のツールが何かを変えたときだけ hookSpecificOutput.additionalContext で応答します。 |
| [![Codex stars](https://img.shields.io/github/stars/openai/codex?style=social&label=Codex)](https://github.com/openai/codex) | Codex は ~/.codex/AGENTS.md を読みます。aisync はそこに共有ルールブロックを用意します。また Codex の hooks.json は同じ SessionStart / UserPromptSubmit / PostToolUse のイベントを受け取るため、`aisync hook codex` は Claude Code と同じ応答形式を再利用します。 |
| [Cursor](https://cursor.com/docs/agent/hooks) | Cursor のフック（~/.cursor/hooks.json）は、sessionStart でルールと進捗を、postToolUse の後にニュースを additional_context で配信します。Cursor のユーザールールはアプリ内にあり、aisync が同期できるファイルではないためです。 |
| [![git stars](https://img.shields.io/github/stars/git/git?style=social&label=git)](https://github.com/git/git) | ジャーナルは git のハッシュチェーンを借りています。各イベントは直前のイベントの ID を保存し、自分の ID はそのリンクと本体の SHA-256 です。そのため `aisync verify` は、編集・欠落・順序の入れ替えられた行を検出できます。ハブ自体はローカルの git リポジトリです。 |

aisync は独立したプロジェクトであり、上記のプロジェクトとの提携関係はありません。

## 開発

```bash
python3 -m unittest discover -s tests   # includes wheel -> venv -> demo
```

画像は生成されたものです。ターミナルの GIF は [`.github/refine/demo-terminal.json`](.github/refine/demo-terminal.json) を記録した実際の実行結果です。how-it-works のアニメーションは [`.github/refine/how-it-works.stages.json`](.github/refine/how-it-works.stages.json) から作られ、上記のクレジットは [`.github/refine/lineage.json`](.github/refine/lineage.json) から作られています。デモのデータはすべて合成されたものです。

MIT ライセンス。


## 気に入ったら他の動画もどうぞ

https://github.com/user-attachments/assets/5ea7928b-4985-4700-8551-90477a84df40

https://github.com/user-attachments/assets/91385ac4-714c-4a16-908b-1859ef1c78e2


https://github.com/user-attachments/assets/bf661d5c-b7b1-4876-8469-2e11dd95b232



https://github.com/user-attachments/assets/d32f3abd-2986-4fbf-83a2-be287f0185a7

