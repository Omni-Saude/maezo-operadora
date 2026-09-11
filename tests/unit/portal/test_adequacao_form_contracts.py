"""Closed ADEQUACAO forms derived from current contract, BPMN and worker consumption."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    AdequacaoCoordinationInputs,
    AdequacaoDecisionInputs,
    TaskDecision,
    TaskSnapshot,
)

PROCESS = "SP-OP-ADEQUACAO-001"
FALLBACK_TASK = "UT_DecisaoFallback"
COORDINATION_TASK = "UT_CoordenacaoRede"
REMEDIATION_OUTCOMES = (
    "MONITORAR_OK",
    "ENCAMINHAR_CRED",
    "COMPROMISSO_FALLBACK",
    "SOLICITAR_INFO",
)
COORDINATION_OUTCOMES = ("assumir_decisao", "prorrogar_prazo", "seguir_analise")
FALLBACK_BASIS = {
    "tipo_fallback": "livre_escolha",
    "justificativa_fallback": "Human-reviewed fallback basis",
    "referencia_regulatoria": "regulatory-reference",
}
DECISION_INPUTS = (
    "decisao_remediacao",
    "tipo_fallback",
    "justificativa_fallback",
    "referencia_regulatoria",
    "estimativa_custo_cents",
)
COORDINATION_INPUTS = ("decisao_coordenacao", *DECISION_INPUTS)


def _command(task: str, form: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-adequacao-1",
        "task_id": "task-adequacao-1",
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


def _remediation(outcome: str) -> dict[str, object]:
    fields: dict[str, object] = {"decisao_remediacao": outcome}
    if outcome == "COMPROMISSO_FALLBACK":
        fields.update(FALLBACK_BASIS)
    return fields


def _snapshot(task: str, form: str, allowed_inputs: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_at": datetime(2026, 9, 9, tzinfo=UTC),
        "task_id": "task-adequacao-1",
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
        "eligible_candidate_groups": ("gestao-rede",),
        "evidence_revision": 2,
        "evidence_digest": "c" * 64,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": allowed_inputs,
        "read_only_evidence": None,
    }


@pytest.mark.parametrize("outcome", REMEDIATION_OUTCOMES)
def test_fallback_task_accepts_each_documented_remediation(outcome: str) -> None:
    command = _command(FALLBACK_TASK, "adequacao_decisao", _remediation(outcome))
    parsed = TaskDecision.model_validate_json(json.dumps(command))
    assert parsed.inputs.kind == "adequacao_decisao"
    assert parsed.inputs.model_dump()["decisao_remediacao"] == outcome


@pytest.mark.parametrize("outcome", REMEDIATION_OUTCOMES)
def test_coordination_can_assume_each_documented_remediation(outcome: str) -> None:
    fields = {"decisao_coordenacao": "assumir_decisao", **_remediation(outcome)}
    command = _command(COORDINATION_TASK, "adequacao_coordenacao", fields)
    parsed = TaskDecision.model_validate_json(json.dumps(command))
    assert parsed.inputs.kind == "adequacao_coordenacao"
    assert parsed.inputs.model_dump()["decisao_remediacao"] == outcome


@pytest.mark.parametrize("outcome", COORDINATION_OUTCOMES[1:])
def test_non_assuming_coordination_has_no_remediation_payload(outcome: str) -> None:
    command = _command(
        COORDINATION_TASK,
        "adequacao_coordenacao",
        {"decisao_coordenacao": outcome},
    )
    assert TaskDecision.model_validate(command).inputs.model_dump(exclude_none=True) == {
        "kind": "adequacao_coordenacao",
        "decisao_coordenacao": outcome,
    }


@pytest.mark.parametrize(
    "task,form",
    [
        (FALLBACK_TASK, "adequacao_decisao"),
        (COORDINATION_TASK, "adequacao_coordenacao"),
    ],
)
@pytest.mark.parametrize("field", tuple(FALLBACK_BASIS))
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_fallback_basis_refuses_missing_blank_and_coerced_values(
    task: str, form: str, field: str, bad: object
) -> None:
    fields = _remediation("COMPROMISSO_FALLBACK")
    if task == COORDINATION_TASK:
        fields["decisao_coordenacao"] = "assumir_decisao"
    fields[field] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(_command(task, form, fields)))


@pytest.mark.parametrize("field", tuple(FALLBACK_BASIS))
def test_missing_fallback_basis_refuses_then_fresh_complete_command_recovers(field: str) -> None:
    fields = _remediation("COMPROMISSO_FALLBACK")
    del fields[field]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(FALLBACK_TASK, "adequacao_decisao", fields))
    recovered = TaskDecision.model_validate(
        _command(FALLBACK_TASK, "adequacao_decisao", _remediation("COMPROMISSO_FALLBACK"))
    )
    assert recovered.inputs.kind == "adequacao_decisao"


def test_assuming_coordination_requires_a_remediation_decision() -> None:
    command = _command(
        COORDINATION_TASK,
        "adequacao_coordenacao",
        {"decisao_coordenacao": "assumir_decisao"},
    )
    with pytest.raises(ValidationError, match="decisao_remediacao"):
        TaskDecision.model_validate(command)


@pytest.mark.parametrize("outcome", COORDINATION_OUTCOMES[1:])
@pytest.mark.parametrize("field", DECISION_INPUTS)
def test_non_assuming_coordination_rejects_stale_remediation_fields(outcome: str, field: str) -> None:
    values: dict[str, object] = {
        "decisao_remediacao": "MONITORAR_OK",
        "tipo_fallback": "livre_escolha",
        "justificativa_fallback": "stale-value",
        "referencia_regulatoria": "stale-value",
        "estimativa_custo_cents": "1",
    }
    command = _command(
        COORDINATION_TASK,
        "adequacao_coordenacao",
        {"decisao_coordenacao": outcome, field: values[field]},
    )
    with pytest.raises(ValidationError, match="does not accept"):
        TaskDecision.model_validate(command)


@pytest.mark.parametrize(
    "bad",
    [1, True, 1.0, "01", "+1", "1.0", " 1", "1 ", "1e2", [], {}],
)
def test_cost_estimate_uses_exact_canonical_decimal_integer_string(bad: object) -> None:
    fields = _remediation("COMPROMISSO_FALLBACK")
    fields["estimativa_custo_cents"] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(FALLBACK_TASK, "adequacao_decisao", fields))


@pytest.mark.parametrize("value", ["0", "1", "-1", "999999999999999999999999999999"])
def test_cost_estimate_has_no_invented_business_ceiling(value: str) -> None:
    fields = _remediation("COMPROMISSO_FALLBACK")
    fields["estimativa_custo_cents"] = value
    parsed = TaskDecision.model_validate(_command(FALLBACK_TASK, "adequacao_decisao", fields))
    assert parsed.inputs.model_dump()["estimativa_custo_cents"] == value


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "tenant",
        "tenant_id",
        "tier",
        "human_approved",
        "variables",
        "gap_adequacao",
        "roteamento_remediacao",
        "routing",
        "regiao_saude",
        "especialidade",
        "tipo_carater",
        "prestadores_disponiveis",
        "cobertura_geo_suficiente",
        "dados_geo_completos",
        "sla",
        "sla_remediacao",
        "sla_alerta",
        "dossie_andre",
        "compromisso_fallback_registrado",
    ],
)
@pytest.mark.parametrize(
    "task,form",
    [
        (FALLBACK_TASK, "adequacao_decisao"),
        (COORDINATION_TASK, "adequacao_coordenacao"),
    ],
)
def test_browser_cannot_supply_actor_network_routing_or_effect_facts(
    field: str, task: str, form: str
) -> None:
    fields = _remediation("MONITORAR_OK")
    if task == COORDINATION_TASK:
        fields["decisao_coordenacao"] = "assumir_decisao"
    fields[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(task, form, fields))


@pytest.mark.parametrize(
    "bad",
    ["MONITORAR", "ANALISE_HUMANA", "COMPROMETER", "monitorar_ok", "", 1, True, [], {}],
)
def test_dmn_routing_and_coerced_values_are_not_remediation_outcomes(bad: object) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(FALLBACK_TASK, "adequacao_decisao", {"decisao_remediacao": bad}))


@pytest.mark.parametrize("bad", ["ASSUMIR_DECISAO", "estender_prazo", "", 1, True, [], {}])
def test_unknown_and_coerced_coordination_controls_refuse(bad: object) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(
            _command(
                COORDINATION_TASK,
                "adequacao_coordenacao",
                {"decisao_coordenacao": bad},
            )
        )


@pytest.mark.parametrize(
    "task,form,inputs,wrong_form",
    [
        (FALLBACK_TASK, "adequacao_decisao", _remediation("MONITORAR_OK"), "adequacao_coordenacao"),
        (
            COORDINATION_TASK,
            "adequacao_coordenacao",
            {"decisao_coordenacao": "seguir_analise"},
            "adequacao_decisao",
        ),
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
        (FALLBACK_TASK, "adequacao_decisao", DECISION_INPUTS),
        (COORDINATION_TASK, "adequacao_coordenacao", COORDINATION_INPUTS),
    ],
)
def test_snapshot_exposes_only_reconciled_draft_inputs(
    task: str, form: str, allowed: tuple[str, ...]
) -> None:
    snapshot = _snapshot(task, form, allowed)
    parsed = TaskSnapshot.model_validate(snapshot)
    assert parsed.allowed_inputs == allowed
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    snapshot["allowed_inputs"] = (*allowed, "responsavel_id")
    with pytest.raises(ValidationError, match="allowed_inputs"):
        TaskSnapshot.model_validate(snapshot)


def test_adequacao_dtos_roundtrip_schema_immutability_and_exports_are_closed() -> None:
    decision = AdequacaoDecisionInputs(
        kind="adequacao_decisao",
        decisao_remediacao="COMPROMISSO_FALLBACK",
        tipo_fallback="livre_escolha",
        justificativa_fallback="Human-reviewed fallback basis",
        referencia_regulatoria="regulatory-reference",
        estimativa_custo_cents="12345",
    )
    coordination = AdequacaoCoordinationInputs(
        kind="adequacao_coordenacao",
        decisao_coordenacao="assumir_decisao",
        decisao_remediacao="MONITORAR_OK",
    )
    assert AdequacaoDecisionInputs.model_validate_json(decision.model_dump_json()) == decision
    assert AdequacaoCoordinationInputs.model_validate_json(coordination.model_dump_json()) == coordination
    assert set(AdequacaoDecisionInputs.model_json_schema()["properties"]) == {"kind", *DECISION_INPUTS}
    assert set(AdequacaoCoordinationInputs.model_json_schema()["properties"]) == {
        "kind",
        *COORDINATION_INPUTS,
    }
    with pytest.raises(ValidationError, match="frozen"):
        decision.decisao_remediacao = "MONITORAR_OK"

    import maezo.portal.contracts as contracts

    assert contracts.AdequacaoDecisionInputs is AdequacaoDecisionInputs
    assert contracts.AdequacaoCoordinationInputs is AdequacaoCoordinationInputs
    assert "AdequacaoDecisionInputs" in contracts.__all__
    assert "AdequacaoCoordinationInputs" in contracts.__all__
