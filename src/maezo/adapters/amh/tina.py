"""Fatos do plano (vinculos, carencias, requisicoes) pelo contrato `tina` da AMH (ADR-0037 XRD-04/06).

O artefato `schemas/openapi/maezo/v1/tina.openapi.yaml` de `Omni-Saude/amh-data-platform` entra pelo
manifest ADITIVO v1.2, publicado pela AMH em 13/10/2026 (XRG-2 run 38346972222) e pinado no bloco
`manifest_v1_2`. Este adaptador e' fail-closed por construcao, como `billing_status.py`: so' sobe com os
BYTES do OpenAPI cujo sha256 esta no bloco `manifest_v1_2` do pin imutavel
(`config/integrations/amh/contracts.lock.json`). Sem o bloco nao ha' digest e o construtor recusa.

Mesma postura de `billing_status.py`/`subject_resolution.py`: nenhum cliente HTTP, host ou credencial
aqui. A E/S passa por um executor do gateway (`GovernedTinaExecutor`), sem implementacao padrao. O
adaptador valida cada resposta contra o schema pinado e devolve sucesso ou recusa fechada
(`PortFailureReason`); nenhuma excecao atravessa o port e nenhum detalhe do executor estrangeiro e'
copiado. Uma resposta de OUTRO sujeito e' violacao de contrato, nunca um fato.

DEPENDENTES (decisao do dono de 13/10/2026): a rota `GET .../subjects/{ref}/dependentes` vive em OUTRO
artefato, `tina-dependentes.openapi.yaml` (manifest aditivo v1.5, bloco `manifest_v1_5` do pin). Ela e'
OPCIONAL no adaptador: so' existe com `dependentes_openapi_bytes=` (digest conferido contra o pin); sem ele,
`get_dependentes` recusa com `SCOPE_NOT_SUPPORTED` e o comportamento dos fatos do plano nao muda.

CARTEIRINHA COMPLETA (decisao do dono de 17/10/2026, "modelo de teste" so' em dev): a rota
`GET .../subjects/{ref}/carteirinha` vive no artefato `tina-carteirinha.openapi.yaml` (manifest aditivo v1.12,
bloco `manifest_v1_12` do pin, PINADO em 17/10/2026; bytes fora do digest = o construtor recusa
subir). Tambem OPCIONAL: sem os bytes, `get_carteirinha` recusa com `SCOPE_NOT_SUPPORTED`.
Diferente das outras leituras: finalidade FIXA `atendimento_whatsapp`, cabecalho obrigatorio
`X-Amh-Request-Ref` (id OPACO da solicitacao) e escopo OAuth proprio (`interop/tina.carteirinha`) — por isso
a E/S vai por um executor PROPRIO (`carteirinha_executor=`). O numero completo so' atravessa em
`CarteirinhaCompleta` (fora do `repr`).
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
    MAX_CARTEIRINHAS,
    MAX_DEPENDENTES,
    CarenciaPlano,
    CarenciasView,
    CarteirinhaCompleta,
    CarteirinhasView,
    DependenteAnonimo,
    DependentesView,
    ElegibilidadeView,
    RequisicaoPlano,
    RequisicoesView,
    VinculoPlano,
)

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/tina.openapi.yaml"
ARTIFACT_DEPENDENTES: Final[str] = "schemas/openapi/maezo/v1/tina-dependentes.openapi.yaml"
#: Manifest aditivo v1.12 (decisao do dono de 17/10/2026). O digest entra no bloco `manifest_v1_12` do pin.
ARTIFACT_CARTEIRINHA: Final[str] = "schemas/openapi/maezo/v1/tina-carteirinha.openapi.yaml"
OP_ELEGIBILIDADE: Final[str] = "amh.tina_get_elegibilidade"
OP_CARENCIAS: Final[str] = "amh.tina_get_carencias"
OP_REQUISICOES: Final[str] = "amh.tina_get_requisicoes"
OP_DEPENDENTES: Final[str] = "amh.tina_get_dependentes"
OP_CARTEIRINHA: Final[str] = "amh.tina_get_carteirinha"
OPERATIONS: Final[frozenset[str]] = frozenset(
    {OP_ELEGIBILIDADE, OP_CARENCIAS, OP_REQUISICOES, OP_DEPENDENTES, OP_CARTEIRINHA}
)
#: A UNICA finalidade da rota da carteirinha (enum do contrato).
PURPOSE_CARTEIRINHA: Final[str] = "atendimento_whatsapp"
#: O cabecalho do id opaco da solicitacao (o unico cabecalho que o adaptador aceita de um contrato TINA).
HEADER_REQUEST_REF: Final[str] = "X-Amh-Request-Ref"
SERVER: Final[str] = "/interop/tina/v1"
#: Operacao -> (caminho do contrato, schema da resposta 200).
_ROTAS: Final[dict[str, tuple[str, str]]] = {
    OP_ELEGIBILIDADE: ("/subjects/{portable_subject_ref}/elegibilidade", "Elegibilidade"),
    OP_CARENCIAS: ("/subjects/{portable_subject_ref}/carencias", "Carencias"),
    OP_REQUISICOES: ("/subjects/{portable_subject_ref}/requisicoes", "Requisicoes"),
    OP_DEPENDENTES: ("/subjects/{portable_subject_ref}/dependentes", "Dependentes"),
    OP_CARTEIRINHA: ("/subjects/{portable_subject_ref}/carteirinha", "Carteirinhas"),
}
_ROTAS_FATOS: Final[tuple[str, ...]] = (OP_ELEGIBILIDADE, OP_CARENCIAS, OP_REQUISICOES)
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
    #: `X-Amh-Request-Ref` (so' a rota da carteirinha o declara); `None` nas outras.
    request_ref: str | None = None
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
        dependentes_openapi_bytes: bytes | None = None,
        carteirinha_openapi_bytes: bytes | None = None,
        carteirinha_executor: GovernedTinaExecutor | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedTinaExecutor):
                raise ValueError
            if carteirinha_executor is not None and not isinstance(
                carteirinha_executor, GovernedTinaExecutor
            ):
                raise ValueError
            pin = load_contract_pin(pin_path)
            self._validators: dict[str, Draft4Validator] = {}
            self._parameters: dict[str, list[dict[str, Any]]] = {}
            self._carregar(openapi_bytes, ARTIFACT, pin.artifact_digests, _ROTAS_FATOS)
            self._dependentes = dependentes_openapi_bytes is not None
            if dependentes_openapi_bytes is not None:
                self._carregar(
                    dependentes_openapi_bytes, ARTIFACT_DEPENDENTES, pin.artifact_digests, (OP_DEPENDENTES,)
                )
            # Carteirinha completa (manifest v1.12): so' com os bytes; o digest TEM de estar no pin (sem o
            # bloco `manifest_v1_12`, `digests[ARTIFACT_CARTEIRINHA]` levanta e o construtor recusa).
            self._carteirinha = carteirinha_openapi_bytes is not None
            if carteirinha_openapi_bytes is not None:
                self._carregar(
                    carteirinha_openapi_bytes, ARTIFACT_CARTEIRINHA, pin.artifact_digests, (OP_CARTEIRINHA,)
                )
            self._executor = executor
            self._executor_carteirinha = carteirinha_executor or executor
        except Exception:
            raise TinaContractError() from None

    def _carregar(
        self,
        openapi_bytes: bytes,
        artefato: str,
        digests: Any,
        operacoes: tuple[str, ...],
    ) -> None:
        """Confere os bytes contra o digest do pin e monta o validador de cada rota do documento."""
        if (
            not isinstance(openapi_bytes, bytes)
            or len(openapi_bytes) > _MAX_BYTES
            or hashlib.sha256(openapi_bytes).hexdigest() != digests[artefato]
        ):
            raise ValueError
        document = yaml.safe_load(openapi_bytes)
        if document["openapi"] != "3.0.3" or document["servers"][0]["url"] != SERVER:
            raise ValueError
        definitions = _json_schema(document["components"]["schemas"])
        for operacao in operacoes:
            caminho, nome = _ROTAS[operacao]
            get = document["paths"][caminho]["get"]
            if get["responses"]["200"]["content"]["application/json"]["schema"] != {
                "$ref": "#/components/schemas/" + nome
            }:
                raise ValueError
            schema = {"$ref": "#/definitions/" + nome, "definitions": definitions}
            Draft4Validator.check_schema(schema)
            self._validators[operacao] = Draft4Validator(schema, format_checker=_FORMATS)
            self._parameters[operacao] = _parametros(document, get)

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
            request_ref: str | None = None
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
                elif parameter["in"] == "header":
                    # So' o id opaco da solicitacao; qualquer outro cabecalho do contrato e' recusado.
                    if name != HEADER_REQUEST_REF or not isinstance(value, str):
                        return PortResult.refused(Reason.INVALID_REQUEST)
                    request_ref = value
            executor = self._executor_carteirinha if operacao == OP_CARTEIRINHA else self._executor
            registradas = executor.registered_operations
            if type(registradas) is not frozenset or operacao not in registradas:
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            caminho = _ROTAS[operacao][0]
            request = GovernedTinaRequest(
                operation=operacao,
                path=SERVER + caminho.replace("{portable_subject_ref}", quote(portable_subject_ref, safe="")),
                query=tuple(query),
                consent_decision_ref=consent_decision_ref,
                timeout_seconds=timeout_seconds,
                request_ref=request_ref,
            )
            async with asyncio.timeout(timeout_seconds):
                response = await executor.execute(request)
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

    async def get_dependentes(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[DependentesView]:
        """Dependentes ATIVOS do titular, anonimos (contagem exata + ate 20 itens). Sem o artefato
        `tina-dependentes` carregado no construtor, recusa fechada (`SCOPE_NOT_SUPPORTED`)."""
        if not self._dependentes:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        resultado = await self._consultar(
            OP_DEPENDENTES,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _dependentes)

    async def get_carteirinha(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        request_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CarteirinhasView]:
        """O numero COMPLETO da carteirinha dos vinculos ATIVOS do proprio sujeito (manifest v1.12). Sem o
        artefato `tina-carteirinha` carregado no construtor, recusa fechada (`SCOPE_NOT_SUPPORTED`). `404`
        (sem vinculo ativo, sujeito desconhecido ou rota desligada) = `NOT_FOUND`. Nenhum numero vai a log."""
        if not self._carteirinha:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        resultado = await self._consultar(
            OP_CARTEIRINHA,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use, HEADER_REQUEST_REF: request_ref},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _carteirinhas)


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


def _carteirinhas(body: dict[str, Any]) -> CarteirinhasView:
    itens = tuple(
        CarteirinhaCompleta(
            carteirinha_numero=c["carteirinha_numero"],
            carteirinha_mascarada=c["carteirinha_mascarada"],
            vinculo_principal=c["vinculo_principal"],
        )
        for c in body["carteirinhas"]
    )
    if not 1 <= len(itens) <= MAX_CARTEIRINHAS:
        raise ValueError
    return CarteirinhasView(
        portable_subject_ref=body["portable_subject_ref"],
        carteirinhas=itens,
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


def _dependentes(body: dict[str, Any]) -> DependentesView:
    itens = tuple(
        DependenteAnonimo(
            relacao_dependencia=d["relacao_dependencia"],
            situacao_vinculo=d["situacao_beneficiario"],
            rn_no_plano=d["recem_nascido_no_plano"],
        )
        for d in body["dependentes"]
    )
    total = body["total_ativos"]
    # Coerencia que o schema nao expressa: um desacordo e' violacao de contrato, nunca um numero "arrumado".
    if (
        len(itens) > MAX_DEPENDENTES
        or total < len(itens)
        or body["lista_truncada"] is not (total > len(itens))
        or (not body["eh_titular"] and (total != 0 or itens))
    ):
        raise ValueError
    return DependentesView(
        portable_subject_ref=body["portable_subject_ref"],
        eh_titular=body["eh_titular"],
        total_ativos=total,
        lista_truncada=body["lista_truncada"],
        dependentes=itens,
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


__all__ = [
    "ARTIFACT",
    "ARTIFACT_CARTEIRINHA",
    "ARTIFACT_DEPENDENTES",
    "HEADER_REQUEST_REF",
    "OPERATIONS",
    "OP_CARENCIAS",
    "OP_CARTEIRINHA",
    "OP_DEPENDENTES",
    "OP_ELEGIBILIDADE",
    "OP_REQUISICOES",
    "PURPOSE_CARTEIRINHA",
    "SERVER",
    "AmhTinaAdapter",
    "GovernedTinaExecutor",
    "GovernedTinaRequest",
    "GovernedTinaResponse",
    "TinaContractError",
]
