"""The assembler's pure parts: pin construction, admission binding, and what it refuses to invent.

The head-reading halves (`_facts`, `_authority`, `_published`) need a real protected/native
Postgres pair and are proven by the integration tests listed for the engine runner; what is proven
HERE is every decision the assembler makes that does not need a database — because those are the
ones where a wrong answer would silently produce a command that binds the wrong sources.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntentPayload,
    GuideIdentity,
    InputPublication,
    Scope,
    SessionBinding,
    StartFacts,
)
from maezo.gateway.human.read_profile import SourceProvenance, digest, wire
from maezo.portal.engine.profile import canonicalize
from maezo.runtime.intake_dispatch.assembly import PublishedCommandSource
from maezo.runtime.intake_dispatch.service import CommandUnassembledError

HASH = "a" * 64
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def source() -> SourceProvenance:
    return SourceProvenance(
        publisher_ref="publisher",
        source_ref="source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="source-receipt",
        observed_at=NOW - timedelta(seconds=5),
        valid_until=NOW + timedelta(seconds=600),
    )


def actor() -> Actor:
    return Actor(
        principal_ref="principal",
        issuer="https://identity.example",
        subject="subject",
        membership_revision=1,
        audience="provider",
    )


def intent(*, command_id: str = "command", operation: str = "auth.start") -> AuditIntentPayload:
    return AuditIntentPayload(
        intent_ref="intake",
        intake_or_response_ref="intake",
        command_id=command_id,
        actor=actor(),
        admitted_digest=HASH,
        operation=operation,
        state="committed",
        admitted_at=NOW - timedelta(seconds=10),
        session_binding=SessionBinding(
            session_ref="session",
            authenticated_at=NOW - timedelta(seconds=30),
            session_expires_at=NOW + timedelta(seconds=600),
            authorization_until=NOW + timedelta(seconds=300),
            session_source_revision=1,
            session_record_digest=HASH,
        ),
    )


def publication(*, kind: str = "audit_intent", payload: object | None = None) -> InputPublication:
    payload = intent() if payload is None else payload
    return InputPublication(
        schema="human-auth-input-publication.v1",
        scope=Scope(
            tenant="amh",
            environment="test",
            engine_name="engine",
            database_incarnation="incarnation",
            installation_ref="installation",
            installation_revision=1,
        ),
        workload_ref="publisher-workload",
        publication_id="publication",
        kind=kind,
        resource_ref="intake",
        expected_generation=0,
        source=source(),
        state="active",
        payload=payload,
        payload_digest=digest(payload),
        valid_until=NOW + timedelta(seconds=300),
    )


class FakeRow(dict):
    """A published head row, with only the columns the pin is built from."""


def head_row(*, generation: int = 3) -> FakeRow:
    return FakeRow(
        generation_=generation,
        payload_digest_=HASH,
        source_=canonicalize(wire(source())),
    )


def test_the_pin_takes_generation_digest_and_source_from_the_head_row():
    pin = PublishedCommandSource._pin("guide", "guide-1", head_row(generation=7))
    assert (pin.kind, pin.resource_ref, pin.head_generation, pin.payload_digest) == (
        "guide",
        "guide-1",
        7,
        HASH,
    )
    assert pin.source == source()


def test_the_admission_is_bound_from_the_published_admission_head():
    admission = PublishedCommandSource._admission(publication(), "command")
    assert (admission.intent_ref, admission.admitted_command_id, admission.admitted_digest) == (
        "intake",
        "command",
        HASH,
    )
    assert admission.source == source()


def test_a_validly_published_head_of_another_kind_is_not_an_admission():
    """`InputPublication` itself forbids a payload/kind mismatch, so this is a REAL guide head."""
    guide = GuideIdentity(
        guide_identity_ref="guide",
        source_ref="source",
        namespace_ref="namespace",
        source_guide_ref="source-guide",
        cutover_ref="cutover",
        cutover_revision=1,
        legacy_state="absent_at_cutover",
        prior_instance_id=None,
        prior_case_ref=None,
        source=source(),
    )
    with pytest.raises(CommandUnassembledError, match="admission_unpublished"):
        PublishedCommandSource._admission(publication(kind="guide", payload=guide), "command")


def test_a_non_publication_object_is_refused_rather_than_duck_typed():
    with pytest.raises(CommandUnassembledError, match="admission_unpublished"):
        PublishedCommandSource._admission(object(), "command")


def test_an_admission_for_another_command_is_refused():
    with pytest.raises(CommandUnassembledError, match="admission_command_mismatch"):
        PublishedCommandSource._admission(publication(), "another-command")


def test_an_admission_of_a_document_response_is_not_a_start_admission():
    with pytest.raises(CommandUnassembledError, match="admission_command_mismatch"):
        PublishedCommandSource._admission(
            publication(payload=intent(operation="auth.documents.respond")), "command"
        )


def test_a_payload_of_the_wrong_shape_is_an_installation_fault_not_a_weaker_fact():
    with pytest.raises(CommandUnassembledError, match="head_payload"):
        PublishedCommandSource._exact(StartFacts, object(), "head_payload_shape")


def test_an_exact_payload_passes_through_unchanged():
    value = intent()
    assert PublishedCommandSource._exact(AuditIntentPayload, value, "token") is value


def test_a_misbound_composition_is_refused_at_construction():
    with pytest.raises(CommandUnassembledError, match="command_source_misbound"):
        PublishedCommandSource(composition=None, intake_store=None, definition=None)
