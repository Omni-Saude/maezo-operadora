# BPMN Process Completeness — Verification Findings

> **Scope.** Verify whether any BPMN **process or subprocess** is missing from
> `src/maezo/processes/bpmn/`, by reconciling the actual `.bpmn` definitions against the
> authoritative catalog (`docs/processes/catalog.md`) and the runtime allowlist
> (`KNOWN_PROCESS_KEYS`). Code is ground truth. Generated 2026-06-14, branch `main`.
> Companion to `docs/audits/forensic-adr-audit.md`.

## Verdict

- **Top-level processes: nothing missing — 15 expected = 15 present = 15 allowlisted.**
- **Subprocesses: 3 present as specified; 0 divergences** (`SUB_RetryEnvio` now built as a named
  embedded subprocess — see §3 Resolved).
- **No `<callActivity>` anywhere** — cross-process handoffs are event-choreographed by design
  (ADR-0003), not a missing subprocess.
- One **documentation** side finding (stale catalog status). No process/capability gap.

---

## 1. Top-level processes — 3-way reconciliation

Catalog (`docs/processes/catalog.md:9-23`) ↔ BPMN files (`src/maezo/processes/bpmn/`, all
`isExecutable="true"`) ↔ `KNOWN_PROCESS_KEYS` (`src/maezo/tools/process_allowlist.py:33-54`,
`== DEFAULT_ALLOWED_PROCESS_KEYS`). All three sets contain the same 15 keys.

| # | Process key | Catalog | BPMN file | Allowlist | Phase |
|---|---|:--:|:--:|:--:|:--:|
| 1 | SP-OP-ESCALATION-001 | ✅ | ✅ | ✅ | 0 |
| 2 | SP-OP-AUTH-001 | ✅ | ✅ | ✅ | 1 |
| 3 | SP-OP-LGPD-DSR-001 | ✅ | ✅ | ✅ | 1 |
| 4 | SP-OP-CONTAS-001 | ✅ | ✅ | ✅ | 2 |
| 5 | SP-OP-RECURSO-001 | ✅ | ✅ | ✅ | 2 |
| 6 | SP-OP-NIP-001 | ✅ | ✅ | ✅ | 2 |
| 7 | SP-OP-ANS-SUBMIT-001 | ✅ | ✅ | ✅ | 2 |
| 8 | SP-OP-CANCEL-001 | ✅ | ✅ | ✅ | 2 |
| 9 | SP-OP-REEMBOLSO-001 | ✅ | ✅ | ✅ | 2 |
| 10 | SP-OP-INADIMPLENCIA-001 | ✅ | ✅ | ✅ | 3 |
| 11 | SP-OP-CRED-001 | ✅ | ✅ | ✅ | 3 |
| 12 | SP-OP-ADEQUACAO-001 | ✅ | ✅ | ✅ | 3 |
| 13 | SP-OP-FRAUDE-001 | ✅ | ✅ | ✅ | 3 |
| 14 | SP-OP-PROGRAMA-001 | ✅ | ✅ | ✅ | 3 |
| 15 | SP-OP-PAGTO-001 | ✅ | ✅ | ✅ | 3 |

**No process appears in one set but not the others.** Confirmed by:
`grep -l 'isExecutable="true"' src/maezo/processes/bpmn/*.bpmn | wc -l` → 15;
distinct `bpmn:process id="…"` → 15.

---

## 2. Subprocess inventory

| Subprocess | Parent process | Type | Status | Evidence |
|---|---|---|:--:|---|
| `ESP_SlaGlobal` | SP-OP-LGPD-DSR-001 | Event subprocess (`triggeredByEvent="true"`, timer — 15-day LGPD SLA) | ✅ present | `SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:205` |
| `SUB_Cuidado` | SP-OP-PROGRAMA-001 | Embedded subprocess (care container) with interrupting boundary message `BME_RevogacaoConsentimento` (consent-revocation → stop+purge) attached to it | ✅ present | `SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn:138` (sub) + `:356` (boundary) |
| `SUB_RetryEnvio` | SP-OP-ANS-SUBMIT-001 | embedded subProcess `SUB_RetryEnvio` com DMN `ans_retry_policy` (backoff PT5M/PT30M/PT2H) — built | ✅ present | `SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn:355` (sub) + `BRT_RetryPolicy`/`ans_retry_policy.dmn`; see §3 Resolved |

Confirmed by `grep -rn 'subProcess id=' src/maezo/processes/bpmn/` → exactly `ESP_SlaGlobal`,
`SUB_Cuidado`, and `SUB_RetryEnvio`; no other embedded/event subprocess elements exist.

---

## 3. The one divergence — `SUB_RetryEnvio` (ANS-SUBMIT)

**Not a missing capability — a modeling divergence from the contract spec.**

The `SP-OP-ANS-SUBMIT-001` contract spec describes an embedded subprocess `SUB_RetryEnvio`
(≈3 attempts, exponential backoff at PT5M/PT30M/PT2H). The BPMN delivers the retry capability,
but models it differently:

- Boundary **error** event `BE_SubmitNack` on `ST_SubmeterEnvio` —
  `SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn:261`.
- A **serviceTask** `ST_RetransmitirEnvio` ("Retransmitir envio (retry/backoff)") —
  `…:349` (comments at `:260,348` call it a "subprocess de retry/backoff", but the element is a serviceTask, not a `<subProcess>`).
- Gateway `GW_Retransmissao` with a `"retry esgotado (default)"` flow to `ST_PublishFailed` — `…:459`.
- **Backoff timing is delegated to the worker's retry policy**, not modeled as BPMN timers.

So: retry-on-NACK with backoff **works**; the *named embedded subprocess element* and
*BPMN-level timed backoff* the spec calls for are **absent**. Left as-is per decision
(findings-only). Deferred options if revisited later:
- **(a) Build to spec** — remodel as an embedded `<subProcess id="SUB_RetryEnvio">` with timer
  backoff (BPMN-first vertical slice: BPMN + worker + test).
- **(b) Align spec to impl** — update the contract + test-spec to describe the implemented
  serviceTask-loop pattern so spec and code agree (lower-cost; the worker-driven pattern is valid).

**Resolved (2026-06-14):** option **(a) Build to spec** was implemented. The retry is now an
embedded `<subProcess id="SUB_RetryEnvio">` (`…bpmn:355`) whose backoff is DMN-driven by
`BRT_RetryPolicy` / `ans_retry_policy.dmn` (PT5M/PT30M/PT2H, DRAFT) feeding the `ICE_Backoff`
timer via `${retry.backoff}`; exhaustion raises `ERR_ANS_RETRY_ESGOTADO` (boundary
`BE_RetryEsgotado`) → `ST_PublishFailed` → `UT_TratarNack`. Entry stays the boundary error
`BE_SubmitNack` (`ERR_ANS_PROTOCOLO_NACK`) on `ST_SubmeterEnvio`. The non-repudio guard is
**unchanged** — the retransmit worker reuses the human-set `APROVAR_ENVIO`+`revisor_id`
(`ERR_ANS_SUBMIT_NOT_HUMAN`) and never re-authorizes (worker-enforced). The historical analysis
above is retained for context.

---

## 4. Architectural note — no `<callActivity>` (intentional, not a gap)

There are **zero** `<callActivity>` elements in any of the 15 BPMN files
(`grep -rc 'callActivity' src/maezo/processes/bpmn/` → all 0). Cross-process handoffs are
**event-choreographed**: a service task publishes a fact and the downstream process starts on
its own trigger — consistent with ADR-0003 (A2A / Kafka facts). Observed handoffs:

- SP-OP-CONTAS-001 → SP-OP-RECURSO-001 (`operadora.contas.start_recurso`)
- SP-OP-FRAUDE-001 → SP-OP-CRED-001 / SP-OP-CANCEL-001 / Jurídico (`…fraude.start_credenciamento` / `…start_contratual` / `…refer_to_legal`)
- SP-OP-INADIMPLENCIA-001 → SP-OP-CANCEL-001 (`operadora.inadimplencia.handoff_rescisao`)
- SP-OP-ADEQUACAO-001 → SP-OP-CRED-001 (`operadora.adequacao.start_credenciamento`)

These are **not** missing subprocesses — they are deliberate choreography. (If a future
requirement needs synchronous orchestration with a return value, `callActivity` would be the
tool; nothing in the current specs requires it.)

---

## 5. Side finding (documentation, not a missing process)

`docs/processes/catalog.md` Status column is **stale**: 12 processes (rows 12–23) show
`nao iniciado` ("not started"), but `docs/handoffs/HANDOFF-phase3.yaml` reports the Phase-2/3
domain as built, merged, and green. This is a doc/reality drift, not a missing process —
flagged for a future catalog refresh, not fixed here.

---

## Confirmation commands

```
grep -l 'isExecutable="true"' src/maezo/processes/bpmn/*.bpmn | wc -l   # → 15
grep -rc 'callActivity'        src/maezo/processes/bpmn/                # → all 0
grep -rn 'subProcess id='      src/maezo/processes/bpmn/                # → SUB_Cuidado, ESP_SlaGlobal, SUB_RetryEnvio
sed -n '33,54p' src/maezo/tools/process_allowlist.py                   # → 15 KNOWN_PROCESS_KEYS
```
