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

COBERTURA POR PROCEDIMENTO (decisao do dono D1 de 18/10/2026: a Helena diz se o procedimento consta no Rol
para a segmentacao do plano): a rota `GET .../subjects/{ref}/cobertura` vive no artefato
`tina-cobertura.openapi.yaml` (manifest aditivo v1.9, bloco `manifest_v1_9` do pin, PUBLISHED em 16/10/2026).
OPCIONAL como as outras: sem `cobertura_openapi_bytes=`, `get_cobertura` recusa com `SCOPE_NOT_SUPPORTED`.
Finalidade FIXA `atendimento_whatsapp` (enum do contrato), por isso a E/S pode ir por um executor PROPRIO
(`cobertura_executor=`; a finalidade do executor de sempre pode ser outra). O termo pedido (`procedimento`)
so' atravessa no pedido, validado pelo schema do parametro; a resposta nao o devolve e nada aqui o loga.

CPT VIGENTE (caso C1 autorizado pelo dono e DPO em 20/10/2026, DL-0130): a rota
`GET .../subjects/{ref}/cpt` vive no artefato `tina-cpt.openapi.yaml` (manifest aditivo v1.15, bloco
`manifest_v1_15` do pin). OPCIONAL: sem `cpt_openapi_bytes=`, `get_cpt_vigente` recusa com
`SCOPE_NOT_SUPPORTED`. Finalidade FIXA `atendimento_whatsapp` e escopo `interop/tina.read` (o mesmo da
cobertura): a E/S vai por um executor PROPRIO (`cpt_executor=`). A resposta e' minimizada na fonte (sem CID
nem doenca): so' o sim/nao, a contagem e a maior data de fim.
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
    MAX_COBERTURA_ITENS,
    MAX_DEPENDENTES,
    CarenciaDoItem,
    CarenciaPlano,
    CarenciasView,
    CarteirinhaCompleta,
    CarteirinhasView,
    CatalogoProcedimentosView,
    CoberturaItemView,
    CoberturaView,
    CptVigenteView,
    DependenteAnonimo,
    DependenteNominal,
    DependentesNominaisView,
    DependentesView,
    ElegibilidadeView,
    ProcedimentoCatalogo,
    RequisicaoPlano,
    RequisicoesView,
    VinculoPlano,
)

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/tina.openapi.yaml"
ARTIFACT_DEPENDENTES: Final[str] = "schemas/openapi/maezo/v1/tina-dependentes.openapi.yaml"
#: Manifest aditivo v1.12 (decisao do dono de 17/10/2026). O digest entra no bloco `manifest_v1_12` do pin.
ARTIFACT_CARTEIRINHA: Final[str] = "schemas/openapi/maezo/v1/tina-carteirinha.openapi.yaml"
#: Manifest aditivo v1.13 (decisao do dono e DPO de 19/10/2026, DL-0127): os NOMES dos dependentes do proprio
#: titular. O digest entra no bloco `manifest_v1_13` do pin.
ARTIFACT_DEPENDENTES_NOME: Final[str] = "schemas/openapi/maezo/v1/tina-dependentes-nome.openapi.yaml"
#: Manifest aditivo v1.14 (DL-0128): o CATALOGO de procedimentos resolvidos por codigo (dado de referencia).
ARTIFACT_PROCEDIMENTOS: Final[str] = "schemas/openapi/maezo/v1/tina-procedimentos.openapi.yaml"
#: Manifest aditivo v1.9 (PUBLISHED em 16/10/2026). O digest esta' no bloco `manifest_v1_9` do pin.
ARTIFACT_COBERTURA: Final[str] = "schemas/openapi/maezo/v1/tina-cobertura.openapi.yaml"
#: Manifest aditivo v1.15 (DL-0130): a CPT VIGENTE do proprio sujeito. Digest no bloco `manifest_v1_15`.
ARTIFACT_CPT: Final[str] = "schemas/openapi/maezo/v1/tina-cpt.openapi.yaml"
OP_ELEGIBILIDADE: Final[str] = "amh.tina_get_elegibilidade"
OP_CARENCIAS: Final[str] = "amh.tina_get_carencias"
OP_REQUISICOES: Final[str] = "amh.tina_get_requisicoes"
OP_DEPENDENTES: Final[str] = "amh.tina_get_dependentes"
OP_CARTEIRINHA: Final[str] = "amh.tina_get_carteirinha"
#: `operationId: getCobertura` do contrato, no padrao de nome das operacoes do gateway.
OP_COBERTURA: Final[str] = "amh.tina_get_cobertura"
#: `operationId: getDependentesNominais` do contrato `tina-dependentes-nome` (manifest v1.13).
OP_DEPENDENTES_NOME: Final[str] = "amh.tina_get_dependentes_nome"
#: `operationId: getCatalogoProcedimentos` do contrato `tina-procedimentos` (manifest v1.14).
OP_PROCEDIMENTOS: Final[str] = "amh.tina_get_catalogo_procedimentos"
#: `operationId: getCptVigente` do contrato `tina-cpt` (manifest v1.15).
OP_CPT: Final[str] = "amh.tina_get_cpt_vigente"
#: A UNICA finalidade da rota da CPT (enum do contrato `tina-cpt`).
PURPOSE_CPT: Final[str] = "atendimento_whatsapp"
#: A UNICA finalidade do catalogo (enum do contrato).
PURPOSE_PROCEDIMENTOS: Final[str] = "atendimento_whatsapp"
OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        OP_ELEGIBILIDADE,
        OP_CARENCIAS,
        OP_REQUISICOES,
        OP_DEPENDENTES,
        OP_CARTEIRINHA,
        OP_COBERTURA,
        OP_DEPENDENTES_NOME,
        OP_PROCEDIMENTOS,
        OP_CPT,
    }
)
#: A UNICA finalidade da rota da carteirinha (enum do contrato).
PURPOSE_CARTEIRINHA: Final[str] = "atendimento_whatsapp"
#: A UNICA finalidade da rota dos nomes dos dependentes (enum do contrato `tina-dependentes-nome`).
PURPOSE_DEPENDENTES_NOME: Final[str] = "atendimento_whatsapp"
#: A UNICA finalidade da rota da cobertura (enum do contrato `tina-cobertura`).
PURPOSE_COBERTURA: Final[str] = "atendimento_whatsapp"
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
    OP_COBERTURA: ("/subjects/{portable_subject_ref}/cobertura", "Cobertura"),
    OP_DEPENDENTES_NOME: ("/subjects/{portable_subject_ref}/dependentes/nomes", "DependentesNominais"),
    OP_PROCEDIMENTOS: ("/procedimentos", "CatalogoProcedimentos"),
    OP_CPT: ("/subjects/{portable_subject_ref}/cpt", "CptVigente"),
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
        cobertura_openapi_bytes: bytes | None = None,
        cobertura_executor: GovernedTinaExecutor | None = None,
        dependentes_nome_openapi_bytes: bytes | None = None,
        dependentes_nome_executor: GovernedTinaExecutor | None = None,
        procedimentos_openapi_bytes: bytes | None = None,
        procedimentos_executor: GovernedTinaExecutor | None = None,
        cpt_openapi_bytes: bytes | None = None,
        cpt_executor: GovernedTinaExecutor | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedTinaExecutor):
                raise ValueError
            if carteirinha_executor is not None and not isinstance(
                carteirinha_executor, GovernedTinaExecutor
            ):
                raise ValueError
            if cobertura_executor is not None and not isinstance(cobertura_executor, GovernedTinaExecutor):
                raise ValueError
            if dependentes_nome_executor is not None and not isinstance(
                dependentes_nome_executor, GovernedTinaExecutor
            ):
                raise ValueError
            if procedimentos_executor is not None and not isinstance(
                procedimentos_executor, GovernedTinaExecutor
            ):
                raise ValueError
            if cpt_executor is not None and not isinstance(cpt_executor, GovernedTinaExecutor):
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
            # Cobertura por procedimento (manifest v1.9): so' com os bytes; o digest TEM de estar no pin.
            self._cobertura = cobertura_openapi_bytes is not None
            if cobertura_openapi_bytes is not None:
                self._carregar(
                    cobertura_openapi_bytes, ARTIFACT_COBERTURA, pin.artifact_digests, (OP_COBERTURA,)
                )
            # Nomes dos dependentes (manifest v1.13): so' com os bytes; o digest TEM de estar no pin (sem o
            # bloco `manifest_v1_13`, `digests[ARTIFACT_DEPENDENTES_NOME]` levanta e o construtor recusa).
            self._dependentes_nome = dependentes_nome_openapi_bytes is not None
            if dependentes_nome_openapi_bytes is not None:
                self._carregar(
                    dependentes_nome_openapi_bytes,
                    ARTIFACT_DEPENDENTES_NOME,
                    pin.artifact_digests,
                    (OP_DEPENDENTES_NOME,),
                )
            # Catalogo de procedimentos (manifest v1.14): so' com os bytes; o digest TEM de estar no pin.
            self._procedimentos = procedimentos_openapi_bytes is not None
            if procedimentos_openapi_bytes is not None:
                self._carregar(
                    procedimentos_openapi_bytes,
                    ARTIFACT_PROCEDIMENTOS,
                    pin.artifact_digests,
                    (OP_PROCEDIMENTOS,),
                )
            self._executor_procedimentos = procedimentos_executor or executor
            # CPT vigente (manifest v1.15): so' com os bytes; o digest TEM de estar no pin.
            self._cpt = cpt_openapi_bytes is not None
            if cpt_openapi_bytes is not None:
                self._carregar(cpt_openapi_bytes, ARTIFACT_CPT, pin.artifact_digests, (OP_CPT,))
            self._executor_cpt = cpt_executor or executor
            self._executor = executor
            self._executor_carteirinha = carteirinha_executor or executor
            self._executor_cobertura = cobertura_executor or executor
            self._executor_dependentes_nome = dependentes_nome_executor or executor
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
            if operacao == OP_CARTEIRINHA:
                executor = self._executor_carteirinha
            elif operacao == OP_COBERTURA:
                executor = self._executor_cobertura
            elif operacao == OP_DEPENDENTES_NOME:
                executor = self._executor_dependentes_nome
            elif operacao == OP_PROCEDIMENTOS:
                executor = self._executor_procedimentos
            elif operacao == OP_CPT:
                executor = self._executor_cpt
            else:
                executor = self._executor
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
            # O catalogo (v1.14) e' dado de referencia: nao ha' sujeito para conferir.
            if operacao != OP_PROCEDIMENTOS and body["portable_subject_ref"] != portable_subject_ref:
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

    async def get_catalogo_procedimentos(
        self,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        pagina: int = 1,
        tamanho: int = 1000,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CatalogoProcedimentosView]:
        """Uma pagina do catalogo de procedimentos (manifest v1.14; DL-0128). Sem o artefato carregado,
        recusa fechada (`SCOPE_NOT_SUPPORTED`). Dado de referencia: nao ha' sujeito."""
        if not self._procedimentos:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        if type(pagina) is not int or type(tamanho) is not int:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._consultar(
            OP_PROCEDIMENTOS,
            "",
            {"purpose_of_use": purpose_of_use, "pagina": pagina, "tamanho": tamanho},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _catalogo)

    async def get_dependentes_nominais(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        request_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[DependentesNominaisView]:
        """Os dependentes ATIVOS do proprio titular COM o nome completo (manifest v1.13; DL-0127). Sem o
        artefato `tina-dependentes-nome` carregado no construtor, recusa fechada (`SCOPE_NOT_SUPPORTED`).
        `404` (sujeito desconhecido ou rota desligada) = `NOT_FOUND`. Nenhum nome vai a log."""
        if not self._dependentes_nome:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        resultado = await self._consultar(
            OP_DEPENDENTES_NOME,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use, HEADER_REQUEST_REF: request_ref},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _dependentes_nominais)

    async def get_cobertura(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        procedimento: str,
        limite: int = 5,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CoberturaView]:
        """Os fatos de cobertura do procedimento (codigo ou termo) para o plano do proprio sujeito (manifest
        v1.9). Sem o artefato `tina-cobertura` carregado no construtor, recusa fechada
        (`SCOPE_NOT_SUPPORTED`). `procedimento` fora do padrao do contrato ou `limite` fora de 1..10 =
        `INVALID_REQUEST` sem despacho. `404` (rota desligada, sujeito sem plano) = `NOT_FOUND`."""
        if not self._cobertura:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        if type(procedimento) is not str or type(limite) is not int:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._consultar(
            OP_COBERTURA,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use, "procedimento": procedimento, "limite": limite},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _cobertura)

    async def get_cpt_vigente(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CptVigenteView]:
        """A CPT vigente do proprio sujeito (manifest v1.15; DL-0130). Sem o artefato `tina-cpt` carregado,
        recusa fechada (`SCOPE_NOT_SUPPORTED`). `404` (rota desligada, sujeito sem plano) = `NOT_FOUND`;
        `429` (limite por sujeito, compartilhado com a cobertura) = `RATE_LIMITED`."""
        if not self._cpt:
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        resultado = await self._consultar(
            OP_CPT,
            portable_subject_ref,
            {"purpose_of_use": purpose_of_use},
            consent_decision_ref=consent_decision_ref,
            timeout_seconds=timeout_seconds,
        )
        return _converter(resultado, _cpt)


def _converter(resultado: PortResult[dict[str, Any]], montar: Any) -> PortResult[Any]:
    if not resultado.succeeded or resultado.value is None:
        failure = resultado.failure
        return PortResult.refused(failure.reason if failure is not None else Reason.UPSTREAM_UNAVAILABLE)
    try:
        return PortResult.ok(montar(resultado.value))
    except Exception:
        return PortResult.refused(Reason.CONTRACT_VIOLATION)


def _catalogo(body: dict[str, Any]) -> CatalogoProcedimentosView:
    return CatalogoProcedimentosView(
        versao_catalogo=body["versao_catalogo"],
        pagina=body["pagina"],
        total_paginas=body["total_paginas"],
        total_itens=body["total_itens"],
        itens=tuple(
            ProcedimentoCatalogo(
                codigo=i["codigo"], descricao=i["descricao"], tuss=i.get("nome_tuss"), grupo=i.get("grupo")
            )
            for i in body["itens"]
        ),
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


def _dependentes_nominais(body: dict[str, Any]) -> DependentesNominaisView:
    itens = tuple(
        DependenteNominal(
            ordem=d["ordem"],
            relacao_dependencia=d.get("relacao_dependencia"),
            nome_completo=d["nome_completo"],
        )
        for d in body["dependentes"]
    )
    ordens = [d.ordem for d in itens]
    # A ordem do contrato e' 1..N sem buraco nem repeticao; nao titular nunca traz nome.
    if ordens != list(range(1, len(itens) + 1)) or len(itens) > MAX_DEPENDENTES:
        raise ValueError
    if body["eh_titular"] is not True and itens:
        raise ValueError
    return DependentesNominaisView(
        portable_subject_ref=body["portable_subject_ref"],
        eh_titular=body["eh_titular"],
        dependentes=itens,
        lista_truncada=body["lista_truncada"],
        fonte_atualizada_em=body["fonte_atualizada_em"],
    )


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


def _cobertura(body: dict[str, Any]) -> CoberturaView:
    itens = tuple(
        CoberturaItemView(
            descricao=i["descricao"],
            # `situacao` nula no contrato = a fonte nao classificou: o mesmo que `indeterminado` (nunca
            # "nao cobre"), sem inventar fato.
            situacao=i["situacao"] if i["situacao"] is not None else "indeterminado",
            coberto=i["coberto"],
            no_rol=i["no_rol"],
            na_segmentacao=i["na_segmentacao"],
            rol_edicao=i["rol_edicao"],
            tem_diretriz=i["diretriz_de_utilizacao"] is True,
            carencias=tuple(
                CarenciaDoItem(cumprida=c["cumprida"], dias=c["dias"], termina_em=c["termina_em"])
                for c in i["carencias"]
            ),
        )
        for i in body["itens"]
    )
    if len(itens) > MAX_COBERTURA_ITENS or type(body["truncada"]) is not bool:
        raise ValueError
    plano = body["plano"]
    return CoberturaView(
        portable_subject_ref=body["portable_subject_ref"],
        segmentacao=plano["segmentacao"],
        regulamentado=plano["regulamentado"],
        itens=itens,
        truncada=body["truncada"],
        avisos=tuple(body["avisos"]),
        fonte_atualizada_em=body["fonte"]["atualizada_em"],
    )


def _cpt(body: dict[str, Any]) -> CptVigenteView:
    tem, quantas, termina = body["tem_cpt_vigente"], body["cpt_vigentes"], body["termina_em"]
    # Coerencia que o schema nao expressa (descrita no contrato): sem CPT vigente, contagem 0 e sem data;
    # com CPT vigente, ao menos uma linha. Um desacordo e' violacao de contrato, nunca um aviso.
    if type(tem) is not bool or type(quantas) is not int:
        raise ValueError
    if (not tem and (quantas != 0 or termina is not None)) or (tem and quantas < 1):
        raise ValueError
    return CptVigenteView(
        portable_subject_ref=body["portable_subject_ref"],
        tem_cpt_vigente=tem,
        cpt_vigentes=quantas,
        termina_em=termina,
        fonte_atualizada_em=body["fonte"]["atualizada_em"],
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
    "ARTIFACT_COBERTURA",
    "ARTIFACT_CPT",
    "ARTIFACT_DEPENDENTES_NOME",
    "ARTIFACT_PROCEDIMENTOS",
    "ARTIFACT_DEPENDENTES",
    "HEADER_REQUEST_REF",
    "OPERATIONS",
    "OP_CARENCIAS",
    "OP_CARTEIRINHA",
    "OP_COBERTURA",
    "OP_CPT",
    "OP_DEPENDENTES_NOME",
    "OP_PROCEDIMENTOS",
    "PURPOSE_PROCEDIMENTOS",
    "OP_DEPENDENTES",
    "OP_ELEGIBILIDADE",
    "OP_REQUISICOES",
    "PURPOSE_CARTEIRINHA",
    "PURPOSE_COBERTURA",
    "PURPOSE_CPT",
    "PURPOSE_DEPENDENTES_NOME",
    "SERVER",
    "AmhTinaAdapter",
    "GovernedTinaExecutor",
    "GovernedTinaRequest",
    "GovernedTinaResponse",
    "TinaContractError",
]
