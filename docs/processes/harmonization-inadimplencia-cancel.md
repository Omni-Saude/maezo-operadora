# INADIMPLENCIA-001 ⇄ CANCEL-001 Harmonization — BINDING DECISION (RN 593 double-rescission)

**Status:** DECISION (DRAFT/verify flags marked inline) — to be followed VERBATIM by the next-batch
INADIMPLENCIA-001 builder. Authored DESIGN-ONLY (no repo files touched).
**Scope:** resolves the BLOCKING L0 double-termination risk identified in
`docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md` §Harmonizacao before the BPMN is authored.
**Sources read:** `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` (in main),
`docs/processes/contracts/SP-OP-CANCEL-001.md`, `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`,
`spec/policies/autonomy/_hard_frozen.yaml` (confirms `contract_termination: L0`),
`maezo-p3-wa/.../workers/fraude.py` (`start_contratual` handoff precedent + `_deterministic_id`).

---

## TL;DR — the decision

**DRAFT-A is ADOPTED.** Exactly ONE process owns each `contract_termination` terminal:

- **CANCEL-001 OWNS the RESCISAO terminal** (`End_ContratoRescindido`). It is already in `main`, already
  human-gated through `UT_AnaliseRescisao` + `ERR_CANCELLATION_NOT_HUMAN`. Do not duplicate it.
- **INADIMPLENCIA-001 OWNS the cobranca lifecycle**: notificacao previa, the RN-593 cure/purga
  event-gateway, and the **SUSPENSAO terminal** (`End_ContratoSuspenso_Inad`) — suspension is the
  inadimplencia-native adverse effect and there is **no suspension produced by CANCEL-001 in the
  inadimplencia flow that INADIMPLENCIA would collide with at runtime** once routing (below) is set.
- When the human in `UT_AnaliseInadimplencia` decides **RESCINDIR**, INADIMPLENCIA-001 does **NOT**
  rescind. It runs a **handoff** worker that starts/correlates CANCEL-001 and ends at the **neutral**
  `End_RescisaoHandoffCancel`. CANCEL-001's existing human gate produces the actual rescisao terminal.

This means INADIMPLENCIA-001's BPMN declares **NO `End_ContratoRescindido` and NO `register_contract_rescission`
adverse worker is wired into a terminal**. The rescission terminal exists in exactly one place: CANCEL-001.

> **WHY A over B:** CANCEL-001 is already merged to `main`, already passes the no-denial invariant test,
> already carries `responsavel_id` into the ADR-0007 audit chain on `send_cancellation_notice`, and already
> owns `End_PedidoCancelamentoNegado` / `End_ContratoMantido` for the same contract identity. Moving the
> rescission terminal into INADIMPLENCIA (DRAFT-B) would require *editing a merged Phase-2 process to remove
> its inadimplencia rescission branch* — a riskier change to a human-reviewed L0 artifact, and it would split
> the rescisao terminal across two processes depending on trigger (`for_cause_operadora` rescission would
> still live in CANCEL). A keeps **one** rescisao terminal for **all** triggers. B does not. A is strictly
> less invasive and keeps the L0 invariant test green on the existing artifact.

---

## 1. Business-key scheme + how two active terminations are prevented

### Keys (distinct prefix, SAME contract identity)
```
INADIMPLENCIA-001:  INAD-{tenant_id}-{numero_contrato}
CANCEL-001:         CANCEL-{tenant_id}-{numero_contrato}
```
- Distinct prefixes ⇒ the two processes never collide on the engine's business-key uniqueness (each can
  have its own active instance for the same contract). This is correct and intentional — they model
  **different lifecycles** (cobranca/cura vs cancelamento/rescisao).
- **Variant for individual/familiar plans** (where the operational key is the matricula), mirror CANCEL's
  existing variant: `INAD-{tenant_id}-{matricula_beneficiario}`. Keep the SAME tie-break rule CANCEL uses
  so the two processes always derive the same correlation identity.
- **Correlation identity (the join key) is the bare `{tenant_id}-{numero_contrato}`** (or
  `{tenant_id}-{matricula_beneficiario}` for individual/familiar) — prefix-stripped. Both processes MUST
  derive it identically. Use the existing `_deterministic_id`-style derivation (SHA over the bare identity)
  if a stable correlation token is needed for the handoff.

### Anti-double-termination — THREE layers (defense in depth)

1. **Topology (primary, the real guarantee):** only ONE process can reach a rescisao terminal at runtime
   (CANCEL-001). INADIMPLENCIA cannot reach `End_ContratoRescindido` because it does not declare one.
   Suspensao likewise lives in exactly one reachable place per flow (see §2 routing seam — CANCEL's
   inadimplencia branch is *deprecated as a duplicate entry point*; new inadimplencia intake is routed to
   INADIMPLENCIA-001).
2. **Start-time idempotency:** `mcp-cibseven.start_process` consults the business key before starting, so a
   re-sent inadimplencia request returns the active `INAD-…` instance instead of starting a second one
   (same idiom CANCEL already documents).
3. **Worker guard (last line — defense in depth):** every adverse worker checks `ja_em_rescisao_cancel`
   (cross-process correlation: is there an active CANCEL-001 instance already rescinding/suspending this
   `{tenant}-{contrato}`?) and **refuses** to materialize the effect if `true`, raising the BPMN error
   (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN` / on CANCEL's side `ERR_CANCELLATION_NOT_HUMAN`). Even if two
   instances somehow both reached an adverse worker, the second one is refused. This is the existing
   `WorkerBpmnError(error_code)` idiom from the harness.

> **DRAFT/verify:** the cross-process active-instance lookup (`ja_em_rescisao_cancel`) is a query against
> the engine/audit store keyed by the bare correlation identity. It is a FACT resolver (no decision), runs
> in `operadora.inadimplencia.resolve_facts`, and must be deterministic + read-only (TASY write DROP,
> ADR-0013).

---

## 2. WHO owns the terminal rescisao under RN 593 — and the exact seam

### Ownership (binding)
| Effect | Owner process | Terminal | Human gate | Worker (gated) |
|---|---|---|---|---|
| **Suspensao por inadimplencia** | **INADIMPLENCIA-001** | `End_ContratoSuspenso_Inad` (ADVERSO) | `UT_AnaliseInadimplencia` / `UT_CoordenacaoCobranca`, `decisao_inadimplencia=SUSPENDER` | `operadora.inadimplencia.register_contract_suspension` (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`) |
| **Rescisao por inadimplencia** | **CANCEL-001** (handoff target) | `End_ContratoRescindido` (ADVERSO, already in main) | CANCEL's `UT_AnaliseRescisao`, `decisao_cancelamento=RESCINDIR` | CANCEL's `operadora.cancel.send_cancellation_notice` (`ERR_CANCELLATION_NOT_HUMAN`) |
| **Handoff of rescisao (neutral)** | **INADIMPLENCIA-001** | `End_RescisaoHandoffCancel` (NEUTRO) | `UT_AnaliseInadimplencia`, `decisao_inadimplencia=RESCINDIR` triggers handoff (the handoff itself is neutral; the *adverse* decision is re-confirmed by CANCEL's own human gate) | `operadora.inadimplencia.handoff_rescisao` (NOT an adverse worker — it starts/correlates CANCEL) |

### The seam — which process emits/starts what (so a contract is never terminated twice)

```
INADIMPLENCIA-001 human (UT_AnaliseInadimplencia) decides RESCINDIR
        │
        ▼
operadora.inadimplencia.handoff_rescisao  (worker)
   1. derive correlation identity {tenant}-{numero_contrato} (prefix-stripped)
   2. start/correlate CANCEL-001 with business key  CANCEL-{tenant}-{numero_contrato}
      via mcp-cibseven.start_process, passing:
        tipo_solicitacao = "inadimplencia"
        origem_solicitacao = "operadora"     (handoff origin)
        + the resolved inadimplencia facts (meses_inadimplencia, notificacao_previa_feita,
          comprovacao_periodo_minimo, documentos_refs, the inadimplencia responsavel_id for the audit trail)
      start is IDEMPOTENT (business-key lookup → returns active CANCEL instance if any)
   3. publish agents.events.inadimplencia.completed { desfecho: "rescisao_handoff" }
        │
        ▼
INADIMPLENCIA-001 ends at  End_RescisaoHandoffCancel  (NEUTRAL — no adverse effect here)
        │
        ▼  (now CANCEL-001 owns the rescisao decision/terminal)
CANCEL-001 runs its OWN flow: cancel_admissibility (inadimplencia + notificacao_previa_feita=true
   → SEGUE_ANALISE) → UT_AnaliseRescisao (human RE-CONFIRMS, decisao_cancelamento=RESCINDIR with
   fundamentacao/referencia/comprovacao/responsavel_id) → send_cancellation_notice (gated) →
   End_ContratoRescindido (the ONE rescisao terminal).
```

**Critical anti-loop / anti-double rule for the handoff (DRAFT/verify with arquitetura):**
- The handoff carries the already-collected RN-593 evidence so CANCEL's `notificacao_previa_feita=true`
  branch routes to `SEGUE_ANALISE` and does **not** re-open the cure-window (purga already elapsed in
  INADIMPLENCIA). INADIMPLENCIA owns the cure-window; CANCEL must not re-run it on a handoff.
- CANCEL-001's inadimplencia *intake from non-handoff origins* must be reconciled so it is not **also** an
  independent rescission entry point for the same contract while an `INAD-…` instance is active. Two safe
  options for the builder to record (pick with arquitetura — see §open questions):
  - **(A1, preferred, no edit to merged CANCEL BPMN):** route all NEW inadimplencia intake to
    INADIMPLENCIA-001 at the *intake/router* layer (the thing that calls `start_process`), so CANCEL-001 is
    only ever entered for inadimplencia *via the handoff*. CANCEL's existing branch stays as the
    handoff landing zone. No change to the merged artifact.
  - **(A2, fallback):** keep CANCEL reachable for inadimplencia directly, and rely on the
    `ja_em_rescisao_cancel` / reverse `ja_em_inadimplencia` worker guards + start-time idempotency to
    prevent the double. Topologically weaker than A1; only the worker guard saves it.
  > Builder MUST adopt A1 unless arquitetura overrides. A1 keeps the "one reachable rescisao path"
  > property at the topology level, which is what the invariant test ultimately asserts.

### Suspensao seam
Suspensao is INADIMPLENCIA-owned and **not** handed off. CANCEL-001 still *declares* `End_ContratoSuspenso`
in main (cannot be removed without editing the merged artifact), but under A1 it is no longer **entered** for
inadimplencia at runtime (intake routes to INADIMPLENCIA). The worker guard (`ja_em_rescisao_cancel` and its
reverse) is the runtime backstop. **DRAFT/verify:** confirm with arquitetura whether CANCEL's
`End_ContratoSuspenso` should be documented as "legacy/handoff-only, not entered for inadimplencia post-A1"
in a later non-blocking PR — this is NOT required to author INADIMPLENCIA-001 and must NOT block this batch.

---

## 3. Consequences for INADIMPLENCIA-001's BPMN (which end-events it MAY declare)

**MAY declare (and ONLY these):**
| End event | Class | Adverse? | Reachable only via |
|---|---|---|---|
| `End_ContratoSuspenso_Inad` | ADVERSO | YES | `UT_AnaliseInadimplencia`/`UT_CoordenacaoCobranca` human `SUSPENDER` (after cure-window) |
| `End_RescisaoHandoffCancel` | NEUTRO (handoff) | no | `UT_AnaliseInadimplencia` human `RESCINDIR` → `handoff_rescisao` |
| `End_Purgado` | NEUTRO | no | `msg.inadimplencia.pagamento_recebido` inside cure-window |
| `End_ContratoMantido` | NEUTRO | no | `UT_AnaliseInadimplencia` human `MANTER` |
| (optional) `End_RiscoSlaNotificado` | NEUTRO | no | non-interruptive SLA-alert boundary (clone of CANCEL) |

**MUST NOT declare:**
- ❌ `End_ContratoRescindido_Inad` (or any rescisao terminal) — rescisao lives in CANCEL-001 only.
- ❌ Any adverse end reachable without a completed human User Task (no-adverse 5-part pattern, ADR-0018).
- ❌ Any DMN output column that yields `SUSPENDER`/`RESCINDIR`. DMNs only **classify + route**
  (`AGUARDA_PURGA | PENDENTE_NOTIFICACAO | SEGUE_ANALISE | ANALISE_HUMANA`); catch-all → `ANALISE_HUMANA`.

**Worker wiring consequence:**
- WIRE `operadora.inadimplencia.register_contract_suspension` (gated, `ERR_CONTRACT_SUSPENSION_NOT_HUMAN`,
  carries `responsavel_id`+tier, checks `ja_em_rescisao_cancel`) to the SUSPENDER branch → suspensao terminal.
- WIRE `operadora.inadimplencia.handoff_rescisao` (NEUTRAL handoff, NOT an adverse worker; raises
  `WorkerFailure` on transport error like fraude's `start_contratual`) to the RESCINDIR branch → handoff terminal.
- The contract lists `operadora.inadimplencia.register_contract_rescission` +
  `ERR_CONTRACT_RESCISSION_NOT_HUMAN`. Under A, **this worker/error are NOT wired to any reachable terminal.**
  Builder choice (record in BPMN doc): either (i) DROP `register_contract_rescission` and
  `Error_ContractRescissionNotHuman` from INADIMPLENCIA entirely (cleanest — A owns no rescisao terminal), or
  (ii) keep `Error_ContractRescissionNotHuman` *declared but unreferenced* only if a linter forbids unused
  decls (it does — ENGINE-22004 is about dup ids, not unused; unused error decls are fine but add noise).
  **Recommendation: DROP both** to keep the artifact honest about A. The handoff (neutral) is the rescisao path.

**Invariant tests (clone CANCEL's, retarget):**
- `test_suspensao_exige_user_task` — no DMN input combo reaches `End_ContratoSuspenso_Inad` automatically;
  if the adverse terminal is in history, `UT_AnaliseInadimplencia`/`UT_CoordenacaoCobranca` is too.
- `test_expiracao_purga_nao_auto_suspende` — cure-window expiry routes to User Task, never to a terminal.
- `test_inadimplencia_nao_dupla_rescisao` — same `{tenant}-{numero_contrato}` never reaches an adverse
  terminal in BOTH processes; `ja_em_rescisao_cancel` honored; RESCINDIR path ends at the **neutral**
  handoff in INADIMPLENCIA and the rescisao terminal appears (if at all) only in CANCEL's history.
- **DROP `test_rescisao_exige_user_task` from INADIMPLENCIA** (no rescisao terminal here); that invariant is
  already covered by CANCEL-001's existing test. INADIMPLENCIA's RESCINDIR path is exercised by the
  handoff/no-double test instead.
- Report `End_ContratoSuspenso_Inad` (ADVERSO) + the four neutral ends to the integrator for
  `test_no_denial_consolidated.py` (DO NOT edit that shared file).

---

## 4. RN 593 regulatory flags (DRAFT/verify — do NOT hard-code as settled)

All of these are **DRAFT/verify with regulatório+jurídico** and MUST stay flagged in the BPMN/DMN docs:
- **RN 593 supersedes/consolidates RN 412/2016?** — both contracts assume "consolida/substitui (DRAFT)".
  CANCEL cites RN 593 **and** RN 412; INADIMPLENCIA cites RN 593. **Both processes must reference the SAME
  vigente source for the same effect** — do not let CANCEL say "RN 412" and INADIMPLENCIA say "RN 593" for
  the same rescisao. Carry `referencia_regulatoria` as a human-entered field on the User Task (already the
  case) so the citation is the human's, audited, not hard-coded by the engine.
- **Cure/purga window, prior-notice deadline (50º dia), minimum-default period (~60d):** all DRAFT/verify;
  ISO 8601 conservative (corridos); resolve dias-uteis in the worker. **Dias uteis vs corridos = OPEN.**
- **L0 coverage:** `_hard_frozen.yaml` lists `contract_termination: L0` — confirmed it covers BOTH
  suspensao and rescisao by inadimplencia. No new hard action needed; report (do not edit `_hard_frozen.yaml`
  or `L0-core.yaml`): INADIMPLENCIA's adverse actions (suspensao, and rescisao-via-handoff) map onto the
  existing `contract_termination` L0 item.

---

## 5. Open questions for arquitetura/regulatório/jurídico (do NOT block authoring under A; record as DRAFT)

1. **A1 vs A2 intake routing** (does NEW inadimplencia intake go to INADIMPLENCIA-001 only, leaving CANCEL's
   inadimplencia branch as handoff-landing-only?). Builder defaults to **A1**; if arquitetura forces A2, the
   worker guards become the sole double-defense — note it in the BPMN doc.
2. **RN 593 ⇄ RN 412 consolidation** (same citation both processes) — regulatório+jurídico.
3. **Dias uteis vs corridos** for purga/notificacao/periodo-minimo — regulatório.
4. **Fernando** (inadimplencia agent): own SP-OP-INADIMPLENCIA-001 vs navigator-only handoff — PO. Does not
   change A; affects only `prepare_dossier` framing.
5. **Later, non-blocking:** whether CANCEL-001's now-unentered `End_ContratoSuspenso` (under A1) should be
   documented as legacy in a separate PR. NOT this batch.

---

## 6. One-paragraph instruction the next-batch builder follows verbatim

> Author SP-OP-INADIMPLENCIA-001 cloning the CANCEL-001 skeleton (event-gateway cure-window + boundary
> timers + human-gated terminals). Declare exactly these end-events: `End_ContratoSuspenso_Inad` (ADVERSO,
> reachable only via `UT_AnaliseInadimplencia`/`UT_CoordenacaoCobranca` human `SUSPENDER`),
> `End_RescisaoHandoffCancel` (NEUTRAL — reached when human decides `RESCINDIR`, via
> `operadora.inadimplencia.handoff_rescisao` which idempotently starts/correlates CANCEL-001 by business key
> `CANCEL-{tenant}-{numero_contrato}` and ends neutrally), `End_Purgado`, `End_ContratoMantido`, and
> optionally `End_RiscoSlaNotificado`. **Do NOT declare any rescisao terminal and do NOT wire
> `register_contract_rescission` to any terminal — rescisao lives only in CANCEL-001.** Business key
> `INAD-{tenant}-{numero_contrato}` (variant `INAD-{tenant}-{matricula_beneficiario}` for individual/familiar).
> Cross-correlate to CANCEL on the bare `{tenant}-{numero_contrato}`; `resolve_facts` resolves
> `ja_em_rescisao_cancel` (read-only) and the suspensao worker refuses (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`)
> if it is true. Keep every RN-593 prazo/citation DRAFT/verify; carry `referencia_regulatoria` as a
> human-entered audited field. Report topics/end-events/adverse-actions to the integrator — do NOT touch
> `topic_registry.yaml`, `test_no_denial_consolidated.py`, `_hard_frozen.yaml`, `L0-core.yaml`, or the
> process allowlist.
