# Test spec — SP-OP-RECURSO-001 (DRAFT — acompanha o processo)

Stubs de integração (pytest, marker `integration`) contra CIB Seven **REAL** (CONTRIBUTING §3
— **sem mock de engine**; o teste guia a integração futura contra o engine real). Arquivo alvo:
`tests/integration/processes/test_sp_op_recurso_001.py`.

Dados sempre sintéticos: guia `GUIA-TESTE-0001`, glosa `GLOSA-TESTE-0001`, lote `LOTE-TESTE-0001`,
`Paciente Teste 001`, `beneficiario_pseudo_id="PSEUDO-TESTE-001"`, prestador `PREST-TESTE-001`,
tenant `amh`. CPFs/identificadores reais NUNCA — usar faixas de CPF inválidas (`000.000.000-00`).
Business key `RECURSO-amh-GUIA-TESTE-0001-GLOSA-TESTE-0001`.

> **Perspectiva (ADR-0040).** O dono do processo é a **OPERADORA**: ela recebe o recurso do
> prestador e emite a resposta. Os cenários de recorrente que este spec descrevia — interpor o
> recurso, seguir o andamento, reconciliar o crédito recebido — **desapareceram sem
> substituto**, junto com o `test_loop_acompanhamento_limitado`. É a única perda líquida de
> cobertura do pacote, e é correta: o comportamento testado não deve existir.

## Convenções de fixture

- `engine`: cliente REST CIB Seven do dev-stack (`make dev-stack`).
- `deploy_artifacts`: deploya o BPMN + `recurso_admissibility.dmn` + `recurso_eligibility.dmn` + `recurso_sla.dmn`.
- `iniciar_recurso(**overrides)`: inicia com a business key canônica e payload sintético (glosa
  `administrativa`, `valor_glosado_brl=150.00`, `glosa_existe=true`, `dentro_prazo_recurso=true`,
  `documentacao_recurso_completa=true`, `data_recebimento_recurso_iso` dinâmica no futuro,
  `data_vencimento`/`competencia`/`conta_origem_ref`/`instrumento_pagamento` herdados do intake).
- `recurso_probe`: além do `bpmn_error_allowlist`, wira os seams de start fenceado
  (`engine=`/`audit_sink=`) — `handoff_pagamento` roda `start_process_idempotent` contra
  SP-OP-PAGTO-001, a única família **STRICT** de dedup.
- `kafka_probe(topic)`: consumidor de teste; o worker genérico `operadora.events.publish` roda no dev-stack.
- `history(instance)`: consulta `history/activity-instance` do engine (base do teste de invariante).
- Timers: executar via API de gerenciamento de jobs do engine (executar o job do timer), **nunca `sleep`**.

## Invariante L0 (testes de segurança — PRIORITARIOS)

### test_nenhum_caminho_automatizado_indeferimento
- **Given** varredura de TODAS as combinações dos inputs das DMNs:
  `glosa_existe`, `dentro_prazo_recurso`, `documentacao_recurso_completa` ∈ {true,false};
  `glosa_type` ∈ {administrativa, tecnica, clinica, linha_duplicada, formatacao};
  `valor_glosado_brl` em {baixo, médio, alto}
- **When** cada instância percorre até estabilizar (executar todos os jobs assíncronos)
- **Then** para CADA combinação, a `history/activity-instance` do engine **NUNCA** contém
  `End_RecursoIndeferido`, `End_RecursoIndeferidoAuditor`, `End_RecursoDeferidoParcial` nem
  `End_RecursoInadmissivel` **sem** uma User Task humana (`UT_AnaliseRecursoAnalista` /
  `UT_RevisaoAuditorMedico` / `UT_CoordenacaoRecursoAssume` / `UT_EscalonamentoPrazo`) concluída na
  mesma history com `decisao_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}` (ou o equivalente do auditor).
  Nenhuma combinação de DMN indefere por automação. **(verifica os 5 componentes no-denial)**

### test_inadmissibilidade_aparente_roteia_para_humano
- **Given** start com `dentro_prazo_recurso=false` (inadmissibilidade procedural aparente — R5)
- **When** instância percorre
- **Then** DMN `recurso_admissibility` retorna `ANALISE_HUMANA` (NUNCA um desfecho de inadmissibilidade
  automático); o fluxo chega a `UT_AnaliseRecursoAnalista` — não a `End_RecursoInadmissivel`.
  Idem para `glosa_existe=false`: roteia a humano, não inadmite automaticamente.

### test_inelegibilidade_roteia_para_humano_nao_nega
- **Given** start com `glosa_type=tecnica` (mérito clínico/técnico)
- **When** instância percorre
- **Then** DMN `recurso_eligibility` roteia para `grupo_revisor=medico-auditor` (`UT_RevisaoAuditorMedico`)
  — NUNCA para um fim adverso automático. O mérito é decidido por humano (auditor).

### test_indeferir_exige_campos_obrigatorios
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** completar com `decisao_recurso=INDEFERIR` SEM `fundamentacao_indeferimento` /
  `valor_glosa_mantido_brl` / `referencia_contratual`
- **Then** o guard do worker **RECUSA** — manter a glosa sem fundamentação é impossível (espelha
  `NEGAR` de AUTH exigindo justificativa); a instância não atinge terminal adverso.

### test_deferir_parcial_exige_valor_deferido
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** completar com `decisao_recurso=DEFERIR_PARCIAL` sem `valor_deferido_brl`
- **Then** o guard **RECUSA** — sem o valor revertido não há ordem de pagamento a emitir.
  A soma `valor_deferido_brl + valor_glosa_mantido_brl == valor_glosado_brl` é conferida em
  **centavos-inteiros, igualdade exata** — **invariante PERMANENTE** do guard (decisão do dono
  **R-155** de 2026-09-04, que fechou **OQ-R2**). Tolerância/arredondamento só AFROUXA e por isso
  segue humano: regra nova assinada por finanças, em PR próprio. O **limite declarado** da
  conversão (três operandos arredondados independentemente ⇒ até 1,5 centavo de resíduo agregado
  em entradas sub-centavo) está no contrato, na linha `valor_deferido_brl`.

### test_worker_guard_registrar_indeferimento_recusa_sem_humano
- **Given** o worker `operadora.recurso.registrar_indeferimento` recebe uma external task sem que
  nenhum dos dois canais humanos case (analista: `decisao_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}` +
  `analista_id`; auditor: `decisao_auditor_recurso ∈ {…}` + `auditor_id`)
- **When** o worker processa
- **Then** lança `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN` (`PermissionError` → **incidente auditado**);
  NÃO registra o indeferimento. Com os campos obrigatórios presentes, registra e carrega o id do
  decisor humano na trilha de auditoria (ADR-0007), cunhando um protocolo **determinístico** pela
  business key.

### test_decisao_invalida_termina_em_erro_sem_efeito
- **Given** `UT_AnaliseRecursoAnalista` (ou `UT_RevisaoAuditorMedico`) aberta
- **When** o humano conclui a task SEM `decisao_recurso` (ou com valor fora do domínio)
- **Then** o **default fail-closed** do gateway leva a `End_ErrRecursoDecisaoInvalida`
  (`ERR_RECURSO_DECISAO_INVALIDA`) — nenhum efeito é materializado: nem indeferimento, nem
  comunicação, nem ordem de pagamento. Antes, o default de `GW_DecisaoRecurso` era uma **ação**.

## Happy paths

### test_happy_path_deferir_pelo_analista
- **Given** dossiê preparado (worker `operadora.recurso.analyze_request` / Marina completa)
- **When** `analista-recurso-glosa` completa `UT_AnaliseRecursoAnalista` com `decisao_recurso=DEFERIR`
  + `valor_deferido_brl` + `analista_id`
- **Then** `operadora.recurso.comunicar_resposta` emite a resposta ao prestador;
  `operadora.recurso.handoff_pagamento` **encaminha o pagamento da glosa revertida** a
  SP-OP-PAGTO-001 (`tipo_pagamento=glosa_revertida`, business key
  `PAGTO-{tenant}-{guia}-{glosa}`); `recurso.completed` com `desfecho=deferido_humano`; fim em
  `End_RecursoDeferido`.

### test_happy_path_indeferir_pelo_analista
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** analista completa `decisao_recurso=INDEFERIR` com `fundamentacao_indeferimento` +
  `valor_glosa_mantido_brl` + `referencia_contratual` + `analista_id`
- **Then** `operadora.recurso.registrar_indeferimento` executa (guard satisfeito);
  `comunicar_resposta` emite a resposta; `recurso.completed` com `desfecho=indeferido_humano`; fim
  em `End_RecursoIndeferido`; `analista_id` na trilha.
  **Único caminho que mantém a glosa — e é humano.**

### test_happy_path_escalar_auditor_defere
- **Given** `decisao_recurso=ESCALAR_AUDITOR` (glosa técnica/clínica)
- **When** `UT_RevisaoAuditorMedico` (`medico-auditor`) completa com `decisao_auditor_recurso=DEFERIR`
  + `valor_deferido_brl` + `auditor_id`
- **Then** converge no MESMO `ST_ComunicarDeferimento` do canal do analista + handoff de pagamento;
  `auditor_id` registrado.

### test_happy_path_auditor_indefere_humano
- **Given** `decisao_recurso=ESCALAR_AUDITOR`
- **When** o auditor completa `decisao_auditor_recurso=INDEFERIR` com os campos obrigatórios
- **Then** `registrar_indeferimento` (canal auditor) + `comunicar_resposta`; `recurso.completed`
  com `desfecho=indeferido_humano` carregando `auditor_id`; fim em `End_RecursoIndeferidoAuditor`.

### test_recurso_parcialmente_deferido
- **Given** `UT_AnaliseRecursoAnalista` aberta
- **When** o humano decide `DEFERIR_PARCIAL` (parte da glosa é MANTIDA — adverso L0) com a soma
  fechando em centavos-inteiros
- **Then** `registrar_indeferimento` (guard) + `comunicar_resposta` + `handoff_pagamento` da
  **parcela revertida**; `recurso.completed` com `desfecho=deferido_parcial_humano`; fim em
  `End_RecursoDeferidoParcial`.

### test_glosa_revertida_nunca_alcanca_liberacao_automatica_sem_ut
- **Given** um deferimento humano que gerou uma ordem em SP-OP-PAGTO-001
- **When** a instância de PAGTO percorre
- **Then** ela **NÃO** alcança `End_PagamentoLiberadoAutomatico` sem `UT_AnaliseAdmissibilidade`
  concluída. É a prova da **segregação de funções** (invariante **I-PAGTO-1**): quem julga o
  recurso não confirma o lastro da ordem que ele próprio gerou.

### test_todos_os_fins_comunicam_o_prestador
- **Given** os 5 terminais de negócio
- **Then** cada um é precedido por `operadora.recurso.comunicar_resposta` ANTES do `ST_Publish*` —
  a operadora responde ao prestador em TODO desfecho que o afeta, favorável ou adverso.

## Pendência de documentação

### test_pendencia_docs_recebidos_reavalia
- **Given** `documentacao_recurso_completa=false` → DMN `recurso_admissibility` retorna
  `PENDENTE_DOCUMENTACAO`; `recurso.pended` publicado; aguardando documentação
- **When** message `msg.recurso.docs_received` correlacionada (business key) com `documentacao_recurso_completa=true`
- **Then** `BRT_Admissibilidade` reavaliada; fluxo segue para análise (`SEGUE_ANALISE`).

### test_pendencia_expira_decisao_humana
- **Given** aguardando docs
- **When** job do timer `ICE_PrazoPendencia` (ref P5D) executado
- **Then** roteia para `UT_AnaliseRecursoAnalista` (humano decide destino) — **nunca** auto-indefere
  por pendência expirada.

## Timers de SLA (auto-approve-on-timeout INVERTIDO)

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseRecursoAnalista` aberta (DMN `sla_alerta`)
- **When** job do timer `BT_AlertaSlaRecurso` executado
- **Then** `operadora.recurso.notify_sla_risk` recebeu task; a User Task **segue aberta** (não-interruptivo).

### test_timer_sla_estourado_coordenacao_assume_nao_auto_aprova
- **Given** análise aberta além de `sla.sla_analise`
- **When** job do timer `BT_SlaAnaliseRecurso` executado
- **Then** `recurso.sla_breached` (fase=`analise`) publicado; `UT_AnaliseRecursoAnalista` **CANCELADA**;
  `UT_CoordenacaoRecursoAssume` criada (`coordenacao-recurso`). **NÃO** há auto-deferimento nem
  auto-indeferimento por timeout (o `Task_AutoApprove` 48h do reference foi removido/invertido) — a
  decisão continua humana.

### test_solicitar_info_repetido_nao_sobrevive_ao_teto_absoluto
- **Given** o único ciclo remanescente (`SOLICITAR_INFO → pendência → docs recebidos → UT`)
- **When** o ciclo é percorrido três vezes e o relógio passa de `sla.prazo_max_absoluto_iso`
- **Then** o token está em `UT_EscalonamentoPrazo`, **nunca** num terminal adverso e **nunca** num
  quarto ciclo. Não há contador — **por desenho**: um teto por contagem produziria um auto-desfecho
  por esgotamento (anti-padrão proibido por ADR-0018). O que limita é o **teto absoluto**, que
  reentrar no ciclo NÃO adia (o `dueDate` é idêntico a cada volta).

### test_prazo_max_recurso_escala_humano
- **Given** instância ativa além do teto absoluto (ref P30D, prazo contratual — DRAFT/verify)
- **When** job do timer `BT_PrazoMaxRecurso` executado
- **Then** `operadora.recurso.escalate_ans_timeout` recebeu task; `UT_EscalonamentoPrazo` criada
  (`coordenacao-recurso`) — escalonamento humano, **nunca** auto-desfecho adverso por prazo.

### test_dmn_recurso_sla_valores
- **Given/When** start com `glosa_type=clinica`, `valor_glosado_brl` alto
- **Then** `sla.sla_analise` é string ISO 8601 válida; `sla.prazo_regulatorio` presente;
  `fonte_regulatoria` nomeia o **prazo contratual de resposta ao recurso** (+ RN 501/2022), com
  RN 424/2017 aparecendo apenas na row técnico-clínica e condicionada à junta médica (todos
  DRAFT/verify).

### test_prazo_max_ancora_defaultada_pelo_intake_com_warning
- **Given** start SEM `data_recebimento_recurso_iso`
- **When** `ST_ValidarRecurso` (intake) roda
- **Then** a âncora é normalizada e **defaultada fail-safe para HOJE/UTC com warning**, escrita de
  volta como variável de processo, e o teto ancora nela. **Nunca** na data de ciência da glosa pelo
  prestador — essa é a âncora do recorrente, e ela saiu de `recurso_sla` inteira.

## Roteamento DMN (catch-all fail-safe)

### test_dmn_admissibility_catchall_fail_safe
- **Given/When** start com combinação de inputs não-mapeada explicitamente
- **Then** `recurso_admissibility.roteamento == ANALISE_HUMANA` (catch-all conservador) — nunca um
  desfecho de inadmissibilidade automático.

### test_dmn_eligibility_glosa_tecnica_vai_ao_auditor
- **Given/When** start com `glosa_type=tecnica` ou `clinica`
- **Then** `recurso_eligibility.grupo_revisor == medico-auditor` e `roteamento == SEGUE_MERITO`
  (o domínio nomeia o roteamento ao MÉRITO, não a "recorribilidade"); a User Task de mérito é do
  auditor.

## Idempotência

### test_business_key_uma_instancia_por_glosa
- **Given** instância ativa `RECURSO-amh-GUIA-TESTE-0001-GLOSA-TESTE-0001`
- **When** re-intake da mesma glosa (o prestador retransmite, a operação abre manualmente, ou
  Marina redelega — via `mcp-cibseven.start_process`)
- **Then** sem segunda instância ativa; resposta referencia a instância existente.

### test_glosa_id_ausente_termina_limpo_sem_incidente_travado
- **Given** start com `glosa_id` ausente/vazio (defeito **TÉCNICO** de origem — distinto do fato de
  negócio `glosa_existe`, que roteia a `ANALISE_HUMANA` pela DMN, nunca erro)
- **When** `ST_ValidarRecurso` (o **primeiro** dos três sites) valida a origem
- **Then** lança `ERR_RECURSO_INVALID_GLOSA`; o boundary `BE_GlosaInvalidaValidacao` termina a
  instância **LIMPO** em `End_RecursoGlosaInvalidaOrigem` (terminal NEUTRO), sem sequer avaliar
  `recurso_admissibility` e sem nenhum efeito adverso.

## Auditoria

### test_todos_os_fins_emitem_evento_de_dominio
- **Given** os caminhos de fim (`deferido_humano`, `deferido_parcial_humano`, `indeferido_humano`,
  `inadmissivel_humano`)
- **When** cada um é percorrido
- **Then** há evento Kafka `recurso.completed` correspondente publicado ANTES do end event (sem fim
  silencioso; auditoria dupla engine + Kafka, ADR-0007).
