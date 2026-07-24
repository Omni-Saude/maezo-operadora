"""Unit tests for `maezo.tools.workers.lgpd`'s `make_notify_sla_risk_handler` (#55 R-G, T2.8).

No engine — every test drives `make_notify_sla_risk_handler`/`register_lgpd_workers` directly
against `ExternalTask` + `FakeKafkaPublisher` (or `kafka=None`), mirroring `test_events.py`'s
pattern for `make_publish_event_handler`. The real-engine acceptance tests
(`test_alerta_interno_nao_interruptivo`/`test_sla_global_15_dias_event_subprocess` in
`tests/integration/processes/test_sp_op_lgpd_dsr_001.py`) remain strict-xfail pending a live CIB
Seven engine run (`lgpd_probe` still wires `_gap_topic_stub` for this topic there) — this batch is
mechanical/engine-less only.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    _NOTIFICATIONS_TOPIC,
    _NOTIFY_SLA_RISK_NOTIFICATION_TYPE,
    _NOTIFY_SLA_RISK_TOPIC,
    make_notify_sla_risk_handler,
    register_lgpd_workers,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "task-1",
    business_key: str = "DSR-amh-PSEUDO-001-confirmacao_acesso-2026-06-12",
    process_instance_id: str = "proc-1",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=_NOTIFY_SLA_RISK_TOPIC,
        process_instance_id=process_instance_id,
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


# ---------------------------------------------------------------------------
# Discriminator — ack phase (ST_NotificarRiscoSla, P7D internal alert, bpmn:217-232)
# ---------------------------------------------------------------------------


async def test_notify_sla_risk_ack_phase_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        variables={
            "tenant_id": "amh",
            "titular_pseudo_id": "PSEUDO-001",
            "tipo_requisicao": "confirmacao_acesso",
            "sla_breach_task_name": "UT_RevisaoDpo",
            "sla_breach_phase": "ack",
        }
    )

    result = await handler(task)

    assert result == {}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert key == task.business_key
    assert payload["type"] == _NOTIFY_SLA_RISK_NOTIFICATION_TYPE
    assert payload["tenant_id"] == "amh"
    assert payload["titular_pseudo_id"] == "PSEUDO-001"
    assert payload["sla_breach_task_name"] == "UT_RevisaoDpo"
    assert payload["sla_breach_phase"] == "ack"


# ---------------------------------------------------------------------------
# Discriminator — resolution phase (ST_NotificarJuridicoBreach, P15D legal breach, bpmn:325-337)
# ---------------------------------------------------------------------------


async def test_notify_sla_risk_resolution_phase_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        variables={
            "tenant_id": "amh",
            "titular_pseudo_id": "PSEUDO-002",
            "tipo_requisicao": "eliminacao",
            "sla_breach_task_name": "UT_RevisaoDpo",
            "sla_breach_phase": "resolution",
        }
    )

    result = await handler(task)

    assert result == {}
    payload = kafka.published[0][1]
    assert payload["sla_breach_phase"] == "resolution"
    assert payload["sla_breach_task_name"] == "UT_RevisaoDpo"
    assert payload["titular_pseudo_id"] == "PSEUDO-002"


async def test_notify_sla_risk_ack_and_resolution_are_distinguishable() -> None:
    """The ONE topic serves BOTH service tasks — `sla_breach_phase` must be the sole, faithfully
    echoed discriminator (ExternalTask exposes no activityId, module docstring)."""
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)

    await handler(_task(variables={"sla_breach_task_name": "UT_RevisaoDpo", "sla_breach_phase": "ack"}))
    await handler(
        _task(variables={"sla_breach_task_name": "UT_RevisaoDpo", "sla_breach_phase": "resolution"})
    )

    phases = [payload["sla_breach_phase"] for _t, payload, _k in kafka.published]
    assert phases == ["ack", "resolution"]


# ---------------------------------------------------------------------------
# kafka=None — never blocks, never raises (notify-only, fail-safe)
# ---------------------------------------------------------------------------


async def test_notify_sla_risk_kafka_none_completes_without_publishing() -> None:
    handler = make_notify_sla_risk_handler(None)
    task = _task(variables={"sla_breach_task_name": "UT_RevisaoDpo", "sla_breach_phase": "ack"})

    result = await handler(task)

    assert result == {}


async def test_notify_sla_risk_kafka_none_never_raises_even_with_missing_vars() -> None:
    handler = make_notify_sla_risk_handler(None)
    task = _task(variables={})  # no phase/task_name/tenant_id at all

    # Must not raise (would trade "unregistered topic" incident for "crashing handler" incident).
    result = await handler(task)

    assert result == {}


# ---------------------------------------------------------------------------
# Missing input — defensive defaults, never crashes the dispatch worker
# ---------------------------------------------------------------------------


async def test_notify_sla_risk_missing_phase_defaults_to_empty_string() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(variables={"tenant_id": "amh"})  # no sla_breach_phase/sla_breach_task_name

    result = await handler(task)

    assert result == {}
    payload = kafka.published[0][1]
    assert payload["sla_breach_phase"] == ""
    assert payload["sla_breach_task_name"] == ""


# ---------------------------------------------------------------------------
# Fail-safe on publish failure — propagates (never silently swallowed), mirrors
# `make_publish_event_handler`'s non-opt-in path (no BPMN error boundary is declared on either
# ST_NotificarRiscoSla or ST_NotificarJuridicoBreach).
# ---------------------------------------------------------------------------


class _FailingPublisher:
    async def publish(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None:
        del topic, value, key
        raise RuntimeError("kafka unavailable (test)")


async def test_notify_sla_risk_publish_failure_propagates_raw_exception() -> None:
    handler = make_notify_sla_risk_handler(_FailingPublisher())
    task = _task(variables={"sla_breach_task_name": "UT_RevisaoDpo", "sla_breach_phase": "ack"})

    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)


# ---------------------------------------------------------------------------
# register_lgpd_workers wiring — raw handler, NOT in the WorkerRegistry
# ---------------------------------------------------------------------------


def test_register_lgpd_workers_registers_notify_sla_risk_as_raw_handler() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_lgpd_workers(harness, FakeKafkaPublisher())

    assert _NOTIFY_SLA_RISK_TOPIC in harness.registered_topics
    assert harness.registry.get(_NOTIFY_SLA_RISK_TOPIC) is None


def test_register_lgpd_workers_notify_sla_risk_defaults_kafka_to_none() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_lgpd_workers(harness)  # kafka omitted — must not raise

    assert _NOTIFY_SLA_RISK_TOPIC in harness.registered_topics
