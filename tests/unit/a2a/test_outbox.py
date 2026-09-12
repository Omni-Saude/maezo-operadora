"""Unit tests for the A2A fact OUTBOX write side — no Postgres.

Two-tier structure, mirroring `test_idempotency_store.py`:

  - PURE: the dedup key and the `(topic, value, key) -> bind params` projection, against REAL
    `DelegationFact.to_value()` bytes, plus the fail-closed refusal for anything that is not one.
  - FAKE-CONN: a captured-SQL asyncpg double proving `enqueue`/`claim_batch`/`mark_*` bind what
    they claim, that the producer satisfies the `KafkaLike` shape the composition roots inject it
    at, and — the important one — that `outbox_transaction` ENLISTMENT actually redirects the
    INSERT onto the caller's connection instead of the store's own pool.

The LIVE-Postgres tier (real DDL, real rollback, real lease expiry) is
`tests/unit/a2a/test_outbox_live_pg.py`.

EXPECTATION-TABLE PROVENANCE: `_DEDUP_KEY_CASES` is written from the documented shape
`{tenant}:a2a:delegate:{task_id}:{kind}` — the construction `dispatcher.a2a_audit_dedup_key`
already established (`{tenant}:a2a:delegate:{task_id}`), extended with the kind. The expected
strings are typed literally, NEVER built by calling `fact_dedup_key` or by f-string-ing the same
parts the function under test uses.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from maezo.a2a import FactProducer
from maezo.a2a.dispatcher import KafkaLike
from maezo.a2a.facts import DelegationFactKind, build_fact
from maezo.a2a.outbox import (
    CLAIM_SQL,
    ENQUEUE_SQL,
    MARK_DELIVERED_SQL,
    MARK_FAILED_SQL,
    MalformedFactError,
    OutboxRecord,
    PostgresFactOutbox,
    PostgresOutboxFactProducer,
    build_outbox_fact_producer,
    current_outbox_connection,
    fact_dedup_key,
    outbox_row_params,
    outbox_transaction,
)

_DSN = "postgresql://maezo:maezo@localhost:5433/maezo"
_TENANT = "amh"


# ---------------------------------------------------------------------------
# Fakes (never imported by production code)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Fake asyncpg connection: captures every statement + args; programmable replies."""

    def __init__(self, *, fetchval_result: Any = 1, fetch_result: Any = None) -> None:
        self.in_transaction = False
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchval_result = fetchval_result
        self._fetch_result = fetch_result if fetch_result is not None else []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.executed.append((sql, args))
        return "amh" if sql == "SELECT current_schema()" else self._fetchval_result

    async def fetch(self, sql: str, *args: Any) -> Any:
        self.executed.append((sql, args))
        return self._fetch_result

    def is_in_transaction(self) -> bool:
        return self.in_transaction

    def transaction(self) -> Any:
        conn = self

        @asynccontextmanager
        async def _tx() -> AsyncIterator[_FakeConn]:
            self.in_transaction = True
            try:
                yield conn
            finally:
                self.in_transaction = False

        return _tx()


class _FakePool:
    """Fake asyncpg pool handing out one `_FakeConn`."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.acquires = 0

    def acquire(self) -> Any:
        conn = self._conn
        self.acquires += 1

        @asynccontextmanager
        async def _acquire() -> AsyncIterator[_FakeConn]:
            yield conn

        return _acquire()

    async def close(self) -> None:
        return None


def _outbox(conn: _FakeConn) -> tuple[PostgresFactOutbox, _FakePool]:
    pool = _FakePool(conn)
    return PostgresFactOutbox(dsn=_DSN, tenant=_TENANT, pool=pool), pool


def _fact_bytes(
    kind: DelegationFactKind = DelegationFactKind.REQUESTED,
    *,
    tenant: str = "amh",
    task_id: str = "task-1",
) -> bytes:
    return build_fact(
        kind,
        task_id=task_id,
        task_type="authorization.analyze",
        tenant=tenant,
        origin="helena",
        target="rafael",
        delegation_chain=("helena", "rafael"),
    ).to_value()


# ---------------------------------------------------------------------------
# PURE: dedup key
# ---------------------------------------------------------------------------

#: (tenant, task_id, kind) -> the EXACT expected key. Literals, never re-derived. See the module
#: docstring for where the shape comes from.
_DEDUP_KEY_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("amh", "task-1", "requested", "amh:a2a:delegate:task-1:requested"),
    ("amh", "task-1", "completed", "amh:a2a:delegate:task-1:completed"),
    ("amh", "task-1", "rejected", "amh:a2a:delegate:task-1:rejected"),
    ("outro", "T-99", "requested", "outro:a2a:delegate:T-99:requested"),
)


@pytest.mark.parametrize(("tenant", "task_id", "kind", "expected"), _DEDUP_KEY_CASES)
def test_dedup_key_shape(tenant: str, task_id: str, kind: str, expected: str) -> None:
    assert fact_dedup_key(tenant=tenant, task_id=task_id, kind=kind) == expected


def test_the_three_fact_kinds_of_one_delegation_get_three_distinct_keys() -> None:
    """Collapsing `requested` and the terminal fact onto one key would make the terminal fact
    invisible to a consumer deduplicating on it."""
    keys = {fact_dedup_key(tenant="amh", task_id="t", kind=k.value) for k in DelegationFactKind}
    assert len(keys) == len(DelegationFactKind) == 3


def test_dedup_key_is_stable_across_redelivery_of_the_same_fact() -> None:
    """The at-least-once contract's linchpin: two emissions of the same fact — different
    timestamps, therefore different BYTES — must still yield the identical dedup key, because the
    key is derived only from (tenant, task_id, kind)."""
    first = _fact_bytes(DelegationFactKind.COMPLETED)
    second = _fact_bytes(DelegationFactKind.COMPLETED)
    # Non-vacuity: the two payloads really are different objects with their own timestamps.
    assert json.loads(first.decode())["kind"] == json.loads(second.decode())["kind"]
    assert outbox_row_params("t", first, None)[1] == outbox_row_params("t", second, None)[1]


# ---------------------------------------------------------------------------
# PURE: bind-parameter projection
# ---------------------------------------------------------------------------


def test_row_params_from_a_real_delegation_fact() -> None:
    value = _fact_bytes(DelegationFactKind.REQUESTED, tenant="amh", task_id="task-7")
    tenant, dedup_key, topic, partition_key, payload = outbox_row_params(
        "agents.events.delegation.requested", value, b"amh"
    )
    assert tenant == "amh"
    assert dedup_key == "amh:a2a:delegate:task-7:requested"
    assert topic == "agents.events.delegation.requested"
    assert partition_key == "amh"
    # The stored bytes are the SAME OBJECT the producer was handed — never re-encoded.
    assert payload is value


def test_row_params_carries_a_null_partition_key_when_none_is_supplied() -> None:
    assert outbox_row_params("t", _fact_bytes(), None)[3] is None


#: (label, raw bytes) -> must raise. Each is a real shape `FactProducer.emit` could never produce,
#: so reaching one means a caller bypassed the contract.
_MALFORMED_CASES: tuple[tuple[str, bytes], ...] = (
    ("not json", b"{not json"),
    ("json array", b'["a"]'),
    ("json scalar", b'"a string"'),
    ("missing tenant", b'{"task_id":"t","kind":"requested"}'),
    ("missing task_id", b'{"tenant":"amh","kind":"requested"}'),
    ("missing kind", b'{"tenant":"amh","task_id":"t"}'),
    ("non-string task_id", b'{"tenant":"amh","task_id":7,"kind":"requested"}'),
    ("invalid utf-8", b"\xff\xfe"),
)


@pytest.mark.parametrize(("label", "raw"), _MALFORMED_CASES, ids=[c[0] for c in _MALFORMED_CASES])
def test_malformed_payloads_fail_closed(label: str, raw: bytes) -> None:
    with pytest.raises(MalformedFactError):
        outbox_row_params("agents.events.delegation.requested", raw, None)


def test_malformed_error_never_carries_the_payload_bytes() -> None:
    """The message names the reason and the missing KEYS — never the value, since a caller that
    logs this exception must not have logged a payload it made no PHI assumption about."""
    with pytest.raises(MalformedFactError) as exc:
        outbox_row_params("t", b'{"tenant":"amh","task_id":"t"}', None)
    assert "kind" in str(exc.value)
    assert "amh" not in str(exc.value)


# ---------------------------------------------------------------------------
# FAKE-CONN: enqueue
# ---------------------------------------------------------------------------


async def test_enqueue_binds_the_documented_parameters() -> None:
    conn = _FakeConn(fetchval_result=42)
    outbox, _pool = _outbox(conn)
    row_id = await outbox.enqueue("agents.events.delegation.requested", _fact_bytes(), key=b"amh")

    assert row_id == 42
    sql, args = conn.executed[-1]
    assert sql == ENQUEUE_SQL
    assert args[0] == "amh"
    assert args[1] == "amh:a2a:delegate:task-1:requested"
    assert args[2] == "agents.events.delegation.requested"
    assert args[3] == "amh"
    assert isinstance(args[4], bytes)


async def test_producer_send_reaches_the_outbox_through_the_real_fact_producer() -> None:
    """The whole injection path, end to end without a database: `FactProducer.emit` ->
    `PostgresOutboxFactProducer.send` -> INSERT. This is the exact wiring the composition roots
    build, so it proves the replacement really is drop-in for `_NoopKafkaProducer`."""
    conn = _FakeConn()
    outbox, _pool = _outbox(conn)
    facts = FactProducer(PostgresOutboxFactProducer(outbox))

    fact = build_fact(
        DelegationFactKind.COMPLETED,
        task_id="task-9",
        task_type="credentialing.analyze",
        tenant="amh",
        origin="credenciamento-worker",
        target="carolina",
        delegation_chain=("credenciamento-worker", "carolina"),
        output_ref="process://CRED-amh-1",
    )
    await facts.emit(fact)

    sql, args = conn.executed[-1]
    assert sql == ENQUEUE_SQL
    assert args[1] == "amh:a2a:delegate:task-9:completed"
    assert args[2] == "agents.events.delegation.completed"
    assert json.loads(args[4].decode())["output_ref"] == "process://CRED-amh-1"


def test_producer_matches_the_kafkalike_protocol_signature() -> None:
    """Structural pin on the seam the composition roots inject at. `KafkaLike` is a plain
    (non-runtime-checkable) Protocol, so this compares the actual `send` signature instead of
    pretending `isinstance` would mean something."""
    import inspect

    expected = inspect.signature(KafkaLike.send)
    actual = inspect.signature(PostgresOutboxFactProducer.send)
    assert actual.parameters.keys() == expected.parameters.keys()
    assert [p.kind for p in actual.parameters.values()] == [p.kind for p in expected.parameters.values()]


def test_build_outbox_fact_producer_wires_dsn_and_tenant() -> None:
    producer = build_outbox_fact_producer(dsn=_DSN, tenant="amh")
    assert isinstance(producer, PostgresOutboxFactProducer)
    assert producer.outbox.tenant == "amh"
    assert producer.outbox.schema == "amh"


def test_tenant_is_validated_as_a_schema_identifier() -> None:
    """Anti-injection, same rule as the audit sink / idempotency store."""
    with pytest.raises(ValueError):
        PostgresFactOutbox(dsn=_DSN, tenant="not a schema; DROP SCHEMA public")


# ---------------------------------------------------------------------------
# FAKE-CONN: enlistment (the unit-level half of the atomicity claim)
# ---------------------------------------------------------------------------


async def test_enlisted_connection_receives_the_insert_and_the_pool_is_never_touched() -> None:
    """THE atomicity mechanism. With a caller's connection bound, the INSERT rides THAT connection
    — so the caller's transaction owns the fact row and their rollback discards it. The live-PG
    suite proves the rollback for real; this proves the routing."""
    pool_conn = _FakeConn()
    outbox, pool = _outbox(pool_conn)
    caller_conn = _FakeConn(fetchval_result=7)

    async with caller_conn.transaction(), outbox_transaction(caller_conn):
        row_id = await outbox.enqueue("t", _fact_bytes())

    assert row_id == 7
    assert [sql for sql, _ in caller_conn.executed] == ["SELECT current_schema()", ENQUEUE_SQL]
    assert pool_conn.executed == []
    assert pool.acquires == 0


async def test_without_enlistment_the_insert_goes_to_the_stores_own_pool() -> None:
    conn = _FakeConn()
    outbox, pool = _outbox(conn)
    await outbox.enqueue("t", _fact_bytes())
    assert pool.acquires == 1
    assert [sql for sql, _ in conn.executed] == [ENQUEUE_SQL]


async def test_enlistment_is_reset_after_the_context_exits_even_on_error() -> None:
    conn = _FakeConn()
    assert current_outbox_connection() is None
    with pytest.raises(RuntimeError):
        async with outbox_transaction(conn):
            assert current_outbox_connection() is conn
            raise RuntimeError("caller blew up")
    assert current_outbox_connection() is None


async def test_nested_enlistment_restores_the_outer_binding_not_none() -> None:
    """`reset(token)` rather than `set(None)` — nesting must not silently un-enlist the outer
    transaction, which would send its remaining facts to the pool and break its atomicity."""
    outer, inner = _FakeConn(), _FakeConn()
    async with outbox_transaction(outer):
        async with outbox_transaction(inner):
            assert current_outbox_connection() is inner
        assert current_outbox_connection() is outer
    assert current_outbox_connection() is None


# ---------------------------------------------------------------------------
# FAKE-CONN: relay-side SQL
# ---------------------------------------------------------------------------


async def test_claim_batch_binds_ttl_worker_and_limit_and_maps_rows() -> None:
    row = {
        "id": 5,
        "tenant": "amh",
        "dedup_key": "amh:a2a:delegate:t:requested",
        "topic": "agents.events.delegation.requested",
        "partition_key": "amh",
        "payload": b"{}",
        "attempts": 2,
    }
    conn = _FakeConn(fetch_result=[row])
    outbox, _pool = _outbox(conn)

    records = await outbox.claim_batch(claimed_by="host:1", batch_size=25, claim_ttl_s=30.0)

    sql, args = conn.executed[-1]
    assert sql == CLAIM_SQL
    assert args == (30.0, "host:1", 25)
    assert records == [
        OutboxRecord(
            id=5,
            tenant="amh",
            dedup_key="amh:a2a:delegate:t:requested",
            topic="agents.events.delegation.requested",
            partition_key="amh",
            payload=b"{}",
            attempts=2,
        )
    ]


@pytest.mark.parametrize(("batch_size", "ttl"), [(0, 30.0), (-1, 30.0), (10, 0.0), (10, -5.0)])
async def test_claim_batch_refuses_nonsense_bounds(batch_size: int, ttl: float) -> None:
    """A zero TTL would produce an already-expired lease — every claim instantly re-claimable by
    anyone, i.e. unbounded duplicate publishing. A zero batch is a silent no-op loop."""
    outbox, _pool = _outbox(_FakeConn())
    with pytest.raises(ValueError):
        await outbox.claim_batch(claimed_by="w", batch_size=batch_size, claim_ttl_s=ttl)


async def test_mark_delivered_guards_on_the_claiming_worker() -> None:
    conn = _FakeConn(fetch_result=[{"id": 1}, {"id": 2}])
    outbox, _pool = _outbox(conn)
    sealed = await outbox.mark_delivered([1, 2], claimed_by="host:1")
    sql, args = conn.executed[-1]
    assert sql == MARK_DELIVERED_SQL
    assert args == ([1, 2], "host:1")
    assert sealed == 2


async def test_mark_delivered_reports_a_shortfall_rather_than_raising() -> None:
    """A lost lease is a normal at-least-once outcome (the other worker delivers the row), not an
    error — but it must be COUNTED, so a persistently short lease is visible."""
    conn = _FakeConn(fetch_result=[{"id": 1}])
    outbox, _pool = _outbox(conn)
    assert await outbox.mark_delivered([1, 2, 3], claimed_by="host:1") == 1


async def test_mark_failed_truncates_the_error_and_keeps_the_worker_guard() -> None:
    conn = _FakeConn(fetch_result=[{"id": 1}])
    outbox, _pool = _outbox(conn)
    await outbox.mark_failed([1], claimed_by="host:1", error="x" * 5000)
    sql, args = conn.executed[-1]
    assert sql == MARK_FAILED_SQL
    assert args[1] == "host:1"
    assert len(args[2]) == 500


@pytest.mark.parametrize("method", ["mark_delivered", "mark_failed"])
async def test_marking_an_empty_id_list_is_a_no_op_and_touches_no_connection(method: str) -> None:
    conn = _FakeConn()
    outbox, pool = _outbox(conn)
    call = getattr(outbox, method)
    result = (
        await call([], claimed_by="w", error="e")
        if method == "mark_failed"
        else await call([], claimed_by="w")
    )
    assert result == 0
    assert pool.acquires == 0
