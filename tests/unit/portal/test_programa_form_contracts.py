"""Closed PROGRAMA human-decision form derived from the current SP-OP/BPMN prose.

This slice validates an immutable browser payload shape only.  It cannot authenticate a clinician,
establish consent/elegibility/freshness, or execute a clinical or engine decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import ProgramaDecisionInputs, TaskDecision, TaskSnapshot

TASKS = ("UT_DecisaoClinica", "UT_CoordenacaoDecisao")
OUTCOMES = ("ENROLL", "MANTER_ACOMPANHAMENTO", "DESLIGAR_CLINICO", "SOLICITAR_INFO")
BASIS = {
    "motivo_desligamento_clinico": "Alta de ciclo avaliada pelo humano",
    "referencia_clinica": "protocolo-clinico-ref",
}
PROGRAM_INPUTS = (
    "decisao_programa",
    "motivo_desligamento_clinico",
    "referencia_clinica",
)


def _command(task: str, outcome: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-programa-1",
        "task_id": "task-programa-1",
        "process_definition_key": "SP-OP-PROGRAMA-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-PROGRAMA-001:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": task,
        "form_key": "programa_decisao",
        "form_version": 1,
        "form_digest": "b" * 64,
        "expected_task_revision": 3,
        "expected_evidence_revision": 2,
        "expected_evidence_digest": "c" * 64,
        "expected_membership_revision": 1,
        "inputs": {
            "kind": "programa_decisao",
            "decisao_programa": outcome,
            **(BASIS if outcome == "DESLIGAR_CLINICO" else {}),
        },
    }


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_documented_human_outcomes_through_actual_command(task: str, outcome: str) -> None:
    command = TaskDecision.model_validate_json(json.dumps(_command(task, outcome)))
    assert command.inputs.model_dump()["decisao_programa"] == outcome
    assert command.inputs.kind == "programa_decisao"


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("field", tuple(BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_clinical_discharge_basis_cannot_be_removed_blank_or_coerced(
    task: str, field: str, bad: object
) -> None:
    payload = _command(task, "DESLIGAR_CLINICO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", tuple(BASIS))
def test_missing_discharge_basis_refuses_then_fresh_complete_command_recovers(field: str) -> None:
    payload = _command(TASKS[0], "DESLIGAR_CLINICO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    del inputs[field]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)
    recovered = TaskDecision.model_validate(_command(TASKS[0], "DESLIGAR_CLINICO"))
    assert recovered.inputs.model_dump() == {
        "kind": "programa_decisao",
        "decisao_programa": "DESLIGAR_CLINICO",
        **BASIS,
    }


@pytest.mark.parametrize("outcome", ("ENROLL", "MANTER_ACOMPANHAMENTO", "SOLICITAR_INFO"))
def test_non_adverse_outcomes_do_not_invent_a_clinical_basis_requirement(outcome: str) -> None:
    command = TaskDecision.model_validate(_command(TASKS[0], outcome))
    assert command.inputs.model_dump(exclude_none=True) == {
        "kind": "programa_decisao",
        "decisao_programa": outcome,
    }


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_clinico_id",
        "consent_status",
        "consentimento_ativo",
        "consent_checked",
        "consent_event_ref",
        "elegivel_programa",
        "elegibilidade_criterios_atendidos",
        "enrollment_gap",
        "sla",
        "sla_decisao",
        "sla_alerta",
        "tenant",
        "tenant_id",
        "tier",
        "human_approved",
        "variables",
        "decisao_coordenacao",
    ],
)
def test_browser_cannot_supply_trusted_facts_or_unbound_coordination(field: str) -> None:
    payload = _command(TASKS[1], "MANTER_ACOMPANHAMENTO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "outcome",
    ["MANTER", "ANALISE_HUMANA", "ALTA", "desligar_clinico", "", 1, True, [], {}],
)
def test_invented_dmn_and_coerced_outcomes_are_not_human_inputs(outcome: object) -> None:
    payload = _command(TASKS[0], "ENROLL")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["decisao_programa"] = outcome
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_definition_key", "UT_AnaliseInadimplencia"),
        ("process_definition_key", "SP-OP-INADIMPLENCIA-001"),
        ("form_key", "inad_decisao"),
    ],
)
def test_cross_family_or_form_substitution_refuses(field: str, value: str) -> None:
    payload = _command(TASKS[0], "ENROLL")
    payload[field] = value
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize("task", TASKS)
def test_snapshot_exposes_only_the_reconciled_draft_inputs(task: str) -> None:
    command = _command(task, "ENROLL")
    snapshot = {
        key: value
        for key, value in command.items()
        if key not in {"command_id", "inputs"} and not key.startswith("expected_")
    }
    snapshot.update(
        snapshot_at=datetime(2026, 9, 9, tzinfo=UTC),
        form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        task_revision=3,
        assignee_ref=None,
        eligible_candidate_groups=("coordenacao-clinica",),
        evidence_revision=2,
        evidence_digest="c" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "decision"),
        allowed_inputs=PROGRAM_INPUTS,
        read_only_evidence=None,
    )
    parsed = TaskSnapshot.model_validate(snapshot)
    assert parsed.allowed_inputs == PROGRAM_INPUTS
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert parsed.read_only_evidence is None

    snapshot["allowed_inputs"] = (*PROGRAM_INPUTS, "responsavel_clinico_id")
    with pytest.raises(ValidationError, match="allowed_inputs"):
        TaskSnapshot.model_validate(snapshot)


def test_programa_binding_rejects_wrong_source_status() -> None:
    command = _command(TASKS[0], "ENROLL")
    snapshot = {
        key: value
        for key, value in command.items()
        if key not in {"command_id", "inputs"} and not key.startswith("expected_")
    }
    snapshot.update(
        snapshot_at=datetime(2026, 9, 9, tzinfo=UTC),
        form_source_status="BPMN_FORMDATA",
        task_revision=3,
        assignee_ref=None,
        eligible_candidate_groups=("coordenacao-clinica", "equipe-cuidado"),
        evidence_revision=2,
        evidence_digest="c" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "decision"),
        allowed_inputs=PROGRAM_INPUTS,
        read_only_evidence=None,
    )
    with pytest.raises(ValidationError, match="binding"):
        TaskSnapshot.model_validate(snapshot)


def test_programa_dto_json_roundtrip_schema_and_immutability_are_closed() -> None:
    inputs = ProgramaDecisionInputs(
        kind="programa_decisao",
        decisao_programa="DESLIGAR_CLINICO",
        motivo_desligamento_clinico=BASIS["motivo_desligamento_clinico"],
        referencia_clinica=BASIS["referencia_clinica"],
    )
    assert ProgramaDecisionInputs.model_validate_json(inputs.model_dump_json()) == inputs
    schema = ProgramaDecisionInputs.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"kind", *PROGRAM_INPUTS}
    with pytest.raises(ValidationError):
        inputs.decisao_programa = "ENROLL"
