"""WORKER-METRICS-COVERAGE: the M11 per-worker metrics cover RAW handlers too, and only once.

THE GAP THIS PINS. `maezo_worker_execution_time_seconds` and `maezo_worker_error_count_total` are
what `MaezoSLAWorkerLatencyHigh`, `MaezoSLAWorkerErrorRateHigh` and `MaezoWorkerCrashLoop` are
built on (`deploy/observability/alert-rules.yml:18-33,:56-73,:81-95`). `WorkerBase.run()` was the
ONLY emitter, so every topic served by a raw `harness.register()` handler — `events`,
`stratify_risk`, `stop_processing`, `proactive_contact`, `notify_sla_risk`,
`update_monitoring_plan`, and the raw handlers in recurso/lgpd/escalation — was invisible to all
three alerts. `programa.py` disclosed it for its own four topics; the gap was much wider.

THE TWO HALVES, both asserted here, because either alone would be a false repair:
  * COVERAGE — a raw handler now emits both metrics, with the same label sets a `WorkerBase` topic
    emits, from the harness dispatch chokepoint.
  * NO DOUBLE COUNT — a `WorkerBase` topic emits them EXACTLY ONCE (from `WorkerBase.run()`), not
    twice. Doubling would silently corrupt the p95 the latency alert reads, which is worse than
    the gap it replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.platform.observability import get_metrics_collector
from maezo.tools.workers.base import WorkerBase
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerHarness,
    derive_handler_name,
)

_RAW_TOPIC = "operadora.metricas.raw_topic"
_BASE_TOPIC = "operadora.metricas.base_topic"


def _task(*, task_id: str = "task-1", topic: str = _RAW_TOPIC) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables={},
        retries=None,
    )


def _harness() -> WorkerHarness:
    return WorkerHarness(
        FakeWorkerTransport(),
        worker_id="w-1",
        tenant="amh",
        audit_sink=FakeAuditSink(),
    )


def _execution_count(worker: str, topic: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_worker_execution_time_seconds_count", {"worker": worker, "topic": topic}
    )
    return float(value or 0.0)


def _error_count(worker: str, topic: str, error_type: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_worker_error_count_total",
        {"worker": worker, "topic": topic, "error_type": error_type},
    )
    return float(value or 0.0)


# -------------------------------------------------------------------------------------------------
# The `worker` label for a raw handler
# -------------------------------------------------------------------------------------------------


def test_the_raw_handler_worker_label_is_the_factory_not_the_closure() -> None:
    """`<module leaf>.<factory>` — stable across restarts, one value per topic, no task data.

    The inner closure is named `_handler` in a dozen unrelated modules, so labelling by it would
    collapse every raw topic onto one series. The FACTORY is the meaningful identity.
    """

    def make_stratify_risk_handler() -> Any:
        async def _handler(task: ExternalTask) -> None:
            return None

        return _handler

    assert derive_handler_name(make_stratify_risk_handler()).endswith("make_stratify_risk_handler"), (
        derive_handler_name(make_stratify_risk_handler())
    )


def test_the_real_programa_raw_handlers_get_distinct_bounded_labels() -> None:
    """Against the SHIPPED handlers, not a synthetic one — the gap was reported on these topics."""
    from maezo.tools.workers import programa

    names = {
        derive_handler_name(programa.make_stratify_risk_handler(None)),
        derive_handler_name(programa.make_stop_processing_handler(None)),
    }
    assert len(names) == 2, names
    for name in names:
        assert name.startswith("programa."), name
        # Bounded, non-PHI: no task id, no business key, no process instance can appear in it.
        assert "bk-" not in name and "proc-" not in name, name


# -------------------------------------------------------------------------------------------------
# Coverage: a raw handler emits both metrics
# -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_raw_handler_success_observes_worker_execution_time() -> None:
    harness = _harness()

    async def raw_ok_handler(task: ExternalTask) -> dict[str, Any]:
        return {"ok": True}

    harness.register(_RAW_TOPIC, raw_ok_handler)
    label = derive_handler_name(raw_ok_handler)

    before = _execution_count(label, _RAW_TOPIC)
    await harness.handle_task(_task())
    after = _execution_count(label, _RAW_TOPIC)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_raw_handler_failure_increments_worker_error_count_with_the_exception_type() -> None:
    harness = _harness()

    async def raw_failing_handler(task: ExternalTask) -> dict[str, Any]:
        raise ValueError("entrada invalida")

    harness.register(_RAW_TOPIC, raw_failing_handler)
    label = derive_handler_name(raw_failing_handler)

    before = _error_count(label, _RAW_TOPIC, "ValueError")
    await harness.handle_task(_task(task_id="task-err"))
    after = _error_count(label, _RAW_TOPIC, "ValueError")

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_failed_raw_handler_does_not_also_observe_execution_time() -> None:
    """Mirrors `WorkerBase.run()`: the histogram is a SUCCESS latency, the counter is the failure.

    If a failure also observed the histogram, `MaezoSLAWorkerErrorRateHigh`'s denominator
    (`rate(maezo_worker_execution_time_seconds_count[5m])`) would grow with the numerator and the
    error rate would be permanently understated.
    """
    harness = _harness()

    async def raw_boom_handler(task: ExternalTask) -> dict[str, Any]:
        raise RuntimeError("engine indisponivel")

    harness.register(_RAW_TOPIC, raw_boom_handler)
    label = derive_handler_name(raw_boom_handler)

    before = _execution_count(label, _RAW_TOPIC)
    await harness.handle_task(_task(task_id="task-boom"))
    after = _execution_count(label, _RAW_TOPIC)

    assert after == before, (before, after)


@pytest.mark.asyncio
async def test_a_task_for_an_unregistered_topic_is_counted_not_silently_lost() -> None:
    """The `handler is None` branch already reports a fail-closed incident; now it is also visible
    in the metric the crash-loop alert reads."""
    harness = _harness()

    before = _error_count("raw_handler", "operadora.metricas.desconhecido", "UnregisteredTopic")
    await harness.handle_task(_task(task_id="task-unreg", topic="operadora.metricas.desconhecido"))
    after = _error_count("raw_handler", "operadora.metricas.desconhecido", "UnregisteredTopic")

    assert after == before + 1.0, (before, after)


# -------------------------------------------------------------------------------------------------
# No double count: a WorkerBase topic still emits exactly once
# -------------------------------------------------------------------------------------------------


class _OkWorker(WorkerBase):
    def __init__(self) -> None:
        super().__init__(topic=_BASE_TOPIC)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


@pytest.mark.asyncio
async def test_a_worker_base_topic_observes_execution_time_exactly_once() -> None:
    """The whole reason the harness emits for raw topics ONLY.

    `WorkerBase.run()` observes its own duration. A second observation from the harness would put
    TWO points into the histogram for one task — the handler body and the whole dispatch — and
    `MaezoSLAWorkerLatencyHigh` reads a p95 out of exactly that histogram.
    """
    harness = _harness()
    worker = _OkWorker()
    harness.register_worker(worker)

    before = _execution_count("_OkWorker", _BASE_TOPIC)
    await harness.handle_task(_task(task_id="task-base", topic=_BASE_TOPIC))
    after = _execution_count("_OkWorker", _BASE_TOPIC)

    assert after == before + 1.0, (before, after)
    # And the harness did NOT open a second series under a raw-handler label for the same topic.
    assert _execution_count("harness.register_worker", _BASE_TOPIC) == 0.0


@pytest.mark.asyncio
async def test_re_registering_a_worker_base_topic_raw_moves_it_back_to_harness_emission() -> None:
    """ "Last registration wins" has to hold for the metric identity too.

    Otherwise a topic re-registered raw would still be treated as `WorkerBase`-emitted and would
    emit NOTHING — a silent hole opened by an ordinary re-registration.
    """
    harness = _harness()
    harness.register_worker(_OkWorker())

    async def replacement_handler(task: ExternalTask) -> dict[str, Any]:
        return {"ok": True}

    harness.register(_BASE_TOPIC, replacement_handler)
    label = derive_handler_name(replacement_handler)

    before = _execution_count(label, _BASE_TOPIC)
    await harness.handle_task(_task(task_id="task-rereg", topic=_BASE_TOPIC))
    after = _execution_count(label, _BASE_TOPIC)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_both_handler_kinds_report_under_the_same_metric_and_label_names() -> None:
    """Side by side: the alert cannot tell a raw topic from a `WorkerBase` topic, which is the point.

    Same metric name, same `{worker, topic}` label KEYS — only the values differ. An alert grouping
    by `worker`/`topic` therefore sees both kinds without any rule change (and `deploy/` is
    owner-gated, so a rule change was never an option here).
    """
    harness = _harness()
    harness.register_worker(_OkWorker())

    async def raw_ok_handler(task: ExternalTask) -> dict[str, Any]:
        return {"ok": True}

    harness.register(_RAW_TOPIC, raw_ok_handler)
    raw_label = derive_handler_name(raw_ok_handler)

    await harness.handle_task(_task(task_id="task-a", topic=_BASE_TOPIC))
    await harness.handle_task(_task(task_id="task-b", topic=_RAW_TOPIC))

    assert _execution_count("_OkWorker", _BASE_TOPIC) >= 1.0
    assert _execution_count(raw_label, _RAW_TOPIC) >= 1.0
