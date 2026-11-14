"""R-060 / F-092 (forensic audit): the publish-dedup flag is WIRED end to end, not only unit-true.

`test_events_publish_dedup.py` proves the handler dedups when it is GIVEN a ledger. These tests pin
every hop between the deployment flag and that handler, each one failing under the mutation that
removes it:

1. settings — `MAEZO_EXTASK_PUBLISH_DEDUP` is the alias, default OFF;
2. composition root — flag ON + `DATABASE_URL` builds the durable `PostgresDriverIdempotencyRegistry`
   for the deployment tenant; flag OFF builds none (dark);
3. `register_default_workers` -> `register_all_workers` -> `register_events_workers` forward the
   seam to the `operadora.events.publish` handler;
4. the forensic probe itself (SEAM-2 `probe_extask`, same task, same shape: the SAME `task_id`
   handled twice sequentially then twice concurrently through `WorkerHarness._handle`) yields ONE
   Kafka publish when the ledger is wired, and the historical 4 when it is not.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from tests.support.dedup_fakes import FakeDedupRegistry

_DSN = "postgresql://maezo@localhost:5432/maezo"


def _probe_task() -> ExternalTask:
    """The exact task the SEAM-2 `probe_extask` redelivers (synthetic, no PHI)."""
    return ExternalTask(
        task_id="AUDIT-PROBE-task-1",
        topic="operadora.events.publish",
        process_instance_id="p1",
        business_key="AUDIT-PROBE-BK",
        worker_id="w",
        variables={"event_topic": "agents.events.probe", "event_type": "probe.event"},
        process_definition_key="SP-OP-ESCALATION-001",
        activity_id="ST_PublishRequested",
    )


async def _redeliver(harness: WorkerHarness) -> None:
    """SEAM-2 redelivery pattern: sequential x2, then concurrent x2 (lock expiry / two workers)."""
    await harness._handle(_probe_task())
    await harness._handle(_probe_task())
    await asyncio.gather(harness._handle(_probe_task()), harness._handle(_probe_task()))


def _harness() -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport, audit = FakeWorkerTransport(), FakeAuditSink()
    return WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=audit), transport, audit


# -- 1. settings ---------------------------------------------------------------------------------


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_EXTASK_PUBLISH_DEDUP", raising=False)
    assert WorkerRuntimeSettings().extask_publish_dedup is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE"])
def test_flag_reads_its_env_alias(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("MAEZO_EXTASK_PUBLISH_DEDUP", raw)
    assert WorkerRuntimeSettings().extask_publish_dedup is True


# -- 2. composition root -------------------------------------------------------------------------


class _SpyHarness:
    def __init__(self, transport: Any, *, audit_sink: Any = None, **kwargs: Any) -> None:
        self.audit_sink = audit_sink

    @property
    def registered_topics(self) -> list[str]:
        return ["t"]

    async def run(self) -> None:
        await asyncio.Event().wait()


async def _bring_up_capturing_ledger(monkeypatch: pytest.MonkeyPatch, settings: WorkerRuntimeSettings) -> Any:
    import maezo.runtime.worker_runtime.service as svc
    from maezo.runtime.worker_runtime.service import WorkerState

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **seams: Any) -> None:
        captured.update(seams)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            await asyncio.gather(state.harness_task, return_exceptions=True)
    assert "publish_ledger" in captured, "the composition root must always pass the publish_ledger seam"
    return captured["publish_ledger"]


async def test_flag_on_builds_the_durable_ledger_for_the_deployment_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.platform.driver_idempotency import PostgresDriverIdempotencyRegistry

    settings = WorkerRuntimeSettings(DATABASE_URL=_DSN, MAEZO_EXTASK_PUBLISH_DEDUP=True)
    ledger = await _bring_up_capturing_ledger(monkeypatch, settings)

    assert isinstance(ledger, PostgresDriverIdempotencyRegistry)
    assert ledger.tenant == settings.tenant_id


async def test_flag_off_builds_no_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = WorkerRuntimeSettings(DATABASE_URL=_DSN, MAEZO_EXTASK_PUBLISH_DEDUP=False)
    assert await _bring_up_capturing_ledger(monkeypatch, settings) is None


# -- 3 + 4. bootstrap forwarding, proven by the forensic probe ----------------------------------


async def test_probe_publishes_once_when_ledger_rides_register_default_workers() -> None:
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness, transport, audit = _harness()
    kafka = FakeKafkaPublisher()
    register_default_workers(harness, tenant_id="amh", kafka=kafka, publish_ledger=FakeDedupRegistry())

    await _redeliver(harness)

    assert len(kafka.published) == 1
    assert len(audit.emitted) == 1
    # Every redelivery is still answered to the engine (deduplicated ones complete, never hang).
    assert len(transport.completed) == 4


async def test_probe_publishes_once_when_ledger_rides_register_events_workers() -> None:
    from maezo.tools.workers.events import register_events_workers

    harness, _transport, _audit = _harness()
    kafka = FakeKafkaPublisher()
    register_events_workers(harness, kafka, tenant_id="amh", publish_ledger=FakeDedupRegistry())

    await _redeliver(harness)

    assert len(kafka.published) == 1


async def test_probe_without_ledger_keeps_the_historical_behaviour() -> None:
    """Dark default: no ledger -> the SEAM-2 measurement (4 publishes) is unchanged byte for byte."""
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness, _transport, _audit = _harness()
    kafka = FakeKafkaPublisher()
    register_default_workers(harness, tenant_id="amh", kafka=kafka)

    await _redeliver(harness)

    assert len(kafka.published) == 4
