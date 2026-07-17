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

## Turnaround

10 business days per contract (standard, proposed, non-binding) — see `../README.md`
§"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
