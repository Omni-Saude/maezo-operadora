"""VW1-P0 vendor publication job unit: fail-closed source, durable ledger, session creatability.

No live PostgreSQL, engine or Cognito is claimed here. The access plane is a fake that
materializes the canonical payload into the local test identity store, so the session
creatability assertions run through the REAL `HumanSessionResolver` gate (revoked ⇒ refused).
"""

import json
import stat
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.human.vendor_membership_publication_job import (
    RefusingVendorDeliverySource,
    VendorJobResult,
    VendorLedgerEntry,
    VendorMembershipAccessPayload,
    VendorMembershipPublicationJob,
    VendorPublicationLedger,
    access_payload,
    payload_digest,
)
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
NOW = datetime.now(UTC)


def config() -> PortalSettings:
    return PortalSettings(
        tenant="test-tenant",
        issuer=ISSUER,
        cognito_origin="https://humans.auth.sa-east-1.amazoncognito.com",
        client_id="human123",
        machine_client_id="machine123",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://portal.example.test",
        mode="local-test",
    )


def record(**changes: Any) -> MembershipRecord:
    return MembershipRecord(
        **dict(
            tenant="test-tenant",
            issuer=ISSUER,
            subject="idp-vendor-subject",
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


def channel_row(**changes: Any) -> Any:
    from maezo.gateway.human.vendor_membership_administration import VendorChannelState

    return VendorChannelState(
        **dict(
            tenant="test-tenant",
            channel_ref="channel-a",
            revision=0,
            status="active",
            updated_at=NOW,
        )
        | changes
    )


class UnitChannels:
    def __init__(self, rows: tuple[Any, ...] | None) -> None:
        self.rows = rows

    async def channels(self, tenant: str) -> tuple[Any, ...] | None:
        return self.rows


class AccessPlane:
    """The access side: decodes the wire projection and materializes the record; the outcome is
    recorded by the resolver assertions below, never claimed by the fake itself."""

    def __init__(self) -> None:
        self.store = LocalTestIdentityStore("test-tenant")
        self.payloads: list[bytes] = []

    async def publish(self, payload: bytes) -> None:
        projection = parse_model(VendorMembershipAccessPayload, json.loads(payload))
        published = MembershipRecord(
            tenant=projection.tenant,
            issuer=projection.issuer,
            subject=projection.subject,
            principal_ref=projection.principal_ref,
            revision=int(projection.membership_revision),
            audience=projection.audience,
            memberships=projection.memberships,
            subject_bindings=projection.subject_bindings,
            revoked=projection.state == "revoked",
            reviewed_until=projection.reviewed_until,
        )
        self.payloads.append(payload)
        self.store.memberships[(published.issuer, published.subject)] = published


def job(
    channels: UnitChannels, plane: AccessPlane, ledger: VendorPublicationLedger, current: Any
) -> VendorMembershipPublicationJob:
    class Reader:
        def __init__(self, current: Any) -> None:
            self.current = current

        async def membership(self, tenant: str, channel_ref: str) -> MembershipRecord | None:
            return self.current

    return VendorMembershipPublicationJob(
        channels=channels, memberships=Reader(current), publisher=plane, ledger=ledger
    )


async def test_publishes_the_vendor_membership_once_and_is_idempotent(tmp_path: Any) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    result = await job(UnitChannels((channel_row(),)), plane, ledger, record()).run()
    assert result == VendorJobResult(published=1, unchanged=0)
    assert len(plane.payloads) == 1
    # The canonical payload is a faithful record encoding: the access plane re-reads it.
    projection = parse_model(VendorMembershipAccessPayload, json.loads(plane.payloads[0]))
    assert projection.audience == "vendor" and projection.state == "active"
    second = await job(UnitChannels((channel_row(),)), plane, ledger, record()).run()
    assert second == VendorJobResult(published=0, unchanged=1)
    assert len(plane.payloads) == 1  # same content: nothing republished


async def test_publishes_revoke_payload_and_the_session_becomes_uncreatable(tmp_path: Any) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    revoked = record(revision=2, revoked=True)
    result = await job(UnitChannels((channel_row(),)), plane, ledger, revoked).run()
    assert result == VendorJobResult(published=1, unchanged=0)
    on_wire = json.loads(plane.payloads[-1])
    assert on_wire["state"] == "revoked" and on_wire["membership_revision"] == "2"
    resolver = HumanSessionResolver(config(), plane.store)
    # Revoke-before-change semantics: the revoked record IS the newest published state.
    with pytest.raises(AuthenticationError):
        await resolver.membership(ISSUER, revoked.subject)


async def test_session_is_creatable_after_publication_and_refused_after_revoke(tmp_path: Any) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    active = record()
    await job(UnitChannels((channel_row(),)), plane, ledger, active).run()
    resolver = HumanSessionResolver(config(), plane.store)
    resolved = await resolver.membership(ISSUER, active.subject)
    assert resolved.audience == "vendor" and resolved.revoked is False
    revoked = active.model_copy(update={"revision": 2, "revoked": True})
    await job(UnitChannels((channel_row(revision=1),)), plane, ledger, revoked).run()
    with pytest.raises(AuthenticationError):
        await resolver.membership(ISSUER, active.subject)


@pytest.mark.parametrize("rows", [None, ()])
async def test_absent_or_empty_store_is_unknown_never_zero(rows: Any, tmp_path: Any) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    with pytest.raises(ReadRefusalError):
        await job(UnitChannels(rows), plane, ledger, record()).run()
    assert plane.payloads == []


async def test_unknown_membership_for_a_listed_channel_refuses_and_never_publishes_a_deletion(
    tmp_path: Any,
) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    with pytest.raises(ReadRefusalError):
        await job(UnitChannels((channel_row(),)), plane, ledger, None).run()
    assert plane.payloads == []
    assert ledger.state.entries == ()


async def test_stray_audience_on_a_channel_row_refuses(tmp_path: Any) -> None:
    plane, ledger = AccessPlane(), VendorPublicationLedger(tmp_path / "ledger.json", "test-tenant")
    stray = record(audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref="p"),))
    with pytest.raises(ReadRefusalError):
        await job(UnitChannels((channel_row(),)), plane, ledger, stray).run()
    assert plane.payloads == []


def test_ledger_is_durable_private_and_tenant_bound(tmp_path: Any) -> None:
    path = tmp_path / "ledger.json"
    ledger = VendorPublicationLedger(path, "test-tenant")
    entry = VendorLedgerEntry(
        kind="membership",
        channel_ref="channel-a",
        principal_ref="vendor-principal",
        membership_revision=1,
        payload_digest=payload_digest(record()),
        revoked=False,
    )
    ledger.record(entry)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, mode
    on_disk = json.loads(path.read_bytes())
    assert on_disk["schema"] == "vendor-membership-publication-ledger.v1"
    assert on_disk["tenant"] == "test-tenant"
    reloaded = VendorPublicationLedger(path, "test-tenant")
    assert reloaded.has("channel-a", "vendor-principal", 1, payload_digest(record()), False)
    with pytest.raises(ReadRefusalError):
        VendorPublicationLedger(path, "other-tenant")  # a ledger of another tenant is not yours
    path.write_bytes(b"{not json")
    with pytest.raises(ReadRefusalError):
        VendorPublicationLedger(path, "test-tenant")  # unreadable ledger = unknown, never empty


def test_access_payload_is_canonical_and_content_addressed() -> None:
    first, again = record(), record()
    assert access_payload(first) == access_payload(again)
    assert payload_digest(first) == payload_digest(again)
    changed = record(revision=2)
    assert payload_digest(first) != payload_digest(changed)


async def test_refusing_delivery_source_never_answers(tmp_path: Any) -> None:
    with pytest.raises(ReadRefusalError):
        await RefusingVendorDeliverySource().read("channel-a")
