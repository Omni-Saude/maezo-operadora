# Test spec — SP-OP-FRAUDE-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra **CIB Seven real** (doutrina
"nunca mockar o engine" de Phase 0/1/2; **sem engine mock**). Arquivo alvo:
`tests/integration/processes/test_sp_op_fraude_001.py`. O teste de integridade de custodia
(3 partes) reside junto ao gateway: `tests/unit/gateway/test_custody.py` para as partes
unitarias e no teste de integracao para a ordem selagem-antes-da-decisao.

Dados sinteticos: caso `CASO-TESTE-0001`, entidade/prestador `Prestador Teste 001`
(pseudonimo), beneficiario pseudonimizado `bnf-teste-0001`, tenant `amh`, CPF/CNPJ de teste
em faixa invalida. Business key `FRAUDE-amh-CASO-TESTE-0001`. Evidencias = ponteiros
pseudonimizados sinteticos (**NUNCA PHI bruto**).

## Invariante L0 — no-adverse (testes de seguranca — PRIORITARIOS, parte 5 de 5 do §4-bis-F / ADR-0018)

### test_nenhum_caminho_automatizado_acusa_fraude
- **Given** varredura de TODAS as combinacoes de input das DMNs `fraude_indicadores` + `fraude_routing`:
  `score_indicadores ∈ {amostra: 0, baixo, alto}` × `indicadores_presentes ∈ {vazio, parcial, todos}`
  × `entidade_tipo ∈ {prestador, beneficiario, contrato, rede}` × `origem_encaminhamento ∈ {amostra}`
- **When** cada instancia percorre ate estabilizar (consultando `history/activity-instance` do engine), **sem completar nenhuma User Task**
- **Then** NENHUMA instancia atinge `End_FraudeConfirmadaHumano` / `End_EncaminhadoJuridico` /
  `End_EncaminhadoCredenciamento` / `End_EncaminhadoContratual` sem que a history contenha
  `UT_DecisaoInvestigador` **ou** `UT_CoordenacaoInvestigacao` **concluida por humano** com
  `decisao_fraude=ACUSAR_FRAUDE`. **A varredura completa nao produz nenhum terminal adverso.**
  Score alto NUNCA gera acusacao — so roteia para `PRIORITARIA`/humano. (invariante D2)

> Assercao central (history-based, sem mock do engine): para cada `historicProcessInstance`
> que termina em end-event adverso, existe na mesma history um `historicTaskInstance` de
> `UT_DecisaoInvestigador`/`UT_CoordenacaoInvestigacao` com `endTime != null` e `assignee`
> humano que setou `decisao_fraude=ACUSAR_FRAUDE`. Co-ocorrencia obrigatoria.

### test_score_alto_roteia_para_humano_nunca_acusa
- **Given** start com `score_indicadores` alto e `indicadores_presentes` = todos
- **When** instancia percorre `fraude_indicadores`→`fraude_routing`
- **Then** `intensidade_investigacao=PRIORITARIA`, `roteamento=INVESTIGACAO_HUMANA`; fluxo chega a
  `UT_DecisaoInvestigador` — **nunca** a `End_FraudeConfirmadaHumano` nem a qualquer terminal de
  referral automatico. **Inverte o `FRAUD_DETECTED` do reference:** score alto investiga mais,
  nunca acusa.

### test_acusar_fraude_exige_campos
- **Given** `UT_DecisaoInvestigador` aberta sobre `bundle_root` selado
- **When** completar com `decisao_fraude=ACUSAR_FRAUDE` **sem** `fundamentacao_investigacao` /
  `indicadores_fundamentantes` / `referencia_normativa` / `destino_referral` / `investigator_id` / `tier`
- **Then** a task NAO completa (validacao de formulario/listener) — acusacao sem fundamentacao,
  indicadores, fundamento normativo e aprovador e impossivel. Espelha `test_aceitar_glosa_exige_campos`.

### test_dmn_fraude_routing_sem_saida_de_acusacao
- **Given** a definicao das DMNs `fraude_indicadores`, `fraude_routing`, `fraude_sla`
- **Then** o dominio de `roteamento` (`fraude_routing`) e exatamente `{INVESTIGACAO_HUMANA}`;
  `fraude_indicadores.intensidade_investigacao ∈ {LEVE, APROFUNDADA, PRIORITARIA}` — **nenhum valor
  de acusacao/veredito/bloqueio** (`ACUSAR`/`FRAUD_DETECTED`/`BLOQUEAR`/`CONFIRMAR` ausentes); e existe
  row catch-all → `INVESTIGACAO_HUMANA` (conservador, grupo/tier mais senior).

## Cadeia de custodia — teste de integridade 3 partes (a classe nova — D4)

### test_custodia_selada_antes_da_decisao (parte 1 — selagem-antes-da-decisao)
- **Given** instancia com dossie montado (`operadora.fraude.assemble_dossier` completo)
- **When** instancia percorre `operadora.fraude.seal_custody_bundle` → `GW_CustodiaSelada`
- **Then** o `bundle_root` esta gravado na hash-chain ADR-0007 **ANTES** de `UT_DecisaoInvestigador`
  existir na history (`custody_sealed` precede a criacao da UT). `register_fraud_accusation` re-verifica
  o `bundle_root` selado e o root bate. Se o gate alcancar a UT sem selagem → `ERR_CUSTODY_NOT_SEALED`.

### test_custodia_tamper_reorder_quebra_root (parte 1b — tamper-evidence)
- **Given** um `CustodyBundle` selado com N itens e `bundle_root` R (em `test_custody.py`)
- **When** qualquer item e alterado **ou** a ordem dos `record_hashes` e trocada e o root recalculado
- **Then** o novo root != R; o registro selado na chain nao bate; `register_fraud_accusation` recusa
  (`ERR_FRAUD_ACCUSATION_NOT_HUMAN`/`ERR_CUSTODY_NOT_SEALED`). Qualquer adulteracao/reordenacao e detectavel.

### test_no_phi_in_custody (parte 2 — no-PHI-in-custody)
- **Given** itens de evidencia candidatos contendo (sinteticamente) PHI bruto (nome/CPF de teste invalido)
- **When** `operadora.fraude.seal_custody_bundle` tenta selar
- **Then** o worker recusa com `ERR_PHI_IN_CUSTODY` — o bundle so aceita ponteiros pseudonimizados +
  hashes (ADR-0006). Nenhum PHI bruto entra na custodia nem na chain. (defesa em profundidade)

### test_accusation_requires_human (parte 3 — accusation-requires-human / worker-guard)
- **Given** invocacao direta do worker `operadora.fraude.register_fraud_accusation` com
  `decisao_fraude` ausente ou `!= ACUSAR_FRAUDE` (ou faltando `investigator_id`/`tier`, ou
  `bundle_root` nao selado/nao verificavel)
- **When** o worker executa
- **Then** lanca `ERR_FRAUD_ACCUSATION_NOT_HUMAN`; NAO registra acusacao; instancia nao avanca a
  `End_FraudeConfirmadaHumano`. Com `decisao_fraude=ACUSAR_FRAUDE` + campos + `investigator_id`+`tier`
  setados por humano E `bundle_root` selado verificavel → registra e carrega `investigator_id`+`tier`
  na cadeia de auditoria (ADR-0007). (Unidade — complementa o teste de invariante de integracao.)

## Costura com Phase 2 (sink convergente)

### test_intake_neutro_registra_procedencia
- **Given** start via `operadora.fraude.intake` com `encaminhado_por_id` (humano de Phase 2),
  `origem_encaminhamento=contas`
- **Then** `agents.events.fraude.intake_received` publicado (correlacao `FRAUDE-amh-CASO-TESTE-0001`);
  `encaminhado_por_id` registrado na cadeia de auditoria; **nenhuma** User Task adversa criada no
  intake; nenhum efeito adverso.

### test_fraude_nunca_le_de_volta_glosa
- **Given** caso de fraude originado de CONTAS (`origem_encaminhamento=contas`)
- **Then** FRAUDE-001 nao publica nem consome topico que leia de volta/bloqueie/pre-empte a decisao
  de glosa; nao ha mensagem de volta a CONTAS. A glosa segue independente. FRAUDE e estritamente downstream.

### test_multiplos_encaminhamentos_consolidam_no_caso
- **Given** instancia ativa `FRAUDE-amh-CASO-TESTE-0001`
- **When** segundo `encaminhar_fraude` sobre a mesma entidade/caso chega (`msg.fraude.evidencia_anexada`)
- **Then** sem segunda instancia ativa (business key idempotente); evidencia anexada ao dossie;
  `bundle_root` re-selado (novo root) enquanto nao houver decisao.

## Happy paths

### test_happy_path_arquivar_sem_indicio
- **Given** `UT_DecisaoInvestigador` aberta sobre dossie selado
- **When** investigador completa `decisao_fraude=ARQUIVAR`
- **Then** `fraude.completed` com `desfecho=arquivado_sem_indicio`; fim `End_ArquivadoSemIndicio`;
  `register_fraud_accusation` NUNCA invocado; nenhum efeito adverso.

### test_happy_path_monitorar
- **Given** `UT_DecisaoInvestigador` aberta
- **When** investigador completa `decisao_fraude=MONITORAR`
- **Then** `fraude.completed` `desfecho=monitorar`; fim `End_MonitorarSemAcao` (neutro).

### test_happy_path_acusar_fraude_humano
- **Given** `UT_DecisaoInvestigador` aberta sobre `bundle_root` selado, dossie de Beatriz montado
- **When** investigador completa `ACUSAR_FRAUDE` com `fundamentacao_investigacao`+
  `indicadores_fundamentantes`+`referencia_normativa`+`destino_referral`+`investigator_id`+`tier`
- **Then** `operadora.fraude.register_fraud_accusation` executado (guard satisfeito, root re-verificado);
  `fraude.completed` `desfecho=fraude_confirmada_humano`; fim `End_FraudeConfirmadaHumano`;
  `investigator_id`+`tier` na trilha de auditoria.

### test_happy_path_acusar_handoff_credenciamento
- **Given** acusacao humana com `entidade_tipo=prestador` e `destino_referral.cred=true`
- **When** a jusante de `End_FraudeConfirmadaHumano`
- **Then** `operadora.fraude.start_credenciamento` executado (inicia SP-OP-CRED-001 — que tem **sua
  propria UT adversa**; FRAUDE nunca auto-descredencia); fim `End_EncaminhadoCredenciamento`.

### test_happy_path_acusar_handoff_contratual
- **Given** acusacao humana com `entidade_tipo ∈ {beneficiario, contrato}` e `destino_referral.contratual=true`
- **Then** `operadora.fraude.start_contratual` executado (inicia SP-OP-CANCEL-001/INADIMPLENCIA-001 —
  com **UT adversa propria**; FRAUDE nunca auto-rescinde); fim `End_EncaminhadoContratual`.

### test_happy_path_acusar_handoff_juridico
- **Given** acusacao humana com `destino_referral.juridico=true`/`ans=true`
- **Then** `operadora.fraude.refer_to_legal` executado (referral a juridico/ANS/civel/penal — so a
  jusante de acusacao humana); fim `End_EncaminhadoJuridico`.

## Diligencia (event gateway — INVERTE auto-acao por timeout)

### test_solicitar_diligencia_aguarda_correlacao
- **Given** `UT_DecisaoInvestigador` aberta
- **When** investigador completa `decisao_fraude=SOLICITAR_DILIGENCIA`
- **Then** instancia aguarda no event gateway (`msg.fraude.diligencia_concluida` **vs** timer
  `${fraude_sla.sla_diligencia}`); ao correlacionar (business key), reabre `UT_DecisaoInvestigador`.

### test_prazo_diligencia_expira_vai_para_humano_nao_acusa
- **Given** aguardando no event gateway de diligencia
- **When** job do timer executado (sem `diligencia_concluida`)
- **Then** `UT_DecisaoInvestigador` reaberta (ou `UT_CoordenacaoInvestigacao`) — a expiracao **NUNCA**
  alcanca `End_FraudeConfirmadaHumano` nem `End_ArquivadoSemIndicio` automaticamente (INVERTE o
  auto-acao-no-timeout do reference).

## Timers de SLA

### test_timer_alerta_sla_nao_interruptivo
- **Given** `UT_DecisaoInvestigador` aberta (alerta = 50–70% de `sla.sla_alerta`)
- **When** job do timer `BT_AlertaSlaFraude` executado
- **Then** `operadora.fraude.notify_sla_risk` recebeu task (alerta `coordenacao-investigacao`); a
  User Task segue aberta; nenhum desfecho adverso produzido.

### test_timer_sla_estourado_coordenacao_assume
- **Given** investigacao aberta alem de `${fraude_sla.sla_investigacao}`
- **When** job do timer `BT_SlaInvestigacao` (interruptivo) executado
- **Then** `agents.events.fraude.sla_breached` publicado; `UT_DecisaoInvestigador` cancelada;
  `UT_CoordenacaoInvestigacao` criada (`coordenacao-investigacao`) — decisao continua humana, mesmos
  campos obrigatorios e mesmo requisito de `bundle_root` selado. **Nao** ha auto-acusacao por timeout.

### test_dmn_fraude_sla_registra_fonte
- **Given/When** start com `intensidade_investigacao=PRIORITARIA`
- **Then** `fraude_sla.fonte_regulatoria` preenchida (ex.: "obrigacao de referral — DRAFT/verify") e
  propagada para auditoria; `sla_investigacao`/`sla_alerta` em string ISO 8601.

## DMN — shape e typeRef allowlist

### test_dmn_typeref_allowlist
- **Given** todas as DMNs do processo (`fraude_indicadores`, `fraude_routing`, `fraude_sla`) + as 7
  DMNs de score portadas/invertidas (`upcoding_complexity_ceiling`, `unbundling_partial_bundles`,
  `phantom_no_diagnosis`, `phantom_suspicious_prefix`, `frequency_zscore_threshold`,
  `provider_peer_deviation`, `risk_thresholds`)
- **Then** todo `typeRef` ∈ {string, boolean, integer, long, double, date}; nenhuma coluna usa
  `"number"` (o `score: number` do reference foi corrigido para `integer`); score em `integer`;
  prazos em string ISO. Nenhuma das 7 DMNs portadas emite `recommendation→FRAUD_DETECTED` nem coluna
  de veredito/bloqueio. (gate `validate-artifacts`)

## A2A (Beatriz — human-gated)

### test_beatriz_instrui_nao_decide
- **Given** delegacao `fraude.investigate` a Beatriz (`operadora.fraude.gather_evidence`/`assemble_dossier`)
- **Then** Beatriz coleta/monta o dossie mas **nao** seta `decisao_fraude` (grafo `instruct_investigation`
  single-node; `zero_auto_accusation`); respeita `accepted_task_types` + max-hops (ADR-0015). A decisao
  permanece exclusivamente humana em `UT_DecisaoInvestigador`.

## Idempotencia

### test_business_key_uma_instancia_por_caso
- **Given** instancia ativa `FRAUDE-amh-CASO-TESTE-0001`
- **When** reenvio do mesmo caso (segundo `encaminhar_fraude`)
- **Then** sem segunda instancia ativa (`start_process` consulta a business key e retorna a existente).

## Eventos de dominio (auditoria dupla — engine + Kafka, ADR-0007)

### test_todo_fim_publica_evento_antes
- **Given** qualquer caminho que atinge um end-event
- **Then** `agents.events.fraude.completed` publicado com `payload.desfecho` correspondente ANTES do
  fim (auditoria dupla); para os terminais adversos, o payload carrega `investigator_id`+`tier`.
