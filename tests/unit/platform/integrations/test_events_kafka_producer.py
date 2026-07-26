"""Unit tests for `maezo.platform.integrations.events_kafka_producer` (T4 producer-leg).

TDD London School: `AioKafkaEventsProducer` is exercised against `FakeRawKafkaProducer` — no
Kafka broker. Proves the THREE decisions the module docstring documents:
  1. Topic-routing: `MIRROR_TOPICS` also republish onto `NOTIFICATIONS_TOPIC`, typed+scrubbed.
  2. Fail-safe discipline: `BEST_EFFORT_TOPICS` never raise (primary AND mirror leg
     independently); every OTHER topic preserves the pre-existing propagate-on-failure
     behavior (needed for SP-OP-ESCALATION-001's `ERR_EVENT_PUBLISH_FAILED` boundary-catch).
  3. PHI/scrub allowlist backstop applies ONLY to the mirrored envelope, never the primary
     per-domain payload.

See `tests/integration/platform/test_events_kafka_producer_live.py` for the real-broker proof
(topic-landing, consumer round-trip, bridge dispatch, live-PG audit, dedup, kafka-down isolation).
"""

from __future__ import annotations

import pytest

from maezo.platform.integrations.events_kafka_producer import (
    BEST_EFFORT_TOPICS,
    MIRROR_PAYLOAD_ALLOWLIST,
    MIRROR_TOPICS,
    NOTIFICATIONS_TOPIC,
    AioKafkaEventsProducer,
    FakeRawKafkaProducer,
    scrub_mirror_payload,
)

_CONTAS_TOPIC = "agents.events.contas.completed"
_FRAUDE_TOPIC = "agents.events.fraude.completed"
_ESCALATION_TOPIC = "agents.events.escalation.requested"  # NOT a mirror/best-effort topic


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_bootstrap_servers_or_raw_producer() -> None:
    with pytest.raises(ValueError, match="requires either bootstrap_servers"):
        AioKafkaEventsProducer()


def test_accepts_raw_producer_without_bootstrap_servers() -> None:
    AioKafkaEventsProducer(raw_producer=FakeRawKafkaProducer())  # does not raise


# ---------------------------------------------------------------------------
# Routing table sanity
# ---------------------------------------------------------------------------


def test_mirror_topics_are_a_subset_of_best_effort_topics() -> None:
    assert MIRROR_TOPICS <= BEST_EFFORT_TOPICS


def test_notifications_topic_is_best_effort_but_not_a_mirror_source() -> None:
    assert NOTIFICATIONS_TOPIC in BEST_EFFORT_TOPICS
    assert NOTIFICATIONS_TOPIC not in MIRROR_TOPICS


def test_escalation_topic_is_neither_mirrored_nor_best_effort() -> None:
    """SP-OP-ESCALATION-001's own `ERR_EVENT_PUBLISH_FAILED` boundary-catch machinery needs a
    publish failure to PROPAGATE — it must stay outside both routing sets."""
    assert _ESCALATION_TOPIC not in MIRROR_TOPICS
    assert _ESCALATION_TOPIC not in BEST_EFFORT_TOPICS


# ---------------------------------------------------------------------------
# Non-mirror, non-best-effort topic (escalation-shaped): unchanged propagate-on-failure behavior.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_non_mirror_topic_sends_once_no_mirror() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(_ESCALATION_TOPIC, {"conversation_id": "c1"}, key="c1")

    assert len(raw.sent) == 1
    assert raw.sent[0] == (_ESCALATION_TOPIC, {"conversation_id": "c1"}, "c1")


@pytest.mark.asyncio
async def test_publish_non_best_effort_topic_failure_propagates() -> None:
    """A send failure on a topic OUTSIDE `BEST_EFFORT_TOPICS` must still raise — this is what
    lets `events.py`'s `except Exception` -> `WorkerBpmnError(ERR_EVENT_PUBLISH_FAILED)` keep
    firing for SP-OP-ESCALATION-001's boundary-carrying topics."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(RuntimeError, match="broker down"):
        await producer.publish(_ESCALATION_TOPIC, {"conversation_id": "c1"})

    assert producer.failed_publishes == []  # not best-effort -> not recorded, just raised


# ---------------------------------------------------------------------------
# Mirror topics (CONTAS/FRAUDE completed): dual-publish + scrub + best-effort.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_mirror_topic_sends_primary_and_scrubbed_mirror() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    payload = {
        "tenant_id": "amh",
        "desfecho": "encaminhada_recurso",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "numero_lote_tiss": "LOTE-1",
        "_business_key": "irrelevant-marker",
        "_worker_topic": "operadora.events.publish",
        # A field NOT on the allowlist — must survive on the PRIMARY topic (untouched) but be
        # dropped from the MIRRORED envelope only.
        "not_allowlisted_field": "some-internal-marker-value",
    }

    await producer.publish(_CONTAS_TOPIC, payload, key="bk-1")

    assert len(raw.sent) == 2
    primary_topic, primary_value, primary_key = raw.sent[0]
    assert primary_topic == _CONTAS_TOPIC
    assert primary_key == "bk-1"
    assert primary_value == payload  # primary leg is UNTOUCHED — no scrub applied here

    mirror_topic, mirror_value, mirror_key = raw.sent[1]
    assert mirror_topic == NOTIFICATIONS_TOPIC
    assert mirror_key == "bk-1"
    assert mirror_value["type"] == _CONTAS_TOPIC
    assert mirror_value["tenant_id"] == "amh"
    assert mirror_value["desfecho"] == "encaminhada_recurso"
    assert mirror_value["numero_guia_tiss"] == "GUIA-1"
    assert mirror_value["glosa_id"] == "GLOSA-1"
    assert "not_allowlisted_field" not in mirror_value


@pytest.mark.asyncio
async def test_publish_fraude_completed_is_also_mirrored() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _FRAUDE_TOPIC,
        {"tenant_id": "amh", "desfecho": "encaminhado_credenciamento", "prestador_id": "PREST-1"},
    )

    assert len(raw.sent) == 2
    assert raw.sent[1][0] == NOTIFICATIONS_TOPIC
    assert raw.sent[1][1]["type"] == _FRAUDE_TOPIC
    # prestador_id is NOT on the CONTAS/FRAUDE union allowlist explicitly tested above via
    # numero_guia_tiss/glosa_id; confirm it round-trips too (it IS allowlisted).
    assert raw.sent[1][1]["prestador_id"] == "PREST-1"


@pytest.mark.asyncio
async def test_publish_mirror_topic_primary_failure_is_swallowed_and_mirror_still_attempted() -> None:
    """Fail-safe discipline (module docstring): a Kafka-down primary-publish failure on a
    best-effort topic must NOT raise (the source BPMN process must still complete) — and the
    independent mirror leg is still attempted."""
    raw = FakeRawKafkaProducer()
    raw.fail_next = RuntimeError("broker down for primary")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh", "desfecho": "sem_glosa"}, key="bk-2")

    assert producer.failed_publishes == [(_CONTAS_TOPIC, "RuntimeError('broker down for primary')")]
    # Primary failed (fail_next consumed), but the mirror leg's OWN send should have gone through.
    assert len(raw.sent) == 1
    assert raw.sent[0][0] == NOTIFICATIONS_TOPIC


@pytest.mark.asyncio
async def test_publish_mirror_topic_mirror_leg_failure_is_swallowed() -> None:
    """The mirror leg is unconditionally best-effort — even if the caller's primary topic were
    (hypothetically) not itself best-effort, a mirror-only failure must never raise, since no
    BPMN boundary event anywhere expects a 'mirror publish failed' error."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    calls = {"n": 0}
    real_send = raw.send_and_wait

    async def _send_and_wait(topic: str, value: bytes, key: bytes | None = None) -> None:
        calls["n"] += 1
        if topic == NOTIFICATIONS_TOPIC:
            raise RuntimeError("mirror send failed")
        await real_send(topic, value, key)

    raw.send_and_wait = _send_and_wait  # type: ignore[method-assign]

    await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh", "desfecho": "sem_glosa"})

    assert len(raw.sent) == 1  # only the primary leg landed
    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('mirror send failed')")]


@pytest.mark.asyncio
async def test_publish_direct_notifications_topic_is_not_double_mirrored() -> None:
    """`ans.cron_due` already targets `NOTIFICATIONS_TOPIC` directly in the BPMN literal (gap
    (1), module docstring) — publishing straight to it must be a SINGLE send, best-effort, no
    second mirror hop (it is not in `MIRROR_TOPICS`, only `BEST_EFFORT_TOPICS`)."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due", "report_type": "RN_124_SIP"})

    assert len(raw.sent) == 1
    assert raw.sent[0][0] == NOTIFICATIONS_TOPIC


@pytest.mark.asyncio
async def test_publish_direct_notifications_topic_failure_is_swallowed() -> None:
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"})  # must not raise

    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('broker down')")]


# ---------------------------------------------------------------------------
# scrub_mirror_payload
# ---------------------------------------------------------------------------


def test_scrub_drops_non_allowlisted_keys() -> None:
    scrubbed = scrub_mirror_payload(
        {
            "tenant_id": "amh",
            "desfecho": "sem_glosa",
            "justificativa_clinica": "texto livre nunca deveria estar aqui",
            "arbitrary_internal_marker": 123,
        }
    )
    assert scrubbed == {"tenant_id": "amh", "desfecho": "sem_glosa"}


def test_scrub_keeps_every_allowlisted_key_present() -> None:
    payload = {k: f"v-{k}" for k in MIRROR_PAYLOAD_ALLOWLIST}
    assert scrub_mirror_payload(payload) == payload


def test_scrub_empty_payload_is_empty() -> None:
    assert scrub_mirror_payload({}) == {}


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_before_start_is_a_noop() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    await producer.close()
    assert raw.stopped is False


@pytest.mark.asyncio
async def test_close_after_publish_stops_the_raw_producer() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    await producer.publish(_ESCALATION_TOPIC, {"x": 1})
    await producer.close()
    assert raw.stopped is True


@pytest.mark.asyncio
async def test_close_never_raises_even_if_stop_fails() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    await producer.publish(_ESCALATION_TOPIC, {"x": 1})

    async def _boom() -> None:
        raise RuntimeError("stop failed")

    raw.stop = _boom  # type: ignore[method-assign]
    await producer.close()  # must not raise
