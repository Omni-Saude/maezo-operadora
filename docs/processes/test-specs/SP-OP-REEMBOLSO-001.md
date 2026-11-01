# Test spec — SP-OP-REEMBOLSO-001 v0.2.0 (DRAFT — acompanha o processo)

Testes de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina "nunca mockar o engine"; sem
a stack docker o teste fica marcado e o CI o roda). Arquivo: `tests/integration/processes/test_sp_op_reembolso_001.py`.
Unitarios (worker + cercas estruturais que leem o BPMN/DMN): `tests/unit/tools/workers/test_reembolso.py`.

**v0.2.0 (DL-0161): 100% analise humana.** Nao ha calculo, tabela, teto, aprovacao automatica nem pagamento emitido pelo
sistema; o financeiro paga fora e registra `data_pagamento`. Os testes de calculo, de teto D-07, do bloqueio R-058 e da
aprovacao automatica L2 da v0.1.0 **foram removidos** com o caminho que cobriam — e ha testes que provam que ele nao voltou.

Dados sinteticos: protocolo `REEMB-TESTE-NNNN`, beneficiario pseudonimizado `benef-TESTE-001`, matricula `MAT-TESTE-001`,
tenant `amh`. Business key `REEMB-amh-{protocolo}`. Valores em **centavos inteiros** (`valor_solicitado_cents=12000` = R$120,00).
`aviso_beneficiario_whatsapp=true`, `agent_id=lucas` e `conversation_id` simulam uma instancia aberta pelo Lucas.

## Invariante L0 e analise humana

| Teste | Garante |
|---|---|
| `test_todo_pedido_vai_para_a_analise_humana` | Mesmo um pedido "perfeito" (valor baixo, urgencia) para em `UT_AnaliseReembolso`; nenhum terminal de decisao antes; `BRT_Calculo`/`ST_CalculateAmount`/`BRT_AutoApproval`/`ST_IssuePaymentAuto` nao existem no historico; sem incidente; `prazo.situacao_prazo == NAO_AVALIADO` |
| `test_dias_desde_atendimento_e_resolvido_pelo_worker` | `check_coverage` resolve `dias_desde_atendimento` (aritmetica de datas, sem regra) |
| `test_decisao_desconhecida_nao_tem_caminho` | `GW_DecisaoReembolso` nao tem default: uma decisao desconhecida nao completa a tarefa e nao vira aprovacao |
| `test_negar_sem_texto_ao_beneficiario_vira_incidente` | O guard `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` recusa a negativa sem `texto_ao_beneficiario`: incidente, nenhum aviso, terminal de negativa nao atingido |
| Unitario `test_every_path_to_a_terminal_crosses_a_human_task` | Nao ha caminho de Start a um terminal de decisao sem User Task humana (le o BPMN) |

## Desfechos humanos

| Teste | Garante |
|---|---|
| `test_aprovado_pelo_analista_passa_pelo_pagamento_humano` | `APROVAR` → aviso `reembolso_aprovado` → `UT_RegistrarPagamento` (grupo `financeiro-reembolso`, `data_pagamento`) → aviso `reembolso_pago` → `End_ReembolsoAprovadoAnalista`; desfecho `aprovado_analista` |
| `test_aprovado_parcial_comunica_a_reducao_e_depois_paga` | `APROVAR_PARCIAL` → `ST_ComunicarReducao` (guard) → aviso `reembolso_parcial` → pagamento humano → `End_ReembolsoParcial` |
| `test_negado_pelo_analista` | `NEGAR` → aviso `reembolso_negado` → `End_ReembolsoNegado`; sem pagamento; a negativa so nasce na User Task |
| `test_escalar_para_o_medico_auditor` | `SOLICITAR_AUDITOR` (botao "escalar") → `UT_RevisaoAuditorMedico` (`medico-auditor`) → decide |
| `test_sem_a_flag_do_lucas_nenhum_aviso_e_publicado` | Sem `aviso_beneficiario_whatsapp`, nenhum `process_completed` sai; os fatos de dominio saem |

## Pedir mais informacoes e pendencia

| Teste | Garante |
|---|---|
| `test_solicitar_info_avisa_aguarda_e_volta_para_a_analise` | `SOLICITAR_INFO` (+ `info_solicitada`) → aviso `reembolso_info_solicitada` (o **texto** da pergunta nao viaja no evento) → `reembolso.pended` → `msg.reembolso.docs_received` volta **direto** a `UT_AnaliseReembolso` (2a instancia da tarefa) |
| `test_pendencia_expira_decisao_humana_nunca_auto_nega` | Timer `ICE_PrazoPendencia` (P5D) → `UT_DecidirPendenciaExpirada`; `cancelar_solicitacao` → `End_ReembolsoCancelado`; nunca negativa |
| `test_conceder_prazo_extra_volta_a_aguardar` | `conceder_prazo_extra` rearma o timer de pendencia |

## Resposta da beneficiaria e conclusao pelo portal (DL-0163)

| Teste | Garante |
|---|---|
| `test_resposta_da_beneficiaria_pelo_lucas_volta_a_analise_com_texto_e_arquivos` | Com o turno REAL do Lucas e o transporte HTTP real: sem pendencia a mensagem nao e' resposta; `SOLICITAR_INFO` marca `aguardando_resposta=true`; texto + arquivo + TERMINEI fazem UMA correlacao que volta a `UT_AnaliseReembolso` com `resposta_beneficiario` (sem o CPF digitado) e `documentos_adicionais_refs`, e a espera termina |
| `test_conclusao_tipada_do_portal_passa_pela_guarda_l0` | O corpo da conclusao interina do portal (valor `Long`, textos, `analista_id` do servidor) passa pela guarda L0 de `send_reembolso_denial` e leva o parcial ao pagamento humano, sem incidente |

## SLA

| Teste | Garante |
|---|---|
| `test_timer_alerta_sla_nao_interruptivo` | `BT_AlertaSla` aciona `ST_NotificarRiscoSla`; a User Task segue aberta |
| `test_timer_sla_estourado_coordenacao_assume_e_nao_auto_nega` | `BT_SlaAnalise` (interruptivo) cancela a UT do analista, publica `sla_breached` e abre `UT_CoordenacaoReembolso` (`coordenacao-reembolso`); estourar o SLA nunca decide; a coordenacao pode negar |

## Validacao de origem e idempotencia

| Teste | Garante |
|---|---|
| `test_protocolo_invalido_atinge_o_terminal_neutro` | `protocolo_reembolso` em branco → `BE_ReembolsoProtocoloInvalido` → `End_ReembolsoProtocoloInvalido`, sem incidente e sem decisao |
| `test_business_key_uma_instancia_por_protocolo` | Uma instancia por protocolo (`REEMB-amh-{protocolo}`) |

## Cercas unitarias (arquivo `test_reembolso.py`)

- Os topicos registrados sao **exatamente** os topicos `operadora.reembolso.*` do BPMN + `publish_completed` (convencao); `calculate_amount`, `issue_payment` e `check_prazo` nao voltam.
- O modulo nao tem mais `calculate_value`, `process_payment`, `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO` etc.
- Os elementos do caminho automatico nao existem no BPMN; o pagamento e `userTask` do grupo `financeiro-reembolso`.
- Todo aviso e `agents.events.process_completed` com so `tenant_id,agent_id,conversation_id,resultado`; os cinco `resultado` esperados.
- `reembolso_prazo.dmn` so tem o catch-all `NAO_AVALIADO`; `reembolso_calculo.dmn` e `reembolso_coverage.dmn` nao existem.

## Fora desta suite

- Coleta de documentos (foto/PDF), perguntas do Lucas e os avisos ao WhatsApp: testes do agente Lucas e da retomada (PR 3).
- Formularios do portal (`reembolso_decisao` com `info_solicitada`/`texto_ao_beneficiario`, `reembolso_pagamento`): `tests/unit/portal` (PR 2).
