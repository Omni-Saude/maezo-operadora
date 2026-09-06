"""Gated outbound WhatsApp seam — action class `comunicacao_beneficiario` (C1, design §6.1).

Satisfies `helena/graph.py`'s and `fernando/graph.py`'s `WhatsAppSender` Protocol
(`async def send(to_hash, text) -> dict`) AND `lucas/graph.py`'s, which since LUC-08 additionally
REQUIRES `idempotency_key` (kwonly). The three are redeclared per agent (ADR-0004), so they are
free to diverge; this decorator takes the key as OPTIONAL and forwards it only when supplied —
see `GatedWhatsAppSender.send`.

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

    async def send(self, to_hash: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """§Delta W4-HYGIENE F2: `idempotency_key` is FORWARDED, never dropped.

        LUC-08 made Lucas's `WhatsAppSender` Protocol REQUIRE the kwarg, and this decorator is
        what his composition root actually hands his graph (`tool_registry.build_agent_seams`).
        A decorator that did not accept it raised `TypeError` INSIDE the graph's best-effort
        `except Exception`, so the key never reached the store, the message was never sent, and
        the turn recorded a swallowed transport failure — "protocol-preserving" was false for
        exactly the seam that had grown a parameter. Proven by
        `tests/unit/agents/test_lucas_adapters.py::
        test_the_production_seam_forwards_the_key_from_the_graph_to_the_durable_store`.

        OPTIONAL, and forwarded only when present, because Helena's and Fernando's Protocols
        (ADR-0004: redeclared per agent, never shared) still take two positional arguments and
        their inners — `webhooks/whatsapp/dispatch.py::_ScopedWhatsAppSender`,
        `agents/helena/adapters.py::WhatsAppServerSender` — accept nothing more. With the kwarg
        absent the inner call is BYTE-IDENTICAL to the pre-change one; this is the same shape
        `_ScopedWhatsAppSender` already uses for its own optional key.

        Nothing about the DECISION changes: `idempotency_key` is a delivery-dedup token, not an
        identity, and is not passed to `decide_effect` (`EffectCall` has no field for it), so no
        new value reaches a policy decision or a log line.
        """
        await gate(self._seam, _OP_SEND_MESSAGE)
        if idempotency_key is None:
            ack: dict[str, Any] = await self._inner.send(to_hash, text)
        else:
            ack = await self._inner.send(to_hash, text, idempotency_key=idempotency_key)
        return ack


def gate_whatsapp(inner: Any, seam: SeamContext) -> GatedWhatsAppSender:
    """The ONLY sanctioned way to build one (`gateway.tool_registry`, and the live dispatch path)."""
    return GatedWhatsAppSender(inner, seam=seam)


__all__ = ["GatedWhatsAppSender", "gate_whatsapp"]
