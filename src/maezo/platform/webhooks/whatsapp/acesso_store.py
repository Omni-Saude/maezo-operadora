"""Persistencia do acesso do beneficiario: `conversa_acesso_beneficiario` (migration 0022, DL-0083).

Uma linha por `(tenant, conversation_id)`, gravada por UPSERT. So' estado fechado, contadores, carimbos,
versao/sha256 do texto de consentimento e a referencia PSEUDONIMA da AMH: nunca telefone, CPF, nascimento,
nome ou texto digitado (a migration documenta e os testes fixam o que a tabela NAO tem). O tenant entra na PK,
em todo `WHERE` e num CHECK que amarra o prefixo do `conversation_id`; uma conversa de outro tenant e'
recusada AQUI, antes de qualquer SQL.

Falha de banco PROPAGA (nao vira "sem estado"): o despachante nao pode tratar um banco fora como uma conversa
nova, que reabriria o pedido de consentimento e zeraria o contador de tentativas. O turno falha, a Meta
reentrega e nenhum agente roda.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

from .acesso import EstadoAcesso

TABELA: Final[str] = "conversa_acesso_beneficiario"

_COLUNAS: Final[str] = (
    "conversation_id, estado, tentativas, texto_versao, texto_sha256, consentido_em, revogado_em, "
    "portable_subject_ref, consent_ref, consentimento_pendente_gravacao, revogacao_pendente, "
    "verificado_em, expira_em, bloqueado_ate, ultima_mensagem_em, regravacoes_falhas, falhas_janela, "
    "janela_inicio"
)
_SELECT_SQL: Final[str] = f"SELECT {_COLUNAS} FROM {TABELA} WHERE tenant = $1 AND conversation_id = $2"
_UPSERT_SQL: Final[str] = f"""
INSERT INTO {TABELA} (
    tenant, conversation_id, estado, tentativas, texto_versao, texto_sha256, consentido_em, revogado_em,
    portable_subject_ref, consent_ref, consentimento_pendente_gravacao, revogacao_pendente,
    verificado_em, expira_em, bloqueado_ate, ultima_mensagem_em, regravacoes_falhas, falhas_janela,
    janela_inicio
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
ON CONFLICT (tenant, conversation_id) DO UPDATE SET
    estado = EXCLUDED.estado,
    tentativas = EXCLUDED.tentativas,
    texto_versao = EXCLUDED.texto_versao,
    texto_sha256 = EXCLUDED.texto_sha256,
    consentido_em = EXCLUDED.consentido_em,
    revogado_em = EXCLUDED.revogado_em,
    portable_subject_ref = EXCLUDED.portable_subject_ref,
    consent_ref = EXCLUDED.consent_ref,
    consentimento_pendente_gravacao = EXCLUDED.consentimento_pendente_gravacao,
    revogacao_pendente = EXCLUDED.revogacao_pendente,
    verificado_em = EXCLUDED.verificado_em,
    expira_em = EXCLUDED.expira_em,
    bloqueado_ate = EXCLUDED.bloqueado_ate,
    ultima_mensagem_em = EXCLUDED.ultima_mensagem_em,
    regravacoes_falhas = EXCLUDED.regravacoes_falhas,
    falhas_janela = EXCLUDED.falhas_janela,
    janela_inicio = EXCLUDED.janela_inicio
"""


class AcessoStoreError(RuntimeError):
    """Falha do store. A mensagem e' sempre um token de classe, nunca dado de beneficiario."""


def _opcional(valor: Any) -> datetime | None:
    return valor if isinstance(valor, datetime) else None


class PostgresAcessoStore:
    """`AcessoStore` sobre asyncpg, por tenant (`search_path` pinado em todo acquire)."""

    def __init__(self, *, dsn: str, tenant: str, pool: Any = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._prefixo = f"wa:{tenant}:"
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    def __repr__(self) -> str:
        return "PostgresAcessoStore(<redacted>)"

    def _conferir(self, conversation_id: str) -> None:
        if not conversation_id.startswith(self._prefixo):
            raise AcessoStoreError("acesso: conversa de outro tenant")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def ler(self, conversation_id: str) -> EstadoAcesso | None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, self._tenant, conversation_id)
        if row is None:
            return None
        return EstadoAcesso(
            conversation_id=str(row["conversation_id"]),
            estado=str(row["estado"]),
            tentativas=int(row["tentativas"]),
            texto_versao=row["texto_versao"],
            texto_sha256=row["texto_sha256"],
            consentido_em=_opcional(row["consentido_em"]),
            revogado_em=_opcional(row["revogado_em"]),
            portable_subject_ref=row["portable_subject_ref"],
            consent_ref=row["consent_ref"],
            consentimento_pendente_gravacao=bool(row["consentimento_pendente_gravacao"]),
            revogacao_pendente=bool(row["revogacao_pendente"]),
            verificado_em=_opcional(row["verificado_em"]),
            expira_em=_opcional(row["expira_em"]),
            bloqueado_ate=_opcional(row["bloqueado_ate"]),
            ultima_mensagem_em=row["ultima_mensagem_em"],
            regravacoes_falhas=int(row["regravacoes_falhas"]),
            falhas_janela=int(row["falhas_janela"]),
            janela_inicio=_opcional(row["janela_inicio"]),
        )

    async def gravar(self, estado: EstadoAcesso) -> None:
        self._conferir(estado.conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                self._tenant,
                estado.conversation_id,
                estado.estado,
                estado.tentativas,
                estado.texto_versao,
                estado.texto_sha256,
                estado.consentido_em,
                estado.revogado_em,
                estado.portable_subject_ref,
                estado.consent_ref,
                estado.consentimento_pendente_gravacao,
                estado.revogacao_pendente,
                estado.verificado_em,
                estado.expira_em,
                estado.bloqueado_ate,
                estado.ultima_mensagem_em,
                estado.regravacoes_falhas,
                estado.falhas_janela,
                estado.janela_inicio,
            )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
