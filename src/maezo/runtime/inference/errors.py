"""Typed inference-provider errors (D2-02 split, step 1/8).

Moved verbatim out of ``maezo/runtime/inference.py`` (now the ``maezo.runtime.inference`` package
facade). No behaviour change: every class below is the SAME class object it always was — this is
a cut-and-paste relocation of the ``class`` statements' physical text, never a redefinition (see
``docs/reports/inference-split-plan.md`` §5 step 1, and its I-6 note on why identity must be
preserved: ``except PhiZoneRoutingError`` at the producer and every consumer must keep matching
the same class across the codebase).

Never fabricates a fallback (constraint 2/3, module docstring of the facade): every error here is
raised at a fail-closed decision point, never swallowed into a default.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Errors — typed, never a silent fallback (constraint 2 / constraint 3)
# ---------------------------------------------------------------------------


class InferenceConfigError(ValueError):
    """Raised when the inference provider configuration is invalid.

    Fail-closed: an unknown provider name, or a real provider missing
    required credentials, MUST raise here rather than silently falling
    back to a mock/noop provider. Raised at construction time (startup),
    not on first use.
    """


class InferenceProviderError(RuntimeError):
    """Raised when a configured provider fails to produce a completion.

    Wraps SDK-level errors (timeouts, rate limits, auth failures, refusals,
    etc.) with a provider-agnostic type so callers never need to import —
    or catch — a specific LLM SDK's exception classes (module docstring:
    no SDK leaks past this module).
    """

    def __init__(
        self, provider: str, message: str, *, retryable: bool = False, committed: bool = False
    ) -> None:
        self.provider = provider
        self.retryable = retryable
        #: True when the request reached the COMMITTED point — bytes were transmitted to the
        #: endpoint (the prompt is on the wire) before this failure surfaced. The W8 retry budget
        #: (leg 4) refuses to re-dial a committed, non-idempotent call EVEN WHEN ``retryable`` is
        #: True: re-sending PHI on a read-timeout is a data-exposure + double-spend hazard.
        #: ``retryable`` (the ``_DISPOSITIONS`` truth) and ``committed`` COMPOSE — a retry needs
        #: BOTH ``retryable and not committed``; neither notion is authoritative alone.
        self.committed = committed
        super().__init__(f"[{provider}] {message}")


class PhiZoneRoutingError(PermissionError):
    """Raised when a PHI-tagged inference request has no PHI-zone provider.

    Per ADR-0006 (duas zonas) / ADR-0017 (egress enforcement), PHI-tagged
    inference must NEVER silently fall back to the general-zone cloud
    provider. This mirrors the ``PermissionError`` subclass pattern used
    elsewhere in the codebase for structural, fail-closed denials (e.g.
    ADR-0016 ``ProcessKeyNotAllowedError``) — callers must route to a
    human / incident, never retry against a different provider.
    """


class BrEndpointNotApprovedError(PermissionError):
    """Raised when BR-resident inference would reach a NON-APPROVED endpoint.

    Deliberately a SIBLING of :class:`PhiZoneRoutingError`, not a subclass of it (and not
    caught by anything that catches it). The two refusals answer different questions and must
    stay separately observable:

    * ``PhiZoneRoutingError`` — "this PROVIDER may not see PHI" (invariant I-6, decided from
      ``phi_capable`` alone, at the facade, before any transport is involved).
    * this — "this provider is PHI-designated, but the ENDPOINT it is about to talk to is not
      on the BR-regional allowlist" (decided inside the adapter, client-side, against a URL).

    Folding the second into the first would make an endpoint escape read, in logs and in
    ``except`` clauses, as a provider-capability problem — and would let a future edit to I-6's
    raise silently change what happens when a vendor redirects PHI out of São Paulo.

    A ``PermissionError`` subclass for the same reason ``PhiZoneRoutingError`` is one: this is a
    structural, fail-closed denial that must reach a human / incident, never a retry.
    """


class BrRegionalTransportUnavailableError(InferenceProviderError):
    """The BR-regional transport could not be reached, or refuses to exist.

    An :class:`InferenceProviderError` subclass so it satisfies this module's contract that no
    SDK/transport-level error type leaks past it (module docstring), and so existing callers
    that already handle provider failure keep working unchanged.
    """

    def __init__(self, message: str, *, retryable: bool = False, committed: bool = False) -> None:
        super().__init__("br_resident", message, retryable=retryable, committed=committed)
