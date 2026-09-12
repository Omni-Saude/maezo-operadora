"""Actual populated Alembic 0010 -> 0011 -> 0012 lifecycle (ADR-0049 D4/D9).

Root owns the PostgreSQL service. This module creates only a random tenant schema;
no engine simulation, stamping, reconstructed DDL, marker backfill or 0009 downgrade.
0012 downgrade intentionally removes portal identity tables; pre-0012 data survives.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.a2a.test_atomic_admission_live_pg import dispatcher_for, envelope_for
from tests.unit.a2a.test_envelope_signing import _signer, _verifier
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.portal.test_human_session import membership
from tests.unit.portal.test_human_session_postgres import seed_membership, session, transaction

from maezo.a2a.dispatcher import HandlerOutput
from maezo.a2a.facts import TOPIC_REQUESTED, DelegationFactKind, build_fact
from maezo.a2a.idempotency import CLAIM_SQL
from maezo.a2a.outbox import PostgresFactOutbox
from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain
from maezo.portal.api.postgres import PostgresIdentityStore

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]
_OLD_TABLES = ("a2a_idempotency", "audit_chain", "audit_emit_dedup", "a2a_fact_outbox", "driver_idempotency")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(autouse=True)
def _skip_if_postgres_unreachable() -> None:
    """Root-cause R3-Q3 (release-capability-floor §5): this suite was written on the train,
    where CI always had the compose stack up, so it never gained the loud-skip guard every
    other `*_live_pg.py` suite under `tests/unit/**` carries (see `test_outbox_live_pg.py`).
    Unreachable Postgres must SKIP loudly here too, never raise a raw `OSError` mid-test."""
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — foundation migrations live tests SKIPPED (visible, not "
            "silent). Start it with `docker compose --profile core up -d postgres` and re-run "
            "this file."
        )
_PORTAL_TABLES = ("portal_login_transactions", "portal_code_claims", "portal_sessions", "portal_memberships")


async def _migrate(dsn, tenant, direction, revision):
    """Use the real CLI/env.py and version table; URL is passed only in the child environment."""
    env = dict(os.environ, ALEMBIC_DATABASE_URL=dsn)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-x",
        f"tenant={tenant}",
        direction,
        revision,
        cwd=_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 60)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _snapshot(conn, tables, *, marker=None):
    result = {}
    for table in tables:
        rows = [dict(row) for row in await conn.fetch(f'SELECT * FROM "{table}"')]
        if marker is not None and table == "a2a_idempotency":
            for row in rows:
                value = row.pop("requested_enqueued_at")
                assert (value is None) == (marker == "null")
        result[table] = sorted(rows, key=repr)
    return result


async def _assert_version(conn, tenant, revision):
    assert await conn.fetchval(f'SELECT version_num FROM "{tenant}_alembic_version"') == revision


@pytest.mark.parametrize("has_requested", [False, True])
async def test_populated_linear_upgrade_replay_and_bounded_downgrade(has_requested):
    dsn = _default_test_dsn()
    tenant = "pf" + uuid.uuid4().hex[:20]
    calls = []

    async def handler(envelope):
        calls.append(envelope.task_id)
        return HandlerOutput(output_ref="fhir://Task/new-result")

    async with AsyncExitStack() as cleanup:
        admin = await asyncpg.connect(dsn)
        cleanup.push_async_callback(admin.close)
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
        cleanup.push_async_callback(admin.execute, f'DROP SCHEMA "{tenant}" CASCADE')
        await _migrate(dsn, tenant, "upgrade", "0010")
        conn = await asyncpg.connect(dsn)
        cleanup.push_async_callback(conn.close)
        await conn.execute(f'SET search_path TO "{tenant}"')
        assert await conn.fetchval("SELECT current_schema()") == tenant
        await _assert_version(conn, tenant, "0010")
        audit = PostgresAuditSink(dsn, tenant)
        cleanup.push_async_callback(audit.aclose)
        outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
        cleanup.push_async_callback(outbox.aclose)
        envelopes = [replace(envelope_for(tenant), task_id=f"legacy-{s}") for s in ("processing", "done")]
        for envelope in envelopes:
            await conn.fetchval(CLAIM_SQL, envelope.task_id, tenant, "unknown", "unknown", "unknown")
            await audit.emit_once(
                AuditRecord(
                    agent_id="rafael",
                    tenant_id=tenant,
                    agent_version="1.0.0",
                    action="foundation-migration-sentinel",
                    decision="ALLOW",
                    details={"task": envelope.task_id, "sentinel": "é"},
                ),
                dedup_key="sentinel:" + envelope.task_id,
            )
            kinds = [DelegationFactKind.COMPLETED]
            if has_requested:
                kinds.append(DelegationFactKind.REQUESTED)
            for kind in kinds:
                fact = build_fact(
                    kind,
                    task_id=envelope.task_id,
                    task_type=envelope.task_type,
                    tenant=tenant,
                    origin=envelope.origin,
                    target=envelope.target,
                    delegation_chain=envelope.delegation_chain,
                )
                await outbox.enqueue(fact.topic, fact.to_value(), key=tenant.encode())
        await conn.execute(
            "UPDATE a2a_idempotency SET status='done', result=$1::jsonb, completed_at=now() "
            "WHERE task_id='legacy-done'",
            json.dumps(
                dict(
                    success=True,
                    output_ref="fhir://Task/old-result",
                    rejection_reason=None,
                    detail="preserve é",
                    meta={"sentinel": [1, "é"]},
                )
            ),
        )
        await conn.execute(
            "INSERT INTO driver_idempotency (key,tenant,expires_at,status) "
            "VALUES ('wa:in:synthetic:hk1_fixture', $1, now()+interval '1 day', 'pending')",
            tenant,
        )
        before = await _snapshot(conn, _OLD_TABLES)
        assert all(before.values()), "each old store must have actual sentinel rows"
        assert (await verify_chain(dsn, tenant)).valid
        for target in ("0011", "0012"):
            await _migrate(dsn, tenant, "upgrade", target)
            await _assert_version(conn, tenant, target)
            assert await _snapshot(conn, _OLD_TABLES, marker="null") == before
            assert (await verify_chain(dsn, tenant)).valid
        assert all(not rows for rows in (await _snapshot(conn, _PORTAL_TABLES)).values())

        url = make_url(dsn).set(drivername="postgresql+asyncpg")

        def identity_engine():
            engine = create_async_engine(
                url,
                echo=False,
                hide_parameters=True,
                connect_args={"server_settings": {"search_path": tenant}},
            )
            cleanup.push_async_callback(engine.dispose)
            return engine

        first_engine = identity_engine()
        store = PostgresIdentityStore(tenant, first_engine)
        tx, old_session, review = transaction(), session(), membership(tenant=tenant)
        await store.put_transaction(tx)
        await store.claim_code("synthetic-code-hash", datetime.now(UTC) + timedelta(hours=1))
        await store.put_session(old_session, None)
        await seed_membership(first_engine, review)
        portal_before = await _snapshot(conn, _PORTAL_TABLES)
        assert all(portal_before.values())
        await first_engine.dispose()
        restarted = PostgresIdentityStore(tenant, identity_engine())
        assert await restarted.get_session(old_session.secret_hash, datetime.now(UTC)) == old_session
        assert await restarted.get_membership(review.issuer, review.subject) == review
        assert not await restarted.claim_code("synthetic-code-hash", datetime.now(UTC) + timedelta(hours=1))
        await _migrate(dsn, tenant, "upgrade", "0012")
        assert await _snapshot(conn, _PORTAL_TABLES) == portal_before
        assert await _snapshot(conn, _OLD_TABLES, marker="null") == before

        async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
            dispatcher._envelope_verifier = _verifier()
            signed = _signer(tenant=tenant).sign(envelopes[0])
            assert _verifier().verify(signed)
            assert not (await dispatcher.delegate(replace(signed, target="tampered-target"))).success
            assert calls == []
            assert await _snapshot(conn, _OLD_TABLES, marker="null") == before
            for envelope in envelopes:
                result = await dispatcher.delegate(_signer(tenant=tenant).sign(envelope))
                assert result.success
                if envelope.task_id == "legacy-done":
                    assert result.idempotent_replay and result.output_ref == "fhir://Task/old-result"
        assert calls == ["legacy-processing"]
        after_replay = await _snapshot(conn, _OLD_TABLES)
        assert all(row["requested_enqueued_at"] is not None for row in after_replay["a2a_idempotency"])
        for table in ("audit_chain", "audit_emit_dedup", "a2a_fact_outbox", "driver_idempotency"):
            assert all(row in after_replay[table] for row in before[table]), table
        requested = await conn.fetch("SELECT payload FROM a2a_fact_outbox WHERE topic=$1", TOPIC_REQUESTED)
        assert len(requested) == 2
        assert {json.loads(bytes(row["payload"]))["task_id"] for row in requested} == {
            envelope.task_id for envelope in envelopes
        }
        assert await _snapshot(conn, _PORTAL_TABLES) == portal_before
        assert (await verify_chain(dsn, tenant)).valid

        await _migrate(dsn, tenant, "downgrade", "0011")
        await _assert_version(conn, tenant, "0011")
        assert await _snapshot(conn, _OLD_TABLES) == after_replay
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename LIKE 'portal_%')",
            tenant,
        )
        await _migrate(dsn, tenant, "downgrade", "0010")
        await _assert_version(conn, tenant, "0010")
        expected = await _snapshot(conn, _OLD_TABLES)
        for row in after_replay["a2a_idempotency"]:
            del row["requested_enqueued_at"]
        assert expected == after_replay
        assert (await verify_chain(dsn, tenant)).valid
        await _migrate(dsn, tenant, "upgrade", "0012")
        await _assert_version(conn, tenant, "0012")
        assert await _snapshot(conn, _OLD_TABLES, marker="null") == expected
        assert all(not rows for rows in (await _snapshot(conn, _PORTAL_TABLES)).values())
