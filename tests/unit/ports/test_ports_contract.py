"""Structural contract of the five ports: taxonomy determinism, purpose/consent required-ness,
explicit deadlines, and the "success or refusal, never a raise" result shape (MZO-030).

These are the invariants the ports' whole value rests on, asserted as PROPERTIES of the declared
signatures and types rather than as prose in a docstring. A port that quietly grew a default for
`consent_decision_ref`, or lost its deadline parameter, or started declaring an exception type,
would still typecheck and still pass every behavioural test written against a fake — and would
silently reopen the hole ADR-0037 closes. That is why these are signature-level assertions.

On "mypy-level" required-ness: `purpose_of_use` / `consent_decision_ref` are declared KEYWORD-ONLY
with NO default, so mypy strict (`[tool.mypy] strict = true`, `packages = ["maezo"]`) rejects any
call site inside `src/maezo` that omits them — that enforcement is the type checker's, run by
`make type`. mypy does not scan `tests/`, so the same guarantee is ALSO asserted here structurally
via `inspect.signature`, which is what actually fails CI if a default appears.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import importlib
import inspect
import typing
from datetime import datetime
from enum import StrEnum
from typing import Any

import pytest

from maezo.ports.clinical_context import ClinicalContextPort, CodedSummary, SummaryPage
from maezo.ports.consent import ConsentDecisionSource
from maezo.ports.errors import (
    DEFAULT_PORT_TIMEOUT_SECONDS,
    PortFailure,
    PortFailureReason,
    PortResult,
)
from maezo.ports.outcomes import OutcomePublisherPort
from maezo.ports.population_features import (
    AggregateCell,
    AggregateResult,
    ConsentFilter,
    KAnonymityPolicy,
    PopulationFeaturePort,
)
from maezo.ports.work_items import WorkItemSource

# PER-SUBJECT protected reads: each names a subject, so each must ALSO name the consent decision
# that authorised reading that subject. `ConsentDecisionSource.latest_decision` is deliberately NOT
# here — it is the call that PRODUCES a consent decision reference, so requiring one would be
# circular; it has its own assertion below (purpose required, consent ref absent by design).
_CONSENT_ANCHORED_READS: tuple[tuple[type[Any], str], ...] = (
    (ClinicalContextPort, "get_subject_context"),
    (ClinicalContextPort, "list_subject_encounters"),
    (ClinicalContextPort, "list_subject_conditions"),
    (ClinicalContextPort, "get_subject_coverage"),
)

# POPULATION reads: purpose-bound like everything else, but subject-less, so there is no decision
# that could authorise them and no wire slot on the pinned contract to carry one. Requiring a
# `consent_decision_ref` here would be an invariant no adapter could honour — it would have to
# accept the argument and drop it, leaving the audit record asserting an authority nothing checked.
# Consent on these reads is the provider's filter, evidenced in the response (`ConsentFilter`).
_PURPOSE_BOUND_POPULATION_READS: tuple[tuple[type[Any], str], ...] = (
    (PopulationFeaturePort, "list_feature_sets"),
    (PopulationFeaturePort, "get_feature_set"),
    (PopulationFeaturePort, "get_feature_set_aggregates"),
)

# Every protected read, of either kind. Purpose-binding is universal across this set.
_PROTECTED_READS: tuple[tuple[type[Any], str], ...] = (
    *_CONSENT_ANCHORED_READS,
    *_PURPOSE_BOUND_POPULATION_READS,
)

# Every request/response method across the package: each must express an explicit deadline. A
# stream (`WorkItemSource.stream`, `ConsentDecisionSource.stream`) is deliberately excluded — a
# long-lived iteration has no single deadline; see the port docstrings.
_DEADLINE_BEARING: tuple[tuple[type[Any], str], ...] = (
    *_PROTECTED_READS,
    (WorkItemSource, "ack"),
    (WorkItemSource, "nack"),
    (ConsentDecisionSource, "ack"),
    (ConsentDecisionSource, "nack"),
    (ConsentDecisionSource, "latest_decision"),
    (OutcomePublisherPort, "publish"),
)

_ALL_PORTS: tuple[type[Any], ...] = (
    WorkItemSource,
    ConsentDecisionSource,
    ClinicalContextPort,
    PopulationFeaturePort,
    OutcomePublisherPort,
)


# --------------------------------------------------------------------------------------------
# Taxonomy determinism
# --------------------------------------------------------------------------------------------


def test_failure_reason_is_a_closed_strenum_with_the_exact_pinned_members() -> None:
    """The taxonomy is CLOSED: callers branch exhaustively on it, so a silent addition/removal is
    a contract change and must fail here."""
    assert issubclass(PortFailureReason, StrEnum)
    assert [member.name for member in PortFailureReason] == [
        "CONSENT_REQUIRED",
        "PURPOSE_DENIED",
        "SCOPE_NOT_SUPPORTED",
        "BELOW_COMMITTED_K",
        "INDIVIDUAL_DIMENSION",
        "INVALID_REQUEST",
        "NOT_FOUND",
        "NOT_AUTHENTICATED",
        "RATE_LIMITED",
        "TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "CONTRACT_VIOLATION",
    ]


def test_failure_reason_values_are_stable_deterministic_tokens() -> None:
    """The wire/audit form of every reason is a fixed snake_case token — an audit record written
    today must still be readable after a refactor that renames a member."""
    assert {member.name: member.value for member in PortFailureReason} == {
        "CONSENT_REQUIRED": "consent_required",
        "PURPOSE_DENIED": "purpose_denied",
        "SCOPE_NOT_SUPPORTED": "scope_not_supported",
        "BELOW_COMMITTED_K": "below_committed_k",
        "INDIVIDUAL_DIMENSION": "individual_dimension",
        "INVALID_REQUEST": "invalid_request",
        "NOT_FOUND": "not_found",
        "NOT_AUTHENTICATED": "not_authenticated",
        "RATE_LIMITED": "rate_limited",
        "TIMEOUT": "timeout",
        "UPSTREAM_UNAVAILABLE": "upstream_unavailable",
        "CONTRACT_VIOLATION": "contract_violation",
    }
    # StrEnum: the member IS its token, so `str(reason)` in a log line is deterministic.
    assert str(PortFailureReason.TIMEOUT) == "timeout"


def test_k_suppression_is_not_a_call_level_refusal_code() -> None:
    """REGRESSION PIN. An earlier draft carried a `SUPPRESSED_K_ANONYMITY` refusal, on the theory
    that a below-k population read returns nothing at all. The pinned contract says otherwise:
    below-k suppression is IN-BAND and PER CELL on a successful read (`AggregateCell.suppressed`,
    `metrics is None`), and the only k-shaped call-level refusal is the caller asking for a floor
    BELOW the committed minimum — a different condition, with a different cause and a different
    fix. Reinstating a whole-call suppression refusal would discard the reportable cells of a good
    response and erase the provider's own record of what was withheld.
    """
    names = {member.name for member in PortFailureReason}
    assert "SUPPRESSED_K_ANONYMITY" not in names
    assert "BELOW_COMMITTED_K" in names


def test_authentication_and_rate_limiting_are_not_conflated_with_consent_or_outage() -> None:
    """The conflations the taxonomy exists to prevent, pinned as distinctness. An adapter with no
    code for its own rejected credential would have to report `CONSENT_REQUIRED` (a consent denial
    that never happened, written into an LGPD audit trail) and one with no code for a rate limit
    would report `UPSTREAM_UNAVAILABLE` (inviting the retry storm the limit is defending against).
    """
    distinct = {
        PortFailureReason.NOT_AUTHENTICATED,
        PortFailureReason.CONSENT_REQUIRED,
        PortFailureReason.PURPOSE_DENIED,
        PortFailureReason.SCOPE_NOT_SUPPORTED,
        PortFailureReason.RATE_LIMITED,
        PortFailureReason.UPSTREAM_UNAVAILABLE,
    }
    assert len(distinct) == 6
    assert len({member.value for member in distinct}) == 6


def test_ports_package_declares_no_exception_type() -> None:
    """ "Success or refusal, never a raise": a policy-shaped failure that could be raised would be
    a failure a broad `except` can swallow. The package must define no exception at all."""
    for module_name in (
        "envelope",
        "errors",
        "work_items",
        "consent",
        "clinical_context",
        "population_features",
        "outcomes",
    ):
        module = importlib.import_module(f"maezo.ports.{module_name}")
        declared = [
            name
            for name, obj in vars(module).items()
            if inspect.isclass(obj) and issubclass(obj, BaseException) and obj.__module__ == module.__name__
        ]
        assert not declared, f"maezo.ports.{module_name} declares exception type(s): {declared}"


def test_no_port_method_declares_a_retry_or_fire_and_forget_knob() -> None:
    """Retry is CALLER policy and a publish must not be silenceable — so no port method may expose
    a retry/backoff/best-effort/fire-and-forget parameter."""
    banned = {"retries", "retry", "max_retries", "backoff", "best_effort", "fire_and_forget"}
    for port in _ALL_PORTS:
        for name, member in inspect.getmembers(port, callable):
            if name.startswith("_"):
                continue
            found = banned & set(inspect.signature(member).parameters)
            assert not found, f"{port.__name__}.{name} exposes forbidden knob(s): {sorted(found)}"


# --------------------------------------------------------------------------------------------
# PortResult / PortFailure shape
# --------------------------------------------------------------------------------------------


def test_port_result_ok_and_refused_are_mutually_exclusive_states() -> None:
    ok = PortResult.ok("value")
    assert ok.succeeded is True
    assert ok.value == "value"
    assert ok.failure is None

    refused = PortResult[str].refused(PortFailureReason.NOT_FOUND, detail="unknown handle")
    assert refused.succeeded is False
    assert refused.value is None
    assert refused.failure == PortFailure(reason=PortFailureReason.NOT_FOUND, detail="unknown handle")


def test_port_result_rejects_incoherent_construction() -> None:
    """The invariant is enforced at construction, so a caller may trust `succeeded is False` to
    imply `failure is not None` with no defensive checks. (A construction-time programming error
    is not a policy failure — this raise is not a refusal escaping a port.)"""
    with pytest.raises(ValueError, match="successful AND carry a failure"):
        PortResult[str](succeeded=True, failure=PortFailure(reason=PortFailureReason.TIMEOUT))
    with pytest.raises(ValueError, match="must carry a PortFailure"):
        PortResult[str](succeeded=False)
    with pytest.raises(ValueError, match="must not carry a value"):
        PortResult[str](
            succeeded=False,
            value="leaked",
            failure=PortFailure(reason=PortFailureReason.TIMEOUT),
        )


def test_port_result_and_failure_are_frozen_and_slotted() -> None:
    """Frozen: a refusal cannot be mutated into a success downstream of the port. Slotted: no
    ad-hoc attribute can smuggle an unaudited field across the boundary."""
    result = PortResult.ok(1)
    with pytest.raises(dataclasses.FrozenInstanceError, match="cannot assign to field"):
        result.succeeded = False  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
    assert not hasattr(PortFailure(reason=PortFailureReason.TIMEOUT), "__dict__")


def test_acknowledgement_shaped_results_carry_no_value() -> None:
    """`ack`/`nack` succeed with `None` — proving the ok-with-None case is legal and does not trip
    the coherence guard (it would, if the guard keyed on `value is None`)."""
    ack = PortResult[None].ok(None)
    assert ack.succeeded is True
    assert ack.value is None
    assert ack.failure is None


# --------------------------------------------------------------------------------------------
# Purpose / consent required-ness
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("port", "method_name"), _PROTECTED_READS)
def test_every_protected_read_is_purpose_bound_keyword_only(port: type[Any], method_name: str) -> None:
    """UNIVERSAL half of the invariant: no protected read of any kind may happen without the caller
    naming the LGPD purpose it is for. This still covers all seven reads — narrowing the consent
    half below does not narrow this one."""
    param = inspect.signature(getattr(port, method_name)).parameters.get("purpose_of_use")
    assert param is not None, f"{port.__name__}.{method_name} is missing `purpose_of_use`"
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{port.__name__}.{method_name}.purpose_of_use must be keyword-only "
        "(a positional purpose argument is a transposition hazard)"
    )
    assert param.default is inspect.Parameter.empty, (
        f"{port.__name__}.{method_name}.purpose_of_use must have NO default — a default would let "
        "a call site perform a protected read without declaring what it is for"
    )


@pytest.mark.parametrize(("port", "method_name"), _CONSENT_ANCHORED_READS)
def test_every_per_subject_read_names_its_authorising_decision(port: type[Any], method_name: str) -> None:
    """PER-SUBJECT half: a read that names a subject must name the decision that authorised reading
    THAT subject, so the audit record of the read is never anonymous about its authority."""
    param = inspect.signature(getattr(port, method_name)).parameters.get("consent_decision_ref")
    assert param is not None, f"{port.__name__}.{method_name} is missing `consent_decision_ref`"
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{port.__name__}.{method_name}.consent_decision_ref must be keyword-only "
        "(a positional consent argument is a transposition hazard)"
    )
    assert param.default is inspect.Parameter.empty, (
        f"{port.__name__}.{method_name}.consent_decision_ref must have NO default — a default would "
        "let a call site perform a protected read without declaring its authority"
    )


@pytest.mark.parametrize(("port", "method_name"), _PURPOSE_BOUND_POPULATION_READS)
def test_population_reads_declare_no_consent_decision_ref(port: type[Any], method_name: str) -> None:
    """The invariant is binding in BOTH directions, and this is the direction that was wrong.

    A `consent_decision_ref` on a subject-less population read is unenforceable: the pinned
    contract carries no such parameter on any of these operations, so an adapter can only accept
    the token and discard it — producing an audit trail that claims an authorisation nobody
    verified. That is worse than not asking, so asking is now forbidden here, not merely optional.
    """
    params = inspect.signature(getattr(port, method_name)).parameters
    assert "consent_decision_ref" not in params, (
        f"{port.__name__}.{method_name} declares `consent_decision_ref`, which the pinned "
        "population-features contract has no slot for — an unenforceable authority claim"
    )
    assert "portable_subject_ref" not in params, (
        f"{port.__name__}.{method_name} declares a subject reference — the population seam is "
        "aggregate-only and must have no per-subject entry point"
    )


def test_latest_decision_requires_purpose_and_deliberately_takes_no_consent_ref() -> None:
    """The consent point-read is the ONE protected call that cannot take a consent reference: it
    is the call that produces one. It still must be purpose-bound."""
    params = inspect.signature(ConsentDecisionSource.latest_decision).parameters
    assert params["purpose_of_use"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["purpose_of_use"].default is inspect.Parameter.empty
    assert "consent_decision_ref" not in params
    assert params["portable_subject_ref"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_nack_requires_an_explicit_closed_reason() -> None:
    """A quarantine routing decision must name a closed, opaque reason token — never default to
    one, and never accept free text as the reason."""
    for port in (WorkItemSource, ConsentDecisionSource):
        reason = inspect.signature(port.nack).parameters["reason"]
        assert reason.kind is inspect.Parameter.KEYWORD_ONLY
        assert reason.default is inspect.Parameter.empty
        assert reason.annotation in (PortFailureReason, "PortFailureReason")


# --------------------------------------------------------------------------------------------
# Driveability against the pinned contract
#
# A seam whose signature cannot express a conformant request, or whose return type cannot hold a
# conformant response, is not a boundary — it is a boundary-shaped object that no adapter can
# implement. These pin the specific request/response facts the ports were previously unable to
# carry.
# --------------------------------------------------------------------------------------------


def test_aggregates_read_requires_grouping_dimensions() -> None:
    """The pinned contract makes grouping REQUIRED and non-empty on the aggregates read. Without
    `group_by` on the signature there is no conformant call an adapter could construct."""
    params = inspect.signature(PopulationFeaturePort.get_feature_set_aggregates).parameters
    group_by = params.get("group_by")
    assert group_by is not None, "get_feature_set_aggregates cannot be driven without `group_by`"
    assert group_by.kind is inspect.Parameter.KEYWORD_ONLY
    assert group_by.default is inspect.Parameter.empty, (
        "`group_by` is required by the contract — a default would let a caller issue a request the "
        "provider must reject"
    )
    # `str` satisfies `Sequence[str]`, so a bare string would typecheck and then group by its
    # characters. The tuple spelling makes that mistake impossible to express.
    assert group_by.annotation in (tuple[str, ...], "tuple[str, ...]")


def test_aggregates_read_expresses_the_optional_window_and_k_floor() -> None:
    """The three optional parameters of the pinned aggregates operation. `min_cell_size` defaults
    to `None` (= the provider's committed floor) rather than to a hard-coded number: a floor
    duplicated into the payer core goes stale the day the contract commits to a higher one."""
    params = inspect.signature(PopulationFeaturePort.get_feature_set_aggregates).parameters
    for name in ("period_start", "period_end", "min_cell_size"):
        param = params.get(name)
        assert param is not None, f"get_feature_set_aggregates is missing `{name}`"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None, f"`{name}` must default to None (provider's default applies)"


def test_aggregates_read_declares_no_cohort_parameter() -> None:
    """REGRESSION PIN: `cohort_ref` was an invented required parameter with no counterpart anywhere
    in the pinned contract. A required parameter the provider does not accept makes every call
    unsatisfiable, and inventing one is how a port stops describing the system it fronts."""
    params = inspect.signature(PopulationFeaturePort.get_feature_set_aggregates).parameters
    assert "cohort_ref" not in params
    assert "cohort_id" not in params


def test_aggregate_result_can_represent_a_conformant_multi_cell_response() -> None:
    """The return type must hold what the provider actually sends. Because grouping is required,
    EVERY conformant response is multi-cell — a flat `Mapping[str, float]` is structurally unable
    to carry one, and a tuple of cell NAMES cannot represent cells keyed by a dimension mapping.
    """
    fields = {f.name for f in dataclasses.fields(AggregateResult)}
    assert fields == {"feature_set_ref", "purpose_of_use", "k_policy", "consent_filter", "cells"}
    assert {f.name for f in dataclasses.fields(AggregateCell)} == {"dimensions", "suppressed", "metrics"}
    assert {f.name for f in dataclasses.fields(KAnonymityPolicy)} == {
        "committed_minimum_cell_size",
        "applied_min_cell_size",
        "suppressed_cell_count",
    }
    assert {f.name for f in dataclasses.fields(ConsentFilter)} == {"applied", "consent_snapshot_at"}


def test_k_policy_keeps_the_committed_and_applied_minimums_apart() -> None:
    """Conflating the two hides whether the floor was RAISED for a given read — so a response
    computed at k=50 would be indistinguishable from one at the contractual minimum."""
    policy = KAnonymityPolicy(
        committed_minimum_cell_size=10, applied_min_cell_size=50, suppressed_cell_count=2
    )
    assert policy.applied_min_cell_size > policy.committed_minimum_cell_size


def test_consent_filter_carries_the_pinned_snapshot_instant() -> None:
    """ADR-0037's "snapshot pinado": consent is revocable, so without the snapshot instant an
    aggregate is an unfalsifiable claim about a moving population."""
    field_types = {f.name: f.type for f in dataclasses.fields(ConsentFilter)}
    assert field_types["consent_snapshot_at"] in (datetime, "datetime")


def test_suppressed_cells_are_representable_in_band_with_no_metrics() -> None:
    """`metrics is None` (withheld under k) and `metrics == {}` (measured, nothing there) are
    different states and must stay distinguishable — reading a suppressed cell as a measured zero
    is the specific misreading the type exists to prevent."""
    suppressed = AggregateCell(dimensions={"age_band": "90+"}, suppressed=True)
    measured = AggregateCell(dimensions={"age_band": "40-49"}, suppressed=False, metrics={})
    assert suppressed.metrics is None
    assert measured.metrics == {}
    assert suppressed.metrics != measured.metrics


@pytest.mark.parametrize("method_name", ["list_subject_encounters", "list_subject_conditions"])
def test_clinical_list_reads_accept_pagination_and_return_a_page(method_name: str) -> None:
    """The pinned contract pages both clinical list reads. A bare tuple return could not
    distinguish a complete history from a truncated first page — in an authorization path that is
    a wrong decision, not a slow one."""
    method = getattr(ClinicalContextPort, method_name)
    params = inspect.signature(method).parameters
    for name in ("limit", "page_token"):
        param = params.get(name)
        assert param is not None, f"{method_name} cannot express pagination: missing `{name}`"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
    hints = typing.get_type_hints(method)
    assert hints["return"] == PortResult[SummaryPage], (
        f"{method_name} must return a page carrying the continuation token, not a bare sequence"
    )


def test_summary_page_exposes_items_and_the_continuation_token() -> None:
    """`next_page_token is None` is the completeness signal; an under-full page is NOT."""
    assert {f.name for f in dataclasses.fields(SummaryPage)} == {"items", "next_page_token"}
    last = SummaryPage(items=())
    assert last.next_page_token is None
    more = SummaryPage(items=(CodedSummary(attributes={}),), next_page_token="opaque-token")
    assert more.next_page_token == "opaque-token"


@pytest.mark.parametrize("method_name", ["get_subject_context", "get_subject_coverage"])
def test_clinical_point_reads_stay_unpaged(method_name: str) -> None:
    """The pinned contract does NOT page these two. Adding pagination the provider does not offer
    would be the same class of invention as `cohort_ref` was, in the opposite direction."""
    params = inspect.signature(getattr(ClinicalContextPort, method_name)).parameters
    assert "limit" not in params
    assert "page_token" not in params


# --------------------------------------------------------------------------------------------
# Deterministic deadlines
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("port", "method_name"), _DEADLINE_BEARING)
def test_every_request_response_method_expresses_an_explicit_deadline(
    port: type[Any], method_name: str
) -> None:
    param = inspect.signature(getattr(port, method_name)).parameters["timeout_seconds"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == DEFAULT_PORT_TIMEOUT_SECONDS, (
        f"{port.__name__}.{method_name} must default to the ONE pinned deadline constant, not a "
        "per-seam magic number"
    )


def test_pinned_default_timeout_is_a_positive_finite_number() -> None:
    assert isinstance(DEFAULT_PORT_TIMEOUT_SECONDS, float)
    assert 0 < DEFAULT_PORT_TIMEOUT_SECONDS < 60


def test_streams_have_no_deadline_and_are_not_coroutine_functions() -> None:
    """`stream()` returns an async ITERATOR (`async for ...`), so it is a plain def. If it were
    `async def`, callers would have to `await` before iterating and the seam would silently change
    shape."""
    for port in (WorkItemSource, ConsentDecisionSource):
        assert not asyncio.iscoroutinefunction(port.stream)
        assert "timeout_seconds" not in inspect.signature(port.stream).parameters


def test_every_non_stream_port_method_is_a_coroutine_function() -> None:
    for port, method_name in _DEADLINE_BEARING:
        assert asyncio.iscoroutinefunction(getattr(port, method_name)), (
            f"{port.__name__}.{method_name} must be `async def`"
        )


# --------------------------------------------------------------------------------------------
# Seam style
# --------------------------------------------------------------------------------------------


def test_all_five_ports_are_runtime_checkable_protocols_not_abcs() -> None:
    """`typing.Protocol` (structural), never an ABC: an adapter, an in-memory fake and a replay
    harness satisfy a port by SHAPE, inheriting nothing from the payer core."""
    for port in _ALL_PORTS:
        assert getattr(port, "_is_protocol", False), f"{port.__name__} is not a Protocol"
        assert getattr(port, "_is_runtime_protocol", False), (
            f"{port.__name__} must be @runtime_checkable so fakes can be asserted structurally"
        )
        assert abc.ABC not in port.__bases__
