"""Observability setup: structlog + OpenTelemetry + Prometheus metrics (ADR-0010, ADR-0014).

Provides:
- setup_observability(): initializes structured logging and OpenTelemetry tracing
- WorkerBase instrumentation: worker_execution_time, worker_error_count
- MetricsCollector extensions for worker metrics (M11)

Design decisions (ADR-0010, ADR-0014):
- OTel with agent semantics: trace per conversation, span per node/tool/LLM call
- Worker metrics: execution time histogram + error counter with labels
- Prometheus + Grafana (dev local) / AMP + AMG (staging/prod)
- SigV4 remote-write in prod via OTel Collector
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from maezo.runtime.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Singleton metrics collector reference
# ---------------------------------------------------------------------------

_metrics_collector: MetricsCollector | None = None


def _get_metrics_collector() -> MetricsCollector:
    """Lazy-init the singleton MetricsCollector for worker instrumentation."""
    global _metrics_collector
    if _metrics_collector is None:
        from maezo.runtime.metrics import MetricsCollector

        _metrics_collector = MetricsCollector()
    return _metrics_collector


def get_metrics_collector() -> MetricsCollector:
    """Public accessor for the global metrics collector.

    Used by WorkerBase.run() to emit execution-time and error metrics.
    """
    return _get_metrics_collector()


# ---------------------------------------------------------------------------
# setup_observability
# ---------------------------------------------------------------------------


def setup_observability(
    service_name: str = "maezo-operadora",
    otlp_endpoint: str | None = None,
) -> TracerProvider:
    """Initialize structlog + OpenTelemetry for the Maezo platform.

    Args:
        service_name: Service name for OTel resource (default: "maezo-operadora").
        otlp_endpoint: OTLP gRPC endpoint for trace export. If None or empty,
                       traces are exported to a no-op backend (dev mode).
                       In prod, set to the OTel Collector endpoint.

    Returns:
        Configured TracerProvider instance.

    Environment variables used:
        OTEL_SERVICE_NAME: overrides service_name if set.
        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (overrides otlp_endpoint arg).
        MAEZO_LOG_LEVEL: structlog log level (default: INFO).

    ADR-0010: OTel with agent semantics. Traces are per-conversation/task;
    spans represent LangGraph nodes, tool calls, and LLM calls.
    ADR-0014: Dev local uses no-op exporter; staging/prod uses OTLP->AMP
    via OTel Collector with SigV4 remote-write.
    """
    # --- Structlog configuration ---
    log_level = os.environ.get("MAEZO_LOG_LEVEL", "INFO").upper()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(__name__).info(
        "observability_structlog_configured",
        log_level=log_level,
    )

    # --- OpenTelemetry configuration ---
    svc_name = os.environ.get("OTEL_SERVICE_NAME", service_name)

    resource = Resource.create({SERVICE_NAME: svc_name})

    provider = TracerProvider(resource=resource)

    # Determine exporter
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", otlp_endpoint or "")
    if endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        processor = BatchSpanProcessor(otlp_exporter)
        provider.add_span_processor(processor)
        logger.info(
            "observability_otel_exporter_configured",
            endpoint=endpoint,
        )
    else:
        logger.info(
            "observability_otel_noop_exporter",
            message="No OTLP endpoint configured; traces will not be exported (dev mode)",
        )

    trace.set_tracer_provider(provider)

    # Warm the metrics collector
    _get_metrics_collector()

    logger.info(
        "observability_setup_complete",
        service_name=svc_name,
        otlp_configured=bool(endpoint),
    )

    return provider


# ---------------------------------------------------------------------------
# Worker metrics recording helpers
# ---------------------------------------------------------------------------


def record_worker_execution(
    worker_name: str,
    topic: str,
    duration_seconds: float,
) -> None:
    """Record a worker execution time observation.

    Called by WorkerBase.run() after successful execution.

    Args:
        worker_name: Worker class name or identifier.
        topic: External task topic.
        duration_seconds: Execution duration in seconds.
    """
    collector = _get_metrics_collector()
    collector.worker_execution_time.labels(
        worker=worker_name,
        topic=topic,
    ).observe(duration_seconds)


def record_worker_error(
    worker_name: str,
    topic: str,
    error_type: str,
) -> None:
    """Record a worker error count.

    Called by WorkerBase.run() on execution failure.

    Args:
        worker_name: Worker class name or identifier.
        topic: External task topic.
        error_type: Exception class name (e.g., 'ValueError').
    """
    collector = _get_metrics_collector()
    collector.worker_error_count.labels(
        worker=worker_name,
        topic=topic,
        error_type=error_type,
    ).inc()
