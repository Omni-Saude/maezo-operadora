"""Resolucao do sujeito pelo DOCUMENTO (hashes) pelo contrato de identidade da AMH (DL-0084).

So' para o TELEFONE SEM CANDIDATO (decisao do dono, risco aceito contra o alerta da revisao de seguranca do
#700): `POST /interop/identity/v1/resolve-by-document` com `{cpf_hash, cpfdob_hash, hash_scheme,
purpose_of_use, amh_tenant}` -> `{resultado: "unico"|"nenhum", portable_subject_ref?, fonte_atualizada_em}`.

Contrato da AMH #214 (`schemas/openapi/maezo/v1/subject-document-resolution.openapi.yaml`, 3o artefato do
manifest v1.3, AINDA DRAFT/nao publicado): mesmo servidor `/interop/identity/v1`, mesmo esquema de hash e
mesma chave da conferencia, escopo `interop/subject.verify`. Todo nao-achado da AMH e' o mesmo 200 `nenhum`
(sem oraculo); 5 falhas por `cpf_hash` em 24 h bloqueiam o CPF na AMH;
60/min por cliente -> 429 `rate_limited`.

Mesma postura do `subject_verification.py`: fail-closed por construcao (so' sobe com os BYTES do OpenAPI cujo
sha256 esta' no bloco `manifest_v1_3` do pin imutavel), nenhum cliente HTTP aqui, E/S por um executor do
gateway sem implementacao padrao, nenhuma excecao cruza o port e nada do executor estrangeiro e' copiado.
Os hashes vao no CORPO do POST, nunca na URL; este modulo nunca ve' o CPF nem a data.

Leitura da resposta (fechada):
  * `unico` exige `portable_subject_ref` bem formada; `nenhum` exige a referencia AUSENTE ou nula.
  * `fonte_atualizada_em: null` = o indice da AMH nao esta' carregado: recusa `UPSTREAM_UNAVAILABLE` (a
    pessoa nao gasta tentativa por uma falha da AMH), qualquer que seja o `resultado`.
  * 429 (`rate_limited`: teto de 60/min por cliente M2M, nunca por CPF) = `RATE_LIMITED`; o acesso o le'
    como indisponivel. CPF bloqueado na AMH responde 200 `nenhum` (gasta tentativa no acesso).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.document_resolution import DocumentResolution
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/subject-document-resolution.openapi.yaml"
OP_RESOLVE_DOCUMENT: Final[str] = "amh.resolve_by_document"
SERVER: Final[str] = "/interop/identity/v1"
ROTA: Final[str] = "/resolve-by-document"
PATH: Final[str] = SERVER + ROTA
RESULTADOS: Final[frozenset[str]] = frozenset({"unico", "nenhum"})
#: A referencia devolvida so' vale com esta forma (a mesma da conferencia).
_REF: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
Reason = PortFailureReason

_STATUS_REASON: Final[dict[int, PortFailureReason]] = {
    400: Reason.INVALID_REQUEST,
    401: Reason.NOT_AUTHENTICATED,
    404: Reason.NOT_FOUND,
    429: Reason.RATE_LIMITED,
    500: Reason.UPSTREAM_UNAVAILABLE,
    503: Reason.UPSTREAM_UNAVAILABLE,
}
_FORBIDDEN_REASON: Final[dict[str, PortFailureReason]] = {
    "consent_denied": Reason.CONSENT_REQUIRED,
    "purpose_not_permitted": Reason.PURPOSE_DENIED,
    "scope_not_supported": Reason.SCOPE_NOT_SUPPORTED,
}


class DocumentResolutionContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("document_resolution_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedDocumentResolutionRequest:
    """Descritor so' do servidor. `body` leva os dois hashes no POST; o repr nunca os mostra."""

    operation: str
    method: str
    path: str
    body: bytes
    timeout_seconds: float


@dataclass(frozen=True, slots=True, repr=False)
class GovernedDocumentResolutionResponse:
    status_code: int
    body: bytes


class GovernedDocumentResolutionExecutor(ABC):
    """Porta de composicao do gateway, nao um callback HTTP qualquer. Sem implementacao padrao.

    `execute` recusa estado nao autorizado a CADA chamada. Nunca segue redirecionamento, nunca registra corpo
    nem hash, nunca devolve resposta de cache.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedDocumentResolutionRequest
    ) -> PortResult[GovernedDocumentResolutionResponse]: ...


def _schema_referenciado(node: Any) -> str:
    ref = node["content"]["application/json"]["schema"]["$ref"]
    prefixo = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefixo) or len(ref) == len(prefixo):
        raise ValueError
    return ref[len(prefixo) :]


class AmhDocumentResolutionAdapter:
    """`DocumentResolutionPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedDocumentResolutionExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedDocumentResolutionExecutor):
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
            post = document["paths"][ROTA]["post"]
            nome_corpo = _schema_referenciado(post["requestBody"])
            nome_resposta = _schema_referenciado(post["responses"]["200"])
            definitions = _json_schema(document["components"]["schemas"])
            self._validators: dict[str, Draft4Validator] = {}
            for chave, nome in (("pedido", nome_corpo), ("resposta", nome_resposta)):
                schema = {"$ref": "#/definitions/" + nome, "definitions": definitions}
                Draft4Validator.check_schema(schema)
                self._validators[chave] = Draft4Validator(schema, format_checker=_FORMATS)
            self._executor = executor
        except Exception:
            raise DocumentResolutionContractError() from None

    async def resolve_by_document(
        self,
        *,
        document_hash: str,
        document_dob_hash: str,
        hash_scheme: str,
        purpose_of_use: str,
        amh_tenant: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[DocumentResolution]:
        try:
            if (
                type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
                or type(document_hash) is not str
                or type(document_dob_hash) is not str
                or document_hash == document_dob_hash
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            corpo = {
                "cpf_hash": document_hash,
                "cpfdob_hash": document_dob_hash,
                "hash_scheme": hash_scheme,
                "purpose_of_use": purpose_of_use,
                "amh_tenant": amh_tenant,
            }
            if not self._validators["pedido"].is_valid(corpo):
                return PortResult.refused(Reason.INVALID_REQUEST)
            registradas = self._executor.registered_operations
            if type(registradas) is not frozenset or OP_RESOLVE_DOCUMENT not in registradas:
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedDocumentResolutionRequest(
                operation=OP_RESOLVE_DOCUMENT,
                method="POST",
                path=PATH,
                body=json.dumps(corpo, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
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
        except TimeoutError:
            return PortResult.refused(Reason.TIMEOUT)
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        try:
            recebido = response.value
            if not isinstance(recebido, GovernedDocumentResolutionResponse) or (
                type(recebido.status_code) is not int
            ):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            status = recebido.status_code
            if status == 403:
                return PortResult.refused(
                    _FORBIDDEN_REASON.get(_json(recebido.body).get("reason"), Reason.CONTRACT_VIOLATION)
                )
            if status != 200:
                return PortResult.refused(_STATUS_REASON.get(status, Reason.CONTRACT_VIOLATION))
            body = _json(recebido.body)
            if not self._validators["resposta"].is_valid(body):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if "fonte_atualizada_em" not in body:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if body["fonte_atualizada_em"] is None:
                # Indice da AMH fora: dependencia, nunca "nenhum". Nao gasta tentativa.
                return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
            resultado = body.get("resultado")
            ref = body.get("portable_subject_ref")
            if resultado == "unico" and type(ref) is str and _REF.fullmatch(ref):
                return PortResult.ok(DocumentResolution(resultado="unico", portable_subject_ref=ref))
            if resultado == "nenhum" and ref is None:
                return PortResult.ok(DocumentResolution(resultado="nenhum", portable_subject_ref=None))
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)


__all__ = [
    "ARTIFACT",
    "OP_RESOLVE_DOCUMENT",
    "PATH",
    "RESULTADOS",
    "AmhDocumentResolutionAdapter",
    "DocumentResolutionContractError",
    "GovernedDocumentResolutionExecutor",
    "GovernedDocumentResolutionRequest",
    "GovernedDocumentResolutionResponse",
]
