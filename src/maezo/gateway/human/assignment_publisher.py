"""Exact durable staff publication; uncertain ACK retries the original inner bytes."""

from __future__ import annotations

from maezo.portal.admin.assignments import PostgresStaffAssignmentAdministration, StaffAssignmentSourceSigner
from maezo.portal.engine.profile import strict_loads

from .assignment_policy import AssignmentPublication, AssignmentPublicationReceipt, GenerationPayload
from .assignment_transport import AssignmentPrivateTransport
from .errors import GatewayRefusalError
from .read_profile import digest, parse_model, wire


class StaffAssignmentPublisher:
    def __init__(
        self,
        *,
        administration: PostgresStaffAssignmentAdministration,
        source: StaffAssignmentSourceSigner,
        client: AssignmentPrivateTransport,
    ) -> None:
        if client.scope != administration.scope or client.purpose != "human-authority":
            raise GatewayRefusalError("production_capabilities_unavailable")
        self._administration, self._source, self._client = administration, source, client

    async def publish(self, source_revision: int) -> AssignmentPublicationReceipt:
        frozen = await self._administration.read_frozen(source_revision)
        # If dispatch was already prepared, never refresh source signature or engine CAS.
        try:
            raw = await self._administration.reconcile(frozen.publication_id)
        except GatewayRefusalError:
            attestation = await self._source.attest(source_revision)
            generation = (
                parse_model(GenerationPayload, strict_loads(frozen.payload))
                if frozen.operation == "replace"
                else None
            )
            # Through the closed codec, not the constructor: nested closed models carry the
            # `schema` alias, and re-validating an instance by field name (`schema_`) refuses it
            # (measured in the C1 harness, Onda 8: 4 errors on source/generation.schema).
            publication = parse_model(
                AssignmentPublication,
                wire(
                    dict(
                        schema="human-assignment-publication.v1",
                        tenant=frozen.tenant,
                        workload_ref=self._client.scope.workload_ref,
                        publication_id=frozen.publication_id,
                        expected_revision=frozen.expected_native_revision,
                        operation=frozen.operation,
                        source=attestation,
                        generation=generation,
                        expected_generation_digest=frozen.expected_generation_digest,
                    )
                ),
            )
            raw = await self._administration.persist_request(frozen, publication)
        return await self._dispatch(raw)

    async def reconcile(self, publication_id: str) -> AssignmentPublicationReceipt:
        return await self._dispatch(await self._administration.reconcile(publication_id))

    async def _dispatch(self, raw: bytes) -> AssignmentPublicationReceipt:
        request = parse_model(AssignmentPublication, strict_loads(raw))
        try:
            result = await self._client.publish(raw)
            receipt = parse_model(AssignmentPublicationReceipt, result)
            if receipt.request_digest != digest(request) or receipt.publication_id != request.publication_id:
                raise GatewayRefusalError("production_capabilities_unavailable")
            await self._administration.ack_native(receipt)
            return receipt
        except BaseException:
            await self._administration.uncertain(request.publication_id)
            raise
