"""D6 actual PostgreSQL storage tests; ROOT owns serial execution.

Requires MAEZO_TEST_DATABASE_URL and a disposable test database. No engine transport
is mocked or invoked. Receipt bytes below are typed *storage inputs* for local TX
tests, never evidence of CIB execution or an integration relay journey. The separate
combined mTLS/engine journey must establish that boundary with the real engine.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.gateway.human.test_durable_projection import assignment, evidence, receipt_payload

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain
from maezo.gateway.human.outbox import (
    CommandConflictError,
    HumanOutboxError,
    LeaseLostError,
    PostgresHumanAdmission,
    PostgresHumanOutbox,
)
from maezo.gateway.human.projection import EvidenceReferenceSource, project_assignment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def db():
    dsn = _default_test_dsn()
    raw = normalize_dsn(dsn)
    schema = "human_d6_" + uuid4().hex
    admin = await asyncpg.connect(raw)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    engine = create_async_engine(
        make_url(dsn).set(drivername="postgresql+asyncpg"),
        echo=False,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:

        def apply(sync):
            with Operations.context(MigrationContext.configure(sync)):
                for name in ("0002_audit_chain", "0005_audit_emit_dedup", "0013_human_command_outbox"):
                    importlib.import_module("maezo.platform.migrations.versions." + name).upgrade()

        await conn.run_sync(apply)
    # Deliberately unconfigured pool. Production adapter must bind every transaction.
    pool = await asyncpg.create_pool(
        raw, min_size=1, max_size=4, server_settings={"application_name": schema}
    )
    a = assignment()
    scope = a.scope.model_copy(update={"tenant": schema})
    a = a.model_copy(update={"scope": scope, "principal": a.principal.model_copy(update={"tenant": schema})})
    command = project_assignment(a, evidence(a))
    store = PostgresHumanOutbox(scope=scope, pool=pool)
    await admin.execute(f'SET search_path TO "{schema}"')
    try:
        yield dict(
            dsn=raw,
            schema=schema,
            admin=admin,
            pool=pool,
            store=store,
            command=command,
            assignment=a,
            engine=engine,
        )
    finally:
        await pool.close()
        await engine.dispose()
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def persist(db, command=None):
    return await db["store"].persist(
        command or db["command"], evidence_valid_until=datetime.now(UTC) + timedelta(minutes=5)
    )


async def counts(db):
    return tuple(
        [
            await db["admin"].fetchval(f"SELECT count(*) FROM {table}")
            for table in ("audit_chain", "audit_emit_dedup", "human_command_outbox", "human_command_delivery")
        ]
    )


async def assert_chain(db, count):
    result = await verify_chain(db["dsn"], db["schema"])
    assert result.valid, result
    assert await db["admin"].fetchval("SELECT count(*) FROM audit_chain") == count


async def test_admission_commits_all_four_rows_and_idempotent_concurrent_replicas(db):
    results = await asyncio.gather(*(persist(db) for _ in range(12)))
    assert len({(r.outbox_ref, r.transaction_ref, r.audit_intent_ref) for r in results}) == 1
    assert all(r.status == "pending" for r in results)
    assert await counts(db) == (1, 1, 1, 1)
    await assert_chain(db, 1)
    recovered = PostgresHumanOutbox(scope=db["store"].scope, pool=db["pool"])
    public = await recovered.read_owned(
        db["assignment"].principal, db["command"].task_id, db["command"].command_id
    )
    assert public.status == "pending" and public.engine_receipt_ref is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_revision", "99"),
        ("principal_ref", "other"),
        ("principal_subject", "other"),
        ("task_revision", "99"),
    ],
)
async def test_conflicting_identity_never_changes_winner_or_audit(db, field, value):
    await persist(db)
    before = await db["admin"].fetch("SELECT * FROM human_command_outbox")
    with pytest.raises(CommandConflictError):
        await persist(db, replace(db["command"], **{field: value}))
    assert await counts(db) == (1, 1, 1, 1)
    assert await db["admin"].fetch("SELECT * FROM human_command_outbox") == before
    await assert_chain(db, 1)


@pytest.mark.parametrize("table", ["audit_chain", "human_command_outbox", "human_command_delivery"])
async def test_admission_database_failure_rolls_back_intent_claim_and_outbox(db, table):
    await db["admin"].execute(
        "CREATE FUNCTION d6_abort() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'controlled abort'; END $$"
    )
    await db["admin"].execute(
        f"CREATE TRIGGER d6_abort BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION d6_abort()"
    )
    with pytest.raises(HumanOutboxError):
        await persist(db)
    assert await counts(db) == (0, 0, 0, 0)
    await db["admin"].execute(f"DROP TRIGGER d6_abort ON {table}")
    await persist(db)
    assert await counts(db) == (1, 1, 1, 1)


async def test_deferred_commit_failure_exposes_no_admission_and_rolls_back(db):
    await db["admin"].execute(
        "CREATE FUNCTION d6_commit_abort() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'controlled commit abort'; END $$"
    )
    await db["admin"].execute(
        "CREATE CONSTRAINT TRIGGER d6_commit_abort AFTER INSERT ON "
        "human_command_delivery DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE "
        "FUNCTION d6_commit_abort()"
    )
    with pytest.raises(HumanOutboxError):
        await persist(db)
    assert await counts(db) == (0, 0, 0, 0)


async def test_backend_termination_before_commit_leaves_no_partial_intent(db):
    # Real process/backend kill after audit INSERT while the same admission TX is
    # blocked at its next INSERT. No audit method or engine is mocked.
    blocker = await asyncpg.connect(db["dsn"])
    await blocker.execute(f'SET search_path TO "{db["schema"]}"')
    tx = blocker.transaction()
    await tx.start()
    await blocker.execute("LOCK TABLE human_command_outbox IN ACCESS EXCLUSIVE MODE")
    pending = asyncio.create_task(persist(db))
    try:
        pid = None
        for _ in range(200):
            pid = await db["admin"].fetchval(
                "SELECT pid FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND "
                "wait_event_type='Lock' "
                "AND application_name=$1 AND query LIKE 'SELECT * FROM human_command_outbox%' LIMIT 1",
                db["schema"],
            )
            if pid:
                break
            await asyncio.sleep(0.025)
        assert pid is not None, "actual admission backend must be observed blocked"
        assert await db["admin"].fetchval("SELECT pg_terminate_backend($1)", pid)
        with pytest.raises(HumanOutboxError):
            await pending
    finally:
        await tx.rollback()
        await blocker.close()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert await counts(db) == (0, 0, 0, 0)
    await persist(db)
    await assert_chain(db, 1)


async def test_exclusive_lease_and_restart_takeover_fence_old_worker(db):
    await persist(db)
    claims = await asyncio.gather(*(db["store"].claim(lease_seconds=30) for _ in range(8)))
    first = next(c for c in claims if c is not None)
    assert sum(c is not None for c in claims) == 1
    await db["admin"].execute(
        "UPDATE human_command_delivery SET lease_until=clock_timestamp()-interval '1 second'"
    )
    recovered = PostgresHumanOutbox(scope=db["store"].scope, pool=db["pool"])
    second = await recovered.claim(lease_seconds=30)
    assert second.fence == first.fence + 1 and second.lease_id != first.lease_id
    for method in (
        lambda: recovered.require_lease(first),
        lambda: recovered.retry_later(first, retry_seconds=1),
        lambda: recovered.finish(first, receipt=receipt_payload(db["command"])),
    ):
        with pytest.raises(LeaseLostError):
            await method()
    await recovered.require_lease(second)
    assert await counts(db) == (1, 1, 1, 1)


async def test_local_result_audit_failure_preserves_pending_and_retry(db):
    await persist(db)
    lease = await db["store"].claim(lease_seconds=30)
    await db["admin"].execute(
        "CREATE FUNCTION d6_result_abort() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN IF NEW.action='human_command.result' THEN RAISE EXCEPTION "
        "'controlled result abort'; END IF; RETURN NEW; END $$"
    )
    await db["admin"].execute(
        "CREATE TRIGGER d6_result_abort BEFORE INSERT ON audit_chain FOR EACH ROW "
        "EXECUTE FUNCTION d6_result_abort()"
    )
    with pytest.raises(HumanOutboxError):
        await db["store"].finish(lease, receipt=receipt_payload(db["command"]))
    assert await counts(db) == (1, 1, 1, 1)
    row = await db["admin"].fetchrow("SELECT * FROM human_command_delivery")
    assert row["status"] == "pending" and row["engine_receipt"] is None
    await db["admin"].execute("DROP TRIGGER d6_result_abort ON audit_chain")
    await db["store"].finish(lease, receipt=receipt_payload(db["command"]))
    public = await db["store"].read_owned(
        db["assignment"].principal, lease.command.task_id, lease.command.command_id
    )
    assert public.status == "committed" and public.engine_receipt_ref == "receipt-1"
    assert public.audit_result_ref is not None
    await assert_chain(db, 2)


async def test_terminal_mark_failure_rolls_back_result_audit_and_dedup(db):
    await persist(db)
    lease = await db["store"].claim(lease_seconds=30)
    await db["admin"].execute(
        "CREATE FUNCTION d6_mark_abort() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN IF NEW.status<>'pending' THEN RAISE EXCEPTION 'controlled mark "
        "abort'; END IF; RETURN NEW; END $$"
    )
    await db["admin"].execute(
        "CREATE TRIGGER d6_mark_abort BEFORE UPDATE ON human_command_delivery FOR "
        "EACH ROW EXECUTE FUNCTION d6_mark_abort()"
    )
    with pytest.raises(HumanOutboxError):
        await db["store"].finish(lease, conflict="FORM_NOT_ACTIVATED")
    assert await counts(db) == (1, 1, 1, 1)
    assert await db["admin"].fetchval("SELECT status FROM human_command_delivery") == "pending"


async def test_immutable_payload_and_terminal_result(db):
    await persist(db)
    with pytest.raises(asyncpg.RaiseError):
        await db["admin"].execute("UPDATE human_command_outbox SET principal_ref='other'")
    with pytest.raises(asyncpg.RaiseError):
        await db["admin"].execute("DELETE FROM human_command_outbox")
    lease = await db["store"].claim(lease_seconds=30)
    await db["store"].finish(lease, conflict="FORM_NOT_ACTIVATED")
    with pytest.raises(asyncpg.RaiseError):
        await db["admin"].execute(
            "UPDATE human_command_delivery SET status='pending',technical_code=NULL,audit_result_hash=NULL"
        )
    with pytest.raises(asyncpg.RaiseError):
        await db["admin"].execute("DELETE FROM human_command_delivery")
    await assert_chain(db, 2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "foreign"),
        ("principal_ref", "foreign"),
        ("issuer", "https://foreign.example"),
        ("subject", "foreign"),
    ],
)
async def test_read_requires_original_immutable_principal_identity(db, field, value):
    await persist(db)
    p = db["assignment"].principal.model_copy(update={field: value})
    with pytest.raises(HumanOutboxError):
        await db["store"].read_owned(p, db["command"].task_id, db["command"].command_id)


async def test_reused_pool_never_uses_previous_search_path_and_foreign_schema_cannot_claim(db):
    await persist(db)
    async with db["pool"].acquire() as conn:
        await conn.execute("SET search_path TO public")
    foreign_scope = db["store"].scope.model_copy(update={"tenant": "missing_d6_tenant"})
    with pytest.raises(HumanOutboxError):
        await PostgresHumanOutbox(scope=foreign_scope, pool=db["pool"]).claim(lease_seconds=30)
    assert (await db["store"].claim(lease_seconds=30)).command == db["command"]


async def test_chain_stays_structural_with_clock_skew_and_standalone_emit(db):
    sink = PostgresAuditSink(db["dsn"], db["schema"])
    try:
        await sink.emit(
            AuditRecord(
                agent_id="standalone",
                tenant_id=db["schema"],
                agent_version="v1",
                action="before",
                decision="ALLOW",
                details={},
                timestamp=datetime(2100, 1, 1, tzinfo=UTC),
            )
        )
        await persist(db)
        await sink.emit(
            AuditRecord(
                agent_id="standalone",
                tenant_id=db["schema"],
                agent_version="v1",
                action="after",
                decision="ALLOW",
                details={},
                timestamp=datetime(2000, 1, 1, tzinfo=UTC),
            )
        )
    finally:
        await sink.aclose()
    await assert_chain(db, 3)


async def test_audit_row_tampering_is_not_exposed_as_valid_receipt(db):
    await persist(db)
    await db["admin"].execute("UPDATE audit_chain SET decision_basis='{}'::jsonb")
    with pytest.raises(HumanOutboxError):
        await db["store"].read_owned(
            db["assignment"].principal, db["command"].task_id, db["command"].command_id
        )
    with pytest.raises(HumanOutboxError):
        await db["store"].claim(lease_seconds=30)


async def test_dedicated_admission_requires_exact_trusted_evidence_reference(db):
    class Source(EvidenceReferenceSource):
        scope = db["store"].scope

        async def current_reference(self, command):
            return evidence(command)

    admission = PostgresHumanAdmission(db["store"], Source())
    result = await admission.admit(db["assignment"])
    assert result.status == "pending" and await counts(db) == (1, 1, 1, 1)


async def test_audit_lock_precedes_own_row_locks_during_conflicting_admission(db):
    await persist(db)
    blocker = await asyncpg.connect(db["dsn"])
    tx = blocker.transaction()
    await tx.start()
    await blocker.execute("SELECT pg_advisory_xact_lock(hashtext($1))", db["schema"])
    pending = asyncio.create_task(persist(db))
    try:
        blocked = False
        for _ in range(200):
            blocked = await db["admin"].fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=$1 "
                "AND wait_event_type='Lock' AND query LIKE '%pg_advisory_xact_lock%')",
                db["schema"],
            )
            if blocked:
                break
            await asyncio.sleep(0.025)
        assert blocked, "admission must be waiting for its tenant audit lock"
        async with db["admin"].transaction():
            # This fails immediately if admission took an outbox row lock first.
            await db["admin"].fetch("SELECT * FROM human_command_outbox FOR UPDATE NOWAIT")
    finally:
        await tx.rollback()
        await blocker.close()
    assert (await pending).status == "pending"
    await assert_chain(db, 1)


async def test_lease_expiry_after_terminal_sql_rolls_back_result_and_audit(db):
    await persist(db)
    await db["admin"].execute("CREATE SEQUENCE d6_late_result_called")
    lease = await db["store"].claim(lease_seconds=1)
    # Actual PostgreSQL trigger consumes the remaining lease after UPDATE's WHERE
    # already matched. The post-SQL fence must roll back the complete result TX.
    await db["admin"].execute("""
        CREATE FUNCTION d6_late_result() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.status <> 'pending' THEN
        PERFORM nextval('d6_late_result_called'); PERFORM pg_sleep(1.1); END IF;
        RETURN NEW; END $$
    """)
    await db["admin"].execute("""
        CREATE TRIGGER d6_late_result BEFORE UPDATE ON human_command_delivery
        FOR EACH ROW EXECUTE FUNCTION d6_late_result()
    """)
    with pytest.raises(LeaseLostError):
        await db["store"].finish(lease, conflict="REVISION_CONFLICT")
    assert await db["admin"].fetchval("SELECT is_called FROM d6_late_result_called")
    assert await counts(db) == (1, 1, 1, 1)
    assert await db["admin"].fetchval("SELECT status FROM human_command_delivery") == "pending"
    await assert_chain(db, 1)


async def test_evidence_expiry_after_last_admission_sql_rolls_back_every_row(db):
    await db["admin"].execute("CREATE SEQUENCE d6_late_admission_called")
    await db["admin"].execute("""
        CREATE FUNCTION d6_late_admission() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM nextval('d6_late_admission_called');
        PERFORM pg_sleep(1.1); RETURN NEW; END $$
    """)
    await db["admin"].execute("""
        CREATE TRIGGER d6_late_admission BEFORE INSERT ON human_command_delivery
        FOR EACH ROW EXECUTE FUNCTION d6_late_admission()
    """)
    with pytest.raises(HumanOutboxError):
        await db["store"].persist(
            db["command"], evidence_valid_until=datetime.now(UTC) + timedelta(seconds=1)
        )
    assert await db["admin"].fetchval("SELECT is_called FROM d6_late_admission_called")
    assert await counts(db) == (0, 0, 0, 0)


async def test_result_commit_failure_rolls_back_result_audit_and_terminal_mark(db):
    await persist(db)
    lease = await db["store"].claim(lease_seconds=30)
    await db["admin"].execute("""
        CREATE FUNCTION d6_result_commit_abort() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.status <> 'pending' THEN RAISE EXCEPTION 'controlled result commit abort';
        END IF; RETURN NEW; END $$
    """)
    await db["admin"].execute("""
        CREATE CONSTRAINT TRIGGER d6_result_commit_abort AFTER UPDATE ON human_command_delivery
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION d6_result_commit_abort()
    """)
    with pytest.raises(HumanOutboxError):
        await db["store"].finish(lease, receipt=receipt_payload(db["command"]))
    assert await counts(db) == (1, 1, 1, 1)
    assert await db["admin"].fetchval("SELECT status FROM human_command_delivery") == "pending"
    await assert_chain(db, 1)


async def test_same_task_and_command_are_isolated_in_two_real_tenant_schemas(db):
    await persist(db)
    second = db["schema"] + "_second"
    await db["admin"].execute(f'CREATE SCHEMA "{second}"')
    try:
        async with db["engine"].begin() as conn:
            from sqlalchemy import text

            await conn.execute(text(f'SET LOCAL search_path TO "{second}"'))

            def migrate(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    for name in ("0002_audit_chain", "0005_audit_emit_dedup", "0013_human_command_outbox"):
                        importlib.import_module("maezo.platform.migrations.versions." + name).upgrade()

            await conn.run_sync(migrate)
        a = db["assignment"]
        scope = a.scope.model_copy(update={"tenant": second})
        a = a.model_copy(
            update={"scope": scope, "principal": a.principal.model_copy(update={"tenant": second})}
        )
        c = project_assignment(a, evidence(a))
        other = PostgresHumanOutbox(scope=scope, pool=db["pool"])
        await other.persist(c, evidence_valid_until=datetime.now(UTC) + timedelta(minutes=1))
        one, two = await asyncio.gather(db["store"].claim(lease_seconds=30), other.claim(lease_seconds=30))
        assert one.command.task_id == two.command.task_id and one.command.command_id == two.command.command_id
        assert one.command.tenant != two.command.tenant
        with pytest.raises(HumanOutboxError):
            await db["store"].require_lease(two)
        assert (await verify_chain(db["dsn"], second)).valid
        await assert_chain(db, 1)
    finally:
        await db["admin"].execute(f'DROP SCHEMA "{second}" CASCADE')
