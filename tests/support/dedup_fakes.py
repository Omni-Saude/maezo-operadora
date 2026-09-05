"""In-memory `DedupRegistry` double for the `WEBHOOK-WAMID-DEDUP` unit suites.

Deliberately lives under `tests/support/`, NOT in `src/`: an in-memory dedup store that a
composition root could accidentally wire would be a NON-DURABLE guard that looks durable — the
exact "does not survive multiple replicas" failure the owner rejected when he chose option C
(a shared durable registry) over option A (an in-memory window).

The behaviour mirrors `platform/driver_idempotency.PostgresDriverIdempotencyRegistry` closely
enough to be a fair double: claim/seal/release with the same return semantics, `unavailable`
raising the same fail-closed error, and a call log so a test can assert that the receiver never
even ASKED (e.g. the message-without-wamid path).

WHAT THIS DOUBLE DELIBERATELY DOES NOT MODEL (say it here so no unit test is read as proving it):
`ttl_s` is ACCEPTED AND IGNORED, and so is the in-flight lease. A key claimed here stays claimed
for the life of the object, so every TTL/lease property — expiry re-claim, an abandoned `pending`
claim becoming re-claimable, the concurrency of two replicas racing on one key — is real ONLY in
`tests/integration/platform/test_wamid_dedup_live_pg.py`, against a real Postgres and the real
`CLAIM_SQL`. What the unit suites prove with this double is the CALLER's protocol (claim before
the effect, seal after it, withdraw on failure), never the store's time semantics.
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

    def __init__(self, *, unavailable: bool = False, fail_ops: frozenset[str] | None = None) -> None:
        #: key -> STATUS_PENDING | STATUS_PROCESSED
        self.rows: dict[str, str] = {}
        #: every (op, key) in order, so a test can assert what was and was NOT asked.
        self.calls: list[tuple[str, str]] = []
        #: when True, every operation fails closed exactly like an unreachable Postgres.
        self.unavailable = unavailable
        #: operations that fail while the others still work — the narrow window VER-A2-WEBHOOK
        #: MINOR-2 is about (a registry blip BETWEEN the Cloud API 2xx and the seal).
        self.fail_ops = fail_ops or frozenset()
        self.closed = False

    def _check(self, operation: str = "") -> None:
        if self.unavailable or operation in self.fail_ops:
            raise DedupRegistryUnavailableError("ConnectionDoesNotExistError: fake registry is down")

    async def claim(self, key: str, *, ttl_s: float = DEFAULT_TTL_S) -> bool:
        self.calls.append(("claim", key))
        self._check("claim")
        if key in self.rows:
            return False
        self.rows[key] = STATUS_PENDING
        return True

    async def mark_processed(self, key: str) -> None:
        self.calls.append(("mark_processed", key))
        self._check("mark_processed")
        if self.rows.get(key) == STATUS_PENDING:
            self.rows[key] = STATUS_PROCESSED

    async def release(self, key: str) -> None:
        self.calls.append(("release", key))
        self._check("release")
        if self.rows.get(key) == STATUS_PENDING:
            del self.rows[key]

    async def aclose(self) -> None:
        self.closed = True
