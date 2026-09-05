"""In-memory `DedupRegistry` double for the `WEBHOOK-WAMID-DEDUP` unit suites.

Deliberately lives under `tests/support/`, NOT in `src/`: an in-memory dedup store that a
composition root could accidentally wire would be a NON-DURABLE guard that looks durable — the
exact "does not survive multiple replicas" failure the owner rejected when he chose option C
(a shared durable registry) over option A (an in-memory window).

The behaviour mirrors `platform/driver_idempotency.PostgresDriverIdempotencyRegistry` closely
enough to be a fair double: claim/seal/release with the same return semantics, `unavailable`
raising the same fail-closed error, and a call log so a test can assert that the receiver never
even ASKED (e.g. the message-without-wamid path).
"""

from __future__ import annotations

from maezo.platform.driver_idempotency import (
    DEFAULT_TTL_S,
    STATUS_PENDING,
    STATUS_PROCESSED,
    DedupRegistryUnavailableError,
)


class FakeDedupRegistry:
    """A `DedupRegistry` over a dict. Not durable, not concurrent — a unit double."""

    def __init__(self, *, unavailable: bool = False) -> None:
        #: key -> STATUS_PENDING | STATUS_PROCESSED
        self.rows: dict[str, str] = {}
        #: every (op, key) in order, so a test can assert what was and was NOT asked.
        self.calls: list[tuple[str, str]] = []
        #: when True, every operation fails closed exactly like an unreachable Postgres.
        self.unavailable = unavailable
        self.closed = False

    def _check(self) -> None:
        if self.unavailable:
            raise DedupRegistryUnavailableError("ConnectionDoesNotExistError: fake registry is down")

    async def claim(self, key: str, *, ttl_s: float = DEFAULT_TTL_S) -> bool:
        self.calls.append(("claim", key))
        self._check()
        if key in self.rows:
            return False
        self.rows[key] = STATUS_PENDING
        return True

    async def mark_processed(self, key: str) -> None:
        self.calls.append(("mark_processed", key))
        self._check()
        if self.rows.get(key) == STATUS_PENDING:
            self.rows[key] = STATUS_PROCESSED

    async def release(self, key: str) -> None:
        self.calls.append(("release", key))
        self._check()
        if self.rows.get(key) == STATUS_PENDING:
            del self.rows[key]

    async def aclose(self) -> None:
        self.closed = True
