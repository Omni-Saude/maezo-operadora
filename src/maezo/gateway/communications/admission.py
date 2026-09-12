"""Same-transaction identity/authority admission and source-owned PostgreSQL publication.

No source provider is installed here. Its durable freeze must cover every authority input
and writer until the exact publication is acknowledged, including ambiguous commits.
Colocation, installed roles and source coverage require independent qualification.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import CommunicationAccess, CommunicationAuthority, CommunicationGrant, CommunicationScope, alive


def packed(value: Closed) -> bytes:
    raw = canonicalize(wire(value))
    if len(raw) > 65536:
        raise ExternalCaseError("unavailable")
    return raw


class Publication(Closed):
    publication_ref: ResourceRef
    access: CommunicationAccess
    expected_revision: DecimalRevision
    source_receipt_ref: ResourceRef
    source_digest: Sha256Digest
    valid_until: datetime
    grant: CommunicationGrant | None


class PublicationReceipt(Closed):
    publication_ref: ResourceRef
    request_digest: Sha256Digest
    revision: DecimalRevision


@dataclass(frozen=True, repr=False)
class FrozenAuthority:
    """Only a qualified source can furnish this durable, complete invalidation freeze.

    verify binds the exact publication bytes to original authority inputs and coverage.
    uncertain MUST retain the durable source freeze through process/connection loss;
    committed releases it only after reconciliation of this exact immutable receipt.
    No live() callback is used as a substitute for mutation/identity database locks.
    """

    expected_revision: str
    source_receipt_ref: str
    source_digest: str
    valid_until: datetime
    grant: CommunicationGrant | None = field(repr=False)
    verify: Callable[[bytes], None] = field(repr=False)
    live: Callable[[], None] = field(repr=False)
    committed: Callable[[PublicationReceipt], None] = field(repr=False)
    uncertain: Callable[[], None] = field(repr=False)


class CommunicationPublicationSource(ABC):
    @abstractmethod
    async def freeze(self, access: CommunicationAccess) -> FrozenAuthority:
        """Freeze ALL case/resource/recipient/consent/field/key authority inputs/writers.

        Absent/incomplete qualification or coverage must refuse. This is source-owned,
        never a client-supplied grant or a callback that merely reads current state.
        """
        raise NotImplementedError


async def head(connection: AsyncConnection, access: CommunicationAccess, *, lock: str = "") -> Any:
    if lock not in {"", " FOR SHARE", " FOR UPDATE"}:
        raise ExternalCaseError("unavailable")
    return (
        (
            await connection.execute(
                text(
                    "SELECT revision,publication_ref,payload FROM portal_communication.authority_head "
                    "WHERE tenant=:tenant AND environment=:environment AND access_digest=:access" + lock
                ),
                dict(**access.scope.model_dump(), access=digest(access)),
            )
        )
        .mappings()
        .one_or_none()
    )


def current(row: Any, access: CommunicationAccess) -> CommunicationGrant:
    try:
        if row is None or row["payload"] is None:
            raise ExternalCaseError("unavailable")
        raw = bytes(row["payload"])
        publication = parse_model(Publication, strict_loads(raw))
        if (
            packed(publication) != raw
            or publication.access != access
            or publication.publication_ref != row["publication_ref"]
            or str(int(publication.expected_revision) + 1) != row["revision"]
        ):
            raise ExternalCaseError("unavailable")
        alive(publication.valid_until)
        grant = publication.grant
        if grant is None:
            raise ExternalCaseError("denied")
        if grant.access != access:
            raise ExternalCaseError("unavailable")
        ceiling = alive(grant.ceiling(), publication.valid_until)
        return grant.model_copy(update={"valid_until": ceiling})
    except ExternalCaseError:
        raise
    except Exception:
        raise ExternalCaseError("unavailable") from None


class PostgresCommunicationAuthority(CommunicationAuthority):
    def __init__(self, engine: AsyncEngine, *, scope: CommunicationScope, seconds: float = 5) -> None:
        self.engine, self.scope, self.seconds = engine, scope, seconds

    async def authorize(self, access: CommunicationAccess) -> CommunicationGrant:
        if access.scope != self.scope or access.principal.tenant != self.scope.tenant:
            raise ExternalCaseError("denied")
        async with transaction(self.engine, self.seconds) as connection:
            return current(await head(connection, access), access)


class PostgresCommunicationAdmission:
    """Mutations use the actual identity store's engine and one SQL transaction only."""

    def __init__(self, store: PostgresIdentityStore, *, scope: CommunicationScope, issuer: str) -> None:
        if not isinstance(store, PostgresIdentityStore) or store.tenant != scope.tenant:
            raise ExternalCaseError("unavailable")
        self.store, self.scope, self.issuer = store, scope, issuer
        self.engine = store._engine

    def bind(self, resolver: HumanSessionResolver, engine: AsyncEngine) -> None:
        if (
            resolver.store is not self.store
            or resolver.settings.issuer != self.issuer
            or resolver.settings.tenant != self.scope.tenant
            or engine is not self.engine
        ):
            raise ExternalCaseError("unavailable")

    async def _identity(
        self, connection: AsyncConnection, secret: str, expected: ResolvedHumanSession
    ) -> datetime:
        if len(secret) != 43 or not all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in secret
        ):
            raise ExternalCaseError("denied")
        hashed = secret_digest(secret)
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM portal_communication.lock_session(:secret)"),
                    {"secret": hashed},
                )
            )
            .mappings()
            .one_or_none()
        )
        # This SQL function reads/locks the original public.portal_sessions and
        # public.portal_memberships rows in THIS connection, never a copied projection.
        try:
            if row is None:
                raise ValueError
            import json

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError
                    value[key] = item
                return value

            for payload in (row["session_payload"], row["membership_payload"]):
                if type(payload) is not str or len(payload.encode()) > 65536:
                    raise ValueError
                json.loads(payload, object_pairs_hook=unique)
            session = SessionRecord.model_validate_json(row["session_payload"])
            member = MembershipRecord.model_validate_json(row["membership_payload"])
            if (
                row["session_tenant"] != self.scope.tenant
                or row["membership_tenant"] != self.scope.tenant
                or member.tenant != self.scope.tenant
                or row["secret_hash"] != hashed
                or session.secret_hash != hashed
                or row["expires_at"] != session.expires_at
                or row["issuer"] != member.issuer
                or row["subject"] != member.subject
                or row["principal_ref"] != member.principal_ref
                or member.issuer != self.issuer
                or session.issuer != self.issuer
                or session.subject != member.subject
                or session.principal_ref != member.principal_ref
                or session.membership_revision != member.revision
                or member.revoked
            ):
                raise ValueError
            principal = HumanPrincipal(
                schema_version=1,
                principal_ref=member.principal_ref,
                issuer=session.issuer,
                subject=session.subject,
                tenant=member.tenant,
                membership_revision=member.revision,
                memberships=member.memberships,
                session_ref=session.session_ref,
                authenticated_at=session.authenticated_at,
                subject_bindings=member.subject_bindings,
            )
            if (
                principal != expected.principal
                or member.audience != expected.membership.audience
                or session.csrf_token != expected.record.csrf_token
            ):
                raise ValueError
            return alive(
                session.expires_at,
                member.reviewed_until,
                expected.record.expires_at,
                expected.membership.reviewed_until,
            )
        except Exception:
            raise ExternalCaseError("denied") from None

    @asynccontextmanager
    async def acquire(
        self,
        *,
        secret: str,
        session: ResolvedHumanSession,
        grant: CommunicationGrant,
        deadline: datetime,
        seconds: float,
    ) -> AsyncIterator[AsyncConnection]:
        if grant.access.scope != self.scope or grant.access.principal != session.principal:
            raise ExternalCaseError("denied")
        ceiling = alive(deadline, grant.ceiling())
        async with transaction(self.engine, seconds) as connection:
            # Fixed order: authority head, session, membership, command, case.
            latest = current(await head(connection, grant.access, lock=" FOR SHARE"), grant.access)
            if (
                latest.authority_digest != grant.authority_digest
                or latest.permitted_fields != grant.permitted_fields
                or [
                    (r.identity_digest, r.audience, r.source_revision, r.policy_digest)
                    for r in latest.intended_recipients
                ]
                != [
                    (r.identity_digest, r.audience, r.source_revision, r.policy_digest)
                    for r in grant.intended_recipients
                ]
            ):
                raise ExternalCaseError("conflict")
            ceiling = alive(ceiling, latest.ceiling(), await self._identity(connection, secret, session))
            if not connection.in_transaction():
                raise ExternalCaseError("unavailable")
            yield connection
            alive(ceiling)
            if not connection.in_transaction():
                raise ExternalCaseError("unavailable")
        alive(ceiling)


class PostgresCommunicationPublisher:
    """Actual CAS storage/receipt/recovery. Missing qualified source closes publication."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        scope: CommunicationScope,
        source: CommunicationPublicationSource | None,
        seconds: float = 5,
    ) -> None:
        self.engine, self.scope, self.source, self.seconds = engine, scope, source, seconds
        self._pending: tuple[Publication, FrozenAuthority] | None = None
        self._busy = False

    async def publish(self, access: CommunicationAccess) -> PublicationReceipt:
        if self._busy or self._pending is not None or self.source is None or access.scope != self.scope:
            raise ExternalCaseError("unavailable")
        self._busy = True
        try:
            frozen = await self.source.freeze(access)
            try:
                frozen.live()
                publication = Publication(
                    publication_ref=uuid4().hex,
                    access=access,
                    expected_revision=frozen.expected_revision,
                    source_receipt_ref=frozen.source_receipt_ref,
                    source_digest=frozen.source_digest,
                    valid_until=frozen.valid_until,
                    grant=frozen.grant,
                )
                if publication.grant is not None and publication.grant.access != access:
                    raise ExternalCaseError("unavailable")
                alive(publication.valid_until)
                frozen.verify(packed(publication))
                self._pending = publication, frozen
            except BaseException:
                frozen.uncertain()
                raise
            return await self._submit()
        finally:
            self._busy = False

    async def reconcile(self) -> PublicationReceipt:
        if self._busy or self._pending is None:
            raise ExternalCaseError("unavailable")
        self._busy = True
        try:
            return await self._submit()
        finally:
            self._busy = False

    async def _submit(self) -> PublicationReceipt:
        assert self._pending is not None
        publication, frozen = self._pending
        raw = packed(publication)
        request_digest = digest(publication)
        params = dict(
            **self.scope.model_dump(),
            access=digest(publication.access),
            publication=publication.publication_ref,
            digest=request_digest,
            payload=raw,
            revision=str(int(publication.expected_revision) + 1),
        )
        try:
            frozen.live()
            frozen.verify(raw)
            async with transaction(self.engine, self.seconds) as connection:
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.authority_head "
                        "(tenant,environment,access_digest,revision) "
                        "VALUES (:tenant,:environment,:access,'0') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    params,
                )
                row = await head(connection, publication.access, lock=" FOR UPDATE")
                previous = (
                    (
                        await connection.execute(
                            text(
                                "SELECT request_digest,revision "
                                "FROM portal_communication.authority_publication "
                                "WHERE tenant=:tenant AND environment=:environment "
                                "AND publication_ref=:publication"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous is not None:
                    if (
                        previous["request_digest"] != request_digest
                        or previous["revision"] != params["revision"]
                    ):
                        raise ExternalCaseError("conflict")
                else:
                    alive(publication.valid_until)
                    if row is None or row["revision"] != publication.expected_revision:
                        raise ExternalCaseError("conflict")
                    await connection.execute(
                        text(
                            "UPDATE portal_communication.authority_head SET revision=:revision,"
                            "publication_ref=:publication,payload=:payload WHERE tenant=:tenant "
                            "AND environment=:environment AND access_digest=:access"
                        ),
                        params,
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO portal_communication.authority_publication "
                            "(tenant,environment,publication_ref,request_digest,revision) "
                            "VALUES (:tenant,:environment,:publication,:digest,:revision)"
                        ),
                        params,
                    )
                    alive(publication.valid_until)
                frozen.live()
            receipt = PublicationReceipt(
                publication_ref=publication.publication_ref,
                request_digest=request_digest,
                revision=params["revision"],
            )
            frozen.live()
            frozen.committed(receipt)
            self._pending = None
            return receipt
        except BaseException:
            frozen.uncertain()
            raise
