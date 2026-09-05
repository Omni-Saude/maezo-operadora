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
| `operadora.lgpd.verify_identity` | `lgpd.py::ValidateIdentityWorker` (`topic="operadora.lgpd.verify_identity"`) | Fail-closed: confirma só com `identidade_verificada is True`; `titular_pseudo_id` ausente/vazio ⇒ `WorkerBpmnError("ERR_DSR_IDENTITY_UNVERIFIED")` — guarda **técnico**, nunca acusação de fraude |
| `operadora.lgpd.request_additional_proof` | `make_request_additional_proof_handler` (`_REQUEST_PROOF_TOPIC`) | Pede prova adicional; alimenta `GW_AguardarProva` |
| `operadora.lgpd.send_response` | `make_send_response_handler` (`_SEND_RESPONSE_TOPIC`) | Envia a resposta/negativa ao titular |
| `operadora.lgpd.notify_sla_risk` | `make_notify_sla_risk_handler` (`_NOTIFY_SLA_RISK_TOPIC`) | Serve **duas** tarefas pelo discriminador `sla_breach_phase` (`ack` em `ST_NotificarRiscoSla`, `resolution` em `ST_NotificarJuridicoBreach`) |
| `operadora.events.publish` | publicador genérico | `lgpd_dsr.received` / `.completed` / `.sla_breached` |

Registro: `src/maezo/tools/workers/lgpd.py::register_lgpd_workers`.

### 2.2 Tópicos SEM worker (o humano faz)

O próprio comentário de bootstrap declara a lacuna: *"that BPMN's remaining 2 topics
(compile_data_package/execute_request) still have no worker — #55 R-C/R-D, DPO/SME-sign-off-gated"*
(`lgpd.py`, bloco imediatamente acima de `register_lgpd_workers`).

| Tópico | Tarefa BPMN | Consequência operacional |
|---|---|---|
| `operadora.lgpd.compile_data_package` | `ST_CompilarPacote` (`bpmn:181-182`) | A tarefa externa fica **pendente no motor** até que alguém a complete manualmente |
| `operadora.lgpd.execute_request` | `ST_ExecutarRequisicao` (`bpmn:270-271`) | Idem |

A casa já mantém a reconciliação código↔modelo completa deste processo em
[`docs/compliance/lgpd-topic-reconciliation.md`](../../compliance/lgpd-topic-reconciliation.md)
(tabela §2, linhas T1-T7 e O1-O5). Este runbook não a substitui — ele a executa do lado
operacional. Leia as duas juntas.

### 2.3 Guardas estruturais que o motor executa sozinho

- `GW_DecisaoDsr` só avança com um dos três valores explícitos de `decisao_dsr`
  (`APROVAR_ENVIO` | `EXECUTAR_E_ENVIAR` | `NEGAR_FUNDAMENTADO`); ausente/desconhecido roteia a
  `End_ErrDecisaoInvalida` (`ERR_DSR_DECISION_INVALID`) — **nunca libera dados por omissão**.
- `GW_GuardFundamentacao` barra `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal`
  (`End_ErrFundamentacaoAusente`, `ERR_DSR_FUNDAMENTACAO_AUSENTE`) — guard HITL avaliado pelo
  **engine**, não por validação de formulário.
- `BE_IdentidadeInverificavel` (boundary em `ST_VerificarIdentidade`) termina em
  `End_IdentidadeInverificavel`, terminal **neutro** (fail-safe, não adverso).

### 2.4 As duas variáveis de TEXTO LIVRE que nenhum controle ancorado em nome cobre

Este é o único ponto em que **o próprio runbook** cria exposição de PHI — por isso está aqui, no
corpo, e não numa nota de rodapé.

| Variável | Onde o runbook a pede | Em `PHI_PROCESS_VARS`? | Em `PHI_FREE_TEXT_VARS` (chokepoint CC-06)? | Proteção que existe HOJE |
|---|---|---|---|---|
| `detalhes_requisicao` | Passo 1 (abertura da instância) | **NÃO** | **NÃO** | Omissão **manual** num único call site: `lgpd.py::make_request_additional_proof_handler` não copia o campo para a notificação (comentário no próprio código: *"detalhes_requisicao (free-text PHI) is NEVER copied into the notification"*) |
| `fundamentacao_legal` | Passo 5 (**obrigatória** em `NEGAR_FUNDAMENTADO`) | **NÃO** | **NÃO** | Omissão **manual** num único call site: `lgpd.py::make_send_response_handler` publica apenas o booleano de presença `tem_fundamentacao` |

Verificado por comando, não por memória:

```
$ .venv/bin/python -c "from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS, PHI_FREE_TEXT_VARS
for n in ('detalhes_requisicao','fundamentacao_legal'):
    print(n, n in PHI_PROCESS_VARS, n in PHI_FREE_TEXT_VARS)"
detalhes_requisicao False False
fundamentacao_legal False False
```

Consequência exata: `redact_phi_vars` **não toca** nesses dois nomes (ele só substitui valores cujo
NOME está em `PHI_PROCESS_VARS`), e o scrub de início de processo `redact_free_text_vars`
(chokepoint CC-06) também não — além de que, como o Passo 1 declara, **este processo não tem sítio
de start pelo chokepoint**, então um start manual nem passa por lá. O valor fica **cru** nas
variáveis de processo do motor, isto é, no banco do CIB Seven, cuja retenção é justamente o que a
matriz **não ratificada** de AF-07 deveria fixar.

A cerca de completude PHI da própria casa já diz isso, endereçada a **este mesmo assinante**
(`src/maezo/platform/validation/phi_completeness.py::DISPOSITIONS`, entrada `detalhes_requisicao`,
status `DRAFT/verify (DPO)`):

> *"RECOMMEND adding it to `PHI_PROCESS_VARS`. The strongest case in this table: the repo's OWN
> code already names it free-text PHI and excludes it from ONE egress by hand, which means today's
> protection is a manual omission at a single call site instead of the name-anchored control.*
> ***Any other worker that copies process variables into an output dict emits it raw.****"*

A entrada irmã `DISPOSITIONS["fundamentacao_legal"]` recomenda tratá-la como a mesma classe de
`fundamentacao_dut` — que **está** em `PHI_PROCESS_VARS` — porque é "ilimitada por construção"
(sem enum, sem domínio `camunda:value`, sem tabela de decisão que a escreva) e a prosa do próprio
BPMN espera fundamento **clínico** dentro dela (*"NEGAR_FUNDAMENTADO exige fundamentacao_legal
(ex.: retencao obrigatoria de prontuario)"*). As duas já estão na fila de revisão da casa como
perguntas abertas ao DPO: `docs/review-queue.md`, seção **GAP-DU-07**, linhas `detalhes_requisicao`
e `fundamentacao_legal`.

**Aresta de vazamento que já existe no código — latente hoje, e NÃO corrigida por este rascunho.**
`lgpd.py::ExecuteErasureWorker.execute` é literalmente "outro worker que copia process variables
para um dict de saída": no ramo `NEGAR_FUNDAMENTADO` ele devolve `"fundamentacao_legal"` **cru**
para as variáveis de processo e ainda o escreve numa linha structlog (`fundamentacao=...`). Isso
está **latente**, não vivo: esse worker está registrado no tópico **órfão**
`operadora.lgpd.execute_erasure`, que o BPMN não declara (§5.2) — nenhuma service task o aciona.
O PR de conformidade **R-181**, que colapsa os três `Execute*Worker` no tópico modelado
`operadora.lgpd.execute_request`, **torna essa aresta viva**. Isto é uma condição para a
assinatura, não uma curiosidade: R-181 não é um PR de nomenclatura — ele precisa levar junto o
tratamento de `fundamentacao_legal`.

> **REGRA DO PROCEDIMENTO, enquanto o DPO não decidir a disposição desses dois nomes.**
> Nos dois campos digita-se **apenas texto mínimo e pseudonimizado**:
> - **nunca** CPF, nome, telefone, e-mail, endereço, matrícula, número de carteirinha, número de
>   guia, nem transcrição/colagem da mensagem do titular;
> - em `detalhes_requisicao`: o **tipo** do pedido já vai em `tipo_requisicao`; o campo carrega no
>   máximo uma referência do atendimento (protocolo interno) e um resumo em **classes**
>   (ex.: `"pedido de acesso a dados de atendimento ambulatorial; protocolo AT-2026-000123"`);
> - em `fundamentacao_legal`: apenas o **fundamento**, não o caso (ex.: `"retenção obrigatória de
>   prontuário — Lei 13.787/2018 art. 6"`), nunca o relato clínico do titular;
> - o detalhe integral do pedido fica no sistema de atendimento, **fora** das variáveis do motor,
>   e é ele que o Passo 4 usa.
>
> O contrato (`docs/processes/contracts/SP-OP-LGPD-DSR-001.md`) já descreve `detalhes_requisicao`
> como "texto da requisição (pseudonimizado)". O que este rascunho acrescenta é a verdade
> incômoda: essa pseudonimização é hoje **uma promessa de quem digita**, não um controle da
> plataforma. Enquanto for assim, a disciplina acima é o controle.

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
| `detalhes_requisicao` | **texto mínimo, em classes, pseudonimizado — LEIA §2.4 ANTES DE DIGITAR.** Este nome **não** está em `PHI_PROCESS_VARS` nem em `PHI_FREE_TEXT_VARS`: o que for digitado aqui fica cru nas variáveis de processo do motor |
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

> **PHI: `fundamentacao_legal` é texto livre SEM controle ancorado em nome (§2.4).** O engine
> exige que o campo esteja **preenchido**; nada limita **o que** ele contém. Escreva o
> **fundamento**, não o caso: a base legal e a classe da retenção, nunca o relato clínico, nunca
> identificador do titular. O valor persiste cru nas variáveis de processo do motor, e
> `lgpd.py::ExecuteErasureWorker.execute` já o devolve cru num dict de saída (latente hoje;
> vivo quando R-181 colapsar os workers no tópico modelado).

### Passo 6 — **MANUAL**: executar (`execute_request`)

Não há worker. E, mais grave que a ausência do worker: **a eliminação real não é executável**.
`ErasureManager.erase()`/`.verify()` levantam `ErasureNotImplementedError` **sempre**
(`src/maezo/platform/erasure.py:152` e `src/maezo/platform/erasure.py:187`), e as duas pontes de identidade
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
   **não feito aqui**.

   Hoje `register_lgpd_workers` registra **quatro** tópicos que o BPMN não declara, não três:
   `operadora.lgpd.execute_export`, `.execute_rectification`, `.execute_erasure` **e**
   `.publish_completed` — o `ST_PublishCompleted` do BPMN usa o publicador genérico
   `operadora.events.publish`, então nenhuma service task carrega `publish_completed`.
   A reconciliação da casa já os enumera como **O2-O5** em
   [`docs/compliance/lgpd-topic-reconciliation.md`](../../compliance/lgpd-topic-reconciliation.md)
   (o O1, `assess_request`, foi RETIRADO em T2.8 e não conta mais). Os dois atos são **distintos**:
   R-181 colapsa O2-O4 em `execute_request`; a ação **R-H** daquele documento pede a **retirada**
   de `publish_completed` (O5). Assinar um não resolve o outro.
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
| Eliminação prometida e não executada | `src/maezo/platform/erasure.py:152` / `src/maezo/platform/erasure.py:187` (`ErasureManager.erase`/`.verify`) |
| Pacote compilado sem matriz ratificada | AF-07 (`load_retention_matrix` recusa) |
| Prazos internos P7D/P10D ainda `DRAFT/verify` | contrato §SLAs |
| Política de produtor de `identidade_verificada` não ratificada | ADR-0031 Decisão ponto 5 |
| **`detalhes_requisicao` digitado no Passo 1 fica CRU no motor** — o nome não está em `PHI_PROCESS_VARS` nem em `PHI_FREE_TEXT_VARS`; a proteção de hoje é uma omissão manual num call site | §2.4; `phi_completeness.py::DISPOSITIONS["detalhes_requisicao"]` (`DRAFT/verify (DPO)`); `docs/review-queue.md` §GAP-DU-07 |
| **`fundamentacao_legal` exigida no Passo 5 fica CRA no motor** — mesma classe do irmão JÁ listado `fundamentacao_dut`, e `ExecuteErasureWorker` já a devolve crua num dict de saída (latente enquanto o tópico for órfão; viva com R-181) | §2.4; `phi_completeness.py::DISPOSITIONS["fundamentacao_legal"]` (`DRAFT/verify (DPO)`); `docs/review-queue.md` §GAP-DU-07 |
| **Quatro** workers registrados fora do tópico modelado (não três) | R-181 (O2-O4) + R-H/retirada de `publish_completed` (O5), `docs/compliance/lgpd-topic-reconciliation.md` |

---

## 7. Rastreabilidade

- Decisões do dono: **R-029** (forma = runbook que abre a instância real), **R-055** (os cinco
  artefatos prontos antes da designação).
- Gap: **F-2** (P1). Depende de **AF-07** (R-022) e da designação **D7-03** (R-027).
- Destrava, quando fechar: `LGPD-WORKER-DRIFT` (agent-executable).
- Reconciliação código↔modelo que este runbook executa do lado operacional:
  `docs/compliance/lgpd-topic-reconciliation.md` (tabela §2; ações R-A…R-H).
- Perguntas PHI já abertas à mesma assinatura: `docs/review-queue.md`, seção **GAP-DU-07** —
  linhas `detalhes_requisicao` e `fundamentacao_legal`.
- Promoção alvo, **ato do encarregado**: mover/renomear este conteúdo para
  `docs/compliance/dsr-procedimento-manual.md` com a assinatura preenchida.

---

## 8. Adendo de re-verificação — 2026-09-05 (`ce38100`)

Este rascunho foi escrito sobre `0433db0` e re-verificado contra `ce38100` (merge de #318). O que
mudou na re-verificação, declarado em vez de reescrito em silêncio:

1. **§2.4 é NOVA.** A primeira redação do Passo 1 dizia apenas "texto da requisição,
   pseudonimizado" e o §6 não listava nenhum dos dois nomes. Isso instruía o operador a digitar
   PHI num campo sem controle ancorado em nome, sem declarar o risco — o defeito mais grave
   apontado na verificação adversarial deste pacote.
2. **§5.2 dizia "os três"; são quatro** (`publish_completed` incluído) — corrigido, com as duas
   ações distintas (R-181 e R-H) separadas.
3. **Citações por linha em `lgpd.py` (`:97`, `:754`) trocadas por citação por símbolo**, que é a
   regra da casa para documentos que sobrevivem a merges.
4. Nada em `spec/`, `src/`, `tests/` ou `spec/policies/**` foi alterado por este rascunho; os
   campos de assinatura continuam **vazios**.
