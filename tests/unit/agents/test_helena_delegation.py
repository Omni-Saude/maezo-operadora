"""Unit tests for `agents.helena.delegation` — the A2A originator helper (T2.4 A2A W3).

Engine-free, PG-free: only exercises `build_auth_analysis_envelope`/`auth_task_id`/
`_build_payload_meta` against the real `DelegationEnvelope` (W2). No dispatcher, no graph, no I/O.
See `tests/unit/a2a/test_dispatcher.py::test_dispatcher_routes_a_real_authorization_analysis_
delegation_to_rafael` for the end-to-end dispatch-through-Rafael proof.
"""

from __future__ import annotations

import pytest

from maezo.a2a import Budget, DelegationError
from maezo.agents.helena.delegation import (
    ORIGIN_AGENT,
    TARGET_AGENT,
    TASK_TYPE_AUTH_ANALYSIS,
    auth_task_id,
    build_auth_analysis_envelope,
)

_CASE_META = {
    "beneficiario_pseudo_id": "pseudo-123",
    "prestador_id": "prestador-1",
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 250.0,
    "cid10": "Z00.0",
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": False,
    "rede_credenciada": True,
}


def test_auth_task_id_is_deterministic() -> None:
    assert auth_task_id("amh", "GUIA-001") == "auth-amh-GUIA-001"
    assert auth_task_id("amh", "GUIA-001") == auth_task_id("amh", "GUIA-001")


def test_build_auth_analysis_envelope_shape() -> None:
    envelope = build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-001",
        coverage_ref="fhir://Coverage/abc",
        case_meta=_CASE_META,
    )

    assert envelope.task_id == "auth-amh-GUIA-001"
    assert envelope.task_type == TASK_TYPE_AUTH_ANALYSIS == "authorization.analyze"
    assert envelope.origin == ORIGIN_AGENT == "helena"
    assert envelope.target == TARGET_AGENT == "rafael"
    assert envelope.tenant == "amh"
    assert envelope.delegation_chain == ("helena", "rafael")
    assert envelope.payload_ref == "fhir://Coverage/abc"


def test_envelope_payload_meta_carries_only_non_phi_keys_and_booleans() -> None:
    envelope = build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-001",
        coverage_ref="fhir://Coverage/abc",
        case_meta=_CASE_META,
    )
    meta = envelope.payload_meta

    assert meta["numero_guia_tiss"] == "GUIA-001"
    assert meta["beneficiario_pseudo_id"] == "pseudo-123"
    assert meta["prestador_id"] == "prestador-1"
    assert meta["codigo_procedimento_tuss"] == "10101012"
    assert meta["categoria_procedimento"] == "consulta"
    assert meta["carater_atendimento"] == "eletivo"
    assert meta["cid10"] == "Z00.0"
    assert meta["valor_estimado_brl"] == "250.0"
    # Pre-resolved worker booleans are carried as "true"/"false" strings (payload_meta is
    # Mapping[str, str] — A2A never carries raw Python bools).
    assert meta["requer_autorizacao"] == "true"
    assert meta["dentro_teto_l2"] == "false"
    # No raw PHI key (cpf/name/cns) is ever present.
    assert "cpf" not in meta
    assert "nome" not in meta


def test_envelope_payload_meta_omits_unset_optional_keys() -> None:
    envelope = build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-002",
        coverage_ref="fhir://Coverage/xyz",
        case_meta={"beneficiario_pseudo_id": "pseudo-999"},
    )
    meta = envelope.payload_meta
    assert meta == {"numero_guia_tiss": "GUIA-002", "beneficiario_pseudo_id": "pseudo-999"}


def test_default_budget_is_charged_one_hop() -> None:
    envelope = build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-001",
        coverage_ref="fhir://Coverage/abc",
        case_meta=_CASE_META,
    )
    # Default budget tokens=64, cost_per_hop=1 -> one hop charged at root() -> 63 remaining.
    assert envelope.budget.tokens == 63
    assert envelope.budget.time_ms == 59_999


def test_custom_budget_is_respected_and_charged() -> None:
    envelope = build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-001",
        coverage_ref="fhir://Coverage/abc",
        case_meta=_CASE_META,
        budget=Budget(tokens=10, time_ms=1_000, cost_per_hop=2),
    )
    assert envelope.budget.tokens == 8
    assert envelope.budget.time_ms == 998


def test_cpf_shaped_coverage_ref_is_rejected_as_phi() -> None:
    """`coverage_ref` must be a FHIR/pseudonymized reference — a CPF-shaped payload_ref is
    rejected by the envelope's own `_looks_like_phi` guard (ADR-0006)."""
    with pytest.raises(DelegationError, match="looks like raw PHI"):
        build_auth_analysis_envelope(
            tenant="amh",
            numero_guia_tiss="GUIA-001",
            coverage_ref="123.456.789-01",  # CPF-shaped, never a real payload_ref
            case_meta=_CASE_META,
        )
