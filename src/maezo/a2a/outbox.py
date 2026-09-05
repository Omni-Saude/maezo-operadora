"""Transactional OUTBOX for A2A delegation facts — the durable replacement for `_NoopKafkaProducer`.

**What was broken.** `runtime/agent_runtime/a2a_composition.py` built BOTH A2A composition roots as
`FactProducer(kafka_producer or _NoopKafkaProducer())`, and `_NoopKafkaProducer.send` is `return
None`. Every `agents.events.delegation.{requested,completed,rejected}` fact this platform produced
was DROPPED at the instant of emission. The durable T-F audit chain never depended on those facts
(that rides `PostgresAuditSink`, and `_execute` audits BEFORE it emits), so nothing about the audit
proof was ever at risk — but the entire ADR-0003 observability/reactive surface was fiction.

**What this module is.** The write side of the fix: a `KafkaLike`-shaped producer whose `send`
INSERTs the fact into `a2a_fact_outbox` (migration `0008_a2a_fact_outbox.py`) instead of dropping
it, plus the claim/mark repository the relay (`maezo.a2a.outbox_relay`) drains. Postgres is the
only durable store in this platform (`PostgresIdempotencyStore`/`PostgresAuditSink`), so it is the
only place an outbox can live.

Structure mirrors `idempotency.py` deliberately — same asyncpg posture (lazy pool, `setup=` rather
than `init=` so `search_path` survives asyncpg's connection RESET on release, `normalize_dsn` +
`schema_for_tenant` from `gateway.audit_postgres`), same "pure functions the tests can reach
without a database" split, same module-level SQL constants.

=================================================================================================
Transaction boundary — stated precisely, including where it does NOT exist
=================================================================================================
An outbox is only worth the name if the fact row and the effect it describes commit together.
There are exactly two shapes here, and only one of them exists in production TODAY.

**(1) ENLISTED (`outbox_transaction`) — genuinely same-transaction.** A caller that already owns an
asyncpg connection inside a transaction binds it with `async with outbox_transaction(conn):`. Every
`send()` on that async task then INSERTs on THAT connection, inside THAT transaction: the caller's
rollback discards the fact row, the caller's commit publishes exactly one. This is real atomicity,
and `tests/unit/a2a/test_outbox_live_pg.py` proves both directions against a real server.

**(2) AMBIENT-LESS (no enlistment) — its own single-statement transaction.** With nothing bound,
`send()` acquires from the outbox's own pool and INSERTs in its own transaction. The row is atomic
in itself; it is NOT atomic with anything else.

**Which one the live A2A edges use today: (2). No production call site enlists.** That is a
statement of fact, not a design aspiration, and it is worth being blunt about because the phrase
"transactional outbox" invites the opposite assumption. `DelegationDispatcher._execute` performs
three durable writes through three INDEPENDENT asyncpg pools —
`PostgresAuditSink.emit_once` (audit_chain), this outbox (a2a_fact_outbox), and
`PostgresIdempotencyStore.claim_or_get`/`complete` (a2a_idempotency) — and there is no ambient
transaction spanning them. Joining them would mean threading one connection through the audit
sink, the dispatcher and the store: a change to `AuditEmitter`'s seam contract and to the
dispatcher's constructor, i.e. exactly the seam surface this leg is forbidden to widen. So the
enlistment mechanism ships, is tested, and is documented as UNUSED by the current roots — see the
report's open questions rather than a claim of atomicity that does not hold.

What (2) DOES buy over the noop, precisely: the fact is durable and redeliverable instead of gone.
What it does NOT buy: if the process dies between the audit row committing and the outbox INSERT
committing, that fact is lost — the same window `_audit_delegation_outcome` -> `_emit(COMPLETED)`
already has for the audit row itself.

=================================================================================================
Failure posture: an outbox write PROPAGATES. Chosen, not inherited.
=================================================================================================
`FactProducer.emit` is awaited inside `DelegationDispatcher._execute`, so a raise here fails the
delegation. The noop could never raise, so this IS a behavior change and it deserves an argument
rather than a shrug:

  * It adds no new dependency. `_execute` calls `_audit_delegation` -> `emit_once` (a Postgres
    write) BEFORE the first `_emit`. A Postgres outage already fails the delegation before any
    fact is emitted; this write is on the same database.
  * The alternative is the defect. Swallowing a failed outbox INSERT is `_NoopKafkaProducer` with
    extra steps and a lower drop rate — the thing this leg exists to delete.
  * `events_kafka_producer.BEST_EFFORT_TOPICS` is NOT a counter-precedent: its argument is
    explicitly "no BPMN gateway routes on this, so a stall would be silent-forever" for a NETWORK
    publish to a broker that may be down. This is a local write to a database the caller already
    requires.

The residual, stated rather than hidden: `_emit(COMPLETED)` runs AFTER the handler has already had
its effect and BEFORE `store.complete()` seals idempotency, so a raise there means a retry may
re-execute the handler. That window is not new — `_audit_delegation_outcome` (also a Postgres
write) sits immediately before it and has exactly the same property. This widens an existing
window by one statement; it does not open a new class of hazard. The operational consequence is
ordinary and must be stated anyway: migration 0008 must be applied BEFORE this code is deployed,
or every delegation fails loudly on a missing relation. Loudly is the point — the prior behavior
was to fail silently, forever.

=================================================================================================
PHI
=================================================================================================
`DelegationFact` is `task_id`/chain/type/route/reason/`output_ref` only, and `facts.build_fact`
refuses to let `meta` enter the fact at all ("reserved for extension; never enters the fact
(avoids accidental PHI)"). `output_ref` is a business-key/FHIR reference by `HandlerOutput`'s own
contract. So the bytes stored here are the same bytes that would have gone on the wire, and
nothing in this module ever logs a payload — only topic, dedup_key, row id and counts.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

logger = structlog.get_logger(__name__)

#: The table `0008_a2a_fact_outbox.py` creates. Unqualified — the tenant schema comes from the
#: connection's `search_path` (DL-0017), exactly as in `idempotency.py`.
OUTBOX_TABLE: Final[str] = "a2a_fact_outbox"

#: The alembic revision whose DDL this module's SQL speaks. Pinned so the structural test can
#: assert the two agree instead of a reader assuming it.
OUTBOX_MIGRATION_REVISION: Final[str] = "0008"

# Row lifecycle (mirrors the `ck_a2a_fact_outbox_status` CHECK — the test asserts the two agree).
STATUS_PENDING: Final[str] = "pending"
STATUS_CLAIMED: Final[str] = "claimed"
STATUS_DELIVERED: Final[str] = "delivered"

#: Every legal `status` value, in the order the DDL lists them.
OUTBOX_STATUSES: Final[tuple[str, ...]] = (STATUS_PENDING, STATUS_CLAIMED, STATUS_DELIVERED)

#: Default lease length for a claimed batch. Long enough that an ordinary broker publish finishes
#: inside it; short enough that a crashed relay's rows return to the pool within a minute rather
#: than waiting for an operator. Overridable per call — never a hidden constant.
DEFAULT_CLAIM_TTL_S: Final[float] = 60.0

#: Default rows per drain batch.
DEFAULT_BATCH_SIZE: Final[int] = 100

#: `last_error` is triage text, not a log sink: truncated so a pathological driver repr cannot
#: grow the row unboundedly.
_MAX_LAST_ERROR_CHARS: Final[int] = 500


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

#: Enqueue. `status`/`attempts`/`created_at` take their DDL defaults ('pending'/0/now()) — the
#: INSERT names only what the caller actually knows, so a default change is a migration concern
#: and not silently overridden here.
ENQUEUE_SQL: Final[str] = (
    "INSERT INTO a2a_fact_outbox (tenant, dedup_key, topic, partition_key, payload) "
    "VALUES ($1, $2, $3, $4, $5) "
    "RETURNING id"
)

#: Lease a batch. Two things make this crash-safe and multi-worker-safe at once:
#:
#:   * the inner SELECT takes `pending` rows AND `claimed` rows whose lease has EXPIRED, so
#:     recovery from a crashed relay needs no recovery process — the sweep IS the claim; and
#:   * `FOR UPDATE SKIP LOCKED` lets N relay workers drain the same table concurrently without
#:     blocking each other or handing the same row to two workers in the same instant.
#:
#: `attempts` increments on every claim (not on every failure), so it counts DELIVERY ATTEMPTS
#: including the ones that died mid-publish — which is the number an operator actually wants when
#: asking "why is this row still here?".
CLAIM_SQL: Final[str] = (
    "UPDATE a2a_fact_outbox AS o "
    "SET status = 'claimed', "
    "    claimed_at = now(), "
    "    claim_expires_at = now() + make_interval(secs => $1::double precision), "
    "    claimed_by = $2, "
    "    attempts = o.attempts + 1 "
    "WHERE o.id IN ( "
    "    SELECT c.id FROM a2a_fact_outbox AS c "
    "    WHERE c.status = 'pending' "
    "       OR (c.status = 'claimed' AND c.claim_expires_at <= now()) "
    "    ORDER BY c.created_at, c.id "
    "    FOR UPDATE SKIP LOCKED "
    "    LIMIT $3 "
    ") "
    "RETURNING o.id, o.tenant, o.dedup_key, o.topic, o.partition_key, o.payload, o.attempts"
)

#: Seal a published batch. Guarded on `claimed_by` as well as `status`: if this worker's lease
#: expired and ANOTHER worker re-claimed the row, this UPDATE matches nothing and the other
#: worker's publish/mark wins — instead of this worker marking a row it no longer owns as
#: delivered while the new owner is still publishing it.
#:
#: NOT guarded on `claim_expires_at > now()`: a publish that overran its lease with nobody else
#: having taken the row should still seal. Guarding on the clock would manufacture duplicates that
#: no correctness requirement asks for.
MARK_DELIVERED_SQL: Final[str] = (
    "UPDATE a2a_fact_outbox "
    "SET status = 'delivered', delivered_at = now(), claim_expires_at = NULL, "
    "    claimed_by = NULL, last_error = NULL "
    "WHERE id = ANY($1::bigint[]) AND status = 'claimed' AND claimed_by = $2 "
    "RETURNING id"
)

#: Release a row whose publish FAILED back to `pending`, recording why. `attempts` is left at the
#: value the claim already incremented — it is not decremented, so a permanently-failing row is
#: visible as a large `attempts` rather than looking untouched.
MARK_FAILED_SQL: Final[str] = (
    "UPDATE a2a_fact_outbox "
    "SET status = 'pending', claimed_at = NULL, claim_expires_at = NULL, claimed_by = NULL, "
    "    last_error = $3 "
    "WHERE id = ANY($1::bigint[]) AND status = 'claimed' AND claimed_by = $2 "
    "RETURNING id"
)

#: Operational counters for the relay's log line and for tests. Deliberately NOT part of the drain
#: path.
COUNT_BY_STATUS_SQL: Final[str] = "SELECT status, count(*) AS n FROM a2a_fact_outbox GROUP BY status"


# ---------------------------------------------------------------------------
# Fail-closed payload contract
# ---------------------------------------------------------------------------


class MalformedFactError(ValueError):
    """A value handed to the outbox producer is not a `DelegationFact.to_value()` encoding.

    Fail-closed, for the same reason `notifications_bridge.MalformedBridgeMessageError` is: the
    ONLY caller is `FactProducer.emit`, which always passes `DelegationFact.to_value()`. A value
    that cannot yield a dedup key is therefore a programming error, and enqueueing it under a
    fabricated key would put a row in the outbox that no consumer can deduplicate — silently
    converting an at-least-once pipeline into an at-least-once pipeline WITH duplicates nobody can
    collapse. Raising says so at the call site instead.

    Carries the offending KEYS only, never the value bytes (the payload is PHI-safe by
    `DelegationFact`'s contract, but this exception can be logged by callers that make no such
    assumption).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"a2a outbox: not a DelegationFact payload ({reason})")


def fact_dedup_key(*, tenant: str, task_id: str, kind: str) -> str:
    """The consumer-side dedup key: `{tenant}:a2a:delegate:{task_id}:{kind}`.

    Pure. Same construction as `dispatcher.a2a_audit_dedup_key`
    (`{tenant}:a2a:delegate:{task_id}`) and `a2a_audit_outcome_dedup_key` (`...:outcome`), with the
    fact KIND appended because one delegation legitimately emits a `requested` AND a terminal
    `completed`/`rejected` — collapsing those onto one key would make the terminal fact invisible.

    STABLE ACROSS REDELIVERY by construction: it is derived only from the payload, so the relay
    republishing the same row after a crash produces the identical key, which is exactly what
    lets a consumer collapse the duplicate.
    """
    return f"{tenant}:a2a:delegate:{task_id}:{kind}"


def outbox_row_params(topic: str, value: bytes, key: bytes | None) -> tuple[str, str, str, str | None, bytes]:
    """`(topic, value, key)` -> the five `ENQUEUE_SQL` bind parameters. Pure; testable without a DB.

    Decodes `value` as the canonical `DelegationFact.to_value()` JSON object and reads `tenant`,
    `task_id` and `kind` from it — the three fields `DelegationFact.to_value` ALWAYS writes
    (they are non-optional dataclass fields, unlike `reason`/`output_ref`). Anything else raises
    `MalformedFactError`.

    The stored `payload` is `value` UNCHANGED — the bytes are not re-encoded, so what the relay
    later hands the broker is byte-identical to what a direct producer would have sent (see the
    module docstring and migration 0008 on why that matters for ADR-0039).
    """
    try:
        decoded: Any = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedFactError(f"undecodable payload: {type(exc).__name__}") from exc
    if not isinstance(decoded, dict):
        raise MalformedFactError(f"expected a JSON object, got {type(decoded).__name__}")
    missing = [field for field in ("tenant", "task_id", "kind") if not isinstance(decoded.get(field), str)]
    if missing:
        raise MalformedFactError(f"missing/non-string field(s): {sorted(missing)}")
    tenant = str(decoded["tenant"])
    dedup_key = fact_dedup_key(tenant=tenant, task_id=str(decoded["task_id"]), kind=str(decoded["kind"]))
    partition_key = key.decode("utf-8") if key is not None else None
    return tenant, dedup_key, topic, partition_key, value


# ---------------------------------------------------------------------------
# Enlistment — the ambient transaction seam
# ---------------------------------------------------------------------------

#: The connection `send()` writes on when a caller has enlisted one. A `ContextVar` (not an
#: attribute) because the enlisting caller and the producer are separated by
#: `FactProducer.emit` -> `DelegationDispatcher._emit` -> `_execute` -> `delegate`, none of which
#: has a parameter to thread a connection through and none of which this leg may widen. A
#: ContextVar is inherited by awaited coroutines within the same task, which is exactly the scope
#: "this delegation" occupies, and is NOT shared with concurrent tasks — two delegations running
#: concurrently on the same loop cannot see each other's transaction.
_ENLISTED_CONNECTION: ContextVar[Any | None] = ContextVar("maezo_a2a_outbox_connection", default=None)


@asynccontextmanager
async def outbox_transaction(conn: Any) -> AsyncIterator[Any]:
    """Bind `conn` as the connection every `send()` in this async context INSERTs on.

    The caller owns the transaction and the connection's `search_path`; this only says WHERE the
    fact rows go. Usage:

        async with pool.acquire() as conn, conn.transaction(), outbox_transaction(conn):
            ...          # the caller's own effect, on `conn`
            await dispatcher.delegate(envelope)   # its facts land on `conn`

    A rollback of the caller's transaction therefore discards the fact rows, and a commit
    publishes exactly one row per emitted fact — real same-transaction atomicity.

    Reset is `finally`-guarded with the `Token` (never `set(None)`), so nesting restores the OUTER
    binding rather than clearing it.
    """
    token = _ENLISTED_CONNECTION.set(conn)
    try:
        yield conn
    finally:
        _ENLISTED_CONNECTION.reset(token)


def current_outbox_connection() -> Any | None:
    """The enlisted connection for this async context, or `None`. Read-only accessor for tests."""
    return _ENLISTED_CONNECTION.get()


# ---------------------------------------------------------------------------
# Claimed rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One leased outbox row, as the relay sees it.

    `payload` is the verbatim `DelegationFact.to_value()` bytes; `partition_key` is the Kafka
    message key `FactProducer.emit` supplied (`fact.tenant`, today). `attempts` is the count
    INCLUDING this claim.
    """

    id: int
    tenant: str
    dedup_key: str
    topic: str
    partition_key: str | None
    payload: bytes
    attempts: int

    @classmethod
    def from_row(cls, row: Any) -> OutboxRecord:
        """Map a `CLAIM_SQL` row. Pure enough to test against a plain mapping."""
        return cls(
            id=int(row["id"]),
            tenant=str(row["tenant"]),
            dedup_key=str(row["dedup_key"]),
            topic=str(row["topic"]),
            partition_key=None if row["partition_key"] is None else str(row["partition_key"]),
            payload=bytes(row["payload"]),
            attempts=int(row["attempts"]),
        )


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


class PostgresFactOutbox:
    """The `a2a_fact_outbox` repository: enqueue (write side) + claim/mark (relay side).

    Lazy asyncpg pool, `search_path` pinned to the tenant's schema on EVERY acquire via `setup=`
    (not `init=`) — asyncpg RESETs session state on release, so `init=` would leave the 2nd+
    statement on a reused connection looking in the wrong schema. That is fix #55 from
    `PostgresAuditSink`, re-stated here for the same reason `PostgresIdempotencyStore` re-states
    it: it is a real production bug, not a style preference.

    `tenant` fixes the SCHEMA. It does NOT filter the drain: the schema already scopes the relay,
    and a fact whose own `tenant` differs (a rejected cross-tenant envelope) must still drain —
    see migration 0008's docstring.
    """

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        # Validates the tenant as a schema identifier (anti-injection) — same rule as the
        # checkpointer / audit sink / idempotency store.
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    @property
    def tenant(self) -> str:
        return self._tenant

    @property
    def schema(self) -> str:
        return self._schema

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
                setup=self._set_search_path,
            )
        return self._pool

    async def _set_search_path(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by `schema_for_tenant()` in `__init__` (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')

    # -- write side ---------------------------------------------------------

    async def enqueue(self, topic: str, value: bytes, *, key: bytes | None = None) -> int:
        """Persist one fact. Returns the row id.

        Writes on the ENLISTED connection when one is bound (`outbox_transaction`) — joining the
        caller's transaction, so their rollback discards this row. Otherwise acquires from this
        store's own pool and commits its own single-statement transaction. See the module
        docstring for which of the two the live A2A roots use today (the second).
        """
        tenant, dedup_key, topic_value, partition_key, payload = outbox_row_params(topic, value, key)
        enlisted = _ENLISTED_CONNECTION.get()
        if enlisted is not None:
            row_id = await enlisted.fetchval(
                ENQUEUE_SQL, tenant, dedup_key, topic_value, partition_key, payload
            )
        else:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn, conn.transaction():
                row_id = await conn.fetchval(
                    ENQUEUE_SQL, tenant, dedup_key, topic_value, partition_key, payload
                )
        logger.debug(
            "a2a_fact_outbox_enqueued",
            topic=topic_value,
            dedup_key=dedup_key,
            enlisted=enlisted is not None,
            row_id=row_id,
        )
        return int(row_id)

    # -- relay side ---------------------------------------------------------

    async def claim_batch(
        self,
        *,
        claimed_by: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
    ) -> list[OutboxRecord]:
        """Lease up to `batch_size` undelivered rows, oldest first.

        Takes `pending` rows AND `claimed` rows whose lease EXPIRED — so a relay that crashed
        mid-publish needs no recovery process: its rows come back to the pool on their own, and
        get republished. That is the at-least-once guarantee, and the duplicate it can produce is
        what `dedup_key` exists to let a consumer collapse.
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        if claim_ttl_s <= 0:
            raise ValueError(f"claim_ttl_s must be positive, got {claim_ttl_s}")
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(CLAIM_SQL, float(claim_ttl_s), claimed_by, int(batch_size))
        return [OutboxRecord.from_row(row) for row in rows]

    async def mark_delivered(self, ids: Sequence[int], *, claimed_by: str) -> int:
        """Seal rows this worker still owns as delivered. Returns how many actually sealed.

        A shortfall is not an error — it means a lease expired and another worker took the row,
        which the other worker will deliver. It IS logged, because a persistent shortfall means
        the lease is too short for the broker's real latency.
        """
        if not ids:
            return 0
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(MARK_DELIVERED_SQL, [int(i) for i in ids], claimed_by)
        sealed = len(rows)
        if sealed != len(ids):
            logger.warning(
                "a2a_fact_outbox_lease_lost",
                requested=len(ids),
                sealed=sealed,
                claimed_by=claimed_by,
                detail="rows were re-claimed by another relay before this one sealed them",
            )
        return sealed

    async def mark_failed(self, ids: Sequence[int], *, claimed_by: str, error: str) -> int:
        """Release rows whose publish failed back to `pending`, recording why. Returns the count."""
        if not ids:
            return 0
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                MARK_FAILED_SQL, [int(i) for i in ids], claimed_by, error[:_MAX_LAST_ERROR_CHARS]
            )
        return len(rows)

    async def counts_by_status(self) -> dict[str, int]:
        """`{status: rows}` for the relay's log line and for tests. Never on the drain hot path."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(COUNT_BY_STATUS_SQL)
        return {str(row["status"]): int(row["n"]) for row in rows}

    async def aclose(self) -> None:
        """Close the pool (if this store opened it)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class PostgresOutboxFactProducer:
    """`KafkaLike` over `PostgresFactOutbox` — what replaces `_NoopKafkaProducer` in both roots.

    Structurally satisfies `maezo.a2a.dispatcher.KafkaLike`
    (`async def send(self, topic, value, *, key=None) -> None`), so it drops into the EXISTING
    injection point — `FactProducer(kafka_producer or ...)` — with `FactProducer`,
    `DelegationDispatcher` and `GatedDelegationDispatcher`'s pinned `{delegate}` surface all
    untouched. The adapter is deliberately this thin: the repository is testable without a
    `FactProducer`, and the producer is substitutable without a repository.

    A write failure PROPAGATES (module docstring's "Failure posture" — chosen, argued, and with
    its residual stated).
    """

    def __init__(self, outbox: PostgresFactOutbox) -> None:
        self._outbox = outbox

    @property
    def outbox(self) -> PostgresFactOutbox:
        return self._outbox

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        await self._outbox.enqueue(topic, value, key=key)


def build_outbox_fact_producer(*, dsn: str, tenant: str) -> PostgresOutboxFactProducer:
    """One-call construction for a composition root (`dsn` + `tenant` -> a wired producer)."""
    return PostgresOutboxFactProducer(PostgresFactOutbox(dsn=dsn, tenant=tenant))


__all__ = [
    "CLAIM_SQL",
    "COUNT_BY_STATUS_SQL",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CLAIM_TTL_S",
    "ENQUEUE_SQL",
    "MARK_DELIVERED_SQL",
    "MARK_FAILED_SQL",
    "OUTBOX_MIGRATION_REVISION",
    "OUTBOX_STATUSES",
    "OUTBOX_TABLE",
    "STATUS_CLAIMED",
    "STATUS_DELIVERED",
    "STATUS_PENDING",
    "MalformedFactError",
    "OutboxRecord",
    "PostgresFactOutbox",
    "PostgresOutboxFactProducer",
    "build_outbox_fact_producer",
    "current_outbox_connection",
    "fact_dedup_key",
    "outbox_row_params",
    "outbox_transaction",
]
