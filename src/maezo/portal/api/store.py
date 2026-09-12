"""Atomic, tenant-bound persistence contract. Memory implementation is explicitly local/test."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from maezo.portal.api.records import LoginTransaction, MembershipRecord, SessionRecord


class IdentityStore(Protocol):
    tenant: str
    persistent: bool

    async def put_transaction(self, transaction: LoginTransaction) -> None: ...
    async def consume_transaction(
        self, state_hash: str, browser_hash: str, now: datetime
    ) -> LoginTransaction | None: ...
    async def claim_code(self, digest: str, expires_at: datetime) -> bool: ...
    async def put_session(self, session: SessionRecord, old_hash: str | None) -> None: ...
    async def get_session(self, secret_hash: str, now: datetime) -> SessionRecord | None: ...
    async def revoke_session(self, secret_hash: str) -> None: ...
    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None: ...
    async def purge_expired(self, now: datetime) -> None: ...
    async def close(self) -> None: ...


class LocalTestIdentityStore:
    """Never selected by production factory; shared only within one local process."""

    persistent = False

    def __init__(self, tenant: str) -> None:
        self.tenant = tenant
        self._lock = asyncio.Lock()
        self._transactions: dict[str, LoginTransaction] = {}
        self._codes: dict[str, datetime] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self.memberships: dict[tuple[str, str], MembershipRecord] = {}

    async def put_transaction(self, transaction: LoginTransaction) -> None:
        async with self._lock:
            if transaction.state_hash in self._transactions:
                raise ValueError("duplicate transaction")
            self._transactions[transaction.state_hash] = transaction

    async def consume_transaction(
        self, state_hash: str, browser_hash: str, now: datetime
    ) -> LoginTransaction | None:
        async with self._lock:
            tx = self._transactions.get(state_hash)
            if tx is None or tx.browser_hash != browser_hash or tx.expires_at <= now:
                return None
            return self._transactions.pop(state_hash)

    async def claim_code(self, digest: str, expires_at: datetime) -> bool:
        async with self._lock:
            if digest in self._codes:
                return False
            self._codes[digest] = expires_at
            return True

    async def put_session(self, session: SessionRecord, old_hash: str | None) -> None:
        async with self._lock:
            if old_hash:
                self._sessions.pop(old_hash, None)
            self._sessions[session.secret_hash] = session

    async def get_session(self, secret_hash: str, now: datetime) -> SessionRecord | None:
        session = self._sessions.get(secret_hash)
        return session if session and session.expires_at > now else None

    async def revoke_session(self, secret_hash: str) -> None:
        async with self._lock:
            self._sessions.pop(secret_hash, None)

    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None:
        record = self.memberships.get((issuer, subject))
        return record if record and record.tenant == self.tenant else None

    async def purge_expired(self, now: datetime) -> None:
        async with self._lock:
            self._transactions = {k: v for k, v in self._transactions.items() if v.expires_at > now}
            self._codes = {k: v for k, v in self._codes.items() if v > now}
            self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > now}

    async def close(self) -> None:
        self._transactions.clear()
        self._codes.clear()
        self._sessions.clear()
