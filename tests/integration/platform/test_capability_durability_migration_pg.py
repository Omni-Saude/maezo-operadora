"""DUR0/0018 real PostgreSQL migration proofs; ROOT owns the serial DB lane.

The full Alembic chain reaches 0017 in an isolated generated tenant schema, then
0018 is applied through the real environment. Synthetic journal metadata exercises
DDL only: no source/provider/privacy/production grant or retention qualification.
No CIB client or engine is called, and no skip/xfail replaces a PostgreSQL failure.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.gateway.capabilities.durability.models import JournalCallTechnicalStatus
from maezo.gateway.capabilities.durability.postgres import PostgresDurabilityJournal
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.gateway.capabilities.durability.fixtures import (
    SyntheticProofs,
    binding,
    descriptor,
    dispatch_evidence,
    inbox,
    observation,
    outbox,
    wait,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_TABLES = {
    "v21_journey_journal",
    "v21_capability_command",
    "v21_journal_observation",
    "v21_journal_inbox",
    "v21_external_wait",
    "v21_journal_outbox",
}
_FUNCTIONS = {"v21_journal_immutable", "v21_command_guard", "v21_outbox_guard"}
_TRIGGERS = {
    ("v21_journal_observation", "v21_observation_immutable"),
    ("v21_journal_inbox", "v21_inbox_immutable"),
    ("v21_capability_command", "v21_command_transition"),
    ("v21_journal_outbox", "v21_outbox_transition"),
}
_REPO = Path(__file__).resolve().parents[3]
_MIGRATION = "maezo.platform.migrations.versions.0018_capability_durability_journal"


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable():
    """PostgreSQL-only migration proof; no engine is mocked or invoked."""


@pytest.fixture
async def migration_db(monkeypatch):
    dsn = normalize_dsn(_default_test_dsn())
    schema = "dur1m_" + uuid4().hex
    admin = await asyncpg.connect(dsn, server_settings={"search_path": "pg_catalog"})
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    config = Config()
    config.set_main_option("script_location", str(_REPO / "src/maezo/platform/migrations"))
    config.cmd_opts = SimpleNamespace(x=["tenant=" + schema])
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", dsn)
    try:
        # The owned technical PostgreSQL image has no vector extension. Fail if
        # this assumption changes; full-chain legacy extension DDL is DB-global.
        assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
        assert not await admin.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='vector')"
        )
        await asyncio.to_thread(command.upgrade, config, "0017")
        await admin.execute(f'SET search_path TO "{schema}",pg_catalog')
        assert await admin.fetchval(f'SELECT version_num FROM "{schema}_alembic_version"') == "0017"
        yield SimpleNamespace(dsn=dsn, schema=schema, admin=admin, config=config)
    finally:
        await admin.execute("SET search_path TO pg_catalog")
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def _objects(db):
    tables = await db.admin.fetch(
        "SELECT c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=$1 AND c.relkind IN ('r','p')",
        db.schema,
    )
    functions = await db.admin.fetch(
        "SELECT p.proname,p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=$1",
        db.schema,
    )
    return ({r["relname"]: r["oid"] for r in tables}, {r["proname"]: r["oid"] for r in functions})


async def _upgrade(db):
    await asyncio.to_thread(command.upgrade, db.config, "0018")
    assert await db.admin.fetchval(f'SELECT version_num FROM "{db.schema}_alembic_version"') == "0018"


async def _direct_migration(db, direction, search_path):
    """Real Operations/transaction for explicit unsafe-schema guard probes only."""
    engine = create_async_engine(make_url(db.dsn).set(drivername="postgresql+asyncpg"), hide_parameters=True)
    try:
        async with engine.begin() as connection:
            from sqlalchemy import text

            await connection.execute(text("SET LOCAL search_path TO " + search_path))

            def apply(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    getattr(importlib.import_module(_MIGRATION), direction)()

            await connection.run_sync(apply)
    finally:
        await engine.dispose()


def _recorded(result):
    assert result.technical_status == JournalCallTechnicalStatus.RECORDED, result
    assert result.snapshot is not None
    return result.snapshot


async def _populate(db):
    b = binding(db.schema)
    pool = await asyncpg.create_pool(
        db.dsn, min_size=1, max_size=2, server_settings={"search_path": "pg_catalog"}
    )
    try:
        journal = PostgresDurabilityJournal(pool=pool, binding=b, proofs=SyntheticProofs(), enabled=True)
        d = descriptor(b)
        c = _recorded(await journal.record_command(b, d, 0))
        token = _recorded(await journal.begin_dispatch(b, c.handle, dispatch_evidence(b, d), 1))
        obs = observation(b, d)
        _recorded(
            await journal.record_verified_result(
                b, c.handle, token.dispatch_ref, obs, (outbox(c.handle),), (wait(c.handle),), 2
            )
        )
        _recorded(
            await journal.ingest_verified_observation(
                b, inbox(c.handle, obs), None, (outbox(c.handle),), (wait(c.handle),), 3
            )
        )
    finally:
        await pool.close()
    assert await db.admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 4
    return {
        table: [dict(row) for row in await db.admin.fetch(f"SELECT * FROM {table}")]
        for table in sorted(_TABLES)
    }


async def test_full_chain_forward_tables_constraints_triggers_and_public_revokes(migration_db):
    db = migration_db
    before_tables, before_functions = await _objects(db)
    # Prove explicit REVOKE overrides permissive tenant-scoped defaults, rather
    # than merely observing PostgreSQL's usual absence of table PUBLIC grants.
    await db.admin.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{db.schema}" GRANT ALL ON TABLES TO PUBLIC')
    await _upgrade(db)
    after_tables, after_functions = await _objects(db)
    assert set(after_tables) - set(before_tables) == _TABLES
    assert set(after_functions) - set(before_functions) == _FUNCTIONS
    assert all(after_tables[name] == oid for name, oid in before_tables.items())
    assert all(after_functions[name] == oid for name, oid in before_functions.items())
    triggers = await db.admin.fetch(
        "SELECT c.relname,t.tgname,t.tgenabled::text AS tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND NOT t.tgisinternal "
        "AND c.relname=ANY($2::text[])",
        db.schema,
        sorted(_TABLES),
    )
    assert {(r["relname"], r["tgname"]) for r in triggers} == _TRIGGERS
    assert all(r["tgenabled"] == "O" for r in triggers)
    constraints = await db.admin.fetch(
        "SELECT c.relname,x.contype::text AS contype,x.conname FROM pg_constraint x "
        "JOIN pg_class c ON c.oid=x.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND c.relname=ANY($2::text[])",
        db.schema,
        sorted(_TABLES),
    )
    assert {r["relname"] for r in constraints if r["contype"] == "p"} == _TABLES
    assert sum(r["contype"] == "f" for r in constraints) == 8
    assert any(r["conname"] == "v21_command_head_fk" and r["contype"] == "f" for r in constraints)
    assert (
        await db.admin.fetchval(
            "SELECT i.indisunique FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=$1 AND c.relname='v21_outbox_dedupe'",
            db.schema,
        )
        is True
    )
    assert (
        await db.admin.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace, "
            "LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE n.nspname=$1 AND c.relname=ANY($2::text[]) AND a.grantee=0",
            db.schema,
            sorted(_TABLES),
        )
        == 0
    )
    assert (
        await db.admin.fetchval(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace, "
            "LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE n.nspname=$1 AND p.proname=ANY($2::text[]) AND a.grantee=0",
            db.schema,
            sorted(_FUNCTIONS),
        )
        == 0
    )


async def test_migrated_constraints_and_immutable_triggers_reject_writes_without_evidence_loss(migration_db):
    db = migration_db
    await _upgrade(db)
    before = await _populate(db)
    checks = [
        (asyncpg.UniqueViolationError, "INSERT INTO v21_journey_journal SELECT * FROM v21_journey_journal"),
        (asyncpg.CheckViolationError, "UPDATE v21_journey_journal SET journal_revision=-1"),
        (asyncpg.CheckViolationError, "UPDATE v21_journey_journal SET binding='[]'::jsonb"),
        (
            asyncpg.ForeignKeyViolationError,
            "INSERT INTO v21_journal_observation SELECT environment_ref,tenant_ref,legal_entity_ref,"
            "'missing-command','unit-fk-probe',observation,causal_intents,observed_at "
            "FROM v21_journal_observation",
        ),
        (asyncpg.RaiseError, "UPDATE v21_journal_observation SET observation=observation"),
        (asyncpg.RaiseError, "UPDATE v21_journal_inbox SET observation=observation"),
        (
            asyncpg.RaiseError,
            "UPDATE v21_capability_command SET idempotency_key='changed',"
            "descriptor=jsonb_set(descriptor,'{envelope,idempotency_key}','\"changed\"')",
        ),
        (
            asyncpg.RaiseError,
            "UPDATE v21_capability_command SET snapshot=jsonb_set(snapshot,'{dispatch_ref}','\"reset\"')",
        ),
        (
            asyncpg.RaiseError,
            "UPDATE v21_journal_outbox SET "
            "snapshot=jsonb_set(snapshot,'{technical_state}','\"ACK_RECORDED\"')",
        ),
    ]
    for error, sql in checks:
        with pytest.raises(error):
            async with db.admin.transaction():
                await db.admin.execute(sql)
    assert {
        table: [dict(row) for row in await db.admin.fetch(f"SELECT * FROM {table}")]
        for table in sorted(_TABLES)
    } == before


async def test_empty_downgrade_removes_only_journal_and_preserves_0017_objects(migration_db):
    db = migration_db
    before = await _objects(db)
    await _upgrade(db)
    await asyncio.to_thread(command.downgrade, db.config, "0017")
    assert await _objects(db) == before
    assert await db.admin.fetchval(f'SELECT version_num FROM "{db.schema}_alembic_version"') == "0017"


async def test_populated_downgrade_refuses_and_preserves_all_six_tables(migration_db):
    db = migration_db
    await _upgrade(db)
    before = await _populate(db)
    objects = await _objects(db)
    assert all(len(rows) == 1 for rows in before.values())
    with pytest.raises(
        DBAPIError, match="populated capability journal requires qualified lifecycle disposition"
    ):
        await asyncio.to_thread(command.downgrade, db.config, "0017")
    assert await _objects(db) == objects
    assert await db.admin.fetchval(f'SELECT version_num FROM "{db.schema}_alembic_version"') == "0018"
    assert {
        table: [dict(row) for row in await db.admin.fetch(f"SELECT * FROM {table}")]
        for table in sorted(_TABLES)
    } == before


@pytest.mark.parametrize(
    "search_path",
    ['"public"', '"pg_catalog"', '"information_schema"', '"dur1m_missing",public', '"dur1m_missing"'],
)
async def test_upgrade_refuses_public_system_or_missing_fallback_without_partial_ddl(
    migration_db, search_path
):
    db = migration_db
    before = await _objects(db)
    global_before = await db.admin.fetch(
        "SELECT n.nspname,c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=ANY($1::text[]) ORDER BY n.nspname,c.relname",
        sorted(_TABLES),
    )
    with pytest.raises(DBAPIError, match="capability journal requires explicit tenant schema"):
        await _direct_migration(db, "upgrade", search_path)
    assert await _objects(db) == before
    assert await db.admin.fetchval(f'SELECT version_num FROM "{db.schema}_alembic_version"') == "0017"
    assert (
        await db.admin.fetch(
            "SELECT n.nspname,c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname=ANY($1::text[]) ORDER BY n.nspname,c.relname",
            sorted(_TABLES),
        )
        == global_before
    )


@pytest.mark.parametrize("search_path", ['"public"', '"pg_catalog"'])
async def test_downgrade_refuses_fallback_and_preserves_tenant_evidence(migration_db, search_path):
    db = migration_db
    await _upgrade(db)
    before = await _populate(db)
    objects = await _objects(db)
    with pytest.raises(DBAPIError, match="capability journal requires explicit tenant schema"):
        await _direct_migration(db, "downgrade", search_path)
    assert await _objects(db) == objects
    assert {
        table: [dict(row) for row in await db.admin.fetch(f"SELECT * FROM {table}")]
        for table in sorted(_TABLES)
    } == before


async def test_ephemeral_role_has_no_implicit_access_or_ddl_authority(migration_db):
    db = migration_db
    await _upgrade(db)
    role = "dur1m_role_" + uuid4().hex
    await db.admin.execute(
        f'CREATE ROLE "{role}" '
        "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
    )
    try:
        await db.admin.execute(f'GRANT USAGE ON SCHEMA "{db.schema}" TO "{role}"')
        for table in sorted(_TABLES):
            assert not await db.admin.fetchval(
                "SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')",
                role,
                f"{db.schema}.{table}",
            )
        for function in sorted(_FUNCTIONS):
            assert not await db.admin.fetchval(
                "SELECT has_function_privilege($1,$2,'EXECUTE')", role, f"{db.schema}.{function}()"
            )
        await db.admin.execute(f'SET ROLE "{role}"')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.admin.execute("SELECT * FROM v21_journey_journal")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.admin.execute("CREATE TABLE unit_unauthorized_ddl (value integer)")
        await db.admin.execute("RESET ROLE")
        await db.admin.execute(f'GRANT SELECT ON TABLE v21_journey_journal TO "{role}"')
        await db.admin.execute(f'SET ROLE "{role}"')
        assert await db.admin.fetchval("SELECT count(*) FROM v21_journey_journal") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.admin.execute("DELETE FROM v21_journey_journal")
    finally:
        await db.admin.execute("RESET ROLE")
        await db.admin.execute(f'REVOKE ALL ON TABLE v21_journey_journal FROM "{role}"')
        await db.admin.execute(f'REVOKE ALL ON SCHEMA "{db.schema}" FROM "{role}"')
        await db.admin.execute(f'DROP ROLE "{role}"')
