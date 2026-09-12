"""Contract-backed human form shape tests; no runtime or human approval claims."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal import contracts
from maezo.portal.contracts import TaskDecision, TaskSnapshot

PROCESS = "SP-OP-AUTH-001"
CASES = [
    ("UT_DecidirPendenciaExpirada", "auth_pendencia", {"decisao_pendencia": "cancelar_guia"}),
    ("UT_DecidirPendenciaExpirada", "auth_pendencia", {"decisao_pendencia": "conceder_prazo_extra"}),
    ("UT_DecidirPendenciaExpirada", "auth_pendencia", {"decisao_pendencia": "seguir_analise"}),
]
BINDINGS = [("UT_DecidirPendenciaExpirada", "auth_pendencia")]
INPUTS = {"auth_pendencia": ("decisao_pendencia",)}
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
    "auditor_id",
    "decisao_auditor",
    "prazo_extra",
]
EXPORTS = ["AuthPendingInputs"]


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


@pytest.mark.parametrize(
    "bad", ["cancelar_pedido", "APROVAR", "CANCELAR_GUIA", " seguir_analise", None, True, 1, [], {}]
)
def test_pending_vocabulary_is_family_specific(bad: object) -> None:
    with pytest.raises(ValidationError):
        TaskDecision.model_validate(
            command("UT_DecidirPendenciaExpirada", "auth_pendencia", {"decisao_pendencia": bad})
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


def test_all_43_actual_bpmn_tasks_have_closed_source_shapes_only() -> None:
    from pathlib import Path
    from xml.etree import ElementTree

    from maezo.portal.contracts.models import _BINDINGS, _INPUTS_BY_FORM

    root = Path(__file__).resolve().parents[3]
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    tasks = set()
    for path in (root / "spec/processes/bpmn").glob("SP-OP-*.bpmn"):
        tree = ElementTree.parse(path)
        for process in tree.findall("b:process", ns):
            tasks.update(
                (process.attrib["id"], task.attrib["id"]) for task in process.findall(".//b:userTask", ns)
            )
    assert tasks == set(_BINDINGS)
    assert len(tasks) == 43
    assert len(_INPUTS_BY_FORM) == 31
