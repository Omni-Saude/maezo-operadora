"""Unit tests for maezo.runtime.metrics (ADR-0010)."""

import pytest
from prometheus_client import CollectorRegistry


def test_metrics_registry_exists() -> None:
    """MetricsCollector should register Prometheus metrics in a dedicated registry."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    # Registry should be a valid CollectorRegistry
    assert isinstance(collector.registry, CollectorRegistry)

    # All three required metrics should be present.
    # Note: prometheus_client internally strips the _total suffix from Counter
    # metric names (it's auto-appended at scrape time, not stored).
    metric_names = {m.name for m in collector.registry.collect()}
    assert "maezo_agent_latency_seconds" in metric_names
    assert "maezo_tool_calls" in metric_names
    assert "maezo_agent_errors" in metric_names


def test_metrics_counter_increment() -> None:
    """Counters should correctly increment and expose their value."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    # Increment tool calls twice
    collector.tool_calls.inc()
    collector.tool_calls.inc()

    # Increment errors once
    collector.errors.inc()

    # Verify counter values via the registry
    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            samples.append((sample.name, sample.value))

    tool_calls_total = sum(v for n, v in samples if n.endswith("_total") and "tool_calls" in n)
    errors_total = sum(v for n, v in samples if n.endswith("_total") and "errors" in n)

    assert tool_calls_total == 2.0
    assert errors_total == 1.0


def test_sla_alert_human_task_counter_increments_by_alert_domain_and_outcome() -> None:
    """R-104/WP-ALERTA-SLA-CANAL: the Counter that
    `notifications_bridge._record_sla_alert_outcome` increments exists with the CLOSED label set
    it declares. That the bridge really emits it is proven end-to-end in
    `tests/unit/platform/integrations/test_notifications_bridge.py` (this test only pins the
    metric's shape)."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    collector.sla_alert_human_task.labels(alert_domain="recurso", outcome="escalated").inc()
    collector.sla_alert_human_task.labels(alert_domain="recurso", outcome="escalated").inc()
    collector.sla_alert_human_task.labels(alert_domain="unknown", outcome="unrecognised_shape").inc()

    samples = {
        (sample.labels.get("outcome"), sample.labels.get("alert_domain")): sample.value
        for metric in collector.registry.collect()
        for sample in metric.samples
        if sample.name == "maezo_sla_alert_human_task_total"
    }

    assert samples[("escalated", "recurso")] == 2.0
    assert samples[("unrecognised_shape", "unknown")] == 1.0


def test_metrics_latency_histogram() -> None:
    """Histogram should accept observation values."""
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()

    # Observe some latencies
    collector.latency.observe(0.1)
    collector.latency.observe(0.5)
    collector.latency.observe(1.2)

    # Verify histogram has observations
    samples = []
    for metric in collector.registry.collect():
        for sample in metric.samples:
            if "latency" in sample.name:
                samples.append((sample.name, sample.value))

    # Sum should be total of observations
    total = sum(v for n, v in samples if n.endswith("_sum"))
    count = sum(v for n, v in samples if n.endswith("_count"))

    assert total == pytest.approx(1.8, rel=0.01)
    assert count == 3.0
