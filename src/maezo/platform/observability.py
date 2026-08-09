"""Observability setup: structlog + OpenTelemetry + Prometheus metrics (ADR-0010, ADR-0014).

Provides:
- setup_observability(): initializes structured logging and OpenTelemetry tracing
- WorkerBase instrumentation: worker_execution_time, worker_error_count
- MetricsCollector extensions for worker metrics (M11)
- record_llm_token_usage(): LLM token-metering (T8) — COUNTS ONLY, never a cost value

Design decisions (ADR-0010, ADR-0014):
- OTel with agent semantics: trace per conversation, span per node/tool/LLM call
- Worker metrics: execution time histogram + error counter with labels
- Prometheus + Grafana (dev local) / AMP + AMG (staging/prod)
- SigV4 remote-write in prod via OTel Collector
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

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
# Business-key log scrubbing (DL-0043 leg (c)) — POLICY-GATED, inert by default
# ---------------------------------------------------------------------------


def _build_key_scrubber() -> Any | None:
    """Build the `BusinessKeyScrubber` structlog processor, or None when the policy is inert.

    DL-0043 leg (c). `LogScrubber` has existed in `maezo.gateway` since ADR-0006 but was NEVER
    wired into `structlog.configure` — its own docstring says so. This is the wire, and it is
    deliberately gated on `spec/policies/privacy/phi-business-key-remediation.yaml`: the reason
    the scrubber matters here is that business keys can carry `matricula_beneficiario`, and
    whether that gets pseudonymized is the OWNER's call, not this function's.

    KNOWN GAP, RECORDED NOT PAPERED OVER: `setup_observability` has NO production caller today
    (repo-wide sweep — only tests call it), so the daemons run structlog's DEFAULT configuration,
    which renders every kwarg to stdout just the same. Installing the scrubber here is the right
    SEAM, but ratifying `scrub_only` without also invoking this bootstrap from each daemon's
    composition root would close the Kafka/mirror egress and NOT the log egress. The manifest
    carries this as an explicit ratification pre-requisite; wiring the composition roots is a
    separate change because it also changes log FORMATTING, which is not byte-identical and
    therefore not something this flag may do silently.

    Returns None (no processor, byte-identical logging) whenever the effective policy mode is
    `off` — which is the shipped state. When the policy IS active the pseudonymizer is built
    through `Pseudonymizer.from_settings`, so the ADR-0035 fail-closed key rule is inherited: a
    runtime with no `PHI_HMAC_KEY` raises `PseudonymizerKeyMissingError` here rather than
    installing a scrubber that would pseudonymize with a publicly-known dev key.

    WHERE THAT RAISE ACTUALLY SURFACES — NOT here, and not at boot, TODAY. It is a direct
    consequence of the gap recorded just above: with no composition root calling
    `setup_observability`, nothing in production ever reaches this function, so this raise is
    UNREACHABLE in production and no daemon "fails at boot" on it. The one production-reachable
    construction of the same pseudonymizer is `key_scrubber.egress_message_key`, called by the
    `operadora.events.publish` worker handler — so an operator who ratifies `scrub_only` without
    provisioning `PHI_HMAC_KEY` finds out on the FIRST publish external task after the restart,
    as a raw `PseudonymizerKeyMissingError` on the harness retry/incident ladder (that handler
    hoists the call out of its publish-`try` precisely so the fault is not re-labelled as a Kafka
    publish failure). The boot-time reading becomes true only once a composition root wires this
    bootstrap — which is the same pre-requisite the manifest already carries.
    """
    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # noqa: PLC0415 — lazy

    policy = phi_key_policy()
    if not policy.scrubbing_enabled:
        return None

    from maezo.platform.privacy.key_scrubber import (  # noqa: PLC0415 — lazy
        BusinessKeyScrubber,
        egress_pseudonymizer,
    )

    return BusinessKeyScrubber(egress_pseudonymizer())


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
    scrubber = _build_key_scrubber()
    processors: list[Any] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if scrubber is not None:
        # Scrub LAST before rendering: every earlier processor may still ADD fields to the
        # event dict, so a scrubber placed before them would miss whatever they contribute.
        processors.append(scrubber)
    processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(__name__).info(
        "observability_structlog_configured",
        log_level=log_level,
        key_scrubber_installed=scrubber is not None,
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


def record_worker_task_outcome(
    *,
    tenant: str,
    topic: str,
    outcome: str,
    duration_seconds: float | None = None,
) -> None:
    """Record an external-task dispatch outcome (T1.1, GAP-XOBS-4).

    Called by `WorkerHarness._handle` (tools/workers/harness.py) on each terminal branch.
    Increments `maezo_worker_task_total` and, when `duration_seconds` is known, observes
    `maezo_worker_task_duration_seconds` — both with the same bounded label set.

    `outcome` MUST be one of `harness.WORKER_TASK_OUTCOMES` (completed | bpmn_error | failed |
    incident). `topic` is the external-task topic (bounded by the worker registry). NEVER pass
    task_id / business_key / process_instance_id here — those are per-instance identifiers and
    never belong on a metric label (ADR-0010).

    This is the raw typed helper. The harness wraps it in a defensive guard (never raises into
    dispatch) — see `harness._emit_worker_task_outcome`.
    """
    collector = _get_metrics_collector()
    collector.worker_task_total.labels(tenant=tenant, topic=topic, outcome=outcome).inc()
    if duration_seconds is not None:
        collector.worker_task_duration.labels(tenant=tenant, topic=topic, outcome=outcome).observe(
            duration_seconds
        )


def record_phi_business_key_mint(*, family: str, modo: str, anchor: str) -> None:
    """Record ONE business-key mint by derivation anchor (DL-0043 leg (c) shadow telemetry).

    Called by `maezo.tools.workers.base.mint_contract_business_key` — the single composer every
    CANCEL/INAD business key is MINTED through. Increments
    `maezo_phi_business_key_mint_total{family, modo, anchor}`.

    THAT CLAIM, ENUMERATED (it was false before this repair, and a claim of completeness has to
    be checkable). The six mint sites, all routed through `mint_contract_business_key`:
    `inadimplencia._cancel_business_key`, `fernando.graph._business_key`,
    `fraude._cancel_business_key`, `fraude._inadimplencia_business_key`,
    `notification_bridge._cancel_business_key`, `notification_bridge._inadimplencia_business_key`.
    The last four called the bare formatter `base.contract_business_key` directly until this
    repair, so the `anchor="contrato"` series under-counted by every fraude-handoff and
    bridge-rule start. Pinned by `test_phi_key_flag_behavior.py::
    test_no_cancel_inad_key_is_minted_outside_the_shared_mint_composer`.

    NOT counted, deliberately: `base.contract_business_key_forms`, which composes the same
    strings to QUERY for pre-existing instances (the anti-dupla-terminacao guard). It mints
    nothing, so counting it would inflate the very number the owner ratifies against.

    THE POINT. While the remediation policy is `off` (today), the `anchor="matricula"` series is
    a running count of keys that WOULD have been pseudonymized under `pseudo_keys` — i.e. how
    many individual/familiar-plan beneficiaries had their `matricula_beneficiario` minted into a
    durable business key. That count, and not an argument, is what the owner ratifies against.

    CONTENT-FREE. All three labels are closed vocabularies (`family` in {CANCEL, INAD}, `modo`
    in {off, scrub_only, pseudo_keys}, `anchor` in {contrato, matricula, pseudo}). NEVER pass a
    tenant id, a business key, or a matricula here — same rule `record_worker_task_outcome` and
    `record_llm_token_usage` document above. A per-instance identifier on this metric would
    recreate, in Prometheus, exactly the leak the metric exists to measure.

    This is the raw typed helper; the caller wraps it in a defensive guard (telemetry must never
    raise into a key mint) — see `base._record_key_mint`.
    """
    collector = _get_metrics_collector()
    collector.phi_business_key_mint.labels(family=family, modo=modo, anchor=anchor).inc()


def record_llm_token_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record LLM token consumption (T8, token-metering).

    Called by `maezo.runtime.inference._emit_llm_token_usage` — reached only from
    `AnthropicInferenceProvider.generate()`, the single seam every REAL LLM response
    passes through (ADR-0009). Increments `maezo_llm_tokens_total` once for the
    `token_type="input"` series and once for `token_type="output"`, each by the actual
    token count via `Counter.inc(amount)` (not a plain +1), so the metric reflects true
    consumption volume rather than a call count.

    `provider`/`model` are deliberately the only labels: a small, bounded set (one
    provider today, a handful of Anthropic model ids), safe Prometheus cardinality per
    ADR-0010. Per-instance correlation (tenant/agent/thread) is NEVER a label here —
    same rule `record_worker_task_outcome` documents above for task/business-key
    identifiers; that granularity belongs in the structured log line the caller also
    emits (`maezo.runtime.inference._emit_llm_token_usage`'s `llm_token_usage` event).

    COUNTS ONLY — this function never computes or emits a cost/price value. Pricing is a
    finance-gated human decision (see the EXTENSION POINT note in
    `maezo.runtime.inference._emit_llm_token_usage`).

    This is the raw typed helper — intentionally NOT wrapped in a defensive try/except
    here (contrast `record_worker_task_outcome`'s caller, `_emit_worker_task_outcome`):
    the caller (`_emit_llm_token_usage`) already wraps its entire body, including this
    call, in a single broad guard, so a second guard here would be redundant.
    """
    collector = _get_metrics_collector()
    collector.llm_tokens.labels(provider=provider, model=model, token_type="input").inc(input_tokens)
    collector.llm_tokens.labels(provider=provider, model=model, token_type="output").inc(output_tokens)
