"""Persistent per-tenant identity store. SQL DELETE RETURNING consumes across BFF workers."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.api.records import LoginTransaction, MembershipRecord, SessionRecord


class PostgresIdentityStore:
    persistent = True

    def __init__(self, tenant: str, engine: AsyncEngine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        self.tenant = tenant
        self._engine = engine

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
        async with self._engine.begin() as connection:
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
        async with self._engine.connect() as connection:
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
        async with self._engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM portal_sessions WHERE tenant=:tenant AND secret_hash=:secret"),
                {"tenant": self.tenant, "secret": secret_hash},
            )

    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None:
        # This is intentionally uncached: auth revision/revocation is re-read for each resolve.
        async with self._engine.connect() as connection:
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
        async with self._engine.begin() as connection:
            for query in (
                "DELETE FROM portal_login_transactions WHERE tenant=:tenant AND expires_at<=:now",
                "DELETE FROM portal_code_claims WHERE tenant=:tenant AND expires_at<=:now",
                "DELETE FROM portal_sessions WHERE tenant=:tenant AND expires_at<=:now",
            ):
                await connection.execute(text(query), {"tenant": self.tenant, "now": now})

    async def close(self) -> None:
        await self._engine.dispose()
