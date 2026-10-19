"""DL-0150: o registro de leitura no `audit_chain` de um Postgres REAL (sem motor).

Requer `MAEZO_TEST_DATABASE_URL` (ou o Postgres do compose) e um banco descartavel: cada teste cria
um schema proprio, aplica as migracoes REAIS `0002_audit_chain` e `0005_audit_emit_dedup` e o
derruba no fim. Prova que o registro cai na MESMA cadeia da conclusao (encadeado ao elo anterior,
cadeia verificavel), que cada leitura e' uma linha, que a linha guarda so' a lista fechada de
campos e que um banco sem a cadeia RECUSA (fail-closed), em vez de engolir a falha.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import AuditPersistenceError, PostgresAuditSink, normalize_dsn, verify_chain
from maezo.gateway.human.models import Scope
from maezo.gateway.human.read_audit import READ_AUDIT_DETAIL_FIELDS, ReadAccessEntry
from maezo.gateway.human.read_audit_postgres import PostgresReadAccessAudit

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _schema(*, migrate: bool):
    dsn = _default_test_dsn()
    raw = normalize_dsn(dsn)
    schema = "read_audit_" + uuid4().hex
    admin = await asyncpg.connect(raw)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    if migrate:
        engine = create_async_engine(
            make_url(dsn).set(drivername="postgresql+asyncpg"),
            echo=False,
            hide_parameters=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with engine.begin() as conn:

            def apply(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    for name in ("0002_audit_chain", "0005_audit_emit_dedup"):
                        importlib.import_module("maezo.platform.migrations.versions." + name).upgrade()

            await conn.run_sync(apply)
        await engine.dispose()
    # Pool EMPRESTADO e sem search_path, como o do plano humano: o sink tem de fixar o schema.
    pool = await asyncpg.create_pool(raw, min_size=1, max_size=4)
    return raw, schema, admin, pool


async def _drop(admin, pool, schema):
    await pool.close()
    await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
    await admin.close()


@pytest.fixture
async def db():
    raw, schema, admin, pool = await _schema(migrate=True)
    try:
        yield dict(dsn=raw, schema=schema, admin=admin, pool=pool)
    finally:
        await _drop(admin, pool, schema)


def _entry(scope: Scope, read_at: datetime, *, kind="task_context", ref="task-1") -> ReadAccessEntry:
    return ReadAccessEntry(
        scope=scope,
        read_kind=kind,
        resource_ref=ref,
        principal_ref="principal-7",
        session_ref="session-9",
        membership_revision=4,
        read_at=read_at,
    )


async def test_each_read_is_one_row_in_the_same_chain_with_only_the_allowed_fields(db):
    scope = Scope(tenant=db["schema"], environment="test", workload_ref="portal-bff")
    # Um elo anterior na MESMA cadeia (o que a conclusao grava), para provar o encadeamento.
    prior = PostgresAuditSink("", db["schema"], pool=db["pool"])
    prior_hash = await prior.emit(
        AuditRecord(
            agent_id="portal-bff",
            tenant_id=db["schema"],
            agent_version="portal-direct-completion.v1",
            action="portal_direct_completion.intent",
            decision="REQUIRE_HUMAN",
            details={"operation": "completion"},
        )
    )
    audit = PostgresReadAccessAudit(scope=scope, pool=db["pool"])
    t0 = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    h1 = await audit.record(_entry(scope, t0))
    h2 = await audit.record(_entry(scope, t0 + timedelta(seconds=1)))
    h3 = await audit.record(_entry(scope, t0 + timedelta(seconds=2), kind="staff_case_detail", ref="case_1"))
    assert len({prior_hash, h1, h2, h3}) == 4

    rows = await db["admin"].fetch(
        f"SELECT action, decision, timestamp, decision_basis, prev_record_hash, record_hash "
        f'FROM "{db["schema"]}".audit_chain WHERE action LIKE $1 ORDER BY timestamp',
        "portal_read.%",
    )
    assert [r["action"] for r in rows] == [
        "portal_read.task_context",
        "portal_read.task_context",
        "portal_read.staff_case_detail",
    ]
    assert {r["decision"] for r in rows} == {"ALLOW"}
    assert [r["timestamp"] for r in rows] == [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2)]
    # Encadeado ao elo da conclusao: a primeira leitura aponta para ele.
    assert rows[0]["prev_record_hash"] == prior_hash
    assert [r["record_hash"] for r in rows] == [h1, h2, h3]
    for row in rows:
        basis = json.loads(row["decision_basis"])
        assert set(basis) == READ_AUDIT_DETAIL_FIELDS
        assert basis["principal_ref"] == "principal-7" and basis["session_ref"] == "session-9"
    assert json.loads(rows[2]["decision_basis"])["resource_ref"] == "case_1"
    # Nenhuma linha de dedup: uma leitura nunca e' deduplicada.
    assert await db["admin"].fetchval(f'SELECT count(*) FROM "{db["schema"]}".audit_emit_dedup') == 0

    result = await verify_chain(db["dsn"], db["schema"])
    assert result.valid, result


async def test_a_tenant_schema_without_the_chain_refuses_instead_of_swallowing():
    raw, schema, admin, pool = await _schema(migrate=False)
    try:
        scope = Scope(tenant=schema, environment="test", workload_ref="portal-bff")
        audit = PostgresReadAccessAudit(scope=scope, pool=pool)
        with pytest.raises(AuditPersistenceError):
            await audit.record(_entry(scope, datetime.now(UTC)))
    finally:
        await _drop(admin, pool, schema)
