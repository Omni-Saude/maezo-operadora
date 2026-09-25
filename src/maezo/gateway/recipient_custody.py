"""Custodia cifrada do telefone do beneficiario para a retomada (GAP-XHITL-4, ADR-0061 Proposto).

Envelope encryption, com a separacao de papeis que o dono pediu expressa no proprio codigo:

  * o RECEPTOR do webhook so' CIFRA: gera uma chave de dados aleatoria (AES-256), cifra o numero
    com AES-GCM e manda o KMS EMBRULHAR a chave (`kms:Encrypt`). Ele nunca chama `Decrypt`;
  * o servico de RETOMADA so' DECIFRA: pede ao KMS para desembrulhar a chave (`kms:Decrypt`) e
    abre o GCM. Ele nunca escreve.

O AAD do GCM e o encryption context do KMS sao `(tenant, conversation_id, finalidade)`: uma linha
copiada para outra conversa nao abre, e o CloudTrail do KMS registra para QUE conversa cada
desembrulho aconteceu.

O TELEFONE EM CLARO NUNCA E' LOGADO. Nenhum log deste modulo recebe o numero, o ciphertext ou a
chave; os erros carregam so' tokens de classe (`test_recipient_custody.py` fixa isso).

O dublê de KMS NAO mora aqui (cerca §8.4: nenhum `Fake*` alcancavel de raiz de producao): ele
fica em `tests/support/recipient_custody_fakes.py`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

logger = structlog.get_logger(__name__)

#: Finalidade gravada no AAD e no encryption context — a mesma string nos dois lados.
FINALIDADE: Final[str] = "maezo-retomada-pos-humano"

#: Retencao PROPOSTA (ADR-0061): 30 dias depois da ultima mensagem do beneficiario.
DEFAULT_TTL_DAYS: Final[int] = 30

#: A janela de atendimento da Meta: mensagem livre so' ate' 24h depois da ultima mensagem recebida.
JANELA_META: Final[timedelta] = timedelta(hours=24)


class RecipientCustodyError(RuntimeError):
    """Falha da custodia. A mensagem e' SEMPRE um token de classe — nunca numero nem blob."""


def encryption_context(tenant: str, conversation_id: str) -> dict[str, str]:
    return {"tenant": tenant, "conversation_id": conversation_id, "finalidade": FINALIDADE}


def _aad(tenant: str, conversation_id: str) -> bytes:
    return f"{FINALIDADE}|{tenant}|{conversation_id}".encode()


class KeyWrapper(Protocol):
    """O KMS visto pela custodia: embrulhar (receptor) e desembrulhar (retomada)."""

    key_ref: str

    async def wrap(self, data_key: bytes, context: dict[str, str]) -> bytes: ...

    async def unwrap(self, wrapped: bytes, context: dict[str, str]) -> bytes: ...


class AwsKmsKeyWrapper:
    """`KeyWrapper` sobre o AWS KMS (boto3, ja' dependencia via `anthropic[bedrock]`).

    As chamadas sao sincronas no boto3; vao para `asyncio.to_thread` para nao travar o laco. A
    permissao e' da ROLE da task (`service-*.tf`): o receptor so' tem `kms:Encrypt`, a retomada so'
    `kms:Decrypt` — chamar o outro lado aqui daria `AccessDenied`, que e' a separacao funcionando.
    """

    def __init__(self, *, key_arn: str, region: str | None = None, client: Any = None) -> None:
        if not key_arn:
            raise RecipientCustodyError("recipient_custody: kms key arn ausente")
        self.key_ref = key_arn
        if client is None:
            import boto3  # type: ignore[import-untyped]  # lazy: so' quem liga a custodia paga

            client = boto3.client("kms", region_name=region) if region else boto3.client("kms")
        self._client = client

    async def wrap(self, data_key: bytes, context: dict[str, str]) -> bytes:
        resp = await asyncio.to_thread(
            self._client.encrypt, KeyId=self.key_ref, Plaintext=data_key, EncryptionContext=context
        )
        return bytes(resp["CiphertextBlob"])

    async def unwrap(self, wrapped: bytes, context: dict[str, str]) -> bytes:
        resp = await asyncio.to_thread(
            self._client.decrypt, KeyId=self.key_ref, CiphertextBlob=wrapped, EncryptionContext=context
        )
        return bytes(resp["Plaintext"])


@dataclass(frozen=True, slots=True)
class SealedPhone:
    key_ref: str
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes

    def __repr__(self) -> str:  # nunca imprime bytes, nem por acidente num log/traceback
        return f"SealedPhone(key_ref={self.key_ref!r}, <cifrado>)"


async def seal_phone(phone: str, wrapper: KeyWrapper, *, tenant: str, conversation_id: str) -> SealedPhone:
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(data_key).encrypt(nonce, phone.encode("utf-8"), _aad(tenant, conversation_id))
    wrapped = await wrapper.wrap(data_key, encryption_context(tenant, conversation_id))
    return SealedPhone(key_ref=wrapper.key_ref, wrapped_key=wrapped, nonce=nonce, ciphertext=ciphertext)


async def open_phone(sealed: SealedPhone, wrapper: KeyWrapper, *, tenant: str, conversation_id: str) -> str:
    try:
        data_key = await wrapper.unwrap(sealed.wrapped_key, encryption_context(tenant, conversation_id))
        plain = AESGCM(data_key).decrypt(sealed.nonce, sealed.ciphertext, _aad(tenant, conversation_id))
    except Exception as exc:
        # `from None`: a excecao original do cryptography/KMS nao carrega o numero, mas nao ha'
        # por que arriscar — o token de classe e' tudo que sai daqui.
        raise RecipientCustodyError(
            f"recipient_custody: nao foi possivel abrir ({type(exc).__name__})"
        ) from None
    return plain.decode("utf-8")


@dataclass(frozen=True, slots=True)
class RecipientRecord:
    """O que a retomada precisa: o numero (em memoria, so' para o envio) e a ultima mensagem."""

    phone: str
    last_inbound_at: datetime

    def __repr__(self) -> str:
        return f"RecipientRecord(phone=<oculto>, last_inbound_at={self.last_inbound_at.isoformat()})"


class RecipientSealer(Protocol):
    """O lado do RECEPTOR: grava/atualiza a custodia a cada mensagem recebida."""

    async def upsert(self, *, conversation_id: str, phone: str, received_at: datetime) -> None: ...


_UPSERT_SQL: Final[str] = (
    "INSERT INTO beneficiario_contato_retomada "
    "(tenant, conversation_id, key_ref, wrapped_key, nonce, ciphertext, last_inbound_at, expires_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
    "ON CONFLICT (tenant, conversation_id) DO UPDATE SET "
    "key_ref = EXCLUDED.key_ref, wrapped_key = EXCLUDED.wrapped_key, nonce = EXCLUDED.nonce, "
    "ciphertext = EXCLUDED.ciphertext, "
    "last_inbound_at = GREATEST(beneficiario_contato_retomada.last_inbound_at, EXCLUDED.last_inbound_at), "
    "expires_at = GREATEST(beneficiario_contato_retomada.expires_at, EXCLUDED.expires_at), "
    "updated_at = clock_timestamp()"
)
_SELECT_SQL: Final[str] = (
    "SELECT key_ref, wrapped_key, nonce, ciphertext, last_inbound_at FROM beneficiario_contato_retomada "
    "WHERE tenant = $1 AND conversation_id = $2 AND expires_at > clock_timestamp()"
)
_PURGE_SQL: Final[str] = "DELETE FROM beneficiario_contato_retomada WHERE expires_at <= clock_timestamp()"


class PostgresRecipientVault:
    """A custodia duravel, por tenant (`search_path` pinado em todo acquire, como os irmaos)."""

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        wrapper: KeyWrapper,
        ttl_days: int = DEFAULT_TTL_DAYS,
        pool: Any = None,
    ) -> None:
        if ttl_days < 1:
            raise ValueError("recipient_custody: ttl_days deve ser >= 1")
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._wrapper = wrapper
        self._ttl = timedelta(days=ttl_days)
        self._pool = pool

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def upsert(self, *, conversation_id: str, phone: str, received_at: datetime) -> None:
        sealed = await seal_phone(phone, self._wrapper, tenant=self._tenant, conversation_id=conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                self._tenant,
                conversation_id,
                sealed.key_ref,
                sealed.wrapped_key,
                sealed.nonce,
                sealed.ciphertext,
                received_at,
                received_at + self._ttl,
            )

    async def lookup(self, *, tenant_id: str, conversation_id: str) -> RecipientRecord | None:
        if tenant_id != self._tenant:
            raise RecipientCustodyError("recipient_custody: tenant diferente do cofre")
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, tenant_id, conversation_id)
        if row is None:
            return None
        sealed = SealedPhone(
            key_ref=str(row["key_ref"]),
            wrapped_key=bytes(row["wrapped_key"]),
            nonce=bytes(row["nonce"]),
            ciphertext=bytes(row["ciphertext"]),
        )
        phone = await open_phone(sealed, self._wrapper, tenant=tenant_id, conversation_id=conversation_id)
        return RecipientRecord(phone=phone, last_inbound_at=row["last_inbound_at"])

    async def purge_expired(self) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(_PURGE_SQL)
        return int(str(status).rsplit(" ", 1)[-1] or 0)


def dentro_da_janela(last_inbound_at: datetime, *, agora: datetime | None = None) -> bool:
    """`True` se a ultima mensagem do beneficiario tem MENOS de 24h (janela da Meta)."""
    agora = agora or datetime.now(UTC)
    return agora - last_inbound_at < JANELA_META
