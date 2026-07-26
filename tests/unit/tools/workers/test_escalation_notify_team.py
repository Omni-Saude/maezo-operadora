"""Unit tests for SP-OP-ESCALATION-001 notify handlers — TDD London School.

DL-0034 (ratified by orchestrator 2026-07-26, built in t5): the escalation notify workers are RAW
ASYNC KAFKA HANDLERS (`make_notify_team_handler` / `make_notify_supervisor_handler`, registered via
`harness.register`), mirroring the #55 R-B precedent (`operadora.lgpd.request_additional_proof`) /
`events.py` — NOT the prior `WorkerBase` classes, which had no async Kafka seam and so NEVER
actually published (live-confirmed gap: `tests/integration/processes/test_sp_op_escalation_001.py`
`_NOTIFY_KAFKA_GAP_REASON`). These tests mirror `test_recurso.py`'s raw-handler tests:
publish-observed via `FakeKafkaPublisher`, and kafka=None completes anyway (never fabricates a
publish, never hangs the flow). Handlers must NEVER make adverse decisions — only route and notify.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.workers.escalation import (
    make_notify_supervisor_handler,
    make_notify_team_handler,
)
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"


def _task(
    *,
    topic: str = "operadora.escalation.notify_team",
    business_key: str = "ESC-amh-conv-1",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


# ---------------------------------------------------------------------------
# notify_team
# ---------------------------------------------------------------------------


async def test_notify_team_publishes_notification_and_returns_routing() -> None:
    """Happy path: emits the `escalation.notify_team` notification (observable via the probe's
    `notified_teams`) AND returns the routing metadata."""
    kafka = FakeKafkaPublisher()
    handler = make_notify_team_handler(kafka)
    result = await handler(
        _task(
            variables={
                "tenant_id": "amh",
                "source_agent_id": "helena",
                "severity": "grave",
                "motivo_categoria": "red_flag_clinico",
                "beneficiario_pseudo_id": "pseudo-abc123",
            }
        )
    )

    assert result["status"] == "teams_notified"
    assert result["group"] == "plantao-clinico"
    assert result["severity"] == "grave"
    assert result["event"] == "agents.events.escalation.requested"

    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert payload["type"] == "escalation.notify_team"
    assert payload["group"] == "plantao-clinico"
    assert payload["motivo_categoria"] == "red_flag_clinico"
    assert key == "ESC-amh-conv-1"


async def test_notify_team_routes_group_by_severity() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_team_handler(kafka)
    for severity, expected in [
        ("grave", "plantao-clinico"),
        ("moderada", "enfermagem-triagem"),
        ("leve", "atendimento-humano"),
    ]:
        result = await handler(_task(variables={"tenant_id": "amh", "severity": severity}))
        assert result["group"] == expected


async def test_notify_team_unknown_severity_falls_to_default() -> None:
    """Unknown severity defaults to atendimento-humano (fail-safe, never P1)."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(
        _task(variables={"tenant_id": "amh", "severity": "unknown", "motivo_categoria": "outro"})
    )
    assert result["group"] == "atendimento-humano"


async def test_notify_team_never_decides_adverse_action() -> None:
    """ONLY routing info — never a decision about the case (L0 hard)."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(
        _task(variables={"tenant_id": "amh", "severity": "grave", "motivo_categoria": "red_flag_clinico"})
    )
    assert "decisao" not in result
    assert "resultado" not in result
    assert "notas_resolucao" not in result
    _topic, payload, _key = kafka.published[0]
    assert "decisao" not in payload
    assert "resultado" not in payload


async def test_notify_team_kafka_none_completes_without_publish() -> None:
    """kafka=None (no producer wired): completes anyway so the flow reaches UT_TratarEscalonamento
    (a notification gap must never HANG the escalation), never fabricates a publish."""
    result = await make_notify_team_handler(None)(_task(variables={"tenant_id": "amh", "severity": "grave"}))
    assert result["status"] == "teams_notified"
    assert result["group"] == "plantao-clinico"


# ---------------------------------------------------------------------------
# notify_supervisor (serves ST_NotificarSupervisor + ST_NotificarFallback)
# ---------------------------------------------------------------------------


async def test_notify_supervisor_publishes_notification_on_sla_breach() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_supervisor_handler(kafka)
    result = await handler(
        _task(
            topic="operadora.escalation.notify_supervisor",
            variables={"tenant_id": "amh", "sla_status": "breached", "severity": "grave"},
        )
    )

    assert result["status"] == "supervisor_notified"
    assert result["sla_status"] == "breached"
    assert result["require_human_resolution"] is True

    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert payload["type"] == "escalation.notify_supervisor"
    assert payload["sla_status"] == "breached"
    assert key == "ESC-amh-conv-1"


async def test_notify_supervisor_no_human_decision() -> None:
    kafka = FakeKafkaPublisher()
    result = await make_notify_supervisor_handler(kafka)(
        _task(
            topic="operadora.escalation.notify_supervisor",
            variables={"tenant_id": "amh", "sla_status": "breached"},
        )
    )
    assert "decision" not in result
    assert "approve" not in str(result).lower()
    assert "deny" not in str(result).lower()


async def test_notify_supervisor_kafka_none_completes_without_publish() -> None:
    result = await make_notify_supervisor_handler(None)(
        _task(
            topic="operadora.escalation.notify_supervisor",
            variables={"tenant_id": "amh", "sla_status": "breached"},
        )
    )
    assert result["status"] == "supervisor_notified"


# ---------------------------------------------------------------------------
# registration (DL-0034: raw handlers, not WorkerRegistry entries)
# ---------------------------------------------------------------------------


def test_register_escalation_workers_registers_both_topics_as_raw_handlers() -> None:
    from maezo.tools.workers.escalation import register_escalation_workers
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_escalation_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    for topic in ("operadora.escalation.notify_team", "operadora.escalation.notify_supervisor"):
        assert topic in topics, f"{topic} not registered"
        # DL-0034: raw handlers populate _handlers but NOT the WorkerRegistry.
        assert harness.registry.get(topic) is None
