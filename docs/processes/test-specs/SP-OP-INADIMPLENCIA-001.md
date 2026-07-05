# Test spec — SP-OP-INADIMPLENCIA-001 (DRAFT — acompanha o processo; GAP-INAD-4)

Suite de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina "nunca
mockar o engine" — ADR-0011) + testes **ESTATICOS** (varredura do XML BPMN/DMN, sem engine; rodam
no lane unit/CI rapido) + um teste unitario do worker-guard (sem engine). Arquivo alvo (JA
implementado): `tests/integration/processes/test_sp_op_inadimplencia_001.py`. O modulo
**self-contains** suas fixtures (redefine `engine`/`deploy_artifacts`/`inad_probe`/`start_inad`
localmente — NAO edita o `conftest.py` compartilhado), espelhando `test_sp_op_cancel_001.py`.

Dados sinteticos: contrato `CONTRATO-TESTE-{hex}` (unico por teste), beneficiario
`BENEF-TESTE-0001` (pseudonimo — NUNCA CPF/nome real), tenant `amh`, origem `operadora`. Business
key `INAD-amh-{numero_contrato}`. Process key: `SP-OP-INADIMPLENCIA-001` (exato — nao alterar).

**Harmonizacao com CANCEL-001 (vinculante — ver `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
§Harmonizacao e `docs/processes/harmonization-inadimplencia-cancel.md` DRAFT-A):**
INADIMPLENCIA-001 detem a **SUSPENSAO** (`End_ContratoSuspenso_Inad`, ADVERSO, human-gated) e o
ciclo de cobranca/purga. A **RESCISAO** e propriedade EXCLUSIVA de SP-OP-CANCEL-001: quando o
humano decide `ENCAMINHAR_RESCISAO`, o processo roda o worker de handoff NEUTRO
`operadora.inadimplencia.handoff_rescisao` (inicia/correlaciona CANCEL-001 por business key
`CANCEL-{tenant}-{numero_contrato}`) e termina em `End_RescisaoHandoffCancel` (NEUTRO) — o processo
**nao declara nem alcanca** nenhum terminal de rescisao local. `register_contract_rescission` /
`Error_ContractRescissionNotHuman` do contrato NAO sao wireados a nenhum terminal (DRAFT-A: "DROP
both" — o BPMN so declara `Error_InadContratoInvalido` e `Error_ContractSuspensionNotHuman`).

## Invariante L0 — no-adverse (testes de seguranca — PRIORITARIOS, parte 5 de 5 do §4-bis-F)

### test_nenhum_caminho_automatizado_suspende_contrato
- **Given** varredura de TODAS as combinacoes de input da DMN `inadimplencia_status`:
  `tipo_plano ∈ {individual, familiar, coletivo_empresarial, coletivo_adesao}` ×
  `dentro_periodo_minimo ∈ {true,false}` × `notificacao_previa_feita ∈ {true,false}` ×
  `dentro_janela_purga ∈ {true,false}` (4×2×2×2 = 32 combinacoes)
- **When** cada instancia percorre ate estabilizar (drena workers reais; sem completar nenhuma
  User Task)
- **Then** NENHUMA das 32 combinacoes atinge `End_ContratoSuspenso_Inad` automaticamente; para toda
  instancia, se um terminal adverso estiver na history, `UT_AnaliseInadimplencia` OU
  `UT_CoordenacaoCobranca` humana tambem esta (`_assert_no_adverse_without_human_task`,
  history-based via `history/activity-instance`).

### test_inadimplencia_aparente_roteia_para_humano_nao_suspende
- **Given** `dentro_periodo_minimo=true`, `notificacao_previa_feita=true`,
  `dentro_janela_purga=false`
- **Then** DMN `inadimplencia_status` retorna `SEGUE_ANALISE` (nunca `SUSPENDER`); fluxo chega a
  `UT_AnaliseInadimplencia` (`juridico-contratos`), nao a `End_ContratoSuspenso_Inad`. Inadimplencia
  aparente, por si so, nunca suspende por automacao.

## Cure-window (event gateway — INVERTE auto-suspender/auto-rescindir por timeout)

### test_pagamento_dentro_janela_purga_purgado_nunca_adverso
- **Given** `dentro_janela_purga=true`, `notificacao_previa_feita=true` — instancia aguarda no
  `GW_CureWindow` (event-based gateway)
- **When** message `msg.inadimplencia.pagamento_recebido` correlacionada (business key)
- **Then** fim `End_Purgado` (NEUTRO); `agents.events.inadimplencia.completed`
  (`desfecho=purgado`); **NUNCA** um terminal adverso.

### test_expiracao_purga_nao_auto_suspende
- **Given** aguardando no `GW_CureWindow`, sem pagamento
- **When** job do timer `ICE_PrazoPurga` (RN 593 — DRAFT/verify) executado via job execution
  (nunca sleep)
- **Then** `UT_AnaliseInadimplencia` criada (`juridico-contratos`) — a expiracao da janela de purga
  **NUNCA** alcanca `End_ContratoSuspenso_Inad` automaticamente (INVERTE o anti-pattern de
  auto-suspender/auto-rescindir-no-timeout do reference).

### test_notificacao_ack_reavalia_nunca_suspende
- **Given** aguardando no `GW_CureWindow`
- **When** message `msg.inadimplencia.notificacao_ack` correlacionada (`dentro_janela_purga=false`
  nas process variables da correlacao)
- **Then** fluxo reavalia e chega a `UT_AnaliseInadimplencia` (`juridico-contratos`) — recebimento
  do ack **nunca** suspende automaticamente.

## Happy paths

### test_happy_path_suspensao_por_inadimplencia_humano
- **Given** `SEGUE_ANALISE`; `UT_AnaliseInadimplencia` aberta (dossie preparado por
  `ST_PrepareDossier`/Fernando)
- **When** humano completa `decisao_inadimplencia=SUSPENDER` + `fundamentacao_contratual` +
  `referencia_regulatoria` + `comprovacao_notificacao_previa` + `comprovacao_periodo_minimo` +
  `responsavel_id` (+ `tier` opcional)
- **Then** `operadora.inadimplencia.register_contract_suspension` executado (guard satisfeito);
  `inadimplencia.completed` (`desfecho=suspenso`); fim `End_ContratoSuspenso_Inad`; a notificacao
  de registro carrega `decisao_inadimplencia=SUSPENDER` e `responsavel_id`.

### test_happy_path_encaminhar_rescisao_handoff_neutro_nao_rescinde
- **Given** `SEGUE_ANALISE`; `UT_AnaliseInadimplencia` aberta
- **When** humano completa `decisao_inadimplencia=ENCAMINHAR_RESCISAO` + os mesmos campos
  obrigatorios de SUSPENDER (fundamentacao/referencia/comprovacoes/responsavel_id — a decisao
  adversa-adjacente nunca muda de exigencia)
- **Then** `operadora.inadimplencia.handoff_rescisao` executado (NEUTRO — encaminha a CANCEL-001
  por business key `CANCEL-{tenant}-{numero_contrato}`, `cancel_process_key=SP-OP-CANCEL-001`); fim
  `End_RescisaoHandoffCancel` (NEUTRO); `inadimplencia.completed`
  (`desfecho=rescisao_handoff`). **NENHUM** terminal de rescisao local atingido;
  `register_contract_suspension` **NUNCA** invocado neste caminho.

### test_happy_path_contrato_mantido
- **Given** `SEGUE_ANALISE`; `UT_AnaliseInadimplencia` aberta
- **When** humano completa `decisao_inadimplencia=MANTER` + `fundamentacao_contratual` (ex.: purga
  negociada)
- **Then** fim `End_ContratoMantido` (NEUTRO); `inadimplencia.completed` (`desfecho=mantido`);
  nenhum efeito adverso.

## Aceite exige campos / worker-guard (defesa em profundidade)

### test_suspender_exige_campos_worker_guard
- **Given** `UT_AnaliseInadimplencia` aberta
- **When** humano completa `decisao_inadimplencia=SUSPENDER` **sem** os campos obrigatorios
  (fundamentacao_contratual/referencia_regulatoria/comprovacao_notificacao_previa/
  comprovacao_periodo_minimo/responsavel_id)
- **Then** `End_ContratoSuspenso_Inad` **NAO** e atingido; `register_contract_suspension`
  **NAO** publica (guard `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` recusa antes do registro).

## Anti-dupla-terminacao (GAP-INAD-1 — correlacao cross-process REAL, sem mock de engine)

### test_suspensao_recusada_se_ja_em_rescisao_cancel
- **Given** uma instancia REAL de SP-OP-CANCEL-001 ATIVA para o MESMO contrato (business key
  `CANCEL-amh-{numero_contrato}`, deployada e iniciada no proprio teste; estacionada ativa porque o
  `inad_probe` nao serve os topicos externos de CANCEL)
- **When** o fluxo de inadimplencia para o MESMO contrato roda `resolve_facts` (que CONSULTA o
  engine — nao ecoa uma variavel de entrada — via `CibSevenHttpTransport.find_active_instance` pela
  business key `CANCEL-{tenant}-{contrato}`) e o humano completa
  `decisao_inadimplencia=SUSPENDER` com todos os campos obrigatorios
- **Then** `ja_em_rescisao_cancel=true` e resolvido pelo worker e devolvido ao engine;
  `register_contract_suspension` **recusa** (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`);
  `End_ContratoSuspenso_Inad` **NAO** e atingido; nenhuma suspensao publicada — o contrato NUNCA e
  terminado duas vezes mesmo com decisao humana completa.

### test_suspensao_prossegue_sem_cancel_ativo
- **Given** NENHUMA instancia CANCEL-001 ativa para o contrato
- **When** `resolve_facts` consulta o engine (retorna `None` -> `ja_em_rescisao_cancel=false`) e o
  humano completa `SUSPENDER` com campos completos
- **Then** a suspensao PROSSEGUE: `register_contract_suspension` publica; fim
  `End_ContratoSuspenso_Inad` — prova o outro lado do guard pela consulta REAL (nao por seed).

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseInadimplencia` aberta
- **When** job do timer nao-interruptivo `BT_AlertaSla` executado
- **Then** `operadora.inadimplencia.notify_sla_risk` recebeu a task; `UT_AnaliseInadimplencia`
  segue aberta (nao cancelada); nenhum desfecho adverso produzido.

### test_timer_sla_estourado_coordenacao_assume
- **Given** `UT_AnaliseInadimplencia` aberta alem do SLA de analise
- **When** job do timer interruptivo `BT_SlaAnalise` executado
- **Then** `agents.events.inadimplencia.sla_breached` publicado; `UT_AnaliseInadimplencia`
  cancelada (interruptivo); `UT_CoordenacaoCobranca` criada (`coordenacao-cobranca`) — decisao
  continua humana; nenhum terminal adverso atingido so pelo estouro de SLA.

### test_coordenacao_assume_e_suspende
- **Given** SLA de analise estourado; `UT_CoordenacaoCobranca` aberta (**segunda** via humana ao
  mesmo terminal adverso)
- **When** coordenacao humana completa `decisao_inadimplencia=SUSPENDER` + campos obrigatorios
  (`responsavel_id` da coordenacao)
- **Then** `End_ContratoSuspenso_Inad` atingido via `UT_CoordenacaoCobranca` (a decisao adversa
  nunca muda de natureza por estouro de SLA); `inadimplencia.completed` (`desfecho=suspenso`).

## Pendencia de informacao (humano solicita)

### test_solicitar_info_aguarda_correlacao
- **Given** `UT_AnaliseInadimplencia` aberta
- **When** humano completa `decisao_inadimplencia=SOLICITAR_INFO`; depois message
  `msg.inadimplencia.info_received` correlacionada (business key)
- **Then** `UT_AnaliseInadimplencia` reaberta (`juridico-contratos`) para nova decisao humana;
  `SOLICITAR_INFO` **nunca** auto-suspende (L0).

## Worker-guard unit-style (SEM engine — roda sem dev-stack)

### test_register_contract_suspension_recusa_sem_humano
- **Given** invocacao DIRETA do handler real `make_register_contract_suspension_handler` (sem
  engine, sem docker) com variaveis: (a) ausentes; (b) `decisao_inadimplencia=MANTER` (neutra);
  (c) `decisao_inadimplencia=ENCAMINHAR_RESCISAO` (handoff — tampouco passa pelo worker de
  suspensao); (d) `SUSPENDER` mas faltando `responsavel_id`/campos obrigatorios; (e) `SUSPENDER` +
  campos completos mas `ja_em_rescisao_cancel=true`
- **Then** em todos os casos (a)-(e), `WorkerBpmnError(ERR_CONTRACT_SUSPENSION_NOT_HUMAN)` e
  levantado; nenhuma notificacao publicada
- **Given/When** (f) `decisao_inadimplencia=SUSPENDER` + `ja_em_rescisao_cancel=false` + todos os
  campos obrigatorios
- **Then** o worker emite o registro, carregando `responsavel_id`+`tier` e um `suspensao_id`
  determinista (`INADSUSP-{hash}`).

## DMN — shape e fail-safe (sem engine; varredura estatica do XML)

### test_inadimplencia_status_sem_saida_adversa
- **Then** o dominio de `roteamento` da DMN `inadimplencia_status` e EXATAMENTE `{AGUARDA_PURGA,
  PENDENTE_NOTIFICACAO, SEGUE_ANALISE, ANALISE_HUMANA}` — nenhum valor `SUSPENDER`/`RESCINDIR`/
  `NEGAR`/`RETER`; a ultima row (catch-all) roteia a `ANALISE_HUMANA`.

### test_inadimplencia_dmns_sem_saida_adversa
- **Then** nenhuma das 3 DMN do processo (`inadimplencia_status`/`inadimplencia_purga`/
  `inadimplencia_sla`) possui uma outputEntry com valor `SUSPENDER`/`RESCINDIR`/`NEGAR`/`RETER`.

### test_dmn_typeref_allowlist
- **Then** toda DMN do processo usa `typeRef ∈ {string, boolean, integer, long, double, date}`;
  `number` e proibido em qualquer coluna (§4-bis-A, ADR-0018 parte 2).

## ANTI-DUPLA-RESCISAO — topologia do BPMN (parse estatico; sem engine)

### test_inadimplencia_nao_tem_terminal_de_rescisao_proprio
- **Given** parse estatico do BPMN (`ElementTree`, sem engine)
- **Then** o conjunto de `endEvent` do processo e EXATAMENTE `{End_ContratoSuspenso_Inad,
  End_RescisaoHandoffCancel, End_Purgado, End_ContratoMantido, End_RiscoSlaNotificado}` — nenhum
  terminal de rescisao local (nenhum id contem os tokens `rescindid*`/`rescisao_local`/
  `contratorescindido`, exceto o proprio `End_RescisaoHandoffCancel`, que e o handoff NEUTRO e
  explicitamente permitido citar "rescisao" no nome).

### test_rescisao_so_acontece_via_handoff_a_cancel
- **Given** parse estatico do BPMN
- **Then** NENHUMA `serviceTask` do corpo usa o topico `operadora.inadimplencia.
  register_contract_rescission` (nao wireado a nada); o flow `Flow_GWDec_Rescisao`
  (`decisao_inadimplencia=='ENCAMINHAR_RESCISAO'`) alcanca `ST_HandoffRescisao`
  (`operadora.inadimplencia.handoff_rescisao`), que por alcancabilidade estatica chega SOMENTE a
  `End_RescisaoHandoffCancel` — nunca a `End_ContratoSuspenso_Inad`.

### test_bpmn_user_tasks_tem_candidate_groups
- **Then** toda `userTask` do corpo (`UT_AnaliseInadimplencia`, `UT_CoordenacaoCobranca`) declara
  `camunda:candidateGroups` nao-vazio (gate humano L0 estrutural).

### test_bpmn_xml_ids_unicos
- **Then** todos os ids do documento BPMN sao unicos (ENGINE-22004 / cvc-id.2 — a duplicacao
  quebraria o deploy no engine real).

## Idempotencia (residuo — nao coberto por teste dedicado nesta suite)

A instancia sempre inicia via `engine.start_by_key("SP-OP-INADIMPLENCIA-001", "INAD-amh-{numero_
contrato}", ...)` (mesma semantica idempotente de `mcp-cibseven.start_process` — a business key e
consultada antes de iniciar, ver `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
§"Business key"). `test_sp_op_cancel_001.py::test_business_key_uma_instancia_por_contrato` prova
esse comportamento para CANCEL-001; um teste analogo (`test_business_key_uma_instancia_por_
contrato`, reenvio da mesma solicitacao => sem segunda instancia ativa) **ainda nao existe** para
INADIMPLENCIA-001 nesta suite — registrado aqui como gap de cobertura para o proximo batch, nao
bloqueante (a propriedade e a MESMA do engine, ja provada estruturalmente por CANCEL-001).

## Eventos de dominio (auditoria dupla — engine + Kafka, ADR-0007)

Cada teste happy-path acima ja prova, via `InadEngineProbe.has_event`, que
`agents.events.inadimplencia.completed` e publicado com o `payload.desfecho` correto
(`suspenso`/`rescisao_handoff`/`purgado`/`mantido`) antes do fim da instancia. Os terminais
adversos (`End_ContratoSuspenso_Inad`) carregam `responsavel_id` no payload de
`ST_PublishSuspenso` (`event_payload_vars=tenant_id,numero_contrato,responsavel_id`).
