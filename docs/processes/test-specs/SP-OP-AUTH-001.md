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

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseMedicoAuditor` aberta (urgencia: `sla.sla_alerta=PT1H`)
- **When** job do timer `BT_AlertaSla` executado
- **Then** `operadora.auth.notify_sla_risk` recebeu task; User Task segue aberta
- **And** (WP-J1-09, decisao do dono #17) a sonda observa UMA notificacao
  `type=auth.notify_sla_risk` em `operadora.notifications.internal`, com `tenant_id` e
  `numero_guia_tiss`. Ate WP-J1-09 este canal era MORTO — o worker era sincrono e sem seam de
  Kafka — e a asserção de notificacao tinha sido removida por ser um fato fabricado
  (FAB-SLA-RISK-NOTIFIED-SLICE4); agora ela volta porque ha publicacao real.

### test_alerta_sla_de_auth_abre_escalonamento_humano (WP-J1-09 — prova de ponta a ponta)
- **Given** `UT_AnaliseMedicoAuditor` aberta numa guia com `sla.sla_alerta` curto, com o
  `notification_bridge` consumindo `operadora.notifications.internal`
- **When** o job do timer `BT_AlertaSla` executa e o bridge processa a notificacao
- **Then** existe UMA instancia de `SP-OP-ESCALATION-001` com business key
  `ESC-{tenant_id}-sla-auth-{numero_guia_tiss}`, e `UT_TratarEscalonamento` aparece em `/tasks`
  com o grupo RESOLVIDO pela DMN `escalation_routing` (catch-all `r7` -> `atendimento-humano`,
  P2) — grupo vindo do engine, nunca do worker
- **And** `UT_AnaliseMedicoAuditor` continua ABERTA e nenhuma decisao adversa foi tomada: o
  escalonamento e informativo e nao move a titularidade do caso
- **And** reexecutar o mesmo alerta NAO cria segunda instancia (start idempotente pela business
  key); a fila nao inunda

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
