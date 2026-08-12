"""Kafka consumer entry point for `NotificationBridge` (T2.6-EB3 part 5 — "make it live" wiring).

`deploy/helm/maezo-tenant/templates/deployment-bridge.yaml:50` has ALWAYS referenced
`command: ["python", "-m", "maezo.platform.integrations.notifications_bridge"]` — but the module
never existed (`ModuleNotFoundError`). The Helm chart's own comment already documents the intended
shape: consume the ONE Kafka topic `operadora.notifications.internal`, and on the `type`-tagged
messages a registered rule matches (e.g. `contas.start_recurso` / `nip.handoff_ans_submit` /
`ans.cron_due`), start the target CIB Seven process via `mcp-cibseven.start_process`
(idempotent) — i.e. dispatch every message through `NotificationBridge.on_event`, constructed
with the FENCED starter (`build_cibseven_process_starter`, ADR-0007/T-C2 chokepoint — see
`notification_bridge.py`'s own module docstring; NEVER a raw/un-audited engine call).

This module provides:
  - `NotificationsBridgeSettings` — env-driven config (mirrors
    `runtime.worker_runtime.settings.WorkerRuntimeSettings`'s Field/alias/BaseSettings shape;
    Helm already injects `TENANT_ID`/`CIBSEVEN_BASE_URL`/`KAFKA_BOOTSTRAP_SERVERS`).
  - `BridgeKafkaConsumer` (Protocol) + `AioKafkaBridgeConsumer` (real, `aiokafka`-backed) +
    `FakeBridgeKafkaConsumer` (in-memory, tests only).
  - `handle_bridge_message` — the AGENT-CONTROLLABLE, fully unit-tested dispatch handler: one
    Kafka message in, `NotificationBridge.on_event(...)` out. Fail-closed on a malformed message
    (`MalformedBridgeMessageError`) — never silently drops or misclassifies a bad message as "no
    rule matched".
  - `run_consumer_loop` — drives the handler over ANY `BridgeKafkaConsumer` (real or fake); fully
    exercised in unit tests against the fake.
  - `main()` — composition root: builds the real transport/audit-sink/consumer and runs the loop
    until SIGTERM/SIGINT.

HONEST EXTERNAL-VALIDATION BOUNDARY (the same "honest-seam" pattern T2.6-7 used for the XSD/
gateway boundary — see `docs/design/T2.6-ans-submission-rescope.md` §2.A/§2.B): this repo's
sandbox has NEITHER a reachable Kafka broker NOR a running CIB Seven engine. Everything up to
"bytes arrive over the wire from a real broker" is unit-proven here (the handler, the loop's
dispatch/commit/fail-closed behavior, the fenced-start wiring against a fake/PG-backed audit
sink). What is NOT, and CANNOT be, proven in this environment:
  1. `AioKafkaBridgeConsumer` actually connecting to and consuming from a REAL Kafka broker
     (`aiokafka.AIOKafkaConsumer.start()`/iteration against `KAFKA_BOOTSTRAP_SERVERS`).
  2. The Helm deployment itself (`deployment-bridge.yaml`) actually scheduling and running this
     module inside a real cluster, with a real `notifications-bridge` service-account/network
     policy, against the real CIB Seven + Kafka the chart wires it to.
`tests/integration/platform/test_notifications_bridge_live_kafka.py` marks this boundary
explicitly: it probes for a reachable broker and SKIPS LOUDLY (never silently, never faking a
broker) when none is present — mirroring `tests/integration/conftest.py`'s `engine_base_url`/
`audit_pg` "COULD NOT VERIFY" skip convention for the CIB Seven engine / Postgres dependencies.

Residual gap NOT closed by this module (documented, not fabricated): a single malformed message
or a single genuine handoff failure (`NotificationBridgeHandoffFailedError` — EB-3 part 1) makes
`run_consumer_loop` re-raise and `main()` exit — no dead-letter queue / poison-message shunt
exists yet. That is the deliberate fail-closed choice (never silently drop a message this bridge
could not process), but it means a single bad message on this shared topic currently blocks
every OTHER message behind it until an operator intervenes — flagged here for the R1 verifier,
not silently absorbed into a "looks-done" DLQ nobody built.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.gateway.seams.cibseven import GatedCibSevenTransport
from maezo.gateway.tool_registry import (
    BRIDGE_PRINCIPAL,
    build_cibseven_seam,
    build_worker_seam_context,
    effect_seams_gated,
)
from maezo.platform.notification_bridge import (
    HandoffResult,
    NotificationBridge,
    build_cibseven_process_starter,
)

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]  # pragma: no cover

logger = structlog.get_logger(__name__)

#: The bridge's single input topic — the `type`-discriminated internal-notification channel
#: every publisher (`recurso.py`/`lgpd.py`'s own `_NOTIFICATIONS_TOPIC`, `ans_cron.py`'s
#: docstring) already targets. Overridable via env for test/dev isolation (never hardcoded past
#: a default, same convention as every other Helm-injected setting below).
NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

DEFAULT_CONSUMER_GROUP_ID = "notifications-bridge"


# ---------------------------------------------------------------------------
# Settings (env-driven — Helm injects; mirrors WorkerRuntimeSettings' Field/alias shape)
# ---------------------------------------------------------------------------


class NotificationsBridgeSettings(BaseSettings):
    """Config for the notifications-bridge daemon — one process drives the consume loop."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- Identity (Helm injects, deployment-bridge.yaml) --------------------------------------
    tenant_id: str = Field(default="amh", alias="TENANT_ID")

    # --- External dependencies ------------------------------------------------------------------
    cibseven_base_url: str = Field(default="http://cibseven:8080/engine-rest", alias="CIBSEVEN_BASE_URL")
    cibseven_auth_token: str | None = Field(default=None, alias="CIBSEVEN_AUTH_TOKEN")
    # FAIL-CLOSED (ADR-0007/T-C2, mirrors WorkerRuntimeSettings.database_url's own docstring):
    # absent means `main()` REFUSES to start rather than construct a fake/no-op audit sink —
    # this daemon starts CIB Seven processes and must never do so un-audited.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_topic: str = Field(default=NOTIFICATIONS_TOPIC, alias="NOTIFICATIONS_BRIDGE_KAFKA_TOPIC")
    kafka_group_id: str = Field(
        default=DEFAULT_CONSUMER_GROUP_ID, alias="NOTIFICATIONS_BRIDGE_KAFKA_GROUP_ID"
    )
    # httpx client timeout for the CIB Seven transport (WorkerRuntimeSettings' own default).
    client_timeout_s: float = Field(default=40.0, alias="WORKER_CLIENT_TIMEOUT_S")


# ---------------------------------------------------------------------------
# Fail-closed malformed-message handling
# ---------------------------------------------------------------------------


class MalformedBridgeMessageError(ValueError):
    """Raised when a Kafka message on the bridge's input topic cannot even be classified.

    Fail-closed (mirrors `NotificationBridgeHandoffFailedError`'s rationale): a message that is
    not a JSON object, or lacks a usable `type` field, is NOT the same thing as a well-formed
    message whose `type` simply matches no registered rule (`NotificationBridge.on_event` already
    handles that legitimately — `evaluated=True, handoff_triggered=False`, no exception). Silently
    treating a malformed message as "no rule matched" would misclassify a genuinely broken
    producer/payload as a benign no-op. This raises instead, so `run_consumer_loop` never commits
    the offset for a message it could not even parse enough to dispatch.
    """

    def __init__(self, reason: str, raw: Any) -> None:
        self.reason = reason
        self.raw = raw
        super().__init__(f"notifications_bridge: malformed message ({reason}): {raw!r}")


async def handle_bridge_message(bridge: NotificationBridge, message: Any) -> list[HandoffResult]:
    """The bridge's Kafka message HANDLER — the one piece this module fully unit-proves.

    message -> fenced start: extracts the `type`-discriminator, then dispatches through
    `NotificationBridge.on_event(event_type, payload)` — the SAME full evaluate+execute pipeline
    `tests/unit/platform/test_notification_bridge.py` already proves against a fenced starter.

    Fail-closed on a bad message: raises `MalformedBridgeMessageError` when `message` is not a
    JSON object, or when it lacks a non-blank `type` string — never silently no-ops a message the
    bridge cannot even classify.

    A genuine handoff failure for a WELL-FORMED, rule-matching message propagates
    `NotificationBridgeHandoffFailedError` unchanged (EB-3 part 1) — this function adds no
    additional try/except around `on_event`, so the fail-closed behavior lives in exactly one
    place (`notification_bridge.py`), not duplicated here.
    """
    if not isinstance(message, Mapping):
        raise MalformedBridgeMessageError("not a JSON object", message)
    event_type = message.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise MalformedBridgeMessageError("missing/blank 'type' field", message)
    return await bridge.on_event(event_type, dict(message))


# ---------------------------------------------------------------------------
# Consumer abstraction — real (aiokafka) + fake (tests), mirrors the repo's transport-triple
# pattern (dmn_transport.py / mcp_cibseven/transport.py): a Protocol + one real + one fake.
# ---------------------------------------------------------------------------


class BridgeKafkaConsumer(Protocol):
    """The minimal async-iterable Kafka consumer seam `run_consumer_loop` needs."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def commit(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[Any]: ...


class AioKafkaBridgeConsumer:
    """Real `aiokafka`-backed consumer against `NOTIFICATIONS_TOPIC`.

    THE HONEST EXTERNAL BOUNDARY (module docstring): `start()`/iteration require a REACHABLE
    Kafka broker at `bootstrap_servers`, which this sandbox does not have. Every other class in
    this module is exercised by a fast, broker-free unit test; this one is exercised ONLY by
    `tests/integration/platform/test_notifications_bridge_live_kafka.py`, which skips loudly
    when no broker is reachable — never faked here.

    `enable_auto_commit=False`: `run_consumer_loop` commits explicitly, ONLY after a message
    dispatches without raising — at-least-once delivery with no offset advance on a fail-closed
    message (malformed OR a genuine handoff failure), so a crash/restart re-delivers it rather
    than silently skipping it.
    """

    def __init__(self, *, bootstrap_servers: str, topic: str, group_id: str) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer: AIOKafkaConsumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
            value_deserializer=_deserialize_json_value,
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def commit(self) -> None:
        await self._consumer.commit()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter_values()

    async def _iter_values(self) -> AsyncIterator[Any]:
        async for record in self._consumer:
            yield record.value


def _deserialize_json_value(raw: bytes) -> Any:
    """`aiokafka`'s `value_deserializer` hook — raw message bytes -> parsed JSON.

    A decode/parse failure becomes `MalformedBridgeMessageError` (fail-closed) rather than
    propagating a raw `UnicodeDecodeError`/`JSONDecodeError` — `handle_bridge_message` and
    `run_consumer_loop` only need to know about ONE malformed-message exception type.
    """
    import json

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedBridgeMessageError(f"invalid JSON payload: {exc}", raw) from exc


class FakeBridgeKafkaConsumer:
    """In-memory `BridgeKafkaConsumer` double for unit tests. NEVER imported by production code.

    Iterates a fixed, injected list of already-deserialized messages exactly once (mirrors
    `FakeKafkaPublisher`/`FakeCibSevenTransport`'s "NEVER imported by production code" discipline
    elsewhere in this repo).
    """

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)
        self.started = False
        self.stopped = False
        self.commits = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def commit(self) -> None:
        self.commits += 1

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter_values()

    async def _iter_values(self) -> AsyncIterator[Any]:
        for message in self._messages:
            yield message


# ---------------------------------------------------------------------------
# The consume loop — fully unit-tested against FakeBridgeKafkaConsumer (no broker needed); the
# SAME loop drives AioKafkaBridgeConsumer in production (main(), below).
# ---------------------------------------------------------------------------


async def run_consumer_loop(
    consumer: BridgeKafkaConsumer,
    bridge: NotificationBridge,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Drive `handle_bridge_message` over every message `consumer` yields.

    Commits ONLY after a message dispatches without raising. Fail-closed: a
    `MalformedBridgeMessageError` or a `NotificationBridgeHandoffFailedError` (EB-3 part 1)
    PROPAGATES out of this loop (no swallowing try/except) — the caller (`main()`) treats either
    as a fatal daemon error, never a silently-skipped message. See the module docstring's
    "Residual gap" note: there is no dead-letter/poison-message shunt yet, a deliberate,
    documented fail-closed tradeoff, not an oversight.

    `stop_event`, when provided and set between messages, ends the loop gracefully (used by
    `main()`'s SIGTERM/SIGINT handling) — irrelevant to `FakeBridgeKafkaConsumer`-backed unit
    tests, which simply exhaust their fixed message list.
    """
    async for message in consumer:
        results = await handle_bridge_message(bridge, message)
        logger.info(
            "notifications_bridge.dispatched",
            handoffs=len(results),
            triggered=sum(1 for r in results if r.handoff_triggered),
        )
        await consumer.commit()
        if stop_event is not None and stop_event.is_set():
            break


# ---------------------------------------------------------------------------
# Composition root
# ---------------------------------------------------------------------------


def build_bridge(
    settings: NotificationsBridgeSettings,
) -> tuple[NotificationBridge, GatedCibSevenTransport]:
    """Construct the `NotificationBridge` wired to the FENCED starter — the only sanctioned
    production path (`build_cibseven_process_starter`'s own docstring). Never a raw/un-audited
    starter reaches production from this composition root.

    FAIL-CLOSED: raises if `settings.database_url` is unset — this daemon starts CIB Seven
    processes and must never do so without a durable ADR-0007 audit sink (never a fake/no-op
    sink standing in for a real one in production).
    """
    if not settings.database_url:
        raise RuntimeError(
            "notifications_bridge: DATABASE_URL is required — refusing to start without a "
            "durable audit sink (ADR-0007/T-C2 fail-closed; a fake/no-op sink is never "
            "constructed in production)."
        )
    audit_sink = PostgresAuditSink(settings.database_url, settings.tenant_id)
    # ONDA 1 §5.5, root (d). The engine transport comes from the ONE sanctioned constructor and is
    # GATED. Re-derived while wiring this: `platform/notification_bridge.py:1034`
    # (`build_cibseven_process_starter`) does NOT construct a transport — it RECEIVES one — so this
    # function is the bridge's only construction site and there is no second root to wire.
    #
    # Principal is `notifications_bridge`, not an agent id: this daemon has no `agent.yaml`, so it
    # has no declared-capability record and every call is an honest `CAPACIDADE_INDISPONIVEL`
    # would-deny at L-1 (see `build_worker_seam_context`). Inventing a capability list to tidy the
    # telemetry would be inventing a governance record.
    seam = build_worker_seam_context(tenant=settings.tenant_id, principal=BRIDGE_PRINCIPAL)
    transport = build_cibseven_seam(
        seam=seam,
        base_url=settings.cibseven_base_url,
        auth_token=settings.cibseven_auth_token,
        timeout=settings.client_timeout_s,
    )
    gated, detail = effect_seams_gated({"cibseven": transport})
    if not gated:
        # §5.5's boot assertion, in this root's own fail-closed idiom (the DATABASE_URL check
        # above is the precedent): refuse to construct rather than run a bridge that starts
        # regulatory processes through an un-gated engine seam.
        raise RuntimeError(f"notifications_bridge: effect seams not gated — refusing to start: {detail}")
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)
    return bridge, transport


async def main() -> None:
    """Composition root + runnable entry point (`python -m maezo.platform.integrations.
    notifications_bridge`) — what `deployment-bridge.yaml:50` has referenced all along.

    Builds the real transport/audit-sink/bridge (`build_bridge`) and the real
    `AioKafkaBridgeConsumer`, then drives `run_consumer_loop` until SIGTERM/SIGINT. See the
    module docstring for the honest boundary this function's Kafka/engine calls cannot be
    exercised against in this sandbox.
    """
    settings = NotificationsBridgeSettings()
    bridge, transport = build_bridge(settings)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - e.g. Windows ProactorEventLoop
            loop.add_signal_handler(sig, stop_event.set)

    await consumer.start()
    logger.info(
        "notifications_bridge.started",
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
        tenant_id=settings.tenant_id,
    )
    try:
        await run_consumer_loop(consumer, bridge, stop_event=stop_event)
    finally:
        await consumer.stop()
        await transport.close()
        logger.info("notifications_bridge.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
