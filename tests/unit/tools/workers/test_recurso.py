"""Unit tests for maezo.tools.workers.recurso — SP-OP-RECURSO-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.recurso import (
    DesistenciaNotHumanError,
    RecursoDesistenciaInput,
    RecursoGlosaInvalidaError,
    RecursoInput,
    analyze_merits,
    analyze_request_entry,
    assess_eligibility,
    escalate_to_junta,
    notify_prestador,
    prepare_dossier,
    publish_completed,
    publish_completed_entry,
    register_desistencia,
    register_desistencia_entry,
    request_documents_entry,
    validate_recurso,
    validate_recurso_entry,
)

# ---------------------------------------------------------------------------
# validate_recurso
# ---------------------------------------------------------------------------


def test_validate_recurso_valid() -> None:
    """validate_recurso accepts a valid recurso with glosa confirmada."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        prestador_id="PREST-001",
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = validate_recurso(inp)
    assert result.valid is True
    assert result.glosa_existe is True


def test_validate_recurso_missing_glosa_id() -> None:
    """validate_recurso marks invalid when glosa_id is empty."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="",
        numero_guia_tiss="GUIDE-001",
    )
    result = validate_recurso(inp)
    assert result.valid is False
    assert result.glosa_existe is False


def test_validate_recurso_fora_prazo() -> None:
    """validate_recurso captures fora_prazo in errors but doesn't decide."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        glosa_existe=True,
        dentro_prazo_recurso=False,
        data_ciencia_glosa="2024-01-01",
    )
    result = validate_recurso(inp)
    assert "fora do prazo" in str(result.errors).lower() or len(result.errors) > 0


# ---------------------------------------------------------------------------
# assess_eligibility
# ---------------------------------------------------------------------------


def test_assess_eligibility_glosa_nao_existe() -> None:
    """Glosa not confirmed -> ANALISE_HUMANA, never auto-desistencia."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=False,
        glosa_existe=False,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = assess_eligibility(inp, validation)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "Glosa nao confirmada" in result.motivo


def test_assess_eligibility_tecnica_clinica_routes_auditor() -> None:
    """Glosa tecnica/clinica -> grupo_revisor = medico-auditor."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="clinica",
    )
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = assess_eligibility(inp, validation)
    assert result.grupo_revisor == "medico-auditor"


def test_assess_eligibility_segue_analise() -> None:
    """All conditions met -> SEGUE_ANALISE."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="administrativa",
    )
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = assess_eligibility(inp, validation)
    assert result.roteamento == "SEGUE_ANALISE"
    assert result.recorivel is True


def test_assess_eligibility_pendente_documentacao() -> None:
    """Missing documents -> PENDENTE_DOCUMENTACAO."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=False,
    )
    result = assess_eligibility(inp, validation)
    assert result.roteamento == "PENDENTE_DOCUMENTACAO"


# ---------------------------------------------------------------------------
# analyze_merits
# ---------------------------------------------------------------------------


def test_analyze_merits_never_decides() -> None:
    """analyze_merits returns dossie instructions, never decides."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="administrativa",
        valor_glosado_brl=500.0,
        codigo_procedimento_tuss="10101012",
    )
    result = analyze_merits(inp)
    assert result["glosa_id"] == "GLOSA-001"
    assert result["merito_sugerido"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------------------
# prepare_dossier
# ---------------------------------------------------------------------------


def test_prepare_dossier() -> None:
    """prepare_dossier combines validation and merits."""
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    merits = {"analise": "dossie_instruido"}
    result = prepare_dossier(validation, merits)
    assert "dossier" in result
    assert result["dossier"]["validacao"]["glosa_existe"] is True


# ---------------------------------------------------------------------------
# notify_prestador
# ---------------------------------------------------------------------------


def test_notify_prestador() -> None:
    """notify_prestador sends notification to the prestador."""
    result = notify_prestador("PREST-001", "GLOSA-001", message_type="pendencia_documentacao")
    assert result["notified"] is True
    assert result["prestador_id"] == "PREST-001"


# ---------------------------------------------------------------------------
# escalate_to_junta
# ---------------------------------------------------------------------------


def test_escalate_to_junta() -> None:
    """escalate_to_junta routes to medico-auditor."""
    result = escalate_to_junta("GLOSA-001", motivo="Glosa clinica complexa", glosa_type="clinica")
    assert result["escalated"] is True
    assert result["grupo"] == "medico-auditor"


# ---------------------------------------------------------------------------
# register_desistencia — GUARD tests
# ---------------------------------------------------------------------------


def test_desistencia_guard_missing_decisao() -> None:
    """register_desistencia raises if decisao_recurso != NAO_RECORRER."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="RECORRER",
        justificativa_desistencia="test",
        valor_glosa_aceito=100.0,
        referencia_contratual="CLAUSULA-1",
        analista_id="analista-1",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "decisao_recurso" in str(exc.value)


def test_desistencia_guard_missing_justificativa() -> None:
    """register_desistencia raises if justificativa_desistencia is empty."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="NAO_RECORRER",
        justificativa_desistencia="",
        valor_glosa_aceito=100.0,
        referencia_contratual="CLAUSULA-1",
        analista_id="analista-1",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "justificativa_desistencia" in str(exc.value)


def test_desistencia_guard_missing_referencia() -> None:
    """register_desistencia raises if referencia_contratual is empty."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="NAO_RECORRER",
        justificativa_desistencia="Motivo valido",
        valor_glosa_aceito=100.0,
        referencia_contratual="",
        analista_id="analista-1",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "referencia_contratual" in str(exc.value)


def test_desistencia_guard_missing_analista() -> None:
    """register_desistencia raises if analista_id is empty."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="NAO_RECORRER",
        justificativa_desistencia="Motivo valido",
        valor_glosa_aceito=100.0,
        referencia_contratual="CLAUSULA-1",
        analista_id="",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "analista_id" in str(exc.value)


def test_desistencia_success() -> None:
    """register_desistencia succeeds with all required fields."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="NAO_RECORRER",
        justificativa_desistencia="Glosa mantida conforme contrato",
        valor_glosa_aceito=500.0,
        referencia_contratual="CLAUSULA-12.3",
        analista_id="analista-456",
    )
    result = register_desistencia(inp)
    assert result.registered is True
    assert result.protocolo.startswith("RECDESIST-")


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_recurso() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="deferido")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "deferido"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_desistencia_not_human_is_permission_error() -> None:
    """DesistenciaNotHumanError must be a subclass of PermissionError."""
    assert issubclass(DesistenciaNotHumanError, PermissionError)


def test_recurso_glosa_invalida_is_value_error() -> None:
    """RecursoGlosaInvalidaError must be a subclass of ValueError."""
    assert issubclass(RecursoGlosaInvalidaError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_validate_recurso_entry_round_trips_validate_recurso() -> None:
    variables = {"glosa_id": "G-1", "glosa_existe": True, "dentro_prazo_recurso": True}
    direct = validate_recurso(RecursoInput(**variables))
    result = validate_recurso_entry(variables)
    assert result["valid"] == direct.valid
    assert result["glosa_existe"] == direct.glosa_existe


def test_request_documents_entry_round_trips_notify_prestador_default_pendencia() -> None:
    """notify_prestador's default message_type ("pendencia_documentacao") IS what makes this
    the request_documents topic's handler (see recurso.py bootstrap docstring)."""
    variables = {"prestador_id": "P-1", "glosa_id": "G-1"}
    assert request_documents_entry(variables) == notify_prestador("P-1", "G-1", "pendencia_documentacao")


def test_analyze_request_entry_round_trips_analyze_merits() -> None:
    variables = {"glosa_id": "G-1", "glosa_type": "tecnica", "valor_glosado_brl": 100.0}
    assert analyze_request_entry(variables) == analyze_merits(RecursoInput(**variables))


def test_register_desistencia_entry_guards_missing_human_decision() -> None:
    """register_desistencia_entry raises the UNCHANGED DesistenciaNotHumanError guard."""
    with pytest.raises(DesistenciaNotHumanError):
        register_desistencia_entry({"decisao_recurso": ""})


def test_register_desistencia_entry_happy_path() -> None:
    variables = {
        "decisao_recurso": "NAO_RECORRER",
        "justificativa_desistencia": "sem elementos novos",
        "valor_glosa_aceito": 100.0,
        "referencia_contratual": "clausula-1",
        "analista_id": "analista-1",
    }
    direct = register_desistencia(RecursoDesistenciaInput(**variables))
    result = register_desistencia_entry(variables)
    assert result["registered"] == direct.registered


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "recurso.completed", "desfecho": "deferido"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="recurso.completed", payload={}, desfecho="deferido"
    )
