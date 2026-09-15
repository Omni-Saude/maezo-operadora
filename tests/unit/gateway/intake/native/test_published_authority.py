"""`PublishedIntakeAuthority` — the production IntakeAuthority. Absence of a row is a refusal.

The head read itself needs PostgreSQL and belongs to the engine lane; it is replaced here by a
snapshot of already-verified rows, exactly the shape `NativeAuthReader.published_authorities`
returns. What is under test is the part that decides nothing and refuses everything unproven.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.human.auth_profile import (
    Actor,
    DocumentCustody,
    DocumentRef,
    ResourceAuthority,
    Scope,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import SourceProvenance, digest
from maezo.gateway.intake.links import SYNTHETIC_PUBLISHER_REF, IntakeLinkSource, actor_matches
from maezo.gateway.intake.models import IntakeError, request_bytes
from maezo.gateway.intake.native_authority import (
    NativeAuthReader,
    NativeDatabaseBinding,
    PublishedAuthorities,
)
from maezo.gateway.intake.native_source_lifecycle import RelationPin
from maezo.gateway.intake.published_authority import PublishedIntakeAuthority
from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding, SubjectBinding

pytestmark = pytest.mark.asyncio

HASH = "a" * 64
TENANT = "tenant-published"
PRINCIPAL = "human-published-1"
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
SUBJECT = "00000000-0000-4000-8000-000000000001"
BENEFICIARY = "beneficiary_published_00001"
PROVIDER = "provider_published_000001"
GUIDE = "guide_published_0000000001"
INTAKE = "intake_published_000000001"
DOCUMENT = "document_published_0000001"


def now() -> datetime:
    return datetime.now(UTC)


def scope() -> Scope:
    return Scope(
        tenant=TENANT,
        environment="dev",
        engine_name="engine",
        database_incarnation="db",
        installation_ref="installed",
        installation_revision=1,
    )


def binding() -> NativeDatabaseBinding:
    names = ("installation", "trust", "revoked_key", "input_head", "input_version")
    return NativeDatabaseBinding(
        scope=scope(),
        database_name="native",
        database_oid=1,
        schema_name="mzo_auth",
        schema_oid=2,
        owner_role="mzo_owner",
        reader_role="mzo_reader",
        relations=tuple(
            RelationPin(schema_name="mzo_auth", name="mzo_auth_" + n, oid=index + 10, owner="mzo_owner")
            for index, n in enumerate(names)
        ),
        installed_binding_digest=HASH,
        installed_qualification_digest=HASH,
        valid_until=now() + timedelta(days=1),
    )


def reader() -> NativeAuthReader:
    # Lazily constructed: no connection is opened and no query is issued in this module.
    return NativeAuthReader(create_async_engine("postgresql+asyncpg://u:p@h/native"), binding())


def principal(**changes: Any) -> HumanPrincipal:
    values: dict[str, Any] = dict(
        schema_version=1,
        principal_ref=PRINCIPAL,
        issuer=ISSUER,
        subject=SUBJECT,
        tenant=TENANT,
        membership_revision=1,
        memberships=(MembershipBinding(membership_ref="m-1", roles=("provider",), groups=()),),
        session_ref="session-1",
        authenticated_at=now() - timedelta(minutes=1),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),),
    )
    values.update(changes)
    return HumanPrincipal(**values)


def provenance(**changes: Any) -> SourceProvenance:
    values: dict[str, Any] = dict(
        publisher_ref=SYNTHETIC_PUBLISHER_REF,
        source_ref="synthetic-fixture-source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="synthetic-receipt-000000001",
        observed_at=now() - timedelta(minutes=5),
        valid_until=now() + timedelta(hours=2),
    )
    values.update(changes)
    return SourceProvenance(**values)


def authority(**changes: Any) -> ResourceAuthority:
    values: dict[str, Any] = dict(
        authority_ref="authority-1",
        actor=Actor(
            principal_ref=PRINCIPAL,
            issuer=ISSUER,
            subject=SUBJECT,
            membership_revision=1,
            audience="provider",
        ),
        beneficiary_ref=BENEFICIARY,
        provider_ref=PROVIDER,
        resource_kind="guide",
        resource_ref=GUIDE,
        action="auth.start",
        request_ref=None,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant-1",
        basis_ref="basis-1",
        legal_basis="other_qualified_basis",
        consent_state="not_required",
        state="active",
        valid_from=now() - timedelta(hours=1),
        valid_until=now() + timedelta(hours=1),
        source=provenance(),
    )
    values.update(changes)
    return ResourceAuthority(**values)


def custody(**changes: Any) -> DocumentCustody:
    values: dict[str, Any] = dict(
        document=DocumentRef(
            document_ref=DOCUMENT,
            custody_revision=1,
            content_sha256=HASH,
            storage_version_ref="storage-1",
            policy_digest=HASH,
        ),
        resource_kind="intake",
        resource_ref=INTAKE,
        creator_principal_ref=PRINCIPAL,
        screening_ref="screening-1",
        screening_revision=1,
        screening_result="clean",
        custody_state="available",
        key_custody_ref="key-1",
        key_custody_revision=1,
        valid_until=now() + timedelta(hours=1),
    )
    values.update(changes)
    return DocumentCustody(**values)


def submission(**changes: Any) -> AuthIntakeSubmission:
    values: dict[str, Any] = dict(
        command_id="command_published_00000001",
        beneficiary_ref=BENEFICIARY,
        provider_ref=PROVIDER,
        guide_ref=GUIDE,
        codigo_procedimento_tuss="10101012",
        categoria_procedimento="consulta",
        carater_atendimento="eletivo",
        valor_estimado_centavos="12345",
        document_refs=(),
    )
    values.update(changes)
    return AuthIntakeSubmission(**values)


def install(
    monkeypatch: pytest.MonkeyPatch,
    authorities: tuple[ResourceAuthority, ...] = (),
    custodies: tuple[DocumentCustody, ...] = (),
    *,
    ceiling: datetime | None = None,
    fails: bool = False,
) -> list[tuple[str, str, tuple[str, ...]]]:
    calls: list[tuple[str, str, tuple[str, ...]]] = []
    deadline = ceiling or (now() + timedelta(hours=3))

    async def published(
        self: NativeAuthReader, principal_ref: str, *, action: str, documents: tuple[str, ...] = ()
    ) -> PublishedAuthorities:
        calls.append((principal_ref, action, documents))
        if fails:
            raise AuthUnavailableError()
        return PublishedAuthorities(
            tuple((a, deadline) for a in authorities if a.action == action),
            tuple((c, deadline) for c in custodies),
            deadline,
        )

    monkeypatch.setattr(NativeAuthReader, "published_authorities", published)
    return calls


async def test_admit_refuses_when_no_authority_is_published(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install(monkeypatch)
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())
    assert refusal.value.code == "operation_forbidden"
    assert calls == [(PRINCIPAL, "auth.start", ())]


async def test_admit_grants_only_on_an_exactly_matching_published_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = authority()
    install(monkeypatch, (published,))
    request, caller = submission(), principal()
    grant = await PublishedIntakeAuthority(reader()).admit(caller, request)
    assert grant.request_digest == hashlib.sha256(request_bytes(request)).hexdigest()
    assert grant.guide_identity_ref == GUIDE
    assert grant.authority_receipt_ref == published.source.receipt_ref
    assert grant.authority_digest == digest(published)
    assert grant.principal == caller
    assert grant.valid_until > now()


@pytest.mark.parametrize(
    "change",
    [
        {"resource_kind": "intake"},
        {"resource_ref": "guide_published_0000000009"},
        {"beneficiary_ref": "beneficiary_published_00009"},
        {"provider_ref": "provider_published_000009"},
        {"provider_ref": None},
        {"state": "revoked"},
        {"consent_state": "revoked"},
        {"legal_basis": "consent", "consent_state": "not_required"},
        {"action": "auth.receipt.read"},
    ],
)
async def test_admit_refuses_every_authority_that_does_not_cover_the_request(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    install(monkeypatch, (authority(**change),))
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())
    assert refusal.value.code == "operation_forbidden"


@pytest.mark.parametrize(
    "change",
    [
        {"principal_ref": "human-published-9"},
        {"issuer": "https://other.example.test"},
        {"subject": "00000000-0000-4000-8000-000000000009"},
        {"membership_revision": 2},
    ],
)
async def test_admit_refuses_an_authority_naming_another_identity(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    actor = authority().actor.model_copy(update=change)
    install(monkeypatch, (authority(actor=actor),))
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())


async def test_admit_refuses_an_authority_naming_a_staff_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    # Staff hold no vínculo anywhere in this plane (ADR-0049 D4): a row that otherwise
    # names this exact principal, issuer, subject and membership revision but labels the
    # actor `staff` is not a submit grant either. `_covers` and `project_links` share one
    # `actor_matches` predicate (`links.py`) so this floor cannot drift between them.
    staff = authority().actor.model_copy(update={"audience": "staff"})
    install(monkeypatch, (authority(actor=staff),))
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())
    assert refusal.value.code == "operation_forbidden"


async def test_admit_refuses_an_expired_window_or_an_expired_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, (authority(valid_until=now() - timedelta(seconds=1)),))
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())
    install(monkeypatch, (authority(),), ceiling=now() - timedelta(seconds=1))
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())


async def test_admit_refuses_two_authorities_over_one_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, (authority(), authority(authority_ref="authority-2")))
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).admit(principal(), submission())
    assert refusal.value.code == "operation_forbidden"


async def test_admit_refuses_a_selected_document_without_clean_available_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = submission(document_refs=(DOCUMENT,))
    install(monkeypatch, (authority(),))
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).admit(principal(), request)
    for change in (
        {"custody_state": "revoked"},
        {"custody_state": "deleted"},
        {"screening_result": "quarantined"},
        {"screening_result": "pending"},
        {"valid_until": now() - timedelta(seconds=1)},
    ):
        install(monkeypatch, (authority(),), (custody(**change),))
        with pytest.raises(IntakeError):
            await PublishedIntakeAuthority(reader()).admit(principal(), request)
    install(monkeypatch, (authority(),), (custody(),))
    grant = await PublishedIntakeAuthority(reader()).admit(principal(), request)
    assert grant.valid_until <= custody().valid_until


async def test_admit_passes_the_selected_documents_to_the_head_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch, (authority(),), (custody(),))
    await PublishedIntakeAuthority(reader()).admit(principal(), submission(document_refs=(DOCUMENT,)))
    assert calls == [(PRINCIPAL, "auth.start", (DOCUMENT,))]


async def test_another_tenant_is_out_of_scope_not_weakly_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch, (authority(),))
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).admit(principal(tenant="other-tenant"), submission())
    assert refusal.value.code == "operation_forbidden"
    # The refusal precedes the read entirely: no cross-tenant query is ever issued.
    assert calls == []


async def test_an_unavailable_head_read_is_a_dependency_never_a_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, (authority(),), fails=True)
    for call in (
        PublishedIntakeAuthority(reader()).admit(principal(), submission()),
        PublishedIntakeAuthority(reader()).read(principal(), INTAKE),
    ):
        with pytest.raises(IntakeError) as refusal:
            await call
        assert refusal.value.code == "dependency_unavailable"


async def test_read_requires_a_published_read_authority_over_that_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch)
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).read(principal(), INTAKE)
    assert calls == [(PRINCIPAL, "auth.receipt.read", ())]
    # A start authority is not a read authority, even over the same resource.
    install(monkeypatch, (authority(resource_kind="intake", resource_ref=INTAKE),))
    with pytest.raises(IntakeError):
        await PublishedIntakeAuthority(reader()).read(principal(), INTAKE)
    readable = authority(action="auth.receipt.read", resource_kind="intake", resource_ref=INTAKE)
    install(monkeypatch, (readable,))
    until = await PublishedIntakeAuthority(reader()).read(principal(), INTAKE)
    assert now() < until <= readable.valid_until


async def test_read_refuses_a_read_authority_naming_a_staff_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before this fix a `read`-authorised row naming a staff actor was honoured: `_covers`
    # compared identity but never `actor.audience`, unlike `project_links`, which can never
    # even receive `audience="staff"` to compare against (`links` is refused for staff
    # sessions upstream). One `actor_matches` predicate now floors both at "not staff".
    readable = authority(action="auth.receipt.read", resource_kind="intake", resource_ref=INTAKE)
    staff = readable.actor.model_copy(update={"audience": "staff"})
    install(monkeypatch, (readable.model_copy(update={"actor": staff}),))
    with pytest.raises(IntakeError) as refusal:
        await PublishedIntakeAuthority(reader()).read(principal(), INTAKE)
    assert refusal.value.code == "operation_forbidden"


async def test_links_are_audience_scoped_and_read_the_start_authorities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch, (authority(),))
    result = await PublishedIntakeAuthority(reader()).links(principal(), "provider")
    assert [link.resource_ref for link in result.links] == [GUIDE]
    assert result.links[0].provenance_kind == "synthetic"
    assert calls == [(PRINCIPAL, "auth.start", ())]
    # The same published row is not a beneficiary's link.
    install(monkeypatch, (authority(),))
    beneficiary = principal(subject_bindings=(SubjectBinding(kind="beneficiary", resource_ref=BENEFICIARY),))
    assert (await PublishedIntakeAuthority(reader()).links(beneficiary, "beneficiary")).links == ()


async def test_the_class_satisfies_both_protocols_and_refuses_a_fake_reader() -> None:
    assert isinstance(PublishedIntakeAuthority(reader()), IntakeLinkSource)
    for fake in (None, object(), "reader"):
        with pytest.raises(AuthUnavailableError):
            PublishedIntakeAuthority(fake)  # type: ignore[arg-type]


async def test_actor_matches_is_the_single_identity_and_audience_predicate() -> None:
    """The exact function `_covers` and `project_links` both call — proven at each branch
    so the two call sites can never silently diverge again."""
    p = principal()
    matching = authority().actor
    assert actor_matches(matching, p, "provider")
    assert actor_matches(matching, p, None)
    # A caller-supplied audience must equal the actor's exactly.
    assert not actor_matches(matching, p, "beneficiary")
    # No caller audience (admit/read): the floor is "not staff", any real audience passes.
    beneficiary_actor = matching.model_copy(update={"audience": "beneficiary"})
    assert actor_matches(beneficiary_actor, p, None)
    # A staff-labelled actor is refused whether or not a caller audience is supplied —
    # `links` structurally never passes "staff", and admit/read must floor it too.
    staff_actor = matching.model_copy(update={"audience": "staff"})
    assert not actor_matches(staff_actor, p, None)
    # Identity mismatch refuses regardless of audience.
    foreign = matching.model_copy(update={"principal_ref": "human-published-9"})
    assert not actor_matches(foreign, p, "provider")
    assert not actor_matches(foreign, p, None)
