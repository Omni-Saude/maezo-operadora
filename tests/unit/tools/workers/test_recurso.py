"""Unit tests for maezo.tools.workers.recurso — SP-OP-RECURSO-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
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


def _recurso_admissibility_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("recurso_admissibility", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


def _recurso_admissibility_and_eligibility_fake(
    *,
    adm_roteamento: str = "SEGUE_ANALISE",
    adm_motivo: str = "",
    elig_roteamento: str,
    grupo_revisor: str,
    elig_motivo: str = "",
) -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("recurso_admissibility", [{"roteamento": adm_roteamento, "motivo": adm_motivo}])
    fake.register(
        "recurso_eligibility",
        [{"roteamento": elig_roteamento, "grupo_revisor": grupo_revisor, "motivo": elig_motivo}],
    )
    return fake


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
    """Glosa not confirmed -> ANALISE_HUMANA, never auto-desistencia (live-verified motivo)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=False,
        glosa_existe=False,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_fake(
        roteamento="ANALISE_HUMANA",
        motivo=(
            "Glosa referida nao confirmada/ativa em CONTAS — analise humana obrigatoria "
            "(R5; nunca auto-desistencia)"
        ),
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "nao confirmada" in result.motivo
    # Terminal admissibility state (not SEGUE_ANALISE) — recurso_eligibility is NOT consulted.
    assert fake.calls == [
        (
            "recurso_admissibility",
            {
                "glosa_existe": False,
                "dentro_prazo_recurso": True,
                "documentacao_recurso_completa": True,
            },
        )
    ]


def test_assess_eligibility_tecnica_clinica_routes_auditor() -> None:
    """Glosa tecnica/clinica -> grupo_revisor = medico-auditor (via recurso_eligibility chain)."""
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
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="RECORRIVEL", grupo_revisor="medico-auditor"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.grupo_revisor == "medico-auditor"


def test_assess_eligibility_segue_analise() -> None:
    """All conditions met -> SEGUE_ANALISE + recorivel derived from recurso_eligibility."""
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
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="RECORRIVEL", grupo_revisor="analista-recurso-glosa"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "SEGUE_ANALISE"
    assert result.recorivel is True
    assert fake.calls == [
        (
            "recurso_admissibility",
            {
                "glosa_existe": True,
                "dentro_prazo_recurso": True,
                "documentacao_recurso_completa": True,
            },
        ),
        (
            "recurso_eligibility",
            {"glosa_type": "administrativa", "glosa_reason_code": "", "valor_glosado_brl": 0.0},
        ),
    ]


def test_assess_eligibility_pendente_documentacao() -> None:
    """Missing documents -> PENDENTE_DOCUMENTACAO (terminal — eligibility not consulted)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=False,
    )
    fake = _recurso_admissibility_fake(roteamento="PENDENTE_DOCUMENTACAO")
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "PENDENTE_DOCUMENTACAO"
    assert len(fake.calls) == 1  # eligibility NOT consulted for a terminal admissibility state


def test_assess_eligibility_unmapped_glosa_type_not_recorivel() -> None:
    """golden-parity finding (T1.5, live-verified): recurso_eligibility's catch-all (unmapped
    glosa_type) returns ANALISE_HUMANA, not RECORRIVEL — the old Python's hand-coded
    tecnica/clinica-only split never modeled this third possibility (it derived `recorivel`
    purely from admissibility, never from the real eligibility table)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001", glosa_type="outra")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="ANALISE_HUMANA", grupo_revisor="analista-recurso-glosa"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "SEGUE_ANALISE"  # admissibility still says proceed
    assert result.recorivel is False  # but eligibility's own signal says not (yet) recorrivel


def test_assess_eligibility_dmn_unwired_raises_dmn_evaluation_error() -> None:
    from maezo.tools.workers.recurso import RecursoValidationResult

    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    validation = RecursoValidationResult(glosa_existe=True)
    with pytest.raises(DmnEvaluationError):
        assess_eligibility(inp, validation, dmn=None)


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
# register_desistencia — auditor channel (finding 4, t3.1-recurso-findings-a)
# ---------------------------------------------------------------------------


def test_desistencia_guard_neither_channel() -> None:
    """register_desistencia raises if NEITHER the analista nor the auditor channel matches."""
    inp = RecursoDesistenciaInput(
        decisao_recurso="",
        decisao_auditor_recurso="",
        justificativa_desistencia="Motivo valido",
        valor_glosa_aceito=100.0,
        referencia_contratual="CLAUSULA-1",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "decisao_recurso" in str(exc.value)
    assert "decisao_auditor_recurso" in str(exc.value)


def test_desistencia_guard_auditor_missing_auditor_id() -> None:
    """register_desistencia raises if the auditor channel is attempted (ACEITAR_GLOSA) but
    auditor_id is empty — same fail-closed shape as the analista channel's missing analista_id."""
    inp = RecursoDesistenciaInput(
        decisao_auditor_recurso="ACEITAR_GLOSA",
        justificativa_desistencia="Merito tecnico confirma a glosa",
        valor_glosa_aceito=150.0,
        referencia_contratual="Diretriz DUT",
        auditor_id="",
    )
    with pytest.raises(DesistenciaNotHumanError) as exc:
        register_desistencia(inp)
    assert "ERR_DESISTENCIA_NOT_HUMAN" in str(exc.value)
    assert "auditor_id" in str(exc.value)
    assert "analista_id" not in str(exc.value), (
        "o canal auditor foi o atacado — a mensagem nao deve reclamar de analista_id"
    )


def test_desistencia_success_auditor_channel() -> None:
    """register_desistencia succeeds via the auditor channel (ACEITAR_GLOSA + auditor_id) —
    ST_RegisterGlosaMantida routes here on the SAME topic as the analista's NAO_RECORRER path."""
    inp = RecursoDesistenciaInput(
        decisao_auditor_recurso="ACEITAR_GLOSA",
        justificativa_desistencia="Merito tecnico-clinico confirma a glosa",
        valor_glosa_aceito=150.0,
        referencia_contratual="Diretriz clinica DUT",
        auditor_id="auditor-456",
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


def test_register_desistencia_entry_happy_path_auditor_channel() -> None:
    """register_desistencia_entry round-trips the auditor channel (finding 4) — `pick_fields`
    picks up `decisao_auditor_recurso`/`auditor_id` off the flat BPMN variables dict."""
    variables = {
        "decisao_auditor_recurso": "ACEITAR_GLOSA",
        "justificativa_desistencia": "merito tecnico-clinico confirma a glosa",
        "valor_glosa_aceito": 150.0,
        "referencia_contratual": "diretriz DUT",
        "auditor_id": "auditor-1",
    }
    direct = register_desistencia(RecursoDesistenciaInput(**variables))
    result = register_desistencia_entry(variables)
    assert result["registered"] == direct.registered is True


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "recurso.completed", "desfecho": "deferido"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="recurso.completed", payload={}, desfecho="deferido"
    )
