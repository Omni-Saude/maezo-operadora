"""Unit tests for maezo.tools.workers.nip — SP-OP-NIP-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.nip import (
    NipInput,
    NipNegativaNotHumanError,
    NipProtocoloInvalidoError,
    NipResponseInput,
    assemble_response,
    classify_nip,
    classify_nip_entry,
    handoff_ans_submit,
    handoff_ans_submit_entry,
    notify_beneficiario,
    publish_completed,
    publish_completed_entry,
    review_juridico,
    route_nip,
    submit_response_entry,
    submit_to_ans,
)

# ---------------------------------------------------------------------------
# classify_nip
# ---------------------------------------------------------------------------


def test_classify_nip_assistencial_contesta() -> None:
    """Assistencial + contesta_negativa -> ASSISTENCIAL_CONTESTA_NEGATIVA, 5d, juridico."""
    inp = NipInput(
        tenant_id="amh",
        numero_nip_ans="NIP-001",
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
    )
    result = classify_nip(inp)
    assert result.classificacao == "ASSISTENCIAL_CONTESTA_NEGATIVA"
    assert result.prazo_dias == 5
    assert result.grupo_revisor == "juridico-regulatorio"


def test_classify_nip_assistencial_outro() -> None:
    """Assistencial without contesta -> ASSISTENCIAL_OUTRO."""
    inp = NipInput(
        tenant_id="amh",
        numero_nip_ans="NIP-002",
        classificacao_nip="assistencial",
        tema_nip="prazo_atendimento",
        contesta_negativa=False,
    )
    result = classify_nip(inp)
    assert result.classificacao == "ASSISTENCIAL_OUTRO"


def test_classify_nip_nao_assistencial() -> None:
    """Nao-assistencial -> NAO_ASSISTENCIAL, 10d."""
    inp = NipInput(
        tenant_id="amh",
        numero_nip_ans="NIP-003",
        classificacao_nip="nao_assistencial",
        tema_nip="cobranca",
        contesta_negativa=False,
    )
    result = classify_nip(inp)
    assert result.classificacao == "NAO_ASSISTENCIAL"
    assert result.prazo_dias == 10


def test_classify_nip_unknown_tema() -> None:
    """Unknown tema -> catch-all: ASSISTENCIAL_CONTESTA_NEGATIVA, juridico-regulatorio."""
    inp = NipInput(
        tenant_id="amh",
        numero_nip_ans="NIP-004",
        classificacao_nip="assistencial",
        tema_nip="tema_desconhecido",
        contesta_negativa=True,
    )
    result = classify_nip(inp)
    assert result.classificacao == "ASSISTENCIAL_CONTESTA_NEGATIVA"
    assert result.grupo_revisor == "juridico-regulatorio"


# ---------------------------------------------------------------------------
# route_nip
# ---------------------------------------------------------------------------


def test_route_nip_elaborar_resposta() -> None:
    """Documentacao suficiente + nao contesta negativa -> ELABORAR_RESPOSTA."""
    from maezo.tools.workers.nip import NipClassificationResult

    classification = NipClassificationResult(
        classificacao="NAO_ASSISTENCIAL",
        prazo_dias=10,
        grupo_revisor="nucleo-ans",
    )
    result = route_nip(classification, documentacao_suficiente=True)
    assert result.roteamento == "ELABORAR_RESPOSTA"
    assert result.grupo_humano == "nucleo-ans"


def test_route_nip_revisao_juridica() -> None:
    """Assistencial + contesta_negativa -> REVISAO_JURIDICA."""
    from maezo.tools.workers.nip import NipClassificationResult

    classification = NipClassificationResult(
        classificacao="ASSISTENCIAL_CONTESTA_NEGATIVA",
        prazo_dias=5,
        grupo_revisor="juridico-regulatorio",
    )
    result = route_nip(classification, documentacao_suficiente=True)
    assert result.roteamento == "REVISAO_JURIDICA"
    assert result.grupo_humano == "juridico-regulatorio"


def test_route_nip_pendente_info() -> None:
    """Insufficient documentation -> PENDENTE_INFO."""
    from maezo.tools.workers.nip import NipClassificationResult

    classification = NipClassificationResult(
        classificacao="ASSISTENCIAL_OUTRO",
        prazo_dias=5,
        grupo_revisor="regulatorio-ans",
    )
    result = route_nip(classification, documentacao_suficiente=False)
    assert result.roteamento == "PENDENTE_INFO"


# ---------------------------------------------------------------------------
# assemble_response
# ---------------------------------------------------------------------------


def test_assemble_response() -> None:
    """assemble_response builds a response dossier."""
    from maezo.tools.workers.nip import NipClassificationResult, NipRoutingResult

    classification = NipClassificationResult()
    routing = NipRoutingResult()
    inp = NipInput(tenant_id="amh", numero_nip_ans="NIP-005", tema_nip="reembolso")

    result = assemble_response(classification, routing, inp)
    assert result["numero_nip_ans"] == "NIP-005"
    assert "dossie" in result


# ---------------------------------------------------------------------------
# review_juridico
# ---------------------------------------------------------------------------


def test_review_juridico() -> None:
    """review_juridico approves final response text."""
    result = review_juridico(
        texto_minuta="Resposta formal...",
        classificacao="ASSISTENCIAL_CONTESTA_NEGATIVA",
        revisor_id="juridico-001",
    )
    assert result["revisado"] is True
    assert result["revisor_id"] == "juridico-001"


# ---------------------------------------------------------------------------
# submit_to_ans — GUARD tests
# ---------------------------------------------------------------------------


def test_submit_to_ans_guard_missing_revisor() -> None:
    """submit_to_ans raises if MANTER_NEGATIVA without revisor_id."""
    inp = NipResponseInput(
        decisao_nip="MANTER_NEGATIVA",
        fundamentacao_regulatoria="Fundamentacao XYZ",
        referencia_negativa_original="AUTH-001",
        texto_resposta_nip="Texto da resposta",
        revisor_id="",
    )
    with pytest.raises(NipNegativaNotHumanError) as exc:
        submit_to_ans(inp)
    assert "revisor_id" in str(exc.value)


def test_submit_to_ans_guard_missing_fundamentacao() -> None:
    """submit_to_ans raises if MANTER_NEGATIVA without fundamentacao_regulatoria."""
    inp = NipResponseInput(
        decisao_nip="MANTER_NEGATIVA",
        fundamentacao_regulatoria="",
        referencia_negativa_original="AUTH-001",
        texto_resposta_nip="Texto da resposta",
        revisor_id="juridico-001",
    )
    with pytest.raises(NipNegativaNotHumanError) as exc:
        submit_to_ans(inp)
    assert "fundamentacao_regulatoria" in str(exc.value)


def test_submit_to_ans_conceder_no_guard() -> None:
    """submit_to_ans allows CONCEDER without the MANTER_NEGATIVA guard."""
    inp = NipResponseInput(
        decisao_nip="CONCEDER",
        texto_resposta_nip="Resposta favoravel ao beneficiario",
    )
    result = submit_to_ans(inp)
    assert result["submitted"] is True
    assert result["decisao_nip"] == "CONCEDER"


def test_submit_to_ans_missing_texto() -> None:
    """submit_to_ans raises if texto_resposta_nip is empty."""
    inp = NipResponseInput(
        decisao_nip="CONCEDER",
        texto_resposta_nip="",
    )
    with pytest.raises(NipNegativaNotHumanError):
        submit_to_ans(inp)


# ---------------------------------------------------------------------------
# notify_beneficiario
# ---------------------------------------------------------------------------


def test_notify_beneficiario_nip() -> None:
    """notify_beneficiario sends resolution notification."""
    result = notify_beneficiario("BEN-PSEUDO-001", "NIP-006", decisao_nip="CONCEDER")
    assert result["notified"] is True


# ---------------------------------------------------------------------------
# handoff_ans_submit
# ---------------------------------------------------------------------------


def test_handoff_ans_submit_valid() -> None:
    """handoff_ans_submit with protocolo_ans=None is valid."""
    result = handoff_ans_submit(
        numero_nip_ans="NIP-007",
        protocolo_ans=None,
        decisao_nip="CONCEDER",
    )
    assert result["handoff"] == "SP-OP-ANS-SUBMIT-001"
    assert result["origem_envio"] == "nip_filing"


def test_handoff_ans_submit_empty_protocolo_raises() -> None:
    """handoff_ans_submit with empty string raises ERR_NIP_PROTOCOLO_INVALIDO."""
    with pytest.raises(NipProtocoloInvalidoError):
        handoff_ans_submit(
            numero_nip_ans="NIP-008",
            protocolo_ans="   ",
        )


def test_handoff_ans_submit_with_protocolo() -> None:
    """handoff_ans_submit with valid protocolo_ans passes."""
    result = handoff_ans_submit(
        numero_nip_ans="NIP-009",
        protocolo_ans="ANSPROTO-123",
    )
    assert result["protocolo_ans"] == "ANSPROTO-123"


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_nip() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="resolvida_favoravel")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "resolvida_favoravel"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_nip_negativa_not_human_is_permission_error() -> None:
    """NipNegativaNotHumanError must be a subclass of PermissionError."""
    assert issubclass(NipNegativaNotHumanError, PermissionError)


def test_nip_protocolo_invalido_is_value_error() -> None:
    """NipProtocoloInvalidoError must be a subclass of ValueError."""
    assert issubclass(NipProtocoloInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_classify_nip_entry_round_trips_classify_nip() -> None:
    variables = {"classificacao_nip": "assistencial", "tema_nip": "reembolso", "contesta_negativa": True}
    direct = classify_nip(NipInput(**variables))
    result = classify_nip_entry(variables)
    assert result["classificacao"] == direct.classificacao
    assert result["prazo_dias"] == direct.prazo_dias


def test_submit_response_entry_guards_missing_human_decision() -> None:
    """submit_response_entry raises the UNCHANGED NipNegativaNotHumanError guard."""
    with pytest.raises(NipNegativaNotHumanError):
        submit_response_entry({"decisao_nip": "MANTER_NEGATIVA"})


def test_submit_response_entry_happy_path() -> None:
    variables = {
        "decisao_nip": "MANTER_NEGATIVA",
        "revisor_id": "revisor-1",
        "fundamentacao_regulatoria": "RN 259",
        "referencia_negativa_original": "NEG-1",
        "texto_resposta_nip": "resposta final",
    }
    direct = submit_to_ans(NipResponseInput(**variables))
    assert submit_response_entry(variables) == direct


def test_handoff_ans_submit_entry_raises_on_blank_protocolo() -> None:
    """Absent (None) protocolo_ans is legitimate; blank/empty raises NipProtocoloInvalidoError
    (unchanged guard, GAP-NIP-6)."""
    with pytest.raises(NipProtocoloInvalidoError):
        handoff_ans_submit_entry({"numero_nip_ans": "NIP-1", "protocolo_ans": "  "})


def test_handoff_ans_submit_entry_allows_absent_protocolo() -> None:
    result = handoff_ans_submit_entry({"numero_nip_ans": "NIP-1"})
    assert result == handoff_ans_submit("NIP-1", None, "", "")


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "nip.completed", "desfecho": "resolvida_favoravel"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="nip.completed", payload={}, desfecho="resolvida_favoravel"
    )
