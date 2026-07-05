# Test spec — SP-OP-ADEQUACAO-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (ADR-0011: sem mock de
engine) + testes **ESTATICOS** (varredura do XML das DMN/BPMN, sem engine — rodam no lane unit/CI
rapido). Arquivo alvo: `tests/integration/processes/test_sp_op_adequacao_001.py`. O modulo
**self-contains** suas fixtures (nao edita o `conftest.py` compartilhado).

Dados sinteticos: regiao `REGIAO-TESTE-NNNN`, especialidade `cardiologia`, tenant `amh`, ciclo
`2026-Q2`. Business key `ADEQ-amh-{regiao}-{especialidade}-{ciclo}`. Process key:
`SP-OP-ADEQUACAO-001` (exato — nao alterar).

`AdequacaoEngineProbe` drena as external tasks com os workers reais Phase-3
(`register_adequacao_workers`) + `register_phase0_workers`. `WorkerHarness` **nao** retorna
variaveis de saida de roteamento ao engine; os fatos apurados (`tempo_acesso_apurado_min`,
`distancia_apurada_km`, `prestadores_disponiveis`, `cobertura_geo_suficiente`,
`dados_geo_completos`, `tipo_carater`) sao seeded como variaveis de **START** — a DMN
`adequacao_gap`/`adequacao_remediation_routing` computa gap/roteamento a partir deles.

## Invariante no-adverse (DoD deliverable) — adequacao_fallback_commitment (ADR-0018, processo L3)

Espelha `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` §Invariante: processo
**majoritariamente L3** (monitoramento autonomo), mas com **um** efeito adverso human-gated: o
compromisso financeiro de fallback (livre escolha / reembolso garantido / contratacao ad-hoc).

### test_nenhum_caminho_automatizado_firma_compromisso_fallback
- **Given** varredura de cenarios apurados cobrindo `CONFORME` / `GAP_LEVE` / `GAP_MODERADO` /
  `GAP_CRITICO` × `dados_geo_completos ∈ {true,false}` (6 cenarios representativos: conforme,
  gap leve, gap moderado, gap critico por urgencia, dados incompletos, gap critico sem prestador)
- **When** cada instancia percorre ate estabilizar, **sem completar nenhuma User Task**
- **Then** NENHUMA atinge `End_CompromissoFallbackHumano` automaticamente. Os caminhos L3
  (`CONFORME`/`MONITORAR`/`ENCAMINHAR_CREDENCIAMENTO`) atingem terminais **NEUTROS**
  autonomamente (sem User Task); so `GAP_CRITICO`/dados incompletos criam `UT_DecisaoFallback`.
  Prova via `_assert_no_adverse_without_human_task` (history-based).

### test_gap_critico_roteia_para_humano_nao_firma_compromisso
- **Given** `prestadores_disponiveis=0`, `dados_geo_completos=true` → `GAP_CRITICO`
- **Then** fluxo chega a `UT_DecisaoFallback` (`gestao-rede`) — **nao** a
  `End_CompromissoFallbackHumano` automaticamente.

### test_dados_incompletos_roteia_para_humano
- **Given** `dados_geo_completos=false` (mesmo com metrica favoravel)
- **Then** `roteamento_remediacao=ANALISE_HUMANA` (catch-all) — nunca decide compromisso com dados
  incompletos; chega a `UT_DecisaoFallback`.

## Caminhos L3 autonomos (sem User Task)

### test_l3_conforme_atinge_neutro_sem_user_task
- **Given** rede conforme (dentro RN 259: tempo/distancia/prestadores favoraveis)
- **Then** `adequacao_gap=CONFORME` → `MONITORAR` → fim `End_AdequacaoConforme`; **NENHUMA** User
  Task; `adequacao.completed` (`desfecho=conforme`); `register_fallback_commitment` NUNCA invocado.

### test_l3_gap_moderado_encaminha_credenciamento_sem_user_task
- **Given** `GAP_MODERADO` (tempo/distancia/prestadores intermediarios)
- **Then** `roteamento_remediacao=ENCAMINHAR_CREDENCIAMENTO` → handoff `operadora.adequacao.
  start_credenciamento` (dispara SP-OP-CRED-001 — **nao** e adverso, credenciar nao onera); fim
  `End_RemediacaoEncaminhada`; **NENHUMA** User Task; `adequacao.gap_detected`
  (`gap_adequacao=GAP_MODERADO`).

### test_l3_gap_leve_monitora_sem_user_task
- **Given** `GAP_LEVE`
- **Then** `roteamento_remediacao=MONITORAR` → `update_monitoring_plan` + `notify_rede`; fim
  `End_MonitoramentoAtualizado`; **NENHUMA** User Task.

## Happy path adverso (so via humano) + handoff via decisao humana

### test_happy_path_compromisso_fallback_humano
- **Given** `GAP_CRITICO` → `UT_DecisaoFallback` (`gestao-rede`)
- **When** humano completa `decisao_remediacao=COMPROMISSO_FALLBACK` + `tipo_fallback` +
  `justificativa_fallback` + `referencia_regulatoria` + `responsavel_id` (**GAP-ADEQ-1, #94: sem
  `tier`** — nenhuma UT o coleta, o contrato nao o declara como variavel de saida; e opcional)
- **Then** `operadora.adequacao.register_fallback_commitment` executado (guard satisfeito); fim
  `End_CompromissoFallbackHumano` — **UNICO** caminho ao terminal adverso via esta UT direta;
  `adequacao.completed` (`desfecho=compromisso_fallback_humano`); `responsavel_id` na trilha de
  auditoria.

### test_humano_encaminhar_cred_nao_firma_compromisso
- **Given** `UT_DecisaoFallback` aberta (`GAP_CRITICO`)
- **When** humano completa `decisao_remediacao=ENCAMINHAR_CRED`
- **Then** fim `End_RemediacaoEncaminhada` (sem compromisso); `register_fallback_commitment` NUNCA
  invocado.

## Coordenacao (SLA estourado) — segunda via humana ao terminal adverso

### test_timer_sla_estourado_coordenacao_assume
- **Given** `UT_DecisaoFallback` aberta alem de `${adequacao_sla.sla_remediacao}`
- **When** job do timer interruptivo `BT_SlaRemediacao` executado
- **Then** `adequacao.sla_breached` publicado; `UT_CoordenacaoRede` criada (`coordenacao-rede`);
  decisao continua humana; nenhum auto-compromisso por timeout.

### test_coordenacao_assume_e_firma_compromisso_fallback_humano
- **Given** SLA de remediacao estourado; `UT_CoordenacaoRede` aberta (**segunda** via humana ao
  mesmo terminal adverso — o gateway `GW_DecisaoRemediacao` recebe fluxo tanto de
  `UT_DecisaoFallback` quanto de `UT_CoordenacaoRede`)
- **When** coordenacao humana (`coordenacao-rede`) completa `decisao_remediacao=COMPROMISSO_FALLBACK`
  + os mesmos campos obrigatorios de `UT_DecisaoFallback` (a decisao adversa **nunca** muda de
  natureza por estouro de SLA — contrato §Papeis humanos)
- **Then** ANTES de completar: o terminal adverso ainda **nao** foi atingido so pelo estouro de SLA
  (prova negativa). DEPOIS de completar: `operadora.adequacao.register_fallback_commitment`
  executado (guard satisfeito, `responsavel_id` da coordenacao); fim `End_CompromissoFallbackHumano`;
  `adequacao.completed` (`desfecho=compromisso_fallback_humano`). **GAP-ADEQ-2:** fecha o gap
  explicitamente deferido do Wave-0 — prova por engine real que `End_CompromissoFallbackHumano`
  e alcancavel **por qualquer uma** das duas User Tasks humanas (`UT_DecisaoFallback` OU
  `UT_CoordenacaoRede`) e **nunca** por nenhum outro caminho (mirror de
  `test_sp_op_cred_001.py::test_coordenacao_assume_e_descredencia`).

## Idempotencia

### test_business_key_uma_instancia_por_celula
- **Given** instancia ativa `ADEQ-amh-{regiao}-cardiologia-2026-Q2`
- **When** re-disparo (novo fato de rede no mesmo ciclo/celula)
- **Then** sem segunda instancia ativa (`start_process` consulta a business key e retorna a
  existente).

## DMN — shape e fail-safe (sem engine; varredura estatica do XML)

### test_adequacao_gap_sem_saida_que_compromete
- **Then** dominio de `gap_adequacao` **exatamente** `{CONFORME, GAP_LEVE, GAP_MODERADO,
  GAP_CRITICO}` (catch-all → `GAP_CRITICO`, conservador); dominio de `roteamento_remediacao`
  **exatamente** `{MONITORAR, ENCAMINHAR_CREDENCIAMENTO, ANALISE_HUMANA}` (catch-all →
  `ANALISE_HUMANA`); nenhuma saida `COMPROMETER`/`GARANTIR`/`CONTRATAR`/`GARANTIR_REEMBOLSO`.

### test_adequacao_sla_sem_saida_adversa
- **Then** `adequacao_sla` so produz prazos ISO 8601 + `fonte_regulatoria`; nenhuma saida adversa.

### test_dmn_typeref_allowlist
- **Then** toda DMN (`adequacao_gap`, `adequacao_sla`) usa `typeRef ∈ {string, boolean, integer,
  long, double, date}` — `number` proibido (`distancia_apurada_km`→`double`;
  `tempo_acesso_apurado_min`/`prestadores_disponiveis`→`integer`).

### test_bpmn_compromisso_so_apos_user_task_humana
- **Given** prova de alcancabilidade estatica a partir do `startEvent`, tratando cada `userTask`
  humana como semi-absorvente (so escapa por boundary event) — espelha
  `test_no_denial_consolidated.py` (engine-free)
- **Then** `End_CompromissoFallbackHumano` **nao** e alcancavel sem cruzar uma `userTask` humana;
  ao menos um terminal neutro L3 (`End_AdequacaoConforme`/`End_MonitoramentoAtualizado`/
  `End_RemediacaoEncaminhada`) e alcancavel autonomamente.
