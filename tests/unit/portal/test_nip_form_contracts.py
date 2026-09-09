"""Closed NIP draft and final-review forms from current BPMN consumption."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import NipDecisionInputs, NipDraftInputs, TaskDecision, TaskSnapshot

PROCESS = "SP-OP-NIP-001"
DRAFT_TASK = "UT_ElaborarRespostaNip"
DECISION_TASKS = ("UT_CoordenacaoNip", "UT_RevisaoJuridicaNip")
OUTCOMES = ("MANTER_NEGATIVA", "CONCEDER", "RESPONDER_NAO_ASSISTENCIAL", "SOLICITAR_INFO")
ADVERSE_BASIS = {
    "fundamentacao_regulatoria": "Human-reviewed regulatory basis",
    "referencia_negativa_original": "original-denial-ref",
}
DRAFT_INPUTS = ("texto_resposta_nip",)
DECISION_INPUTS = (
    "decisao_nip",
    "fundamentacao_regulatoria",
    "referencia_negativa_original",
    "texto_resposta_nip",
)


def _command(task: str, form: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-nip-1",
        "task_id": "task-nip-1",
        "process_definition_key": PROCESS,
        "process_definition_version": 7,
        "process_definition_id": f"{PROCESS}:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": task,
        "form_key": form,
        "form_version": 1,
        "form_digest": "b" * 64,
        "expected_task_revision": 3,
        "expected_evidence_revision": 2,
        "expected_evidence_digest": "c" * 64,
        "expected_membership_revision": 1,
        "inputs": {"kind": form, **inputs},
    }


def _decision(outcome: str) -> dict[str, object]:
    fields: dict[str, object] = {
        "decisao_nip": outcome,
        "texto_resposta_nip": "Human-authored and reviewed final response",
    }
    if outcome == "MANTER_NEGATIVA":
        fields.update(ADVERSE_BASIS)
    return fields


def _snapshot(task: str, form: str, allowed_inputs: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_at": datetime(2026, 9, 9, tzinfo=UTC),
        "task_id": "task-nip-1",
        "process_definition_key": PROCESS,
        "process_definition_version": 7,
        "process_definition_id": f"{PROCESS}:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": task,
        "form_key": form,
        "form_version": 1,
        "form_digest": "b" * 64,
        "form_source_status": "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        "task_revision": 3,
        "assignee_ref": None,
        "eligible_candidate_groups": ("juridico-regulatorio",),
        "evidence_revision": 2,
        "evidence_digest": "c" * 64,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": allowed_inputs,
        "read_only_evidence": None,
    }


def test_elaboration_task_accepts_the_human_authored_draft() -> None:
    command = _command(DRAFT_TASK, "nip_minuta", {"texto_resposta_nip": "Human-authored draft"})
    parsed = TaskDecision.model_validate_json(json.dumps(command))
    assert parsed.inputs.model_dump() == {
        "kind": "nip_minuta",
        "texto_resposta_nip": "Human-authored draft",
    }


@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_draft_text_refuses_missing_blank_and_coerced_values(bad: object) -> None:
    command = _command(DRAFT_TASK, "nip_minuta", {"texto_resposta_nip": bad})
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(command))


@pytest.mark.parametrize(
    "unconsumed",
    ["CONCEDER", "RESPONDER_NAO_ASSISTENCIAL", "ENCAMINHAR_REVISAO", "MANTER_NEGATIVA"],
)
def test_elaboration_decision_prose_does_not_invent_a_gateway_control(unconsumed: str) -> None:
    command = _command(
        DRAFT_TASK,
        "nip_minuta",
        {"texto_resposta_nip": "Human-authored draft", "decisao_nip": unconsumed},
    )
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(command)


@pytest.mark.parametrize("task", DECISION_TASKS)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_final_review_tasks_accept_each_documented_outcome(task: str, outcome: str) -> None:
    parsed = TaskDecision.model_validate_json(json.dumps(_command(task, "nip_decisao", _decision(outcome))))
    assert parsed.inputs.kind == "nip_decisao"
    assert parsed.inputs.model_dump()["decisao_nip"] == outcome


@pytest.mark.parametrize("task", DECISION_TASKS)
@pytest.mark.parametrize("outcome", OUTCOMES[:3])
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_submitted_response_text_refuses_missing_blank_and_coerced_values(
    task: str, outcome: str, bad: object
) -> None:
    fields = _decision(outcome)
    fields["texto_resposta_nip"] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(_command(task, "nip_decisao", fields)))


@pytest.mark.parametrize("task", DECISION_TASKS)
def test_requesting_information_does_not_invent_final_response_text_requirement(task: str) -> None:
    parsed = TaskDecision.model_validate(_command(task, "nip_decisao", {"decisao_nip": "SOLICITAR_INFO"}))
    assert parsed.inputs.model_dump(exclude_none=True) == {
        "kind": "nip_decisao",
        "decisao_nip": "SOLICITAR_INFO",
    }


@pytest.mark.parametrize("task", DECISION_TASKS)
@pytest.mark.parametrize("bad", ["", " \t", 1, True, [], {}])
def test_requesting_information_refuses_blank_or_coerced_text_when_supplied(task: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(
            json.dumps(
                _command(
                    task,
                    "nip_decisao",
                    {"decisao_nip": "SOLICITAR_INFO", "texto_resposta_nip": bad},
                )
            )
        )


@pytest.mark.parametrize("task", DECISION_TASKS)
@pytest.mark.parametrize("field", tuple(ADVERSE_BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_maintained_denial_basis_refuses_missing_blank_and_coerced_values(
    task: str, field: str, bad: object
) -> None:
    fields = _decision("MANTER_NEGATIVA")
    fields[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(_command(task, "nip_decisao", fields)))


@pytest.mark.parametrize("field", tuple(ADVERSE_BASIS))
def test_missing_denial_basis_refuses_then_fresh_complete_command_recovers(field: str) -> None:
    fields = _decision("MANTER_NEGATIVA")
    del fields[field]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(DECISION_TASKS[0], "nip_decisao", fields))
    recovered = TaskDecision.model_validate(
        _command(DECISION_TASKS[0], "nip_decisao", _decision("MANTER_NEGATIVA"))
    )
    assert recovered.inputs.kind == "nip_decisao"


@pytest.mark.parametrize("outcome", OUTCOMES[1:3])
def test_non_adverse_outcomes_do_not_invent_denial_basis(outcome: str) -> None:
    parsed = TaskDecision.model_validate(_command(DECISION_TASKS[0], "nip_decisao", _decision(outcome)))
    assert parsed.inputs.model_dump(exclude_none=True) == {
        "kind": "nip_decisao",
        "decisao_nip": outcome,
        "texto_resposta_nip": "Human-authored and reviewed final response",
    }


@pytest.mark.parametrize(
    "task,form,fields",
    [
        (DRAFT_TASK, "nip_minuta", {"texto_resposta_nip": "draft"}),
        (DECISION_TASKS[0], "nip_decisao", _decision("CONCEDER")),
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "revisor_id",
        "tenant",
        "tenant_id",
        "tier",
        "human_approved",
        "variables",
        "numero_nip_ans",
        "beneficiario_pseudo_id",
        "classificacao",
        "classificacao_nip",
        "roteamento",
        "roteamento_nip",
        "grupo_humano",
        "documentacao_suficiente",
        "contesta_negativa",
        "dossie_gustavo",
        "sla",
        "prazo_resposta_iso",
        "protocolo_ans",
        "protocolo_filing",
    ],
)
def test_browser_cannot_supply_actor_routing_case_or_protocol_facts(
    task: str, form: str, fields: dict[str, object], field: str
) -> None:
    inputs = dict(fields)
    inputs[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(task, form, inputs))


@pytest.mark.parametrize(
    "bad",
    ["ENCAMINHAR_REVISAO", "REVISAO_JURIDICA", "PENDENTE_INFO", "manter_negativa", "", 1, True, [], {}],
)
def test_draft_and_dmn_values_are_not_final_decision_outcomes(bad: object) -> None:
    fields = _decision("CONCEDER")
    fields["decisao_nip"] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(DECISION_TASKS[0], "nip_decisao", fields))


@pytest.mark.parametrize(
    "task,form,inputs,wrong_form",
    [
        (DRAFT_TASK, "nip_minuta", {"texto_resposta_nip": "draft"}, "nip_decisao"),
        (DECISION_TASKS[0], "nip_decisao", _decision("CONCEDER"), "nip_minuta"),
    ],
)
def test_cross_task_form_substitution_refuses(
    task: str, form: str, inputs: dict[str, object], wrong_form: str
) -> None:
    payload = _command(task, form, inputs)
    payload["form_key"] = wrong_form
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "task,form,allowed",
    [
        (DRAFT_TASK, "nip_minuta", DRAFT_INPUTS),
        (DECISION_TASKS[0], "nip_decisao", DECISION_INPUTS),
        (DECISION_TASKS[1], "nip_decisao", DECISION_INPUTS),
    ],
)
def test_snapshot_exposes_only_reconciled_draft_inputs(
    task: str, form: str, allowed: tuple[str, ...]
) -> None:
    snapshot = _snapshot(task, form, allowed)
    parsed = TaskSnapshot.model_validate(snapshot)
    assert parsed.allowed_inputs == allowed
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    snapshot["allowed_inputs"] = (*allowed, "revisor_id")
    with pytest.raises(ValidationError, match="allowed_inputs"):
        TaskSnapshot.model_validate(snapshot)


def test_nip_dtos_roundtrip_schema_immutability_and_exports_are_closed() -> None:
    draft = NipDraftInputs(kind="nip_minuta", texto_resposta_nip="Human-authored draft")
    decision = NipDecisionInputs(
        kind="nip_decisao",
        decisao_nip="MANTER_NEGATIVA",
        texto_resposta_nip="Human-reviewed final response",
        **ADVERSE_BASIS,
    )
    assert NipDraftInputs.model_validate_json(draft.model_dump_json()) == draft
    assert NipDecisionInputs.model_validate_json(decision.model_dump_json()) == decision
    assert set(NipDraftInputs.model_json_schema()["properties"]) == {"kind", *DRAFT_INPUTS}
    assert set(NipDecisionInputs.model_json_schema()["properties"]) == {"kind", *DECISION_INPUTS}
    with pytest.raises(ValidationError, match="frozen"):
        draft.texto_resposta_nip = "changed"

    import maezo.portal.contracts as contracts

    assert contracts.NipDraftInputs is NipDraftInputs
    assert contracts.NipDecisionInputs is NipDecisionInputs
    assert "NipDraftInputs" in contracts.__all__
    assert "NipDecisionInputs" in contracts.__all__
