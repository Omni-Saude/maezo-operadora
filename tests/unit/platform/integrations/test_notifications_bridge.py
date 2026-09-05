"""Unit tests for `maezo.platform.integrations.notifications_bridge` (T2.6-EB3 part 5).

TDD London School: `handle_bridge_message`/`run_consumer_loop` are exercised against
`FakeBridgeKafkaConsumer` and a spy `NotificationBridge` starter — no Kafka broker, no CIB
Seven engine. The module's own docstring documents the honest boundary this test file does
NOT (and cannot) cross: `AioKafkaBridgeConsumer` actually talking to a real broker — see
`tests/integration/platform/test_notifications_bridge_live_kafka.py` for that loud skip.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord
from maezo.platform.integrations.notifications_bridge import (
    BRIDGE_DLQ_REASONS,
    DEFAULT_CONSUMER_GROUP_ID,
    NOTIFICATIONS_TOPIC,
    REASON_INVALID_JSON,
    REASON_MISSING_TYPE,
    REASON_NOT_A_JSON_OBJECT,
    SLA_ALERT_CANDIDATE_GROUP,
    SLA_ALERT_NOTIFICATION_TYPES,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
    HumanTaskRouted,
    MalformedBridgeMessageError,
    NotificationsBridgeSettings,
    _deserialize_json_value,
    build_bridge,
    handle_bridge_message,
    route_sla_alert_to_human_task,
    run_consumer_loop,
)
from maezo.platform.notification_bridge import NotificationBridge, NotificationBridgeHandoffFailedError
from maezo.tools.workers.harness import FakeAuditSink

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


_INTAKE_RECURSO_MESSAGE = {
    "type": "agents.events.recurso.intake_recebido",  # ADR-0040: the intake event
    "tenant_id": "amh",
    "glosa_id": "GLOSA-1",
    "numero_guia_tiss": "GUIA-1",
}


# ---------------------------------------------------------------------------
# handle_bridge_message — message -> fenced start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_bridge_message_dispatches_matching_rule() -> None:
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))

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
        await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))


# ---------------------------------------------------------------------------
# SLA-risk alert -> human task routing (R-104, WP-ALERTA-SLA-CANAL)
# ---------------------------------------------------------------------------


def test_sla_alert_notification_types_are_the_three_known_publishers() -> None:
    """Documents the allowlist so a future edit to it is a REVIEWED diff, not a silent drift."""
    expected = {"lgpd.notify_sla_risk", "recurso.notify_sla_risk", "programa.notify_sla_risk"}
    assert expected == SLA_ALERT_NOTIFICATION_TYPES


def test_a_known_sla_alert_type_produces_a_human_task_with_the_candidate_group() -> None:
    routed = route_sla_alert_to_human_task(
        "recurso.notify_sla_risk", {"tenant_id": "amh", "glosa_id": "GLOSA-1"}
    )

    assert routed == HumanTaskRouted(
        event_type="recurso.notify_sla_risk",
        candidate_group=SLA_ALERT_CANDIDATE_GROUP,
        tenant_id="amh",
        recognised=True,
    )


@pytest.mark.parametrize("sla_type", sorted(SLA_ALERT_NOTIFICATION_TYPES))
def test_every_known_sla_alert_type_routes_to_a_human_task(sla_type: str) -> None:
    routed = route_sla_alert_to_human_task(sla_type, {"tenant_id": "amh"})

    assert routed is not None
    assert routed.recognised is True
    assert routed.candidate_group == SLA_ALERT_CANDIDATE_GROUP


def test_a_non_sla_type_is_untouched() -> None:
    """A `type` unrelated to SLA-risk alerts (an ordinary process-start rule) is not routed —
    `route_sla_alert_to_human_task` returns `None`, so `handle_bridge_message`'s existing
    `on_event` dispatch is the ONLY thing that runs for it (byte-identical to before this rule
    existed)."""
    assert route_sla_alert_to_human_task("contas.start_fraude", {"tenant_id": "amh"}) is None
    assert route_sla_alert_to_human_task("ans.cron_due", {"tenant_id": "amh"}) is None
    assert route_sla_alert_to_human_task("some.unregistered.event", {}) is None


@pytest.mark.asyncio
async def test_handle_bridge_message_leaves_on_event_dispatch_unchanged_for_a_non_sla_type() -> None:
    """Wiring `route_sla_alert_to_human_task` into `handle_bridge_message` must not change the
    outcome for a message `on_event` already handles — the starter is called the same way, with
    the same variables, as before this rule existed."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))

    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert spy.calls[0][1]["business_key"] == "RECURSO-amh-GUIA-1-GLOSA-1"


@pytest.mark.asyncio
async def test_handle_bridge_message_routes_an_sla_alert_and_still_evaluates_on_event() -> None:
    """An SLA-alert message is BOTH routed to a human task AND still dispatched through
    `on_event` — no existing rule matches an SLA-alert `type`, so this is the same harmless
    'no rule matched' outcome `test_handle_bridge_message_no_matching_rule_is_not_an_error`
    proves, now alongside the new human-task routing side effect."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, {"type": "recurso.notify_sla_risk", "tenant_id": "amh"})

    assert len(results) == 1
    assert results[0].handoff_triggered is False
    assert len(spy.calls) == 0


def test_an_unrecognised_sla_shaped_type_fails_closed_instead_of_dropping_silently() -> None:
    """A `type` that matches every known SLA-risk alert's `<domain>.notify_sla_risk` NAMING SHAPE,
    but whose domain is not (yet) in the allowlist, is NOT silently ignored — it is still routed
    (to the same generic queue) and marked `recognised=False`, so a caller can log/count the gap
    instead of it vanishing as an ordinary unmatched event."""
    routed = route_sla_alert_to_human_task("credenciamento.notify_sla_risk", {"tenant_id": "amh"})

    assert routed == HumanTaskRouted(
        event_type="credenciamento.notify_sla_risk",
        candidate_group=SLA_ALERT_CANDIDATE_GROUP,
        tenant_id="amh",
        recognised=False,
    )


def test_sla_alert_routing_never_raises_even_when_the_metrics_registry_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry must never be able to fail the routing decision (same posture as
    `BridgeDlqShunt._record_metric`)."""
    import maezo.platform.observability as observability_module

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(observability_module, "record_sla_alert_human_task", _boom)

    routed = route_sla_alert_to_human_task("recurso.notify_sla_risk", {"tenant_id": "amh"})

    assert routed is not None
    assert routed.recognised is True


# ---------------------------------------------------------------------------
# run_consumer_loop — drives FakeBridgeKafkaConsumer, commits only on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_consumer_loop_dispatches_and_commits_each_message() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), {"type": "no.such.rule"}])
    await consumer.start()

    await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 2
    assert len(spy.calls) == 1  # only the matching message actually started a process


@pytest.mark.asyncio
async def test_run_consumer_loop_fails_closed_on_malformed_message_without_committing() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), {"no": "type-field"}])
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
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
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
    # 3-tuple since GAP-SC-04-a (changed assertion): the root also returns the `PostgresAuditSink`
    # it constructs, so `build_dlq_shunt` reuses that ONE sink instead of opening a second pool and
    # splitting the daemon's audit chain across two instances.
    bridge, transport, audit_sink = build_bridge(settings)
    assert isinstance(bridge, NotificationBridge)
    assert bridge.count_handoffs() == 7
    assert transport is not None
    assert audit_sink is not None


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the dead-letter / poison-message shunt (audit D5, gateway gvr-d05).
#
# The gap this closes: a single malformed message made `run_consumer_loop` re-raise and `main()`
# exit; the offset was never committed, so the restart re-delivered the SAME message and the
# daemon died again — head-of-line blocking every other message on the shared topic until an
# operator intervened.
#
# The shunt must not weaken fail-closed. These tests pin the four halves of that claim:
#   A. a malformed message is shunted (never dropped) and the loop CONTINUES;
#   B. a TRANSIENT failure still propagates and still blocks the offset (the A3 guarantee);
#   C. a failed DLQ publish OR a failed audit emit restores the original re-raise, no commit;
#   D. with no shunt wired, behaviour is byte-identical to before.
# ---------------------------------------------------------------------------


class _DlqPublisherSpy:
    """In-memory `BridgeDlqPublisher` double. Lives in tests/ (never in the module) — the same
    §8.4 discipline `a2a/outbox_relay.py` follows for its own composition root."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[str, bytes, bytes | None, dict[str, bytes]]] = []
        #: The header SEQUENCE as sent, duplicates intact. `published`'s `dict()` view collapses a
        #: repeated name onto its last value, which is exactly the distinction the forged-header
        #: test has to make (dropped vs merely re-stated later).
        self.header_lists: list[list[tuple[str, bytes]]] = []
        self.started = False
        self.stopped = False
        self._fail_with = fail_with

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def publish_dlq(
        self,
        topic: str,
        *,
        raw: bytes,
        key: bytes | None,
        headers: Any,
    ) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.header_lists.append(list(headers))
        self.published.append((topic, raw, key, dict(headers)))


def _shunt(*, publisher: _DlqPublisherSpy | None = None, sink: FakeAuditSink | None = None) -> BridgeDlqShunt:
    return BridgeDlqShunt(
        publisher=publisher or _DlqPublisherSpy(),
        audit_sink=sink or FakeAuditSink(),
        tenant_id="amh",
    )


_MALFORMED_NO_TYPE = {"tenant_id": "amh", "no": "type-field"}


# --- A. the poison path: shunted, audited, metered, and the loop keeps going -------------------


@pytest.mark.asyncio
async def test_malformed_message_is_shunted_to_the_dlq_and_the_loop_continues() -> None:
    """THE FIX, end to end: a poison message between two good ones no longer stops the loop, and
    every message — including the poison one — advances its offset."""
    bridge, spy = _bridge_with_spy()
    publisher, sink = _DlqPublisherSpy(), FakeAuditSink()
    dlq = _shunt(publisher=publisher, sink=sink)
    consumer = FakeBridgeKafkaConsumer(
        [dict(_INTAKE_RECURSO_MESSAGE), dict(_MALFORMED_NO_TYPE), dict(_INTAKE_RECURSO_MESSAGE)]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert consumer.commits == 3, "the poison message's offset must advance, like the good ones"
    assert len(spy.calls) == 2, "both well-formed messages still started their handoff"
    assert len(publisher.published) == 1
    assert publisher.published[0][0] == "operadora.notifications.internal.dlq"
    assert len(sink.emitted) == 1, "a shunt is never silent — it leaves a durable audit fact"


@pytest.mark.asyncio
async def test_shunted_message_carries_the_raw_bytes_verbatim() -> None:
    """A DLQ that re-encodes the parsed value is not evidence of what the producer sent — and for
    unparseable bytes it could not exist at all."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    raw = b'{"this is": not json'
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage(
                topic=NOTIFICATIONS_TOPIC,
                partition=2,
                offset=41,
                raw=raw,
                key=b"amh|GUIA-1|GLOSA-1",
                parse_error="invalid JSON payload: boom",
                parse_code=REASON_INVALID_JSON,
            )
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    _topic, published_raw, published_key, _headers = publisher.published[0]
    assert published_raw == raw
    assert published_key == b"amh|GUIA-1|GLOSA-1", (
        "the producer's partition key is carried through, so a quarantined record keeps the "
        "per-entity co-location GAP-SC-04-a's key derivation gave it"
    )


@pytest.mark.asyncio
async def test_dlq_headers_are_bounded_diagnostic_tokens_only() -> None:
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=1, offset=7)]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    headers = publisher.published[0][3]
    assert headers["maezo_dlq_reason"] == REASON_MISSING_TYPE.encode()
    assert headers["maezo_dlq_source_topic"] == NOTIFICATIONS_TOPIC.encode()
    assert headers["maezo_dlq_source_partition"] == b"1"
    assert headers["maezo_dlq_source_offset"] == b"7"
    assert headers["maezo_dlq_tenant"] == b"amh"


@pytest.mark.asyncio
async def test_a_producer_forged_dlq_header_is_dropped_rather_than_carried_through() -> None:
    """`maezo_dlq_*` is the bridge's OWN namespace on this path. Kafka header lists admit
    duplicates and which copy a consumer keeps is consumer-dependent, so carrying a producer's
    forged `maezo_dlq_reason` through — even placed before ours — would let a first-wins reader
    attribute the producer's label to the bridge. Dropped; every other producer header survives."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage.from_value(
                dict(_MALFORMED_NO_TYPE),
                partition=1,
                offset=7,
                headers=(
                    ("maezo_dlq_reason", b"forjado_pelo_produtor"),
                    ("maezo_dlq_tenant", b"outro_tenant"),
                    ("traceparent", b"00-abc-def-01"),
                ),
            )
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    sent = publisher.header_lists[0]
    assert [value for name, value in sent if name == "maezo_dlq_reason"] == [REASON_MISSING_TYPE.encode()], (
        "the forged copy is GONE from the list, not merely outranked by ours"
    )
    assert [value for name, value in sent if name == "maezo_dlq_tenant"] == [b"amh"]
    assert ("traceparent", b"00-abc-def-01") in sent, "non-reserved producer headers survive"


@pytest.mark.asyncio
async def test_audit_fact_is_phi_safe_and_content_free_beyond_a_one_way_hash() -> None:
    """The durable chain gets bounded class tokens plus a one-way `raw_sha256` — never the bytes.
    The hash is what makes the row EVIDENCE (an operator can match it to the DLQ record) without
    putting unvalidated, possibly-PHI-bearing content into the chain — the same discipline
    `build_start_audit_record` applies with its own `input_sha256`."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=0, offset=3)
    consumer = FakeBridgeKafkaConsumer([message])
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(sink=sink))

    record, dedup_key = sink.emitted[0]
    assert isinstance(record, AuditRecord)
    assert record.agent_id == "notification_bridge"
    assert record.tenant_id == "amh"
    assert record.decision == "DENY"
    assert record.action == f"bridge_dlq:{NOTIFICATIONS_TOPIC}"
    assert record.details["reason_code"] == REASON_MISSING_TYPE
    assert record.details["raw_sha256"] == hashlib.sha256(message.raw).hexdigest()
    assert "no" not in record.details and "tenant_id" not in record.details
    serialized = repr(record.details)
    assert "type-field" not in serialized, "no byte of the offending payload reaches the chain"
    expected_sha = hashlib.sha256(message.raw).hexdigest()
    assert dedup_key == f"amh:bridge_dlq:{NOTIFICATIONS_TOPIC}:0:3:{expected_sha}"


@pytest.mark.asyncio
async def test_redelivered_poison_message_writes_one_audit_row() -> None:
    """The at-least-once cost of publish-then-audit, bounded: the dedup key pins the record's
    coordinates AND its bytes, so the SAME record redelivered collapses onto one chain row."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    dlq = _shunt(sink=sink)
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=0, offset=9)

    for _attempt in range(3):
        consumer = FakeBridgeKafkaConsumer([message])
        await consumer.start()
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert len(dlq.shunted) == 3, "each redelivery really did re-publish to the DLQ"
    assert len(sink.emitted) == 1, "and they converge on ONE durable audit row"
    assert dlq.deduped_audits == [dlq.dedup_key(message)] * 2, (
        "the two collapsed emits are OBSERVED (`emit_once_status`), not discarded — the whole "
        "point of MAJOR-2: a shunt must never commit on a dedup outcome it cannot see"
    )


@pytest.mark.asyncio
async def test_a_different_payload_at_a_reused_coordinate_writes_its_own_audit_row() -> None:
    """MAJOR-2's root cause, pinned. Broker coordinates are REUSED after a topic recreation
    (`docker compose down -v` in dev/CI; a DR recreate in production) while `audit_emit_dedup`
    survives. Under a coordinates-only dedup key the NEW poison message hit the OLD claim,
    `emit_once` no-op'd (`audit_postgres.py:317-320`), the loop committed the offset and the
    quarantine had NO row in the chain — a silent drop with extra steps.

    Same tenant, same topic, same `(partition, offset)`, DIFFERENT bytes -> TWO chain rows."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    dlq = _shunt(sink=sink)
    first = BridgeMessage.from_value({"tenant_id": "amh", "sem": "tipo-1"}, partition=2, offset=0)
    second = BridgeMessage.from_value({"tenant_id": "amh", "sem": "tipo-2"}, partition=2, offset=0)
    assert (first.topic, first.partition, first.offset) == (second.topic, second.partition, second.offset)
    assert first.raw != second.raw

    for message in (first, second):
        consumer = FakeBridgeKafkaConsumer([message])
        await consumer.start()
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert len(sink.emitted) == 2, "a NEW poison message at a reused coordinate is a NEW fact"
    assert [key for _record, key in sink.emitted] == [dlq.dedup_key(first), dlq.dedup_key(second)]
    assert dlq.deduped_audits == [], "neither emit was a dedup no-op"
    assert {record.details["raw_sha256"] for record, _key in sink.emitted} == {
        hashlib.sha256(first.raw).hexdigest(),
        hashlib.sha256(second.raw).hexdigest(),
    }


def test_dedup_key_folds_the_content_hash_into_the_broker_coordinates() -> None:
    """The shape itself, stated once: `{tenant}:bridge_dlq:{topic}:{partition}:{offset}:{sha256}`,
    with the SAME digest `details["raw_sha256"]` carries — key and evidence name the same bytes."""
    dlq = _shunt()
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=7, offset=13)

    assert dlq.dedup_key(message) == (
        f"amh:bridge_dlq:{NOTIFICATIONS_TOPIC}:7:13:{hashlib.sha256(message.raw).hexdigest()}"
    )


def test_shunt_refuses_a_sink_that_cannot_report_its_dedup_outcome() -> None:
    """FAIL-CLOSED at CONSTRUCTION. An emit-only sink cannot tell "chain row written" from "a prior
    claim already owned this key", so it would silently restore the defect the content-bound dedup
    key closes. Refused where the mistake is made, not on the first poison message."""

    class _EmitOnlySink:
        async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
            return "hash"

    emit_only: Any = _EmitOnlySink()
    with pytest.raises(TypeError, match="emit_once_status"):
        BridgeDlqShunt(publisher=_DlqPublisherSpy(), audit_sink=emit_only, tenant_id="amh")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_reason"),
    [
        ({"tenant_id": "amh"}, REASON_MISSING_TYPE),
        ({"type": "   "}, REASON_MISSING_TYPE),
        (["not", "a", "dict"], REASON_NOT_A_JSON_OBJECT),
    ],
)
async def test_every_malformation_class_reaches_the_dlq_with_its_bounded_reason_code(
    message: Any, expected_reason: str
) -> None:
    bridge, _spy = _bridge_with_spy()
    dlq = _shunt()
    consumer = FakeBridgeKafkaConsumer([message])
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert dlq.shunted == [("operadora.notifications.internal.dlq", expected_reason)]
    assert expected_reason in BRIDGE_DLQ_REASONS


@pytest.mark.asyncio
async def test_undecodable_bytes_reach_the_dlq_rather_than_killing_the_iterator() -> None:
    """Before GAP-SC-04-a a parse failure raised from INSIDE aiokafka's `value_deserializer`, i.e.
    out of the `async for` itself — no `try` the loop owned could see it, and the raw bytes were
    gone. The consumer now parses and records the failure on the message."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage(
                topic=NOTIFICATIONS_TOPIC,
                partition=0,
                offset=1,
                raw=b"\xff\xfe\x00\x01",
                parse_error="invalid JSON payload: codec error",
                parse_code=REASON_INVALID_JSON,
            ),
            dict(_INTAKE_RECURSO_MESSAGE),
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    assert publisher.published[0][1] == b"\xff\xfe\x00\x01"
    assert consumer.commits == 2


# --- B. the fail-closed half the shunt must NOT weaken -----------------------------------------


@pytest.mark.asyncio
async def test_transient_handoff_failure_still_propagates_and_still_blocks_the_offset() -> None:
    """THE LOAD-BEARING NEGATIVE. `NotificationBridgeHandoffFailedError` is a TRANSIENT infra
    fault (engine unreachable), not malformation — retry can clear it, so it must keep blocking
    the offset. If the shunt ever caught bare `Exception`, this goes RED and a real handoff
    failure would be quietly quarantined as if it were poison."""
    spy = _StarterSpy(raises=RuntimeError("engine unreachable"))
    bridge = NotificationBridge(cibseven_starter=spy)
    publisher, sink = _DlqPublisherSpy(), FakeAuditSink()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher, sink=sink))

    assert consumer.commits == 0
    assert publisher.published == [], "a transient failure must never be quarantined as poison"
    assert sink.emitted == []


@pytest.mark.asyncio
async def test_audit_persistence_style_failure_from_the_starter_still_propagates() -> None:
    """The A3 chaos certification's own fault class (`test_a3_bridge_fail_closed.py`: audit sink
    down / dedup table absent -> the fenced starter raises before any engine start). It must reach
    the caller unchanged — the shunt's `except` arm is typed on `MalformedBridgeMessageError` and
    never sees it."""

    class _AuditDownError(RuntimeError):
        pass

    spy = _StarterSpy(raises=_AuditDownError("audit sink down"))
    bridge = NotificationBridge(cibseven_starter=spy)
    dlq = _shunt()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert consumer.commits == 0
    assert dlq.shunted == []


# --- C. the shunt's own failures restore the original fail-closed outcome ----------------------


@pytest.mark.asyncio
async def test_failed_dlq_publish_re_raises_and_does_not_commit() -> None:
    """ "Confirmed before commit" as a test: if the DLQ publish fails, the offset must NOT advance,
    or the message would be lost — a silent drop wearing a DLQ's clothes."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy(fail_with=RuntimeError("dlq broker down"))
    sink = FakeAuditSink()
    consumer = FakeBridgeKafkaConsumer([dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(RuntimeError, match="dlq broker down"):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher, sink=sink))

    assert consumer.commits == 0
    assert sink.emitted == [], "no audit fact may assert a shunt that did not happen"


@pytest.mark.asyncio
async def test_failed_audit_emit_re_raises_and_does_not_commit() -> None:
    """The audit fact is as load-bearing as the publish: a quarantine nobody recorded is a drop
    the platform cannot account for later."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    sink.always_fail = RuntimeError("audit sink down")
    consumer = FakeBridgeKafkaConsumer([dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(RuntimeError, match="audit sink down"):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(sink=sink))

    assert consumer.commits == 0


@pytest.mark.asyncio
async def test_invalid_source_topic_fails_closed_rather_than_minting_a_dlq_name() -> None:
    """`dlq_topic_for` validates the SOURCE topic through the registry's convention on the hot
    path. A malformed topic raises here — no commit — instead of quarantining into a
    valid-looking name nobody registered."""
    from maezo.platform.topic_registry import TopicValidationError

    bridge, _spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer(
        [BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), topic="Not.A.Valid.Topic")]
    )
    await consumer.start()

    with pytest.raises(TopicValidationError):
        await run_consumer_loop(consumer, bridge, dlq=_shunt())

    assert consumer.commits == 0


# --- D. no shunt wired -> the pre-GAP-SC-04-a behaviour, byte for byte -------------------------


@pytest.mark.asyncio
async def test_without_a_shunt_a_malformed_message_still_re_raises_and_does_not_commit() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(MalformedBridgeMessageError):
        await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 1
    assert len(spy.calls) == 1


# --- BridgeMessage ------------------------------------------------------------------------------


def test_bridge_message_from_value_encodes_its_own_raw_bytes() -> None:
    import json

    message = BridgeMessage.from_value({"type": "ans.cron_due"})
    assert message.raw == json.dumps({"type": "ans.cron_due"}).encode("utf-8")
    assert message.value == {"type": "ans.cron_due"}
    assert message.parse_error == ""


def test_bridge_message_from_value_refuses_a_non_serializable_value() -> None:
    """No `repr()`-shaped `raw` invented for something that would not round-trip."""
    with pytest.raises(TypeError):
        BridgeMessage.from_value(object())


def test_malformed_error_codes_are_all_in_the_closed_vocabulary() -> None:
    """The metric label's cardinality bound, pinned: every code the module can raise is declared,
    so no unbounded parser text can reach `maezo_bridge_dlq_total{reason}`."""
    with pytest.raises(MalformedBridgeMessageError) as not_object:
        raise MalformedBridgeMessageError("x", [], code=REASON_NOT_A_JSON_OBJECT)
    assert not_object.value.code in BRIDGE_DLQ_REASONS

    with pytest.raises(MalformedBridgeMessageError) as bad_json:
        _deserialize_json_value(b"{nope")
    assert bad_json.value.code == REASON_INVALID_JSON
    assert {REASON_INVALID_JSON, REASON_NOT_A_JSON_OBJECT, REASON_MISSING_TYPE} == BRIDGE_DLQ_REASONS
