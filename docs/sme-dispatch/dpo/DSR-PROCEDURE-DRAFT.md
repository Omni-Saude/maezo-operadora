# Procedimento de atendimento de DSR (LGPD art. 18) — RASCUNHO de runbook (F-2)

> **rascunho — pendente de designação e assinatura do encarregado (LGPD art. 41)**
>
> **recomendação — pendente de assinatura de DPO**
>
> Este documento **não** é o estado declarado da operadora. Ele vira o estado declarado quando o
> encarregado o assinar e promovê-lo a `docs/compliance/dsr-procedimento-manual.md` (R-029). Até
> lá é rascunho: nenhum passo abaixo está autorizado a rodar.

**Ratificado por (DPO):** ______________________  **data:** ______________________

---

## 1. A forma escolhida: runbook que ABRE A INSTÂNCIA REAL

Decisão aprovada do dono (R-029): o procedimento **não** é um registro manual em planilha. É um
runbook que abre a instância real de `SP-OP-LGPD-DSR-001` no motor, de modo que:

- o prazo legal de **15 dias** corra em `ESP_SlaGlobal` (event subprocess não-interruptivo,
  ancorado no **início da instância**, LGPD art. 19, II) e não na cabeça de alguém;
- o gate de identidade **fail-closed** de `src/maezo/tools/workers/lgpd.py::ValidateIdentityWorker`
  seja executado pelo sistema;
- apenas os passos **sem worker** — `compile_data_package` e `execute_request` — permaneçam
  manuais, nomeados como tal (§4).

---

## 2. O que existe hoje, verificado por símbolo

### 2.1 Tópicos servidos (o sistema faz)

| Tópico BPMN | Símbolo em `src/maezo/tools/workers/lgpd.py` | O que faz |
|---|---|---|
| `operadora.lgpd.verify_identity` | `ValidateIdentityWorker` (`topic=` em `lgpd.py:97`) | Fail-closed: confirma só com `identidade_verificada is True`; `titular_pseudo_id` ausente/vazio ⇒ `WorkerBpmnError("ERR_DSR_IDENTITY_UNVERIFIED")` — guarda **técnico**, nunca acusação de fraude |
| `operadora.lgpd.request_additional_proof` | `make_request_additional_proof_handler` (`_REQUEST_PROOF_TOPIC`) | Pede prova adicional; alimenta `GW_AguardarProva` |
| `operadora.lgpd.send_response` | `make_send_response_handler` (`_SEND_RESPONSE_TOPIC`) | Envia a resposta/negativa ao titular |
| `operadora.lgpd.notify_sla_risk` | `make_notify_sla_risk_handler` (`_NOTIFY_SLA_RISK_TOPIC`) | Serve **duas** tarefas pelo discriminador `sla_breach_phase` (`ack` em `ST_NotificarRiscoSla`, `resolution` em `ST_NotificarJuridicoBreach`) |
| `operadora.events.publish` | publicador genérico | `lgpd_dsr.received` / `.completed` / `.sla_breached` |

Registro: `register_lgpd_workers` (`lgpd.py:754`).

### 2.2 Tópicos SEM worker (o humano faz)

O próprio comentário de bootstrap declara a lacuna: *"that BPMN's remaining 2 topics
(compile_data_package/execute_request) still have no worker — #55 R-C/R-D, DPO/SME-sign-off-gated"*
(`lgpd.py`, bloco imediatamente acima de `register_lgpd_workers`).

| Tópico | Tarefa BPMN | Consequência operacional |
|---|---|---|
| `operadora.lgpd.compile_data_package` | `ST_CompilarPacote` (`bpmn:181-182`) | A tarefa externa fica **pendente no motor** até que alguém a complete manualmente |
| `operadora.lgpd.execute_request` | `ST_ExecutarRequisicao` (`bpmn:270-271`) | Idem |

### 2.3 Guardas estruturais que o motor executa sozinho

- `GW_DecisaoDsr` só avança com um dos três valores explícitos de `decisao_dsr`
  (`APROVAR_ENVIO` | `EXECUTAR_E_ENVIAR` | `NEGAR_FUNDAMENTADO`); ausente/desconhecido roteia a
  `End_ErrDecisaoInvalida` (`ERR_DSR_DECISION_INVALID`) — **nunca libera dados por omissão**.
- `GW_GuardFundamentacao` barra `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal`
  (`End_ErrFundamentacaoAusente`, `ERR_DSR_FUNDAMENTACAO_AUSENTE`) — guard HITL avaliado pelo
  **engine**, não por validação de formulário.
- `BE_IdentidadeInverificavel` (boundary em `ST_VerificarIdentidade`) termina em
  `End_IdentidadeInverificavel`, terminal **neutro** (fail-safe, não adverso).

---

## 3. O runbook

### Passo 0 — Recepção (canal de entrada)

Canais aceitos pelo contrato (`canal`, obrigatório): `whatsapp` | `portal` | `email`.
Responsável: atendimento/privacidade. Registrar data de recebimento — ela **compõe a business
key** e é o marco do prazo.

### Passo 1 — Abrir a instância no motor

Chave de negócio (contrato `SP-OP-LGPD-DSR-001.md`, §"Business key"):

```
DSR-{tenant_id}-{titular_pseudo_id}-{tipo_requisicao}-{data_solicitacao_iso}
```

Variáveis de entrada obrigatórias:

| Variável | Valor |
|---|---|
| `tenant_id` | tenant da operadora |
| `titular_pseudo_id` | pseudônimo do titular (ADR-0006) — **nunca** CPF, nome ou matrícula crua |
| `canal` | `whatsapp` \| `portal` \| `email` |
| `tipo_requisicao` | `confirmacao_acesso` \| `correcao` \| `eliminacao` \| `portabilidade` \| `info_compartilhamento` \| `revogacao_consentimento` |
| `detalhes_requisicao` | texto da requisição, **pseudonimizado** |
| `data_solicitacao_iso` | `YYYY-MM-DD` |
| `envolve_dados_saude` | booleano |

> **Limitação declarada, não contornada.** Não existe hoje nenhum sítio em `src/` que inicie
> `SP-OP-LGPD-DSR-001` pelo chokepoint auditado `start_process_idempotent` — o próprio mapa de
> posturas declara: *"Workers exist (`tools/workers/lgpd.py`) but NO call site starts it through
> this chokepoint (`check_start_process_fence` lists 13 calling files; none passes this key)"*
> (`src/maezo/tools/mcp_cibseven/transport.py`, bloco acima de
> `"SP-OP-LGPD-DSR-001": StartDedupPosture.NON_STRICT`).
>
> Consequência que o encarregado precisa aceitar por escrito: um start feito **pela mão** (REST
> do motor / Cockpit) **não** emite a linha de proveniência ADR-0007 que o chokepoint emitiria, e
> **não** passa pelo `find_active_instance` idempotente. Duas requisições do mesmo titular, do
> mesmo tipo, no mesmo dia, abertas manualmente, podem gerar **duas** instâncias.
>
> Mitigação enquanto o sítio de start não existir: conferir a chave de negócio acima **antes** de
> abrir, e registrar a abertura no controle de atendimentos com o `processInstanceId` devolvido.

### Passo 2 — Verificação de identidade (o sistema faz)

`ST_VerificarIdentidade` roda `ValidateIdentityWorker`. Ele confirma **apenas** com o sinal
explícito `identidade_verificada is True`. Ausente/`False`/lixo ⇒ `identidade_confirmada=False` ⇒
sub-fluxo de desafio (`ST_PedirProvaAdicional` → `GW_AguardarProva`), com prazo interno **P10D**
(política interna, ainda **DRAFT/verify jurídico**) que termina em `expirada_identidade`.

**Quem semeia `identidade_verificada=True`, e por qual prova, é uma política de produtor ainda
pendente de ratificação do DPO** (ADR-0031 Decisão ponto 5). Enquanto ela não existir, o
procedimento é: a verificação é feita por pessoa, fora do sistema, e a pessoa registra o
resultado; a semântica fail-closed do consumidor não depende dessa ratificação.

### Passo 3 — Roteamento (o motor faz)

`BRT_RotearDsr` avalia a DMN `lgpd_dsr_routing` (FIRST, DRAFT) e produz `fluxo`
(`EXPORTACAO` | `RETIFICACAO` | `ELIMINACAO_AVALIACAO` | `INFORMATIVO`), `grupo_revisor`
(`dpo` | `juridico-privacidade`), `sla_resposta`, `sla_alerta`. Catch-all de tipo desconhecido:
`juridico-privacidade`.

### Passo 4 — **MANUAL**: compilar o pacote (`compile_data_package`)

Não há worker. A tarefa externa fica pendente no motor.

Responsável: `grupo_revisor` resolvido no passo 3. O que compilar depende da matriz de retenção —
que **não está ratificada** (AF-07). Enquanto não estiver, o pacote é montado com a orientação
provisória do dossiê candidato ([`RETENTION-MATRIX-CANDIDATE.md`](RETENTION-MATRIX-CANDIDATE.md)),
e **cada decisão de reter/eliminar por categoria é decisão humana registrada no próprio
atendimento**, não uma consulta a um artefato ratificado.

Ao concluir, completar a tarefa externa no motor devolvendo as variáveis que `UT_RevisaoDpo`
precisa.

### Passo 5 — Revisão humana (`UT_RevisaoDpo`)

Grupo: `dpo` (sem dados de saúde) ou `juridico-privacidade` (dados de saúde, eliminação, tipos
desconhecidos). Alerta interno em **P7D** na tarefa (`BT_AlertaDpo`, não-interruptivo) dispara
`notify_sla_risk` com `sla_breach_phase=ack`.

Saída obrigatória: `decisao_dsr` ∈ {`APROVAR_ENVIO`, `EXECUTAR_E_ENVIAR`, `NEGAR_FUNDAMENTADO`}.
Em `NEGAR_FUNDAMENTADO`, `fundamentacao_legal` é **obrigatória** — o engine barra o contrário.

### Passo 6 — **MANUAL**: executar (`execute_request`)

Não há worker. E, mais grave que a ausência do worker: **a eliminação real não é executável**.
`ErasureManager.erase()`/`.verify()` levantam `ErasureNotImplementedError` **sempre**
(`src/maezo/platform/erasure.py:152` e `:187`), e as duas pontes de identidade
(`titular_pseudo_id → fhir_patient_id`, `thread_id → fhir_patient_id`) não existem em lugar
nenhum da árvore.

Regra do procedimento, sem eufemismo:

- **`EXECUTAR_E_ENVIAR` com eliminação de dado pessoal em camada persistente NÃO PODE ser
  concluído como "executado".** O atendimento registra a decisão e a **impossibilidade técnica
  atual**, e o titular é informado do que foi e do que não foi feito. Afirmar uma eliminação que
  não ocorreu é violação silenciosa do art. 18 VI — é exatamente o que o
  `ErasureNotImplementedError` existe para impedir.
- Retificação de dado **cadastral/factual** pode ser executada no sistema de origem, fora desta
  cadeia, e registrada aqui.
- Retificação de **conteúdo de juízo clínico** não é matéria de DSR — encaminhar ao médico
  auditor (integridade de prontuário).

### Passo 7 — Resposta (o sistema faz)

`ST_EnviarResposta` roda `make_send_response_handler`. Depois, `ST_PublishCompleted` publica
`lgpd_dsr.completed` com `desfecho` = `atendida` | `negada_fundamentada` (ou
`expirada_identidade` / `identidade_inverificavel` nos ramos anteriores).

### Passo 8 — Prazo legal

`ESP_SlaGlobal` (P15D do início da instância) dispara `lgpd_dsr.sla_breached` +
`ST_NotificarJuridicoBreach` (`sla_breach_phase=resolution`). **Não interrompe** o caso — ele
segue aberto e o jurídico é alertado.

---

## 4. Os dois passos manuais, nomeados

| Passo | Tópico sem worker | Responsável | Registro |
|---|---|---|---|
| 4 | `operadora.lgpd.compile_data_package` (`ST_CompilarPacote`) | `grupo_revisor` do passo 3 | Completar a tarefa externa no motor + anexar o pacote ao atendimento |
| 6 | `operadora.lgpd.execute_request` (`ST_ExecutarRequisicao`) | `grupo_revisor` + operação do sistema de origem | Completar a tarefa externa + registrar **o que foi e o que não foi executado** |

---

## 5. O que este runbook NÃO habilita

1. **Nenhuma eliminação real.** `ErasureManager` continua fail-closed.
2. **Nenhuma alteração de código.** Em particular, o colapso dos três `ExecuteExportWorker` /
   `ExecuteRectificationWorker` / `ExecuteErasureWorker` em **um** worker no tópico modelado
   `operadora.lgpd.execute_request` (R-181) é um **PR de conformidade código↔modelo pendente**,
   **não feito aqui**. Hoje os três seguem registrados em tópicos que o BPMN não declara.
3. **Nenhum signoff.** `docs/processes/contracts/signoffs/SP-OP-LGPD-DSR-001.signoff.yaml`
   **não** foi criado por este rascunho e não pode sê-lo por um agente
   (`docs/sme-dispatch/README.md:109-111`, regra 5 do protocolo de redline).
4. **Nenhuma promoção do contrato.** `SP-OP-LGPD-DSR-001.md` continua `DRAFT (v0.1.0)`; suas
   pendências para FINAL (DI, validação jurídica dos prazos internos, matriz de retenção, fluxo
   de revogação com efeito imediato, interação com `SP-OP-CANCEL-001`) seguem abertas.

---

## 6. Riscos residuais a declarar na assinatura

| Risco | Origem verificada |
|---|---|
| Start manual sem proveniência ADR-0007 e sem idempotência | ausência de sítio de start pelo chokepoint (`transport.py`, bloco de `StartDedupPosture`) |
| Eliminação prometida e não executada | `erasure.py:152`/`:187` |
| Pacote compilado sem matriz ratificada | AF-07 (`load_retention_matrix` recusa) |
| Prazos internos P7D/P10D ainda `DRAFT/verify` | contrato §SLAs |
| Política de produtor de `identidade_verificada` não ratificada | ADR-0031 Decisão ponto 5 |
| Três workers registrados fora do tópico modelado | R-181, PR pendente |

---

## 7. Rastreabilidade

- Decisões do dono: **R-029** (forma = runbook que abre a instância real), **R-055** (os cinco
  artefatos prontos antes da designação).
- Gap: **F-2** (P1). Depende de **AF-07** (R-022) e da designação **D7-03** (R-027).
- Destrava, quando fechar: `LGPD-WORKER-DRIFT` (agent-executable).
- Promoção alvo, **ato do encarregado**: mover/renomear este conteúdo para
  `docs/compliance/dsr-procedimento-manual.md` com a assinatura preenchida.
