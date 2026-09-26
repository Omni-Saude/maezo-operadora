"""`StaffAssignmentPublisher` + `ack_native`: nested closed models pass through the closed codec.

Medido no harness C1 (Onda 8): construir `AssignmentPublication(...)` com a atestacao e a geracao
como instancias revalidava pelo nome do campo (`schema_`) e recusava o alias `schema` (4 erros);
`AssignmentPublicationReceipt.model_validate(instancia)` tinha o mesmo defeito no `ack_native`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.gateway.human.assignment_policy import (
    AdminSourceAttestation,
    AssignmentPublication,
    AssignmentPublicationReceipt,
    GenerationPayload,
)
from maezo.gateway.human.assignment_publisher import StaffAssignmentPublisher
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.models import Scope
from maezo.gateway.human.read_profile import ArtifactPin, digest, parse_model, wire
from maezo.portal.admin.assignments import FrozenAssignmentSource
from maezo.portal.engine.profile import canonicalize

SCOPE = Scope(tenant="amh", environment="dev", workload_ref="portal")


def _generation() -> GenerationPayload:
    until = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    return parse_model(
        GenerationPayload,
        wire(
            dict(
                schema="human-staff-assignment-generation.v1",
                tenant="amh",
                environment="dev",
                engine_name="default",
                database_incarnation="inc-1",
                source_revision=1,
                state="complete",
                memberships=[],
                membership_count=0,
                membership_digest=digest(()),
                policies=[],
                bindings=[],
                resource_designations=[],
                artifacts=[],
                valid_until=until,
            )
        ),
    )


class Admin:
    def __init__(self, frozen: FrozenAssignmentSource) -> None:
        self.scope, self.frozen, self.persisted, self.acked = SCOPE, frozen, None, None

    async def read_frozen(self, revision: int) -> FrozenAssignmentSource:
        return self.frozen

    async def reconcile(self, publication_id: str) -> bytes:
        if self.persisted is None:
            raise GatewayRefusalError("production_capabilities_unavailable")
        return self.persisted

    async def persist_request(self, frozen: Any, publication: AssignmentPublication) -> bytes:
        self.persisted = canonicalize(wire(publication))
        return self.persisted

    async def ack_native(self, receipt: AssignmentPublicationReceipt) -> None:
        self.acked = receipt

    async def uncertain(self, publication_id: str) -> None:
        raise AssertionError("delivery must not be uncertain")


class Source:
    def __init__(self, generation_digest: str) -> None:
        self.digest = generation_digest

    async def attest(self, revision: int) -> AdminSourceAttestation:
        now = datetime.now(UTC)
        return parse_model(
            AdminSourceAttestation,
            wire(
                dict(
                    schema="human-staff-assignment-source.v1",
                    source=dict(
                        publisher_ref="owner",
                        source_ref="src",
                        source_revision=1,
                        source_digest=self.digest,
                        receipt_ref="pub-1",
                        observed_at=now,
                        valid_until=now + timedelta(hours=1),
                    ),
                    tenant="amh",
                    environment="dev",
                    engine_name="default",
                    database_incarnation="inc-1",
                    source_key_id="k",
                    algorithm="Ed25519",
                    generation_digest=self.digest,
                    signature="s",
                )
            ),
        )


class Client:
    scope, purpose = SCOPE, "human-authority"

    async def publish(self, raw: bytes) -> dict[str, Any]:
        request = parse_model(AssignmentPublication, json.loads(raw))
        return wire(
            dict(
                schema="human-assignment-publication-receipt.v1",
                tenant="amh",
                publication_id="pub-1",
                request_digest=digest(request),
                operation="replace",
                authority_revision=1,
                source_revision=1,
                generation_digest=digest(request.generation),
                state="active",
            )
        )


@pytest.mark.asyncio
async def test_publish_builds_the_request_through_the_closed_codec() -> None:
    generation = _generation()
    frozen = FrozenAssignmentSource(
        tenant="amh",
        publication_id="pub-1",
        source_revision=1,
        operation="replace",
        payload=canonicalize(wire(generation)),
        source_digest=digest(generation),
        owner_receipt=ArtifactPin(artifact_ref="review", digest="c" * 64),
        expected_native_revision=0,
        expected_generation_digest=None,
        committed_at=datetime.now(UTC),
    )
    admin = Admin(frozen)
    publisher = StaffAssignmentPublisher.__new__(StaffAssignmentPublisher)
    publisher._administration, publisher._source, publisher._client = (
        admin,
        Source(digest(generation)),
        Client(),
    )
    receipt = await publisher.publish(1)
    assert receipt.state == "active" and admin.acked == receipt
    assert parse_model(AssignmentPublication, json.loads(admin.persisted)).generation == generation
