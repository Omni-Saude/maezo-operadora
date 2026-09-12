"""Real Alembic current-head lifecycle, preserving the historical 0012 test recipe."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.gateway.human.test_durable_projection import assignment, evidence
from tests.unit.portal.test_foundation_migrations_live_pg import _assert_version, _migrate, _snapshot

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain
from maezo.gateway.human.outbox import PostgresHumanOutbox
from maezo.gateway.human.projection import project_assignment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual_head_0013_preserves_populated_0012_and_bounded_downgrade():
    dsn = _default_test_dsn()
    raw = normalize_dsn(dsn)
    tenant = "human_mig_" + uuid4().hex
    admin = await asyncpg.connect(raw)
    await admin.execute(f'CREATE SCHEMA "{tenant}"')
    pool = await asyncpg.create_pool(raw, min_size=1, max_size=2)
    audit = PostgresAuditSink(raw, tenant)
    try:
        await _migrate(raw, tenant, "upgrade", "0012")
        await admin.execute(f'SET search_path TO "{tenant}"')
        await _assert_version(admin, tenant, "0012")
        await audit.emit(
            AuditRecord(
                agent_id="migration-fixture",
                tenant_id=tenant,
                agent_version="1",
                action="preserve",
                decision="ALLOW",
                details={"sentinel": "é"},
            )
        )
        await admin.execute(
            "INSERT INTO portal_code_claims VALUES "
            "($1,'migration-sentinel',clock_timestamp()+interval '1 hour')",
            tenant,
        )
        tables = (
            "audit_chain",
            "audit_emit_dedup",
            "a2a_idempotency",
            "a2a_fact_outbox",
            "driver_idempotency",
            "portal_code_claims",
            "portal_memberships",
            "portal_sessions",
            "portal_login_transactions",
        )
        before = await _snapshot(admin, tables)
        await _migrate(raw, tenant, "upgrade", "head")
        await _assert_version(admin, tenant, "0014")
        assert await _snapshot(admin, tables) == before
        for name in ("human_command_outbox", "human_command_delivery"):
            assert await admin.fetchval(f"SELECT count(*) FROM {name}") == 0
        a = assignment()
        scope = a.scope.model_copy(update={"tenant": tenant})
        a = a.model_copy(
            update={"scope": scope, "principal": a.principal.model_copy(update={"tenant": tenant})}
        )
        outbox = PostgresHumanOutbox(scope=scope, pool=pool)
        await outbox.persist(
            project_assignment(a, evidence(a)), evidence_valid_until=datetime.now(UTC) + timedelta(minutes=5)
        )
        await _migrate(raw, tenant, "upgrade", "head")
        assert await admin.fetchval("SELECT count(*) FROM human_command_outbox") == 1
        retained = await _snapshot(admin, tables)
        await _migrate(raw, tenant, "downgrade", "0012")
        await _assert_version(admin, tenant, "0012")
        assert await _snapshot(admin, tables) == retained
        assert await admin.fetchval("SELECT to_regclass('human_command_outbox')") is None
        assert await admin.fetchval("SELECT to_regclass('human_command_delivery')") is None
        assert (await verify_chain(raw, tenant)).valid
        await _migrate(raw, tenant, "upgrade", "head")
        await _assert_version(admin, tenant, "0014")
        assert await _snapshot(admin, tables) == retained
        assert await admin.fetchval("SELECT count(*) FROM human_command_outbox") == 0
    finally:
        await audit.aclose()
        await pool.close()
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        await admin.close()
