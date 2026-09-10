"""Dedicated PHI host for the approved E04 occurrence/inbox consumer (§6).

This is not a WorkerHarness handler: InstalledProducer owns native completion.
The owner supplies already prepared, durably retained fetch commands and exact
source-owned successor invocations. This host neither schedules replacements nor
installs policy, activation, issuer, recipient or document authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import TypeVar

from maezo.gateway.document_requests.composition import InstalledProducer
from maezo.gateway.document_requests.producer import ProducerResult
from maezo.gateway.native_fetch.adapter import NativeFetchAdapter
from maezo.gateway.native_fetch.models import (
    FetchProfile,
    FetchResult,
    FetchUnavailableError,
    PreparedFetch,
    decode,
    require,
)
from maezo.gateway.native_fetch.transport import NativeAdmissionProvider, NativeFetchClient, NativeTLS

TOPIC = "operadora.auth.request_documents"
PROCESS = "SP-OP-AUTH-001"
PROFILE = "portal-auth-intake.v1"
_Result = TypeVar("_Result", bound=ProducerResult | FetchResult)


class DocumentRequestHost:
    """One installed profile, one operation at a time, no generic completion.

    Cancellation/failure disables subsequent mutations on this host. It does not
    establish no effect: independently authorized receipt reads remain possible.
    Closing stops admission and drains the actual channels; it never unlocks or
    sends a replacement effect. Database pools remain owned by the PHI host.
    """

    def __init__(
        self,
        *,
        installed: InstalledProducer,
        tls: NativeTLS,
        profile: FetchProfile,
        authority: NativeAdmissionProvider,
        generic_topics: Iterable[str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require(type(installed) is InstalledProducer and type(profile) is FetchProfile)
        profile.__post_init__()
        target = profile.value()["target"]
        require(target["process_key"] == PROCESS and target["topic"] == TOPIC)
        require(profile.input_profile == PROFILE)
        # A generic harness cannot distinguish this profile by topic. The daemon
        # must remove this topic before admitting this dedicated installation.
        self.assert_exclusive(generic_topics)
        require(installed.worker.context.profile == profile)
        require(installed.worker.context.channel.tls == tls)
        installed.placement.live()
        self.installed, self.profile = installed, profile
        self._client = NativeFetchClient(tls, (profile,), authority, clock=clock)
        self._adapter = NativeFetchAdapter(self._client, clock=clock)
        self._busy = False
        self._stopped = False
        self._mutation_failed = False
        self._idle = asyncio.Event()
        self._idle.set()

    @staticmethod
    def assert_exclusive(generic_topics: Iterable[str]) -> None:
        """Run at installation and after any daemon worker re-registration."""
        require(TOPIC not in tuple(generic_topics))

    @property
    def busy(self) -> bool:
        return self._busy

    def _prepared(self, expected: PreparedFetch) -> None:
        require(type(expected) is PreparedFetch)
        expected.__post_init__()
        require(expected.profile == self.profile)
        # One supplied successor belongs to one occurrence, never a batch fanout.
        require(decode(expected.body)["parameters"]["maxTasks"] == 1)

    async def _operation(self, function: Callable[[], Awaitable[_Result]], *, mutation: bool) -> _Result:
        require(not self._stopped and not self._busy)
        require(not mutation or not self._mutation_failed)
        self._busy = True
        self._idle.clear()
        try:
            value = await function()
            # This original cap survives the host handoff. Do not detach results
            # into a queue: FetchResult recovery carries no renewable lease token.
            if isinstance(value, ProducerResult):
                value.current()
            return value
        except BaseException as exc:
            if mutation:
                self._mutation_failed = True
            if isinstance(exc, Exception):
                raise FetchUnavailableError() from None
            raise
        finally:
            self._busy = False
            self._idle.set()

    async def run(self, expected: PreparedFetch) -> ProducerResult | FetchResult:
        """Acquire once, consume authentic inputs directly, let producer complete.

        Empty/duplicate/uncertain fetch results cannot become input projections.
        Original commands must be retained by the caller before this entrypoint.
        """
        self._prepared(expected)

        async def perform() -> ProducerResult | FetchResult:
            result, inputs = await self._adapter.acquire(expected)
            if not inputs:
                return result
            require(len(inputs) == 1)
            return await self.installed.run(inputs[0])

        return await self._operation(perform, mutation=True)

    async def successor(
        self, expected: PreparedFetch, predecessor_command_id: str, invocation_ref: str
    ) -> ProducerResult | FetchResult:
        """Pass source intent unchanged; source.successor must admit its freeze.

        No fall-through to initial policy, and no automatic successor on ambiguity.
        The producer seals and validates the invocation and original predecessor.
        """
        self._prepared(expected)

        async def perform() -> ProducerResult | FetchResult:
            result, inputs = await self._adapter.acquire(expected)
            if not inputs:
                return result
            require(len(inputs) == 1)
            return await self.installed.successor(inputs[0], predecessor_command_id, invocation_ref)

        return await self._operation(perform, mutation=True)

    async def recover(self, request_ref: str) -> ProducerResult:
        """Current-authorized read/resume of the exact sealed producer commands."""
        return await self._operation(lambda: self.installed.recover(request_ref), mutation=False)

    async def recover_successor(self, request_ref: str, invocation_ref: str) -> ProducerResult:
        """Restore the original sealed successor; never select another invocation."""
        return await self._operation(
            lambda: self.installed.recover_successor(request_ref, invocation_ref), mutation=False
        )

    async def recover_fetch(self, expected: PreparedFetch) -> FetchResult:
        """Receipt only; even not_observed grants neither resend nor new inputs."""
        self._prepared(expected)
        return await self._operation(lambda: self._client.recover(expected), mutation=False)

    async def close(self, timeout: float) -> bool:  # noqa: ASYNC109 - bounded drain
        """One total drain budget, no cancellation or automatic unlock of work."""
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout >= 0)
        self._stopped = True
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        if self._busy and timeout > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._idle.wait(), timeout)
        drained = not self._busy
        fetch_closed = await self._client.close(max(0.0, end - loop.time()))
        producer_closed = await self.installed.close(max(0.0, end - loop.time()))
        return drained and fetch_closed and producer_closed
