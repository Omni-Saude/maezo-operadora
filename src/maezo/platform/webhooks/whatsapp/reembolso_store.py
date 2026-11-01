"""Armazenamento da coleta do pedido de reembolso (DL-0159/DL-0160; tabela `reembolso_coleta`, 0031).

Uma linha por `(tenant, conversation_id)`. Escrita por compare-and-set (`revisao`): `INSERT ... ON CONFLICT
DO NOTHING` na primeira vez, `UPDATE ... WHERE revisao = :esperada` depois. Quem perde a corrida rele' e
refaz o turno (a maquina de estados nao chama modelo). Nada de texto livre nem de telefone: so' enums
fechados, data, valor em centavos e `document_ref` opaco (o arquivo vive na Zona PHI, ADR-0006).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.domain.reembolso.pedido import Documento, PedidoReembolso
from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

TABELA: Final[str] = "reembolso_coleta"


class ColetaReembolsoError(RuntimeError):
    """Falha de armazenamento ou conversa de outro tenant."""


@dataclass(frozen=True, slots=True)
class ColetaGravada:
    """A linha lida: o pedido, o protocolo (so' depois de aberto) e a versao para o CAS."""

    pedido: PedidoReembolso
    protocolo: str | None
    revisao: int
    atualizado_em: datetime
    expira_em: datetime

    def vencida(self, agora: datetime) -> bool:
        return self.expira_em <= agora


class ColetaReembolsoStore(Protocol):
    async def ler(self, conversation_id: str) -> ColetaGravada | None: ...

    async def gravar(
        self,
        conversation_id: str,
        pedido: PedidoReembolso,
        *,
        protocolo: str | None,
        agora: datetime,
        expira_em: datetime,
        revisao_esperada: int | None,
    ) -> bool:
        """`True` = gravou; `False` = perdeu o CAS (outra mensagem gravou antes): rele' e refaz."""
        ...

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int: ...


_COLUNAS: Final[str] = (
    "conversation_id, etapa, relacao_com_titular, categoria_procedimento, tipo_reembolso, motivo_nao_rede, "
    "data_atendimento, valor_solicitado_cents, perguntou_terminou, documentos, protocolo, resposta_texto, "
    "revisao, criado_em, atualizado_em, expira_em"
)
_SELECT_SQL: Final[str] = f"SELECT {_COLUNAS} FROM {TABELA} WHERE tenant = $1 AND conversation_id = $2"
_INSERT_SQL: Final[str] = (
    f"INSERT INTO {TABELA} (tenant, {_COLUNAS}) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, 0, $14, $14, $15) "
    "ON CONFLICT (tenant, conversation_id) DO NOTHING"
)
_UPDATE_SQL: Final[str] = (
    f"UPDATE {TABELA} SET etapa = $3, relacao_com_titular = $4, categoria_procedimento = $5, "
    "tipo_reembolso = $6, motivo_nao_rede = $7, data_atendimento = $8, valor_solicitado_cents = $9, "
    "perguntou_terminou = $10, documentos = $11::jsonb, protocolo = $12, resposta_texto = $13, "
    "revisao = revisao + 1, atualizado_em = $14, expira_em = $15 "
    "WHERE tenant = $1 AND conversation_id = $2 AND revisao = $16"
)
_PURGE_SQL: Final[str] = (
    f"DELETE FROM {TABELA} WHERE tenant = $1 AND conversation_id IN ("
    f"SELECT conversation_id FROM {TABELA} WHERE tenant = $1 AND expira_em < $2 "
    "ORDER BY expira_em LIMIT $3)"
)


def _linhas_afetadas(status: Any) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


def _documentos_json(pedido: PedidoReembolso) -> str:
    return json.dumps(
        [{"tipo": d.tipo, "document_ref": d.document_ref, "sha256": d.sha256} for d in pedido.documentos]
    )


def _pedido_da_linha(row: Any) -> PedidoReembolso:
    bruto = row["documentos"]
    itens = json.loads(bruto) if isinstance(bruto, str) else (bruto or [])
    documentos = tuple(
        Documento(tipo=str(i["tipo"]), document_ref=str(i["document_ref"]), sha256=str(i.get("sha256") or ""))
        for i in itens
    )
    data: date | None = row["data_atendimento"]
    return PedidoReembolso(
        etapa=row["etapa"],
        relacao_com_titular=row["relacao_com_titular"],
        categoria_procedimento=row["categoria_procedimento"],
        tipo_reembolso=row["tipo_reembolso"],
        data_atendimento=data.isoformat() if data else "",
        valor_solicitado_cents=int(row["valor_solicitado_cents"]),
        motivo_nao_rede=row["motivo_nao_rede"],
        perguntou_terminou=bool(row["perguntou_terminou"]),
        documentos=documentos,
        resposta_texto=str(row["resposta_texto"] or ""),
    )


def _valores(
    conversation_id: str, pedido: PedidoReembolso, protocolo: str | None, agora: datetime, expira_em: datetime
) -> tuple[Any, ...]:
    return (
        conversation_id,
        pedido.etapa,
        pedido.relacao_com_titular,
        pedido.categoria_procedimento,
        pedido.tipo_reembolso,
        pedido.motivo_nao_rede,
        date.fromisoformat(pedido.data_atendimento) if pedido.data_atendimento else None,
        pedido.valor_solicitado_cents,
        pedido.perguntou_terminou,
        _documentos_json(pedido),
        protocolo,
        pedido.resposta_texto,
        agora,
        expira_em,
    )


class PostgresColetaReembolsoStore:
    """`ColetaReembolsoStore` sobre asyncpg, por tenant (`search_path` pinado em todo acquire).

    O tenant entra na PK, em todo `WHERE` e num CHECK que amarra o prefixo do `conversation_id` ao proprio
    tenant. Uma conversa de outro tenant e' recusada AQUI, antes de qualquer SQL.
    """

    def __init__(self, *, dsn: str, tenant: str, pool: Any = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._prefixo = f"wa:{tenant}:"
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    def _conferir(self, conversation_id: str) -> None:
        if not conversation_id.startswith(self._prefixo):
            raise ColetaReembolsoError("coleta de reembolso: conversa de outro tenant")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def ler(self, conversation_id: str) -> ColetaGravada | None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, self._tenant, conversation_id)
        if row is None:
            return None
        return ColetaGravada(
            pedido=_pedido_da_linha(row),
            protocolo=row["protocolo"],
            revisao=int(row["revisao"]),
            atualizado_em=row["atualizado_em"],
            expira_em=row["expira_em"],
        )

    async def gravar(
        self,
        conversation_id: str,
        pedido: PedidoReembolso,
        *,
        protocolo: str | None,
        agora: datetime,
        expira_em: datetime,
        revisao_esperada: int | None,
    ) -> bool:
        self._conferir(conversation_id)
        valores = _valores(conversation_id, pedido, protocolo, agora, expira_em)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if revisao_esperada is None:
                status = await conn.execute(_INSERT_SQL, self._tenant, *valores)
            else:
                status = await conn.execute(_UPDATE_SQL, self._tenant, *valores, revisao_esperada)
        return _linhas_afetadas(status) == 1

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(_PURGE_SQL, self._tenant, antes_de, limite)
        return _linhas_afetadas(status)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class MemoriaColetaReembolsoStore:
    """Mesma semantica de CAS, em memoria (testes e simulador). Nunca em producao."""

    def __init__(self, *, tenant: str = "amh") -> None:
        self._tenant = tenant
        self._linhas: dict[str, ColetaGravada] = {}

    async def ler(self, conversation_id: str) -> ColetaGravada | None:
        if not conversation_id.startswith(f"wa:{self._tenant}:"):
            raise ColetaReembolsoError("coleta de reembolso: conversa de outro tenant")
        return self._linhas.get(conversation_id)

    async def gravar(
        self,
        conversation_id: str,
        pedido: PedidoReembolso,
        *,
        protocolo: str | None,
        agora: datetime,
        expira_em: datetime,
        revisao_esperada: int | None,
    ) -> bool:
        if not conversation_id.startswith(f"wa:{self._tenant}:"):
            raise ColetaReembolsoError("coleta de reembolso: conversa de outro tenant")
        if protocolo is not None and pedido.etapa not in ("aberto", "resposta"):
            raise ColetaReembolsoError("coleta de reembolso: protocolo so' na etapa aberto")
        atual = self._linhas.get(conversation_id)
        if revisao_esperada is None:
            if atual is not None:
                return False
            revisao = 0
        else:
            if atual is None or atual.revisao != revisao_esperada:
                return False
            revisao = atual.revisao + 1
        self._linhas[conversation_id] = ColetaGravada(
            pedido=pedido, protocolo=protocolo, revisao=revisao, atualizado_em=agora, expira_em=expira_em
        )
        return True

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int:
        vencidas = sorted(
            (c for c, linha in self._linhas.items() if linha.expira_em < antes_de),
            key=lambda c: self._linhas[c].expira_em,
        )[:limite]
        for conversa in vencidas:
            del self._linhas[conversa]
        return len(vencidas)


def agora_utc() -> datetime:
    return datetime.now(UTC)
