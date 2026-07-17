# RIPD / DPIA — Kickoff Package (T2.8, defect B8/B12)

**Status:** `blocked(external: DPO designation)` — this is a KICKOFF package, not a completed RIPD. The
Relatório de Impacto à Proteção de Dados Pessoais (RIPD; LGPD Art. 5, XVII; Art. 38 — *article
references to confirm with jurídico*) is an **external deliverable** that must be authored/ratified by a
designated DPO (encarregado, LGPD Art. 41). No DPO is designated in-repo. This package inventories the
**real PHI flows now on `main`**, states candidate legal bases **for DPO confirmation only**, poses the
DPIA questions, and lists the gaps — so the DPO can start on evidence, not from zero.
**Author:** compliance-analyst (R1). **No legal conclusions are drawn here** (charter constraint 1/3).

## 0. Why this is a kickoff and not the RIPD

- LGPD Art. 38 (*confirm*): the ANPD may require the controller to produce a RIPD, especially for
  processing of **sensitive data** (health data = Art. 11) at scale. An operadora processes health data
  as its core activity → a RIPD is a live obligation, not optional.
- A RIPD is a **DPO/controller instrument**. This document does the engineering legwork (data-flow
  inventory with mechanisms cited path:line) and hands the DPO a structured questionnaire. It does not
  and cannot self-certify the assessment (constraint 1: no self-certification).

## 1. Data-flow inventory — the REAL PHI flows on `main`

Only mechanisms that actually exist on `main` are listed (capability-honest, constraint 3). Where a
control is a labeled mock, a scaffold, or absent, it is marked as such.

### F1 · WhatsApp webhook intake (inbound PHI at the edge)
- **What:** Meta Cloud API webhook receiver. `GET /webhook` verification handshake; `POST /webhook`
  event ingestion. Evidence: `src/maezo/platform/webhooks/whatsapp/app.py:82-133`.
- **Real control:** POST body signature is verified with **HMAC-SHA256** over the raw request body using
  the Meta app secret, timing-safe (`hmac.compare_digest`). Evidence:
  `src/maezo/platform/webhooks/whatsapp/security.py:16-28`.
- **Phone pseudonymization:** `hash_phone(phone, tenant)` = SHA-256 of `"{tenant}:{phone}"`; docstring
  asserts the raw phone is "NEVER logged, stored, or forwarded past this point" (ADR-0006 General Zone).
  Evidence: `security.py:31-34`.
- **Capability-honest limit:** a signature-verified POST returns **501 (queue-less by design)** — there
  is NO Kafka publish and NO downstream consumer yet (Helena intake graph is T1.11). Evidence:
  `app.py:98-133` + module docstring `app.py:9-21`. **PHI is received (raw body read for signature
  check) but not yet persisted or forwarded.**
- **PHI touchpoint:** the raw inbound message body (may contain free-text PHI, phone, possibly
  `location.latitude/longitude`) transits memory for signature verification. → see GAP-1, GAP-4.

### F2 · Gateway pseudonymization (General Zone boundary)
- **What:** `Pseudonymizer.pseudonymize()` replaces PHI fields with deterministic SHA-256 hex digests
  before data reaches an LLM context. Evidence: `src/maezo/gateway/pseudonymizer.py:40-64`.
- **PHI_FIELDS:** immutable, CI-enforced frozenset `{cpf, nome, telefone, email}` (`:20-27`). Non-PHI
  fields pass through unchanged; empty/None PHI preserved as-is.
- **Design basis:** ADR-0006 (PHI two zones) — General Zone agents (Helena, Lucas, Fernando, Gustavo)
  see pseudonymized PHI; the re-identification map lives only in the gateway. Evidence:
  `docs/adr/0006-phi-two-zones.md:9-14`.
- → see GAP-2 (deterministic unsalted hash = pseudonymization, NOT anonymization; still personal data),
  GAP-4 (geolocation minimization referenced in review-queue is NOT present in this module on `main`).

### F3 · Engine process variables (pseudonymized identifiers only)
- **What:** BPMN process instances carry `titular_pseudo_id` (ADR-0006 pseudonym), `tenant_id`, `canal`,
  `tipo_requisicao`, `detalhes_requisicao` (documented as pseudonymized text). Evidence:
  `docs/processes/contracts/SP-OP-LGPD-DSR-001.md:37-46`; BPMN start vars `bpmn:53-56`.
- **PHI posture:** the engine layer is designed to handle pseudonyms, not raw PHI. `detalhes_requisicao`
  is documented "(pseudonimizado)" — DPO should verify free-text pseudonymization is actually enforced
  upstream (no PHI_FIELDS coverage for free text; see GAP-2).

### F4 · Audit hash chain (append-only, 5-year retention)
- **What:** `PostgresAuditSink` writes every world-effect to the `audit_chain` table, append-only,
  per-tenant advisory-locked, **fail-closed** (raises on any DB failure — an unauditable action must not
  proceed). Evidence: `src/maezo/gateway/audit_postgres.py:167-265`.
- **PHI posture:** "this module never receives, hashes, or stores raw PHI; `input_hash` is a SHA-256 of
  `details`" (`:45-47`). `decision_basis` / `dmn_versions` stored as **jsonb** (`:129,235`) — must be
  safe-to-persist structured data by the time it reaches the sink (caller's responsibility).
- **Retention:** 5 years (DL-0018, `docs/decisions-log.md:20`), enforced by
  `RetentionManager.retention_query()` → `DELETE FROM audit_chain WHERE ts < cutoff`
  (`src/maezo/platform/retention.py:132-143`). → this is the retention-vs-legal-hold conflict; see the
  companion `ADR-0020-amendment-draft.md`.

### F5 · LLM PHI-zone seam (egress control for model calls)
- **What:** inference providers carry a `phi_capable` ClassVar gate. A PHI-tagged request must route to a
  `phi_capable=True` provider or raise `PhiZoneRoutingError` (fail-closed — never silently falls back to
  the general-zone cloud provider). Evidence: `src/maezo/runtime/inference.py:88-97,142,167,207`.
- **Real vs mock:** the ONLY `phi_capable=True` provider is `PhiZoneMockProvider` — an
  **explicitly-labeled mock** returning synthetic responses; the real BR-resident, zero-retention PHI
  endpoint is **NOT provisioned** (`:296-337`, ADR-0006/ADR-0017). General-zone providers
  (`Noop`, `Anthropic`) are `phi_capable=False`.
- **PHI posture:** the *routing seam* is real and fail-closed; the *PHI-zone endpoint itself* is mocked.
  Network egress enforcement (NetworkPolicy allowlist, ADR-0006) is a K8s-layer control, not verifiable
  from application code here. → see GAP-5.

### Flow summary (inbound → engine → audit → LLM)
```
WhatsApp POST (raw body, HMAC-verified)          F1  → [501 today; T1.11 wires Kafka]
   → gateway pseudonymize {cpf,nome,telefone,email} → SHA-256   F2
      → engine vars (titular_pseudo_id, pseudonymized detalhes)  F3
         → audit_chain (hashes + jsonb decision_basis, no raw PHI, 5y retention)  F4
         → LLM call: PHI-tagged → phi_capable gate → PhiZoneMock (real endpoint absent)  F5
```

## 2. Candidate legal bases per flow — **FOR DPO CONFIRMATION ONLY**

The controller/DPO determines the legal basis. The following are *candidates to evaluate*, not
conclusions. Health data is **sensitive data (LGPD Art. 5, II; Art. 11)** → the Art. 7 bases do NOT
apply to it; only **Art. 11** hypotheses do. **Exact article letters/paragraphs must be confirmed by
jurídico — flagged where uncertain.**

| Flow | Data categories | Candidate LGPD basis (DPO to confirm) | Notes / uncertainty |
|---|---|---|---|
| F1 WhatsApp intake | phone, free-text (may contain health data) | Art. 11 (dado sensível de saúde) — hypothesis TBD; possibly Art. 11, II 'a' (obrigação legal/regulatória) and/or the health-tutelage / operadora provisions of Art. 11 §§ | **requires jurídico confirmation** of the exact Art. 11 hypothesis for an operadora; free-text may carry health data pre-pseudonymization |
| F2 pseudonymization | cpf, nome, telefone, email | processing basis inherits from the originating purpose; pseudonymization is a **security/minimization measure** (Art. 6, VII segurança; Art. 46), not itself a legal basis | pseudonymized data remains **personal data** under LGPD (not anonymized — GAP-2) |
| F3 engine vars | pseudonymized identifiers | inherits F1/F2 basis | — |
| F4 audit chain | hashes, structured decision_basis | Art. 16, I — conservação para **cumprimento de obrigação legal/regulatória** (5-year retention) | strong candidate; period (5y) pending jurídico/regulatório sign-off (DL-0018 is DRAFT on the number) |
| F5 LLM PHI-zone | pseudonymized (General) / integral (PHI zone) | inherits originating-purpose basis; egress control is a **security measure** (Art. 6, VII; Art. 46), DPA + BR residency + zero-retention required | real endpoint absent → basis is theoretical until provisioned |
| DSR process | subject-rights requests | Art. 18 (direitos do titular) — **obligation, not a processing basis** | the DSR process *serves* Art. 18; response within Art. 19, II (15 days) |

## 3. DPIA questions for the DPO (start here)

1. **RIPD trigger:** Confirm the operadora's health-data processing meets the Art. 38 / Art. 5-XVII
   threshold requiring a formal RIPD, and set its scope (which flows F1–F5, which agents/zones).
2. **Legal-basis matrix:** For each flow in §2, fix the exact Art. 11 hypothesis (and Art. 16 conservation
   basis for F4). This matrix is ALSO the pending "matriz de bases legais de retenção por tipo de dado"
   from `SP-OP-LGPD-DSR-001.md:103-104` and `review-queue.md:22` — one artefact serves both.
3. **Pseudonymization vs anonymization (GAP-2):** Is a deterministic, **unsalted** SHA-256 of a CPF
   (11-digit, brute-forceable) acceptable as a minimization control, given it remains re-identifiable and
   therefore **personal data** (not anonymized, LGPD Art. 5, III/XI)? Should PHI_FIELDS hashing be
   keyed/salted (HMAC) rather than plain SHA-256? Evidence: `pseudonymizer.py:56`.
4. **Free-text PHI (GAP-3):** `detalhes_requisicao` and inbound WhatsApp free-text are documented as
   pseudonymized, but `PHI_FIELDS` covers only `{cpf, nome, telefone, email}` — free-text health content
   is not field-pseudonymizable. What is the required control (redaction, human review, PHI-zone routing)
   before free-text reaches an LLM or the audit `decision_basis` jsonb?
5. **Webhook raw-payload retention (GAP-1):** Once T1.11 wires the Kafka publish, what is the retention /
   expurgo policy for the **raw inbound webhook payload** (which may carry unpseudonymized PHI)? Today it
   is received-then-dropped (501); the future path needs an explicit retention window and a working-layer
   expurgo rule (GAP-6).
6. **Geolocation minimization (GAP-4):** `review-queue.md:394-420` records a DPO-pending decision to round
   `location.latitude/longitude` to 2 decimals (~1.1 km) — but this control (`_GEO_COORDINATE_KEYS`) is
   **NOT present in `gateway/pseudonymizer.py` on `main`**. Confirm the intended granularity AND that it is
   actually implemented before any consumer exists.
7. **PHI-zone endpoint (GAP-5):** The BR-resident zero-retention PHI inference endpoint is a labeled mock;
   the routing seam is fail-closed but the endpoint is absent. What are the DPA / residency /
   zero-retention contractual requirements the real provider must meet before PHI-tagged inference is
   enabled?
8. **DSR ↔ retention/legal-hold:** How does an Art. 18-VI erasure request interact with the 5-year audit
   retention (F4) and any legal-hold? (Cross-ref: `ADR-0020-amendment-draft.md`; and the erasure DSR path
   `SP-OP-LGPD-DSR-001.md` §Invariantes.)
9. **International transfer:** Do any flows (LLM cloud provider in General Zone, Art. 33) constitute
   international data transfer requiring an adequacy/safeguard basis? `AnthropicInferenceProvider` is a
   general-zone cloud provider (`inference.py:184-207`). **requires jurídico confirmation.**
10. **Titular rights operability:** Confirm the SP-OP-LGPD-DSR-001 process (access, correction, erasure,
    portability, sharing-info, consent-revocation) covers the full Art. 18 catalogue and the Art. 19-II
    15-day deadline as implemented (SLA global P15D, `bpmn:307-313`).

## 4. Gaps register (engineering-visible, DPO-actionable)

| ID | Gap | Evidence | Fail-closed today? | Owner |
|---|---|---|---|---|
| GAP-1 | Webhook raw-payload retention undefined for the post-T1.11 path | `app.py:98-133` (501 scaffold) | Yes — nothing persisted yet (dropped) | runtime-lane + DPO |
| GAP-2 | Deterministic **unsalted** SHA-256 pseudonymization → re-identifiable, remains personal data (not anonymization) | `pseudonymizer.py:56` | N/A (design question) | DPO + jurídico |
| GAP-3 | Free-text PHI not field-pseudonymizable; `PHI_FIELDS` covers 4 structured fields only | `pseudonymizer.py:20-27` | Partial — free text passes through | DPO + runtime-lane |
| GAP-4 | Geolocation rounding (`_GEO_COORDINATE_KEYS`) is in review-queue but **NOT in `pseudonymizer.py` on main** | `review-queue.md:394-420` vs `pseudonymizer.py` (absent) | No control on main | DPO + runtime-lane |
| GAP-5 | Real BR-resident zero-retention PHI-zone endpoint absent (mock only) | `inference.py:296-337` | Yes — `PhiZoneRoutingError` on PHI without real provider | infra + DPO |
| GAP-6 | Working-layer **expurgo** (elimination in transient/working stores — Kafka topics, engine history TTL, re-identification map) not specified as a retention/erasure policy | `retention.py` covers `audit_chain` only; ADR-0006 re-id map "só no gateway" | Partial | DPO + infra |

## 5. Tracking as an external deliverable

- **Status:** `blocked(external: DPO designation)`. The RIPD itself cannot be produced or ratified in-repo.
- **This package is the input**: data-flow inventory (§1), candidate-basis matrix (§2), DPIA questionnaire
  (§3), gaps register (§4). Hand to the DPO on designation.
- **Review-queue linkage:** DPO items already tracked at `review-queue.md:22-23,142-144,182,405-420`.
- **No legal conclusion is asserted here.** All bases and thresholds are marked for DPO/jurídico
  confirmation (constraint 1/3). Uncertain article references are flagged "requires jurídico confirmation"
  rather than stated as fact.
