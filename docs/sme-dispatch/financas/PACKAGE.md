# SME package — finanças (finance)

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

## Why finanças is on 7 of 16 contracts

Assigned wherever a contract carries a monetary ceiling, an auto-approval threshold, an alçada
(payment authority) escada, a reference-table calculation, or a debt/ceiling value pending sign-off
— the "ceilings/reimbursement → finanças" rule from the task charter. Every ceiling value named
in these contracts is explicitly marked illustrative/DRAFT, not a real number to deploy against.

## The ceiling/threshold ask, up front

Every contract below has at least one BRL threshold marked "DRAFT/verify financas" or
"ilustrativo, não real" in the contract text. None of these numbers should be read as production
values — they are placeholders the modeling wave used to prove the shape of the escada/teto
mechanism works. **Finanças needs to supply the real numbers** (and the escada/tier→group mapping
where applicable) before any of these contracts can leave DRAFT.

## Assigned contracts

### SP-OP-ADEQUACAO-001 — Adequação Geográfica de Rede (DRAFT, v0.1.0)

- **Why finanças:** the "compromisso financeiro de fallback" (livre escolha / reembolso garantido
  / contratação ad-hoc, triggered by network-adequacy failure) is a financial obligation of the
  operadora with no ceiling defined yet.
- **Contract:** `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`
- **DMN:** `spec/processes/dmn/adequacao_gap.dmn`, `adequacao_remediation_routing.dmn`,
  `adequacao_sla.dmn`.
- **Review questions:**
  1. What is the financial policy for the fallback commitment (limits, interaction with
     SP-OP-PAGTO-001 when the fallback generates a payment, interaction with
     SP-OP-REEMBOLSO-001 for the guaranteed-reimbursement variant)? The contract states this is
     entirely DRAFT.
  2. Is `estimativa_custo_cents` (informational, recorded for audit) sufficient, or should it
     drive any threshold/routing logic?

### SP-OP-AUTH-001 — Autorização Prévia (DRAFT, v0.1.0)

- **Why finanças:** the auto-approval path (`auth_auto_approval`, L2) only fires within
  `dentro_teto_l2` — a per-tenant financial ceiling on `valor_estimado_brl`.
- **Contract:** `docs/processes/contracts/SP-OP-AUTH-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
- **DMN:** `spec/processes/dmn/auth_admissibility.dmn`, `auth_auto_approval.dmn`,
  `auth_sla.dmn`.
- **Review questions:**
  1. Sign off the `teto do tenant` value(s) referenced from `tenants-amh.yaml` that gate
     `dentro_teto_l2` for auto-approval.
  2. Confirm auto-approval should remain integral-only (no partial approval ever auto-fires) —
     matches the platform-wide pattern used elsewhere (REEMBOLSO, PAGTO).

### SP-OP-CONTAS-001 — Processamento de Contas / Glosa (DRAFT, v0.1.0)

- **Why finanças:** glosa acceptance directly reduces payment to the provider; the financial
  impact calculation (`denial_ratio`, `total_glosado_candidato_centavos`) needs finance
  sign-off on the arithmetic and any downstream reconciliation policy.
- **Contract:** `docs/processes/contracts/SP-OP-CONTAS-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/glosa_reason_normalization.dmn`, `glosa_classification.dmn`,
  `glosa_triage.dmn`, `contas_sla.dmn`.
- **Review questions:**
  1. Confirm the `Long`-above-R$21.47M landmine note on `total_glosado_candidato_centavos` (int32
     overflow risk) is adequately handled — is there a real-world lote size that could hit this?
  2. Confirm the reconciliation path (`reconcile_payment` on `REENVIAR`) matches your actual
     payment-reconciliation process.

### SP-OP-INADIMPLENCIA-001 — Suspensão / Rescisão por Inadimplência (DRAFT, v0.2.0)

- **Why finanças:** `valor_total_devido_cents` and `meses_inadimplencia` are debt figures that
  drive suspension/rescission; the collection policy itself is a finance process.
- **Contract:** `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`
- **DMN:** `spec/processes/dmn/inadimplencia_status.dmn`, `inadimplencia_purga.dmn`,
  `inadimplencia_sla.dmn`.
- **Review questions:**
  1. Confirm the "período mínimo" (e.g. 60 days) and purga-window thresholds reflect actual
     collections policy, not just the regulatory floor (RN 593) — regulatório owns the legal
     minimum, finanças owns any stricter internal policy layered on top, if one exists.
  2. Confirm `gestao-cobranca` candidate group and its coordination with `juridico-contratos`
     matches how your collections team actually operates.

### SP-OP-PAGTO-001 — Pagamentos de Alçada (DRAFT, v0.1.0)

- **Why finanças:** this is the primary financial-controls contract — the entire alçada escada
  (payment authority ladder) is finance policy, not ANS regulation ("não há RN ANS específica que
  defina o teto; o teto é governança financeira interna").
- **Contract:** `docs/processes/contracts/SP-OP-PAGTO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn`
- **DMN:** `spec/processes/dmn/pagto_admissibility.dmn`, `spec/processes/dmn/pagto_alcada.dmn`,
  `spec/processes/dmn/pagto_sla.dmn`.
- **Review questions:**
  1. **Sign off the complete alçada escada** — the contract's illustrative values are: L2
     auto-release ceiling ≤ R$100,000; `ALCADA_L1` R$100k–500k; `ALCADA_L2` R$500k–2MM;
     `ALCADA_L3` R$2MM–10MM; above R$10MM or ambiguous → `comite-financeiro`. **These are stated
     explicitly as illustrative, not real** — please supply the actual thresholds.
  2. Sign off the faixa→grupo→tier mapping (`aprovacao-financeira-l1/l2/l3`, `comite-financeiro`,
     `coordenacao-financeira`) against your actual approval hierarchy and candidate-group/IdP
     names.
  3. Confirm the segregation-of-duties policy (approver ≠ requester) and whether the comitê tier
     requires collegiate (quorum) approval — the contract flags this as undetailed.
  4. Sign off `sla_aprovacao` per tier (illustrative: P2D L1 … P5D comitê) — do higher tiers
     really need longer SLA, and how long?
  5. Confirm the SLA-breach coordination-assumption path re-enforces tier-match (a coordinator
     assuming an escalated approval still needs the tier appropriate to the value — confirm this
     is what you want, not an automatic override).

### SP-OP-RECURSO-001 — Recurso de Glosa (DRAFT, v0.1.0)

- **Why finanças:** a successful appeal reverses a glosa and triggers repayment
  (`reconcile_payment`); `valor_glosado_brl` is the amount at stake.
- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/recurso_admissibility.dmn`, `recurso_eligibility.dmn`,
  `recurso_sla.dmn`.
- **Review questions:**
  1. Confirm the re-payment reconciliation semantics (`operadora.recurso.reconcile_payment` on
     deferimento) match your actual provider-payment reconciliation process.
  2. Confirm no ceiling or extra sign-off is needed for large-value glosa reversals (the contract
     doesn't currently model a value-based escada here, unlike PAGTO-001) — should one exist?

### SP-OP-REEMBOLSO-001 — Reembolso ao Beneficiário (DRAFT, v0.1.0)

- **Why finanças:** the reimbursement reference table (`reembolso_calculo`) and the L2
  auto-approval ceiling (`dentro_teto_l2`) are both finance-owned figures requiring actuarial
  input.
- **Contract:** `docs/processes/contracts/SP-OP-REEMBOLSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
- **DMN:** `spec/processes/dmn/reembolso_calculo.dmn`, `spec/processes/dmn/reembolso_sla.dmn`
  exist and match. Note: the contract's DMN table also names `reembolso_admissibility` and
  `reembolso_auto_approval`; neither file exists in `spec/processes/dmn/` (see
  `../juridico/PACKAGE.md` for the full path/gap note) — the auto-approval ceiling logic finanças
  is signing off on may not yet be built.
- **Review questions:**
  1. **Sign off the reimbursement reference table** (múltiplo/limite por categoria/segmentação
     used by `reembolso_calculo`) — joint with regulatório (legal basis) and jurídico.
  2. Sign off the L2 auto-approval ceiling (`dentro_teto_l2`, per `tenants-amh.yaml`) — confirm
     it's the same governance process as AUTH-001's ceiling or a distinct one.
  3. Given `reembolso_auto_approval` has no DMN file yet (build gap), confirm the auto-approval
     ceiling policy you're signing off on is accurately described in the contract text even
     though the artifact doesn't exist to inspect directly.

## Turnaround

10 business days per contract (standard, proposed, non-binding) — see `../README.md`
§"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
