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
  4. Every graph-invocation seam in `src/` counts agent errors — enumerated STRUCTURALLY, per call
     site: the seams are derived from the AST (`<expr>.ainvoke(`), and each site must sit inside a
     `try` whose `except Exception` handler CALLS `record_agent_error()` and re-raises bare. The
     gated-seam chokepoint (`gate()` -> `_count_tool_call` -> `record_tool_call`) gets the mirror
     check, including that it does NOT re-raise.
It does NOT assert the alerts are well-tuned, that the thresholds are right, or that a metric is
emitted on the correct code path — those are behavioural claims, and the behavioural proofs sit
below in this same file and in the worker-metrics file.

WHY (4) IS AST AND NOT `substring in file` (WP-COMPOSICAO-V2 review, MAJOR-3). The first cut of
this fence checked the file SET structurally but each file's instrumentation with
`"record_agent_error" not in path.read_text()`. Every seam carries a comment that names the helper,
so deleting the CALL from four of the five seams — including Helena's live WhatsApp receiver — left
this file and the whole `tests/unit/{agents,platform,runtime,a2a}` suite green. That is the very
defect class ALERTS-WITHOUT-METRICS-a exists to close, re-created inside its own guard.
"""

from __future__ import annotations

import ast
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
#: * `kube_job_annotations` — also kube-state-metrics (R-040/SC-07): the info-metric that exports
#:   the `maezo.io/expected-fail-until` Job annotation as the label
#:   `annotation_maezo_io_expected_fail_until`, which `MaezoLifecycleJobFailed`'s `unless` clause
#:   joins on to exclude by-design-failing lifecycle jobs while their marker is in date. Same
#:   external-to-`src/` reasoning as `kube_job_status_failed` — it is the cluster's own annotation
#:   echoed back, not application telemetry.
#:
#: All three belong to owner slice ALERTS-WITHOUT-METRICS-b / R-040 (scrape targets and cluster
#: annotations are a `deploy/` change, and `deploy/` is owner-gated for this work package).
EXTERNAL_ALERT_METRICS: Final[dict[str, str]] = {
    "maezo_dead_letter_queue_size": "ALERTS-WITHOUT-METRICS-b — Kafka/DLQ exporter series",
    "kube_job_status_failed": "ALERTS-WITHOUT-METRICS-b — kube-state-metrics series",
    "kube_job_annotations": "R-040/SC-07 — kube-state-metrics annotation-derived series",
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

#: A NON-VACUITY FLOOR, not the closed set. The set of graph-invocation seams is DERIVED from the
#: AST of `src/` by `_ainvoke_lines()`/`_uninstrumented_graph_invocations()`; a sixth seam requires
#: instrumentation, never an edit here. What this frozenset defends against is the opposite
#: failure — an AST walk that stops finding anything (a rename, a refactor into a helper, a bug in
#: the walk itself) would make a fence over an EMPTY set pass trivially. Losing one of these five
#: is a reviewed edit; gaining a sixth is not.
_GRAPH_INVOCATION_FLOOR: Final[frozenset[str]] = frozenset(
    {
        "runtime/harness.py",
        "platform/webhooks/whatsapp/dispatch.py",
        "agents/rafael/delegation.py",
        "agents/carolina/delegation.py",
        "agents/andre/delegation.py",
    }
)

#: The one gated-seam chokepoint, and the helper it must call. Every gated seam (dmn, cibseven,
#: fhir, whatsapp, inference, population, a2a) passes through `gate()` exactly once per call.
_GATE_CHOKEPOINT_FILE: Final[str] = "gateway/seams/_base.py"

#: PromQL suffixes a histogram/summary series carries that its declared metric name does not.
_SERIES_SUFFIXES: Final[tuple[str, ...]] = ("_bucket", "_count", "_sum")


# =================================================================================================
# Parsing the shipped alert file
# =================================================================================================


def _alert_exprs() -> list[tuple[str, str]]:
    """Every `(alert_name, expr)` in the shipped rules file.

    ALERTS-WITHOUT-METRICS-b / R-056 added a `record:` rule (`maezo_dead_letter_derived` group,
    deriving `maezo_dead_letter_queue_size` from the Kafka Exporter) alongside the `alert:` rules.
    A Prometheus recording rule has no `alert` key, so it is skipped here — this function is
    specifically about ALERTS, and the recording rule gets its own non-vacuity proof below
    (`test_the_dlq_recording_rule_derives_from_the_kafka_exporter`).
    """
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    exprs: list[tuple[str, str]] = []
    for group in document["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            exprs.append((rule["alert"], rule["expr"]))
    return exprs


def _recording_rules() -> list[tuple[str, str]]:
    """Every `(record_name, expr)` in the shipped rules file — the mirror of `_alert_exprs()`."""
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    records: list[tuple[str, str]] = []
    for group in document["groups"]:
        for rule in group["rules"]:
            if "record" not in rule:
                continue
            records.append((rule["record"], rule["expr"]))
    return records


#: PromQL vector-matching / set-operator keywords that read as bare identifiers not followed by
#: `(` (the same shape as a metric name) but are never one: `unless`/`and`/`or` are binary set
#: operators (R-040/SC-07's `... unless on (job_name) (...)` is the first shipped user of
#: `unless`), and `bool` is the comparison-operator modifier (`> bool 0`). `on`/`ignoring` ARE
#: followed by `(` and so are already excluded by the not-a-call test below, but are named here too
#: for readers matching this set against the PromQL grammar.
_PROMQL_KEYWORDS: Final[frozenset[str]] = frozenset({"and", "or", "unless", "bool"})


def _metric_names(expr: str) -> set[str]:
    """The metric names an `expr` reads.

    Strips, in order: quoted strings, `{...}` label matchers, `[...]` range selectors, and
    grouping/vector-matching clauses (`by (...)`/`without (...)`/`on (...)`/`ignoring (...)`/
    `group_left(...)`/`group_right(...)`) — a PromQL label list in any of these (e.g.
    `sum by (agent) (...)`, `unless on (job_name) (...)`) is neither a metric nor a function call,
    and left unstripped both the clause keyword (`sum`, `on` — not followed directly by `(`... but
    the label INSIDE the parens (`agent`, `job_name` — followed by `)` not `(`) would be
    misidentified as a metric name by the heuristic below. What remains after those strips is
    identifiers and operators; `_PROMQL_KEYWORDS` removes the binary set-operators/modifiers that
    still read as bare identifiers (`unless`, `and`, `or`, `bool`) after the strip. A metric is
    what is left: an identifier NOT followed by `(` and not a known keyword — which is what
    separates `maezo_worker_execution_time_seconds_bucket` from `rate`/`histogram_quantile`
    (function calls) and from `unless`/`on` (operators).
    """
    cleaned = re.sub(r'"[^"]*"', " ", expr)
    cleaned = re.sub(r"\{[^}]*\}", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    cleaned = re.sub(r"\b(?:by|without|on|ignoring)\s*\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\bgroup_(?:left|right)\s*\([^)]*\)", " ", cleaned)
    return {
        match.group(1)
        for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(\(?)", cleaned)
        if not match.group(2) and match.group(1) not in _PROMQL_KEYWORDS
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


def _emitter_call_sites() -> dict[str, list[str]]:
    """Emitter name -> the `src/` files that actually CALL it, outside the observability module.

    AST, not `substring in text`, and the difference IS the fence. A textual match is satisfied by
    a docstring that merely NAMES the helper — which is precisely the state a dead library is in:
    extensively described, never invoked. Verified by construction: deleting the real call from
    `gateway/seams/_base.py` while leaving its docstring intact keeps a textual check GREEN and
    turns this one RED.
    """
    observability = _SRC / "platform" / "observability.py"
    wanted = set(ALERT_METRIC_EMITTERS.values())
    sites: dict[str, list[str]] = {name: [] for name in wanted}
    for path in sorted(_SRC.rglob("*.py")):
        if path == observability:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name: str | None = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in wanted:
                sites[str(name)].append(str(path.relative_to(_SRC)))
    return sites


# =================================================================================================
# Structural instrumentation checks — AST, per call site (NOT `substring in file`)
# =================================================================================================


def _called_names(node: ast.AST) -> set[str]:
    """Every function/method NAME called anywhere under `node`. `f()` and `obj.f()` both count."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _ainvoke_lines(node: ast.AST) -> set[int]:
    """Line numbers of every `<expr>.ainvoke(` CALL under `node` — a graph run, structurally."""
    return {
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "ainvoke"
    }


def _handler_catches_exception(handler: ast.ExceptHandler) -> bool:
    """`except Exception` (alone or in a tuple). `BaseException` and bare `except:` do NOT qualify.

    Deliberate: `asyncio.CancelledError` is a `BaseException` and means "the pod is draining", not
    "the agent failed". A seam that widened its handler to `BaseException` would start counting
    every rolling deploy as an agent error and must fail this fence, not pass it.
    """
    kind = handler.type
    if kind is None:  # bare `except:` — catches BaseException too.
        return False
    candidates = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    named = {c.id for c in candidates if isinstance(c, ast.Name)}
    return named == {"Exception"}


def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """A bare `raise` somewhere in the handler body — nothing swallowed, nothing retyped."""
    return any(isinstance(child, ast.Raise) and child.exc is None for child in ast.walk(handler))


def _uninstrumented_graph_invocations() -> list[str]:
    """Every `.ainvoke(` site in `src/` NOT wrapped in a counting, re-raising `except Exception`.

    THIS is the check, and it is per SITE, not per file. The version this replaces asked
    `"record_agent_error" not in path.read_text()`, which the whole-file substring made satisfiable
    by a COMMENT — so the counter could be deleted from four of the five seams (including Helena's
    live WhatsApp receiver) with this fence green. Reproduced by the gatekeeper; see the mutation
    table in `test_every_graph_invocation_in_src_counts_agent_errors`.
    """
    uninstrumented: list[str] = []
    for path, tree in _src_trees():
        sites = _ainvoke_lines(tree)
        if not sites:
            continue
        covered: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            guarded = {line for stmt in node.body for line in _ainvoke_lines(stmt)}
            if not guarded:
                continue
            if any(
                _handler_catches_exception(handler)
                and "record_agent_error" in _called_names(handler)
                and _handler_reraises(handler)
                for handler in node.handlers
            ):
                covered |= guarded
        uninstrumented += [f"{path.relative_to(_SRC)}:{line}" for line in sorted(sites - covered)]
    return uninstrumented


def _src_trees() -> list[tuple[Path, ast.Module]]:
    """Every `src/maezo` module, parsed once. Sorted, so failures name files in a stable order."""
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(_SRC.rglob("*.py"))
    ]


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no function named {name!r} — the fence's anchor moved, so it is vacuous")


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


def test_the_dlq_recording_rule_derives_from_the_kafka_exporter() -> None:
    """ALERTS-WITHOUT-METRICS-b / R-056: `maezo_dead_letter_queue_size` is now DERIVED, not absent.

    Both DLQ alerts still read `maezo_dead_letter_queue_size` — still correctly EXTERNAL from
    `src/`'s point of view (see `EXTERNAL_ALERT_METRICS`), since the ultimate source is the Kafka
    Exporter, not application code. What changed is that the series is no longer undefined: a
    `record:` rule in the shipped file derives it from `kafka_topic_partition_current_offset`
    (danielqsj/kafka_exporter, prometheus.yml job `kafka-exporter`). This is the non-vacuity proof
    that the recording rule exists, targets the right name, and reads the exporter's real metric —
    not merely that `_alert_exprs()` tolerates a `record:` entry without crashing.
    """
    records = _recording_rules()
    names = {name for name, _expr in records}
    assert "maezo_dead_letter_queue_size" in names, sorted(names)
    (expr,) = [expr for name, expr in records if name == "maezo_dead_letter_queue_size"]
    assert "kafka_topic_partition_current_offset" in expr, expr
    assert "environment" in expr, expr


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


def test_external_metric_allowlist_is_exactly_the_three_owner_slice_series() -> None:
    """The escape hatch is THREE named series, and widening it is a reviewed edit to this list."""
    assert set(EXTERNAL_ALERT_METRICS) == {
        "maezo_dead_letter_queue_size",
        "kube_job_status_failed",
        "kube_job_annotations",
    }
    for series, reason in EXTERNAL_ALERT_METRICS.items():
        assert "ALERTS-WITHOUT-METRICS-b" in reason or "R-040/SC-07" in reason, (series, reason)
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
    called = _emitter_call_sites()
    orphans = [
        f"{metric} (emitter {emitter}())"
        for metric, emitter in sorted(ALERT_METRIC_EMITTERS.items())
        if not called[emitter]
    ]
    assert not orphans, (
        f"no caller anywhere in src/ for: {orphans}. The metric is declared, the helper exists, "
        "and nothing writes it — the alert built on it can never fire."
    )


def test_every_graph_invocation_in_src_counts_agent_errors() -> None:
    """EVERY `.ainvoke(` call site in `src/` runs inside a counting, re-raising `except Exception`.

    The set of seams is DERIVED from the AST, not declared: a sixth graph invocation needs
    instrumentation, not an entry in a list here, so the fence cannot be satisfied by editing the
    list instead of the code. `_GRAPH_INVOCATION_FLOOR` only guards the vacuous direction.

    MUTATION RESULTS. Throwaway `rsync` copy of the worktree (`.git`/`.venv`/`__pycache__`
    excluded), worktree venv reused via `UV_PROJECT_ENVIRONMENT`, `PYTHONDONTWRITEBYTECODE=1`;
    the copy was deleted afterwards. Baseline on the copy: **14 passed**. Command per mutant:
    `uv run pytest tests/unit/platform/test_alert_metrics_fence.py -q -p no:cacheprovider`.

      | mutant                                                            | result           |
      |-------------------------------------------------------------------|------------------|
      | M1 delete `record_agent_error()` from `dispatch.py` ONLY, keeping   | RED 1/14 (this)  |
      |    the import and the comment that names the helper                 |                  |
      | M2 delete it from all four NON-harness seams (rafael/carolina/      | RED 1/14 (this)  |
      |    andre delegation + `dispatch.py`), keeping every comment         |                  |
      | M3 widen `dispatch.py`'s handler to `except BaseException`          | RED 1/14 (this)  |
      | M4 drop the bare `raise` from `dispatch.py`'s handler               | RED 1/14 (this)  |
      | M5 add a sixth, uninstrumented `.ainvoke(` site in a new module     | RED 1/14 (this)  |
      | M6 delete `record_tool_call()` from `_count_tool_call`              | RED 4/14         |
      | M7 delete `_count_tool_call(operation)` from `gate()`               | RED 3/14         |

    CONTROL, on the same M1 tree: the check this replaces
    (`"record_agent_error" not in path.read_text()`) stays GREEN — 2 textual occurrences of the
    name survive in `dispatch.py` (the import line and the explanatory comment) with **0 calls**.
    That is the gatekeeper's E3, and it is why M1 and M2 are the load-bearing rows above: the
    counter could be deleted from Helena's live WhatsApp receiver and all three A2A delegation
    targets with this file — and the whole `tests/unit/{agents,platform,runtime,a2a}` suite —
    green.
    """
    uninstrumented = _uninstrumented_graph_invocations()
    assert not uninstrumented, (
        f"graph-invocation site(s) whose failures are never counted: {uninstrumented}. Each "
        "`.ainvoke(` must sit in a `try` whose `except Exception` handler calls "
        "`record_agent_error()` and re-raises bare — `MaezoAgentCrashLoop` reads that counter and "
        "nothing else does."
    )


def test_the_graph_invocation_walk_is_not_vacuous() -> None:
    """A derived fence over an EMPTY set passes everything. Anchor it to the five known seams."""
    found = {str(path.relative_to(_SRC)) for path, tree in _src_trees() if _ainvoke_lines(tree)}
    assert found >= _GRAPH_INVOCATION_FLOOR, (
        f"graph-invocation seam(s) disappeared from the AST walk: "
        f"{sorted(_GRAPH_INVOCATION_FLOOR - found)}. Either they were genuinely removed (a "
        "reviewed edit to this floor) or the walk stopped seeing them, which would make "
        "`test_every_graph_invocation_in_src_counts_agent_errors` vacuous."
    )


def test_the_gated_seam_chokepoint_counts_every_tool_call() -> None:
    """`gate()` counts BEFORE the ladder, and the counter cannot break an effect call.

    Structural for the same reason as the agent-error seam: `maezo_tool_calls_total` is the
    DENOMINATOR of `MaezoSLAAgentErrorRateHigh`, so a `gate()` that stopped counting would not
    make the alert noisy — it would make it silently unfireable, which is the exact defect
    ALERTS-WITHOUT-METRICS-a names. Three properties, all read off the AST:

      1. `gate()`'s FIRST statement is the count. Anywhere later and a denial would return before
         counting, so the denominator would become "calls the policy allowed" — a different, and
         wrong, quantity.
      2. `_count_tool_call` really calls `record_tool_call` (not merely mentions it).
      3. Its handler catches `Exception` and does NOT re-raise — the OPPOSITE of the agent-error
         seam, deliberately: telemetry must never reach a care path. A re-raise here would let a
         metrics fault deny an effect.
    """
    tree = ast.parse((_SRC / _GATE_CHOKEPOINT_FILE).read_text(encoding="utf-8"))

    gate = _function_def(tree, "gate")
    statements = list(gate.body)
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant):
        statements = statements[1:]  # the docstring is not a statement for this purpose
    assert statements, "`gate()` has no body — the rest of this test would be vacuous"
    assert "_count_tool_call" in _called_names(statements[0]), (
        f"`gate()`'s first statement is {ast.dump(statements[0])[:120]}, not the tool-call count. "
        "A denial must already have been counted by the time it returns."
    )

    counter = _function_def(tree, "_count_tool_call")
    tries = [node for node in ast.walk(counter) if isinstance(node, ast.Try)]
    assert tries, "`_count_tool_call` must guard the emitter — a metric fault cannot break an effect"
    assert any("record_tool_call" in _called_names(node) for node in tries), (
        "`_count_tool_call` never CALLS `record_tool_call` — naming it in a docstring is exactly "
        "the dead-library shape this fence exists to reject."
    )
    for node in tries:
        for handler in node.handlers:
            assert _handler_catches_exception(handler), ast.dump(handler)[:120]
            assert not _handler_reraises(handler), (
                "the tool-call counter re-raises: a telemetry fault would deny a gated effect."
            )


# =================================================================================================
# 4: behavioural proofs — the counters actually move
# =================================================================================================


def _sample(name: str, **labels: str) -> float:
    """One labelled series' value (ALERT-COUNTER-LABELS / R-063: both counters are labelled now,
    so `get_sample_value` needs the exact label dict a Prometheus scrape would carry — the metric
    NAME alone no longer identifies a series)."""
    from maezo.platform.observability import get_metrics_collector

    value = get_metrics_collector().registry.get_sample_value(name, labels or None)
    return float(value or 0.0)


@pytest.mark.asyncio
async def test_gate_counts_a_tool_call() -> None:
    """One `gate()` call == one `maezo_tool_calls_total` increment (cited from `seams/_base.py`)."""
    from maezo.gateway.seams._base import SeamContext, gate
    from maezo.runtime.metrics import AGENT_ERROR_TYPE_NONE

    seam = SeamContext(tenant="amh", principal="helena")
    labels = {"agent": "helena", "error_type": AGENT_ERROR_TYPE_NONE}
    before = _sample("maezo_tool_calls_total", **labels)
    await gate(seam, "inference.generate")
    after = _sample("maezo_tool_calls_total", **labels)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_denied_gate_still_counts_the_attempted_tool_call() -> None:
    """The alert's denominator is "calls the agents made", not "calls the policy allowed".

    Under the shipped manifest nothing is ratified, so an unknown operation is a DENY at L-0. The
    counter must already have moved by then — it is incremented before the ladder runs.
    """
    from maezo.gateway.seams._base import SeamContext, gate
    from maezo.runtime.metrics import AGENT_ERROR_TYPE_NONE

    seam = SeamContext(tenant="amh", principal="helena")
    labels = {"agent": "helena", "error_type": AGENT_ERROR_TYPE_NONE}
    before = _sample("maezo_tool_calls_total", **labels)
    decision = await gate(seam, "operacao.que.nao.existe")
    after = _sample("maezo_tool_calls_total", **labels)

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

    # `create_graph()` called with no `agent_id` above leaves `Harness._agent_id` None (the
    # "trivial default graph" case `record_agent_error`'s docstring names) -> "nao_declarado".
    # `_BoomError` is not in `_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS` (exact-class-name lookup, no
    # subclass walk — by design) -> falls back to `AGENT_ERROR_TYPE_OUTRO`.
    from maezo.runtime.metrics import AGENT_ERROR_TYPE_OUTRO

    labels = {"agent": "nao_declarado", "error_type": AGENT_ERROR_TYPE_OUTRO}
    before = _sample("maezo_agent_errors_total", **labels)
    with pytest.raises(_BoomError):
        await harness.invoke({"messages": []})
    after = _sample("maezo_agent_errors_total", **labels)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_failed_agent_turn_classifies_a_catalogued_exception_by_its_declared_type() -> None:
    """A `ValueError` (a cataloged class, unlike `_BoomError` above) gets its real bounded label."""
    from maezo.runtime.harness import Harness
    from maezo.runtime.metrics import AGENT_ERROR_TYPE_VALIDACAO

    async def _explode(_state: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad state")

    from langgraph.graph import StateGraph

    harness = Harness()
    harness.create_graph()  # default trivial graph — agent_id set directly below, not via
    # a real spec/agents/helena build (which needs inference/dmn/cibseven/whatsapp/audit_sink
    # deps this test does not construct; only the LABEL matters here, not a real Helena run).
    exploding: StateGraph[Any] = StateGraph(dict)
    exploding.add_node("agent", _explode)
    exploding.add_edge("__start__", "agent")
    exploding.add_edge("agent", "__end__")
    harness._graph = exploding  # noqa: SLF001
    harness._agent_id = "helena"  # noqa: SLF001 — driving the label directly is the point of the test
    harness._compiled = None  # noqa: SLF001

    labels = {"agent": "helena", "error_type": AGENT_ERROR_TYPE_VALIDACAO}
    before = _sample("maezo_agent_errors_total", **labels)
    with pytest.raises(ValueError, match="bad state"):
        await harness.invoke({"messages": []})
    after = _sample("maezo_agent_errors_total", **labels)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_completed_agent_turn_counts_no_error() -> None:
    """The counter must not move on the happy path — otherwise `MaezoAgentCrashLoop` fires always."""
    from maezo.runtime.harness import Harness
    from maezo.runtime.metrics import AGENT_ERROR_TYPE_OUTRO

    harness = Harness()
    harness.create_graph()

    labels = {"agent": "nao_declarado", "error_type": AGENT_ERROR_TYPE_OUTRO}
    before = _sample("maezo_agent_errors_total", **labels)
    await harness.invoke({"messages": ["oi"]})
    after = _sample("maezo_agent_errors_total", **labels)

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

    from maezo.runtime.metrics import AGENT_ERROR_TYPE_OUTRO

    labels = {"agent": "nao_declarado", "error_type": AGENT_ERROR_TYPE_OUTRO}
    before = _sample("maezo_agent_errors_total", **labels)
    turn = asyncio.create_task(harness.invoke({"messages": []}))
    await entered.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    after = _sample("maezo_agent_errors_total", **labels)

    assert after == before, (before, after)


def test_the_two_agent_counters_share_a_label_set_so_the_ratio_alert_can_aggregate_by_agent() -> None:
    """`MaezoSLAAgentErrorRateHigh` DIVIDES the two counters — ALERT-COUNTER-LABELS / R-063.

    This REPLACES the pre-R-063 label-free pin (`test_the_two_agent_counters_are_label_free_...`,
    docs/review-queue.md's now-resolved entry): the owner ratified widening both counters to
    `labelnames=["agent", "error_type"]` so the on-call knows WHICH agent failed, and
    `alert-rules.yml`'s `MaezoSLAAgentErrorRateHigh`/`MaezoAgentCrashLoop` were edited in the SAME
    PR to `sum by (agent) (...)` on both sides of the division/rate. That `by (agent)` aggregation
    — not label-SET equality — is what keeps PromQL's default vector matching valid despite
    `error_type` differing between the two counters in practice (`tool_calls` always carries the
    `AGENT_ERROR_TYPE_NONE` sentinel; `errors` carries a real classified value). This test pins the
    NEW shape: same label NAMES on both counters (so a future edit that drops one without
    updating the other is caught here), and that the shipped alert exprs actually use `by (agent)`.
    """
    from maezo.runtime.metrics import AGENT_ERROR_TYPES, MetricsCollector

    collector = MetricsCollector()
    assert collector.tool_calls._labelnames == ("agent", "error_type")  # noqa: SLF001
    assert collector.errors._labelnames == ("agent", "error_type")  # noqa: SLF001

    # AGENT_ERROR_TYPES is non-empty and bounded — the whole point of a "declared catalogue".
    assert AGENT_ERROR_TYPES, "the error_type catalogue must not be empty"
    assert all(isinstance(v, str) and v for v in AGENT_ERROR_TYPES)

    exprs = dict(_alert_exprs())
    for alert_name in ("MaezoSLAAgentErrorRateHigh", "MaezoAgentCrashLoop"):
        assert "by (agent)" in exprs[alert_name], (
            f"{alert_name}'s expr no longer aggregates `by (agent)` — with labelled counters this "
            "would either fail PromQL's default vector matching (division) or collapse every "
            "agent into one series again (rate)."
        )
