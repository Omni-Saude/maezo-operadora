"""D6 recoverable human relay: committed outbox -> authenticated receipt -> audited result."""

import asyncio
import contextlib
from dataclasses import dataclass

from .outbox import DeliveryLease, HumanOutboxError, LeaseLostError, PostgresHumanOutbox, _positive_seconds
from .projection import verify_engine_receipt
from .transport import EngineConflictError, HumanEngineTransport


@dataclass(frozen=True)
class RelaySettings:
    lease_seconds: int
    retry_seconds: int
    poll_seconds: int

    def __post_init__(self) -> None:
        for value in (self.lease_seconds, self.retry_seconds, self.poll_seconds):
            _positive_seconds(value)


class HumanCommandRelay:
    def __init__(
        self, *, outbox: PostgresHumanOutbox, transport: HumanEngineTransport, settings: RelaySettings
    ) -> None:
        if outbox.scope != transport.scope:
            raise HumanOutboxError("human relay scope mismatch")
        self._outbox = outbox
        self._transport = transport
        self._settings = settings

    async def run_once(self) -> bool:
        lease = await self._outbox.claim(lease_seconds=self._settings.lease_seconds)
        if lease is None:
            return False
        try:
            # All PG transactions have exited before any network call. Every takeover
            # queries first, including first dispatch, because process-local knowledge
            # cannot establish whether an earlier worker committed at the engine.
            await self._outbox.require_lease(lease)
            receipt = await self._transport.receipt(lease.command)
            if receipt is None:
                await self._outbox.require_lease(lease)
                receipt = await self._transport.dispatch(lease.command)
            verify_engine_receipt(receipt, lease.command)
            await self._outbox.finish(lease, receipt=receipt)
        except EngineConflictError as conflict:
            # The authenticated endpoint gives a definitive technical refusal. No
            # engine commit fields are produced for it; auditing is still mandatory.
            try:
                await self._outbox.finish(lease, conflict=conflict.code)
            except Exception:
                await self._pending(lease)
        except LeaseLostError:
            pass  # A stale worker must not update or release the successor's lease.
        except Exception:
            # Includes response loss AND final-audit failure: preserve reconciliation.
            await self._pending(lease)
        return True

    async def _pending(self, lease: DeliveryLease) -> None:
        # Durable lease expiry recovers when the database itself is unavailable.
        with contextlib.suppress(Exception):
            await self._outbox.retry_later(lease, retry_seconds=self._settings.retry_seconds)

    async def run(self, stop: asyncio.Event) -> None:
        """Caller owns lifecycle/health. Database errors propagate; no silent unhealthy loop."""
        while not stop.is_set():
            worked = await self.run_once()
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._settings.poll_seconds)
