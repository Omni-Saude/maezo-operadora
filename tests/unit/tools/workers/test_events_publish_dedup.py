"""F-092 (forensic audit): a redelivered external task must not publish its event twice.

The generic `operadora.events.publish` handler, given a `PublishLedger` (ADR-0024 claim/commit),
publishes once per `task_id`: a redelivery after the effect is sealed completes without a second
publish; a redelivery while the first attempt is in flight is a retryable failure, never a
fabricated "published"; a failed publish releases the claim so a retry gets a real second chance.
Without a ledger nothing changes (the seam ships dark: `MAEZO_EXTASK_PUBLISH_DEDUP`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.tools.workers.events import make_publish_event_handler, publish_ledger_key
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerFailureError
from tests.support.dedup_fakes import FakeDedupRegistry


def _task(task_id: str = "t-1") -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic="operadora.events.publish",
        process_instance_id="p-1",
        business_key="CONTAS-amh-G1",
        worker_id="w-1",
        variables={
            "event_topic": "agents.events.contas",
            "event_payload_vars": "tenant_id",
            "tenant_id": "amh",
        },
        process_definition_key="SP-OP-CONTAS-001",
        activity_id="ST_PublishCompleted",
    )


class _KafkaFora:
    async def publish(
        self, topic: str, value: dict[str, Any], *, key: str | None = None, best_effort: bool | None = None
    ) -> bool:
        raise RuntimeError("broker fora")


async def test_redelivery_after_success_publishes_once() -> None:
    kafka, ledger = FakeKafkaPublisher(), FakeDedupRegistry()
    handler = make_publish_event_handler(kafka, deployment_tenant_id="amh", publish_ledger=ledger)
    first = await handler(_task())
    second = await handler(_task())
    assert len(kafka.published) == 1
    assert first["event_published"] is True and "event_publish_deduplicated" not in first
    assert second == {
        "event_published": True,
        "event_publish_deduplicated": True,
        "event_topic": "agents.events.contas",
    }


async def test_concurrent_redeliveries_publish_once() -> None:
    kafka, ledger = FakeKafkaPublisher(), FakeDedupRegistry()
    handler = make_publish_event_handler(kafka, deployment_tenant_id="amh", publish_ledger=ledger)
    results = await asyncio.gather(*(handler(_task()) for _ in range(4)), return_exceptions=True)
    assert len(kafka.published) == 1
    assert all(isinstance(r, (dict, WorkerFailureError)) for r in results)


async def test_redelivery_while_first_attempt_in_flight_is_retryable_not_published() -> None:
    kafka, ledger = FakeKafkaPublisher(), FakeDedupRegistry()
    await ledger.claim(publish_ledger_key("amh", "t-1"))  # another attempt holds the claim
    handler = make_publish_event_handler(kafka, deployment_tenant_id="amh", publish_ledger=ledger)
    with pytest.raises(WorkerFailureError):
        await handler(_task())
    assert kafka.published == []


async def test_failed_publish_releases_the_claim() -> None:
    ledger = FakeDedupRegistry()
    with pytest.raises(RuntimeError):
        await make_publish_event_handler(_KafkaFora(), deployment_tenant_id="amh", publish_ledger=ledger)(
            _task()
        )
    assert publish_ledger_key("amh", "t-1") not in ledger.rows
    kafka = FakeKafkaPublisher()
    await make_publish_event_handler(kafka, deployment_tenant_id="amh", publish_ledger=ledger)(_task())
    assert len(kafka.published) == 1


async def test_distinct_tasks_are_independent() -> None:
    kafka, ledger = FakeKafkaPublisher(), FakeDedupRegistry()
    handler = make_publish_event_handler(kafka, deployment_tenant_id="amh", publish_ledger=ledger)
    await handler(_task("t-1"))
    await handler(_task("t-2"))
    assert len(kafka.published) == 2


async def test_without_ledger_behaviour_is_unchanged() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_publish_event_handler(kafka, deployment_tenant_id="amh")
    await handler(_task())
    await handler(_task())
    assert len(kafka.published) == 2
