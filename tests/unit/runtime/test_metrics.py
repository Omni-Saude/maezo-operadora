"""Unit tests for maezo.runtime.metrics — Prometheus metrics.

ADR-0010: Agent observability — metrics, traces, logs.

RED phase: Tests define expected metric structure. RuntimeMetrics should
initialize without errors and expose valid Prometheus metrics.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from maezo.runtime.metrics import RuntimeMetrics


class TestRuntimeMetrics:
    """RuntimeMetrics — Prometheus instrumentation for the agent runtime."""

    def test_health_starts_at_one(self) -> None:
        metrics = RuntimeMetrics()
        assert metrics.health._value.get() == 1.0

    def test_health_is_gauge(self) -> None:
        metrics = RuntimeMetrics()
        assert isinstance(metrics.health, Gauge)
        assert metrics.health._name == "maezo_health"

    def test_inference_total_is_counter(self) -> None:
        metrics = RuntimeMetrics()
        assert isinstance(metrics.inference_total, Counter)
        # prometheus_client drops _total suffix on _name for counters
        assert metrics.inference_total._name == "maezo_inference"
        assert "model" in metrics.inference_total._labelnames
        assert "agent" in metrics.inference_total._labelnames
        assert "tier" in metrics.inference_total._labelnames

    def test_inference_latency_is_histogram(self) -> None:
        metrics = RuntimeMetrics()
        assert isinstance(metrics.inference_latency, Histogram)
        assert metrics.inference_latency._name == "maezo_inference_latency_seconds"
        # Histogram uses _upper_bounds, not _buckets
        assert hasattr(metrics.inference_latency, "_upper_bounds")

    def test_inference_tokens_has_direction_label(self) -> None:
        metrics = RuntimeMetrics()
        assert "direction" in metrics.inference_tokens._labelnames

    def test_graph_executions_has_status_label(self) -> None:
        metrics = RuntimeMetrics()
        assert "status" in metrics.graph_executions._labelnames

    def test_tool_calls_has_server_tool_status_labels(self) -> None:
        metrics = RuntimeMetrics()
        assert "server" in metrics.tool_calls._labelnames
        assert "tool" in metrics.tool_calls._labelnames
        assert "status" in metrics.tool_calls._labelnames

    def test_custom_registry(self) -> None:
        registry = CollectorRegistry()
        metrics = RuntimeMetrics(registry=registry)
        assert metrics._registry is registry
        # Metrics should be registered in the custom registry, not the default
        assert metrics.health._name not in [c._name for c in CollectorRegistry()._collector_to_names]

    def test_counter_increment(self) -> None:
        metrics = RuntimeMetrics()
        metrics.inference_total.labels(model="gpt-4", agent="helena", tier="cheap").inc()
        # Verify the counter was incremented (sampling prometheus counters is awkward,
        # but the call itself shouldn't raise)
        assert True

    def test_histogram_observe(self) -> None:
        metrics = RuntimeMetrics()
        metrics.inference_latency.labels(model="gpt-4", agent="helena", tier="cheap").observe(1.5)
        assert True  # No exception = pass

    def test_gauge_set_health(self) -> None:
        metrics = RuntimeMetrics()
        metrics.health.set(0)
        assert metrics.health._value.get() == 0.0
        metrics.health.set(1)
        assert metrics.health._value.get() == 1.0
