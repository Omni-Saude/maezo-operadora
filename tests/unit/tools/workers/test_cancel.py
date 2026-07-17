"""Unit tests for maezo.tools.workers.cancel — SP-OP-CANCEL-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.cancel import (
    CancelContratoInvalidoError,
    CancelDecisionInput,
    CancelInput,
    CancellationNotHumanError,
    CancelManterNotHumanError,
    assess_admissibility,
    confirm_maintained_decision,
    confirm_maintained_decision_entry,
    effectuate_member_request,
    effectuate_member_request_entry,
    notify_beneficiario,
    notify_sla_risk,
    notify_sla_risk_entry,
    prepare_dossier_entry,
    process_cancel,
    process_cancel_entry,
    publish_completed,
    publish_completed_entry,
    register_cancel_workers,
    register_contract_termination,
    request_notification_entry,
    resolve_facts_entry,
    send_cancellation_notice_entry,
    validate_cancel,
)
from maezo.tools.workers.harness import (
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)

# ---------------------------------------------------------------------------
# validate_cancel
# ---------------------------------------------------------------------------


def test_validate_cancel_valid() -> None:
    """validate_cancel accepts a valid cancel input."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="CONT-001",
        matricula_beneficiario="BEN-001",
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = validate_cancel(inp)
    assert result.valid is True
    assert result.errors == []


def test_validate_cancel_invalid_tipo_solicitacao() -> None:
    """validate_cancel rejects unknown tipo_solicitacao."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="CONT-001",
        tipo_solicitacao="tipo_invalido",
    )
    result = validate_cancel(inp)
    assert result.valid is False
    assert any("tipo_solicitacao" in e for e in result.errors)


def test_validate_cancel_missing_contract_key() -> None:
    """validate_cancel flags missing contract and beneficiario keys."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="",
        matricula_beneficiario="",
        tipo_solicitacao="pedido_beneficiario",
    )
    result = validate_cancel(inp)
    assert any("contrato" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# assess_admissibility
# ---------------------------------------------------------------------------


def test_assess_admissibility_pedido_individual() -> None:
    """Pedido beneficiario individual -> EFETIVAR_PEDIDO (L2 clerical)."""
    inp = CancelInput(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "EFETIVAR_PEDIDO"
    assert result.grupo_sugerido == "gestao-contratos"


def test_assess_admissibility_pedido_coletivo() -> None:
    """Pedido beneficiario coletivo -> ANALISE_HUMANA (cabe ao estipulante)."""
    inp = CancelInput(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="coletivo_empresarial",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "coletivo" in result.motivo.lower() or "estipulante" in result.motivo.lower()


def test_assess_admissibility_fraude_referida() -> None:
    """fraude_referida -> ANALISE_HUMANA always (L0 hard)."""
    inp = CancelInput(
        tipo_solicitacao="fraude_referida",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(valid=True)
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "fraude" in result.motivo.lower()


def test_assess_admissibility_pendente_notificacao() -> None:
    """inadimplencia without notificacao -> PENDENTE_NOTIFICACAO."""
    inp = CancelInput(
        tipo_solicitacao="inadimplencia",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        notificacao_previa_feita=False,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "PENDENTE_NOTIFICACAO"


def test_assess_admissibility_segue_analise() -> None:
    """for_cause_operadora with notificacao -> SEGUE_ANALISE."""
    inp = CancelInput(
        tipo_solicitacao="for_cause_operadora",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        notificacao_previa_feita=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "SEGUE_ANALISE"


# ---------------------------------------------------------------------------
# notify_beneficiario
# ---------------------------------------------------------------------------


def test_notify_beneficiario_cancel() -> None:
    """notify_beneficiario sends status notification."""
    result = notify_beneficiario("BEN-001", "CONT-001", "rescindido")
    assert result["notified"] is True


# ---------------------------------------------------------------------------
# process_cancel
# ---------------------------------------------------------------------------


def test_process_cancel_rescindir() -> None:
    """process_cancel routes RESCINDIR to send_cancellation_notice."""
    inp = CancelInput(numero_contrato="CONT-001")
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="Clausula X",
        referencia_regulatoria="RN 593",
        comprovacao_notificacao_previa="NOTIF-001",
        responsavel_id="resp-001",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "send_cancellation_notice"


def test_process_cancel_suspender() -> None:
    """process_cancel routes SUSPENDER to send_cancellation_notice."""
    inp = CancelInput(numero_contrato="CONT-002")
    decision = CancelDecisionInput(
        decisao_cancelamento="SUSPENDER",
        fundamentacao_contratual="Clausula Y",
        referencia_regulatoria="RN 593",
        comprovacao_notificacao_previa="NOTIF-002",
        responsavel_id="resp-002",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "send_cancellation_notice"


def test_process_cancel_manter() -> None:
    """process_cancel routes MANTER to confirm_maintained_decision."""
    inp = CancelInput(numero_contrato="CONT-003")
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="Clausula Z",
        responsavel_id="resp-003",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "confirm_maintained_decision"


def test_process_cancel_efetivar_pedido() -> None:
    """process_cancel routes EFETIVAR_PEDIDO to effectuate_member_request."""
    inp = CancelInput(numero_contrato="CONT-004")
    decision = CancelDecisionInput(decisao_cancelamento="EFETIVAR_PEDIDO")
    result = process_cancel(inp, decision)
    assert result["action"] == "effectuate_member_request"


# ---------------------------------------------------------------------------
# register_contract_termination — GUARD tests
# ---------------------------------------------------------------------------


def test_register_termination_guard_not_adverse() -> None:
    """register_contract_termination raises if decisao not in {RESCINDIR, SUSPENDER}."""
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="justificativa",
        referencia_regulatoria="RN-593",
        comprovacao_notificacao_previa="NOTIF-1",
        responsavel_id="resp-1",
    )
    with pytest.raises(CancellationNotHumanError):
        register_contract_termination(decision)


def test_register_termination_guard_missing_fields() -> None:
    """register_contract_termination raises if required fields are missing."""
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="",
        referencia_regulatoria="",
        comprovacao_notificacao_previa="",
        responsavel_id="",
    )
    with pytest.raises(CancellationNotHumanError) as exc:
        register_contract_termination(decision)
    msg = str(exc.value)
    assert "fundamentacao_contratual" in msg
    assert "referencia_regulatoria" in msg


def test_register_termination_success() -> None:
    """register_contract_termination succeeds with all required fields."""
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="Violacao contratual art 13",
        referencia_regulatoria="RN 593/2023",
        comprovacao_notificacao_previa="NOTIF-2026-001",
        responsavel_id="resp-999",
    )
    result = register_contract_termination(decision)
    assert result["registered"] is True
    assert result["decisao"] == "RESCINDIR"


# ---------------------------------------------------------------------------
# GUARD tests: process_cancel raises on invalid MANTER
# ---------------------------------------------------------------------------


def test_process_cancel_manter_guard_missing_fundamentacao() -> None:
    """MANTER without fundamentacao_contratual -> raises CancelManterNotHumanError."""
    inp = CancelInput(numero_contrato="CONT-005")
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="",
        responsavel_id="resp-005",
    )
    with pytest.raises(CancelManterNotHumanError):
        process_cancel(inp, decision)


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_cancel() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="cancelado_beneficiario")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "cancelado_beneficiario"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_cancellation_not_human_is_permission_error() -> None:
    """CancellationNotHumanError must be a subclass of PermissionError."""
    assert issubclass(CancellationNotHumanError, PermissionError)


def test_cancel_manter_not_human_is_permission_error() -> None:
    """CancelManterNotHumanError must be a subclass of PermissionError."""
    assert issubclass(CancelManterNotHumanError, PermissionError)


def test_cancel_contrato_invalido_is_value_error() -> None:
    """CancelContratoInvalidoError must be a subclass of ValueError."""
    assert issubclass(CancelContratoInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_resolve_facts_entry_round_trips_validate_cancel() -> None:
    variables = {"numero_contrato": "C-1", "tipo_solicitacao": "pedido_beneficiario", "dentro_prazo": True}
    direct = validate_cancel(CancelInput(**variables))
    result = resolve_facts_entry(variables)
    assert result["valid"] == direct.valid
    assert result["dentro_prazo"] == direct.dentro_prazo


def test_prepare_dossier_entry_round_trips_assess_admissibility() -> None:
    variables = {
        "numero_contrato": "C-1",
        "tipo_solicitacao": "pedido_beneficiario",
        "tipo_plano": "individual",
        "dentro_prazo": True,
        "titularidade_confirmada": True,
        "vinculo_ativo": True,
    }
    input_data = CancelInput(**{k: v for k, v in variables.items() if k in CancelInput.__dataclass_fields__})
    validation = validate_cancel(input_data)
    direct = assess_admissibility(input_data, validation)

    entry_variables = dict(variables)
    entry_variables.update(
        valid=validation.valid,
        dentro_prazo=validation.dentro_prazo,
        notificacao_previa_feita=validation.notificacao_previa_feita,
        titularidade_confirmada=validation.titularidade_confirmada,
        vinculo_ativo=validation.vinculo_ativo,
    )
    result = prepare_dossier_entry(entry_variables)
    assert result["roteamento"] == direct.roteamento


def test_request_notification_entry_round_trips_notify_beneficiario() -> None:
    variables = {"matricula_beneficiario": "M-1", "numero_contrato": "C-1", "message_type": "aviso"}
    assert request_notification_entry(variables) == notify_beneficiario("M-1", "C-1", "aviso")


def test_send_cancellation_notice_entry_guards_missing_human_decision() -> None:
    """send_cancellation_notice_entry raises the UNCHANGED CancellationNotHumanError guard."""
    with pytest.raises(CancellationNotHumanError):
        send_cancellation_notice_entry({"decisao_cancelamento": ""})


def test_send_cancellation_notice_entry_happy_path() -> None:
    variables = {
        "decisao_cancelamento": "RESCINDIR",
        "fundamentacao_contratual": "inadimplencia",
        "referencia_regulatoria": "RN 593",
        "comprovacao_notificacao_previa": "carta-123",
        "responsavel_id": "resp-1",
    }
    result = send_cancellation_notice_entry(variables)
    assert result["registered"] is True


def test_process_cancel_entry_round_trips_process_cancel() -> None:
    variables = {
        "numero_contrato": "C-1",
        "decisao_cancelamento": "EFETIVAR_PEDIDO",
    }
    input_data = CancelInput(**{k: v for k, v in variables.items() if k in CancelInput.__dataclass_fields__})
    decision = CancelDecisionInput(
        **{k: v for k, v in variables.items() if k in CancelDecisionInput.__dataclass_fields__}
    )
    assert process_cancel_entry(variables) == process_cancel(input_data, decision)


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "cancel.completed", "desfecho": "efetivado"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="cancel.completed", payload={}, desfecho="efetivado"
    )


# ---------------------------------------------------------------------------
# effectuate_member_request (T3.1 R2 — BPMN-declared, previously missing)
# ---------------------------------------------------------------------------


def test_effectuate_member_request_happy_path() -> None:
    """effectuate_member_request executes a MEMBER-initiated request — always succeeds, no guard."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="CONT-EFET-001",
        matricula_beneficiario="BENEF-001",
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
    )
    result = effectuate_member_request(inp)
    assert result.member_request_effectuated is True
    assert result.numero_contrato == "CONT-EFET-001"
    assert result.matricula_beneficiario == "BENEF-001"


def test_effectuate_member_request_never_originates_denial() -> None:
    """Invariant: effectuate_member_request's output never carries an adverse/denial marker —
    it executes a member-initiated request, it never originates a verdict against the member."""
    result = effectuate_member_request(CancelInput(numero_contrato="CONT-EFET-002"))
    result_dict = {
        "member_request_effectuated": result.member_request_effectuated,
        "numero_contrato": result.numero_contrato,
        "matricula_beneficiario": result.matricula_beneficiario,
    }
    assert "decisao_cancelamento" not in result_dict
    assert not any(
        adverse in str(v).upper() for v in result_dict.values() for adverse in ("RESCIND", "NEGA", "DENY")
    )


def test_effectuate_member_request_entry_round_trips() -> None:
    variables = {
        "numero_contrato": "CONT-EFET-003",
        "matricula_beneficiario": "BENEF-003",
        "tipo_plano": "familiar",
    }
    input_data = CancelInput(**{k: v for k, v in variables.items() if k in CancelInput.__dataclass_fields__})
    direct = effectuate_member_request(input_data)
    result = effectuate_member_request_entry(variables)
    assert result["member_request_effectuated"] == direct.member_request_effectuated
    assert result["numero_contrato"] == direct.numero_contrato


def test_effectuate_member_request_entry_ignores_kafka_seam() -> None:
    """kafka is accepted (ADR-0026 §2 bootstrap contract) but unused — never raises when passed."""
    kafka = FakeKafkaPublisher()
    result = effectuate_member_request_entry({"numero_contrato": "CONT-EFET-004"}, kafka=kafka)
    assert result["member_request_effectuated"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# confirm_maintained_decision (T3.1 R2 — GAP-CANCEL-3, BPMN-declared, previously missing)
# ---------------------------------------------------------------------------


def test_confirm_maintained_decision_happy_path() -> None:
    """MANTER + fundamentacao_contratual present -> confirms; never echoes the justification text."""
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="Purga da inadimplencia comprovada",
        responsavel_id="juridico-001",
    )
    result = confirm_maintained_decision(decision)
    assert result.maintained_decision_confirmed is True
    assert result.fundamentacao_provided is True
    assert result.responsavel_id == "juridico-001"


def test_confirm_maintained_decision_never_echoes_fundamentacao_text() -> None:
    """no-denial (ADR-0018): the justification TEXT never rides the confirmation output."""
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="Texto sensivel da fundamentacao juridica",
        responsavel_id="juridico-002",
    )
    result = confirm_maintained_decision(decision)
    result_values = {
        result.maintained_decision_confirmed,
        result.fundamentacao_provided,
        result.responsavel_id,
    }
    assert "Texto sensivel da fundamentacao juridica" not in result_values
    assert not hasattr(result, "fundamentacao_contratual")


def test_confirm_maintained_decision_responsavel_id_defaults_empty() -> None:
    """GAP-CANCEL-6 parity: responsavel_id is ALWAYS returned (default "" when the UT omitted it —
    MANTER, unlike RESCINDIR/SUSPENDER, does not require it in the form)."""
    decision = CancelDecisionInput(decisao_cancelamento="MANTER", fundamentacao_contratual="Fundamentado")
    result = confirm_maintained_decision(decision)
    assert result.responsavel_id == ""


@pytest.mark.parametrize(
    "decisao",
    ["", "RESCINDIR", "SUSPENDER", "EFETIVAR_PEDIDO", "SOLICITAR_INFO", "manter", "MANTER "],
)
def test_confirm_maintained_decision_guard_rejects_non_strict_manter(decisao: str) -> None:
    """Guard L0 (GAP-CANCEL-3): decisao_cancelamento must be EXACTLY "MANTER" — strict equality,
    not membership. Any other value (including near-misses/whitespace/gateway-default-absent)
    raises WorkerBpmnError(ERR_CANCEL_MANTER_NOT_HUMAN) — never silently confirms."""
    decision = CancelDecisionInput(decisao_cancelamento=decisao, fundamentacao_contratual="x")
    with pytest.raises(WorkerBpmnError) as exc:
        confirm_maintained_decision(decision)
    assert exc.value.error_code == "ERR_CANCEL_MANTER_NOT_HUMAN"


def test_confirm_maintained_decision_guard_rejects_missing_fundamentacao() -> None:
    """Guard L0 (GAP-CANCEL-3): MANTER without fundamentacao_contratual is refused."""
    decision = CancelDecisionInput(decisao_cancelamento="MANTER", fundamentacao_contratual="")
    with pytest.raises(WorkerBpmnError) as exc:
        confirm_maintained_decision(decision)
    assert exc.value.error_code == "ERR_CANCEL_MANTER_NOT_HUMAN"


def test_confirm_maintained_decision_guard_rejects_whitespace_only_fundamentacao() -> None:
    """Defense in depth: whitespace-only fundamentacao_contratual is treated as absent."""
    decision = CancelDecisionInput(decisao_cancelamento="MANTER", fundamentacao_contratual="   ")
    with pytest.raises(WorkerBpmnError) as exc:
        confirm_maintained_decision(decision)
    assert exc.value.error_code == "ERR_CANCEL_MANTER_NOT_HUMAN"


def test_confirm_maintained_decision_guard_is_worker_bpmn_error_not_permission_error() -> None:
    """Invariant (module docstring): the GAP-CANCEL-3 guard raises a MODELED WorkerBpmnError — NOT
    CancelManterNotHumanError/PermissionError — so the BPMN's own BE_ManterNaoConfirmado boundary
    catch can fire instead of an opaque engine incident (contract SS Codigos de erro)."""
    decision = CancelDecisionInput(decisao_cancelamento="", fundamentacao_contratual="")
    with pytest.raises(WorkerBpmnError):
        confirm_maintained_decision(decision)
    # Confirm this is NOT also raised as the sibling PermissionError type.
    try:
        confirm_maintained_decision(decision)
    except WorkerBpmnError as exc:
        assert not isinstance(exc, PermissionError)


def test_confirm_maintained_decision_never_originates_adverse_verdict() -> None:
    """Invariant: confirm_maintained_decision only CONFIRMS a human's MANTER decision — it never
    sets/originates decisao_cancelamento or any adverse value itself; GW_Manter (not this worker)
    decides pedido_negado vs mantido, based on the input fact tipo_solicitacao."""
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER", fundamentacao_contratual="x", responsavel_id="resp-1"
    )
    result = confirm_maintained_decision(decision)
    result_dict = {
        "maintained_decision_confirmed": result.maintained_decision_confirmed,
        "fundamentacao_provided": result.fundamentacao_provided,
        "responsavel_id": result.responsavel_id,
    }
    assert "decisao_cancelamento" not in result_dict
    assert "tipo_solicitacao" not in result_dict


def test_confirm_maintained_decision_entry_round_trips_happy_path() -> None:
    variables = {
        "decisao_cancelamento": "MANTER",
        "fundamentacao_contratual": "Fundamentado (teste)",
        "responsavel_id": "juridico-003",
    }
    decision = CancelDecisionInput(
        **{k: v for k, v in variables.items() if k in CancelDecisionInput.__dataclass_fields__}
    )
    direct = confirm_maintained_decision(decision)
    result = confirm_maintained_decision_entry(variables)
    assert result == {
        "maintained_decision_confirmed": direct.maintained_decision_confirmed,
        "fundamentacao_provided": direct.fundamentacao_provided,
        "responsavel_id": direct.responsavel_id,
    }


def test_confirm_maintained_decision_entry_propagates_guard() -> None:
    """The UNCHANGED guard propagates through the dict-boundary entry function."""
    with pytest.raises(WorkerBpmnError) as exc:
        confirm_maintained_decision_entry({"decisao_cancelamento": "MANTER"})
    assert exc.value.error_code == "ERR_CANCEL_MANTER_NOT_HUMAN"


def test_confirm_maintained_decision_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = confirm_maintained_decision_entry(
        {"decisao_cancelamento": "MANTER", "fundamentacao_contratual": "x"}, kafka=kafka
    )
    assert result["maintained_decision_confirmed"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# notify_sla_risk (T3.1 R2 — BPMN-declared, previously missing)
# ---------------------------------------------------------------------------


def test_notify_sla_risk_happy_path() -> None:
    """notify_sla_risk is informational-only: no guard, no decision, UT stays open."""
    inp = CancelInput(numero_contrato="CONT-SLA-001", tipo_solicitacao="inadimplencia")
    result = notify_sla_risk(inp)
    assert result["sla_risk_notified"] is True
    assert result["numero_contrato"] == "CONT-SLA-001"


def test_notify_sla_risk_never_alters_a_decision() -> None:
    """Invariant: notify_sla_risk's output never carries a decision/adverse marker."""
    result = notify_sla_risk(CancelInput(numero_contrato="CONT-SLA-002"))
    assert "decisao_cancelamento" not in result
    assert set(result.keys()) == {"sla_risk_notified", "numero_contrato"}


def test_notify_sla_risk_entry_round_trips() -> None:
    variables = {"numero_contrato": "CONT-SLA-003", "tipo_solicitacao": "for_cause_operadora"}
    input_data = CancelInput(**{k: v for k, v in variables.items() if k in CancelInput.__dataclass_fields__})
    assert notify_sla_risk_entry(variables) == notify_sla_risk(input_data)


def test_notify_sla_risk_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = notify_sla_risk_entry({"numero_contrato": "CONT-SLA-004"}, kafka=kafka)
    assert result["sla_risk_notified"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# register_cancel_workers — registry/drift coverage (T3.1 R2)
# ---------------------------------------------------------------------------

_BPMN_CANCEL_TOPICS = frozenset(
    {
        "operadora.cancel.resolve_facts",
        "operadora.cancel.prepare_dossier",
        "operadora.cancel.request_notification",
        "operadora.cancel.effectuate_member_request",
        "operadora.cancel.send_cancellation_notice",
        "operadora.cancel.confirm_maintained_decision",
        "operadora.cancel.notify_sla_risk",
    }
)


def test_register_cancel_workers_matches_bpmn_topics_exactly() -> None:
    """Registry coverage (ADR-0026 test strategy): the registered `operadora.cancel.*` topic set
    equals EXACTLY the 7 BPMN-declared topics — no gap (a missing spec topic) and no orphan (an
    extra registration with no BPMN counterpart, the T3.1 R2 finding this PR fixes)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_cancel_workers(harness, FakeKafkaPublisher())
    cancel_topics = {t for t in harness.registered_topics if t.startswith("operadora.cancel.")}
    assert cancel_topics == _BPMN_CANCEL_TOPICS


def test_register_cancel_workers_does_not_register_orphan_topics() -> None:
    """T3.1 R2: `operadora.cancel.process_cancel`/`operadora.cancel.publish_completed` correspond
    to NO camunda:topic in the BPMN — must never be registered (this was the proximate cause of
    all 24 cancel_probe-based integration xfails prior to this fix)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_cancel_workers(harness, FakeKafkaPublisher())
    assert "operadora.cancel.process_cancel" not in harness.registered_topics
    assert "operadora.cancel.publish_completed" not in harness.registered_topics
