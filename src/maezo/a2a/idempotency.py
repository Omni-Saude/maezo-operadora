"""Durable A2A delegation idempotency in Postgres (Guard 4 of ADR-0003, R9).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
idempotency.py:1-238`) as part of the T2.4 A2A W2 (delegation runtime) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §2/§5 for the full port rationale.

`DelegationDispatcher` only keeps `task_id` state IN MEMORY (`_inflight`): every replica/restart
re-executes the handler — i.e. re-starts the delegation's downstream effects. This module makes
Guard 4 DURABLE and cross-replica, mirroring `gateway.audit_postgres` (asyncpg, `schema_for_tenant`,
`normalize_dsn`, a lazy pool, per-tenant search_path):

  - `IdempotencyStore` (Protocol): the contract the dispatcher consumes (`claim_or_get` +
    `complete`).
  - `StoredResult`: the terminal result persisted for a delegation (success OR rejection — BOTH
    are terminal 'done', ADR-0003: a rejection never retries).
  - `PostgresIdempotencyStore`: the asyncpg implementation.

**SCHEMA NOTE — no new migration added (deviation from a literal donor port):** the donor's SQL
targets columns `(task_id, tenant, task_type, state, origin, target)` with `state IN ('pending',
'done')` and 4 separate result columns (`success`, `output_ref`, `rejection_reason`, `detail`),
under a single-column `PRIMARY KEY (task_id)`. v2 ALREADY HAS an `a2a_idempotency` table —
`platform/migrations/versions/0003_a2a_idempotency.py` (landed before this build wave, per its own
"ADR-0015: A2A idempotency (Guard 4)" docstring) — but with a DIFFERENT, already-migrated shape:
`(task_id, tenant, task_type, origin, target, status DEFAULT 'processing', result jsonb,
created_at, completed_at)` under composite `PRIMARY KEY (task_id, tenant)`, plus a partial index
`ix_a2a_idempotency_status ... WHERE status = 'processing'`. Adding a SECOND, differently-shaped
migration would either collide (the `CREATE TABLE IF NOT EXISTS` would silently no-op, leaving the
donor-shaped columns this module would then reference NONEXISTENT in the real table) or fork the
table's identity. The correct port action — SQL rewritten against the REAL, already-migrated
schema, not a new migration — is:

  donor `state` ('pending'/'done')          -> v2 `status` ('processing'/'done'; 'processing'
                                               matches the existing partial index's assumption)
  donor `success`/`output_ref`/                -> v2 `result: jsonb` (a single column packing the
    `rejection_reason`/`detail` (4 columns)       same 4 fields — `StoredResult`'s Python shape is
                                                    UNCHANGED; only the persistence encoding moves
                                                    from 4 columns to 1 jsonb blob)
  donor `PRIMARY KEY (task_id)`                -> v2 `PRIMARY KEY (task_id, tenant)` (claim/select/
                                                    complete all filter on BOTH columns)
  `runtime.checkpoint.normalize_conn_string`   -> `gateway.audit_postgres.normalize_dsn` (v2 has no
                                                    `runtime.checkpoint.normalize_conn_string`; the
                                                    equivalent lives in `gateway.audit_postgres`,
                                                    design §2 friction row 4 — mechanical rename)
  `runtime.checkpoint.schema_for_tenant`       -> `gateway.audit_postgres.schema_for_tenant`
                                                    (same module, mechanical import repoint)

`claim_or_get(*, tenant, task_id)`:
  - Atomically claims `task_id`: `INSERT ... ON CONFLICT (task_id, tenant) DO NOTHING` under
    `pg_advisory_xact_lock(hashtext(task_id))` (serializes concurrent claims of the same task_id).
  - NEW row (claim won) -> returns `None`: the caller (dispatcher) executes the handler.
  - ALREADY 'done' row -> returns the terminal `StoredResult` (replay; the handler does NOT run).
  - 'processing' row (another replica executing) -> bounded poll until 'done'; if it doesn't seal
    within budget, returns `None` (best-effort; the caller falls back to the normal path — the PK
    still prevents double-persisting the terminal result via `complete`).

`complete(*, tenant, task_id, result)`: seals the row as 'done' (UPDATE), writing the packed
`result` jsonb. Idempotent: only updates rows still 'processing'.

PHI: the table carries only references (`output_ref` is already a FHIR/process reference, never
raw PHI — guaranteed by `HandlerOutput`/`DelegationResult`) plus route metadata (origin/target).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

if TYPE_CHECKING:
    from .dispatcher import DelegationResult

# Terminal/transient states of an idempotency row. `STATUS_PROCESSING` matches the DEFAULT the
# already-migrated `a2a_idempotency` table's `status` column carries (0003_a2a_idempotency.py)
# and the partial index `ix_a2a_idempotency_status ... WHERE status = 'processing'`.
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"

# Placeholder `task_type`/`origin`/`target` at claim time: the claim happens BEFORE the envelope is
# validated, so the 'processing' row is born with placeholders (the DDL requires NOT NULL on those
# route columns). `complete` never rewrites them — the Kafka fact already carries the full route.
_UNKNOWN = "unknown"

# Atomic claim: inserts the task_id's 'processing' row; if it already exists, does NOTHING (the
# 2nd replica reads the existing row instead). RETURNING distinguishes a won claim (row returned)
# from a conflict (empty).
CLAIM_SQL = (
    "INSERT INTO a2a_idempotency (task_id, tenant, task_type, status, origin, target) "
    "VALUES ($1, $2, $3, 'processing', $4, $5) "
    "ON CONFLICT (task_id, tenant) DO NOTHING "
    "RETURNING task_id"
)

# Reads the existing row (state + packed terminal result) for a task_id.
SELECT_SQL = "SELECT status, result FROM a2a_idempotency WHERE task_id = $1 AND tenant = $2"

# Seals the row as terminal 'done' (only if still 'processing' — idempotent; never reseals a done
# row).
COMPLETE_SQL = (
    "UPDATE a2a_idempotency "
    "SET status = 'done', result = $3::jsonb, completed_at = now() "
    "WHERE task_id = $1 AND tenant = $2 AND status = 'processing'"
)

# Per-task_id (xact) lock: serializes the concurrent claim of the SAME task_id across replicas.
# Released on the transaction's commit/rollback (same pattern as audit_postgres's per-tenant
# advisory lock).
ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"

# Budget for polling a 'processing' row (another replica executing). Short and bounded: if it
# doesn't seal, the caller falls back to the normal path (the PK + idempotent `complete` still
# protect the persisted result).
_POLL_INTERVAL_S: float = 0.05
_POLL_MAX_ATTEMPTS: int = 40  # ~2s total


@dataclass(frozen=True, slots=True)
class StoredResult:
    """The persisted terminal result of a delegation (success OR rejection — BOTH 'done').

    Mirrors the relevant fields of `DelegationResult`. Reconstructed on replay to hand the caller
    exactly the prior outcome (with `idempotent_replay=True` set by the dispatcher).
    """

    task_id: str
    success: bool
    output_ref: str | None = None
    rejection_reason: str | None = None
    detail: str | None = None

    @classmethod
    def from_result(cls, result: DelegationResult) -> StoredResult:
        """Map a `DelegationResult` to its persisted shape (pure, testable without a DB)."""
        reason = result.rejection_reason
        return cls(
            task_id=result.task_id,
            success=result.success,
            output_ref=result.output_ref,
            rejection_reason=str(reason) if reason is not None else None,
            detail=result.detail,
        )


class IdempotencyStore(Protocol):
    """The contract `DelegationDispatcher` consumes (durable Guard 4)."""

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        """Claim the task_id. `None` -> the caller executes; `StoredResult` -> replay (no execute)."""
        ...

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        """Seal the terminal result (success OR rejection) of the delegation."""
        ...


def _result_payload(stored: StoredResult) -> dict[str, Any]:
    """The `result` jsonb payload for a `StoredResult` (pure, testable without a DB)."""
    return {
        "success": stored.success,
        "output_ref": stored.output_ref,
        "rejection_reason": stored.rejection_reason,
        "detail": stored.detail,
    }


def _decode_result(value: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as raw text unless a codec is registered; decode defensively."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    decoded: Any = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError(f"expected a JSON object in a2a_idempotency.result, got {type(decoded).__name__}")
    return decoded


def _row_to_stored(task_id: str, row: Any) -> StoredResult:
    """Map a 'done' `a2a_idempotency` row to a `StoredResult` (pure, testable without a DB)."""
    payload = _decode_result(row["result"])
    return StoredResult(
        task_id=task_id,
        success=bool(payload.get("success", False)),
        output_ref=payload.get("output_ref"),
        rejection_reason=payload.get("rejection_reason"),
        detail=payload.get("detail"),
    )


def complete_params(*, result: DelegationResult) -> tuple[str, str]:
    """Pure `DelegationResult` -> `(task_id, result_jsonb)` mapping for `COMPLETE_SQL`.

    Testable without a DB. `tenant` (`COMPLETE_SQL`'s `$2`) is supplied separately by `complete()`
    — it does not derive from the result. Adapted to v2's actual `a2a_idempotency` schema
    (`status`/`result: jsonb`) — see this module's docstring for why no new migration replaces it
    with the donor's 5-column shape.
    """
    stored = StoredResult.from_result(result)
    return stored.task_id, json.dumps(_result_payload(stored), sort_keys=True, default=str)


class PostgresIdempotencyStore:
    """Durable A2A idempotency store (satisfies `IdempotencyStore`).

    Lazy asyncpg pool (opened on first access), search_path pinned to the tenant's schema
    (schema-per-tenant; mirrors `PostgresAuditSink`). NEVER re-executes nor re-seals a terminal
    delegation — it only claims/reads/seals the `task_id` row.
    """

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        pool: asyncpg.Pool | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
        poll_max_attempts: int = _POLL_MAX_ATTEMPTS,
    ) -> None:
        # Validates the tenant as a schema identifier (anti-injection) — same rule as the
        # checkpointer/audit sink.
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool
        self._poll_interval_s = poll_interval_s
        self._poll_max_attempts = poll_max_attempts

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=10,
                # `setup` (NOT `init`): runs on EVERY acquire. asyncpg's pool RESETs ALL on
                # connection release, which CLEARS session state like `SET search_path`. `init`
                # only runs once at connection CREATION, so the 2nd+ claim/complete on a REUSED
                # connection would silently fall back to the default search_path and fail to find
                # `a2a_idempotency` — a real production bug (fix #55 in `PostgresAuditSink`, ported
                # here for the same reason). `setup` re-pins the search_path on every acquire.
                setup=self._set_search_path,
            )
        return self._pool

    async def _set_search_path(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by `schema_for_tenant()` in `__init__` (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        """Atomically claim `task_id` (durable Guard 4).

        - Claim WON (new row) -> `None`: the caller executes the handler and calls `complete`.
        - ALREADY 'done' row -> the terminal `StoredResult` (replay; the handler does NOT run).
        - 'processing' row (another replica) -> bounded poll; once sealed -> `StoredResult`. If
          the budget is exceeded, `None` (best-effort — the PK + idempotent `complete` still
          protect the persisted result).
        """
        _ = tenant  # tenant already fixes the schema at construction; kept for Protocol parity.
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            # Serializes the concurrent claim of the SAME task_id; released at transaction end.
            await conn.execute(ADVISORY_LOCK_SQL, task_id)
            claimed = await conn.fetchval(CLAIM_SQL, task_id, self._tenant, _UNKNOWN, _UNKNOWN, _UNKNOWN)
            if claimed is not None:
                # Claim won: a freshly-created 'processing' row -> the caller executes.
                return None
            # Conflict: the row already exists. Read its current state.
            row = await conn.fetchrow(SELECT_SQL, task_id, self._tenant)
        if row is not None and row["status"] == STATUS_DONE:
            return _row_to_stored(task_id, row)
        # 'processing' row (another replica executing): bounded poll outside the claim transaction.
        return await self._poll_until_done(pool, task_id)

    async def _poll_until_done(self, pool: asyncpg.Pool, task_id: str) -> StoredResult | None:
        """Wait (bounded) for another replica to seal the 'processing' row. `None` if it times out."""
        for _ in range(self._poll_max_attempts):
            await asyncio.sleep(self._poll_interval_s)
            async with pool.acquire() as conn:
                row = await conn.fetchrow(SELECT_SQL, task_id, self._tenant)
            if row is not None and row["status"] == STATUS_DONE:
                return _row_to_stored(task_id, row)
        return None

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        """Seal the terminal result (success OR rejection). Idempotent: only updates 'processing'."""
        _ = task_id  # derives from result.task_id via complete_params (Protocol-shape parity).
        pool = await self._ensure_pool()
        stored_task_id, result_json = complete_params(result=result)
        async with pool.acquire() as conn:
            await conn.execute(COMPLETE_SQL, stored_task_id, tenant, result_json)

    async def aclose(self) -> None:
        """Close the pool (if this store opened it)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
