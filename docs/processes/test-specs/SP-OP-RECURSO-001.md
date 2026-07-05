# Test spec — SP-OP-RECURSO-001 (DRAFT — acompanha o processo)

Stubs de integração (pytest, marker `integration`) contra CIB Seven **REAL** (CONTRIBUTING §3
— **sem mock de engine**; o teste guia a integração futura contra o engine real). Arquivo alvo:
`tests/integration/processes/test_sp_op_recurso_001.py`.

Dados sempre sintéticos: guia `GUIA-TESTE-0001`, glosa `GLOSA-TESTE-0001`, lote `LOTE-TESTE-0001`,
`Paciente Teste 001`, `beneficiario_pseudo_id="PSEUDO-TESTE-001"`, prestador `PREST-TESTE-001`,
tenant `amh`. CPFs/identificadores reais NUNCA — usar faixas de CPF inválidas (`000.000.000-00`).
Business key `RECURSO-amh-GUIA-TESTE-0001-GLOSA-TESTE-0001`.

## Convenções de fixture

- `engine`: cliente REST CIB Seven do dev-stack (`make dev-stack`).
- `deploy_artifacts`: deploya o BPMN + `recurso_admissibility.dmn` + `recurso_eligibility.dmn` + `recurso_sla.dmn`.
- `start_recurso(**overrides)`: inicia com a business key canônica e payload sintético (glosa
  `administrativa`, `valor_glosado_brl=150.00`, `glosa_existe=true`, `dentro_prazo_recurso=true`,
  `documentacao_recurso_completa=true`).
- `kafka_probe(topic)`: consumidor de teste; o worker genérico `operadora.events.publish` roda no dev-stack.
- `history(instance)`: consulta `history/activity-instance` do engine (base do teste de invariante).
- Timers: executar via API de gerenciamento de jobs do engine (executar o job do timer), **nunca `sleep`**.

## Invariante L0 (testes de segurança — PRIORITARIOS)

### test_nenhum_caminho_automatizado_produz_desistencia
- **Given** varredura de TODAS as combinações dos inputs das DMNs:
  `glosa_existe`, `dentro_prazo_recurso`, `documentacao_recurso_completa` ∈ {true,false};
  `glosa_type` ∈ {administrativa, tecnica, clinica, linha_duplicada, formatacao};
  `valor_glosado_brl` em {baixo, médio, alto}
- **When** cada instância percorre até estabilizar (executar todos os jobs assíncronos)
- **Then** para CADA combinação, a `history/activity-instance` do engine **NUNCA** contém
  `End_RecursoNaoInterposto` nem `End_GlosaMantida` **sem** uma User Task humana
  (`UT_AnaliseRecursoAnalista` / `UT_RevisaoAuditorMedico` / `UT_CoordenacaoRecursoAssume`)
  concluída na mesma history com `decisao_recurso=NAO_RECORRER` (ou `decisao_auditor_recurso=ACEITAR_GLOSA`).
  Nenhuma combinação de DMN produz desistência por automação. **(verifica os 5 componentes no-denial)**

### test_inadmissibilidade_aparente_roteia_para_humano
- **Given** start com `dentro_prazo_recurso=false` (inadmissibilidade procedural aparente — R5)
- **When** instância percorre
- **Then** DMN `recurso_admissibility` retorna `ANALISE_HUMANA` (NUNCA um desfecho de inadmissibilidade
  automático); o fluxo chega a `UT_AnaliseRecursoAnalista` — não a `End_RecursoNaoInterposto`.
  Idem para `glosa_existe=false`: roteia a humano, não auto-desiste.

### test_inelegibilidade_roteia_para_humano_nao_nega
- **Given** start com `glosa_type=tecnica` (mérito clínico/técnico)
- **When** instância percorre
- **Then** DMN `recurso_eligibility` roteia para `grupo_revisor=medico-auditor` (`UT_RevisaoAuditorMedico`)
  — NUNCA para um fim adverso automático. O mérito é decidido por humano (auditor).

### test_nao_recorrer_exige_campos_obrigatorios
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** completar com `decisao_recurso=NAO_RECORRER` SEM `justificativa_desistencia` /
  `valor_glosa_aceito` / `referencia_contratual`
- **Then** a task **NÃO completa** (validação de formulário/listener) — manter glosa sem
  fundamentação é impossível (espelha `NEGAR` de AUTH exigindo justificativa).

### test_worker_guard_register_desistencia_recusa_sem_humano
- **Given** o worker `operadora.recurso.register_desistencia` recebe uma external task SEM
  `decisao_recurso=NAO_RECORRER` setado por humano (ou sem `analista_id`)
- **When** o worker processa
- **Then** lança `ERR_DESISTENCIA_NOT_HUMAN`; NÃO registra a desistência; a glosa não é mantida
  por automação. Com `analista_id` + campos obrigatórios presentes, registra e carrega `analista_id`
  na trilha de auditoria (ADR-0007).

## Happy paths

### test_happy_path_recorrer_e_deferido
- **Given** dossiê preparado (worker `operadora.recurso.analyze_request` / Marina completa)
- **When** `analista-recurso-glosa` completa `UT_AnaliseRecursoAnalista` com `decisao_recurso=RECORRER`;
  `operadora.recurso.submit_appeal` executa; `msg.recurso.resposta_recebida` correlaciona com deferimento
- **Then** `operadora.recurso.reconcile_payment` executado; `recurso.completed` com `desfecho=deferido`;
  re-pagamento conciliado ao prestador.

### test_happy_path_recurso_indeferido_pela_operadora
- **Given** recurso interposto (`RECORRER`), aguardando em `ICE_AguardarResposta`
- **When** `msg.recurso.resposta_recebida` correlaciona com indeferimento pela operadora
- **Then** `recurso.completed` com `desfecho=indeferido`. (Indeferimento pela operadora externa NÃO é
  uma desistência da Maezo — é resposta de terceiro; não requer worker-guard, mas é registrado.)

### test_happy_path_nao_recorrer_humano
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** analista completa `decisao_recurso=NAO_RECORRER` com `justificativa_desistencia` +
  `valor_glosa_aceito` + `referencia_contratual` (campos obrigatórios presentes)
- **Then** `operadora.recurso.register_desistencia` executa (guard satisfeito); `recurso.completed`
  com `desfecho=nao_interposto_humano`; fim em `End_RecursoNaoInterposto` (ou `End_GlosaMantida`);
  `analista_id` na trilha. **Único caminho que mantém a glosa — e é humano.**

### test_happy_path_escalar_auditor_mantem_recurso
- **Given** `decisao_recurso=ESCALAR_AUDITOR` (glosa técnica/clínica)
- **When** `UT_RevisaoAuditorMedico` (`medico-auditor`) completa com `decisao_auditor_recurso=MANTER_RECURSO`
- **Then** `operadora.recurso.submit_appeal` executa; segue o fluxo de recurso normal; `auditor_id` registrado.

### test_recurso_parcialmente_deferido
- **Given** recurso interposto
- **When** `msg.recurso.resposta_recebida` correlaciona com deferimento parcial
- **Then** `recurso.completed` com `desfecho=parcialmente_deferido`; conciliação parcial.

## Pendência de documentação

### test_pendencia_docs_recebidos_reavalia
- **Given** `documentacao_recurso_completa=false` → DMN `recurso_admissibility` retorna
  `PENDENTE_DOCUMENTACAO`; `recurso.pended` publicado; aguardando documentação
- **When** message `msg.recurso.docs_received` correlacionada (business key) com `documentacao_recurso_completa=true`
- **Then** `BRT_Admissibilidade` reavaliada; fluxo segue para análise (`SEGUE_ANALISE`).

### test_pendencia_expira_decisao_humana
- **Given** aguardando docs
- **When** job do timer `ICE_PrazoPendencia` (ref P5D) executado
- **Then** roteia para `UT_AnaliseRecursoAnalista` (humano decide destino) — **nunca** auto-desiste por
  pendência expirada.

## Timers de SLA (auto-approve-on-timeout INVERTIDO)

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseRecursoAnalista` aberta (DMN `sla_alerta`)
- **When** job do timer `BT_AlertaSlaRecurso` executado
- **Then** `operadora.recurso.notify_sla_risk` recebeu task; a User Task **segue aberta** (não-interruptivo).

### test_timer_sla_estourado_coordenacao_assume_nao_auto_aprova
- **Given** análise aberta além de `sla.sla_analise`
- **When** job do timer `BT_SlaAnaliseRecurso` executado
- **Then** `recurso.sla_breached` (fase=`analise`) publicado; `UT_AnaliseRecursoAnalista` **CANCELADA**;
  `UT_CoordenacaoRecursoAssume` criada (`coordenacao-recurso`). **NÃO** há auto-aprovação nem
  auto-desistência por timeout (o `Task_AutoApprove` 48h do reference foi removido/invertido) — a
  decisão continua humana.

### test_loop_acompanhamento_limitado
- **Given** recurso interposto, aguardando em `ICE_AguardarResposta` (ref P5D)
- **When** o timer dispara e `GW_RecursoResolvido` ainda não resolveu, repetidamente
- **Then** `operadora.recurso.track_status` é reexecutado; `loopCounter` é limitado (ref `< 6`); ao
  exceder, roteia a humano (`UT_CoordenacaoRecursoAssume`) — nunca loop infinito nem auto-desfecho.

### test_prazo_max_recurso_escala_humano
- **Given** instância ativa além de `sla.prazo_regulatorio` (ref P30D, RN 424 — DRAFT/verify)
- **When** job do timer `BT_PrazoMaxRecurso` executado
- **Then** `operadora.recurso.escalate_ans_timeout` recebeu task; `UT_EscalonamentoPrazo` criada
  (`coordenacao-recurso`) — escalonamento humano, **nunca** auto-desfecho adverso por prazo.

### test_dmn_recurso_sla_valores
- **Given/When** start com `glosa_type=clinica`, `valor_glosado_brl` alto
- **Then** `sla.sla_analise` é string ISO 8601 válida; `sla.prazo_regulatorio` presente; `fonte_regulatoria`
  registrada na variável (todos DRAFT/verify).

## Roteamento DMN (catch-all fail-safe)

### test_dmn_admissibility_catchall_fail_safe
- **Given/When** start com combinação de inputs não-mapeada explicitamente
- **Then** `recurso_admissibility.roteamento == ANALISE_HUMANA` (catch-all conservador) — nunca um
  desfecho de inadmissibilidade automático.

### test_dmn_eligibility_glosa_tecnica_vai_ao_auditor
- **Given/When** start com `glosa_type=tecnica` ou `clinica`
- **Then** `recurso_eligibility.grupo_revisor == medico-auditor`; a User Task de mérito é do auditor.

## Idempotência

### test_business_key_uma_instancia_por_glosa
- **Given** instância ativa `RECURSO-amh-GUIA-TESTE-0001-GLOSA-TESTE-0001`
- **When** reenvio da mesma glosa (re-handoff de CONTAS ou redelegação a Marina, via `mcp-cibseven.start_process`)
- **Then** sem segunda instância ativa; resposta referencia a instância existente.

### test_glosa_inexistente_lanca_erro
- **Given** start com `glosa_id` que não referencia glosa confirmada em CONTAS (`glosa_existe=false`
  no worker de pré-resolução)
- **When** o worker valida a origem
- **Then** lança `ERR_RECURSO_INVALID_GLOSA` (`Error_RecursoGlosaInvalida`) — não inicia recurso órfão.

## Auditoria

### test_todos_os_fins_emitem_evento_de_dominio
- **Given** os caminhos de fim (`deferido`, `indeferido`, `parcialmente_deferido`, `inadmissivel`,
  `nao_interposto_humano`)
- **When** cada um é percorrido
- **Then** há evento Kafka `recurso.completed` correspondente publicado ANTES do end event (sem fim
  silencioso; auditoria dupla engine + Kafka, ADR-0007).
