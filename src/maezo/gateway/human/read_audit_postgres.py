"""Adaptador Postgres do registro de leitura (DL-0150): a MESMA cadeia da conclusao.

Grava pelo `PostgresAuditSink` do tenant, sobre o pool emprestado pela composicao — o mesmo pool,
tabela, lock e algoritmo de `completion_engine.py::PostgresCompletionAudit`. Fail-closed: qualquer
falha levanta (`AuditPersistenceError` ou recusa tipada) e o chamador recusa a leitura.
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]

from maezo.gateway.audit_postgres import PostgresAuditSink

from .errors import GatewayRefusalError
from .models import Scope
from .read_audit import ReadAccessAuditSink, ReadAccessEntry, read_access_record

__all__ = ["PostgresReadAccessAudit"]


class PostgresReadAccessAudit(ReadAccessAuditSink):
    """O registro de leitura, gravado pelo sink que ja' e' dono da cadeia deste tenant.

    Mesma construcao de `PostgresCompletionAudit`: DSN vazio mais o pool do tenant emprestado pela
    composicao, entao nenhuma connection string e' lida aqui e nenhum pool e' criado.
    """

    def __init__(self, *, scope: Scope, pool: asyncpg.Pool) -> None:
        self.scope = Scope.model_validate(scope)
        self._sink = PostgresAuditSink("", self.scope.tenant, pool=pool)

    async def record(self, entry: ReadAccessEntry) -> str:
        if type(entry) is not ReadAccessEntry or entry.scope != self.scope:
            raise GatewayRefusalError("admission_unavailable")
        return await self._sink.emit(read_access_record(entry, workload_ref=self.scope.workload_ref))
