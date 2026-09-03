"""Deterministic, PHI-free Kafka PARTITION-KEY derivation (GAP-SC-04-a).

THE DEFECT THIS CLOSES (audit D5, gateway gvr-d05). `AioKafkaEventsProducer.publish(topic, value,
*, key=None)` accepted `key=None` as a silent default, and every worker call site spelled the key
`key=task.business_key or None`. A blank `business_key` therefore degraded — silently — into "no
partition key at all". With the registry's default `partitions=3`
(`maezo.platform.topic_registry.TopicEntry.partitions`, `topic_registry.py:75`; the same default
`TopicRegistry.register`'s signature carries at `:142`) a keyless record is assigned round-robin,
so two events about the SAME beneficiary/process can land on DIFFERENT partitions. A single-replica
consumer hides that (one reader drains every partition in arrival order per partition, and there is
only one reader), which is exactly why the defect is latent today and would surface the moment the
notifications-bridge is scaled past one replica.

THE FIX IS A CHOKEPOINT, NOT A CONVENTION. `publish()` now REFUSES a keyless publish
(`MissingPartitionKeyError`) unless the caller states `unordered=True`, and derives a key from the
payload when the caller has none. This module owns that derivation: ONE function, table-driven,
deterministic, and PHI-free by construction.

## The derivation chain (in order; first hit wins)

1. **The caller's explicit key.** Every existing call site passes `task.business_key` when it has
   one, so this arm reproduces today's behaviour byte-for-byte for every non-degenerate publish.
2. **`_business_key` from the payload.** The generic `operadora.events.publish` handler
   (`maezo.tools.workers.events.make_publish_event_handler`) stamps it on EVERY payload it builds,
   so every `agents.events.*` domain event carries the source process instance's own identity.
   This is the strongest available per-entity anchor: the engine business key is precisely what
   `start_process_idempotent` dedups on and what each SP-OP-* contract declares as "one active
   instance per business key". It is routed through `egress_message_key`, so the DL-0043
   `scrub_only` policy (once ratified) governs this key exactly as it already governs the one
   `events.py` passes explicitly — this module introduces no second, unpoliced egress of a
   business key.
3. **The per-family ENTITY ANCHORS below.** Reached only when a payload carries no business key at
   all — in practice the worker NOTIFICATION dicts published straight to
   `operadora.notifications.internal` (`recurso`/`lgpd`/`programa`/`adequacao`/`ans_submit`/
   `escalation`), which are built field-by-field and do not carry `_business_key`.
4. **`{tenant_id}|{_process_instance_id}`.** The universal fallback: every event of one process
   instance shares a partition. `events.py` always stamps `_process_instance_id`, and
   `partition_key_for_task` (below) supplies the same pair from the `ExternalTask` itself.
5. **Nothing** -> `None`. The producer then FAILS CLOSED. Absence of a key is never silently
   accepted.

## PHI discipline (enforced, not asserted)

Every anchor field name is checked against `maezo.tools.workers.phi_vars.PHI_PROCESS_VARS` at
IMPORT time (`_assert_anchors_phi_free`) and again by `anchor_fields()`'s public contract, so a
future editor cannot add `matricula_beneficiario` (or any other PHI-named variable) to the table
without the module refusing to import. A Kafka message key is worse than a payload field for PHI:
it rides the broker's own partition metadata, is visible to every consumer group, and is retained
independently of the payload. `beneficiario_pseudo_id` is the safe pseudonym convention and is the
ONLY beneficiary-shaped name this table may ever carry.

`_business_key` is deliberately NOT an anchor field name in the table: it is handled by arm (2)
above, under the DL-0043 policy, precisely because the CANCEL/INAD families can mint it FROM
`matricula_beneficiario` (`maezo.platform.privacy.phi_key_policy`'s own module docstring).

## Why ONE anchor group per family, never a fallback chain within a family

A per-event fallback (try `numero_guia_tiss`, else `numero_lote_tiss`) would put the SAME entity on
two different partitions whenever one event happened to omit the richer anchor — re-creating, one
level down, exactly the reordering this module exists to prevent. So each family declares exactly
one group; a payload missing any field of that group falls through to arm (4), which is stable for
the whole process instance.

Every group below is copied from an EXISTING business-key derivation in this repo (cited inline),
never invented. Families with no verified anchor set are deliberately ABSENT from the table rather
than given a guessed one: they resolve through arms (2)/(4), which are always available.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# MODULE-SCOPE deliberately, inheriting `events.py`'s own rationale for the same import: the
# derivation below calls `egress_message_key` from OUTSIDE every publish-`try` in the codebase, so
# an ImportError must surface at import time as an import failure — never as a mid-dispatch
# exception a publish error path could re-label `event_publish_failed`. Neither module imports
# anything under `maezo.platform.integrations`, so there is no cycle: `key_scrubber` reaches only
# `gateway.{log_scrubber,pseudonymizer}` (and defers `phi_key_policy` lazily inside its own
# function bodies), and `phi_vars` is stdlib-only by design.
from maezo.platform.privacy.key_scrubber import egress_message_key
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

#: The bridge's single input topic — duplicated as a plain literal (same "no import dependency on
#: the consumer module" rationale `events_kafka_producer.NOTIFICATIONS_TOPIC` documents; the three
#: copies are proven equal by `tests/integration/platform/test_events_kafka_producer_live.py`).
NOTIFICATIONS_TOPIC: Final[str] = "operadora.notifications.internal"

#: Separator between the tenant segment and the entity segment(s) of a derived key. A pipe cannot
#: appear in a topic segment, a tenant id (`[a-z][a-z0-9]*` per the live-test fixtures) or any
#: engine process-instance UUID, so a derived key never collides with a business key
#: (`{FAMILY}-{tenant}-{anchor}`, hyphen-separated) minted anywhere else in the platform.
KEY_SEPARATOR: Final[str] = "|"

#: Family token used when a topic matches no known shape (arms (2)/(4) still apply).
_UNKNOWN_FAMILY: Final[str] = ""

#: Family token for `operadora.notifications.internal`.
NOTIFICATIONS_FAMILY: Final[str] = "notifications"

#: Per-family business-entity anchors — ONE group per family (module docstring: never a chain).
#: Each group is the anchor set of that family's OWN business key, cited to the deriving function.
#: `tenant_id` is prepended structurally by `derive_partition_key` and is therefore NOT repeated in
#: any group (mirrors `notification_bridge._anchored`'s "tenant required structurally" rule, which
#: exists so no future rule can forget it).
ENTITY_ANCHORS: Final[Mapping[str, tuple[str, ...]]] = {
    # notification_bridge._recurso_business_key: RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}
    "contas": ("numero_guia_tiss", "glosa_id"),
    "recurso": ("numero_guia_tiss", "glosa_id"),
    # notification_bridge._fraude_business_key: FRAUDE-{tenant}-{numero_caso}
    "fraude": ("numero_caso",),
    # notification_bridge._cred_business_key: CRED-{tenant}-{prestador_id}
    "cred": ("prestador_id",),
    # notification_bridge._cancel_business_key: CANCEL-{tenant}-{numero_contrato}
    "cancel": ("numero_contrato",),
    # notification_bridge._inadimplencia_business_key: INAD-{tenant}-{numero_contrato}
    "inadimplencia": ("numero_contrato",),
    # notification_bridge._ans_cron_business_key: ANSSUB-{tenant}-{report_type}-{competencia}
    "anssubmit": ("report_type", "competencia"),
}

#: Payload field carrying the source process instance's engine business key (stamped by
#: `events.py::make_publish_event_handler` on every payload it builds).
_BUSINESS_KEY_FIELD: Final[str] = "_business_key"

#: Payload field carrying the source engine process-instance id (same stamper).
_PROCESS_INSTANCE_FIELD: Final[str] = "_process_instance_id"

#: Payload field carrying the tenant. `events.py` stamps it (`setdefault`, deployment identity) and
#: every worker notification dict sets it explicitly.
_TENANT_FIELD: Final[str] = "tenant_id"


class MissingPartitionKeyError(ValueError):
    """No partition key could be derived and the caller did not declare `unordered=True`.

    FAIL-CLOSED, and deliberately a `ValueError`: the worker harness's own ladder routes a
    `ValueError` to `failure(retries=0)` — a loud, operator-visible incident — WITHOUT an
    uncatalogued `bpmnError` code (the same reasoning `events.py` records for its missing-
    `event_topic` raise, ADR-0030 §2 clause-(b)). A keyless publish is bad INPUT to the producer,
    not a modeled business outcome: there is no `bpmn:error` boundary anywhere in `spec/**` for
    "the event had no partition key", and inventing one would be inventing a governance record.
    """

    def __init__(self, topic: str) -> None:
        self.topic = topic
        super().__init__(
            f"partition_key: no key derivable for topic '{topic}' and the caller did not pass "
            "unordered=True — refusing to publish unkeyed (per-entity ordering would be lost "
            "across the topic's partitions)"
        )


def _assert_anchors_phi_free() -> None:
    """Refuse to import if any anchor field name is a `PHI_PROCESS_VARS` name.

    Import-time rather than call-time on purpose: a PHI-named partition key is not a condition to
    detect in production, it is a condition to make unreachable. The equivalent runtime check
    (`derive_partition_key` consulting the table) would only fire on the one message that already
    leaked. `tests/unit/platform/integrations/test_partition_key.py` pins the same invariant with
    an explicit attempt to register a PHI name.
    """
    offenders = sorted({field for group in ENTITY_ANCHORS.values() for field in group} & PHI_PROCESS_VARS)
    if offenders:
        raise RuntimeError(
            "partition_key: PHI-named fields in ENTITY_ANCHORS — a Kafka message key rides the "
            f"broker's partition metadata and must never carry PHI (ADR-0006): {offenders}"
        )


_assert_anchors_phi_free()


def anchor_fields() -> frozenset[str]:
    """Every field name any family's anchor group reads. PHI-free by `_assert_anchors_phi_free`."""
    return frozenset(field for group in ENTITY_ANCHORS.values() for field in group)


def topic_family(topic: str) -> str:
    """Resolve a topic to its `ENTITY_ANCHORS` family token.

    - `operadora.notifications.internal` -> `NOTIFICATIONS_FAMILY` (the bridge's `type`-
      discriminated channel; it declares no anchor group of its own — a notification's entity is
      whatever the source worker put in it, resolved by arms (2)/(4)).
    - `agents.events.{dominio}.{acao}` -> `{dominio}` (the 4-segment reserved-prefix shape every
      BPMN `event_topic` literal uses: `agents.events.contas.completed`, ...).
    - anything else (including the 3-segment `agents.events.process_completed`) -> `""`.
    """
    if topic == NOTIFICATIONS_TOPIC:
        return NOTIFICATIONS_FAMILY
    parts = topic.split(".")
    if len(parts) == 4 and parts[0] == "agents" and parts[1] == "events":
        return parts[2]
    return _UNKNOWN_FAMILY


def _non_blank(value: Any) -> str:
    """`str(value)` when `value` is a present, non-blank scalar; `""` otherwise.

    Mirrors `notification_bridge._non_blank`'s fail-closed posture, including its reason for
    existing: the naive `str(value).strip()` idiom turns an explicit `None` into the 4-character
    string `"None"`, which would mint a key like `amh|None` — a degenerate key that looks usable
    and silently co-locates every tenant-less event on one partition.
    """
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return text if text else ""


def derive_partition_key(topic: str, payload: Mapping[str, Any]) -> str | None:
    """Derive the partition key for `payload` on `topic`. `None` when nothing is derivable.

    The chain is arms (2) -> (3) -> (4) of the module docstring (arm (1), the caller's explicit
    key, is applied by the producer BEFORE calling this). Pure and deterministic: the same payload
    always yields the same key, so two events about one entity always share a partition.

    NEVER raises for a malformed payload — an undeliverable derivation returns `None` and the
    producer's own fail-closed check decides what that means for the caller.
    """
    business_key = _non_blank(payload.get(_BUSINESS_KEY_FIELD))
    if business_key:
        # DL-0043 leg (c): the SAME policy gate `events.py` applies to the key it passes
        # explicitly. Under the shipped (`off`) policy this returns the key unchanged.
        scrubbed = egress_message_key(business_key)
        return _non_blank(scrubbed) or None

    tenant = _non_blank(payload.get(_TENANT_FIELD))
    anchors = ENTITY_ANCHORS.get(topic_family(topic), ())
    if tenant and anchors:
        values = [_non_blank(payload.get(field)) for field in anchors]
        if all(values):
            return KEY_SEPARATOR.join([tenant, *values])

    process_instance_id = _non_blank(payload.get(_PROCESS_INSTANCE_FIELD))
    if tenant and process_instance_id:
        return f"{tenant}{KEY_SEPARATOR}{process_instance_id}"
    if process_instance_id:
        # Tenant-less but instance-identified: still strictly better than round-robin (all events
        # of one instance co-locate). Not silently "good" — the producer logs the derivation arm.
        return process_instance_id
    return None


def partition_key_for_task(
    task: Any, topic: str = "", payload: Mapping[str, Any] | None = None
) -> str | None:
    """The partition key for a publish made FROM an external task. The worker call sites' seam.

    `task` is a `maezo.tools.workers.harness.ExternalTask` (typed `Any` so this platform module
    takes no import dependency on the worker harness — the same one-way-dependency discipline
    `key_scrubber` keeps for `tools.workers`). `topic` is the PUBLISH TARGET topic (the family
    resolver's input), never `task.topic` — the worker topic (`operadora.events.publish`) says
    nothing about which domain family the event belongs to.

    Chain: the task's own `business_key` (policy-gated by `egress_message_key`, so CANCEL/INAD keys
    are pseudonymized once DL-0043 `scrub_only` is ratified) -> `derive_partition_key` over the
    payload -> `{tenant}|{process_instance_id}` from the task itself. Returns `None` only when the
    task carries no business key, no derivable payload anchor AND no process-instance id — which a
    real external task never does (the engine always assigns one), so in production this never
    returns `None`; the possibility is kept honest rather than asserted away.

    CALL IT OUTSIDE THE PUBLISH `try`. Under a ratified `scrub_only` with no `PHI_HMAC_KEY`
    provisioned, `egress_message_key` raises `PseudonymizerKeyMissingError` — a CONFIGURATION
    fault, not a broker fault. `events.py:326-344` documents at length why that raise must not be
    caught by a publish-`try` and re-labelled `event_publish_failed`; every call site added by
    GAP-SC-04-a keeps the same hoist.
    """
    business_key = _non_blank(getattr(task, "business_key", None))
    if business_key:
        scrubbed = _non_blank(egress_message_key(business_key))
        if scrubbed:
            return scrubbed

    if payload is not None:
        derived = derive_partition_key(topic, payload)
        if derived:
            return derived

    variables = getattr(task, "variables", None)
    tenant = _non_blank(variables.get(_TENANT_FIELD)) if isinstance(variables, Mapping) else ""
    if payload is not None and not tenant:
        tenant = _non_blank(payload.get(_TENANT_FIELD))
    process_instance_id = _non_blank(getattr(task, "process_instance_id", None))
    if not process_instance_id:
        return None
    return f"{tenant}{KEY_SEPARATOR}{process_instance_id}" if tenant else process_instance_id
