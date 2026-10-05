"""PW1-A field-selection mechanics against real CIB Seven, without executing handlers.

The default worker registration supplies all 32 CRED/CONTAS/RECURSO scopes. Each is
bound to a unique synthetic topic so this probe cannot lock another process's task.
The existing echo BPMN fixture is reused, deployed and validated by the real engine.
HTTPX hooks only observe actual request/response bytes; no transport or decoder is
replaced. This proves engine field selection, not malicious-server resistance,
business-process acceptance, human authority, delivery or administrative issuance.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from maezo.platform.validation.bpmn import BPMN_NS, CAMUNDA_NS
from maezo.tools.workers.bootstrap import register_all_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, TopicSubscription, WorkerHarness

from .conftest import echo_process_bpmn

pytestmark = pytest.mark.integration

_SENTINEL = "pw1_synthetic_unrequested_sentinel"
_SENTINEL_VALUE = "SYNTHETIC_ONLY_PW1_EXTRA_VARIABLE"
_PROVIDER_PREFIXES = ("operadora.cred.", "operadora.contas.", "operadora.recurso.")


@asynccontextmanager
async def _owned_deployment(
    client: httpx.AsyncClient,
    subscriptions: list[TopicSubscription],
    values: Mapping[str, str],
    record_property: Callable[[str, object], None],
) -> AsyncIterator[dict[str, str]]:
    """Create and delete only this test's uniquely named deployment and instances."""
    run_id = uuid.uuid4().hex
    files: dict[str, tuple[str, str, str]] = {}
    processes: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for index, subscription in enumerate(subscriptions):
        key = f"pw1_fetch_probe_{run_id}_{index}"
        xml = echo_process_bpmn(process_key=key, topic=subscription.topic_name)
        document = ET.fromstring(xml)
        assert document.tag == f"{{{BPMN_NS}}}definitions"
        task = document.find(f".//{{{BPMN_NS}}}serviceTask")
        assert task is not None
        assert task.get(f"{{{CAMUNDA_NS}}}type") == "external"
        assert task.get(f"{{{CAMUNDA_NS}}}topic") == subscription.topic_name
        files[f"{key}.bpmn"] = (f"{key}.bpmn", xml, "text/xml")
        processes[subscription.topic_name] = key
        hashes[key] = hashlib.sha256(xml.encode()).hexdigest()
    record_property("synthetic_fixture_sha256", json.dumps(hashes, sort_keys=True))
    record_property(
        "fixture_origin",
        "tests/integration/conftest.py::echo_process_bpmn sha256="
        + hashlib.sha256(inspect.getsource(echo_process_bpmn).encode()).hexdigest(),
    )
    response = await client.post(
        "/deployment/create", data={"deployment-name": f"pw1-fetch-probe-{run_id}"}, files=files
    )
    response.raise_for_status()
    deployment_id = response.json()["id"]
    assert isinstance(deployment_id, str) and deployment_id
    instances: dict[str, str] = {}
    try:
        for topic, key in processes.items():
            started = await client.post(
                f"/process-definition/key/{key}/start",
                json={
                    "businessKey": f"SYNTHETIC-PW1-{run_id}-{key}",
                    "variables": {name: {"type": "String", "value": value} for name, value in values.items()},
                },
            )
            started.raise_for_status()
            instance_id = started.json()["id"]
            assert isinstance(instance_id, str) and instance_id
            instances[topic] = instance_id
            # Establish that omission is caused by fetch selection, not missing storage.
            persisted = await client.get(f"/process-instance/{instance_id}/variables/{_SENTINEL}")
            persisted.raise_for_status()
            assert persisted.json()["value"] == _SENTINEL_VALUE
        yield instances
    finally:
        # Cascade is restricted to this unique deployment; it includes only our probes.
        deleted = await client.delete(f"/deployment/{deployment_id}", params={"cascade": "true"})
        deleted.raise_for_status()


def _observe_http(transport: CibSevenWorkerTransport) -> tuple[list[dict[str, Any]], list[bytes]]:
    requests: list[dict[str, Any]] = []
    responses: list[bytes] = []

    async def request_hook(request: httpx.Request) -> None:
        if request.url.path.endswith("/external-task/fetchAndLock"):
            requests.append(json.loads(request.content))

    async def response_hook(response: httpx.Response) -> None:
        if response.request.url.path.endswith("/external-task/fetchAndLock"):
            responses.append(await response.aread())

    # Observe the real client's traffic, retaining its default HTTP transport and decoder.
    transport._client.event_hooks["request"].append(request_hook)
    transport._client.event_hooks["response"].append(response_hook)
    return requests, responses


async def test_all_32_default_provider_scopes_omit_persisted_sentinel_on_wire_and_task(
    engine_base_url: str,
    engine_client: httpx.AsyncClient,
    record_property: Callable[[str, object], None],
) -> None:
    transport = CibSevenWorkerTransport(engine_base_url)
    try:
        harness = WorkerHarness(transport, worker_id=f"pw1-fetch-{uuid.uuid4().hex}")
        register_all_workers(harness)
        actual = [s for s in harness._topic_subscriptions() if s.topic_name.startswith(_PROVIDER_PREFIXES)]
        assert len(actual) == 32
        assert len({s.topic_name for s in actual}) == 32
        run_id = uuid.uuid4().hex
        scoped: list[TopicSubscription] = []
        for index, subscription in enumerate(actual):
            assert isinstance(subscription.variables, tuple) and subscription.variables, (
                subscription.topic_name
            )
            assert _SENTINEL not in subscription.variables
            assert len(set(subscription.variables)) == len(subscription.variables)
            scoped.append(
                TopicSubscription(f"pw1.synthetic.{run_id}.{index}", 30_000, subscription.variables)
            )
        record_property(
            "real_registration_scopes",
            json.dumps({s.topic_name: s.variables for s in actual}, sort_keys=True),
        )
        record_property(
            "synthetic_topic_mapping",
            json.dumps(
                {
                    probe.topic_name: original.topic_name
                    for probe, original in zip(scoped, actual, strict=True)
                }
            ),
        )
        # Every process stores the union. Each fetch must return only its own real scope.
        names = {name for subscription in actual for name in subscription.variables or ()}
        values = {name: f"SYNTHETIC-PW1-{name}" for name in names}
        values[_SENTINEL] = _SENTINEL_VALUE
        requests, responses = _observe_http(transport)
        async with _owned_deployment(engine_client, scoped, values, record_property) as instances:
            tasks = await transport.fetch_and_lock(
                harness._worker_id, scoped, max_tasks=32, async_response_timeout_ms=1_000
            )
            assert len(requests) == len(responses) == 1
            assert len(tasks) == 32
            assert {task.topic for task in tasks} == set(instances)
            assert {task.process_instance_id for task in tasks} == set(instances.values())
            sent = {item["topicName"]: item["variables"] for item in requests[0]["topics"]}
            wire = json.loads(responses[0])
            assert len(wire) == 32
            assert _SENTINEL.encode() not in responses[0]
            assert _SENTINEL_VALUE.encode() not in responses[0]
            raw_by_topic = {item["topicName"]: item for item in wire}
            parsed_by_topic = {task.topic: task for task in tasks}
            for subscription in scoped:
                expected = {name: values[name] for name in subscription.variables or ()}
                assert sent[subscription.topic_name] == list(subscription.variables or ())
                raw = raw_by_topic[subscription.topic_name]
                assert raw["processInstanceId"] == instances[subscription.topic_name]
                assert set(raw["variables"]) == set(expected)
                assert {name: value["value"] for name, value in raw["variables"].items()} == expected
                assert parsed_by_topic[subscription.topic_name].variables == expected
    finally:
        await transport.close()


@pytest.mark.parametrize("scope", [(), None], ids=["explicit-empty-fetches-none", "legacy-none-fetches-all"])
async def test_real_engine_empty_scope_is_distinct_from_legacy_none(
    scope: tuple[str, ...] | None,
    engine_base_url: str,
    engine_client: httpx.AsyncClient,
    record_property: Callable[[str, object], None],
) -> None:
    transport = CibSevenWorkerTransport(engine_base_url)
    try:
        subscription = TopicSubscription(f"pw1.synthetic.control.{uuid.uuid4().hex}", 30_000, scope)
        values = {"pw1_synthetic_control": "SYNTHETIC-CONTROL", _SENTINEL: _SENTINEL_VALUE}
        requests, responses = _observe_http(transport)
        async with _owned_deployment(engine_client, [subscription], values, record_property) as instances:
            tasks = await transport.fetch_and_lock(
                f"pw1-control-{uuid.uuid4().hex}",
                [subscription],
                max_tasks=1,
                async_response_timeout_ms=1_000,
            )
            assert len(requests) == len(responses) == len(tasks) == 1
            assert tasks[0].process_instance_id == instances[subscription.topic_name]
            raw = json.loads(responses[0])
            assert len(raw) == 1
            if scope is None:
                assert "variables" not in requests[0]["topics"][0]
                assert _SENTINEL.encode() in responses[0]
                assert _SENTINEL_VALUE.encode() in responses[0]
                assert {name: value["value"] for name, value in raw[0]["variables"].items()} == values
                assert tasks[0].variables == values
            else:
                assert requests[0]["topics"][0]["variables"] == []
                assert _SENTINEL.encode() not in responses[0]
                assert _SENTINEL_VALUE.encode() not in responses[0]
                assert raw[0]["variables"] == {}
                assert tasks[0].variables == {}
    finally:
        await transport.close()
