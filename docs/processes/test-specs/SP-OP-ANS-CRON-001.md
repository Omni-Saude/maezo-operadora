# Test spec — SP-OP-ANS-CRON-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra CIB Seven **real** (doutrina
"nunca mockar o engine", Phase 0/1). Arquivos alvo:

| Arquivo | O que vive la | Roda em CI obrigatorio? |
|---|---|---|
| `tests/integration/processes/test_sp_op_ans_cron_001.py` | o tick real do timer, a competencia no fato, a ausencia de auto-start | nao (job `integration tests (real engine)`, nao obrigatorio) |
| `tests/unit/spec/test_ans_cron_timers_taxonomy.py` | os 3 fences **estaticos** BPMN<->worker (topicos, `timeCycle`x taxonomia, `tenant_id` fora de `event_payload_vars`) | **sim** (job unitario) |
| `tests/unit/tools/workers/test_ans_cron.py` | taxonomia, aritmetica de competencia, fail-closed, ancora temporal | **sim** |
| `tests/integration/dmn/test_dmn_golden_parity.py` | paridade Python<->DMN | nao |

Os tres fences estaticos foram MOVIDOS para `tests/unit/spec/` (eles nao tocam engine): sob o
marker `integration` + o autouse `_skip_if_engine_unreachable` da conftest, nenhum contexto
OBRIGATORIO de CI os executava, e uma regressao (p.ex. `tenant_id` de volta em
`event_payload_vars`, ou um `timeCycle` fora de sincronia) passaria **verde** nos quatro checks
exigidos.

Dados sinteticos: tenant do deployment (fixture `audit_tenant`), `report_type` nos literais
RN-citation (`RN_124_SIP`, `RN_209_UTILIZACAO`, `RN_388_QUALIDADE`, `RN_424_TISS_MONITORAMENTO`,
`DIOPS_TRIMESTRAL`). Nenhum dado clinico, nenhum identificador de beneficiario — o agendador nao
toca PHI.

> **NAO negativa-like:** agendador puro (operadora -> regulador). Nao existe decisao adversa,
> nao existe DMN propria e nao existe User Task propria — logo **nao ha invariante de negativa a
> testar aqui**. A salvaguarda equivalente, o **HITL pre-filing**, vive no processo de envio
> (`SP-OP-ANS-SUBMIT-001`, `test_submit_exige_user_task_humana`) e nao e contornavel por este
> processo: o agendador so publica um FATO; nada nele transmite a ANS.

> **DRAFT/verify regulatorio.** Nenhum teste desta spec assere conteudo regulatorio. As
> periodicidades (`timeCycle` `R/P1M`/`R/P3M`/`R/P1Y`), o mapeamento periodo->competencia
> (mes/trimestre/ano ANTERIOR ao da ancora do tick) e o **fuso civil da ancora**
> (`America/Sao_Paulo`) seguem **nao confirmados** com o regulatorio (`docs/review-queue.md`,
> `docs/sme-dispatch/regulatorio/PACKAGE.md`). Os testes provam **coerencia interna**
> (BPMN <-> worker <-> DMN) e **fail-closed**, nunca correcao regulatoria.

> **DRAFT/verify — `RN_124_SIP` agenda uma obrigacao EXTINTA.** `docs/decisions-log.md:26-27`
> (**DL-0029**, com **DL-0028**) registra, de fonte primaria, que a **RN 639/2025** revoga a RN
> 551/2022, desobriga o envio do **SIP** apos o 4o trimestre/2025 e vigora desde 02/03/2026. O
> despacho ao SME coloca o timer `SP-OP-ANS-CRON-001-RN124SIP` em **retirada cirurgica** e
> **exclui a sua cadencia da revisao** (`docs/sme-dispatch/regulatorio/PACKAGE.md:79-82`). O
> literal aparece nos casos abaixo apenas como **chave de taxonomia compartilhada** (BPMN, DMN,
> contrato, worker) — nenhum caso aqui afirma que a obrigacao esta vigente, e troca-lo/remove-lo e
> decisao de SME/re-scope (T2.6), nao de engenharia. Mesma classe, tambem no despacho
> (`:90-94`): `RN_209_UTILIZACAO` e `RN_388_QUALIDADE` sao citacoes **miscitadas** que precisam de
> re-derivacao.

## Topologia sob teste

Cinco process definitions num unico arquivo BPMN (o engine rejeita multiplos timer starts por
definition — ENGINE-09005, ver o contrato). Cada uma:

```
TimerStartEvent (timeCycle proprio)
  -> ST_ResolverCompetencia*   topic operadora.ans_cron.trigger_submissions
  -> ST_PublishCronDue*        topic operadora.events.publish   -> fato ans.cron_due
  -> End_Cron*
```

**Tecnica de disparo (NUNCA `sleep`):** o `timeCycle` agenda o primeiro tick adiante; o teste
localiza o job de timer-START pendente e o executa na marra
(`engine_rest.py::start_timer_job_id`/`execute_job`, helpers construidos para esta familia).

## Alcancabilidade de topico e taxonomia (GAP-ANS-1 / ANS-CRON-DEAD-CODE / PERSP-B5-ANSCRON-TOPICS)

### test_ans_cron_registered_topics_reachable_from_bpmn (unit/spec, estatico)
- **Given** o XML do BPMN e o `harness` real com `register_ans_cron_workers` aplicado
- **Then** os topicos declarados pelo BPMN sao exatamente
  {`operadora.ans_cron.trigger_submissions`, `operadora.events.publish`}; o unico topico
  `operadora.ans_cron.*` registrado e `trigger_submissions`; e a relacao e **bidirecional** —
  nenhum topico registrado sem `serviceTask` e nenhum `serviceTask` sem worker registrado
- **Racional** `WorkerHarness` despacha por match EXATO de string de topico. Antes deste WP os 2
  topicos registrados (`trigger_submissions` e `check_calendar`) nao apareciam no BPMN, o que
  tornava `_compute_competencia` codigo morto. `check_calendar` foi **removida** (ver abaixo)

### test_ans_cron_timecycle_do_bpmn_bate_com_a_taxonomia_do_worker (unit/spec, estatico)
- **Given** as 5 process definitions
- **Then** cada uma tem **exatamente um** TimerStartEvent com `timeCycle`, e esse `timeCycle`
  corresponde ao periodo ISO que `ans_cron._REPORT_PERIODICIDADE` associa ao `report_type` literal
  do `ST_ResolverCompetencia*` **da mesma definition** (`R/P1M`->`P1M`, `R/P3M`->`P3M`,
  `R/P1Y`->`P12M`); ha **pelo menos 2 periodicidades distintas** entre os tipos (AC de GAP-ANS-1 —
  hoje sao 3: mensal, trimestral, anual)
- **Racional** as duas metades do agendamento per-report_type; se uma mudar sem a outra, a
  competencia deixa de corresponder ao ciclo que a disparou

### test_taxonomia_report_type_e_a_do_bpmn_contrato_e_dmn (unit)
- **Then** `_REPORT_PERIODICIDADE` tem exatamente os 5 literais RN-citation do BPMN/contrato/DMN, e
  **nenhum** literal da taxonomia paralela eliminada (`MAPEAMENTO_REDE`/`DIOPS`/`SIP`/`RPC`/
  `ANS_TISS`/`QUALIFICACAO`, que tinha intersecao VAZIA com o resto do repo) reaparece

### test_ans_calendar_periodicidade_paridade_com_taxonomia_do_worker (DMN, engine real)
- **Given** a tabela `ans_calendar` deployada
- **When** avaliada com cada `report_type` da taxonomia do worker
- **Then** casa uma row PROPRIA (nao a catch-all) e a `periodicidade` devolvida e a MESMA string
  pt-BR do worker — a evidencia de paridade que o gate do ADR-0028 §7 exige para **deletar** a
  re-implementacao Python (`check_calendar`, removida). `due_date`/`sla_alerta` permanecem
  `DRAFT_*` e **nao** sao asseridos como conteudo

### test_ans_calendar_report_type_fora_da_taxonomia_cai_no_catchall (DMN, engine real)
- **Then** um `report_type` desconhecido (inclusive os da taxonomia eliminada) cai na catch-all:
  `fonte_regulatoria=REVISAO_HUMANA` + `periodicidade=indeterminada` — o MESMO literal que
  `trigger_submissions` devolve nesse caso. Fail-closed preservado

## Competencia computada (ANS-CRON-DEAD-CODE — AC principal)

### test_cron_competencia_computada_por_report_type
- **Given** o agendador deployado; parametrizado sobre os **5** `report_type`
- **When** o job do TimerStartEvent daquele tipo e executado e os workers REAIS drenam os dois
  topicos
- **Then** (a) exatamente **1** instancia nova daquela definition; (b) `ST_ResolverCompetencia*`
  consta em `activity-instance` **encerradas** (prova viva de que o topico e alcancavel); (c) o
  ciclo termina em `End_Cron*`; (d) o fato `ans.cron_due` publicado em
  `operadora.notifications.internal` carrega `periodicidade` pt-BR do tipo, `origem_envio=calendario`
  e uma `competencia` **igual** a `_compute_competencia(competencia_referencia_iso, <ISO do tipo>)`
  — derivada da ancora que o proprio fato carrega, nunca de uma data de hoje hardcoded (a assercao
  segue valida se o tick cruzar qualquer virada de dia); (e) `competencia != COMPETENCIA_PENDENTE`
- **Racional** ate este WP o fato viajava com o literal `COMPETENCIA_PENDENTE` embutido no BPMN e
  **toda** instancia SP-OP-ANS-SUBMIT-001 aberta pelo caminho cron nascia pendente de competencia

### test_trigger_submissions_report_type_desconhecido_e_fail_closed (unit)
- **Given** `ans_cron_report_type` fora da taxonomia, ausente, vazio ou so espacos
- **Then** `periodicidade=indeterminada` e `competencia=COMPETENCIA_PENDENTE` — **nunca** um mes
  anterior inventado (o default `P1M` anterior escrevia um periodo plausivel na business key
  `ANSSUB-...` de um tipo que ninguem reconhece)

### test_compute_competencia_{p1m,p3m,p12m}_table (unit)
- **Then** para os 12 meses de um ano de referencia: `P1M` -> mes anterior (inclui o wrap
  Jan->Dez/ano-1); `P3M` -> o trimestre **fechado** anterior, identico para os 3 meses do
  trimestre corrente; `P12M` -> o ano civil anterior, representado `YYYY-01`

### test_compute_competencia_periodicidade_desconhecida_e_fail_closed (unit)
### test_compute_competencia_invalid_reference_date_is_pending_not_a_crash (unit)
- **Then** periodicidade fora de {`P1M`,`P3M`,`P12M`} ou ancora nao parseavel -> sentinela; nunca
  excecao, nunca competencia plausivel

### test_ancora_mensal_na_virada_do_mes_usa_o_calendario_brasileiro (unit)
### test_ancora_mensal_depois_da_virada_no_brasil_fecha_o_mes (unit)
### test_ancora_mensal_no_fim_do_mes_nunca_nomeia_o_mes_aberto (unit)
### test_ancora_trimestral_na_virada_do_trimestre_usa_o_calendario_brasileiro (unit)
### test_ancora_anual_na_virada_do_ano_usa_o_calendario_brasileiro (unit)
- **Given** o relogio congelado (seam `ans_cron._now_business`) num instante das fronteiras de
  mes, trimestre e ano
- **Then** a competencia e a do ultimo periodo fechado no **horario civil brasileiro**: a 00:30
  UTC do dia 1 (= 21:30 do ultimo dia do mes anterior no Brasil) o mes anterior segundo o UTC
  ainda esta **ABERTO** no Brasil, e a resposta correta e o mes ANTERIOR A ELE; a 03:00 UTC do dia
  1 (= 00:00 no Brasil) o mes fecha e passa a ser a competencia
- **Racional** o defeito nao estava no mapeamento (esse ja era correto) e sim na **ancora**: com
  `datetime.now(UTC)` havia uma janela de ~3h em cada virada de mes/trimestre/ano em que o
  agendador nomeava um periodo que ainda **nao havia fechado** para o regulador — violando o
  invariante documentado do proprio `_compute_competencia`. Cada caso compara explicitamente o
  resultado com o que a ancora UTC teria produzido no MESMO instante
- **DRAFT/verify** a **escolha** do fuso (`America/Sao_Paulo`) e um default de engenharia — ver a
  pergunta 2b em `docs/sme-dispatch/regulatorio/PACKAGE.md`

### test_ancora_carrega_offset_explicito_e_nao_e_data_nua (unit)
### test_business_tz_e_o_fuso_civil_brasileiro (unit)
### test_now_business_devolve_instante_aware_no_fuso_de_negocio (unit)
- **Then** `competencia_referencia_iso` viaja em ISO-8601 **com offset**
  (`2026-02-28T20:30:00-03:00`), a constante `_BUSINESS_TZ` e `America/Sao_Paulo` e a seam de
  relogio devolve sempre um `datetime` **aware** — sem offset o consumidor do fato nao consegue
  dizer em que calendario civil o periodo foi fechado

### test_trigger_submissions_nao_devolve_fato_fabricado (unit)
- **Then** o retorno tem exatamente
  {`report_type`, `periodicidade`, `competencia`, `competencia_referencia_iso`, `tenant_id`} —
  sem `fato_publicado`/`event_type`: esta funcao **nao publica nada** (quem publica e o
  `ST_PublishCronDue*` seguinte), e afirmar publicacao era um fato fabricado (classe GAP-FAB-NOTIF)

### test_trigger_submissions_ignora_report_type_do_escopo_de_processo (unit)
- **Then** o tipo vem SO do literal LOCAL `ans_cron_report_type`; um `report_type` ja existente no
  escopo de processo nunca e a fonte (racional de escopo Camunda: um literal chamado `report_type`
  capturaria localmente o valor devolvido e ele nunca chegaria ao task de publish)

## Tenant e fronteira com a ponte

### test_ans_cron_payload_vars_nao_carregam_tenant_id (unit/spec, estatico)
- **Then** nenhum `event_payload_vars` das 5 definitions lista `tenant_id` (e todos listam
  `report_type`/`periodicidade`/`competencia`)
- **Racional** o agendador e tenant-agnostico; o tenant do fato vem do carimbo `setdefault` do
  publicador generico (`events.py`, seam `deployment_tenant_id`). Listar `tenant_id` faria o `""`
  de uma composicao sem seam viajar no payload, o `setdefault` nao sobrescreveria, e a regra
  `ans.cron_due` da ponte (que exige tenant nao-vazio) ficaria dormente ou geraria a chave
  degenerada `ANSSUB--...`

### test_cron_dispara_fato_e_nao_inicia_submit_automaticamente
- **Given** agendador **e** SP-OP-ANS-SUBMIT-001 (+ suas 4 DMNs) genuinamente deployados — para
  que a prova "nenhuma instancia nova" seja significativa, nao vacua contra uma key inexistente
- **When** o tick e drenado
- **Then** o fato e publicado **com** `tenant_id` = tenant do deployment (carimbo do publicador),
  e o diff de `instance_ids_of_definition("SP-OP-ANS-SUBMIT-001")` antes/depois e **vazio**
- **Racional (FINDING #2, lacuna ABERTA e fora do escopo deste processo)** a `NotificationBridge`
  TEM a regra `ans.cron_due` -> SP-OP-ANS-SUBMIT-001 com business key deterministica
  `ANSSUB-{tenant}-{report_type}-{competencia}`, mas **nenhum consumidor vivo** le
  `operadora.notifications.internal` e chama `bridge.on_event(...)`. O teste registra o estado
  real, honestamente — nao finge um seam que nao existe

### test_notification_bridge_ans_cron_rule_now_wired
- **Then** existe exatamente **1** regra para `ans.cron_due`, alvo `SP-OP-ANS-SUBMIT-001`; o
  predicado exige `tenant_id` **e** `report_type` nao vazios (fail-closed); total de handoffs = 7

## Idempotencia (coberta a jusante, registrada aqui)

O re-tick do timer e a reentrega Kafka convergem para a MESMA business key
`ANSSUB-{tenant}-{report_type}-{competencia}` — dentro de um mesmo periodo fechado a competencia
computada e **estavel** (todos os dias do mes/trimestre/ano corrente resolvem o mesmo periodo
anterior; pinado pelas tabelas de 12 meses acima). O `find_active_instance` antes do start vive no
starter fenced (`start_process_idempotent`), exercitado em
`tests/integration/processes/test_t33_a2_agent_start_idempotency_matrix.py`. **SP-OP-ANS-CRON-001
nao esta em `KNOWN_PROCESS_KEYS` por design** (e iniciado por timer, nunca por
`start_process_idempotent`) e este WP nao mudou isso.

## Fora de escopo (registrado, nao testado aqui)

- Consumidor vivo de `operadora.notifications.internal` (FINDING #2) — o seam ponta a ponta.
- Confirmacao regulatoria dos `timeCycle`, das periodicidades e do mapeamento
  periodo->competencia, incluindo a ancora de dia do mes de cada `R/P1M` (**DRAFT/verify**,
  `docs/review-queue.md`).
- Conteudo de `due_date`/`sla_alerta` de `ans_calendar` (`DRAFT_*`, ratificacao humana pendente).
- Confirmacao regulatoria do **fuso civil** que define a ancora (`America/Sao_Paulo` e um default
  de engenharia — pergunta 2b do despacho regulatorio).
- **Retirada** do timer `RN_124_SIP` (obrigacao extinta, RN 639/2025 — DL-0028/DL-0029) e
  re-derivacao de `RN_209_UTILIZACAO`/`RN_388_QUALIDADE`: decisao de SME/re-scope T2.6.
