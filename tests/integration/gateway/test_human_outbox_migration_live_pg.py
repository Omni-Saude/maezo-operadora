"""Real Alembic current-head lifecycle, preserving the historical 0012 test recipe."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
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

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions"


def _alembic_head() -> str:
    """Derive the sole Alembic head from `versions/` instead of pinning it.

    Pinning the head here is what broke this suite: 0016 (2026-09-25) and then 0017 landed
    and main's nightly went red on `assert '0017' == '0015'` — an unrelated migration should
    never have to edit this file, the exact lesson `test_migration_0007_amh_inbox.py` already
    recorded for its own fence. What this test owns is that `upgrade head` reaches THE head,
    so the expected version is derived: a revision is the head iff no file declares it as
    `down_revision`, and the chain must yield exactly one. Same extraction as the fences.
    """
    revisions: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert rev is not None, f"{path.name} declares no revision"
        assert down is not None, f"{path.name} declares no down_revision"
        assert rev.group(1) not in revisions, f"duplicate revision id {rev.group(1)}"
        revisions[rev.group(1)] = down.group(1)
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert len(heads) == 1, f"the chain must have exactly one head, got {sorted(heads)}"
    return heads.pop()


async def test_actual_head_0013_preserves_populated_0012_and_bounded_downgrade():
    dsn = _default_test_dsn()
    raw = normalize_dsn(dsn)
    tenant = "human_mig_" + uuid4().hex
    admin = await asyncpg.connect(raw)
    await admin.execute(f'CREATE SCHEMA "{tenant}"')
    pool = await asyncpg.create_pool(raw, min_size=1, max_size=2)
    audit = PostgresAuditSink(raw, tenant)
    try:
        # 0012 is a historical fact under test, not the head: it is the populated base the 0013
        # tables must preserve and the bounded-downgrade target, so it stays a literal even as
        # the head moves (0014 ... 0017 and counting).
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
        # `upgrade head` must land on the current head, so the expected version is derived, never
        # a literal — "0015" went stale the day 0016 landed and the nightly stayed red for it.
        await _migrate(raw, tenant, "upgrade", "head")
        await _assert_version(admin, tenant, _alembic_head())
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
        # Same derivation as the first `upgrade head`: the re-upgrade must also land on the head.
        await _migrate(raw, tenant, "upgrade", "head")
        await _assert_version(admin, tenant, _alembic_head())
        assert await _snapshot(admin, tables) == retained
        assert await admin.fetchval("SELECT count(*) FROM human_command_outbox") == 0
    finally:
        await audit.aclose()
        await pool.close()
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        await admin.close()
