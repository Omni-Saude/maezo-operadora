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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from typing import Any

import pytest

from maezo.platform.integrations.notifications_bridge import (
    NOTIFICATIONS_TOPIC,
    AioKafkaBridgeConsumer,
    handle_bridge_message,
)
from maezo.platform.notification_bridge import NotificationBridge

_CONNECT_TIMEOUT_S = 5.0


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


@pytest.mark.asyncio
async def test_aiokafka_bridge_consumer_consumes_a_real_published_message(
    kafka_bootstrap_servers: str,
) -> None:
    """PENDING KAFKA RUNTIME (skips loudly above when unreachable): publish a real
    `contas.glosa_confirmed` message via `AIOKafkaProducer`, consume it via
    `AioKafkaBridgeConsumer`, and dispatch it through `handle_bridge_message` against a spy
    bridge — proving the REAL consumer (not the fake) actually receives and correctly shapes a
    message a real broker delivered."""
    from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

    group_id = f"eb3-live-probe-{uuid.uuid4().hex[:8]}"
    business_key_suffix = uuid.uuid4().hex[:8]
    message = {
        "type": "contas.glosa_confirmed",
        "tenant_id": "amh",
        "decisao_contas": "RECORRER",
        "glosa_id": f"GLOSA-{business_key_suffix}",
        "numero_guia_tiss": f"GUIA-{business_key_suffix}",
    }

    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=kafka_bootstrap_servers,
        topic=NOTIFICATIONS_TOPIC,
        group_id=group_id,
    )
    await consumer.start()
    try:
        producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap_servers)
        await producer.start()
        try:
            await producer.send_and_wait(NOTIFICATIONS_TOPIC, json.dumps(message).encode("utf-8"))
        finally:
            await producer.stop()

        received = await asyncio.wait_for(consumer.__aiter__().__anext__(), timeout=15.0)
        await consumer.commit()

        assert received == message

        calls: list[tuple[str, dict[str, Any]]] = []

        async def _spy_starter(process_key: str, variables: dict[str, Any]) -> str:
            calls.append((process_key, variables))
            return f"instance-{process_key}-live-probe"

        bridge = NotificationBridge(cibseven_starter=_spy_starter)
        results = await handle_bridge_message(bridge, received)
        expected_business_key = f"RECURSO-amh-GUIA-{business_key_suffix}-GLOSA-{business_key_suffix}"
        assert len(results) == 1
        assert results[0].handoff_triggered is True
        assert calls[0][1]["business_key"] == expected_business_key
    finally:
        await consumer.stop()
