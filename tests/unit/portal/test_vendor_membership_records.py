"""VW1-P0 spine: the vendor branch of `_require_authority` and the default-off capability fence.

Positive and negative halves of the symmetric homogeneity branch, the neighbor audiences kept
green, and the proof that a DEFAULT deployment can never project a `vendor` capability (the
config literal is untouched; no session is born vendor).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from tests.unit.portal.test_human_session import (
    ISSUER,
    ORIGIN,
    PREFIX,
    SUBJECT,
    Harness,
    SignedTestIdP,
    config,
    membership,
)

from maezo.portal.api.records import MembershipRecord, SessionDTO
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

NOW = datetime.now(UTC)


def vendor_record(**changes: Any) -> MembershipRecord:
    return MembershipRecord(
        **dict(
            tenant="test-tenant",
            issuer=ISSUER,
            subject=SUBJECT,
            principal_ref="vendor-principal",
            revision=1,
            audience="vendor",
            memberships=(
                MembershipBinding(membership_ref="vendor-binding", roles=("vendor_portal",), groups=()),
            ),
            subject_bindings=(SubjectBinding(kind="vendor", resource_ref="channel-a"),),
            reviewed_until=NOW + timedelta(hours=2),
            revoked=False,
        )
        | changes
    )


def test_vendor_record_with_vendor_bindings_is_valid() -> None:
    record = vendor_record()
    assert record.audience == "vendor"
    assert all(b.kind == "vendor" for b in record.subject_bindings)


def test_vendor_audience_refuses_an_incompatible_subject_relationship() -> None:
    # A vendor record with NO vendor binding never passes the verified-relationship branch.
    for stray_kind in ("beneficiary", "provider"):
        with pytest.raises(ValidationError, match="verified subject relationship required"):
            vendor_record(subject_bindings=(SubjectBinding(kind=stray_kind, resource_ref="x"),))
    # The vendor homogeneity branch refuses any MIXED set: one vendor binding is not a licence.
    for stray_kind in ("beneficiary", "provider"):
        with pytest.raises(ValidationError, match="incompatible subject relationship"):
            vendor_record(
                subject_bindings=(
                    SubjectBinding(kind="vendor", resource_ref="channel-a"),
                    SubjectBinding(kind=stray_kind, resource_ref="x"),
                )
            )


def external_record(audience: str, kind: str, resource_ref: str = "x") -> MembershipRecord:
    return membership().model_copy(
        update={
            "audience": audience,
            "subject_bindings": (SubjectBinding(kind=kind, resource_ref=resource_ref),),
        }
    )


def test_neighbor_audiences_keep_their_own_branches_green() -> None:
    assert membership().audience == "staff"  # staff: no subject binding required
    assert external_record("beneficiary", "beneficiary", "b-1").audience == "beneficiary"
    assert external_record("provider", "provider", "p-1").audience == "provider"
    # The existing negative halves still refuse: a mixed set against a homogeneous audience.
    for audience, own, stray in (
        ("beneficiary", "beneficiary", "provider"),
        ("provider", "provider", "beneficiary"),
        ("vendor", "vendor", "beneficiary"),
        ("vendor", "vendor", "provider"),
    ):
        fields = external_record(audience, own, "own-ref").model_dump(mode="python")
        fields["subject_bindings"] = (
            SubjectBinding(kind=own, resource_ref="own-ref"),
            SubjectBinding(kind=stray, resource_ref="stray-ref"),
        )
        with pytest.raises(ValidationError, match="incompatible subject relationship"):
            MembershipRecord(**fields)


def test_session_dto_type_carries_the_fourth_capability_and_audience() -> None:
    dto = SessionDTO(
        schema_version=1,
        principal_ref="vendor-principal",
        audience="vendor",
        roles=("vendor_portal",),
        expires_at=NOW + timedelta(minutes=5),
        csrf_token="s3cret",
        capabilities=("identity", "vendor"),
    )
    assert dto.capabilities == ("identity", "vendor")
    with pytest.raises(ValidationError):
        SessionDTO(
            schema_version=1,
            principal_ref="p",
            audience="vendor",
            roles=(),
            expires_at=NOW + timedelta(minutes=5),
            csrf_token="s3cret",
            capabilities=("identity", "phi"),  # nothing outside the closed set, widened or not
        )


async def test_default_config_never_projects_a_vendor_capability() -> None:
    """Flags default-off: the default settings literal has no `vendor`, so no session is born
    vendor — even for a vendor record, the projection only ever carries configured values."""
    from maezo.portal.api.app import create_app

    assert config().capabilities == "identity"
    store = LocalTestIdentityStore("test-tenant")
    store.memberships[(ISSUER, SUBJECT)] = vendor_record(subject=SUBJECT, principal_ref="human-internal-1")
    idp = SignedTestIdP()
    oidc = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
    app = create_app(config(), store=store, oidc_client=oidc)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            harness = Harness(client, store, idp, app)
            assert (await harness.login()).status_code == 303
            data = (await client.get(PREFIX + "/session")).json()
    finally:
        await oidc.aclose()
    assert data["audience"] == "vendor"  # the record IS vendor; the capability is NOT
    assert data["capabilities"] == ["identity"]
    assert "vendor" not in data["capabilities"]
