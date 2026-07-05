"""Anti-Loop Guard — structural delegation loop prevention (ADR-0003, ADR-0015).

The AntiLoopGuard enforces two structural invariants on delegation chains:
  Guard 1 (acyclic): No agent may appear twice in a delegation chain.
  Guard 2 (max_depth): The chain length must not exceed `max_depth` hops.

Both guards are enforced at validation time — it is impossible to construct
or submit an invalid delegation chain without raising an error.

Per ADR-0003, default max_depth is 3.
Per ADR-0015, max_depth is checked BEFORE cycle detection (first guard wins).
"""

from __future__ import annotations


class CyclicDelegationError(ValueError):
    """Raised when a delegation chain contains a cycle (agent appears twice)."""


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
