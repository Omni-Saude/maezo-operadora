"""Prometheus metrics for agent runtime (ADR-0010, M11).

Exposes core metrics via prometheus_client:
- maezo_agent_latency_seconds (Histogram) — latency of agent interactions
- maezo_tool_calls_total (Counter) — total tool call invocations
- maezo_agent_errors_total (Counter) — total agent errors

M11 worker metrics:
- maezo_worker_execution_time_seconds (Histogram) — worker execution duration
- maezo_worker_error_count_total (Counter) — worker error count by type

T1.1 dispatch-outcome metrics (design docs/design/T1.1-runtime-spine.md §13, GAP-XOBS-4):
- maezo_worker_task_total (Counter) — external-task dispatch outcome by {tenant,topic,outcome}
- maezo_worker_task_duration_seconds (Histogram) — external-task handler wall-clock latency
"""

from __future__ import annotations

import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram

logger = structlog.get_logger(__name__)


class MetricsCollector:
    """Collector for Prometheus metrics: latency, error rate, tool call counters.

    Implements ADR-0010 observability requirements.
    Creates a dedicated CollectorRegistry so metrics don't collide
    with the default PROCESS_COLLECTOR or other libraries.

    M11: adds worker_execution_time and worker_error_count metrics
    with labels for worker name, topic, and error type.
    """

    def __init__(self) -> None:
        """Create the registry and register all metrics."""
        self._registry = CollectorRegistry(auto_describe=True)

        self._latency = Histogram(
            "maezo_agent_latency_seconds",
            "Agent interaction latency in seconds",
            registry=self._registry,
        )

        self._tool_calls = Counter(
            "maezo_tool_calls_total",
            "Total number of tool call invocations",
            registry=self._registry,
        )

        self._errors = Counter(
            "maezo_agent_errors_total",
            "Total number of agent errors",
            registry=self._registry,
        )

        # M11: Worker metrics
        self._worker_execution_time = Histogram(
            "maezo_worker_execution_time_seconds",
            "Worker execution time in seconds",
            labelnames=["worker", "topic"],
            registry=self._registry,
        )

        self._worker_error_count = Counter(
            "maezo_worker_error_count_total",
            "Worker error count by error type",
            labelnames=["worker", "topic", "error_type"],
            registry=self._registry,
        )

        # T1.1: external-task dispatch-outcome metrics (design §13, GAP-XOBS-4). Emitted by
        # `WorkerHarness._handle` (tools/workers/harness.py) on every terminal branch — distinct
        # from worker_execution_time/error_count above (those are per-worker, emitted by
        # `WorkerBase.run()`; these are per-dispatch, emitted once per external task regardless of
        # whether the topic is served by a WorkerBase or a raw handler).
        self._worker_task_total = Counter(
            "maezo_worker_task_total",
            "External-task dispatch outcome count",
            labelnames=["tenant", "topic", "outcome"],
            registry=self._registry,
        )

        self._worker_task_duration = Histogram(
            "maezo_worker_task_duration_seconds",
            "External-task handler wall-clock latency in seconds",
            labelnames=["tenant", "topic", "outcome"],
            registry=self._registry,
        )

        logger.info("metrics_collector_initialized")

    @property
    def registry(self) -> CollectorRegistry:
        """Return the dedicated Prometheus CollectorRegistry."""
        return self._registry

    @property
    def latency(self) -> Histogram:
        """Histogram for agent interaction latency."""
        return self._latency

    @property
    def tool_calls(self) -> Counter:
        """Counter for tool call invocations."""
        return self._tool_calls

    @property
    def errors(self) -> Counter:
        """Counter for agent errors."""
        return self._errors

    @property
    def worker_execution_time(self) -> Histogram:
        """Histogram for worker execution time (M11).

        Labels: worker (class name), topic (external task topic).
        """
        return self._worker_execution_time

    @property
    def worker_error_count(self) -> Counter:
        """Counter for worker errors by type (M11).

        Labels: worker (class name), topic (external task topic),
        error_type (Exception class name).
        """
        return self._worker_error_count

    @property
    def worker_task_total(self) -> Counter:
        """Counter for external-task dispatch outcomes (T1.1, GAP-XOBS-4).

        Labels: tenant, topic (external task topic), outcome (one of
        `harness.WORKER_TASK_OUTCOMES`: completed | bpmn_error | failed | incident).
        """
        return self._worker_task_total

    @property
    def worker_task_duration(self) -> Histogram:
        """Histogram for external-task handler wall-clock latency (T1.1, GAP-XOBS-4).

        Same label set as `worker_task_total`.
        """
        return self._worker_task_duration
