"""Shared fixtures for `tests/integration/` — real CIB Seven engine, no mock (ADR-0011).

Per T1.1 charter constraint: "if docker unavailable, explicit could-not-verify + mocked
variants" — every test in this package first probes `GET /engine-rest/version`; if the engine
is unreachable, the test SKIPS with an explicit, loud reason (never silently passes, never
fabricates a result). CI always brings the engine up before running this lane (see
`.github/workflows/ci.yml` job `integration`), so this only triggers for local runs without
`docker compose --profile core up -d`.

Engine URL resolution mirrors `maezo.platform.deploy.engine_deploy.resolve_engine_rest_url()`
(same `ENGINE_REST_URL` env var, same default) — this package deploys ad hoc test-only BPMN,
never anything from `spec/` (constraint 5: spec/ is the single source of truth for real
artifacts; a temp BPMN string here is explicitly NOT a spec/ artifact, per design §14 point 3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from maezo.platform.deploy.engine_deploy import resolve_engine_rest_url

#: Unique per pytest session — every deployment/process-definition key this package creates is
#: suffixed with this so repeated local runs against a persistent dev-stack volume never collide
#: with engine-side duplicate-filtering or a stale process definition from a previous run.
RUN_ID = uuid.uuid4().hex[:8]


def _engine_reachable(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/version", timeout=3.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def engine_base_url() -> str:
    return resolve_engine_rest_url()


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable(engine_base_url: str) -> None:
    """Explicit could-not-verify boundary (charter constraint) — never a silent pass."""
    if not _engine_reachable(engine_base_url):
        pytest.skip(
            f"COULD NOT VERIFY: CIB Seven engine unreachable at {engine_base_url} "
            "(GET /version failed). Run `docker compose --profile core up -d` and retry — "
            "see docs/design/T1.1-runtime-spine.md §14 point 3."
        )


@pytest.fixture
async def engine_client(engine_base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=engine_base_url, timeout=30.0) as client:
        yield client


def echo_process_bpmn(*, process_key: str, topic: str) -> str:
    """A minimal single-external-task process: start -> external task(`topic`) -> end.

    Deliberately the smallest possible fixture — the point of this suite is to exercise the
    HARNESS's fetch/complete/failure/retry/unlock mechanics against a real engine, not to
    exercise BPMN modeling. Never written under `spec/` (constraint 5) — posted directly to
    `/deployment/create` as an in-memory string.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  id="Definitions_{process_key}"
                  targetNamespace="http://maezo.health/test/t1-1-runtime-spine">
  <bpmn:process id="{process_key}" name="T1.1 integration test process" isExecutable="true"
                camunda:historyTimeToLive="1">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:serviceTask id="ExternalTask_1" name="test external task"
                      camunda:type="external" camunda:topic="{topic}">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:serviceTask>
    <bpmn:endEvent id="EndEvent_1">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="ExternalTask_1"/>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="ExternalTask_1" targetRef="EndEvent_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


async def deploy_process(
    client: httpx.AsyncClient, *, process_key: str, topic: str, deployment_name: str
) -> None:
    """POST /deployment/create with the ad hoc echo process — fail-closed (raises on non-2xx,
    the engine's error body is never swallowed, mirroring `engine_deploy.py`'s posture)."""
    bpmn_xml = echo_process_bpmn(process_key=process_key, topic=topic)
    resp = await client.post(
        "/deployment/create",
        data={
            "deployment-name": deployment_name,
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files={f"{process_key}.bpmn": (f"{process_key}.bpmn", bpmn_xml, "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")


async def start_process(client: httpx.AsyncClient, *, process_key: str, business_key: str) -> str:
    resp = await client.post(
        f"/process-definition/key/{process_key}/start",
        json={"businessKey": business_key, "variables": {}},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"start-process failed: {resp.status_code} {resp.text}")
    result: dict[str, Any] = resp.json()
    return str(result["id"])


async def history_process_instance(client: httpx.AsyncClient, process_instance_id: str) -> dict[str, Any]:
    resp = await client.get(f"/history/process-instance/{process_instance_id}")
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


async def list_incidents(client: httpx.AsyncClient, process_instance_id: str) -> list[dict[str, Any]]:
    resp = await client.get("/incident", params={"processInstanceId": process_instance_id})
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result
