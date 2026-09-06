"""Gustavo as A2A delegation TARGET (`nip.instruct`) — GUS-01's target half.

`spec/agents/gustavo/agent.yaml` declares `accepted_task_types: [nip.instruct]`;
`docs/processes/contracts/SP-OP-NIP-001.md` declares the message start
(`msg.nip.instruct` — "start por delegacao A2A `nip.instruct`, correlaciona
`NIP-{tenant}-{numero_nip_ans}`") and the input variable `origem_a2a` ("`true` quando a instancia
nasce de delegacao A2A `nip.instruct`"). All three sides were missing (CC-02/GUS-01); this module
builds the TARGET one, mirroring `agents/fernando/delegation.py` / `agents/carolina/delegation.py`.

WHAT IS AND IS NOT BUILT HERE:
- TARGET side (`state_from_envelope` + `make_gustavo_handler`) — BUILT. Compiles Gustavo's REAL
  graph (`agents.gustavo.graph.build(config)`, fail-closed on missing `inference`/`dmn`/
  `cibseven`/`audit_sink`) and runs it against envelope-materialized state.
- ORIGIN builders (`build_nip_instruction_envelope` / `delegate_nip_instruction`) — BUILT and
  unit-proven in isolation, **NOT CALLED** from `tools/workers/nip.py`. That worker is outside
  this work package's editable surface, and CONFIRM-C recorded its `assemble_response` docstring
  ("Delegates to Gustavo") as a FALSE claim over code that returns `minuta: ""` — honestifying it
  is a SEPARATE work package, not this one, and this module does not claim it is done.
- REGISTRATION in `runtime.agent_runtime.a2a_composition` — **NOT DONE** (owner decision, gap
  `FERNANDO-DELEGATION-CALL-SITE`). Nothing routes a `nip.instruct` envelope here today.

J2 ONLY. Gustavo serves two flows; only the NIP one (`fluxo="nip"`) is an A2A target.
`ans_submit` has no accepted task type and — per his own graph docstring — no live trigger at
all, so this seam sets `fluxo="nip"` EXPLICITLY and never reads it off the envelope: his graph's
own fail-safe for an absent/unknown `fluxo` is the conservative NIP route, and depending on a
fail-safe to select a PROCESS is not a design.

Idempotency (Guard 4): `task_id` IS the SP-OP-NIP-001 business key
(`NIP-{tenant}-{numero_nip_ans}`), delegated to `gustavo.graph._business_key` itself so the two
derivations cannot drift — the SAME key the contract's `msg.nip.instruct` correlates on. An
engine re-delivery replays the SAME delegation result without re-running Gustavo, and his own
`start_process` is business-key idempotent anyway.

INPUT-BOUNDARY GATE: Gustavo's graph has no `new_gustavo_state` constructor (like Carolina's /
Fernando's / Beatriz's, unlike Marina's / Rafael's); his gate is `graph._CALLER_INPUT_FIELDS` +
`receive`'s own output-field reset. `state_from_envelope` enforces it by ALLOWLIST-BY-
CONSTRUCTION — `raw` only ever copies named keys off the `_..._META_KEYS` tuples below (each a
subset of `_CALLER_INPUT_FIELDS`), so an out-of-allowlist `payload_meta` entry is never read at
all. The `unknown` check at the end is the same structural regression guard Carolina's carries.

L0 HARD (contract §Invariante): Gustavo NEVER decides to maintain a denial. `decisao_nip` is
shipped to the engine as an explicit `None` guardrail by his own `_contract_variables`, this
handler forwards no dossier content, and `MANTER_NEGATIVA` is born exclusively in
`UT_RevisaoJuridicaNip` (worker guard `ERR_NIP_NEGATIVA_NOT_HUMAN`).

PHI / free text (ADR-0006) — WHAT NEVER RIDES THIS SEAM. `payload_meta` is a strict allowlist of
pseudonymized identifiers, bounded enums, an ISO date, a pointer ref and two worker-pre-resolved
booleans. DELIBERATELY EXCLUDED, with the consequence stated:
  * `referencia_negativa_original` — FREE TEXT quoting the original denial. It is the single
    highest-risk field on this seam (unbounded, caller-controlled, beneficiary-adjacent), and his
    own graph docstring already forbids echoing it into class-token fields. It does NOT travel.
  * `tema_nip` — also free text, excluded for exactly the same reason.
  * `documentos_refs` (list of dicts) — `payload_meta` is a strict `Mapping[str, str]`; mirrors
    Carolina's own documented exclusion of her list-shaped `documentos_refs`.
  CONSEQUENCE, recorded rather than hidden: a delegated NIP start seeds `tema_nip=""`,
  `documentos_refs=[]` and no `referencia_negativa_original` into SP-OP-NIP-001. The human
  authoring the response in `UT_ElaborarRespostaNip` reads them from the case, not from this
  envelope — but whoever wires the (owner-gated) origin call site must confirm that is acceptable
  for a delegated START, or carry them over a channel that is not a `Mapping[str, str]`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput
from maezo.a2a.dispatcher import origin_signer_of
from maezo.platform.observability import record_agent_error
from maezo.runtime.metrics import classify_agent_error_type
from maezo.runtime.start_outcome import StartProcessFailedError

from .graph import (
    _CALLER_INPUT_FIELDS,
    PROCESS_KEY_NIP,
    GustavoState,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

    from .graph import FhirReader

# Task type delegated to Gustavo — MUST match `spec/agents/gustavo/agent.yaml`'s
# `a2a.accepted_task_types` (fleet parity fence: `tests/unit/a2a/test_agent_card_handlers_parity.py`).
TASK_TYPE_NIP_INSTRUCT = "nip.instruct"
TASK_TYPES: frozenset[str] = frozenset({TASK_TYPE_NIP_INSTRUCT})

# The delegation ORIGINATES in the NIP worker runtime (`operadora.nip.instruct_dossier`), not in
# an agent graph — the dispatcher validates only the TARGET's Card, so the origin id is a stable,
# self-describing worker identity (audited as `agent_id` on the delegation chain link). Mirrors
# `carolina/delegation.py::ORIGIN_WORKER`.
ORIGIN_WORKER = "nip-worker"
TARGET_AGENT = "gustavo"

#: Gustavo's flow for every `nip.instruct` delegation — set EXPLICITLY (module docstring §J2 ONLY).
_FLOW_NIP = "nip"

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1) —
# same 6h rationale as `agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL`.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# Non-PHI STRING keys forwarded in `payload_meta` (pseudonymized id / bounded enums / ANS
# protocol numbers / the regulatory ISO anchor / a pointer ref). NO free text — see the module
# docstring's PHI note for `referencia_negativa_original` and `tema_nip`.
_STRING_META_KEYS = (
    "numero_nip_ans",
    "protocolo_ans",
    "beneficiario_pseudo_id",
    "classificacao_nip",
    "data_recebimento_nip_iso",
    "patient_ref",
)

# Worker-pre-resolved boolean facts forwarded in `payload_meta` (never PHI, never a decision).
_BOOLEAN_META_KEYS = ("contesta_negativa", "documentacao_suficiente")


def nip_task_id(tenant: str, numero_nip_ans: str) -> str:
    """Idempotent `task_id` == the SP-OP-NIP-001 business key (dispatcher Guard 4).

    Delegates to `gustavo.graph._business_key` (`NIP-{tenant}-{numero_nip_ans}`, contract
    §Business key + the `msg.nip.instruct` correlation key) rather than re-formatting it here,
    so the two derivations cannot drift.
    """
    stub = cast(GustavoState, {"tenant_id": tenant, "fluxo": _FLOW_NIP, "numero_nip_ans": numero_nip_ans})
    return _business_key(stub)


def _build_payload_meta(numero_nip_ans: str, case_meta: dict[str, Any]) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist)."""
    meta: dict[str, str] = {"numero_nip_ans": numero_nip_ans}
    for key in _STRING_META_KEYS:
        if key == "numero_nip_ans":
            continue  # explicit parameter, not case_meta-sourced
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


def build_nip_instruction_envelope(
    *,
    tenant: str,
    numero_nip_ans: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Gustavo root envelope for a NIP instruction request.

    - `payload_ref` is the case's process reference (`process://NIP-...`) — a non-PHI anchor.
    - `case_meta`: the SP-OP-NIP-001 variables available at the origin service task, serialized
      through the strict allowlist above (unknown keys are silently NOT forwarded — allowlist,
      not blocklist).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` and is HMAC-signed over its v2 canonical digest. Absent -> unsigned (dev path).
    """
    task_id = nip_task_id(tenant, numero_nip_ans)
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_NIP_INSTRUCT,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(numero_nip_ans, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


async def delegate_nip_instruction(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    numero_nip_ans: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Gustavo delegation. Idempotent by `task_id`.

    NOT CALLED from `tools/workers/nip.py` — see the module docstring's STOP boundary. Returns the
    dispatcher's structured `DelegationResult` (never a raise out of the dispatcher for a TERMINAL
    handler failure). On re-delivery of the same `task_id`, `idempotent_replay=True` and the
    handler does NOT run again.

    A regra REAL, e ela nao e' uma ressalva unica: SO' as classes TERMINAIS (`ValueError`,
    `TypeError`, `KeyError` E SUBCLASSES, decididas por `isinstance` em
    `a2a/dispatcher.py::_TERMINAL_HANDLER_ERROR_CLASSES`) voltam como rejeicao estruturada e
    selada. TODA OUTRA excecao do handler PROPAGA sem selar, para que a entrega continue
    retentavel — e isso inclui DUAS coisas diferentes: o canal TRANSITORIO RAF-02
    (`StartProcessFailedError` de um start recusado pelo engine, `AuditPersistenceError`), que
    propaga de proposito, E qualquer bug NAO CLASSIFICADO do grafo (`AttributeError`,
    `IndexError`, ...), que propaga porque selar um bug desconhecido seria pior. Nenhuma das duas
    e' silenciosa: o dispatcher grava a linha de audit NAO-terminal `PROPAGATED` e conta em
    `maezo_a2a_handler_error_total` antes de relevantar
    (`a2a/dispatcher.py::DelegationDispatcher._trace_propagated_handler_error`).

    SIGNING (ADR-0039 §4.4): `signer` defaults to the edge signer carried by `dispatcher`
    (`origin_signer_of`), so a future live worker call site signs without per-worker wiring.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_nip_instruction_envelope(
        tenant=tenant,
        numero_nip_ans=numero_nip_ans,
        case_meta=case_meta,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors carolina/fernando delegation.py) --------------------------------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def state_from_envelope(envelope: DelegationEnvelope) -> GustavoState:
    """Materialize Gustavo's initial graph state from a delegation envelope.

    Sets `fluxo="nip"` and `origem_a2a=True` EXPLICITLY (never `payload_meta`-sourced): the first
    because the task type IS the flow selector on this seam (module docstring §J2 ONLY), the
    second because it is the contract's own definition of the variable ("`true` quando a
    instancia nasce de delegacao A2A `nip.instruct`") — a delegated turn asserting `false` there
    would be lying about its own provenance. `canal` is ALWAYS `"a2a"`; `tenant` comes from the
    envelope (ADR-0004).

    Enforces his input boundary (`graph._CALLER_INPUT_FIELDS`) by ALLOWLIST-BY-CONSTRUCTION: an
    unknown/output-only `payload_meta` key is silently DROPPED (never read, never copied). The
    `unknown` check below is a structural regression guard, identical to Carolina's (REG-06:
    exercised for real by a monkeypatch test, not `# pragma: no cover`); `receive` re-sanitizes
    every output-only field on top of it as defense in depth.

    Fails closed on a missing `numero_nip_ans`: his graph derives the idempotent business key —
    and the contract's `msg.nip.instruct` correlation key — from it. `receive` would fail-safe to
    the juridical human route with a class token, but a blank NIP number here is a producer bug
    and must be loud at the seam.
    """
    meta = dict(envelope.payload_meta)
    numero_nip_ans = str(meta.get("numero_nip_ans", "")).strip()
    if not numero_nip_ans:
        raise ValueError(
            "nip.instruct envelope has no numero_nip_ans in payload_meta — the NIP business key "
            "(and the msg.nip.instruct correlation key) cannot be derived (producer bug; the "
            "originating worker validates identifiers before delegating)"
        )
    raw: dict[str, Any] = {
        "tenant_id": envelope.tenant,
        "fluxo": _FLOW_NIP,
        "canal": "a2a",
        "numero_nip_ans": numero_nip_ans,
        "origem_a2a": True,
    }
    for key in _STRING_META_KEYS:
        if key == "numero_nip_ans":
            continue
        if meta.get(key):
            raw[key] = meta[key]
    for key in _BOOLEAN_META_KEYS:
        if key in meta:
            raw[key] = _as_bool(meta[key])

    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:  # REG-06: exercised for real, see test_gustavo_delegation.py's monkeypatch fence.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Gustavo: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast(GustavoState, raw)


def make_gustavo_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: FhirReader | None = None,
    agent_version: str = "gustavo@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Gustavo's A2A handler (delegation target).

    NOT REGISTERED with any dispatcher (module docstring): `a2a_composition` still wires only
    rafael / carolina+andre+fernando, and adding `"gustavo"` is the owner decision this work
    package stops in front of.

    Compiles the REAL Gustavo graph via `gustavo.graph.build(config)` — the same fail-closed
    contract every other caller goes through (`inference`/`dmn`/`cibseven`/`audit_sink` REQUIRED,
    `fhir` optional) — and invokes it with the envelope-materialized state.

    `output_ref` is SP-OP-NIP-001's business key reference (auditable, never PHI). `meta` carries
    ONLY bounded routing class tokens — the instruction dossier CONTENT stays in Gustavo's own
    engine variables (`dossie_gustavo`, via his idempotent `start_process`) and is never forwarded
    over this seam, mirroring Carolina's guardrail and his own L0-hard invariant that only
    `UT_RevisaoJuridicaNip`'s human may maintain a denial.
    """
    compiled = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "audit_sink": audit_sink,
            "fhir": fhir,
            "agent_version": agent_version,
        }
    ).compile()

    async def handler(envelope: DelegationEnvelope) -> HandlerOutput:
        state = state_from_envelope(envelope)
        try:
            result: dict[str, Any] = await compiled.ainvoke(state)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a (mirrors carolina/delegation.py's handler exactly — see its
            # comment for the full rationale and the fence that pins this shape,
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`).
            record_agent_error(agent="gustavo", error_type=classify_agent_error_type(exc))
            raise
        business_key = result.get("business_key") or _business_key(state)
        if result.get("start_failed") is True:
            # RAF-02: o grafo TENTOU abrir o processo e o engine recusou. Devolver
            # `HandlerOutput` aqui seria um sucesso para o dispatcher (`HandlerOutput` nao tem
            # campo `success`): ele gravaria o audit terminal `_DECISION_COMPLETED`, emitiria o
            # fato COMPLETED e SELARIA o resultado por `task_id` — tornando o falso sucesso
            # irretentavel. A excecao tipada propaga, entao nada disso acontece e a reentrega do
            # mesmo `task_id` reexecuta o handler. So tokens de classe na mensagem, nunca PHI.
            raise StartProcessFailedError(
                f"gustavo nao conseguiu iniciar {PROCESS_KEY_NIP} "
                f"(business_key={business_key!r}): o turno NAO foi concluido"
            )
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "route": str(result.get("route") or ""),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "grupo_destino": str(result.get("grupo_humano") or ""),
                "classificacao": str(result.get("classificacao") or ""),
                "process_started": str(result.get("process_started", False)),
            },
        )

    return handler
