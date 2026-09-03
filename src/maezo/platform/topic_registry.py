"""TopicRegistry — Central Kafka topic registry with naming-convention validation.

Per ADR-0007 (dual engine+Kafka audit), all domain events are published to
Kafka topics following the convention: {dominio}.{contexto}.{acao}

Per ADR-0037 XRD-04 (AMH compatibility boundary), the pinned AMH boundary topics
(`config/integrations/amh/contracts.lock.json`) extend that convention with an explicit version
segment and, for the amh-to-maezo direction, a `.quarantine.v{N}` dead-letter suffix:
{dominio}.{contexto}.{acao}.v{N}[.quarantine.v{N}], where {acao} may be kebab-case (e.g.
`work-items`). This is a WIDER SHAPE of the same convention, not a per-name allowlist — it admits
any topic following that structure, not only the three currently pinned names, so a future
AMH-side version bump under the same shape does not require another registry change.

Per GAP-SC-04-a (audit D5), the INTERNAL dead-letter convention extends it once more with a
`.dlq` suffix: `{any topic valid on its own merits}.dlq`. This is the internal analogue of the AMH
boundary's own `.quarantine.v{N}` suffix above — the registry already admits a dead-letter shape
for the AMH direction, and the notifications-bridge's poison-message shunt needs the same for the
internal direction (`operadora.notifications.internal.dlq` is 4 segments and matched NEITHER
pre-existing pattern). It is deliberately NOT implemented as a reserved-prefix carve-out: a
`.dlq` name is validated by validating its BASE topic recursively, so a DLQ name can never be
more permissive than the traffic it quarantines, and `agents.events.*.dlq` still has to satisfy
the reserved-prefix rules. `.dlq.dlq` is rejected.

The TopicRegistry enforces:
- Topics must follow the 3-segment convention (or the special agents.audit topic), OR the
  versioned boundary-topic convention above, OR the `.dlq` suffix over any of them.
- No duplicate topic registrations (warns on overwrite).
- Topics registered here are referenced by the PEP (Policy Enforcement Point)
  and the audit gateway for dual-publishing.

London School TDD: the registry is pure in-memory state, injectable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Topic validation
# ---------------------------------------------------------------------------

# Convention: {dominio}.{contexto}.{acao}
_TOPIC_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# Versioned boundary-topic convention (ADR-0037 XRD-04): {dominio}.{contexto}.{acao}.v{N}, with an
# OPTIONAL ".quarantine.v{N}" dead-letter suffix. {dominio}/{contexto} keep the original
# [a-z][a-z0-9_]* shape (no hyphens — unchanged strictness); {acao} may be kebab-case (hyphen
# separated, no leading/trailing/doubled hyphen) to admit nouns like "work-items". This is the
# shape every pinned AMH boundary topic uses — see
# config/integrations/amh/contracts.lock.json:topics[].name/.quarantine and the lock-anchored proof
# in tests/unit/platform/test_topic_registry.py::TestAmhBoundaryTopicsFromLock.
_VERSIONED_BOUNDARY_TOPIC_PATTERN: re.Pattern[str] = re.compile(
    r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.v[0-9]+"
    r"(?:\.quarantine\.v[0-9]+)?$"
)

# Kafka's own hard limit on a topic name (org.apache.kafka.common.internals.Topic,
# TOPIC_MAX_NAME_LENGTH). Rejected outright, independent of the shape checks below.
_MAX_TOPIC_NAME_LENGTH = 249

#: Internal dead-letter suffix (GAP-SC-04-a). `{base}.dlq` is valid iff `{base}` is valid on its
#: own merits — see `_validate_topic_name`'s `.dlq` branch and `dlq_topic_for` below.
DLQ_SUFFIX: str = ".dlq"

# Reserved prefixes
_RESERVED_PREFIXES: frozenset[str] = frozenset({"agents.events", "agents.audit"})

# Special single-segment topics (allowed exceptions)
_SPECIAL_TOPICS: frozenset[str] = frozenset({"agents.audit"})


@dataclass
class TopicEntry:
    """A registered Kafka topic."""

    name: str
    dominio: str = ""
    contexto: str = ""
    acao: str = ""
    description: str = ""
    retention_hours: int = 72
    partitions: int = 3
    replication_factor: int = 3
    pii_zone: str = "zona_geral"  # zona_geral or zona_phi (ADR-0006)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TopicValidationError(ValueError):
    """Raised when a topic name does not conform to the naming convention."""

    def __init__(self, topic: str, detail: str = "") -> None:
        self.topic = topic
        msg = f"Invalid topic name '{topic}': {detail}" if detail else f"Invalid topic name '{topic}'"
        super().__init__(msg)


class TopicDuplicateError(ValueError):
    """Raised when attempting to register a duplicate topic with strict=True."""

    def __init__(self, topic: str) -> None:
        self.topic = topic
        super().__init__(f"Topic '{topic}' is already registered")


# ---------------------------------------------------------------------------
# TopicRegistry
# ---------------------------------------------------------------------------


class TopicRegistry:
    """Central registry of Kafka topics with naming-convention validation.

    Enforces:
    - Topics must follow {dominio}.{contexto}.{acao} (or be agents.audit).
    - Reserved prefixes (agents.events, agents.audit) must be used correctly.
    - OR the versioned boundary-topic convention (ADR-0037 XRD-04):
      {dominio}.{contexto}.{acao}.v{N}[.quarantine.v{N}], acao may be kebab-case.
    - PII zone awareness (zona_geral vs zona_phi, ADR-0006).

    Usage:
        registry = TopicRegistry()
        registry.register("contas.glosa.confirmed")
        registry.validate("contas.glosa.confirmed")  # True
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the topic registry.

        Args:
            strict: If True, raises TopicDuplicateError on duplicate registration.
                    If False (default), warns and overwrites.
        """
        self._topics: dict[str, TopicEntry] = {}
        self._strict = strict

    # -------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------

    def register(
        self,
        topic: str,
        description: str = "",
        retention_hours: int = 72,
        partitions: int = 3,
        replication_factor: int = 3,
        pii_zone: str = "zona_geral",
    ) -> TopicEntry:
        """Register a Kafka topic, validating the naming convention.

        Args:
            topic: The topic name (e.g., 'contas.glosa.confirmed').
            description: Human-readable description.
            retention_hours: Log retention in hours.
            partitions: Number of Kafka partitions.
            replication_factor: Kafka replication factor.
            pii_zone: Data zone (zona_geral or zona_phi, ADR-0006).

        Returns:
            The registered TopicEntry.

        Raises:
            TopicValidationError: If the topic name does not follow the convention.
            TopicDuplicateError: If strict=True and topic is already registered.
        """
        # Validate naming convention
        self._validate_topic_name(topic)

        # Parse segments (`.dlq`-aware — a DLQ entry records the FULL name in `entry.name` and
        # the BASE topic's own dominio/contexto/acao, so it groups with the traffic it quarantines
        # rather than parsing "dlq" as an acao segment).
        dominio, contexto, acao = TopicRegistry.parse(topic)

        entry = TopicEntry(
            name=topic,
            dominio=dominio,
            contexto=contexto,
            acao=acao,
            description=description,
            retention_hours=retention_hours,
            partitions=partitions,
            replication_factor=replication_factor,
            pii_zone=pii_zone,
        )

        if topic in self._topics:
            if self._strict:
                raise TopicDuplicateError(topic)
            logger.warning(
                "topic_registry.overwrite",
                topic=topic,
                previous=self._topics[topic].description,
                new=description,
            )

        self._topics[topic] = entry
        logger.debug("topic_registry.registered", topic=topic, dominio=dominio, contexto=contexto)
        return entry

    def register_batch(self, topics: list[str], **kwargs: Any) -> list[TopicEntry]:
        """Register multiple topics at once.

        Args:
            topics: List of topic names.
            **kwargs: Passed to register() for each topic.

        Returns:
            List of registered TopicEntry instances.
        """
        return [self.register(t, **kwargs) for t in topics]

    # -------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------

    @staticmethod
    def _validate_topic_name(topic: str) -> None:
        """Validate a topic name against the naming convention.

        Rules:
        - Must be a non-empty string, and at most _MAX_TOPIC_NAME_LENGTH characters (Kafka's own
          hard limit).
        - agents.audit is a special single-segment topic (allowed).
        - agents.events.* / agents.audit.* reserved-prefix topics (3- or 4-segment).
        - The generic {dominio}.{contexto}.{acao} convention: each segment must start with
          [a-z] and contain only [a-z0-9_].
        - OR the versioned boundary-topic convention (ADR-0037 XRD-04):
          {dominio}.{contexto}.{acao}.v{N}[.quarantine.v{N}], where {acao} may be kebab-case.
        """
        if not topic or not isinstance(topic, str):
            raise TopicValidationError(topic, "Topic name must be a non-empty string")

        if len(topic) > _MAX_TOPIC_NAME_LENGTH:
            raise TopicValidationError(
                topic, f"Topic name exceeds the {_MAX_TOPIC_NAME_LENGTH}-character Kafka limit"
            )

        # Special topics
        if topic in _SPECIAL_TOPICS:
            return

        # Internal dead-letter suffix (GAP-SC-04-a). Validated by DELEGATION to the base topic, so
        # a `.dlq` name is never more permissive than the traffic it quarantines — in particular
        # `agents.events.x.y.dlq` still has to clear the reserved-prefix rules below, and there is
        # no `.dlq` bypass for a name that would be rejected without the suffix. A doubled suffix
        # (`.dlq.dlq`) is refused outright: it would be a dead-letter of a dead-letter, which this
        # platform has no consumer for and which would hide an operator mistake behind a valid name.
        if topic.endswith(DLQ_SUFFIX):
            base = topic[: -len(DLQ_SUFFIX)]
            if not base or base.endswith(DLQ_SUFFIX):
                raise TopicValidationError(topic, f"'{DLQ_SUFFIX}' suffix requires a valid base topic")
            TopicRegistry._validate_topic_name(base)
            return

        # Reserved prefix check: agents.events.* and agents.audit.*
        for prefix in _RESERVED_PREFIXES:
            if topic.startswith(prefix) and topic != "agents.audit":
                # agents.events.X.Y or agents.audit.X.Y
                suffix = topic[len(prefix) + 1 :]  # skip the dot
                if suffix and _TOPIC_PATTERN.match(f"agents.{suffix}"):
                    # Valid: agents.events.contas.glosa or agents.audit.write
                    # Reconstruct and validate as 3-segment
                    parts = topic.split(".")
                    if len(parts) == 3 and all(_is_valid_segment(p) for p in parts):
                        return
                    elif (
                        len(parts) == 4
                        and parts[0] == "agents"
                        and parts[1] in ("events", "audit")
                        and all(_is_valid_segment(p) for p in parts)
                    ):
                        # agents.events.X.Y or agents.audit.X.Y
                        return
                    raise TopicValidationError(topic, f"Reserved prefix '{prefix}' used with invalid suffix")

        # Standard 3-segment convention, OR the versioned boundary-topic convention (ADR-0037 XRD-04).
        if _TOPIC_PATTERN.match(topic) or _VERSIONED_BOUNDARY_TOPIC_PATTERN.match(topic):
            return

        raise TopicValidationError(
            topic,
            "Topic must follow convention {dominio}.{contexto}.{acao} with segments matching "
            "[a-z][a-z0-9_]*, or the versioned boundary-topic convention "
            "{dominio}.{contexto}.{acao}.v{N}[.quarantine.v{N}] (acao may be kebab-case)",
        )

    @staticmethod
    def validate(topic: str) -> bool:
        """Validate a topic name without registering it.

        Args:
            topic: The topic name to validate.

        Returns:
            True if valid, False otherwise.
        """
        try:
            TopicRegistry._validate_topic_name(topic)
            return True
        except TopicValidationError:
            return False

    @staticmethod
    def parse(topic: str) -> tuple[str, str, str]:
        """Parse a topic name into (dominio, contexto, acao).

        Args:
            topic: A validated topic name.

        Returns:
            Tuple of (dominio, contexto, acao). For agents.audit, returns
            ('agents', 'audit', '').

        Raises:
            TopicValidationError: If the topic is invalid.
        """
        TopicRegistry._validate_topic_name(topic)
        if topic.endswith(DLQ_SUFFIX):
            # Base-relative on purpose (GAP-SC-04-a): a DLQ topic parses to the SAME
            # dominio/contexto/acao as the traffic it quarantines. `is_dlq` is the bit that tells
            # the two apart; duplicating the distinction inside `acao` would make every consumer
            # of `parse()` re-strip the suffix.
            return TopicRegistry.parse(topic[: -len(DLQ_SUFFIX)])
        if topic == "agents.audit":
            return ("agents", "audit", "")
        parts = topic.split(".")
        if len(parts) == 4 and parts[0] == "agents":
            # agents.events.X.Y or agents.audit.X.Y
            return (f"{parts[0]}.{parts[1]}", parts[2], parts[3])
        return (parts[0], parts[1], parts[2])

    @staticmethod
    def is_dlq_topic(topic: str) -> bool:
        """True iff `topic` is an internal dead-letter name (`{base}.dlq`, GAP-SC-04-a).

        Name-shape only — it does not check that the base is registered (a DLQ topic is
        legitimately created for traffic the registry has never been told about; the registry has
        no production registration call site today, `a2a/facts.py`'s own docstring).
        """
        return topic.endswith(DLQ_SUFFIX)

    def register_dlq(self, topic: str, **kwargs: Any) -> TopicEntry:
        """Register the dead-letter topic for `topic` (GAP-SC-04-a). Returns its `TopicEntry`.

        Honest registration, not a bypass: the name is derived by `dlq_topic_for` (which validates
        the BASE topic first) and then goes through the SAME `register` -> `_validate_topic_name`
        path every other topic goes through. `pii_zone` defaults to the registry's own
        `zona_geral` and should be passed explicitly as `zona_phi` for any base topic whose raw
        payload may carry PHI — a DLQ carries the payload VERBATIM, so it inherits the base
        topic's zone, never a laxer one.
        """
        return self.register(dlq_topic_for(topic), **kwargs)

    # -------------------------------------------------------------------
    # Lookup
    # -------------------------------------------------------------------

    def get(self, topic: str) -> TopicEntry | None:
        """Retrieve a registered topic entry.

        Args:
            topic: The topic name.

        Returns:
            TopicEntry if registered, None otherwise.
        """
        return self._topics.get(topic)

    def list_topics(self) -> list[str]:
        """Return all registered topic names."""
        return list(self._topics.keys())

    def list_entries(self) -> list[TopicEntry]:
        """Return all registered TopicEntry instances."""
        return list(self._topics.values())

    def count(self) -> int:
        """Return the number of registered topics."""
        return len(self._topics)

    def clear(self) -> None:
        """Remove all registered topics (useful for testing)."""
        self._topics.clear()
        logger.debug("topic_registry.cleared")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_segment(segment: str) -> bool:
    """Check if a single topic segment is valid: [a-z][a-z0-9_]*."""
    return bool(re.match(r"^[a-z][a-z0-9_]*$", segment))


def dlq_topic_for(topic: str) -> str:
    """`{topic}.dlq` — the ONE sanctioned derivation of an internal dead-letter topic name.

    Validates `topic` on its OWN merits first, so a malformed source topic can never acquire a
    valid-looking DLQ name (the failure mode a bare f-string at the call site would allow), and
    refuses a topic that is already a DLQ (no `.dlq.dlq`).

    Called on the notifications-bridge's poison-message path
    (`platform/integrations/notifications_bridge.py`), so the registry's convention is enforced on
    the hot path rather than only at a registration site the platform does not have yet.

    Raises:
        TopicValidationError: if `topic` is not a valid topic name, or is already a `.dlq` name.
    """
    if topic.endswith(DLQ_SUFFIX):
        raise TopicValidationError(topic, "already a dead-letter topic — refusing to nest '.dlq'")
    TopicRegistry._validate_topic_name(topic)
    return f"{topic}{DLQ_SUFFIX}"
