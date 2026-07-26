"""Unit tests for maezo.a2a.registry — A2ARegistry (ADR-0003/0004/0007/0015).

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/test_registry.py` as part of the T2.4 A2A W1 (card-signing) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5. Adaptations vs the donor: v2 keeps the
`A2ARegistry` class name (not `AgentCardRegistry`); `AgentCard.from_definition` sources the
definition via `maezo.agents.AgentLoader`/`spec/agents/` (not `maezo.runtime.harness`); the
`version` assertion checks determinism of v2's content-hash convention (v2's `AgentDefinition` has
no donor-equivalent `agent_version`).

This intentionally REPLACES the pre-W1 test suite (which exercised the old mutable, 4-field,
public_key-carrying AgentCard and the old `register(tenant, card)` signature) — there are ZERO
production call sites for that shape (ADR-0032), so the old tests are superseded rather than kept
alongside the new frozen-dataclass API. Card-signing-specific security properties (tamper/fail-
closed/constant-time/cross-tenant) live in `test_card_signing.py`; this file covers the registry's
own CRUD/tenant-isolation/derivation behavior.
"""

from __future__ import annotations

import pytest

from maezo.a2a import A2ARegistry, AgentCard, RegistryError
from maezo.agents import AgentLoader


def _card(agent_id: str, *, tenant: str = "amh", **kw: object) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        version="v0",
        tenant=tenant,
        security_zone="phi",
        **kw,  # type: ignore[arg-type]
    )


def test_register_lookup_roundtrip() -> None:
    reg = A2ARegistry()
    card = _card("rafael")
    reg.register(card)
    assert reg.lookup("rafael", tenant="amh") is card
    assert ("amh", "rafael") in reg


def test_lookup_missing_raises() -> None:
    reg = A2ARegistry()
    with pytest.raises(RegistryError):
        reg.lookup("ghost", tenant="amh")
    assert reg.get("ghost", tenant="amh") is None


def test_registration_is_tenant_scoped() -> None:
    """The same agent_id in distinct tenants is two distinct entries (ADR-0004)."""
    reg = A2ARegistry()
    reg.register(_card("rafael", tenant="amh"))
    reg.register(_card("rafael", tenant="outra-op"))
    assert reg.lookup("rafael", tenant="amh").tenant == "amh"
    assert reg.lookup("rafael", tenant="outra-op").tenant == "outra-op"
    # Lookup never crosses tenants.
    with pytest.raises(RegistryError):
        reg.lookup("rafael", tenant="tenant-inexistente")


def test_list_cards_is_tenant_filtered_and_sorted() -> None:
    reg = A2ARegistry()
    reg.register(_card("rafael", tenant="amh"))
    reg.register(_card("beatriz", tenant="amh"))
    reg.register(_card("rafael", tenant="outra-op"))
    amh = reg.list_cards(tenant="amh")
    assert [c.agent_id for c in amh] == ["beatriz", "rafael"]
    assert all(c.tenant == "amh" for c in amh)
    assert len(reg) == 3


def test_reregister_replaces_card() -> None:
    reg = A2ARegistry()
    reg.register(AgentCard(agent_id="rafael", version="v0", tenant="amh", security_zone="phi"))
    reg.register(AgentCard(agent_id="rafael", version="v1", tenant="amh", security_zone="phi"))
    assert reg.lookup("rafael", tenant="amh").version == "v1"
    assert len(reg) == 1


def test_card_requires_agent_id() -> None:
    with pytest.raises(RegistryError):
        AgentCard(agent_id="", version="v0", tenant="amh", security_zone="phi")


def test_card_requires_tenant() -> None:
    with pytest.raises(RegistryError):
        AgentCard(agent_id="rafael", version="v0", tenant="", security_zone="phi")


def test_invalid_federation_layer_rejected() -> None:
    with pytest.raises(RegistryError):
        AgentCard(agent_id="rafael", version="v0", tenant="amh", security_zone="phi", federation_layer="L9")


def test_accepts_contract() -> None:
    restrictive = _card("rafael", accepted_task_types=frozenset({"authorization.analyze"}))
    assert restrictive.accepts("authorization.analyze") is True
    assert restrictive.accepts("something.else") is False
    # A Card without declared task types accepts anything (Phase-0 compat).
    permissive = _card("legacy")
    assert permissive.accepts("anything") is True


def test_from_definition_references_agent_yaml() -> None:
    """The Card derives from the Agent Definition (source of truth); it never duplicates agent_id/zone."""
    definition = AgentLoader().load_by_id("helena")
    card = AgentCard.from_definition(definition, tenant="amh", federation_layer="L3")
    assert card.agent_id == "helena"
    assert card.security_zone == "general"
    assert "health_navigation" in card.capabilities
    assert card.queue_ref == "agents.tasks.helena"
    # Helena is not a delegation target in Phase 1.
    assert card.accepted_task_types == frozenset()
    assert card.accepts("authorization.analyze") is True  # empty = permissive


def test_from_definition_version_is_a_deterministic_content_hash() -> None:
    """`version` is a content hash of the definition (v2 has no donor-equivalent agent_version)."""
    definition = AgentLoader().load_by_id("helena")
    card_a = AgentCard.from_definition(definition, tenant="amh")
    card_b = AgentCard.from_definition(definition, tenant="amh")
    assert card_a.version == card_b.version
    assert card_a.version != ""


def test_from_definition_signs_when_signer_injected() -> None:
    from maezo.a2a import CardSigner

    signer = CardSigner(b"test-fixture-registry-signing-key-000")
    definition = AgentLoader().load_by_id("rafael")
    card = AgentCard.from_definition(definition, tenant="amh", signer=signer)
    assert card.is_signed is True
    assert signer.verify(card) is True
