# Test spec — SP-OP-PROGRAMA-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (ADR-0011: sem mock de
engine). Arquivo alvo: `tests/integration/processes/test_sp_op_programa_001.py`. O modulo
**self-contains** suas fixtures (`engine`, `deploy_artifacts`, `programa_probe`, `start_programa`),
espelhando `test_sp_op_pagto_001.py`/`test_sp_op_cancel_001.py`.

Dados sinteticos: `beneficiario_pseudo_id` `bnf-teste-NNNNNNNN` (pseudonimo, ADR-0006 — NUNCA CPF/
nome), `programa_id` `cronicos`/`pre-natal`, `ciclo` `2026-Q2`, tenant `amh`. Business key
`PROG-{tenant}-{programa_id}-{beneficiario_pseudo_id}-{ciclo}`. Process key: `SP-OP-PROGRAMA-001`
(exato — nao alterar).

`ProgramaEngineProbe` drena as external tasks com os workers reais Phase-3
(`register_programa_workers`) + `register_phase0_workers`. Os FATOS de estratificacao
(`risco_estratificado`, `elegibilidade_criterios_atendidos`) sao **seeded** como variaveis de
START (o teste e o agente de origem) porque `BRT_Routing` roda ANTES de qualquer worker de
estratificacao completar — mesma tecnica de `pagto_alcada`/`valor_pagamento_cents`.

## Invariante (A) — CHOKEPOINT de consentimento (LGPD art. 7/art. 11)

Espelha `docs/processes/contracts/SP-OP-PROGRAMA-001.md` §Invariante A: NENHUM PHI de programa e
processado antes de `check_consent` confirmar consentimento ativo (e, no canal proativo,
`consent_checked`). `ST_CheckConsent` e o UNICO gate antes de `SUB_Cuidado`.

### test_chokepoint_consentimento_nenhum_phi_sem_consentimento
- **Given** `consentimento_ativo=False`
- **When** a instancia percorre ate estabilizar
- **Then** `ERR_PROGRAMA_NO_CONSENT` (boundary error) → `programa.consent_blocked` publicado →
  `End_SemConsentimento`; NENHUM worker de PHI (`stratify_risk`/`build_care_plan`/
  `proactive_contact`) executado; `SUB_Cuidado` nunca entra (`ST_StratifyRisk` ausente da
  historia); nenhum terminal adverso.

### test_canal_proativo_sem_consent_checked_barra
- **Given** `gatilho=canal_proativo`, `consentimento_ativo=True`, `consent_checked=False`
- **Then** o chokepoint barra fail-closed (D9 exige `consent_checked==true` no canal proativo) →
  `End_SemConsentimento`; nenhum worker de PHI executado.

## Invariante (B) — REVOGACAO = boundary interruptivo (fail-safe LGPD, NAO adverso)

Espelha §Invariante B: `SUB_Cuidado` carrega um interrupting boundary message event
(`msg.programa.consent_revoked`) que para o tratamento de PHI a qualquer momento — NUNCA um
efeito adverso; e o comportamento seguro/legal (LGPD art. 8 §5/art. 18 §2).

### test_revogacao_interrompe_processamento
- **Given** consentido, risco alto (`SUB_Cuidado` ativo, `UT_DecisaoClinica` aberta)
- **When** `msg.programa.consent_revoked` correlacionado por **business_key** (canal direto/agente)
- **Then** boundary interruptivo dispara → `operadora.programa.stop_processing` executado →
  `programa.processing_stopped` publicado → `End_ProcessamentoInterrompidoRevogacao`; **NUNCA**
  `End_DesligamentoClinicoHumano` (revogacao e fail-safe, distinta de desligamento clinico).

### GAP-PROG-4 — `consent_revocation_bridge`: correlacao por VARIAVEIS (nao business_key unico)

A `revogacao_consentimento` de SP-OP-LGPD-DSR-001 e a origem canonica de
`msg.programa.consent_revoked` (contrato §Notas de design) mas, ate a fix, **nenhum
produtor/correlator** ligava as duas pontas. A `consent_revocation_bridge`
(`src/maezo/platform/integrations/consent_revocation_bridge/`) consome
`agents.events.lgpd_dsr.completed` (topico JA existente) e, quando `tipo_requisicao=
revogacao_consentimento` E `decisao_dsr=EXECUTAR_E_ENVIAR`, correlaciona por
`correlation_keys={tenant_id, beneficiario_pseudo_id}` + `all_matching=True` — a revogacao e
TITULAR-WIDE para `consent_scope=programa_cuidado` (deve interromper TODO processamento do
beneficiario, nao uma unica inscricao). Extensao de `mcp_cibseven.CibSevenServer.correlate_message`
(`correlation_keys`/`all_matching`, delega o fan-out ao `POST /message` `correlationKeys`+`all` do
proprio engine — API padrao Camunda 7/CIB Seven).

### test_bridge_correlaciona_revogacao_por_correlation_keys_instancia_unica
- **Given** consentido, risco alto (`UT_DecisaoClinica` aberta)
- **When** `msg.programa.consent_revoked` correlacionado por `correlation_keys=
  {tenant_id, beneficiario_pseudo_id}` + `all_matching=True` (POST /message cru — mesmo shape que
  `CibSevenHttpTransport.correlate_message` envia; NAO usa business_key)
- **Then** MESMO desfecho de `test_revogacao_interrompe_processamento`:
  `End_ProcessamentoInterrompidoRevogacao`, `stop_processing` executado,
  `programa.processing_stopped` publicado.

### test_bridge_correlaciona_revogacao_fan_out_multiplas_instancias_do_titular
- **Given** o MESMO titular com **duas** instancias PROGRAMA-001 ativas
  (`programa_id=cronicos`/`programa_id=pre-natal`, mesmo `ciclo`), ambas com `UT_DecisaoClinica`
  aberta
- **When** **uma unica** correlacao `correlation_keys={tenant_id, beneficiario_pseudo_id}` +
  `all_matching=True`
- **Then** **AMBAS** as instancias atingem `End_ProcessamentoInterrompidoRevogacao` — o fan-out e
  resolvido pelo ENGINE (prova contra o engine real que `correlationKeys`+`all` casa multiplas
  instancias ativas simultaneamente, nao so a primeira).

## Invariante (C) — NO-ADVERSE clinico (DoD deliverable, ADR-0018 clinical_decision L0 hard)

Espelha §Invariante C: o desligamento clinico do programa e L0-hard e SO nasce em
`UT_DecisaoClinica`/`UT_CoordenacaoDecisao` com `decisao_programa=DESLIGAR_CLINICO` humano. NENHUMA
DMN deste processo tem saida de desligamento/alta/negativa (`programa_routing` so estratifica).

### test_nenhum_caminho_automatizado_desliga_clinicamente
- **Given** varredura de combinacoes de input da DMN `programa_routing` (com consentimento ativo):
  `risco_estratificado ∈ {baixo, moderado, alto, indeterminado}` × `elegibilidade_criterios_
  atendidos ∈ {true, false}` (4 × 2 = **8 combinacoes**)
- **When** cada instancia percorre ate estabilizar, **sem completar nenhuma User Task**
- **Then** NENHUMA atinge `End_DesligamentoClinicoHumano` automaticamente; risco alto NAO desliga —
  so roteia a `ANALISE_HUMANA` (clinico humano). Prova por historia do engine
  (`_assert_no_adverse_without_human_task`).

### test_estratificacao_alta_roteia_para_humano_nunca_desliga
- **Given** `risco_estratificado=alto`, `elegibilidade_criterios_atendidos=True`
- **Then** `programa_routing=ANALISE_HUMANA` → `UT_DecisaoClinica` criada
  (`coordenacao-clinica`/`equipe-cuidado`); NUNCA desliga sozinho.

## Happy paths (caminho L3 consentido + decisao humana)

### test_happy_path_enrollment_elegivel_l3
- **Given** consentido, `risco_estratificado=moderado`, `elegibilidade_criterios_atendidos=True`
- **Then** `programa_routing=ELEGIVEL` → `ST_ProactiveContact` executado (`consent_checked==true`)
  → `End_EnrollmentRealizado`; **nenhuma** User Task criada (caminho L3); `register_program_
  discharge` NUNCA invocado; `programa.completed` (`desfecho=enrollment_realizado`, GAP-PROG-2).

### test_happy_path_nao_elegivel_neutro
- **Given** `risco_estratificado=baixo`, `elegibilidade_criterios_atendidos=False`
- **Then** `programa_routing=NAO_ELEGIVEL` → `End_NaoElegivel` (neutro — **NAO** e negativa de
  cobertura); `programa.completed` (`desfecho=nao_elegivel`).

### test_happy_path_desligamento_clinico_humano
- **Given** `UT_DecisaoClinica` aberta (risco alto)
- **When** clinico completa `decisao_programa=DESLIGAR_CLINICO` + `motivo_desligamento_clinico` +
  `referencia_clinica` + `responsavel_clinico_id`
- **Then** `register_program_discharge` executado (guard satisfeito) → `End_DesligamentoClinico
  Humano`; `programa.completed` (`desfecho=desligamento_clinico_humano`, `responsavel_clinico_id`
  na trilha de auditoria ADR-0007). **UNICO** caminho ao terminal adverso clinico.

### test_happy_path_enroll_humano
- **Given** `UT_DecisaoClinica` aberta
- **When** clinico completa `decisao_programa=ENROLL`
- **Then** `End_EnrollmentRealizadoHumano` (neutro, consentido); `programa.completed`
  (`desfecho=enrollment_realizado` — mesmo desfecho do ramo L3).

### test_happy_path_manter_acompanhamento
- **Given** `UT_DecisaoClinica` aberta
- **When** clinico completa `decisao_programa=MANTER_ACOMPANHAMENTO` (default)
- **Then** `End_AcompanhamentoConcluido` (neutro); `programa.completed`
  (`desfecho=acompanhamento_concluido`).

## Desligamento exige campos (worker guard — defesa em profundidade)

### test_desligar_exige_campos_worker_guard
- **Given** `UT_DecisaoClinica` aberta
- **When** clinico completa `DESLIGAR_CLINICO` **sem** `motivo_desligamento_clinico`/
  `referencia_clinica`/`responsavel_clinico_id`
- **Then** `ERR_PROGRAM_DISCHARGE_NOT_HUMAN`; a instancia **NAO** atinge
  `End_DesligamentoClinicoHumano`; `programa.completed` adverso **NAO** publicado.

## Timers de SLA (decisao clinica)

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_DecisaoClinica` aberta
- **When** job do timer nao-interruptivo `BT_AlertaSlaPrograma` executado
- **Then** `notify_sla_risk` recebeu task; a UT **continua aberta** (nao-interruptivo nao cancela).

### test_timer_sla_estourado_coordenacao_assume
- **Given** `UT_DecisaoClinica` aberta
- **When** job do timer interruptivo `BT_SlaDecisao` executado
- **Then** `programa.sla_breached` publicado; `UT_DecisaoClinica` cancelada; `UT_CoordenacaoDecisao`
  criada (`coordenacao-clinica`); nenhum desfecho adverso automatico (INVERTE
  `Task_AutoApprove`/timeout).

### test_coordenacao_assume_e_desliga
- **Given** SLA estourado (`UT_CoordenacaoDecisao` aberta)
- **When** coordenacao completa `DESLIGAR_CLINICO` + campos obrigatorios
- **Then** `End_DesligamentoClinicoHumano` — mesmo no escalonamento, o desligamento passa por UT
  humana (invariante C nunca muda de natureza por estouro de SLA).

### test_dmn_programa_sla_resolve_timers
- **Given** `programa_routing=ANALISE_HUMANA`
- **Then** os jobs de timer `BT_AlertaSlaPrograma`/`BT_SlaDecisao` existem (prova indireta de que a
  DMN `programa_sla` resolveu `${sla.sla_alerta}`/`${sla.sla_decisao}` corretamente).

## Pendencia de informacao clinica (humano solicita)

### test_solicitar_info_aguarda_correlacao
- **Given** `UT_DecisaoClinica` aberta
- **When** clinico completa `SOLICITAR_INFO`, depois `msg.programa.info_received` correlacionado
  (business key)
- **Then** `UT_DecisaoClinica` reaberta (`coordenacao-clinica`/`equipe-cuidado`); NUNCA desliga
  automaticamente.

## DMN — shape e fail-safe (varredura estatica do XML, sem engine)

### test_programa_routing_sem_saida_adversa
- **Given/Then** o dominio de `elegivel_programa` da DMN `programa_routing` e EXATAMENTE
  `{ELEGIVEL, NAO_ELEGIVEL, ANALISE_HUMANA}` — nenhum valor `DESLIGAR`/`ALTA`/`NEGAR`; row
  catch-all → `ANALISE_HUMANA`.

### test_programa_dmn_typeref_allowlist
- **Given/Then** toda DMN do processo (`programa_routing`, `programa_sla`) usa `typeRef ∈ {string,
  boolean, integer, long, double, date}` — `"number"` proibido por construcao (ADR-0018 parte 2).

### test_programa_sla_sem_saida_adversa
- **Given/Then** `programa_sla` so produz prazos ISO + fonte — nenhuma saida adversa.

### test_programa_bpmn_desligamento_so_apos_user_task
- **Given/Then** prova estatica: `End_DesligamentoClinicoHumano` so e alcancado via
  `ST_RegisterDischarge`, cujo unico predecessor e `GW_Decisao`, alimentado SO por
  `UT_DecisaoClinica`/`UT_CoordenacaoDecisao` (nunca uma DMN/service-task automatica).

## Idempotencia (business key)

### test_business_key_uma_instancia_por_ciclo
- **Given** `PROG-{tenant}-{programa_id}-{beneficiario_pseudo_id}-{ciclo}`
- **When** a instancia esta ativa (`UT_DecisaoClinica` aberta)
- **Then** `find_active_instances` retorna EXATAMENTE 1 instancia para essa business key — nao cria
  uma segunda instancia para o mesmo (programa × beneficiario × ciclo).
