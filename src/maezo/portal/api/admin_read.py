"""Administrative READ boundary (D-40) — read-only roster of reviewed staff vínculos.

The staff `administration` area answers with what the reviewed membership rows ALREADY say
(same rows, same identity store the session resolver reads for every request): which staff
principal holds which governed roles/groups and resource bindings, at which membership
revision, reviewed until when, and whether it is revoked. The surface has NO mutation: the
router mounts GET only, the service takes no command, and this module NEVER imports
`maezo.portal.admin` — the fence there ("Private reviewed human-administration services;
never public portal routes"; `portal/admin/assignments.py:1-8`: no BFF routes call that
service) stands untouched, and the independently granted administrative connection remains
the only writer of membership facts.

The route ships DOCUMENTED and DARK, the OP16/DSR posture: `staff_admin_panel` is OFF by
default, the `staff_admin_read` capability is in NO default profile (the SC1 production
bootstrap cannot even name the profile), and `admin_read_service_factory` has NO default —
so a default deployment answers `operation_forbidden` (flag/profile) or
`admin_read_unavailable` (no bound projection) before any effect. Turning the key is a
ROLL-wave act with a named reviewer; nothing here grants anything on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.routing import Match

from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.decisions import _body
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.api.store import RosterUnavailableError
from maezo.portal.contracts.admin_read import (
    PortalAdminReadError,
    StaffAdminBinding,
    StaffAdminMembership,
    StaffAdminVinculo,
    StaffAdminVinculos,
)

AdminReadServiceFactory = Callable[[HumanSessionResolver], "StaffAdminReadService"]
ROUTE_PATH = "/api/v1/portal/administration/memberships"
STATUS: dict[str, int] = {
    "invalid_request": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "admin_read_unavailable": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalAdminReadError} for status in STATUS.values()
}


def admin_read_error(code: str = "admin_read_unavailable") -> JSONResponse:
    return JSONResponse({"code": code}, status_code=STATUS.get(code, 503))


@runtime_checkable
class StaffRosterSource(Protocol):
    """The one read the roster needs: the store's own reviewed staff rows, uncached."""

    async def staff_memberships(self) -> tuple[MembershipRecord, ...]: ...


def _vinculo(record: MembershipRecord, now: datetime) -> StaffAdminVinculo:
    """Server-side projection of one reviewed row. Never echoes identity coordinates."""
    if record.audience != "staff":
        raise RosterUnavailableError()
    state: Literal["active", "expired", "revoked"] = (
        "revoked" if record.revoked else ("active" if record.reviewed_until > now else "expired")
    )
    return StaffAdminVinculo(
        principal_ref=record.principal_ref,
        membership_revision=record.revision,
        memberships=tuple(
            StaffAdminMembership(
                membership_ref=binding.membership_ref,
                roles=binding.roles,
                groups=binding.groups,
            )
            for binding in record.memberships
        ),
        subject_bindings=tuple(
            StaffAdminBinding(kind=binding.kind, resource_ref=binding.resource_ref)
            for binding in record.subject_bindings
        ),
        reviewed_until=record.reviewed_until,
        revoked=record.revoked,
        state=state,
    )


class StaffAdminReadService:
    """Session resolution + the two deployment gates + the roster read; no authority here."""

    def __init__(self, resolver: HumanSessionResolver) -> None:
        self.resolver = resolver

    def _profile_enabled(self) -> bool:
        settings = self.resolver.settings
        return settings.staff_admin_panel and "staff_admin_read" in settings.capabilities.split(",")

    def _roster_source(self) -> StaffRosterSource:
        # `isinstance` against the `runtime_checkable` protocol only proves the attribute
        # EXISTS (the intakes links lesson): `callable()` closes the shaped-composition gap
        # here, so a badly-shaped store refuses as the missing dependency it is.
        store = self.resolver.store
        listed = getattr(store, "staff_memberships", None)
        if not isinstance(store, StaffRosterSource) or not callable(listed):
            raise RosterUnavailableError()
        return store

    async def _resolved(self, session_secret: str) -> ResolvedHumanSession:
        session = await self.resolver.resolve(session_secret)
        if datetime.now(UTC) >= session_ceiling(session):
            raise AuthenticationError()
        return session

    async def read(self, session_secret: str) -> StaffAdminVinculos:
        first = await self._resolved(session_secret)
        if not self._profile_enabled():
            # The area is not enabled in THIS deployment; the session proves nothing about it.
            raise IntakeError("operation_forbidden")
        source = self._roster_source()
        records = await source.staff_memberships()
        # Revalidated per request: a session or membership revoked while the rows were read
        # must not be served (ADR-0049 D4; portal/api/session.py:65-66).
        current = await self._resolved(session_secret)
        if current.principal != first.principal:
            raise IntakeError("operation_forbidden")
        now = datetime.now(UTC)
        roster = StaffAdminVinculos(
            generated_at=now,
            count=len(records),
            vinculos=tuple(_vinculo(record, now) for record in records),
        )
        if datetime.now(UTC) >= min(session_ceiling(first), session_ceiling(current)):
            raise IntakeError("operation_forbidden")
        return roster


def _service(request: Request) -> StaffAdminReadService:
    factory: AdminReadServiceFactory | None = request.app.state.admin_read_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        # The unbound slot is the missing dependency it is: the boundary's own closed 503
        # code, never a faked empty roster and never a foreign error domain (the intake
        # lane's `IntakeError` literal stays exactly the codes that lane owns).
        raise RosterUnavailableError()
    service = factory(resolver)
    if not isinstance(service, StaffAdminReadService) or service.resolver is not resolver:
        raise RosterUnavailableError()
    return service


def _secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise IntakeError("authentication_unavailable") from None


class AdminReadRoute(APIRoute):
    """GET-only framing first (any body is a rejected request), then the route itself.

    Mirrors the intake lane's `ProductRoute` without its POST half: a read boundary never
    accepts a body, so a body on ANY method here is refused before the route runs.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                if await _body(request):
                    raise ValueError
            except Exception:
                return admin_read_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            try:
                return await original(Request(request.scope, receive=replay))
            except IntakeError as exc:
                # This boundary answers only its OWN closed code set: an intake code from
                # another plane is never echoed into a contract that does not name it.
                return admin_read_error(exc.code if exc.code in STATUS else "admin_read_unavailable")
            except RosterUnavailableError:
                return admin_read_error("admin_read_unavailable")
            except RequestValidationError:
                return admin_read_error("invalid_request")
            except AuthenticationError:
                return admin_read_error("authentication_unavailable")
            except Exception:
                return admin_read_error()

        return checked


admin_read_router = APIRouter(route_class=AdminReadRoute)


def is_admin_read_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in admin_read_router.routes)


@admin_read_router.get(ROUTE_PATH, response_model=StaffAdminVinculos, responses=ERRORS)
async def read_admin_vinculos(request: Request) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    roster = await _service(request).read(_secret(request))
    return Response(content=roster.model_dump_json(), media_type="application/json")
