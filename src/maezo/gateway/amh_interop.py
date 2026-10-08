"""Executores do gateway para os contratos AMH de cobranca e identidade do Lucas (ADR-0037 XRD-04/05).

O QUE ISTO E'. A E/S concreta que os adaptadores `adapters/amh/billing_status.py` e
`adapters/amh/subject_resolution.py` deixam, de proposito, sem implementacao padrao. Composto SO' por
`gateway.tool_registry.build_amh_interop` e SO' quando `MAEZO_LUCAS_FONTE_COBRANCA=amh`. Os adaptadores
continuam fail-closed por construcao: sem o OpenAPI publicado e pinado (XRG-2/XRG-3), nada sobe.

O SERVICO DO OUTRO LADO e' interno da AMH (ALB interno, `http://` dentro da VPC por enquanto), com
OAuth2 `client_credentials` do Cognito. Tres rotas FIXAS, montadas aqui a partir da operacao do
pedido — nunca uma URL vinda do chamador:

  * `GET  /interop/billing-status/v1/subjects/{ref}/billing/status`      (`interop/billing.read`)
  * `POST /interop/subject-resolution/v1/subjects/resolve-by-phone`      (`interop/subject.resolve`)
  * `GET  /interop/subject-resolution/v1/subjects/{ref}/profile`         (`interop/profile.read`)

e, SO' com `MAEZO_HELENA_CONSULTAS_AMH` ligada (DL de 07/10/2026, fatos do plano na Helena), mais tres
leituras do contrato TINA (manifest aditivo v1.2, ainda DRAFT — sem o bloco `manifest_v1_2` no pin o
adaptador recusa subir):

  * `GET  /interop/tina/v1/subjects/{ref}/elegibilidade`                 (`interop/tina.read`)
  * `GET  /interop/tina/v1/subjects/{ref}/carencias`                     (`interop/tina.read`)
  * `GET  /interop/tina/v1/subjects/{ref}/requisicoes`                   (`interop/tina.read`)

DECISOES DO DONO (06/10/2026, `docs/decisions-log.md`), cada uma diferente de `gateway/amh.py`:

  A. SOMBRA. Cada chamada passa por `gate(seam, operacao)` com a semantica ORDINARIA de
     `gateway/seams/_base.py`: levanta so' numa negacao ENFORCED; com o manifesto em `DRAFT`/`shadow`
     a chamada segue e a decisao fica registrada. NAO se copia a exigencia estrita de `amh.py`
     (`decision.allow and reason == APPROVED`), que deixaria a fonte morta ate' a aprovacao da classe.
  B. ZONA GERAL. Cobranca/identidade do Lucas e' Zona Geral (`security_zone: general`); o dado que
     volta nao tem identificador cru (so' `portable_subject_ref` opaco e boleto mascarado). O executor
     exige `phi_zone == general` — o inverso da checagem de `amh.py`.
  C. BASE LEGAL. A referencia que chega como `consent_decision_ref` e' so' metadado de auditoria (o
     hash dela entra no registro duravel); nunca vai na URL. Hoje e' a base legal fixa de execucao de
     contrato, nao um consentimento — ver `agents/lucas/identidade_amh.py`.
  D. HASH DO TELEFONE `amh-phone-lookup-v1` = HMAC-SHA256(chave dedicada, "{amh_tenant}:{E164}") em
     hex (`AmhPhoneLookupHasher`). Calculado no RECEBIMENTO, onde o numero cru ainda existe, e
     descartado no fim do turno. Nunca e' logado nem persistido; vai so' no CORPO do POST.

O QUE O EXECUTOR GARANTE A CADA CHAMADA: pedido do tipo e da forma exatos da operacao registrada,
proposito/tenant iguais aos da composicao, prazo limitado, gate, token, auditoria duravel ANTES do
despacho, transporte sem proxy de ambiente/sem retry/sem redirect, corpo de resposta limitado e
recusa FECHADA (`PortFailureReason`) para tudo — nenhum detalhe de excecao, corpo, token ou segredo
atravessa nem vai para log.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import quote, unquote, urlsplit

import httpx
import structlog

from maezo.adapters.amh.billing_status import (
    OPERATION as OP_BILLING,
)
from maezo.adapters.amh.billing_status import (
    AmhBillingStatusAdapter,
    GovernedBillingStatusExecutor,
    GovernedBillingStatusRequest,
    GovernedBillingStatusResponse,
)
from maezo.adapters.amh.subject_resolution import (
    OP_PROFILE,
    OP_RESOLVE,
    AmhSubjectResolutionAdapter,
    GovernedSubjectResolutionExecutor,
    GovernedSubjectResolutionRequest,
    GovernedSubjectResolutionResponse,
)
from maezo.adapters.amh.tina import (
    OP_CARENCIAS,
    OP_ELEGIBILIDADE,
    OP_REQUISICOES,
    AmhTinaAdapter,
    GovernedTinaExecutor,
    GovernedTinaRequest,
    GovernedTinaResponse,
)
from maezo.gateway.audit import AuditRecord, hash_input
from maezo.gateway.effect_pep import PHI_ZONE_GENERAL
from maezo.gateway.rate_limit import REASON_RATE_LIMITED
from maezo.gateway.seams._base import EffectDeniedError, SeamContext, gate
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult

logger = structlog.get_logger(__name__)

#: Esquema do hash do telefone que a AMH resolve (decisao D). Nome do contrato, nao nosso.
HASH_SCHEME: Final[str] = "amh-phone-lookup-v1"
#: Escopo das tres leituras TINA (fatos do plano). Pedido SO' com as consultas da Helena ligadas.
SCOPE_TINA: Final[str] = "interop/tina.read"
#: Escopos OAuth2 de cada operacao (servidor de recursos `interop` no Cognito da AMH).
SCOPE_BY_OPERATION: Final[dict[str, str]] = {
    OP_BILLING: "interop/billing.read",
    OP_RESOLVE: "interop/subject.resolve",
    OP_PROFILE: "interop/profile.read",
    OP_ELEGIBILIDADE: SCOPE_TINA,
    OP_CARENCIAS: SCOPE_TINA,
    OP_REQUISICOES: SCOPE_TINA,
}

_MAX_RESPONSE_BYTES: Final[int] = 1_048_576
_MAX_TOKEN_RESPONSE_BYTES: Final[int] = 65_536
_MAX_REQUEST_BODY_BYTES: Final[int] = 4_096
_MAX_TIMEOUT_SECONDS: Final[float] = 30.0  # teto tecnico de E/S; nada de negocio e' inferido daqui
_TOKEN_REFRESH_MARGIN_S: Final[float] = 120.0
_MAX_TOKEN_LIFETIME_S: Final[int] = 86_400

_BILLING_PREFIX: Final[str] = "/interop/billing-status/v1/subjects/"
_BILLING_SUFFIX: Final[str] = "/billing/status"
_RESOLVE_PATH: Final[str] = "/interop/subject-resolution/v1/subjects/resolve-by-phone"
_PROFILE_PREFIX: Final[str] = "/interop/subject-resolution/v1/subjects/"
_PROFILE_SUFFIX: Final[str] = "/profile"
_TINA_PREFIX: Final[str] = "/interop/tina/v1/subjects/"
#: Operacao TINA -> sufixo FIXO da rota (o sujeito vem entre o prefixo e o sufixo, sempre conferido).
_TINA_SUFFIX: Final[dict[str, str]] = {
    OP_ELEGIBILIDADE: "/elegibilidade",
    OP_CARENCIAS: "/carencias",
    OP_REQUISICOES: "/requisicoes",
}
#: Query permitida por operacao TINA (`purpose_of_use` sempre obrigatorio e igual ao da composicao).
_TINA_QUERY: Final[dict[str, frozenset[str]]] = {
    OP_ELEGIBILIDADE: frozenset({"purpose_of_use"}),
    OP_CARENCIAS: frozenset({"purpose_of_use", "limite", "pendentes"}),
    OP_REQUISICOES: frozenset({"purpose_of_use", "limite"}),
}

_SUBJECT: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_VERSION: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_CLIENT_ID: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SCOPE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_COMPETENCIA: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])\Z")
_PHONE_HASH: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_HTTPCORE_LOGGERS: Final[tuple[str, ...]] = (
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
)
#: Telefone BR em E.164 so' com digitos: 55 + DDD (sem zero a esquerda) + 8 ou 9 digitos.
_E164_BR: Final[re.Pattern[str]] = re.compile(r"55[1-9][0-9][0-9]{8,9}\Z")
_MIN_PHONE_KEY_CHARS: Final[int] = 16

TransportFactory = Callable[[], httpx.AsyncBaseTransport]


class AmhInteropCompositionError(ValueError):
    """Composicao invalida. A mensagem e' um token fixo: nunca ecoa URL, id ou segredo."""

    def __init__(self, motivo: str = "amh_interop_binding_invalid") -> None:
        super().__init__(motivo)


class InteropAuditSink(Protocol):
    """O sink duravel ADR-0007 (`PostgresAuditSink`): `emit` devolve o hash do elo gravado."""

    def emit(self, record: AuditRecord) -> Awaitable[str]: ...


def _transporte_padrao() -> httpx.AsyncBaseTransport:
    # API publica de transporte: sem o log INFO com URL do AsyncClient, sem proxy do ambiente,
    # sem retry, sem cookie e sem redirect (o transporte nunca segue 3xx).
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False)


def _httpcore_em_debug() -> bool:
    # Traces DEBUG do httpcore incluem cabecalhos crus (Authorization). Recusa-se o transporte em vez
    # de mexer na configuracao global de log.
    return any(logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in _HTTPCORE_LOGGERS)


def validar_origem(origin: str) -> str:
    """`http(s)://host[:porta]` sem caminho, query, fragmento ou credencial. Devolve sem `/` final.

    `http://` e' aceito porque o servico e' um ALB INTERNO dentro da VPC (decisao do dono); a
    credencial que viaja e' o bearer de curta duracao, nunca o segredo do cliente.
    """
    if type(origin) is not str or not origin.isascii():
        raise AmhInteropCompositionError()
    candidato = origin[:-1] if origin.endswith("/") else origin
    partes = urlsplit(candidato)
    try:
        porta = partes.port
    except ValueError:
        raise AmhInteropCompositionError() from None
    if (
        partes.scheme not in ("http", "https")
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
        or partes.path
        or partes.query
        or partes.fragment
        or porta == 0
        or candidato != f"{partes.scheme}://{partes.netloc}"
    ):
        raise AmhInteropCompositionError()
    return candidato


def _validar_token_url(token_url: str) -> str:
    """O endpoint de token do Cognito: SEMPRE `https` (o segredo do cliente viaja nele)."""
    if type(token_url) is not str or not token_url.isascii():
        raise AmhInteropCompositionError()
    partes = urlsplit(token_url)
    try:
        porta = partes.port
    except ValueError:
        raise AmhInteropCompositionError() from None
    if (
        partes.scheme != "https"
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
        or not partes.path.startswith("/")
        or partes.query
        or partes.fragment
        or porta == 0
    ):
        raise AmhInteropCompositionError()
    return token_url


def _prazo_valido(timeout_seconds: Any) -> bool:
    return (
        type(timeout_seconds) in (int, float)
        and math.isfinite(timeout_seconds)
        and 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
    )


def _token_valido(token: Any) -> bool:
    return (
        type(token) is str
        and 0 < len(token) <= 8_192
        and token.isascii()
        and not any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in token)
    )


async def _ler_corpo(response: httpx.Response, limite: int) -> bytes | None:
    """O corpo bruto, ou `None` se passar do limite (lido em pedacos, nunca inteiro antes)."""
    raw = bytearray()
    async for chunk in response.aiter_raw(chunk_size=65_536):
        raw.extend(chunk)
        if len(raw) > limite:
            return None
    return bytes(raw)


class CognitoClientCredentials:
    """Token OAuth2 `client_credentials` do Cognito, em cache ate' `expires_in - 120s`. Fail-closed.

    `token()` devolve o bearer ou `None` — nunca levanta, nunca loga segredo, token ou corpo. Um 401
    do servico chama `invalidar()` e a proxima chamada busca um token novo. Pedidos concorrentes com
    o cache vazio esperam UM unico POST (lock), em vez de abrir uma rajada no Cognito.
    """

    __slots__ = (
        "_cache",
        "_client_id",
        "_client_secret",
        "_clock",
        "_closed",
        "_expira_em",
        "_lock",
        "_scopes",
        "_timeout",
        "_token_url",
        "_transport_factory",
    )

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: tuple[str, ...],
        timeout_seconds: float = 5.0,
        transport_factory: TransportFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = _validar_token_url(token_url)
        if type(client_id) is not str or not _CLIENT_ID.fullmatch(client_id):
            raise AmhInteropCompositionError()
        if not _token_valido(client_secret) or len(client_secret) > 512:
            raise AmhInteropCompositionError()
        if (
            type(scopes) is not tuple
            or not scopes
            or any(type(s) is not str or not _SCOPE.fullmatch(s) for s in scopes)
            or len(set(scopes)) != len(scopes)
        ):
            raise AmhInteropCompositionError()
        if not _prazo_valido(timeout_seconds):
            raise AmhInteropCompositionError()
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._timeout = float(timeout_seconds)
        self._transport_factory = transport_factory or _transporte_padrao
        self._clock = clock
        self._cache: str | None = None
        self._expira_em = 0.0
        self._lock = asyncio.Lock()
        self._closed = False

    def __repr__(self) -> str:
        return "CognitoClientCredentials(<redacted>)"

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._scopes

    def invalidar(self) -> None:
        self._cache = None
        self._expira_em = 0.0

    async def aclose(self) -> None:
        self._closed = True
        self.invalidar()

    async def token(self) -> str | None:
        if self._closed:
            return None
        if self._cache is not None and self._clock() < self._expira_em:
            return self._cache
        async with self._lock:
            if self._cache is not None and self._clock() < self._expira_em:
                return self._cache
            try:
                async with asyncio.timeout(self._timeout):
                    obtido = await self._buscar()
            except asyncio.CancelledError:
                raise
            except Exception:
                obtido = None
            if obtido is None:
                # So' a CLASSE do fracasso; nenhuma URL, id, corpo ou excecao vai para o log.
                logger.warning("amh_interop_token_indisponivel")
                return None
            token, expires_in = obtido
            if self._closed:
                return None
            self._cache = token
            self._expira_em = self._clock() + max(0.0, expires_in - _TOKEN_REFRESH_MARGIN_S)
            return token

    async def _buscar(self) -> tuple[str, float] | None:
        if _httpcore_em_debug():
            return None
        basic = base64.b64encode(
            f"{quote(self._client_id, safe='')}:{quote(self._client_secret, safe='')}".encode("ascii")
        ).decode("ascii")
        corpo = "grant_type=client_credentials&scope=" + quote(" ".join(self._scopes), safe="")
        pedido = httpx.Request(
            "POST",
            self._token_url,
            content=corpo.encode("ascii"),
            headers={
                "Authorization": "Basic " + basic,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            extensions={"timeout": dict.fromkeys(("connect", "read", "write", "pool"), self._timeout)},
        )
        async with self._transport_factory() as transport:
            response = await transport.handle_async_request(pedido)
            try:
                if response.status_code != 200:
                    return None
                if response.headers.get("content-encoding", "identity") != "identity":
                    return None
                tipo = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if tipo != "application/json":
                    return None
                raw = await _ler_corpo(response, _MAX_TOKEN_RESPONSE_BYTES)
            finally:
                await response.aclose()
        if raw is None:
            return None
        body = json.loads(raw)
        if not isinstance(body, dict):
            return None
        token = body.get("access_token")
        token_type = body.get("token_type")
        expires_in = body.get("expires_in")
        if (
            not _token_valido(token)
            or type(token_type) is not str
            or token_type.lower() != "bearer"
            or type(expires_in) is not int
            or not 0 < expires_in <= _MAX_TOKEN_LIFETIME_S
        ):
            return None
        return str(token), float(expires_in)


class AmhPhoneLookupHasher:
    """`amh-phone-lookup-v1`: HMAC-SHA256(chave, "{amh_tenant}:{E164 so' digitos}") em hex (decisao D).

    A chave e' DEDICADA (segredo `amh/interop/phone-lookup-key`), distinta do `PHI_HMAC_KEY` do Maezo:
    o pseudonimo `hk1_` do Maezo nao significa nada para a AMH, e este hash nao serve de chave de
    nada no Maezo. Numero fora do padrao brasileiro devolve `None` (o Lucas segue sem identidade).
    Nunca levanta, nunca loga o numero nem o hash; `repr` nao mostra a chave.
    """

    __slots__ = ("_amh_tenant", "_key")

    def __init__(self, *, key: str, amh_tenant: str) -> None:
        if not _token_valido(key) or len(key) < _MIN_PHONE_KEY_CHARS:
            raise AmhInteropCompositionError()
        if type(amh_tenant) is not str or not _TOKEN.fullmatch(amh_tenant):
            raise AmhInteropCompositionError()
        self._key = key.encode("ascii")
        self._amh_tenant = amh_tenant

    def __repr__(self) -> str:
        return "AmhPhoneLookupHasher(<redacted>)"

    @property
    def amh_tenant(self) -> str:
        return self._amh_tenant

    def __call__(self, raw_phone: str) -> str | None:
        if type(raw_phone) is not str:
            return None
        digitos = raw_phone.strip()
        if digitos.startswith("+"):
            digitos = digitos[1:]
        if not digitos.isascii() or not _E164_BR.fullmatch(digitos):
            return None
        if len(digitos) == 12 and digitos[4] in "6789":
            # Celular sem o nono digito: e' assim que o WhatsApp entrega muitos numeros brasileiros
            # (`wa_id` de conta antiga), e o indice da AMH guarda o celular SEMPRE com o 9
            # (`build_phone_lookup.normalizar_e164`). Sem esta forma canonica o hash nunca bate.
            digitos = digitos[:4] + "9" + digitos[4:]
        mensagem = f"{self._amh_tenant}:{digitos}".encode("ascii")
        return hmac.new(self._key, mensagem, hashlib.sha256).hexdigest()


class _ExecutorInteropAmh:
    """O caminho comum: zona, gate (sombra), token, auditoria duravel, despacho fixo e recusa fechada."""

    def __init__(
        self,
        *,
        seam: SeamContext,
        origin: str,
        tokens: CognitoClientCredentials,
        audit: InteropAuditSink,
        purpose_of_use: str,
        agent_version: str,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        if not isinstance(seam, SeamContext) or seam.phi_zone != PHI_ZONE_GENERAL:
            # Decisao B: esta composicao e' do receptor de Zona Geral.
            raise AmhInteropCompositionError()
        if not isinstance(tokens, CognitoClientCredentials):
            raise AmhInteropCompositionError()
        if not callable(getattr(audit, "emit", None)):
            raise AmhInteropCompositionError()
        if type(purpose_of_use) is not str or not _TOKEN.fullmatch(purpose_of_use):
            raise AmhInteropCompositionError()
        if type(agent_version) is not str or not _VERSION.fullmatch(agent_version):
            raise AmhInteropCompositionError()
        self._seam = seam
        self._origin = validar_origem(origin)
        self._tokens = tokens
        self._audit = audit
        self._purpose = purpose_of_use
        self._version = agent_version
        self._transport_factory = transport_factory or _transporte_padrao
        self._closed = False
        self._active: set[asyncio.Task[Any]] = set()

    async def aclose(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        pending = [task for task in self._active if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _sujeito(self, path: str, prefixo: str, sufixo: str) -> str:
        if (
            not path.startswith(prefixo)
            or not path.endswith(sufixo)
            or len(path) <= len(prefixo) + len(sufixo)
        ):
            raise ValueError
        codificado = path[len(prefixo) : -len(sufixo)]
        sujeito = unquote(codificado, errors="strict")
        if not _SUBJECT.fullmatch(sujeito) or quote(sujeito, safe="") != codificado:
            raise ValueError
        return sujeito

    def _query(self, query: Any, permitidos: frozenset[str]) -> dict[str, Any]:
        if type(query) is not tuple:
            raise ValueError
        valores: dict[str, Any] = {}
        for item in query:
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
                raise ValueError
            nome, valor = item
            if nome in valores or nome not in permitidos:
                raise ValueError
            valores[nome] = valor
        if valores.get("purpose_of_use") != self._purpose:
            raise ValueError
        return valores

    async def _rodar(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        query: tuple[tuple[str, str | int], ...],
        body: bytes | None,
        consent_ref: str | None,
        timeout_seconds: float,
    ) -> PortResult[tuple[int, bytes]]:
        if self._closed:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        task = asyncio.current_task()
        if task is None:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        self._active.add(task)
        try:
            return await self._rodar_ativo(
                operation=operation,
                method=method,
                path=path,
                query=query,
                body=body,
                consent_ref=consent_ref,
                timeout_seconds=timeout_seconds,
            )
        finally:
            self._active.discard(task)

    async def _rodar_ativo(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        query: tuple[tuple[str, str | int], ...],
        body: bytes | None,
        consent_ref: str | None,
        timeout_seconds: float,
    ) -> PortResult[tuple[int, bytes]]:
        try:
            async with asyncio.timeout(timeout_seconds):
                # Decisao A: semantica ORDINARIA do gate. Levanta so' numa negacao ENFORCED; em
                # sombra (manifesto DRAFT) segue, e a linha de decisao ja' foi emitida pelo gate.
                decision = await gate(self._seam, operation)
                token = await self._tokens.token()
                if token is None:
                    return PortResult.refused(Reason.NOT_AUTHENTICATED)
                record = AuditRecord(
                    tenant_id=self._seam.tenant,
                    agent_id=self._seam.principal,
                    agent_version=self._version,
                    action=operation,
                    decision="ALLOW",
                    details={
                        "phase": "READ_DISPATCHED_UNDER_SHADOW_GATE",
                        "purpose_of_use": self._purpose,
                        "pep_allow": decision.allow,
                        "pep_reason": decision.reason,
                        "pep_enforced": decision.enforced,
                        # O POST de resolucao leva o hash do telefone no corpo: ele NUNCA entra
                        # aqui, nem como hash de hash. So' operacao, rota e query (sem PII crua).
                        "request_sha256": hash_input([operation, path, list(query)]),
                        "legal_basis_or_consent_sha256": (
                            hash_input(consent_ref) if consent_ref is not None else None
                        ),
                        "origin_sha256": hashlib.sha256(self._origin.encode()).hexdigest(),
                    },
                )
                receipt = await self._audit.emit(record)
                if type(receipt) is not str or not _RECEIPT.fullmatch(receipt):
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                if self._closed or _httpcore_em_debug():
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                headers = {
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                }
                if body is not None:
                    headers["Content-Type"] = "application/json"
                pedido = httpx.Request(
                    method,
                    self._origin + path,
                    params=query,
                    content=body,
                    headers=headers,
                    extensions={
                        "timeout": dict.fromkeys(("connect", "read", "write", "pool"), timeout_seconds)
                    },
                )
                async with self._transport_factory() as transport:
                    response = await transport.handle_async_request(pedido)
                    try:
                        status = response.status_code
                        if status in (400, 401, 404, 429) or status >= 500:
                            if status == 401:
                                self._tokens.invalidar()
                            return PortResult.refused(
                                {
                                    400: Reason.INVALID_REQUEST,
                                    401: Reason.NOT_AUTHENTICATED,
                                    404: Reason.NOT_FOUND,
                                    429: Reason.RATE_LIMITED,
                                }.get(status, Reason.UPSTREAM_UNAVAILABLE)
                            )
                        if 300 <= status < 400:
                            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            return PortResult.refused(Reason.CONTRACT_VIOLATION)
                        tipo = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if tipo != "application/json":
                            return PortResult.refused(Reason.CONTRACT_VIOLATION)
                        raw = await _ler_corpo(response, _MAX_RESPONSE_BYTES)
                        if raw is None:
                            return PortResult.refused(Reason.CONTRACT_VIOLATION)
                    finally:
                        await response.aclose()
                if self._closed:
                    return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
                return PortResult.ok((status, raw))
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            return PortResult.refused(Reason.TIMEOUT)
        except EffectDeniedError as denied:
            return PortResult.refused(
                Reason.RATE_LIMITED
                if denied.decision.reason == REASON_RATE_LIMITED
                else Reason.SCOPE_NOT_SUPPORTED
            )
        except Exception:
            # Classe fechada; nenhuma mensagem de excecao (pode carregar URL ou corpo) e' copiada.
            logger.warning("amh_interop_despacho_falhou", operation=operation)
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)


class AmhBillingStatusExecutor(_ExecutorInteropAmh, GovernedBillingStatusExecutor):
    """`GovernedBillingStatusExecutor` real: `GET .../billing/status` no servico interop da AMH."""

    registered_operations = frozenset({OP_BILLING})

    def _validar(self, request: GovernedBillingStatusRequest) -> None:
        if (
            type(request) is not GovernedBillingStatusRequest
            or request.method != "GET"
            or request.operation != OP_BILLING
        ):
            raise ValueError
        self._sujeito(request.path, _BILLING_PREFIX, _BILLING_SUFFIX)
        query = self._query(request.query, frozenset({"purpose_of_use", "competencia", "janela_meses"}))
        competencia = query.get("competencia")
        if competencia is not None and (
            type(competencia) is not str or not _COMPETENCIA.fullmatch(competencia)
        ):
            raise ValueError
        janela = query.get("janela_meses")
        if janela is not None and (type(janela) is not int or not 1 <= janela <= 36):
            raise ValueError
        ref = request.consent_decision_ref
        if type(ref) is not str or not ref.strip() or len(ref) > 4096:
            raise ValueError
        if not _prazo_valido(request.timeout_seconds):
            raise ValueError

    async def execute(
        self, request: GovernedBillingStatusRequest
    ) -> PortResult[GovernedBillingStatusResponse]:
        try:
            self._validar(request)
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._rodar(
            operation=request.operation,
            method="GET",
            path=request.path,
            query=request.query,
            body=None,
            consent_ref=request.consent_decision_ref,
            timeout_seconds=request.timeout_seconds,
        )
        if not resultado.succeeded or resultado.value is None:
            return PortResult.refused(
                resultado.failure.reason if resultado.failure else Reason.UPSTREAM_UNAVAILABLE
            )
        status, raw = resultado.value
        return PortResult.ok(GovernedBillingStatusResponse(status, raw))


class AmhSubjectResolutionExecutor(_ExecutorInteropAmh, GovernedSubjectResolutionExecutor):
    """`GovernedSubjectResolutionExecutor` real: resolucao por hash de telefone (POST) e perfil (GET)."""

    registered_operations = frozenset({OP_RESOLVE, OP_PROFILE})

    def __init__(self, *, amh_tenant: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if type(amh_tenant) is not str or not _TOKEN.fullmatch(amh_tenant):
            raise AmhInteropCompositionError()
        self._amh_tenant = amh_tenant

    def _validar(self, request: GovernedSubjectResolutionRequest) -> None:
        if type(request) is not GovernedSubjectResolutionRequest or not _prazo_valido(
            request.timeout_seconds
        ):
            raise ValueError
        if request.operation == OP_RESOLVE:
            if (
                request.method != "POST"
                or request.path != _RESOLVE_PATH
                or request.query != ()
                or request.consent_decision_ref is not None
                or type(request.body) is not bytes
                or len(request.body) > _MAX_REQUEST_BODY_BYTES
            ):
                raise ValueError
            corpo = json.loads(request.body)
            if (
                type(corpo) is not dict
                or set(corpo) != {"amh_tenant", "phone_hash", "hash_scheme", "purpose_of_use"}
                or corpo["amh_tenant"] != self._amh_tenant
                or corpo["hash_scheme"] != HASH_SCHEME
                or corpo["purpose_of_use"] != self._purpose
                or type(corpo["phone_hash"]) is not str
                or not _PHONE_HASH.fullmatch(corpo["phone_hash"])
            ):
                raise ValueError
            return
        if request.operation == OP_PROFILE:
            if request.method != "GET" or request.body is not None:
                raise ValueError
            self._sujeito(request.path, _PROFILE_PREFIX, _PROFILE_SUFFIX)
            self._query(request.query, frozenset({"purpose_of_use"}))
            ref = request.consent_decision_ref
            if type(ref) is not str or not ref.strip() or len(ref) > 4096:
                raise ValueError
            return
        raise ValueError

    async def execute(
        self, request: GovernedSubjectResolutionRequest
    ) -> PortResult[GovernedSubjectResolutionResponse]:
        try:
            self._validar(request)
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._rodar(
            operation=request.operation,
            method=request.method,
            path=request.path,
            query=request.query,
            body=request.body,
            consent_ref=request.consent_decision_ref,
            timeout_seconds=request.timeout_seconds,
        )
        if not resultado.succeeded or resultado.value is None:
            return PortResult.refused(
                resultado.failure.reason if resultado.failure else Reason.UPSTREAM_UNAVAILABLE
            )
        status, raw = resultado.value
        return PortResult.ok(GovernedSubjectResolutionResponse(status, raw))


class AmhTinaExecutor(_ExecutorInteropAmh, GovernedTinaExecutor):
    """`GovernedTinaExecutor` real: as tres leituras GET do contrato TINA no servico interop da AMH.

    Mesmo caminho comum (zona geral, gate em sombra, token, auditoria duravel ANTES do despacho,
    transporte sem proxy/retry/redirect, corpo limitado, recusa fechada). Rota, query e base legal sao
    conferidas aqui de novo, a cada chamada: o adaptador monta o pedido, o executor nao confia nele.
    """

    registered_operations = frozenset({OP_ELEGIBILIDADE, OP_CARENCIAS, OP_REQUISICOES})

    def _validar(self, request: GovernedTinaRequest) -> None:
        if (
            type(request) is not GovernedTinaRequest
            or request.method != "GET"
            or request.operation not in self.registered_operations
        ):
            raise ValueError
        self._sujeito(request.path, _TINA_PREFIX, _TINA_SUFFIX[request.operation])
        query = self._query(request.query, _TINA_QUERY[request.operation])
        limite = query.get("limite")
        if limite is not None and (type(limite) is not int or not 1 <= limite <= 50):
            raise ValueError
        pendentes = query.get("pendentes")
        if pendentes is not None and pendentes not in ("true", "false"):
            raise ValueError
        ref = request.consent_decision_ref
        if type(ref) is not str or not ref.strip() or len(ref) > 4096:
            raise ValueError
        if not _prazo_valido(request.timeout_seconds):
            raise ValueError

    async def execute(self, request: GovernedTinaRequest) -> PortResult[GovernedTinaResponse]:
        try:
            self._validar(request)
        except Exception:
            return PortResult.refused(Reason.INVALID_REQUEST)
        resultado = await self._rodar(
            operation=request.operation,
            method="GET",
            path=request.path,
            query=request.query,
            body=None,
            consent_ref=request.consent_decision_ref,
            timeout_seconds=request.timeout_seconds,
        )
        if not resultado.succeeded or resultado.value is None:
            return PortResult.refused(
                resultado.failure.reason if resultado.failure else Reason.UPSTREAM_UNAVAILABLE
            )
        status, raw = resultado.value
        return PortResult.ok(GovernedTinaResponse(status, raw))


@dataclass(frozen=True, slots=True, repr=False)
class AmhInteropComposition:
    """O que a raiz de composicao recebe: os dois ports da AMH ja' sobre os executores reais, o hasher
    do telefone e o vocabulario fixo (tenant AMH, proposito, esquema). Fecha tudo em `aclose()`."""

    billing: AmhBillingStatusAdapter
    subjects: AmhSubjectResolutionAdapter
    phone_hasher: AmhPhoneLookupHasher
    amh_tenant: str
    purpose_of_use: str
    hash_scheme: str
    _executors: tuple[_ExecutorInteropAmh, ...]
    _tokens: CognitoClientCredentials
    #: As leituras TINA (fatos do plano), SO' quando a composicao as pediu (`incluir_tina=True` em
    #: `tool_registry.build_amh_interop`, com `MAEZO_HELENA_CONSULTAS_AMH` ligada). Senao `None`.
    tina: AmhTinaAdapter | None = None

    async def aclose(self) -> None:
        for executor in self._executors:
            with contextlib.suppress(Exception):
                await executor.aclose()
        with contextlib.suppress(Exception):
            await self._tokens.aclose()


__all__ = [
    "HASH_SCHEME",
    "SCOPE_BY_OPERATION",
    "AmhBillingStatusExecutor",
    "AmhInteropComposition",
    "AmhInteropCompositionError",
    "AmhPhoneLookupHasher",
    "AmhSubjectResolutionExecutor",
    "AmhTinaExecutor",
    "SCOPE_TINA",
    "CognitoClientCredentials",
    "InteropAuditSink",
    "validar_origem",
]
