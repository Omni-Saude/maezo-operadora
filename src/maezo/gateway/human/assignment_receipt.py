"""C9 independent current receipt disclosure; no assignment or Q2 grant conversion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import Identity as CaseIdentity
from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Sha256Digest, SubjectBinding

from .assignment_policy import AdminSourceAttestation, Number, RoleGroupRequirement, Version, ordered
from .assignment_transport import AssignmentPrivateTransport, unavailable
from .models import Closed, Scope, Timed
from .read_profile import Artifact, ArtifactPin, digest, parse_model, wire
from .receipt import CurrentReceiptAuthority, ReceiptIdentity, ReceiptResourceAuthority

if TYPE_CHECKING:
    from maezo.portal.admin.assignments import (
        PostgresStaffAssignmentAdministration,
        ReceiptDisclosureSourceSigner,
    )


class ReceiptLinkage(Closed):
    task_id: OpaqueRef
    process_instance_id: OpaqueRef
    process_definition_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: Version
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    binding_ref: OpaqueRef
    binding_version: Version
    binding_digest: Sha256Digest


class TaskReceiptResource(Closed):
    kind: Literal["task"]
    resource_ref: OpaqueRef


class CaseReceiptResource(Closed):
    kind: Literal["case"]
    resource_ref: OpaqueRef
    case_identity: CaseIdentity
    ownership_source_ref: OpaqueRef
    ownership_source_revision: Number
    ownership_source_digest: Sha256Digest


class ReceiptDisclosurePolicy(Timed):
    schema_: Literal["human-assignment-receipt-policy.v1"] = Field(alias="schema")
    policy_ref: OpaqueRef
    version: Version
    tenant: OpaqueRef
    contract_pins: tuple[ArtifactPin, ...]
    membership_requirement: RoleGroupRequirement
    resource_kinds: tuple[Literal["task", "case"], ...]
    subject_mode: Literal["none", "resource_bindings"]
    required_consent_scopes: tuple[OpaqueRef, ...]
    projection: Literal["human-assignment-receipt-disclosure.v1"]
    review_receipt: ArtifactPin

    @model_validator(mode="after")
    def policy_shape(self) -> Self:
        ordered(self.contract_pins, lambda p: (p.artifact_ref, p.digest))
        ordered(self.resource_kinds)
        ordered(self.required_consent_scopes)
        if not self.contract_pins or not self.resource_kinds:
            raise ValueError("incomplete receipt policy")
        return self


class ReceiptDisclosureGrant(Timed):
    issuer: str
    subject: OpaqueRef
    principal_ref: OpaqueRef
    membership_revision: Number
    consent_scopes: tuple[OpaqueRef, ...]
    decision_receipt: ArtifactPin


class ReceiptDisclosure(Timed):
    schema_: Literal["human-assignment-receipt-disclosure.v1"] = Field(alias="schema")
    identity: ReceiptIdentity
    linkage: ReceiptLinkage
    resource: Annotated[TaskReceiptResource | CaseReceiptResource, Field(discriminator="kind")]
    policy: ReceiptDisclosurePolicy
    required_subject_bindings: tuple[SubjectBinding, ...]
    grant: ReceiptDisclosureGrant
    projection: Literal["human-assignment-receipt-disclosure.v1"]
    state: Literal["active", "revoked"]

    @model_validator(mode="after")
    def exact_resource(self) -> Self:
        i, linkage, r, p, g = self.identity, self.linkage, self.resource, self.policy, self.grant
        ordered(self.required_subject_bindings, lambda s: (s.kind, s.resource_ref))
        ordered(g.consent_scopes)
        if (
            i.task_id != linkage.task_id
            or i.principal_ref != g.principal_ref
            or p.tenant != i.tenant
            or r.kind not in p.resource_kinds
        ):
            raise ValueError("receipt resource mismatch")
        if (p.subject_mode == "none") != (not self.required_subject_bindings):
            raise ValueError("receipt subject policy mismatch")
        if not set(p.required_consent_scopes).issubset(g.consent_scopes) or self.valid_until > min(
            p.valid_until, g.valid_until
        ):
            raise ValueError("receipt consent or deadline mismatch")
        if isinstance(r, TaskReceiptResource):
            if r.resource_ref != i.task_id:
                raise ValueError("receipt task mismatch")
        elif (
            r.resource_ref != r.case_identity.case_ref
            or linkage.process_instance_id != r.case_identity.process_instance_ref
            or any(
                str(getattr(linkage, k)) != str(getattr(r.case_identity, k))
                for k in (
                    "process_definition_id",
                    "process_definition_key",
                    "process_definition_version",
                    "process_definition_digest",
                )
            )
        ):
            raise ValueError("receipt case mismatch")
        return self


class ReceiptDisclosurePublication(Closed):
    schema_: Literal["human-assignment-receipt-publication.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    workload_ref: OpaqueRef
    publication_id: OpaqueRef
    expected_revision: Number
    source: AdminSourceAttestation
    disclosure: ReceiptDisclosure
    artifacts: tuple[Artifact, ...]

    @model_validator(mode="after")
    def source_bound(self) -> Self:
        expected = digest(dict(disclosure=self.disclosure, artifacts=self.artifacts))
        if (
            self.tenant != self.disclosure.identity.tenant
            or self.source.tenant != self.tenant
            or self.source.generation_digest != expected
            or self.source.source.source_digest != expected
        ):
            raise ValueError("receipt source mismatch")
        required = {
            (p.artifact_ref, p.digest)
            for p in (
                *self.disclosure.policy.contract_pins,
                self.disclosure.policy.review_receipt,
                self.disclosure.grant.decision_receipt,
            )
        }
        ordered(self.artifacts, lambda a: (a.artifact_ref, a.digest))
        if required != {(a.artifact_ref, a.digest) for a in self.artifacts}:
            raise ValueError("receipt artifact coverage mismatch")
        return self


class ReceiptDisclosurePublicationReceipt(Closed):
    schema_: Literal["human-assignment-receipt-publication-receipt.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    publication_id: OpaqueRef
    request_digest: Sha256Digest
    authority_revision: Number
    source_revision: Number
    disclosure_digest: Sha256Digest
    state: Literal["active", "revoked"]


class NativeAssignmentReceiptAuthority(ReceiptResourceAuthority):
    def __init__(
        self, *, transport: AssignmentPrivateTransport, source_engine: AsyncEngine, command_scope: Scope
    ) -> None:
        if transport.purpose != "human-assignment-read" or source_engine.dialect.name != "postgresql":
            raise unavailable()
        scope = Scope.model_validate(command_scope)
        if (scope.tenant, scope.environment) != (
            transport.scope.tenant,
            transport.scope.environment,
        ) or scope.workload_ref == transport.scope.workload_ref:
            raise unavailable()
        self.scope, self._transport, self._engine = scope, transport, source_engine

    async def _current_source(self, identity: ReceiptIdentity) -> None:
        from maezo.gateway.audit_postgres import schema_for_tenant
        from maezo.gateway.human.projection import restore_command
        from maezo.portal.engine.assignment import HumanAssignmentCommand

        async with self._engine.begin() as db:
            schema = schema_for_tenant(self.scope.tenant)
            await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            actual_schema = (await db.execute(text("SELECT current_schema()"))).scalar_one()
            if actual_schema != schema:
                raise unavailable()
            canonical = (
                await db.execute(
                    text(
                        "SELECT canonical_payload FROM human_command_outbox "
                        "WHERE tenant=:tenant AND task_id=:task AND command_id=:command "
                        "AND payload_digest=:digest"
                    ),
                    dict(
                        tenant=identity.tenant,
                        task=identity.task_id,
                        command=identity.command_id,
                        digest=identity.payload_digest,
                    ),
                )
            ).scalar_one_or_none()
            if canonical is None:
                raise unavailable()
            command = restore_command(bytes(canonical))
            if (
                not isinstance(command, HumanAssignmentCommand)
                or any(
                    getattr(command, key) != getattr(identity, key)
                    for key in ("tenant", "task_id", "command_id", "principal_ref", "workload_ref")
                )
                or command.digest != identity.payload_digest
            ):
                raise unavailable()
            row = (
                await db.execute(
                    text(
                        "SELECT state FROM portal_assignment_receipt_source WHERE tenant=:tenant AND "
                        "task_id=:task AND command_id=:command AND identity_digest=:digest"
                    ),
                    dict(
                        tenant=self.scope.tenant,
                        task=identity.task_id,
                        command=identity.command_id,
                        digest=digest(identity),
                    ),
                )
            ).scalar_one_or_none()
            if row != "active":
                raise unavailable()

    async def current_authority(
        self, principal: HumanPrincipal, identity: ReceiptIdentity
    ) -> CurrentReceiptAuthority:
        p = HumanPrincipal.model_validate(principal)
        i = ReceiptIdentity.model_validate(identity)
        if (
            p.tenant != self.scope.tenant
            or i.tenant != self.scope.tenant
            or i.principal_ref != p.principal_ref
            or i.workload_ref != self.scope.workload_ref
        ):
            raise unavailable()
        await self._current_source(i)
        request = dict(
            schema="human-assignment-receipt-authority.v1",
            tenant=self.scope.tenant,
            workload_ref=self._transport.scope.workload_ref,
            principal_ref=p.principal_ref,
            principal_issuer=p.issuer,
            principal_subject=p.subject,
            membership_revision=str(p.membership_revision),
            identity=wire(i),
        )
        result = await self._transport.receipt_authority(request)
        if (
            set(result) != {"schema", "request_digest", "authority", "valid_until"}
            or result["schema"] != "human-assignment-receipt-authority-result.v1"
        ):
            raise unavailable()
        a = parse_model(CurrentReceiptAuthority, result["authority"])
        deadline = parse_model(Timed, {"valid_until": result["valid_until"]}).valid_until
        if (
            a.identity != i
            or (a.issuer, a.subject, a.membership_revision) != (p.issuer, p.subject, p.membership_revision)
            or not a.read_permitted
            or min(a.valid_until, deadline) <= datetime.now(UTC)
        ):
            raise unavailable()
        await self._current_source(i)
        if min(a.valid_until, deadline) <= datetime.now(UTC):
            raise unavailable()
        return a


class ReceiptDisclosurePublisher:
    """Durable source owner publication/recovery with exact request bytes, no refreshed CAS."""

    def __init__(
        self,
        *,
        administration: PostgresStaffAssignmentAdministration,
        source: ReceiptDisclosureSourceSigner,
        transport: AssignmentPrivateTransport,
    ) -> None:
        if transport.purpose != "human-authority" or transport.scope != administration.scope:
            raise unavailable()
        self._administration, self._source, self._transport = administration, source, transport

    async def publish(
        self, task_id: str, command_id: str, source_revision: int
    ) -> ReceiptDisclosurePublicationReceipt:
        from maezo.portal.engine.profile import strict_loads

        from .errors import GatewayRefusalError

        frozen = await self._administration.read_frozen_receipt(task_id, command_id, source_revision)
        try:
            raw = await self._administration.reconcile(frozen.publication_id)
        except GatewayRefusalError:
            attestation = await self._source.attest(task_id, command_id, source_revision)
            payload = strict_loads(frozen.payload)
            request = ReceiptDisclosurePublication(
                schema="human-assignment-receipt-publication.v1",
                tenant=self._transport.scope.tenant,
                workload_ref=self._transport.scope.workload_ref,
                publication_id=frozen.publication_id,
                expected_revision=frozen.expected_native_revision,
                source=attestation,
                disclosure=parse_model(ReceiptDisclosure, payload["disclosure"]),
                artifacts=tuple(parse_model(Artifact, a) for a in payload["artifacts"]),
            )
            raw = await self._administration.persist_receipt_request(frozen, request)
        return await self._dispatch(raw)

    async def reconcile(self, publication_id: str) -> ReceiptDisclosurePublicationReceipt:
        return await self._dispatch(await self._administration.reconcile(publication_id))

    async def _dispatch(self, raw: bytes) -> ReceiptDisclosurePublicationReceipt:
        from maezo.portal.engine.profile import strict_loads

        request = parse_model(ReceiptDisclosurePublication, strict_loads(raw))
        try:
            result = await self._transport.publish(raw)
            receipt = parse_model(ReceiptDisclosurePublicationReceipt, result)
            if receipt.request_digest != digest(request) or receipt.publication_id != request.publication_id:
                raise unavailable()
            await self._administration.ack_receipt(receipt)
            return receipt
        except BaseException:
            await self._administration.uncertain(request.publication_id)
            raise
