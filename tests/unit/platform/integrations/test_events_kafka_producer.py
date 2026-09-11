"""Unit tests for `maezo.platform.integrations.events_kafka_producer` (T4 producer-leg).

TDD London School: `AioKafkaEventsProducer` is exercised against `FakeRawKafkaProducer` — no
Kafka broker. Proves the THREE decisions the module docstring documents:
  1. Topic-routing: `MIRROR_TOPICS` also republish onto `NOTIFICATIONS_TOPIC`, typed+scrubbed.
  2. Fail-safe discipline: `BEST_EFFORT_TOPICS` never raise (primary AND mirror leg
     independently); every OTHER topic preserves the pre-existing propagate-on-failure
     behavior (needed for SP-OP-ESCALATION-001's `ERR_EVENT_PUBLISH_FAILED` boundary-catch).
  3. PHI/scrub allowlist backstop applies ONLY to the mirrored envelope, never the primary
     per-domain payload.
  4. GAP-SC-04-a partition-key chokepoint: `publish()` REFUSES a keyless publish (before touching
     the broker) unless the caller declares `unordered=True`, and derives a deterministic,
     PHI-free key from the payload when the caller passes none.

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
from maezo.platform.integrations.partition_key import MissingPartitionKeyError

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
        await producer.publish(_ESCALATION_TOPIC, {"conversation_id": "c1"}, key="bk-esc")

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
        "desfecho": "glosa_aplicada_humano",
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
    assert mirror_value["desfecho"] == "glosa_aplicada_humano"
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
        key="bk-fraude",
    )

    assert len(raw.sent) == 2
    assert raw.sent[1][0] == NOTIFICATIONS_TOPIC
    assert raw.sent[1][1]["type"] == _FRAUDE_TOPIC
    # prestador_id is NOT on the CONTAS/FRAUDE union allowlist explicitly tested above via
    # numero_guia_tiss/glosa_id; confirm it round-trips too (it IS allowlisted).
    assert raw.sent[1][1]["prestador_id"] == "PREST-1"


@pytest.mark.asyncio
async def test_mirror_envelope_type_cannot_be_shadowed_by_a_source_payload_type_key() -> None:
    """R1 F1 (gatekeeper-proven): the old `{"type": topic, **scrub(value)}` spread ordering let a
    source payload carrying its own `type` key OVERRIDE the envelope discriminator (and `type`
    was allowlisted, so the scrub did not stop it) — the gatekeeper injected
    `type="attacker_injected"` and it won. Fixed twice over: `type` is stamped LAST
    (`{**scrub(value), "type": topic}`) so the envelope always wins, AND `type` is no longer on
    `MIRROR_PAYLOAD_ALLOWLIST` so an injected discriminator is scrubbed regardless. This test
    goes RED on the old ordering."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _CONTAS_TOPIC,
        {"type": "attacker_injected", "tenant_id": "amh", "desfecho": "glosa_aplicada_humano"},
        key="bk-shadow",
    )

    assert len(raw.sent) == 2
    mirror_topic, mirror_value, _key = raw.sent[1]
    assert mirror_topic == NOTIFICATIONS_TOPIC
    assert mirror_value["type"] == _CONTAS_TOPIC  # envelope owns the discriminator, always


def test_type_is_not_on_the_mirror_scrub_allowlist() -> None:
    """R1 F1 defense-in-depth: the envelope owns `type` exclusively — a source-payload `type` key
    on a mirrored topic is always foreign (no CONTAS/FRAUDE `ST_Publish*` task sets an
    `event_type` inputParameter) and must be dropped by the scrub, independent of spread order."""
    assert "type" not in MIRROR_PAYLOAD_ALLOWLIST
    assert scrub_mirror_payload({"type": "attacker_injected", "tenant_id": "amh"}) == {"tenant_id": "amh"}


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

    await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh", "desfecho": "sem_glosa"}, key="bk-3")

    assert len(raw.sent) == 1  # only the primary leg landed
    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('mirror send failed')")]


@pytest.mark.asyncio
async def test_publish_direct_notifications_topic_is_not_double_mirrored() -> None:
    """`ans.cron_due` already targets `NOTIFICATIONS_TOPIC` directly in the BPMN literal (gap
    (1), module docstring) — publishing straight to it must be a SINGLE send, best-effort, no
    second mirror hop (it is not in `MIRROR_TOPICS`, only `BEST_EFFORT_TOPICS`)."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        NOTIFICATIONS_TOPIC, {"type": "ans.cron_due", "report_type": "RN_124_SIP"}, key="bk-cron"
    )

    assert len(raw.sent) == 1
    assert raw.sent[0][0] == NOTIFICATIONS_TOPIC


@pytest.mark.asyncio
async def test_publish_direct_notifications_topic_failure_is_swallowed() -> None:
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"}, key="bk-cron")  # no raise

    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('broker down')")]


# ---------------------------------------------------------------------------
# Per-call posture override (t8-escalation-boundary): best_effort=False FORCES propagate on a
# topic that is otherwise topic-default best-effort — the ROOT-CAUSE fix for the escalation/lgpd
# notify swallow. best_effort=None keeps the topic-based default (no existing caller changes).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notifications_topic_best_effort_false_propagates_on_send_failure() -> None:
    """The escalation/lgpd notify posture: publishing to `NOTIFICATIONS_TOPIC` (topic-default
    best-effort) with `best_effort=False` must PROPAGATE a broker-down failure — NOT swallow it —
    so the escalation handler's `ERR_ESC_NOTIFY_FAILED` boundary / lgpd's harness incident fires.
    This goes RED against the pre-fix producer (which had no override and always swallowed)."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(RuntimeError, match="broker down"):
        await producer.publish(
            NOTIFICATIONS_TOPIC,
            {"type": "escalation.notify_team", "severidade": "grave"},
            key="bk-esc",
            best_effort=False,
        )

    assert producer.failed_publishes == []  # forced propagate -> not recorded, just raised


@pytest.mark.asyncio
async def test_notifications_topic_best_effort_none_still_swallows() -> None:
    """`best_effort=None` (default) keeps the topic-based default — `NOTIFICATIONS_TOPIC` stays
    best-effort for callers that do NOT opt in (e.g. `ans.cron_due`, which has no BPMN fallback)."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"}, key="bk-cron", best_effort=None
    )  # no raise

    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('broker down')")]


@pytest.mark.asyncio
async def test_mirror_topic_best_effort_none_still_swallows_primary_and_mirror() -> None:
    """A mirrored topic (contas.completed) with no override is UNCHANGED: both legs best-effort,
    a broker-down failure swallowed on both — a Kafka outage must not stall CONTAS at its
    unconditional, no-gateway `ST_Publish*` step."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _CONTAS_TOPIC, {"tenant_id": "amh", "desfecho": "sem_glosa"}, key="bk-4"
    )  # no raise

    assert producer.failed_publishes == [
        (_CONTAS_TOPIC, "RuntimeError('broker down')"),
        (NOTIFICATIONS_TOPIC, "RuntimeError('broker down')"),
    ]


@pytest.mark.asyncio
async def test_best_effort_false_on_mirror_source_forces_primary_propagate_mirror_stays_swallowed() -> None:
    """Even on a MIRROR source topic, `best_effort=False` forces the PRIMARY leg to propagate; the
    mirror leg is ALWAYS best-effort and is never reached once the primary raises (fail-fast on the
    load-bearing leg). Guards the invariant that the override never weakens the mirror's isolation."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(RuntimeError, match="broker down"):
        await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh"}, key="bk-5", best_effort=False)

    assert producer.failed_publishes == []  # primary forced-propagate; mirror never recorded
    assert raw.sent == []


# ---------------------------------------------------------------------------
# t2-notify-integrity — `publish()` return contract: True = primary delivered; False = primary
# best-effort failure swallowed; mirror-leg outcome never affects the return. This is the
# root-cause fix for the false-success class (events.py's `event_published: True` on a swallowed
# broker-down failure); every pre-existing caller ignores the return, so it is non-breaking.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_true_when_primary_delivered() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    assert await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"}, key="bk-a") is True
    assert await producer.publish(_ESCALATION_TOPIC, {"conversation_id": "c1"}, key="bk-b") is True


@pytest.mark.asyncio
async def test_publish_returns_false_when_best_effort_failure_swallowed() -> None:
    """The swallow is no longer invisible in-band: the caller gets `False` and can report an
    honest `event_published=False` instead of the pre-fix fabricated success."""
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    assert await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"}, key="bk-c") is False
    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('broker down')")]


@pytest.mark.asyncio
async def test_publish_returns_false_on_forced_best_effort_true_failure() -> None:
    raw = FakeRawKafkaProducer()
    raw.always_fail = RuntimeError("broker down")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    assert await producer.publish(_ESCALATION_TOPIC, {"x": 1}, key="bk-d", best_effort=True) is False


@pytest.mark.asyncio
async def test_mirror_leg_failure_does_not_affect_return_value() -> None:
    """Primary delivered + mirror failed -> still `True`: the mirror is log/ledger-only by
    design (no BPMN models a mirror failure), so it must never flip the primary's honest
    delivery signal."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    real_send = raw.send_and_wait

    async def _send_and_wait(topic: str, value: bytes, key: bytes | None = None) -> None:
        if topic == NOTIFICATIONS_TOPIC:
            raise RuntimeError("mirror send failed")
        await real_send(topic, value, key)

    raw.send_and_wait = _send_and_wait  # type: ignore[method-assign]

    assert await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh"}, key="bk-e") is True
    assert producer.failed_publishes == [(NOTIFICATIONS_TOPIC, "RuntimeError('mirror send failed')")]


@pytest.mark.asyncio
async def test_primary_swallowed_failure_returns_false_even_when_mirror_succeeds() -> None:
    """Primary swallowed + mirror delivered -> `False`: the return tracks the PRIMARY leg only
    (the load-bearing publish), never the redundancy leg."""
    raw = FakeRawKafkaProducer()
    raw.fail_next = RuntimeError("broker down for primary")
    producer = AioKafkaEventsProducer(raw_producer=raw)

    assert await producer.publish(_CONTAS_TOPIC, {"tenant_id": "amh"}, key="bk-f") is False
    assert len(raw.sent) == 1
    assert raw.sent[0][0] == NOTIFICATIONS_TOPIC  # mirror landed; primary still honestly False


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
    await producer.publish(_ESCALATION_TOPIC, {"x": 1}, key="bk-g")
    await producer.close()
    assert raw.stopped is True


@pytest.mark.asyncio
async def test_close_never_raises_even_if_stop_fails() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    await producer.publish(_ESCALATION_TOPIC, {"x": 1}, key="bk-h")

    async def _boom() -> None:
        raise RuntimeError("stop failed")

    raw.stop = _boom  # type: ignore[method-assign]
    await producer.close()  # must not raise


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the partition-key chokepoint. `publish()` REFUSES a keyless publish unless the
# caller declares `unordered=True`; a keyless record is assigned round-robin across the topic's
# partitions (registry default 3, `topic_registry.py:75,142`), which loses per-entity ordering the
# moment the notifications-bridge is scaled past one replica.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_fails_closed_without_a_key_or_a_derivable_payload() -> None:
    """THE DEFECT, as a gate: a payload with no business key, no family anchor and no
    process-instance id used to publish UNKEYED and silently. It now raises — before any send."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(MissingPartitionKeyError, match="refusing to publish unkeyed"):
        await producer.publish(_ESCALATION_TOPIC, {"severity": "grave"})

    assert raw.sent == [], "the refusal must happen BEFORE the broker is touched"
    assert producer.failed_publishes == [], "a missing key is bad input, not a publish failure"


@pytest.mark.asyncio
async def test_key_refusal_is_not_swallowed_by_a_best_effort_topic() -> None:
    """`NOTIFICATIONS_TOPIC` is topic-default best-effort — but best-effort covers BROKER faults,
    never bad input. A missing key must raise even here (the raise is outside `_publish_one`)."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(MissingPartitionKeyError):
        await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"})

    assert raw.sent == []


@pytest.mark.asyncio
async def test_key_refusal_is_not_swallowed_by_an_explicit_best_effort_true() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(MissingPartitionKeyError):
        await producer.publish(NOTIFICATIONS_TOPIC, {"type": "ans.cron_due"}, best_effort=True)

    assert raw.sent == []


@pytest.mark.asyncio
async def test_blank_key_is_treated_as_absent_not_as_a_key() -> None:
    """A whitespace key would partition every blank-keyed event onto ONE partition and look like
    a working key. Treated as absent -> derivation -> refusal."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    with pytest.raises(MissingPartitionKeyError):
        await producer.publish(_ESCALATION_TOPIC, {"severity": "grave"}, key="   ")


@pytest.mark.asyncio
async def test_publish_unordered_true_publishes_unkeyed() -> None:
    """The declared escape hatch: `unordered=True` is the caller stating that per-entity ordering
    is meaningless for this stream. No production call site passes it in this build (see
    `resolve_partition_key`'s enumeration) — it exists so the claim must be WRITTEN, not implied
    by a silent `key=None`."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    assert await producer.publish(_ESCALATION_TOPIC, {"severity": "grave"}, unordered=True) is True

    assert len(raw.sent) == 1
    assert raw.sent[0][2] is None


@pytest.mark.asyncio
async def test_publish_derives_the_key_from_the_payload_business_key() -> None:
    """Arm (2): every `operadora.events.publish` payload carries `_business_key`, so a caller that
    passes no key still gets the source instance's own identity on the wire."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _ESCALATION_TOPIC,
        {"_business_key": "ESC-amh-CASO-1", "_process_instance_id": "pi-1", "tenant_id": "amh"},
    )

    assert raw.sent[0][2] == "ESC-amh-CASO-1"


@pytest.mark.asyncio
async def test_publish_derives_the_key_from_family_anchors_when_there_is_no_business_key() -> None:
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _CONTAS_TOPIC,
        {
            "tenant_id": "amh",
            "desfecho": "encaminhada_recurso",
            "numero_guia_tiss": "GUIA-1",
            "glosa_id": "GLOSA-1",
        },
    )

    assert raw.sent[0][2] == "amh|GUIA-1|GLOSA-1"


@pytest.mark.asyncio
async def test_mirror_leg_reuses_the_resolved_key_not_the_callers_raw_key() -> None:
    """The mirror must be keyed by the SAME entity as its primary — a derived primary key and an
    unkeyed mirror would put the bridge's copy back on round-robin, which is the leg that actually
    reaches a scaled consumer."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)

    await producer.publish(
        _CONTAS_TOPIC,
        {
            "tenant_id": "amh",
            "desfecho": "encaminhada_recurso",
            "numero_guia_tiss": "GUIA-1",
            "glosa_id": "GLOSA-1",
        },
    )

    assert len(raw.sent) == 2
    assert raw.sent[0][2] == raw.sent[1][2] == "amh|GUIA-1|GLOSA-1"


@pytest.mark.asyncio
async def test_two_events_about_the_same_entity_publish_under_the_same_key() -> None:
    """The ordering property, proven at the producer seam rather than only at the derivation."""
    raw = FakeRawKafkaProducer()
    producer = AioKafkaEventsProducer(raw_producer=raw)
    base = {"tenant_id": "amh", "numero_caso": "CASO-9"}

    await producer.publish(_FRAUDE_TOPIC, {**base, "fase": "intake"})
    await producer.publish(_FRAUDE_TOPIC, {**base, "desfecho": "encaminhado_credenciamento"})

    primary_keys = [key for topic, _value, key in raw.sent if topic == _FRAUDE_TOPIC]
    assert primary_keys == ["amh|CASO-9", "amh|CASO-9"]


def test_resolve_partition_key_prefers_the_explicit_caller_key() -> None:
    producer = AioKafkaEventsProducer(raw_producer=FakeRawKafkaProducer())
    resolved = producer.resolve_partition_key(
        _CONTAS_TOPIC,
        {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
        key="explicit-bk",
        unordered=False,
    )
    assert resolved == "explicit-bk"


@pytest.mark.asyncio
async def test_configured_factory_is_used_once_and_failed_start_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.gateway.kafka_client import KafkaConnectionSettings
    from maezo.platform.integrations import events_kafka_producer as module

    config = KafkaConnectionSettings("localhost:9092")
    calls: list[object] = []

    class Raw:
        async def start(self) -> None:
            calls.append("start")
            raise RuntimeError("synthetic connect refusal")

        async def stop(self) -> None:
            calls.append("stop")

    async def factory(settings: KafkaConnectionSettings, *, request_timeout_ms: int) -> Raw:
        assert settings is config and request_timeout_ms == 10000
        calls.append("factory")
        return Raw()

    monkeypatch.setattr(module, "create_kafka_producer", factory)
    producer = AioKafkaEventsProducer(connection_settings=config)
    with pytest.raises(RuntimeError, match="synthetic connect refusal"):
        await producer._ensure_started()
    assert calls == ["factory", "start", "stop"]
    assert producer._raw is None and not producer._started


def test_configuration_cannot_be_bypassed_by_raw_override_or_other_brokers() -> None:
    from maezo.gateway.kafka_client import KafkaConnectionSettings

    config = KafkaConnectionSettings("localhost:9092")
    with pytest.raises(ValueError, match="configuration_conflict"):
        AioKafkaEventsProducer(connection_settings=config, raw_producer=FakeRawKafkaProducer())
    with pytest.raises(ValueError, match="configuration_conflict"):
        AioKafkaEventsProducer(connection_settings=config, bootstrap_servers="other:9092")
