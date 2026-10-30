# Contrato — SP-OP-ANS-CRON-001 (Agendador per-report_type dos Envios Periodicos ANS)

**Status:** DRAFT (v0.4.0) — R-007: calendario automatico desligado, ver secao propria. — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**BPMN:** `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` (GAP-ANS-1)
**Test spec:** `docs/processes/test-specs/SP-OP-ANS-CRON-001.md`
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

## Topologia por report_type: DOIS serviceTasks por definition

Cada uma das 4 process definitions (hoje **desligadas**, `isExecutable="false"` — ver "Calendario
automatico desligado") executaria, a partir do seu proprio TimerStartEvent:

```
TimerStartEvent (timeCycle proprio)
  -> ST_ResolverCompetencia*   topic operadora.ans_cron.trigger_submissions
  -> ST_PublishCronDue*        topic operadora.events.publish
  -> End_Cron*
```

**1) `ST_ResolverCompetencia*`** — servido por
`src/maezo/tools/workers/ans_cron.py::trigger_submissions`. Le o literal
`ans_cron_report_type` (`camunda:inputParameter` LOCAL — o UNICO lugar do BPMN onde o tipo e
declarado) e devolve, como **variaveis de processo**:

| Variavel devolvida | Conteudo |
|---|---|
| `report_type` | ecoado sob o nome canonico (o literal local nao viaja) |
| `periodicidade` | pt-BR (`mensal`/`trimestral`/`anual`) — MESMO vocabulario dos outputs de `ans_calendar.dmn`; `indeterminada` fora da taxonomia |
| `competencia` | o periodo FECHADO imediatamente anterior a ancora do tick (`_compute_competencia`), ou `COMPETENCIA_PENDENTE` fail-closed |
| `competencia_referencia_iso` | a ancora de que a competencia foi derivada, em **ISO-8601 com offset** no fuso civil de negocio (`America/Sao_Paulo`, constante `_BUSINESS_TZ`) — ex.: `2026-02-28T21:30:00-03:00`. Torna a derivacao auditavel/reproduzivel E explicita sobre o calendario civil em que o periodo foi fechado. **Leitor autorizado: `ans_cron.parse_competencia_referencia_iso`** — `date.fromisoformat` REJEITA um instante com offset e nao pode ser usado neste campo (ver "Ancora temporal") |
| `tenant_id` | seam de deployment (T2.6-EB3); **nao** viaja no fato — ver abaixo |

O nome do literal e `ans_cron_report_type`, **nao** `report_type`, por uma razao de engine:
`camunda:inputParameter` cria variavel LOCAL do activity scope e o `complete()` do external task
usa `setVariable`, que atualiza no escopo mais proximo onde o nome ja existe — um literal chamado
`report_type` capturaria o valor devolvido no proprio escopo local e ele NUNCA chegaria ao task
seguinte.

**2) `ST_PublishCronDue*`** — worker generico `operadora.events.publish`
(`src/maezo/tools/workers/events.py::make_publish_event_handler`; `tools/workers/phase0.py` NAO
existe em v2). Publica o **fato tipado** no topico interno de notificacoes:

```
topico : operadora.notifications.internal        (ja registrado; nenhum topico novo)
type   : ans.cron_due
payload: { report_type, periodicidade, origem_envio: "calendario",
           competencia, competencia_referencia_iso,
           ans_cron_reference_date_iso: <YYYY-MM-DD UTC do instante da PUBLICACAO>,
           tenant_id: <carimbo de deployment, setdefault> }
           (identificadores nao-PHI)
```

`ans_cron_reference_date_iso` e carimbado pelo publicador generico sempre que
`event_type == ans.cron_due` (data **UTC** do instante da publicacao, sem offset).
`competencia_referencia_iso` e a ancora do RESOLVER, em **horario civil brasileiro com offset**.
As duas normalmente designam o mesmo dia, mas **podem divergir** na janela de 00:00-02:59 UTC
(21:00-23:59 do dia anterior no Brasil) — e nesse caso a segunda e a correta, porque e ela que
explica a competencia. Ver "Ancora temporal" abaixo.

**`tenant_id` NAO esta em `event_payload_vars`, de proposito.** O agendador e tenant-agnostico
(TimerStartEvent, sem contexto de caso). O tenant do fato vem do carimbo `setdefault` do publicador
generico (seam `deployment_tenant_id`, sourced de `WorkerRuntimeSettings.tenant_id`). Se
`tenant_id` fosse listado, o `""` de uma composicao sem seam viajaria no payload, o `setdefault`
nao sobrescreveria, e a regra `ans.cron_due` da ponte — que exige tenant nao-vazio — ficaria
dormente (ou produziria a chave degenerada `ANSSUB--...`). Ha fence estatico para isso em
`tests/unit/spec/test_ans_cron_timers_taxonomy.py::test_ans_cron_payload_vars_nao_carregam_tenant_id`
— deliberadamente em `tests/unit/` (e nao sob `tests/integration/`, onde vivia): sob o marker
`integration` + o autouse `_skip_if_engine_unreachable` nenhum contexto OBRIGATORIO de CI o
executava.

## Dispatch por FATO (ADR-0003 — event-choreographed; ZERO callActivity no repo)

Cross-process e SEMPRE choreografado por fato Kafka (`docs/audits/bpmn-process-completeness.md`
§4 — nenhum `callActivity` em nenhum BPMN). O **`notification_bridge`**
(`src/maezo/platform/notification_bridge.py` — modulo unico, singular; NAO um pacote
`notifications_bridge/consumer.py`, que nao existe em lugar nenhum do repo) mapeia `ans.cron_due`
-> start de **SP-OP-ANS-SUBMIT-001** via `plan_start` -> `mcp-cibseven.start_process` (PEP +
auditoria + `find_active_instance` — idempotente), na regra
`_ans_submit_variables_from_cron_due`:

- **business key DETERMINISTICA** = `ANSSUB-{tenant}-{report_type}-{competencia}`
  (`_ans_cron_business_key`; a calendar-key do contrato de envio; NUNCA uuid4). A regra REPASSA a
  `competencia` que o fato carrega — que hoje, pos-GAP-ANS-1, e a competencia REALMENTE COMPUTADA
  pelo `ST_ResolverCompetencia*`, nao mais o sentinel. Dentro de um mesmo periodo fechado a
  competencia e estavel, entao re-tick e reentrega Kafka reconvergem para a MESMA instancia —
  idempotente, nunca duplica ciclo/filing. O sentinel `COMPETENCIA_PENDENTE` permanece como
  fallback fail-closed para `report_type` fora da taxonomia (a resolucao volta a ser humana em
  `UT_CorrigirPendenciaEnvio`).
- o **tenant NAO viaja como variavel de processo do agendador**: chega no fato pelo carimbo do
  publicador generico, e a ponte injeta o seu proprio tenant no plano de start
  (`ans_cron_business_key`/`ans_cron_variables`, param `bridge_tenant`). Um `tenant_id` explicito
  no fato tem precedencia.

**LACUNA ABERTA (FINDING #2, fora do escopo deste processo):** a REGRA existe e esta unit-testada,
mas **nenhum consumidor vivo** le `operadora.notifications.internal` e chama
`NotificationBridge.on_event(...)`. Contra o engine real, nenhuma instancia de
SP-OP-ANS-SUBMIT-001 nascia de um tick (`test_cron_dispara_fato_e_nao_inicia_submit_automaticamente`,
retirado por R-007: hoje nao ha tick nenhum — calendario automatico desligado).

> **Nota de escopo (decisao do dono, R-112, 2026-09-04).** Este estado e **intencional e
> divulgado ate G4**: nenhum tick de cron abre uma instancia de SP-OP-ANS-SUBMIT-001 enquanto o
> ambiente-alvo (broker e engine alcancaveis) nao estiver provisionado. O entrypoint Helm
> (`deploy/helm/maezo-tenant/templates/deployment-bridge.yaml`) e o modulo consumidor
> (`src/maezo/platform/integrations/notifications_bridge.py`) ja existem e sao coerentes entre
> si — falta o ambiente, nao codigo. Nenhum invocador alternativo sera construido para
> contornar essa ausencia (opcao C do memorando foi descartada como capacidade especulativa).
> **Gatilho de reabertura:** provisionamento de G4 (broker Kafka e engine CIB Seven alcancaveis
> no ambiente-alvo) — a partir dai, subir o Deployment do bridge (opcao A) volta a ser a acao
> pendente. Ate la, a obrigacao periodica ANS permanece **sem execucao automatica real**.

## Variaveis seedadas pela ponte no start (ENGINE-16004-safe — todo identificador downstream SETADO)

| Variavel | Valor no caminho cron | Racional |
|---|---|---|
| `report_type` | do fato (literal por tipo, ecoado pelo resolver) | input de `ans_calendar`/`ans_sla` |
| `periodicidade` | do fato (pt-BR, resolvida por `trigger_submissions`) | payload de eventos; mesmo vocabulario dos outputs de `ans_calendar` |
| `origem_envio` | `calendario` (**forcado pela ponte**, nunca lido do wire) | condicao `${origem_envio == 'nip_filing'}` resolve |
| `competencia` | do fato: o periodo FECHADO anterior computado por `ans_cron._compute_competencia` a partir da ancora do tick (**DRAFT/verify** o mapeamento periodo->competencia); `COMPETENCIA_PENDENTE` apenas quando o `report_type` esta fora da taxonomia | fail-closed: sem competencia real, humano resolve em `UT_CorrigirPendenciaEnvio` e o fluxo reavalia `ans_calendar` (row de fallback conservadora por tipo enquanto pendente) |
| `tenant_id` | tenant da ponte (fato tem precedencia se presente) | identidade multi-tenant |
| `dataset_ref` | `""` | resolvido por humano/worker; workers usam `.get` com default |
| `dataset_complete`, `schema_valid`, `lgpd_anonimizado` | `false` (**fail-closed, seedados pela ponte** — nunca lidos do fato) | fatos ainda nao resolvidos => admissibilidade `PENDENTE` => humano; **nunca auto-transmite** |

## Topicos / DMN / grupos humanos

Dois external-task topics, ambos ja existentes e ambos ALCANCAVEIS a partir de um `serviceTask`
(PERSP-B5-ANSCRON-TOPICS):

| Topico | Worker | Onde |
|---|---|---|
| `operadora.ans_cron.trigger_submissions` | `ans_cron.py::trigger_submissions` | `ST_ResolverCompetencia*` (4x, desligadas) |
| `operadora.events.publish` | `events.py::make_publish_event_handler` (generico, 16 BPMNs) | `ST_PublishCronDue*` (4x, desligadas) |

Nenhum topico Kafka novo (`ans.cron_due` e um `type` de envelope em
`operadora.notifications.internal`, ja registrado), **nenhuma DMN propria** (zero
`camunda:decisionRef` neste BPMN — o calendario regulatorio `ans_calendar` e avaliado
ENGINE-SIDE por `BRT_Calendario` no processo de ENVIO, ADR-0028), nenhuma User Task propria
(agendador puro; toda decisao humana vive no processo de envio).

`operadora.ans_cron.check_calendar` **nao existe mais**: era uma re-implementacao Python da
`ans_calendar.dmn`, registrada num topico que nenhum `serviceTask` declarava, e o seu output
`deve_enviar` nao tinha consumidor algum. Com a taxonomia de `report_type` reconciliada, o gate do
ADR-0028 §7 ("only after 100% parity in CI: delete the Python re-implementation") passou a valer —
a paridade e provada engine-side em `tests/integration/dmn/test_dmn_golden_parity.py`.

## Ancora temporal — fuso civil de negocio (nao UTC)

A competencia e derivada da ancora do tick tomada em **`America/Sao_Paulo`** (`_BUSINESS_TZ` em
`src/maezo/tools/workers/ans_cron.py`), **nao** em UTC. Razao: `_compute_competencia` promete
nomear o periodo JA FECHADO; com uma ancora UTC, um tick entre 00:00 e 02:59 UTC do dia 1 (=
21:00-23:59 do ultimo dia do mes anterior no horario civil brasileiro) leria o mes NOVO enquanto o
Brasil ainda esta no ANTIGO, e o "mes imediatamente anterior" segundo o UTC seria um mes que ainda
**nao fechou** para o regulador. A mesma janela de ~3h existe em cada virada de trimestre e de ano.
Fences de fronteira: `tests/unit/tools/workers/test_ans_cron.py`
(`test_ancora_mensal_na_virada_do_mes_usa_o_calendario_brasileiro` e vizinhos).

**Forma do campo e quem pode le-lo.** Com a mudanca de ancora, `competencia_referencia_iso` deixou
de ser a data nua `YYYY-MM-DD` (UTC) e passou a ser um INSTANTE COM OFFSET. `date.fromisoformat`
aceita a primeira forma e **rejeita** a segunda
(`ValueError: Invalid isoformat string: '2026-09-03T06:08:06-03:00'`), entao todo leitor passa
obrigatoriamente por `ans_cron.parse_competencia_referencia_iso` — a UNICA dona do formato, que
aceita as duas formas (a canonica com offset e a legada nua, para fatos ja em transito) e LEVANTA
em lixo, deixando o fail-closed com quem chama. O campo IRMAO `ans_cron_reference_date_iso`
(carimbo do publicador generico) continua sendo DATA NUA UTC de proposito e continua sendo lido com
`date.fromisoformat`: a assimetria entre os dois campos e intencional e esta pinada em
`tests/unit/tools/workers/test_ans_cron.py` (laco produtor->leitor sobre os 5 report_types) e na
suite de integracao.

> **DRAFT/verify regulatorio.** A **escolha do fuso** e um default de ENGENHARIA (a operadora e
> brasileira e os prazos ANS sao publicados em horario civil brasileiro); NAO foi confirmada com o
> regulatorio. Pergunta 2b de SP-OP-ANS-CRON-001 em `docs/sme-dispatch/regulatorio/PACKAGE.md`.

## Calendario automatico desligado (R-007 — F-002, F-121; decisao do dono DEC-03)

Decisao do dono DEC-03 (issue #1081, item R-006 da auditoria FORENSIC-COMPLETENESS-2026-10),
aplicada aqui sem re-decidir: **retirar o timer do SIP** para competencias novas, manter caminho
**manual** para o backlog ate o 4o trimestre/2025, **demais gatilhos so com norma primaria**, e os
placeholders `DRAFT_*` de data saem do `ans_calendar`.

| Gatilho | Base normativa verificada | Estado |
|---|---|---|
| SIP (`RN_124_SIP`; o literal cita a RN 124/2006, que e penalidades e nao o SIP) | RN 639/2025 revoga a RN 551/2022, desobriga o SIP apos o 4o tri/2025, vigente 2026-03-02 (**DL-0029**; aviso ANS conferido em `raw/prazos-verificados.csv`) | definition e DI **removidas** do BPMN; o literal sai da taxonomia do worker. Backlog ate o 4o tri/2025: start **manual** de SP-OP-ANS-SUBMIT-001; `ans_calendar` marca a competencia (`< 2026-01`, forma `YYYY-MM`/`YYYY-Qn`) como `CALENDARIO_MANUAL` e qualquer outra como `REVISAO_HUMANA` (extinta) |
| `RN_209_UTILIZACAO` | nenhuma — RN 209/2009 revogada pela RN 451/2020 | `isExecutable="false"` |
| `RN_388_QUALIDADE` | nenhuma — RN 388/2015 revogada pela RN 483/2022 | `isExecutable="false"` |
| `RN_424_TISS_MONITORAMENTO` | nenhuma — RN 424/2017 trata de junta medica, nao de TISS | `isExecutable="false"` |
| `DIOPS_TRIMESTRAL` | nenhuma norma primaria localizada | `isExecutable="false"` |

**Efeito no engine (provado contra o CIB Seven local):** definition nao-executavel e ignorada no
deploy — nao vira process definition e nao cria job de timer. Nenhum tick automatico acontece; o
envio periodico e manual ate parecer.

**Nenhuma data inventada.** `ans_calendar.dmn` (v0.2.0) devolve `due_date`/`sla_alerta` vazios em
todas as rows, porque nenhuma obrigacao tem data verificada em fonte primaria; a
`fonte_regulatoria` diz o motivo (`CALENDARIO_MANUAL — ...` ou `REVISAO_HUMANA — ...`). Uma data so
entra como ISO `YYYY-MM-DD` real com a norma citada.

**Religar um gatilho** exige, no MESMO PR: parecer COUNSEL (R-006) com norma primaria vigente e
cadencia; `isExecutable="true"` na definition; `timeCycle` ancorado na norma; a data no
`ans_calendar`; e a troca do fence
`tests/unit/spec/test_ans_calendar_r007_fences.py::test_cron_nenhum_gatilho_automatico_sem_norma_primaria`.
Ratificacao juridica antes de qualquer flip.

**Competencia trimestral `YYYY-Qn` (F-121).** O contrato de envio (SP-OP-ANS-SUBMIT-001, entrada)
define `competencia` como `YYYY-MM` (mensal) ou `YYYY-Qn` (trimestral). `_compute_competencia`
passou a devolver o trimestre fechado anterior como `YYYY-Qn` (ex.: `2026-Q1`). O caso anual segue
`YYYY-01` (o contrato de envio nao define forma anual; DRAFT/verify).

## Pendencias para promocao a FINAL

- **Parecer COUNSEL (R-006)** com a norma primaria vigente, a cadencia e a data de cada gatilho
  restante (RN 209/388/424, DIOPS — hoje desligados), incluindo ancora de data (ex.: `R/P1M` a
  partir de que dia do mes). Sem isso nenhum gatilho volta a ser executavel.
- **Confirmacao regulatoria do mapeamento periodo->competencia** (mes/trimestre/ano ANTERIOR ao
  da ancora do tick — assuncao **DRAFT**, `docs/review-queue.md`), incluindo a representacao
  `YYYY-01` escolhida para o caso anual (P12M) por consistencia de forma com o `YYYY-MM` mensal
  dentro da business key `ANSSUB-...` (trimestral ja segue `YYYY-Qn`, F-121). O WIRING ja existe (GAP-ANS-1/ANS-CRON-DEAD-CODE
  fechados: `ST_ResolverCompetencia*` -> `ans_cron.trigger_submissions` ->
  `_compute_competencia`); o que falta e a ratificacao do CONTEUDO.
- **Consumidor vivo da ponte (FINDING #2, ABERTO):** nenhum processo le
  `operadora.notifications.internal` e chama `NotificationBridge.on_event(...)`, entao nenhum
  tick abre de fato uma instancia de SP-OP-ANS-SUBMIT-001 hoje. Fora do escopo deste processo.
- Timer do SIP: **retirado** (R-007). Resta a **re-derivacao** dos literais miscitados
  `RN_209_UTILIZACAO` / `RN_388_QUALIDADE` / `RN_424_TISS_MONITORAMENTO`: decisao de SME/re-scope
  T2.6, nao de engenharia (a taxonomia e chave compartilhada BPMN/DMN/contrato).
- **Confirmacao do fuso civil da ancora** (`America/Sao_Paulo`, hoje um default de engenharia —
  pergunta 2b do despacho regulatorio).
- Revisao humana registrada (regulatorio-ANS) antes de qualquer deploy.
