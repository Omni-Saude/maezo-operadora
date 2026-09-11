"""Authentic initial acquisition → classified inputs → qualified worker metadata.

Trace: native v2 partial FetchRow; C acquisition ownership; E04 mechanical
amendment exact amount. This is not a lifecycle/effect transport. Existing
generic harness fetch/complete routes must not consume these acquisitions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, SupportsIndex

from maezo.tools.workers.auth_exact_amount import TYPE, hydrate_auth_amount
from maezo.tools.workers.harness import ExternalTask

from .models import FetchResult, FetchUnavailableError, PreparedFetch, decode, encode, require, token
from .transport import NativeFetchClient

_MINT = object()


@dataclass(frozen=True, slots=True, repr=False)
class MetadataLease:
    """Qualified engine metadata and family prerequisites, not inferred fields.

    The provider binds native task/execution/definition to its real business key
    and activity. It must qualify the opaque AUTH guide worker/event/TISS profile
    before supplying portal-auth-intake.v1. The v2 fetch response omits both fields.
    """

    command_binding: bytes
    receipt_ref: str
    task_ref: str
    acquisition_ref: str
    lease_revision: int
    definition_id: str
    process_instance_id: str
    execution_id: str
    process_key: str
    business_key: str
    activity_id: str
    input_profile: str
    not_before: datetime
    valid_until: datetime
    live: Callable[[], None] = field(repr=False)


class WorkerMetadataProvider(ABC):
    @abstractmethod
    async def acquire(self, command_binding: bytes, receipt: bytes, task_ref: str) -> MetadataLease:
        """Current classified read authority and exact acquisition-bound metadata.

        Native v2 currently has no such projection operation. A separately
        qualified source is required; no default/raw engine REST fallback exists.
        """
        raise NotImplementedError


class AcquiredInputs:
    """Process-local, single-consumer inputs; receipt is historical, guard current.

    Cannot be copied/serialized into a replacement acquisition owner. Native
    disposition still requires a separately admitted v2 lifecycle capability.
    """

    __slots__ = ("_expected", "_receipt", "_row", "_snapshot", "_current", "_clock", "_consumed")

    def __init__(
        self,
        expected: PreparedFetch,
        receipt: bytes,
        row: bytes,
        snapshot: bytes,
        current: Callable[[], None],
        clock: Callable[[], datetime],
        *,
        _mint: object,
    ) -> None:
        require(_mint is _MINT)
        for name, value in (
            ("_expected", expected),
            ("_receipt", receipt),
            ("_row", row),
            ("_snapshot", snapshot),
            ("_current", current),
            ("_clock", clock),
            ("_consumed", False),
        ):
            object.__setattr__(self, name, value)

    _expected: PreparedFetch
    _receipt: bytes
    _row: bytes
    _snapshot: bytes
    _current: Callable[[], None]
    _clock: Callable[[], datetime]
    _consumed: bool

    def __setattr__(self, name: str, value: Any) -> None:
        raise FetchUnavailableError()

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise FetchUnavailableError()

    def __copy__(self) -> Any:
        raise FetchUnavailableError()

    def __deepcopy__(self, memo: Any) -> Any:
        raise FetchUnavailableError()

    @property
    def technical_receipt(self) -> bytes:
        return self._receipt

    def _guard(self) -> None:
        self._current()
        # A historical expiry is only an upper bound. D/current metadata/native
        # effects independently check revocation and actual acquisition state.
        until = decode(self._snapshot)["lock_expires_at"]
        now = self._clock()
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = now - epoch
        now_millis = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
        require(now_millis < until)

    async def worker_task(self, source: WorkerMetadataProvider) -> ExternalTask:
        require(not self._consumed and isinstance(source, WorkerMetadataProvider))
        object.__setattr__(self, "_consumed", True)  # No repeat after any await/uncertainty.
        row, snapshot, receipt = decode(self._row), decode(self._snapshot), decode(self._receipt)
        try:
            self._guard()
            lease = await source.acquire(self._expected.binding, self._receipt, row["id"])

            def current() -> None:
                self._guard()
                lease.live()
                require(
                    lease.command_binding == self._expected.binding
                    and lease.receipt_ref == receipt["receipt_ref"]
                )
                require(lease.task_ref == row["id"] and lease.acquisition_ref == snapshot["acquisition_ref"])
                require(
                    type(lease.lease_revision) is int and lease.lease_revision == snapshot["lease_revision"]
                )
                require(
                    lease.definition_id == row["definition_id"]
                    and lease.process_instance_id == row["process_instance_id"]
                )
                require(lease.execution_id == row["execution_id"])
                require(lease.process_key == self._expected.profile.value()["target"]["process_key"])
                require(lease.input_profile == self._expected.profile.input_profile)
                token(lease.business_key)
                token(lease.activity_id)
                self._guard()
                require(lease.not_before <= self._clock() < lease.valid_until)

            current()
            values: dict[str, Any] = {}
            for name, wire in row["variables"].items():
                if wire["type"] == TYPE:
                    values[name] = hydrate_auth_amount(wire, input_profile=lease.input_profile)
                else:
                    # v2 already validated the exact tag/value classification.
                    # Spin json remains bounded decoded JSON, never valueInfo or
                    # Java ObjectValue. No legacy engine envelope coercion runs.
                    values[name] = wire["value"]
            task = ExternalTask(
                task_id=row["id"],
                topic=row["topic"],
                process_instance_id=row["process_instance_id"],
                business_key=lease.business_key,
                worker_id=row["worker_id"],
                variables=values,
                retries=row["retries"],
                process_definition_key=lease.process_key,
                activity_id=lease.activity_id,
            )
            current()  # Actual worker DTO frozen before the last ceiling check.
            return task
        except Exception:
            raise FetchUnavailableError() from None


class NativeFetchAdapter:
    def __init__(
        self, client: NativeFetchClient, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        require(type(client) is NativeFetchClient)
        self._client, self._clock = client, clock

    async def acquire(self, expected: PreparedFetch) -> tuple[FetchResult, tuple[AcquiredInputs, ...]]:
        initial = await self._client.fetch(expected)
        if initial.status != "executed":
            return initial, ()
        current = self._client.claim(expected, initial)
        require(initial.receipt is not None)
        assert initial.receipt is not None
        receipt = decode(initial.receipt)
        acquired = tuple(
            AcquiredInputs(
                expected, initial.receipt, row, encode(snapshot), current, self._clock, _mint=_MINT
            )
            for row, snapshot in zip(initial.rows, receipt["acquisitions"], strict=True)
        )
        for item in acquired:
            item._guard()
        current()
        return initial, acquired
