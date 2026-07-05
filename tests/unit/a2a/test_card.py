"""Unit tests for maezo.a2a.card — AgentCard (ADR-0003, ADR-0015).

TDD London School: tests written BEFORE implementation.
"""

from maezo.a2a.card import AgentCard


def test_card_serialization_to_dict() -> None:
    """AgentCard.to_dict() must produce a dict with all fields."""
    card = AgentCard(
        agent_id="helena",
        capabilities=["triagem_whatsapp", "agendamento"],
        endpoint="https://helena.maezo.local/a2a",
        public_key="-----BEGIN PUBLIC KEY-----\nMIIB...\n-----END PUBLIC KEY-----",
    )

    d = card.to_dict()

    assert d["agent_id"] == "helena"
    assert d["capabilities"] == ["triagem_whatsapp", "agendamento"]
    assert d["endpoint"] == "https://helena.maezo.local/a2a"
    assert d["public_key"] == "-----BEGIN PUBLIC KEY-----\nMIIB...\n-----END PUBLIC KEY-----"


def test_card_serialization_from_dict() -> None:
    """AgentCard.from_dict() must reconstruct the same AgentCard."""
    original = AgentCard(
        agent_id="rafael",
        capabilities=["aprovacao_auth_dmn_favoravel", "glosa_padrao"],
        endpoint="https://rafael.maezo.local/a2a",
        public_key="pk-rafael",
    )

    d = original.to_dict()
    restored = AgentCard.from_dict(d)

    assert restored.agent_id == original.agent_id
    assert restored.capabilities == original.capabilities
    assert restored.endpoint == original.endpoint
    assert restored.public_key == original.public_key


def test_card_roundtrip_json_like() -> None:
    """AgentCard must survive to_dict() → from_dict() roundtrip."""
    card = AgentCard(
        agent_id="beatriz",
        capabilities=["resposta_nip", "envio_ans"],
        endpoint="https://beatriz.maezo.local/a2a",
        public_key="pk-beatriz",
    )

    restored = AgentCard.from_dict(card.to_dict())

    assert restored.agent_id == card.agent_id
    assert restored.capabilities == card.capabilities
    assert restored.endpoint == card.endpoint
    assert restored.public_key == card.public_key


def test_card_empty_capabilities() -> None:
    """AgentCard with empty capabilities is valid (Phase 0 compatibility per ADR-0015)."""
    card = AgentCard(
        agent_id="agent-phase0",
        capabilities=[],
        endpoint="https://agent.maezo.local/a2a",
        public_key="pk",
    )

    assert card.capabilities == []
    d = card.to_dict()
    assert d["capabilities"] == []


def test_card_required_fields() -> None:
    """AgentCard must expose agent_id, capabilities, endpoint, public_key as attributes."""
    card = AgentCard(
        agent_id="agent-1",
        capabilities=["c1"],
        endpoint="https://agent-1.maezo.local/a2a",
        public_key="pk-1",
    )

    assert hasattr(card, "agent_id")
    assert hasattr(card, "capabilities")
    assert hasattr(card, "endpoint")
    assert hasattr(card, "public_key")
