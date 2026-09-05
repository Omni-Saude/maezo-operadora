"""WhatsApp dedup guard — the PHI-safe adapter between the receiver and the durable registry.

Gap `WEBHOOK-WAMID-DEDUP` (owner decision R-071, 2026-09-04, option C: "tabela/registro duravel
compartilhado com TTL curto para dedup por `wamid`, mais idempotencia na saida `send`, tratadas
como uma entrega so").

WHY THIS THIN LAYER EXISTS instead of the receiver talking to `platform.driver_idempotency`
directly: the registry stores whatever key it is handed, and the ONE thing that must never vary
per call site is that the key is a KEYED PSEUDONYM of the wamid, never the wamid itself (a raw
wamid embeds the counterpart phone number as base64 — `security.py::hash_message_id`). Both legs
of the "uma entrega so" the owner asked for (the inbound guard in `app.py` and the outbound
`send` guard in `tools/mcp_whatsapp/server.py`) derive their keys HERE, from the same
`Pseudonymizer` and the same tenant, so the two legs cannot drift apart and no third call site
can invent a differently-derived key.

This object holds NO connection state of its own: the pool lives in the registry, which
`platform/webhooks/service.py` builds once at bring-up and closes on drain.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.driver_idempotency import (
    DEFAULT_TTL_S,
    DedupRegistry,
    inbound_dedup_key,
    outbound_dedup_key,
)

from .security import hash_message_id

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WhatsAppDedupGuard:
    """Keyed-pseudonym dedup for one tenant's WhatsApp channel.

    `claim_inbound` returns False for a redelivery Meta already delivered inside the TTL — the
    caller must then NOT dispatch. Every method propagates
    `platform.driver_idempotency.DedupRegistryUnavailableError` unchanged: deciding what a
    degraded registry means for an HTTP response belongs to the receiver, not here.
    """

    registry: DedupRegistry
    pseudonymizer: Pseudonymizer
    tenant: str
    ttl_s: float = DEFAULT_TTL_S

    def pseudonym(self, message_id: str) -> str:
        """The keyed `hk1_` pseudonym of one inbound wamid — safe to log and to persist."""
        return hash_message_id(message_id, self.tenant, self.pseudonymizer)

    def inbound_key(self, message_id: str) -> str:
        return inbound_dedup_key(tenant=self.tenant, message_pseudonym=self.pseudonym(message_id))

    def outbound_key(self, message_id: str, *, occurrence: int = 1) -> str:
        """Key for the Nth outbound send caused by the inbound message `message_id`.

        Derived from the INBOUND wamid on purpose: the outbound message has no id until Meta
        answers, so the only identity available BEFORE the send — which is when idempotency has to
        be decided — is the delivery that caused it.
        """
        return outbound_dedup_key(
            tenant=self.tenant, message_pseudonym=self.pseudonym(message_id), occurrence=occurrence
        )

    async def claim_inbound(self, message_id: str) -> bool:
        """`True` -> first delivery of this wamid inside the TTL; `False` -> suppress it."""
        return await self.registry.claim(self.inbound_key(message_id), ttl_s=self.ttl_s)

    async def mark_inbound_processed(self, message_id: str) -> None:
        await self.registry.mark_processed(self.inbound_key(message_id))

    async def release_inbound(self, message_id: str) -> None:
        """Withdraw the claim of a delivery whose handling FAILED, so a redelivery retries it."""
        await self.registry.release(self.inbound_key(message_id))

    async def aclose(self) -> None:
        """Release the underlying registry's resources (drain; idempotent)."""
        await self.registry.aclose()
