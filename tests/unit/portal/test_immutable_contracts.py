from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    AuthDecisionInputs,
    AuthJuntaInputs,
    Centavos,
    EscalationDecisionInputs,
    HumanCommandReceipt,
    HumanPrincipal,
    MembershipBinding,
    PagtoAdmissibilityEvidence,
    PagtoAdmissibilityInputs,
    SubjectBinding,
    TaskDecision,
    TaskSnapshot,
)

SHA256 = "a" * 64
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _principal() -> HumanPrincipal:
    return HumanPrincipal(
        schema_version=1,
        principal_ref="principal-7",
        issuer="https://issuer.example/tenant",
        subject="subject-9",
        tenant="amh",
        membership_revision=3,
        memberships=(
            MembershipBinding(
                membership_ref="membership-4",
                roles=("employee",),
                groups=("medico-auditor",),
            ),
        ),
        session_ref="session-8",
        authenticated_at=NOW,
        subject_bindings=(SubjectBinding(kind="beneficiary", resource_ref="beneficiary-opaque-2"),),
    )


def _snapshot(**updates: object) -> TaskSnapshot:
    values: dict[str, object] = {
        "schema_version": 1,
        "snapshot_at": NOW,
        "task_id": "task-1",
        "process_definition_key": "SP-OP-AUTH-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-AUTH-001:7:deployment-2",
        "process_definition_digest": SHA256,
        "task_definition_key": "UT_AnaliseMedicoAuditor",
        "form_key": "auth_decisao",
        "form_version": 1,
        "form_digest": SHA256,
        "form_source_status": "BPMN_FORMDATA",
        "task_revision": 11,
        "assignee_ref": None,
        "eligible_candidate_groups": ("medico-auditor",),
        "evidence_revision": 4,
        "evidence_digest": SHA256,
        "engine_due_at": NOW + timedelta(hours=1),
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": (
            "decisao_auditor",
            "justificativa_clinica",
            "cid10_referencia",
            "fundamentacao_dut",
        ),
        "read_only_evidence": None,
    }
    values.update(updates)
    return TaskSnapshot.model_validate(values)


def _decision(**updates: object) -> TaskDecision:
    values: dict[str, object] = {
        "schema_version": 1,
        "command_id": "command-1",
        "task_id": "task-1",
        "process_definition_key": "SP-OP-AUTH-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-AUTH-001:7:deployment-2",
        "process_definition_digest": SHA256,
        "task_definition_key": "UT_AnaliseMedicoAuditor",
        "form_key": "auth_decisao",
        "form_version": 1,
        "form_digest": SHA256,
        "expected_task_revision": 11,
        "expected_evidence_revision": 4,
        "expected_evidence_digest": SHA256,
        "expected_membership_revision": 3,
        "inputs": AuthDecisionInputs(kind="auth_decisao", decisao_auditor="APROVAR"),
    }
    values.update(updates)
    return TaskDecision.model_validate(values)


def test_human_principal_is_deeply_immutable() -> None:
    principal = _principal()

    with pytest.raises(ValidationError):
        principal.tenant = "other"
    with pytest.raises(ValidationError):
        principal.memberships[0].groups = ("other",)
    with pytest.raises(AttributeError):
        principal.memberships.append("other")  # type: ignore[attr-defined]


@pytest.mark.parametrize("forged", ["actor", "tenant", "tier", "human_approved", "engine_variables"])
def test_task_decision_rejects_authority_and_open_engine_fields(forged: str) -> None:
    payload = _decision().model_dump(mode="python")
    payload[forged] = {"arbitrary": "process-variable"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskDecision.model_validate(payload)


@pytest.mark.parametrize("forged", ["actor", "tenant", "tier", "human_approved", "engine_variables"])
def test_typed_inputs_reject_nested_authority_and_open_engine_fields(forged: str) -> None:
    payload = _decision().model_dump(mode="python")
    nested = payload["inputs"]
    assert isinstance(nested, dict)
    nested[forged] = True

    with pytest.raises(ValidationError):
        TaskDecision.model_validate(payload)


def test_task_decision_and_nested_inputs_are_immutable() -> None:
    decision = _decision()

    with pytest.raises(ValidationError):
        decision.task_id = "other-task"
    with pytest.raises(ValidationError):
        decision.inputs.kind = "auth_junta"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", True),
        ("process_definition_version", True),
        ("process_definition_version", "7"),
        ("form_version", 0),
        ("task_revision", -1),
        ("evidence_revision", "4"),
        ("process_definition_digest", "not-a-digest"),
        ("form_digest", "A" * 64),
        ("evidence_digest", "a" * 63),
    ],
)
def test_snapshot_rejects_malformed_versions_revisions_and_digests(field: str, bad_value: object) -> None:
    payload = _snapshot().model_dump(mode="python")
    payload[field] = bad_value

    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(payload)


def test_snapshot_rejects_missing_binding_component() -> None:
    payload = _snapshot().model_dump(mode="python")
    del payload["form_digest"]

    with pytest.raises(ValidationError, match="Field required"):
        TaskSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "process_definition_version",
        "process_definition_digest",
        "form_version",
        "form_digest",
        "expected_task_revision",
        "expected_evidence_revision",
        "expected_evidence_digest",
        "expected_membership_revision",
    ],
)
def test_decision_rejects_missing_version_digest_or_revision(field: str) -> None:
    payload = _decision().model_dump(mode="python")
    del payload[field]

    with pytest.raises(ValidationError, match="Field required"):
        TaskDecision.model_validate(payload)


def test_timestamps_must_be_aware_utc() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        _snapshot(snapshot_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="UTC"):
        _snapshot(engine_due_at=NOW.astimezone(timezone(timedelta(hours=-3))))


@pytest.mark.parametrize(
    ("process_key", "task_key", "form_key"),
    [
        ("SP-OP-PAGTO-001", "UT_AnaliseMedicoAuditor", "auth_decisao"),
        ("SP-OP-AUTH-001", "UT_DecidirPendenciaExpirada", "auth_decisao"),
        ("SP-OP-AUTH-001", "UT_RegistrarParecerJunta", "auth_decisao"),
    ],
)
def test_snapshot_rejects_unknown_or_mismatched_process_task_form_binding(
    process_key: str, task_key: str, form_key: str
) -> None:
    with pytest.raises(ValidationError, match="binding"):
        _snapshot(
            process_definition_key=process_key,
            task_definition_key=task_key,
            form_key=form_key,
        )


def test_auth_negative_requires_all_contractual_clinical_fields() -> None:
    with pytest.raises(ValidationError, match="NEGAR"):
        AuthDecisionInputs(kind="auth_decisao", decisao_auditor="NEGAR")

    inputs = AuthDecisionInputs(
        kind="auth_decisao",
        decisao_auditor="NEGAR",
        justificativa_clinica="Fundamentacao restrita a Zona PHI",
        cid10_referencia="Z00.0",
        fundamentacao_dut="DUT contratual aplicavel",
    )
    decision = _decision(inputs=inputs)
    assert isinstance(decision.inputs, AuthDecisionInputs)
    assert decision.inputs.decisao_auditor == "NEGAR"
    assert "auditor_id" not in decision.model_dump(mode="python")["inputs"]


def test_junta_has_distinct_outcome_discriminator_and_binding() -> None:
    inputs = AuthJuntaInputs(kind="auth_junta", decisao_auditor="APROVAR")
    decision = _decision(
        task_definition_key="UT_RegistrarParecerJunta",
        form_key="auth_junta",
        inputs=inputs,
    )
    assert decision.inputs.kind == "auth_junta"

    with pytest.raises(ValidationError):
        AuthJuntaInputs.model_validate({"kind": "auth_junta", "decisao_auditor": "JUNTA_MEDICA"})


@pytest.mark.parametrize("task_key", ["UT_TratarEscalonamento", "UT_SupervisorAssume"])
def test_escalation_tasks_share_only_the_explicit_form(task_key: str) -> None:
    decision = _decision(
        process_definition_key="SP-OP-ESCALATION-001",
        task_definition_key=task_key,
        form_key="escalation",
        inputs=EscalationDecisionInputs(
            kind="escalation",
            resultado="devolvido_agente",
            notas_resolucao="Resumo pseudonimizado",
        ),
    )
    assert isinstance(decision.inputs, EscalationDecisionInputs)
    assert decision.inputs.resultado == "devolvido_agente"


def test_pagto_admissibility_is_separate_from_financial_approval() -> None:
    inputs = PagtoAdmissibilityInputs(
        kind="pagto_admissibilidade",
        decisao_admissibilidade="DEVOLVER",
        justificativa_recusa="Dados requerem revisao",
    )
    decision = _decision(
        process_definition_key="SP-OP-PAGTO-001",
        task_definition_key="UT_AnaliseAdmissibilidade",
        form_key="pagto_admissibilidade",
        inputs=inputs,
    )
    serialized = decision.model_dump(mode="python")["inputs"]
    assert serialized["decisao_admissibilidade"] == "DEVOLVER"
    assert not {"decisao_pagamento", "aprovador_id", "aprovador_tier"} & serialized.keys()


def test_pagto_read_only_evidence_and_centavos_are_exact_and_immutable() -> None:
    exact = "900719925474099312345678901234567890"
    evidence = PagtoAdmissibilityEvidence(
        kind="pagto_admissibilidade",
        valor_pagamento_cents=Centavos(exact),
        dados_pagamento_validos=True,
        lastro_confirmado=False,
        lastro_origem="contas_adjudicacao_humana",
        lastro_decisor_id="decisor-opaque-3",
        duplicidade_suspeita=True,
    )
    snapshot = _snapshot(
        process_definition_key="SP-OP-PAGTO-001",
        task_definition_key="UT_AnaliseAdmissibilidade",
        form_key="pagto_admissibilidade",
        form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        allowed_inputs=("decisao_admissibilidade", "justificativa_recusa"),
        read_only_evidence=evidence,
    )

    assert isinstance(snapshot.read_only_evidence, PagtoAdmissibilityEvidence)
    assert snapshot.read_only_evidence.valor_pagamento_cents.as_int() == int(exact)
    with pytest.raises(ValidationError):
        snapshot.read_only_evidence.duplicidade_suspeita = False


def test_json_boundary_preserves_exact_centavos_and_freezes_arrays_as_tuples() -> None:
    principal = HumanPrincipal.model_validate_json(json.dumps(_principal().model_dump(mode="json")))
    assert isinstance(principal.memberships, tuple)
    assert isinstance(principal.memberships[0].groups, tuple)

    exact = "-900719925474099312345678901234567890"
    centavos = Centavos.model_validate_json(json.dumps(exact))
    assert centavos.as_int() == int(exact)


@pytest.mark.parametrize("contract", [HumanPrincipal, TaskSnapshot, TaskDecision, HumanCommandReceipt])
def test_json_schemas_have_no_open_object(contract: type[object]) -> None:
    schema = contract.model_json_schema()  # type: ignore[attr-defined]

    def assert_closed(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for child in node.values():
                assert_closed(child)
        elif isinstance(node, list):
            for child in node:
                assert_closed(child)

    assert_closed(schema)


@pytest.mark.parametrize("bad", [1, True, 1.0, "01", "-0", "+1", "1.0", "1e3", " 1"])
def test_centavos_rejects_coercion_and_noncanonical_values(bad: object) -> None:
    with pytest.raises(ValidationError):
        Centavos.model_validate(bad)


def test_pagto_devolver_requires_justification() -> None:
    with pytest.raises(ValidationError, match="DEVOLVER"):
        PagtoAdmissibilityInputs(kind="pagto_admissibilidade", decisao_admissibilidade="DEVOLVER")


def _receipt(**updates: object) -> HumanCommandReceipt:
    values: dict[str, object] = {
        "schema_version": 1,
        "status": "pending",
        "operation": "decision",
        "command_id": "command-1",
        "tenant": "amh",
        "task_id": "task-1",
        "payload_digest": SHA256,
        "principal_ref": "principal-7",
        "workload_ref": "portal-bff-workload-1",
        "audit_intent_ref": "audit-intent-1",
        "recorded_at": NOW,
    }
    values.update(updates)
    return HumanCommandReceipt.model_validate(values)


def test_pending_receipt_does_not_claim_engine_commit() -> None:
    pending = _receipt()
    assert pending.status == "pending"
    assert pending.engine_receipt_ref is None
    assert pending.engine_commit_ref is None


def test_committed_receipt_requires_engine_and_audit_references() -> None:
    with pytest.raises(ValidationError, match="committed"):
        _receipt(status="committed")

    committed = _receipt(
        status="committed",
        consumed_task_revision=11,
        engine_receipt_ref="engine-receipt-1",
        engine_commit_ref="engine-commit-1",
        audit_result_ref="audit-result-1",
        engine_committed_at=NOW,
    )
    assert committed.status == "committed"


@pytest.mark.parametrize("status", ["conflict", "failure"])
def test_noncommitted_terminal_receipt_requires_code_but_forbids_commit_claims(status: str) -> None:
    terminal = _receipt(
        status=status,
        technical_code="REVISION_MISMATCH",
        audit_result_ref="audit-result-2",
    )
    assert terminal.engine_commit_ref is None

    with pytest.raises(ValidationError):
        _receipt(
            status=status,
            technical_code="REVISION_MISMATCH",
            audit_result_ref="audit-result-2",
            engine_commit_ref="fabricated-commit",
        )
