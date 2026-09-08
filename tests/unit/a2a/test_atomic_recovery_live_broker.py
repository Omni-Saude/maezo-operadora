"""PLAN-W3-A2A R5-R7/R13: process crashes, real PostgreSQL and real Kafka.

Uses the production outbox/relay and ACK adapter. The handler is an explicit
non-engine test action. These tests make no claims about remote effect replay.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import uuid

import pytest
from aiokafka import AIOKafkaConsumer

from maezo.a2a.outbox import PostgresFactOutbox
from maezo.a2a.outbox_relay import AioKafkaFactPublisher, drain_once

from .test_atomic_admission_live_pg import database as database
from .test_atomic_admission_live_pg import dispatcher_for, envelope_for
from .test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration


def broker_address():
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


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
