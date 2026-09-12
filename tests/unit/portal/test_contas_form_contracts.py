"""Closed CONTAS portal forms from SP-OP-CONTAS-001 and its two User Tasks.

These tests prove DTO parsing only. They do not prove a deployed process version, authoritative
task state, actor provenance, BRL engine conversion, fraud evidence, or PHI projection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    ContasCoordinationInputs,
    ContasDecisionInputs,
    TaskDecision,
    TaskSnapshot,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
DIGEST = "d" * 64
ANALYST_INPUTS = (
    "decisao_contas",
    "justificativa_glosa",
    "codigo_glosa_tiss",
    "valor_glosado_centavos",
    "valor_liberado_centavos",
    "justificativa_devolucao",
)
COORDINATION_INPUTS = (*ANALYST_INPUTS, "decisao_coordenacao")


def _decision(*, task_key: str, form_key: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-contas-1",
        "task_id": "task-contas-1",
        "process_definition_key": "SP-OP-CONTAS-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-CONTAS-001:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "expected_task_revision": 11,
        "expected_evidence_revision": 4,
        "expected_evidence_digest": DIGEST,
        "expected_membership_revision": 3,
        "inputs": inputs,
    }


def _snapshot(*, task_key: str, form_key: str, allowed_inputs: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_at": NOW,
        "task_id": "task-contas-1",
        "process_definition_key": "SP-OP-CONTAS-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-CONTAS-001:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "form_source_status": "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        "task_revision": 11,
        "assignee_ref": None,
        "eligible_candidate_groups": (
            "coordenacao-contas" if task_key == "UT_CoordenacaoContasAssume" else "auditoria-contas",
        ),
        "evidence_revision": 4,
        "evidence_digest": DIGEST,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": allowed_inputs,
        "read_only_evidence": None,
    }


@pytest.mark.parametrize(
    "inputs",
    [
        {"kind": "contas_decisao", "decisao_contas": "PAGAR", "valor_liberado_centavos": "9007199254740993"},
        {
            "kind": "contas_decisao",
            "decisao_contas": "GLOSAR",
            "justificativa_glosa": "Divergencia confirmada pelo analista",
            "codigo_glosa_tiss": "1401",
            "valor_glosado_centavos": "15000",
        },
        {
            "kind": "contas_decisao",
            "decisao_contas": "PAGAR_PARCIAL",
            "justificativa_glosa": "Parte da cobranca diverge da tabela",
            "codigo_glosa_tiss": "1402",
            "valor_glosado_centavos": "6000",
            "valor_liberado_centavos": "9000",
        },
        {
            "kind": "contas_decisao",
            "decisao_contas": "DEVOLVER",
            "justificativa_devolucao": "Documentacao da linha ausente",
        },
        {"kind": "contas_decisao", "decisao_contas": "ENCAMINHAR_FRAUDE"},
    ],
)
def test_each_explicit_analyst_outcome_parses_from_json(inputs: dict[str, object]) -> None:
    decision = TaskDecision.model_validate_json(
        json.dumps(_decision(task_key="UT_AnalistaContas", form_key="contas_decisao", inputs=inputs))
    )

    assert isinstance(decision.inputs, ContasDecisionInputs)
    assert decision.inputs.decisao_contas == inputs["decisao_contas"]


@pytest.mark.parametrize(
    "coordination",
    ["assumir_analise", "prorrogar_prazo", "seguir_analise"],
)
def test_coordinator_parses_the_same_decision_fields_plus_its_disposition(
    coordination: str,
) -> None:
    inputs = {
        "kind": "contas_coordenacao",
        "decisao_contas": "PAGAR_PARCIAL",
        "justificativa_glosa": "Reducao confirmada pela coordenacao",
        "codigo_glosa_tiss": "1402",
        "valor_glosado_centavos": "10000000000000001",
        "valor_liberado_centavos": "20000000000000003",
        "decisao_coordenacao": coordination,
    }
    decision = TaskDecision.model_validate_json(
        json.dumps(
            _decision(
                task_key="UT_CoordenacaoContasAssume",
                form_key="contas_coordenacao",
                inputs=inputs,
            )
        )
    )

    assert isinstance(decision.inputs, ContasCoordinationInputs)
    assert decision.inputs.valor_glosado_centavos is not None
    assert decision.inputs.valor_glosado_centavos.as_int() == 10_000_000_000_000_001
    assert decision.inputs.decisao_coordenacao == coordination
    assert decision.model_dump(mode="json")["inputs"]["valor_liberado_centavos"] == "20000000000000003"


@pytest.mark.parametrize(
    ("decision", "present", "missing"),
    [
        (
            "GLOSAR",
            {
                "justificativa_glosa": "Fundamento",
                "codigo_glosa_tiss": "1401",
                "valor_glosado_centavos": "1",
            },
            "justificativa_glosa",
        ),
        (
            "GLOSAR",
            {
                "justificativa_glosa": "Fundamento",
                "codigo_glosa_tiss": "1401",
                "valor_glosado_centavos": "1",
            },
            "codigo_glosa_tiss",
        ),
        (
            "GLOSAR",
            {
                "justificativa_glosa": "Fundamento",
                "codigo_glosa_tiss": "1401",
                "valor_glosado_centavos": "1",
            },
            "valor_glosado_centavos",
        ),
        (
            "PAGAR_PARCIAL",
            {
                "justificativa_glosa": "Fundamento",
                "codigo_glosa_tiss": "1401",
                "valor_glosado_centavos": "1",
                "valor_liberado_centavos": "1",
            },
            "valor_liberado_centavos",
        ),
        ("PAGAR", {"valor_liberado_centavos": "1"}, "valor_liberado_centavos"),
        ("DEVOLVER", {"justificativa_devolucao": "Corrigir conta"}, "justificativa_devolucao"),
    ],
)
def test_conditional_contract_fields_are_required(
    decision: str, present: dict[str, object], missing: str
) -> None:
    inputs = {"kind": "contas_decisao", "decisao_contas": decision, **present}
    del inputs[missing]

    with pytest.raises(ValidationError, match=missing):
        ContasDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize("field", ["valor_glosado_centavos", "valor_liberado_centavos"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_required_monetary_amounts_must_be_positive(field: str, value: str) -> None:
    inputs: dict[str, object] = {
        "kind": "contas_decisao",
        "decisao_contas": "PAGAR_PARCIAL",
        "justificativa_glosa": "Fundamento",
        "codigo_glosa_tiss": "1401",
        "valor_glosado_centavos": "1",
        "valor_liberado_centavos": "1",
    }
    inputs[field] = value

    with pytest.raises(ValidationError, match=field):
        ContasDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize("value", [1, True, 1.0, "01", "+1", "1.0", "1e2", " 1"])
def test_contas_money_reuses_canonical_centavos_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        ContasDecisionInputs.model_validate(
            {"kind": "contas_decisao", "decisao_contas": "PAGAR", "valor_liberado_centavos": value}
        )


def test_coordinator_requires_its_explicit_disposition() -> None:
    with pytest.raises(ValidationError, match="decisao_coordenacao"):
        ContasCoordinationInputs.model_validate(
            {"kind": "contas_coordenacao", "decisao_contas": "PAGAR", "valor_liberado_centavos": "1"}
        )


@pytest.mark.parametrize(
    "forged",
    [
        "actor",
        "analista_id",
        "tenant",
        "tier",
        "human_approved",
        "engine_variables",
        "indicio_fraude_sinalizado",
        "motivo_devolucao",
    ],
)
def test_contas_inputs_reject_browser_authority_and_arbitrary_variables(forged: str) -> None:
    inputs: dict[str, object] = {
        "kind": "contas_decisao",
        "decisao_contas": "PAGAR",
        "valor_liberado_centavos": "1",
        forged: "forged",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContasDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize(
    ("task_key", "form_key", "allowed_inputs"),
    [
        ("UT_AnalistaContas", "contas_decisao", ANALYST_INPUTS),
        ("UT_CoordenacaoContasAssume", "contas_coordenacao", COORDINATION_INPUTS),
    ],
)
def test_contas_snapshot_binding_is_closed_and_draft_marked(
    task_key: str, form_key: str, allowed_inputs: tuple[str, ...]
) -> None:
    snapshot = TaskSnapshot.model_validate(
        _snapshot(task_key=task_key, form_key=form_key, allowed_inputs=allowed_inputs)
    )

    assert snapshot.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert snapshot.allowed_inputs == allowed_inputs
    assert snapshot.read_only_evidence is None

    wrong_source = _snapshot(task_key=task_key, form_key=form_key, allowed_inputs=allowed_inputs)
    wrong_source["form_source_status"] = "BPMN_FORMDATA"
    with pytest.raises(ValidationError, match="binding"):
        TaskSnapshot.model_validate(wrong_source)


@pytest.mark.parametrize(
    ("task_key", "form_key", "inputs"),
    [
        (
            "UT_CoordenacaoContasAssume",
            "contas_decisao",
            {"kind": "contas_decisao", "decisao_contas": "PAGAR", "valor_liberado_centavos": "1"},
        ),
        (
            "UT_AnalistaContas",
            "contas_coordenacao",
            {
                "kind": "contas_coordenacao",
                "decisao_contas": "PAGAR",
                "valor_liberado_centavos": "1",
                "decisao_coordenacao": "assumir_analise",
            },
        ),
    ],
)
def test_contas_task_forms_cannot_be_cross_bound(
    task_key: str, form_key: str, inputs: dict[str, object]
) -> None:
    payload = _decision(task_key=task_key, form_key=form_key, inputs=inputs)

    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


def test_contas_has_no_unreviewed_read_only_or_phi_projection() -> None:
    payload = _snapshot(
        task_key="UT_AnalistaContas",
        form_key="contas_decisao",
        allowed_inputs=ANALYST_INPUTS,
    )
    payload["read_only_evidence"] = {
        "kind": "contas_decisao",
        "beneficiario_nome": "must-not-cross-the-unresolved-boundary",
    }

    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(payload)


def test_contas_inputs_are_immutable_and_json_schema_is_closed() -> None:
    inputs = ContasDecisionInputs(
        kind="contas_decisao",
        decisao_contas="PAGAR",
        valor_liberado_centavos="1",
    )
    with pytest.raises(ValidationError):
        inputs.decisao_contas = "DEVOLVER"

    for contract in (ContasDecisionInputs, ContasCoordinationInputs):
        schema = contract.model_json_schema()
        assert schema["additionalProperties"] is False
