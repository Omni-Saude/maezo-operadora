"""PLAN-W3-A2A: short tenant transactions around admission and finalization.

The handler, engine, inference and relay are outside this scope. Lock order is
always audit tenant then task id. The lease is explicit, task-owned and revoked
on exit: no ambient connection is inherited by a child task (DL-0017/18).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn

from .facts import TOPIC_REQUESTED
from .idempotency import (
    MARK_REQUESTED_SQL,
    STATUS_DONE,
    PostgresIdempotencyStore,
    StoredResult,
    _row_to_stored,
)
from .outbox import ENQUEUE_SQL, PostgresFactOutbox, fact_dedup_key, outbox_row_params


class AtomicSession:
    """One active connection lease. All methods reject wrong task/tenant/schema."""

    def __init__(self, owner: PostgresDelegationTransactions, conn: Any) -> None:
        self.owner = owner
        self.conn = conn
        self.task = asyncio.current_task()
        self.active = True

    async def check(self, tenant: str) -> None:
        if not self.active or asyncio.current_task() is not self.task:
            raise RuntimeError("A2A transaction lease is inactive or belongs to another task")
        if tenant != self.owner.tenant or not self.conn.is_in_transaction():
            raise ValueError("A2A transaction tenant or lifetime mismatch")
        if await self.conn.fetchval("SELECT current_schema()") != self.owner.outbox.schema:
            raise ValueError("A2A transaction schema mismatch")

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        await self.check(record.tenant_id)
        return (await self.owner.audit.emit_once_on(self.conn, record, dedup_key=dedup_key)).record_hash

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        params = outbox_row_params(topic, value, key)
        await self.check(params[0])
        await self.conn.fetchval(ENQUEUE_SQL, *params)

    async def claim(self, task_id: str) -> tuple[bool, Any]:
        await self.check(self.owner.tenant)
        return await self.owner.store.claim_on(self.conn, tenant=self.owner.tenant, task_id=task_id)

    async def stored(self, task_id: str) -> StoredResult | None:
        await self.check(self.owner.tenant)
        await self.conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", task_id)
        row = await self.conn.fetchrow(
            "SELECT status, result, requested_enqueued_at FROM a2a_idempotency "
            "WHERE task_id = $1 AND tenant = $2 FOR UPDATE",
            task_id,
            self.owner.tenant,
        )
        if row is None or row["requested_enqueued_at"] is None:
            raise RuntimeError("A2A admission missing during finalization")
        return _row_to_stored(task_id, row) if row["status"] == STATUS_DONE else None

    async def requested_exists(self, task_id: str, row: Any) -> bool:
        """Recognize legacy bytes without rewriting or creating fictional history."""
        await self.check(self.owner.tenant)
        if row["requested_enqueued_at"] is not None:
            return True
        key = fact_dedup_key(tenant=self.owner.tenant, task_id=task_id, kind="requested")
        rows = await self.conn.fetch(
            "SELECT tenant, topic, partition_key, payload FROM a2a_fact_outbox "
            "WHERE tenant = $1 AND dedup_key = $2 ORDER BY id",
            self.owner.tenant,
            key,
        )
        for old in rows:
            # outbox_row_params checks required wire shape and kind; also pin topic,
            # partition key and task identity before acknowledging a legacy row.
            params = outbox_row_params(
                old["topic"],
                bytes(old["payload"]),
                key=old["partition_key"].encode() if old["partition_key"] else None,
            )
            payload = json.loads(bytes(old["payload"]))
            if (
                params[0] != self.owner.tenant
                or params[1] != key
                or old["topic"] != TOPIC_REQUESTED
                or payload["kind"] != "requested"
                or payload["task_id"] != task_id
                or old["partition_key"] != self.owner.tenant
            ):
                raise ValueError("invalid legacy A2A requested identity")
        return bool(rows)

    async def mark_requested(self, task_id: str) -> None:
        await self.check(self.owner.tenant)
        # Same statement the non-transactional seam uses (`PostgresIdempotencyStore.mark_requested`);
        # shared so the two writers of this column cannot drift. Here it commits WITH the outbox
        # insert, which is what makes this path stronger than the seam's two separate writes.
        await self.conn.execute(MARK_REQUESTED_SQL, task_id, self.owner.tenant)

    async def complete(self, result: Any) -> None:
        await self.check(self.owner.tenant)
        await self.owner.store.complete_on(
            self.conn, tenant=self.owner.tenant, task_id=result.task_id, result=result
        )


class PostgresDelegationTransactions:
    """Enlists the existing repositories; one pool, no new relay or payload format."""

    def __init__(
        self,
        *,
        tenant: str,
        audit: PostgresAuditSink,
        store: PostgresIdempotencyStore,
        outbox: PostgresFactOutbox,
    ) -> None:
        self.tenant, self.audit, self.store, self.outbox = tenant, audit, store, outbox
        if (
            audit._tenant_id != tenant
            or store._tenant != tenant
            or outbox.tenant != tenant
            or len({normalize_dsn(audit._dsn), store._dsn, outbox._dsn}) != 1
        ):
            raise ValueError("A2A persistence participants must share tenant and database")

    @asynccontextmanager
    async def transaction(self, tenant: str) -> AsyncIterator[AtomicSession]:
        if tenant != self.tenant:
            raise ValueError("A2A transaction tenant mismatch")
        pool = await self.outbox._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            session = AtomicSession(self, conn)
            try:
                await session.check(tenant)
                # Same advisory lock as PostgresAuditSink, acquired before any task lock.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant)
                yield session
            finally:
                session.active = False

    async def poll(self, task_id: str) -> StoredResult | None:
        """Preserve bounded best-effort wait; no lease/exactly-once handler claim."""
        pool = await self.outbox._ensure_pool()
        return await self.store._poll_until_done(pool, task_id)
