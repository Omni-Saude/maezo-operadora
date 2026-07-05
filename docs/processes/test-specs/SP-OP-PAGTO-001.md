# Test spec — SP-OP-PAGTO-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (ADR-0011: sem mock de
engine). Arquivo alvo: `tests/integration/processes/test_sp_op_pagto_001.py`. O modulo
**self-contains** suas fixtures, espelhando `test_sp_op_cancel_001.py`/`test_sp_op_fraude_001.py`.

Dados sinteticos: ordem `ORDEM-TESTE-NNNN`, prestador pseudonimo `prov:teste-0001`, conta
`conta:teste-origem`, tenant `amh`. Business key `PAGTO-amh-{ordem_pagamento_id}`. Process key:
`SP-OP-PAGTO-001` (exato — nao alterar). Valores em centavos de BRL (**int64/long — nunca
`number`**; centavos de alto valor excedem o int32 do Java — R$ 50MM = 5.000.000.000 centavos >
2.147.483.647 = max Integer — o helper de start os tipa como Camunda Long). Nenhum dado bancario cru
(ADR-0006).

`PagtoEngineProbe` drena as external tasks com os workers reais Phase-3 (`register_pagto_workers`) +
`register_phase0_workers`. Diferente de CRED/ADEQUACAO, `WorkerHarness` (Phase 3) **PROPAGA** as
variaveis de saida dos handlers ao completar a external task; ainda assim, os FATOS DE ROTEAMENTO
que a DMN `pagto_alcada` le (`valor_pagamento_cents`, `dentro_teto_l2`) sao **seeded** como
variaveis de START — o `BRT_AlcadaRouting` roda ANTES do worker `calculate_facts` completar.

## Invariante L1 (DoD deliverable) — high_value_payment / no-adverse (parte 5 de 5, ADR-0018)

Espelha `docs/processes/contracts/SP-OP-PAGTO-001.md` §Invariante L1: a liberacao de um pagamento
de alto valor so nasce numa User Task humana **com tier de alcada compativel** — o **UNICO**
processo da plataforma com guard de **tier-match** alem de `ERR_*_NOT_HUMAN`.

### test_nenhum_pagamento_acima_teto_auto_libera
- **Given** varredura de combinacoes de input da DMN `pagto_alcada`: `valor_pagamento_cents ∈
  {5MM (R$50k, abaixo teto), 30MM (R$300k, L1), 120MM (R$1.2MM, L2), 500MM (R$5MM, L3), 2.000MM
  (R$20MM, acima do maior tier)}` × `dentro_teto_l2 ∈ {true,false}` × `tipo_pagamento ∈
  {prestador_rede, reembolso_beneficiario}` (5 × 2 × 2 = **20 combinacoes**)
- **When** cada instancia percorre ate estabilizar, **sem completar nenhuma User Task**
- **Then** NENHUMA atinge `End_PagamentoLiberadoHumano` (terminal adverso) automaticamente; valor
  alto NAO produz liberacao — so roteia ao grupo aprovador correto (UT humana); **apenas**
  `valor<=teto` **e** `dentro_teto_l2=true` pode auto-liberar (`End_PagamentoLiberadoAutomatico`,
  clerical, neutro). Prova por historia do engine (`_assert_no_adverse_without_human_task`).

## Gate de admissibilidade (GAP-PAGTO-1, #98) — precede a escada de alcada

`pagto_admissibility` (DMN) roda ANTES de `pagto_alcada` (ADR-0012); registrada em
`config/artifact_signoff.yaml` como `intentional_draft`. Fail-safe catch-all → `ANALISE_HUMANA`;
nenhuma saida libera pagamento.

### test_gate_admissibilidade_precede_alcada_no_happy_path
- **Given** ordem seed valida (`dados_pagamento_validos=true`, `lastro_confirmado=true`,
  `duplicidade_suspeita=false`)
- **Then** `BRT_PagtoAdmissibility` E `BRT_AlcadaRouting` executam, o de admissibilidade **antes**
  (prova via `history_decision_instances`); fluxo segue normalmente ate a UT de aprovacao
  value-driven.

### test_duplicidade_suspeita_roteia_humano_nunca_alcada
- **Given** `duplicidade_suspeita=true`
- **Then** `UT_AnaliseAdmissibilidade` (`coordenacao-financeira`) criada; `BRT_AlcadaRouting`
  **NAO** executa; `pagto.routed` **nao** publicado; nenhum terminal adverso/auto-liberacao.

### test_lastro_nao_confirmado_nunca_auto_libera_clerical
- **Given** `valor_pagamento_cents=85_000` (abaixo do teto), `dentro_teto_l2=true`,
  `lastro_confirmado=false` — **prova-mestra do guardrail**: sem o gate, esta ordem
  AUTO-LIBERARIA pelo caminho clerical L2
- **Then** o gate desvia para `UT_AnaliseAdmissibilidade`; `End_PagamentoLiberadoAutomatico`
  **NAO** atingido; `BRT_AlcadaRouting` nao executa; `release_low_value_payment` NUNCA invocado.

### test_admissibilidade_devolver_registra_recusa_humana
- **Given** `UT_AnaliseAdmissibilidade` aberta (duplicidade suspeita)
- **When** humano completa `decisao_admissibilidade=DEVOLVER`
- **Then** default conservador reusa o terminal de recusa humana
  (`register_payment_refusal` → `End_PagamentoRecusadoHumano`); nenhuma liberacao;
  `BRT_AlcadaRouting`/`ST_ReleaseHighValue` nunca alcancados.

## Value-driven candidate group routing (coracao do processo)

A DMN `pagto_alcada` emite `grupo_aprovador` que alimenta
`camunda:candidateGroups="${grupo_aprovador}"` — binding **unico na plataforma** dirigido por valor.

### test_valor_dirige_candidate_group (parametrizado)
- **Given/Then** `30_000_000→aprovacao-financeira-l1/ALCADA_L1`,
  `120_000_000→aprovacao-financeira-l2/ALCADA_L2`, `500_000_000→aprovacao-financeira-l3/ALCADA_L3`,
  `2_000_000_000→comite-financeiro/ANALISE_HUMANA` (catch-all); `pagto.routed` publicado
  (`faixa_valor`, `grupo_aprovador`); roteamento nunca produz terminal adverso (so cria a UT).

### test_catchall_conservador_acima_do_maior_tier
- **Given** `valor_pagamento_cents=5_000_000_000` (acima do maior tier configurado)
- **Then** catch-all conservador → `comite-financeiro`; NUNCA auto-libera (risco #5 do
  phase3-plan: "se o ramo L2 abaixo-do-teto for mal-escopado, pagamentos acima de alcada
  auto-liberam" — este catch-all e a defesa).

## Caminho clerical L2 (abaixo do teto) — auto-liberacao neutra

### test_abaixo_do_teto_auto_libera_clerical
- **Given** `valor_pagamento_cents=85_000`, `dentro_teto_l2=true`
- **Then** `End_PagamentoLiberadoAutomatico` (neutro, analogo a `auth_auto_approval`);
  `pagto.completed` (`desfecho=liberado_automatico`); `release_high_value_payment` NUNCA invocado.

## Happy paths (decisao humana em UT_AprovacaoAlcada)

### test_happy_path_aprovar_libera_humano
- **Given** `UT_AprovacaoAlcada` (`aprovacao-financeira-l2`, faixa `ALCADA_L2`)
- **When** aprovador de **tier 2** (compativel) completa `decisao_pagamento=APROVAR` +
  `justificativa_aprovacao` + `valor_aprovado_cents` + `aprovador_id` + `aprovador_tier`
- **Then** `operadora.pagto.release_high_value_payment` executado (guard + tier-match satisfeitos);
  fim `End_PagamentoLiberadoHumano`; `pagto.completed` (`desfecho=liberado_humano`,
  `aprovador_id`+`aprovador_tier` na trilha de auditoria).

### test_happy_path_recusar_humano
- **Given** `UT_AprovacaoAlcada` aberta
- **When** aprovador completa `decisao_pagamento=RECUSAR` + `justificativa_recusa`
- **Then** `register_payment_refusal` executado; fim `End_PagamentoRecusadoHumano` (neutro,
  **NAO** e glosa — glosa nasce em CONTAS-001); `release_high_value_payment` NUNCA invocado.

## Tier-match guard (a verificacao EXTRA — exclusiva deste processo)

### test_tier_insuficiente_nao_libera
- **Given** `UT_AprovacaoAlcada` para faixa `ALCADA_L3` (tier minimo 3)
- **When** aprovador de **tier 1** (insuficiente) completa `APROVAR` + todos os demais campos
- **Then** o worker `release_high_value_payment` **recusa** liberar (tier-match falhou, defesa em
  profundidade alem do roteamento); a instancia **NAO** atinge `End_PagamentoLiberadoHumano`;
  `pagto.completed` adverso **NAO** publicado.

### test_aprovar_exige_campos
- **Given** `UT_AprovacaoAlcada` aberta
- **When** humano completa `APROVAR` **sem** `justificativa_aprovacao`/`valor_aprovado_cents`/
  `aprovador_id`/`aprovador_tier`
- **Then** `ERR_PAYMENT_RELEASE_NOT_HUMAN`; a instancia **NAO** atinge `End_PagamentoLiberadoHumano`
  (defesa em profundidade — espelha `test_acusar_fraude_exige_campos`).

## SLA estourado — coordenacao humana assume (NUNCA auto-libera por timeout)

### test_sla_estourado_coordenacao_assume_nunca_auto_libera
- **Given** `UT_AprovacaoAlcada` aberta alem de `${pagto_sla.sla_aprovacao}`
- **When** job do timer interruptivo `BT_SlaAprovacao` executado
- **Then** `pagto.sla_breached` publicado; `UT_CoordenacaoAlcada` criada (`coordenacao-financeira`);
  nenhum desfecho adverso automatico (INVERTE `Task_AutoApprove`/timeout). Coordenacao humana
  (tier compativel) decide `APROVAR` → liberacao **continua** humana → fim
  `End_PagamentoLiberadoHumano`.

### test_alerta_sla_nao_interruptivo_notifica
- **Given** `UT_AprovacaoAlcada` aberta
- **When** job do timer nao-interruptivo `BT_AlertaSlaPagto` executado
- **Then** `notify_sla_risk` recebeu task; fim `End_RiscoSlaNotificado` (informativo); a UT de
  aprovacao **continua aberta** (nao-interruptivo nao cancela); nenhum terminal adverso.
