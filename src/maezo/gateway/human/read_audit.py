"""Registro de LEITURA na cadeia de auditoria do tenant (DL-0150; pendencia (a) do DL-0050).

O QUE FALTAVA. A conclusao interina grava `portal_direct_completion.intent/result` na cadeia do
tenant (`completion_engine.py::PostgresCompletionAudit`); as LEITURAS de detalhe nao gravavam nada.
Quem abriu o contexto de um escalonamento (com o resumo, que e' PHI) ou o detalhe de um caso do
colaborador nao deixava rastro. Este modulo e' esse rastro, e nada alem dele.

Este modulo e' PURO (tipos, porta e montagem do registro); o adaptador Postgres fica em
`read_audit_postgres.py`, para o gateway nao importar driver de banco.

O MESMO MECANISMO, NAO UM SEGUNDO. O registro vai para a MESMA tabela (`audit_chain`), pelo MESMO
sink (`PostgresAuditSink`), sobre o MESMO pool do tenant que a conclusao e o outbox humano usam.
Nao ha tabela nova, migracao nova, fila em memoria nem fallback: `PostgresAuditSink.emit` e'
fail-closed (ADR-0027), e quem chama trata a falha como recusa da leitura (fail-closed tambem).

SEM PHI POR CONSTRUCAO. O registro so' carrega ids tecnicos (tarefa/caso), as referencias
pseudonimas que o portal ja' usa (`principal_ref`, `session_ref`, revisao de membership), o
codigo fechado da leitura e o instante. Nenhum campo do que foi lido entra: nem resumo, nem
motivo, nem guia, nem sujeito do IdP. `READ_AUDIT_DETAIL_FIELDS` e' a lista FECHADA de chaves de
`details`, e `read_access_record` recusa montar qualquer outra.

UMA LEITURA, UM REGISTRO. `emit`, nao `emit_once`: duas aberturas do mesmo caso sao dois
acessos, e cada uma tem de aparecer. O registro e' gravado DEPOIS de toda a autorizacao e de
toda a leitura remota, e ANTES de a resposta sair; leitura recusada nao grava registro de acesso.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Literal

from maezo.gateway.audit import AuditRecord
from maezo.portal.contracts.models import HumanPrincipal

from .errors import GatewayRefusalError
from .models import Scope

__all__ = [
    "READ_AUDIT_ACTION_PREFIX",
    "READ_AUDIT_DECISION_REF",
    "READ_AUDIT_DETAIL_FIELDS",
    "READ_RESOURCE_KINDS",
    "ReadAccessAuditSink",
    "ReadAccessEntry",
    "ReadKind",
    "read_access_record",
]

#: A linha do decision log que autoriza este registro.
READ_AUDIT_DECISION_REF: Final = "DL-0150"

#: `action` = `portal_read.<read_kind>`. Separa estas linhas das de `portal_direct_completion.*`
#: e `human_command.*` na mesma cadeia.
READ_AUDIT_ACTION_PREFIX: Final = "portal_read"

#: O codigo FECHADO de cada leitura registrada. Novo valor = nova decisao, nao um parametro.
ReadKind = Literal["task_context", "staff_case_detail"]

#: Que tipo de recurso cada leitura identifica em `resource_ref`.
READ_RESOURCE_KINDS: Final[Mapping[str, str]] = MappingProxyType(
    {"task_context": "task", "staff_case_detail": "case"}
)

#: As UNICAS chaves de `details` que um registro de leitura pode ter. Nenhuma delas carrega o
#: conteudo lido; o teste de lista permitida compara contra este conjunto.
READ_AUDIT_DETAIL_FIELDS: Final = frozenset(
    {
        "decision_ref",
        "principal_kind",
        "principal_ref",
        "session_ref",
        "membership_revision",
        "workload_ref",
        "operation",
        "read_kind",
        "resource_kind",
        "resource_ref",
        "read_at",
    }
)


@dataclass(frozen=True, slots=True)
class ReadAccessEntry:
    """QUEM leu O QUE e QUANDO. Pseudonimo por construcao: so' referencias opacas e codigos.

    `principal_ref`/`session_ref` sao as referencias opacas do lado do servidor (as mesmas de
    `CompletionAuditEntry`), nunca o `subject` do IdP nem e-mail. `resource_ref` e' o id tecnico
    da tarefa ou do caso que a rota ja' recebeu na URL.
    """

    scope: Scope
    read_kind: ReadKind
    resource_ref: str
    principal_ref: str
    session_ref: str
    membership_revision: int
    read_at: datetime

    @classmethod
    def of(
        cls,
        *,
        scope: Scope,
        read_kind: ReadKind,
        resource_ref: str,
        principal: HumanPrincipal,
        read_at: datetime | None = None,
    ) -> ReadAccessEntry:
        return cls(
            scope=scope,
            read_kind=read_kind,
            resource_ref=resource_ref,
            principal_ref=principal.principal_ref,
            session_ref=principal.session_ref,
            membership_revision=principal.membership_revision,
            read_at=read_at or datetime.now(UTC),
        )


def read_access_record(entry: ReadAccessEntry, *, workload_ref: str) -> AuditRecord:
    """A tupla ADR-0007 de UMA leitura. `decision="ALLOW"`: a leitura foi autorizada e entregue."""
    if (
        type(entry) is not ReadAccessEntry
        or entry.read_kind not in READ_RESOURCE_KINDS
        or entry.read_at.tzinfo is None
    ):
        raise GatewayRefusalError("admission_unavailable")
    details: dict[str, object] = {
        "decision_ref": READ_AUDIT_DECISION_REF,
        "principal_kind": "human",
        "principal_ref": entry.principal_ref,
        "session_ref": entry.session_ref,
        "membership_revision": entry.membership_revision,
        "workload_ref": workload_ref,
        "operation": "read",
        "read_kind": entry.read_kind,
        "resource_kind": READ_RESOURCE_KINDS[entry.read_kind],
        "resource_ref": entry.resource_ref,
        "read_at": entry.read_at.astimezone(UTC).isoformat(),
    }
    if set(details) != READ_AUDIT_DETAIL_FIELDS:  # pragma: no cover - cinto da lista fechada
        raise GatewayRefusalError("admission_unavailable")
    return AuditRecord(
        agent_id=workload_ref,
        tenant_id=entry.scope.tenant,
        agent_version="portal-read-audit.v1",
        action=f"{READ_AUDIT_ACTION_PREFIX}.{entry.read_kind}",
        decision="ALLOW",
        details=details,
        timestamp=entry.read_at,
    )


class ReadAccessAuditSink(ABC):
    """Acrescenta UM registro de leitura a cadeia do tenant, ou levanta.

    TEM de ser a cadeia que `PostgresAuditSink` mantem — sem segundo algoritmo, sem tabela
    paralela e sem fallback em memoria em producao. Uma falha levanta; quem chama recusa a leitura.
    """

    scope: Scope

    @abstractmethod
    async def record(self, entry: ReadAccessEntry) -> str:
        """Devolve o `record_hash` do registro gravado."""
        raise NotImplementedError
