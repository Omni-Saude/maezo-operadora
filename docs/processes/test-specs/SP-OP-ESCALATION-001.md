# Test spec — SP-OP-ESCALATION-001

Stubs de integracao (pytest, marker `integration`) contra CIB Seven REAL (CONTRIBUTING §3
— sem mock de engine). Arquivo alvo sugerido: `tests/integration/processes/test_sp_op_escalation_001.py`.
Timers: usar job execution via API de gerenciamento (executar job do timer), nunca sleep.
Dados sempre sinteticos (`Beneficiario Teste 001`, tenant `amh`).

## Convencoes de fixture

- `engine`: cliente REST CIB Seven do dev-stack (`make dev-stack`).
- `deploy_artifacts`: deploya o BPMN + `escalation_routing.dmn` da arvore.
- `start_escalation(**overrides)`: inicia com business key `ESC-amh-{conversation_id}` e payload canonico
  (helena, conversa `conv-teste-001`, `beneficiario_pseudo_id="PSEUDO-TESTE-001"`).
- `kafka_probe(topic)`: consumidor de teste; o worker generico `operadora.events.publish` roda no dev-stack.

## Happy paths

### test_happy_path_resolvido_por_humano
- **Given** instancia iniciada com `motivo_categoria=red_flag_clinico`, `severidade=grave`
- **When** worker de notificacao completa; usuario do grupo `plantao-clinico` (DMN: P1) completa `UT_TratarEscalonamento` com `resultado=resolvido_humano`
- **Then** evento `escalation.requested` publicado no inicio; `escalation.resolved` com `payload.resultado=resolvido_humano`; instancia termina em `End_ResolvidoPorHumano`; NENHUM evento `process_completed` publicado

### test_happy_path_devolvido_ao_agente
- **Given** instancia com `motivo_categoria=solicitacao_humano`, `severidade=leve`
- **When** humano de `atendimento-humano` completa com `resultado=devolvido_agente`, `notas_resolucao="instrucoes do humano"`
- **Then** `escalation.resolved` publicado E `agents.events.process_completed` publicado com `conversation_id`, `agent_id` e `_business_key` (SEM `notas_resolucao` — Zona PHI, GAP-ESC-1); fim em `End_DevolvidoAoAgente`

### test_happy_path_emergencia_acionada
- **Given** instancia P1 (risco_psicossocial)
- **When** humano completa com `resultado=emergencia_acionada`
- **Then** `escalation.resolved` com `payload.resultado=emergencia_acionada`; fim em `End_ResolvidoPorHumano`

## Roteamento DMN

### test_dmn_routing_p1_plantao_clinico
- **Given/When** start com `red_flag_clinico`+`grave`
- **Then** `UT_TratarEscalonamento` tem candidate group `plantao-clinico`; `roteamento.sla_ack == "PT5M"`

### test_dmn_routing_catchall_fail_safe
- **Given/When** start com `motivo_categoria=categoria_inexistente`
- **Then** roteado P2/`atendimento-humano` (nunca P3) — regra catch-all

## Timers

### test_timer_ack_nao_interruptivo_alerta_supervisor
- **Given** instancia P1 com `UT_TratarEscalonamento` aberta e nao completada
- **When** job do timer `BT_SlaAck` (PT5M) executado
- **Then** `escalation.sla_breached` publicado com `fase=ack`; worker `operadora.escalation.notify_supervisor` recebeu task; **a User Task continua aberta** (nao-interruptivo); ramo termina em `End_SupervisorAlertado`

### test_timer_resolucao_interruptivo_supervisor_assume
- **Given** instancia P1 nao tratada
- **When** job do timer `BT_SlaResolucao` (PT30M) executado
- **Then** `escalation.sla_breached` publicado com `fase=resolucao`; `UT_TratarEscalonamento` CANCELADA; `UT_SupervisorAssume` criada com candidate group `supervisao-atendimento`

### test_supervisor_resolve_apos_breach
- **Given** `UT_SupervisorAssume` aberta (apos breach)
- **When** supervisor completa com `resultado=resolvido_humano`
- **Then** `escalation.resolved` publicado; fim em `End_ResolvidoPorHumano`

## Escalation/erros

### test_falha_notificacao_usa_fallback
- **Given** worker `notify_team` configurado para lancar BPMN error `ERR_ESC_NOTIFY_FAILED`
- **When** instancia inicia
- **Then** `ST_NotificarFallback` (topic `notify_supervisor`) executa e `UT_TratarEscalonamento` e criada mesmo assim (escalonamento nunca se perde por falha de canal)

## Idempotencia

### test_business_key_idempotente
- **Given** instancia ativa `ESC-amh-conv-teste-001`
- **When** segundo start com a mesma business key (via mcp-cibseven.start_process)
- **Then** nenhuma segunda instancia ativa; resposta referencia a instancia existente

## Auditoria

### test_todos_os_fins_emitem_evento_de_dominio
- **Given** os tres caminhos de fim (resolvido, devolvido, supervisor alertado)
- **When** cada um e percorrido
- **Then** ha evento Kafka correspondente publicado ANTES do end event (sem fim silencioso)

## Inbox duravel de `notify_team` (WP-J1-09 — ADR-0050, ADR-0037 XRD-10)

### test_notify_team_grava_linha_de_inbox_antes_do_offset
- **Given** uma instancia de `SP-OP-ESCALATION-001` cujo `ST_NotificarTime` completou, com o
  daemon `maezo.platform.integrations.notifications_inbox` consumindo
  `operadora.notifications.internal` no seu PROPRIO grupo de consumo
- **When** a notificacao `type=escalation.notify_team` e entregue
- **Then** existe UMA linha em `escalation_team_notice` com o `grupo_atendimento` que a DMN
  escolheu (o MESMO valor do `candidateGroups` de `UT_TratarEscalonamento`), `audience='staff'` e
  o `business_key` da instancia
- **And** o offset do consumidor so avanca DEPOIS dessa linha — entrega e a linha commitada mais
  o recibo, nunca o offset (ADR-0037 XRD-10)

### test_notify_team_reentregue_nao_duplica_a_linha
- **Given** a mesma notificacao entregue duas vezes (redelivery do broker)
- **Then** UMA unica linha (PK `(tenant, notice_ref)`, com `notice_ref` derivado do conteudo) e o
  segundo recibo relata `duplicate`, nunca `recorded`

### test_notify_team_sem_banco_nao_acka
- **Given** o banco indisponivel quando a notificacao chega
- **Then** o consumidor NAO commita o offset e a mensagem e reentregue — um aviso de
  escalonamento nunca e perdido por um ack sem linha durável

