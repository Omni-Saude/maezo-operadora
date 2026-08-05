"""Behavioural proof that the five ports are IMPLEMENTABLE and that every closed reason code is
reachable — exercised through in-memory fakes (MZO-030).

Two things are being proven here, and neither is proven by the signature tests:

1. **The Protocols are satisfiable structurally.** Each fake inherits NOTHING from `maezo.ports`
   and is still accepted by `isinstance(fake, Port)`. That is the whole point of the
   `typing.Protocol` seam choice over an ABC: an adapter (MZO-050+), a replay harness and a test
   double are all first-class implementations, and none of them acquires a base class from the
   payer core.
2. **The taxonomy is not decorative.** `test_every_reason_code_is_reachable_through_a_port`
   asserts that the union of reasons actually produced by the fakes equals the FULL
   `PortFailureReason` enum. A reason code no port can ever return is dead vocabulary in an audit
   trail; a reason code a caller must handle but that this suite never produces is untested
   fail-closed behaviour.

The fakes also pin the behaviours the port docstrings promise but a signature cannot express:
ack/nack idempotency, quarantine routing carrying only the closed token, the empty-result vs
refusal distinction, page truncation being detectable, k-suppression arriving IN-BAND per cell on a
successful read, the k floor refusing to be lowered, and the publisher never reporting a success it
did not observe (DL-0038).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from maezo.ports.clinical_context import ClinicalContextPort, CodedSummary, SummaryPage
from maezo.ports.consent import (
    CanonicalConsentEvent,
    ConsentDecision,
    ConsentDecisionSource,
    ConsentDelivery,
)
from maezo.ports.envelope import SourcePosition
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailureReason, PortResult
from maezo.ports.outcomes import CanonicalOutcome, OutcomeAck, OutcomePublisherPort
from maezo.ports.population_features import (
    AggregateCell,
    AggregateResult,
    ConsentFilter,
    FeatureSetDefinition,
    FeatureSetDescriptor,
    KAnonymityPolicy,
    PopulationFeaturePort,
)
from maezo.ports.work_items import CanonicalWorkItem, WorkItemDelivery, WorkItemSource

_SUBJECT = "opaque-subject-ref-1"
_PURPOSE = "opaque-purpose-1"
_DECISION = "opaque-decision-ref-1"
_NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _envelope_kwargs(**overrides: Any) -> dict[str, Any]:
    """The 28 pinned envelope fields with opaque placeholder values. Every reference is a nonsense
    opaque token on purpose: the ports parse nothing, so the tests must not imply a format."""
    base: dict[str, Any] = {
        "event_id": "opaque-event-1",
        "event_type": "work_item.created",
        "canonical_schema_version": "1.0.0",
        "occurred_at": _NOW,
        "ingested_at": _NOW,
        "source_vendor": "opaque-vendor",
        "source_product": "opaque-product",
        "source_instance": "opaque-instance",
        "source_tenant": "opaque-source-tenant",
        "source_entity": "opaque-entity",
        "protected_source_record_ref": "opaque-record-ref",
        "source_position": SourcePosition(kind="opaque-kind", value="1"),
        "amh_tenant": "opaque-tenant",
        "legal_entity": "opaque-legal-entity",
        "portable_subject_ref": _SUBJECT,
        "correlation_id": "opaque-correlation",
        "causation_id": "opaque-causation",
        "idempotency_key": "opaque-idempotency",
        "consent_decision_ref": _DECISION,
        "purpose_of_use": _PURPOSE,
        "data_classification": "opaque-classification",
        "trace_id": "opaque-trace",
        "producer_version": "1.0.0",
        "contract_manifest_digest": "opaque-digest",
        "payload_hash": "opaque-hash",
        "replay_count": 0,
    }
    base.update(overrides)
    return base


def _work_item(**overrides: Any) -> CanonicalWorkItem:
    return CanonicalWorkItem(payload={"opaque": "payload"}, **_envelope_kwargs(**overrides))


def _consent_event(**overrides: Any) -> CanonicalConsentEvent:
    return CanonicalConsentEvent(payload={"opaque": "payload"}, **_envelope_kwargs(**overrides))


def _outcome(**overrides: Any) -> CanonicalOutcome:
    return CanonicalOutcome(payload={"opaque": "payload"}, **_envelope_kwargs(**overrides))


# --------------------------------------------------------------------------------------------
# Fakes — none of them inherits anything from maezo.ports
# --------------------------------------------------------------------------------------------


class _SettleLog:
    """Shared settlement bookkeeping for the two stream-shaped fakes."""

    def __init__(self, *, available: bool = True, slow: bool = False) -> None:
        self.acked: list[str] = []
        self.quarantined: list[tuple[str, PortFailureReason]] = []
        self.available = available
        self.slow = slow

    def ack(self, delivery_ref: str, timeout_seconds: float) -> PortResult[None]:
        if not self.available:
            return PortResult[None].refused(PortFailureReason.UPSTREAM_UNAVAILABLE)
        if self.slow and timeout_seconds < DEFAULT_PORT_TIMEOUT_SECONDS:
            return PortResult[None].refused(PortFailureReason.TIMEOUT)
        if delivery_ref not in self.acked:
            self.acked.append(delivery_ref)
        return PortResult[None].ok(None)

    def nack(self, delivery_ref: str, reason: PortFailureReason) -> PortResult[None]:
        if not self.available:
            return PortResult[None].refused(PortFailureReason.UPSTREAM_UNAVAILABLE)
        entry = (delivery_ref, reason)
        if entry not in self.quarantined:
            self.quarantined.append(entry)
        return PortResult[None].ok(None)


class FakeWorkItemSource:
    def __init__(self, deliveries: list[WorkItemDelivery], **kwargs: Any) -> None:
        self._deliveries = deliveries
        self.log = _SettleLog(**kwargs)

    def stream(self) -> AsyncIterator[WorkItemDelivery]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[WorkItemDelivery]:
        for delivery in self._deliveries:
            yield delivery

    async def ack(
        self, delivery_ref: str, *, timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS
    ) -> PortResult[None]:
        return self.log.ack(delivery_ref, timeout_seconds)

    async def nack(
        self,
        delivery_ref: str,
        *,
        reason: PortFailureReason,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        return self.log.nack(delivery_ref, reason)


class FakeConsentDecisionSource:
    def __init__(
        self,
        deliveries: list[ConsentDelivery] | None = None,
        decisions: Mapping[tuple[str, str], ConsentDecision] | None = None,
        *,
        known_subjects: frozenset[str] = frozenset({_SUBJECT}),
        **kwargs: Any,
    ) -> None:
        self._deliveries = deliveries or []
        self._decisions = dict(decisions or {})
        self._known_subjects = known_subjects
        self.log = _SettleLog(**kwargs)

    def stream(self) -> AsyncIterator[ConsentDelivery]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[ConsentDelivery]:
        for delivery in self._deliveries:
            yield delivery

    async def ack(
        self, delivery_ref: str, *, timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS
    ) -> PortResult[None]:
        return self.log.ack(delivery_ref, timeout_seconds)

    async def nack(
        self,
        delivery_ref: str,
        *,
        reason: PortFailureReason,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        return self.log.nack(delivery_ref, reason)

    async def latest_decision(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ConsentDecision]:
        if portable_subject_ref not in self._known_subjects:
            return PortResult[ConsentDecision].refused(PortFailureReason.NOT_FOUND)
        decision = self._decisions.get((portable_subject_ref, purpose_of_use))
        if decision is None:
            return PortResult[ConsentDecision].refused(PortFailureReason.PURPOSE_DENIED)
        return PortResult[ConsentDecision].ok(decision)


class FakeClinicalContextPort:
    """Authorises exactly one (subject, purpose, decision) triple; everything else is a refusal.

    Pages from a fixed synthetic history so truncation is observable: with `encounters=6` and
    `page_size=4`, the first page carries a continuation token and the second does not.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        authenticated: bool = True,
        rate_limited: bool = False,
        serves_coverage: bool = True,
        encounters: int = 0,
        page_size: int = 50,
    ) -> None:
        self._available = available
        self._authenticated = authenticated
        self._rate_limited = rate_limited
        self._serves_coverage = serves_coverage
        self._encounters = encounters
        self._page_size = page_size

    def _gate(
        self, subject: str, purpose: str, decision_ref: str, timeout_seconds: float
    ) -> PortFailureReason | None:
        if not self._available:
            return PortFailureReason.UPSTREAM_UNAVAILABLE
        if not self._authenticated:
            return PortFailureReason.NOT_AUTHENTICATED
        if self._rate_limited:
            return PortFailureReason.RATE_LIMITED
        if timeout_seconds < DEFAULT_PORT_TIMEOUT_SECONDS:
            return PortFailureReason.TIMEOUT
        if subject != _SUBJECT:
            return PortFailureReason.NOT_FOUND
        if decision_ref != _DECISION:
            return PortFailureReason.CONSENT_REQUIRED
        if purpose != _PURPOSE:
            return PortFailureReason.PURPOSE_DENIED
        return None

    def _page(self, total: int, limit: int | None, page_token: str | None) -> PortResult[SummaryPage]:
        size = self._page_size if limit is None else limit
        if size < 1:
            return PortResult[SummaryPage].refused(PortFailureReason.INVALID_REQUEST)
        offset = 0 if page_token is None else int(page_token)
        window = tuple(CodedSummary(attributes={"n": i}) for i in range(offset, min(offset + size, total)))
        nxt = offset + size
        return PortResult[SummaryPage].ok(
            SummaryPage(items=window, next_page_token=str(nxt) if nxt < total else None)
        )

    async def get_subject_context(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        refusal = self._gate(portable_subject_ref, purpose_of_use, consent_decision_ref, timeout_seconds)
        if refusal is not None:
            return PortResult[CodedSummary].refused(refusal)
        return PortResult[CodedSummary].ok(CodedSummary(attributes={"opaque": "summary"}))

    async def list_subject_encounters(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limit: int | None = None,
        page_token: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SummaryPage]:
        refusal = self._gate(portable_subject_ref, purpose_of_use, consent_decision_ref, timeout_seconds)
        if refusal is not None:
            return PortResult[SummaryPage].refused(refusal)
        return self._page(self._encounters, limit, page_token)

    async def list_subject_conditions(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limit: int | None = None,
        page_token: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SummaryPage]:
        refusal = self._gate(portable_subject_ref, purpose_of_use, consent_decision_ref, timeout_seconds)
        if refusal is not None:
            return PortResult[SummaryPage].refused(refusal)
        return self._page(0, limit, page_token)

    async def get_subject_coverage(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        refusal = self._gate(portable_subject_ref, purpose_of_use, consent_decision_ref, timeout_seconds)
        if refusal is not None:
            return PortResult[CodedSummary].refused(refusal)
        if not self._serves_coverage:
            # Subject and purpose are authorised; this provider simply does not publish coverage.
            return PortResult[CodedSummary].refused(PortFailureReason.SCOPE_NOT_SUPPORTED)
        return PortResult[CodedSummary].ok(CodedSummary(attributes={"opaque": "coverage"}))


class FakePopulationFeaturePort:
    """Aggregates over a synthetic two-cell population, one cell of which is below k.

    The committed floor is 10 and the fake applies whatever the caller asks for at or above it —
    which is how `applied_min_cell_size > committed_minimum_cell_size` becomes observable.
    """

    _FEATURE_SET = "opaque-feature-set-1"
    _COMMITTED_K = 10
    # dimension label -> that cell's population. The second is deliberately below the floor.
    _POPULATION: Mapping[str, int] = {"band-a": 128, "band-b": 3}
    _DIMENSIONS: frozenset[str] = frozenset({"opaque_dimension"})

    def __init__(self, *, consent_computable: bool = True) -> None:
        self._consent_computable = consent_computable

    async def list_feature_sets(
        self,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[tuple[FeatureSetDescriptor, ...]]:
        if purpose_of_use != _PURPOSE:
            return PortResult[tuple[FeatureSetDescriptor, ...]].refused(PortFailureReason.PURPOSE_DENIED)
        return PortResult[tuple[FeatureSetDescriptor, ...]].ok(
            (FeatureSetDescriptor(feature_set_ref=self._FEATURE_SET, feature_names=("f1", "f2")),)
        )

    async def get_feature_set(
        self,
        feature_set_ref: str,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[FeatureSetDefinition]:
        if feature_set_ref != self._FEATURE_SET:
            return PortResult[FeatureSetDefinition].refused(PortFailureReason.NOT_FOUND)
        return PortResult[FeatureSetDefinition].ok(
            FeatureSetDefinition(
                feature_set_ref=feature_set_ref,
                feature_names=("f1", "f2"),
                attributes={"opaque": "metadata"},
            )
        )

    async def get_feature_set_aggregates(
        self,
        feature_set_ref: str,
        *,
        group_by: tuple[str, ...],
        purpose_of_use: str,
        period_start: date | None = None,
        period_end: date | None = None,
        min_cell_size: int | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[AggregateResult]:
        if feature_set_ref != self._FEATURE_SET:
            return PortResult[AggregateResult].refused(PortFailureReason.NOT_FOUND)
        if not group_by:
            return PortResult[AggregateResult].refused(PortFailureReason.INVALID_REQUEST)
        if not set(group_by) <= self._DIMENSIONS:
            # Not in the published catalogue: individual-granularity dimensions never are.
            return PortResult[AggregateResult].refused(PortFailureReason.INDIVIDUAL_DIMENSION)
        if min_cell_size is not None and min_cell_size < self._COMMITTED_K:
            # Refused, never silently clamped — the floor is not lowerable by parameter.
            return PortResult[AggregateResult].refused(PortFailureReason.BELOW_COMMITTED_K)
        if not self._consent_computable:
            return PortResult[AggregateResult].refused(PortFailureReason.CONSENT_REQUIRED)

        applied = self._COMMITTED_K if min_cell_size is None else min_cell_size
        cells = tuple(
            AggregateCell(
                dimensions={group_by[0]: label},
                suppressed=count < applied,
                metrics=None if count < applied else {"opaque_metric": float(count)},
            )
            for label, count in self._POPULATION.items()
        )
        return PortResult[AggregateResult].ok(
            AggregateResult(
                feature_set_ref=feature_set_ref,
                purpose_of_use=purpose_of_use,
                k_policy=KAnonymityPolicy(
                    committed_minimum_cell_size=self._COMMITTED_K,
                    applied_min_cell_size=applied,
                    suppressed_cell_count=sum(1 for cell in cells if cell.suppressed),
                ),
                consent_filter=ConsentFilter(applied=True, consent_snapshot_at=_NOW),
                cells=cells,
            )
        )


class FakeOutcomePublisher:
    """Never reports a success it did not observe (DL-0038)."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.accepted: list[str] = []

    async def publish(
        self, outcome: CanonicalOutcome, *, timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS
    ) -> PortResult[OutcomeAck]:
        if not self._available:
            return PortResult[OutcomeAck].refused(PortFailureReason.UPSTREAM_UNAVAILABLE)
        if not outcome.contract_manifest_digest:
            return PortResult[OutcomeAck].refused(PortFailureReason.CONTRACT_VIOLATION)
        replay = outcome.idempotency_key in self.accepted
        if not replay:
            self.accepted.append(outcome.idempotency_key)
        return PortResult[OutcomeAck].ok(OutcomeAck(event_id=outcome.event_id, idempotent_replay=replay))


# --------------------------------------------------------------------------------------------
# Structural satisfaction
# --------------------------------------------------------------------------------------------


def test_every_fake_satisfies_its_port_structurally_without_inheriting_it() -> None:
    pairs: list[tuple[object, type[Any]]] = [
        (FakeWorkItemSource([]), WorkItemSource),
        (FakeConsentDecisionSource(), ConsentDecisionSource),
        (FakeClinicalContextPort(), ClinicalContextPort),
        (FakePopulationFeaturePort(), PopulationFeaturePort),
        (FakeOutcomePublisher(), OutcomePublisherPort),
    ]
    for fake, port in pairs:
        assert isinstance(fake, port), f"{type(fake).__name__} does not satisfy {port.__name__}"
        assert port not in type(fake).__mro__, (
            f"{type(fake).__name__} INHERITS {port.__name__} — the seam must be satisfied "
            "structurally, otherwise it is an ABC in disguise"
        )


# --------------------------------------------------------------------------------------------
# Work-item intake behaviour
# --------------------------------------------------------------------------------------------


async def test_work_item_stream_yields_the_pinned_envelope_and_an_opaque_payload() -> None:
    delivery = WorkItemDelivery(delivery_ref="opaque-delivery-1", work_item=_work_item())
    source = FakeWorkItemSource([delivery])
    seen = [d async for d in source.stream()]
    assert seen == [delivery]
    assert seen[0].work_item.portable_subject_ref == _SUBJECT
    assert seen[0].work_item.payload == {"opaque": "payload"}


async def test_ack_is_idempotent_per_delivery_handle() -> None:
    source = FakeWorkItemSource([])
    first = await source.ack("opaque-delivery-1")
    second = await source.ack("opaque-delivery-1")
    assert first.succeeded and second.succeeded
    assert source.log.acked == ["opaque-delivery-1"], "a repeated ack must not double-settle"


async def test_nack_routes_to_quarantine_carrying_only_the_closed_reason_token() -> None:
    source = FakeWorkItemSource([])
    result = await source.nack("opaque-delivery-1", reason=PortFailureReason.CONTRACT_VIOLATION)
    assert result.succeeded
    assert source.log.quarantined == [("opaque-delivery-1", PortFailureReason.CONTRACT_VIOLATION)]
    # No payload, no subject reference, no upstream error body reached quarantine.
    ref, reason = source.log.quarantined[0]
    assert isinstance(reason, PortFailureReason)
    assert ref == "opaque-delivery-1"
    await source.nack("opaque-delivery-1", reason=PortFailureReason.CONTRACT_VIOLATION)
    assert len(source.log.quarantined) == 1, "a repeated nack must not double-quarantine"


async def test_settlement_refuses_instead_of_reporting_an_unobserved_success() -> None:
    source = FakeWorkItemSource([], available=False)
    result = await source.ack("opaque-delivery-1")
    assert result.succeeded is False
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.UPSTREAM_UNAVAILABLE
    assert source.log.acked == []


async def test_a_breached_deadline_is_the_timeout_reason_not_an_exception() -> None:
    source = FakeWorkItemSource([], slow=True)
    result = await source.ack("opaque-delivery-1", timeout_seconds=0.001)
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.TIMEOUT


# --------------------------------------------------------------------------------------------
# Consent behaviour
# --------------------------------------------------------------------------------------------


async def test_latest_decision_returns_the_minimal_projection() -> None:
    decision = ConsentDecision(
        consent_decision_ref=_DECISION,
        portable_subject_ref=_SUBJECT,
        purpose_of_use=_PURPOSE,
        granted=True,
        consent_revision=2,
        decided_at=_NOW,
    )
    source = FakeConsentDecisionSource(decisions={(_SUBJECT, _PURPOSE): decision})
    result = await source.latest_decision(_SUBJECT, purpose_of_use=_PURPOSE)
    assert result.succeeded
    assert result.value == decision


async def test_a_revoked_decision_is_a_successful_read_not_a_refusal() -> None:
    """`granted=False` is a real decision the caller must act on; conflating it with a refusal
    would erase the difference between 'denied' and 'could not ask'."""
    revoked = ConsentDecision(
        consent_decision_ref=_DECISION,
        portable_subject_ref=_SUBJECT,
        purpose_of_use=_PURPOSE,
        granted=False,
        consent_revision=3,
        decided_at=_NOW,
    )
    source = FakeConsentDecisionSource(decisions={(_SUBJECT, _PURPOSE): revoked})
    result = await source.latest_decision(_SUBJECT, purpose_of_use=_PURPOSE)
    assert result.succeeded is True
    assert result.value is not None
    assert result.value.granted is False


async def test_unknown_subject_is_not_found_and_unknown_purpose_is_purpose_denied() -> None:
    source = FakeConsentDecisionSource()
    missing = await source.latest_decision("opaque-unknown-subject", purpose_of_use=_PURPOSE)
    assert missing.failure is not None
    assert missing.failure.reason is PortFailureReason.NOT_FOUND

    denied = await source.latest_decision(_SUBJECT, purpose_of_use="opaque-other-purpose")
    assert denied.failure is not None
    assert denied.failure.reason is PortFailureReason.PURPOSE_DENIED


async def test_consent_stream_carries_the_same_envelope_shape_as_work_items() -> None:
    delivery = ConsentDelivery(delivery_ref="opaque-delivery-2", consent_event=_consent_event())
    source = FakeConsentDecisionSource([delivery])
    seen = [d async for d in source.stream()]
    assert seen[0].consent_event.consent_decision_ref == _DECISION


# --------------------------------------------------------------------------------------------
# Clinical context behaviour (XRD-06)
# --------------------------------------------------------------------------------------------


async def test_clinical_reads_are_gated_on_consent_then_purpose() -> None:
    port = FakeClinicalContextPort()
    ok = await port.get_subject_context(_SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION)
    assert ok.succeeded
    assert ok.value == CodedSummary(attributes={"opaque": "summary"})

    no_consent = await port.get_subject_context(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref="opaque-other-decision"
    )
    assert no_consent.failure is not None
    assert no_consent.failure.reason is PortFailureReason.CONSENT_REQUIRED

    wrong_purpose = await port.get_subject_coverage(
        _SUBJECT, purpose_of_use="opaque-other-purpose", consent_decision_ref=_DECISION
    )
    assert wrong_purpose.failure is not None
    assert wrong_purpose.failure.reason is PortFailureReason.PURPOSE_DENIED


async def test_empty_list_read_is_a_success_not_a_refusal() -> None:
    port = FakeClinicalContextPort(encounters=0)
    result = await port.list_subject_conditions(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
    )
    assert result.succeeded is True
    assert result.value is not None
    assert result.value.items == ()
    assert result.value.next_page_token is None, "an empty COMPLETE read must not look truncated"
    assert result.failure is None


async def test_a_truncated_history_is_detectable_and_a_complete_one_is_not_mistaken_for_it() -> None:
    """The hazard the page type exists to remove: a subject with more encounters than the page size
    previously read as a COMPLETE short history, because the bare tuple carried no truncation
    signal at all. In an authorization path that is a wrong decision, not a slow one."""
    port = FakeClinicalContextPort(encounters=6, page_size=4)
    first = await port.list_subject_encounters(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
    )
    assert first.succeeded
    assert first.value is not None
    assert len(first.value.items) == 4
    assert first.value.next_page_token is not None, "a truncated page MUST announce its continuation"

    second = await port.list_subject_encounters(
        _SUBJECT,
        purpose_of_use=_PURPOSE,
        consent_decision_ref=_DECISION,
        page_token=first.value.next_page_token,
    )
    assert second.succeeded
    assert second.value is not None
    assert len(second.value.items) == 2
    assert second.value.next_page_token is None, "the LAST page carries no continuation token"

    # Reading the whole history is now expressible, and it is not what a single call returns.
    assert len(first.value.items) + len(second.value.items) == 6
    assert len(first.value.items) != 6


async def test_a_caller_supplied_page_size_is_honoured() -> None:
    port = FakeClinicalContextPort(encounters=6, page_size=50)
    result = await port.list_subject_encounters(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION, limit=2
    )
    assert result.value is not None
    assert len(result.value.items) == 2
    assert result.value.next_page_token is not None


async def test_an_out_of_range_page_size_is_refused_not_clamped() -> None:
    port = FakeClinicalContextPort(encounters=6)
    result = await port.list_subject_encounters(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION, limit=0
    )
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.INVALID_REQUEST


async def test_an_unserved_scope_is_not_reported_as_a_purpose_denial() -> None:
    """`SCOPE_NOT_SUPPORTED` exists so a provider that simply does not publish a section cannot be
    recorded as having denied an authorisation that was in fact valid."""
    port = FakeClinicalContextPort(serves_coverage=False)
    result = await port.get_subject_coverage(
        _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
    )
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.SCOPE_NOT_SUPPORTED


async def test_a_rejected_machine_credential_is_not_reported_as_a_consent_denial() -> None:
    """The conflation that would write a consent denial nobody ever decided into an LGPD audit
    trail."""
    port = FakeClinicalContextPort(authenticated=False)
    result = await port.get_subject_context(_SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION)
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.NOT_AUTHENTICATED
    assert result.failure.reason is not PortFailureReason.CONSENT_REQUIRED


async def test_a_rate_limit_is_distinguishable_from_an_outage() -> None:
    port = FakeClinicalContextPort(rate_limited=True)
    result = await port.get_subject_context(_SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION)
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.RATE_LIMITED


async def test_clinical_read_of_an_unknown_subject_is_not_found() -> None:
    port = FakeClinicalContextPort()
    result = await port.list_subject_encounters(
        "opaque-unknown-subject", purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
    )
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.NOT_FOUND


# --------------------------------------------------------------------------------------------
# Population features behaviour (XRD-07)
# --------------------------------------------------------------------------------------------


async def _aggregates(port: FakePopulationFeaturePort, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {"group_by": ("opaque_dimension",), "purpose_of_use": _PURPOSE}
    kwargs.update(overrides)
    return await port.get_feature_set_aggregates(FakePopulationFeaturePort._FEATURE_SET, **kwargs)


async def test_below_k_cells_are_suppressed_in_band_on_a_successful_read() -> None:
    """The correction at the heart of this seam. Suppression is PER CELL and IN-BAND: the below-k
    cell comes back, flagged, carrying no metrics, on a 200-shaped SUCCESS — alongside the cells
    that were reportable. Refusing the whole call (as an earlier draft did) would have thrown away
    a good aggregate and erased the provider's own record of what it withheld."""
    result = await _aggregates(FakePopulationFeaturePort())
    assert result.succeeded is True
    aggregate = result.value
    assert aggregate is not None

    assert len(aggregate.cells) == 2, "grouping is required, so a conformant response is multi-cell"
    reportable = [cell for cell in aggregate.cells if not cell.suppressed]
    withheld = [cell for cell in aggregate.cells if cell.suppressed]
    assert len(reportable) == 1
    assert len(withheld) == 1
    assert reportable[0].metrics == {"opaque_metric": 128.0}
    assert withheld[0].metrics is None, "a suppressed cell carries NO metrics"
    assert withheld[0].metrics != {}, "'withheld under k' must never read as 'measured zero'"
    assert aggregate.k_policy.suppressed_cell_count == 1

    # Egress shape: group labels and numbers only.
    assert not hasattr(aggregate, "portable_subject_ref")
    assert all(not hasattr(cell, "portable_subject_ref") for cell in aggregate.cells)


async def test_a_raised_k_floor_is_visible_and_the_committed_floor_is_still_reported() -> None:
    """Conflating applied and committed k would hide that this read was computed at a HIGHER floor
    than the contract's minimum — and would let k=50 be read as the contractual k."""
    result = await _aggregates(FakePopulationFeaturePort(), min_cell_size=200)
    assert result.succeeded
    assert result.value is not None
    policy = result.value.k_policy
    assert policy.committed_minimum_cell_size == 10
    assert policy.applied_min_cell_size == 200
    assert policy.applied_min_cell_size > policy.committed_minimum_cell_size
    # At k=200 both synthetic cells fall below the floor and are suppressed — still a SUCCESS.
    assert policy.suppressed_cell_count == 2
    assert all(cell.suppressed and cell.metrics is None for cell in result.value.cells)


async def test_the_k_floor_cannot_be_lowered_by_parameter() -> None:
    """Refused, not clamped: a caller must never be able to believe it received data at a k the
    boundary would not grant."""
    result = await _aggregates(FakePopulationFeaturePort(), min_cell_size=2)
    assert result.succeeded is False
    assert result.value is None
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.BELOW_COMMITTED_K


async def test_an_individual_granularity_grouping_is_refused() -> None:
    """The population seam returns aggregates or nothing — an attempt to de-aggregate is refused
    outright, not served at a smaller grain."""
    result = await _aggregates(FakePopulationFeaturePort(), group_by=("opaque_individual_dimension",))
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.INDIVIDUAL_DIMENSION


async def test_an_empty_grouping_is_an_invalid_request() -> None:
    """The pinned contract has no ungrouped mode (`minItems: 1`)."""
    result = await _aggregates(FakePopulationFeaturePort(), group_by=())
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.INVALID_REQUEST


async def test_the_aggregate_carries_the_consent_gate_evidence_and_its_snapshot_instant() -> None:
    """ADR-0037's "snapshot pinado" — the property the population path must preserve when it
    migrates onto this API. Without the instant, two aggregates taken either side of a revocation
    are indistinguishable."""
    result = await _aggregates(FakePopulationFeaturePort())
    assert result.value is not None
    assert result.value.consent_filter.applied is True
    assert result.value.consent_filter.consent_snapshot_at == _NOW
    assert result.value.purpose_of_use == _PURPOSE


async def test_uncomputable_consent_is_fail_closed_with_nothing_partial_returned() -> None:
    result = await _aggregates(FakePopulationFeaturePort(consent_computable=False))
    assert result.succeeded is False
    assert result.value is None, "fail-closed: no partial aggregate is emitted"
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.CONSENT_REQUIRED


async def test_population_catalogue_reads_are_purpose_bound() -> None:
    port = FakePopulationFeaturePort()
    ok = await port.list_feature_sets(purpose_of_use=_PURPOSE)
    assert ok.succeeded
    denied = await port.list_feature_sets(purpose_of_use="opaque-other-purpose")
    assert denied.failure is not None
    assert denied.failure.reason is PortFailureReason.PURPOSE_DENIED

    unknown = await port.get_feature_set("opaque-unknown-set", purpose_of_use=_PURPOSE)
    assert unknown.failure is not None
    assert unknown.failure.reason is PortFailureReason.NOT_FOUND


# --------------------------------------------------------------------------------------------
# Outcome publishing behaviour (DL-0038)
# --------------------------------------------------------------------------------------------


async def test_publisher_reports_first_acceptance_then_idempotent_replay() -> None:
    publisher = FakeOutcomePublisher()
    first = await publisher.publish(_outcome())
    assert first.succeeded
    assert first.value is not None
    assert first.value.idempotent_replay is False

    second = await publisher.publish(_outcome())
    assert second.succeeded
    assert second.value is not None
    assert second.value.idempotent_replay is True, "a redelivery must be reported, not hidden"
    assert publisher.accepted == ["opaque-idempotency"]


async def test_publisher_refuses_rather_than_fabricating_success() -> None:
    publisher = FakeOutcomePublisher(available=False)
    result = await publisher.publish(_outcome())
    assert result.succeeded is False
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.UPSTREAM_UNAVAILABLE
    assert publisher.accepted == []


async def test_publisher_reports_a_contract_violation_deterministically() -> None:
    publisher = FakeOutcomePublisher()
    result = await publisher.publish(_outcome(contract_manifest_digest=""))
    assert result.failure is not None
    assert result.failure.reason is PortFailureReason.CONTRACT_VIOLATION


def test_an_outcome_cannot_be_built_without_an_idempotency_key() -> None:
    kwargs = _envelope_kwargs()
    del kwargs["idempotency_key"]
    with pytest.raises(TypeError, match="idempotency_key"):
        CanonicalOutcome(payload={}, **kwargs)


# --------------------------------------------------------------------------------------------
# The taxonomy is exhaustively reachable
# --------------------------------------------------------------------------------------------


async def test_every_reason_code_is_reachable_through_a_port() -> None:
    """Non-vacuity for the taxonomy: a reason no port can produce is dead audit vocabulary."""
    observed: set[PortFailureReason] = set()

    async def record(result: PortResult[Any]) -> None:
        assert result.failure is not None
        observed.add(result.failure.reason)

    clinical = FakeClinicalContextPort()
    await record(
        await clinical.get_subject_context(
            _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref="opaque-other-decision"
        )
    )  # CONSENT_REQUIRED
    await record(
        await clinical.get_subject_context(
            _SUBJECT, purpose_of_use="opaque-other-purpose", consent_decision_ref=_DECISION
        )
    )  # PURPOSE_DENIED
    await record(
        await clinical.get_subject_context(
            "opaque-unknown-subject", purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
        )
    )  # NOT_FOUND
    await record(
        await clinical.get_subject_context(
            _SUBJECT,
            purpose_of_use=_PURPOSE,
            consent_decision_ref=_DECISION,
            timeout_seconds=0.001,
        )
    )  # TIMEOUT
    await record(
        await FakeClinicalContextPort(serves_coverage=False).get_subject_coverage(
            _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
        )
    )  # SCOPE_NOT_SUPPORTED
    await record(
        await FakeClinicalContextPort(authenticated=False).get_subject_context(
            _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
        )
    )  # NOT_AUTHENTICATED
    await record(
        await FakeClinicalContextPort(rate_limited=True).get_subject_context(
            _SUBJECT, purpose_of_use=_PURPOSE, consent_decision_ref=_DECISION
        )
    )  # RATE_LIMITED
    await record(await FakeOutcomePublisher(available=False).publish(_outcome()))  # UPSTREAM_UNAVAILABLE
    await record(
        await FakeOutcomePublisher().publish(_outcome(contract_manifest_digest=""))
    )  # CONTRACT_VIOLATION
    await record(await _aggregates(FakePopulationFeaturePort(), min_cell_size=2))  # BELOW_COMMITTED_K
    await record(
        await _aggregates(FakePopulationFeaturePort(), group_by=("opaque_individual_dimension",))
    )  # INDIVIDUAL_DIMENSION
    await record(await _aggregates(FakePopulationFeaturePort(), group_by=()))  # INVALID_REQUEST

    assert observed == set(PortFailureReason), (
        f"unreachable reason code(s): {sorted(set(PortFailureReason) - observed)}"
    )
