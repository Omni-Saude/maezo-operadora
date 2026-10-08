"""Verificacao de sujeito por fator (hash) pelo contrato `subject-verification` da AMH (DL-0083).

O CONTRATO AINDA NAO ESTA PUBLICADO (esta' sendo escrito na AMH: `POST
/interop/subject-verification/v1/subjects/verify`, escopo `interop/subject.verify`). Mesma postura do
`subject_resolution.py`: fail-closed por construcao (so' sobe com os BYTES do OpenAPI cujo sha256 esta' no
bloco `manifest_v1_3` do pin imutavel; sem o bloco nao ha' digest e o construtor recusa), nenhum cliente
HTTP aqui, E/S por um executor do gateway sem implementacao padrao, nenhuma excecao cruza o port e nada do
executor estrangeiro e' copiado.

O HASH DO FATOR vai no CORPO do POST, nunca na URL nem na query. Este modulo nunca ve' o CPF nem a data de
nascimento: recebe o hash ja' calculado (`gateway/amh_interop.py::AmhSubjectVerifyHasher`, no recebimento).

O adaptador NAO assume os nomes dos schemas do contrato: o schema do corpo e o da resposta 200 sao os que a
propria operacao `post` referencia (`$ref`), e a resposta so' vale com `verificado` booleano e, quando
verdadeiro, a referencia opaca.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult
from maezo.ports.subject_verification import SubjectVerification

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/subject-verification.openapi.yaml"
OP_VERIFY: Final[str] = "amh.verify_subject"
SERVER: Final[str] = "/interop/subject-verification/v1"
PATH: Final[str] = "/subjects/verify"
FATORES: Final[frozenset[str]] = frozenset({"cpf", "cpf_nascimento"})
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


class SubjectVerificationContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("subject_verification_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectVerificationRequest:
    """Descritor so' do servidor. `body` leva o hash do fator no POST; o repr nunca o mostra."""

    operation: str
    method: str
    path: str
    body: bytes
    timeout_seconds: float


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectVerificationResponse:
    status_code: int
    body: bytes


class GovernedSubjectVerificationExecutor(ABC):
    """Porta de composicao do gateway, nao um callback HTTP qualquer. Sem implementacao padrao.

    `execute` recusa estado nao autorizado a CADA chamada. Nunca segue redirecionamento, nunca registra corpo
    nem hash de fator, nunca devolve resposta de cache de outro sujeito ou proposito.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedSubjectVerificationRequest
    ) -> PortResult[GovernedSubjectVerificationResponse]: ...


def _schema_referenciado(node: Any) -> str:
    """O nome do schema que um no' `{content: {application/json: {schema: {$ref}}}}` referencia."""
    ref = node["content"]["application/json"]["schema"]["$ref"]
    prefixo = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefixo) or len(ref) == len(prefixo):
        raise ValueError
    return ref[len(prefixo) :]


class AmhSubjectVerificationAdapter:
    """`SubjectVerificationPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedSubjectVerificationExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedSubjectVerificationExecutor):
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
            post = document["paths"][PATH]["post"]
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
            raise SubjectVerificationContractError() from None

    async def verify(
        self,
        verification_hash: str,
        *,
        amh_tenant: str,
        hash_scheme: str,
        factor: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectVerification]:
        try:
            if (
                type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
                or factor not in FATORES
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            corpo = {
                "amh_tenant": amh_tenant,
                "hash_scheme": hash_scheme,
                "factor": factor,
                "verification_hash": verification_hash,
                "purpose_of_use": purpose_of_use,
            }
            if not self._validators["pedido"].is_valid(corpo):
                return PortResult.refused(Reason.INVALID_REQUEST)
            registradas = self._executor.registered_operations
            if type(registradas) is not frozenset or OP_VERIFY not in registradas:
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedSubjectVerificationRequest(
                operation=OP_VERIFY,
                method="POST",
                path=SERVER + PATH,
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
            if not isinstance(recebido, GovernedSubjectVerificationResponse) or (
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
            verificado = body["verificado"]
            ref = body.get("portable_subject_ref")
            # Coerencia do proprio contrato: verificado sem referencia nao e' um veredito utilizavel.
            if type(verificado) is not bool or (verificado and not (isinstance(ref, str) and ref)):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            return PortResult.ok(
                SubjectVerification(verificado=verificado, portable_subject_ref=ref if verificado else None)
            )
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
