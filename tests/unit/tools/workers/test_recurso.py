"""Unit tests for maezo.tools.workers.recurso — SP-OP-RECURSO-001.

TDD London School: tests exercise the external task contracts.

PERSPECTIVE (ADR-0040): the process owner is the OPERADORA. She RECEIVES the glosa appeal the
prestador filed and ANSWERS it. There is no `submit_appeal`, no `track_status` and no
`reconcile_payment` here any more — those were the appellant's acts, and they were deleted, not
renamed. What replaces them is `comunicar_resposta` (the payer's answer) and `handoff_pagamento`
(the payment order for a glosa the payer reversed).
"""

import asyncio
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import structlog

from maezo.agents.andre.keys import key_segment
from maezo.gateway.audit import AuditRecord
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    ProcessInstance,
    StartDedupGateUnavailableError,
    start_dedup_key,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import (
    AUDIT_AGENT_ID,
    ExternalTask,
    FakeKafkaPublisher,
    WorkerBpmnError,
)
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS
from maezo.tools.workers.recurso import (
    COMUNICACAO_TIPOS,
    FONTES_VALOR_PERMITIDAS,
    HANDOFF_PAGTO_FORBIDDEN_KEYS,
    HANDOFF_PAGTO_SEEDED_KEYS,
    LASTRO_ORIGEM_RECURSO,
    PAGTO_PROCESS_KEY,
    RECURSO_BPMN_ERROR_ALLOWLIST,
    ComunicarRespostaInput,
    EscalateAnsTimeoutInput,
    NotifySlaRiskInput,
    RecursoHandoffPagamentoInvalidoError,
    RecursoIndeferimentoInput,
    RecursoIndeferimentoNotHumanError,
    RecursoInput,
    _mint_protocolo_resposta,
    _ordem_pagamento_id,
    _pagto_business_key,
    _require_glosa_id,
    analyze_merits,
    analyze_request_entry,
    assess_eligibility,
    comunicar_resposta,
    escalate_ans_timeout,
    escalate_to_junta,
    handoff_pagamento,
    handoff_pagamento_entry,
    make_comunicar_resposta_handler,
    make_escalate_ans_timeout_handler,
    make_notify_sla_risk_handler,
    notify_prestador,
    notify_sla_risk,
    prepare_dossier,
    publish_completed,
    publish_completed_entry,
    registrar_indeferimento,
    registrar_indeferimento_entry,
    request_documents_entry,
    validate_recurso,
    validate_recurso_entry,
)
from tests.support.audit_fakes import FakeStartAuditSink


def _task(
    *,
    topic: str = "operadora.recurso.probe",
    business_key: str = "RECURSO-amh-GUIA-1-GLOSA-1",
    variables: dict | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
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
# validate_recurso — the INTAKE task (ST_ValidarRecurso)
# ---------------------------------------------------------------------------


def test_validate_recurso_valid() -> None:
    """validate_recurso accepts a valid recurso with glosa confirmada, and ECHOES the normalised
    anchor back as a process variable (recurso_sla's FEEL has no other branch to fall into)."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        prestador_id="PREST-001",
        data_recebimento_recurso_iso="2026-05-20",
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = validate_recurso(inp)
    assert result.valid is True
    assert result.glosa_existe is True
    assert result.data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_missing_glosa_id() -> None:
    """validate_recurso marks invalid when glosa_id is empty (the typed function still runs; the
    dict-boundary entry is where the ERR_RECURSO_INVALID_GLOSA guard fires first)."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="",
        numero_guia_tiss="GUIDE-001",
        data_recebimento_recurso_iso="2026-05-20",
        dentro_prazo_recurso=True,
    )
    result = validate_recurso(inp)
    assert result.valid is False
    assert result.glosa_existe is False


def test_validate_recurso_fora_prazo() -> None:
    """validate_recurso captures fora_prazo in errors but doesn't decide, and the anchor it
    normalises is the OPERADORA's receipt date — never the prestador's ciencia date."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        glosa_existe=True,
        dentro_prazo_recurso=False,
        data_ciencia_alegada_prestador="2024-01-01",
        data_recebimento_recurso_iso="2026-05-20",
    )
    result = validate_recurso(inp)
    assert "fora do prazo" in str(result.errors).lower()
    assert result.data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_normaliza_data_recebimento_ausente_com_warning() -> None:
    """GAP-RECURSO-1 (ADR-0040 §2.3): the anchor is defaulted FAIL-SAFE to TODAY/UTC when absent
    — never silently, and NEVER to the appellant's own alleged ciencia date (which is exactly the
    perspective swap this redesign removes: an earlier date would be a plausible-looking value
    that belongs to the other party's clock)."""
    from datetime import UTC, datetime

    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        data_ciencia_alegada_prestador="2024-01-01",
        data_recebimento_recurso_iso="",
        glosa_existe=True,
        dentro_prazo_recurso=True,
    )
    with structlog.testing.capture_logs() as logs:
        result = validate_recurso(inp)
    assert result.data_recebimento_recurso_iso == datetime.now(UTC).strftime("%Y-%m-%d")
    assert result.data_recebimento_recurso_iso != "2024-01-01"
    warnings = [
        entry for entry in logs if entry.get("event") == "recurso.validate_recurso.data_recebimento_defaulted"
    ]
    assert warnings, "o default fail-safe DEVE ser logado — nunca um fallback silencioso"
    assert warnings[0]["log_level"] == "warning"


@pytest.mark.parametrize("raw", ["", "   ", "nao-e-uma-data", "20/05/2026"])
def test_validate_recurso_normaliza_data_recebimento_invalida(raw: str) -> None:
    """Any unusable shape defaults with a warning — the DMN must never see null/garbage."""
    from datetime import UTC, datetime

    inp = RecursoInput(tenant_id="amh", glosa_id="G-1", data_recebimento_recurso_iso=raw)
    with structlog.testing.capture_logs() as logs:
        result = validate_recurso(inp)
    assert result.data_recebimento_recurso_iso == datetime.now(UTC).strftime("%Y-%m-%d")
    assert any(e.get("event") == "recurso.validate_recurso.data_recebimento_defaulted" for e in logs)


def test_validate_recurso_normaliza_datetime_iso_para_data_calendario() -> None:
    """A full ISO datetime is trimmed to the calendar date the DMN concatenates 'T00:00:00' to."""
    inp = RecursoInput(tenant_id="amh", glosa_id="G-1", data_recebimento_recurso_iso="2026-05-20T09:31:00Z")
    assert validate_recurso(inp).data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_raises_worker_bpmn_error_when_glosa_id_absent() -> None:
    """THIRD site of ERR_RECURSO_INVALID_GLOSA and the FIRST on every path: an origin-invalid
    appeal never reaches BRT_Admissibilidade (BE_GlosaInvalidaValidacao boundary catch)."""
    with pytest.raises(WorkerBpmnError) as exc:
        validate_recurso_entry({"tenant_id": "amh", "glosa_id": "  "})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


# ---------------------------------------------------------------------------
# assess_eligibility
# ---------------------------------------------------------------------------


def test_assess_eligibility_glosa_nao_existe() -> None:
    """Glosa not confirmed -> ANALISE_HUMANA, never auto-inadmissibility (live-verified motivo)."""
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
            "(R5; nunca inadmissibilidade automatica)"
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
        elig_roteamento="SEGUE_MERITO", grupo_revisor="medico-auditor"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.grupo_revisor == "medico-auditor"


def test_assess_eligibility_segue_analise() -> None:
    """All conditions met -> SEGUE_ANALISE + segue_merito derived from recurso_eligibility."""
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
        elig_roteamento="SEGUE_MERITO", grupo_revisor="analista-recurso-glosa"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "SEGUE_ANALISE"
    assert result.segue_merito is True
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


def test_assess_eligibility_unmapped_glosa_type_nao_segue_merito() -> None:
    """golden-parity finding (T1.5, live-verified): recurso_eligibility's catch-all (unmapped
    glosa_type) returns ANALISE_HUMANA, not SEGUE_MERITO — the old Python's hand-coded
    tecnica/clinica-only split never modeled this third possibility (it derived the flag purely
    from admissibility, never from the real eligibility table)."""
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
    assert result.segue_merito is False  # but eligibility's own signal says: human decides


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
# registrar_indeferimento — GUARD tests (ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN)
# ---------------------------------------------------------------------------


def _indeferimento_input(**over: object) -> RecursoIndeferimentoInput:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "Sem elementos novos que afastem a glosa",
        "valor_glosa_mantido_brl": 100.0,
        "referencia_contratual": "CLAUSULA-1",
        "analista_id": "analista-1",
    }
    base.update(over)
    return RecursoIndeferimentoInput(**base)  # type: ignore[arg-type]


def test_indeferimento_guard_missing_decisao() -> None:
    """registrar_indeferimento raises if decisao_recurso is not an adverse human decision."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(decisao_recurso="SOLICITAR_INFO"))
    assert "decisao_recurso" in str(exc.value)


def test_indeferimento_guard_recusa_decisao_deferir() -> None:
    """DEFERIR is FAVOURABLE — it must never reach the adverse-effect worker. The gateway routes
    it to `ST_ComunicarDeferimento`; a forged direct dispatch of this topic refuses."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(decisao_recurso="DEFERIR"))
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc.value)


def test_indeferimento_guard_missing_fundamentacao() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(fundamentacao_indeferimento=""))
    assert "fundamentacao_indeferimento" in str(exc.value)


def test_indeferimento_guard_missing_referencia() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(referencia_contratual=""))
    assert "referencia_contratual" in str(exc.value)


def test_indeferimento_guard_missing_analista() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(analista_id=""))
    assert "analista_id" in str(exc.value)


@pytest.mark.parametrize("valor", ["", "   ", "nao-numerico", 0.0, -1.0, None, True])
def test_indeferimento_guard_valor_mantido_fail_closed(valor: object) -> None:
    """`_parse_valor_monetario` NEVER defaults to 0 — a wrong monetary decision on a glosa is a
    financial defect, so an unusable amount is treated as a MISSING required field."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(valor_glosa_mantido_brl=valor))
    assert "valor_glosa_mantido_brl" in str(exc.value)


def test_indeferimento_success() -> None:
    """registrar_indeferimento succeeds with all required fields, minting a DETERMINISTIC
    protocolo from the instance's own business identity (no wall clock, no uuid)."""
    result = registrar_indeferimento(_indeferimento_input())
    assert result.registered is True
    assert result.protocolo == "RECIND-RECURSO-amh-GUIA-1-GLOSA-1"
    assert result.protocolo == registrar_indeferimento(_indeferimento_input()).protocolo


def test_indeferimento_aceita_deferir_parcial() -> None:
    """DEFERIR_PARCIAL is L0 adverse (part of the glosa is MAINTAINED against the prestador) —
    same guarded worker, with `valor_deferido_brl` additionally required."""
    result = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="DEFERIR_PARCIAL",
            valor_glosa_mantido_brl=40.0,
            valor_deferido_brl=60.0,
            valor_glosado_brl=100.0,
        )
    )
    assert result.registered is True


def test_indeferimento_deferir_parcial_exige_valor_deferido() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="DEFERIR_PARCIAL",
                valor_glosa_mantido_brl=40.0,
                valor_deferido_brl="",
                valor_glosado_brl=100.0,
            )
        )
    assert "valor_deferido_brl" in str(exc.value)


def test_indeferimento_deferir_parcial_soma_confere() -> None:
    """`valor_deferido + valor_glosa_mantido == valor_glosado`, in INTEGER CENTS with exact
    equality. Pure arithmetic, not a business rule: comparing floats would fail spuriously on
    exact amounts (0.1 + 0.2 != 0.3). A sum that does not close REFUSES — the worker never rounds
    on its own authority (OQ-R2: if the operation uses a tolerance, that is an SME decision)."""
    # Closes exactly at a value where float arithmetic is treacherous (0.1 + 0.2).
    ok = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="DEFERIR_PARCIAL",
            valor_deferido_brl=0.1,
            valor_glosa_mantido_brl=0.2,
            valor_glosado_brl=0.3,
        )
    )
    assert ok.registered is True

    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="DEFERIR_PARCIAL",
                valor_deferido_brl=60.0,
                valor_glosa_mantido_brl=41.0,  # one real off
                valor_glosado_brl=100.0,
            )
        )
    assert "soma nao fecha" in str(exc.value)


# ---------------------------------------------------------------------------
# registrar_indeferimento — auditor channel (finding 4, t3.1-recurso-findings-a)
# ---------------------------------------------------------------------------


def test_indeferimento_guard_neither_channel() -> None:
    """Raises if NEITHER the analista nor the auditor channel matches."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(decisao_recurso="", decisao_auditor_recurso="", analista_id="")
        )
    assert "decisao_recurso" in str(exc.value)
    assert "decisao_auditor_recurso" in str(exc.value)


def test_indeferimento_guard_auditor_missing_auditor_id() -> None:
    """The auditor channel is attempted (INDEFERIR) but auditor_id is empty — same fail-closed
    shape as the analista channel's missing analista_id."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="",
                analista_id="",
                decisao_auditor_recurso="INDEFERIR",
                fundamentacao_indeferimento="Merito tecnico confirma a glosa",
                valor_glosa_mantido_brl=150.0,
                referencia_contratual="Diretriz DUT",
                auditor_id="",
            )
        )
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc.value)
    assert "auditor_id" in str(exc.value)
    assert "analista_id" not in str(exc.value), (
        "o canal auditor foi o atacado — a mensagem nao deve reclamar de analista_id"
    )


def test_indeferimento_success_auditor_channel() -> None:
    """Succeeds via the auditor channel (INDEFERIR + auditor_id) — ST_RegistrarIndeferimentoAuditor
    routes here on the SAME topic as the analista's INDEFERIR path."""
    result = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="",
            analista_id="",
            decisao_auditor_recurso="INDEFERIR",
            fundamentacao_indeferimento="Merito tecnico-clinico confirma a glosa",
            valor_glosa_mantido_brl=150.0,
            referencia_contratual="Diretriz clinica DUT",
            auditor_id="auditor-1",
        )
    )
    assert result.registered is True
    assert result.protocolo.startswith("RECIND-")


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_recurso() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="deferido_humano")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "deferido_humano"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_recurso_indeferimento_not_human_is_permission_error() -> None:
    """`RecursoIndeferimentoNotHumanError` must be a subclass of `PermissionError` — that is what
    makes `harness._handle` classify it as an AUDITED REFUSAL (ADR-0030 §4), and the errorCode's
    `_NOT_HUMAN` suffix is what `harness.is_guard_refusal_code` recognises."""
    assert issubclass(RecursoIndeferimentoNotHumanError, PermissionError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_validate_recurso_entry_round_trips_validate_recurso() -> None:
    variables = {
        "glosa_id": "G-1",
        "glosa_existe": True,
        "dentro_prazo_recurso": True,
        "data_recebimento_recurso_iso": "2026-05-20",
    }
    direct = validate_recurso(RecursoInput(**variables))
    result = validate_recurso_entry(variables)
    assert result["valid"] == direct.valid
    assert result["glosa_existe"] == direct.glosa_existe
    assert result["data_recebimento_recurso_iso"] == "2026-05-20"
    # m8: ST_ValidarRecurso is the FIRST service task of EVERY path, so this is the first
    # completion payload the engine sees. No value in it may be a container.
    assert "errors" not in result
    assert result["errors_validacao"] == ""
    assert all(isinstance(v, str | bool) for v in result.values()), result


def test_validate_recurso_entry_escreve_de_volta_a_ancora_quando_ausente_do_start() -> None:
    """GAP-RECURSO-1, metade ENGINE-INDEPENDENTE de `test_prazo_max_ancora_defaultada_pelo_intake`.

    O cenario vivo remove `data_recebimento_recurso_iso` das variaveis de start (o fixture
    dropa `None`), e o BPMN nao tem mais fail-safe para a data do RECORRENTE: se o intake nao
    escrevesse a ancora de volta, `recurso_sla` receberia nulo e os tres boundary de teto
    (`timeDate ${sla.prazo_max_absoluto_iso}`) nao teriam instante — a instancia nem alcancaria
    a User Task. Aqui a chave esta AUSENTE do dict (nao vazia): `pick_fields` cai no default do
    dataclass, o worker defaulta fail-safe para HOJE/UTC com warning e a ancora SAI no payload
    de completion, que e como ela vira variavel de processo.

    A data alegada pelo prestador nao pode virar a ancora: e o relogio do recorrente, e a
    tempestividade continua sendo o fato separado `dentro_prazo_recurso`.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415 — convencao local deste modulo

    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    ciencia = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

    with structlog.testing.capture_logs() as logs:
        result = validate_recurso_entry(
            {
                "glosa_id": "G-1",
                "glosa_existe": True,
                "dentro_prazo_recurso": True,
                "data_ciencia_alegada_prestador": ciencia,
            }
        )

    assert result["data_recebimento_recurso_iso"] == hoje
    assert result["data_recebimento_recurso_iso"] != ciencia
    warnings = [
        entry for entry in logs if entry.get("event") == "recurso.validate_recurso.data_recebimento_defaulted"
    ]
    assert warnings, "o default fail-safe da ancora TEM de logar warning — nunca silencioso"
    assert warnings[0]["log_level"] == "warning"


def test_validate_recurso_entry_achata_erros_em_escalar() -> None:
    """m8: the diagnosis survives, as a SCALAR — never a `list[str]` process variable."""
    result = validate_recurso_entry({"glosa_id": "G-1", "glosa_existe": True, "dentro_prazo_recurso": False})
    assert result["errors_validacao"] == "fora do prazo recursal"
    assert not isinstance(result["errors_validacao"], list)


def test_request_documents_entry_round_trips_notify_prestador_default_pendencia() -> None:
    """notify_prestador's default message_type ("pendencia_documentacao") IS what makes this
    the request_documents topic's handler (see recurso.py bootstrap docstring)."""
    variables = {"prestador_id": "P-1", "glosa_id": "G-1"}
    assert request_documents_entry(variables) == notify_prestador("P-1", "G-1", "pendencia_documentacao")


def test_analyze_request_entry_round_trips_analyze_merits() -> None:
    variables = {"glosa_id": "G-1", "glosa_type": "tecnica", "valor_glosado_brl": 100.0}
    assert analyze_request_entry(variables) == analyze_merits(RecursoInput(**variables))


# ---------------------------------------------------------------------------
# GAP-RECURSO-3 / Finding 5 — ERR_RECURSO_INVALID_GLOSA guard (WorkerBpmnError, NOT ValueError)
# ---------------------------------------------------------------------------


def test_require_glosa_id_raises_worker_bpmn_error_when_absent() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        _require_glosa_id("")
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_require_glosa_id_raises_worker_bpmn_error_when_whitespace_only() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        _require_glosa_id("   ")
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_require_glosa_id_accepts_non_empty() -> None:
    _require_glosa_id("GLOSA-001")  # must not raise


def test_request_documents_entry_raises_before_notifying_prestador_when_glosa_id_absent() -> None:
    """The guard fires BEFORE `notify_prestador` — test-spec invariant
    (`test_glosa_id_ausente_pendencia_termina_limpo_sem_incidente_travado`: no
    `notifications_of_type("recurso.request_documents")` may be observed)."""
    with pytest.raises(WorkerBpmnError) as exc:
        request_documents_entry({"prestador_id": "P-1", "glosa_id": ""})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_request_documents_entry_raises_when_glosa_id_missing_entirely() -> None:
    with pytest.raises(WorkerBpmnError):
        request_documents_entry({"prestador_id": "P-1"})


def test_analyze_request_entry_raises_before_analyzing_merits_when_glosa_id_absent() -> None:
    """Test-spec invariant (`test_glosa_id_ausente_analise_termina_limpo_sem_incidente_travado`):
    no `notifications_of_type("recurso.analyze_request")` may be observed."""
    with pytest.raises(WorkerBpmnError) as exc:
        analyze_request_entry({"glosa_id": "", "glosa_type": "tecnica"})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_recurso_bpmn_error_allowlist_is_exactly_invalid_glosa() -> None:
    """`RECURSO_BPMN_ERROR_ALLOWLIST` is unioned into `worker_runtime/service.py`'s production
    allowlist (SHARED FILE) — pin its exact membership here so a drift is caught locally too."""
    assert frozenset({"ERR_RECURSO_INVALID_GLOSA"}) == RECURSO_BPMN_ERROR_ALLOWLIST


def test_registrar_indeferimento_entry_guards_missing_human_decision() -> None:
    """registrar_indeferimento_entry raises the UNCHANGED two-channel guard."""
    with pytest.raises(RecursoIndeferimentoNotHumanError):
        registrar_indeferimento_entry({"decisao_recurso": ""})


def test_registrar_indeferimento_entry_happy_path() -> None:
    variables = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "sem elementos novos",
        "valor_glosa_mantido_brl": 100.0,
        "referencia_contratual": "clausula-1",
        "analista_id": "analista-1",
    }
    direct = registrar_indeferimento(RecursoIndeferimentoInput(**variables))
    result = registrar_indeferimento_entry(variables)
    assert result["registered"] == direct.registered
    assert result["protocolo"] == direct.protocolo


def test_registrar_indeferimento_entry_happy_path_auditor_channel() -> None:
    """Round-trips the auditor channel (finding 4) — `pick_fields` picks up
    `decisao_auditor_recurso`/`auditor_id` off the flat BPMN variables dict."""
    variables = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_auditor_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "merito tecnico-clinico confirma a glosa",
        "valor_glosa_mantido_brl": 150.0,
        "referencia_contratual": "diretriz DUT",
        "auditor_id": "auditor-1",
    }
    direct = registrar_indeferimento(RecursoIndeferimentoInput(**variables))
    result = registrar_indeferimento_entry(variables)
    assert result["registered"] == direct.registered is True


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "recurso.completed", "desfecho": "deferido_humano"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="recurso.completed", payload={}, desfecho="deferido_humano"
    )


# ---------------------------------------------------------------------------
# Raw-handler workers
# ---------------------------------------------------------------------------

# ----- notify_sla_risk (BT_AlertaSlaRecurso -> ST_NotificarRiscoSla) --------------------------


def test_notify_sla_risk_happy_path() -> None:
    """Informational only — no decision made/altered."""
    inp = NotifySlaRiskInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    result = notify_sla_risk(inp)
    assert result["sla_risk_notified"] is True
    assert result["glosa_id"] == "GLOSA-1"


def test_notify_sla_risk_missing_input_defaults_safe() -> None:
    """A blank input still completes (informational-only, non-adverse) — no exception."""
    result = notify_sla_risk(NotifySlaRiskInput())
    assert result["sla_risk_notified"] is True
    assert result["glosa_id"] == ""


async def test_make_notify_sla_risk_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        topic="operadora.recurso.notify_sla_risk",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "glosa_type": "administrativa",
        },
    )
    result = await handler(task)
    assert result["sla_risk_notified"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.notify_sla_risk"
    assert payload["glosa_id"] == "GLOSA-1"
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — the coordenacao
    # alert is this task's only effect; a swallowed failure would fabricate success.
    assert kafka.best_effort_calls == [False]


class _FailingPublisher:
    """Raising `KafkaPublisher` double (records the posture) — for the raw-propagate pins."""

    def __init__(self) -> None:
        self.best_effort_calls: list[bool | None] = []

    async def publish(
        self,
        topic: str,
        value: dict,
        *,
        key: str | None = None,
        best_effort: bool | None = None,
    ) -> bool:
        del topic, value, key
        self.best_effort_calls.append(best_effort)
        raise RuntimeError("kafka unavailable (test)")


async def test_make_notify_sla_risk_handler_publish_failure_propagates_raw() -> None:
    """No BPMN boundary on ST_NotificarRiscoSla -> raw propagate (harness retry/incident,
    ADR-0030); contained to the non-interrupting BT_AlertaSlaRecurso side branch."""
    kafka = _FailingPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        topic="operadora.recurso.notify_sla_risk",
        variables={"tenant_id": "amh", "glosa_id": "GLOSA-1"},
    )
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    assert kafka.best_effort_calls == [False]


async def test_make_notify_sla_risk_handler_fail_closed_default_no_producer() -> None:
    """kafka=None: completes anyway (informational-only task must not block the timer path),
    never fabricates a publish."""
    handler = make_notify_sla_risk_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["sla_risk_notified"] is True


# ----- escalate_ans_timeout (BT_PrazoMax* -> ST_EscalateAnsTimeout) ---------------------------


def test_escalate_ans_timeout_happy_path() -> None:
    inp = EscalateAnsTimeoutInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    result = escalate_ans_timeout(inp)
    assert result["escalated"] is True
    assert result["fase"] == "prazo_max"


def test_escalate_ans_timeout_never_touches_ans_gateway() -> None:
    """'ANS' in the topic name is HISTORICAL — grep-proof no import of ans_gateway anywhere in
    recurso.py (AST-based: prose mentions in docstrings/comments don't count, only real
    `import`/`from ... import` statements)."""
    import ast

    import maezo.tools.workers.recurso as recurso_module

    with open(recurso_module.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            imported_roots.update(alias.name for alias in node.names)
    assert "ans_gateway" not in imported_roots


async def test_make_escalate_ans_timeout_handler_publishes_domain_event_prazo_max() -> None:
    """has_event(_RECURSO_SLA_BREACHED, fase="prazo_max") — a REAL domain event (not merely an
    internal notification), matching the BPMN's own embedded event_topic_breach inputParameter."""
    kafka = FakeKafkaPublisher()
    handler = make_escalate_ans_timeout_handler(kafka)
    task = _task(
        topic="operadora.recurso.escalate_ans_timeout",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "glosa_type": "administrativa",
            "event_topic_breach": "agents.events.recurso.sla_breached",
        },
    )
    result = await handler(task)
    assert result["escalated"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "agents.events.recurso.sla_breached"
    assert payload["fase"] == "prazo_max"
    assert payload["glosa_id"] == "GLOSA-1"
    assert key == task.business_key


async def test_make_escalate_ans_timeout_handler_defaults_event_topic_when_absent() -> None:
    """Defensive default when `event_topic_breach` is missing from variables (should never
    happen against the real BPMN, which always carries it)."""
    kafka = FakeKafkaPublisher()
    handler = make_escalate_ans_timeout_handler(kafka)
    await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    topic, _payload, _key = kafka.published[0]
    assert topic == "agents.events.recurso.sla_breached"


async def test_make_escalate_ans_timeout_handler_fail_closed_default_no_producer() -> None:
    handler = make_escalate_ans_timeout_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["escalated"] is True


# ----- comunicar_resposta (the 5 terminal ST_Comunicar* tasks) --------------------------------


def test_comunicar_resposta_protocolo_is_deterministic_by_business_key() -> None:
    """ADR-0030/T-H determinism: the SAME business key always mints the IDENTICAL protocol — no
    time_ns/uuid/random (mirrors LabeledMockAnsGatewayTransport's MOCK-...-{business_key}). This
    is the ONE idiom that survives from the deleted appellant branch, because it is a real
    retry-idempotency fix, not vocabulary."""
    bk = "RECURSO-amh-GUIA-1-GLOSA-1"
    first = _mint_protocolo_resposta(bk, "amh", "GUIA-1", "GLOSA-1")
    second = _mint_protocolo_resposta(bk, "amh", "GUIA-1", "GLOSA-1")
    assert first == second
    assert first == f"RECRESP-{bk}"


def test_comunicar_resposta_protocolo_differs_by_business_key() -> None:
    a = _mint_protocolo_resposta("RECURSO-amh-G-1-X", "amh", "G-1", "X")
    b = _mint_protocolo_resposta("RECURSO-amh-G-2-Y", "amh", "G-2", "Y")
    assert a != b


def test_comunicar_resposta_protocolo_falls_back_to_composed_key_when_blank() -> None:
    """Defensive fallback (production always carries business_key) — still deterministic."""
    result = _mint_protocolo_resposta("", "amh", "GUIA-1", "GLOSA-1")
    assert result == "RECRESP-RECURSO-amh-GUIA-1-GLOSA-1"
    assert result == _mint_protocolo_resposta("  ", "amh", "GUIA-1", "GLOSA-1")


def test_comunicar_resposta_protocolo_never_uses_time_or_random() -> None:
    """Static proof (independent of the shared arch-test baseline fence) that THIS specific
    function's BODY (not its docstring prose) contains no nondeterministic call — AST-based,
    mirrors test_worker_handler_purity.py's `_nondeterministic_calls` detector."""
    import ast
    import inspect

    nondet_dotted = {"time.time_ns", "time.time", "uuid.uuid4", "uuid.uuid1", "uuid.uuid3", "uuid.uuid5"}
    nondet_roots = {"random", "secrets", "os"}

    tree = ast.parse(inspect.getsource(_mint_protocolo_resposta))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in nondet_dotted or node.value.id in nondet_roots:
                hits.add(dotted)
    assert not hits, f"nondeterministic call(s) found in _mint_protocolo_resposta: {hits}"


def test_comunicar_resposta_serve_os_quatro_desfechos() -> None:
    """ONE worker serves every terminal that touches the prestador — the payer answers in ALL of
    them, favourable or adverse. The tipo is carried by the BPMN's own inputParameter; the worker
    never derives it (it holds no decision)."""
    seen = set()
    for tipo in sorted(COMUNICACAO_TIPOS):
        result = comunicar_resposta(
            ComunicarRespostaInput(
                tenant_id="amh",
                numero_guia_tiss="GUIA-1",
                glosa_id="GLOSA-1",
                prestador_id="PREST-1",
                tipo_comunicacao=tipo,
            ),
            business_key="RECURSO-amh-GUIA-1-GLOSA-1",
        )
        assert result["comunicado"] is True
        assert result["tipo_comunicacao"] == tipo
        assert result["protocolo_resposta_recurso"] == "RECRESP-RECURSO-amh-GUIA-1-GLOSA-1"
        seen.add(tipo)
    assert seen == set(COMUNICACAO_TIPOS)
    assert (
        frozenset({"deferimento", "deferimento_parcial", "indeferimento", "inadmissibilidade"})
        == COMUNICACAO_TIPOS
    )


async def test_make_comunicar_resposta_handler_publishes_notification_and_result() -> None:
    """`test_todos_os_fins_comunicam_o_prestador`'s worker-side half:
    notifications_of_type("recurso.comunicar_resposta") must be observable."""
    kafka = FakeKafkaPublisher()
    handler = make_comunicar_resposta_handler(kafka)
    task = _task(
        topic="operadora.recurso.comunicar_resposta",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "prestador_id": "PREST-1",
            "tipo_comunicacao": "indeferimento",
        },
    )
    result = await handler(task)
    assert result["protocolo_resposta_recurso"] == "RECRESP-RECURSO-amh-G-1-GLOSA-1"
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.comunicar_resposta"
    assert payload["tipo_comunicacao"] == "indeferimento"
    assert payload["protocolo_resposta_recurso"] == result["protocolo_resposta_recurso"]
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — the prestador is
    # ENTITLED to this answer; silent loss is unacceptable.
    assert kafka.best_effort_calls == [False]


async def test_make_comunicar_resposta_handler_publish_failure_propagates_raw() -> None:
    """No BPMN boundary on any ST_Comunicar* -> raw propagate (harness retry/incident, ADR-0030);
    re-dispatch republishes the IDENTICAL fact (deterministic protocolo minting)."""
    kafka = _FailingPublisher()
    handler = make_comunicar_resposta_handler(kafka)
    task = _task(
        topic="operadora.recurso.comunicar_resposta",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={"tenant_id": "amh", "numero_guia_tiss": "G-1", "glosa_id": "GLOSA-1"},
    )
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    assert kafka.best_effort_calls == [False]


async def test_make_comunicar_resposta_handler_fail_closed_default_no_producer() -> None:
    handler = make_comunicar_resposta_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["comunicado"] is True
    assert result["protocolo_resposta_recurso"]


# ----- handoff_pagamento (ST_HandoffPagamentoRecurso / ST_HandoffPagamentoParcial) ------------

_PAGTO_BK = "PAGTO-amh-GUIA-1-GLOSA-1"


def _handoff_vars(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "numero_lote_tiss": "LOTE-9",
        "prestador_id": "PREST-1",
        "valor_deferido_brl": 150.0,
        "data_vencimento": "2026-06-30",
        "competencia": "2026-05",
        "conta_origem_ref": "CONTA-REF-1",
        "instrumento_pagamento": "pix",
        "fonte_valor": "deferido",
        "analista_id": "analista-1",
    }
    base.update(over)
    return base


def test_handoff_pagamento_glosa_revertida_inicia_pagto_idempotente() -> None:
    """The handoff REALLY starts SP-OP-PAGTO-001 through the fenced chokepoint: exact business
    key, `tipo_pagamento=glosa_revertida`, integer-cents value, and the exactly-once ADR-0007
    start record."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    assert result["handoff"] == PAGTO_PROCESS_KEY
    assert result["handoff_executado"] is True
    assert result["pagto_business_key"] == _PAGTO_BK
    assert result["pagto_already_existed"] is False
    assert result["tipo_pagamento"] == "glosa_revertida"
    assert result["valor_pagamento_cents"] == 15000

    started = asyncio.run(engine.find_active_instance(_PAGTO_BK))
    assert started is not None and started.process_key == PAGTO_PROCESS_KEY
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["tipo_pagamento"] == "glosa_revertida"
    assert payload["valor_pagamento_cents"] == 15000
    assert payload["moeda"] == "BRL"
    assert payload["data_vencimento"] == "2026-06-30"

    assert sink.dedup_keys == [start_dedup_key("amh", PAGTO_PROCESS_KEY, _PAGTO_BK)]
    [record] = sink.records
    assert isinstance(record, AuditRecord)
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.action == f"start_process:{PAGTO_PROCESS_KEY}"
    assert record.model_id is None and record.prompt_version is None
    assert record.details["lastro_origem"] == LASTRO_ORIGEM_RECURSO
    assert "glosa_id" not in record.details  # identifiers hash-bound only, never in the clear


def test_handoff_pagamento_idempotente_retorna_instancia_ativa() -> None:
    """A PAGTO-001 already active for this reverted glosa is returned unchanged — one order per
    reverted glosa, never a duplicate payment."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-pagto",
            process_key=PAGTO_PROCESS_KEY,
            business_key=_PAGTO_BK,
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    assert result["pagto_instance_id"] == "pre-existing-pagto"
    assert result["pagto_already_existed"] is True


def test_handoff_pagamento_semeia_exatamente_o_conjunto_declarado() -> None:
    """I-PAGTO-1 amarra 1: EQUALITY, never `issubset` — a new key must fail here, not surface in
    production as a skipped human admissibility review."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert set(payload) == set(HANDOFF_PAGTO_SEEDED_KEYS)


def test_handoff_pagamento_nunca_semeia_os_fatos_de_admissibilidade_de_pagto() -> None:
    """I-PAGTO-1 amarra 2 (the negative, named): the four booleans PAGTO resolves for itself are
    NEVER seeded — not even when they arrive in RECURSO's own process scope. Seeding
    `lastro_confirmado=true` would drop the order straight into the clerical DENTRO_TETO_L2 lane
    (`ST_ReleaseLowValue`, no User Task) and skip the segregation of duties."""
    engine = FakeCibSevenTransport()
    hostile = _handoff_vars(
        lastro_confirmado=True,
        dados_pagamento_validos=True,
        duplicidade_suspeita=False,
        dentro_teto_l2=True,
    )
    handoff_pagamento(hostile, engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    for forbidden in HANDOFF_PAGTO_FORBIDDEN_KEYS:
        assert forbidden not in payload, f"{forbidden} vazou para PAGTO — I-PAGTO-1 violada"
    assert (
        frozenset({"lastro_confirmado", "dados_pagamento_validos", "duplicidade_suspeita", "dentro_teto_l2"})
        == HANDOFF_PAGTO_FORBIDDEN_KEYS
    )


def test_handoff_pagamento_semeia_lastro_origem_recurso_deferimento_humano() -> None:
    """EVIDENCE, not the fact: `lastro_origem` + `lastro_decisor_id` feed the human
    `UT_AnaliseAdmissibilidade` form. The decisor falls back to the auditor when the auditor
    channel granted the appeal."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["lastro_origem"] == "recurso_deferimento_humano"
    assert payload["lastro_decisor_id"] == "analista-1"

    engine2 = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(analista_id="", auditor_id="auditor-1"),
        engine=engine2,
        audit_sink=FakeStartAuditSink(),
    )
    payload2 = asyncio.run(engine2.get_process_status(_PAGTO_BK)).variables
    assert payload2["lastro_decisor_id"] == "auditor-1"


def test_handoff_pagamento_declara_a_fonte_do_valor() -> None:
    """M7: every monetary origin is declared. `fonte_valor=deferido` says the amount came from
    the human's `valor_deferido_brl`, not from an inferred remainder."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["fonte_valor"] == "deferido"


@pytest.mark.parametrize("valor", ["", "   ", "nao-numerico", 0.0, -1.0, None, True])
def test_handoff_pagamento_recusa_valor_deferido_zero_ausente_ou_nao_numerico(valor: object) -> None:
    """`_parse_valor_monetario` NEVER defaults to 0 — even though `valor_deferido_brl` is a
    MANDATORY User Task field, the worker refuses rather than emitting an order for nothing."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _handoff_vars(valor_deferido_brl=valor), engine=engine, audit_sink=FakeStartAuditSink()
        )
    assert "valor_deferido_brl" in str(exc.value)
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize("vencimento", ["", "   ", None])
def test_handoff_pagamento_recusa_data_vencimento_em_branco(vencimento: object) -> None:
    """`data_vencimento` is mandatory in PAGTO and inherited from the intake envelope. Where it
    comes from for a REVERTED glosa is OQ-3 (DRAFT/verify, financas + juridico) — until that is
    settled the worker refuses instead of inventing a due date."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _handoff_vars(data_vencimento=vencimento), engine=engine, audit_sink=FakeStartAuditSink()
        )
    assert "data_vencimento" in str(exc.value)
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize(
    "over",
    [
        {"tenant_id": ""},
        {"tenant_id": "   "},
        {"tenant_id": None},
        {"numero_guia_tiss": ""},
        {"glosa_id": None},
    ],
)
def test_handoff_pagamento_recusa_ancora_degenerada(over: dict) -> None:
    """`non_blank` BEFORE `str()`: an explicit `None` must refuse, never stringify to the truthy
    "None" and mint `PAGTO-amh--None`."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError):
        handoff_pagamento(_handoff_vars(**over), engine=engine, audit_sink=FakeStartAuditSink())


def test_handoff_pagamento_fail_closed_when_engine_seam_not_wired() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        handoff_pagamento(_handoff_vars(), audit_sink=FakeStartAuditSink())


def test_handoff_pagamento_fail_closed_when_audit_sink_not_wired() -> None:
    with pytest.raises(RuntimeError, match="audit sink"):
        handoff_pagamento(_handoff_vars(), engine=FakeCibSevenTransport())


class _HashOnlySink:
    """A legacy sink implementing ONLY `emit_once` — it cannot report the dedup flag."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self.calls.append(dedup_key)
        return "hash"


def test_handoff_pagamento_exige_seams_strict_de_dedup() -> None:
    """SP-OP-PAGTO-001 is the ONE STRICT start-dedup family. A sink that cannot report its
    durable claim makes the start un-gateable, and the chokepoint REFUSES rather than degrading
    to the TOCTOU-only path — which is exactly the duplicate-payment behaviour the gate exists to
    prevent. No engine effect happens."""
    engine = FakeCibSevenTransport()
    with pytest.raises(StartDedupGateUnavailableError):
        handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=_HashOnlySink())  # type: ignore[arg-type]
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


def test_handoff_pagamento_entry_round_trips_and_never_calls_kafka() -> None:
    """kafka is accepted (donor contract) but never invoked — a real FakeKafkaPublisher must stay
    untouched (no `.published` entries); the handoff starts a process, it publishes nothing."""
    kafka = FakeKafkaPublisher()
    engine = FakeCibSevenTransport()
    result = handoff_pagamento_entry(
        _handoff_vars(), kafka=kafka, engine=engine, audit_sink=FakeStartAuditSink()
    )
    assert result["pagto_business_key"] == _PAGTO_BK
    assert kafka.published == []


def test_handoff_pagamento_converte_centavos_sem_truncar() -> None:
    """BRL -> integer cents by ROUNDING, not truncation: `1.15 * 100` is 114.999... in binary and
    truncating would shave a cent off a real payment."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(valor_deferido_brl="1.15"), engine=engine, audit_sink=FakeStartAuditSink()
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["valor_pagamento_cents"] == 115


# ----- M2: os segmentos da chave STRICT sao NORMALIZADOS -------------------------------------

#: As 6 formas da MESMA glosa revertida que o gatekeeper mintou como 6 chaves distintas
#: (VERIFY-PR3-RECURSO §4.4). `non_blank` recusa ausente/vazio/whitespace-only/`None`, mas ACEITA
#: whitespace-PADDED — e `str()` preserva o padding. Numa familia STRICT de dedup, uma chave a
#: mais e uma SEGUNDA ordem de pagamento para a mesma glosa.
_VARIANTES_DA_MESMA_GLOSA: tuple[dict[str, object], ...] = (
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": " GUIA-1 ", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1\t"},
    {"tenant_id": " amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "  GLOSA-1  "},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1\n", "glosa_id": "GLOSA-1"},
)


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_pagto_business_key_colapsa_as_seis_variantes_numa_chave(variante: dict) -> None:
    """M2: as 6 variantes da MESMA glosa revertida produzem UMA chave — a de referencia."""
    assert _pagto_business_key(**variante) == _PAGTO_BK  # type: ignore[arg-type]


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_ordem_pagamento_id_colapsa_as_seis_variantes_num_id(variante: dict) -> None:
    """A ordem que a chave carrega tem de concordar com ela sobre a identidade da glosa."""
    assert _ordem_pagamento_id(**variante) == "ORDEM-GLOSAREV-amh-GUIA-1-GLOSA-1"  # type: ignore[arg-type]


def test_as_seis_variantes_sao_realmente_distintas_sem_normalizacao() -> None:
    """CONTROLE NEGATIVO do teste acima: sem `key_segment`, a interpolacao crua mintaria 6 chaves
    diferentes. Se este teste ficar verde com 1 chave, o corpus perdeu o poder de discriminar."""
    cruas = {"PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}".format(**v) for v in _VARIANTES_DA_MESMA_GLOSA}
    assert len(cruas) == 6, cruas


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_pagto_business_key_usa_a_normalizacao_canonica_do_repo(variante: dict) -> None:
    """PINO DE IGUALDADE (M2): a normalizacao e a MESMA de `maezo.agents.andre.keys.key_segment`
    — o unico composer STRICT da familia PAGTO —, nao uma copia local que possa divergir de novo
    (o defeito M-8 que o cabecalho daquele modulo descreve para ESTA familia)."""
    esperado = "PAGTO-{}-{}-{}".format(
        key_segment(variante["tenant_id"]),
        key_segment(variante["numero_guia_tiss"]),
        key_segment(variante["glosa_id"]),
    )
    assert _pagto_business_key(**variante) == esperado  # type: ignore[arg-type]


def test_handoff_pagamento_dedup_converge_com_guia_com_padding() -> None:
    """A prova de ponta a ponta do vetor: a MESMA glosa reentregue com um espaco a mais na guia
    encontra a instancia anterior em vez de abrir uma segunda ordem de pagamento."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    primeira = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    segunda = handoff_pagamento(
        _handoff_vars(numero_guia_tiss=" GUIA-1 ", glosa_id="GLOSA-1\t"),
        engine=engine,
        audit_sink=sink,
    )
    assert primeira["pagto_business_key"] == segunda["pagto_business_key"] == _PAGTO_BK
    assert segunda["pagto_already_existed"] is True
    assert primeira["pagto_instance_id"] == segunda["pagto_instance_id"]
    # A identidade que VIAJA para PAGTO tambem e a normalizada — nao o padding do reenvio.
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["numero_guia_tiss"] == "GUIA-1"
    assert payload["glosa_id"] == "GLOSA-1"


def test_chave_de_pagto_nunca_carrega_phi() -> None:
    """A chave e a ordem sao ancoradas em `tenant_id`/`numero_guia_tiss`/`glosa_id` — nenhuma
    variavel de `PHI_PROCESS_VARS` entra nelas nem no conjunto semeado (ADR-0006 duas zonas)."""
    assert not (HANDOFF_PAGTO_SEEDED_KEYS & PHI_PROCESS_VARS)
    engine = FakeCibSevenTransport()
    phi_semeada = {var: f"PHI-SENTINELA-{var}" for var in sorted(PHI_PROCESS_VARS)}
    result = handoff_pagamento(_handoff_vars(**phi_semeada), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert result["pagto_business_key"] == _PAGTO_BK
    assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS
    for var, sentinela in phi_semeada.items():
        assert sentinela not in result["pagto_business_key"], var
        assert sentinela not in str(payload["ordem_pagamento_id"]), var
        assert sentinela not in payload.values(), var


# ----- m1/m2: proveniencia da ordem -----------------------------------------------------------


@pytest.mark.parametrize("fonte", ["apresentado", "glosado", "", "  ", "DEFERIDO", None])
def test_handoff_pagamento_recusa_fonte_valor_desconhecida(fonte: object) -> None:
    """m1: `fonte_valor` era ECOADA e entrava em `AgentDecisionProvenance.decision_basis`,
    enquanto `valor_pagamento_cents` vem SEMPRE de `valor_deferido_brl` — proveniencia que podia
    contradizer a fonte real. Fora do dominio declarado: recusa, sem efeito no engine.

    `""`/`"  "`/`None` sao o caso benigno (a ausencia defaulta para `deferido`); qualquer OUTRO
    rotulo e uma afirmacao falsa sobre a origem do dinheiro.
    """
    engine = FakeCibSevenTransport()
    esperado_ok = str(fonte or "deferido").strip() in FONTES_VALOR_PERMITIDAS
    if esperado_ok:
        handoff_pagamento(_handoff_vars(fonte_valor=fonte), engine=engine, audit_sink=FakeStartAuditSink())
        payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
        assert payload["fonte_valor"] == "deferido"
        return
    with pytest.raises(RecursoHandoffPagamentoInvalidoError, match="fonte_valor"):
        handoff_pagamento(_handoff_vars(fonte_valor=fonte), engine=engine, audit_sink=FakeStartAuditSink())
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize("over", [{"analista_id": ""}, {"analista_id": "   "}, {"analista_id": None}])
def test_handoff_pagamento_recusa_ordem_sem_decisor(over: dict) -> None:
    """m2: `lastro_decisor_id` aceitava vazio. Uma ordem de pagamento nascida de decisao humana
    nao pode sair sem o decisor identificado — e a EVIDENCIA que UT_AnaliseAdmissibilidade le
    (ADR-0007). Sem `auditor_id` de reserva, a recusa e a resposta; nenhum efeito no engine."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError, match="decisor"):
        handoff_pagamento(_handoff_vars(**over), engine=engine, audit_sink=FakeStartAuditSink())
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


def test_handoff_pagamento_cai_para_o_auditor_como_decisor() -> None:
    """O ramo do merito: sem `analista_id`, o decisor e o `auditor_id` — normalizado, nao cru."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(analista_id=None, auditor_id="  auditor-7  "),
        engine=engine,
        audit_sink=FakeStartAuditSink(),
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["lastro_decisor_id"] == "auditor-7"


# ----- register_recurso_workers wires exactly 12 topics ---------------------------------------

_EXPECTED_TOPICS = frozenset(
    {
        "operadora.recurso.validate_recurso",
        "operadora.recurso.assess_eligibility",
        "operadora.recurso.request_documents",
        "operadora.recurso.analyze_request",
        "operadora.recurso.prepare_dossier",
        "operadora.recurso.escalate_to_junta",
        "operadora.recurso.registrar_indeferimento",
        "operadora.recurso.publish_completed",
        "operadora.recurso.handoff_pagamento",
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
        "operadora.recurso.comunicar_resposta",
    }
)


def test_register_recurso_workers_registers_all_12_topics() -> None:
    """EXACT SET, not a count: 13 - 3 (submit_appeal, track_status, reconcile_payment) + 2
    (comunicar_resposta, handoff_pagamento) = 12. A matching number with the wrong membership
    must not pass, which is why this asserts equality and not `>=`."""
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
    from maezo.tools.workers.recurso import register_recurso_workers

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_recurso_workers(harness, FakeKafkaPublisher())
    assert set(harness.registered_topics) == set(_EXPECTED_TOPICS)
    assert len(_EXPECTED_TOPICS) == 12
    for dead in (
        "operadora.recurso.submit_appeal",
        "operadora.recurso.track_status",
        "operadora.recurso.reconcile_payment",
        "operadora.recurso.register_desistencia",
    ):
        assert dead not in harness.registered_topics, f"{dead} nao deveria existir (sem shim)"


# ---------------------------------------------------------------------------
# BPMN — inicializacao das variaveis de decisao humana (defeito de engine, prova estatica)
# ---------------------------------------------------------------------------

_BPMN_RECURSO = Path(__file__).resolve().parents[4] / (
    "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"
)

#: Variaveis humanas OPCIONAIS que as conditionExpressions dos dois gateways decisorios leem.
#: Toda uma delas TEM de ser inicializada por `ST_PublishReceived` (a primeira service task de
#: TODO caminho) — ver `_publish_received_output_parameters` abaixo.
_VARS_DE_DECISAO_HUMANA = ("decisao_recurso", "decisao_auditor_recurso", "desfecho_humano")


def _bpmn_root() -> ET.Element:
    return ET.parse(_BPMN_RECURSO).getroot()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _publish_received_output_parameters() -> dict[str, str]:
    """`name -> texto` dos `camunda:outputParameter` de `ST_PublishReceived`."""
    for el in _bpmn_root().iter():
        if _local(el.tag) == "serviceTask" and el.get("id") == "ST_PublishReceived":
            return {
                str(p.get("name")): (p.text or "")
                for p in el.iter()
                if _local(p.tag) == "outputParameter" and p.get("name")
            }
    raise AssertionError("ST_PublishReceived nao existe no BPMN")


def _condition_expressions() -> dict[str, str]:
    """`sequenceFlow id -> texto da conditionExpression` (so os flows condicionais)."""
    out: dict[str, str] = {}
    for el in _bpmn_root().iter():
        if _local(el.tag) != "sequenceFlow":
            continue
        for child in el:
            if _local(child.tag) == "conditionExpression" and child.text:
                out[str(el.get("id"))] = child.text
    return out


def test_variaveis_de_decisao_humana_sao_inicializadas_no_primeiro_service_task() -> None:
    """CIB Seven 2.1.0 lanca `Unknown property used in expression ... Cannot resolve identifier`
    quando uma conditionExpression le uma variavel que NUNCA foi setada — e o faz ANTES de cair
    no default do gateway. Sem esta inicializacao, o caso "decisao AUSENTE" nao alcanca
    `End_ErrRecursoDecisaoInvalida`: o `POST /task/{id}/complete` devolve 500 e a instancia fica
    PARADA na User Task. O terminal de erro cobre "ausente OU desconhecida" (documentacao do
    proprio end event); so o segundo caso funcionava.

    `""` e o valor certo: nenhuma condicao do dominio casa com ele, entao o token cai no default
    fail-closed, e o guard do worker (`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`) recusa `""` como
    recusa qualquer nao-INDEFERIR — a inicializacao nao afrouxa o L0 hard.
    """
    outputs = _publish_received_output_parameters()
    for var in _VARS_DE_DECISAO_HUMANA:
        assert outputs.get(var) == '${""}', (
            f'{var} tem de ser inicializada como ${{""}} em ST_PublishReceived; veio '
            f"{outputs.get(var)!r}. Sem isso o default fail-closed do gateway e INALCANCAVEL "
            "para uma decisao ausente (a instancia trava na UT com HTTP 500)."
        )


def test_toda_variavel_lida_por_gateway_decisorio_esta_inicializada() -> None:
    """Fecha a classe inteira, nao so as tres variaveis de hoje: qualquer identificador que as
    conditionExpressions dos gateways decisorios leiam tem de estar inicializado em
    `ST_PublishReceived` — senao um autor futuro reintroduz o mesmo travamento."""
    inicializadas = set(_publish_received_output_parameters())
    condicoes = _condition_expressions()
    lidas: set[str] = set()
    for flow_id, expr in condicoes.items():
        if not flow_id.startswith(("Flow_GWDec_", "Flow_GWMerito_")):
            continue
        lidas.update(re.findall(r"\b([a-z_][a-z0-9_]*)\s*(?:==|!=)", expr))
    assert lidas, "nenhuma conditionExpression de gateway decisorio encontrada — teste inerte"
    assert lidas <= inicializadas, (
        f"variaveis lidas pelos gateways decisorios sem inicializacao em ST_PublishReceived: "
        f"{sorted(lidas - inicializadas)}"
    )


def test_a_inicializacao_nao_casa_com_nenhuma_rota_de_acao() -> None:
    """O valor inicializado (`""`) nao pode satisfazer NENHUMA condicao de acao — se casasse,
    a inicializacao teria criado um ato por omissao, o anti-padrao que o default existe para
    impedir."""
    for flow_id, expr in _condition_expressions().items():
        if not flow_id.startswith(("Flow_GWDec_", "Flow_GWMerito_")):
            continue
        assert "== ''" not in expr.replace('"', "'"), (
            f"{flow_id} casa com a string vazia — a inicializacao viraria uma acao por omissao"
        )
