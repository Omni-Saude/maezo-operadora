# Contrato — SP-OP-ANS-CRON-001 (Agendador per-report_type dos Envios Periodicos ANS)

**Status:** DRAFT (v0.2.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**BPMN:** `src/maezo/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` (GAP-ANS-1)
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

**res-ans-competencia-sentinel (Wave-1 residue, resolvido):** o worker generico
`operadora.events.publish` (`tools/workers/phase0.py::make_publish_event_handler`) tambem GRAVA
`ans_cron_reference_date_iso` no payload sempre que `event_type == ans.cron_due` — a data (UTC) do
INSTANTE em que o serviceTask de publish executa (o tick acabou de disparar). Um `camunda:
inputParameter` do BPMN e LITERAL/estatico por deploy (nao pode carregar "hoje"), entao a ancora so
pode ser produzida em runtime por este worker — nenhum topico/worker/serviceTask novo (o agendador
continua "puro": um unico serviceTask por definition, sempre o publish generico). O literal
`competencia: "COMPETENCIA_PENDENTE"` do BPMN permanece INALTERADO — quem resolve a competencia
REAL e o `notifications_bridge` (abaixo), nao este worker.

O **`notifications_bridge`** (consumidor unico do topico, deployment per-tenant) mapeia
`ans.cron_due` -> start de **SP-OP-ANS-SUBMIT-001** via `plan_start` ->
`mcp-cibseven.start_process` (PEP + auditoria + `find_active_instance` — idempotente):

- **business key DETERMINISTICA** = `ANSSUB-{tenant}-{report_type}-{competencia}` (a
  calendar-key do contrato de envio; NUNCA uuid4). `competencia` e COMPUTADA por
  `_ans_cron_competencia` (`notifications_bridge/consumer.py`) a partir de
  `ans_cron_reference_date_iso` — **DRAFT — requires human review (regulatorio-ANS,
  docs/review-queue.md)**: mapeamento assumido = o periodo de calendario (mes ou trimestre, por
  `periodicidade`) IMEDIATAMENTE ANTERIOR ao mes da ancora. NUNCA do relogio de processamento da
  ponte (a ancora ja vem FIXA no fato — reler o MESMO fato Kafka, mesmo em reentrega/redelivery
  meses depois, produz sempre a MESMA competencia/bk). Sem a ancora (fato antigo/legado sem o
  campo), cai fail-closed na sentinela `COMPETENCIA_PENDENTE`: **no maximo UMA instancia
  pendente-de-competencia ativa por report_type por tenant** nesse caso — re-tick do timer e
  reentrega Kafka reconvergem para a instancia ativa (nunca duplica ciclo/filing). Um
  `tenant_id`/`competencia` explicitos no fato (scheduler futuro/override, ou humano resolvendo em
  `UT_CorrigirPendenciaEnvio`) sempre tem precedencia sobre o valor computado.
- o **tenant NAO viaja no fato** (o BPMN do agendador e tenant-agnostico): a ponte injeta o
  proprio tenant (`ans_cron_business_key`/`ans_cron_variables`, param `bridge_tenant`). Um
  `tenant_id` explicito no fato (scheduler futuro) tem precedencia.

## Variaveis seedadas pela ponte no start (ENGINE-16004-safe — todo identificador downstream SETADO)

| Variavel | Valor no caminho cron | Racional |
|---|---|---|
| `report_type` | do fato (literal por tipo) | input de `ans_calendar`/`ans_sla` |
| `periodicidade` | do fato (literal por tipo) | payload de eventos |
| `origem_envio` | `calendario` (**forcado pela ponte**, nunca lido do wire) | condicao `${origem_envio == 'nip_filing'}` resolve |
| `competencia` | COMPUTADA de `ans_cron_reference_date_iso` (res-ans-competencia-sentinel — DRAFT/verify regulatorio); default `COMPETENCIA_PENDENTE` (sentinela) quando a ancora esta ausente | fail-closed: sem ancora, humano resolve em `UT_CorrigirPendenciaEnvio` e o fluxo reavalia `ans_calendar` (row de fallback conservadora por tipo enquanto pendente) |
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
- **Resolucao automatica da `competencia` no caminho cron: IMPLEMENTADA**
  (res-ans-competencia-sentinel) — `notifications_bridge/consumer.py::_ans_cron_competencia`
  deriva a competencia real de `ans_cron_reference_date_iso`; a sentinela so sobrevive fail-closed
  quando a ancora esta ausente. Pendente: **confirmacao regulatoria do mapeamento** (mes/trimestre
  ANTERIOR ao da ancora — assuncao DRAFT, ver docs/review-queue.md).
- Revisao humana registrada (regulatorio-ANS) antes de qualquer deploy.
