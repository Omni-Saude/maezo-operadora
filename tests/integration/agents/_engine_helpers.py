"""Shared REST helpers for `tests/integration/agents/` — direct engine assertions (ADR-0011: no
`EngineRest` abstraction ported from v1 yet; these are the minimal, direct calls this package's
two acceptance suites both need).

Also provides `noop_events_publish`: a TEST-ONLY external-task handler for the generic
`operadora.events.publish` topic. This topic is declared by EVERY SP-OP-* BPMN (a domain-event
publish step) but has NO production worker anywhere in this codebase yet (verified: zero hits for
`operadora.events.publish` across `tools/workers/bootstrap.py` and all 16 worker modules) — a
pre-existing gap, not introduced or hidden by this change, and out of this charter's scope to
fix (no Kafka producer exists in this build either, per T1.1/T1.6's own module docstrings).
Without SOMETHING servicing this topic, every SP-OP-ESCALATION-001/SP-OP-AUTH-001 instance would
stall forever at its first `ST_Publish*` step and never reach its user task — so this suite's
probes register this no-op completion (never a fabricated Kafka publish; it does nothing beyond
unblocking the BPMN sequence flow) purely to exercise the REST of the flow this charter DOES own.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask


async def noop_events_publish(task: ExternalTask) -> dict[str, Any]:
    """Test-only completion for `operadora.events.publish` — see module docstring."""
    del task
    return {}


async def active_instances(client: httpx.AsyncClient, business_key: str) -> list[dict[str, Any]]:
    resp = await client.get("/process-instance", params={"businessKey": business_key, "active": "true"})
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


async def tasks_for_instance(client: httpx.AsyncClient, process_instance_id: str) -> list[dict[str, Any]]:
    resp = await client.get("/task", params={"processInstanceId": process_instance_id})
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


async def candidate_groups(client: httpx.AsyncClient, task_id: str) -> set[str]:
    resp = await client.get(f"/task/{task_id}/identity-links", params={"type": "candidate"})
    resp.raise_for_status()
    links: list[dict[str, Any]] = resp.json()
    return {link["groupId"] for link in links if link.get("groupId")}


async def wait_for_task(
    client: httpx.AsyncClient, process_instance_id: str, *, attempts: int = 90, delay_s: float = 1.0
) -> dict[str, Any]:
    """Poll for a user task. Generous default budget (90s): the probe harnesses in this package
    service TWO sequential external tasks (a generic publish + a domain notify) before a user
    task appears, and `WorkerHarness`'s own idle-backoff between empty long-polls can reach
    several seconds per hop — see the probe fixtures' `poll_interval_ms` tuning for why this is
    still normally much faster in practice."""
    for _ in range(attempts):
        tasks = await tasks_for_instance(client, process_instance_id)
        if tasks:
            return tasks[0]
        await asyncio.sleep(delay_s)
    raise AssertionError(f"no user task ever appeared for process instance {process_instance_id}")


async def history_ended_activities(client: httpx.AsyncClient, process_instance_id: str) -> set[str]:
    resp = await client.get(
        "/history/activity-instance",
        params={"processInstanceId": process_instance_id, "activityType": "endEvent"},
    )
    resp.raise_for_status()
    items: list[dict[str, Any]] = resp.json()
    return {str(item["activityId"]) for item in items if item.get("activityId")}


async def wait_for_history_end(
    client: httpx.AsyncClient, process_instance_id: str, *, attempts: int = 30, delay_s: float = 0.5
) -> set[str]:
    for _ in range(attempts):
        ended = await history_ended_activities(client, process_instance_id)
        if ended:
            return ended
        await asyncio.sleep(delay_s)
    raise AssertionError(f"process instance {process_instance_id} never reached an end event")
