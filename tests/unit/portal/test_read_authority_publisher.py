"""Finite source/handshake controls. No synthetic adapter qualifies production authority."""

from unittest.mock import AsyncMock

import pytest
from tests.unit.portal.test_queue_cursor_custody import setup

from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import SourceProvenance, digest, wire
from maezo.gateway.human.read_publisher import (
    PostgresMembershipPublicationSource,
    SourceFreezeLease,
)
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.engine.profile import canonicalize


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_actual_store_port_preserves_membership_tuple_and_source_deadline(revoked):
    clock, p, _, _, b = setup()
    principal = b.principal
    record = MembershipRecord(
        tenant=p.scope.tenant,
        issuer=principal.issuer,
        subject=principal.subject,
        principal_ref=principal.principal_ref,
        revision=principal.membership_revision,
        audience="staff",
        memberships=principal.memberships,
        subject_bindings=principal.subject_bindings,
        reviewed_until=p.until,
        revoked=revoked,
    )
    calls = []
    source = SourceProvenance(
        publisher_ref="publisher",
        source_ref="membership",
        source_revision=1,
        source_digest=digest(record),
        receipt_ref="upstream-committed",
        observed_at=clock(),
        valid_until=p.until,
    )

    def verify(raw):
        assert raw == canonicalize(wire(record))
        calls.append("verified")

    lease = SourceFreezeLease(
        source,
        verify,
        lambda: calls.append("live"),
        lambda _: calls.append("commit"),
        lambda: calls.append("uncertain"),
    )
    # Unit isolation of DB I/O, explicitly not PostgreSQL evidence. The concrete
    # source refuses LocalTestIdentityStore; integration exercises the real store.
    store = object.__new__(PostgresIdentityStore)
    store.tenant = p.scope.tenant
    store.get_membership = AsyncMock(return_value=record)
    handshake = AsyncMock()
    handshake.freeze.return_value = lease
    result = await PostgresMembershipPublicationSource(store=store, handshake=handshake).read(
        record.issuer, record.subject
    )
    store.get_membership.assert_awaited_once_with(record.issuer, record.subject)
    assert result.payload.memberships == record.memberships and result.source == source
    assert result.payload.state == ("revoked" if revoked else "active") and "commit" not in calls


def test_no_memory_or_browser_membership_source():
    with pytest.raises(ReadRefusalError):
        PostgresMembershipPublicationSource(store=LocalTestIdentityStore("tenant"), handshake=AsyncMock())


@pytest.mark.asyncio
async def test_lost_ack_reconciles_only_exact_publication_before_upstream_ack():
    import httpx
    from tests.unit.portal.test_read_engine_transport import client_fixture

    from maezo.gateway.human.read_profile import MembershipProjection
    from maezo.gateway.human.read_publisher import PortalReadPublisher, SourceSnapshot
    from maezo.portal.engine.profile import strict_loads

    client, anchor, _ = client_fixture()
    p = anchor.scope
    clock = client.partition.clock
    calls = []
    record = MembershipProjection(
        principal_ref="human-1",
        issuer="https://issuer.example",
        subject="subject-1",
        membership_revision=1,
        audience="staff",
        memberships=(),
        subject_bindings=(),
        state="active",
        reviewed_until=clock().replace(minute=1),
    )
    source = SourceProvenance(
        publisher_ref=p.workload_ref,
        source_ref="source-membership",
        source_revision=1,
        source_digest=digest(record),
        receipt_ref="source-committed",
        observed_at=clock(),
        valid_until=record.reviewed_until,
    )
    lease = SourceFreezeLease(
        source,
        lambda raw: None,
        lambda: None,
        lambda receipt: calls.append("upstream-ack"),
        lambda: calls.append("uncertain"),
    )
    snapshot = SourceSnapshot(source, record, lease)
    membership = AsyncMock()
    membership.read.return_value = snapshot
    bodies = []

    async def handler(request):
        outer = strict_loads(request.content)
        assert outer["purpose"] == "portal-read-publication"
        inner = outer["request"]
        bodies.append(canonicalize(inner))
        if len(bodies) == 1:
            raise httpx.ReadTimeout("PUBLIC_SYNTHETIC lost acknowledgement")
        return httpx.Response(
            200,
            content=canonicalize(
                {
                    "schema": "portal-read-publication-receipt.v1",
                    "publication_id": inner["publication_id"],
                    "request_digest": digest(inner),
                    "authority_revision": "8",
                    "kind": "membership",
                    "record_digest": digest(inner["payload"]),
                }
            ),
            headers={"content-type": "application/json", "cache-control": "no-store"},
        )

    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._peer = lambda response: None  # Explicit transport-only double; not mTLS proof.
    publisher = PortalReadPublisher(
        client=client,
        membership=membership,
        catalog=AsyncMock(),
        resource=AsyncMock(),
        pagto=AsyncMock(),
        revocation=AsyncMock(),
    )
    try:
        with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
            await publisher.publish_membership(record.issuer, record.subject, expected_revision=7)
        assert "upstream-ack" not in calls and publisher._pending is not None
        receipt = await publisher.reconcile()
        assert receipt.authority_revision == 8 and bodies[0] == bodies[1]
        assert calls[-1] == "upstream-ack" and publisher._pending is None
        membership.read.assert_awaited_once()
    finally:
        await client.close()
