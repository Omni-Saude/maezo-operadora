# Test spec — SP-OP-CRED-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" de Phase 0/1/2/3; **sem engine mock** — ADR-0011). Arquivo alvo:
`tests/integration/processes/test_sp_op_cred_001.py`. O modulo **self-contains** suas fixtures
(NAO edita o `conftest.py` compartilhado): redefine `engine`, `deploy_artifacts`, `cred_probe`,
`start_cred` localmente, espelhando o padrao de `test_sp_op_cancel_001.py`/`test_sp_op_fraude_001.py`.

Dados sinteticos: prestador `PREST-TESTE-NNNN` (cadastral pseudonimo — **NUNCA** CPF/nome real),
tenant `amh`, origem `TESTE`. Business key `CRED-amh-{prestador_id}` (variante por protocolo:
`CRED-amh-{prestador_id}-{protocolo_cred}`). Process key: `SP-OP-CRED-001` (exato — nao alterar).

`CredEngineProbe` drena as external tasks com os workers reais Phase-3 (`register_cred_workers`) +
`register_phase0_workers` (publicador generico). Como `WorkerHarness` **NAO** retorna variaveis de
saida de roteamento ao engine (os workers observam/ecoam/publicam, mas nao decidem), os TESTES seed
os fatos clericais (`direcao`, `tipo_prestador`, `licenca_valida`, `documentacao_completa`,
`dentro_criterios_rede`, `indicio_irregularidade_sinalizado`, `tem_beneficiarios_vinculados`) como
variaveis de **START** — o teste e o agente de origem (mesma tecnica de `test_sp_op_cancel_001.py`).

## Invariante L1 (DoD deliverable) — provider_decredentialing / no-adverse (DUAS direcoes adversas)

Espelha `docs/processes/contracts/SP-OP-CRED-001.md` §Invariante L1: **descredenciar** um prestador
ja credenciado **e** **negar** um pedido de credenciamento sao duas direcoes adversas distintas;
ambas so nascem em User Task humana.

### test_nenhum_caminho_automatizado_descredencia_ou_nega
- **Given** varredura de TODAS as combinacoes de input das DMN `cred_admissibility` + `cred_route`:
  `direcao ∈ {credenciamento, descredenciamento}` × `tipo_prestador ∈ {hospital, clinica,
  pessoa_fisica}` × `licenca_valida ∈ {true,false}` × `documentacao_completa ∈ {true,false}` ×
  `dentro_criterios_rede ∈ {true,false}` × `indicio_irregularidade_sinalizado ∈ {true,false}`
  (2 × 3 × 2 × 2 × 2 × 2 = **96 combinacoes**)
- **When** cada instancia percorre ate estabilizar (consultando `history/activity-instance` do
  engine), **sem completar nenhuma User Task**
- **Then** NENHUMA instancia atinge `End_PrestadorDescredenciado` / `End_CredenciamentoNegado` sem
  que a history contenha `UT_AnaliseDescredenciamento` / `UT_AnaliseCredenciamento` (ou as UTs de
  coordenacao `UT_CoordenacaoRedeDescred` / `UT_CoordenacaoRedeCred`) **concluida por humano**. A
  varredura completa (96/96) nao produz nenhum terminal adverso automatico.

> Assercao central (history-based, sem mock do engine): `_assert_no_adverse_without_human_task`
> consulta `activity_instances_ended` — se um terminal adverso esta no historico, exige que ao menos
> uma das 4 User Tasks humanas tambem esteja. Prova por vacuidade quando nenhum terminal adverso e
> atingido.

### test_credenciamento_licenca_irregular_roteia_para_humano_nunca_auto_nega
- **Given** `direcao=credenciamento`, `tipo_prestador=clinica`, `documentacao_completa=true`,
  `licenca_valida=false`, `dentro_criterios_rede=true`
- **When** instancia percorre ate `UT_AnaliseCredenciamento`
- **Then** fluxo chega a `UT_AnaliseCredenciamento` (`gestao-rede`) — **nunca** a
  `End_CredenciamentoNegado`. Licenca aparentemente irregular NAO produz negativa automatica.

### test_indicio_irregularidade_roteia_para_humano_nunca_auto_acusa
- **Given** `direcao=credenciamento`, `indicio_irregularidade_sinalizado=true`, `licenca_valida=true`,
  `documentacao_completa=true`, `dentro_criterios_rede=true`
- **When** instancia percorre ate `UT_AnaliseDescredenciamento`
- **Then** fluxo chega a `UT_AnaliseDescredenciamento` (`juridico-rede`); nenhum terminal adverso
  automatico; nenhum caminho automatizado registra acusacao de fraude (`fraud_accusation` L0 hard —
  a UT decide se encaminha a SP-OP-FRAUDE-001 via `encaminhar_fraude`).

## Happy paths

### test_happy_path_credenciamento_clerical_neutro
- **Given** `direcao=credenciamento`, `documentacao_completa=true`, `licenca_valida=true`,
  `dentro_criterios_rede=true`
- **Then** `CLERICAL_CREDENCIAR` (DMN favoravel); `operadora.cred.register_credenciamento`
  executado; `cred.network_changed` (`tipo_mudanca=prestador_credenciado`) + `cred.completed`
  (`desfecho=credenciado`); fim `End_PrestadorCredenciado`; **NENHUMA** User Task adversa criada;
  `register_cred_denial`/`register_descredenciamento` NUNCA invocados.

### test_happy_path_descredenciamento_humano
- **Given** `direcao=descredenciamento`, `tem_beneficiarios_vinculados=false` → `UT_AnaliseDescredenciamento`
- **When** humano completa `decisao_cred=DESCREDENCIAR` + `fundamentacao` + `referencia_regulatoria`
  + `comprovacao_notificacao_previa` + `responsavel_id` + `tier`
- **Then** `operadora.cred.register_descredenciamento` executado (guard satisfeito);
  `cred.network_changed` (`prestador_descredenciado`) + `cred.completed` (`descredenciado`); fim
  `End_PrestadorDescredenciado`; `responsavel_id`+`tier` na trilha de auditoria (ADR-0007).

### test_happy_path_descredenciamento_com_substituicao
- **Given** `direcao=descredenciamento`, `tipo_prestador=hospital`, `tem_beneficiarios_vinculados=true`
- **When** humano completa `DESCREDENCIAR` + `plano_substituicao` (substituto equivalente, RN 567)
- **Then** fim `End_SubstituicaoRegistrada` (nao `End_PrestadorDescredenciado` puro);
  `cred.network_changed` (`substituicao_registrada`) + `cred.completed` (`substituicao_registrada`).

### test_happy_path_descredenciamento_manter_vinculo
- **Given** `UT_AnaliseDescredenciamento` aberta
- **When** humano completa `decisao_cred=MANTER`
- **Then** fim `End_VinculoMantido`; `cred.completed` (`desfecho=vinculo_mantido`);
  `register_descredenciamento` NUNCA invocado; nenhum terminal adverso.

### test_happy_path_credenciamento_negado_humano
- **Given** `direcao=credenciamento`, `licenca_valida=false` → `UT_AnaliseCredenciamento`
- **When** humano completa `decisao_cred=NEGAR_CREDENCIAMENTO` + `fundamentacao` +
  `referencia_regulatoria` + `responsavel_id` + `tier`
- **Then** `operadora.cred.register_cred_denial` executado (guard satisfeito); fim
  `End_CredenciamentoNegado` — **UNICA** via ao terminal (decisao humana). `cred.completed`
  (`desfecho=credenciamento_negado`).

### test_happy_path_credenciamento_aprovado_humano
- **Given** `UT_AnaliseCredenciamento` aberta (licenca irregular)
- **When** humano completa `decisao_cred=APROVAR_CREDENCIAMENTO`
- **Then** fim `End_PrestadorCredenciado`; `register_cred_denial` NUNCA invocado; nenhum terminal
  adverso.

## Aceite exige campos / worker guard (defesa em profundidade)

### test_descredenciar_exige_campos_worker_guard
- **Given** `UT_AnaliseDescredenciamento` aberta
- **When** humano completa `decisao_cred=DESCREDENCIAR` **sem** `fundamentacao` /
  `referencia_regulatoria` / `comprovacao_notificacao_previa` / `responsavel_id` / `tier`
- **Then** `ST_RegisterDescredenciamento` lanca `ERR_DECRED_NOT_HUMAN`; a instancia **NAO** atinge
  `End_PrestadorDescredenciado`; `register_descredenciamento` NUNCA publica. **GAP-CRED-1 (#96)
  regressao:** o boundary catch `BE_DecredNaoHumano` captura o erro e termina a instancia de forma
  **LIMPA** em `End_DecredBloqueadoNaoHumano` (guard/fail-safe NEUTRO) — antes do fix, o mesmo
  cenario deixava a instancia com **incidente TRAVADO** (nenhum end event atingido).

### test_negar_exige_campos_worker_guard
- **Given** `UT_AnaliseCredenciamento` aberta
- **When** humano completa `decisao_cred=NEGAR_CREDENCIAMENTO` sem os campos obrigatorios
- **Then** `ST_RegisterCredDenial` lanca `ERR_CRED_DENIAL_NOT_HUMAN`; a instancia **NAO** atinge
  `End_CredenciamentoNegado`. **GAP-CRED-1 (#96) regressao:** o boundary catch
  `BE_CredDenialNaoHumano` termina a instancia LIMPO em `End_CredGuardBloqueadoNaoHumano`.

### test_register_descredenciamento_recusa_sem_humano / test_register_cred_denial_recusa_sem_humano
- **Given** invocacao direta dos handlers `make_register_descredenciamento_handler` /
  `make_register_cred_denial_handler` (unit-style, **sem engine** — roda mesmo sem o dev-stack)
- **When** `decisao_cred` ausente/errado ou faltam campos obrigatorios
- **Then** `WorkerBpmnError` com `ERR_DECRED_NOT_HUMAN` / `ERR_CRED_DENIAL_NOT_HUMAN`; nada
  publicado. Com decisao humana completa: registra e carrega `responsavel_id`+`tier` na auditoria
  (ids deterministicos `DESCRED-*` / `CREDNEG-*`).

## Boundary-error catches (GAP-CRED-1, #96) — guards TECNICOS terminam LIMPO, nunca travam

Espelha o padrao `BE_SemConsentimento` de SP-OP-PROGRAMA-001. Os 3 erros declarados no contrato
(`Error_CredPrestadorInvalido` / `Error_DecredNotHuman` / `Error_CredDenialNotHuman`) tinham, antes
de #96, **zero** boundary catches — um erro lancado deixava a instancia com incidente TRAVADO na
mainline em vez de terminar de forma limpa. Os 3 terminais abaixo sao **NEUTROS** (guard/fail-safe,
nao decisoes adversas automatizadas) e ja constam no sweep consolidado
(`tests/integration/processes/test_no_denial_consolidated.py`).

### test_prestador_id_ausente_termina_limpo_sem_incidente_travado
- **Given** start de `SP-OP-CRED-001` com `prestador_id=""` (ausente/vazio)
- **When** `ST_VerifyCredentials` (primeiro service task da mainline) executa
- **Then** o worker lanca `ERR_CRED_INVALID_PRESTADOR` (validacao de origem — FATO, nunca decisao);
  o boundary catch `BE_PrestadorInvalido` termina a instancia LIMPO em `End_CredPrestadorInvalido`
  (fail-safe NEUTRO, primeiro service task). Nenhum efeito adverso; nenhum worker adverso invocado.

## Documentacao pendente (nunca negativa)

### test_documentacao_incompleta_pendente_nunca_nega
- **Given** `direcao=credenciamento`, `documentacao_completa=false`, `licenca_valida=true`,
  `dentro_criterios_rede=true`
- **Then** `PENDENTE_DOCUMENTACAO`; `operadora.cred.notify_doc_pendente` executado; `cred.pended`
  publicado; documentacao incompleta NUNCA produz negativa automatica — instancia aguarda ativa.

## Notificacao previa (cure-window — INVERTE auto-descredenciamento por timeout)

### test_prazo_notificacao_expira_vai_para_humano_nao_descredencia
- **Given** `direcao=descredenciamento` aguardando no cure-window (`ICE_PrazoNotificacao`), sem ack
- **When** job do timer executado (job execution, NUNCA sleep)
- **Then** `UT_AnaliseDescredenciamento` criada (`gestao-rede`/`juridico-rede`) — a expiracao do
  prazo **NUNCA** alcanca `End_PrestadorDescredenciado` automaticamente (INVERTE
  `Boundary_LicenseTimeout`→`Task_ExpireRequest` do reference SP-PS-002).

### test_notificacao_ack_destrava_analise
- **Given** aguardando no cure-window
- **When** message `msg.cred.notification_ack` correlacionada (business key)
- **Then** fluxo segue para `UT_AnaliseDescredenciamento` — nunca auto-descredencia por ack.

## Timers de SLA

### test_timer_sla_estourado_coordenacao_assume
- **Given** `UT_AnaliseDescredenciamento` aberta alem de `${cred_sla.sla_analise}`
- **When** job do timer interruptivo `BT_SlaDescred` executado
- **Then** `cred.sla_breached` publicado; `UT_AnaliseDescredenciamento` cancelada (interruptivo);
  `UT_CoordenacaoRedeDescred` criada (`coordenacao-rede`) — decisao continua humana, mesmos campos
  obrigatorios; nenhum terminal adverso automatico.

### test_coordenacao_assume_e_descredencia
- **Given** SLA estourado; `UT_CoordenacaoRedeDescred` aberta
- **When** coordenacao humana completa `DESCREDENCIAR` + campos obrigatorios
- **Then** fim `End_PrestadorDescredenciado` (decisao continua humana, via a **segunda** via de UT).

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseDescredenciamento` aberta
- **When** job do timer nao-interruptivo `BT_AlertaSlaDescred` executado
- **Then** `operadora.cred.notify_sla_risk` recebeu task; a User Task segue aberta; nenhum desfecho
  adverso.

## DMN — shape e typeRef allowlist (sem engine; varredura estatica do XML)

### test_cred_admissibility_sem_saida_adversa
- **Then** dominio de `roteamento` **exatamente** `{CLERICAL_CREDENCIAR, SEGUE_ANALISE,
  PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}`; nenhum valor `NEGAR`/`DESCREDENCIAR`/`RECUSAR`/`DESLISTAR`;
  row catch-all → `ANALISE_HUMANA`.

### test_cred_route_sem_saida_adversa
- **Then** dominio de `roteamento` **exatamente** `{ANALISE_CREDENCIAMENTO,
  ANALISE_DESCREDENCIAMENTO, ANALISE_HUMANA}`; sem `NEGAR`/`DESCREDENCIAR`/`AUTO_APROVAR_ADVERSO`/
  `FRAUD_DETECTED`; row catch-all → `ANALISE_HUMANA`.

### test_dmn_typeref_allowlist
- **Then** toda DMN (`cred_admissibility`, `cred_route`, `cred_prior_notice`, `cred_sla`) usa
  `typeRef ∈ {string, boolean, integer, long, double, date}` — `number` proibido.

### test_dmn_history_ttl_e_hitpolicy
- **Then** toda `<decision>` tem `camunda:historyTimeToLive`; toda `<decisionTable>` tem `hitPolicy`.

### test_bpmn_user_tasks_tem_candidate_groups
- **Then** as 4 `<bpmn:userTask>` (2 analise + 2 coordenacao) trazem `camunda:candidateGroups`
  nao-vazio (gate D1).

### test_bpmn_xml_ids_unicos
- **Then** todos os ids do BPMN sao unicos (classe ENGINE-22004).

## Idempotencia

### test_business_key_uma_instancia_por_prestador
- **Given** instancia ativa `CRED-amh-{prestador}`
- **When** reenvio da mesma solicitacao
- **Then** sem segunda instancia ativa (`start_process` consulta a business key e retorna a
  existente).
