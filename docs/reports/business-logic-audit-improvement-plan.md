# Maezo Business-Logic Audit — Improvement Plan

**Generated:** 2026-07-02
**Method:** 16 per-process auditors (15 SP-OP operational processes + AGJ-HELENA-TRIAGE agent-graph) + 5 cross-cutting analyzers (PHI seams, HITL/autonomy, cross-process choreography, worker observability, strengths) + 1 synthesizer. Model routing: T1 = claude-haiku-4-5 (mechanical/config/docs), T2 = claude-sonnet-4-6 (standard impl + tests + DMN authoring), T3 = claude-opus-4-8 (PEP/PHI/audit/engine-critical, cross-component debugging).
**Input baseline:** `main` @ current HEAD (post-#91, `1c520b3`).
**Companion audits (do not re-litigate):** `docs/audits/forensic-adr-audit.md` (37 Section-4 gaps resolved via PRs #65–#86), `docs/audits/bpmn-process-completeness.md` (zero-`callActivity` is intentional), `docs/reports/autonomous-completion-report.md` (autonomous-completion run, 2026-06-14).
**Verification:** independent adversarial re-verification pass (3 reviewers, 20 critical/high findings sampled): 17 confirmed, 2 partial (GAP-AUTH-2 downgraded critical→high, GAP-ESC-2 downgraded high→low), 1 payload-detail correction (GAP-AUTH-1); registry re-sorted and wave membership reconciled accordingly.

Every gap below carries fresh `file:line`/element evidence in CURRENT code. Findings already resolved by the forensic audit are not re-reported unless a live regression is cited.

---

## 4.1 Executive Summary

### Exemplary patterns (reference templates — keep and propagate)

1. **ADR-0018 five-part no-denial structural pattern** — every adverse/irreversible outcome (denial, cancellation, discharge, glosa acceptance, descredenciamento, fraud accusation, high-value payment, LGPD release, ANS transmission) is structurally unreachable by automation across all 13 adverse-bearing processes: (1) DMN has no adverse output column, (2) adverse-end gateway fed only by a completed human User Task, (3) worker re-validates the human decision variable against an exact allowed set, (4) worker requires a non-empty audit trail or raises a typed `WorkerBpmnError`, (5) the LLM agent's LangGraph is type-level incapable of the adverse variant. Evidence: `auth.py:283-289` (ERR_AUTH_DENIAL_NOT_HUMAN), `cancel.py:357-395`, `fraude.py:546-559`, `pagto.py:426-483`, mirrored in 13 modules.
2. **DMN fail-safe catch-all row (hitPolicy FIRST, conservative last rule)** — every audited DMN closes with a wildcard `-` final rule routing unmapped input to the most conservative outcome (ANALISE_HUMANA / shortest SLA / worst severity), never a permissive default. `triage_redflag_adult.dmn:122-141`, `pagto_alcada.dmn:90-98`, `adequacao_gap.dmn:130-194`.
3. **Type-level agent adverse-exclusion** — routing types are closed `Literal[...]` unions with no adverse member; dossier builders hardcode decision fields to `None` with `GUARDRAIL ESTRUTURAL` comments. `rafael/graph.py:301,606-608`, `beatriz/graph.py:391-409`, `carolina/graph.py:743-746`.
4. **Real-engine (never-mock-the-engine) invariant sweep tests** — 8–96-combination DMN sweeps assert against real CIB Seven history that no adverse end appears without a preceding completed human User Task. `test_sp_op_cred_001.py:351-399` (96 combos), `test_sp_op_fraude_001.py:420-445`.
5. **Fail-closed BPMN gateway default (error-terminal, not happy-path)** — integrity-critical gateways invert the default flow to a thrown `bpmn:error` end event. `SP-OP-FRAUDE-001.bpmn:183-193` (GW_CustodiaSelada), `SP-OP-LGPD-DSR-001.bpmn:73` (GW_Identidade).
6. **Engine-safe DMN output-variable promotion (anticipating ENGINE-16004)** — explicit `camunda:outputParameter` promotions after every `singleResult` businessRuleTask; currency typeRefs use `long`/centavos; harness promotes int32-overflow to Camunda `Long`. `SP-OP-ADEQUACAO-001.bpmn:125-139`, `harness.py:102-110`.
7. **Bounded engine-driven retry/loop with DMN-computed cap** — `loop_counter` init-once via `outputParameter=${0}`, incremented in-body, gateway-capped to human escalation. `SP-OP-RECURSO-001.bpmn:292-329`, `ans_retry_policy.dmn:59-64`.
8. **LGPD/PHI chokepoint gate preceding every PHI-touching branch** — single fail-closed consent gate upstream of all PHI use. `programa.py:206-223` (ERR_PROGRAMA_NO_CONSENT), `custody.py:136-179`.
9. **Drift-free choreography registry** — `config/topic_registry.yaml` (GENERATIVO) + `process_allowlist.py` `KNOWN_PROCESS_KEYS` with `DEFAULT_ALLOWED_PROCESS_KEYS = frozenset(KNOWN_PROCESS_KEYS)`; zero topic/key drift across 17 processes.
10. **SLA as BPMN boundary timers reading DMN-computed durations** — non-interruptive (at-risk) + interruptive (breach) timer pairs; LGPD anchors its statutory 15-day clock at instance start. `SP-OP-AUTH-001.bpmn:234-272`, `SP-OP-LGPD-DSR-001.bpmn:205-211`.

### Audit statistics

**Total deduplicated gaps: 115** (99 per-process Group A + 16 cross-cutting Group B; one B/B duplicate merged — see §4.2).

| Severity | Count |
|---|---|
| critical | 13 |
| high | 43 |
| medium | 41 |
| low | 18 |

| Dimension | Count |
|---|---|
| business_logic | 70 |
| implementation | 45 |

**Per-process gap counts (Group A):** AUTH 7, CANCEL 7, CONTAS 6, FRAUDE 6, NIP 6, REEMBOLSO 6, RECURSO 4, INADIMPLENCIA 7, LGPD 6, ADEQUACAO 8, CRED 6, ESCALATION 5, PAGTO 8, PROGRAMA 7, ANS-SUBMIT 5, HELENA-TRIAGE 5. **Cross-process (Group B):** XPHI 3, XHITL 3 (XHITL-1 merged into XPROC-1), XPROC 3, XOBS 7.

### Top 3 systemic risks needing immediate attention

- **SR-1 — Deployment-completeness collapse (six processes never run; five handoffs never fire).** `worker_runtime/service.py::_register_all_workers` wires only 8 of 15 worker modules; `adequacao/cred/fraude/inadimplencia/pagto/programa` are unregistered, so those processes deadlock at their first serviceTask forever, yet `/readyz` stays green (`GAP-XOBS-1`). In parallel, `notifications_bridge` dispatches only 2 of 6 cross-process handoff types — `fraude.start_credenciamento`, `fraude.start_contratual`, `adequacao.start_credenciamento`, `inadimplencia.handoff_rescisao` all silently NO-OP, so human-confirmed fraud/inadimplência corrective actions orphan (`GAP-XPROC-1`, absorbing `GAP-INAD-2`, `GAP-XHITL-1`). LGPD-DSR has no worker module at all (`GAP-LGPD-1`) and Helena crashes on the first tool call every turn (`GAP-TRIAGE-1/2`). This is the single largest go-live blocker.
- **SR-2 — PHI egress seams bypass the ADR-0016 single-pseudonymization guarantee.** The gateway seam pseudonymizes tool *results*, but three worker→Kafka egress classes copy raw process variables verbatim onto `agents.events.*` / `operadora.notifications.internal` (`GAP-XPHI-1`, absorbing `GAP-AUTH-1`, `GAP-ESC-3`), no application-layer structlog PHI processor exists at either runtime entrypoint (`GAP-XPHI-2`, with a live cleartext ICD-10 leak at `auth.py:345` = `GAP-AUTH-7`), and the WhatsApp webhook publishes raw `message_body` then persists it to the DLQ (`GAP-XPHI-3`). ADR-0006/0016 breach at scale.
- **SR-3 — Governance ceilings and RN259 SLA observability are decorative.** Financial autonomy ceilings (`authorization_approval.max_value_brl`, `high_value_payment.threshold_brl`) are read by zero code; `dentro_teto_l2` is echoed from an inbound boolean, so any upstream component seeding it `true` bypasses the L2 ceiling (`GAP-XHITL-2`, absorbing `GAP-AUTH-4`, `GAP-REEMBOLSO-3`, `GAP-PAGTO-3`). The one named critical compliance alert (`MaezoUserTaskSLABreach`, RN259 / Lei 9.656/98 art. 35-C) reads a counter emitted from zero production paths (`GAP-XOBS-2`), and 11 of 15 `notify_sla_risk` handlers never import `metrics` (`GAP-XOBS-3`) — SLA breaches are unobservable platform-wide.

---

## 4.2 Gap Registry (deduplicated, sorted severity DESC then fix_complexity ASC)

Dedup notes: cross-process findings keep their `GAP-X*-N` id with **Process = CROSS**. `GAP-XHITL-1` is merged into `GAP-XPROC-1` (identical `notifications_bridge` defect). Cross-cutting findings that generalize a specific per-process gap keep both rows with a cross-ref (the specific row is fixed as part of the systemic work-stream where noted).

### Critical (13)

| Gap ID | Process | Sev | Dim | Summary | ADR | Cx |
|---|---|---|---|---|---|---|
| GAP-TRIAGE-2 | HELENA-TRIAGE | critical | impl | `_evaluate_dmn()` calls mcp-dmn with `{table,input}` but handler needs `decision_id/variables` → KeyError every turn | ADR-0012 | xs |
| GAP-ADEQ-1 | ADEQUACAO-001 | critical | impl | `register_fallback_commitment` requires `tier` no UT/DMN sets → every COMPROMISSO_FALLBACK raises ERR_FALLBACK_COMMITMENT_NOT_HUMAN, no boundary catch → stuck | ADR-0018 | s |
| GAP-REEMBOLSO-1 | REEMBOLSO-001 | critical | impl | Sign-off gate `promotable_artifacts()` resolves `decisionRef` to `{ref}.dmn` by filename; `reembolso_admissibility`/`reembolso_auto_approval` live inside `reembolso_coverage.dmn` → ungoverned | ADR-0012 | s |
| GAP-AUTH-1 | AUTH-001 | critical | bl | ST_PublishNegada copies raw `justificativa_clinica`/`tenant_id` verbatim onto `agents.events.auth.completed`, zero pseudonymization (root = GAP-XPHI-1) | ADR-0006 | s |
| GAP-INAD-2 | INADIMPLENCIA-001 | critical | bl | `inadimplencia.handoff_rescisao` published but notifications_bridge actions only contas/nip types → SP-OP-CANCEL never starts (single-process view of GAP-XPROC-1) | ADR-0003/0015 | s |
| GAP-TRIAGE-1 | HELENA-TRIAGE | critical | impl | Helena `identify()` calls mcp-fhir with `beneficiario_pseudo_id` but handler needs `patient_id` → KeyError before classify()/DMN runs | ADR-0012 | m |
| GAP-INAD-1 | INADIMPLENCIA-001 | critical | bl | `ja_em_rescisao_cancel` anti-double-termination guard only echoes an absent input var; never a real cross-process query → guard can never fire | ADR-0018 | m |
| GAP-CRED-1 | CRED-001 | critical | bl | 3 bpmn:errors declared, zero errorEventDefinition/boundary anywhere; ST_VerifyCredentials throws on mainline → stuck incident | — | m |
| GAP-XPROC-1 | CROSS | critical | bl | `notifications_bridge` ACTIONABLE_TYPES dispatches only 2 of 6 cross-process handoff types; `fraude.start_credenciamento/contratual`, `adequacao.start_credenciamento`, `inadimplencia.handoff_rescisao` NO-OP; producers omit a downstream-derived business_key/entidade_tipo (absorbs GAP-INAD-2, GAP-XHITL-1) | ADR-0003/0015 | m |
| GAP-XOBS-1 | CROSS | critical | bl | 6 of 15 worker modules (adequacao/cred/fraude/inadimplencia/pagto/programa) never registered in `_register_all_workers` → 6 processes deadlock; `/readyz` stays green (absorbs GAP-ADEQ-5) | ADR-0001 | m |
| GAP-XOBS-2 | CROSS | critical | impl | `inc_user_task_sla_breach`/`set_process_instance_active`/etc. called from zero prod paths → named critical RN259 alert `MaezoUserTaskSLABreach` can never fire; 4/5 engine dashboard panels blank | ADR-0010 | m |
| GAP-PAGTO-1 | PAGTO-001 | critical | bl | `pagto_admissibility.dmn` (gates dados_pagamento_validos/lastro/duplicidade) does not exist and is never wired; GW_Faixa routes purely on value | ADR-0012 | l |
| GAP-LGPD-1 | LGPD-DSR-001 | critical | impl | No `workers/lgpd.py`; none of 6 DSR external-task topics has a worker → deadlocks on ST_VerificarIdentidade (first mainline task) | ADR-0001 | l |

### High (43)

| Gap ID | Process | Sev | Dim | Summary | ADR | Cx |
|---|---|---|---|---|---|---|
| GAP-AUTH-5 | AUTH-001 | high | impl | ST_PublishAprovadaAuto/Auditor omit `numero_autorizacao` from event_payload_vars though produced moments earlier | — | xs |
| GAP-CRED-2 | CRED-001 | high | bl | `cred_prior_notice.dmn` requires `tem_beneficiarios_vinculados`; seeded by graph but undeclared in contract Variáveis de entrada | — | xs |
| GAP-INAD-3 | INADIMPLENCIA-001 | high | impl | `register_contract_suspension` hard-requires `tier`, absent from contract campos obrigatórios and UT output docs | — | xs |
| GAP-LGPD-4 | LGPD-DSR-001 | high | bl | GW_DecisaoDsr default (`Flow_GWDec_Enviar`) fires for any non-exact value incl. missing/blank → silent data release; currently unreachable in prod because GAP-LGPD-1 deadlocks instances upstream, but the fail-open gateway design is real | ADR-0005 | xs |
| GAP-CANCEL-1 | CANCEL-001 | high | bl | `vinculo_ativo` (contract-mandatory fact) never wired as input to `cancel_admissibility.dmn`; r_pedido_l2 can fire EFETIVAR on inactive vínculo | ADR-0012 | s |
| GAP-REEMBOLSO-2 | REEMBOLSO-001 | high | bl | `ERR_REEMBOLSO_INVALID_PROTOCOLO` thrown by check_coverage guard on mainline, no boundary catch → stuck instance | — | s |
| GAP-CONTAS-3 | CONTAS-001 | high | bl | `glosa_triage` r_sem_glosa (row 1) ignores `categoria_normalizada`, preempts r_tecnica_humano under hitPolicy FIRST → tecnica/clinica glosa auto-cleared | ADR-0012 | s |
| GAP-FRAUDE-1 | FRAUDE-001 | high | bl | BRT_Indicadores writes `indicadores` object; fraude_routing/fraude_sla read bare `intensidade_investigacao` never set top-level | ADR-0012 | s |
| GAP-AUTH-7 | AUTH-001 | high | bl | `send_denial_notice` logs `cid10_referencia` (ICD-10 PHI) cleartext via structlog on every NEGAR (specific instance of GAP-XPHI-2) | ADR-0006 | s |
| GAP-REEMBOLSO-3 | REEMBOLSO-001 | high | bl | `dentro_teto_l2` ceiling has no governance backing; no reembolso action in L0-core.yaml, no ceiling in tenants-amh.yaml (see GAP-XHITL-2) | ADR-0008 | s |
| GAP-LGPD-3 | LGPD-DSR-001 | high | bl | `NEGAR_FUNDAMENTADO exige fundamentacao_legal` unenforced; UT_RevisaoDpo no formData, gateway checks only decisao_dsr | — | s |
| GAP-PAGTO-2 | PAGTO-001 | high | bl | Contract terminal `End_PagamentoCancelado` (cancelled/duplicate/no-lastro) has no endEvent in BPMN | — | s |
| GAP-PAGTO-4 | PAGTO-001 | high | impl | `ERR_PAGTO_ORDEM_INVALIDA` thrown by validate_payment_data, no boundary catch → stuck incident | — | s |
| GAP-CANCEL-3 | CANCEL-001 | high | bl | MANTER path has no worker-level guard enforcing `fundamentacao_contratual` mandatory (unlike RESCINDIR/SUSPENDER) | ADR-0018 | m |
| GAP-XPHI-2 | CROSS | high | impl | No app-layer structlog PHI processor at either runtime entrypoint; span deny-list omits clinical names; live cleartext `cid10_referencia` leak at `auth.py:345` (= GAP-AUTH-7) | ADR-0006 | m |
| GAP-XPHI-1 | CROSS | high | bl | 3 worker→Kafka egress classes copy raw process vars verbatim onto `agents.events.*`/`operadora.notifications.internal`; no phi_fields concept on any worker→Kafka path (absorbs GAP-AUTH-1, GAP-ESC-3) | ADR-0006 | m |
| GAP-CONTAS-2 | CONTAS-001 | high | impl | `identify_glosa`/`calculate_impact` echo inputs; never read `linhas_conta_refs`/`reason_codes_tiss`/`valor_apresentado_brl` to compute | — | m |
| GAP-FRAUDE-2 | FRAUDE-001 | high | impl | `register_fraud_accusation` calls `verify_custody_bundle(candidate)` with `chain=None`; fabricates `sealed_record_hash` from `audit.head_hash` | ADR-0020 | m |
| GAP-XHITL-2 | CROSS | high | bl | Financial autonomy ceilings read by zero code; `dentro_teto_l2` echoed from inbound boolean → L2 ceiling bypassable (absorbs GAP-AUTH-4, GAP-REEMBOLSO-3, GAP-PAGTO-3) | ADR-0008 | m |
| GAP-AUTH-4 | AUTH-001 | high | bl | ADR-0008 `authorization_approval.max_value_brl` never read/enforced by any PEP or worker (specific instance of GAP-XHITL-2) | ADR-0008 | m |
| GAP-RECURSO-1 | RECURSO-001 | high | bl | BT_PrazoMaxRecurso (P30D RN424 ceiling) attached only to UT_AnaliseRecursoAnalista; sibling tasks after cancel/escalation have no equivalent | — | m |
| GAP-LGPD-2 | LGPD-DSR-001 | high | bl | Erasure cascade (`mcp_memory.erase_by_patient`, expurgo) has zero path from ST_ExecutarRequisicao → approved erasure erases nothing | ADR-0002 | m |
| GAP-CRED-3 | CRED-001 | high | bl | No `docs/processes/test-specs/SP-OP-CRED-001.md` → mandatory quadruple broken (sibling FRAUDE has one) | — | m |
| GAP-ADEQ-2 | ADEQUACAO-001 | high | bl | No `test-specs/SP-OP-ADEQUACAO-001.md`; no real-engine test verifies End_CompromissoFallbackHumano-only-via-human invariant | ADR-0018 | m |
| GAP-ADEQ-3 | ADEQUACAO-001 | high | bl | Contract declares `decisao_coordenacao`; GW_DecisaoRemediacao reads only `decisao_remediacao` → coordinator disposition never routes | — | m |
| GAP-ESC-1 | ESCALATION-001 | high | bl | `agents.events.process_completed` published on devolvido_agente but no consumer exists → agent conversation never resumed (see GAP-XHITL-4) | ADR-0003 | m |
| GAP-PAGTO-3 | PAGTO-001 | high | impl | faixa→min-approver-tier escada hardcoded as Python dict `_FAIXA_TIER_MINIMO`, outside DMN governance (see GAP-XHITL-2) | ADR-0012 | m |
| GAP-PAGTO-8 | PAGTO-001 | high | bl | No `test-specs/SP-OP-PAGTO-001.md` → mandatory quadruple broken | — | m |
| GAP-PROG-1 | PROGRAMA-001 | high | impl | stratify_risk/build_care_plan never invoke Valentina A2A (care.stratify/care.enroll); workers echo inputs | ADR-0012 | m |
| GAP-PROG-2 | PROGRAMA-001 | high | impl | ST_PublishCompleted declares no `event_desfecho` input → `agents.events.programa.completed` never carries desfecho | ADR-0007 | m |
| GAP-ANS-1 | ANS-SUBMIT-001 | high | bl | Start_CalendarioRegulatorio single TimerStartEvent (R/P1M) can't deliver per-type cron for 5 report types with distinct periodicities | — | m |
| GAP-ANS-2 | ANS-SUBMIT-001 | high | bl | `ans_calendar.dmn` single input `report_type`; contract requires `competencia` too → identical due_date/sla per competência | ADR-0012 | m |
| GAP-TRIAGE-5 | HELENA-TRIAGE | high | impl | Prompts promise Helena answers from Patient/Coverage but `agent.yaml` tools allowlist omits mcp-fhir.read_coverage/search_coverage | — | m |
| GAP-XOBS-3 | CROSS | high | impl | 11 of 15 notify_sla_risk-class handlers never import `metrics`; only NIP/ANS emit, via non-canonical instruments the alert can't read | ADR-0010 | m |
| GAP-XOBS-4 | CROSS | high | impl | `WorkerHarness._handle` emits zero span/counter on all 4 outcomes; worker_runtime never calls `install_tracer_provider` → no-denial guard firings invisible | ADR-0010 | m |
| GAP-XPROC-2 | CROSS | high | impl | CRED→ADEQUACAO `agents.events.cred.network_changed` has no consumer; Start_AvaliacaoAdequacao is a plain none-start (no messageEventDefinition) | ADR-0003/0015 | m |
| GAP-AUTH-2 | AUTH-001 | high | bl | Console enforces NEGAR's 3 fields server-side (schemas.py:204-213); residual: AUTH BPMN declares ERR_AUTH_INVALID_GUIA with zero errorEventDefinition/boundary (`:234`/`:250` are timer-only) + worker's `ERR_AUTH_DENIAL_INCOMPLETE` undeclared/uncaught → stuck incident only if console bypassed (direct engine REST completion); UT_AnaliseMedicoAuditor has no camunda:formData — missing defense-in-depth, not a live bypass | ADR-0005 | m |
| GAP-AUTH-3 | AUTH-001 | high | bl | `dut_rol_coverage`/`dut_criteria_*`/`carencia_check` documented as SP-OP-AUTH-001 DMN consumers but no code path (Rafael's graph, workers) invokes them via mcp-dmn | ADR-0012 | l |
| GAP-CONTAS-1 | CONTAS-001 | high | impl | ST_PrepareTriageDossier never invokes MarinaGraph/A2A; publishes inert no-op notification; test asserts "TEM handler real" | — | l |
| GAP-CONTAS-4 | CONTAS-001 | high | bl | BT_SlaTriagem timers anchored to UT creation, not `data_recebimento_lote` (contract âncora); the anchor var is never read | — | l |
| GAP-FRAUDE-3 | FRAUDE-001 | high | bl | 7 `fraude_scoring/*.dmn` tables have no real consumer; `score_indicators`/`gather_evidence` pass-through, no mcp-dmn call | ADR-0012 | l |
| GAP-NIP-1 | NIP-001 | high | bl | All NIP boundary timers anchored to activity-attach, not `data_recebimento_nip_iso` (contract mandate); anchor never read | — | l |
| GAP-TRIAGE-4 | HELENA-TRIAGE | high | impl | ESCALATION End_DevolvidoAoAgente publishes `agents.events.process_completed`, no consumer → Helena never resumes (see GAP-XHITL-4) | ADR-0003/0015 | l |

### Medium (41)

| Gap ID | Process | Sev | Dim | Summary | ADR | Cx |
|---|---|---|---|---|---|---|
| GAP-AUTH-6 | AUTH-001 | medium | impl | `notify_sla_risk` reads flat `sla_analise`/`sla_alerta`; BRT_SlaAnalise stores single `sla` object var | — | xs |
| GAP-FRAUDE-4 | FRAUDE-001 | medium | impl | `register_fraud_accusation` raises ERR_FRAUD_ACCUSATION_NOT_HUMAN even for custody-verification failures, contradicting contract error table | ADR-0018 | xs |
| GAP-RECURSO-2 | RECURSO-001 | medium | impl | `notify_sla_risk` reads flat `sla_analise`/`sla_alerta`; BRT_Sla sets only nested `sla` var | — | xs |
| GAP-ADEQ-6 | ADEQUACAO-001 | medium | bl | Contract Tópicos omits `update_monitoring_plan`/`notify_rede` though implemented + registered | — | xs |
| GAP-FRAUDE-5 | FRAUDE-001 | medium | bl | Contract declares only `destino_referral`; GW_DestinoReferral depends on 4 undeclared flat booleans `destino_referral_cred/_contratual/_juridico/_ans` | — | xs |
| GAP-ADEQ-5 | ADEQUACAO-001 | medium | impl | `register_adequacao_workers` never called from `_register_all_workers` (subsumed by GAP-XOBS-1) | — | xs |
| GAP-CANCEL-2 | CANCEL-001 | medium | bl | `dentro_prazo` declared mandatory input to `cancel_admissibility.dmn` but every rule uses `-` → zero effect | ADR-0012 | s |
| GAP-CANCEL-4 | CANCEL-001 | medium | impl | `notificacao_previa_feita=true` requirement for progress undeclared in contract (only in test scaffolding) | — | s |
| GAP-CANCEL-5 | CANCEL-001 | medium | bl | `cancel_routing.dmn` (claims "Consumida por SP-OP-CANCEL-001") wired to no businessRuleTask/worker → dead | ADR-0012 | s |
| GAP-CANCEL-7 | CANCEL-001 | medium | bl | Flow_GWDec_EfetivarPedido lets human route any tipo_solicitacao (incl. inadimplencia/fraude) to member-request effectuation | ADR-0007 | s |
| GAP-CONTAS-5 | CONTAS-001 | medium | bl | `operadora.contas.analyze_reason` has worker + registry entry but no serviceTask dispatches it → dead | — | s |
| GAP-NIP-2 | NIP-001 | medium | bl | `nip_routing.dmn` computes `grupo_humano`/`sla_alerta_iso` never referenced; UTs use static candidateGroups | ADR-0012 | s |
| GAP-INAD-4 | INADIMPLENCIA-001 | medium | bl | No `test-specs/SP-OP-INADIMPLENCIA-001.md` → mandatory quadruple broken | — | s |
| GAP-INAD-5 | INADIMPLENCIA-001 | medium | bl | Contract frames CANCEL rescission ownership as unresolved BLOQUEANTE + documents RESCINDIR artifacts contradicting shipped BPMN | — | s |
| GAP-CRED-6 | CRED-001 | medium | bl | BRT_PriorNotice wired before GW_Natureza → runs for credenciamento too, where it has no direcao input | — | s |
| GAP-ESC-3 | ESCALATION-001 | medium | bl | `notas_resolucao` free text published verbatim (no scrub) via publish worker into escalation.resolved/process_completed (see GAP-XPHI-1) | ADR-0006 | s |
| GAP-ESC-4 | ESCALATION-001 | medium | bl | `resultado` enum has no structural enforcement; UTs declare no formData, no schema validation | — | s |
| GAP-PAGTO-5 | PAGTO-001 | medium | bl | Contract declares `decisao_coordenacao`; UT_CoordenacaoAlcada reuses `decisao_pagamento` field/gateway | — | s |
| GAP-PROG-3 | PROGRAMA-001 | medium | bl | `elegivel_programa` in event_payload_vars but BRT_Routing binds only nested `roteamento` → flat var never set | — | s |
| GAP-PROG-7 | PROGRAMA-001 | medium | bl | No `test-specs/SP-OP-PROGRAMA-001.md` → mandatory quadruple broken | — | s |
| GAP-ANS-3 | ANS-SUBMIT-001 | medium | impl | `ans_submit_variables()` never sets report_type/competencia/periodicidade (all obrigatória) → bad business key | — | s |
| GAP-ANS-4 | ANS-SUBMIT-001 | medium | bl | UT_RevisarEnvioJuridico/UT_CorrigirPendenciaEnvio have no boundary SLA timer → indefinite stall on deadline | ADR-0007 | s |
| GAP-XPROC-3 | CROSS | medium | bl | `recurso_variables()` omits `glosa_type` + 3 pre-resolved facts → recurso_admissibility.dmn falls to ANALISE_HUMANA for 100% of handoff instances, skips BRT_Eligibility | ADR-0012 | s |
| GAP-XOBS-5 | CROSS | medium | impl | `record_hitl_approved` fired once from console with literal `agent='console.medico-auditor'` never matching real agent → MaezoHITLApprovalRateDrop permanent false alarm | ADR-0010 | s |
| GAP-RECURSO-3 | RECURSO-001 | medium | impl | `Error_RecursoGlosaInvalida` declared but zero throw sites in recurso.py; siblings implement the invalid-origin guard | — | s |
| GAP-PROG-5 | PROGRAMA-001 | medium | impl | Valentina hardcodes DMN id `programa_stratification`; deployed decision is `programa_routing` → ENGINE-16004 at assess node | ADR-0012 | s |
| GAP-NIP-3 | NIP-001 | medium | bl | MANTER_NEGATIVA "embute authorization_denial L0-hard" but classified as generic `nip_response:{L1}` (see GAP-XHITL-3) | ADR-0008 | m |
| GAP-NIP-4 | NIP-001 | medium | impl | Contract declares `protocolo_filing` output; no worker/BPMN sets it; ends terminate before correlation | ADR-0003 | m |
| GAP-REEMBOLSO-4 | REEMBOLSO-001 | medium | impl | ST_PrepararDossie docstring claims A2A Marina convocation; handler only publishes internal notification | — | m |
| GAP-REEMBOLSO-5 | REEMBOLSO-001 | medium | bl | ST_CalculateAmount runs before BRT_Calculo (produces `valor_calculado_tabela_cents`); `dentro_tabela` depends on it | ADR-0012 | m |
| GAP-INAD-6 | INADIMPLENCIA-001 | medium | impl | `prepare_dossier` (Fernando) only publishes to internal topic with no wired consumer → dossier never assembled | ADR-0005 | m |
| GAP-CRED-4 | CRED-001 | medium | bl | GW_Natureza default routes cred_route ANALISE_HUMANA catch-all into credenciamento-only branch, skipping RN567 prior-notice | ADR-0012 | m |
| GAP-CRED-5 | CRED-001 | medium | bl | `decisao_cred=SOLICITAR_INFO` has no dedicated gateway branch; routed as MANTER → wrong desfecho | — | m |
| GAP-ESC-5 | ESCALATION-001 | medium | bl | Only ST_NotificarTime has a boundary error catch; 6 other serviceTasks (publish/notify) have none | — | m |
| GAP-PROG-4 | PROGRAMA-001 | medium | bl | No producer/correlator of `msg.programa.consent_revoked` from DSR revogacao_consentimento → choreography missing | ADR-0003 | m |
| GAP-TRIAGE-3 | HELENA-TRIAGE | medium | impl | `respond()` episodic memory write payload shape mismatches MemoryServer._store kwargs → KeyError | ADR-0002 | m |
| GAP-XPHI-3 | CROSS | medium | bl | WhatsApp webhook hashes phone but publishes raw `message_body` on inbound topic; DLQ republishes untouched raw payload | ADR-0006 | m |
| GAP-XHITL-3 | CROSS | medium | bl | Coverage-denial effect is L0-hard authorization_denial in AUTH but lowerable L1 nip_response in NIP (extends GAP-NIP-3) | ADR-0008 | m |
| GAP-XOBS-6 | CROSS | medium | bl | console hardcodes `_AUTH_CANDIDATE_GROUPS`; pending-task gauge/alert blind to 14 other processes' candidate groups | ADR-0010 | m |
| GAP-ADEQ-4 | ADEQUACAO-001 | medium | impl | `prepare_remediation_dossier` claims Andre A2A analytics.population but delegation maps it to PAGTO-only pagto_dossier flow | ADR-0003 | m |
| GAP-XHITL-4 | CROSS | medium | impl | `agents.events.process_completed` (universal agent-resume leg) has zero subscribers → escalation is one-way for all 8 agents (generalizes GAP-ESC-1/GAP-TRIAGE-4) | ADR-0003/0015 | l |

### Low (18)

| Gap ID | Process | Sev | Dim | Summary | ADR | Cx |
|---|---|---|---|---|---|---|
| GAP-CANCEL-6 | CANCEL-001 | low | impl | ST_PublishMantido event_payload_vars omits `responsavel_id` (siblings include it) | ADR-0007 | xs |
| GAP-CONTAS-6 | CONTAS-001 | low | bl | Contract states hitPolicy UNIQUE for 3 glosa DMNs; implemented FIRST (justified inline) — contract text drift | — | xs |
| GAP-FRAUDE-6 | FRAUDE-001 | low | bl | Contract fraude_sla description omits `sla_diligencia` though DMN/timer/worker use it | — | xs |
| GAP-REEMBOLSO-6 | REEMBOLSO-001 | low | impl | UT_CoordenacaoReembolso docs claim `decisao_coordenacao`; no gateway reads it, absent from contract outputs | — | xs |
| GAP-RECURSO-4 | RECURSO-001 | low | bl | `resposta_operadora` (drives GW_RecursoResolvido) never declared in contract Variáveis tables | — | xs |
| GAP-INAD-7 | INADIMPLENCIA-001 | low | bl | Contract says inadimplencia_purga/sla UNIQUE; DMNs correctly use FIRST — contract text drift | — | xs |
| GAP-LGPD-5 | LGPD-DSR-001 | low | bl | DMN `sla_resposta` (always P15D) never consumed; ESP_SlaGlobal hardcodes P15D → drift risk | ADR-0012 | xs |
| GAP-ADEQ-7 | ADEQUACAO-001 | low | bl | adequacao.py docstring claims fallback L1 "não rebaixável" but absent from `_hard_frozen.yaml` → overclaim | ADR-0008 | xs |
| GAP-ADEQ-8 | ADEQUACAO-001 | low | impl | `network_change_ref` (CRED-001 correlation) never read by any task/worker/payload → dead traceability field | — | xs |
| GAP-PAGTO-6 | PAGTO-001 | low | impl | `pagto_alcada.dmn` DENTRO_TETO_L2 emits `grupo_aprovador="clerical-pagamentos"` not in contract allowlist | — | xs |
| GAP-PAGTO-7 | PAGTO-001 | low | impl | Andre `_contract_variables` omits mandatory `data_vencimento`, writes several undeclared vars | — | xs |
| GAP-PROG-6 | PROGRAMA-001 | low | bl | `programa_sla.dmn` single input `programa_id`; contract declares `{programa_id,elegivel_programa}` → SLA can't differ by eligibility | ADR-0012 | xs |
| GAP-ANS-5 | ANS-SUBMIT-001 | low | bl | `lgpd_anonimizado` marked "pré-resolvido por worker" but no serviceTask computes/echoes it | — | xs |
| GAP-XOBS-7 | CROSS | low | impl | `AuditRecord.dmn_versions` always `{}` in prod; superseded by wired `decision_basis.dmn_refs` → dead duplicate plumbing | ADR-0007 | xs |
| GAP-ESC-2 | ESCALATION-001 | low | impl | `notify_team` echoes flat `grupo_atendimento`/`sla_ack` defaults (phase0.py:218,220) into the notification event payload instead of the DMN-decided `roteamento` values; real routing correctly uses `${roteamento.grupo_atendimento}` (BPMN:97)/timer `${roteamento.sla_ack}` (BPMN:115) — misleading audit/notification data only | — | xs |
| GAP-NIP-5 | NIP-001 | low | bl | Contract says info-wait timeout returns to elaboracao/coordenacao; BPMN routes unconditionally to UT_RevisaoJuridicaNip | — | s |
| GAP-NIP-6 | NIP-001 | low | impl | `Error_NipProtocoloInvalido` declared but no worker validates/throws it → dead | — | s |
| GAP-LGPD-6 | LGPD-DSR-001 | low | bl | `Error_LgpdIdentidade` declared, no boundary catch (contract flags deferred to FINAL — known-pending) | — | s |

---

## 4.3 Improvement Plan — Wave Structure

**Wave 0 — Critical blockers (minimum viable set gating any production tenant).** 13 gaps. These are engine deadlocks, adverse-outcome-without-human/audit, PHI leaks on mainline paths, a broken governance sign-off gate, orphaned regulatory handoffs, and Helena's per-turn crash. Two intra-wave orderings are declared (not cross-wave): `GAP-XOBS-2` (SLA-breach emission) is authored after `GAP-XOBS-1` (workers registered) so its poller/handlers have live workers to instrument; `GAP-XPHI-1`'s systemic module supersedes the targeted `GAP-AUTH-1` patch, so land `GAP-AUTH-1` first as a scoped patch and let `GAP-XPHI-1` (Wave 1) generalize it.

Wave 0 members: GAP-TRIAGE-2 (xs), GAP-ADEQ-1 (s), GAP-REEMBOLSO-1 (s), GAP-AUTH-1 (s), GAP-INAD-2 (s, handled inside the GAP-XPROC-1 brief), GAP-TRIAGE-1 (m), GAP-INAD-1 (m), GAP-CRED-1 (m), GAP-XPROC-1 (m), GAP-XOBS-1 (m), GAP-XOBS-2 (m, after GAP-XOBS-1), GAP-PAGTO-1 (l), GAP-LGPD-1 (l). *(13 criticals: the two `l` are PAGTO-1 and LGPD-1; GAP-AUTH-2 downgraded to high → Wave 1; TRIAGE-3 is medium and sits in Wave 2.)*

**Wave 1 — High severity, independent, parallelizable, grouped by complexity.** 43 gaps. All independent of Wave 0 except the declared cross-refs below. Batch order xs → s → m → l.
- *xs batch (parallel, T1/T2):* GAP-AUTH-5, GAP-CRED-2, GAP-INAD-3, GAP-LGPD-4.
- *s batch (parallel, T2):* GAP-CANCEL-1, GAP-REEMBOLSO-2, GAP-CONTAS-3, GAP-FRAUDE-1, GAP-AUTH-7 (patch site; GAP-XPHI-2 generalizes), GAP-REEMBOLSO-3 (subsumed by GAP-XHITL-2), GAP-LGPD-3, GAP-PAGTO-2, GAP-PAGTO-4.
- *m batch (parallel, T2/T3):* GAP-XPHI-1 *(declared dep: Wave 0 GAP-AUTH-1 patch)*, GAP-XPHI-2, GAP-XHITL-2 *(absorbs GAP-AUTH-4, GAP-REEMBOLSO-3, GAP-PAGTO-3)*, GAP-AUTH-4 (absorbed by GAP-XHITL-2 brief), GAP-PAGTO-3 (absorbed by GAP-XHITL-2 brief), GAP-CONTAS-2, GAP-FRAUDE-2, GAP-CANCEL-3, GAP-RECURSO-1, GAP-LGPD-2 *(declared dep: Wave 0 GAP-LGPD-1 provides lgpd.py)*, GAP-CRED-3, GAP-ADEQ-2, GAP-ADEQ-3, GAP-ESC-1, GAP-AUTH-2, GAP-PAGTO-8, GAP-PROG-1, GAP-PROG-2, GAP-ANS-1, GAP-ANS-2, GAP-TRIAGE-5, GAP-XOBS-3 *(declared dep: Wave 0 GAP-XOBS-1)*, GAP-XOBS-4, GAP-XPROC-2.
- *l batch (T2/T3):* GAP-AUTH-3, GAP-CONTAS-1, GAP-CONTAS-4, GAP-FRAUDE-3, GAP-NIP-1, GAP-TRIAGE-4 *(generalized by Wave 2 GAP-XHITL-4)*.

**Wave 2 — Medium + refactors, parallel batches with declared Wave 1 dependencies.** 41 gaps.
- Independent xs/s (parallel, T1/T2): GAP-AUTH-6, GAP-FRAUDE-4, GAP-RECURSO-2, GAP-ADEQ-6, GAP-FRAUDE-5, GAP-ADEQ-5 *(absorbed by Wave-0 GAP-XOBS-1 brief — verification only)*, GAP-CANCEL-2, GAP-CANCEL-4, GAP-CANCEL-5, GAP-CANCEL-7, GAP-CONTAS-5, GAP-NIP-2, GAP-INAD-4, GAP-INAD-5, GAP-CRED-6, GAP-ESC-3 *(dep: GAP-XPHI-1)*, GAP-ESC-4, GAP-PAGTO-5, GAP-PROG-3, GAP-PROG-7, GAP-ANS-3, GAP-ANS-4, GAP-XPROC-3, GAP-XOBS-5, GAP-RECURSO-3, GAP-PROG-5.
- m batch (T2/T3): GAP-NIP-3 *(pairs with GAP-XHITL-3)*, GAP-NIP-4, GAP-REEMBOLSO-4, GAP-REEMBOLSO-5, GAP-INAD-6, GAP-CRED-4, GAP-CRED-5, GAP-ESC-5, GAP-PROG-4, GAP-TRIAGE-3, GAP-XPHI-3, GAP-XHITL-3, GAP-XHITL-4 *(supersedes GAP-ESC-1/GAP-TRIAGE-4 resume leg)*, GAP-XOBS-6, GAP-ADEQ-4.

**Wave 3 — Low / polish, fully parallelizable at T1.** 18 gaps: GAP-CANCEL-6, GAP-CONTAS-6, GAP-FRAUDE-6, GAP-NIP-5, GAP-NIP-6, GAP-REEMBOLSO-6, GAP-RECURSO-4, GAP-INAD-7, GAP-LGPD-5, GAP-LGPD-6, GAP-ADEQ-7, GAP-ADEQ-8, GAP-PAGTO-6, GAP-PAGTO-7, GAP-PROG-6, GAP-ANS-5, GAP-XOBS-7, GAP-ESC-2.

**Wave counts:** Wave 0 = 13, Wave 1 = 43, Wave 2 = 41, Wave 3 = 18.

---

## 4.4 Agent Briefs

> All briefs are self-contained. Clinical/regulatory CONTENT briefs must mark the artifact `status: DRAFT — requires human review (médico auditor / jurídico)` and register it in `docs/review-queue.md`. Forbidden: automating negativa/clinical/fraud-accusation decisions; new business rules in Python if-chains (use DMN); regulatory timers outside BPMN; autonomy levels outside `src/maezo/policies/autonomy/`.

### WAVE 0 BRIEFS

**Agent:** fix-triage-dmn-payload
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/agents/helena/graph.py`
**Task:** GAP-TRIAGE-2. `_evaluate_dmn()` at `graph.py:560-563` calls `mcp-dmn.evaluate` with payload `{"table": table, "input": dmn_input}`, but `DmnServer._handler` requires kwargs `decision_id` and `variables` (every other agent passes those). Change the call to `{"decision_id": table, "variables": dmn_input}` matching rafael/andre/carolina/fernando/gustavo/marina/lucas graphs.
**Acceptance criteria:** Helena classify path evaluates its DMN without KeyError; add/repair a unit test asserting the payload keys are `decision_id`/`variables`; existing Helena tests green.
**Dependencies:** none.

**Agent:** fix-triage-fhir-payload
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/agents/helena/graph.py`
**Task:** GAP-TRIAGE-1. `identify()` at `graph.py:212-224` calls `mcp-fhir.read_patient_summary` with payload key `beneficiario_pseudo_id` (line 218), but the registered handler requires kwarg `patient_id` → KeyError on the FIRST tool call every turn, short-circuiting classify()/DMN. Rename the payload key to `patient_id` (value stays the pseudonymized id), matching the handler contract. Verify no other call site in the graph reuses the wrong key.
**Acceptance criteria:** identify() returns a patient summary without KeyError; classify() runs afterward; unit test asserts the payload key; Helena integration path green.
**Dependencies:** none.

**Agent:** fix-adequacao-fallback-tier
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/tools/workers/adequacao.py`, `src/maezo/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`, `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`
**Task:** GAP-ADEQ-1. `register_fallback_commitment` at `adequacao.py:495-518` includes `('tier', tier)` in its missing-field guard, but no DMN/gateway/UserTask ever sets `tier`; UT_DecisaoFallback (`BPMN:250-265`) only collects tipo_fallback/justificativa_fallback/referencia_regulatoria/responsavel_id, and the contract Variáveis de saída (`:110-122`) never declares `tier`. Every legitimate COMPROMISSO_FALLBACK therefore raises ERR_FALLBACK_COMMITMENT_NOT_HUMAN with no boundary catch → stuck. Remove `tier` from the guard (it is not part of this decision's collected fields); keep the existing decision + required-fields + audit-trail guards intact (ADR-0018). Do NOT add `tier` collection to the UT unless the contract is updated to declare it.
**Acceptance criteria:** a well-formed human COMPROMISSO_FALLBACK completes register_fallback_commitment without raising; the ADR-0018 no-auto-fallback invariant still holds (adverse still requires the human UT + audit trail); real-engine test proving End_CompromissoFallbackHumano reachable only via UT (create per GAP-ADEQ-2).
**Dependencies:** none.

**Agent:** fix-reembolso-signoff-embedded-dmn
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/platform/validation/signoff.py`
**Task:** GAP-REEMBOLSO-1. `promotable_artifacts()` at `signoff.py:132` resolves `decisionRef` to `dmn_dir / f"{ref}.dmn"` (one file per decision id). But `reembolso_admissibility` and `reembolso_auto_approval` are additional `<decision>` elements co-located inside `reembolso_coverage.dmn`, so the sign-off gate never finds/governs them — the DMNs gating human-vs-auto routing and the entire L2 auto-approval decision are ungoverned. Change resolution to index every `<decision id=...>` across ALL `*.dmn` files in `dmn_dir` (parse each file, map decision id → containing file) rather than assuming `{ref}.dmn`. Return the containing file for any referenced decision id.
**Acceptance criteria:** `promotable_artifacts()` resolves `reembolso_admissibility`/`reembolso_auto_approval` to `reembolso_coverage.dmn`; unit test with a multi-decision DMN file asserts all decision ids resolve; existing single-decision resolution unchanged; sign-off gate now covers the previously-invisible decisions.
**Dependencies:** none.

**Agent:** patch-auth-publish-negada-phi
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`, `src/maezo/tools/workers/phase0.py`
**Task:** GAP-AUTH-1 (scoped Wave 0 patch; GAP-XPHI-1 generalizes later). ST_PublishNegada (`BPMN:319`) sets `event_payload_vars="tenant_id,numero_guia_tiss,justificativa_clinica"` into the generic `operadora.events.publish` worker (`phase0.py:175-177`), which copies the raw process variable verbatim onto `agents.events.auth.completed` with zero pseudonymization — broadcasting free-text clinical justification (ADR-0006 PHI) to Zona Geral. Immediate fix: remove `justificativa_clinica` (and any PHI-named var) from ST_PublishNegada's `event_payload_vars`; carry only `tenant_id,numero_guia_tiss` plus a `payload_ref`/business_key so a PHI-zone consumer resolves clinical content from the engine/store (matching `a2a/facts.py` "O fato NUNCA carrega PHI"). Do not alter the denial decision path.
**Acceptance criteria:** `agents.events.auth.completed` payload contains no `justificativa_clinica`/clinical free text; test asserts PHI-named vars absent from the published fact; denial flow otherwise unchanged.
**Dependencies:** none. (GAP-XPHI-1 in Wave 1 introduces the reusable `phi_vars.py` seam and supersedes this patch.)

**Agent:** fix-inad-double-termination-guard
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/inadimplencia.py`
**Task:** GAP-INAD-1. `ja_em_rescisao_cancel` is the anti-double-termination guard, but `make_resolve_facts_handler` (`inadimplencia.py:184-228`) only echoes `vars_.get("ja_em_rescisao_cancel")` (an optional, usually-absent input), and the guard at `374-384` trusts that echoed value — so `register_contract_suspension`'s defense-in-depth check can never actually detect an in-flight CANCEL rescission. Replace the echo with a real cross-process query: in resolve_facts, query the engine (mcp-cibseven) for an active SP-OP-CANCEL-001 instance for the same `numero_contrato`/tenant (business-key lookup, mirroring `find_active_instance`) and set `ja_em_rescisao_cancel` from that result. Keep the existing typed-error guard (ADR-0018). Fail closed: if the query cannot be performed, treat as `true` (block) and route to human.
**Acceptance criteria:** with an active CANCEL rescission present, register_contract_suspension's guard fires; with none, suspension proceeds; unit/integration test drives both; anti-double-adverse-effect topology invariant preserved.
**Dependencies:** none.

**Agent:** fix-cred-error-boundaries
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`
**Task:** GAP-CRED-1. Three `bpmn:error` elements are declared (`Error_CredPrestadorInvalido`, `Error_DecredNotHuman`, `Error_CredDenialNotHuman`, decls `:15-17`) but the file contains zero `<errorEventDefinition>`/boundary-error events. ST_VerifyCredentials (`:109-115`) is the first business task after start and unconditionally throws Error_CredPrestadorInvalido on invalid input → stuck incident on the mainline. Add boundary error events for all three: on ST_VerifyCredentials (Error_CredPrestadorInvalido → a validation-reject end), on ST_RegisterDescredenciamento (`:320-325`, Error_DecredNotHuman) and ST_RegisterCredDenial (`:484-489`, Error_CredDenialNotHuman) → their respective human-required/incident ends. Preserve the ADR-0018 no-denial guards (the errors remain the enforcement mechanism; they simply now have catch paths).
**Acceptance criteria:** each of the 3 declared errors has a boundary catch; an invalid credential input no longer stalls as an unhandled incident; real-engine sweep (GAP-CRED-3 test-spec) proves adverse ends unreachable without a human UT and that error paths terminate cleanly.
**Dependencies:** none.

**Agent:** fix-notifications-bridge-handoff-fanout
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/platform/integrations/notifications_bridge/consumer.py`, `src/maezo/tools/workers/fraude.py`, `src/maezo/tools/workers/adequacao.py`, `tests/integration/processes/test_cross_process_handoff_seam.py`, `tests/unit/platform/integrations/test_notifications_bridge.py`
**Task:** GAP-XPROC-1 (absorbs GAP-INAD-2 and GAP-XHITL-1). `notifications_bridge` ACTIONABLE_TYPES (`consumer.py:73`) dispatches only `contas.start_recurso` and `nip.handoff_ans_submit`; the adverse-referral starters `fraude.start_credenciamento` (`fraude.py:748-782`), `fraude.start_contratual` (`fraude.py:785-815`, missing entidade_tipo/beneficiario_pseudo_id), `adequacao.start_credenciamento` (`adequacao.py:318-336`), and `inadimplencia.handoff_rescisao` all hit `plan_start()->None` (NO-OP) → human-confirmed corrective actions orphan. (1) In fraude.py's two start handlers add `entidade_tipo`, `beneficiario_pseudo_id` (contratual only), and a deterministic downstream business_key (`cred_business_key=f"CRED-{tenant_id}-{prestador_id}"`; `cancel_business_key=f"CANCEL-{tenant_id}-{numero_contrato}"` / `inad_business_key` selected by entidade_tipo) plus an explicit `target_process_key`. (2) In adequacao.py add `cred_business_key` from prestador/regiao. (3) Extend ACTIONABLE_TYPES + plan_start() with 4 branches (targets already in KNOWN_PROCESS_KEYS): fraude.start_credenciamento + adequacao.start_credenciamento → SP-OP-CRED-001; fraude.start_contratual → SP-OP-CANCEL-001 or SP-OP-INADIMPLENCIA-001 keyed on entidade_tipo; inadimplencia.handoff_rescisao → SP-OP-CANCEL-001. Add identity-only variable builders mirroring `recurso_variables()`/`ans_submit_variables()`. Handoffs are event-choreographed Kafka facts, NOT callActivity (ADR-0003/0015).
**Acceptance criteria:** unit tests assert the 4 types now dispatch (mirror test_recurso_dispatch); real-engine seam tests prove FRAUDE→CRED, FRAUDE→CANCEL/INADIMPLENCIA, ADEQUACAO→CRED each START the downstream process and halt at its own human UserTask; each producer emits a deterministic business_key.
**Dependencies:** none.

**Agent:** wire-worker-modules-and-audit
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/worker_runtime/service.py`
**Task:** GAP-XOBS-1 (absorbs GAP-ADEQ-5). `_register_all_workers` (`service.py:146-163`) wires only phase0/auth/contas/recurso/nip/cancel/reembolso/ans_submit; `register_adequacao_workers`, `register_cred_workers`, `register_fraude_workers`, `register_inadimplencia_workers`, `register_pagto_workers`, `register_programa_workers` are never imported/called → those 6 processes deadlock at their first serviceTask, while `/readyz` (`build_readiness_checks` workers_registered, `service.py:121-130`) only checks `len(registered_topics) > 0`. (1) Import and call the 6 missing `register_*_workers` mirroring existing call sites. (2) In `_bring_up_dependencies` STEP B construct `AuditLog([JsonlFileSink(path), KafkaSink(kafka)])` and pass `audit=` to register_fraude_workers/register_pagto_workers/register_programa_workers (each module's docstring documents this exact wiring, ADR-0007). (3) Strengthen the workers_registered readiness check to assert the full expected topic count, not `> 0`.
**Acceptance criteria:** all 15 modules' topics registered at bootstrap; readiness fails if any expected topic is missing; a real instance of each of the 6 previously-dead processes advances past its first serviceTask; AuditLog constructed and threaded to the 3 custody-chain modules.
**Dependencies:** none. (Wave 1 GAP-XOBS-3 and Wave 0 GAP-XOBS-2 declare a dependency on this brief.)

**Agent:** wire-sla-breach-metrics-and-poller
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/worker_runtime/engine_metrics.py` (new), `src/maezo/runtime/worker_runtime/service.py`, `src/maezo/tools/workers/*.py` (SLA boundary handlers)
**Task:** GAP-XOBS-2. `inc_user_task_sla_breach`/`set_process_instance_active`/`inc_process_instance_completed`/`record_user_task_age` (`metrics.py:687-739`) are called from zero production paths, so the named critical alert `MaezoUserTaskSLABreach` (RN259 / Lei 9.656/98 art. 35-C, `alert-rules.yaml:121-135`) can never fire and 4/5 `engine.json` panels stay blank. (1) Wire `metrics.inc_user_task_sla_breach(tenant=, task_name=<BT/UT element id>, breach_phase='ack'|'resolution', priority=<P1|P2|P3 from each process's *_sla.dmn>)` and `record_user_task_age(...)` into the serviceTask handlers the non-interrupting SLA boundary timers route to, defensively (try/except, never raise into the handler main path — mirror nip.py `_emit_deadline_risk`). (2) Add a bounded non-fatal reconciliation poller `engine_metrics.py` on the existing `poll_interval_ms` cadence querying CIB Seven `/process-instance/count` and `/history/process-instance/count` per process_key, calling `set_process_instance_active`/`inc_process_instance_completed`. Wire it into service.py bootstrap.
**Acceptance criteria:** a simulated SLA breach increments `cibseven_user_task_sla_breach_total` with the canonical labels the alert reads; poller populates the active/completed gauges; MaezoUserTaskSLABreach fires in a test harness; no handler regression.
**Dependencies:** GAP-XOBS-1 (workers must be registered so the SLA handlers run).

**Agent:** author-pagto-admissibility-dmn
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/processes/dmn/pagto_admissibility.dmn` (new), `src/maezo/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn`, `config/artifact_signoff.yaml`, `docs/review-queue.md`
**Task:** GAP-PAGTO-1. The contract (`SP-OP-PAGTO-001.md:123`) declares a `pagto_admissibility` DMN gating `dados_pagamento_validos`/`lastro_confirmado`/`duplicidade_suspeita` before alçada routing, but no such file exists and GW_Faixa routes purely on `pagto_alcada.faixa_valor`. Author `pagto_admissibility.dmn` (hitPolicy FIRST, typed io, mandatory FAIL-SAFE catch-all → ANALISE_HUMANA, ADR-0012) taking those 3 boolean inputs and outputting an admissibility decision (e.g. `admissivel` / route to human on suspicion or missing lastro). Wire a businessRuleTask between ST_CalculateFacts (`BPMN:187`) and BRT_AlcadaRouting (`:108`) with `decisionRef="pagto_admissibility"`, promoting the output via `camunda:outputParameter` to a top-level var (avoid ENGINE-16004). Add to `artifact_signoff.yaml` (new DMN) and, since this encodes lastro/duplicity regulatory logic, mark `status: DRAFT — requires human review (jurídico/financeiro)` and register in `docs/review-queue.md`.
**Acceptance criteria:** DMN deploys on the real engine; a payment with duplicidade_suspeita=true or lastro_confirmado=false routes to human, never auto-release; catch-all covers unmapped input; sign-off gate recognizes the new table; real-engine test asserts the gate precedes alçada routing.
**Dependencies:** none.

**Agent:** author-lgpd-workers
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/lgpd.py` (new), `src/maezo/runtime/worker_runtime/service.py`, `docs/review-queue.md`
**Task:** GAP-LGPD-1. No `workers/lgpd.py` exists; none of the 6 DSR external-task topics (verify_identity, request_additional_proof, compile_data_package, execute_request, send_response, notify_sla_risk) plus notify_juridico_breach has a worker, and `_register_all_workers` never registers one → every instance deadlocks on ST_VerificarIdentidade (BPMN:65, first mainline task). Create `lgpd.py` mirroring the uniform pattern in contas.py/cancel.py/programa.py: `make_verify_identity_handler` (raises `ERR_DSR_IDENTITY_UNVERIFIED` on failure), `make_request_additional_proof_handler`, `make_compile_data_package_handler`, `make_execute_request_handler`, `make_send_response_handler`, `make_notify_sla_risk_handler`, `make_notify_juridico_breach_handler`, each emitting ADR-0010 structured events and pseudonymized payloads only (titular_pseudo_id). Add `register_lgpd_workers(harness, kafka)` and wire it into `_register_all_workers`. Do NOT automate any NEGAR/release decision (those stay human-UT per ADR-0005). Mark any legal response text DRAFT and register in `docs/review-queue.md` (already listed).
**Acceptance criteria:** all 7 topics are served; a DSR instance advances past ST_VerificarIdentidade; ERR_DSR_IDENTITY_UNVERIFIED raised on unverifiable identity; real-engine test drives verify→compile→send happy path and the identity-fail path; no PHI in any published payload.
**Dependencies:** none. (Wave 1 GAP-LGPD-2 depends on this module.)

### WAVE 1 BRIEFS (high severity)

**Agent:** fix-auth-denial-boundary-and-formdata
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`, `src/maezo/tools/workers/auth.py`
**Task:** GAP-AUTH-2. NOTE: console-side field enforcement already exists — the médico-auditor console rejects an incomplete NEGAR server-side (`schemas.py build_complete_variables:204-213` raises; `app.py complete_task` maps ValueError→HTTP 422 and requires a human principal + signing credential). This brief adds the missing engine-level error-boundary catches + formData as defense-in-depth for the direct-engine-REST bypass path (not a live bypass). (1) `ERR_AUTH_DENIAL_INCOMPLETE` is thrown by the worker (`auth.py:308-313`) but is undeclared in the BPMN and has no boundary catch; add the `bpmn:error` declaration and a boundaryEvent on ST_EnviarNegativaFormal (`BPMN:307-312`) routing to a corrective/incident end so an incomplete denial does not become a stuck job. (2) UT_AnaliseMedicoAuditor (`BPMN:220-231`) has no `camunda:formData` enforcing NEGAR's 3 required fields, so a médico auditor completing directly against the engine could complete NEGAR incomplete. Add `camunda:formData` with validation constraints requiring the 3 NEGAR fields (justificativa_clinica, cid10_referencia, auditor_id per contract) when the decision is NEGAR. This is HITL-structural (ADR-0005/0018); do not automate the decision. Mark form-content changes touching clinical fields DRAFT and register in `docs/review-queue.md`.
**Acceptance criteria:** NEGAR cannot be completed with any of the 3 fields blank; ERR_AUTH_DENIAL_INCOMPLETE is caught by a boundary event (no unhandled incident); real-engine test confirms NEGAR-without-fields is rejected and no adverse end reached without a complete human UT.
**Dependencies:** none.

**Agent:** phi-worker-egress-seam
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/phi_vars.py` (new), `src/maezo/tools/workers/phase0.py`, `src/maezo/tools/workers/auth.py`, `src/maezo/platform/validation/` (validate-artifacts lint), `src/maezo/tools/mcp_cibseven/server.py`
**Task:** GAP-XPHI-1 (absorbs GAP-AUTH-1 systemic, GAP-ESC-3). Close the worker→Kafka egress seam the way ADR-0016 closed the tool seam. (a) Add `phi_vars.py` with `frozenset PHI_PROCESS_VARS = {justificativa_clinica, cid10_referencia, fundamentacao_dut, notas_resolucao, resumo_contexto, matricula_beneficiario, laudo, diagnostico}` (inverse of the `*_pseudo_id` safe convention). (b) Inject the tenant `Pseudonymizer` into `register_phase0_workers` and each `register_*_workers`; in `make_publish_event_handler` (after `phase0.py:177`) and in `auth.py:315-329 send_denial_notice`, run any value whose key ∈ PHI_PROCESS_VARS through `Pseudonymizer.pseudonymize_text` before `kafka.publish`. (c) Preferred structural control: add a validate-artifacts lint gate forbidding PHI_PROCESS_VARS names inside `event_payload_vars` (mirror topic_registry checks); carry them by `payload_ref`/business_key so only a PHI-zone consumer resolves them (matching `a2a/facts.py`). (d) Once clinical vars are no longer raw, correct the `mcp_cibseven` PHI_FIELDS=[] invariant comment (`server.py:30,345`).
**Acceptance criteria:** no PHI_PROCESS_VARS value leaves any worker onto Kafka unpseudonymized; lint gate fails a BPMN that puts a PHI-named var in event_payload_vars; ESCALATION `notas_resolucao` and AUTH clinical vars verified scrubbed; tests cover phase0 publish + auth denial notice paths.
**Dependencies:** Wave 0 GAP-AUTH-1 (this brief supersedes that scoped patch).

**Agent:** phi-structlog-processor
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/log_phi.py` (new), `src/maezo/runtime/worker_runtime/__main__.py`, `src/maezo/runtime/agent_runtime/__main__.py`, `src/maezo/runtime/metrics.py`, `src/maezo/tools/workers/auth.py`
**Task:** GAP-XPHI-2 (with GAP-AUTH-7 as the concrete leak). No app-layer structlog PHI processor exists — both `structlog.configure()` calls (`worker_runtime/__main__.py:18-20`, `agent_runtime/__main__.py:18-20`) set only a level filter; the span deny-list (`metrics.py:170-196`) omits every clinical field name; and `auth.py:340-348` logs `cid10_referencia` (ICD-10 PHI) cleartext on every NEGAR. (1) Add `log_phi.py` with a structlog processor that drops/tokenizes any event-dict key in a shared PHI-key denylist and runs remaining free-text string values through `Pseudonymizer.pseudonymize_text`; wire it as the FIRST processor in both `structlog.configure()` calls. (2) Seed the denylist from a single shared constant reused by `metrics.PHI_FORBIDDEN_ATTRIBUTE_NAMES`, extended with cid10, cid10_referencia, diagnostico, justificativa_clinica, fundamentacao_dut, cns, matricula_beneficiario, endereco, birthdate, laudo, notas_resolucao, resumo_contexto. (3) Remove `cid10_referencia=` from the `logger.warning` at `auth.py:345` (log a hashed guia reference only) — this is GAP-AUTH-7.
**Acceptance criteria:** a `logger.warning(cid10_referencia=...)` no longer emits the raw value at either runtime; span deny-list and structlog denylist share one constant; auth denial log carries only a hashed guia ref; unit test asserts the processor tokenizes a seeded PHI key.
**Dependencies:** none. (GAP-AUTH-7 fully resolved here.)

**Agent:** enforce-financial-ceilings
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/auth_analyze.py`, `src/maezo/tools/workers/reembolso.py`, `src/maezo/tools/workers/pagto.py`, `src/maezo/processes/dmn/pagto_alcada.dmn`, `src/maezo/policies/autonomy/L0-core.yaml`, `src/maezo/policies/autonomy/tenants-amh.yaml`, `docs/review-queue.md`
**Task:** GAP-XHITL-2 (absorbs GAP-AUTH-4, GAP-REEMBOLSO-3, GAP-PAGTO-3). The financial autonomy ceilings are decorative: `authorization_approval.max_value_brl` (`tenants-amh.yaml:6`, currently 0 = D-07) and `high_value_payment.threshold_brl=100000` (`L0-core.yaml:14`) are read by zero Python; `dentro_teto_l2` is defaulted False and echoed from process vars in AUTH (`auth_analyze.py:96,110,171`) and REEMBOLSO (`reembolso.py:255,276`); PAGTO's escada is a hardcoded dict (`_FAIXA_TIER_MINIMO pagto.py:120-127`). Add a deterministic ceiling-resolution step that computes `dentro_teto_l2` (AUTH/REEMBOLSO) and the minimum-approver tier (PAGTO) by comparing the request value against the tenant governance ceiling loaded via `gateway.pep.load_matrix` — feed the COMPUTED boolean/tier into the auto-approval DMN as an input rather than trusting an inbound boolean. Move `_FAIXA_TIER_MINIMO` into `pagto_alcada.dmn` (ADR-0012). Add a `reembolso_auto_approval` action to `L0-core.yaml`. Until D-07 sets `max_value_brl>0`, a resolved ceiling of 0 must force `dentro_teto_l2=false` so every AUTO_APROVAR route falls through to ANALISE_HUMANA. Mark autonomy-matrix edits per `_hard_frozen.yaml` header (CODEOWNERS review) and register in `docs/review-queue.md`.
**Acceptance criteria:** seeding `dentro_teto_l2=true` from upstream no longer bypasses the ceiling (value is recomputed); with ceiling=0 all AUTO_APROVAR routes go to human; pagto escada sourced from DMN not Python; tests cover AUTH/REEMBOLSO/PAGTO ceiling paths.
**Dependencies:** none. (GAP-AUTH-4, GAP-REEMBOLSO-3, GAP-PAGTO-3 resolved here.)

**Agent:** wire-worker-sla-metrics
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/tools/workers/adequacao.py`, `auth.py`, `contas.py`, `cred.py`, `inadimplencia.py`, `cancel.py`, `fraude.py`, `programa.py`, `pagto.py`, `recurso.py`, `reembolso.py`
**Task:** GAP-XOBS-3. 11 of 15 `notify_sla_risk`-class handlers never import `maezo.runtime.metrics`; only nip.py/ans_submit.py emit, via non-canonical instruments the `MaezoUserTaskSLABreach` alert can't read. Standardize every `notify_sla_risk`/`*_estourado` handler across these 11 modules on `metrics.inc_user_task_sla_breach(tenant=, task_name=<the BT_/UT_ element id firing>, breach_phase='ack'|'resolution', priority=<derived from each process's *_sla.dmn output>)`, wrapped in try/except that only logs on failure (template = nip.py `_emit_deadline_risk`). Keep NIP/ANS ad hoc counters as additive detail, not replacements.
**Acceptance criteria:** all 11 modules import metrics and emit the canonical counter with correct labels; a fired SLA handler increments `cibseven_user_task_sla_breach_total`; no handler raises on metric failure.
**Dependencies:** GAP-XOBS-1 (workers registered so handlers run).

**Agent:** worker-harness-observability
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/metrics.py`, `src/maezo/tools/workers/harness.py`, `src/maezo/runtime/worker_runtime/service.py`
**Task:** GAP-XOBS-4. `WorkerHarness._handle` (`harness.py:386-425`) emits zero span/counter on any of its 4 outcomes (complete/bpmn_error/failure) — only stdlib logging — and `worker_runtime/service.py` never calls `install_tracer_provider`, so worker traces are structurally impossible and no-denial-guard firings (ERR_*_NOT_HUMAN) produce only a WARNING with no counter/audit. (1) Add `WORKER_TASK_TOTAL` (Counter, labels tenant/topic/outcome∈{completed,bpmn_error,failed}) and `WORKER_TASK_DURATION_SECONDS` (Histogram) to metrics.py mirroring PEP_DECISION_TOTAL. (2) In `_handle` wrap `_execute_with_retry` in `agent_span('worker.external_task', topic=, task_id=, process_instance_id=, business_key=)` and call a defensive `record_worker_task_outcome(...)` in each outcome branch. (3) Call `install_tracer_provider(settings)` from `worker_runtime/service.py` STEP B exactly as agent_runtime does.
**Acceptance criteria:** worker external-task spans export; each outcome increments WORKER_TASK_TOTAL; a WorkerBpmnError firing is queryable as a counter; no functional regression in dispatch/retry.
**Dependencies:** none (independent of GAP-XOBS-1; instruments the harness itself).

**Agent:** cred-network-changed-consumer
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/platform/integrations/network_change_bridge/consumer.py` (new) OR `src/maezo/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn` + `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` + `docs/review-queue.md`, `tests/integration/processes/`
**Task:** GAP-XPROC-2. The CRED→ADEQUACAO choreography (`agents.events.cred.network_changed`) has no consumer; Start_AvaliacaoAdequacao (`BPMN:78`) is a plain none-start (no messageEventDefinition), contradicting its doc ("consumo via start message correlacionado por tenant_id+regiao_saude+especialidade"). Either (a) build `network_change_bridge/consumer.py` (mirror notifications_bridge) subscribing to `agents.events.cred.network_changed`, calling `mcp-cibseven.start_process(process_key=SP-OP-ADEQUACAO-001, business_key=f"ADEQ-{tenant_id}-{regiao_saude}-{especialidade}-{ciclo_avaliacao}", gatilho="mudanca_rede")` (deriving regiao_saude/especialidade from prestador_id via a lookup step); or (b) if geo-derivation is not yet feasible, mark the mudanca_rede trigger DRAFT/not-yet-wired in the BPMN doc + contract and register in `docs/review-queue.md`. Either way add a real-engine seam test that drives CRED to a ST_PublishNetwork* task and asserts an ADEQUACAO-001 instance is reachable (not just payload-shape).
**Acceptance criteria:** either an ADEQUACAO instance actually starts from a network_changed fact (option a) or the gap is explicitly marked DRAFT and queued (option b); seam test added.
**Dependencies:** none.

**Agent:** lgpd-erasure-cascade
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/lgpd.py`
**Task:** GAP-LGPD-2. The PR#72 erasure cascade (`mcp_memory.erase_by_patient` at `mcp_memory/server.py:616-636`; `platform.lifecycle.expurgo.erase_patient_working_layer`) has zero path from SP-OP-LGPD-DSR-001. In the ST_ExecutarRequisicao handler (`BPMN:173-178`, created in GAP-LGPD-1's lgpd.py), when `roteamento_dsr.fluxo == 'ELIMINACAO_AVALIACAO'` and the human decision `decisao_dsr == 'EXECUTAR_E_ENVIAR'`, call `mcp_memory.erase_by_patient` (episodic/semantic) and `expurgo.erase_patient_working_layer` (working layer) keyed by the `fhir_patient_id` resolved for `titular_pseudo_id`, recording the result in the signed audit trail (ADR-0007) before ST_EnviarResposta confirms. Erasure runs only after the human approval (ADR-0005); no automation of the decision.
**Acceptance criteria:** an approved ELIMINACAO_AVALIACAO/EXECUTAR_E_ENVIAR actually erases across memory + working layers; the erasure is audit-logged; a test drives the approved-erasure path and asserts both stores called.
**Dependencies:** GAP-LGPD-1 (provides lgpd.py + the handler).

**Agent:** lgpd-decision-fail-closed
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`
**Task:** GAP-LGPD-4 + GAP-LGPD-3. (LGPD-4) GW_DecisaoDsr (`:165-170`) has an unconditional default `Flow_GWDec_Enviar` that sends the compiled data package for ANY value not exactly EXECUTAR_E_ENVIAR/NEGAR_FUNDAMENTADO — including missing/blank → silent release. Change to fail-closed: make the send branch conditional on `decisao_dsr == 'APROVAR_ENVIO'` and add a default branch to an error end (`ERR_DSR_DECISION_INVALID`) for any other value. (LGPD-3) `NEGAR_FUNDAMENTADO exige fundamentacao_legal` is unenforced: add `camunda:formData` to UT_RevisaoDpo (`:136-147`) requiring non-empty `fundamentacao_legal` when `decisao_dsr == 'NEGAR_FUNDAMENTADO'`, or a guard gateway before `Flow_GWDec_Negar` (`:258-260`) that rejects completion when blank. HITL-structural; mark clinical/legal form fields DRAFT and register in `docs/review-queue.md`.
**Acceptance criteria:** an unset/blank decisao_dsr no longer releases data (incidents instead); NEGAR_FUNDAMENTADO with blank fundamentacao_legal is rejected; real-engine test covers both.
**Dependencies:** none.

**Agent:** helena-resume-and-coverage-tools
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/inbound_driver.py` OR a new resume consumer, `src/maezo/agents/helena/agent.yaml`
**Task:** GAP-TRIAGE-4 + GAP-TRIAGE-5 (TRIAGE-4 generalized by Wave 2 GAP-XHITL-4). (TRIAGE-4) ESCALATION End_DevolvidoAoAgente publishes `agents.events.process_completed` but nothing subscribes (only inbound_driver/notifications_bridge/fhir_sync consume) → Helena never resumes. Add a resume consumer mirroring inbound_driver's AIOKafkaConsumer + manual-commit, subscribing to `agents.events.process_completed`, re-driving the originating agent graph on the devolvido_agente branch keyed idempotently by conversation_id/business_key (ESC-{tenant}-{conv}); wire into agent_runtime bootstrap. (TRIAGE-5) `agent.yaml:15-21` tools allowlist omits `mcp-fhir.read_coverage`/`search_coverage` though prompts promise Patient/Coverage answers — add them to the allowlist.
**Acceptance criteria:** an ESCALATION devolvido_agente terminal re-enters the Helena conversation (integration test); Helena can call read_coverage/search_coverage; no duplicate-drive on redelivery.
**Dependencies:** none. (Wave 2 GAP-XHITL-4 may fold this consumer into the agent-agnostic version.)

**Agent:** wave1-xs-batch-declared-vars
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`, `docs/processes/contracts/SP-OP-FRAUDE-001.md`, `docs/processes/contracts/SP-OP-CRED-001.md`, `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
**Task:** Batch of xs high-severity contract/wiring fixes (independent, one PR): **GAP-AUTH-5** — add `numero_autorizacao` to ST_PublishAprovadaAuto (`:202`) and ST_PublishAprovadaAuditor (`:296`) event_payload_vars (already produced by ST_EmitirAutorizacao*). **GAP-CRED-2** — declare `tem_beneficiarios_vinculados` in the CRED contract Variáveis de entrada table (`:46-65`); it is required by `cred_prior_notice.dmn` and seeded by graph.py. **GAP-INAD-3** — remove the `tier` hard-requirement from `register_contract_suspension` OR add `tier` to the contract campos obrigatórios + UT output docs (choose remove unless tier is genuinely collected). (GAP-FRAUDE-5, the sibling `destino_referral_*` contract fix, is medium → handled in the Wave 2 batch.)
**Acceptance criteria:** each variable-contract mismatch resolved; consumers of numero_autorizacao/destino_referral_* see the values; unit tests updated.
**Dependencies:** none.

**Agent:** wave1-s-batch-dmn-and-boundaries
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/dmn/cancel_admissibility.dmn`, `src/maezo/processes/dmn/glosa_triage.dmn`, `src/maezo/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`, `src/maezo/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn`, `src/maezo/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`, `src/maezo/agents/valentina/graph.py`
**Task:** Batch of s high-severity fixes (independent): **GAP-CANCEL-1** — add `vinculo_ativo` as an input to `cancel_admissibility.dmn` (`:34-48`) and gate r_pedido_l2 (`:51-60`) so an inactive vínculo routes to ANALISE_HUMANA. **GAP-CONTAS-3** — add `categoria_normalizada` to r_sem_glosa (`glosa_triage.dmn:44-53`) so it no longer preempts r_tecnica_humano under hitPolicy FIRST. **GAP-REEMBOLSO-2** — add a boundary error catch on ST_CheckCoverage (`:84-89`) for `Error_ReembolsoProtocoloInvalido` (declared `:14`, thrown at `reembolso.py:152-155`). **GAP-PAGTO-4** — add a boundary catch on ST_ValidatePaymentData for `Error_PagtoOrdemInvalida` (declared line 15). **GAP-PAGTO-2** — add the contract terminal `End_PagamentoCancelado` (neutral: cancelled/duplicate/no-lastro) endEvent. **GAP-FRAUDE-1** — promote `intensidade_investigacao` to a top-level var via `camunda:outputParameter` after BRT_Indicadores (`:134-141`) so fraude_routing/fraude_sla resolve it (ENGINE-16004 avoidance). Mark any DMN clinical-content edits (cancel/glosa) DRAFT + register in `docs/review-queue.md`. (GAP-PROG-5, Valentina's `DMN_PROGRAMA_STRATIFICATION` → `programa_routing` fix, is medium → handled in the Wave 2 batch.)
**Acceptance criteria:** each DMN/boundary behaves per fix; no stuck-instance on the two invalid-input errors; PAGTO neutral terminal reachable; real-engine tests updated.
**Dependencies:** none.

**Agent:** auth-dut-dmn-consumers
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/agents/rafael/graph.py`, `src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`, `config/artifact_signoff.yaml`, `docs/review-queue.md`
**Task:** GAP-AUTH-3. `dut_rol_coverage`, `dut_criteria_bariatrica`, `dut_criteria_oncologia_pet_ct`, `dut_criteria_terapias_especiais` and `carencia_check` are documented (README.md, review-queue.md) as "SP-OP-AUTH-001 (via mcp-dmn)" consumers, but no code path invokes them: Rafael's graph (`rafael/graph.py:76-78`) references only DMN_ADMISSIBILITY/DMN_AUTO_APPROVAL/DMN_SLA, and `artifact_signoff.yaml:54-56` flags carencia_check as orphan (dut_* not even flagged). Wire these DMNs into the admissibility/coverage evaluation: add each `dut_*` and `carencia_check` decision to the mcp-dmn evaluation path in Rafael's assess node (or a dedicated businessRuleTask before UT_AnaliseMedicoAuditor), feeding the DUT-criteria and carência outputs as inputs to the existing routing — WITHOUT allowing any adverse/coverage decision to be produced by the agent (the DMNs inform, the human decides; ADR-0005/0018). These tables encode clinical coverage criteria, so mark each `status: DRAFT — requires human review (médico auditor)` and keep/refresh their entries in `docs/review-queue.md`; add them to `artifact_signoff.yaml` as intentional_draft.
**Acceptance criteria:** each of the 5 DMNs is invoked on the AUTH mainline (verified by a real-engine or graph test asserting the decision_ids are evaluated); no agent path produces a coverage denial; sign-off gate recognizes the tables; review-queue entries present.
**Dependencies:** none.

**Agent:** contas-real-computation-and-a2a
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/contas.py`, `src/maezo/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
**Task:** GAP-CONTAS-1 + GAP-CONTAS-2 + GAP-CONTAS-4 (grouped, same module). (CONTAS-1) `make_prepare_triage_dossier_handler` (`:262-301`) never invokes MarinaGraph or the A2A DelegationDispatcher — only an inert Kafka notification (test asserts "TEM handler real"). Invoke Marina via the A2A dispatcher (mirror the working delegation pattern) so UT_AnalistaContas opens on a real dossier. (CONTAS-2) `make_identify_glosa_handler` (`:125-162`) and `make_calculate_impact_handler` (`:218-257`) echo has_glosas/denial_ratio/divergencia_valor; make them read `linhas_conta_refs`/`reason_codes_tiss`/`valor_apresentado_brl` and actually compute the facts. (CONTAS-4) BT_SlaTriagem/BT_AlertaSlaContas (`BPMN:208-213`) anchor to UT creation; re-anchor the SLA timers to `data_recebimento_lote` (the contract âncora, currently never read) — model as BPMN timers reading the DMN/ingress-supplied timestamp, not a Python computation.
**Acceptance criteria:** dossier is a real Marina A2A call; glosa/impact computed from line-item inputs on an organically-started instance; SLA timers fire relative to data_recebimento_lote; integration tests cover an organic (non-echoed) instance.
**Dependencies:** none.

**Agent:** fraude-custody-and-scoring
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/tools/workers/fraude.py`, `src/maezo/agents/beatriz/graph.py`
**Task:** GAP-FRAUDE-2 + GAP-FRAUDE-3 (same module/agent). (FRAUDE-2) `make_register_fraud_accusation_handler` (`:605-631`) calls `verify_custody_bundle(candidate)` with `chain=None` and fabricates `candidate.sealed_record_hash = audit.head_hash`, so custody is never proven against the real ADR-0007 chain. Pass the real `chain=` (the process's audit chain) into `verify_custody_bundle` and stop fabricating the sealed hash — derive it from the actual sealed record. (FRAUDE-3) The 7 `fraude_scoring/*.dmn` tables have no real consumer: `make_score_indicators_handler` (`:320-367`) and `make_gather_evidence_handler` (`:278-301`) pass through/return None. Wire `score_indicators` to evaluate the fraude_scoring DMNs via mcp-dmn against real gathered evidence. Preserve the type-level agent adverse-exclusion in beatriz/graph.py (`:75-77`) — no automated accusation (ADR-0005/0018).
**Acceptance criteria:** custody verification uses the real chain and fails on tamper; score_indicators produces DMN-computed scores from evidence, not echoes; no path lets an agent produce an accusation; tests cover tamper + scoring.
**Dependencies:** none. (Note GAP-FRAUDE-4, medium, refines the error-code selection in the same guard — Wave 2.)

**Agent:** nip-sla-anchor
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn`
**Task:** GAP-NIP-1. All NIP boundary timers (BT_AlertaPrazoNip, BT_PrazoNipEstourado, BT_AlertaPrazoRevisao, BT_PrazoRevisaoEstourado, ICE_PrazoInfo, `:172-196,241-267,380-386`) use relative timeDuration anchored to activity-attach (UT creation), but the contract mandates the prazo counts from `data_recebimento_nip_iso` (currently never read anywhere). Re-model the timers as timeDate/absolute deadlines computed from `data_recebimento_nip_iso` (a NIP `*_sla.dmn` may compute the concrete deadline ISO; attach the computed value to the boundary timers), so regulatory deadlines anchor correctly. Regulatory timers stay in BPMN (not Python).
**Acceptance criteria:** NIP deadlines anchor to data_recebimento_nip_iso; `data_recebimento_nip_iso` is now read; real-engine test drives a timer and asserts the anchor.
**Dependencies:** none.

**Agent:** recurso-max-prazo-boundary
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
**Task:** GAP-RECURSO-1. BT_PrazoMaxRecurso (P30D RN424 regulatory ceiling, `:234-239`) is attached ONLY to UT_AnaliseRecursoAnalista. Once BT_SlaAnaliseRecurso cancels that task (escalating to UT_CoordenacaoRecursoAssume `:225-231`), or ESCALAR_AUDITOR routes to UT_RevisaoAuditorMedico (`:270-282`), or UT_EscalonamentoPrazo (`:251-256`) is active, the RN424 ceiling no longer applies. Attach an equivalent P30D boundary (anchored to the same regulatory start) to each of those human tasks so the regulatory ceiling holds on every human-analysis path.
**Acceptance criteria:** the P30D ceiling is enforced regardless of which human task holds the recurso; real-engine test covers the escalation paths.
**Dependencies:** none.

**Agent:** adequacao-coordenacao-disposition
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`
**Task:** GAP-ADEQ-3. The contract declares `decisao_coordenacao` (assumir_decisao|prorrogar_prazo|seguir_analise) as the SLA-breach coordinator disposition, but GW_DecisaoRemediacao (`:315-322`) reads only `decisao_remediacao`; UT_CoordenacaoRede (`:302-307`) output is never routed. Add gateway conditions reading `decisao_coordenacao` from UT_CoordenacaoRede so assumir_decisao/prorrogar_prazo/seguir_analise each route correctly (mirror the sibling coordenacao patterns).
**Acceptance criteria:** each `decisao_coordenacao` value routes distinctly; real-engine test covers the three dispositions.
**Dependencies:** none.

**Agent:** programa-a2a-and-desfecho
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/tools/workers/programa.py`, `src/maezo/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
**Task:** GAP-PROG-1 + GAP-PROG-2. (PROG-1) `ST_StratifyRisk` (`:266-292`) and `ST_BuildCarePlan` (`:295-331`) never invoke the Valentina A2A delegation (care.stratify/care.enroll) their docstrings + delegation.py document — they echo inputs. Invoke the A2A delegation for the in-zone post-consent computation. (PROG-2) ST_PublishCompleted (`BPMN:378-389`) declares no `event_desfecho` input parameter, so `agents.events.programa.completed` never carries a desfecho payload. Add `event_desfecho` as a `camunda:inputOutput` parameter sourced from the process's desfecho variable.
**Acceptance criteria:** stratify/build_care_plan perform real A2A calls post-consent; programa.completed carries the desfecho; tests cover both.
**Dependencies:** none.

**Agent:** ans-calendar-per-type
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn`, `src/maezo/processes/dmn/ans_calendar.dmn`, `docs/review-queue.md`
**Task:** GAP-ANS-1 + GAP-ANS-2. (ANS-1) Start_CalendarioRegulatorio (`:69-74`) is a single timerEventDefinition (R/P1M) with no inputOutput to set report_type/competencia/tenant_id — a single TimerStartEvent cannot deliver per-type cron for 5 report types with different periodicities. Re-model so each report type has its own scheduled trigger (separate timer start events per report_type/periodicidade, or an external scheduler emitting typed start messages) that sets report_type/competencia/tenant_id. (ANS-2) `ans_calendar.dmn` (`:28-31`) has a single input `report_type`; add `competencia` as the required second input so due_date/sla_alerta differ per competência (hitPolicy FIRST, keep catch-all). Regulatory timers stay in BPMN; regulatory dates in DMN. Mark DRAFT + register in `docs/review-queue.md`.
**Acceptance criteria:** each report type fires on its own periodicity with report_type/competencia set; ans_calendar differentiates due_date by competência; real-engine test drives at least two report types.
**Dependencies:** none.

**Agent:** wave1-testspec-quadruples
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `docs/processes/test-specs/SP-OP-CRED-001.md` (new), `docs/processes/test-specs/SP-OP-ADEQUACAO-001.md` (new), `docs/processes/test-specs/SP-OP-PAGTO-001.md` (new), corresponding `tests/integration/processes/`
**Task:** GAP-CRED-3 + GAP-ADEQ-2 + GAP-PAGTO-8. The mandatory BPMN+contract+DMN+test-spec quadruple (`catalog.md:25`) is broken for CRED, ADEQUACAO, PAGTO (files absent; siblings have them). Author each `test-specs/SP-OP-*.md` with the canonical acceptance-criteria scenarios, in particular the ADR-0018 no-adverse-without-human invariant (CRED descredenciamento/denial; ADEQUACAO End_CompromissoFallbackHumano-only-via-UT; PAGTO high-value release). Ensure a real-engine integration test exists for each invariant (add if missing).
**Acceptance criteria:** three test-spec files exist matching the sibling format; each references a real-engine sweep test proving the no-denial invariant; catalog quadruple satisfied.
**Dependencies:** none. (ADEQ-2's fallback test complements Wave 0 GAP-ADEQ-1.)

**Agent:** escalation-resume-publish
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
**Task:** GAP-ESC-1. ST_PublishProcessCompleted (`:186-196`) publishes `agents.events.process_completed` on devolvido_agente to "retomar conversa do agente", but no consumer exists. This brief covers the PUBLISH side correctness only (the consumer is GAP-XHITL-4/GAP-TRIAGE-4): verify the published fact carries the correlation keys a resume consumer needs (conversation_id/business_key ESC-{tenant}-{conv}, agent_id) and that the topic is registered in `config/topic_registry.yaml`. If keys are missing, add them to event_payload_vars (non-PHI only; route PHI by ref per GAP-XPHI-1).
**Acceptance criteria:** the process_completed fact carries conversation_id + agent_id + business_key; topic registered; no PHI in payload.
**Dependencies:** none. (Consumer side: GAP-XHITL-4, Wave 2.)

### WAVE 2 & WAVE 3 BRIEFS (medium + low, batched)

> Wave 2/3 gaps are grouped into per-process or per-theme batch briefs; each remains self-contained via the gap detail in §4.2. Only groupings with non-obvious constraints are expanded below; the rest follow the same batch template (one PR per process, T1/T2 by complexity).

**Agent:** xhitl4-universal-resume-consumer
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/runtime/agent_runtime/` (new resume consumer), bootstrap wiring
**Task:** GAP-XHITL-4 (supersedes/absorbs the per-agent resume work in GAP-ESC-1 + GAP-TRIAGE-4). `agents.events.process_completed` (the universal human→agent return leg for all 8 agents) has zero subscribers — only 3 AIOKafkaConsumer sites exist (inbound_driver, notifications_bridge, fhir_sync). Build one agent-agnostic resume consumer (mirror inbound_driver's AIOKafkaConsumer + manual-commit) subscribing to `agents.events.process_completed`, re-driving the originating agent graph on the devolvido_agente branch keyed idempotently by conversation_id/business_key (ESC-{tenant}-{conv}); dispatch to the correct agent by `agent_id` in the fact. Wire into agent_runtime bootstrap alongside inbound driver.
**Acceptance criteria:** an ESCALATION-001 devolvido_agente terminal re-enters the correct agent's conversation for any agent (integration test); idempotent on redelivery.
**Dependencies:** GAP-ESC-1 (publish carries correlation keys). If GAP-TRIAGE-4's Helena-only consumer landed in Wave 1, replace it with this universal one.

**Agent:** nip-autonomy-parity
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/policies/autonomy/L0-core.yaml`, `src/maezo/policies/autonomy/_hard_frozen.yaml`, `docs/review-queue.md`
**Task:** GAP-NIP-3 + GAP-XHITL-3 (same policy defect). The NIP MANTER_NEGATIVA disposition "embute authorization_denial-class (L0 hard)" per the contract, but the autonomy matrix classifies the whole NIP family under generic `nip_response:{level: L1}` (`L0-core.yaml:11`), not the frozen `authorization_denial`. Structural HITL still holds (mandatory UT + ERR_NIP_NEGATIVA_NOT_HUMAN at `nip.py:300`), so this is an autonomy-class/hard-frozen parity gap. Split NIP semantics: keep `nip_response` (L1) for CONCEDER/RESPONDER_NAO_ASSISTENCIAL, but classify MANTER_NEGATIVA under `authorization_denial` (L0 hard) OR add a dedicated `nip_manter_negativa` hard item to BOTH `L0-core.yaml` and `_hard_frozen.yaml` in the same PR (governance change per `_hard_frozen.yaml` header: CODEOWNERS review + superseding note vs ADR-0008/0018). Register in `docs/review-queue.md`.
**Acceptance criteria:** MANTER_NEGATIVA inherits frozen DENY semantics; hard-frozen CI test covers the new item; parity with AUTH authorization_denial.
**Dependencies:** none.

**Agent:** whatsapp-webhook-phi-edge
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/platform/webhooks/whatsapp/app.py`, `src/maezo/runtime/inbound_driver.py`, `config/topic_registry.yaml`
**Task:** GAP-XPHI-3. The webhook hashes the phone (`app.py:113`) but publishes raw `message_body` (`:136` via `_publish:262`) onto `agents.events.whatsapp.message-received`; the driver scrubs only at `inbound_driver.py:189`, and `_to_dlq` (`:342-354`) republishes the untouched raw `msg.value`. Route `message_body` through a webhook-edge one-way scrub in `app.py:_extract` (~line 136) — reuse the salted-hash pattern for structured identifiers or a CPF/CNS/phone redactor mirroring `pseudonymizer._FREE_TEXT_PASSES` — before `_publish`; OR classify the topic + its `.dlq` as PHI-zone-restricted short-retention in `config/topic_registry.yaml` and document the exception. In `_to_dlq` replace `'value': msg.value` with correlation-only fields (wamid, tenant, reason, error) plus a scrubbed body.
**Acceptance criteria:** no raw free-text PHI reaches Kafka from the webhook; DLQ never persists raw PHI; tests cover a message containing a CPF.
**Dependencies:** none.

**Agent:** xobs-hitl-metric-split-and-scope
**Tier:** T3 (claude-opus-4-8)
**Files:** `src/maezo/platform/console/app.py`, `src/maezo/runtime/metrics.py`, `config/candidate_groups.yaml` (new) or startup BPMN parse, `deploy/observability/alert-rules.yaml`
**Task:** GAP-XOBS-5 + GAP-XOBS-6 (same console module). (XOBS-5) `record_hitl_approved` is fired once from `console/app.py:481` with literal `agent='console.medico-auditor'` (never matches a real agent_id), on a BPMN L0-hard UserTask completion — so `MaezoHITLApprovalRateDrop` is a guaranteed false alarm. Stop emitting `record_hitl_approved` under a synthetic agent id; keep `maezo_hitl_*` scoped to agent-mediated L1 proposals. Introduce a separately-named `maezo_bpmn_usertask_decision_total{task_name,decisao}` counter for BPMN UserTask completions with its own alert. (XOBS-6) `_AUTH_CANDIDATE_GROUPS` (`:73`, consumed `:298`) is hardcoded to 2 AUTH groups; the pending-task gauge/alert is blind to 14 other processes. Generalize `list_tasks`'s candidate-group filter + the `set_user_task_pending` loop (`:311-317`) to the full deployed candidate-group set — derive at startup by parsing `camunda:candidateGroups` across all `.bpmn` or from a new `config/candidate_groups.yaml`.
**Acceptance criteria:** MaezoHITLApprovalRateDrop no longer false-alarms; a new BPMN-usertask-decision metric exists; pending-task gauge covers all deployed candidate groups; tests cover a non-AUTH group.
**Dependencies:** none.

**Agent:** xproc3-recurso-handoff-vars
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/platform/integrations/notifications_bridge/consumer.py`, `src/maezo/tools/workers/contas.py`, `config/topic_registry.yaml`, `tests/integration/processes/test_cross_process_handoff_seam.py`
**Task:** GAP-XPROC-3. `recurso_variables()` (`consumer.py:185-194`) supplies only 5 identity fields; it omits `glosa_type` (contract obrigatório, read by ST_PublishReceived) and the 3 pre-resolved facts `glosa_existe*`/`dentro_prazo_recurso*`/`documentacao_recurso_completa*` that `recurso_admissibility.dmn` needs — so every bridge-started CONTAS→RECURSO instance falls through to ANALISE_HUMANA and SKIPS BRT_Eligibility. Extend `recurso_variables()` to read `glosa_type` from the CONTAS envelope (add it to `contas.py make_start_recurso_handler` payload if absent). For the 3 facts, either have CONTAS compute+include them at handoff, or add an `operadora.recurso.resolve_facts` external task (new topic in `topic_registry.yaml`) before BRT_Admissibilidade (mirror cancel.py resolve_facts). Update `test_cross_process_handoff_seam.py` to call the REAL `recurso_variables()` instead of hand-building richer dicts.
**Acceptance criteria:** a bridge-started recurso carries glosa_type + the 3 facts; the SEGUE_ANALISE fast path and BRT_Eligibility are reachable; the seam test exercises the real function.
**Dependencies:** none.

**Agent:** wave2-cancel-batch
**Tier:** T2 (claude-sonnet-4-6)
**Files:** `src/maezo/processes/dmn/cancel_admissibility.dmn`, `src/maezo/processes/dmn/cancel_routing.dmn`, `src/maezo/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn`, `docs/processes/contracts/SP-OP-CANCEL-001.md`, `src/maezo/tools/workers/cancel.py`
**Task:** CANCEL medium batch: **GAP-CANCEL-2** — `dentro_prazo` mandatory DMN input uses `-` in every rule (zero effect); either make it gate a rule or document/remove. **GAP-CANCEL-3** — add a worker-level guard enforcing `fundamentacao_contratual` mandatory for MANTER (mirror RESCINDIR/SUSPENDER's ERR guard; ADR-0018). **GAP-CANCEL-4** — declare the `notificacao_previa_feita=true` requirement in the contract (currently only in test scaffolding). **GAP-CANCEL-5** — either wire `cancel_routing.dmn` to a businessRuleTask/worker or remove its "Consumida por" claim (dead DMN). **GAP-CANCEL-7** — restrict Flow_GWDec_EfetivarPedido so only `pedido_beneficiario` tipo_solicitacao routes to member-request effectuation (match the DMN-gated L2 path). Mark contract/DMN content edits DRAFT where clinical/legal + register in `docs/review-queue.md`.
**Acceptance criteria:** each sub-gap resolved; MANTER without fundamentacao rejected; cancel_routing either live or removed; tests updated.
**Dependencies:** none.

**Agent:** wave2-remaining-medium-batches
**Tier:** T2 (claude-sonnet-4-6)
**Files:** per-process (AUTH/FRAUDE/RECURSO/CONTAS/NIP/REEMBOLSO/INADIMPLENCIA/CRED/ESCALATION/PAGTO/PROGRAMA/ANS/ADEQUACAO/HELENA)
**Task:** Remaining Wave 2 medium gaps, one PR per process, each fix per its §4.2 row and the constraints below. **GAP-AUTH-6/GAP-RECURSO-2** (notify_sla_risk read nested `sla` object not flat vars). **GAP-FRAUDE-4** (choose ERR_FRAUD_CUSTODY_* vs ERR_FRAUD_ACCUSATION_NOT_HUMAN by failure cause per contract error table). **GAP-CONTAS-5** (wire or remove `operadora.contas.analyze_reason` dead topic). **GAP-NIP-2** (promote nip_routing `grupo_humano`/`sla_alerta_iso` to be consumed, or drop static candidateGroups). **GAP-NIP-4** (produce `protocolo_filing` via post-handoff correlation). **GAP-REEMBOLSO-4** (real Marina A2A in ST_PrepararDossie). **GAP-REEMBOLSO-5** (reorder so BRT_Calculo runs before `dentro_tabela` is derived). **GAP-INAD-4** (author test-spec). **GAP-INAD-5** (reconcile contract rescission-ownership text with shipped BPMN). **GAP-INAD-6** (wire a consumer for Fernando's prepare_dossier, same root as GAP-XPROC-1 bridge). **GAP-CRED-4** (route cred_route ANALISE_HUMANA catch-all to a co-review branch, not credenciamento-only). **GAP-CRED-5** (add SOLICITAR_INFO gateway branch). **GAP-CRED-6** (gate BRT_PriorNotice to direcao=descredenciamento only). **GAP-ESC-4** (formData enforcing `resultado` enum). **GAP-ESC-5** (boundary error catches on the 6 unguarded serviceTasks). **GAP-PAGTO-5** (distinct `decisao_coordenacao` field/gateway for UT_CoordenacaoAlcada). **GAP-PROG-3** (promote flat `elegivel_programa` from `roteamento`). **GAP-PROG-4** (produce/correlate `msg.programa.consent_revoked` from DSR revogacao). **GAP-PROG-7** (author test-spec). **GAP-ANS-3** (set report_type/competencia/periodicidade + correct business key in `ans_submit_variables()`). **GAP-ANS-4** (boundary SLA timers on UT_RevisarEnvioJuridico/UT_CorrigirPendenciaEnvio). **GAP-ADEQ-4** (add an `adequacao_dossier` flow to Andre's delegation map, not the PAGTO-only mapping). **GAP-ADEQ-6** (add update_monitoring_plan/notify_rede to contract Tópicos). **GAP-TRIAGE-3** (fix episodic memory write payload to MemoryServer._store kwargs tenant/agent_id/fhir_patient_id/note). **GAP-FRAUDE-5** (add the 4 flat booleans `destino_referral_cred/_contratual/_juridico/_ans` to the FRAUDE contract Variáveis de saída table `:85-97`). **GAP-PROG-5** (fix Valentina `DMN_PROGRAMA_STRATIFICATION` `graph.py:102,399,415` from `programa_stratification` to deployed decision id `programa_routing`). **GAP-RECURSO-3** (implement + throw `Error_RecursoGlosaInvalida` in recurso.py mirroring contas.py `ERR_CONTAS_LOTE_INVALIDO`/auth.py invalid-origin guard, and add the `test_glosa_inexistente_lanca_erro` scenario). **GAP-ESC-3** (scrub `notas_resolucao` — depends on GAP-XPHI-1 seam). Constraints: business rules → DMN; regulatory timers → BPMN; clinical/legal content → DRAFT + `docs/review-queue.md`.
**Acceptance criteria:** each gap resolved per its §4.2 description with a test; no ADR violation introduced.
**Dependencies:** GAP-ESC-3 depends on GAP-XPHI-1 (Wave 1); GAP-INAD-6 relates to GAP-XPROC-1 (Wave 0). All others independent.

**Agent:** wave3-low-polish-batch
**Tier:** T1 (claude-haiku-4-5)
**Files:** per-process (see §4.2 Low table)
**Task:** All 18 low gaps, fully parallel, one PR per process or a single docs PR where doc-only. Each per its §4.2 row: **GAP-CANCEL-6** (add responsavel_id to ST_PublishMantido payload), **GAP-CONTAS-6/GAP-INAD-7** (reconcile contract UNIQUE→FIRST text drift), **GAP-FRAUDE-6** (add sla_diligencia to fraude_sla contract description), **GAP-NIP-5** (route info-wait timeout to elaboracao/coordenacao per contract), **GAP-NIP-6** (validate+throw Error_NipProtocoloInvalido or remove decl), **GAP-REEMBOLSO-6** (remove decisao_coordenacao doc overclaim), **GAP-RECURSO-4** (declare resposta_operadora in contract), **GAP-LGPD-5** (remove dead sla_resposta output or add drift test), **GAP-LGPD-6** (known-pending: add boundary catch for Error_LgpdIdentidade at FINAL promotion, or keep queued), **GAP-ADEQ-7** (correct docstring overclaim vs _hard_frozen.yaml), **GAP-ADEQ-8** (read or remove network_change_ref), **GAP-PAGTO-6** (fix grupo_aprovador clerical-pagamentos vs allowlist), **GAP-PAGTO-7** (add data_vencimento to Andre _contract_variables, drop undeclared vars), **GAP-PROG-6** (add elegivel_programa input to programa_sla.dmn or fix contract), **GAP-ANS-5** (compute/echo lgpd_anonimizado or fix doc), **GAP-XOBS-7** (wire AuditRecord.dmn_versions from ctx['dmn_decision_refs'] or remove the dead field), **GAP-ESC-2** (`make_notify_team_handler` echoes flat `grupo_atendimento`/`sla_ack` defaults `phase0.py:218,220` into the notification event payload — read the echo values from the `roteamento` map or drop the echo fields; real routing via `${roteamento.*}` is already correct). Doc-only edits touching clinical/regulatory text → DRAFT + `docs/review-queue.md`.
**Acceptance criteria:** each low gap closed or explicitly deferred with a queue entry; no behavior regression.
**Dependencies:** none.

---

## 4.5 Orchestration Instructions

**Wave gating.** Waves run strictly in sequence (0→1→2→3). Within a wave, all listed briefs spawn simultaneously in isolated git worktrees, one PR per work-stream. A wave is complete only when EVERY agent returns green structured output AND `validate-artifacts` + `pytest` (including the real-engine integration suites) pass on each merged work-stream. Do not start Wave N+1 until Wave N is merged and green on `main`.

**Per-wave spawn groups.**
- *Wave 0 (13 gaps / 12 briefs, all T2/T3):* spawn all 12 briefs at once. Two intra-wave orderings: land `wire-worker-modules-and-audit` (GAP-XOBS-1) before `wire-sla-breach-metrics-and-poller` (GAP-XOBS-2); land `patch-auth-publish-negada-phi` (GAP-AUTH-1) as a scoped patch (its systemic successor is Wave 1). All others fully parallel. Because `main` may be unprotected, use incremental green-merge: CI-gated squash-merge each PR as it passes, rebasing the rest.
- *Wave 1 (43):* spawn the xs and s batches first (fast, T1/T2), then the m and l briefs (T2/T3). Declared Wave1→Wave0 dependencies (must merge after their Wave-0 prereq): `phi-worker-egress-seam` (after GAP-AUTH-1), `lgpd-erasure-cascade` (after GAP-LGPD-1), `wire-worker-sla-metrics` (after GAP-XOBS-1).
- *Wave 2 (41):* spawn per-process batch briefs + the cross-cutting m briefs in parallel. Declared deps: `wave2-remaining-medium-batches` GAP-ESC-3 sub-item after `phi-worker-egress-seam` (Wave 1); `xhitl4-universal-resume-consumer` after `escalation-resume-publish` (Wave 1).
- *Wave 3 (18):* spawn the single `wave3-low-polish-batch` fan-out fully parallel at T1.

**Stalled/failed agent handling.** If an agent returns non-green or stalls, retry once at the next tier up (T1→T2→T3). If it fails again, escalate to the orchestrator with the partial diff and CI log; do NOT merge a red work-stream. A worktree/branch may be deleted mid-session (uncommitted edits lost, remote branch survives) — recreate via `git worktree add --track -b ... origin/...` then `uv sync --extra dev` (dev tools are an optional extra).

**Merging partial wave outputs.** Each work-stream is a separate worktree + branch + PR. Fetch before touching shared files (topic_registry.yaml, service.py, metrics.py, consumer.py, L0-core.yaml are hot — several briefs touch them; serialize those merges and rebase). CI-gated squash-merge only. After each wave, update `docs/handoffs/HANDOFF.yaml` (or the current handoff doc) with the merged gap ids and remaining residue before the next wave starts. Hot-file contention map for scheduling: `worker_runtime/service.py` (GAP-XOBS-1, GAP-XOBS-2, GAP-LGPD-1, GAP-XOBS-4), `notifications_bridge/consumer.py` (GAP-XPROC-1, GAP-XPROC-3), `metrics.py` (GAP-XOBS-2/3/4/5), `L0-core.yaml`/`_hard_frozen.yaml` (GAP-XHITL-2, GAP-NIP-3) — merge these serially within their wave.

---

## Quality-gate self-verification

1. **Unique traceable ids** — PASS. Every gap has a unique `GAP-*` id with a `file:line`/element ref (§4.2); `GAP-XHITL-1` merged into `GAP-XPROC-1` with a note. Post-verification, the registry is re-sorted severity DESC then fix_complexity ASC within each severity, and the severity counts (critical 13 / high 43 / medium 41 / low 18, total 115) reconcile across §4.1, §4.2, and the §4.3 wave lists (13 / 43 / 41 / 18).
2. **Self-contained briefs** — PASS. Each §4.4 brief repeats file paths, element ids, error codes, and ADR constraints; batch briefs inline every sub-gap's specifics.
3. **No undeclared Wave1+ → Wave0 dependency** — PASS. The three Wave-1 deps on Wave-0 (GAP-XPHI-1→AUTH-1, GAP-LGPD-2→LGPD-1, GAP-XOBS-3→XOBS-1) and the two Wave-2 deps are declared explicitly in §4.3/§4.5.
4. **Strengths per process/dimension** — PASS with stated exceptions (§4.1 patterns + per-process evidence below).
5. **Forbidden phrase** — PASS. The banned vague-prescription phrase appears nowhere in this document; every fix specifies concrete element ids / DMN table+row / variable names / error codes.
6. **All critical/high at T2+** — PASS. Every Wave 0 and Wave 1 brief is T2 or T3; T1 is used only for Wave 3 low gaps and xs doc edits.

**Strengths coverage note (gate 4).** Exemplary elements are cited per process across both dimensions in §4.1 and the XSTR digest: AUTH (guard `auth.py:283-289` [impl] + SLA timers `bpmn:234-272` [bl]), CANCEL (guard `cancel.py:357-395` + anti-double-adverse topology), CONTAS (`register_glosa_accept` guard + SLA), FRAUDE (fail-closed gateway + custody PHI scan + real-engine sweep), NIP (guard + Gustavo type-level exclusion), REEMBOLSO (guard + PHI log exclusion), RECURSO (bounded loop `bpmn:292-329` + guard), INADIMPLENCIA (guard + single-owner rescission topology), LGPD (chokepoint gate + instance-anchored SLA + fail-closed identity gateway), ADEQUACAO (ENGINE-16004 output promotion + guard + PHI log), CRED (guard + Carolina type-level exclusion + 96-combo sweep), PAGTO (guard + `typeRef=long` money discipline + sweep), PROGRAMA (consent chokepoint + sweep), ANS-SUBMIT (bounded retry + chokepoint), ESCALATION (SLA DMN `escalation_routing.dmn` r1-r7 [bl]). **Explicit exceptions:** (a) ESCALATION-001 implementation dimension — its only exemplary impl element is the single ST_NotificarTime boundary catch; the other serviceTasks lack catches (GAP-ESC-5), so no strong impl exemplar beyond that one element. (b) AGJ-HELENA-TRIAGE implementation dimension — the design-layer strength (triage_redflag two-tier fail-safe DMN + type-level exclusion) is exemplary, but the tool-call implementation is currently defective (GAP-TRIAGE-1/2/3), so no exemplary impl element is honestly available for Helena until Wave 0 lands.
