"""Dublês da custodia do telefone (ADR-0061). Test-only — nunca importado por `src/`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from maezo.gateway.recipient_custody import (
    RecipientRecord,
    SealedPhone,
    open_phone,
    seal_phone,
)


class FakeKms:
    """KMS local: embrulha com uma chave AES propria e EXIGE o mesmo encryption context.

    `allow_encrypt`/`allow_decrypt` simulam a separacao de IAM (receptor so' cifra, retomada so'
    decifra): o lado negado levanta, como o `AccessDenied` real.
    """

    def __init__(
        self, *, allow_encrypt: bool = True, allow_decrypt: bool = True, key_ref: str = "fake-kms"
    ) -> None:
        self.key_ref = key_ref
        self._key = AESGCM.generate_key(bit_length=256)
        self.allow_encrypt = allow_encrypt
        self.allow_decrypt = allow_decrypt
        self.calls: list[tuple[str, dict[str, str]]] = []

    def with_permissions(self, *, allow_encrypt: bool, allow_decrypt: bool) -> FakeKms:
        other = FakeKms(allow_encrypt=allow_encrypt, allow_decrypt=allow_decrypt, key_ref=self.key_ref)
        other._key = self._key
        other.calls = self.calls
        return other

    @staticmethod
    def _aad(context: dict[str, str]) -> bytes:
        return json.dumps(context, sort_keys=True).encode()

    async def wrap(self, data_key: bytes, context: dict[str, str]) -> bytes:
        self.calls.append(("encrypt", dict(context)))
        if not self.allow_encrypt:
            raise PermissionError("AccessDenied: kms:Encrypt")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, data_key, self._aad(context))

    async def unwrap(self, wrapped: bytes, context: dict[str, str]) -> bytes:
        self.calls.append(("decrypt", dict(context)))
        if not self.allow_decrypt:
            raise PermissionError("AccessDenied: kms:Decrypt")
        return AESGCM(self._key).decrypt(wrapped[:12], wrapped[12:], self._aad(context))


@dataclass
class InMemoryRecipientVault:
    """A mesma semantica de `PostgresRecipientVault` (upsert, cifrado, `last_inbound_at` so' avanca)
    sem banco. Guarda SO' o `SealedPhone` — o numero em claro nao fica em lugar nenhum."""

    tenant: str
    kms: FakeKms
    rows: dict[str, tuple[SealedPhone, datetime]] = field(default_factory=dict)

    async def upsert(self, *, conversation_id: str, phone: str, received_at: datetime) -> None:
        sealed = await seal_phone(phone, self.kms, tenant=self.tenant, conversation_id=conversation_id)
        previous = self.rows.get(conversation_id)
        last = max(received_at, previous[1]) if previous else received_at
        self.rows[conversation_id] = (sealed, last)

    async def lookup(self, *, tenant_id: str, conversation_id: str) -> RecipientRecord | None:
        row = self.rows.get(conversation_id)
        if row is None or tenant_id != self.tenant:
            return None
        phone = await open_phone(row[0], self.kms, tenant=tenant_id, conversation_id=conversation_id)
        return RecipientRecord(phone=phone, last_inbound_at=row[1])
