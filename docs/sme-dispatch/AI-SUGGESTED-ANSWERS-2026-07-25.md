# AI-DRAFTED ADVISORY — Suggested answers for the pending SME decisions (2026-07-25)

> ## ⚠️ READ FIRST — WHAT THIS DOCUMENT IS AND IS NOT
>
> **This is AI-DRAFTED ADVISORY MATERIAL. It is NON-BINDING.** It was produced to *accelerate* —
> **not replace** — professional review by the designated humans (DPO, jurídico, médico-auditor,
> regulatório). **Nothing here is a decision, a sign-off, or a ratification.** No contract is moved
> DRAFT→FINAL by this file; no signoff YAML is created or implied by it; no `decisions-log.md` entry
> is authored by it. Per the repo's governance (`docs/sme-dispatch/README.md` §"Signoff artifact
> spec" and §"The redline protocol"), a review verdict binds **only** when the designated human
> records it in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` with their own name,
> date, and attestation. **No agent — this one included — may create, pre-fill, backdate, or infer a
> signoff.** The "no self-certification" constraint is absolute.
>
> **How to use this.** Each entry restates the question, says what it blocks/ships in the code (with
> file citations), lays out the options, and gives a **SUGGESTED ANSWER** the reviewer can adopt,
> amend, or reject. Treat every SUGGESTED ANSWER as a *first draft written in the reviewer's voice* —
> the reviewer must independently verify the legal/clinical/regulatory grounding before adopting it.
> Where the call is genuinely the professional's own, the entry says so and stops short of deciding.
>
> **Provenance.** Drafted against `origin/main` tip `d4bfa13`. Sources read: every package under
> `docs/sme-dispatch/` (dpo/medico-auditor/juridico/regulatorio + README + tracker), `docs/review-queue.md`,
> `docs/decisions-log.md` (DL-0028/0029/0031/0032/0033/0034), `docs/adr/0030` + `0031` + `0032`,
> `docs/compliance/lgpd-topic-reconciliation.md`, `docs/compliance/rn-currency-review.md`,
> `docs/compliance/rn-639-primary-source.md`, `docs/design/T2.6-ans-submission-rescope.md`, the
> LGPD-DSR / PROGRAMA / ANS-SUBMIT contracts, and `spec/processes/dmn/programa_routing.dmn`.
> **The single most load-bearing companion document is `docs/compliance/rn-currency-review.md`** — it
> is the analyst answer-sheet the RN-currency questions were written to be checked against, and most
> jurídico/regulatório suggested answers below simply operationalize its verified verdicts. That
> report is itself marked `DRAFT — pending SME confirmation`; adopting its verdicts is the SME's act.

---

## Table of contents

- [A · DPO (data protection officer)](#a--dpo)
  - A1 LGPD-DSR §Invariantes/SLA/guards · A2 **R-C retention matrix (FOCUS)** · A3 **R-D execute_request routing (FOCUS)** · A4 **ERR_DSR_ERASURE_* boundary design (FOCUS)** · A5 consent-revocation fast path · A6 ANS-SUBMIT GAP-ANS-5 anonymization gate · A7 CANCEL retention-on-rescission · A8 FRAUDE no-PHI-custody + evidentiary retention · A9 PROGRAMA consent chokepoint / revocation / fan-out / k-anon / retention · A10 **proactive_contact channel+consent (FOCUS, joint MA)**
- [B · Jurídico (legal)](#b--juridico)
  - RN-currency answer-key + per-contract structural questions
- [C · Médico-auditor (medical auditor)](#c--medico-auditor)
  - C1 AUTH · C2 CONTAS · C3 CRED · C4 ESCALATION retro-verification · C5 FRAUDE scoring · C6 NIP · C7 **PROGRAMA stratify_risk (FOCUS)** + **proactive_contact clinical half (FOCUS)** · C8 RECURSO · C9 REEMBOLSO
- [D · Regulatório (ANS regulatory affairs)](#d--regulatorio)
  - RN-currency + ANS calendar/cron + **TISS XSD sourcing & version pin (FOCUS)** + NIP + PROGRAMA-RN
- [E · Cross-cutting: the dias-úteis vs dias-corridos SLA basis](#e--sla-basis)

---

<a name="a--dpo"></a>
# A · DPO (data protection officer)

The DPO package (`docs/sme-dispatch/dpo/PACKAGE.md`) covers 5 contracts (LGPD-DSR, ANS-SUBMIT,
CANCEL, FRAUDE, PROGRAMA) plus the #137-refreshed T2.9 addenda. The three items the orchestrator
flagged as most load-bearing — the **R-C retention matrix**, the **R-D execute_request routing
shape**, and the **ERR_DSR_ERASURE_\* boundary design** — get the full treatment in A2–A4.

## A1 · SP-OP-LGPD-DSR-001 — SLA anchor + fail-closed guard sufficiency (DPO Q1, Q2)

**Question (`dpo/PACKAGE.md:38-44`).** (Q1) Confirm the 15-day legal SLA (LGPD art. 19, II) is
anchored on instance start (`ESP_SlaGlobal` event subprocess), not on any individual task. (Q2)
Confirm the three fail-closed guards — `ERR_DSR_IDENTITY_UNVERIFIED`, `ERR_DSR_DECISION_INVALID`,
`ERR_DSR_FUNDAMENTACAO_AUSENTE` — cover every path PHI could leak on an omission.

**What's at stake in the code.** The 15-day anchor is modeled at the process level: the contract's
§SLAs table (`SP-OP-LGPD-DSR-001.md:86-91`) and §Invariantes (`:18`) state "SLA legal de 15 dias
conta do INICIO da instancia (event subprocess `ESP_SlaGlobal`), nao da tarefa." ADR-0031 confirms
the P15D clock keeps running even while the request parks awaiting identity proof (`0031-...:84,106`).
The three guards are the structural fail-closed spine (contract §Codigos de erro `:93-99`; the
identity guard hardened by ADR-0031/DL-0031, the decision/fundamentação guards by GAP-LGPD-4/GAP-LGPD-3,
`review-queue.md:204-215`).

**Options.** (i) Ratify as-is. (ii) Ratify the anchor but request the identity-proof P10D sub-timer
be re-examined against the 15-day clock. (iii) Withhold pending the retention matrix (A2).

**SUGGESTED ANSWER (adopt, verdict `approved` on these two points; the contract as a whole stays
`needs-changes` pending A2).** The 15-day anchor is **correct and should be ratified**: LGPD art. 19,
II sets a 15-day response window and anchoring it on `ESP_SlaGlobal` (instance start) — a
non-interrupting event subprocess independent of any task — is exactly right, because the legal clock
is owed to the *titular from the request*, not reset by internal task hops. The P10D identity-proof
timer correctly sits *inside* that 15-day envelope (the request parks but the legal clock does not
pause — ADR-0031's stated posture), which is the conservative reading of art. 19. On Q2, the three
guards **do** cover the omission-leak paths for the *decision-and-release* stage: no release without
an explicit `decisao_dsr` (GAP-LGPD-4), no denial without `fundamentacao_legal` (GAP-LGPD-3), no
compilation on an unverifiable identity (GAP-LGPD-6/ADR-0031). **One residual the DPO must verify
before signing:** these guards protect the *gateway and identity* stages, but the actual PHI
**compilation** (`compile_data_package`, R-C) and **execution** (`execute_request`, R-D) workers are
still unbuilt (`lgpd.py:631-641`), so the "no PHI leaks on omission" property is only *partially*
enforced until those two workers land with the guards specified in A2/A3. Recommend the DPO's signoff
note read: "guards Q1/Q2 confirmed for identity+decision stages; full coverage contingent on R-C/R-D
worker guards per this review."

**Confidence: high** on the anchor; **medium** on "covers every path" (genuinely contingent on the
unbuilt R-C/R-D workers). **Must still check:** that no `compile_data_package`/`execute_request`
worker, once built, can emit PHI before `identidade_confirmada is True` AND an explicit `decisao_dsr`.

## A2 · **[FOCUS]** R-C `compile_data_package` — the legal-bases / retention matrix (DPO Q3 + T2.9 R-C)

**Question (`dpo/PACKAGE.md:45-47, 60-68`; `lgpd-topic-reconciliation.md` R-C).** `ST_CompilarPacote`
is documented to compile a package "incluindo mapa de bases legais e obrigacoes de retencao
aplicaveis" (`SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:183`), but no such map exists in the repo,
and the contract lists "matriz de bases legais de retencao por tipo de dado" as an open pendência
(`SP-OP-LGPD-DSR-001.md:103-104`). Specify, per `tipo_requisicao`/data type, which fields belong in
an export/rectification/erasure package and which retention exceptions apply — in particular where
**Lei 13.787/2018** (prontuário retention) legitimately overrides an elimination request vs. where
LGPD elimination should proceed.

**What's at stake in the code.** This is the content gate on the whole `compile_data_package` worker
(R-C in `lgpd-topic-reconciliation.md:87-95`, flagged DPO/SME sign-off). The worker cannot be "honest"
(the reconciliation doc's word) until the DPO defines this matrix; the topic wiring is mechanical but
the *content* is DPO-owned. It also feeds R-D (execute_request) and R-F (send_response): a
`NEGAR_FUNDAMENTADO` on an elimination request needs a `fundamentacao_legal` drawn from exactly this
matrix.

**Options.** (i) Leave abstract ("DPO defines later") — blocks the worker indefinitely. (ii) Adopt a
concrete starter matrix now, per data category × DSR type × legal basis × retention exception, and
refine. (iii) Fail-closed-to-human on every elimination until the matrix exists (safe but defeats the
automation's purpose and overloads `UT_RevisaoDpo`).

**SUGGESTED ANSWER — adopt this starter matrix (option ii), refine the periods against counsel.**
The DPO should own and publish the following matrix as the seed for `compile_data_package`. It is
grounded in LGPD arts. 16 and 18 §2, Lei 13.787/2018 art. 6 (prontuário), and the general prescriptive
periods; **every retention period below must be confirmed by jurídico** (they are the defensible
defaults, not decrees). The controlling principle: **LGPD art. 18 VI (elimination) yields to LGPD
art. 16 I (retention required to comply with a legal/regulatory obligation)** — elimination proceeds
*only* for data categories with no surviving legal-retention obligation.

| Data category (Zona Geral pseudonymized) | In EXPORTAÇÃO pkg? | In RETIFICAÇÃO scope? | ELIMINAÇÃO verdict | Legal basis to RETAIN (overrides erasure) | Min. retention |
|---|---|---|---|---|---|
| **Dados de saúde / prontuário** (CID, autorizações, laudos, procedimentos) — *sensível, art. 11* | Yes (art. 18 II/V) | Yes if factually wrong; NOT clinical-judgment content | **RETER — negar eliminação fundamentada** | **Lei 13.787/2018 art. 6 + CFM Res. 1.821/2007** (prontuário ≥ 20 anos do último registro); LGPD art. 16 I | **≥ 20 anos** do último lançamento |
| **Cadastrais/contratuais** (matrícula, plano, vínculo, endereço) | Yes | Yes | **RETER enquanto vínculo ativo + prazo prescricional**; eliminar após | LGPD art. 7 V (execução de contrato) + art. 16 I; Código Civil art. 206 (prescrição) | Vínculo + **5 anos** |
| **Financeiros/faturamento** (mensalidades, boletos, CNAB, glosas) | Yes | Yes (valores) | **RETER — obrigação fiscal/prescricional** | LGPD art. 16 I; CTN art. 173/174; Cód. Civil art. 206 | **5 anos** (fiscal) |
| **Regulatórios ANS** (dados de envio periódico, NIP, DIOPS) | Parcial (só o que é do titular) | Não (dado agregado) | **RETER — obrigação regulatória** | LGPD art. 16 I; RN de guarda ANS | Per RN aplicável (**verify SME regulatório**) |
| **Consentimento / revogação** (logs de consent, `consent_event_ref`) | Yes (prova do próprio direito) | Não | **RETER — prova de conformidade** | LGPD art. 16 I (accountability, art. 37/50) | Enquanto durar + **5 anos** pós-cessação |
| **Auditoria / não-repúdio** (`audit_chain`, custody bundles) | Não (metadado técnico) | Não (imutável por design) | **RETER — imutável; nunca eliminável a pedido** | ADR-0007; LGPD art. 16 I + defesa em processo (art. 7 VI / art. 10) | **5+ anos** (DL-0018) |
| **Evidência de fraude** (`bundle_root`, pointers) | Não | Não | **RETER — legal-hold** | LGPD art. 16 I + art. 7 VI (exercício de direitos em processo) | **5+ anos** / até fim do processo |

Package composition by DSR type: **EXPORTAÇÃO** (`fluxo=EXPORTACAO`) = all "In EXPORTAÇÃO pkg? = Yes"
categories, pseudonymized, in a portable format (art. 18 V/§5). **RETIFICAÇÃO** = only factual/cadastral
fields; clinical-judgment content is *not* rectifiable via DSR (it is a prontuário-integrity matter —
route to médico-auditor). **ELIMINAÇÃO_AVALIACAO** = the worker compiles the *retention verdict per
category* so the human `UT_RevisaoDpo` can issue a partial `NEGAR_FUNDAMENTADO` (health/financial
retained) + partial `EXECUTAR_E_ENVIAR` (categories with no surviving obligation) — **elimination is
per-category, never all-or-nothing.** The `fundamentacao_legal` text for a retained health record
should cite Lei 13.787/2018 art. 6.

**Confidence: medium-high on the structure; the retention *periods* are the DPO+jurídico's call.**
The 20-year prontuário floor (Lei 13.787/2018 art. 6, superseding the earlier CFM 20-year rule for
digital records) is the load-bearing legal fact; verify whether your plano retains longer by policy.
**Must still check:** (a) the exact ANS retention RN per regulatory data category (regulatório lane);
(b) whether any category carries a *shorter* contractual retention that LGPD art. 15/16 would force
earlier deletion of; (c) that "rectification of clinical content" is correctly excluded from the DSR
and redirected to a clinical-integrity process, not silently dropped.

## A3 · **[FOCUS]** R-D `execute_request` — collapse 3 workers + ratify the erasure-failure/denial routing shape (T2.9 R-D)

**Question (`dpo/PACKAGE.md:69-91`; `lgpd-topic-reconciliation.md:97-127`).** The BPMN has ONE
execute task `operadora.lgpd.execute_request` reached only on `EXECUTAR_E_ENVIAR`; the code splits it
into three orphan topics (`execute_export`/`execute_rectification`/`execute_erasure`, `lgpd.py:658-665`).
`ST_ExecutarRequisicao` has **no boundary event at all** and its single outgoing flow feeds
`ST_EnviarResposta` unconditionally. Ratify (a) collapsing the three into a single `execute_request`
worker matching the modeled shape, and (b) the routing shape: should R-D **raise** the two
declared-but-unbound errors (`ERR_DSR_ERASURE_FAILED`/`ERR_DSR_ERASURE_NOT_HUMAN`), accepting the
BPMN's documented incident-fail-closed intent (no BPMN edit), **or** should a modeled boundary be
added routing to a clean neutral terminal (a BPMN change, out of scope)?

**What's at stake in the code.** With R-F (`send_response`) now BUILT and live (`lgpd.py:479`, #125),
the unfiltered flow from execute→send is **live-reachable** — so whatever a future R-D worker returns
for a denied/failed execution flows into `send_response` **with no BPMN-level filter** (`dpo/PACKAGE.md:83-91`).
The only thing distinguishing "erasure succeeded" from "erasure blocked/failed" today would be a
worker-internal guard. This is the exact GAP-LGPD-2 hazard the BPMN comments warn about: *"a resposta
jamais afirma uma erasure que nao ocorreu"* (`bpmn:15-18`).

**Options.**
- **(A) RAISE the codes; no BPMN edit (incident-fail-closed).** Consistent with ADR-0030's default:
  a `WorkerBpmnError(code)` whose code is *not* in a gate-proven boundary allowlist is demoted to
  `failure(retries=0)` → a real, human-visible engine incident (`0030-...:256-260`; `harness.py:925-943`).
  The BPMN's own GAP-LGPD-2 comment already documents this as the intended posture ("sem catch
  declarado, `ST_ExecutarRequisicao` vira incidente"). Zero spec change.
- **(B) Add a modeled boundary → neutral terminal.** Cleaner, mirrors the no-denial structural
  terminals (`End_ManterNaoConfirmado`, `End_DecredBloqueadoNaoHumano`). But it is a BPMN change AND —
  critically — ADR-0030 §4 (amended per R1 F4) **hard-gates every `*_NOT_HUMAN` guard code on T-E
  (audited-refusal) landing**, because activating a neutral-terminal boundary *before* T-E converts a
  guaranteed-human-visible incident into a **silent clean end** with no audit row.
- **(C) Keep three workers.** Rejected — diverges from spec SoT (`lgpd-topic-reconciliation.md`
  headline: every actionable divergence reconciles code→BPMN).

**SUGGESTED ANSWER — ratify the collapse (Option C rejected), and choose Option A *now* with Option B
as the sequenced target after T-E.** Concretely, the DPO should sign off:

1. **Collapse the three workers into one `execute_request`-topic worker** matching `ST_ExecutarRequisicao`
   (spec is SoT; `lgpd-topic-reconciliation.md` R-D). The single worker must preserve the three L0
   guards intact: **(a) no erasure without `human_approved`; (b) no erasure without explicit
   `decisao_dsr == 'EXECUTAR_E_ENVIAR'`; (c) `NEGAR_FUNDAMENTADO` never erases** (`lgpd-topic-reconciliation.md:126-127`).
2. **RAISE, do not return (Option A) — for now.** The worker must `raise WorkerBpmnError(...)` on a
   blocked/failed execution, not return a status dict — exactly the GAP-LGPD-6 fix ADR-0031 applied to
   the identity gate (return-as-value left the boundary unreachable; raising makes the failure real).
   Because these codes are not yet in a gate-proven boundary, they demote to a **human-visible
   incident** — the *safe* failure, and the BPMN's own documented intent. This ships R-D without a
   spec change and without waiting on T-E.
3. **Sequence Option B (the modeled boundary) as the eventual target, gated on T-E.** The clean design
   is a modeled boundary routing to a neutral terminal (A4), but per ADR-0030 §4 it must **not** go
   live until T-E (audited-refusal, #131 landed the harness chokepoint but deliberately did not enable
   any `*_NOT_HUMAN` code) records the refusal per ADR-0007. Adding the neutral terminal pre-T-E would
   trade a human-visible incident for a silent clean end — the regression ADR-0030 §4 explicitly
   forbids. So: **incident now, modeled-boundary-after-T-E.**
4. **Regardless of A/B: `send_response` must be gated on the actual execution *result*, not merely on
   human approval.** The unfiltered execute→send flow means R-F must refuse to affirm an erasure that
   the R-D worker did not actually perform. This is the load-bearing invariant (GAP-LGPD-2) and is
   independent of the boundary decision.

**Confidence: high** on the collapse + guard preservation + "raise not return" + the send_response
gating invariant; **the A-vs-B *timing* is a real judgment the DPO shares with the runtime lane and
depends on T-E's schedule.** **Must still check:** that the collapsed worker's guard for `decisao_dsr`
is byte-identical to the gateway's GAP-LGPD-4 allowlist (no second, looser copy), and that R-F cannot
emit "erasure concluída" text on a raised/failed R-D path.

## A4 · **[FOCUS]** `ERR_DSR_ERASURE_FAILED` / `ERR_DSR_ERASURE_NOT_HUMAN` — the boundary design (T2.9)

**Question (`dpo/PACKAGE.md:92-106`).** Both codes are declared at the top of the BPMN (`bpmn:19,22`)
with GAP-LGPD-2 rationale but **zero** boundary events reference either `errorRef`. If a boundary is
added for `ERR_DSR_ERASURE_NOT_HUMAN`, note it is a `*_NOT_HUMAN` guard hard-gated on T-E per ADR-0030
§4. What boundary design should these two codes eventually get?

**What's at stake.** These are the *declared-but-unbound* half of the no-denial structural pattern
(ADR-0018) for the erasure path. Today the incident-fail-closed behavior is BPMN's *default* absence
of catch (A3 Option A), not a modeled route. The remaining blockers (per the package) are (a) this
boundary-shape decision and (b) per-code enablement through the boundary-proof gate — the same posture
as `ERR_DECRED_NOT_HUMAN` / `ERR_CRED_DENIAL_NOT_HUMAN` / `ERR_CONTRACT_SUSPENSION_NOT_HUMAN`
(ADR-0030 Tier 3).

**SUGGESTED ANSWER — endorse this target design; sequence it behind T-E; keep incident-fail-closed
until then.** The two codes are semantically different and should route differently when the boundary
is eventually modeled (all of this is the *target*, not a now-change):

- **`ERR_DSR_ERASURE_NOT_HUMAN`** (an L0 `*_NOT_HUMAN` guard — the worker refuses to erase without a
  human `EXECUTAR_E_ENVIAR` + `human_approved`) → a **neutral terminal**, e.g. `End_ErasureBloqueadaNaoHumana`,
  that **blocks the erasure and terminates cleanly, never performing it** — the exact shape of
  `End_ManterNaoConfirmado`/`End_DecredBloqueadoNaoHumano` (ADR-0030 G2-guard). Per ADR-0030 §4 this
  boundary goes live **only after T-E** so the refusal is recorded per ADR-0007 (otherwise the incident
  becomes a silent clean end). Until T-E: incident-fail-closed (A3 Option A).
- **`ERR_DSR_ERASURE_FAILED`** (a *technical* fail-safe — the erasure was authorized but the downstream
  delete failed) → a **technical fail-safe terminal or a human-repair task** (`UT_TratarFalhaErasure`),
  **never** a flow that lets `send_response` affirm completion. This code is a G2-fs technical fail-safe
  (like ANS NACK retry), so T-E is *desirable but not a hard blocker* (ADR-0030 §4) — but the send-side
  invariant (A3 point 4) is absolute: a failed erasure must never produce a "concluída" response.

The load-bearing property both designs must preserve: **the response never affirms an erasure that did
not occur.** Whether via incident (now) or modeled boundary (post-T-E), the failed/blocked path must
be structurally unable to reach an affirming `send_response`.

**Confidence: high on the target shapes and the T-E sequencing** (they follow ADR-0030 §4 and the
existing G2-guard precedent directly); **the decision to build the boundary at all vs. stay on
incident indefinitely is a genuine architecture+DPO call** — incident-fail-closed is fully compliant,
so the modeled boundary is a *cleanliness/observability* upgrade, not a compliance necessity.
**Must still check:** with the runtime lane, that the boundary-proof gate (`make check-bpmn-error-allowlist`)
would accept these two codes as consumption-covered once boundaries are declared.

## A5 · SP-OP-LGPD-DSR-001 — consent-revocation immediate-effect fast path (DPO Q4 + jurídico Q2)

**Question (`dpo/PACKAGE.md:48-49`; `juridico/PACKAGE.md:249-251`).** Does consent revocation (art. 18
§2, "immediate effect") need a dedicated fast path distinct from the general DSR flow, especially given
SP-OP-PROGRAMA-001 depends on this process's `revogacao_consentimento` output as its interrupt trigger?

**What's at stake.** `revogacao_consentimento` is one `tipo_requisicao` of the DSR (`SP-OP-LGPD-DSR-001.md:42`),
routed by `lgpd_dsr_routing`. PROGRAMA-001's interrupting boundary is fired by the
`consent_revocation_bridge`, which consumes `agents.events.lgpd_dsr.completed` and correlates
`msg.programa.consent_revoked` **only when `decisao_dsr=EXECUTAR_E_ENVIAR`** (revocation actually
executed, `SP-OP-PROGRAMA-001.md:202-209`). So the "immediate effect" in PROGRAMA is currently gated
behind the *full DSR merit flow* completing with an execute decision.

**SUGGESTED ANSWER — recommend a dedicated fast path for the *cessation* effect, while keeping the
general DSR flow for the *record/erasure* consequences.** LGPD art. 8 §5 makes revocation effective
"a qualquer momento mediante manifestação expressa," and the practical reading is that **the cessation
of processing must be immediate**, not queued behind a 15-day merit review. Two-layer design:

1. **Immediate cessation (fast path).** Revocation should stop program PHI processing *on receipt*,
   independent of the DSR merit clock. The cleanest wiring: let the `consent_revocation_bridge` fire
   `msg.programa.consent_revoked` on the revocation *intake/confirmation* signal, not only on a
   completed `EXECUTAR_E_ENVIAR`. **Caveat the DPO must weigh:** art. 8 §5 ratifies treatments already
   performed under the prior consent "enquanto não houver requerimento de eliminação" — so *stopping
   future processing* is immediate, but *deleting already-collected data* still follows the retention
   matrix (A2) and is not automatic on revocation.
2. **Merit/record path (general DSR).** The full DSR flow still runs for the *documentation* of the
   revocation and any coupled elimination request (subject to A2's retention exceptions).

So: **yes, a dedicated fast path for cessation is advisable** (art. 8 §5 / art. 18 §2 immediacy),
**but it governs *stopping*, not *deleting*** — deletion remains matrix-gated. This resolves the
tension the current `EXECUTAR_E_ENVIAR`-gated bridge creates (immediacy vs. a 15-day merit review).

**Confidence: medium-high on the legal reading; the exact trigger point (intake vs. confirmed) is a
DPO+jurídico+architecture call** because it trades immediacy against the risk of acting on an
unverified revocation (identity gate applies to revocation too — a revocation is also a social-
engineering vector). **Must still check:** whether firing cessation on *intake* needs its own light
identity check, so an attacker cannot revoke a victim's care-program consent to disrupt treatment.

## A6 · SP-OP-ANS-SUBMIT-001 — GAP-ANS-5 human-attestation anonymization gate (DPO Q1, Q2)

**Question (`dpo/PACKAGE.md:140-150`; `review-queue.md:115`).** `lgpd_anonimizado` is seeded `false`
fail-closed, echoed unchanged by the assemble worker (explicitly **not** a real anonymization
computation — the real source `mcp-regdata` is AWS-blocked, issue #16), and flips to `true` only when
a human confirms via `UT_CorrigirPendenciaEnvio` that the dataset is aggregated/anonymized. Is this
human-attestation gate an acceptable substitute for an automated check before ANS transmission? And
is the dataset genuinely aggregate/anonymized (Zona Geral, ADR-0006) with no PHI beyond the opaque
`dataset_ref`?

**SUGGESTED ANSWER — accept the human-attestation gate as an acceptable *interim* posture, with two
conditions.** The design is defensible: fail-closed to `false`, no automated path flips it, and a
named human must attest before a legally binding filing leaves. That is the correct fail-closed
posture while `mcp-regdata` is blocked (T2.6 §2.B confirms the same non-computing echo pattern is used
for `schema_valid`). **Conditions for DPO sign-off:** (1) the attestation must be **non-repudiable** —
tied to a `revisor_id` in the audit record (ADR-0007), so "who attested this dataset was anonymized"
is provable; the same non-repudiation the `ERR_ANS_SUBMIT_NOT_HUMAN` guard gives the transmit act
should extend to the anonymization attestation. (2) When `mcp-regdata` is unblocked, the automated
aggregation/anonymization check **must** replace the echo (do not leave a human attesting a property a
machine could verify — that is a latent GAP-ANS-5 debt, not a permanent design). On Q2: the "no PHI
beyond `dataset_ref`" property should be **enforced by an architecture test**, not assumed — mirror the
W8 recommendation for `read_phi_data` (`review-queue.md:47`): a structural test that fails if any
process variable other than the opaque pointer carries a raw identifier.

**Confidence: medium-high.** The interim gate is reasonable; the risk is that a human attesting
"anonymized" without seeing the actual dataset is attesting a `dataset_ref` pointer, not the data.
**Must still check:** that `UT_CorrigirPendenciaEnvio`'s form gives the human enough to *actually*
verify aggregation (row counts, k-threshold — cross-reference the PROGRAMA k-anonymity ask, A9/Q4),
not just a checkbox; and that the audit record binds the attestation to a person.

## A7 · SP-OP-CANCEL-001 — data retention/elimination on rescission (DPO Q1, Q2; joint jurídico)

**Question (`dpo/PACKAGE.md:162-167`; `juridico/PACKAGE.md:152`).** Confirm the data
retention/elimination policy on contract rescission (contract flags "eliminação de dados pós-rescisão
vs retenção legal de prontuário" as unresolved), and that no PHI beyond the pseudonymized
`matricula_beneficiario` travels in this process's variables (ADR-0006 Zona Geral).

**SUGGESTED ANSWER — the retention matrix in A2 is the single answer; do not create a second, divergent
one.** Jurídico's own Q3 (`juridico/PACKAGE.md:252-253`) warns against "two conflicting legal answers
to the same retention question." So: rescission triggers **no automatic deletion**. Post-rescission
data follows exactly the A2 matrix — prontuário retained ≥20 years (Lei 13.787/2018 art. 6),
financial/contractual retained for the prescriptive/fiscal window (5 years), regulatory data per ANS
RN. A beneficiary who wants deletion after rescission files a DSR elimination request, which is then
subject to the same per-category retention exceptions. The CANCEL process should **not** implement its
own erasure — it should terminate the contract and leave data lifecycle to the LGPD-DSR process +
retention schedule. On Q2 (no PHI beyond `matricula_beneficiario`): confirm via the same structural
PHI-leak test recommended in A6; the pseudonymized matrícula is Zona-Geral-safe per ADR-0006, but the
*absence* of raw PHI should be test-enforced, not assumed.

**Confidence: high on "one matrix, no auto-delete on rescission"; the periods are the A2 caveats.**
**Must still check:** that CANCEL's terminals do not fire any deletion side-effect, and that the
handoff to LGPD-DSR for a post-rescission elimination request exists (or is a known gap).

## A8 · SP-OP-FRAUDE-001 — no-PHI-in-custody, evidentiary retention, snapshot freeze, Merkle custody (DPO Q1–Q4)

**Question (`dpo/PACKAGE.md:177-188`).** (Q1) Is the "no-PHI-in-custody" design (bundle carries only
pointers + hashes) sufficient, with `ERR_PHI_IN_CUSTODY` as the last-line guard? (Q2) Evidentiary
retention (5+ years) vs LGPD minimization — where is the boundary? (Q3) Is freezing the
`feature_snapshot_ref` (ADR-0020, PROPOSTO) DPO-acceptable from a minimization standpoint? (Q4, joint
jurídico) Is the `bundle_root`-Merkle-over-hash-chain custody design evidentiarily acceptable?

**SUGGESTED ANSWER.** (Q1) **The no-PHI-in-custody design is the right architecture and `ERR_PHI_IN_CUSTODY`
is the correct last-line guard** — it operationalizes ADR-0006's two-zone rule and the ADR-0020 custody
projection. Recommend the guard be an *architecture-tested* invariant (a fixture that fails if any
bundle field matches a raw-PHI pattern), not just a runtime check, consistent with the repo's fail-closed
doctrine. (Q2) The retention boundary: **evidentiary retention (5+ years) is a legitimate LGPD art. 16 I
exception (legal/regulatory obligation) + art. 7 VI (exercise of rights in proceedings)** — it *does not*
violate minimization *because the custody bundle holds pointers+hashes, not raw PHI*. The minimization
principle is satisfied by the no-PHI design itself: you retain the *proof of integrity*, not the
sensitive data. This is the crucial DPO framing — the 5-year retention conflicts with LGPD only if it
retains PHI; it doesn't. (Q3) The snapshot freeze (ADR-0020): **acceptable, but it is itself a retention
decision** — freezing a feature snapshot for multi-year dossier reproducibility is retaining derived
data. Recommend: the frozen snapshot must be (a) held in the PHI zone under Object-Lock (ADR-0020's own
design), (b) covered by legal-hold semantics tied to the specific investigation (not a blanket freeze),
and (c) released/deleted when the investigation + appeal windows close. (Q4, joint jurídico) The Merkle
design is evidentiarily *promising* but this is a **jurídico call under CPC/CPP chain-of-custody** — see
B (jurídico FRAUDE Q3). DPO's contribution: the custody chain must not become a backdoor that
re-identifies pseudonymized beneficiaries; confirm the pointers resolve to PHI-zone stores under the
same access controls, never to Zona Geral.

**Confidence: high on Q1/Q2 framing (the no-PHI design resolves the apparent retention-vs-minimization
tension); medium on Q3 (legal-hold scoping is a design detail); Q4 defers to jurídico.** **Must still
check:** the exact legal-hold trigger/release lifecycle for both the custody bundle and the frozen
snapshot, and that k-anonymity (A9/Q4) applies to any population aggregate the fraud process exposes.

## A9 · SP-OP-PROGRAMA-001 — consent chokepoint, revocation boundary, fan-out, k-anon, retention (DPO Q1–Q5)

**Question (`dpo/PACKAGE.md:198-213`).** (Q1) Does `check_consent` (fail-closed, `ERR_PROGRAMA_NO_CONSENT`)
gate **every** PHI path before any stratification/care-plan work? (Q2) Does the interrupting boundary
(`msg.programa.consent_revoked` → `stop_processing` → `End_ProcessamentoInterrompidoRevogacao`) satisfy
art. 18 §2 "immediate effect" — is stopping enough, or does revocation also require active deletion? (Q3)
Is the `consent_revocation_bridge` fan-out (`all_matching=True` by `{tenant_id, beneficiario_pseudo_id}`)
correct for reaching every active enrollment? (Q4) k-anonymity/small-cell parameters for population
aggregates (WP3.5). (Q5) Retention/cessation after revocation vs Lei 13.787/CFM (joint jurídico).

**SUGGESTED ANSWER.** (Q1) **Confirmed — the chokepoint design is correct.** The contract's invariant
(A) makes `check_consent` the single gate before any PHI processing (`SP-OP-PROGRAMA-001.md:15-22`), and
no PHI variable is populated before the gate passes (`:95`). Recommend the DPO's signoff require the
invariant test (contract §Codigos de erro `:184-189`, "nenhum processamento de PHI sem consentimento")
be present and green before FINAL. (Q2) **Stopping is sufficient for the "immediate effect" of art. 18
§2 / art. 8 §5; active deletion is NOT automatically required** — art. 8 §5 ratifies treatments already
performed under prior consent "enquanto não houver requerimento de eliminação." So revocation → cease
processing (the modeled boundary is correct); deletion is a *separate* DSR elimination request subject
to the A2 retention matrix (prontuário content generated during the program is retained ≥20 years). This
is the same two-layer answer as A5. (Q3) **The fan-out is correct.** A titular can have multiple active
PROGRAMA instances (one per programa×ciclo), so `all_matching=True` correlated by `{tenant_id,
beneficiario_pseudo_id}` — not business-key — is exactly right to reach every enrollment; a
business-key correlation would miss siblings (`SP-OP-PROGRAMA-001.md:206-209`). (Q4) k-anonymity — **this
is a genuine DPO parameter the reviewer must set**, not one the code implies. Recommend a *starter*
floor of **k ≥ 5** with small-cell suppression for any Zona-Geral population aggregate (a common health-
data default; ADR-0019/WP3.5), versioned per ADR-0019, but the DPO owns the exact k against your
re-identification risk assessment. (Q5) Retention after revocation = the A2 matrix (joint jurídico);
one answer, not a program-specific divergent one.

**Confidence: high on Q1–Q3 (the modeled design is sound); Q4's exact k is the DPO's own risk call; Q5
defers to A2.** **Must still check:** that `stop_processing` actually halts in-flight Valentina
in-zone work (not just the BPMN mainline), and that the k-floor is enforced before any aggregate
crosses into Zona Geral.

## A10 · **[FOCUS]** `proactive_contact` — channel/consent constraints (T2.9 addendum; DPO half, joint médico-auditor C7)

**Question (`dpo/PACKAGE.md:215-228`).** `operadora.programa.proactive_contact` re-fetches PHI in-zone
and contacts the beneficiary only when `consent_checked==true`, following the Helena/WhatsApp precedent
(D9, `SP-OP-PROGRAMA-001.md:125`) — but the worker is unbuilt (`programa.py:318-319` gap list). Confirm
the WhatsApp/Helena channel is DPO-acceptable for PHI-adjacent proactive outreach under this program's
consent scope, and whether additional consent granularity (beyond the general `programa_cuidado` scope)
is needed for proactive (vs reactive) contact specifically.

**What's at stake.** `proactive_contact` is the one worker that pushes operadora-initiated outbound
messages referencing a care program. The channel precedent is D9 (Helena/WhatsApp, start-signal-only,
PHI-minimized). The W8 autonomy review already graded free-text WhatsApp as riskier than templates
(`review-queue.md:48-49`): `send_beneficiary_message` (free text) = DOWNGRADE-flagged L3;
`send_beneficiary_template` (WABA pre-approved) = L2/OK.

**SUGGESTED ANSWER — acceptable with three DPO constraints; recommend a distinct proactive-contact
consent facet.** WhatsApp/Helena is acceptable for proactive outreach **only** under:

1. **Template-only, no clinical content in the message body.** Proactive messages use WABA
   pre-approved templates (the W8-sanctioned `send_beneficiary_template`, L2), never LLM free-text
   clinical content. The message is start-signal-only (D9): it invites/nudges; any actual clinical
   content is delivered *only after* the beneficiary engages and re-authenticates in-zone. This
   prevents an unencrypted third-party channel from carrying art. 11 sensitive data.
2. **A distinct consent facet for proactive contact.** The general `programa_cuidado` scope authorizes
   *processing* PHI in the program; **proactive outbound contact via WhatsApp is a different purpose**
   (art. 8/9 finalidade) — it reveals to anyone with access to the phone that the beneficiary is in a
   specific care program, which can itself disclose a health condition (art. 11 sensitive). Recommend
   the consent capture include an explicit, separately-revocable opt-in for *proactive channel
   contact* (and channel choice), distinct from the processing consent. This is more protective than
   folding it into the general scope, and matches art. 9's transparency requirement.
3. **Immediate honor of revocation + `consent_checked==true` re-verified in-zone.** The existing
   modeled gate (`consent_checked==true` before contact, re-verified by the chokepoint) is correct and
   must be preserved; revocation of the channel opt-in stops proactive contact immediately (A5/A9).

**Confidence: medium-high on the template-only + revocation constraints (they follow W8 + D9 + art. 11
directly); the "distinct consent facet" is a protective recommendation the DPO may reasonably soften to
a granular purpose-statement within the general scope** — that is the DPO's proportionality call.
**Must still check:** whether your WABA template inventory already separates program-invitation
templates from clinical templates; and coordinate with médico-auditor (C7) on *which risk bands* may be
contacted proactively at all — the DPO channel-consent answer and the clinical-appropriateness answer
must agree that high-risk/urgent findings never go by async WhatsApp.

---

<a name="b--juridico"></a>
# B · Jurídico (legal)

Jurídico is on 14 of 16 contracts (`juridico/PACKAGE.md:44`). The dominant question class is
**RN-currency confirmation**, and the analyst answer-sheet `docs/compliance/rn-currency-review.md`
already verified each citation against ANS primary/authoritative sources (access 2026-07-16). The
suggested answers below **operationalize that report's verdicts** — adopting them is the jurídico
reviewer's act (the report is itself `DRAFT — pending SME confirmation`). Structural (non-currency)
questions are answered per contract.

## B0 · The RN-currency answer key (applies across AUTH, CANCEL, CONTAS, CRED, INADIMPLENCIA, NIP, RECURSO, REEMBOLSO, ADEQUACAO, ANS-SUBMIT)

**SUGGESTED ANSWER — adopt the following supersession map** (source: `rn-currency-review.md` §1–§4,
each row source-cited there). These are the highest-confidence items in this whole document because
they rest on fetched/authoritative ANS text:

| Repo cites | Verdict | In-force norm to assert | Note |
|---|---|---|---|
| RN 259/2011 (garantia de atendimento) | **superseded** | **RN 566/2022** | prazos em **dias úteis** (`[fetched]` gov.br) |
| RN 395/2016 (resposta/negativa escrita) | **superseded** | **RN 623/2024** (vig. 01/07/2025) | tightens negativa fundamentada |
| RN 388/2015 & "388/2016" (NIP/fiscalização) | **superseded + miscited year** | **RN 483/2022** | NIP: 5 d.ú. assist. / 10 d.ú. não-assist. |
| RN 412/2016 (cancelamento a pedido) | **superseded** | **RN 561/2022** — **NOT RN 593** | efeito imediato/irretratável |
| RN 305/2012 & "305/2016" (TISS) | **superseded + miscited year** | **RN 501/2022** | revogou 305/2012 + 341/2013 |
| RN 424/2017 | **vigente for junta médica ONLY** | keep in AUTH; **remove** from ANS-SUBMIT/CONTAS/RECURSO glosa-prazo | those belong to RN 501/2022 |
| RN 567/2018 (descred. hospitalar) | **wrong year + subject** | **RN 567/2022** (não-hospitalar) + **RN 585/2023** (hospitalar, uncited) | cite BOTH by prestador type |
| RN 593/2023 (inadimplência) | **vigente & correct** | keep for inadimplência; **the "consolida 412" claim is FALSE** | 412's successor is 561/2022 |
| RN 209/2009 ("utilização") | **superseded + miscited** | **RN 451/2020** (capital) | not "utilização"; econ-fin |
| RN 124/2006 ("SIP") | **superseded + miscited** | penalidades → **RN 489/2022**; SIP → RN 551/2022 → **EXTINCT by RN 639/2025** | see D (regulatório) |
| RN 162/2007, 186/2009, 195/2009 (carência family) | **superseded** | **RN 558/2022, RN 438/2018, RN 557/2022** | carencia_check.dmn |

## B1 · SP-OP-AUTH-001 — RN currency, 24h written-denial, junta médica (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:126-131`).** Confirm RN 259/395/424 currency; confirm the 24h
written-denial-notice (RN 395 art. 10) is embedded in `send_denial_notice` with correct day-basis;
what does RN 424 require for junta médica tie-break/deadlines?

**SUGGESTED ANSWER.** RN currency per B0: **RN 259/2011 → RN 566/2022; RN 395/2016 → RN 623/2024; RN
424/2017 stays (junta médica, correct)**. The **negativa por escrito** obligation moved from RN 395 art.
10 to **RN 623/2024**, which per the currency review "reduz a negativa a termo com fundamentação
explícita e linguagem clara" (`rn-currency-review.md:76`) — so `send_denial_notice` must be re-cited to
RN 623/2024, and jurídico should confirm the exact fundamentação fields RN 623 requires (this couples
to the médico-auditor's `UT_AnaliseMedicoAuditor` form-field question, `review-queue.md:18`). On junta
médica (RN 424): the RN forms a junta of 3 profissionais and the **desempatador's parecer is vinculante**
(`rn-currency-review.md:86`) — that is the tie-break rule; the *deadline* is not pinned in the repo and
jurídico must set it (RN 424 text). **Day-basis: dias úteis** for the response classes (see E).

**Confidence: high on the supersession; the exact RN 623 fundamentação fields and the RN 424 deadline
are text-level details jurídico must read from the norms.** **Must still check:** RN 623 per-class
figures (`rn-currency-review.md` R5); whether the 24h→RN 623 change alters the timer value.

## B2 · SP-OP-CANCEL-001 — RN 593 vs 412, notification/purga windows, unilateral-rescission matrix, retention (jurídico Q1–Q4)

**Question (`juridico/PACKAGE.md:144-152`).** Does RN 593/2023 consolidate/replace RN 412/2016? Confirm
notification-prévia and purga windows (business vs calendar days); confirm the unilateral-rescission
hypotheses matrix by `tipo_plano`; retention on rescission (joint DPO).

**SUGGESTED ANSWER.** **No — RN 593 does NOT consolidate/replace RN 412.** Per B0 and
`rn-currency-review.md:78-80`: cancelamento *a pedido do beneficiário* → **RN 561/2022** (revogou 412;
efeito imediato/irretratável); RN 593/2023 is a *distinct* norm governing *inadimplência* notification.
The contract's premise and the INADIMPLENCIA-001 open question ("RN 593 supersede/consolida 412?") are
**answerable: NO — 412's successor is 561/2022**. So CANCEL-001 should cite RN 561/2022 for the
beneficiary-request path and reserve RN 593/2023 for the inadimplência-suspension terminal only. On the
unilateral-rescission matrix (Lei 9.656 art. 13 §único): individual/familiar plans have the art. 13
protections (non-rescission except fraud or non-payment >60 days); coletivos follow the estipulante's
contract — this is correct as the contract frames it, but jurídico must pin the exact hypótese list.
Retention: **use the A2 matrix; do not create a second answer** (jurídico's own Q4 warns of this).
**Day-basis:** the RN 593 windows ("50º dia", "10 dias") read as dias corridos but are **not fetched** —
this is register item R4 (`rn-currency-review.md:233`), a genuine open jurídico question (see E).

**Confidence: high on 593≠412 (fetched sources); medium on the day-basis (explicitly unresolved).**
**Must still check:** RN 561/2022 text for the "efeito imediato" mechanics; the exact art. 13 hypótese
matrix; RN 593 day-basis (R4).

## B3 · SP-OP-INADIMPLENCIA-001 — RN 593, day-basis, contract_termination coverage (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:228-236`).** Same 593-vs-412 question; dias úteis vs corridos for
purga/notificação (até 50º dia)/período mínimo; confirm `contract_termination` (L0) covers both
CANCEL-001 and INADIMPLENCIA-001.

**SUGGESTED ANSWER.** RN 593/2023 is **correct and in force for inadimplência** — keep it (B0). Both
contracts must cite the **same** vigente source for the same effect, and the harmonization already
resolved ownership: CANCEL-001 owns the sole rescission terminal; INADIMPLENCIA-001 owns
cobrança/purga/suspensão + a neutral `handoff_rescisao` (`review-queue.md:329-337`, GAP-INAD-5 SHIPPED).
So `contract_termination` (L0 `_hard_frozen`) correctly spans both. RN 593 requirements to encode
(per `rn-currency-review.md:90`): mínimo **2 mensalidades** não pagas em 12 meses; **notificação até o
50º dia**; exclusão/rescisão só **10 dias após** a notificação. **Day-basis is the open R4 question** —
jurídico must confirm dias corridos vs úteis and the counting anchor (não-pagamento vs notificação);
the repo currently encodes ISO calendar durations, which *may* be correct for this family specifically
(unlike the atendimento/NIP families where dias-úteis is confirmed — see E, the "mixing bases" bug).

**Confidence: high on RN 593 substance; the day-basis is genuinely open (R4).** **Must still check:**
R4 day-basis; that no dead `register_contract_rescission`/`RESCINDIR` path lingers (GAP-INAD-5 dropped it).

## B4 · SP-OP-CONTAS-001 — RN 305→501, P30D SLA, no-auto-fraud-flag (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:162-167`).** Confirm RN 305/2012 (TISS) currency; confirm P30D
triagem/análise SLA vs RN 424 + day-basis; confirm no branch auto-flags `fraud_accusation` (L0).

**SUGGESTED ANSWER.** **RN 305/2012 → RN 501/2022** (TISS Componente de Comunicação; B0). Crucially,
the P30D SLA anchor should be re-cited: glosa/recurso prazos belong to **RN 501/2022 (TISS)**, **not RN
424/2017** — RN 424 is junta médica only, misattributed here (`rn-currency-review.md:87`). On the
fraud-referral policy: **confirmed correct** — the handoff to SP-OP-FRAUDE-001 must always be human
(`encaminhar_fraude`), never an auto-flag, because `fraud_accusation` is L0-hard (DL-0005; ADR-0008).
This is the right posture and jurídico should ratify it explicitly (do not treat silence as consent to
auto-route). **Day-basis:** dias úteis for TISS/analysis windows (confirm exact figure in RN 501/424 as
applicable).

**Confidence: high.** **Must still check:** the exact P30D figure and anchor in RN 501/2022; that the
médico-auditor concurs the default (no auto-route for técnica/clínica glosa) stays in force (C2).

## B5 · SP-OP-CRED-001 — RN 567/585 currency, prior-notice, hard-frozen promotion, fraud-referral (jurídico Q1–Q4)

**Question (`juridico/PACKAGE.md:178-186`).** Confirm RN 567 and RN 566 currency; confirm the
prior-notice cure-window and substitute-provider trigger; what sign-off is required to promote
`provider_decredentialing` to `_hard_frozen.yaml` (L1); confirm the fraud-referral mirrors CONTAS-001.

**SUGGESTED ANSWER.** **Two norms, not one** (`rn-currency-review.md:95-97, 212`): **RN 567/2022**
(substituição de prestadores **NÃO hospitalares**; correct the "2018" year) **and RN 585/2023**
(alterações na rede **HOSPITALAR** — substituição de entidade hospitalar, comunicação 30 dias,
portabilidade 180 dias, CNES; **currently uncited**). Since CRED-001 models hospital descredenciamento
(`End_PrestadorDescredenciado`), it needs **both**, mapped by `tipo_prestador`. RN 566/2022 (rede
dimensioning) is current — keep. Prior-notice: RN 567/585 require prior notice + equivalent substitute;
the P30D `cred_prior_notice` window aligns with the 30-day communication requirement (confirm exact
antecedência per norm and per prestador type). The `_hard_frozen` L1 promotion of
`provider_decredentialing` requires **architecture + compliance CODEOWNERS sign-off** (`review-queue.md:148-151`)
— it is a governance change (non-rebaixável por overlay), not a routine draft. Fraud-referral: yes,
mirror CONTAS-001 — no auto-flag of `indicio_irregularidade_sinalizado`, always human.

**Confidence: high on the 567/585 split (fetched sources); the exact antecedência figures are text-level.**
**Must still check:** RN 585/2023 in-force dates (R8: 01/09/2024 vs 31/12/2024 for substitution rules);
the exact substitute-equivalent trigger (hospital vs isolated provider).

## B6 · SP-OP-FRAUDE-001 — L0 accusation boundary, referral obligations, custody, RN currency (jurídico Q1–Q5)

**Question (`juridico/PACKAGE.md:204-218`).** (Q1) Does `decisao_fraude=ACUSAR_FRAUDE` constitute
`fraud_accusation` by itself, or is the accusation the downstream act (`End_EncaminhadoJuridico`)? (Q2)
Referral obligations (ANS/civil/penal) — deadlines/form/authority/tier. (Q3) Merkle custody
evidentiary acceptability (joint DPO). (Q4) RN 593/567 currency + which process owns each referral
terminal. (Q5) Is the `encaminhar_fraude` seam only in CONTAS-001 an acceptable DRAFT gap?

**SUGGESTED ANSWER.** (Q1) **This is the load-bearing legal call the contract itself defers, and it is
genuinely jurídico's to make** — but the defensible position: `decisao_fraude=ACUSAR_FRAUDE` is an
*internal investigative conclusion*, and the **L0 `fraud_accusation` boundary should sit at the
downstream external act** (`End_EncaminhadoJuridico` / the referral to an authority), because an
accusation with legal consequence is the *communication to a third party/authority*, not the internal
finding. Placing L0 at the internal decision would over-gate routine investigative work; placing it at
the referral captures the act that actually accuses. **Recommend jurídico ratify the boundary at the
referral act**, with the internal `ACUSAR_FRAUDE` decision still human-made (`UT_DecisaoInvestigador`)
but the L0-hard non-repudiation gate on the outbound referral. (Q2) Referral obligations
(ANS/civil/penal deadlines, competent authority, tier ladder) are **not pinned anywhere** — this is
substantive legal research jurídico must supply; the AI cannot responsibly invent referral deadlines.
(Q3) Merkle custody — see A8/Q4: the `bundle_root`-over-hash-chain is a *projection over ADR-0007*
(ADR-0020) sealed before the human decision; its CPC/CPP acceptability turns on whether the chain is
tamper-evident and the timestamps are trustworthy — **recommend jurídico require a qualified timestamp
(ICP-Brasil/carimbo do tempo) on the `bundle_root` seal** to strengthen probative value, but the
acceptability judgment is jurídico's. (Q4) RN 593/2023 (fraud as cancellation ground) and RN 567/2022
+ 585/2023 currency per B0; ownership: CANCEL-001 owns beneficiary-contract terminals, INADIMPLENCIA-001
the inadimplência terminal, CRED-001 the network terminal — the notifications-bridge already splits
`fraude.start_contratual` by `entidade_tipo` (`review-queue.md:184`). (Q5) The asymmetric
`encaminhar_fraude` seam (only CONTAS-001 today) is an **acceptable known DRAFT gap** — the other
processes (RECURSO/REEMBOLSO/CANCEL/NIP) need the symmetric human handoff built, but a missing seam is a
build task, not a compliance defect that blocks DRAFT status.

**Confidence: medium on Q1 (defensible but genuinely jurídico's call); LOW on Q2 (referral
deadlines require primary legal research I cannot responsibly draft — see the "could not draft"
note at the end); high on Q4.** **Must still check:** Q2 in full (this is the item most needing a
real lawyer); the qualified-timestamp recommendation for custody.

## B7 · SP-OP-LGPD-DSR-001 — retention matrix, revocation pathway, CANCEL interaction (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:246-253`).** (Q1) When does prontuário retention (Lei
13.787/2018/CFM) override elimination? (Q2) Does consent revocation need a dedicated legal pathway
(given PROGRAMA depends on it)? (Q3) Confirm the CANCEL interaction doesn't create two conflicting
retention answers.

**SUGGESTED ANSWER.** (Q1) = **A2 matrix**. Prontuário retention overrides elimination for health-data
categories under **Lei 13.787/2018 art. 6** (≥20 years from last entry) via LGPD art. 16 I; other
categories eliminate once their legal/fiscal/contractual obligation expires. (Q2) = **A5**: a dedicated
fast path for *cessation* (art. 8 §5 immediacy) is advisable; *deletion* stays matrix-gated. (Q3) =
**one matrix, enforced identically in CANCEL and DSR** (A7) — the whole point of A2 is a single
source of truth so CANCEL and DSR cannot diverge.

**Confidence: high (this is the legal spine of A2/A5/A7).** **Must still check:** the exact prontuário
floor for *your* record types (some ancillary records may have shorter statutory retention).

## B8 · SP-OP-NIP-001 — RN 483/2022, business-key, static legal-review group (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:262-270`).** Confirm RN 388/2016 currency and the 5/10-business-day
split; confirm the stable business-key (`numero_nip_ans` vs `protocolo_ans`); confirm the static
candidate group (`juridico-regulatorio,medico-auditor`) and the "revisão sempre jurídica" invariant.

**SUGGESTED ANSWER.** **RN 388/2016 → RN 483/2022** (B0; the "2016" year is also wrong — the norm was
388/2015, revoked by 483/2022). The **5 dias úteis (assistencial) / 10 dias úteis (não-assistencial)**
values survive; only the citation and day-basis note change (`rn-currency-review.md:72`). `MANTER_NEGATIVA`
is `authorization_denial`-class L0-hard and correctly frozen as `nip_manter_negativa` in `_hard_frozen`
(`review-queue.md:282-296`) — the "revisão sempre jurídica" invariant (static
`juridico-regulatorio,medico-auditor` group, three incoming flows) is **correct and non-negotiable**;
ratify it. Business-key: `numero_nip_ans` vs `protocolo_ans` is an open item flagged with regulatório
(`juridico/PACKAGE.md:266`) — recommend the **ANS-assigned NIP number** (`numero_nip_ans`) as the stable
business key because it is the regulator's own identifier and survives internal protocol re-issuance;
confirm with regulatório (D).

**Confidence: high on RN 483 and the static-group invariant; the business-key pick is a
jurídico+regulatório joint call (I recommend `numero_nip_ans`).** **Must still check:** day-basis (E,
confirmed dias úteis for NIP); the exact RN 483 upload window (10 d.ú.).

## B9 · SP-OP-RECURSO-001 — RN currency, R5 auto-route inadmissibility, P30D ceiling (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:310-318`).** Confirm RN 424/305/501 currency; **R5**: should hard
procedural-deadline inadmissibility (prazo expirado) ever auto-route without a human, or must it always
be `ANALISE_HUMANA`? Confirm the P30D absolute ceiling vs RN 424.

**SUGGESTED ANSWER.** Currency: glosa/recurso TISS → **RN 501/2022** (not 305/2016 — wrong year; not RN
424 for glosa prazo — junta médica only); RN 424/2017 stays only for the junta facet (B0). **R5 —
recommend REJECTING the auto-route: keep `ANALISE_HUMANA` as the default.** Even a "clear" prazo-expirado
inadmissibility can turn on a contested anchor date (recebimento vs ciência — `review-queue.md:112`) or
a tempestividade exception; auto-denying a recurso on a computed deadline is an adverse act that should
stay human, consistent with the repo's fail-closed/no-auto-adverse doctrine (ADR-0018) and the
contract's own default. The R5 note explicitly says auto-route requires jurídico sign-off — **withhold
that sign-off.** The P30D ceiling (three boundary timers sharing one absolute deadline anchored on
`data_recebimento_recurso_iso`) is a defensible reading of RN 501/424; confirm the legal anchor
(recebimento vs ciência) and day-basis (`review-queue.md:112`).

**Confidence: high on R5 (reject auto-route — the conservative, doctrine-aligned call); high on
currency.** **Must still check:** the exact prazo recursal figure in RN 501/2022; the legal anchor for
the ceiling.

## B10 · SP-OP-REEMBOLSO-001 — RN 259→566, Lei 9.656 art. 12, reference table (jurídico Q1–Q2)

**Question (`juridico/PACKAGE.md:332-337`).** Confirm RN 259 and Lei 9.656 art. 12 currency and the
~30-day deadline; confirm the reimbursement reference table has regulatory + actuarial sign-off.

**SUGGESTED ANSWER.** **RN 259/2011 → RN 566/2022** (B0). Lei 9.656 art. 12 (reimbursement in
livre-escolha/fora-de-rede/urgência-emergência) is a *statute* and remains in force — keep. The
**~30-day reimbursement deadline is NOT resolved to a specific vigente RN** — this is register item R3
(`rn-currency-review.md:232`), a genuine open jurídico+regulatório question; do not assert a norm the
analyst could not verify. The reference table (múltiplo/limite by category) is **joint with regulatório
+ finanças (actuarial)** — jurídico confirms the legal ceiling/floor exists and is non-discriminatory;
the actuarial values are finanças's.

**Confidence: high on RN 566; the 30-day norm is genuinely unresolved (R3) — flag, don't invent.**
**Must still check:** R3 (which norm sets the reimbursement deadline); the shorter urgência window.

## B11 · SP-OP-ADEQUACAO-001 — RN 259→566, fallback commitment, cycle periodicity (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:74-82`).** Confirm RN 259 and RN 566 currency; confirm the legal basis
and limits of the "compromisso financeiro de fallback"; confirm `ciclo_avaliacao` periodicity.

**SUGGESTED ANSWER.** **RN 259/2011 → RN 566/2022** (B0; RN 566 *is* the 259 successor and is already
cited — retire the stale 259). The fallback commitment (livre escolha / reembolso garantido /
contratação ad-hoc) is triggered by RN 566's garantia-de-atendimento failure: when the operadora cannot
provide in-network access within the maximum times, it must guarantee attendance by alternative means at
its own cost. The **legal basis is RN 566/2022 (garantia de atendimento) + Lei 9.656**; the *limits/ceiling*
of the ad-hoc contracting are the operadora's to define but cannot fall below the guaranteed-attendance
standard. Periodicity (`ciclo_avaliacao` trimestre vs mês): the network-change bridge derives it as
**trimestre** (`YYYY-Qn`, `review-queue.md:198`) — recommend trimestral as the RN 259/566 evaluation
cadence, but this is a **regulatório+PO** call (D) more than jurídico.

**Confidence: high on RN 566; the fallback ceiling and cycle are policy calls to coordinate with
finanças/regulatório.** **Must still check:** RN 566's exact fallback mechanics; the cycle periodicity
(regulatório D).

## B12 · SP-OP-ANS-SUBMIT-001 — legal basis of surviving filings, sanction exposure, retry sufficiency (jurídico Q1–Q3)

**Question (`juridico/PACKAGE.md:98-117`).** Confirm the correct in-force legal basis for each surviving
filing (DIOPS, Monitoramento-TISS, NIP-response), and the sanction exposure of transmitting under a
wrong/extinct citation; confirm the `juridico-regulatorio` group for NIP-originated filings; is the
retry/backoff policy legally sufficient, or does a transmission failure carry its own notice obligation?

**SUGGESTED ANSWER.** Per B0 + the RN 639 re-scope: **SIP is EXTINCT** (RN 639/2025, primary-confirmed,
DL-0029) — do **not** attach a legal opinion to SIP. The two miscited literals the pipeline still carries
must be re-based before any live filing: **`RN_209_UTILIZACAO` → RN 451/2020** (capital/econ-fin, the
DIOPS basis) and **`RN_388_QUALIDADE` → RN 483/2022** (fiscalização/NIP — and NIP is *event-driven*, not
a periodic filing). Sanction exposure of a wrong/extinct citation: a filing transmitted under a dead norm
risks **intempestividade/inconsistência** findings and the penalties regime (now **RN 489/2022**,
`rn-currency-review.md:100`) — the citation is not cosmetic because the filing is legally binding and
non-repudiable. The `juridico-regulatorio` static group on NIP-originated filings (`origem_envio==nip_filing`)
is **correct and load-bearing** — the NIP-response filing (RN 483/2022) is the primary in-force reason the
transmit path is kept (T2.6 §1.5), so "revisão sempre jurídica" on it is right. Retry sufficiency: the
`PT5M/PT30M/PT2H` backoff is a *technical* fail-safe (DL-0019), but jurídico's real concern is correct:
**a transmission failure that lets a filing deadline lapse while retries run carries an intempestividade
risk** — recommend the design surface an explicit human/legal notice when retries approach the regulatory
due date (not just exhaust silently), so the operadora can invoke any force-majeure/technical-unavailability
provision with ANS before the breach.

**Confidence: high on the re-citations and sanction framing (fetched sources); the "notice-on-deadline-
approach" recommendation is a prudent legal add the reviewer should weigh.** **Must still check:** the
DIOPS governing norm exactly (R2, unresolved); whether ANS has a technical-unavailability notification
channel for missed periodic filings.

## B13 · SP-OP-PAGTO-001 (light) — segregation of duties, collegiate quorum (jurídico Q1–Q2)

**Question (`juridico/PACKAGE.md:280-284`).** Confirm the segregation-of-duties policy (approver ≠
requester) has a documented compliance basis; confirm whether "aprovação colegiada" for the comitê tier
needs to be modeled as a quorum (parallel/multi-instance User Task) to be legally defensible as
collegiate.

**SUGGESTED ANSWER.** There is **no ANS RN** on the payment ceiling (`regulatorio` correctly excludes
PAGTO, `regulatorio/PACKAGE.md:46-47`) — this is internal financial governance. The segregation-of-duties
control (approver ≠ requester) is a **SOX-like / internal-controls** requirement; its basis should be a
**documented internal financial-controls policy**, not merely implied by the DMN — recommend jurídico +
finanças jointly reference or author that policy so the control is auditable. On the collegiate quorum:
**yes — if the comitê tier is meant to be genuinely collegiate, it should be modeled as a quorum**
(parallel/multi-instance User Task requiring N approvers), because a single user clicking "committee
approved" is not a defensible collegiate decision. A single-approver task labeled "comitê" is a
governance fiction. Recommend modeling the quorum explicitly.

**Confidence: high on both (standard internal-controls reasoning).** **Must still check:** whether an
existing corporate governance policy already fixes the quorum size; coordinate with finanças on the tier
ladder.

---

<a name="c--medico-auditor"></a>
# C · Médico-auditor (medical auditor)

Médico-auditor is on 9 of 16 contracts (`medico-auditor/PACKAGE.md:23`). The two orchestrator-flagged
focus items — **`stratify_risk` starter criteria** and the **`proactive_contact` clinical half** — are
in C7. Where a question is a candidate-group/taxonomy matter, it is a PO decision the médico-auditor can
only confirm against the org chart; those are noted but not decided here.

## C1 · SP-OP-AUTH-001 — DUT sourcing, SLA basis, RN currency, junta médica, groups (médico Q1–Q5)

**Question (`medico-auditor/PACKAGE.md:43-61`).** (Q1) Confirm the four `dut_criteria_*` + `dut_rol_coverage`
DMNs resolve `dut_atendida` and should be cited. (Q2) SLA basis: are PT2H/P10D/P5D business-day windows,
and does calendar-day ISO compress the review window below RN 395/Lei 9.656 art. 35-C? (Q3) RN 259/395/424
currency. (Q4) Junta médica tie-break/deadlines. (Q5) Candidate groups.

**SUGGESTED ANSWER.** (Q1) **Yes** — `dut_criteria_bariatrica/oncologia_pet_ct/terapias_especiais` +
`dut_rol_coverage` are the worker's source for `dut_atendida` (they feed the Rafael graph as informative
inputs on the human route, `review-queue.md:70-84`); the contract **should cite them explicitly**. Note
they are all `DRAFT — requires médico-auditor review` with **synthetic clinical thresholds** (IMC 35/40,
session limits, etc.) that need real clinical validation before deploy. (Q2) **The SLAs are meant as
business-day windows but are encoded as calendar-day ISO** — this is the cross-cutting E problem. For
*attendance/authorization*, RN 566/2022 and RN 623/2024 count in **dias úteis** (confirmed, `[fetched]`),
so a calendar-day ISO encoding fires *earlier* than the legal deadline (conservative for compliance but
produces false breaches). The clinically dangerous direction would be the opposite (compressing the
window *below* the legal minimum) — that does **not** happen with the current conservative encoding, but
the médico-auditor should confirm PT2H for urgência (Lei 9.656 art. 35-C "imediato") is a clinically
adequate operationalization, not a compression. (Q3) RN currency per B0: **259→566, 395→623, 424 stays**.
(Q4) Junta médica (RN 424): 3 profissionais, desempatador vinculante; the *deadline* is unpinned —
médico-auditor + jurídico set it. (Q5) Candidate groups (`medico-auditor`/`coordenacao-auditoria-medica`/
`junta-medica`) are **PROPOSTO — a PO/IdP taxonomy call**; médico-auditor confirms they map to real
clinical roles.

**Confidence: high on Q1/Q3; the SLA-basis (Q2) is the shared E question; junta deadline (Q4) needs the
RN text.** **Must still check:** the synthetic DUT thresholds (real clinical validation); PT2H adequacy
for urgência.

## C2 · SP-OP-CONTAS-001 — R4 auto-route default, glosa taxonomy, group (médico Q1–Q3)

**Question (`medico-auditor/PACKAGE.md:72-82`).** (Q1) Confirm the default (no auto-route for
técnica/clínica glosa) stays in force — do not treat silence as consent. (Q2) Review the
`categoria_normalizada` taxonomy for clinical accuracy vs the TISS glosa reason-code table. (Q3) Confirm
`auditoria-contas` group name.

**SUGGESTED ANSWER.** (Q1) **Confirm the default stays — do NOT enable the auto-route exception.**
Técnica/clínica glosa acceptance is `authorization_denial`-adjacent (L0-hard); auto-routing it would
let an adverse clinical/technical denial proceed without human review, contradicting ADR-0018. The R4
exception (auto-route puramente formatacional) should remain gated on an explicit compliance sign-off
that is **not** being given here. (Q2) The `categoria_normalizada` taxonomy
(`tecnica/administrativa/clinica/valor/documental/desconhecida`) should be validated against the
**official TISS/ANS glosa reason-code table in force (RN 501/2022)** and the repo's own `GlosaReasonCode`
26-code set (`review-queue.md:118`) — this is real clinical/faturamento validation the médico-auditor +
equipe-faturamento must do against the vigente table; the AI cannot certify the mapping. (Q3)
`auditoria-contas` is PROPOSTO (PO/IdP).

**Confidence: high on Q1 (keep default); Q2 is genuine validation work; Q3 is PO's.** **Must still
check:** every reason-code→categoria mapping against the current TISS table.

## C3 · SP-OP-CRED-001 (secondary) — clinical input to network criteria (médico Q1–Q2)

**Question (`medico-auditor/PACKAGE.md:95-101`).** (Q1) Does any provider-quality/clinical-adequacy
criterion belong in `dentro_criterios_rede` (RN 566), or is it correctly out of scope for
médico-auditor? (Q2) Should médico-auditor have an advisory role in the cred/decred analysis given
`false_decredentialing_rate == 0` is a KPI?

**SUGGESTED ANSWER.** (Q1) `dentro_criterios_rede` is today a **purely administrative/geographic** fact
(RN 566 network dimensioning). Recommend it **stays administrative** — network adequacy is measured by
time/distance/capacity, not clinical quality; injecting clinical-quality criteria into an objective
dimensioning fact would blur an administrative measure with a clinical judgment. **However**, provider
*quality* (as distinct from network *adequacy*) is a legitimate clinical concern — recommend it live as
a **separate advisory input to `UT_AnaliseDescredenciamento`**, not inside `dentro_criterios_rede`. (Q2)
Given the `false_decredentialing_rate == 0` KPI, an **advisory (non-decision) médico-auditor role** in
the descredenciamento analysis is defensible when the descredenciamento is quality-motivated — but the
decision stays with `gestão-rede`/`jurídico-rede`. So: advisory yes, decision no.

**Confidence: medium-high (this is a reasonable scoping recommendation, but the "should clinical quality
gate the network?" question is genuinely the auditor's to weigh).** **Must still check:** whether your
org already has a provider-quality review distinct from network adequacy.

## C4 · SP-OP-ESCALATION-001 — retro-verification of SLA values (médico Q1–Q4 + regulatório) + the signoff

**Question (`medico-auditor/PACKAGE.md:116-129`; `regulatorio/PACKAGE.md:209-214`).** Confirm the ack
SLAs (P1 `PT5M`, P2 `PT30M`, P3 `PT4H`) and resolution SLAs (P1 `PT30M`, P2 `PT4H`, P3 `PT24H`) are
clinically adequate and consistent with Lei 9.656 art. 35-C "imediato"; confirm the P1/P2/P3 severity
mapping and the fail-safe catch-all (unknown → P2, never P3); confirm the upstream `triage_redflag_*`
tables; confirm candidate groups. Then **produce the signoff file** —
`docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml`.

**What's at stake.** This is the one **FINAL contract with no signoff on record** — grandfathered in
`signoffs/retro-verification-pending.yaml` under a shrink-only exception that the sign-off gate warns on
every run. The exception is removed the moment the real médico-auditor + regulatório signoff files land
(`retro-verification-pending.yaml:47-62`). The SLA values are DRAFT despite the FINAL status
(`escalation_routing` header "shape FINAL, SLAs DRAFT").

**SUGGESTED ANSWER (advisory only — the AI cannot and does not produce the signoff file).** The SLA
values are **clinically defensible as an internal escalation policy**: for an *urgência/emergência* red
flag (P1), a **5-minute ack / 30-minute resolution** is aggressive and appropriate — Lei 9.656 art. 35-C
requires "imediato" for urgência/emergência, and PT5M ack operationalizes "imediato" conservatively
(faster is safer). P2 (moderada) PT30M/PT4H and P3 (leve) PT4H/PT24H are reasonable graded windows. The
fail-safe catch-all (unknown motivo → **P2, never P3**) is the correct conservative default — an
unclassified escalation should never get the *lowest* priority. The severity mapping (grave→P1,
moderada→P2, leve→P3) is sound. **The four `triage_redflag_*` tables (adult/gestante/mental-health/
pediatric) carry synthetic clinical content** (`review-queue.md:10-13`) — these need real clinical
protocol validation by the respective specialists (the pediatric febre <3 meses threshold, the obstetric
pré-eclâmpsia ≥20s, etc.) before the médico-auditor can attest they reflect current red-flag protocol.
**On the signoff file: this is exclusively the human médico-auditor's + regulatório's act** — this AI
document explicitly does **not** create, pre-fill, or template it. Once the human reviewers confirm the
SLA values and the red-flag tables, they author the signoff YAML per `README.md` §"Signoff artifact
spec" (a list with `reviewer_name`/`role`/`date`/`contract_version_reviewed: v1.0.0`/`verdict`/`notes`),
and the `retro-verification-pending.yaml` exception is then removed.

**Confidence: high that the SLA *shape* is defensible as internal policy; the red-flag *clinical content*
genuinely needs specialist validation, and the "imediato" anchor is a jurídico/regulatório co-sign
(art. 35-C).** **Must still check:** each `triage_redflag_*` table against current protocol; whether
PT24H for a P3 "leve" is consistent with your assistencial policy; and that the signoff is produced by
the humans, not inferred.

## C5 · SP-OP-FRAUDE-001 (secondary) — clinical plausibility of scoring indicators (médico Q1–Q2)

**Question (`medico-auditor/PACKAGE.md:155-162`).** (Q1) Should médico-auditor review the clinical
plausibility thresholds — especially `phantom_no_diagnosis.dmn` and `phantom_suspicious_prefix.dmn` —
before they feed `indicadores_presentes`/`score_indicadores`? (Q2) Confirm `intensidade_investigacao`
(LEVE/APROFUNDADA/PRIORITARIA) is purely an investigation-priority signal, never a de facto clinical
verdict.

**SUGGESTED ANSWER.** (Q1) **Yes** — the 7 scoring DMNs are ported 1:1 from the v1 donor with
**synthetic, unreviewed thresholds** (`medico-auditor/PACKAGE.md:143-154`; orphan-allowlisted pending
`operadora.fraude.score_indicators` wiring). The two phantom tables score coding/diagnosis patterns and
**must** get médico-auditor clinical-plausibility review before wiring (T2.7 phase 2) — a "phantom
no-diagnosis" flag on a legitimately un-coded encounter would generate false fraud signals. This review
should happen *before* the score feeds anything. (Q2) **Confirm it is priority-only.** The whole donor
inversion removed the verdict column — `indicador_score`/`indicador_label`/`motivo` are *assembly/routing
facts*, never a verdict (`review-queue.md:181`; the contract's "não são portadas (são o anti-padrão
puro)" rule). The `intensidade_investigacao` routing must **only** set investigation depth, never
function as a clinical or fraud verdict — the accusation stays with `UT_DecisaoInvestigador`. Recommend
the médico-auditor's signoff explicitly record that the score is a triage signal, so a future change
cannot silently promote it to a verdict.

**Confidence: high on both (the architecture already enforces "no verdict"; the thresholds genuinely
need clinical review).** **Must still check:** the phantom/upcoding/unbundling thresholds against real
coding-audit experience.

## C6 · SP-OP-NIP-001 — joint review workability, assistencial deadline adequacy (médico Q1–Q2)

**Question (`medico-auditor/PACKAGE.md:173-179`).** (Q1) Is joint `juridico-regulatorio` +
`medico-auditor` review of `UT_RevisaoJuridicaNip` operationally workable — who has the casting vote on
`decisao_nip==MANTER_NEGATIVA`? (Q2) From a clinical-urgency standpoint, is ~5 business days enough for
a clinically substantive NIP response?

**SUGGESTED ANSWER.** (Q1) The joint review is workable, but a casting-vote rule is needed. Recommend:
**on a clinical-merit NIP, the médico-auditor's clinical judgment governs the *clinical* question
(was the negativa clinically correct?), and jurídico governs the *legal sufficiency* (fundamentação,
prazo, form).** `MANTER_NEGATIVA` requires *both* to agree; if the médico-auditor finds the negativa
clinically unsustainable, that should **override toward `CONCEDER`** (the no-adverse/fail-safe direction),
because maintaining a clinically wrong denial is the harmful outcome. So the casting vote toward the
*less adverse* outcome is the safe rule. (Q2) The ~5 business days (RN 483/2022 assistencial) is the
**regulatory** window, not a clinical-safety window — if the NIP concerns an *urgent* clinical situation,
5 business days may be too slow, and such a case should have already escalated via SP-OP-ESCALATION-001
(P1/P2) rather than wait on the NIP cycle. Recommend the médico-auditor confirm that urgent clinical NIPs
are flagged for expedited handling, not held to the full 5-day window.

**Confidence: high on the casting-vote-toward-less-adverse rule; the urgency-flag recommendation is a
sensible clinical safeguard.** **Must still check:** RN 483 exact assistencial window; the interaction
with the escalation process for urgent cases.

## C7 · **[FOCUS]** SP-OP-PROGRAMA-001 — `stratify_risk` criteria + `proactive_contact` clinical half (médico Q1–Q3 + T2.9 addendum)

### C7a — `stratify_risk`: ratify the fail-closed default; do NOT invent clinical bands

**Question (`medico-auditor/PACKAGE.md:190-197, 204-224`).** (a) Ratify that the fail-closed
`risco_estratificado="alto"` → `ANALISE_HUMANA` default is the correct clinical posture (now shipped
behavior, #126). (b) Supply the actual clinical stratification criteria — what inputs/thresholds should
the (unwired) Valentina `care.stratify` delegation compute into `{baixo, moderado, alto}`? Plus the
program taxonomy (crônicos/pré-natal/oncologia/APS) and `UT_DecisaoClinica` discharge criteria.

**What's at stake in the code.** `programa_routing.dmn` consumes `risco_estratificado ∈ {baixo,
moderado, alto}` and `elegibilidade_criterios_atendidos`, and is itself fully fail-safe (catch-all →
`ANALISE_HUMANA`; `alto` → `ANALISE_HUMANA`; `moderado`+criteria → `ELEGIVEL`; `baixo`+no-criteria →
`NAO_ELEGIVEL`; `baixo`+criteria → `ANALISE_HUMANA`). The `stratify_risk` worker (#126) fail-closes to
`"alto"` on any absent/invalid/unresolvable band (`programa.py:107,121,124-186`). **What is entirely
unspecified is what produces the band in the first place** — the DMN's own `<description>` says
"criterios de estratificacao/elegibilidade requerem SME medico-auditor + taxonomia de programas"
(`programa_routing.dmn:9-10`).

**Options.** (i) Invent deterministic clinical band cutoffs now. (ii) Ratify the fail-closed default and
specify *what evidence/structure* the auditor must supply, anchoring to a recognized published instrument
per program rather than a bespoke table. (iii) Leave entirely open.

**SUGGESTED ANSWER — option (ii): ratify the fail-closed default; do NOT invent clinical bands; anchor
each program to a recognized published stratification instrument.** Per the orchestrator's own
instruction and the repo's fail-closed doctrine, **the AI must not fabricate clinical stratification
thresholds** — no defensible clinical-administrative basis for specific band cutoffs exists in the
contract or DMN, and the program taxonomy itself is open. So:

1. **Ratify the fail-closed `"alto"` → `ANALISE_HUMANA` default as clinically correct.** It is the right
   posture: an absent/unresolvable risk band routes to a human clinician, never to auto-enrollment or
   auto-discharge. This is now shipped behavior (#126, 9 tests flipped) and it aligns with ADR-0018
   (no adverse clinical decision without a human). The médico-auditor can and should ratify *this
   property* now.
2. **Do not encode bespoke band cutoffs.** Instead, the médico-auditor should specify that
   `care.stratify` **adopt a recognized, published stratification instrument per program**, so the bands
   are defensible and auditable rather than invented. Candidate anchors (for the *auditor* to select and
   validate, not AI prescriptions): **pré-natal** — the Ministério da Saúde "Gestação de Alto Risco"
   risk classification (idade, comorbidades, antecedentes obstétricos); **crônicos** — an established
   chronic-condition risk score appropriate to the condition (e.g. validated cardiovascular/diabetes
   risk stratification); **oncologia** — staging/treatment-phase-based stratification; **APS** — a
   recognized primary-care risk-group model. For each, the auditor supplies: (a) the **inputs** (which
   clinical facts, sourced from the amh Feature Store/CDC in-zone — never Zona Geral), (b) the
   **thresholds** mapping to `{baixo, moderado, alto}`, and (c) an explicit confirmation that **no band
   ever auto-discharges or auto-denies care** (the DMN already structurally guarantees this; the
   stratifier must not undermine it).
3. **Discharge criteria (`UT_DecisaoClinica`).** `motivo_desligamento_clinico`/`referencia_clinica` are
   required on `DESLIGAR_CLINICO` — the médico-auditor must supply the per-program discharge protocol
   (e.g. completion of a therapeutic cycle, clinical stability criteria). These are L0-hard human
   decisions (ADR-0008) — no DMN produces them, correctly.

**Confidence: HIGH on ratifying the fail-closed default; the actual clinical criteria are genuinely and
appropriately the médico-auditor's — the AI deliberately declines to invent them, which is the correct
fail-closed posture.** **Must still check:** program taxonomy scope; the published instrument per
program; that the Feature Store exposes the needed inputs in-zone.

### C7b — `proactive_contact`: clinical-appropriateness half (joint DPO A10)

**Question (`medico-auditor/PACKAGE.md:225-235`).** Which risk bands/program types warrant proactive
outbound contact, and should an urgent/high-risk finding ever be delivered by an unmonitored async
channel, or always escalate to a synchronous/human channel?

**SUGGESTED ANSWER — proactive async contact is appropriate ONLY for low/moderate-risk, non-urgent
engagement; urgent/high-risk findings must NEVER go by unmonitored async WhatsApp.** Concretely:

1. **High-risk/urgent → synchronous/human channel, never async.** Anything the stratifier routes
   **`alto` → `ANALISE_HUMANA`** (C7a) must **not** be handled by `proactive_contact` at all — a
   high-risk or urgent clinical finding delivered by an unmonitored async channel (where read/response
   is uncertain) is clinically unsafe and should escalate to a synchronous human channel
   (SP-OP-ESCALATION-001 territory). This directly aligns the clinical answer with the fail-closed
   stratification posture.
2. **Proactive async contact is appropriate for `baixo`/`moderado`, non-urgent purposes** — program
   invitations, appointment reminders, adherence nudges — where the *content* is start-signal-only and
   PHI-minimized (D9), and no time-critical clinical information depends on the beneficiary reading the
   message promptly.
3. **This constraint must be encoded, not just documented.** Recommend the `proactive_contact` worker
   refuse (fail-closed) to send for any case whose band is `alto`/unresolved, routing it to the human/
   synchronous path instead — mirroring the stratifier's own fail-closed default.

Coupled with the DPO's A10 constraints (template-only, distinct channel consent, immediate revocation),
this gives a consistent joint answer: **proactive WhatsApp is a low/moderate-risk engagement channel,
never an urgent-clinical-delivery channel.**

**Confidence: high (this is the clinically conservative, doctrine-aligned position).** **Must still
check:** the exact boundary between "engagement nudge" and "clinical information"; that the escalation
path exists for the high-risk cases proactive_contact must refuse.

### C7c — `programa_routing` risk-band inputs + candidate groups (médico Q2–Q3)

**SUGGESTED ANSWER.** The `programa_routing` inputs (`risco_estratificado`,
`elegibilidade_criterios_atendidos`) are structurally sound (fully fail-safe, no adverse output) but
their *clinical validity* depends entirely on C7a (what produces the band). Ratify the *structure* now;
the *content* follows C7a. Candidate groups (`coordenacao-clinica`/`equipe-cuidado`) are PROPOSTO
(PO/IdP) — médico-auditor confirms they map to real clinical roles.

## C8 · SP-OP-RECURSO-001 — group reuse/COI, glosa_type taxonomy (médico Q1–Q2)

**Question (`medico-auditor/PACKAGE.md:246-252`).** (Q1) Does reusing the `medico-auditor` group across
AUTH/CONTAS/RECURSO create a workload/conflict-of-interest issue when the same team both denied the
original claim and reviews the appeal? (Q2) Is the `glosa_type` taxonomy routing clinically sound?

**SUGGESTED ANSWER.** (Q1) **Yes, there is a genuine COI risk** — the same médico-auditor reviewing an
appeal of a glosa they (or their immediate team) originated is not impartial. Recommend a **segregation
rule**: the recurso reviewer must be a *different* auditor than the one who issued the original glosa
(a "four-eyes" / different-reviewer constraint on `UT_RevisaoAuditorMedico` when the appeal traces to a
prior AUTH/CONTAS decision by the same person). This is analogous to the payment approver≠requester
control (B13). The *group* can be reused; the *individual* must differ. (Q2) The `glosa_type` taxonomy
(`administrativa/tecnica/clinica/linha_duplicada/formatacao`) routing técnica/clínica to médico-auditor
vs administrativa/formatação to `analista-recurso-glosa` is clinically defensible — técnica/clínica
correctly require clinical review; validate the exact routing against the RN 501/2022 TISS glosa
categories (couples to C2/Q2).

**Confidence: high on the COI segregation recommendation; the taxonomy routing needs the same TISS-table
validation as C2.** **Must still check:** whether your Tasklist/IdP can enforce different-individual
routing; the glosa_type→group mapping against the TISS table.

## C9 · SP-OP-REEMBOLSO-001 — clinical-review triggers, DMN path (médico Q1–Q2)

**Question (`medico-auditor/PACKAGE.md:268-275`).** (Q1) Are the `requer_avaliacao_clinica` triggers
(alta-complexidade/OPME, sensitive CID, coding divergence) clinically complete? (Q2) Is
`reembolso_coverage.dmn` in fact the built artifact for the contract-named `reembolso_admissibility`?

**SUGGESTED ANSWER.** (Q1) The triggers are a **reasonable starting set** but the médico-auditor should
confirm completeness — candidates to consider adding: procedures on the DUT list requiring clinical
criteria (bariátrica, PET-CT, terapias especiais — the same `dut_criteria_*` set), high-value
reimbursements above an actuarial threshold, and reimbursements where the fora-de-rede justification is
clinical (urgência/emergência). This is a clinical-completeness judgment the auditor owns. (Q2) The
path issue is real: `reembolso_admissibility` and `reembolso_auto_approval` are **named in the contract
but only `reembolso_coverage.dmn` exists** (`medico-auditor/PACKAGE.md:262-267`); `reembolso_coverage`
is the *likely* rename of `reembolso_admissibility` (same `cobertura_prevista` shape) but **not
confirmed**, and `reembolso_auto_approval` has **no file at all** (build gap). The médico-auditor is
reviewing coverage/admissibility logic that **may not be the artifact actually running** — recommend
flagging this for engineering confirmation before the clinical review is treated as complete, because
reviewing the wrong DMN is worse than not reviewing.

**Confidence: high on flagging the path issue (do not certify a DMN whose identity is unconfirmed);
the trigger-completeness is the auditor's clinical call.** **Must still check:** the DMN identity
(engineering); the trigger set against real reimbursement case mix.

---

<a name="d--regulatorio"></a>
# D · Regulatório (ANS regulatory affairs)

Regulatório is on 14 of 16 contracts (`regulatorio/PACKAGE.md:41`). Most questions are RN-currency +
ANS-calendar cadence, answered by the B0 map + `rn-currency-review.md`. The focus item is the **TISS
XSD sourcing & version pin** (D-ANS-SUBMIT/Q5 + T2.6 §2.B), in D3.

## D1 · SP-OP-ADEQUACAO-001 / AUTH / CONTAS / CRED / NIP / RECURSO / REEMBOLSO — RN currency + thresholds

**SUGGESTED ANSWER — apply the B0 map** (regulatório owns the ANS-facing confirmation of each). Highest
priorities: **RN 259/2011 → RN 566/2022** (thresholds + dias úteis) across ADEQUACAO/AUTH/REEMBOLSO;
**RN 388 → RN 483/2022** (NIP, most schedule-critical); **RN 305 → RN 501/2022** (TISS, CONTAS/RECURSO);
**RN 567/2022 + RN 585/2023** split by prestador type (CRED); **RN 424/2017 kept only for junta médica**.
The `adequacao_gap` time/distance thresholds per `tipo_carater` must be signed off against **RN 566/2022
vigente** (`regulatorio/PACKAGE.md:68-73`) — these are real ANS thresholds regulatório confirms, not AI.
`ciclo_avaliacao` periodicity: recommend **trimestral** (the bridge derives `YYYY-Qn`), but this is
regulatório+PO's cadence call against RN 259/566.

**Confidence: high on the supersession map (fetched sources); the exact threshold *values* are ANS-text
confirmations regulatório must make.** **Must still check:** RN 566 threshold table; RN 585 in-force
dates (R8).

## D2 · SP-OP-ANS-CRON-001 + SP-OP-ANS-SUBMIT-001 — surviving timers, competência mapping, calendar, NIP handoff (regulatório CRON Q1–Q3 + SUBMIT Q1–Q5)

**Question (`regulatorio/PACKAGE.md:86-100, 115-139`).** Which in-force obligation each surviving timer
serves + real `timeCycle`; the competência-derivation mapping; `due_date`/`fonte_regulatoria` for the
surviving report types; holiday/business-day resolution; the NIP→ANS-SUBMIT handoff; candidate groups;
and the Monitoramento-TISS obligation-vs-successor question.

**SUGGESTED ANSWER (grounded in T2.6 design + DL-0028/0029 + RN 639 primary source).** **SIP is EXTINCT**
(RN 639/2025 primary-confirmed — DL-0029; last owed Q4/2025, in force 02/03/2026) — surgically retire the
`SP-OP-ANS-CRON-001-RN124SIP` timer, the `r_rn124_sip` calendar row, and the `SIP`/`RN_124_SIP` report
type (T2.6 §3, Option B). **Surviving timers and their re-derived basis:**

- **DIOPS** (econ-financeiro trimestral): survives; the governing norm is **unresolved (register R2)** —
  regulatório must confirm the current DIOPS norm; the miscited `RN_209_UTILIZACAO` label re-bases to
  **RN 451/2020** (capital) for the econ-fin channel. Cadence `R/P3M`.
- **Monitoramento TISS** (padrão TISS): survives under **RN 501/2022**; the `RN_424_TISS_MONITORAMENTO`
  label's "424" is a misattribution (424 = junta médica). Cadence per the Monitoramento norm (verify).
- **`RN_388_QUALIDADE`**: **not a periodic filing** — it is fiscalização/NIP → **RN 483/2022**, which is
  *event-driven*, not a timer. Recommend **dropping this timer** and letting the NIP-response filing
  flow through the event-driven `origem_envio==nip_filing` path instead.

**Competência mapping:** the implemented rule (`_ans_cron_competencia`) assumes the period *immediately
prior* to the anchor month — recommend regulatório confirm this against each obligation (most periodic
ANS filings report the *prior closed period*, so "immediately prior" is likely correct, but DIOPS and
Monitoramento-TISS may differ). **Holiday/business-day:** the conservative "antecipar, nunca postergar"
default is **regulatorily safe** — confirm it. **NIP→ANS-SUBMIT handoff** (`origem_envio==nip_filing`):
regulatorily sound and load-bearing (the NIP-response filing under RN 483/2022 is the primary in-force
reason the transmit path is kept, T2.6 §1.5); sharing the channel with DIOPS/Monitoramento-TISS origins
is correct. Candidate groups (`regulatorio-ans`/`juridico-regulatorio`/`coordenacao-regulatorio`) are
PROPOSTO (PO/IdP). **Monitoramento-TISS obligation vs successor (Q5):** answer the two parts separately —
(a) Monitoramento TISS **is** an in-force periodic obligation this channel must serve (RN 501/2022); (b)
whether it is the *named successor* to SIP is **SME-gated and unresolved** — RN 639's primary text names
**no** successor (`rn-639-primary-source.md:23,81-88`; DL-0029(b)). **Do not assert Monitoramento TISS
is the SIP successor** — treat it as a distinct in-force obligation unless regulatório finds a primary
text naming it as successor.

**Confidence: high on SIP extinction + the re-citations (primary + fetched sources); the DIOPS norm (R2)
and the successor status are genuinely unresolved — flag, don't assert.** **Must still check:** DIOPS
governing norm (R2); Monitoramento-TISS cadence + successor status; the NIP-filing `report_type`
classification (`review-queue.md:315` — currently reuses `RN_209_UTILIZACAO`, likely needs its own type).

## D3 · **[FOCUS]** TISS XSD sourcing + version pin (SUBMIT Q5 + T2.6 §2.B, §7)

**Question (`regulatorio/PACKAGE.md:134-139`; `T2.6-ans-submission-rescope.md:125-134, 168-178, 207-209`).**
What is the concrete TISS version pin and XSD source for real schema validation in `validate_data`? Is
the padrão TISS the governing schema, and does DIOPS use TISS XSD at all?

**What's at stake in the code.** `validate_data` performs **no XSD parse today** — it echoes
`submission.schema_valid` and only checks `dataset_ref` non-empty (`ans_submit.py:152-184`); the BPMN
`ST_ValidateSchema` and the admissibility DMN both assume a real validation produced `schema_valid`, but
nothing computes it (the same non-computing echo as `lgpd_anonimizado`). T2.6 §2.B designs the fix:
vendor the TISS XSDs, pin the version in config, validate with `lxml.etree.XMLSchema`, fail-closed on
violation/missing-XSD/stub. The **exact version and source are the SME (regulatório) question**.

**Options.** (i) Hardcode a version in the worker (rejected by T2.6 — version lives in config). (ii)
Pin a specific padrão-TISS Componente de Comunicação version, vendored + checksum-pinned. (iii) Leave
validation stubbed (rejected — DL-0029(a) makes the fix mandatory-regardless).

**SUGGESTED ANSWER — pin the current padrão-TISS "Componente de Comunicação" version under RN 501/2022,
vendored checksum-pinned; validate DIOPS separately (it is NOT TISS).** Concrete recommendation for
regulatório to confirm and the runtime lane to implement:

1. **Source.** Vendor the **full** padrão-TISS schema set (the XSDs `import` each other and reference
   dictionaries) from ANS's official "Padrão TISS — Componente de Comunicação" downloads
   (gov.br/ans → Espaço da operadora de plano de saúde → Padrão TISS / TISS downloads), into a
   checksum-pinned, provenance-documented directory `spec/schemas/tiss/<version>/` under the same
   checksum discipline T2.4 established (T2.6 §2.B). Governing norm: **RN 501/2022**.
2. **Version pin.** Pin the **specific Componente de Comunicação version in force for the competência
   being filed**, in config (not hardcoded). **Candidate starter pin: TISS Componente de Comunicação
   `4.01.00`** — the version series published under RN 501/2022 as of recent ANS releases — **but this
   MUST be confirmed by regulatório against the ANS padrão-TISS page for the exact minor version and
   the coexistence/transition window** in force on the target competência (ANS runs implantação
   schedules where an outgoing and incoming version overlap; filing under the wrong version is a
   rejection/intempestividade risk). Do not treat `4.01.00` as authoritative — it is a defensible
   starting hypothesis the SME must verify (the exact current version may have advanced by 2026-07).
3. **DIOPS is NOT TISS.** DIOPS has its **own economic-financial layout** (not the TISS
   Componente de Comunicação) — T2.6 §2.B flags exactly this. So the XSD-validation path applies to the
   **TISS filings (Monitoramento TISS, NIP-response)**, and DIOPS must be validated against its **own**
   schema/format (regulatório confirms the DIOPS layout norm — coupled to the unresolved R2 DIOPS-norm
   question). **Do not run DIOPS through the TISS XSD.**
4. **Fail-closed rules (from T2.6 §2.B, ratify).** Schema violation → `schema_valid=False` → routes to
   `UT_CorrigirPendenciaEnvio` (never rejects, never auto-passes); **missing/unvendored XSD → False →
   human** (never pass on absence); stub/empty dataset (assemble is still a stub, mcp-regdata AWS-blocked)
   → stays False → human. The pinned version must surface in the audit record.

**Confidence: MEDIUM on the exact version number (`4.01.00` is a defensible starting hypothesis but
genuinely SME-gated — the AI must not assert a specific ANS version as current without regulatório
confirmation); HIGH on the sourcing method, the "DIOPS is not TISS" split, and the fail-closed rules
(these follow T2.6 §2.B directly).** **Must still check:** the exact Componente de Comunicação version
+ coexistence window in force on the filing competência (regulatório, against gov.br/ans); the DIOPS
schema/norm (R2); whether Monitoramento TISS uses the Componente de Comunicação schema or a distinct
monitoring schema set.

## D4 · SP-OP-NIP-001 — RN 483/2022, business-key, groups (regulatório Q1–Q3)

**SUGGESTED ANSWER.** **RN 388/2016 → RN 483/2022** (B0); the 5 d.ú. assistencial / 10 d.ú.
não-assistencial deadlines are the ANS-sanction-critical values — regulatório confirms them against RN
483 text (the values survive; the citation and day-basis change). Business-key: recommend
**`numero_nip_ans`** (the ANS-assigned identifier) as the stable key over `protocolo_ans` (internal,
re-issuable) — joint with jurídico (B8). Candidate groups (`nucleo-ans`/`regulatorio-ans`/
`coordenacao-regulatorio`) are PROPOSTO (PO/IdP).

**Confidence: high on RN 483; the business-key is a joint jurídico+regulatório recommendation.**
**Must still check:** the exact RN 483 upload window; the business-key against ANS practice.

## D5 · SP-OP-PROGRAMA-001 — is there an ANS RN for health-promotion/APS programs? (regulatório Q1–Q2)

**Question (`regulatorio/PACKAGE.md:272-275`).** Is there an ANS RN specifically governing
health-promotion/APS programs this contract should cite alongside LGPD? If so, does it impose a deadline
or reporting obligation not currently modeled?

**SUGGESTED ANSWER — this is a genuine regulatório research question the AI should NOT answer
definitively.** ANS does have a **Programa de Promoção da Saúde e Prevenção de Riscos e Doenças**
regulatory framework (historically via RN and IN on programas de promoção à saúde and the incentive/
certification programs, e.g. the APS incentive frameworks), and there are ANS monitoring/certification
obligations tied to registered programs — **but the exact in-force RN/IN and whether it imposes a
reporting obligation on *this* contract's program taxonomy is precisely what regulatório must verify.**
The AI cannot responsibly assert a specific RN number here without primary-source confirmation (the
`rn-currency-review.md` pass did not resolve a programa-de-saúde norm). Recommend regulatório check the
current ANS programas-de-promoção-à-saúde framework and, if a registration/monitoring obligation exists,
add it to the contract's gatilho + model any reporting deadline (which could route through the
ANS-SUBMIT channel like the other periodic obligations).

**Confidence: LOW on a specific norm (genuinely unresolved — flag, don't invent); high that LGPD alone
is insufficient citation if a programa-de-saúde RN exists.** **Must still check:** the current ANS
programa-de-promoção-à-saúde / APS regulatory framework and any monitoring/reporting obligation.

---

<a name="e--sla-basis"></a>
# E · Cross-cutting — the dias-úteis vs dias-corridos SLA basis (jurídico + regulatório, all SLA-bearing contracts)

**Question (`rn-currency-review.md` §"SLA day-basis question", :243-264).** For each SLA class, does the
in-force norm count in **dias úteis** or **dias corridos**, and from **which anchor**? The repo stores
every prazo as an ISO calendar-day (dias corridos) duration while the underlying regulatory prazo is
often in dias úteis — a latent mixing-of-bases bug.

**SUGGESTED ANSWER (grounded in `rn-currency-review.md` verified findings).**

| SLA class | In-force norm | Day-basis | Status |
|---|---|---|---|
| Autorização / realização | RN 566/2022 (realização) + RN 623/2024 (resposta) | **dias úteis** (confirmed `[fetched]`) | encode as útil; current ISO fires *early* (conservative but not the regulator's instant) |
| NIP | RN 483/2022 | **dias úteis** (5/10) | encode as útil |
| Inadimplência | RN 593/2023 ("50º dia", "10 dias") | **likely dias corridos — NOT fetched (R4)** | genuinely open — jurídico must confirm |
| Reembolso | norm unresolved (R3) | unknown | genuinely open |

**The actual latent bug:** one calendar convention (dias corridos) cannot be right for all classes —
attendance/NIP are dias úteis, inadimplência is likely dias corridos. Encoding a dias-úteis prazo as
dias-corridos ISO makes the timer fire *earlier* than the legal deadline (conservative for compliance,
but produces false SLA-breach escalations and — critically — is **not the instant ANS measures**, so any
ANS-facing metric computed from the ISO value is wrong-early). **Recommendation:** before any production
timer, the engine must **resolve a business-day calendar in the worker** for the dias-úteis classes
(per `CONTRIBUTING`: prazo lives in DMN/timer, útil-calendar resolution in worker), replacing the
conservative ISO approximation. The urgência "2h" (`PT2H` for Lei 9.656 art. 35-C "imediato") is an
acceptable *internal* SLA but jurídico should confirm "imediato" isn't legally stricter.

**Confidence: high on classes 1–2 (fetched dias-úteis); classes 3–4 are the genuinely open R3/R4
register items.** **Must still check:** R4 (inadimplência basis + anchor), R3 (reembolso norm), and the
business-day-calendar-in-worker decision before any production timer.

---

## Items this AI could NOT responsibly draft a full answer for (and why)

Per the "blocked ≠ done / no fabrication" discipline, these questions require primary legal/regulatory
research or a genuinely professional judgment the AI declined to fabricate:

1. **FRAUDE-001 referral obligations (jurídico B6/Q2)** — ANS/civil/penal referral deadlines, competent
   authority, form, and the alçada/tier ladder are **not pinned anywhere** in the repo and require
   primary legal research (which authority, which statute of limitations, which form). Inventing referral
   deadlines would be exactly the fabrication the repo forbids. **This is the single item most needing a
   real lawyer.**
2. **The exact TISS Componente de Comunicação version in force (regulatório D3)** — `4.01.00` is a
   defensible starting hypothesis, but asserting a specific ANS version as *current on the 2026-07
   competência* without confirming the gov.br/ans page + coexistence window would be fabrication. The
   sourcing method and DIOPS split are answered; the version number is SME-gated.
3. **The DIOPS governing norm (regulatório, register R2) and the reembolso ~30-day norm (jurídico,
   register R3)** — the analyst pass could not resolve these to a specific vigente RN; they are marked
   `cannot-verify — requires SME` and the AI honors that (does not assert a norm).
4. **The programa-de-saúde/APS RN (regulatório D5)** — a framework likely exists but the specific
   in-force norm + reporting obligation is unresolved; the AI flags the research rather than inventing a
   number.
5. **The specific clinical stratification thresholds for `stratify_risk` and the per-program discharge
   criteria (médico-auditor C7a)** — deliberately NOT invented; the correct fail-closed posture is to
   ratify the "alto"→human default and anchor to a recognized published instrument the auditor selects.
6. **Every synthetic clinical threshold** (DUT criteria C1, red-flag tables C4, fraud scoring C5) — these
   need real specialist validation against current protocols; the AI can confirm the *structure* is
   fail-safe but cannot certify the clinical *values*.

For everything above, the suggested answers stop at the boundary of what is verifiable and name what the
professional must supply. **Nothing in this document is a decision; every verdict binds only when the
designated human records it in a signoff file.**
