"""Fatos do plano (vinculos, carencias, requisicoes) pelo contrato `tina` da AMH (ADR-0037 XRD-04/06).

O CONTRATO AINDA NAO ESTA PUBLICADO. O artefato `schemas/openapi/maezo/v1/tina.openapi.yaml` vive em
`Omni-Saude/amh-data-platform` como DRAFT e entra pelo manifest ADITIVO v1.2, que depende de atestacao
humana (XRG-2). Este adaptador e' fail-closed por construcao, como `billing_status.py`: so' sobe com os
BYTES do OpenAPI cujo sha256 esta no bloco `manifest_v1_2` do pin imutavel
(`config/integrations/amh/contracts.lock.json`). Sem o bloco nao ha' digest e o construtor recusa.

Mesma postura de `billing_status.py`/`subject_resolution.py`: nenhum cliente HTTP, host ou credencial
aqui. A E/S passa por um executor do gateway (`GovernedTinaExecutor`), sem implementacao padrao. O
adaptador valida cada resposta contra o schema pinado e devolve sucesso ou recusa fechada
(`PortFailureReason`); nenhuma excecao atravessa o port e nenhum detalhe do executor estrangeiro e'
copiado. Uma resposta de OUTRO sujeito e' violacao de contrato, nunca um fato.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult
from maezo.ports.tina import (
    CarenciaPlano,
    CarenciasView,
    ElegibilidadeView,
    RequisicaoPlano,
    RequisicoesView,
    VinculoPlano,
)

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/tina.openapi.yaml"
OP_ELEGIBILIDADE: Final[str] = "amh.tina_get_elegibilidade"
OP_CARENCIAS: Final[str] = "amh.tina_get_carencias"
OP_REQUISICOES: Final[str] = "amh.tina_get_requisicoes"
OPERATIONS: Final[frozenset[str]] = frozenset({OP_ELEGIBILIDADE, OP_CARENCIAS, OP_REQUISICOES})
SERVER: Final[str] = "/interop/tina/v1"
#: Operacao -> (caminho do contrato, schema da resposta 200).
_ROTAS: Final[dict[str, tuple[str, str]]] = {
    OP_ELEGIBILIDADE: ("/subjects/{portable_subject_ref}/elegibilidade", "Elegibilidade"),
    OP_CARENCIAS: ("/subjects/{portable_subject_ref}/carencias", "Carencias"),
    OP_REQUISICOES: ("/subjects/{portable_subject_ref}/requisicoes", "Requisicoes"),
}
Reason = PortFailureReason

_STATUS_REASON: Final[dict[int, PortFailureReason]] = {
    400: Reason.INVALID_REQUEST,
    401: Reason.NOT_AUTHENTICATED,
    404: Reason.NOT_FOUND,
    429: Reason.RATE_LIMITED,
    500: Reason.UPSTREAM_UNAVAILABLE,
}
_FORBIDDEN_REASON: Final[dict[str, PortFailureReason]] = {
    "consent_denied": Reason.CONSENT_REQUIRED,
    "purpose_not_permitted": Reason.PURPOSE_DENIED,
    "scope_not_supported": Reason.SCOPE_NOT_SUPPORTED,
}


class TinaContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("tina_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedTinaRequest:
    """Descritor so' do servidor. A referencia de base legal e' metadado de auditoria, nunca query."""

    operation: str
    path: str
    query: tuple[tuple[str, str | int], ...]
    consent_decision_ref: str
    timeout_seconds: float
    method: str = field(default="GET", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class GovernedTinaResponse:
    status_code: int
    body: bytes


class GovernedTinaExecutor(ABC):
    """Porta de composicao do gateway, nao um callback HTTP qualquer. Sem implementacao padrao.

    O registro em producao amarra tenant, carga de trabalho, principal, proposito, zona PHI, auditoria e
    credencial; `execute` recusa estado nao autorizado a CADA chamada. Nunca segue redirecionamento.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(self, request: GovernedTinaRequest) -> PortResult[GovernedTinaResponse]: ...


def _parametros(document: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
    resolvidos: list[dict[str, Any]] = []
    for item in operation["parameters"]:
        if "$ref" in item:
            pointer = item["$ref"]
            if not pointer.startswith("#/components/parameters/"):
                raise ValueError
            item = document["components"]["parameters"][pointer.rsplit("/", 1)[1]]
        resolvidos.append(item)
    return resolvidos


class AmhTinaAdapter:
    """`TinaPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedTinaExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedTinaExecutor):
                raise ValueError
            pin = load_contract_pin(pin_path)
            if (
                len(openapi_bytes) > _MAX_BYTES
                or hashlib.sha256(openapi_bytes).hexdigest() != pin.artifact_digests[ARTIFACT]
            ):
                raise ValueError
            document = yaml.safe_load(openapi_bytes)
            if document["openapi"] != "3.0.3" or document["servers"][0]["url"] != SERVER:
                raise ValueError
            definitions = _json_schema(document["components"]["schemas"])
            self._validators: dict[str, Draft4Validator] = {}
            self._parameters: dict[str, list[dict[str, Any]]] = {}
            for operacao, (caminho, nome) in _ROTAS.items():
                get = document["paths"][caminho]["get"]
                if get["responses"]["200"]["content"]["application/json"]["schema"] != {
                    "$ref": "#/components/schemas/" + nome
                }:
                    raise ValueError
                schema = {"$ref": "#/definitions/" + nome, "definitions": definitions}
                Draft4Validator.check_schema(schema)
                self._validators[operacao] = Draft4Validator(schema, format_checker=_FORMATS)
                self._parameters[operacao] = _parametros(document, get)
            self._executor = executor
        except Exception:
            raise TinaContractError() from None

    async def _consultar(
        self,
        operacao: str,
        portable_subject_ref: str,
        valores: dict[str, Any],
        *,
        consent_decision_ref: str,
        timeout_seconds: float,
    ) -> PortResult[dict[str, Any]]:
        """Monta o pedido pelo contrato, despacha e devolve o corpo VALIDADO (ou a recusa fechada)."""
        try:
            if (
                not isinstance(consent_decision_ref, str)
                or not consent_decision_ref.strip()
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            valores = {"portable_subject_ref": portable_subject_ref, **valores}
            query: list[tuple[str, str | int]] = []
            for parameter in self._parameters[operacao]:
                name = parameter["name"]
                value = valores.get(name)
                if value is None and not parameter.get("required", False):
                    continue
                validator = Draft4Validator(_json_schema(parameter["schema"]), format_checker=_FORMATS)
                if not validator.is_valid(value):
                    return PortResult.refused(Reason.INVALID_REQUEST)
                if parameter["in"] == "query":
                    if not isinstance(value, (str, int)) or isinstance(value, bool):
                        return PortResult.refused(Reason.INVALID_REQUEST)
                    query.append((name, value))
            registradas = self._executor.registered_operations
            if type(registradas) is not frozenset or operacao not in registradas:
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            caminho = _ROTAS[operacao][0]
            request = GovernedTinaRequest(
                operation=operacao,
                path=SERVER + caminho.replace("{portable_subject_ref}", quote(portable_subject_ref, safe="")),
                query=tuple(query),
                consent_decision_ref=consent_decision_ref,
                timeout_seconds=timeout_seconds,
            )
            async with asyncio.timeout(timeout_seconds):
                response = await self._executor.execute(request)
            if not isinstance(response, PortResult) or type(response.succeeded) is not bool:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if not response.succeeded:
                failure = response.failure
                if not isinstance(failure, PortFailure) or not isinstance(failure.reason, Reason):
                    return PortResult.refused(Reason.CONTRACT_VIOLATION)
                # O detalhe de um executor estrangeiro nao e' confiavel como livre de PHI.
                return PortResult.refused(failure.reason)
        except TimeoutError:
            return PortResult.refused(Reason.TIMEOUT)
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        try:
            received = response.value
            if not isinstance(received, GovernedTinaResponse) or type(received.status_code) is not int:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            status = received.status_code
            if status == 403:
                body = _json(received.body)
                return PortResult.refused(
                    _FORBIDDEN_REASON.get(body.get("reason"), Reason.CONTRACT_VIOLATION)
                )
            if status != 200:
                return PortResult.refused(_STATUS_REASON.get(status, Reason.CONTRACT_VIOLATION))
            body = _json(received.body)
            if not self._validators[operacao].is_valid(body):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if body["portable_subject_ref"] != portable_subject_ref:
                # A resposta e' de OUTRO sujeito: nunca devolver fato do plano de quem nao foi pedido.
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            return PortResult.ok(body)
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)

    async def get_elegibilidade(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ElegibilidadeView]:
        resultado = await self._consultar(
            OP_ELEGIBILIDADE,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _elegibilidade)

    async def get_carencias(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limite: int = 20,
        pendentes: bool | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CarenciasView]:
        if pendentes is not None and type(pendentes) is not bool:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._consultar(
            OP_CARENCIAS,
            portable_subject_ref,
            {
                "purpose_of_use": purpose_of_use,
                "limite": limite,
                # O contrato declara `pendentes` como TEXTO ('true'/'false'), nao booleano.
                "pendentes": None if pendentes is None else ("true" if pendentes else "false"),
            },
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _carencias)

    async def get_requisicoes(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limite: int = 20,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[RequisicoesView]:
        resultado = await self._consultar(
            OP_REQUISICOES,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use, "limite": limite},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _requisicoes)


def _converter(resultado: PortResult[dict[str, Any]], montar: Any) -> PortResult[Any]:
    if not resultado.succeeded or resultado.value is None:
        failure = resultado.failure
        return PortResult.refused(failure.reason if failure is not None else Reason.UPSTREAM_UNAVAILABLE)
    try:
        return PortResult.ok(montar(resultado.value))
    except Exception:
        return PortResult.refused(Reason.CONTRACT_VIOLATION)


def _elegibilidade(body: dict[str, Any]) -> ElegibilidadeView:
    return ElegibilidadeView(
        portable_subject_ref=body["portable_subject_ref"],
        ativo=body["ativo"],
        vinculos=tuple(
            VinculoPlano(
                carteirinha_mascarada=v["carteirinha_mascarada"],
                plano=v["plano"],
                registro_ans_plano=v.get("registro_ans_plano"),
                segmentacao=v.get("segmentacao"),
                acomodacao=v.get("acomodacao"),
                tipo_contratacao=v.get("tipo_contratacao"),
                vigencia_inicio=v["vigencia_inicio"],
                cancelamento=v["cancelamento"],
                atendimento_liberado=v.get("atendimento_liberado"),
                situacao_vinculo=v.get("situacao_beneficiario"),
                situacao_contrato=v.get("situacao_contrato"),
                titular=v["titular"],
                relacao_dependencia=v.get("relacao_dependencia"),
                titular_ref=v.get("titular_ref"),
            )
            for v in body["vinculos"]
        ),
        fonte_atualizada_em=body["fonte_atualizada_em"],
        campos_ausentes=frozenset(body["qualidade"]["campos_ausentes"]),
    )


def _carencias(body: dict[str, Any]) -> CarenciasView:
    return CarenciasView(
        portable_subject_ref=body["portable_subject_ref"],
        pendentes=body["pendentes"],
        carencias=tuple(
            CarenciaPlano(
                carencia=c["carencia"],
                inicio=c["inicio"],
                dias=c["dias"],
                validade=c["validade"],
                cumprida=c["cumprida"],
            )
            for c in body["carencias"]
        ),
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


def _requisicoes(body: dict[str, Any]) -> RequisicoesView:
    return RequisicoesView(
        portable_subject_ref=body["portable_subject_ref"],
        requisicoes=tuple(
            RequisicaoPlano(
                solicitacao=r["solicitacao"],
                solicitada_em=r["solicitada_em"],
                status=r["status"],
                senha_mascarada=r.get("senha_mascarada"),
                senha_validade=r.get("senha_validade"),
                senha_vigente=r.get("senha_vigente"),
                sla_dias=r.get("sla_dias"),
                liberacao_prevista=r.get("liberacao_prevista"),
            )
            for r in body["requisicoes"]
        ),
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


__all__ = [
    "ARTIFACT",
    "OPERATIONS",
    "OP_CARENCIAS",
    "OP_ELEGIBILIDADE",
    "OP_REQUISICOES",
    "SERVER",
    "AmhTinaAdapter",
    "GovernedTinaExecutor",
    "GovernedTinaRequest",
    "GovernedTinaResponse",
    "TinaContractError",
]
