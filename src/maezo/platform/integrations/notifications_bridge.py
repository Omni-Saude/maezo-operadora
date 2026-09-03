"""Kafka consumer entry point for `NotificationBridge` (T2.6-EB3 part 5 — "make it live" wiring).

`deploy/helm/maezo-tenant/templates/deployment-bridge.yaml:50` has ALWAYS referenced
`command: ["python", "-m", "maezo.platform.integrations.notifications_bridge"]` — but the module
never existed (`ModuleNotFoundError`). The Helm chart's own comment already documents the intended
shape: consume the ONE Kafka topic `operadora.notifications.internal`, and on the `type`-tagged
messages a registered rule matches (e.g. `contas.start_fraude` / `nip.handoff_ans_submit` /
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

POISON-MESSAGE SHUNT (GAP-SC-04-a, audit D5 / gateway gvr-d05 — this CLOSES the residual gap this
docstring used to only disclose). The gap was: a single malformed message made `run_consumer_loop`
re-raise and `main()` exit; on restart the same message is re-delivered (the offset was never
committed) and the daemon dies again — head-of-line blocking of every other message on this shared
topic until an operator intervenes. `run_consumer_loop` now accepts a `BridgeDlqShunt`, and on a
`MalformedBridgeMessageError` it moves the RAW bytes to `<topic>.dlq` and continues.

The shunt does NOT weaken the fail-closed posture; it narrows it to the one error class where
"retry forever" is provably useless:

  - It fires ONLY on `MalformedBridgeMessageError` — DETERMINISTIC malformation (not a JSON object,
    no usable `type`, undecodable bytes). Re-delivering those produces the identical failure
    forever; there is no state of the world in which attempt N+1 parses.
  - Every OTHER error still propagates and still blocks the offset: a genuine handoff failure
    (`NotificationBridgeHandoffFailedError`, EB-3 part 1), an `AuditPersistenceError` from the
    fenced starter (the fault class `tests/integration/chaos/test_a3_bridge_fail_closed.py` pins),
    a broker/engine timeout. Those are TRANSIENT infra faults where retry is exactly right, and
    the A3 certification's guarantee — the bridge never starts a process it could not audit — is
    untouched, because the shunt sits in a `except MalformedBridgeMessageError` arm that an
    `AuditPersistenceError` never enters.
  - A malformed message is never DROPPED. It is published to the DLQ, the publish is CONFIRMED,
    and a durable ADR-0007 audit fact is emitted — all three BEFORE the consumer offset is
    committed. If any of the three fails, the original fail-closed re-raise happens and the offset
    stays where it was.
  - With no shunt wired (`dlq=None`, the default — every pre-existing unit test), the behaviour is
    byte-identical to before: re-raise, no commit.

SCALE-OUT (what changes for >1 replica, and what does not). Since GAP-SC-04-a every publish carries
a deterministic per-entity partition key (`platform/integrations/partition_key.py`), so all events
about one beneficiary/process land on one partition, and Kafka's per-partition FIFO plus
consumer-group semantics (one partition is assigned to exactly one consumer in a group at a time)
give per-entity ordering for ANY replica count sharing `NOTIFICATIONS_BRIDGE_KAFKA_GROUP_ID`. The
DLQ removes the second scale-out blocker: one poison message no longer stalls a partition for
every entity behind it. What is NOT decided here and stays owner-gated: the REPLICA COUNT itself
and the chart values that carry it (`deploy/`, slice SC-04-b — see `docs/review-queue.md`). Two
operational facts an operator must know before flipping it: (a) parallelism is capped by the
topic's partition count (registry default 3, `topic_registry.py:75`) — replicas beyond that idle;
(b) `enable_auto_commit=False` plus the commit-after-dispatch order below keeps at-least-once
delivery per replica, and the downstream `start_process_idempotent` fence is what makes a
re-delivery converge instead of double-starting.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import signal
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.audit import AuditRecord
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
from maezo.platform.topic_registry import dlq_topic_for
from maezo.tools.mcp_cibseven.transport import DedupReportingAuditSink
from maezo.tools.workers.phi_vars import redact_error_message

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


#: CLOSED vocabulary of dead-letter reason codes (GAP-SC-04-a). These are the ONLY values that
#: ever reach the `reason` label of `maezo_bridge_dlq_total` or the `reason_code` field of a DLQ
#: audit record. Bounded on purpose: the human-readable `reason` string can embed a JSON parser's
#: own message (unbounded, and derived from bytes nobody validated), which is fine for an exception
#: message and a DLQ header but is exactly wrong for a metric label.
REASON_INVALID_JSON: Final[str] = "invalid_json"
REASON_NOT_A_JSON_OBJECT: Final[str] = "not_a_json_object"
REASON_MISSING_TYPE: Final[str] = "missing_type"

BRIDGE_DLQ_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_INVALID_JSON, REASON_NOT_A_JSON_OBJECT, REASON_MISSING_TYPE}
)


class MalformedBridgeMessageError(ValueError):
    """Raised when a Kafka message on the bridge's input topic cannot even be classified.

    Fail-closed (mirrors `NotificationBridgeHandoffFailedError`'s rationale): a message that is
    not a JSON object, or lacks a usable `type` field, is NOT the same thing as a well-formed
    message whose `type` simply matches no registered rule (`NotificationBridge.on_event` already
    handles that legitimately — `evaluated=True, handoff_triggered=False`, no exception). Silently
    treating a malformed message as "no rule matched" would misclassify a genuinely broken
    producer/payload as a benign no-op. This raises instead, so `run_consumer_loop` never commits
    the offset for a message it could not even parse enough to dispatch.

    THIS ERROR CLASS IS THE DLQ'S TRIGGER, AND ITS SCOPE IS THE WHOLE ARGUMENT (GAP-SC-04-a). It
    means DETERMINISTIC malformation: re-delivering the same bytes produces the identical failure
    forever, so "block the offset and retry" is a guarantee of a stalled partition, not of
    eventual success. Every TRANSIENT fault (handoff failure, `AuditPersistenceError`, broker or
    engine timeout) is a different exception type, is not caught by the shunt's `except` arm, and
    keeps the original block-the-offset behaviour — which is the correct posture for a fault that
    a retry can actually clear.

    `code` is the bounded `BRIDGE_DLQ_REASONS` token (metric label / audit field); `reason` is the
    human-readable detail (DLQ header, log line, exception message). Keeping them separate is what
    lets the detail stay informative without putting unbounded, unvalidated text on a metric.
    """

    def __init__(self, reason: str, raw: Any, *, code: str = REASON_NOT_A_JSON_OBJECT) -> None:
        self.reason = reason
        self.raw = raw
        self.code = code
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
        raise MalformedBridgeMessageError("not a JSON object", message, code=REASON_NOT_A_JSON_OBJECT)
    event_type = message.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise MalformedBridgeMessageError("missing/blank 'type' field", message, code=REASON_MISSING_TYPE)
    return await bridge.on_event(event_type, dict(message))


# ---------------------------------------------------------------------------
# Consumer abstraction — real (aiokafka) + fake (tests), mirrors the repo's transport-triple
# pattern (dmn_transport.py / mcp_cibseven/transport.py): a Protocol + one real + one fake.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeMessage:
    """One consumed record, carrying everything a dead-letter shunt needs (GAP-SC-04-a).

    THE ROOT-CAUSE REASON THIS TYPE EXISTS. The loop used to iterate already-DESERIALIZED values
    (`AioKafkaBridgeConsumer` passed `_deserialize_json_value` as aiokafka's `value_deserializer`,
    and `_iter_values` yielded `record.value`). Two consequences made a DLQ impossible:

      1. The RAW BYTES were gone by the time anything could shunt them. A DLQ that re-serializes
         the parsed value cannot exist for the one case that needs it most — bytes that never
         parsed — and for the cases that did parse it would quarantine a re-encoding rather than
         what the producer actually sent, which is not evidence.
      2. A parse failure raised INSIDE aiokafka's deserializer, i.e. during iteration, so it never
         reached a `try` the loop could own — `async for` itself blew up.

    So the consumer now takes the raw record and does its own parsing, recording a failure as
    `parse_error`/`parse_code` instead of raising mid-iteration. `topic`/`partition`/`offset`
    identify the record for the audit dedup key; `headers` and `key` are carried through to the
    DLQ so a quarantined record keeps the producer's own partitioning and metadata.

    `value` is `None` when `parse_error` is set — the two are mutually exclusive by construction,
    and the loop checks `parse_error` FIRST so a parse failure can never be mistaken for a message
    whose value legitimately parsed to JSON `null`.
    """

    topic: str
    partition: int
    offset: int
    raw: bytes
    key: bytes | None = None
    headers: tuple[tuple[str, bytes], ...] = ()
    value: Any = None
    parse_error: str = ""
    parse_code: str = ""

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        topic: str = NOTIFICATIONS_TOPIC,
        partition: int = 0,
        offset: int = 0,
        key: bytes | None = None,
        headers: tuple[tuple[str, bytes], ...] = (),
    ) -> BridgeMessage:
        """Build a message from an ALREADY-PARSED value, encoding its own `raw` bytes.

        Used by `FakeBridgeKafkaConsumer` and by tests: a double that was handed a Python object
        legitimately owns the bytes it claims to have delivered. Production never calls this — the
        real consumer starts from bytes and parses them (`from_record`), which is the only order
        in which `raw` is evidence rather than a re-encoding.

        Raises `TypeError` for a value that is not JSON-serializable, rather than inventing a
        `repr()`-shaped `raw` that would not round-trip.
        """
        import json

        return cls(
            topic=topic,
            partition=partition,
            offset=offset,
            raw=json.dumps(value).encode("utf-8"),
            key=key,
            headers=headers,
            value=value,
        )


class BridgeKafkaConsumer(Protocol):
    """The minimal async-iterable Kafka consumer seam `run_consumer_loop` needs."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def commit(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[BridgeMessage]: ...


class AioKafkaBridgeConsumer:
    """Real `aiokafka`-backed consumer against `NOTIFICATIONS_TOPIC`.

    THE HONEST EXTERNAL BOUNDARY (module docstring): `start()`/iteration require a REACHABLE
    Kafka broker at `bootstrap_servers`, which this sandbox does not have. Every other class in
    this module is exercised by a fast, broker-free unit test; this one is exercised ONLY by
    `tests/integration/platform/test_notifications_bridge_live_kafka.py`, which skips loudly
    when no broker is reachable — never faked here.

    `enable_auto_commit=False`: `run_consumer_loop` commits explicitly, ONLY after a message
    dispatches without raising OR is confirmed into the dead-letter topic — at-least-once delivery
    with no offset advance on a fail-closed message (a genuine handoff failure, or a malformed one
    whose DLQ shunt did not complete), so a crash/restart re-delivers it rather than silently
    skipping it.

    NO `value_deserializer` (GAP-SC-04-a — see `BridgeMessage`'s docstring for the root cause):
    aiokafka hands over the raw bytes and `_to_message` parses them HERE, recording a failure as
    `parse_error` instead of raising from inside the iterator. That is what makes the raw bytes
    available to the DLQ and puts the failure inside a `try` the loop owns.
    """

    def __init__(self, *, bootstrap_servers: str, topic: str, group_id: str) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer: AIOKafkaConsumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def commit(self) -> None:
        await self._consumer.commit()

    def __aiter__(self) -> AsyncIterator[BridgeMessage]:
        return self._iter_values()

    async def _iter_values(self) -> AsyncIterator[BridgeMessage]:
        async for record in self._consumer:
            yield _to_message(record)


def _to_message(record: Any) -> BridgeMessage:
    """Adapt one `aiokafka.ConsumerRecord` to a `BridgeMessage`, parsing its value.

    A decode/parse failure becomes `parse_error`/`parse_code` on the returned message — NOT an
    exception — so the raw bytes survive to the dead-letter shunt and the failure surfaces inside
    `run_consumer_loop`'s own `try` rather than out of the `async for` itself. `raw` is the
    producer's bytes verbatim; nothing is re-encoded.
    """
    raw: bytes = record.value if isinstance(record.value, bytes) else bytes(record.value or b"")
    headers = tuple(
        (str(name), value if isinstance(value, bytes) else bytes(value or b""))
        for name, value in (record.headers or ())
    )
    common: dict[str, Any] = {
        "topic": str(record.topic),
        "partition": int(record.partition),
        "offset": int(record.offset),
        "raw": raw,
        "key": record.key if isinstance(record.key, bytes) else None,
        "headers": headers,
    }
    try:
        value = _deserialize_json_value(raw)
    except MalformedBridgeMessageError as exc:
        return BridgeMessage(**common, parse_error=exc.reason, parse_code=exc.code)
    return BridgeMessage(**common, value=value)


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
        raise MalformedBridgeMessageError(
            f"invalid JSON payload: {exc}", raw, code=REASON_INVALID_JSON
        ) from exc


class FakeBridgeKafkaConsumer:
    """In-memory `BridgeKafkaConsumer` double for unit tests. NEVER imported by production code.

    Iterates a fixed, injected list exactly once (mirrors `FakeKafkaPublisher`/
    `FakeCibSevenTransport`'s "NEVER imported by production code" discipline elsewhere in this
    repo). Entries may be:
      - a `BridgeMessage` — used verbatim (how a test drives a specific offset, header set, or a
        pre-set `parse_error` for the undecodable-bytes case);
      - anything else — an already-parsed value, wrapped by `BridgeMessage.from_value` with a
        monotonic offset. The double encodes its own `raw` bytes, which is honest for a double:
        it IS the thing that "delivered" them.
    """

    def __init__(self, messages: list[Any]) -> None:
        self._messages: list[BridgeMessage] = [
            message if isinstance(message, BridgeMessage) else BridgeMessage.from_value(message, offset=index)
            for index, message in enumerate(messages)
        ]
        self.started = False
        self.stopped = False
        self.commits = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def commit(self) -> None:
        self.commits += 1

    def __aiter__(self) -> AsyncIterator[BridgeMessage]:
        return self._iter_values()

    async def _iter_values(self) -> AsyncIterator[BridgeMessage]:
        for message in self._messages:
            yield message


# ---------------------------------------------------------------------------
# Dead-letter shunt (GAP-SC-04-a) — publisher seam + the confirm-then-audit-then-commit unit.
# ---------------------------------------------------------------------------


class BridgeDlqPublisher(Protocol):
    """The minimal producer seam the dead-letter shunt needs.

    Deliberately NOT `AioKafkaEventsProducer` (`platform/integrations/events_kafka_producer.py`),
    for two independent reasons. (1) That producer is a JSON-ENVELOPE publisher: it re-encodes a
    `dict`, applies the mirror-routing table and the scrub allowlist. A DLQ must carry the
    producer's original BYTES verbatim — a re-encoded, scrubbed, possibly-mirrored copy is not
    evidence of what arrived. (2) It is a fenced effect class (`check_effect_chokepoint_fence.py`
    §8.1) whose construction is allowlisted to two files; routing the DLQ through it would need
    the fence's allowlist widened for a publish that does not belong to it anyway.
    """

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish_dlq(
        self,
        topic: str,
        *,
        raw: bytes,
        key: bytes | None,
        headers: Sequence[tuple[str, bytes]],
    ) -> None: ...


class AioKafkaDlqPublisher:
    """Real `aiokafka`-backed dead-letter publisher. Same honest external boundary as
    `AioKafkaBridgeConsumer`: `start()`/`send_and_wait` need a REACHABLE broker, which this
    sandbox does not have, so it is exercised only by the live suites — never faked here.

    `send_and_wait` (not fire-and-forget `send`) is load-bearing: the shunt must CONFIRM the
    dead-letter record reached the broker before the consumer offset is allowed to advance. A
    fire-and-forget send would let the offset commit on a record still sitting in an in-memory
    accumulator that a crash would discard — a silent drop wearing a DLQ's clothes.
    """

    def __init__(self, *, bootstrap_servers: str, send_timeout_s: float = 10.0) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            request_timeout_ms=int(send_timeout_s * 1000),
        )
        self._send_timeout_s = send_timeout_s

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish_dlq(
        self,
        topic: str,
        *,
        raw: bytes,
        key: bytes | None,
        headers: Sequence[tuple[str, bytes]],
    ) -> None:
        await asyncio.wait_for(
            self._producer.send_and_wait(topic, raw, key=key, headers=list(headers)),
            timeout=self._send_timeout_s,
        )


#: `agent_id`/`agent_version` for the DLQ audit record — the same principal the fenced starter uses
#: for this daemon (`build_cibseven_process_starter`'s own defaults), so a tenant's audit chain
#: shows the bridge's quarantine decisions and its start decisions under one identity.
_DLQ_AGENT_ID: Final[str] = "notification_bridge"
_DLQ_AGENT_VERSION: Final[str] = "notification_bridge@v1"

#: ADR-0007 `action`/`decision` for a quarantine. `DENY` is the honest token: the bridge REFUSED to
#: process this message. It is not `REQUIRE_HUMAN` — nothing here opens a HITL task; a DLQ record
#: is an operator artifact, and claiming a human review that no BPMN models would be fabricating a
#: governance record.
_DLQ_ACTION_PREFIX: Final[str] = "bridge_dlq"
_DLQ_DECISION: Final[str] = "DENY"

#: Reserved header-name prefix for the bridge's own quarantine annotations. A producer header
#: carrying it is dropped rather than forwarded — see `BridgeDlqShunt._headers`.
_DLQ_HEADER_PREFIX: Final[str] = "maezo_dlq_"

#: Cap on the free-text reason carried in a DLQ header. The reason can embed a JSON parser's own
#: message, which is derived from bytes nobody validated — bounded here, and additionally passed
#: through `redact_error_message` (the repo's PHI-shaped-substring backstop) before it is written.
_DLQ_REASON_HEADER_MAX: Final[int] = 300


@dataclass
class BridgeDlqShunt:
    """Publisher + audit sink + tenant, bound together so the invariant is STRUCTURAL.

    Bundling is the point: a DLQ publish with no audit fact is a silent drop with extra steps, and
    an audit fact with no confirmed publish is a fabricated one. Passing the two separately to
    `run_consumer_loop` would make "both or neither" a convention a future caller could forget;
    here it is a constructor.

    ORDER — publish, then audit, then (the loop) commit. Deliberately NOT the emit-before-effect
    order the ADR-0007 process-start fence uses, and the difference is principled: that fence
    guards a REGULATED EFFECT (starting a beneficiary's process), where an un-audited effect is
    the unacceptable outcome, so the audit must precede it. Here the "effect" is moving bytes to a
    quarantine topic, and the unacceptable outcome is an audit row asserting a shunt that never
    happened. So the fact is recorded only after the shunt is CONFIRMED.

    The cost of that order is stated rather than hidden: if the audit emit fails after a successful
    DLQ publish, `shunt` raises, the offset is NOT committed, and the redelivery publishes the same
    record to the DLQ a second time. That is at-least-once — the same tradeoff, for the same
    reason, that `a2a/outbox_relay.py`'s publish-then-mark order documents. The duplicate is bounded
    and self-collapsing on the audit side: `dedup_key` (below) is identical across every redelivery
    OF THE SAME BYTES, so `emit_once_status` writes ONE chain row no matter how many times that
    record is re-shunted — while a DIFFERENT record occupying the same broker coordinates gets its
    own key and therefore its own chain row.

    THE SINK MUST REPORT ITS DEDUP OUTCOME (`DedupReportingAuditSink`, not the bare
    `AuditStartSink`). `emit_once` returns a hash on BOTH paths, so a shunt built on it cannot tell
    "chain row written" from "a prior claim already owned this key, nothing was written" — and a
    silent second outcome is precisely the ADR-0007 fact this class exists to guarantee. The
    Protocol is checked in `__post_init__`, so a non-reporting sink is refused at construction
    rather than at the first poison message.
    """

    publisher: BridgeDlqPublisher
    audit_sink: DedupReportingAuditSink
    tenant_id: str
    #: Populated by `shunt` — `(dlq_topic, reason_code)` per shunted message. In-process
    #: observability/test surface only, never a durable trail (the audit chain is that).
    shunted: list[tuple[str, str]] = field(default_factory=list)
    #: Populated by `shunt` — the `dedup_key` of every emit that hit a PRIOR claim (a redelivery
    #: of bytes already audited). Same in-process-only status as `shunted`; the point is that the
    #: outcome is OBSERVED rather than discarded.
    deduped_audits: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """FAIL-CLOSED on a sink that cannot report its dedup outcome.

        `isinstance` against a `runtime_checkable` Protocol (the same probe
        `start_process_idempotent` uses for its strict families) — structural, so any sink that
        really implements `emit_once_status` passes, and an emit-only sink is refused HERE instead
        of degrading `shunt` back to a discarded dedup flag.
        """
        if not isinstance(self.audit_sink, DedupReportingAuditSink):
            raise TypeError(
                "BridgeDlqShunt: audit_sink must implement `emit_once_status` "
                "(DedupReportingAuditSink) — a sink that cannot report whether the chain row was "
                "written would make a deduped emit indistinguishable from a fresh one, i.e. a "
                "quarantine committed with no ADR-0007 fact"
            )

    def dedup_key(self, message: BridgeMessage) -> str:
        """`{tenant}:bridge_dlq:{topic}:{partition}:{offset}:{sha256(raw)}` — the record's IDENTITY.

        Broker coordinates alone are NOT an identity. They are stable across redelivery (which is
        the property this key needs), but they are also REUSED: deleting and recreating a topic
        restarts its offsets at 0 while `audit_emit_dedup` (migration 0005) survives, so a brand
        new poison message can land on `(topic, partition, offset)` that an old quarantine already
        claimed. `emit_once_status` on a claimed key writes NO second chain link
        (`audit_postgres.py:317-320`), so a coordinates-only key would let that new message commit
        its offset with no fact in the chain — the silent drop this class exists to prevent,
        reachable by an ordinary operational event (`docker compose down -v` in dev/CI, a topic
        recreation during a production DR).

        Folding the content hash in fixes that at the root and keeps BOTH properties:

          - same bytes at the same coordinates (a genuine redelivery) -> same key -> ONE chain row;
          - different bytes at the same coordinates (a reused offset) -> different key -> its OWN
            chain row.

        The hash is the one `_audit_record` already computes for `details["raw_sha256"]`, so the
        key and the durable evidence name the same bytes. The FULL digest is used, not a prefix:
        the key is a `text` column (`0005_audit_emit_dedup.py:61`) with no length pressure, and a
        truncation would trade a proven property for an unnecessary birthday-bound argument.
        """
        raw_sha256 = hashlib.sha256(message.raw).hexdigest()
        return (
            f"{self.tenant_id}:{_DLQ_ACTION_PREFIX}:{message.topic}:"
            f"{message.partition}:{message.offset}:{raw_sha256}"
        )

    def _headers(self, message: BridgeMessage, error: MalformedBridgeMessageError) -> list[tuple[str, bytes]]:
        """DLQ headers: bounded diagnostic tokens ONLY.

        The offending PAYLOAD is the record's value (carried verbatim, which is the point of a
        DLQ); the headers must not add anything derived from it beyond the bounded, redacted
        reason. `maezo_dlq_reason` is the closed-vocabulary code; `maezo_dlq_detail` is the
        human-readable reason run through `redact_error_message` and capped — the parser's message
        can quote a fragment of the bytes. What that backstop actually covers is narrow and stated
        narrowly (`phi_vars.redact_error_message`): separated CPF/CNPJ digit-group shapes and any
        run of 11+ contiguous digits -> `[REDACTED_DIGITS]`. It does NOT recognise e-mail
        addresses, names or free-text identifiers. It is a backstop for a message shape that is
        already bounded by construction here — `json.JSONDecodeError`/`UnicodeDecodeError` state a
        position and a codec, not payload content — never a general-purpose PHI scrubber.

        The producer's OWN `maezo_dlq_*` headers are DROPPED and every other producer header is
        carried through unchanged and FIRST. Dropping is what makes ours authoritative: Kafka's
        header list admits duplicates, and which one a consumer keeps (first-wins vs last-wins) is
        consumer-dependent — so a producer-forged `maezo_dlq_reason` preceding ours would be read
        as the bridge's own verdict by a first-wins reader. The `maezo_dlq_` prefix is OURS on this
        path; a producer setting it is either a mistake or an attempt to mislabel a quarantine.
        """
        detail = redact_error_message(error.reason)[:_DLQ_REASON_HEADER_MAX]
        carried = [
            (name, value) for name, value in message.headers if not name.startswith(_DLQ_HEADER_PREFIX)
        ]
        return [
            *carried,
            ("maezo_dlq_reason", error.code.encode("utf-8")),
            ("maezo_dlq_detail", detail.encode("utf-8")),
            ("maezo_dlq_source_topic", message.topic.encode("utf-8")),
            ("maezo_dlq_source_partition", str(message.partition).encode("utf-8")),
            ("maezo_dlq_source_offset", str(message.offset).encode("utf-8")),
            ("maezo_dlq_tenant", self.tenant_id.encode("utf-8")),
        ]

    def _audit_record(
        self, message: BridgeMessage, error: MalformedBridgeMessageError, dlq_topic: str
    ) -> AuditRecord:
        """The durable ADR-0007 fact for one quarantine. PHI-safe by construction.

        `details` carries ONLY bounded class tokens plus a one-way `raw_sha256` of the offending
        bytes. The hash is what makes the record EVIDENCE — an operator reading the DLQ can prove
        a given record is the one this row is about — without ever putting unvalidated,
        possibly-PHI-bearing bytes into the durable chain. The same discipline
        `build_start_audit_record` applies with its own `input_sha256`.
        """
        return AuditRecord(
            agent_id=_DLQ_AGENT_ID,
            tenant_id=self.tenant_id,
            agent_version=_DLQ_AGENT_VERSION,
            action=f"{_DLQ_ACTION_PREFIX}:{message.topic}",
            decision=_DLQ_DECISION,
            details={
                "reason_code": error.code,
                "source_topic": message.topic,
                "source_partition": message.partition,
                "source_offset": message.offset,
                "dlq_topic": dlq_topic,
                "raw_bytes": len(message.raw),
                "raw_sha256": hashlib.sha256(message.raw).hexdigest(),
            },
        )

    @staticmethod
    def _record_metric(source_topic: str, reason_code: str) -> None:
        """Emit `maezo_bridge_dlq_total{topic,reason}`. NEVER raises into the shunt.

        Same defensive posture as `base._record_key_mint`/`harness._emit_worker_task_outcome`: a
        metrics/registry problem must never be able to fail — or silently alter — a fail-closed
        quarantine. The audit chain, not this counter, is the record of what happened.
        """
        try:
            from maezo.platform.observability import record_bridge_dlq  # noqa: PLC0415 — lazy

            record_bridge_dlq(topic=source_topic, reason=reason_code)
        except Exception:  # noqa: BLE001 — telemetry is best-effort; the shunt must never fail on it
            logger.debug("notifications_bridge.dlq_metric_failed", reason=reason_code)

    async def shunt(self, message: BridgeMessage, error: MalformedBridgeMessageError) -> str:
        """Quarantine ONE poison message. Returns the DLQ topic it landed on.

        RAISES on any failure — that is the whole contract. `run_consumer_loop` commits the offset
        only if this returns, so a failed DLQ publish or a failed audit emit keeps the original
        fail-closed behaviour (no offset advance, the message is re-delivered) instead of
        advancing past a message that went nowhere.

        `dlq_topic_for` validates the SOURCE topic through the registry's own convention on this
        hot path, so a malformed source topic raises here rather than minting a valid-looking DLQ
        name — also a fail-closed outcome (no commit).

        THE DEDUP OUTCOME IS OBSERVED, NEVER DISCARDED. `emit_once_status` reports whether this
        call wrote the chain link or found a prior claim. Because `dedup_key` binds the record's
        CONTENT to its coordinates, `deduped=True` has exactly one meaning here — these same bytes,
        at these same coordinates, were already quarantined and already audited — which is the
        legitimate at-least-once redelivery this order's tradeoff predicts. So it is a normal
        return (the offset may advance: the fact exists), logged rather than swallowed. It is NOT
        the "someone else already committed to the effect" gate `start_process_idempotent` reads
        the same flag for: re-publishing identical bytes to a quarantine topic is idempotent by
        nature, so refusing the commit would only stall the partition on a message whose fact is
        already in the chain.
        """
        dlq_topic = dlq_topic_for(message.topic)
        await self.publisher.publish_dlq(
            dlq_topic,
            raw=message.raw,
            key=message.key,
            headers=self._headers(message, error),
        )
        dedup_key = self.dedup_key(message)
        outcome = await self.audit_sink.emit_once_status(
            self._audit_record(message, error, dlq_topic), dedup_key=dedup_key
        )
        if outcome.deduped:
            self.deduped_audits.append(dedup_key)
            logger.warning(
                "notifications_bridge.dlq_audit_deduped",
                dlq_topic=dlq_topic,
                source_topic=message.topic,
                partition=message.partition,
                offset=message.offset,
                reason=error.code,
                record_hash=outcome.record_hash,
            )
        self.shunted.append((dlq_topic, error.code))
        self._record_metric(message.topic, error.code)
        logger.warning(
            "notifications_bridge.dlq_shunted",
            dlq_topic=dlq_topic,
            source_topic=message.topic,
            partition=message.partition,
            offset=message.offset,
            reason=error.code,
        )
        return dlq_topic


# ---------------------------------------------------------------------------
# The consume loop — fully unit-tested against FakeBridgeKafkaConsumer (no broker needed); the
# SAME loop drives AioKafkaBridgeConsumer in production (main(), below).
# ---------------------------------------------------------------------------


async def run_consumer_loop(
    consumer: BridgeKafkaConsumer,
    bridge: NotificationBridge,
    *,
    stop_event: asyncio.Event | None = None,
    dlq: BridgeDlqShunt | None = None,
) -> None:
    """Drive `handle_bridge_message` over every message `consumer` yields.

    Commits ONLY after a message dispatches without raising, or (when `dlq` is wired) after a
    malformed message is CONFIRMED into its dead-letter topic AND durably audited.

    FAIL-CLOSED, NARROWED — NOT WEAKENED (GAP-SC-04-a; the full argument is in the module
    docstring's poison-message section):

      - `except MalformedBridgeMessageError` is the ONLY arm the shunt lives in. That error means
        DETERMINISTIC malformation: the same bytes fail identically forever, so blocking the
        offset guarantees a stalled partition and never eventual success.
      - EVERY other exception propagates exactly as before — `NotificationBridgeHandoffFailedError`
        (EB-3 part 1), `AuditPersistenceError` from the fenced starter (the fault class
        `tests/integration/chaos/test_a3_bridge_fail_closed.py` pins), broker/engine timeouts.
        Those are transient; retry is the right posture and the offset stays put.
      - `dlq=None` (the default) reproduces the pre-GAP-SC-04-a behaviour byte-for-byte: a
        malformed message re-raises and nothing is committed.
      - A malformed message is NEVER dropped. `BridgeDlqShunt.shunt` raises unless the DLQ publish
        was confirmed and the audit fact emitted; the commit below is reached only if it returns.

    `message.parse_error` is checked FIRST, before `handle_bridge_message`: bytes that never parsed
    carry no value to dispatch, and checking the flag ahead of the value is what keeps a parse
    failure from being confused with a value that legitimately parsed to JSON `null`.

    `stop_event`, when provided and set between messages, ends the loop gracefully (used by
    `main()`'s SIGTERM/SIGINT handling) — irrelevant to `FakeBridgeKafkaConsumer`-backed unit
    tests, which simply exhaust their fixed message list.
    """
    async for message in consumer:
        try:
            if message.parse_error:
                raise MalformedBridgeMessageError(
                    message.parse_error, message.raw, code=message.parse_code or REASON_INVALID_JSON
                )
            results = await handle_bridge_message(bridge, message.value)
        except MalformedBridgeMessageError as exc:
            if dlq is None:
                raise
            # Raises on a failed publish / failed audit -> the commit below is never reached and
            # the offset does not advance (the original fail-closed outcome, preserved exactly).
            await dlq.shunt(message, exc)
        else:
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
) -> tuple[NotificationBridge, GatedCibSevenTransport, PostgresAuditSink]:
    """Construct the `NotificationBridge` wired to the FENCED starter — the only sanctioned
    production path (`build_cibseven_process_starter`'s own docstring). Never a raw/un-audited
    starter reaches production from this composition root.

    FAIL-CLOSED: raises if `settings.database_url` is unset — this daemon starts CIB Seven
    processes and must never do so without a durable ADR-0007 audit sink (never a fake/no-op
    sink standing in for a real one in production).

    RETURNS THE SINK (GAP-SC-04-a): the dead-letter shunt needs the SAME `PostgresAuditSink` this
    root already builds. Constructing a second one inside `build_dlq_shunt` would open a second
    connection pool against the same database for the same tenant AND, worse, split one daemon's
    audit chain across two sink instances — so the root hands out the one it owns.
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
    return bridge, transport, audit_sink


def build_dlq_shunt(
    settings: NotificationsBridgeSettings, audit_sink: DedupReportingAuditSink
) -> BridgeDlqShunt:
    """Construct the production dead-letter shunt (GAP-SC-04-a).

    Takes the audit sink `build_bridge` already constructed rather than building its own — see
    that function's docstring. There is no "DLQ disabled" setting on purpose: a daemon that can
    consume but cannot quarantine is the head-of-line-blocking build this slice exists to end, and
    an env flag able to silently restore it would make the fix optional in exactly the deployment
    that most needs it. The seam that CAN be `None` is `run_consumer_loop(dlq=...)`, and its only
    user is the unit suite proving the pre-GAP-SC-04-a behaviour is unchanged when no shunt exists.
    """
    return BridgeDlqShunt(
        publisher=AioKafkaDlqPublisher(bootstrap_servers=settings.kafka_bootstrap_servers),
        audit_sink=audit_sink,
        tenant_id=settings.tenant_id,
    )


async def main() -> None:
    """Composition root + runnable entry point (`python -m maezo.platform.integrations.
    notifications_bridge`) — what `deployment-bridge.yaml:50` has referenced all along.

    Builds the real transport/audit-sink/bridge (`build_bridge`) and the real
    `AioKafkaBridgeConsumer`, then drives `run_consumer_loop` until SIGTERM/SIGINT. See the
    module docstring for the honest boundary this function's Kafka/engine calls cannot be
    exercised against in this sandbox.
    """
    settings = NotificationsBridgeSettings()
    bridge, transport, audit_sink = build_bridge(settings)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
    )
    dlq = build_dlq_shunt(settings, audit_sink)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - e.g. Windows ProactorEventLoop
            loop.add_signal_handler(sig, stop_event.set)

    await consumer.start()
    # Started BEFORE the loop, not lazily on first use: a broker the DLQ publisher cannot reach is
    # a startup failure an operator sees at boot, not a surprise discovered by the first poison
    # message — at which point the daemon would be back to blocking the partition.
    await dlq.publisher.start()
    logger.info(
        "notifications_bridge.started",
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
        tenant_id=settings.tenant_id,
        dlq_topic=dlq_topic_for(settings.kafka_topic),
    )
    try:
        await run_consumer_loop(consumer, bridge, stop_event=stop_event, dlq=dlq)
    finally:
        await consumer.stop()
        with contextlib.suppress(Exception):
            await dlq.publisher.stop()
        await transport.close()
        logger.info("notifications_bridge.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
