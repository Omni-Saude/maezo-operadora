"""ESC-TOLERANT-LOG-RAW-VALUE / ESCALATION-REFUSAL-PATH-RAW-VALUE.

ADR-0006: discard untrusted labels at their source, including diagnostic outputs.
SP-OP-ESCALATION-001: preserve DMN routing and the modeled human fallback.
All narratives below are arbitrary synthetic text, not real personal data.
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

import pytest
import structlog

from maezo.tools.workers.escalation import (
    ESCALATION_BPMN_ERROR_ALLOWLIST,
    make_notify_supervisor_handler,
    make_notify_team_handler,
)
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerBpmnError
from maezo.tools.workers.phi_vars import redact_error_message, redact_free_text

_NARRATIVE = "inventado castelo violeta relata sonhos de planetas de papel"
_LOGGER = "maezo.tools.workers.escalation"
_FACTORIES = {"team": make_notify_team_handler, "supervisor": make_notify_supervisor_handler}


def _task(kind: str, **overrides: Any) -> ExternalTask:
    variables = {
        "tenant_id": "synthetic",
        "severidade": "grave",
        "grupo_atendimento": "plantao-clinico",
        "prioridade": "P1",
        "motivo_categoria": "red_flag_clinico",
    }
    variables.update(overrides)
    return ExternalTask(
        task_id="synthetic-task",
        topic=f"operadora.escalation.notify_{kind}",
        process_instance_id="synthetic-process",
        business_key="ESC-synthetic-case",
        worker_id="synthetic-worker",
        variables=variables,
    )


def _sink_text(sink: str, logs: list[dict[str, Any]], caplog: pytest.LogCaptureFixture) -> str:
    if sink == "structlog":
        assert logs
        return json.dumps(logs, ensure_ascii=False)
    records = [record for record in caplog.records if record.name == _LOGGER]
    assert records
    # Cover both rendered text and unformatted args/extra fields retained by handlers.
    return caplog.text + repr([record.__dict__ for record in records])


@pytest.mark.parametrize("sink", ["structlog", "stdlib"])
@pytest.mark.parametrize("producer", [True, False])
async def test_tolerated_narrative_is_fully_omitted(
    sink: str, producer: bool, caplog: pytest.LogCaptureFixture
) -> None:
    assert redact_free_text(_NARRATIVE) == _NARRATIVE  # proves the old heuristic is blind
    kafka = FakeKafkaPublisher() if producer else None
    with caplog.at_level(logging.WARNING, logger=_LOGGER), structlog.testing.capture_logs() as logs:
        result = await make_notify_team_handler(kafka)(
            _task(
                "team", motivo_categoria=_NARRATIVE, grupo_atendimento="atendimento-humano", prioridade="P2"
            )
        )
    assert result["grupo_atendimento"] == "atendimento-humano"
    assert result["prioridade"] == "P2"
    assert result["severidade"] == "grave"
    assert result["status"] == ("teams_notified" if producer else "teams_notification_skipped_no_producer")
    assert "motivo_categoria" not in result
    if kafka is not None:
        assert len(kafka.published) == 1
        assert kafka.best_effort_calls == [False]
        assert "motivo_categoria" not in kafka.published[0][1]
        assert _NARRATIVE not in repr(kafka.published)
    events = [event for event in logs if event["event"] == "escalation_rotulo_fora_do_dominio_tolerado"]
    assert len(events) == 1
    assert events[0]["campo"] == "motivo_categoria"
    assert events[0]["business_key"] == "ESC-synthetic-case"
    assert events[0]["dominio"] == sorted(events[0]["dominio"])
    assert _NARRATIVE not in _sink_text(sink, logs, caplog)
    assert "valor" not in events[0]


@pytest.mark.parametrize("kind", ["team", "supervisor"])
@pytest.mark.parametrize("field", ["severidade", "grupo_atendimento", "prioridade"])
@pytest.mark.parametrize("sink", ["structlog", "stdlib", "exception"])
async def test_strict_refusal_never_renders_narrative(
    kind: str, field: str, sink: str, caplog: pytest.LogCaptureFixture
) -> None:
    kafka = FakeKafkaPublisher()
    handler = _FACTORIES[kind](kafka)
    with (
        caplog.at_level(logging.ERROR, logger=_LOGGER),
        structlog.testing.capture_logs() as logs,
        pytest.raises(WorkerBpmnError) as caught,
    ):
        await handler(_task(kind, **{field: _NARRATIVE}))
    exc = caught.value
    assert exc.error_code == "ERR_ESC_NOTIFY_FAILED"
    assert exc.error_code in ESCALATION_BPMN_ERROR_ALLOWLIST
    assert exc.variables is None
    assert not kafka.published
    assert logs[0]["motivo"] == f"{field}_fora_do_dominio"
    assert field in logs[0]["detalhe"]
    if sink == "exception":
        rendered = "".join(traceback.format_exception(exc))
        output = str(exc) + repr(exc) + repr(exc.args) + repr(vars(exc)) + rendered
        # The harness uses this exact projection for BPMN errors and demoted incidents.
        output += redact_error_message(exc)
    else:
        output = _sink_text(sink, logs, caplog)
    assert _NARRATIVE not in output
    # A refusal must not poison a later valid notification on the same handler.
    result = await handler(_task(kind))
    assert len(kafka.published) == 1
    assert kafka.best_effort_calls == [False]
    assert result["grupo_atendimento"] == "plantao-clinico"
    assert result["severidade"] == "grave"
    assert result["prioridade"] == "P1"
    if kind == "supervisor":
        assert result["require_human_resolution"] is True
        assert result["alert_to"] == "supervisao-atendimento"


@pytest.mark.parametrize("kind", ["team", "supervisor"])
@pytest.mark.parametrize("alias", ["severity", "group", "priority"])
async def test_alias_refusal_keeps_only_the_key(
    kind: str, alias: str, caplog: pytest.LogCaptureFixture
) -> None:
    kafka = FakeKafkaPublisher()
    with (
        caplog.at_level(logging.ERROR, logger=_LOGGER),
        structlog.testing.capture_logs() as logs,
        pytest.raises(WorkerBpmnError) as caught,
    ):
        await _FACTORIES[kind](kafka)(_task(kind, **{alias: {"nested": _NARRATIVE}}))
    assert logs[0]["motivo"] == "alias_ingles_proibido"
    assert alias in logs[0]["detalhe"]
    assert _NARRATIVE not in repr(logs) + caplog.text + "".join(traceback.format_exception(caught.value))
    assert not kafka.published
    assert caught.value.error_code == "ERR_ESC_NOTIFY_FAILED"


@pytest.mark.parametrize("kind", ["team", "supervisor"])
async def test_valid_labels_and_missing_producer_preserve_human_route(kind: str) -> None:
    result = await _FACTORIES[kind](None)(_task(kind))
    assert result["severidade"] == "grave"
    assert result["grupo_atendimento"] == "plantao-clinico"
    assert result["prioridade"] == "P1"
    if kind == "team":
        assert result["motivo_categoria"] == "red_flag_clinico"
        assert result["status"] == "teams_notification_skipped_no_producer"
    else:
        assert result["status"] == "supervisor_notification_skipped_no_producer"
        assert result["require_human_resolution"] is True
