# SME package — regulatório (ANS regulatory affairs)

**Task:** T0.6 dispatch packaging. **Status:** prepared — awaiting SME roster (`blocked(external)`,
see `../README.md` and `../tracker.md`). No reviewer has been named or contacted.

## Re-scope note — RN 639/2025 (SIP filing extinct) — read before the ANS items

**2026-07-18 (t2.6-regulatorio-package-reframe).** The two ANS items below (SP-OP-ANS-CRON-001 and
SP-OP-ANS-SUBMIT-001) were re-framed **before roster** so this package never asks you to ratify an
obligation that no longer exists. Basis:

- **RN 639/2025** revokes RN 551/2022 and desobriga o envio do **SIP** após o 4º trimestre/2025
  (vigência 02/03/2026) — the SIP periodic-filing obligation is **EXTINCT** as of the current date
  (verified against the primary ANS text; `docs/compliance/rn-639-primary-source.md`).
- **DL-0028 / DL-0029** (`../../decisions-log.md`) ratify the extinction and the T2.6 re-scope.
  The ratified direction is **Option B — KEEP and harden the transmit path, not delete it**
  (`../../design/T2.6-ans-submission-rescope.md`, esp. §2.A/§3): `ST_SubmeterEnvio` / the
  `transmit_to_ans` filing pipeline **survive** because they serve obligations that are **still in
  force** — the **NIP-response filing (RN 483/2022)**, **DIOPS** (econ-financeiro trimestral), and
  **Monitoramento TISS** (padrão TISS, RN 501/2022). Only the SIP-specific surface (the `SIP` /
  `RN_124_SIP` report type, its calendar row, and its cron timer) is being surgically retired.

**What this means for your review:** do **not** review SIP periodicity, deadlines, or transmission
— that obligation is gone. Every ANS question below is scoped to the **surviving in-force
obligations** and to the kept-and-hardened transmit path. Whether **Monitoramento TISS** is the
*named successor* to SIP is a separate SME-gated question (RN 639's primary text names no successor)
— see SP-OP-ANS-SUBMIT-001 Q5. The non-ANS items in this package are unchanged.

## How to use this package

1. Read the contract at the canonical path listed for each item (`docs/processes/contracts/` —
   single source of truth; BPMN/DMN under `spec/processes/` are the modeled artifacts).
2. Answer the review questions below in writing against the contract text.
3. Return redlines as a PR against the contract file (preferred) or an annotated copy — see
   `../README.md` §"The redline protocol".
4. Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
   `../README.md` §"Signoff artifact spec". No agent creates this file for you.
5. **Turnaround ask:** 10 business days per contract from receipt (non-binding), except
   SP-OP-ESCALATION-001's retro-verification (3 business days) — see below.

## Why regulatório is on 14 of 16 contracts

Assigned wherever a contract cites an ANS resolution (RN) or an ANS-facing obligation (filing,
NIP response, network adequacy, credentialing notice) that anchors a timer, threshold, or
admissibility rule. Not assigned to SP-OP-LGPD-DSR-001 (LGPD + CFM retention only, no ANS RN cited)
or SP-OP-PAGTO-001 (internal financial governance, no ANS RN — "não há RN ANS específica que
defina o teto" per the contract itself).

## Cross-cutting flag for every contract below

This is the same RN-currency flag as jurídico's package (see `../juridico/PACKAGE.md`): several
contracts cite RN 388, RN 259, RN 412, RN 305, RN 501, RN 593, RN 567, RN 424 as current. The
orchestrator's sweep flagged these as **possibly superseded** by later consolidations (RN 483,
RN 566, RN 593 itself supersedes some of the above). Please confirm per contract whether the
citation is the one in force today — this local check complements T2.5's systematic sweep, it
doesn't replace it.

## Assigned contracts

### SP-OP-ADEQUACAO-001 — Adequação Geográfica de Rede (DRAFT, v0.1.0)

- **Why regulatório:** RN 259 (tempos/distâncias de acesso) and RN 566 (dimensionamento de rede)
  set the thresholds the `adequacao_gap` DMN measures against.
- **Contract:** `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`
- **DMN:** `spec/processes/dmn/adequacao_gap.dmn`, `adequacao_remediation_routing.dmn`,
  `adequacao_sla.dmn`.
- **Review questions:**
  1. Sign off the time/distance thresholds per `tipo_carater` (eletivo vs urgência) that
     `adequacao_gap` measures against, contra RN 259 vigente.
  2. Confirm `ciclo_avaliacao` periodicity (trimestre vs mês) against RN 259/566 — flagged
     unresolved.
  3. Confirm RN 259/RN 566 haven't been consolidated (cross-cutting flag above).

### SP-OP-ANS-CRON-001 — Agendador per-report_type (DRAFT, v0.2.0)

- **Why regulatório:** the `timeCycle` per surviving `report_type` and the derived `competencia`
  mapping are entirely regulatory-calendar questions with no legal-decision content of their own.
- **RN 639 scope note:** the `SP-OP-ANS-CRON-001-RN124SIP` timer (the SIP schedule) is being
  **surgically retired** — the SIP obligation is extinct (see the re-scope note above). Do **not**
  review the SIP timer's cadence; the questions below cover only the timers that dispatch a
  **still-in-force** obligation.
- **Contract:** `docs/processes/contracts/SP-OP-ANS-CRON-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn`
- **DMN:** none (agendador puro; the contract states no DMN of its own).
- **Review questions:**
  1. For each **surviving** timer, tell us **which in-force obligation it serves** and its real
     `timeCycle` (including the date anchor — e.g. `R/P1M` from which day of month): confirm the
     **DIOPS** trimestral cadence and the **Monitoramento-TISS** cadence. Two of the current timer
     labels are miscited and need re-derivation, not confirmation: `RN_209_UTILIZACAO` (RN 209/2009
     is financial/*Recursos Próprios*, revoked by RN 451/2020 — not "utilização") and
     `RN_388_QUALIDADE` (RN 388/2015 is *fiscalização/NIP*, superseded by RN 483/2022 — not
     "qualidade", and NIP is event-driven, not a periodic timer). For each, state the correct
     in-force norm and cadence, or confirm the timer should be dropped.
  2. **Confirm the competência-derivation mapping** (for the surviving timers): the implemented
     rule (`_ans_cron_competencia`) assumes the calendar period (month/quarter/**year**, per
     periodicidade) **immediately prior** to the anchor month — is that the correct regulatory
     mapping, or should it be the period containing the anchor? **Annual case (P12M —
     `QUALIFICACAO`/`RN_388_QUALIDADE`, contingent on it surviving Q1's re-derivation above):**
     the prior calendar year is represented `"YYYY-01"` (not bare `"YYYY"`), for format-
     consistency with the monthly/quarterly `"YYYY-MM"` competências (`ans_cron.py`'s
     `_compute_competencia`) — confirm this representation is acceptable, or state the correct one.
  3. Confirm this scheduler requires no ANS filing of its own (it only dispatches surviving
     obligations to SP-OP-ANS-SUBMIT-001, which carries the actual HITL pre-filing gate).

### SP-OP-ANS-SUBMIT-001 — Envios Periódicos ANS (DRAFT, v0.1.0)

- **Why regulatório:** this is the **generic, obligation-agnostic gated filing pipeline**
  (assemble → validate → human-signs → transmit → track → retry). Per DL-0028/DL-0029 it is being
  **kept and hardened (Option B), not decommissioned** — it is the transmit path for the ANS
  obligations that are **still in force**: the **NIP-response filing (RN 483/2022)**, **DIOPS**
  (econ-financeiro trimestral), and **Monitoramento TISS** (padrão TISS, RN 501/2022). The `SIP` /
  `RN_124_SIP` report type it once carried is **extinct** (RN 639/2025) and is being surgically
  removed. Do **not** review SIP periodicity/deadlines/sources — see the re-scope note above.
- **Contract:** `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn`
- **DMN:** `spec/processes/dmn/ans_calendar.dmn`, `ans_sla.dmn`,
  `ans_submission_admissibility.dmn`, `ans_retry_policy.dmn`.
- **Review questions:**
  1. Confirm `due_date`/`fonte_regulatoria` in `ans_calendar` **for the surviving report types
     only** (DIOPS, Monitoramento-TISS, and the NIP-response filing), against vigente ANS text —
     the contract states every date/periodicity is DRAFT/verify. Note: the `r_rn124_sip` calendar
     row is being removed (SIP extinct — do not review it); the `RN_209_UTILIZACAO` and
     `RN_388_QUALIDADE` rows are **miscited** (RN 209 → financial/RN 451/2020; RN 388 →
     fiscalização/NIP → RN 483/2022) — for each, state the correct in-force norm, or confirm the
     row should be dropped.
  2. Confirm the holiday/business-day resolution policy for `due_date` (contract says "resolver
     feriados/fim de semana no worker de calendário conservadoramente — antecipar, nunca
     postergar" — confirm this conservative default is regulatorily correct). This is
     obligation-agnostic and applies to every surviving filing.
  3. Confirm the **NIP→ANS-SUBMIT handoff** (`origem_envio==nip_filing`) is regulatorily sound —
     this is now a **load-bearing in-force obligation** (the NIP-response filing under RN 483/2022,
     see SP-OP-NIP-001 below), and it is the primary reason the transmit path is kept. Confirm that
     routing a formal NIP response through this filing channel, and sharing the channel with the
     periodic (DIOPS / Monitoramento-TISS) origins, is the correct regulatory posture.
  4. Confirm candidate group names `regulatorio-ans` / `juridico-regulatorio` /
     `coordenacao-regulatorio` against your organizational taxonomy.
  5. **Monitoramento TISS — obligation vs successor.** Independently of SIP: is **Monitoramento
     TISS** an in-force periodic obligation this channel must serve, and what is its governing norm
     and submission version (candidate: padrão TISS under RN 501/2022)? Separately — and this is the
     SME-gated point RN 639's primary text leaves open — is Monitoramento TISS the *named successor*
     to the extinct SIP filing, or merely a distinct in-force obligation? Please answer these two
     parts separately; we are **not** asking you to re-instate SIP.

### SP-OP-AUTH-001 — Autorização Prévia (DRAFT, v0.1.0)

- **Why regulatório:** RN 259 (garantia de atendimento), RN 395 (resposta/negativa), RN 424
  (junta médica) set every SLA and admissibility threshold in this process.
- **Contract:** `docs/processes/contracts/SP-OP-AUTH-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
- **DMN:** `spec/processes/dmn/auth_admissibility.dmn`, `auth_auto_approval.dmn`, `auth_sla.dmn`.
- **Review questions:**
  1. Confirm PT2H (urgência), P10D (eletivo alta complexidade/OPME/internação), P5D (eletivo
     padrão) against RN 259/395 vigente, and the dias-úteis basis of each.
  2. Confirm the junta médica flow (RN 424) prazo/desempate rules — currently unspecified.
  3. Confirm the pendência-de-documentação suspension-of-deadline rule (does RN 395 allow the
     analysis clock to pause while awaiting documents?).

### SP-OP-CANCEL-001 — Cancelamento / Rescisão Contratual (DRAFT, v0.1.0)

- **Why regulatório:** RN 593/2023 and RN 412/2016 govern cancellation/rescission and suspension
  rules.
- **Contract:** `docs/processes/contracts/SP-OP-CANCEL-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` (contract cites
  `..._Cancelamento_Contratual.bpmn` — see `../juridico/PACKAGE.md` for the basename-drift flag).
- **DMN:** `spec/processes/dmn/cancel_admissibility.dmn`, `cancel_routing.dmn`, `cancel_sla.dmn`.
- **Review questions:**
  1. Does RN 593/2023 consolidate/supersede RN 412/2016? (Same question posed to jurídico —
     please answer jointly or cross-reference.)
  2. Confirm the notification-prévia and purga windows and whether they run in business or
     calendar days.
  3. Confirm the granularity/periodicity is coordinated with SP-OP-INADIMPLENCIA-001's citations
     of the same RN 593 (the contracts must not diverge on which RN is vigente for the same
     effect — already flagged by the two contracts themselves).

### SP-OP-CONTAS-001 — Processamento de Contas / Glosa (DRAFT, v0.1.0)

- **Why regulatório:** RN 305/2012 (TISS/Componente de Comunicação) and RN 424/2017
  (recurso/análise de conta) anchor the glosa triage and SLA.
- **Contract:** `docs/processes/contracts/SP-OP-CONTAS-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/glosa_reason_normalization.dmn`, `glosa_classification.dmn`,
  `glosa_triage.dmn`, `contas_sla.dmn`.
- **Review questions:**
  1. Confirm RN 305/2012 currency against vigente ANS text.
  2. Confirm the P30D triagem/análise SLA (anchored on `data_recebimento_lote`) against RN 424.
  3. Confirm the complete TISS reason-code → categoria map used by
     `glosa_reason_normalization` is current.

### SP-OP-CRED-001 — (Des)credenciamento de Prestador (DRAFT, v0.1.0)

- **Why regulatório:** RN 567 (descredenciamento, notificação prévia, substituição) and RN 566
  (dimensionamento de rede) are the two RNs this process exists to implement.
- **Contract:** `docs/processes/contracts/SP-OP-CRED-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`
- **DMN:** `spec/processes/dmn/cred_admissibility.dmn`, `cred_route.dmn`, `cred_prior_notice.dmn`,
  `cred_sla.dmn`.
- **Review questions:**
  1. Confirm RN 567's prior-notice minimum antecedência (`cred_prior_notice`, e.g. P30D) and the
     hospital-vs-isolated-provider distinction for the substitute-equivalent obligation.
  2. Confirm RN 566's network-dimensioning objective criteria feeding `dentro_criterios_rede`.
  3. Confirm the periodicity of `ciclo_avaliacao` used by the CRED→ADEQUACAO network-change bridge
     (trimestre vs mês) — same open item flagged in SP-OP-ADEQUACAO-001.

### SP-OP-ESCALATION-001 — Escalonamento Humano Universal (**FINAL, v1.0.0**) — retro-verification

- **Why regulatório:** the SLA values anchor to Lei 9.656/1998 art. 35-C ("urgência = imediato"),
  and the contract flags this anchor as DRAFT/verify despite the process being FINAL — see
  `../tracker.md` signoff-absent flag.
- **Contract:** `docs/processes/contracts/SP-OP-ESCALATION-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
- **DMN:** `spec/processes/dmn/escalation_routing.dmn`.
- **Retro-verification questions:**
  1. Confirm the ack/resolution SLA values (PT5M/PT30M P1, PT30M/PT4H P2, PT4H/PT24H P3) are
     consistent with Lei 9.656 art. 35-C's "imediato" standard for urgência/emergência.
  2. Since this contract is already FINAL and shipped, please produce the missing signoff file —
     `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` — once this pass is
     complete. No agent will create it.
- **Turnaround ask:** 3 business days (narrow scope — see `../medico-auditor/PACKAGE.md` for the
  paired clinical retro-verification ask on the same contract).

### SP-OP-FRAUDE-001 — Investigação de Fraude (DRAFT, v0.1.0)

- **Why regulatório:** RN 593/2023 (fraud as cancellation ground) and RN 567/2018
  (descredenciamento) both interact with this process's referral targets.
- **Contract:** `docs/processes/contracts/SP-OP-FRAUDE-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`
- **DMN:** `spec/processes/dmn/fraude_indicadores.dmn`, `fraude_routing.dmn`, `fraude_sla.dmn`.
- **Review questions:**
  1. Confirm the ANS referral obligation (deadline, form, competent authority) for confirmed
     fraud findings — the contract states none of this is pinned yet.
  2. Confirm RN 593/RN 567 currency for the referral interactions with SP-OP-CANCEL-001/
     SP-OP-INADIMPLENCIA-001 (contract) and SP-OP-CRED-001 (network).

### SP-OP-INADIMPLENCIA-001 — Suspensão / Rescisão por Inadimplência (DRAFT, v0.2.0)

- **Why regulatório:** RN 593 (purga/cure window, prior notice) and Lei 9.656 art. 13 §único II
  (50-day notice threshold) set every timer in this process.
- **Contract:** `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`
- **DMN:** `spec/processes/dmn/inadimplencia_status.dmn`, `inadimplencia_purga.dmn`,
  `inadimplencia_sla.dmn`.
- **Review questions:**
  1. Confirm the purga window, notificação-prévia deadline (até o 50º dia), and período mínimo
     (e.g. 60 days) against RN 593/Lei 9.656 art. 13.
  2. Confirm dias úteis vs corridos — explicitly an open question (OQ) in the contract.
  3. Confirm RN 593 vs RN 412/2016 supersession, coordinated with the same question in
     SP-OP-CANCEL-001 (the contracts must cite the same vigente source for the same effect).

### SP-OP-NIP-001 — Resposta a NIP (DRAFT, v0.1.0)

- **Why regulatório:** this process exists entirely to implement the NIP response obligation
  (contract cites RN 388/2016; T2.5's currency sweep flags this as possibly superseded by
  **RN 483/2022** — see cross-cutting flag above and confirm below). This is an **in-force**
  obligation, and its formal response is the **NIP-response filing** that anchors the kept
  (Option-B) SP-OP-ANS-SUBMIT-001 transmit path via `origem_envio==nip_filing` — so the NIP
  deadlines here and the SUBMIT filing questions above are two halves of the same in-force channel.
- **Contract:** `docs/processes/contracts/SP-OP-NIP-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn`
- **DMN:** `spec/processes/dmn/nip_classification.dmn`, `nip_routing.dmn`, `nip_sla.dmn`.
- **Review questions:**
  1. Confirm the in-force NIP norm (RN 388/2016 vs **RN 483/2022**) and its assistencial
     (~5 business days) / não-assistencial (~10 business days) deadlines — the contract flags
     exposure to ANS sanction if these are wrong.
  2. Confirm the stable business-key identifier: `numero_nip_ans` vs `protocolo_ans`.
  3. Confirm candidate groups `nucleo-ans` / `regulatorio-ans` / `coordenacao-regulatorio` against
     your organizational taxonomy.

### SP-OP-PROGRAMA-001 — Programas de Cuidado (DRAFT, v0.1.0)

- **Why regulatório:** the contract asks explicitly whether an ANS RN specific to health-promotion
  / APS programs exists (today only LGPD is cited).
- **Contract:** `docs/processes/contracts/SP-OP-PROGRAMA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
- **DMN:** `spec/processes/dmn/programa_routing.dmn`, `programa_sla.dmn`.
- **Review questions:**
  1. Is there an ANS RN specifically governing health-promotion/APS programs that this contract
     should cite alongside LGPD?
  2. If so, does it impose any deadline or reporting obligation not currently modeled?

### SP-OP-RECURSO-001 — Recurso de Glosa (DRAFT, v0.1.0)

- **Why regulatório:** RN 424/2017 (recurso/junta médica), RN 305/2016, RN 501/2022 (TISS
  glosa/recurso flow) anchor the appeal process.
- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/recurso_admissibility.dmn`, `recurso_eligibility.dmn`,
  `recurso_sla.dmn`.
- **Review questions:**
  1. Confirm RN 424/2017, RN 305/2016, RN 501/2022 currency (flagged as possibly consolidated —
     R2).
  2. Confirm the P30D absolute ceiling on the appeal (three boundary timers sharing one deadline)
     against RN 424.
  3. Confirm the ~P5D loop for `ICE_AguardarResposta` (waiting for the operadora's TISS response)
     against the actual TISS turnaround expectation.

### SP-OP-REEMBOLSO-001 — Reembolso ao Beneficiário (DRAFT, v0.1.0)

- **Why regulatório:** RN 259/2011 and Lei 9.656 art. 12 govern reimbursement in
  livre-escolha/fora-de-rede/urgência-emergência; the ~30-day reimbursement deadline is
  DRAFT/verify.
- **Contract:** `docs/processes/contracts/SP-OP-REEMBOLSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
- **DMN:** `spec/processes/dmn/reembolso_calculo.dmn`, `spec/processes/dmn/reembolso_sla.dmn`
  exist and match. See `../juridico/PACKAGE.md` for the `reembolso_admissibility` /
  `reembolso_auto_approval` path/gap flag (relevant to regulatório's admissibility-rule review
  too, since the file that actually runs may not be the one named in the contract).
- **Review questions:**
  1. Confirm the current reimbursement deadline (contract says "~30 dias — DRAFT/verify RN
     vigente") and the shorter urgência/emergência window.
  2. Sign off the reimbursement reference table (múltiplo/limite by category/segmentação) — joint
     with finanças (actuarial) and jurídico.
  3. Confirm RN 259 currency for this process specifically (same RN as ADEQUACAO/AUTH — confirm
     it applies consistently to reimbursement too).

## Turnaround

10 business days per contract (standard), except SP-OP-ESCALATION-001's retro-verification
(3 business days) — see `../README.md` §"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
