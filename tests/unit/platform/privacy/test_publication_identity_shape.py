"""R228 D01/D02: real fetch DTO -> composed publisher; exact returned aggregate shape.

Offline HTTP transport fixtures only, never engine integration or policy ratification.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from maezo.agents.andre.graph import CohortAggregate
from maezo.gateway.seams import population
from maezo.gateway.seams._base import SeamContext
from maezo.platform.privacy import population_policy as policy
from maezo.platform.privacy.program_publication import PROGRAM_ACTIVITY_TOPICS, PROGRAM_EVENT_FIELDS
from maezo.tools.workers import events
from maezo.tools.workers.harness import CibSevenWorkerTransport, TopicSubscription, WorkerHarness
from tests.unit.platform.privacy.test_population_policy import authorized

ROOT = Path(__file__).resolve().parents[4]
PROGRAM = "SP-OP-PROGRAMA-001"
GENERIC = "operadora.events.publish"


def response_item(*, process=PROGRAM, activity="ST_PublishCompleted", topic=GENERIC, variables=None):
    return dict(
        id="synthetic-task",
        topicName=topic,
        processInstanceId="synthetic-instance",
        businessKey="unrelated-description",
        workerId="unit",
        processDefinitionKey=process,
        activityId=activity,
        variables={
            k: {
                "type": "Json" if type(v) in (dict, list) else "String",
                "value": json.dumps(v) if type(v) in (dict, list) else v,
            }
            for k, v in (variables or {}).items()
        },
    )


async def fetched(item):
    transport = CibSevenWorkerTransport("http://offline.invalid")
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(
        base_url="http://offline.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[item])),
    )
    try:
        result = await transport.fetch_and_lock(
            "unit", [TopicSubscription(GENERIC, 1000)], max_tasks=1, async_response_timeout_ms=1000
        )
        assert len(result) == 1
        return result[0]
    finally:
        await transport.close()


def composed(sink):
    # Register via the actual worker composition entry point. Only the HTTP fixture above
    # supplies fetched tasks; this harness is not run or labelled engine integration.
    harness = WorkerHarness(Mock(), worker_id="unit")
    events.register_events_workers(harness, kafka=sink)
    return harness._handlers[GENERIC]


@pytest.mark.parametrize(
    "destination",
    [
        "agents.events.analytics.completed",
        "agents.events.programa",
        "operadora.notifications.internal",
        "agents.events.programa.received",
    ],
)
async def test_program_source_refuses_alternate_destination_before_partition(monkeypatch, destination):
    item = response_item(
        variables=dict(
            event_topic=destination,
            event_type="analytics.resultados",
            event_payload_vars="resultados",
            resultados={"individual_rows": ["synthetic"]},
        )
    )
    task = await fetched(item)
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    partition = Mock(return_value="synthetic-key")
    monkeypatch.setattr(events, "partition_key_for_task", partition)
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await composed(sink)(task)
    partition.assert_not_called()
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("processDefinitionKey", None),
        ("processDefinitionKey", ""),
        ("processDefinitionKey", "unknown"),
        ("processDefinitionKey", {"program": PROGRAM}),
        ("processDefinitionKey", "SP-OP-AUTH-001"),
        ("activityId", None),
        ("activityId", ""),
        ("activityId", " "),
        ("activityId", ["ST_PublishCompleted"]),
        ("activityId", "unknown"),
        ("activityId", "ST_PublishReceived"),
        ("topicName", "operadora.programa.stop_processing"),
    ],
)
async def test_source_missing_unknown_mismatched_never_partitions(monkeypatch, field, value):
    item = response_item(
        variables=dict(
            event_topic="agents.events.programa.completed",
            event_payload_vars="programa_id",
            programa_id="synthetic",
        )
    )
    item[field] = value
    # Neither a program-shaped key nor forged process variables may supply provenance.
    item["businessKey"] = "PROG-tenant-program-subject-cycle"
    item["variables"]["processDefinitionKey"] = {"type": "String", "value": PROGRAM}
    item["variables"]["activityId"] = {"type": "String", "value": "ST_PublishCompleted"}
    task = await fetched(item)
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    partition = Mock()
    monkeypatch.setattr(events, "partition_key_for_task", partition)
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await composed(sink)(task)
    partition.assert_not_called()
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize("field", ["processDefinitionKey", "activityId"])
async def test_omitted_metadata_is_not_reconstructed(field):
    item = response_item(variables=dict(event_topic="agents.events.analytics.completed"))
    del item[field]
    task = await fetched(item)
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await composed(sink)(task)
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize("activity,destination", sorted(PROGRAM_ACTIVITY_TOPICS.items()))
async def test_all_five_source_bound_events_still_publish(activity, destination):
    values = {k: "synthetic" for k in PROGRAM_EVENT_FIELDS[destination]}
    item = response_item(
        activity=activity,
        variables=dict(values, event_topic=destination, event_payload_vars=",".join(values)),
    )
    task = await fetched(item)
    assert task.process_definition_key == PROGRAM and task.activity_id == activity
    with pytest.raises(FrozenInstanceError):
        task.activity_id = "other"
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(task))["event_published"] is True
    sink.publish.assert_awaited_once()
    assert values.items() <= sink.publish.call_args.args[1].items()


@pytest.mark.parametrize(
    "process,activity,destination,event_type",
    [
        ("SP-OP-AUTH-001", "ST_PublishCompleted", "agents.events.auth.completed", "auth.completed"),
        (
            "SP-OP-ESCALATION-001",
            "ST_PublishRequested",
            "agents.events.escalation.requested",
            "escalation.requested",
        ),
        (
            "SP-OP-ANS-CRON-001-RN124SIP",
            "ST_PublishCronDueRn124Sip",
            "operadora.notifications.internal",
            "ans.cron_due",
        ),
    ],
)
async def test_nonprogram_engine_origin_preserves_nested_payload(process, activity, destination, event_type):
    # A program-looking business key and process variable do not override engine metadata.
    item = response_item(
        process=process,
        activity=activity,
        variables=dict(
            event_topic=destination,
            event_type=event_type,
            event_payload_vars="resultados",
            resultados={"synthetic": [1]},
            processDefinitionKey=PROGRAM,
        ),
    )
    item["businessKey"] = "PROG-tenant-program-subject-cycle"
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(await fetched(item)))["event_published"] is True
    assert sink.publish.call_args.args[1]["resultados"] == {"synthetic": [1]}


def test_program_activity_bindings_match_canonical_bpmn():
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "c": "http://camunda.org/schema/1.0/bpmn"}
    tree = ET.parse(ROOT / "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn")
    process = tree.find("b:process", ns)
    assert process.attrib["id"] == PROGRAM
    observed = {}
    for node in process.findall(".//b:serviceTask", ns):
        if node.get("{" + ns["c"] + "}topic") == GENERIC:
            params = {p.attrib["name"]: p.text for p in node.findall(".//c:inputParameter", ns)}
            observed[node.attrib["id"]] = params["event_topic"]
    assert observed == PROGRAM_ACTIVITY_TOPICS


class ExtraAggregate(CohortAggregate):
    pass


class ExtraDict(dict):
    pass


class ExtraTuple(tuple):
    pass


class ExtraString(str):
    pass


class ExtraFloat(float):
    pass


def aggregate(**changes):
    return CohortAggregate(
        **(
            dict(
                cohort_id="opaque reference accepted",
                dataset_ref="another opaque reference",
                metrics={"synthetic_metric": 2.0},
                cohort_size=7,
                k_anonymity=7,
                suppressed=("synthetic_metric",),
            )
            | changes
        )
    )


MALFORMED = [
    {"cohort_id": {"individual_rows": [{"subject": "synthetic"}]}},
    {"dataset_ref": {"individual_rows": [{"subject": "synthetic"}]}},
    {"cohort_id": ["synthetic"]},
    {"dataset_ref": None},
    {"cohort_id": ExtraString("synthetic")},
    {"dataset_ref": ExtraString("synthetic")},
    {"metrics": ExtraDict(synthetic_metric=2.0)},
    {"metrics": [("synthetic_metric", 2.0)]},
    {"metrics": {ExtraString("synthetic_metric"): 2.0}},
    {"metrics": {7: 2.0}},
    {"metrics": {"synthetic_metric": {"individual_rows": ["synthetic"]}}},
    {"metrics": {"synthetic_metric": True}},
    {"metrics": {"synthetic_metric": ExtraFloat(2.0)}},
    {"metrics": {"synthetic_metric": float("inf")}},
    {"suppressed": "synthetic_metric"},
    {"suppressed": None},
    {"suppressed": {"synthetic_metric": "synthetic"}},
    {"suppressed": ["synthetic_metric"]},
    {"suppressed": ExtraTuple(("synthetic_metric",))},
    {"suppressed": ({"individual_rows": ["synthetic"]},)},
    {"suppressed": (ExtraString("synthetic_metric"),)},
    {"suppressed": (None,)},
    {"cohort_size": True},
    {"k_anonymity": 7.0},
]


@pytest.mark.parametrize("method", ["population_metrics", "actuarial_risk"])
@pytest.mark.parametrize("changes", MALFORMED)
async def test_complete_shape_refused_before_phi_checker_and_consumer(monkeypatch, method, changes):
    monkeypatch.setattr(population, "load_population_policy", authorized)
    phi_checker = Mock(return_value=False)
    monkeypatch.setattr(CohortAggregate, "has_resolvable_phi", phi_checker)
    inner = SimpleNamespace(**{method: AsyncMock(return_value=aggregate(**changes))})
    consumer = AsyncMock()
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await consumer(await getattr(client, method)("synthetic", features=["synthetic_metric"]))
    getattr(inner, method).assert_awaited_once()
    phi_checker.assert_not_called()
    consumer.assert_not_awaited()


@pytest.mark.parametrize("method", ["population_metrics", "actuarial_risk"])
@pytest.mark.parametrize("kind", ["subclass", "duck", "dict", "object", "none", "uninitialized"])
async def test_extra_or_duck_objects_refused(monkeypatch, method, kind):
    values = asdict(aggregate())
    if kind == "subclass":
        value = ExtraAggregate(**values)
        object.__setattr__(value, "individual_rows", ["synthetic"])
    elif kind == "duck":
        value = SimpleNamespace(
            **values, individual_rows=["synthetic"], has_resolvable_phi=Mock(return_value=False)
        )
    elif kind == "dict":
        value = values | {"individual_rows": ["synthetic"]}
    elif kind == "uninitialized":
        value = object.__new__(CohortAggregate)
    else:
        value = object() if kind == "object" else None
    monkeypatch.setattr(population, "load_population_policy", authorized)
    inner = SimpleNamespace(**{method: AsyncMock(return_value=value)})
    consumer = AsyncMock()
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await consumer(await getattr(client, method)("synthetic", features=["synthetic_metric"]))
    getattr(inner, method).assert_awaited_once()
    consumer.assert_not_awaited()


@pytest.mark.parametrize("method", ["population_metrics", "actuarial_risk"])
@pytest.mark.parametrize("metric", [2, 2.0])
async def test_exact_valid_shape_keeps_reference_contract_and_phi_checker(monkeypatch, method, metric):
    value = aggregate(metrics={"synthetic_metric": metric})
    monkeypatch.setattr(population, "load_population_policy", authorized)
    checker = Mock(wraps=CohortAggregate.has_resolvable_phi)
    monkeypatch.setattr(CohortAggregate, "has_resolvable_phi", checker)
    inner = SimpleNamespace(**{method: AsyncMock(return_value=value)})
    consumer = AsyncMock()
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    await consumer(await getattr(client, method)("synthetic", features=["synthetic_metric"]))
    checker.assert_called_once_with(value)
    consumer.assert_awaited_once_with(value)


@pytest.mark.parametrize(
    "destination,event_type",
    [
        ("agents.events.programa.completed", None),
        ("agents.events.programa", None),
        ("operadora.notifications.internal", "programa.stratify_risk"),
        ("operadora.notifications.internal", "programa"),
    ],
)
async def test_other_process_cannot_impersonate_program_sink(monkeypatch, destination, event_type):
    task = await fetched(
        response_item(
            process="SP-OP-AUTH-001", variables=dict(event_topic=destination, event_type=event_type)
        )
    )
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    partition = Mock()
    monkeypatch.setattr(events, "partition_key_for_task", partition)
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await composed(sink)(task)
    partition.assert_not_called()
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "destination,event_type",
    [
        ("agents.events.programacao.completed", "programacao.completed"),
        ("operadora.notifications.internal", "programacao.completed"),
    ],
)
async def test_similarly_named_nonprogram_namespace_keeps_existing_behavior(destination, event_type):
    task = await fetched(
        response_item(
            process="SP-OP-AUTH-001", variables=dict(event_topic=destination, event_type=event_type)
        )
    )
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    assert (await composed(sink)(task))["event_published"] is True
    sink.publish.assert_awaited_once()
