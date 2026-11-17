# Contrato — SP-OP-ARREPENDIMENTO-001 (Rescisao por arrependimento)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (nome do processo proposto, PROVISORIO)

> Processo **sem BPMN** nesta versao: nenhum topico registrado, nenhum intake vivo, nenhuma User Task. Entrega a variante executavel, com portao fechado por construcao: validar, classificar e encaminhar (`route_to_human`). O BPMN entra junto com o intake real (GAP-09-3).

## 1. Gatilho regulatorio

Texto so' do que o pacote de decisoes transcreveu; o que nao esta transcrito e GAP.

- CDC art. 49 (prazo de 7 dias a contar da assinatura): o texto verbatim do pacote esta transcrito no cabecalho de `spec/policies/saida-a-pedido/janela-arrependimento.yaml`, nao duplicado aqui.
- RN 413 art. 8º: "[...] no prazo de 7 (sete) dias de duração a partir da data de vigência do contrato." O §1º vem truncado com "[...]" no pacote (GAP-09-2); o §2º segue a transcricao do pacote.
- RN 413 art. 6º: a vigencia coincide com o pagamento (ancora `vigencia_start`; Q9 pendente).
- A RN 561 nao trata arrependimento; o pedido de cancelamento a pedido segue o processo distinto de §4.
- Portoes: RL-REG-03, RL-REG-19 e TB-17 (RATIFY-LATER-juridico). A contagem do prazo (corrida/util, inclusiva) e Q3 e **nao** esta decidida.

## 2. Invariante L0 hard

`contract_termination` e item hard-frozen (`spec/policies/autonomy/_hard_frozen.yaml`; `HARD_ACTIONS` em `src/maezo/gateway/pep.py`). Nenhum agente efetiva a rescisao. O ponto unico de recusa e `saida_a_pedido.arrependimento.recusar_efetivacao_por_agente`, que sempre levanta e consulta o PEP real.

## 3. Politica da janela (EP-REG-03a)

Arquivo: `spec/policies/saida-a-pedido/janela-arrependimento.yaml`. O prazo e **dado**, nunca Python.

- Default entregue: `status: NAO_RATIFICADO`, `variante_ativa: UNRATIFIED_UNION`, `display_numeric_promise: false`, `fora_da_janela: route_to_human`.
- Variantes A, B, UNRATIFIED_UNION e D mudam so' por dado. Qualquer variante diferente do default, promessa numerica ou texto de declaracao ratificado exige `status: RATIFICADO` e bloco `ratificacao.juridico` completo (ato humano em PR).
- O leitor recusa `fora_da_janela` diferente de `route_to_human` (`AUTO_REFUSAL_FORBIDDEN_PENDING_Q3`), chave desconhecida (`UNKNOWN_KEY`) e duracao fora de `P<n>D`.
- A union entregue e **contigua** (da assinatura ate o ultimo termo final): superconjunto da uniao de dois intervalos do TB-17. Sem efeito de recusa, porque nada se recusa por prazo.

## 4. Distincao do SP-OP-CANCEL-001

- Fluxo distinto: o pedido de cancelamento nao carrega declaracao de arrependimento (campo desconhecido) e o aviso de arrependimento no cancelamento e so um link.
- A confirmacao unica do cancelamento ordinario ("Responda Sim para confirmar.", `src/maezo/agents/lucas/graph.py`) **nao** e a declaracao do art. 8º §1º (`CANCEL_CONFIRMATION_NOT_WITHDRAWAL_DECLARATION`).
- O back-office do cancelamento ordinario segue sendo o `SP-OP-CANCEL-001` (secao "Pedido pela pagina e fluxo distinto do arrependimento").

## 5. Ancoras

- `signed_at`: receipt de aceite (A3/REG-16 nao admitido; hoje ausente).
- `vigencia_start`: receipt de pagamento/efetivacao (Q9).
- Origem **so' de fonte**. Ancora vinda do navegador ou do payload e recusada (`anchor_unauthenticated`). Ancora exigida ausente deixa a janela `INDETERMINADA`, sempre encaminhada a humano (fail-closed; GAP-09-5).

## 6. Intake

- Payload de conjunto fechado: `pedido_ref`, `contrato_ref`, `abertura_ref`, `envio_ref`, `assinatura_ref` e `declaracao`. Qualquer outra chave (uso do plano, procedimentos, motivo, texto livre) e `unknown_field`.
- Codigos de recusa do formulario (`CODIGOS_DE_RECUSA_DO_FORMULARIO`): `declaration_required`, `DECLARATION_FUSED_WITH_CLICK`, `CANCEL_CONFIRMATION_NOT_WITHDRAWAL_DECLARATION`, `anchor_unauthenticated`, `unknown_field`. Nenhum fala de prazo ou janela: **nao ha recusa por prazo**.
- Saida: sempre `route_to_human`, com `desfecho` nulo. Protocolo `ARR-XXXX-XXXX`, estavel por `pedido_ref`, emitido no recebimento. `recebido_em` vem do relogio de servidor (GAP-09-4).

## 7. Desfecho

So' humano. User Task e grupo humano: GAP-09-3, para a versao com BPMN.

## 8. Estorno

`ESTORNO_CONCLUIDO` so' com estado de fonte `completed` **e** dois receipts de fonte distintos (restituicao e ack Tasy). O fio comercial atual carrega so' `source_receipt_ref`, nunca o ack Tasy: o caminho positivo fica **PARADO** ate um campo novo no sucessor do contrato C6-4 (GAP-09-8).

## 9. Portoes fechados

| Portao | O que fica fechado | Como o teste afirma |
|---|---|---|
| RL-REG-03 / TB-17 (Q3) | variante diferente da union; promessa numerica | `test_variant_switch_is_data_only`, `test_numeric_promise_hidden_until_ratified`, `test_portoes_fechados_no_estado_entregue`, `test_withdrawal_never_auto_refused_on_window` |
| RL-REG-19 | texto da declaracao; efetivacao | `test_portoes_fechados_no_estado_entregue`, `test_contract_termination_never_autonomous`, `test_agent_cannot_terminate` |
| RL-REG-27 / RT-J2 | norma que rege; texto do art. 15; teto do art. 18 | `test_art15_text_not_publishable_until_reg27`, `test_no_regulatory_deadline_until_reg27` |
| RT-J3 (a) | linha unica de alternativa (OS-08) | `test_os08_alternative_line_absent_in_executable_variant` |
| RL-REG-16 (A3) | ancora `signed_at` | `test_withdrawal_anchor_requires_source_receipt` |
| `vinculo_contratual_mudanca` | efetivacao por topico | `test_contract_termination_never_autonomous` |

## 10. Pendencias para promocao

BPMN e grupo humano; texto da declaracao e forma de assinatura (art. 7º); Q3; Q9; carregamento empacotado de `spec/policies/saida-a-pedido/` no wheel (GAP-09-6); vocabulario do fio comercial (sucessor do C6-4); resposta automatica + protocolo do pedido de cancelamento pela pagina (GAP-09-9; REUSE do `CAN-XXXX-XXXX` do SP-OP-CANCEL-001).

## 11. Testes de guarda

`tests/unit/processes/test_withdrawal_window_policy.py`, `test_arrependimento.py`, `test_cancel_request_rn561.py`, `test_e_sf_09_fronteira.py` e `tests/unit/commercial/test_e_sf_09_refund_receipts.py`.
