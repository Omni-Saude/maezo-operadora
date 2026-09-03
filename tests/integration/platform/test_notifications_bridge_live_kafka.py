"""Live-Kafka proof for `AioKafkaBridgeConsumer` — the HONEST external boundary
`maezo.platform.integrations.notifications_bridge`'s own module docstring documents.

Every OTHER piece of that module (the handler, the loop, the fail-closed malformed-message and
genuine-handoff-failure paths, the settings, the fail-closed composition root) is unit-proven in
`tests/unit/platform/integrations/test_notifications_bridge.py` against a fake, in-memory
consumer — no broker needed. What CANNOT be proven without a real broker is
`AioKafkaBridgeConsumer.start()`/iteration actually talking to Kafka: connecting, subscribing,
and yielding a message a real producer published.

PENDING KAFKA RUNTIME: this sandbox has no reachable broker. Mirroring
`tests/integration/conftest.py`'s `engine_base_url`/`audit_pg` fixtures (the SAME ADR-0011
"explicit could-not-verify" posture — never silent, never a faked broker standing in for a real
one), this suite probes for a broker at `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`) and
SKIPS LOUDLY when none is reachable. Bring one up (e.g. `docker run -p 9092:9092
apache/kafka:latest` or the project's own compose Kafka profile, once one exists) to run this
for real.

GAP CI-KAFKA-HEALTH-WAIT (test-side mitigation). `pytestmark = pytest.mark.integration` below
made this suite RUN in CI for the first time (gap BRIDGE-SUITES-UNMARKED); CI run 33740368677 then
showed `TimeoutError` on the strict 15s consume deadline while `kafka_bootstrap_servers` (below)
had already proven the broker reachable. Root cause, verified against a real (throwaway, isolated)
broker: `NOTIFICATIONS_TOPIC` is never pre-created anywhere in this repo, so this test is the
FIRST thing to ever touch it — `_await_topic_ready` below now creates it EXPLICITLY and confirms
a leader before anything subscribes, instead of leaning on the broker's own
`auto.create.topics.enable` + the consumer's own first-metadata-fetch race. `.github/workflows/
ci.yml`'s health-wait (~:414-423) waits for postgres/cibseven/hapi but never Kafka, even though
`docker-compose.yml`'s Kafka service declares its own `start_period: 45s` healthcheck — that fix
is owner-gated (`.github/` is not touched by this test) and stays open; see
`docs/evidence-ledger.md` gap `CI-KAFKA-HEALTH-WAIT`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from typing import Any

import pytest

from maezo.platform.integrations.notifications_bridge import (
    NOTIFICATIONS_TOPIC,
    AioKafkaBridgeConsumer,
    handle_bridge_message,
)
from maezo.platform.notification_bridge import NotificationBridge

pytestmark = pytest.mark.integration

_CONNECT_TIMEOUT_S = 5.0

#: Total bound for `_await_topic_ready`'s pre-flight poll (topic create + leader confirm), kept
#: entirely SEPARATE from the strict 15s publish->consume deadline further down — the whole point
#: is that the timed assertion should measure ONLY the round trip, never Kafka's own
#: topic-creation/group-join latency. ≤ 90s per this gap's own bound.
_READINESS_TIMEOUT_S = 90.0

#: Kafka protocol error codes `_await_topic_ready` tolerates as "topic is fine, proceed": NONE (0)
#: and TOPIC_ALREADY_EXISTS (36) — every other code is a genuine failure worth raising on.
_TOLERATED_CREATE_ERROR_CODES = (0, 36)


def _kafka_bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _kafka_reachable(bootstrap_servers: str) -> bool:
    async def _probe() -> bool:
        from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        try:
            await asyncio.wait_for(producer.start(), timeout=_CONNECT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - any failure means "skip loudly", never an error here
            with contextlib.suppress(Exception):  # best-effort cleanup (start() never completed)
                await producer.stop()
            return False
        with contextlib.suppress(Exception):  # best-effort cleanup only
            await producer.stop()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001 - defensive: a probe-internal crash is still "unreachable"
        return False


@pytest.fixture(scope="module")
def kafka_bootstrap_servers() -> str:
    """Session-scoped-per-module reachability gate — skips LOUDLY (ADR-0011 posture) rather
    than faking a broker when Kafka is not present in this environment."""
    servers = _kafka_bootstrap_servers()
    if not _kafka_reachable(servers):
        pytest.skip(
            f"COULD NOT VERIFY: no reachable Kafka broker at {servers!r} (override with "
            "KAFKA_BOOTSTRAP_SERVERS). T2.6-EB3 part 5's documented external boundary: "
            "AioKafkaBridgeConsumer needs a REAL broker to prove start()/iteration against; "
            "everything else in notifications_bridge.py is unit-proven without one. Bring one "
            "up (e.g. `docker run -p 9092:9092 apache/kafka:latest`) to run this for real."
        )
    return servers


async def _await_topic_ready(
    bootstrap_servers: str, topic: str, *, timeout_s: float = _READINESS_TIMEOUT_S
) -> None:
    """Bounded, LOUDLY-LOGGED pre-flight (gap CI-KAFKA-HEALTH-WAIT) run BEFORE the strict timed
    publish/consume in the test below, so THAT window measures only the publish->consume round
    trip — never Kafka's own topic-creation or leader-election latency.

    THE EXACT MECHANISM THIS CLOSES. `kafka_bootstrap_servers` above already proves the broker
    answers a plain `AIOKafkaProducer.start()` metadata fetch — TCP/broker liveness, nothing about
    THIS topic. `NOTIFICATIONS_TOPIC` (`operadora.notifications.internal`) is never pre-created
    anywhere in this repo, and no OTHER integration suite touches a real broker with it (the
    BPMN-process suites under `tests/integration/processes/` publish through `FakeKafkaPublisher`
    doubles, never a live one — verified: `grep -rn "AIOKafkaConsumer\\|AIOKafkaProducer" tests/
    integration/processes/` finds none). So THIS test is the first thing in a CI run to ever touch
    the topic on the shared broker: `auto.create.topics.enable` (broker default `true`) means the
    FIRST metadata request that names it triggers ASYNC creation, and the response to that very
    request can still show no leader yet. Verified against a real (throwaway, isolated) broker
    that a brand-new consumer group ALSO always pays Kafka's own `group.initial.rebalance.delay.
    ms` (broker default 3000ms) on `consumer.start()`, cold topic or not — this test mints a fresh
    `uuid4` consumer group every run, so it pays that tax every run. Neither cost is bounded by
    anything in the ORIGINAL test: only the final consume was ever wrapped in `asyncio.wait_for`.
    Under CI's specific load (this suite runs ~90 minutes and ~487 tests into the job — CI run
    33740368677's own timings), that latency is enough to blow the 15s consume deadline; it never
    reproduced locally (idle broker, idle CPU) across 20+ trials against a genuinely cold topic
    (this test's own bootstrap script, not committed).

    So: create the topic EXPLICITLY here — never rely on the auto-creation race — and confirm it
    has a partition with an elected leader, before anything ever subscribes to it.

    Every attempt logs its outcome (`print`, visible in pytest's captured-on-failure output and
    under `-s`) so CI output shows *why* readiness took as long as it did. Raises `AssertionError`
    — a genuine test FAILURE, never a swallowed skip; `kafka_bootstrap_servers` already proved the
    broker itself is reachable, so failing to ready ONE topic within `timeout_s` is a real defect
    worth failing loudly on — if the bound expires.
    """
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
            await admin.start()
            try:
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
                await admin.close()

            # Confirm a leader exists for the topic's partition(s). A short-lived producer's
            # `partitions_for` does its own internal metadata-wait/retry
            # (`AIOKafkaClient._wait_on_metadata`), bounded here to 10s per attempt.
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            try:
                partitions = await asyncio.wait_for(probe.partitions_for(topic), timeout=10.0)
            finally:
                await probe.stop()

            if partitions:
                elapsed = timeout_s - (deadline - time.monotonic())
                print(
                    f"[live_kafka readiness] attempt {attempt}: topic {topic!r} ready "
                    f"(partitions with a leader: {sorted(partitions)}) after {elapsed:.1f}s",
                    flush=True,
                )
                return
            last_reason = "create_topics succeeded but no partition reports a leader yet"
        except Exception as exc:  # noqa: BLE001 - any failure here is retried until the deadline
            last_reason = f"{type(exc).__name__}: {exc}"
        print(
            f"[live_kafka readiness] attempt {attempt}: topic {topic!r} not ready yet "
            f"({last_reason}) — retrying",
            flush=True,
        )
        await asyncio.sleep(1.0)

    raise AssertionError(
        f"live_kafka readiness: topic {topic!r} on {bootstrap_servers!r} still not ready after "
        f"{timeout_s}s ({attempt} attempts) — last reason: {last_reason}. The broker itself is "
        "reachable (the kafka_bootstrap_servers fixture already proved that); a topic that never "
        "gets a leader is a genuine defect, not a skip condition. See gap CI-KAFKA-HEALTH-WAIT in "
        "docs/evidence-ledger.md."
    )


async def _await_consumer_assigned(consumer: AioKafkaBridgeConsumer, *, timeout_s: float = 15.0) -> None:
    """Poll the real `aiokafka` consumer's own `assignment()` until non-empty.

    `AioKafkaBridgeConsumer` (`notifications_bridge.py`) deliberately exposes nothing beyond
    `start`/`stop`/`commit`/`__aiter__` (its own docstring: the honest external boundary is
    iteration, not internals) — this test reaches through to the wrapped real consumer (`_consumer`)
    only for this readiness probe, never for the assertion itself.

    In every local trial (20+ runs against a genuinely cold topic, a throwaway isolated broker —
    see `_await_topic_ready`'s docstring) `consumer.assignment()` was ALREADY non-empty the instant
    `await consumer.start()` returned: `aiokafka.AIOKafkaConsumer.start()`'s own docstring commits
    to "Join group if `group_id` provided", and its source `await`s `wait_for_assignment()` before
    returning whenever `group_id` is set and topics were passed to the constructor (exactly this
    consumer's construction). So this poll is defense-in-depth against a future aiokafka version
    where that invariant does not hold, not the primary fix — `_await_topic_ready` is. Raises
    `AssertionError` (fails, never skips) if the bound expires: a consumer whose own already-proven-
    reachable broker never hands it a partition is a real defect.
    """
    real_consumer = consumer._consumer  # noqa: SLF001 - no public seam; see docstring above
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        assignment = real_consumer.assignment()
        if assignment:
            print(
                f"[live_kafka readiness] attempt {attempt}: consumer.assignment()={assignment!r}",
                flush=True,
            )
            return
        print(
            f"[live_kafka readiness] attempt {attempt}: consumer.assignment() still empty — retrying",
            flush=True,
        )
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"live_kafka readiness: consumer.assignment() still empty after {timeout_s}s "
        f"({attempt} attempts) despite start() returning and the topic already having a leader — "
        "a genuine defect, not a skip condition."
    )


@pytest.mark.asyncio
async def test_aiokafka_bridge_consumer_consumes_a_real_published_message(
    kafka_bootstrap_servers: str,
) -> None:
    """PENDING KAFKA RUNTIME (skips loudly above when unreachable): publish a real
    `agents.events.contas.completed` message (T4 producer-leg update — this test previously used
    the pre-EB-4-reconciliation `contas.glosa_confirmed` literal, which `notification_bridge.py`
    no longer registers AT ALL post-reconciliation; kept it dormant/misleading rather than a
    genuine live proof) via `AIOKafkaProducer`, consume it via `AioKafkaBridgeConsumer`, and
    dispatch it through `handle_bridge_message` against a spy bridge — proving the REAL consumer
    (not the fake) actually receives and correctly shapes a message a real broker delivered. The
    payload is a SYNTHETIC enriched one (carries `numero_guia_tiss`, the business-key anchor
    today's real minimal `event_payload_vars` does not yet emit — see the EB-4 "arming" follow-up
    note in `notification_bridge.py`), matching
    `tests/unit/platform/integrations/test_notifications_bridge.py`'s `_CONTAS_MESSAGE` fixture.

    GAP CI-KAFKA-HEALTH-WAIT: before any of the above, `_await_topic_ready` + `_await_consumer_
    assigned` (both bounded, both logged, both FAIL rather than skip on genuine broker trouble)
    make the topic/leader/assignment readiness honest, and `seek_to_end()` pins this brand-new
    consumer group's starting position to the log end AS OF THAT MOMENT — strictly BEFORE the
    producer below ever sends — so the strict 15s deadline that follows measures only the publish-
    >consume round trip itself, never any of Kafka's own warm-up latency."""
    from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

    group_id = f"eb3-live-probe-{uuid.uuid4().hex[:8]}"
    business_key_suffix = uuid.uuid4().hex[:8]
    message = {
        "type": "agents.events.contas.completed",
        "tenant_id": "amh",
        "desfecho": "encaminhada_recurso",
        "glosa_id": f"GLOSA-{business_key_suffix}",
        "numero_guia_tiss": f"GUIA-{business_key_suffix}",
    }

    await _await_topic_ready(kafka_bootstrap_servers, NOTIFICATIONS_TOPIC)

    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers,
        topic=NOTIFICATIONS_TOPIC,
        group_id=group_id,
    )
    await consumer.start()
    try:
        await _await_consumer_assigned(consumer)
        # Pin the starting position to the log end AS OF NOW, strictly before the producer below
        # ever sends — see this function's docstring and the test's own docstring.
        real_consumer = consumer._consumer  # noqa: SLF001 - see _await_consumer_assigned
        await real_consumer.seek_to_end()

        producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap_servers)
        await producer.start()
        try:
            await producer.send_and_wait(NOTIFICATIONS_TOPIC, json.dumps(message).encode("utf-8"))
        finally:
            await producer.stop()

        received = await asyncio.wait_for(consumer.__aiter__().__anext__(), timeout=15.0)
        await consumer.commit()

        # CHANGED ASSERTION (GAP-SC-04-a): the consumer now yields a `BridgeMessage` carrying the
        # RAW bytes alongside the parsed value, because a dead-letter shunt cannot quarantine a
        # value it only has in parsed form (see `BridgeMessage`'s docstring). Asserting BOTH is
        # strictly stronger than the old `received == message`: it proves the parse is correct AND
        # that `raw` is the producer's bytes verbatim rather than a re-encoding.
        assert received.value == message
        assert received.raw == json.dumps(message).encode("utf-8")
        assert received.topic == NOTIFICATIONS_TOPIC
        assert received.parse_error == ""

        calls: list[tuple[str, dict[str, Any]]] = []

        async def _spy_starter(process_key: str, variables: dict[str, Any]) -> str:
            calls.append((process_key, variables))
            return f"instance-{process_key}-live-probe"

        bridge = NotificationBridge(cibseven_starter=_spy_starter)
        results = await handle_bridge_message(bridge, received.value)
        expected_business_key = f"RECURSO-amh-GUIA-{business_key_suffix}-GLOSA-{business_key_suffix}"
        assert len(results) == 1
        assert results[0].handoff_triggered is True
        assert calls[0][1]["business_key"] == expected_business_key
    finally:
        await consumer.stop()
