"""TopicRegistry — Central Kafka topic registry with naming-convention validation.

Per ADR-0007 (dual engine+Kafka audit), all domain events are published to
Kafka topics following the convention: {dominio}.{contexto}.{acao}

The TopicRegistry enforces:
- Topics must follow the 3-segment convention (or the special agents.audit topic).
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

        # Parse segments
        parts = topic.split(".")
        if topic == "agents.audit":
            dominio, contexto, acao = "agents", "audit", ""
        else:
            dominio, contexto, acao = parts[0], parts[1], parts[2]

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
        - Must be a non-empty string.
        - agents.audit is a special single-segment topic (allowed).
        - All other topics must match {dominio}.{contexto}.{acao}.
        - Each segment must start with [a-z] and contain only [a-z0-9_].
        """
        if not topic or not isinstance(topic, str):
            raise TopicValidationError(topic, "Topic name must be a non-empty string")

        # Special topics
        if topic in _SPECIAL_TOPICS:
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

        # Standard 3-segment convention
        if not _TOPIC_PATTERN.match(topic):
            raise TopicValidationError(
                topic,
                "Topic must follow convention {dominio}.{contexto}.{acao} "
                "with segments matching [a-z][a-z0-9_]*",
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
        if topic == "agents.audit":
            return ("agents", "audit", "")
        parts = topic.split(".")
        if len(parts) == 4 and parts[0] == "agents":
            # agents.events.X.Y or agents.audit.X.Y
            return (f"{parts[0]}.{parts[1]}", parts[2], parts[3])
        return (parts[0], parts[1], parts[2])

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
