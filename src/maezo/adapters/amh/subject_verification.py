"""Conferencia de sujeito por fator (hash) pelo contrato `subject-verification` da AMH (DL-0083).

O CONTRATO AINDA NAO ESTA PUBLICADO (esta' sendo escrito na AMH, #212: `POST
/interop/identity/v1/subjects/{ref}/verify`, escopo `interop/subject.verify`). CONFERE, NAO RESOLVE: a
referencia candidata vai NA ROTA (a do telefone) e a resposta e' so' `confere` + o eco da mesma referencia;
um eco diferente e' violacao de contrato, nunca "outra pessoa". Mesma postura do
`subject_resolution.py`: fail-closed por construcao (so' sobe com os BYTES do OpenAPI cujo sha256 esta' no
bloco `manifest_v1_3` do pin imutavel; sem o bloco nao ha' digest e o construtor recusa), nenhum cliente
HTTP aqui, E/S por um executor do gateway sem implementacao padrao, nenhuma excecao cruza o port e nada do
executor estrangeiro e' copiado.

O HASH DO FATOR vai no CORPO do POST, nunca na URL nem na query. Este modulo nunca ve' o CPF nem a data de
nascimento: recebe o hash ja' calculado (`gateway/amh_interop.py::AmhSubjectVerifyHasher`, no recebimento).

O adaptador NAO assume os nomes dos schemas do contrato nem o nome do parametro da rota: o schema do corpo e o
da resposta 200 sao os que a propria operacao `post` referencia (`$ref`), a rota e' a UNICA da forma
`/subjects/{<parametro>}/verify`, e a resposta so' vale com `confere` booleano e o eco EXATO da referencia.
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
from urllib.parse import quote

import yaml
from jsonschema import Draft4Validator

from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_context import _FORMATS, _MAX_BYTES, _json, _json_schema
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult
from maezo.ports.subject_verification import SubjectVerification

ARTIFACT: Final[str] = "schemas/openapi/maezo/v1/subject-verification.openapi.yaml"
OP_VERIFY: Final[str] = "amh.verify_subject"
SERVER: Final[str] = "/interop/identity/v1"
PATH_PREFIX: Final[str] = "/subjects/"
PATH_SUFFIX: Final[str] = "/verify"
#: `cpf` (telefone de UMA pessoa) e `cpfdob` (CPF + nascimento, telefone de mais de uma pessoa).
FATORES: Final[frozenset[str]] = frozenset({"cpf", "cpfdob"})
_ROTA: Final[re.Pattern[str]] = re.compile(r"/subjects/\{[A-Za-z_][A-Za-z0-9_]{0,63}\}/verify\Z")
#: A referencia candidata so' entra na rota com esta forma (a mesma que o executor confere de novo).
_REF: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
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
            rotas = [nome for nome in document["paths"] if isinstance(nome, str) and _ROTA.fullmatch(nome)]
            if len(rotas) != 1:
                raise ValueError
            post = document["paths"][rotas[0]]["post"]
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
        portable_subject_ref: str,
        *,
        fator: str,
        verification_hash: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectVerification]:
        try:
            if (
                type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
                or fator not in FATORES
                or type(portable_subject_ref) is not str
                or not _REF.fullmatch(portable_subject_ref)
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            corpo = {
                "fator": fator,
                "verification_hash": verification_hash,
                "hash_scheme": hash_scheme,
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
                path=SERVER + PATH_PREFIX + quote(portable_subject_ref, safe="") + PATH_SUFFIX,
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
            confere = body["confere"]
            eco = body["portable_subject_ref"]
            # CONFERE, NAO RESOLVE: o veredito so' vale para a referencia PERGUNTADA. Um eco diferente nunca
            # e' lido como "e' outra pessoa": e' resposta fora do contrato.
            if type(confere) is not bool or eco != portable_subject_ref:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            # #212: `fonte_atualizada_em: null` = o indice de verificacao da AMH nao esta' carregado, e entao
            # `confere` e' SEMPRE `false`. Isso e' dependencia fora, nao "nao confere": lido como recusa, a
            # pessoa nao gasta tentativa por uma falha da AMH.
            if "fonte_atualizada_em" in body and body["fonte_atualizada_em"] is None:
                return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
            return PortResult.ok(SubjectVerification(portable_subject_ref=eco, confere=confere))
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
