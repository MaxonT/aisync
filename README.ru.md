<!-- translated from README.md; the English README is the source of truth -->
[English](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · **Русский**





<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="aisync: управление контекстом, координация и наблюдаемость мультиагентных систем"/>

# aisync: управление контекстом, координация и наблюдаемость мультиагентных систем

**Один локальный хаб для разработчиков, работающих с несколькими AI-агентами для написания кода: делитесь нужным контекстом, координируйте параллельную работу и проверяйте, что на самом деле произошло.**

aisync **не привязан к конкретному агенту** по своей сути. Он предоставляет нативные хуки для Claude Code, Codex и Cursor, а свой хаб открывает другим агентам через CLI, MCP-сервер или общие файлы инструкций. Эти три нативные интеграции **не** ограничивают, какие агенты могут использовать aisync.

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

**English** · [中文](README.zh-CN.md) · [Сайт](https://maxont.github.io/aisync/) · [Статусная доска с демо](https://maxont.github.io/aisync/board/)

</div>

> [!NOTE]
> **Несколько агентов для написания кода не должны работать с изолированными правилами, конфликтующим владением файлами и разорванной историей.**
> aisync — **локальный, проверяемый хаб** (`~/.ai-sync/`, git-репозиторий), созданный для мультиагентных workflow:
>
> - **Управление контекстом:** делитесь правилами и синхронизируйте их трёхсторонне; загружайте базовый контекст заранее и подставляйте релевантный контекст по запросу, включая обновления посреди сессии там, где доступны хуки.
> - **Координация агентов:** объявляйте claim-ы на файлы, обнаруживайте пересекающиеся claim-ы (код выхода 3), освобождайте владение и показывайте блокеры и активность других агентов.
> - **Наблюдаемость и проверка:** смотрите активность агентов, блокеры и состояние через журнал, отчёты о статусе и доску; `aisync verify` проверяет хеш-цепочку журнала на подмену.
>
> Claim-ы на файлы **кооперативные**, а не блокировки на уровне ОС: агенты обязаны использовать протокол claim-ов. Проверка хеш-цепочки обнаруживает изменения записанных событий; она не доказывает, что каждое заявленное действие действительно произошло.

## Для кого это

Для разработчиков, которые работают с **двумя и более AI-агентами для написания кода** в одном или нескольких репозиториях, особенно когда агенты работают параллельно, передают друг другу задачи или нуждаются в общей записи решений и блокеров. aisync — не ещё один агент для написания кода и не оркестратор задач: это **общий слой контекста и координации**, которым могут пользоваться эти агенты.

## Посмотрите

<div align="center">

### Рабочий зал роя агентов

<img width="705" height="875" alt="обложка" src="https://github.com/user-attachments/assets/cee23378-2379-49a5-960b-ac63764bb95f" />



https://github.com/user-attachments/assets/f741ef6f-0c10-46f4-8c94-769c6e02d30e




## Интеграции: нативные хуки не являются границей совместимости

| Интеграция | Кто может использовать | Что ожидать |
|---|---|---|
| **Нативные хуки** | Claude Code, Codex, Cursor | Автоматическая доставка контекста и новостей на поддерживаемых событиях хуков, а также доступ к командам координации и журнала aisync |
| **MCP-сервер** | MCP-совместимые агенты | Открывает правила, добавление правил, логирование, claim-ы, освобождение, новости, статус и tidy; агент должен сам вызывать инструменты |
| **CLI** | Агенты, умеющие запускать shell-команды | Прямой доступ к операциям хаба, например `aisync claim`, `aisync news`, `aisync log` и `aisync verify` |
| **Общие файлы инструкций** | Агенты, читающие поддерживаемые файлы инструкций | Только постоянные правила и контекст; без автоматических новостей посреди сессии и без принудительного соблюдения claim-ов на файлы |

**Совместимость не равна автоматической интеграции.** Для агентов без нативных хуков функции зависят от того, как агент использует MCP/CLI и как он настроен. aisync не перехватывает автоматически все правки и не гарантирует, что каждый агент будет соблюдать claim-ы на файлы.

<p align="center"><img src="docs/assets/cli.png" alt="Запуск aisync в терминале: градиентный пиксельный логотип AISYNC, затем все команды, сгруппированные по разделам: начало, каждая сессия, обзор, поддержка правил в порядке, работа в фоне" width="720"></p>

## Установка

**Передайте это своему AI.** Он читает страницу, сначала делает пробный запуск и спрашивает перед любыми изменениями:

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**Или в терминале.** Установка идёт через uv, pipx или pip, конфиг вашей оболочки никогда не редактируется:

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Нужны Python 3.11+ и git, macOS или Linux. Больше ничего: aisync использует только стандартную библиотеку.

**📦 Или через менеджер пакетов.** Команда по-прежнему называется `aisync`:

```bash
pipx install aisync-cli           # 🐍 PyPI (or: uv tool install aisync-cli)
npx @mingyang2026/aisync-cli --version   # 📦 npm (0.2.7 now; 0.2.8 pending)
brew install maxont/tap/aisync    # 🍺 Homebrew tap (Python 3.14 first)
docker run --rm ghcr.io/maxont/aisync --version   # 🐳 GHCR image
```


### Два агента, один файл

<img src="docs/assets/demo.gif" width="100%" alt="Реальный терминал: демо aisync показывает состояние хаба, claude-code захватывает app/api/refunds.py, codex пытается захватить тот же файл и получает OVERLAP с кодом выхода 3, codex записывает блокер, claude-code узнаёт о нём через aisync news, освобождает файл, и claim codex затем проходит"/>

<sub>Реальный запуск в терминале: `aisync demo` (синтетические данные), затем два инструмента обращаются к одному и тому же файлу.
Пошагово: [как это работает](https://maxont.github.io/aisync/how-it-works/) · тот же демо-хаб как
[статусная доска](https://maxont.github.io/aisync/board/).</sub>

</div>

| Потребность в мультиагентной работе | Что даёт aisync |
|---|---|
| **Управление контекстом** | Правила, синхронизируемые трёхсторонне, многоуровневая загрузка правил и релевантные новости посреди сессии через хуки |
| **Координация агентов** | Кооперативные claim-ы и освобождения файлов, обнаружение пересечений (код выхода 3), блокеры и обновления активности |
| **Наблюдаемость и проверка** | Журнал событий с хеш-цепочкой, `aisync verify`, `status`, `report`, `tail` и статичная `board` |

aisync также берёт на себя правила: один блок правил копируется в файл правил каждого инструмента, синхронизируется трёхсторонне, конфликты никогда не перезаписываются.

**Проверено на себе:** более 6 700 событий журнала в собственном хабе автора.

## Зачем не просто AGENTS.md?

AGENTS.md оставьте для постоянных инструкций репозитория. aisync работает рядом с ним и покрывает то, что не может один файл: контекст, который меняется, пока агенты работают.

| | AGENTS.md | aisync |
|---|---|---|
| Область действия | Постоянные инструкции для агентов, работающих в репозитории | Общий хаб для настроенных агентов и репозиториев |
| Когда читается | При старте сессии | В начале и посреди сессии через хуки |
| Кто что редактирует | Не покрывается | Кооперативные claim-ы: пересечение возвращает вызывающему код 3 |
| Защита от подмены | Формат файла её не даёт | Журнал с хеш-цепочкой: `aisync verify` обнаруживает изменения записанных событий |
| Контекст на сессию | Весь файл каждый раз | Только сработавшие правила: постоянно загруженные правила 20,673 → 7,876 токенов* |

<sub>\* Измерено однократно в собственном хабе одного автора (2026-11-08): постоянно загруженные правила до и после многоуровневой загрузки. Одна машина, это не бенчмарк: ваши цифры будут другими. `aisync tidy` показывает ваш бюджет правил и правила, на которые никто не ссылается.</sub>

## Использование

```bash
aisync demo            # a throwaway hub with a synthetic two-week journal
aisync init            # create ~/.ai-sync (or wherever AISYNC_HOME points)
aisync install-hooks   # shows every tool config change as a diff, then asks
aisync watch &         # event-driven sync loop (or run `aisync sync` by hand)
```

Что агент делает во время сессии:

```bash
aisync claim   -a codex -p api -f app/payments/ -m "SDK upgrade"  # 3 = taken
aisync log     -a codex -p api -m "SDK 5.0, retries kept"
aisync rule add -a codex --when "changing an API" --do "add a contract test"
aisync release -a codex -p api
```

| Команда | Что делает |
|---|---|
| `init` | создаёт хаб: общие правила, журнал, конфиг, локальный git-репозиторий |
| `log` | добавляет одно событие (`dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`) |
| `rule add` / `rule list` | любой агент добавляет общее правило (`--when/--do/--not`, `--topic`, `--replaces R-012`); копия каждого инструмента получает его при следующей синхронизации |
| `rule lint` / `rule rewrite` / `rule enforcement` | правила не в формате When/Do/Not / переписать их по ID без потери фактов / какой тест или хук удерживает каждое правило |
| `rule enforce` | указать, что удерживает существующее правило: `rule enforce R-012 --enforced-by tests/test_x.py`, `--judgment-only "<почему>"` или `--planned "<проверка, которую нужно написать>" --size S\|M\|L` для проверки, которая возможна, но ещё не написана. Затем `rule enforcement` перечисляет каждую открытую позицию с её следующим шагом: файл-проверка, которого больше нет, план с просроченным сроком (S 7, M 30, L 90 дней), правило без плана. Часы плана только идут вперёд: более поздняя дата, больший размер или переход на judgment-only требуют `--reason` и остаются в записи |
| `tidy` | «клерк» правил: вероятные дубликаты, правила, на которые давно никто не ссылался, основной слой сверх бюджета; только предложения, отмечаете вы |
| `rulecheck` | доказывает, что очистка правил не потеряла ни одного жёсткого факта (пути, числа, команды, ID) |
| `instinct` | необязательный список привычек, выросший из прошлых ошибок, подставляется в каждую сессию |
| `gate` | тестовый шлюз при push: `gate check` запускает `./check.sh --fast` на коммите; git-хук pre-push вызывает `gate pre-push` (по выбору, см. ниже) |
| `web-inbox` | импортирует события web-Claude из ветки `web-inbox` прямо сейчас (`--no-fetch` использует последнюю загруженную копию) |
| `guard` | хук Claude Code: одна сессия на репозиторий; отклоняет правку, если другая сессия держит репозиторий (по выбору, см. ниже) |
| `lease` | кто держит какой репозиторий (`lease status`) или освобождает его вручную (`lease release --key`) |
| `wt` | собственный git-worktree сессии: `wt start` открывает его; `wt land` вливает его в main и требует `--yes` (без него: пробный запуск) |
| `mcp` | открывает хаб любому MCP-агенту через stdio (`pip install 'aisync-cli[mcp]'`) |
| `claim` / `release` / `claims` | кто и над какими файлами сейчас работает; пересечение = код 3 |
| `news` | что делали другие инструменты с тех пор, как эта сессия в последний раз смотрела |
| `hook <tool>` | точка входа для хуков Claude Code, Codex и Cursor (читает JSON хука, никогда не падает) |
| `install-hooks` | подключает хуки, заполняет блок правил, добавляет git-хук с trailer `Agent:`; сначала показывает diff, спрашивает перед записью (`--dry-run`, `--yes`) |
| `status` | здоровье, конфликты, claim-ы, блокеры, метрики (код выхода 1, если что-то требует вашего внимания) |
| `board` | записывает хаб в одну статичную HTML-страницу: claim-ы, блокеры, кто что сделал, таймлайн |
| `report` | только чтение, самопроверка: молчащие инструменты, коммиты без trailer `Agent:`, устаревшие блокеры |
| `tail` | последние события журнала |
| `verify` | проверяет хеш-цепочку |
| `sync` / `watch` | один цикл синхронизации / цикл с дебаунсом, управляемый событиями |
| `resolve` / `resume` | решить конфликт правил / сбросить счётчики ошибок после паузы |
| `demo` | демо-хаб, показанный выше |
| `version` / `-v` / `-V` / `--version` | печатает версию |
| `help [command]` | этот экран или параметры одной команды (`aisync help log`) |
| `update` | проверяет GitHub на более новый релиз и печатает команду обновления (uv, pipx, install.sh, brew или pip); сам ничего не устанавливает |
| `doctor` | проверяет python, git, папку хаба и цепочку журнала (код выхода 1 при любой ошибке) |
| `completion` | печатает скрипт автодополнения для bash, zsh или fish: `eval "$(aisync completion zsh)"` |

## Новое в 0.2.0: правила как интерфейсы, загружаемые слоями

Файл правил растёт, пока каждая сессия не начинает за него платить, а никто его не читает. В 0.2.0 правило обрабатывается как API:

- **When / Do / Not.** Каждое правило — одна строка со стабильным ID: `[R-087] When: <trigger>. Do: <action>. Not: <forbidden>.`
  Причина уходит в журнал, а не в правило. `rule_spec = true` отклоняет всё остальное; `rule lint` перечисляет несоответствия;
  `rule rewrite` переформатирует старые правила по ID и отказывается от переписывания, которое теряет путь, число или команду.
- **Слои.** Пометьте копию инструмента `layer = "core"`: она содержит `core_topics` целиком, а каждую другую тему одной строкой.
  Когда промпт или вызов инструмента затрагивает ключевые слова темы (`[shelf]`), хук подставляет эту тему один раз за сессию.
  В собственном хабе автора это сократило постоянно загруженные правила с 20,673 до 7,876 токенов.
- **Стабильный префикс.** `progress_file` выносит живую таблицу прогресса из общей страницы, поэтому страница остаётся побайтово одинаковой, а кэши промптов продолжают срабатывать.
- **Клерк, а не уборщик.** `aisync tidy` предлагает (дубликаты, неиспользуемые правила, превышения бюджета) и никогда не редактирует;
  заменённое правило переносится в `rules-archive.md` вместе со своим ID, ничего не удаляется.
- **Любой агент.** Три входа: страница AGENTS.md, CLI и `aisync mcp` (инструменты: rules, rule_add, log, claim,
  release, news, status, tidy). Ядро использует только стандартную библиотеку; MCP SDK — необязательный extra `aisync-cli[mcp]`.

## Как это работает

<p align="center"><img src="docs/assets/how-it-works.gif" width="100%" alt="Шесть шагов: инструмент A захватывает файл, инструмент B получает отказ с кодом 3, B записывает блокер, хук сообщает A посреди сессии, A освобождает файл, B продолжает"/></p>
<p align="center"><sub><a href="https://maxont.github.io/aisync/how-it-works/">Пройдите по шагам в своём темпе</a> (воспроизведение, пауза, ← →)</sub></p>

Цикл синхронизации — это `harvest` (сбор: новые git-коммиты в отслеживаемых репозиториях становятся событиями журнала, атрибутированными по их trailer `Agent:`) → `rules` (новые записи в репозиториях из `rule_sources`) → `distribute`
(трёхсторонняя синхронизация блока правил в каждый файл инструмента) → `render` (страница прогресса) → `flush` (коммит хаба, необязательный push). Каждый шаг изолирован: один сбой никогда не блокирует остальные.

Блок правил синхронизируется трёхсторонне. Если изменилась только истина, копии перезаписываются (с резервной копией).
Если одну копию отредактировали, а никто больше ничего не менял, правка принимается везде. Если изменились обе стороны, ничего не перезаписывается: возникает конфликт, который решаете вы.

Надёжность и наблюдаемость: таймауты и повторы с backoff на каждом git-вызове, изоляция сбоев по шагам и по проектам, автопауза после `fail_threshold` сбоев подряд с `ALERT.md` и уведомлением на рабочем столе, журнал запусков по каждому циклу (`state/runs.jsonl`), статистика вызовов хуков и
`aisync status` для всего этого.

## Конфигурация

`~/.ai-sync/config.toml` (задайте `AISYNC_HOME`, чтобы разместить хаб где угодно):

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

Чтобы `aisync watch` работал постоянно, запускайте его из элементов входа в систему, юнита systemd user или агента launchd,
который выполняет `aisync watch`; чтобы остановить его чисто, создайте `~/.ai-sync/STOP`.

**Опциональные функции (все выключены по умолчанию).** `gate_hook`, `guard_hook` и `web_inbox_secs` меняют поведение за пределами хаба, поэтому ничего не включается, пока вы не задали их. Guard может отклонять правки: если сессия заблокирована, выполните
`aisync lease status`, а когда сессия-держатель завершилась, `aisync lease release --key <repo>`. Чтобы отключить guard,
удалите записи `PreToolUse` и `SessionEnd`, команда которых заканчивается на ` guard`, из `~/.claude/settings.json`.

## Опираясь на плечи

> aisync: управление контекстом мультиагентов, координация и наблюдаемость. AGENTS.md был отправной идеей.

| Проект | Как он повлиял на aisync |
|---|---|
| [![AGENTS.md stars](https://img.shields.io/github/stars/agentsmd/agents.md?style=social&label=AGENTS.md)](https://github.com/agentsmd/agents.md) | Отправная идея: один простой текстовый файл инструкций, который читают все агенты, без SDK. aisync сохраняет эту форму для общих правил (один блок правил копируется в AGENTS.md / CLAUDE.md каждого инструмента) и добавляет то, чего статичный файл не может сказать: кто что редактирует прямо сейчас и что изменилось с начала сессии. |
| [![Claude Code stars](https://img.shields.io/github/stars/anthropics/claude-code?style=social&label=Claude%20Code)](https://github.com/anthropics/claude-code) | Новости посреди сессии передаются через хуки Claude Code: `aisync install-hooks` добавляет записи SessionStart, UserPromptSubmit и PostToolUse в ~/.claude/settings.json, а `aisync hook claude-code` отвечает hookSpecificOutput.additionalContext только тогда, когда другой инструмент что-то изменил. |
| [![Codex stars](https://img.shields.io/github/stars/openai/codex?style=social&label=Codex)](https://github.com/openai/codex) | Codex читает ~/.codex/AGENTS.md, куда aisync записывает блок общих правил, а его hooks.json принимает те же события SessionStart / UserPromptSubmit / PostToolUse, поэтому `aisync hook codex` использует тот же формат ответа, что и Claude Code. |
| [Cursor](https://cursor.com/docs/agent/hooks) | Хуки Cursor (~/.cursor/hooks.json) доставляют правила и прогресс на sessionStart, а новости после каждого postToolUse через additional_context, потому что User Rules Cursor живут внутри приложения, а не в файле, который aisync мог бы синхронизировать. |
| [![git stars](https://img.shields.io/github/stars/git/git?style=social&label=git)](https://github.com/git/git) | Журнал заимствует хеш-цепочку git: каждое событие хранит id предыдущего и собственный id, который является SHA-256 от этой связи и тела, поэтому `aisync verify` замечает любую изменённую, потерянную или переставленную строку. Сам хаб — локальный git-репозиторий. |

aisync — независимый проект, не связанный с перечисленными выше.

## Разработка

```bash
python3 -m unittest discover -s tests   # includes wheel -> venv -> demo
```

Изображения сгенерированы: терминальный GIF — записанный реальный запуск
[`.github/refine/demo-terminal.json`](.github/refine/demo-terminal.json), анимация how-it-works
берётся из [`.github/refine/how-it-works.stages.json`](.github/refine/how-it-works.stages.json), а указанные выше благодарности — из [`.github/refine/lineage.json`](.github/refine/lineage.json). Все демо-данные синтетические.

Лицензия MIT.


## Ещё видео, если интересно!

https://github.com/user-attachments/assets/5ea7928b-4985-4700-8551-90477a84df40

https://github.com/user-attachments/assets/91385ac4-714c-4a16-908b-1859ef1c78e2


https://github.com/user-attachments/assets/bf661d5c-b7b1-4876-8469-2e11dd95b232



https://github.com/user-attachments/assets/d32f3abd-2986-4fbf-83a2-be287f0185a7

