"""Shared machinery for the seven gated seams: `SeamContext`, `gate`, the denial-shape table.

WHY A SHARED BASE AND NOT SEVEN COPIES. Design §5.4 specifies ONE shape for all seven wrappers.
Duplicating it seven times would mean seven chances for a wrapper to drift into deciding without
logging, logging without deciding, or improvising a refusal — the three defects §5.4 and A-12
exist to prevent. Everything a wrapper does per call lives in :func:`gate`; a wrapper's own body
is one `await gate(...)` line plus the delegation, and a test asserts that shape structurally.

THE PRINCIPAL IS CLOSURE-BOUND (adversary A-8). :class:`SeamContext` carries `(tenant, principal,
phi_zone, DecisionContext)` and is built ONCE per (tenant, principal) by
`maezo.gateway.tool_registry`. No public method of any gated wrapper accepts a `tenant`,
`principal` or `agent_id` argument — asserted by a fence test, because a wrapper that trusts a
caller-supplied principal gates nothing. The technique is `_ScopedWhatsAppSender`'s
(`platform/webhooks/whatsapp/dispatch.py:100-118`), which binds a recipient to exactly one turn.

FAIL-CLOSED AT COMPOSITION (I-2). `SeamContext.__post_init__` validates `tenant`/`principal`/
`phi_zone` through the SAME `EffectCall` constructor the per-call path uses — one vocabulary, not
two. A malformed identity raises `EffectCallError` at BUILD time, where every composition root
already isolates failures into a red readiness check, so the failure mode is "replica not ready",
never "replica ready and ungated".

THE DENIAL SHAPE IS AN EXCEPTION TYPE, CHOSEN SO IT LANDS ON THE NODE'S DECLARED PATH. Design
§6.1 states each class's denial-mutation proof shape in behavioural terms ("the node takes its
declared DMN-unavailable path", "degrades to the disclosed gap note"). Re-derived against the
tree, those behaviours are reached through the seam's OWN error type:

  * `_evaluate_dmn` catches `(DmnEvaluationError, DmnNoResultError)` ONLY — `rafael/graph.py:549`,
    `helena/graph.py:761`, `marina/graph.py:807`. A plain `RuntimeError` would escape the node and
    crash the turn, which is adversary A-12 (the PEP harming the patient) in its purest form. So
    `ROTA_DMN_INDISPONIVEL` raises a `DmnEvaluationError` SUBCLASS.
  * Every agent's `start_process` node catches `CibSevenError` (`rafael:517`, `helena:681`,
    `marina:712`, `lucas:647`, `fernando:568`, `gustavo:705`, `carolina:634`, `valentina:656`,
    `andre:994`). So `LEITURA_INCONCLUSIVA` and `INCIDENTE_FALHA_FECHADA` raise a `CibSevenError`
    SUBCLASS — and, for the anti-dupla-terminação query, RAISING (never returning `None`) is the
    whole point: a denied read must not be readable as "no active instance".
  * The FHIR, WhatsApp, LLM, population and A2A call sites all catch bare `Exception`
    (`rafael:349,:356`, `helena:713`, `andre:716,:729`, every `_llm.generate` site, and the
    dossier workers' "ANY delegation failure" branch — `credenciamento.py:580-601`), so
    `LACUNA_DECLARADA` / `ESCALONAMENTO_HUMANO` / `ROTA_LLM_INDISPONIVEL` /
    `DEGRADACAO_SEM_DOSSIE` need no seam-specific base and stay plain :class:`EffectDeniedError`.

The two transport error classes are imported BRANCH-LOCALLY, inside the factory that needs them
(`maezo/tools/mcp_cibseven/transport.py` itself imports `maezo.gateway.audit`, so a module-scope
import here would be a package-level cycle waiting to happen, and the policy core must stay free
of `maezo.tools` — `action_execution.py:206-208`). The subclasses are built once and cached.

NO PHI IN A DENIAL (I-3). `EffectDeniedError.__str__` is assembled from bounded tokens only —
operation, action class, layer, reason, denial shape. The arguments that triggered the call are
never touched, so a denial that a node folds into a gap note (`f"... indisponivel: {exc}"`,
`rafael/graph.py:350`) cannot carry a patient id into graph state.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import structlog

from maezo.gateway import effect_classes
from maezo.gateway.effect_classes import (
    SHAPE_DEGRADACAO_SEM_DOSSIE,
    SHAPE_ESCALONAMENTO_HUMANO,
    SHAPE_INCIDENTE_FALHA_FECHADA,
    SHAPE_LACUNA_DECLARADA,
    SHAPE_LEITURA_INCONCLUSIVA,
    SHAPE_ROTA_DMN_INDISPONIVEL,
    SHAPE_ROTA_LLM_INDISPONIVEL,
    agent_action_ref,
)
from maezo.gateway.effect_pep import (
    PHI_ZONE_GENERAL,
    DecisionContext,
    EffectCall,
    EffectDecision,
    decide_effect,
    log_effect_decision,
)

logger = structlog.get_logger(__name__)

#: The keys of `build_agent_seams`'s result that carry an EFFECT and must therefore be gated
#: instances. Deliberately NOT "every key": the dict also carries `audit_sink` (a durable sink,
#: not an effect seam — it is the thing effects are recorded TO) and `agent_version` (a string).
#: The `effect_seams_gated` readiness check at each composition root iterates exactly this set.
EFFECT_SEAM_KEYS: Final[frozenset[str]] = frozenset(
    {"dmn", "cibseven", "fhir", "whatsapp", "inference", "population", "a2a"}
)

#: Operation token used ONLY to validate a `SeamContext`'s closure-bound identity through the real
#: `EffectCall` constructor. It is deliberately NOT in the catalogue and never reaches `decide`.
_IDENTITY_PROBE_OPERATION: Final[str] = "seam.identity_probe"


class PreEffectAuditHook(Protocol):
    """The `audita_antes` seam (design §5.4 / I-1). INERT today — see :func:`_pre_effect_audit`."""

    async def __call__(
        self,
        *,
        tenant: str,
        principal: str,
        operation: str,
        action_class: str,
        decision_reason: str,
    ) -> None: ...


class EffectDeniedError(RuntimeError):
    """An ENFORCED deny at the effect chokepoint. Carries bounded tokens only (I-3).

    Subclassed per denial shape so the refusal lands on the node's DECLARED path (module
    docstring). `decision` is attached for the tests and for a caller that wants the layer.
    """

    def __init__(self, decision: EffectDecision) -> None:
        super().__init__(
            "efeito negado pelo chokepoint de efeitos: "
            f"operation={decision.operation} classe={decision.action_class} "
            f"camada={decision.layer} motivo={decision.reason} forma={decision.denial_shape}"
        )
        self.decision = decision


#: Cache for the two shape-specific subclasses, which can only be built after a branch-local
#: import of the transport module that owns their second base.
_DENIAL_TYPES: dict[str, type[EffectDeniedError]] = {}


def _dmn_denial_type() -> type[EffectDeniedError]:
    """`EffectDeniedError` that a graph's `except (DmnEvaluationError, DmnNoResultError)` catches."""
    cached = _DENIAL_TYPES.get(SHAPE_ROTA_DMN_INDISPONIVEL)
    if cached is None:
        from maezo.tools.workers.dmn_transport import DmnEvaluationError

        cached = type("DmnEffectDeniedError", (EffectDeniedError, DmnEvaluationError), {})
        _DENIAL_TYPES[SHAPE_ROTA_DMN_INDISPONIVEL] = cached
    return cached


def _cibseven_denial_type() -> type[EffectDeniedError]:
    """`EffectDeniedError` that a graph's `except CibSevenError` catches."""
    cached = _DENIAL_TYPES.get(SHAPE_LEITURA_INCONCLUSIVA)
    if cached is None:
        from maezo.tools.mcp_cibseven.transport import CibSevenError

        cached = type("CibSevenEffectDeniedError", (EffectDeniedError, CibSevenError), {})
        _DENIAL_TYPES[SHAPE_LEITURA_INCONCLUSIVA] = cached
    return cached


#: The CLOSED shape -> exception-factory table (design §6.1's "Denial-mutation proof shape"
#: column). A shape absent from this table is a fail-closed condition, not a formatting choice:
#: :func:`denial_for` raises the base `EffectDeniedError`, which still refuses the effect, and a test
#: asserts the table covers every member of `effect_classes.DENIAL_SHAPES` so the gap can never
#: appear silently.
_DENIAL_FACTORIES: Final[dict[str, Any]] = {
    SHAPE_ROTA_DMN_INDISPONIVEL: _dmn_denial_type,
    SHAPE_LEITURA_INCONCLUSIVA: _cibseven_denial_type,
    SHAPE_INCIDENTE_FALHA_FECHADA: _cibseven_denial_type,
    SHAPE_ESCALONAMENTO_HUMANO: lambda: EffectDeniedError,
    SHAPE_LACUNA_DECLARADA: lambda: EffectDeniedError,
    SHAPE_ROTA_LLM_INDISPONIVEL: lambda: EffectDeniedError,
    SHAPE_DEGRADACAO_SEM_DOSSIE: lambda: EffectDeniedError,
}


def denial_for(decision: EffectDecision) -> EffectDeniedError:
    """Build the exception for `decision`'s DECLARED denial shape. Never improvises (§5.4).

    An unknown/absent shape means the catalogue and the manifest disagree — fail-closed: the base
    `EffectDeniedError` still refuses, and the mismatch is logged loudly rather than papered over.
    """
    factory = _DENIAL_FACTORIES.get(decision.denial_shape or "")
    if factory is None:
        logger.error(
            "effect_seam_denial_shape_unknown",
            operation=decision.operation,
            action_class=decision.action_class,
            denial_shape=decision.denial_shape,
        )
        return EffectDeniedError(decision)
    denial: EffectDeniedError = factory()(decision)
    return denial


@dataclass(frozen=True, slots=True)
class SeamContext:
    """Everything a gated wrapper closes over. Built ONCE per (tenant, principal) — never per call.

    Attributes:
        tenant: bounded token (I-8). Mandatory.
        principal: bounded token — the agent id, or `worker_runtime` for the worker daemon's
            engine/DMN seams. NEVER a call argument (A-8).
        phi_zone: the principal's ADR-0006 zone, from `agent.yaml`'s `security_zone`. Carried for
            telemetry and the C2 operation split; it gates nothing here — `runtime/inference.py`'s
            `PhiZoneRoutingError` stays the INDEPENDENT enforcement (I-6).
        decision: the injected decision seams (`DecisionContext`) `decide` may consult.
        pre_effect_audit: the `audita_antes` sink. `None` today for every root, and INERT because
            no class declares `audita_antes` (Q-9).

    Raises:
        EffectCallError: at CONSTRUCTION, if `tenant`/`principal`/`phi_zone` are not bounded
            non-PHI values. Composition time is the right place for that to be loud (I-2).
    """

    tenant: str
    principal: str
    phi_zone: str = PHI_ZONE_GENERAL
    decision: DecisionContext = field(default_factory=DecisionContext)
    pre_effect_audit: PreEffectAuditHook | None = None

    def __post_init__(self) -> None:
        # Validated through the REAL value object, so there is exactly one bounded-token
        # vocabulary in the chokepoint rather than a second, drifting copy here.
        EffectCall(
            tenant=self.tenant,
            principal=self.principal,
            operation=_IDENTITY_PROBE_OPERATION,
            action_ref=agent_action_ref(_IDENTITY_PROBE_OPERATION),
            phi_zone=self.phi_zone,
        )


class GatedSeam:
    """Marker base for every gated wrapper — what `effect_seams_gated` and the fence test look for.

    Deliberately a BASE CLASS and not a duck-typed attribute: the boot assertion (§5.5) must be
    able to answer "is this object gated?" about something a monkeypatch, a plugin or a
    config-driven factory produced, and an attribute is trivially forged by an object that gates
    nothing. `isinstance` against a class this package owns is not.
    """

    __slots__ = ("_inner", "_seam")

    def __init__(self, inner: Any, *, seam: SeamContext) -> None:
        self._inner = inner
        self._seam = seam

    @property
    def inner(self) -> Any:
        """The wrapped seam. Exposed for the parity proofs and the registry's own assertions."""
        return self._inner

    @property
    def seam_context(self) -> SeamContext:
        """The closure-bound `(tenant, principal, …)` this wrapper decides under."""
        return self._seam


def is_gated_seam(candidate: object) -> bool:
    """True iff `candidate` is one of this package's gated wrappers (the boot assertion, §5.5)."""
    return isinstance(candidate, GatedSeam)


def _emit(
    seam: SeamContext,
    decision: EffectDecision,
    operation: str,
    process_key: str | None,
    value_cents: int | None,
) -> None:
    """Emit the ONE shadow line for this call. Never raises onto the effect path.

    The `EffectCall` is rebuilt here for the log rather than returned by `decide_effect`, and the
    rebuild is guarded in a specific, deterministic way: `tenant`/`principal`/`phi_zone` were
    validated at `SeamContext` construction and `operation` comes from the closed catalogue, so
    the ONLY fields that can make construction fail are the two PER-CALL ones. Dropping them and
    retrying therefore always succeeds — and the shadow line is emitted even for the malformed
    call that `decide_effect` already turned into a bounded `CHAMADA_INVALIDA` DENY. A call that
    decides without recording would be invisible to the §9.2 evidence packet, which is the one
    thing Phase 0 exists to produce.
    """
    action_ref = agent_action_ref(operation)
    try:
        call = EffectCall(
            tenant=seam.tenant,
            principal=seam.principal,
            operation=operation,
            action_ref=action_ref,
            process_key=process_key,
            value_cents=value_cents,
            phi_zone=seam.phi_zone,
        )
    except Exception:  # noqa: BLE001 - only the per-call fields can fail; drop them and still log
        try:
            call = EffectCall(
                tenant=seam.tenant,
                principal=seam.principal,
                operation=operation,
                action_ref=action_ref,
                phi_zone=seam.phi_zone,
            )
        except Exception:  # noqa: BLE001 - nothing may escape onto a care path
            logger.error("effect_seam_telemetry_unbuildable", operation=operation, exc_info=True)
            return
    with contextlib.suppress(Exception):
        log_effect_decision(decision, call)


async def _pre_effect_audit(seam: SeamContext, decision: EffectDecision, operation: str) -> None:
    """The `audita_antes` record, emitted BEFORE the inner seam is called (I-1).

    INERT TODAY, and provably so: every entry in `effect_classes.ACTION_CLASSES` declares
    `audita_antes=False` (Q-9 — a durable write in front of an engine-path call needs the SRE
    latency sign-off design I-9 demands), so this function returns at its first branch for every
    real call. That inertness is what keeps the parity proofs honest: a wrapper that added a
    durable write would not be behaviourally invisible.

    When a class IS flagged, the ordering mirrors `transport.py:1148-1157` — the record is
    written before the effect, and a write failure means NO EFFECT MAY HAPPEN. Under `enforced`
    that is a denial in the class's declared shape. Under `shadow` it is an error LINE and
    nothing else: shadow must never change behaviour, or the whole ramp loses its meaning.
    """
    spec = effect_classes.lookup_class(decision.action_class)
    if spec is None or not spec.audita_antes:
        return
    hook = seam.pre_effect_audit
    failure: Exception | None = None
    if hook is None:
        failure = RuntimeError(
            f"class {decision.action_class!r} declares audita_antes but no pre-effect audit sink "
            "was wired at composition"
        )
    else:
        try:
            await hook(
                tenant=seam.tenant,
                principal=seam.principal,
                operation=operation,
                action_class=decision.action_class or "",
                decision_reason=decision.reason,
            )
        except Exception as exc:  # noqa: BLE001 - I-1: no effect without a preceding record
            failure = exc
    if failure is None:
        return
    logger.error(
        "effect_seam_pre_effect_audit_failed",
        operation=operation,
        action_class=decision.action_class,
        enforced=decision.enforced,
        error=type(failure).__name__,
    )
    if decision.enforced:
        raise denial_for(decision)


def _count_tool_call(operation: str) -> None:
    """Increment `maezo_tool_calls_total` for ONE gated invocation. Never raises onto the effect path.

    ALERTS-WITHOUT-METRICS-a: `deploy/observability/alert-rules.yml:36-52`'s
    `MaezoSLAAgentErrorRateHigh` divides `maezo_agent_errors_total` by this counter, and NOTHING in
    `src/` incremented either of them — the alert could not fire, in any deployment, ever. This is
    the tool-call half of the repair, placed in `gate` because that is the ONE function every gated
    seam calls exactly once per invocation; a seam that skipped the counter would also have skipped
    the decision, so coverage is structural rather than a convention.

    The local import mirrors the module's own discipline of keeping `maezo.gateway`'s policy core
    free of import-time coupling to the observability stack (`effect_pep.py`'s note on
    `maezo.tools`), and the broad guard mirrors `_emit`'s: telemetry never reaches a care path.
    Proof: `tests/unit/platform/test_alert_metrics_fence.py::test_gate_counts_a_tool_call`.
    """
    try:
        from maezo.platform.observability import record_tool_call  # noqa: PLC0415 — lazy

        record_tool_call()
    except Exception:  # noqa: BLE001 — a metric error must never break an effect call.
        logger.debug("effect_seam_tool_call_metric_failed", operation=operation, exc_info=True)


async def gate(
    seam: SeamContext,
    operation: str,
    *,
    process_key: str | None = None,
    value_cents: int | None = None,
) -> EffectDecision:
    """Decide, record, and (on an ENFORCED deny) refuse — the whole per-call shape of §5.4.

    Returns the decision when the caller may delegate to the inner seam. Raises the class's
    DECLARED denial shape otherwise. Adds ZERO network I/O: `decide_effect` is dict lookups over
    an `lru_cache`d manifest plus one log line (I-9 / Q-9).

    Args:
        seam: the closure-bound context. The principal is NOT a parameter (A-8).
        operation: a catalogue token (`effect_classes.OPERATIONS`). Unknown => L-0 DENY.
        process_key: `SP-OP-*` for the engine start only; a bounded, non-PHI ADR-0016 shape.
        value_cents: the ceiling leg's number. No catalogued operation declares a teto today.

    Also increments `maezo_tool_calls_total` (ALERTS-WITHOUT-METRICS-a) BEFORE deciding, so a
    denied call still counts as an attempted invocation — the alert's denominator is "tool calls
    the agents made", not "tool calls the policy allowed".
    """
    _count_tool_call(operation)
    decision = decide_effect(
        tenant=seam.tenant,
        principal=seam.principal,
        operation=operation,
        ctx=seam.decision,
        process_key=process_key,
        value_cents=value_cents,
        phi_zone=seam.phi_zone,
    )
    _emit(seam, decision, operation, process_key, value_cents)
    if decision.enforced and not decision.allow:
        raise denial_for(decision)
    await _pre_effect_audit(seam, decision, operation)
    return decision


__all__ = [
    "EFFECT_SEAM_KEYS",
    "EffectDeniedError",
    "GatedSeam",
    "PreEffectAuditHook",
    "SeamContext",
    "denial_for",
    "gate",
    "is_gated_seam",
]
