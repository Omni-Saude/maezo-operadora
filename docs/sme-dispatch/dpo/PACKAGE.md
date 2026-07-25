# SME package — DPO (data protection officer)

**Task:** T0.6 dispatch packaging. **Status:** prepared — awaiting SME roster (`blocked(external)`,
see `../README.md` and `../tracker.md`). No reviewer has been named or contacted.

## How to use this package

1. Read the contract at the canonical path listed for each item (`docs/processes/contracts/` —
   single source of truth; BPMN/DMN under `spec/processes/` are the modeled artifacts).
2. Answer the review questions below in writing against the contract text.
3. Return redlines as a PR against the contract file (preferred) or an annotated copy — see
   `../README.md` §"The redline protocol".
4. Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
   `../README.md` §"Signoff artifact spec". No agent creates this file for you.
5. **Turnaround ask:** 10 business days per contract from receipt (non-binding, see
   `../README.md`).

## Why DPO is on 5 of 16 contracts

Assigned wherever a contract's core mechanism is an LGPD data-subject right, a consent gate over
PHI, an anonymization/aggregation claim feeding an external regulator, or an evidentiary
data-handling design (chain of custody) — the "LGPD/PHI → DPO" rule from the task charter. Not
assigned to contracts that only carry the platform's baseline pseudonymization pattern
(`beneficiario_pseudo_id`, ADR-0006) without a distinct LGPD mechanism of their own — that
baseline is assumed correct platform-wide and isn't re-litigated contract by contract here.

## Assigned contracts

### SP-OP-LGPD-DSR-001 — Direitos do Titular (DRAFT, v0.1.0)

- **Why DPO:** this is the contract implementing LGPD data-subject rights directly
  (confirmação de acesso, correção, eliminação, portabilidade, revogação de consentimento) —
  primary owner role.
- **Contract:** `docs/processes/contracts/SP-OP-LGPD-DSR-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`
- **DMN:** `spec/processes/dmn/lgpd_dsr_routing.dmn`.
- **Review questions:**
  1. Confirm the 15-day legal SLA (LGPD art. 19, II) is correctly anchored on instance start
     (`ESP_SlaGlobal`, event subprocess) rather than on any individual task.
  2. Confirm the fail-closed guards are sufficient: `ERR_DSR_IDENTITY_UNVERIFIED` (identity
     unverifiable — explicitly NOT an automatic fraud accusation),
     `ERR_DSR_DECISION_INVALID` (no data release on an absent/unrecognized decision), and
     `ERR_DSR_FUNDAMENTACAO_AUSENTE` (no denial without legal grounding) — do these cover every
     path PHI could otherwise leak on an omission?
  3. Confirm the legal-basis-for-retention matrix by data type is DPO-owned and complete before
     this reaches FINAL — the contract lists this as an open Pendência.
  4. Confirm whether consent revocation (art. 18 §2, "immediate effect") needs a dedicated fast
     path distinct from the general DSR flow, especially given SP-OP-PROGRAMA-001 depends on this
     process's output as its interrupt trigger (see that contract below).

**T2.9 addendum (2026-07-24, `t2.9-sme-packages`; status refreshed 2026-07-25 after merging
`origin/main` 6288341 — #125/#126/#131 landed; the QUESTIONS are unchanged) — #55 R-C/R-D
orphan-worker design + producer-policy status.** Packaged after auditing the DSR pipeline's
still-worker-less BPMN topics — as of the refresh exactly **two** remain: `compile_data_package`
(R-C) and `execute_request` (R-D) (`src/maezo/tools/workers/lgpd.py:631-641`; R-F/R-G were built
by #125, see the status note below). Three design questions, plus a status note on what is and is
not blocked:

  1. **R-C `compile_data_package` — legal-bases/retention matrix.** `ST_CompilarPacote` is
     documented to compile "conforme `roteamento_dsr.fluxo` ..., incluindo mapa de bases legais e
     obrigacoes de retencao aplicaveis" (`spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:183`),
     but no such map exists anywhere in the repo, and the contract's own "Pendencias para promocao
     a FINAL" lists it as unresolved: "matriz de bases legais de retencao por tipo de dado"
     (`docs/processes/contracts/SP-OP-LGPD-DSR-001.md:103-104`). Please specify, per
     `tipo_requisicao`/data type: which fields belong in an export/rectification/erasure package,
     and which retention exceptions apply — in particular where Lei 13.787/2018 (CFM prontuario
     retention) legitimately overrides an elimination request vs. where LGPD elimination should
     proceed.
  2. **R-D `execute_request` — collapse 3 orphan workers + ratify the erasure-failure/denial
     routing shape.** Today `register_lgpd_workers` registers `ExecuteExportWorker`/
     `ExecuteRectificationWorker`/`ExecuteErasureWorker` under three topics
     (`operadora.lgpd.execute_export`/`execute_rectification`/`execute_erasure`) that do **not**
     match the BPMN's single `ST_ExecutarRequisicao` topic (`operadora.lgpd.execute_request`,
     `bpmn:270-275`) — the registry's own bootstrap comment discloses the remaining gap
     (`lgpd.py:631-641`, post-#125: "that BPMN's remaining 2 topics (compile_data_package/
     execute_request) still have no worker — #55 R-C/R-D, DPO/SME-sign-off-gated"; the three
     `Execute*Worker` orphan registrations persist at `lgpd.py:658-665`). Please ratify collapsing
     the three into a single
     `execute_request`-topic worker matching the modeled shape. Separately: `ST_ExecutarRequisicao`
     has **no** boundary event (confirmed: zero `errorRef` to `Error_LgpdErasureFalhou`/
     `Error_LgpdErasureNaoHumana` anywhere in the file) and its one outgoing flow
     (`Flow_Executar_Enviar`) feeds `ST_EnviarResposta` unconditionally, alongside two other
     incoming flows (`bpmn:270-275` execute task, `bpmn:277-284` send-response task) — so whatever
     a future R-D worker returns for a denied/failed execution flows into `send_response` (#55
     R-F — now BUILT, #125, so the unfiltered flow is live-reachable) with no BPMN-level filter;
     only a worker-internal guard (today,
     `ExecuteErasureWorker`'s dual guard on `human_approved`/`decisao_dsr`) distinguishes success
     from block, and it currently returns a status dict rather than raising. Please ratify: should
     R-D raise the two declared-but-unbound errors below instead (accepting the BPMN's own
     documented incident-fail-closed intent — no BPMN edit needed), or should a modeled boundary be
     added routing to a clean neutral terminal (a BPMN change, out of this dispatch's scope)?
  3. **`ERR_DSR_ERASURE_FAILED` / `ERR_DSR_ERASURE_NOT_HUMAN` — declared, unbound.** Both are
     declared at the top of the BPMN (`bpmn:19,22`) with inline rationale (GAP-LGPD-2 comment,
     `bpmn:15-18`: "sem catch declarado, `ST_ExecutarRequisicao` vira incidente ... a resposta
     jamais afirma uma erasure que nao ocorreu") but **zero** boundary events reference either
     `errorRef` anywhere in the file (grep-confirmed) — the incident-fail-closed behavior described
     in that comment is BPMN's *default* absence-of-catch semantics, not a modeled route. If a
     future boundary is added for `ERR_DSR_ERASURE_NOT_HUMAN` specifically, note it is a
     `*_NOT_HUMAN` guard code hard-gated on T-E (audited-refusal) per ADR-0030 §4 — and **T-E has
     now LANDED** (#131: harness-level audited-refusal chokepoint, `is_guard_refusal_code`,
     `harness.py:333-346`, emitting a PHI-safe REFUSED AuditRecord before every guard/denial
     refusal; #131 deliberately does NOT enable any `*_NOT_HUMAN` code in the production
     allowlist). So the remaining blockers for these two codes are (a) the boundary-shape decision
     asked in this very question, and (b) the per-code enablement proof through the
     boundary-proof gate (consumption coverage) — same posture as `ERR_DECRED_NOT_HUMAN`/
     `ERR_CRED_DENIAL_NOT_HUMAN`/`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`.

  **What is NOT blocked (status check, not a question — refreshed 2026-07-25 post-merge).** The
  identity gate is LIVE and adversarially verified in production shape: `ValidateIdentityWorker`
  (#55 R-A) fail-closes on an explicit `identidade_verificada is True` signal, and #55 R-B
  (`request_additional_proof`) is a built, live-proven raw async handler (#113, ADR-0031/DL-0031;
  independent R1 adversarial reproduction on a live CIB Seven 2.1.0 engine,
  `docs/evidence-ledger.md` T2.8 row, 2026-07-19). **#55 R-F (`send_response`) and R-G
  (`notify_sla_risk`) are now BUILT and MERGED (#125,** `make_send_response_handler`
  `lgpd.py:479` / `make_notify_sla_risk_handler` `lgpd.py:558`, registered `lgpd.py:670-674`):
  R-G is live-proven — its two SLA-phase tests (P7D ack via `ST_NotificarRiscoSla`, P15D
  resolution via `ST_NotificarJuridicoBreach`, discriminated by `sla_breach_phase`) flipped; R-F
  is built AND live-verified (`lgpd_send_response_sent decisao=APROVAR_ENVIO` observed on the
  live engine) but its integration-suite probe wiring is **deliberately kept on the gap stub**
  pending DPO sign-off on the full DSR merit flow — a governance gate, not a src gap
  (`tests/integration/processes/test_sp_op_lgpd_dsr_001.py:245-250`): un-shadowing R-F genuinely
  flips a happy-path test, and a known assert-key mismatch is tracked for that moment (the worker
  emits `tem_fundamentacao`, `lgpd.py:516`; the test asserts `has_fundamentacao`, test `:553`).
  (An earlier revision of this addendum, written against pre-#125 main, correctly recorded
  R-F/R-G as unbuilt at that time.) Only R-C/R-D remain unbuilt — exactly the two topics this
  addendum's questions cover. Separately, the **producer policy** — which channels/proof types
  are sanctioned to seed `identidade_verificada=True` — remains pending DPO ratification per
  ADR-0031 Decisao point 5; the consumer-side fail-closed semantics does not wait on that
  ratification (ADR-0031's own framing).

### SP-OP-ANS-SUBMIT-001 — Envios Periódicos ANS (DRAFT, v0.1.0)

- **Why DPO:** the regulatory dataset sent to ANS is supposed to be aggregated/anonymized before
  transmission, and the contract itself documents that this anonymization is currently **not**
  computed — it's a non-computing echo, fail-closed to `false` until a human confirms it (GAP-ANS-5).
- **Contract:** `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn`
- **DMN:** `spec/processes/dmn/ans_calendar.dmn`, `ans_sla.dmn`,
  `ans_submission_admissibility.dmn`, `ans_retry_policy.dmn`.
- **Review questions:**
  1. **Read GAP-ANS-5 carefully:** `lgpd_anonimizado` is seeded `false` fail-closed by the
     `notifications_bridge`, echoed unchanged by `regulatorio.anssubmit.assemble` (which is
     explicitly **not** a real anonymization computation — the real source, `mcp-regdata`, is
     AWS-blocked per issue #16), and only flips to `true` when a human explicitly confirms via
     `UT_CorrigirPendenciaEnvio` that the dataset referenced by `dataset_ref` is in fact
     aggregated/anonymized. Is this human-attestation gate an acceptable substitute for an
     automated anonymization check before ANS transmission, or does DPO require the automated
     check to exist before this contract reaches FINAL?
  2. Confirm the dataset is genuinely aggregate/anonymized (Zona Geral, ADR-0006) with no PHI in
     any process variable other than the opaque `dataset_ref` pointer.

### SP-OP-CANCEL-001 — Cancelamento / Rescisão Contratual (DRAFT, v0.1.0)

- **Why DPO:** contract rescission raises the question of what happens to the beneficiary's data
  afterward — elimination vs the legal retention obligation for prontuário — an LGPD/DPO call
  that interacts directly with SP-OP-LGPD-DSR-001.
- **Contract:** `docs/processes/contracts/SP-OP-CANCEL-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` (contract cites
  `..._Cancelamento_Contratual.bpmn` — basename drift, see `../juridico/PACKAGE.md` for the
  detail).
- **DMN:** `spec/processes/dmn/cancel_admissibility.dmn`, `cancel_routing.dmn`, `cancel_sla.dmn`.
- **Review questions:**
  1. Confirm the data retention/elimination policy on contract rescission — the contract
     explicitly flags this as unresolved, "interação com SP-OP-LGPD-DSR-001 (eliminação de dados
     pós-rescisão vs retenção legal de prontuário)."
  2. Confirm no PHI beyond the pseudonymized `matricula_beneficiario` travels in this process's
     variables (per ADR-0006 Zona Geral).

### SP-OP-FRAUDE-001 — Investigação de Fraude (DRAFT, v0.1.0)

- **Why DPO:** the evidence custody bundle (`bundle_root` Merkle) is explicitly designed to carry
  no raw PHI ("no-PHI-in-custody" property, guarded by `ERR_PHI_IN_CUSTODY`), and evidentiary
  retention (regulatory 5+ years) may conflict with LGPD retention limits — both DPO calls.
- **Contract:** `docs/processes/contracts/SP-OP-FRAUDE-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`
- **DMN:** `spec/processes/dmn/fraude_indicadores.dmn`, `fraude_routing.dmn`, `fraude_sla.dmn`.
- **Review questions:**
  1. Confirm the "no-PHI-in-custody" design (bundle carries only pointers + hashes, never raw PHI)
     is sufficient, and that `ERR_PHI_IN_CUSTODY` is the right last-line guard.
  2. Confirm the evidentiary retention policy (regulatory 5+ years per the contract's own note)
     against LGPD retention-minimization principles — where is the boundary, and who resolves a
     conflict if one arises?
  3. The feature-store snapshot referenced by `feature_snapshot_ref` rotates in ~90 days but the
     dossier needs multi-year reproducibility — confirm the plan to freeze/materialize that
     snapshot (ADR-0020, currently PROPOSTO) is DPO-acceptable from a data-minimization
     standpoint (freezing data for years is itself a retention decision).
  4. Joint with jurídico: is the `bundle_root`-Merkle-over-hash-chain custody design evidentiarily
     acceptable (Código de Processo Civil/Penal chain-of-custody)?

### SP-OP-PROGRAMA-001 — Programas de Cuidado (DRAFT, v0.1.0)

- **Why DPO:** this is a consent-gated process end to end — LGPD art. 7/9/11 (sensitive health
  data consent, transparency) and art. 8 §5 / art. 18 §2 (revocation with immediate effect) are
  the contract's central design constraint, not an afterthought.
- **Contract:** `docs/processes/contracts/SP-OP-PROGRAMA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
- **DMN:** `spec/processes/dmn/programa_routing.dmn`, `programa_sla.dmn`.
- **Review questions:**
  1. Confirm the consent chokepoint (`operadora.programa.check_consent`, fail-closed,
     `ERR_PROGRAMA_NO_CONSENT`) correctly gates **every** PHI-processing path before any
     stratification/care-plan work happens.
  2. Confirm the interrupting boundary event design for revocation (`msg.programa.consent_revoked`
     → `stop_processing` → `End_ProcessamentoInterrompidoRevogacao`) satisfies LGPD art. 18 §2's
     "immediate effect" requirement — is stopping processing enough, or does revocation also
     require active deletion of anything already collected?
  3. Confirm the correlation bridge from SP-OP-LGPD-DSR-001
     (`consent_revocation_bridge`, fan-out by `{tenant_id, beneficiario_pseudo_id}` since a
     titular can have multiple active PROGRAMA-001 instances) is the right design — does
     `all_matching=True` correctly reach every active enrollment for that titular?
  4. Confirm k-anonymity / small-cell-suppression parameters for any population-level aggregate
     this program exposes to Zona Geral (noted as pending, WP3.5).
  5. Confirm retention/cessation of PHI after revocation, given the interaction with Lei
     13.787/2018/CFM prontuário retention (joint with jurídico).

**T2.9 addendum (2026-07-24, `t2.9-sme-packages`) — `proactive_contact` channel/consent
constraints (joint with médico-auditor).**

  1. `operadora.programa.proactive_contact` re-fetches PHI in-zone and contacts the beneficiary
     only when `consent_checked==true`, following "o precedente Helena/WhatsApp, D9"
     (`docs/processes/contracts/SP-OP-PROGRAMA-001.md:125`) — but the worker itself is unbuilt
     (`programa.py:318-319`, the "Spec topics with NO implementing function today" gap list —
     which, post-#126, no longer contains `stratify_risk`; that one is now built). Confirm the
     WhatsApp/Helena channel is DPO-acceptable for PHI-adjacent
     proactive outreach under this program's consent scope, and whether any additional consent
     granularity (beyond the general `programa_cuidado` scope already gated by `check_consent`) is
     needed for proactive (vs. reactive/beneficiary-initiated) contact specifically. Joint with
     médico-auditor on the clinical-appropriateness half — see `../medico-auditor/PACKAGE.md`
     SP-OP-PROGRAMA-001 addendum.

## Turnaround

10 business days per contract (standard, proposed, non-binding) — see `../README.md`
§"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
