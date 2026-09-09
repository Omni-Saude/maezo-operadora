"""ALERT-RULES-VECTOR-MATCH-MISMATCH: every ratio (division) rule in `alert-rules.yml` must align
its numerator and denominator's label sets, or PromQL's default vector matching silently produces
an EMPTY VECTOR and the rule can never fire/resolve — indistinguishable, on a dashboard, from a
healthy zero.

WHY THIS FENCE EXISTS. `MaezoSLAWorkerErrorRateHigh` divided `maezo_worker_error_count_total`
(labelnames=["worker","topic","error_type"], `runtime/metrics.py`) by
`maezo_worker_execution_time_seconds_count` (labelnames=["worker","topic"]) with NEITHER side
wrapped in an aggregating `sum by (...)`/`sum(...)` — PromQL's default vector matching for a binary
`/` requires the two operands' label sets to match EXACTLY, so every numerator sample (carrying the
extra `error_type` label) had no denominator sample to pair with, and the division always produced
an empty result. The sibling `MaezoSLAAgentErrorRateHigh` avoided the same trap by wrapping BOTH
sides in `sum by (agent) (...)` (`ALERT-COUNTER-LABELS`/R-063) — this fence generalizes that
precedent's shape into a rule ANY future ratio-shaped alert/recording-rule must satisfy, so the
defect class cannot recur silently.

THE INVARIANT (tree-derived from `deploy/observability/alert-rules.yml`, never a hardcoded list of
"which alerts are OK"): for every rule whose `expr` contains a top-level `/`, each operand must be
wrapped in `sum(...)` or `sum by (<labels>) (...)`, and the two operands' `by (...)` label SETS
(both empty counts as a match — a bare `sum(...)` on both sides collapses every label away
identically) must be equal. A bare, unwrapped division (the exact `MaezoSLAWorkerErrorRateHigh`
pre-fix shape) fails this fence by construction.

RED proof (reproduced this session, reverted before commit): dropping `sum by (worker, topic)`
from either side of `MaezoSLAWorkerErrorRateHigh`'s expr in `alert-rules.yml` makes
`test_every_ratio_rules_operands_share_their_aggregation_label_set` fail, naming the rule and the
two mismatched label sets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"

#: `sum` or `sum by (a, b, ...)` immediately followed by the opening `(` of its argument.
_SUM_PREFIX_RE = re.compile(r"^\s*sum\s*(?:by\s*\(\s*([^)]*)\s*\))?\s*\(")


def _all_rule_exprs() -> list[tuple[str, str]]:
    """Every `(name, expr)` in the shipped rules file — `alert:` and `record:` rules alike, since
    the vector-matching hazard applies identically to both (three `record:` ratios already ship,
    see `maezo_helena_resolution_rate` et al.)."""
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    rules: list[tuple[str, str]] = []
    for group in document["groups"]:
        for rule in group["rules"]:
            name = rule.get("alert") or rule.get("record")
            assert name, f"rule with neither `alert` nor `record` key: {rule!r}"
            rules.append((name, rule["expr"]))
    return rules


def _strip_wrapping_parens_and_comparison(expr: str) -> str:
    """Strip a `> <threshold>` tail and ONE pair of parens that wraps the whole expression, so
    `(A / B) > 0.05` and bare `A\\n/\\nB` both reduce to the same `A / B` shape before splitting."""
    text = expr.strip()
    if text.startswith("("):
        depth = 0
        close_idx = None
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_idx = index
                    break
        assert close_idx is not None, f"unbalanced parens in expr: {expr!r}"
        text = text[1:close_idx].strip()
    return text


def _split_top_level_division(expr: str) -> tuple[str, str] | None:
    """Split `expr` on the first `/` at bracket-depth 0. Returns None if there is none — most
    rules (rates, histogram_quantile, counters with no ratio) are not divisions at all, and this
    fence has nothing to say about them."""
    text = _strip_wrapping_parens_and_comparison(expr)
    depth = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "/" and depth == 0:
            return text[:index], text[index + 1 :]
    return None


def _aggregation_label_set(operand: str) -> frozenset[str] | None:
    """The label set an operand's leading `sum(...)`/`sum by (...)` reduces TO.

    `sum(...)` (no `by`) drops every label -> empty frozenset. `sum by (a, b) (...)` keeps exactly
    `{a, b}`. Returns None when the operand has NO leading `sum` aggregation at all — the exact
    unsafe shape (a raw `rate(...)` divided directly) this fence must catch, since two counters
    with different native label sets are then vector-matched on their FULL native labels, which
    is almost never identical between two different metrics.
    """
    match = _SUM_PREFIX_RE.match(operand.strip())
    if not match:
        return None
    labels_str = match.group(1)
    if labels_str is None:
        return frozenset()
    return frozenset(label.strip() for label in labels_str.split(",") if label.strip())


def test_every_ratio_rules_operands_share_their_aggregation_label_set() -> None:
    """The generalized R-198-sibling invariant. Every `/` in `alert-rules.yml` must have both
    operands wrapped in a `sum`/`sum by (...)` that reduces to the SAME label set — never a bare,
    unaggregated division of two counters (the pre-fix `MaezoSLAWorkerErrorRateHigh` shape) and
    never two aggregations that disagree on which labels survive.
    """
    violations: list[str] = []
    for name, expr in _all_rule_exprs():
        split = _split_top_level_division(expr)
        if split is None:
            continue
        numerator, denominator = split
        num_labels = _aggregation_label_set(numerator)
        den_labels = _aggregation_label_set(denominator)
        if num_labels is None or den_labels is None:
            unaggregated = "numerator" if num_labels is None else "denominator"
            violations.append(
                f"{name}: {unaggregated} of its ratio has no `sum(...)`/`sum by (...)` wrapper — "
                "PromQL default vector matching on raw series can silently empty-vector "
                f"(numerator={numerator.strip()!r}, denominator={denominator.strip()!r})"
            )
            continue
        if num_labels != den_labels:
            violations.append(
                f"{name}: numerator aggregates by {sorted(num_labels)}, denominator by "
                f"{sorted(den_labels)} — label sets must match for the division to vector-match"
            )
    assert not violations, "vector-matching hazard(s) in alert-rules.yml:\n" + "\n".join(violations)


def test_the_worker_error_rate_alert_is_the_named_fix_and_not_accidentally_reverted() -> None:
    """Names the specific rule ALERT-RULES-VECTOR-MATCH-MISMATCH is about, so a revert of just
    this one rule (e.g. someone "simplifying" the expr back to a bare division) fails by NAME here,
    not only via the generic sweep above."""
    exprs = dict(_all_rule_exprs())
    expr = exprs["MaezoSLAWorkerErrorRateHigh"]
    split = _split_top_level_division(expr)
    assert split is not None, "MaezoSLAWorkerErrorRateHigh's expr is no longer a ratio"
    numerator, denominator = split
    assert _aggregation_label_set(numerator) == frozenset({"worker", "topic"})
    assert _aggregation_label_set(denominator) == frozenset({"worker", "topic"})


def test_the_worker_error_rate_annotation_does_not_reference_a_label_the_expr_drops() -> None:
    """`$labels.error_type` cannot survive a `sum by (worker, topic)` that drops `error_type` —
    Grafana/Alertmanager renders it as an empty string, which reads as a data bug in a page. The
    pre-fix annotation referenced it; the fix must not silently reintroduce that reference."""
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    for group in document["groups"]:
        for rule in group["rules"]:
            if rule.get("alert") != "MaezoSLAWorkerErrorRateHigh":
                continue
            description = rule["annotations"]["description"]
            assert "error_type" not in description, (
                "MaezoSLAWorkerErrorRateHigh's annotation references `error_type`, a label its "
                "`sum by (worker, topic)` expr no longer carries"
            )
            return
    raise AssertionError("alert MaezoSLAWorkerErrorRateHigh not found")
