"""Current authorized recovery of admission pointers, never execution or absence proof."""

from collections.abc import Callable
from datetime import UTC, datetime

from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.intake_recovery import IntakeCommandObservation, IntakeRecoveryPage

from .models import IntakeAuthority, IntakeError
from .recovery_store import PAGE_SIZE, PostgresIntakeRecoveryStore, RecoveryCandidate, instant
from .service import session_ceiling


class IntakeRecoveryService:
    def __init__(
        self,
        resolver: HumanSessionResolver,
        authority: IntakeAuthority,
        store: PostgresIntakeRecoveryStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.resolver, self.authority, self.store, self.clock = resolver, authority, store, clock
        if resolver.settings.tenant != store.scope.tenant:
            raise IntakeError()

    def _current(self, *deadlines: datetime) -> datetime:
        ceiling = min(instant(value) for value in deadlines)
        if self.clock() >= ceiling:
            raise IntakeError("operation_forbidden")
        return ceiling

    async def _session(self, secret: str) -> ResolvedHumanSession:
        try:
            session = await self.resolver.resolve(secret)
            if session.principal.tenant != self.store.scope.tenant:
                raise IntakeError()
            self._current(session_ceiling(session))
            return session
        except Exception:
            raise IntakeError("authentication_unavailable") from None

    async def _authorize(
        self, session: ResolvedHumanSession, candidates: tuple[RecoveryCandidate, ...], ceiling: datetime
    ) -> datetime:
        for candidate in candidates:
            self._current(ceiling)
            try:
                until = await self.authority.read(session.principal, candidate.item.intake_ref)
            except IntakeError:
                raise
            except Exception:
                raise IntakeError() from None
            ceiling = self._current(ceiling, until)
        return self._current(ceiling)

    async def _finish[T](
        self,
        secret: str,
        first: ResolvedHumanSession,
        candidates: tuple[RecoveryCandidate, ...],
        ceiling: datetime,
        freeze: Callable[[], T],
    ) -> T:
        current = await self._session(secret)
        if current.principal != first.principal:
            raise IntakeError("operation_forbidden")
        ceiling = self._current(ceiling, session_ceiling(current))
        ceiling = await self._authorize(current, candidates, ceiling)
        current = await self._session(secret)
        if current.principal != first.principal:
            raise IntakeError("operation_forbidden")
        ceiling = self._current(ceiling, session_ceiling(current))
        frozen = freeze()
        self._current(ceiling)
        return frozen

    async def observe_frozen[T](
        self, secret: str, command_id: str, *, freeze: Callable[[IntakeCommandObservation], T]
    ) -> T:
        first = await self._session(secret)
        ceiling = session_ceiling(first)
        candidate = await self.store.command(first.principal, command_id)
        self._current(ceiling)
        candidates = () if candidate is None else (candidate,)
        ceiling = await self._authorize(first, candidates, ceiling)
        result = IntakeCommandObservation(
            command_id=command_id,
            observation="not_observed" if candidate is None else "observed",
            intake_ref=None if candidate is None else candidate.item.intake_ref,
        )
        return await self._finish(secret, first, candidates, ceiling, lambda: freeze(result))

    async def discover_frozen[T](
        self, secret: str, cursor: str | None, *, freeze: Callable[[IntakeRecoveryPage], T]
    ) -> T:
        first = await self._session(secret)
        ceiling = session_ceiling(first)
        scan = await self.store.scan(first.principal, cursor)
        self._current(ceiling)
        if scan.cursor_until is not None:
            ceiling = self._current(ceiling, scan.cursor_until)
        # The extra next-page candidate must also be authorized, never hidden by
        # count/cursor metadata or silently omitted when authority is unavailable.
        ceiling = await self._authorize(first, scan.candidates, ceiling)
        next_cursor = None
        if len(scan.candidates) > PAGE_SIZE:
            next_cursor = await self.store.cursor(first.principal, scan, ceiling)
            self._current(ceiling)
        result = IntakeRecoveryPage(
            items=tuple(c.item for c in scan.candidates[:PAGE_SIZE]), next_cursor=next_cursor
        )
        return await self._finish(secret, first, scan.candidates, ceiling, lambda: freeze(result))
