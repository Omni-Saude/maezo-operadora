"""R228 D03/D04: canonical engine IDs and faithful integration fixture provenance.

Real decoder and registered handler, with offline HTTP/output fixtures only.
No live timer, broker or engine integration claim.
"""

import ast
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from maezo.platform.privacy import program_publication
from maezo.platform.privacy.population_policy import PopulationPolicyUnavailableError
from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS, ProcessAllowlist, ProcessKeyNotAllowedError
from maezo.tools.workers import events
from maezo.tools.workers.harness import ExternalTask, _from_camunda_var
from tests.unit.platform.privacy.test_publication_identity_shape import (
    GENERIC,
    ROOT,
    composed,
    fetched,
    response_item,
)

B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
C = "{http://camunda.org/schema/1.0/bpmn}"


def canonical_processes():
    return [
        process
        for path in (ROOT / "spec/processes/bpmn").glob("*.bpmn")
        for process in ET.parse(path).getroot().findall(B + "process")
    ]


def timer_bindings():
    path = ROOT / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"
    return [
        (
            process.attrib["id"],
            task.attrib["id"],
            {p.attrib["name"]: p.text for p in task.iter(C + "inputParameter")},
        )
        for process in ET.parse(path).getroot().findall(B + "process")
        for task in process.iter(B + "serviceTask")
        if task.get(C + "topic") == GENERIC
    ]


def test_publication_sources_equal_exact_canonical_engine_ids():
    processes = canonical_processes()
    canonical_ids = {process.attrib["id"] for process in processes}
    assert len(processes) == len(canonical_ids) == 20
    assert canonical_ids == program_publication._PUBLICATION_PROCESS_KEYS
    timers = {source for source, _, _ in timer_bindings()}
    assert len(timers) == 5
    assert canonical_ids - KNOWN_PROCESS_KEYS == timers
    assert not timers & KNOWN_PROCESS_KEYS
    assert "SP-OP-ANS-CRON-001" not in canonical_ids


@pytest.mark.parametrize("source,activity,params", timer_bindings())
async def test_all_canonical_timer_publishers_decode_and_publish(source, activity, params):
    assert params["event_topic"] == "operadora.notifications.internal"
    assert params["event_type"] == "ans.cron_due"
    # Agent-start permission must never follow from publication-source permission.
    with pytest.raises(ProcessKeyNotAllowedError):
        ProcessAllowlist().ensure_allowed(source)
    item = response_item(process=source, activity=activity, variables=params)
    task = await fetched(item)
    assert task.process_definition_key == source
    assert task.activity_id == activity
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(task))["event_published"] is True
    sink.publish.assert_awaited_once()
    assert sink.publish.call_args.args[0] == params["event_topic"]
    assert sink.publish.call_args.args[1]["type"] == params["event_type"]


@pytest.mark.parametrize(
    "source",
    [
        "SP-OP-ANS-CRON-001",
        "SP-OP-ANS-CRON-001-UNKNOWN",
        "SP-OP-ANS-CRON-001-RN124SIP-extra",
        "SP-OP-ANS-CRON-001-RN124SIP ",
        " SP-OP-ANS-CRON-001-RN124SIP",
        "SP-OP-ANS-CRON-001-rn124sip",
        "SP-OP-ANS-CRON-001-RN124SІP",  # Cyrillic lookalike I.
        "SP-OP-UNKNOWN-001",
        None,
        "",
    ],
)
async def test_timer_unknown_lookalike_missing_source_refuses_before_partition(monkeypatch, source):
    valid, activity, params = timer_bindings()[0]
    item = response_item(process=source, activity=activity, variables=params)
    item["businessKey"] = valid
    item["variables"]["processDefinitionKey"] = {"type": "String", "value": valid}
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    partition = Mock()
    monkeypatch.setattr(events, "partition_key_for_task", partition)
    with pytest.raises(PopulationPolicyUnavailableError, match="publication_source_unavailable"):
        await composed(sink)(await fetched(item))
    partition.assert_not_called()
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize("field", ["processDefinitionKey", "activityId"])
async def test_timer_omitted_top_level_identity_refuses(monkeypatch, field):
    source, activity, params = timer_bindings()[0]
    item = response_item(process=source, activity=activity, variables=params)
    item["variables"][field] = {"type": "String", "value": item.pop(field)}
    sink = SimpleNamespace(publish=AsyncMock())
    partition = Mock()
    monkeypatch.setattr(events, "partition_key_for_task", partition)
    with pytest.raises(PopulationPolicyUnavailableError, match="publication_source_unavailable"):
        await composed(sink)(await fetched(item))
    partition.assert_not_called()
    sink.publish.assert_not_awaited()


def fixture_constructor(path, item):
    """Project the actual source constructor; do not import/run integration fixtures."""
    calls = [
        node
        for node in ast.walk(ast.parse((ROOT / path).read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ExternalTask"
    ]
    assert len(calls) == 1
    bindings = dict(
        ExternalTask=ExternalTask,
        item=item,
        instance_id=item["processInstanceId"],
        business_key=item["businessKey"],
        worker_id=item["workerId"],
        _from_camunda_var=_from_camunda_var,
        CONTAS_COMPLETED_EVENT="agents.events.contas.completed",
    )
    return eval(compile(ast.Expression(calls[0]), str(ROOT / path), "eval"), bindings)


async def test_helena_manual_decoder_matches_production_top_level_source():
    item = response_item(
        process="SP-OP-ESCALATION-001",
        activity="ST_PublishRequested",
        variables=dict(event_topic="agents.events.escalation.requested", processDefinitionKey="forged"),
    )
    manual = fixture_constructor("tests/integration/agents/test_helena_escalation_broker.py", item)
    production = await fetched(item)
    assert manual.process_definition_key == production.process_definition_key == item["processDefinitionKey"]
    assert manual.activity_id == production.activity_id == item["activityId"]
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(manual))["event_published"] is True
    sink.publish.assert_awaited_once()


async def test_kafka_outage_fixture_preserves_canonical_contas_source():
    task = fixture_constructor(
        "tests/integration/platform/test_events_kafka_producer_live.py", response_item()
    )
    process = next(p for p in canonical_processes() if p.get("id") == task.process_definition_key)
    activity = next(t for t in process.iter(B + "serviceTask") if t.get("id") == task.activity_id)
    params = {p.attrib["name"]: p.text for p in activity.iter(C + "inputParameter")}
    assert activity.get(C + "topic") == task.topic == GENERIC
    assert params["event_topic"] == task.variables["event_topic"]
    assert params["event_desfecho"] == task.variables["event_desfecho"]
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(task))["event_published"] is True
    sink.publish.assert_awaited_once()
