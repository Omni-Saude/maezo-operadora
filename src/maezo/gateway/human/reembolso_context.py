"""INTERIM reembolso-context read (DL-0162): o que o analista/financeiro precisa ver na tarefa do reembolso.

MESMO ATALHO DA DL-0050, pelo mesmo motivo e atras do mesmo portao: o modelo de leitura assinado nao carrega
evidencia de reembolso, e estende-lo e' trabalho de plugin Java + grants + vetores assinados. Aqui se
leem, pelo
REST do motor e pelo MESMO cliente fixo do contexto do escalonamento (`EngineRestEscalationContext`), so' as
variaveis de NOME FIXO abaixo — nunca "todas as variaveis da instancia" (varias sao Zona PHI) — de uma tarefa
que o gateway JA' provou que o usuario pode ler.

O que sai: os dados FECHADOS do pedido (enums, data, valores em centavos, protocolo, dias desde o atendimento,
a decisao ja' tomada quando a tarefa e' o pagamento) e a lista de anexos `{tipo, document_ref, sha256}`.
Nenhum
texto livre da beneficiaria (a coleta nao guarda nenhum) e nenhum texto do analista.

Os BYTES de um anexo saem por outra leitura (`HumanGateway.read_reembolso_anexo`), que exige que a referencia
esteja nesta lista e confere o sha256 registrado contra o objeto do S3 (ADR-0020, principio do `input_hash`).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from .errors import GatewayRefusalError

DECISION_REF: Final = "DL-0162"
REEMBOLSO_PROCESS_KEY: Final = "SP-OP-REEMBOLSO-001"

#: As tarefas humanas do reembolso (as do catalogo). Outra tarefa e' recusada.
REEMBOLSO_TASKS: Final[frozenset[str]] = frozenset(
    {
        "UT_AnaliseReembolso",
        "UT_CoordenacaoReembolso",
        "UT_RevisaoAuditorMedico",
        "UT_DecidirPendenciaExpirada",
        "UT_RegistrarPagamento",
    }
)

_ENUMS: Final[dict[str, frozenset[str]]] = {
    "relacao_com_titular": frozenset({"titular", "dependente"}),
    "categoria_procedimento": frozenset({"consulta", "exame", "terapia"}),
    "tipo_reembolso": frozenset(
        {"livre_escolha", "fora_rede", "urgencia_emergencia", "indisponibilidade_rede"}
    ),
    "motivo_nao_rede": frozenset({"urgencia", "sem_prestador_no_prazo", "preferencia_fora_rede"}),
    "decisao_reembolso": frozenset(
        {"APROVAR", "APROVAR_PARCIAL", "NEGAR", "SOLICITAR_INFO", "SOLICITAR_AUDITOR"}
    ),
}
_PROTOCOLO: Final = re.compile(r"^REE-[0-9A-F]{4}-[0-9A-F]{4}$")
_DATA: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REF: Final = re.compile(r"^rdoc_[0-9a-f]{32}$")
_SHA: Final = re.compile(r"^[0-9a-f]{64}$")
_TIPOS_DOC: Final = frozenset({"pedido_medico", "nota_fiscal", "complemento"})
#: Teto da resposta da beneficiaria a um pedido de informacao (a coleta ja' limita a 1000 depois do scrub).
MAX_RESPOSTA: Final = 1000
#: Teto de anexos lidos (10 por tipo na coleta + os da resposta ao pedido de informacao).
MAX_ANEXOS: Final = 60


@dataclass(frozen=True, slots=True)
class AnexoDoPedido:
    tipo: str
    document_ref: str
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class ReembolsoContexto:
    """O que o analista/financeiro ve. `repr=False`: nada disto vai a log."""

    task_definition_key: str
    protocolo: str | None
    relacao_com_titular: str | None
    categoria_procedimento: str | None
    tipo_reembolso: str | None
    motivo_nao_rede: str | None
    data_atendimento: str | None
    data_solicitacao: str | None
    dias_desde_atendimento: int | None
    valor_solicitado_cents: int | None
    decisao_reembolso: str | None
    valor_reembolso_aprovado_cents: int | None
    anexos: tuple[AnexoDoPedido, ...] = field(default_factory=tuple)
    #: DL-0162: o resultado da DMN informativa de prazo (nao roteia nada) e a ultima resposta da
    #: beneficiaria a
    #: um pedido de informacao (texto ja' pelo scrub de identificadores; PHI, so' exibido).
    situacao_prazo: str | None = None
    resposta_beneficiario: str | None = None

    def anexo(self, document_ref: str) -> AnexoDoPedido | None:
        return next((a for a in self.anexos if a.document_ref == document_ref), None)


class LeitorDeAnexos(Protocol):
    """`gateway/reembolso_anexos.py::CustodiaDeAnexosS3.abrir` satisfaz esta forma."""

    async def abrir(self, document_ref: str, *, sha256_esperado: str) -> Any: ...


class EngineJsonReader(Protocol):
    """O pedaco do cliente fixo do motor que este modulo usa (`EngineRestEscalationContext`)."""

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> Any: ...

    def same_tenant(self, payload: Any) -> None: ...


_SEGMENT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _segment(value: object) -> str:
    if not isinstance(value, str) or _SEGMENT.fullmatch(value) is None:
        raise GatewayRefusalError("task_unavailable")
    return value


def _enum(nome: str, valor: object) -> str | None:
    return valor if isinstance(valor, str) and valor in _ENUMS[nome] else None


def _data(valor: object) -> str | None:
    return valor if isinstance(valor, str) and _DATA.match(valor) else None


def _centavos(valor: object) -> int | None:
    return valor if type(valor) is int and 0 <= valor <= 10**12 else None


def parse_anexos(valor: object) -> tuple[AnexoDoPedido, ...]:
    """`documentos_refs` (Json no motor) -> anexos validos. Item fora da forma e' DESCARTADO (nunca
    servido)."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return ()
    if not isinstance(valor, list):
        return ()
    anexos: list[AnexoDoPedido] = []
    for item in valor[:MAX_ANEXOS]:
        if not isinstance(item, dict):
            continue
        tipo, ref, sha = item.get("tipo"), item.get("document_ref"), item.get("sha256")
        if (
            isinstance(tipo, str)
            and tipo in _TIPOS_DOC
            and isinstance(ref, str)
            and _REF.match(ref)
            and isinstance(sha, str)
            and _SHA.match(sha)
        ):
            anexos.append(AnexoDoPedido(tipo=tipo, document_ref=ref, sha256=sha))
    return tuple(anexos)


_VARIAVEIS: Final[dict[str, frozenset[str]]] = {
    "protocolo_reembolso": frozenset({"String"}),
    "relacao_com_titular": frozenset({"String"}),
    "categoria_procedimento": frozenset({"String"}),
    "tipo_reembolso": frozenset({"String"}),
    "motivo_nao_rede": frozenset({"String"}),
    "data_atendimento": frozenset({"String"}),
    "data_solicitacao": frozenset({"String"}),
    "dias_desde_atendimento": frozenset({"Integer", "Long"}),
    "valor_solicitado_cents": frozenset({"Integer", "Long"}),
    "decisao_reembolso": frozenset({"String"}),
    "valor_reembolso_aprovado_cents": frozenset({"Integer", "Long"}),
    "documentos_refs": frozenset({"Json", "String"}),
    "documentos_adicionais_refs": frozenset({"Json", "String"}),
    "situacao_prazo": frozenset({"String"}),
    "resposta_beneficiario": frozenset({"String"}),
}


async def _variavel(engine: EngineJsonReader, instancia: str, nome: str) -> Any:
    rows = await engine.get_json(
        "/history/variable-instance",
        {"processInstanceId": instancia, "variableName": nome, "deserializeValues": "false"},
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict) or row.get("name") != nome or row.get("type") not in _VARIAVEIS[nome]:
        return None
    return row.get("value")


async def ler_contexto_do_reembolso(engine: EngineJsonReader, *, task_id: str) -> ReembolsoContexto:
    """Le o contexto de UMA tarefa de reembolso. Tarefa/instancia de outro processo ou tenant: recusa."""
    task = await engine.get_json(f"/task/{_segment(task_id)}")
    engine.same_tenant(task)
    chave = task.get("taskDefinitionKey")
    if not isinstance(chave, str) or chave not in REEMBOLSO_TASKS:
        raise GatewayRefusalError("task_unavailable")
    instancia = _segment(task.get("processInstanceId"))
    processo = await engine.get_json(f"/history/process-instance/{instancia}")
    engine.same_tenant(processo)
    if processo.get("processDefinitionKey") != REEMBOLSO_PROCESS_KEY:
        raise GatewayRefusalError("task_unavailable")
    nomes = tuple(_VARIAVEIS)
    valores = dict(
        zip(nomes, await asyncio.gather(*(_variavel(engine, instancia, n) for n in nomes)), strict=True)
    )
    protocolo = valores["protocolo_reembolso"]
    dias = valores["dias_desde_atendimento"]
    return ReembolsoContexto(
        task_definition_key=chave,
        protocolo=protocolo if isinstance(protocolo, str) and _PROTOCOLO.match(protocolo) else None,
        relacao_com_titular=_enum("relacao_com_titular", valores["relacao_com_titular"]),
        categoria_procedimento=_enum("categoria_procedimento", valores["categoria_procedimento"]),
        tipo_reembolso=_enum("tipo_reembolso", valores["tipo_reembolso"]),
        motivo_nao_rede=_enum("motivo_nao_rede", valores["motivo_nao_rede"]),
        data_atendimento=_data(valores["data_atendimento"]),
        data_solicitacao=_data(valores["data_solicitacao"]),
        dias_desde_atendimento=dias if type(dias) is int and -1 <= dias <= 100_000 else None,
        valor_solicitado_cents=_centavos(valores["valor_solicitado_cents"]),
        decisao_reembolso=_enum("decisao_reembolso", valores["decisao_reembolso"]),
        valor_reembolso_aprovado_cents=_centavos(valores["valor_reembolso_aprovado_cents"]),
        anexos=parse_anexos(valores["documentos_refs"]) + parse_anexos(valores["documentos_adicionais_refs"]),
        situacao_prazo=(
            valores["situacao_prazo"]
            if valores["situacao_prazo"] in ("DENTRO", "FORA", "NAO_AVALIADO")
            else None
        ),
        resposta_beneficiario=_resposta(valores["resposta_beneficiario"]),
    )


def _resposta(valor: object) -> str | None:
    """A resposta da beneficiaria, como chegou ao processo (ja' pelo scrub). Vazia/fora da forma: `None`."""
    if not isinstance(valor, str) or not valor.strip() or len(valor) > MAX_RESPOSTA:
        return None
    return "".join(c for c in valor if c == chr(10) or c.isprintable())
