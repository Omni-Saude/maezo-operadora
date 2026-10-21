"""Persistencia do lembrete: o que ja' foi lembrado (idempotencia) e quem pediu PARAR (opt-out).

Duas tabelas pequenas (migration 0027), por `(tenant, conversation_id)`. So' o `conversation_id`
pseudonimo (keyed), a competencia `AAAA-MM`, carimbos e o canal de envio: nunca telefone, CPF, valor, nome ou
texto. O tenant entra na PK, em todo `WHERE` e num CHECK; conversa de outro tenant e' recusada AQUI.

Falha de banco PROPAGA. O job nao envia sem conseguir gravar a reserva ANTES (reserva -> envio -> libera se
falhou): o pior caso de uma queda entre a reserva e o envio e' um lembrete a menos, nunca um a mais.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

TABELA_ENVIADO: Final[str] = "lembrete_cobranca_enviado"
TABELA_OPTOUT: Final[str] = "lembrete_cobranca_optout"

_RESERVAR_SQL: Final[str] = f"""
INSERT INTO {TABELA_ENVIADO} (tenant, conversation_id, competencia, reservado_em, canal_envio)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (tenant, conversation_id, competencia) DO NOTHING
"""
_LIBERAR_SQL: Final[str] = (
    f"DELETE FROM {TABELA_ENVIADO} WHERE tenant = $1 AND conversation_id = $2 AND competencia = $3"
)
_JA_SQL: Final[str] = (
    f"SELECT 1 FROM {TABELA_ENVIADO} WHERE tenant = $1 AND conversation_id = $2 AND competencia = $3"
)
_OPTOUT_INSERT_SQL: Final[str] = f"""
INSERT INTO {TABELA_OPTOUT} (tenant, conversation_id, optout_em) VALUES ($1, $2, $3)
ON CONFLICT (tenant, conversation_id) DO NOTHING
"""
_OPTOUT_DELETE_SQL: Final[str] = f"DELETE FROM {TABELA_OPTOUT} WHERE tenant = $1 AND conversation_id = $2"
_OPTOUT_TODOS_SQL: Final[str] = f"SELECT conversation_id FROM {TABELA_OPTOUT} WHERE tenant = $1"


class LembreteStoreError(RuntimeError):
    """Falha do store. A mensagem e' sempre um token de classe, nunca dado de beneficiario."""


class LembreteStore(Protocol):
    async def reservar(
        self, conversation_id: str, competencia: str, agora: datetime, canal_envio: str
    ) -> bool:
        """`True` se a reserva e' NOVA (pode enviar); `False` se ja' havia (nao envia)."""
        ...

    async def liberar(self, conversation_id: str, competencia: str) -> None: ...

    async def ja_lembrado(self, conversation_id: str, competencia: str) -> bool: ...

    async def registrar_optout(self, conversation_id: str, agora: datetime) -> None: ...

    async def remover_optout(self, conversation_id: str) -> None: ...

    async def optouts(self) -> frozenset[str]: ...


class PostgresLembreteStore:
    """`LembreteStore` sobre asyncpg, por tenant (`search_path` pinado em todo acquire)."""

    def __init__(self, *, dsn: str, tenant: str, pool: Any = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._prefixo = f"wa:{tenant}:"
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    def __repr__(self) -> str:
        return "PostgresLembreteStore(<redacted>)"

    def _conferir(self, conversation_id: str) -> None:
        if not conversation_id.startswith(self._prefixo):
            raise LembreteStoreError("lembrete: conversa de outro tenant")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def reservar(
        self, conversation_id: str, competencia: str, agora: datetime, canal_envio: str
    ) -> bool:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                _RESERVAR_SQL, self._tenant, conversation_id, competencia, agora, canal_envio
            )
        return str(status).rsplit(" ", 1)[-1] == "1"

    async def liberar(self, conversation_id: str, competencia: str) -> None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_LIBERAR_SQL, self._tenant, conversation_id, competencia)

    async def ja_lembrado(self, conversation_id: str, competencia: str) -> bool:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_JA_SQL, self._tenant, conversation_id, competencia)
        return row is not None

    async def registrar_optout(self, conversation_id: str, agora: datetime) -> None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_OPTOUT_INSERT_SQL, self._tenant, conversation_id, agora)

    async def remover_optout(self, conversation_id: str) -> None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_OPTOUT_DELETE_SQL, self._tenant, conversation_id)

    async def optouts(self) -> frozenset[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_OPTOUT_TODOS_SQL, self._tenant)
        return frozenset(str(r["conversation_id"]) for r in rows)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
