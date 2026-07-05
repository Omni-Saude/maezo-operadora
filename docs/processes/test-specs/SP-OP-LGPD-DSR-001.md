# Test spec — SP-OP-LGPD-DSR-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra CIB Seven real. Arquivo alvo:
`tests/integration/processes/test_sp_op_lgpd_dsr_001.py`. Dados sinteticos:
`titular_pseudo_id="PSEUDO-TESTE-001"` (Paciente Teste 001), tenant `amh`.
Business key `DSR-amh-PSEUDO-TESTE-001-confirmacao_acesso-2026-06-12`.

## Happy paths

### test_happy_path_acesso_dados_saude_aprovado
- **Given** start `tipo_requisicao=confirmacao_acesso`, `envolve_dados_saude=true`; `verify_identity` retorna `identidade_confirmada=true`
- **When** `compile_data_package` completa; revisor de `juridico-privacidade` (DMN roteia dados de saude para juridico) completa `UT_RevisaoDpo` com `decisao_dsr=APROVAR_ENVIO`
- **Then** `send_response` executado; `lgpd_dsr.completed` publicado; fim `End_RequisicaoConcluida`; `lgpd_dsr.received` foi publicado no inicio

### test_happy_path_correcao_executa_e_envia
- **Given** `tipo_requisicao=correcao` (Paciente Teste 002 corrige endereco), identidade ok
- **When** revisor `dpo` completa com `decisao_dsr=EXECUTAR_E_ENVIAR`
- **Then** `operadora.lgpd.execute_request` executado ANTES de `send_response`; `lgpd_dsr.completed`

### test_negativa_fundamentada_e_decisao_humana
- **Given** `tipo_requisicao=eliminacao` (conflito com retencao de prontuario)
- **When** revisor `juridico-privacidade` completa com `decisao_dsr=NEGAR_FUNDAMENTADO` e `fundamentacao_legal` preenchida
- **Then** `send_response` executado com a fundamentacao; `lgpd_dsr.completed`; NAO existe caminho que negue sem User Task humana

### test_negar_fundamentado_exige_fundamentacao
- **When** complete `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal`
- **Then** task nao completa (guard de worker, `send_response` — defesa em profundidade)

### test_decisao_ausente_ou_desconhecida_nao_libera_dados (GAP-LGPD-4, fail-closed)
- **Given** `tipo_requisicao=confirmacao_acesso`, identidade ok, revisor chega em `UT_RevisaoDpo`
- **When** completa a UT com `decisao_dsr` ausente/em branco/valor desconhecido (nao um dos tres
  valores explicitos)
- **Then** `GW_DecisaoDsr` roteia ao DEFAULT `End_ErrDecisaoInvalida` (`ERR_DSR_DECISION_INVALID`);
  `send_response`/`execute_request` NUNCA executam; nenhum `lgpd_dsr.completed` favoravel e publicado

### test_negar_fundamentado_sem_fundamentacao_guard_estrutural (GAP-LGPD-3, HITL-estrutural)
- **Given** `tipo_requisicao=eliminacao`, identidade ok, revisor chega em `UT_RevisaoDpo`
- **When** completa a UT com `decisao_dsr=NEGAR_FUNDAMENTADO` e `fundamentacao_legal` ausente/em
  branco
- **Then** `GW_GuardFundamentacao` (avaliado pelo ENGINE) roteia ao DEFAULT
  `End_ErrFundamentacaoAusente` (`ERR_DSR_FUNDAMENTACAO_AUSENTE`) ANTES de `ST_EnviarResposta`;
  `send_response` NUNCA executa

## Identidade

### test_identidade_nao_confirmada_pede_prova
- **Given** `verify_identity` retorna `identidade_confirmada=false`
- **Then** `request_additional_proof` executado; instancia aguarda em `GW_AguardarProva`; NENHUM dado compilado antes da confirmacao

### test_prova_recebida_reverifica
- **When** message `msg.lgpd.proof_received` correlacionada
- **Then** `verify_identity` reexecutado; com `true`, segue para `BRT_RotearDsr`

### test_prova_expira_encerra_sem_vazamento
- **When** job do timer `ICE_PrazoProva` (P10D) executado
- **Then** `lgpd_dsr.completed` com `desfecho=expirada_identidade`; fim `End_ExpiradaIdentidade`; `compile_data_package` NUNCA executado

### test_identidade_inverificavel_titular_ausente_boundary_catch (GAP-LGPD-6, dangling-catch fix)
- **Given** start com `titular_pseudo_id=""` (sem sujeito identificavel)
- **When** `operadora.lgpd.verify_identity` levanta `Error_LgpdIdentidade` (`ERR_DSR_IDENTITY_UNVERIFIED`)
- **Then** `BE_IdentidadeInverificavel` captura o erro; `lgpd_dsr.completed` publicado com
  `desfecho=identidade_inverificavel`; fim `End_IdentidadeInverificavel`; `compile_data_package`
  NUNCA executado; `End_RequisicaoConcluida` NUNCA alcancado; nenhuma acusacao automatica de fraude

## Roteamento DMN

### test_dmn_dados_saude_sobe_para_juridico
- **Given/When** `confirmacao_acesso` + `envolve_dados_saude=true`
- **Then** `roteamento_dsr.grupo_revisor == "juridico-privacidade"`

### test_dmn_catchall_tipo_desconhecido_juridico
- **Given/When** `tipo_requisicao=tipo_inexistente`
- **Then** roteado para `juridico-privacidade` (fail-safe)

## Timers

### test_alerta_interno_nao_interruptivo
- **Given** `UT_RevisaoDpo` aberta
- **When** job do timer `BT_AlertaDpo` (`sla_alerta=P7D`) executado
- **Then** `notify_sla_risk` recebeu task; revisao segue aberta

### test_sla_global_15_dias_event_subprocess
- **Given** instancia aberta (em qualquer estado: aguardando prova OU em revisao)
- **When** job do timer do event subprocess `Start_SlaGlobal` (P15D do inicio) executado
- **Then** `lgpd_dsr.sla_breached` publicado; juridico notificado; **a instancia principal segue aberta** (nao-interruptivo — obrigacao legal persiste)

## Idempotencia

### test_business_key_por_titular_tipo_dia
- **Given** instancia ativa para `(PSEUDO-TESTE-001, confirmacao_acesso, 2026-06-12)`
- **When** segundo pedido identico no mesmo dia
- **Then** sem segunda instancia ativa
