# Test spec — SP-OP-AUTH-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra CIB Seven real. Arquivo alvo:
`tests/integration/processes/test_sp_op_auth_001.py`. Dados sinteticos: guia
`GUIA-TESTE-0001`, `Paciente Teste 001`, tenant `amh`. Business key `AUTH-amh-GUIA-TESTE-0001`.

## Invariante L0 (testes de seguranca — prioritarios)

### test_nenhum_caminho_automatizado_produz_negativa
- **Given** todas as combinacoes de DMN: `requer_autorizacao/documentacao_completa/beneficiario_ativo/carencia_cumprida` (admissibilidade) e, para `auth_auto_approval` v0.2.0 (PERSP-B5-TESTSPEC-AUTH/GAP-AUTH-4 — `auto_criteria_verificado/criterio_tecnico_ok/criterio_financeiro_ok/criterio_regulatorio_ok/criterio_contratual_ok` substituem `dut_atendida/dentro_teto_l2/rede_credenciada`, que a tabela **NAO le mais**, ver contrato `SP-OP-AUTH-001.md:47-49`) em {true,false}
- **When** instancia percorre ate estabilizar
- **Then** NUNCA atinge `End_NegadaAuditor` sem `UT_AnaliseMedicoAuditor`/`UT_CoordenacaoAssume`/`UT_RegistrarParecerJunta` completada por humano com `decisao_auditor=NEGAR`

### test_negar_exige_campos_obrigatorios
- **Given** `UT_AnaliseMedicoAuditor` aberta
- **When** complete com `decisao_auditor=NEGAR` sem `justificativa_clinica`/`cid10_referencia`/`fundamentacao_dut`
- **Then** a negativa NAO e transmitida, e o caso fica retido e visivel a um humano

> **Mecanismo real, corrigido em 24/08/2026 apos medicao no ambiente da AWS.**
> Esta linha dizia "task NAO completa (validacao de formulario/listener)". Nao e o que
> acontece, e a diferenca importa para quem escreve o teste.
>
> A User Task **e** concluida. O bloqueio acontece DEPOIS, em `ST_EnviarNegativaFormal`:
> `send_denial_notice` levanta `ERR_AUTH_DENIAL_INCOMPLETE` com "fundamentacao incompleta,
> campos ausentes: [...] — negativa formal NAO transmitida (RN 395 art. 10, L0 hard
> ADR-0005)". Medido: external task com `retries=0`, incident aberto, instancia ACTIVE
> parada em `ST_EnviarNegativaFormal`, nenhum end event alcancado.
>
> A garantia que esta linha protege continua valendo por inteiro — nada e transmitido sem
> fundamentacao. O que muda e ONDE conferir: **o incident e o historico da instancia**, nao
> o estado da tarefa. Um teste que asserte "a task nao completou" falha contra um sistema
> que esta funcionando exatamente como projetado.
>
> **O incident e deliberado, nao um defeito a consertar.** `ERR_AUTH_DENIAL_INCOMPLETE` e
> reconhecido como consumption-covered e mesmo assim filtrado do allowlist de producao pelo
> portao T-E (ADR-0030 §4): ativa-lo antes do T-E trocaria um incident garantidamente
> visivel a um humano por um fim limpo e silencioso num terminal neutro. Ha teste unitario
> (`tests/unit/runtime/test_worker_runtime_bpmn_error_allowlist.py`) que falha se alguem
> remover o filtro. O caminho de volta a mesa do auditor (`BE_NegativaIncompleta` ->
> `End_FundamentacaoIncompletaBloqueada`) so passa a ser alcancado quando o T-E aterrissar;
> ate la, o incident E o desfecho esperado deste caso de aceite.

### test_inelegibilidade_roteia_para_humano_nao_nega
- **Given** start com `beneficiario_ativo=false`
- **Then** DMN `auth_admissibility` retorna `SEGUE_ANALISE`; fluxo chega a `UT_AnaliseMedicoAuditor` (nao a um fim de negativa)

## Happy paths

### test_happy_path_aprovacao_automatica_l2
- **Given** `auto_criteria_verificado=true, criterio_tecnico_ok=true, criterio_financeiro_ok=true, criterio_regulatorio_ok=true, criterio_contratual_ok=true` (v0.2.0 — GAP-AUTH-4; substitui `dut_atendida/dentro_teto_l2/rede_credenciada`, que `auth_auto_approval` nao le mais, ver `auth_auto_approval.dmn:60-77`), eletivo
- **When** instancia percorre
- **Then** `operadora.auth.issue_authorization` executado; `auth.completed` com `desfecho=aprovada_automatica`; fim `End_AprovadaAutomatica`; NENHUMA User Task criada

### test_happy_path_aprovada_pelo_auditor
- **Given** qualquer um dos quatro criterios de `auth_auto_approval` v0.2.0 falso (ex.: `criterio_tecnico_ok=false`) — vai a analise (PERSP-B5-TESTSPEC-AUTH/GAP-AUTH-4; `dut_atendida=false` **NAO** e mais o que causa isso, ver `SP-OP-AUTH-001.md:47`); dossie preparado (worker `operadora.auth.analyze_request` completa)
- **When** medico auditor completa com `decisao_auditor=APROVAR`
- **Then** autorizacao emitida; `auth.completed` `desfecho=aprovada_auditor`

### test_happy_path_negada_pelo_auditor
- **Given** analise humana aberta
- **When** auditor completa NEGAR com campos obrigatorios
- **Then** `operadora.auth.send_denial_notice` executado; `auth.completed` `desfecho=negada_auditor`; fim `End_NegadaAuditor`

### test_nao_requer_autorizacao
- **Given** `requer_autorizacao=false`
- **Then** `auth.completed` `desfecho=nao_requer_autorizacao`; fim imediato sem User Task

## Pendencia de documentacao

### test_pendencia_docs_recebidos_reavalia
- **Given** `documentacao_completa=false` -> `auth.pended` publicado, aguardando em `GW_AguardarDocs`
- **When** message `msg.auth.docs_received` correlacionada (business key) com `documentacao_completa=true`
- **Then** `BRT_Admissibilidade` reavaliada; fluxo segue para analise

### test_pendencia_expira_decisao_humana
- **Given** aguardando docs
- **When** job do timer `ICE_PrazoPendencia` (P5D) executado
- **Then** `UT_DecidirPendenciaExpirada` criada para `medico-auditor`; com `decisao_pendencia=cancelar_guia` -> `auth.completed` `desfecho=cancelada_pendencia`; com `conceder_prazo_extra`/`seguir_analise` -> volta ao fluxo de analise

### test_form_pendencia_expirada_enum_fechado (WP-J1-05)
- **Given** `UT_DecidirPendenciaExpirada` criada na definicao implantada por esta emenda
- **When** a definicao e lida do engine (`ACT_RE_PROCDEF` -> bytes do recurso BPMN)
- **Then** a tarefa declara `camunda:formData` com EXATAMENTE um `formField` `decisao_pendencia`
  (`type=enum`, `required`, valores `cancelar_guia|conceder_prazo_extra|seguir_analise`) — o
  conjunto exato que `gateway.human.decision_binding` exige contra `allowed_inputs` do formulario
  `auth_pendencia`; NENHUM `auditor_id` (nao nasce negativa nesta tarefa)
- **And** `ClassifiedDecisionEngineIT.qualifiedCompletionAndExactRetryShareOneReceipt` passa com
  esta task key: conclusao qualificada e retry exato compartilham UM recibo
- **And** instancia ainda ATIVA na definicao ANTERIOR (sem `formData`) continua com binding
  indisponivel — a emenda nao migra instancia

### test_prazo_extra_nao_rearma_espera (lacuna registrada, nao corrigida)
- **Given** `UT_DecidirPendenciaExpirada` aberta
- **When** completada com `decisao_pendencia=conceder_prazo_extra`
- **Then** a instancia segue para `BRT_SlaAnalise` (identico a `seguir_analise`) e **NAO** retorna
  a `GW_AguardarDocs`; nenhuma nova espera documental e criada. O teste PROVA a lacuna do
  contrato — ele falha se alguem "consertar" a semantica sem o pacote BPMN/DMN/form/worker e a
  decisao do dono do contrato/SME

## Entradas das jornadas (WP-BPMN-J1)

### test_intake_portal_inicia_por_mensagem_no_mesmo_processo
- **Given** definicao implantada com `Start_IntakePortal` (`msg.auth.start`)
- **When** start por mensagem com business key `AUTH-amh-GUIA-TESTE-0001` e as VARIAVEIS DE ENTRADA
- **Then** UMA instancia de `SP-OP-AUTH-001` ativa; passa por `ST_PublishReceived` (`auth.received`
  publicado) e chega a `BRT_Admissibilidade` — o MESMO caminho do none start, sem processo,
  evento ou fluxo por publico
- **And** o none start `Start_SolicitacaoRecebida` continua funcional: start por key/id do canal de
  agente nao regride (a definicao tem dois start events)

### test_intake_dois_canais_uma_guia_uma_instancia (BLOQUEADO ate #16 implementado)
- **Given** instancia ativa iniciada pelo canal de agente com `AUTH-amh-GUIA-TESTE-0001`
- **When** o portal inicia por `msg.auth.start` com a MESMA business key
- **Then** nenhuma segunda instancia ativa
- **Nota:** este caso so e executavel quando o despachante passar a usar a chave contratual e
  `AUTH` estiver classificado em `_START_DEDUP_POLICY` (WP-J1-01/WP-J1-11). Ate la o caminho de
  mensagem esta INERTE e somente guias sinteticas circulam

### test_resposta_documental_correlaciona_na_espera_existente
- **Given** instancia aguardando em `GW_AguardarDocs` com um pedido documental aberto
- **When** a operacao nativa `auth.documents.respond` correlaciona `msg.auth.docs_received` pela
  subscricao/ocorrencia exata (nao pela business key)
- **Then** `documentos_refs`/`documentacao_completa` sao reescritos a partir da verificacao da
  politica documental e `BRT_Admissibilidade` e reavaliada
- **And** mensagem atrasada, espera expirada/substituida ou corrida com `ICE_PrazoPendencia` NAO
  consome uma espera posterior; comando repetido retorna o MESMO recibo
- **And** nenhuma mensagem nova foi criada para o publico beneficiario: prestador e beneficiario
  autorizado usam a MESMA `msg.auth.docs_received` no MESMO `ICE_DocsRecebidos`

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseMedicoAuditor` aberta (urgencia: `sla.sla_alerta=PT1H`)
- **When** job do timer `BT_AlertaSla` executado
- **Then** `operadora.auth.notify_sla_risk` recebeu task; User Task segue aberta

### test_timer_sla_estourado_coordenacao_assume
- **Given** analise aberta alem de `sla.sla_analise`
- **When** job do timer `BT_SlaAnalise` executado
- **Then** `auth.sla_breached` publicado; `UT_AnaliseMedicoAuditor` cancelada; `UT_CoordenacaoAssume` criada (`coordenacao-auditoria-medica`) — decisao continua humana

### test_dmn_auth_sla_urgencia
- **Given/When** start com `carater_atendimento=urgencia`
- **Then** `sla.sla_analise == "PT2H"` (fonte registrada na variavel `fonte_regulatoria`)

## Junta medica (DRAFT)

### test_junta_medica_parecer_aprova
- **Given** auditor completa com `decisao_auditor=JUNTA_MEDICA`
- **When** `operadora.auth.convene_junta` completa; `UT_RegistrarParecerJunta` completada com `decisao_auditor=APROVAR`
- **Then** autorizacao emitida via caminho normal (`End_AprovadaAuditor`)

### test_solicitar_info_volta_para_pendencia
- **Given** auditor completa com `decisao_auditor=SOLICITAR_INFO`
- **Then** `operadora.auth.request_documents` reexecutado; instancia aguarda em `GW_AguardarDocs`

## Idempotencia

### test_business_key_uma_instancia_por_guia
- **Given** instancia ativa `AUTH-amh-GUIA-TESTE-0001`
- **When** reenvio da mesma guia
- **Then** sem segunda instancia ativa

## Pedido de documentos — ponte PHI (WP-J1-03, decisao do dono #18)

Contrato: `docs/processes/contracts/SP-OP-AUTH-001.md`, "Pedido de documentos — ponte PHI".
Todos os casos abaixo estao implementados em
`tests/unit/gateway/document_requests/test_production_bridge.py`, exceto os tres marcados
`[motor]`, que precisam de motor real e estao listados para o executor de integracao.

### Exclusividade do topico

#### test_generic_harness_serves_the_topic_only_while_the_bridge_is_absent
- **Given** o conjunto canonico de topicos derivado de `register_default_workers`
- **When** calculado com e sem a ponte instalada
- **Then** 117 topicos sem a ponte, 116 com; a diferenca e' EXATAMENTE
  `{operadora.auth.request_documents}` e nenhum topico vizinho se move

#### test_readiness_expectation_and_registration_cannot_disagree
- **Given** as duas posturas de `MAEZO_AUTH_DOCUMENT_REQUEST_HOST`
- **Then** o harness vivo e o conjunto esperado por `/readyz` coincidem nas duas — `/readyz`
  nunca exige um topico deliberadamente ausente

#### test_host_refuses_to_install_while_the_generic_worker_still_holds_the_topic
- **Given** o conjunto de topicos do harness generico ainda contendo o topico
- **Then** `DocumentRequestHost.assert_exclusive` recusa

#### test_builder_refuses_on_a_shared_topic_before_composing_anything
- **Then** a recusa precede `compose` (canais nativos e pools de banco nunca chegam a abrir)

### Destinatarios (decisao #18)

#### test_provider_and_beneficiary_both_become_recipients_when_both_hold_authority
- **Given** `resource_authority` ativa de `auth.documents.respond` para prestador e beneficiario
- **Then** dois destinatarios, audiencias `provider` e `beneficiary`, digests ordenados e unicos

#### test_provider_only_is_admitted_when_no_beneficiary_holds_an_authority
- **Then** um destinatario prestador; a ausencia de delegacao do beneficiario nao e' erro

#### test_a_beneficiary_holding_an_authority_but_omitted_by_the_policy_is_refused
- **Then** RECUSA — a metade silenciosa da decisao #18

#### test_a_policy_naming_a_principal_with_no_active_authority_is_refused
#### test_a_request_with_no_provider_recipient_is_refused
#### test_a_provider_audience_without_a_provider_reference_is_refused
#### test_a_staff_actor_on_a_respond_authority_is_refused_not_silently_dropped
#### test_two_authorities_for_one_principal_are_refused_never_preferred
#### test_every_published_validity_condition_refuses_the_recipient
- consentimento revogado; base `consent` sem consentimento valido; janela expirada; janela
  ainda nao aberta
#### test_an_observation_ceiling_already_passed_refuses_the_recipient
#### test_recipient_identity_separates_membership_revisions
#### test_recipient_valid_until_never_outlives_its_authority_or_source

### Fonte de politica (nunca autora)

#### test_policy_returns_the_attested_publication_byte_for_byte
#### test_policy_refuses_when_the_published_policy_disagrees_with_the_authorities
#### test_policy_refuses_when_no_document_policy_head_is_published
- **Then** ausencia de head publicada e' recusa, nunca uma politica default inventada
#### test_policy_refuses_an_already_answered_request
#### test_policy_refuses_a_head_bound_to_another_case_or_request
#### test_restart_resumes_the_sealed_publication_and_refuses_a_newer_head
#### test_successor_refuses_because_no_installed_source_issues_an_invocation_authority
#### test_notice_projects_the_recipients_and_carries_no_invented_prior_command
#### test_notice_rereads_the_authorities_so_a_revocation_between_the_calls_refuses
#### test_notice_refuses_a_receipt_that_does_not_bind_this_policy
#### test_the_source_refuses_an_observation_from_another_tenant

### Autoridade de conclusao

#### test_completion_authority_acquires_only_the_outcome_designation
#### test_completion_authority_refuses_the_fetch_purpose_without_touching_the_provider
#### test_completion_authority_refuses_a_lease_for_another_capability_or_binding

### Custodia PHI

#### test_the_production_module_never_reaches_phi_body_custody
- **Then** o modulo de autoridade nao nomeia nenhum simbolo nem tabela do plano de corpo PHI;
  as unicas relacoes citadas sao `mzo_auth_input_head` e `mzo_auth_input_version`

### Motor real (nao cobertos pelo nivel unitario)

#### [motor] test_case_respond_authorities_selects_by_resource_over_published_heads
- **Given** heads `resource_authority` publicadas para o mesmo caso, uma delas revogada
- **When** `CaseRespondAuthorities.read`
- **Then** so' as verificadas por `NativeAuthReader.head` voltam; a revogada e' omitida

#### [motor] test_document_policy_publication_request_round_trips_from_the_version_row
- **Then** a `InputPublication` lida de `mzo_auth_input_version` bate digest com a head

#### [motor] test_solicitar_info_produces_one_inbox_line_for_provider_and_one_for_beneficiary
- **Given** autoridade ativa dos dois e `document_policy` que nomeia os dois
- **When** o auditor completa com `decisao_auditor=SOLICITAR_INFO`
- **Then** DUAS linhas de caixa de entrada, uma por destinatario, e a tarefa externa concluida
  exatamente uma vez
