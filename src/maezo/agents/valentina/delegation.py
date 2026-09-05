"""Valentina as A2A delegation TARGET (`care.stratify` / `care.enroll`) — VAL-01's target half.

`spec/agents/valentina/agent.yaml` declares `accepted_task_types: [care.stratify, care.enroll]`
and — until this module landed — its comment ASSERTED that "o inbound handler
`make_valentina_handler` (delegation.py, R9) ja o consome", which was FALSE: no `delegation.py`
existed for her at all (VAL-01). `docs/processes/contracts/SP-OP-PROGRAMA-001.md` names both
origin service tasks as "consome (worker->A2A)": `operadora.programa.stratify_risk`
("delegacao `care.stratify` a Valentina — instrui, nao decide") and
`operadora.programa.build_care_plan` ("delegacao `care.enroll` a Valentina — sem decidir alta").
This module is the TARGET half, mirroring `agents/carolina/delegation.py` with
`agents/andre/delegation.py`'s multi-flow disambiguation on top.

WHAT IS AND IS NOT BUILT HERE:
- TARGET side (`state_from_envelope` + `make_valentina_handler`) — BUILT. Compiles Valentina's
  REAL graph (`agents.valentina.graph.build(config)`, fail-closed on missing `inference`/`dmn`/
  `cibseven`/`audit_sink`) and runs it against envelope-materialized state.
- ORIGIN builders (`build_care_envelope` / `delegate_care_task`) — BUILT and unit-proven in
  isolation, **NOT CALLED** from `tools/workers/programa.py` (outside this work package's
  editable surface; that worker's `stratify_risk` is a disclosed STUB awaiting exactly this
  delegation). The call site is an OWNER DECISION (gap `FERNANDO-DELEGATION-CALL-SITE`).
- REGISTRATION in `runtime.agent_runtime.a2a_composition` — **NOT DONE** (same owner decision).
  Nothing routes a `care.stratify`/`care.enroll` envelope here today.

Idempotency (Guard 4): `task_id` IS the SP-OP-PROGRAMA-001 business key
(`PROG-{tenant}-{programa}-{beneficiario}-{ciclo}` — one active instance per
programa x beneficiario x ciclo), delegated to `valentina.graph._business_key` itself so the two
derivations cannot drift. BOTH task types of one cycle therefore share ONE `task_id`, which is
deliberate and matches the contract: a `care.enroll` after a `care.stratify` on the same cycle is
the SAME case, and the dispatcher's Guard-4 replay is the correct behavior for a re-delivery of
either external task. (A caller that needs the two hops to run as distinct units must carry a
per-hop key, which the contract does not define — recorded here rather than invented.)

CONSENT IS NOT DECIDED HERE (LGPD, ADR-0006 / contract §CHOKEPOINT). The consent VERDICT is
computed by `graph.consent_gate` from the pre-resolved FACTS, and the binding chokepoint is the
ENGINE worker `operadora.programa.check_consent` (`ERR_PROGRAMA_NO_CONSENT`, fail-closed) —
neither is this handler. This seam only RELAYS the worker-resolved facts
(`consentimento_ativo`/`consent_checked`/`consent_revoked`/`consent_scope`/`consent_event_ref`),
exactly as `_CALLER_INPUT_FIELDS` classifies them (facts = input, verdict = output). Two
properties make that safe rather than a bypass:
  1. `consent_status` (the verdict) is an OUTPUT-only field and is NEVER seeded from an envelope —
     `new`-style construction here cannot place it, `receive` resets it, and `consent_gate`
     recomputes it. A caller can assert facts; it can never assert the verdict.
  2. `consent_gate` demands the STRICTEST signal (`consentimento_ativo is True` AND
     `consent_checked is True`, exact booleans, plus a matching `consent_scope`), so a
     `payload_meta` string is normalized to a real `bool` here or the gate fail-closes to
     `ausente` — no PHI, neutral terminal.
On a SIGNED envelope (ADR-0039) the consent facts are inside `payload_meta_hash`, so a tampered
`consentimento_ativo=true` invalidates the signature at the dispatcher before this module runs.

L0 HARD (contract §Invariante, `false_denial_rate == 0`): Valentina NEVER discharges, never
denies care. `Route` has no adverse variant, `decisao_programa`/`motivo_desligamento_clinico`/
`referencia_clinica`/`responsavel_clinico_id` ship to the engine as explicit `None` guardrails,
and this handler forwards NO dossier content — only bounded routing class tokens.

INPUT-BOUNDARY GATE: Valentina's graph has no `new_valentina_state` constructor (like Carolina's
and Fernando's); her gate is `graph._CALLER_INPUT_FIELDS` + `receive`'s output-field reset.
`state_from_envelope` enforces it by ALLOWLIST-BY-CONSTRUCTION — `raw` only ever copies named
keys off the `_..._META_KEYS` tuples below, so an out-of-allowlist `payload_meta` entry is never
read at all. The `unknown` check at the end is the same structural regression guard Carolina's
carries; note in particular that `consent_status` is absent from every tuple BY CONSTRUCTION.

PHI (ADR-0006, Zona PHI) — WHAT NEVER RIDES THIS SEAM. `payload_meta` carries pseudonymized
identifiers, bounded enums (`gatilho`, the risk BAND `risco_estratificado`), pointer refs and
worker-pre-resolved booleans. There is NO free text and NO clinical CONTENT: the risk band is a
class token, never a diagnosis; `patient_summary_ref` is a pointer, and the summary itself is
read IN-ZONE by `gather`, after the consent gate, never over A2A.
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
    PROCESS_KEY_PROGRAMA,
    Task,
    ValentinaState,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

    from .graph import PatientSummaryReader

# Task types delegated to Valentina — MUST match `spec/agents/valentina/agent.yaml`'s
# `a2a.accepted_task_types` (fleet parity fence: `tests/unit/a2a/test_agent_card_handlers_parity.py`).
TASK_TYPE_CARE_STRATIFY = "care.stratify"
TASK_TYPE_CARE_ENROLL = "care.enroll"
TASK_TYPES: frozenset[str] = frozenset({TASK_TYPE_CARE_STRATIFY, TASK_TYPE_CARE_ENROLL})

# The delegation ORIGINATES in the programa worker runtime (`operadora.programa.stratify_risk` /
# `operadora.programa.build_care_plan`), from INSIDE a running SP-OP-PROGRAMA-001 instance and
# AFTER the engine's own consent chokepoint — never in an agent graph. The dispatcher validates
# only the TARGET's Card, so the origin id is a stable, self-describing worker identity (audited
# as `agent_id` on the delegation chain link). Mirrors `carolina/delegation.py::ORIGIN_WORKER`.
ORIGIN_WORKER = "programa-worker"
TARGET_AGENT = "valentina"

#: task_type -> Valentina's graph `task` (which dossier she assembles). Her two task types are
#: DISJOINT, so the task type itself is the selector — never her graph's missing-key default.
_TASK_TYPE_TO_TASK: dict[str, Task] = {
    TASK_TYPE_CARE_STRATIFY: "stratify",
    TASK_TYPE_CARE_ENROLL: "enroll",
}

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1) —
# same 6h rationale as `agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL`.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# Non-PHI STRING keys forwarded in `payload_meta` (pseudonymized ids / bounded enums / the risk
# BAND / pointer refs). No free text, no clinical content — see the module docstring's PHI note.
_STRING_META_KEYS = (
    "programa_id",
    "beneficiario_pseudo_id",
    "ciclo",
    "gatilho",
    "proactive_trigger_ref",
    "consent_scope",
    "consent_event_ref",
    "risco_estratificado",
    "patient_summary_ref",
)

# Worker-pre-resolved boolean facts forwarded in `payload_meta`. The three consent FACTS are here
# on purpose (module docstring §CONSENT IS NOT DECIDED HERE); the consent VERDICT
# (`consent_status`) is structurally absent from every tuple in this module.
_BOOLEAN_META_KEYS = (
    "consentimento_ativo",
    "consent_checked",
    "consent_revoked",
    "elegibilidade_criterios_atendidos",
    "criterio_alta_aparente",
)


def _task_for_task_type(task_type: str) -> Task:
    """`task_type` -> Valentina's graph `task`. Fail-closed on anything else.

    The dispatcher already refuses a task type outside her Card's `accepted_task_types`
    (`AgentCard.accepts`), so this raise is the SECOND fence: a direct/handler-level caller must
    never fall through to her graph's own missing-key default (`_task` -> `stratify`) and assemble
    the WRONG dossier under a `care.enroll` audit trail.
    """
    task = _TASK_TYPE_TO_TASK.get(task_type)
    if task is None:
        raise ValueError(
            f"task_type {task_type!r} is not one of Valentina's accepted_task_types "
            f"({sorted(TASK_TYPES)}) — the dossier to assemble cannot be derived; a delegation "
            "with an unknown task type is a producer bug"
        )
    return task


def care_task_id(tenant: str, *, programa_id: str, beneficiario_pseudo_id: str, ciclo: str) -> str:
    """Idempotent `task_id` == the SP-OP-PROGRAMA-001 business key (dispatcher Guard 4).

    Delegates to `valentina.graph._business_key` (`PROG-{tenant}-{programa}-{benef}-{ciclo}`,
    contract §Business key) rather than re-formatting it here, so the two cannot drift.
    """
    stub = cast(
        ValentinaState,
        {
            "tenant_id": tenant,
            "programa_id": programa_id,
            "beneficiario_pseudo_id": beneficiario_pseudo_id,
            "ciclo": ciclo,
        },
    )
    return _business_key(stub)


def _build_payload_meta(
    programa_id: str, beneficiario_pseudo_id: str, ciclo: str, case_meta: dict[str, Any]
) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist)."""
    meta: dict[str, str] = {
        "programa_id": programa_id,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "ciclo": ciclo,
    }
    for key in _STRING_META_KEYS:
        if key in meta:
            continue  # explicit params, not case_meta-sourced
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


def build_care_envelope(
    *,
    tenant: str,
    task_type: str,
    programa_id: str,
    beneficiario_pseudo_id: str,
    ciclo: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Valentina root envelope for a stratification / care-plan request.

    - `payload_ref` is the case's process reference (`process://PROG-...`) — a non-PHI anchor.
    - `case_meta`: the SP-OP-PROGRAMA-001 variables available AFTER the engine's consent
      chokepoint, serialized through the strict allowlist above (unknown keys are silently NOT
      forwarded — allowlist, not blocklist).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` and is HMAC-signed over its v2 canonical digest — which is what binds the CONSENT
    FACTS in `payload_meta_hash` (module docstring). Absent -> unsigned (dev path).
    """
    _task_for_task_type(task_type)  # fail closed before minting a key for an unroutable type
    task_id = care_task_id(
        tenant,
        programa_id=programa_id,
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        ciclo=ciclo,
    )
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=task_type,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(programa_id, beneficiario_pseudo_id, ciclo, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


async def delegate_care_task(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    task_type: str,
    programa_id: str,
    beneficiario_pseudo_id: str,
    ciclo: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Valentina delegation. Idempotent by `task_id`.

    NOT CALLED from `tools/workers/programa.py` — see the module docstring's STOP boundary.
    Returns the dispatcher's structured `DelegationResult` (never a raise out of the dispatcher
    for a TERMINAL handler failure). On re-delivery of the same `task_id`,
    `idempotent_replay=True` and the handler does NOT run again.

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
    envelope = build_care_envelope(
        tenant=tenant,
        task_type=task_type,
        programa_id=programa_id,
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        ciclo=ciclo,
        case_meta=case_meta,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors carolina/delegation.py + andre's task disambiguation) -----------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to a REAL
    `bool`. Load-bearing for consent: `consent_gate` demands `is True` (exact booleans; a string
    `"true"` is AMBIGUOUS and therefore NO consent), so the normalization has to happen here or
    the gate fail-closes to `ausente`."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def state_from_envelope(envelope: DelegationEnvelope) -> ValentinaState:
    """Materialize Valentina's initial graph state from a delegation envelope.

    Sets `task` EXPLICITLY from the task type (`_task_for_task_type`) — never omitted, so her
    graph's own missing-key default (`graph._task` -> `stratify`) can never decide which dossier a
    `care.enroll` delegation assembles. `canal` is ALWAYS `"a2a"`; `tenant` comes from the
    envelope (ADR-0004).

    The CONSENT FACTS are relayed, the consent VERDICT is not (module docstring §CONSENT IS NOT
    DECIDED HERE): `consent_status` is structurally absent from `_STRING_META_KEYS`/
    `_BOOLEAN_META_KEYS`, so it cannot be seeded from an envelope even by a malicious producer;
    `receive` resets it and `consent_gate` computes it.

    Enforces her input boundary (`graph._CALLER_INPUT_FIELDS`) by ALLOWLIST-BY-CONSTRUCTION: an
    unknown/output-only `payload_meta` key is silently DROPPED (never read, never copied). The
    `unknown` check below is a structural, currently-unreachable regression guard, identical to
    Carolina's.

    Fails closed on a missing identity: her business key is
    `PROG-{tenant}-{programa}-{benef}-{ciclo}` and `receive` refuses without all three — which
    fail-closes THROUGH the consent gate to the neutral `no_consent` terminal. That is safe, but
    it is also indistinguishable from a genuine consent absence, so a producer bug must be loud
    HERE rather than be recorded downstream as "sem consentimento".
    """
    task = _task_for_task_type(envelope.task_type)
    meta = dict(envelope.payload_meta)
    missing = [
        key
        for key in ("programa_id", "beneficiario_pseudo_id", "ciclo")
        if not str(meta.get(key, "")).strip()
    ]
    if missing:
        raise ValueError(
            f"{envelope.task_type} envelope is missing {missing} in payload_meta — the PROGRAMA "
            "business key cannot be derived, and the graph would fail-closed to the neutral "
            "no-consent terminal, which must never be how a producer bug surfaces (the "
            "originating worker validates identifiers before delegating)"
        )
    raw: dict[str, Any] = {"task": task, "tenant_id": envelope.tenant, "canal": "a2a"}
    for key in _STRING_META_KEYS:
        if meta.get(key):
            raw[key] = meta[key]
    for key in _BOOLEAN_META_KEYS:
        if key in meta:
            raw[key] = _as_bool(meta[key])

    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:  # pragma: no cover - structural guard; raw is built from the allowlists above.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Valentina: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast(ValentinaState, raw)


def make_valentina_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: PatientSummaryReader | None = None,
    agent_version: str = "valentina@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Valentina's A2A handler (delegation target).

    NOT REGISTERED with any dispatcher (module docstring): `a2a_composition` still wires only
    rafael / carolina+andre+fernando, and adding `"valentina"` is the owner decision this work
    package stops in front of. `spec/agents/valentina/agent.yaml`'s comment claimed this factory
    already existed and was already consumed; with this module it EXISTS — the consumption half is
    still absent, and the yaml comment was corrected to say exactly that.

    Compiles the REAL Valentina graph via `valentina.graph.build(config)` — the same fail-closed
    contract every other caller goes through (`inference`/`dmn`/`cibseven`/`audit_sink` REQUIRED,
    `fhir` optional) — and invokes it with the envelope-materialized state.

    `output_ref` is SP-OP-PROGRAMA-001's business key reference (auditable, never PHI). `meta`
    carries ONLY bounded routing class tokens — the care-plan/stratification dossier CONTENT
    stays in Valentina's own engine variables (`dossie_valentina`, via her idempotent
    `start_process`) and is never forwarded over this seam. `consent_status` IS reported (it is
    a bounded LGPD class token and the caller needs to know the turn produced no PHI), never
    `summary_facts` and never a clinical fact.
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
            record_agent_error(agent="valentina", error_type=classify_agent_error_type(exc))
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
                f"valentina nao conseguiu iniciar {PROCESS_KEY_PROGRAMA} "
                f"(business_key={business_key!r}): o turno NAO foi concluido"
            )
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "task": str(result.get("task") or state.get("task", "")),
                "route": str(result.get("route") or "human_review"),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "grupo_destino": str(result.get("grupo_humano") or ""),
                # Bounded LGPD class token — the VERDICT this turn computed, never a caller's.
                "consent_status": str(result.get("consent_status") or ""),
                "process_started": str(result.get("process_started", False)),
            },
        )

    return handler
