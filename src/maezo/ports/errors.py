"""ONE deterministic failure taxonomy shared by every ERP-neutral application port (MZO-030).

**Why a single taxonomy.** The five ports in this package (`WorkItemSource`,
`ConsentDecisionSource`, `ClinicalContextPort`, `PopulationFeaturePort`,
`OutcomePublisherPort`) are the payer core's ONLY sanctioned way to reach anything outside
itself (ADR-0037 XRD-06/XRD-07). If each seam invented its own failure vocabulary, a caller
would have to know WHICH infrastructure it is talking to in order to branch on a refusal —
re-coupling the core to the very infrastructure the ports exist to isolate. A closed
`PortFailureReason` StrEnum shared by all five keeps refusal handling ERP-neutral and
exhaustively checkable.

**Success or refusal, NEVER a raise (for policy-shaped failures).** This mirrors the house
posture already proven in `maezo.a2a.dispatcher` (`RejectionReason` + `DelegationResult`,
`dispatcher.py`): a delegation that is denied returns a structured rejection, it does not raise.
Same here — a consent gate, a purpose denial, a refused k floor or an upstream outage is DATA the
caller must route on (fail-closed, auditable), not a control-flow exception that a broad `except`
can swallow. Per-seam TRANSPORT exception hierarchies (the
`tools/workers/dmn_transport.py` `DmnEvaluationError`/`DmnNoResultError` pattern) remain
legitimate — but they belong to the ADAPTER that implements a port (MZO-050+), which MUST map
them onto `UPSTREAM_UNAVAILABLE`/`TIMEOUT`/`CONTRACT_VIOLATION` before returning. No adapter
exception may cross a port boundary. This module therefore defines NO exception type at all.

**Timeouts are expressed, never implicit.** Every request/response port method takes an explicit
`timeout_seconds` bounded by `DEFAULT_PORT_TIMEOUT_SECONDS`, and a breach is reported as
`PortFailureReason.TIMEOUT` — not as an `asyncio.TimeoutError` escaping the seam. Today's seams
(harness/dmn transports) have no timeout taxonomy at all; this is the deterministic one the
AMH-compat plan demands for the boundary.

**No retries inside a port.** Retry/backoff is CALLER policy (engine-computed retries for worker
paths, outbox redelivery for publish paths). A port that retried internally would make the
caller's fail-closed accounting unverifiable.

**No PHI, no raw source identifiers.** `PortFailure.detail` is an OPERATOR-facing, bounded,
non-PHI token (same posture as `DelegationResult.detail` and ADR-0037's immutable prohibition #5:
no PHI nor raw source id in keys, logs, traces, metrics or quarantine metadata). Adapters MUST
NOT copy payload content, patient-identifying data, or an upstream error body into it.

**Identity semantics are NOT defined here (DL-0040 open gate).** ADR-0037 XRD-05 is explicitly
"(Cláusula gated por DPO/Legal)" and DL-0040 records that the §5 downstream gates — DPO/Legal on
identity/consent values (MZO-020) — remain OPEN. Nothing in this package parses, validates,
prefixes or otherwise ascribes structure to any reference string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# Pinned default deadline for every request/response port call. A constant (not a magic number
# per seam) so the boundary's timeout posture is one auditable value; callers override per call
# when a modeled SLA demands it. Deliberately SHORT: a payer-core read blocking behind an
# unavailable upstream must surface as UPSTREAM_UNAVAILABLE/TIMEOUT fast enough for the caller's
# fail-closed path to run, never as an unbounded hang.
DEFAULT_PORT_TIMEOUT_SECONDS: Final[float] = 5.0


class PortFailureReason(StrEnum):
    """CLOSED set of refusal codes every port in this package may return. Closed on purpose: a
    caller can exhaustively branch on it, and a new infrastructure failure mode cannot silently
    widen the payer core's error surface without a deliberate change here.

    **Every member traces to a real condition of the pinned contract or to a genuine payer-side
    transport/policy state** — no speculative code, and no code that merely restates another. The
    grouping below is the caller's decision structure: an authorisation refusal is fail-closed and
    must be audited as such; a privacy-floor refusal means the CALLER asked for something the
    boundary will not do; a request-shape refusal is deterministic and unretryable; a provider-state
    refusal may be worth retrying under CALLER policy.

    Conflation is the failure mode this taxonomy exists to prevent. An adapter that reported an
    expired machine credential as `CONSENT_REQUIRED` would write a false consent-denial into the
    audit trail, and one that reported a rate limit as `UPSTREAM_UNAVAILABLE` would invite exactly
    the retry storm the limit is defending against. Hence the separate codes.
    """

    # -- Authorisation (fail-closed) ----------------------------------------------------------
    CONSENT_REQUIRED = "consent_required"
    """No consent decision authorises this read/write for the given subject + purpose. This is the
    port-side name for the provider's fail-closed consent denial; it also covers an adapter
    observing that the decision reference the caller asserted is NOT the one the provider says
    authorised the response."""

    PURPOSE_DENIED = "purpose_denied"
    """A consent decision exists but the declared `purpose_of_use` is not authorised by it.

    Spelled `purpose_not_permitted` on the wire. Three members of this enum (`SCOPE_NOT_SUPPORTED`,
    `BELOW_COMMITTED_K`, `INDIVIDUAL_DIMENSION`) happen to be identical to the provider's `reason`
    token, which makes a naive `PortFailureReason(reason)` pass-through look viable — it is not:
    this member and `CONSENT_REQUIRED` (wire `consent_denied`) do not round-trip. An adapter must
    map explicitly, in both directions."""

    SCOPE_NOT_SUPPORTED = "scope_not_supported"
    """The subject and purpose are authorised, but the provider does not serve the requested
    context scope. Distinct from `PURPOSE_DENIED` on purpose: recording "purpose denied" for a
    scope the provider simply does not publish would defame an authorisation that was in fact
    valid."""

    # -- Privacy floor (the caller asked for something the boundary will not do) ---------------
    BELOW_COMMITTED_K = "below_committed_k"
    """A population read asked for a minimum cell size BELOW the provider's committed k floor. The
    floor is never lowered by parameter — the request is refused, not silently clamped, so the
    caller cannot later believe it received data at the k it asked for. NOTE this is a refusal of
    the REQUEST; per-cell suppression of a below-k cell is IN-BAND on a successful read (see
    `maezo.ports.population_features.AggregateCell`), never a call-level refusal."""

    INDIVIDUAL_DIMENSION = "individual_dimension"
    """A population read asked to group by a dimension of individual granularity — i.e. asked the
    aggregate surface to de-aggregate. Refused outright; the population seam returns aggregates or
    nothing."""

    # -- Request shape / resolution (deterministic, unretryable) -------------------------------
    INVALID_REQUEST = "invalid_request"
    """The request did not satisfy the pinned parameter contract for reasons with no more specific
    code above (a malformed reference, an empty required list, an out-of-range page size). Retrying
    the identical request changes nothing — the CALLER must fix the request."""

    NOT_FOUND = "not_found"
    """The referenced subject/feature-set/decision does not exist (or is not visible here)."""

    # -- Provider / transport state ------------------------------------------------------------
    NOT_AUTHENTICATED = "not_authenticated"
    """The ADAPTER's own machine credential was absent, expired or rejected. Deliberately NOT
    folded into the consent codes: this says nothing whatsoever about the subject's consent, and
    reporting it as a consent denial would poison the LGPD audit trail with a decision that was
    never taken."""

    RATE_LIMITED = "rate_limited"
    """The provider refused the call for exceeding its request budget. Separate from
    `UPSTREAM_UNAVAILABLE` because the correct caller response is to slow down, not to retry."""

    TIMEOUT = "timeout"
    """The call exceeded its `timeout_seconds` deadline. NEVER an escaping asyncio timeout."""

    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    """The implementing infrastructure is unreachable/erroring. Transient; retry is CALLER policy."""

    CONTRACT_VIOLATION = "contract_violation"
    """The wire payload did not satisfy the pinned canonical contract (digest/shape/vocabulary).
    Deterministic: retrying identical bytes changes nothing — the caller must quarantine."""


@dataclass(frozen=True, slots=True)
class PortFailure:
    """A refusal carried BY a result object — never raised out of a port.

    `detail` is optional, operator-facing and non-PHI (see module docstring). It exists so an
    adapter can disambiguate two occurrences of the same closed reason for an operator, NOT so it
    can smuggle an upstream payload across the boundary.
    """

    reason: PortFailureReason
    detail: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PortResult[T]:
    """Success-or-refusal envelope returned by every port method (mirrors `DelegationResult`).

    Construct via `PortResult.ok(...)` / `PortResult.refused(...)` — the constructor enforces the
    invariant that a result is EXACTLY one of the two states, so a caller can trust
    `succeeded is False` to imply `failure is not None` without defensive checks.

    Deliberately NO `unwrap()`: a helper that raises on refusal would re-introduce exactly the
    control flow this taxonomy exists to remove.
    """

    succeeded: bool
    value: T | None = None
    failure: PortFailure | None = None

    def __post_init__(self) -> None:
        if self.succeeded and self.failure is not None:
            raise ValueError("PortResult cannot be successful AND carry a failure")
        if not self.succeeded and self.failure is None:
            raise ValueError("a refused PortResult must carry a PortFailure")
        if not self.succeeded and self.value is not None:
            raise ValueError("a refused PortResult must not carry a value")

    @classmethod
    def ok(cls, value: T) -> PortResult[T]:
        """Successful result. Pass `None` explicitly for the acknowledgement-shaped ports."""
        return cls(succeeded=True, value=value)

    @classmethod
    def refused(cls, reason: PortFailureReason, *, detail: str | None = None) -> PortResult[T]:
        """Structured refusal carrying a closed reason code. The port does NOT raise."""
        return cls(succeeded=False, failure=PortFailure(reason=reason, detail=detail))
