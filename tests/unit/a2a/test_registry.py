"""Unit tests for maezo.a2a.registry — A2ARegistry (ADR-0015, ADR-0004).

TDD London School: tests written BEFORE implementation.
"""

import pytest

from maezo.a2a.card import AgentCard
from maezo.a2a.registry import A2ARegistry


def _make_card(agent_id: str) -> AgentCard:
    """Helper: create a minimal AgentCard for testing."""
    return AgentCard(
        agent_id=agent_id,
        capabilities=["triage_and_routing"],
        endpoint=f"https://{agent_id}.maezo.local/a2a",
        public_key=f"pk-{agent_id}",
    )


def test_register_and_lookup() -> None:
    """Register a card and then look it up by (tenant, agent_id)."""
    registry = A2ARegistry()
    card = _make_card("helena")

    registry.register("operadora-amh", card)

    retrieved = registry.lookup("operadora-amh", "helena")
    assert retrieved is not None
    assert retrieved.agent_id == "helena"
    assert retrieved.capabilities == card.capabilities
    assert retrieved.endpoint == card.endpoint


def test_lookup_nonexistent_agent() -> None:
    """Looking up an agent that was never registered must return None."""
    registry = A2ARegistry()

    result = registry.lookup("operadora-amh", "nonexistent")
    assert result is None


def test_lookup_wrong_tenant() -> None:
    """Looking up an agent registered under tenant-A in tenant-B must return None."""
    registry = A2ARegistry()
    card = _make_card("helena")

    registry.register("operadora-amh", card)

    # Same agent_id, different tenant
    result = registry.lookup("operadora-xyz", "helena")
    assert result is None


def test_duplicate_register_rejected() -> None:
    """Registering the same (tenant, agent_id) twice must raise an error."""
    registry = A2ARegistry()
    card1 = _make_card("helena")
    card2 = _make_card("helena")
    # card2 could differ slightly (different endpoint, same agent_id); should still reject

    registry.register("operadora-amh", card1)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("operadora-amh", card2)


def test_same_agent_different_tenants() -> None:
    """The same agent_id can be registered in different tenants independently."""
    registry = A2ARegistry()
    card_a = _make_card("helena")
    card_b = AgentCard(
        agent_id="helena",
        capabilities=["authorization_approval"],
        endpoint="https://helena.tenant-b.maezo.local/a2a",
        public_key="pk-helena-b",
    )

    registry.register("operadora-amh", card_a)
    registry.register("operadora-xyz", card_b)

    retrieved_a = registry.lookup("operadora-amh", "helena")
    retrieved_b = registry.lookup("operadora-xyz", "helena")

    assert retrieved_a is not None
    assert retrieved_b is not None
    assert retrieved_a.endpoint != retrieved_b.endpoint


def test_list_capabilities() -> None:
    """list_capabilities() must return all capabilities for agents in a tenant."""
    registry = A2ARegistry()

    registry.register("operadora-amh", _make_card("helena"))
    registry.register(
        "operadora-amh",
        AgentCard(
            agent_id="rafael",
            capabilities=["authorization_approval", "standard_glosa_processing"],
            endpoint="https://rafael.maezo.local/a2a",
            public_key="pk-rafael",
        ),
    )

    caps = registry.list_capabilities("operadora-amh")

    assert "triage_and_routing" in caps
    assert "authorization_approval" in caps
    assert "standard_glosa_processing" in caps


def test_list_capabilities_empty_tenant() -> None:
    """list_capabilities() on a tenant with no agents must return an empty set."""
    registry = A2ARegistry()

    caps = registry.list_capabilities("tenant-vazio")
    assert caps == set()


def test_list_agents() -> None:
    """list_agents() must return all agent_ids registered for a tenant."""
    registry = A2ARegistry()

    registry.register("operadora-amh", _make_card("helena"))
    registry.register("operadora-amh", _make_card("rafael"))
    registry.register("operadora-amh", _make_card("beatriz"))

    agents = registry.list_agents("operadora-amh")
    assert agents == {"helena", "rafael", "beatriz"}
