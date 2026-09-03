# Contrato — SP-OP-ANS-CRON-001 (Agendador per-report_type dos Envios Periodicos ANS)

**Status:** DRAFT (v0.2.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**BPMN:** `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` (GAP-ANS-1)
**Natureza:** agendador puro (operadora -> regulador). **NAO negativa-like**; nenhum efeito
adverso; todos os terminais NEUTROS. O HITL pre-filing vive no processo de envio
(SP-OP-ANS-SUBMIT-001) e nao e contornavel por aqui (guard `ERR_ANS_SUBMIT_NOT_HUMAN` no worker).

## Por que existe (racional de engine — verificado empiricamente no CIB Seven 2.1.0)

O engine rejeita no deploy (ENGINE-09005):

- `camunda:inputOutput` em start events ("camunda:inputOutput mapping unsupported for element
  type 'startEvent'");
- multiplos none/timer start events num mesmo process definition ("multiple none start events or
  timer start events not supported on process definition").

Logo "um TimerStartEvent por report_type" (GAP-ANS-1) so e engine-deployavel como **um process
definition por report_type**, cada um com um unico TimerStartEvent proprio.

## Dispatch por FATO (ADR-0003 — event-choreographed; ZERO callActivity no repo)

Cross-process e SEMPRE choreografado por fato Kafka (`docs/audits/bpmn-process-completeness.md`
§4 — nenhum `callActivity` em nenhum BPMN). Cada tick do timer publica, via o worker generico
`operadora.events.publish` (inputParameter literal `event_type` -> `payload.type`), o **fato
tipado** no topico interno de notificacoes:

```
topico : operadora.notifications.internal        (ja registrado; nenhum topico novo)
type   : ans.cron_due
payload: { report_type, periodicidade, origem_envio: "calendario",
           competencia: "COMPETENCIA_PENDENTE",   (literal do BPMN — ver nota abaixo)
           ans_cron_reference_date_iso: <YYYY-MM-DD do instante do tick> }
           (identificadores nao-PHI; sem tenant)
```

**res-ans-competencia-sentinel (GAP-ANS-1/GAP-ANS-3 — ABERTO, NAO resolvido; GAP-FAB-NOTIF fix
corrige esta secao, que antes descrevia um mecanismo que nao existe no repo):** o worker generico
`operadora.events.publish` (nome real: `src/maezo/tools/workers/events.py::
make_publish_event_handler` — `tools/workers/phase0.py` NAO existe em v2) GRAVA
`ans_cron_reference_date_iso` no payload sempre que `event_type == ans.cron_due` — a data (UTC) do
INSTANTE em que o serviceTask de publish executa (o tick acabou de disparar). Isso e REAL e
confere. O que NAO e real: nada consome essa ancora para calcular uma competencia. O literal
`competencia: "COMPETENCIA_PENDENTE"` do BPMN permanece INALTERADO por todo o caminho —
NAO existe um `notifications_bridge` que a resolva depois.

O **`notification_bridge`** (`src/maezo/platform/notification_bridge.py` — modulo unico, singular;
NAO um pacote `notifications_bridge/consumer.py`, que nao existe em lugar nenhum do repo, confirmado
por `find`/`grep`) mapeia `ans.cron_due` -> start de **SP-OP-ANS-SUBMIT-001** via `plan_start` ->
`mcp-cibseven.start_process` (PEP + auditoria + `find_active_instance` — idempotente), na regra
`_ans_submit_variables_from_cron_due` (`:308-343`):

- **business key DETERMINISTICA** = `ANSSUB-{tenant}-{report_type}-{competencia}`
  (`_ans_cron_business_key`, `:189-197`; a calendar-key do contrato de envio; NUNCA uuid4). Essa
  parte da formula e real. `competencia`, porem, NAO e computada por nada chamado
  `_ans_cron_competencia` (essa funcao nao existe) — a regra so REPASSA o que o fato carrega
  (`str(payload.get("competencia","")).strip() or _COMPETENCIA_PENDENTE`, `:341`), e hoje o fato
  SEMPRE carrega o literal `COMPETENCIA_PENDENTE` (nenhum caminho do BPMN deployado o substitui —
  ver nota acima). A funcao que FARIA esse calculo existe de verdade, so que noutro modulo e
  desconectada: `ans_cron._compute_competencia` (`src/maezo/tools/workers/ans_cron.py:37`,
  chamada apenas por `trigger_submissions:132`) — mas `trigger_submissions` esta registrada no
  topico `operadora.ans_cron.trigger_submissions`, que NENHUM serviceTask do BPMN deployado
  invoca (as 5 definitions ligam `ST_PublishCronDue*` direto a `operadora.events.publish` com o
  literal estatico). Isso e **GAP-ANS-1** (remodelagem de scheduler per-report-type necessaria
  para religar isso — `docs/reports/business-logic-audit-improvement-plan.md:445`, fora do
  escopo deste fix) — nao um "residuo resolvido". Consequencia pratica: **toda instancia
  SP-OP-ANS-SUBMIT-001 aberta pelo caminho cron hoje nasce com `competencia=COMPETENCIA_PENDENTE`**
  — no maximo UMA instancia pendente-de-competencia ativa por report_type por tenant (re-tick do
  timer e reentrega Kafka reconvergem para a instancia ativa, nunca duplica ciclo/filing). Um
  `tenant_id`/`competencia` explicitos no fato (scheduler futuro/override, ou humano resolvendo em
  `UT_CorrigirPendenciaEnvio`) sempre tem precedencia quando presentes.
- o **tenant NAO viaja no fato** (o BPMN do agendador e tenant-agnostico): a ponte injeta o
  proprio tenant (`ans_cron_business_key`/`ans_cron_variables`, param `bridge_tenant`). Um
  `tenant_id` explicito no fato (scheduler futuro) tem precedencia.

## Variaveis seedadas pela ponte no start (ENGINE-16004-safe — todo identificador downstream SETADO)

| Variavel | Valor no caminho cron | Racional |
|---|---|---|
| `report_type` | do fato (literal por tipo) | input de `ans_calendar`/`ans_sla` |
| `periodicidade` | do fato (literal por tipo) | payload de eventos |
| `origem_envio` | `calendario` (**forcado pela ponte**, nunca lido do wire) | condicao `${origem_envio == 'nip_filing'}` resolve |
| `competencia` | SEMPRE o literal `COMPETENCIA_PENDENTE` hoje (GAP-FAB-NOTIF fix: NAO ha computo real no caminho deployado — GAP-ANS-1 aberto, ver secao acima); repassado sem alteracao por `notification_bridge._ans_submit_variables_from_cron_due` | fail-closed: sem competencia real, humano resolve em `UT_CorrigirPendenciaEnvio` e o fluxo reavalia `ans_calendar` (row de fallback conservadora por tipo enquanto pendente) |
| `tenant_id` | tenant da ponte (fato tem precedencia se presente) | identidade multi-tenant |
| `dataset_ref` | `""` | resolvido por humano/worker; workers usam `.get` com default |
| `dataset_complete`, `schema_valid`, `lgpd_anonimizado` | `false` (**fail-closed, seedados pela ponte** — nunca lidos do fato) | fatos ainda nao resolvidos => admissibilidade `PENDENTE` => humano; **nunca auto-transmite** |

## Topicos / DMN / grupos humanos

Nenhum external-task topic novo (reusa `operadora.events.publish`), nenhum topico Kafka novo
(`ans.cron_due` e um `type` de envelope em `operadora.notifications.internal`, ja registrado),
nenhuma DMN propria, nenhuma User Task propria (agendador puro; toda decisao humana vive no
processo de envio).

## Pendencias para promocao a FINAL

- Confirmar os `timeCycle` reais por report_type (RN 124/209/388/424, DIOPS — **DRAFT/verify**),
  incluindo ancora de data (ex.: `R/P1M` a partir de que dia do mes).
- **Resolucao automatica da `competencia` no caminho cron: NAO IMPLEMENTADA (GAP-ANS-1, ABERTO)**
  — GAP-FAB-NOTIF fix: a linha anterior desta secao citava `notifications_bridge/consumer.py::
  _ans_cron_competencia`, que nao existe. A implementacao real do calculo
  (`ans_cron._compute_competencia`, `src/maezo/tools/workers/ans_cron.py:37`) existe mas e
  codigo morto — nenhum serviceTask do BPMN deployado a alcanca (ver secao "res-ans-competencia-
  sentinel" acima). Ate GAP-ANS-1 religar o scheduler per-report-type, TODA instancia aberta pelo
  caminho cron carrega `competencia=COMPETENCIA_PENDENTE`. Pendente: wiring de GAP-ANS-1 +
  **confirmacao regulatoria do mapeamento** (mes/trimestre ANTERIOR ao da ancora — assuncao
  DRAFT, ver docs/review-queue.md) quando esse wiring acontecer.
- Revisao humana registrada (regulatorio-ANS) antes de qualquer deploy.
