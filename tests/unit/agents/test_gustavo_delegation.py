"""Unit tests for `agents.gustavo.delegation` — the inbound `nip.instruct` edge (GUS-01).

Mirrors `tests/unit/agents/test_fernando_delegation.py` (engine-free, PG-free — `make_gustavo_
handler` runs the REAL `gustavo.graph.build(config)` graph against fakes, no I/O). The origin side
(`delegate_nip_instruction`) is NOT called from `tools/workers/nip.py` (see
`agents/gustavo/delegation.py`'s module docstring — that call site is an owner decision outside
this work package); these tests cover the TARGET side end to end plus the origin helpers in
isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.gustavo.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_NIP_INSTRUCT,
    TASK_TYPES,
    build_nip_instruction_envelope,
    make_gustavo_handler,
    nip_task_id,
    state_from_envelope,
)
from maezo.agents.gustavo.graph import (
    _CALLER_INPUT_FIELDS,
    DMN_NIP_CLASSIFICATION,
    DMN_NIP_ROUTING,
    DMN_NIP_SLA,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# The two FREE-TEXT NIP fields plus a narrative plant: none may ride the seam (ADR-0006, and
# gustavo/graph.py's own rule that free text never travels in class-token fields).
_FREE_TEXT_PLANTS: dict[str, Any] = {
    "tema_nip": "negativa_cobertura",
    "referencia_negativa_original": "AUTH-guia-000 — negativa por ausencia de DUT, texto integral",
    "narrativa": "beneficiario relata negativa indevida",
}

_CASE_META: dict[str, Any] = {
    # Low-entropy synthetic NIP number (gitleaks hygiene, same rationale as test_gustavo.py).
    "protocolo_ans": "ANS-000000042",
    "beneficiario_pseudo_id": "pseudo-123",
    "classificacao_nip": "assistencial",
    "data_recebimento_nip_iso": "2026-07-10",
    "patient_ref": "Patient/pseudo-123",
    "contesta_negativa": True,
    "documentacao_suficiente": True,
    "documentos_refs": [{"ref": "doc://1"}],
    **_FREE_TEXT_PLANTS,
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie de instrucao sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self, prompt: str, *, phi: bool = False, agent_id: str | None = None, tenant_id: str | None = None
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


def _envelope(*, numero_nip_ans: str = "000000042", case_meta: dict[str, Any] | None = None) -> Any:
    return build_nip_instruction_envelope(
        tenant="amh",
        numero_nip_ans=numero_nip_ans,
        case_meta=case_meta if case_meta is not None else _CASE_META,
    )


def _dmn_nip() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        DMN_NIP_CLASSIFICATION,
        [
            {
                "classificacao": "ASSISTENCIAL_CONTESTA_NEGATIVA",
                "prazo_dias": 5,
                "grupo_revisor": "juridico-regulatorio",
            }
        ],
    )
    dmn.register(
        DMN_NIP_SLA,
        [
            {
                "prazo_resposta_iso": "P5D",
                "prazo_resposta_absoluto_iso": "2026-07-15T00:00:00",
                "sla_alerta_iso": "P3D",
                "sla_alerta_absoluto_iso": "2026-07-13T00:00:00",
                "fonte_regulatoria": "RN 388/2016 — DRAFT/verify",
            }
        ],
    )
    dmn.register(
        DMN_NIP_ROUTING,
        [{"roteamento": "REVISAO_JURIDICA", "grupo_humano": "juridico-regulatorio"}],
    )
    return dmn


def _handler(dmn: FakeDmnTransport) -> Any:
    return make_gustavo_handler(
        _FakeInference(),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


# --- task types / task_id / envelope builder ----------------------------------------------------


def test_task_types_match_the_agent_card_contract() -> None:
    assert {"nip.instruct"} == TASK_TYPES


def test_task_id_is_the_process_business_key_and_the_correlation_key() -> None:
    """MUTATION PROBE (dispatcher Guard 4 + contract §Correlacao): the `msg.nip.instruct` message
    correlates on `NIP-{tenant}-{numero_nip_ans}`; a different `task_id` breaks BOTH the
    idempotent replay and the engine correlation."""
    assert nip_task_id("amh", "000000042") == "NIP-amh-000000042"


def test_envelope_contract_matches_gustavo_card() -> None:
    envelope = _envelope()
    assert envelope.task_type == TASK_TYPE_NIP_INSTRUCT == "nip.instruct"
    assert envelope.origin == ORIGIN_WORKER == "nip-worker"
    assert envelope.target == TARGET_AGENT == "gustavo"
    assert envelope.task_id == "NIP-amh-000000042"
    assert envelope.payload_ref == "process://NIP-amh-000000042"
    assert envelope.delegation_chain == ("nip-worker", "gustavo")


def test_envelope_task_id_equals_the_graphs_own_business_key() -> None:
    envelope = _envelope()
    assert envelope.task_id == _business_key(state_from_envelope(envelope))


# --- payload_meta: strict non-PHI allowlist -----------------------------------------------------


def test_payload_meta_never_carries_the_free_text_nip_fields() -> None:
    """MUTATION PROBE (ADR-0006): adding `referencia_negativa_original` or `tema_nip` to
    `_STRING_META_KEYS` turns this RED. `referencia_negativa_original` is the single highest-risk
    field on this seam — unbounded, caller-controlled, beneficiary-adjacent."""
    meta = dict(_envelope().payload_meta)
    for forbidden in _FREE_TEXT_PLANTS:
        assert forbidden not in meta
    assert "documentos_refs" not in meta  # list-shaped
    assert all(isinstance(value, str) for value in meta.values())


def test_payload_meta_carries_the_bounded_scalars() -> None:
    meta = dict(_envelope().payload_meta)
    assert meta["numero_nip_ans"] == "000000042"
    assert meta["classificacao_nip"] == "assistencial"
    assert meta["data_recebimento_nip_iso"] == "2026-07-10"
    assert meta["contesta_negativa"] == "true"


# --- state_from_envelope ------------------------------------------------------------------------


def test_state_from_envelope_maps_meta_into_gustavo_input_fields_only() -> None:
    state = state_from_envelope(_envelope())

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert state["numero_nip_ans"] == "000000042"
    assert state["contesta_negativa"] is True


def test_state_from_envelope_pins_the_nip_flow_and_the_a2a_provenance() -> None:
    """`fluxo` is never envelope-sourced (the task type IS the selector) and `origem_a2a` is the
    contract's own definition of "born from an A2A `nip.instruct` delegation" — a delegated turn
    asserting otherwise would be lying about its own provenance."""
    state = state_from_envelope(_envelope())
    assert state["fluxo"] == "nip"
    assert state["origem_a2a"] is True


def test_state_from_envelope_never_seeds_the_free_text_fields() -> None:
    state = state_from_envelope(_envelope())
    assert "tema_nip" not in state
    assert "referencia_negativa_original" not in state
    assert "documentos_refs" not in state


def test_state_from_envelope_fails_closed_without_numero_nip_ans() -> None:
    envelope = _envelope()
    object.__setattr__(envelope, "payload_meta", {"protocolo_ans": "ANS-1"})
    with pytest.raises(ValueError, match="numero_nip_ans"):
        state_from_envelope(envelope)


def test_state_from_envelope_cannot_be_pushed_into_the_ans_submit_flow() -> None:
    """A producer planting `fluxo=ans_submit` (or the J1 identifiers) cannot cross journeys:
    `fluxo` is set here, and `report_type`/`competencia`/`dataset_ref` are not on this seam's
    allowlist at all — so a delegated turn can never start SP-OP-ANS-SUBMIT-001."""
    envelope = _envelope()
    planted = {
        **dict(envelope.payload_meta),
        "fluxo": "ans_submit",
        "report_type": "RN_124_SIP",
        "competencia": "2026-Q2",
        "dataset_ref": "ds://1",
    }
    object.__setattr__(envelope, "payload_meta", planted)
    state = state_from_envelope(envelope)

    assert state["fluxo"] == "nip"
    assert "report_type" not in state
    assert "dataset_ref" not in state


def test_state_from_envelope_drops_a_planted_output_field() -> None:
    envelope = _envelope()
    planted = {**dict(envelope.payload_meta), "route": "review_submission", "dossier": "x"}
    object.__setattr__(envelope, "payload_meta", planted)
    state = state_from_envelope(envelope)
    assert "route" not in state
    assert "dossier" not in state


# --- make_gustavo_handler -----------------------------------------------------------------------


async def test_handler_instructs_the_nip_and_starts_the_process() -> None:
    output = await _handler(_dmn_nip())(_envelope())

    assert output.output_ref == "process://NIP-amh-000000042"
    assert output.meta["route"] == "instruct_nip"
    assert output.meta["process_started"] == "True"


async def test_handler_dmn_unavailable_still_routes_to_the_human_instruction() -> None:
    """Both of Gustavo's destinations are human; an unavailable DMN can never produce an adverse
    outcome by omission (graph docstring's FAIL-SAFE FECHADO)."""
    output = await _handler(FakeDmnTransport())(_envelope())
    assert output.meta["route"] == "instruct_nip"


async def test_handler_never_forwards_the_dossier_content() -> None:
    """A CLOSED key set — adding `dossier`/`nip_facts` to the handler turns this RED. `decisao_
    nip` (L0 hard: only `UT_RevisaoJuridicaNip`'s human) can never appear here."""
    output = await _handler(_dmn_nip())(_envelope())

    assert set(output.meta) == {
        "route",
        "desfecho",
        "motivo_humano",
        "grupo_destino",
        "classificacao",
        "process_started",
    }
    assert all(isinstance(v, str) and len(v) <= 40 for v in output.meta.values())
    assert "decisao_nip" not in output.meta
    assert "referencia_negativa_original" not in output.meta


async def test_handler_uses_the_real_fail_closed_build_contract() -> None:
    state = state_from_envelope(_envelope())
    direct = build(
        {
            "inference": _FakeInference(),
            "dmn": _dmn_nip(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        }
    ).compile()
    direct_result: dict[str, Any] = await direct.ainvoke(state)

    handler_output = await _handler(_dmn_nip())(_envelope())

    assert direct_result["route"] == "instruct_nip"
    assert handler_output.meta["route"] == "instruct_nip"
