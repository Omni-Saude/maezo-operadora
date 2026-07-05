# Test spec — SP-OP-CONTAS-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" de Phase 0/1 — estes stubs guiam o teste de integracao real contra o
motor; **sem engine mock**). Arquivo alvo: `tests/integration/processes/test_sp_op_contas_001.py`.
Dados sinteticos: lote `LOTE-TESTE-0001`, prestador `Prestador Teste 001`, beneficiario
pseudonimizado `bnf-teste-0001`, tenant `amh`, CPF de teste em faixa invalida
(`000.000.000-00`). Business key `CONTAS-amh-LOTE-TESTE-0001`.

## Invariante L0 (testes de seguranca — prioritarios)

### test_nenhum_caminho_automatizado_aceita_glosa
- **Given** varredura de TODAS as combinacoes de input da DMN `glosa_triage`:
  `tipo_item` ∈ {amostra sintetica}, `categoria_normalizada` ∈ {tecnica, administrativa, clinica, valor, documental, desconhecida},
  `item_conforme_tabela`/`divergencia_valor`/`documentacao_anexa` ∈ {true,false}
- **When** cada instancia percorre ate estabilizar (consultando `history/activity-instance` do engine)
- **Then** NUNCA atinge `End_GlosaAceitaHumano` sem uma User Task humana (`UT_AnalistaContas` ou
  `UT_CoordenacaoContasAssume` → `UT_AnalistaContas`) **concluida por humano** com
  `decisao_contas=ACEITAR_GLOSA` no historico. O end-event adverso NUNCA aparece no history do
  engine sem uma User Task humana no mesmo history. (invariante §4-bis-F, parte 5)

### test_inelegibilidade_roteia_para_humano_nao_aceita
- **Given** start com item nao conforme a tabela e sem documentacao
  (`item_conforme_tabela=false`, `documentacao_anexa=false`)
- **When** instancia percorre
- **Then** DMN `glosa_triage` retorna `roteamento=ANALISE_HUMANA` (catch-all conservador); fluxo
  chega a `UT_AnalistaContas` (`auditoria-contas`) — **nao** a `End_GlosaAceitaHumano` nem a
  qualquer fim adverso automatico. Inelegibilidade aparente roteia, nunca auto-aceita.

### test_aceitar_glosa_exige_campos
- **Given** `UT_AnalistaContas` aberta
- **When** completar com `decisao_contas=ACEITAR_GLOSA` **sem**
  `justificativa_glosa`/`codigo_glosa_aceito`/`valor_glosa_aceito_brl`
- **Then** a task NAO completa (validacao de formulario/listener) — aceite de glosa sem
  justificativa+codigo+valor e impossivel. Espelha `test_negar_exige_campos_obrigatorios` de AUTH.

### test_glosa_tecnica_roteia_para_humano_nao_aceita
- **Given** start com `categoria_normalizada=tecnica` (glosa tecnica/formatacao)
- **When** instancia percorre `glosa_triage`
- **Then** `roteamento=ANALISE_HUMANA` (default conservador R4); fluxo chega a `UT_AnalistaContas`.
  (auto-route de glosa puramente formatacional so existiria com sign-off de compliance — **nao**
  esta no fluxo DRAFT)

### test_worker_register_glosa_accept_recusa_sem_humano
- **Given** invocacao direta do worker `operadora.contas.register_glosa_accept` com
  `decisao_contas` ausente ou `!= ACEITAR_GLOSA` (ou faltando `analista_id`)
- **When** o worker executa
- **Then** lanca `ERR_GLOSA_ACCEPT_NOT_HUMAN`; NAO registra aceite; instancia nao avanca a
  `End_GlosaAceitaHumano`. Com `decisao_contas=ACEITAR_GLOSA` + campos + `analista_id` setados por
  humano → registra e carrega `analista_id` na cadeia de auditoria (ADR-0007).

### test_indicio_fraude_nunca_auto_flag
- **Given** start com `indicio_fraude_sinalizado=true` (sinal informativo de worker)
- **When** instancia percorre
- **Then** nenhum branch auto-acusa fraude; o fluxo cria `UT_AnalistaContas` (`auditoria-contas`)
  que decide `encaminhar_fraude` (handoff a SP-OP-FRAUDE-001, Phase 3). `fraud_accusation` L0 hard
  nunca nasce de automacao.

## Happy paths

### test_happy_path_sem_glosa
- **Given** demonstrativo sem linhas glosadas (`hasGlosas`-equivalente falso após
  `operadora.contas.identify_glosa`)
- **When** instancia percorre
- **Then** `glosa_triage`/fluxo chega a `End_SemGlosa`; `contas.completed` com `desfecho=sem_glosa`;
  NENHUMA User Task criada.

### test_happy_path_recorrer_handoff_recurso
- **Given** glosa candidata; `UT_AnalistaContas` aberta com dossie de Marina preparado
  (`operadora.contas.prepare_triage_dossier` completa)
- **When** analista completa com `decisao_contas=RECORRER`
- **Then** `operadora.contas.start_recurso` executado (handoff: inicia SP-OP-RECURSO-001 com
  `glosa_id`+`numero_guia_tiss`); `contas.completed` com `desfecho=encaminhada_recurso`; fim
  `End_EncaminhadaRecurso`.

### test_happy_path_aceitar_glosa_pelo_analista
- **Given** `UT_AnalistaContas` aberta
- **When** analista completa `ACEITAR_GLOSA` com `justificativa_glosa`+`codigo_glosa_aceito`+
  `valor_glosa_aceito_brl`+`analista_id`
- **Then** `operadora.contas.register_glosa_accept` executado (guard satisfeito); `contas.completed`
  com `desfecho=glosa_aceita_humano`; fim `End_GlosaAceitaHumano`; `analista_id` na trilha de auditoria.

### test_happy_path_reenviar
- **Given** `UT_AnalistaContas` aberta
- **When** analista completa com `decisao_contas=REENVIAR`
- **Then** `operadora.contas.reconcile_payment` executado (registra reenvio, sem efeito adverso);
  `contas.completed` com `desfecho=reenviada`; fim `End_Reenviada`.

## Reavaliacao por correcao de linhas (mensagem)

### test_linhas_atualizadas_reavalia
- **Given** instancia aguardando correcao (linhas inconsistentes → `documentacao_anexa=false`)
- **When** message `msg.contas.linhas_atualizadas` correlacionada (business key) com
  `documentacao_anexa=true`
- **Then** `BRT_TriagemGlosa` reavaliada; fluxo segue para triagem/analise atualizada.

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnalistaContas` aberta (alerta = 60–70% de `sla.sla_alerta`)
- **When** job do timer `BT_AlertaSlaContas` executado
- **Then** `operadora.contas.notify_sla_risk` recebeu task (alerta `coordenacao-contas`); a User
  Task segue aberta.

### test_timer_sla_estourado_coordenacao_assume
- **Given** triagem/analise aberta alem de `sla.sla_analise` (tipico P30D — DRAFT/verify)
- **When** job do timer `BT_SlaTriagem` (interruptivo) executado
- **Then** `contas.sla_breached` publicado; `UT_AnalistaContas` cancelada;
  `UT_CoordenacaoContasAssume` criada (`coordenacao-contas`). **Nao** ha auto-aceite por timeout
  (inversao do `Task_AutoApprove`/48h do reference) — a decisao continua humana.

### test_dmn_contas_sla_internacao
- **Given/When** start com `tipo_lote=internacao`
- **Then** `sla.sla_analise` retornado como string ISO (ex.: `"P30D"`), com a fonte registrada em
  `sla.fonte_regulatoria` (valor DRAFT/verify).

## DMN — shape e fail-safe

### test_glosa_triage_sem_saida_de_aceite
- **Given** a definicao da DMN `glosa_triage`
- **Then** o dominio de `roteamento` e exatamente `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}` — **nenhum
  valor de aceite/confirmacao de glosa**; e existe row catch-all → `ANALISE_HUMANA`.

### test_dmn_typeref_allowlist
- **Given** todas as DMNs do processo (`glosa_reason_normalization`, `glosa_classification`,
  `glosa_triage`, `contas_sla`)
- **Then** todo `typeRef` ∈ {string, boolean, integer, long, double, date}; nenhuma coluna usa
  `"number"`; valores BRL sao `double`; prazos sao string ISO. (gate `validate-artifacts`)

## Idempotencia

### test_business_key_uma_instancia_por_lote
- **Given** instancia ativa `CONTAS-amh-LOTE-TESTE-0001`
- **When** reenvio do mesmo lote TISS
- **Then** sem segunda instancia ativa (start idempotente; retorna a existente).
