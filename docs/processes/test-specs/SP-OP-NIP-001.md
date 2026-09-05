# Test spec — SP-OP-NIP-001 (DRAFT — acompanha o processo)

Stubs de integracao (pytest, marker `integration`) contra CIB Seven **real** (doutrina
"nunca mockar o engine" de Phase 0/1 — os asserts consultam `history/activity-instance` do
engine, nao um mock). Arquivo alvo: `tests/integration/processes/test_sp_op_nip_001.py`.

Dados sinteticos: NIP `NIP-TESTE-0001`, protocolo `PROTO-TESTE-0001`, beneficiario
pseudonimizado `BEN-TESTE-001`, tenant `amh`. Business key `NIP-amh-NIP-TESTE-0001`. Nenhum
CPF/nome real; referencias de negativa original sinteticas (`AUTH-amh-GUIA-TESTE-0001`).

## Invariante L0 (testes de seguranca — prioritarios)

### test_manter_negativa_so_via_user_task_humana  (INVARIANTE no-denial)
- **Given** varredura de TODAS as combinacoes de input das DMNs `nip_classification`/`nip_routing`/`nip_sla`: `classificacao_nip ∈ {assistencial, nao_assistencial}` × `tema_nip ∈ {negativa_cobertura, prazo_atendimento, reembolso, rede, cobranca, <tema_desconhecido>}` × `contesta_negativa ∈ {true,false}` × `documentacao_suficiente ∈ {true,false}`
- **When** cada instancia percorre ate estabilizar
- **Then** o end-event adverso `End_NipNegativaMantida` NUNCA aparece no `history/activity-instance` do engine **sem** uma User Task humana (`UT_RevisaoJuridicaNip`) concluida com `decisao_nip==MANTER_NEGATIVA` no mesmo historico. Assercao estrutural: para todo activity-instance de tipo endEvent adverso, existe activityInstance de userTask humano antecedente com a variavel de decisao setada. Nenhuma combinacao de DMN, sozinha, alcanca o terminal adverso.

### test_resposta_final_nunca_gerada_por_dmn
- **Given** qualquer combinacao de input que produza `roteamento=ELABORAR_RESPOSTA`
- **Then** `operadora.nip.submit_response` NUNCA executa antes de uma User Task humana (`UT_ElaborarRespostaNip` + `UT_RevisaoJuridicaNip`) ter setado `texto_resposta_nip`/`revisor_id`; nenhuma DMN escreve `decisao_nip` nem `texto_resposta_nip`

### test_worker_guard_recusa_negativa_sem_humano  (ERR_NIP_NEGATIVA_NOT_HUMAN)
- **Given** instancia com `decisao_nip=MANTER_NEGATIVA` injetado **sem** `revisor_id`/sem User Task humana na cadeia (simula bypass)
- **When** `operadora.nip.submit_response` e acionado
- **Then** o worker recusa, lanca `ERR_NIP_NEGATIVA_NOT_HUMAN`; a resposta NAO e transmitida/arquivada; nenhum `nip.completed` com `desfecho=negativa_mantida` e publicado

### test_manter_negativa_exige_campos_obrigatorios
- **Given** `UT_RevisaoJuridicaNip` aberta
- **When** complete com `decisao_nip=MANTER_NEGATIVA` sem `fundamentacao_regulatoria`/`referencia_negativa_original`
- **Then** a task NAO completa (validacao de formulario/listener) — manter negativa sem fundamentacao e impossivel

### test_inelegibilidade_roteia_para_humano_nunca_auto_nega  (inelegibilidade → humano)
- **Given** start com `documentacao_suficiente=false` (caso "inelegivel/sem base para responder")
- **When** instancia percorre
- **Then** `nip_routing` retorna `roteamento=REVISAO_JURIDICA` (ou `PENDENTE_INFO`), NUNCA um caminho de auto-resposta; o fluxo chega a uma User Task humana (`juridico-regulatorio`), nao a `End_NipNegativaMantida` nem a qualquer fim sem User Task

### test_tema_desconhecido_fail_safe_juridico
- **Given** start com `tema_nip=<tema_desconhecido>` (linha catch-all)
- **Then** `nip_classification`/`nip_routing` roteiam para `juridico-regulatorio` com o menor `prazo_dias` (conservador); nunca classifica como `NAO_ASSISTENCIAL` respondida automaticamente

## Happy paths

### test_happy_path_resolvida_favoravel
- **Given** NIP assistencial contestando negativa; `UT_ElaborarRespostaNip` autora a resposta; `UT_RevisaoJuridicaNip` revisa
- **When** humano completa com `decisao_nip=CONCEDER` (concede o pleito) + `texto_resposta_nip`/`revisor_id`
- **Then** `operadora.nip.handoff_ans_submit` executado; `nip.completed` com `desfecho=resolvida_favoravel`; fim `End_NipResolvidaFavoravel`

### test_happy_path_negativa_mantida_pelo_humano
- **Given** `UT_RevisaoJuridicaNip` aberta (juridico-regulatorio)
- **When** humano completa `decisao_nip=MANTER_NEGATIVA` com `fundamentacao_regulatoria`+`referencia_negativa_original`+`revisor_id`
- **Then** `operadora.nip.submit_response` aceita (guard satisfeito; `revisor_id` na cadeia ADR-0007); handoff a ANS-SUBMIT; `nip.completed` `desfecho=negativa_mantida`; fim `End_NipNegativaMantida`

### test_happy_path_nao_assistencial_respondida
- **Given** `classificacao_nip=nao_assistencial`, `tema_nip=cobranca`, `documentacao_suficiente=true`
- **When** `UT_ElaborarRespostaNip` (`nucleo-ans`) autora e humano confirma o envio
- **Then** `nip.completed` `desfecho=nao_assistencial_respondida`; fim `End_NipNaoAssistencialRespondida`; mesmo no caminho clerical, o envio passa por User Task (nunca auto-submete)

## Classificacao e roteamento

### test_dmn_nip_classification_assistencial_contesta_negativa
- **Given/When** start com `classificacao_nip=assistencial`, `tema_nip=negativa_cobertura`, `contesta_negativa=true`
- **Then** `nip_classification` retorna `classificacao=ASSISTENCIAL_CONTESTA_NEGATIVA`, `grupo_revisor=juridico-regulatorio`; `nip.classified` publicado com `prazo_dias`

### test_a2a_start_via_nip_instruct
- **Given** delegacao A2A `nip.instruct` (Helena/intake → Gustavo) com `origem_a2a=true`
- **When** `msg.nip.instruct` correlaciona a business key
- **Then** instancia inicia (`nip.received`); convoca `operadora.nip.instruct_dossier` (Gustavo monta dossie, nao decide merito)

## Prazos / SLA (HARD — DRAFT/verify regulatorio)

### test_prazo_nip_dispara_alerta_nao_interruptivo
- **Given** `UT_ElaborarRespostaNip` aberta; `nip_sla.sla_alerta_iso` atingido
- **When** job do timer de alerta executado
- **Then** `operadora.events.publish` recebeu task (ST_NotificarRiscoPrazo, convertida do topico especifico
  para o generico em t3.1); payload `agents.events.nip.deadline_risk` publicado (countdown WD.3) — ver as
  9 asserções em `tests/integration/processes/test_sp_op_nip_001.py:1345,1375,1380,1392,1403,1423,1540,1626,1631`;
  a User Task segue aberta (nao-interruptivo)

### test_prazo_nip_estourado_coordenacao_assume_interruptivo
- **Given** instancia aberta alem de `nip_sla.prazo_resposta_iso`
- **When** job do timer interruptivo de prazo executado
- **Then** `nip.breached` publicado (metrica de compliance — exposicao a sancao); a User Task de elaboracao e cancelada; `UT_CoordenacaoNip` criada (`coordenacao-regulatorio`/`juridico-regulatorio`); **nenhuma auto-resposta por timeout** — decisao continua humana

### test_dmn_nip_sla_assistencial_prazo_mais_curto
- **Given/When** start com `classificacao=ASSISTENCIAL_CONTESTA_NEGATIVA`
- **Then** `nip_sla.prazo_resposta_iso` reflete o prazo assistencial (~P5D ref, **DRAFT/verify** RN 388); `fonte_regulatoria` registrada na variavel

### test_anchor_failsafe_ancora_ausente_ou_lixo_nao_derruba_o_processo  (GAP-NIP-1 fail-safe)
- **Given** start com `data_recebimento_nip_iso` EM BRANCO (`""`) ou LIXO (`"not-a-date"`) — simulando um start path (bridge/REST/correlacao/fixture) que nao seede a ancora corretamente
- **When** a instancia percorre (o worker `operadora.nip.instruct_dossier` roda em ST_InstruirDossie, presente em TODO caminho antes de BRT_NipSla/timers)
- **Then** o worker aplica o fail-safe: `data_recebimento_nip_iso` corrigido para hoje (UTC, YYYY-MM-DD) como output var (verificado via GET da variavel no engine); `UT_RevisaoJuridicaNip` APARECE (sem incidente de parse FEEL em BRT_NipSla, sem insta-fire do boundary interruptivo); o `dueDate` do job `BT_PrazoRevisaoEstourado` esta no FUTURO e ~hoje+P5D. Datas VALIDAS no passado NAO caem no fail-safe (devem estourar — NIP realmente vencida).

### test_prazo_ancora_em_data_recebimento_nip_nao_em_attach_da_ut  (GAP-NIP-1)
- **Given** `data_recebimento_nip_iso` setado no FUTURO (ex.: hoje + N dias) no start da instancia — de proposito distinto do instante em que `UT_RevisaoJuridicaNip` e efetivamente criada (que so ocorre apos dossie de Gustavo + 3 BusinessRuleTasks, ou seja, alguns segundos depois do start)
- **When** a instancia percorre ate `UT_RevisaoJuridicaNip` abrir e o job de timer `BT_PrazoRevisaoEstourado`/`BT_AlertaPrazoRevisao` e localizado (sem `execute_job` ainda)
- **Then** o `dueDate` do job de timer bate com `data_recebimento_nip_iso + duracao regulatoria` (calculado em Python no teste) — E NAO com "momento do attach da UT + duracao" (que seria ~N dias antes, dado que o attach ocorre segundos apos o start, nao no futuro configurado). Prova que o timer usa `timeDate` absoluto ancorado em `data_recebimento_nip_iso` (contrato SP-OP-NIP-001.md §"SLAs"), nao mais `timeDuration` relativo ao attach da activity. Em seguida, `execute_job` dispara o timer (sem sleep) e o fluxo segue normalmente (alerta nao-interruptivo continua a UT aberta; interruptivo cancela a UT e cria `UT_CoordenacaoNip`), confirmando que o mecanismo `timeDate` e engine-deployavel.

## Solicitacao de informacao

### test_solicitar_info_aguarda_e_retoma
- **Given** `UT_RevisaoJuridicaNip` completa com `decisao_nip=SOLICITAR_INFO`
- **When** aguarda em event gateway; `msg.nip.info_recebida` correlacionada (business key)
- **Then** fluxo retoma para elaboracao/revisao; nenhum desfecho adverso enquanto a info nao chega

## Idempotencia

### test_business_key_uma_instancia_por_nip
- **Given** instancia ativa `NIP-amh-NIP-TESTE-0001`
- **When** reentrega da mesma NIP (mesmo `numero_nip_ans`)
- **Then** sem segunda instancia ativa; start retorna a existente

## Handoff a ANS-SUBMIT

### test_handoff_ans_submit_apos_decisao_humana
- **Given** qualquer desfecho que exige filing formal (favoravel, mantida, nao-assistencial) apos User Task humana
- **When** `operadora.nip.handoff_ans_submit` executa
- **Then** payload carrega `protocolo_ans`/`numero_nip_ans`+`texto_resposta_nip`+`revisor_id`; o filing legalmente vinculante e responsabilidade de SP-OP-ANS-SUBMIT-001 (HITL pre-filing, nao-repudio ADR-0007) — NIP-001 nao transmite diretamente a ANS
