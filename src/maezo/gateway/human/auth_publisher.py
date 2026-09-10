"""Durable source-kind publication journal and authenticated acknowledgement.

Trace: approved E04 native contract 4.2. Source barrier must remain frozen on
uncertainty; publication transport success alone cannot release source issuance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.portal.engine.profile import canonicalize

from .auth_profile import InputPublication, PublicationLookup, PublicationQuery, PublicationReceipt
from .auth_transport import AuthNativeClient, AuthUnavailableError, bind_result
from .read_profile import digest, parse_model, wire
from .read_publisher import SourceSnapshot


@dataclass(frozen=True, slots=True, repr=False)
class AuthPublicationSnapshot:
    """Qualified source snapshot, existing freeze lease, and Auth-specific ack barrier.

    The legacy Q2 committed callback is deliberately not called with a fabricated Q2
    receipt. The source adapter supplies its real AUTH publication acknowledgement.
    """

    publication: InputPublication = field(repr=False)
    snapshot: SourceSnapshot = field(repr=False)
    acknowledge: Callable[[PublicationReceipt], None] = field(repr=False)

    def guard(self, now: datetime) -> None:
        p, s = self.publication, self.snapshot
        s.lease.live()
        if (
            p.source != s.source
            or s.source != s.lease.provenance
            or now >= min(p.valid_until, s.source.valid_until)
        ):
            raise AuthUnavailableError()
        if p.state == "active" and (
            wire(p.payload) != wire(s.payload) or p.payload_digest != digest(s.payload)
        ):
            raise AuthUnavailableError()


class PostgresAuthPublicationJournal:
    def __init__(self, protected_store: PostgresAuthDispatchStore) -> None:
        self.store = protected_store

    async def freeze(self, publication: InputPublication) -> PublicationReceipt | None:
        s = self.store
        if publication.scope.tenant != s.tenant:
            raise AuthUnavailableError()
        nonce, ciphertext = s.seal("publication", publication.publication_id, publication)
        result = None
        try:
            async with transaction(s.engine, s.seconds) as c:
                key = canonicalize(
                    [s.tenant, "publication", publication.kind, publication.resource_ref]
                ).decode()
                await c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key})
                row = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM portal_intake.native_publication WHERE tenant=:tenant "
                                "AND publication_id=:publication"
                            ),
                            dict(tenant=s.tenant, publication=publication.publication_id),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is not None:
                    if row["request_digest"] != digest(publication) or s.unseal(
                        "publication",
                        publication.publication_id,
                        row["key_id"],
                        row["nonce"],
                        row["ciphertext"],
                    ) != wire(publication):
                        raise ExternalCaseError("conflict")
                    if row["state"] == "acknowledged":
                        result = parse_model(
                            PublicationReceipt,
                            s.unseal(
                                "publication-receipt",
                                publication.publication_id,
                                row["key_id"],
                                row["receipt_nonce"],
                                row["receipt_ciphertext"],
                            ),
                        )
                        if digest(result) != row["receipt_digest"]:
                            raise ExternalCaseError("conflict")
                else:
                    pending = (
                        await c.execute(
                            text(
                                "SELECT publication_id FROM portal_intake.native_publication WHERE "
                                "tenant=:tenant AND kind=:kind AND resource_ref=:resource AND "
                                "state='frozen'"
                            ),
                            dict(tenant=s.tenant, kind=publication.kind, resource=publication.resource_ref),
                        )
                    ).first()
                    if pending is not None:
                        raise ExternalCaseError("conflict")
                    await c.execute(
                        text(
                            "INSERT INTO "
                            "portal_intake.native_publication(tenant,publication_id,kind,resource_ref"
                            ",request_digest,key_id,nonce,ciphertext,state) VALUES(:tenant,:publicati"
                            "on,:kind,:resource,:digest,:key,:nonce,:ciphertext,'frozen')"
                        ),
                        dict(
                            tenant=s.tenant,
                            publication=publication.publication_id,
                            kind=publication.kind,
                            resource=publication.resource_ref,
                            digest=digest(publication),
                            key=s.key_id,
                            nonce=nonce,
                            ciphertext=ciphertext,
                        ),
                    )
            return result
        except Exception:
            raise AuthUnavailableError() from None

    async def acknowledge(self, publication: InputPublication, receipt: PublicationReceipt) -> None:
        s = self.store
        bind_result(receipt, publication, s.clock())
        nonce, ciphertext = s.seal("publication-receipt", publication.publication_id, receipt)
        try:
            async with transaction(s.engine, s.seconds) as c:
                row = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM portal_intake.native_publication WHERE tenant=:tenant "
                                "AND publication_id=:publication FOR UPDATE"
                            ),
                            dict(tenant=s.tenant, publication=publication.publication_id),
                        )
                    )
                    .mappings()
                    .one()
                )
                if row["request_digest"] != digest(publication) or (
                    row["receipt_digest"] is not None and row["receipt_digest"] != digest(receipt)
                ):
                    raise ExternalCaseError("conflict")
                await c.execute(
                    text(
                        "UPDATE portal_intake.native_publication SET "
                        "state='acknowledged',receipt_digest=:digest,receipt_nonce=:nonce,receipt_cip"
                        "hertext=:ciphertext WHERE tenant=:tenant AND publication_id=:publication"
                    ),
                    dict(
                        tenant=s.tenant,
                        publication=publication.publication_id,
                        digest=digest(receipt),
                        nonce=nonce,
                        ciphertext=ciphertext,
                    ),
                )
        except Exception:
            raise AuthUnavailableError() from None


class AuthInputPublisher:
    def __init__(
        self,
        *,
        client: AuthNativeClient,
        journal: PostgresAuthPublicationJournal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client, self.journal, self.clock = client, journal, clock

    async def publish(self, source: AuthPublicationSnapshot) -> PublicationReceipt:
        p = source.publication
        try:
            source.guard(self.clock())
            prior = await self.journal.freeze(p)
            source.guard(self.clock())
            if prior is not None:
                bind_result(prior, p, self.clock())
                source.acknowledge(prior)
                return prior
            # Always query first: an earlier process could have sent before dying.
            q = PublicationQuery(
                schema="human-auth-publication-query.v1",
                scope=p.scope,
                workload_ref=p.workload_ref,
                query_id=uuid4().hex,
                publication_id=p.publication_id,
                expected_digest=digest(p),
            )
            lookup = await self.client.execute(q, current=lambda: source.guard(self.clock()))
            source.guard(self.clock())
            if not isinstance(lookup, PublicationLookup):
                raise AuthUnavailableError()
            receipt = lookup.receipt
            if receipt is None:
                source.guard(self.clock())
                sent = await self.client.execute(p, current=lambda: source.guard(self.clock()))
                if not isinstance(sent, PublicationReceipt):
                    raise AuthUnavailableError()
                receipt = sent
            bind_result(receipt, p, self.clock())
            await self.journal.acknowledge(p, receipt)
            source.guard(self.clock())
            source.acknowledge(receipt)
            return receipt
        except BaseException:
            source.snapshot.lease.uncertain()
            raise


class PostgresAuthAuditIntentSource:
    """Read actual gateway audit/outbox TX, verify source-owned frozen snapshot.

    This adapter supplies no handshakes itself and does not designate its publisher.
    The supplied freeze lease must be backed by the real source issuance barrier.
    """

    def __init__(self, store: PostgresAuthDispatchStore) -> None:
        self.store = store

    async def read(self, command_id: str, actor: object, lease: object) -> SourceSnapshot:
        from .auth_profile import Actor
        from .read_publisher import SourceFreezeLease

        if type(actor) is not Actor or type(lease) is not SourceFreezeLease:
            raise AuthUnavailableError()
        try:
            lease.live()
            payload = await self.store.audit_intent(command_id, actor)
            lease.verify(canonicalize(wire(payload)))
            lease.live()
            if self.store.clock() >= lease.provenance.valid_until:
                raise AuthUnavailableError()
            return SourceSnapshot(lease.provenance, payload, lease)
        except BaseException:
            lease.uncertain()
            raise
