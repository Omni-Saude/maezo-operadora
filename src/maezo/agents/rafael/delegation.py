"""Rafael as A2A delegation TARGET (ADR-0003): envelope -> real graph -> output_ref.

Helena ORIGINATES an `authorization.analyze` delegation (Helena->Rafael) via
`DelegationDispatcher.delegate(envelope)` (see `agents/helena/delegation.py`). The dispatcher
validates (the target's Agent Card accepts the task_type), audits (T-F, `_audit_delegation` ->
`emit_once`), emits the fact, and ROUTES to the target's handler. This module provides that
handler:

    handler: DelegationEnvelope -> HandlerOutput(output_ref=...)

REWRITE, not a port (`docs/design/A2A-dispatcher-card-signing.md` §9.2): the donor's
`rafael/delegation.py` imports `from .contracts import InferenceProvider, ToolInvoker` and builds
`RafaelGraph(inference, tools)` — neither `contracts.py` nor that constructor signature exists in
v2. v2's `RafaelGraph.__init__(*, inference, dmn, cibseven, audit_sink, fhir=None, ...)`
(`graph.py:293-310`) already consumes v2's `runtime.inference.InferenceProvider` FACADE directly —
there is no router/adapter to build (design doc §9.1, the W3 headline finding). The dependency
surface IS larger than the donor's: v2's `build(config)` fail-closes without `dmn`/`cibseven`/
`audit_sink` (`graph.py:648-668`), so `make_rafael_handler` below requires all three, unlike the
donor's inference+tools-only handler.

Handler contract (`maezo.a2a.dispatcher.AgentHandler`):
- Receives the `DelegationEnvelope` (idempotent by `task_id`; `payload_ref` is a FHIR/
  pseudonymized reference — ADR-0006, never raw PHI).
- Runs Rafael's REAL graph (`agents.rafael.graph.build(config)`) with state materialized from the
  envelope's `payload_meta`/`payload_ref`, routed through the graph's own input-boundary gate
  (`new_rafael_state`, `graph.py:258-274`) rather than a raw cast.
- Returns `HandlerOutput(output_ref=...)` — a REFERENCE to the result (the started process's
  business key), NEVER raw PHI and NEVER a coverage decision.

HARD GUARDRAIL (L0 hard, `graph.py`'s own module docstring — re-enforced here, not merely
inherited): the handler's `output_ref` is always `f"process://{business_key}"`
(`AUTH-{tenant}-{numero_guia_tiss}"`). The dossier's `decisao_cobertura` is structurally always
`None` (`graph.py::_build_dossier`); this handler does not even forward the dossier in
`HandlerOutput.meta` — only `route`/`desfecho`/`process_started` — so a coverage decision has no
path to leak into the A2A response even if a future edit to the dossier's shape changed.

`payload_meta` carries the case's non-PHI identifiers (tenant, guide number, pseudo ids, category,
care type, value) plus the pre-resolved-by-worker booleans; `payload_ref` is the FHIR
coverage/beneficiary resource for dossier enrichment. Rafael never receives a CPF/name over this
seam — only references and pseudo ids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.a2a import DelegationEnvelope, HandlerOutput
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.dmn_transport import DmnTransport

from .graph import (
    CATEGORIA_PROCEDIMENTO_AUSENTE,
    FhirReader,
    RafaelState,
    _business_key,
    build,
    new_rafael_state,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.runtime.inference import InferenceProvider

# Task type Helena delegates to Rafael (must match his agent.yaml's accepted_task_types).
TASK_TYPE_AUTH_ANALYSIS = "authorization.analyze"

# Pre-resolved-by-worker boolean keys carried in payload_meta (never PHI).
_BOOLEAN_META_KEYS = (
    "requer_autorizacao",
    "documentacao_completa",
    "beneficiario_ativo",
    "carencia_cumprida",
    "dut_atendida",
    "dentro_teto_l2",
    "rede_credenciada",
)


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def state_from_envelope(envelope: DelegationEnvelope) -> RafaelState:
    """Materialize Rafael's initial graph state from a delegation envelope.

    Case identifiers/data come from `payload_meta` (never PHI); `payload_ref` (a FHIR reference)
    becomes `coverage_ref` for dossier enrichment. `tenant` comes from the envelope (ADR-0004).

    Routes the raw mapping through `new_rafael_state` (the T1.11 input-boundary gate,
    `graph.py:258-274`) rather than a raw `cast` — an unknown/output-only key raises loudly
    instead of silently entering the state (design doc §9.2's one deliberate change vs. the
    donor's `state_from_envelope`, which cast directly).
    """
    meta = dict(envelope.payload_meta)
    raw: dict[str, Any] = {
        "tenant_id": envelope.tenant,
        "numero_guia_tiss": meta.get("numero_guia_tiss", ""),
        "beneficiario_pseudo_id": meta.get("beneficiario_pseudo_id", ""),
        "prestador_id": meta.get("prestador_id", ""),
        "canal": "a2a",
        "codigo_procedimento_tuss": meta.get("codigo_procedimento_tuss", ""),
        "categoria_procedimento": meta.get("categoria_procedimento", CATEGORIA_PROCEDIMENTO_AUSENTE),
        "carater_atendimento": meta.get("carater_atendimento", "eletivo"),
        "valor_estimado_brl": float(meta.get("valor_estimado_brl", 0.0) or 0.0),
        "coverage_ref": envelope.payload_ref,
    }
    if meta.get("cid10"):
        raw["cid10"] = meta["cid10"]
    if meta.get("patient_ref"):
        raw["patient_ref"] = meta["patient_ref"]
    for key in _BOOLEAN_META_KEYS:
        if key in meta:
            raw[key] = _as_bool(meta[key])
    return new_rafael_state(raw)


def make_rafael_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: FhirReader | None = None,
    agent_version: str = "rafael@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Rafael's A2A handler (delegation target). The composition root registers it with the
    dispatcher (`handlers={"rafael": make_rafael_handler(...)}`, see
    `runtime.agent_runtime.a2a_composition.build_auth_delegation_dispatcher`).

    Compiles the REAL Rafael graph via `rafael.graph.build(config)` (the same fail-closed
    contract every other caller of `build` goes through — `dmn`/`cibseven`/`audit_sink` are
    REQUIRED, `fhir` is optional) and invokes it with the envelope-materialized state. The
    `output_ref` is `SP-OP-AUTH-001`'s business key (an auditable reference, never PHI) — the
    coverage outcome NEVER travels over this seam (it belongs to the medico-auditor's User Task).
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
        except Exception:
            # ALERTS-WITHOUT-METRICS-a: a delegated turn that raised IS a failed agent turn, and
            # `maezo_agent_errors_total` is what `MaezoAgentCrashLoop` reads. Placed at the
            # `ainvoke` seam — the structural entry to a graph run — and NOT anywhere in rafael's
            # business logic, which is why it is one line here rather than a rule each node has to
            # remember. `asyncio.CancelledError` is excluded (BaseException): a drained delegation
            # is not a failed agent. Enumerated and pinned by
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`.
            from maezo.platform.observability import record_agent_error  # noqa: PLC0415

            record_agent_error()
            raise
        business_key = result.get("business_key") or _business_key(state)
        # GUARDRAIL: output_ref is the process business key — never a coverage decision. The
        # dossier (whose `decisao_cobertura` is structurally always None, `graph.py`'s own
        # guardrail) is deliberately NOT forwarded here at all.
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "route": str(result.get("route", "human_auditor")),
                "desfecho": str(result.get("desfecho", "")),
                "process_started": str(result.get("process_started", False)),
            },
        )

    return handler
