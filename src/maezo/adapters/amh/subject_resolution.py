"""Resolucao de sujeito e perfil minimo pelo contrato `subject-resolution` da AMH (ADR-0037 XRD-05/06).

O CONTRATO AINDA NAO ESTA PUBLICADO (rascunho em `Omni-Saude/amh-data-platform`,
`schemas/openapi/maezo/v1/subject-resolution.openapi.yaml`, branch `proposta/maezo-cobranca-e-identidade-v1`).
Mesma postura do `billing_status.py` e do `subject_context.py`: fail-closed por construcao (so' sobe com os
bytes do OpenAPI cujo sha256 esta no pin imutavel; hoje nao ha' digest, entao o construtor recusa), nenhum
cliente HTTP aqui, E/S por um executor do gateway sem implementacao padrao, nenhuma excecao cruza o port e
nada do executor estrangeiro e' copiado.

DUAS OPERACOES, de forma diferente:
  * `resolve_by_phone` e' POST: o hash do telefone vai no CORPO, nunca na URL nem na query (nao entra em log
    de acesso). Nao leva `consent_decision_ref`: o consentimento e' por sujeito e o sujeito ainda e'
    desconhecido; a AMH o avalia do lado dela, fail-closed.
  * `get_profile` e' GET por `portable_subject_ref`, com a referencia de consentimento so' como metadado de
    auditoria do executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult
from maezo.ports.subject_resolution import PlanSummary, SubjectCandidate, SubjectProfile, SubjectResolution

ARTIFACT = "schemas/openapi/maezo/v1/subject-resolution.openapi.yaml"
OP_RESOLVE = "amh.resolve_subject_by_phone"
OP_PROFILE = "amh.get_subject_profile"
_SERVER = "/interop/subject-resolution/v1"
_PATH_RESOLVE = "/subjects/resolve-by-phone"
_PATH_PROFILE = "/subjects/{portable_subject_ref}/profile"
Reason = PortFailureReason


class SubjectResolutionContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("subject_resolution_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectResolutionRequest:
    """Descritor so' do servidor. `body` leva o hash do telefone no POST; repr nunca o mostra."""

    operation: str
    method: str
    path: str
    query: tuple[tuple[str, str | int], ...]
    body: bytes | None
    consent_decision_ref: str | None
    timeout_seconds: float


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectResolutionResponse:
    status_code: int
    body: bytes


class GovernedSubjectResolutionExecutor(ABC):
    """Porta de composicao do gateway, nao um callback HTTP qualquer. Sem implementacao padrao.

    O registro em producao amarra tenant, carga de trabalho, principal, proposito, zona PHI, auditoria e
    credencial; `execute` recusa estado nao autorizado a CADA chamada. Nunca segue redirecionamento, nunca
    registra corpo nem hash de telefone, nunca devolve resposta de cache de outro sujeito ou proposito.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedSubjectResolutionRequest
    ) -> PortResult[GovernedSubjectResolutionResponse]: ...


_STATUS_REASON = {
    400: Reason.INVALID_REQUEST,
    401: Reason.NOT_AUTHENTICATED,
    404: Reason.NOT_FOUND,
    429: Reason.RATE_LIMITED,
    500: Reason.UPSTREAM_UNAVAILABLE,
}
_FORBIDDEN_REASON = {
    "consent_denied": Reason.CONSENT_REQUIRED,
    "purpose_not_permitted": Reason.PURPOSE_DENIED,
    "scope_not_supported": Reason.SCOPE_NOT_SUPPORTED,
}


class AmhSubjectResolutionAdapter:
    """`SubjectResolutionPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedSubjectResolutionExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedSubjectResolutionExecutor):
                raise ValueError
            pin = load_contract_pin(pin_path)
            if (
                len(openapi_bytes) > _MAX_BYTES
                or hashlib.sha256(openapi_bytes).hexdigest() != pin.artifact_digests[ARTIFACT]
            ):
                raise ValueError
            document = yaml.safe_load(openapi_bytes)
            if document["openapi"] != "3.0.3" or document["servers"][0]["url"] != _SERVER:
                raise ValueError
            post = document["paths"][_PATH_RESOLVE]["post"]
            get = document["paths"][_PATH_PROFILE]["get"]
            if (
                post["requestBody"]["content"]["application/json"]["schema"]
                != {"$ref": "#/components/schemas/ResolveByPhoneRequest"}
                or post["responses"]["200"]["content"]["application/json"]["schema"]
                != {"$ref": "#/components/schemas/ResolveByPhoneResponse"}
                or get["responses"]["200"]["content"]["application/json"]["schema"]
                != {"$ref": "#/components/schemas/SubjectProfile"}
            ):
                raise ValueError
            definitions = _json_schema(document["components"]["schemas"])
            self._validators = {}
            for nome in ("ResolveByPhoneRequest", "ResolveByPhoneResponse", "SubjectProfile"):
                schema = {"$ref": "#/definitions/" + nome, "definitions": definitions}
                Draft4Validator.check_schema(schema)
                self._validators[nome] = Draft4Validator(schema, format_checker=_FORMATS)
            self._get_parameters: list[dict[str, Any]] = []
            for item in get["parameters"]:
                if "$ref" in item:
                    pointer = item["$ref"]
                    if not pointer.startswith("#/components/parameters/"):
                        raise ValueError
                    item = document["components"]["parameters"][pointer.rsplit("/", 1)[1]]
                self._get_parameters.append(item)
            self._executor = executor
        except Exception:
            raise SubjectResolutionContractError() from None

    @staticmethod
    def _prazo_valido(timeout_seconds: Any) -> bool:
        return (
            type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds) and timeout_seconds > 0
        )

    def _registrada(self, operation: str) -> bool:
        registered = self._executor.registered_operations
        return type(registered) is frozenset and operation in registered

    async def _despachar(
        self, request: GovernedSubjectResolutionRequest, timeout_seconds: float
    ) -> PortResult[GovernedSubjectResolutionResponse]:
        try:
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
            return response
        except TimeoutError:
            return PortResult.refused(Reason.TIMEOUT)
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)

    def _corpo(self, resposta: GovernedSubjectResolutionResponse, nome: str) -> tuple[Any, Reason | None]:
        """O corpo validado, ou a recusa que o status HTTP e o schema impõem."""
        if type(resposta.status_code) is not int:
            return None, Reason.CONTRACT_VIOLATION
        status = resposta.status_code
        if status == 403:
            body = _json(resposta.body)
            return None, _FORBIDDEN_REASON.get(body.get("reason"), Reason.CONTRACT_VIOLATION)
        if status != 200:
            return None, _STATUS_REASON.get(status, Reason.CONTRACT_VIOLATION)
        body = _json(resposta.body)
        if not self._validators[nome].is_valid(body):
            return None, Reason.CONTRACT_VIOLATION
        return body, None

    async def resolve_by_phone(
        self,
        contact_pseudonym: str,
        *,
        amh_tenant: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectResolution]:
        try:
            if not self._prazo_valido(timeout_seconds):
                return PortResult.refused(Reason.INVALID_REQUEST)
            corpo = {
                "amh_tenant": amh_tenant,
                "phone_hash": contact_pseudonym,  # nome do campo no contrato da AMH
                "hash_scheme": hash_scheme,
                "purpose_of_use": purpose_of_use,
            }
            if not self._validators["ResolveByPhoneRequest"].is_valid(corpo):
                return PortResult.refused(Reason.INVALID_REQUEST)
            if not self._registrada(OP_RESOLVE):
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedSubjectResolutionRequest(
                operation=OP_RESOLVE,
                method="POST",
                path=_SERVER + _PATH_RESOLVE,
                query=(),
                body=json.dumps(corpo, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                consent_decision_ref=None,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        enviado = await self._despachar(request, timeout_seconds)
        if not enviado.succeeded:
            return PortResult.refused(
                enviado.failure.reason if enviado.failure else Reason.CONTRACT_VIOLATION
            )
        try:
            recebido = enviado.value
            if not isinstance(recebido, GovernedSubjectResolutionResponse):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            body, recusa = self._corpo(recebido, "ResolveByPhoneResponse")
            if recusa is not None:
                return PortResult.refused(recusa)
            candidatos = tuple(
                SubjectCandidate(
                    portable_subject_ref=c["portable_subject_ref"],
                    relacao=c["relacao"],
                    vigencia_ativa=c["vigencia_ativa"],
                )
                for c in body["candidatos"]
            )
            # Coerencia do proprio contrato: o rotulo tem de dizer quantos candidatos vieram.
            esperado = {0: "nenhum", 1: "unico"}.get(len(candidatos), "multiplos")
            if body["resultado"] != esperado:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            return PortResult.ok(
                SubjectResolution(
                    resultado=body["resultado"],
                    candidatos=candidatos,
                    campos_ausentes=frozenset(body["qualidade"]["campos_ausentes"]),
                )
            )
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)

    async def get_profile(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectProfile]:
        try:
            if (
                not isinstance(consent_decision_ref, str)
                or not consent_decision_ref.strip()
                or not self._prazo_valido(timeout_seconds)
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            values = {"portable_subject_ref": portable_subject_ref, "purpose_of_use": purpose_of_use}
            query: list[tuple[str, str | int]] = []
            for parameter in self._get_parameters:
                value = values[parameter["name"]]
                validator = Draft4Validator(_json_schema(parameter["schema"]), format_checker=_FORMATS)
                if not validator.is_valid(value):
                    return PortResult.refused(Reason.INVALID_REQUEST)
                if parameter["in"] == "query":
                    query.append((parameter["name"], value))
            if not self._registrada(OP_PROFILE):
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedSubjectResolutionRequest(
                operation=OP_PROFILE,
                method="GET",
                path=_SERVER
                + _PATH_PROFILE.replace("{portable_subject_ref}", quote(portable_subject_ref, safe="")),
                query=tuple(query),
                body=None,
                consent_decision_ref=consent_decision_ref,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        enviado = await self._despachar(request, timeout_seconds)
        if not enviado.succeeded:
            return PortResult.refused(
                enviado.failure.reason if enviado.failure else Reason.CONTRACT_VIOLATION
            )
        try:
            recebido = enviado.value
            if not isinstance(recebido, GovernedSubjectResolutionResponse):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            body, recusa = self._corpo(recebido, "SubjectProfile")
            if recusa is not None:
                return PortResult.refused(recusa)
            if body["portable_subject_ref"] != portable_subject_ref:
                # O perfil e' de OUTRO sujeito: nunca devolver idade ou plano de quem nao foi pedido.
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            plano = body["plano"]
            return PortResult.ok(
                SubjectProfile(
                    portable_subject_ref=body["portable_subject_ref"],
                    idade_anos=body["idade_anos"],
                    idade_meses=body["idade_meses"],
                    plano=PlanSummary(
                        ativo=plano["ativo"],
                        vigencia_inicio=plano["vigencia_inicio"],
                        vigencia_fim=plano["vigencia_fim"],
                        carencia_vigente=plano["carencia_vigente"],
                    ),
                    titular_ref=body["titular_ref"],
                    fonte_atualizada_em=body["fonte_atualizada_em"],
                    campos_ausentes=frozenset(body["qualidade"]["campos_ausentes"]),
                )
            )
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
