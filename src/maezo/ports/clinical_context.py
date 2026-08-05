"""`ClinicalContextPort` — the payer core's ONLY clinical read seam (ADR-0037 XRD-06, MZO-030).

XRD-06 verbatim: only the contract owner writes to the clinical record store; "o Maezo lê contexto
clínico exclusivamente via um `ClinicalContextPort` read-only sobre a API subject-context", and in
the hosted cell the payer receives NO clinical-store write credential. This module is that seam.

**READ-ONLY by construction.** There is no create/update/delete/patch method here and there is no
"escape hatch" method returning a client. A write path cannot be added by an adapter, only by
editing this Protocol — which is exactly the review checkpoint XRD-06 wants.

**Four operations, mirroring the pinned subject-context surface.** `get_subject_context`,
`list_subject_encounters`, `list_subject_conditions`, `get_subject_coverage`. No fifth speculative
operation and no generic `query(...)` — a generic query method would let a caller re-derive an
arbitrary clinical search and would make purpose-binding unauditable.

**The two LIST operations are paged; the two point reads are not.** That asymmetry is the pinned
contract's, not a preference: the published surface pages encounters and conditions (a page size
and an opaque continuation token in, a page carrying `next_page_token` out) and does not page the
context summary or the coverage summary. Mirroring it matters more than symmetry would. A list read
that returned a bare tuple could not distinguish a complete history from a truncated first page, so
a subject with sixty encounters would read as a subject with fifty — and in an authorization path,
a silently truncated history is a wrong decision, not a slow one.

**Every call is purpose-bound AND consent-anchored.** `purpose_of_use` and `consent_decision_ref`
are REQUIRED keyword-only arguments on all four operations: mypy rejects any call site inside
`src/maezo` that omits either, and `tests/unit/ports/test_ports_contract.py` proves structurally
that neither has a default. The `consent_decision_ref` is the token obtained from
`maezo.ports.consent.ConsentDecisionSource.latest_decision`.

**Be precise about what `consent_decision_ref` is and is not.** It is a PAYER-SIDE AUDIT INVARIANT,
not a wire parameter the provider validates. The pinned contract transmits no decision reference on
the request — the provider evaluates consent itself, fail-closed, from the subject and the purpose.
What the port buys by requiring the argument is twofold, and both are payer-side: no call site in
`src/maezo` can perform a protected clinical read without having first obtained a decision and
named it in the audit record of that read; and because the contract's context response RETURNS the
`consent_decision_ref` that authorised it, an adapter can COMPARE the two and refuse with
`CONSENT_REQUIRED` when the decision the caller asserted is not the decision the provider acted on.
The port does not perform that comparison — it has no parsing and no wire access — but it is the
reason the argument is on the seam rather than in a logging helper.

**No FHIR anywhere.** No FHIR resource type, no `hapi`/`fhirclient` import (the architecture fence
forbids the import root outright), no resource-id parsing, no `Patient/…` reference vocabulary.
The port speaks subject references and opaque coded summaries; how those map onto a clinical
record standard is entirely the adapter's problem (MZO-050+, gated).

**Subject references are opaque (DL-0040 open gate).** `portable_subject_ref` is a `str` with NO
format defined, validated or assumed here. Identity semantics are MZO-020's DPO/Legal-gated scope
and DL-0040 records that gate as still OPEN.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True, kw_only=True)
class CodedSummary:
    """An OPAQUE coded summary returned by a clinical read.

    `attributes` is deliberately unstructured (`Mapping[str, Any]`): the payer core has no
    sanctioned reason to depend on a clinical record model, and declaring one here would import
    the very domain ADR-0037 isolates. The port neither parses nor validates it — a payload that
    violates the pinned contract is a `CONTRACT_VIOLATION` the ADAPTER reports.

    One value type serves all four operations on purpose: four near-identical wrappers would be
    speculative generalisation, and the operation the caller invoked already says what it holds.
    """

    attributes: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryPage:
    """ONE page of a paged clinical list read, plus the token that continues it.

    - `items` — this page's summaries, in provider order. An empty tuple is a real page.
    - `next_page_token` — `None` on the LAST page, a token otherwise. The token is OPAQUE: the port
      defines no format for it, never parses it, and the caller's only legal use is to hand it back
      unmodified on the following call.

    **`next_page_token is None` is the completeness signal, and it is the whole point of this
    type.** Without it, a caller has no way to tell "these are all the encounters" from "these are
    the first fifty of sixty", and the two readings support opposite authorization decisions. A
    caller that ignores the token has silently chosen the truncated history; a caller that loops
    until it is `None` has read the whole record. The distinction is now expressible, so it is now
    the caller's to get right — which it was not before.

    One page type serves both list operations, for the same reason one `CodedSummary` serves all
    four reads: the operation the caller invoked already says what the page holds.
    """

    items: tuple[CodedSummary, ...]
    next_page_token: str | None = None


@runtime_checkable
class ClinicalContextPort(Protocol):
    """Read-only clinical context seam. Structural (`typing.Protocol`), never an ABC.

    Every method returns `PortResult` — a consent gate, a purpose denial, an unknown subject, a
    deadline breach or an upstream outage is a structured refusal carried on the result, never an
    exception escaping the port (`maezo.ports.errors`).
    """

    async def get_subject_context(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        """The subject's coded clinical context summary. NOT paged — the pinned contract returns
        this as a single summary.

        Refusals: `CONSENT_REQUIRED` (no consent authorises this subject for this read, including
        the case where the provider names a different authorising decision than the caller
        asserted), `PURPOSE_DENIED` (consent exists but not for this purpose), `SCOPE_NOT_SUPPORTED`
        (subject and purpose are authorised but the provider does not serve this context scope),
        `NOT_FOUND` (unknown subject), `INVALID_REQUEST`, `NOT_AUTHENTICATED`, `RATE_LIMITED`,
        `TIMEOUT`, `UPSTREAM_UNAVAILABLE`, `CONTRACT_VIOLATION`.
        """
        ...

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
        """One PAGE of the subject's coded encounter summaries.

        `limit` is the maximum page size; `None` means "the provider's default". The default is
        deliberately not restated here — a page size duplicated into the payer core would go stale
        the day the contract changes it, and a caller that needs a specific size can say so. A
        `limit` outside the provider's permitted range is `INVALID_REQUEST`, never silently clamped.

        `page_token` continues a previous page: pass back the `next_page_token` the previous
        `SummaryPage` carried, unmodified. `None` starts at the first page.

        A page with an empty `items` is a SUCCESSFUL read of zero encounters — materially different
        from a `NOT_FOUND`/`CONSENT_REQUIRED` refusal, and that distinction is why this returns a
        result object rather than a bare sequence. `next_page_token is None` — NOT an
        under-full page — is what tells the caller the history is complete; see `SummaryPage`.

        Same refusal taxonomy as `get_subject_context`.
        """
        ...

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
        """One PAGE of the subject's coded condition summaries. Same pagination, empty-vs-refused
        and completeness semantics as `list_subject_encounters`."""
        ...

    async def get_subject_coverage(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        """The subject's coded coverage summary. NOT paged — the pinned contract returns this as a
        single summary. Same refusal taxonomy as `get_subject_context`."""
        ...
