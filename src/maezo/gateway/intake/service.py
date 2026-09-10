"""Durable AUTH admission; never infers a process start from an HTTP acknowledgement."""

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeReceipt

from .models import IntakeAuthority, IntakeError, IntakeStore, request_bytes


def session_ceiling(session: ResolvedHumanSession) -> datetime:
    """Retain both identity deadlines; principal equality contains neither."""
    return min(session.record.expires_at, session.membership.reviewed_until)


class IntakeService:
    def __init__(
        self, resolver: HumanSessionResolver, authority: IntakeAuthority, store: IntakeStore
    ) -> None:
        self.resolver, self.authority, self.store = resolver, authority, store

    async def _session(
        self, secret: str, csrf: str | None = None, origin: str | None = None, *, mutation: bool = False
    ) -> ResolvedHumanSession:
        try:
            session = await self.resolver.resolve(secret)
            if mutation and (
                session.membership.audience != "provider"
                or origin != self.resolver.settings.public_origin
                or not csrf
                or not secrets.compare_digest(csrf, session.record.csrf_token)
            ):
                raise IntakeError("authentication_unavailable")
            if datetime.now(UTC) >= session_ceiling(session):
                raise IntakeError("authentication_unavailable")
            return session
        except Exception:
            raise IntakeError("authentication_unavailable") from None

    async def submit(
        self, secret: str, csrf: str, origin: str, request: AuthIntakeSubmission
    ) -> IntakeReceipt:
        return await self.submit_frozen(secret, csrf, origin, request, freeze=lambda value: value)

    async def submit_frozen[T](
        self,
        secret: str,
        csrf: str,
        origin: str,
        request: AuthIntakeSubmission,
        *,
        freeze: Callable[[IntakeReceipt], T],
    ) -> T:
        first = await self._session(secret, csrf, origin, mutation=True)
        grant = await self.authority.admit(first.principal, request)
        digest = hashlib.sha256(request_bytes(request)).hexdigest()
        if (
            grant.principal != first.principal
            or grant.request_digest != digest
            or grant.valid_until <= datetime.now(UTC)
        ):
            raise IntakeError("operation_forbidden")
        current = await self._session(secret, csrf, origin, mutation=True)
        if current.principal != first.principal:
            raise IntakeError("conflict")
        ceiling = min(session_ceiling(first), session_ceiling(current), grant.valid_until)
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("operation_forbidden")
        grant = grant.model_copy(update={"valid_until": ceiling})
        from .postgres import PostgresIntakeStore

        if isinstance(self.store, PostgresIntakeStore) and self.store.native_dispatch is not None:
            from .native_source_lifecycle import AuthCallerBinding

            caller = await AuthCallerBinding.resolve(self.resolver, secret, ceiling=ceiling)
            if caller.principal != first.principal:
                raise IntakeError("operation_forbidden")
            result = await self.store.admit(grant, request, caller=caller)
        else:
            result = await self.store.admit(grant, request)
        if result.command_id != request.command_id:
            raise IntakeError("conflict")
        # A committed intake may survive a lost/revoked response; recovery uses the same
        # command, and dispatch independently rechecks live authority before any effect.
        current = await self._session(secret, csrf, origin, mutation=True)
        if current.principal != first.principal:
            raise IntakeError("operation_forbidden")
        ceiling = min(ceiling, session_ceiling(current))
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("operation_forbidden")
        frozen = freeze(result)
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("operation_forbidden")
        return frozen

    async def read(self, secret: str, intake_ref: str) -> IntakeReceipt:
        return await self.read_frozen(secret, intake_ref, freeze=lambda value: value)

    async def read_frozen[T](
        self, secret: str, intake_ref: str, *, freeze: Callable[[IntakeReceipt], T]
    ) -> T:
        first = await self._session(secret)
        until = await self.authority.read(first.principal, intake_ref)
        if until.tzinfo is None or datetime.now(UTC) >= until:
            raise IntakeError("operation_forbidden")
        ceiling = min(session_ceiling(first), until)
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("operation_forbidden")
        result = await self.store.read(first.principal.tenant, first.principal.principal_ref, intake_ref)
        current = await self._session(secret)
        final_until = await self.authority.read(current.principal, intake_ref)
        if (
            current.principal != first.principal
            or final_until.tzinfo is None
            or datetime.now(UTC) >= min(ceiling, session_ceiling(current), final_until)
        ):
            raise IntakeError("operation_forbidden")
        frozen = freeze(result)
        if datetime.now(UTC) >= min(ceiling, session_ceiling(current), final_until):
            raise IntakeError("operation_forbidden")
        return frozen
