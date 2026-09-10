"""Persistent per-tenant identity store. SQL DELETE RETURNING consumes across BFF workers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.portal.api.records import LoginTransaction, MembershipRecord, SessionRecord

if TYPE_CHECKING:
    from maezo.gateway.intake.native_authority import AuthProductionComposition


class PostgresIdentityStore:
    persistent = True

    def __init__(self, tenant: str, engine: AsyncEngine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        self.tenant = tenant
        self._engine = engine
        self._auth_composition: AuthProductionComposition | None = None

    def bind_auth_lifecycle(self, composition: object) -> None:
        from maezo.gateway.intake.native_authority import AuthProductionComposition

        if (
            type(composition) is not AuthProductionComposition
            or composition.source.writer is not self._engine
            or composition.source.binding.tenant != self.tenant
            or self._auth_composition is not None
        ):
            raise ValueError("invalid AUTH identity composition")
        self._auth_composition = composition

    async def _without_auth(self, connection: AsyncConnection) -> None:
        # Absence is read from the actual DB, never guessed from missing config.
        installed = await connection.scalar(text("SELECT to_regclass('portal_auth.installation')"))
        if installed is not None:
            raise ValueError("installed AUTH identity participant required")

    async def put_transaction(self, transaction: LoginTransaction) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO portal_login_transactions "
                    "(tenant, state_hash, browser_hash, expires_at, payload) "
                    "VALUES (:tenant, :state, :browser, :expires, :payload)"
                ),
                {
                    "tenant": self.tenant,
                    "state": transaction.state_hash,
                    "browser": transaction.browser_hash,
                    "expires": transaction.expires_at,
                    "payload": transaction.model_dump_json(),
                },
            )

    async def consume_transaction(
        self, state_hash: str, browser_hash: str, now: datetime
    ) -> LoginTransaction | None:
        async with self._engine.begin() as connection:
            value = (
                await connection.execute(
                    text(
                        "DELETE FROM portal_login_transactions WHERE tenant=:tenant AND state_hash=:state "
                        "AND browser_hash=:browser AND expires_at>:now RETURNING payload"
                    ),
                    {"tenant": self.tenant, "state": state_hash, "browser": browser_hash, "now": now},
                )
            ).scalar_one_or_none()
            return LoginTransaction.model_validate_json(value) if value is not None else None

    async def claim_code(self, digest: str, expires_at: datetime) -> bool:
        async with self._engine.begin() as connection:
            value = (
                await connection.execute(
                    text(
                        "INSERT INTO portal_code_claims (tenant, code_hash, expires_at) "
                        "VALUES (:tenant, :digest, :expires) ON CONFLICT DO NOTHING RETURNING code_hash"
                    ),
                    {"tenant": self.tenant, "digest": digest, "expires": expires_at},
                )
            ).scalar_one_or_none()
            return value is not None

    async def put_session(self, session: SessionRecord, old_hash: str | None) -> None:
        if self._auth_composition is not None:
            await self._auth_composition.qualify()
            await self._auth_composition.source.put_session(session, old_hash)
            return
        async with self._engine.begin() as connection:
            await self._without_auth(connection)
            if old_hash:
                await connection.execute(
                    text("DELETE FROM portal_sessions WHERE tenant=:tenant AND secret_hash=:old"),
                    {"tenant": self.tenant, "old": old_hash},
                )
            await connection.execute(
                text(
                    "INSERT INTO portal_sessions (tenant, secret_hash, expires_at, payload) "
                    "VALUES (:tenant, :secret, :expires, :payload)"
                ),
                {
                    "tenant": self.tenant,
                    "secret": session.secret_hash,
                    "expires": session.expires_at,
                    "payload": session.model_dump_json(),
                },
            )

    async def get_session(self, secret_hash: str, now: datetime) -> SessionRecord | None:
        if self._auth_composition is not None:
            return await self._auth_composition.source.get_session(secret_hash, now)
        async with self._engine.connect() as connection:
            await self._without_auth(connection)
            value = (
                await connection.execute(
                    text(
                        "SELECT payload FROM portal_sessions WHERE tenant=:tenant "
                        "AND secret_hash=:secret AND expires_at>:now"
                    ),
                    {"tenant": self.tenant, "secret": secret_hash, "now": now},
                )
            ).scalar_one_or_none()
            return SessionRecord.model_validate_json(value) if value is not None else None

    async def revoke_session(self, secret_hash: str) -> None:
        if self._auth_composition is not None:
            await self._auth_composition.source.revoke_session(secret_hash)
            return
        async with self._engine.begin() as connection:
            await self._without_auth(connection)
            await connection.execute(
                text("DELETE FROM portal_sessions WHERE tenant=:tenant AND secret_hash=:secret"),
                {"tenant": self.tenant, "secret": secret_hash},
            )

    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None:
        if self._auth_composition is not None:
            return await self._auth_composition.source.get_membership(issuer, subject)
        # This is intentionally uncached: auth revision/revocation is re-read for each resolve.
        async with self._engine.connect() as connection:
            await self._without_auth(connection)
            value = (
                await connection.execute(
                    text(
                        "SELECT payload FROM portal_memberships WHERE tenant=:tenant "
                        "AND issuer=:issuer AND subject=:subject"
                    ),
                    {"tenant": self.tenant, "issuer": issuer, "subject": subject},
                )
            ).scalar_one_or_none()
            return MembershipRecord.model_validate_json(value) if value is not None else None

    async def purge_expired(self, now: datetime) -> None:
        if self._auth_composition is not None:
            await self._auth_composition.qualify()
            await self._auth_composition.source.purge_expired(now)
        async with self._engine.begin() as connection:
            if self._auth_composition is None:
                await self._without_auth(connection)
            for query in (
                "DELETE FROM portal_login_transactions WHERE tenant=:tenant AND expires_at<=:now",
                "DELETE FROM portal_code_claims WHERE tenant=:tenant AND expires_at<=:now",
            ):
                await connection.execute(text(query), {"tenant": self.tenant, "now": now})
            if self._auth_composition is None:
                await connection.execute(
                    text("DELETE FROM portal_sessions WHERE tenant=:tenant AND expires_at<=:now"),
                    {"tenant": self.tenant, "now": now},
                )

    async def close(self) -> None:
        if self._auth_composition is not None:
            await self._auth_composition.close()
        else:
            await self._engine.dispose()
