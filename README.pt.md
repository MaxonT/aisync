<!-- translated from README.md; the English README is the source of truth -->
[English](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Português** · [Русский](README.ru.md)





<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="aisync: Multi-Agent Context Management, Coordination & Observability"/>

# aisync: Gerenciamento de Contexto, Coordenação e Observabilidade Multiagente

**Um hub local para desenvolvedores que trabalham com vários agentes de codificação com IA: compartilhe o contexto certo, coordene o trabalho concorrente e verifique o que aconteceu.**

O aisync é **agnóstico de agente em sua essência**. Ele oferece hooks nativos para Claude Code, Codex e Cursor, e expõe seu hub para outros agentes por meio de uma CLI, de um servidor MCP ou de arquivos de instrução compartilhados. Essas três integrações nativas **não** limitam quais agentes podem usar o aisync.

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

**English** · [中文](README.zh-CN.md) · [Site](https://maxont.github.io/aisync/) · [Painel de status da demo](https://maxont.github.io/aisync/board/)

</div>

> [!NOTE]
> **Vários agentes de codificação não deveriam precisar trabalhar com regras isoladas, posse de arquivos em conflito e históricos desconectados.**
> O aisync é um **hub local e verificável** (`~/.ai-sync/`, um repositório git) feito para fluxos de trabalho multiagente:
>
> - **Gerenciamento de contexto:** compartilha e sincroniza regras em 3 vias; carrega o contexto central no início e injeta o contexto relevante sob demanda, inclusive atualizações durante a sessão onde há hooks disponíveis.
> - **Coordenação multiagente:** declara file claims, detecta claims sobrepostos (código de saída 3), libera a posse e exibe bloqueios e a atividade de outros agentes.
> - **Observabilidade e verificação:** inspeciona a atividade, os bloqueios e a saúde dos agentes pelo journal, pelos relatórios de status e pelo board; `aisync verify` checa a cadeia de hash do journal em busca de adulteração.
>
> Os file claims são **cooperativos**, não bloqueios impostos pelo sistema operacional: os agentes precisam usar o protocolo de claims. A verificação por cadeia de hash detecta alterações nos eventos registrados; ela não prova que toda ação reivindicada de fato ocorreu.

## Para quem é

Desenvolvedores que usam **dois ou mais agentes de codificação com IA** em um ou vários repositórios, especialmente quando os agentes trabalham simultaneamente, passam tarefas adiante ou precisam de um registro compartilhado de decisões e bloqueios. O aisync não é mais um agente de codificação nem um orquestrador de tarefas: ele é a **camada compartilhada de contexto e coordenação** que esses agentes podem usar.

## Veja em ação

<div align="center">

### O chão do enxame de agentes

<img width="705" height="875" alt="cover" src="https://github.com/user-attachments/assets/cee23378-2379-49a5-960b-ac63764bb95f" />



https://github.com/user-attachments/assets/f741ef6f-0c10-46f4-8c94-769c6e02d30e




## Integrações: hooks nativos não são a fronteira de compatibilidade

| Integração | Quem pode usar | O que esperar |
|---|---|---|
| **Hooks nativos** | Claude Code, Codex, Cursor | Entrega automática de contexto e notícias nos eventos de hook suportados, além de acesso aos comandos de coordenação e de journal do aisync |
| **Servidor MCP** | Agentes compatíveis com MCP | Expõe regras, adição de regras, registro de logs, claims, releases, notícias, status e tidy; o agente precisa invocar as ferramentas |
| **CLI** | Agentes que conseguem executar comandos de shell | Acesso direto às operações do hub, como `aisync claim`, `aisync news`, `aisync log` e `aisync verify` |
| **Arquivos de instrução compartilhados** | Agentes que leem arquivos de instrução suportados | Somente regras e contexto permanentes; sem notícias automáticas durante a sessão nem imposição de file claims |

**Compatibilidade não é o mesmo que integração automática.** Para agentes sem hooks nativos, os recursos dependem do uso e da configuração de MCP/CLI pelo agente. O aisync não intercepta automaticamente todas as edições nem garante que todo agente vai respeitar os file claims.

<p align="center"><img src="docs/assets/cli.png" alt="Running aisync in a terminal: a gradient AISYNC pixel logo, then every command grouped as start, every session, look around, keep the rules lean, keep it running" width="720"></p>

## Instalação

**Entregue para a sua IA.** Ela lê a página, faz primeiro uma simulação (dry run) e pede confirmação antes de alterar qualquer coisa:

```text
Read https://maxont.github.io/aisync/install and install aisync for me.
```

**Ou em um terminal.** Ele instala com uv, pipx ou pip e nunca edita o seu arquivo de configuração do shell:

```bash
curl -fsSL https://maxont.github.io/aisync/install.sh | bash
```

Requer Python 3.11+ e git, em macOS ou Linux. Nada além disso: o aisync usa apenas a biblioteca padrão.

**📦 Ou por um gerenciador de pacotes.** O comando continua sendo `aisync`:

```bash
pipx install aisync-cli           # 🐍 PyPI (or: uv tool install aisync-cli)
npx @mingyang2026/aisync-cli --version   # npm 0.2.8 (0.2.9 pending)
brew install maxont/tap/aisync    # 🍺 Homebrew tap (Python 3.14 first)
docker run --rm ghcr.io/maxont/aisync --version   # 🐳 GHCR image
```


### Dois agentes, um arquivo

<img src="docs/assets/demo.gif" width="100%" alt="A real terminal: aisync demo prints a hub's status, claude-code claims app/api/refunds.py, codex claims the same file and is refused with OVERLAP and exit 3, codex logs a blocker, claude-code hears it through aisync news, releases the file, and codex's claim then succeeds"/>

<sub>Uma execução real no terminal: `aisync demo` (dados sintéticos), e então duas ferramentas tentam acessar o mesmo arquivo.
Passo a passo: [como funciona](https://maxont.github.io/aisync/how-it-works/) · o mesmo hub de demonstração como
[painel de status](https://maxont.github.io/aisync/board/).</sub>

</div>

| Necessidade multiagente | O que o aisync oferece |
|---|---|
| **Gerenciamento de contexto** | Regras sincronizadas em 3 vias, carregamento de regras em camadas e notícias relevantes durante a sessão via hooks |
| **Coordenação multiagente** | File claims e releases cooperativos, detecção de sobreposição (saída 3), bloqueios e atualizações de atividade |
| **Observabilidade e verificação** | Journal de eventos com encadeamento de hash, `aisync verify`, `status`, `report`, `tail` e um `board` estático |

O aisync também cuida da parte de regras: um único bloco de regras copiado para o arquivo de regras de cada ferramenta, sincronizado em 3 vias, com conflitos nunca sobrescritos.

**Usado internamente:** mais de 6.700 eventos no journal do hub do próprio autor.

## Por que não apenas AGENTS.md?

Mantenha o AGENTS.md para as instruções permanentes de um repositório. O aisync fica ao lado dele e cobre o que um único arquivo não consegue: o contexto que muda enquanto os agentes trabalham.

| | AGENTS.md | aisync |
|---|---|---|
| Escopo | Instruções permanentes para agentes que trabalham em um repositório | Um hub compartilhado entre os agentes e repositórios configurados |
| Quando é lido | No início da sessão | No início e durante a sessão, via hooks |
| Quem está editando o quê | Não coberto | File claims cooperativos: uma sobreposição devolve a saída 3 ao chamador |
| Evidência contra adulteração | Não fornecida pelo formato do arquivo | Journal com encadeamento de hash: `aisync verify` detecta alterações nos eventos registrados |
| Contexto por sessão | Arquivo inteiro, sempre | Somente regras acionadas: regras sempre carregadas de 20,673 → 7,876 tokens* |

<sub>\* Medido uma vez no hub do próprio autor (2026-11-08), com as regras sempre carregadas antes e depois do carregamento em camadas. Uma máquina, não um benchmark: os seus números serão diferentes. `aisync tidy` informa o seu próprio orçamento de regras e as regras que não são citadas.</sub>

## Como usar

```bash
aisync demo            # a throwaway hub with a synthetic two-week journal
aisync init            # create ~/.ai-sync (or wherever AISYNC_HOME points)
aisync install-hooks   # shows every tool config change as a diff, then asks
aisync watch &         # event-driven sync loop (or run `aisync sync` by hand)
```

O que um agente faz durante uma sessão:

```bash
aisync claim   -a codex -p api -f app/payments/ -m "SDK upgrade"  # 3 = taken
aisync log     -a codex -p api -m "SDK 5.0, retries kept"
aisync rule add -a codex --when "changing an API" --do "add a contract test"
aisync release -a codex -p api
```

| Comando | O que faz |
|---|---|
| `init` | cria o hub: regras compartilhadas, journal, configuração, repositório git local |
| `log` | acrescenta um evento (`dev_log`, `decision`, `blocked`, `unblocked`, `rule_change`, `note`) |
| `rule add` / `rule list` | qualquer agente adiciona uma regra compartilhada (`--when/--do/--not`, `--topic`, `--replaces R-012`); a cópia de cada ferramenta recebe a regra na próxima sincronização |
| `rule lint` / `rule rewrite` / `rule enforcement` | regras fora do formato When/Do/Not / reescreve regras pelo ID sem perder nenhum fato / mostra qual teste ou hook mantém cada regra |
| `rule enforce` | informa o que sustenta uma regra existente: `rule enforce R-012 --enforced-by tests/test_x.py`, `--judgment-only "<why>"`, ou `--planned "<the check to write>" --size S\|M\|L` para uma verificação possível e ainda não escrita. Depois, `rule enforcement` lista cada item em aberto com o próximo passo: um arquivo de enforcement que já não existe, um plano com prazo vencido (S 7, M 30, L 90 dias), uma regra sem plano. O relógio do plano só anda para frente: uma data posterior, um tamanho maior ou a troca para judgment-only exigem `--reason` e ficam registrados |
| `tidy` | "escriturário" de regras: prováveis duplicatas, regras que ninguém citou recentemente, camada core acima do orçamento; apenas propõe, você marca |
| `rulecheck` | prova que uma limpeza de regras não perdeu nenhum fato importante (caminhos, números, comandos, IDs) |
| `instinct` | lista opcional de hábitos, formada a partir de erros passados, injetada em cada sessão |
| `gate` | portão de testes no push: `gate check` executa `./check.sh --fast` em um commit; o hook git pre-push chama `gate pre-push` (opt-in, veja abaixo) |
| `web-inbox` | importa agora os eventos do Claude web da branch `web-inbox` (`--no-fetch` usa a última cópia buscada) |
| `guard` | hook do Claude Code: uma sessão por repositório; nega uma edição quando outra sessão detém o repositório (opt-in, veja abaixo) |
| `lease` | mostra quem detém qual repositório (`lease status`), ou libera um manualmente (`lease release --key`) |
| `wt` | worktree git próprio de uma sessão: `wt start` o abre; `wt land` o incorpora à main e exige `--yes` (sem ele: uma simulação) |
| `mcp` | serve o hub para qualquer agente MCP via stdio (`pip install 'aisync-cli[mcp]'`) |
| `claim` / `release` / `claims` | quem está trabalhando em quais arquivos agora; sobreposição = saída 3 |
| `news` | o que as outras ferramentas fizeram desde a última vez que esta sessão olhou |
| `hook <tool>` | ponto de entrada para os hooks do Claude Code, Codex e Cursor (lê o JSON do hook e nunca falha) |
| `install-hooks` | conecta os hooks, semeia o bloco de regras, adiciona o hook do trailer `Agent:` do git; mostra o diff primeiro e pede confirmação antes de escrever (`--dry-run`, `--yes`) |
| `status` | saúde, conflitos, claims, bloqueios, métricas (saída 1 quando algo precisa de você) |
| `board` | escreve o hub como uma única página HTML estática: claims, bloqueios, quem fez o quê, linha do tempo |
| `report` | autoverificação somente leitura: ferramentas silenciosas, commits sem trailer `Agent:`, bloqueios obsoletos |
| `tail` | eventos mais recentes do journal |
| `verify` | verifica a cadeia de hash |
| `sync` / `watch` | executa um ciclo de sincronização / o loop com debounce, orientado a eventos |
| `resolve` / `resume` | decide um conflito de regras / zera os contadores de falha após uma pausa |
| `demo` | o hub descartável mostrado acima |
| `version` / `-v` / `-V` / `--version` | imprime a versão |
| `help [command]` | esta tela, ou as opções de um comando (`aisync help log`) |
| `update` | verifica no GitHub se há uma versão mais nova e imprime o comando que faz a atualização (uv, pipx, install.sh, brew ou pip); nunca instala sozinho |
| `doctor` | verifica python, git, a pasta do hub e a cadeia do journal (saída 1 em qualquer falha) |
| `completion` | imprime um script de autocompletar para bash, zsh ou fish: `eval "$(aisync completion zsh)"` |

## Novidade na 0.2.0: regras como interfaces, carregadas em camadas

Um arquivo de regras cresce até que toda sessão pague por ele e ninguém o leia. A 0.2.0 trata uma regra como uma API:

- **When / Do / Not.** Cada regra é uma linha com um ID estável: `[R-087] When: <trigger>. Do: <action>. Not: <forbidden>.`
  O porquê vai para o journal, não para a regra. `rule_spec = true` recusa qualquer outro formato; `rule lint` lista os fora do padrão;
  `rule rewrite` reformata regras antigas pelo ID e recusa uma reescrita que perca um caminho, um número ou um comando.
- **Camadas.** Marque a cópia de uma ferramenta com `layer = "core"`: ela carrega `core_topics` por completo e cada outro tópico como uma única linha.
  Quando um prompt ou uma chamada de ferramenta toca nas palavras-chave de um tópico (`[shelf]`), o hook injeta esse tópico uma vez por sessão.
  No hub do próprio autor, isso reduziu as regras sempre carregadas de 20,673 para 7,876 tokens.
- **Um prefixo estável.** `progress_file` tira a tabela de progresso ao vivo da página compartilhada, então a página fica byte a byte igual e os caches de prompt continuam acertando.
- **Um escriturário, não um faxineiro.** `aisync tidy` propõe (duplicatas, regras sem citação, orçamento estourado) e nunca edita;
  uma regra substituída vai para `rules-archive.md` com o seu ID, e nada é apagado.
- **Qualquer agente.** Três portas: a página AGENTS.md, a CLI e `aisync mcp` (ferramentas: rules, rule_add, log, claim,
  release, news, status, tidy). O núcleo usa apenas a biblioteca padrão; o SDK do MCP é o extra opcional `aisync-cli[mcp]`.

## Como funciona

<p align="center"><img src="docs/assets/how-it-works.gif" width="100%" alt="Six steps: tool A claims a file, tool B is refused with exit 3, B logs a blocker, a hook tells A mid-session, A releases, B proceeds"/></p>
<p align="center"><sub><a href="https://maxont.github.io/aisync/how-it-works/">Veja passo a passo no seu ritmo</a> (play, pause, ← →)</sub></p>

Um ciclo de sincronização é `harvest` (novos commits git nos repositórios observados viram eventos do journal, atribuídos pelo trailer `Agent:`) → `rules` (novas entradas nos repositórios listados em `rule_sources`) → `distribute`
(sincronização em 3 vias do bloco de regras em cada arquivo de ferramenta) → `render` (página de progresso) → `flush` (commit do hub, com push opcional). Cada etapa é isolada: uma falha nunca bloqueia as outras.

O bloco de regras é sincronizado em 3 vias. Se só a versão de referência mudou, as cópias são sobrescritas (com backup).
Se uma cópia foi editada e ninguém mais mudou nada, a edição é adotada em todos os lugares. Se os dois lados mudaram,
nada é sobrescrito: vira um conflito para você resolver.

Confiabilidade e observabilidade: timeouts e novas tentativas com backoff em cada chamada git, isolamento de falhas por etapa e por projeto, pausa automática após `fail_threshold` falhas seguidas com um `ALERT.md` e uma notificação no desktop, um log de execução por ciclo (`state/runs.jsonl`), estatísticas de chamadas de hook e `aisync status` para tudo isso.

## Configuração

`~/.ai-sync/config.toml` (defina `AISYNC_HOME` para colocar o hub em outro lugar):

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

Para manter o `aisync watch` em execução, inicie-o pelos itens de login, por uma unidade de usuário do systemd ou por um agente launchd que execute `aisync watch`; crie `~/.ai-sync/STOP` para pará-lo de forma limpa.

**Recursos opcionais (todos desligados por padrão).** `gate_hook`, `guard_hook` e `web_inbox_secs` mudam o comportamento fora do hub,
então nada é ativado até que você os defina. O guard pode negar edições: se uma sessão ficar bloqueada, execute
`aisync lease status` e, quando a sessão que detém o repositório tiver terminado, `aisync lease release --key <repo>`. Para desfazer o guard,
apague as entradas `PreToolUse` e `SessionEnd` cujo comando termina em ` guard` de `~/.claude/settings.json`.

## Apoiado nos ombros de gigantes

> aisync: Gerenciamento de Contexto, Coordenação e Observabilidade Multiagente. O AGENTS.md foi a ideia inicial.

| Projeto | Como moldou o aisync |
|---|---|
| [![AGENTS.md stars](https://img.shields.io/github/stars/agentsmd/agents.md?style=social&label=AGENTS.md)](https://github.com/agentsmd/agents.md) | A ideia inicial: um único arquivo de instruções em texto puro que todo agente lê, sem SDK. O aisync mantém esse formato para as regras compartilhadas (um bloco de regras copiado para o AGENTS.md / CLAUDE.md de cada ferramenta) e acrescenta o que um arquivo estático não consegue dizer: quem está editando o quê agora e o que mudou desde o início da sessão. |
| [![Claude Code stars](https://img.shields.io/github/stars/anthropics/claude-code?style=social&label=Claude%20Code)](https://github.com/anthropics/claude-code) | As notícias durante a sessão usam os hooks do Claude Code: `aisync install-hooks` adiciona entradas SessionStart, UserPromptSubmit e PostToolUse em ~/.claude/settings.json, e `aisync hook claude-code` responde com hookSpecificOutput.additionalContext somente quando outra ferramenta mudou algo. |
| [![Codex stars](https://img.shields.io/github/stars/openai/codex?style=social&label=Codex)](https://github.com/openai/codex) | O Codex lê ~/.codex/AGENTS.md, e é ali que o aisync semeia o bloco de regras compartilhadas; o hooks.json dele aceita os mesmos eventos SessionStart / UserPromptSubmit / PostToolUse, então `aisync hook codex` reaproveita o formato de resposta do Claude Code. |
| [Cursor](https://cursor.com/docs/agent/hooks) | Os hooks do Cursor (~/.cursor/hooks.json) entregam as regras e o progresso no sessionStart e as notícias após cada postToolUse por meio de additional_context, porque as User Rules do Cursor vivem dentro do aplicativo, e não em um arquivo que o aisync possa sincronizar. |
| [![git stars](https://img.shields.io/github/stars/git/git?style=social&label=git)](https://github.com/git/git) | O journal toma emprestada a cadeia de hash do git: cada evento guarda o id do anterior e o próprio id é um SHA-256 desse vínculo mais o corpo, então `aisync verify` identifica qualquer linha editada, perdida ou reordenada. O próprio hub é um repositório git local. |

O aisync é um projeto independente, não afiliado aos projetos acima.

## Desenvolvimento

```bash
python3 -m unittest discover -s tests   # includes wheel -> venv -> demo
```

As imagens são geradas: o GIF do terminal é uma execução real gravada de
[`.github/refine/demo-terminal.json`](.github/refine/demo-terminal.json), a animação de como funciona vem de
[`.github/refine/how-it-works.stages.json`](.github/refine/how-it-works.stages.json), e os créditos acima vêm de
[`.github/refine/lineage.json`](.github/refine/lineage.json). Todos os dados de demonstração são sintéticos.

Licença MIT.


## Mais vídeos, se quiser!

https://github.com/user-attachments/assets/5ea7928b-4985-4700-8551-90477a84df40

https://github.com/user-attachments/assets/91385ac4-714c-4a16-908b-1859ef1c78e2


https://github.com/user-attachments/assets/bf661d5c-b7b1-4876-8469-2e11dd95b232



https://github.com/user-attachments/assets/d32f3abd-2986-4fbf-83a2-be287f0185a7

