"""Contract-backed human form shape tests; no runtime or human approval claims."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal import contracts
from maezo.portal.contracts import TaskDecision, TaskSnapshot

PROCESS = "SP-OP-PAGTO-001"
CASES = [
    (
        "UT_AprovacaoAlcada",
        "pagto_aprovacao",
        {
            "decisao_pagamento": "APROVAR",
            "valor_aprovado_cents": "9007199254740993",
            "justificativa_aprovacao": "Human basis",
        },
    ),
    (
        "UT_CoordenacaoAlcada",
        "pagto_coordenacao",
        {
            "decisao_coordenacao": "assumir_aprovacao",
            "decisao_pagamento": "APROVAR",
            "valor_aprovado_cents": "9007199254740993",
            "justificativa_aprovacao": "Human basis",
        },
    ),
    (
        "UT_AprovacaoAlcada",
        "pagto_aprovacao",
        {"decisao_pagamento": "RECUSAR", "justificativa_recusa": "Human basis"},
    ),
    (
        "UT_CoordenacaoAlcada",
        "pagto_coordenacao",
        {
            "decisao_coordenacao": "assumir_aprovacao",
            "decisao_pagamento": "RECUSAR",
            "justificativa_recusa": "Human basis",
        },
    ),
    ("UT_AprovacaoAlcada", "pagto_aprovacao", {"decisao_pagamento": "SOLICITAR_INFO"}),
    (
        "UT_CoordenacaoAlcada",
        "pagto_coordenacao",
        {"decisao_coordenacao": "assumir_aprovacao", "decisao_pagamento": "SOLICITAR_INFO"},
    ),
    (
        "UT_AprovacaoAlcada",
        "pagto_aprovacao",
        {"decisao_pagamento": "CANCELAR", "justificativa_recusa": "Human basis"},
    ),
    (
        "UT_CoordenacaoAlcada",
        "pagto_coordenacao",
        {
            "decisao_coordenacao": "assumir_aprovacao",
            "decisao_pagamento": "CANCELAR",
            "justificativa_recusa": "Human basis",
        },
    ),
    ("UT_CoordenacaoAlcada", "pagto_coordenacao", {"decisao_coordenacao": "prorrogar_prazo"}),
    ("UT_CoordenacaoAlcada", "pagto_coordenacao", {"decisao_coordenacao": "seguir_analise"}),
]
BINDINGS = [("UT_AprovacaoAlcada", "pagto_aprovacao"), ("UT_CoordenacaoAlcada", "pagto_coordenacao")]
INPUTS = {
    "pagto_aprovacao": (
        "decisao_pagamento",
        "valor_aprovado_cents",
        "justificativa_aprovacao",
        "justificativa_recusa",
    ),
    "pagto_coordenacao": (
        "decisao_coordenacao",
        "decisao_pagamento",
        "valor_aprovado_cents",
        "justificativa_aprovacao",
        "justificativa_recusa",
    ),
}
FORBIDDEN = [
    "actor_id",
    "tenant_id",
    "variables",
    "human_approved",
    "tier",
    "bundle_root",
    "dataset_ref",
    "evidence_revision",
    "engine_due_at",
    "aprovador_id",
    "aprovador_tier",
    "faixa_valor",
    "grupo_aprovador",
    "lastro_confirmado",
    "valor_pagamento_cents",
]
EXPORTS = ["PagtoApprovalInputs", "PagtoCoordinationInputs"]


def command(task: str, form: str, fields: dict[str, object]) -> dict[str, object]:
    return dict(
        schema_version=1,
        command_id="command-1",
        task_id="task-1",
        process_definition_key=PROCESS,
        process_definition_version=7,
        process_definition_id=f"{PROCESS}:7:deployment",
        process_definition_digest="a" * 64,
        task_definition_key=task,
        form_key=form,
        form_version=1,
        form_digest="b" * 64,
        expected_task_revision=3,
        expected_evidence_revision=2,
        expected_evidence_digest="c" * 64,
        expected_membership_revision=1,
        inputs={"kind": form, **fields},
    )


def snapshot(task: str, form: str) -> dict[str, object]:
    data = command(task, form, {})
    for key in (
        "command_id",
        "inputs",
        "expected_task_revision",
        "expected_evidence_revision",
        "expected_evidence_digest",
        "expected_membership_revision",
    ):
        data.pop(key)
    data.update(
        snapshot_at=datetime(2026, 9, 9, tzinfo=UTC),
        form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        task_revision=3,
        assignee_ref=None,
        eligible_candidate_groups=("server-resolved",),
        evidence_revision=2,
        evidence_digest="c" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "decision"),
        allowed_inputs=INPUTS[form],
        read_only_evidence=None,
    )
    return data


@pytest.mark.parametrize("task,form,fields", CASES)
def test_explicit_human_choices_round_trip(task: str, form: str, fields: dict[str, object]) -> None:
    parsed = TaskDecision.model_validate_json(json.dumps(command(task, form, fields)))
    assert parsed.inputs.model_dump(mode="json", exclude_none=True) == {"kind": form, **fields}


@pytest.mark.parametrize("task,form,fields", CASES)
def test_frozen_closed_input_and_command(task: str, form: str, fields: dict[str, object]) -> None:
    parsed = TaskDecision.model_validate_json(json.dumps(command(task, form, fields)))
    with pytest.raises(ValidationError):
        parsed.inputs.kind = form  # type: ignore[misc,assignment]
    with pytest.raises(ValidationError):
        parsed.task_id = "other-task"
    forged = parsed.inputs.model_copy(update={"kind": "auth_junta"})
    with pytest.raises(ValidationError):
        TaskDecision.model_validate({**command(task, form, fields), "inputs": forged})


@pytest.mark.parametrize("task,form", BINDINGS)
@pytest.mark.parametrize("field", FORBIDDEN)
def test_no_browser_authority(task: str, form: str, field: str) -> None:
    fields = next(c[2] for c in CASES if c[:2] == (task, form))
    payload = command(task, form, {**fields, field: "forged"})
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("task,form", BINDINGS)
def test_snapshot_exact_draft_input_set(task: str, form: str) -> None:
    data = snapshot(task, form)
    parsed = TaskSnapshot.model_validate(data)
    assert parsed.allowed_inputs == INPUTS[form]
    assert parsed.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    for altered in ((), (*INPUTS[form], INPUTS[form][0]), (*INPUTS[form], "decisao_auditor")):
        with pytest.raises(ValidationError):
            TaskSnapshot.model_validate({**data, "allowed_inputs": altered})
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate({**data, "form_source_status": "BPMN_FORMDATA"})


@pytest.mark.parametrize("task,form", BINDINGS)
@pytest.mark.parametrize(
    "field,value",
    [
        ("process_definition_key", "SP-OP-ESCALATION-001"),
        ("task_definition_key", "UT_AnaliseAdmissibilidade"),
        ("form_key", "auth_junta"),
        ("process_definition_version", 0),
        ("form_version", True),
        ("expected_task_revision", -1),
        ("expected_evidence_digest", "ABC"),
        ("process_definition_digest", "a" * 63),
    ],
)
def test_bad_binding_or_revision_refuses_with_valid_control(
    task: str, form: str, field: str, value: object
) -> None:
    fields = next(c[2] for c in CASES if c[:2] == (task, form))
    data = command(task, form, fields)
    with pytest.raises(ValidationError):
        TaskDecision.model_validate_json(json.dumps({**data, field: value}))
    assert TaskDecision.model_validate_json(json.dumps(data)).form_key == form


@pytest.mark.parametrize("name", EXPORTS)
def test_public_exports(name: str) -> None:
    assert name in contracts.__all__
    assert getattr(contracts, name).__module__ == "maezo.portal.contracts.models"


@pytest.mark.parametrize("task,form,fields", CASES[:8])
@pytest.mark.parametrize("bad", [None, "", " \t", 1, True, [], {}])
def test_payment_conditional_basis(task: str, form: str, fields: dict[str, object], bad: object) -> None:
    key = "justificativa_aprovacao" if fields["decisao_pagamento"] == "APROVAR" else "justificativa_recusa"
    if fields["decisao_pagamento"] == "SOLICITAR_INFO":
        assert TaskDecision.model_validate(command(task, form, fields))
        return
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(command(task, form, {**fields, key: bad}))


@pytest.mark.parametrize("bad", [None, "", True, 1, 1.5, "01", "-0", "+1", "1.0", "1e2", " 1", "1\n"])
def test_payment_approval_requires_canonical_money(bad: object) -> None:
    task, form, fields = CASES[0]
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(command(task, form, {**fields, "valor_aprovado_cents": bad}))


@pytest.mark.parametrize("value", ["0", "-1", "900719925474099312345678901234567890"])
def test_payment_preserves_inherited_signed_centavos_shape(value: str) -> None:
    task, form, fields = CASES[0]
    parsed = TaskDecision.model_validate(command(task, form, {**fields, "valor_aprovado_cents": value}))
    assert parsed.inputs.model_dump()["valor_aprovado_cents"] == value


@pytest.mark.parametrize("route", ["prorrogar_prazo", "seguir_analise"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("decisao_pagamento", "APROVAR"),
        ("valor_aprovado_cents", "10"),
        ("justificativa_aprovacao", "basis"),
        ("justificativa_recusa", "basis"),
    ],
)
def test_non_deciding_coordination_cannot_smuggle_payment(route: str, field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(
            command("UT_CoordenacaoAlcada", "pagto_coordenacao", {"decisao_coordenacao": route, field: value})
        )


def test_assuming_coordination_requires_payment_decision() -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(
            command("UT_CoordenacaoAlcada", "pagto_coordenacao", {"decisao_coordenacao": "assumir_aprovacao"})
        )


@pytest.mark.parametrize(
    "task,form",
    [(task, form) for task, form in BINDINGS if any(key.startswith("decisao_") for key in INPUTS[form])],
)
@pytest.mark.parametrize("bad", [None, "", "UNKNOWN", " APROVAR", 0, True, [], {}])
def test_each_decision_discriminator_refuses_unknown_or_coerced_choices(
    task: str, form: str, bad: object
) -> None:
    fields = next(c[2] for c in CASES if c[:2] == (task, form))
    decision_keys = [key for key in fields if key.startswith("decisao_")]
    assert decision_keys
    for key in decision_keys:
        with pytest.raises(ValidationError):
            TaskDecision.model_validate_json(json.dumps(command(task, form, {**fields, key: bad})))


@pytest.mark.asyncio
@pytest.mark.parametrize("task,form", BINDINGS)
@pytest.mark.parametrize("failure", ["draft", "stale_task", "stale_evidence", "failed_source"])
async def test_real_gateway_refuses_draft_stale_or_failed_source_without_admission(
    task: str, form: str, failure: str
) -> None:
    # Reuse the existing UNIT ports, never a mocked engine integration test.
    from tests.unit.gateway.human import test_gateway as existing

    fields = next(c[2] for c in CASES if c[:2] == (task, form))
    data = snapshot(task, form)
    data.update(
        snapshot_at=datetime.now(UTC),
        assignee_ref="human-internal-1",
        eligible_candidate_groups=("medico-auditor",),
    )
    snap = TaskSnapshot.model_validate(data)
    gateway, _, transport, _, admission = await existing.setup(snap=snap)
    values = existing.command(snap).model_dump(exclude={"operation", "expected_authority_revision"})
    values["inputs"] = {"kind": form, **fields}
    if failure == "stale_task":
        values["expected_task_revision"] = snap.task_revision + 1
    if failure == "stale_evidence":
        values["expected_evidence_revision"] = snap.evidence_revision + 1
    if failure == "failed_source":
        transport.fail = True
    decision = TaskDecision.model_validate_json(json.dumps(values))
    reason = {
        "draft": "form_contract_unavailable",
        "stale_task": "revision_conflict",
        "stale_evidence": "revision_conflict",
        "failed_source": "task_unavailable",
    }[failure]
    with pytest.raises(existing.GatewayRefusalError, match=reason):
        await gateway.submit_decision(
            session_secret=existing.SECRET,
            csrf_token=existing.CSRF,
            origin=existing.config().public_origin,
            decision=decision,
            expected_authority_revision=7,
        )
    assert not admission.calls
    with pytest.raises(existing.GatewayRefusalError, match="production_capabilities_unavailable"):
        existing.create_production_gateway(resolver=gateway._resolver, scope=existing.SCOPE)
