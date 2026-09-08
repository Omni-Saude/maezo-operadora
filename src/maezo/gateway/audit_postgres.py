"""Durable, fail-closed Postgres sink for the audit hash chain (T1.10, ADR-0007, DL-0018, B7).

The in-memory `AuditSink` (gateway/audit.py) is a dev/test reference only: it forks the chain
from genesis on every process restart, which violates the append-only, tamper-evident guarantee
ADR-0007 requires ("cadeia completa e reproduzivel"). `PostgresAuditSink` makes the chain durable
by writing every record straight through to the `audit_chain` table
(`platform/migrations/versions/0002_audit_chain.py`) on `emit()` — no batching, no
fire-and-forget, no in-memory head cache.

Design decisions (see docs/adr/0027-audit-transport-postgres-first.md for the transport ADR):

  - **Fail-closed**: `emit()` raises on any DB failure (connection loss, constraint violation,
    lock timeout). An unauditable action must not proceed silently — callers MUST NOT swallow
    exceptions from `emit()` and continue.

  - **No in-memory head cache**: every `emit()` reads the current chain tail from Postgres
    *inside* the same locked transaction that inserts the new record (see `_fetch_tail` below).
    This trades one extra read per write for eliminating an entire class of restart-fork bugs:
    a fresh `PostgresAuditSink` instance (e.g. after a crash/restart, or a second replica) needs
    no explicit "recover head" step — its very first `emit()` already sees the true tail, because
    the tail is derived from durable storage, not from process memory. (Contrast with v1's
    `AuditLog`, which cached `last_hash` in the writer and needed a separate `recover_head()` call
    to reseed after restart — a real gap the kill-test in
    tests/unit/gateway/test_audit_postgres.py exercises directly.)

  - **Per-tenant serialization, not global**: the platform is schema-per-tenant (env.py sets
    `search_path` to the tenant's own schema; each tenant gets its own physical `audit_chain`
    table via `alembic -x tenant=<id> upgrade head`). `UNIQUE(prev_record_hash)` in 0002 is
    therefore already scoped to one tenant's chain — there is no cross-tenant chain to protect,
    and a *global* lock would serialize unrelated tenants against each other for no correctness
    benefit. `pg_advisory_xact_lock(hashtext($tenant_id))` serializes concurrent writers for the
    SAME tenant (same physical table) only; different tenants proceed fully in parallel.

  - **Advisory lock is the primary guard; UNIQUE(prev_record_hash) is belt-and-suspenders**: the
    lock makes the "read tail, compute hash, insert" sequence atomic per tenant, so two
    concurrent `emit()` calls for the same tenant can never observe the same tail. If that lock
    were ever bypassed (bug, a writer that doesn't use this sink, a second uncoordinated
    connection pool), the UNIQUE constraint on `prev_record_hash` still turns a concurrent fork
    attempt into a hard `UniqueViolationError` instead of a silently-accepted second chain —
    EXCEPT for genesis, where the column being nullable would let two NULLs both satisfy UNIQUE
    (Postgres treats NULL <> NULL). `AuditRecord.GENESIS_PREV_HASH` (a real 64-char sentinel,
    never SQL NULL) closes that gap so the belt-and-suspenders guard actually covers every
    record, including the first.

PHI: `AuditRecord.details` must already be safe-to-persist structured data by the time it reaches
this sink — this module never receives, hashes, or stores raw PHI; `input_hash` is a SHA-256 of
`details`, never the details themselves in identifiable form beyond what the caller put there.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog

from maezo.gateway.audit import GENESIS_PREV_HASH, AuditRecord, EmitOnceOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger(__name__)

# Postgres schema identifiers here are always the raw tenant id (env.py: `SEARCH_PATH =
# (TENANT_ID, "public")`, no prefix) — validated to prevent SQL injection via string-built
# `SET search_path` / advisory-lock statements. Intentionally conservative: lowercase
# snake_case starting with a letter, matching the tenant ids used elsewhere in the platform
# (e.g. "amh", "public").
_SAFE_TENANT_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class AuditPersistenceError(RuntimeError):
    """Raised when a durable audit write fails. Always propagates — never swallowed.

    Wraps the underlying asyncpg/DB error so callers get a stable, sink-agnostic exception
    type, while `__cause__` preserves the original for diagnostics.
    """


def schema_for_tenant(tenant_id: str) -> str:
    """Validate a tenant id as a safe Postgres schema identifier and return it.

    Raises ValueError (fail-closed) for anything that is not a plain lowercase
    identifier — this string is interpolated into `SET search_path` / advisory-lock
    statements, so it must never carry attacker- or bug-controlled SQL.
    """
    if not _SAFE_TENANT_ID.match(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} is not a valid schema identifier "
            "(expected [a-z][a-z0-9_]*, matching platform/migrations/env.py's tenant convention)"
        )
    return tenant_id


def normalize_dsn(dsn: str) -> str:
    """Convert a SQLAlchemy-style asyncpg DSN to the plain DSN the `asyncpg` driver expects.

    The platform's DATABASE_URL convention (alembic.ini, Helm's Aurora secret) is
    `postgresql+asyncpg://...` for SQLAlchemy. The `asyncpg` library used directly here (not
    through SQLAlchemy) does not understand the `+asyncpg` driver suffix — strip it.
    """
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


# Columns of `audit_chain`, in INSERT order. `id`/`created_at` are server-generated (DEFAULT
# gen_random_uuid() / now()) and deliberately omitted — see 0002_audit_chain.py.
_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "tenant_id",
    "agent_id",
    "agent_version",
    "action",
    "decision",
    "input_hash",
    "decision_basis",
    "dmn_versions",
    "model_id",
    "prompt_version",
    "record_hash",
    "prev_record_hash",
)

_INSERT_PLACEHOLDERS = ", ".join(
    f"${i}::jsonb" if col in ("decision_basis", "dmn_versions") else f"${i}"
    for i, col in enumerate(_COLUMNS, start=1)
)
INSERT_SQL = f"INSERT INTO audit_chain ({', '.join(_COLUMNS)}) VALUES ({_INSERT_PLACEHOLDERS})"

# Per-tenant transactional advisory lock — released automatically at COMMIT/ROLLBACK. Serializes
# the read-tail -> compute-hash -> insert critical section for one tenant's chain; see module
# docstring ("Per-tenant serialization, not global").
_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"

# Idempotency claim for exactly-once emission (emit_once) — audit_emit_dedup
# (0005_audit_emit_dedup.py). The read below returns the prior chain link's record_hash for an
# already-claimed key (so a re-delivered effect gets the SAME identity back, not an error); the
# claim INSERT below runs INSIDE emit()'s advisory-lock transaction so claim+chain-link commit
# atomically. `ON CONFLICT DO NOTHING` is belt-and-suspenders under the advisory lock (which
# already serializes claims per tenant): if the lock were ever bypassed, a colliding claim returns
# zero rows and emit_once fails closed rather than forking the chain.
_DEDUP_LOOKUP_SQL = "SELECT record_hash FROM audit_emit_dedup WHERE tenant = $1 AND dedup_key = $2"
_DEDUP_CLAIM_SQL = (
    "INSERT INTO audit_emit_dedup (tenant, dedup_key, record_hash) VALUES ($1, $2, $3) "
    "ON CONFLICT (tenant, dedup_key) DO NOTHING RETURNING record_hash"
)

# Structural tail lookup: the tail is the ONE record_hash that is never anyone's
# prev_record_hash. This is robust to wall-clock skew (unlike `ORDER BY timestamp`, which the
# v1 implementation's own comments flag as fragile across replicas/failover). Uses a correlated
# NOT EXISTS rather than `... NOT IN (SELECT prev_record_hash ...)`: prev_record_hash IS
# nullable at the schema level (0002_audit_chain.py), and SQL `NOT IN` against a set containing
# NULL evaluates to UNKNOWN for every row — silently returning zero tails even when one exists.
# NOT EXISTS has no such trap. (This sink never itself writes a NULL prev_record_hash — see
# GENESIS_PREV_HASH — but the query must not assume every past/future writer is equally careful.)
_TAIL_SQL = """
    SELECT record_hash FROM audit_chain t1
    WHERE NOT EXISTS (
        SELECT 1 FROM audit_chain t2 WHERE t2.prev_record_hash = t1.record_hash
    )
"""


def _decode_jsonb(value: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as raw text unless a codec is registered; decode defensively."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    result: Any = json.loads(value)
    if not isinstance(result, dict):
        raise AuditPersistenceError(f"expected a JSON object, got {type(result).__name__}")
    return result


class PostgresAuditSink:
    """Durable, fail-closed audit sink — write-through to `audit_chain` on every `emit()`.

    One sink instance is scoped to one tenant (one Postgres schema). The underlying asyncpg
    pool is created lazily on first `emit()`/use and configured with `setup=` (NOT `init=`):
    asyncpg pools run `RESET ALL` on connection release, which clears session state like
    `SET search_path`. `init=` only runs once per physical connection, so the 2nd+ `emit()` on
    a *reused* connection would silently fall back to the default search_path and fail to find
    `audit_chain` (a real bug class documented in docs/decisions-log.md DL-0017 for the sibling
    `PostgresMemoryStore`/checkpointer sinks). `setup=` re-applies the search_path on every
    acquire, which is what this sink uses.
    """

    def __init__(self, dsn: str, tenant_id: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._schema = schema_for_tenant(tenant_id)
        self._tenant_id = tenant_id
        self._dsn = normalize_dsn(dsn)
        self._pool = pool
        self._owns_pool = pool is None
        logger.info("postgres_audit_sink_initialized", tenant_id=tenant_id)

    async def _configure_connection(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by schema_for_tenant() in __init__ (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                # D4-02 / R-108 (OWNER-DECISIONS-REGISTER, opcao B): min_size/max_size abaixo sao
                # DEFAULT DELIBERADO, nao esquecimento. O dono decidiu MANTER estes valores e
                # documenta-los em vez de expor min/max por ambiente em values.yaml, adiando essa
                # exposicao para depois de SC-05 — sem a aritmetica agregada de conexoes (pools x
                # processos x replicas x tenants vs max_connections do Aurora) que SC-05 produz,
                # expor knobs transferiria para quem preencher o valor o risco de estourar o teto do
                # Aurora. Ha' QUATRO sitios com este MESMO default nesta arvore — a2a/idempotency.py,
                # a2a/outbox.py, platform/integrations/amh_inbox.py, gateway/audit_postgres.py —
                # mude os quatro juntos se mudar aqui. REABRE quando SC-05 landar (ver
                # docs/review-queue.md, secao "D4-02 — pools asyncpg sem tuning por ambiente").
                min_size=1,
                max_size=10,
                setup=self._configure_connection,
            )
        return self._pool

    async def check_ready(self) -> None:
        """Bounded connectivity probe for the fail-closed `audit_sink_ready` readiness gate (T-D).

        Proves the sink's pool can reach the tenant schema AND that `audit_chain` exists there —
        i.e. that a subsequent `emit()`/`emit_once()` could actually durably persist. `to_regclass`
        resolves against the connection's `search_path`, which `setup=self._configure_connection`
        pins to this tenant's schema on every acquire, so a `None` result means the chain table is
        absent from the tenant schema (migrations not applied) even if the server is reachable.

        Raises `AuditPersistenceError` (never swallowed) on ANY failure — no server, wrong schema,
        missing table. The worker daemon's readiness gate treats a raised probe as `/readyz` red and
        REFUSES to enter the fetch-and-lock rotation (ADR-0007 L0: an un-auditable daemon must not
        accept effect-producing work — design §7 T-D / Revision MUST-FIX 2). Read-only: acquires a
        pooled connection and runs one metadata lookup; it never writes to `audit_chain`.
        """
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                table = await conn.fetchval("SELECT to_regclass('audit_chain')")
        except Exception as exc:  # deliberately broad: FAIL CLOSED, always re-raise
            raise AuditPersistenceError(
                f"audit sink not ready for tenant={self._tenant_id!r} "
                f"(schema {self._schema!r} unreachable): {exc}"
            ) from exc
        if table is None:
            raise AuditPersistenceError(
                f"audit sink not ready for tenant={self._tenant_id!r}: `audit_chain` not found in "
                f"schema {self._schema!r} (migrations not applied) — cannot durably audit"
            )

    async def emit(self, record: AuditRecord) -> str:
        """Persist `record` as the next link in this tenant's chain. FAIL-CLOSED.

        Reads the current tail, chains `record` onto it, and inserts — all inside one
        transaction holding the per-tenant advisory lock, so concurrent `emit()` calls for the
        same tenant are fully serialized and can never observe (or write) a stale tail.

        Mutates `record.prev_hash`/`record.record_hash` in place (matching the in-memory
        `AuditSink.emit()` contract) and returns the record hash.

        Raises `AuditPersistenceError` (chaining the original DB error) on ANY failure —
        connection loss, lock timeout, constraint violation (e.g. a genuine concurrent fork
        slipping past the advisory lock somehow). Never returns a partial/best-effort result:
        an unauditable action must not proceed as if it had been audited.
        """
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(_ADVISORY_LOCK_SQL, self._tenant_id)
                tail = await self._fetch_tail(conn)

                record.prev_hash = tail
                record.record_hash = record._compute_hash()

                await self._insert_chain_row(conn, record)
        except AuditPersistenceError:
            raise
        except Exception as exc:  # deliberately broad: FAIL CLOSED, always re-raise
            logger.error(
                "audit_persistence_write_failed",
                tenant_id=self._tenant_id,
                agent_id=record.agent_id,
                action=record.action,
                error=str(exc),
            )
            raise AuditPersistenceError(
                f"failed to persist audit record for tenant={self._tenant_id!r} "
                f"agent={record.agent_id!r} action={record.action!r}: {exc}"
            ) from exc

        logger.debug(
            "audit_record_persisted",
            tenant_id=self._tenant_id,
            agent_id=record.agent_id,
            action=record.action,
            decision=record.decision,
            record_hash=record.record_hash,
        )
        return record.record_hash

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        """Hash-only view of `emit_once_status` — UNCHANGED contract for every existing caller.

        Returns the persisted (or deduped-prior) `record_hash`, discarding the dedup FLAG. Callers
        that need the flag (an effect gate, not just a double-audit guard) must call
        `emit_once_status` — see `EmitOnceOutcome` and `mcp_cibseven.transport.
        start_process_idempotent`. All the guarantees documented on `emit_once_status` hold here
        verbatim; this wrapper adds nothing but the narrowing.
        """
        return (await self.emit_once_status(record, dedup_key=dedup_key)).record_hash

    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
        """Idempotently persist `record` as the next chain link, keyed on `dedup_key`. FAIL-CLOSED.

        The exactly-once wrapper around `emit()` for effects the engine may re-deliver: it claims
        `dedup_key` in `audit_emit_dedup` (0005_audit_emit_dedup.py) and inserts the chain link
        INSIDE THE SAME per-tenant advisory-lock transaction, so the claim and the link are atomic
        (see docs/design/audit-emit-path-wiring.md §4.3):

          - **First emit for a key** → reads the tail, chains `record` onto it, claims the key
            (storing the new `record_hash`), inserts the chain link — all in one committed
            transaction. Returns `EmitOnceOutcome(record_hash=<new hash>, deduped=False)`.
          - **Second emit for the same key** (engine re-delivery of the same effect) → the claim
            already exists, so this is a NO-OP: NO second chain link is written, and the PRIOR
            link's `record_hash` is returned as `EmitOnceOutcome(..., deduped=True)`. Not an
            error — a re-delivered effect legitimately maps to the audit row already written for it.
          - **Crash between claim and chain-insert** → both roll back together (one transaction),
            so re-delivery re-emits cleanly: no dangling claim to suppress the retry (no gap), no
            orphan chain link for the retry to duplicate (no duplicate). This is the property the
            kill-test proves.

        `dedup_key` is supplied by the caller and identifies the logical effect. Its shape is a
        caller contract (T-C / T-C2 — not enforced here), and MUST be stable across the engine's
        re-delivery of one effect:
          - worker completions:  ``f"{tenant}:{task_id}"`` (the CIB Seven external-task id is
            stable across lock-expiry / failure-with-retries re-delivery);
          - process-start emits: ``f"{tenant}:start:{process_key}:{business_key}"``.

        Preserves every guarantee `emit()` makes: single-writer-per-tenant ordering
        (`pg_advisory_xact_lock`), a structurally-derived tail (no in-memory head cache), and the
        hash-chain integrity `verify_chain()` checks. The dedup lookup and claim are serialized
        with the tail read by the same advisory lock, so there is no TOCTOU between "already
        audited?" and "chain the link".

        Mutates `record.prev_hash`/`record.record_hash` in place on the first emit (as `emit()`
        does); on a dedup no-op the record is NOT chained (there is no new link) and the returned
        hash is the prior link's, read from the claim row.

        EFFECT-GATE GUARANTEE (what `deduped` buys, beyond the double-audit guard): the claim
        lookup, the claim INSERT and the chain INSERT all run under ONE `pg_advisory_xact_lock`
        on the tenant, inside ONE transaction. Concurrent callers for the same
        `(tenant, dedup_key)` are therefore SERIALIZED, and exactly ONE of them can ever observe
        `deduped=False`. A caller may thus treat `deduped=False` as "I hold the exclusive right to
        perform this effect" and `deduped=True` as "someone already committed to it" — a real
        mutual-exclusion gate, not a check-then-act. `mcp_cibseven.transport.
        start_process_idempotent` relies on exactly this for its strict process families.

        Raises `AuditPersistenceError` (chaining the original DB error) on ANY failure — an
        unauditable effect must not proceed as if it had been audited.
        """
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn, conn.transaction():
                outcome = await self.emit_once_on(conn, record, dedup_key=dedup_key)
        except AuditPersistenceError:
            raise
        except Exception as exc:  # deliberately broad: FAIL CLOSED, always re-raise
            logger.error(
                "audit_emit_once_write_failed",
                tenant_id=self._tenant_id,
                agent_id=record.agent_id,
                action=record.action,
                dedup_key=dedup_key,
                error=str(exc),
            )
            raise AuditPersistenceError(
                f"failed to persist audit record (emit_once) for tenant={self._tenant_id!r} "
                f"agent={record.agent_id!r} action={record.action!r} dedup_key={dedup_key!r}: {exc}"
            ) from exc

        if outcome.deduped:
            return outcome
        logger.debug(
            "audit_emit_once_persisted",
            tenant_id=self._tenant_id,
            agent_id=record.agent_id,
            action=record.action,
            decision=record.decision,
            dedup_key=dedup_key,
            record_hash=record.record_hash,
        )
        return outcome

    async def emit_once_on(
        self, conn: asyncpg.Connection, record: AuditRecord, *, dedup_key: str
    ) -> EmitOnceOutcome:
        """Enlist the original chain operation in the caller's tenant transaction.

        No acquire/commit here. Audit lock MUST precede task locks when combined
        with A2A idempotency (ADR-0007, DL-0017/18). Standalone emit_once retains
        its existing transaction and failure translation.
        """
        if record.tenant_id != self._tenant_id or not conn.is_in_transaction():
            raise AuditPersistenceError("invalid enlisted audit transaction")
        if await conn.fetchval("SELECT current_schema()") != self._schema:
            raise AuditPersistenceError("enlisted audit schema mismatch")
        await conn.execute(_ADVISORY_LOCK_SQL, self._tenant_id)

        # 1. Already audited? Serialized with the tail read below by the advisory lock —
        #    no TOCTOU. Returns the prior chain link's identity for a re-delivered effect.
        prior_hash: str | None = await conn.fetchval(_DEDUP_LOOKUP_SQL, self._tenant_id, dedup_key)
        if prior_hash is not None:
            logger.debug(
                "audit_emit_once_deduped",
                tenant_id=self._tenant_id,
                dedup_key=dedup_key,
                record_hash=prior_hash,
            )
            return EmitOnceOutcome(record_hash=prior_hash, deduped=True)

        # 2. First time for this effect — chain the record onto the current tail.
        tail = await self._fetch_tail(conn)
        record.prev_hash = tail
        record.record_hash = record._compute_hash()

        # 3. Claim the key AND insert the chain link, atomically. The claim carries the
        #    new record_hash so a later re-delivery gets it back (step 1). ON CONFLICT
        #    returning zero rows means a concurrent claim slipped past the advisory lock
        #    (should be impossible) — fail closed rather than fork the chain.
        claimed: str | None = await conn.fetchval(
            _DEDUP_CLAIM_SQL, self._tenant_id, dedup_key, record.record_hash
        )
        if claimed is None:
            raise AuditPersistenceError(
                f"audit_emit_dedup claim for tenant={self._tenant_id!r} "
                f"dedup_key={dedup_key!r} collided under the advisory lock "
                "(concurrent claim bypassed serialization) — refusing to fork the chain"
            )

        await self._insert_chain_row(conn, record)
        return EmitOnceOutcome(record_hash=record.record_hash, deduped=False)

    async def _insert_chain_row(self, conn: asyncpg.Connection, record: AuditRecord) -> None:
        """Insert `record` as a chain row on `conn`. Caller owns the advisory-locked transaction.

        Shared by `emit()` and `emit_once()` — the single place the `audit_chain` INSERT is issued,
        so the two write paths can never drift in the columns/order they persist. Assumes
        `record.prev_hash`/`record.record_hash` are already set for the current tail.
        """
        await conn.execute(
            INSERT_SQL,
            record.timestamp,
            record.tenant_id,
            record.agent_id,
            record.agent_version,
            record.action,
            record.decision,
            record.compute_input_hash(),
            json.dumps(record.details, sort_keys=True, default=str),
            json.dumps(record.dmn_versions, sort_keys=True, default=str),
            record.model_id,
            record.prompt_version,
            record.record_hash,
            record.prev_hash,
        )

    async def _fetch_tail(self, conn: asyncpg.Connection) -> str:
        """Return the current chain tail, or GENESIS_PREV_HASH if the chain is empty.

        Must be called while holding the per-tenant advisory lock (see `emit`) — otherwise two
        concurrent callers could both observe the same tail and race to insert onto it (the
        UNIQUE constraint would then reject the second, per-design, but that surfaces as an
        avoidable `AuditPersistenceError` instead of clean serialization).

        Raises `AuditPersistenceError` if more than one tail is found — that means the chain
        already forked (corruption, a bypassed lock, manual tampering) and this sink refuses to
        silently pick one arm and keep writing.
        """
        rows = await conn.fetch(_TAIL_SQL + " LIMIT 2")
        if len(rows) > 1:
            raise AuditPersistenceError(
                f"audit_chain for tenant={self._tenant_id!r} has multiple tails "
                f"(chain already forked) — refusing to write until resolved"
            )
        if not rows:
            return GENESIS_PREV_HASH
        tail_hash: str = rows[0]["record_hash"]
        return tail_hash

    async def aclose(self) -> None:
        """Close the underlying pool, if this sink created it."""
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None


class FreshSinkAuditEmitter:
    """Loop-agnostic per-call adapter over `PostgresAuditSink` (T1.10 wave integration).

    WHY THIS EXISTS: `PostgresAuditSink`'s asyncpg pool is created lazily and BINDS to the event
    loop that first uses it. That is correct for the worker harness (one long-lived loop), but a
    SYNC worker handler that needs to emit — `inadimplencia.handoff_rescisao`'s audited CANCEL-001
    start through the T-C2 `start_process_idempotent` fence — runs each dispatch on its own fresh
    `asyncio.run` loop. Threading the daemon's pooled sink into that seam would produce the exact
    cross-loop asyncpg failure class `FreshClientCibSevenTransport` (tools/workers/
    cibseven_engine.py) exists to prevent on the engine side. This adapter is the sink-side
    mirror of that pattern: construct a fresh `PostgresAuditSink` INSIDE the calling loop, emit,
    and close it before the loop dies — nothing pooled ever outlives or crosses a loop.

    Fail-closed by construction: emit failures propagate as the delegate's
    `AuditPersistenceError` — no fallback, no swallow. A close-time failure AFTER a successful
    emit also propagates (fail-closed direction: the row is durably persisted, and a caller retry
    dedups to the same chain link via `emit_once`, so raising loses nothing and hides nothing).
    Satisfies both seam Protocols structurally (`AuditEmitter` in tools/workers/harness.py and
    `AuditStartSink` in tools/mcp_cibseven/transport.py). Construction is pure (no I/O): a bad
    DSN/tenant surfaces on the first emit, which raises — never a silent no-op.

    One connection-pool setup per emit is the deliberate price of loop-safety at this seam — the
    handoff is a rare human-gated event (ENCAMINHAR_RESCISAO), not a hot path; the harness's own
    completion emits keep using the pooled sink on the daemon's main loop.
    """

    def __init__(self, dsn: str, tenant_id: str) -> None:
        # Validate the tenant eagerly (same fail-closed posture as PostgresAuditSink itself).
        schema_for_tenant(tenant_id)
        self._dsn = dsn
        self._tenant_id = tenant_id

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        """Durable exactly-once emit via a fresh, per-call `PostgresAuditSink`. FAIL-CLOSED."""
        return (await self.emit_once_status(record, dedup_key=dedup_key)).record_hash

    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
        """`emit_once` plus the durable dedup FLAG — satisfies `DedupReportingAuditSink` (B-3).

        Forwarded rather than dropped so this adapter can front a GATED-posture process start
        (`mcp_cibseven.transport._START_DEDUP_POLICY`). No longer merely latent: the seam wired
        through here — `inadimplencia.handoff_rescisao`'s CANCEL-001 start — targets an
        `EXCLUSIVE` family since GAP-D3-02, so an adapter that exposed only the hash would make
        that start fail closed at the gate probe (`StartDedupGateUnavailableError`).
        """
        sink = PostgresAuditSink(self._dsn, self._tenant_id)
        try:
            return await sink.emit_once_status(record, dedup_key=dedup_key)
        finally:
            await sink.aclose()


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    """Result of `verify_chain()` — recomputed, not trusted from stored flags."""

    tenant_id: str
    valid: bool
    total_records: int
    verified_records: int
    reason: str | None = None
    break_at_hash: str | None = None


def _row_to_record(row: asyncpg.Record) -> AuditRecord:
    """Reconstruct an `AuditRecord` from a stored `audit_chain` row for hash recomputation.

    `input_hash` is intentionally NOT read back from the row and fed in — it is always
    recomputed from `decision_basis` (see `AuditRecord.compute_input_hash`), so a row tampered
    to change `decision_basis` without also updating the stored `input_hash` (or vice versa)
    is caught as a hash mismatch either way.
    """
    return AuditRecord(
        agent_id=row["agent_id"],
        tenant_id=row["tenant_id"],
        agent_version=row["agent_version"],
        action=row["action"],
        decision=row["decision"],
        details=_decode_jsonb(row["decision_basis"]),
        timestamp=row["timestamp"],
        dmn_versions=_decode_jsonb(row["dmn_versions"]),
        model_id=row["model_id"],
        prompt_version=row["prompt_version"],
        prev_hash=row["prev_record_hash"],
        record_hash=row["record_hash"],
    )


async def verify_chain(dsn: str, tenant_id: str) -> ChainVerificationResult:
    """Recompute and verify the stored hash chain for one tenant.

    Walks the chain structurally from genesis (via `prev_record_hash` links, not `timestamp` —
    see `_TAIL_SQL`'s docstring on clock-skew fragility) and, for every record, recomputes the
    hash from the STORED row content and checks it against the stored `record_hash`, and checks
    that `prev_record_hash` correctly links to the previous record's `record_hash`.

    A tenant's chain lives entirely within that tenant's own schema (schema-per-tenant), so
    verification is inherently scoped to `tenant_id` — there is no cross-tenant chain to walk.

    Detects two independent failure modes:
      - tamper: a stored row's recomputed hash does not match its stored `record_hash`.
      - gap/fork: some rows are not reachable by following prev_record_hash links from genesis
        (`verified_records < total_records`) — e.g. an orphaned branch, a hole left by a partial
        write that somehow bypassed this sink's transactional guarantee.

    Returns a `ChainVerificationResult`; never raises for a merely-invalid chain (that is the
    expected, reportable outcome of this function) — only for connection-level failures.
    """
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema}"')
        rows = await conn.fetch("SELECT * FROM audit_chain")
    finally:
        await conn.close()

    total = len(rows)
    if total == 0:
        return ChainVerificationResult(tenant_id=tenant_id, valid=True, total_records=0, verified_records=0)

    by_prev: dict[str, asyncpg.Record] = {}
    for row in rows:
        prev = row["prev_record_hash"]
        if prev in by_prev:
            return ChainVerificationResult(
                tenant_id=tenant_id,
                valid=False,
                total_records=total,
                verified_records=0,
                reason=f"fork detected: two records share prev_record_hash={prev!r}",
                break_at_hash=prev,
            )
        by_prev[prev] = row

    verified = 0
    current = by_prev.get(GENESIS_PREV_HASH)
    while current is not None:
        record = _row_to_record(current)
        expected_hash = record._compute_hash()
        if expected_hash != current["record_hash"]:
            return ChainVerificationResult(
                tenant_id=tenant_id,
                valid=False,
                total_records=total,
                verified_records=verified,
                reason="hash mismatch — record content does not match its stored record_hash",
                break_at_hash=current["record_hash"],
            )
        verified += 1
        current = by_prev.get(current["record_hash"])

    if verified != total:
        return ChainVerificationResult(
            tenant_id=tenant_id,
            valid=False,
            total_records=total,
            verified_records=verified,
            reason=(
                f"{total - verified} record(s) unreachable from genesis by following "
                "prev_record_hash links (gap or disconnected fork)"
            ),
        )

    return ChainVerificationResult(
        tenant_id=tenant_id, valid=True, total_records=total, verified_records=verified
    )


def _cli(argv: Sequence[str] | None = None) -> int:
    """`python -m maezo.gateway.audit_postgres --dsn ... --tenant ...` chain-verification CLI."""
    import asyncio

    parser = argparse.ArgumentParser(description="Verify a tenant's Postgres-backed audit chain.")
    parser.add_argument("--dsn", required=True, help="Postgres DSN (postgresql[+asyncpg]://...)")
    parser.add_argument("--tenant", required=True, help="Tenant id (schema name)")
    args = parser.parse_args(argv)

    result = asyncio.run(verify_chain(args.dsn, args.tenant))
    print(  # CLI output, not logging
        json.dumps(
            {
                "tenant_id": result.tenant_id,
                "valid": result.valid,
                "total_records": result.total_records,
                "verified_records": result.verified_records,
                "reason": result.reason,
                "break_at_hash": result.break_at_hash,
            },
            indent=2,
        )
    )
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(_cli())
