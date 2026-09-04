# Contrato — SP-OP-ANS-SUBMIT-001 (Envios Periodicos ANS — calendar-driven)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 (Wave B — canal regulatorio ANS) · **BPMN:** `spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn` (autorado em wave posterior contra este contrato)
**Classificacao de autonomia:** `ans_official_submission: L1` (require_human — humano assina o envio vinculante; nao-repudio ADR-0007).
**Gatilho regulatorio (todos DRAFT/verify regulatorio+juridico):** RN 124/2006 (SIP — Sistema de Informacoes de Produtos: **DRAFT/verify**), RN 209/2009 (utilizacao de servicos: **DRAFT/verify**), RN 388/2015 (indicadores/transparencia: **DRAFT/verify**), RN 424/2017 (padrao TISS / monitoramento: **DRAFT/verify**), DIOPS (informacoes economico-financeiras, periodicidade trimestral: **DRAFT/verify**). **Todas as citacoes de RN, periodicidades, datas de competencia e fontes do calendario sao DRAFT — confirmar contra texto vigente ANS antes de qualquer envio.**

## Invariante (NAO negativa-like — sem padrao no-denial)

Este processo e operadora -> regulador; **nenhuma decisao adversa contra beneficiario ou
prestador**. Portanto **NAO recebe o padrao no-denial de cinco partes** (sem enums de "negar",
sem coluna de saida de negativa — nada a inverter aqui). O que se mantem da disciplina da
quadrupla:

- **Idempotencia por competencia:** uma instancia por `{report_type, competencia}`; re-disparo
  do mesmo periodo retorna a instancia ativa (nunca duplica filing junto a ANS).
- **HITL pre-filing (nao-repudio):** o envio oficial e legalmente vinculante; so e transmitido
  apos `UT_RevisarEnvio` concluida por humano do grupo `regulatorio-ans`. **Nenhum caminho
  automatizado transmite a ANS** — `submit_to_ans` so executa com `decisao_envio==APROVAR_ENVIO`
  setado por humano (worker-guard `ERR_ANS_SUBMIT_NOT_HUMAN`). Espelha o gate "agente instrui,
  humano assina" de Rafael/Gustavo.
- **Nunca auto-submete em dados faltantes:** dataset incompleto / schema invalido **roteia a
  humano** (`UT_RevisarEnvio` ou `UT_CorrigirPendenciaEnvio`), nunca rejeita por design nem
  arquiva sozinho.
- **SLA de calendario:** prazo dirigido por `ans_calendar` (inputs `report_type` +
  `competencia`; due_date por competencia); alerta nao-interruptivo de deadline-risk antes do
  vencimento. O cron per-`report_type` vive em **SP-OP-ANS-CRON-001** (um process definition por
  tipo, cada um com seu proprio TimerStartEvent — o engine rejeita multiplos timer starts e
  `camunda:inputOutput` em start events, ENGINE-09005); cada tick publica o fato `ans.cron_due`
  em `operadora.notifications.internal` e o **notifications_bridge** inicia este processo com
  bk deterministica (event-choreographed, ADR-0003 — nunca callActivity; ver
  `docs/processes/contracts/SP-OP-ANS-CRON-001.md`).
- **Eventos de dominio start/end:** todo fim publica evento Kafka antes (auditoria dupla engine
  + Kafka, ADR-0007).

## Business key (idempotencia)

```
ANSSUB-{tenant_id}-{report_type}-{competencia}
```

- `report_type` ∈ enum abaixo; `competencia` no formato `YYYY-MM` (mensal) ou `YYYY-Qn`
  (trimestral, ex. `2026-Q1`). Uma instancia por tipo de relatorio por periodo de competencia;
  re-disparo do cron para a mesma competencia retorna a instancia ativa (start idempotente —
  `mcp-cibseven.start_process` consulta a business key antes de iniciar).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `report_type` | string | sim | `RN_124_SIP` \| `RN_209_UTILIZACAO` \| `RN_388_QUALIDADE` \| `RN_424_TISS_MONITORAMENTO` \| `DIOPS_TRIMESTRAL` |
| `competencia` | string | sim | Periodo de competencia (`YYYY-MM` ou `YYYY-Qn`) — compoe a business key |
| `periodicidade` | string | sim | `mensal` \| `trimestral` \| `anual` (derivada do `report_type` por worker) |
| `origem_envio` | string | sim | `calendario` \| `nip_filing` \| `retransmissao_manual` — distingue cron de filing originado por NIP (handoff SP-OP-NIP-001) |
| `nip_protocolo_origem` | string | nao* | Protocolo NIP de origem (*obrigatorio se `origem_envio==nip_filing`; correlaciona o handoff) |
| `dataset_complete` | boolean | sim | Pre-resolvido por worker (`regulatorio.anssubmit.assemble` retornou dataset integro) |
| `schema_valid` | boolean | sim | Pre-resolvido por worker (`regulatorio.anssubmit.validate` — XSD/TISS) |
| `lgpd_anonimizado` | boolean | sim | Fail-closed `false` seedado pela ponte (`notifications_bridge` — nunca confia no fato do wire); ecoado por `regulatorio.anssubmit.assemble` (mesmo padrao non-computing de `dataset_complete` — **NAO** e um calculo real de anonimizacao: `mcp-regdata`, a fonte de um atestado real, esta AWS-blocked, issue #16); so passa a `true` quando um humano confirma explicitamente via `UT_CorrigirPendenciaEnvio` que o dataset referenciado por `dataset_ref` esta de fato agregado/anonimizado (ADR-0006; LGPD em relatorio regulatorio) — GAP-ANS-5 |
| `dataset_ref` | string | sim | Referencia ao artefato montado (sem PHID; ponteiro de storage) |
| `due_date` | string (date ISO `YYYY-MM-DD`) | nao* | Eco best-effort de `GustavoGraph._contract_variables` (`src/maezo/agents/gustavo/graph.py`, ramo `fluxo == "ans_submit"`) a partir da pre-avaliacao informativa da DMN `ans_calendar` — **NAO** e a fonte do timer interruptivo: o deadline real vem de `${calendario.due_date}`, resolvido pela re-avaliacao de `ans_calendar` dentro do proprio BPMN. Achado do fleet audit: semeada no start mas so documentada como saida `out` de DMN |

\* Best-effort — se a pre-avaliacao de Gustavo falhar (DMN indisponivel), a variavel simplesmente nao e semeada; o timer de calendario depende so da re-avaliacao real dentro do BPMN, nunca deste eco.

Nota: o dataset e **agregado/anonimizado** (Zona Geral, ADR-0006). Nenhuma variavel de processo
carrega PHI direto — apenas `dataset_ref` (ponteiro) e flags.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_envio` | string | `APROVAR_ENVIO` \| `CORRIGIR_PENDENCIA` \| `ADIAR_ENVIO` (User Task humana `UT_RevisarEnvio`) |
| `revisor_id` | string | Id do humano (`regulatorio-ans`) que aprovou o envio (cadeia de auditoria ADR-0007; obrigatorio se `APROVAR_ENVIO`) |
| `justificativa_adiamento` | string | Obrigatoria se `decisao_envio==ADIAR_ENVIO` (registra o motivo do nao-envio no prazo) |
| `protocolo_ans` | string | Protocolo retornado pela ANS (emitido por `regulatorio.anssubmit.submit`). **Quando `origem_envio==nip_filing`, este e o `protocolo_filing` referenciado pelo contrato SP-OP-NIP-001** (GAP-NIP-4) — SP-OP-NIP-001 termina antes de a ANS emitir protocolo, entao nao ha `protocolo_filing` como variavel de processo de NIP; correlacionar por `nip_protocolo_origem`/business key `ANSSUB-{tenant}-nipfiling-{submit_id}` |
| `status_envio` | string | `enviado` \| `ack` \| `nack` \| `retransmitido` |
| `data_envio` | string | Timestamp ISO 8601 do envio (nao-repudio) |
| `nack_motivo` | string | Motivo do NACK retornado pela ANS (preenchido apenas em retransmissao) |
| `retry_attempt` | integer | Contador de tentativa de retransmissao (subprocess SUB_RetryEnvio; INPUT da DMN ans_retry_policy) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}`. Eventos de dominio Kafka em start/end;
external-task topics para cada service task. **Todos a registrar em `config/topic_registry.yaml`
(W0.2 — este processo NAO edita o registry; ver secao "Pendencias / registro").**

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.anssubmit.received` | produz | apos start (instancia de envio aberta — cron, nip_filing ou retransmissao) |
| Kafka | `agents.events.anssubmit.generated` | produz | dataset montado+validado (apos `assemble`/`validate`) |
| Kafka | `agents.events.anssubmit.submitted` | produz | envio transmitido a ANS (apos User Task + `submit`) |
| Kafka | `agents.events.anssubmit.acked` | produz | ACK da ANS correlacionado (payload.protocolo_ans) |
| Kafka | `agents.events.anssubmit.deadline_risk` | produz | timer nao-interruptivo de risco de prazo de calendario |
| Kafka | `agents.events.anssubmit.failed` | produz | NACK/erro definitivo apos esgotar retry (payload.nack_motivo) |
| Kafka | `agents.events.anssubmit.completed` | produz | fim (payload.desfecho = `enviado_ack` \| `enviado_pendente_ack` \| `adiado_humano` \| `falha_retransmissao`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (igual aos demais SP-OP) |
| External task | `regulatorio.anssubmit.assemble` | consome (worker) | monta o dataset regulatorio anonimizado (port `generate_regulatory_reports` → `mcp-regdata.assemble`) — **tarefa externa; conectividade real AWS-blocked (issue #16)** |
| External task | `regulatorio.anssubmit.validate` | consome (worker) | valida schema XSD/TISS do dataset montado |
| External task | `regulatorio.anssubmit.submit` | consome (worker) | transmite a ANS (`mcp-ans.submit`); so apos `decisao_envio==APROVAR_ENVIO` humano — **tarefa externa; conectividade ANS real AWS-blocked (issue #16)** |
| External task | `regulatorio.anssubmit.track_protocol` | consome (worker) | consulta/correlaciona status do protocolo (`mcp-ans.get_protocol`; ACK/NACK) |
| External task | `regulatorio.anssubmit.notify_regulatorio` | consome (worker) | alerta o grupo `regulatorio-ans` (deadline-risk, NACK, adiamento) |
| External task | regulatorio.anssubmit.retransmit | consome (worker) | retransmite a ANS no NACK transitorio (subprocess SUB_RetryEnvio); reusa APROVAR_ENVIO+revisor_id (mesmo guard ERR_ANS_SUBMIT_NOT_HUMAN) |
| Message BPMN | `msg.anssubmit.ack_received` | recebe | ACK/NACK assincrono da ANS correlacionado por `protocolo_ans` (destrava o aguardo) |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date} —
**`"number"` invalido**; dinheiro BRL (nao aplicavel aqui) → `double`/inteiro-centavos; dias/SLA/
datas → string ISO 8601. Todo decisionTable com `hitPolicy`; toda tabela com row catch-all →
caminho humano conservador. **Sem coluna de saida de negativa (nao ha decisao adversa neste
processo).**

### `ans_calendar` (hit policy FIRST — DRAFT; TODAS as datas/fontes/periodicidades DRAFT/verify)
| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `report_type` | string | ver variaveis de entrada |
| in | `competencia` | string | `YYYY-MM` \| `YYYY-Qn` |
| out | `due_date` | string | data-limite ISO 8601 (`YYYY-MM-DD`) — **DRAFT/verify** |
| out | `sla_alerta` | string | data ISO 8601 do alerta de deadline-risk (ex.: due_date − 5d) — **DRAFT/verify** |
| out | `periodicidade` | string | `mensal` \| `trimestral` \| `anual` |
| out | `fonte_regulatoria` | string | RN/IN vigente que ancora o prazo (ex.: `RN_124_2006`) — **DRAFT/verify** |

Catch-all (report_type/competencia desconhecidos) → `due_date`/`sla_alerta` conservadores +
`fonte_regulatoria="REVISAO_HUMANA"` (roteia a humano via admissibilidade; nunca prazo "infinito").

### `ans_sla` (hit policy FIRST — DRAFT; todos os prazos DRAFT/verify)

DMN que computa as DURACOES relativas (ISO 8601) dos timers de boundary das User Tasks.
Separada de `ans_calendar` (que resolve datas absolutas de calendario/competencia): esta DMN
produz duracoes para `timeDuration`, enquanto `ans_calendar` produz datas para dossie/eventos.

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `report_type` | string | ver variaveis de entrada (PERSP-B5-INPUTS: **UNICO input real** da tabela shippada — `spec/processes/dmn/ans_sla.dmn:34-36`; `origem_envio` e `fonte_regulatoria` abaixo removidos desta lista por nao existirem na DMN, ver nota) |
| out | `sla_analise` | string (ISO 8601 duration) | duracao do prazo de revisao (ex.: `P5D`), consumida via `${sla.sla_analise}` pelos timers `timeDuration` interruptivos (`BT_DueDate` / `BT_DueDateJuridico` / `BT_DueDatePendencia`) — **DRAFT/verify** |
| out | `sla_alerta` | string (ISO 8601 duration) | duracao do alerta de deadline-risk (~50-70% do SLA, ex.: `P3D`), consumida via `${sla.sla_alerta}` pelos timers `timeDuration` nao-interruptivos (`BT_DeadlineRisk` / `BT_DeadlineRiskJuridico` / `BT_DeadlineRiskPendencia`) — **DRAFT/verify** |

PERSP-B5-INPUTS (achado alem do escopo original, corrigido junto por estar na mesma tabela): esta
secao afirmava `origem_envio` como segundo input e `fonte_regulatoria` como terceiro output; a DMN
`ans_sla` shippada (`spec/processes/dmn/ans_sla.dmn`) so declara `in_report_type` (`:34-36`) e os
dois outputs `out_sla_analise`/`out_sla_alerta` (`:37-38`) — nenhum `in_origem_envio` nem
`out_fonte`/`fonte_regulatoria` em lugar nenhum de `spec/processes/dmn/*.dmn` (confirmado por
`grep -rl origem_envio spec/processes/dmn/*.dmn` -> 0 arquivos). `origem_envio` e real como
variavel de PROCESSO (seedada no start, `bpmn:79,112`) e como condicao de gateway BPMN
(`${origem_envio == 'nip_filing'}`, `bpmn:611`) — nunca como input de uma DMN.

Catch-all (report_type/origem_envio desconhecidos) → `sla_analise`/`sla_alerta` conservadores
(janela curta; nunca prazo infinito). **Sem saida adversa** — so produz duracoes de SLA.

### `ans_submission_admissibility` (hit policy FIRST — DRAFT)
| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `dataset_complete` | boolean | — |
| in | `schema_valid` | boolean | — |
| in | `lgpd_anonimizado` | boolean | — |
| out | `roteamento` | string | `SEGUE_ENVIO` \| `PENDENTE` \| `REVISAO_HUMANA` |
| out | `motivo` | string | rationale legivel |

**Sem saida "reject"/"deny" por design** — dado faltante nunca rejeita: `dataset_complete=false`
ou `schema_valid=false` ou `lgpd_anonimizado=false` → `PENDENTE` (corrige) ou `REVISAO_HUMANA`.
Catch-all → `REVISAO_HUMANA`. Mesmo `SEGUE_ENVIO` so habilita a User Task de aprovacao — **a DMN
nunca transmite**; quem libera o filing e o humano.

### `ans_retry_policy` (hit policy FIRST — politica DRAFT)
| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `retry_attempt` | integer | contador de loop (incrementado pelo worker `make_retransmit_handler`) |
| out | `backoff` | string | duracao ISO 8601 (`P...`/`PT...`) consumida pelo timer `ICE_Backoff` via `${retry.backoff}` |
| out | `continue_retry` | boolean | true = tenta de novo (loop); false = esgotado |

Regras (**DRAFT/verify regulatorio** — contagem de tentativas e duracoes ainda nao confirmadas):
`retry_attempt` 1 → `PT5M`/`continue_retry=true`; 2 → `PT30M`/`true`; 3 → `PT2H`/`true`;
catch-all (>3) → `""`/`continue_retry=false` (esgotado → `End_RetryEsgotado` → `ERR_ANS_RETRY_ESGOTADO`
→ `UT_TratarNack`). typeRef segue a allowlist engine-deployavel (§4-bis-A): `integer`/`string`/
`boolean` validos — **`"number"` proibido**.

## Papeis humanos (candidate groups) — **PROPOSTOS, requerem confirmacao (ver Open Questions)**

| Grupo | Papel | Tarefa |
|---|---|---|
| `regulatorio-ans` | Nucleo regulatorio ANS (sign-off vinculante do envio) | `UT_RevisarEnvio` (assina o envio oficial — unica origem de `decisao_envio==APROVAR_ENVIO`); `UT_CorrigirPendenciaEnvio` (dataset/schema pendente); `UT_TratarNack` (NACK da ANS) |
| `juridico-regulatorio` | Juridico/regulatorio (revisao de envios sensiveis / NIP filing) | `UT_RevisarEnvio` quando `origem_envio==nip_filing` (filing de resposta NIP — texto legal sempre humano) |
| `coordenacao-regulatorio` | Coordenacao regulatoria (assume no estouro de prazo) | `UT_CoordenacaoEnvioAssume` (deadline-risk persistente / SLA de calendario estourado) |

> **Nota (Open Questions):** `regulatorio-ans`, `juridico-regulatorio` e `coordenacao-regulatorio`
> sao **nomes PROPOSTOS** — confirmar contra a taxonomia organizacional da operadora (grupos
> reais de candidatos no console de User Tasks / OIDC) antes da promocao a FINAL.

## SLAs (todos DRAFT/verify regulatorio — prazos de CALENDARIO ANS)

| Timer | Valor | Tipo | Fonte |
|---|---|---|---|
| Start por calendario | cron por `report_type` em **SP-OP-ANS-CRON-001** (um TimerStartEvent `timeCycle` por tipo: `R/P1M` mensal, `R/P3M` trimestral, `R/P1Y` anual); cada tick publica o fato `ans.cron_due` (`operadora.notifications.internal`, com a ancora `ans_cron_reference_date_iso`) e o `notifications_bridge` inicia este processo com bk `ANSSUB-{tenant}-{report_type}-{competencia}` seedando `report_type`/`periodicidade`/`origem_envio` + fatos fail-closed; `competencia` e COMPUTADA da ancora (res-ans-competencia-sentinel — DRAFT/verify regulatorio, docs/review-queue.md), com fallback fail-closed a `COMPETENCIA_PENDENTE` (humano resolve em `UT_CorrigirPendenciaEnvio`) quando a ancora esta ausente; o start local `Start_DespachoEnvio` e none (despacho com variaveis seedadas) | dispara a instancia por tipo/periodicidade | RN 124/209/388/424 vigentes — **DRAFT/verify** |
| Alerta de deadline-risk | `${calendario.sla_alerta}` (ex.: due_date − 5d) | nao-interruptivo → `regulatorio.anssubmit.notify_regulatorio` + evento `anssubmit.deadline_risk` (instancia segue aberta) | politica interna ancorada na RN — **DRAFT/verify** |
| Prazo de calendario (`due_date`) | `${calendario.due_date}` | timer interruptivo na espera de revisao → `UT_CoordenacaoEnvioAssume` (coordenacao assume; decisao de envio continua humana) | RN/IN vigente — **DRAFT/verify** |
| Retry/backoff de transmissao | subprocess embarcado `SUB_RetryEnvio` dirigido pela DMN `ans_retry_policy` (`BRT_RetryPolicy`): contagem de tentativas + backoff exponencial `PT5M`/`PT30M`/`PT2H`; timer `ICE_Backoff` usa `${retry.backoff}` | retry no NACK transitorio (entrada via `ERR_ANS_PROTOCOLO_NACK`); esgotado (`continue_retry=false`) → `ERR_ANS_RETRY_ESGOTADO` → `UT_TratarNack` (humano) | politica operacional — **DRAFT** (contagem/backoff a confirmar) |
| Aguardo de ACK | `ICE_AguardarAck` timer intermediario (ref `P1D`) com `GW_AckRecebido` | espera correlacao `msg.anssubmit.ack_received`; expirado → `UT_TratarNack`/alerta | politica operacional — **DRAFT/verify** |

Nota: o calendario regulatorio e por **dias corridos/data fixa de competencia** (diferente dos
prazos em dias uteis dos processos negativa-like). Resolver feriados/fim de semana no worker de
calendario conservadoramente (antecipar, nunca postergar). **Toda data e periodicidade e DRAFT.**

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_ANS_SUBMIT_NOT_HUMAN` | worker-guard em `regulatorio.anssubmit.submit` | recusa transmitir sem `decisao_envio==APROVAR_ENVIO` setado por humano; exige `revisor_id` na cadeia de auditoria (ADR-0007). **O efeito vinculante (filing) so ocorre apos User Task humana** — analogo a `ERR_*_NOT_HUMAN` dos processos negativa-like, aplicado ao ato regulatorio vinculante (nao-repudio) em vez de a uma negativa. |
| `ERR_ANS_DATASET_INCOMPLETO` | declarado (`Error_AnsDatasetIncompleto`) para uso do worker `assemble` | worker pode lancar BPMN error se a montagem do dataset falhar na origem; roteia a `UT_CorrigirPendenciaEnvio` (humano), **nunca** auto-rejeita o envio; tratamento a detalhar na promocao a FINAL |
| `ERR_ANS_PROTOCOLO_NACK` | boundary error `BE_SubmitNack` em `ST_SubmeterEnvio`; lancado pelo worker `regulatorio.anssubmit.submit` APOS o guard humano, num NACK transitorio | entra no subprocess `SUB_RetryEnvio` (retry/backoff); esgotado → `UT_TratarNack` (humano) + evento `anssubmit.failed`; nunca encerra silenciosamente |
| `ERR_ANS_RETRY_ESGOTADO` | boundary error em `SUB_RetryEnvio` (declarado `Error_AnsRetryEsgotado`); lancado pelo `End_RetryEsgotado` quando a DMN `ans_retry_policy` retorna `continue_retry=false` | retry esgotado → `ST_PublishFailed` → `UT_TratarNack` (humano) + evento `anssubmit.failed` (`nack_motivo`); nunca encerra silenciosamente |

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) — BPMN/DMN bodies autorados em wave posterior contra este contrato.
- **Confirmacao com regulatorio/juridico de TODAS as datas de competencia, periodicidades e
  fontes RN do `ans_calendar`** (RN 124/209/388/424 podem ter sido consolidadas/substituidas;
  DIOPS periodicidade/prazo a confirmar).
- Confirmacao dos **nomes de candidate groups** (`regulatorio-ans` / `juridico-regulatorio` /
  `coordenacao-regulatorio`) contra a taxonomia organizacional da operadora.
- Semantica precisa de ACK/NACK da ANS e politica de retransmissao (tentativas, backoff, janela)
  contra a conectividade real (AWS-blocked, issue #16).
- Mapeamento dos cron por `report_type` (datas/cadencia reais dos `timeCycle` de
  SP-OP-ANS-CRON-001) — **DRAFT/verify**.
- **Resolucao automatica da `competencia`: IMPLEMENTADA no caminho cron, NAO IMPLEMENTADA no
  nip_filing** (GAP-FAB-NOTIF fix — correcao de uma alegacao falsa desta linha; `notifications_bridge/
  consumer.py` e a funcao `_competencia_from_anchor` NAO EXISTEM em lugar nenhum do repo,
  confirmado por `grep`/`find`). Realidade, por caminho:
  - **cron:** `ans_cron._compute_competencia` (`src/maezo/tools/workers/ans_cron.py:127`) e a
    unica implementacao REAL desse calculo no repo (mes/trimestre/ano ANTERIOR a uma data-ancora,
    com testes tabulares — `tests/unit/tools/workers/test_ans_cron.py`) e, desde
    **ANS-CRON-DEAD-CODE**, esta ALCANCAVEL a partir do BPMN deployado: cada uma das 5 definitions
    de SP-OP-ANS-CRON-001 executa `ST_ResolverCompetencia*` no topico registrado
    `operadora.ans_cron.trigger_submissions` ANTES de `ST_PublishCronDue*` no
    `operadora.events.publish` generico, e o literal estatico
    `competencia=COMPETENCIA_PENDENTE` deixou de existir nos `camunda:inputParameter`. Isto FECHA
    o gap **GAP-ANS-1** (remodelagem de scheduler per-report-type, detalhada em
    `docs/reports/business-logic-audit-improvement-plan.md:445`).
    `notification_bridge._ans_submit_variables_from_cron_due`
    (`src/maezo/platform/notification_bridge.py:308-362`) apenas repassa o que o fato carrega —
    hoje um periodo FECHADO real — e nunca inventa um periodo: a sentinela so sobrevive
    fail-closed para `report_type` FORA da taxonomia ratificada, caso que os 5 literais do BPMN
    nao conseguem emitir. **DRAFT/verify regulatorio**: o mapeamento periodo->competencia e a
    ancora (fuso) permanecem NAO confirmados com o regulatorio — ver
    `docs/processes/contracts/SP-OP-ANS-CRON-001.md` e `docs/review-queue.md`.
  - **nip_filing:** `notification_bridge._ans_submit_variables_from_nip_handoff`
    (`:266-304`) fixa `competencia = _COMPETENCIA_PENDENTE` (constante de MODULO — nunca deriva
    de `data_recebimento_nip_iso`, ao contrario do que este contrato afirmava antes desta
    correcao). GAP-ANS-3 (`docs/review-queue.md:320`) permanece aberto.
  Pendente: **wiring de GAP-ANS-3** (GAP-ANS-1 fechado por ANS-CRON-DEAD-CODE) + **confirmacao
  regulatoria do mapeamento assumido** (mes/trimestre IMEDIATAMENTE ANTERIOR ao da ancora, e o
  fuso civil que define a ancora) contra o texto vigente ANS (docs/review-queue.md).
- Politica de feriado/dia util na resolucao de `due_date`.
- Interacao do handoff `SP-OP-NIP-001 → ANS-SUBMIT-001` (`origem_envio==nip_filing`): contrato de
  correlacao do `nip_protocolo_origem` **IMPLEMENTADO** (GAP-NIP-4) — GAP-FAB-NOTIF fix (citacao
  corrigida): o consumidor de runtime vive em `src/maezo/platform/notification_bridge.py`
  (NAO num pacote `notifications_bridge/consumer.py`, que nao existe), na regra
  `_ans_submit_variables_from_nip_handoff` (`:266-304`), que mapeia `protocolo_ans`/
  `numero_nip_ans` do payload de handoff para `nip_protocolo_origem`; provado por
  `tests/unit/platform/test_notification_bridge.py::test_nip_handoff_triggers_ans_submit`
  (`:659-681`, unitario — a funcao de mapeamento, nao a cadeia completa contra um engine real). O
  arquivo `tests/integration/processes/test_cross_process_handoff_seam.py` citado antes desta
  correcao NAO EXISTE (confirmado por `find`); nenhuma prova de engine-real (`UT_RevisarEnvioJuridico`
  → `protocolo_ans` emitido) foi encontrada no repo — UNREPRODUCED, nao fechado. Pendente
  apenas a revisao regulatoria/juridica dos nomes de grupo (ja listado acima).

## Notas de design

- **Nao negativa-like:** ausencia do padrao no-denial e **intencional e documentada** — nao ha
  decisao adversa contra beneficiario/prestador. A salvaguarda equivalente e o **HITL pre-filing**
  (humano assina o ato vinculante) + idempotencia por competencia, nao o gate de negativa.
- **Anti-pattern invertido do reference:** o `generate_regulatory_reports_worker` / fluxo de
  `billing_submission.bpmn` do reference **transmite sem sign-off humano** e auto-arquiva. Aqui
  **inserimos a User Task `UT_RevisarEnvio` antes de `submit`** — o agente (Gustavo) monta e
  instrui; o humano `regulatorio-ans` assina o envio vinculante (nao-repudio ADR-0007). O port do
  worker entra como `regulatorio.anssubmit.assemble`/`validate`/`submit` reautorado async, sem o
  caminho de auto-filing.
- Nenhum fim do processo ocorre sem evento de dominio publicado antes (auditoria dupla engine +
  Kafka, ADR-0007).
- O agente **Gustavo** (general zone) conduz o processo (J1: cron → assemble → validate →
  `UT_RevisarEnvio` → submit → track ACK), mas **nunca transmite autonomamente** — o PEP retorna
  `require_human` para `ans_official_submission` (L1).
