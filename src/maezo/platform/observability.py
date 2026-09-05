"""Observability setup: structlog + OpenTelemetry + Prometheus metrics (ADR-0010, ADR-0014).

Provides:
- setup_observability(): initializes structured logging and OpenTelemetry tracing
- bootstrap_observability(): the COMPOSITION-ROOT wrapper around it (AF-13) — isolated,
  never propagates, and returns the `ObservabilityStatus` each daemon's readiness check reads
- WorkerBase instrumentation: worker_execution_time, worker_error_count
- MetricsCollector extensions for worker metrics (M11)
- record_llm_token_usage(): LLM token-metering (T8) — COUNTS ONLY, never a cost value
- record_tool_call()/record_agent_error(): the two counters `deploy/observability/alert-rules.yml`
  reads for `MaezoSLAAgentErrorRateHigh` / `MaezoAgentCrashLoop` (AF-13 / ALERTS-WITHOUT-METRICS-a)

Design decisions (ADR-0010, ADR-0014):
- OTel with agent semantics: trace per conversation, span per node/tool/LLM call
- Worker metrics: execution time histogram + error counter with labels
- Prometheus + Grafana (dev local) / AMP + AMG (staging/prod)
- SigV4 remote-write in prod via OTel Collector
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

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

    GAP CLOSED (AF-13, 2026-09-03) — WAS: "`setup_observability` has NO production caller today
    (repo-wide sweep — only tests call it), so the daemons run structlog's DEFAULT configuration".
    That was true and is no longer: the three composition roots (`runtime/agent_runtime/service.py`,
    `runtime/worker_runtime/service.py`, `gateway/service.py`) now call
    :func:`bootstrap_observability` as the FIRST act of `run()`, before their own first log line,
    so this seam is on the live path of every daemon.

    WHAT THE WIRING DOES *NOT* CHANGE, corrected after the WP-COMPOSICAO-V2 review (MAJOR-1). An
    earlier version of this note claimed the wiring "replaces structlog's default with
    ConsoleRenderer + PrintLoggerFactory". That was WRONG: structlog 25.5.0's own default IS
    `ConsoleRenderer` over `PrintLoggerFactory`. The renderer family never changed. What the first
    cut of the wiring actually changed — and what is now repaired above — was that it DROPPED
    `add_log_level` and `merge_contextvars` and FORCED ANSI colours regardless of TTY, which broke
    both `grep ERROR`-style level triage and `key=value` field greps in containers. The chain now
    carries both processors and asks stdout whether it is a terminal. The remaining, deliberate
    delta from the default is the ISO-8601/UTC timestamp (vs local `%Y-%m-%d %H:%M:%S`) plus this
    scrubber slot; it is disclosed to SRE in `docs/review-queue.md`.

    WHAT THAT DOES NOT CLOSE: this function is still gated on the policy, and the policy is still
    `off`. Ratifying `scrub_only` now DOES reach the log egress (it did not before) — but the
    ratification itself remains the owner's, unchanged by this wiring.

    Returns None (no processor, byte-identical logging) whenever the effective policy mode is
    `off` — which is the shipped state. When the policy IS active the pseudonymizer is built
    through `Pseudonymizer.from_settings`, so the ADR-0035 fail-closed key rule is inherited: a
    runtime with no `PHI_HMAC_KEY` raises `PseudonymizerKeyMissingError` here rather than
    installing a scrubber that would pseudonymize with a publicly-known dev key.

    WHERE THAT RAISE SURFACES, RE-DERIVED AFTER AF-13. It used to be unreachable in production
    (no composition root called `setup_observability`). It is now reachable at BOOT: each root
    calls this through :func:`bootstrap_observability`, whose guard catches the raise, leaves the
    root's `observability_configured` readiness check RED with the error text, and lets liveness
    stay up — the same bounded/non-fatal posture every other bring-up block in those daemons uses.
    So under a ratified `scrub_only` with no `PHI_HMAC_KEY`, the daemon now reports NOT READY at
    boot instead of only failing later. The second, independent surface is unchanged:
    `key_scrubber.egress_message_key`, called by the `operadora.events.publish` worker handler,
    still raises `PseudonymizerKeyMissingError` onto the harness retry/incident ladder (that
    handler hoists the call out of its publish-`try` precisely so the fault is not re-labelled as
    a Kafka publish failure).
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
# Console rendering: TTY-aware, and never silently different from structlog's default
# ---------------------------------------------------------------------------

#: `MAEZO_LOG_LEVEL` values this module accepts. Anything else is refused loudly rather than
#: silently downgraded to INFO — a log level that is quietly ignored is exactly the defect
#: minor-7 of the WP-COMPOSICAO-V2 review named.
_LOG_LEVELS: Final[dict[str, int]] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _console_colors_enabled() -> bool:
    """Whether `ConsoleRenderer` may emit ANSI escapes. TTY-aware, exactly like structlog's default.

    WHY THIS EXISTS AT ALL (WP-COMPOSICAO-V2 review, MAJOR-1). `structlog.dev.ConsoleRenderer`'s
    own parameter default is `colors=True`, whereas the renderer structlog builds for its DEFAULT
    configuration is TTY-aware. So constructing a bare `ConsoleRenderer()` here — which is what
    this module used to do — does NOT reproduce the default: it FORCES colours, and a container
    with no TTY (every one of the three daemons, in every deployment) then writes escape sequences
    into `kubectl logs` / CloudWatch. That breaks field-level greps too, not just colour: the
    escapes sit BETWEEN key, `=` and value, so `topic=t` stops being a substring of the line.

    `NO_COLOR` (https://no-color.org) is honoured for the case an operator pipes a TTY session
    into a file. `sys.stdout` is the stream `PrintLoggerFactory()` writes to, so it is the stream
    whose TTY-ness decides — reading `sys.stdout` at CONFIGURE time, which is also when
    `PrintLogger` captures it.
    """
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(getattr(sys, "stdout", None), "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        # A detached/closed stream is not a terminal. Never a reason to fail bring-up.
        return False


def _log_level_wrapper_class(log_level: str) -> Any:
    """The structlog `wrapper_class` for `MAEZO_LOG_LEVEL`. UNSET == no filtering, as before.

    MAJOR-1/minor-7 pair. `MAEZO_LOG_LEVEL` was read into a variable, emitted as a log FIELD, and
    then never applied: `structlog.stdlib.BoundLogger` over `PrintLoggerFactory` performs no level
    filtering at all, so `MAEZO_LOG_LEVEL=ERROR` printed every DEBUG line just the same. Now it is
    real — and the DEFAULT is deliberately `NOTSET` (no filtering), which is byte-identical to the
    behaviour of both the pre-AF-13 world and structlog's own default. Setting the variable is an
    opt-in; not setting it changes nothing. An unknown value raises rather than falling back,
    because a silently-ignored log level is the very thing being repaired here.
    """
    try:
        level = _LOG_LEVELS[log_level]
    except KeyError:
        raise ValueError(
            f"MAEZO_LOG_LEVEL={log_level!r} is not a log level. Accepted: "
            f"{', '.join(sorted(_LOG_LEVELS))}. Refusing to fall back to a default, because a "
            "log level that is silently ignored is indistinguishable from one that is applied."
        ) from None
    return structlog.make_filtering_bound_logger(level)


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
        MAEZO_LOG_LEVEL: minimum level to EMIT. Unset == `NOTSET` == no filtering (the
                         pre-existing behaviour); an unknown value raises `ValueError`.
        NO_COLOR: disables ANSI colours even on a TTY (https://no-color.org). Colours are off
                  by default whenever stdout is not a terminal — i.e. in every container.

    ADR-0010: OTel with agent semantics. Traces are per-conversation/task;
    spans represent LangGraph nodes, tool calls, and LLM calls.
    ADR-0014: Dev local uses no-op exporter; staging/prod uses OTLP->AMP
    via OTel Collector with SigV4 remote-write.
    """
    # --- Structlog configuration ---
    #
    # THE RULE THIS CHAIN OBEYS (WP-COMPOSICAO-V2 review, MAJOR-1): wiring the daemons must not
    # silently change what an operator's `grep` finds. Everything structlog's own default chain
    # provides is provided here too — `merge_contextvars`, `add_log_level`, TTY-aware colours —
    # and the only deliberate deltas are the ISO-8601/UTC timestamp and the DL-0043 scrubber slot.
    # Concretely: `add_log_level` is what puts the `[error    ]` token on the line that
    # `docs/runbooks/devops-stack.md:318` and the SLA-alert runbook grep for (structlog renders it
    # LOWER-case — `grep -i` — which was already true of the pre-wiring default), and TTY-aware
    # colours are what keep `topic=t` an unbroken substring in a container's log stream.
    log_level = os.environ.get("MAEZO_LOG_LEVEL", "NOTSET").upper()
    scrubber = _build_key_scrubber()
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if scrubber is not None:
        # Scrub LAST before rendering: every earlier processor may still ADD fields to the
        # event dict, so a scrubber placed before them would miss whatever they contribute.
        processors.append(scrubber)
    colors = _console_colors_enabled()
    processors.append(structlog.dev.ConsoleRenderer(colors=colors))
    structlog.configure(
        processors=processors,
        wrapper_class=_log_level_wrapper_class(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(__name__).info(
        "observability_structlog_configured",
        log_level=log_level,
        colors=colors,
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
# Composition-root bootstrap (AF-13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservabilityStatus:
    """What a composition root learned when it brought observability up.

    The daemons already isolate every bring-up block into a NAMED readiness check rather than
    letting it kill liveness (`agent_runtime/service.py` STEP B, `worker_runtime/service.py`
    STEP B). This value object is that block's result for observability, so `/readyz` can state
    the truth instead of the absence of a complaint.

    `traces_exported` is the field that exists because of the specific dishonesty AF-13 named: a
    no-op exporter and a working exporter were indistinguishable from outside the pod. `configured`
    True + `traces_exported` False is the SHIPPED dev/no-endpoint posture the module documents
    ("If None or empty, traces are exported to a no-op backend (dev mode)") — it is healthy, and it
    says so in words, which is the whole point.
    """

    #: `setup_observability` completed without raising. False means structlog/OTel are NOT
    #: configured for this process — a real misconfiguration (e.g. ADR-0035's fail-closed
    #: `PseudonymizerKeyMissingError` under a ratified `pseudo_keys`), and the readiness check
    #: for it is RED.
    configured: bool
    #: The OTel resource `service.name` this process booted under.
    service_name: str
    #: The resolved OTLP endpoint, or None when none was configured.
    otlp_endpoint: str | None
    #: True only when a real `BatchSpanProcessor`/OTLP exporter was installed. False = no-op.
    traces_exported: bool
    #: `type(exc).__name__: exc` when `configured` is False; None otherwise.
    error: str | None = None

    @property
    def detail(self) -> str:
        """One line for the readiness check — never silent about a no-op exporter."""
        if not self.configured:
            return f"observability bootstrap FAILED: {self.error or 'unknown error'}"
        if self.traces_exported:
            # "configured, delivery unverified" and NOT "traces are flowing": nothing in this
            # process validates the endpoint. A malformed or unroutable `OTEL_EXPORTER_OTLP_ENDPOINT`
            # builds an exporter that never raises here (gRPC fails later, on a background thread,
            # to stderr). Saying `traces=otlp:<endpoint>` alone would re-create AF-13's own defect
            # one level up — a positive statement about export that was never checked.
            return (
                f"service={self.service_name} traces=otlp:{self.otlp_endpoint} "
                "(exporter configured; DELIVERY NOT VERIFIED by this process)"
            )
        return (
            f"service={self.service_name} traces=NOT EXPORTED — no OTLP endpoint configured "
            "(OTEL_EXPORTER_OTLP_ENDPOINT unset). Spans are built and dropped; this is the "
            "documented dev/no-op posture (ADR-0014), NOT a working trace pipeline."
        )


def bootstrap_observability(
    *,
    service_name: str,
    otlp_endpoint: str | None = None,
) -> ObservabilityStatus:
    """Bring observability up at a COMPOSITION ROOT. The AF-13 wire; never propagates.

    AF-13, stated plainly: `setup_observability` was a complete, tested library function with zero
    production callers, so every daemon ran structlog's default configuration and built no tracer
    provider at all. This is the call the three roots make, and the reason it is a wrapper rather
    than a bare `setup_observability(...)` at each root is that a root needs THREE things the bare
    function does not give it: isolation (a bad PHI-key policy must leave the pod not-ready, never
    CrashLooping), an explicit statement of whether traces actually leave the process, and one
    shape for all three roots so they cannot drift.

    NOT FAIL-OPEN, NOT FAIL-FATAL. A raise here is contained and reported RED
    (`ObservabilityStatus.configured=False`) — the same posture as every other bring-up block in
    those daemons (`docs/design/T1.1-runtime-spine.md` §10 / I-2: "the failure mode is 'replica not
    ready', never 'replica ready and ungated'"). An ABSENT OTLP endpoint is NOT an error: the
    module's own contract documents the no-op exporter as the dev mode, so this returns
    `configured=True, traces_exported=False` and says so in `detail` and in a WARNING log —
    explicit, per AF-13's "no silent no-op".

    Args:
        service_name: OTel resource `service.name` (a bounded, non-PHI token — a daemon/agent id).
        otlp_endpoint: the endpoint from the root's SETTINGS class (never a fresh `os.environ`
            read at the root — `check_effect_chokepoint_fence.py` §8.3 discipline). Note that
            `setup_observability` still lets `OTEL_EXPORTER_OTLP_ENDPOINT` win, and the settings
            field is aliased to exactly that variable, so the two agree by construction.
    """
    try:
        setup_observability(service_name=service_name, otlp_endpoint=otlp_endpoint)
    except Exception as exc:  # noqa: BLE001 — isolated: a telemetry fault must not CrashLoop a pod.
        status = ObservabilityStatus(
            configured=False,
            service_name=service_name,
            otlp_endpoint=otlp_endpoint,
            traces_exported=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.error("observability_bootstrap_failed", service_name=service_name, exc_info=True)
        return status

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", otlp_endpoint or "") or None
    status = ObservabilityStatus(
        configured=True,
        service_name=os.environ.get("OTEL_SERVICE_NAME", service_name),
        otlp_endpoint=endpoint,
        traces_exported=endpoint is not None,
    )
    if status.traces_exported:
        logger.info("observability_bootstrapped", service_name=status.service_name, detail=status.detail)
    else:
        # WARNING, not info: "we are emitting no traces" is an operational fact an on-call needs to
        # be able to find, and the pre-AF-13 world's defect was precisely that it was unfindable.
        logger.warning(
            "observability_bootstrapped_without_trace_export",
            service_name=status.service_name,
            detail=status.detail,
        )
    return status


# ---------------------------------------------------------------------------
# Agent counters the SLA/crash-loop alerts read (AF-13 / ALERTS-WITHOUT-METRICS-a)
# ---------------------------------------------------------------------------


def record_tool_call() -> None:
    """Count ONE gated tool/effect invocation — `maezo_tool_calls_total`.

    Called from `maezo.gateway.seams._base.gate` — the ONE per-call chokepoint every gated seam
    (dmn, cibseven, fhir, whatsapp, inference, population, a2a) passes through, agent-side and
    worker-side. Counting here rather than in each wrapper is what makes coverage structural: a
    seam that skipped this counter would also have skipped the policy decision.

    NO LABELS, DELIBERATELY, and this is not laziness — it is read off the alert. The shipped
    `MaezoSLAAgentErrorRateHigh` expr is
    `rate(maezo_agent_errors_total[5m]) / rate(maezo_tool_calls_total[5m])`
    (`deploy/observability/alert-rules.yml:38-42`), a binary operation whose default vector
    matching requires the two operands to carry IDENTICAL label sets. Giving this counter a
    `tool`/`principal` label that `maezo_agent_errors_total` cannot also carry would produce an
    empty result — an alert that can never fire, which is the exact defect class
    ALERTS-WITHOUT-METRICS-a exists to close. Widening both counters to a shared label set is a
    real option and is recorded in `docs/review-queue.md` as an owner decision, because it is only
    safe together with an `alert-rules.yml` edit and `deploy/` is owner-gated.

    Best-effort: telemetry must never break an effect call, so the caller guards it.
    """
    _get_metrics_collector().tool_calls.inc()


def record_effect_rate_limited(*, tenant: str, principal: str) -> None:
    """Count ONE gated effect call refused by the chokepoint rate limit — D6-01.

    Called from `maezo.gateway.seams._base.gate`, the same single per-call chokepoint
    :func:`record_tool_call` is called from, so a throttled call is counted exactly once and by
    construction (a seam that skipped this counter would also have skipped the decision).

    `maezo_effect_rate_limited_total` is NOT read by any shipped alert rule
    (`deploy/observability/alert-rules.yml` is owner-gated and untouched by this change), so it is
    deliberately absent from `test_alert_metrics_fence.py`'s emitter table — which is derived from
    the alert file, not from this module. It is the src-side signal an operator and a future alert
    can both read; proposing the alert itself is an owner decision, recorded in
    `docs/review-queue.md`.

    Args:
        tenant: the throttled key's tenant. A bounded, non-PHI token.
        principal: the throttled key's principal (agent id or daemon principal). Same.

    Best-effort: telemetry must never break an effect call, so the caller guards it.
    """
    _get_metrics_collector().effect_rate_limited.labels(tenant=tenant, principal=principal).inc()


def record_agent_error() -> None:
    """Count ONE failed agent turn — `maezo_agent_errors_total`.

    Called from the TWO turn-execution seams this repo has, both of them platform composition code
    and neither of them inside an agent graph:
      * `maezo.runtime.harness.Harness.invoke` (the agent-runtime ingress path), and
      * `maezo.platform.webhooks.whatsapp.dispatch.HelenaDispatcher.dispatch` (the live WhatsApp
        receiver, which compiles and `ainvoke`s Helena's graph directly rather than through
        `Harness`).
    `tests/unit/platform/test_alert_metrics_fence.py::test_every_turn_execution_seam_counts_agent_errors`
    pins that enumeration so a THIRD turn seam cannot appear un-instrumented.

    WHAT IS AND IS NOT AN "AGENT ERROR" HERE. A turn that raised out of the graph is one. A policy
    DENIAL at the effect chokepoint is NOT — `gate()` refusing an effect is the system working, and
    counting it would make `MaezoAgentCrashLoop` (`rate(maezo_agent_errors_total[1m]) > 0`) fire on
    correct refusals. That distinction is a judgement, so it is written down rather than implied.

    Label-free for the same vector-matching reason as :func:`record_tool_call`.

    GUARDED INTERNALLY (unlike :func:`record_tool_call`, whose caller guards it): every call site
    is an `except` block that is about to RE-RAISE the real failure, and a metrics fault there
    would replace the turn's genuine exception with a telemetry one — the worst possible trade.
    """
    try:
        _get_metrics_collector().errors.inc()
    except Exception:  # noqa: BLE001 — never mask the turn failure this is counting.
        logger.debug("agent_error_metric_emit_failed", exc_info=True)


def record_llm_tier_resolution(*, task_kind: str, tier: str, resolution: str) -> None:
    """Record how ONE `task_kind` resolved to a model — `maezo_llm_tier_resolution_total` (AF-12).

    The "explicit metric" half of AF-12's fail-closed tier resolution: this repo's inference
    configuration has exactly ONE model (`MAEZO_INFERENCE_MODEL` / the provider default) and no
    per-tier model map, so every declared tier resolves to that same model. That is a legitimate
    resolution, but it must be VISIBLE — a tier that silently means nothing is how ADR-0009's
    routing stayed decorative for a year.

    All three labels are closed vocabularies (`inference.MODEL_TASK_KINDS`, `inference.MODEL_TIERS`
    plus the two sentinels, `inference.TIER_RESOLUTIONS`). No prompt, no tenant, no agent id.
    """
    _get_metrics_collector().llm_tier_resolution.labels(
        task_kind=task_kind, tier=tier, resolution=resolution
    ).inc()


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


def record_bridge_dlq(*, topic: str, reason: str) -> None:
    """Record ONE notifications-bridge dead-letter shunt (GAP-SC-04-a, audit D5).

    Called by `maezo.platform.integrations.notifications_bridge.BridgeDlqShunt.shunt` — the single
    place a poison message is moved to `<topic>.dlq`. Increments
    `maezo_bridge_dlq_total{topic, reason}`.

    `topic` is the bridge's SOURCE topic (never the derived `.dlq` name — the alert reader wants
    "which stream is producing poison", and the `.dlq` name is a mechanical suffix of it).
    `reason` MUST be one of `notifications_bridge.BRIDGE_DLQ_REASONS`: a CLOSED vocabulary, never
    the JSON parser's error text and never any byte of the offending payload. A message in the DLQ
    is by definition one nobody validated — a metric label derived from its content would be both
    unbounded cardinality and the worst possible PHI surface.

    NOT `maezo_dead_letter_queue_size`. That is a GAUGE (current DLQ depth) read by the
    `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` alert rules, and nothing in `src/` can emit it
    honestly: depth is a broker-side fact this process cannot observe. See
    `MetricsCollector.bridge_dlq`'s construction comment and `docs/review-queue.md`.

    This is the raw typed helper. The caller wraps it in a defensive guard (telemetry must never
    fail — or worse, silently alter — a fail-closed shunt) — see `BridgeDlqShunt._record_metric`.
    """
    collector = _get_metrics_collector()
    collector.bridge_dlq.labels(topic=topic, reason=reason).inc()


def record_llm_token_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record LLM token consumption (T8, token-metering).

    Called by `maezo.runtime.inference.providers._emit_llm_token_usage` — reached only from
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
    emits (`maezo.runtime.inference.providers._emit_llm_token_usage`'s `llm_token_usage` event).

    COUNTS ONLY — this function never computes or emits a cost/price value. Pricing is a
    finance-gated human decision (see the EXTENSION POINT note in
    `maezo.runtime.inference.providers._emit_llm_token_usage`).

    This is the raw typed helper — intentionally NOT wrapped in a defensive try/except
    here (contrast `record_worker_task_outcome`'s caller, `_emit_worker_task_outcome`):
    the caller (`_emit_llm_token_usage`) already wraps its entire body, including this
    call, in a single broad guard, so a second guard here would be redundant.
    """
    collector = _get_metrics_collector()
    collector.llm_tokens.labels(provider=provider, model=model, token_type="input").inc(input_tokens)
    collector.llm_tokens.labels(provider=provider, model=model, token_type="output").inc(output_tokens)


# ---------------------------------------------------------------------------
# Agent-turn telemetry (G3 — the #222 PHI-fence bar, one record per completed turn)
# ---------------------------------------------------------------------------

#: Marker prefixing a turn's conversation CORRELATION token. Mirrors the pseudonymizer's `hk1_`
#: keyed-pseudonym marker (gateway/pseudonymizer.py): a downstream reader can tell a correlation
#: digest from a raw id by shape. `_` (not `:`) so it never collides with the `wa:{tenant}:{hash}`
#: colon split. Bump the version digit on any scheme change.
TURN_CORRELATION_PREFIX: Final[str] = "tc1_"

#: The CLOSED, pinned field set of an `agent_turn_completed` record. COUNTS plus one HASHED
#: correlation token — nothing drawn from message content, and disjoint from `PHI_FIELDS` by
#: construction (asserted in `tests/unit/platform/test_observability.py`). Widening this set is a
#: reviewable change: every field must be a count or a hash, never raw content or a raw identifier.
TURN_TELEMETRY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "agent_id",
        "input_message_count",
        "output_message_count",
        "produced_message_count",
        "conversation_digest",
    }
)


def turn_conversation_digest(conversation_ref: str) -> str:
    """Stable, one-way correlation token for a turn's conversation/thread id — NEVER the raw id.

    The #222 discipline (`record_llm_token_usage`) keeps per-instance identifiers OUT of the
    metric and confines correlation to the structured log line; the turn record goes one step
    further and lets even that line carry only a HASH of the conversation id, never the id itself.
    The turn path's identifier is the langgraph `thread_id`, already a PHI-safe KEYED pseudonym
    (`hk1_<hmac>`, `runtime/checkpoint.py::assert_phi_safe_thread_id`) or an `ESC-` process
    business key — so an unkeyed SHA-256 here is a hash OF a pseudonym, defense-in-depth, and
    never sees raw PHI. It is deterministic (two turns of one conversation correlate) and
    truncated to 32 hex to stay compact; the `tc1_` marker names the scheme.

    Uses the same `hashlib.sha256` primitive the `Pseudonymizer` is built on
    (gateway/pseudonymizer.py), not a parallel one.
    """
    import hashlib  # noqa: PLC0415 — keep this module's top-level import surface minimal

    digest = hashlib.sha256(conversation_ref.encode("utf-8")).hexdigest()[:32]
    return f"{TURN_CORRELATION_PREFIX}{digest}"


def record_agent_turn(
    *,
    agent_id: str | None,
    input_message_count: int,
    output_message_count: int,
    conversation_ref: str | None,
) -> None:
    """Emit ONE `agent_turn_completed` telemetry record when an agent turn finishes (G3).

    The #222 PHI fence, applied to the agent-turn path. Emits COUNTS and a single HASHED
    conversation correlation token — and NOTHING drawn from message content: never a message
    string, never a tenant PHI field (`cpf`/`nome`/`telefone`/`email`), never the raw
    conversation/thread id or a business key. The conversation id is passed through
    `turn_conversation_digest` FIRST, so the raw `conversation_ref` never reaches the sink.

    The counts describe the turn's SHAPE, not its content: how many messages entered the turn, how
    many the final state holds, and the difference (what this turn produced) — `len()` only, the
    strings themselves are never read. `agent_id` is an agent NAME (e.g. `helena`), not PHI.

    Best-effort by construction (mirrors `_emit_llm_token_usage`): a telemetry defect must never
    break the turn that already completed, so the whole body is guarded.
    """
    try:
        produced = max(output_message_count - input_message_count, 0)
        logger.info(
            "agent_turn_completed",
            agent_id=agent_id,
            input_message_count=input_message_count,
            output_message_count=output_message_count,
            produced_message_count=produced,
            conversation_digest=(turn_conversation_digest(conversation_ref) if conversation_ref else None),
        )
    except Exception:  # noqa: BLE001 — defensive: telemetry must never break a completed turn.
        logger.debug("agent_turn_telemetry_emit_failed", exc_info=True)
