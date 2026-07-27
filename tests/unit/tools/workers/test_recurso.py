"""Unit tests for maezo.tools.workers.recurso — SP-OP-RECURSO-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerBpmnError
from maezo.tools.workers.recurso import (
    RECURSO_BPMN_ERROR_ALLOWLIST,
    DesistenciaNotHumanError,
    EscalateAnsTimeoutInput,
    NotifySlaRiskInput,
    RecursoDesistenciaInput,
    RecursoInput,
    SubmitAppealInput,
    TrackStatusInput,
    _mint_protocolo_recurso,
    _require_glosa_id,
    analyze_merits,
    analyze_request_entry,
    assess_eligibility,
    escalate_ans_timeout,
    escalate_to_junta,
    make_escalate_ans_timeout_handler,
    make_notify_sla_risk_handler,
    make_submit_appeal_handler,
    make_track_status_handler,
    notify_prestador,
    notify_sla_risk,
    prepare_dossier,
    publish_completed,
    publish_completed_entry,
    reconcile_payment,
    reconcile_payment_entry,
    register_desistencia,
    register_desistencia_entry,
    request_documents_entry,
    submit_appeal,
    track_status,
    validate_recurso,
    validate_recurso_entry,
)


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


# ---------------------------------------------------------------------------
# T3.1 P2b (Finding 2) — the 5 previously-zero-worker topics.
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
    """'ANS' in the task name is the RN 424 regulatory deadline — grep-proof no import of
    ans_gateway anywhere in recurso.py (AST-based: prose mentions in docstrings/comments don't
    count, only real `import`/`from ... import` statements)."""
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


# ----- submit_appeal (GW_DecisaoRecurso RECORRER / GW_MeritoAuditor MANTER -> ST_SubmitAppeal) -


def test_mint_protocolo_recurso_is_deterministic_by_business_key() -> None:
    """ADR-0030/T-H determinism: the SAME business key always mints the IDENTICAL protocol — no
    time_ns/uuid/random (mirrors LabeledMockAnsGatewayTransport's MOCK-...-{business_key})."""
    bk = "RECURSO-amh-GUIA-1-GLOSA-1"
    first = _mint_protocolo_recurso(bk, "amh", "GUIA-1", "GLOSA-1")
    second = _mint_protocolo_recurso(bk, "amh", "GUIA-1", "GLOSA-1")
    assert first == second
    assert first == f"RECAPPEAL-{bk}"


def test_mint_protocolo_recurso_differs_by_business_key() -> None:
    a = _mint_protocolo_recurso("RECURSO-amh-G-1-X", "amh", "G-1", "X")
    b = _mint_protocolo_recurso("RECURSO-amh-G-2-Y", "amh", "G-2", "Y")
    assert a != b


def test_mint_protocolo_recurso_falls_back_to_composed_key_when_business_key_blank() -> None:
    """Defensive fallback (production always carries business_key) — still deterministic."""
    result = _mint_protocolo_recurso("", "amh", "GUIA-1", "GLOSA-1")
    assert result == "RECAPPEAL-RECURSO-amh-GUIA-1-GLOSA-1"
    assert result == _mint_protocolo_recurso("  ", "amh", "GUIA-1", "GLOSA-1")


def test_mint_protocolo_recurso_never_uses_time_or_random() -> None:
    """Static proof (independent of the shared arch-test baseline fence) that THIS specific
    function's BODY (not its docstring prose) contains no nondeterministic call — AST-based,
    mirrors test_worker_handler_purity.py's `_nondeterministic_calls` detector."""
    import ast
    import inspect

    nondet_dotted = {"time.time_ns", "time.time", "uuid.uuid4", "uuid.uuid1", "uuid.uuid3", "uuid.uuid5"}
    nondet_roots = {"random", "secrets", "os"}

    tree = ast.parse(inspect.getsource(_mint_protocolo_recurso))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in nondet_dotted or node.value.id in nondet_roots:
                hits.add(dotted)
    assert not hits, f"nondeterministic call(s) found in _mint_protocolo_recurso: {hits}"


def test_submit_appeal_happy_path_sets_loop_counter_zero() -> None:
    inp = SubmitAppealInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    result = submit_appeal(inp, business_key="RECURSO-amh-G-1-GLOSA-1")
    assert result["loop_counter"] == 0
    assert result["protocolo_recurso"] == "RECAPPEAL-RECURSO-amh-G-1-GLOSA-1"


def test_submit_appeal_missing_business_key_still_deterministic() -> None:
    inp = SubmitAppealInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    result = submit_appeal(inp)
    assert result["protocolo_recurso"] == submit_appeal(inp)["protocolo_recurso"]


async def test_make_submit_appeal_handler_publishes_notification_and_result() -> None:
    """Test-spec invariant (`test_happy_path_escalar_auditor_mantem_recurso`):
    notifications_of_type("recurso.submit_appeal") must be observable."""
    kafka = FakeKafkaPublisher()
    handler = make_submit_appeal_handler(kafka)
    task = _task(
        topic="operadora.recurso.submit_appeal",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={"tenant_id": "amh", "numero_guia_tiss": "G-1", "glosa_id": "GLOSA-1"},
    )
    result = await handler(task)
    assert result["protocolo_recurso"] == "RECAPPEAL-RECURSO-amh-G-1-GLOSA-1"
    assert result["loop_counter"] == 0
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.submit_appeal"
    assert payload["protocolo_recurso"] == result["protocolo_recurso"]
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — a MAIN-path,
    # post-human-decision fact carrying the minted protocolo; silent loss is unacceptable.
    assert kafka.best_effort_calls == [False]


async def test_make_submit_appeal_handler_publish_failure_propagates_raw() -> None:
    """No BPMN boundary on ST_SubmitAppeal -> raw propagate (harness retry/incident, ADR-0030);
    re-dispatch republishes the IDENTICAL fact (deterministic protocolo minting)."""
    kafka = _FailingPublisher()
    handler = make_submit_appeal_handler(kafka)
    task = _task(
        topic="operadora.recurso.submit_appeal",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={"tenant_id": "amh", "numero_guia_tiss": "G-1", "glosa_id": "GLOSA-1"},
    )
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    assert kafka.best_effort_calls == [False]


async def test_make_submit_appeal_handler_fail_closed_default_no_producer() -> None:
    handler = make_submit_appeal_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["protocolo_recurso"]
    assert result["loop_counter"] == 0


# ----- track_status (ICE_AguardarResposta loop -> ST_TrackStatus) ----------------------------


def test_track_status_happy_path() -> None:
    inp = TrackStatusInput(glosa_id="GLOSA-1", protocolo_recurso="RECAPPEAL-X")
    result = track_status(inp)
    assert result["tracked"] is True
    assert result["glosa_id"] == "GLOSA-1"


def test_track_status_never_touches_loop_counter() -> None:
    """The BPMN's own outputParameter increments loop_counter; the worker must not echo/return
    it (double-count risk against that pre-complete expression)."""
    result = track_status(TrackStatusInput())
    assert "loop_counter" not in result


async def test_make_track_status_handler_publishes_notification() -> None:
    """Test-spec invariant (`test_loop_acompanhamento_limitado`):
    notifications_of_type("recurso.track_status") must be observable."""
    kafka = FakeKafkaPublisher()
    handler = make_track_status_handler(kafka)
    task = _task(
        topic="operadora.recurso.track_status",
        variables={"glosa_id": "GLOSA-1", "protocolo_recurso": "RECAPPEAL-X"},
    )
    result = await handler(task)
    assert result["tracked"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.track_status"
    assert payload["protocolo_recurso"] == "RECAPPEAL-X"
    assert key == task.business_key
    # DECIDED KEEP (DL-0038, t2-notify-integrity): track_status STAYS topic-default best-effort
    # BY DESIGN — it re-publishes every P5D ICE_AguardarResposta loop iteration, so a swallowed
    # failure self-heals on the next tick. This pin goes RED if someone flips the posture.
    assert kafka.best_effort_calls == [None]


async def test_make_track_status_handler_fail_closed_default_no_producer() -> None:
    handler = make_track_status_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["tracked"] is True


# ----- reconcile_payment (ST_ReconcilePaymentDeferido/Parcial) --------------------------------


def test_reconcile_payment_happy_path_mirrors_contas_shape() -> None:
    result = reconcile_payment("LOTE-1", "GUIA-1", "deferido")
    assert result == {
        "reconciled": True,
        "status": "deferido",
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
    }


def test_reconcile_payment_default_status_is_deferido() -> None:
    result = reconcile_payment("LOTE-1", "GUIA-1")
    assert result["status"] == "deferido"


def test_reconcile_payment_parcial_status() -> None:
    result = reconcile_payment("LOTE-1", "GUIA-1", "parcialmente_deferido")
    assert result["status"] == "parcialmente_deferido"


def test_reconcile_payment_entry_reads_resposta_operadora_as_status() -> None:
    variables = {
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
        "resposta_operadora": "parcialmente_deferido",
    }
    result = reconcile_payment_entry(variables)
    assert result == reconcile_payment("LOTE-1", "GUIA-1", "parcialmente_deferido")


def test_reconcile_payment_entry_missing_input_defaults_safe() -> None:
    result = reconcile_payment_entry({})
    assert result["reconciled"] is True
    assert result["status"] == "deferido"
    assert result["numero_lote_tiss"] == ""
    assert result["numero_guia_tiss"] == ""


def test_reconcile_payment_entry_never_calls_kafka() -> None:
    """kafka is accepted (donor contract) but never invoked — a real FakeKafkaPublisher must
    stay untouched (no `.published` entries)."""
    kafka = FakeKafkaPublisher()
    reconcile_payment_entry({}, kafka=kafka)
    assert kafka.published == []


# ----- register_recurso_workers wires all 5 new topics ----------------------------------------


def test_register_recurso_workers_registers_all_5_new_topics() -> None:
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
    from maezo.tools.workers.recurso import register_recurso_workers

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_recurso_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    for topic in (
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
        "operadora.recurso.submit_appeal",
        "operadora.recurso.track_status",
        "operadora.recurso.reconcile_payment",
    ):
        assert topic in topics, f"{topic} not registered by register_recurso_workers"
