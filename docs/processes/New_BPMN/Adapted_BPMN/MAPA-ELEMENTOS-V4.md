# MAPA-ELEMENTOS-V4 — elemento → capacidade → processo SP-OP → código/teste (W2 CORP-2.7, D-02)

**Data:** 2026-10-23 · **Base:** `main` @ `5a8ba09e` (contém as ondas CORP-2.1..2.6 já merged: CA_3 #930/`179a970a`, CA_0 #932/`3b07bfbb`, CA_2 #933/`5a8ba09e`; CA_1 intocado desde `067cf607`). **Decisão que governa:** D-02, opção (a) — MAPA primeiro; ponte = programa à parte, APENAS para lacuna provada por este mapa, como NOVOS process-keys `SP-OP-*` pelo caminho canônico (allowlist `src/maezo/tools/process_allowlist.py:21-42` + `src/maezo/platform/deploy/engine_deploy.py` + cercas `effect_pep.py:174`/`transport.py:1759`), **NUNCA** como os 44 beans AI delegados (D-02: "forquilha de autoridade de efeitos"; racional integral em `docs/plans/maezo-completion-program.md` §Decisões, ruling D-02).

**CA_1 é mapeamento leitura-only.** Nenhum `.bpmn` foi editado por este mapa (a onda de reparos fechou; D-30 resguarda CA_1). Nenhum bloco de aprovação. Nenhuma ponte runtime implementada — o mapa É a entrega (CORP-2.7, opção (a) do ruling).

---

## §1 Denominador — contagem própria do XML atual (não do inventário antigo)

Método: parser namespace-aware (ElementTree), qualquer profundidade sob `<process>`; elemento = **processo + tasks (todos os tipos, incl. `callActivity` e a task sem tipo) + gateways + events** (start/end/catch/throw/boundary). O container `subProcess` NÃO entra no denominador (conteúdos contam individualmente) — mesma convenção do inventário da auditoria (324 v3 → 355 v4-pré-CORP), para as tabelas alinharem por diff. Script de contagem/reconciliação: `/tmp/w2_mapa_contagem.py` (python stdlib; saída colada em §7).

| Arquivo (sha256 em main @ 5a8ba09e) | processos | tasks | gateways | events | **denominador** |
|---|---|---|---|---|---|
| `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn` (`a01b1a3e…`) | 7 | 36 (2 uT + 31 sT + 3 cA) | 21 (19X + 2P) | 34 (8+20+1+2+3) | **98** |
| `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn` (`067cf607…`) | 5 | 21 (4 uT + 10 sT + 7 cA) | 21 (20X + 1EB) | 57 (10+28+4+6+9) | **104** |
| `CA_2_UTILIZACAO_v4_adaptado.bpmn` (`a2f2ddde…`) | 5 | 11 (2 uT + 2 sT + 2 rT + 4 cA + 1 task) | 12 (9X + 2P + 1EB) | 40 (8+15+2+9+6) | **68** |
| `CA_3_SUPORTE_v4_adaptado.bpmn` (`f9db4574…`) | 5 | 25 (10 uT + 10 sT + 5 cA) | 18 (18X) | 41 (7+22+0+2+10) | **89** |
| **TOTAL** | **22** | **93** | **72** | **172** | **359** |

Soma checada: 98+104+68+89 = **359** = 22+93+72+172 ✓. Deltas vs inventário v4-pré-CORP (355): **+4, todos no CA_0** (#932: processo `Process_CA_DS_Monitoramento` +3 elementos: start de sinal, serviceTask, end). CA_1/CA_2/CA_3: contagens idênticas (CA_2 ganhou messageFlow 9→10 e DI nomeada — fora do denominador; CA_3 re-tipou `S4_T_Ouvidoria` task→userTask — troca de tipo, mesma contagem).

**Relatados à parte (fora do denominador):** sequenceFlow 314 (91+91+55+77) · subProcess 4 (0+1+3+0) · textAnnotation 2 (CA_0, CA_3) · association 2 · dataObject 24 · dataObjectReference 24 · dataStore 16 · dataStoreReference 19 · dataInputAssociation 43 · dataOutputAssociation 3 · participant 34 · messageFlow 36 (6+15+10+5) · laneSet 21 · lane 57 (16+16+12+13) · declarações 42 (36 message + 1 signal + 4 error + 1 escalation) · BPMNDiagram 8 planos (**0 sem id** — ver §8) · BPMNShape 477 · BPMNEdge 398 · conditionExpression 53 · collaboration 4.

## §2 Estados terminais e catálogo de casas

Cada um dos 359 elementos está em **exatamente 1** estado terminal:

- **CASA** — existe capacidade runtime em main que exerce a função do elemento (worker/motor/fila humana/DMN/escalação/observabilidade), com caminho + teste citáveis. O elemento do v4 é *vocabulário de desenho* dessa capacidade existente; a admissão runtime do desenho é a ponte (à parte, se o mapa provar lacuna de chave).
- **LACUNA** — nenhuma capacidade existente exerce a função; único candidato legítimo a ponte futura, delimitado ao caminho canônico D-02 (§8/§9). LACUNA **não** é autorização de implementação.

Catálogo de casas (referenciado como C-1..C-14 nas tabelas):

| Ref | Capacidade existente (o que absorve) | Código | Teste/lane |
|---|---|---|---|
| C-1 | **Roteador/triagem helena** — classificação de intenção/evento, conversa de atendimento, aviso factual, recusa fail-closed (absorve `eventClassifierAI`, `intentTriageAI`, `contactCenterAI` como vocabulário) | `src/maezo/agents/helena/roteador.py`, `roteador_contrato.py`, `triagem_v2.py`, `conversa.py`, `historico.py`, `cadastro.py` | `tests/unit/agents/test_helena_roteador_ativo.py`, `test_helena_roteador_contrato.py`, `test_helena_triagem_v2.py` (lane unit/restante) |
| C-2 | **Workers SP-OP-AUTH-001** — autorização prévia: critérios computados, rota automática com `numero_autorizacao` TISS, requisição (absorve `authorizationRequestService`, `autoAuthorizationService`, `coverageRulesEngine`) | `src/maezo/tools/workers/auth.py`, `auth_criteria.py`, `auth_exact_amount.py`; spec `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:207` | `tests/integration/processes/test_sp_op_auth_001.py`; `tests/unit/tools/workers/test_auth_auto_criteria.py`, `test_auth_denial_guard.py` |
| C-3 | **Contas/glosa/recurso/pagamento** — liquidação e adjudicação de conta (absorve `U4_T_Liquidar` e o vocabulário TISS/glosa) | `src/maezo/tools/workers/contas.py`, `recurso.py`, `pagto.py`; keys `SP-OP-CONTAS-001`, `SP-OP-RECURSO-001`, `SP-OP-PAGTO-001` | `tests/integration/processes/test_sp_op_{contas,recurso,pagto}_001.py` |
| C-4 | **Escalonamento humano universal** — os 10 ramos de SLA do corpus + boundaries de SLA (padrão F-04) | `src/maezo/tools/workers/escalation.py`; key `SP-OP-ESCALATION-001` | `tests/integration/processes/test_sp_op_escalation_001.py`, `test_sp_op_escalation_001_supervisor_inbox.py` |
| C-5 | **Publicador de eventos de domínio** — marcos de jornada/business events (absorve `journeyTransitionPublisher`, `businessEventPublisher`, `comunicarClientePublisher` como mecanismo de publicação) | `src/maezo/tools/workers/events.py` (`operadora.events.publish`), `src/maezo/gateway/kafka_client.py` | `tests/unit/tools/workers/test_events.py`, `test_events_sla_resolucao.py` |
| C-6 | **LGPD/consentimento/auditoria** — consulta e trilha de consentimento, direitos do titular (absorve `lgpdConsentService`, `lgpdAuditService`; substrato AMH consent) | `src/maezo/tools/workers/lgpd.py`; `src/maezo/gateway/amh_consent_revision.py`; `src/maezo/gateway/audit_anchor.py`; keys `SP-OP-LGPD-DSR-001` (+consent-gate `SP-OP-PROGRAMA-001`, `src/maezo/tools/workers/programa.py`) | `tests/integration/processes/test_sp_op_lgpd_dsr_001.py`; `tests/unit/gateway/amh*` |
| C-7 | **Fila humana SP-OP** — userTasks no engine CIB Seven + inbox de supervisor + `HumanBasis` de decisão (absorve toda userTask; binding de `formKey ced:*` continua wiring futuro — GUIA/CORRECOES já registram) | `src/maezo/gateway/human/decision.py`; `src/maezo/tools/mcp_cibseven/transport.py`; read-provider `src/maezo/portal/engine/read-provider/` | `tests/integration/processes/test_sp_op_escalation_001_supervisor_inbox.py`; `HumanTaskReadJarIT` (java-plugin) |
| C-8 | **Cancelamento contratual** — `SP-OP-CANCEL-001` (cancela contrato; fronteira A-06: slot ≠ contrato preservada) | `src/maezo/tools/workers/cancel.py` | `tests/integration/processes/test_sp_op_cancel_001.py`; `test_t33_a1_cancel_handoff_redelivery_idempotency.py` |
| C-9 | **NIP/ouvidoria ANS** — `SP-OP-NIP-001` resposta a NIP (casa parcial do rito ouvidoria; prazo ≤7du RN 623 art. 16 é REG-1.3, pendência registrada) | `src/maezo/tools/workers/nip.py` | `tests/integration/processes/test_sp_op_nip_001.py` |
| C-10 | **Autoridade comercial congelada** — orquestração de venda guardada, catálogo/admissão, aceite no motor (C6-4 congelado, D-15) | `src/maezo/gateway/commercial.py`, `commercial_admission.py`; registro `docs/delivery/commercial-platform/CONTRACT-FREEZE-C6-4-REGISTER.md` | `tests/unit/test_commercial_c6.py`, `tests/unit/gateway/test_commercial_*.py`; `tests/integration/processes/test_aceite_motivos_no_motor.py` |
| C-11 | **Motor de jornadas + handoffs** — máquina de estados única, transições T-*, ingestão de intents | `src/maezo/gateway/capabilities/journeys/driver.py` (`JourneyDriver`), `topology.py`; `src/maezo/gateway/intake/` | `tests/unit/gateway/capabilities/journeys/test_driver.py`; `tests/integration/processes/test_t33_a2_agent_start_idempotency_matrix.py` |
| C-12 | **Negativa com fundamento** — parecer/negativa assistencial com base registrada (absorve o domínio do parecer de C3) | `src/maezo/gateway/denial_notices/producer.py`, `models.py` | `tests/unit/gateway/denial_notices/test_guard_and_ownership.py` |
| C-13 | **Observabilidade/alerta** — o alerta ao time de dados tem consumidor in-corpus (`Process_CA_DS_Monitoramento`, #932) e substrato de alerta/observabilidade | `src/maezo/platform/observability.py`; `src/maezo/agents/helena/alarme_deterministico.py` | testes de observabilidade em `tests/unit/platform/` (lane unit/gateway-platform) |
| C-14 | **Cron/ingestão ANS e timers de engine** — crons mensais/trimestrais (key não-admitido `SP-OP-ANS-CRON-001`, `ans_cron.py`) + timers do engine | `src/maezo/tools/workers/ans_cron.py`, `ans_submit.py` | `tests/integration/processes/test_sp_op_ans_cron_001.py`, `test_sp_op_ans_submit_001.py` |

## §3 CA_0 — Serviços compartilhados (98 = 29 CASA + 69 LACUNA)

Famílias de lacuna: **L1** despacho proativo omnicanal ao cliente · **L2** resolução de identidade · **L4** NBA/aprendizado · **L5** perfil 360/enriquecimento/ingestão · **L9** ciclo de vida de modelo ML.

| Elemento(s) | Tipo | Capacidade/casa | Processo SP-OP (chave allowlist) | Código/teste | Estado |
|---|---|---|---|---|---|
| `Process_CA_H2_ComunicarCliente` | process | jornada de comunicação proativa — sem casa (L1) | — | LACUNA→candidato SP-OP-NOTIF-001 | LACUNA |
| `H2_T_Classificar` | serviceTask | classificação de evento/intenção (bean `eventClassifierAI`) | — (vocabulário absorvido por C-1) | C-1 | **CASA** |
| `H2_T_Revisao` | userTask (`ced:revisao-clinica-mensagem`) | fila humana SP-OP | — (C-7) | C-7 | **CASA** |
| `H2_B_SLA_Revisao`, `H2_T_EscalarRevisao`, `H2_End_SLA_Revisao` | boundary+serviceTask+endEvent | escalonamento humano (padrão F-04) | **SP-OP-ESCALATION-001** | C-4 | **CASA** |
| `H2_Start`, `H2_T_Gerar`, `H2_GW_Tipo`, `H2_GW_Confiavel`, `H2_GW_MergeRevisao`, `H2_CA_Canal`, `H2_GW_Envio`, `H2_EV_Janela`, `H2_End_Suprimido`, `H2_T_Enviar`, `H2_GW_Entregue`, `H2_End_Informado`, `H2_T_Fallback`, `H2_End_Contingencia`, `H2_T_Portal`, `H2_End_Portal` | start/task×4/gw×5/cA/catch/end×5 (16) | geração/despacho/janela/canal de mensagem proativa — sem casa (L1; canais conversacionais existem em C-1, campanha não) | — | LACUNA→SP-OP-NOTIF-001 | LACUNA |
| `H2_T_Log`, `H2_End_Log` | serviceTask+endEvent | registro em contexto do cliente — sem casa (L5) | — | LACUNA→SP-OP-PERFIL-001 | LACUNA |
| `Process_CA_H2_SelecionarCanal` + `SC_Start`, `SC_T_Preferencias`, `SC_GW_OptIn`, `SC_End_OptOut`, `SC_T_Canal`, `SC_GW_Critico`, `SC_T_Capping`, `SC_GW_Decisao`, `SC_GW_MergeEnviar`, `SC_End_Enviar`, `SC_End_Adiar`, `SC_End_Suprimir` | process + 11 (12) | preferência/opt-in/capping/rota de canal — sem casa (L1; domínio "não-contato" tem casa parcial C-6/C7-supp de escopo vendor) | — (nota: extensão de `SP-OP-SUPP-001` é a absorção candidata do trio opt-in) | LACUNA→SP-OP-NOTIF-001 | LACUNA |
| `Process_CA_H1_IdentidadeConsentimento` | process | gate de identidade+consentimento — casa parcial (C-6; identidade = L2) | **SP-OP-LGPD-DSR-001** | C-6 | **CASA** (parcial) |
| `H1_Start`, `H1_T_Consentimento`, `H1_GW_Consentimento`, `H1_T_Auditoria`, `H1_End_Negado` | start/task×2/gw/end (5) | consentimento por finalidade + trilha de auditoria | **SP-OP-LGPD-DSR-001** | C-6 | **CASA** |
| `H1_T_Diagnostico`, `H1_GW_Identidade`, `H1_T_Unificar`, `H1_GW_Merge`, `H1_End_OK` | task/gw×2/end (5) | avaliação/unificação de identidade (`identityResolutionService`) — 0 hits em src (L2) | — | LACUNA→candidato: absorção em SP-OP-LGPD-DSR-001 ou nova SP-OP-IDENT-001 | LACUNA |
| `Process_CA_H3_ConhecerCliente` | process | ciclo de enriquecimento/modelo — casa parcial (C-5/C-14 para eventos e cron; L5/L9 para o núcleo) | — | — | LACUNA (parcial) |
| `H3_Start_Ciclo` | startEvent (cron 03:00) | crons de engine | — (substrato C-14, key ANS-CRON não-admitido por desenho) | C-14 | **CASA** |
| `H3_Start_Evento`, `H3_T_Registrar`, `H3_EV_AlertaDS`, `H3_EV_Recomendar`, `H3_End_Evento`, `H3_GW_MergeEvt` | start/throw×2/task/gw/end (6) | ingestão e publicação de eventos de domínio (C-5) + alerta com consumidor in-corpus (C-13; Gate-3 PAREADO §8) | — (marco = C-5) | C-5, C-13 | **CASA** |
| `H3_T_Lote`, `H3_CA_Lote` | serviceTask+callActivity (2) | seleção de lote + enriquecimento (L5) | — | LACUNA→SP-OP-PERFIL-001 | LACUNA |
| `H3_T_Retreinar`, `H3_GW_Modelo`, `H3_T_Promover`, `H3_End_Pipeline`, `H3_End_ModeloRetido` | task×2/gw/end×2 (5) | ciclo de vida de modelo ML (`modelRegistryService`; classe `br.amh.ced.m1.DataScienceTrainingJob` 0 hits) — fora do escopo SP-OP, registrado | — | LACUNA L9 (sem candidato SP-OP; plataforma de ML é programa à parte) | LACUNA |
| `H3_GW_Nutrir` | exclusiveGateway | decisão de nutrição (comunicação) — sem casa (L1) | — | LACUNA→SP-OP-NOTIF-001 | LACUNA |
| `Process_CA_H3_EnriquecerCliente`, `EC_Start`, `EC_GW_Split`, `EC_GW_Join`, `EC_T_Abertos`, `EC_T_Uso`, `EC_T_Digitais`, `EC_T_Clinicos`, `EC_T_Pesquisas`, `EC_T_Consolidar`, `EC_End` | process + 10 (11) | fanout de enriquecimento/perfil 360 (`openDataEnrichmentService`, `planUsageCollector`, `digitalSignalCollector`, `clinicalDataIngestion`, `surveyResponseCollector`, `profileConsolidationService` — 0 hits) — sem casa (L5; bases LGPD arts. 33/16 = REG-1.10/REG-1.12 pendentes) | — | LACUNA→SP-OP-PERFIL-001 | LACUNA |
| `EC_CA_H1`, `EC_B_Negado`, `EC_End_Negado` | callActivity+boundary+end (3) | gate de consentimento + trilha de negado | **SP-OP-LGPD-DSR-001** | C-6 | **CASA** |
| `Process_CA_H3_ProximaMelhorAcao` | process | NBA — F2 design-mapped (`contracts.json:1892`, W5, lead lucas, `runtime_admitted:false`) e 0 runtime (L4) | — | LACUNA→SP-OP-NBA-001 | LACUNA |
| `NBA_T_Revisao` | userTask (`ced:revisao-nba`) | fila humana SP-OP | — (C-7) | C-7 | **CASA** |
| `NBA_B_SLA_Revisao`, `NBA_T_EscalarRevisao`, `NBA_End_SLA_Revisao` | boundary+task+end (3) | escalonamento humano | **SP-OP-ESCALATION-001** | C-4 | **CASA** |
| `NBA_Start`, `NBA_T_Contexto`, `NBA_GW_ColdStart`, `NBA_T_Ranquear`, `NBA_T_Popularidade`, `NBA_GW_Merge`, `NBA_T_Guardrails`, `NBA_T_Explicar`, `NBA_GW_Clinico`, `NBA_GW_MergeFim`, `NBA_End` | start/task×5/gw×4/end (11) | motor NBA (ranking/cold-start/guardrail/explicação/feature store — 0 hits) — sem casa (L4) | — | LACUNA→SP-OP-NBA-001 | LACUNA |
| `Process_CA_DS_Monitoramento`, `DS_Start_AlertaDS`, `DS_T_RegistrarAlerta`, `DS_End_AlertaRegistrado` | process+start+task+end (4) | consumidor do sinal `Signal_CA_AlertaDS` (fechado no corpus por #932) + substrato de alerta | — (monitoramento interno CA_0; sem chave — admissão runtime = ponte à parte se o dono quiser o consumo em engine) | C-13 | **CASA** |

Soma CA_0 (linhas, incl. processo): H2 5+19=24 · H2.1 0+13=13 · H1 6+5=11 · H3 7+9=16 · H3.1 3+11=14 · H3.2 4+12=16 · DS 4+0=4 → **98** ✓ (CASA 29, LACUNA 69).

## §4 CA_1 — Compra (com suporte) (104 = 55 CASA + 49 LACUNA) — LEITURA-ONLY

Famílias: **L3C** oferta/agenda de compra (casa parcial = autoridade comercial C-10; ponte = wiring, não processo novo) · **L4** NBA · **L5** perfil · **L6** agenda/slot/reagendamento · **L1** lembretes/cadência.

| Elemento(s) | Tipo | Capacidade/casa | Processo SP-OP | Código/teste | Estado |
|---|---|---|---|---|---|
| `Process_CA_COMPRA` | process | macro de compra — casa parcial (C-10 + cadastro C-1 + C-8) | — (autoridade comercial congelada C6-4, D-15) | C-10 | **CASA** (parcial) |
| `CP_Start_NovoBeneficiario` | startEvent | cadastro de beneficiário | — (C-1 `cadastro.py`) | C-1 | **CASA** |
| `CP_B_ConsentimentoNegado` | boundaryEvent | gate de consentimento | **SP-OP-LGPD-DSR-001** | C-6 | **CASA** |
| `CP_ES_Start_Ajuda`, `CP_ES_End_Ajuda` | start+end do ES de ajuda (2) | pedido de ajuda → suporte (porta helena) | — (C-1) | C-1, C-11 | **CASA** |
| `CP_End_Suporte`, `CP_End_Recovery` | endEvent (2) | handoff para suporte | — (C-11) | C-11 | **CASA** |
| `CP_End_DireitoEncerrado` | endEvent | encerramento de direito (contrato) | **SP-OP-CANCEL-001** | C-8 | **CASA** |
| `CP_Start_Necessidade`, `CP_Start_Reacao`, `CP_Start_NovaNecessidade`, `CP_Start_ConsumoNaoRealizado`, `CP_GW_MergeIntencao`, `CP_CA_C1`, `CP_GW_Proposta`, `CP_CA_C2`, `CP_GW_Decisao`, `CP_End_Recusa`, `CP_End_SemDecisao`, `CP_GW_MergeHabilitar`, `CP_CA_C3`, `CP_GW_Habilitado`, `CP_End_DireitoHabilitado`, `CP_CA_C4`, `CP_GW_Manutencao`, `CP_GW_MergeSuporte`, `CP_End_SemConsentimento`, `CP_End_Nutrir`, `CP_GW_SemOferta`, `CP_GW_TipoDireito`, `CP_End_VendaPerdida`, `CP_End_Beneficiario` | 24 (starts×4, cA×4, gw×8, end×8) | orquestração compra/oferta/nutrição (L3C; a casa da transação é C-10, a orquestração destes nós é wiring futuro) | — | LACUNA→wiring sobre C-10 (não é candidato a processo novo) | LACUNA |
| `Process_CA_COMPRA_C1_EntenderRecomendar` | process | entender/recomendar — NBA (L4) + oferta (L3C) sem runtime | — | LACUNA→SP-OP-NBA-001 + wiring C-10 | LACUNA |
| `C1_Start`, `C1_CA_H1` | start+callActivity (2) | intake + gate H1 | **SP-OP-LGPD-DSR-001** (via H1) | C-6, C-11 | **CASA** |
| `C1_CA_NBA`, `C1_T_Reward` | callActivity+serviceTask (2) | chamada do recomendador + registro de adesão (L4) | — | LACUNA→SP-OP-NBA-001 | LACUNA |
| `C1_T_Agenda`, `C1_T_Capacidade` | serviceTask (2) | agenda em tempo real + política de capacidade (`schedulingAIAgent`, `capacityPolicyService` — 0 hits; L6/L3C) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `C1_T_Cruzar` | serviceTask | cruzar perfil × oferta (`offerMatcherService`; L3C; perfil = L5) | — | LACUNA→wiring C-10 | LACUNA |
| `C1_GW_Proposta`, `C1_End_Proposta`, `C1_End_SemProposta` | gw+end×2 (3) | desfecho de proposta (L3C) | — | LACUNA→wiring C-10 | LACUNA |
| `Process_CA_COMPRA_C2_DecidirContratar` | process | decisão/contratação — casa parcial (C-10 + aceite no motor) | — | C-10 | **CASA** (parcial) |
| `C2_Start`, `C2_T_Aceite`, `C2_EV_Agendado`, `C2_B_Recusa`, `C2_End_Aceite`, `C2_End_Recusa` | start+uT+throw+boundary+end×2 (6) | aceite do cliente no motor + marcos + recusa | — (C-10; fila humana C-7; marcos C-5) | C-10, C-5, C-7 | **CASA** |
| `C2_EV_Lembrete`, `C2_B_Lembrete`, `C2_End_Lembrete`, `C2_B_PrazoMax`, `C2_End_SemResposta` | throw+boundary+end×2+boundary (5) | cadência de lembrete e prazo de decisão — política comercial sem runtime (L1-cadência; `${prazoMaximoDecisao}` sem default é contrato de ingresso, CORRECOES §6.5) | — | LACUNA→SP-OP-NOTIF-001 (+política C-10) | LACUNA |
| `Process_CA_COMPRA_C3_HabilitarDireito` | process | habilitação/autorização — **SP-OP-AUTH-001 absorve** (ruling D-02: `authorizationRequestService` ≈ workers SP-OP-AUTH) | **SP-OP-AUTH-001** | C-2 | **CASA** |
| `C3_Start`, `C3_GW_Tipo`, `C3_GW_MergeEleg`, `C3_T_Elegibilidade`, `C3_GW_Auto`, `C3_T_AutoAutorizar`, `C3_GW_MergeAut`, `C3_EV_Autorizado`, `C3_End_Habilitado`, `C3_GW_MergeReq`, `C3_T_Requisitar`, `C3_EV_EmAnalise`, `C3_GW_Espera`, `C3_EV_Concedida`, `C3_EV_Pendencia`, `C3_EV_Negada`, `C3_EV_Expirou` (PT48H — valor = D-05/RN 623, REG-1.2), `C3_GW_Pendencia`, `C3_GW_TipoNegativa`, `C3_T_Parecer`, `C3_B_SLA_Parecer`, `C3_T_EscalarParecer`, `C3_End_SLA_Parecer`, `C3_GW_MergeReneg`, `C3_T_Renegociar`, `C3_B_SLA_Renegociar`, `C3_T_EscalarRenegociar`, `C3_End_SLA_Renegociar`, `C3_GW_Alternativa`, `C3_End_Perdida`, `C3_End_Suporte`, `C3_End_Plano` | 32 (start, cA? não — gw×9, task×5, uT×2, catch×4, throw×2, boundary×2, end×6, EBG×1) | critérios de autorização, rota automática TISS, requisição, desfechos, negativa com fundamento, fila humana de parecer/alternativa, escalações F-04 | **SP-OP-AUTH-001** (+negativa C-12; fila humana C-7; escalação **SP-OP-ESCALATION-001**; marcos C-5) | C-2, C-12, C-7, C-4, C-5 | **CASA** |
| `C3_CA_Perfil` | callActivity | chama `EnriquecerCliente` (L5) | — | LACUNA→SP-OP-PERFIL-001 | LACUNA |
| `Process_CA_COMPRA_C4_ManterDireito` | process | manutenção de slot — sem casa (L6) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `C4_Start`, `C4_T_Confirmar`, `C4_EV_Agendado`, `C4_End_Remarcado`, `C4_End_Cancelado` | start+uT+throw+end×2 (5) | trigger intake, confirmação humana, marco, cancelamento pelo cliente (→ contrato ≠ slot, A-06) | **SP-OP-CANCEL-001** (só `C4_End_Cancelado`) + fila humana C-7 + marcos C-5 | C-8, C-7, C-5 | **CASA** |
| `C4_GW_Motivo`, `C4_T_Reagendar`, `C4_B_SemSlot`, `C4_B_Lembrete`, `C4_B_PrazoMax`, `C4_EV_Lembrete`, `C4_End_Lembrete`, `C4_End_SemConfirmacao`, `C4_End_SemSlot` | gw+task+boundary×3+throw+end×3 (9) | reagendamento automático em slot equivalente (`autoRebookingService`; erro `SEM_SLOT_EQUIVALENTE` = contrato declarado externo, F-05/§8) — sem casa (L6) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |

Soma CA_1: (1+1+2+2+1) + 24 + (1+2+2+2+3) + (1+6+5) + (1+32+1) + (1+5+9) = **104** ✓ (CASA 55, LACUNA 49).

## §5 CA_2 — Utilização (68 = 40 CASA + 28 LACUNA)

Famílias: **L6** agenda/no-show/slot · **L1** comunicação (evento clínico, lembretes, predição) · **L5** ingestão clínica · **L8** desfecho/pesquisa de satisfação.

| Elemento(s) | Tipo | Capacidade/casa | Processo SP-OP | Código/teste | Estado |
|---|---|---|---|---|---|
| `Process_CA_UTILIZACAO` | process | orquestração de entrega de cuidado — sem casa como processo (partes com casa) | — | — | LACUNA (parcial) |
| `UT_Start` | startEvent | intake pós-habilitação | — (C-11) | C-11 | **CASA** |
| `UT_CA_U4` | callActivity | chama liquidação/avaliação | **SP-OP-CONTAS-001** | C-3 | **CASA** |
| `UT_B_SLA`, `UT_End_SLA` | boundary+end (2) | escalação por SLA violado (entrada do SUPORTE) | **SP-OP-ESCALATION-001** | C-4 | **CASA** |
| `UT_ES_Start_CancCliente`, `UT_ES_End_CancCliente` | start+end (2) | cancelamento pelo cliente (consumo/contrato) | **SP-OP-CANCEL-001** | C-8 | **CASA** |
| `UT_End_Encerrado`, `UT_End_Insatisfeito` | endEvent (2) | encerramento pós-liquidação + handoff suporte | **SP-OP-CONTAS-001** (fechamento) + C-11 | C-3, C-11 | **CASA** |
| `UT_CA_U1`, `UT_CA_U2`, `UT_CA_U3` | callActivity (3) | chamadas de preparar/receber/acompanhar (jornadas sem casa, L6/L5) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `UT_GW_Status`, `UT_End_NoShow` | gw+end (2) | desfecho informado pelo prestador + no-show (contrato de dados sem runtime, L6) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `UT_GW_Satisfacao` | exclusiveGateway | roteamento de satisfação (depende da pesquisa, L8) | — | LACUNA→SP-OP-DESFECHO-001 | LACUNA |
| `UT_ES_Start_CancPrestador`, `UT_ES_End_CancPrestador` | start+end (2) | cancelamento pelo prestador → reoferta (L6; descredenciamento de rede é C-10-domínio CRED, não este nó) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `UT_EC_Start`, `UT_EC_Comunicar`, `UT_EC_End` | start+throw+end (3) | evento clínico → comunicação ao cliente (ingestão clínica L5 + comms L1; base LGPD = REG-1.12) | — | LACUNA→SP-OP-NOTIF-001 (+SP-OP-PERFIL-001) | LACUNA |
| `Process_CA_UTILIZACAO_U1_Preparar` | process | preparo/check-in — sem casa como processo | — | — | LACUNA (parcial) |
| `U1_T_CheckIn` | userTask (`ced:checkin-preparo`, cliente) | fila humana SP-OP | — (C-7) | C-7 | **CASA** |
| `U1_B_DiaServico`, `U1_EV_Pronto`, `U1_End` | boundary+throw+end (3) | timer do engine + marcos | — (C-5/C-14) | C-5 | **CASA** |
| `U1_Start`, `U1_GW_Preparo`, `U1_GW_Merge`, `U1_B_Lembrete`, `U1_EV_Lembrete`, `U1_End_Lembrete` | start+gw×2+boundary+throw+end (6) | jornada de preparo + lembrete D-1 (L6/L1) | — | LACUNA→SP-OP-AGENDA-001 / SP-OP-NOTIF-001 | LACUNA |
| `Process_CA_UTILIZACAO_U2_Receber` | process | receber o cuidado — casa parcial (marcos/escalação com casa) | — | — | **CASA** (parcial) |
| `U2_Start`, `U2_GW_Chegada`, `U2_T_Aguardar`, `U2_EV_EmAtendimento`, `U2_EV_Realizado`, `U2_End_Realizado`, `U2_B_SLA`, `U2_EV_Escalar`, `U2_End_Escalado` | start+EBG+receive+throw×2+boundary+end×2+catch (9) | espera correlacionada, marcos factuais, escalação SLA 2h | **SP-OP-ESCALATION-001** (SLA) + C-5 + C-7 | C-4, C-5 | **CASA** |
| `U2_EV_Chegou`, `U2_EV_NoShow`, `U2_T_NoShow`, `U2_End_NoShow` | catch+boundary+task+end (4) | chegada física + registro de no-show/liberação de slot (`noShowRecorder`; L6) | — | LACUNA→SP-OP-AGENDA-001 | LACUNA |
| `Process_CA_UTILIZACAO_U3_Acompanhar` | process | acompanhamento de resultado | — | — | **CASA** (parcial) |
| `U3_Start`, `U3_T_Aguardar`, `U3_B_Atraso`, `U3_EV_Resultado`, `U3_GW_Resultado`, `U3_GW_Merge`, `U3_End` | start+receive+boundary+throw+gw×2+end (7) | espera, timer de prazo, marco de resultado, regra | — (C-5/C-14; fila C-7) | C-5 | **CASA** |
| `U3_EV_Atraso`, `U3_End_Atraso` | throw+end (2) | atraso **predito** comunicado ao cliente (L1; predição = vocabulário L4 sem runtime) | — | LACUNA→SP-OP-NOTIF-001 | LACUNA |
| `Process_CA_UTILIZACAO_U4_LiquidarAvaliar` | process | liquidação/avaliação | **SP-OP-CONTAS-001** | C-3 | **CASA** |
| `U4_Start`, `U4_GW_Split`, `U4_GW_Join`, `U4_GW_EscapePesquisa`, `U4_GW_MergeProx`, `U4_B_Pesquisa`, `U4_End` | start+P×2+X×2+boundary+end (7) | topologia F-01 corrigida (join 2 entradas, escape XOR) + timer 72h — substrato engine | **SP-OP-CONTAS-001** (jornada) | C-3; regressão F-E01 pinada `tests/processes/test_ca2_v4_regression_f_e01.py` | **CASA** |
| `U4_T_Liquidar` | task (sem tipo — IA-09 residual) | liquidação de consumo/glosa | **SP-OP-CONTAS-001** / **SP-OP-PAGTO-001** | C-3 | **CASA** (tipo abstrato mantido — GUIA §6) |
| `U4_T_Pesquisa` | userTask (`ced:pos-servico`, cliente) | fila humana SP-OP (NPS/form = wiring futuro) | — (C-7) | C-7 | **CASA** |
| `U4_T_Desfecho`, `U4_GW_ProxAcao`, `U4_EV_ProxAcao` | task+gw+throw (3) | desfecho clínico + próxima ação (`careOutcomeRecorder`; L8; LGPD art. 11 §4º = COUNSEL-4/REG-1.8) | — | LACUNA→SP-OP-DESFECHO-001 | LACUNA |

Soma CA_2: (1+1+1+2+2+2) + (1+3+6) + (1+9+4) + (1+7+2) + (1+7+1+1+3) + 5 processos = **68** ✓ (CASA 40, LACUNA 28).

## §6 CA_3 — Suporte (89 = 85 CASA + 4 LACUNA)

O suporte é a jornada com mais casa: helena é a porta de atendimento real (ADR-0067), a fila humana SP-OP existe, e a escalação universal é literalmente o propósito de `SP-OP-ESCALATION-001`.

| Elemento(s) | Tipo | Capacidade/casa | Processo SP-OP | Código/teste | Estado |
|---|---|---|---|---|---|
| `Process_CA_SUPORTE` + `SP_Start_Contato`, `SP_Start_Insatisfacao`, `SP_Start_SLA`, `SP_GW_MergeInicio`, `SP_CA_S1`, `SP_GW_Origem`, `SP_CA_S2`, `SP_GW_Compra`, `SP_End_Compra`, `SP_GW_MergeResolver`, `SP_CA_S3`, `SP_CA_S4`, `SP_GW_Desfecho`, `SP_End_Resolvido`, `SP_End_Ouvidoria`, `SP_End_SemConfirmacao` | process + 16 | porta de entrada omnicanal, triagem, devolução à compra, desfechos | — (intake/triagem C-1; handoff C-11; compra C-10; ouvidoria **SP-OP-NIP-001** parcial) | C-1, C-11, C-10, C-9 | **CASA** |
| `Process_CA_SUPORTE_S1_OuvirRegistrar` + `S1_Start`, `S1_CA_H1`, `S1_B_Consent`, `S1_EV_Cuidando`, `S1_GW_MergeCons`, `S1_GW_Merge`, `S1_End` | process + 7 | registro de caso + gate H1 + aviso factual "estamos cuidando" | **SP-OP-LGPD-DSR-001** (via H1) | C-6, C-1, C-5 | **CASA** |
| `S1_T_Contexto` | serviceTask | montar contexto 360 (`contextAssemblyService`) — depende do perfil (L5) | — | LACUNA→SP-OP-PERFIL-001 | LACUNA |
| `S1_GW_Iniciativa` | exclusiveGateway | iniciativa da organização (contato ativo — consentimento de contato ativo sem runtime, L1) | — | LACUNA→SP-OP-NOTIF-001 | LACUNA |
| `Process_CA_SUPORTE_S2_DiagnosticarRotear` + `S2_Start`, `S2_T_Triagem`, `S2_GW_Clinico`, `S2_End_Humano`, `S2_End_IA` | process + 5 | triagem de intenção IA × clínico (`intentTriageAI` ≈ roteador/triagem helena; DMN de códigos) | — (C-1) | C-1; `tests/unit/agents/test_helena_codigos_casam_com_a_dmn.py` | **CASA** |
| `Process_CA_SUPORTE_S3_ResolverEncaminhar` (43: start 1 · gw 6 (`S3_GW_Origem`, `S3_GW_Confianca`, `S3_GW_MergeHumano`, `S3_GW_Natureza`, `S3_GW_MergeAtend`, `S3_GW_PrimeiroContato`) · uT 8 (`S3_T_Especialista`, `S3_T_Enfermagem`, `S3_T_Protocolo`, `S3_T_Cumprir`, `S3_T_Priorizar`, `S3_T_Recovery`, `S3_T_Supervisor`, `S3_T_Intervencao`) · sT 7 (`S3_T_IA` + 6 `S3_T_Escalar*`) · boundary 8 (6 SLA + `S3_B_SLARecovery` + `S3_B_D1`) · end 13) | 43 | resposta assistida por IA com verificação, fila humana de resolução/protocolo/supervisão, escalações F-04 e SLA de recovery | **SP-OP-ESCALATION-001** (todos os ramos de SLA/D-1) + fila humana C-7 + C-1 (`contactCenterAI` ≈ conversa helena c/ guardrails de confiança) + C-5 (marcos) | C-4, C-7, C-1, C-5 | **CASA** (43/43) |
| `Process_CA_SUPORTE_S4_ConfirmarEncerrar` + `S4_Start`, `S4_T_Confirmar`, `S4_GW_Resolvido`, `S4_GW_Rota`, `S4_T_Ouvidoria` (userTask — tipada em #930; **sem binding** de executor/alçada/conclusão), `S4_GW_MergeAprender`, `S4_End`, `S4_End_Reencaminhar`, `S4_B_Silencio` | process + 9 | confirmação do cliente, rota de ouvidoria/ANS com retorno, reencaminhamento, silêncio 24h | **SP-OP-NIP-001** (casa parcial do rito ouvidoria — prazo ≤7du RN 623 art. 16 = REG-1.3) + fila humana C-7 + C-4 | C-9, C-7, C-4 | **CASA** |
| `S4_T_Aprender`, `S4_EV_Aprendizado` | serviceTask+throw (2) | registro para treinar a IA (`interactionLearningService`; consentimento de uso do dataset = GUIA Q-2 de S4) — sem casa (L4) | — | LACUNA→SP-OP-NBA-001 (aprendizado) | LACUNA |

Soma CA_3: (1+16) + (1+7) + 1 + 1 + (1+5) + 43 + (1+9) + 2 = **89** ✓ (CASA 85, LACUNA 4).

## §7 Reconciliação maquinável (soma checada — saída do script)

Script: `/tmp/w2_mapa_contagem.py` (python stdlib; contagem ElementTree namespace-aware, pareamento corpus-inteiro, 0 edições nos `.bpmn`).

```
A. CONTAGENS POR ARQUIVO
   CA_0: DENOMINADOR = 98   (process=7 userTask=2 serviceTask=31 callActivity=3 XOR=19 AND=2 start=8 end=20 catch=1 throw=2 boundary=3)
   CA_1: DENOMINADOR = 104  (process=5 userTask=4 serviceTask=10 callActivity=7 XOR=20 EBG=1 start=10 end=28 catch=4 throw=6 boundary=9)
   CA_2: DENOMINADOR = 68   (process=5 userTask=2 serviceTask=2 receiveTask=2 callActivity=4 task=1 XOR=9 AND=2 EBG=1 start=8 end=15 catch=2 throw=9 boundary=6)
   CA_3: DENOMINADOR = 89   (process=5 userTask=10 serviceTask=10 callActivity=5 XOR=18 start=7 end=22 throw=2 boundary=10)
   planos DI sem id: CA_0=0 CA_1=0 CA_2=0 CA_3=0
B. RECONCILIACAO — soma checada
   por arquivo: CA_0=98 | CA_1=104 | CA_2=68 | CA_3=89
   TOTAL DENOMINADOR CORPUS = 359 = 22 processos + 93 tasks + 72 gateways + 172 events
   soma por arquivo 359 == total 359: OK
D. GATE-3 SET-LEVEL
   ids de denominador duplicados no corpus: 0
   [PAREADO] Signal_CA_AlertaDS ('Alerta Time Data Science')
      THROW: CA_0#H3_EV_AlertaDS
      CATCH: CA_0#DS_Start_AlertaDS        <- Process_CA_DS_Monitoramento (#932)
   [PAREADO] Error_CA_ConsentimentoNegado: THROW CA_0#H1_End_Negado; CATCH CA_0#EC_B_Negado, CA_1#CP_B_ConsentimentoNegado, CA_3#S1_B_Consent
   [SEM-PAR] Error_CA_SemSlotEquivalente ('Sem slot equivalente para reagendamento'): THROW - ; CATCH CA_1#C4_B_SemSlot
F. DECLARACOES: message=36 (2/15/13/6) · signal=1 · error=4 (1/2/0/1) · escalation=1
```

Estados terminais (contagem deste documento, somada por arquivo): CA_0 29 CASA + 69 LACUNA = 98 ✓ · CA_1 55 + 49 = 104 ✓ · CA_2 40 + 28 = 68 ✓ · CA_3 85 + 4 = 89 ✓. **Total: 209 CASA + 150 LACUNA = 359 — zero none-por-silêncio** (todo elemento em exatamente 1 estado; nenhum elemento citado em duas linhas; IDs duplicados no corpus: 0).

## §8 Veredito Gate-3 set-level (re-derivado na árvore nova)

1. **`Signal_CA_AlertaDS` — FECHADO.** Emissor `CA_0#H3_EV_AlertaDS`; consumidor `CA_0#DS_Start_AlertaDS` (start de sinal de `Process_CA_DS_Monitoramento`, #932/`3b07bfbb`, plano DI nomeado `BPMNPlane_CA_DS_Monitoramento`). O par THROW/CATCH existe corpus-inteiro por NOME ("Alerta Time Data Science", declaração única `CA_0:991`). Nenhum outro arquivo referencia o sinal. F-E10 (parte sinal) e a condição 1 do COMPARISON estão resolvidas na árvore atual.
2. **`SEM_SLOT_EQUIVALENTE` — permanece CA_1-interno, pendência do pacote broker (D-30).** Elementos citados (os 5): declaração `Error_CA_SemSlotEquivalente` ("Sem slot equivalente para reagendamento") · `C4_T_Reagendar` (host, delegate `${autoRebookingService}` = emissor declarado externo, F-05) · `C4_B_SemSlot` (boundary que captura) · `C4_GW_Motivo` (convergência pós-captura) · `C4_End_SemSlot` (desfecho). O throw é do delegate em runtime — nunca haverá emissor no XML por desenho; a pendência é de **coordenação CA_1 via broker** (D-30: "proposta de coordenação CA_1 via canal broker"), não de edição unilateral. NÃO editado por esta missão (cerca).
3. **DI: 0 planos sem id, corpus-inteiro** (8/8 planos com `id`; CA_2 tinha 2 planos sem id antes da onda 2.3 — antes/depois na §9 do CORRECOES). Corolário: a nota de conflação do COMPARISON (9×11) é hoje 100% histórica — com ids nos planos, a contagem estrita e a visual coincidem.

## §9 Lista de lacunas — os ÚNICOS candidatos legítimos a novos process-keys SP-OP-*

Caminho canônico de qualquer ponte futura (D-02, citado do ruling): nova chave no universo `KNOWN_PROCESS_KEYS` (`process_allowlist.py:21-42`, regex `:63`), espelho `effect_pep.py:174`, spec BPMN em `spec/processes/bpmn/`, deploy por `src/maezo/platform/deploy/engine_deploy.py`, cercas de regressão nos 3 chokepoints (W7-7.3: `start_process_idempotent` em `transport.py:1759`, contrato do `engine_deploy.py`, espelho do allowlist). **Nenhum destes candidatos está aprovado**; a ponte é programa à parte (opção (b) do ruling, só para lacuna provada — e este mapa é a prova).

| # | Lacuna (função sem casa) | Elementos | Candidato delimitado |
|---|---|---|---|
| 1 | **Despacho proativo omnicanal ao cliente** (geração/janela/capping/canal/fallback/FAQ/lembretes/aviso de evento clínico/atraso predito/contato ativo) | ~40 (L1) | `SP-OP-NOTIF-001` — nota: trio opt-in preferências tem absorção candidata por extensão de `SP-OP-SUPP-001` (domínio não-contato, hoje escopo vendor/DPO) |
| 2 | **Agenda/slot/no-show/reagendamento** (`schedulingAIAgent`, `capacityPolicyService`, `autoRebookingService`, `noShowRecorder`, preparo, cancelamento pelo prestador) | ~18 (L6) | `SP-OP-AGENDA-001` — inclui o contrato `SEM_SLOT_EQUIVALENTE` (§8.2) |
| 3 | **NBA/aprendizado** (ranking, cold-start, guardrails, explicação, reward, aprendizado de interação) | ~15 (L4) | `SP-OP-NBA-001` — F2 já design-mapped (`contracts.json:1892`, lead lucas, W5, `runtime_admitted:false`); habilitação condicionada ao ruling LGPD art. 11 §4º (REG-1.8/D-09) |
| 4 | **Perfil 360/enriquecimento/ingestão** (coletas abertas/uso/digitais/clínica/pesquisas, consolidação, contexto) | ~15 (L5) | `SP-OP-PERFIL-001` — gated por REG-1.10 (arts. 33/48/16) e REG-1.12 (base do fluxo clínico→cliente) |
| 5 | **Resolução de identidade** (diagnóstico de perfil, unificação) | ~6 (L2) | absorção em `SP-OP-LGPD-DSR-001` (preferível — sem chave nova) ou `SP-OP-IDENT-001` |
| 6 | **Desfecho/pesquisa de satisfação** (`careOutcomeRecorder`, roteamento de satisfação) | ~5 (L8) | `SP-OP-DESFECHO-001` — ou absorção por `SP-OP-CONTAS-001`; LGPD art. 11 §4º (COUNSEL-4) é pré-condição |
| 7 | **Ciclo de vida de modelo ML** (retreino/promoção/registro; `DataScienceTrainingJob`) | ~5 (L9) | **sem candidato SP-OP** — não é jornada de operadora; plataforma de ML é programa à parte (registrado para não virar forquilha) |
| — | Oferta/matcher/capacidade comercial (C1) | ~7 (L3C) | **não é candidato a processo novo** — a casa é a autoridade comercial congelada (C6-4, D-15); ponte = wiring sobre `commercial.py` |

## §10 Proveniência e limites

- Contagem e pareamento: gerados do XML em main @ `5a8ba09e` (shas por arquivo na §1); snapshot dos documentos de auditoria gitignored consumido com cp+sha256 (`/tmp/w2-corp27-snapshot-211616`; FINDINGS `ba2ed41e…`, COMPARISON `b788149e…`, INVENTORY-A-V4 `428dac64…`, TRACEABILITY `87694ddf…`).
- "CASA" = capacidade existente que exerce a função, NÃO admissão runtime do desenho v4 (o corpus CA continua `runtime_admitted: false`; F1/F2 em `contracts.json:1814,1892`). A maturidade por elemento permanece `none` no sentido da auditoria; o que este mapa estabelece é a CASA FUNCIONAL de cada vocabulário, conforme D-02 ("cada elemento ganha casa real" sem forquilha).
- Nomes de empresa/pessoa: zero nos `.bpmn` (heurística re-executada pela auditoria: 0 hits em 500 valores distintos); este documento cita apenas caminhos de código e chaves SP-OP.
