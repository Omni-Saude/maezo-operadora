"""Real-Postgres acceptance for the harness emit-before-complete wiring (T-C, T1.10; ADR-0007, L0).

Mirrors `test_audit_postgres.py`'s real-PG tier (ADR-0024/T1.10 pattern): each test creates and
tears down its own throwaway tenant schema and SKIPS loudly if Postgres is unreachable
(`MAEZO_TEST_DATABASE_URL`, or the compose default). Never silently omitted, never a fake pass.

Three proofs (design §6.2/§6.3):
  1. ACCEPTANCE (headline): a REAL `WorkerHarness` wired to a REAL `PostgresAuditSink` drives ONE
     DMN-consulting worker task to completion -> a queryable `audit_chain` row exists with NON-NULL
     `dmn_versions` and a PHI-safe `decision_basis`, and `verify_chain(...).valid is True`.
  2. CONCURRENCY: N concurrent tasks, same tenant -> verify_chain valid, single tail (no fork).
  3. KILL-TEST (fail-closed + exactly-once, §6.3): SIGKILL the writer AFTER emit committed but
     BEFORE `complete` -> the audit row is durable (audit-before-complete); re-delivery dedups
     (exactly one chain row, no double), and the effect ultimately completes (no gap).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import FakeDmnTransport, evaluate_sync, first_row
from maezo.tools.workers.harness import ExternalTask, FakeWorkerTransport, WorkerHarness

_HARNESS_KILLTEST_WRITER = Path(__file__).parent / "_harness_emit_killtest_writer.py"

# Mirrors 0002_audit_chain.py / 0005_audit_emit_dedup.py upgrade() DDL exactly — duplicated for
# test speed/isolation, identical rationale to test_audit_postgres.py's own copies (the migrations
# themselves are exercised end-to-end there and in T-A's real-alembic 0004<->0005 run).
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
_AUDIT_EMIT_DEDUP_DDL = """
    CREATE TABLE audit_emit_dedup (
        tenant       text NOT NULL,
        dedup_key    text NOT NULL,
        record_hash  text NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant, dedup_key)
    )
"""


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — harness "
            "audit integration tests SKIPPED (visible, not silent). Bring one up with e.g. "
            "`MAEZO_PG_HOST_PORT=5541 docker compose -p tc_audit up -d postgres`."
        )
    return dsn


async def _make_tenant_schema(dsn: str) -> str:
    tenant_id = f"tc{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        await conn.execute(_AUDIT_CHAIN_DDL)
        await conn.execute(_AUDIT_EMIT_DEDUP_DDL)
    finally:
        await conn.close()
    return tenant_id


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = await _make_tenant_schema(pg_dsn)
    yield tenant_id
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


def _jsonb(value: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as text unless a codec is registered — decode defensively."""
    return json.loads(value) if isinstance(value, str) else value


async def _fetch_chain_rows(dsn: str, tenant_id: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        return await conn.fetch("SELECT * FROM audit_chain")
    finally:
        await conn.close()


async def _count_chain_rows(dsn: str, tenant_id: str) -> int:
    return len(await _fetch_chain_rows(dsn, tenant_id))


def _task(*, task_id: str, topic: str, variables: dict[str, Any] | None = None) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables=variables or {},
    )


def _route_via_dmn(variables: dict[str, Any], *, dmn: FakeDmnTransport) -> dict[str, Any]:
    rows, _v = evaluate_sync(dmn, "pagto_alcada", {"valor_pagamento_cents": 5000})
    row = first_row(rows, "pagto_alcada", variables)
    return {"faixa_valor": str(row["faixa_valor"]), "grupo_aprovador": str(row["grupo_aprovador"])}


# ---------------------------------------------------------------------------
# 1. ACCEPTANCE — a live worker execution produces a verifiable audit_chain row
#    with non-null dmn_versions (the headline, design §6.2).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_live_worker_completion_writes_verifiable_audit_row(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    dmn = FakeDmnTransport()
    dmn.register(
        "pagto_alcada",
        [{"faixa_valor": "DENTRO_TETO_L2", "grupo_aprovador": "AUTO"}],
        version=9,
        definition_id="pagto_alcada:9:live",
        deployment_id="dep-live",
    )
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="pod-1", tenant=tenant_schema, audit_sink=sink)
    harness.register_worker(
        FunctionWorker("operadora.pagto.calculate_facts", functools.partial(_route_via_dmn, dmn=dmn))
    )
    try:
        await harness._handle(
            _task(
                task_id="live-1",
                topic="operadora.pagto.calculate_facts",
                variables={"cpf": "12345678900", "valor_pagamento_cents": 5000},
            )
        )
    finally:
        await sink.aclose()

    # The effect was committed (fake engine got the complete)...
    assert transport.completed and transport.completed[0][0] == "live-1"

    # ...AND a durable, PHI-safe, DMN-provenanced audit row exists.
    rows = await _fetch_chain_rows(pg_dsn, tenant_schema)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "operadora.pagto.calculate_facts"
    assert row["agent_id"] == "operadora-worker"  # stable service identity, not pod-1
    assert row["model_id"] is None and row["prompt_version"] is None

    dmn_versions = _jsonb(row["dmn_versions"])
    assert dmn_versions == {
        "pagto_alcada": {"version": 9, "id": "pagto_alcada:9:live", "deploymentId": "dep-live"}
    }, "acceptance requires NON-NULL dmn_versions captured from the live DMN eval"
    basis = _jsonb(row["decision_basis"])
    assert len(basis["input_sha256"]) == 64
    assert "12345678900" not in json.dumps(basis)  # raw PHI never persisted
    assert basis["faixa_valor"] == "DENTRO_TETO_L2"

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 1 and result.verified_records == 1


# ---------------------------------------------------------------------------
# 2. CONCURRENCY — N concurrent completions, same tenant, no fork (design §6.2).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_completions_same_tenant_no_fork(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="pod-1", tenant=tenant_schema, audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"desfecho": "liberado_automatico"}

    harness.register("t", handler)
    n = 12
    try:
        await asyncio.gather(*(harness._handle(_task(task_id=f"c{i}", topic="t")) for i in range(n)))
    finally:
        await sink.aclose()

    assert len(transport.completed) == n
    assert await _count_chain_rows(pg_dsn, tenant_schema) == n
    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid, f"chain forked/tampered under concurrency: {result.reason}"
    assert result.total_records == n and result.verified_records == n


# ---------------------------------------------------------------------------
# 3. KILL-TEST — fail-closed + exactly-once end-to-end through _handle (design §6.3).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_kill_test_emit_before_complete_exactly_once(pg_dsn: str, tenant_schema: str) -> None:
    """SIGKILL the worker AFTER emit committed but BEFORE `complete`; re-deliver the same task.

    Proves end-to-end what §4 argues: the audit row is durable BEFORE the effect (audit-before-
    complete), a crash in that window leaves the SAFE direction (audited-but-not-yet-completed),
    and re-delivery dedups to EXACTLY ONE chain row while the effect ultimately completes — zero
    un-audited effects, zero double-audits, across a hard crash.
    """
    task_id = "kt-effect-1"
    dedup_key = f"{tenant_schema}:{task_id}"

    proc = subprocess.Popen(  # noqa: ASYNC220 — kill-test needs real-time stdout + a real OS signal
        [
            sys.executable,
            str(_HARNESS_KILLTEST_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--task-id",
            task_id,
            "--mode",
            "hang-after-emit",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    emitted_seen = False
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("EMITTED "):
                emitted_seen = True
                break
            if line.startswith("ERROR "):
                pytest.fail(f"harness kill-test writer errored before emit: {line!r}")
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    assert proc.returncode != 0  # actually killed, not a clean exit
    assert emitted_seen, "writer never reached the post-emit/complete window — widen it"

    # Audit-before-complete: the row is DURABLE even though `complete` never ran (SAFE direction).
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 1, (
        "the audit row must be committed BEFORE complete — audit-before-complete (ADR-0007 §4.2)"
    )

    # Re-deliver the SAME task_id: emit dedups (no 2nd row), complete now succeeds.
    proc2 = subprocess.run(  # noqa: ASYNC221 — re-delivery writer must finish before verifying
        [
            sys.executable,
            str(_HARNESS_KILLTEST_WRITER),
            "--dsn",
            pg_dsn,
            "--tenant",
            tenant_schema,
            "--task-id",
            task_id,
            "--mode",
            "redeliver",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc2.returncode == 0, f"re-delivery failed: stdout={proc2.stdout!r} stderr={proc2.stderr!r}"
    assert any(line.startswith(f"COMPLETED {task_id}") for line in proc2.stdout.splitlines()), (
        f"re-delivery did not complete the effect: {proc2.stdout!r}"
    )

    # EXACTLY ONE record for the effect — dedup prevented a double; no gap.
    assert await _count_chain_rows(pg_dsn, tenant_schema) == 1
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        claim = await conn.fetchval(
            "SELECT record_hash FROM audit_emit_dedup WHERE dedup_key = $1", dedup_key
        )
    finally:
        await conn.close()
    assert claim is not None
    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid and result.total_records == 1 and result.verified_records == 1
