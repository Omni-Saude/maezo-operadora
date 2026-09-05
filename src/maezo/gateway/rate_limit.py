"""Token-bucket rate limiting for the effect chokepoint (gap D6-01).

WHAT WAS MISSING. `grep -rn 'rate_limit\\|RateLimit\\|token_bucket' src/maezo/gateway/
src/maezo/runtime/` returned exactly two hits before this module existed, both
`except anthropic.RateLimitError` in `runtime/inference/providers.py` — handling of a limit the
UPSTREAM vendor imposes on us, not a limit we impose on an agent. Nothing in this tree bounded the
RATE at which a principal could drive effects. `a2a/anti_loop.py` bounds delegation DEPTH (chain
length <= max_depth, ADR-0003) which is a different invariant entirely: a graph that loops between
two nodes at depth 1 makes an unbounded number of engine/FHIR/LLM calls without ever growing a
delegation chain.

WHERE IT LIVES, AND WHY THERE AND NOWHERE ELSE. Inside `seams/_base.py::gate` — the ONE function
every gated seam calls exactly once per invocation (design §5.4/§8, the same argument
`_count_tool_call` is written against). Placing the limiter BESIDE the chokepoint (in a wrapper, a
root, or a middleware) would leave a principal able to reach an effect without passing it, which is
invariant I-11 ("the chokepoint must be INEVITABLE, not merely available"). A seam that skipped the
limiter would also have skipped the policy decision, so coverage is structural rather than a
convention someone must remember.

WHY IT REFUSES EVEN THOUGH THE POLICY LADDER IS IN SHADOW. Every action class ships
`enforcement: shadow` today, so `EffectDecision.enforced` is False everywhere and a POLICY deny
only logs. A rate limit is deliberately NOT on that ramp. The shadow ramp exists because deciding
whether a given clinical action is permitted is a governance question that needs approver evidence
per class (design §9.2); "one principal may not make ten thousand engine calls a second" is not a
governance question about the action, it is an availability and abuse control over the runtime, and
a control that is configured but does not refuse is not a control. So the refusal here is
UNCONDITIONAL and fail-closed: over the limit, the effect does not happen.

THE REFUSAL IS STRUCTURED, NEVER A SILENT DROP, AND NEVER THE BASE CLASS. It is raised through
`seams/_base.py::denial_for` in the action class's OWN declared denial shape, which since D6-01-F1
is a NAMED `EffectDeniedError` subclass for every one of the seven shapes — the two that need a
second base to be caught (`DmnEffectDeniedError` for `_evaluate_dmn`'s
`except (DmnEvaluationError, DmnNoResultError)`; `CibSevenEffectDeniedError` for the nine
`except CibSevenError` start-process nodes) and the five whose nodes catch bare `Exception` and
fold the refusal into a gap note. That distinction is worth stating precisely rather than
generalising from the DMN case: for the LACUNA_DECLARADA / ESCALONAMENTO_HUMANO /
ROTA_LLM_INDISPONIVEL / DEGRADACAO_SEM_DOSSIE families the node's handler is a broad
`except Exception`, so what the named type buys is identification (in a handler, a log line, a
`type(exc).__name__` gap note), not reachability. Until D6-01-F1 those ten operations refused with
the bare `EffectDeniedError`, and a `RuntimeError` on a raising path that shadow mode had never
exercised is exactly the A-12 shape this control must not introduce.

The refusal carries only bounded tokens (I-3: operation, class, layer, reason, shape — never a
call argument, never PHI). It also increments `maezo_effect_rate_limited_total{tenant,principal}`
and writes ONE structured log line, so a throttled principal is visible in both the metric and the
log stream rather than inferred from missing work.

NO I/O, NO LOCK, NO CLOCK SKEW. `time.monotonic()` plus float arithmetic over a dict — I-9's
budget is "<= 1 ms p99 added per gated call, ZERO added network I/O", and a bucket update is a few
hundred nanoseconds. The state is per PROCESS, which is the honest scope: this is a per-replica
guard, not a distributed quota (a distributed one needs a shared store, i.e. network I/O on every
effect call, which I-9 forbids). A deployment with N replicas therefore admits up to N times the
configured rate; that is disclosed here rather than papered over, and it still bounds a runaway
loop inside ONE replica, which is the failure mode D6-01 names.

NOT ASYNC-LOCKED, DELIBERATELY. `consume` is a synchronous, non-awaiting read-modify-write over
plain floats: no `await` can interleave inside it, so on an event loop it is already atomic. A
`asyncio.Lock` would add a suspension point on the effect path to protect nothing. Across OS
threads the worst case is a lost update on a single token, which cannot turn a refusal into an
allowance beyond one call — and no root drives `gate` from more than one thread today.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# =================================================================================================
# Defaults — derived, not chosen by feel
# =================================================================================================
#
# The busiest legitimate principal in this tree is the worker daemon, and its throughput is
# CONFIGURED, so the sustained rate can be read off the settings rather than guessed:
# `worker_runtime/settings.py` ships `max_tasks_per_poll=10` and `poll_interval_ms=5000`, i.e. at
# most 10 external tasks per 5 s per replica. The heaviest task shape in this repo touches the
# gated seams a handful of times (DMN evaluate + engine complete/failure + a FHIR read on the
# dossier edges); taking SIX gated calls per task as the worst case gives
#
#     10 tasks / 5 s * 6 gated calls = 12 gated calls per second, sustained.
#
# DEFAULT_REFILL_PER_SECOND is 20, ~1.7x that worst case, so a fully-loaded worker never touches
# the limiter. An agent turn is far below this: the live WhatsApp path makes one turn per inbound
# message and a turn's graph is statically wired (design §2 — "there is no LLM-chosen tool
# dispatch"), so its gated-call count is bounded by the node count, not by a model's choices.
#
# DEFAULT_CAPACITY is 120 = twice the ~60 gated calls a full 10-task poll batch can produce when
# every task arrives at once, so an ENTIRE burst batch fits in the bucket with a batch of headroom.
# From empty, 120 tokens refill in 6 s at 20/s — comfortably inside the worker's
# `lock_duration_ms=30000` external-task lock, so a throttled task is delayed, never lost to a
# lock expiry it could not renew.
#
# These are the FLOOR, not a policy: both are settings (`GatewaySettings.rate_limit_capacity` /
# `rate_limit_refill_per_second`) so a deployment that measures a higher legitimate rate raises
# them by env, and a `0` refill is refused at settings validation rather than silently creating a
# bucket that can never refill.

#: Burst allowance per (tenant, principal), in gated calls. See the derivation above.
DEFAULT_CAPACITY: Final[int] = 120

#: Sustained gated calls per second per (tenant, principal). See the derivation above.
DEFAULT_REFILL_PER_SECOND: Final[float] = 20.0

#: The bounded reason token a rate-limited refusal carries. Same vocabulary shape as
#: `effect_pep.EFFECT_REASONS` (upper snake, pt-BR, non-PHI) and deliberately DISTINCT from every
#: member of it: a throttled call is not an unauthorised one, and an approver reading the telemetry
#: must be able to tell "this principal was too fast" from "this principal was not allowed".
REASON_RATE_LIMITED: Final[str] = "TAXA_EXCEDIDA"

#: The layer token for the same reason. Not an `EffectLayer` member: the ladder L-0..L-5 is the
#: POLICY ladder, and inventing an L-6 inside it would imply the rate limit is one more policy
#: layer that the shadow ramp governs. It is not — it always enforces.
LAYER_RATE_LIMIT: Final[str] = "L_TAXA"


@dataclass(slots=True)
class TokenBucket:
    """One (tenant, principal)'s bucket. Pure arithmetic — no clock reads except `monotonic`.

    Attributes:
        capacity: maximum tokens held, i.e. the burst allowance.
        refill_per_second: tokens added per second of elapsed monotonic time.
        tokens: tokens currently held. Starts FULL, so a freshly built replica is not throttled
            for its first calls (an empty start would refuse legitimate boot-time traffic).
        updated_at: the `time.monotonic()` reading the token count is current as of.
    """

    capacity: int
    refill_per_second: float
    tokens: float = field(default=0.0)
    updated_at: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {self.capacity}")
        if self.refill_per_second <= 0:
            raise ValueError(f"refill_per_second must be > 0, got {self.refill_per_second}")
        self.tokens = float(self.capacity)

    def consume(self, *, now: float, amount: float = 1.0) -> bool:
        """Refill for elapsed time, then take `amount` tokens if they are there.

        Args:
            now: a `time.monotonic()` reading. Injected rather than read here so the bucket maths
                is testable without sleeping — a limiter whose only proof is a `sleep` is a
                limiter nobody re-runs.
            amount: tokens this call costs. One gated call = one token.

        Returns:
            True when the call may proceed (tokens were taken), False when it is over the limit
            (NOTHING is taken — a refused call must not dig the bucket deeper and starve the
            calls behind it).
        """
        elapsed = now - self.updated_at
        if elapsed > 0:
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_per_second)
        # A backwards `now` (never produced by `monotonic`, but possible from an injected clock)
        # is treated as zero elapsed rather than as a refund.
        self.updated_at = now
        if self.tokens < amount:
            return False
        self.tokens -= amount
        return True


class RateLimiter:
    """Token buckets keyed by (tenant, principal) — the D6-01 throttle.

    PER-KEY ISOLATION IS THE POINT. One agent looping must not consume another agent's budget, and
    one tenant must not consume another's (invariant I-8, tenant isolation, applied to throughput).
    The key is the SAME pair the chokepoint already decides under — `SeamContext.tenant` /
    `SeamContext.principal`, both closure-bound and validated as bounded non-PHI tokens at
    `SeamContext` construction — so a key can never carry a call argument.
    """

    __slots__ = ("_buckets", "_capacity", "_refill_per_second")

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        if refill_per_second <= 0:
            raise ValueError(f"refill_per_second must be > 0, got {refill_per_second}")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._buckets: dict[tuple[str, str], TokenBucket] = {}

    @property
    def capacity(self) -> int:
        """The configured burst allowance per key."""
        return self._capacity

    @property
    def refill_per_second(self) -> float:
        """The configured sustained rate per key."""
        return self._refill_per_second

    def allow(self, *, tenant: str, principal: str, now: float | None = None) -> bool:
        """True iff this (tenant, principal) may make ONE more gated call right now.

        Args:
            tenant: the closure-bound tenant of the calling seam.
            principal: the closure-bound principal (agent id / daemon principal).
            now: monotonic reading; defaults to `time.monotonic()`. Injectable for the bucket-maths
                tests (see :meth:`TokenBucket.consume`).

        Returns:
            True to proceed, False when the key is over its limit.
        """
        reading = time.monotonic() if now is None else now
        key = (tenant, principal)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(capacity=self._capacity, refill_per_second=self._refill_per_second)
            bucket.updated_at = reading
            self._buckets[key] = bucket
        return bucket.consume(now=reading)

    def keys(self) -> frozenset[tuple[str, str]]:
        """The (tenant, principal) pairs that have a bucket. For the tests and for diagnostics."""
        return frozenset(self._buckets)


__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_REFILL_PER_SECOND",
    "LAYER_RATE_LIMIT",
    "REASON_RATE_LIMITED",
    "RateLimiter",
    "TokenBucket",
]
