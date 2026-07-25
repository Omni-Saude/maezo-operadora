# ADR-0031: LGPD DSR identity gate — fail-closed on an explicit `identidade_verificada is True` [T2.8]

**Status:** Proposed (2026-07-19) · **Data:** 2026-07-19 · **Area:** Seguranca (LGPD/PHI, runtime spine)

> Orchestrator-ratified design; authored for the gatekeeper. **Pending DPO ratification of the
> PRODUCER POLICY only** — i.e. *which* channels may seed `identidade_verificada=True` and *which*
> proof types are sanctioned (LGPD Art. 11 human-review of sensitive data). The fail-closed
> *consumer* semantics decided here do NOT wait on that: reading the real signal instead of
> rubber-stamping is strictly safer under any producer policy. Every citation re-pinned against
> `main = a86195e`.

## Contexto

`SP-OP-LGPD-DSR-001` (Direitos do Titular) is the process a data subject uses to exercise LGPD Art.
18 rights (access / portability / rectification / erasure). Its own contract and BPMN state the
threat model explicitly: *"Verificacao de identidade ANTES de qualquer compilacao de dados (anti
engenharia social)"* (`docs/processes/contracts/SP-OP-LGPD-DSR-001.md:11`) and *"Anti-vazamento:
requisicao LGPD e vetor classico de engenharia social"* (`bpmn:87`). A subject-rights request is a
classic exfiltration vector: an attacker impersonates the titular to pull their health data (LGPD
Art. 11 — sensitive), so the identity gate is the primary structural defense before
`ST_CompilarPacote` compiles any PHI.

**The defect (A3, fail-OPEN).** `ValidateIdentityWorker.execute()` set `identidade_confirmada=True`
on the mere *presence* of the OBLIGATORY subject identifier `titular_pseudo_id` — it never read any
verification signal. `identidade_verificada` (the donor's clerical fact) was seeded at process
start but consumed NOWHERE. Consequence: every normal request (pseudo_id always present) sailed
through `GW_Identidade`'s confirmed branch straight to `BRT_RotearDsr` → `ST_CompilarPacote`, and
the entire anti-social-engineering challenge sub-flow (`ST_PedirProvaAdicional` → `GW_AguardarProva`
→ P10D `ICE_PrazoProva`, `bpmn:133-169`) was DEAD CODE. PHI *disclosure* stayed fail-closed behind
`UT_RevisaoDpo` (human), but the automated identity-verification burden collapsed entirely onto the
human DPO — a defense-in-depth loss the contract's own invariant forbids.

**Adjacent defect GAP-LGPD-6 (dangling boundary).** For the empty-`titular_pseudo_id` case the
worker RETURNED `ERR_DSR_IDENTITY_UNVERIFIED` as a dict *value* instead of RAISING it, so the
modeled boundary `BE_IdentidadeInverificavel` (`bpmn:105-108`, `errorRef=Error_LgpdIdentidade`) could
never fire and `End_IdentidadeInverificavel` (`bpmn:121`, a NEUTRAL fail-safe terminal) was
unreachable — the empty case fell through `GW_Identidade`'s ordinary default branch.

**Producer reality.** No v2 producer of any identity-verification signal was wired, and
`operadora.lgpd.request_additional_proof` (`ST_PedirProvaAdicional`, `bpmn:133`) had NO worker — so
even had the gate read a real signal, the challenge path would HANG in production (the task would
never complete, never reaching `GW_AguardarProva`).

## Decisao

1. **Consumer is fail-closed on the explicit boolean.** `ValidateIdentityWorker` sets
   `identidade_confirmada = (process_vars.get("identidade_verificada") is True)`. Only the literal
   `True` confirms; absent / `False` / truthy-garbage (`"true"`, `1`) → NOT confirmed →
   `GW_Identidade` routes to the challenge sub-flow. The presence of `titular_pseudo_id` NEVER
   confirms identity. (`src/maezo/tools/workers/lgpd.py`.)

2. **GAP-LGPD-6: RAISE, not return.** Empty/absent `titular_pseudo_id` →
   `raise WorkerBpmnError("ERR_DSR_IDENTITY_UNVERIFIED", ...)`, caught by
   `BE_IdentidadeInverificavel` → `End_IdentidadeInverificavel` (NEUTRAL, non-adverse). This is a
   TECHNICAL guard (mechanical impossibility of verifying), NEVER an automatic fraud accusation —
   the L0-hard `fraud_accusation` action is untouched, and the raised message carries no accusatory
   language. `max_retries=1` on the worker (mirrors `auth.SendDenialNoticeWorker`) so
   `WorkerBase.run()`'s generic-exception retry loop (`base.py:143-201`, which retries even
   `WorkerBpmnError` with `time.sleep`) does not delay/mask propagation to the harness boundary
   dispatch — the ENGINE owns durable retry (T1.1 §9).

3. **`ERR_DSR_IDENTITY_UNVERIFIED` is a Tier-0 production allowlist code.** It is consumption-covered
   (verify_identity is consumed only by SP-OP-LGPD-DSR-001, which declares the boundary — proven by
   `scripts/ci/check_bpmn_error_allowlist.py`) and NON-adverse (a technical fail-safe to a neutral
   terminal, not a denial), so it is NOT T-E-gated (ADR-0030 §4) and IS unioned into
   `worker_runtime/service.py`'s production `bpmn_error_allowlist` now. The module exposes
   `LGPD_BPMN_ERROR_ALLOWLIST` (mirrors `AUTH_BPMN_ERROR_ALLOWLIST`); the boundary-proof gate — not
   the constant — is the source of truth.

4. **Build the #55 R-B `request_additional_proof` worker.** A raw async handler on
   `operadora.lgpd.request_additional_proof` that ONLY asks the titular for additional proof (emits
   an internal notification) so the flow reaches `GW_AguardarProva`. It MUST NOT set
   `identidade_verificada` — confirmation arrives LATER from a ratified producer.

5. **Two ratified producers of `identidade_verificada=True` (policy pending DPO).** (a) a DPO-ratified
   authenticated-channel seed at intake; (b) the human proof-review message correlation
   `msg.lgpd.proof_received` (`bpmn:143-146`) which loops back to `ST_VerificarIdentidade`
   (`Flow_ProvaOk_Verify`, `bpmn:359`) to re-verify. What COUNTS as authenticated / sanctioned proof
   (doc match / gov-id / portal auth / DPO attestation) is the **producer policy** the DPO must
   ratify — the consumer decided here is agnostic to that choice.

**Grounding (no invented law):** LGPD Art. 18 (confirm the requester IS the titular before acting on
subject rights), Art. 11 (health data is sensitive → the whole flow is human-reviewed at
`UT_RevisaoDpo`), Art. 19 II (15-day response window — the `ESP_SlaGlobal` P15D subprocess keeps the
legal clock running even while the request parks awaiting proof). The anti-social-engineering
requirement is the **process's own** threat model (`bpmn:87`, `contract:11`), not an external
citation. ADR-0030-conformant (modeled `WorkerBpmnError` opt-in per gate-proven code).

## Consequencias

**Positivas:**
- The identity gate performs a real verification check; the anti-social-engineering challenge
  sub-flow is live, not dead code. No PHI is compiled (`ST_CompilarPacote`) on a missing / `False` /
  garbage identity signal.
- GAP-LGPD-6's neutral fail-safe terminal (`End_IdentidadeInverificavel`) is reachable and publishes
  `lgpd_dsr.completed(desfecho=identidade_inverificavel)` — DPO visibility, never a silent drop.
- Zero BPMN/DMN edits — the model already wired both the boundary and the challenge/re-verify loop;
  only the worker code was wrong.
- Four T3.1 strict-xfails (boundary catch, prova-expira, pede-prova, prova-recebida) flip to passing,
  each now asserting the fail-closed property directly on a live engine.

**Negativas (aceitas):**
- Until the DPO ratifies the producer policy and a producer is wired, a request with no seeded
  `identidade_verificada` parks at `GW_AguardarProva` and, absent proof, expires at P10D
  (`End_ExpiradaIdentidade`) — the *safe* failure (no PHI leaves), and the intended posture pending
  policy. The 15-day legal clock is unaffected (independent subprocess).
- `request_additional_proof` emits a notification only where a Kafka producer is wired; with
  `kafka=None` it logs loudly and completes (so the flow still reaches `GW_AguardarProva`) — mirrors
  `events.py`'s ratified `kafka=None` posture.
- `ERR_DSR_ERASURE_{FAILED,NOT_HUMAN}` remain declared-but-unbound and `execute_request` /
  `compile_data_package` / `send_response` / `notify_sla_risk` remain worker-less (#55
  R-C/R-D/R-F/R-G) — out of scope here, tracked separately.

## Supersedes
—  (refina o gate identity de SP-OP-LGPD-DSR-001; conformante com ADR-0030 e ADR-0008 no-denial.)
