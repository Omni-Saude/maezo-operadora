"""Closed LGPD-DSR DPO review shape from the current contract and BPMN guards."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import LgpdDsrDecisionInputs, TaskDecision, TaskSnapshot

PROCESS = "SP-OP-LGPD-DSR-001"
TASK = "UT_RevisaoDpo"
FORM = "lgpd_decisao"
OUTCOMES = ("APROVAR_ENVIO", "EXECUTAR_E_ENVIAR", "NEGAR_FUNDAMENTADO")
INPUTS = ("decisao_dsr", "fundamentacao_legal")


def _command(outcome: object, *, basis: object = None) -> dict[str, object]:
    inputs: dict[str, object] = {"kind": FORM, "decisao_dsr": outcome}
    if basis is not None:
        inputs["fundamentacao_legal"] = basis
    return {
        "schema_version": 1,
        "command_id": "command-lgpd-1",
        "task_id": "task-lgpd-1",
        "process_definition_key": PROCESS,
        "process_definition_version": 7,
        "process_definition_id": f"{PROCESS}:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": TASK,
        "form_key": FORM,
        "form_version": 1,
        "form_digest": "b" * 64,
        "expected_task_revision": 3,
        "expected_evidence_revision": 2,
        "expected_evidence_digest": "c" * 64,
        "expected_membership_revision": 1,
        "inputs": inputs,
    }


def _snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_at": datetime(2026, 9, 9, tzinfo=UTC),
        "task_id": "task-lgpd-1",
        "process_definition_key": PROCESS,
        "process_definition_version": 7,
        "process_definition_id": f"{PROCESS}:7:deployment",
        "process_definition_digest": "a" * 64,
        "task_definition_key": TASK,
        "form_key": FORM,
        "form_version": 1,
        "form_digest": "b" * 64,
        "form_source_status": "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        "task_revision": 3,
        "assignee_ref": None,
        "eligible_candidate_groups": ("dpo", "juridico-privacidade"),
        "evidence_revision": 2,
        "evidence_digest": "c" * 64,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": INPUTS,
        "read_only_evidence": None,
    }


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_dpo_task_accepts_each_explicit_fail_closed_outcome(outcome: str) -> None:
    basis = "Human-reviewed legal basis" if outcome == "NEGAR_FUNDAMENTADO" else None
    parsed = TaskDecision.model_validate_json(json.dumps(_command(outcome, basis=basis)))
    assert parsed.inputs.kind == FORM
    assert parsed.inputs.model_dump()["decisao_dsr"] == outcome


@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_legal_denial_basis_refuses_missing_blank_and_coerced_values(bad: object) -> None:
    payload = _command("NEGAR_FUNDAMENTADO", basis="Human-reviewed legal basis")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs["fundamentacao_legal"] = bad
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))


def test_missing_legal_basis_refuses_then_fresh_complete_command_recovers() -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command("NEGAR_FUNDAMENTADO"))
    recovered = TaskDecision.model_validate(
        _command("NEGAR_FUNDAMENTADO", basis="Human-reviewed legal basis")
    )
    assert recovered.inputs.kind == FORM


@pytest.mark.parametrize("outcome", OUTCOMES[:2])
def test_non_denial_outcomes_do_not_invent_legal_basis(outcome: str) -> None:
    parsed = TaskDecision.model_validate(_command(outcome))
    assert parsed.inputs.model_dump(exclude_none=True) == {
        "kind": FORM,
        "decisao_dsr": outcome,
    }


@pytest.mark.parametrize(
    "field",
    [
        "responsavel_id",
        "revisor_id",
        "dpo_id",
        "tenant",
        "tenant_id",
        "tier",
        "human_approved",
        "variables",
        "titular_pseudo_id",
        "tipo_requisicao",
        "detalhes_requisicao",
        "envolve_dados_saude",
        "identidade_confirmada",
        "roteamento_dsr",
        "grupo_revisor",
        "sla_resposta",
        "sla_alerta",
        "data_solicitacao_iso",
        "canal",
        "pacote_dados",
        "tem_fundamentacao",
    ],
)
def test_browser_cannot_supply_actor_dpo_phi_routing_or_effect_facts(field: str) -> None:
    payload = _command("APROVAR_ENVIO")
    inputs = payload["inputs"]
    assert isinstance(inputs, dict)
    inputs[field] = "forged"
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize(
    "bad",
    ["NEGAR", "APROVAR", "INFORMATIVO", "aprovAR_ENVIO", "", None, 1, True, [], {}],
)
def test_routing_values_unknowns_and_coercions_are_not_dpo_outcomes(bad: object) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(_command(bad))


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_definition_key", "UT_RevisaoJuridicaNip"),
        ("process_definition_key", "SP-OP-NIP-001"),
        ("form_key", "nip_decisao"),
    ],
)
def test_cross_family_or_form_substitution_refuses(field: str, value: str) -> None:
    payload = _command("APROVAR_ENVIO")
    payload[field] = value
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


def test_snapshot_exposes_only_reconciled_draft_inputs() -> None:
    snapshot = _snapshot()
    parsed = TaskSnapshot.model_validate(snapshot)
    assert parsed.allowed_inputs == INPUTS
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert parsed.read_only_evidence is None
    snapshot["allowed_inputs"] = (*INPUTS, "human_approved")
    with pytest.raises(ValidationError, match="allowed_inputs"):
        TaskSnapshot.model_validate(snapshot)


def test_draft_source_cannot_claim_runtime_verified_formdata() -> None:
    snapshot = _snapshot()
    snapshot["form_source_status"] = "BPMN_FORMDATA"
    with pytest.raises(ValidationError, match="binding"):
        TaskSnapshot.model_validate(snapshot)


def test_lgpd_dto_roundtrip_schema_immutability_and_export_are_closed() -> None:
    decision = LgpdDsrDecisionInputs(
        kind="lgpd_decisao",
        decisao_dsr="NEGAR_FUNDAMENTADO",
        fundamentacao_legal="Human-reviewed legal basis",
    )
    assert LgpdDsrDecisionInputs.model_validate_json(decision.model_dump_json()) == decision
    assert set(LgpdDsrDecisionInputs.model_json_schema()["properties"]) == {"kind", *INPUTS}
    with pytest.raises(ValidationError, match="frozen"):
        decision.decisao_dsr = "APROVAR_ENVIO"

    import maezo.portal.contracts as contracts

    assert contracts.LgpdDsrDecisionInputs is LgpdDsrDecisionInputs
    assert "LgpdDsrDecisionInputs" in contracts.__all__
