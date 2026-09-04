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
> e esta VIVA, e as camadas **episodica/semantica**, que nao existem.

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

Nenhum grafo escreve ou le memoria:

```
grep -rn 'store_episodic\|recall_semantic\|read_write_memory' src/maezo/agents/   -> 0 linhas
```

O servidor MCP que atenderia essas declaracoes recusa fail-closed **toda** chamada:
`src/maezo/tools/mcp_memory/server.py::MemoryServer.store_episodic` levanta
`EpisodicMemoryUnavailableError` e `::MemoryServer.recall_semantic` levanta
`SemanticMemoryUnavailableError`, ambas com o motivo citado no proprio codigo: nenhuma migracao cria
as tabelas que a implementacao doadora mirava (`episodic_events`, `semantic_memory`), e
`recall_semantic` devolvia `similarity: 1.0` constante independentemente da query. As oito migracoes
existentes (`src/maezo/platform/migrations/versions/0001..0008`) nao criam nenhuma das duas nem
habilitam `pgvector`.

Nenhum codigo de producao instancia `MemoryServer`:

```
grep -rn 'MemoryServer' src/ tests/ | grep -v src/maezo/tools/mcp_memory/
```

devolve apenas docstrings dos grafos (que ja divulgam a ausencia honestamente — ex.
`src/maezo/agents/helena/graph.py:69-70`, `carolina/graph.py:104`) e
`tests/unit/tools/test_mcp_memory.py`. **Nao ha composition root que o registre.**

O resultado: a fila de dimensao D8 (Memoria) do fleet marca 1.9/5 de media, e um leitor da spec
conclui que os agentes tem memoria de longo prazo. Nao tem.

### A confusao que este ADR desfaz: a camada de TRABALHO existe e esta viva

ADR-0002 define TRES camadas (trabalho / episodica / semantica). Somente as duas ultimas estao
mortas. A camada de **trabalho** foi entregue no T4b e esta em producao:
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

### Bloqueio declarado

`DU-01-b` (schema Postgres/pgvector do `MemoryServer`) e uma dependencia **externa nao iniciada**;
o pacote `WP-MEMORIA-PGVECTOR` esta parcial. A auditoria a lista em "Dependencias externas
declaradas (nao mudam o agente; so o desbloqueiam)". Nenhuma perna de agente destrava esse item.

### O que ja foi implementado como perna agent-buildable

A unica perna que um agente pode construir sem decidir infra e a **desdeclaracao provisoria**:
alinhar `spec/agents/*/agent.yaml` com a arvore ate a decisao, de modo que a spec pare de prometer o
que nao existe. Essa perna esta atribuida a `fleet/w3-docs-sweep` (**nao mergeada**); na leitura
feita para redigir este ADR o branch ainda estava na base (`git diff origin/main..HEAD` vazio), de
modo que a desdeclaracao **ainda nao existe em nenhum branch** e este ADR nao pode cita-la por
simbolo. O ADR fica valido de qualquer forma: a desdeclaracao e uma medida provisoria, nao a decisao.

---

## Decisao proposta

**Escolhemos, de forma explicita e datada, entre ATIVAR e APOSENTAR a memoria episodica/semantica; e,
ate a escolha valer na arvore, a spec dos agentes declara apenas o que o codigo exerce.**

### Opcoes

| Opcao | Descricao | Custo | Consequencia sobre a spec |
|---|---|---|---|
| **A — Ativar por flag de build** | Construir DU-01-b (migracao com `episodic_events` + `semantic_memory` sobre `pgvector`), registrar `MemoryServer` no composition root e habilitar a escrita episodica **por flag de build por agente**, com o default DESLIGADO. `memory.*` no `agent.yaml` volta a ser verdade onde a flag esta ligada | Alto (infra + LGPD: episodios sao dado pessoal, entram na cascata de erasure do ADR-0002 e na retencao de `spec/policies/retention/`) | `memory.episodic/semantic` viram `true` de novo, agente a agente, conforme a flag |
| **B — Aposentar o `MemoryServer`** | Deletar `src/maezo/tools/mcp_memory/`, remover `mcp-memory.read_write` e `read_write_memory` das 11 definicoes e das allowlists, e registrar em ADR que a Fase atual opera com memoria de TRABALHO apenas | Baixo, e reversivel (o codigo vive no historico) | Bloco `memory:` some ou fica `episodic: false, semantic: false` |
| C — Status quo | Manter as declaracoes e o servidor que recusa | Zero imediato | Spec continua prometendo o que nao existe; D8 continua 1.9/5 |

**Recomendacao de engenharia (NAO VINCULANTE): A ou B, nunca C** — e a escolha entre A e B e do dono,
porque depende de roadmap (a memoria episodica e requisito de alguma capacidade de Fase 1?) e de
apetite de LGPD (episodios de decisao sobre beneficiario sao dado pessoal com finalidade propria), nao
de engenharia. Se a resposta for "sim, mas nao agora", a forma honesta disso e **B com um ADR de
reintroducao no roadmap**, nao C.

### Medida provisoria, valida em qualquer das opcoes

Ate a opcao valer na arvore, `spec/agents/*/agent.yaml` declara `memory.episodic: false` e
`memory.semantic: false`, e `mcp-memory.read_write` / `read_write_memory` saem das listas de tools e
actions (ou ganham marcador explicito de nao-exercido). A medida e **provisoria e reversivel**, e
existe por um motivo unico: uma spec que promete capacidade inexistente e pior do que uma spec que
declara a lacuna — e a mesma licao que `AgentDefinition.model` carregava antes do AF-12
(`src/maezo/agents/__init__.py:291-296`: «Dead config that LOOKS live is worse than absent config»).

---

## Consequencias

**Positivas**
- A spec volta a ser um documento confiavel sobre o que o fleet faz.
- Na opcao A, `MemoryServer` deixa de ser codigo morto que recusa; na opcao B, deixa de existir.
- A separacao trabalho vs episodica/semantica fica registrada, e uma auditoria futura nao volta a
  contar a camada viva como ausente (nem a ausente como viva).

**Negativas (aceitas)**
- Opcao A: episodios de decisao sobre beneficiario sao dado pessoal — entram na cascata de erasure por
  `fhir_patient_id` (ADR-0002), na politica de retencao (`spec/policies/retention/`, dono-gated) e no
  perimetro de zona PHI (ADR-0006). O custo real de A e regulatorio, nao de engenharia.
- Opcao B: um agente novo que precise de recall de casos similares tera de reabrir a decisao. Aceito;
  reabrir com ADR e barato, e manter promessa falsa e caro.
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

1. **A ou B** — construir DU-01-b e ligar a memoria episodica/semantica por flag, ou aposentar o
   `MemoryServer`. C (status quo) nao e oferecida como resultado aceitavel.
2. **Se A: o perimetro de LGPD** — retencao dos episodios, cascata de erasure, e se o episodio vive em
   Zona PHI ou Geral. Toca `spec/policies/retention/` e `spec/policies/privacy/` (ambos dono-gated,
   `.github/CODEOWNERS:116,134`).
3. **Se A: a granularidade da flag** — por agente, por tenant, ou global; e qual e o default (a
   recomendacao de engenharia e default DESLIGADO).
4. **Se B: o destino da declaracao** — bloco `memory:` removido dos 11 yamls ou mantido com ambos
   `false` e um comentario de disclosure.
5. **A medida provisoria (desdeclaracao) entra ja, ou espera a decisao 1?**

---

## Criterio de aceite testavel

**Comum as duas opcoes**
1. Cerca de paridade spec-vs-arvore: para cada `spec/agents/*/agent.yaml`, `memory.episodic == true`
   implica pelo menos uma chamada de escrita de memoria alcancavel no grafo daquele agente. Hoje o
   teste e VERMELHO em 11/11; apos a medida provisoria e VERDE por vacuidade; apos a opcao A e VERDE
   por substancia.
2. Nenhum `agent.yaml` declara `mcp-memory.read_write` sem que a tool esteja registrada num
   composition root vivo.

**Se A**
3. Migracao nova cria `episodic_events` e `semantic_memory` e habilita `pgvector`;
   `MemoryServer.store_episodic` grava e retorna o id gravado; `recall_semantic` devolve
   similaridades **variaveis** com a query (o defeito da implementacao doadora — `similarity: 1.0`
   constante — nao pode reaparecer; teste com duas queries distintas exigindo scores distintos).
4. Com a flag DESLIGADA, o grafo se comporta byte-a-byte como hoje (golden Tier-A inalterado).
5. Teste de particao: nenhum campo derivado de memoria recuperada alcanca uma variavel de decisao do
   estado do agente — memoria alimenta contexto, nunca `desfecho`/`route`/`recomendacao_auto`.
6. Erasure: apagar um `fhir_patient_id` remove os episodios correspondentes (teste de cascata).

**Se B**
7. `grep -rn 'mcp-memory\|MemoryServer\|store_episodic\|recall_semantic' src/ spec/` retorna zero
   linhas fora de docstrings de disclosure historica, e o pacote `src/maezo/tools/mcp_memory/` nao
   existe.

## Supersedes

—. **Reconcilia e sequencia** `ADR-0002` (tres camadas de estado); nao supersede nem emenda. Relacionado
a `ADR-0022` (MCP in-process), `ADR-0006` (zonas PHI) e as politicas de retencao/erasure.
