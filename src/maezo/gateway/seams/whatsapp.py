"""Gated outbound WhatsApp seam — action class `comunicacao_beneficiario` (C1, design §6.1).

Satisfies `helena/graph.py:144`, `fernando/graph.py:209` and `lucas/graph.py:205`'s identical
`WhatsAppSender` Protocol (`async def send(to_hash, text) -> dict`).

THE LIVE PATH IS THE HARD ONE, AND IT IS WHY THIS WRAPPER TAKES ITS INNER PER TURN. The webhook
receiver does NOT inject a long-lived sender: `platform/webhooks/whatsapp/dispatch.py:158-160`
builds a `_ScopedWhatsAppSender` INSIDE `HelenaDispatcher.dispatch`, closing over the RAW
recipient number of the one inbound request being handled (`:100-118`, including its
hash-mismatch refusal at `:110-117`). That object cannot exist at composition time, and it must
not outlive the turn.

So the split this seam is built around: the DECISION context is per (tenant, principal) and is
built ONCE at receiver bring-up; the WRAPPER is a two-attribute object re-created per turn around
the per-turn sender. `SeamContext` is frozen and shared; `GatedWhatsAppSender(inner, seam=ctx)`
costs one object allocation. Nothing per-turn is recomputed — no policy load, no PEP build, no
manifest read.

NO RAW RECIPIENT, ANYWHERE (I-3). `send`'s `to_hash` is already a keyed HMAC pseudonym in the
graph (ADR-0035); the RAW number lives only inside `_ScopedWhatsAppSender` and is never seen by
this wrapper at all. Neither `to_hash` nor `text` is passed to `decide_effect` — `EffectCall` has
no field for either — so no recipient, in any form, reaches a decision or a log line. The
class's declared denial shape is `ESCALONAMENTO_HUMANO`: `helena/graph.py:713`'s
`except Exception` records the refusal in `error`, the turn ends on its escalation path, and the
beneficiary is not silently dropped.
"""

from __future__ import annotations

from typing import Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

_OP_SEND_MESSAGE = "whatsapp.send_message"


class GatedWhatsAppSender(GatedSeam):
    """Protocol-preserving decorator over any `WhatsAppSender` — including the per-turn scoped one."""

    __slots__ = ()

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        await gate(self._seam, _OP_SEND_MESSAGE)
        ack: dict[str, Any] = await self._inner.send(to_hash, text)
        return ack


def gate_whatsapp(inner: Any, seam: SeamContext) -> GatedWhatsAppSender:
    """The ONLY sanctioned way to build one (`gateway.tool_registry`, and the live dispatch path)."""
    return GatedWhatsAppSender(inner, seam=seam)


__all__ = ["GatedWhatsAppSender", "gate_whatsapp"]
