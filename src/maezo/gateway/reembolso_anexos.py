"""Custodia dos anexos do pedido de reembolso no S3 com SSE-KMS (DL-0161/DL-0162; Zona PHI, ADR-0006).

O QUE GUARDA. A foto ou o PDF que a beneficiaria manda pelo WhatsApp (pedido medico, nota fiscal), por
inteiro, num bucket privado cifrado com chave KMS propria (`deploy/.../reembolso-anexos.tf`). A chave do
objeto
e' `reembolso/{tenant}/{document_ref}`, com `document_ref = rdoc_<32 hex>` ALEATORIO: nenhum dado pessoal no
nome, nenhum nome de arquivo (o da Meta e' metadado PHI e e' descartado). Metadados: so' o mime, o sha256 e o
`conversation_id` keyed (pseudonimo, para o apagamento LGPD por conversa).

INTEGRIDADE (ADR-0020, principio do `input_hash`). O sha256 do conteudo volta para quem grava e viaja nas
variaveis do processo (`documentos_refs[].sha256`), que o start auditado amarra por `input_sha256`. Quem LE
confere o sha256 do objeto contra o esperado: um objeto trocado no bucket e' recusado, nunca entregue.

SEPARACAO DE PAPEIS (IAM). O receptor do webhook so' GRAVA; o portal so' LE. Cada lado chama so' o seu metodo
e a role do outro lado nem teria permissao.

O cliente e' o `boto3` da imagem (extra `anthropic[bedrock]`), importado so' por quem liga os anexos, com as
chamadas sincronas em `asyncio.to_thread` (mesma postura de `recipient_custody.py`).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from maezo.tools.mcp_whatsapp.media import TAMANHO_MAXIMO_BYTES, mime_aceito, mime_limpo

PREFIXO: Final[str] = "reembolso"
_REF: Final[re.Pattern[str]] = re.compile(r"^rdoc_[0-9a-f]{32}$")
_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


class AnexoIndisponivelError(Exception):
    """O anexo nao pode ser gravado/lido. A mensagem nunca carrega chave, bucket ou conteudo."""


@dataclass(frozen=True, slots=True)
class AnexoGuardado:
    document_ref: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Anexo:
    conteudo: bytes = field(repr=False)
    mime: str


class ClienteS3(Protocol):
    """O pedaco do cliente `boto3` S3 que usamos (sincrono)."""

    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> Any: ...


def ref_valida(valor: object) -> bool:
    return isinstance(valor, str) and _REF.match(valor) is not None


def sha256_valido(valor: object) -> bool:
    return isinstance(valor, str) and _SHA256.match(valor) is not None


def nova_ref() -> str:
    return f"rdoc_{secrets.token_hex(16)}"


class CustodiaDeAnexosS3:
    """Grava (receptor) e le (portal) anexos do reembolso. `cliente` e' injetavel para teste."""

    def __init__(
        self,
        *,
        bucket: str,
        kms_key_arn: str | None,
        tenant: str,
        region: str | None = None,
        cliente: ClienteS3 | None = None,
    ) -> None:
        if not bucket or not tenant:
            raise AnexoIndisponivelError("reembolso_anexos: bucket e tenant sao obrigatorios")
        self._bucket = bucket
        self._kms = kms_key_arn
        self._tenant = tenant
        self._region = region
        self._cliente = cliente

    def __repr__(self) -> str:  # nada de bucket nem chave em log
        return "CustodiaDeAnexosS3(...)"

    def _s3(self) -> ClienteS3:
        if self._cliente is None:
            import boto3  # type: ignore[import-untyped]  # lazy: so' quem liga os anexos paga

            self._cliente = (
                boto3.client("s3", region_name=self._region) if self._region else boto3.client("s3")
            )
        return self._cliente

    def _chave(self, document_ref: str) -> str:
        return f"{PREFIXO}/{self._tenant}/{document_ref}"

    async def guardar_anexo(self, *, conversation_id: str, mime: str, conteudo: bytes) -> AnexoGuardado:
        """Grava UM anexo. Recusa mime fora da lista, vazio ou acima do teto ANTES de qualquer chamada."""
        if not self._kms:
            # Quem so' le (o portal) nao tem a chave de gravar: gravar daqui e' defeito de composicao.
            raise AnexoIndisponivelError("reembolso_anexos: sem chave KMS de gravacao")
        if not conversation_id.startswith(f"wa:{self._tenant}:"):
            raise AnexoIndisponivelError("reembolso_anexos: conversa de outro tenant")
        if not mime_aceito(mime) or not conteudo or len(conteudo) > TAMANHO_MAXIMO_BYTES:
            raise AnexoIndisponivelError("reembolso_anexos: arquivo fora da forma")
        ref = nova_ref()
        digest = hashlib.sha256(conteudo).hexdigest()
        argumentos = {
            "Bucket": self._bucket,
            "Key": self._chave(ref),
            "Body": conteudo,
            "ContentType": mime_limpo(mime),
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self._kms,
            "ChecksumAlgorithm": "SHA256",
            "Metadata": {"sha256": digest, "conversa": conversation_id, "finalidade": "reembolso-anexo"},
        }
        try:
            await asyncio.to_thread(self._s3().put_object, **argumentos)
        except Exception:
            # Nada da excecao original sai: ela pode trazer bucket/chave.
            raise AnexoIndisponivelError("reembolso_anexos: falha ao gravar") from None
        return AnexoGuardado(document_ref=ref, sha256=digest)

    async def abrir(self, document_ref: str, *, sha256_esperado: str) -> Anexo:
        """Le UM anexo e confere o sha256 contra o que o processo registrou. Qualquer divergencia recusa."""
        if not ref_valida(document_ref) or not sha256_valido(sha256_esperado):
            raise AnexoIndisponivelError("reembolso_anexos: referencia fora da forma")
        try:
            resposta = await asyncio.to_thread(
                self._s3().get_object, Bucket=self._bucket, Key=self._chave(document_ref)
            )
            corpo = resposta["Body"]
            conteudo = await asyncio.to_thread(corpo.read, TAMANHO_MAXIMO_BYTES + 1)
            mime = str(resposta.get("ContentType") or "")
        except Exception:
            raise AnexoIndisponivelError("reembolso_anexos: falha ao ler") from None
        if (
            not conteudo
            or len(conteudo) > TAMANHO_MAXIMO_BYTES
            or not mime_aceito(mime)
            or not secrets.compare_digest(hashlib.sha256(conteudo).hexdigest(), sha256_esperado)
        ):
            raise AnexoIndisponivelError("reembolso_anexos: conteudo nao confere com o registrado")
        return Anexo(conteudo=conteudo, mime=mime_limpo(mime))
