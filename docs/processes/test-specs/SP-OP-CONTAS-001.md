# Test spec — SP-OP-CONTAS-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" de Phase 0/1 — estes stubs guiam o teste de integracao real contra o
motor; **sem engine mock**). Arquivo alvo: `tests/integration/processes/test_sp_op_contas_001.py`.
Dados sinteticos: lote `LOTE-TESTE-0001`, prestador `Prestador Teste 001`, beneficiario
pseudonimizado `bnf-teste-0001`, tenant `amh`, CPF de teste em faixa invalida
(`000.000.000-00`). Business key `CONTAS-amh-LOTE-TESTE-0001`.

> **Perspectiva (ADR-0040, Proposed — nao ratificada).** O dono do processo e a **OPERADORA**. Ela
> recebe o lote de guias do prestador, adjudica a conta apresentada e emite o demonstrativo de
> analise. O ato adverso testado aqui e **aplicar** a glosa (`GLOSAR`/`PAGAR_PARCIAL`), nao aceitar
> uma glosa alheia. Os cenarios do antigo vocabulario do recorrente — contestar a glosa, aceita-la,
> reapresentar a conta — **desapareceram sem shim**, e cada morte tem substituto nomeado abaixo.

## Invariante L0 (testes de seguranca — prioritarios)

### test_nenhum_caminho_automatizado_glosa
*(RENOMEADO de `test_nenhum_caminho_automatizado_aceita_glosa`.)*
- **Given** varredura de TODAS as combinacoes de input da DMN `glosa_triage`:
  `tipo_item` ∈ {amostra sintetica}, `categoria_normalizada` ∈ {tecnica, administrativa, clinica, valor, documental, desconhecida},
  `item_conforme_tabela`/`divergencia_valor`/`documentacao_anexa` ∈ {true,false}
- **When** cada instancia percorre ate estabilizar (consultando `history/activity-instance` do engine)
- **Then** NUNCA atinge `End_GlosaAplicadaHumano` **nem** `End_PagamentoParcialHumano` sem uma User
  Task humana (`UT_AnalistaContas` ou `UT_CoordenacaoContasAssume`) **concluida por humano** com
  `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}` no historico. Os end-events adversos NUNCA aparecem no
  history do engine sem uma User Task humana no mesmo history. (invariante §4-bis-F, parte 5 —
  o item que ADR-0018 cita nominalmente)

### test_inelegibilidade_roteia_para_humano_nao_glosa
- **Given** start com item nao conforme a tabela contratada e sem documentacao
  (`item_conforme_tabela=false`, `documentacao_anexa=false`)
- **When** instancia percorre
- **Then** DMN `glosa_triage` retorna `roteamento=ANALISE_HUMANA` (catch-all conservador); fluxo
  chega a `UT_AnalistaContas` (`auditoria-contas`) — **nao** a um terminal adverso automatico.
  Divergencia aparente roteia; nunca glosa sozinha.

### test_glosar_exige_campos
- **Given** `UT_AnalistaContas` aberta
- **When** completar com `decisao_contas=GLOSAR` **sem**
  `justificativa_glosa`/`codigo_glosa_tiss`/`valor_glosado_brl`
- **Then** a task NAO completa (validacao de formulario/listener) — glosar sem
  justificativa+codigo+valor e impossivel. Espelha `test_negar_exige_campos_obrigatorios` de AUTH.

### test_pagar_parcial_exige_valor_liberado
- **Given** `UT_AnalistaContas` aberta
- **When** completar com `decisao_contas=PAGAR_PARCIAL` e todos os campos de glosa, mas **sem**
  `valor_liberado_brl` — ou com ele **`<= 0`** (`0.0`, `"0"`, `"0,00"`, negativo)
- **Then** o worker `registrar_glosa` recusa (`ERR_CONTAS_GLOSA_NOT_HUMAN`). Uma reducao declara as
  duas metades: sem o valor liberado o demonstrativo nao fecha `apresentado = liberado + glosa` e o
  handoff de pagamento nao tem valor. **`> 0`, nao `>= 0`:** um "parcial" que libera R$ 0,00 e,
  materialmente, um `GLOSAR` integral — aceita-lo registrava o efeito adverso, emitia ao prestador um
  demonstrativo de "pagamento parcial" que nao paga nada, e depois travava a instancia em
  `ST_HandoffPagamentoParcial`, que recusa `<= 0`. Controle negativo emparelhado
  (`test_registrar_glosa_pagar_parcial_aceita_valor_liberado_positivo`) para que a recusa nao valha
  por construcao.

### test_glosa_tecnica_roteia_para_humano_nao_glosa
- **Given** start com `categoria_normalizada=tecnica` (glosa tecnica/formatacao)
- **When** instancia percorre `glosa_triage`
- **Then** `roteamento=ANALISE_HUMANA` (default conservador R4); fluxo chega a `UT_AnalistaContas`.
  (auto-route de glosa puramente formatacional so existiria com sign-off de compliance — **nao**
  esta no fluxo DRAFT)

### test_worker_registrar_glosa_recusa_sem_humano
- **Given** invocacao direta do worker `operadora.contas.registrar_glosa` com `decisao_contas`
  ausente, fora de `{GLOSAR, PAGAR_PARCIAL}` (inclusive `PAGAR`, que e favoravel), ou faltando
  `analista_id`
- **When** o worker executa
- **Then** levanta `ERR_CONTAS_GLOSA_NOT_HUMAN` (`PermissionError` → incidente auditado, **nunca**
  `bpmnError`); NAO registra a glosa; a instancia nao avanca aos terminais adversos. Com
  `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}` + campos + `analista_id` setados por humano → registra,
  cunha `glosa_id` deterministico e carrega `analista_id` na cadeia de auditoria (ADR-0007).

### test_decisao_invalida_termina_em_erro_sem_efeito
- **Given** `UT_AnalistaContas` concluida com `decisao_contas` ausente ou fora do dominio
- **When** o token alcanca `GW_DecisaoContas`
- **Then** cai no **default** `End_ErrContasDecisaoInvalida` (`ERR_CONTAS_DECISAO_INVALIDA`) e
  NENHUM efeito e materializado — nem glosa, nem demonstrativo, nem ordem de pagamento. Antes desta
  correcao o default do gateway era uma ACAO: uma variavel ausente PRODUZIA um ato.

### test_indicio_fraude_nunca_auto_flag
- **Given** start com `indicio_fraude_sinalizado=true` (sinal informativo de worker)
- **When** instancia percorre
- **Then** nenhum branch auto-acusa fraude; o fluxo cria `UT_AnalistaContas` (`auditoria-contas`)
  que decide `ENCAMINHAR_FRAUDE` (handoff a SP-OP-FRAUDE-001). `fraud_accusation` L0 hard nunca
  nasce de automacao.

## I-PAGTO-1 — nenhuma ordem originada aqui vira dinheiro sozinha

### test_conta_originada_em_contas_nunca_alcanca_liberacao_automatica_sem_ut
- **Given** start pela perna automatica (`has_glosas=false`), com o handoff criando a instancia
  SP-OP-PAGTO-001
- **When** a instancia de PAGTO percorre
- **Then** `End_PagamentoLiberadoAutomatico` **nao** esta no history enquanto
  `UT_AnaliseAdmissibilidade` nao for concluida; `lastro_confirmado` chega `false` a
  `pagto_admissibility`. O gate real deste caminho e a admissibilidade fail-closed, **nao**
  `ERR_PAYMENT_RELEASE_NOT_HUMAN` (que guarda so `ST_ReleaseHighValue`).
  Irmao generico ja em `main`: `tests/integration/processes/test_sp_op_pagto_001.py`
  (`test_lastro_nao_confirmado_nunca_auto_libera_clerical`).

### test_m4_categoria_desconhecida_para_em_admissibilidade_humana
- **Given** `categoria_normalizada="desconhecida"` com os tres fatos favoraveis
  (`item_conforme_tabela=true`, `divergencia_valor=false`, `documentacao_anexa=true`) — o repro do
  achado **M-4**
- **Then** a conta e adjudicada `PAGAR` sem analista de contas (o residuo, **OQ-10**), MAS a ordem
  criada para em `UT_AnaliseAdmissibilidade`: **ordem pendente de humano, nunca liberacao**. E o
  teste que torna a divulgacao de raio do ADR-0040 um fato verificavel.

### test_handoff_pagamento_inicia_pagto_idempotente
- **Given** duas entregas do mesmo handoff (ou uma segunda decisao no mesmo lote apos
  `msg.contas.linhas_atualizadas`)
- **Then** UMA unica instancia sob `PAGTO-amh-LOTE-TESTE-0001-{prestador_id}`. SP-OP-PAGTO-001 e a
  familia **STRICT** de dedup: o chokepoint exige `DedupReportingAuditSink` +
  `HistoryQueryingTransport` e recusa sem eles — um handoff mal-cabeado falha, nunca paga duas vezes.

## Happy paths

### test_lote_sem_data_vencimento_nao_gera_ordem_de_pagamento
*(NOVO — par negativo do happy path integral.)*
- **Given** lote sem `data_vencimento` (o campo que o intake `operadora.contas.identify_glosa` ecoa
  do lote/termo contratual e **nunca** defaulta), na perna AUTOMATICA — a mais perigosa, porque nada
  nela e humano
- **When** a instancia chega a `ST_HandoffPagamentoAuto` (asserido: sem chegar la a prova valeria por
  construcao)
- **Then** `ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO`: **nenhuma** instancia PAGTO sob
  `PAGTO-amh-{lote}-{prestador_id}`, nenhum `contas.completed`, `End_ContaAprovadaIntegral` NAO
  alcancado, e um **incidente aberto** no engine — a recusa e visivel, nunca um no-op silencioso. Um
  vencimento inventado seria um prazo falso lido pelo revisor de `UT_AnaliseAdmissibilidade` como se
  viesse do termo contratual (ADR-0040 OQ-2).

### test_happy_path_pagamento_integral
*(RESCRITO de `test_happy_path_sem_glosa`.)*
- **Given** conta apresentada sem divergencias (`has_glosas` falso apos
  `operadora.contas.identify_glosa`)
- **When** instancia percorre
- **Then** `operadora.contas.emitir_demonstrativo` (`tipo_comunicacao=demonstrativo_analise`) e
  `operadora.contas.handoff_pagamento` (`fonte_valor=apresentado`) executados; `contas.completed`
  com `desfecho=pagar_integral`; fim `End_ContaAprovadaIntegral`; NENHUMA User Task criada **neste
  processo** (a humana esta em PAGTO).

### test_happy_path_glosar_pelo_analista
*(SUBSTITUI `test_happy_path_recorrer_handoff_recurso`, que morre — a operadora nao recorre da
propria glosa.)*
- **Given** glosa candidata; `UT_AnalistaContas` aberta com dossie de Marina preparado
  (`operadora.contas.prepare_triage_dossier` completa)
- **When** analista completa com `decisao_contas=GLOSAR` + `justificativa_glosa` +
  `codigo_glosa_tiss` + `valor_glosado_brl` + `analista_id`
- **Then** `operadora.contas.registrar_glosa` executado (guard satisfeito, `glosa_id` cunhado);
  `operadora.contas.emitir_demonstrativo` executado; `contas.completed` com
  `desfecho=glosa_aplicada_humano` carregando `glosa_id`/`codigo_glosa_tiss`; fim
  `End_GlosaAplicadaHumano`; `analista_id` na trilha de auditoria.

### test_happy_path_pagar_parcial_pelo_analista
*(SUBSTITUI `test_happy_path_aceitar_glosa_pelo_analista`, que morre — era o ato do prestador.)*
- **Given** `UT_AnalistaContas` aberta
- **When** analista completa `PAGAR_PARCIAL` com `justificativa_glosa`+`codigo_glosa_tiss`+
  `valor_glosado_brl`+`valor_liberado_brl`+`analista_id`
- **Then** `registrar_glosa` → `emitir_demonstrativo` (demonstrativo **misto**) →
  `handoff_pagamento` (`fonte_valor=liberado`) → `contas.completed` com
  `desfecho=pagamento_parcial_humano`; fim `End_PagamentoParcialHumano`.

### test_happy_path_devolver_conta
*(SUBSTITUI `test_happy_path_reenviar`, que morre — reapresentar a conta e ato do prestador.)*
- **Given** `UT_AnalistaContas` aberta
- **When** analista completa com `decisao_contas=DEVOLVER` + `justificativa_devolucao` +
  `analista_id`
- **Then** `operadora.contas.devolver_conta` executado (sem efeito adverso L0) e
  `operadora.contas.emitir_demonstrativo` com `tipo_comunicacao=devolucao_para_correcao`;
  `contas.completed` com `desfecho=conta_devolvida_humano`; fim `End_ContaDevolvidaPrestador`.

### test_happy_path_encaminhar_fraude_handoff_fraude
- **Given** `UT_AnalistaContas` aberta com `indicio_fraude_sinalizado=true`
- **When** analista completa com `decisao_contas=ENCAMINHAR_FRAUDE`
- **Then** `operadora.contas.start_fraude` executado (inalterado); `contas.completed` com
  `desfecho=encaminhada_fraude`; fim `End_EncaminhadaFraude` — **o unico terminal de negocio sem
  comunicacao ao prestador**, omissao deliberada (OQ-14).

## Comunicacao ao prestador (M6)

### test_todos_os_fins_afetando_prestador_emitem_comunicacao
- **Given** cada um dos 6 terminais de negocio, alcancado
- **Then** varredura do history: os **5** terminais comunicantes tem
  `operadora.contas.emitir_demonstrativo` concluido **antes** do `ST_Publish*` correspondente;
  `End_EncaminhadaFraude` e a **unica** excecao e o teste a nomeia, para que a omissao seja
  deliberada e visivel. E uma varredura, nao uma lista, porque a ordem canonica de todo terminal e
  a mesma.

### test_todos_os_fins_emitem_evento_de_dominio
- **Then** todo terminal de **negocio** e precedido por um `ST_Publish*`; o terminal **tecnico**
  `End_ErrContasDecisaoInvalida` e a excecao declarada (mesma de `End_ErrDecisaoInvalida` de AUTH).

## Reavaliacao por correcao de linhas (mensagem)

### test_linhas_atualizadas_reavalia
- **Given** instancia aguardando correcao (linhas inconsistentes → `documentacao_anexa=false`)
- **When** message `msg.contas.linhas_atualizadas` correlacionada (business key) com
  `documentacao_anexa=true`
- **Then** `ST_ApurarDivergencias` reexecutado e `BRT_TriagemGlosa` reavaliada; fluxo segue para a
  analise atualizada, na MESMA instancia.

### test_devolucao_comunica_prestador_e_reabre_por_linhas_atualizadas
- **Given** a conta devolvida (`ST_ComunicarDevolucao` concluido)
- **When** chega `msg.contas.linhas_atualizadas`
- **Then** reentra em `ST_ApurarDivergencias` na mesma instancia. Sem a comunicacao da devolucao o
  prestador nunca saberia que a conta voltou, e esta mensagem ficaria sem gatilho.

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_AnalistaContas` aberta (alerta = 60–70% de `sla.sla_alerta`)
- **When** job do timer `BT_AlertaSlaContas` executado
- **Then** `operadora.contas.notify_sla_risk` recebeu task (alerta `coordenacao-contas`); a User
  Task segue aberta.

### test_timer_sla_estourado_coordenacao_assume
- **Given** analise aberta alem de `sla.sla_analise` (tipico P30D — DRAFT/verify)
- **When** job do timer `BT_SlaAnaliseContas` (interruptivo) executado
- **Then** `contas.sla_breached` publicado; `UT_AnalistaContas` cancelada;
  `UT_CoordenacaoContasAssume` criada (`coordenacao-contas`). **Nao** ha desfecho automatico por
  timeout (inversao do `Task_AutoApprove`/48h do reference) — a decisao continua humana.

### test_coordenacao_assume_e_glosa
- **Given** `UT_CoordenacaoContasAssume` aberta apos o estouro
- **When** a coordenacao completa com `decisao_contas=GLOSAR` + campos + `analista_id`
- **Then** mesmo caminho e mesmo guard do analista: o guard le a **decisao**, nao o elemento que a
  produziu (dois canais humanos, um guard).

### test_sla_ancora_em_data_recebimento_lote_nao_em_attach_da_ut
- **Then** os dois timers ancoram no recebimento do lote **pela operadora**
  (`data_recebimento_lote`), nunca no attach da User Task (GAP-CONTAS-4).

### test_dmn_contas_sla_internacao
- **Given/When** start com `tipo_lote=internacao`
- **Then** `sla.sla_analise` retornado como string ISO (ex.: `"P30D"` — **valor inalterado por
  ADR-0040**), com a fonte registrada em `sla.fonte_regulatoria` = prazo contratual + RN 501/2022
  (valor DRAFT/verify; a atribuicao a RN 424/2017 foi retirada).

## DMN/BPMN — shape e fail-safe (gate UNITARIO, `tests/unit/spec/test_sp_op_contas_001_artefatos.py`)

> Estas asercoes nao precisam de engine e **sairam** de `tests/integration/processes/` para
> `tests/unit/spec/`: o modulo de integracao carrega `pytestmark = pytest.mark.integration`, entao
> `make test` as DESELECIONAVA — foi por isso que a asercao de dominio da `glosa_triage` ficou
> afirmando o vocabulario antigo sem ninguem ver.

### test_glosa_triage_sem_saida_de_glosa
*(RENOMEADO de `test_glosa_triage_sem_saida_de_aceite`; movido para o gate unitario.)*
- **Given** a definicao da DMN `glosa_triage`
- **Then** o dominio de `roteamento` e exatamente `{PAGAR, ANALISE_HUMANA}` — **nenhum valor que
  glose**; e existe row catch-all → `ANALISE_HUMANA`. Na perspectiva do pagador o adverso E a
  glosa, logo a parte 2 de ADR-0018 e trivialmente verificavel: o dominio nao contem valor adverso.

### test_dmn_typeref_allowlist
- **Given** todas as DMNs do processo (`glosa_reason_normalization`, `glosa_classification`,
  `glosa_triage`, `contas_sla`)
- **Then** todo `typeRef` ∈ {string, boolean, integer, long, double, date}; nenhuma coluna usa
  `"number"`; valores BRL sao `double`; prazos sao string ISO. (gate `validate-artifacts`)

### test_variaveis_de_decisao_humana_sao_inicializadas_no_primeiro_service_task
*(NOVO.)*
- **Given** o BPMN de SP-OP-CONTAS-001
- **Then** `decisao_contas` e inicializada com `${""}` em `ST_PublishReceived`. Sem isso o CIB Seven
  2.1.0 avalia as `conditionExpression` de `GW_DecisaoContas` ANTES do `default` e lanca
  `Cannot resolve identifier` (HTTP 500 no complete da User Task): o caso «decisao AUSENTE» que
  `End_ErrContasDecisaoInvalida` declara cobrir deixava a instancia PARADA na UT.

### test_toda_variavel_lida_por_gateway_decisorio_esta_inicializada
*(NOVO — fecha a CLASSE.)*
- **Then** todo identificador lido por uma `conditionExpression` de `GW_DecisaoContas` esta
  inicializado em `ST_PublishReceived`; o valor inicializado (`""`) nao casa com nenhuma rota de
  acao (senao a inicializacao criaria um ato por omissao); e `GW_DecisaoContas` continua tendo
  `default=Flow_GWDec_Invalida` (controle de nao-vacuidade).

### test_catalogo_de_erros_e_declarado_e_nao_capturado
*(NOVO — fixa a invariante que a leitura «entrada morta» violaria.)*
- **Given** o BPMN de SP-OP-CONTAS-001
- **Then** o catalogo `bpmn:error` de raiz e EXATAMENTE
  `{Error_ContasLoteInvalido, Error_ContasGlosaNotHuman, Error_ContasDecisaoInvalida}`; o unico
  referenciado por um `errorEventDefinition` e `Error_ContasDecisaoInvalida`; e essa referencia
  esta num **throw-end** (`endEvent`), nao num `boundaryEvent`.
- **Por que:** as duas entradas nao referenciadas NAO sao residuo. ADR-0030 §2 poe a fonte da
  verdade do gate no boundary (*«on an error boundary event attached to an external task»*), entao
  uma entrada de catalogo sem boundary e invisivel para `check_bpmn_error_allowlist.py` — remove-la
  nao mudaria nenhum resultado de gate, so apagaria a ancora documental. ADR-0030 §5 nomeia o estado
  (`declared-uncaught` → incidente) e a emenda de ADR-0040 cita `ERR_CONTAS_GLOSA_NOT_HUMAN` pelo
  nome como *«Tier-3 declared-and-uncaught»*.

### test_contas_nao_tem_error_boundary_sobre_external_task
*(NOVO — controle estrutural.)*
- **Then** ZERO `boundaryEvent` com `errorEventDefinition` no processo — o estado que o censo do
  ADR-0030 registra para CONTAS. Modelar um antes de T-E trocaria o incidente visivel por um fim
  silencioso (ADR-0030 §4), regressao de visibilidade sob a invariante HITL.

### test_contas_nunca_levanta_worker_bpmn_error
*(NOVO — metade do worker, em `tests/unit/tools/workers/test_contas.py`.)*
- **Given** o modulo `maezo.tools.workers.contas`
- **Then** ele nao menciona `WorkerBpmnError` em lugar nenhum; `ContasGlosaNotHumanError` continua
  `PermissionError` e `ContasLoteInvalidoError` continua `ValueError`, e nenhum dos dois e
  `WorkerBpmnError`. Se um `WorkerBpmnError` aparecesse, a clausula (b) do gate falharia (*«an
  uncatalogued raise relying on demote-to-incident»*) — este teste falha antes, com o motivo junto.

## Idempotencia

### test_business_key_uma_instancia_por_lote
- **Given** instancia ativa `CONTAS-amh-LOTE-TESTE-0001`
- **When** reenvio do mesmo lote TISS
- **Then** sem segunda instancia ativa (start idempotente; retorna a existente).
