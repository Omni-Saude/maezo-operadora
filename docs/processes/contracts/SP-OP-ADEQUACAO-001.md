# Contrato — SP-OP-ADEQUACAO-001 (Adequacao Geografica de Rede)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review (medico-auditor/juridico/DPO/regulatorio/financas/PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 (Wave W-C; modelado uma fase a frente — playbook 5-bis) · **BPMN (alvo, autorado em wave posterior):** `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`
**Gatilho regulatorio:** **RN 259/2011** (garantia de atendimento — **tempos e distancias maximas de acesso** a rede assistencial) e consolidacoes posteriores ANS (**DRAFT/verify** com regulatorio — RN 259 pode ter sido consolidada/atualizada); RN 566 (dimensionamento/regras de rede — **DRAFT/verify**); Lei 9.656/1998. Disponibilidade de rede adequada e obrigacao da operadora; a falha de adequacao dispara remediacao e, no limite, **compromisso financeiro de fallback** (livre escolha / reembolso garantido).

> **Processo majoritariamente L3 (monitoramento autonomo com telemetria — ADR-0008), MAS com um
> efeito adverso human-gated:** o **compromisso financeiro de fallback** (garantir livre escolha /
> reembolso ao beneficiario quando a rede nao atende os tempos/distancias RN 259) e uma obrigacao
> financeira da operadora e DEVE nascer numa User Task humana — mesmo num processo cujo grosso e
> L3. "Menos obvio como negativa-like, mas o compromisso de fallback e adverso (custo) e segue o
> padrao das cinco partes" (phase3-plan §processos, linha ADEQUACAO).

## Invariante — no-adverse no compromisso de fallback (ADR-0018, mesmo num processo L3)

O **registro de um compromisso financeiro de fallback** (`register_fallback_commitment` —
garantir livre escolha / reembolso garantido / contratacao ad-hoc com custo para a operadora)
so nasce numa User Task humana (`UT_DecisaoFallback`, ou `UT_CoordenacaoRede` no estouro de SLA),
com `decisao_remediacao == COMPROMISSO_FALLBACK` setado por humano do grupo `gestao-rede`
(ou `coordenacao-rede`). Nenhuma DMN deste processo possui saida que **firme/autorize** o
compromisso financeiro: a DMN `adequacao_gap` apenas **mede o gap de adequacao e classifica a
severidade/roteamento de remediacao**; ela nunca produz `COMPROMETER`/`GARANTIR_REEMBOLSO`/
`CONTRATAR`. Gap ambiguo, severidade alta, dados de geolocalizacao insuficientes,
indisponibilidade da DMN e estouro de SLA **todos fail-safe para a User Task humana** (catch-all →
`roteamento_remediacao = ANALISE_HUMANA`). O efeito adverso (custo) e materializado **apenas**
pelo worker `operadora.adequacao.register_fallback_commitment`, guardado por
`ERR_FALLBACK_COMMITMENT_NOT_HUMAN`. O terminal `End_CompromissoFallbackHumano` so e alcancavel
apos a User Task humana concluida.

**Caminhos L3 que NAO sao adversos** (autonomos com telemetria — sem User Task): medir cobertura
geografica, detectar gap, abrir/atualizar plano de monitoramento, notificar a area de rede,
disparar busca de prestador para credenciamento (handoff a SP-OP-CRED-001), publicar metrica de
adequacao. **Nenhum desses compromete caixa nem nega atendimento** — sao monitoramento/alerta. So
o **compromisso financeiro** (e qualquer decisao que onere a operadora ou afete o beneficiario)
passa pela User Task.

## Terminais (humano-gated vs neutros)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_CompromissoFallbackHumano` | **ADVERSO (custo/compromisso financeiro)** — livre escolha / reembolso garantido / contratacao ad-hoc | `UT_DecisaoFallback` / `UT_CoordenacaoRede` com `decisao_remediacao=COMPROMISSO_FALLBACK` humano |
| `End_RemediacaoEncaminhada` | neutro — gap encaminhado para credenciamento (handoff a SP-OP-CRED-001) sem compromisso de caixa | caminho L3 (DMN `roteamento_remediacao=ENCAMINHAR_CREDENCIAMENTO`) ou User Task |
| `End_AdequacaoConforme` | neutro — rede dentro dos tempos/distancias RN 259; nada a remediar | caminho L3 (`adequacao_gap` sem gap) |
| `End_MonitoramentoAtualizado` | neutro — plano de monitoramento atualizado; segue observando (gap leve sob acompanhamento) | caminho L3 |

O terminal ADVERSO `End_CompromissoFallbackHumano` NUNCA aparece na history do engine sem uma
User Task humana concluida (teste de invariante — ver test-spec). Nenhum fim ocorre sem evento de
dominio publicado antes (auditoria dupla engine+Kafka, ADR-0007).

## Dependencia: fato de mudanca de rede do SP-OP-CRED-001 (HARMONIZADO — GAP-XPROC-2)

Este processo **consome o fato de mudanca de rede** produzido pelo SP-OP-CRED-001
((des)credenciamento). O STUB original foi **harmonizado** com o CRED-001 real (GAP-XPROC-2,
fechando o item W-E "CRED→ADEQUACAO fato de rede"): o consumo NAO e um message-start no BPMN, e sim
uma **ponte de runtime** — a `network_change_bridge`
(`src/maezo/platform/integrations/network_change_bridge/`) consome o topico Kafka e INICIA esta
avaliacao via `mcp-cibseven.start_process` (mesmo padrao da `notifications_bridge`, idempotente,
fail-closed na allowlist). O `Start_AvaliacaoAdequacao` permanece um none-start (nascido pela
ponte) — sem message event no BPMN.

**Fato REAL `agents.events.cred.network_changed`** (harmonizado; contrato SP-OP-CRED-001 §Topicos):

| Campo | Tipo | Descricao |
|---|---|---|
| `tenant_id` | string | Tenant |
| `tipo_mudanca` | string | `prestador_credenciado` \| `prestador_descredenciado` \| `substituicao_registrada` (literal do ST_PublishNetwork* de CRED; substitui o `change_type` do STUB) |
| `prestador_id` | string | Prestador afetado (dado cadastral PJ/PF — nao PHI de beneficiario) |
| `tipo_prestador` | string | `pessoa_fisica` \| `clinica` \| `hospital` \| `laboratorio` \| `sadt` \| `opme` |
| `especialidade` | string | Especialidade/servico da celula afetada (taxonomia TUSS/CBO — DRAFT) |
| `regiao_saude` | string | Regiao de saude / area de atuacao (granularidade geografica RN 259 — DRAFT; substitui o `municipio_ibge` do STUB — **nunca endereco cru de beneficiario**, ADR-0006) |
| `data_efeito_iso` | string | Data de efeito (`YYYY-MM-DD`; substitui o `effective_date` do STUB) — a ponte deriva `ciclo_avaliacao` (trimestre `YYYY-Qn`) dela |
| `_business_key` | string | bk da instancia CRED de origem (injetada pelo worker generico de publish) — vira `network_change_ref` (substitui o `event_id` do STUB; a idempotencia do consumo vem da bk deterministica de ADEQUACAO, nao de um event_id) |

> O consumo e via a **ponte de runtime `network_change_bridge`**, correlacionado por
> `tenant_id`+`regiao_saude`+`especialidade`; `ciclo_avaliacao` = trimestre de `data_efeito_iso`
> (`YYYY-Qn`, deterministico — a reentrega do mesmo fato reconverge para a MESMA business key /
> instancia, sem duplo start). As variaveis de start sao SO a identidade da celula
> (`tenant_id`/`regiao_saude`/`especialidade`/`ciclo_avaliacao`) + `gatilho=mudanca_rede` +
> `network_change_ref`; os fatos geograficos (tempo/distancia/contagem/cobertura) sao resolvidos
> pelo worker `measure_coverage` do PROPRIO ADEQUACAO — na ausencia, a DMN `adequacao_gap` cai no
> catch-all `GAP_CRITICO` → `ANALISE_HUMANA` (nunca compromisso automatico). Fato sem
> `regiao_saude`/`especialidade`/`data_efeito_iso` derivavel e NO-OP fail-closed na ponte (nunca
> cria business key incompleta). A **periodicidade do ciclo** (trimestre vs mes) e **DRAFT/verify**
> contra RN 259/566 — docs/review-queue.md.

## Business key (idempotencia)

```
ADEQ-{tenant_id}-{regiao_saude}-{especialidade}-{ciclo_avaliacao}
```

`ciclo_avaliacao` = janela de avaliacao (ex.: `YYYY-MM` ou `YYYY-Qn`, **DRAFT/verify** contra a
periodicidade RN 259/566). Uma instancia ativa por celula (regiao × especialidade × ciclo);
re-disparo (novo fato de rede no mesmo ciclo/celula) retorna/atualiza a instancia ativa
(`mcp-cibseven.start_process` consulta a business key antes de iniciar — start idempotente, sem
duplicar avaliacao de adequacao da mesma celula).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `regiao_saude` | string | sim | Regiao de saude / area de atuacao avaliada (chave de negocio) |
| `especialidade` | string | sim | Especialidade/servico avaliado (taxonomia TUSS/CBO — DRAFT) |
| `ciclo_avaliacao` | string | sim | Janela de avaliacao (`YYYY-MM`/`YYYY-Qn` — compoe a business key) |
| `gatilho` | string | sim | `mudanca_rede` (fato CRED) \| `monitoramento_periodico` \| `reclamacao_beneficiario` \| `auditoria_ans` |
| `network_change_ref` | string | nao | Ref do `agents.events.cred.network_changed` que disparou (quando `gatilho=mudanca_rede`) — a `network_change_bridge` preenche com a bk da instancia CRED de origem (`_business_key` do fato) |
| `tempo_acesso_apurado_min` | integer | sim* | Pre-resolvido por worker: tempo de acesso apurado a especialidade na regiao, em **minutos** (inteiro — nunca `number`) |
| `distancia_apurada_km` | double | sim* | Pre-resolvido por worker: distancia ate o prestador mais proximo, em **km** (`double`) |
| `prestadores_disponiveis` | integer | sim* | Pre-resolvido por worker: contagem de prestadores ativos da especialidade na regiao |
| `cobertura_geo_suficiente` | boolean | sim* | Pre-resolvido por worker (geoanalise): cobertura atende dimensionamento RN 566 (DRAFT) |
| `dados_geo_completos` | boolean | sim* | Pre-resolvido por worker: dados de geolocalizacao/rede suficientes para avaliar |
| `tipo_carater` | string | sim | `eletivo` \| `urgencia_emergencia` (tempos maximos diferem — RN 259) |

\* Pre-resolvido por worker de fatos (geoanalise/contagem) antes de `BRT_AdequacaoGap` (medicao/aritmetica geografica; **sem decisao de compromisso financeiro**). Geografia em granularidade de regiao/municipio — **nunca endereco cru de beneficiario** (ADR-0006).

> **Numeros nunca como `number`.** `tempo_acesso_apurado_min`/`prestadores_disponiveis` = `integer`;
> `distancia_apurada_km` = `double`. A allowlist de shape DMN (§4-bis-A, ADR-0018 parte 2) torna
> `number` invalido por construcao.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `gap_adequacao` | string | `CONFORME` \| `GAP_LEVE` \| `GAP_MODERADO` \| `GAP_CRITICO` (severidade medida pela DMN `adequacao_gap`; **mede, nao decide compromisso**) |
| `roteamento_remediacao` | string | `MONITORAR` \| `ENCAMINHAR_CREDENCIAMENTO` \| `ANALISE_HUMANA` (roteamento; **sem variante COMPROMETER/GARANTIR**) |
| `decisao_remediacao` | string | `MONITORAR_OK` \| `ENCAMINHAR_CRED` \| `COMPROMISSO_FALLBACK` \| `SOLICITAR_INFO` (preenchida SO por User Task humana quando ha decisao adversa) |
| `tipo_fallback` | string | **Obrigatoria se `COMPROMISSO_FALLBACK`** — `livre_escolha` \| `reembolso_garantido` \| `contratacao_ad_hoc` |
| `justificativa_fallback` | string | **Obrigatoria se `COMPROMISSO_FALLBACK`** — fundamentacao do compromisso (gap apurado, RN 259, impacto ao beneficiario) |
| `referencia_regulatoria` | string | **Obrigatoria se `COMPROMISSO_FALLBACK`** — RN citada (ex.: RN 259 — **DRAFT/verify**) |
| `estimativa_custo_cents` | integer | Estimativa de custo do compromisso, em centavos BRL (inteiro — nunca `number`; informativa, registrada na auditoria) |
| `responsavel_id` | string | Humano que decidiu o compromisso (cadeia de auditoria ADR-0007); carregado no worker do efeito adverso |
| `decisao_coordenacao` | string | `assumir_decisao` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano `coordenacao-rede`) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado). Contexto = `adequacao`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.cred.network_changed` | **consome (start via `network_change_bridge`)** | **HARMONIZADO (GAP-XPROC-2)** — mudanca de rede dispara avaliacao de adequacao da celula afetada; a ponte de runtime correlaciona por `tenant_id`+`regiao_saude`+`especialidade` e inicia com `gatilho=mudanca_rede` (bk `ADEQ-{tenant}-{regiao}-{especialidade}-{ciclo}`; DLQ `agents.events.cred.network_changed.bridge.dlq`) |
| Kafka | `agents.events.adequacao.received` | produz | apos start (avaliacao de adequacao iniciada) |
| Kafka | `agents.events.adequacao.gap_detected` | produz | gap de adequacao detectado (payload `gap_adequacao`, `regiao_saude`, `especialidade`; **sem PHI**) (payload: `tenant_id`, `regiao_saude`, `especialidade`, `gap_adequacao`; publicado por `ST_PublishGapDetected` SOMENTE quando `gap_adequacao != CONFORME`) |
| Kafka | `agents.events.adequacao.sla_breached` | produz | SLA de remediacao estourado |
| Kafka | `agents.events.adequacao.completed` | produz | fim (payload.desfecho = `conforme` \| `monitoramento_atualizado` \| `encaminhada_credenciamento` \| `compromisso_fallback_humano`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso) |
| External task | `operadora.adequacao.measure_coverage` | consome (worker) | geoanalise: apura tempo/distancia/contagem de prestadores (fatos; **sem decisao**) |
| External task | `operadora.adequacao.calculate_gap` | consome (worker) | aritmetica pura: compara apurado vs thresholds RN 259/566 (inteiros/`double` — **nunca `number`**) |
| External task | `operadora.adequacao.update_monitoring_plan` | consome (worker) | **GAP-ADEQ-6** — abre/atualiza o plano de monitoramento da celula (NEUTRO; monitoramento/alerta; sem efeito adverso) — roteamento `MONITORAR` (`CONFORME`/`GAP_LEVE`) |
| External task | `operadora.adequacao.notify_rede` | consome (worker) | **GAP-ADEQ-6** — alerta a area de rede (`gestao-rede`) sobre o gap sob acompanhamento (NEUTRO; informativo) — mesmo roteamento `MONITORAR` |
| External task | `operadora.adequacao.prepare_remediation_dossier` | consome (worker→A2A) | monta dossie de remediacao para a User Task (impacto, alternativas; delegacao `analytics.population` a Andre — **instrui, nao decide**) |
| External task | `operadora.adequacao.start_credenciamento` | consome (worker) | **handoff** — dispara SP-OP-CRED-001 para credenciar prestador na celula (sem compromisso de caixa) |
| External task | `operadora.adequacao.notify_sla_risk` | consome (worker) | alerta `gestao-rede`/`coordenacao-rede` (timer nao-interruptivo) |
| External task | `operadora.adequacao.register_fallback_commitment` | consome (worker) | **efeito adverso gated** — registra o compromisso financeiro de fallback; recusa sem decisao humana (`ERR_FALLBACK_COMMITMENT_NOT_HUMAN`); carrega `responsavel_id` |
| Message BPMN | `msg.adequacao.rede_atualizada` | recebe | correlacao por business key — nova mudanca de rede / credenciamento concluido chegou, destrava reavaliacao |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A, ADR-0018 parte 2): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; distancia → `double`; tempo/contagem → `integer`; dias/SLA → string ISO 8601; BRL (estimativa) → inteiro-centavos. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida que firme/autorize compromisso financeiro.**

### `adequacao_gap` (hitPolicy FIRST — DRAFT; thresholds RN 259 **DRAFT/verify regulatorio**)
in: `tipo_carater: string`, `tempo_acesso_apurado_min: integer`, `distancia_apurada_km: double`, `prestadores_disponiveis: integer`, `cobertura_geo_suficiente: boolean`
out: `gap_adequacao: string` (`CONFORME` | `GAP_LEVE` | `GAP_MODERADO` | `GAP_CRITICO`), `motivo: string`
**Apenas mede a severidade do gap — sem saida que comprometa caixa.** Thresholds de tempo/distancia maximos por `tipo_carater` (eletivo vs urgencia) sao **DRAFT/verify** contra RN 259 vigente. Catch-all (dados insuficientes/combinacao nao coberta) → `GAP_CRITICO` (conservador → humano a jusante).

### `adequacao_remediation_routing` (hitPolicy FIRST — DRAFT)
in: `gap_adequacao: string`, `dados_geo_completos: boolean`
out: `roteamento_remediacao: string` (`MONITORAR` | `ENCAMINHAR_CREDENCIAMENTO` | `ANALISE_HUMANA`), `motivo: string`
- `CONFORME` → `MONITORAR` (L3, sem User Task).
- `GAP_LEVE` → `MONITORAR` (acompanhamento; L3).
- `GAP_MODERADO` → `ENCAMINHAR_CREDENCIAMENTO` (handoff CRED; L3 — credenciar nao e adverso).
- `GAP_CRITICO` → `ANALISE_HUMANA` (humano decide remediacao, incl. eventual compromisso de fallback).
- `dados_geo_completos=false` → `ANALISE_HUMANA` (nunca decide compromisso com dados incompletos).
- **Catch-all (row final):** qualquer combinacao nao coberta → `ANALISE_HUMANA`. **Sem saida COMPROMETER/GARANTIR/CONTRATAR** por design (ADR-0018 parte 1 e 2).

### `adequacao_sla` (hitPolicy UNIQUE — DRAFT; todos os prazos DRAFT/verify)
in: `gap_adequacao: string`, `tipo_carater: string`
out: `sla_remediacao: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte_regulatoria: string`
Gap critico / urgencia tem SLA mais curto. Prazos como string ISO — **DRAFT/verify regulatorio** (RN 259 garantia de atendimento).

## Papeis humanos (candidate groups)

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver Pendencias). Nomes
> DRAFT; podem nao corresponder aos grupos reais do IdP/console de User Tasks.

| Grupo | Papel | Tarefa |
|---|---|---|
| `gestao-rede` | Gestao de rede credenciada | `UT_DecisaoFallback` (**compromisso financeiro de fallback SO aqui**: `decisao_remediacao ∈ {MONITORAR_OK, ENCAMINHAR_CRED, COMPROMISSO_FALLBACK, SOLICITAR_INFO}`) |
| `coordenacao-rede` | Coordenacao de rede / regulacao | `UT_CoordenacaoRede` (SLA de remediacao estourado — assume a decisao, que continua humana; herda os mesmos campos obrigatorios de `UT_DecisaoFallback`) |

Toda `<bpmn:userTask>` traz `camunda:candidateGroups` (gate D1). A decisao adversa (compromisso
de fallback) NUNCA muda de natureza por estouro de SLA.

### Roteamento de `decisao_coordenacao` (GW_DecisaoCoordenacao — GAP-ADEQ-3)

`UT_CoordenacaoRede` produz **dois** campos possiveis: `decisao_coordenacao` (sempre) e, quando
`assumir_decisao`, os mesmos campos de `UT_DecisaoFallback` (`decisao_remediacao` + obrigatorios de
`COMPROMISSO_FALLBACK`). Um gateway dedicado `GW_DecisaoCoordenacao` (logo apos `UT_CoordenacaoRede`,
antes de `GW_DecisaoRemediacao`) roteia por `decisao_coordenacao`:

| `decisao_coordenacao` | Roteamento |
|---|---|
| `assumir_decisao` | `GW_DecisaoRemediacao` — a coordenacao decide agora; `decisao_remediacao` (preenchida na mesma User Task) roteia normalmente, incl. eventual `COMPROMISSO_FALLBACK` (a decisao adversa continua HUMANA — nunca muda de natureza por estouro de SLA) |
| `prorrogar_prazo` | volta a `UT_DecisaoFallback` — prazo estendido; os boundary timers/mensagem (`BT_AlertaSlaAdequacao`/`BT_SlaRemediacao`/`BME_RedeAtualizada`) rearmam |
| `seguir_analise` (**default/catch-all conservador** — inclui `""` e qualquer valor nao mapeado) | volta a `ST_PrepareRemediationDossier` — o dossie e refeito por Andre antes de reabrir `UT_DecisaoFallback` |

`GW_DecisaoCoordenacao` so e alcancavel apos `UT_CoordenacaoRede` completar (caminho de estouro de
SLA) — `decisao_coordenacao` esta sempre SET quando o gateway o le (ENGINE-16004).

**Hardening (default-init, ENGINE-16004).** `ST_PublishSlaBreach` — o **unico** task que precede
`UT_CoordenacaoRede` no caminho de estouro de SLA — default-inicializa `decisao_coordenacao=${""}`
via `camunda:outputParameter`. Consequencia: mesmo uma completude de `UT_CoordenacaoRede` que
**omita** `decisao_coordenacao` chega ao gateway com a variavel definida (`""`), avalia as condicoes
sem `Unknown property`/500 e cai no **default** (`seguir_analise`). O default-init fica em
`ST_PublishSlaBreach` (task externo predecessor) e **nao** como `outputParameter` da propria
`UT_CoordenacaoRede`: mapeamentos de saida de User Task avaliam tambem em saidas por **interrupcao**
(licao #112), o que reintroduziria o risco. Foi exatamente a completude sem `decisao_coordenacao`
(teste do #108) que colidiu com o gateway do #110 e derrubou o `main` — revertido em #120, agora
re-landed com este hardening + o teste do #108 alinhado ao contrato (`assumir_decisao`).

## SLAs

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaAdequacao`) | 60–70% de `${sla.sla_alerta}` (DMN `adequacao_sla`) | nao-interruptivo → `operadora.adequacao.notify_sla_risk` | politica interna |
| Remediacao (`BT_SlaRemediacao`) | `${sla.sla_remediacao}` (ex.: **P30D** gap moderado, mais curto p/ critico/urgencia — **DRAFT/verify** RN 259) | interruptivo → publica `adequacao.sla_breached` → (se ha decisao humana pendente) cancela `UT_DecisaoFallback`, cria `UT_CoordenacaoRede` | RN 259 (garantia de atendimento) — **DRAFT/verify** |

Nota: o caminho L3 de monitoramento e majoritario (sem timer interruptivo de decisao humana —
apenas observa/encaminha). O timer interruptivo de remediacao so e relevante quando o fluxo entrou
em `ANALISE_HUMANA` (gap critico). **No estouro de SLA a coordenacao humana assume; nunca ha
auto-compromisso por timeout** (o compromisso financeiro nunca nasce de um timer).

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_FALLBACK_COMMITMENT_NOT_HUMAN` | worker-guard em `operadora.adequacao.register_fallback_commitment` | o worker **recusa** registrar o compromisso se `decisao_remediacao != COMPROMISSO_FALLBACK` setado por humano numa User Task, ou se faltarem `tipo_fallback`/`justificativa_fallback`/`referencia_regulatoria`/`responsavel_id`. Lanca BPMN error; instancia nao atinge `End_CompromissoFallbackHumano`. Registra a tentativa (ADR-0007). (espelha `ERR_*_NOT_HUMAN` de AUTH/CONTAS/CANCEL) |
| `ERR_ADEQUACAO_CELULA_INVALIDA` | declarado (`Error_AdequacaoCelulaInvalida`) para uso dos workers | worker lanca BPMN error se a celula (regiao×especialidade) for inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **Guarda no-adverse (parte 4 de 5 do padrao §4-bis-F/ADR-0018):** mesmo num processo
> majoritariamente L3, o unico caminho que onera a operadora (compromisso financeiro) e guardado.
> O teste de invariante (parte 5) verifica na history do engine que todo
> `End_CompromissoFallbackHumano` co-ocorre com User Task humana concluida.

## Notas de design / inversao do reference

- **L3-majority com chokepoint adverso human-gated:** o grosso do processo e monitoramento
  autonomo (medir, detectar, encaminhar a credenciamento) — L3, sem User Task. O **unico** efeito
  adverso (compromisso financeiro de fallback) e isolado num caminho humano. Isto evita o
  anti-padrao de "auto-garantir reembolso/livre escolha" (que oneraria a operadora sem decisao
  humana) e tambem o de "auto-negar adequacao" (que prejudicaria o beneficiario).
- **Handoff a SP-OP-CRED-001 nao e adverso:** disparar credenciamento para fechar o gap e acao L3
  (busca de prestador). A direcao **adversa** do credenciamento (descredenciar / negar
  credenciamento) pertence a CRED-001 e e human-gated **la** — nunca aqui.
- **Consome o fato de mudanca de rede do CRED-001** (HARMONIZADO — GAP-XPROC-2, fecha o item W-E).
  A relacao e CRED produz `network_changed` → a `network_change_bridge` consome e INICIA a
  reavaliacao da celula (bk deterministica por celula×ciclo; idempotente na reentrega).
- **Andre (Zona PHI/Financeira/Populacional)** enriquece o dossie de remediacao via
  `prepare_remediation_dossier` (`analytics.population`); **instrui, nao decide**.
- **Sem PHI / sem endereco cru de beneficiario:** geografia em granularidade de
  regiao/municipio (IBGE), nunca endereco de paciente (ADR-0006). `gap_detected` carrega so a
  celula afetada.
- **TASY write DROP** em todo worker: consumimos o CDC do amh-data-platform e o fato de rede do
  CRED; nunca escrevemos no Tasy (MEMORY/ADR-0013).

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e o BPMN/DMN bodies (autorados em wave posterior contra este contrato).
- ~~Harmonizar o fato `agents.events.cred.network_changed` com o contrato real do SP-OP-CRED-001~~
  **FEITO (GAP-XPROC-2)**: fato harmonizado + consumidor real (`network_change_bridge`).
  **Restam para review humana**: periodicidade do `ciclo_avaliacao` derivado (trimestre de
  `data_efeito_iso` vs mensal — RN 259/566) e a fonte cadastral de `regiao_saude`/`especialidade`
  do prestador no start de CRED (base de rede) — docs/review-queue.md.
- **Sign-off regulatorio dos thresholds de tempo/distancia** (`adequacao_gap`) contra RN 259
  vigente (e consolidacoes) por `tipo_carater`; periodicidade de avaliacao (`ciclo_avaliacao`) —
  todos DRAFT/verify.
- Confirmacao da citacao RN 259/RN 566 contra texto vigente ANS (podem ter sido consolidadas) —
  **DRAFT/verify**.
- **Confirmacao dos candidate groups** `gestao-rede` / `coordenacao-rede` contra a taxonomia
  organizacional da operadora.
- Politica financeira do compromisso de fallback (limites, interacao com SP-OP-PAGTO-001 quando o
  fallback gerar pagamento, interacao com SP-OP-REEMBOLSO-001 no reembolso garantido) — **DRAFT**.
- Taxonomia de especialidade (TUSS/CBO) e granularidade geografica (regiao de saude vs municipio
  IBGE) — alinhar com a base de rede e a RN.
