"""Finite D3/D5 classified-wire controls; no engine or custody qualification claimed."""

import copy
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.unit.portal.test_engine_profile import SEED, context

from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import ProfileError, canonicalize, seal, strict_loads


def payload():
    value = {
        "schema": "human-classified-decision.v1",
        "scope": {"tenant": "tenant-test", "environment": "test", "workload_ref": "gateway-test"},
        "principal_ref": "human-test",
        "principal_issuer": "https://issuer.example.test/",
        "principal_subject": "subject-test",
        "target": {
            "schema_version": "1",
            "command_id": "command-test",
            "task_id": "task-test",
            "process_definition_key": "SP-OP-AUTH-001",
            "process_definition_version": "1",
            "process_definition_id": "process-test:1:id",
            "process_definition_digest": "a" * 64,
            "task_definition_key": "UT_AnaliseMedicoAuditor",
            "form_key": "auth_decisao",
            "form_version": "1",
            "form_digest": "b" * 64,
            "expected_task_revision": "2",
            "expected_evidence_revision": "3",
            "expected_evidence_digest": "c" * 64,
            "expected_membership_revision": "1",
            "expected_authority_revision": "3",
        },
        "binding_digest": "d" * 64,
        "evidence_ref": "evidence-test",
        "request_digest": "e" * 64,
        "audit_intent_ref": "intent-test",
        "outcome": {"kind": "auth_decisao", "decisao_auditor": "NEGAR"},
        "human_basis": {"custody_ref": "custody-test", "content_digest": "f" * 64},
    }

    value["audit_intent_ref"] = hashlib.sha256(
        canonicalize([value["scope"], value["target"]["task_id"], value["target"]["command_id"]])
    ).hexdigest()
    return value


def decision():
    return HumanDecisionCommand(canonicalize(payload()))


def test_exact_wire_and_signature_bind_basis_without_narrative():
    value = decision()
    assert value.canonical == canonicalize(payload())
    assert value.digest == hashlib.sha256(value.canonical).hexdigest()
    key = Ed25519PrivateKey.from_private_bytes(SEED)
    raw = seal(value, context=context(), issued_at=100, expires_at=120, sign=key.sign)
    assert strict_loads(raw)["command"] == payload()
    assert value.operation == "decision" and value.task_revision == "2"
    assert value.assignee_ref == value.principal_ref


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(variables={"human_approved": True}),
        lambda p: p["human_basis"].update(justificativa_clinica="pointer"),
        lambda p: p["outcome"].update(justificativa_clinica="clinical narrative"),
        lambda p: p["outcome"].update(decisao_auditor="ACK"),
        lambda p: p["target"].update(expected_task_revision=2),
        lambda p: p["target"].update(expected_task_revision="02"),
        lambda p: p["target"].update(task_definition_key="UT_Other"),
        lambda p: p["target"].update(form_key="auth_junta"),
        lambda p: p["human_basis"].update(content_digest="none"),
        lambda p: p.update(principal_ref="gateway-test"),
    ],
)
def test_closed_shape_refuses_unknown_or_mismatched_fields(mutate):
    value = copy.deepcopy(payload())
    mutate(value)
    with pytest.raises(ProfileError):
        HumanDecisionCommand(canonicalize(value))


def test_restore_requires_canonical_immutable_bytes():
    with pytest.raises(ProfileError):
        HumanDecisionCommand(b" " + canonicalize(payload()))


@pytest.mark.parametrize(
    "process,task,kind,field,outcomes",
    [
        (
            "SP-OP-AUTH-001",
            "UT_AnaliseMedicoAuditor",
            "auth_decisao",
            "decisao_auditor",
            ("APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA"),
        ),
        (
            "SP-OP-AUTH-001",
            "UT_CoordenacaoAssume",
            "auth_decisao",
            "decisao_auditor",
            ("APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA"),
        ),
        ("SP-OP-AUTH-001", "UT_RegistrarParecerJunta", "auth_junta", "decisao_auditor", ("APROVAR", "NEGAR")),
        (
            "SP-OP-ESCALATION-001",
            "UT_TratarEscalonamento",
            "escalation",
            "resultado",
            ("resolvido_humano", "devolvido_agente", "emergencia_acionada"),
        ),
        (
            "SP-OP-ESCALATION-001",
            "UT_SupervisorAssume",
            "escalation",
            "resultado",
            ("resolvido_humano", "devolvido_agente", "emergencia_acionada"),
        ),
        (
            "SP-OP-PAGTO-001",
            "UT_AnaliseAdmissibilidade",
            "pagto_admissibilidade",
            "decisao_admissibilidade",
            ("PROSSEGUIR", "DEVOLVER"),
        ),
    ],
)
def test_each_contract_binding_keeps_each_typed_human_outcome(process, task, kind, field, outcomes):
    for outcome in outcomes:
        p = payload()
        p["target"].update(process_definition_key=process, task_definition_key=task, form_key=kind)
        p["outcome"] = {"kind": kind, field: outcome}
        c = HumanDecisionCommand(canonicalize(p))
        assert c.outcome == p["outcome"] and c.human_basis == p["human_basis"]
