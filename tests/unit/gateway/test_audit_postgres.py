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

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import (
    _COLUMNS,
    INSERT_SQL,
    AuditPersistenceError,
    PostgresAuditSink,
    normalize_dsn,
    schema_for_tenant,
    verify_chain,
)

_KILLTEST_WRITER = Path(__file__).parent / "_audit_killtest_writer.py"


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


async def _make_tenant_schema(dsn: str, *, with_table: bool = True) -> str:
    tenant_id = f"kt{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        if with_table:
            await conn.execute(f'SET search_path TO "{tenant_id}"')
            await conn.execute(_AUDIT_CHAIN_DDL)
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
