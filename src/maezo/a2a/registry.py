"""A2A Registry — tenant-scoped AgentCard registry (ADR-0015, ADR-0004).

The A2ARegistry stores AgentCards keyed by (tenant, agent_id), enforcing
strict tenant isolation per ADR-0004. Zero cross-contamination by construction:
lookups without a tenant are structurally impossible.

Per ADR-0015:
- Cards are derived from AgentDefinitions, not configured standalone.
- The registry is tenant-scoped: key = (tenant, agent_id).
- Capabilities lookup is per-tenant.
- Duplicate registrations are rejected.
"""

from __future__ import annotations

from typing import Any

from maezo.a2a.card import AgentCard


class A2ARegistry:
    """Tenant-scoped registry of A2A AgentCards.

    Cards are indexed by (tenant, agent_id). All lookups require
    explicit tenant context — cross-tenant access is impossible
    by construction (ADR-0004).

    Usage:
        registry = A2ARegistry()
        registry.register("operadora-amh", card)
        card = registry.lookup("operadora-amh", "helena")
        caps = registry.list_capabilities("operadora-amh")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._cards: dict[tuple[str, str], AgentCard] = {}

    def register(self, tenant: str, card: AgentCard) -> None:
        """Register an AgentCard for a given tenant.

        Args:
            tenant: Tenant identifier (e.g. 'operadora-amh').
            card: The AgentCard to register.

        Raises:
            ValueError: If (tenant, agent_id) is already registered.
        """
        key = (tenant, card.agent_id)
        if key in self._cards:
            raise ValueError(f"Agent {card.agent_id!r} already registered for tenant {tenant!r}")
        self._cards[key] = card

    def lookup(self, tenant: str, agent_id: str) -> AgentCard | None:
        """Look up an AgentCard by tenant and agent_id.

        Args:
            tenant: Tenant identifier.
            agent_id: Agent identifier to look up.

        Returns:
            The AgentCard if found, or None.
        """
        return self._cards.get((tenant, agent_id))

    def list_capabilities(self, tenant: str) -> set[str]:
        """List all capabilities available in a given tenant.

        Args:
            tenant: Tenant identifier.

        Returns:
            A set of all capability strings across all agents in the tenant.
        """
        caps: set[str] = set()
        for (t, _), card in self._cards.items():
            if t == tenant:
                caps.update(card.capabilities)
        return caps

    def list_agents(self, tenant: str) -> set[str]:
        """List all agent_ids registered for a given tenant.

        Args:
            tenant: Tenant identifier.

        Returns:
            A set of agent_id strings.
        """
        return {agent_id for (t, agent_id) in self._cards if t == tenant}

    @property
    def tenant_count(self) -> int:
        """Return the number of distinct tenants with registered agents."""
        return len({t for (t, _) in self._cards})

    def to_dict(self) -> dict[str, Any]:
        """Serialize the registry to a dict (for debugging/persistence)."""
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (tenant, agent_id), card in self._cards.items():
            if tenant not in result:
                result[tenant] = {}
            result[tenant][agent_id] = card.to_dict()
        return result
