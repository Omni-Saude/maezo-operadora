"""`PopulationFeaturePort` — purpose-bound, consent-filtered, k-suppressed population reads.

ADR-0037 XRD-07 (MZO-030): population/actuarial access goes through the population-features API
"(purpose-bound, consent-filtered, k-suprimida) implementando o `PopulationFeaturePort`; sem acesso
direto Athena/Gold e sem credencial Gold irrestrita". This module is the TARGET SEAM for that
clause.

**The shapes here mirror the PINNED published contract**, whose digest this repo holds in
`config/integrations/amh/contracts.lock.json` (artefact
`schemas/openapi/maezo/v1/population-features.openapi.yaml`). The contract itself is owned and
edited EXCLUSIVELY upstream — ADR-0037 immutable prohibition #4 forbids an editable copy living
here — so what this module carries is the payer-side TYPE of the same information, not the schema.
Where a name differs it is because this package's own idiom won (`feature_set_ref` for the opaque
handle the contract calls `feature_set_id`); the structure does not differ, because a structure
that could not represent a conformant response would make the seam undriveable.

**Relationship to André's existing seam (ADR-0019, `agents/andre/graph.py`).** André's graph
already depends structurally on a `PopulationFeatureClient` Protocol returning a k-anon aggregate —
ADR-0037's own note is that when the population-features API is published the population path
migrates from the ADR-0019 gold-view mechanism to the API "mesma semântica: consent-gate,
k-supressão, snapshot pinado". This port is that destination, and it preserves each of those three:
the consent gate is `ConsentFilter.applied`, the k-suppression is `KAnonymityPolicy` plus the
per-cell `suppressed` flag, and the "snapshot pinado" is `ConsentFilter.consent_snapshot_at` — the
exact instant of the consent snapshot the aggregate was computed over, without which two aggregates
taken either side of a revocation are indistinguishable.

**This work package does NOT touch André's module.** Wiring an adapter that satisfies both this
port and `PopulationFeatureClient` is MZO-050+/MZO-080 scope and remains gated. Two consequences
are deliberate here:

- The port declares its OWN value types rather than importing André's. It must: `maezo.ports` is
  leaf-domain (stdlib + typing only — see `tests/unit/ports/test_ports_purity.py`, which forbids
  importing `maezo.agents` at all), and a port that imported an agent's module would invert the
  dependency the ports exist to establish.
- The port does NOT carry André's `has_resolvable_phi()` structural probe. That helper hard-codes
  clinical-record reference vocabulary (`Patient/`, `fhir:`) — legitimate defence-in-depth inside
  an agent, but encoding a FHIR reference grammar in the payer core's ERP-neutral port would be
  precisely the coupling ADR-0037 prohibition #2 forbids. Be honest about what that costs: the
  types below make an individual-granularity value STRUCTURALLY unnatural (`AggregateCell` is
  keyed by group labels and has no subject slot) and the provider rejects individual dimensions at
  source (`INDIVIDUAL_DIMENSION`), but nothing in this package INSPECTS a `dimensions` value, so
  the residual egress check André's probe performed is not performed anywhere right now.
  Re-establishing it adapter-side — where the reference grammar legitimately lives — is MZO-080's
  obligation, and it is not discharged by this module.

**Three operations, mirroring the pinned population-features surface.** `list_feature_sets`,
`get_feature_set`, `get_feature_set_aggregates`. No raw-query method, ever: a generic query would
re-open the direct-lake access XRD-07 closes.

**Purpose-bound, but NOT consent-ref-anchored — and the asymmetry is deliberate.**
`purpose_of_use` is REQUIRED and keyword-only on all three operations. `consent_decision_ref` is
declared on NONE of them, unlike the per-subject reads in `maezo.ports.clinical_context`. A
consent decision reference names the decision taken for ONE subject; a population read has no
subject, so there is no decision that could authorise it and no wire slot on the pinned contract to
carry one. Requiring it here would have been an invariant no implementation could honour — an
adapter would have had to accept the argument and drop it, which is worse than not asking, because
the audit record would then assert an authority that was never checked. Consent is still enforced,
one layer down and by the provider: every aggregate is computed exclusively over subjects with
valid consent for the declared purpose, and the response says so in `ConsentFilter`. On the two
CATALOGUE reads `purpose_of_use` is not transmitted on the wire at all (the pinned contract
declares no parameters on the feature-set listing and only the identifier on the single read) — it
is kept as a PAYER-SIDE LGPD purpose-discipline invariant, so that no code path in the payer core
can read the population catalogue without having named why.

**References are opaque.** `feature_set_ref` is a `str` with NO format defined here — DL-0040/
DL-0042, same posture as every other reference in this package: ADR-0037 XRD-05 gated identity/
reference semantics on DPO/Legal, DL-0040 opened that gate and DL-0042 discharged it on
2026-08-05 — discharged on opaque refs, not on a prefix vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureSetDescriptor:
    """Catalogue entry for one published feature set: an opaque handle plus the feature names it
    exposes. Names only — a descriptor carries no values and therefore no aggregate and no PHI."""

    feature_set_ref: str
    feature_names: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureSetDefinition:
    """A single feature set's definition: its handle, its feature names, and an OPAQUE attribute
    mapping for whatever else the provider publishes about it (dimension catalogue, metric kinds,
    data classification, …).

    `attributes` is unstructured on purpose — modelling the provider's catalogue metadata here
    would be speculative generalisation and would re-declare a schema this repo may not hold
    editable copies of (ADR-0037 immutable prohibition #4).
    """

    feature_set_ref: str
    feature_names: tuple[str, ...]
    attributes: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class KAnonymityPolicy:
    """The k-anonymity policy the provider actually applied to one aggregate response.

    TWO minimums, never one. `committed_minimum_cell_size` is the floor the contract commits to and
    that no parameter can lower; `applied_min_cell_size` is the k this particular response was
    computed at, which is `>=` the committed floor because a caller may only RAISE it. Collapsing
    them into a single `k` would destroy exactly the fact an analyst needs — whether the floor was
    raised for this read — and would let a response computed at k=50 be read as the contractual
    minimum.

    `suppressed_cell_count` makes the suppression countable without walking the cells, so a caller
    can record "n cells withheld" in an audit line, and so a response whose cells were dropped in
    transit does not read as a response with nothing to suppress.
    """

    committed_minimum_cell_size: int
    applied_min_cell_size: int
    suppressed_cell_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsentFilter:
    """Evidence that the consent gate ran, and the instant it ran against.

    `applied` is always true on a conformant response — the provider does not emit an unfiltered
    aggregate — but it is carried explicitly rather than assumed, because an invariant that is
    never transmitted is an invariant no consumer can audit.

    `consent_snapshot_at` is the "snapshot pinado" of ADR-0037's population-migration note. Consent
    is revocable and therefore time-varying: without the snapshot instant, an aggregate is an
    unfalsifiable claim about a moving population, and two reads taken either side of a revocation
    are indistinguishable. With it, a caller can say precisely which consent state a number rests
    on.
    """

    applied: bool
    consent_snapshot_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateCell:
    """ONE grouped cell of an aggregate result — the unit k-suppression acts on.

    - `dimensions` — the group labels that identify this cell (e.g. an age band and a
      municipality), keyed by dimension name. Aggregate labels only; there is no subject slot and
      no individual-granularity slot on this type. The port neither parses nor validates the values
      (see the module docstring on what that leaves to MZO-080).
    - `suppressed` — true when the cell's count fell below the applied k.
    - `metrics` — `None` exactly when `suppressed` is true.

    **Suppression is IN-BAND and per cell, not a refusal of the call.** A below-k cell is RETURNED,
    flagged, and carries no metrics — that is what makes the suppression explicit and countable. A
    response is therefore normally a MIX of reportable and suppressed cells, and modelling
    suppression as a call-level refusal (as an earlier draft of this port did) was wrong twice
    over: it discarded the reportable cells of a perfectly good response, and it erased the
    provider's own record of what was withheld.

    `metrics is None` and `metrics == {}` are different states and must stay different: the first
    is "withheld under k", the second is "measured, and there is nothing here". Reading a
    suppressed cell as a measured zero is the specific misreading this type exists to prevent.
    """

    dimensions: Mapping[str, str]
    suppressed: bool
    metrics: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateResult:
    """The ONLY shape a population aggregate read may return — grouped, k-suppressed cells plus the
    evidence of the two gates that produced them.

    STRUCTURAL EGRESS GATE (the property ADR-0006/0019 bought and this port keeps): no subject
    reference, no individual row, no resolvable identifier field anywhere in this type or its
    parts. What crosses the boundary is group labels and numbers.

    Because grouping dimensions are REQUIRED on the read, every conformant response is
    multi-cell — a single flat metric mapping could not carry one, which is why the cells are a
    sequence of `AggregateCell` rather than a `Mapping[str, float]`.

    `purpose_of_use` is echoed back so an aggregate cannot be filed against a purpose other than
    the one whose consent filter produced it.
    """

    feature_set_ref: str
    purpose_of_use: str
    k_policy: KAnonymityPolicy
    consent_filter: ConsentFilter
    cells: tuple[AggregateCell, ...]


@runtime_checkable
class PopulationFeaturePort(Protocol):
    """Population/actuarial read seam. Structural (`typing.Protocol`), never an ABC."""

    async def list_feature_sets(
        self,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[tuple[FeatureSetDescriptor, ...]]:
        """The published feature-set catalogue. An empty tuple is a successful read of zero feature
        sets, not a refusal.

        `purpose_of_use` is a PAYER-SIDE invariant on this operation, NOT a wire parameter: the
        pinned contract declares no parameters on the catalogue listing at all. It is required here
        so that purpose discipline has no per-method exception a caller could learn to route
        around — see the module docstring.

        Refusals: `NOT_AUTHENTICATED`, `RATE_LIMITED`, `TIMEOUT`, `UPSTREAM_UNAVAILABLE`,
        `CONTRACT_VIOLATION`.
        """
        ...

    async def get_feature_set(
        self,
        feature_set_ref: str,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[FeatureSetDefinition]:
        """One feature set's definition. `NOT_FOUND` when the handle is unknown.

        `purpose_of_use` is a payer-side invariant here too — the pinned contract declares only the
        feature-set identifier on this operation.

        Refusals: `NOT_FOUND`, `INVALID_REQUEST`, `NOT_AUTHENTICATED`, `RATE_LIMITED`, `TIMEOUT`,
        `UPSTREAM_UNAVAILABLE`, `CONTRACT_VIOLATION`.
        """
        ...

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
        """Purpose-bound, consent-filtered, k-suppressed aggregates of a feature set.

        `group_by` is REQUIRED and must be NON-EMPTY: the pinned contract has no ungrouped mode,
        and an aggregate with no grouping dimension is a single whole-population number this seam
        does not serve. An empty tuple is `INVALID_REQUEST`. It is typed `tuple[str, ...]` rather
        than `Sequence[str]` deliberately — `str` itself satisfies `Sequence[str]`, so a caller who
        passed one dimension as a bare string would typecheck and then group by its characters.

        `period_start` / `period_end` bound the observation window; `None` leaves the window to the
        provider's default.

        `min_cell_size` may only RAISE the suppression floor. `None` means "apply the provider's
        committed minimum" — the floor is deliberately NOT hard-coded here, because a number
        duplicated into the payer core would silently go stale the day the contract commits to a
        higher one. A value below the committed floor is refused with `BELOW_COMMITTED_K`: the
        request fails rather than being silently clamped, so a caller can never believe it received
        data at a k the boundary would not grant.

        **Below-k cells come back SUPPRESSED, on a SUCCESSFUL read** (`AggregateCell.suppressed`,
        with `metrics is None`) — suppression is in-band and explicit, never a refusal of the whole
        call. `KAnonymityPolicy.suppressed_cell_count` says how many.

        Refusals: `BELOW_COMMITTED_K` (the floor may not be lowered), `INDIVIDUAL_DIMENSION` (a
        requested grouping dimension is of individual granularity), `CONSENT_REQUIRED` /
        `PURPOSE_DENIED` (no computable consent for this purpose — fail-closed, nothing partial is
        returned), `INVALID_REQUEST`, `NOT_FOUND`, `NOT_AUTHENTICATED`, `RATE_LIMITED`, `TIMEOUT`,
        `UPSTREAM_UNAVAILABLE`, `CONTRACT_VIOLATION`.
        """
        ...
