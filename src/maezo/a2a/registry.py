"""A2A Registry — tenant-scoped AgentCard registry with a signature-verification gate.

Ported from the donor `Maezo-Healthcare-Plan` reference implementation
(`src/maezo/a2a/registry.py:239-291`, class `AgentCardRegistry`) as part of the T2.4 A2A W1
(card-signing) build wave — see `docs/design/A2A-dispatcher-card-signing.md` §1.2/§5. The class
name `A2ARegistry` is kept (v2 naming) rather than the donor's `AgentCardRegistry`.

The registry stores AgentCards keyed by `(tenant, agent_id)`, enforcing strict tenant isolation per
ADR-0004. Zero cross-contamination by construction: lookups without a tenant are structurally
impossible (`tenant` is now baked into the `AgentCard` itself, not a separate call argument).

`verifier` (a `CardSigner`) is the trust gate (ADR-0003/0007): when injected, `register` REFUSES
(fail-closed) a Card without a valid signature — so a tampered or unsigned Card never enters the
registry that a (future, W2) dispatcher consults via `lookup`. `verifier=None` (default) preserves
Phase-0/dev behavior (unsigned Cards are accepted): verification is gated on the PRESENCE of the
key, keeping the local fail-safe without loosening production.

**Breaking change from the prior (Phase-0) shape** (see `docs/design/A2A-dispatcher-card-signing.md`
§5 W1 "Breaking-change note" and `src/maezo/a2a/card.py`'s module docstring): `register` used to
take `(tenant, card)` as two separate arguments and return `None`, raising a bare `ValueError` on a
duplicate; `lookup` used to take `(tenant, agent_id)` and return `None` when missing. This version
takes `card` alone (tenant lives on the card), RE-REGISTERING the same `(tenant, agent_id)` REPLACES
the prior Card (a version bump) rather than raising, and `lookup` raises `RegistryError` when
missing (`get` is the None-returning variant). There are ZERO production call sites for the old
shape (ADR-0032), so the blast radius is this module plus `tests/unit/a2a/test_registry.py`
(updated in the same change).
"""

from __future__ import annotations

from maezo.a2a.card import AgentCard, RegistryError
from maezo.a2a.signing import CardSigner

__all__ = ["A2ARegistry", "RegistryError"]


class A2ARegistry:
    """Tenant-scoped registry of A2A AgentCards (in-memory; a durable backing store lands later).

    Key: `(tenant, agent_id)`. There is no cross-tenant lookup — `lookup`/`get`/`list_cards`
    require the tenant (ADR-0004). Re-registering the same `(tenant, agent_id)` replaces the Card
    (an Agent Definition version bump).

    Usage:
        registry = A2ARegistry()
        registry.register(card)
        card = registry.lookup("rafael", tenant="operadora-amh")

    With a `verifier` injected:
        registry = A2ARegistry(verifier=CardSigner(signing_key))
        registry.register(card)  # raises CardSignatureError if `card` is unsigned/tampered
    """

    def __init__(self, *, verifier: CardSigner | None = None) -> None:
        """Initialize an empty registry.

        Args:
            verifier: Optional `CardSigner` used as the fail-closed admission gate. When present,
                `register` refuses any Card without a signature valid under this key. When absent
                (default), Cards are admitted regardless of signature state (Phase-0/dev
                fail-safe).
        """
        self._cards: dict[tuple[str, str], AgentCard] = {}
        self._verifier = verifier

    def register(self, card: AgentCard) -> AgentCard:
        """Publish/update a Card. Returns the registered Card.

        With a `verifier` injected, validates the signature BEFORE admission (raises
        `CardSignatureError` if absent/invalid) — this is the trust chokepoint: the (future)
        dispatcher only ever `lookup`s Cards that have already been verified.
        """
        if self._verifier is not None:
            self._verifier.require_valid(card)
        self._cards[(card.tenant, card.agent_id)] = card
        return card

    def lookup(self, agent_id: str, *, tenant: str) -> AgentCard:
        """Discover the Card for `agent_id` in `tenant`. Raises `RegistryError` if absent."""
        try:
            return self._cards[(tenant, agent_id)]
        except KeyError as exc:
            raise RegistryError(f"no Agent Card for agent_id={agent_id!r} tenant={tenant!r}") from exc

    def get(self, agent_id: str, *, tenant: str) -> AgentCard | None:
        """Like `lookup`, but returns None instead of raising."""
        return self._cards.get((tenant, agent_id))

    def list_cards(self, *, tenant: str) -> tuple[AgentCard, ...]:
        """List the Cards registered for a tenant (sorted by agent_id)."""
        cards = [c for (t, _), c in self._cards.items() if t == tenant]
        return tuple(sorted(cards, key=lambda c: c.agent_id))

    def __contains__(self, key: tuple[str, str]) -> bool:
        """`(tenant, agent_id) in registry`."""
        return key in self._cards

    def __len__(self) -> int:
        return len(self._cards)
