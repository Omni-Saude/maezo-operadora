# Test spec — SP-OP-ANS-CRON-001 (DRAFT — acompanha o processo; PERSP-C5-ANSCRON-TESTSPEC)

Este test-spec estava AUSENTE (`ls docs/processes/test-specs/` listava as 15 chaves de
`KNOWN_PROCESS_KEYS`, nenhuma delas `SP-OP-ANS-CRON-001` — o ANS-CRON e timer-started, excluido
por desenho da allowlist, `process_allowlist.py:4,20`). A ausencia da allowlist e declarada; a
ausencia deste arquivo nao era — a suite de integracao ja existe
(`tests/integration/processes/test_sp_op_ans_cron_001.py`, marker `integration`, engine CIB Seven
real). Este documento descreve os testes JA EXISTENTES; nao inventa nenhum cenario novo.

**Natureza:** agendador puro (operadora -> regulador). Nao ha padrao HITL/no-denial proprio (nenhuma
User Task, nenhuma DMN — `grep -o 'camunda:decisionRef' spec/processes/bpmn/SP-OP-ANS-CRON-001_*.bpmn`
-> 0 matches); todo terminal e NEUTRO. O agendador deploya 5 process definitions a partir de UM
arquivo BPMN (`SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn`), um por `report_type`
(`RN124SIP`/`RN209`/`RN388`/`RN424TISS`/`DIOPS`), cada um com exatamente um `TimerStartEvent` e um
unico service task (`ST_PublishCronDue*`, topico `operadora.events.publish`).

## Achados estruturais que o test-spec cobre (grep-confirmados no HEAD desta branch)

### FINDING #1 — topicos registrados por `ans_cron.py` sao inalcancaveis a partir do BPMN
`register_ans_cron_workers` (`src/maezo/tools/workers/ans_cron.py`) registra dois `FunctionWorker`
sob `operadora.ans_cron.trigger_submissions`/`operadora.ans_cron.check_calendar` — nomes que NAO
aparecem em nenhum dos 5 service tasks do BPMN deployado (todos usam
`camunda:topic="operadora.events.publish"`, o handler generico). Consequencia: a logica propria de
`ans_cron.py` (`_compute_competencia`, `deve_enviar`/`motivo`) nunca roda contra o engine real; so o
handler generico de `operadora.events.publish` (`register_events_workers`) serve essas tasks.

### FINDING #2 — a `NotificationBridge` tem regra para `ans.cron_due`, mas nenhum consumidor a invoca
`NotificationBridge` ganhou (T2.6-7) uma regra `ans.cron_due` -> `SP-OP-ANS-SUBMIT-001`
(`count_handoffs() == 7`), mas nada no runtime le `operadora.notifications.internal` e chama
`NotificationBridge.on_event(...)` — por isso nenhuma instancia de SP-OP-ANS-SUBMIT-001 nasce
automaticamente do tick do cron hoje, mesmo com a regra existindo.

## Testes (arquivo alvo: `tests/integration/processes/test_sp_op_ans_cron_001.py`)

### test_ans_cron_registered_topics_unreachable_from_bpmn
- **Given** o XML do BPMN do agendador e um `WorkerHarness` real com
  `register_ans_cron_workers` chamado
- **When** compara-se `bpmn_topics` (regex sobre o XML) com `harness.registered_topics`
  filtrado a `operadora.ans_cron.*`
- **Then** os dois conjuntos NAO se interceptam — prova estatica (sem engine, sempre verde) da
  FINDING #1

### test_notification_bridge_ans_cron_rule_now_wired
- **Given** uma `NotificationBridge()` nova
- **When** consulta-se `get_handoff("ans.cron_due")`
- **Then** existe EXATAMENTE 1 regra, alvo `SP-OP-ANS-SUBMIT-001`; o predicado exige
  `tenant_id` E `report_type` (fail-closed: fato sem `tenant_id` nao dispara);
  `count_handoffs() == 7` (as 5 regras pre-T2.6-7 + NIP + este cron)

## Seam contra o engine REAL (`tests.mark.integration`)

### test_cron_dispara_fato_e_nao_inicia_submit_automaticamente (parametrizado pelas 5 report_type/periodicidade)
- **Given** o agendador CRON + SP-OP-ANS-SUBMIT-001 (BPMN + 4 DMNs) deployados no engine real
- **When** o `TimerStartEvent` de um `report_type` e executado (`start_timer_job_id`/`execute_job`
  — nunca `sleep`) e o worker generico (`operadora.events.publish`) drena a external task
- **Then**:
  1. exatamente 1 instancia do process definition per-tipo nasce e completa (`End_Cron*`);
  2. o fato tipado `ans.cron_due` e publicado em `operadora.notifications.internal` com
     `report_type`/`periodicidade`/`origem_envio="calendario"` corretos e
     `competencia="COMPETENCIA_PENDENTE"` (literal do BPMN — GAP-ANS-1, nao computado; ver
     contrato `docs/processes/contracts/SP-OP-ANS-CRON-001.md`) e `tenant_id` carimbado pelo
     publicador generico (nunca sobrescreve um `tenant_id` vindo de variavel de processo) e
     `ans_cron_reference_date_iso` (data ISO do instante do tick);
  3. NENHUMA instancia de `SP-OP-ANS-SUBMIT-001` nasce (diff de
     `instance_ids_of_definition` antes/depois do drain é vazio) — FINDING #2: a regra da
     bridge existe, mas nenhum consumidor a invoca.

## O que este test-spec NAO cobre (fora do escopo desta familia)

- O conteudo/aprovacao de SP-OP-ANS-SUBMIT-001 (test-spec proprio,
  `docs/processes/test-specs/SP-OP-ANS-SUBMIT-001.md`) — este arquivo cobre so o lado
  agendador/dispatch-por-fato.
- Resolucao automatica de `competencia` (GAP-ANS-1, ABERTO) — a funcao que faria isso
  (`ans_cron._compute_competencia`) existe mas e codigo morto (FINDING #1); nao ha teste
  cobrindo um calculo que nenhum caminho do engine alcanca.
