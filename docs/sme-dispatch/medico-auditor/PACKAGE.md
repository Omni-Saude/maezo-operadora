# SME package — médico-auditor (medical auditor)

**Task:** T0.6 dispatch packaging. **Status:** prepared — awaiting SME roster (`blocked(external)`,
see `../README.md` and `../tracker.md`). No reviewer has been named or contacted; nothing in this
file implies a review has started.

## How to use this package

1. Read the contract at the canonical path listed for each item below (`docs/processes/contracts/`
   — this is the single source of truth for the process; the BPMN/DMN paths are the modeled
   artifacts it derives from, in `spec/processes/` — see `../README.md` §"On artifact paths" for
   why the contracts' own path prose is out of date).
2. Answer the review questions below, in writing, against the contract text.
3. Return redlines as a PR against the contract file (preferred) or an annotated copy — see
   `../README.md` §"The redline protocol".
4. Signoff (approval or needs-changes) is recorded by you, the human reviewer, in
   `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` — see `../README.md`
   §"Signoff artifact spec". No agent creates this file on your behalf.
5. **Turnaround ask:** 10 business days per contract from receipt (non-binding until the roster
   confirms), except SP-OP-ESCALATION-001's retro-verification ask (3 business days — narrow
   scope, see below).

## Why médico-auditor is on these 9 of 16 contracts

Assigned wherever a User Task's clinical merit, clinical-discharge decision, or clinically
sourced criterion decides an adverse outcome (negativa, glosa técnica/clínica, desligamento
clínico) — the "clinical → médico-auditor" rule from the task charter. Not assigned to
contracts with no clinical merit gate (e.g. ANS-CRON/ANS-SUBMIT scheduling, CANCEL/INADIMPLENCIA
contract termination, LGPD-DSR privacy requests, PAGTO payment alçada) — those are jurídico/
regulatório/DPO/finanças territory, see the other packages.

## Assigned contracts

### SP-OP-AUTH-001 — Autorização Prévia (DRAFT, v0.1.0)

- **Why médico-auditor:** the only source of `NEGAR` (negativa de cobertura) is
  `UT_AnaliseMedicoAuditor`; auto-approval (L2) only fires within a financial ceiling and DUT/ROL
  match — this role owns the clinical merit decision.
- **Contract:** `docs/processes/contracts/SP-OP-AUTH-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
- **DMN:** `spec/processes/dmn/auth_admissibility.dmn`, `spec/processes/dmn/auth_auto_approval.dmn`,
  `spec/processes/dmn/auth_sla.dmn` — all three exist and match the contract's DMN table.
- **Review questions:**
  1. The contract's DMN table lists only the three DMNs above, but the input `dut_atendida` is
     "pré-resolvido por worker DUT/ROL" — `spec/processes/dmn/dut_criteria_bariatrica.dmn`,
     `dut_criteria_oncologia_pet_ct.dmn`, `dut_criteria_terapias_especiais.dmn`, and
     `dut_rol_coverage.dmn` exist in the repo and appear to be that worker's source. Please
     confirm these are in fact what resolves `dut_atendida`, and whether the contract should cite
     them explicitly.
  2. SLA basis: the contract states "prazos legais são em dias úteis; ISO 8601 usa dias corridos"
     — confirm PT2H (urgência), P10D (eletivo alta complexidade/OPME/internação), and P5D
     (eletivo padrão) are meant as business-day windows, and that a calendar-day ISO encoding
     (worst case) doesn't compress the clinically available review window below what RN 395/2011
     and Lei 9.656 art. 35-C actually require.
  3. RN currency: is RN 259/2011, RN 395/2016, and RN 424/2017 (junta médica) still the correct
     citation, or superseded by a later consolidation (RN 483/566/593)?
  4. Junta médica flow (RN 424) is explicitly under-specified ("detalhamento do fluxo de junta:
     prazos/desempate RN 424" — Pendências) — what tie-break and deadline rules should
     `UT_RegistrarParecerJunta` enforce?
  5. Confirm the candidate groups `medico-auditor` / `coordenacao-auditoria-medica` /
     `junta-medica` match your actual organizational taxonomy (they are marked PROPOSTO).

### SP-OP-CONTAS-001 — Processamento de Contas / Glosa (DRAFT, v0.1.0)

- **Why médico-auditor:** glosa acceptance is `authorization_denial`-adjacent (L0 hard); the
  `glosa_triage` DMN routes technical/clinical glosa exclusively to human review, and the
  contract explicitly asks for a compliance sign-off before any auto-route exception.
- **Contract:** `docs/processes/contracts/SP-OP-CONTAS-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/glosa_reason_normalization.dmn`, `glosa_classification.dmn`,
  `glosa_triage.dmn`, `contas_sla.dmn` — all four exist and match the contract.
- **Review questions:**
  1. The contract flags an R4 exception: "auto-route de glosa puramente formatacional só com
     sign-off de compliance; default é humano." Confirm médico-auditor agrees the default (no
     auto-route for técnica/clínica glosa) should stay in force — do not treat silence as consent
     to enable the exception.
  2. Review the `categoria_normalizada` taxonomy in `glosa_reason_normalization`
     (`tecnica`/`administrativa`/`clinica`/`valor`/`documental`/`desconhecida`) for clinical
     accuracy against the TISS glosa reason-code table in force.
  3. Confirm `auditoria-contas` is the right candidate group name for your organization (marked
     PROPOSTO, alternate names `analista-contas-medicas`/`coordenacao-faturamento` cited in the
     contract).

### SP-OP-CRED-001 — (Des)credenciamento de Prestador (DRAFT, v0.1.0) — secondary role

- **Why médico-auditor (secondary):** the objective network-criteria check
  (`dentro_criterios_rede`, RN 566) and the credentialing/decredentialing merit decision are
  currently entirely non-clinical roles (`gestão-rede`/`jurídico-rede`); médico-auditor is
  included here to weigh in on whether provider-quality criteria need clinical input, not because
  the contract currently assigns this role a User Task.
- **Contract:** `docs/processes/contracts/SP-OP-CRED-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`
- **DMN:** `spec/processes/dmn/cred_admissibility.dmn`, `cred_route.dmn`, `cred_prior_notice.dmn`,
  `cred_sla.dmn` — all four exist and match the contract.
- **Review questions:**
  1. `dentro_criterios_rede` (RN 566, network dimensioning) is a purely administrative/geographic
     fact today — does any provider-quality or clinical-adequacy criterion belong in this fact, or
     is it correctly out of scope for médico-auditor?
  2. Should médico-auditor have an advisory (not decision) role in `UT_AnaliseCredenciamento` /
     `UT_AnaliseDescredenciamento` given `false_decredentialing_rate == 0` is a named KPI, or is
     `gestão-rede`/`jurídico-rede` sufficient without clinical input?

### SP-OP-ESCALATION-001 — Escalonamento Humano Universal (**FINAL, v1.0.0**) — retro-verification

- **Why médico-auditor:** this is the flagged case (SP-OP-ESCALATION-001-signoff-absent, see
  `../tracker.md`) — the contract is already FINAL and shipped, but the SLA values were never
  signed off and the contract itself says so ("shape FINAL, SLAs DRAFT"). This is **not** a
  routine DRAFT review; it's a retro-verification of a contract already in production.
- **Contract:** `docs/processes/contracts/SP-OP-ESCALATION-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
- **DMN:** `spec/processes/dmn/escalation_routing.dmn` (shape FINAL, SLA outputs DRAFT per the
  contract's own DMN section). Upstream (consumed before this process starts, not part of this
  contract but referenced by `dmn_decision_ref`): `spec/processes/dmn/triage_redflag_adult.dmn`,
  `triage_redflag_gestante.dmn`, `triage_redflag_mental_health.dmn`,
  `triage_redflag_pediatric.dmn` — all four exist.
- **Retro-verification questions:**
  1. Confirm the ack SLAs (P1 `PT5M`, P2 `PT30M`, P3 `PT4H`) and resolution SLAs (P1 `PT30M`, P2
     `PT4H`, P3 `PT24H`) are clinically adequate given the contract's own annotation: "Política
     assistencial ancorada em Lei 9.656/98 art. 35-C — DRAFT/verify (gestão assistencial)."
  2. Confirm the P1/P2/P3 severity mapping (`grave`→P1, `moderada`→P2, `leve`→P3) and the
     fail-safe catch-all (unknown motivo → P2/`atendimentoHumano`, never P3) match current
     clinical escalation policy.
  3. Confirm the upstream `triage_redflag_*` tables (four files listed above) reflect the current
     red-flag clinical protocol for adult/gestante/mental-health/pediatric populations.
  4. Confirm candidate groups `plantaoClinico` / `enfermagemTriagem` / `atendimentoHumano` /
     `supervisaoAtendimento` are correct.
  - **Once this pass is done, please produce the signoff file** —
    `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` — per `../README.md`
    §"Signoff artifact spec". No agent will create this file for you.
- **Turnaround ask:** 3 business days (narrow scope; existing production behavior is the fallback
  if unconfirmed — this is not a new-contract review).

### SP-OP-FRAUDE-001 — Investigação de Fraude (DRAFT, v0.1.0) — secondary role

- **Why médico-auditor (secondary):** the fraud-indicator scoring includes clinically flavored
  signals (e.g. "phantom" diagnosis coding); médico-auditor input on clinical plausibility of
  those indicators would strengthen the dossier the human investigator relies on, though the
  accusation decision itself (`UT_DecisaoInvestigador`) stays with `investigacao-fraude`.
- **Contract:** `docs/processes/contracts/SP-OP-FRAUDE-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`
- **DMN:** `spec/processes/dmn/fraude_indicadores.dmn`, `fraude_routing.dmn`, `fraude_sla.dmn` —
  all three exist and match the contract. **Ported, wiring pending** (T2.7 artifact phase — no
  longer "não portado"): the 7 scoring DMNs now exist in
  `spec/processes/dmn/upcoding_complexity_ceiling.dmn`, `unbundling_partial_bundles.dmn`,
  `phantom_no_diagnosis.dmn`, `phantom_suspicious_prefix.dmn`, `frequency_zscore_threshold.dmn`,
  `provider_peer_deviation.dmn`, `risk_thresholds.dmn`, ported 1:1 (decision logic byte-faithful)
  from the v1 donor `Maezo-Healthcare-Plan src/maezo/processes/dmn/fraude_scoring/` (commit
  1ba8cb8) — the donor's already-inverted tables: outputs are
  `indicador_score:integer`/`indicador_label:string`/`motivo:string` only, with NO verdict/blocking
  column (the reference repo's verdict domain was removed by the v1 inversion, per the contract's
  "não são portadas (são o anti-padrão puro)" rule). They are deliberate orphans
  (`spec/processes/dmn/orphans-allowlist.yaml`) until `operadora.fraude.score_indicators` is wired
  ("pendente T2.7 fase 2 apos T1.4" — wiring caveat carried forward; DMN-evaluation ADR). The
  synthetic scores/thresholds are unreviewed and flagged for SME sign-off before that wiring lands.
- **Review questions:**
  1. Now that the 7 scoring DMNs exist as real artifacts (T2.7 artifact phase; wiring still
     pending T2.7 phase 2/T1.4), should médico-auditor review the clinical plausibility
     thresholds — in particular `phantom_no_diagnosis.dmn` and `phantom_suspicious_prefix.dmn`,
     which score coding/diagnosis patterns — before they feed
     `indicadores_presentes`/`score_indicadores`?
  2. Confirm the `intensidade_investigacao` routing (`LEVE`/`APROFUNDADA`/`PRIORITARIA`) is purely
     an investigation-priority signal and never functions as a de facto clinical verdict.

### SP-OP-NIP-001 — Resposta a NIP (DRAFT, v0.1.0)

- **Why médico-auditor:** `UT_RevisaoJuridicaNip` candidate groups are statically
  `juridico-regulatorio,medico-auditor` for clinical-merit NIPs (assistencial NIPs contesting a
  clinical negativa).
- **Contract:** `docs/processes/contracts/SP-OP-NIP-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn`
- **DMN:** `spec/processes/dmn/nip_classification.dmn`, `nip_routing.dmn`, `nip_sla.dmn` — all
  three exist and match the contract.
- **Review questions:**
  1. Confirm joint `juridico-regulatorio` + `medico-auditor` review is operationally workable for
     `UT_RevisaoJuridicaNip` when the merit is clinical — who has the casting vote if they
     disagree on `decisao_nip==MANTER_NEGATIVA`?
  2. RN 388/2016 deadlines (~5 business days assistencial, ~10 business days não-assistencial) are
     flagged DRAFT/verify — from a clinical-urgency standpoint, is 5 business days enough for a
     clinically substantive NIP response?

### SP-OP-PROGRAMA-001 — Programas de Cuidado (DRAFT, v0.1.0)

- **Why médico-auditor:** `UT_DecisaoClinica` is the only source of `DESLIGAR_CLINICO` (clinical
  discharge from a care program), an L0-hard clinical decision per ADR-0008.
- **Contract:** `docs/processes/contracts/SP-OP-PROGRAMA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
- **DMN:** `spec/processes/dmn/programa_routing.dmn`, `programa_sla.dmn` — both exist and match
  the contract (note: the contract records that an earlier working name, `programa_stratification`,
  was never deployed — `programa_routing` is the real artifact).
- **Review questions:**
  1. There is no concrete clinical discharge protocol yet — the taxonomy of programs itself
     (crônicos / pré-natal / oncologia / APS) is an open question. What are the clinical
     discharge criteria `UT_DecisaoClinica` should require in `motivo_desligamento_clinico` /
     `referencia_clinica`?
  2. Review the `programa_routing` DMN's risk-band inputs (`risco_estratificado`,
     `elegibilidade_criterios_atendidos`) for clinical validity — remember this DMN runs in-zone
     (PHI) only after the LGPD consent chokepoint passes.
  3. Confirm candidate groups `coordenacao-clinica` / `equipe-cuidado`.

**T2.9 addendum (2026-07-24, `t2.9-sme-packages`; status refreshed 2026-07-25 after merging
`origin/main` 6288341 — #126 landed; the ASKS are unchanged) — `stratify_risk` criteria
prescribed nowhere; `proactive_contact` clinical-appropriateness (joint with DPO):**

  1. **`stratify_risk` — ratify the fail-safe default, and supply the actual criteria.**
     `programa_routing.dmn` consumes `risco_estratificado` (values `{baixo, moderado, alto}`) but
     the DMN's own `<description>` states plainly: "criterios de estratificacao/elegibilidade
     requerem SME medico-auditor + taxonomia de programas" (`programa_routing.dmn:9-10`) — no
     deterministic stratification rule exists anywhere in the repo. **Status (refreshed
     post-merge):** the worker for `operadora.programa.stratify_risk` (`ST_StratifyRisk`,
     delegating "care.stratify a Valentina ... INSTRUI, NAO decide," `bpmn:152-154`) is now
     **BUILT and MERGED (#126)** as a fail-closed delegation stub: it echoes a pre-resolved band
     only when it is a valid member of `_RISCO_BANDAS_VALIDAS` `{baixo, moderado, alto}`, and
     otherwise (absent/invalid/unresolvable) fail-closes to `RISCO_FAIL_CLOSED_DEFAULT = "alto"`
     (`programa.py:107,121,124-186`) — which `programa_routing`'s rule `r_risco_alto` routes
     unconditionally to `ANALISE_HUMANA`, same as the catch-all `r_catchall` for any unmapped
     value. The fail-closed "alto" default is live-proven (#126: 9 integration tests flipped).
     (An earlier revision of this addendum, written against pre-#126 main, correctly recorded the
     worker as unbuilt at that time.) The two asks STAND unchanged: (a) ratify that the
     fail-closed `"alto"` → `ANALISE_HUMANA` default is the correct clinical posture — it is now
     shipped behavior, not just intent; (b) supply the actual clinical stratification criteria —
     what inputs and thresholds should the (still-unwired) Valentina `care.stratify` delegation
     compute into `{baixo, moderado, alto}` in the first place (today entirely unspecified — the
     taxonomy of programs itself, crônicos/pré-natal/oncologia/APS, is the same open question as
     Q1 above).
  2. **`proactive_contact` — PHI-bearing outbound contact, clinical-appropriateness half (DPO
     co-owns the channel/consent half).** `operadora.programa.proactive_contact` is documented as
     contact "so com `consent_checked==true`; re-busca PHI in-zone — precedente Helena/WhatsApp, D9"
     (`docs/processes/contracts/SP-OP-PROGRAMA-001.md:125`) but has no implementing worker yet
     (`programa.py:318-319` gap list — which, post-#126, no longer contains `stratify_risk`).
     From a clinical standpoint:
     which risk bands / program types warrant proactive outbound contact, and does the
     WhatsApp/Helena channel precedent (D9) carry any clinical-appropriateness constraint (e.g.
     should an urgent/high-risk finding ever be delivered by an unmonitored async channel, or
     should it always escalate to a synchronous/human channel instead)? See
     `../dpo/PACKAGE.md` SP-OP-PROGRAMA-001 addendum for the paired consent/channel question.

### SP-OP-RECURSO-001 — Recurso de Glosa (DRAFT, v0.1.0)

- **Why médico-auditor:** `UT_RevisaoAuditorMedico` decides the merit of técnica/clínica glosa
  appeals (`recurso_eligibility` routes there); reuses the same `medico-auditor` group as
  AUTH-001/CONTAS-001.
- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/recurso_admissibility.dmn`, `recurso_eligibility.dmn`,
  `recurso_sla.dmn` — all three exist and match the contract.
- **Review questions:**
  1. Confirm reusing the `medico-auditor` group across AUTH/CONTAS/RECURSO doesn't create a
     workload/conflict-of-interest issue when the same team both denied the original claim and
     reviews the appeal of a related glosa.
  2. Confirm the `glosa_type` taxonomy (`administrativa`/`tecnica`/`clinica`/`linha_duplicada`/
     `formatacao`) that routes to `medico-auditor` vs `analista-recurso-glosa` is clinically
     sound.

### SP-OP-REEMBOLSO-001 — Reembolso ao Beneficiário (DRAFT, v0.1.0)

- **Why médico-auditor:** `UT_RevisaoAuditorMedico` decides clinical merit when
  `requer_avaliacao_clinica=true` (alta-complexidade/OPME, CID sensível, divergência de
  codificação), and negativa/redução of reimbursement is `authorization_denial`-class L0 hard.
- **Contract:** `docs/processes/contracts/SP-OP-REEMBOLSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
- **DMN:** `spec/processes/dmn/reembolso_calculo.dmn`, `spec/processes/dmn/reembolso_sla.dmn` exist
  and match the contract. **Path issue (do not treat as confirmed):** the contract's DMN table
  also names `reembolso_admissibility` and `reembolso_auto_approval` — neither file exists in
  `spec/processes/dmn/`. Only `reembolso_coverage.dmn` exists there and is the likely renamed
  artifact for `reembolso_admissibility` (same `cobertura_prevista`-shaped inputs), but the name
  doesn't match 1:1 — flagged for confirmation, not assumed. `reembolso_auto_approval` has no
  file at all (build gap).
- **Review questions:**
  1. Confirm `requer_avaliacao_clinica` triggers (alta-complexidade/OPME procedures, sensitive
     CID, coding divergence) are clinically complete — is anything missing that should force
     `UT_RevisaoAuditorMedico` involvement?
  2. Confirm whether `reembolso_coverage.dmn` is in fact the built artifact for the
     `reembolso_admissibility` named in the contract (see path issue above) — this affects
     whether the clinical/coverage admissibility logic médico-auditor is reviewing is the one
     actually running.

## Turnaround summary

10 business days per contract (standard), except SP-OP-ESCALATION-001's retro-verification pass
(3 business days). Proposed, non-binding until the human dispatch owner confirms with each
reviewer — see `../README.md` §"Expected turnaround".

## Signoff

Record your verdict (per contract reviewed) in
`docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` following the schema in
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file — that is exclusively your act as the human reviewer.

## Dossie de sessao — ratificacao dos criterios de AUTH (R-163, ate 2026-09-19)

`AUTH-CRITERIA-RATIFICATION-DOSSIER.md` (gemeo legivel por maquina:
`auth-criteria-ratification-dossier.yaml`) e o insumo da **sessao conjunta medico auditor +
juridico/regulatorio + financas** que preenche `ratificado` / `revisor` / `ratificado_em` em
`spec/processes/dmn/auth-criteria-ratification.yaml` (CODEOWNED). Por tabela, o dossie traz
`conteudo atual -> fonte normativa/clinica a conferir -> o que muda ao ratificar`, mais o
**binding sha256** dos bytes de cada tabela em disco, de modo que a assinatura cubra o texto lido
e nao apenas um nome de arquivo. Os tres campos de ratificacao aparecem **vazios**: preenche-los e
ato exclusivo dos signatarios. Enquanto `ratificado: false`, os criterios tecnico, regulatorio e
contratual avaliam FALSE via `auth.py::_gate_on_ratification` — o unico cadeado hoje, ja que
`spec/policies/autonomy/tenants-amh.yaml` fixa `authorization_approval.max_value_brl = 500`
(D-07 FECHADA em 25/08/2026) e o criterio financeiro ja pode passar sozinho; por isso **nada
auto-aprova ainda**, mas por fonte nao ratificada, nao por teto zerado.
