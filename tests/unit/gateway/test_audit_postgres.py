"""Tests for maezo.gateway.audit_postgres — durable Postgres audit sink (T1.10, B7).

Two tiers:

  - Pure unit tests (no DB): identifier validation, DSN normalization, SQL shape. Always run.

  - Postgres-backed tests (marked `@pytest.mark.integration`, matching the marker registered in
    pyproject.toml for "exige stack docker-compose"): exercise `PostgresAuditSink` against a
    REAL Postgres. Each test creates and tears down its own throwaway tenant schema. These
    SKIP (loudly, with a clear reason) if `MAEZO_TEST_DATABASE_URL` — or the docker-compose
    default `postgresql://maezo:maezo@localhost:5433/maezo` — is not reachable; they are never
    silently omitted, and they never fake a pass. Bring the stack up locally with
    `docker compose --profile core up -d postgres` to run this file's DB tests for real.

The kill-test (`test_kill_test_process_crash_loses_zero_committed_records`) is the acceptance
criterion for T1.10: spawn a subprocess writing records through PostgresAuditSink, SIGKILL it
mid-stream, restart a fresh writer process, and verify no committed record was lost and the
chain still verifies with no gaps.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit import AuditRecord, EmitOnceOutcome
from maezo.gateway.audit_postgres import (
    _COLUMNS,
    _DEDUP_CLAIM_SQL,
    _DEDUP_LOOKUP_SQL,
    INSERT_SQL,
    AuditPersistenceError,
    FreshSinkAuditEmitter,
    PostgresAuditSink,
    normalize_dsn,
    schema_for_tenant,
    verify_chain,
)

_KILLTEST_WRITER = Path(__file__).parent / "_audit_killtest_writer.py"
_KILLTEST_EMIT_ONCE_WRITER = Path(__file__).parent / "_audit_emit_once_killtest_writer.py"


# ---------------------------------------------------------------------------
# Pure unit tests — no DB required, always run.
# ---------------------------------------------------------------------------


def test_schema_for_tenant_accepts_valid_identifiers() -> None:
    assert schema_for_tenant("amh") == "amh"
    assert schema_for_tenant("public") == "public"
    assert schema_for_tenant("tenant_2") == "tenant_2"


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "Amh",  # uppercase
        "1amh",  # leading digit
        "amh; DROP TABLE audit_chain;--",  # injection attempt
        "amh public",  # space
        'amh"; SELECT 1--',
        "amh-prod",  # hyphen not allowed
    ],
)
def test_schema_for_tenant_rejects_unsafe_identifiers(bad_id: str) -> None:
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        schema_for_tenant(bad_id)


def test_normalize_dsn_strips_asyncpg_driver_suffix() -> None:
    assert (
        normalize_dsn("postgresql+asyncpg://maezo:maezo@localhost:5433/maezo")
        == "postgresql://maezo:maezo@localhost:5433/maezo"
    )


def test_normalize_dsn_passthrough_for_plain_dsn() -> None:
    plain = "postgresql://maezo:maezo@localhost:5433/maezo"
    assert normalize_dsn(plain) == plain


def test_insert_sql_column_count_matches_placeholders() -> None:
    # Guards against a column being added to _COLUMNS without updating INSERT_SQL's arity.
    assert INSERT_SQL.count("$") == len(_COLUMNS)
    for column in _COLUMNS:
        assert column in INSERT_SQL


def test_insert_sql_never_writes_id_or_created_at() -> None:
    # id/created_at are server-generated (DEFAULT gen_random_uuid()/now()) — the sink must
    # never try to set them explicitly.
    assert "id" not in _COLUMNS
    assert "created_at" not in _COLUMNS


def test_dedup_claim_sql_is_conflict_safe_and_returns_prior_identity() -> None:
    # The emit_once claim must (a) target audit_emit_dedup, (b) be an idempotent claim
    # (ON CONFLICT DO NOTHING) so a re-delivered effect never errors, and (c) RETURN the
    # record_hash so a first-writer win can be distinguished from a lost race in one round-trip.
    assert "INSERT INTO audit_emit_dedup" in _DEDUP_CLAIM_SQL
    assert "ON CONFLICT (tenant, dedup_key) DO NOTHING" in _DEDUP_CLAIM_SQL
    assert "RETURNING record_hash" in _DEDUP_CLAIM_SQL
    # The lookup returns the prior chain link's identity for an already-claimed key.
    assert "SELECT record_hash FROM audit_emit_dedup" in _DEDUP_LOOKUP_SQL


# ---------------------------------------------------------------------------
# Postgres-backed tests — real DB, skip loudly if unreachable.
# ---------------------------------------------------------------------------


def _default_test_dsn() -> str:
    # Matches the convention referenced by .github/workflows/ci.yml's integration job comments
    # (MAEZO_TEST_DATABASE_URL) and docker-compose.yml's local default host port (5433).
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — "
            "PostgresAuditSink DB-backed tests SKIPPED (visible, not silent). Start it with "
            "`docker compose --profile core up -d postgres` to run these for real."
        )
    return dsn


# Mirrors platform/migrations/versions/0002_audit_chain.py's upgrade() DDL exactly. Duplicated
# here (rather than driving real alembic migrations per test) purely for test speed/isolation;
# the migration itself — including this exact DDL and the env.py search_path plumbing — was
# separately run end-to-end against this same docker-compose postgres (see PR body / VERIFY
# section for the transcript, and the env.py fixes T1.10 needed to make that possible).
_AUDIT_CHAIN_DDL = """
    CREATE TABLE audit_chain (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        timestamp       timestamptz NOT NULL DEFAULT now(),
        tenant_id       text NOT NULL,
        agent_id        text NOT NULL,
        agent_version   text NOT NULL,
        action          text NOT NULL,
        decision        text NOT NULL,
        input_hash      text NOT NULL,
        decision_basis  jsonb NOT NULL DEFAULT '{}'::jsonb,
        dmn_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
        model_id        text,
        prompt_version  text,
        record_hash     text NOT NULL,
        prev_record_hash text,
        created_at      timestamptz NOT NULL DEFAULT now(),

        CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)
    )
"""

# Mirrors platform/migrations/versions/0005_audit_emit_dedup.py's upgrade() DDL exactly (same
# rationale as _AUDIT_CHAIN_DDL: duplicated for test speed/isolation, migration itself is run
# end-to-end separately — see PR body). emit_once() claims a dedup_key here inside the same
# advisory-lock transaction as the chain insert.
_AUDIT_EMIT_DEDUP_DDL = """
    CREATE TABLE audit_emit_dedup (
        tenant       text NOT NULL,
        dedup_key    text NOT NULL,
        record_hash  text NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),

        PRIMARY KEY (tenant, dedup_key)
    )
"""


async def _make_tenant_schema(dsn: str, *, with_table: bool = True) -> str:
    tenant_id = f"kt{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        if with_table:
            await conn.execute(f'SET search_path TO "{tenant_id}"')
            await conn.execute(_AUDIT_CHAIN_DDL)
            await conn.execute(_AUDIT_EMIT_DEDUP_DDL)
    finally:
        await conn.close()
    return tenant_id


async def _drop_tenant_schema(dsn: str, tenant_id: str) -> None:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = await _make_tenant_schema(pg_dsn)
    yield tenant_id
    await _drop_tenant_schema(pg_dsn, tenant_id)


@pytest.mark.integration
async def test_emit_persists_and_chain_verifies(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        for i in range(5):
            record = AuditRecord(
                agent_id=f"agent-{i}",
                tenant_id=tenant_schema,
                agent_version="1.0.0",
                action=f"action-{i}",
                decision="ALLOW",
                details={"i": i},
            )
            record_hash = await sink.emit(record)
            assert record_hash == record.record_hash
    finally:
        await sink.aclose()

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid
    assert result.total_records == 5
    assert result.verified_records == 5


@pytest.mark.integration
async def test_emit_populates_versioning_columns_non_null(pg_dsn: str, tenant_schema: str) -> None:
    """Acceptance criterion: agent_version/dmn_versions non-null in the persisted row."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        await sink.emit(
            AuditRecord(
                agent_id="helena",
                tenant_id=tenant_schema,
                agent_version="2.3.1",
                action="triagem",
                decision="ALLOW",
                details={},
            )
        )
    finally:
        await sink.aclose()

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        row = await conn.fetchrow("SELECT agent_version, dmn_versions FROM audit_chain")
    finally:
        await conn.close()

    assert row is not None
    assert row["agent_version"] == "2.3.1"
    assert row["dmn_versions"] is not None
    # asyncpg returns jsonb as text absent a registered codec; either way it must be present
    # and decode to the explicit non-null empty structure (TODO T1.5), never NULL/absent.
    raw_dmn_versions = row["dmn_versions"]
    decoded = raw_dmn_versions if isinstance(raw_dmn_versions, dict) else json.loads(raw_dmn_versions)
    assert decoded == {}


@pytest.mark.integration
async def test_emit_fail_closed_when_table_missing(pg_dsn: str) -> None:
    """FAIL-CLOSED: a write that cannot be persisted must raise, never be swallowed."""
    tenant_id = await _make_tenant_schema(pg_dsn, with_table=False)
    try:
        sink = PostgresAuditSink(pg_dsn, tenant_id)
        try:
            with pytest.raises(AuditPersistenceError):
                await sink.emit(
                    AuditRecord(
                        agent_id="x",
                        tenant_id=tenant_id,
                        agent_version="1.0.0",
                        action="a",
                        decision="ALLOW",
                        details={},
                    )
                )
        finally:
            await sink.aclose()
    finally:
        await _drop_tenant_schema(pg_dsn, tenant_id)


@pytest.mark.integration
async def test_emit_fail_closed_on_unreachable_database() -> None:
    """FAIL-CLOSED even when the whole database is unreachable — no fire-and-forget fallback."""
    sink = PostgresAuditSink("postgresql://maezo:maezo@localhost:1/maezo", "anytenant")
    try:
        with pytest.raises(AuditPersistenceError):
            await sink.emit(
                AuditRecord(
                    agent_id="x",
                    tenant_id="anytenant",
                    agent_version="1.0.0",
                    action="a",
                    decision="ALLOW",
                    details={},
                )
            )
    finally:
        await sink.aclose()


@pytest.mark.integration
async def test_verify_chain_detects_tamper(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        await sink.emit(
            AuditRecord(
                agent_id="a",
                tenant_id=tenant_schema,
                agent_version="1.0.0",
                action="act1",
                decision="ALLOW",
                details={},
            )
        )
        await sink.emit(
            AuditRecord(
                agent_id="b",
                tenant_id=tenant_schema,
                agent_version="1.0.0",
                action="act2",
                decision="ALLOW",
                details={},
            )
        )
    finally:
        await sink.aclose()

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        # Genesis record's prev_record_hash is the GENESIS_PREV_HASH sentinel (never SQL NULL —
        # see audit.py's GENESIS_PREV_HASH docstring), so target it by value, not `IS NULL`.
        await conn.execute("UPDATE audit_chain SET decision = 'DENY' WHERE prev_record_hash = $1", "0" * 64)
    finally:
        await conn.close()

    result = await verify_chain(pg_dsn, tenant_schema)
    assert not result.valid
    assert result.reason is not None and "hash mismatch" in result.reason


@pytest.mark.integration
async def test_verify_chain_empty_is_valid(pg_dsn: str, tenant_schema: str) -> None:
    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid
    assert result.total_records == 0


# ---------------------------------------------------------------------------
# H1 regression (R1 verification cycle 1): jsonb round-trip false positives.
#
# The verifier reproduced a FALSE tamper alarm on a CLEAN 1-record chain: record_hash was
# computed from the in-memory object at write, but verify_chain recomputes from the
# jsonb-ROUND-TRIPPED row, and Postgres numeric normalizes some values differently than
# Python json.dumps ({"x": -0.0} → stored as 0.0 → hash mismatch → valid=False). Fixed by
# canonicalizing every AuditRecord jsonb field through audit.canonicalize_jsonb at
# construction (hash what you store). These tests are the end-to-end proof against real
# Postgres; the pure canonicalizer rules are unit-tested in test_audit.py.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_verify_chain_negative_zero_no_false_positive(pg_dsn: str, tenant_schema: str) -> None:
    """The verifier's exact H1 repro: decision_basis {"x": -0.0}, one-record chain.
    Before the fix: stored as 0.0 → recomputed hash differs → valid=False on a clean chain."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        await sink.emit(
            AuditRecord(
                agent_id="helena",
                tenant_id=tenant_schema,
                agent_version="1.0.0",
                action="triagem",
                decision="ALLOW",
                details={"x": -0.0},
            )
        )
    finally:
        await sink.aclose()

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid, f"false tamper alarm on a clean chain: {result.reason}"
    assert result.total_records == 1
    assert result.verified_records == 1


# Every payload class Postgres jsonb normalizes (or might): the -0.0 sign drop, exponent-form
# integral floats (stored as plain integers → json.loads returns int; incl. 1e308, where the
# stored value is decimal 10**308, NOT the double's binary value), float extremes (5e-324 min
# subnormal), small-exponent floats, plain floats/ints, big ints beyond 2**63, unicode keys
# and values, deep nesting, None/bools, and the same exposure on dmn_versions.
_JSONB_SWEEP_PAYLOADS: list[dict[str, object]] = [
    {"x": -0.0},
    {"x": 0.0},
    {"x": 1e16},
    {"x": -1e16},
    {"x": 1.234e16},
    {"x": 1.0000000000000002e16},
    {"x": 1e22},
    {"x": 1e308},
    {"x": -1e308},
    {"x": 5e-324},
    {"x": 1e-05},
    {"x": 0.1},
    {"x": 2.5},
    {"x": 9.99e15},
    {"x": 123456789012345678901234567890},
    {"x": -42},
    {"unicode_ключ_鍵": {"nested": [{"deep": [-0.0, 1e16, "café", None, True, False]}]}},
    {"mixed": [1, 1.5, "1", None], "empty": {}, "lista": []},
]


@pytest.mark.integration
async def test_verify_chain_jsonb_roundtrip_sweep_no_false_positive(pg_dsn: str, tenant_schema: str) -> None:
    """Property-style sweep: write one record per jsonb-normalization-sensitive payload
    (in both decision_basis AND dmn_versions), then verify_chain must report the whole
    chain valid — zero false tamper alarms."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        for i, payload in enumerate(_JSONB_SWEEP_PAYLOADS):
            await sink.emit(
                AuditRecord(
                    agent_id=f"sweep-{i}",
                    tenant_id=tenant_schema,
                    agent_version="1.0.0",
                    action="jsonb_sweep",
                    decision="ALLOW",
                    details=dict(payload),
                    dmn_versions=dict(payload),  # same exposure on the other jsonb column
                )
            )
    finally:
        await sink.aclose()

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid, f"false tamper alarm in sweep: {result.reason} (break_at={result.break_at_hash})"
    assert result.total_records == len(_JSONB_SWEEP_PAYLOADS)
    assert result.verified_records == len(_JSONB_SWEEP_PAYLOADS)


@pytest.mark.integration
async def test_verify_chain_sweep_genuine_tamper_still_detected(pg_dsn: str, tenant_schema: str) -> None:
    """Canonicalization must not blunt detection: on a chain of the SAME tricky payloads,
    genuinely tampering a stored decision_basis must still flip verify_chain to invalid."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        for i, payload in enumerate(_JSONB_SWEEP_PAYLOADS[:4]):
            await sink.emit(
                AuditRecord(
                    agent_id=f"tamper-{i}",
                    tenant_id=tenant_schema,
                    agent_version="1.0.0",
                    action="jsonb_sweep",
                    decision="ALLOW",
                    details=dict(payload),
                )
            )
    finally:
        await sink.aclose()

    assert (await verify_chain(pg_dsn, tenant_schema)).valid  # clean before tamper

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        # Tamper the jsonb content itself (the very column class H1 was about).
        await conn.execute(
            "UPDATE audit_chain SET decision_basis = '{\"x\": 999}'::jsonb WHERE agent_id = 'tamper-2'"
        )
    finally:
        await conn.close()

    result = await verify_chain(pg_dsn, tenant_schema)
    assert not result.valid
    assert result.reason is not None and "hash mismatch" in result.reason


@pytest.mark.integration
async def test_concurrent_emits_same_tenant_serialize_without_fork(pg_dsn: str, tenant_schema: str) -> None:
    """The per-tenant advisory lock must fully serialize concurrent writers — no fork, no lost
    write, no duplicate hash — even under real concurrency (not just sequential calls)."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:

        async def emit_one(i: int) -> str:
            record = AuditRecord(
                agent_id=f"conc-{i}",
                tenant_id=tenant_schema,
                agent_version="1.0.0",
                action=f"a-{i}",
                decision="ALLOW",
                details={"i": i},
            )
            return await sink.emit(record)

        results = await asyncio.gather(*[emit_one(i) for i in range(25)])
    finally:
        await sink.aclose()

    assert len(set(results)) == 25  # every hash unique — no collision/fork

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid
    assert result.total_records == 25
    assert result.verified_records == 25


@pytest.mark.integration
async def test_different_tenants_do_not_share_a_chain(pg_dsn: str) -> None:
    tenant_a = await _make_tenant_schema(pg_dsn)
    tenant_b = await _make_tenant_schema(pg_dsn)
    try:
        sink_a = PostgresAuditSink(pg_dsn, tenant_a)
        sink_b = PostgresAuditSink(pg_dsn, tenant_b)
        try:
            for i in range(3):
                await sink_a.emit(
                    AuditRecord(
                        agent_id="a",
                        tenant_id=tenant_a,
                        agent_version="1.0.0",
                        action=f"a-{i}",
                        decision="ALLOW",
                        details={},
                    )
                )
            for i in range(2):
                await sink_b.emit(
                    AuditRecord(
                        agent_id="b",
                        tenant_id=tenant_b,
                        agent_version="1.0.0",
                        action=f"b-{i}",
                        decision="ALLOW",
                        details={},
                    )
                )
        finally:
            await sink_a.aclose()
            await sink_b.aclose()

        result_a = await verify_chain(pg_dsn, tenant_a)
        result_b = await verify_chain(pg_dsn, tenant_b)
        assert result_a.valid and result_a.total_records == 3
        assert result_b.valid and result_b.total_records == 2
    finally:
        await _drop_tenant_schema(pg_dsn, tenant_a)
        await _drop_tenant_schema(pg_dsn, tenant_b)


# ---------------------------------------------------------------------------
# emit_once — exactly-once idempotent emission (T-A, docs/design/audit-emit-path-wiring.md §4.3).
# ---------------------------------------------------------------------------


async def _count_chain_rows(dsn: str, tenant_id: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        count: int = await conn.fetchval("SELECT count(*) FROM audit_chain")
    finally:
        await conn.close()
    return count


async def _count_dedup_rows(dsn: str, tenant_id: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        count: int = await conn.fetchval("SELECT count(*) FROM audit_emit_dedup")
    finally:
        await conn.close()
    return count


def _emit_once_record(tenant_id: str, *, marker: str) -> AuditRecord:
    return AuditRecord(
        agent_id="operadora-worker",
        tenant_id=tenant_id,
        agent_version="1.0.0",
        action="task_complete",
        decision="ALLOW",
        details={"marker": marker},
    )


@pytest.mark.integration
async def test_emit_once_same_key_twice_writes_one_link_and_returns_prior_identity(
    pg_dsn: str, tenant_schema: str
) -> None:
    """Same dedup_key twice → exactly one chain row + one dedup row; the second call is a no-op
    that returns the FIRST record's identity (not an error, not a second link)."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    key = f"{tenant_schema}:task-abc"
    try:
        first = _emit_once_record(tenant_schema, marker="first")
        h1 = await sink.emit_once(first, dedup_key=key)

        # A genuinely different record (different details) under the SAME key — must be suppressed.
        second = _emit_once_record(tenant_schema, marker="second-should-be-ignored")
        h2 = await sink.emit_once(second, dedup_key=key)
    finally:
        await sink.aclose()

    assert h2 == h1, "re-delivery must return the prior link's identity, not a new hash"
    assert h1 == first.record_hash
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 1, "second emit_once wrote a 2nd link"
    assert await _count_dedup_rows(pg_dsn, tenant_schema) == 1

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 1 and result.verified_records == 1


@pytest.mark.integration
async def test_emit_once_different_keys_write_distinct_links(pg_dsn: str, tenant_schema: str) -> None:
    """Different dedup_keys → two distinct chain rows, two dedup rows, a valid chain."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        h1 = await sink.emit_once(
            _emit_once_record(tenant_schema, marker="a"), dedup_key=f"{tenant_schema}:task-1"
        )
        h2 = await sink.emit_once(
            _emit_once_record(tenant_schema, marker="b"), dedup_key=f"{tenant_schema}:task-2"
        )
    finally:
        await sink.aclose()

    assert h1 != h2
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 2
    assert await _count_dedup_rows(pg_dsn, tenant_schema) == 2

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 2 and result.verified_records == 2


@pytest.mark.integration
async def test_emit_once_concurrent_same_key_exactly_one_wins(pg_dsn: str, tenant_schema: str) -> None:
    """N coroutines racing emit_once on the SAME key → exactly one chain row is written, every
    caller returns that one record's identity, and the chain verifies (no fork, no duplicate)."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    key = f"{tenant_schema}:task-race"
    try:

        async def race(i: int) -> str:
            return await sink.emit_once(_emit_once_record(tenant_schema, marker=f"r{i}"), dedup_key=key)

        results = await asyncio.gather(*[race(i) for i in range(25)])
    finally:
        await sink.aclose()

    assert len(set(results)) == 1, "concurrent same-key emits must all resolve to ONE identity"
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 1, "the race wrote more than one link"
    assert await _count_dedup_rows(pg_dsn, tenant_schema) == 1

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 1 and result.verified_records == 1


@pytest.mark.integration
async def test_emit_once_concurrent_distinct_keys_serialize_without_fork(
    pg_dsn: str, tenant_schema: str
) -> None:
    """N coroutines racing emit_once on DISTINCT keys → all N links written, chain still valid
    (the advisory lock serializes the tail read across emit_once calls exactly as for emit)."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:

        async def emit(i: int) -> str:
            return await sink.emit_once(
                _emit_once_record(tenant_schema, marker=f"d{i}"), dedup_key=f"{tenant_schema}:task-{i}"
            )

        results = await asyncio.gather(*[emit(i) for i in range(25)])
    finally:
        await sink.aclose()

    assert len(set(results)) == 25, "distinct keys must each produce a unique link — no collision"
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 25

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 25 and result.verified_records == 25


@pytest.mark.integration
async def test_emit_once_interoperates_with_plain_emit(pg_dsn: str, tenant_schema: str) -> None:
    """emit() and emit_once() share the same chain-insert path — interleaving them keeps one
    unbroken, verifiable chain."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        await sink.emit(_emit_once_record(tenant_schema, marker="plain-1"))
        await sink.emit_once(
            _emit_once_record(tenant_schema, marker="once-1"), dedup_key=f"{tenant_schema}:k1"
        )
        await sink.emit(_emit_once_record(tenant_schema, marker="plain-2"))
        # re-delivery of k1 — no new link
        await sink.emit_once(
            _emit_once_record(tenant_schema, marker="once-1-again"), dedup_key=f"{tenant_schema}:k1"
        )
    finally:
        await sink.aclose()

    assert await _count_chain_rows(pg_dsn, tenant_schema) == 3
    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 3 and result.verified_records == 3


@pytest.mark.integration
async def test_emit_once_fail_closed_when_dedup_table_missing(pg_dsn: str) -> None:
    """A missing audit_emit_dedup table (misconfiguration) must FAIL CLOSED — raise, write no
    chain link — never silently skip the dedup claim and complete the effect unaudited."""
    tenant_id = await _make_tenant_schema(pg_dsn, with_table=False)
    try:
        # Give it audit_chain but NOT audit_emit_dedup, so the failure is specifically the claim.
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'SET search_path TO "{tenant_id}"')
            await conn.execute(_AUDIT_CHAIN_DDL)
        finally:
            await conn.close()

        sink = PostgresAuditSink(pg_dsn, tenant_id)
        try:
            with pytest.raises(AuditPersistenceError):
                await sink.emit_once(_emit_once_record(tenant_id, marker="x"), dedup_key=f"{tenant_id}:k")
        finally:
            await sink.aclose()

        assert await _count_chain_rows(pg_dsn, tenant_id) == 0, "fail-closed must leave no chain link"
    finally:
        await _drop_tenant_schema(pg_dsn, tenant_id)


# ---------------------------------------------------------------------------
# Kill-test — the T1.10 acceptance criterion.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_kill_test_process_crash_loses_zero_committed_records(pg_dsn: str, tenant_schema: str) -> None:
    """Kill-test: SIGKILL a writer subprocess mid-stream; verify zero flushed records are lost,
    the chain verifies across the restart, and there are no gaps before the kill point.

    Protocol:
      1. Spawn a subprocess writing COUNT records through PostgresAuditSink, one per line of
         confirmed stdout output ("COMMITTED <i> <hash>", unbuffered).
      2. Read confirmations until KILL_AFTER have been seen, then SIGKILL immediately — no
         artificial delay, so the process is still actively writing (tight loop) when the
         signal lands, giving a realistic chance the kill interrupts a write in flight.
      3. Verify: every CONFIRMED record (stdout said "COMMITTED") is present in Postgres —
         zero flushed records lost. (A record dispatched but not yet stdout-confirmed at kill
         time may or may not have committed — Postgres's own transactional guarantee, not this
         sink, decides that, and either outcome is correct: never a torn/partial row.)
      4. Restart: spawn a FRESH writer process for the SAME tenant, writing the remaining
         records. It needs no explicit "recover" step — PostgresAuditSink reads the true tail
         from Postgres on every emit(), so a brand-new process naturally continues the chain
         (see audit_postgres.py's "No in-memory head cache" design note).
      5. verify_chain() must report valid=True with total_records == verified_records (no gaps,
         no fork) and total_records == the sum of what both processes actually wrote.
    """
    count = 200
    kill_after = 20

    proc = subprocess.Popen(  # noqa: S603, ASYNC220 — kill-test needs real-time stdout + a real OS signal
        [
            sys.executable,
            str(_KILLTEST_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--count",
            str(count),
            "--label",
            "batch1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    confirmed: list[tuple[int, str]] = []
    try:
        while len(confirmed) < kill_after:
            line = proc.stdout.readline()
            if not line:
                break  # process exited before reaching kill_after — still valid, just note it
            line = line.strip()
            if line.startswith("COMMITTED "):
                _, idx, record_hash = line.split(" ", 2)
                confirmed.append((int(idx), record_hash))
    finally:
        # SIGKILL cannot be caught/cleaned up — that is the entire point of this test: prove
        # durability WITHOUT relying on graceful shutdown.
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    assert proc.returncode != 0  # confirms it was actually killed, not a clean exit
    assert len(confirmed) >= 1, "writer subprocess produced no confirmed writes before dying"

    # Every CONFIRMED hash must be present in Postgres — zero flushed records lost.
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        db_hashes = {r["record_hash"] for r in await conn.fetch("SELECT record_hash FROM audit_chain")}
        rows_after_kill = await conn.fetchval("SELECT count(*) FROM audit_chain")
    finally:
        await conn.close()

    missing = [h for _, h in confirmed if h not in db_hashes]
    assert not missing, f"{len(missing)} CONFIRMED record(s) lost after SIGKILL: {missing}"

    # Restart: a fresh process picks up where the chain (in Postgres, not memory) left off.
    # Write the rest of the batch under a distinct label so both batches are identifiable.
    remaining = count - rows_after_kill
    assert remaining > 0, "kill happened after the writer had already finished — widen the race"

    proc2 = subprocess.run(  # noqa: S603, ASYNC221 — restart writer must run to completion before verifying
        [
            sys.executable,
            str(_KILLTEST_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--count",
            str(remaining),
            "--label",
            "batch2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc2.returncode == 0, f"restart writer failed: stdout={proc2.stdout!r} stderr={proc2.stderr!r}"

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid, f"chain invalid after restart: {result.reason} (break_at={result.break_at_hash})"
    assert result.total_records == rows_after_kill + remaining
    assert result.verified_records == result.total_records, (
        "gap detected after restart — records lost or forked"
    )

    # Sanity: every pre-kill CONFIRMED hash is still reachable in the verified chain (not just
    # "a" row in the table, but actually linked in — verify_chain() already proved the whole
    # table is one unbroken chain from genesis, so membership in db_hashes above is sufficient).


@pytest.mark.integration
async def test_kill_test_emit_once_atomic_across_crash(pg_dsn: str, tenant_schema: str) -> None:
    """emit_once kill-test (T-A acceptance): SIGKILL a writer BETWEEN the dedup-claim and the
    chain-insert; prove the claim+link are atomic across the crash.

    The dedup-claim INSERT and the chain-insert run in ONE per-tenant advisory-lock transaction.
    A crash between them must roll back BOTH — otherwise a surviving claim would suppress the
    re-delivery's audit (a GAP), or a surviving link with no claim would let the re-delivery
    double-audit (a DUPLICATE). This test lands the SIGKILL exactly in that window and proves,
    after re-delivery of the SAME dedup_key, that EXACTLY ONE audit record exists and the chain
    verifies.

    Protocol:
      1. Spawn the writer in `hang-before-chain` mode: it runs emit_once far enough to execute
         the dedup-claim INSERT (inside its uncommitted transaction), prints `CLAIMED <key>`, then
         blocks at the chain insert.
      2. On seeing `CLAIMED`, SIGKILL — Postgres rolls back the whole transaction (claim included)
         when the connection dies.
      3. Assert: zero chain rows AND zero dedup rows survived (atomic rollback — the claim did not
         outlive the link it was atomic with).
      4. Re-deliver: spawn a FRESH writer in `complete` mode with the SAME dedup_key. It sees no
         prior claim, so it emits cleanly.
      5. Assert: EXACTLY ONE chain row (no duplicate), one dedup row, verify_chain valid (no gap,
         no fork), and its record_hash is what the completing writer returned.
    """
    dedup_key = f"{tenant_schema}:killtest-effect-1"

    proc = subprocess.Popen(  # noqa: S603, ASYNC220 — kill-test needs real-time stdout + a real OS signal
        [
            sys.executable,
            str(_KILLTEST_EMIT_ONCE_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--dedup-key",
            dedup_key,
            "--mode",
            "hang-before-chain",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    claimed_seen = False
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("CLAIMED "):
                claimed_seen = True
                break
            if line.startswith("ERROR "):
                pytest.fail(f"emit_once writer errored before the claim: {line!r}")
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    assert proc.returncode != 0  # confirms it was actually killed, not a clean exit
    assert claimed_seen, "writer never reached the dedup-claim (CLAIMED) — widen the window"

    # The kill landed between claim and chain-insert. Atomic rollback: NEITHER survives.
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 0, (
        "a chain link survived the crash without its dedup claim — non-atomic (duplicate hazard)"
    )
    assert await _count_dedup_rows(pg_dsn, tenant_schema) == 0, (
        "a dedup claim survived the crash without its chain link — non-atomic (gap hazard: the "
        "re-delivery would be suppressed and the effect left unaudited)"
    )

    # Re-deliver the SAME effect (same dedup_key). It must emit cleanly — the crashed claim left
    # nothing behind to suppress it.
    proc2 = subprocess.run(  # noqa: S603, ASYNC221 — re-delivery writer must finish before verifying
        [
            sys.executable,
            str(_KILLTEST_EMIT_ONCE_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--dedup-key",
            dedup_key,
            "--mode",
            "complete",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc2.returncode == 0, f"re-delivery failed: stdout={proc2.stdout!r} stderr={proc2.stderr!r}"
    done_line = next((line for line in proc2.stdout.splitlines() if line.startswith("DONE ")), None)
    assert done_line is not None, f"re-delivery writer produced no DONE line: {proc2.stdout!r}"
    redelivered_hash = done_line.split(" ", 1)[1]

    # EXACTLY ONE record for the effect — no duplicate from the retry, no gap.
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 1
    assert await _count_dedup_rows(pg_dsn, tenant_schema) == 1

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid, f"chain invalid after re-delivery: {result.reason} (break_at={result.break_at_hash})"
    assert result.total_records == 1 and result.verified_records == 1

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        db_hash = await conn.fetchval("SELECT record_hash FROM audit_chain")
        claim_hash = await conn.fetchval(
            "SELECT record_hash FROM audit_emit_dedup WHERE dedup_key = $1", dedup_key
        )
    finally:
        await conn.close()
    assert db_hash == redelivered_hash, "the surviving chain row is not the one the retry wrote"
    assert claim_hash == redelivered_hash, "the dedup claim does not point at the surviving link"


# ---------------------------------------------------------------------------
# FreshSinkAuditEmitter — per-call, loop-agnostic adapter (T1.10 wave integration).
# Pure-unit tests monkeypatch the module-global `PostgresAuditSink` the adapter constructs;
# the cross-loop test below runs against real Postgres (integration-marked).
# ---------------------------------------------------------------------------


def _adapter_record(tenant_id: str, action: str = "start_process:SP-OP-CANCEL-001") -> AuditRecord:
    return AuditRecord(
        agent_id="operadora-worker",
        tenant_id=tenant_id,
        agent_version="1.0.0",
        action=action,
        decision="START_PROCESS",
        details={"decisao_inadimplencia": "ENCAMINHAR_RESCISAO"},
    )


def test_fresh_sink_emitter_rejects_unsafe_tenant_at_construction() -> None:
    """Same eager fail-closed tenant validation as PostgresAuditSink itself — a bad tenant is a
    construction-time ValueError, never a deferred surprise inside a worker dispatch."""
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        FreshSinkAuditEmitter("postgresql://x@localhost/db", "bad-tenant;drop")


async def test_fresh_sink_emitter_delegates_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """emit_once constructs a FRESH delegate inside the calling loop, delegates verbatim
    (record + dedup_key), returns the delegate's hash, and ALWAYS closes the delegate."""
    import maezo.gateway.audit_postgres as ap

    events: list[tuple[str, object]] = []

    class _RecordingSink:
        def __init__(self, dsn: str, tenant_id: str, *, pool: object | None = None) -> None:
            events.append(("init", (dsn, tenant_id)))

        async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
            # The adapter forwards `emit_once_status` (B-3: it must carry the dedup FLAG through,
            # not just the hash), so the delegate double implements THAT method.
            events.append(("emit", (record, dedup_key)))
            return EmitOnceOutcome(record_hash="hash-1", deduped=False)

        async def aclose(self) -> None:
            events.append(("aclose", None))

    monkeypatch.setattr(ap, "PostgresAuditSink", _RecordingSink)
    emitter = FreshSinkAuditEmitter("postgresql://maezo@localhost/maezo", "amh")
    record = _adapter_record("amh")

    got = await emitter.emit_once(record, dedup_key="amh:start:SP-OP-CANCEL-001:CANCEL-amh-C-1")

    assert got == "hash-1"
    assert [e[0] for e in events] == ["init", "emit", "aclose"]  # fresh ctor, emit, then close
    assert events[0][1] == ("postgresql://maezo@localhost/maezo", "amh")
    emitted_record, emitted_key = events[1][1]  # type: ignore[misc]
    assert emitted_record is record
    assert emitted_key == "amh:start:SP-OP-CANCEL-001:CANCEL-amh-C-1"


async def test_fresh_sink_emitter_failure_propagates_and_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL-CLOSED: the delegate's AuditPersistenceError propagates unchanged (no swallow, no
    fallback) and the fresh delegate is still closed — no leaked pool on the failure path."""
    import maezo.gateway.audit_postgres as ap

    closed: list[bool] = []

    class _FailingSink:
        def __init__(self, dsn: str, tenant_id: str, *, pool: object | None = None) -> None:
            pass

        async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
            raise AuditPersistenceError("durable write failed")

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(ap, "PostgresAuditSink", _FailingSink)
    emitter = FreshSinkAuditEmitter("postgresql://maezo@localhost/maezo", "amh")

    with pytest.raises(AuditPersistenceError, match="durable write failed"):
        await emitter.emit_once(_adapter_record("amh"), dedup_key="amh:k")
    assert closed == [True]


@pytest.mark.integration
def test_fresh_sink_emitter_cross_loop_sequential_asyncio_run(pg_dsn: str) -> None:
    """THE reason this adapter exists (T1.10 wave): two SEQUENTIAL `asyncio.run` loops — exactly
    how sync worker dispatches (handoff_rescisao) emit — through ONE emitter instance, against
    REAL Postgres. A pooled PostgresAuditSink shared across these loops fails with cross-loop
    asyncpg errors; the per-call adapter must succeed on both, honouring emit_once dedup:
    a DIFFERENT dedup key appends a second chain link; the SAME key returns the prior hash."""
    tenant_id = asyncio.run(_make_tenant_schema(pg_dsn))
    try:
        emitter = FreshSinkAuditEmitter(pg_dsn, tenant_id)

        # Loop 1: first dispatch emits.
        hash_1 = asyncio.run(
            emitter.emit_once(_adapter_record(tenant_id, action="start:one"), dedup_key="t:one")
        )
        # Loop 2 (fresh loop, same emitter): a second, distinct dispatch emits.
        hash_2 = asyncio.run(
            emitter.emit_once(_adapter_record(tenant_id, action="start:two"), dedup_key="t:two")
        )
        # Loop 3: re-delivery of dispatch one dedups to the prior link (no third row).
        hash_redelivery = asyncio.run(
            emitter.emit_once(_adapter_record(tenant_id, action="start:one"), dedup_key="t:one")
        )

        assert hash_1 != hash_2
        assert hash_redelivery == hash_1

        result = asyncio.run(verify_chain(pg_dsn, tenant_id))
        assert result.valid
        assert result.total_records == 2
        assert result.verified_records == 2
    finally:
        asyncio.run(_drop_tenant_schema(pg_dsn, tenant_id))
