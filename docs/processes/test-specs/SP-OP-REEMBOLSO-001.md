# Test spec — SP-OP-REEMBOLSO-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" de Phase 0/1; estes stubs guiam o teste de integracao contra o
engine real numa wave posterior). Arquivo alvo:
`tests/integration/processes/test_sp_op_reembolso_001.py`. Dados sinteticos: protocolo
`REEMB-TESTE-0001`, beneficiario pseudonimizado `benef-TESTE-001`, matricula
`MAT-TESTE-001`, CPF invalido de teste (faixa `000.000.000-00`), tenant `amh`. Business key
`REEMB-amh-REEMB-TESTE-0001`. Valores em **centavos inteiros** (ex.: `valor_solicitado_cents=12000` = R$120,00).

## Invariante L0 (testes de seguranca — prioritarios)

### test_nenhum_caminho_automatizado_nega_reembolso
- **Given** varredura de TODAS as combinacoes das entradas das DMNs: `cobertura_prevista`, `documentacao_completa`, `dentro_prazo`, `beneficiario_ativo`, `carencia_cumprida`, `dentro_tabela`, `dentro_teto_l2`, `requer_avaliacao_clinica` em {true, false}; e `valor_solicitado_cents` em {0, abaixo-da-tabela, igual-tabela, acima-da-tabela}
- **When** a instancia percorre ate estabilizar (consulta `history/activity-instance` do engine)
- **Then** NUNCA atinge `End_ReembolsoNegado` nem `End_ReembolsoParcial` sem uma User Task humana (`UT_AnaliseReembolso`/`UT_RevisaoAuditorMedico`/`UT_CoordenacaoReembolso`) **concluida** no history com `decisao_reembolso ∈ {NEGAR, APROVAR_PARCIAL}`. Nenhuma DMN produz a negativa/reducao; o end-event adverso so co-ocorre com a User Task humana.

### test_negar_reembolso_exige_campos
- **Given** `UT_AnaliseReembolso` aberta
- **When** completa com `decisao_reembolso=NEGAR` sem `justificativa`/`fundamentacao_contratual` (e, no merito clinico, sem `cid10_referencia`/`parecer_auditor`)
- **Then** a task NAO completa (validacao de formulario/listener) — negativa sem fundamentacao e impossivel

### test_aprovar_parcial_exige_campos_e_valor_menor
- **Given** `UT_AnaliseReembolso` aberta com `valor_solicitado_cents=12000`
- **When** completa com `decisao_reembolso=APROVAR_PARCIAL` sem `justificativa`/`fundamentacao_contratual`, ou com `valor_reembolso_aprovado_cents >= valor_solicitado_cents`
- **Then** a task NAO completa — reducao exige justificativa + valor aprovado estritamente menor que o solicitado

### test_inelegibilidade_roteia_para_humano (nunca auto-nega)
- **Given** start com `cobertura_prevista=false` (e, em variantes, `dentro_prazo=false` / `carencia_cumprida=false` / `beneficiario_ativo=false`)
- **Then** a DMN `reembolso_admissibility` retorna `ANALISE_HUMANA` (jamais uma saida de negativa); o fluxo chega a `UT_AnaliseReembolso` — **nao** a um fim de negativa. Inelegibilidade aparente nunca vira `End_ReembolsoNegado` automatico

### test_worker_guard_recusa_negativa_sem_humano
- **Given** tentativa de executar `operadora.reembolso.send_reembolso_denial` com `decisao_reembolso` ausente ou setado fora de uma User Task humana (sem `analista_id`/`auditor_id`)
- **Then** o worker recusa com `ERR_REEMBOLSO_DENIAL_NOT_HUMAN`; nenhum efeito adverso e emitido; a instancia nao alcanca `End_ReembolsoNegado`/`End_ReembolsoParcial`

### test_fora_de_tabela_nao_auto_aprova
- **Given** procedimento sem tabela de referencia → `reembolso_calculo` retorna `valor_calculado_tabela_cents=0`, `fonte_tabela="SEM_TABELA"` → `dentro_tabela=false`
- **Then** `reembolso_auto_approval` retorna `ANALISE_HUMANA`; nenhuma auto-aprovacao fora de tabela; vai a `UT_AnaliseReembolso`

## Happy paths

### test_happy_path_aprovacao_automatica_l2
- **Given** `cobertura_prevista=true, documentacao_completa=true, dentro_prazo=true, beneficiario_ativo=true, carencia_cumprida=true, dentro_tabela=true, dentro_teto_l2=true, requer_avaliacao_clinica=false`, `valor_solicitado_cents` igual ao calculado pela tabela
- **When** a instancia percorre
- **Then** `reembolso_auto_approval=AUTO_APROVAR`; `operadora.reembolso.issue_payment` executado com `valor_reembolso_aprovado_cents == valor_calculado_tabela_cents == valor_solicitado_cents` (aprovacao **integral**, nunca parcial); `reembolso.completed` com `desfecho=aprovado_automatico`; fim `End_ReembolsoAprovadoAutomatico`; NENHUMA User Task criada

### test_happy_path_aprovado_pelo_analista
- **Given** `dentro_teto_l2=false` (vai a analise); dossie preparado (`operadora.reembolso.analyze_request` completa)
- **When** o analista (`analise-reembolso`) completa com `decisao_reembolso=APROVAR` e `valor_reembolso_aprovado_cents` definido
- **Then** `operadora.reembolso.issue_payment` executado; `reembolso.completed` com `desfecho=aprovado_analista`; fim `End_ReembolsoAprovadoAnalista`

### test_happy_path_negado_pelo_analista
- **Given** analise humana aberta
- **When** o analista completa `NEGAR` com `justificativa` + `fundamentacao_contratual`
- **Then** `operadora.reembolso.send_reembolso_denial` executado (guard satisfeito; carrega `analista_id`); `reembolso.completed` com `desfecho=negado_analista`; fim `End_ReembolsoNegado`

### test_happy_path_aprovado_parcial_pelo_analista
- **Given** analise humana aberta com `valor_solicitado_cents=12000` e `valor_calculado_tabela_cents=8000`
- **When** o analista completa `APROVAR_PARCIAL` com `justificativa` + `fundamentacao_contratual` + `valor_reembolso_aprovado_cents=8000`
- **Then** `operadora.reembolso.send_reembolso_denial` (comunica a reducao; guard satisfeito) **e** `operadora.reembolso.issue_payment` (paga 8000); `reembolso.completed` com `desfecho=aprovado_parcial`; fim `End_ReembolsoParcial`

### test_revisao_auditor_medico_decide_merito
- **Given** `requer_avaliacao_clinica=true` (ou analista escolhe encaminhar ao auditor)
- **When** `UT_RevisaoAuditorMedico` (`medico-auditor`) completa — o auditor decide o merito clinico (APROVAR/NEGAR/APROVAR_PARCIAL com `parecer_auditor`)
- **Then** o desfecho segue a decisao do auditor; uma negativa/reducao por merito clinico exige `parecer_auditor` + `cid10_referencia`. O DMN calcula o valor; **o humano decide pagar/negar**

## Pendencia de documentacao (sub-fluxo portado do AUTH-001)

### test_pendencia_docs_recebidos_reavalia
- **Given** `documentacao_completa=false` → `reembolso.pended` publicado, aguardando em `GW_AguardarDocs`
- **When** message `msg.reembolso.docs_received` correlacionada (business key) com `documentacao_completa=true`
- **Then** `BRT_Admissibilidade` reavaliada; o fluxo segue para analise

### test_pendencia_expira_decisao_humana_nunca_auto_nega
- **Given** aguardando docs
- **When** o job do timer `ICE_PrazoPendencia` (P5D) executa
- **Then** `UT_DecidirPendenciaExpirada` criada para `analise-reembolso`; com `decisao_pendencia=cancelar_solicitacao` → `reembolso.completed` `desfecho=cancelado_pendencia` (cancelamento por inacao do beneficiario — **nao** e negativa de cobertura); com `conceder_prazo_extra`/`seguir_analise` → volta ao fluxo de analise. **A expiracao nunca atinge `End_ReembolsoNegado`**

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseReembolso` aberta (`sla.sla_alerta` da DMN `reembolso_sla`)
- **When** o job do timer `BT_AlertaSla` executa
- **Then** `operadora.reembolso.notify_sla_risk` recebeu task; a User Task segue aberta

### test_timer_sla_estourado_coordenacao_assume
- **Given** analise aberta alem de `sla.sla_analise`
- **When** o job do timer `BT_SlaAnalise` executa
- **Then** `reembolso.sla_breached` publicado; `UT_AnaliseReembolso` cancelada; `UT_CoordenacaoReembolso` criada (`coordenacao-reembolso`) — **a decisao continua humana** (o estouro de SLA nunca auto-nega; apenas troca o ator humano)

### test_dmn_reembolso_sla_urgencia
- **Given/When** start com `tipo_reembolso=urgencia_emergencia`
- **Then** `sla.sla_analise` mais curto que o padrao P30D (valor exato DRAFT/verify); `fonte_regulatoria` registrada na variavel

## DMN de calculo (valor, nao decisao)

### test_dmn_reembolso_calculo_retorna_valor_nao_decide
- **Given/When** `reembolso_calculo` avaliada para um procedimento com tabela
- **Then** retorna `valor_calculado_tabela_cents` (integer, centavos) e `multiplo_tabela_aplicado` (double) e `fonte_tabela`; **nenhuma saida de decisao** (sem APROVAR/NEGAR/REDUZIR). typeRef de dinheiro e `integer` (centavos), nunca `number`

### test_dmn_nenhuma_tem_saida_de_negativa
- **Given** as DMNs `reembolso_admissibility`, `reembolso_calculo`, `reembolso_auto_approval`, `reembolso_sla`
- **Then** nenhuma coluna de saida contem `NEGAR`/`REDUZIR`/equivalente; `reembolso_admissibility` e `reembolso_auto_approval` so emitem `{SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}` / `{AUTO_APROVAR, ANALISE_HUMANA}`; toda tabela tem catch-all conservador → caminho humano

## Idempotencia

### test_business_key_uma_instancia_por_protocolo
- **Given** instancia ativa `REEMB-amh-REEMB-TESTE-0001`
- **When** reenvio da mesma solicitacao (mesmo protocolo)
- **Then** sem segunda instancia ativa; `start_process` retorna a existente
