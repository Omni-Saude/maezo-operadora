"""Metrics — Prometheus metrics for the agent runtime.

ADR-0010: Agent observability — metrics, traces, logs.
ADR-0014: Prometheus/Grafana in-cluster for dev.

Exposes:
- Inference call count, latency, token usage (per model, per agent)
- Graph execution count, latency, errors
- Tool call count, latency, errors
- Health check gauge
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class RuntimeMetrics:
    """Prometheus metrics for the MAEZO agent runtime."""

    def __init__(self, *, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry

        # Inference metrics (per model, per agent)
        self.inference_total = Counter(
            "maezo_inference_total",
            "Total inference calls",
            ["model", "agent", "tier"],
            registry=registry,
        )
        self.inference_latency = Histogram(
            "maezo_inference_latency_seconds",
            "Inference call latency",
            ["model", "agent", "tier"],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
            registry=registry,
        )
        self.inference_tokens = Counter(
            "maezo_inference_tokens_total",
            "Total tokens consumed",
            ["model", "agent", "direction"],  # direction: input/output
            registry=registry,
        )

        # Graph execution metrics
        self.graph_executions = Counter(
            "maezo_graph_executions_total",
            "Total graph executions",
            ["agent", "status"],  # status: success/error
            registry=registry,
        )
        self.graph_latency = Histogram(
            "maezo_graph_latency_seconds",
            "Graph execution latency",
            ["agent"],
            buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
            registry=registry,
        )

        # Tool call metrics (per server, per tool)
        self.tool_calls = Counter(
            "maezo_tool_calls_total",
            "Total tool calls",
            ["server", "tool", "status"],
            registry=registry,
        )
        self.tool_latency = Histogram(
            "maezo_tool_latency_seconds",
            "Tool call latency",
            ["server", "tool"],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
            registry=registry,
        )

        # Health
        self.health = Gauge(
            "maezo_health",
            "Runtime health (1 = healthy, 0 = unhealthy)",
            registry=registry,
        )
        self.health.set(1)
