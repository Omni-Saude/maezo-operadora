"""ANS timer identity regression: canonical BPMN inputs, real resolver and publisher.

Unit seams only; deployed timer/Kafka acceptance remains in integration tests.
Contract: SP-OP-ANS-CRON-001 topology; AUTH payload_ref: ADR-0006 / ADR-0050.
"""

import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from maezo.tools.workers.ans_cron import trigger_submissions
from maezo.tools.workers.events import make_publish_event_handler
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher

_NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "c": "http://camunda.org/schema/1.0/bpmn"}
_BPMN = (
    Path(__file__).resolve().parents[4] / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"
)


def _timer_tasks() -> list[ExternalTask]:
    tasks = []
    for process in ET.parse(_BPMN).getroot().findall("b:process", _NS):
        resolver, publisher = process.findall("b:serviceTask", _NS)
        inputs = {p.attrib["name"]: p.text for p in resolver.findall(".//c:inputParameter", _NS)}
        variables = trigger_submissions(inputs, tenant_id="amh")
        variables.update({p.attrib["name"]: p.text for p in publisher.findall(".//c:inputParameter", _NS)})
        tasks.append(
            ExternalTask(
                task_id="timer-task",
                topic=publisher.attrib[f"{{{_NS['c']}}}topic"],
                process_instance_id="timer-instance",
                business_key="",
                worker_id="timer-worker",
                variables=variables,
                process_definition_key=process.attrib["id"],
                activity_id=publisher.attrib["id"],
            )
        )
    assert len(tasks) == 5
    return tasks


@pytest.mark.parametrize("task", _timer_tasks(), ids=lambda task: task.process_definition_key)
async def test_canonical_ans_timer_publishes_without_case_business_key(task: ExternalTask) -> None:
    kafka = FakeKafkaPublisher()
    result = await make_publish_event_handler(kafka, deployment_tenant_id="amh")(task)
    assert result is not None and result["event_published"] is True
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "ans.cron_due"
    assert payload["_business_key"] == ""
    assert payload["tenant_id"] == "amh"
    assert payload["report_type"] == task.variables["report_type"]
    assert payload["competencia"] == task.variables["competencia"]
    assert key == f"amh|{payload['report_type']}|{payload['competencia']}"
    # Another timer instance for the same tenant/report/period keeps its partition identity.
    await make_publish_event_handler(kafka, deployment_tenant_id="amh")(
        replace(task, process_instance_id="another-timer-instance")
    )
    assert kafka.published[1][2] == key


@pytest.mark.parametrize("business_key", ["", " ", "\t\n"])
@pytest.mark.parametrize("event_type", [None, "", "ans.cron_due", "auth.completed", []])
async def test_auth_blank_identity_cannot_be_bypassed_by_event_type(
    business_key: str, event_type: object
) -> None:
    task = _timer_tasks()[0]
    variables = dict(
        task.variables, event_type=event_type, process_definition_key=task.process_definition_key
    )
    task = replace(
        task,
        process_definition_key="SP-OP-AUTH-001",
        activity_id="ST_PublishCompleted",
        business_key=business_key,
        variables=variables,
    )
    kafka = FakeKafkaPublisher()
    with pytest.raises(ValueError, match="business_key"):
        await make_publish_event_handler(kafka, deployment_tenant_id="amh")(task)
    assert not kafka.published


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_definition_key", None),
        ("process_definition_key", "SP-OP-ANS-CRON-001"),
        ("process_definition_key", "SP-OP-ANS-CRON-001-UNKNOWN"),
        ("process_definition_key", "SP-OP-NIP-001"),
        ("activity_id", "ST_PublishCronDueDiops"),
        ("activity_id", ""),
        ("topic", "operadora.auth.publish"),
    ],
)
async def test_ans_event_fields_do_not_override_engine_source(field: str, value: str | None) -> None:
    task = _timer_tasks()[0]
    task = replace(task, **{field: value})
    kafka = FakeKafkaPublisher()
    with pytest.raises(ValueError, match="business_key"):
        await make_publish_event_handler(kafka, deployment_tenant_id="amh")(task)
    assert not kafka.published


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", None),
        ("event_type", "auth.completed"),
        ("event_topic", "agents.events.auth.completed"),
    ],
)
async def test_timer_exception_requires_its_contracted_event(field: str, value: str | None) -> None:
    task = _timer_tasks()[0]
    task = replace(task, variables=dict(task.variables, **{field: value}))
    kafka = FakeKafkaPublisher()
    with pytest.raises(ValueError, match="business_key"):
        await make_publish_event_handler(kafka, deployment_tenant_id="amh")(task)
    assert not kafka.published


async def test_timer_payload_cannot_replace_its_contracted_type() -> None:
    task = _timer_tasks()[0]
    variables = dict(task.variables, type="auth.completed")
    variables["event_payload_vars"] += ",type"
    kafka = FakeKafkaPublisher()
    with pytest.raises(ValueError, match="business_key"):
        await make_publish_event_handler(kafka, deployment_tenant_id="amh")(
            replace(task, variables=variables)
        )
    assert not kafka.published
