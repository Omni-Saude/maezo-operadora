# Alert Runbook: MaezoContasPagtoOrdemDeOutraConta

**Audience:** Finance (contas a pagar / analise de contas), platform on-call
**Last updated:** 2026-11-02
**Applies to:** `worker-daemon` (topic `operadora.contas.handoff_pagamento`, process SP-OP-CONTAS-001)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_pagamento_contas`
**Requirement source:** owner decision DEC-30 D11 (2026-11-02, issue #1084: "manter o incidente
visível + alerta e procedimento no runbook antes da janela ADR-0068"); key design ADR-0068
(Accepted, DEC-29) and ADR-0074 (Proposed, DEC-30 D12/D13)

---

## Table of Contents

1. [Trigger](#1-trigger)
2. [What this means in business terms](#2-what-this-means-in-business-terms)
3. [First 5 minutes](#3-first-5-minutes)
4. [Procedure for finance (before the key window)](#4-procedure-for-finance-before-the-key-window)
5. [Escalation](#5-escalation)
6. [False-alarm checks](#6-false-alarm-checks)

---

## 1. Trigger

```yaml
- alert: MaezoContasPagtoOrdemDeOutraConta
  expr: |
    sum by (veredito, fase) (
      increase(maezo_contas_pagto_ordem_recusada_total[5m])
    ) > 0
```

`maezo_contas_pagto_ordem_recusada_total{veredito,fase}` is incremented once per refused attempt by
`src/maezo/tools/workers/contas.py::_recusar_ordem_de_outra_conta`, the same site that writes the
log line `contas_handoff_pagamento_ordem_de_outra_conta` and raises
`ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO` (an engine incident at `ST_HandoffPagamentoAuto`,
`ST_HandoffPagamentoHumano` or `ST_HandoffPagamentoParcial`). Labels are closed vocabularies:

- `veredito`: `outra` (the existing order is provably of another conta or another prestador) or
  `indeterminada` (the identity cannot be proven: order seeded before the conta entered the seed,
  order without `prestador_id`, or an order the engine says exists but history cannot find);
- `fase`: `legado` (default), `leitura_dupla` or `escrita_alvo` (`MAEZO_PAGTO_CHAVE_CONTA_FASE`).

The series is pre-initialised at 0 when the CONTAS workers register, so the first refusal is visible
to `increase()`. The count is of ATTEMPTS (the worker retries in-process); the list of affected
contas is the list of engine incidents, not this number. No tenant, lote, guia, conta or key ever
appears as a label.

## 2. What this means in business terms

A conta was approved for payment, but the system did NOT create its payment order. Today the
payment-order key (`PAGTO-{t}-{lote}-{prestador}`) does not carry the conta, so the second conta of
the same prestador in the same lote collides with the first conta's order. Before #1180 that second
conta was silently recorded as "paid"; now it is refused and visible. **The prestador is owed money
that no order covers.** The fix (key per conta, `PAGTO-{t}-C-{prestador}-{guia}-{conta}`) ships dark
and only takes effect inside the key-migration window (ADR-0068/ADR-0074).

## 3. First 5 minutes

```promql
sum by (veredito, fase) (increase(maezo_contas_pagto_ordem_recusada_total[1h]))
```

```bash
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep contas_handoff_pagamento_ordem_de_outra_conta | tail -20
```

In CIB Seven Cockpit, list the open incidents of SP-OP-CONTAS-001 at `ST_HandoffPagamento*` whose
message contains `NAO e' comprovadamente desta conta`. Each incident is one conta without an order.
The log line carries `numero_lote_tiss`, `pagto_business_key`, `pagto_instance_id`, `veredito` and
`historico_encontrado` (no guia/conta: personal data, ADR-0068 row 2).

## 4. Procedure for finance (before the key window)

Follow `docs/runbooks/pagto-chave-por-conta-migracao.md` §3 ("O que fazer com o incidente hoje").
In short:

1. **Identify the conta.** From the incident, open the SP-OP-CONTAS-001 instance: its business key
   is `CONTAS-{t}-{guia}-{conta}`; record tenant, prestador, lote, guia, conta, the amount and the
   `pagto_instance_id` the incident names (the order that belongs to the OTHER conta).
2. **Record it in the inventory.** Add the conta to the pending list of the key window; the
   read-only inventory (`scripts/ops/pagto_chave_conta_inventario.py`, runbook §5 step 2) lists it
   under `contas_sem_ordem`.
3. **Do not retry now.** In `legado` and `leitura_dupla` the retry fails the same way, by design.
4. **Retry only after `escrita_alvo`** (and only after the race-block precondition of the migration
   runbook §5 step 5 is met): the retry reads the lot-keyed order, proves it belongs to another
   conta and opens this conta's own order, which goes through PAGTO's mandatory human
   `UT_AnaliseAdmissibilidade` (I-PAGTO-1).
5. **Never pay outside the system** without the SP-OP-PAGTO-001 order: a manual payment has no
   order, no alçada, no audit trail and would be paid AGAIN when the order is created in the window.

A `veredito=indeterminada` incident stays refused even after `escrita_alvo` until a human attributes
the old order to a conta (inventory disposition `indeterminada`); that is correct: it may be the
same conta.

## 5. Escalation

- Finance lead owns the pending list and the decision to pay before the window (owner decision; an
  out-of-system payment is NOT an available mitigation).
- Platform on-call: only if the refusals do not match the "2nd conta of the same lote" pattern
  (e.g. `veredito=indeterminada` with `historico_encontrado=false` repeatedly — engine history
  unavailable or inconsistent), or if `fase` is not what the deployment declares.

## 6. False-alarm checks

- A legitimate retry of an OLD order (created before `numero_conta` entered the seed) for the same
  conta is refused as `indeterminada` (fail-closed by design, gate advisory A4 of #1180): it is not a
  new lost payment, but it still needs a human to confirm that the existing order pays this conta.
- A conta re-presented in another lote after an alias in the window may also be refused (advisory
  A5): reconcile against the inventory before acting.
