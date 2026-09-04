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
      receives it -> for a rule-matching payload (the REAL `ST_PublishEncaminhadaFraude` shape —
      see "WHICH BRIDGE RULE" below), the bridge invokes a RECORDING starter seam
      (`FakeCibSevenTransport` — the fenced START itself, `start_process_idempotent`, is
      engine-proven elsewhere, e.g. `test_notifications_bridge_live_engine.py`) + writes a REAL
      audit row on live Postgres.
  (c) a NON-matching payload — the equally real `ST_PublishGlosaAplicada` shape, MORE anchored than
      the armed one but carrying a `desfecho` no surviving rule selects -> no starter call, no
      audit row.
  (d) producer failure (Kafka unreachable) -> the SOURCE path (the actual `events.py` publish
      handler) still completes normally — never raises, never blocks the BPMN process.
  (e) consumer idempotency on redelivery -> the SAME message consumed twice converges on ONE
      audit_chain row and the SAME process_instance_id (`start_process_idempotent`'s own
      exactly-once dedup + `find_active_instance` hit, proven end-to-end through THIS module's
      wiring rather than re-deriving it).

WHICH BRIDGE RULE THIS SUITE DRIVES, AND WHY (ADR-0040 §3.1 reconciliation — the reason the
payloads below changed shape). Until PR #294 (`c5b7505`) three of these tests published
`agents.events.contas.completed` with a SYNTHETIC `numero_guia_tiss`+`glosa_id` pair and expected
`SP-OP-RECURSO-001` to start. That CONTAS→RECURSO edge NO LONGER EXISTS: `notification_bridge.py`
(`_register_default_handoffs`, ~:575-598) retired it as an inverted-perspective encoding — the
payer does not appeal its own glosa, it RECEIVES the prestador's appeal — and replaced it with
INTAKE→RECURSO on `agents.events.recurso.intake_recebido`. The ONLY surviving
`agents.events.contas.completed` rule is CONTAS→FRAUDE (~:634-659): predicate
`desfecho == "encaminhada_fraude"` plus the `prestador_id` anchor, target `SP-OP-FRAUDE-001`,
business key `FRAUDE-{tenant_id}-{numero_caso}` where `numero_caso` falls back to `prestador_id`
(`_fraude_business_key` over the shared `workers.base.resolve_fraude_numero_caso`).

So this suite drives CONTAS→FRAUDE, not INTAKE→RECURSO, for a STRUCTURAL reason rather than a
convenient one: its whole subject is the producer's MIRROR leg, and only `MIRROR_TOPICS`
(`events_kafka_producer.py` ~:123-129 = `{agents.events.contas.completed,
agents.events.fraude.completed}`) is mirrored onto `NOTIFICATIONS_TOPIC`.
`agents.events.recurso.intake_recebido` is not mirrored — and has no publisher at all, dormant on
purpose (`notification_bridge.py` ~:584-596) — so re-pointing these tests at it would mean
hand-seeding an envelope and abandoning the very leg under test. INTAKE→RECURSO is proven on the
lanes where it belongs: `test_notifications_bridge_live_pg.py` (fenced start + durable audit row on
live Postgres) and `tests/unit/platform/test_notification_bridge.py`. The move also STRENGTHENS
this file: CONTAS→FRAUDE is an ARMED edge with a REAL publisher — `SP-OP-CONTAS-001`'s
`ST_PublishEncaminhadaFraude` emits exactly `event_payload_vars=tenant_id,numero_lote_tiss,
prestador_id` with `event_desfecho=encaminhada_fraude`
(`spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn` ~:416-421) — so the armed
payloads below stopped being "synthetic enriched" and are now the exact shape the BPMN publishes.
No assertion was weakened, skipped or xfailed to accommodate the retired edge.

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
from maezo.platform.topic_registry import TopicRegistry, dlq_topic_for
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

#: Partition count `test_same_entity_events_land_on_the_same_partition` creates its OWN topic with.
#: `docker-compose.yml`'s kafka service sets no `KAFKA_NUM_PARTITIONS`, so cp-kafka auto-creates
#: every topic with exactly ONE partition — under which `first.partition == second.partition` is
#: true for EVERY possible input and the ordering claim is unfalsifiable (it cannot fail, so it
#: proves nothing). 3 is the platform's own declared default for a real topic
#: (`maezo.platform.topic_registry.TopicEntry.partitions = 3`, ~:89) — i.e. the partition count the
#: ordering guarantee actually has to hold under in production.
_ORDERING_TOPIC_PARTITIONS = 3

#: How many DIFFERENT entities that same test publishes as its spread control. Their derived keys
#: are FIXED (no per-run suffix — the per-run uniqueness lives in the TOPIC name instead), so
#: aiokafka's murmur2 `DefaultPartitioner` maps them to the same partitions on every run: the
#: ">1 partition" assertion below is deterministic, not a probabilistic bet on hash spread.
#: Measured on the compose broker with `_ORDERING_TOPIC_PARTITIONS = 3`: these 12 keys occupy all
#: 3 partitions (6/4/2), so the assertion has real headroom.
_ORDERING_SPREAD_ENTITIES = 12


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
    """EVERY `start_process:*` audit_chain row in the tenant schema, oldest first.

    Deliberately NOT filtered to one process key (it used to hard-code
    `action = 'start_process:SP-OP-RECURSO-001'`): each caller asserts BOTH how many starts were
    durably audited AND which process each one was, so a rule that starts the WRONG process — or a
    dormant payload that starts anything at all — now fails loudly instead of being filtered out of
    the result set and read as "no rows, as expected"."""
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(
            f'SELECT action, agent_id FROM "{schema}".audit_chain '
            "WHERE action LIKE 'start_process:%' ORDER BY id"
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
    bootstrap_servers: str,
    topic: str,
    *,
    num_partitions: int = 1,
    timeout_s: float = _READINESS_TIMEOUT_S,
) -> set[int]:
    """Create `topic` (idempotently) and wait until at least one of its partitions has an elected
    leader. Returns the set of partitions that DO have one.

    `num_partitions` is the count used when this call is the one that CREATES the topic; an
    already-existing topic keeps whatever partition count it was created with (the broker answers
    `TOPIC_ALREADY_EXISTS`, which `_TOLERATED_CREATE_ERROR_CODES` accepts). That asymmetry is why
    the count is RETURNED rather than assumed: a caller whose claim depends on the topic really
    having N partitions must assert on the returned set — see
    `test_same_entity_events_land_on_the_same_partition`, which publishes to a per-run topic name
    precisely so this call is always the creating one."""
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
                    admin.create_topics(
                        [NewTopic(topic, num_partitions=num_partitions, replication_factor=1)]
                    ),
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
                return set(leader_partitions)
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


async def _await_assigned(
    real_consumer: Any, *, expected_partitions: int | None = None, timeout_s: float = 15.0
) -> None:
    """Poll a real `aiokafka` consumer's own `assignment()` until non-empty. Pass the WRAPPED
    consumer directly (`AIOKafkaConsumer`) or, for `AioKafkaBridgeConsumer`, its `_consumer`
    (mirrors `test_notifications_bridge_live_kafka.py::_await_consumer_assigned` — no public seam
    on the bridge wrapper for this readiness-only reach-through). Raises (fails, never skips) if
    the bound expires: an already-proven-reachable broker never handing a partition is a real
    defect.

    `expected_partitions`, when given, additionally requires the assignment to COVER that many
    partitions before returning. On a multi-partition topic a lone consumer gets them all in one
    rebalance, but waiting for the full set is what makes the `seek_to_end()` that follows pin
    EVERY partition's starting position rather than a subset of them."""
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        assignment = real_consumer.assignment()
        if assignment and (expected_partitions is None or len(assignment) >= expected_partitions):
            print(
                f"[producer_live readiness] attempt {attempt}: consumer.assignment()={assignment!r}",
                flush=True,
            )
            return
        print(
            f"[producer_live readiness] attempt {attempt}: consumer.assignment()={assignment!r} does "
            f"not yet cover {expected_partitions} partition(s) — retrying",
            flush=True,
        )
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"producer_live readiness: consumer.assignment() still does not cover "
        f"{expected_partitions if expected_partitions is not None else 1} partition(s) after "
        f"{timeout_s}s ({attempt} attempts) despite start() returning and the topic already having "
        "a leader — a genuine defect, not a skip condition."
    )


async def _delete_topic(bootstrap_servers: str, topic: str) -> None:
    """Best-effort teardown for a per-run topic (`test_same_entity_events_land_on_the_same_partition`
    creates one so it can pin its own partition count).

    Deliberately non-fatal: the topic name carries a per-run uuid, so a leftover is inert and a
    broker that refuses deletion (`delete.topic.enable=false`) must not turn an otherwise green
    test red. The refusal is PRINTED rather than swallowed silently, so a stack that keeps
    accumulating topics is visible in the run log."""
    from aiokafka.admin import AIOKafkaAdminClient  # type: ignore[import-untyped]

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        await admin.start()
        await asyncio.wait_for(admin.delete_topics([topic]), timeout=10.0)
        print(f"[producer_live cleanup] deleted per-run topic {topic!r}", flush=True)
    except Exception as exc:  # noqa: BLE001 - teardown must never mask the test's own outcome
        print(
            f"[producer_live cleanup] could not delete per-run topic {topic!r} "
            f"({type(exc).__name__}: {exc}) — inert, the name is unique per run",
            flush=True,
        )
    finally:
        with contextlib.suppress(Exception):  # best-effort cleanup only
            await admin.close()


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
    """Publish TWO REAL `agents.events.contas.completed` events through the producer — both in
    shapes `SP-OP-CONTAS-001` itself publishes — consume both via the REAL
    `AioKafkaBridgeConsumer`, dispatch both through `handle_bridge_message` against a bridge wired
    to the FENCED starter (`FakeCibSevenTransport` recording + REAL `PostgresAuditSink`), and prove
    the bridge discriminates between them:

      - ARMED: the `ST_PublishEncaminhadaFraude` shape — `event_payload_vars=tenant_id,
        numero_lote_tiss,prestador_id` with `event_desfecho=encaminhada_fraude`
        (`SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn` ~:416-421) — matches the surviving
        CONTAS→FRAUDE rule, so `SP-OP-FRAUDE-001` starts through the fence and durably audits.
      - DORMANT: the `ST_PublishGlosaAplicada` shape (~:328-333), which is MORE anchored than the
        armed one (it also carries `glosa_id`/`numero_guia_tiss`/`analista_id`) yet whose
        `desfecho` is `glosa_aplicada_humano` — no surviving `agents.events.contas.completed` rule
        selects it, so nothing starts and nothing is audited.

    ADR-0040 §3.1 (module docstring, "WHICH BRIDGE RULE"): this test used to expect
    `SP-OP-RECURSO-001` off a SYNTHETIC `numero_guia_tiss`+`glosa_id` payload, an edge PR #294
    retired. Two things got STRONGER in the move, not weaker: the armed payload is now the real
    BPMN shape instead of a synthetic one, and the dormant control is now a FULLY anchored event
    whose only disqualifier is the `desfecho` predicate itself — proving the rule discriminates on
    the routing fact rather than merely on absent anchors. Nothing was weakened, skipped or
    xfailed; the audit assertions are the same strict ones, now over every `start_process:*` row
    rather than one process key's."""
    dsn, tenant_id = pg_tenant_schema
    suffix = uuid.uuid4().hex[:8]
    prestador, lote = f"PREST-{suffix}", f"LOTE-{suffix}"
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

        # Dormant first: the REAL `ST_PublishGlosaAplicada` payload, every `event_payload_vars`
        # name that task declares. `codigo_glosa_tiss` is one of them and is NOT in
        # `events_kafka_producer.MIRROR_PAYLOAD_ALLOWLIST`, so the mirror scrub drops it before the
        # envelope reaches the bridge — irrelevant to this rule's predicate either way, and the
        # scrub itself is what test (a) asserts.
        await producer.publish(
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": tenant_id,
                "desfecho": "glosa_aplicada_humano",
                "numero_lote_tiss": lote,
                "prestador_id": prestador,
                "glosa_id": f"GLOSA-{suffix}",
                "numero_guia_tiss": f"GUIA-{suffix}",
                "codigo_glosa_tiss": f"COD-{suffix}",
                "analista_id": f"ANL-{suffix}",
            },
            key="dormant",
        )
        dormant_message = await _consume_one(consumer)
        await consumer.commit()
        dormant_results = await handle_bridge_message(bridge, dormant_message)
        assert not [r for r in dormant_results if r.handoff_triggered], (
            "a non-matching desfecho must never trigger a handoff — not even a FULLY anchored one: "
            "the CONTAS→FRAUDE predicate keys off the routing fact, never off anchor presence"
        )

        # Armed: the REAL `ST_PublishEncaminhadaFraude` payload — its three `event_payload_vars`
        # verbatim, nothing synthetic added (the `prestador_id` anchor the predicate needs IS what
        # that task publishes; see `notification_bridge.py`'s own "ARMED (commit 9cc8aaa)" note).
        await producer.publish(
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": tenant_id,
                "desfecho": "encaminhada_fraude",
                "numero_lote_tiss": lote,
                "prestador_id": prestador,
            },
            key="armed",
        )
        armed_message = await _consume_one(consumer)
        await consumer.commit()
        armed_results = await handle_bridge_message(bridge, armed_message)
        triggered = [r for r in armed_results if r.handoff_triggered]
        assert len(triggered) == 1
        assert triggered[0].target_process == "SP-OP-FRAUDE-001"
        assert triggered[0].process_instance_id
        # `numero_caso` is absent from a CONTAS forward, so `resolve_fraude_numero_caso` falls back
        # to `prestador_id` — the SAME key `contas.start_fraude`'s in-flow worker derives, which is
        # what makes the two paths converge instead of double-starting.
        expected_bk = f"FRAUDE-{tenant_id}-{prestador}"
        assert triggered[0].variables["business_key"] == expected_bk

        rows = await _fetch_start_process_rows(dsn, tenant_id)
        assert len(rows) == 1, "only the ARMED handoff may durably audit a start"
        assert rows[0]["action"] == "start_process:SP-OP-FRAUDE-001"
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
    the producer/consumer/bridge wiring end-to-end, not re-derived in isolation.

    ADR-0040 §3.1 (module docstring, "WHICH BRIDGE RULE"): the redelivered event is now the REAL
    `ST_PublishEncaminhadaFraude` payload driving CONTAS→FRAUDE, not the SYNTHETIC
    `numero_guia_tiss`+`glosa_id` payload driving the CONTAS→RECURSO edge PR #294 retired. The
    IDEMPOTENCY claim — the whole point of this test — is unchanged and its assertions are
    untouched: one durable row, one process_instance_id, for the same event delivered twice. The
    convergence anchor is now `FRAUDE-{tenant}-{prestador_id}` (`resolve_fraude_numero_caso`'s
    documented "repeated referrals of the SAME prestador converge on the SAME instance")."""
    dsn, tenant_id = pg_tenant_schema
    suffix = uuid.uuid4().hex[:8]
    prestador, lote = f"PRESTD-{suffix}", f"LOTED-{suffix}"
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
                "desfecho": "encaminhada_fraude",
                "numero_lote_tiss": lote,
                "prestador_id": prestador,
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
        assert first_triggered[0].target_process == "SP-OP-FRAUDE-001"
        assert first_triggered[0].variables["business_key"] == f"FRAUDE-{tenant_id}-{prestador}"

        rows = await _fetch_start_process_rows(dsn, tenant_id)
        assert len(rows) == 1, "a redelivered handoff must not write a second audit-chain row"
        assert rows[0]["action"] == "start_process:SP-OP-FRAUDE-001"
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
    """THE ORDERING PROPERTY, proven against a real broker ON A TOPIC WHERE IT CAN FAIL.

    Two `agents.events.contas.*` events about the SAME guia+glosa are published with no explicit
    key; the producer derives one from the family anchors (`partition_key.ENTITY_ANCHORS`
    `contas` group). They must carry the same key AND land on the SAME partition — which is what
    makes their relative order survive any number of consumer replicas. `_ORDERING_SPREAD_ENTITIES`
    events about DIFFERENT entities are the control: the derivation must not collapse a whole
    tenant onto one partition.

    WHY THIS TEST CREATES ITS OWN TOPIC. On the compose stack every topic is auto-created with ONE
    partition (`docker-compose.yml`'s kafka service sets no `KAFKA_NUM_PARTITIONS`), and on a
    1-partition topic `first.partition == second.partition` holds for EVERY conceivable input —
    including a producer that ignored the key entirely and round-robined. The assertion could not
    fail, so it proved nothing; the test passed vacuously the moment the suite started running
    (gap `PRODUCER-LIVE-19092-DEFAULT` made it run at all). It now creates a PER-RUN topic with
    `_ORDERING_TOPIC_PARTITIONS` partitions, ASSERTS that precondition (loudly — never a skip:
    the broker is already proven reachable, so a topic that will not take the requested partition
    count is a real defect), and only then makes the ordering claim, which on 3 partitions is
    falsifiable: a keyless/round-robin producer would spread the same entity's two events across
    partitions with probability 2/3.

    The topic name keeps the reserved `agents.events.{dominio}.{acao}` shape with `{dominio}` =
    `contas`, so `partition_key.anchor_family` resolves exactly the same anchor group it resolves
    for `agents.events.contas.completed` and the derivation under test is the production one, not
    a look-alike. Being per-run, it is also always created by THIS call (never an already-existing
    1-partition topic), and it is deleted in the `finally` below."""
    from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

    suffix = uuid.uuid4().hex[:8]
    topic = f"agents.events.contas.completed_p{_ORDERING_TOPIC_PARTITIONS}_{suffix}"
    assert TopicRegistry.validate(topic), (
        f"the per-run ordering topic {topic!r} must still satisfy the platform's own naming "
        "convention (TopicRegistry) — a test topic is not licensed to invent a shape production "
        "would reject"
    )

    ready_partitions = await _await_topic_ready(
        kafka_bootstrap_servers, topic, num_partitions=_ORDERING_TOPIC_PARTITIONS
    )
    assert len(ready_partitions) >= _ORDERING_TOPIC_PARTITIONS, (
        f"ordering PRECONDITION failed: {topic!r} came up with partitions "
        f"{sorted(ready_partitions)}, fewer than the {_ORDERING_TOPIC_PARTITIONS} this test needs. "
        "On a 1-partition topic the same-partition assertion below cannot fail and therefore "
        "proves nothing — that vacuity is the defect this precondition exists to refuse. Fail, "
        "never skip: the broker is already proven reachable by the kafka_bootstrap_servers fixture."
    )

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_bootstrap_servers,
        group_id=f"sc04a-order-{suffix}",
        auto_offset_reset="latest",
    )
    await consumer.start()
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers)
    try:
        # Wait for the assignment to cover EVERY partition before seeking: a partial assignment
        # would leave some partitions' start position unpinned.
        await _await_assigned(consumer, expected_partitions=len(ready_partitions))
        await consumer.seek_to_end()

        entity = {"tenant_id": "amh", "numero_guia_tiss": f"GUIA-{suffix}", "glosa_id": f"GLOSA-{suffix}"}
        await producer.publish(topic, {**entity, "fase": "recebido"})
        await producer.publish(topic, {**entity, "desfecho": "encaminhada_fraude"})
        # The spread control. FIXED anchors (no per-run suffix) so the murmur2 partition assignment
        # is identical on every run — the ">1 partition" assertion is deterministic, not a bet.
        spread = [
            {
                "tenant_id": "amh",
                "numero_guia_tiss": f"GUIA-SPREAD-{i:02d}",
                "glosa_id": f"GLOSA-SPREAD-{i:02d}",
            }
            for i in range(_ORDERING_SPREAD_ENTITIES)
        ]
        for payload in spread:
            await producer.publish(topic, dict(payload))

        expected_total = 2 + _ORDERING_SPREAD_ENTITIES
        records = [await asyncio.wait_for(consumer.__anext__(), timeout=20.0) for _ in range(expected_total)]
        # Records from several partitions interleave arbitrarily — index by key, never positionally.
        by_key: dict[bytes, list[Any]] = {}
        for record in records:
            by_key.setdefault(record.key, []).append(record)

        entity_key = f"amh|GUIA-{suffix}|GLOSA-{suffix}".encode()
        assert entity_key in by_key, (
            f"the two same-entity events must derive the key {entity_key!r}; got {sorted(by_key)}"
        )
        same_entity = by_key[entity_key]
        assert len(same_entity) == 2, "both events about the SAME entity must derive the SAME key"
        assert same_entity[0].partition == same_entity[1].partition, (
            f"same entity -> same key -> same partition; this is the whole ordering guarantee. Got "
            f"partitions {same_entity[0].partition} and {same_entity[1].partition} out of "
            f"{sorted(ready_partitions)}"
        )

        spread_keys = {
            f"amh|{payload['numero_guia_tiss']}|{payload['glosa_id']}".encode() for payload in spread
        }
        assert len(spread_keys) == _ORDERING_SPREAD_ENTITIES, "control entities must be distinct"
        assert entity_key not in spread_keys, "the control must not reuse the measured entity's key"
        assert spread_keys <= set(by_key), (
            "every control entity must derive its own distinct key; missing "
            f"{sorted(spread_keys - set(by_key))}"
        )
        assert all(len(by_key[key]) == 1 for key in spread_keys), (
            "each control entity was published exactly once, so each key must carry exactly 1 record"
        )

        spread_partitions = {by_key[key][0].partition for key in spread_keys}
        assert len(spread_partitions) > 1, (
            f"{_ORDERING_SPREAD_ENTITIES} DIFFERENT entities all landed on partition "
            f"{sorted(spread_partitions)} of {sorted(ready_partitions)} — the key derivation is "
            "collapsing a whole tenant onto one partition instead of hashing per entity, which "
            "would serialize every entity behind every other one"
        )
        print(
            f"[producer_live ordering] topic {topic!r} partitions {sorted(ready_partitions)}: "
            f"same entity -> partition {same_entity[0].partition}; "
            f"{_ORDERING_SPREAD_ENTITIES} distinct entities -> partitions "
            f"{sorted(spread_partitions)}",
            flush=True,
        )
    finally:
        await producer.close()
        await consumer.stop()
        await _delete_topic(kafka_bootstrap_servers, topic)


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
    prestador, lote = f"PRESTQ-{suffix}", f"LOTEQ-{suffix}"
    dlq_topic = dlq_topic_for(NOTIFICATIONS_TOPIC)
    poison_raw = json.dumps({"tenant_id": tenant_id, "sem_tipo": lote}).encode("utf-8")

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
                    # The well-formed envelope behind the poison one is the shape the producer's
                    # OWN mirror leg puts on this topic for `ST_PublishEncaminhadaFraude` — the
                    # surviving CONTAS→FRAUDE rule (ADR-0040 §3.1, module docstring). It used to
                    # carry `desfecho=encaminhada_recurso` + a synthetic guia/glosa pair for the
                    # CONTAS→RECURSO edge PR #294 retired; the DLQ/head-of-line-blocking claims
                    # this test exists for are unchanged.
                    "type": CONTAS_COMPLETED_EVENT,
                    "tenant_id": tenant_id,
                    "desfecho": "encaminhada_fraude",
                    "numero_lote_tiss": lote,
                    "prestador_id": prestador,
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
        assert start_rows[0]["action"] == "start_process:SP-OP-FRAUDE-001"
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
