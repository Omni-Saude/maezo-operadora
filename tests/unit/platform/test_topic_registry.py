"""Unit tests for maezo.platform.topic_registry — Kafka topic naming convention.

TDD London School: tests validate the {dominio}.{contexto}.{acao} convention
and the special agents.audit topic.

The `TestAmhBoundaryTopicsFromLock` class anchors to the immutable AMH contract pin
(`config/integrations/amh/contracts.lock.json`, ADR-0037 XRD-04) rather than to a literal string
typed in this file — mirroring `tests/unit/ports/test_envelope_pin.py` and
`tests/contract/amh/test_contract_pin.py` — so the registry can never silently drift away from the
pinned set of boundary topic names.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.topic_registry import (
    DLQ_SUFFIX,
    TopicDuplicateError,
    TopicRegistry,
    TopicValidationError,
    dlq_topic_for,
)

# tests/unit/platform/<this file> -> parents[3] is the repository root.
_LOCK_PATH = Path(__file__).resolve().parents[3] / "config" / "integrations" / "amh" / "contracts.lock.json"


def _lock() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
    return payload


def _pinned_topic_names() -> list[str]:
    """Every pinned AMH boundary topic name AND its quarantine counterpart, read from the lock."""
    lock = _lock()
    names: list[str] = []
    for entry in lock["topics"]:
        names.append(entry["name"])
        names.append(entry["quarantine"])
    return names


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

    # -- AMH-shape widening: malformed variants that must still be REJECTED --------------------

    def test_invalid_hyphenated_segment_without_version_suffix(self) -> None:
        """4 segments with a kebab acao but NO trailing v{N} — not the versioned shape."""
        assert TopicRegistry.validate("amh.maezo.work-items.beta") is False

    def test_invalid_uppercase_amh_shaped_topic(self) -> None:
        assert TopicRegistry.validate("AMH.MAEZO.WORK-ITEMS.V1") is False

    def test_invalid_leading_hyphen_in_kebab_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.-work-items.v1") is False

    def test_invalid_trailing_hyphen_in_kebab_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work-items-.v1") is False

    def test_invalid_doubled_hyphen_in_kebab_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work--items.v1") is False

    def test_invalid_leading_dot(self) -> None:
        assert TopicRegistry.validate(".amh.maezo.work-items.v1") is False

    def test_invalid_trailing_dot(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work-items.v1.") is False

    def test_invalid_whitespace_in_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work items.v1") is False

    def test_invalid_control_character_in_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work\titems.v1") is False

    def test_invalid_version_segment_not_numeric(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work-items.vX") is False

    def test_invalid_pure_punctuation_segment(self) -> None:
        assert TopicRegistry.validate("amh.maezo.---.v1") is False

    def test_invalid_absurdly_long_topic_name(self) -> None:
        absurd = "amh.maezo." + ("a" * 260) + ".v1"
        assert TopicRegistry.validate(absurd) is False

    def test_invalid_quarantine_missing_base_version(self) -> None:
        """The malformed quarantine shape guarded against in the AMH contract-pin tests."""
        assert TopicRegistry.validate("amh.maezo.work-items.quarantine.v1") is False


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

    def test_pre_existing_operadora_style_topics_still_validate(self) -> None:
        """Regression: the pre-existing 3-segment `operadora.*` vocabulary (src/maezo/tools/workers/
        auth.py, contas.py, inadimplencia.py, ans_submit.py) must remain valid after the AMH-shape
        widening — none of these are 4-segment or kebab-case."""
        for topic in (
            "operadora.auth.analyze_request",
            "operadora.auth.request_documents",
            "operadora.auth.issue_authorization",
            "operadora.contas.analyze_reason",
            "operadora.contas.publish",
            "operadora.events.publish",
            "operadora.notifications.internal",
            "operadora.inadimplencia.assess_status",
            "operadora.escalation.notify_team",
        ):
            assert TopicRegistry.validate(topic) is True, topic


# ---------------------------------------------------------------------------
# AMH boundary topics — lock-anchored (ADR-0037 XRD-04, MZO-050-prep)
# ---------------------------------------------------------------------------


class TestAmhBoundaryTopicsFromLock:
    """Anchors to `config/integrations/amh/contracts.lock.json`, NOT to a literal string typed in
    this file (see module docstring). If the pin ever adds/renames a topic, this test reads the
    NEW pinned names automatically — a silent drift between the registry's accepted shape and the
    immutable AMH contract pin is impossible to introduce without this test catching it.
    """

    def test_lock_file_is_present_and_readable(self) -> None:
        """Non-vacuity guard: every assertion below is meaningless if the pin cannot be read."""
        assert _LOCK_PATH.is_file(), f"contract pin not found at {_LOCK_PATH}"
        topics = _lock()["topics"]
        assert isinstance(topics, list) and topics, "the pin's topics list must be a non-empty list"

    def test_pin_declares_exactly_three_topics(self) -> None:
        """Non-vacuity: prove there are 3 base topics (hence 6 names) before asserting over them."""
        assert len(_lock()["topics"]) == 3

    def test_every_pinned_topic_name_validates(self) -> None:
        lock = _lock()
        for entry in lock["topics"]:
            name = entry["name"]
            assert TopicRegistry.validate(name) is True, f"pinned topic name rejected: {name}"

    def test_every_pinned_quarantine_name_validates(self) -> None:
        lock = _lock()
        for entry in lock["topics"]:
            quarantine = entry["quarantine"]
            assert TopicRegistry.validate(quarantine) is True, (
                f"pinned quarantine topic name rejected: {quarantine}"
            )

    def test_all_six_pinned_names_validate(self) -> None:
        """Belt-and-suspenders: the flat de-duplicated list of base + quarantine names, all valid."""
        names = _pinned_topic_names()
        assert len(names) == 6
        assert len(set(names)) == 6  # no accidental duplicate between base/quarantine sets
        for name in names:
            assert TopicRegistry.validate(name) is True, name

    def test_every_pinned_topic_name_is_registrable(self) -> None:
        """Not just `validate()` — `register()` must actually accept each pinned name without
        raising, since a registry that only "validates" but can't register a topic is not
        "capable of naming" it."""
        registry = TopicRegistry(strict=True)
        for name in _pinned_topic_names():
            entry = registry.register(name, description="AMH boundary topic (ADR-0037 XRD-04)")
            assert entry.name == name
        assert registry.count() == 6

    def test_pinned_topic_names_parse_dominio_and_contexto_correctly(self) -> None:
        """dominio/contexto (the first two segments) must survive parsing even though `parse()`'s
        3-tuple return type predates the versioned/quarantine shape and does not carry the
        trailing version/quarantine segments (same pre-existing simplification already applied to
        the 4-segment `agents.events.X.Y` reserved-prefix shape)."""
        for entry in _lock()["topics"]:
            name = entry["name"]
            dominio, contexto, _acao = TopicRegistry.parse(name)
            expected_dominio, expected_contexto = name.split(".")[:2]
            assert (dominio, contexto) == (expected_dominio, expected_contexto), name


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the internal `.dlq` dead-letter convention (audit D5 / gvr-d05).
#
# The notifications-bridge's poison-message shunt needs `operadora.notifications.internal.dlq`,
# a 4-segment name that matched NEITHER pre-existing pattern. It is admitted by DELEGATION to the
# base topic, never by a reserved-prefix carve-out — so a DLQ name can never be more permissive
# than the traffic it quarantines. These tests pin exactly that asymmetry.
# ---------------------------------------------------------------------------


class TestInternalDlqTopicConvention:
    """The `.dlq` suffix: admitted honestly, never as a bypass."""

    def test_bridge_dlq_topic_is_valid(self) -> None:
        assert TopicRegistry.validate("operadora.notifications.internal.dlq") is True

    def test_dlq_over_a_reserved_prefix_topic_is_valid(self) -> None:
        assert TopicRegistry.validate("agents.events.contas.completed.dlq") is True

    def test_dlq_over_a_versioned_boundary_topic_is_valid(self) -> None:
        assert TopicRegistry.validate("amh.maezo.work-items.v1.dlq") is True

    def test_dlq_suffix_does_not_rescue_an_invalid_base(self) -> None:
        """THE ANTI-BYPASS PIN. If `.dlq` were a prefix/suffix carve-out rather than a delegation,
        an otherwise-illegal name would become legal simply by appending the suffix — the exact
        "reserved-prefix bypass" this convention was required not to be."""
        assert TopicRegistry.validate("Invalid.Topic") is False
        assert TopicRegistry.validate("Invalid.Topic.dlq") is False
        assert TopicRegistry.validate("nosegments") is False
        assert TopicRegistry.validate("nosegments.dlq") is False
        assert TopicRegistry.validate("agents.events.BADSEGMENT.completed") is False
        assert TopicRegistry.validate("agents.events.BADSEGMENT.completed.dlq") is False

    def test_nested_dlq_is_refused(self) -> None:
        """A dead-letter of a dead-letter has no consumer in this platform and would hide an
        operator mistake behind a valid-looking name."""
        assert TopicRegistry.validate("operadora.notifications.internal.dlq.dlq") is False

    def test_bare_dlq_suffix_is_refused(self) -> None:
        assert TopicRegistry.validate(DLQ_SUFFIX) is False

    def test_dlq_topic_for_derives_and_validates_the_base(self) -> None:
        assert dlq_topic_for("operadora.notifications.internal") == "operadora.notifications.internal.dlq"

    def test_dlq_topic_for_refuses_an_invalid_base(self) -> None:
        """The derivation validates the BASE before minting a name, so a malformed source topic
        can never acquire a valid-looking DLQ name (what a bare f-string at the call site would
        allow)."""
        with pytest.raises(TopicValidationError):
            dlq_topic_for("Invalid.Topic")

    def test_dlq_topic_for_refuses_to_nest(self) -> None:
        with pytest.raises(TopicValidationError, match="refusing to nest"):
            dlq_topic_for("operadora.notifications.internal.dlq")

    def test_is_dlq_topic(self) -> None:
        assert TopicRegistry.is_dlq_topic("operadora.notifications.internal.dlq") is True
        assert TopicRegistry.is_dlq_topic("operadora.notifications.internal") is False

    def test_register_dlq_records_the_full_name_and_the_base_segments(self) -> None:
        """A DLQ entry groups with the traffic it quarantines: `name` is the FULL `.dlq` name,
        while dominio/contexto/acao are the BASE topic's — never "dlq" parsed as an acao."""
        registry = TopicRegistry(strict=True)
        registry.register("operadora.notifications.internal")
        entry = registry.register_dlq(
            "operadora.notifications.internal", description="poison-message shunt (GAP-SC-04-a)"
        )
        assert entry.name == "operadora.notifications.internal.dlq"
        assert (entry.dominio, entry.contexto, entry.acao) == ("operadora", "notifications", "internal")
        assert registry.get("operadora.notifications.internal.dlq") is entry

    def test_register_dlq_over_a_reserved_prefix_topic_keeps_the_base_segments(self) -> None:
        registry = TopicRegistry(strict=True)
        registry.register("agents.events.contas.completed")
        entry = registry.register_dlq("agents.events.contas.completed")
        assert entry.name == "agents.events.contas.completed.dlq"
        assert (entry.dominio, entry.contexto, entry.acao) == ("agents.events", "contas", "completed")

    # --- ADR-0006: a DLQ carries the payload VERBATIM, so it inherits the base topic's zone ----

    def test_register_dlq_inherits_a_phi_base_topics_zone(self) -> None:
        """The MAJOR of this rule: a poison message from a `zona_phi` topic IS PHI — the bytes are
        carried through untouched. Before this, `register_dlq` fell through to `register`'s
        `zona_geral` default and silently downgraded the one topic nobody validated."""
        registry = TopicRegistry(strict=True)
        registry.register("operadora.notifications.internal", pii_zone="zona_phi")

        entry = registry.register_dlq("operadora.notifications.internal")

        assert entry.pii_zone == "zona_phi"

    def test_register_dlq_inherits_a_general_base_topics_zone(self) -> None:
        registry = TopicRegistry(strict=True)
        registry.register("agents.events.contas.completed", pii_zone="zona_geral")

        assert registry.register_dlq("agents.events.contas.completed").pii_zone == "zona_geral"

    def test_register_dlq_refuses_to_downgrade_a_phi_base_topics_zone(self) -> None:
        """An explicit laxer zone is a REFUSAL, not an override — the caller cannot opt a DLQ out
        of the zone of the traffic it quarantines."""
        registry = TopicRegistry(strict=True)
        registry.register("operadora.notifications.internal", pii_zone="zona_phi")

        with pytest.raises(TopicValidationError, match="laxer"):
            registry.register_dlq("operadora.notifications.internal", pii_zone="zona_geral")

        assert registry.get("operadora.notifications.internal.dlq") is None

    def test_register_dlq_allows_raising_the_zone_above_the_base(self) -> None:
        """Raising is always allowed — only downgrades are refused."""
        registry = TopicRegistry(strict=True)
        registry.register("agents.events.contas.completed", pii_zone="zona_geral")

        entry = registry.register_dlq("agents.events.contas.completed", pii_zone="zona_phi")

        assert entry.pii_zone == "zona_phi"

    def test_register_dlq_fails_closed_when_the_base_topic_is_unregistered(self) -> None:
        """FAIL CLOSED rather than assume. With no base entry the zone is unknowable, and assuming
        `zona_geral` is exactly the silent downgrade this rule exists to prevent — so the zone must
        be stated."""
        registry = TopicRegistry(strict=True)

        with pytest.raises(TopicValidationError, match="cannot be inherited"):
            registry.register_dlq("operadora.notifications.internal")

        assert registry.get("operadora.notifications.internal.dlq") is None

    def test_register_dlq_accepts_an_explicit_zone_when_the_base_is_unregistered(self) -> None:
        registry = TopicRegistry(strict=True)

        entry = registry.register_dlq("operadora.notifications.internal", pii_zone="zona_phi")

        assert entry.pii_zone == "zona_phi"

    def test_register_dlq_refuses_an_unknown_zone_rather_than_ranking_it(self) -> None:
        """A typo must not pass as "not laxer" — an unrecognised zone is refused outright."""
        registry = TopicRegistry(strict=True)
        registry.register("operadora.notifications.internal", pii_zone="zona_phi")

        with pytest.raises(TopicValidationError, match="unknown pii_zone"):
            registry.register_dlq("operadora.notifications.internal", pii_zone="zona_phy")

    def test_parse_of_a_dlq_topic_is_base_relative(self) -> None:
        assert TopicRegistry.parse("operadora.notifications.internal.dlq") == (
            "operadora",
            "notifications",
            "internal",
        )

    def test_dlq_name_still_respects_the_kafka_length_limit(self) -> None:
        """The suffix is part of the name Kafka sees — a base that fits but whose DLQ does not
        must be refused, not silently truncated."""
        base = "a" + "b" * 244 + ".c.d"  # 249 chars exactly
        assert len(base) == 249
        assert TopicRegistry.validate(base) is True
        assert TopicRegistry.validate(base + DLQ_SUFFIX) is False
