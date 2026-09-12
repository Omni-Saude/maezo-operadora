"""PLAN-W3-A2A R5-R7/R13: process crashes, real PostgreSQL and real Kafka.

Uses the production outbox/relay and ACK adapter. The handler is an explicit
non-engine test action. These tests make no claims about remote effect replay.
Unreachable Postgres or Kafka SKIPS loudly (root-cause R3-Q3, release-capability-floor §5:
this suite was written on the train, where CI always had the compose stack up, so it never
gained the loud-skip guard every other `*_live_pg.py`/`*_live_broker.py` suite under
`tests/unit/**` carries — see `test_outbox_live_pg.py` and
`tests/integration/platform/test_events_kafka_producer_live.py::_kafka_reachable`). The
imported `database` fixture (from `.test_atomic_admission_live_pg`) now skips loudly on its
own for tests collected in ITS home module, but a fixture imported across modules does not
inherit the other module's `autouse` guard, so this file probes both services itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys
import time
import uuid

import asyncpg
import pytest
from aiokafka import AIOKafkaConsumer

from maezo.a2a.outbox import PostgresFactOutbox
from maezo.a2a.outbox_relay import AioKafkaFactPublisher, drain_once
from maezo.gateway.audit_postgres import normalize_dsn

from .test_atomic_admission_live_pg import database as database
from .test_atomic_admission_live_pg import dispatcher_for, envelope_for
from .test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration


def broker_address():
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


def _kafka_reachable(bootstrap_servers: str) -> bool:
    async def _probe() -> bool:
        from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        try:
            await asyncio.wait_for(producer.start(), timeout=2.0)
        except Exception:  # any failure means "skip loudly", never an error here
            with contextlib.suppress(Exception):
                await producer.stop()
            return False
        with contextlib.suppress(Exception):
            await producer.stop()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:  # defensive: a probe-internal crash is still "unreachable"
        return False


@pytest.fixture(autouse=True)
def _skip_if_dependencies_unreachable() -> None:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — atomic recovery live tests SKIPPED (visible, not silent). "
            "Start it with `docker compose --profile core up -d postgres` and re-run this file."
        )
    servers = broker_address()
    if not _kafka_reachable(servers):
        pytest.skip(
            f"Kafka not reachable at {servers!r} (override with KAFKA_BOOTSTRAP_SERVERS) — "
            "atomic recovery live tests SKIPPED (visible, not silent). Start it with "
            "`docker compose --profile core up -d` and re-run this file."
        )


async def crash_worker(mode, tenant):
    """Only called in child processes; exit cuts AFTER the observed boundary."""
    dsn = _default_test_dsn()
    if mode == "admitted":

        async def crash_before_handler_effect(envelope):
            os._exit(71)

        async with dispatcher_for(dsn, tenant, crash_before_handler_effect) as (dispatcher, _):
            await dispatcher.delegate(envelope_for(tenant))
    elif mode == "acked":
        outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
        publisher = AioKafkaFactPublisher(bootstrap_servers=broker_address())
        await publisher.start()

        async def die_before_mark(*args, **kwargs):
            os._exit(72)

        outbox.mark_delivered = die_before_mark
        await drain_once(outbox, publisher, claimed_by="crashed", claim_ttl_s=0.1)
    raise AssertionError("crash boundary was not reached")


async def spawn_crash(mode, tenant):
    # Credentials remain environment-only, never command-line arguments.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import asyncio,sys; from tests.unit.a2a.test_atomic_recovery_live_broker import crash_worker; "
        "asyncio.run(crash_worker(sys.argv[1], sys.argv[2]))",
        mode,
        tenant,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == {"admitted": 71, "acked": 72}[mode], (
        process.returncode,
        stdout.decode(),
        stderr.decode(),
    )


async def receive(consumer, tenant, count):
    records = []
    deadline = time.monotonic() + 20
    while len(records) < count and time.monotonic() < deadline:
        batches = await consumer.getmany(timeout_ms=500)
        for batch in batches.values():
            records.extend(record for record in batch if json.loads(record.value)["tenant"] == tenant)
    assert len(records) == count
    return records


@pytest.fixture
async def consumer():
    from maezo.a2a.facts import TOPIC_COMPLETED, TOPIC_REJECTED, TOPIC_REQUESTED

    value = AIOKafkaConsumer(
        TOPIC_REQUESTED,
        TOPIC_COMPLETED,
        TOPIC_REJECTED,
        bootstrap_servers=broker_address(),
        group_id="atomic-" + uuid.uuid4().hex,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await value.start()
    try:
        yield value
    finally:
        await value.stop()


async def test_process_crash_after_admission_publishes_without_caller_replay(database, consumer):
    dsn, tenant, conn = database
    await spawn_crash("admitted", tenant)
    assert await conn.fetchval("SELECT status FROM a2a_idempotency") == "processing"
    assert await conn.fetchval("SELECT requested_enqueued_at FROM a2a_idempotency") is not None
    assert await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox") == 1
    original = bytes(await conn.fetchval("SELECT payload FROM a2a_fact_outbox"))
    outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
    publisher = AioKafkaFactPublisher(bootstrap_servers=broker_address())
    await publisher.start()
    try:
        report = await drain_once(outbox, publisher, claimed_by="replacement")
        assert (report.delivered, report.sealed) == (1, 1)
        messages = await receive(consumer, tenant, 1)
        assert messages[0].value == original
        assert messages[0].key == tenant.encode()
        assert await conn.fetchval("SELECT status FROM a2a_idempotency") == "processing"
        assert await conn.fetchval("SELECT count(*) FROM audit_chain WHERE decision='COMPLETED'") == 0
    finally:
        await publisher.stop()
        await outbox.aclose()


async def test_real_ack_then_process_death_redelivers_same_bytes_and_one_consumer_effect(database, consumer):
    dsn, tenant, conn = database
    await spawn_crash("admitted", tenant)
    await spawn_crash("acked", tenant)
    original = await conn.fetchrow("SELECT * FROM a2a_fact_outbox")
    assert original["status"] == "claimed"
    assert original["delivered_at"] is None
    first = await receive(consumer, tenant, 1)
    await asyncio.sleep(0.2)
    outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
    publisher = AioKafkaFactPublisher(bootstrap_servers=broker_address())
    await publisher.start()
    try:
        report = await drain_once(outbox, publisher, claimed_by="new-owner", claim_ttl_s=0.1)
        assert report.sealed == 1
        second = await receive(consumer, tenant, 1)
        assert first[0].value == second[0].value == bytes(original["payload"])
        assert first[0].key == second[0].key == tenant.encode()
        # Independent consumer dedup model: a unique logical effect for both
        # actual broker deliveries. No claim about arbitrary consumer software.
        await conn.execute("CREATE TABLE consumed (logical_key text PRIMARY KEY)")
        for message in first + second:
            payload = json.loads(message.value)
            logical_key = f"{payload['tenant']}:a2a:delegate:{payload['task_id']}:{payload['kind']}"
            assert logical_key == original["dedup_key"]
            await conn.execute("INSERT INTO consumed VALUES ($1) ON CONFLICT DO NOTHING", logical_key)
        assert await conn.fetchval("SELECT count(*) FROM consumed") == 1
        assert await conn.fetchval("SELECT attempts FROM a2a_fact_outbox") == 2
        assert await outbox.mark_delivered([original["id"]], claimed_by="crashed") == 0
    finally:
        await publisher.stop()
        await outbox.aclose()


async def test_real_connection_outage_keeps_pending_intent_then_broker_recovery(database, consumer):
    dsn, tenant, conn = database
    await spawn_crash("admitted", tenant)
    outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
    # Reserved but non-listening local socket guarantees a real connection refusal.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        bad = AioKafkaFactPublisher(
            bootstrap_servers=f"127.0.0.1:{unavailable.getsockname()[1]}",
            connect_timeout_s=1,
            send_timeout_s=1,
        )

        class ConnectBeforeSending:
            async def send(self, *args, **kwargs):
                await bad.start()
                await bad.send(*args, **kwargs)

        report = await drain_once(outbox, ConnectBeforeSending(), claimed_by="offline")
        assert (report.delivered, report.sealed, report.released) == (0, 0, 1)
        assert report.publish_error == "broker_publish_failed"
        row = await conn.fetchrow("SELECT status, attempts, last_error FROM a2a_fact_outbox")
        assert tuple(row) == ("pending", 1, "broker_publish_failed")
        await bad.stop()
    good = AioKafkaFactPublisher(bootstrap_servers=broker_address())
    await good.start()
    try:
        report = await drain_once(outbox, good, claimed_by="online")
        assert report.sealed == 1
        assert len(await receive(consumer, tenant, 1)) == 1
        assert await conn.fetchval("SELECT attempts FROM a2a_fact_outbox") == 2
    finally:
        await good.stop()
        await outbox.aclose()


async def _relay_schedule_worker(role, tenant):
    """Two actual relay processes; only the fault/barrier schedule is controlled."""
    outbox = PostgresFactOutbox(dsn=_default_test_dsn(), tenant=tenant)
    publisher = AioKafkaFactPublisher(bootstrap_servers=broker_address())
    await publisher.start()

    def signal(event, **fields):
        print("SCHEDULE:" + json.dumps(dict(event=event, pid=os.getpid(), **fields)), flush=True)

    async def command(expected):
        received = await asyncio.to_thread(sys.stdin.readline)
        assert received.strip() == expected, received

    try:
        if role == "a":

            class PrefixFailure:
                sends = 0

                async def send(self, *args, **kwargs):
                    self.sends += 1
                    if self.sends == 2:
                        signal("prefix-acked", first_task=self.first_task)
                        await command("fail")
                        raise ConnectionError("synthetic second-send failure")
                    await publisher.send(*args, **kwargs)
                    self.first_task = json.loads(args[1])["task_id"]

            first = await drain_once(
                outbox, PrefixFailure(), claimed_by="relay-a", batch_size=3, claim_ttl_s=60
            )
            signal(
                "prefix-failed",
                report=[first.claimed, first.delivered, first.sealed, first.released],
                error=first.publish_error,
            )
            await command("claim-remainder")
            original_mark = outbox.mark_delivered

            async def pause_before_mark(ids, *, claimed_by):
                signal("remainder-acked", ids=list(ids))
                await command("mark-stale")
                return await original_mark(ids, claimed_by=claimed_by)

            outbox.mark_delivered = pause_before_mark
            second = await drain_once(outbox, publisher, claimed_by="relay-a", batch_size=2, claim_ttl_s=0.2)
            signal("stale-refused", delivered=second.delivered, sealed=second.sealed)
            await command("exit")
        else:
            first = await drain_once(outbox, publisher, claimed_by="relay-b", batch_size=2, claim_ttl_s=60)
            signal("later-delivered", claimed=first.claimed, sealed=first.sealed)
            await command("takeover")
            second = await drain_once(outbox, publisher, claimed_by="relay-b", batch_size=2, claim_ttl_s=60)
            signal("taken-over", claimed=second.claimed, sealed=second.sealed)
            await command("exit")
    finally:
        await publisher.stop()
        await outbox.aclose()


async def test_two_live_relays_multirow_prefix_failure_out_of_order_takeover(database, consumer):
    from maezo.a2a.facts import DelegationFactKind, build_fact

    dsn, tenant, conn = database
    outbox = PostgresFactOutbox(dsn=dsn, tenant=tenant)
    children = []
    transcript = []

    async def spawn(role):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import asyncio,sys; from tests.unit.a2a.test_atomic_recovery_live_broker "
            "import _relay_schedule_worker; asyncio.run(_relay_schedule_worker(sys.argv[1], sys.argv[2]))",
            role,
            tenant,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        children.append(child)
        return child

    async def event(child, expected):
        async def read():
            while True:
                line = await child.stdout.readline()
                assert line, (expected, transcript, await child.stderr.read())
                transcript.append(line.decode())
                if line.startswith(b"SCHEDULE:"):
                    value = json.loads(line[len(b"SCHEDULE:") :])
                    assert value["event"] == expected, value
                    return value

        return await asyncio.wait_for(read(), 30)

    async def send(child, text):
        child.stdin.write((text + "\n").encode())
        await child.stdin.drain()

    try:
        for index in range(5):
            envelope = envelope_for(tenant)
            fact = build_fact(
                DelegationFactKind.REQUESTED,
                task_id=f"schedule-{index}",
                tenant=tenant,
                task_type=envelope.task_type,
                origin=envelope.origin,
                target=envelope.target,
                delegation_chain=envelope.delegation_chain,
            )
            await outbox.enqueue(fact.topic, fact.to_value(), key=tenant.encode())
        original = [
            dict(row) for row in await conn.fetch("SELECT * FROM a2a_fact_outbox ORDER BY created_at,id")
        ]
        a = await spawn("a")
        prefix = await event(a, "prefix-acked")
        claimed_a = await conn.fetch("SELECT id FROM a2a_fact_outbox WHERE claimed_by='relay-a' ORDER BY id")
        assert {row["id"] for row in claimed_a} == {row["id"] for row in original[:3]}
        b = await spawn("b")
        later = await event(b, "later-delivered")
        assert prefix["pid"] != later["pid"]
        assert a.returncode is None and b.returncode is None
        assert (later["claimed"], later["sealed"]) == (2, 2)
        # Later rows are durably sealed while earlier claimed rows remain unsealed.
        sealed_ids = await conn.fetch("SELECT id FROM a2a_fact_outbox WHERE status='delivered'")
        assert {row["id"] for row in sealed_ids} == {row["id"] for row in original[3:]}
        await send(a, "fail")
        failed = await event(a, "prefix-failed")
        assert failed["report"] == [3, 1, 1, 2]
        assert failed["error"] == "broker_publish_failed"
        assert await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox WHERE status='pending'") == 2
        await send(a, "claim-remainder")
        acked = await event(a, "remainder-acked")
        remainder_ids = {
            row["id"]
            for row in original[:3]
            if json.loads(bytes(row["payload"]))["task_id"] != prefix["first_task"]
        }
        assert set(acked["ids"]) == remainder_ids

        # Natural expiry observed in PostgreSQL, no forced lease rewrite.
        async def expired():
            while (  # noqa: ASYNC110 -- expiry is a database clock predicate, not an event
                await conn.fetchval(
                    "SELECT count(*) FROM a2a_fact_outbox WHERE claimed_by='relay-a' "
                    "AND status='claimed' AND claim_expires_at<=now()"
                )
                != 2
            ):
                await asyncio.sleep(0.02)

        await asyncio.wait_for(expired(), 5)
        await send(b, "takeover")
        takeover = await event(b, "taken-over")
        assert (takeover["claimed"], takeover["sealed"]) == (2, 2)
        assert a.returncode is None and b.returncode is None
        await send(a, "mark-stale")
        stale = await event(a, "stale-refused")
        assert (stale["delivered"], stale["sealed"]) == (2, 0)
        rows = await conn.fetch("SELECT * FROM a2a_fact_outbox ORDER BY created_at,id")
        assert len(rows) == 5 and all(row["status"] == "delivered" for row in rows)
        expected = {row["dedup_key"]: bytes(row["payload"]) for row in original}
        assert {row["dedup_key"]: bytes(row["payload"]) for row in rows} == expected
        assert {row["id"]: row["attempts"] for row in rows} == {
            row["id"]: (3 if row["id"] in remainder_ids else 1) for row in original
        }
        messages = await receive(consumer, tenant, 7)
        observed = {}
        for message in messages:
            payload = json.loads(message.value)
            key = f"{tenant}:a2a:delegate:{payload['task_id']}:{payload['kind']}"
            assert message.value == expected[key] and message.key == tenant.encode()
            observed[key] = observed.get(key, 0) + 1
        assert set(observed) == set(expected)
        assert sorted(observed.values()) == [1, 1, 1, 2, 2]
        # This asserts this controlled schedule's deliveries, never global FIFO or
        # exactly-once transport/effects for deployed consumers.
        for child in children:
            await send(child, "exit")
        outputs = await asyncio.wait_for(asyncio.gather(*(child.communicate() for child in children)), 20)
        assert [child.returncode for child in children] == [0, 0], outputs
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()
        await outbox.aclose()
