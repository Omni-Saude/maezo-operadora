"""Unit tests for maezo.tools.workers.contas — SP-OP-CONTAS-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.contas import (
    ContasLoteInvalidoError,
    GlosaAcceptInput,
    GlosaAcceptNotHumanError,
    GlosaInput,
    analyze_reason,
    calculate_impact,
    identify_glosa,
    notify_sla_risk,
    prepare_triage_dossier,
    publish,
    reconcile_payment,
    register_glosa_accept,
    start_recurso,
)

# ---------------------------------------------------------------------------
# identify_glosa
# ---------------------------------------------------------------------------


def test_identify_glosa_no_glosas() -> None:
    """identify_glosa returns has_glosas=False for a clean lote."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-001",
        valor_apresentado_brl=1000.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
        ],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is False
    assert result.denial_ratio == 0.0
    assert result.divergencia_valor is False
    assert result.glosa_count == 0


def test_identify_glosa_with_glosas() -> None:
    """identify_glosa detects glosa candidates from linhas with valor_glosado > 0."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-002",
        valor_apresentado_brl=1500.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 100000, "valor_glosado_centavos": 30000},
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
        ],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True
    assert result.glosa_count == 1
    assert result.denial_ratio > 0.0
    assert result.divergencia_valor is True


def test_identify_glosa_fail_closed_empty_lines() -> None:
    """Empty linhas_conta_refs -> fail-closed: has_glosas=True, denial_ratio=1.0."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-003",
        valor_apresentado_brl=500.0,
        linhas_conta_refs=[],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True
    assert result.denial_ratio == 1.0
    assert result.divergencia_valor is True


def test_identify_glosa_reason_codes_trigger_glosa() -> None:
    """Reason codes in the lote-level list also trigger has_glosas."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-004",
        valor_apresentado_brl=1000.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 100000, "valor_glosado_centavos": 0},
        ],
        reason_codes_tiss=["COD001"],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True


# ---------------------------------------------------------------------------
# analyze_reason
# ---------------------------------------------------------------------------


def test_analyze_reason_normalizes_codes() -> None:
    """analyze_reason maps TISS codes to normalized categories."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-005",
        reason_codes_tiss=["TECNICA-001", "ADMINISTRATIVA"],
    )
    result = analyze_reason(inp)
    assert "reason_map" in result
    assert result["categoria_normalizada"] in ("tecnica", "administrativa")


def test_analyze_reason_unknown_code() -> None:
    """Unknown TISS codes map to 'desconhecida' (catch-all)."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-006",
        reason_codes_tiss=["XYZ-999"],
    )
    result = analyze_reason(inp)
    assert result["categoria_normalizada"] == "desconhecida"


# ---------------------------------------------------------------------------
# calculate_impact
# ---------------------------------------------------------------------------


def test_calculate_impact_computes_brl() -> None:
    """calculate_impact converts centavos to BRL and computes ratio."""
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(
        has_glosas=True,
        denial_ratio=0.2,
        divergencia_valor=True,
        glosa_count=2,
        total_glosado_candidato_centavos=20000,
    )
    result = calculate_impact(identified, 1000.0)
    assert result["total_glosado_candidato_brl"] == 200.0
    assert result["impacto_percentual"] == 20.0


# ---------------------------------------------------------------------------
# prepare_triage_dossier
# ---------------------------------------------------------------------------


def test_prepare_triage_dossier_routes_to_human() -> None:
    """Triage always routes conservatively to ANALISE_HUMANA for glosas."""
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-007")
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=True, denial_ratio=0.5, divergencia_valor=True, glosa_count=1)
    reason = {"categoria_normalizada": "tecnica"}

    result = prepare_triage_dossier(inp, identified, reason)
    assert result.roteamento in ("ANALISE_HUMANA", "SEM_GLOSA")
    # With glosas present, must not be SEM_GLOSA
    assert result.roteamento != "SEM_GLOSA" or not identified.has_glosas


def test_prepare_triage_dossier_no_glosas() -> None:
    """No glosas -> SEM_GLOSA."""
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-008")
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=False, divergencia_valor=False)
    reason = {"categoria_normalizada": "administrativa"}

    result = prepare_triage_dossier(inp, identified, reason)
    assert result.roteamento == "SEM_GLOSA"


# ---------------------------------------------------------------------------
# register_glosa_accept — GUARD tests
# ---------------------------------------------------------------------------


def test_glosa_accept_guard_missing_decisao() -> None:
    """register_glosa_accept raises if decisao_contas != ACEITAR_GLOSA."""
    inp = GlosaAcceptInput(
        decisao_contas="RECORRER",
        justificativa_glosa="test",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "decisao_contas" in str(exc.value)


def test_glosa_accept_guard_missing_justificativa() -> None:
    """register_glosa_accept raises if justificativa_glosa is empty."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "justificativa_glosa" in str(exc.value)


def test_glosa_accept_guard_missing_analista() -> None:
    """register_glosa_accept raises if analista_id is empty."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="Motivo valido",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "analista_id" in str(exc.value)


def test_glosa_accept_success() -> None:
    """register_glosa_accept succeeds with all required fields."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="Glosa justificada conforme contrato",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=150.0,
        analista_id="analista-123",
    )
    result = register_glosa_accept(inp)
    assert result.registered is True
    assert result.glosa_id.startswith("GLOSA-")


# ---------------------------------------------------------------------------
# notify_sla_risk
# ---------------------------------------------------------------------------


def test_notify_sla_risk() -> None:
    """notify_sla_risk alerts the coordination group."""
    result = notify_sla_risk("amh", "LOTE-009", sla_remaining="P2D")
    assert result["notified"] is True
    assert result["grupo"] == "coordenacao-contas"


# ---------------------------------------------------------------------------
# start_recurso
# ---------------------------------------------------------------------------


def test_start_recurso_handoff() -> None:
    """start_recurso creates handoff payload to SP-OP-RECURSO-001."""
    result = start_recurso(
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        glosa_type="administrativa",
        documentacao_anexa=True,
    )
    assert result["handoff"] == "SP-OP-RECURSO-001"
    assert result["glosa_existe"] is True
    assert result["glosa_id"] == "GLOSA-001"


# ---------------------------------------------------------------------------
# reconcile_payment
# ---------------------------------------------------------------------------


def test_reconcile_payment() -> None:
    """reconcile_payment reconciles a reenvio without adverse effect."""
    result = reconcile_payment("LOTE-010", "GUIDE-002")
    assert result["reconciled"] is True
    assert result["status"] == "REENVIAR"


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_contas() -> None:
    """publish emits a domain event."""
    result = publish("contas.completed", {"desfecho": "sem_glosa"})
    assert result["published"] is True
    assert "contas.completed" in result["topic"]


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_glosa_accept_not_human_is_permission_error() -> None:
    """GlosaAcceptNotHumanError must be a subclass of PermissionError."""
    assert issubclass(GlosaAcceptNotHumanError, PermissionError)


def test_contas_lote_invalido_is_value_error() -> None:
    """ContasLoteInvalidoError must be a subclass of ValueError."""
    assert issubclass(ContasLoteInvalidoError, ValueError)
