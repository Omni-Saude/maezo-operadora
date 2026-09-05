"""Durable dedup registry backed by `driver_idempotency` (gaps `WEBHOOK-WAMID-DEDUP` /
`DRIVER-IDEMPOTENCY-ORPHAN-TABLE`; owner decisions R-071, R-072, R-073 of 2026-09-04).

THE DEFECT THIS CLOSES. The WhatsApp receiver captured Meta's `wamid` and only LOGGED it
(`webhooks/whatsapp/dispatch.py` said so itself: "NO wamid DEDUP"), while dispatch was
synchronous and answered 500 whenever a batch failed. Meta re-delivers on any non-2xx and on
timeout, so one slow Helena turn produced a SECOND complete turn for the same inbound message:
a duplicate reply to the beneficiary plus a duplicate LLM spend and a duplicate checkpointed
turn — the landmine `docs/adr/0024-durable-idempotency-resume-inbound-drivers.md:7` names as
"dupla resposta ao beneficiario".

THE STORE IS THE EXISTING TABLE, NOT A NEW ONE (R-073, option 2 `REPROPOR`). `driver_idempotency`
was created by `migrations/versions/0003_a2a_idempotency.py:61-72` for ADR-0024's
`InboundDriver`/`ResumeDriver`, which were never built — it had zero readers and zero writers
outside the LGPD retention inventory. `migrations/versions/0010_webhook_wamid_dedup.py` declares
the new use in the schema and adds the `status` column the claim/commit protocol needs.

PROTOCOL (why claim/commit and not a single INSERT). `claim()` writes a `'pending'` row BEFORE
the work runs and `mark_processed()` seals it AFTER the beneficiary was actually answered:

    claim   -> True  : first delivery within the TTL. The caller runs the turn/send.
            -> False : a duplicate inside the window. The caller must NOT run the effect.
    mark_processed   : the effect completed; the key stays suppressive until `expires_at`.
    release          : the effect FAILED; the claim is withdrawn so a redelivery gets a real
                       second chance. Without this, a transient failure would be laundered into
                       permanent, silent message loss by the dedup itself.

A `'pending'` row older than `lease_s` is RE-CLAIMABLE (see `CLAIM_SQL`). That is the recovery
path for the one case `release` cannot cover: a receiver killed mid-turn never runs its `except`
branch, and the abandoned claim would otherwise suppress the redelivery of a message nobody
answered. The lease is a bounded in-flight budget, not a TTL: it must exceed the longest honest
turn and stay far under the TTL.

CONCURRENCY. `CLAIM_SQL` is a single `INSERT ... ON CONFLICT (key) DO UPDATE ... WHERE`, so two
replicas racing on the same key serialize on the row lock and the loser re-evaluates the `WHERE`
against the winner's COMMITTED row — exactly one gets the claim. No advisory lock is needed (the
A2A store's `pg_advisory_xact_lock` exists because its claim spans two statements; this one does
not).

FAIL-CLOSED. Every database failure raises :class:`DedupRegistryUnavailableError` rather than
returning a value. A registry that answered "first delivery" while it could not read its own
table would re-open the duplicate-reply landmine precisely when the platform is already
degraded, and one that answered "duplicate" would silently drop real messages. The caller
(`webhooks/whatsapp/app.py`) turns the exception into a non-2xx so Meta retries — no dispatch, no
fabricated success.

PHI (LGPD/ADR-0006/ADR-0035). The `key` column NEVER carries a raw `wamid`. Meta's message id
embeds the counterpart phone number as base64 inside its own payload (proven, not assumed:
`tests/unit/platform/webhooks/whatsapp/test_dedup_keys.py
::test_a_real_shaped_wamid_leaks_the_phone_number_in_base64`), so a raw `wamid` in a durable
table is a stored identifier. Keys are built from the KEYED HMAC pseudonym produced by the
vault-keyed `Pseudonymizer` (`webhooks/whatsapp/security.py::hash_message_id`, the same one
`hash_phone` uses), which is irreversible without `PHI_HMAC_KEY`.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

logger = structlog.get_logger(__name__)

#: The repurposed table (`0003_a2a_idempotency.py:61-72`, redeclared by 0010).
DEDUP_TABLE: Final[str] = "driver_idempotency"

#: The migration that declares the repurposed use (R-073). Asserted against the migration file by
#: `tests/unit/platform/test_migration_0010_webhook_wamid_dedup.py`, so the two cannot drift.
DEDUP_MIGRATION_REVISION: Final[str] = "0010"

#: Claim states. `pending` = in flight; `processed` = the beneficiary was answered.
STATUS_PENDING: Final[str] = "pending"
STATUS_PROCESSED: Final[str] = "processed"
#: The CLOSED vocabulary `ck_driver_idempotency_status` enforces in the DDL.
DEDUP_STATUSES: Final[frozenset[str]] = frozenset({STATUS_PENDING, STATUS_PROCESSED})

#: Dedup window default, in seconds. NOT invented here: `docs/adr/0024-durable-idempotency-resume-
#: inbound-drivers.md:60` fixes "TTL default 86400s (24h, igual ao whatsapp idempotency)" and
#: `docs/runbooks/whatsapp-webhook.md` §3 documents the same 24h for `wamid` deduplication —
#: those two lines are this default's provenance. Meta re-delivers an unacknowledged webhook with
#: backoff over a window far longer than one request, which is why the window is measured in
#: hours and not in seconds; a deployment that needs a different one sets
#: `WHATSAPP_WAMID_DEDUP_TTL_S` (`webhooks/whatsapp/settings.py`) rather than editing code.
DEFAULT_TTL_S: Final[float] = 86400.0

#: In-flight lease default, in seconds: how long a `pending` claim suppresses redelivery before it
#: is considered abandoned (crashed receiver) and becomes re-claimable. Bounded ABOVE the longest
#: honest synchronous turn and far BELOW the TTL. 120s is the same order as the receiver's own
#: request budget (`docs/runbooks/whatsapp-webhook.md` §1 records Meta's <20s ack expectation, and
#: `HelenaDispatcher.dispatch` runs LLM + DMN + engine calls inside one request).
DEFAULT_LEASE_S: Final[float] = 120.0

#: Atomic claim. Inserts a `pending` row; on conflict it takes the row over ONLY if the previous
#: claim's dedup window has expired OR the previous claim is a `pending` one older than the lease
#: (an abandoned in-flight claim — see the module docstring). `RETURNING key` distinguishes a won
#: claim (one row) from a suppressed duplicate (no rows).
CLAIM_SQL: Final[str] = (
    "INSERT INTO driver_idempotency (key, tenant, expires_at, status) "
    "VALUES ($1, $2, now() + make_interval(secs => $3::double precision), 'pending') "
    "ON CONFLICT (key) DO UPDATE "
    "SET tenant = EXCLUDED.tenant, "
    "expires_at = EXCLUDED.expires_at, "
    "created_at = now(), "
    "status = 'pending' "
    "WHERE driver_idempotency.expires_at <= now() "
    "OR (driver_idempotency.status = 'pending' "
    "AND driver_idempotency.created_at <= now() - make_interval(secs => $4::double precision)) "
    "RETURNING key"
)

#: Seals a won claim. Only ever moves `pending` -> `processed`; never resurrects an expired row
#: and never re-seals one (so a late `mark_processed` after another replica re-claimed the key
#: cannot steal that replica's claim).
MARK_PROCESSED_SQL: Final[str] = (
    "UPDATE driver_idempotency SET status = 'processed' "
    "WHERE key = $1 AND tenant = $2 AND status = 'pending' "
    "RETURNING key"
)

#: Withdraws a claim whose effect FAILED. Deletes only a still-`pending` row: a `processed` row
#: means the beneficiary was answered and must keep suppressing redelivery.
RELEASE_SQL: Final[str] = (
    "DELETE FROM driver_idempotency WHERE key = $1 AND tenant = $2 AND status = 'pending' RETURNING key"
)


class DedupRegistryUnavailableError(RuntimeError):
    """The dedup registry could not be read/written — never "no duplicate" by default.

    Carries no key material and no driver detail beyond the exception type/message of the cause:
    the key is a keyed pseudonym, but the exception text travels into logs and (for the caller's
    504/500 body) potentially into an HTTP response, so it stays deliberately thin.
    """


class DedupRegistry(Protocol):
    """The contract the WhatsApp inbound path and the outbound send guard consume."""

    async def claim(self, key: str, *, ttl_s: float = DEFAULT_TTL_S) -> bool:
        """`True` -> first delivery, the caller runs the effect. `False` -> suppress it."""
        ...

    async def mark_processed(self, key: str) -> None:
        """Seal a won claim once the effect really happened."""
        ...

    async def release(self, key: str) -> None:
        """Withdraw a won claim whose effect failed, so a redelivery can retry it."""
        ...

    async def aclose(self) -> None:
        """Release whatever resources the implementation owns. Idempotent.

        Part of the contract (rather than an implementation detail of the Postgres one) because
        the composition root closes the registry on drain without knowing which implementation it
        wired — `platform/webhooks/service.py`'s STEP D.
        """
        ...


def inbound_dedup_key(*, tenant: str, message_pseudonym: str) -> str:
    """Dedup key for ONE inbound WhatsApp message (the `wamid` leg).

    `message_pseudonym` MUST already be the keyed `hk1_` pseudonym of the wamid
    (`webhooks/whatsapp/security.py::hash_message_id`) — this function does no hashing, so that
    there is exactly one place in the repo where a raw wamid could turn into a stored key, and it
    is the one that owns the vault key.
    """
    return f"wa:inbound:{tenant}:{message_pseudonym}"


def outbound_dedup_key(*, tenant: str, message_pseudonym: str, occurrence: int) -> str:
    """Dedup key for the Nth outbound send caused by ONE inbound message (the `send` leg).

    `occurrence` is 1-based and counts sends WITHIN one handling of one inbound message. Today
    exactly one outbound send exists per inbound message (Helena's `respond` node, or the fixed
    non-text ack), so `occurrence` is always 1 on the live path; it is part of the key anyway
    because a future turn that sends two messages would otherwise have its SECOND message
    suppressed by its own first — a silent truncation of a beneficiary reply, which is the exact
    failure class this module exists to prevent.
    """
    return f"wa:outbound:{tenant}:{message_pseudonym}:{occurrence}"


class PostgresDriverIdempotencyRegistry:
    """asyncpg-backed :class:`DedupRegistry` over `driver_idempotency` (schema-per-tenant).

    Mirrors `a2a.idempotency.PostgresIdempotencyStore` / `gateway.audit_postgres.PostgresAuditSink`
    exactly: `normalize_dsn`, `schema_for_tenant` (which also VALIDATES the tenant as a schema
    identifier — anti-injection), a lazy pool, and `setup=` (never `init=`) to re-pin the
    `search_path` on EVERY acquire. `init=` would pin it once per physical connection, and
    asyncpg RESETs session state on release — so the second claim on a reused connection would
    look for `driver_idempotency` in `public` and fail (fix #55, ported here for the same reason).
    """

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        pool: asyncpg.Pool | None = None,
        lease_s: float = DEFAULT_LEASE_S,
    ) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool
        self._owns_pool = pool is None
        self._lease_s = lease_s

    @property
    def tenant(self) -> str:
        return self._tenant

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by `schema_for_tenant()` in `__init__` (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def _fetchval(self, sql: str, *args: Any) -> Any:
        """Run one statement, translating EVERY driver failure into the fail-closed error.

        `Exception` (not `asyncpg.PostgresError`) on purpose: a pool that cannot be created, a DNS
        failure, a cancelled connect and a `relation does not exist` are the same fact to the
        caller — the registry cannot answer, so nothing may be dispatched on its word.
        """
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await conn.fetchval(sql, *args)
        except Exception as exc:  # noqa: BLE001 — see the docstring: total, and never silent.
            raise DedupRegistryUnavailableError(f"{type(exc).__name__}: {exc}") from exc

    async def claim(self, key: str, *, ttl_s: float = DEFAULT_TTL_S) -> bool:
        """Atomically claim `key` for `ttl_s` seconds. `False` means "duplicate — suppress"."""
        claimed = await self._fetchval(CLAIM_SQL, key, self._tenant, float(ttl_s), self._lease_s)
        return claimed is not None

    async def mark_processed(self, key: str) -> None:
        sealed = await self._fetchval(MARK_PROCESSED_SQL, key, self._tenant)
        if sealed is None:
            # Not an error: the claim's lease expired and another replica re-claimed the key, or
            # the TTL swept the row. Announced rather than silent, because a run of these means
            # the lease is shorter than the real turn duration — a tuning fact operators need.
            logger.warning("dedup_claim_not_sealed", tenant=self._tenant, dedup_key=key)

    async def release(self, key: str) -> None:
        released = await self._fetchval(RELEASE_SQL, key, self._tenant)
        if released is None:
            logger.warning("dedup_claim_not_released", tenant=self._tenant, dedup_key=key)

    async def aclose(self) -> None:
        """Close the pool, if this registry opened it (an injected pool belongs to its owner)."""
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None
