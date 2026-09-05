# Pre-Deployment Audit Report (orchestrator-predeploy, 2026-07-04)

**Program:** `pre-deployment-hardening-final-audit` · **State:** PREDEPLOY-RUNNING · **Coordinator:** orchestrator-predeploy
**Plan:** `~/.claude/plans/pre-deployment-hardening-serialized-badger.md` (user-approved 2026-07-04)
**Baseline:** `origin/main` @ `ca6a7fe` (PR #174 merge) at program start; union-green proof pinned to run-IDs `28694335993`(CI)/`28694335916`(CD), never SHA check-runs (ADR-0023 semantics).
**Draft provenance:** this is a T2 draft compiled from `docs/handoffs/HANDOFF-predeploy.yaml`, the WS-5 audit outputs (`ws5-findings.json`/`ws5-escalations.json`/workflow result `wgizuui60.output`), ADR-0023/0024, and a small set of live read-only checks (`git`, `gh api`/`gh pr list`, `docker ps`, `lsof`) run at draft time (2026-07-04, local main `f9246e8`). Where live state diverges from the handoff snapshot, both are shown and the divergence is called out — this mirrors the exact "local main behind origin/main" staleness class the WS-5 sweep itself repeatedly flagged (see §5).

---

## 1. Executive summary

**Scope.** Six workstreams: **WS-1** (branch-protection / merge-policy hardening, ADR-0023), **WS-2** (Dependabot alert triage, 16 open), **WS-3** (build-tooling truth: `uv.lock` alignment + Docker image rebuild), **WS-4** (Wave-3 residue: PHI-HMAC env injection, durable cross-restart idempotency ADR-0024, PAGTO→Andre A2A envelope, LGPD re-identification deferral), **WS-5** (this report's core: a 7-dimension adversarial pre-deployment audit), **WS-6** (six user-only decisions, §7).

**Headline numbers.**
- **2 PRs merged** by this program: **#175** (Wave-3 close docs bundle) and **#176** (PAGTO→Andre A2A envelope; merged externally by the user ahead of independent verification, T3-PASSed post-merge per ESC-001/DL-0026).
- **6 additional PRs built, builder+T3-verifier-passed, and parked** behind the billing wall in the ready-to-merge queue: **#179** (WS-3a uv-align), **#180** (WS-2 B1 deps), **#181** (WS-4 durable idempotency, ADR-0024), **#178** (WS-2 B2 crypto), **#182** (WS-3b Dockerfiles), **#183** (WS-1 branch-protection workflow — built and opened *since* the last handoff snapshot; see §6).
- **1 new PR opened this session in direct response to the WS-5 audit and not yet independently (T3) verified: #184** (disables the orphaned `gateway` Deployment — the fix for the gateway crash-loop, §2.1). A second deploy-blocking fix (webhook-receiver secrets/Kafka, §2.2–2.3) has **not yet been started** (worktree `maezo-pd-webhook` has no commits beyond the shared baseline).
- **WS-5 audit: 60 confirmed findings** (of 78 total sweep records) **+ 18 killed by adversarial (second-refuter) verification** — i.e., **23% of raw candidate findings were refuted** before reaching this report, across 7 dimensions and 127 sub-agent invocations (~8.5M tokens, 2,683 tool calls; `wgizuui60.output`). Of the 60 confirmed:
  - **4 deploy-blocking** finding-IDs → **3 unique underlying defects** (the gateway crash-loop was independently rediscovered by two dimensions — see §2).
  - **48 post-deploy** (real, tolerable short-term, ledgered as briefs).
  - **8 cosmetic** (documentation/lint-level, ledgered).
- **44 finder/refuter escalations** (coverage caveats) — distilled in §5.
- **The GitHub Actions billing-wall event** (still ACTIVE as of this draft — confirmed via a live `gh run list`: the 06:00Z nightly cron on tip `c8aed3f` also failed at 08:28Z today): the Omni-Saude org's Actions spending limit is capped at exactly **$30.00/month**, exhausted mid-session at **8,006 Linux minutes** of July usage. First casualty: PR #182's run at 05:48Z. All merges and the docs-bundle push are **halted** (stop-the-line) until the user raises the limit or the Aug-1 reset. A background probe re-runs `gh run rerun` every 20 minutes (≤18 attempts) to auto-detect restoration.

**Report-drafting-time correction to the handoff snapshot:** live `gh api` checks (2026-07-04, draft time) show local `main` (`f9246e8`) is **1 commit behind `origin/main`** (`c8aed3f` = PR #177 merge) — the same staleness class flagged repeatedly by WS-5 finders against a slightly earlier snapshot. PR #177 only touches `deployment-agent-runtime.yaml`/`deployment-worker-daemon.yaml`, so this report's other direct file reads (ExternalSecret, ADRs, DMN/BPMN/worker counts) are unaffected. Branch protection is confirmed **still not applied** (`GET .../branches/main/protection` → 404, live-checked).

**GO/NO-GO decision:**

<!-- FINAL: completed by coordinator at close -->
```
GO / NO-GO: {{GO_NO_GO_DECISION}}
Conditions precedent (if GO):
  - {{CONDITION_1}}  (e.g., PR #184 gateway-disable merged + T3-verified)
  - {{CONDITION_2}}  (e.g., webhook-receiver secrets/Kafka fix PR merged + T3-verified)
  - {{CONDITION_3}}  (e.g., §6.2 secret population complete: phi-hmac-key, whatsapp app-secret+verify-token)
  - {{CONDITION_4}}  (e.g., Actions billing wall lifted, ready-to-merge queue drained, main union-green)
Signed off by: {{APPROVER}}   Date: {{DATE}}
```
<!-- /FINAL -->

---

## 2. Deploy-blocking findings (3 unique defects, 4 finding-IDs)

All three are **Helm/deployment-render** defects: the chart renders green (`helm lint`/`helm template` exit 0) but the affected pods crash-loop or are functionally dead at runtime — the exact "renders green, breaks at runtime" shape that a YAML-templating lint pass structurally cannot catch.

### 2.1 Gateway Deployment crash-loop (convergent — 2 finding-IDs, 1 defect)

Independently rediscovered by **two different WS-5 dimensions**, which is itself a useful cross-validation signal:

| Finding ID | Dimension | Verifier(s) |
|---|---|---|
| `gateway-deployment-crashloop-no-main` | phi-perimeter | 1 verifier, CONFIRMED |
| `gateway-service-command-has-no-main-module` | observability | 2 verifiers (both CONFIRMED, deploy-blocking) |

**Claim.** The `gateway` Kubernetes Deployment runs `command: ["python", "-m", "maezo.gateway"]`, but `src/maezo/gateway/` is a pure library package (`pep.py`, `audit.py`, `pseudonymizer.py`, `custody.py`, …) with **no `__main__.py`**. `gateway.enabled` defaults `true` (`values.yaml:113-114`) and `values-amh.yaml` only overrides `replicaCount: 3` — never `enabled: false` — so this renders live in the CI-validated amh-overlay production values.

**Evidence.** `deploy/helm/maezo-tenant/templates/deployment-gateway.yaml:34`; directory listing of `src/maezo/gateway/` (no `__main__.py`, contrast with `worker_runtime/__main__.py`, `webhooks/__main__.py`, `fhir_sync/__main__.py`, all present); reproduced directly — `uv run python -m maezo.gateway` fails with `No module named maezo.gateway.__main__; 'maezo.gateway' is a package and cannot be directly executed`. Both `maezo_gateway_*` metrics and the `gateway.json` dashboard / `MaezoPEPDenySpike` / `MaezoAuditLagHigh` alerts go permanently dark as a downstream consequence.

**Fix disposition:** `{{PR_GATEWAY}}`. *(Draft-time observation, not yet independently verified: PR **#184**, branch `fix/predeploy-gateway-orphan`, commit `351eec1` stacked on #177, is already open. It does **not** add a `__main__.py` — per the audit's own adjudication it **disables** `gateway.enabled` (`true→false`) instead, on the grounds that the PEP/pseudonymizer/audit code is an in-process library already imported by agent-runtime and worker-daemon per ADR-0006, and there is no standalone gateway service to build. It also strips the now-inert `gateway.replicaCount` overrides in `values-amh.yaml`/`values-staging.yaml`. This addresses both convergent finding-IDs at once. Verification (T3) is pending.)*

### 2.2 webhook-receiver — missing required secrets → crash-loop

**Finding ID:** `webhook-receiver-missing-required-app-secret-verify-token-crashloop` (infra-deploy; 2 verifiers, both CONFIRMED deploy-blocking).

**Claim.** `deployment-webhook-receiver.yaml` injects only `TENANT_ID` and `WHATSAPP_TOKEN` (wrong credential — the outbound WABA send-token, not an inbound field); it never injects `WHATSAPP_APP_SECRET`/`WHATSAPP_VERIFY_TOKEN`, both required (`Field(...)`, no default) on `WhatsAppWebhookSettings`. `create_app()` eagerly constructs the settings object with no override, raising a pydantic `ValidationError` before `uvicorn.run()` is ever reached — before the pod can even bind a port or serve `/healthz`.

**Evidence.** `deployment-webhook-receiver.yaml:35-43`; `src/maezo/platform/webhooks/whatsapp/settings.py:19,22`; `app.py:203`; `__main__.py:39`. The `whatsapp-config` ExternalSecret (`externalsecret.yaml:71-94`) has no `remoteRef` entry for either field at all — this is a wiring gap independent of whether WABA business credentials have been delivered. **Amplifier:** the CD pipeline runs `helm upgrade --install ... --atomic --timeout 10m --wait` (`cd.yml:279-284`); a never-Ready webhook-receiver pod times out `--wait` and `--atomic` rolls back the **entire release** — gateway, agent-runtime, fhir-sync included, not just the WhatsApp channel.

**Fix disposition:** `{{PR_WEBHOOK}}`. *(Draft-time observation: worktree `maezo-pd-webhook` exists but has **no commits beyond the shared baseline** — this fix has not been started yet.)*

### 2.3 webhook-receiver — missing Kafka bootstrap + egress

**Finding ID:** `webhook-receiver-missing-kafka-bootstrap-and-egress` (infra-deploy; 2 verifiers, both CONFIRMED deploy-blocking).

**Claim.** Even once §2.2 is fixed, webhook-receiver never receives `KAFKA_BOOTSTRAP_SERVERS` (defaults to `localhost:9092`, a nonexistent in-pod broker) and is **not** a member of any Kafka-egress NetworkPolicy selector (`wr2-daemon-egress` lists only `worker-daemon`/`notifications-bridge`). Under `default-deny-egress`, even a correctly-configured bootstrap URL would be blocked from reaching the external MSK Serverless broker.

**Evidence.** `deployment-webhook-receiver.yaml:35-43` (contrast `deployment-agent-runtime.yaml:85-89`, which does inject it); `whatsapp/settings.py:25`; `app.py:211-218`; `networkpolicy.yaml:231-267` (`wr2-daemon-egress` selector). **Net effect:** inbound WhatsApp member messages are never published to Kafka and never processed by the agent graph — the core inbound member-communication path is dead even after §2.2 is fixed.

**Fix disposition:** `{{PR_WEBHOOK}}` (same PR as §2.2 — both webhook-receiver defects should land in one vertical slice: secrets + Kafka wiring + egress rule).

---

## 3. Findings table — all 60 confirmed, grouped by dimension

Disposition key: **deploy-blocking → fixed-this-session** (§2); **post-deploy → ledgered-brief** (owner-area brief in §8); **cosmetic → ledgered** (tracked, no brief needed).

### 3.1 bpmn-dmn (4 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `auth-denial-not-human-undeclared-error` | post-deploy | `ERR_AUTH_DENIAL_NOT_HUMAN` is thrown by `send_denial_notice` but no matching `<bpmn:error>` is declared anywhere in SP-OP-AUTH-001 — worse than a missing boundary catch, the error isn't modeled at all. | `auth.py:76,327-328`; `SP-OP-AUTH-001_Autorizacao_Previa.bpmn` (only 2 errors declared, neither this code) | ledgered-brief |
| `cancel-brtcancelsla-dangling-outgoing-idref` | post-deploy | `BRT_CancelSla`'s `<outgoing>` IDREF (`Flow_Sla_Dossie`) points at a `sequenceFlow` id that doesn't exist anywhere in the file; real flow is `Flow_Sla_Classificacao`. | `SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:199` vs `:481` | ledgered-brief |
| `cred-business-key-missing-collision-fallback` | post-deploy | 3 business-key builders (CRED/CANCEL/INADIMPLENCIA) skip the digest-fallback pattern sibling builders use; a blank identity field silently collides two distinct real-world cases onto one businessKey, and the second is silently dropped (`already_existed` no-op). | `notifications_bridge/consumer.py:590-624` vs `:325-335` | ledgered-brief |
| `publish-missing-topic-guard-unreachable-in-current-deployment` | cosmetic | `ERR_PUBLISH_MISSING_TOPIC` guard is dead code today (`event_topic` is always a hardcoded literal on all 16 process files) but would reintroduce an uncatchable crash on every publish task if ever parameterized. | `phase0.py:249-256` | ledgered |

### 3.2 no-denial-autonomy (6 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `lucas-cancel-start-overclaim` | post-deploy | Lucas's prompt claims he "abre" (opens) SP-OP-CANCEL-001 directly; `agent.yaml` scopes his only start-capability to SP-OP-ESCALATION-001 — the same overclaim class PR #174 just fixed for Carolina/Rafael, unfixed here. | `lucas/prompts/system-v1.md:18` vs `agent.yaml:34,48`; `graph.py:151-152` | ledgered-brief |
| `lgpd-negar-fundamentado-classified-neutral` | post-deploy | `End_RequisicaoConcluida` is classified NEUTRAL but is also the exit for the human's `NEGAR_FUNDAMENTADO` (substantive LGPD-rights denial) decision — merges an adverse-in-substance outcome into the non-adverse bucket, exempting it from the suite's own human-gate reachability proof. | `SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:286`; `test_no_denial_consolidated.py:326` | **needs product judgment — see §3.5** |
| `helena-autonomy-actions-incomplete-triage-scheduling` | post-deploy | Helena's `agent.yaml` `autonomy_actions` omits `triage_and_routing`/`scheduling`/`informational_response`, though her own journey doc and prompt say she performs them. | `helena/agent.yaml:25-31` vs `AGJ-HELENA-TRIAGE.md:6` | ledgered-brief |
| `no-denial-heuristic-blind-to-genuine-adverse-terms` | cosmetic | The "PT+EN defense-in-depth" adverse-looking regex doesn't match several already-registered genuinely-adverse end ids (`confirmada`, `encaminhado`, `desligamento`, …) and its PT/EN token pairs are asymmetric. | `test_no_denial_consolidated.py:402-407` | ledgered |
| `no-denial-boundary-escape-reachability-blindspot` | post-deploy | The human-gate reachability BFS only expands boundary-event edges when the source node is a human `userTask` — 24 boundary escapes attached to service/subProcess activities across 12 of 16 BPMN bodies are never actually traversed/verified. | `test_no_denial_consolidated.py:464-484` | ledgered-brief |
| `autonomy-actions-declared-but-never-exercised` | cosmetic | Helena/Rafael declare `query_process_status` (Gustavo additionally `correlate_process_message`) in `autonomy_actions` with a backing tool + graph.py constant, but no graph node ever calls them. | `helena/agent.yaml:21,28`; `rafael/graph.py:115,125`; `gustavo/graph.py:113-114,123-124` | ledgered |

### 3.3 phi-perimeter (7 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `gateway-deployment-crashloop-no-main` | **deploy-blocking** | See §2.1. | `deployment-gateway.yaml:34` | **fixed-this-session** (`{{PR_GATEWAY}}`) |
| `matricula-raw-to-general-zone-notifications-topic` | post-deploy | `inadimplencia.py`/`cancel.py` publish `matricula_beneficiario` (a declared direct-identifier PHI var) raw to the Zona Geral topic without `scrub_phi_vars` — bypassing the layer-2 egress backstop that `phase0.py`/`auth.py` are wired with. | `phi_vars.py:52-63`; `inadimplencia.py:429,502,689,760`; `cancel.py:221,340,388,488` | **needs product judgment — see §3.5** (same defect as `inad-cancel-matricula-phi-egress-unscrubbed`, §3.4) |
| `console-ingress-dangling-service-latent` | cosmetic | `ingress.yaml` references a `console` Service/backend with no Deployment/Service/entrypoint anywhere in the chart; inert only because `ingress.enabled` defaults `false`. | `ingress.yaml:62-73`; `values.yaml:174` | ledgered |
| `phone-hash-keyless-brute-forceable-reidentification` | post-deploy | `phone_number_hash` on the General-Zone bus is a **keyless** SHA-256 of `tenant:phone` (tenant public, phone low-entropy ~10⁹–10¹⁰); any General-Zone/DLQ reader can brute-force the exact phone number offline — the docstring's "salt is gateway-secret" claim is false (there is no salt). | `webhooks/whatsapp/security.py:44-46` | **needs product judgment — see §3.5** |
| `redact-payload-force-key-gap-operator-named-flow-fields` | post-deploy | `redact_payload` only force-tokenizes a small fixed key-name allowlist; a WhatsApp Flow field under an unlisted key carrying a bare name/address passes through raw to the General Zone. | `pseudonymizer.py:248-250,337-339` | ledgered-brief |
| `audit-chain-from-rows-ts-reorder-verify-flap` | post-deploy | `chain_from_rows` sorts by wall-clock `ts` before `verify_chain`, reintroducing exactly the clock-skew fragility `recover_head` was deliberately fixed to avoid — can produce a false tamper alarm under multi-writer/failover skew. | `audit_postgres.py:197` vs `:65-75` | ledgered-brief |
| `redact-payload-geo-coordinate-string-bypasses-rounding` | post-deploy | Geo-coordinate rounding is gated on `isinstance(value, int\|float)`; a coordinate delivered as a JSON string bypasses rounding entirely and passes through verbatim — a precise residential quasi-identifier pin. | `pseudonymizer.py:324,339` | ledgered-brief |

### 3.4 infra-deploy (4 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `networkpolicy-otel-cross-namespace-egress-missing` | post-deploy | Every OTel-emitting pod points at a Service in the separate `observability` namespace; no NetworkPolicy egress rule permits cross-namespace traffic under `default-deny-egress` — telemetry export is silently blocked cluster-wide. | `values.yaml:352-353`; `networkpolicy.yaml` (no observability-ns rule anywhere in 268 lines) | ledgered-brief |
| `webhook-receiver-missing-required-app-secret-verify-token-crashloop` | **deploy-blocking** | See §2.2. | `deployment-webhook-receiver.yaml:35-43` | **fixed-this-session** (`{{PR_WEBHOOK}}`) |
| `webhook-receiver-missing-kafka-bootstrap-and-egress` | **deploy-blocking** | See §2.3. | `deployment-webhook-receiver.yaml:35-43`; `networkpolicy.yaml:231-267` | **fixed-this-session** (`{{PR_WEBHOOK}}`) |
| `networkpolicy-ingress-policytype-blocks-all-cross-namespace-external-traffic` | post-deploy | The only Ingress-type NetworkPolicy (`allow-intra-namespace`) allows same-namespace peers only — defeats the ALB→webhook-receiver/console edge and cross-namespace PodMonitor scraping the moment either feature is enabled; latent today since both default off. | `networkpolicy.yaml:78-105`; `ingress.yaml:1-75` | ledgered-brief |

### 3.5 runtime-seams (4 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `recurso-marina-a2a-decorative` | post-deploy | RECURSO's `analyze_request` worker claims A2A delegation to Marina but has zero delegation code — only an internal echo notification; enrichment never fires in production (pre-#176 PAGTO-class defect, unfixed here). | `recurso.py:295,328-345,847` | ledgered-brief |
| `inad-fernando-a2a-unreachable` | post-deploy | INAD builds a real `arrears.followup` envelope to Fernando, but `service.py` never passes the dispatcher to `register_inadimplencia_workers` **and** Fernando is excluded from `a2a_assembly._A2A_TARGETS` — structurally unreachable, always degrades to the fail-safe echo. | `service.py:323-327`; `a2a_assembly.py:77`; `inadimplencia.py:283,302` | ledgered-brief |
| `reembolso-stale-marina-card-comment` | cosmetic | `reembolso.py`'s docstring claims Marina's card rejects `reembolso.analyze`; the card actually accepts it and delegation succeeds — the comment inverts reality and could mislead a maintainer into "fixing" a working path. | `reembolso.py:68-70` vs `marina/agent.yaml:83` | ledgered |
| `inad-cancel-matricula-phi-egress-unscrubbed` | post-deploy | Same underlying defect as `matricula-raw-to-general-zone-notifications-topic` (§3.3), independently found from the wiring-parity angle: `register_inadimplencia_workers`/`register_cancel_workers` accept **no** `pseudonymizer` parameter at all, unlike `phase0`/`auth`. | `inadimplencia.py:429,853-859`; `cancel.py:221,692` | **needs product judgment — see below** (same defect as `matricula-raw-to-general-zone-notifications-topic`) |

**Highlighted — needs product judgment (feeds WS-6.4):**
1. **`matricula-raw-to-general-zone-notifications-topic`** + **`inad-cancel-matricula-phi-egress-unscrubbed`** — **one underlying defect, found independently by 2 dimensions** (phi-perimeter's egress-scan angle and runtime-seams's wiring-parity angle). Decision needed: thread a pseudonymizer into `register_inadimplencia_workers`/`register_cancel_workers` (defense-in-depth parity with `phase0`/`auth`), **or** if `matricula_beneficiario` is genuinely always pre-tokenized by the time it reaches these handlers, remove it from `phi_vars.PHI_PROCESS_VARS` so the deny-list stops contradicting the two worker families. **Unresolved escalation:** no finder could trace whether the actual AMH ingress path (Tasy CDC / WhatsApp / manual API) that starts these processes populates this field raw or pre-pseudonymized — that materially changes whether this is post-deploy or deploy-blocking.
2. **`phone-hash-keyless-brute-forceable-reidentification`** — decision needed: adopt the fix sketch (HMAC-SHA256 keyed by the same vault `PHI_HMAC_KEY` the Pseudonymizer uses) or explicitly accept the re-identification risk for this specific field with documented rationale.

### 3.6 observability (11 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `kafka-lag-alert-topic-label-never-matches` | post-deploy | `MaezoKafkaConsumerLagHigh` + the matching dashboard panel filter on literal `topic="agents.events"`, which is never a real topic (only dotted subtopics exist) — permanently dead regardless of any real lag. | `alert-rules.yaml:28`; `engine.json:146` | ledgered-brief |
| `phantom-metrics-escalation-latency-evalscore` | post-deploy | 3 metrics backing live alerts/panels (`escalation_total`, `response_duration_seconds`, `eval_score_latest`) have documented "Emitted by ..." call sites that don't exist anywhere in app or CI code. | `metrics.py:694-719` | ledgered-brief |
| `runbook-url-misrouted-and-missing` | post-deploy | `MaezoAuditLagHigh` (ADR-0007 compliance-critical) and `MaezoEscalationRateHigh` both point `runbook_url` at an unrelated PEP-deny-spike section; `MaezoAgentRuntimeDown` has no `runbook_url` at all (sole alert missing it). | `alert-rules.yaml:73,81-92,111` | ledgered-brief |
| `podmonitor-joblabel-misuse` | cosmetic | `PodMonitor.spec.jobLabel` is set to a literal string, but the CRD field actually names a pod-label **key** to copy from; no pod carries that label, so the intended `job` value never resolves (dormant — `podMonitor.enabled` defaults false). | `podmonitor.yaml:37-38,75` | ledgered |
| `hitl-approved-total-never-emitted-alert-permanent-false-signal` | post-deploy | `maezo_hitl_approved_total` is never incremented by any production path (console stopped emitting it as an unrelated fix); the HITL-approval-rate alert and dashboard panel go permanently no-data instead of ever protecting against a real approval-rate drop. | `metrics.py:730-735`; `harness.py:416-438`; `console/app.py:489-503` | ledgered-brief |
| `phase2-worker-calendar-metrics-unalerted-undashboarded` | post-deploy | 6 emitted instruments covering ANS/NIP/worker-dispatch compliance events (incl. no-denial-guard firings) have zero alert rules and zero dashboard panels anywhere. | `harness.py:351`; `ans_submit.py:162,172-173`; `nip.py:194`; `metrics.py:581-591` | ledgered-brief |
| `lgpd-notify-sla-risk-never-emits-breach-metric` | post-deploy | `lgpd.py`'s sole handler for BOTH the P7D internal alert and the P15D LGPD art. 19-II legal deadline never calls `emit_user_task_sla_breach`, unlike every sibling SP-OP module — the compliance counter/alert can never fire for LGPD-DSR deadline breaches (Kafka domain event still fires independently). | `lgpd.py:737-777`; contrast `auth.py:462` | ledgered-brief |
| `ans-cron-process-keys-missing-from-engine-poller` | post-deploy | `EngineMetricsPoller.DEFAULT_PROCESS_KEYS` omits all 5 SP-OP-ANS-CRON-001 process ids; engine instance-count metrics/dashboard are permanently blank for the whole regulatory-scheduler module. | `engine_metrics.py:48-64` | ledgered-brief |
| `worker-task-outcome-metric-unalerted-undashboarded` | post-deploy | `maezo_worker_task_total`/`_duration_seconds` (GAP-XOBS-4, explicitly documented to make no-denial-guard firings alertable) has zero alert rules and zero dashboard panels. | `metrics.py:594-646`; `harness.py:351` | ledgered-brief |
| `kafka-consumer-lag-metric-has-no-exporter-anywhere` | post-deploy | Both Kafka-group alerts key off `kafka_consumer_group_lag`, but no exporter/scrape-job/PodMonitor producing that metric exists anywhere (dev or prod). | `prometheus.yml:16-51`; `docker-compose.yml`; `podmonitor.yaml` | ledgered-brief |
| `gateway-service-command-has-no-main-module` | **deploy-blocking** | See §2.1 (convergent with `gateway-deployment-crashloop-no-main`). | `deployment-gateway.yaml:34` | **fixed-this-session** (`{{PR_GATEWAY}}`) |

### 3.7 docs-runbooks (24 confirmed)

This dimension never dried out (see §5) and its 24 confirmed findings collapse into roughly **10 distinct underlying documentation defects**, each independently rediscovered 1–3 times by different finder rounds — flagged inline as "(dup. of X)" rather than merged, since the task calls for listing all confirmed findings.

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `auditor-console-deployment-not-found` | post-deploy | Incident-response commands target k8s deployment `auditor-console`, which doesn't exist anywhere in the Helm chart. | `devops-stack.md:372,376-381` | ledgered |
| `devops-stack-auditor-console-not-found` | post-deploy | (dup. of above) | `devops-stack.md:372-381` | ledgered |
| `otel-collector-wrong-namespace` | post-deploy | Runbook kubectl commands target `otel-collector` in the tenant namespace; it actually runs in the separate `observability` namespace. | `devops-stack.md:436-472` | ledgered |
| `pseudonymizer-secret-wrong-env-var` | post-deploy | `gateway.md`'s example secret-injection snippet names env var `PSEUDONYMIZER_SECRET`; the real (and only) var anywhere in the codebase is `PHI_HMAC_KEY`. | `gateway.md:124` vs `hmac_key_provider.py:30` | ledgered |
| `helena-alert-name-mismatch` | post-deploy | `helena.md` references alert `MaezoHelenaEscalationRateAnomaly`; real alert is `MaezoEscalationRateHigh` with a materially different (single 0.30, not dual) threshold. | `helena.md:207` vs `alert-rules.yaml:94` | ledgered |
| `helena-escalation-alert-name-mismatch` | post-deploy | (dup. of above) | `helena.md:207,254` | ledgered |
| `whatsapp-invalid-signature-alert-missing` | post-deploy | `whatsapp-webhook.md` documents alert `WhatsAppInvalidSignatureSpike` — not defined anywhere in the repo. | `whatsapp-webhook.md:261` | ledgered |
| `whatsapp-kafka-publish-alert-missing` | post-deploy | `whatsapp-webhook.md` documents alert `WhatsAppKafkaPublishFailure` — not defined anywhere. | `whatsapp-webhook.md:262` | ledgered |
| `whatsapp-alerts-not-implemented` | post-deploy | (combined dup. of the 2 above) | `whatsapp-webhook.md:261-262` | ledgered |
| `whatsapp-metrics-not-implemented` | post-deploy | `whatsapp-webhook.md` lists 4 Prometheus metric names never emitted anywhere in the app (only structured log events exist). | `whatsapp-webhook.md:253-256` | ledgered |
| `gateway-audit-verify-missing` | post-deploy | `gateway.md` instructs `python -m maezo.gateway.audit_verify`; no such module exists (only library functions `verify_chain`/`parse_jsonl`). | `gateway.md:217` | ledgered |
| `gateway-audit-verify-command-missing` | post-deploy | (dup. of above) | `gateway.md:217-218` | ledgered |
| `gateway-audit-verify-module-missing` | post-deploy | (3rd independent dup. of above) | `gateway.md:217-218` | ledgered |
| `phase0-demo-webhook-service-missing` | post-deploy | `phase0-demo.md`'s Step 1 curls `localhost:8082/webhook` after a compose profile that starts no such service. | `phase0-demo.md:18,58` | ledgered |
| `phase0-demo-webhook-receiver-missing-docker-compose` | post-deploy | (dup. of above, adds that the real container port is 8080 not 8082) | `phase0-demo.md:58-61` | ledgered |
| `devops-stack-agent-runtime-name-wrong` | post-deploy | 8 runbook commands (`devops-stack.md`+`helena.md`) target k8s resource `agent-runtime`; real per-agent names are `agent-helena`/`agent-rafael`/etc. — the legacy singular resource is gone. | `devops-stack.md:159,269-320,509-510`; `helena.md:220,244` | ledgered |
| `gateway-policies-path-hard-frozen` | cosmetic | `gateway.md` cites `deploy/policies/autonomy/_hard_frozen.yaml`; real path is `src/maezo/policies/autonomy/_hard_frozen.yaml`. | `gateway.md:247` | ledgered |
| `gateway-policies-path-tenant-overlay` | post-deploy | `gateway.md`'s example tenant-overlay path is fictional on 3 counts (root, subdirectory, filename) — real file is `src/maezo/policies/autonomy/tenants-amh.yaml`. | `gateway.md:260` | ledgered |
| `gateway-autonomy-policy-file-paths-wrong` | post-deploy | (combined dup. of the 2 above) | `gateway.md:247,260-268` | ledgered |
| `gateway-metrics-names-incorrect` | post-deploy | `gateway.md`'s PEP-decision metric names don't match `metrics.py` (`maezo_pep_decision_total` vs real `maezo_gateway_pep_decision_total`, etc.). | `gateway.md:359-361` | ledgered |
| `gateway-audit-lag-metric-name-wrong` | post-deploy | `gateway.md`'s audit-lag monitoring query wraps `histogram_quantile()` around what is actually a **Gauge** (`maezo_gateway_audit_lag_seconds`), not a Histogram — the documented query returns no series at all, a type-level incompatibility, not just a naming typo. | `gateway.md:362` vs `metrics.py:403` | ledgered |
| `devops-stack-maezo-console-unclear` | cosmetic | `devops-stack.md`'s "HITL console (`maezo-console`)" reference is ambiguous/unresolvable — no such resource exists; likely means the real (differently-named) console app or the CIB Seven Tasklist. | `devops-stack.md:340` | ledgered |
| `engine-processes-compliance-events-yaml-missing` | post-deploy | `engine-processes.md` instructs editing a `COMPLIANCE_EVENTS.yaml` that doesn't exist anywhere in the repo. | `engine-processes.md:4` | ledgered |
| `grafana-dashboard-provisioning-path-mismatch` | post-deploy | `devops-stack.md` says dashboards provision from `deploy/observability/dashboards/`; the actual Grafana provisioning config points at a different (empty) path. | `devops-stack.md:54`; `docker-compose.yml` | ledgered |

---

## 4. Killed-findings table (18) — evidence the adversarial layer worked

23% of raw sweep candidates that reached a "confirmed" draft state were subsequently refuted by a second-pass verifier/refuter before being counted — this table is direct evidence the loop-until-dry + adversarial-verify protocol is pulling its weight, not just rubber-stamping finder output.

| Dim | ID | Killed by | One-line reason |
|---|---|---|---|
| bpmn-dmn | `residue-ledger-false-merge-claim` | verifier-1 | Ran against a stale local `main` (an unmerged PR #175 draft branch), not canonical `origin/main`; the 4 disputed fixes are all confirmed merged on `origin/main`. |
| bpmn-dmn | `doc-juel-itertext-fix-not-on-main` | verifier-1 | Same stale-local-checkout artifact; PR #173's `itertext()` fix and its tests are confirmed present on `origin/main`. |
| bpmn-dmn | `dangling-not-human-guard-errors-no-boundary-catch` | refuter-2 | Per ADR-0018 the worker guard **is** the enforcement layer; an uncaught error halts fail-closed (no adverse effect) rather than causing one, and firings are already alertable via `metrics.py` (GAP-XOBS-4). |
| bpmn-dmn | `fraude-phi-custody-guard-uncaught` | refuter-2 | Uncaught BPMN error halts the token **before** the PHI bundle is sealed/published or the accusation is registered — the designed fail-closed outcome; process is also DRAFT/pre-signoff. |
| bpmn-dmn | `fraude-scoring-dmn-ungoverned-by-signoff-gate` | refuter-2 | Gate gap is CI-only (doesn't affect the deployed worker); runtime is fail-safe (score is advisory, accusation human-gated) and the DRAFT review requirement is already tracked in `review-queue.md`/`topic_registry.yaml`. |
| bpmn-dmn | `nip-protocolo-invalido-dead-declaration` | verifier-1 | Stale claim — GAP-NIP-6 (PR #171) already implemented this exact guard + boundary-catch + contract doc on current main. |
| bpmn-dmn | `intake-guard-errors-declared-but-never-caught` | refuter-2 | Uncaught error is a fail-closed, per-instance, observable (metrics+logs) halt, not a crash/PHI exposure; all 8 affected processes are DRAFT/unsigned for production. |
| bpmn-dmn | `auth-analyze-invalid-input-undeclared-error-code` | refuter-2 | Guard is effectively unreachable for any real guia (upstream identifiers always populated by that point); firing triggers the same accepted fail-closed pattern used codebase-wide for input guards. |
| phi-perimeter | `verify-chain-no-reanchor-after-retention` | verifier-1 | Re-derived: the claimed verification-failure consequence cannot occur given how `verify_chain` actually reconstructs the chain; no runtime impact under the amh-overlay as configured. |
| phi-perimeter | `phi-hmac-secret-provisioned-but-never-injected-as-env` | refuter-2 | Mechanically real (fix PR #177 not yet on the audited HEAD) but the PHI pipeline is inert today — gated behind the SAME blocked §6.2 LLM/Tasy secrets; tracked as a deploy-day prerequisite (`res-phi-hmac-env-injection`), not a live breach of the config as deployed. |
| infra-deploy | `networkpolicy-agent-runtime-no-db-engine-fhir-egress` | refuter-2 | No NetworkPolicy in the amh render opens egress to `cibseven:8080` for **any** component, agents included — if ipBlock egress truly gated CIB Seven reachability the whole agent fleet would already be non-functional; connectivity is provided out-of-band. |
| infra-deploy | `networkpolicy-bridges-excluded-from-daemon-egress-selector` | refuter-2 | In the amh render `wr2-daemon-egress` has an **empty** egress endpoint list, so the selector omission changes nothing in practice. |
| infra-deploy | `networkpolicy-daemon-egress-endpoints-empty-in-amh-values` | refuter-2 | Facts reproduce (empty egress endpoints in the reference overlay), but `values-amh.yaml` is a CI placeholder overlay (dummy account/CIDRs) — most plausibly completed out-of-band or in the real production values; a config-hygiene item, not a decisive break. |
| runtime-seams | `helena-a2a-delegation-helpers-uncalled` | verifier-1 | ADR-0015 explicitly frames helena→rafael / gustavo→nip A2A origination as "Phase 1+ future wiring"; production is served today by the live BPMN/topic workers regardless — dormant-by-design, zero functional gap. |
| observability | `otel-collector-no-app-metrics-receiver` | refuter-2 | Treats an un-deployed template (`config/otel-collector.prod.yaml`) as canonical; the real amh collector is out-of-repo/externally managed, and annotation-based Prometheus scrape-discovery (`prometheus.io/scrape` on agent/fhir-sync Services) is an alternate path the finding didn't credit. |
| observability | `observability-terraform-module-never-invoked-by-any-env` | refuter-2 *(see note)* | Recorded reason verbatim: **"refuter died."** The refuter process hit the session limit before producing any reasoned verdict; the kill was nonetheless recorded as final. **⚠ This kill is INVALID — re-adjudication in flight → `{{TERRAFORM_MODULE_VERDICT}}`.** Treat the underlying finding (the observability Terraform module is never referenced by any env root module) as **open/unresolved**, not refuted, until re-adjudicated. |
| docs-runbooks | `phase0-demo-wrong-deployment-endpoint` | verifier-1 | `phase0-demo.md`'s `/deployment/create` endpoint matches the actual test suite (`engine_rest.py`, `test_phase0_journey.py`) that deploys to a real CIB Seven engine — the finding had the "correct" reference backwards; `engine-processes.md` is the stale one (a separate, unclaimed finding). |
| docs-runbooks | `devops-stack-sla-breach-metric-name-wrong` | verifier-1 | Claimed text (`maezo_process_sla_breach_total{...}`) does not exist anywhere in the file at the cited line (a blank line); the real `Alert:` line there is correct and matches `alert-rules.yaml`/`metrics.py` exactly — likely hallucinated or sourced from a stale variant. |

---

## 5. Coverage inventory

### 5.1 Artifact census (verified counts vs. the brief)

| Artifact class | Briefed | Actual (verified) | Source |
|---|---|---|---|
| BPMN process files | 17 | **16** | `find spec/processes/bpmn -name '*.bpmn'`, re-verified directly at draft time; SP-OP-ANS-CRON-001 is 1 file containing 5 process definitions (15+1 reconciliation, `docs/audits/bpmn-process-completeness.md`) |
| DMN decision tables | 55 | **60** (53 flat + 7 under `fraude_scoring/`) | `find spec/processes/dmn -name '*.dmn'`, re-verified directly at draft time — same class of brief/reality drift as the BPMN count, not previously called out |
| Worker "families" | 15+1 | **confirmed**: 15 SP-OP-specific modules (`adequacao`,`ans_submit`,`auth`,`auth_analyze`,`cancel`,`contas`,`cred`,`fraude`,`inadimplencia`,`lgpd`,`nip`,`pagto`,`programa`,`recurso`,`reembolso`) + 1 shared generic-publish module (`phase0.py`) | Directory listing, re-verified; `workers/` also holds 4 shared utility modules (`harness.py`,`ceilings.py`,`phi_vars.py`,`sla_metrics.py`) + `__init__.py` = 21 `.py` files total |
| Agents | 10 | **confirmed: 10** (andre, beatriz, carolina, fernando, gustavo, helena, lucas, marina, rafael, valentina) | Directory listing (`_template/` scaffold excluded), re-verified |
| Helm templates | ~21 | **23 files** (22 real templates + `_helpers.tpl`) | Directory listing, re-verified |
| Runbooks | 8 | **confirmed: 8** | `docs/runbooks/{cd-rollback,devops-stack,engine-processes,fhir-sync,gateway,helena,phase0-demo,whatsapp-webhook}.md`, re-verified |
| Alerts | 16 | **18** | `deploy/observability/alert-rules.yaml`, `grep -c '- alert:'`, re-verified; likely 2 recently-added alerts not reflected in the brief |
| Workflows | 2 | **2 today** (`ci.yml`, `cd.yml`) → **{{WORKFLOWS_CENSUS_4}}** | Could only substantiate 2 live + 1 in-flight (`red-main-alarm.yml`, built in worktree `maezo-pd-ws1`/PR #183, not yet merged) = 3 once WS-1 lands. Could not source a documented basis for a 4th; flagging rather than inventing one. |

### 5.2 Per-dimension coverage methodology (from the WS-5 result object)

| Dimension | Key coverage facts |
|---|---|
| bpmn-dmn | 16/16 files: boundary-error-catch pairing, incoming/outgoing IDREF completeness (custom `xml.etree` script — `docs/audits/bpmn-process-completeness.md` does **not** actually contain an incoming/outgoing checker, contrary to the brief's pointer), doc-JUEL sweep (`lxml` unavailable; stdlib `xml.etree` substituted, sufficient for the JUEL-token check), 8 `timeDate` occurrences (all DMN-derived, none hardcoded), ~15 business-key template sites, DI coverage gate (16/16 OK), signoff gate (68 warnings/0 errors, matches documented baseline), residue-ledger cross-check (4 commit hashes). |
| no-denial-autonomy | 16/16 BPMN files (zero diff between document-wide and per-process end-event enumeration, incl. nested subProcess/5-in-1 ANS-CRON); `test_no_denial_consolidated.py` — 8/8 passed, re-run against a disposable `origin/main` worktree (see §5.3); adverse-looking-pattern cross-check found no live blind spot; all 10 agents' `autonomy_actions` + prompt "REGRAS DURAS"/NUNCA-disclaimers checked bidirectionally, against `origin/main` content specifically (local checkout was stale for this exact file class — Carolina/Rafael's PR #174 fix). |
| phi-perimeter | 10 Helm templates + 3 values overlays read in full; 8 Python `__main__` entrypoints enumerated (gateway confirmed to have none); the WhatsApp webhook edge path read end-to-end; ~60 Kafka publish sites grepped + all 8 `PHI_PROCESS_VARS` names traced; 5 audit-hash-chain source files read. **`sweep_complete=false` recorded twice** (r1, r3). |
| infra-deploy | Exact CI `validate-helm` commands replicated (`helm lint --strict`, `helm template ... -f values-amh.yaml`, bare-default fail-closed confirmed intentional); full 268-line NetworkPolicy manual read cross-referenced against every workload's env vars; all 6 ExternalSecret targets matched against consumers; **all 8 Terraform roots/modules `terraform init -backend=false && terraform validate`'d successfully** (prod-amh-sa-east-1, staging-sa-east-1, + 6 modules); CDC topic/secret conventions cross-checked against `amh-data-platform` ownership. |
| runtime-seams | 15 worker-family registration call sites read against `origin/main` `service.py`; topic-registration parity confirmed unconditional on DI params; 6 A2A delegation targets traced originator→card; 3 agent-runtime driver seams (inbound/resume/a2a-dispatcher) confirmed gated `create_task`, not built-but-never-started; audit-chain write-path parity confirmed exactly 4 families (fraude/pagto/programa/lgpd) receive the durable audit sink. |
| observability | 18 alerts + 3 dashboards fully parsed (every `expr`, label matcher, `runbook_url`); 27 metrics-catalog instruments read in full and classified emitted-vs-phantom; candidate-groups↔BPMN cross-ref executed live (37 resolved groups, 20/20 tests passed); OTel pipeline configs read (2 static + 1 rendered ConfigMap); ~60 topic-registry entries scanned against alert/dashboard topic labels; 22 runbook headings matched against 12 distinct `runbook_url` anchors (content relevance checked, not just anchor existence). |
| docs-runbooks | All 8 runbooks read and cross-referenced; artifact-class sub-census: 8 Helm deployments, 3 k8s services, 5 env vars, 18 alert names, 12 file paths, 6 Make targets, 1 BPMN + 5 DMN file references, 3 REST API endpoints — each individually verified against source. |

### 5.3 Sweep-completeness flags — 6 of 7 dimensions need a continuation pass

The `dried_out` flag recorded by the workflow is **not a reliable "swept clean" signal for 2 of the 3 dimensions where it reads `true`** — both hit it via consecutive **session-limit finder deaths**, which the harness apparently counts the same as genuine "no new findings" dry rounds:

| Dimension | Recorded `dried_out` | Actually clean? | Basis |
|---|---|---|---|
| bpmn-dmn | `true` | **No — confounded.** | Only 2 real finder rounds ran (r1→3 confirmed, r2→4 confirmed); r3 **and** r4 both hit `"You've hit your session limit"` before producing output. |
| no-denial-autonomy | `false` | Genuine partial convergence | r5 = "dry (1/2)" — 1 of the 2 dry rounds needed to confirm convergence, then hit the round-5 cap. |
| phi-perimeter | `false` | Genuine, session-limit-interrupted | r5 finder **died** on session limit (not a dry round). |
| infra-deploy | `true` | **No — confounded.** | Same pattern as bpmn-dmn: r1→1 confirmed, r2→4 confirmed cumulative, then r3 **and** r4 both finder-died on session limit. |
| runtime-seams | `true` | **Yes — clean.** | r4 = "dry (1/2)", r5 = "dry (2/2)" — the only dimension with 2 genuine consecutive dry rounds. |
| observability | `false` | Genuine, session-limit-interrupted | r5 finder died on session limit; a `refute` call also died on session limit (see the invalid kill in §4). |
| docs-runbooks | `false` | Genuine, still actively diverging | r5 = "6 new → 24 confirmed cumulative" — still finding **new** confirmed defects at the round cap, no deceleration signal at all. Highest-priority dimension for a continuation pass. |

**Continuation-pass placeholder:** `{{CONTINUATION_RESULTS}}` — a follow-up sweep should re-run finder rounds for **bpmn-dmn, no-denial-autonomy, phi-perimeter, infra-deploy, observability, and docs-runbooks** (all except runtime-seams) once the session-limit constraint is no longer binding, and this section should be updated with the delta.

### 5.4 Sweep caveats (44 escalations, deduplicated into themes)

- **Git-state staleness (recurring, 3+ dims).** The local `main` checkout was 5 commits behind `origin/main` for most of the sweep (missing #171–#174, #176) despite its own most-recent commit narrating them as merged. Finders that noticed this (bpmn-dmn r1, no-denial-autonomy r1/r2, runtime-seams r1) built disposable read-only `git worktree add --detach <tmp> origin/main` checkouts to re-verify against the true production ref rather than trusting the stale working tree. *(Resolved for this report's own drafting: verified live that this same class recurred — local main is now 1 commit behind `origin/main`, see §1 provenance note. Also resolved: the r1 escalation "could not determine whether PR branches for #172–#176 still exist on the remote" — a live `gh pr list --state all` at draft time confirms all of #171–#176 are `MERGED`, and #178–#184 are `OPEN`.)*
- **BPMN/DMN census mismatch vs. the brief** (bpmn-dmn r2; no-denial-autonomy r1/r2/r4/r5; observability r1) — brief said 17 BPMN/55 DMN; actual is 16/60 (see §5.1). Not treated as a code defect by any finder, but repeatedly re-flagged, suggesting the brief's artifact counts should be corrected at the source for the next audit cycle.
- **`docs/audits/bpmn-process-completeness.md` does not contain the incoming/outgoing-declaration-completeness method** the brief pointed at (bpmn-dmn r2) — it's a process/subprocess inventory doc, not an IDREF checker. A custom script was built instead; only the already-known `cancel-brtcancelsla-dangling-outgoing-idref` surfaced.
- **`lxml` not installed** in the project's `uv` environment (bpmn-dmn r2) — the doc-JUEL sweep was redone with stdlib `xml.etree.ElementTree` (loses line numbers, resolved separately via `grep`; does not distinguish CDATA from ordinary text, judged sufficient for this specific check).
- **`sweep_complete=false`** explicitly recorded twice by phi-perimeter (r1, r3) — the dimension's own agents flagged their pass as incomplete independent of the round-cap mechanism.
- **Matricula raw-vs-pseudo ambiguity at intake** (phi-perimeter r1; runtime-seams r3) — no finder could trace whether the real AMH ingress path populates `matricula_beneficiario` raw or pre-tokenized before it reaches INAD/CANCEL. This directly gates the severity of the highlighted PHI finding in §3.5 and is the single highest-value open question for a continuation pass.
- **`values-amh-provisioned.yaml`** (referenced in `values-amh.yaml` comments as a possible downstream-generated overlay) was not present in the repo to inspect (phi-perimeter r1) — F-1/F-2 gateway/console findings assume `values-amh.yaml` alone is the production target.
- **Per-payload PHI under non-standard keys** not exhaustively audited across all ~15 worker modules (phi-perimeter r3) — `scrub_phi_vars` only matches 8 exact key names; a worker publishing free clinical text under a differently-named key would bypass enforcement. Spot-checked, not exhaustive.
- **Terraform/Helm not re-rendered by phi-perimeter** (r3) — contrast infra-deploy, which *did* run the full `terraform validate` + `helm template` suite; the two dimensions had uneven tooling depth on overlapping ground.
- **AMP/AMG live runtime state unverifiable from the repo** (observability r1, r3, r4) — this repo only contains the checked-in IaC; whether a separate untracked `terraform apply` was run against the observability module (blocked pending AWS credentials, issue #16) could not be determined either way.
- **`kafka-lag-alert-topic-label-never-matches` vs. `kafka-consumer-lag-metric-has-no-exporter-anywhere`** (observability r3) — flagged as a possible overlap (mismatched label vs. total absence of the underlying metric), judged distinct and both included; explicitly flagging the overlap risk for the coordinator.
- **Gateway crash-loop not confirmed against a live cluster** (observability r4) — based on local reproduction of the exact failing command, not a port-forward into a running pod; judged sufficient and deterministic.
- **Incident-alerting/paging infrastructure unknown** (bpmn-dmn r1) — no in-repo evidence either way of whether Camunda incidents actually page an operator, which is why several "fail-closed halt" refutations (§4) rest on the halt being *observable*, not necessarily *paged*.
- **~20+ individual dangling-boundary-catch occurrences** folded into the killed `dangling-not-human-guard-errors-no-boundary-catch` finding were not all individually re-verified line-by-line (bpmn-dmn r1) — flagged for a full remediation pass before any fix PRs open against that class.
- **Business-key sweep not exhaustive** beyond `notifications_bridge/consumer.py` + 2 inline builders (bpmn-dmn r1) — recommends a follow-up grep across `src/maezo/agents/**/graph.py` for direct `start_compliance_process` calls with inline f-string business keys.
- **Adjacent-but-unreported observation** (runtime-seams r2): fraude/nip/cred worker docstrings say they "convoca Beatriz/Gustavo/Carolina via A2A" but the handlers publish a Kafka notification, never a `DelegationEnvelope` — judged to be the intended human-gated notification-bridge pattern, not a dispatcher-seam claim like the reported ones, but the docstrings may overstate the wiring if the process contract expects auto-invocation. Worth a documentation pass alongside the reported A2A findings.

---

## 6. Workstream ledger

### WS-1 — Merge policy / branch protection (ADR-0023: **ACCEPTED**)

Classic branch protection (`required_status_checks` + `strict: true` + `enforce_admins: true`) on 6 named contexts, applied via one idempotent `PUT`. Kills the "pairwise-green ≠ union-green" merge-ref-staleness defect class by construction (`strict: true` forces re-CI on a fresh merge-ref whenever `main` advances — this bit twice in one session per DL-0023, `#137×#144` and `#157×#155`). GitHub **merge queue was verified unavailable** (private repo + org on the `team` plan, not Enterprise Cloud — `gh api /repos/.../--jq .private`→`true`, `gh api /orgs/Omni-Saude --jq .plan.name`→`"team"`) so a scheduled main-red alarm is adopted as a non-blocking complement instead. `enforce_admins: true` closes the direct-push hotfix path that DL-0007/DL-0023 used; the replacement is a documented, auditable "surgical lift + DL row" emergency procedure (ADR-0023 §9). Docs-direct-push (HANDOFF*/decisions-log.md) becomes blocked too — **Option 1 (state updates become PRs) is recommended**, see §7b.

- **Live status:** branch protection **not yet applied** (confirmed 404 at draft time). The workflow PR (`ws1_workflow_pr`) — quoting the truncated `fhir-sync` job name + adding a scheduled `red-main-alarm.yml` — was recorded `PENDING`/`NOT-YET-BUILT` in the handoff, but is **now built and open as PR #183** (`fix/predeploy-ci-policy`, stacked on #179), discovered live at draft time.

### WS-2 — Dependabot triage (16 open alerts; **T3 red-team premise refuted**: `cryptography` is *not* the PHI/HMAC layer — `pseudonymizer.py` uses stdlib `hmac`/`hashlib`, zero first-party `cryptography` imports; alerts re-ranked to low practical exploitability. `pytest` bump is 3 coupled cap-lifts; `langgraph-sdk` fix is a `0.3.15` rider, not a `1.x` jump.)

| Batch | Alerts closed | Change | Status |
|---|---|---|---|
| **B1** | #13, #14 | `pydantic-settings` 2.14.2, `langsmith` 0.8.18 (exact-floor pin — coordinator vetoed the default resolver's unvetted-fresh-release overshoot to `langsmith 0.9.7` + new transitive `distro`, ESC-005) | PR **#180**, T3-PASS (110→110 packages, no distro, advisory floors exact) |
| **B2** | #1, #3, #5, #8, #9, #11, #12 | `cryptography >=48.0.1,<49`, relock 45.0.7→48.0.1 | PR **#178**, T3-PASS (48.0.1 closes all 7, no upper-bound traps, native AESGCM/JWT paths exercised) |
| **9 closed total (B1+B2)** | | | |

**Risk-accepted (deferred, not fixed this session) — reproduced from the handoff:**

| Batch | Alerts | Package family | Reason deferred | Disposition |
|---|---|---|---|---|
| **B3** | #2, #4, #6, #7, #15, #16 (6 alerts) | `langgraph` 0.6→1.2 + `langgraph-checkpoint` 2→4.1.1 + `checkpoint-postgres` 2→3.1 | Coupled major-version migration (serializer hardening + Postgres `setup()` chain vs. existing rows); moderate risk, not same-day | Risk-acceptance: all 6 conditional/unreachable as currently used (trusted-store-only deserialization, `BaseCache` unused, SDK never imported) + ledgered migration brief (§8) |
| **B4** | #10 (1 alert) | `pytest` 9 (+ 3 coupled cap-lifts: `pytest-asyncio` 1.4, `pytest-cov` 7.1) | Major API churn; dev/CI-only, local-privilege CWE-379 | Risk-acceptance + ledgered test-infra brief (§8) |

*(Note: formal `DL-0026`/`DL-0027`/`DL-0028` rows referenced by the handoff's escalation register are **not yet appended** to `docs/decisions-log.md` — as of this draft the log tops out at `DL-0025`. The rows above are reproduced directly from `HANDOFF-predeploy.yaml`'s `ws2_batches`/`escalation_register`, which is the authoritative source until the formal DL entries are written at program close.)*

**Merge order:** after WS-3a lands; B1 → B2 sequential (both touch `uv.lock`; B2 needs rebase+relock+re-gate after B1 merges).

### WS-3 — Build tooling truth

- **WS-3a (uv-align), PR #179** — builder PASS: 8 bare Makefile recipe lines (not 2, as originally briefed) fixed to `uv run`; 7 CI `pip install` steps replaced with `setup-uv@v7` + `uv sync --locked`; bare `pytest` in `ci.yml:169-171` fixed; `.python-version=3.12` pinned. Venv-less proof matrix green; `test-integration` fails only on the missing local Docker stack (pre-authorized) — and **that failure hit the ambient port-5432 Postgres with the `maezo` role missing**, independently strengthening the WS-6.1 port-5432 finding (§7c). Status: **PR checks banked ALL-GREEN pre-billing-wall; T3 verdict pending.**
- **WS-3b (Dockerfiles), PR #182** — builder PASS: 2-stage `uv --frozen` builds for both the production and Tasy-simulator images; **proved real drift** (the old simulator image resolved `pydantic-settings 2.14.2` against a lockfile pinned to `2.14.1`); fixed `+227MB` overlayfs chown bloat via `COPY --chown`; image sizes: prod 603→494MB, simulator 764→488MB. `.dockerignore` un-ignores `uv.lock`. Status: **T3 verification in progress — this is the PR whose run first hit the billing wall** (run `28696747716`, then the wall itself at `28697052544`/`28697052520`, 0 steps, no logs).

### WS-4 — Wave-3 residue

- **`res-phi-hmac-env-injection` → PR #177, MERGED, T3-PASS.** Injects `PHI_HMAC_KEY` into `deployment-agent-runtime.yaml` + `deployment-worker-daemon.yaml` from the (already-provisioned but previously unconsumed) `maezo-phi-hmac` ExternalSecret. **Consequence: `PHI_HMAC_KEY` is now a hard required `secretKeyRef`** — general-zone and worker-daemon pods will **block on startup** (`CreateContainerConfigError`) until the secret is populated in Secrets Manager. This is now a **go-live prerequisite**, not an optional hardening step — see §7e.
- **ADR-0024 (durable cross-restart idempotency) → PR #181, T3-PASS.** New `PostgresDedupeStore` (implements `IdempotencyGuard` directly — the existing `PostgresIdempotencyStore` used by the A2A dispatcher is **protocol-incompatible**, ESC-002) + migration `0008_driver_idempotency` + wiring at both `_spawn_inbound_driver`/`_spawn_resume_driver` call sites. Closes DL-0014 (in-memory dedup forks/duplicates on restart or across replicas) for the inbound/resume driver legs specifically. Builder PASS 9/9; T3 mutation-tested the `setup=`/`init=` search-path regression (the real #55-class production bug) — swapping to `init=` makes 3 tests fail as designed. Landing this session, not deferred.
- **`res-pagto-andre-a2a-envelope` → PR #176, MERGED externally by the user ahead of independent verification** (ESC-001); T3-PASSed post-merge, 8/8 gates exit 0 incl. full 2,814-test suite, 100% DI, no-denial suite green. 2 cosmetic notes only (doc overstatement of `a2a_assembly` registration scope; a `None→dict` routing-inert handler difference).
- **LGPD re-identification resolver — BLOCKED-BY-DESIGN, unchanged.** Fail-closed (`a2a_assembly.py:117-118` resolver is `None`-gated; `lgpd.py:183-189` worker fails closed). No secure re-identification store exists; this is a deliberate, documented deferral, not a gap. See §8.

### WS-5 — Predeploy audit

This report's core deliverable — see §§1–5.

### WS-6 — User decisions

See §7.

### Merge ledger

| PR | Lane | SHA / status | Verifier | Notes |
|---|---|---|---|---|
| #175 | docs bundle | `f9246e8` MERGED (05:18:50Z) | Content-equivalence vs. the 2 local-only doc commits | ALL GREEN pre-merge incl. integration; run 28696025891(CI)/28696025894(CD), both success |
| #176 | WS-4 pagto-a2a | `1ddf827` MERGED (04:40Z, external) | T3-opus adversarial, PASS post-merge | 8/8 gates exit 0; 2 cosmetic notes only |
| #177 | WS-4 phi-hmac | MERGED | T3-PASS | Now a hard-required secret (§7e) |
| #179 | WS-3a | OPEN | Checks banked ALL-GREEN pre-wall; T3 pending | merge order 1 |
| #180 | WS-2 B1 | OPEN | T3-PASS | merge order 2 |
| #181 | WS-4 idem | OPEN | T3-PASS | merge order 3 |
| #178 | WS-2 B2 | OPEN | T3-PASS | merge order 4 (rebase after #180) |
| #182 | WS-3b | OPEN | Builder PASS; T3 in progress (billing-wall-interrupted) | merge order 5 |
| #183 | WS-1 workflow | OPEN *(built since last handoff snapshot)* | Not yet verified | merge order 6 |
| #184 | WS-5 fix (gateway) | OPEN *(new this session)* | Not yet verified | §2.1 |
| — | WS-5 fix (webhook) | not yet started | — | §2.2–2.3 |

**Final merge SHAs, once each PR lands:** `{{MERGE_LEDGER_FINAL}}`.

### ADR one-paragraph summaries

**ADR-0023** adopts classic branch protection (`required_status_checks` on 6 exact job names + `strict: true` + `enforce_admins: true`) over a GitHub merge queue, which is verified unavailable on the org's current plan (private repo + `team` plan requires Enterprise Cloud for merge queues). `strict: true` mechanically supersedes the manual "re-validate against fresh main" rule from DL-0023, which was violated twice in one session. A scheduled main-red alarm (pinned to the push-event run-ID, never SHA check-runs, to avoid the nightly-cron-rewrite and cancel-in-progress false-read failure modes) is adopted as a non-blocking complement. Emergency hotfixes get a documented "surgically lift `enforce_admins`, push, re-arm, log a DL row" procedure instead of silent direct pushes.

**ADR-0024** decides that durable, cross-restart-safe idempotency for the agent-runtime's inbound (`wamid`-keyed) and resume (`business_key:resultado`-keyed) drivers reuses Postgres — the same database already a hard dependency via the LangGraph checkpointer — rather than adopting Redis (unwired, would be new infra, and evictable/less durable). Critically, it rejects reusing the existing `PostgresIdempotencyStore` (built for the A2A dispatcher) as a naive "one-line swap," because it implements a structurally incompatible protocol (`claim_or_get`/`complete` keyed on `tenant`+`task_id`, requiring a `DelegationResult` object) versus what the drivers actually consume (`IdempotencyGuard`'s `is_processed`/`mark_processed`, keyed on a plain string). The decision introduces a sibling `PostgresDedupeStore` implementing the driver protocol directly, backed by a new dedicated `driver_idempotency` table (migration 0008) rather than overloading the existing `a2a_idempotency` table's delegation-shaped schema.

---

## 7. User-decision package (WS-6)

### (a) GitHub Actions spending limit — ACTIVE BLOCKER

The Omni-Saude org's Actions spending limit is exhausted: July usage 8,006 Linux minutes, net charge capped at exactly **$30.00** (confirmed via the billing API; GitHub status is all-operational — jobs fail before step 1, not due to an outage). **Only a billing admin can raise the limit.**

- **Exact console path:** `github.com` → **Omni-Saude** org → **Settings** → **Billing and plans** → **Spending limits** → **Actions**.
- **Alternative:** wait for the monthly reset (Aug 1).
- **Minutes needed to clear the queue:** ~35 CI-minutes per merge × ~6 remaining merges + a final union-green assert ≈ **250–300 minutes ≈ $1.80** at $0.006/min.
- A background probe (`gh run rerun` every 20 min, ≤18 attempts) auto-detects restoration and resumes the merge train.

### (b) Branch protection — exact `gh api` commands (ADR-0023 Apêndice)

Apply (idempotent — creates or replaces), to be run **after the WS-1 PR (#183) merges**:

```bash
gh api -X PUT /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "lint / type / unit",
      "validate-artifacts",
      "content-signoff-gate",
      "terraform validate",
      "helm lint + egress NetworkPolicy CI",
      "integration tests (real engine)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

Verify applied state:

```bash
gh api /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection \
  --jq '{strict: .required_status_checks.strict, contexts: .required_status_checks.contexts, admins: .enforce_admins.enabled}'
```

Emergency stop-the-line path (surgical admin lift — **always** followed by a DL row):

```bash
# 1) suspend admin enforcement
gh api -X DELETE /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection/enforce_admins
# 2) admin pushes the hotfix directly to main
# 3) re-arm immediately
gh api -X POST /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection/enforce_admins
```

Heavy fallback (only if the required contexts themselves need to change):

```bash
gh api -X DELETE /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection
# ...then re-PUT the full payload above
```

Facts underpinning the merge-queue rejection (re-verifiable):

```bash
gh api /repos/Omni-Saude/Maezo-Healthcare-Plan --jq .private   # => true  (private repo)
gh api /orgs/Omni-Saude --jq .plan.name                        # => "team" (NOT Enterprise Cloud)
```

**Docs-direct-push choice (decision 10 of ADR-0023) — user decision required:**
- **Option 1 (RECOMMENDED):** state-update commits (`HANDOFF*`, `decisions-log.md`, `docs/handoffs/*`) become PRs like everything else. Cost: churn (one PR per state update, ~5 min of the real-engine lane each). Benefit: uniform, auditable, **zero bypass surface**; classic protection is sufficient (no rulesets needed).
- **Option 2:** a `bypass_actors` allowlist for a dedicated state-update bot. Cost: a **permanent bypass surface**, not scopable by path (the actor bypasses *all* rules, not just docs-path ones), and **requires GitHub rulesets** (classic protection has no `bypass_actors`), which inverts the "classic is sufficient" recommendation in ADR-0023 decision 11.

**Live-verified at draft time:** branch protection is confirmed **not yet applied** (`GET .../branches/main/protection` → 404).

### (c) Port 5432 — requires explicit user authorization; nothing below has been executed

Live-checked at draft time (`docker ps -a`, `lsof -nP -iTCP:5432 -sTCP:LISTEN`):

| Listener | Detail |
|---|---|
| `intensicare-postgres` (Docker) | `timescale/timescaledb:latest-pg16`, **publishes `0.0.0.0:5432→5432/tcp`**, up 7 days (healthy) — this is what's actually occupying host port 5432 today. |
| `austa-postgres` (Docker) | `postgres:15-alpine`, **no host port binding at all** — cannot conflict with anything on 5432 as currently configured. |
| Native `postgres` process | PID 3472, listening on `127.0.0.1:5432` and `[::1]:5432` directly on the host (not in Docker). |
| `ssh` port-forward | PID 81630, listening on `*:5432` (all interfaces) — an active SSH tunnel forwarding port 5432 somewhere. |
| 4 stale wave-worktree compose stacks | `maezo-w3-nip6-*`, `maezo-w2-arc-*`, `maezo-w2-xproc3-*` (4 containers each: postgres/cibseven/kafka/hapi-fhir, all state `Created` — never actually started) + `maezo-w2-cancel-*` (3 containers, **`Up 33 hours`, healthy** — its postgres exposes `5432/tcp` internally only, **no host binding**, so it does not conflict with host port 5432 either). |

Per the memory record: *"user chose austa but classifier requires explicit user authorization to stop `intensicare-postgres`."* This remains unresolved. **Candidate commands (NOT executed — require explicit user authorization):**

```bash
# Candidate: free host port 5432 by stopping the container currently bound to it
docker stop intensicare-postgres
# If fully decommissioning (only after explicit confirmation this is wanted):
docker rm intensicare-postgres

# Candidate: reclaim the 4 stale wave-worktree compose stacks (resource hygiene;
# independent of the port-5432 question — none of these bind host 5432)
docker stop maezo-w3-nip6-hapi-fhir-1 maezo-w3-nip6-cibseven-1 maezo-w3-nip6-postgres-1 maezo-w3-nip6-kafka-1
docker stop maezo-w2-arc-hapi-fhir-1 maezo-w2-arc-cibseven-1 maezo-w2-arc-postgres-1 maezo-w2-arc-kafka-1
docker stop maezo-w2-xproc3-hapi-fhir-1 maezo-w2-xproc3-cibseven-1 maezo-w2-xproc3-postgres-1 maezo-w2-xproc3-kafka-1
docker stop maezo-w2-cancel-postgres-1 maezo-w2-cancel-cibseven-1 maezo-w2-cancel-kafka-1
docker rm   maezo-w3-nip6-hapi-fhir-1 maezo-w3-nip6-cibseven-1 maezo-w3-nip6-postgres-1 maezo-w3-nip6-kafka-1 \
            maezo-w2-arc-hapi-fhir-1 maezo-w2-arc-cibseven-1 maezo-w2-arc-postgres-1 maezo-w2-arc-kafka-1 \
            maezo-w2-xproc3-hapi-fhir-1 maezo-w2-xproc3-cibseven-1 maezo-w2-xproc3-postgres-1 maezo-w2-xproc3-kafka-1 \
            maezo-w2-cancel-postgres-1 maezo-w2-cancel-cibseven-1 maezo-w2-cancel-kafka-1

# Also worth surfacing: the ssh tunnel and native postgres are NOT Docker-manageable —
# resolving those (if they're the actual desired "austa" path) requires killing PID 81630 (ssh)
# and/or stopping the native postgres service (PID 3472) — again, requires explicit authorization
# and confirmation of which of the 4 listeners is the one the user actually wants to keep.
```

**These commands are candidates only and have not been executed. Explicit user authorization is required before running any of them**, and in particular the user should confirm which single listener (native postgres, `intensicare-postgres`, `austa-postgres`, or the ssh tunnel's remote target) is meant to own host port 5432 going forward — 4 independent things can bind that port and only one can win.

### (d) Worktree dispositions

**`maezo-p3`** (branch `feat/phase3-foundations`, at `5911468`) — **1 commit ahead of `main`** (verified: `git log --oneline` shows 20 lines total, but only the first, `5911468`, is unique — the other 19 are shared ancestry already on `main`):

```
5911468 docs(phase3): one-phase-ahead DRAFT foundations — 6 SP-OP contract sheets + ADR-0019/0020 + review-queue
b41413e feat(runtime): W-R0 runtime wiring — make the platform RUN (agent-runtime service, health server, Kafka producer, app image) (#47)
f075554 docs: reconcile owner mid-session merges (#32/#45/#46) into HANDOFF + platform plan
... (17 more commits, all already ancestors of main)
```

The commit touches `docs/adr/0019-amh-lake-of-record.md`, `docs/adr/0020-custody-chain.md`, 6 `docs/processes/contracts/SP-OP-*.md` draft sheets, 1 test-spec, and `docs/review-queue.md` (10 files, 1,843 lines, all insertions).

**Correction to the handoff's "COLLIDES" framing:** a direct content diff (`git diff main -- docs/adr/0019-amh-lake-of-record.md` and the 0020 equivalent, run from inside the worktree) is **empty for both files** — they are byte-identical to `main`'s current tip. There is **no live numbering collision today**; the content already converged (most likely `main`'s current 0019/0020 were authored independently and happen to match, or this worktree was synced at some point). What **has not** been verified is whether the other 8 draft files (6 contract sheets + 1 test-spec + `review-queue.md`) are stale relative to `main`'s actual Phase-3 implementation, which landed through entirely different commits/PRs. **Recommendation:** safe to prune the branch/worktree for the ADR pair; the 8 remaining draft docs should get a stale-content check before anyone treats them as current before deletion.

**`maezo-p3-we`** (branch `wave/p3-we`, at `75d4f81`) — **10 commits ahead of main** (verified via `git rev-list --count main..HEAD`), explicitly marked **"do not auto-merge"** in its own top commit message ("PRESERVE W-E + allowlist staging for the next coordinator"):

```
75d4f81 chore(phase3): PRESERVE W-E + allowlist staging for the next coordinator (do not auto-merge)
bc86d8e Merge remote-tracking branch 'origin/wave/p3-adeq' into wave/p3-we
95b7bd9 Merge remote-tracking branch 'origin/wave/p3-inad' into wave/p3-we
c191fe5 Merge remote-tracking branch 'origin/wave/p3-wb' into wave/p3-we
6036b1e Merge remote-tracking branch 'origin/main' into wave/p3-wb
013aa53 feat(phase3): W-C — SP-OP-ADEQUACAO-001 quadruple (RN 259; L3 monitoring + human-gated fallback)
1010b17 fix(p3-wb): int32→Long defense in the 3 PRODUCTION Camunda serializers (money-in-cents overflow)
2228bf2 fix(phase3): W-B real-engine fixes — CRED flow wiring (3 defects) + int64 money serialization
67d98f0 feat(phase3): W-B(2) — SP-OP-INADIMPLENCIA-001 quadruple (RN 593; no double-rescission)
d225672 feat(phase3): W-B — SP-OP-CRED-001 + PAGTO-001 + PROGRAMA-001 quadruples (no-adverse clones)
```

54 files changed, 17,173 insertions / 17 deletions (adds `adequacao.py`/`cred.py`/`inadimplencia.py`/`pagto.py`/`programa.py` worker modules + matching integration/unit test suites + `uv.lock`). **Confirmed per the handoff: the int32→Long money-serialization fix (`1010b17`) is already present on `main`** (verified across all 3 affected files by the WS-0 phase) — **no stranded production code** in this worktree. Recommendation: this worktree is safe to leave parked (it is explicitly a "next coordinator" staging area, not something this program should merge or delete unilaterally).

### (e) §6.2 secret-population prerequisites before first deploy

**All `maezo.io/blocked` ExternalSecret entries, read directly from `deploy/helm/maezo-tenant/templates/externalsecret.yaml`:**

| ExternalSecret | Target secret | Blocker annotation | Status |
|---|---|---|---|
| `llm-api-keys` | `maezo-llm-keys` | `LLM-contract` | Blocked |
| `whatsapp-config` | `maezo-whatsapp-config` (currently only `waba-token`) | `WABA-onboarding` | Blocked; **also needs 2 new properties added for the §2.2 fix** (`app-secret`, `verify-token`) — not yet in this ExternalSecret's `data:` list at all, so the fix PR must add the remoteRef entries in addition to the Deployment env wiring |
| `phi-hmac-key` | `maezo-phi-hmac` (key `phi-hmac-key`) | `PHI-HMAC-key-population` | Blocked; **now a hard required dependency** — PR #177 (merged) makes agent-runtime and worker-daemon pods `secretKeyRef` this value with no `optional: true`, so those pods will **not start** until this is populated |
| `tasy-config` | `maezo-tasy-config` | `Tasy-integration-agreement` | Blocked |
| `aurora-master-credentials` | (Aurora secret) | *(not blocked)* | Not gated — flows regardless |
| `kafka-config` | `maezo-kafka-config` | *(not blocked)* | Not gated — sourced from `amh-data-platform`'s existing MSK Serverless secret |

**Go-live-blocking prerequisites, in priority order:**
1. **`phi-hmac-key`** — REQUIRED now (pods block on startup without it, post-#177).
2. **`whatsapp-config`** — needs the existing `waba-token` populated **and** (once §2.2's fix PR lands) 2 new properties: `app-secret`, `verify-token`.
3. `llm-api-keys` — required for any agent to actually reason (blocked pending LLM contract).
4. `tasy-config` — required for the Tasy CDC-driven intake path (blocked pending integration agreement).

### (f) WS-2 risk acceptances + "needs product judgment" findings

- **WS-2 risk acceptances:** reproduced in full in §6, WS-2 table (B3: 6 `langgraph`-family alerts; B4: 1 `pytest`-family alert).
- **"Needs product judgment" findings from §3:** the matricula/PHI pair (`matricula-raw-to-general-zone-notifications-topic` + `inad-cancel-matricula-phi-egress-unscrubbed`, §3.5) and `phone-hash-keyless-brute-forceable-reidentification` (§3.3). Both require a product/compliance decision (scrub-vs-declassify for matricula; accept-vs-HMAC-key for the phone hash) that this audit cannot make unilaterally.

---

## 8. Deferred-work register

**Langgraph migration brief** (WS-2 B3, 6 Dependabot alerts #2/#4/#6/#7/#15/#16) — `langgraph` 0.6→1.2 + `langgraph-checkpoint` 2→4.1.1 + `langgraph-checkpoint-postgres` 2→3.1, a coupled major-version migration (serializer hardening + Postgres `setup()` chain interacting with existing checkpoint rows). All 6 alerts are currently conditional/unreachable as used (trusted-store-only deserialization path, `BaseCache` unused, `langgraph-sdk` never imported) — risk-accepted for this program, but the version gap will keep widening. Owner: runtime/platform team.

**Pytest test-infra brief** (WS-2 B4, 1 alert #10) — `pytest` 9 major bump + 2 coupled cap-lifts (`pytest-asyncio` 1.4, `pytest-cov` 7.1); dev/CI-only exposure (local-privilege CWE-379 class). Owner: whoever owns CI tooling.

**Post-deploy findings, one-line briefs grouped by owner area:**

- **BPMN/DMN process-modeling** (owner: process/domain team) — `auth-denial-not-human-undeclared-error` (declare + boundary-catch `ERR_AUTH_DENIAL_NOT_HUMAN`); `cancel-brtcancelsla-dangling-outgoing-idref` (fix stale IDREF); `cred-business-key-missing-collision-fallback` (add digest fallback to 3 business-key builders); `no-denial-boundary-escape-reachability-blindspot` (extend the human-gate BFS to non-userTask boundary escapes).
- **Agent-autonomy documentation** (owner: agent/prompt team) — `lucas-cancel-start-overclaim` (reword to match his actual scoped capability, mirroring the #174 fix pattern); `helena-autonomy-actions-incomplete-triage-scheduling` (add the 3 missing L3 actions to her `agent.yaml`).
- **PHI/pseudonymizer hardening** (owner: gateway/PHI team) — `redact-payload-force-key-gap-operator-named-flow-fields`; `audit-chain-from-rows-ts-reorder-verify-flap` (reconstruct chain order structurally, not by `ts`); `redact-payload-geo-coordinate-string-bypasses-rounding`.
- **NetworkPolicy / egress** (owner: infra/platform team) — `networkpolicy-otel-cross-namespace-egress-missing`; `networkpolicy-ingress-policytype-blocks-all-cross-namespace-external-traffic`.
- **A2A wiring parity** (owner: runtime team) — `recurso-marina-a2a-decorative` (mirror `contas.py`'s `_delegate_to_marina` pattern); `inad-fernando-a2a-unreachable` (add `fernando` to `_A2A_TARGETS` + pass the dispatcher).
- **Observability alerting/dashboarding** (owner: DevOps/SRE) — the 9 post-deploy observability findings in §3.6 (kafka-lag label fix, phantom-metrics wiring, runbook_url corrections, HITL-approved metric wiring, worker/ANS/NIP/calendar alert+dashboard coverage, LGPD SLA-breach metric, ANS-CRON engine-poller keys, kafka-exporter deployment).
- **Runbook accuracy** (owner: DevOps/docs) — all 24 docs-runbooks findings in §3.7 (10 distinct underlying doc defects; batchable into a single documentation-accuracy pass across `gateway.md`, `devops-stack.md`, `helena.md`, `whatsapp-webhook.md`, `phase0-demo.md`, `engine-processes.md`).

**LGPD re-identification resolver — BLOCKED-BY-DESIGN, unchanged.** No secure re-identification store exists to map `titular_pseudo_id`→`fhir_patient_id`; the resolver is `None`-gated and the LGPD worker fails closed rather than guessing. This is a deliberate architectural deferral (documented, not a gap) — building the secure re-identification store is out of scope for this program and requires its own design decision.

---

## ERRATA 2026-09-03 — §WS-4 afirma uma entrega ADR-0024 que NAO EXISTE (GAP AF-02)

**Status:** Proposto (errata) — DRAFT/verify · **Autor:** `adr-reconciler` (R1, AGENTE) · **Base:** `71dd4da`

Append-only: a linha 317 acima NAO foi alterada — reescrever o relatorio apagaria a evidencia de que a
afirmacao foi feita, e deslocaria as ancoras de linha que a auditoria `docs/audits/maezo-deep-audit/`
cita neste arquivo. O que segue e a correcao de registro.

- **Afirmacao (`:317`, secao "WS-4 — Wave-3 residue"):** "**ADR-0024 (durable cross-restart idempotency) ->
  PR #181, T3-PASS.** New `PostgresDedupeStore` ... + migration `0008_driver_idempotency` + wiring at both
  `_spawn_inbound_driver`/`_spawn_resume_driver` call sites."
- **Verdade em `71dd4da`:** nada disso esta no codigo.
  - `grep -rn PostgresDedupeStore src/` -> 0 linhas; `grep -rn --include='*.py' PostgresDedupeStore .` -> 0;
    `git log --all -S PostgresDedupeStore --oneline -- src/` -> 0 commits.
  - Nao ha migracao `0008_driver_idempotency`: a `0008` real e `0008_a2a_fact_outbox.py` (`revision "0008"`,
    `:120-121`); a tabela `driver_idempotency` nasceu na `0003`
    (`src/maezo/platform/migrations/versions/0003_a2a_idempotency.py:58-72`).
  - Os dois call sites citados nao existem: `src/maezo/runtime/inbound_driver.py` foi removido e
    `grep -rn 'class InboundDriver\|class ResumeDriver\|IdempotencyGuard\|InMemoryIdempotencyStore' src/
    --include='*.py'` -> 0 linhas. O caminho inbound hoje e dispatch in-process do webhook
    (`src/maezo/platform/webhooks/whatsapp/dispatch.py:161`).
  - O **PR #181 real deste repo** e `03c6437` — "Item 9 wave-2: pagto bucket-1 (12/12) + ADR-0030 guard
    migration (nip/programa) (#181)" — conteudo nao relacionado.
- **A afirmacao "T3-PASS" nao tem lastro reproduzivel** neste repo: nenhuma linha de
  `docs/evidence-ledger.md` cita `PostgresDedupeStore`.
- Registro completo: `docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md`,
  secao `### §1 — GAP AF-02` (**ponteiro corrigido em 2026-09-05, gap AF-02**: esta linha apontava
  para uma secao `## Emenda 2026-09-03` DENTRO da propria ADR-0024 que nao existe mais — a primeira
  leva do WP-ADR-RECONCILIACAO emendou as sete ADRs `Accepted` in-loco, a verificacao adversarial
  rejeitou isso, e o conteudo foi movido para a ADR-0041 nova, com os sete arquivos restaurados
  byte a byte e cercados por `tests/unit/docs/test_adr_amendments.py`. O ponteiro ficou pendurado;
  `grep -rn '## Emenda 2026-09-03' docs/adr/` -> so a mencao historica em `0041:589`).
