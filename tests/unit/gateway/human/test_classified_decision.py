"""D3 trusted-port unit proofs only; no real PHI, database or engine is used."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import CSRF, SCOPE, SECRET, command, config, setup, snapshot
from tests.unit.portal.test_human_session import membership

from maezo.gateway.human.decision import (
    BoundDecisionPorts,
    DecisionAdmission,
    DecisionBindingSource,
    DecisionCustodyRecord,
    HumanDecisionCustody,
    PendingDecisionAdmission,
    QualifiedDecisionBinding,
    project_decision,
)
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.gateway import HumanGateway
from maezo.portal.contracts.models import PagtoAdmissibilityEvidence, TaskDecision

BINDING = "c" * 64
PRIVATE = "SYNTHETIC PRIVATE human basis — exact accents ã and whitespace  "


class Binding(DecisionBindingSource):
    scope = SCOPE

    def __init__(self):
        self.changes = {}
        self.fail = False
        self.calls = 0

    async def qualify(self, principal, task, authority):
        self.calls += 1
        if self.fail:
            raise RuntimeError(PRIVATE)
        values = dict(
            scope=SCOPE,
            principal=principal,
            task=task,
            authority=authority,
            binding_digest=BINDING,
            evidence_ref="evidence-real-1",
            valid_until=task.valid_until,
        )
        values.update(self.changes)
        return QualifiedDecisionBinding(**values)


class Custody(HumanDecisionCustody):
    """Unit fixture with actual idempotency/conflict; not production PHI persistence."""

    scope = SCOPE

    def __init__(self):
        self.requests = []
        self.records = {}
        self.changes = {}
        self.after = None
        self.fail = False

    async def preserve(self, request):
        self.requests.append(request)
        key = (request.scope.tenant, request.decision.task_id, request.decision.command_id)
        values = dict(
            scope=SCOPE,
            principal_ref=request.principal.principal_ref,
            task_id=request.decision.task_id,
            command_id=request.decision.command_id,
            request_digest=request.request_digest,
            custody_ref="custody-fixed-1",
            content_digest=request.content_digest,
            valid_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        values.update(self.changes)
        record = DecisionCustodyRecord(**values)
        if key in self.records and self.records[key].request_digest != record.request_digest:
            raise ValueError("conflict")
        self.records.setdefault(key, record)
        if self.after:
            self.after()
        if self.fail:
            raise RuntimeError(PRIVATE)
        return self.records[key]


class Admit(DecisionAdmission):
    """Unit transaction model; losing ack retains the same committed identity."""

    scope = SCOPE

    def __init__(self):
        self.calls = []
        self.rows = {}
        self.fail = False
        self.changes = {}

    async def admit(self, value, *, valid_until):
        self.calls.append(value)
        assert datetime.now(UTC) < valid_until
        key = (value.scope.tenant, value.target.task_id, value.target.command_id)
        if key in self.rows and self.rows[key].payload_digest != value.digest:
            raise ValueError("conflict")
        values = dict(
            schema_version=1,
            tenant=value.scope.tenant,
            task_id=value.target.task_id,
            command_id=value.target.command_id,
            principal_ref=value.principal_ref,
            workload_ref=value.scope.workload_ref,
            audit_intent_ref=value.audit_intent_ref,
            outbox_ref="outbox-1",
            transaction_ref="tx-1",
            committed_at=datetime.now(UTC),
            payload_digest=value.digest,
            request_digest=value.request_digest,
        )
        values.update(self.changes)
        self.rows.setdefault(key, PendingDecisionAdmission(**values))
        if self.fail:
            raise RuntimeError(PRIVATE)
        return self.rows[key]


async def configured(form="auth_decisao", outcome="NEGAR"):
    changes = dict(assignee_ref=membership().principal_ref)
    if form == "auth_junta":
        changes.update(task_definition_key="UT_RegistrarParecerJunta", form_key=form)
    if form == "escalation":
        changes.update(
            process_definition_key="SP-OP-ESCALATION-001",
            task_definition_key="UT_TratarEscalonamento",
            form_key=form,
            allowed_inputs=("resultado", "notas_resolucao"),
        )
        inputs = dict(kind=form, resultado=outcome, notas_resolucao=PRIVATE)
    elif form == "pagto_admissibilidade":
        changes.update(
            process_definition_key="SP-OP-PAGTO-001",
            task_definition_key="UT_AnaliseAdmissibilidade",
            form_key=form,
            form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            allowed_inputs=("decisao_admissibilidade", "justificativa_recusa"),
            read_only_evidence=PagtoAdmissibilityEvidence(
                kind=form,
                valor_pagamento_cents="100",
                dados_pagamento_validos=False,
                lastro_confirmado=False,
                duplicidade_suspeita=True,
            ),
        )
        inputs = dict(kind=form, decisao_admissibilidade=outcome, justificativa_recusa=PRIVATE)
    else:
        inputs = dict(
            kind=form,
            decisao_auditor=outcome,
            justificativa_clinica=PRIVATE,
            cid10_referencia="SYNTHETIC-X",
            fundamentacao_dut=PRIVATE,
        )
    old, store, transport, authority, assignment = await setup(snap=snapshot(**changes))
    binding, custody, admission = Binding(), Custody(), Admit()
    gateway = HumanGateway(
        resolver=old._resolver,
        scope=SCOPE,
        credentials=old._credentials,
        ports=old._ports,
        decision_ports=BoundDecisionPorts(binding, custody, admission),
    )
    values = command(transport.task.snapshot).model_dump(exclude={"operation", "expected_authority_revision"})
    decision = TaskDecision(**values, inputs=inputs)
    return gateway, decision, binding, custody, admission, store, transport, authority, assignment


async def submit(gateway, decision, **changes):
    values = dict(
        session_secret=SECRET,
        csrf_token=CSRF,
        origin=config().public_origin,
        decision=decision,
        expected_authority_revision=7,
        expected_binding_digest=BINDING,
    )
    values.update(changes)
    return await gateway.submit_decision(**values)


@pytest.mark.parametrize(
    "form,outcome",
    [
        ("auth_decisao", "APROVAR"),
        ("auth_decisao", "NEGAR"),
        ("auth_decisao", "SOLICITAR_INFO"),
        ("auth_decisao", "JUNTA_MEDICA"),
        ("auth_junta", "APROVAR"),
        ("auth_junta", "NEGAR"),
        ("escalation", "resolvido_humano"),
        ("escalation", "devolvido_agente"),
        ("escalation", "emergencia_acionada"),
        ("pagto_admissibilidade", "PROSSEGUIR"),
        ("pagto_admissibilidade", "DEVOLVER"),
    ],
)
async def test_qualified_human_form_preserved_and_only_typed_outcome_admitted(form, outcome):
    g, d, binding, custody, admission, _, _, _, assignments = await configured(form, outcome)
    original = d.model_dump()
    context = await g.read_decision_context(session_secret=SECRET, task_id=d.task_id)
    assert context.binding_digest == BINDING and context.expected_membership_revision == 1
    assert context.snapshot.form_source_status == (
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY" if form.startswith("pagto") else "BPMN_FORMDATA"
    )
    result = await submit(g, d)
    assert result.status == "pending" and not hasattr(result, "engine_receipt_ref")
    assert d.model_dump() == original == custody.requests[0].decision.model_dump()
    projected = admission.calls[0]
    assert PRIVATE.encode() not in projected.canonical
    assert "justificativa_clinica" not in projected.canonical.decode()
    assert projected.human_basis.content_digest == custody.requests[0].content_digest
    assert projected.digest == result.payload_digest and projected.request_digest == result.request_digest
    assert projected.outcome.model_dump()["kind"] == form
    assert projected.scope.workload_ref != projected.principal_ref
    assert not assignments.calls and binding.calls == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"csrf_token": "wrong"},
        {"origin": "https://other.test"},
        {"expected_authority_revision": 8},
        {"expected_authority_revision": True},
        {"expected_binding_digest": "d" * 64},
        {"expected_binding_digest": None},
    ],
)
async def test_rejects_forged_context_before_custody(changes):
    g, d, _, custody, admission, *_ = await configured()
    with pytest.raises(GatewayRefusalError):
        await submit(g, d, **changes)
    assert not custody.requests and not admission.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_digest", "d" * 64),
        ("content_digest", "d" * 64),
        ("principal_ref", "someone-else"),
        ("task_id", "other"),
        ("command_id", "other"),
        ("scope", SCOPE.model_copy(update={"tenant": "other"})),
        ("valid_until", datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
async def test_custody_substitution_or_expiry_never_admits(field, value):
    g, d, _, custody, admission, *_ = await configured()
    custody.changes[field] = value
    with pytest.raises(GatewayRefusalError):
        await submit(g, d)
    assert not admission.calls


@pytest.mark.parametrize(
    "kind", ["reassignment", "evidence", "authority", "revocation", "qualification", "expiry"]
)
async def test_current_authority_refreshed_after_custody_io(kind):
    g, d, binding, custody, admission, store, transport, authority, _ = await configured()

    def mutate():
        if kind == "reassignment":
            transport.task = transport.task.model_copy(
                update={"snapshot": transport.task.snapshot.model_copy(update={"assignee_ref": "other"})}
            )
        elif kind == "evidence":
            transport.task = transport.task.model_copy(
                update={"snapshot": transport.task.snapshot.model_copy(update={"evidence_revision": 9})}
            )
        elif kind == "authority":
            authority.authority = authority.authority.model_copy(update={"permitted_operations": ()})
        elif kind == "revocation":
            m = membership()
            store.memberships[(m.issuer, m.subject)] = m.model_copy(update={"revoked": True})
        elif kind == "qualification":
            binding.changes["binding_digest"] = "f" * 64
        elif kind == "expiry":
            transport.task = transport.task.model_copy(
                update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)}
            )

    custody.after = mutate
    with pytest.raises(GatewayRefusalError):
        await submit(g, d)
    assert not admission.calls


@pytest.mark.parametrize("phase", ["qualification", "custody", "admission"])
async def test_dependency_error_is_static_and_uncertain_admission_is_recoverable(phase):
    g, d, binding, custody, admission, *_ = await configured()
    port = {"qualification": binding, "custody": custody, "admission": admission}[phase]
    port.fail = True
    with pytest.raises(GatewayRefusalError) as caught:
        await submit(g, d)
    assert PRIVATE not in str(caught.value) and caught.value.__cause__ is None
    assert len(admission.rows) == (1 if phase == "admission" else 0)
    port.fail = False
    result = await submit(g, d)
    assert result.status == "pending" and len(admission.rows) == 1
    if phase == "admission":
        assert admission.calls[0].canonical == admission.calls[1].canonical


async def test_changed_raw_basis_same_command_is_conflict_not_silent_retry():
    g, d, _, custody, admission, *_ = await configured()
    first = await submit(g, d)
    changed = TaskDecision(
        **{**d.model_dump(), "inputs": {**d.inputs.model_dump(), "fundamentacao_dut": PRIVATE + " changed"}}
    )
    with pytest.raises(GatewayRefusalError):
        await submit(g, changed)
    assert len(admission.calls) == 1 and len(custody.records) == 1
    assert (await submit(g, d)).payload_digest == first.payload_digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("payload_digest", "d" * 64),
        ("request_digest", "d" * 64),
        ("audit_intent_ref", "other"),
        ("command_id", "other"),
        ("task_id", "other"),
        ("principal_ref", "other"),
        ("tenant", "other"),
        ("workload_ref", "other"),
        ("committed_at", datetime.now(UTC) + timedelta(hours=1)),
    ],
)
async def test_mismatched_ack_is_unknown_not_execution_or_rollback(field, value):
    g, d, _, _, admission, *_ = await configured()
    admission.changes[field] = value
    with pytest.raises(GatewayRefusalError, match="admission_unavailable"):
        await submit(g, d)
    assert len(admission.rows) == 1


async def test_projection_never_accepts_forged_missing_required_negative_basis():
    g, d, _, custody, admission, *_ = await configured()
    broken = d.model_copy(update={"inputs": d.inputs.model_copy(update={"justificativa_clinica": None})})
    with pytest.raises(GatewayRefusalError):
        await submit(g, broken)
    assert not custody.requests and not admission.calls


async def test_canonical_large_revisions_are_exact_and_stable():
    g, d, _, custody, admission, *_ = await configured()
    await submit(g, d)
    original = custody.requests[0]
    huge = 2**100
    principal = original.principal.model_copy(update={"membership_revision": huge})
    request = original.model_copy(
        update={
            "principal": principal,
            "decision": d.model_copy(update={"expected_membership_revision": huge}),
        }
    )
    record = next(iter(custody.records.values())).model_copy(
        update={"request_digest": request.request_digest}
    )
    value = project_decision(request, record)
    assert str(huge).encode() in value.canonical
    assert (b'"expected_membership_revision":"' + str(huge).encode() + b'"') in value.canonical
    assert value.digest != admission.calls[0].digest


def test_python_gateway_exposes_typed_decision_context():
    assert callable(getattr(HumanGateway, "read_decision_context", None))


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", SCOPE.model_copy(update={"tenant": "other"})),
        ("principal", membership),
        ("evidence_ref", "${unresolved}"),
        ("valid_until", datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
async def test_unqualified_or_rebound_source_rejects_before_phi_custody(field, value):
    g, d, binding, custody, admission, *_ = await configured()
    binding.changes[field] = value
    with pytest.raises(GatewayRefusalError, match="form_projection_unavailable"):
        await submit(g, d)
    assert not custody.requests and not admission.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_revision", 88),
        ("evidence_digest", "f" * 64),
        ("assignee_ref", "other"),
    ],
)
async def test_context_cannot_be_obtained_for_inconsistent_or_unowned_task(field, value):
    g, _, _, custody, admission, _, transport, _, _ = await configured()
    transport.task = transport.task.model_copy(
        update={"snapshot": transport.task.snapshot.model_copy(update={field: value})}
    )
    with pytest.raises(GatewayRefusalError):
        await g.read_decision_context(session_secret=SECRET, task_id=transport.task.snapshot.task_id)
    assert not custody.requests and not admission.calls


async def test_classified_model_rejects_wrong_outcome_binding_and_forged_intent():
    from pydantic import ValidationError

    from maezo.gateway.human.decision import ClassifiedDecision

    g, d, _, _, admission, *_ = await configured()
    await submit(g, d)
    valid = admission.calls[0].model_dump(by_alias=True)
    for changes in (
        {"outcome": {"kind": "escalation", "resultado": "resolvido_humano"}},
        {"audit_intent_ref": "made-up"},
        {"principal_ref": SCOPE.workload_ref},
        {"clinical_basis": PRIVATE},
    ):
        with pytest.raises(ValidationError):
            ClassifiedDecision.model_validate({**valid, **changes})


async def test_all_supplied_phi_fields_remain_exact_in_custody_canonical():
    g, d, _, custody, admission, *_ = await configured()
    await submit(g, d)
    original = custody.requests[0]
    assert PRIVATE.encode() in original.canonical
    assert original.decision.inputs.justificativa_clinica == PRIVATE
    assert original.decision.inputs.fundamentacao_dut == PRIVATE
    assert b"SYNTHETIC-X" in original.canonical
    assert b"SYNTHETIC-X" not in admission.calls[0].canonical


@pytest.mark.parametrize(
    "form,outcome,task_key",
    [
        ("auth_decisao", "NEGAR", "UT_CoordenacaoAssume"),
        ("escalation", "devolvido_agente", "UT_SupervisorAssume"),
    ],
)
async def test_coordination_takeovers_preserve_exact_task_binding(form, outcome, task_key):
    g, decision, _, _, admission, _, transport, authority, _ = await configured(form, outcome)
    snap = transport.task.snapshot.model_copy(update={"task_definition_key": task_key})
    transport.task = transport.task.model_copy(update={"snapshot": snap})
    authority.authority = authority.authority.model_copy(update={"task_definition_key": task_key})
    decision = TaskDecision.model_validate(decision.model_copy(update={"task_definition_key": task_key}))
    result = await submit(g, decision)
    assert result.status == "pending"
    assert admission.calls[0].target.task_definition_key == task_key


async def test_retry_after_new_observation_keeps_same_immutable_digest():
    g, d, _, _, admission, _, transport, authority, _ = await configured()
    first = await submit(g, d)
    transport.task = transport.task.model_copy(
        update={
            "snapshot": transport.task.snapshot.model_copy(update={"snapshot_at": datetime.now(UTC)}),
            "valid_until": transport.task.valid_until + timedelta(minutes=1),
        }
    )
    authority.authority = authority.authority.model_copy(
        update={"valid_until": authority.authority.valid_until + timedelta(minutes=1)}
    )
    second = await submit(g, d)
    assert first == second
    assert admission.calls[0].canonical == admission.calls[1].canonical
