"""Synthetic source barrier proofs exercise real publisher control flow only."""

from datetime import timedelta

import pytest
from tests.unit.gateway.intake.native.test_wire_transport import NOW, command, scope, source

from maezo.gateway.human.auth_profile import (
    InputPublication,
    PublicationLookup,
    PublicationQuery,
    PublicationReceipt,
)
from maezo.gateway.human.auth_publisher import AuthInputPublisher, AuthPublicationSnapshot
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.human.read_publisher import SourceFreezeLease, SourceSnapshot


class Journal:
    def __init__(self):
        self.events = []
        self.saved = None

    async def freeze(self, p):
        self.events.append("freeze")
        return self.saved

    async def acknowledge(self, p, r):
        self.events.append("persist-ack")
        self.saved = r


class Client:
    def __init__(self):
        self.sent = []
        self.fail = False
        self.expire = False
        self.live = True

    async def execute(self, request, **kwargs):
        self.sent.append(request)
        if self.fail:
            raise AuthUnavailableError()
        if isinstance(request, PublicationQuery):
            return PublicationLookup(
                schema="human-auth-publication-lookup.v1",
                scope=request.scope,
                query_id=request.query_id,
                query_digest=digest(request),
                status="absent",
                receipt=None,
                observed_at=NOW,
            )
        if self.expire:
            self.live = False
        return PublicationReceipt(
            schema="human-auth-input-receipt.v1",
            scope=request.scope,
            publication_id=request.publication_id,
            request_digest=digest(request),
            kind=request.kind,
            resource_ref=request.resource_ref,
            previous_generation=request.expected_generation,
            head_generation=request.expected_generation + 1,
            state=request.state,
            payload_digest=request.payload_digest,
            committed_at=NOW,
        )


def setup():
    j, c, events = Journal(), Client(), []

    def live():
        if not c.live:
            raise AuthUnavailableError()

    lease = SourceFreezeLease(
        source(),
        lambda raw: None,
        live,
        lambda r: events.append("WRONG-Q2-ACK"),
        lambda: events.append("uncertain"),
    )
    publication = InputPublication(
        schema="human-auth-input-publication.v1",
        scope=scope(),
        workload_ref="publisher",
        publication_id="publication",
        kind="guide",
        resource_ref="guide",
        expected_generation=0,
        source=source(),
        state="frozen",
        payload=None,
        payload_digest=None,
        valid_until=NOW + timedelta(seconds=30),
    )
    snap = AuthPublicationSnapshot(
        publication, SourceSnapshot(source(), command(), lease), lambda r: events.append("source-ack")
    )
    return j, c, events, snap, AuthInputPublisher(client=c, journal=j, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_exact_publication_queries_before_send_and_acks_after_persist():
    j, c, e, s, p = setup()
    first = await p.publish(s)
    second = await p.publish(s)
    assert first == second and len(c.sent) == 2
    assert isinstance(c.sent[0], PublicationQuery) and c.sent[1] == s.publication
    assert j.events == ["freeze", "persist-ack", "freeze"] and e == ["source-ack", "source-ack"]


@pytest.mark.asyncio
async def test_uncertain_publication_never_unfreezes_source():
    j, c, e, s, p = setup()
    c.fail = True
    with pytest.raises(AuthUnavailableError):
        await p.publish(s)
    assert e == ["uncertain"] and j.saved is None and len(c.sent) == 1


@pytest.mark.asyncio
async def test_expired_source_after_native_ack_remains_frozen():
    j, c, e, s, p = setup()
    c.expire = True
    with pytest.raises(AuthUnavailableError):
        await p.publish(s)
    assert e == ["uncertain"] and j.saved is not None
