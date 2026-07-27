"""LangGraph checkpointer backed by PostgreSQL (T3.4/F4 prod wiring; DL-0017).

This wraps `langgraph-checkpoint-postgres`'s saver so agent graph state (the LangGraph
"working layer") is durably persisted, enabling multi-turn / resume-after-restart behaviour.

ASYNC SAVER, NOT SYNC (proven, T3.4/F4 compat verification):
  The daemon and every graph execution path (`ainvoke`) run under asyncio. The sync
  `PostgresSaver`'s async methods (`aput`/`aget_tuple`/...) raise `NotImplementedError`, so a
  sync saver wired into an async graph fails at the FIRST checkpoint write. Production wiring
  therefore uses `AsyncPostgresSaver` (verified round-trip: awaited setup()/aput/aget_tuple/alist/
  adelete_thread all pass against langgraph-checkpoint 4.1.1 + langgraph-checkpoint-postgres
  3.0.5 on real Postgres 16). `InMemorySaver` (dev fallback) is likewise async-capable.

SCHEMA PROVISIONING is imperative, NOT Alembic (see
`platform/migrations/versions/0006_retire_dead_checkpoint_tables.py`): the four real tables
(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created by
the saver's own `setup()` (a plain call on the sync saver; a coroutine — `await saver.setup()` —
on `AsyncPostgresSaver`; there is NO `asetup()` in langgraph-checkpoint-postgres 3.0.5). The
schema is UPSTREAM-OWNED (its `MIGRATIONS` list is versioned by the `checkpoint_migrations` table
itself). `setup()` is idempotent; it re-runs only the migrations newer than the row recorded
there. Mirroring that DDL into Alembic would fork an upstream-owned schema and drift on every
library bump — so we await `setup()` once at daemon bootstrap instead (see
`runtime/agent_runtime/service.py::_provision_checkpointer`).

PHI / THREAD-ID DISCIPLINE (LGPD; ADR-0035 extension): `checkpoint_blobs` stores BYTEA channel
values that ARE PHI-bearing, keyed by `thread_id`. Thread ids must therefore NEVER embed a raw
identifier (phone, CPF, patient id) NOR a REVERSIBLE unkeyed hash of one. The platform convention
(webhooks/whatsapp/dispatch.py, helena/graph.py) is a KEYED `wa:{tenant}:hk1_{phone_hash}`
conversation id — where `phone_hash` is a keyed HMAC-SHA256 pseudonym (ADR-0035), tagged with the
`hk1_` marker — or an `ESC-{tenant}-{conversation_id}` process business key that wraps one.
`checkpoint_thread_config` builds a RunnableConfig from one and fail-closes UNLESS the id embeds
the `hk1_<hex>` keyed-pseudonym token: because a keyed HMAC hex and an unkeyed sha256 hex are
byte-for-byte indistinguishable, requiring the marker is the only way to reject the legacy
reversible `wa:{tenant}:{bare-sha256}` identity (not merely a bare raw number).
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import structlog
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX

logger = structlog.get_logger(__name__)

#: A raw phone/CPF-like identifier: an optionally-`+`-prefixed run of >=6 digits with nothing
#: else. Kept for a SPECIFIC error message; it is now subsumed by the keyed-token requirement
#: below (a raw number carries no `hk1_<hex>` token either), but naming the raw-identifier failure
#: mode explicitly is clearer than a generic "no keyed pseudonym" for the commonest mistake.
_RAW_NUMERIC_THREAD_ID = re.compile(r"^\+?\d{6,}$")

#: A KEYED conversation/thread pseudonym token: the `hk1_` marker (ADR-0035 extension) followed by
#: a 64-char HMAC-SHA256 hex digest. `assert_phi_safe_thread_id` REQUIRES this token to appear in
#: the thread id. This is the crux of the tightening: a keyed HMAC hex and an UNKEYED sha256 hex
#: are byte-for-byte indistinguishable (both 64 lowercase hex chars), so "not raw-numeric" alone
#: (the previous gate) still ACCEPTED the reversible `wa:{tenant}:{bare-sha256}` scheme. Requiring
#: the `hk1_` marker — emitted only by `security.hash_phone` routed through the keyed
#: `Pseudonymizer` — makes it structurally impossible for an unkeyed hash to be admitted as a
#: thread id ever again. The token is matched with `.search` (not `.fullmatch`) so it accepts both
#: a bare `wa:{tenant}:hk1_{hex}` conversation id and an `ESC-{tenant}-wa:{tenant}:hk1_{hex}`
#: business key that wraps one.
_KEYED_PSEUDONYM_TOKEN = re.compile(rf"{re.escape(KEYED_PSEUDONYM_PREFIX)}[0-9a-f]{{64}}")


def assert_phi_safe_thread_id(thread_id: str) -> str:
    """Return `thread_id` if it is PHI-safe, else raise (LGPD, T3.4/F4; ADR-0035 extension).

    `checkpoint_blobs` rows are PHI-bearing and keyed by `thread_id`, so the id must be an
    IRREVERSIBLE pseudonym. Two fail-closed checks:

      1. A bare raw phone/CPF-like number is refused (writes plaintext PHI into the table).
      2. The id MUST embed a KEYED pseudonym token (`hk1_<64-hex>`) — not merely "not a raw
         number". Because an unkeyed sha256 hex and a keyed HMAC hex are structurally identical,
         requiring the keyed marker is the ONLY way the guard can reject the legacy reversible
         `wa:{tenant}:{bare-sha256}` scheme (reversible via a precomputed table over the ~6.7e9
         BR-mobile keyspace). Derive the id via `security.hash_phone` routed through the keyed
         `Pseudonymizer` (ADR-0035 / `PHI_HMAC_KEY`): `wa:{tenant}:hk1_{hmac}` or an
         `ESC-{tenant}-...` business key that wraps one.
    """
    tid = (thread_id or "").strip()
    if not tid:
        raise ValueError("thread_id must be a non-empty string")
    if _RAW_NUMERIC_THREAD_ID.match(tid):
        raise ValueError(
            "thread_id must not be a raw phone/CPF-like number — checkpoint tables are PHI-bearing "
            "and keyed by thread_id. Derive it from a KEYED conversation id "
            "(wa:{tenant}:hk1_{hmac}) or a process business key (ESC-{tenant}-...), never a raw "
            "identifier (LGPD, T3.4/F4)."
        )
    if not _KEYED_PSEUDONYM_TOKEN.search(tid):
        raise ValueError(
            "thread_id must embed a KEYED pseudonym token (hk1_<hmac-sha256 hex>) — checkpoint "
            "tables are PHI-bearing and keyed by thread_id, so an UNKEYED sha256 hash (byte-for-"
            "byte identical in shape to a keyed HMAC, but reversible via a precomputed table over "
            "the ~6.7e9 BR-mobile keyspace) must never be accepted. Derive the conversation id via "
            "security.hash_phone routed through the keyed Pseudonymizer (ADR-0035 / PHI_HMAC_KEY): "
            "wa:{tenant}:hk1_{hmac} or ESC-{tenant}-... that wraps one (LGPD, T3.4/F4)."
        )
    return tid


def checkpoint_thread_config(thread_id: str, *, checkpoint_ns: str = "") -> RunnableConfig:
    """Build a LangGraph `RunnableConfig` scoping a checkpoint thread, PHI-safety-gated.

    `thread_id` MUST already be a PHI-safe identity (a `wa:{tenant}:{phone_hash}` conversation id
    or an `ESC-{tenant}-...` process business key). `checkpoint_ns` namespaces sub-graph state
    within the thread (upstream langgraph semantics), NOT tenant isolation — tenant is already
    embedded in the conventional thread id.
    """
    tid = assert_phi_safe_thread_id(thread_id)
    return {"configurable": {"thread_id": tid, "checkpoint_ns": checkpoint_ns}}


class Checkpointer:
    """Async-capable LangGraph checkpointer for durable agent-state persistence.

    Wraps a `BaseCheckpointSaver` (production: `AsyncPostgresSaver`; dev fallback:
    `InMemorySaver`). Construct one of three ways:

      - `await Checkpointer.connect_and_setup(dsn)` — opens an `AsyncPostgresSaver` pool and
        awaits `setup()` ONCE (idempotent). Use `async with`, or call `aclose()` to release the
        pool.
      - `Checkpointer(saver=<a BaseCheckpointSaver>)` — wrap an already-constructed saver.
      - `Checkpointer(conn_string=...)` — carry a DSN without opening a pool (metadata only).
    """

    def __init__(
        self,
        saver: BaseCheckpointSaver[Any] | None = None,
        conn_string: str | None = None,
    ) -> None:
        """Initialize the checkpointer.

        Args:
            saver: A pre-configured async-capable saver. Takes precedence.
            conn_string: PostgreSQL DSN carried for reference (used when saver is None).
        """
        self._saver = saver
        self._conn_string = conn_string
        # Set only by `connect_and_setup`; owns the AsyncPostgresSaver pool context manager.
        self._pool_cm: Any | None = None
        if saver is not None:
            logger.info("checkpointer_initialized_with_saver", saver_type=type(saver).__name__)
        elif conn_string is not None:
            logger.info("checkpointer_initialized_with_conn_string")
        else:
            logger.warning("checkpointer_initialized_without_backend")

    @classmethod
    async def connect_and_setup(cls, conn_string: str) -> Checkpointer:
        """Open an `AsyncPostgresSaver` pool on `conn_string` and await `setup()` once (idempotent).

        The awaited `setup()` (a coroutine on `AsyncPostgresSaver` — there is no `asetup()`)
        provisions the four upstream-owned tables and advances `checkpoint_migrations`;
        it is safe to call on every boot (it re-runs only migrations newer than the recorded row).
        On ANY failure the pool is released and the error propagates — the caller
        (`_provision_checkpointer`) turns that into a fail-closed readiness signal in production.

        DSN NORMALIZATION (prod-critical): the platform `DATABASE_URL` convention is a SQLAlchemy
        `postgresql+asyncpg://...` DSN (Helm's Aurora ExternalSecret, `alembic.ini`). psycopg —
        which `AsyncPostgresSaver` connects with — cannot parse the `+asyncpg` driver token and
        raises `ProgrammingError`, which in production would fail the checkpointer CLOSED on EVERY
        boot (readiness red / webhook refuses to serve). So strip it to the plain `postgresql://`
        form via the SAME `normalize_dsn` the ADR-0007 audit sink uses — guaranteeing one
        `DATABASE_URL` is interpreted identically by both the audit sink and the checkpointer in
        the same pod. A plain `postgresql://` DSN is passed through unchanged.
        """
        # Imported lazily so importing this module never hard-requires the postgres extra
        # (dev/test paths that only touch InMemorySaver / the wrapper stay import-light).
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from maezo.gateway.audit_postgres import normalize_dsn

        dsn = normalize_dsn(conn_string)
        pool_cm = AsyncPostgresSaver.from_conn_string(dsn)
        saver = await pool_cm.__aenter__()
        try:
            await saver.setup()  # idempotent DDL; checkpoint_migrations owns versioning
        except BaseException:
            await pool_cm.__aexit__(*sys.exc_info())
            raise
        instance = cls(saver=saver, conn_string=dsn)
        instance._pool_cm = pool_cm
        logger.info("checkpointer_postgres_setup_complete")
        return instance

    async def __aenter__(self) -> Checkpointer:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying connection pool, if this instance owns one."""
        if self._pool_cm is not None:
            await self._pool_cm.__aexit__(None, None, None)
            self._pool_cm = None
            logger.info("checkpointer_pool_closed")

    @property
    def saver(self) -> BaseCheckpointSaver[Any] | None:
        """The underlying saver — pass to `StateGraph.compile(checkpointer=...)`."""
        return self._saver

    @property
    def conn_string(self) -> str | None:
        """Return the configured PostgreSQL connection string, if any."""
        return self._conn_string

    def _ensure_saver(self) -> BaseCheckpointSaver[Any]:
        """Return the underlying saver, raising if not configured."""
        if self._saver is None:
            raise RuntimeError("Checkpointer not connected. Provide a saver instance or conn_string.")
        return self._saver

    async def save(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        """Persist a checkpoint state via the async saver.

        Args:
            config: LangGraph RunnableConfig with thread_id.
            checkpoint: The checkpoint state to persist.
            metadata: Additional metadata for the checkpoint.

        Returns:
            The updated config after save.
        """
        saver = self._ensure_saver()
        logger.debug("checkpoint_save", thread_id=config.get("configurable", {}).get("thread_id"))
        result: RunnableConfig = await saver.aput(config, checkpoint, metadata, {})
        return result

    async def load(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Load a checkpoint state via the async saver.

        Args:
            config: LangGraph RunnableConfig with thread_id.

        Returns:
            The CheckpointTuple or None if not found.
        """
        saver = self._ensure_saver()
        logger.debug("checkpoint_load", thread_id=config.get("configurable", {}).get("thread_id"))
        return await saver.aget_tuple(config)


@dataclass(frozen=True, slots=True)
class CheckpointerProvision:
    """Outcome of :func:`provision_checkpointer` — the ONE F2 fail-closed decision, mapped
    uniformly onto each caller's own surface: the agent-runtime daemon's `AgentState` readiness
    gate (`checkpointer_ready`) and the webhook dispatch path's refuse-to-serve (a `None`
    dispatcher → `/webhook` 501). `ready` is the fail-closed bit; `backend` names the live saver
    ("postgres"/"memory") for the readiness detail; `error` carries the reason on any
    non-durable outcome (kept even on a healthy in-memory fallback, so the reason it degraded is
    still observable)."""

    checkpointer: Checkpointer | None
    backend: str | None
    ready: bool
    error: str | None


async def provision_checkpointer(
    *,
    database_url: str | None,
    is_production: bool,
    component: str,
    setup_timeout_s: float | None = None,
) -> CheckpointerProvision:
    """Provision a durable LangGraph checkpointer with F2 fail-closed discipline (T3.4/F4).

    SINGLE SOURCE OF TRUTH for the checkpoint provisioning POLICY — shared verbatim by
    `runtime/agent_runtime/service.py::_provision_checkpointer` (the health-daemon readiness gate)
    and `platform/webhooks/service.py` (the live Helena webhook dispatch path). Both call this so
    the fail-closed semantics can never drift apart between "the pod that says it's ready" and
    "the pod that actually runs Helena's turns":

      - DSN present -> open an `AsyncPostgresSaver` and await `setup()` ONCE (idempotent DDL;
        `checkpoint_migrations` owns versioning — see module docstring).
          success -> ready=True, backend="postgres".
          failure -> PRODUCTION fails CLOSED (ready=False, no fallback — the caller must refuse to
                     advertise readiness / refuse to serve); non-production falls back to an
                     in-memory saver with a LOUD warning.
      - DSN absent -> PRODUCTION fails CLOSED (a prod component must not run stateless);
        non-production falls back to in-memory + warning.

    `setup_timeout_s` (optional): when set, the connect+`setup()` is bounded by
    `asyncio.wait_for` — a slow/hung Postgres then surfaces as a `TimeoutError`, caught and routed
    through the SAME fail-closed branch as any other setup failure (prod refuse / dev fallback).
    The webhook receiver passes this (its health server binds AFTER bring-up, so a hung connect
    must not block the bind); the agent-runtime daemon leaves it None (it binds `/healthz` BEFORE
    bring-up, so an unbounded connect there cannot stall liveness — #165 behavior, unchanged).

    Never raises for the provisioning decision itself: a connect/`setup()` failure (or timeout) is
    caught and captured into `CheckpointerProvision.error`. `component` is a caller label
    ("agent_runtime:helena", "webhook") threaded into the structured logs so the two call sites
    stay distinguishable in observability.
    """
    error: str
    if database_url:
        try:
            if setup_timeout_s is not None:
                checkpointer = await asyncio.wait_for(
                    Checkpointer.connect_and_setup(database_url), timeout=setup_timeout_s
                )
            else:
                checkpointer = await Checkpointer.connect_and_setup(database_url)
            return CheckpointerProvision(
                checkpointer=checkpointer, backend="postgres", ready=True, error=None
            )
        except Exception as exc:  # noqa: BLE001 — captured; fail-closed in prod, fallback in dev.
            error = f"AsyncPostgresSaver setup failed: {type(exc).__name__}: {exc}"
            logger.error(
                "checkpointer_postgres_setup_failed",
                component=component,
                is_production=is_production,
                exc_info=True,
            )
            if is_production:
                # FAIL-CLOSED: no silent degradation to in-memory in production.
                return CheckpointerProvision(checkpointer=None, backend=None, ready=False, error=error)
    else:
        error = "DATABASE_URL unset — no durable checkpoint backend available"
        if is_production:
            logger.error(
                "checkpointer_no_database_url_production",
                component=component,
                detail="component refuses to run with a non-durable (in-memory) checkpointer",
            )
            # FAIL-CLOSED: leave ready=False so the caller refuses (readiness red / 501).
            return CheckpointerProvision(checkpointer=None, backend=None, ready=False, error=error)

    # NON-production fallback ONLY (reached only when is_production is False): in-memory saver,
    # explicit warning. State does NOT survive a restart — dev/test ergonomics, never prod.
    from langgraph.checkpoint.memory import InMemorySaver

    logger.warning(
        "checkpointer_inmemory_fallback_dev",
        component=component,
        reason=error,
        detail=(
            "IN-MEMORY checkpointer: state will NOT survive a restart. Non-production fallback "
            "ONLY (is_production=False); production fails closed instead (T3.4/F4)."
        ),
    )
    return CheckpointerProvision(
        checkpointer=Checkpointer(saver=InMemorySaver()), backend="memory", ready=True, error=error
    )
