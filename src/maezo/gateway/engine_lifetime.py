"""Local C ownership only (ADR-0049; reviewed C lifetime contract, stage 1).

These handles are process-local consistency/lifetime controls, not authenticated D
admission or native acquisition authority. No local generation enters the v1 wire.
An awaiting task's cancellation is never evidence that its thread/effect terminated.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, SupportsIndex, TypeVar

from maezo.gateway.engine_contracts import EngineCapabilityError, EngineRefusalCode

_T = TypeVar("_T")


def _denied() -> EngineCapabilityError:
    return EngineCapabilityError(EngineRefusalCode.IDENTITY_UNAVAILABLE)


def _observe(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


def _digest(value: str) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True, slots=True)
class ProfileSelectionKey:
    """Value selector; not a bearer grant or native authority."""

    profile_document: bytes
    profile_digest: str
    config_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.profile_document) is not bytes
            or not self.profile_document
            or not _digest(self.profile_digest)
            or not _digest(self.config_digest)
            or hashlib.sha256(self.profile_document).hexdigest() != self.profile_digest
        ):
            raise _denied()


@dataclass(frozen=True, slots=True)
class CallerSelection:
    identity_document: bytes
    config_digest: str
    profiles: tuple[ProfileSelectionKey, ...]

    def __post_init__(self) -> None:
        if (
            type(self.identity_document) is not bytes
            or not self.identity_document
            or not _digest(self.config_digest)
            or type(self.profiles) is not tuple
            or not self.profiles
            or any(type(p) is not ProfileSelectionKey for p in self.profiles)
        ):
            raise _denied()
        for profile in self.profiles:
            profile.__post_init__()
        if any(p.config_digest != self.config_digest for p in self.profiles) or len(
            {p.profile_digest for p in self.profiles}
        ) != len(self.profiles):
            raise _denied()


class _Opaque:
    __slots__ = ()

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise TypeError("local lifetime handles cannot be serialized")

    def __copy__(self) -> Any:
        raise TypeError("local lifetime handles cannot be copied")

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise TypeError("local lifetime handles cannot be copied")


@dataclass(frozen=True, slots=True)
class SubmissionSnapshot:
    sequence: int
    operation: str
    profile_digest: str
    body_digest: str
    state: str
    response_digest: str


class Submission(_Opaque):
    __slots__ = ("_owner",)

    def __init__(self, owner: LocalLifetimeOwner) -> None:
        self._owner = owner

    def submitted(self) -> None:
        self._owner._transition(self, "submitted")

    def acknowledged(self, response_digest: str) -> None:
        if not _digest(response_digest):
            raise _denied()
        self._owner._transition(self, "acknowledged", response_digest)

    def refused(self) -> None:
        self._owner._transition(self, "refused")

    def ambiguous(self) -> None:
        self._owner._transition(self, "ambiguous")


class _Child(_Opaque):
    __slots__ = ("borrower", "terminal", "execution", "escaped", "seen", "unproven")

    def __init__(self, borrower: LifetimeBorrower) -> None:
        self.borrower = borrower
        # This signal is never the caller's cancellable asyncio wrapper. Only a
        # callback observing the actual execution future may complete it.
        self.terminal: Future[Any] = Future()
        self.terminal.set_running_or_notify_cancel()
        self.execution: Future[Any] | asyncio.Future[Any] | None = None
        self.escaped = False
        self.seen: set[Future[Any] | asyncio.Future[Any]] = set()
        self.unproven = False


class LocalLifetimeOwner(_Opaque):
    """Owns local children and an in-memory ambiguity journal. Not restart authority."""

    __slots__ = (
        "_selection",
        "_lock",
        "_borrowers",
        "_children",
        "_records",
        "_closing",
        "_poisoned",
        "_pool",
        "_revoked",
    )

    def __init__(self, selection: CallerSelection) -> None:
        if type(selection) is not CallerSelection:
            raise _denied()
        selection.__post_init__()
        # Store immutable primitives, not even a caller-owned frozen dataclass alias.
        self._selection = (
            selection.identity_document,
            selection.config_digest,
            tuple((p.profile_document, p.profile_digest, p.config_digest) for p in selection.profiles),
        )
        self._lock = threading.RLock()
        self._borrowers: dict[LifetimeBorrower, tuple[str, ...]] = {}
        self._children: dict[_Child, _Child] = {}
        self._records: dict[Submission, tuple[LifetimeBorrower, SubmissionSnapshot]] = {}
        self._revoked: set[LifetimeBorrower] = set()
        self._closing = False
        self._poisoned = False
        self._pool: ThreadPoolExecutor | None = None

    @property
    def selection(self) -> CallerSelection:
        identity, config, profiles = self._selection
        return CallerSelection(identity, config, tuple(ProfileSelectionKey(*p) for p in profiles))

    @property
    def submissions(self) -> tuple[SubmissionSnapshot, ...]:
        with self._lock:
            return tuple(
                SubmissionSnapshot(
                    *(s.sequence, s.operation, s.profile_digest, s.body_digest, s.state, s.response_digest)
                )
                for _, s in self._records.values()
            )

    @property
    def pending_children(self) -> int:
        with self._lock:
            return len(self._children)

    @property
    def poisoned(self) -> bool:
        with self._lock:
            return self._poisoned

    def borrow(self, profile_digests: tuple[str, ...]) -> LifetimeBorrower:
        with self._lock:
            if (
                self._closing
                or self._poisoned
                or type(profile_digests) is not tuple
                or not profile_digests
                or any(type(d) is not str for d in profile_digests)
                or len(set(profile_digests)) != len(profile_digests)
                or not set(profile_digests) <= {p[1] for p in self._selection[2]}
            ):
                raise _denied()
            borrower = LifetimeBorrower(self)
            self._borrowers[borrower] = profile_digests
            return borrower

    def _check(self, borrower: LifetimeBorrower, profile_digest: str = "") -> None:
        with self._lock:
            if (
                type(borrower) is not LifetimeBorrower
                or borrower not in self._borrowers
                or borrower._owner is not self
                or borrower in self._revoked
                or self._closing
                or self._poisoned
                or (profile_digest and profile_digest not in self._borrowers[borrower])
            ):
                raise _denied()

    def reserve(
        self, borrower: LifetimeBorrower, profile_digest: str, operation: str, body_digest: str
    ) -> Submission:
        with self._lock:
            self._check(borrower, profile_digest)
            if operation not in {"start", "correlate", "read_active", "read_history"} or not _digest(
                body_digest
            ):
                raise _denied()
            token = Submission(self)
            self._records[token] = (
                borrower,
                SubmissionSnapshot(
                    len(self._records) + 1, operation, profile_digest, body_digest, "prepared", ""
                ),
            )
            return token

    def _transition(self, token: Submission, state: str, response_digest: str = "") -> None:
        with self._lock:
            if type(token) is not Submission or token not in self._records or token._owner is not self:
                raise _denied()
            borrower, old = self._records[token]
            if state == "submitted":
                self._check(borrower, old.profile_digest)
                if old.state != "prepared":
                    raise _denied()
            elif state == "ambiguous":
                if old.state in {"acknowledged", "refused"}:
                    return
                if old.state == "prepared":
                    state = "refused"  # No submission was admitted.
                else:
                    self._poisoned = True
            elif state == "acknowledged":
                if old.state not in {"submitted", "ambiguous"}:
                    raise _denied()
            elif state == "refused":
                if old.state not in {"prepared", "submitted", "ambiguous"}:
                    raise _denied()
            else:
                raise _denied()
            self._records[token] = (
                borrower,
                SubmissionSnapshot(
                    old.sequence, old.operation, old.profile_digest, old.body_digest, state, response_digest
                ),
            )

    def _finish(self, child: _Child, future: Future[Any] | asyncio.Future[Any]) -> None:
        with self._lock:
            # Never remove by business/task ID, nor let a repeated old callback
            # remove another generation. Follow only the exact retained future.
            if (
                self._children.get(child) is not child
                or child.execution is not future
                or child.unproven
                or not future.done()
            ):
                return
            try:
                value = future.result()
                if isinstance(value, (Future, asyncio.Future)):
                    self._poisoned = child.escaped = True
                    if value is future or value in child.seen:
                        return  # Cyclic returned future: no terminal proof.
                    child.seen.add(future)
                    child.execution = value
                    if isinstance(value, asyncio.Future):
                        # A returned wrapper can finish (even with TimeoutError)
                        # while hidden thread work continues. This API has no
                        # authentic underlying terminal binding for it. Retain
                        # this exact child indefinitely, including on callbacks
                        # repeated after the wrapper or hidden work has ended.
                        child.unproven = True
                        return
                    value.add_done_callback(lambda actual: self._finish(child, actual))
                    return
                if inspect.iscoroutine(value):
                    if inspect.getcoroutinestate(value) not in {inspect.CORO_CREATED, inspect.CORO_CLOSED}:
                        self._poisoned = True
                        return  # Another task may own it; closing is not terminal proof.
                    value.close()
                    raise _denied()
                if isinstance(value, Iterator):
                    # A generator may execute finally during close. If no close
                    # exists, or close fails, its lifetime remains unproven.
                    self._poisoned = True
                    if not inspect.isgenerator(value):
                        return
                    try:
                        value.close()
                    except BaseException:
                        return
                    raise _denied()
                if isinstance(value, (Awaitable, AsyncIterator)):
                    self._poisoned = True
                    return  # Unknown running child: quarantine, never terminal proof.
                if child.escaped:
                    raise _denied()
            except BaseException as exc:
                child.terminal.set_exception(exc)
            else:
                child.terminal.set_result(value)
            del self._children[child]
            if self._closing and not self._children and self._pool is not None:
                self._pool.shutdown(wait=False)

    async def _run(self, borrower: LifetimeBorrower, function: Callable[[], Any], *, sync: bool) -> Any:
        with self._lock:
            self._check(borrower)
            child = _Child(borrower)
            self._children[child] = child
            try:
                if sync:
                    if self._pool is None:
                        self._pool = ThreadPoolExecutor(thread_name_prefix="maezo-local-lifetime")

                    def invoke_sync() -> Any:
                        borrower.check()
                        return function()

                    actual: Future[Any] | asyncio.Future[Any] = self._pool.submit(invoke_sync)
                else:

                    async def invoke() -> Any:
                        borrower.check()
                        return await function()

                    actual = asyncio.create_task(invoke())
                child.execution = actual
                actual.add_done_callback(lambda future: self._finish(child, future))
            except BaseException:
                del self._children[child]
                raise
        try:
            wrapper = asyncio.wrap_future(child.terminal)
            wrapper.add_done_callback(_observe)
            value = await asyncio.shield(wrapper)
        except asyncio.CancelledError:
            with self._lock:
                self._revoked.add(borrower)
            raise
        borrower.check()
        return value

    async def _close(self, borrower: LifetimeBorrower | None, timeout: float | None) -> bool:  # noqa: ASYNC109
        if timeout is not None and (
            type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout < 0
        ):
            raise ValueError("invalid local drain timeout")
        with self._lock:
            if borrower is None:
                self._closing = True
                for item in self._borrowers:
                    self._revoked.add(item)
            elif (
                type(borrower) is LifetimeBorrower and borrower in self._borrowers and borrower._owner is self
            ):
                self._revoked.add(borrower)
            else:
                raise _denied()
            pending = [c.terminal for c in self._children if borrower is None or c.borrower is borrower]
            if self._closing and not self._children and self._pool is not None:
                self._pool.shutdown(wait=False)
        if not pending:
            return True
        # Wait on separate asyncio wrappers: deadline/caller cancellation cannot
        # cancel a thread, task, or its private terminal signal.
        waits = [asyncio.wrap_future(future) for future in pending]
        for wait in waits:
            wait.add_done_callback(_observe)
        await asyncio.wait(waits, timeout=timeout)
        for future in waits:
            if future.done() and not future.cancelled():
                future.exception()
        return all(future.done() for future in pending)

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109 - drain, not cancellation
        return await self._close(None, timeout)


class LifetimeBorrower(_Opaque):
    __slots__ = ("_owner",)

    def __init__(self, owner: LocalLifetimeOwner) -> None:
        self._owner = owner

    def check(self, profile_digest: str = "", *, selection: CallerSelection | None = None) -> None:
        self._owner._check(self, profile_digest)
        if selection is not None and (
            type(selection) is not CallerSelection or selection != self._owner.selection
        ):
            raise _denied()

    async def run_async(self, function: Callable[[], Awaitable[_T]]) -> _T:
        return await self._owner._run(self, function, sync=False)  # type: ignore[no-any-return]

    async def run_sync(self, function: Callable[[], _T]) -> _T:
        return await self._owner._run(self, function, sync=True)  # type: ignore[no-any-return]

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109 - drain, not cancellation
        return await self._owner._close(self, timeout)
