"""Unit tests for `agents.rafael.delegation` — the A2A target handler (T2.4 A2A W3).

Engine-free, PG-free: `make_rafael_handler` runs the REAL `rafael.graph.build(config)` graph
against fakes (`FakeDmnTransport`/`FakeCibSevenTransport`/`FakeStartAuditSink`/an in-file fake
inference) — no I/O. See `tests/unit/a2a/test_dispatcher.py` for the dispatcher-routes-to-the-
real-handler + idempotent-replay proof, and `tests/integration/agents/test_a2a_auth_delegation_
edge.py` for the "T-F becomes real" live-Postgres proof.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from maezo.agents.helena.delegation import build_auth_analysis_envelope
from maezo.agents.rafael.delegation import make_rafael_handler, state_from_envelope
from maezo.agents.rafael.graph import RAFAEL_INPUT_FIELDS, build
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CASE_META = {
    "beneficiario_pseudo_id": "pseudo-edge-1",
    "prestador_id": "prestador-1",
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 500.0,
    "cid10": "Z00.0",
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": False,
    "rede_credenciada": True,
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        assert phi is True, "Rafael's dossier LLM call must be phi=True (security_zone: phi)"
        return self._responses.pop(0) if self._responses else ""


def _envelope(*, numero_guia_tiss: str = "GUIA-EDGE-1") -> Any:
    return build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss=numero_guia_tiss,
        coverage_ref="fhir://Coverage/edge-1",
        case_meta=_CASE_META,
    )


def _dmn(*, recomendacao: str = "ANALISE_HUMANA") -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("auth_admissibility", [{"resultado": "SEGUE_ANALISE", "motivo": "test"}])
    dmn.register("auth_sla", [{"sla_analise": "P5D", "sla_alerta": "P3D", "fonte_regulatoria": "RN 395"}])
    dmn.register("auth_auto_approval", [{"recomendacao": recomendacao, "motivo": "test"}])
    return dmn


# --- state_from_envelope --------------------------------------------------------------------


def test_state_from_envelope_maps_payload_meta_into_rafael_input_fields() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= RAFAEL_INPUT_FIELDS
    assert state["tenant_id"] == "amh"
    assert state["numero_guia_tiss"] == "GUIA-EDGE-1"
    assert state["beneficiario_pseudo_id"] == "pseudo-edge-1"
    assert state["canal"] == "a2a"
    assert state["coverage_ref"] == "fhir://Coverage/edge-1"
    assert state["valor_estimado_brl"] == 500.0
    assert state["dentro_teto_l2"] is False
    assert state["requer_autorizacao"] is True


def test_state_from_envelope_routes_through_new_rafael_state_gate() -> None:
    """`state_from_envelope` must pass through `graph.new_rafael_state` (the T1.11 input-boundary
    gate) rather than a raw cast — this is the ONE deliberate change vs. the donor's
    `state_from_envelope`, per the design doc §9.2."""
    with patch("maezo.agents.rafael.delegation.new_rafael_state", wraps=lambda raw: raw) as gate:
        state = state_from_envelope(_envelope())
    gate.assert_called_once()
    (raw_arg,) = gate.call_args.args
    assert raw_arg["numero_guia_tiss"] == "GUIA-EDGE-1"
    assert state == raw_arg  # the `wraps` identity function proves the gate's return IS used


# --- make_rafael_handler ---------------------------------------------------------------------


async def test_handler_returns_process_business_key_output_ref() -> None:
    handler = make_rafael_handler(
        _FakeInference(),
        dmn=_dmn(recomendacao="ANALISE_HUMANA"),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_envelope())

    assert output.output_ref == "process://AUTH-amh-GUIA-EDGE-1"
    assert output.meta["route"] == "human_auditor"
    assert output.meta["process_started"] == "True"


async def test_handler_auto_approve_route_also_returns_process_reference_never_a_decision() -> None:
    handler = make_rafael_handler(
        _FakeInference(),
        dmn=_dmn(recomendacao="AUTO_APROVAR"),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_envelope(numero_guia_tiss="GUIA-EDGE-AUTO"))

    assert output.output_ref == "process://AUTH-amh-GUIA-EDGE-AUTO"
    assert output.meta["route"] == "auto_approve"
    # GUARDRAIL: no field of HandlerOutput ever mentions a coverage decision.
    assert "decisao_cobertura" not in output.meta
    assert all("decisao" not in str(v).lower() for v in output.meta.values())


async def test_handler_output_never_leaks_the_dossier_and_decisao_cobertura_stays_none() -> None:
    """The L0-hard structural guardrail (`graph.py::_build_dossier`): re-run the SAME state
    directly through `build(config)` (bypassing the handler) to inspect the full result dict —
    the handler itself never even forwards the dossier, but this proves the deeper graph
    invariant the handler's safety ultimately rests on."""
    dmn = _dmn(recomendacao="ANALISE_HUMANA")
    cibseven = FakeCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    envelope = _envelope(numero_guia_tiss="GUIA-EDGE-GUARDRAIL")
    state = state_from_envelope(envelope)

    compiled = build(
        {"inference": _FakeInference(), "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink}
    ).compile()
    result: dict[str, Any] = await compiled.ainvoke(state)

    assert result["dossier"]["decisao_cobertura"] is None

    handler = make_rafael_handler(_FakeInference(), dmn=dmn, cibseven=cibseven, audit_sink=audit_sink)
    output = await handler(_envelope(numero_guia_tiss="GUIA-EDGE-GUARDRAIL-2"))
    assert "dossier" not in output.meta
    assert "decisao_cobertura" not in output.meta
