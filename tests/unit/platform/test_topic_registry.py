"""Unit tests for maezo.platform.topic_registry — Kafka topic naming convention.

TDD London School: tests validate the {dominio}.{contexto}.{acao} convention
and the special agents.audit topic.
"""

import pytest

from maezo.platform.topic_registry import (
    TopicDuplicateError,
    TopicRegistry,
    TopicValidationError,
)

# ---------------------------------------------------------------------------
# TopicRegistry.validate (static)
# ---------------------------------------------------------------------------


class TestValidateTopic:
    """Topic name validation without registration."""

    def test_valid_3_segment_topic(self) -> None:
        assert TopicRegistry.validate("contas.glosa.confirmed") is True

    def test_valid_agents_audit_topic(self) -> None:
        assert TopicRegistry.validate("agents.audit") is True

    def test_valid_agents_events_topic(self) -> None:
        assert TopicRegistry.validate("agents.events.contas") is True

    def test_valid_with_underscores(self) -> None:
        assert TopicRegistry.validate("contas.glosa_medica.confirmed") is True

    def test_invalid_empty(self) -> None:
        assert TopicRegistry.validate("") is False

    def test_invalid_single_segment(self) -> None:
        assert TopicRegistry.validate("contas") is False

    def test_invalid_two_segments(self) -> None:
        assert TopicRegistry.validate("contas.glosa") is False

    def test_invalid_starts_with_number(self) -> None:
        assert TopicRegistry.validate("1contas.glosa.confirmed") is False

    def test_invalid_uppercase(self) -> None:
        assert TopicRegistry.validate("CONTAS.glosa.confirmed") is False

    def test_invalid_special_chars(self) -> None:
        assert TopicRegistry.validate("contas.glosa.confirmed!") is False

    def test_invalid_hyphens(self) -> None:
        assert TopicRegistry.validate("contas.glosa-medica.confirmed") is False


# ---------------------------------------------------------------------------
# TopicRegistry.parse (static)
# ---------------------------------------------------------------------------


class TestParseTopic:
    """Parsing topic names into segments."""

    def test_parse_standard_3_segment(self) -> None:
        dominio, contexto, acao = TopicRegistry.parse("contas.glosa.confirmed")
        assert dominio == "contas"
        assert contexto == "glosa"
        assert acao == "confirmed"

    def test_parse_agents_audit(self) -> None:
        dominio, contexto, acao = TopicRegistry.parse("agents.audit")
        assert dominio == "agents"
        assert contexto == "audit"
        assert acao == ""

    def test_parse_invalid_raises(self) -> None:
        with pytest.raises(TopicValidationError):
            TopicRegistry.parse("invalid")


# ---------------------------------------------------------------------------
# TopicRegistry.register
# ---------------------------------------------------------------------------


class TestRegister:
    """Registration with naming validation."""

    def test_register_valid_topic(self) -> None:
        registry = TopicRegistry()
        entry = registry.register("contas.glosa.confirmed", description="Glosa confirmada")
        assert entry.name == "contas.glosa.confirmed"
        assert entry.dominio == "contas"
        assert entry.contexto == "glosa"
        assert entry.acao == "confirmed"
        assert entry.description == "Glosa confirmada"
        assert registry.count() == 1

    def test_register_agents_audit(self) -> None:
        registry = TopicRegistry()
        entry = registry.register("agents.audit", description="Audit trail")
        assert entry.name == "agents.audit"
        assert entry.dominio == "agents"
        assert entry.contexto == "audit"
        assert entry.acao == ""

    def test_register_agents_events_topic(self) -> None:
        registry = TopicRegistry()
        entry = registry.register("agents.events.contas", description="Contas events")
        assert entry.name == "agents.events.contas"
        assert registry.count() == 1

    def test_register_invalid_raises(self) -> None:
        registry = TopicRegistry()
        with pytest.raises(TopicValidationError, match="Invalid topic name"):
            registry.register("INVALID")

    def test_register_duplicate_warns_not_raises(self) -> None:
        registry = TopicRegistry(strict=False)
        registry.register("contas.glosa.confirmed", description="First")
        # Should not raise, just warn
        entry = registry.register("contas.glosa.confirmed", description="Second")
        assert entry.description == "Second"
        assert registry.count() == 1  # Overwrites, not duplicates

    def test_register_duplicate_strict_raises(self) -> None:
        registry = TopicRegistry(strict=True)
        registry.register("contas.glosa.confirmed")
        with pytest.raises(TopicDuplicateError, match="already registered"):
            registry.register("contas.glosa.confirmed")

    def test_register_with_custom_retention(self) -> None:
        registry = TopicRegistry()
        entry = registry.register(
            "contas.glosa.confirmed",
            retention_hours=168,
            partitions=6,
            replication_factor=2,
            pii_zone="zona_phi",
        )
        assert entry.retention_hours == 168
        assert entry.partitions == 6
        assert entry.replication_factor == 2
        assert entry.pii_zone == "zona_phi"

    def test_register_defaults(self) -> None:
        registry = TopicRegistry()
        entry = registry.register("contas.glosa.confirmed")
        assert entry.retention_hours == 72
        assert entry.partitions == 3
        assert entry.replication_factor == 3
        assert entry.pii_zone == "zona_geral"


# ---------------------------------------------------------------------------
# TopicRegistry.register_batch
# ---------------------------------------------------------------------------


class TestRegisterBatch:
    """Batch registration."""

    def test_register_batch_valid_topics(self) -> None:
        registry = TopicRegistry()
        entries = registry.register_batch(
            [
                "contas.glosa.confirmed",
                "recurso.validacao.completed",
                "fraude.caso.registrado",
            ]
        )
        assert len(entries) == 3
        assert registry.count() == 3

    def test_register_batch_rejects_invalid(self) -> None:
        registry = TopicRegistry()
        with pytest.raises(TopicValidationError):
            registry.register_batch(["contas.glosa.confirmed", "bad"])


# ---------------------------------------------------------------------------
# TopicRegistry lookup
# ---------------------------------------------------------------------------


class TestLookup:
    """Retrieval of registered topics."""

    def test_get_existing_topic(self) -> None:
        registry = TopicRegistry()
        registry.register("contas.glosa.confirmed", description="Glosa")
        entry = registry.get("contas.glosa.confirmed")
        assert entry is not None
        assert entry.description == "Glosa"

    def test_get_nonexistent_topic(self) -> None:
        registry = TopicRegistry()
        assert registry.get("nonexistent.topic.here") is None

    def test_list_topics(self) -> None:
        registry = TopicRegistry()
        registry.register("contas.glosa.confirmed")
        registry.register("recurso.validacao.completed")
        topics = registry.list_topics()
        assert len(topics) == 2
        assert "contas.glosa.confirmed" in topics
        assert "recurso.validacao.completed" in topics

    def test_list_entries(self) -> None:
        registry = TopicRegistry()
        registry.register("contas.glosa.confirmed", description="A")
        registry.register("recurso.validacao.completed", description="B")
        entries = registry.list_entries()
        assert len(entries) == 2
        descriptions = {e.description for e in entries}
        assert descriptions == {"A", "B"}

    def test_count(self) -> None:
        registry = TopicRegistry()
        assert registry.count() == 0
        registry.register("contas.glosa.confirmed")
        assert registry.count() == 1
        registry.register("recurso.validacao.completed")
        assert registry.count() == 2

    def test_clear(self) -> None:
        registry = TopicRegistry()
        registry.register("contas.glosa.confirmed")
        registry.register("recurso.validacao.completed")
        assert registry.count() == 2
        registry.clear()
        assert registry.count() == 0


# ---------------------------------------------------------------------------
# Topic naming convention edge cases
# ---------------------------------------------------------------------------


class TestNamingConvention:
    """Edge cases for the {dominio}.{contexto}.{acao} convention."""

    def test_long_segments(self) -> None:
        assert TopicRegistry.validate("contas_medicas.glosa_tecnica.processado") is True

    def test_numeric_in_segments_ok(self) -> None:
        assert TopicRegistry.validate("contas.glosa2.confirmed") is True
        assert TopicRegistry.validate("contas2.glosa.confirmed") is True

    def test_single_char_segments(self) -> None:
        assert TopicRegistry.validate("a.b.c") is True

    def test_reserved_prefix_agents_events_standard(self) -> None:
        """agents.events.X.Y matches the reserved prefix convention."""
        assert TopicRegistry.validate("agents.events.contas") is True

    def test_reserved_prefix_agents_audit_four_segment(self) -> None:
        """agents.audit.write.metadata is valid (4-segment reserved)."""
        assert TopicRegistry.validate("agents.audit.write") is True

    def test_non_string_topic(self) -> None:
        assert TopicRegistry.validate(123) is False  # type: ignore[arg-type]
