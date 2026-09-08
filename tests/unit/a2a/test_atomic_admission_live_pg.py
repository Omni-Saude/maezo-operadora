"""PLAN-W3-A2A R3/R4/R8-R12: real PostgreSQL, controlled non-engine handler.

No skip fallback: unavailable services fail this integration lane. Migrations
run to the real head; each test owns and drops only its random tenant schema.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

from maezo.a2a.dispatcher import DelegationDispatcher, FactProducer, HandlerOutput
from maezo.a2a.facts import TOPIC_REQUESTED
from maezo.a2a.idempotency import CLAIM_SQL, PostgresIdempotencyStore
from maezo.a2a.outbox import PostgresFactOutbox, PostgresOutboxFactProducer
from maezo.a2a.registry import A2ARegistry
from maezo.gateway.audit_postgres import PostgresAuditSink, verify_chain
from maezo.runtime.agent_runtime.a2a_composition import _require_transactions_or_fail_closed

from .fakes import make_card
from .test_a2a_edge_live_pg import _apply_migrations
from .test_idempotency import _envelope
from .test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration


@pytest.fixture
async def database():
    dsn = _default_test_dsn()
    tenant = "ax" + uuid.uuid4().hex[:16]
    await asyncio.to_thread(_apply_migrations, dsn, tenant)
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'SET search_path TO "{tenant}"')
    try:
        yield dsn, tenant, conn
    finally:
        await conn.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        await conn.close()


def envelope_for(tenant):
    from dataclasses import replace

    return replace(_envelope(), tenant=tenant)


@asynccontextmanager
async def dispatcher_for(dsn, tenant, handler, *, cards=True):
    registry = A2ARegistry()
    if cards:
        registry.register(make_card("rafael", tenant=tenant))
    audit = PostgresAuditSink(dsn, tenant)
    store = PostgresIdempotencyStore(dsn=dsn, tenant=tenant, poll_max_attempts=0)
    outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
    facts = FactProducer(PostgresOutboxFactProducer(outbox))
    transactions = _require_transactions_or_fail_closed(
        runtime_mode="production", tenant=tenant, audit=audit, facts=facts, idempotency=store
    )
    dispatcher = DelegationDispatcher(
        registry=registry,
        handlers={"rafael": handler},
        audit=audit,
        facts=facts,
        idempotency=store,
        transactions=transactions,
    )
    try:
        yield dispatcher, outbox
    finally:
        await outbox.aclose()
        await store.aclose()
        await audit.aclose()


async def counts(conn):
    return tuple(
        [
            await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            for table in ("a2a_idempotency", "audit_chain", "audit_emit_dedup", "a2a_fact_outbox")
        ]
    )


async def install_failure(conn, table, condition="true"):
    await conn.execute(f"""CREATE FUNCTION abort_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF {condition} THEN RAISE EXCEPTION 'synthetic fault'; END IF; RETURN NEW; END $$""")
    await conn.execute(
        f"CREATE TRIGGER injected BEFORE INSERT OR UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION abort_write()"
    )


@pytest.mark.parametrize("table", ["audit_chain", "a2a_fact_outbox"])
async def test_admission_write_failure_rolls_back_all_participants_and_fresh_retry(database, table):
    dsn, tenant, conn = database
    calls = 0

    async def handler(envelope):
        nonlocal calls
        calls += 1
        # Independent connection can see admission; no transaction lock spans handler.
        assert await counts(conn) == (1, 1, 1, 1)
        return HandlerOutput(output_ref="fhir://Task/result")

    await install_failure(conn, table)
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        with pytest.raises(Exception, match="synthetic fault"):
            await dispatcher.delegate(envelope_for(tenant))
    assert calls == 0
    assert await counts(conn) == (0, 0, 0, 0)
    await conn.execute(f"DROP TRIGGER injected ON {table}")
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        assert (await dispatcher.delegate(envelope_for(tenant))).success
    assert calls == 1
    assert await counts(conn) == (1, 2, 2, 2)


@pytest.mark.parametrize(
    ("table", "condition"),
    [
        ("audit_chain", "NEW.decision = 'COMPLETED'"),
        ("a2a_fact_outbox", "NEW.topic LIKE '%completed'"),
        ("a2a_idempotency", "NEW.status = 'done'"),
    ],
)
async def test_terminal_failure_has_no_partial_audit_fact_or_done(database, table, condition):
    dsn, tenant, conn = database

    async def handler(envelope):
        return HandlerOutput(output_ref="fhir://Task/result")

    await install_failure(conn, table, condition)
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        with pytest.raises(Exception, match="synthetic fault"):
            await dispatcher.delegate(envelope_for(tenant))
    assert await counts(conn) == (1, 1, 1, 1)
    assert await conn.fetchval("SELECT status FROM a2a_idempotency") == "processing"
    await conn.execute(f"DROP TRIGGER injected ON {table}")
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        assert (await dispatcher.delegate(envelope_for(tenant))).success
        assert (await dispatcher.delegate(envelope_for(tenant))).idempotent_replay
    assert await counts(conn) == (1, 2, 2, 2)
    assert (await verify_chain(dsn, tenant)).valid


@pytest.mark.parametrize("has_requested", [False, True])
async def test_legacy_claim_recognizes_bytes_or_repairs_only_from_valid_replay(database, has_requested):
    dsn, tenant, conn = database
    envelope = envelope_for(tenant)
    await conn.fetchval(CLAIM_SQL, envelope.task_id, tenant, "unknown", "unknown", "unknown")
    original = None

    async def handler(envelope):
        return HandlerOutput(output_ref="fhir://Task/result")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, outbox):
        if has_requested:
            from maezo.a2a.facts import DelegationFactKind, build_fact

            fact = build_fact(
                DelegationFactKind.REQUESTED,
                task_id=envelope.task_id,
                task_type=envelope.task_type,
                tenant=tenant,
                origin=envelope.origin,
                target=envelope.target,
                delegation_chain=envelope.delegation_chain,
            )
            original = fact.to_value()
            await outbox.enqueue(fact.topic, original, key=tenant.encode())
        assert await conn.fetchval("SELECT requested_enqueued_at FROM a2a_idempotency") is None
        assert (await dispatcher.delegate(envelope)).success
    rows = await conn.fetch("SELECT payload FROM a2a_fact_outbox WHERE topic = $1", TOPIC_REQUESTED)
    assert len(rows) == 1
    if original is not None:
        assert bytes(rows[0]["payload"]) == original
    assert await conn.fetchval("SELECT requested_enqueued_at FROM a2a_idempotency") is not None


async def test_two_dispatchers_enqueue_one_requested_with_handlers_outside_transactions(database):
    dsn, tenant, conn = database
    both = asyncio.Event()
    calls = 0

    async def handler(envelope):
        nonlocal calls
        calls += 1
        if calls == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        return HandlerOutput(output_ref="fhir://Task/result")

    async with dispatcher_for(dsn, tenant, handler) as (a, _), dispatcher_for(dsn, tenant, handler) as (b, _):
        results = await asyncio.gather(a.delegate(envelope_for(tenant)), b.delegate(envelope_for(tenant)))
    assert calls == 2  # Existing best-effort execution semantics; no fabricated exclusive lease.
    assert all(r.success for r in results)
    assert await counts(conn) == (1, 2, 2, 2)
    assert await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox WHERE topic = $1", TOPIC_REQUESTED) == 1
    assert (await verify_chain(dsn, tenant)).valid


async def test_wrong_schema_and_expired_or_child_session_refuse_before_writes(database):
    dsn, tenant, conn = database

    async def handler(envelope):
        raise AssertionError("handler must not run")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        transactions = dispatcher._transactions
        async with transactions.transaction(tenant) as session:

            async def child():
                with pytest.raises(RuntimeError, match="another task"):
                    await session.claim("other")

            await asyncio.create_task(child())
            await session.conn.execute('SET LOCAL search_path TO "public"')
            with pytest.raises(ValueError, match="schema mismatch"):
                await session.claim("other")
        with pytest.raises(RuntimeError, match="inactive"):
            await session.claim("other")
        with pytest.raises(ValueError, match="tenant mismatch"):
            async with transactions.transaction("other"):
                raise AssertionError("wrong tenant admitted")
    assert await counts(conn) == (0, 0, 0, 0)


async def test_invalid_card_rejection_has_no_requested(database):
    dsn, tenant, conn = database

    async def handler(envelope):
        raise AssertionError("handler must not run")

    async with dispatcher_for(dsn, tenant, handler, cards=False) as (dispatcher, _):
        result = await dispatcher.delegate(envelope_for(tenant))
    assert not result.success
    assert await counts(conn) == (1, 1, 1, 1)
    assert await conn.fetchval("SELECT requested_enqueued_at FROM a2a_idempotency") is None
    assert await conn.fetchval("SELECT topic FROM a2a_fact_outbox") != TOPIC_REQUESTED


@pytest.mark.parametrize("kind", ["unsigned", "tampered", "expired"])
async def test_signature_rejection_precedes_any_claim_or_audit(database, kind):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from .test_envelope_signing import _signer, _verifier

    dsn, tenant, conn = database

    async def handler(envelope):
        raise AssertionError("unverified envelope reached handler")

    envelope = envelope_for(tenant)
    if kind == "tampered":
        envelope = replace(_signer(tenant=tenant).sign(envelope), task_id="retagged")
    elif kind == "expired":
        envelope = _signer(tenant=tenant).sign(envelope, now=datetime.now(UTC) - timedelta(days=30))
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        dispatcher._envelope_verifier = _verifier()
        assert not (await dispatcher.delegate(envelope)).success
    assert await counts(conn) == (0, 0, 0, 0)


async def test_admission_commit_ack_uncertainty_never_runs_handler_and_retry_reads_state(database):
    dsn, tenant, conn = database
    calls = 0

    async def handler(envelope):
        nonlocal calls
        calls += 1
        return HandlerOutput(output_ref="fhir://Task/result")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        original = dispatcher._transactions.transaction

        @asynccontextmanager
        async def lose_ack(tenant):
            async with original(tenant) as session:
                yield session
            # Transaction really committed; loss of the caller's acknowledgement
            # is injected at the transaction boundary, not inferred from flags.
            raise ConnectionError("synthetic lost commit acknowledgement")

        dispatcher._transactions.transaction = lose_ack
        with pytest.raises(ConnectionError, match="lost commit"):
            await dispatcher.delegate(envelope_for(tenant))
    assert calls == 0
    assert await counts(conn) == (1, 1, 1, 1)
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        assert (await dispatcher.delegate(envelope_for(tenant))).success
    assert calls == 1
    assert await counts(conn) == (1, 2, 2, 2)


async def test_deleted_admission_cannot_be_recreated_by_terminal_commit(database):
    dsn, tenant, conn = database

    async def handler(envelope):
        await conn.execute("DELETE FROM a2a_idempotency")
        return HandlerOutput(output_ref="fhir://Task/result")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        with pytest.raises(RuntimeError, match="admission missing"):
            await dispatcher.delegate(envelope_for(tenant))
    assert await counts(conn) == (0, 1, 1, 1)


async def test_same_task_id_in_two_tenants_has_independent_admission_and_results(database):
    dsn, tenant, conn = database
    other = "ay" + uuid.uuid4().hex[:16]
    await asyncio.to_thread(_apply_migrations, dsn, other)
    other_conn = await asyncpg.connect(dsn)
    await other_conn.execute(f'SET search_path TO "{other}"')

    async def handler(envelope):
        return HandlerOutput(output_ref="fhir://Task/" + envelope.tenant)

    try:
        async with (
            dispatcher_for(dsn, tenant, handler) as (a, _),
            dispatcher_for(dsn, other, handler) as (b, _),
        ):
            first, second = await asyncio.gather(
                a.delegate(envelope_for(tenant)), b.delegate(envelope_for(other))
            )
        assert first.output_ref != second.output_ref
        assert await counts(conn) == await counts(other_conn) == (1, 2, 2, 2)
        assert set(await conn.fetchval("SELECT array_agg(DISTINCT tenant) FROM a2a_fact_outbox")) == {tenant}
        assert set(await other_conn.fetchval("SELECT array_agg(DISTINCT tenant) FROM a2a_fact_outbox")) == {
            other
        }
    finally:
        await other_conn.execute(f'DROP SCHEMA "{other}" CASCADE')
        await other_conn.close()


async def _cross_process_worker(tenant):
    """Real replica for R9; its handler barrier lives in PostgreSQL, outside A2A TX."""
    dsn = _default_test_dsn()
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'SET search_path TO "{tenant}"')

    both = asyncio.Event()
    await conn.add_listener(tenant + "_ready", lambda *args: both.set())

    async def handler(envelope):
        await conn.execute("INSERT INTO handler_entries DEFAULT VALUES")
        if await conn.fetchval("SELECT count(*) FROM handler_entries") == 2:
            await conn.execute("SELECT pg_notify($1, 'ready')", tenant + "_ready")
        else:
            await asyncio.wait_for(both.wait(), 20)
        assert await conn.fetchval("SELECT count(*) FROM handler_entries") == 2
        return HandlerOutput(output_ref="fhir://Task/result")

    try:
        async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
            assert (await dispatcher.delegate(envelope_for(tenant))).success
    finally:
        await conn.close()


async def test_two_real_processes_share_one_requested_intent(database):
    import sys

    dsn, tenant, conn = database
    await conn.execute("CREATE TABLE handler_entries (id bigserial PRIMARY KEY)")
    children = []
    try:
        for _ in range(2):
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import asyncio,sys; from tests.unit.a2a.test_atomic_admission_live_pg "
                "import _cross_process_worker; asyncio.run(_cross_process_worker(sys.argv[1]))",
                tenant,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            children.append(child)
        outputs = await asyncio.wait_for(asyncio.gather(*(child.communicate() for child in children)), 30)
        assert [child.returncode for child in children] == [0, 0], outputs
        assert await conn.fetchval("SELECT count(*) FROM handler_entries") == 2
        assert await counts(conn) == (1, 2, 2, 2)
        assert (
            await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox WHERE topic=$1", TOPIC_REQUESTED) == 1
        )
        assert (await verify_chain(dsn, tenant)).valid
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


def _upgrade_to(dsn, tenant, revision):
    """Run real Alembic at an explicit revision; no stamp or synthesized DDL."""
    import argparse
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "src/maezo/platform/migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant}"])
    command.upgrade(cfg, revision)


async def _durable_snapshot(conn, *, marker=False):
    result = {}
    for table in ("a2a_idempotency", "audit_chain", "audit_emit_dedup", "a2a_fact_outbox"):
        rows = [dict(row) for row in await conn.fetch(f'SELECT * FROM "{table}"')]
        for row in rows:
            if marker and table == "a2a_idempotency":
                assert row.pop("requested_enqueued_at") is None
        result[table] = sorted(rows, key=repr)
    return result


@pytest.mark.parametrize("has_requested", [False, True])
async def test_populated_0010_upgrade_preserves_all_rows_bytes_chain_and_valid_replay(has_requested):
    import json
    from dataclasses import replace

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from maezo.a2a.facts import DelegationFactKind, build_fact
    from maezo.gateway.audit import AuditRecord

    from .test_envelope_signing import _signer, _verifier

    dsn, tenant = _default_test_dsn(), "az" + uuid.uuid4().hex[:16]
    await asyncio.to_thread(_upgrade_to, dsn, tenant, "0010")
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'SET search_path TO "{tenant}"')
    audit, outbox = PostgresAuditSink(dsn, tenant), PostgresFactOutbox(dsn=dsn, tenant=tenant)
    envelopes = [replace(envelope_for(tenant), task_id=f"legacy-{state}") for state in ("processing", "done")]
    original_bytes = {}
    calls = []

    async def handler(envelope):
        calls.append(envelope.task_id)
        return HandlerOutput(output_ref="fhir://Task/new-result")

    try:
        assert await conn.fetchval(f'SELECT version_num FROM "{tenant}_alembic_version"') == "0010"
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=$1 "
            "AND table_name='a2a_idempotency' AND column_name='requested_enqueued_at')",
            tenant,
        )
        for envelope in envelopes:
            await conn.fetchval(CLAIM_SQL, envelope.task_id, tenant, "unknown", "unknown", "unknown")
            await audit.emit_once(
                AuditRecord(
                    agent_id="rafael",
                    tenant_id=tenant,
                    agent_version="1.0.0",
                    action="migration-sentinel",
                    decision="ALLOW",
                    details={"task": envelope.task_id},
                ),
                dedup_key="sentinel:" + envelope.task_id,
            )
            # At least one exact BYTEA row exists even when requested is missing.
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
                value = fact.to_value()
                await outbox.enqueue(fact.topic, value, key=tenant.encode())
                original_bytes[(envelope.task_id, kind.value)] = value
        await conn.execute(
            "UPDATE a2a_idempotency SET status='done', result=$1::jsonb, completed_at=now() "
            "WHERE task_id='legacy-done'",
            json.dumps(
                dict(
                    success=True,
                    output_ref="fhir://Task/old-result",
                    rejection_reason=None,
                    detail="preserve old result",
                    meta={"sentinel": [1, "é"]},
                )
            ),
        )
        before = await _durable_snapshot(conn)
        chain_before = await verify_chain(dsn, tenant)
        assert chain_before.valid and chain_before.total_records == 2
        await asyncio.to_thread(_upgrade_to, dsn, tenant, "0011")
        assert await conn.fetchval(f'SELECT version_num FROM "{tenant}_alembic_version"') == "0011"
        assert await _durable_snapshot(conn, marker=True) == before
        assert (await verify_chain(dsn, tenant)).valid
        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        assert script.get_revision("0011").down_revision == "0010"
        assert len(script.get_heads()) == 1
        assert "0011" in {revision.revision for revision in script.walk_revisions()}
        async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
            dispatcher._envelope_verifier = _verifier()
            for envelope in envelopes:
                result = await dispatcher.delegate(_signer(tenant=tenant).sign(envelope))
                assert result.success
                if envelope.task_id == "legacy-done":
                    assert result.idempotent_replay and result.output_ref == "fhir://Task/old-result"
        assert calls == ["legacy-processing"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM a2a_idempotency WHERE requested_enqueued_at IS NOT NULL"
            )
            == 2
        )
        requested = await conn.fetch("SELECT payload FROM a2a_fact_outbox WHERE topic=$1", TOPIC_REQUESTED)
        assert {json.loads(bytes(row["payload"]))["task_id"] for row in requested} == {
            env.task_id for env in envelopes
        }
        assert len(requested) == 2
        for row in await conn.fetch("SELECT payload FROM a2a_fact_outbox"):
            payload = json.loads(bytes(row["payload"]))
            key = (payload["task_id"], payload["kind"])
            if key in original_bytes:
                assert bytes(row["payload"]) == original_bytes[key]
        assert (await verify_chain(dsn, tenant)).valid
    finally:
        await audit.aclose()
        await outbox.aclose()
        await conn.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        await conn.close()


async def _die_after_claim_worker(tenant):
    import os

    from maezo.a2a.transaction import AtomicSession

    original = AtomicSession.claim

    async def die_after_actual_claim(self, task_id):
        fresh, row = await original(self, task_id)
        assert fresh and row["status"] == "processing"
        assert await self.conn.fetchval("SELECT count(*) FROM a2a_idempotency") == 1
        assert await self.conn.fetchval("SELECT count(*) FROM a2a_fact_outbox") == 0
        os._exit(73)

    AtomicSession.claim = die_after_actual_claim

    async def handler(envelope):
        raise AssertionError("pre-enqueue crash reached handler")

    async with dispatcher_for(_default_test_dsn(), tenant, handler) as (dispatcher, _):
        await dispatcher.delegate(envelope_for(tenant))
    raise AssertionError("crash boundary not reached")


async def test_process_death_after_actual_claim_before_enqueue_rolls_back_then_replays(database):
    import sys

    dsn, tenant, conn = database
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import asyncio,sys; from tests.unit.a2a.test_atomic_admission_live_pg "
        "import _die_after_claim_worker; asyncio.run(_die_after_claim_worker(sys.argv[1]))",
        tenant,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output = await asyncio.wait_for(child.communicate(), 30)
        assert child.returncode == 73, output
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert await counts(conn) == (0, 0, 0, 0)

    async def handler(envelope):
        assert await counts(conn) == (1, 1, 1, 1)
        return HandlerOutput(output_ref="fhir://Task/recovered")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        assert (await dispatcher.delegate(envelope_for(tenant))).success
        assert (await dispatcher.delegate(envelope_for(tenant))).idempotent_replay
    assert await counts(conn) == (1, 2, 2, 2)
    assert (await verify_chain(dsn, tenant)).valid


@pytest.mark.parametrize("fault", ["lost_ack", "cancel_before_commit"])
async def test_finalization_commit_uncertainty_or_cancellation_has_atomic_readback(database, fault):
    dsn, tenant, conn = database
    calls = 0

    async def handler(envelope):
        nonlocal calls
        calls += 1
        return HandlerOutput(output_ref="fhir://Task/terminal")

    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        original = dispatcher._transactions.transaction
        entries = 0

        @asynccontextmanager
        async def terminal_fault(tenant):
            nonlocal entries
            entries += 1
            async with original(tenant) as session:
                yield session
                if entries == 2 and fault == "cancel_before_commit":
                    raise asyncio.CancelledError("synthetic terminal cancellation")
            if entries == 2 and fault == "lost_ack":
                raise ConnectionError("synthetic terminal lost commit ack")

        dispatcher._transactions.transaction = terminal_fault
        expected = ConnectionError if fault == "lost_ack" else asyncio.CancelledError
        with pytest.raises(expected, match="synthetic terminal"):
            await dispatcher.delegate(envelope_for(tenant))
    committed = fault == "lost_ack"
    assert await counts(conn) == ((1, 2, 2, 2) if committed else (1, 1, 1, 1))
    assert await conn.fetchval("SELECT status FROM a2a_idempotency") == (
        "done" if committed else "processing"
    )
    async with dispatcher_for(dsn, tenant, handler) as (dispatcher, _):
        result = await dispatcher.delegate(envelope_for(tenant))
        assert result.success and result.idempotent_replay is committed
    assert calls == (1 if committed else 2)
    assert await counts(conn) == (1, 2, 2, 2)
    assert (await verify_chain(dsn, tenant)).valid
