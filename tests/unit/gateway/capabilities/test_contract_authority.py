"""PW2 contract/adapter negatives; synthetic UNIT ports are never source qualification."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from maezo.gateway.capabilities.admission import AdmissionBinding
from maezo.gateway.capabilities.contract_authority import (
    ContractAuthoritySource,
    ContractClauseGroup,
    ContractReadScope,
    ContractSnapshot,
    ContractSnapshotProof,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    PROVIDER_SCHEMA_VERSION,
    REQUEST_MODELS,
    RESULT_MODELS,
    CapabilityContractError,
    CapabilityOutcome,
    CapabilityRefusalReason,
    ContractAuthorityResult,
    ContractResolutionUnavailable,
    ProviderCapabilityEnvelope,
    parse_envelope,
    parse_request,
    parse_result,
    request_digest,
)
from maezo.gateway.capabilities.service import CapabilityService

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
EXPIRY = NOW + timedelta(hours=1)  # UNIT proof ceiling only, not a product policy.


def binding(**updates) -> AdmissionBinding:
    return AdmissionBinding(
        **{
            "principal_ref": "unit-reader-principal",
            "task_ref": "unit-contract-task",
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "purpose_ref": "unit-qualified-read-purpose",
            "operation_name": "contract.authority.resolve",
            "schema_version": PROVIDER_SCHEMA_VERSION,
            "contract_revision": "unit-contract-revision",
            "source_authority_ref": "unit-source",
            "policy_revision": "unit-policy",
            "data_classification": "unit-general",
            "autonomy_action": "scheduling",
            "security_zone": "general",
            **updates,
        }
    )


def envelope(**updates) -> ProviderCapabilityEnvelope:
    return ProviderCapabilityEnvelope(
        **{
            "schema_version": PROVIDER_SCHEMA_VERSION,
            "operation_name": "contract.authority.resolve",
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "journey_ref": "unit-journey",
            "correlation_ref": "unit-correlation",
            "causation_ref": "unit-cause",
            "idempotency_key": "unit-invocation",
            "expected_business_revision": "unit-business-1",
            "source_authority_ref": "unit-source",
            "policy_revision": "unit-policy",
            "data_classification": "unit-general",
            **updates,
        }
    )


def request(**updates):
    return parse_request(
        "contract.authority.resolve",
        {
            "provider_ref": "unit-provider",
            "contract_instrument_ref": "unit-instrument",
            "clause_purpose": "payment_prazo",
            "purpose_policy_ref": "unit-purpose-policy",
            "expected_business_revision": "unit-business-1",
            **updates,
        },
        schema_version=PROVIDER_SCHEMA_VERSION,
    )


def snapshot(**updates) -> ContractSnapshot:
    return ContractSnapshot(
        **{
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "provider_ref": "unit-provider",
            "contract_instrument_ref": "unit-instrument",
            "business_revision": "unit-business-1",
            "source_revision_ref": "unit-publication-3",
            "contract_status": "vigente",
            "admission_state": "enabled",
            "clause_groups": (
                ContractClauseGroup(
                    clause_purpose="payment_prazo",
                    clause_refs=("unit-payment-clause",),
                    declared_value_refs=("unit-payment-value",),
                ),
                ContractClauseGroup(
                    clause_purpose="glosa_symmetry",
                    clause_refs=("unit-symmetry-clause",),
                    declared_value_refs=("unit-symmetry-value",),
                ),
            ),
            "evidence_ref": "UNIT-SYNTHETIC-EVIDENCE-NOT-LEGAL-ACT",
            "declared_at": NOW,
            "authority_receipt_ref": "UNIT-SYNTHETIC-SOURCE-RECEIPT",
            "source_publication_ref": "unit-publication",
            "currentness_ref": "unit-current-head",
            "valid_from": NOW - timedelta(hours=1),
            "valid_until": EXPIRY,
            **updates,
        }
    )


class UnitReader:
    def __init__(self, value: ContractSnapshot | None = None):
        self.value = value or snapshot()
        self.calls: list[ContractReadScope] = []

    async def resolve(self, scope, *, timeout_seconds):
        self.calls.append(scope)
        return self.value


class UnitVerifier:
    """UNIT-only synthetic proof issuer; real mandate/publication tests belong to D1."""

    def __init__(self):
        self.initial_updates = {}
        self.current_updates = {}
        self.calls = []
        self.revoked = False

    async def verify_snapshot(self, scope, source):
        self.calls.append(("verify", scope))
        return ContractSnapshotProof(
            **{
                "scope": scope,
                "snapshot_sha256": source.snapshot_sha256(),
                "snapshot_key": source.snapshot_key(),
                "authority_receipt_ref": source.authority_receipt_ref,
                "source_publication_ref": source.source_publication_ref,
                "currentness_ref": source.currentness_ref,
                "checked_at": NOW,
                "valid_until": EXPIRY,
                **self.initial_updates,
            }
        )

    async def check_current(self, scope, proof):
        self.calls.append(("current", scope))
        if self.revoked:
            raise CapabilityContractError(CapabilityRefusalReason.STALE_REVISION)
        return proof.model_copy(update=self.current_updates)


def source(reader=None, verifier=None, **updates):
    return ContractAuthoritySource(
        binding=binding(), reader=reader, verifier=verifier, clock=lambda: NOW, **updates
    )


@pytest.mark.asyncio
async def test_projection_of_two_purposes_reuses_full_snapshot_not_cached_disclosure() -> None:
    reader, verifier = UnitReader(), UnitVerifier()
    port = source(reader, verifier)
    first = await port.execute(envelope(), request(), timeout_seconds=1)
    second = await port.execute(envelope(), request(clause_purpose="glosa_symmetry"), timeout_seconds=1)
    assert first.clause_refs == ("unit-payment-clause",)
    assert second.clause_refs == ("unit-symmetry-clause",)
    assert first.declared_value_refs != second.declared_value_refs
    assert len(reader.calls) == 2
    assert [phase for phase, _ in verifier.calls] == ["verify", "current", "verify", "current"]
    assert request_digest(envelope(), request()) != request_digest(
        envelope(), request(clause_purpose="glosa_symmetry")
    )


@pytest.mark.asyncio
async def test_optional_lookup_requires_source_resolved_identity_before_key_or_projection() -> None:
    reader, verifier = UnitReader(), UnitVerifier()
    result = await source(reader, verifier).execute(
        envelope(), request(contract_instrument_ref=None, expected_business_revision=None), timeout_seconds=1
    )
    assert reader.calls[0].contract_instrument_ref is None
    assert reader.calls[0].expected_business_revision == envelope().expected_business_revision
    assert result.source_revision_ref == reader.value.source_revision_ref
    assert result.authority_receipt_ref == reader.value.authority_receipt_ref
    # The key uses only source-resolved non-null identifiers, not caller NULLs.
    changed = reader.value.model_copy(update={"contract_instrument_ref": "unit-other-instrument"})
    assert changed.snapshot_key() != reader.value.snapshot_key()


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_optional", [False, True])
async def test_optional_request_revision_never_erases_mandatory_envelope_expectation(
    omit_optional: bool,
) -> None:
    reader, verifier = UnitReader(snapshot(business_revision="unit-business-2")), UnitVerifier()
    intent = request(contract_instrument_ref=None, expected_business_revision=None)
    if omit_optional:
        intent = parse_request(
            "contract.authority.resolve",
            intent.model_dump(exclude={"expected_business_revision"}),
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
    with pytest.raises(CapabilityContractError) as caught:
        await source(reader, verifier).execute(envelope(), intent, timeout_seconds=1)
    assert caught.value.reason == CapabilityRefusalReason.STALE_REVISION
    assert reader.calls[0].contract_instrument_ref is None
    assert reader.calls[0].expected_business_revision == "unit-business-1"
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_explicit_request_envelope_revision_conflict_is_refused_before_source_io() -> None:
    reader, verifier = UnitReader(), UnitVerifier()
    with pytest.raises(CapabilityContractError) as caught:
        await source(reader, verifier).execute(
            envelope(), request(expected_business_revision="unit-business-2"), timeout_seconds=1
        )
    assert caught.value.reason == CapabilityRefusalReason.STALE_REVISION
    assert reader.calls == []
    assert verifier.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_revision", ["unit-business-1", "unit-business-2"])
async def test_awaited_optional_lookup_keeps_revision_binding_before_disclosure(
    source_revision: str,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class AwaitedUnitReader(UnitReader):
        async def resolve(self, scope, *, timeout_seconds):
            self.calls.append(scope)
            entered.set()
            await release.wait()
            return self.value

    reader, verifier = AwaitedUnitReader(), UnitVerifier()
    pending = asyncio.create_task(
        source(reader, verifier).execute(
            envelope(),
            request(contract_instrument_ref=None, expected_business_revision=None),
            timeout_seconds=1,
        )
    )
    await entered.wait()
    reader.value = snapshot(business_revision=source_revision)
    release.set()
    if source_revision == "unit-business-2":
        with pytest.raises(CapabilityContractError) as caught:
            await pending
        assert caught.value.reason == CapabilityRefusalReason.STALE_REVISION
        assert verifier.calls == []
    else:
        result = await pending
        assert result.source_revision_ref == reader.value.source_revision_ref
        assert [phase for phase, _ in verifier.calls] == ["verify", "current"]
        assert all(scope.expected_business_revision == "unit-business-1" for _, scope in verifier.calls)
    assert reader.calls[0].contract_instrument_ref is None
    assert reader.calls[0].expected_business_revision == "unit-business-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["tenant_ref", "legal_entity_ref", "source_authority_ref", "policy_revision", "data_classification"],
)
async def test_cross_binding_denied_before_reader_io(field) -> None:
    reader = UnitReader()
    with pytest.raises(CapabilityContractError) as caught:
        await source(reader, UnitVerifier()).execute(
            envelope(**{field: "unit-other"}), request(), timeout_seconds=1
        )
    assert caught.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert reader.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,reason",
    [
        ("tenant_ref", "AUTHORITY_UNPROVEN"),
        ("legal_entity_ref", "AUTHORITY_UNPROVEN"),
        ("provider_ref", "AUTHORITY_UNPROVEN"),
        ("contract_instrument_ref", "CONTRACT_MISMATCH"),
        ("business_revision", "STALE_REVISION"),
    ],
)
async def test_snapshot_scope_identity_and_expected_revision_are_checked(field, reason) -> None:
    verifier = UnitVerifier()
    with pytest.raises(CapabilityContractError) as caught:
        await source(UnitReader(snapshot(**{field: "unit-other"})), verifier).execute(
            envelope(), request(), timeout_seconds=1
        )
    assert caught.value.reason.value == reason
    assert verifier.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "received", "invalid", "expired", "revoked"])
async def test_received_or_invalid_evidence_is_not_ratification(state) -> None:
    with pytest.raises(CapabilityContractError) as caught:
        await source(UnitReader(snapshot(admission_state=state)), UnitVerifier()).execute(
            envelope(), request(), timeout_seconds=1
        )
    assert caught.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_sha256", "a" * 64),
        ("snapshot_key", "b" * 64),
        ("authority_receipt_ref", "unit-forged-receipt"),
        ("source_publication_ref", "unit-wrong-publication"),
        ("currentness_ref", "unit-wrong-head"),
        ("valid_until", EXPIRY + timedelta(hours=1)),
    ],
)
async def test_receipt_publication_bytes_and_original_validity_ceiling_must_match(field, value) -> None:
    verifier = UnitVerifier()
    verifier.initial_updates[field] = value
    with pytest.raises(CapabilityContractError) as caught:
        await source(UnitReader(), verifier).execute(envelope(), request(), timeout_seconds=1)
    assert caught.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert [phase for phase, _ in verifier.calls] == ["verify"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["actor", "purpose", "policy", "receipt", "extend", "revoke"])
async def test_currentness_after_io_cannot_switch_actor_purpose_policy_or_extend_proof(change) -> None:
    reader, verifier = UnitReader(), UnitVerifier()
    original = await verifier.verify_snapshot(
        source(reader, verifier)._scope(envelope(), request()), reader.value
    )
    verifier.calls.clear()
    if change in {"actor", "purpose", "policy"}:
        field = {"actor": "principal_ref", "purpose": "clause_purpose", "policy": "purpose_policy_ref"}[
            change
        ]
        value = "tabela_valor" if change == "purpose" else "unit-other"
        verifier.current_updates["scope"] = original.scope.model_copy(update={field: value})
    elif change == "receipt":
        verifier.current_updates["authority_receipt_ref"] = "unit-other-receipt"
    elif change == "extend":
        verifier.initial_updates["valid_until"] = EXPIRY - timedelta(minutes=1)
        verifier.current_updates["valid_until"] = EXPIRY
    else:
        verifier.revoked = True
    with pytest.raises(CapabilityContractError):
        await source(reader, verifier).execute(envelope(), request(), timeout_seconds=1)
    assert [phase for phase, _ in verifier.calls] == ["verify", "current"]


@pytest.mark.asyncio
async def test_expiry_after_async_source_read_is_refused() -> None:
    times = iter([NOW, NOW, EXPIRY])
    port = ContractAuthoritySource(
        binding=binding(), reader=UnitReader(), verifier=UnitVerifier(), clock=lambda: next(times)
    )
    with pytest.raises(CapabilityContractError) as caught:
        await port.execute(envelope(), request(), timeout_seconds=1)
    assert caught.value.reason == CapabilityRefusalReason.STALE_REVISION


@pytest.mark.asyncio
async def test_unavailable_clause_does_not_substitute_other_purpose_values() -> None:
    verifier = UnitVerifier()
    with pytest.raises(CapabilityContractError) as caught:
        await source(UnitReader(), verifier).execute(
            envelope(), request(clause_purpose="tabela_valor"), timeout_seconds=1
        )
    assert caught.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ports,reason",
    [
        ({}, "SOURCE_UNAVAILABLE"),
        ({"reader": UnitReader()}, "AUTHORITY_UNPROVEN"),
    ],
)
async def test_no_default_source_or_verifier_creates_authority(ports, reason) -> None:
    with pytest.raises(CapabilityContractError) as caught:
        await source(**ports).execute(envelope(), request(), timeout_seconds=1)
    assert caught.value.reason.value == reason


@pytest.mark.parametrize("forged", [False, 1, "true", None])
def test_literal_true_guards_and_real_receipt_are_mandatory(forged) -> None:
    fields = {
        "contract_status": "vigente",
        "clause_refs": ["unit-clause"],
        "declared_value_refs": [],
        "authority_receipt_ref": "UNIT-SYNTHETIC-SOURCE-RECEIPT",
        "single_provider_scope": True,
        "no_inter_provider_aggregation": True,
    }
    for name in ("single_provider_scope", "no_inter_provider_aggregation"):
        with pytest.raises(CapabilityContractError):
            parse_result(
                "contract.authority.resolve", fields | {name: forged}, schema_version=PROVIDER_SCHEMA_VERSION
            )
    with pytest.raises(CapabilityContractError):
        parse_result(
            "contract.authority.resolve",
            {k: v for k, v in fields.items() if k != "authority_receipt_ref"},
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
    with pytest.raises(CapabilityContractError):
        parse_result(
            "contract.authority.resolve",
            fields | {"contract_status": "unknown"},
            schema_version=PROVIDER_SCHEMA_VERSION,
        )


def test_provider_schema_is_explicit_and_original_eleven_contracts_remain_closed() -> None:
    assert len(REQUEST_MODELS) == len(RESULT_MODELS) == 11
    assert "contract.authority.resolve" not in REQUEST_MODELS
    with pytest.raises(CapabilityContractError):
        parse_request("contract.authority.resolve", request())
    with pytest.raises(CapabilityContractError):
        parse_envelope(envelope().model_dump() | {"schema_version": CANDIDATE_SCHEMA_VERSION})
    with pytest.raises(CapabilityContractError):
        parse_envelope(envelope().model_dump() | {"schema_version": "unpublished.schema"})
    with pytest.raises(CapabilityContractError):
        parse_request(
            "contract.authority.resolve",
            request().model_copy(update={"principal_ref": "planted"}),
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
    with pytest.raises(CapabilityContractError):
        request(clause_purpose="unpublished-purpose")


def test_delta_acceptance_domains_are_discriminated_without_customer_as_party_fallback() -> None:
    common = {
        "offer_ref": "unit-offer",
        "offer_version": "unit-offer-version",
        "decision": "accept",
        "terms_evidence_ref": "unit-terms",
    }
    provider = common | {
        "acceptance_domain": "provider_contract",
        "party_authority_proof_ref": "unit-party",
        "contract_instrument_ref": "unit-instrument",
        "contract_version": "unit-version",
    }
    assert (
        parse_request("acceptance.record", provider, schema_version=PROVIDER_SCHEMA_VERSION).acceptance_domain
        == "provider_contract"
    )
    with pytest.raises(CapabilityContractError):
        parse_request("acceptance.record", provider)
    with pytest.raises(CapabilityContractError):
        parse_request(
            "acceptance.record",
            provider | {"customer_authority_proof_ref": "unit-customer"},
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
    customer = common | {
        "acceptance_domain": "customer_offer",
        "customer_authority_proof_ref": "unit-customer",
    }
    assert (
        parse_request("acceptance.record", customer, schema_version=PROVIDER_SCHEMA_VERSION).acceptance_domain
        == "customer_offer"
    )


def test_delta_wait_and_feedback_version_do_not_silently_extend_v21() -> None:
    wait = {
        "wait_ref": "unit-wait",
        "expected_producer_ref": "unit-producer",
        "correlation_ref": "unit-corr",
        "counterparty_deadline_ref": "unit-counterparty-deadline",
        "symmetry_evidence_ref": "unit-symmetry",
    }
    feedback = {
        "case_or_fulfillment_ref": "unit-external-case",
        "instrument_version_ref": "unit-instrument",
        "respondent_authority_ref": "unit-respondent",
        "feedback_evidence_ref": "unit-evidence",
        "respondent_kind": "provider",
    }
    for operation, payload in [("external_wait.settle", wait), ("feedback.record", feedback)]:
        assert parse_request(operation, payload, schema_version=PROVIDER_SCHEMA_VERSION)
        with pytest.raises(CapabilityContractError):
            parse_request(operation, payload)
    with pytest.raises(CapabilityContractError):
        parse_request(
            "feedback.record",
            feedback | {"respondent_kind": "anonymous"},
            schema_version=PROVIDER_SCHEMA_VERSION,
        )


@pytest.mark.asyncio
async def test_shared_service_absence_yields_typed_unknown_without_source_receipt() -> None:
    outcome = await CapabilityService().execute(envelope(), request())
    assert outcome.refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert outcome.result is None
    assert outcome.unavailable_observation.contract_status == "unknown"
    assert outcome.unavailable_observation.model_dump() == {"contract_status": "unknown"}
    with pytest.raises(ValueError):
        CapabilityOutcome(
            result=ContractAuthorityResult(
                contract_status="vigente",
                clause_refs=(),
                declared_value_refs=(),
                authority_receipt_ref="unit-receipt",
                single_provider_scope=True,
                no_inter_provider_aggregation=True,
            ),
            unavailable_observation=ContractResolutionUnavailable(),
        )


class UnitAdmissionAuthority:
    """UNIT source attestation for lifecycle tests; no installed signer or mandate."""

    def __init__(self):
        self.events = []

    async def authorize(self, scope, env, payload):
        from maezo.gateway.capabilities.admission import VerifiedAuthority

        self.events.append("authority")
        return VerifiedAuthority(
            binding=scope,
            request_sha256=request_digest(env, payload),
            authorization_ref="unit-authorization",
            signed_task_ref="unit-signed-task",
            agent_card_sha256="a" * 64,
            source_contract_publication_ref="unit-publication",
            policy_ratification_ref="unit-ratification",
            currentness_ref="unit-current-head",
            enforcement="enforcing",
            decision="allow",
            verified_at=NOW,
            valid_until=EXPIRY,
        )

    async def check_current(self, authority, *, source_result=None):
        from maezo.gateway.capabilities.admission import VerifiedCurrentness

        self.events.append("current")
        return VerifiedCurrentness(
            binding=authority.binding,
            request_sha256=authority.request_sha256,
            authorization_ref=authority.authorization_ref,
            currentness_ref=authority.currentness_ref,
            source_result_sha256=source_result.result_sha256 if source_result else None,
            checked_at=NOW,
            valid_until=EXPIRY,
        )

    async def verify_source_result(self, authority, result):
        from maezo.gateway.capabilities.admission import VerifiedSourceResult, result_digest

        self.events.append("receipt")
        return VerifiedSourceResult(
            binding=authority.binding,
            request_sha256=authority.request_sha256,
            result_sha256=result_digest(result),
            authorization_ref=authority.authorization_ref,
            source_receipt_ref=result.authority_receipt_ref,
            source_revision_ref=result.source_revision_ref,
            currentness_ref=authority.currentness_ref,
            checked_at=NOW,
            valid_until=EXPIRY,
        )


class UnitAdmissionAudit:
    def __init__(self, authority):
        self.authority = authority

    async def emit_intent(self, intent):
        self.authority.events.append("audit")
        return "b" * 64


@pytest.mark.asyncio
async def test_op12_uses_original_shared_admission_lifecycle_and_independent_result_attestation() -> None:
    from maezo.gateway.capabilities.admission import CapabilityAdmission
    from maezo.gateway.pep import build_pep

    authority = UnitAdmissionAuthority()
    admission = CapabilityAdmission(
        binding=binding(),
        authority=authority,
        audit=UnitAdmissionAudit(authority),
        autonomy=build_pep(tenant="unit-tenant"),
        clock=lambda: NOW,
    )
    reader, verifier = UnitReader(), UnitVerifier()
    service = CapabilityService(
        admission=admission, sources={"contract.authority.resolve": source(reader, verifier)}
    )
    outcome = await service.execute(envelope(), request())
    assert outcome.succeeded and outcome.unavailable_observation is None
    assert outcome.result.authority_receipt_ref == reader.value.authority_receipt_ref
    assert authority.events == ["authority", "audit", "current", "receipt", "current"]
    assert len(reader.calls) == 1
    assert [phase for phase, _ in verifier.calls] == ["verify", "current"]
    assert not admission._leases


@pytest.mark.asyncio
async def test_missing_source_on_shared_service_retains_negative_observation_without_audit_receipt() -> None:
    from maezo.gateway.capabilities.admission import CapabilityAdmission

    service = CapabilityService(admission=CapabilityAdmission(binding=binding()))
    outcome = await service.execute(envelope(), request())
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert outcome.unavailable_observation.model_dump() == {"contract_status": "unknown"}
    assert outcome.result is None


def test_source_actor_and_admission_actor_cannot_differ_in_server_composition() -> None:
    from maezo.gateway.capabilities.admission import CapabilityAdmission

    with pytest.raises(CapabilityContractError) as caught:
        CapabilityService(
            admission=CapabilityAdmission(binding=binding(principal_ref="unit-other-reader")),
            sources={"contract.authority.resolve": source(UnitReader(), UnitVerifier())},
        )
    assert caught.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
async def test_snapshot_replay_with_different_actor_requires_new_actor_proof() -> None:
    reader, verifier = UnitReader(), UnitVerifier()
    first = source(reader, verifier)
    second = ContractAuthoritySource(
        binding=binding(principal_ref="unit-other-reader"),
        reader=reader,
        verifier=verifier,
        clock=lambda: NOW,
    )
    await first.execute(envelope(), request(), timeout_seconds=1)
    await second.execute(envelope(), request(), timeout_seconds=1)
    scopes = [scope for phase, scope in verifier.calls if phase == "verify"]
    assert [scope.principal_ref for scope in scopes] == ["unit-reader-principal", "unit-other-reader"]
    assert reader.value.snapshot_key() == snapshot().snapshot_key()


def test_snapshot_key_is_exact_canonical_tuple_and_changes_with_source_business_revision() -> None:
    import hashlib

    from maezo.portal.engine.profile import canonicalize

    value = snapshot()
    expected = hashlib.sha256(
        canonicalize(["unit-tenant", "OP12", "unit-instrument", "unit-business-1"])
    ).hexdigest()
    assert value.snapshot_key() == expected
    assert value.model_copy(update={"business_revision": "unit-business-2"}).snapshot_key() != expected


def test_explicit_shared_version_keeps_noncontract_waits_without_fabricated_symmetry() -> None:
    from maezo.gateway.capabilities.models import ExternalWaitOutcome, ProviderExternalWaitOutcome

    fields = {
        "wait_ref": "unit-wait",
        "expected_producer_ref": "unit-producer",
        "correlation_ref": "unit-corr",
    }
    regular = parse_request("external_wait.settle", fields, schema_version=PROVIDER_SCHEMA_VERSION)
    result = {"wait_status": "pending", "settlement_revision": "unit-revision", "owner_case_ref": "unit-case"}
    assert (
        type(
            parse_result(
                "external_wait.settle", result, schema_version=PROVIDER_SCHEMA_VERSION, request=regular
            )
        )
        is ExternalWaitOutcome
    )
    contractual = parse_request(
        "external_wait.settle",
        fields | {"counterparty_deadline_ref": "unit-deadline", "symmetry_evidence_ref": "unit-symmetry"},
        schema_version=PROVIDER_SCHEMA_VERSION,
    )
    with pytest.raises(CapabilityContractError):
        parse_result(
            "external_wait.settle", result, schema_version=PROVIDER_SCHEMA_VERSION, request=contractual
        )
    assert (
        type(
            parse_result(
                "external_wait.settle",
                result | {"symmetry_status": "symmetric"},
                schema_version=PROVIDER_SCHEMA_VERSION,
                request=contractual,
            )
        )
        is ProviderExternalWaitOutcome
    )
    with pytest.raises(CapabilityContractError):
        parse_result(
            "external_wait.settle",
            result | {"symmetry_status": "symmetric"},
            schema_version=PROVIDER_SCHEMA_VERSION,
            request=regular,
        )
