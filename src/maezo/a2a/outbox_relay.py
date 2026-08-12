"""The A2A fact-outbox RELAY — drains `a2a_fact_outbox` to a real broker, at-least-once.

Delivery half of the fix `outbox.py` describes: the write side makes every delegation fact DURABLE
instead of dropping it (`_NoopKafkaProducer`), and this is what later moves those rows onto
`agents.events.delegation.{requested,completed,rejected}`.

=================================================================================================
Explicitly invocable. Never auto-started. This is a property, not an omission.
=================================================================================================
Nothing imports this module. It is not wired into `agent_runtime/service.py`, not into
`worker_runtime/service.py`, not into any Helm template, and no composition root constructs it. It
runs when a human or a scheduler runs it:

    python -m maezo.a2a.outbox_relay --once          # drain what is there, then exit
    python -m maezo.a2a.outbox_relay                 # poll until SIGTERM/SIGINT

That is deliberate. Turning delegation facts into real broker traffic is a DEPLOYMENT decision:
there is no A2A fact consumer in this platform, `register_a2a_topics` still has no production call
site (`facts.py`'s own docstring), and the topics may not exist on the target cluster. Auto-starting
a publisher for topics nobody consumes, from a daemon whose readiness would then depend on a broker
it never needed, is how a build wave manufactures an outage out of an observability feature. The
outbox accumulates safely in the meantime — rows are `pending`, nothing is lost, and starting the
relay later delivers the backlog oldest-first.

=================================================================================================
At-least-once, and exactly where the duplicate comes from
=================================================================================================
`drain_once` is publish-THEN-mark, in that order, per row:

    claim batch (lease) -> publish to broker -> mark delivered

A crash, kill, or broker timeout anywhere between "published" and "marked" leaves the row
`claimed` with a lease that EXPIRES. The next claim sweep takes expired leases, so the row is
re-published. The consumer sees the same fact twice with the SAME `dedup_key`
(`{tenant}:a2a:delegate:{task_id}:{kind}` — derived from the payload, therefore identical across
every redelivery) and collapses it.

The reverse order (mark-then-publish) would be at-most-once: a crash after marking loses the fact
permanently, which is `_NoopKafkaProducer` again with a database bill. There is no third option
without broker-side transactions this platform does not have.

Ordering: `drain_once` stops the batch at the FIRST publish failure rather than skipping past it,
so facts are not reordered on the wire behind a failed one. Rows already published in that batch
are marked delivered; the failed row and everything after it are released to `pending` with
`last_error`, and the next batch retries from the same point.

=================================================================================================
Fail-closed composition
=================================================================================================
`build_relay` REFUSES to construct without `DATABASE_URL` (there is no outbox to drain) and without
`KAFKA_BOOTSTRAP_SERVERS` (there is nowhere to drain it to). Unlike the composition roots in
`runtime/agent_runtime/a2a_composition.py`, this refusal has NO local/production discriminator and
deliberately reads no `RUNTIME_MODE`: a relay with no database or no broker is useless in every
runtime mode, so a mode-dependent answer here would be a switch with one legal position. The
runtime-mode discriminators stay where the roots that need them live.

=================================================================================================
Honest external boundary
=================================================================================================
Same boundary `notifications_bridge.py` states for its own consumer: everything up to "bytes leave
for a real broker" is unit-proven here against an injected publisher; `AioKafkaFactPublisher`
actually connecting to a reachable `KAFKA_BOOTSTRAP_SERVERS` is NOT provable in this sandbox and is
not faked. No test double is defined in this module — the in-memory publisher the unit tests drive
lives in `tests/unit/a2a/`, so §8.4 of the effect-chokepoint fence needs no new exception for a
composition root that has none.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import socket
from typing import Any, Final, Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.a2a.outbox import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CLAIM_TTL_S,
    OutboxRecord,
    PostgresFactOutbox,
)

logger = structlog.get_logger(__name__)

#: Seconds between drain sweeps when the previous sweep found nothing. A short idle poll (the
#: outbox is a low-volume table — one to three rows per delegation) rather than LISTEN/NOTIFY,
#: which would add a second durable-connection failure mode for a latency nobody has asked for.
DEFAULT_POLL_INTERVAL_S: Final[float] = 2.0


class FactBrokerPublisher(Protocol):
    """The minimal broker seam `drain_once` needs — the `KafkaLike` shape plus a lifecycle.

    Same `send(topic, value, *, key)` signature as `maezo.a2a.dispatcher.KafkaLike`, so the object
    the relay publishes THROUGH is interchangeable with the object the dispatcher would have
    published to had a real producer ever existed.
    """

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None: ...


class AioKafkaFactPublisher:
    """Real `aiokafka`-backed publisher. THE honest external boundary (module docstring).

    `send_and_wait` (never fire-and-forget `send`): the relay may only mark a row delivered once
    the broker has ACKed it, and an un-awaited send would let `drain_once` mark rows delivered for
    bytes still sitting in a client-side buffer — silently turning at-least-once into
    at-most-once, which is the exact defect this whole leg removes.

    The `aiokafka` import is lazy (inside `start`), mirroring
    `events_kafka_producer.AioKafkaEventsProducer`: constructing the relay must not require the
    optional broker client to be importable, so `--help` and unit tests never touch it.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        connect_timeout_s: float = 10.0,
        send_timeout_s: float = 10.0,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._connect_timeout_s = connect_timeout_s
        self._send_timeout_s = send_timeout_s
        self._producer: Any | None = None

    async def start(self) -> None:
        from aiokafka import (  # type: ignore[import-untyped]  # noqa: PLC0415 - lazy: no import-time network dep
            AIOKafkaProducer,
        )

        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            request_timeout_ms=int(self._send_timeout_s * 1000),
        )
        await asyncio.wait_for(producer.start(), timeout=self._connect_timeout_s)
        self._producer = producer

    async def stop(self) -> None:
        """Best-effort shutdown — never raises (mirrors `AioKafkaEventsProducer.close`)."""
        if self._producer is not None:
            with contextlib.suppress(Exception):
                await self._producer.stop()
            self._producer = None

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        if self._producer is None:
            raise RuntimeError("AioKafkaFactPublisher.send before start() — refusing to drop a fact")
        await asyncio.wait_for(self._producer.send_and_wait(topic, value, key), timeout=self._send_timeout_s)


class DrainReport:
    """What ONE `drain_once` sweep did. Counts only — never a payload, never a dedup key list."""

    __slots__ = ("claimed", "delivered", "released", "sealed", "publish_error")

    def __init__(
        self,
        *,
        claimed: int = 0,
        delivered: int = 0,
        sealed: int = 0,
        released: int = 0,
        publish_error: str | None = None,
    ) -> None:
        #: rows leased by this sweep.
        self.claimed = claimed
        #: rows this sweep successfully PUBLISHED to the broker.
        self.delivered = delivered
        #: rows this sweep published AND sealed as delivered. `sealed < delivered` means a lease
        #: was lost to another relay between publish and mark — see `mark_delivered`.
        self.sealed = sealed
        #: rows released back to `pending` because the batch stopped at a publish failure.
        self.released = released
        #: the publish failure that stopped the batch, if any.
        self.publish_error = publish_error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DrainReport(claimed={self.claimed}, delivered={self.delivered}, "
            f"sealed={self.sealed}, released={self.released}, publish_error={self.publish_error!r})"
        )


def default_worker_id() -> str:
    """Lease owner id: `{hostname}:{pid}`.

    Identifies WHICH relay holds a lease, so `mark_delivered`'s `claimed_by` guard can tell "my
    lease" from "someone re-claimed this". Host+pid, never a random uuid, so an operator reading
    `claimed_by` on a stuck row can find the process.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


async def drain_once(
    outbox: PostgresFactOutbox,
    publisher: FactBrokerPublisher,
    *,
    claimed_by: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
) -> DrainReport:
    """Claim -> publish -> mark, once. Returns what happened.

    Publish-then-mark per row (module docstring): a crash between the two leaves the row `claimed`
    with an expiring lease, so the next sweep republishes it. That duplicate is the at-least-once
    guarantee working, not a bug — `dedup_key` is stable across it.

    Stops at the FIRST publish failure so the wire order is not shuffled behind a failed row: what
    was already published is sealed, the failure and everything behind it goes back to `pending`
    carrying `last_error`.
    """
    records: list[OutboxRecord] = await outbox.claim_batch(
        claimed_by=claimed_by, batch_size=batch_size, claim_ttl_s=claim_ttl_s
    )
    if not records:
        return DrainReport()

    published: list[int] = []
    failure: str | None = None
    for index, record in enumerate(records):
        try:
            await publisher.send(
                record.topic,
                record.payload,
                key=None if record.partition_key is None else record.partition_key.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 - the whole point: a broker failure must not lose the row
            failure = f"{type(exc).__name__}: {exc}"
            logger.error(
                "a2a_outbox_relay_publish_failed",
                topic=record.topic,
                dedup_key=record.dedup_key,
                attempts=record.attempts,
                row_id=record.id,
                error=failure,
            )
            remaining = [r.id for r in records[index:]]
            sealed = await outbox.mark_delivered(published, claimed_by=claimed_by)
            released = await outbox.mark_failed(remaining, claimed_by=claimed_by, error=failure)
            return DrainReport(
                claimed=len(records),
                delivered=len(published),
                sealed=sealed,
                released=released,
                publish_error=failure,
            )
        published.append(record.id)

    # CRASH WINDOW, by construction: everything above is on the wire and nothing below has run.
    # Dying here costs a redelivery, never a fact.
    sealed = await outbox.mark_delivered(published, claimed_by=claimed_by)
    logger.info(
        "a2a_outbox_relay_drained",
        claimed=len(records),
        delivered=len(published),
        sealed=sealed,
    )
    return DrainReport(claimed=len(records), delivered=len(published), sealed=sealed)


async def run_relay_loop(
    outbox: PostgresFactOutbox,
    publisher: FactBrokerPublisher,
    *,
    claimed_by: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    stop_event: asyncio.Event | None = None,
    max_sweeps: int | None = None,
) -> list[DrainReport]:
    """Drain repeatedly until `stop_event` is set (or `max_sweeps` sweeps have run).

    Sleeps `poll_interval_s` only when a sweep found NOTHING — a full batch means there is more
    backlog, so the next sweep starts immediately. A publish failure also sleeps, so a down broker
    is retried at the poll interval rather than spun on.

    `max_sweeps` exists for tests and for `--once`; `None` (production) loops forever. The loop
    never swallows a non-publish error: a database failure propagates and ends the process, the
    same fail-closed posture `notifications_bridge.run_consumer_loop` takes.
    """
    reports: list[DrainReport] = []
    sweeps = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        report = await drain_once(
            outbox,
            publisher,
            claimed_by=claimed_by,
            batch_size=batch_size,
            claim_ttl_s=claim_ttl_s,
        )
        reports.append(report)
        sweeps += 1
        if max_sweeps is not None and sweeps >= max_sweeps:
            break
        idle = report.claimed == 0 or report.publish_error is not None
        if idle:
            if stop_event is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            else:
                await asyncio.sleep(poll_interval_s)
    return reports


class OutboxRelaySettings(BaseSettings):
    """Env-driven config (mirrors `NotificationsBridgeSettings`' Field/alias/BaseSettings shape)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    # Both FAIL-CLOSED in `build_relay` — no outbox to drain / nowhere to drain it to.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str | None = Field(default=None, alias="KAFKA_BOOTSTRAP_SERVERS")

    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, alias="A2A_OUTBOX_RELAY_BATCH_SIZE")
    claim_ttl_s: float = Field(default=DEFAULT_CLAIM_TTL_S, alias="A2A_OUTBOX_RELAY_CLAIM_TTL_S")
    poll_interval_s: float = Field(default=DEFAULT_POLL_INTERVAL_S, alias="A2A_OUTBOX_RELAY_POLL_INTERVAL_S")
    connect_timeout_s: float = Field(default=10.0, alias="A2A_OUTBOX_RELAY_CONNECT_TIMEOUT_S")
    send_timeout_s: float = Field(default=10.0, alias="A2A_OUTBOX_RELAY_SEND_TIMEOUT_S")


def build_relay(settings: OutboxRelaySettings) -> tuple[PostgresFactOutbox, AioKafkaFactPublisher]:
    """Composition root. Fail-closed on either missing dependency (module docstring)."""
    if not settings.database_url:
        raise RuntimeError(
            "a2a outbox relay: DATABASE_URL is required — there is no outbox to drain without it. "
            "Refusing to start (the facts stay durable in whatever database the composition roots "
            "wrote them to; a relay pointed at nothing would report healthy while delivering "
            "nothing)."
        )
    if not settings.kafka_bootstrap_servers:
        raise RuntimeError(
            "a2a outbox relay: KAFKA_BOOTSTRAP_SERVERS is required — there is nowhere to deliver "
            "to. Refusing to start rather than claiming rows and dropping them, which is exactly "
            "the `_NoopKafkaProducer` defect this outbox exists to remove."
        )
    outbox = PostgresFactOutbox(dsn=settings.database_url, tenant=settings.tenant_id)
    publisher = AioKafkaFactPublisher(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        connect_timeout_s=settings.connect_timeout_s,
        send_timeout_s=settings.send_timeout_s,
    )
    return outbox, publisher


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maezo.a2a.outbox_relay",
        description=(
            "Drain the A2A delegation-fact outbox (a2a_fact_outbox) to Kafka, at-least-once. "
            "Explicitly invoked — never auto-started by any daemon."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single drain sweep and exit (default: poll until SIGTERM/SIGINT)",
    )
    return parser


async def main(argv: list[str] | None = None) -> None:
    """Runnable entry point: `python -m maezo.a2a.outbox_relay [--once]`."""
    args = build_arg_parser().parse_args(argv)
    settings = OutboxRelaySettings()
    outbox, publisher = build_relay(settings)
    claimed_by = default_worker_id()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - e.g. Windows
            loop.add_signal_handler(sig, stop_event.set)

    await publisher.start()
    logger.info(
        "a2a_outbox_relay_started",
        tenant_id=settings.tenant_id,
        claimed_by=claimed_by,
        batch_size=settings.batch_size,
        once=args.once,
    )
    try:
        await run_relay_loop(
            outbox,
            publisher,
            claimed_by=claimed_by,
            batch_size=settings.batch_size,
            claim_ttl_s=settings.claim_ttl_s,
            poll_interval_s=settings.poll_interval_s,
            stop_event=stop_event,
            max_sweeps=1 if args.once else None,
        )
    finally:
        await publisher.stop()
        await outbox.aclose()
        logger.info("a2a_outbox_relay_stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
