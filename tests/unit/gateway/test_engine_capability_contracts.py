"""Offline D7-A contract tests. Recording sources do not simulate engine authorization."""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from maezo.gateway.audit import hash_input
from maezo.gateway.engine_contracts import (
    AttestedField,
    EngineAuthority,
    EngineCapabilityError,
    EngineCapabilityProfile,
    EngineIdentity,
    EngineOperation,
    EngineOperationAuthorizer,
    EngineRefusalCode,
    EngineRequest,
    EngineTarget,
    FieldOrigin,
    ValueKind,
    VariableField,
    canonical_json,
    parse_json,
)
from maezo.gateway.engine_schemas import (
    CONSENT_REVOKED,
    CONTAS_IMPACT_COMPLETE,
    CONTAS_PAGTO_START,
    ESCALATION_NOTIFY_ERROR,
    HELENA_START,
    RAFAEL_START,
    SCHEMAS,
    schema_by_id,
    start_read_schema,
    worker_lifecycle_schema,
)
from maezo.gateway.engine_start import ProfileStartAuthorizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.tool_registry import bind_cibseven_start_preflight
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenStartAuthorizationError,
    FakeCibSevenTransport,
    HistoryQueryingTransport,
    StartAuthorizingTransport,
    StartOutcome,
    _to_camunda_vars,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink

VERSION = "synthetic-v1"
RESOURCE = "synthetic-resource"


def profile(schema=HELENA_START):
    return EngineCapabilityProfile(
        EngineIdentity(
            "synthetic", "isolated-test", schema.workload, VERSION, "synthetic-issuer", "synthetic-subject"
        ),
        EngineTarget(schema.process_key, 7, "synthetic-definition-7", schema.topic, schema.message),
        schema,
        worker_id="synthetic-worker" if schema.topic else "",
        source_target=EngineTarget(
            schema.source_process_key, 3, "synthetic-source-definition", schema.source_topic
        )
        if schema.source_process_key
        else None,
    )


def variables(schema=HELENA_START):
    values: dict[str, Any] = {}
    for rule in schema.fields:
        value = {
            ValueKind.STRING: "synthetic",
            ValueKind.BOOLEAN: False,
            ValueKind.INTEGER: 37,
            ValueKind.DOUBLE: 37.25,
            ValueKind.JSON: [],
        }[rule.kind]
        if rule.name.startswith("dossie_") or rule.name == "dmn_decision_refs":
            value = dict.fromkeys(rule.null_members)
        if rule.origin is FieldOrigin.FIXED_NULL:
            value = None
        if rule.fixed_json is not None:
            value = json.loads(rule.fixed_json)
        values[rule.name] = value
    values.update(
        {
            k: v
            for k, v in {
                "tenant_id": "synthetic",
                "source_agent_id": schema.workload,
                "source_agent_version": VERSION,
            }.items()
            if k in values
        }
    )
    return values


def request(p, data=None, **overrides):
    defaults = dict(
        operation=p.schema.operation,
        process_key=p.target.process_key,
        resource_ref=RESOURCE,
        variables_json=canonical_json(variables(p.schema) if data is None else data),
        topic=p.target.topic,
        message=p.target.message,
        worker_id=p.worker_id,
    )
    defaults.update(overrides)
    return EngineRequest(**defaults)


class AuthoritySource:
    """Gateway/engine authority port recorder with fixed expected identity and resource."""

    def __init__(self, profiles, *, data=None):
        self.profiles = profiles
        self.data = data
        self.error = False
        self.overrides = {}
        self.calls = []

    async def resolve(self, req):
        self.calls.append(req)
        if self.error:
            raise RuntimeError("SYNTHETIC_PRIVATE_PROVIDER_ERROR")
        p = next(p for p in self.profiles if p.schema.operation is req.operation)
        facts = variables(p.schema) if self.data is None else self.data
        fields = tuple(
            AttestedField(f.name, f.origin, canonical_json(facts[f.name]), "synthetic-source-evidence")
            for f in p.schema.fields
            if f.origin in (FieldOrigin.PRIOR_HUMAN, FieldOrigin.ENGINE)
        )
        if p.schema.correlation_fields:
            fields += (
                AttestedField(
                    "beneficiario_pseudo_id",
                    FieldOrigin.ENGINE,
                    canonical_json("synthetic-person"),
                    "synthetic-dsr-source",
                ),
            )
        result = EngineAuthority(
            p.identity,
            p.target,
            RESOURCE,
            p.digest,
            datetime.now(UTC) + timedelta(minutes=1),
            fields=fields,
            source_target=EngineTarget(
                p.schema.source_process_key, 3, "synthetic-source-definition", p.schema.source_topic
            )
            if p.schema.source_process_key
            else None,
            source_task_ref="synthetic-source-task",
            lock_owner=p.worker_id,
            lock_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            prior_human_task_ref="synthetic-prior-human-task",
            prior_human_completed=True,
        )
        return replace(result, **self.overrides)


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.schema_id)
def test_every_schema_preserves_typed_wire_semantics(schema):
    data = variables(schema)
    schema.validate(data)
    assert schema.serialize_variables(data) == _to_camunda_vars(data)
    assert parse_json(canonical_json(data)) == data


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.schema_id)
@pytest.mark.parametrize("field", ["decisao_auditor", "auditor_id", "localVariables", "startInstructions"])
def test_every_row_refuses_unknown_or_privileged_root_fields(schema, field):
    with pytest.raises(EngineCapabilityError) as caught:
        schema.validate({**variables(schema), field: "synthetic-forgery"})
    assert caught.value.code is EngineRefusalCode.FIELD_DENIED


@pytest.mark.parametrize(
    "body",
    [
        b'{"a":1,"a":2}',
        b'{"a":{"b":1,"b":2}}',
        b'{"a":NaN}',
        b'{"a":1e999}',
        b"[]",
        b"{} trailing",
        b"\xff",
        rb'{"a":"\ud800"}',
    ],
)
def test_strict_json_rejects_ambiguity_and_nonfinite_values(body):
    with pytest.raises(EngineCapabilityError) as caught:
        parse_json(body)
    assert str(caught.value) == "engine_invalid_body"


@pytest.mark.parametrize("bad", [True, 1.0, "1", 2**63, -(2**63) - 1, {"value": 1, "type": "Integer"}])
def test_centavos_never_coerced(bad):
    with pytest.raises(EngineCapabilityError):
        CONTAS_PAGTO_START.validate({**variables(CONTAS_PAGTO_START), "valor_pagamento_cents": bad})


def test_declared_long_does_not_depend_on_magnitude():
    small = variables(CONTAS_IMPACT_COMPLETE)
    small["total_glosado_candidato_centavos"] = 1
    large = {**small, "total_glosado_candidato_centavos": 2**40}
    assert CONTAS_IMPACT_COMPLETE.serialize_variables(small)["total_glosado_candidato_centavos"] == {
        "value": 1,
        "type": "Long",
    }
    assert CONTAS_IMPACT_COMPLETE.serialize_variables(large)["total_glosado_candidato_centavos"] == {
        "value": 2**40,
        "type": "Long",
    }


@pytest.mark.parametrize(
    "schema", [s for s in SCHEMAS if any(f.origin is FieldOrigin.FIXED_NULL for f in s.fields)]
)
def test_explicit_null_is_initialization_only(schema):
    original = variables(schema)
    for rule in schema.fields:
        if rule.origin is FieldOrigin.FIXED_NULL:
            for value in (False, "NEGAR", 0, {}, ""):
                with pytest.raises(EngineCapabilityError):
                    schema.validate({**original, rule.name: value})


@pytest.mark.parametrize("schema", [s for s in SCHEMAS if any(f.null_members for f in s.fields)])
def test_dossier_null_guards_and_typed_envelope_escape(schema):
    for rule in schema.fields:
        if rule.null_members:
            with pytest.raises(EngineCapabilityError):
                schema.validate({**variables(schema), rule.name: {rule.null_members[0]: "forged"}})
            with pytest.raises(EngineCapabilityError):
                schema.validate({**variables(schema), rule.name: {"value": "forged", "type": "Object"}})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity_field", ["tenant", "environment", "workload", "workload_version", "issuer", "subject"]
)
async def test_authenticated_identity_must_match_entire_deployment_profile(identity_field):
    p = profile()
    source = AuthoritySource((p,))
    source.overrides["identity"] = replace(p.identity, **{identity_field: "different"})
    with pytest.raises(EngineCapabilityError) as caught:
        await EngineOperationAuthorizer((p,), source).authorize(request(p))
    assert caught.value.code is EngineRefusalCode.IDENTITY_MISMATCH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"process_version": 8},
        {"definition_id": "other-definition"},
        {"process_key": "SP-OP-PAGTO-001"},
        {"topic": "unrelated"},
    ],
)
async def test_resolved_definition_scope_is_exact(changes):
    p = profile()
    source = AuthoritySource((p,))
    source.overrides["target"] = replace(p.target, **changes)
    with pytest.raises(EngineCapabilityError) as caught:
        await EngineOperationAuthorizer((p,), source).authorize(request(p))
    assert caught.value.code is EngineRefusalCode.RESOURCE_MISMATCH


@pytest.mark.asyncio
async def test_prior_payment_actor_requires_bound_source_evidence():
    p = profile(CONTAS_PAGTO_START)
    source = AuthoritySource((p,))
    authorizer = EngineOperationAuthorizer((p,), source)
    allowed = await authorizer.authorize(request(p))
    assert allowed.target == p.target
    for changes in ({"fields": ()}, {"source_target": None}, {"source_task_ref": ""}):
        source.overrides = changes
        with pytest.raises(EngineCapabilityError) as caught:
            await authorizer.authorize(request(p))
        assert caught.value.code is EngineRefusalCode.EVIDENCE_UNAVAILABLE
    source.overrides = {}
    with pytest.raises(EngineCapabilityError):
        await authorizer.authorize(request(p, {**variables(p.schema), "lastro_decisor_id": "other-human"}))
    for privileged in (
        "lastro_confirmado",
        "decisao_pagamento",
        "aprovador_id",
        "grupo_aprovador",
        "faixa_valor",
    ):
        with pytest.raises(EngineCapabilityError):
            await authorizer.authorize(request(p, {**variables(p.schema), privileged: "forged"}))


@pytest.mark.asyncio
async def test_scoped_consent_fanout_and_bound_correlation():
    p = profile(CONSENT_REVOKED)
    source = AuthoritySource((p,))
    source.overrides["resource_ref"] = ""
    authorizer = EngineOperationAuthorizer((p,), source)
    data = variables(p.schema)
    args = dict(
        resource_ref="",
        all_matching=True,
        correlation_json=canonical_json(
            {"tenant_id": "synthetic", "beneficiario_pseudo_id": "synthetic-person"}
        ),
    )
    result = await authorizer.authorize(request(p, data, **args))
    assert result.request.all_matching is True
    for keys in (
        {},
        {"tenant_id": "other", "beneficiario_pseudo_id": "synthetic-person"},
        {"tenant_id": "synthetic", "beneficiario_pseudo_id": "synthetic-person", "auditor_id": "forged"},
    ):
        with pytest.raises(EngineCapabilityError):
            await authorizer.authorize(request(p, data, **{**args, "correlation_json": canonical_json(keys)}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"lock_owner": "other-worker"},
        {"lock_expires_at": None},
        {"lock_expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
)
async def test_external_output_requires_current_owned_lock(changes):
    p = profile(CONTAS_IMPACT_COMPLETE)
    source = AuthoritySource((p,))
    authorizer = EngineOperationAuthorizer((p,), source)
    assert (await authorizer.authorize(request(p))).target.topic == p.target.topic
    source.overrides = changes
    with pytest.raises(EngineCapabilityError) as caught:
        await authorizer.authorize(request(p))
    assert caught.value.code is EngineRefusalCode.RESOURCE_MISMATCH


@pytest.mark.asyncio
async def test_unavailable_auth_does_not_become_anonymous_or_missing():
    p = profile()
    source = AuthoritySource((p,))
    source.error = True
    for profiles, authority, expected in (
        ((), source, EngineRefusalCode.PROFILE_UNAVAILABLE),
        ((p,), None, EngineRefusalCode.IDENTITY_UNAVAILABLE),
        ((p,), source, EngineRefusalCode.IDENTITY_UNAVAILABLE),
    ):
        with pytest.raises(EngineCapabilityError) as caught:
            await EngineOperationAuthorizer(profiles, authority).authorize(request(p))
        assert caught.value.code is expected
        assert "SYNTHETIC_PRIVATE" not in str(caught.value)


def test_profile_is_immutable_and_policy_bytes_are_deterministic():
    p = profile()
    assert p.digest == profile().digest
    assert p.digest != replace(p, target=replace(p.target, process_version=8)).digest
    for obj, name, value in (
        (p, "identity", None),
        (p.identity, "tenant", "other"),
        (p.schema, "fields", ()),
        (p.schema.fields[0], "origin", FieldOrigin.FACT),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [HELENA_START, CONTAS_PAGTO_START], ids=lambda s: s.schema_id)
@pytest.mark.parametrize(
    "failure", ["absent-authorizer", "empty-profile", "identity", "read-denied", "audit-actor"]
)
async def test_preclaim_denial_and_recovery_same_key(schema, failure):
    start = profile(schema)
    profiles = [start, replace(start, schema=start_read_schema(schema, EngineOperation.READ_ACTIVE))]
    if schema is CONTAS_PAGTO_START:
        profiles.append(replace(start, schema=start_read_schema(schema, EngineOperation.READ_HISTORY)))
    profiles = tuple(profiles)
    source = AuthoritySource(profiles)
    operations = EngineOperationAuthorizer(profiles, source)
    authorizer = ProfileStartAuthorizer(operations)
    denied = authorizer
    if failure == "absent-authorizer":
        denied = None
    elif failure == "empty-profile":
        denied = ProfileStartAuthorizer(EngineOperationAuthorizer((), source))
    elif failure == "identity":
        source.error = True
    elif failure == "read-denied":
        denied = ProfileStartAuthorizer(EngineOperationAuthorizer((start,), source))
    inner = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    seam = SeamContext(tenant="synthetic", principal=schema.workload)
    transport = bind_cibseven_start_preflight(inner=inner, seam=seam, authorizer=denied)
    assert isinstance(transport, StartAuthorizingTransport)
    assert isinstance(transport, HistoryQueryingTransport)
    data = variables(schema)
    provenance = AgentDecisionProvenance(schema.audit_actor or schema.workload, VERSION, "synthetic", {})
    rejected = replace(provenance, agent_id="forged") if failure == "audit-actor" else provenance
    with pytest.raises(CibSevenStartAuthorizationError):
        await start_process_idempotent(
            transport,
            process_key=schema.process_key,
            business_key=RESOURCE,
            variables=data,
            audit_sink=sink,
            provenance=rejected,
        )
    assert sink.calls == []
    assert sink._claimed == {}
    assert inner._history == {}
    assert inner._variables == {}
    source.error = False
    recovered = bind_cibseven_start_preflight(inner=inner, seam=seam, authorizer=authorizer)
    first = await start_process_idempotent(
        recovered,
        process_key=schema.process_key,
        business_key=RESOURCE,
        variables=data,
        audit_sink=sink,
        provenance=provenance,
    )
    again = await start_process_idempotent(
        recovered,
        process_key=schema.process_key,
        business_key=RESOURCE,
        variables=data,
        audit_sink=sink,
        provenance=provenance,
    )
    assert first.start_outcome is StartOutcome.STARTED
    assert again.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert first.instance_id == again.instance_id
    assert len(sink._claimed) == 1
    assert len(inner._history[RESOURCE]) == 1
    assert inner._variables[RESOURCE] == data


@pytest.mark.parametrize(
    "agent,family,flow",
    [
        ("rafael", "auth", ""),
        ("lucas", "escalation", ""),
        ("carolina", "cred", ""),
        ("fernando", "inadimplencia", ""),
        ("valentina", "programa", ""),
        ("gustavo", "nip", "nip"),
        ("gustavo", "ans-submit", "ans_submit"),
        ("marina", "contas", "contas"),
        ("marina", "recurso", "recurso"),
    ],
)
def test_actual_agent_builders_and_dynamic_fields_preserved(agent, family, flow):
    schema = schema_by_id(f"{agent}.{family}.start.v1")
    module = importlib.import_module(f"maezo.agents.{agent}.graph")
    cls = getattr(module, f"{agent.capitalize()}Graph")
    graph = object.__new__(cls)
    graph._agent_version = VERSION
    state = variables(schema)
    state.update(
        dossier={"narrativa": "synthetic narrative"},
        route="human_review",
        flow=flow,
        fluxo=flow,
        dmn_refs={"synthetic-table": "synthetic-ref"},
        motivo_humano="outro",
        grupo_humano="synthetic-group",
    )
    method = {"lucas": "_escalation_variables", "fernando": "_inadimplencia_variables"}.get(
        agent, "_contract_variables"
    )
    output = getattr(graph, method)(state)
    schema.validate(output)
    assert schema.serialize_variables(output) == _to_camunda_vars(output)
    assert output["source_agent_id"] == agent
    assert output["source_agent_version"] == VERSION
    assert output["dossie_" + agent] == state["dossier"]
    assert "dmn_decision_refs" in output


def test_schema_sources_exist_and_shipped_agent_start_capability_is_declared():
    import yaml

    for schema in SCHEMAS:
        for source in schema.sources:
            assert Path(source.split("#")[0].split(":")[0]).is_file(), source
        agent_path = Path("spec/agents") / schema.workload / "agent.yaml"
        if schema.operation is EngineOperation.START and agent_path.exists():
            declared = yaml.safe_load(agent_path.read_text())
            permitted = declared.get("process_keys", [declared["escalation"]["process"]])
            assert schema.process_key in permitted
            assert "mcp-cibseven.start_process" in declared["tools"]


def test_conflicted_and_unimplemented_callers_have_no_accidental_grant():
    for row in ("andre.pagto.start.v1", "marina.reembolso.start.v1", "_template.start.v1", "admin.start.v1"):
        with pytest.raises(EngineCapabilityError) as caught:
            schema_by_id(row)
        assert caught.value.code is EngineRefusalCode.PROFILE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,params",
    [
        (
            EngineOperation.FETCH_LOCK,
            {
                "maxTasks": 1,
                "lockDuration": 30000,
                "asyncResponseTimeout": 1000,
                "variables": ["denial_ratio", "glosa_count"],
            },
        ),
        (EngineOperation.FAILURE, {"retries": 0, "retryTimeout": 5000, "errorCategory": "worker_failure"}),
        (EngineOperation.EXTEND_LOCK, {"newDuration": 30000}),
        (EngineOperation.UNLOCK, {}),
    ],
)
async def test_worker_lifecycle_is_closed_and_preserves_integer_parameters(operation, params):
    schema = worker_lifecycle_schema(CONTAS_IMPACT_COMPLETE, operation)
    p = profile(schema)
    source = AuthoritySource((p,))
    authorizer = EngineOperationAuthorizer((p,), source)
    req = request(p, {}, parameters_json=canonical_json(params))
    result = await authorizer.authorize(req)
    assert parse_json(result.request.parameters_json) == params
    for mutation in (
        {"parameters_json": canonical_json({**params, "localVariables": {"auditor_id": "forged"}})},
        {"worker_id": "other-worker"},
        {"topic": "operadora.auth.send_denial_notice"},
        {"variables_json": canonical_json({"auditor_id": "forged"})},
    ):
        with pytest.raises(EngineCapabilityError):
            await authorizer.authorize(replace(req, **mutation))


@pytest.mark.asyncio
async def test_fetch_projection_cannot_read_arbitrary_patient_variables():
    p = profile(worker_lifecycle_schema(CONTAS_IMPACT_COMPLETE, EngineOperation.FETCH_LOCK))
    authorizer = EngineOperationAuthorizer((p,), AuthoritySource((p,)))
    for names in (["auditor_id"], ["denial_ratio", "denial_ratio"], [False], ["patient_full_record"]):
        with pytest.raises(EngineCapabilityError) as caught:
            await authorizer.authorize(
                request(
                    p,
                    {},
                    parameters_json=canonical_json(
                        {"maxTasks": 1, "lockDuration": 30, "asyncResponseTimeout": 10, "variables": names}
                    ),
                )
            )
        assert caught.value.code is EngineRefusalCode.FIELD_DENIED


@pytest.mark.asyncio
async def test_only_modeled_bpmn_error_can_leave_the_worker_topic():
    p = profile(ESCALATION_NOTIFY_ERROR)
    authorizer = EngineOperationAuthorizer((p,), AuthoritySource((p,)))
    req = request(p, {}, error_code="ERR_ESC_NOTIFY_FAILED")
    assert (await authorizer.authorize(req)).request.error_code == "ERR_ESC_NOTIFY_FAILED"
    for code in ("", "ERR_AUTH_DENIAL_NOT_HUMAN", "ERR_UNKNOWN", "ERR_ESC_NOTIFY_FAILED "):
        with pytest.raises(EngineCapabilityError) as caught:
            await authorizer.authorize(replace(req, error_code=code))
        assert caught.value.code is EngineRefusalCode.OPERATION_DENIED


@pytest.mark.asyncio
async def test_previous_human_must_be_completed_and_source_version_pinned():
    p = profile(CONTAS_PAGTO_START)
    source = AuthoritySource((p,))
    authorizer = EngineOperationAuthorizer((p,), source)
    for mutation in (
        {"prior_human_task_ref": ""},
        {"prior_human_completed": False},
        {"source_target": replace(p.source_target, process_version=4)},
    ):
        source.overrides = mutation
        with pytest.raises(EngineCapabilityError) as caught:
            await authorizer.authorize(request(p))
        assert caught.value.code is EngineRefusalCode.EVIDENCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_automatic_contas_handoff_does_not_invent_prior_human():
    p = profile(CONTAS_PAGTO_START)
    data = {**variables(p.schema), "lastro_origem": "contas_adjudicacao_automatica", "lastro_decisor_id": ""}
    source = AuthoritySource((p,), data=data)
    source.overrides = {"prior_human_task_ref": "", "prior_human_completed": False}
    result = await EngineOperationAuthorizer((p,), source).authorize(request(p, data))
    assert parse_json(result.request.variables_json)["lastro_decisor_id"] == ""
    data["lastro_origem"] = "contas_adjudicacao_humana"
    with pytest.raises(EngineCapabilityError):
        await EngineOperationAuthorizer((p,), source).authorize(request(p, data))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"valid_until": datetime(2020, 1, 1, tzinfo=UTC)},
        {"valid_until": datetime(2099, 1, 1)},
        {"valid_until": "2099"},
        {"policy_digest": "0" * 64},
        {"resource_ref": "another-case"},
    ],
)
async def test_expired_malformed_or_wrong_resource_authority_refuses(change):
    p = profile()
    source = AuthoritySource((p,))
    source.overrides = change
    with pytest.raises(EngineCapabilityError):
        await EngineOperationAuthorizer((p,), source).authorize(request(p))


def test_configuration_cannot_extend_a_reviewed_schema_or_install_unmapped_operations():
    p = profile()
    for schema in (
        replace(p.schema, fields=()),
        replace(p.schema, workload="other-agent"),
        replace(p.schema, operation=EngineOperation.DMN_EVALUATE),
        replace(p.schema, operation=EngineOperation.DMN_METADATA),
    ):
        with pytest.raises(EngineCapabilityError) as caught:
            replace(p, schema=schema)
        assert caught.value.code is EngineRefusalCode.PROFILE_UNAVAILABLE
    with pytest.raises(FrozenInstanceError):
        EngineOperationAuthorizer((p,), None)._profiles = ()


@pytest.mark.asyncio
async def test_bad_fields_refuse_at_real_chokepoint_before_audit_or_start():
    p = profile()
    profiles = (p, replace(p, schema=start_read_schema(p.schema, EngineOperation.READ_ACTIVE)))
    source = AuthoritySource(profiles)
    inner, sink = FakeCibSevenTransport(), FakeStartAuditSink()
    transport = bind_cibseven_start_preflight(
        inner=inner,
        seam=SeamContext("synthetic", "helena"),
        authorizer=ProfileStartAuthorizer(EngineOperationAuthorizer(profiles, source)),
    )
    with pytest.raises(CibSevenStartAuthorizationError) as caught:
        await start_process_idempotent(
            transport,
            process_key=p.target.process_key,
            business_key=RESOURCE,
            variables={**variables(), "auditor_id": "forged"},
            audit_sink=sink,
            provenance=AgentDecisionProvenance("helena", VERSION, "synthetic", {}),
        )
    assert caught.value.code is EngineRefusalCode.FIELD_DENIED
    assert source.calls == []
    assert sink.calls == [] and sink._claimed == {}
    assert inner._history == {} and inner._variables == {}


def test_real_worker_impact_result_retains_long_cents_and_output_shape():
    from maezo.tools.workers.contas import GlosaIdentified, calculate_impact

    facts = GlosaIdentified(
        total_glosado_candidato_centavos=2**40, glosa_count=3, denial_ratio=0.2, divergencia_valor=True
    )
    output = calculate_impact(facts, 25000.25)
    wire = CONTAS_IMPACT_COMPLETE.serialize_variables(output)
    assert wire == _to_camunda_vars(output)
    assert wire["total_glosado_candidato_centavos"] == {"type": "Long", "value": 2**40}
    assert wire["valor_apresentado_brl"] == {"type": "Double", "value": 25000.25}


def test_preflight_does_not_fabricate_history_capability():
    wrapped = bind_cibseven_start_preflight(
        inner=object(), seam=SeamContext("synthetic", "helena"), authorizer=None
    )
    assert isinstance(wrapped, StartAuthorizingTransport)
    assert not isinstance(wrapped, HistoryQueryingTransport)


def test_legacy_wrapper_does_not_claim_a_d7_preflight():
    from maezo.gateway.seams.cibseven import gate_cibseven

    legacy = gate_cibseven(FakeCibSevenTransport(), SeamContext("synthetic", "helena"))
    assert isinstance(legacy, HistoryQueryingTransport)
    assert not isinstance(legacy, StartAuthorizingTransport)


# Names are taken from the five canonical contract tables, independently of schema.null_members.
_CONTRACT_DOSSIER_NULLS = (
    ("carolina.cred.start.v1", "dossie_carolina", "decisao_cred"),
    ("gustavo.nip.start.v1", "dossie_gustavo", "decisao_nip"),
    ("gustavo.ans-submit.start.v1", "dossie_gustavo", "decisao_envio"),
    ("marina.contas.start.v1", "dossie_marina", "decisao_contas"),
    ("valentina.programa.start.v1", "dossie_valentina", "decisao_programa"),
    ("valentina.programa.start.v1", "dossie_valentina", "motivo_desligamento_clinico"),
    ("valentina.programa.start.v1", "dossie_valentina", "referencia_clinica"),
    ("valentina.programa.start.v1", "dossie_valentina", "responsavel_clinico_id"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("sid,dossier,field", _CONTRACT_DOSSIER_NULLS)
async def test_repair_canonical_dossier_refuses_before_authority_and_recovers(sid, dossier, field):
    schema = schema_by_id(sid)
    p = profile(schema)
    profiles = (p, replace(p, schema=start_read_schema(schema, EngineOperation.READ_ACTIVE)))
    source = AuthoritySource(profiles)
    inner, sink = FakeCibSevenTransport(), FakeStartAuditSink()
    wrapped = bind_cibseven_start_preflight(
        inner=inner,
        seam=SeamContext("synthetic", schema.workload),
        authorizer=ProfileStartAuthorizer(EngineOperationAuthorizer(profiles, source)),
    )
    data = variables(schema)
    data[dossier][field] = "SYNTHETIC_FORGED_DECISION"
    args = dict(
        process_key=schema.process_key,
        business_key=RESOURCE,
        variables=data,
        audit_sink=sink,
        provenance=AgentDecisionProvenance(schema.workload, VERSION, "synthetic", {}),
    )
    with pytest.raises(CibSevenStartAuthorizationError):
        await start_process_idempotent(wrapped, **args)
    assert source.calls == [] and sink.calls == [] and sink._claimed == {} and inner._variables == {}
    data[dossier][field] = None
    data[dossier]["supporting_fact"] = {"count": 2, "refs": [{"decisao_informada": "ordinary data"}]}
    assert (await start_process_idempotent(wrapped, **args)).start_outcome is StartOutcome.STARTED
    assert inner._variables[RESOURCE] == data
    del data[dossier][field]
    schema.validate(data)  # Optional absence remains legal; no field is silently stripped.


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["authority-start", "authority-read", "audit", "read", "effect"])
@pytest.mark.parametrize("shape", ["dict", "list"])
async def test_repair_snapshot_is_the_audited_and_emitted_payload_at_every_await(boundary, shape):
    p = profile(RAFAEL_START)
    # WP-J1-11: `SP-OP-AUTH-001` passou a ser familia GATED (`EXCLUSIVE`, decisao do dono #16),
    # e o preflight D7-A autoriza `READ_HISTORY` ALEM de `READ_ACTIVE` para familia gated
    # (`gateway/engine_start.py::authorize_start`) — o portao precisa poder perguntar ao engine
    # se a geracao reivindicada terminou. Sem esse perfil o start e' negado
    # (`engine_operation_denied`) antes de qualquer boundary pausado. E' o MESMO provisionamento
    # que `test_preclaim_denial_and_recovery_same_key` ja faz para `CONTAS_PAGTO_START`, e o
    # plano seguro de producao ja o deriva para TODO schema de START
    # (`platform/engine_bootstrap/secured_plan.py::_registered`), entao isto alinha a fixture
    # com o que a implantacao real instala — nao afrouxa nada.
    profiles = (
        p,
        replace(p, schema=start_read_schema(p.schema, EngineOperation.READ_ACTIVE)),
        replace(p, schema=start_read_schema(p.schema, EngineOperation.READ_HISTORY)),
    )
    data = variables(p.schema)
    field = "dmn_decision_refs" if shape == "dict" else "documentos_refs"
    data[field] = {"refs": [{"id": "original"}]} if shape == "dict" else [{"refs": ["original"]}]
    alias = data[field]
    original_json = canonical_json(data)
    original = parse_json(original_json)
    ready, proceed = asyncio.Event(), asyncio.Event()

    async def pause(at):
        if boundary == at:
            ready.set()
            await proceed.wait()

    source = AuthoritySource(profiles)

    class PausedAuthority:
        async def resolve(self, req):
            response = await source.resolve(req)
            await pause("authority-start" if req.operation is EngineOperation.START else "authority-read")
            return response

    class PausedSink(FakeStartAuditSink):
        async def emit_once_status(self, record, *, dedup_key):
            await pause("audit")
            return await super().emit_once_status(record, dedup_key=dedup_key)

    class PausedTransport(FakeCibSevenTransport):
        async def find_active_instance(self, business_key):
            await pause("read")
            return await super().find_active_instance(business_key)

        async def start_process_instance(self, process_key, business_key, variables):
            await pause("effect")
            self.wire = _to_camunda_vars(variables)
            return await super().start_process_instance(process_key, business_key, variables)

    inner, sink = PausedTransport(), PausedSink()
    wrapped = bind_cibseven_start_preflight(
        inner=inner,
        seam=SeamContext("synthetic", "rafael"),
        authorizer=ProfileStartAuthorizer(EngineOperationAuthorizer(profiles, PausedAuthority())),
    )
    pending = asyncio.create_task(
        start_process_idempotent(
            wrapped,
            process_key=p.target.process_key,
            business_key=RESOURCE,
            variables=data,
            audit_sink=sink,
            provenance=AgentDecisionProvenance("rafael", VERSION, "synthetic", {}),
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), 2)
        if shape == "dict":
            alias["refs"][0]["id"] = "changed"
            alias.update(
                {
                    "type": "Object",
                    "value": "SYNTHETIC_UNREVIEWED",
                    "valueInfo": {"serializationDataFormat": "application/x-java-serialized-object"},
                }
            )
        else:
            alias[0]["refs"].append("changed")
            alias.append({"injected": ["changed"]})
        data[field] = {"replaced": True}
    finally:
        proceed.set()
    result = await pending
    assert result.start_outcome is StartOutcome.STARTED
    assert source.calls[0].variables_json == original_json
    assert inner._variables[RESOURCE] == original
    assert inner.wire == _to_camunda_vars(original)
    assert len(sink.calls) == len(sink._claimed) == len(inner._history[RESOURCE]) == 1
    assert sink.records[0].details["input_sha256"] == hash_input(original)


class _MutableString(str):
    pass


class _MutableBytes(bytes):
    pass


@pytest.mark.parametrize(
    "slot,value",
    [
        ("fixed_json", bytearray(b'"BRL"')),
        ("fixed_json", memoryview(b'"BRL"')),
        ("fixed_json", _MutableBytes(b'"BRL"')),
        ("fixed_json", b' {"v": 1}'),
        ("fixed_json", b'{"v":1,"v":2}'),
        ("fixed_json", b"NaN"),
        ("fixed_json", b"[] trailing"),
        ("empty_evidence_when", ["lastro_origem", b'"automatic"']),
        ("empty_evidence_when", ("lastro_origem", bytearray(b'"automatic"'))),
        ("empty_evidence_when", ("lastro_origem", _MutableBytes(b'"automatic"'))),
        ("empty_evidence_when", (_MutableString("lastro_origem"), b'"automatic"')),
        ("empty_evidence_when", ("lastro_origem", b'"automatic"', b'"extra"')),
        ("empty_evidence_when", ("lastro_origem",)),
        ("empty_evidence_when", ([], b'"automatic"')),
        ("null_members", (_MutableString("decisao_cred"),)),
        ("null_members", (["decisao_cred"],)),
        ("origin", "prior_human_evidence"),
        ("kind", "String"),
    ],
)
def test_repair_nested_policy_values_reject_mutable_and_malformed_members(slot, value):
    with pytest.raises(EngineCapabilityError):
        VariableField("synthetic", ValueKind.STRING, **{slot: value}) if slot != "kind" else VariableField(
            "synthetic", value
        )


@pytest.mark.parametrize("slot", ["identity", "target", "source_target"])
def test_repair_profile_rejects_mutable_identity_and_target_lookalikes(slot):
    from dataclasses import asdict

    p = profile(CONTAS_PAGTO_START)
    values = asdict(getattr(p, slot))
    if slot == "identity":
        values["origin"] = "request_header"
    with pytest.raises(EngineCapabilityError):
        replace(p, **{slot: SimpleNamespace(**values)})


@pytest.mark.parametrize(
    "slot,value",
    [
        ("topic", []),
        ("message", []),
        ("worker_id", []),
        ("resource_ref", []),
        ("variables_json", bytearray(b"{}")),
        ("correlation_json", bytearray(b"{}")),
        ("parameters_json", bytearray(b"{}")),
    ],
)
def test_repair_request_rejects_falsey_mutable_members(slot, value):
    with pytest.raises(EngineCapabilityError):
        replace(request(profile()), **{slot: value})


@pytest.mark.parametrize(
    "slot,value",
    [
        ("sources", (["synthetic"],)),
        ("correlation_fields", (_MutableString("tenant_id"),)),
        ("error_codes", ([],)),
        ("read_projection", (["synthetic"],)),
        ("topic", []),
        ("message", []),
        ("source_process_key", []),
        ("source_topic", []),
        ("audit_actor", []),
        ("fields", (SimpleNamespace(name="tenant_id"),)),
    ],
)
def test_repair_schema_rejects_equivalent_nested_mutability(slot, value):
    with pytest.raises(EngineCapabilityError):
        replace(HELENA_START, **{slot: value})


@pytest.mark.parametrize(
    "slot,value", [("origin", "request_header"), ("subject", _MutableString("synthetic"))]
)
def test_repair_profile_revalidates_real_identity_runtime_invariants(slot, value):
    # Even an exact-class object bypassing its constructor cannot enter trusted configuration.
    p = profile()
    malformed = replace(p.identity)
    object.__setattr__(malformed, slot, value)
    with pytest.raises(EngineCapabilityError):
        replace(p, identity=malformed)


def test_repair_profile_and_request_ports_reject_mutable_lookalikes():
    with pytest.raises(EngineCapabilityError):
        EngineOperationAuthorizer((SimpleNamespace(schema=HELENA_START, target=profile().target),), None)
    with pytest.raises(EngineCapabilityError):
        replace(profile().target, topic=[])
    with pytest.raises(EngineCapabilityError):
        AttestedField("synthetic", "engine_fact", b'"value"', "synthetic-source")


@pytest.mark.asyncio
async def test_repair_authority_and_request_reject_mutable_lookalikes():
    p = profile()
    source = AuthoritySource((p,))
    authorizer = EngineOperationAuthorizer((p,), source)
    with pytest.raises(EngineCapabilityError):
        await authorizer.authorize(SimpleNamespace(**{"operation": EngineOperation.START}))
    original = await source.resolve(request(p))
    for slot, value in (
        ("identity", SimpleNamespace()),
        ("target", SimpleNamespace()),
        ("source_target", SimpleNamespace()),
        ("lock_owner", []),
        ("source_task_ref", []),
        ("prior_human_completed", 1),
    ):
        with pytest.raises(EngineCapabilityError):
            replace(original, **{slot: value})
    malformed = AttestedField("synthetic", FieldOrigin.ENGINE, b'"value"', "synthetic-source")
    object.__setattr__(malformed, "name", [])
    with pytest.raises(EngineCapabilityError):
        replace(original, fields=(malformed,))
