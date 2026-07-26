"""Anti-Loop Guard — structural delegation loop prevention (ADR-0003, ADR-0015).

**DEPRECATED (W2, design §1.3/§7.2 decision #3):** this standalone validator predates the A2A
delegation runtime. The donor `Maezo-Healthcare-Plan` reference implementation has no equivalent
standalone module — it folds the acyclic + max-hops + budget guards STRUCTURALLY into
`DelegationEnvelope.root()`/`.extend()` (`maezo.a2a.delegation`), which is now where the real
enforcement lives for anything that builds an envelope. `AntiLoopGuard` is kept here only as a
thin, backward-compatible façade over a bare `list[str]` chain — there are ZERO production call
sites for it (ADR-0032), so nothing depends on it beyond its own test suite
(`tests/unit/a2a/test_anti_loop.py`).

`CyclicDelegationError` is NOT redefined here anymore: this module used to declare its own
`CyclicDelegationError(ValueError)`, while the donor's `delegation.py` independently declares
`CyclicDelegationError(DelegationError)` — two classes with the same name is exactly the
"CyclicDelegationError name collision" design doc §1.3 flags. This module now re-exports
`maezo.a2a.delegation.CyclicDelegationError` (the canonical definition, matching the donor's
hierarchy: `DelegationError(ValueError)` -> `CyclicDelegationError`) so there is exactly ONE class
in the codebase, not two structurally-identical-but-distinct ones. `MaxDepthExceededError` has no
donor-side name collision (the donor's equivalent is the distinctly-named `MaxHopsExceededError`)
and is kept as-is.

The AntiLoopGuard enforces two structural invariants on delegation chains:
  Guard 1 (acyclic): No agent may appear twice in a delegation chain.
  Guard 2 (max_depth): The chain length must not exceed `max_depth` hops.

Both guards are enforced at validation time — it is impossible to construct
or submit an invalid delegation chain without raising an error.

Per ADR-0003, default max_depth is 3.
Per ADR-0015, max_depth is checked BEFORE cycle detection (first guard wins).
"""

from __future__ import annotations

from maezo.a2a.delegation import CyclicDelegationError

__all__ = ["AntiLoopGuard", "CyclicDelegationError", "MaxDepthExceededError"]


class MaxDepthExceededError(ValueError):
    """Raised when a delegation chain exceeds max_depth."""


class AntiLoopGuard:
    """Structural guard against delegation loops.

    Validates delegation chains against depth and cycle invariants.
    Use validate_chain() before submitting any delegation.
    """

    def __init__(self, max_depth: int = 3) -> None:
        """Initialize the guard.

        Args:
            max_depth: Maximum allowed delegation chain length (default: 3).
        """
        self.max_depth = max_depth

    def validate_chain(self, chain: list[str]) -> None:
        """Validate a delegation chain.

        Checks are applied in order:
        1. Max depth: len(chain) must not exceed max_depth.
        2. Cycle: no agent_id may appear more than once in the chain.

        Args:
            chain: Ordered list of agent_ids in the delegation chain.

        Raises:
            MaxDepthExceededError: If the chain exceeds max_depth.
            CyclicDelegationError: If an agent appears twice in the chain.
        """
        if len(chain) > self.max_depth:
            raise MaxDepthExceededError(
                f"Delegation chain exceeds max depth: {len(chain)} > {self.max_depth}"
            )

        seen: set[str] = set()
        for agent_id in chain:
            if agent_id in seen:
                raise CyclicDelegationError(f"Delegation cycle detected: agent {agent_id!r} appears twice")
            seen.add(agent_id)
