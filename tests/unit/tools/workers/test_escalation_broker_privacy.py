"""PLAN-W3-PRIVACY-REMAINDER: broker diagnostics, ADR-0006/0030 and DL-0034.

Synthetic arbitrary narratives; unit doubles only, no engine claims.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import Any

import pytest
import structlog

from maezo.tools.workers import escalation
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.phi_vars import redact_error_message, redact_free_text

_NARRATIVE = "inventado castelo violeta relata sonhos de planetas de papel"
_FACTORIES = {
    "team": escalation.make_notify_team_handler,
    "supervisor": escalation.make_notify_supervisor_handler,
}
_MESSAGES = {
    "team": "Falha ao notificar time humano (plantao-clinico)",
    "supervisor": "Falha ao notificar supervisor",
}
_EVENTS = {"team": "escalation_notify_team_failed", "supervisor": "escalation_supervisor_notify_failed"}
_LOGGER = "maezo.tools.workers.escalation"


def _task(kind: str) -> ExternalTask:
    return ExternalTask(
        task_id="synthetic-task",
        topic=f"operadora.escalation.notify_{kind}",
        process_instance_id="synthetic-process",
        business_key="ESC-synthetic-case",
        worker_id="synthetic-worker",
        retries=3,
        variables={
            "tenant_id": "synthetic",
            "severidade": "grave",
            "grupo_atendimento": "plantao-clinico",
            "prioridade": "P1",
            "motivo_categoria": "red_flag_clinico",
        },
    )


class _Broker(FakeKafkaPublisher):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error: BaseException | None = error
        self.attempts: list[tuple[str, dict[str, Any], str | None, bool | None, bool]] = []

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
        unordered: bool = False,
    ) -> bool:
        self.attempts.append((topic, value.copy(), key, best_effort, unordered))
        if self.error is not None:
            raise self.error
        return await super().publish(topic, value, key=key, best_effort=best_effort, unordered=unordered)


def _error(shape: str) -> Exception:
    if shape == "nested":
        error = RuntimeError(_NARRATIVE)
        error.__cause__ = ValueError(_NARRATIVE)
        return error
    if shape == "group":
        return ExceptionGroup(_NARRATIVE, [OSError(_NARRATIVE)])
    return RuntimeError(_NARRATIVE)


@pytest.mark.parametrize("kind", ["team", "supervisor"])
@pytest.mark.parametrize("shape", ["plain", "nested", "group"])
@pytest.mark.parametrize("sink", ["structlog", "stdlib", "exception"])
async def test_broker_narrative_never_reaches_diagnostics(
    kind: str, shape: str, sink: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert redact_free_text(_NARRATIVE) == _NARRATIVE
    broker = _Broker(_error(shape))
    if sink == "stdlib":
        # Exercise the real stdlib renderer/LogRecord route, independent of global logging config.
        monkeypatch.setattr(
            escalation,
            "logger",
            structlog.wrap_logger(
                logging.getLogger(_LOGGER),
                processors=[structlog.processors.JSONRenderer()],
                wrapper_class=structlog.stdlib.BoundLogger,
            ),
        )
    with caplog.at_level(logging.ERROR), structlog.testing.capture_logs() as logs:
        with pytest.raises(WorkerBpmnError) as caught:
            await _FACTORIES[kind](broker)(_task(kind))
        error = caught.value
        assert error.error_code == "ERR_ESC_NOTIFY_FAILED"
        assert error.variables is None
        assert len(broker.attempts) == 1
        assert broker.attempts[0][3:] == (False, False)
        assert broker.published == []
        if sink == "exception":
            rendered = "".join(traceback.format_exception(error))
            assert _NARRATIVE not in rendered
            assert error.__cause__ is None
            assert error.__context__ is None  # `from None` inside except alone is insufficient
            assert error.args == (_MESSAGES[kind],)
            assert _NARRATIVE not in repr(vars(error)) + repr(error) + str(error)
            assert redact_error_message(error) == f"WorkerBpmnError: {_MESSAGES[kind]}"
            logging.getLogger(_LOGGER).error(
                "synthetic outer trace", exc_info=(type(error), error, error.__traceback__)
            )
            assert "WorkerBpmnError" in caplog.text
            assert _NARRATIVE not in caplog.text
        elif sink == "stdlib":
            records = [record for record in caplog.records if record.name == _LOGGER]
            assert len(records) == 1
            assert _EVENTS[kind] in records[0].getMessage()
            assert _NARRATIVE not in caplog.text + repr([record.__dict__ for record in records])
            assert records[0].exc_info is None
        else:
            events = [event for event in logs if event["event"] == _EVENTS[kind]]
            assert len(events) == 1
            assert events[0]["log_level"] == "error"
            assert _NARRATIVE not in json.dumps(events)
            assert not ({"error", "exception", "exc_info", "cause", "error_type"} & events[0].keys())


@pytest.mark.parametrize("kind", ["team", "supervisor"])
@pytest.mark.parametrize("allowlisted", [True, False])
async def test_real_harness_projection_preserves_modeled_error_or_incident(
    kind: str, allowlisted: bool
) -> None:
    task = _task(kind)
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport,
        worker_id=task.worker_id,
        bpmn_error_allowlist=escalation.ESCALATION_BPMN_ERROR_ALLOWLIST if allowlisted else frozenset(),
    )
    broker = _Broker(_error("nested"))
    harness.register(task.topic, _FACTORIES[kind](broker))
    with structlog.testing.capture_logs() as logs:
        await harness._handle(task)
    expected = f"WorkerBpmnError: {_MESSAGES[kind]}"
    assert transport.completed == []
    assert broker.published == []
    assert len(broker.attempts) == 1
    assert broker.attempts[0][3:] == (False, False)
    if allowlisted:
        assert transport.bpmn_errors == [(task.task_id, "ERR_ESC_NOTIFY_FAILED", expected)]
        assert transport.bpmn_error_variables == [None]
        assert transport.failures == []
    else:
        assert transport.bpmn_errors == []
        assert transport.failures == [(task.task_id, expected, 0, 0)]
    assert _NARRATIVE not in repr(logs) + repr(transport.__dict__)


@pytest.mark.parametrize("kind", ["team", "supervisor"])
async def test_failure_then_recovery_preserves_publish_payload_key_and_human_route(kind: str) -> None:
    task = _task(kind)
    control = FakeKafkaPublisher()
    expected = await _FACTORIES[kind](control)(task)
    broker = _Broker(_error("plain"))
    handler = _FACTORIES[kind](broker)
    with pytest.raises(WorkerBpmnError):
        await handler(task)
    broker.error = None
    actual = await handler(task)
    assert actual is not None
    assert actual == expected
    assert broker.published == control.published
    assert broker.best_effort_calls == control.best_effort_calls == [False]
    assert broker.attempts[0] == broker.attempts[1]
    assert broker.attempts[0][:3] == control.published[0]
    assert actual["grupo_atendimento"] == "plantao-clinico"
    assert actual["severidade"] == "grave"
    if kind == "supervisor":
        assert actual["require_human_resolution"] is True
        assert broker.published[0][1]["alert_to"] == "supervisao-atendimento"


@pytest.mark.parametrize("kind", ["team", "supervisor"])
async def test_cancellation_is_not_translated_or_retried(kind: str) -> None:
    cancellation = asyncio.CancelledError("synthetic cancellation")
    broker = _Broker(cancellation)
    with structlog.testing.capture_logs() as logs, pytest.raises(asyncio.CancelledError) as caught:
        await _FACTORIES[kind](broker)(_task(kind))
    assert caught.value is cancellation
    assert len(broker.attempts) == 1
    assert broker.published == []
    assert not any(event["event"] == _EVENTS[kind] for event in logs)
