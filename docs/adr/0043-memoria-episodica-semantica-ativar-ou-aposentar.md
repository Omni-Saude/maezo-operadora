# ADR-0043: Memoria episodica/semantica declarada e nao exercida — ativar por flag de build ou aposentar o `MemoryServer` (ADR-P-004 / CC-07, HEL-09, AF-11, DU-01-b)

**Status:** **Proposed — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(`adr-drafter`, R1) no programa de remediacao do Agent Fleet Audit; um agente nao ratifica ADR.
`/docs/adr/` e dono-gated (`.github/CODEOWNERS:61`). · **Data:** 2026-09-04
· **Area:** Dados / Arquitetura de agentes / Infra

> **Enquadramento.** Este ADR NAO decide se memoria de longo prazo e desejavel — ADR-0002 ja decidiu
> que sim. Ele resolve uma **divergencia entre a spec declarada e a arvore**, que hoje faz 11 arquivos
> `agent.yaml` prometerem uma capacidade que zero grafos exercem, e pede ao dono a escolha entre
> construir a infra que a torna verdadeira ou aposentar o servidor que a promete. Ele tambem separa,
> explicitamente, duas coisas que a auditoria e facil de confundir: a **camada de trabalho**, que EXISTE
> e esta VIVA, e as camadas **episodica/semantica**, cujo substrato de banco existe (`pgvector` +
> `agent_memory.embedding`) mas que nao tem ESCRITOR nenhum — nao e o mesmo que "nao existem", e a
> diferenca muda o custo das duas opcoes.

---

## Contexto

### O achado (CC-07, P2; HEL-09, RAF-08, FER-02, VAL-03)

Os onze arquivos de definicao de agente (10 agentes + `_template`) declaram um bloco `memory:`:

```
grep -c 'memory:' spec/agents/*/agent.yaml      -> 11 arquivos, 1 bloco cada
```

Todos os onze declaram `episodic: true`; dez declaram tambem `semantic: true` (a excecao e
`spec/agents/_template/agent.yaml`, que declara `semantic: false`). Os onze tambem declaram a tool
`mcp-memory.read_write` e a action `read_write_memory`.

**Divergencia registrada em relacao ao relatorio de auditoria (leitura do disco prevalece).** O
relatorio enuncia CC-07 como «Memoria declarada em **10/10** `agent.yaml`» (snapshot:121), contando
apenas os dez agentes. A contagem do disco e **11/11**, porque `spec/agents/_template/agent.yaml`
tambem declara o bloco — e o `_template` importa mais do que os outros dez neste achado: ele e o
molde de onde o proximo agente nasce, de modo que a promessa se auto-replica se ficar la. A
desdeclaracao provisoria e as opcoes abaixo cobrem os onze, nao os dez.

Nenhum grafo escreve ou le memoria:

```
grep -rn 'store_episodic\|recall_semantic\|read_write_memory' src/maezo/agents/   -> 0 linhas
```

O servidor MCP que atenderia essas declaracoes recusa fail-closed **toda** chamada:
`src/maezo/tools/mcp_memory/server.py::MemoryServer.store_episodic` levanta
`EpisodicMemoryUnavailableError` (motivo `REASON_EPISODIC_SCHEMA_DRIFT`) e
`::MemoryServer.recall_semantic` levanta `SemanticMemoryUnavailableError` (motivo
`REASON_SEMANTIC_SEARCH_NOT_WIRED`), ambas com a justificativa escrita no proprio codigo. A
implementacao doadora mirava tabelas (`episodic_events`, `semantic_memory`) que nenhuma migracao
cria, e `recall_semantic` devolvia `similarity: 1.0` constante independentemente da query — a
fabricacao foi removida em GAP-DU-01-a.

### O que falta NAO e o substrato de banco

Este ponto precisa ser exato, porque um ADR que errasse aqui mandaria construir o que ja existe. A
extensao `pgvector` **esta habilitada** e a relacao de memoria **existe** desde a primeira migracao:

```
sed -n '28p;64p;72p' src/maezo/platform/migrations/versions/0001_schema_agents.py
28:    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
64:        CREATE TABLE IF NOT EXISTS agent_memory (
72:            embedding         vector(1536),
```

`src/maezo/platform/migrations/versions/0001_schema_agents.py` habilita `pgvector` em `public`
(`:28`, DL-0017 — tipo/operador sao DB-globais) e cria `agent_memory` (`:64-75`) com
`tenant_id`, `agent_id`, `thread_id`, `fhir_patient_id`, `event_type`, `payload` e
`embedding vector(1536)` (`:72`). E `0006_retire_dead_checkpoint_tables.py` **nao** retira nada disso:
seus dois `DROP TABLE` sao `agent_checkpoint_writes` e `agent_checkpoints` (`:68-69`), ambos da camada
de TRABALHO.

O que falta sao **duas outras coisas**:

1. **As tabelas da implementacao doadora** — `episodic_events` e `semantic_memory` — que migracao
   nenhuma cria. Os nomes simplesmente **divergem do schema vivo**: a relacao real e `agent_memory`, e
   a camada semantica nao e uma relacao separada, e a coluna `embedding` da MESMA linha.
2. **Um ESCRITOR.** `agent_memory` nao tem nenhum writer em `src/`, `embedding` nunca e populado, nao
   ha indice de similaridade (`grep -rn 'ivfflat\|hnsw' src/maezo` -> 0 linhas) e nenhuma chamada a
   `runtime.inference` computa o vetor. O proprio `MemoryServer` ja registra as duas coisas
   (`server.py::REASON_EPISODIC_SCHEMA_DRIFT`, `::REASON_SEMANTIC_SEARCH_NOT_WIRED` e as mensagens de
   recusa dos dois metodos) — inclusive o detalhe que impede um wire trivial: a assinatura
   `store_episodic(agent_id, event)` **nao carrega** `tenant_id` nem `thread_id`, ambos `text NOT NULL`
   em `agent_memory`, e inventar um placeholder violaria o isolamento multi-tenant do ADR-0002.

E, do lado do consumidor, nenhum codigo de producao instancia `MemoryServer`:

```
grep -rn 'MemoryServer' src/ tests/ | grep -v src/maezo/tools/mcp_memory/
```

devolve apenas docstrings dos grafos (que ja divulgam a ausencia honestamente — ex.
`src/maezo/agents/helena/graph.py:69-70`, `carolina/graph.py:104`) e
`tests/unit/tools/test_mcp_memory.py`. **Nao ha composition root que o registre.**

A consequencia para as opcoes abaixo e direta: **nenhuma delas comeca por "instalar pgvector"**. O
resultado hoje: a fila de dimensao D8 (Memoria) do fleet marca 1.9/5 de media, e um leitor da spec
conclui que os agentes tem memoria de longo prazo. Nao tem — ha substrato sem escritor, que e uma
coisa diferente de "nao ha nada".

### A confusao que este ADR desfaz: a camada de TRABALHO existe e esta viva

ADR-0002 define TRES camadas (trabalho / episodica / semantica). Somente as duas ultimas estao sem
escritor. A camada de **trabalho** foi entregue no T4b e esta em producao:
`src/maezo/runtime/checkpoint.py::Checkpointer` (LangGraph checkpointer duravel sobre Postgres)
compilado e invocado por
`src/maezo/platform/webhooks/whatsapp/dispatch.py::HelenaDispatcher.dispatch`
(`compiled = graph.compile(checkpointer=saver)`, `dispatch.py:363-364`), sob thread config PHI-safe
(`checkpoint_thread_config`), com recusa-a-servir quando o checkpointer nao esta provisionado
(`platform/webhooks/service.py::_provision_dispatch_checkpointer` -> `/webhook` 501).

**Nao confundir as duas coisas.** Um pedido de "ligar a memoria" que na verdade quer multi-turn de
conversa ja esta atendido. O que falta e persistencia de EPISODIOS (decisoes passadas do agente) e
recall SEMANTICO (casos similares) — capacidades diferentes, com dependencias de infra diferentes
e com implicacoes de LGPD proprias.

### Bloqueio declarado — e ele e exatamente a decisao que este ADR pede

`GAP-DU-01-b` nao e "infra pendente": e uma **decisao de dono, severidade P0, aberta**, com este
enunciado — *ligar o caminho pgvector (embedding + indice de similaridade) ou REMOVER a extensao, a
coluna e a referencia Aurora, por migracao nova*. O pacote `WP-MEMORIA-PGVECTOR` esta parcial: a
perna `-a` (retirar o score fabricado e reconciliar `episodic_events`/`semantic_memory` vs
`agent_memory`) ja esta em `main`; a perna `-b` e a que continua aberta, e nenhuma perna de agente a
destrava. O proprio codigo aponta para ela como decisao de dono: a docstring de modulo de
`src/maezo/tools/mcp_memory/server.py` diz que a escolha entre wire e aposentadoria «is an OWNER
decision (GAP-DU-01-b) and is deliberately NOT made here», e as duas mensagens de recusa repetem
«(or retiring this tool) is GAP-DU-01-b».

Consequencia de forma para este ADR: as opcoes abaixo **sao** as duas metades de `GAP-DU-01-b`.
Escolher A ou B fecha esse P0; nao escolher o mantem aberto.

### O que ja foi implementado como perna agent-buildable

A unica perna que um agente pode construir sem decidir infra e a **desdeclaracao provisoria**:
alinhar `spec/agents/*/agent.yaml` com a arvore ate a decisao, de modo que a spec pare de prometer o
que nao existe. Essa perna **existe e esta escrita**, em `fleet/w3-docs-sweep` (**nao mergeada**):

```
git diff --name-only origin/main...fleet/w3-docs-sweep -- spec/agents/ tests/
spec/agents/{_template,andre,beatriz,carolina,fernando,gustavo,helena,lucas,marina,rafael,valentina}/agent.yaml
tests/unit/platform/test_agent_memory_declaration_fence.py
```

Os onze `agent.yaml` passam a declarar `episodic: false` / `semantic: false`, cada linha com o
comentario de reabilitacao («reabilitar quando DU-01-b/AF-11 ligar pgvector»), e a cerca nova
`tests/unit/platform/test_agent_memory_declaration_fence.py` a torna permanente por dois testes
parametrizados: `::test_agent_yaml_does_not_declare_live_memory` e
`::test_agent_yaml_memory_block_is_the_explicit_honest_pair`.

Um ponto de escopo que importa para a Parte seguinte: esse branch **nao toca** `mcp-memory.read_write`
nem `read_write_memory` — a desdeclaracao se limita aos flags `memory:`
(`git diff origin/main...fleet/w3-docs-sweep -- spec/agents/ | grep 'mcp-memory\|read_write_memory'`
-> nenhuma linha). Isso e deliberado, e a razao esta na medida provisoria abaixo.

---

## Decisao proposta

**Escolhemos, de forma explicita e datada, entre ATIVAR e APOSENTAR a memoria episodica/semantica; e,
ate a escolha valer na arvore, a spec dos agentes declara apenas o que o codigo exerce.**

### Opcoes

| Opcao | Descricao | Custo | Consequencia sobre a spec |
|---|---|---|---|
| **A — Ativar por flag de build** | Wire sobre o schema que JA existe: escrever em `agent_memory` (nao criar `episodic_events`/`semantic_memory` — ou, se os nomes doadores forem preferidos, reconcilia-los por migracao), o que exige alargar `store_episodic` para carregar `tenant_id`/`thread_id`, popular `embedding` por chamada a `runtime.inference`, criar o indice de similaridade (hoje `0 hits` para `ivfflat\|hnsw`), registrar `MemoryServer` num composition root e habilitar a escrita **por flag de build por agente**, default DESLIGADO | Alto (LGPD: episodios sao dado pessoal, entram na cascata de erasure do ADR-0002 e na retencao de `spec/policies/retention/`). **Nao** inclui instalar `pgvector` nem criar a coluna: ambos ja estao em `0001_schema_agents.py:28,72` | `memory.episodic/semantic` viram `true` de novo, agente a agente, conforme a flag |
| **B — Aposentar o `MemoryServer`** | Deletar `src/maezo/tools/mcp_memory/`, retirar `mcp-memory.read_write`/`read_write_memory` das 11 definicoes **junto com** a excecao divulgada da cerca (ver Parte provisoria) e registrar em ADR que a Fase atual opera com memoria de TRABALHO apenas | Baixo no codigo do agente — **mas B sozinho NAO fecha `GAP-DU-01-b`** (ver a coluna de ressalva abaixo da tabela) | Bloco `memory:` some ou fica `episodic: false, semantic: false` |
| C — Status quo | Manter as declaracoes e o servidor que recusa | Zero imediato | Spec continua prometendo o que nao existe; D8 continua 1.9/5 |

**Ressalva sobre a opcao B, que muda o seu custo real.** Aposentar o `MemoryServer` remove o
CONSUMIDOR, nao o SUBSTRATO: a extensao `pgvector` (`0001:28`), a coluna `agent_memory.embedding
vector(1536)` (`0001:72`) e a tabela inteira continuam na base — e, com elas, o custo de superficie no
Aurora. E precisamente essa disposicao — «ligar o caminho de embedding **ou** remover extensao,
coluna e referencia Aurora por migracao nova» — que constitui `GAP-DU-01-b`, o P0 aberto. Logo:

> **B sem migracao de remocao deixa `GAP-DU-01-b` ABERTO.** Para que B feche o P0, ela tem de vir
> acompanhada de uma migracao nova (a proxima da serie, `0009`) que retire a coluna e a extensao, e
> essa migracao tem de reconhecer que `agent_memory` esta declarada no plano de erasure (abaixo). B
> "so deletando o pacote Python" e uma limpeza parcial que se apresenta como decisao fechada.

**Recomendacao de engenharia (NAO VINCULANTE): A ou B, nunca C** — e a escolha entre A e B e do dono,
porque depende de roadmap (a memoria episodica e requisito de alguma capacidade de Fase 1?) e de
apetite de LGPD (episodios de decisao sobre beneficiario sao dado pessoal com finalidade propria), nao
de engenharia. Se a resposta for "sim, mas nao agora", a forma honesta disso e **B com um ADR de
reintroducao no roadmap**, nao C.

### Medida provisoria, valida em qualquer das opcoes — e o que ela NAO pode fazer

Ate a opcao valer na arvore, `spec/agents/*/agent.yaml` declara `memory.episodic: false` e
`memory.semantic: false`. E **so isso** — exatamente o escopo de `fleet/w3-docs-sweep`. A medida e
**provisoria e reversivel**, e existe por um motivo unico: uma spec que promete capacidade
inexistente e pior do que uma spec que declara a lacuna — e a mesma licao que `AgentDefinition.model`
carregava antes do AF-12 (`src/maezo/agents/__init__.py:292-298`: «Dead config that LOOKS live is
worse than absent config»).

**Retirar `mcp-memory.read_write` dos onze yamls, isoladamente, QUEBRA o CI.** Esse id nao e um
resto: e a UNICA excecao divulgada do item 2 da cerca de chokepoint de efeitos, e a cerca verifica
que a excecao continua sendo exercida pela arvore real —

```
sed -n '926p;1034,1038p' scripts/ci/check_effect_chokepoint_fence.py
926:_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS: Final[frozenset[str]] = frozenset({"mcp-memory.read_write"})
1034:    if "mcp-memory.read_write" not in declared_tool_ids:
         violations.append("§8.5 item 2: mcp-memory.read_write is declared as the disclosed tool-id
         exception but no agent.yaml actually declares it — the exception has gone stale")
```

Ou seja: `scripts/ci/check_effect_chokepoint_fence.py::_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS` (`:926`) e
a checagem de staleness (`:1034-1038`) ficam VERMELHAS se nenhum `agent.yaml` declarar o id. A
excecao existe porque `src/maezo/gateway/effect_classes.py:38-47` a divulga como «KNOWN GAP,
DISCLOSED»: a tool e declarada por 11 dos 11 yamls e mapeia para a acao ratificada
`read_write_memory` (L3), mas o §6.1 do design nao declara classe de efeito para memoria, e
classificar e «decisao humana, nao inferencia de agente» — a mesma reserva que
`spec/policies/autonomy/action-approvals.yaml:640-643` ja aplica aos topicos de worker nao mapeados.

Retirar o id, portanto, e um movimento de **tres partes simultaneas**: os onze yamls, a cerca em
`scripts/ci/check_effect_chokepoint_fence.py` (dono-gated, `.github/CODEOWNERS:223-242`) e a
divulgacao em `src/maezo/gateway/effect_classes.py` (nao dono-gated por CODEOWNERS, mas o que ela
divulga e uma classificacao que o proprio texto reserva a um humano). Aposentar a excecao divulgada e
decisao do DONO, listada abaixo; **nao** e parte da medida provisoria.

---

## Consequencias

**Positivas**
- A spec volta a ser um documento confiavel sobre o que o fleet faz.
- Na opcao A, `MemoryServer` deixa de ser codigo morto que recusa; na opcao B, deixa de existir.
- A separacao trabalho vs episodica/semantica fica registrada, e uma auditoria futura nao volta a
  contar a camada viva como ausente (nem a ausente como viva).

**Negativas (aceitas)**
- Opcao A: episodios de decisao sobre beneficiario sao dado pessoal — entram na politica de retencao
  (`spec/policies/retention/`, dono-gated) e no perimetro de zona PHI (ADR-0006). O custo real de A e
  regulatorio, nao de engenharia. **Mas a cascata de erasure ja esta mapeada, e isso reduz o custo de
  A**: `src/maezo/platform/lifecycle/erasure_plan.py` ja carrega DUAS camadas para esta memoria — a
  episodica (`tabela="agent_memory"`, `:338`) e a semantica (`tabela="agent_memory.embedding"`,
  `:351`), ambas com `subject_column="fhir_patient_id"` e um `count_statement` pronto. O que A precisa
  fazer nao e desenhar a cascata: e mover a `resolucao` dessas duas linhas de
  `IdentityResolution.PONTE_AUSENTE` (a coluna de titular existe, mas a referencia que o processo DSR
  carrega nao a alcanca) para `RESOLVIVEL` — construindo a ponte, nao a relacao.
- Opcao B: um agente novo que precise de recall de casos similares tera de reabrir a decisao. Aceito;
  reabrir com ADR e barato, e manter promessa falsa e caro. **E, no lado do dado:** as duas linhas
  acima do plano de erasure passam a descrever tabela/coluna inexistentes e tem de ser retiradas na
  mesma migracao de remocao — caso contrario o plano de erasure passa a mentir na direcao oposta.
- A medida provisoria altera 11 arquivos de spec e, por um periodo, o `agent.yaml` divergira de
  ADR-0002 (que decide as tres camadas). **Isso e um registro honesto da lacuna, nao uma emenda a
  ADR-0002**: nenhuma clausula de ADR-0002 e revogada aqui.

---

## Invariantes preservados

- **D2 (particao determinismo/LLM):** intacta. Memoria e substrato de contexto, nunca fonte de regra
  de negocio; nenhuma opcao aqui permite que um episodio recuperado DECIDA (regra dura 5 de
  `AGENTS.md:31`). Na opcao A isso vira criterio de aceite (abaixo).
- **Fail-closed:** o comportamento atual de `MemoryServer` (recusa tipada com motivo citado, nunca
  resultado fabricado nem `similarity` constante) e o padrao correto e permanece em A; em B o
  servidor deixa de existir, o que e igualmente fail-closed.
- **Regra dura 7 (AGENTS.md:33):** intacta. Memoria nao toca `clinical_decision`,
  `authorization_denial`, `nip_manter_negativa`, `fraud_accusation` nem `contract_termination`
  (`spec/policies/autonomy/L0-core.yaml:6-13`).
- **ADR-0002:** este ADR **reconcilia e sequencia** a implementacao das tres camadas; nao supersede
  nem emenda a decisao das camadas.
- **ADR-0022 (MCP in-process no boot):** a opcao A registra `MemoryServer` in-process como qualquer
  outro MCP server; nao introduz Deployment stdio/SSE out-of-process.

---

## O que e decisao do DONO (explicito)

1. **A ou B** — ligar a memoria episodica/semantica por flag sobre o schema que ja existe, ou aposentar
   o `MemoryServer`. As duas SAO as metades de `GAP-DU-01-b` (P0, aberto). C (status quo) nao e
   oferecida como resultado aceitavel.
2. **Se B: entra ou nao a migracao de remocao** — a proxima da serie (`0009`), retirando
   `agent_memory.embedding`, a extensao `pgvector` e as duas linhas correspondentes de
   `src/maezo/platform/lifecycle/erasure_plan.py`. Sem ela, B fecha o codigo Python e **deixa o P0
   aberto**.
3. **A aposentadoria da excecao divulgada `mcp-memory.read_write`** — retirar o id dos onze yamls
   exige, no MESMO commit, retirar `_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS` de
   `scripts/ci/check_effect_chokepoint_fence.py` (dono-gated, `.github/CODEOWNERS:223-242`) e a
   divulgacao «KNOWN GAP» de `src/maezo/gateway/effect_classes.py`. A alternativa e o dono
   **classificar** a acao de memoria no §6.1 do design — o ato humano que a propria excecao esta
   esperando. Qualquer dos dois e decisao do dono; nenhum e agent-buildable.
4. **Se A: o perimetro de LGPD** — retencao dos episodios, e se o episodio vive em Zona PHI ou Geral.
   Toca `spec/policies/retention/` e `spec/policies/privacy/` (ambos dono-gated,
   `.github/CODEOWNERS:116,134`). A cascata de erasure em si ja esta mapeada (ver Consequencias): o
   que falta e a ponte de identidade, nao o desenho.
5. **Se A: a granularidade da flag** — por agente, por tenant, ou global; e qual e o default (a
   recomendacao de engenharia e default DESLIGADO).
6. **Se B: o destino da declaracao** — bloco `memory:` removido dos 11 yamls ou mantido com ambos
   `false` e um comentario de disclosure.
7. **A medida provisoria (desdeclaracao dos flags `memory:`) entra ja, ou espera a decisao 1?** Ela ja
   esta escrita em `fleet/w3-docs-sweep` e nao depende de nenhuma das outras.

---

## Criterio de aceite testavel

**Comum as duas opcoes**
1. Cerca de paridade spec-vs-arvore: para cada `spec/agents/*/agent.yaml`, `memory.episodic == true`
   implica pelo menos uma chamada de escrita de memoria alcancavel no grafo daquele agente. Os tres
   estados sao distintos e devem ser lidos como tres, nao como um:
   - **VERMELHO na base `origin/main`** — 11/11 declaram `true` e 0 grafos exercem;
   - **VERDE por VACUIDADE em `fleet/w3-docs-sweep`** — 11/11 declaram `false`, a implicacao nao tem
     antecedente, e a cerca ja existe la
     (`tests/unit/platform/test_agent_memory_declaration_fence.py::test_agent_yaml_does_not_declare_live_memory`).
     Verde por vacuidade e honestidade, **nao** capacidade;
   - **VERDE por SUBSTANCIA** — so depois da opcao A, quando um `true` voltar a existir e for
     sustentado por uma escrita alcancavel.
2. Nenhum `agent.yaml` declara `mcp-memory.read_write` sem que **ou** a tool esteja registrada num
   composition root vivo, **ou** o id conste da excecao divulgada da cerca
   (`scripts/ci/check_effect_chokepoint_fence.py::_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS`). Hoje vale o
   segundo ramo, e a propria cerca verifica a reciproca (`:1034-1038`: a excecao fica VERMELHA se
   nenhum yaml declarar o id). O criterio so passa a exigir o primeiro ramo depois que o dono
   aposentar a excecao — os dois lados tem de se mover no mesmo commit.

**Se A**
3. `MemoryServer.store_episodic` grava em `agent_memory` — a relacao que ja existe — e retorna o id
   gravado, com `tenant_id` e `thread_id` REAIS na assinatura (nunca placeholder). **Nao** e criterio
   criar `episodic_events`/`semantic_memory` nem habilitar `pgvector`: a extensao ja esta em
   `0001_schema_agents.py:28` e a coluna em `:72`. Se a migracao nova preferir os nomes doadores, ela
   e uma RECONCILIACAO de nomes e tem de migrar/retirar `agent_memory` no mesmo passo — nao pode
   deixar as duas formas coexistindo.
4. `recall_semantic` devolve similaridades **variaveis** com a query, calculadas sobre
   `agent_memory.embedding` populado por chamada real a `runtime.inference` (o defeito da
   implementacao doadora — `similarity: 1.0` constante — nao pode reaparecer; teste com duas queries
   distintas exigindo scores distintos), e existe indice de similaridade (`ivfflat`/`hnsw`), hoje
   inexistente.
5. Com a flag DESLIGADA, o grafo se comporta byte-a-byte como hoje (golden Tier-A inalterado).
6. Teste de particao: nenhum campo derivado de memoria recuperada alcanca uma variavel de decisao do
   estado do agente — memoria alimenta contexto, nunca `desfecho`/`route`/`recomendacao_auto`.
7. Erasure: apagar um `fhir_patient_id` remove os episodios correspondentes (teste de cascata), e as
   duas linhas de `platform/lifecycle/erasure_plan.py` para `agent_memory` e
   `agent_memory.embedding` deixam de ser `IdentityResolution.PONTE_AUSENTE`.

**Se B**
8. `grep -rn 'mcp-memory\|MemoryServer\|store_episodic\|recall_semantic' src/ spec/` retorna zero
   linhas fora de docstrings de disclosure historica, e o pacote `src/maezo/tools/mcp_memory/` nao
   existe. **Isso sozinho nao basta**: a excecao da cerca tem de sair no mesmo commit
   (`make effect-chokepoint-fence` VERDE, nao vermelho por excecao stale).
9. Se a migracao de remocao entrar (decisao 2 do dono): `grep -rn 'vector(1536)\|CREATE EXTENSION IF
   NOT EXISTS vector' src/maezo/platform/migrations/versions/` mostra a coluna e a extensao criadas em
   `0001` e RETIRADAS em `0009`, e `agent_memory`/`agent_memory.embedding` nao aparecem mais no plano
   de erasure. Sem esta linha, o criterio de B esta incompleto e `GAP-DU-01-b` permanece aberto.

## Supersedes

—. **Reconcilia e sequencia** `ADR-0002` (tres camadas de estado); nao supersede nem emenda. Relacionado
a `ADR-0022` (MCP in-process), `ADR-0006` (zonas PHI) e as politicas de retencao/erasure.
