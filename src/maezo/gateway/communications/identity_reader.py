"""Read-only adapter over the registered original identity relations."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.records import LoginTransaction, MembershipRecord, SessionRecord

from .identity_owner import IdentityOwnerClient, identity_record_digest
from .production_config import PhiProductionError


@dataclass(frozen=True, repr=False)
class _Observation:
    session_hash: str
    session: SessionRecord
    session_digest: str
    membership_digest: str | None
    observed_at: datetime


class PhiIdentityReader:
    """IdentityStore-compatible reader; all mutation methods refuse except remote revoke."""

    persistent = True

    def __init__(
        self,
        tenant: str,
        engine: AsyncEngine,
        owner: IdentityOwnerClient,
        *,
        seconds: float = 5,
    ) -> None:
        if engine.dialect.name != "postgresql" or owner.tenant != tenant or not 0 < seconds <= 10:
            raise PhiProductionError()
        self.tenant = tenant
        self._engine = engine
        self._owner = owner
        self._seconds = seconds
        self._observation: ContextVar[_Observation | None] = ContextVar(
            f"phi_identity_observation_{id(self)}", default=None
        )
        self._closed = False

    async def get_session(self, secret_hash: str, now: datetime) -> SessionRecord | None:
        self._observation.set(None)
        if self._closed or now.tzinfo is None:
            raise AuthenticationError()
        async with asyncio.timeout(self._seconds):
            async with self._engine.connect() as connection:
                raw = (
                    await connection.execute(
                        text(
                            "SELECT payload FROM public.portal_sessions WHERE tenant=:tenant "
                            "AND secret_hash=:secret AND expires_at>:now"
                        ),
                        {"tenant": self.tenant, "secret": secret_hash, "now": now},
                    )
                ).scalar_one_or_none()
        if raw is None:
            return None
        try:
            record = SessionRecord.model_validate_json(raw)
            if record.secret_hash != secret_hash or record.expires_at <= now:
                raise ValueError
            self._observation.set(
                _Observation(
                    session_hash=secret_hash,
                    session=record,
                    session_digest=identity_record_digest(record),
                    membership_digest=None,
                    observed_at=datetime.now(UTC),
                )
            )
            return record
        except Exception:
            self._observation.set(None)
            raise AuthenticationError() from None

    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None:
        observation = self._observation.get()
        if (
            self._closed
            or observation is None
            or (observation.session.issuer, observation.session.subject) != (issuer, subject)
        ):
            raise AuthenticationError()
        async with asyncio.timeout(self._seconds):
            async with self._engine.connect() as connection:
                raw = (
                    await connection.execute(
                        text(
                            "SELECT payload FROM public.portal_memberships WHERE tenant=:tenant "
                            "AND issuer=:issuer AND subject=:subject"
                        ),
                        {"tenant": self.tenant, "issuer": issuer, "subject": subject},
                    )
                ).scalar_one_or_none()
        if raw is None:
            self._observation.set(None)
            return None
        try:
            record = MembershipRecord.model_validate_json(raw)
            if record.tenant != self.tenant or (record.issuer, record.subject) != (issuer, subject):
                raise ValueError
            self._observation.set(
                _Observation(
                    session_hash=observation.session_hash,
                    session=observation.session,
                    session_digest=observation.session_digest,
                    membership_digest=identity_record_digest(record),
                    observed_at=observation.observed_at,
                )
            )
            return record
        except Exception:
            self._observation.set(None)
            raise AuthenticationError() from None

    async def revoke_session(self, secret_hash: str) -> None:
        observation = self._observation.get()
        self._observation.set(None)
        if (
            self._closed
            or observation is None
            or observation.session_hash != secret_hash
            or observation.membership_digest is None
        ):
            raise AuthenticationError()
        await self._owner.revoke(
            session_hash=secret_hash,
            observed_session_digest=observation.session_digest,
            observed_membership_digest=observation.membership_digest,
            observed_at=observation.observed_at,
        )

    async def put_transaction(self, transaction: LoginTransaction) -> None:
        raise AuthenticationError()

    async def consume_transaction(
        self, state_hash: str, browser_hash: str, now: datetime
    ) -> LoginTransaction | None:
        raise AuthenticationError()

    async def claim_code(self, digest: str, expires_at: datetime) -> bool:
        raise AuthenticationError()

    async def put_session(self, session: SessionRecord, old_hash: str | None) -> None:
        raise AuthenticationError()

    async def purge_expired(self, now: datetime) -> None:
        raise AuthenticationError()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._observation.set(None)
            try:
                await self._owner.close()
            finally:
                await self._engine.dispose()
