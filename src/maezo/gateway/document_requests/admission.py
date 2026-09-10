"""System-only current source publication and colocated PostgreSQL admission."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.communications.models import CommunicationScope, alive
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.read_profile import digest

from .models import (
    SystemAccess,
    SystemGrant,
    SystemPublication,
    SystemPublicationReceipt,
    packed,
    parsed,
    require,
)


async def one(c: AsyncConnection, sql: str, params: dict[str, Any]) -> Any:
    return (await c.execute(text(sql), params)).mappings().one_or_none()


async def lock_key(c: AsyncConnection, value: object) -> None:
    await c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": digest(value)})


async def head(c: AsyncConnection, access: SystemAccess, lock: str = " FOR SHARE") -> Any:
    require(lock in {" FOR SHARE", " FOR UPDATE"})
    return await one(
        c,
        "SELECT revision,publication_ref,payload FROM portal_document_request.authority_head "
        "WHERE tenant=:tenant AND environment=:environment AND access_digest=:access" + lock,
        dict(**access.scope.model_dump(), access=digest(access)),
    )


def current(row: Any, access: SystemAccess) -> tuple[SystemGrant, datetime]:
    require(row is not None and row["payload"] is not None, "unavailable")
    publication = parsed(SystemPublication, bytes(row["payload"]))
    require(
        publication.access == access
        and publication.publication_ref == row["publication_ref"]
        and str(int(publication.expected_revision) + 1) == row["revision"],
        "unavailable",
    )
    grant = publication.grant
    require(grant is not None)
    assert grant is not None
    require(
        grant.access == access
        and publication.source_receipt_ref == grant.source.receipt_ref
        and publication.source_digest == digest(grant.source),
        "unavailable",
    )
    return grant, alive(publication.valid_until, grant.ceiling())


class SystemAdmission:
    def __init__(self, engine: AsyncEngine, scope: CommunicationScope, *, seconds: float = 5) -> None:
        self.engine, self.scope, self.seconds = engine, scope, seconds

    async def identity(
        self, c: AsyncConnection, access: SystemAccess, kind: str, source_ref: str | None = None
    ) -> datetime:
        row = await one(
            c,
            "SELECT i.*,r.rolsuper,r.rolcreaterole,r.rolcreatedb,r.rolreplication,r.rolbypassrls "
            "FROM portal_document_request.system_identity i "
            "JOIN pg_roles r ON r.rolname=session_user AND r.oid=i.login_oid "
            "WHERE session_user=current_user AND i.login_name=session_user AND i.tenant=:tenant "
            "AND i.environment=:environment AND i.binding_kind=:kind AND i.binding_ref=:binding "
            "FOR SHARE OF i",
            dict(
                **self.scope.model_dump(),
                kind=kind,
                binding=access.producer.identity_digest if kind == "producer" else source_ref,
            ),
        )
        require(row is not None and not row["revoked"] and access.operation in row["operations"])
        require(
            not any(
                row[k] for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")
            )
        )
        # Table and column privileges both matter; read-write roles cannot self-issue identity or authority.
        forbidden = (
            ("system_identity", "authority_head", "authority_publication")
            if kind == "producer"
            else ("system_identity",)
        )
        for table in forbidden:
            privileges = await one(
                c,
                "SELECT has_table_privilege(current_user,:table,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') "
                "OR has_any_column_privilege(current_user,:table,'INSERT,UPDATE,REFERENCES') AS writable",
                {"table": "portal_document_request." + table},
            )
            require(privileges is not None and not privileges["writable"], "unavailable")
        if kind == "producer":
            require(bytes(row["identity_payload"]) == packed(access.producer))
        return alive(row["valid_until"])

    async def authorize(self, access: SystemAccess) -> SystemGrant:
        require(access.scope == self.scope)
        async with transaction(self.engine, self.seconds) as c:
            grant, ceiling = current(await head(c, access), access)
            ceiling = alive(ceiling, await self.identity(c, access, "producer"))
        alive(ceiling)
        return grant

    @asynccontextmanager
    async def acquire(self, grant: SystemGrant, deadline: datetime) -> AsyncIterator[AsyncConnection]:
        require(grant.access.scope == self.scope)
        ceiling = alive(deadline, grant.ceiling())
        async with transaction(self.engine, self.seconds) as c:
            latest, until = current(await head(c, grant.access), grant.access)
            require(packed(latest) == packed(grant), "conflict")
            ceiling = alive(ceiling, until, await self.identity(c, grant.access, "producer"))
            require(c.in_transaction(), "unavailable")
            yield c
            require(c.in_transaction(), "unavailable")
            alive(ceiling)
        # Original ceilings survive commit/connection cleanup, never re-authorized here.
        alive(ceiling)


@dataclass(frozen=True, repr=False)
class FrozenSystemAuthority:
    """Source owns the durable freeze and exact stable publication across restart."""

    publication: SystemPublication
    source_ref: str
    verify: Callable[[bytes], None]
    live: Callable[[], None]
    committed: Callable[[SystemPublicationReceipt], None]
    uncertain: Callable[[], None]


class SystemPublicationSource(ABC):
    @abstractmethod
    async def freeze(self, access: SystemAccess) -> FrozenSystemAuthority:
        """Durably seal all invalidating writers; restore any outstanding same publication."""
        raise NotImplementedError


class SystemPublisher:
    """Actual separate publisher-role CAS and immutable receipt recovery, no default grants."""

    def __init__(self, admission: SystemAdmission, source: SystemPublicationSource) -> None:
        require(isinstance(source, SystemPublicationSource), "unavailable")
        self.admission, self.source = admission, source

    async def publish(self, access: SystemAccess) -> SystemPublicationReceipt:
        frozen = await self.source.freeze(access)
        try:
            publication = frozen.publication
            require(publication.access == access and access.scope == self.admission.scope)
            if publication.grant is not None:
                require(
                    publication.grant.access == access
                    and publication.source_receipt_ref == publication.grant.source.receipt_ref
                    and publication.source_digest == digest(publication.grant.source)
                )
            frozen.live()
            frozen.verify(packed(publication))
            p = dict(
                **access.scope.model_dump(),
                access=digest(access),
                publication=publication.publication_ref,
                digest=digest(publication),
                payload=packed(publication),
                revision=str(int(publication.expected_revision) + 1),
            )
            require(int(p["revision"]) <= 2**63 - 1, "conflict")
            async with transaction(self.admission.engine, self.admission.seconds) as c:
                # Empty heads and receipt keys serialize under the same exact access lock.
                await lock_key(c, {"system-authority": digest(access), "scope": access.scope.model_dump()})
                await c.execute(
                    text(
                        "INSERT INTO portal_document_request.authority_head "
                        "(tenant,environment,access_digest,revision) VALUES "
                        "(:tenant,:environment,:access,'0') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    p,
                )
                row = await head(c, access, " FOR UPDATE")
                source_ref = frozen.source_ref
                if publication.grant is not None:
                    require(source_ref == publication.grant.source.source_ref)
                ceiling = alive(
                    publication.valid_until, await self.admission.identity(c, access, "publisher", source_ref)
                )
                prior = await one(
                    c,
                    "SELECT request_digest,revision FROM portal_document_request.authority_publication "
                    "WHERE tenant=:tenant AND environment=:environment AND publication_ref=:publication",
                    p,
                )
                if prior is not None:
                    require(
                        prior["request_digest"] == p["digest"] and prior["revision"] == p["revision"],
                        "conflict",
                    )
                else:
                    require(row is not None and row["revision"] == publication.expected_revision, "conflict")
                    await c.execute(
                        text(
                            "UPDATE portal_document_request.authority_head SET revision=:revision,"
                            "publication_ref=:publication,payload=:payload WHERE tenant=:tenant AND "
                            "environment=:environment "
                            "AND access_digest=:access"
                        ),
                        p,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_document_request.authority_publication "
                            "(tenant,environment,publication_ref,request_digest,revision) "
                            "VALUES (:tenant,:environment,:publication,:digest,:revision)"
                        ),
                        p,
                    )
                frozen.live()
                frozen.verify(packed(publication))
                alive(ceiling)
            receipt = SystemPublicationReceipt(
                publication_ref=publication.publication_ref,
                request_digest=p["digest"],
                revision=p["revision"],
            )
            frozen.live()
            alive(ceiling)
            frozen.committed(receipt)
            alive(ceiling)
            return receipt
        except BaseException:
            frozen.uncertain()
            raise
