# SME review tracker — 16 process contracts (T0.6)

One row per contract in `docs/processes/contracts/` (15 DRAFT + 1 FINAL = 16, confirmed by
listing the directory at HEAD `e1c0b34`). See `README.md` for the redline protocol and signoff
spec, and `<role>/PACKAGE.md` for per-contract review questions per role.

**Dispatch status legend:** all rows read `prepared — awaiting roster (blocked external)` — the
six role packages and this tracker are complete and ready to send, but the actual send (naming a
human reviewer, emailing/ticketing them) is blocked on the SME roster from Rodrigo. No contract
below has been sent to anyone; "receipt" and "redline rounds" are consequently all zero/none —
recording anything else would be fabrication.

**Role abbreviations:** MA = médico-auditor · JU = jurídico · DPO = DPO · REG = regulatório ·
FIN = finanças · PO = PO. Full assignment rationale is in each role's `PACKAGE.md`, not repeated
here.

| # | Contract ID | Status (version) | Roles assigned | Dispatch status | Receipt | Redline rounds | Signoff present |
|---|---|---|---|---|---|---|---|
| 1 | SP-OP-ADEQUACAO-001 | DRAFT (v0.1.0) | JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 2 | SP-OP-ANS-CRON-001 | DRAFT (v0.2.0) | REG, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 3 | SP-OP-ANS-SUBMIT-001 | DRAFT (v0.1.0) | JU, REG, DPO, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 4 | SP-OP-AUTH-001 | DRAFT (v0.1.0) | MA, JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 5 | SP-OP-CANCEL-001 | DRAFT (v0.1.0) | JU, REG, DPO, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 6 | SP-OP-CONTAS-001 | DRAFT (v0.1.0) | MA, JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 7 | SP-OP-CRED-001 | DRAFT (v0.1.0) | MA (secondary), JU, REG, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 8 | SP-OP-ESCALATION-001 | **FINAL (v1.0.0)** | MA, REG, PO (retro-verification) | prepared — awaiting roster (blocked external) | none | 0 | **NO — see flag below** |
| 9 | SP-OP-FRAUDE-001 | DRAFT (v0.1.0) | MA (secondary), JU, REG, DPO, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 10 | SP-OP-INADIMPLENCIA-001 | DRAFT (v0.2.0) | JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 11 | SP-OP-LGPD-DSR-001 | DRAFT (v0.1.0) | DPO, JU, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 12 | SP-OP-NIP-001 | DRAFT (v0.1.0) | MA, JU, REG, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 13 | SP-OP-PAGTO-001 | DRAFT (v0.1.0) | FIN, JU (light), PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 14 | SP-OP-PROGRAMA-001 | DRAFT (v0.1.0) | MA, JU, DPO, REG, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 15 | SP-OP-RECURSO-001 | DRAFT (v0.1.0) | MA, JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |
| 16 | SP-OP-REEMBOLSO-001 | DRAFT (v0.1.0) | MA, JU, REG, FIN, PO | prepared — awaiting roster (blocked external) | none | 0 | no |

## Signoff-absent flag — SP-OP-ESCALATION-001

**FINAL contract, zero signoff files on record.** `docs/processes/contracts/signoffs/` does not
exist as a directory (verified: `ls docs/processes/contracts/signoffs/` → no such file or
directory, HEAD `e1c0b34`) — so no contract in this repo has a signoff file yet, but
SP-OP-ESCALATION-001 is the only one that is already **FINAL (v1.0.0)** without one. Per the
no-self-certification rule (constraint 1 of this task and README §"Signoff artifact spec"), this
cannot be waved through retroactively by an agent. It **requires retro-verification**: a human
pass that (a) confirms the SLA values the contract itself marks DRAFT despite the FINAL status
(`escalation_routing` header: "shape FINAL, SLAs DRAFT"; ack/resolution timers annotated
"Politica assistencial ancorada em Lei 9.656/98 art. 35-C — DRAFT/verify (gestao assistencial)"),
and (b) then produces the missing signoff file(s) — by médico-auditor and regulatório, the two
roles assigned in this dispatch (see `medico-auditor/PACKAGE.md` and `regulatorio/PACKAGE.md`).
Until that happens, treat the SLA values in production as unverified even though the contract
text reads FINAL.

## Notes on the "roles assigned" column

- A contract can and typically does need more than one role; each role's `PACKAGE.md` states the
  one-line reason that contract is in its package (e.g. "cites RN 259/566 → regulatório+jurídico",
  "financial ceiling teto_l2 → finanças"). This table only lists the abbreviations to keep the
  16-row view scannable — the justification lives with the role, not here.
- PO appears on all 16 rows because every contract in this batch carries at least one
  organizational-taxonomy or process-design open question (candidate-group names, taxonomy of a
  category/program, ciclo/periodicity resolution) that is a product/process decision, not a
  clinical/legal/privacy/financial one — see `po/PACKAGE.md` for the specific ask per contract.
- "Redline rounds" and "Signoff present" will only change once dispatch is unblocked and a human
  reviewer actually returns a redline PR or a signoff file lands in
  `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml`. This agent does not create,
  simulate, or pre-fill either.
