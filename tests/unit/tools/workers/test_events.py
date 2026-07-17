"""Unit tests for `maezo.tools.workers.events` — `operadora.events.publish` (T3.1 R2).

No engine — every test drives `make_publish_event_handler`/`register_events_workers` directly
against `ExternalTask` + `FakeKafkaPublisher` (or `kafka=None`). The real-engine acceptance tests
live under `tests/integration/processes/test_sp_op_{escalation,auth,cancel}_001.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.events import (
    _ANS_CRON_REFERENCE_DATE_KEY,
    _ESCALATION_PUBLISH_BPMN_ERROR_TOPICS,
    _ESCALATION_REQUESTED_TOPIC,
    _parse_payload_vars,
    make_publish_event_handler,
    register_events_workers,
)
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "task-1",
    topic: str = "operadora.events.publish",
    business_key: str = "BK-teste-001",
    process_instance_id: str = "proc-1",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id=process_instance_id,
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


# ---------------------------------------------------------------------------
# _parse_payload_vars
# ---------------------------------------------------------------------------


def test_parse_payload_vars_none() -> None:
    assert _parse_payload_vars(None) == []


def test_parse_payload_vars_comma_string() -> None:
    assert _parse_payload_vars("tenant_id,conversation_id, severidade") == [
        "tenant_id",
        "conversation_id",
        "severidade",
    ]


def test_parse_payload_vars_list() -> None:
    assert _parse_payload_vars(["tenant_id", "conversation_id"]) == ["tenant_id", "conversation_id"]


def test_parse_payload_vars_empty_string() -> None:
    assert _parse_payload_vars("") == []


# ---------------------------------------------------------------------------
# Missing event_topic -> WorkerBpmnError (fail-closed, never silently drops)
# ---------------------------------------------------------------------------


async def test_missing_event_topic_raises_bpmn_error() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_task(variables={}))
    assert exc.value.error_code == "ERR_PUBLISH_MISSING_TOPIC"
    assert not kafka.published


# ---------------------------------------------------------------------------
# Payload construction (business_key/process_instance_id/topic + payload_vars +
# event_fase/event_desfecho/event_tipo_mudanca/event_type)
# ---------------------------------------------------------------------------


async def test_publishes_with_task_metadata_and_payload_vars() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(
        business_key="ESC-amh-conv-1",
        process_instance_id="proc-42",
        variables={
            "event_topic": "agents.events.escalation.requested",
            "event_payload_vars": "tenant_id,conversation_id,severidade",
            "tenant_id": "amh",
            "conversation_id": "conv-1",
            "severidade": "grave",
            "unrelated_var": "should not leak",
        },
    )
    result = await handler(task)

    assert result == {"event_published": True, "event_topic": "agents.events.escalation.requested"}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "agents.events.escalation.requested"
    assert key == "ESC-amh-conv-1"
    assert payload["_business_key"] == "ESC-amh-conv-1"
    assert payload["_process_instance_id"] == "proc-42"
    assert payload["_worker_topic"] == "operadora.events.publish"
    assert payload["tenant_id"] == "amh"
    assert payload["conversation_id"] == "conv-1"
    assert payload["severidade"] == "grave"
    assert "unrelated_var" not in payload


async def test_event_fase_becomes_payload_fase() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "x", "event_fase": "ack"})
    await handler(task)
    assert kafka.published[0][1]["fase"] == "ack"


async def test_event_desfecho_becomes_payload_desfecho() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "x", "event_desfecho": "aprovada_automatica"})
    await handler(task)
    assert kafka.published[0][1]["desfecho"] == "aprovada_automatica"


async def test_event_tipo_mudanca_becomes_payload_tipo_mudanca() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "x", "event_tipo_mudanca": "prestador_descredenciado"})
    await handler(task)
    assert kafka.published[0][1]["tipo_mudanca"] == "prestador_descredenciado"


async def test_event_type_becomes_payload_type() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "operadora.notifications.internal", "event_type": "custom.type"})
    await handler(task)
    assert kafka.published[0][1]["type"] == "custom.type"


async def test_payload_vars_missing_from_variables_are_skipped() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "x", "event_payload_vars": "present,absent", "present": "ok"})
    await handler(task)
    payload = kafka.published[0][1]
    assert payload["present"] == "ok"
    assert "absent" not in payload


# ---------------------------------------------------------------------------
# ans.cron_due anchor (GAP-ANS-1/GAP-ANS-3)
# ---------------------------------------------------------------------------


async def test_ans_cron_due_stamps_reference_date() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "operadora.notifications.internal", "event_type": "ans.cron_due"})
    await handler(task)
    payload = kafka.published[0][1]
    assert _ANS_CRON_REFERENCE_DATE_KEY in payload
    assert len(payload[_ANS_CRON_REFERENCE_DATE_KEY]) == 10  # YYYY-MM-DD


async def test_non_ans_cron_due_does_not_stamp_reference_date() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka)
    task = _task(variables={"event_topic": "x", "event_type": "other.type"})
    await handler(task)
    assert _ANS_CRON_REFERENCE_DATE_KEY not in kafka.published[0][1]


# ---------------------------------------------------------------------------
# kafka=None — fail-closed, never fabricates success (module docstring decision)
# ---------------------------------------------------------------------------


async def test_kafka_none_completes_with_explicit_false_marker() -> None:
    handler = make_publish_event_handler(None)
    task = _task(variables={"event_topic": "agents.events.escalation.requested"})
    result = await handler(task)
    assert result == {"event_published": False, "event_topic": "agents.events.escalation.requested"}


async def test_kafka_none_never_raises() -> None:
    handler = make_publish_event_handler(None)
    task = _task(
        variables={
            "event_topic": "agents.events.escalation.sla_breached",
            "event_payload_vars": "tenant_id",
            "tenant_id": "amh",
        }
    )
    # Must not raise (would trade "unregistered topic" incident for "crashing handler" incident).
    result = await handler(task)
    assert result["event_published"] is False


# ---------------------------------------------------------------------------
# Publish failure — propagates raw exception UNLESS topic opts into bpmn_error_topics (GAP-ESC-5)
# ---------------------------------------------------------------------------


class _FailingPublisher:
    async def publish(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None:
        del topic, value, key
        raise RuntimeError("kafka unavailable (test)")


async def test_publish_failure_on_non_opt_in_topic_propagates_raw_exception() -> None:
    handler = make_publish_event_handler(_FailingPublisher())
    task = _task(variables={"event_topic": "agents.events.auth.completed"})
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)


async def test_publish_failure_on_opt_in_topic_raises_bpmn_error() -> None:
    handler = make_publish_event_handler(
        _FailingPublisher(), bpmn_error_topics=_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS
    )
    task = _task(variables={"event_topic": _ESCALATION_REQUESTED_TOPIC})
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(task)
    assert exc.value.error_code == "ERR_EVENT_PUBLISH_FAILED"


async def test_publish_failure_on_opt_in_topic_without_allowlist_still_propagates_raw() -> None:
    """Opt-in is per-CALL (`bpmn_error_topics=...`), not automatic — a caller (e.g. a future
    non-escalation registrar) that does not pass the escalation set gets the pre-existing raw
    propagation for every topic, zero behavior change."""
    handler = make_publish_event_handler(_FailingPublisher())  # no bpmn_error_topics opt-in
    task = _task(variables={"event_topic": _ESCALATION_REQUESTED_TOPIC})
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)


# ---------------------------------------------------------------------------
# register_events_workers — raw handler registration (NOT FunctionWorker/register_worker)
# ---------------------------------------------------------------------------


def test_register_events_workers_registers_raw_handler() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_events_workers(harness, FakeKafkaPublisher())

    assert "operadora.events.publish" in harness.registered_topics
    # Raw `harness.register()` path — never added to the WorkerRegistry (register_worker only).
    assert harness.registry.get("operadora.events.publish") is None


def test_register_events_workers_defaults_kafka_to_none() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_events_workers(harness)  # kafka omitted — must not raise
    assert "operadora.events.publish" in harness.registered_topics


def test_register_events_workers_accepts_and_ignores_seams() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_events_workers(harness, FakeKafkaPublisher(), dmn=object(), audit=object())
    assert "operadora.events.publish" in harness.registered_topics


async def test_register_events_workers_end_to_end_via_harness_handle() -> None:
    """Dispatch through the SAME `harness._handle` the production loop uses (design §16.1 —
    preserved fixture surface) — proves the wiring, not just the bare handler function."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    kafka = FakeKafkaPublisher()
    register_events_workers(harness, kafka)

    transport = harness._transport  # type: ignore[attr-defined]  # FakeWorkerTransport, test-only
    transport.enqueue(
        _task(
            variables={
                "event_topic": "agents.events.escalation.requested",
                "event_payload_vars": "tenant_id",
                "tenant_id": "amh",
            }
        )
    )
    tasks = await transport.fetch_and_lock(
        "probe",
        harness._topic_subscriptions(),  # type: ignore[attr-defined]
        max_tasks=10,
        async_response_timeout_ms=100,
    )
    assert len(tasks) == 1
    await harness._handle(tasks[0])  # noqa: SLF001 — preserved v1 fixture surface (design §16.1)

    assert len(kafka.published) == 1
    assert transport.completed
    completed_task_id, completed_vars = transport.completed[0]
    assert completed_task_id == "task-1"
    assert completed_vars == {"event_published": True, "event_topic": "agents.events.escalation.requested"}
