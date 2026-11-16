<!-- translated from README.md; the English README is the source of truth -->
[English](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)





<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="aisync: Multi-Agent Context Management, Coordination & Observability"/>

# aisync: Multi-Agent Context Management, Coordination & Observability

**Un hub local para desarrolladores que trabajan con varios agentes de programación de IA: comparte el contexto correcto, coordina el trabajo concurrente y verifica qué pasó.**

aisync es **agnóstico de agente** en su núcleo. Ofrece hooks nativos para Claude Code, Codex y Cursor, y expone su hub a otros agentes mediante una CLI, un servidor MCP o archivos de instrucciones compartidos. Estas tres integraciones nativas **no** limitan qué agentes pueden usar aisync.

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

**English** · [中文](README.zh-CN.md) · [Website](https://maxont.github.io/aisync/) · [Demo status board](https://maxont.github.io/aisync/board/)

</div>

> [!NOTE]
> **Varios agentes de programación no deberían tener que trabajar con reglas aisladas, propiedad de archivos en conflicto e historiales desconectados.**
> aisync es un **hub local y verificable** (`~/.ai-sync/`, un repositorio git) diseñado para flujos de trabajo con varios agentes:
>
> - **Gestión de contexto:** comparte y sincroniza en 3 vías las reglas; carga el contexto central al inicio e inyecta el contexto relevante bajo demanda, incluidas las actualizaciones durante la sesión donde haya hooks disponibles.
> - **Coordinación entre agentes:** declara reclamaciones de archivos, detecta reclamaciones que se solapan (código de salida 3), libera la propiedad y muestra bloqueos y la actividad de los otros agentes.
> - **Observabilidad y verificación:** inspecciona la actividad, los bloqueos y el estado de los agentes mediante el diario, los informes de estado y el tablero; `aisync verify` comprueba la cadena de hashes del diario para detectar manipulaciones.
>
> Las reclamaciones de archivos son **cooperativas**, no bloqueos impuestos por el sistema operativo: los agentes deben usar el protocolo de reclamaciones. La verificación de la cadena de hashes detecta cambios en los eventos registrados; no demuestra que cada acción reclamada haya ocurrido.

## Para quién es

Desarrolladores que ejecutan **dos o más agentes de programación de IA** en uno o varios repositorios, sobre todo cuando los agentes trabajan a la vez, se pasan tareas o necesitan un registro compartido de decisiones y bloqueos. aisync no es otro agente de programación ni un orquestador de tareas: es la **capa compartida de contexto y coordinación** que esos agentes pueden usar.

## Véalo en acción

<div align="center">

### The Agent Swarm Floor

<img width="705" height="875" alt="cover" src="https://github.com/user-attachments/assets/cee23378-2379-49a5-960b-ac63764bb95f" />



https://github.com/user-attachments/assets/f741ef6f-0c10-46f4-8c94-769c6e02d30e




## Integraciones: los hooks nativos no son el límite de compatibilidad

| Integración | Quién puede usarla | Qué esperar |
|---|---|---|
| **Hooks nativos** | Claude Code, Codex, Cursor | Entrega automática de contexto y novedades en los eventos de hook compatibles, más acceso a los comandos de coordinación y diario de aisync |
| **Servidor MCP** | Agentes compatibles con MCP | Expone reglas, adición de reglas, registro, reclamaciones, liberaciones, novedades, estado y limpieza; el agente debe invocar las herramientas |
| **CLI** | Agentes que pueden ejecutar comandos de shell | Acceso directo a las operaciones del hub, como `aisync claim`, `aisync news`, `aisync log` y `aisync verify` |
| **Archivos de instrucciones compartidos** | Agentes que leen archivos de instrucciones compatibles | Solo reglas y contexto permanentes; no incluye novedades automáticas durante la sesión ni cumplimiento de reclamaciones de archivos |

**Ser compatible no equivale a una integración automática.** En agentes sin hooks nativos, las funciones dependen de cómo el agente use el MCP o la CLI y de su configuración. aisync no intercepta automáticamente todas las ediciones ni garantiza que cada agente respete las reclamaciones de archivos.

<p align="center"><img src="docs/assets/cli.png" alt="Running aisync in a terminal: a gradient AISYNC pixel logo, then every command grouped as start, every session, look around, keep the rules lean, keep it running" width="720"></p>

## Instalación

**Dásela a su IA.** Lee la página, hace primero una prueba en seco y pide permiso antes de cambiar nada:

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**O desde una terminal.** Se instala con uv, pipx o pip y nunca edita su configuración de shell:

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Requiere Python 3.11+ y git, en macOS o Linux. Nada más: aisync usa solo la biblioteca estándar.

**📦 O desde un gestor de paquetes.** El comando sigue siendo `aisync`:

```bash
pipx install aisync-cli           # 🐍 PyPI (or: uv tool install aisync-cli)
npx @mingyang2026/aisync-cli --version   # 📦 npm (0.2.7 now; 0.2.8 pending)
brew install maxont/tap/aisync    # 🍺 Homebrew tap (Python 3.14 first)
docker run --rm ghcr.io/maxont/aisync --version   # 🐳 GHCR image
```


### Dos agentes, un archivo

<img src="docs/assets/demo.gif" width="100%" alt="A real terminal: aisync demo prints a hub's status, claude-code claims app/api/refunds.py, codex claims the same file and is refused with OVERLAP and exit 3, codex logs a blocker, claude-code hears it through aisync news, releases the file, and codex's claim then succeeds"/>

<sub>Una ejecución real en terminal: `aisync demo` (datos sintéticos), y luego dos herramientas intentan tocar el mismo archivo.
Paso a paso: [cómo funciona](https://maxont.github.io/aisync/how-it-works/) · el mismo hub de demostración como
[tablero de estado](https://maxont.github.io/aisync/board/).</sub>

</div>

| Necesidad multiagente | Lo que aporta aisync |
|---|---|
| **Gestión de contexto** | Reglas sincronizadas en 3 vías, carga de reglas por capas y novedades relevantes durante la sesión mediante hooks |
| **Coordinación entre agentes** | Reclamaciones y liberaciones de archivos cooperativas, detección de solapamientos (código 3), bloqueos y actualizaciones de actividad |
| **Observabilidad y verificación** | Diario de eventos con cadena de hashes, `aisync verify`, `status`, `report`, `tail` y un `board` estático |

aisync también se encarga de la parte de las reglas: un bloque de reglas copiado en el archivo de reglas de cada herramienta, sincronizado en 3 vías, sin sobrescribir nunca los conflictos.

**Usado en casa:** más de 6.700 eventos en el diario del hub del autor.

## ¿Por qué no solo AGENTS.md?

Mantenga AGENTS.md para las instrucciones permanentes de un repositorio. aisync va junto a él y cubre lo que un solo archivo no puede: el contexto que cambia mientras los agentes trabajan.

| | AGENTS.md | aisync |
|---|---|---|
| Alcance | Instrucciones permanentes para agentes que trabajan en un repositorio | Un hub compartido entre los agentes configurados y varios repositorios |
| Cuándo se lee | Al iniciar la sesión | Al inicio, y durante la sesión mediante hooks |
| Quién edita qué | No lo cubre | Reclamaciones cooperativas: un solapamiento devuelve el código 3 a quien llama |
| Evidencia de manipulación | No la ofrece el formato del archivo | Diario con cadena de hashes: `aisync verify` detecta alteraciones de los eventos registrados |
| Contexto por sesión | Todo el archivo, siempre | Solo las reglas activadas: reglas siempre cargadas de 20.673 → 7.876 tokens* |

<sub>\* Medido una vez en el hub propio de un autor (2026-11-08), reglas siempre cargadas antes y después de la carga por capas. Una máquina, no un benchmark: sus cifras serán distintas. `aisync tidy` informa de su propio presupuesto de reglas y de las reglas sin citar.</sub>

## Uso

```bash
aisync demo            # a throwaway hub with a synthetic two-week journal
aisync init            # create ~/.ai-sync (or wherever AISYNC_HOME points)
aisync install-hooks   # shows every tool config change as a diff, then asks
aisync watch &         # event-driven sync loop (or run `aisync sync` by hand)
```

Lo que hace un agente durante una sesión:

```bash
aisync claim   -a codex -p api -f app/payments/ -m "SDK upgrade"  # 3 = taken
aisync log     -a codex -p api -m "SDK 5.0, retries kept"
aisync rule add -a codex --when "changing an API" --do "add a contract test"
aisync release -a codex -p api
```

| Comando | Qué hace |
|---|---|
| `init` | crea el hub: reglas compartidas, diario, configuración, repositorio git local |
| `log` | añade un evento (`dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`) |
| `rule add` / `rule list` | cualquier agente añade una regla compartida (`--when/--do/--not`, `--topic`, `--replaces R-012`); la copia de cada herramienta la recibe en la siguiente sincronización |
| `rule lint` / `rule rewrite` / `rule enforcement` | reglas que no tienen la forma When/Do/Not / reescribirlas por ID sin perder ningún dato / qué prueba o hook cumple cada regla |
| `rule enforce` | indica qué cumple una regla existente: `rule enforce R-012 --enforced-by tests/test_x.py`, `--judgment-only "<why>"`, o `--planned "<the check to write>" --size S\|M\|L` para una comprobación posible que aún no está escrita. `rule enforcement` lista después cada una pendiente con su siguiente paso: un archivo de cumplimiento que ya no existe, un plan con los días vencidos (S 7, M 30, L 90), una regla sin plan. El reloj de un plan solo avanza: una fecha posterior, un tamaño mayor o el cambio a solo juicio requiere `--reason` y queda registrado |
| `tidy` | secretario de reglas: posibles duplicados, reglas que nadie citó recientemente, capa central por encima del presupuesto; solo propone, usted marca |
| `rulecheck` | demuestra que una limpieza de reglas no perdió ningún dato duro (rutas, números, comandos, IDs) |
| `instinct` | lista opcional de hábitos construida a partir de errores pasados, inyectada en cada sesión |
| `gate` | puerta de pruebas al hacer push: `gate check` ejecuta `./check.sh --fast` sobre un commit; el hook pre-push de git llama a `gate pre-push` (opt-in, ver abajo) |
| `web-inbox` | importa los eventos de Claude web desde la rama `web-inbox` ahora (`--no-fetch` usa la última copia descargada) |
| `guard` | hook de Claude Code: una sesión por repositorio; deniega una edición cuando otra sesión tiene el repositorio (opt-in, ver abajo) |
| `lease` | quién tiene cada repositorio (`lease status`), o liberarlo a mano (`lease release --key`) |
| `wt` | el worktree de git propio de una sesión: `wt start` lo abre; `wt land` lo integra en main y necesita `--yes` (sin él: una simulación) |
| `mcp` | sirve el hub a cualquier agente MCP por stdio (`pip install 'aisync-cli[mcp]'`) |
| `claim` / `release` / `claims` | quién trabaja en qué archivos en este momento; solapamiento = código 3 |
| `news` | lo que hicieron otras herramientas desde la última vez que esta sesión miró |
| `hook <tool>` | punto de entrada para los hooks de Claude Code, Codex y Cursor (lee el JSON del hook, nunca falla) |
| `install-hooks` | conecta los hooks, siembra el bloque de reglas, añade el hook de cabecera git `Agent:`; muestra primero las diferencias y pide confirmación antes de escribir (`--dry-run`, `--yes`) |
| `status` | estado, conflictos, reclamaciones, bloqueos, métricas (código de salida 1 cuando algo requiere su atención) |
| `board` | escribe el hub como una sola página HTML estática: reclamaciones, bloqueos, quién hizo qué, cronología |
| `report` | autocomprobación de solo lectura: herramientas silenciosas, commits sin cabecera `Agent:`, bloqueos obsoletos |
| `tail` | últimos eventos del diario |
| `verify` | comprueba la cadena de hashes |
| `sync` / `watch` | ejecuta un ciclo de sincronización / el bucle con rebote, dirigido por eventos |
| `resolve` / `resume` | resuelve un conflicto de reglas / limpia los contadores de fallos tras una pausa |
| `demo` | el hub de demostración mostrado arriba |
| `version` / `-v` / `-V` / `--version` | muestra la versión |
| `help [command]` | esta pantalla, o las opciones de un comando (`aisync help log`) |
| `update` | busca en GitHub una versión más reciente y muestra el comando que la actualiza (uv, pipx, install.sh, brew o pip); nunca se instala solo |
| `doctor` | comprueba python, git, la carpeta del hub y la cadena del diario (código de salida 1 ante cualquier fallo) |
| `completion` | muestra un script de autocompletado para bash, zsh o fish: `eval "$(aisync completion zsh)"` |

## Novedades en 0.2.0: reglas como interfaces, cargadas por capas

Un archivo de reglas crece hasta que cada sesión lo paga y nadie lo lee. La 0.2.0 trata una regla como una API:

- **When / Do / Not.** Cada regla es una línea con un ID estable: `[R-087] When: <trigger>. Do: <action>. Not: <forbidden>.`
  El porqué va al diario, no a la regla. `rule_spec = true` rechaza cualquier otra forma; `rule lint` lista las que no encajan;
  `rule rewrite` reformatea reglas antiguas por ID y rechaza una reescritura que pierda una ruta, un número o un comando.
- **Capas.** Marque una copia de herramienta con `layer = "core"`: lleva `core_topics` completos y cada otro tema como una sola línea.
  Cuando un prompt o una llamada a herramienta toca las palabras clave de un tema (`[shelf]`), el hook inyecta ese tema una vez por sesión.
  En el hub del autor esto redujo las reglas siempre cargadas de 20.673 a 7.876 tokens.
- **Un prefijo estable.** `progress_file` saca la tabla de progreso en vivo de la página compartida, así la página sigue siendo idéntica byte a byte y las cachés de prompts siguen acertando.
- **Un secretario, no un conserje.** `aisync tidy` propone (duplicados, reglas sin citar, presupuesto excedido) y nunca edita;
  una regla reemplazada pasa a `rules-archive.md` con su ID; nada se borra.
- **Cualquier agente.** Tres puertas: la página AGENTS.md, la CLI y `aisync mcp` (herramientas: rules, rule_add, log, claim,
  release, news, status, tidy). El núcleo solo usa la biblioteca estándar; el SDK de MCP es el extra opcional `aisync-cli[mcp]`.

## Cómo funciona

<p align="center"><img src="docs/assets/how-it-works.gif" width="100%" alt="Six steps: tool A claims a file, tool B is refused with exit 3, B logs a blocker, a hook tells A mid-session, A releases, B proceeds"/></p>
<p align="center"><sub><a href="https://maxont.github.io/aisync/how-it-works/">Recórralo a su ritmo</a> (reproducir, pausar, ← →)</sub></p>

Un ciclo de sincronización es `harvest` (los nuevos commits de git en los repositorios vigilados se convierten en eventos del diario, atribuidos por
su cabecera `Agent:`) → `rules` (nuevas entradas en los repositorios que liste en `rule_sources`) → `distribute`
(sincronización en 3 vías del bloque de reglas en cada archivo de herramienta) → `render` (página de progreso) → `flush` (commit del
hub, push opcional). Cada paso está aislado: un fallo nunca bloquea a los demás.

El bloque de reglas se sincroniza en 3 vías. Si solo cambió la fuente de verdad, las copias se sobrescriben (con copia de seguridad).
Si una copia fue editada y nadie más cambió nada, la edición se adopta en todas partes. Si ambos lados cambiaron,
no se sobrescribe nada: queda como conflicto para que usted lo resuelva.

Fiabilidad y observabilidad: tiempos de espera y reintentos con retroceso en cada llamada a git, aislamiento de fallos por paso y por proyecto, pausa automática tras `fail_threshold` fallos seguidos con un `ALERT.md` y una notificación de escritorio, un registro de ejecución por ciclo (`state/runs.jsonl`), estadísticas de llamadas a hooks y `aisync status` para todo ello.

## Configuración

`~/.ai-sync/config.toml` (defina `AISYNC_HOME` para colocar el hub en cualquier lugar):

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

Para mantener `aisync watch` en marcha, iníciela desde sus elementos de inicio de sesión, una unidad de usuario de systemd o un
agente de launchd que ejecute `aisync watch`; cree `~/.ai-sync/STOP` para detenerlo limpiamente.

**Funciones opcionales (todas desactivadas por defecto).** `gate_hook`, `guard_hook` y `web_inbox_secs` cambian el comportamiento fuera del hub,
así que no se activa nada hasta que usted lo configure. El guard puede denegar ediciones: si una sesión queda bloqueada, ejecute
`aisync lease status`, y, cuando la sesión que la tiene haya terminado, `aisync lease release --key <repo>`. Para desactivar el guard,
borre las entradas `PreToolUse` y `SessionEnd` cuyo comando termine en ` guard` de `~/.claude/settings.json`.

## Sobre los hombros de

> aisync: Multi-Agent Context Management, Coordination & Observability. AGENTS.md fue la idea de partida.

| Proyecto | Cómo dio forma a aisync |
|---|---|
| [![AGENTS.md stars](https://img.shields.io/github/stars/agentsmd/agents.md?style=social&label=AGENTS.md)](https://github.com/agentsmd/agents.md) | La idea de partida: un archivo de instrucciones en texto plano que todo agente lee, sin SDK. aisync mantiene esa forma para las reglas compartidas (un bloque de reglas copiado en el AGENTS.md / CLAUDE.md de cada herramienta) y añade lo que un archivo estático no puede decir: quién edita qué ahora mismo, y qué cambió desde que empezó la sesión. |
| [![Claude Code stars](https://img.shields.io/github/stars/anthropics/claude-code?style=social&label=Claude%20Code)](https://github.com/anthropics/claude-code) | Las novedades durante la sesión viajan por los hooks de Claude Code: `aisync install-hooks` añade entradas SessionStart, UserPromptSubmit y PostToolUse a ~/.claude/settings.json, y `aisync hook claude-code` responde con hookSpecificOutput.additionalContext solo cuando otra herramienta cambió algo. |
| [![Codex stars](https://img.shields.io/github/stars/openai/codex?style=social&label=Codex)](https://github.com/openai/codex) | Codex lee ~/.codex/AGENTS.md, donde aisync siembra el bloque de reglas compartidas, y su hooks.json acepta los mismos eventos SessionStart / UserPromptSubmit / PostToolUse, así que `aisync hook codex` reutiliza el formato de respuesta de Claude Code. |
| [Cursor](https://cursor.com/docs/agent/hooks) | Los hooks de Cursor (~/.cursor/hooks.json) entregan las reglas y el progreso en sessionStart y las novedades tras cada postToolUse mediante additional_context, porque las User Rules de Cursor viven dentro de la aplicación y no en un archivo que aisync pueda sincronizar. |
| [![git stars](https://img.shields.io/github/stars/git/git?style=social&label=git)](https://github.com/git/git) | El diario toma prestada la cadena de hashes de git: cada evento guarda el id del anterior y su propio id es un SHA-256 de ese enlace más su contenido, así que `aisync verify` detecta cualquier línea editada, perdida o reordenada. El hub en sí es un repositorio git local. |

aisync es un proyecto independiente, no afiliado a los proyectos anteriores.

## Desarrollo

```bash
python3 -m unittest discover -s tests   # includes wheel -> venv -> demo
```

Las imágenes están generadas: el GIF de terminal es una ejecución real grabada de
[`.github/refine/demo-terminal.json`](.github/refine/demo-terminal.json), la animación de cómo funciona sale de
[`.github/refine/how-it-works.stages.json`](.github/refine/how-it-works.stages.json), y los créditos de arriba de
[`.github/refine/lineage.json`](.github/refine/lineage.json). Todos los datos de demostración son sintéticos.

Licencia MIT.


## Más vídeos, si quiere

https://github.com/user-attachments/assets/5ea7928b-4985-4700-8551-90477a84df40

https://github.com/user-attachments/assets/91385ac4-714c-4a16-908b-1859ef1c78e2


https://github.com/user-attachments/assets/bf661d5c-b7b1-4876-8469-2e11dd95b232



https://github.com/user-attachments/assets/d32f3abd-2986-4fbf-83a2-be287f0185a7

