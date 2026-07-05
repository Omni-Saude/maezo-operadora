"""Unit tests for maezo.platform.observability (ADR-0010, ADR-0014).

TDD London School — tests written before implementation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# setup_observability tests
# ---------------------------------------------------------------------------


def test_setup_observability_initializes_without_error() -> None:
    """setup_observability() should run without raising exceptions in dev mode."""
    from maezo.platform.observability import setup_observability

    # Should not raise — dev mode with OTLP exporter disabled
    setup_observability(service_name="test-service", otlp_endpoint=None)


def test_setup_observability_returns_tracer_provider() -> None:
    """setup_observability() should return a configured TracerProvider."""
    from opentelemetry.sdk.trace import TracerProvider

    from maezo.platform.observability import setup_observability

    provider = setup_observability(service_name="test-service", otlp_endpoint=None)
    assert isinstance(provider, TracerProvider)


def test_setup_observability_configures_service_name() -> None:
    """setup_observability() should set the service.name resource attribute."""
    from maezo.platform.observability import setup_observability

    provider = setup_observability(service_name="maezo-test", otlp_endpoint=None)
    resource = provider.resource
    attributes = resource.attributes

    assert attributes["service.name"] == "maezo-test"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Worker metrics tests
# ---------------------------------------------------------------------------


def test_worker_metrics_registered_in_collector() -> None:
    """MetricsCollector should expose worker_execution_time and worker_error_count."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()
    metric_names = {m.name for m in collector.registry.collect()}

    # Base metrics from ADR-0010
    assert "maezo_agent_latency_seconds" in metric_names

    # Worker-specific metrics (M11)
    assert "maezo_worker_execution_time_seconds" in metric_names
    assert "maezo_worker_error_count" in metric_names


def test_worker_execution_time_histogram() -> None:
    """worker_execution_time_seconds histogram should accept observations."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    collector.worker_execution_time.labels(  # type: ignore[attr-defined]
        worker="test_worker", topic="operadora.test"
    ).observe(0.05)
    collector.worker_execution_time.labels(  # type: ignore[attr-defined]
        worker="test_worker", topic="operadora.test"
    ).observe(0.15)

    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_execution_time" in sample.name:
                samples.append((sample.name, sample.labels, sample.value))

    assert len(samples) > 0
    # Verify we have observations
    sum_vals = [v for n, lbl, v in samples if n.endswith("_sum")]
    count_vals = [v for n, lbl, v in samples if n.endswith("_count")]
    assert sum(sum_vals) == pytest.approx(0.20, rel=0.01)
    assert sum(count_vals) == 2.0


def test_worker_error_count_counter() -> None:
    """worker_error_count counter should increment correctly."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    collector.worker_error_count.labels(  # type: ignore[attr-defined]
        worker="failing_worker", topic="operadora.fail", error_type="RuntimeError"
    ).inc()
    collector.worker_error_count.labels(  # type: ignore[attr-defined]
        worker="failing_worker", topic="operadora.fail", error_type="RuntimeError"
    ).inc()

    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_error_count" in sample.name:
                samples.append((sample.name, sample.labels, sample.value))

    total = sum(v for n, lbl, v in samples if n.endswith("_total") and lbl.get("worker") == "failing_worker")
    assert total == 2.0


# ---------------------------------------------------------------------------
# WorkerBase metrics emission tests
# ---------------------------------------------------------------------------


def test_worker_base_emits_execution_time_metric() -> None:
    """WorkerBase.run() should emit worker_execution_time_seconds metric."""
    from maezo.runtime.metrics import MetricsCollector
    from maezo.tools.workers.base import WorkerBase

    collector = MetricsCollector()

    class MetricEmittingWorker(WorkerBase):
        def execute(self, process_vars: dict) -> dict:
            return {"status": "ok"}

    worker = MetricEmittingWorker(topic="operadora.test.metrics")

    # Patch to use our collector
    with patch(
        "maezo.platform.observability._get_metrics_collector",
        return_value=collector,
    ):
        result = worker.run({"tenant_id": "amh"})

    assert result == {"status": "ok"}

    # Check that execution time was recorded
    metric_names = {m.name for m in collector.registry.collect()}
    assert "maezo_worker_execution_time_seconds" in metric_names


def test_worker_base_emits_error_count_on_failure() -> None:
    """WorkerBase.run() should emit worker_error_count on failure."""
    from maezo.runtime.metrics import MetricsCollector
    from maezo.tools.workers.base import WorkerBase

    collector = MetricsCollector()

    class AlwaysFailingWorker(WorkerBase):
        def execute(self, process_vars: dict) -> dict:
            raise ValueError("simulated worker failure")

    worker = AlwaysFailingWorker(topic="operadora.test.error", max_retries=1)

    with (
        patch(
            "maezo.platform.observability._get_metrics_collector",
            return_value=collector,
        ),
        pytest.raises(ValueError, match="simulated worker failure"),
    ):
        worker.run({"tenant_id": "amh"})

    # Check error count was recorded
    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "worker_error_count" in sample.name:
                samples.append(sample)

    total = sum(
        s.value for s in samples if s.name.endswith("_total") and s.labels.get("error_type") == "ValueError"
    )
    assert total >= 1.0
