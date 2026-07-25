"""Unit tests for maezo.a2a.card — AgentCard (ADR-0003, ADR-0007, ADR-0015).

Rewritten for the W1 card-signing build wave (see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5 and `src/maezo/a2a/card.py`'s module
docstring) — the old `AgentCard` was a plain mutable class with exactly
`agent_id, capabilities: list[str], endpoint, public_key: str` and `to_dict`/`from_dict`. This
suite covers the new frozen-dataclass shape's own behavior (construction, immutability,
`accepts()`); the signature lifecycle (sign/verify/tamper/fail-closed) is covered end-to-end in
`test_card_signing.py`, and registry admission/tenant-isolation in `test_registry.py`.
"""

from __future__ import annotations

import dataclasses

import pytest

from maezo.a2a.card import AgentCard, RegistryError


def _card(**kw: object) -> AgentCard:
    base: dict[str, object] = {
        "agent_id": "helena",
        "version": "v0",
        "tenant": "amh",
        "security_zone": "general",
    }
    base.update(kw)
    return AgentCard(**base)  # type: ignore[arg-type]


def test_card_required_fields_and_defaults() -> None:
    """agent_id/version/tenant/security_zone are required; the rest default sensibly."""
    card = _card()
    assert card.agent_id == "helena"
    assert card.version == "v0"
    assert card.tenant == "amh"
    assert card.security_zone == "general"
    assert card.capabilities == frozenset()
    assert card.skills == frozenset()
    assert card.accepted_task_types == frozenset()
    assert card.federation_layer == "L3"
    assert card.endpoint is None
    assert card.queue_ref is None
    assert card.signature is None
    assert card.is_signed is False


def test_card_carries_full_field_set() -> None:
    card = _card(
        capabilities=frozenset({"triage_and_routing", "scheduling"}),
        skills=frozenset({"whatsapp_triage"}),
        accepted_task_types=frozenset({"authorization.analyze"}),
        federation_layer="L2",
        endpoint="https://helena.maezo.local/a2a",
        queue_ref="agents.tasks.helena",
    )
    assert card.capabilities == {"triage_and_routing", "scheduling"}
    assert card.skills == {"whatsapp_triage"}
    assert card.accepted_task_types == {"authorization.analyze"}
    assert card.federation_layer == "L2"
    assert card.endpoint == "https://helena.maezo.local/a2a"
    assert card.queue_ref == "agents.tasks.helena"


def test_card_is_frozen() -> None:
    """AgentCard is immutable — mutating a field must raise, not silently succeed."""
    card = _card()
    with pytest.raises(dataclasses.FrozenInstanceError):
        card.agent_id = "someone-else"  # type: ignore[misc]


def test_card_is_hashable_and_equatable() -> None:
    """Frozen + slots -> hashable (usable as a dict/set key) and value-equal by field tuple."""
    a = _card(capabilities=frozenset({"x"}))
    b = _card(capabilities=frozenset({"x"}))
    c = _card(capabilities=frozenset({"y"}))
    assert a == b
    assert a != c
    assert {a, b, c} == {a, c}  # a == b, so the set collapses to 2 members
    assert hash(a) == hash(b)


def test_signed_copy_only_changes_signature() -> None:
    card = _card(capabilities=frozenset({"x"}))
    signed = card.signed_copy("v1:deadbeef")
    assert signed.signature == "v1:deadbeef"
    assert signed.is_signed is True
    assert dataclasses.replace(signed, signature=None) == card


def test_accepts_permissive_when_no_task_types_declared() -> None:
    """Phase-0 compat: a Card with no `accepted_task_types` accepts any task_type."""
    card = _card()
    assert card.accepted_task_types == frozenset()
    assert card.accepts("anything.at.all") is True


def test_accepts_restrictive_when_task_types_declared() -> None:
    card = _card(accepted_task_types=frozenset({"authorization.analyze"}))
    assert card.accepts("authorization.analyze") is True
    assert card.accepts("payment.approve") is False


@pytest.mark.parametrize("bad_agent_id", ["", None])
def test_card_requires_nonempty_agent_id(bad_agent_id: str | None) -> None:
    with pytest.raises(RegistryError):
        _card(agent_id=bad_agent_id)


@pytest.mark.parametrize("bad_tenant", ["", None])
def test_card_requires_nonempty_tenant(bad_tenant: str | None) -> None:
    with pytest.raises(RegistryError):
        _card(tenant=bad_tenant)


def test_card_rejects_unknown_federation_layer() -> None:
    with pytest.raises(RegistryError):
        _card(federation_layer="L9")


@pytest.mark.parametrize("layer", ["L0", "L1", "L2", "L3"])
def test_card_accepts_all_valid_federation_layers(layer: str) -> None:
    assert _card(federation_layer=layer).federation_layer == layer
