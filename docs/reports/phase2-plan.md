# Phase 2 Plan — "A operadora decide pelo humano onde nega" (Ciclo de Receita + Calendário Regulatório)

**Status:** PLAN (replanning lead, Fable tier) · supersedes nothing — extends Phase 1 (`8241335`) · **destino:** `docs/reports/phase2-plan.md`
**Escopo:** 6 processos SP-OP (CONTAS, RECURSO, NIP, ANS-SUBMIT, CANCEL, REEMBOLSO) + 3 agentes (Marina, Gustavo, Lucas) + provisionamento de tenant ≤1 dia + federação de Agent Definition (ADR-0004) + port do `.archive` (glosa/CNAB/relatórios regulatórios).
**Constraints vinculantes:** ADR-0001..0016. Não-negociáveis herdadas: estrutura no-denial §4-bis-F; validate-artifacts §4-bis-A; quádrupla §5-bis; `_hard_frozen.yaml` (clinical_decision / authorization_denial / fraud_accusation / contract_termination = L0 hard, CI-enforced); ADR-0011 greenfield (o reference é só ideias; copiar+reautorar+re-testar, nunca importar); ADR-0012 (DMN decide, LLM raciocina); ADR-0006 (duas zonas); ADR-0016 (allowlist de process_key é PR-gated em código).

---

## 1. Objetivo e Definition of Done

**Objetivo:** materializar o ciclo de receita (contas/glosa/recurso/reembolso), o canal regulatório ANS (envios periódicos + NIP), o encerramento contratual (cancelamento) e três novos agentes — **com a mesma invariante estrutural de Phase 1: nenhum caminho automatizado produz negativa/efeito adverso; toda negativa nasce numa User Task humana.** Em paralelo, tornar o provisionamento de um novo tenant uma operação de ≤1 dia e construir o motor de merge L0–L3 que ADR-0004 declara mas não existe.

**DoD (espelha phase0-report; evidência = CI, exceto o que for AWS-blocked):**

| # | Critério | Evidência exigida |
|---|---|---|
| D1 | 6 quádruplas SP-OP completas (bpmn+contrato+dmn+test-spec) passam `make validate-artifacts` | gate verde: regex de nome, `camunda:candidateGroups` em todo userTask, topics em `topic_registry.yaml` no padrão `{dominio}.{contexto}.{acao}`, ordenação XSD, `camunda:historyTimeToLive` namespaced em process e em cada `<decision>`, typeRef allowlist (sem `"number"`) |
| D2 | **Invariante no-denial verde** para CONTAS, RECURSO, NIP, CANCEL, REEMBOLSO | teste de integração consulta `history/activity-instance` do engine: todo end-event adverso co-ocorre com User Task humana concluída; varredura de TODAS as combinações de input das DMNs não produz nenhuma negativa |
| D3 | Worker-guards `ERR_*_NOT_HUMAN` ativos | testes unitários: o worker do efeito adverso (`send_glosa_notice`/`register_glosa_accept`/`register_desistencia`/`send_nip_negativa`/`send_reembolso_denial`/`send_cancellation_notice`) recusa sem variável de decisão setada por humano; carrega `*_id` do aprovador na cadeia de auditoria (ADR-0007) |
| D4 | 3 agentes (Marina, Gustavo, Lucas) com agent.yaml válido + grafo + golden eval | `validate-artifacts` aceita agent.yaml; `false_denial_rate == 0` onde aplicável; evals verdes |
| D5 | Delegações Helena→Gustavo (`nip.instruct`), CONTAS→Marina (`glosa.recurso`), Lucas→Marina | testes A2A: dispatcher aceita só os `accepted_task_types` declarados; anti-loop/max-hops (ADR-0015) |
| D6 | `KNOWN_PROCESS_KEYS` ampliado com os 6 keys via PR humano (ADR-0016) | diff em `process_allowlist.py` revisado; tenant opt-in via `process_allowlist.yaml` |
| D7 | Port: CNAB parser + conciliação + relatórios regulatórios reautorados async, re-testados | suíte verde contra fixtures próprios; nenhum import do reference |
| D8 | Provisionamento ≤1 dia: `provision-tenant <slug>` sobe namespace + per-agent runtimes + Agent Definitions + tópicos Kafka + config de autonomia/allowlist | Helm template render + dry-run; runbook |
| D9 | Motor de merge L0–L3 (ADR-0004) com hash da versão efetiva (ADR-0007) | testes do overlay engine; harness carrega definição mergeada e registra hash |
| D10 | Observabilidade Phase 2: métricas de submissão periódica, countdown de prazo NIP, job de calendário | catálogo `runtime/metrics.py` estendido, label-bounded |
| **AWS-blocked** | staging apply, conectividade ANS/banco real, LLM PHI-zone BR | rastreado em issue #16 — **não bloqueia D1–D7, D9, D10** |

---

## 2. Estrutura de Waves

Princípios de sequenciamento: **§5-bis** (autoria BPMN-first dentro de cada quádrupla); **§4-bis-F** (a *contract sheet* é o ponto de sincronização — assim que o contrato de um processo existe, dependentes podem começar contra ele, mesmo antes do BPMN ficar pronto); **§6** (só sequencia em dependência real de artefato; o resto paraleliza). Cada processo clona o esqueleto provado `SP-OP-AUTH-001` (no-denial de cinco partes), então os 6 são estruturalmente paralelos — a ordenação abaixo é por **dependência de domínio** e por **maximizar reuso do primeiro artefato concluído**.

### Wave 0 — Fundações compartilhadas (desbloqueia tudo; curta, alta prioridade)
Rationale: três artefatos são pré-requisito de várias waves; fazê-los primeiro evita retrabalho e conflito de merge.
- **W0.1 — Allowlist PR (ADR-0016):** editar `KNOWN_PROCESS_KEYS` com os 6 keys (`SP-OP-CONTAS-001`, `-RECURSO-001`, `-NIP-001`, `-ANS-SUBMIT-001`, `-CANCEL-001`, `-REEMBOLSO-001`). Regex já aceita. **Gate humano** (CODEOWNERS).
- **W0.2 — Registro de tópicos:** adicionar todos os tópicos Kafka/external-task/message das 6 quádruplas em `config/topic_registry.yaml` (o validator hard-falha em tópico não registrado). Bloqueia BPMN de todas as waves.
- **W0.3 — Port base (puro, dependency-light):** CNAB 240/400 parser (consumidor de RECURSO/REEMBOLSO/Lucas) + enums de domínio glosa (GlosaType, GlosaReasonCode) como `Literal`s. Pode rodar 100% paralelo ao resto.
- **W0.4 — Motor de merge L0–L3 (ADR-0004) — esqueleto:** o overlay engine é pré-requisito do provisionamento ≤1 dia e do registro dos 3 novos agentes em múltiplas camadas; começa cedo, em paralelo, time de plataforma.

### Wave A — Ciclo de receita glosa (o coração negativa-like) + agente Marina
Rationale: CONTAS→RECURSO é a única **dependência dura de ordering** entre processos (RECURSO consome uma glosa confirmada). RECURSO é o candidato mais forte a clonar o esqueleto AUTH-001 no-denial. Marina é o agente que ambos convocam.
- **WA.1 — SP-OP-CONTAS-001** quádrupla (BPMN-first). Produz handoff `operadora.contas.start_recurso`.
- **WA.2 — SP-OP-RECURSO-001** quádrupla (o negativa-like canônico do ciclo de receita). **Começa contra o contract sheet de CONTAS** (§4-bis-F) assim que WA.1 publica o contrato — não espera o BPMN de CONTAS terminar.
- **WA.3 — Agente Marina** (PHI zone): convocada por ambos via `operadora.contas.prepare_triage_dossier` e `operadora.recurso.analyze_request`. Começa contra os contratos de WA.1/WA.2.
- **WA.4 — Port glosa workers** (identify/classify/analyze/impact/escalate) reautorados async → DMNs + service tasks de CONTAS/RECURSO.

### Wave B — Canal regulatório ANS + agente Gustavo
Rationale: NIP e ANS-SUBMIT compartilham o agente Gustavo e o port de relatórios regulatórios; independem do ciclo glosa (paralelizam com Wave A). NIP é negativa-like na branch "mantém negativa"; ANS-SUBMIT **não** é negativa-like.
- **WB.1 — SP-OP-NIP-001** quádrupla (negativa-like na branch manter-negativa; clona o gate humano de AUTH). Prazos ANS **HARD** — os timers mais críticos da Phase 2.
- **WB.2 — SP-OP-ANS-SUBMIT-001** quádrupla (calendar-driven, TimerStartEvent; HITL pré-filing mas sem padrão no-denial).
- **WB.3 — Agente Gustavo** (general zone): owner do calendário regulatório + instrução de NIP; alvo de delegação `nip.instruct` da Helena.
- **WB.4 — Port relatórios regulatórios** (`generate_regulatory_reports_worker` — SIP/RN124, RN209, RN388, RN424) + `ans_client` → `mcp-ans.*` e `mcp-regdata.assemble`.

### Wave C — Encerramento + reembolso (segundo lote negativa-like)
Rationale: CANCEL (contract_termination L0 hard) e REEMBOLSO (authorization_denial-class L0 hard) são ambos negativa-like puros; clonam o esqueleto já provado em Wave A, então vêm depois para reusar padrões/utilitários consolidados. Independentes entre si → paralelos.
- **WC.1 — SP-OP-CANCEL-001** quádrupla (cancelamento contratual; terminal humano-gated).
- **WC.2 — SP-OP-REEMBOLSO-001** quádrupla (reembolso ao beneficiário; negar reembolso = negativa-like).
- **WC.3 — Agente Lucas** (general zone): atendimento/cobrança ao beneficiário; consumidor vivo do port CNAB/conciliação; **escala** inadimplência/cancelamento (nunca decide). *Charter pendente de sign-off — ver §9.*

### Wave D — Plataforma: provisionamento ≤1 dia + observabilidade
Rationale: depende de W0.4 (merge engine) estar maduro e de os 3 agentes existirem para serem entregues por tenant. Roda em paralelo às Waves A–C no time de plataforma; fecha por último porque integra tudo.
- **WD.1 — Helm one-command tenant factory** (per-agent Deployment templating, entrega de Agent Definition, criação de tópicos Kafka, labels PHI-zone→NetworkPolicy, IRSA por agente).
- **WD.2 — `provision-tenant <slug>`** a partir de um único tenant config file.
- **WD.3 — Deltas de observabilidade** (submissão periódica, countdown NIP, métrica de job de calendário).

### Wave E — Integração e relatório de fase
- **WE.1 — Suíte de integração cross-process** (CONTAS→RECURSO; NIP→ANS-SUBMIT handoff; invariantes no-denial sweeping todas as DMNs).
- **WE.2 — `docs/reports/phase2-report.md`** + atualização de `HANDOFF.yaml`/`review-queue.md`/memory.

---

## 3. As 6 quádruplas SP-OP

Lembrete de shape engine-deployável (**§4-bis-A**, aplicar a TODAS): nome `SP-OP-{AREA}-{NNN}_{Titulo}.bpmn`; `bpmn:definitions` com `xmlns:camunda`; ≥1 startEvent + ≥1 endEvent; **todo userTask com `camunda:candidateGroups`**; todo serviceTask externo com `camunda:type="external" camunda:topic=...` no padrão `{dominio}.{contexto}.{acao}` e registrado; ordenação XSD `documentation < extensionElements < incoming < outgoing < eventDefinition` em todo flow element; `businessRuleTask` com `camunda:decisionRef`+`mapDecisionResult="singleResult"`+`resultVariable`; `camunda:historyTimeToLive="P1825D"` no `<bpmn:process>` e `P###D` namespaced em cada `<decision>`. DMN: typeRef ∈ {string, boolean, integer, long, double, date} — **`"number"` é inválido; dinheiro BRL → `double` ou inteiro-centavos; dias/SLA → string ISO**; todo decisionTable com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador.

Padrão no-denial de cinco partes (**§4-bis-F**, aplicar aos 5 processos negativa-like): (1) enums de rota sem variante "negar" em branch automatizado; (2) nenhuma DMN com coluna de saída de negativa; (3) ambíguo → User Task (catch-all row); (4) worker-guard `ERR_*_NOT_HUMAN` no efeito adverso; (5) teste de invariante consultando history do engine.

---

### 3.1 SP-OP-CONTAS-001 — Processamento de contas / glosa
**NEGATIVA-LIKE: SIM** (glosa = operadora negando/reduzindo pagamento de linha de conta ao prestador; `authorization_denial`-adjacent, L0 hard — embora o *envelope* clerical seja `standard_glosa_processing` L2).
**Inverte** o reference `glosa_management.bpmn` (que auto-identifica/classifica/aplica glosa e tem auto-aceite no timeout): em Maezo a DMN só *sinaliza candidata/roteia*, nunca *confirma* glosa substantiva.

- **Process id/key:** `SP-OP-CONTAS-001` · `targetNamespace=http://maezo.health/operadora/contas`
- **Business key:** `CONTAS-{tenant_id}-{numero_lote_tiss}` (ou `-{numero_guia_tiss}-{numero_conta}` se adjudicação por guia). Uma instância por lote/conta; reenvio retorna ativa.
- **Start:** `Start_RespostaOperadoraRecebida` (TISS ClaimResponse / demonstrativo de análise — gatilho via Tasy CDC do amh-data-platform; **consumir, nunca duplicar** — MEMORY/ADR-0013).
- **Terminais humano-gated:** `End_GlosaAceitaHumano` (aceitar glosa contra o prestador) — alcançável **só** via `UT_AnalistaContas`. `End_SemGlosa`/`End_EncaminhadaRecurso`/`End_Reenviada` são neutros.
- **HITL + grupos:** `UT_AnalistaContas` (`analista-contas-medicas`) — única origem de `decisao_contas ∈ {RECORRER, ACEITAR_GLOSA, REENVIAR}`; `UT_CoordenacaoContasAssume` (`coordenacao-faturamento`) no estouro de SLA. Branch de indício de fraude → User Task `auditoria-contas` (nunca auto-flag; `fraud_accusation` L0 hard; alimenta SP-OP-FRAUDE-001 em Phase 3).
- **Worker-guard:** `ERR_GLOSA_ACCEPT_NOT_HUMAN` — `operadora.contas.register_glosa_accept` recusa sem `decisao_contas==ACEITAR_GLOSA` setado por humano; carrega `analista_id`.
- **DMN:** `glosa_reason_normalization` (reason_code TISS → categoria normalizada); `glosa_classification` (reason_code, denial_ratio:double → glosa_type, glosa_extent); `glosa_triage` (in: tipo_item, item_conforme_tabela:boolean, divergencia_valor:boolean, documentacao_anexa:boolean → out: `roteamento ∈ {SEM_GLOSA, RECORRER, ANALISE_HUMANA}`, motivo — **sem saída ACEITAR/confirmar**; catch-all → ANALISE_HUMANA); `contas_sla` (tipo_lote, valor_lote → sla_analise/sla_alerta ISO string).
- **Timers SLA (todos DRAFT/verify regulatório+contratos):** `BT_AlertaSlaContas` não-interruptivo (~60-70%, `${sla.sla_alerta}`) → `operadora.contas.notify_sla_risk`; `BT_SlaTriagem` interruptivo (`${sla.sla_analise}`, típico 30 dias do recebimento do lote TISS) → publica `contas.sla_breached` → `UT_CoordenacaoContasAssume`.
- **Eventos de domínio:** `agents.events.contas.received` (start), `agents.events.contas.glosa_identified` (payload total_glosado_brl, glosa_count), `agents.events.contas.sla_breached`, `agents.events.contas.completed` (`desfecho ∈ {sem_glosa, encaminhada_recurso, glosa_aceita_humano, reenviada}`).
- **Teste de invariante:** `test_nenhum_caminho_automatizado_aceita_glosa` (sweeping `glosa_triage`); `test_aceitar_glosa_exige_campos` (justificativa + código bloqueia sem); `test_glosa_tecnica_roteia_para_humano_nao_aceita`.
- **Deps:** AUTH-001 → CONTAS (status de autorização alimenta adjudicação); **CONTAS → RECURSO (hard, handoff)**; CONTAS → FRAUDE (Phase 3).

---

### 3.2 SP-OP-RECURSO-001 — Recurso de glosa (o negativa-like canônico do ciclo de receita)
**NEGATIVA-LIKE: SIM** (desistir/não-recorrer = aceitar a glosa contra o prestador = `authorization_denial`-class L0 hard). **Clona diretamente o esqueleto AUTH-001.**

- **Process id/key:** `SP-OP-RECURSO-001` · `targetNamespace=http://maezo.health/operadora/recurso`
- **Business key:** `RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}`. Um recurso por glosa por guia; reenvio idempotente.
- **Start:** `Start_RecursoSolicitado` (de CONTAS handoff ou delegação A2A a Marina).
- **Terminal humano-gated:** `End_RecursoNaoInterposto` / `End_GlosaMantida` (desistência) — só via `UT_AnaliseRecursoAnalista` com `decisao_recurso==NAO_RECORRER`. (Inadmissibilidade por prazo procedural duro é borderline → humano por default; auto-route de prazo puro só com jurídico sign-off, DRAFT.)
- **HITL + grupos:** `UT_AnaliseRecursoAnalista` (`analista-recurso-glosa`) → `decisao_recurso ∈ {RECORRER, NAO_RECORRER, SOLICITAR_INFO, ESCALAR_AUDITOR}`. **`NAO_RECORRER` exige `justificativa_desistencia` + `valor_glosa_aceito` + `referencia_contratual`** (espelha NEGAR de AUTH exigindo justificativa_clinica/cid10/fundamentacao_dut). `UT_RevisaoAuditorMedico` (`medico-auditor`) quando glosa técnica/clínica (ESCALAR_AUDITOR — auditor decide o mérito). `UT_CoordenacaoRecursoAssume` (`coordenacao-recurso`) no breach.
- **Worker-guard:** `ERR_DESISTENCIA_NOT_HUMAN` — `operadora.recurso.register_desistencia` recusa sem decisão humana; carrega `analista_id`.
- **DMN:** `recurso_admissibility` (dentro_prazo_recurso:boolean, documentacao_recurso_completa:boolean, glosa_existe:boolean → `roteamento ∈ {SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}` — **sem NEGAR/DESISTIR**; catch-all → ANALISE_HUMANA); `recurso_eligibility` (→ {RECORRIVEL, ANALISE_HUMANA}); `recurso_sla` (→ sla_analise/sla_alerta/prazo_regulatorio ISO string).
- **Timers (DRAFT/verify):** `BT_AlertaSlaRecurso` não-interruptivo; `BT_SlaAnaliseRecurso` interruptivo → `recurso.sla_breached` → coordenação (**substitui** o auto-aprovar-em-48h do reference — INVERTIDO); `ICE_AguardarResposta` timer intermediário (loop TISS, ref `P5D`) com `GW_RecursoResolvido` e loopCounter limitado (ref `<6`); `BT_PrazoMaxRecurso` (ref `P30D`, ANS RN 424/2017 — **DRAFT/verify**) → `operadora.recurso.escalate_ans_timeout` → `UT_EscalonamentoPrazo`.
- **Eventos:** `agents.events.recurso.received`, `.sla_breached`, `.completed` (`desfecho ∈ {deferido, indeferido, parcialmente_deferido, inadmissivel, nao_interposto_humano}`).
- **Teste de invariante:** `test_nenhum_caminho_automatizado_produz_desistencia`; `test_nao_recorrer_exige_campos_obrigatorios`; `test_inadmissibilidade_aparente_roteia_para_humano`.
- **Deps:** **CONTAS → RECURSO (hard)**; RECURSO outcome → CONTAS (re-pagamento). Marina convocada via `operadora.recurso.analyze_request`.

---

### 3.3 SP-OP-NIP-001 — Resposta a NIP (ANS) — negativa-like na branch "mantém negativa"
**NEGATIVA-LIKE: PARCIAL/SIM** — o envelope (acusar, montar, submeter) é L1-clerical; a resposta que **mantém a negativa** original embute `authorization_denial`-class → gate humano. `nip_response: L1`. **Prazos ANS HARD — os timers mais schedule-críticos da Phase 2** (sanção ANS por descumprimento).

- **Process id/key:** `SP-OP-NIP-001` · `targetNamespace=http://maezo.health/regulatorio/nip`
- **Business key:** `NIP-{tenant_id}-{numero_nip_ans}` (ou `-{protocolo_ans}`). Uma instância por protocolo NIP.
- **Start:** `Start_NipRecebido` MessageStartEvent (frequentemente via A2A `nip.instruct` da Helena/intake para Gustavo).
- **Terminal humano-gated:** `End_NipNegativaMantida` — só via `UT_RevisaoJuridicaNip`/`UT_ElaborarRespostaNip` com decisão humana. Resposta favorável (concede) é permitida, mas o texto legal é **sempre** revisado por humano (jurídico/regulatório autora+aprova; o agente só instrui).
- **HITL + grupos:** `UT_ElaborarRespostaNip` (`regulatorio-ans` / `nucleo-ans`); `UT_RevisaoJuridicaNip` (`juridico-regulatorio`) para manter-negativa/casos complexos; `UT_CoordenacaoNip` no breach. Campos obrigatórios ao manter: `fundamentacao_regulatoria` + `referencia_negativa_original`.
- **Worker-guard:** `ERR_NIP_NEGATIVA_NOT_HUMAN` — `operadora.nip.submit_response` recusa enviar resposta que mantém negativa sem decisão humana; carrega `revisor_id`. **Nenhum caminho DMN/agente produz o texto da resposta como final.**
- **DMN:** `nip_classification` (classificacao_nip ∈ {assistencial, nao_assistencial}, tema → `classificacao`, `prazo_dias`, `grupo_revisor`); `nip_routing` (→ grupo humano; catch-all → juridico-regulatorio + SLA mais curto, conservador). Sem saída "deny".
- **Timers (HARD — flag para regulatório):** `BT_PrazoNipAssistencial ~P5D úteis` / `não-assistencial ~P10D úteis` (**DRAFT/verify**; converter dias úteis→ISO conservadoramente no worker); `BT_AlertaNip` não-interruptivo (~50-70%) → alerta; interruptivo → coordenação/jurídico assume.
- **Eventos:** `agents.events.nip.received`, `.classified`, `.deadline_risk`, `.completed` (`desfecho ∈ {resolvida_favoravel, negativa_mantida, nao_assistencial_respondida}`), `.breached` (incrementa métrica de compliance). Handoff `nip → ANS-SUBMIT` para o filing formal.
- **Teste de invariante:** `test_manter_negativa_exige_user_task`; `test_resposta_final_nunca_gerada_por_dmn`; `test_prazo_nip_dispara_alerta_e_interrupcao`.
- **Deps:** AUTH-001 → NIP (NIP nasce de negativa de pré-autorização contestada); **NIP → ANS-SUBMIT-001** (filing formal); possivelmente RECURSO/REEMBOLSO → NIP.

---

### 3.4 SP-OP-ANS-SUBMIT-001 — Envios periódicos ANS (calendar-driven)
**NEGATIVA-LIKE: NÃO** (operadora→regulador; nenhuma decisão adversa contra beneficiário/prestador). **Não recebe o padrão no-denial**, mas mantém o resto da disciplina da quádrupla: submissão idempotente + HITL antes do filing legalmente vinculante (humano assina — não-repúdio ADR-0007). `ans_official_submission: L1`.

- **Process id/key:** `SP-OP-ANS-SUBMIT-001` · `targetNamespace=http://maezo.health/regulatorio/anssubmit`
- **Business key:** `ANSSUB-{tenant_id}-{report_type}-{competencia}` (idempotente por período de competência).
- **Start:** `Start_CalendarioRegulatorio` **TimerStartEvent** (cron por tipo de envio: SIP/RN124, RN209, RN388, RN424/TISS-monitoramento, DIOPS trimestral).
- **Fluxo:** `assemble_dataset` (`regulatorio.anssubmit.assemble`, relatório LGPD-anonimizado) → `validate_schema` (`regulatorio.anssubmit.validate`, XSD/TISS) → `UT_RevisarEnvio` (**User Task L1 — humano `regulatorio`/`juridico` aprova o envio oficial**) → `submit_to_ans` (`regulatorio.anssubmit.submit`) → `track_protocol` + correlate ACK/NACK → subprocess de retry/backoff (porta de `billing_submission.bpmn`) → timer não-interruptivo de deadline-risk → `notify_regulatorio`.
- **HITL + grupos:** `UT_RevisarEnvio` (`regulatorio` / `juridico-regulatorio`). **Nunca auto-submete em dados faltantes** → roteia a humano.
- **DMN:** `ans_calendar` (report_type, competencia → due_date, sla_alerta, fonte_RN — string ISO); `ans_submission_admissibility` (dataset_complete:boolean, schema_valid:boolean → `SEGUE_ENVIO | PENDENTE | REVISAO_HUMANA` — **sem saída "reject" por design**; catch-all → REVISAO_HUMANA).
- **Timers:** deadline por calendário ANS (`ans_calendar`, **DRAFT/verify** todas as datas/fontes RN); alerta não-interruptivo; retry/backoff no subprocess.
- **Eventos:** `agents.events.anssubmit.generated`, `.submitted`, `.acked`, `.deadline_risk`, `.failed`. Saídas: {protocolo_ans, status ∈ {enviado, ack, nack, retransmitido}, data_envio}.
- **Test-spec:** golden por tipo de relatório incl. missing-data→humano, schema-fail→humano, deadline-risk dispara, re-run idempotente para mesma competência.
- **Deps:** NIP-001 → ANS-SUBMIT (filing da resposta NIP); consome o port de relatórios regulatórios.

---

### 3.5 SP-OP-CANCEL-001 — Cancelamento / rescisão contratual
**NEGATIVA-LIKE: SIM** (`contract_termination` L0 hard — terminal de cancelamento pela operadora). Clona o esqueleto AUTH-001.

- **Process id/key:** `SP-OP-CANCEL-001` · `targetNamespace=http://maezo.health/operadora/cancelamento`
- **Business key:** `CANCEL-{tenant_id}-{numero_contrato}` (ou `-{matricula_beneficiario}`). Uma instância por contrato/beneficiário.
- **Start:** `Start_SolicitacaoCancelamento` (pedido do beneficiário, inadimplência referida por Lucas, ou rescisão pela operadora).
- **Terminal humano-gated:** `End_ContratoRescindido` (rescisão pela operadora) — só via `UT_AnaliseRescisao` com decisão humana. Cancelamento a pedido do beneficiário (direito do titular) segue, mas a rescisão *unilateral pela operadora* é a decisão adversa gated. **RN 593 (regras de cancelamento/suspensão por inadimplência) — DRAFT/verify regulatório/jurídico.**
- **HITL + grupos:** `UT_AnaliseRescisao` (`juridico-contratos` / `gestao-contratos`) → `decisao_cancelamento ∈ {RESCINDIR, MANTER, SUSPENDER, SOLICITAR_INFO}`; `UT_CoordenacaoCancelamento` (`coordenacao-contratos`) no breach. Campos obrigatórios ao RESCINDIR: `fundamentacao_contratual` + `referencia_regulatoria` (RN 593) + comprovação de notificação prévia.
- **Worker-guard:** `ERR_CANCELLATION_NOT_HUMAN` — `operadora.cancel.send_cancellation_notice` recusa sem `decisao_cancelamento==RESCINDIR` humano; carrega `responsavel_id`.
- **DMN:** `cancel_admissibility` (tipo_solicitacao, dentro_prazo:boolean, notificacao_previa_feita:boolean → `roteamento ∈ {SEGUE_ANALISE, PENDENTE_NOTIFICACAO, ANALISE_HUMANA}` — **sem RESCINDIR automático**; catch-all → ANALISE_HUMANA); `cancel_sla` (→ prazos ISO).
- **Timers (DRAFT/verify RN 593):** prazo de notificação prévia (event-gateway); alerta não-interruptivo; SLA interruptivo → coordenação.
- **Eventos:** `agents.events.cancel.received`, `.sla_breached`, `.completed` (`desfecho ∈ {rescindido_operadora, cancelado_beneficiario, mantido, suspenso}`).
- **Teste de invariante:** `test_rescisao_operadora_exige_user_task`; `test_rescindir_exige_fundamentacao_e_notificacao`; `test_inadimplencia_aparente_roteia_para_humano`.
- **Deps:** Lucas → CANCEL (escala inadimplência, nunca rescinde); CANCEL → SP-OP-INADIMPLENCIA-001 (Phase 3).

---

### 3.6 SP-OP-REEMBOLSO-001 — Reembolso ao beneficiário
**NEGATIVA-LIKE: SIM** (negar reembolso ao beneficiário = `authorization_denial`-class L0 hard — nega pagamento por motivo de cobertura). Clona o esqueleto AUTH-001.

- **Process id/key:** `SP-OP-REEMBOLSO-001` · `targetNamespace=http://maezo.health/operadora/reembolso`
- **Business key:** `REEMB-{tenant_id}-{protocolo_reembolso}` (ou `-{numero_guia_tiss}`). Uma instância por solicitação.
- **Start:** `Start_SolicitacaoReembolso` (beneficiário solicita reembolso de despesa fora da rede / livre escolha).
- **Terminal humano-gated:** `End_ReembolsoNegado` — só via `UT_AnaliseReembolso` com decisão humana. Reembolso aprovado dentro de tabela/teto pode auto-aprovar L2 (análogo a `auth_auto_approval`), mas **negar nunca**.
- **HITL + grupos:** `UT_AnaliseReembolso` (`analise-reembolso` / `medico-auditor` se mérito clínico) → `decisao_reembolso ∈ {APROVAR, NEGAR, APROVAR_PARCIAL, SOLICITAR_INFO}`; `UT_CoordenacaoReembolso` no breach. Campos obrigatórios ao NEGAR: `justificativa` + `fundamentacao_contratual` + (se clínico) `cid10`/`parecer_auditor`.
- **Worker-guard:** `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` — `operadora.reembolso.send_reembolso_denial` recusa sem `decisao_reembolso==NEGAR` humano; carrega `analista_id`.
- **DMN:** `reembolso_admissibility` (cobertura_prevista:boolean, documentacao_completa:boolean, dentro_prazo:boolean → `roteamento ∈ {SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}` — **sem NEGAR**); `reembolso_auto_approval` (dentro_tabela:boolean, dentro_teto:boolean → `AUTO_APROVAR | ANALISE_HUMANA`; catch-all → ANALISE_HUMANA); `reembolso_sla`.
- **Timers (DRAFT/verify):** prazo de reembolso (RN — typically 30 dias; confirmar); alerta não-interruptivo; SLA interruptivo → coordenação.
- **Eventos:** `agents.events.reembolso.received`, `.sla_breached`, `.completed` (`desfecho ∈ {aprovado_automatico, aprovado_analista, negado_analista, aprovado_parcial}`).
- **Teste de invariante:** `test_nenhum_caminho_automatizado_nega_reembolso`; `test_negar_reembolso_exige_campos`; `test_inelegibilidade_roteia_para_humano`.
- **Deps:** CNAB/conciliação port (pagamento do reembolso aprovado); REEMBOLSO → NIP possível (beneficiário reclama na ANS).

---

## 4. Agentes a autorar

Shape comum (espelha `_template/agent.yaml` + Rafael): `id`, `name`, `role`, `reports_to` (humano), `security_zone`, `graph: graph.py:build`, `prompt_versions`, `model` (task_default fast / reasoning frontier), `tools` (allowlist — PEP nega o resto), `autonomy_policy`, `autonomy_actions` (só ações REAIS da matriz; **NENHUMA auto-deny**), `kpis`, `escalation`, `memory`, `a2a`. **Princípio Rafael:** DMN decide roteamento, LLM só raciocina/monta dossiê, todo caminho não-auto → User Task, fail-safe = humano em erro/ambiguidade de DMN.

### 4.1 Marina — Analista de Contas/Glosa (PHI zone)
- **id:** `marina` · **role:** "Analista de Contas Médicas e Recurso de Glosa — instrui SP-OP-CONTAS-001/RECURSO-001 e monta o dossiê do analista/auditor" · **reports_to:** `coordenacao-faturamento@<tenant>` · **security_zone: phi** (provider-side TISS, PHI integral — confirmado em values.yaml: Marina é PHI zone).
- **tools:** `mcp-fhir.read_patient_summary`, `mcp-dmn.evaluate` (glosa_* / recurso_*), `mcp-cibseven.start_process` (só CONTAS + RECURSO), `mcp-cibseven.get_process_status`, `mcp-cibseven.correlate_process_message`, `mcp-memory.read_write`. (CNAB/conciliação como tool worker-backed read se precisar de status de pagamento.)
- **autonomy_actions:** `read_phi_data` (L3), `query_decision_engine` (L3), `start_compliance_process` (L2), `query_process_status` (L3), `correlate_process_message` (L2), `read_write_memory` (L3). **Nota L0 hard (intencional):** Marina NÃO exerce `authorization_denial` (a glosa/desistência é só do analista/auditor humano na User Task) nem `standard_glosa_processing` autonomamente para confirmar glosa — ela instrui, humano decide.
- **a2a:** capabilities `glosa_analysis`, `recurso_dossier`; skills `tiss_glosa_analysis`, `recurso_glosa_dossier`; `accepted_task_types: [glosa.analyze, recurso.analyze]`; `queue_ref: agents.tasks.marina`. **Alvo de delegação** de CONTAS (`operadora.contas.prepare_triage_dossier`) e RECURSO (`operadora.recurso.analyze_request`).
- **kpis:** `dossier_completeness >0.95`; `recurso_recovery_rate track`; `human_routing_precision track`; `eval_score >0.9`; **`false_denial_rate ==0`** (invariante L0: zero glosas/desistências por automação).
- **AGJ (journeys):** (J1) ClaimResponse → identify→classify→triagem dossiê → analista decide RECORRER/ACEITAR; (J2) RECURSO delegado → monta dossiê → analista decide RECORRER/NAO_RECORRER → submete (só após aprovação).

### 4.2 Gustavo — Operador de Calendário Regulatório ANS (general zone)
- **id:** `gustavo` · **role:** "Operador de Calendário Regulatório ANS — conduz SP-OP-ANS-SUBMIT-001 e instrui SP-OP-NIP-001" · **reports_to:** `regulatorio@<tenant>` · **security_zone: general** (dados agregados/regulatórios, PHI pseudonimizado — ADR-0006). Owner natural da matriz de autonomia (ADR-0008).
- **tools:** `mcp-dmn.evaluate` (ans_calendar/admissibility/nip_*), `mcp-cibseven.start_process` (só ANS-SUBMIT + NIP), `mcp-cibseven.get_process_status`, `mcp-cibseven.correlate_process_message`, `mcp-memory.read_write`, **NEW** `mcp-ans.submit`/`mcp-ans.get_protocol` (port de `ans_client`), **NEW** `mcp-regdata.assemble` (port do regulatory-reports), `mcp-fhir.read_patient_summary` (pseudonimizado, só NIP).
- **autonomy_actions:** `query_decision_engine` (L3), `start_compliance_process` (L2), `query_process_status` (L3), `correlate_process_message` (L2), `read_write_memory` (L3), **`ans_official_submission` (L1 — require_human)**, **`nip_response` (L1 — require_human)**, `read_phi_data` (L3, só NIP). **Gustavo nunca transmite à ANS nem arquiva NIP autonomamente** — o PEP retorna `require_human`; humano do grupo `regulatorio`/`juridico` confirma (igual ao "agente instrui, humano decide" de Rafael).
- **a2a:** capabilities `regulatory_calendar_operation`, `nip_response_instruction`; skills `ans_periodic_submission`, `nip_dossier`; `accepted_task_types: [nip.instruct]` (Helena/intake delega NIP inbound a Gustavo); `queue_ref: agents.tasks.gustavo`.
- **kpis:** `submission_on_time_rate target ==1.0` (zero prazos ANS perdidos); `nip_deadline_compliance ==1.0`; `dossier_completeness >0.95`; `eval_score >0.9`; **`false_denial_rate ==0`** (aplicável à branch manter-negativa do NIP).
- **AGJ:** (J1) cron dispara → assemble→validate→`UT_RevisarEnvio`→submit→track ACK; (J2) NIP delegado → classifica→monta dossiê→humano autora/aprova resposta→filing via ANS-SUBMIT.

### 4.3 Lucas — Atendimento/Cobrança ao Beneficiário (general zone) — **charter pendente de sign-off (§9)**
- **id:** `lucas` · **role:** "Navegador de Atendimento e Cobrança ao Beneficiário (member-billing)" · **reports_to:** `atendimento@<tenant>` · **security_zone: general** (PHI pseudonimizado, tier WhatsApp/beneficiary-facing junto de Helena/Gustavo/Fernando).
- **tools:** `mcp-whatsapp.send_message`, `mcp-dmn.evaluate` (billing/aging DMNs do port collection), `mcp-fhir.read_patient_summary`, `mcp-memory.read_write`, `mcp-cibseven.start_process` (**só ESCALATION inicialmente**; CANCEL/REEMBOLSO só por escalação), CNAB/conciliação como read service worker-backed ("este pagamento foi conciliado?").
- **autonomy_actions:** `informational_response` (L3), `reminders_nudges` (L3), `send_beneficiary_message` (L3), `send_beneficiary_template` (L2), `query_decision_engine` (L3), `read_phi_data` (L3), `read_write_memory` (L3), `start_compliance_process` (L2, só ESCALATION). **Fronteira L0 dura:** Lucas NÃO faz `contract_termination` nem `high_value_payment`; inadimplência/suspensão/cancelamento → **escala** via SP-OP-ESCALATION-001 (e via CANCEL-001 onde humano decide). Qualquer desfecho negativa-like → humano.
- **a2a:** capabilities `member_billing_navigation`; skills `boleto_2via`, `payment_confirmation`, `collection_nudge`; `accepted_task_types: []` (não é alvo de delegação em Phase 2); **origina** escalações; pode delegar a Marina disputas provider-side (`glosa.analyze`); `queue_ref: agents.tasks.lucas`.
- **kpis:** `resolution_rate track`; `escalation_precision track`; `eval_score >0.9`; **`false_denial_rate ==0`** (Lucas nunca nega nada — invariante trivial mas assertada).
- **AGJ:** (J1) dúvida de boleto/2ª-via → responde/envia; (J2) pagamento → confirma via conciliação; (J3) inadimplência detectada → **escala, nunca suspende**.

### 4.4 Delegações novas (DelegationDispatcher, ADR-0015 anti-loop/max-hops)
- **Helena → Gustavo** (`nip.instruct`): NIP inbound roteado para Gustavo. Adicionar `nip.instruct` aos handlers; Helena origina (mantém `accepted_task_types: []`).
- **CONTAS-001 service task → Marina** (`glosa.analyze` / `recurso.analyze`): via `operadora.contas.prepare_triage_dossier` e `operadora.recurso.analyze_request` (espelha AUTH→Rafael).
- **Lucas → Marina** (`glosa.analyze`): dispute provider-side originada no atendimento. Registrar eventos `agents.events.delegation.{requested,completed,rejected}` (já no registry).

---

## 5. Inventário de PORT do reference (`../maezo-reference`) — copiar+adaptar+re-testar, NUNCA importar (ADR-0011)

Reautoração obrigatória: reference é **Zeebe/Camunda 8 + FederatedDMNService**, workers sync, `WorkerResult`/`@worker(topic=)`/exceções de domínio. Maezo é **CIB Seven external-task + `mcp-dmn.evaluate` + estrutura no-denial**. Port = copiar conhecimento de domínio, reautorar mecânica async contra `ToolInvoker`→PEP, nossa `metrics.py`, nossos testes.

| Asset reference (verificado em disco) | Alvo Maezo | Ação | Cuidado |
|---|---|---|---|
| `.archive/bpmn/glosa_management.bpmn` | split → CONTAS-001 (identify→triagem) + RECURSO-001 (lifecycle) | **ADAPT+SPLIT** | inverte auto-aceite no timeout |
| `identify_glosa_worker.py` | `operadora.contas.identify_glosa` | PORT (lógica) | reason→categoria vira DMN; **TASY write DROP** (consumimos CDC, nunca escrevemos — MEMORY/ADR-0013) |
| `classify_glosa_type_worker.py` | DMN `glosa_classification` (worker só calcula denial_ratio) | PORT→DMN | — |
| `analyze_glosa_reason_worker.py` | `operadora.contas.analyze_reason` (fatos→worker; narrativa→Marina LLM) | PORT (split) | thresholds → tenant config, não hardcoded |
| `calculate_glosa_impact_worker.py` | `operadora.contas.calculate_impact` (aritmética pura) | PORT (clean) | Money/Decimal → BRL em centavos-inteiros ou double (nunca `"number"`) |
| `check_appeal_eligibility_worker.py` (**raises NotAppealable; auto-aceita**) | DMN `recurso_admissibility`/`recurso_eligibility` (→ {SEGUE/RECORRIVEL, ANALISE_HUMANA}) | **INVERT** (o crux do §5) | math de prazo fica no worker; a decisão de não-recorrer é User Task |
| `generate_appeal_documentation_worker.py` | `operadora.recurso.analyze_request` = dossiê Marina (templates como seed) | PORT (como DRAFT) | todo texto legal → DRAFT + review-queue; nunca auto-arquiva |
| `submit_appeal_worker.py` | `operadora.recurso.submit_appeal` (só após User Task de aprovação) | PORT | — |
| `track_appeal_status_worker.py` | `operadora.recurso.track_status` (dentro do timer loop) | PORT | — |
| `update_payment_worker.py` | `operadora.recurso.reconcile_payment` + `operadora.contas.register_glosa_accept` (gated) | PORT (split, gated) | — |
| `escalate_to_supervisor_worker.py` (`requiresHumanDecision=True`) | corpo do service task que alimenta `UT_*`; thresholds → DMN `glosa_escalation_tier` | PORT (melhor-fit; já decide nada) | — |
| `Task_AutoApprove` (48h boundary) | `BT_SlaAnaliseRecurso` interruptivo → coordenação | **DROP/INVERT** | sem auto-pass por timeout |
| `revenue_cycle/collection/lib/cnab_parser.py` (CNAB 240/400 FEBRABAN) | port puro p/ conciliação (RECURSO/REEMBOLSO/Lucas) | **PORT (highest-value, puro)** | dependency-light; re-testar com fixtures próprios |
| `collection/workers/*` (parse/reconcile/auto_matching/handle_over-underpayment/export_erp) + `penalty_calculator.py` | conciliação worker-backed para Lucas/REEMBOLSO | PORT (async) | sync→async; nossa metrics |
| `platform_services/.../generate_regulatory_reports_worker.py` (RN124/209/388/424) | `mcp-regdata.assemble` (Gustavo, ANS-SUBMIT) | PORT (async) | LGPD anonymization mantida |
| `shared/integrations/ans_client.py` | `mcp-ans.submit`/`mcp-ans.get_protocol` | ADAPT (submission-side) | Protocol+DTO+cache |
| `shared/dmn/federation_service.py` (base+`tenant_overrides/` merge) | **modelo** para o merge engine de Agent Definition L0–L3 | ADAPT (ideia de merge) | é o padrão provado de merge a adaptar p/ ADR-0004 |
| `.archive/bpmn/billing_submission.bpmn` (retry/backoff timer) | subprocess de retry de ANS-SUBMIT | PATTERN ref | ACK/NACK semantics |

---

## 6. Workstreams de plataforma

### 6.1 Provisionamento de tenant ≤1 dia (Helm)
**Existe:** `deploy/helm/maezo-tenant/` (namespace, single agent-runtime Deployment, gateway, webhook, fhir-sync, NetworkPolicy de duas zonas, ExternalSecret, ServiceAccount, IRSA hooks), `values-amh.yaml`, overlay autonomia `tenants-amh.yaml`, `process_allowlist.yaml`, Terraform (aurora/eks/ecr/secrets/github-oidc, envs prod-amh + staging).
**Construir agora (WD.1/WD.2):**
1. **Per-agent Deployment templating** — hoje agent-runtime é um único Deployment (uma imagem, env TENANT_ID). Templar um Deployment por agente do roster (general: Helena, Lucas, Gustavo, Fernando; phi: Rafael, Marina, ...), com labels de zona PHI ligadas à NetworkPolicy.
2. **Mecanismo de entrega de Agent Definition** (ConfigMap ou OCI) — entregar o `agent.yaml` mergeado (saída do merge engine) ao runtime.
3. **Criação de tópicos Kafka** por tenant (derivada de `topic_registry.yaml`).
4. **IRSA por agente** (least-privilege por tool allowlist).
5. **`provision-tenant <slug>`** — um único tenant config file → namespace + runtimes + definitions + tópicos + autonomia/allowlist overlay; dry-run + runbook.

### 6.2 Federação de Agent Definition L0–L3 (ADR-0004) — **maior gap de plataforma (W0.4)**
**Declarado, não construído:** `a2a/registry.py` tem `federation_layer`; `runtime/harness.py:load_agent_definition()` lê **um único** `agent.yaml`, canonicaliza e faz SHA-256. **Não há merge/overlay engine.** É pré-requisito de "provisionamento ≤1 dia" ser real.
**Construir:** overlay engine L0(core) < L1(regulatório BR) < L2(segmento) < L3(tenant), resolvendo merge na inicialização e registrando **hash da versão efetiva** (ADR-0007). Adaptar o padrão de merge provado do reference `federation_service.py` (base + `tenant_overrides/`). Validar que `_hard_frozen.yaml` não é rebaixável por nenhuma camada (CI-enforced).

### 6.3 Observabilidade/devops (WD.3)
`runtime/metrics.py` é maduro e label-bounded por `tenant`/`agent`/`process_key`/`task_name`. **Adicionar:** métrica de desfecho de submissão periódica (ANS-SUBMIT); countdown de prazo NIP (deadline-risk); métrica genérica de "job de calendário regulatório"; manter cardinalidade limitada. Dashboards AMP/AMG (ADR-0014) por processo novo.

---

## 7. Mapa de propriedade de arquivos (disjoint) + topologia de swarm + paralelização

**Regra:** um dono por path; nenhum arquivo escrito por dois workstreams. Contract sheets são o ponto de sincronização (§4-bis-F).

### 7.1 Ownership map (um dono por path)
| Path | Dono (workstream) |
|---|---|
| `config/topic_registry.yaml` | **W0.2** (único editor; todos os outros só leem) |
| `src/maezo/tools/process_allowlist.py` + `config/process_allowlist.yaml` | **W0.1** (PR humano) |
| `spec/processes/bpmn/SP-OP-CONTAS-001_*.bpmn` + `docs/processes/contracts/SP-OP-CONTAS-001.md` + `dmn/glosa_*.dmn` (reason_normalization, classification, triage, contas_sla) + `docs/processes/test-specs/SP-OP-CONTAS-001.md` + `tests/integration/processes/test_sp_op_contas_001.py` | **WA.1** |
| `…RECURSO-001.*` + `dmn/recurso_*.dmn` + tests | **WA.2** |
| `src/maezo/agents/marina/**` | **WA.3** |
| workers `operadora.contas.*` / `operadora.recurso.*` (port glosa) | **WA.4** |
| `…NIP-001.*` + `dmn/nip_*.dmn` + tests | **WB.1** |
| `…ANS-SUBMIT-001.*` + `dmn/ans_*.dmn` + tests | **WB.2** |
| `src/maezo/agents/gustavo/**` + `mcp-ans`/`mcp-regdata` tool defs | **WB.3** |
| workers regulatórios (port `generate_regulatory_reports`/`ans_client`) | **WB.4** |
| `…CANCEL-001.*` + `dmn/cancel_*.dmn` + tests | **WC.1** |
| `…REEMBOLSO-001.*` + `dmn/reembolso_*.dmn` + tests | **WC.2** |
| `src/maezo/agents/lucas/**` + CNAB/conciliação worker (port) | **WC.3** |
| `deploy/helm/maezo-tenant/**` + `provision-tenant` script | **WD.1/WD.2** |
| `src/maezo/runtime/harness.py` (merge), novo `src/maezo/platform/tenancy/**` | **W0.4** |
| `src/maezo/runtime/metrics.py` (extensão) | **WD.3** |
| `tests/integration/cross_process/**` | **WE.1** |
| `docs/reports/phase2-report.md`, `docs/review-queue.md`, `HANDOFF.yaml` | **WE.2** (curador final) |

**Contenção a vigiar:** `topic_registry.yaml`, `process_allowlist.py`, `L0-core.yaml`, `review-queue.md`, `harness.py`, helm `values.yaml`. Todos roteados a um único dono ou ao curador final; demais workstreams abrem PR contra eles via o dono.

### 7.2 Topologia de swarm / tier (T1–T4)
- **T4 (Fable, principal):** W0.4 merge engine (ADR-0004, arquitetural, toca harness/hash de versão); WA.2 RECURSO-001 (negativa-like canônico, invariante de integração); WE.1 cross-process invariantes.
- **T3 (senior):** WA.1 CONTAS-001; WB.1 NIP-001 (prazos ANS hard); WC.1 CANCEL-001; WC.2 REEMBOLSO-001; WA.3 Marina; WB.3 Gustavo; WD.1/WD.2 provisionamento.
- **T2 (mid):** WB.2 ANS-SUBMIT-001 (sem no-denial, mais mecânico); WA.4/WB.4 ports de worker (reautoração async de lógica conhecida); WD.3 observabilidade; WC.3 Lucas (escopo mínimo até sign-off).
- **T1 (junior/mecânico):** W0.2 registro de tópicos; W0.3 CNAB parser port (puro, fixtures); preenchimento de test-spec golden derivado de contratos prontos.

### 7.3 Mapa de paralelização (concorrente vs sequenciado em dependência real)
**Concorrente desde o início (após W0):**
- Todas as 6 quádruplas clonam o esqueleto AUTH-001 independentemente → **estruturalmente paralelas**.
- Wave A, Wave B e Wave D rodam concorrentes (sem dependência de artefato entre elas).
- Os 3 agentes começam contra os contract sheets dos seus processos (§4-bis-F), não contra os BPMNs prontos.
- Ports puros (CNAB, relatórios regulatórios) 100% paralelos.

**Sequências reais (única dependência de artefato):**
1. **W0.2 (tópicos) → todo BPMN** (validator hard-falha tópico não registrado). W0.2 primeiro.
2. **W0.1 (allowlist) → start de processo por agente** (Marina/Gustavo só iniciam após `KNOWN_PROCESS_KEYS` aceitar os keys).
3. **WA.1 contract sheet → WA.2 começa** (RECURSO consome glosa; mas só precisa do *contrato* de CONTAS, não do BPMN — §4-bis-F).
4. **W0.4 merge engine → WD.1/WD.2** (provisionamento entrega definições mergeadas).
5. **WB.4 port relatórios → WB.2 service tasks** (ANS-SUBMIT chama `mcp-regdata.assemble`).
6. **WE.1 → depois das quádruplas** (testa invariantes cross-process).

---

## 8. Registros DRAFT / review-queue (artefato → revisor)

Todo artefato regulado/clínico entra em `docs/review-queue.md` com `status: DRAFT — requires human review before any deploy` e é espelhado em `HANDOFF.yaml → review_queue_pending`. Drafts nunca deployam a tenant vivo; `hard: true` permanece CI-enforced.

| Artefato | Revisor |
|---|---|
| `SP-OP-CONTAS-001.*` (fluxo glosa; exceção de glosa técnica auto-route) | médico-auditor + compliance (auditoria-contas) |
| `glosa_classification.dmn` / `glosa_triage.dmn` (tabelas reason→categoria, appealability) | médico-auditor/auditoria-contas + compliance |
| `SP-OP-RECURSO-001.*` + `recurso_*.dmn` (admissibilidade, prazos RN 424) | auditoria-contas + jurídico/regulatório |
| Textos de carta de recurso (port `generate_appeal_documentation`) | jurídico/regulatório |
| `SP-OP-NIP-001.*` + `nip_*.dmn` (**prazos assistencial/não-assistencial RN 388 — HARD**) | jurídico/regulatório + regulatório-ANS |
| `SP-OP-ANS-SUBMIT-001.*` + `ans_calendar.dmn` (todas datas/fontes RN do calendário) | regulatório/jurídico |
| `SP-OP-CANCEL-001.*` + `cancel_*.dmn` (**RN 593 cancelamento/inadimplência**) | jurídico/contratos + compliance |
| `SP-OP-REEMBOLSO-001.*` + `reembolso_*.dmn` (cobertura/teto/prazo de reembolso) | médico-auditor + compliance |
| Charter de Lucas (escopo billing-navigator) | produto + regulatório (§9 OQ1) |
| `KNOWN_PROCESS_KEYS` diff (ADR-0016) | CODEOWNERS (segurança) |
| Prazos SLA de todos os 6 (dias úteis→ISO, RNs vigentes) | jurídico/regulatório |

---

## 9. Riscos + Open Questions (consolidado das três pesquisas; precisa SME/regulatório)

- **R1 — Prazos ANS NIP são HARD com exposição a sanção.** Os timers mais schedule-críticos da Phase 2. **OQ:** confirmar prazos exatos assistencial (~5 úteis) / não-assistencial (~10 úteis) e a conversão dias-úteis→ISO com regulatório/jurídico antes de qualquer timer em produção (RN 388/IN DIDES).
- **R2 — Citações de RN não confirmadas** (RN 305/501 TISS glosa; RN 424 recurso; RN 388 NIP; RN 543/124 SIP; RN 593 cancelamento; RN reembolso). Todas marcadas `DRAFT/verify`; jurídico deve confirmar contra texto vigente ANS (RN podem ter sido consolidadas/substituídas).
- **R3 — Charter de Lucas indefinido.** **OQ1 (sign-off necessário):** Lucas = billing-navigator (recomendado, dá consumidor vivo ao port CNAB) vs segundo clinical navigator (reusa Helena, quádrupla mínima)? Precisa produto+regulatório.
- **R4 — Exceção de glosa técnica auto-route (L2).** Glosas puramente técnicas/formatação (linha duplicada, campo faltante) podem auto-rotear sem humano? **OQ:** só com compliance sign-off; default conservador = revisão humana.
- **R5 — Inadmissibilidade por prazo procedural duro (RECURSO/CANCEL).** Auto-route de prazo-expirado puro é borderline negativa-like. **OQ:** tratar como humano-gated por default; auto-route só com jurídico sign-off.
- **R6 — Merge engine L0–L3 (ADR-0004) é o maior gap de plataforma.** Sem ele, "provisionamento ≤1 dia" é copy-paste-folder, não real. Risco de cronograma se subestimado; mitigação: W0.4 começa cedo, T4, adapta padrão provado do reference.
- **R7 — Versão do engine** (ADRs citam 2.1.3; imagem pública 2.1.0, DL-0006). Revalidar BPMN deployável no upgrade.
- **R8 — Indício de fraude em CONTAS** (`fraud_accusation` L0 hard): garantir que nenhuma branch auto-flagueia; sempre User Task → Phase 3 FRAUDE-001.
- **R9 — Tasy write:** confirmado DROP em todo port (consumimos CDC do amh-data-platform, nunca escrevemos — MEMORY/ADR-0013). Vigiar no port de `identify_glosa`/`update_payment`.

---

## 10. AWS-blocked (issue #16) vs buildable agora

**Buildable agora (toda a engenharia D1–D7, D9, D10):** as 6 quádruplas BPMN/DMN/contrato/test-spec; os testes de invariante no-denial contra o engine real em CI (como Phase 1); os 3 agentes + grafos + golden evals; delegações A2A; ports reautorados + re-testados com fixtures próprios; merge engine L0–L3; Helm templating + `provision-tenant` (render/dry-run); extensão de observabilidade; allowlist PR.

**AWS-blocked (não bloqueia o acima):**
- `terraform apply` em staging (D8 deploy real).
- Tasy Oracle creds (consumo CDC real; simulador cobre dev/CI).
- WhatsApp WABA token (Lucas/Gustavo envio real).
- LLM API keys + endpoint LLM PHI-zone BR-resident (decisão D-06: Bedrock vs dedicado — bloqueia Marina, que é PHI zone).
- Conectividade real ANS (`mcp-ans.submit` filing real) e bancária (CNAB retorno real).
- OIDC/Cognito real do console de User Tasks.
- 16+ itens DRAFT de review-queue aguardam sign-off médico-auditor/jurídico/DPO antes de qualquer deploy produtivo.

**Conclusão operacional:** Phase 2 é integralmente construível e CI-verificável agora (mesma doutrina "nunca mockar o engine" de Phase 0/1); o único bloqueio é deploy vivo + conectividade externa + sign-off humano regulatório — todos rastreados em issue #16 e na review-queue.
