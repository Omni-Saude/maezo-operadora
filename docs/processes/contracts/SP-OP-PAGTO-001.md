# Contrato — SP-OP-PAGTO-001 (Pagamentos de Alcada)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review (medico-auditor/juridico/DPO/regulatorio/financas/PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 (Wave W-B; modelado uma fase a frente — playbook 5-bis) · **BPMN (alvo, autorado em wave posterior):** `spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn`
**Gatilho regulatorio:** **Politica financeira interna** da operadora (escada de alcada / segregacao de funcoes / SOX-like controls — **DRAFT/verify** com financas). Nao ha RN ANS especifica que defina o teto; o teto e governanca financeira interna. Interage com Lei 9.656/1998 (pagamento a prestador) e com SP-OP-CONTAS-001 (a conta adjudicada gera a obrigacao de pagamento — desde ADR-0040 isto **e verdade**: o handoff real e `operadora.contas.handoff_pagamento`) — **DRAFT/verify**.

> Este contrato e o ponto de sincronizacao (§4-bis-F): o BPMN/DMN/worker/agente de PAGTO derivam dele. A mecanica no-adverse de cinco partes (ADR-0018) esta materializada abaixo. **Clona o esqueleto de auto-aprovacao `dentro_teto` do SP-OP-AUTH-001** (`auth_auto_approval`), mas e o **UNICO processo da plataforma com candidate-groups dirigidos por valor**: uma DMN `pagto_alcada` emite `grupo_aprovador` que alimenta `camunda:candidateGroups` da User Task de aprovacao.

## Invariante L1 — no-adverse (nao negociavel, ADR-0018)

A **liberacao de um pagamento de alto valor** (`high_value_payment`, **L1** — ADR-0008: "agente
propoe, humano aprova") so nasce numa User Task humana de aprovacao
(`UT_AprovacaoAlcada`, ou `UT_AprovacaoComite` no tier mais alto), com
`decisao_pagamento == APROVAR` setado por um humano **cujo tier de alcada e >= ao valor do
pagamento**. Nenhuma DMN deste processo possui saida que **libere/autorize** o pagamento
adverso (a liberacao acima de alcada e o efeito adverso de risco financeiro): a DMN
`pagto_alcada` apenas **classifica o valor numa faixa e roteia para o grupo aprovador
correto**; ela nunca produz `LIBERAR`/`AUTORIZAR`/`PAGAR`. Faixa de valor ambigua, valor
acima de qualquer tier configurado, dados de pagamento inconsistentes, indisponibilidade da
DMN e estouro de SLA **todos fail-safe para o tier humano mais alto** (catch-all →
`grupo_aprovador = ANALISE_HUMANA` / comite). O efeito adverso e materializado **apenas** pelo
worker `operadora.pagto.release_high_value_payment`, guardado por `ERR_PAYMENT_RELEASE_NOT_HUMAN`
**+ tier-match** (recusa se o tier do aprovador < faixa do valor). O terminal
`End_PagamentoLiberadoHumano` so e alcancavel apos a User Task humana de aprovacao concluida.

**Por que "liberar pagamento acima de alcada" e adverso (L1, nao L2/L3):** liberar dinheiro
acima do teto sem o aprovador de tier correto e um efeito adverso de risco financeiro/fraude
interna (segregacao de funcoes). O caminho automatico produz **somente** o pagamento de
**baixo valor dentro do teto L2** do tenant (analogo a `auth_auto_approval`); qualquer coisa
acima do teto L2 exige aprovador humano de tier adequado. **Nao existe auto-liberacao acima de
alcada** — nem por DMN, nem por timeout (inversao do anti-padrao `Task_AutoApprove`/timeout).

## Terminais (humano-gated vs neutros)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_PagamentoLiberadoHumano` | **ADVERSO (risco financeiro L1)** — liberacao de pagamento de alto valor | `UT_AprovacaoAlcada` / `UT_AprovacaoComite` com `decisao_pagamento=APROVAR` humano **e** tier do aprovador >= faixa do valor |
| `End_PagamentoRecusadoHumano` | neutro→prestador-adjacente — recusa/devolucao do pagamento para revisao (NAO e glosa; glosa nasce em CONTAS-001) | `UT_AprovacaoAlcada` com `decisao_pagamento=RECUSAR` humano (registra `justificativa_recusa`) |
| `End_PagamentoLiberadoAutomatico` | neutro — pagamento **abaixo do teto L2** liberado por caminho clerical (L2) | `pagto_alcada` favoravel (`faixa_valor=DENTRO_TETO_L2`) + admissibilidade clerical |
| `End_PagamentoCancelado` | neutro — solicitacao cancelada/duplicada/sem lastro (sem liberacao) | `UT_AprovacaoAlcada` / `UT_CoordenacaoAlcada` com `decisao_pagamento=CANCELAR` humano (GAP-PAGTO-2; registra `justificativa_recusa`) |
| `End_PagtoOrdemInvalida` | neutro/tecnico — guard de validacao de origem (`ERR_PAGTO_ORDEM_INVALIDA`), NAO e decisao de liberacao/negativa (GAP-PAGTO-4) | boundary error em `ST_ValidatePaymentData` (qualquer caminho; sem efeito adverso) |

O terminal ADVERSO `End_PagamentoLiberadoHumano` NUNCA aparece na history do engine sem uma
User Task humana de aprovacao concluida na history E sem o tier-match satisfeito (teste de
invariante — ver test-spec). Nenhum fim ocorre sem evento de dominio publicado antes (auditoria
dupla engine+Kafka, ADR-0007).

## Business key (idempotencia)

```
PAGTO-{tenant_id}-{ordem_pagamento_id}
```

Alternativa quando o pagamento e disparado por uma conta adjudicada do CONTAS-001:
`PAGTO-{tenant_id}-{numero_lote_tiss}-{prestador_id}` (cunhada por
`contas.handoff_pagamento`). **Terceira forma da familia**, cunhada por
`recurso.handoff_pagamento` quando a operadora **reverte** uma glosa ao deferir um recurso:
`PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` — uma glosa revertida nao tem `ordem_pagamento_id`
pre-existente e nao e escopada por lote; a sua identidade E a glosa (ADR-0040). Uma instancia ativa por ordem de
pagamento; reenvio da mesma ordem retorna a instancia ativa. `mcp-cibseven.start_process` DEVE
consultar a business key antes de iniciar (start idempotente — **evita liberacao duplicada de
pagamento**, risco financeiro direto).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `ordem_pagamento_id` | string | sim | Id da ordem de pagamento (chave de negocio) |
| `numero_lote_tiss` | string | nao | Lote TISS de origem (quando o pagamento decorre de conta adjudicada CONTAS-001) |
| `prestador_id` | string | sim | Prestador/credor beneficiario do pagamento (pseudonimizado; nunca dados bancarios crus em Zona Geral — ADR-0006) |
| `tipo_pagamento` | string | sim | `prestador_rede` \| `reembolso_beneficiario` \| `prestador_livre_escolha` \| `glosa_revertida` \| `ajuste_conciliacao` |
| `valor_pagamento_cents` | integer | sim | Valor a liberar, em **centavos** de BRL (inteiro — **NUNCA `number`**). Dinheiro = inteiro-centavos OU `double`; **`number` e proibido** (ADR-0018 parte 2) |
| `moeda` | string | sim | `BRL` (fixo nesta fase; campo explicito para futura multi-moeda) |
| `competencia` | string | sim | Competencia do pagamento (`YYYY-MM`) |
| `data_vencimento` | date | sim | Vencimento da obrigacao (ancora dos prazos). Chega a Andre via `payload_meta` do envelope A2A (`operadora.pagto.prepare_approval_dossier` → `analytics.actuarial`/`analytics.population`), peer de `competencia`/`conta_origem_ref` no mesmo payload; Andre a semeia em `_contract_variables` ao iniciar/verificar a instancia (idempotente — GAP-PAGTO-7) |
| `conta_origem_ref` | string | sim | Referencia (token) da conta pagadora — **nunca numero de conta cru em Zona Geral** |
| `instrumento_pagamento` | string | sim | `cnab` \| `pix` \| `ted` \| `compensacao` (forma de liquidacao) |
| `dados_pagamento_validos` | boolean | sim* | Pre-resolvido por worker (`operadora.pagto.validate_payment_data`): credor/instrumento/lastro consistentes |
| `lastro_confirmado` | boolean | sim* | Pre-resolvido por worker: ha obrigacao real por tras (conta adjudicada / reembolso aprovado / ordem valida). Resolvido **dentro de PAGTO** (`operadora.pagto.validate_payment_data`) ou por humano em `UT_AnaliseAdmissibilidade`. **Um processo de origem NUNCA semeia este booleano**; ele semeia `lastro_origem`/`lastro_decisor_id` como evidencia (ADR-0040 **I-PAGTO-1**) |
| `lastro_origem` | string | nao | **Evidencia** do lastro, semeada pelo processo de origem: `contas_adjudicacao_automatica` \| `contas_adjudicacao_humana` \| `recurso_deferimento_humano`. Alimenta o formulario de `UT_AnaliseAdmissibilidade`; **nunca substitui** `lastro_confirmado`, que PAGTO resolve |
| `lastro_decisor_id` | string | nao | **Evidencia**: o `analista_id`/`auditor_id` que adjudicou a conta ou deferiu o recurso. **String vazia** na perna automatica de CONTAS — nunca inventada (ADR-0007) |
| `dentro_teto_l2` | boolean | sim* | Pre-resolvido: `valor_pagamento_cents <= teto de auto-liberacao L2 do tenant` (tenants-amh.yaml) |
| `duplicidade_suspeita` | boolean | nao | Sinal **informativo** de worker (detecta ordem ja paga/similar; NUNCA decide; so roteia a humano) |

\* Pre-resolvido por worker de fatos antes de `BRT_AlcadaRouting` (validacao/conferencia/aritmetica; **sem decisao de liberacao**).

> **Dinheiro nunca como `number`.** `valor_pagamento_cents` e `integer` (centavos). Onde houver
> calculo fracionario de tabela, usar `double` (BRL). A allowlist de shape DMN (§4-bis-A,
> ADR-0018 parte 2) torna `number` invalido por construcao.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `faixa_valor` | string | `DENTRO_TETO_L2` \| `ALCADA_L1` \| `ALCADA_L2` \| `ALCADA_L3` \| `ANALISE_HUMANA` (classificacao da DMN `pagto_alcada`; **classifica/roteia, nao libera**) |
| `grupo_aprovador` | string | Candidate-group dirigido por valor emitido pela DMN → alimenta `camunda:candidateGroups` da User Task (ver §candidate-groups) |
| `decisao_pagamento` | string | `APROVAR` \| `RECUSAR` \| `SOLICITAR_INFO` \| `CANCELAR` (preenchida SO por User Task humana de aprovacao; `CANCELAR` = GAP-PAGTO-2, ordem cancelada/duplicada/sem lastro) |
| `valor_aprovado_cents` | integer | Valor efetivamente aprovado pelo humano (centavos; = `valor_pagamento_cents` quando aprovado integralmente) |
| `justificativa_aprovacao` | string | **Obrigatoria se `APROVAR`** — fundamentacao da liberacao de alto valor |
| `justificativa_recusa` | string | **Obrigatoria se `RECUSAR` ou `CANCELAR`** — motivo da recusa/devolucao para revisao, ou do cancelamento/duplicidade/sem-lastro |
| `aprovador_id` | string | Aprovador humano (cadeia de auditoria ADR-0007); carregado no worker do efeito adverso |
| `aprovador_tier` | integer | Tier de alcada do aprovador (1=mais baixo … N=comite); o worker exige `aprovador_tier` compativel com `faixa_valor` (tier-match) |
| `decisao_coordenacao` | string | `assumir_aprovacao` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano de tier >= faixa assume) |

## Variaveis de proveniencia do agente (Andre — ADR-0007/ADR-0015)

Convencao repo-wide de nao-repudio (ADR-0007) e delegacao A2A (ADR-0015) — nao especifica de PAGTO
(mirror `source_agent_id`/`source_agent_version` de `docs/processes/contracts/SP-OP-ESCALATION-001.md`).
Semeadas por `Andre._contract_variables` (`operadora.pagto.prepare_approval_dossier` →
`analytics.actuarial`/`analytics.population`, worker→A2A) junto com as variaveis de entrada; NENHUMA
delas e uma decisao de preco/liberacao — so proveniencia, dossie instrutivo e roteamento humano
(GAP-PAGTO-7: antes deste registro, `_contract_variables` as emitia sem declaracao no contrato).

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `source_agent_id` | string | nao | Agente que preparou o dossie de risco (`andre`) — cadeia de nao-repudio (ADR-0007) |
| `source_agent_version` | string | nao | Versao do agente Andre que preparou o dossie (auditoria ADR-0007) |
| `dossie_andre` | json | nao | Dossie de risco atuarial/populacional (narrativa factual sobre agregados k-anon + refs DMN) montado por Andre — instrui `UT_AprovacaoAlcada`/`UT_AprovacaoComite`; carrega `decisao_pagamento`/`preco_recomendado` sempre `None` (Andre NUNCA decide) |
| `andre_route` | string | nao | Roteamento do grafo do Andre (`auto_route` \| `human_review`) — espelha, nao decide, o roteamento do processo |
| `motivo_encaminhamento` | string | nao | Presente so quando `andre_route=human_review`; motivo do encaminhamento (`aprovacao_alcada` \| `pendencia_dados` \| `analise_humana` \| `duplicidade_suspeita` \| `phi_egress_risk` \| `dmn_indisponivel` \| `ambiguidade` \| `outro`) |
| `grupo_destino` | string | nao | Presente so quando `andre_route=human_review`; grupo humano sugerido por Andre (espelha `grupo_aprovador` da DMN `pagto_alcada`; catch-all conservador `comite-financeiro`) |
| `dmn_decision_refs` | json | nao | Referencias auditaveis (tabela→regra) das DMN que Andre consultou (`pagto_admissibility`/`pagto_alcada`/`pagto_sla`) — cadeia de decisao (ADR-0007/ADR-0012) |
| `aggregate_dataset_refs` | json (list[string]) | nao | Ponteiros opacos k-anon dos datasets agregados usados no dossie de Andre — NUNCA PHI resolvivel (egress chokepoint, ADR-0006/ADR-0019) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado). Contexto = `pagto`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.pagto.received` | produz | apos start (ordem de pagamento recebida) |
| Kafka | `agents.events.pagto.routed` | produz | faixa classificada + grupo aprovador resolvido (payload `faixa_valor`, `grupo_aprovador`; **valor pode ser omitido/bandado em Zona Geral**) |
| Kafka | `agents.events.pagto.sla_breached` | produz | SLA de aprovacao estourado |
| Kafka | `agents.events.pagto.completed` | produz | fim (payload.desfecho = `liberado_automatico` \| `liberado_humano` \| `recusado_humano` \| `cancelado`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso, todos os SP-OP) |
| External task | `operadora.pagto.validate_payment_data` | consome (worker) | valida credor/instrumento/lastro/duplicidade (fatos; **sem liberar**) |
| External task | `operadora.pagto.calculate_facts` | consome (worker) | aritmetica pura (normaliza centavos, compara com teto L2; BRL em inteiro-centavos/`double` — **nunca `number`**) |
| External task | `operadora.pagto.prepare_approval_dossier` | consome (worker→A2A) | monta dossie de aprovacao para a User Task (narrativa/risco; delegacao `analytics.actuarial`/`analytics.population` a Andre — **instrui, nao decide**) |
| External task | `operadora.pagto.notify_sla_risk` | consome (worker) | worker `notify_sla_risk`: registra que a ETAPA de alerta de risco de SLA a `coordenacao-financeira` rodou (timer nao-interruptivo) e retorna `{}` — **NAO afirma `sla_risk_notified` e nao contata canal algum**; nenhuma mensagem sai desta task hoje (FAB-SLA-RISK-NOTIFIED-SLICE4; antes retornava `sla_risk_notified=True` constante). Informativo e nunca adverso: `UT_AprovacaoAlcada` segue aberta. Ligar o alerta a um canal real segue ABERTO em `docs/review-queue.md` |
| External task | `operadora.pagto.release_high_value_payment` | consome (worker) | **efeito adverso gated** — libera o pagamento; recusa sem decisao humana **e** sem tier-match (`ERR_PAYMENT_RELEASE_NOT_HUMAN`); carrega `aprovador_id` + `aprovador_tier` |
| External task | `operadora.pagto.release_low_value_payment` | consome (worker) | libera pagamento **abaixo do teto L2** (caminho clerical L2; sem efeito adverso de alcada) |
| External task | `operadora.pagto.register_payment_refusal` | consome (worker) | registra recusa/devolucao para revisao quando `RECUSAR` (sem liberar) |
| Message BPMN | `msg.pagto.dados_corrigidos` | recebe | correlacao por business key — correcao de dados de pagamento chegou, destrava reavaliacao |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A, ADR-0018 parte 2): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; dinheiro → `integer` (centavos) ou `double` (BRL); dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador (tier mais alto). `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida que libere/autorize/pague.**

### `pagto_admissibility` (hitPolicy FIRST — DRAFT)
in: `dados_pagamento_validos: boolean`, `lastro_confirmado: boolean`, `duplicidade_suspeita: boolean`
out: `roteamento: string` (`SEGUE_ROTEAMENTO` | `PENDENTE_DADOS` | `ANALISE_HUMANA`), `motivo: string`
`duplicidade_suspeita=true` → `ANALISE_HUMANA` (humano confirma; nunca auto-cancela nem auto-libera). Catch-all → `ANALISE_HUMANA`. **Sem saida de liberacao.**

### `pagto_alcada` (hitPolicy FIRST — DRAFT; coracao value-driven; **escada de alcada DRAFT/verify financas**)
in: `valor_pagamento_cents: integer`, `tipo_pagamento: string`, `dentro_teto_l2: boolean`
out: `faixa_valor: string` (`DENTRO_TETO_L2` | `ALCADA_L1` | `ALCADA_L2` | `ALCADA_L3` | `ANALISE_HUMANA`), `grupo_aprovador: string` (→ `camunda:candidateGroups`), `motivo: string`
**Apenas classifica a faixa e roteia para o grupo aprovador — sem saida que libere.** Mapeia o valor para a faixa da escada (DRAFT) e a faixa para o candidate-group de tier correspondente.

Escada de alcada (**DRAFT/verify — sign-off financas obrigatorio; valores ilustrativos, nao reais**), `hitPolicy=FIRST` (a primeira faixa que casa vence; ordem do menor para o maior):

| Ordem | Condicao (`valor_pagamento_cents`, em centavos BRL) | `faixa_valor` | `grupo_aprovador` (DRAFT) |
|---|---|---|---|
| 1 | `<= 10000000` (R$ 100.000 — teto L2 DRAFT) **e** `dentro_teto_l2=true` | `DENTRO_TETO_L2` | `(nenhum — caminho clerical L2)` |
| 2 | `> 10000000` e `<= 50000000` (R$ 100k–500k DRAFT) | `ALCADA_L1` | `aprovacao-financeira-l1` |
| 3 | `> 50000000` e `<= 200000000` (R$ 500k–2MM DRAFT) | `ALCADA_L2` | `aprovacao-financeira-l2` |
| 4 | `> 200000000` e `<= 1000000000` (R$ 2MM–10MM DRAFT) | `ALCADA_L3` | `aprovacao-financeira-l3` |
| **5 (catch-all)** | **qualquer outro** (acima do maior tier, faixa ambigua, ou inputs inconsistentes) | `ANALISE_HUMANA` | `comite-financeiro` (**tier mais alto / conservador**) |

- O **threshold_brl L1** = `100000` BRL (= `10000000` centavos) **DRAFT/verify financas** (limite acima do qual o pagamento deixa de ser auto-liberavel e exige aprovador humano).
- **Catch-all conservador → tier mais alto** (`comite-financeiro`/`ANALISE_HUMANA`): valor acima do maior tier configurado, faixa ambigua ou DMN indisponivel NUNCA auto-liberam — sobem para o comite. (ADR-0018 parte 3 — risco #5 do phase3-plan: "se o ramo L2 abaixo-do-teto for mal-escopado, pagamentos acima de alcada auto-liberam"; este catch-all e a defesa.)
- A DMN emite `grupo_aprovador` como **string** consumida por `camunda:candidateGroups` da User Task (binding value-driven). O mapeamento faixa→grupo e **DRAFT/verify financas + PO/IdP**.

### `pagto_sla` (hitPolicy FIRST — DRAFT; todos os prazos DRAFT/verify; CONTRACT-HITPOLICY-DRIFT: corrigido de UNIQUE, DMN shippada e FIRST — `spec/processes/dmn/pagto_sla.dmn:26`)
in: `faixa_valor: string`, `tipo_pagamento: string`
out: `sla_aprovacao: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte`: string
Tiers mais altos podem ter SLA maior (mais aprovadores). Prazos como string ISO; politica financeira interna — **DRAFT/verify financas** (nao ha prazo RN ANS aqui).

## Papeis humanos (candidate groups — **VALUE-DRIVEN**)

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora e a escada de alcada de
> financas** (ver Pendencias). Os nomes abaixo sao candidatos DRAFT; o **mapeamento
> faixa→grupo→tier** exige sign-off de financas (segregacao de funcoes / politica de alcada).

| Grupo (candidate group) | Tier (DRAFT) | Tarefa | Faixa atendida (DRAFT) |
|---|---|---|---|
| `aprovacao-financeira-l1` | 1 | `UT_AprovacaoAlcada` (`decisao_pagamento ∈ {APROVAR, RECUSAR, SOLICITAR_INFO, CANCELAR}`) | `ALCADA_L1` |
| `aprovacao-financeira-l2` | 2 | `UT_AprovacaoAlcada` | `ALCADA_L2` |
| `aprovacao-financeira-l3` | 3 | `UT_AprovacaoAlcada` | `ALCADA_L3` |
| `comite-financeiro` | 4 (mais alto) | `UT_AprovacaoComite` (catch-all conservador; faixa ambigua/acima do maior tier; **aprovacao colegiada DRAFT**) | `ANALISE_HUMANA` |
| `coordenacao-financeira` | — | `UT_CoordenacaoAlcada` (SLA estourado — humano de tier **>= faixa** assume; a aprovacao continua humana e tier-adequada) | (qualquer, respeitando tier-match) |

**Binding value-driven (unico na plataforma):** a `<bpmn:userTask>` de aprovacao usa
`camunda:candidateGroups="${grupo_aprovador}"` (expressao resolvida da saida da DMN
`pagto_alcada`), em vez de um grupo estatico. Toda User Task ainda traz `camunda:candidateGroups`
(gate D1 satisfeito — o valor e dinamico, mas sempre presente e sempre de um conjunto fechado de
grupos validos). O conjunto de valores possiveis e a **allowlist fechada** de grupos da escada
(parte 3 do padrao ADR-0018): nao existe valor de grupo fora dessa lista; faixa nao mapeada →
`comite-financeiro`.

## SLAs

| Timer | Valor (DRAFT/verify financas) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaPagto`) | 60–70% de `${sla.sla_alerta}` (DMN `pagto_sla`) | nao-interruptivo → `operadora.pagto.notify_sla_risk` | politica interna |
| Aprovacao (`BT_SlaAprovacao`) | `${sla.sla_aprovacao}` (ex.: **P2D** L1 … **P5D** comite — **DRAFT/verify financas**) | interruptivo → publica `pagto.sla_breached` → cancela `UT_AprovacaoAlcada`, cria `UT_CoordenacaoAlcada` (humano de tier >= faixa assume) | politica interna |

Nota: **substitui qualquer auto-liberacao por timeout** — no estouro de SLA a coordenacao
financeira (humana, tier-adequada) assume; **nunca** ha auto-liberacao por timeout (inversao do
`Task_AutoApprove`/timeout do reference; risco #5 do phase3-plan). O tier-match e re-exigido na
tarefa de coordenacao.

## Desfecho de agente: falha de start (CC-01)

| Desfecho | Onde vive | Quem escreve | Significado |
|---|---|---|---|
| `erro_inicio_processo` | **estado do agente andre** — NAO e variavel de processo | no `notify_start_failure` do grafo, via o helper unico `maezo.runtime.start_outcome.notify_start_failure` | o agente TENTOU iniciar SP-OP-PAGTO-001 pelo chokepoint `start_process_idempotent` e o engine recusou (`CibSevenError`). NENHUMA instancia nasceu |

ONDE ESTE VALOR **NAO** ESTA, e por que. Ele nunca chega ao engine: nao consta de
`## Variaveis de entrada` nem de `## Variaveis de saida`, nao tem `bpmnError` associado, nao
aparece em nenhum `camunda:` do BPMN e **nao exige mudanca nenhuma no BPMN deste processo**. Nao
poderia ser diferente — o processo NAO nasceu, entao nao existe instancia onde gravar uma
variavel nem escopo onde lancar um erro. Ele e declarado AQUI, no contrato, porque e um desfecho
CONTRATUAL do agente que serve este processo e porque quem consome o estado do agente (o handler
A2A, um golden de eval, uma regra de alerta) precisa do literal exato e estavel.

POR QUE ELE EXISTE (auditoria de frota 2026-09-04, achado CC-01). Ate essa data a falha de start
era engolida: o `except CibSevenError` do no de start devolvia apenas `process_started=false` e a
aresta seguinte era INCONDICIONAL para um terminal no-op, de modo que o desfecho de SUCESSO ja
gravado a montante (`dossie_pronto_clerical`) sobrevivia — o estado do agente AFIRMAVA um fato que nao
aconteceu. E o caso se perdia em silencio, porque os prazos desta especificacao vivem em timers
da instancia BPMN que nunca nasceu: sem SLA, sem alerta, sem retry.

EFEITOS ASSOCIADOS ao desfecho, todos no lado do agente:

* `process_started = false` (e, quando o agente distingue no-ops legitimos, o marcador
  `start_failed = true`, que e o que a aresta condicional le);
* `record_agent_error()` -> `maezo_agent_errors_total`, o contador que a regra
  `MaezoAgentCrashLoop` observa;
* evento estruturado `agent_process_start_failed` com a business key idempotente deste contrato —
  este evento e o SUBSTITUTO operacional do prazo enquanto a instancia nao existe;
* RETRY seguro por construcao: `start_process_idempotent` e idempotente por business key, entao
  uma reentrega reencontra a instancia viva (`ALREADY_ACTIVE`) em vez de abrir uma segunda.


## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_PAYMENT_RELEASE_NOT_HUMAN` | worker-guard em `operadora.pagto.release_high_value_payment` | o worker **recusa** liberar se (a) `decisao_pagamento != APROVAR` setado por humano numa User Task, OU (b) faltarem `aprovador_id`/`justificativa_aprovacao`/`valor_aprovado_cents`, OU (c) **tier-match falhar** — `aprovador_tier` insuficiente para `faixa_valor` (ex.: aprovador L1 tentando liberar faixa `ALCADA_L3`). Lanca BPMN error; instancia nao atinge `End_PagamentoLiberadoHumano`. Registra a tentativa (ADR-0007). (espelha `ERR_*_NOT_HUMAN` de AUTH/CONTAS/REEMBOLSO **+ a verificacao extra de tier**) |
| `ERR_PAGTO_ORDEM_INVALIDA` | declarado (`Error_PagtoOrdemInvalida`) para uso dos workers | worker `validate_payment_data` lanca BPMN error se a ordem de pagamento for inconsistente/sem lastro na origem (ex.: `ordem_pagamento_id` ausente); capturado por boundary event `BE_PagtoOrdemInvalida` em `ST_ValidatePaymentData` -> `End_PagtoOrdemInvalida` (GAP-PAGTO-4; fail-safe tecnico, NAO decisao de liberacao/negativa) |

> **Tier-match — extensao do guard no-adverse (parte 4 de 5 do padrao §4-bis-F/ADR-0018):**
> diferente dos outros processos, `ERR_PAYMENT_RELEASE_NOT_HUMAN` checa **duas** condicoes alem
> da decisao humana: presenca dos campos obrigatorios E `aprovador_tier >= faixa_valor`. Mesmo
> que um bug de roteamento entregue a tarefa ao grupo errado, o worker recusa liberar acima do
> tier do aprovador. O teste de invariante (parte 5) verifica na history do engine que todo
> `End_PagamentoLiberadoHumano` co-ocorre com User Task humana concluida **e** com tier-match.

## Notas de design / inversao do reference

- **Clona o esqueleto provado de auto-aprovacao `dentro_teto` do AUTH** (`auth_auto_approval`):
  o caminho automatico produz **somente** a liberacao de baixo valor (dentro do teto L2 do
  tenant). Acima do teto, exige aprovador humano de tier adequado — analogo a `ANALISE_HUMANA`
  do AUTH, mas com **roteamento por tier** em vez de roteamento unico.
- **Value-driven candidate groups (unico na plataforma):** a DMN `pagto_alcada` emite o grupo;
  a User Task o consome via `camunda:candidateGroups="${grupo_aprovador}"`. Conjunto fechado de
  grupos; faixa nao mapeada/ambigua → `comite-financeiro` (tier mais alto). Inverte o anti-padrao
  de "auto-liberar acima de alcada" tornando a liberacao adversa inexpressavel por automacao
  (sem coluna DMN de liberacao) e irregistravel sem tier-match (guard).
- **Andre (Zona PHI/Financeira)** e convocado via `prepare_approval_dossier` para enriquecer o
  dossie de aprovacao (risco atuarial/populacional, `analytics.actuarial`/`analytics.population`);
  **instrui, nao decide** — o aprovador humano de tier adequado decide na User Task (principio
  Rafael/ADR-0005). KPI de Andre `false_pricing_decision_rate==0`.
- **TASY write DROP** em todo worker: consumimos o CDC do amh-data-platform e a obrigacao
  adjudicada do CONTAS-001; nunca escrevemos no Tasy (MEMORY/ADR-0013). A liberacao efetiva de
  caixa (CNAB/PIX/TED) e um sistema externo de tesouraria — fora do escopo de escrita no Tasy.
- **Sem PHI no payload de pagamento em Zona Geral:** credor/conta/instrumento sao tokens/refs;
  `agents.events.pagto.routed` pode bandar/omitir o valor cru. Dados bancarios crus, se houver,
  ficam em zona apropriada (ADR-0006/0017).

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e o BPMN/DMN bodies (autorados em wave posterior contra este contrato).
- **Sign-off de financas da escada de alcada completa** (`threshold_brl` L1, faixas L1/L2/L3,
  teto de auto-liberacao L2 por tenant) e do **mapeamento faixa→grupo→tier** — todos DRAFT.
- **Confirmacao dos candidate groups** (`aprovacao-financeira-l1/l2/l3`, `comite-financeiro`,
  `coordenacao-financeira`) contra a taxonomia organizacional / IdP da operadora (PO + financas).
- Politica de **segregacao de funcoes**: o aprovador nao pode ser o solicitante da ordem; comite
  pode exigir aprovacao colegiada (multiplas User Tasks / quorum) — a detalhar.
- Confirmacao de que `aprovacao colegiada` do comite e modelada como quorum (parallel/multi-instance
  User Task) vs aprovador unico de tier maximo.
- Interacao com SP-OP-CONTAS-001 (conta adjudicada → ordem de pagamento, via `operadora.contas.handoff_pagamento` desde ADR-0040) e prazos de pagamento a
  prestador (contratuais — **DRAFT/verify**).
- Politica de duplicidade/idempotencia de tesouraria (evitar dupla liberacao) e reconciliacao
  CNAB/PIX/TED com o sistema de tesouraria externo.
