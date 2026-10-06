"""Published-main additive provider schema versus exact R5 seams; synthetic UNIT ports only."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from maezo.gateway.capabilities.admission import CapabilityAdmission
from maezo.gateway.capabilities.durable_execution import RestoredCommand
from maezo.gateway.capabilities.journeys.contracts import (
    JourneyContractError,
    PreparedCapabilityAction,
    parse_action,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    PROVIDER_SCHEMA_VERSION,
    CapabilityContractError,
    CapabilityRefusalReason,
    ContextAccessResult,
    DeliveryEvidenceReceipt,
    ProviderCapabilityEnvelope,
    parse_envelope,
    parse_request,
    parse_result,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pep import build_pep
from tests.unit.gateway.capabilities.journeys.helpers import MEMBERS, request_payload
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness as journey_harness
from tests.unit.gateway.capabilities.test_admission import (
    UnitAudit,
    UnitAuthority,
    binding,
    envelope,
    guard,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    NOW as OP12_NOW,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    UnitAdmissionAudit,
    UnitAdmissionAuthority,
    UnitReader,
    UnitVerifier,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    binding as op12_binding,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    envelope as op12_envelope,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    request as op12_request,
)
from tests.unit.gateway.capabilities.test_contract_authority import (
    source as op12_source,
)
from tests.unit.gateway.capabilities.test_durable_execution import harness as durable_harness

COMMON = ("access.resolve", "notice.prepare_or_send")


def provider_env(operation):
    return ProviderCapabilityEnvelope.model_validate(
        {**envelope(operation_name=operation).__dict__, "schema_version": PROVIDER_SCHEMA_VERSION}
    )


def carrier(value, representation):
    if representation == "model":
        return value
    if representation == "dict":
        return value.model_dump(mode="python")
    if representation == "bytes":
        return value.model_dump_json().encode()
    return value.model_copy(deep=True)


def nonjourney_durable(operation):
    h = durable_harness()
    h.admission = guard(
        scope=binding(operation_name=operation), authority=h.authority, audit=UnitAudit(h.authority)
    )
    h.service = CapabilityService(
        admission=h.admission, sources={operation: h.source}, durable=h.executor, memberships=MEMBERS
    )
    return h


@pytest.mark.parametrize("operation", COMMON)
@pytest.mark.parametrize("representation", ["model", "dict", "bytes", "copy"])
def test_generic_provider_parser_remains_valid_but_prepared_r5_action_rejects_common_names(
    operation, representation
):
    env = provider_env(operation)
    assert type(parse_envelope(carrier(env, representation))) is ProviderCapabilityEnvelope
    payload = parse_request(
        operation, request_payload(operation), memberships=MEMBERS, schema_version=PROVIDER_SCHEMA_VERSION
    )
    h = journey_harness()
    prepared = deepcopy(h.action.__dict__)
    prepared.update(
        envelope=carrier(env, representation),
        request=payload,
        cursor_ref="C1_need" if operation == "access.resolve" else "C1_present",
    )
    with pytest.raises(ValidationError):
        PreparedCapabilityAction.model_validate(prepared, context={"memberships": MEMBERS})
    with pytest.raises(JourneyContractError):
        parse_action(prepared, MEMBERS)
    assert h.journal.snapshot is None and h.authority.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
@pytest.mark.parametrize("representation", ["model", "dict", "bytes", "copy"])
async def test_actual_durable_service_provider_rejected_before_executor_metadata_or_authority(
    operation, representation
):
    h = nonjourney_durable(operation)
    calls = []
    original = h.executor.execute

    async def entered(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)

    h.executor.execute = entered
    result = await h.service.execute_durable(
        carrier(provider_env(operation), representation),
        request_payload(operation),
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert calls == [] and h.authority.events == [] and h.source.calls == 0 and h.journal.snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
@pytest.mark.parametrize("representation", ["model", "dict", "bytes", "copy"])
async def test_direct_dur3_provider_rejected_before_custody_prepare_or_journal_source(
    operation, representation
):
    h = nonjourney_durable(operation)
    request = parse_request(operation, request_payload(operation), memberships=MEMBERS)
    result = await h.executor.execute(
        carrier(provider_env(operation), representation),
        request,
        admission=h.admission,
        source=h.source,
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
        timeout_seconds=1,
        memberships=MEMBERS,
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.authority.events == [] and h.source.calls == 0 and h.journal.snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
async def test_provider_admission_binding_cannot_be_used_for_v21_metadata(operation):
    h = nonjourney_durable(operation)
    h.admission = guard(
        scope=binding(operation_name=operation, schema_version=PROVIDER_SCHEMA_VERSION),
        authority=h.authority,
        audit=UnitAudit(h.authority),
    )
    result = await h.executor.execute(
        envelope(operation_name=operation),
        parse_request(operation, request_payload(operation)),
        admission=h.admission,
        source=h.source,
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
        timeout_seconds=1,
        memberships=MEMBERS,
    )
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.authority.events == [] and h.source.calls == 0 and h.journal.snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
async def test_restored_provider_descriptor_rejected_after_protected_read_before_journal_or_read_authority(
    operation,
):
    h = nonjourney_durable(operation)
    env = provider_env(operation)
    request = parse_request(operation, request_payload(operation), memberships=MEMBERS)
    # Protected custody fixture returns a forged revived descriptor. This read
    # is necessary to discover its schema; it cannot authorize later IO.
    base = durable_harness()
    await base.service.execute_durable(
        envelope(),
        base.custody.request,
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
    )
    descriptor = base.journal.descriptor.model_copy(update={"envelope": env})
    handle = base.journal.snapshot.handle
    calls = []

    async def restored(*args):
        calls.append("protected_restore_read")
        return RestoredCommand(handle, descriptor, request)

    h.custody.restore_command = restored
    result = await h.executor.observe(
        handle.command_ref, admissions={operation: h.admission}, memberships=MEMBERS
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH and calls == ["protected_restore_read"]
    assert h.authority.events == [] and h.source.calls == 0 and h.journal.snapshot is None


@pytest.mark.parametrize(
    "schema", [None, "", "provider-capabilities.internal.v9", "v21-capabilities.proposed.v0"]
)
@pytest.mark.asyncio
async def test_unknown_or_missing_schema_does_not_downgrade_to_v21(schema):
    h = nonjourney_durable("access.resolve")
    raw = envelope(operation_name="access.resolve").model_dump()
    if schema is None:
        raw.pop("schema_version")
    else:
        raw["schema_version"] = schema
    result = await h.service.execute_durable(
        raw,
        request_payload("access.resolve"),
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.authority.events == [] and h.source.calls == 0


@pytest.mark.asyncio
async def test_op12_remains_provider_only_and_r5_never_queries_or_writes_it():
    env = op12_envelope()
    intent = op12_request()
    assert type(parse_envelope(env)) is ProviderCapabilityEnvelope
    for candidate in [
        env.model_dump() | {"schema_version": CANDIDATE_SCHEMA_VERSION},
        env.model_copy(update={"schema_version": CANDIDATE_SCHEMA_VERSION}),
    ]:
        with pytest.raises(CapabilityContractError):
            parse_envelope(candidate)
    with pytest.raises(CapabilityContractError):
        parse_request("contract.authority.resolve", intent)
    h = nonjourney_durable("access.resolve")
    result = await h.service.execute_durable(
        env, intent, effect_authority=None, predecessor_command_refs=(), expected_journal_revision=0
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH and h.authority.events == []


class GenericSyntheticSource:
    """UNIT source binding and result attestation mechanics, no real provider."""

    def __init__(self, scope, result, events):
        self.binding, self.result, self.events = scope, result, events
        self.calls = []

    async def execute(self, env, request, *, timeout_seconds):
        assert type(env) is ProviderCapabilityEnvelope and env.schema_version == PROVIDER_SCHEMA_VERSION
        self.events.append("source")
        self.calls.append((deepcopy(env), deepcopy(request)))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
async def test_generic_provider_common_operation_dispatch_and_lease_scope_remain_intact(operation):
    authority = UnitAuthority()
    scope = binding(operation_name=operation, schema_version=PROVIDER_SCHEMA_VERSION)
    admission = guard(scope=scope, authority=authority, audit=UnitAudit(authority))
    result = (
        ContextAccessResult(
            access_status="available",
            context_refs=("UNIT-provider-context",),
            access_decision_ref="UNIT-provider-access",
        )
        if operation == "access.resolve"
        else DeliveryEvidenceReceipt(delivery_status="pending", attempt_revision="UNIT-provider-attempt")
    )
    source = GenericSyntheticSource(scope, result, authority.events)
    service = CapabilityService(admission=admission, sources={operation: source})
    outcome = await service.execute(provider_env(operation), request_payload(operation))
    assert outcome.succeeded and outcome.result == result and len(source.calls) == 1
    assert authority.events == ["authority", "audit", "current", "source", "receipt", "current"]
    assert admission._leases == {}
    with pytest.raises(CapabilityContractError):
        CapabilityService(
            admission=admission,
            sources={
                operation: GenericSyntheticSource(
                    scope.model_copy(update={"principal_ref": "UNIT-other"}), result, []
                )
            },
        )


@pytest.mark.asyncio
async def test_actual_generic_provider_op12_positive_and_unknown_negative_preserve_receipt_distinction():
    authority = UnitAdmissionAuthority()
    admission = CapabilityAdmission(
        binding=op12_binding(),
        authority=authority,
        audit=UnitAdmissionAudit(authority),
        autonomy=build_pep(tenant="unit-tenant"),
        clock=lambda: OP12_NOW,
    )
    reader, verifier = UnitReader(), UnitVerifier()
    service = CapabilityService(
        admission=admission, sources={"contract.authority.resolve": op12_source(reader, verifier)}
    )
    outcome = await service.execute(op12_envelope(), op12_request())
    assert outcome.succeeded and outcome.result.authority_receipt_ref == reader.value.authority_receipt_ref
    assert len(reader.calls) == 1 and authority.events == [
        "authority",
        "audit",
        "current",
        "receipt",
        "current",
    ]
    unavailable = await CapabilityService().execute(op12_envelope(), op12_request())
    assert unavailable.refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert unavailable.result is None and unavailable.unavailable_observation.model_dump() == {
        "contract_status": "unknown"
    }


def test_provider_discriminators_and_op08_request_context_are_not_projected_into_v21():
    common = {
        "offer_ref": "UNIT-offer",
        "offer_version": "UNIT-version",
        "decision": "accept",
        "terms_evidence_ref": "UNIT-terms",
    }
    provider = common | {
        "acceptance_domain": "provider_contract",
        "party_authority_proof_ref": "UNIT-party",
        "contract_instrument_ref": "UNIT-instrument",
        "contract_version": "UNIT-contract",
    }
    customer = common | {
        "acceptance_domain": "customer_offer",
        "customer_authority_proof_ref": "UNIT-customer",
    }
    assert (
        parse_request("acceptance.record", provider, schema_version=PROVIDER_SCHEMA_VERSION).acceptance_domain
        == "provider_contract"
    )
    assert (
        parse_request("acceptance.record", customer, schema_version=PROVIDER_SCHEMA_VERSION).acceptance_domain
        == "customer_offer"
    )
    for value in (provider, customer):
        with pytest.raises(CapabilityContractError):
            parse_request("acceptance.record", value)
    intent = parse_request(
        "external_wait.settle",
        {
            "wait_ref": "UNIT-wait",
            "expected_producer_ref": "UNIT-producer",
            "correlation_ref": "UNIT-corr",
            "counterparty_deadline_ref": "UNIT-counterparty",
            "symmetry_evidence_ref": "UNIT-symmetry",
        },
        schema_version=PROVIDER_SCHEMA_VERSION,
    )
    result = {
        "settlement_revision": "UNIT-revision",
        "settled_at": "2026-10-05T12:00:00Z",
        "outcome": "pending",
        "source_observation_ref": "UNIT-observation",
    }
    with pytest.raises(CapabilityContractError):
        parse_result("external_wait.settle", result, schema_version=PROVIDER_SCHEMA_VERSION, request=intent)


@pytest.mark.parametrize("operation", COMMON)
def test_existing_v21_administrative_consumer_rejects_provider_before_payload_parse(monkeypatch, operation):
    from maezo.agents.lucas.administrative import state
    from maezo.agents.lucas.administrative.handoff import AdministrativeInputError
    from tests.unit.gateway.capabilities.journeys.helpers import turn

    h = journey_harness()
    current = turn(h.binding, 0)
    seen = []
    original = state.parse_request

    def parsed(*args, **kwargs):
        seen.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(state, "parse_request", parsed)
    with pytest.raises(AdministrativeInputError):
        state.new_administrative_state(
            {
                "envelope": provider_env(operation),
                "payload": request_payload(operation),
                "handoff": current.handoff,
                "current_message_ref": current.current_message_ref,
                "health_priority": False,
                "human_requested": False,
            }
        )
    assert seen == [] and h.authority.events == [] and h.journal.snapshot is None


@pytest.mark.parametrize("operation", COMMON)
def test_forged_provider_model_cannot_downgrade_by_changing_schema_literal(operation):
    provider = provider_env(operation).model_copy(update={"schema_version": CANDIDATE_SCHEMA_VERSION})
    with pytest.raises(CapabilityContractError):
        parse_envelope(provider)
    h = journey_harness()
    with pytest.raises(JourneyContractError):
        parse_action(h.action.model_copy(update={"envelope": provider}), MEMBERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", COMMON)
async def test_restore_schema_guard_precedes_request_parsing(monkeypatch, operation):
    from maezo.gateway.capabilities import durable_execution
    from maezo.gateway.capabilities.durability.models import CommandDescriptor, CommandHandle

    h = nonjourney_durable(operation)
    env = provider_env(operation)
    request = parse_request(operation, request_payload(operation), memberships=MEMBERS)
    descriptor = CommandDescriptor.model_construct(
        envelope=env,
        request_ref="UNIT-protected-provider-request",
        request_sha256="a" * 64,
        admission_binding_sha256="b" * 64,
        predecessor_command_refs=(),
    )
    handle = CommandHandle(
        binding=h.executor.binding, command_ref="UNIT-restored-provider-command", request_sha256="a" * 64
    )

    async def restore(*args):
        return RestoredCommand(handle, descriptor, request)

    h.custody.restore_command = restore
    seen = []
    original = durable_execution.parse_request

    def parse(*args, **kwargs):
        seen.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(durable_execution, "parse_request", parse)
    result = await h.executor.observe(
        handle.command_ref, admissions={operation: h.admission}, memberships=MEMBERS
    )
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH and seen == [] and h.authority.events == []


def test_provider_contractual_op08_and_op11_fields_remain_strict_and_version_owned():
    wait = {"wait_ref": "UNIT-wait", "expected_producer_ref": "UNIT-producer", "correlation_ref": "UNIT-corr"}
    ordinary = parse_request("external_wait.settle", wait, schema_version=PROVIDER_SCHEMA_VERSION)
    contractual = parse_request(
        "external_wait.settle",
        wait | {"counterparty_deadline_ref": "UNIT-deadline", "symmetry_evidence_ref": "UNIT-symmetry"},
        schema_version=PROVIDER_SCHEMA_VERSION,
    )
    result = {"wait_status": "pending", "settlement_revision": "UNIT-revision", "owner_case_ref": "UNIT-case"}
    assert parse_result(
        "external_wait.settle", result, schema_version=PROVIDER_SCHEMA_VERSION, request=ordinary
    )
    with pytest.raises(CapabilityContractError):
        parse_result(
            "external_wait.settle", result, schema_version=PROVIDER_SCHEMA_VERSION, request=contractual
        )
    symmetric = parse_result(
        "external_wait.settle",
        result | {"symmetry_status": "symmetric"},
        schema_version=PROVIDER_SCHEMA_VERSION,
        request=contractual,
    )
    assert symmetric.symmetry_status == "symmetric"
    with pytest.raises(CapabilityContractError):
        parse_result(
            "external_wait.settle",
            result | {"symmetry_status": "symmetric"},
            schema_version=PROVIDER_SCHEMA_VERSION,
            request=ordinary,
        )
    feedback = {
        "case_or_fulfillment_ref": "UNIT-case",
        "instrument_version_ref": "UNIT-instrument",
        "respondent_authority_ref": "UNIT-respondent",
        "feedback_evidence_ref": "UNIT-evidence",
        "respondent_kind": "provider",
    }
    assert (
        parse_request("feedback.record", feedback, schema_version=PROVIDER_SCHEMA_VERSION).respondent_kind
        == "provider"
    )
    with pytest.raises(CapabilityContractError):
        parse_request("feedback.record", feedback)


def provider_positive_fixture(operation):
    if operation == "contract.authority.resolve":
        return op12_request(), None
    request = request_payload(operation)
    if operation == "acceptance.record":
        request["acceptance_domain"] = "customer_offer"
    if operation == "feedback.record":
        request = {
            "case_or_fulfillment_ref": "UNIT-case",
            "instrument_version_ref": "UNIT-instrument",
            "respondent_authority_ref": "UNIT-respondent",
            "feedback_evidence_ref": "UNIT-evidence",
            "respondent_kind": "provider",
        }
    result = {
        "access.resolve": {
            "access_status": "available",
            "context_refs": [],
            "access_decision_ref": "UNIT-access",
        },
        "offer.compose": {"option_refs": [], "explanation_refs": [], "status": "unavailable"},
        "acceptance.record": {
            "acceptance_ref": "UNIT-acceptance",
            "recorded_decision": "declined",
            "recorded_at": "2026-10-04T12:00:00Z",
            "receipt_ref": "UNIT-synthetic-receipt",
            "business_revision": "UNIT-source-revision",
        },
        "enrollment.request": {"enrollment_status": "pending"},
        "reservation.command": {"reservation_status": "pending"},
        "fulfillment.observe": {
            "fact_kind": "pending",
            "source_fact_ref": "UNIT-pending-source-fact",
            "source_revision": "UNIT-source-revision",
        },
        "case.open_or_update": {
            "case_ref": "UNIT-case",
            "case_status": "unit-case-status",
            "authority_receipt_ref": "UNIT-synthetic-case-receipt",
            "business_revision": "UNIT-source-revision",
        },
        "external_wait.settle": {
            "wait_status": "pending",
            "settlement_revision": "UNIT-source-revision",
            "owner_case_ref": "UNIT-case",
        },
        "notice.prepare_or_send": {"delivery_status": "pending", "attempt_revision": "UNIT-source-revision"},
        "milestone.publish": {"publication_status": "pending", "milestone_revision": "UNIT-source-revision"},
        "feedback.record": {
            "feedback_observation_ref": "UNIT-feedback",
            "feedback_kind": "no_response",
            "recorded_at": "2026-10-04T12:00:00Z",
            "does_not_authorize_clinical_or_financial_change": True,
        },
    }[operation]
    parsed_request = parse_request(
        operation, request, memberships=MEMBERS, schema_version=PROVIDER_SCHEMA_VERSION
    )
    parsed_result = parse_result(
        operation, result, memberships=MEMBERS, schema_version=PROVIDER_SCHEMA_VERSION, request=parsed_request
    )
    return parsed_request, parsed_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "access.resolve",
        "offer.compose",
        "acceptance.record",
        "enrollment.request",
        "reservation.command",
        "fulfillment.observe",
        "case.open_or_update",
        "external_wait.settle",
        "notice.prepare_or_send",
        "milestone.publish",
        "feedback.record",
        "contract.authority.resolve",
    ],
)
async def test_all12_published_generic_provider_operations_reach_exact_source_once_without_r5_migration(
    operation,
):
    if operation == "contract.authority.resolve":
        authority = UnitAdmissionAuthority()
        admission = CapabilityAdmission(
            binding=op12_binding(),
            authority=authority,
            audit=UnitAdmissionAudit(authority),
            autonomy=build_pep(tenant="unit-tenant"),
            clock=lambda: OP12_NOW,
        )
        reader, verifier = UnitReader(), UnitVerifier()
        service = CapabilityService(admission=admission, sources={operation: op12_source(reader, verifier)})
        result = await service.execute(op12_envelope(), op12_request())
        assert (
            result.succeeded
            and len(reader.calls) == 1
            and result.result.authority_receipt_ref == reader.value.authority_receipt_ref
        )
    else:
        authority = UnitAuthority()
        scope = binding(operation_name=operation, schema_version=PROVIDER_SCHEMA_VERSION)
        admission = guard(scope=scope, authority=authority, audit=UnitAudit(authority))
        request, expected = provider_positive_fixture(operation)
        source = GenericSyntheticSource(scope, expected, authority.events)
        service = CapabilityService(admission=admission, sources={operation: source}, memberships=MEMBERS)
        result = await service.execute(provider_env(operation), request)
        assert result.succeeded and result.result == expected and len(source.calls) == 1
        assert authority.events == ["authority", "audit", "current", "source", "receipt", "current"]
    assert not admission._leases and service.durable is None and not service.journey_sources
