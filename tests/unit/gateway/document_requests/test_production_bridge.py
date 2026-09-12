"""WP-J1-03 — the production document-request bridge: exclusivity, decision #18, fail-closed.

Real production classes throughout. The only synthetic boundaries are the two the unit tier
cannot reach: the `mzo_auth_input_head` / `mzo_auth_input_version` SQL (`CaseRespondAuthorities`
methods, exercised for real by the integration tests listed in the PR body) and the native
admission transport. Everything the owner's decision #18 actually turns on —
`resolve_document_recipients`, `PublishedDocumentPolicySource`, `NativeCompletionAuthority`,
topic exclusivity — runs its real code here.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.document_requests import production as production_module
from maezo.gateway.document_requests.models import require as models_require
from maezo.gateway.document_requests.producer import DocumentRequestPolicySource, NoticePlan
from maezo.gateway.document_requests.production import (
    RESPOND_ACTION,
    CaseRespondAuthorities,
    NativeCompletionAuthority,
    PublishedDocumentPolicySource,
    RespondAuthority,
    build_document_request_host,
    recipient_identity,
    resolve_document_recipients,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.human.auth_profile import (
    Actor,
    Definition,
    DocumentPolicy,
    InputPublication,
    PublicationReceipt,
    ResourceAuthority,
    Scope,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import ArtifactPin, SourceProvenance, digest, wire
from maezo.gateway.native_fetch.transport import AdmissionLease, NativeAdmissionProvider
from maezo.runtime.worker_runtime.document_requests import TOPIC, DocumentRequestHost
from maezo.runtime.worker_runtime.service import _expected_worker_topics

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
HOUR = NOW + timedelta(hours=1)
TENANT = "tenant" * 4
ENVIRONMENT = "test" * 4
CASE = "case" * 8
REQUEST = "request" * 4
HASH = "a" * 64
ISSUER = "https://issuer.example.test"
PIN = ArtifactPin(artifact_ref="notice-template-v1", digest="b" * 64)

# Deliberately low-entropy, structurally obvious test references: these are identity handles in
# a unit fixture, never credentials.
PROVIDER_PRINCIPAL = "principal-" + "p" * 8
BENEFICIARY_PRINCIPAL = "principal-" + "b" * 8
STAFF_PRINCIPAL = "principal-" + "s" * 8


def clock() -> datetime:
    return NOW


def scope() -> Scope:
    return Scope(
        tenant=TENANT,
        environment=ENVIRONMENT,
        engine_name="engine",
        database_incarnation="db",
        installation_ref="installation",
        installation_revision=1,
    )


def provenance(until: datetime = HOUR) -> SourceProvenance:
    return SourceProvenance(
        publisher_ref="publisher",
        source_ref="source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="receipt" * 4,
        observed_at=NOW - timedelta(minutes=1),
        valid_until=until,
    )


def authority(
    principal: str,
    audience: str,
    *,
    provider_ref: str | None = "provider" * 4,
    state: str = "active",
    consent_state: str = "valid",
    legal_basis: str = "consent",
    valid_from: datetime = NOW - timedelta(minutes=5),
    valid_until: datetime = HOUR,
    request_ref: str | None = REQUEST,
    resource_ref: str = CASE,
    membership_revision: int = 1,
) -> ResourceAuthority:
    return ResourceAuthority(
        authority_ref="authority-" + principal,
        actor=Actor(
            principal_ref=principal,
            issuer=ISSUER,
            subject="subject-" + principal,
            membership_revision=membership_revision,
            audience=audience,
        ),
        beneficiary_ref="beneficiary" * 2,
        provider_ref=provider_ref,
        resource_kind="case",
        resource_ref=resource_ref,
        action=RESPOND_ACTION,
        request_ref=request_ref,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant" * 4,
        basis_ref="basis" * 4,
        legal_basis=legal_basis,
        consent_state=consent_state,
        state=state,
        valid_from=valid_from,
        valid_until=valid_until,
        source=provenance(),
    )


def entry(value: ResourceAuthority, ceiling: datetime = HOUR) -> RespondAuthority:
    return RespondAuthority(value, ceiling)


def policy(*principals: str, submitted: str | None = None) -> DocumentPolicy:
    return DocumentPolicy(
        assessment_ref="assessment" * 3,
        resource_kind="case",
        resource_ref=CASE,
        request_ref=REQUEST,
        request_revision=1,
        policy=ArtifactPin(artifact_ref="policy-v1", digest="c" * 64),
        policy_revision=1,
        recipient_principal_refs=tuple(principals),
        required_codes=("code-a",),
        missing_codes=("code-a",),
        submitted_response_digest=submitted,
        effective_document_refs=(),
        document_set_digest=digest(()),
        complete=False,
        source=provenance(),
        valid_until=HOUR,
    )


def publication(value: DocumentPolicy) -> InputPublication:
    return InputPublication(
        schema="human-auth-input-publication.v1",
        scope=scope(),
        workload_ref="workload" * 4,
        publication_id="publication" * 3,
        kind="document_policy",
        resource_ref=CASE,
        expected_generation=1,
        source=value.source,
        state="active",
        payload=value,
        payload_digest=digest(value),
        valid_until=value.valid_until,
    )


def receipt(value: DocumentPolicy) -> PublicationReceipt:
    return PublicationReceipt(
        schema="human-auth-input-receipt.v1",
        scope=scope(),
        publication_id="publication" * 3,
        request_digest=HASH,
        kind="document_policy",
        resource_ref=value.assessment_ref,
        previous_generation=1,
        head_generation=2,
        state="active",
        payload_digest=digest(value),
        committed_at=NOW,
    )


def observation(case_ref: str = CASE, request_ref: str = REQUEST) -> SimpleNamespace:
    """A `ProducerObservation`-shaped value: the source reads only `context` and `current`."""
    calls: list[str] = []
    context = SimpleNamespace(
        scope=scope(),
        case_ref=case_ref,
        request_ref=request_ref,
        definition=Definition(
            process_key="SP-OP-AUTH-001",
            definition_id="definition",
            definition_digest=HASH,
            deployment_id="deployment",
            input_profile="portal-auth-intake.v1",
            profile_digest=HASH,
        ),
    )
    return SimpleNamespace(context=context, current=lambda: calls.append("current"), calls=calls)


def authorities_double(
    entries: tuple[RespondAuthority, ...] | list[tuple[RespondAuthority, ...]],
    *,
    published: tuple[InputPublication, DocumentPolicy, datetime] | Exception | None = None,
) -> CaseRespondAuthorities:
    """A real `CaseRespondAuthorities` with only its two SQL methods replaced.

    `object.__new__` keeps the class's own behaviour (including `tenant`) and replaces exactly
    the boundary a unit test cannot cross. A list of entry tuples serves successive `read()`
    calls, so a revocation between `policy()` and `notice()` can be expressed.
    """
    value = object.__new__(CaseRespondAuthorities)
    value.reader = SimpleNamespace(binding=SimpleNamespace(scope=SimpleNamespace(tenant=TENANT)))
    sequence = list(entries) if isinstance(entries, list) else [entries]

    async def read(*, case_ref, request_ref):
        assert (case_ref, request_ref) == (CASE, REQUEST)
        return sequence[0] if len(sequence) == 1 else sequence.pop(0)

    async def publication_of(*, case_ref):
        assert case_ref == CASE
        if isinstance(published, Exception):
            raise published
        assert published is not None
        return published

    value.read = read
    value.publication = publication_of
    return value


# --- topic exclusivity ---------------------------------------------------------------------


def test_generic_harness_serves_the_topic_only_while_the_bridge_is_absent():
    with_worker = _expected_worker_topics(False)
    with_bridge = _expected_worker_topics(True)
    assert TOPIC in with_worker
    assert TOPIC not in with_bridge
    # Exactly one topic moves. A seam that also dropped a neighbour would leave a service task
    # unserved and stall every instance that reaches it.
    assert with_worker - with_bridge == {TOPIC}
    assert with_bridge - with_worker == frozenset()


def test_readiness_expectation_and_registration_cannot_disagree():
    """`/readyz` compares the live harness against this set; both sides take the same seam."""
    from maezo.tools.workers.harness import WorkerHarness

    class _Null:
        async def fetch_and_lock(self, *a, **k):  # pragma: no cover - never called
            raise AssertionError("probe transport must not be used")

    for installed in (False, True):
        harness = WorkerHarness(_Null(), worker_id="probe")
        from maezo.runtime.worker_runtime.service import register_default_workers

        register_default_workers(harness, document_request_host_installed=installed)
        assert set(harness.registered_topics) == set(_expected_worker_topics(installed))


def test_host_refuses_to_install_while_the_generic_worker_still_holds_the_topic():
    DocumentRequestHost.assert_exclusive(())
    DocumentRequestHost.assert_exclusive(("operadora.auth.analyze_request",))
    with pytest.raises(Exception):
        DocumentRequestHost.assert_exclusive((TOPIC,))
    with pytest.raises(Exception):
        DocumentRequestHost.assert_exclusive(_expected_worker_topics(False))


@pytest.mark.asyncio
async def test_builder_refuses_on_a_shared_topic_before_composing_anything(monkeypatch):
    """The refusal must precede `compose`: composing opens native channels and DB pools."""
    composed: list[object] = []

    async def never(**kwargs):
        composed.append(kwargs)
        raise AssertionError("compose must not run on a shared topic")

    monkeypatch.setattr(production_module, "compose", never)
    with pytest.raises(Exception):
        await build_document_request_host(
            placement=object(),
            producer=object(),
            tls=object(),
            fetch_profile=object(),
            designation_digest=HASH,
            fetch_authority=object(),
            outcome_authority=object(),
            completion_capability=b"{}",
            completion_catalog=b"{}",
            completion_catalog_digest=HASH,
            authorities=object(),
            notice_template=PIN,
            policy_publisher=object(),
            phi_source=object(),
            metadata_source=object(),
            body_keys={},
            active_body_key_id="k",
            body_valid_until=HOUR,
            provenance_key_id="p",
            provenance_key=b"\x00" * 32,
            journal_key_id="j",
            journal_key=b"\x01" * 32,
            generic_topics=(TOPIC,),
        )
    assert composed == []


# --- decision #18: recipients from the active resource_authority -----------------------------


def test_provider_and_beneficiary_both_become_recipients_when_both_hold_authority():
    entries = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
    )
    current = policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL)
    recipients = resolve_document_recipients(entries, scope=scope(), policy=current, now=NOW)
    assert {r.audience for r in recipients} == {"provider", "beneficiary"}
    assert {r.principal_ref for r in recipients} == {PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL}
    # The producer requires the identity digests to be sorted and unique.
    identities = [r.identity_digest for r in recipients]
    assert identities == sorted(set(identities))
    assert all(r.policy_digest == digest(current) for r in recipients)
    assert all(r.valid_until <= HOUR for r in recipients)


def test_provider_only_is_admitted_when_no_beneficiary_holds_an_authority():
    entries = (entry(authority(PROVIDER_PRINCIPAL, "provider")),)
    recipients = resolve_document_recipients(
        entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
    )
    assert [r.audience for r in recipients] == ["provider"]


def test_a_beneficiary_holding_an_authority_but_omitted_by_the_policy_is_refused():
    """The beneficiary half of decision #18: silent omission is the failure mode to prevent."""
    entries = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
    )
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
        )


def test_a_policy_naming_a_principal_with_no_active_authority_is_refused():
    entries = (entry(authority(PROVIDER_PRINCIPAL, "provider")),)
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL), now=NOW
        )


def test_a_request_with_no_provider_recipient_is_refused():
    entries = (entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),)
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(BENEFICIARY_PRINCIPAL), now=NOW
        )


def test_a_provider_audience_without_a_provider_reference_is_refused():
    entries = (entry(authority(PROVIDER_PRINCIPAL, "provider", provider_ref=None)),)
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
        )


def test_a_staff_actor_on_a_respond_authority_is_refused_not_silently_dropped():
    entries = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(STAFF_PRINCIPAL, "staff")),
    )
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL, STAFF_PRINCIPAL), now=NOW
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"consent_state": "revoked"},
        {"legal_basis": "consent", "consent_state": "not_required"},
        {"valid_until": NOW - timedelta(seconds=1)},
        {"valid_from": NOW + timedelta(minutes=1)},
    ],
    ids=["revoked-consent", "consent-basis-without-valid-consent", "expired", "not-yet-valid"],
)
def test_every_published_validity_condition_refuses_the_recipient(overrides):
    entries = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None, **overrides)),
    )
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL), now=NOW
        )


def test_an_observation_ceiling_already_passed_refuses_the_recipient():
    entries = (entry(authority(PROVIDER_PRINCIPAL, "provider"), NOW - timedelta(seconds=1)),)
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
        )


def test_two_authorities_for_one_principal_are_refused_never_preferred():
    entries = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(PROVIDER_PRINCIPAL, "provider", membership_revision=2)),
    )
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients(
            entries, scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
        )


def test_recipient_identity_separates_membership_revisions():
    first = authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)
    second = authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None, membership_revision=2)
    assert recipient_identity(scope(), first) != recipient_identity(scope(), second)


def test_recipient_valid_until_never_outlives_its_authority_or_source():
    short = authority(PROVIDER_PRINCIPAL, "provider", valid_until=NOW + timedelta(minutes=5))
    recipients = resolve_document_recipients(
        (entry(short, HOUR),), scope=scope(), policy=policy(PROVIDER_PRINCIPAL), now=NOW
    )
    assert recipients[0].valid_until == NOW + timedelta(minutes=5)


# --- the policy source: attested publication, never authored ---------------------------------


@pytest.mark.asyncio
async def test_policy_returns_the_attested_publication_byte_for_byte():
    current = policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL)
    published = publication(current)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (
                entry(authority(PROVIDER_PRINCIPAL, "provider")),
                entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
            ),
            published=(published, current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    snapshot = await source.policy(observation(), None)
    assert wire(snapshot.publication) == wire(published)
    assert snapshot.snapshot.payload == current
    assert snapshot.snapshot.source == published.source
    # `AuthInputPublisher.publish` guards the snapshot before every step; it must pass.
    snapshot.guard(NOW)


@pytest.mark.asyncio
async def test_policy_refuses_when_the_published_policy_disagrees_with_the_authorities():
    """Decision #18 is enforced before anything is published, not after delivery."""
    current = policy(PROVIDER_PRINCIPAL)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (
                entry(authority(PROVIDER_PRINCIPAL, "provider")),
                entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
            ),
            published=(publication(current), current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    with pytest.raises(ExternalCaseError):
        await source.policy(observation(), None)


@pytest.mark.asyncio
async def test_policy_refuses_when_no_document_policy_head_is_published():
    """Absence of a published head is a refusal, never an invented default policy."""
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (entry(authority(PROVIDER_PRINCIPAL, "provider")),), published=AuthUnavailableError()
        ),
        template=PIN,
        clock=clock,
    )
    with pytest.raises(AuthUnavailableError):
        await source.policy(observation(), None)


@pytest.mark.asyncio
async def test_policy_refuses_an_already_answered_request():
    current = policy(PROVIDER_PRINCIPAL, submitted=HASH)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (entry(authority(PROVIDER_PRINCIPAL, "provider")),),
            published=(publication(current), current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    with pytest.raises(ExternalCaseError):
        await source.policy(observation(), None)


@pytest.mark.asyncio
async def test_policy_refuses_a_head_bound_to_another_case_or_request():
    current = policy(PROVIDER_PRINCIPAL)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (entry(authority(PROVIDER_PRINCIPAL, "provider")),),
            published=(publication(current), current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    with pytest.raises(ExternalCaseError):
        await source.policy(observation(request_ref="other-request"), None)


@pytest.mark.asyncio
async def test_restart_resumes_the_sealed_publication_and_refuses_a_newer_head():
    current = policy(PROVIDER_PRINCIPAL)
    published = publication(current)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            [
                (entry(authority(PROVIDER_PRINCIPAL, "provider")),),
                (entry(authority(PROVIDER_PRINCIPAL, "provider")),),
            ],
            published=(published, current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    resumed = await source.policy(observation(), published)
    assert wire(resumed.publication) == wire(published)
    superseded = publication(policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL))
    with pytest.raises(ExternalCaseError):
        await source.policy(observation(), superseded)


@pytest.mark.asyncio
async def test_successor_refuses_because_no_installed_source_issues_an_invocation_authority():
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(()), template=PIN, clock=clock
    )
    assert type(source).successor is DocumentRequestPolicySource.successor
    with pytest.raises(ExternalCaseError):
        await source.successor(observation(), object(), "invocation" * 2, None)


@pytest.mark.asyncio
async def test_notice_projects_the_recipients_and_carries_no_invented_prior_command():
    current = policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (
                entry(authority(PROVIDER_PRINCIPAL, "provider")),
                entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
            )
        ),
        template=PIN,
        clock=clock,
    )
    plan = await source.notice(observation(), current, receipt(current))
    assert type(plan) is NoticePlan
    assert plan.template == PIN
    assert plan.prior_commands == ()
    # The producer's own invariant over the plan.
    identities = [r.identity_digest for r in plan.recipients]
    assert identities == sorted(set(identities))
    assert {r.principal_ref for r in plan.recipients} == set(current.recipient_principal_refs)


@pytest.mark.asyncio
async def test_notice_rereads_the_authorities_so_a_revocation_between_the_calls_refuses():
    """A delegation revoked after the policy was sealed must not receive the notice."""
    current = policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL)
    both = (
        entry(authority(PROVIDER_PRINCIPAL, "provider")),
        entry(authority(BENEFICIARY_PRINCIPAL, "beneficiary", provider_ref=None)),
    )
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            [both, (entry(authority(PROVIDER_PRINCIPAL, "provider")),)],
            published=(publication(current), current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    await source.policy(observation(), None)
    with pytest.raises(ExternalCaseError):
        await source.notice(observation(), current, receipt(current))


@pytest.mark.asyncio
async def test_notice_refuses_a_receipt_that_does_not_bind_this_policy():
    current = policy(PROVIDER_PRINCIPAL)
    other = receipt(policy(PROVIDER_PRINCIPAL, BENEFICIARY_PRINCIPAL))
    source = PublishedDocumentPolicySource(
        authorities=authorities_double((entry(authority(PROVIDER_PRINCIPAL, "provider")),)),
        template=PIN,
        clock=clock,
    )
    with pytest.raises(ExternalCaseError):
        await source.notice(observation(), current, other)


@pytest.mark.asyncio
async def test_the_source_refuses_an_observation_from_another_tenant():
    current = policy(PROVIDER_PRINCIPAL)
    source = PublishedDocumentPolicySource(
        authorities=authorities_double(
            (entry(authority(PROVIDER_PRINCIPAL, "provider")),),
            published=(publication(current), current, HOUR),
        ),
        template=PIN,
        clock=clock,
    )
    foreign = observation()
    foreign.context.scope = scope().model_copy(update={"tenant": "other-tenant"})
    with pytest.raises(ExternalCaseError):
        await source.policy(foreign, None)


def test_the_source_refuses_construction_without_a_real_authority_reader():
    with pytest.raises(AuthUnavailableError):
        PublishedDocumentPolicySource(authorities=object(), template=PIN, clock=clock)
    with pytest.raises(AuthUnavailableError):
        PublishedDocumentPolicySource(
            authorities=authorities_double(()), template="not-a-pin", clock=clock
        )


def test_the_authority_reader_refuses_a_reader_that_is_not_the_native_one():
    with pytest.raises(AuthUnavailableError):
        CaseRespondAuthorities(SimpleNamespace(engine=object(), binding=object()))


# --- the completion authority: outcome designation, never the fetch one ----------------------


def lease(**overrides) -> AdmissionLease:
    fields = dict(
        purpose="outcome",
        selection_digest=HASH,
        binding=b"binding",
        capability_digest="d" * 64,
        activation_ref="activation",
        database_incarnation="db",
        policy_digest="e" * 64,
        schema_digest="f" * 64,
        capabilities=("d" * 64,),
        not_before=NOW - timedelta(minutes=1),
        valid_until=HOUR,
        live=lambda: None,
    )
    fields.update(overrides)
    return AdmissionLease(**fields)


class Provider(NativeAdmissionProvider):
    def __init__(self, value: AdmissionLease) -> None:
        self.value, self.calls = value, []

    async def acquire(self, selection_digest, purpose, command_binding):
        self.calls.append(purpose)
        return self.value


@pytest.mark.asyncio
async def test_completion_authority_acquires_only_the_outcome_designation():
    provider = Provider(lease())
    granted = await NativeCompletionAuthority(
        provider, capability_digest="d" * 64, clock=clock
    ).acquire(HASH, b"binding", "outcome")
    assert provider.calls == ["outcome"]
    granted.guard(clock, HASH, b"binding", "outcome")


@pytest.mark.asyncio
async def test_completion_authority_refuses_the_fetch_purpose_without_touching_the_provider():
    provider = Provider(lease(purpose="fetch"))
    with pytest.raises(ExternalCaseError):
        await NativeCompletionAuthority(provider, capability_digest="d" * 64, clock=clock).acquire(
            HASH, b"binding", "fetch"
        )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_completion_authority_refuses_a_lease_for_another_capability_or_binding():
    for overrides in ({"capability_digest": "9" * 64, "capabilities": ("9" * 64,)}, {"binding": b"other"}):
        provider = Provider(lease(**overrides))
        with pytest.raises(ExternalCaseError):
            await NativeCompletionAuthority(
                provider, capability_digest="d" * 64, clock=clock
            ).acquire(HASH, b"binding", "outcome")


def test_completion_authority_refuses_a_provider_that_is_not_one():
    with pytest.raises(AuthUnavailableError):
        NativeCompletionAuthority(object(), capability_digest="d" * 64, clock=clock)


# --- PHI custody is untouched by this module -------------------------------------------------


def test_the_production_module_never_reaches_phi_body_custody():
    """Decision #18 widens the recipient set; it must not widen what the source may read.

    The recipients come from two metadata tables. The PHI body plane (`PhiRequestContent`,
    `PhiContentKeys`, `portal_communication.content`) is owned by the producer under its own
    PHI role and must not appear here at all — a policy source that could read a body would
    put document content on the authority path.
    """
    text = (production_module.__file__ and open(production_module.__file__).read()) or ""
    for forbidden in (
        "PhiRequestContent",
        "PhiContentKeys",
        "portal_communication.content",
        "phi-body",
        "RequestBodyCommand",
    ):
        assert forbidden not in text, forbidden
    # The only relations this module names at all are the two published-input tables: one
    # candidate query over the heads (the document-policy head is reached through
    # `NativeAuthReader.head`, which names its own table) and one version read.
    assert text.count("mzo_auth_input_head") == 1
    assert text.count("mzo_auth_input_version") == 1
    assert "portal_document_request." not in text


def test_the_module_refuses_rather_than_returning_an_empty_recipient_set():
    """No path yields zero recipients: `SystemGrant`/`NoticePlan` both require at least one."""
    with pytest.raises(ExternalCaseError):
        resolve_document_recipients((), scope=scope(), policy=policy(), now=NOW)
    # And the guard used throughout is the package's own fail-closed `require`.
    with pytest.raises(ExternalCaseError):
        models_require(False, "denied")
