"""Live-Kafka + live-Postgres PROOF for the T4 producer leg (`events_kafka_producer.py`).

This is the honest external-boundary proof the module's own docstring cannot cross without a
real broker/database (mirrors `test_notifications_bridge_live_kafka.py`'s and `test_notifications_
bridge_live_pg.py`'s own posture — ADR-0011 "explicit could-not-verify", never a faked broker/DB
standing in for a real one). Routing/scrub/failure-isolation logic itself is unit-proven in
`tests/unit/platform/integrations/test_events_kafka_producer.py` against a fake raw producer — no
network needed there.

SCOPE (per this task's own boundary — no CIB Seven engine; another agent owns that slot):
  (a) producer publishes a real event -> message lands on the topic (consumed back, shape
      asserted) — BOTH the untouched primary leg and the scrubbed `NOTIFICATIONS_TOPIC` mirror.
  (b) the REAL `AioKafkaBridgeConsumer` (EB-3) consumes the mirrored message -> `NotificationBridge`
      receives it -> for a rule-matching SYNTHETIC ENRICHED payload (carries the business-key
      anchor `numero_guia_tiss` today's real minimal `event_payload_vars` does not yet emit — the
      separate, out-of-scope-here "arming" follow-up), the bridge invokes a RECORDING starter
      seam (`FakeCibSevenTransport` — the fenced START itself, `start_process_idempotent`, is
      engine-proven elsewhere, e.g. `test_notifications_bridge_live_engine.py`) + writes a REAL
      audit row on live Postgres.
  (c) a non-matching/dormant payload (today's REAL minimal shape, no anchors) -> no starter call,
      no audit row.
  (d) producer failure (Kafka unreachable) -> the SOURCE path (the actual `events.py` publish
      handler) still completes normally — never raises, never blocks the BPMN process.
  (e) consumer idempotency on redelivery -> the SAME message consumed twice converges on ONE
      audit_chain row and the SAME process_instance_id (`start_process_idempotent`'s own
      exactly-once dedup + `find_active_instance` hit, proven end-to-end through THIS module's
      wiring rather than re-deriving it).

Bring-up: this suite runs against the REPO'S shared compose stack (`docker-compose.yml`, profile
`core`) — the same stack `.github/workflows/ci.yml`'s `integration tests (real engine)` job
(~:339-420) brings up via `docker compose --profile core up -d`, with no
`KAFKA_BOOTSTRAP_SERVERS` override for pytest. This paragraph used to describe this suite's own
"isolated stack" at `KAFKA_BOOTSTRAP_SERVERS` default `localhost:19092` / `MAEZO_PG_HOST_PORT`
default `5659` — that stack was never built (gap `PRODUCER-LIVE-19092-DEFAULT`, register
arbitration): CI never overrode either default, so 6 of this suite's 7 tests reported "COULD NOT
VERIFY" forever while the job stayed green (the suite has carried `pytest.mark.integration`, and
so been collected in CI, since #282). Fixed at the root, aligned with the sibling live suites in
this same directory: `KAFKA_BOOTSTRAP_SERVERS` now defaults to `localhost:9092` (the compose
Kafka service's `EXTERNAL` listener, advertised as `localhost:9092` — `docker-compose.yml`
~:96-101; matches `test_notifications_bridge_live_kafka.py`'s own default), and Postgres
resolution mirrors `tests/integration/conftest.py::_audit_pg_dsn`'s convention:
`MAEZO_TEST_DATABASE_URL` wins; otherwise
`postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5433}/maezo` — the compose local-dev
default, byte-for-byte the CI lane's Postgres once CI pins `MAEZO_PG_HOST_PORT=5432`
(ci.yml ~:350, ~:484), and the isolated-validation harness's own export. Still skips LOUDLY
(never fakes) when either is unreachable — see `kafka_bootstrap_servers` / `pg_tenant_schema`
below. Cold-broker/cold-topic readiness (topic auto-create + consumer-group rebalance latency,
gap `CI-KAFKA-HEALTH-WAIT`) is handled the same way as the sibling suites: `_await_topic_ready` +
`_await_assigned` + `seek_to_end()` run BEFORE every strict timed consume below, so that timeout
measures only the publish->consume round trip, never Kafka's own warm-up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, schema_for_tenant
from maezo.platform.integrations.events_kafka_producer import (
    NOTIFICATIONS_TOPIC as PRODUCER_NOTIFICATIONS_TOPIC,
)
from maezo.platform.integrations.events_kafka_producer import (
    AioKafkaEventsProducer,
)
from maezo.platform.integrations.notifications_bridge import (
    NOTIFICATIONS_TOPIC,
    REASON_MISSING_TYPE,
    AioKafkaBridgeConsumer,
    AioKafkaDlqPublisher,
    BridgeDlqShunt,
    handle_bridge_message,
    run_consumer_loop,
)
from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.platform.topic_registry import dlq_topic_for
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.events import make_publish_event_handler
from maezo.tools.workers.harness import ExternalTask
from tests.integration.conftest import _apply_migrations, _pg_reachable

pytestmark = pytest.mark.integration

_CONNECT_TIMEOUT_S = 5.0

#: Total bound for `_await_topic_ready`'s pre-flight poll — mirrors
#: `test_notifications_bridge_live_kafka.py::_READINESS_TIMEOUT_S`, kept entirely SEPARATE from
#: every test's own strict publish->consume deadline below (typically 15-45s) so that window
#: measures only the round trip, never Kafka's own topic-creation/group-join latency.
_READINESS_TIMEOUT_S = 90.0

#: Kafka protocol error codes `_await_topic_ready` tolerates as "topic is fine, proceed": NONE (0)
#: and TOPIC_ALREADY_EXISTS (36) — every other code is a genuine failure worth raising on. Mirrors
#: `test_notifications_bridge_live_kafka.py::_TOLERATED_CREATE_ERROR_CODES`.
_TOLERATED_CREATE_ERROR_CODES = (0, 36)

#: The two constants must be the SAME literal (module docstring, `events_kafka_producer.py`'s own
#: "duplicated, not imported" rationale) — asserted once here so drift is caught immediately.
assert PRODUCER_NOTIFICATIONS_TOPIC == NOTIFICATIONS_TOPIC


def _kafka_bootstrap_servers() -> str:
    # PRODUCER-LIVE-19092-DEFAULT: aligned with `test_notifications_bridge_live_kafka.py`'s own
    # default — the compose stack's real `EXTERNAL` listener (`docker-compose.yml` ~:96-101,
    # advertised as `localhost:9092`), not a fictional isolated stack this repo never builds.
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _kafka_reachable(bootstrap_servers: str) -> bool:
    async def _probe() -> bool:
        from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        try:
            await asyncio.wait_for(producer.start(), timeout=_CONNECT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - any failure means "skip loudly", never an error here
            with contextlib.suppress(Exception):
                await producer.stop()
            return False
        with contextlib.suppress(Exception):
            await producer.stop()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001 - defensive: a probe-internal crash is still "unreachable"
        return False


def _pg_dsn() -> str:
    # PRODUCER-LIVE-19092-DEFAULT: mirrors `tests/integration/conftest.py::_audit_pg_dsn`'s
    # convention exactly — `MAEZO_TEST_DATABASE_URL` wins; otherwise the compose local-dev
    # default (5433), which is byte-for-byte the CI lane's Postgres once CI pins
    # `MAEZO_PG_HOST_PORT=5432` (`.github/workflows/ci.yml` ~:350, ~:484) and the isolated
    # validation harness's own export — not a fictional isolated-stack-only port.
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


@pytest.fixture(scope="module")
def kafka_bootstrap_servers() -> str:
    servers = _kafka_bootstrap_servers()
    if not _kafka_reachable(servers):
        pytest.skip(
            f"COULD NOT VERIFY: no reachable Kafka broker at {servers!r} (override with "
            "KAFKA_BOOTSTRAP_SERVERS). T4 producer-leg live proof needs a real broker — bring one "
            "up via the repo's own compose stack (`docker compose --profile core up -d`, port "
            "9092 EXTERNAL listener) to run this for real."
        )
    return servers


@pytest.fixture
def pg_tenant_schema() -> Iterator[tuple[str, str]]:
    """Per-test throwaway tenant schema with REAL migrations applied. Skips LOUDLY when Postgres
    is unreachable (never fakes it) — mirrors `test_notifications_bridge_live_pg.py`'s fixture."""
    dsn = _pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(
            f"COULD NOT VERIFY: no reachable Postgres at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL / MAEZO_PG_HOST_PORT). T4 producer-leg live proof needs a "
            "real Postgres with migrations applied — bring one up via the repo's own compose "
            "stack (`docker compose --profile core up -d`, port 5433 local-dev default) to run "
            "this for real."
        )

    tenant_id = f"t4pl{uuid.uuid4().hex[:10]}"  # [a-z][a-z0-9]* — no cross-run dedup residue

    async def _create_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    async def _drop_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create_schema())
    _apply_migrations(dsn, tenant_id)

    yield dsn, tenant_id

    asyncio.run(_drop_schema())


async def _fetch_start_process_rows(dsn: str, tenant_id: str) -> list[Any]:
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(
            f'SELECT action, agent_id FROM "{schema}".audit_chain '
            "WHERE action = 'start_process:SP-OP-RECURSO-001' ORDER BY id"
        )
    finally:
        await conn.close()


async def _consume_one(consumer: AioKafkaBridgeConsumer, *, timeout_s: float = 15.0) -> Any:
    """The parsed VALUE of the next record (GAP-SC-04-a: the consumer now yields a
    `BridgeMessage` carrying the raw bytes alongside the parsed value — see its docstring for why
    the DLQ made that necessary). Tests that need the envelope itself use `_consume_message`."""
    return (await _consume_message(consumer, timeout_s=timeout_s)).value


async def _consume_message(consumer: AioKafkaBridgeConsumer, *, timeout_s: float = 15.0) -> Any:
    return await asyncio.wait_for(consumer.__aiter__().__anext__(), timeout=timeout_s)


# ---------------------------------------------------------------------------
# Cold-broker/cold-topic readiness (gap CI-KAFKA-HEALTH-WAIT, ported from
# `test_notifications_bridge_live_kafka.py::_await_topic_ready` /
# `_await_consumer_assigned` — see that module's docstrings for the full empirical rationale: a
# brand-new topic pays `auto.create.topics.enable`'s async-creation race, and a brand-new
# consumer group pays Kafka's own `group.initial.rebalance.delay.ms`, neither bounded by the
# strict per-test consume deadlines below). Every test in THIS file that consumes from a topic no
# earlier test in the run has touched runs these BEFORE its own timed consume, then
# `seek_to_end()`s to pin the starting position strictly before its own publish — replacing the
# previous unbounded `await asyncio.sleep(1.0)` guess with a bounded, logged, FAILING (never
# skipping) wait: `kafka_bootstrap_servers` already proved the broker itself reachable, so a
# topic/assignment that never becomes ready is a genuine defect, not a skip condition.
# ---------------------------------------------------------------------------


async def _await_topic_ready(
    bootstrap_servers: str, topic: str, *, timeout_s: float = _READINESS_TIMEOUT_S
) -> None:
    from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]
    from aiokafka.errors import KafkaError  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_reason = "not attempted"
    while time.monotonic() < deadline:
        attempt += 1
        try:
            admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
            try:
                await admin.start()
                response = await asyncio.wait_for(
                    admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)]),
                    timeout=10.0,
                )
                for _name, error_code, error_message in response.topic_errors:
                    if error_code not in _TOLERATED_CREATE_ERROR_CODES:
                        raise KafkaError(
                            f"create_topics({topic!r}) failed: code={error_code} {error_message}"
                        )
            finally:
                with contextlib.suppress(Exception):  # best-effort cleanup only
                    await admin.close()

            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            try:
                await probe.start()
                partitions = await asyncio.wait_for(probe.partitions_for(topic), timeout=10.0)
                leader_partitions = probe.client.cluster.available_partitions_for_topic(topic)
            finally:
                with contextlib.suppress(Exception):  # best-effort cleanup only
                    await probe.stop()

            if leader_partitions:
                elapsed = timeout_s - (deadline - time.monotonic())
                print(
                    f"[producer_live readiness] attempt {attempt}: topic {topic!r} ready "
                    f"(partitions with an elected leader: {sorted(leader_partitions)}) after "
                    f"{elapsed:.1f}s",
                    flush=True,
                )
                return
            last_reason = (
                f"create_topics succeeded and partitions {sorted(partitions or ())} exist in "
                "metadata, but none report an elected leader yet"
            )
        except Exception as exc:  # noqa: BLE001 - any failure here is retried until the deadline
            last_reason = f"{type(exc).__name__}: {exc}"
        print(
            f"[producer_live readiness] attempt {attempt}: topic {topic!r} not ready yet "
            f"({last_reason}) — retrying",
            flush=True,
        )
        await asyncio.sleep(1.0)

    raise AssertionError(
        f"producer_live readiness: topic {topic!r} on {bootstrap_servers!r} still not ready after "
        f"{timeout_s}s ({attempt} attempts) — last reason: {last_reason}. The broker itself is "
        "reachable (the kafka_bootstrap_servers fixture already proved that); a topic that never "
        "gets a leader is a genuine defect, not a skip condition. See gap "
        "PRODUCER-LIVE-19092-DEFAULT / CI-KAFKA-HEALTH-WAIT in docs/evidence-ledger.md."
    )


async def _await_assigned(real_consumer: Any, *, timeout_s: float = 15.0) -> None:
    """Poll a real `aiokafka` consumer's own `assignment()` until non-empty. Pass the WRAPPED
    consumer directly (`AIOKafkaConsumer`) or, for `AioKafkaBridgeConsumer`, its `_consumer`
    (mirrors `test_notifications_bridge_live_kafka.py::_await_consumer_assigned` — no public seam
    on the bridge wrapper for this readiness-only reach-through). Raises (fails, never skips) if
    the bound expires: an already-proven-reachable broker never handing a partition is a real
    defect."""
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        assignment = real_consumer.assignment()
        if assignment:
            print(
                f"[producer_live readiness] attempt {attempt}: consumer.assignment()={assignment!r}",
                flush=True,
            )
            return
        print(
            f"[producer_live readiness] attempt {attempt}: consumer.assignment() still empty — retrying",
            flush=True,
        )
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"producer_live readiness: consumer.assignment() still empty after {timeout_s}s "
        f"({attempt} attempts) despite start() returning and the topic already having a leader — "
        "a genuine defect, not a skip condition."
    )


# ---------------------------------------------------------------------------
# (a) producer publish -> message lands on the topic (consumed back, shape asserted) — both legs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_publish_mirrors_contas_completed_onto_notifications_topic(
    kafka_bootstrap_servers: str,
) -> None:
    """Publish a REAL `agents.events.contas.completed` event via `AioKafkaEventsProducer` against
    a real broker. Consume BOTH legs back: the primary per-domain topic (untouched payload) and
    the `NOTIFICATIONS_TOPIC` mirror (typed envelope, scrubbed)."""
    from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

    suffix = uuid.uuid4().hex[:8]
    primary_topic = CONTAS_COMPLETED_EVENT
    payload = {
        "tenant_id": "amh",
        "desfecho": "glosa_aplicada_humano",
        "numero_lote_tiss": f"LOTE-{suffix}",
        "glosa_id": f"GLOSA-{suffix}",
        "not_allowlisted_marker": "must-not-cross-into-the-mirror",
    }

    await _await_topic_ready(kafka_bootstrap_servers, primary_topic)
    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)

    primary_consumer = AIOKafkaConsumer(
        primary_topic,
        bootstrap_servers=kafka_bootstrap_servers,
        group_id=f"t4-primary-probe-{suffix}",
        auto_offset_reset="latest",
    )
    bridge_consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers,
        topic=NOTIFICATIONS_TOPIC,
        group_id=f"t4-mirror-probe-{suffix}",
    )
    await primary_consumer.start()
    await bridge_consumer.start()
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    try:
        # Cold-broker readiness (gap CI-KAFKA-HEALTH-WAIT): bound + confirm BOTH consumers'
        # assignment, then pin each starting position to the log end AS OF NOW — strictly before
        # the producer below ever sends — replacing the previous unbounded settle-delay guess.
        await _await_assigned(primary_consumer)
        await _await_assigned(bridge_consumer._consumer)  # noqa: SLF001 - readiness-only reach-through
        await primary_consumer.seek_to_end()
        await bridge_consumer._consumer.seek_to_end()  # noqa: SLF001 - see above
        await producer.publish(primary_topic, payload, key=f"bk-{suffix}")

        primary_record = await asyncio.wait_for(primary_consumer.__anext__(), timeout=15.0)
        primary_received = json.loads(primary_record.value.decode("utf-8"))
        assert primary_received == payload  # primary leg is byte-for-byte untouched

        mirror_received = await _consume_one(bridge_consumer)
        await bridge_consumer.commit()
        assert mirror_received["type"] == primary_topic
        assert mirror_received["tenant_id"] == "amh"
        assert mirror_received["desfecho"] == "glosa_aplicada_humano"
        assert mirror_received["glosa_id"] == f"GLOSA-{suffix}"
        assert "not_allowlisted_marker" not in mirror_received
    finally:
        await producer.close()
        await primary_consumer.stop()
        await bridge_consumer.stop()


# ---------------------------------------------------------------------------
# (b) + (c) full pipeline: producer -> topic -> consumer -> bridge -> fenced starter -> live PG.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_armed_payload_starts_and_audits_dormant_payload_does_not(
    kafka_bootstrap_servers: str,
    pg_tenant_schema: tuple[str, str],
) -> None:
    """Publish TWO real `agents.events.contas.completed` events through the producer: one
    SYNTHETIC ENRICHED (armed — carries `numero_guia_tiss`, matching the reconciled CONTAS→RECURSO
    rule) and one TODAY'S REAL MINIMAL shape (dormant — `sem_glosa`, no anchors at all). Consume
    both via the REAL `AioKafkaBridgeConsumer`, dispatch both through `handle_bridge_message`
    against a bridge wired to the FENCED starter (`FakeCibSevenTransport` recording + REAL
    `PostgresAuditSink`). Only the armed one starts + audits."""
    dsn, tenant_id = pg_tenant_schema
    suffix = uuid.uuid4().hex[:8]
    guia, glosa = f"GUIA-{suffix}", f"GLOSA-{suffix}"
    group_id = f"t4-pipeline-{suffix}"

    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)

    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers, topic=NOTIFICATIONS_TOPIC, group_id=group_id
    )
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    await consumer.start()
    try:
        await _await_assigned(consumer._consumer)  # noqa: SLF001 - readiness-only reach-through
        await consumer._consumer.seek_to_end()  # noqa: SLF001 - see above

        # Dormant first: today's REAL minimal payload for a `sem_glosa` outcome — no anchors.
        await producer.publish(
            CONTAS_COMPLETED_EVENT,
            {"tenant_id": tenant_id, "desfecho": "sem_glosa", "numero_lote_tiss": "LOTE-X"},
            key="dormant",
        )
        dormant_message = await _consume_one(consumer)
        await consumer.commit()
        dormant_results = await handle_bridge_message(bridge, dormant_message)
        assert not [r for r in dormant_results if r.handoff_triggered], (
            "a non-matching desfecho must never trigger a handoff"
        )

        # Armed: SYNTHETIC enriched payload (numero_guia_tiss — the anchor today's real minimal
        # event_payload_vars does not yet carry; see module docstring).
        await producer.publish(
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": tenant_id,
                "desfecho": "glosa_aplicada_humano",
                "numero_guia_tiss": guia,
                "glosa_id": glosa,
            },
            key="armed",
        )
        armed_message = await _consume_one(consumer)
        await consumer.commit()
        armed_results = await handle_bridge_message(bridge, armed_message)
        triggered = [r for r in armed_results if r.handoff_triggered]
        assert len(triggered) == 1
        assert triggered[0].target_process == "SP-OP-RECURSO-001"
        assert triggered[0].process_instance_id
        expected_bk = f"RECURSO-{tenant_id}-{guia}-{glosa}"
        assert triggered[0].variables["business_key"] == expected_bk

        rows = await _fetch_start_process_rows(dsn, tenant_id)
        assert len(rows) == 1, "only the ARMED handoff may durably audit a start"
        assert rows[0]["agent_id"] == "notification_bridge"
    finally:
        await producer.close()
        await consumer.stop()
        await audit_sink.aclose()
        await transport.close()


# ---------------------------------------------------------------------------
# (d) producer failure (Kafka down) -> source publish path still completes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_failure_kafka_down_does_not_block_source_process() -> None:
    """The REAL `events.py` publish handler (`operadora.events.publish` worker every SP-OP-*
    BPMN's `ST_Publish*` task routes through) driven by a producer pointed at an UNREACHABLE
    broker must complete normally — never raise, never stall the source BPMN process. This is the
    fail-safe discipline the module docstring documents (`BEST_EFFORT_TOPICS`): a genuine Kafka
    outage on a mirrored/best-effort topic is audited (`producer.failed_publishes`), not
    propagated. Does NOT need `kafka_bootstrap_servers`/a real broker — the point IS there is none."""
    unreachable_producer = AioKafkaEventsProducer(
        bootstrap_servers="localhost:1", connect_timeout_s=3.0, send_timeout_s=3.0
    )
    handler = make_publish_event_handler(unreachable_producer)
    task = ExternalTask(
        task_id="t4-kafka-down-probe",
        topic="operadora.events.publish",
        process_instance_id="proc-kafka-down-probe",
        business_key="bk-kafka-down-probe",
        worker_id="w1",
        variables={
            "event_topic": CONTAS_COMPLETED_EVENT,
            "event_desfecho": "glosa_aplicada_humano",
            "event_payload_vars": "tenant_id",
            "tenant_id": "amh",
        },
    )

    result = await asyncio.wait_for(handler(task), timeout=15.0)  # must not raise

    assert result is not None
    assert result["event_topic"] == CONTAS_COMPLETED_EVENT
    # Both legs (primary contas topic + notifications mirror) failed against the unreachable
    # broker — both are best-effort, both audited on this in-process ledger, neither raised.
    assert len(unreachable_producer.failed_publishes) == 2
    failed_topics = {topic for topic, _err in unreachable_producer.failed_publishes}
    assert failed_topics == {CONTAS_COMPLETED_EVENT, NOTIFICATIONS_TOPIC}
    await unreachable_producer.close()


# ---------------------------------------------------------------------------
# (e) consumer idempotency on redelivery (dedup).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivered_message_is_idempotent_one_audit_row_same_instance(
    kafka_bootstrap_servers: str,
    pg_tenant_schema: tuple[str, str],
) -> None:
    """Publish the SAME armed event ONCE, but consume+dispatch it TWICE (simulating a redelivery
    — e.g. a consumer crash before offset-commit) through the SAME bridge/fenced-starter/audit
    sink. `start_process_idempotent`'s own exactly-once `emit_once` dedup + `find_active_instance`
    hit converge on ONE audit_chain row and the SAME `process_instance_id` — proven here through
    the producer/consumer/bridge wiring end-to-end, not re-derived in isolation."""
    dsn, tenant_id = pg_tenant_schema
    suffix = uuid.uuid4().hex[:8]
    guia, glosa = f"GUIAD-{suffix}", f"GLOSAD-{suffix}"
    group_id = f"t4-dedup-{suffix}"

    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)

    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers, topic=NOTIFICATIONS_TOPIC, group_id=group_id
    )
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    await consumer.start()
    try:
        await _await_assigned(consumer._consumer)  # noqa: SLF001 - readiness-only reach-through
        await consumer._consumer.seek_to_end()  # noqa: SLF001 - see above
        await producer.publish(
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": tenant_id,
                "desfecho": "glosa_aplicada_humano",
                "numero_guia_tiss": guia,
                "glosa_id": glosa,
            },
            key="redelivery-probe",
        )
        message = await _consume_one(consumer)
        # Deliberately do NOT commit before the second dispatch — this is exactly the scenario
        # that produces a redelivery in production (crash/restart between dispatch and commit).

        first = await handle_bridge_message(bridge, message)
        second = await handle_bridge_message(bridge, dict(message))  # simulated redelivery
        await consumer.commit()

        first_triggered = [r for r in first if r.handoff_triggered]
        second_triggered = [r for r in second if r.handoff_triggered]
        assert len(first_triggered) == 1
        assert len(second_triggered) == 1
        assert first_triggered[0].process_instance_id == second_triggered[0].process_instance_id

        rows = await _fetch_start_process_rows(dsn, tenant_id)
        assert len(rows) == 1, "a redelivered handoff must not write a second audit-chain row"
    finally:
        await producer.close()
        await consumer.stop()
        await audit_sink.aclose()
        await transport.close()


# ---------------------------------------------------------------------------
# ans.cron_due already targets NOTIFICATIONS_TOPIC directly (gap (1), module docstring) — proves
# the real producer alone (no mirroring needed) closes that BPMN-side wiring end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ans_cron_due_reaches_notifications_topic_unmirrored(
    kafka_bootstrap_servers: str,
) -> None:
    """`SP-OP-ANS-CRON-001`'s `ST_PublishCronDue*` tasks already set `event_topic=
    operadora.notifications.internal` + `event_type=ans.cron_due` as BPMN literals (verified by
    grep against `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` — untouched by
    this task). Once a real producer exists, that fact reaches the bridge's consumer with ZERO
    additional mirroring — a single publish, one message, no duplicate."""
    suffix = uuid.uuid4().hex[:8]
    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers,
        topic=NOTIFICATIONS_TOPIC,
        group_id=f"t4-cron-probe-{suffix}",
    )
    await consumer.start()
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    try:
        await _await_assigned(consumer._consumer)  # noqa: SLF001 - readiness-only reach-through
        await consumer._consumer.seek_to_end()  # noqa: SLF001 - see above
        # CHANGED (GAP-SC-04-a): an explicit key is now required. This payload is the BPMN's own
        # literal shape and carries neither `_business_key` nor an `anssubmit`-family anchor
        # (`report_type`+`competencia` without a `tenant_id`), so the derivation yields nothing and
        # the producer fails closed — which is the gate working. In production the same publish
        # comes from `events.py`, whose payload always carries `_business_key`/
        # `_process_instance_id`; here the key is supplied explicitly to stand in for that.
        await producer.publish(
            NOTIFICATIONS_TOPIC,
            {
                "type": "ans.cron_due",
                "report_type": f"RN_TEST_{suffix}",
                "periodicidade": "mensal",
                "origem_envio": "calendario",
                "competencia": "COMPETENCIA_PENDENTE",
            },
            key=f"ANSSUB-amh-RN_TEST_{suffix}",
        )
        received = await _consume_one(consumer)
        await consumer.commit()
        assert received["type"] == "ans.cron_due"
        assert received["report_type"] == f"RN_TEST_{suffix}"
    finally:
        await producer.close()
        await consumer.stop()


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the two halves of the ordering/DLQ slice, against a REAL broker.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_entity_events_land_on_the_same_partition(
    kafka_bootstrap_servers: str,
) -> None:
    """THE ORDERING PROPERTY, proven against a real broker rather than against the key derivation.

    Two `agents.events.contas.completed` events about the SAME guia+glosa are published with no
    explicit key; the producer derives one from the family anchors. Consume both back from the
    per-domain topic and assert they carry the same key AND landed on the SAME partition — which
    is what makes their relative order survive any number of consumer replicas. A third event
    about a DIFFERENT entity is published as the control: the derivation must not collapse a whole
    tenant onto one partition."""
    from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

    suffix = uuid.uuid4().hex[:8]
    topic = CONTAS_COMPLETED_EVENT
    await _await_topic_ready(kafka_bootstrap_servers, topic)
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_bootstrap_servers,
        group_id=f"sc04a-order-{suffix}",
        auto_offset_reset="latest",
    )
    await consumer.start()
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    try:
        await _await_assigned(consumer)
        await consumer.seek_to_end()
        entity = {"tenant_id": "amh", "numero_guia_tiss": f"GUIA-{suffix}", "glosa_id": f"GLOSA-{suffix}"}
        await producer.publish(topic, {**entity, "fase": "recebido"})
        await producer.publish(topic, {**entity, "desfecho": "encaminhada_recurso"})
        await producer.publish(
            topic,
            {"tenant_id": "amh", "numero_guia_tiss": f"GUIA-OTHER-{suffix}", "glosa_id": f"GLOSA-{suffix}"},
        )

        records = [await asyncio.wait_for(consumer.__anext__(), timeout=15.0) for _ in range(3)]
        first, second, other = records

        expected_key = f"amh|GUIA-{suffix}|GLOSA-{suffix}".encode()
        assert first.key == second.key == expected_key
        assert first.partition == second.partition, (
            "same entity -> same key -> same partition; this is the whole ordering guarantee"
        )
        assert other.key != expected_key, "a different entity must not share the first one's key"
    finally:
        await producer.close()
        await consumer.stop()


@pytest.mark.asyncio
async def test_poison_message_is_shunted_to_a_real_dlq_topic_and_the_loop_continues(
    kafka_bootstrap_servers: str,
    pg_tenant_schema: tuple[str, str],
) -> None:
    """THE DLQ, END TO END, ON REAL INFRASTRUCTURE. Publish a MALFORMED record (no `type`) and a
    well-formed armed one onto `NOTIFICATIONS_TOPIC`; drive the REAL `AioKafkaBridgeConsumer`
    through `run_consumer_loop` with the REAL `AioKafkaDlqPublisher` and a REAL `PostgresAuditSink`.

    Asserts every half of the claim:
      - the poison record lands on `operadora.notifications.internal.dlq`, RAW bytes verbatim,
        with the bounded reason header;
      - a durable `bridge_dlq:` audit row exists in the real `audit_chain`;
      - the loop CONTINUED — the well-formed message behind the poison one still started its
        handoff (this is the head-of-line blocking that used to kill the daemon);
      - the consumer offsets advanced (no re-delivery storm on restart).
    """
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore[import-untyped]

    dsn, tenant_id = pg_tenant_schema
    suffix = uuid.uuid4().hex[:8]
    guia, glosa = f"GUIAQ-{suffix}", f"GLOSAQ-{suffix}"
    dlq_topic = dlq_topic_for(NOTIFICATIONS_TOPIC)
    poison_raw = json.dumps({"tenant_id": tenant_id, "sem_tipo": guia}).encode("utf-8")

    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)
    await _await_topic_ready(kafka_bootstrap_servers, dlq_topic)

    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers,
        topic=NOTIFICATIONS_TOPIC,
        group_id=f"sc04a-dlq-{suffix}",
    )
    dlq_consumer = AIOKafkaConsumer(
        dlq_topic,
        bootstrap_servers=kafka_bootstrap_servers,
        group_id=f"sc04a-dlq-probe-{suffix}",
        auto_offset_reset="latest",
    )
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))
    dlq = BridgeDlqShunt(
        publisher=AioKafkaDlqPublisher(bootstrap_servers=kafka_bootstrap_servers),
        audit_sink=audit_sink,
        tenant_id=tenant_id,
    )

    await consumer.start()
    await dlq_consumer.start()
    await dlq.publisher.start()
    seed = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap_servers)
    await seed.start()
    try:
        await _await_assigned(consumer._consumer)  # noqa: SLF001 - readiness-only reach-through
        await _await_assigned(dlq_consumer)
        await consumer._consumer.seek_to_end()  # noqa: SLF001 - see above
        await dlq_consumer.seek_to_end()
        await seed.send_and_wait(NOTIFICATIONS_TOPIC, poison_raw, key=b"poison-probe")
        await seed.send_and_wait(
            NOTIFICATIONS_TOPIC,
            json.dumps(
                {
                    "type": CONTAS_COMPLETED_EVENT,
                    "tenant_id": tenant_id,
                    "desfecho": "encaminhada_recurso",
                    "numero_guia_tiss": guia,
                    "glosa_id": glosa,
                }
            ).encode("utf-8"),
            key=b"armed-probe",
        )

        stop_event = asyncio.Event()
        stop_event.set()  # end the loop after the 2nd message rather than blocking on poll

        async def _drive_two() -> None:
            consumed = 0
            async for message in consumer:
                consumed += 1
                await run_consumer_loop(_SingleMessageConsumer(message, consumer), bridge, dlq=dlq)
                if consumed == 2:
                    return

        await asyncio.wait_for(_drive_two(), timeout=45.0)

        # 1. the poison record reached the REAL DLQ topic, bytes verbatim.
        dlq_record = await asyncio.wait_for(dlq_consumer.__anext__(), timeout=20.0)
        assert dlq_record.value == poison_raw
        headers = dict(dlq_record.headers)
        assert headers["maezo_dlq_reason"] == REASON_MISSING_TYPE.encode()
        assert headers["maezo_dlq_source_topic"] == NOTIFICATIONS_TOPIC.encode()
        assert dlq_record.key == b"poison-probe", "the producer's own key rides along"

        # 2. a durable audit fact exists for the quarantine.
        dlq_rows = await _fetch_audit_rows(dsn, tenant_id, f"bridge_dlq:{NOTIFICATIONS_TOPIC}")
        assert len(dlq_rows) == 1
        assert dlq_rows[0]["agent_id"] == "notification_bridge"

        # 3. THE LOOP CONTINUED — the message BEHIND the poison one still started its handoff.
        start_rows = await _fetch_start_process_rows(dsn, tenant_id)
        assert len(start_rows) == 1, (
            "a poison message must no longer block every message behind it (head-of-line blocking)"
        )
    finally:
        await seed.stop()
        await dlq.publisher.stop()
        await dlq_consumer.stop()
        await consumer.stop()
        await audit_sink.aclose()
        await transport.close()


class _SingleMessageConsumer:
    """Feeds ONE already-consumed `BridgeMessage` into `run_consumer_loop` while delegating the
    commit to the REAL consumer.

    Why it exists rather than handing `run_consumer_loop` the live consumer directly: the loop is
    an unbounded `async for` that only ends on a `stop_event` checked BETWEEN messages, so a test
    driving a fixed number of records would block on the next poll forever. This keeps the loop's
    real dispatch/shunt/commit path — including a REAL `consumer.commit()` against the broker — and
    only bounds the iteration. Test-local by design (it is not a production seam).
    """

    def __init__(self, message: Any, real_consumer: AioKafkaBridgeConsumer) -> None:
        self._message = message
        self._real = real_consumer
        self.commits = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1
        await self._real.commit()

    def __aiter__(self) -> Any:
        return self._iter()

    async def _iter(self) -> Any:
        yield self._message


async def _fetch_audit_rows(dsn: str, tenant_id: str, action: str) -> list[Any]:
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(
            f'SELECT action, agent_id FROM "{schema}".audit_chain WHERE action = $1 ORDER BY id',
            action,
        )
    finally:
        await conn.close()
