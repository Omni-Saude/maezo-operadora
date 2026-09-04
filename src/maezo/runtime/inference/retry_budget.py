"""Budgeted, idempotency-aware retry (D2-02 split, step 4/8).

Moved verbatim out of ``maezo/runtime/inference.py`` (W8, Onda 2 W2 leg 4) — cut-and-paste, never
a redefinition (``docs/reports/inference-split-plan.md`` §5 step 4). Independent of the BR-regional
transport module (step 5); consumed by :class:`BrResidentInferenceProvider` (step 7).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from maezo.runtime.inference.errors import InferenceProviderError

# ---------------------------------------------------------------------------
# W8 — budgeted, idempotency-aware retry (Onda 2 W2 leg 4)
#
# Two independent bounds and one safety veto, all keyed on the SINGLE disposition truth
# (`InferenceProviderError.retryable`, pinned by the leg-3 `_DISPOSITIONS` table) — this leg
# authors NO second notion of "retryable". A retry happens IFF:
#
#     exc.retryable            # the _DISPOSITIONS truth: ONLY an OUTAGE is retryable
#   AND NOT exc.committed      # the W8 idempotency veto: never re-send bytes-on-wire PHI
#   AND attempt < max_attempts # bound 1: the attempt budget
#   AND a retry token is free  # bound 2: a rate/token budget — a retry storm must not itself
#                              #          become a rate-limit incident
#
# `retryable` and `committed` COMPOSE (logical AND); they do not REPLACE one another. The
# agreement test pins the decision as derivable from the live exceptions alone, so the two notions
# can never silently drift apart (the gate-agreement lesson from Train C leg 3).
#
# DEFAULT = NO RETRY (`max_attempts=1`). A PHI path does not silently re-dial: auto-retrying PHI is
# itself a hazard (re-exposure, double-spend, retry storms), so retry is an OWNER-configured,
# budgeted opt-in — the same "inert until an owner acts" posture as the DPA / endpoint / credential
# gates. Exhausting the budget RAISES the last terminal exception (never an infinite loop), which
# the SP-OP-ESCALATION human-routing seam (`helena/graph._classify_llm`'s bare-except) turns into
# `falha_tecnica` -> a human task.
# ---------------------------------------------------------------------------

#: Retry-stop reason codes. Enum-ish, CONTENT-FREE strings — safe to log and to carry into an
#: escalation reason; never a prompt, never a completion, never a credential.
RETRY_STOP_NOT_RETRYABLE: Final[str] = "not_retryable"
RETRY_STOP_COMMITTED: Final[str] = "committed_bytes_sent"
RETRY_STOP_ATTEMPTS_EXHAUSTED: Final[str] = "attempts_exhausted"
RETRY_STOP_RATE_BUDGET_EXHAUSTED: Final[str] = "rate_budget_exhausted"


@dataclass(frozen=True, slots=True)
class RetryBudget:
    """Immutable retry POLICY for :class:`BrResidentInferenceProvider` (W8, leg 4).

    Pure numbers, no state: the mutable token-bucket state lives in :class:`_RetryTokenBucket` on
    the provider instance so a retry storm is bounded ACROSS calls, not merely within one.

    The default is a NO-RETRY budget (``max_attempts=1``): see the module comment above for why a
    PHI path must not silently re-dial. A caller that wants retries constructs this explicitly and
    injects it (and, in tests, an injected ``sleep``/``now`` so backoff is deterministic).
    """

    #: Total attempts INCLUDING the first. ``1`` == no retry. Provenance for the default: the
    #: safety argument above, not a tuned number — retries are opt-in for the PHI zone.
    max_attempts: int = 1

    #: Exponential backoff base (seconds); the delay after the Nth failure is
    #: ``base_backoff_s * 2**(N-1)``, capped at ``max_backoff_s``.
    base_backoff_s: float = 0.5
    max_backoff_s: float = 30.0

    #: Deterministic jitter as a fraction of the computed delay (0..1). ``0`` disables jitter (an
    #: exact, hardcodable schedule). Non-zero adds ``[0, jitter_ratio*delay)`` derived from
    #: ``jitter_seed`` via SHA-256 — NO RNG, so a test pins it by seed, never by luck.
    jitter_ratio: float = 0.0
    jitter_seed: int = 0

    #: Optional SECOND bound: a token-bucket capacity for retries. ``None`` == only ``max_attempts``
    #: bounds. When set, retries consume tokens that refill at ``retry_token_refill_per_s``; an
    #: empty bucket stops retries even below ``max_attempts`` — the anti-retry-storm bound.
    retry_token_capacity: int | None = None
    retry_token_refill_per_s: float = 0.0


def retry_denial_reason(
    exc: InferenceProviderError,
    *,
    attempt: int,
    max_attempts: int,
    rate_ok: bool = True,
) -> str | None:
    """The reason this failure must NOT be retried, or ``None`` if a retry is permitted.

    The WHOLE retry classification, in one place, derived ONLY from the exception's own
    ``retryable`` (the ``_DISPOSITIONS`` truth) and ``committed`` (the W8 bytes-sent signal) plus
    the two budget bounds. No second table of retryability exists to drift from the first.

    Order is load-bearing AND side-effect-free: the terminal vetoes (``not retryable``,
    ``committed``) are checked BEFORE any budget is considered, so a committed or non-retryable
    failure never consumes an attempt slot or a rate token.
    """
    if not exc.retryable:
        return RETRY_STOP_NOT_RETRYABLE
    if exc.committed:
        return RETRY_STOP_COMMITTED
    if attempt >= max_attempts:
        return RETRY_STOP_ATTEMPTS_EXHAUSTED
    if not rate_ok:
        return RETRY_STOP_RATE_BUDGET_EXHAUSTED
    return None


def _deterministic_jitter_unit(seed: int, attempt: int) -> float:
    """A deterministic pseudo-jitter fraction in ``[0, 1)`` from ``(seed, attempt)``.

    SHA-256 of ``"{seed}:{attempt}"`` — reproducible across processes and machines, so a test pins
    the exact backoff by fixing the seed. Deliberately NOT ``random``: nondeterministic jitter in a
    test is exactly the flake this design injects around.
    """
    digest = hashlib.sha256(f"{seed}:{attempt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _backoff_delay(budget: RetryBudget, attempt: int) -> float:
    """The delay (seconds) to wait AFTER the ``attempt``-th failure, before the next attempt.

    Exponential (``base * 2**(attempt-1)``) capped at ``max_backoff_s``, plus optional deterministic
    jitter. ``attempt`` is 1-indexed: the first failure backs off ``base_backoff_s``.
    """
    # ``2.0 ** …`` (float base), not ``2 ** …``: mypy types ``int ** int`` as ``Any`` because a
    # negative exponent yields a float, which would poison this function's ``float`` return.
    capped = min(budget.base_backoff_s * (2.0 ** (attempt - 1)), budget.max_backoff_s)
    if budget.jitter_ratio <= 0.0:
        return capped
    jitter = capped * budget.jitter_ratio * _deterministic_jitter_unit(budget.jitter_seed, attempt)
    return capped + jitter


class _RetryTokenBucket:
    """Mutable token-bucket state for the retry RATE budget (the anti-storm bound).

    A ``capacity`` of ``None`` disables the bound entirely (retries are limited only by
    ``max_attempts``). Otherwise each retry consumes one token; tokens refill at a fixed rate read
    from the INJECTED ``now`` clock, so the whole thing is deterministic under an injected clock and
    a real storm across calls is genuinely bounded (the bucket lives on the provider, not per-call).
    """

    def __init__(self, *, capacity: int | None, refill_per_s: float, now: Callable[[], float]) -> None:
        self._capacity = capacity
        self._refill_per_s = refill_per_s
        self._now = now
        self._tokens = float(capacity) if capacity is not None else 0.0
        self._last = now()

    def try_consume(self) -> bool:
        """Consume one retry token; ``True`` if one was available. Unbounded when capacity is None."""
        if self._capacity is None:
            return True
        current = self._now()
        elapsed = max(0.0, current - self._last)
        self._last = current
        self._tokens = min(float(self._capacity), self._tokens + elapsed * self._refill_per_s)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
