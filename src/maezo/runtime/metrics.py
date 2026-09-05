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

T8 LLM token-metering (ADR-0009 single seam, maezo.runtime.inference):
- maezo_llm_tokens_total (Counter) — token COUNTS consumed by {provider,model,token_type};
  never a cost/dollar value (pricing is finance-gated — see inference.py's extension-point note)

AF-12 model-tier routing (ADR-0009 §2, maezo.runtime.inference.InferenceProvider.generate):
- maezo_llm_tier_resolution_total (Counter) — how a declared {task_kind,tier} actually resolved
  to a model. Exists because this repo configures ONE model and no per-tier map: the fallback is
  legitimate but must never be silent.

GAP-SC-04-a notifications-bridge dead-letter metering:
- maezo_bridge_dlq_total (Counter) — poison messages shunted to `<topic>.dlq` by {topic,reason}

WHICH OF THESE THE SHIPPED ALERTS READ (deploy/observability/alert-rules.yml, read-only here):
`maezo_worker_execution_time_seconds`, `maezo_worker_error_count_total`, `maezo_agent_errors_total`
and `maezo_tool_calls_total`. The last two had NO emitter in `src/` at all until AF-13
(ALERTS-WITHOUT-METRICS-a) — `MaezoSLAAgentErrorRateHigh` and `MaezoAgentCrashLoop` could not fire.
`tests/unit/platform/test_alert_metrics_fence.py` is the gate that keeps that from recurring.
"""

from __future__ import annotations

from typing import Final

import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ALERT-COUNTER-LABELS / R-063 (owner-ratified 2026-09-04): closed `error_type` vocabulary for
# the `maezo_tool_calls_total`/`maezo_agent_errors_total` labels added below. Bounded on purpose —
# the raw exception CLASS NAME is unbounded (a new exception type would mint a new series forever,
# the exact cardinality hazard `worker_task_total`/`llm_tokens` warn against above), so
# `classify_agent_error_type` maps every exception to one of these tokens and nothing else reaches
# the label.
# ---------------------------------------------------------------------------

AGENT_ERROR_TYPE_VALIDACAO: Final[str] = "validacao"
AGENT_ERROR_TYPE_TIMEOUT: Final[str] = "timeout"
AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL: Final[str] = "upstream_indisponivel"
AGENT_ERROR_TYPE_RUNTIME: Final[str] = "runtime"
AGENT_ERROR_TYPE_OUTRO: Final[str] = "outro"

#: Sentinel for `maezo_tool_calls_total`'s `error_type` label. A tool call is counted on every
#: gated invocation regardless of outcome (`_count_tool_call`, gateway/seams/_base.py) — it is not
#: about an error at all, so "error_type" has no natural value there. This token exists ONLY so
#: both counters share the same label SET, which is what lets
#: `sum by (agent) (rate(maezo_agent_errors_total[5m])) / sum by (agent)
#: (rate(maezo_tool_calls_total[5m]))` (alert-rules.yml `MaezoSLAAgentErrorRateHigh`) evaluate per
#: agent without PromQL's default vector matching failing on a mismatched label set.
AGENT_ERROR_TYPE_NONE: Final[str] = "nao_aplicavel"

AGENT_ERROR_TYPES: Final[frozenset[str]] = frozenset(
    {
        AGENT_ERROR_TYPE_VALIDACAO,
        AGENT_ERROR_TYPE_TIMEOUT,
        AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
        AGENT_ERROR_TYPE_RUNTIME,
        AGENT_ERROR_TYPE_OUTRO,
        AGENT_ERROR_TYPE_NONE,
    }
)

#: Exact exception CLASS NAME -> bounded `error_type` token. Deliberately an EXACT lookup (no
#: isinstance/MRO walk): a subclass not listed here falls back to `AGENT_ERROR_TYPE_OUTRO` rather
#: than silently inheriting a category nobody reviewed for it.
_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS: Final[dict[str, str]] = {
    "ValueError": AGENT_ERROR_TYPE_VALIDACAO,
    "TypeError": AGENT_ERROR_TYPE_VALIDACAO,
    "KeyError": AGENT_ERROR_TYPE_VALIDACAO,
    "ValidationError": AGENT_ERROR_TYPE_VALIDACAO,  # pydantic
    "TimeoutError": AGENT_ERROR_TYPE_TIMEOUT,
    "ConnectTimeout": AGENT_ERROR_TYPE_TIMEOUT,  # httpx
    "ReadTimeout": AGENT_ERROR_TYPE_TIMEOUT,  # httpx
    "ConnectionError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
    "ConnectError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,  # httpx
    "HTTPStatusError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,  # httpx
    "OSError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
    "RuntimeError": AGENT_ERROR_TYPE_RUNTIME,
}


def classify_agent_error_type(exc: BaseException) -> str:
    """Map one exception instance to a bounded `error_type` label value (ALERT-COUNTER-LABELS / R-063).

    Exact `type(exc).__name__` lookup against `_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS`; anything not
    listed maps to `AGENT_ERROR_TYPE_OUTRO` — never the raw class name, never free text. Never
    raises: a classification fault must not be able to break the `except` block calling it.
    """
    return _AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS.get(type(exc).__name__, AGENT_ERROR_TYPE_OUTRO)


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
            labelnames=["agent", "error_type"],
            registry=self._registry,
        )

        self._errors = Counter(
            "maezo_agent_errors_total",
            "Total number of agent errors",
            labelnames=["agent", "error_type"],
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

        # T8: LLM token-metering (design: single seam in maezo.runtime.inference,
        # AnthropicInferenceProvider.generate). `provider`/`model` are a small, bounded set
        # (one provider today, a handful of model ids) — safe Prometheus label cardinality
        # per ADR-0010 discipline. `token_type` is "input" or "output". Deliberately NO
        # tenant/agent/thread label here: those are per-instance correlation, which belongs
        # in the structured log line this metric's emitter also writes (see
        # maezo.runtime.inference._emit_llm_token_usage), never on a metric label — same
        # rule `worker_task_total` documents for task/business-key identifiers above.
        self._llm_tokens = Counter(
            "maezo_llm_tokens_total",
            "LLM tokens consumed (COUNTS ONLY, never a cost value) by provider/model/token_type",
            labelnames=["provider", "model", "token_type"],
            registry=self._registry,
        )

        # DL-0043 leg (c): SHADOW telemetry for the PHI-in-business-keys remediation flag. Counts
        # business-key MINTS by which anchor the key was derived from — it is the owner's
        # evidence for deciding whether to ratify `spec/policies/privacy/
        # phi-business-key-remediation.yaml`. The `anchor="matricula"` series is exactly "how
        # many keys we minted today that WOULD have been pseudonymized under `pseudo_keys`".
        #
        # CONTENT-FREE BY CONSTRUCTION. Every label is a closed vocabulary: `family` in
        # {CANCEL, INAD}, `modo` in {off, scrub_only, pseudo_keys}, `anchor` in
        # {contrato, matricula, pseudo}. NO tenant, NO business key, NO matricula — the same rule
        # `worker_task_total` and `llm_tokens` document above, and the reason this counter can
        # exist at all while the thing it measures is PHI.
        self._phi_business_key_mint = Counter(
            "maezo_phi_business_key_mint_total",
            "Business-key mints by family and derivation anchor (DL-0043 shadow telemetry; "
            "COUNTS ONLY — never any key, tenant or matricula content)",
            labelnames=["family", "modo", "anchor"],
            registry=self._registry,
        )

        # AF-12 (ADR-0009 §2 "Routing por tarefa"): how each declared tier actually resolved.
        # CLOSED vocabularies only — `task_kind` in `inference.MODEL_TASK_KINDS`, `tier` in
        # `inference.MODEL_TIERS` plus the two sentinels, `resolution` in
        # `inference.TIER_RESOLUTIONS`. No agent id, no tenant, no prompt: same discipline
        # `llm_tokens` and `phi_business_key_mint` document above.
        self._llm_tier_resolution = Counter(
            "maezo_llm_tier_resolution_total",
            "Model-tier resolutions by task_kind/tier/resolution (ADR-0009 routing; COUNTS ONLY)",
            labelnames=["task_kind", "tier", "resolution"],
            registry=self._registry,
        )

        # GAP-SC-04-a (audit D5): the notifications-bridge's poison-message shunt. Counts
        # messages the bridge could not even classify and therefore moved to `<topic>.dlq`
        # instead of blocking the partition behind them (head-of-line blocking) or — worse —
        # dropping them.
        #
        # THIS IS A COUNTER, AND IT IS NOT `maezo_dead_letter_queue_size`. The alert rules
        # `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` (`deploy/observability/alert-rules.yml`)
        # read a GAUGE named `maezo_dead_letter_queue_size` — the CURRENT DEPTH of a DLQ topic.
        # Nothing in `src/` can honestly emit that: depth is a broker-side fact (records produced
        # minus records consumed by the DLQ's own reader), and this process only ever sees the
        # records IT produces. Emitting a src-side gauge would fabricate a number that drifts from
        # the broker the moment anyone drains the DLQ. The gauge belongs to a Kafka/JMX exporter
        # scrape job that does not exist yet (owner-gated — `docs/review-queue.md`); this counter
        # is the honest src-side signal, and `rate(maezo_bridge_dlq_total[5m])` is a real
        # INFLOW alert that needs no exporter.
        #
        # CONTENT-FREE BY CONSTRUCTION. `topic` is the bridge's own input topic (a bounded set —
        # one value in this build). `reason` is a CLOSED vocabulary
        # (`notifications_bridge.BRIDGE_DLQ_REASONS`), never the parser's error text, never any
        # part of the offending payload — same rule `worker_task_total` and `llm_tokens` document
        # above. A message that reaches the DLQ is by definition one nobody validated, so putting
        # anything derived from its bytes on a metric label would be the worst possible place for
        # it (unbounded cardinality AND potential PHI).
        self._bridge_dlq = Counter(
            "maezo_bridge_dlq_total",
            "Notifications-bridge poison messages shunted to a dead-letter topic (COUNTS ONLY; "
            "reason is a closed vocabulary, never payload-derived)",
            labelnames=["topic", "reason"],
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

    def agent_counter_labelnames(self) -> dict[str, tuple[str, ...]]:
        """The configured `labelnames` of `tool_calls`/`errors` (ALERT-COUNTER-LABELS / R-063),
        keyed by attribute name — the public accessor callers pinning the label-SHAPE contract
        should use instead of reaching into `prometheus_client.Counter`'s own internals directly.

        `prometheus_client.Counter` exposes NO public way to read a metric's configured label
        NAMES before its first observation: `collect()` — the one public introspection path —
        yields zero `Sample`s until `.labels(...).inc()` has run at least once (a labelled metric
        with no observation yet is indistinguishable, via `collect()`, from one that will never be
        used). `_labelnames` (defined on `prometheus_client`'s own `MetricWrapperBase`, a class
        this repo does not own) is the only place the answer lives before that. This method
        confines that one unavoidable reach-in to the single place that already owns and
        constructs both `Counter` instances, so every caller — starting with the test pinning the
        two agent counters' shared label set — reads a genuinely public contract instead.
        """
        return {
            name: counter._labelnames  # noqa: SLF001 — no public API exists; see docstring above.
            for name, counter in (("tool_calls", self._tool_calls), ("errors", self._errors))
        }

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

    @property
    def llm_tokens(self) -> Counter:
        """Counter for LLM token consumption (T8, token-metering).

        Labels: provider, model, token_type ("input" | "output"). COUNTS ONLY — never
        incremented with, or converted to, a cost/dollar value.
        """
        return self._llm_tokens

    @property
    def llm_tier_resolution(self) -> Counter:
        """Counter for model-tier resolutions (AF-12, ADR-0009 §2).

        Labels: task_kind ("task_default" | "reasoning" | "batch"), tier ("fast" | "frontier" |
        "batch" | "nao_declarado" | "sem_mapa"), resolution (see `inference.TIER_RESOLUTIONS`).
        """
        return self._llm_tier_resolution

    @property
    def bridge_dlq(self) -> Counter:
        """Counter for notifications-bridge dead-letter shunts (GAP-SC-04-a).

        Labels: topic (the bridge's SOURCE topic, not the `.dlq` name), reason (one of
        `notifications_bridge.BRIDGE_DLQ_REASONS`). COUNTS ONLY — no payload byte, no offset, no
        business identifier ever reaches this metric.

        NOT the same series as the `maezo_dead_letter_queue_size` GAUGE the
        `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` alert rules read — see the construction
        comment for why `src/` cannot honestly emit that one.
        """
        return self._bridge_dlq

    @property
    def phi_business_key_mint(self) -> Counter:
        """Counter for business-key mints by derivation anchor (DL-0043 shadow telemetry).

        Labels: family ("CANCEL" | "INAD"), modo ("off" | "scrub_only" | "pseudo_keys"),
        anchor ("contrato" | "matricula" | "pseudo"). COUNTS ONLY — no key, tenant or
        matricula value ever reaches this metric.
        """
        return self._phi_business_key_mint
