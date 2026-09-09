"""INAD human decision shape, from SP-OP-INADIMPLENCIA-001 and its two User Tasks.

Source DTO tests only: no actor, evidence, regulatory or engine authority is established.
The contract's unused coordination-control variable remains outside this decision form.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import TaskDecision, TaskSnapshot

TASKS = ("UT_AnaliseInadimplencia", "UT_CoordenacaoCobranca")
OUTCOMES = ("SUSPENDER", "ENCAMINHAR_RESCISAO", "MANTER", "SOLICITAR_INFO")
BASIS = {
    "fundamentacao_contratual": "Human contractual basis",
    "referencia_regulatoria": "Human-reviewed regulatory reference",
    "comprovacao_notificacao_previa": "notice-reference",
    "comprovacao_periodo_minimo": "period-reference",
}


def _command(task: str, outcome: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-inad-1",
        "task_id": "task-inad-1",
        "process_definition_key": "SP-OP-INADIMPLENCIA-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-INADIMPLENCIA-001:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": task,
        "form_key": "inad_decisao",
        "form_version": 1,
        "form_digest": "b" * 64,
        "expected_task_revision": 3,
        "expected_evidence_revision": 2,
        "expected_evidence_digest": "c" * 64,
        "expected_membership_revision": 1,
        "inputs": {
            "kind": "inad_decisao",
            "decisao_inadimplencia": outcome,
            **(BASIS if outcome in {"SUSPENDER", "ENCAMINHAR_RESCISAO"} else {}),
        },
    }


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_documented_human_outcomes_through_actual_command(task: str, outcome: str) -> None:
    command = TaskDecision.model_validate_json(json.dumps(_command(task, outcome)))
    assert command.inputs.model_dump()["decisao_inadimplencia"] == outcome
    assert command.inputs.kind == "inad_decisao"


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("outcome", OUTCOMES[:2])
@pytest.mark.parametrize("field", tuple(BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True])
def test_adverse_or_handoff_basis_cannot_be_removed_or_coerced(
    task: str, outcome: str, field: str, bad: object
) -> None:
    payload = _command(task, outcome)
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))
    inputs[field] = BASIS[field]
    assert TaskDecision.model_validate_json(json.dumps(payload)).inputs.kind == "inad_decisao"


@pytest.mark.parametrize("field", tuple(BASIS))
def test_missing_basis_refuses_then_fresh_complete_command_recovers(field: str) -> None:
    payload = _command(TASKS[0], "SUSPENDER")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    del inputs[field]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))
    assert TaskDecision.model_validate(_command(TASKS[0], "SUSPENDER")).inputs.kind == "inad_decisao"


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "tenant_id",
        "tier",
        "human_approved",
        "notificacao_previa_feita",
        "dentro_periodo_minimo",
        "ja_em_rescisao_cancel",
        "decisao_coordenacao",
        "variables",
    ],
)
def test_browser_cannot_supply_authority_facts_or_unbound_coordination(field: str) -> None:
    payload = _command(TASKS[1], "MANTER")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize("outcome", ["RESCINDIR", "ANALISE_HUMANA", "AGUARDA_PURGA", "suspender", 1, True])
def test_dmn_and_local_rescission_variants_are_not_human_inputs(outcome: object) -> None:
    payload = _command(TASKS[0], "MANTER")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["decisao_inadimplencia"] = outcome
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_definition_key", "UT_AnaliseRescisao"),
        ("process_definition_key", "SP-OP-CANCEL-001"),
        ("form_key", "cancel_decisao"),
    ],
)
def test_cross_family_or_form_substitution_refuses(field: str, value: str) -> None:
    payload = _command(TASKS[0], "MANTER")
    payload[field] = value
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


def test_effect_date_remains_only_human_supplied_text_and_model_is_frozen() -> None:
    payload = _command(TASKS[0], "SUSPENDER")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["data_efeito_iso"] = "2026-09-30"
    command = TaskDecision.model_validate_json(json.dumps(payload))
    assert command.inputs.model_dump()["data_efeito_iso"] == "2026-09-30"
    with pytest.raises(ValidationError):
        command.inputs.kind = "cancel_decisao"  # type: ignore[misc]
    assert not hasattr(command.inputs, "responsavel_id")


@pytest.mark.parametrize("task", TASKS)
def test_snapshot_exposes_only_the_reconciled_draft_inputs(task: str) -> None:
    command = _command(task, "MANTER")
    fields = (
        "decisao_inadimplencia",
        "fundamentacao_contratual",
        "referencia_regulatoria",
        "comprovacao_notificacao_previa",
        "comprovacao_periodo_minimo",
        "data_efeito_iso",
    )
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
        eligible_candidate_groups=("juridico-contratos",),
        evidence_revision=2,
        evidence_digest="c" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "decision"),
        allowed_inputs=fields,
        read_only_evidence=None,
    )
    assert TaskSnapshot.model_validate(snapshot).allowed_inputs == fields
    snapshot["allowed_inputs"] = fields + ("responsavel_id",)
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(snapshot)
    snapshot["allowed_inputs"] = fields
    snapshot["form_source_status"] = "BPMN_FORMDATA"
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(snapshot)
