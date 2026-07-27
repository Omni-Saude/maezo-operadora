"""Unit tests for `agents.andre.delegation` — the ADEQUACAO dossier A2A edge (DL-0033 real wiring).

Mirrors `tests/unit/agents/test_rafael_delegation.py`: engine-free, PG-free —
`make_andre_handler` runs the REAL `andre.graph.build(config)` graph against fakes, no I/O.
The SHARED-task_type disambiguation (`analytics.population` -> `flow` via `envelope.origin`) and
the EXPLICIT-flow requirement (his graph silently defaults a missing `flow` to `pagto_dossier`)
are the edge-specific proofs here. Live-Postgres idempotent-replay proof DEFERRED to the PR CI
lane (mirroring `test_a2a_edge_live_pg.py`) — no docker in this build environment.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from maezo.agents.andre.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_POPULATION_ANALYTICS,
    _flow_for,
    adequacao_task_id,
    build_adequacao_dossier_envelope,
    make_andre_handler,
    state_from_envelope,
)
from maezo.agents.andre.graph import _CALLER_INPUT_FIELDS, build
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CASE_META = {
    "ciclo_avaliacao": "2026-Q3",
    "tipo_carater": "eletivo",
    "gap_adequacao": "GAP_CRITICO",
    "roteamento_remediacao": "ANALISE_HUMANA",
    "tempo_acesso_apurado_min": 95,
    "distancia_apurada_km": 42.5,
    "prestadores_disponiveis": 1,
    "cobertura_geo_suficiente": False,
    "dados_geo_completos": True,
    # Never forwarded (not in the allowlist):
    "observacoes_livres": "texto livre",
}


class _FakeInference:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        assert phi is True, "Andre's dossier LLM call must be phi=True (security_zone: phi)"
        return "dossie de remediacao sintetico"


def _envelope(*, ciclo: str | None = "2026-Q3") -> Any:
    return build_adequacao_dossier_envelope(
        tenant="amh",
        regiao_saude="SP-01",
        especialidade="cardiologia",
        case_meta=_CASE_META,
        ciclo_avaliacao=ciclo,
    )


# --- task_id / envelope builder ---------------------------------------------------------------


def test_adequacao_task_id_is_the_cell_key() -> None:
    assert adequacao_task_id("amh", "SP-01", "cardiologia") == "ADEQ-amh-SP-01-cardiologia"
    assert adequacao_task_id("amh", "SP-01", "cardiologia", "2026-Q3") == "ADEQ-amh-SP-01-cardiologia-2026-Q3"


def test_envelope_uses_the_shared_task_type_and_worker_origin() -> None:
    """SHARED type (orchestrator decision): `analytics.population` — already accepted by Andre's
    card; NO new task_type is minted and spec/agents is untouched. `origin` is what carries the
    flow disambiguation."""
    envelope = _envelope()
    assert envelope.task_type == TASK_TYPE_POPULATION_ANALYTICS == "analytics.population"
    assert envelope.origin == ORIGIN_WORKER == "adequacao-worker"
    assert envelope.target == TARGET_AGENT == "andre"
    assert envelope.task_id == "ADEQ-amh-SP-01-cardiologia-2026-Q3"
    assert envelope.payload_ref == "process://ADEQ-amh-SP-01-cardiologia-2026-Q3"


def test_payload_meta_is_a_strict_non_phi_allowlist_of_cell_aggregates() -> None:
    meta = dict(_envelope().payload_meta)
    assert meta["regiao_saude"] == "SP-01"
    assert meta["gap_adequacao"] == "GAP_CRITICO"
    assert meta["tempo_acesso_apurado_min"] == "95"
    assert meta["distancia_apurada_km"] == "42.5"
    assert meta["cobertura_geo_suficiente"] == "false"
    assert "observacoes_livres" not in meta
    assert all(isinstance(v, str) for v in meta.values())


# --- origin -> flow disambiguation (the shared-task_type crux) --------------------------------


def test_flow_for_maps_adequacao_worker_origin_to_adequacao_dossier() -> None:
    assert _flow_for(_envelope()) == "adequacao_dossier"


def test_flow_for_any_other_origin_falls_back_to_andres_default_flow() -> None:
    foreign = replace(_envelope(), origin="pagto-worker", delegation_chain=("pagto-worker", "andre"))
    assert _flow_for(foreign) == "pagto_dossier"


def test_state_from_envelope_sets_flow_explicitly_for_adequacao_origin() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["flow"] == "adequacao_dossier"  # EXPLICIT — never the graph's missing-key default
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert state["regiao_saude"] == "SP-01"
    assert state["especialidade"] == "cardiologia"
    assert state["ciclo_avaliacao"] == "2026-Q3"
    assert state["tempo_acesso_apurado_min"] == 95
    assert state["distancia_apurada_km"] == 42.5
    assert state["prestadores_disponiveis"] == 1
    assert state["cobertura_geo_suficiente"] is False
    assert state["dados_geo_completos"] is True


def test_state_from_envelope_sets_default_flow_explicitly_for_foreign_origin() -> None:
    """The graph's `_flow` silently treats a MISSING `flow` key as `pagto_dossier` — the
    delegation layer must never rely on that: even the default flow is set EXPLICITLY."""
    foreign = replace(_envelope(), origin="pagto-worker", delegation_chain=("pagto-worker", "andre"))
    state = state_from_envelope(foreign)
    assert "flow" in state
    assert state["flow"] == "pagto_dossier"


# --- make_andre_handler -----------------------------------------------------------------------


async def test_handler_adequacao_flow_anchors_the_cell_and_routes_human_no_process_start() -> None:
    """Andre's adequacao flow: ALWAYS human (`gestao-rede`, `UT_DecisaoFallback`), NEVER a
    process start (the ADEQ key only anchors) — the handler surfaces exactly that."""
    handler = make_andre_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),  # adequacao flow evaluates NO DMN — an empty fake proves it
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_envelope())

    assert output.output_ref == "process://ADEQ-amh-SP-01-cardiologia-2026-Q3"
    assert output.meta["route"] == "human_review"
    assert output.meta["desfecho"] == "dossie_remediacao_humano"
    assert output.meta["motivo_humano"] == "dossie_remediacao"
    assert output.meta["grupo_destino"] == "gestao-rede"
    assert output.meta["process_started"] == "False"


async def test_handler_never_forwards_the_dossier_and_guardrail_fields_stay_none() -> None:
    """The L0-hard structural guardrails (`graph.py::_build_dossier`): re-run the SAME state
    directly through `build(config)` to inspect the full result — the handler never forwards the
    dossier, but this proves the deeper graph invariant it rests on."""
    dmn = FakeDmnTransport()
    cibseven = FakeCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    state = state_from_envelope(_envelope())

    compiled = build(
        {"inference": _FakeInference(), "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink}
    ).compile()
    result: dict[str, Any] = await compiled.ainvoke(state)

    assert result["dossier"]["decisao_pagamento"] is None
    assert result["dossier"]["preco_recomendado"] is None
    assert result["dossier"]["fhir_patient_id"] is None
    assert result["process_started"] is False  # adequacao NEVER starts a process

    handler = make_andre_handler(_FakeInference(), dmn=dmn, cibseven=cibseven, audit_sink=audit_sink)
    output = await handler(_envelope(ciclo="2026-Q4"))
    assert "dossier" not in output.meta
    assert "narrativa" not in output.meta
    assert all("decisao" not in str(v).lower() for v in output.meta.values())


async def test_handler_missing_cell_identity_fail_safes_to_human_never_a_payment_key() -> None:
    """A malformed adequacao envelope (no regiao/especialidade in meta) reaches Andre's own
    `receive` fail-safe: conservative human review, NO malformed business key, NO process."""
    envelope = build_adequacao_dossier_envelope(
        tenant="amh", regiao_saude="SP-01", especialidade="cardiologia", case_meta={}
    )
    object.__setattr__(envelope, "payload_meta", {})  # simulate a foreign, malformed producer
    handler = make_andre_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(envelope)

    assert output.meta["route"] == "human_review"
    assert output.meta["process_started"] == "False"
    assert not output.output_ref.startswith("process://PAGTO-")  # never a payment key
