# ADR-0045: Fail-notify canonico da falha de `start_process` — desfecho `erro_inicio_processo` como requisito do contrato canonico (ADR-P-007 / CC-01, RAF-02, LUC-05, marina)

**Status:** **Proposed — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(`adr-drafter`, R1) no programa de remediacao do Agent Fleet Audit; um agente nao ratifica ADR.
`/docs/adr/` e dono-gated (`.github/CODEOWNERS:61`). · **Data:** 2026-09-04
· **Area:** Orquestracao / Fail-safe / Compliance

> **Enquadramento.** A familia de risco P1 numero 1 do fleet audit. Este ADR nao introduz mecanismo
> novo de engine: o desfecho proposto e **estado do AGENTE**, nao do engine — precisamente porque o
> engine e o que falhou. Ele eleva a requisito do contrato canonico um padrao que ja esta
> implementado no `_template` e em propagacao para os nove grafos, e pede ao dono a politica de
> retry/alerta que o padrao deve disparar.

---

## Contexto

### O achado (CC-01, P1; RAF-02, LUC-05, marina)

Oito grafos tem um no `start_process` (`andre`, `carolina`, `fernando`, `gustavo`, `lucas`,
`marina`, `rafael`, `valentina`) e Helena tem o equivalente em
`src/maezo/agents/helena/graph.py::HelenaGraph._start_escalation`. Todos chamam o chokepoint
`src/maezo/tools/mcp_cibseven/transport.py::start_process_idempotent` e todos tratam a falha da
mesma forma:

```python
except CibSevenError as exc:
    return {
        "process_started": False,
        "business_key": business_key,
        "error": f"start_process indisponivel: {exc}",
    }
```

(`rafael/graph.py::RafaelGraph.start_process`, `lucas/graph.py::LucasGraph.start_process`,
`marina/graph.py::MarinaGraph.start_process`, e homologos.)

O dicionario devolvido nao vai a lugar nenhum. A aresta que sai de `start_process` e
**incondicional** para um no terminal em todos os oito:

```
grep -n 'add_edge("start_process' src/maezo/agents/*/graph.py
carolina:823  -> "finalize"     andre:1251 -> "finalize"      fernando:873 -> END
marina:1044   -> "finalize"     gustavo:947 -> "finalize"     lucas:857 -> "complete"
valentina:906 -> "finalize"     rafael:763 -> "complete"
```

e `complete`/`finalize` sao terminais no-op (ex.: `rafael/graph.py::RafaelGraph.complete` —
«Terminal node — no further computation; `desfecho` was already set by `assess`»).

**O resultado e um fail-open processual:** a instancia de processo que nao nasceu desaparece sem SLA,
sem alerta e sem retry, enquanto o `desfecho` gravado a montante permanece como se tudo tivesse dado
certo.

### Por que e mais grave do que "um turno perdido"

1. **O beneficiario ja foi informado.** Em Lucas, o ACK «um humano vai continuar» e enviado ANTES do
   start (o proprio `LucasGraph.start_process` documenta a versao anterior desse defeito, corrigida
   pelo fix F1b); em Helena, `_start_escalation` chama `self._respond_llm(...)` e monta a resposta
   antes do bloco `try` que inicia o processo. O beneficiario recebe a promessa de um humano enquanto
   zero instancias existem no engine.

2. **Em Rafael o falso sucesso e IRRETENTAVEL por `task_id`.** O handler A2A
   (`src/maezo/agents/rafael/delegation.py`) devolve `HandlerOutput` com
   `meta={"route", "desfecho", "process_started"}` — e `HandlerOutput` **nao tem campo `success`**
   (`src/maezo/a2a/dispatcher.py:150-154`). Um turno em que o start falhou volta ao dispatcher como
   sucesso: `_audit_delegation_outcome` grava `decision=_DECISION_COMPLETED` (`dispatcher.py:114`,
   `:410`), emite o fato `COMPLETED` (`:413`) e persiste o resultado terminal no cache de
   idempotencia (`StoredResult`, `a2a_idempotency.result`). Uma reentrega do MESMO `task_id` retorna
   o resultado persistido com `idempotent_replay=True` (`dispatcher.py:345-346`) — **o handler nao
   roda de novo**. O `desfecho` fabricado (`encaminhado_auditor`) fica gravado, e o retry natural do
   A2A esta estruturalmente fechado.

3. **A telemetria nao ve.** `record_agent_error` e chamado apenas no `except` em torno do `ainvoke`
   (`delegation.py:150-166`, `runtime/harness.py`) — e o turno NAO levanta: ele retorna um dict
   normal. Logo `maezo_agent_errors_total` nao incrementa e `MaezoAgentCrashLoop`
   (`deploy/observability/alert-rules.yml:98`) nunca dispara. Do lado do transporte,
   `cibseven_start_claim_orphaned` (`transport.py:1548`) cobre a orfandade de claim, **nao** este
   caminho: aqui a excecao foi capturada e convertida em dict pelo grafo.

4. **A cerca existente nao cobre isto.** `make check-start-process-fence` proibe chamada direta ao
   engine fora do chokepoint. E uma cerca de ENTRADA. Ela nao diz nada sobre o que o grafo faz
   **depois** que o chokepoint falha — que e exatamente o buraco.

### O que ja foi implementado como perna agent-buildable

**Implementado em `fleet/template-canonical-contract` (nao mergeado)** — o padrao vive no contrato
canonico, no ponto mais baixo, nunca em N copias:

- `src/maezo/agents/_template/graph.py::DESFECHO_ERRO_INICIO_PROCESSO` — a constante
  `"erro_inicio_processo"`.
- `::TemplateGraph.notify_start_failure` — o no que grava o desfecho de erro.
- `::TemplateGraph._route_after_start` — o roteador condicional
  (`Literal["notify_start_failure", "complete"]`).
- O esqueleto canonico passou de tres arestas lineares para
  `receive -> start_process -> {notify_start_failure | complete}`, com
  `g.add_conditional_edges(...)` e `g.add_edge("notify_start_failure", END)`.

**Em propagacao para os nove grafos em `fleet/cc01-start-fail-notify` (nao mergeado).** Na leitura
feita para redigir este ADR, esse branch carregava o `_template` corrigido (via merge do trem) e a
propagacao aos demais grafos ainda nao estava presente; portanto este ADR cita por simbolo apenas o
que existe, e trata a propagacao como trabalho em curso.

**O que essa perna NAO faz** e o motivo deste ADR: ela nao pode declarar o desfecho nos contratos
`SP-OP-*` sem que o padrao seja um requisito ratificado, nem escolher a politica de retry/alerta.

---

## Decisao proposta

**A falha de `start_process` produz um desfecho contratual explicito — `erro_inicio_processo` — e um
no de notificacao, em todo agente que inicia processo. O padrao e requisito do contrato canonico
(`_template`), nao uma escolha por agente.**

### Parte 1 — o desfecho e estado do AGENTE, nao do engine

Isto e o cerne, e precisa ficar escrito para nao ser mal-implementado: **nao ha instancia de processo
para carregar o desfecho** — a instancia e justamente o que falhou em nascer. `erro_inicio_processo`
e um valor do canal `desfecho` do ESTADO do grafo, materializado em telemetria e no dossie de
handoff. Ele **nao** e um End Event de BPMN, **nao** e um `bpmnErrorCode` e **nao** entra na
`check-bpmn-error-allowlist`. Uma implementacao que tentasse aterra-lo no engine estaria pedindo ao
engine indisponivel que registre a propria indisponibilidade.

Consequencia spec-first (AGENTS.md regra dura 1): os contratos `SP-OP-*` dos agentes que iniciam
processo ganham `erro_inicio_processo` na sua enumeracao de desfechos, com a nota de que se trata de
desfecho do agente. Isso combina com o trabalho de proveniencia CC-13, ja materializado como secao
«Variaveis de proveniencia do agente (— ADR-0007/ADR-0015)» nos contratos em
`fleet/cc13-contract-provenance` (PR #314): a mesma distincao — o que e do agente vs o que e do
processo — aplicada a um desfecho em vez de a uma variavel.

### Parte 2 — a forma do padrao

Aresta condicional a partir de `start_process`, exatamente como no `_template`:

```
start_process --(process_started is False)--> notify_start_failure --> END
start_process --(caso contrario)-----------> complete/finalize
```

`notify_start_failure` grava `desfecho=erro_inicio_processo`, emite telemetria de erro, e — quando o
agente ja prometeu algo ao beneficiario — deixa o handoff em estado honesto (a promessa nao pode ser
retirada; ela pode ser registrada como pendente).

### Parte 3 — retry e alerta (as opcoes)

| Opcao | Politica | Custo | Risco |
|---|---|---|---|
| **A (recomendada)** | `notify_start_failure` chama `record_agent_error()` (fazendo `MaezoAgentCrashLoop` finalmente enxergar o caso) e o retry fica a cargo do RETRY EXISTENTE do driver/worker, sem retry novo dentro do grafo | Baixo; reusa mecanismo vivo | Requer decidir o limiar do alerta para nao gerar ruido com engine em manutencao |
| B | A + retry idempotente com backoff dentro do proprio no (o start ja e idempotente por business key, entao um retry e seguro) | Medio | O grafo passa a segurar um turno por mais tempo; interage com timeout de external task |
| C | A + uma metrica dedicada (`maezo_agent_start_process_failed_total`) e alerta proprio, separado de `MaezoAgentCrashLoop` | Medio-baixo | Mais uma serie e mais uma regra de alerta para manter |

**Recomendacao de engenharia (NAO VINCULANTE): A, com C como reforco se o dono quiser separar este
modo de falha do ruido geral de crash-loop.** B deve ser avaliada com cuidado: a idempotencia por
business key torna o retry seguro, mas prolongar o turno dentro do grafo tem interacao com timeout de
external task que precisa ser medida, nao presumida.

### Parte 4 — a cerca

Uma cerca de CI enumera os grafos que tem no de start e exige, para cada um, a aresta condicional e o
no de notificacao. Sem cerca, o decimo primeiro grafo nasce fail-open. Se a cerca morar em
`scripts/ci/` ela e dono-gated (`.github/CODEOWNERS:223-242`); se for um teste unitario em `tests/`,
nao e — e a escolha entre as duas formas e do dono.

### Parte 5 — o lado A2A (RAF-02)

O handler A2A precisa falhar de forma tipada quando `process_started` e `False`, em vez de devolver
`HandlerOutput` que o dispatcher lera como `COMPLETED`. Sem isso, a Parte 2 corrige o grafo e o
dispatcher continua gravando o falso sucesso no cache de idempotencia. As duas alternativas —
levantar do handler (o dispatcher ja tem caminho de rejeicao tipada) ou acrescentar `success` a
`HandlerOutput` — tem implicacao de contrato A2A e sao decisao do dono.

---

## Consequencias

**Positivas**
- O fail-open processual da familia P1 numero 1 deixa de existir: um processo que nao nasce vira um
  desfecho registrado, uma metrica e um alerta.
- `MaezoAgentCrashLoop` passa a enxergar a classe de falha para a qual o alerta foi escrito.
- O `_template` volta a ser o que o nome promete: um agente novo herda o padrao correto em vez de
  copiar o `except` que devolve dict e segue para o terminal.
- O falso sucesso irretentavel de Rafael (Parte 5) fica fechado no ponto certo — a fronteira A2A.

**Negativas (aceitas)**
- Todos os contratos `SP-OP-*` dos agentes que iniciam processo mudam (adicao de desfecho), o que
  exige revisao spec-first e uma passagem por `validate-artifacts`.
- Um desfecho a mais no vocabulario que evals, telemetria e dashboards precisam conhecer.
- Se a Parte 5 for implementada como raise, alguns fluxos A2A passam a ver rejeicoes que antes viam
  como sucesso — **e esse e exatamente o ponto**, mas e uma mudanca de comportamento observavel por
  consumidores.

---

## Invariantes preservados

- **D2 (particao determinismo/LLM):** intacta. `erro_inicio_processo` e determinado por uma excecao
  de transporte capturada em codigo, nunca por LLM; `notify_start_failure` nao chama modelo para
  decidir nada.
- **Fail-closed:** este ADR **converte um fail-open em fail-closed**. Nenhum caminho novo permite
  concluir um turno com processo ausente e desfecho de sucesso.
- **Regra dura 7 (AGENTS.md:33 — itens `hard` da matriz sao intocaveis):** intacta e reforcada.
  Nenhum item `hard` (`spec/policies/autonomy/L0-core.yaml:6-13`: `clinical_decision`,
  `authorization_denial`, `nip_manter_negativa`, `fraud_accusation`, `contract_termination`) e
  tocado; ao contrario, o padrao impede que uma escalacao humana prometida se perca silenciosamente —
  que e o mecanismo pelo qual um efeito adverso poderia acabar sem revisao humana.
- **ADR-0018 (padrao no-denial de cinco partes):** intacto. `erro_inicio_processo` nao e efeito
  adverso; e a declaracao de que o processo que carregaria o efeito nao existe.
- **ADR-0030 (semantica de erro de worker vs boundary catches do BPMN):** preservada. O desfecho aqui
  e do AGENTE; nenhum `bpmnErrorCode` novo, nenhuma entrada na
  `check-bpmn-error-allowlist`.
- **Chokepoint de start:** preservado. Nenhuma chamada direta nova; `make check-start-process-fence`
  continua verde e ganha um complemento (a cerca da Parte 4) que cobre o lado de SAIDA.

---

## O que e decisao do DONO (explicito)

1. **Elevar o padrao fail-notify a requisito do contrato canonico** — todo agente que inicia processo
   PRECISA ter aresta condicional + `notify_start_failure`, e nao apenas «pode ter».
2. **A politica de retry/alerta** — opcao A, B ou C da Parte 3; e, se A ou C, o limiar do alerta.
3. **A forma da cerca (Parte 4)** — `scripts/ci/` + alvo de `Makefile` (dono-gated,
   `.github/CODEOWNERS:223-242`) ou teste unitario em `tests/` (nao dono-gated).
4. **A correcao da fronteira A2A (Parte 5)** — raise tipado do handler, ou campo `success` em
   `HandlerOutput`. Tem implicacao de contrato A2A (ADR-0003/ADR-0015).
5. **A adicao de `erro_inicio_processo` a enumeracao de desfechos dos contratos `SP-OP-*`** dos
   agentes que iniciam processo.

---

## Criterio de aceite testavel

1. Para cada agente que inicia processo, com o transporte injetado levantando `CibSevenError`, o
   grafo termina em `desfecho == "erro_inicio_processo"` — **nunca** no desfecho de sucesso gravado a
   montante. Golden Tier-A por agente (fecha tambem CC-08 para esses agentes).
2. `record_agent_error` incrementa `maezo_agent_errors_total` nesse caminho (teste sobre a metrica),
   de modo que `MaezoAgentCrashLoop` possa disparar.
3. Cerca de CI: enumera os grafos com no de start e exige a aresta condicional + o no. **VERMELHA**
   num commit que remove a aresta de um grafo qualquer; **VERDE** na arvore corrigida. As duas provas,
   em dois commits.
4. A2A (Rafael): um turno cujo `process_started` e `False` NAO produz
   `decision=_DECISION_COMPLETED` no dispatcher e NAO grava resultado terminal no cache de
   idempotencia — de modo que a mesma `task_id` possa ser reentregue.
5. `validate-artifacts` verde com `erro_inicio_processo` declarado nos contratos; nenhum
   `bpmnErrorCode` novo e `check-bpmn-error-allowlist` inalterada (prova de que o desfecho e do
   agente, nao do engine).
6. `make check-start-process-fence` continua verde (nenhuma chamada direta introduzida).

## Supersedes

—. **Estende** o contrato canonico do `_template` e os contratos `SP-OP-*` dos agentes que iniciam
processo. Nao emenda nem supersede `ADR-0018`, `ADR-0030` ou `ADR-0001`. Complementar ao trabalho de
proveniencia CC-13 (secao «Variaveis de proveniencia do agente» nos contratos, PR #314), que aplica a
mesma distincao agente-vs-processo as variaveis aditivas.
