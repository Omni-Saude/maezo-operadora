"""D-36 commission adjustment STRUCTURE drafts — the DARK internal HTTP boundary.

Decision D-36 (dono, 2026-10-25): an INTERNAL management surface to register commission
adjustment STRUCTURES across the three dimensions (produto, canal, corretor) — multiple
simultaneous adjustments per target, vigência windows, declared precedence — with data
entering in the FUTURE. This boundary creates, replaces, lists and reads DRAFTS. It computes
nothing, publishes nothing, settles nothing: no calculation, activation or liquidation
endpoint EXISTS on this router — not disabled, ABSENT by construction (pinned by test over
the route table and the OpenAPI surface). Activation is a future act that requires the A8
Forma 1 ratification by the `comercial` + `legal` + `DPO` trio and arrives as a SUCCESSOR
migration (`commission_adjustment_policies.status` carries a `CHECK (status = 'DRAFT')` —
the only state the store can persist, so no bug can activate a draft either).

The commission dimension is fail-closed by the A8 admission
(`gateway/commercial_commission_admission.py`): the frozen C6-4 contract carries ZERO
commission surface and the registered decision is `admitted_out_of_scope`. This surface
respects the recorded limit and never imports the A8 loader into any calculation path —
there is no calculation path. The ONE A8 piece consumed here is the admission-independent
structural guard `refuse_commission_policy_payload`, run on sight on every mutation body:
a payload that FABRICATES the contract stub family `commission-policy.proposed.v1` (mutated
consts, stray `monetaryContent`/`ratification`) is refused 403
`commission_policy_fabricated` before admission; and because this boundary is
MAEZO-INTERNAL, the WELL-FORMED stub dialect is refused too — family separation is
structural, test data must never match the contract stub. Server-owned fields (`status`,
`policy_ref`, `revision`, the audit columns, `tenant`) and the stub-transcribed names are
reserved on sight (403 `field_not_client_owned`): a draft never carries its own state.

Fail-closed posture (the OP16/W5 dark mold): `PortalSettings.staff_commission_policies` is
OFF by default and `commission_policy_service_factory` has NO default, so a default
deployment answers 403 `operation_forbidden` (flag) or 503 `dependency_unavailable`
(unbound store) before any effect. The route ships DOCUMENTED and DARK: flipping the flag
is a named owner act, never this package's.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.routing import Match

from maezo.gateway.commercial_commission_admission import (
    COMMISSION_POLICY_STUB_SCHEMA,
    CommissionPayloadError,
    refuse_commission_policy_payload,
)
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.decisions import _body, _nonfinite, _pairs
from maezo.portal.api.intakes import PREFIX
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.commission_policies import (
    CommissionAdjustmentDraft,
    CommissionAdjustmentDraftPage,
    CommissionAdjustmentDraftReplacement,
    CommissionAdjustmentDraftSubmission,
    PortalCommissionPolicyError,
)

CommissionPolicyServiceFactory = Callable[[HumanSessionResolver], "CommissionPolicyService"]
ROUTE_PATH = PREFIX + "/commission-adjustments"

#: The closed refusal vocabulary of this boundary — the wire error's Literal and this typed
#: error's argument are the SAME closed set (a `stale_revision` is not an `IntakeError` code;
#: the vocabulary is this surface's own, quoted by the contract).
CommissionPolicyCode = Literal[
    "invalid_request",
    "authentication_unavailable",
    "operation_forbidden",
    "commission_policy_fabricated",
    "field_not_client_owned",
    "resource_unavailable",
    "stale_revision",
    "dependency_unavailable",
]

STATUS: dict[str, int] = {
    "invalid_request": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "commission_policy_fabricated": 403,
    "field_not_client_owned": 403,
    "resource_unavailable": 404,
    "stale_revision": 409,
    "dependency_unavailable": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalCommissionPolicyError} for status in STATUS.values()
}


class CommissionPolicyError(RuntimeError):
    """One refusal of this boundary, carrying its closed code; cause text never travels."""

    def __init__(self, code: CommissionPolicyCode = "dependency_unavailable") -> None:
        self.code = code
        super().__init__(code)


#: Field names this boundary refuses ON SIGHT (403 `field_not_client_owned`, before the
#: closed DTO, session or not): the facts the SERVER owns on this surface — the draft state
#: (`status`: `DRAFT` is the only state and nobody negotiates it), the draft identity
#: (`policy_ref` is server-generated; the replace path carries it), the audit columns
#: (who/when is the authenticated session's, never the caller's claim) and the tenant scope
#: — plus the stub-transcribed names `monetaryContent`, `ratification` and the camelCase
#: `schemaVersion` (the contract stub dialect never enters an internal draft; a well-formed
#: stub is already refused as a fabrication above). `revision` is NOT here: it is the
#: client-echoed EXPECTATION of the optimistic replace (the house `business_revision`
#: mold), a legitimate closed-DTO input — on create it is simply not in the shape and dies
#: in the DTO. The reserved values are never logged: a refused name is reported to nobody.
_RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "policy_ref",
        "criado_por",
        "criado_em",
        "atualizado_por",
        "atualizado_em",
        "tenant",
        "monetaryContent",
        "ratification",
        "schemaVersion",
    }
)

_POLICY_REF_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_POLICY_REF_PREFIX = "cap-"


def commission_policy_error(code: str = "dependency_unavailable") -> JSONResponse:
    if code not in STATUS:
        code = "dependency_unavailable"
    return JSONResponse({"code": code}, status_code=STATUS[code])


@runtime_checkable
class CommissionDraftStore(Protocol):
    """The one persistence seam, tenant-scoped by construction. Nothing else stores drafts.

    Every method deals ONLY in `DRAFT` receipts: the store has no way to express another
    state (the table's CHECK refuses it), so no store implementation can grow an activation
    path without a successor migration — the same successor rule the A8 Forma 1 pins.
    """

    async def create(self, draft: CommissionAdjustmentDraft) -> None:
        """Persist a freshly minted draft; a ref collision (never observed, 128-bit) refuses."""
        ...

    async def replace(
        self, policy_ref: str, *, expected_revision: str, draft: CommissionAdjustmentDraft
    ) -> CommissionAdjustmentDraft | None:
        """Optimistic replace: persist only if the stored revision is still `expected_revision`.

        `None` means the draft changed (or vanished) under the editor — the service refuses
        `stale_revision`; the store never guesses which.
        """
        ...

    async def read(self, policy_ref: str) -> CommissionAdjustmentDraft | None:
        """The draft, or None — an absent ref and a foreign ref are indistinguishable."""
        ...

    async def list(self) -> tuple[CommissionAdjustmentDraft, ...]:
        """The tenant's drafts, ordered (precedencia, dimensao, policy_ref). Read-only."""
        ...


def _draft_from_submission(
    submission: CommissionAdjustmentDraftSubmission, *, policy_ref: str, author: str, now: datetime
) -> CommissionAdjustmentDraft:
    """Mint the DRAFT receipt server-side: ref, revision 1, state and audit are never inputs."""
    return CommissionAdjustmentDraft(
        policy_ref=policy_ref,
        revision="1",
        dimensao=submission.dimensao,
        dimensao_ref=submission.dimensao_ref,
        ajuste_tipo=submission.ajuste_tipo,
        percentual=submission.percentual,
        valor=submission.valor,
        faixa_min=submission.faixa_min,
        faixa_max=submission.faixa_max,
        moeda=submission.moeda,
        vigencia_inicio=submission.vigencia_inicio,
        vigencia_fim=submission.vigencia_fim,
        precedencia=submission.precedencia,
        criado_por=author,
        criado_em=now,
        atualizado_por=author,
        atualizado_em=now,
    )


def _replacement_draft(
    replacement: CommissionAdjustmentDraftReplacement,
    current: CommissionAdjustmentDraft,
    *,
    editor: str,
    now: datetime,
) -> CommissionAdjustmentDraft:
    """Full-shape replace of the EDITABLE structure only: identity and creation audit stay.

    Absent slots clear values (they are nullable by design — clearing a percentual is an
    edit, not a loss). `criado_por`/`criado_em` are immutable history; `atualizado_*` record
    the editor, whoever it is.
    """
    revision = str(int(current.revision) + 1)
    return CommissionAdjustmentDraft(
        policy_ref=current.policy_ref,
        revision=revision,
        dimensao=replacement.dimensao,
        dimensao_ref=replacement.dimensao_ref,
        ajuste_tipo=replacement.ajuste_tipo,
        percentual=replacement.percentual,
        valor=replacement.valor,
        faixa_min=replacement.faixa_min,
        faixa_max=replacement.faixa_max,
        moeda=replacement.moeda,
        vigencia_inicio=replacement.vigencia_inicio,
        vigencia_fim=replacement.vigencia_fim,
        precedencia=replacement.precedencia,
        criado_por=current.criado_por,
        criado_em=current.criado_em,
        atualizado_por=editor,
        atualizado_em=now,
    )


class CommissionPolicyService:
    """Session resolution + the flag/staff gates + the draft machine; no calculation here."""

    def __init__(self, resolver: HumanSessionResolver, store: CommissionDraftStore) -> None:
        self.resolver, self.store = resolver, store

    def _surface_enabled(self) -> bool:
        return self.resolver.settings.staff_commission_policies

    async def _authorize(
        self, session_secret: str, *, csrf: str | None = None, origin: str | None = None
    ) -> ResolvedHumanSession:
        session = await self.resolver.resolve(session_secret)
        if datetime.now(UTC) >= session_ceiling(session):
            raise AuthenticationError()
        if not self._surface_enabled():
            # The surface is not enabled in THIS deployment; the session proves nothing about it.
            raise CommissionPolicyError("operation_forbidden")
        if session.membership.audience != "staff":
            # An internal management surface: a beneficiary/provider/vendor session never
            # reaches the store, with the flag ON or OFF.
            raise CommissionPolicyError("operation_forbidden")
        if (csrf is not None or origin is not None) and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise CommissionPolicyError("authentication_unavailable")
        current = await self.resolver.resolve(session_secret)
        if current.principal != session.principal or datetime.now(UTC) >= session_ceiling(current):
            raise AuthenticationError()
        return current

    async def create(
        self,
        session_secret: str,
        *,
        csrf: str | None,
        origin: str | None,
        submission: CommissionAdjustmentDraftSubmission,
    ) -> CommissionAdjustmentDraft:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        draft = _draft_from_submission(
            submission,
            policy_ref=_new_policy_ref(),
            author=session.principal.principal_ref,
            now=datetime.now(UTC),
        )
        await self.store.create(draft)
        return draft

    async def replace(
        self,
        session_secret: str,
        *,
        csrf: str | None,
        origin: str | None,
        policy_ref: str,
        replacement: CommissionAdjustmentDraftReplacement,
    ) -> CommissionAdjustmentDraft:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        if not _POLICY_REF_PATTERN.fullmatch(policy_ref):
            raise CommissionPolicyError("invalid_request")
        current = await self.store.read(policy_ref)
        if current is None:
            # An absent ref and a ref owned by another tenant are indistinguishable here —
            # every store is tenant-scoped by construction.
            raise CommissionPolicyError("resource_unavailable")
        if replacement.revision != current.revision:
            raise CommissionPolicyError("stale_revision")
        draft = _replacement_draft(
            replacement, current, editor=session.principal.principal_ref, now=datetime.now(UTC)
        )
        stored = await self.store.replace(policy_ref, expected_revision=current.revision, draft=draft)
        if stored is None:
            raise CommissionPolicyError("stale_revision")
        return stored

    async def read(self, session_secret: str, policy_ref: str) -> CommissionAdjustmentDraft:
        await self._authorize(session_secret)
        if not _POLICY_REF_PATTERN.fullmatch(policy_ref):
            raise CommissionPolicyError("invalid_request")
        draft = await self.store.read(policy_ref)
        if draft is None:
            raise CommissionPolicyError("resource_unavailable")
        return draft

    async def list(self, session_secret: str) -> CommissionAdjustmentDraftPage:
        await self._authorize(session_secret)
        drafts = await self.store.list()
        return CommissionAdjustmentDraftPage(drafts=drafts, count=len(drafts))


def _new_policy_ref() -> str:
    """`cap-` + 128 bits of server randomness — matches `ResourceRef` and the table CHECK."""
    return _POLICY_REF_PREFIX + secrets.token_hex(16)


def _service(request: Request) -> CommissionPolicyService:
    factory: CommissionPolicyServiceFactory | None = request.app.state.commission_policy_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, CommissionPolicyService) or service.resolver is not resolver:
        raise IntakeError()
    return service


def _secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise IntakeError("authentication_unavailable") from None


class CommissionPolicyRoute(APIRoute):
    """Bounded framing first, then the A8 fabrication guard, then the reserved fields.

    Mirrors the DSR lane's `DsrRoute` with the commission-specific on-sight order: (1) a
    payload naming the contract stub family is a fabrication of the commission-policy
    dialect — refused before anything else; (2) `refuse_commission_policy_payload` runs the
    admission-independent A8 structural clauses (mutated consts, stray
    `monetaryContent`/`ratification`); (3) server-owned and stub-dialect field names refuse
    as `field_not_client_owned`. All three hold with ZERO store calls and zero session
    dependence: refusing a fabricated commission payload is not a session's privilege.
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
                        return commission_policy_error("authentication_unavailable")
                    parsed = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite
                    )
                    if type(parsed) is not dict:
                        raise ValueError
                    if parsed.get("schemaVersion") == COMMISSION_POLICY_STUB_SCHEMA:
                        # MAEZO-INTERNAL family separation: the contract stub dialect —
                        # well-formed or not — is never a draft structure on this surface.
                        return commission_policy_error("commission_policy_fabricated")
                    try:
                        refuse_commission_policy_payload(parsed)
                    except CommissionPayloadError:
                        return commission_policy_error("commission_policy_fabricated")
                    if _RESERVED_FIELDS & set(parsed):
                        return commission_policy_error("field_not_client_owned")
                elif data:
                    raise ValueError
            except Exception:
                return commission_policy_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": data, "more_body": False}

            try:
                return await original(Request(request.scope, receive=replay))
            except CommissionPolicyError as exc:
                return commission_policy_error(exc.code)
            except IntakeError as exc:
                return commission_policy_error(exc.code)
            except RequestValidationError:
                return commission_policy_error("invalid_request")
            except AuthenticationError:
                return commission_policy_error("authentication_unavailable")
            except Exception:
                return commission_policy_error()

        return checked


commission_policy_router = APIRouter(route_class=CommissionPolicyRoute)


def is_commission_policy_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in commission_policy_router.routes)


@commission_policy_router.post(
    ROUTE_PATH, response_model=CommissionAdjustmentDraft, status_code=202, responses=ERRORS
)
async def create_commission_adjustment_draft(
    request: Request, body: CommissionAdjustmentDraftSubmission
) -> Response:
    draft = await _service(request).create(
        _secret(request),
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
        submission=body,
    )
    return Response(content=draft.model_dump_json(), status_code=202, media_type="application/json")


@commission_policy_router.post(
    ROUTE_PATH + "/{policy_ref}", response_model=CommissionAdjustmentDraft, status_code=202, responses=ERRORS
)
async def replace_commission_adjustment_draft(
    request: Request, policy_ref: str, body: CommissionAdjustmentDraftReplacement
) -> Response:
    draft = await _service(request).replace(
        _secret(request),
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
        policy_ref=policy_ref,
        replacement=body,
    )
    return Response(content=draft.model_dump_json(), status_code=202, media_type="application/json")


@commission_policy_router.get(ROUTE_PATH, response_model=CommissionAdjustmentDraftPage, responses=ERRORS)
async def list_commission_adjustment_drafts(request: Request) -> Response:
    if request.scope.get("query_string", b""):
        raise CommissionPolicyError("invalid_request")
    page = await _service(request).list(_secret(request))
    return Response(content=page.model_dump_json(), status_code=200, media_type="application/json")


@commission_policy_router.get(
    ROUTE_PATH + "/{policy_ref}", response_model=CommissionAdjustmentDraft, responses=ERRORS
)
async def read_commission_adjustment_draft(request: Request, policy_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise CommissionPolicyError("invalid_request")
    draft = await _service(request).read(_secret(request), policy_ref)
    return Response(content=draft.model_dump_json(), status_code=200, media_type="application/json")
