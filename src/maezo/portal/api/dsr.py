"""Titular DSR HTTP boundary (W5 CLNT 5.2) — and the resolved `decisao_dsr` guard at it.

The route ships DOCUMENTED and DARK: `PortalSettings.lgpd_dsr_channel` is OFF by default (the
program is shadow; flipping is a ROLL-wave act) and `dsr_service_factory` has NO default, so a
default deployment answers `operation_forbidden` (flag) or `dependency_unavailable` (no bound
service) before any effect — the OP16 vendor-submission posture.

RESOLUCAO DO GUARD `decisao_dsr` (semântica implementada lendo os consumers; detalhe no PR):

- PRODUCER (único): a decisão humana RATIFICADA é escrita por `UT_RevisaoDpo` — candidate
  groups `dpo`/`juridico-privacidade` vindos da DMN `lgpd_dsr_routing` — pelo endpoint
  governado `POST /api/v1/portal/tasks/{id}/decisions` no formato fechado `lgpd_decisao`
  (`LgpdDsrDecisionInputs`: literal de 3 valores + `fundamentacao_legal` obrigatória em
  `NEGAR_FUNDAMENTADO`). O valor nasce `""` inicializado pelo PRÓPRIO ENGINE na primeira
  service task (`bpmn:77`), nunca por quem abre a requisição.
- GUARD engine-side: `GW_DecisaoDsr` só avança com um dos TRÊS literais exatos
  (`APROVAR_ENVIO`|`EXECUTAR_E_ENVIAR`|`NEGAR_FUNDAMENTADO`); ausente/em branco/desconhecido é
  incidente fail-closed (`End_ErrDecisaoInvalida`/`ERR_DSR_DECISION_INVALID`), nunca omissão
  que libera (GAP-LGPD-4); `GW_GuardFundamentacao` barra a negativa sem fundamento (GAP-LGPD-3).
- CONSUMERS: `Flow_GWDec_Executar` (`${decisao_dsr == 'EXECUTAR_E_ENVIAR'}`), os guards do
  `ExecuteRequestWorker` (a/b/c), `make_send_response_handler` (publica só o valor) e a ponte
  `consent-revocation-bridge` (`tipo_requisicao=revogacao_consentimento` E
  `decisao_dsr=EXECUTAR_E_ENVIAR`).
- NESTE LIMITE HTTP: `decisao_dsr` (e todo fato de decisão/identidade/efeito) é RECUSADO
  (403 `operation_forbidden`) se aparecer no payload — abrir a requisição NÃO resolve a
  decisão e não pode fingir que resolve (`_RESERVED_FACTS`). Este endpoint nunca escreve a
  variável; quem a escreve segue sendo o humano, pelo caminho governado, com o engine
  fail-closed atrás. A resolução do guard L0 `human_approved` do `ExecuteRequestWorker`
  (linha ABERTA da review-queue) permanece decisão do PR de HABILITAÇÃO — defense-in-depth
  mantida, nada enfraquecido aqui.

A sink (`DsrIntakeSink`) é o ÚNICO ponto futuro de efeito: deve iniciar `SP-OP-LGPD-DSR-001`
PELO CHOKEPOINT auditado (`start_process_idempotent`, business key contratual
`DSR-{tenant_id}-{titular_pseudo_id}-{tipo_requisicao}-{data_solicitacao_iso}`), semeando
`identidade_verificada=False` (login não é prova ratificada de identidade LGPD) e resolvendo
`envolve_dados_saude`/`titular_pseudo_id` server-side — nunca via este limite.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.routing import Match

from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.decisions import _body, _nonfinite, _pairs
from maezo.portal.api.intakes import PREFIX
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.dsr import DsrReceipt, DsrSubmission, PortalDsrError

DsrServiceFactory = Callable[[HumanSessionResolver], "DsrService"]
ROUTE_PATH = PREFIX + "/dsr"
STATUS: dict[str, int] = {
    "invalid_request": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "dependency_unavailable": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": PortalDsrError} for status in STATUS.values()}

#: Fact names this boundary refuses ON SIGHT (403, before admission, before the closed DTO).
#: They are server-resolved facts on this surface — decision (`decisao_dsr`,
#: `fundamentacao_legal`), identity (`identidade_verificada`, `identidade_confirmada`,
#: `human_approved`), routing scope (`envolve_dados_saude`) and the projection variables the
#: server owns (`tenant_id`, `titular_pseudo_id`, `canal`, `data_solicitacao_iso`). The values
#: are never logged: a refused name is reported to nobody, it just refuses.
_RESERVED_FACTS: frozenset[str] = frozenset(
    {
        "decisao_dsr",
        "fundamentacao_legal",
        "identidade_verificada",
        "identidade_confirmada",
        "human_approved",
        "envolve_dados_saude",
        "tenant_id",
        "titular_pseudo_id",
        "canal",
        "data_solicitacao_iso",
    }
)


def dsr_error(code: str = "dependency_unavailable") -> JSONResponse:
    if code not in STATUS:
        code = "dependency_unavailable"
    return JSONResponse({"code": code}, status_code=STATUS[code])


@runtime_checkable
class DsrIntakeSink(Protocol):
    """The one future effect seam. The BFF never starts a process; the sink does — later."""

    async def open_request(self, projection: DsrStartProjection) -> DsrReceipt:
        """Stage the titular request for the audited engine start (idempotent per business key)."""
        ...


@dataclass(frozen=True, slots=True)
class DsrStartProjection:
    """Server-owned start facts handed to the sink. The free-text request never reprs."""

    tenant_id: str
    principal_ref: str
    tipo_requisicao: str
    detalhes_requisicao: str = field(repr=False)
    data_solicitacao_iso: str
    canal: str = "portal"


def _utc_date() -> str:
    return datetime.now(UTC).date().isoformat()


class DsrService:
    """Session resolution + the one flag gate; no engine transport, no authority here."""

    def __init__(self, resolver: HumanSessionResolver, sink: DsrIntakeSink) -> None:
        self.resolver, self.sink = resolver, sink

    def _channel_enabled(self) -> bool:
        return self.resolver.settings.lgpd_dsr_channel

    async def _authorize(
        self, session_secret: str, *, csrf: str | None = None, origin: str | None = None
    ) -> ResolvedHumanSession:
        session = await self.resolver.resolve(session_secret)
        if datetime.now(UTC) >= session_ceiling(session):
            raise AuthenticationError()
        if not self._channel_enabled():
            # The channel is not enabled in THIS deployment; the session proves nothing about it.
            raise IntakeError("operation_forbidden")
        if not any(b.kind == "beneficiary" for b in session.principal.subject_bindings):
            # A DSR is the data subject's right: a session with NO beneficiary vínculo never
            # reaches the sink (a login alone proves no titular relationship — README rule).
            raise IntakeError("operation_forbidden")
        if (csrf is not None or origin is not None) and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise IntakeError("authentication_unavailable")
        current = await self.resolver.resolve(session_secret)
        if current.principal != session.principal or datetime.now(UTC) >= session_ceiling(current):
            raise AuthenticationError()
        return current

    async def submit(
        self, session_secret: str, *, csrf: str | None, origin: str | None, submission: DsrSubmission
    ) -> DsrReceipt:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        projection = DsrStartProjection(
            tenant_id=session.principal.tenant,
            principal_ref=session.principal.principal_ref,
            tipo_requisicao=submission.tipo_requisicao,
            detalhes_requisicao=submission.detalhes_requisicao,
            data_solicitacao_iso=_utc_date(),
        )
        return await self.sink.open_request(projection)


def _service(request: Request) -> DsrService:
    factory: DsrServiceFactory | None = request.app.state.dsr_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, DsrService) or service.resolver is not resolver:
        raise IntakeError()
    return service


def _secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise IntakeError("authentication_unavailable") from None


class DsrRoute(APIRoute):
    """Bounded framing first, then the reserved-fact guard, then the route itself.

    Mirrors the intake lane's `ProductRoute` with one addition: a payload that carries ANY
    `_RESERVED_FACTS` name is refused `operation_forbidden` (403) before admission — with zero
    downstream effect and zero dependence on session state. That is the `decisao_dsr` guard
    RESOLVED at this surface: opening a request never carries a decision.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                data = await _body(request)
                if request.method == "POST":
                    if request.scope.get("query_string", b"") or request.headers.getlist("content-type") != [
                        "application/json"
                    ]:
                        raise ValueError
                    if any(len(request.headers.getlist(name)) != 1 for name in ("origin", "x-csrf-token")):
                        return dsr_error("authentication_unavailable")
                    parsed = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite
                    )
                    if type(parsed) is not dict:
                        raise ValueError
                    if _RESERVED_FACTS & set(parsed):
                        return dsr_error("operation_forbidden")
                elif data:
                    raise ValueError
            except Exception:
                return dsr_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": data, "more_body": False}

            try:
                return await original(Request(request.scope, receive=replay))
            except IntakeError as exc:
                return dsr_error(exc.code)
            except RequestValidationError:
                return dsr_error("invalid_request")
            except AuthenticationError:
                return dsr_error("authentication_unavailable")
            except Exception:
                return dsr_error()

        return checked


dsr_router = APIRouter(route_class=DsrRoute)


def is_dsr_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in dsr_router.routes)


@dsr_router.post(ROUTE_PATH, response_model=DsrReceipt, status_code=202, responses=ERRORS)
async def submit_dsr(request: Request, body: DsrSubmission) -> Response:
    receipt = await _service(request).submit(
        _secret(request),
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
        submission=body,
    )
    return Response(content=receipt.model_dump_json(), status_code=202, media_type="application/json")
