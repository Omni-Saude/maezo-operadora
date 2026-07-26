"""Unit tests for `maezo.platform.integrations.notifications_bridge` (T2.6-EB3 part 5).

TDD London School: `handle_bridge_message`/`run_consumer_loop` are exercised against
`FakeBridgeKafkaConsumer` and a spy `NotificationBridge` starter — no Kafka broker, no CIB
Seven engine. The module's own docstring documents the honest boundary this test file does
NOT (and cannot) cross: `AioKafkaBridgeConsumer` actually talking to a real broker — see
`tests/integration/platform/test_notifications_bridge_live_kafka.py` for that loud skip.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.platform.integrations.notifications_bridge import (
    DEFAULT_CONSUMER_GROUP_ID,
    NOTIFICATIONS_TOPIC,
    FakeBridgeKafkaConsumer,
    MalformedBridgeMessageError,
    NotificationsBridgeSettings,
    _deserialize_json_value,
    build_bridge,
    handle_bridge_message,
    run_consumer_loop,
)
from maezo.platform.notification_bridge import NotificationBridge, NotificationBridgeHandoffFailedError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StarterSpy:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._raises = raises

    async def __call__(self, process_key: str, variables: dict[str, Any]) -> str:
        self.calls.append((process_key, variables))
        if self._raises is not None:
            raise self._raises
        return f"instance-{process_key}-spy"


def _bridge_with_spy() -> tuple[NotificationBridge, _StarterSpy]:
    spy = _StarterSpy()
    return NotificationBridge(cibseven_starter=spy), spy


_CONTAS_MESSAGE = {
    "type": "contas.glosa_confirmed",
    "tenant_id": "amh",
    "decisao_contas": "RECORRER",
    "glosa_id": "GLOSA-1",
    "numero_guia_tiss": "GUIA-1",
}


# ---------------------------------------------------------------------------
# handle_bridge_message — message -> fenced start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_bridge_message_dispatches_matching_rule() -> None:
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_CONTAS_MESSAGE))

    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert spy.calls[0][1]["business_key"] == "RECURSO-amh-GUIA-1-GLOSA-1"


@pytest.mark.asyncio
async def test_handle_bridge_message_no_matching_rule_is_not_an_error() -> None:
    """A well-formed message whose `type` matches no registered rule is a legitimate no-op —
    NOT a malformed message (distinct fail-closed categories)."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, {"type": "some.unregistered.event"})

    assert len(results) == 1
    assert results[0].handoff_triggered is False
    assert len(spy.calls) == 0


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_missing_type() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, {"tenant_id": "amh"})


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_blank_type() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, {"type": "   "})


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_non_mapping() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, ["not", "a", "dict"])


@pytest.mark.asyncio
async def test_handle_bridge_message_propagates_genuine_handoff_failure() -> None:
    """A well-formed, rule-matching message whose starter genuinely fails propagates
    NotificationBridgeHandoffFailedError unchanged (EB-3 part 1) — handle_bridge_message adds
    no extra try/except around on_event."""
    spy = _StarterSpy(raises=RuntimeError("engine unreachable"))
    bridge = NotificationBridge(cibseven_starter=spy)
    with pytest.raises(NotificationBridgeHandoffFailedError):
        await handle_bridge_message(bridge, dict(_CONTAS_MESSAGE))


# ---------------------------------------------------------------------------
# run_consumer_loop — drives FakeBridgeKafkaConsumer, commits only on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_consumer_loop_dispatches_and_commits_each_message() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_CONTAS_MESSAGE), {"type": "no.such.rule"}])
    await consumer.start()

    await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 2
    assert len(spy.calls) == 1  # only the matching message actually started a process


@pytest.mark.asyncio
async def test_run_consumer_loop_fails_closed_on_malformed_message_without_committing() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_CONTAS_MESSAGE), {"no": "type-field"}])
    await consumer.start()

    with pytest.raises(MalformedBridgeMessageError):
        await run_consumer_loop(consumer, bridge)

    # First (well-formed) message dispatched + committed; the malformed second one raised
    # BEFORE any commit for it.
    assert consumer.commits == 1
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_run_consumer_loop_fails_closed_on_genuine_handoff_failure_without_committing() -> None:
    spy = _StarterSpy(raises=RuntimeError("simulated engine failure"))
    bridge = NotificationBridge(cibseven_starter=spy)
    consumer = FakeBridgeKafkaConsumer([dict(_CONTAS_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 0  # never committed a message whose handoff failed to start


@pytest.mark.asyncio
async def test_run_consumer_loop_exhausts_fake_consumer_cleanly_when_all_messages_are_no_ops() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([{"type": "a.b"}, {"type": "c.d"}, {"type": "e.f"}])
    await consumer.start()

    await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 3
    assert len(spy.calls) == 0


# ---------------------------------------------------------------------------
# _deserialize_json_value — the aiokafka value_deserializer hook
# ---------------------------------------------------------------------------


def test_deserialize_json_value_parses_valid_payload() -> None:
    raw = b'{"type": "ans.cron_due", "report_type": "DIOPS"}'
    value = _deserialize_json_value(raw)
    assert value == {"type": "ans.cron_due", "report_type": "DIOPS"}


def test_deserialize_json_value_fails_closed_on_invalid_json() -> None:
    with pytest.raises(MalformedBridgeMessageError):
        _deserialize_json_value(b"{not valid json")


def test_deserialize_json_value_fails_closed_on_undecodable_bytes() -> None:
    with pytest.raises(MalformedBridgeMessageError):
        _deserialize_json_value(b"\xff\xfe\x00\x01")


# ---------------------------------------------------------------------------
# NotificationsBridgeSettings — env-driven config
# ---------------------------------------------------------------------------


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "TENANT_ID",
        "CIBSEVEN_BASE_URL",
        "CIBSEVEN_AUTH_TOKEN",
        "DATABASE_URL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "NOTIFICATIONS_BRIDGE_KAFKA_TOPIC",
        "NOTIFICATIONS_BRIDGE_KAFKA_GROUP_ID",
        "WORKER_CLIENT_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = NotificationsBridgeSettings(_env_file=None)
    assert settings.tenant_id == "amh"
    assert settings.database_url is None
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.kafka_topic == NOTIFICATIONS_TOPIC
    assert settings.kafka_group_id == DEFAULT_CONSUMER_GROUP_ID


def test_settings_reads_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ID", "acme")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("DATABASE_URL", "postgresql://maezo:maezo@localhost:5647/maezo")
    settings = NotificationsBridgeSettings(_env_file=None)
    assert settings.tenant_id == "acme"
    assert settings.kafka_bootstrap_servers == "kafka:9092"
    assert settings.database_url == "postgresql://maezo:maezo@localhost:5647/maezo"


# ---------------------------------------------------------------------------
# build_bridge — fail-closed composition root
# ---------------------------------------------------------------------------


def test_build_bridge_refuses_without_database_url() -> None:
    """FAIL-CLOSED (ADR-0007/T-C2): no DATABASE_URL -> refuse to construct the bridge at all,
    never a fake/no-op audit sink standing in for a real one in production."""
    settings = NotificationsBridgeSettings(_env_file=None, database_url=None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_bridge(settings)


def test_build_bridge_constructs_bridge_with_fenced_starter() -> None:
    settings = NotificationsBridgeSettings(
        _env_file=None,
        database_url="postgresql://maezo:maezo@localhost:5647/maezo",
        tenant_id="amh",
    )
    bridge, transport = build_bridge(settings)
    assert isinstance(bridge, NotificationBridge)
    assert bridge.count_handoffs() == 7
    assert transport is not None
