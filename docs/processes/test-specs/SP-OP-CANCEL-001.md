# Test spec — SP-OP-CANCEL-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" — Phase 0/1). Arquivo alvo:
`tests/integration/processes/test_sp_op_cancel_001.py`. Cada teste e descrito ao nivel de
stub executavel: guia a implementacao do teste de integracao contra o engine real, sem mock.

Dados sinteticos: contrato `CONTRATO-TESTE-0001`, beneficiario `BENEF-TESTE-0001`
(pseudonimo — NUNCA CPF/nome real; faixas de CPF invalidas se necessario), tenant `amh`,
lote/origem `TESTE`. Business key `CANCEL-amh-CONTRATO-TESTE-0001`.

## Invariante L0 — no-denial (testes de seguranca — PRIORITARIOS, parte 5 de 5 do §4-bis-F)

### test_nenhum_caminho_automatizado_rescinde_contrato
- **Given** varredura de TODAS as combinacoes de input da DMN `cancel_admissibility`:
  `tipo_solicitacao ∈ {pedido_beneficiario, inadimplencia, for_cause_operadora, fraude_referida}`
  × `tipo_plano ∈ {individual, familiar, coletivo_empresarial, coletivo_adesao}`
  × `dentro_prazo ∈ {true,false}` × `notificacao_previa_feita ∈ {true,false}`
  × `titularidade_confirmada ∈ {true,false}`
- **When** cada instancia percorre ate estabilizar (sem completar nenhuma User Task)
- **Then** NENHUMA instancia atinge `End_ContratoRescindido` / `End_ContratoSuspenso` /
  `End_PedidoCancelamentoNegado` sem que a history do engine
  (`history/activity-instance`) contenha `UT_AnaliseRescisao` **ou**
  `UT_CoordenacaoCancelamento` **concluida por humano** com
  `decisao_cancelamento ∈ {RESCINDIR, SUSPENDER, MANTER}`. **A varredura completa nao
  produz nenhum terminal adverso.**

> Assercao central (history-based, sem mock do engine): para cada `historicProcessInstance`
> que termina em end-event adverso, existe na mesma history um `historicTaskInstance` de
> `UT_AnaliseRescisao`/`UT_CoordenacaoCancelamento` com `endTime != null` e `assignee`
> humano. Co-ocorrencia obrigatoria.

### test_rescindir_exige_fundamentacao_e_notificacao
- **Given** `UT_AnaliseRescisao` aberta (caminho `SEGUE_ANALISE`)
- **When** completa com `decisao_cancelamento=RESCINDIR` **sem** `fundamentacao_contratual`
  / `referencia_regulatoria` / `comprovacao_notificacao_previa`
- **Then** a task **NAO** completa (validacao de formulario/listener) — rescisao sem
  fundamentacao e comprovacao de notificacao previa e impossivel. Idem para `SUSPENDER`.

### test_inadimplencia_aparente_roteia_para_humano_nao_rescinde
- **Given** start com `tipo_solicitacao=inadimplencia`, `meses_inadimplencia=3`,
  `notificacao_previa_feita=true`
- **Then** DMN `cancel_admissibility` retorna `SEGUE_ANALISE` (NUNCA `RESCINDIR`);
  fluxo chega a `UT_AnaliseRescisao` (`juridico-contratos`), nao a `End_ContratoRescindido`
  nem `End_ContratoSuspenso`. Inadimplencia, por si so, nunca rescinde/suspende por automacao.

### test_inelegibilidade_pedido_roteia_para_humano_nunca_auto_nega
- **Given** start com `tipo_solicitacao=pedido_beneficiario`,
  `titularidade_confirmada=false` (titularidade nao comprovada)
- **Then** DMN `cancel_admissibility` retorna `ANALISE_HUMANA` (catch-all conservador);
  fluxo chega a `UT_AnaliseRescisao`, **nunca** a `End_PedidoCancelamentoNegado`
  automaticamente. A negativa do pedido so existe via decisao humana `MANTER`.

### test_plano_coletivo_pedido_roteia_para_humano
- **Given** `tipo_solicitacao=pedido_beneficiario`, `tipo_plano=coletivo_empresarial`,
  `titularidade_confirmada=true`
- **Then** DMN retorna `ANALISE_HUMANA` (rescisao de coletivo cabe ao estipulante);
  **nunca** `EFETIVAR_PEDIDO` automatico. Chega a `UT_AnaliseRescisao`.

### test_fraude_referida_nunca_auto_flag
- **Given** `tipo_solicitacao=fraude_referida`
- **Then** DMN retorna `ANALISE_HUMANA`; nenhum caminho automatizado registra acusacao de
  fraude (`fraud_accusation` L0 hard); fluxo aguarda decisao humana. (Encaminhamento a
  SP-OP-FRAUDE-001 e Phase 3 — fora de escopo deste processo.)

## Worker-guard (D3 — teste unitario do efeito adverso)

### test_send_cancellation_notice_recusa_sem_humano
- **Given** invocacao de `operadora.cancel.send_cancellation_notice` com variaveis SEM
  `decisao_cancelamento=RESCINDIR`/`SUSPENDER` ou SEM `responsavel_id`
- **Then** o worker lanca/sinaliza `ERR_CANCELLATION_NOT_HUMAN` e NAO emite a notificacao;
  registra a tentativa. (Unidade — nao requer engine; complementa o teste de invariante de
  integracao.)

### test_send_cancellation_notice_recusa_sem_campos_obrigatorios
- **Given** `decisao_cancelamento=RESCINDIR` + `responsavel_id` presentes, mas
  `fundamentacao_contratual`/`referencia_regulatoria`/`comprovacao_notificacao_previa`
  ausentes
- **Then** o worker recusa com `ERR_CANCELLATION_NOT_HUMAN` (defesa em profundidade — a
  validacao da User Task e a primeira barreira; o worker e a ultima).

### test_send_cancellation_notice_carrega_responsavel_id
- **Given** decisao humana valida e completa
- **Then** o worker emite a notificacao e propaga `responsavel_id` para a cadeia de
  auditoria (`agents.audit` / ADR-0007).

### test_confirm_maintained_decision_recusa_sem_humano (GAP-CANCEL-3)
- **Given** invocacao de `operadora.cancel.confirm_maintained_decision` com variaveis SEM
  `decisao_cancelamento=MANTER` (ausente, vazio, ou qualquer outro valor — inclusive o
  caso em que a instancia caiu no flow **default** de `GW_DecisaoCancelamento` sem
  `decisao_cancelamento` reconhecido)
- **Then** o worker lanca/sinaliza `ERR_CANCEL_MANTER_NOT_HUMAN` e NAO confirma a
  manutencao; registra a tentativa. (Unidade — nao requer engine; complementa o teste de
  invariante de integracao.)

### test_confirm_maintained_decision_recusa_sem_fundamentacao
- **Given** `decisao_cancelamento=MANTER` presente, mas `fundamentacao_contratual`
  ausente/vazia
- **Then** o worker recusa com `ERR_CANCEL_MANTER_NOT_HUMAN` (defesa em profundidade — a
  validacao da User Task e a primeira barreira; o worker e a ultima).

### test_confirm_maintained_decision_completa_com_fundamentacao
- **Given** `decisao_cancelamento=MANTER` + `fundamentacao_contratual` presentes
- **Then** o worker confirma a manutencao (`maintained_decision_confirmed=True`), propaga
  `fundamentacao_provided=True` (confirmacao de presenca — SEM ecoar o texto da
  fundamentacao, mesma cautela de `send_cancellation_notice`) e `responsavel_id` (quando
  presente) para a cadeia de auditoria; nunca retorna `decisao_cancelamento` (no-denial,
  ADR-0018).

### test_manter_sem_fundamentacao_bloqueado_pelo_guard (integracao — engine real)
- **Given** `UT_AnaliseRescisao` completada via `/task/{id}/complete` (bypass direto-REST,
  sem validacao de formulario) com `decisao_cancelamento=MANTER` mas SEM
  `fundamentacao_contratual`
- **Then** o worker lanca `ERR_CANCEL_MANTER_NOT_HUMAN`; o boundary `BE_ManterNaoConfirmado`
  CAPTURA o erro -> `End_ManterNaoConfirmado` (terminal NEUTRO — nada registrado). A
  instancia NAO morre silenciosamente NEM trava em incidente (lista de incidentes vazia);
  `End_ContratoMantido`/`End_PedidoCancelamentoNegado` NAO sao atingidos; nenhuma
  confirmacao `cancel.confirm_maintained_decision` e publicada. (Espelha GAP-AUTH-2 /
  `End_FundamentacaoIncompletaBloqueada`.)

## Happy paths

### test_happy_path_cancelamento_a_pedido_beneficiario_l2
- **Given** `tipo_solicitacao=pedido_beneficiario`, `tipo_plano=individual`,
  `titularidade_confirmada=true`, `dentro_prazo=true`
- **When** instancia percorre
- **Then** DMN `cancel_admissibility` retorna `EFETIVAR_PEDIDO`;
  `operadora.cancel.effectuate_member_request` executado; `cancel.completed` com
  `desfecho=cancelado_beneficiario`; fim `End_CanceladoBeneficiario`; **NENHUMA User Task
  adversa criada** (direito do titular). `operadora.cancel.send_cancellation_notice` NUNCA
  invocado.

### test_happy_path_rescisao_pela_operadora_humano
- **Given** `tipo_solicitacao=inadimplencia`, `notificacao_previa_feita=true` ->
  `SEGUE_ANALISE`; `UT_AnaliseRescisao` aberta para `juridico-contratos`
- **When** humano completa `decisao_cancelamento=RESCINDIR` com `fundamentacao_contratual`,
  `referencia_regulatoria` (RN 593 — DRAFT), `comprovacao_notificacao_previa`,
  `responsavel_id`
- **Then** `operadora.cancel.send_cancellation_notice` executado (guard satisfeito);
  `cancel.completed` `desfecho=rescindido_operadora`; fim `End_ContratoRescindido`.

### test_happy_path_suspensao_por_inadimplencia_humano
- **Given** `tipo_solicitacao=inadimplencia` -> `SEGUE_ANALISE`; analise aberta
- **When** humano completa `decisao_cancelamento=SUSPENDER` com campos obrigatorios
- **Then** notificacao de suspensao emitida (guard satisfeito);
  `cancel.completed` `desfecho=suspenso`; fim `End_ContratoSuspenso`.

### test_happy_path_pedido_negado_humano
- **Given** `pedido_beneficiario` + `titularidade_confirmada=false` -> `ANALISE_HUMANA`;
  analise aberta
- **When** humano completa `decisao_cancelamento=MANTER` com `fundamentacao_contratual`
- **Then** `operadora.cancel.confirm_maintained_decision` executado (guard satisfeito,
  GAP-CANCEL-3); `cancel.completed` `desfecho=pedido_negado`; fim
  `End_PedidoCancelamentoNegado`. (A negativa do pedido SO existe por esta via.)

### test_happy_path_contrato_mantido
- **Given** analise aberta
- **When** humano completa `decisao_cancelamento=MANTER` (ex.: purga da inadimplencia)
- **Then** `operadora.cancel.confirm_maintained_decision` executado (guard satisfeito,
  GAP-CANCEL-3); `cancel.completed` `desfecho=mantido`; fim `End_ContratoMantido`.

## Notificacao previa (event gateway — INVERTE auto-rescisao por timeout)

### test_notificacao_previa_pendente_publica_pended
- **Given** `tipo_solicitacao=for_cause_operadora`, `notificacao_previa_feita=false`
- **Then** DMN retorna `PENDENTE_NOTIFICACAO`; `operadora.cancel.request_notification`
  executado; `cancel.pended` publicado; instancia aguarda no event gateway de notificacao
  previa.

### test_notificacao_ack_destrava_analise
- **Given** aguardando no event gateway de notificacao previa
- **When** message `msg.cancel.notification_ack` correlacionada (business key)
- **Then** fluxo segue para `UT_AnaliseRescisao` (humano decide) — **nunca** rescinde
  automaticamente por recebimento do ack.

### test_prazo_notificacao_expira_vai_para_humano_nao_rescinde
- **Given** aguardando no event gateway, `notificacao_previa_feita=false`
- **When** job do timer `${cancel_sla.prazo_notificacao_previa}` executado (sem ack)
- **Then** `UT_AnaliseRescisao` criada para `juridico-contratos` — a expiracao do prazo
  **NUNCA** alcanca `End_ContratoRescindido`/`End_ContratoSuspenso` automaticamente
  (INVERTE o anti-pattern de auto-rescindir-no-timeout do reference).

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnaliseRescisao` aberta (`sla.sla_alerta` ~60-70% do SLA)
- **When** job do timer de alerta executado
- **Then** `operadora.cancel.notify_sla_risk` recebeu task; a User Task segue aberta;
  nenhum desfecho adverso produzido.

### test_timer_sla_estourado_coordenacao_assume
- **Given** analise aberta alem de `${cancel_sla.sla_analise}`
- **When** job do timer interruptivo executado
- **Then** `agents.events.cancel.sla_breached` publicado; `UT_AnaliseRescisao` cancelada;
  `UT_CoordenacaoCancelamento` criada (`coordenacao-contratos`) — decisao continua humana,
  com os mesmos campos obrigatorios.

### test_dmn_cancel_sla_registra_fonte
- **Given/When** start com `tipo_solicitacao=inadimplencia`
- **Then** `cancel_sla.fonte_regulatoria` preenchida (ex.: "RN 593 — DRAFT/verify") e
  propagada para auditoria; `sla_analise`/`sla_alerta`/`prazo_notificacao_previa` em string
  ISO 8601.

## Pendencia de informacao (humano solicita)

### test_solicitar_info_aguarda_correlacao
- **Given** `UT_AnaliseRescisao` aberta
- **When** humano completa `decisao_cancelamento=SOLICITAR_INFO`
- **Then** instancia aguarda `msg.cancel.info_received`; ao correlacionar (business key),
  reabre `UT_AnaliseRescisao` para nova decisao humana.

## Idempotencia

### test_business_key_uma_instancia_por_contrato
- **Given** instancia ativa `CANCEL-amh-CONTRATO-TESTE-0001`
- **When** reenvio da mesma solicitacao de cancelamento do mesmo contrato
- **Then** sem segunda instancia ativa (`start_process` consulta a business key e retorna a
  existente).

## Eventos de dominio (auditoria dupla — engine + Kafka, ADR-0007)

### test_todo_fim_publica_evento_antes
- **Given** qualquer caminho que atinge um end-event
- **Then** `agents.events.cancel.completed` publicado com `payload.desfecho` correspondente
  ANTES do fim (auditoria dupla); para os tres terminais adversos, o payload carrega
  `responsavel_id`.
