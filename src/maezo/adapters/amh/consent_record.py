"""Registro de consentimento pelo contrato `consent-record` da AMH (DL-0083).

O CONTRATO AINDA NAO ESTA PUBLICADO (esta' sendo escrito na AMH: `POST /interop/consent/v1/consents`, escopo
`interop/consent.write`). Mesma postura do `subject_verification.py`: fail-closed por construcao (so' sobe
com os BYTES do OpenAPI cujo sha256 esta' no bloco `manifest_v1_3` do pin imutavel), nenhum cliente HTTP
aqui, E/S por um executor do gateway sem implementacao padrao, nenhuma excecao cruza o port, nada do
executor e' copiado.

E' uma ESCRITA idempotente: a chave de idempotencia deterministica vai no corpo, e repetir a mesma chave
devolve o mesmo `consent_ref` (`recorded=false`). Sem retry aqui: quem chama decide.
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
from maezo.ports.consent_record import ConsentRecordReceipt
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/consent-record.openapi.yaml"
OP_RECORD: Final[str] = "amh.record_consent"
SERVER: Final[str] = "/interop/consent/v1"
PATH: Final[str] = "/consents"
DECISOES: Final[frozenset[str]] = frozenset({"granted", "revoked"})
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


class ConsentRecordContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("consent_record_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedConsentRecordRequest:
    operation: str
    method: str
    path: str
    body: bytes
    timeout_seconds: float


@dataclass(frozen=True, slots=True, repr=False)
class GovernedConsentRecordResponse:
    status_code: int
    body: bytes


class GovernedConsentRecordExecutor(ABC):
    """Porta de composicao do gateway. Sem implementacao padrao; nunca segue redirecionamento."""

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedConsentRecordRequest
    ) -> PortResult[GovernedConsentRecordResponse]: ...


def _schema_referenciado(node: Any) -> str:
    ref = node["content"]["application/json"]["schema"]["$ref"]
    prefixo = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefixo) or len(ref) == len(prefixo):
        raise ValueError
    return ref[len(prefixo) :]


class AmhConsentRecordAdapter:
    """`ConsentRecordPort` concreto, com a E/S delegada ao executor do gateway a cada chamada."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedConsentRecordExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedConsentRecordExecutor):
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
            raise ConsentRecordContractError() from None

    async def record(
        self,
        portable_subject_ref: str,
        *,
        amh_tenant: str,
        scope: str,
        decision: str,
        consent_text_version: str,
        consent_text_sha256: str,
        decided_at: str,
        channel: str,
        idempotency_key: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ConsentRecordReceipt]:
        try:
            if (
                type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
                or decision not in DECISOES
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            corpo = {
                "amh_tenant": amh_tenant,
                "portable_subject_ref": portable_subject_ref,
                "scope": scope,
                "decision": decision,
                "consent_text_version": consent_text_version,
                "consent_text_sha256": consent_text_sha256,
                "decided_at": decided_at,
                "channel": channel,
                "idempotency_key": idempotency_key,
                "purpose_of_use": purpose_of_use,
            }
            if not self._validators["pedido"].is_valid(corpo):
                return PortResult.refused(Reason.INVALID_REQUEST)
            registradas = self._executor.registered_operations
            if type(registradas) is not frozenset or OP_RECORD not in registradas:
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedConsentRecordRequest(
                operation=OP_RECORD,
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
                return PortResult.refused(failure.reason)
        except TimeoutError:
            return PortResult.refused(Reason.TIMEOUT)
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        try:
            recebido = response.value
            if not isinstance(recebido, GovernedConsentRecordResponse) or (
                type(recebido.status_code) is not int
            ):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            status = recebido.status_code
            if status == 403:
                return PortResult.refused(
                    _FORBIDDEN_REASON.get(_json(recebido.body).get("reason"), Reason.CONTRACT_VIOLATION)
                )
            if status not in (200, 201):
                return PortResult.refused(_STATUS_REASON.get(status, Reason.CONTRACT_VIOLATION))
            body = _json(recebido.body)
            if not self._validators["resposta"].is_valid(body):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            consent_ref = body["consent_ref"]
            recorded = body["recorded"]
            if not (isinstance(consent_ref, str) and consent_ref) or type(recorded) is not bool:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            return PortResult.ok(ConsentRecordReceipt(consent_ref=consent_ref, recorded=recorded))
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
