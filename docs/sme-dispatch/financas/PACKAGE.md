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

### SP-OP-RECURSO-001 — Análise de Recurso de Glosa (DRAFT, v0.1.0)

- **Why finanças:** a granted appeal reverses a glosa and the payer EMITS a payment order for the
  reverted amount; `valor_glosado_brl`/`valor_deferido_brl` are the amounts at stake.
- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn`
- **DMN:** `spec/processes/dmn/recurso_admissibility.dmn`, `recurso_eligibility.dmn`,
  `recurso_sla.dmn`.
- **Review questions:**
  1. **(Open — the OBJECT changed, ADR-0040.)** What you now validate is the payer's EMISSION of a
     payment order for a reverted glosa (`operadora.recurso.handoff_pagamento` →
     SP-OP-PAGTO-001, `tipo_pagamento=glosa_revertida`), not the reconciliation of a payment
     someone else made. `operadora.recurso.reconcile_payment` was DELETED: reconciling money
     RECEIVED is a creditor's act, and the operadora is the payer here. Confirm the emission
     semantics match your actual provider-payment process.
  2. **(Partially answered — still yours to close.)** "No ceiling or extra sign-off for large-value
     glosa reversals" is no longer true: the reversal is now a payment order subject to
     **SP-OP-PAGTO-001's own alcada ladder** AND — by invariant **I-PAGTO-1** — to a mandatory
     human `UT_AnaliseAdmissibilidade` (`coordenacao-financeira`) in EVERY value band, because the
     handoff never seeds `lastro_confirmado`. So the answer moved from "there is no ladder" to
     "there is PAGTO's ladder, plus a human admission before it — confirm that is the right one
     for a glosa reversal." **NOT RATIFIED** — this is a proposal, not a sign-off.
  3. **(New — OQ-3.)** `data_vencimento` is mandatory in SP-OP-PAGTO-001 and the handoff REFUSES it
     blank (never defaults). Where does it come from for a reverted glosa — the original bill's due
     date, or a new term counted from the deferimento? **DRAFT/verify with finanças + jurídico.**
  4. **(New — OQ-R2.)** In `DEFERIR_PARCIAL` the guard enforces
     `valor_deferido + valor_glosa_mantido == valor_glosado` in **integer cents, exact equality**,
     and REFUSES a sum that does not close rather than rounding on its own authority. If your
     operation uses a rounding rule or a tolerance, the guard has to reflect it — tell us which.

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
  4. **(Novo — GAP REEMBOLSO-AUTO-OVERPAY / R-058 + R-059.) Escolha A vs B: o que o caminho
     automático paga quando `solicitado < tabela`.** *recomendação — pendente de assinatura de
     atuária (+ regulatório verifica)*

     **O achado.** `ST_IssuePaymentAuto` paga `${calculo.valor_calculado_tabela_cents}` — o valor
     da **tabela**. O gate `dentro_tabela` que autoriza esse caminho compara com `<=`, nunca com
     `==` (`src/maezo/tools/workers/reembolso.py::calculate_value`). Logo `solicitado < tabela`
     satisfaz o gate e o caminho automático pagaria o valor **maior**: sobrepagamento por
     construção do desenho, não por defeito de digitação. A documentação da própria task afirmava
     "= `valor_solicitado_cents` por construção do gate `dentro_tabela`" — afirmação falsa, hoje
     corrigida no BPMN.

     **O que a engenharia já fez (e por que não decidiu por vocês).** Está em vigor o **bloqueio
     fail-closed genérico**: a condição de `Flow_GW_AutoAprovar` exige também
     `reembolso_auto_liberado`, escrito pelo worker a partir da constante nomeada
     `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO` (hoje `False`), e o worker de pagamento recusa a origem
     automática (`ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO`). Enquanto isso valer, **todo** pedido
     do caminho automático vai a análise humana. O bloqueio é **independente do teto D-07**: subir
     `reembolso_auto_approval.params.max_value_brl` de zero (pergunta 2 acima) **não** o reabre.

     **A escolha que é de vocês (a engenharia não a fará sozinha).**
     - **A — `min(solicitado, tabela)`:** o caminho automático paga o menor dos dois; nunca paga
       acima do pedido nem acima da tabela. É a **recomendação registrada do dono** (revisão de
       decisões 2026-09-04, linha R-058 do unlock-ledger), como *default fail-safe*.
     - **B — rotear a humano só quando `solicitado < tabela`:** o caminho automático segue pagando
       quando `solicitado == tabela`, e os casos de discrepância vão para análise. Fica registrado
       como *fallback* na mesma linha.
     - **C — manter o bloqueio genérico** (o estado de hoje): nada é pago automaticamente. É o
       estado provisório, não uma decisão.

     **O que assinar destrava.** A assinatura desta pergunta é o que autoriza virar
     `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO` para `True` **junto com** a fórmula escolhida — nunca
     uma coisa sem a outra. Ordem obrigatória (`dependencies_note` de R-157): esta assinatura vem
     **antes** de o teto D-07 de reembolso subir de zero.

     **Campos de ratificação (vazios — preenchidos só pelo revisor humano, em
     `docs/processes/contracts/signoffs/SP-OP-REEMBOLSO-001.signoff.yaml`):** `reviewer_name:`,
     `role: financas`, `date:`, `contract_version_reviewed:`, `verdict:`, `notes:` (registrar A,
     B ou C). Este pacote não cria, não pré-preenche e não infere nenhum arquivo de signoff.

## Turnaround

10 business days per contract (standard, proposed, non-binding) — see `../README.md`
§"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.
