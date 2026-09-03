# Contrato — SP-OP-CANCEL-001 (Cancelamento / Rescisao Contratual)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 (Wave C — WC.1) · **BPMN:** `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` (GAP-FAB-NOTIF fix: o nome anterior citado aqui, `..._Cancelamento_Contratual.bpmn`, nao existe — `ls spec/processes/bpmn/ | grep -i cancel` confirma o unico arquivo real; drift ja sinalizado em `docs/sme-dispatch/juridico/PACKAGE.md:139`)
**Gatilho regulatorio:** RN 593/2023 (regras de cancelamento/rescisao e suspensao por inadimplencia — consolida/substitui RN 412/2016: **DRAFT/verify**), RN 412/2016 (cancelamento a pedido do beneficiario: **DRAFT/verify**), Lei 9.656/1998 art. 13 (rescisao unilateral / rol taxativo de hipoteses). **TODAS as citacoes e prazos sao DRAFT/verify <RN> com regulatorio/juridico antes de qualquer timer em producao.**

## Invariante L0 hard (nao negociavel)

`contract_termination` e L0 hard (`_hard_frozen.yaml`, CI-enforced). NENHUM caminho
automatizado rescinde o contrato pela operadora (for-cause / unilateral / por
inadimplencia) NEM nega/retem o pedido de cancelamento do beneficiario. A rescisao pela
operadora SO nasce na User Task humana `UT_AnaliseRescisao` (ou `UT_CoordenacaoCancelamento`
no estouro de SLA), com `decisao_cancelamento=RESCINDIR` registrado por humano do grupo
`juridico-contratos`. Nenhuma DMN deste processo possui saida de rescisao, negativa de
cancelamento ou retencao; inadimplencia/inelegibilidade/prazo aparentes roteiam para
analise humana (catch-all conservador).

**Excecao L2 (direito clerical do titular):** o cancelamento **a pedido do beneficiario**
(RN 412 — direito potestativo do titular) pode efetivar-se sem User Task adversa, pois nao
e decisao adversa contra o beneficiario; ainda assim a efetivacao passa por DMN de
admissibilidade clerical (`cancel_admissibility`) e nunca produz, por automacao, uma
*negativa do pedido* nem uma *rescisao pela operadora*. Qualquer ambiguidade (titularidade,
notificacao previa pendente, vinculo coletivo/empresarial, retencao) -> `ANALISE_HUMANA`.

## Terminais humano-gated (no-denial — §4-bis-F)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_ContratoRescindido` | **ADVERSO** — rescisao unilateral pela operadora | `UT_AnaliseRescisao` (ou `UT_CoordenacaoCancelamento`) com `decisao_cancelamento=RESCINDIR` humano |
| `End_PedidoCancelamentoNegado` | **ADVERSO** — negativa do pedido de cancelamento do beneficiario (ex.: titularidade nao comprovada, contrato coletivo cuja rescisao cabe ao estipulante) | `UT_AnaliseRescisao` com `decisao_cancelamento=MANTER` humano + guard `operadora.cancel.confirm_maintained_decision` (`ERR_CANCEL_MANTER_NOT_HUMAN`, GAP-CANCEL-3) |
| `End_ContratoSuspenso` | **ADVERSO** — suspensao por inadimplencia (RN 593) | `UT_AnaliseRescisao` com `decisao_cancelamento=SUSPENDER` humano |
| `End_CanceladoBeneficiario` | neutro — cancelamento a pedido efetivado (direito do titular) | caminho clerical L2 (DMN favoravel) OU User Task |
| `End_ContratoMantido` | neutro — vinculo mantido | qualquer caminho (mesmo guard `confirm_maintained_decision` acima, mesma UT) |
| `End_ManterNaoConfirmado` | neutro — confirmacao MANTER **barrada** pelo guard (nada registrado; espelha `End_FundamentacaoIncompletaBloqueada` de AUTH) | boundary `BE_ManterNaoConfirmado` captura `ERR_CANCEL_MANTER_NOT_HUMAN` lancado por `confirm_maintained_decision` (GAP-CANCEL-3) |

Os tres terminais ADVERSOS NUNCA aparecem na history do engine sem uma User Task humana
concluida na history (teste de invariante — ver test-spec).

## Business key (idempotencia)

```
CANCEL-{tenant_id}-{numero_contrato}
```

Variante por beneficiario (planos individuais/familiares onde a chave operacional e a
matricula): `CANCEL-{tenant_id}-{matricula_beneficiario}`. Uma instancia ativa por
contrato/beneficiario; reenvio da mesma solicitacao retorna a instancia ativa
(`mcp-cibseven.start_process` consulta a business key antes de iniciar — start idempotente,
sem duplicar cancelamento/rescisao).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_contrato` | string | sim | Numero do contrato (chave de negocio) |
| `matricula_beneficiario` | string | sim | Matricula/pseudo-id do beneficiario (ADR-0006 — Zona Geral usa pseudonimo) |
| `tipo_solicitacao` | string | sim | `pedido_beneficiario` \| `inadimplencia` \| `for_cause_operadora` \| `fraude_referida` |
| `origem_solicitacao` | string | sim | `beneficiario` \| `agente_lucas` \| `operadora` \| `juridico` |
| `tipo_plano` | string | sim | `individual` \| `familiar` \| `coletivo_empresarial` \| `coletivo_adesao` |
| `motivo_informado` | string | nao | Texto livre pseudonimizado (motivo do pedido / fundamento) |
| `dentro_prazo` | boolean | sim | Pre-resolvido por worker (janela contratual/regulatoria — math de prazo no worker) |
| `notificacao_previa_feita` | boolean | sim | Pre-resolvido por worker (comprovacao de notificacao previa ao beneficiario — RN 593) |
| `titularidade_confirmada` | boolean | sim | Pre-resolvido por worker (quem solicita e o titular legitimo) |
| `vinculo_ativo` | boolean | sim | Pre-resolvido por worker (cadastro/contrato vigente) |
| `meses_inadimplencia` | integer | nao | Pre-resolvido por worker (so quando `tipo_solicitacao=inadimplencia`) |
| `data_solicitacao_iso` | string | sim | Data (YYYY-MM-DD) da solicitacao |
| `documentos_refs` | json | sim | Referencias de anexos/comprovantes (pode ser vazio) |

> Nota: `meses_inadimplencia` e `integer` (contagem). NUNCA `number`. Valores monetarios de
> debito, quando entrarem na promocao a FINAL, serao `double` (BRL) ou inteiro-centavos.

## Variaveis de saida (preenchidas pelas User Tasks humanas)

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_cancelamento` | string | `RESCINDIR` \| `MANTER` \| `SUSPENDER` \| `EFETIVAR_PEDIDO` \| `SOLICITAR_INFO` (User Task humana — unica origem) |
| `fundamentacao_contratual` | string | **Obrigatoria** se `RESCINDIR`, `MANTER` ou `SUSPENDER` |
| `referencia_regulatoria` | string | **Obrigatoria** se `RESCINDIR`/`SUSPENDER` (ex.: RN 593 — DRAFT/verify) |
| `comprovacao_notificacao_previa` | string | **Obrigatoria** se `RESCINDIR`/`SUSPENDER` (referencia ao comprovante) |
| `responsavel_id` | string | Id do humano que decidiu (cadeia de auditoria ADR-0007) — preenchido no complete da User Task |
| `data_efeito_iso` | string | Data de efeito do desfecho (rescisao/cancelamento/suspensao) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (ADR-0016 portado). Contexto = `cancel`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.cancel.received` | produz | apos start |
| Kafka | `agents.events.cancel.pended` | produz | pendencia de notificacao previa / documentacao aberta |
| Kafka | `agents.events.cancel.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.cancel.completed` | produz | fim (payload.desfecho = `rescindido_operadora` \| `cancelado_beneficiario` \| `mantido` \| `suspenso` \| `pedido_negado`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (todos os SP-OP) |
| External task | `operadora.cancel.resolve_facts` | consome (worker) | pre-resolve fatos clericais (prazo, notificacao previa, titularidade, vinculo, inadimplencia) — fatos, nunca decisao |
| External task | `operadora.cancel.prepare_dossier` | consome (worker) | monta dossie de analise para a User Task (narrativa/montagem — sem decidir; alvo de delegacao a agente futuro) |
| External task | `operadora.cancel.request_notification` | consome (worker) | dispara/registra notificacao previa ao beneficiario (RN 593 — DRAFT/verify) |
| External task | `operadora.cancel.effectuate_member_request` | consome (worker) | **efetiva o cancelamento A PEDIDO do beneficiario** (direito do titular — L2); NAO rescinde for-cause |
| External task | `operadora.cancel.send_cancellation_notice` | consome (worker) | **emite a notificacao formal de RESCISAO/SUSPENSAO** em nome do responsavel humano — **GUARDADO** (ver Codigos de erro) |
| External task | `operadora.cancel.confirm_maintained_decision` | consome (worker) | **confirma a decisao MANTER** (manutencao do vinculo / negativa do pedido) — **GUARDADO** (ver Codigos de erro; GAP-CANCEL-3) |
| External task | `operadora.cancel.notify_sla_risk` | consome (worker) | alerta `coordenacao-contratos` de risco de SLA (timer nao-interruptivo) |
| Message BPMN | `msg.cancel.notification_ack` | recebe | correlacao por business key — confirmacao/recebimento da notificacao previa, destrava o gateway de prazo (GAP-CANCEL-4: **obrigatorio** setar `notificacao_previa_feita=true` no payload da correlacao — ver nota abaixo) |
| Message BPMN | `msg.cancel.info_received` | recebe | correlacao por business key — info/documentacao solicitada pelo humano chegou |

> **GAP-CANCEL-4 (`notificacao_previa_feita=true` como pre-condicao de avanco):** `ST_ResolveFacts`
> (`operadora.cancel.resolve_facts`) NUNCA recalcula fatos clericais — apenas ECOA as variaveis de
> processo ja existentes (WorkerHarness nao retorna output variables ao engine; ver §Codigos de
> erro / ADR-0018). Isso significa que, quando `msg.cancel.notification_ack` e correlacionada, o
> emissor da correlacao (integracao/worker de origem que detecta a confirmacao do recebimento —
> hoje simulado pelo agente de teste) **DEVE** incluir `notificacao_previa_feita=true` como
> variavel de processo no payload da correlacao (`processVariables`). Sem essa atualizacao,
> `ST_ResolveFacts` reavalia `cancel_admissibility` com o `notificacao_previa_feita` ANTIGO
> (`false`), a DMN retorna `PENDENTE_NOTIFICACAO` de novo (r_pendente_notificacao) em vez de
> `SEGUE_ANALISE`, e a instancia reabre o mesmo ramo de espera em vez de avancar a
> `UT_AnaliseRescisao` — nunca um caminho adverso (fail-safe conservador), mas um estouro de
> progresso silencioso. Esta exigencia existia SO no scaffolding do teste de integracao
> (`test_notificacao_ack_destrava_analise`) antes deste registro; nenhum integrador real de
> `msg.cancel.notification_ack` foi implementado ainda (fora de escopo Phase 2 — a promocao a
> FINAL deve materializa-la em um worker/listener real que seta o fato antes de correlacionar).

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}`
— **`"number"` e invalido**; dias/SLA = string ISO 8601; dinheiro = `double`/inteiro-centavos.
Toda `decisionTable` tem `hitPolicy`; toda tabela tem row catch-all -> caminho humano
conservador. **Nenhuma DMN possui coluna de saida de rescisao, negativa ou retencao.**

### `cancel_admissibility` (hitPolicy FIRST — DRAFT)

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `tipo_solicitacao` | string | `pedido_beneficiario` \| `inadimplencia` \| `for_cause_operadora` \| `fraude_referida` |
| in | `tipo_plano` | string | `individual` \| `familiar` \| `coletivo_empresarial` \| `coletivo_adesao` |
| in | `dentro_prazo` | boolean | — (GAP-CANCEL-2: gata r_pedido_l2, ao lado de titularidade_confirmada/vinculo_ativo) |
| in | `notificacao_previa_feita` | boolean | — |
| in | `titularidade_confirmada` | boolean | — |
| in | `vinculo_ativo` | boolean | — (GAP-CANCEL-1: fato mandatorio, wireado como input da DMN) |
| out | `roteamento` | string | `EFETIVAR_PEDIDO` \| `SEGUE_ANALISE` \| `PENDENTE_NOTIFICACAO` \| `ANALISE_HUMANA` |
| out | `motivo` | string | rotulo do roteamento (auditoria) |

Regras (DRAFT — confirmar com juridico/regulatorio):
- `pedido_beneficiario` + `dentro_prazo=true` + `titularidade_confirmada=true` + `vinculo_ativo=true` + `tipo_plano ∈ {individual, familiar}` -> `EFETIVAR_PEDIDO` (direito do titular, L2 clerical). Vinculo inativo (GAP-CANCEL-1) ou fora do prazo contratual/regulatorio (GAP-CANCEL-2) cai no catch-all -> `ANALISE_HUMANA`.
- `pedido_beneficiario` + `tipo_plano ∈ {coletivo_empresarial, coletivo_adesao}` -> `ANALISE_HUMANA` (rescisao cabe ao estipulante — nunca auto-efetiva nem auto-nega).
- `inadimplencia` ou `for_cause_operadora` + `notificacao_previa_feita=false` -> `PENDENTE_NOTIFICACAO`.
- `inadimplencia` ou `for_cause_operadora` + `notificacao_previa_feita=true` -> `SEGUE_ANALISE` (humano decide RESCINDIR/SUSPENDER/MANTER).
- `fraude_referida` -> `ANALISE_HUMANA` (NUNCA auto-flag; `fraud_accusation` L0 hard — alimenta SP-OP-FRAUDE-001 em Phase 3).
- **Catch-all (row final):** qualquer combinacao nao coberta -> `ANALISE_HUMANA`. **Sem saida RESCINDIR/NEGAR/RETER.**

### `cancel_routing` (hitPolicy FIRST — DRAFT; GAP-CANCEL-5)

Consumida por `BRT_Classificacao` (entre `BRT_CancelSla` e `ST_PrepareDossier`) — enriquece o
dossie de `UT_AnaliseRescisao` com rotulos NEUTROS de triagem. **NAO gata nenhum gateway; NAO
possui saida de rescisao/negativa/retencao** (mesma invariante L0 hard das demais DMN deste
processo).

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `tipo_solicitacao` | string | ver entrada |
| in | `tipo_plano` | string | ver entrada |
| out | `natureza_caso` | string | `pedido_titular` \| `inadimplencia` \| `for_cause` \| `fraude_referida` \| `indeterminado` |
| out | `grupo_sugerido` | string | `gestao-contratos` \| `juridico-contratos` (sempre um grupo HUMANO; catch-all -> `juridico-contratos`) |

`BRT_Classificacao` promove `natureza_caso`/`grupo_sugerido` a variaveis de processo FLAT via
`camunda:outputParameter` (`${classificacao.natureza_caso}` / `${classificacao.grupo_sugerido}` —
ENGINE-16004 avoidance, mesmo padrao de GAP-FRAUDE-1/`intensidade_investigacao`: o resultVariable
`classificacao`, tipo Object/HashMap, chega ao worker externo via `fetchAndLock` como blob
Java-serializado no engine real — so eh deserializado nas expressoes avaliadas PELO ENGINE, nao
no worker). `ST_PrepareDossier` (`operadora.cancel.prepare_dossier`) le as duas variaveis flat
diretamente e ecoa no dossie; nenhum gateway ou worker downstream consome esta saida para decidir
(rotulo puramente informativo).

### `cancel_sla` (hitPolicy FIRST — DRAFT, todos os prazos DRAFT/verify RN 593)

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `tipo_solicitacao` | string | ver entrada |
| in | `tipo_plano` | string | ver entrada |
| out | `sla_analise` | string (ISO 8601) | ex.: `P10D` (prazo de analise humana) |
| out | `sla_alerta` | string (ISO 8601) | ex.: `P7D` (~60-70% do SLA) |
| out | `prazo_notificacao_previa` | string (ISO 8601) | janela de notificacao previa / direito de purga da inadimplencia (RN 593 — **DRAFT/verify**) |
| out | `fonte_regulatoria` | string | RN citada (registrada na variavel para auditoria) |

> Prazos legais sao em dias (e por vezes uteis); ISO 8601 usa dias corridos — usar valores
> conservadores e resolver calendario util no worker. **Toda data/prazo aqui e DRAFT/verify
> com juridico/regulatorio (RN 593).**

## Papeis humanos (candidate groups)

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver Open Questions
> / review-queue). Os nomes `gestao-contratos`, `juridico-contratos` e `coordenacao-contratos`
> sao candidatos e podem nao corresponder aos grupos reais do IdP/console de User Tasks.

| Grupo | Papel | Tarefa |
|---|---|---|
| `juridico-contratos` | Juridico/contratos | `UT_AnaliseRescisao` (**rescisao/negativa/suspensao SO aqui**) — `decisao_cancelamento ∈ {RESCINDIR, MANTER, SUSPENDER, EFETIVAR_PEDIDO, SOLICITAR_INFO}` |
| `gestao-contratos` | Gestao de contratos | `UT_AnaliseRescisao` (co-candidate group para casos de pedido/efetivacao clerical revisada) |
| `coordenacao-contratos` | Coordenacao de contratos | `UT_CoordenacaoCancelamento` (SLA de analise estourado — assume a decisao, que continua humana) |

Toda `<bpmn:userTask>` traz `camunda:candidateGroups` (gate D1). A decisao adversa NUNCA
muda de natureza por estouro de SLA: `UT_CoordenacaoCancelamento` herda as mesmas regras de
campos obrigatorios de `UT_AnaliseRescisao`.

## SLAs

| Timer | Valor | Tipo | Fonte |
|---|---|---|---|
| Analise (`${cancel_sla.sla_analise}`, ex. P10D) | interruptivo -> `agents.events.cancel.sla_breached` + `UT_CoordenacaoCancelamento` assume | RN 593 — **DRAFT/verify** |
| Alerta de risco (`${cancel_sla.sla_alerta}`, ~60-70%) | nao-interruptivo -> `operadora.cancel.notify_sla_risk` | politica interna — **DRAFT** |
| Notificacao previa (`${cancel_sla.prazo_notificacao_previa}`) | event gateway: `msg.cancel.notification_ack` **vs** timer -> `UT_AnaliseRescisao` (humano decide; expiracao NUNCA auto-rescinde) | RN 593 (notificacao/purga de inadimplencia) — **DRAFT/verify** |

> O event gateway de notificacao previa **substitui/INVERTE** qualquer padrao de
> "auto-rescindir/auto-suspender no estouro do prazo" (anti-pattern do reference): a
> expiracao do prazo de notificacao roteia para a User Task humana, nunca para um terminal
> adverso automatico.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_CANCELLATION_NOT_HUMAN` | worker-guard em `operadora.cancel.send_cancellation_notice` | o worker do efeito adverso (rescisao/suspensao formal) **recusa** executar sem `decisao_cancelamento ∈ {RESCINDIR, SUSPENDER}` setado por humano E sem `responsavel_id` na cadeia de auditoria (ADR-0007); lanca BPMN error / falha de tarefa, registrando a tentativa. Tambem recusa se `fundamentacao_contratual`/`referencia_regulatoria`/`comprovacao_notificacao_previa` ausentes. |
| `ERR_CANCEL_MANTER_NOT_HUMAN` | worker-guard em `operadora.cancel.confirm_maintained_decision` (GAP-CANCEL-3) | o worker do efeito MANTER (manutencao do vinculo / negativa do pedido) **recusa** executar sem `decisao_cancelamento == MANTER` setado por humano — inclui o flow **default** de `GW_DecisaoCancelamento` (`Flow_GWDec_Mantido`), que dispara para qualquer `decisao_cancelamento` nao reconhecida, inclusive ausente. Tambem recusa se `fundamentacao_contratual` ausente. **CAPTURADO** pelo boundary `BE_ManterNaoConfirmado` -> `End_ManterNaoConfirmado` (terminal NEUTRO — nada registrado; espelha `BE_NegativaIncompleta` de AUTH): o task esta no caminho principal do ramo MANTER, e um BPMN error nao-capturado encerraria o escopo silenciosamente (sem end event, sem incidente). |
| `ERR_CANCEL_INVALID_CONTRATO` | declarado (`Error_CancelContratoInvalido`) para uso dos workers | worker lanca BPMN error se contrato inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **Guarda no-denial (parte 4 de 5 do padrao §4-bis-F):** `ERR_CANCELLATION_NOT_HUMAN` e a
> ultima linha de defesa — mesmo que um bug de modelagem alcance `send_cancellation_notice`
> sem User Task humana, o worker recusa. O teste de invariante (parte 5) verifica que isto
> nunca acontece consultando a history do engine.

## Pendencias para promocao a FINAL

- DI (diagrama BPMN);
- confirmacao de TODOS os prazos com regulatorio/juridico (RN 593: prazo de notificacao
  previa, janela de purga de inadimplencia, prazo de analise) — **dias uteis -> ISO**;
- confirmacao das citacoes de RN (RN 593 consolida/substitui RN 412? — DRAFT/verify);
- matriz de hipoteses de rescisao unilateral por `tipo_plano` (individual/familiar vs
  coletivo — Lei 9.656 art. 13 §unico; coletivos seguem regras proprias do estipulante);
- regra de retencao/portabilidade de carencias na rescisao (interacao com direitos do
  beneficiario);
- **confirmacao dos nomes dos candidate groups** (`gestao-contratos`/`juridico-contratos`/
  `coordenacao-contratos`) contra a taxonomia organizacional da operadora;
- integrador/worker real de `msg.cancel.notification_ack` que sete `notificacao_previa_feita=true`
  no payload da correlacao (GAP-CANCEL-4 — hoje so simulado no teste de integracao; ver nota em
  §Topicos);
- interacao com SP-OP-LGPD-DSR-001 (eliminacao de dados pos-rescisao vs retencao legal de
  prontuario — ja referenciado em LGPD-DSR §promocao a FINAL) e com a futura
  SP-OP-INADIMPLENCIA-001 (Phase 3);
- politica de re-adesao / reativacao apos cancelamento a pedido.
