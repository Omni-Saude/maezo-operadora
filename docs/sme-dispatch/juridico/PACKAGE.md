# SME package — jurídico (legal)

**Task:** T0.6 dispatch packaging. **Status:** prepared — awaiting SME roster (`blocked(external)`,
see `../README.md` and `../tracker.md`). No reviewer has been named or contacted.

## Re-scope note — RN 639/2025 (SIP filing extinct) — read before SP-OP-ANS-SUBMIT-001

**2026-07-18 (t2.6-juridico-package-reframe).** The one ANS filing item in this package
(SP-OP-ANS-SUBMIT-001) was re-framed **before roster** so it never asks you to ratify — or attach a
legal opinion to — an obligation that no longer exists. Basis:

- **RN 639/2025** revokes RN 551/2022 and desobriga o envio do **SIP** após o 4º trimestre/2025
  (vigência 02/03/2026) — the SIP periodic-filing obligation is **EXTINCT** as of the current date
  (verified against the primary ANS text; `docs/compliance/rn-639-primary-source.md`).
- **DL-0028 / DL-0029** (`../../decisions-log.md`) ratify the extinction and the T2.6 re-scope.
  The ratified direction is **Option B — KEEP and harden the transmit path, not delete it**
  (`../../design/T2.6-ans-submission-rescope.md`, esp. §1.5/§2.A): `UT_RevisarEnvio` / the
  legally binding, non-repudiable ANS filing pipeline **survive** because they carry obligations
  that are **still in force** — the **NIP-response filing (RN 483/2022)**, **DIOPS**
  (econ-financeiro trimestral), and **Monitoramento TISS** (padrão TISS, RN 501/2022). Only the
  SIP-specific surface (the `SIP` / `RN_124_SIP` report type and its `r_rn124_sip` calendar row) is
  being surgically retired.

**What this means for your review:** do **not** attach a legal opinion to SIP periodicity, deadlines,
or transmission — that obligation is gone, so there is no residual sanction exposure to assess on it.
The SP-OP-ANS-SUBMIT-001 question below is scoped to the **legal-risk / sanction-exposure /
non-repudiation** dimension of the surviving in-force obligations and of the kept-and-hardened filing
path — the *jurídico* lane. The regulatory-calendar cadence itself (which `timeCycle`/`due_date` per
report type) is regulatório's lane, not asked here (see `../regulatorio/PACKAGE.md`). The non-ANS
items in this package are unchanged.

## How to use this package

1. Read the contract at the canonical path listed for each item (`docs/processes/contracts/` —
   single source of truth; BPMN/DMN paths under `spec/processes/` are the modeled artifacts — see
   `../README.md` §"On artifact paths" for why the contracts' own path prose is stale).
2. Answer the review questions below in writing against the contract text.
3. Return redlines as a PR against the contract file (preferred) or an annotated copy — see
   `../README.md` §"The redline protocol".
4. Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
   `../README.md` §"Signoff artifact spec". No agent creates this file for you.
5. **Turnaround ask:** 10 business days per contract from receipt (non-binding, see `../README.md`).

## Why jurídico is on 14 of 16 contracts

Assigned wherever a contract cites an RN/Lei that needs currency confirmation, embeds a
"NAO_RECORRER"/"NEGAR"/"RESCINDIR"/"MANTER" adverse decision with a fundamentação/referência
regulatória requirement, or carries a referral/notification obligation with legal consequence —
the "RN/SLA/legal → jurídico + regulatório" rule from the task charter. Not assigned to
SP-OP-ANS-CRON-001 (a pure scheduler with no adverse decision or legal text of its own — the
legal review lives in SP-OP-ANS-SUBMIT-001, which it dispatches to) or, at full weight, to
SP-OP-PAGTO-001 (an internal financial-controls policy with no ANS/Lei citation — included there
only lightly, for segregation-of-duties governance).

## Cross-cutting flag for every contract below (do not skip)

Several contracts cite RN 388, RN 259, RN 412, RN 305, RN 501, RN 593, RN 567, RN 424 as if
currently in force. The orchestrator's sweep flagged these as **possibly superseded** by later
ANS consolidations (RN 483, RN 566, RN 593 itself supersedes some of the above). Please confirm,
contract by contract, whether the citation is the one you'd expect to see in force today — this
package does not resolve that; it is T2.5's systematic sweep, and this ask complements it locally.

## Assigned contracts

### SP-OP-ADEQUACAO-001 — Adequação Geográfica de Rede (DRAFT, v0.1.0)

- **Why jurídico:** the financial fallback commitment (`COMPROMISSO_FALLBACK` — livre escolha /
  reembolso garantido / contratação ad-hoc) is a legal obligation triggered by RN 259 network
  adequacy failure; requires `referencia_regulatoria` fundamentação.
- **Contract:** `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`
- **DMN:** `spec/processes/dmn/adequacao_gap.dmn`, `adequacao_remediation_routing.dmn`,
  `adequacao_sla.dmn` — all three exist and match the contract.
- **Review questions:**
  1. RN currency: is RN 259/2011 (garantia de atendimento) still current, or superseded by a
     later consolidation? Same question for RN 566 (dimensionamento de rede).
  2. Confirm the legal basis and limits of the "compromisso financeiro de fallback" (livre
     escolha / reembolso garantido / contratação ad-hoc) — what triggers each variant, and is
     there a ceiling?
  3. Confirm the periodicity of `ciclo_avaliacao` (trimestre vs mês) against RN 259/566 — the
     contract states this is unresolved ("Restam para revisão humana").

### SP-OP-ANS-SUBMIT-001 — Envios Periódicos ANS (DRAFT, v0.1.0)

- **Why jurídico:** `UT_RevisarEnvio` is the sole source of the legally binding, non-repudiable
  filing to ANS, and NIP-originated filings route specifically to `juridico-regulatorio`. Per
  DL-0028/DL-0029 this pipeline is **kept and hardened (Option B), not decommissioned** — it is the
  transmit path for the ANS obligations that are **still in force**: the **NIP-response filing
  (RN 483/2022)**, **DIOPS** (econ-financeiro trimestral), and **Monitoramento TISS** (padrão TISS,
  RN 501/2022). The `SIP` / `RN_124_SIP` report type it once carried is **extinct** (RN 639/2025 —
  see the re-scope note above); jurídico's review is scoped to the legal-risk of the **surviving**
  filings, **not** to SIP.
- **Contract:** `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn`
- **DMN:** `spec/processes/dmn/ans_calendar.dmn`, `ans_sla.dmn`,
  `ans_submission_admissibility.dmn`, `ans_retry_policy.dmn` — all four exist and match.
- **Review questions:**
  1. **Correct in-force legal basis on each surviving filing (sanction exposure).** Every filing
     that leaves `UT_RevisarEnvio` is legally binding and non-repudiable, so it must be transmitted
     under the **correct in-force legal basis**. Two `report_type` literals the pipeline still
     carries are **miscited** and would attach a dead or wrong norm to a live filing:
     `RN_209_UTILIZACAO` (RN 209/2009 is financial/*Recursos Próprios*, revoked by **RN 451/2020**)
     and `RN_388_QUALIDADE` (RN 388/2015 is *fiscalização/NIP*, superseded by **RN 483/2022**). For
     each **surviving** filing — DIOPS, Monitoramento-TISS, and the NIP-response filing — confirm
     the legal basis the operator should assert, and the **sanction exposure** if a filing is
     transmitted under the wrong or extinct citation. Do **not** review SIP / RN 124/2006 — that
     obligation is extinct and its row is being removed; the cadence/calendar itself is
     regulatório's lane.
  2. Confirm the candidate group `juridico-regulatorio` (vs `regulatorio-ans`) is the right
     taxonomy for the mandatory legal review of NIP-originated filings (`origem_envio==nip_filing`)
     — this NIP-response filing (RN 483/2022) is now a **load-bearing in-force obligation** and the
     primary reason the transmit path is kept, so the "revisão sempre jurídica" gate on it is itself
     load-bearing.
  3. Is the retry/backoff policy on ANS NACK (attempts + `PT5M`/`PT30M`/`PT2H` backoff) legally
     sufficient for the surviving in-force filings, or does a transmission failure carry its own
     notice obligation to ANS (e.g. an intempestividade/sanction risk if the filing deadline lapses
     while retries are still running)?

### SP-OP-AUTH-001 — Autorização Prévia (DRAFT, v0.1.0)

- **Why jurídico:** negativa formal by escrito (RN 395 art. 10, 24h) is a legally mandated written
  notice; Lei 9.656 art. 35-C anchors the urgency SLA.
- **Contract:** `docs/processes/contracts/SP-OP-AUTH-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
- **DMN:** `spec/processes/dmn/auth_admissibility.dmn`, `auth_auto_approval.dmn`, `auth_sla.dmn`.
- **Review questions:**
  1. Confirm RN 259/2011, RN 395/2016, and RN 424/2017 currency.
  2. Confirm the 24h written-denial-notice requirement (RN 395 art. 10) is correctly embedded in
     `send_denial_notice`, and that dias-úteis vs dias-corridos conversion doesn't shorten it.
  3. Junta médica flow (RN 424) tie-break/deadline rules are unspecified — what does the RN
     actually require?

### SP-OP-CANCEL-001 — Cancelamento / Rescisão Contratual (DRAFT, v0.1.0)

- **Why jurídico:** `contract_termination` is L0-hard; `UT_AnaliseRescisao` is the sole source of
  `RESCINDIR`/`MANTER`/`SUSPENDER`, each requiring `referencia_regulatoria` fundamentação.
- **Contract:** `docs/processes/contracts/SP-OP-CANCEL-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` — **note:** the
  contract header cites `..._Cancelamento_Contratual.bpmn` (ends "Contratual"); the actual file in
  `spec/processes/bpmn/` is `..._Cancelamento_Contrato.bpmn` (ends "Contrato", one word short).
  Flagged for confirmation that this is the same artifact, not assumed.
- **DMN:** `spec/processes/dmn/cancel_admissibility.dmn`, `cancel_routing.dmn`, `cancel_sla.dmn` —
  all three exist and match the contract.
- **Review questions:**
  1. Does RN 593/2023 consolidate/replace RN 412/2016 for beneficiary-requested cancellation? The
     contract cites both and flags this as unresolved.
  2. Confirm the notification-previa and purga windows (RN 593) and whether they run in business
     or calendar days — the contract explicitly flags "dias úteis → ISO" as unresolved.
  3. Confirm the hypotheses matrix for unilateral rescission by `tipo_plano` (individual/familiar
     vs coletivo — Lei 9.656 art. 13 §único; coletivos follow the estipulante's own rules).
  4. Data retention/elimination on rescission — interaction with SP-OP-LGPD-DSR-001 (owned by DPO
     package, but the legal basis for retention vs elimination is a joint jurídico+DPO call).

### SP-OP-CONTAS-001 — Processamento de Contas / Glosa (DRAFT, v0.1.0)

- **Why jurídico:** glosa acceptance is `authorization_denial`-adjacent (L0 hard); RN 305/2012
  (TISS) and RN 424/2017 (recurso/análise de conta) anchor the process.
- **Contract:** `docs/processes/contracts/SP-OP-CONTAS-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/glosa_reason_normalization.dmn`, `glosa_classification.dmn`,
  `glosa_triage.dmn`, `contas_sla.dmn`.
- **Review questions:**
  1. Confirm RN 305/2012 (TISS/Componente de Comunicação) currency against the vigente ANS text.
  2. Confirm the P30D triagem/análise SLA (anchored on `data_recebimento_lote`) against RN 424 and
     the dias-úteis→ISO conversion.
  3. Confirm the fraud-referral policy: no branch auto-flags `fraud_accusation` (L0 hard) — the
     handoff to SP-OP-FRAUDE-001 is always human (`encaminhar_fraude`).

### SP-OP-CRED-001 — (Des)credenciamento de Prestador (DRAFT, v0.1.0)

- **Why jurídico:** `provider_decredentialing` is L1 with two adverse directions
  (descredenciar/negar credenciamento), both requiring `referencia_regulatoria`; RN 567 mandates
  prior notice to beneficiary and ANS plus substitute-provider obligations.
- **Contract:** `docs/processes/contracts/SP-OP-CRED-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`
- **DMN:** `spec/processes/dmn/cred_admissibility.dmn`, `cred_route.dmn`, `cred_prior_notice.dmn`,
  `cred_sla.dmn`.
- **Review questions:**
  1. Confirm RN 567 (descredenciamento — antecedência de notificação, substituição equivalente)
     and RN 566 (garantias/dimensionamento de rede) currency against vigente ANS text.
  2. Confirm the prior-notice cure-window (`cred_prior_notice`, e.g. P30D) and when the
     substitute-provider obligation applies (hospital vs isolated provider, RN 567).
  3. This is the promotion target for `provider_decredentialing` to `_hard_frozen.yaml` (L1) —
     what architecture+compliance sign-off is required before that promotion?
  4. Confirm the fraud-referral policy mirrors CONTAS-001's (no auto-flag of
     `indicio_irregularidade_sinalizado`).

### SP-OP-FRAUDE-001 — Investigação de Fraude (DRAFT, v0.1.0)

- **Why jurídico:** referral obligations to ANS/civil/criminal spheres, chain-of-custody
  evidentiary standard (Código de Processo Civil/Penal), and the L0-hard fraud accusation gate
  are all legal-content questions the contract explicitly defers to jurídico.
- **Contract:** `docs/processes/contracts/SP-OP-FRAUDE-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`
- **DMN:** `spec/processes/dmn/fraude_indicadores.dmn`, `fraude_routing.dmn`, `fraude_sla.dmn` (the
  7 scoring DMNs are **ported, wiring pending** — T2.7 artifact phase, no longer "não portado";
  now real files under `spec/processes/dmn/{upcoding_complexity_ceiling,
  unbundling_partial_bundles,phantom_no_diagnosis,phantom_suspicious_prefix,
  frequency_zscore_threshold,provider_peer_deviation,risk_thresholds}.dmn`, ported 1:1 from the
  v1 donor `Maezo-Healthcare-Plan src/maezo/processes/dmn/fraude_scoring/` (already-inverted:
  `indicador_score`/`indicador_label`/`motivo` outputs, no verdict column), orphan-allowlisted
  pending `operadora.fraude.score_indicators` wiring (T2.7 phase 2, after T1.4) — see
  `../medico-auditor/PACKAGE.md`).
- **Review questions:**
  1. **Critical open question the contract itself flags:** does `decisao_fraude=ACUSAR_FRAUDE`
     (the investigator's act) constitute `fraud_accusation` by itself, or is the accusation the
     downstream act (`End_EncaminhadoJuridico`)? Where should the L0 boundary sit?
  2. Confirm the referral obligations (ANS/civil/penal) — deadlines, form, competent authority,
     and the alçada/tier escada by referral destination — none of these are pinned anywhere yet.
  3. Confirm the `bundle_root`-Merkle-over-hash-chain custody design is evidentiarily acceptable
     under Código de Processo Civil/Penal chain-of-custody standards (joint with DPO — see
     `../dpo/PACKAGE.md`).
  4. Confirm RN 593/2023 (cancelamento por fraude) and RN 567/2018 (descredenciamento) currency,
     and which process (CANCEL/INADIMPLENCIA/CRED) owns the adverse terminal for each referral
     direction.
  5. The seam `encaminhar_fraude` is only implemented in CONTAS-001 today; RECURSO/REEMBOLSO/
     CANCEL/NIP need the symmetric human handoff built — confirm this is acceptable as a known
     gap for DRAFT status, not a blocker.

### SP-OP-INADIMPLENCIA-001 — Suspensão / Rescisão por Inadimplência (DRAFT, v0.2.0)

- **Why jurídico:** `contract_termination` L0-hard; RN 593 purga/cure window and prior-notice
  requirements; Lei 9.656 art. 13 §único II (50-day notice threshold).
- **Contract:** `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`
- **DMN:** `spec/processes/dmn/inadimplencia_status.dmn`, `inadimplencia_purga.dmn`,
  `inadimplencia_sla.dmn`.
- **Review questions:**
  1. Does RN 593 consolidate/supersede RN 412/2016 for this process too (same open question as
     CANCEL-001 — the contract notes both processes must cite the **same** vigente source, since
     the harmonization already resolved which process owns which terminal — see contract
     §"Harmonização com CANCEL-001").
  2. Confirm dias úteis vs corridos for the purga window, notificação prévia (até o 50º dia), and
     período mínimo — flagged as an open question (OQ) in the contract.
  3. Confirm `contract_termination` in `_hard_frozen.yaml` (L0) is understood to cover both
     CANCEL-001 and INADIMPLENCIA-001 (the two processes that can terminate a contract).

### SP-OP-LGPD-DSR-001 — Direitos do Titular (DRAFT, v0.1.0)

- **Why jurídico:** `fundamentacao_legal` is required whenever a DSR is denied
  (`NEGAR_FUNDAMENTADO`); retention law (Lei 13.787/2018/CFM) can conflict with the LGPD
  elimination right.
- **Contract:** `docs/processes/contracts/SP-OP-LGPD-DSR-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`
- **DMN:** `spec/processes/dmn/lgpd_dsr_routing.dmn`.
- **Review questions:**
  1. Confirm the legal-basis-for-retention matrix by data type — when does prontuário retention
     (Lei 13.787/2018/CFM) legitimately override an elimination request?
  2. Consent revocation with immediate effect (LGPD art. 18 §2) — does it need a dedicated legal
     pathway distinct from the general DSR flow, given SP-OP-PROGRAMA-001 depends on this
     process's `revogacao_consentimento` as its interrupt trigger?
  3. Confirm the interaction with SP-OP-CANCEL-001 (data retention/elimination on contract
     rescission) doesn't create two conflicting legal answers to the same retention question.

### SP-OP-NIP-001 — Resposta a NIP (DRAFT, v0.1.0)

- **Why jurídico:** `UT_RevisaoJuridicaNip` is the sole source of `MANTER_NEGATIVA`
  (maintaining a denial after a NIP) — `authorization_denial`-class, L0 hard.
- **Contract:** `docs/processes/contracts/SP-OP-NIP-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn`
- **DMN:** `spec/processes/dmn/nip_classification.dmn`, `nip_routing.dmn`, `nip_sla.dmn`.
- **Review questions:**
  1. Confirm RN 388/2016 (NIP procedure and deadlines) currency, and the ~5/~10 business-day
     assistencial/não-assistencial split.
  2. Confirm the stable business-key identifier: `numero_nip_ans` vs `protocolo_ans` — the
     contract flags this as unresolved with regulatório.
  3. Confirm `UT_RevisaoJuridicaNip`'s static candidate group (`juridico-regulatorio,
     medico-auditor`) — the contract explains why it must stay static (three incoming flows,
     "revisão sempre jurídica" invariant) — confirm this legal-review-always requirement is
     correctly understood and non-negotiable.

### SP-OP-PAGTO-001 — Pagamentos de Alçada (DRAFT, v0.1.0) — light review

- **Why jurídico (light):** the payment ceiling itself is internal financial governance (no ANS
  RN), but segregation-of-duties / SOX-like controls have a compliance-legal dimension (approver
  cannot be requester; tier-match enforcement).
- **Contract:** `docs/processes/contracts/SP-OP-PAGTO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn`
- **DMN:** `spec/processes/dmn/pagto_admissibility.dmn`, `pagto_alcada.dmn`, `pagto_sla.dmn`.
- **Review questions:**
  1. Confirm the segregation-of-duties policy (approver ≠ requester) has a documented compliance
     basis and isn't just implied.
  2. Confirm whether "aprovação colegiada" for the comitê tier needs to be modeled as a quorum
     (parallel/multi-instance User Task) for the decision to be legally defensible as collegiate.

### SP-OP-PROGRAMA-001 — Programas de Cuidado (DRAFT, v0.1.0)

- **Why jurídico:** LGPD art. 7/9/11/18 §2 (consent, sensitive health data, revocation with
  immediate effect) anchor the entire process design.
- **Contract:** `docs/processes/contracts/SP-OP-PROGRAMA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
- **DMN:** `spec/processes/dmn/programa_routing.dmn`, `programa_sla.dmn`.
- **Review questions:**
  1. Confirm whether any ANS RN specifically governs health-promotion/APS programs (today only
     LGPD is cited) — the contract flags this as DRAFT/verify with regulatório.
  2. Confirm the legal sufficiency of the consent chokepoint design (`check_consent` fail-closed,
     interrupting boundary on revocation) as satisfying LGPD art. 18 §2's "immediate effect"
     requirement.
  3. Confirm data retention/cessation policy after revocation, in light of Lei 13.787/2018/CFM
     prontuário retention (joint with DPO).

### SP-OP-RECURSO-001 — Recurso de Glosa (DRAFT, v0.1.0)

- **Why jurídico:** `NAO_RECORRER` (accepting the glosa against the provider) is
  `authorization_denial`-class L0 hard; RN 424/2017 governs the appeal deadline.
- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/recurso_admissibility.dmn`, `recurso_eligibility.dmn`,
  `recurso_sla.dmn`.
- **Review questions:**
  1. Confirm RN 424/2017 (recurso/junta médica prazo recursal), RN 305/2016, and RN 501/2022
     (TISS glosa/recurso flow) currency — flagged as possibly consolidated/substituted (R2).
  2. **R5 open question (explicitly "não autorizado neste DRAFT" without jurídico sign-off):**
     should hard procedural-deadline inadmissibility (prazo expirado) ever auto-route without a
     human, or must it always be `ANALISE_HUMANA` as currently modeled? The contract's default is
     the latter and states auto-route requires jurídico sign-off — please confirm or reject.
  3. Confirm the P30D absolute ceiling (three boundary timers sharing one deadline, anchored on
     `data_recebimento_recurso_iso`) against RN 424.

### SP-OP-REEMBOLSO-001 — Reembolso ao Beneficiário (DRAFT, v0.1.0)

- **Why jurídico:** negar/reduzir reembolso is `authorization_denial`-class L0 hard; Lei 9.656
  art. 12 governs reimbursement in livre escolha/fora-de-rede/urgência-emergência.
- **Contract:** `docs/processes/contracts/SP-OP-REEMBOLSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
- **DMN:** `spec/processes/dmn/reembolso_calculo.dmn`, `spec/processes/dmn/reembolso_sla.dmn` exist
  and match. **Path issue:** the contract's DMN table also names `reembolso_admissibility` and
  `reembolso_auto_approval`, neither of which exists as a file in `spec/processes/dmn/` — only
  `reembolso_coverage.dmn` exists there (likely rename of `reembolso_admissibility`, not
  confirmed). See `../medico-auditor/PACKAGE.md` for the full note.
  `reembolso_auto_approval` has no file at all (build gap).
- **Review questions:**
  1. Confirm RN 259/2011 and Lei 9.656 art. 12 currency, and the ~30-day reimbursement deadline
     against the RN actually in force.
  2. Confirm the reimbursement reference table (múltiplo/limite by category/segmentação) has
     regulatory + actuarial sign-off — this is joint with regulatório and finanças (see those
     packages).

## Turnaround

10 business days per contract (standard, proposed, non-binding) — see `../README.md`
§"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
