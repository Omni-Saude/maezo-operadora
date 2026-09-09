"""Real PostgreSQL-only integration; run explicitly with -m integration and a test database.

No mock engine, SQLite stand-in, skip or live Cognito claim. A disposable unique schema is
created, migration 0012 is actually applied and downgraded, and independent engines race.
The root orchestrator owns execution; author writes but does not run this lane concurrently.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.portal.test_human_session import ISSUER, SUBJECT, config, membership

from maezo.portal.api.auth import AuthenticationError, digest
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import LoginTransaction, SessionRecord
from maezo.portal.api.session import HumanSessionResolver

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def stores():
    raw_dsn = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not raw_dsn:
        pytest.fail("MAEZO_TEST_DATABASE_URL required for real portal identity storage integration")
    url = make_url(raw_dsn).set(drivername="postgresql+asyncpg")
    schema = "portal_identity_test_" + uuid.uuid4().hex
    admin = create_async_engine(url, echo=False, hide_parameters=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engines = [
        create_async_engine(
            url, echo=False, hide_parameters=True, connect_args={"server_settings": {"search_path": schema}}
        )
        for _ in range(2)
    ]
    migration = importlib.import_module("maezo.platform.migrations.versions.0012_portal_identity_session")

    def migrate(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, direction)()

    try:
        async with engines[0].begin() as connection:
            await connection.run_sync(migrate, "upgrade")
        yield [PostgresIdentityStore("test-tenant", e) for e in engines], engines
        async with engines[0].begin() as connection:
            await connection.run_sync(migrate, "downgrade")
            tables = (
                (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}
                    )
                )
                .scalars()
                .all()
            )
            assert tables == []
    finally:
        for engine in engines:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def transaction(**updates):
    now = datetime.now(UTC)
    values = dict(
        state_hash=digest("state"),
        browser_hash=digest("browser"),
        nonce="nonce",
        verifier="verifier",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        return_path="/",
    )
    values.update(updates)
    return LoginTransaction(**values)


def session(secret="A" * 43, **updates):
    now = datetime.now(UTC)
    values = dict(
        secret_hash=digest(secret),
        session_ref="session-ref",
        csrf_token="csrf-token",
        issuer=ISSUER,
        subject=SUBJECT,
        principal_ref="human-internal-1",
        membership_revision=1,
        authenticated_at=now - timedelta(seconds=10),
        expires_at=now + timedelta(minutes=20),
    )
    values.update(updates)
    return SessionRecord(**values)


async def seed_membership(engine, row):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO portal_memberships (tenant, issuer, subject, principal_ref, payload) "
                "VALUES (:tenant, :issuer, :subject, :principal, :payload) "
                "ON CONFLICT (tenant,issuer,subject) DO UPDATE SET payload=EXCLUDED.payload"
            ),
            {
                "tenant": row.tenant,
                "issuer": row.issuer,
                "subject": row.subject,
                "principal": row.principal_ref,
                "payload": row.model_dump_json(),
            },
        )


async def test_postgres_atomic_transaction_and_code_across_workers(stores):
    workers, _ = stores
    tx = transaction()
    await workers[0].put_transaction(tx)
    assert await workers[1].consume_transaction(tx.state_hash, "wrong-browser", datetime.now(UTC)) is None
    results = await asyncio.gather(
        *(
            workers[i % 2].consume_transaction(tx.state_hash, tx.browser_hash, datetime.now(UTC))
            for i in range(12)
        )
    )
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result is not None) == tx
    claimed = await asyncio.gather(
        *(
            workers[i % 2].claim_code(digest("single-code"), datetime.now(UTC) + timedelta(days=1))
            for i in range(12)
        )
    )
    assert claimed.count(True) == 1
    assert claimed.count(False) == 11


async def test_postgres_session_rotation_revocation_and_cross_tenant(stores):
    workers, engines = stores
    old = session()
    await workers[0].put_session(old, None)
    assert await workers[1].get_session(old.secret_hash, datetime.now(UTC)) == old
    foreign = PostgresIdentityStore("foreign", engines[1])
    assert await foreign.get_session(old.secret_hash, datetime.now(UTC)) is None
    await foreign.revoke_session(old.secret_hash)
    assert await workers[1].get_session(old.secret_hash, datetime.now(UTC)) == old
    new = session("B" * 43, session_ref="new-ref")
    await workers[1].put_session(new, old.secret_hash)
    assert await workers[0].get_session(old.secret_hash, datetime.now(UTC)) is None
    assert await workers[0].get_session(new.secret_hash, datetime.now(UTC)) == new
    await workers[0].revoke_session(new.secret_hash)
    assert await workers[1].get_session(new.secret_hash, datetime.now(UTC)) is None


async def test_postgres_membership_live_revocation_and_bindings(stores):
    workers, engines = stores
    row = membership()
    await seed_membership(engines[0], row)
    await workers[0].put_session(session(), None)
    resolver = HumanSessionResolver(config(), workers[1])
    resolved = await resolver.resolve("A" * 43)
    assert resolved.principal.principal_ref == row.principal_ref
    assert resolved.principal.membership_revision == 1
    await seed_membership(engines[0], row.model_copy(update={"revoked": True}))
    with pytest.raises(AuthenticationError):
        await resolver.resolve("A" * 43)
    await seed_membership(engines[0], row.model_copy(update={"revision": 2}))
    with pytest.raises(AuthenticationError):
        await resolver.resolve("A" * 43)
    assert await workers[0].get_session(digest("A" * 43), datetime.now(UTC)) is None
    assert await workers[0].get_membership(ISSUER, "someone-else") is None
    assert await PostgresIdentityStore("foreign", engines[1]).get_membership(ISSUER, SUBJECT) is None


async def test_postgres_persistence_restart_and_expired_cleanup(stores):
    workers, engines = stores
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)
    await workers[0].put_transaction(transaction(expires_at=expired))
    await workers[0].put_session(session(expires_at=expired), None)
    await workers[0].claim_code(digest("expired-code"), expired)
    live = session("B" * 43)
    await workers[0].put_session(live, None)
    # dispose closes every pooled connection; new requests must reload persistent rows.
    await workers[0].close()
    restarted = PostgresIdentityStore("test-tenant", engines[0])
    assert await restarted.get_session(digest("B" * 43), now) == live
    assert await restarted.get_session(digest("A" * 43), now) is None
    assert await restarted.consume_transaction(digest("state"), digest("browser"), now) is None
    await restarted.purge_expired(now)
    async with engines[1].connect() as connection:
        for table in ("portal_login_transactions", "portal_code_claims"):
            assert (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
        assert (await connection.execute(text("SELECT count(*) FROM portal_sessions"))).scalar_one() == 1


async def test_postgres_rotation_rollback_preserves_old_session(stores):
    workers, engines = stores
    old = session()
    await workers[0].put_session(old, None)
    new = session("B" * 43)
    await workers[0].put_session(new, None)
    # Real PK collision after deleting old row aborts the whole rotation transaction.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await workers[1].put_session(new, old.secret_hash)
    assert await workers[0].get_session(old.secret_hash, datetime.now(UTC)) == old
