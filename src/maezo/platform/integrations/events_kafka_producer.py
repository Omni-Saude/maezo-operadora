"""Kafka PRODUCER leg for the notifications-bridge wire (T4 — "make the mirror live").

`events.py::make_publish_event_handler` (the ONE generic `operadora.events.publish` worker every
SP-OP-* BPMN's `ST_Publish*` service task routes through) has always accepted a `kafka:
KafkaPublisher | None` seam — but no composition root has ever constructed a REAL one
(`worker_runtime/service.py`'s own `kafka_ready` readiness check said so explicitly: "not required
by any worker registered in this build (kafka=None; unused today)"). That is the ENTIRE gap this
module closes: an `AioKafkaEventsProducer` implementing `harness.KafkaPublisher`, wired into
`worker_runtime/service.py`'s bring-up, so `operadora.events.publish` tasks actually reach a wire.

ROOT-CAUSE FINDING (drives the design below — see `docs/design/audit-emit-path-wiring.md` and the
EB-4 bridge-reconciliation note in `notification_bridge.py` for the upstream half of this story):
the notifications-bridge Kafka consumer (`maezo.platform.integrations.notifications_bridge`) reads
exactly ONE topic, `operadora.notifications.internal`, and dispatches on a `type` discriminator
field. Grepping every `spec/processes/bpmn/**.bpmn` `ST_Publish*` service task shows TWO different
wiring shapes already baked into the BPMN literals (never touched here — spec/ is out of this
task's edit authority):

  1. `ans.cron_due` (SP-OP-ANS-CRON-001's 5 timer branches) ALREADY sets
     `event_topic=operadora.notifications.internal` + `event_type=ans.cron_due` as literal
     `camunda:inputParameter`s — i.e. it is ALREADY correctly addressed at the BPMN-authoring
     layer. Once a real `KafkaPublisher` exists (this module), that fact reaches the bridge with
     ZERO further wiring — proven by `tests/integration/platform/
     test_events_kafka_producer_live.py::test_ans_cron_due_reaches_notifications_topic_unmirrored`.

  2. CONTAS/FRAUDE `*.completed` (`agents.events.contas.completed` / `agents.events.fraude.
     completed`, the events `notification_bridge.py`'s 5 reconciled EB-4 rules key off of) set
     `event_topic` to THEIR OWN per-domain topic — NOT `operadora.notifications.internal` — and
     set NO `event_type` input parameter at all (so the raw payload carries no `type` key either).
     Even with a real producer, these would reach the wrong topic, untyped, and the bridge would
     never see them.

  This module closes gap (2) at the PRODUCER layer (never by editing `events.py` or `spec/`,
  both out of this task's edit authority): `AioKafkaEventsProducer.publish()` applies a static
  MIRROR_TOPICS routing table — for `agents.events.contas.completed` / `agents.events.fraude.
  completed`, in ADDITION to the real per-domain publish, it republishes an ENVELOPE (original
  payload + `type=<source topic>`) onto `NOTIFICATIONS_TOPIC`. This is a MIRROR, not a reroute:
  the per-domain topic keeps carrying the full, untouched event trail (any future
  observability/audit consumer is unaffected); the bridge additionally receives a typed copy.

FAIL-SAFE DISCIPLINE (the task's own explicit invariant — "producer failures must not break the
source process's completion; the in-flow fenced workers are the guaranteed path, the Kafka mirror
is best-effort-with-audit"): `events.py`'s `kafka=None` decision (its own module docstring) already
established that NO BPMN gateway ever routes on `event_published`/`event_topic` for any topic
outside SP-OP-ESCALATION-001's 4 `ERR_EVENT_PUBLISH_FAILED`-boundary-carrying topics (the ONLY
family with a modeled fallback for a publish failure — `_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS`).
Wiring a REAL producer must not silently change that: a Kafka outage must not newly stall
CONTAS/FRAUDE/ANS-CRON at their unconditional, no-gateway `ST_Publish*` step (they would sit
retrying/incidented forever with no operator-visible fallback, unlike escalation's modeled one).
So `BEST_EFFORT_TOPICS` (= `MIRROR_TOPICS | {NOTIFICATIONS_TOPIC}`) publishes never raise out of
`publish()` BY DEFAULT: a broker-down/timeout/send failure is caught, logged LOUDLY
(`kafka_publish_failed` / `kafka_mirror_publish_failed`, matching `events.py`'s
`event_publish_failed` log-event naming convention) and recorded on `self.failed_publishes`
(in-process, test/observability-introspectable counter — this module owns no durable-audit table;
the durable ADR-0007 audit chain is the fenced START path's job, not this best-effort mirror's),
then `publish()` returns normally. Every OTHER topic (escalation's 4, and any future one not in
this routing table) is UNCHANGED: a send failure still propagates, so `events.py`'s existing
`except Exception` -> `WorkerBpmnError(ERR_EVENT_PUBLISH_FAILED)` boundary-catch machinery for
escalation keeps working exactly as before this module existed.

PER-CALL POSTURE OVERRIDE (t8-escalation-boundary — the ROOT-CAUSE fix for the escalation/lgpd
notify swallow): the topic-based default above is only the DEFAULT. `publish(..., best_effort=...)`
lets a caller override it, because the criticality of a publish is a property of the CALL, not the
topic. `best_effort=None` keeps the topic-based default (all existing callers). `best_effort=False`
FORCES propagate regardless of topic — escalation's `notify_team`/`notify_supervisor` and lgpd's
`notify_sla_risk` publish to `NOTIFICATIONS_TOPIC` (topic-default best-effort) yet a lost grave
clinical escalation / LGPD Art. 19 legal-deadline notice must NOT be swallowed here: it must reach
escalation's modeled `ERR_ESC_NOTIFY_FAILED` boundary or lgpd's harness retry/incident ladder.
`best_effort=True` forces swallow. The MIRROR leg is ALWAYS best-effort regardless (unchanged) — a
mirror failure must never break a source BPMN.

PARTITION-KEY CHOKEPOINT (GAP-SC-04-a, audit D5 / gateway gvr-d05 — the ordering fix): `publish()`
used to accept `key=None` as a silent default, and every call site spelled the key
`key=task.business_key or None`, so a blank business key degraded — silently — into "no partition
key at all". With the registry's default `partitions=3` (`topic_registry.py:75,142`) a keyless
record is assigned round-robin, so two events about the SAME beneficiary/process can land on
DIFFERENT partitions. One consumer replica hides that; scaling the notifications-bridge past one
replica would expose it as reordering. `publish()` now REFUSES a keyless publish
(`partition_key.MissingPartitionKeyError`) unless the caller declares `unordered=True`, and derives
a deterministic, PHI-free key from the payload when the caller passes none — see
`maezo.platform.integrations.partition_key` for the derivation chain and the PHI proof. The refusal
is the point: absence of a key is never again read as "ordering does not matter here".

PHI/scrub backstop (task's own ask — "if the convention requires one"): the per-domain payload
`events.py` builds is untouched (BPMN `event_payload_vars` authors already curated it — verified,
zero free-text vars in any `event_payload_vars` list, per `events.py`'s own docstring). The
MIRRORED envelope landing on `operadora.notifications.internal` gets an EXTRA allowlist backstop
(`scrub_mirror_payload`) before it is republished: only a curated set of business-key-anchor /
routing-fact field names survive; everything else is dropped (key names only logged, never
values — mirrors `events.py`'s own `payload_keys=sorted(...)` convention) — defense in depth for
the ONE topic a real process-starting Kafka consumer reads, even though the source is already
believed PHI-safe.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any, Protocol

import structlog

from maezo.gateway.kafka_client import KafkaConnectionSettings, create_kafka_producer
from maezo.platform.integrations.partition_key import (
    MissingPartitionKeyError,
    derive_partition_key,
)

logger = structlog.get_logger(__name__)

#: The bridge's single input topic (mirrors `maezo.platform.integrations.notifications_bridge.
#: NOTIFICATIONS_TOPIC` — duplicated as a plain string constant, not an import, so this module
#: carries no dependency on the consumer module; the two are proven to agree by
#: `test_events_kafka_producer_live.py`, which imports BOTH constants and asserts equality).
NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

#: Domain-completed-event topics that ALSO need to reach `NOTIFICATIONS_TOPIC` (module docstring,
#: gap (2)) because their BPMN `ST_Publish*` tasks set `event_topic` to their OWN per-domain topic
#: and set no `event_type` input parameter. Keyed by the exact `event_topic` value; the mirror
#: envelope's `type` becomes this same string — the identical literal
#: `notification_bridge.CONTAS_COMPLETED_EVENT` / `FRAUDE_COMPLETED_EVENT` already key their
#: reconciled rules on.
MIRROR_TOPICS: frozenset[str] = frozenset(
    {
        "agents.events.contas.completed",
        "agents.events.fraude.completed",
    }
)

#: Topics whose publish (primary AND mirror) is BEST-EFFORT (module docstring, fail-safe
#: discipline): a send failure is caught, logged, counted — never raised. `NOTIFICATIONS_TOPIC`
#: itself is included because `ans.cron_due` already targets it DIRECTLY (gap (1) — no mirroring
#: needed, but the SAME "no BPMN gateway routes on this, a stall would be silent-forever" argument
#: applies to that direct publish too).
BEST_EFFORT_TOPICS: frozenset[str] = MIRROR_TOPICS | {NOTIFICATIONS_TOPIC}

#: PHI/business-identifier scrub ALLOWLIST for the MIRRORED envelope only (module docstring). Union
#: of: (a) the fixed prefix/marker keys `events.py`'s handler always sets
#: (`_business_key`/`_process_instance_id`/`_worker_topic`/`desfecho`/`fase`/
#: `tipo_mudanca`/`ans_cron_reference_date_iso` — but NOT `type`, which the mirror envelope owns
#: exclusively, see the R1 F1 note inside the set), (b) every `event_payload_vars` literal name used
#: by a CONTAS/FRAUDE `*.completed` `ST_Publish*` task today (grep-verified against `spec/processes/
#: bpmn/SP-OP-{CONTAS,FRAUDE}-001*.bpmn`), and (c) the business-key-anchor fields `notification_
#: bridge.py`'s reconciled predicates require (`numero_guia_tiss`/`glosa_id`/`prestador_id`/
#: `numero_contrato`/`entidade_tipo`) — present in the payload once the (separate, out-of-scope-
#: here) "arming" follow-up enriches `event_payload_vars`; allowlisted in ADVANCE so this backstop
#: does not need a second edit when that follow-up lands.
MIRROR_PAYLOAD_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Fixed marker/prefix keys events.py always sets. NOTE (R1 F1): `type` is deliberately
        # NOT allowlisted — the mirror ENVELOPE owns that field exclusively (`publish()` stamps
        # `type=<source topic>` itself, and no CONTAS/FRAUDE `ST_Publish*` task sets an
        # `event_type` inputParameter, so a legitimate source payload for a mirrored topic never
        # carries `type`); a source-payload `type` key is therefore always injected/foreign and
        # must be scrubbed, never allowed to shadow the envelope's discriminator.
        "desfecho",
        "fase",
        "tipo_mudanca",
        "ans_cron_reference_date_iso",
        "_business_key",
        "_process_instance_id",
        "_worker_topic",
        # CONTAS *.completed event_payload_vars (union across all 4 desfecho branches).
        "tenant_id",
        "numero_lote_tiss",
        "prestador_id",
        "glosa_id",
        "codigo_glosa_aceito",
        "analista_id",
        # FRAUDE *.completed event_payload_vars (union across all 6 desfecho branches).
        "numero_caso",
        "investigator_id",
        "tier",
        # Business-key anchors the reconciled bridge predicates require (module docstring (c)).
        "numero_guia_tiss",
        "numero_contrato",
        "entidade_tipo",
        "beneficiario_pseudo_id",
        "glosa_type",
    }
)


#: The one allowlist entry the DL-0043 privacy policy can REVOKE. `_business_key` is the raw
#: engine business key, which for the CANCEL/INAD families can be
#: `CANCEL-{tenant}-{matricula_beneficiario}` — a `PHI_PROCESS_VARS` value riding a mirrored
#: Kafka envelope. Under a ratified `scrub_only`/`pseudo_keys` it stops being allowlisted and is
#: dropped by the existing backstop (which already logs dropped key NAMES, never values).
#: Dropping rather than pseudonymizing is deliberate: no bridge rule reads `_business_key` — the
#: reconciled rules each DERIVE their own `business_key` from the payload's anchor fields
#: (`notification_bridge._{recurso,fraude,cred,cancel,inadimplencia}_business_key`) — so removing
#: it costs nothing downstream, whereas a pseudonymized value would look like a usable key.
_POLICY_REVOCABLE_ALLOWLIST_KEYS: frozenset[str] = frozenset({"_business_key"})


def _effective_mirror_allowlist() -> frozenset[str]:
    """`MIRROR_PAYLOAD_ALLOWLIST`, minus whatever the privacy policy revokes.

    Returns the constant UNCHANGED under the shipped (`off`) policy — the mirrored envelope is
    byte-identical to before DL-0043.
    """
    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # lazy

    if phi_key_policy().scrubbing_enabled:
        return MIRROR_PAYLOAD_ALLOWLIST - _POLICY_REVOCABLE_ALLOWLIST_KEYS
    return MIRROR_PAYLOAD_ALLOWLIST


def scrub_mirror_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Allowlist backstop applied ONLY to the envelope mirrored onto `NOTIFICATIONS_TOPIC`.

    Drops every key not in the effective allowlist. Logs the dropped key NAMES only (never
    values — mirrors `events.py`'s `payload_keys=sorted(payload.keys())` convention) at DEBUG,
    so an unexpectedly-dropped field is visible without ever putting a value in a log line.

    The effective allowlist is `MIRROR_PAYLOAD_ALLOWLIST` under the shipped privacy policy and
    `MIRROR_PAYLOAD_ALLOWLIST - {"_business_key"}` once `scrub_only` is ratified — see
    `_POLICY_REVOCABLE_ALLOWLIST_KEYS`.
    """
    allowlist = _effective_mirror_allowlist()
    kept = {k: v for k, v in payload.items() if k in allowlist}
    dropped = sorted(set(payload.keys()) - allowlist)
    if dropped:
        logger.debug("kafka_mirror_payload_scrubbed", dropped_keys=dropped)
    return kept


# ---------------------------------------------------------------------------
# Raw producer seam — Protocol + real (aiokafka) + fake (tests), mirrors the repo's
# transport-triple pattern (dmn_transport.py / mcp_cibseven/transport.py / notifications_bridge.py's
# own BridgeKafkaConsumer triple).
# ---------------------------------------------------------------------------


class RawKafkaProducer(Protocol):
    """The minimal `aiokafka.AIOKafkaProducer`-shaped seam `AioKafkaEventsProducer` needs."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_and_wait(self, topic: str, value: bytes, key: bytes | None = None) -> Any: ...


class FakeRawKafkaProducer:
    """In-memory `RawKafkaProducer` double for unit tests. NEVER imported by production code.

    Records every `send_and_wait` call (`.sent` — `(topic, value, key)`, JSON-decoded). Set
    `.fail_next = <exc>` (or `.always_fail = <exc>`) to make the next (or every) send raise,
    exercising the fail-safe/best-effort paths without a real broker.
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.fail_next: BaseException | None = None
        self.always_fail: BaseException | None = None

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(self, topic: str, value: bytes, key: bytes | None = None) -> None:
        import json

        if self.always_fail is not None:
            raise self.always_fail
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        self.sent.append((topic, json.loads(value.decode("utf-8")), key.decode("utf-8") if key else None))


def _encode_json(value: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(dict(value)).encode("utf-8")


# ---------------------------------------------------------------------------
# AioKafkaEventsProducer — the real KafkaPublisher (harness.py Protocol) implementation.
# ---------------------------------------------------------------------------


class AioKafkaEventsProducer:
    """Real `KafkaPublisher` (`maezo.tools.workers.harness.KafkaPublisher` Protocol structural
    match — `async def publish(self, topic, value, *, key=None, best_effort=None) -> bool`)
    backing every `operadora.events.publish`/raw-handler `kafka.publish(...)` call site in this
    build (`events.py`, `lgpd.py`'s 3 notification handlers, ...).

    Composition: either constructed with `bootstrap_servers` (production — lazily builds a real
    `aiokafka.AIOKafkaProducer` on first `start()`) or with an injected `raw_producer` (tests —
    `FakeRawKafkaProducer`, NEVER used in production). Mirrors `AioKafkaBridgeConsumer`'s own
    real/fake seam split in `notifications_bridge.py`.

    Routing + fail-safe behavior — see module docstring:
      - `MIRROR_TOPICS`: publish also mirrors an envelope onto `NOTIFICATIONS_TOPIC` (the mirror
        leg is ALWAYS best-effort).
      - `BEST_EFFORT_TOPICS`: the primary publish is best-effort BY DEFAULT (never raises; a
        failure is logged + appended to `self.failed_publishes`) — UNLESS the caller passes
        `best_effort=False`, which forces propagate (the escalation/lgpd notify callers do).
      - Every other topic: unchanged pre-existing propagate-on-failure behavior (needed so
        SP-OP-ESCALATION-001's `ERR_EVENT_PUBLISH_FAILED` boundary-catch machinery, the one
        family that DOES model a publish-failure fallback, keeps working).
      - `best_effort` per-call override (see `publish`): the criticality is a property of the CALL.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str | None = None,
        raw_producer: RawKafkaProducer | None = None,
        connection_settings: KafkaConnectionSettings | None = None,
        connect_timeout_s: float = 10.0,
        send_timeout_s: float = 10.0,
    ) -> None:
        if connection_settings is not None:
            if raw_producer is not None or (
                bootstrap_servers is not None and bootstrap_servers != connection_settings.bootstrap_servers
            ):
                raise ValueError("kafka_connection_configuration_conflict")
            bootstrap_servers = connection_settings.bootstrap_servers
        if bootstrap_servers is None and raw_producer is None:
            raise ValueError(
                "AioKafkaEventsProducer requires either bootstrap_servers (production) or "
                "raw_producer (tests) — never neither."
            )
        self._connection_settings = connection_settings or (
            KafkaConnectionSettings(bootstrap_servers) if bootstrap_servers is not None else None
        )
        self._bootstrap_servers = bootstrap_servers
        self._raw = raw_producer
        self._connect_timeout_s = connect_timeout_s
        self._send_timeout_s = send_timeout_s
        self._started = False
        self._start_lock = asyncio.Lock()
        #: Best-effort failure ledger — (topic, error repr) — for observability/tests. NOT a
        #: durable audit trail (module docstring: this mirror is best-effort, the fenced START
        #: path owns durable ADR-0007 audit; this is a lightweight, in-process, log-adjacent
        #: signal only).
        self.failed_publishes: list[tuple[str, str]] = []

    async def _ensure_started(self) -> RawKafkaProducer:
        if self._raw is not None:
            if not self._started:
                async with self._start_lock:
                    if not self._started:
                        await asyncio.wait_for(self._raw.start(), timeout=self._connect_timeout_s)
                        self._started = True
            return self._raw
        async with self._start_lock:
            if not self._started:
                assert self._connection_settings is not None
                producer = await create_kafka_producer(
                    self._connection_settings,
                    request_timeout_ms=int(self._send_timeout_s * 1000),
                )
                try:
                    await asyncio.wait_for(producer.start(), timeout=self._connect_timeout_s)
                except BaseException:
                    # Failed/cancelled authentication must not orphan allocated clients.
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(producer.stop(), timeout=self._connect_timeout_s)
                    raise
                self._raw = producer
                self._started = True
        assert self._raw is not None  # narrows for mypy — set immediately above under the lock
        return self._raw

    def resolve_partition_key(
        self, topic: str, value: Mapping[str, Any], *, key: str | None, unordered: bool
    ) -> str | None:
        """The GAP-SC-04-a fail-closed key gate. Returns the key to publish with.

        `key` when the caller supplied a non-blank one (arm (1) — every existing call site);
        otherwise `partition_key.derive_partition_key(topic, value)`. When BOTH are empty:
          - `unordered=True` -> `None` is returned and the record is published unkeyed. The caller
            has DECLARED that per-entity ordering is meaningless for this publish. Enumerated
            users in this build: NONE. Every production call site routes through
            `partition_key.partition_key_for_task`, which always yields a key for a real external
            task (the engine always assigns a `process_instance_id`), so no topic in this build
            has entity-free ordering semantics and no caller passes the flag. It exists so that a
            future caller with a genuinely unordered stream must WRITE THAT DOWN at the call site
            instead of silently passing `key=None` — the exact silence this gate removes.
          - `unordered=False` (the default) -> `MissingPartitionKeyError`. Fail-closed.
        """
        explicit = key.strip() if isinstance(key, str) else ""
        if explicit:
            return explicit
        derived = derive_partition_key(topic, value)
        if derived:
            logger.debug("kafka_partition_key_derived", topic=topic)
            return derived
        if unordered:
            logger.warning("kafka_publish_unordered", topic=topic)
            return None
        raise MissingPartitionKeyError(topic)

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
        unordered: bool = False,
    ) -> bool:
        """`KafkaPublisher.publish` — the ONE seam `events.py`/`lgpd.py`/`escalation.py` call.

        Publishes to `topic` (primary). If `topic` is in `MIRROR_TOPICS`, ALSO republishes a
        scrubbed envelope (`{**scrub_mirror_payload(value), "type": topic}`) onto
        `NOTIFICATIONS_TOPIC`.

        PARTITION KEY (GAP-SC-04-a): `key` is resolved by `resolve_partition_key` BEFORE any send
        is attempted, so a keyless publish raises `MissingPartitionKeyError` instead of reaching
        the broker round-robin. The raise happens OUTSIDE `_publish_one`'s try/except, so it is
        never swallowed by a best-effort posture nor mislabelled `kafka_publish_failed`: a missing
        key is bad input to this producer, not a broker fault. The mirror leg reuses the SAME
        resolved key, so a mirrored event and its primary share a partition-key value (they are
        different topics, so they are different partitions — what matters is that BOTH are keyed
        by the same entity, and that redeliveries of one entity keep landing together).

        Returns whether the PRIMARY publish was actually delivered (t2-notify-integrity — the
        false-success fix): `True` = the primary send reached the broker; `False` = the primary
        send FAILED but the failure was SWALLOWED here (best-effort posture). Before this
        return existed, a best-effort caller had NO in-band way to see the swallow —
        `events.py`'s `event_published: True` output variable LIED for a broker-down failure on
        a `BEST_EFFORT_TOPICS` publish (the one variable that exists to record whether the fact
        reached the wire). A non-best-effort failure never returns — it raises. The MIRROR
        leg's outcome never affects the return value (a mirror failure is log/ledger-only, by
        design — no BPMN anywhere models it).

        `best_effort` is the OPTIONAL per-call posture (t8-escalation-boundary — criticality is a
        property of the CALL, not the topic):
          - `None` (default): keep the TOPIC-based default (`topic in BEST_EFFORT_TOPICS`), so no
            existing caller changes behavior — a Kafka outage still must NOT newly stall
            CONTAS/FRAUDE/ANS-CRON at their unconditional, no-gateway `ST_Publish*` step.
          - `False`: FORCE propagate-on-failure regardless of topic. This is the ROOT-CAUSE fix
            for the escalation/lgpd notify swallow: those callers publish to `NOTIFICATIONS_TOPIC`
            (which is topic-default best-effort), but a lost grave-escalation team notice / LGPD
            Art. 19 legal-deadline notice must NOT be silently swallowed — it must reach the
            caller's modeled `ERR_ESC_NOTIFY_FAILED` boundary (escalation) or the harness
            retry/incident ladder (lgpd).
          - `True`: FORCE best-effort (swallow) regardless of topic.
        The MIRROR leg is ALWAYS best-effort (a mirror failure must never break a source BPMN —
        no BPMN boundary anywhere expects a "mirror publish failed" error).
        """
        # GAP-SC-04-a: resolve (and fail closed on) the partition key BEFORE the first send.
        resolved_key = self.resolve_partition_key(topic, value, key=key, unordered=unordered)
        primary_best_effort = best_effort if best_effort is not None else (topic in BEST_EFFORT_TOPICS)
        primary_delivered = await self._publish_one(
            topic, value, key=resolved_key, best_effort=primary_best_effort
        )

        if topic in MIRROR_TOPICS:
            # R1 F1: `type` is stamped LAST so the envelope's discriminator always wins — a
            # source payload carrying its own `type` key could otherwise shadow it via
            # dict-spread ordering (`{"type": topic, **scrubbed}` let the payload override).
            # Defense-in-depth: the scrub allowlist ALSO drops any source `type` key (it is not
            # allowlisted — see MIRROR_PAYLOAD_ALLOWLIST's note), so both layers must fail for a
            # foreign discriminator to reach the bridge.
            envelope = {**scrub_mirror_payload(value), "type": topic}
            # NOTIFICATIONS_TOPIC is always in BEST_EFFORT_TOPICS (module constant) — the mirror
            # leg is unconditionally best-effort regardless of the primary topic's own posture,
            # since no BPMN boundary event anywhere expects a "mirror publish failed" error.
            await self._publish_one(
                NOTIFICATIONS_TOPIC,
                envelope,
                key=resolved_key,
                best_effort=True,
                failure_event="kafka_mirror_publish_failed",
            )
        return primary_delivered

    async def _publish_one(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None,
        best_effort: bool,
        failure_event: str = "kafka_publish_failed",
    ) -> bool:
        """Single-leg send. `True` = delivered; `False` = failed-but-swallowed (best-effort);
        raises when `best_effort=False`."""
        try:
            producer = await asyncio.wait_for(self._ensure_started(), timeout=self._connect_timeout_s)
            await asyncio.wait_for(
                producer.send_and_wait(topic, _encode_json(value), key.encode("utf-8") if key else None),
                timeout=self._send_timeout_s,
            )
        except Exception as exc:  # fail-safe boundary; re-raise below when not best-effort
            logger.error(failure_event, topic=topic, key=key, error=str(exc))
            if not best_effort:
                raise
            self.failed_publishes.append((topic, repr(exc)))
            return False
        logger.info("kafka_published", topic=topic, key=key)
        return True

    async def close(self) -> None:
        """Best-effort shutdown — never raises (mirrors `AioKafkaBridgeConsumer.stop()`'s
        unconditional-cleanup posture in the daemon's own drain sequence)."""
        if self._raw is not None and self._started:
            with contextlib.suppress(Exception):
                await self._raw.stop()
