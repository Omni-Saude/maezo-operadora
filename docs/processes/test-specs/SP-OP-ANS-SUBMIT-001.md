# Test spec — SP-OP-ANS-SUBMIT-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra CIB Seven **real** (doutrina
"nunca mockar o engine", Phase 0/1). Arquivo alvo:
`tests/integration/processes/test_sp_op_anssubmit_001.py`. Dados sinteticos: tenant `amh`,
`report_type=RN_124_SIP`, `competencia=2026-01`, lote/dataset `DATASET-TESTE-0001`. Business
key `ANSSUB-amh-RN_124_SIP-2026-01`.

> **NAO negativa-like:** este processo nao tem padrao no-denial — nao ha teste de invariante de
> negativa por DMN (nao existe decisao adversa). A salvaguarda equivalente, testada como
> prioritaria, e o **HITL pre-filing** (`test_submit_exige_user_task_humana`): nenhum caminho
> automatizado transmite a ANS. Estilo executavel-stub: guia o teste de integracao real contra o
> engine — sem mock de engine.

## Invariante de filing humano-gated (testes de seguranca — prioritarios)

### test_submit_exige_user_task_humana
- **Given** instancia em qualquer combinacao da DMN `ans_submission_admissibility`
  (`dataset_complete`/`schema_valid`/`lgpd_anonimizado` em {true,false}) que chegue a `SEGUE_ENVIO`
- **When** a instancia percorre ate estabilizar **sem** completar `UT_RevisarEnvio`
- **Then** `regulatorio.anssubmit.submit` **NUNCA** e executado; nenhum `protocolo_ans` e emitido;
  o ato vinculante so ocorre apos `UT_RevisarEnvio` concluida por humano com
  `decisao_envio=APROVAR_ENVIO`. (Consulta `history/activity-instance` do engine: `submit`
  co-ocorre **sempre** com a User Task de aprovacao concluida.)

### test_dados_faltantes_roteiam_para_humano_nunca_auto_rejeita
- **Given** start com `dataset_complete=false` (ou `schema_valid=false`, ou `lgpd_anonimizado=false`)
- **When** `BRT_Admissibilidade` (`ans_submission_admissibility`) avalia
- **Then** retorna `PENDENTE` ou `REVISAO_HUMANA` (nunca um fim de rejeicao automatica); fluxo
  chega a `UT_CorrigirPendenciaEnvio`/`UT_RevisarEnvio` (humano) — **a inelegibilidade de dados
  roteia para humano, nunca auto-rejeita o envio**

### test_aprovar_envio_exige_revisor_id
- **Given** `UT_RevisarEnvio` aberta
- **When** completa com `decisao_envio=APROVAR_ENVIO` **sem** `revisor_id`
- **Then** task NAO completa (validacao de formulario/listener) — envio vinculante sem identidade
  do aprovador e impossivel (nao-repudio ADR-0007)

### test_nip_filing_exige_revisao_juridica
- **Given** start com `origem_envio=nip_filing` e `nip_protocolo_origem=NIP-TESTE-0001`
- **Then** a revisao do envio e roteada a `juridico-regulatorio` (filing de resposta NIP — texto
  legal sempre humano); `submit` so apos a User Task juridica concluida

## Happy paths (golden por tipo de relatorio)

### test_happy_path_envio_aprovado_e_acked
- **Given** `dataset_complete=true, schema_valid=true, lgpd_anonimizado=true`, despacho de envio
  iniciado (`Start_DespachoEnvio` — none start; o cron per-report_type vive em SP-OP-ANS-CRON-001,
  que publica o fato `ans.cron_due` e o `notifications_bridge` inicia este processo seedando as
  variaveis — bk deterministica)
- **When** `regulatorio.anssubmit.assemble` e `validate` completam; `anssubmit.generated` publicado;
  humano `regulatorio-ans` completa `UT_RevisarEnvio` com `decisao_envio=APROVAR_ENVIO` +
  `revisor_id`; `regulatorio.anssubmit.submit` executa; `msg.anssubmit.ack_received` correlacionada
  por `protocolo_ans`
- **Then** `anssubmit.submitted` e `anssubmit.acked` publicados; `anssubmit.completed`
  `desfecho=enviado_ack`; `status_envio=ack`, `data_envio` setado

### test_golden_por_report_type
- **Given/When/Then** parametrizar `report_type` ∈ {`RN_124_SIP`, `RN_209_UTILIZACAO`,
  `RN_388_QUALIDADE`, `RN_424_TISS_MONITORAMENTO`, `DIOPS_TRIMESTRAL`}; cada um:
  - `ans_calendar` resolve `due_date`/`periodicidade`/`fonte_regulatoria` coerentes (valores DRAFT)
  - happy path ate `enviado_ack` com a `competencia` correta na business key
  - `DIOPS_TRIMESTRAL` usa `competencia` `YYYY-Qn` (periodicidade `trimestral`)

### test_ack_pendente_completa_como_enviado_pendente_ack
- **Given** envio transmitido (`submit` ok) mas ACK ainda nao chegou
- **When** `ICE_AguardarAck` (timer `P1D`) expira sem `msg.anssubmit.ack_received`
- **Then** `anssubmit.completed` `desfecho=enviado_pendente_ack` (ou rota a `UT_TratarNack`
  conforme politica) — instancia nao trava indefinidamente; nada e perdido silenciosamente

## Pendencia de dados / adiamento

### test_corrigir_pendencia_reavalia
- **Given** `dataset_complete=false` → `PENDENTE`, `UT_CorrigirPendenciaEnvio` aberta para
  `regulatorio-ans`
- **When** humano corrige e marca dataset completo; `BRT_Admissibilidade` reavaliada com
  `dataset_complete=true`
- **Then** fluxo segue para `UT_RevisarEnvio`

### test_lgpd_nao_anonimizado_roteia_direto_a_revisao_humana (GAP-ANS-5)
- **Given** `dataset_complete=true, schema_valid=true, lgpd_anonimizado=false`
- **When** `BRT_Admissibilidade` avalia (rule `r_revisao_lgpd`, distinta de `r_pendente_dataset`/
  `r_pendente_schema`) → `REVISAO_HUMANA` → `Flow_GW_Revisao` (default) → `ST_PrepararDossie`
- **Then** fluxo alcanca `UT_RevisarEnvio` **diretamente** (NAO `UT_CorrigirPendenciaEnvio`) — uma
  anonimizacao nao confirmada e um julgamento de revisao humana no sign-off, nao um erro de
  pipeline a corrigir antes; o dossie carrega `lgpd_anonimizado=false` (ecoado por
  `regulatorio.anssubmit.assemble`, nunca calculado — `mcp-regdata` AWS-blocked, issue #16)

### test_corrigir_pendencia_lgpd_anonimizado_reavalia (GAP-ANS-5)
- **Given** `lgpd_anonimizado=false` → `REVISAO_HUMANA` → `UT_RevisarEnvio` aberta para
  `regulatorio-ans`
- **When** humano em `UT_RevisarEnvio` completa com `decisao_envio=CORRIGIR_PENDENCIA` →
  `UT_CorrigirPendenciaEnvio`; humano confirma que o dataset (`dataset_ref`) esta de fato
  agregado/anonimizado (ADR-0006) e marca `lgpd_anonimizado=true`; o loop de reavaliacao
  (`BRT_Calendario` → `BRT_AnsSla` → `regulatorio.anssubmit.assemble` →
  `regulatorio.anssubmit.validate` → `BRT_Admissibilidade`) re-executa `assemble`, que ECOA
  `lgpd_anonimizado=true` (nao o reseta — worker nunca calcula o atestado real, so ecoa o fato
  pre-resolvido; `mcp-regdata` AWS-blocked, issue #16)
- **Then** fluxo segue de volta para `UT_RevisarEnvio` com `lgpd_anonimizado=true` preservado
  (nunca fica preso em loop perpetuo em `UT_CorrigirPendenciaEnvio`)

### test_adiar_envio_registra_justificativa
- **Given** `UT_RevisarEnvio` aberta
- **When** humano completa com `decisao_envio=ADIAR_ENVIO`
- **Then** exige `justificativa_adiamento` (sem ela a task nao completa); `anssubmit.completed`
  `desfecho=adiado_humano`; `submit` NAO executado (o nao-envio no prazo e decisao humana
  registrada, nao um efeito automatico)

## Timers de calendario / SLA

### test_dmn_ans_calendar_resolve_due_date
- **Given/When** start com `report_type=RN_124_SIP`, `competencia=2026-01`
- **Then** `calendario.due_date` e `calendario.sla_alerta` resolvidos (datas ISO `YYYY-MM-DD`);
  `calendario.fonte_regulatoria` registrada (valor DRAFT) — assert de shape/tipo, nao do valor
  regulatorio (que e DRAFT/verify)

### test_timer_deadline_risk_nao_interruptivo
- **Given** instancia aguardando `UT_RevisarEnvio`, `calendario.sla_alerta` atingido
- **When** job do timer de alerta executado
- **Then** `regulatorio.anssubmit.notify_regulatorio` recebeu task; `anssubmit.deadline_risk`
  publicado; `UT_RevisarEnvio` segue aberta (nao-interruptivo)

### test_timer_due_date_coordenacao_assume
- **Given** revisao ainda aberta alem de `calendario.due_date`
- **When** job do timer interruptivo executado
- **Then** `UT_RevisarEnvio` cancelada/escalada; `UT_CoordenacaoEnvioAssume`
  (`coordenacao-regulatorio`) criada — decisao de envio continua humana

### test_catch_all_calendar_roteia_para_humano
- **Given** start com `report_type` desconhecido (ex.: `RELATORIO_TESTE_INVALIDO`)
- **Then** `ans_calendar` catch-all retorna `fonte_regulatoria="REVISAO_HUMANA"` com prazos
  conservadores; admissibilidade roteia a `UT_RevisarEnvio` — nunca prazo "infinito" nem envio
  automatico de tipo desconhecido

## Cron per-report_type (SP-OP-ANS-CRON-001 — GAP-ANS-1)

### test_cron_dispara_envio_por_report_type
- **Given** SP-OP-ANS-CRON-001 deployado (5 definitions, um TimerStartEvent por `report_type`)
- **When** os jobs dos timer starts de DOIS tipos (`RN124SIP` mensal e `DIOPS` trimestral,
  parametrizado) sao executados via job-execution (**NUNCA `sleep`**)
- **Then** cada cron publica o fato `ans.cron_due` (`operadora.notifications.internal`) com
  `report_type`/`periodicidade` do SEU proprio start; o fato AINDA carrega o literal BPMN
  `competencia="COMPETENCIA_PENDENTE"`, mas o worker REAL (`operadora.events.publish`) tambem grava
  a ancora `ans_cron_reference_date_iso` (res-ans-competencia-sentinel — DRAFT/verify regulatorio);
  o planejador REAL da ponte (`plan_start`) deriva o start de SP-OP-ANS-SUBMIT-001 com bk
  deterministica `ANSSUB-{tenant}-{report_type}-{competencia}`, onde `competencia` e COMPUTADA da
  ancora (`_competencia_from_anchor`, mes/trimestre imediatamente anterior ao da ancora) — nao mais
  a sentinela fixa; fatos de admissibilidade permanecem fail-closed; `ans_calendar` resolve
  calendarios DISTINTOS por tipo; a admissibilidade roteia `PENDENTE` → `UT_CorrigirPendenciaEnvio`
  (humano permanece o gate — a competencia computada NAO auto-transmite); **nenhum** caminho cron
  transmite (`ST_SubmeterEnvio` ausente do historico — HITL)

### test_dmn_ans_calendar_diferencia_due_date_por_competencia
- **Given/When** `ans_calendar` avaliada no engine real com o MESMO `report_type` e competencias
  diferentes (`RN_124_SIP` × {`2026-01`,`2026-02`}; `DIOPS_TRIMESTRAL` × {`2026-Q1`,`2026-Q2`})
- **Then** `due_date`/`sla_alerta` DIFEREM por competencia (GAP-ANS-2); catch-all conservador
  preservado para tipo desconhecido

## Retry / NACK

### test_nack_entra_em_retry_e_retransmite_sucesso
- **Given** `ST_SubmeterEnvio` executado e o worker `regulatorio.anssubmit.submit` retorna NACK
  transitorio APOS o guard humano → lanca `ERR_ANS_PROTOCOLO_NACK` (boundary `BE_SubmitNack`)
- **When** o subprocess `SUB_RetryEnvio` inicia em `Start_Retry` → `ST_RetransmitirEnvio`
  (topico `regulatorio.anssubmit.retransmit`) → `GW_RetransmissaoOk`; a retransmissao tem sucesso
  (`status_envio=retransmitido`)
- **Then** `End_RetryOk` → `ST_PublishRetransmitido` → reentra no aguardo de ACK (`GW_AguardarAck`);
  o worker de retransmissao **reusa** `decisao_envio==APROVAR_ENVIO`+`revisor_id` (mesmo guard
  `ERR_ANS_SUBMIT_NOT_HUMAN`) e **nunca re-autoriza** — re-assert do invariante HITL pre-filing

### test_retry_esgotado_roteia_para_humano
- **Given** retransmissoes consecutivas retornam NACK (`status_envio=nack`)
- **When** a cada falha `BRT_RetryPolicy` (DMN `ans_retry_policy`, in `retry_attempt`) dirige o
  backoff limitado: `GW_ContinuarRetry` (`continue_retry==true`) → `ICE_Backoff` (timer com as
  duracoes DMN `PT5M`/`PT30M`/`PT2H`) → volta a `ST_RetransmitirEnvio`; a suite avanca o loop por
  execucao do job do timer (**NUNCA `sleep`**)
- **Then** ao esgotar (`continue_retry=false`) → `End_RetryEsgotado` (`ERR_ANS_RETRY_ESGOTADO`)
  capturado pelo boundary `BE_RetryEsgotado` → `ST_PublishFailed` → `UT_TratarNack` (humano) +
  `anssubmit.failed` (`desfecho=falha_retransmissao`, `nack_motivo` preenchido) — nunca encerra
  silenciosamente

### test_err_ans_submit_not_human_recusa_sem_aprovacao
- **Given** tentativa de invocar `regulatorio.anssubmit.submit` sem `decisao_envio=APROVAR_ENVIO`
  setado por humano (teste unitario do worker-guard, complementar ao de integracao)
- **Then** worker recusa com `ERR_ANS_SUBMIT_NOT_HUMAN`; nenhuma transmissao ocorre; exige
  `revisor_id` na cadeia de auditoria

## Idempotencia (por competencia)

### test_business_key_uma_instancia_por_competencia
- **Given** instancia ativa `ANSSUB-amh-RN_124_SIP-2026-01`
- **When** o cron re-dispara para a mesma `report_type`+`competencia` (re-run idempotente)
- **Then** sem segunda instancia ativa; retorna a existente — nunca duplica filing junto a ANS

### test_competencia_diferente_cria_nova_instancia
- **Given** instancia ativa para `2026-01`
- **When** cron dispara `competencia=2026-02`
- **Then** nova instancia `ANSSUB-amh-RN_124_SIP-2026-02` criada (idempotencia e por periodo, nao
  por tipo)
