"""The alert<->metric fence: every shipped alert must read a metric something in `src/` emits.

WHY THIS FILE EXISTS (audit gap ALERTS-WITHOUT-METRICS-a, register
`docs/audits/maezo-deep-audit/remediation/GAP-REGISTER.md`). `deploy/observability/alert-rules.yml`
has shipped `MaezoSLAAgentErrorRateHigh` and `MaezoAgentCrashLoop` since ADR-0010. Both are built
on `maezo_agent_errors_total` and `maezo_tool_calls_total`. Both counters were DECLARED in
`maezo.runtime.metrics.MetricsCollector` and incremented by NOTHING, anywhere in `src/` — so both
alerts were structurally unfireable in every deployment, and nothing said so. A metric that exists
but is never written looks identical, from a dashboard, to a metric that is always zero because
the system is healthy.

The same class of defect had a second instance: three WORKER alerts read
`maezo_worker_execution_time_seconds` / `maezo_worker_error_count_total`, which only
`WorkerBase.run()` emitted, so every raw `harness.register()` topic was invisible to them
(WORKER-METRICS-COVERAGE). That one is a COVERAGE gap rather than a total absence, and its proof
lives in `tests/unit/tools/workers/test_harness_worker_metrics.py`; this file proves the metric
exists and has a live emitter at all.

WHAT THIS FENCE ASSERTS, and what it deliberately does not:
  1. Every metric named in a shipped alert `expr` is either declared+emitted in-repo, or DECLARED
     EXTERNAL with the register id of the slice that owns it. There is no silent skip.
  2. The external allowlist is EXACTLY two entries. A third would be a reviewed edit here, not a
     quiet addition somewhere else.
  3. Each in-repo metric's emitter has at least one caller in `src/` OUTSIDE the observability
     module — the "dead library" detector the audit asked for. A helper only its own module calls
     is exactly the shape `setup_observability` had (AF-13).
  4. Every graph-invocation seam in `src/` counts agent errors (no un-instrumented sixth turn).
It does NOT assert the alerts are well-tuned, that the thresholds are right, or that a metric is
emitted on the correct code path — those are behavioural claims, and the behavioural proofs sit
below in this same file and in the worker-metrics file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SRC: Final[Path] = _REPO_ROOT / "src" / "maezo"
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"

#: Metrics an alert reads that this repo does NOT and CANNOT emit, each named with the register id
#: of the slice that owns it. DECLARED, never skipped: the audit's finding was precisely that a
#: silent gap is indistinguishable from a healthy zero.
#:
#: * `maezo_dead_letter_queue_size` — the rule's own comment calls it "a synthetic metric — replace
#:   with actual Kafka DLQ metric from the OTel Collector's JMX exporter or Kafka Exporter when
#:   available" (`alert-rules.yml:118-120`). It is an EXPORTER series, not application telemetry.
#: * `kube_job_status_failed` — kube-state-metrics, for the lifecycle CronJobs
#:   (`alert-rules.yml:166-170`). Emitting it from `src/` would be fabricating a Kubernetes fact.
#:
#: Both belong to owner slice ALERTS-WITHOUT-METRICS-b (scrape targets are a `deploy/` change, and
#: `deploy/` is owner-gated for this work package).
EXTERNAL_ALERT_METRICS: Final[dict[str, str]] = {
    "maezo_dead_letter_queue_size": "ALERTS-WITHOUT-METRICS-b — Kafka/DLQ exporter series",
    "kube_job_status_failed": "ALERTS-WITHOUT-METRICS-b — kube-state-metrics series",
}

#: Every in-repo alert metric -> the `maezo.platform.observability` helper that writes it. The
#: fence requires this table to cover the alert file EXACTLY, so a new alert on an unemitted metric
#: fails here instead of shipping as a rule that can never fire.
ALERT_METRIC_EMITTERS: Final[dict[str, str]] = {
    "maezo_worker_execution_time_seconds": "record_worker_execution",
    "maezo_worker_error_count_total": "record_worker_error",
    "maezo_agent_errors_total": "record_agent_error",
    "maezo_tool_calls_total": "record_tool_call",
}

#: Files in `src/` that invoke a compiled LangGraph (`.ainvoke(`). EVERY one must count agent
#: errors — see `test_every_graph_invocation_in_src_counts_agent_errors`.
_GRAPH_INVOCATION_FILES: Final[frozenset[str]] = frozenset(
    {
        "runtime/harness.py",
        "platform/webhooks/whatsapp/dispatch.py",
        "agents/rafael/delegation.py",
        "agents/carolina/delegation.py",
        "agents/andre/delegation.py",
    }
)

#: PromQL suffixes a histogram/summary series carries that its declared metric name does not.
_SERIES_SUFFIXES: Final[tuple[str, ...]] = ("_bucket", "_count", "_sum")


# =================================================================================================
# Parsing the shipped alert file
# =================================================================================================


def _alert_exprs() -> list[tuple[str, str]]:
    """Every `(alert_name, expr)` in the shipped rules file."""
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    exprs: list[tuple[str, str]] = []
    for group in document["groups"]:
        for rule in group["rules"]:
            exprs.append((rule["alert"], rule["expr"]))
    return exprs


def _metric_names(expr: str) -> set[str]:
    """The metric names an `expr` reads.

    Strips, in order: quoted strings, `{...}` label matchers, `[...]` range selectors. What remains
    is identifiers and operators, and a metric is an identifier NOT followed by `(` — which is what
    separates `maezo_worker_execution_time_seconds_bucket` from `rate` and `histogram_quantile`.
    """
    cleaned = re.sub(r'"[^"]*"', " ", expr)
    cleaned = re.sub(r"\{[^}]*\}", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    return {
        match.group(1)
        for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(\(?)", cleaned)
        if not match.group(2)
    }


def _declared_series_names() -> dict[str, str]:
    """Map every series name `MetricsCollector` exposes -> the metric name declared in `src/`.

    Built from `registry.collect()` rather than from sampled values because a LABELLED metric with
    no observation yet yields no samples at all — asking the registry for samples would make this
    check pass or fail depending on what ran before it.

    The mapping direction matters: an alert reads `maezo_worker_execution_time_seconds_bucket`, but
    the code declares `maezo_worker_execution_time_seconds`. A fence that could not connect those
    two would flag a correctly-emitted metric as missing.
    """
    from maezo.runtime.metrics import MetricsCollector

    series: dict[str, str] = {}
    for family in MetricsCollector().registry.collect():
        base = family.name
        # The declared NAME is what the code passes to `Counter(...)`/`Histogram(...)`;
        # prometheus_client strips a trailing `_total` from a counter for the family name, so it
        # is restored here rather than guessed at the call site.
        declared = f"{base}_total" if family.type == "counter" else base
        series[base] = declared
        if family.type == "counter":
            series[f"{base}_total"] = declared
        elif family.type in {"histogram", "summary"}:
            for suffix in _SERIES_SUFFIXES:
                series[f"{base}{suffix}"] = declared
    return series


def _declared_metric_name(series: str, declared: dict[str, str]) -> str:
    """Map a PromQL series name back to the metric name the collector declares."""
    return declared.get(series, series)


def _all_alert_series() -> set[str]:
    return {name for _alert, expr in _alert_exprs() for name in _metric_names(expr)}


# =================================================================================================
# 1-3: the fence
# =================================================================================================


def test_the_alert_file_is_parsed_non_vacuously() -> None:
    """A fence that parsed nothing would pass everything. Anchor it to the shipped file."""
    exprs = _alert_exprs()
    assert len(exprs) >= 7, exprs
    names = {alert for alert, _expr in exprs}
    assert {"MaezoSLAAgentErrorRateHigh", "MaezoAgentCrashLoop"} <= names, sorted(names)
    series = _all_alert_series()
    assert "maezo_agent_errors_total" in series, sorted(series)
    assert "maezo_tool_calls_total" in series, sorted(series)


def test_every_alert_metric_is_declared_in_repo_or_named_external() -> None:
    """No alert may read a metric this repo neither emits nor explicitly disclaims (the fence)."""
    declared = _declared_series_names()
    unaccounted: list[str] = []
    for series in sorted(_all_alert_series()):
        if series in EXTERNAL_ALERT_METRICS:
            continue
        if _declared_metric_name(series, declared) in ALERT_METRIC_EMITTERS:
            continue
        unaccounted.append(series)
    assert not unaccounted, (
        f"alert rule(s) read metric(s) {unaccounted} that are neither declared+emitted in `src/` "
        "nor listed in EXTERNAL_ALERT_METRICS with an owning register id. An alert on a metric "
        "nothing writes cannot fire — that is gap ALERTS-WITHOUT-METRICS-a. Either emit it, or "
        "declare it external here with the slice that owns it."
    )


def test_the_emitter_table_covers_the_alert_file_exactly() -> None:
    """`ALERT_METRIC_EMITTERS` must not drift ahead of, or behind, the shipped rules."""
    declared = _declared_series_names()
    in_repo = {
        _declared_metric_name(series, declared)
        for series in _all_alert_series()
        if series not in EXTERNAL_ALERT_METRICS
    }
    assert in_repo == set(ALERT_METRIC_EMITTERS), (
        f"alert file reads {sorted(in_repo)}; the emitter table maps "
        f"{sorted(ALERT_METRIC_EMITTERS)}. They must agree."
    )


def test_external_metric_allowlist_is_exactly_the_two_owner_slice_series() -> None:
    """The escape hatch is TWO named series, and widening it is a reviewed edit to this list."""
    assert set(EXTERNAL_ALERT_METRICS) == {
        "maezo_dead_letter_queue_size",
        "kube_job_status_failed",
    }
    for series, reason in EXTERNAL_ALERT_METRICS.items():
        assert "ALERTS-WITHOUT-METRICS-b" in reason, (series, reason)
        # And they must genuinely be absent from our own registry — an "external" metric that we
        # actually emit would be a mislabel that hides a real in-repo gap.
        assert series not in _declared_series_names(), series
        assert series not in ALERT_METRIC_EMITTERS, series


def test_every_alert_metric_emitter_has_a_caller_outside_the_observability_module() -> None:
    """The DEAD-LIBRARY detector. A helper only its own module mentions is not wired to anything.

    This is the check that would have been red before AF-13: `record_tool_call` /
    `record_agent_error` did not exist, and the counters they now write had no writer at all. It
    stays red for any future emitter that is defined and then never called — the exact shape
    `setup_observability` had for a year.
    """
    observability = _SRC / "platform" / "observability.py"
    sources = [path for path in _SRC.rglob("*.py") if path != observability]
    orphans: list[str] = []
    for metric, emitter in sorted(ALERT_METRIC_EMITTERS.items()):
        callers = [
            str(path.relative_to(_SRC)) for path in sources if emitter in path.read_text(encoding="utf-8")
        ]
        if not callers:
            orphans.append(f"{metric} (emitter {emitter}())")
    assert not orphans, (
        f"no caller anywhere in src/ for: {orphans}. The metric is declared, the helper exists, "
        "and nothing writes it — the alert built on it can never fire."
    )


def test_every_graph_invocation_in_src_counts_agent_errors() -> None:
    """Enumerate the turn-execution seams, and require every one of them to count failures.

    A sixth `.ainvoke(` added without instrumentation would silently shrink the coverage of
    `MaezoAgentCrashLoop` — this makes that a test failure rather than a discovery during an
    incident. Both halves are asserted: the file set is closed, AND each file in it actually calls
    `record_agent_error`.
    """
    found = {
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if ".ainvoke(" in path.read_text(encoding="utf-8")
    }
    assert found == set(_GRAPH_INVOCATION_FILES), (
        f"graph-invocation sites changed: found {sorted(found)}, declared "
        f"{sorted(_GRAPH_INVOCATION_FILES)}. A new one must count agent errors and be listed here."
    )
    missing = [
        name
        for name in sorted(found)
        if "record_agent_error" not in (_SRC / name).read_text(encoding="utf-8")
    ]
    assert not missing, f"graph-invocation site(s) that never count a failed turn: {missing}"


# =================================================================================================
# 4: behavioural proofs — the counters actually move
# =================================================================================================


def _sample(name: str) -> float:
    from maezo.platform.observability import get_metrics_collector

    value = get_metrics_collector().registry.get_sample_value(name)
    return float(value or 0.0)


@pytest.mark.asyncio
async def test_gate_counts_a_tool_call() -> None:
    """One `gate()` call == one `maezo_tool_calls_total` increment (cited from `seams/_base.py`)."""
    from maezo.gateway.seams._base import SeamContext, gate

    seam = SeamContext(tenant="amh", principal="helena")
    before = _sample("maezo_tool_calls_total")
    await gate(seam, "inference.generate")
    after = _sample("maezo_tool_calls_total")

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_denied_gate_still_counts_the_attempted_tool_call() -> None:
    """The alert's denominator is "calls the agents made", not "calls the policy allowed".

    Under the shipped manifest nothing is ratified, so an unknown operation is a DENY at L-0. The
    counter must already have moved by then — it is incremented before the ladder runs.
    """
    from maezo.gateway.seams._base import SeamContext, gate

    seam = SeamContext(tenant="amh", principal="helena")
    before = _sample("maezo_tool_calls_total")
    decision = await gate(seam, "operacao.que.nao.existe")
    after = _sample("maezo_tool_calls_total")

    assert decision.allow is False
    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_failed_agent_turn_counts_an_agent_error() -> None:
    """`Harness.invoke` raising == one `maezo_agent_errors_total` increment, and the real error
    still reaches the caller unchanged."""
    from maezo.runtime.harness import Harness

    class _BoomError(RuntimeError):
        pass

    harness = Harness()
    graph = harness.create_graph()

    async def _explode(_state: dict[str, Any]) -> dict[str, Any]:
        raise _BoomError("node failed")

    # Replace the default node's body by rebuilding a one-node graph with the same shape.
    from langgraph.graph import StateGraph

    exploding: StateGraph[Any] = StateGraph(dict)
    exploding.add_node("agent", _explode)
    exploding.add_edge("__start__", "agent")
    exploding.add_edge("agent", "__end__")
    harness._graph = exploding  # noqa: SLF001 — driving the seam directly is the point of the test
    harness._compiled = None  # noqa: SLF001
    assert graph is not None

    before = _sample("maezo_agent_errors_total")
    with pytest.raises(_BoomError):
        await harness.invoke({"messages": []})
    after = _sample("maezo_agent_errors_total")

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_completed_agent_turn_counts_no_error() -> None:
    """The counter must not move on the happy path — otherwise `MaezoAgentCrashLoop` fires always."""
    from maezo.runtime.harness import Harness

    harness = Harness()
    harness.create_graph()

    before = _sample("maezo_agent_errors_total")
    await harness.invoke({"messages": ["oi"]})
    after = _sample("maezo_agent_errors_total")

    assert after == before, (before, after)


@pytest.mark.asyncio
async def test_a_drained_turn_is_not_an_agent_error() -> None:
    """A turn cancelled from OUTSIDE (a rolling deploy, a hung-up client) is NOT a failed agent.

    Counting it would make `MaezoAgentCrashLoop` (`rate(maezo_agent_errors_total[1m]) > 0` for 2m)
    fire critical on every deployment, which is how an alert gets ignored. `Harness.invoke` catches
    `Exception`, deliberately not `BaseException`, and `asyncio.CancelledError` is the latter.

    The cancellation is delivered the way a drain delivers it — `task.cancel()` on the coroutine
    awaiting the turn — NOT by raising `CancelledError` inside a node: langgraph deliberately
    converts a node-raised cancellation into `NodeCancelledError` (an ordinary `Exception`, see
    `langgraph/pregel/_retry.py`) precisely so a node that swallows its own failure is not reported
    as success. That case IS a failed turn and IS counted; this test is about the other one.
    """
    import asyncio

    from langgraph.graph import StateGraph

    from maezo.runtime.harness import Harness

    entered = asyncio.Event()

    async def _hang(_state: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        await asyncio.Event().wait()  # never completes; the drain cancels us
        raise AssertionError("unreachable")

    harness = Harness()
    graph: StateGraph[Any] = StateGraph(dict)
    graph.add_node("agent", _hang)
    graph.add_edge("__start__", "agent")
    graph.add_edge("agent", "__end__")
    harness._graph = graph  # noqa: SLF001 — driving the seam directly is the point of the test
    harness._compiled = None  # noqa: SLF001

    before = _sample("maezo_agent_errors_total")
    turn = asyncio.create_task(harness.invoke({"messages": []}))
    await entered.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    after = _sample("maezo_agent_errors_total")

    assert after == before, (before, after)


def test_the_two_agent_counters_are_label_free_so_the_ratio_alert_can_match() -> None:
    """`MaezoSLAAgentErrorRateHigh` DIVIDES the two counters, so their label sets must be equal.

    PromQL's default vector matching requires identical label sets on both sides of a binary
    operation. Giving `maezo_tool_calls_total` a `tool` label that `maezo_agent_errors_total`
    cannot carry would make the division return an empty vector — an alert that never fires, i.e.
    the same defect ALERTS-WITHOUT-METRICS-a exists to close, re-created in a subtler form. This
    pins the shape until `alert-rules.yml` (owner-gated, `deploy/`) is edited to match.
    """
    from maezo.runtime.metrics import MetricsCollector

    collector = MetricsCollector()
    assert collector.tool_calls._labelnames == ()  # noqa: SLF001 — the property under test
    assert collector.errors._labelnames == ()  # noqa: SLF001
