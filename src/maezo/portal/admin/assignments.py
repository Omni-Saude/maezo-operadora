"""E03 reviewed PostgreSQL staff source, frozen before native publication.

The source is the existing membership table. A change from active authority first
returns a durable disable intent without changing memberships. After native ACK,
the same reviewed change can commit its complete replacement. No BFF routes call
this service; the independently granted administrative connection owns mutations.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.human.assignment_policy import (
    AdminSourceAttestation,
    AssignmentBinding,
    AssignmentPolicy,
    AssignmentPublication,
    AssignmentPublicationReceipt,
    GenerationPayload,
    ResourceDesignation,
    StaffMembership,
)
from maezo.gateway.human.credentials import AssignmentSigningLease
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.models import Scope
from maezo.gateway.human.read_profile import Artifact, ArtifactPin, digest, parse_model, wire
from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import canonicalize, strict_loads

if TYPE_CHECKING:
    from maezo.gateway.human.assignment_receipt import (
        ReceiptDisclosure,
        ReceiptDisclosurePublication,
        ReceiptDisclosurePublicationReceipt,
    )


def unavailable() -> GatewayRefusalError:
    return GatewayRefusalError("production_capabilities_unavailable")


def conflict() -> GatewayRefusalError:
    return GatewayRefusalError("revision_conflict")


@dataclass(frozen=True, slots=True)
class FrozenAssignmentSource:
    tenant: str
    publication_id: str
    source_revision: int
    operation: Literal["disable", "replace"]
    payload: bytes
    source_digest: str
    owner_receipt: ArtifactPin
    expected_native_revision: int
    expected_generation_digest: str | None
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class FrozenReceiptSource:
    tenant: str
    publication_id: str
    source_revision: int
    payload: bytes
    source_digest: str
    owner_receipt: ArtifactPin
    expected_native_revision: int
    committed_at: datetime
    task_id: str
    command_id: str


class PostgresStaffAssignmentAdministration:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        scope: Scope,
        engine_name: str,
        database_incarnation: str,
        database_role: str,
        artifacts: tuple[Artifact, ...],
        approved_owner_receipts: tuple[ArtifactPin, ...],
        valid_until: datetime,
        initial_native_revision: int,
        approved_receipt_policy_pins: tuple[ArtifactPin, ...] = (),
        auth_source: Any = None,
    ) -> None:
        if engine.dialect.name != "postgresql" or not database_role or valid_until.tzinfo is None:
            raise unavailable()
        if not 0 <= initial_native_revision < 2**63:
            raise unavailable()
        self.engine, self.scope = engine, Scope.model_validate(scope)
        self.engine_name, self.database_incarnation = engine_name, database_incarnation
        self.database_role = database_role
        self.artifacts = tuple(Artifact.model_validate(a) for a in artifacts)
        self.owner_receipts = tuple(ArtifactPin.model_validate(p) for p in approved_owner_receipts)
        self.valid_until, self.initial_native_revision = valid_until, initial_native_revision
        self.receipt_policy_pins = tuple(ArtifactPin.model_validate(p) for p in approved_receipt_policy_pins)
        if auth_source is not None:
            from maezo.gateway.intake.native_source_lifecycle import PostgresAuthSourceLifecycle

            if (
                type(auth_source) is not PostgresAuthSourceLifecycle
                or auth_source.binding.tenant != self.scope.tenant
            ):
                raise unavailable()
        self.auth_source = auth_source

    async def _auth_source_required(self, db: AsyncConnection) -> bool:
        actual = (await db.execute(text("SELECT to_regclass('portal_memberships')::oid"))).scalar_one()
        guarded = (
            await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=:oid AND "
                    "tgname='portal_auth_membership_write' AND tgenabled='A')"
                ),
                {"oid": actual},
            )
        ).scalar_one()
        if guarded and self.auth_source is None:
            raise unavailable()
        if self.auth_source is not None:
            pin = next(p for p in self.auth_source.binding.relations if p.name == "portal_memberships")
            if actual != pin.oid:
                raise unavailable()
            await self.auth_source.qualified(db, self.auth_source.binding.writer_role)
        return bool(guarded)

    async def _guarded_memberships(
        self,
        db: AsyncConnection,
        changes: tuple[MembershipRecord, ...],
        auth_changes: dict[str, str],
        applied_auth: list[str],
    ) -> tuple[MembershipRecord, ...]:
        if self.auth_source is None:
            raise unavailable()
        for record in changes:
            change_id = auth_changes.get(record.principal_ref)
            if change_id is None:
                continue
            # The existing disabled assignment-source row is locked; only the
            # already-ACKed AUTH change is applied, with no HTTP.
            await self.auth_source.apply_change(change_id, db)
            applied_auth.append(change_id)
        records = await self.auth_source.locked_staff_memberships(db)
        for record in changes:
            prior = tuple(
                candidate
                for candidate in records
                if candidate.principal_ref == record.principal_ref
                or (candidate.issuer, candidate.subject) == (record.issuer, record.subject)
            )
            if len(prior) != 1 or prior[0] != record:
                raise conflict()
        return records

    async def _qualified(self, db: AsyncConnection) -> None:
        from maezo.gateway.audit_postgres import schema_for_tenant

        schema = schema_for_tenant(self.scope.tenant)
        await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        if (await db.execute(text("SELECT current_schema()"))).scalar_one() != schema:
            raise unavailable()
        role = (await db.execute(text("SELECT session_user"))).scalar_one()
        if role != self.database_role or datetime.now(UTC) >= self.valid_until:
            raise unavailable()

    async def _locked(self, db: AsyncConnection) -> dict[str, Any]:
        await self._qualified(db)
        row = (
            (
                await db.execute(
                    text("SELECT * FROM portal_assignment_source WHERE tenant=:tenant FOR UPDATE"),
                    {"tenant": self.scope.tenant},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise unavailable()
        return dict(row)

    def _owner(self, receipt: ArtifactPin, policy: tuple[AssignmentPolicy, ...]) -> ArtifactPin:
        receipt = ArtifactPin.model_validate(receipt)
        if receipt not in self.owner_receipts or any(p.review_receipt != receipt for p in policy):
            raise unavailable()
        return receipt

    async def prepare_change(
        self,
        expected_source_revision: int,
        membership_changes: tuple[MembershipRecord, ...],
        policy: tuple[AssignmentPolicy, ...],
        bindings: tuple[AssignmentBinding, ...],
        constraints: tuple[ResourceDesignation, ...],
        owner_receipt: ArtifactPin,
    ) -> FrozenAssignmentSource:
        owner_receipt = self._owner(owner_receipt, policy)
        changes = tuple(MembershipRecord.model_validate(m) for m in membership_changes)
        if any(m.tenant != self.scope.tenant or m.audience != "staff" for m in changes):
            raise unavailable()
        if len({m.principal_ref for m in changes}) != len(changes):
            raise unavailable()
        designation = canonicalize(
            wire(
                dict(
                    membership_changes=changes,
                    policy=policy,
                    bindings=bindings,
                    constraints=constraints,
                    owner_receipt=owner_receipt,
                )
            )
        )
        if self.auth_source is not None:
            for record in changes:
                await self.auth_source.recover_membership(record)
        auth_changes: dict[str, str] = {}
        applied_auth: list[str] = []
        # Native assignment disable/ACK must have completed before AUTH source
        # preparation. No assignment/source SQL lock is held during native HTTP.
        if changes:
            async with self.engine.begin() as preparation:
                await self._qualified(preparation)
                guarded = await self._auth_source_required(preparation)
                state = (
                    await preparation.execute(
                        text(
                            "SELECT state FROM portal_assignment_source WHERE tenant=:tenan"
                            "t AND source_revision=:revision"
                        ),
                        {"tenant": self.scope.tenant, "revision": expected_source_revision},
                    )
                ).scalar_one_or_none()
                prepare_auth = guarded and (
                    state == "disabled" or (state is None and expected_source_revision == 0)
                )
                unchanged = set()
                if prepare_auth:
                    for record in changes:
                        prior = (
                            await preparation.execute(
                                text(
                                    "SELECT payload FROM portal_memberships WHERE tenant=:tenant AN"
                                    "D issuer=:issuer AND "
                                    "subject=:subject"
                                ),
                                {"tenant": record.tenant, "issuer": record.issuer, "subject": record.subject},
                            )
                        ).scalar_one_or_none()
                        if prior is not None and MembershipRecord.model_validate_json(prior) == record:
                            unchanged.add(record.principal_ref)
            if prepare_auth:
                for record in changes:
                    if record.principal_ref not in unchanged:
                        auth_changes[record.principal_ref] = await self.auth_source.prepare_membership(record)
        async with self.engine.begin() as db:
            await self._qualified(db)
            # Initial provisioning is an explicit reviewed administration action; no seed/grant.
            if expected_source_revision == 0:
                await db.execute(
                    text("""INSERT INTO portal_assignment_source
                    (tenant,source_revision,state,native_revision,designation_bytes)
                    VALUES(:tenant,0,'disabled',:native,:designation) ON CONFLICT(tenant) DO NOTHING"""),
                    dict(tenant=self.scope.tenant, native=self.initial_native_revision, designation=b""),
                )
            row = await self._locked(db)
            if row["source_revision"] != expected_source_revision:
                raise conflict()
            if row["state"] == "frozen":
                if bytes(row["designation_bytes"]) != designation:
                    raise conflict()
                return self._frozen(row)
            if (
                row["state"] == "disabled"
                and row["designation_bytes"]
                and bytes(row["designation_bytes"]) != designation
            ):
                raise conflict()
            next_revision = expected_source_revision + 1
            if next_revision >= 2**63:
                raise unavailable()
            publication_id = secrets.token_urlsafe(24)
            if row["state"] == "active":
                # Freeze commits BEFORE native disable; actual source facts remain unchanged.
                payload = canonicalize(
                    wire(
                        dict(
                            operation="disable",
                            expected_generation_digest=row["active_generation_digest"],
                            source_revision=next_revision,
                        )
                    )
                )
            else:
                guarded = await self._auth_source_required(db)
                if guarded:
                    records = await self._guarded_memberships(db, changes, auth_changes, applied_auth)
                else:
                    for record in changes:
                        prior = (
                            (
                                await db.execute(
                                    text(
                                        "SELECT payload FROM portal_memberships WHERE tenant=:tenant AND "
                                        "(principal_ref=:principal OR (issuer=:issuer AND subject=:subject)) "
                                        "FOR UPDATE"
                                    ),
                                    dict(
                                        tenant=record.tenant,
                                        principal=record.principal_ref,
                                        issuer=record.issuer,
                                        subject=record.subject,
                                    ),
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if len(prior) > 1:
                            raise conflict()
                        if prior:
                            old = MembershipRecord.model_validate_json(prior[0])
                            if (old.principal_ref, old.issuer, old.subject, old.audience) != (
                                record.principal_ref,
                                record.issuer,
                                record.subject,
                                record.audience,
                            ):
                                raise conflict()
                            if old != record and record.revision <= old.revision:
                                raise conflict()
                            if old == record:
                                continue
                        await db.execute(
                            text(
                                """INSERT INTO portal_memberships
                                (tenant,issuer,subject,principal_ref,payload)
                                VALUES(:tenant,:issuer,:subject,:principal,:payload)
                                ON CONFLICT(tenant,issuer,subject)
                                DO UPDATE SET payload=EXCLUDED.payload"""
                            ),
                            dict(
                                tenant=record.tenant,
                                issuer=record.issuer,
                                subject=record.subject,
                                principal=record.principal_ref,
                                payload=record.model_dump_json(),
                            ),
                        )
                    rows = (
                        (
                            await db.execute(
                                text(
                                    "SELECT payload FROM portal_memberships WHERE tenant=:tenant ORDER "
                                    "BY principal_ref FOR SHARE"
                                ),
                                {"tenant": self.scope.tenant},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    records = tuple(MembershipRecord.model_validate_json(raw) for raw in rows)
                members = tuple(
                    StaffMembership(
                        principal_ref=m.principal_ref,
                        issuer=m.issuer,
                        subject=m.subject,
                        membership_revision=m.revision,
                        audience="staff",
                        memberships=m.memberships,
                        subject_bindings=m.subject_bindings,
                        state="revoked" if m.revoked else "active",
                        reviewed_until=m.reviewed_until,
                    )
                    for m in records
                    if m.audience == "staff"
                )
                generation = GenerationPayload(
                    schema="human-staff-assignment-generation.v1",
                    tenant=self.scope.tenant,
                    environment=self.scope.environment,
                    engine_name=self.engine_name,
                    database_incarnation=self.database_incarnation,
                    source_revision=next_revision,
                    state="complete",
                    memberships=members,
                    membership_count=len(members),
                    membership_digest=digest(members),
                    policies=policy,
                    bindings=bindings,
                    resource_designations=constraints,
                    artifacts=self.artifacts,
                    valid_until=int(self.valid_until.timestamp()),
                )
                payload = canonicalize(wire(generation))
            await db.execute(
                text("""UPDATE portal_assignment_source SET source_revision=:revision,state='frozen',
                pending_publication_id=:publication,designation_bytes=:designation,source_bytes=:payload,
                owner_receipt=:owner,committed_at=clock_timestamp() WHERE tenant=:tenant"""),
                dict(
                    tenant=self.scope.tenant,
                    revision=next_revision,
                    publication=publication_id,
                    designation=designation,
                    payload=payload,
                    owner=canonicalize(wire(owner_receipt)),
                ),
            )
        # Signing may only consume this fresh, committed row.
        for change_id in applied_auth:
            await self.auth_source.finish_change(change_id)
        return await self.read_frozen(next_revision)

    def _frozen(self, row: dict[str, Any]) -> FrozenAssignmentSource:
        if row["state"] != "frozen" or not row["pending_publication_id"] or row["source_bytes"] is None:
            raise unavailable()
        raw = bytes(row["source_bytes"])
        parsed = strict_loads(raw)
        operation: Literal["disable", "replace"] = (
            "disable" if parsed.get("operation") == "disable" else "replace"
        )
        if operation == "replace":
            generation = parse_model(GenerationPayload, parsed)
            if generation.source_revision != row["source_revision"]:
                raise unavailable()
        return FrozenAssignmentSource(
            self.scope.tenant,
            row["pending_publication_id"],
            row["source_revision"],
            operation,
            raw,
            digest(parsed),
            parse_model(ArtifactPin, strict_loads(bytes(row["owner_receipt"]))),
            row["native_revision"],
            row["active_generation_digest"],
            row["committed_at"],
        )

    async def read_frozen(self, source_revision: int) -> FrozenAssignmentSource:
        async with self.engine.begin() as db:
            row = await self._locked(db)
            if row["source_revision"] != source_revision:
                raise conflict()
            return self._frozen(row)

    async def persist_request(
        self, frozen: FrozenAssignmentSource, publication: AssignmentPublication
    ) -> bytes:
        raw = canonicalize(wire(publication))
        async with self.engine.begin() as db:
            current = self._frozen(await self._locked(db))
            if (
                current != frozen
                or publication.tenant != frozen.tenant
                or publication.publication_id != frozen.publication_id
                or publication.expected_revision != frozen.expected_native_revision
                or publication.source.generation_digest != frozen.source_digest
                or publication.operation != frozen.operation
                or publication.expected_generation_digest != frozen.expected_generation_digest
            ):
                raise conflict()
            previous = (
                await db.execute(
                    text(
                        "SELECT request_bytes FROM portal_assignment_publications WHERE "
                        "tenant=:tenant AND publication_id=:id"
                    ),
                    dict(tenant=frozen.tenant, id=frozen.publication_id),
                )
            ).scalar_one_or_none()
            if previous is not None:
                if bytes(previous) != raw:
                    raise conflict()
                return bytes(previous)
            await db.execute(
                text("""INSERT INTO portal_assignment_publications
                (tenant,publication_id,source_revision,operation,source_bytes,source_digest,owner_receipt,request_bytes,expected_native_revision,delivery_state)
                VALUES(:tenant,:id,:revision,:operation,:source,:digest,:owner,:request,:native,'prepared')"""),
                dict(
                    tenant=frozen.tenant,
                    id=frozen.publication_id,
                    revision=frozen.source_revision,
                    operation=frozen.operation,
                    source=frozen.payload,
                    digest=frozen.source_digest,
                    owner=canonicalize(wire(frozen.owner_receipt)),
                    request=raw,
                    native=frozen.expected_native_revision,
                ),
            )
        return raw

    async def reconcile(self, publication_id: str) -> bytes:
        async with self.engine.begin() as db:
            await self._qualified(db)
            raw = (
                await db.execute(
                    text(
                        "SELECT request_bytes FROM portal_assignment_publications WHERE "
                        "tenant=:tenant AND publication_id=:id"
                    ),
                    dict(tenant=self.scope.tenant, id=publication_id),
                )
            ).scalar_one_or_none()
            if raw is None:
                raise unavailable()
            return bytes(raw)

    async def uncertain(self, publication_id: str) -> None:
        async with self.engine.begin() as db:
            await self._qualified(db)
            await db.execute(
                text(
                    "UPDATE portal_assignment_publications SET delivery_state='uncertain' WHERE "
                    "tenant=:tenant AND publication_id=:id AND delivery_state<>'acknowledged'"
                ),
                dict(tenant=self.scope.tenant, id=publication_id),
            )

    async def ack_native(self, publication_receipt: AssignmentPublicationReceipt) -> None:
        receipt = AssignmentPublicationReceipt.model_validate(publication_receipt)
        async with self.engine.begin() as db:
            row = await self._locked(db)
            publication = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_assignment_publications WHERE tenant=:tenant AND "
                            "publication_id=:id FOR UPDATE"
                        ),
                        dict(tenant=self.scope.tenant, id=receipt.publication_id),
                    )
                )
                .mappings()
                .first()
            )
            if publication is None or receipt.tenant != self.scope.tenant:
                raise unavailable()
            request = parse_model(AssignmentPublication, strict_loads(bytes(publication["request_bytes"])))
            expected_digest = (
                digest(request.generation)
                if request.operation == "replace"
                else request.expected_generation_digest
            )
            if (
                receipt.request_digest != digest(request)
                or receipt.operation != request.operation
                or receipt.source_revision != publication["source_revision"]
                or receipt.authority_revision != request.expected_revision + 1
                or receipt.generation_digest != expected_digest
                or receipt.state != ("active" if request.operation == "replace" else "disabled")
            ):
                raise unavailable()
            raw = canonicalize(wire(receipt))
            if publication["native_receipt"] is not None:
                if bytes(publication["native_receipt"]) != raw:
                    raise conflict()
                return
            if (
                row["state"] != "frozen"
                or row["pending_publication_id"] != receipt.publication_id
                or row["source_revision"] != receipt.source_revision
            ):
                raise conflict()
            await db.execute(
                text(
                    "UPDATE portal_assignment_publications SET "
                    "native_receipt=:receipt,delivery_state='acknowledged' WHERE tenant=:tenant AND "
                    "publication_id=:id"
                ),
                dict(tenant=self.scope.tenant, id=receipt.publication_id, receipt=raw),
            )
            await db.execute(
                text("""UPDATE portal_assignment_source SET state=:state,native_revision=:native,
                active_generation_digest=:digest,pending_publication_id=NULL,
                designation_bytes=CASE WHEN :state='active' THEN CAST('' AS bytea) ELSE designation_bytes END
                WHERE tenant=:tenant"""),
                dict(
                    tenant=self.scope.tenant,
                    state=receipt.state,
                    native=receipt.authority_revision,
                    digest=receipt.generation_digest,
                ),
            )

    async def prepare_receipt_disclosure(
        self,
        *,
        expected_source_revision: int,
        disclosure: ReceiptDisclosure,
        artifacts: tuple[Artifact, ...],
        owner_receipt: ArtifactPin,
    ) -> FrozenReceiptSource:
        from maezo.gateway.human.assignment_receipt import ReceiptDisclosure
        from maezo.gateway.human.projection import restore_command
        from maezo.portal.engine.assignment import HumanAssignmentCommand

        d = ReceiptDisclosure.model_validate(disclosure)
        owner = ArtifactPin.model_validate(owner_receipt)
        if (
            owner not in self.owner_receipts
            or d.policy.review_receipt != owner
            or ArtifactPin(artifact_ref=d.policy.policy_ref, digest=digest(d.policy))
            not in self.receipt_policy_pins
        ):
            raise unavailable()
        payload = canonicalize(wire(dict(disclosure=d, artifacts=artifacts)))
        async with self.engine.begin() as db:
            tenant = await self._locked(db)
            if tenant["state"] != "active":
                raise unavailable()
            old = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_assignment_receipt_source WHERE tenant=:tenant AND "
                            "task_id=:task AND command_id=:command FOR UPDATE"
                        ),
                        dict(
                            tenant=self.scope.tenant, task=d.identity.task_id, command=d.identity.command_id
                        ),
                    )
                )
                .mappings()
                .first()
            )
            if (0 if old is None else old["source_revision"]) != expected_source_revision:
                raise conflict()
            if old is not None and old["state"] == "frozen":
                if bytes(old["source_bytes"]) != payload:
                    raise conflict()
                return self._frozen_receipt(dict(old))
            raw = (
                await db.execute(
                    text(
                        "SELECT canonical_payload FROM human_command_outbox WHERE tenant=:tenant AND "
                        "task_id=:task AND command_id=:command AND payload_digest=:digest"
                    ),
                    dict(
                        tenant=self.scope.tenant,
                        task=d.identity.task_id,
                        command=d.identity.command_id,
                        digest=d.identity.payload_digest,
                    ),
                )
            ).scalar_one_or_none()
            if raw is None:
                raise unavailable()
            c = restore_command(bytes(raw))
            if (
                not isinstance(c, HumanAssignmentCommand)
                or c.principal_ref != d.identity.principal_ref
                or c.workload_ref != d.identity.workload_ref
                or d.identity.tenant != self.scope.tenant
                or any(
                    str(getattr(c, k)) != str(getattr(d.linkage, k))
                    for k in (
                        "task_id",
                        "process_definition_id",
                        "process_definition_key",
                        "process_definition_version",
                        "process_definition_digest",
                        "task_definition_key",
                        "binding_ref",
                        "binding_version",
                        "binding_digest",
                    )
                )
            ):
                raise unavailable()
            revision = (
                await db.execute(
                    text(
                        "SELECT COALESCE(max(source_revision),0)+1 FROM "
                        "portal_assignment_receipt_source WHERE tenant=:tenant"
                    ),
                    {"tenant": self.scope.tenant},
                )
            ).scalar_one()
            if revision >= 2**63:
                raise unavailable()
            publication = secrets.token_urlsafe(24)
            await db.execute(
                text("""INSERT INTO portal_assignment_receipt_source
                (tenant,task_id,command_id,identity_digest,source_revision,state,pending_publication_id,source_bytes,owner_receipt,expected_native_revision)
                VALUES(:tenant,:task,:command,:identity,:revision,'frozen',:publication,:payload,:owner,:native)
                ON CONFLICT(tenant,task_id,command_id) DO UPDATE SET source_revision=EXCLUDED.source_revision,
                state='frozen',pending_publication_id=EXCLUDED.pending_publication_id,source_bytes=EXCLUDED.source_bytes,
                owner_receipt=EXCLUDED.owner_receipt,expected_native_revision=EXCLUDED.expected_native_revision,
                committed_at=clock_timestamp()"""),
                dict(
                    tenant=self.scope.tenant,
                    task=d.identity.task_id,
                    command=d.identity.command_id,
                    identity=digest(d.identity),
                    revision=revision,
                    publication=publication,
                    payload=payload,
                    owner=canonicalize(wire(owner)),
                    native=tenant["native_revision"],
                ),
            )
        return await self.read_frozen_receipt(d.identity.task_id, d.identity.command_id, revision)

    def _frozen_receipt(self, row: dict[str, Any]) -> FrozenReceiptSource:
        if row["state"] != "frozen":
            raise unavailable()
        raw = bytes(row["source_bytes"])
        return FrozenReceiptSource(
            self.scope.tenant,
            row["pending_publication_id"],
            row["source_revision"],
            raw,
            digest(strict_loads(raw)),
            parse_model(ArtifactPin, strict_loads(bytes(row["owner_receipt"]))),
            row["expected_native_revision"],
            row["committed_at"],
            row["task_id"],
            row["command_id"],
        )

    async def read_frozen_receipt(
        self, task_id: str, command_id: str, source_revision: int
    ) -> FrozenReceiptSource:
        async with self.engine.begin() as db:
            await self._locked(db)
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_assignment_receipt_source WHERE tenant=:tenant AND "
                            "task_id=:task AND command_id=:command FOR SHARE"
                        ),
                        dict(tenant=self.scope.tenant, task=task_id, command=command_id),
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["source_revision"] != source_revision:
                raise conflict()
            return self._frozen_receipt(dict(row))

    async def persist_receipt_request(
        self, frozen: FrozenReceiptSource, publication: ReceiptDisclosurePublication
    ) -> bytes:
        from maezo.gateway.human.assignment_receipt import ReceiptDisclosurePublication

        p = ReceiptDisclosurePublication.model_validate(publication)
        i = p.disclosure.identity
        raw = canonicalize(wire(p))
        async with self.engine.begin() as db:
            await self._locked(db)
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_assignment_receipt_source WHERE tenant=:tenant AND "
                            "task_id=:task AND command_id=:command FOR UPDATE"
                        ),
                        dict(tenant=self.scope.tenant, task=i.task_id, command=i.command_id),
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or self._frozen_receipt(dict(row)) != frozen
                or p.publication_id != frozen.publication_id
                or p.source.generation_digest != frozen.source_digest
                or p.expected_revision != frozen.expected_native_revision
            ):
                raise conflict()
            prior = (
                await db.execute(
                    text(
                        "SELECT request_bytes FROM portal_assignment_publications WHERE "
                        "tenant=:tenant AND publication_id=:id"
                    ),
                    dict(tenant=self.scope.tenant, id=p.publication_id),
                )
            ).scalar_one_or_none()
            if prior is not None:
                if bytes(prior) != raw:
                    raise conflict()
                return raw
            await db.execute(
                text("""INSERT INTO portal_assignment_publications(
                tenant,publication_id,source_revision,operation,
                source_bytes,source_digest,owner_receipt,request_bytes,expected_native_revision,delivery_state)
                VALUES(:tenant,:id,:revision,'receipt',:source,:digest,:owner,:request,:native,'prepared')"""),
                dict(
                    tenant=self.scope.tenant,
                    id=p.publication_id,
                    revision=frozen.source_revision,
                    source=frozen.payload,
                    digest=frozen.source_digest,
                    owner=canonicalize(wire(frozen.owner_receipt)),
                    request=raw,
                    native=p.expected_revision,
                ),
            )
        return raw

    async def ack_receipt(self, receipt: ReceiptDisclosurePublicationReceipt) -> None:
        from maezo.gateway.human.assignment_receipt import (
            ReceiptDisclosurePublication,
            ReceiptDisclosurePublicationReceipt,
        )

        r = ReceiptDisclosurePublicationReceipt.model_validate(receipt)
        async with self.engine.begin() as db:
            await self._locked(db)
            pub = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_assignment_publications WHERE tenant=:tenant AND "
                            "publication_id=:id FOR UPDATE"
                        ),
                        dict(tenant=self.scope.tenant, id=r.publication_id),
                    )
                )
                .mappings()
                .first()
            )
            if pub is None or pub["operation"] != "receipt":
                raise unavailable()
            p = parse_model(ReceiptDisclosurePublication, strict_loads(bytes(pub["request_bytes"])))
            i = p.disclosure.identity
            if (
                r.tenant != self.scope.tenant
                or r.request_digest != digest(p)
                or r.disclosure_digest != digest(p.disclosure)
                or r.source_revision != p.source.source.source_revision
                or r.authority_revision != p.expected_revision + 1
                or r.state != p.disclosure.state
            ):
                raise unavailable()
            raw = canonicalize(wire(r))
            if pub["native_receipt"] is not None:
                if bytes(pub["native_receipt"]) != raw:
                    raise conflict()
                return
            updated = (
                await db.execute(
                    text(
                        "UPDATE portal_assignment_receipt_source SET state=:state WHERE "
                        "tenant=:tenant AND task_id=:task AND command_id=:command AND state='frozen' "
                        "AND source_revision=:revision AND pending_publication_id=:id RETURNING "
                        "source_revision"
                    ),
                    dict(
                        tenant=self.scope.tenant,
                        task=i.task_id,
                        command=i.command_id,
                        state=r.state,
                        revision=r.source_revision,
                        id=r.publication_id,
                    ),
                )
            ).scalar_one_or_none()
            if updated is None:
                raise conflict()
            await db.execute(
                text(
                    "UPDATE portal_assignment_publications SET "
                    "native_receipt=:raw,delivery_state='acknowledged' WHERE tenant=:tenant AND "
                    "publication_id=:id"
                ),
                dict(tenant=self.scope.tenant, id=r.publication_id, raw=raw),
            )
            await db.execute(
                text("UPDATE portal_assignment_source SET native_revision=:revision WHERE tenant=:tenant"),
                dict(tenant=self.scope.tenant, revision=r.authority_revision),
            )


class StaffAssignmentSourceSigner:
    """Admin-only signing service; accepts a committed revision, never supplied facts."""

    def __init__(
        self,
        *,
        administration: PostgresStaffAssignmentAdministration,
        signing: AssignmentSigningLease,
        owner_ref: str,
        source_ref: str,
    ) -> None:
        from maezo.gateway.human.credentials import AssignmentSigningLease

        if (
            not isinstance(signing, AssignmentSigningLease)
            or signing.purpose != "human-staff-assignment-source"
            or signing.scope.tenant != administration.scope.tenant
            or signing.scope.environment != administration.scope.environment
        ):
            raise unavailable()
        self._administration, self._signing = administration, signing
        self.owner_ref, self.source_ref = owner_ref, source_ref

    async def attest(self, source_revision: int) -> AdminSourceAttestation:
        import base64

        from maezo.gateway.human.assignment_policy import AdminSourceAttestation
        from maezo.gateway.human.read_profile import SourceProvenance

        frozen = await self._administration.read_frozen(source_revision)
        now = self._signing.guard()
        until = min(self._administration.valid_until, self._signing.not_after)
        if not frozen.committed_at <= now < until:
            raise unavailable()
        provenance = SourceProvenance(
            publisher_ref=self.owner_ref,
            source_ref=self.source_ref,
            source_revision=frozen.source_revision,
            source_digest=frozen.source_digest,
            receipt_ref=frozen.publication_id,
            observed_at=frozen.committed_at,
            valid_until=until,
        )
        value = dict(
            schema="human-staff-assignment-source.v1",
            source=wire(provenance),
            tenant=frozen.tenant,
            environment=self._administration.scope.environment,
            engine_name=self._administration.engine_name,
            database_incarnation=self._administration.database_incarnation,
            source_key_id=self._signing.key_id,
            algorithm="Ed25519",
            generation_digest=frozen.source_digest,
        )
        value["signature"] = (
            base64.urlsafe_b64encode(self._signing.sign(canonicalize(value))).rstrip(b"=").decode("ascii")
        )
        # Re-read committed state after signing; the handle cannot attest an unfenced mutation.
        if await self._administration.read_frozen(source_revision) != frozen:
            raise conflict()
        return parse_model(AdminSourceAttestation, value)


class ReceiptDisclosureSourceSigner:
    """Distinct admin-only receipt source signer; reads committed reviewed DB facts."""

    def __init__(
        self,
        *,
        administration: PostgresStaffAssignmentAdministration,
        signing: AssignmentSigningLease,
        owner_ref: str,
        source_ref: str,
        staff_source_fingerprint: str,
    ) -> None:
        if (
            signing.purpose != "human-staff-assignment-source"
            or signing.fingerprint == staff_source_fingerprint
            or signing.scope.tenant != administration.scope.tenant
            or signing.scope.environment != administration.scope.environment
        ):
            raise unavailable()
        self._administration, self._signing = administration, signing
        self.owner_ref, self.source_ref = owner_ref, source_ref

    async def attest(self, task_id: str, command_id: str, source_revision: int) -> AdminSourceAttestation:
        import base64

        from maezo.gateway.human.read_profile import SourceProvenance

        frozen = await self._administration.read_frozen_receipt(task_id, command_id, source_revision)
        now = self._signing.guard()
        until = min(self._administration.valid_until, self._signing.not_after)
        if not frozen.committed_at <= now < until:
            raise unavailable()
        provenance = SourceProvenance(
            publisher_ref=self.owner_ref,
            source_ref=self.source_ref,
            source_revision=source_revision,
            source_digest=frozen.source_digest,
            receipt_ref=frozen.publication_id,
            observed_at=frozen.committed_at,
            valid_until=until,
        )
        value = dict(
            schema="human-staff-assignment-source.v1",
            source=wire(provenance),
            tenant=frozen.tenant,
            environment=self._administration.scope.environment,
            engine_name=self._administration.engine_name,
            database_incarnation=self._administration.database_incarnation,
            source_key_id=self._signing.key_id,
            algorithm="Ed25519",
            generation_digest=frozen.source_digest,
        )
        value["signature"] = (
            base64.urlsafe_b64encode(self._signing.sign(canonicalize(value))).rstrip(b"=").decode("ascii")
        )
        if await self._administration.read_frozen_receipt(task_id, command_id, source_revision) != frozen:
            raise conflict()
        return parse_model(AdminSourceAttestation, value)
