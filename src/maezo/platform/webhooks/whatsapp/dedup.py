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

import json
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer
from maezo.platform.driver_idempotency import (
    DEFAULT_TTL_S,
    DedupRegistry,
    inbound_dedup_key,
    inbound_fallback_dedup_key,
    outbound_dedup_key,
)

from .security import hash_message_id, hash_phone

if TYPE_CHECKING:  # import cycle guard: dispatch.py imports this module at runtime
    from .dispatch import InboundEvent

logger = structlog.get_logger(__name__)

#: Quantas entregas IGUAIS de uma mensagem sem `wamid` passam antes da supressao (F-147 / R-060).
#: NAO e' ajuste de engenharia: e' o numero da decisao do dono de 2026-10-31 (#1084, Rodada 2
#: "Marina / WhatsApp / chaves": "ate 2, suprime a 3a em diante, silenciosa"). Mudar exige nova
#: decisao do dono, nao um PR de tuning.
FALLBACK_MAX_ENTREGAS: Final[int] = 2

#: Versao do formato canonico da identidade de fallback. Entra no HMAC: mudar o formato muda a
#: versao, e uma identidade antiga nunca colide por acaso com uma nova.
_FALLBACK_IDENTITY_VERSION: Final[str] = "sem-wamid-v1"


def _normalize_body(text: str) -> str:
    """NFC + bordas aparadas. So' o que a MESMA mensagem pode variar sem mudar de sentido."""
    return unicodedata.normalize("NFC", text).strip()


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

    # --- Mensagem SEM `wamid` (F-147 / R-060; decisao do dono 2026-10-31, #1084) -------------

    def fallback_identity(self, message: InboundEvent) -> str:
        """Identidade KEYED `hk1_` de uma mensagem que chegou sem `wamid`.

        HMAC com a chave do vault (ADR-0035) sobre a forma canonica de: pseudonimo keyed do
        telefone (`hash_phone`), `timestamp` da Meta, tipo da mensagem e um digest KEYED do corpo.
        O corpo nunca entra num hash sem chave: "sim", "1" ou os digitos de um CPF sao revertidos
        por dicionario em um sha256 puro. O telefone cru e o texto cru nunca saem daqui — nem na
        chave persistida, nem em log, nem em rotulo de metrica.

        Uma reentrega da Meta e' byte a byte o mesmo envelope, entao produz a mesma identidade;
        o beneficiario que de fato repete "sim" gera outro `timestamp` e portanto outra identidade.
        """
        from .dispatch import InboundMessage  # local: dispatch.py imports this module at load time

        phone_pseudonym = hash_phone(message.from_number, self.tenant, self.pseudonymizer)
        if isinstance(message, InboundMessage):
            message_type = "text"
            body = _normalize_body(message.text)
        else:
            message_type = message.message_type
            body = ""
        body_digest = (
            self.pseudonymizer.pseudonymize({"telefone": f"{self.tenant}:corpo:{body}"})["telefone"]
            if body
            else ""
        )
        canonical = json.dumps(
            [
                _FALLBACK_IDENTITY_VERSION,
                self.tenant,
                phone_pseudonym,
                message.timestamp,
                message_type,
                body_digest,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        digest = self.pseudonymizer.pseudonymize({"telefone": canonical})["telefone"]
        return f"{KEYED_PSEUDONYM_PREFIX}{digest}"

    def fallback_key(self, identity: str, *, occurrence: int) -> str:
        return inbound_fallback_dedup_key(
            tenant=self.tenant, identity_pseudonym=identity, occurrence=occurrence
        )

    async def claim_without_wamid(self, identity: str) -> str | None:
        """Reivindica o primeiro SLOT livre (1..:data:`FALLBACK_MAX_ENTREGAS`) da identidade.

        Devolve a chave do slot ganho (o chamador despacha e depois sela/devolve ESSA chave) ou
        `None` quando todos os slots ja estao tomados — a entrega deve ser suprimida. Propaga
        `DedupRegistryUnavailableError` sem traduzir: o que um registro fora significa para a
        resposta HTTP e' decisao do receptor.
        """
        for occurrence in range(1, FALLBACK_MAX_ENTREGAS + 1):
            key = self.fallback_key(identity, occurrence=occurrence)
            if await self.registry.claim(key, ttl_s=self.ttl_s):
                return key
        return None

    async def seal_key(self, key: str) -> None:
        """Sela uma chave ja reivindicada (o efeito aconteceu)."""
        await self.registry.mark_processed(key)

    async def release_key(self, key: str) -> None:
        """Devolve uma chave reivindicada cujo efeito FALHOU, para a reentrega ter outra chance."""
        await self.registry.release(key)

    async def aclose(self) -> None:
        """Release the underlying registry's resources (drain; idempotent)."""
        await self.registry.aclose()
