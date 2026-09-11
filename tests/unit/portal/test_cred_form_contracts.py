"""Closed browser shapes for the four current SP-OP-CRED-001 human tasks.

These tests establish immutable DTO shape only. They do not authenticate an actor, establish
provider or network facts, calculate a legal date, or execute a credentialing decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    CredCredentialingInputs,
    CredDecredentialingInputs,
    TaskDecision,
    TaskSnapshot,
)

DESCRED_TASKS = ("UT_AnaliseDescredenciamento", "UT_CoordenacaoRedeDescred")
CRED_TASKS = ("UT_AnaliseCredenciamento", "UT_CoordenacaoRedeCred")
DESCRED_OUTCOMES = ("DESCREDENCIAR", "MANTER", "SOLICITAR_INFO")
CRED_OUTCOMES = ("APROVAR_CREDENCIAMENTO", "NEGAR_CREDENCIAMENTO", "SOLICITAR_INFO")
DESCRED_BASIS = {
    "fundamentacao": "Human-reviewed network basis",
    "referencia_regulatoria": "regulatory-reference",
    "comprovacao_notificacao_previa": "notice-reference",
}
CRED_BASIS = {
    "fundamentacao": "Human-reviewed credentialing basis",
    "referencia_regulatoria": "regulatory-reference",
}
DESCRED_INPUTS = (
    "decisao_cred",
    "fundamentacao",
    "referencia_regulatoria",
    "comprovacao_notificacao_previa",
    "plano_substituicao",
    "data_efeito_iso",
)
CRED_INPUTS = (
    "decisao_cred",
    "fundamentacao",
    "referencia_regulatoria",
    "data_efeito_iso",
)


def _command(task: str, outcome: str) -> dict[str, object]:
    descred = task in DESCRED_TASKS
    form = "cred_descred" if descred else "cred_cred"
    inputs: dict[str, object] = {"kind": form, "decisao_cred": outcome}
    if outcome == "DESCREDENCIAR":
        inputs.update(DESCRED_BASIS)
    elif outcome == "NEGAR_CREDENCIAMENTO":
        inputs.update(CRED_BASIS)
    return {
        "schema_version": 1,
        "command_id": "command-cred-1",
        "task_id": "task-cred-1",
        "process_definition_key": "SP-OP-CRED-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-CRED-001:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": task,
        "form_key": form,
        "form_version": 1,
        "form_digest": "b" * 64,
        "expected_task_revision": 3,
        "expected_evidence_revision": 2,
        "expected_evidence_digest": "c" * 64,
        "expected_membership_revision": 1,
        "inputs": inputs,
    }


def _snapshot(task: str, outcome: str) -> dict[str, object]:
    command = _command(task, outcome)
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
        eligible_candidate_groups=("gestao-rede",),
        evidence_revision=2,
        evidence_digest="c" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "decision"),
        allowed_inputs=DESCRED_INPUTS if task in DESCRED_TASKS else CRED_INPUTS,
        read_only_evidence=None,
    )
    return snapshot


@pytest.mark.parametrize("task", DESCRED_TASKS)
@pytest.mark.parametrize("outcome", DESCRED_OUTCOMES)
def test_documented_decredentialing_outcomes_through_actual_command(task: str, outcome: str) -> None:
    command = TaskDecision.model_validate_json(json.dumps(_command(task, outcome)))
    assert command.inputs.model_dump()["decisao_cred"] == outcome
    assert command.inputs.kind == "cred_descred"


@pytest.mark.parametrize("task", CRED_TASKS)
@pytest.mark.parametrize("outcome", CRED_OUTCOMES)
def test_documented_credentialing_outcomes_through_actual_command(task: str, outcome: str) -> None:
    command = TaskDecision.model_validate_json(json.dumps(_command(task, outcome)))
    assert command.inputs.model_dump()["decisao_cred"] == outcome
    assert command.inputs.kind == "cred_cred"


@pytest.mark.parametrize("task", DESCRED_TASKS)
@pytest.mark.parametrize("field", tuple(DESCRED_BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_decredentialing_basis_cannot_be_removed_blank_or_coerced(task: str, field: str, bad: object) -> None:
    payload = _command(task, "DESCREDENCIAR")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("task", CRED_TASKS)
@pytest.mark.parametrize("field", tuple(CRED_BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_credentialing_denial_basis_cannot_be_removed_blank_or_coerced(
    task: str, field: str, bad: object
) -> None:
    payload = _command(task, "NEGAR_CREDENCIAMENTO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("task", "outcome", "field"),
    [
        *[(DESCRED_TASKS[0], "DESCREDENCIAR", field) for field in DESCRED_BASIS],
        *[(CRED_TASKS[0], "NEGAR_CREDENCIAMENTO", field) for field in CRED_BASIS],
    ],
)
def test_missing_adverse_basis_refuses_then_fresh_complete_command_recovers(
    task: str, outcome: str, field: str
) -> None:
    payload = _command(task, outcome)
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    del inputs[field]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)
    recovered = TaskDecision.model_validate(_command(task, outcome))
    assert recovered.inputs.model_dump()["decisao_cred"] == outcome


@pytest.mark.parametrize("outcome", ("MANTER", "SOLICITAR_INFO"))
def test_non_adverse_decredentialing_outcomes_do_not_invent_basis(outcome: str) -> None:
    command = TaskDecision.model_validate(_command(DESCRED_TASKS[0], outcome))
    assert command.inputs.model_dump(exclude_none=True) == {
        "kind": "cred_descred",
        "decisao_cred": outcome,
    }


@pytest.mark.parametrize("outcome", ("APROVAR_CREDENCIAMENTO", "SOLICITAR_INFO"))
def test_non_adverse_credentialing_outcomes_do_not_invent_basis(outcome: str) -> None:
    command = TaskDecision.model_validate(_command(CRED_TASKS[0], outcome))
    assert command.inputs.model_dump(exclude_none=True) == {
        "kind": "cred_cred",
        "decisao_cred": outcome,
    }


def test_replacement_plan_stays_optional_until_trusted_beneficiary_fact_is_checked() -> None:
    without_plan = TaskDecision.model_validate(_command(DESCRED_TASKS[0], "DESCREDENCIAR"))
    assert without_plan.inputs.model_dump()["plano_substituicao"] is None

    payload = _command(DESCRED_TASKS[0], "DESCREDENCIAR")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["plano_substituicao"] = "Equivalent-provider plan reviewed by human"
    with_plan = TaskDecision.model_validate(payload)
    assert with_plan.inputs.model_dump()["plano_substituicao"] == inputs["plano_substituicao"]


@pytest.mark.parametrize("bad", ["", " \t", 1, True, [], {}])
def test_supplied_replacement_plan_cannot_be_blank_or_coerced(bad: object) -> None:
    payload = _command(DESCRED_TASKS[0], "DESCREDENCIAR")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["plano_substituicao"] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "task,outcome",
    [
        (DESCRED_TASKS[1], "MANTER"),
        (CRED_TASKS[1], "APROVAR_CREDENCIAMENTO"),
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "tier",
        "tenant",
        "tenant_id",
        "direcao",
        "tipo_prestador",
        "tem_beneficiarios_vinculados",
        "tem_plano_substituicao",
        "notificacao_previa_feita",
        "substituto_equivalente_identificado",
        "licenca_valida",
        "documentacao_completa",
        "dentro_criterios_rede",
        "indicio_irregularidade_sinalizado",
        "sla",
        "human_approved",
        "variables",
        "decisao_coordenacao",
        "encaminhar_fraude",
    ],
)
def test_browser_cannot_supply_trusted_facts_or_unbound_contract_fields(
    task: str, outcome: str, field: str
) -> None:
    payload = _command(task, outcome)
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "outcome",
    [
        "APROVAR_CREDENCIAMENTO",
        "NEGAR_CREDENCIAMENTO",
        "ANALISE_HUMANA",
        "descredenciar",
        "",
        1,
        True,
        [],
        {},
    ],
)
def test_credentialing_and_dmn_outcomes_cannot_enter_decredentialing_form(
    outcome: object,
) -> None:
    payload = _command(DESCRED_TASKS[0], "MANTER")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["decisao_cred"] = outcome
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "outcome",
    ["DESCREDENCIAR", "MANTER", "SEGUE_ANALISE", "negar_credenciamento", "", 1, True, [], {}],
)
def test_decredentialing_and_dmn_outcomes_cannot_enter_credentialing_form(
    outcome: object,
) -> None:
    payload = _command(CRED_TASKS[0], "APROVAR_CREDENCIAMENTO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["decisao_cred"] = outcome
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    ("task", "outcome", "wrong_form"),
    [
        (DESCRED_TASKS[0], "MANTER", "cred_cred"),
        (CRED_TASKS[0], "APROVAR_CREDENCIAMENTO", "cred_descred"),
    ],
)
def test_cross_direction_form_substitution_refuses(task: str, outcome: str, wrong_form: str) -> None:
    payload = _command(task, outcome)
    payload["form_key"] = wrong_form
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize("task", (*DESCRED_TASKS, *CRED_TASKS))
def test_snapshot_exposes_only_the_reconciled_draft_inputs(task: str) -> None:
    outcome = "MANTER" if task in DESCRED_TASKS else "APROVAR_CREDENCIAMENTO"
    snapshot = _snapshot(task, outcome)
    parsed = TaskSnapshot.model_validate(snapshot)
    expected = DESCRED_INPUTS if task in DESCRED_TASKS else CRED_INPUTS
    assert parsed.allowed_inputs == expected
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert parsed.read_only_evidence is None

    snapshot["allowed_inputs"] = (*expected, "responsavel_id")
    with pytest.raises(ValidationError, match="allowed_inputs"):
        TaskSnapshot.model_validate(snapshot)


@pytest.mark.parametrize(
    "task,outcome",
    [
        (DESCRED_TASKS[0], "MANTER"),
        (CRED_TASKS[0], "APROVAR_CREDENCIAMENTO"),
    ],
)
def test_cred_binding_rejects_runtime_verified_source_claim(task: str, outcome: str) -> None:
    snapshot = _snapshot(task, outcome)
    snapshot["form_source_status"] = "BPMN_FORMDATA"
    with pytest.raises(ValidationError, match="binding"):
        TaskSnapshot.model_validate(snapshot)


def test_cred_dtos_roundtrip_schema_and_immutability_are_closed() -> None:
    descred = CredDecredentialingInputs(
        kind="cred_descred",
        decisao_cred="DESCREDENCIAR",
        **DESCRED_BASIS,
        plano_substituicao="Equivalent-provider plan",
        data_efeito_iso="human-date-text",
    )
    cred = CredCredentialingInputs(
        kind="cred_cred",
        decisao_cred="NEGAR_CREDENCIAMENTO",
        **CRED_BASIS,
        data_efeito_iso="human-date-text",
    )
    assert CredDecredentialingInputs.model_validate_json(descred.model_dump_json()) == descred
    assert CredCredentialingInputs.model_validate_json(cred.model_dump_json()) == cred
    assert set(CredDecredentialingInputs.model_json_schema()["properties"]) == {
        "kind",
        *DESCRED_INPUTS,
    }
    assert set(CredCredentialingInputs.model_json_schema()["properties"]) == {
        "kind",
        *CRED_INPUTS,
    }
    with pytest.raises(ValidationError, match="frozen"):
        descred.decisao_cred = "MANTER"


def test_cred_contracts_are_explicitly_exported() -> None:
    import maezo.portal.contracts as contracts

    assert contracts.CredDecredentialingInputs is CredDecredentialingInputs
    assert contracts.CredCredentialingInputs is CredCredentialingInputs
    assert "CredDecredentialingInputs" in contracts.__all__
    assert "CredCredentialingInputs" in contracts.__all__
