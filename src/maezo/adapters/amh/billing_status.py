"""Leitura da situacao de cobranca pelo contrato `billing-status` da AMH (ADR-0037 XRD-04/06).

O CONTRATO AINDA NAO ESTA PUBLICADO. O rascunho vive em `Omni-Saude/amh-data-platform`
(`schemas/openapi/maezo/v1/billing-status.openapi.yaml`, branch `proposta/maezo-cobranca-e-identidade-v1`).
Este adaptador e' fail-closed por construcao: ele so' sobe com os BYTES do OpenAPI cujo sha256 esta no pin
imutavel (`config/integrations/amh/contracts.lock.json`). Enquanto o artefato nao for publicado e pinado,
nao ha' digest para conferir e o construtor recusa. Isso e' o desenho, nao um defeito.

Mesma postura do `subject_context.py`: nenhum cliente HTTP, host ou credencial aqui. A E/S passa por um
executor do gateway (`GovernedBillingStatusExecutor`), sem implementacao padrao. O adaptador valida a
resposta contra o schema pinado e devolve sucesso ou recusa fechada (`PortFailureReason`); nenhuma
excecao atravessa o port, e nenhum detalhe do executor estrangeiro e' copiado (pode ter PHI).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.billing_status import BillingStatusView, BillingSummary, CompetenciaBilling
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult

ARTIFACT = "schemas/openapi/maezo/v1/billing-status.openapi.yaml"
#: Revisao 0.2.0 do MESMO contrato (manifest v1.4, decisao do DPO de 08/10/2026): mesma rota e mesmo corpo,
#: `purpose_of_use` aceita tambem `atendimento_whatsapp`. Sucede o 0.1.0 sem substitui-lo: os dois seguem
#: pinados e o construtor escolhe qual, pelo caminho do artefato (cada um com o digest do proprio bloco).
ARTIFACT_ATENDIMENTO = "schemas/openapi/maezo/v1/billing-status-atendimento.openapi.yaml"
ARTIFACTS: frozenset[str] = frozenset({ARTIFACT, ARTIFACT_ATENDIMENTO})
OPERATION = "amh.get_billing_status"
_SERVER = "/interop/billing-status/v1"
_PATH = "/subjects/{portable_subject_ref}/billing/status"
Reason = PortFailureReason


class BillingStatusContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("billing_status_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedBillingStatusRequest:
    """Descritor so' do servidor. A referencia de consentimento e' metadado de auditoria, nunca query."""

    operation: str
    path: str
    query: tuple[tuple[str, str | int], ...]
    consent_decision_ref: str
    timeout_seconds: float
    method: str = field(default="GET", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class GovernedBillingStatusResponse:
    status_code: int
    body: bytes


class GovernedBillingStatusExecutor(ABC):
    """Porta de composicao do gateway, nao um callback HTTP qualquer. Sem implementacao padrao.

    O registro em producao amarra tenant, carga de trabalho, principal, proposito/consentimento, zona PHI,
    auditoria e credencial. `execute` recusa estado nao autorizado a CADA chamada: operacao registrada nao
    e' operacao permitida. Nunca segue redirecionamento nem aceita destino vindo do navegador.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedBillingStatusRequest
    ) -> PortResult[GovernedBillingStatusResponse]: ...


class AmhBillingStatusAdapter:
    """`BillingStatusPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedBillingStatusExecutor,
        pin_path: Path | None = None,
        artifact: str = ARTIFACT,
    ) -> None:
        try:
            if not isinstance(executor, GovernedBillingStatusExecutor) or artifact not in ARTIFACTS:
                raise ValueError
            pin = load_contract_pin(pin_path)
            # Sem o bloco que pina `artifact` (v1.1 para o 0.1.0, v1.4 para o 0.2.0) a chave nao existe e o
            # construtor recusa: fail-closed por construcao, como sempre.
            if (
                len(openapi_bytes) > _MAX_BYTES
                or hashlib.sha256(openapi_bytes).hexdigest() != pin.artifact_digests[artifact]
            ):
                raise ValueError
            document = yaml.safe_load(openapi_bytes)
            if document["openapi"] != "3.0.3" or document["servers"][0]["url"] != _SERVER:
                raise ValueError
            operation = document["paths"][_PATH]["get"]
            if operation["responses"]["200"]["content"]["application/json"]["schema"] != {
                "$ref": "#/components/schemas/BillingStatus"
            }:
                raise ValueError
            definitions = _json_schema(document["components"]["schemas"])
            schema = {"$ref": "#/definitions/BillingStatus", "definitions": definitions}
            Draft4Validator.check_schema(schema)
            self._validator = Draft4Validator(schema, format_checker=_FORMATS)
            self._parameters: list[dict[str, Any]] = []
            for item in operation["parameters"]:
                if "$ref" in item:
                    pointer = item["$ref"]
                    if not pointer.startswith("#/components/parameters/"):
                        raise ValueError
                    item = document["components"]["parameters"][pointer.rsplit("/", 1)[1]]
                self._parameters.append(item)
            self._executor = executor
            self.artifact = artifact
        except Exception:
            raise BillingStatusContractError() from None

    async def get_billing_status(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        competencia: str | None = None,
        janela_meses: int = 12,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingStatusView]:
        try:
            if (
                not isinstance(consent_decision_ref, str)
                or not consent_decision_ref.strip()
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            values: dict[str, Any] = {
                "portable_subject_ref": portable_subject_ref,
                "purpose_of_use": purpose_of_use,
                "competencia": competencia,
                "janela_meses": janela_meses,
            }
            query: list[tuple[str, str | int]] = []
            for parameter in self._parameters:
                name = parameter["name"]
                value = values[name]
                if value is None and not parameter.get("required", False):
                    continue
                validator = Draft4Validator(_json_schema(parameter["schema"]), format_checker=_FORMATS)
                if not validator.is_valid(value):
                    return PortResult.refused(Reason.INVALID_REQUEST)
                if parameter["in"] == "query":
                    if not isinstance(value, (str, int)) or isinstance(value, bool):
                        return PortResult.refused(Reason.INVALID_REQUEST)
                    query.append((name, value))
            if (
                type(self._executor.registered_operations) is not frozenset
                or OPERATION not in self._executor.registered_operations
            ):
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedBillingStatusRequest(
                operation=OPERATION,
                path=_SERVER + _PATH.replace("{portable_subject_ref}", quote(portable_subject_ref, safe="")),
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
            if (
                not isinstance(received, GovernedBillingStatusResponse)
                or type(received.status_code) is not int
            ):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            status = received.status_code
            if status == 403:
                body = _json(received.body)
                reason = {
                    "consent_denied": Reason.CONSENT_REQUIRED,
                    "purpose_not_permitted": Reason.PURPOSE_DENIED,
                    "scope_not_supported": Reason.SCOPE_NOT_SUPPORTED,
                }.get(body.get("reason"), Reason.CONTRACT_VIOLATION)
                return PortResult.refused(reason)
            if status != 200:
                return PortResult.refused(
                    {
                        400: Reason.INVALID_REQUEST,
                        401: Reason.NOT_AUTHENTICATED,
                        404: Reason.NOT_FOUND,
                        429: Reason.RATE_LIMITED,
                        500: Reason.UPSTREAM_UNAVAILABLE,
                    }.get(status, Reason.CONTRACT_VIOLATION)
                )
            body = _json(received.body)
            if not self._validator.is_valid(body):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if body["portable_subject_ref"] != portable_subject_ref:
                # A resposta e' de OUTRO sujeito: nunca devolver fato de cobranca de quem nao foi pedido.
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            return PortResult.ok(_view(body))
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)


def _view(body: dict[str, Any]) -> BillingStatusView:
    resumo = body["resumo"]
    return BillingStatusView(
        portable_subject_ref=body["portable_subject_ref"],
        as_of=body["as_of"],
        fonte_atualizada_em=body["fonte_atualizada_em"],
        resumo=BillingSummary(
            status_conciliado=resumo["status_conciliado"],
            ciclos_sem_conciliacao=resumo["ciclos_sem_conciliacao"],
            valor_em_aberto=resumo["valor_em_aberto"],
            dias_atraso_max=resumo["dias_atraso_max"],
            pagador_tipo=resumo["pagador_tipo"],
            criterio_conciliacao=resumo["criterio_conciliacao"],
        ),
        competencias=tuple(
            CompetenciaBilling(
                competencia=c["competencia"],
                parcela=c["parcela"],
                vencimento=c["vencimento"],
                situacao=c["situacao"],
                valor_total=c["valor_total"],
                valor_coparticipacao=c["valor_coparticipacao"],
                valor_saldo=c["valor_saldo"],
                liquidado_em=c["liquidado_em"],
                boleto_numero_mascarado=c["boleto"]["numero_mascarado"],
                boleto_disponivel_online=c["boleto"]["disponivel_online"],
            )
            for c in body["competencias"]
        ),
        campos_ausentes=frozenset(body["qualidade"]["campos_ausentes"]),
    )
