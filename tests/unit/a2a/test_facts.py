"""Unit tests for maezo.a2a.facts — DelegationFact + the 3 delegation topics (ADR-0003).

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's fact-shape
coverage as part of the T2.4 A2A W2 build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §5 W2 ("register the 3 delegation topics").

Covers: `build_fact`'s PHI-free serialization (never carries `meta`, only bounded structural
fields); topic naming; and `register_a2a_topics` against the real `TopicRegistry` (proving the 3
names satisfy its `agents.events.X.Y` naming convention — the closest v2 equivalent of "registered
in config/topic_registry.yaml", since that YAML file does not exist in v2 — see `facts.py`'s
module docstring).
"""

from __future__ import annotations

import json

from maezo.a2a import (
    TOPIC_COMPLETED,
    TOPIC_REJECTED,
    TOPIC_REQUESTED,
    DelegationFactKind,
    register_a2a_topics,
)
from maezo.a2a.facts import build_fact
from maezo.platform.topic_registry import TopicRegistry


def _fact(**kw: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "kind": DelegationFactKind.REQUESTED,
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "tenant": "amh",
        "origin": "helena",
        "target": "rafael",
        "delegation_chain": ("helena", "rafael"),
    }
    base.update(kw)
    return build_fact(**base)  # type: ignore[arg-type]


class TestDelegationFact:
    def test_topic_matches_kind(self) -> None:
        assert _fact(kind=DelegationFactKind.REQUESTED).topic == TOPIC_REQUESTED
        assert _fact(kind=DelegationFactKind.COMPLETED).topic == TOPIC_COMPLETED
        assert _fact(kind=DelegationFactKind.REJECTED).topic == TOPIC_REJECTED

    def test_to_value_carries_only_structural_fields(self) -> None:
        fact = _fact()
        payload = json.loads(fact.to_value())
        assert payload == {
            "kind": "requested",
            "task_id": "t1",
            "task_type": "authorization.analyze",
            "tenant": "amh",
            "origin": "helena",
            "target": "rafael",
            "delegation_chain": ["helena", "rafael"],
            "ts": payload["ts"],  # timestamp is present but not asserted verbatim
        }

    def test_to_value_includes_reason_when_rejected(self) -> None:
        fact = _fact(kind=DelegationFactKind.REJECTED, reason="unknown_target")
        payload = json.loads(fact.to_value())
        assert payload["reason"] == "unknown_target"

    def test_to_value_includes_output_ref_when_completed(self) -> None:
        fact = _fact(kind=DelegationFactKind.COMPLETED, output_ref="fhir://Task/done")
        payload = json.loads(fact.to_value())
        assert payload["output_ref"] == "fhir://Task/done"

    def test_build_fact_never_emits_meta(self) -> None:
        """`meta` is accepted but NEVER enters the fact — avoids accidental PHI (facts.py docstring)."""
        fact = _fact(meta={"cpf_beneficiario": "123.456.789-01"})
        serialized = fact.to_value().decode("utf-8")
        assert "cpf_beneficiario" not in serialized
        assert "123.456.789-01" not in serialized

    def test_to_value_is_deterministic_json(self) -> None:
        """Sorted keys + compact separators — stable bytes for the same content."""
        first = _fact().to_value()
        # Rebuild with the exact same (non-timestamped) inputs; only `ts` may legitimately drift,
        # so compare the parsed payload minus `ts` instead of raw bytes.
        second = _fact().to_value()
        p1, p2 = json.loads(first), json.loads(second)
        del p1["ts"], p2["ts"]
        assert p1 == p2


class TestRegisterA2ATopics:
    def test_registers_exactly_three_topics(self) -> None:
        registry = TopicRegistry(strict=True)
        entries = register_a2a_topics(registry)
        assert {e.name for e in entries} == {TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED}
        assert registry.count() == 3

    def test_topics_satisfy_naming_convention(self) -> None:
        for topic in (TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED):
            assert TopicRegistry.validate(topic) is True

    def test_topics_parse_as_agents_events_delegation(self) -> None:
        for topic in (TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED):
            dominio, contexto, acao = TopicRegistry.parse(topic)
            assert dominio == "agents.events"
            assert contexto == "delegation"
            assert acao in {"requested", "completed", "rejected"}

    def test_topics_are_general_zone_not_phi(self) -> None:
        registry = TopicRegistry()
        for entry in register_a2a_topics(registry):
            assert entry.pii_zone == "zona_geral"

    def test_registering_twice_is_idempotent_with_default_registry(self) -> None:
        """Non-strict registry: re-registering overwrites without raising (TopicRegistry default)."""
        registry = TopicRegistry()
        register_a2a_topics(registry)
        register_a2a_topics(registry)  # must not raise
        assert registry.count() == 3
