"""ADR-0049 D6: one tenant transaction owns audit intent and immutable outbox.

The pool is trusted composition-root infrastructure. Every acquire uses SET LOCAL
inside the transaction (DL-0017); public schema fallback is never used. No HTTP,
new connection in the audit operation, hash algorithm, chain head cache or timestamp
ordering exists here. The original PostgresAuditSink owns each chain append.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, _row_to_record, schema_for_tenant
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.assignment import HumanAssignmentCommand
from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import HumanCommand

from .decision import ClassifiedDecision, DecisionAdmission, PendingDecisionAdmission
from .models import AuthorizedAssignment, AuthorizedGovernedAssignment, PendingAdmission, Scope
from .ports import DurableAdmission, GovernedAssignmentAdmission
from .projection import (
    EvidenceReference,
    EvidenceReferenceSource,
    GovernedEvidenceReferenceSource,
    project_assignment,
    project_governed_assignment,
    restore_command,
    verify_engine_receipt,
)
from .receipt import PublicReceipt, ReceiptStore, parse_public_receipt


class HumanOutboxError(RuntimeError):
    """Static technical error; never expose SQL, payload, issuer or driver diagnostics."""


class CommandConflictError(HumanOutboxError):
    pass


class LeaseLostError(HumanOutboxError):
    pass


@dataclass(frozen=True)
class DeliveryLease:
    scope: Scope
    command: HumanCommand | HumanDecisionCommand
    lease_id: str
    fence: int
    lease_until: datetime


def _positive_seconds(value: int) -> int:
    # Operational intervals only, not business/regulatory timers or data retention.
    if type(value) is not int or not 0 < value <= 2**31 - 1:
        raise HumanOutboxError("explicit positive operational interval required")
    return value


def _audit(command: HumanCommand | HumanDecisionCommand, phase: str, details: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        agent_id=command.workload_ref,
        tenant_id=command.tenant,
        agent_version=command.schema,
        action=f"human_command.{phase}",
        decision="REQUIRE_HUMAN",
        details={
            "principal_kind": "human",
            "principal_ref": command.principal_ref,
            "workload_ref": command.workload_ref,
            "task_id": command.task_id,
            "command_id": command.command_id,
            "payload_digest": command.digest,
            "audit_intent_ref": command.audit_intent_ref,
            "operation": command.operation,
            **details,
        },
    )


class PostgresHumanOutbox(ReceiptStore):
    def __init__(self, *, scope: Scope, pool: asyncpg.Pool) -> None:
        self.scope = Scope.model_validate(scope)
        self._schema = schema_for_tenant(scope.tenant)
        self._pool = pool
        self._audit = PostgresAuditSink("", scope.tenant, pool=pool)

    @asynccontextmanager
    async def _transaction(self, *, audit_lock: bool = False) -> AsyncIterator[asyncpg.Connection]:
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(f'SET LOCAL search_path TO "{self._schema}"')
                if await conn.fetchval("SELECT current_schema()") != self._schema:
                    raise HumanOutboxError("tenant transaction unavailable")
                if audit_lock:
                    # Same tenant lock as emit_once_on; always BEFORE our row locks.
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", self.scope.tenant)
                yield conn
        except HumanOutboxError:
            raise
        except Exception:
            raise HumanOutboxError("human durable transaction unavailable") from None

    def _scope(self, command: HumanCommand | HumanDecisionCommand) -> None:
        if (
            command.tenant != self.scope.tenant
            or command.workload_ref != self.scope.workload_ref
            or command.principal_ref == self.scope.workload_ref
            or (
                command.operation not in ("claim", "release")
                and not isinstance(command, (HumanDecisionCommand, HumanAssignmentCommand))
            )
            or (isinstance(command, HumanDecisionCommand) and command.environment != self.scope.environment)
        ):
            raise HumanOutboxError("human command scope mismatch")

    async def _verify_link(
        self,
        conn: asyncpg.Connection,
        command: HumanCommand | HumanDecisionCommand,
        phase: str,
        record_hash: str,
        details: dict[str, Any],
    ) -> None:
        rows = await conn.fetch("SELECT * FROM audit_chain WHERE record_hash=$1", record_hash)
        if len(rows) != 1:
            raise HumanOutboxError("human audit linkage unavailable")
        row = rows[0]
        record = _row_to_record(row)
        expected = _audit(command, phase, details)
        if (
            record._compute_hash() != record_hash
            or record.compute_input_hash() != row["input_hash"]
            or any(
                getattr(record, key) != getattr(expected, key)
                for key in ("tenant_id", "agent_id", "agent_version", "action", "decision", "details")
            )
        ):
            raise HumanOutboxError("human audit linkage unavailable")
        claimed = await conn.fetchval(
            "SELECT record_hash FROM audit_emit_dedup WHERE tenant=$1 AND dedup_key=$2",
            self.scope.tenant,
            f"human:{phase}:" + command.audit_intent_ref,
        )
        if claimed != record_hash:
            raise HumanOutboxError("human audit linkage unavailable")

    def _command(self, row: Any) -> HumanCommand | HumanDecisionCommand:
        command = restore_command(bytes(row["canonical_payload"]))
        self._scope(command)
        if (
            row["environment"] != self.scope.environment
            or any(
                row[name] != getattr(command, name)
                for name in (
                    "tenant",
                    "task_id",
                    "command_id",
                    "workload_ref",
                    "principal_ref",
                    "principal_issuer",
                    "principal_subject",
                    "audit_intent_ref",
                )
            )
            or row["payload_digest"] != command.digest
        ):
            raise HumanOutboxError("human command linkage unavailable")
        return command

    async def persist(
        self, command: HumanCommand | HumanDecisionCommand, *, evidence_valid_until: datetime
    ) -> PendingAdmission:
        self._scope(command)
        if evidence_valid_until.tzinfo is None or evidence_valid_until <= datetime.now(UTC):
            raise HumanOutboxError("evidence validity unavailable")
        if len(command.canonical) > 65536:
            raise HumanOutboxError("human command size unavailable")
        async with self._transaction(audit_lock=True) as conn:
            if isinstance(command, HumanAssignmentCommand):
                source = await conn.fetchrow(
                    "SELECT state,source_revision,active_generation_digest,pending_publication_id "
                    "FROM portal_assignment_source WHERE tenant=$1 FOR SHARE",
                    self.scope.tenant,
                )
                if (
                    source is None
                    or source["state"] != "active"
                    or source["pending_publication_id"] is not None
                    or str(source["source_revision"]) != command.source_revision
                    or source["active_generation_digest"] != command.generation_digest
                ):
                    raise HumanOutboxError("assignment source frozen or changed")
            audit = await self._audit.emit_once_on(
                conn, _audit(command, "intent", {}), dedup_key="human:intent:" + command.audit_intent_ref
            )
            row = await conn.fetchrow(
                "SELECT * FROM human_command_outbox WHERE tenant=$1 AND task_id=$2 AND "
                "command_id=$3 FOR UPDATE",
                self.scope.tenant,
                command.task_id,
                command.command_id,
            )
            if row is not None:
                if self._command(row).canonical != command.canonical:
                    raise CommandConflictError("human command identity conflict")
                if not audit.deduped or row["audit_intent_hash"] != audit.record_hash:
                    raise HumanOutboxError("human audit linkage unavailable")
                await self._verify_link(conn, command, "intent", audit.record_hash, {})
            else:
                if audit.deduped:
                    raise HumanOutboxError("human audit intent orphaned")
                row = await conn.fetchrow(
                    "INSERT INTO human_command_outbox "
                    "(tenant,task_id,command_id,environment,workload_ref,"
                    "principal_ref,principal_issuer,principal_subject,payload_digest,canonical_payload,"
                    "audit_intent_ref,audit_intent_hash,outbox_ref,transaction_ref) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING *",
                    command.tenant,
                    command.task_id,
                    command.command_id,
                    self.scope.environment,
                    command.workload_ref,
                    command.principal_ref,
                    command.principal_issuer,
                    command.principal_subject,
                    command.digest,
                    command.canonical,
                    command.audit_intent_ref,
                    audit.record_hash,
                    str(uuid4()),
                    str(uuid4()),
                )
                await conn.execute(
                    "INSERT INTO human_command_delivery (tenant,task_id,command_id) VALUES ($1,$2,$3)",
                    command.tenant,
                    command.task_id,
                    command.command_id,
                )
            if evidence_valid_until <= datetime.now(UTC):
                raise HumanOutboxError("evidence validity unavailable")
            outbox_ref, transaction_ref = row["outbox_ref"], row["transaction_ref"]
        # Construct the acknowledgement only after COMMIT succeeds. This timestamp
        # observes confirmed durability, not a fabricated PostgreSQL commit instant.
        return PendingAdmission(
            schema_version=1,
            tenant=command.tenant,
            task_id=command.task_id,
            command_id=command.command_id,
            principal_ref=command.principal_ref,
            workload_ref=command.workload_ref,
            audit_intent_ref=command.audit_intent_ref,
            outbox_ref=outbox_ref,
            transaction_ref=transaction_ref,
            committed_at=datetime.now(UTC),
        )

    async def claim(self, *, lease_seconds: int) -> DeliveryLease | None:
        lease_seconds = _positive_seconds(lease_seconds)
        async with self._transaction() as conn:
            row = await conn.fetchrow(
                "SELECT d.* FROM human_command_delivery d JOIN human_command_outbox o USING "
                "(tenant,task_id,command_id) "
                "WHERE d.tenant=$1 AND o.environment=$2 AND o.workload_ref=$3 AND d.status='pending' "
                "AND d.next_attempt_at <= clock_timestamp() "
                "AND (d.lease_until IS NULL OR d.lease_until <= clock_timestamp()) "
                "ORDER BY d.next_attempt_at,d.task_id,d.command_id FOR UPDATE OF d SKIP LOCKED LIMIT 1",
                self.scope.tenant,
                self.scope.environment,
                self.scope.workload_ref,
            )
            if row is None:
                return None
            immutable = await conn.fetchrow(
                "SELECT * FROM human_command_outbox WHERE tenant=$1 AND task_id=$2 AND command_id=$3",
                row["tenant"],
                row["task_id"],
                row["command_id"],
            )
            command = self._command(immutable)
            await self._verify_link(conn, command, "intent", immutable["audit_intent_hash"], {})
            token = str(uuid4())
            claimed = await conn.fetchrow(
                "UPDATE human_command_delivery SET "
                "lease_id=$4,lease_until=clock_timestamp()+$5*interval '1 second',"
                "fence=fence+1 WHERE tenant=$1 AND task_id=$2 AND command_id=$3 RETURNING "
                "fence,lease_until",
                command.tenant,
                command.task_id,
                command.command_id,
                token,
                lease_seconds,
            )
            lease = DeliveryLease(self.scope, command, token, claimed["fence"], claimed["lease_until"])
        return lease

    async def _locked_lease(self, conn: asyncpg.Connection, lease: DeliveryLease) -> Any:
        self._scope(lease.command)
        if lease.scope != self.scope:
            raise LeaseLostError("human delivery lease unavailable")
        c = lease.command
        row = await conn.fetchrow(
            "SELECT * FROM human_command_delivery WHERE tenant=$1 AND task_id=$2 AND command_id=$3 "
            "AND status='pending' AND lease_id=$4 AND fence=$5 AND "
            "lease_until>clock_timestamp() FOR UPDATE",
            c.tenant,
            c.task_id,
            c.command_id,
            lease.lease_id,
            lease.fence,
        )
        if row is None:
            raise LeaseLostError("human delivery lease unavailable")
        original = await conn.fetchrow(
            "SELECT * FROM human_command_outbox WHERE tenant=$1 AND task_id=$2 AND command_id=$3",
            c.tenant,
            c.task_id,
            c.command_id,
        )
        if self._command(original).canonical != c.canonical:
            raise HumanOutboxError("human command linkage unavailable")
        await self._verify_link(conn, c, "intent", original["audit_intent_hash"], {})
        return row

    async def require_lease(self, lease: DeliveryLease) -> None:
        async with self._transaction() as conn:
            await self._locked_lease(conn, lease)

    async def retry_later(self, lease: DeliveryLease, *, retry_seconds: int) -> None:
        retry_seconds = _positive_seconds(retry_seconds)
        async with self._transaction() as conn:
            await self._locked_lease(conn, lease)
            c = lease.command
            updated = await conn.fetchval(
                "UPDATE human_command_delivery SET lease_id=NULL,lease_until=NULL,"
                "next_attempt_at=clock_timestamp()+$6*interval '1 second' "
                "WHERE tenant=$1 AND task_id=$2 AND command_id=$3 AND lease_id=$4 AND fence=$5 "
                "AND lease_until>clock_timestamp() RETURNING command_id",
                c.tenant,
                c.task_id,
                c.command_id,
                lease.lease_id,
                lease.fence,
                retry_seconds,
            )
            if updated is None:
                raise LeaseLostError("human delivery lease unavailable")

    async def finish(
        self,
        lease: DeliveryLease,
        *,
        receipt: bytes | None = None,
        conflict: Literal["REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"] | None = None,
    ) -> None:
        c = lease.command
        if (receipt is None) == (conflict is None):
            raise HumanOutboxError("human result unavailable")
        if receipt is not None:
            verified = verify_engine_receipt(receipt, c)
            details = {
                "status": "committed",
                "engine_receipt_ref": verified.engine_receipt_ref,
                "engine_recorded_at": verified.recorded_at,
                "engine_receipt_digest": hashlib.sha256(receipt).hexdigest(),
            }
        else:
            if conflict not in ("REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"):
                raise HumanOutboxError("human result unavailable")
            details = {"status": "conflict", "technical_code": conflict}
        async with self._transaction(audit_lock=True) as conn:
            await self._locked_lease(conn, lease)
            audited = await self._audit.emit_once_on(
                conn, _audit(c, "result", details), dedup_key="human:result:" + c.audit_intent_ref
            )
            if audited.deduped:
                raise HumanOutboxError("human result audit orphaned")
            updated = await conn.fetchval(
                "UPDATE human_command_delivery SET status=$6,engine_receipt=$7,technical_code=$8,"
                "audit_result_hash=$9,lease_id=NULL,lease_until=NULL "
                "WHERE tenant=$1 AND task_id=$2 AND command_id=$3 AND status='pending' "
                "AND lease_id=$4 AND fence=$5 AND lease_until>clock_timestamp() RETURNING command_id",
                c.tenant,
                c.task_id,
                c.command_id,
                lease.lease_id,
                lease.fence,
                details["status"],
                receipt,
                conflict,
                audited.record_hash,
            )
            if updated is None:
                raise LeaseLostError("human delivery lease unavailable")
            if lease.lease_until <= datetime.now(UTC):
                raise LeaseLostError("human delivery lease unavailable")

    async def read_owned(self, principal: HumanPrincipal, task_id: str, command_id: str) -> PublicReceipt:
        p = HumanPrincipal.model_validate(principal)
        if p.tenant != self.scope.tenant:
            raise HumanOutboxError("human receipt unavailable")
        async with self._transaction() as conn:
            row = await conn.fetchrow(
                "SELECT o.*,d.status,d.engine_receipt,d.technical_code,d.audit_result_hash "
                "FROM human_command_outbox o JOIN human_command_delivery d USING "
                "(tenant,task_id,command_id) "
                "WHERE o.tenant=$1 AND o.task_id=$2 AND o.command_id=$3 AND o.principal_ref=$4 "
                "AND o.principal_issuer=$5 AND o.principal_subject=$6 AND o.environment=$7 "
                "AND o.workload_ref=$8",
                self.scope.tenant,
                task_id,
                command_id,
                p.principal_ref,
                p.issuer,
                p.subject,
                self.scope.environment,
                self.scope.workload_ref,
            )
            if row is None:
                raise HumanOutboxError("human receipt unavailable")
            c = self._command(row)
            await self._verify_link(conn, c, "intent", row["audit_intent_hash"], {})
            result: dict[str, Any] = dict(
                tenant=c.tenant,
                task_id=c.task_id,
                command_id=c.command_id,
                payload_digest=c.digest,
                principal_ref=c.principal_ref,
                workload_ref=c.workload_ref,
                status=row["status"],
                audit_intent_ref=c.audit_intent_ref,
                audit_intent_hash=row["audit_intent_hash"],
                audit_result_ref=row["audit_result_hash"],
                technical_code=row["technical_code"],
            )
            if isinstance(c, HumanDecisionCommand):
                result.update(schema_version="human-public-receipt.v2", operation="decision")
            if isinstance(c, HumanAssignmentCommand):
                from maezo.portal.engine.assignment import GOVERNED_FIELDS

                result.update({field: getattr(c, field) for field in GOVERNED_FIELDS})
                result.update(
                    schema_version="human-public-assignment-receipt.v1",
                    operation=c.operation,
                    command_schema=c.schema,
                    prior_assignee_ref=None,
                    resulting_assignee_ref=None,
                    assignment_disposition=None,
                    engine_receipt_ref=None,
                    engine_recorded_at=None,
                    consumed_task_revision=None,
                    resulting_task_revision=None,
                )
            if row["status"] == "committed":
                raw = bytes(row["engine_receipt"])
                verified = verify_engine_receipt(raw, c)
                await self._verify_link(
                    conn,
                    c,
                    "result",
                    row["audit_result_hash"],
                    {
                        "status": "committed",
                        "engine_receipt_ref": verified.engine_receipt_ref,
                        "engine_recorded_at": verified.recorded_at,
                        "engine_receipt_digest": hashlib.sha256(raw).hexdigest(),
                    },
                )
                if isinstance(c, HumanAssignmentCommand):
                    result.update(
                        {
                            field: getattr(verified, field)
                            for field in (
                                "prior_assignee_ref",
                                "resulting_assignee_ref",
                                "assignment_disposition",
                            )
                        }
                    )
                result.update(
                    engine_receipt_ref=verified.engine_receipt_ref,
                    engine_recorded_at=verified.engine_recorded_at,
                    consumed_task_revision=verified.consumed_task_revision,
                    resulting_task_revision=verified.resulting_task_revision,
                )
            elif row["status"] == "conflict":
                await self._verify_link(
                    conn,
                    c,
                    "result",
                    row["audit_result_hash"],
                    {"status": "conflict", "technical_code": row["technical_code"]},
                )
            return parse_public_receipt(result)


class PostgresHumanAdmission(DurableAdmission):
    def __init__(self, outbox: PostgresHumanOutbox, evidence: EvidenceReferenceSource) -> None:
        if evidence.scope != outbox.scope:
            raise HumanOutboxError("human evidence scope mismatch")
        self.scope = outbox.scope
        self._outbox = outbox
        self._evidence = evidence

    async def admit(self, command: AuthorizedAssignment) -> PendingAdmission:
        value = AuthorizedAssignment.model_validate(command)
        if value.scope != self.scope:
            raise HumanOutboxError("human admission scope mismatch")
        reference = EvidenceReference.model_validate(await self._evidence.current_reference(value))
        wire = project_assignment(value, reference)
        return await self._outbox.persist(wire, evidence_valid_until=reference.valid_until)


class PostgresGovernedAssignmentAdmission(GovernedAssignmentAdmission):
    def __init__(self, outbox: PostgresHumanOutbox, evidence: GovernedEvidenceReferenceSource) -> None:
        if evidence.scope != outbox.scope:
            raise HumanOutboxError("human evidence scope mismatch")
        self.scope, self._outbox, self._evidence = outbox.scope, outbox, evidence

    async def admit(self, command: AuthorizedGovernedAssignment) -> PendingAdmission:
        value = AuthorizedGovernedAssignment.model_validate(command)
        if value.scope != self.scope:
            raise HumanOutboxError("human admission scope mismatch")
        reference = EvidenceReference.model_validate(await self._evidence.current_reference(value))
        projected = project_governed_assignment(value, reference)
        deadline = min(
            value.valid_until,
            value.read_context.context.valid_until,
            value.read_context.task.valid_until,
            value.authority.valid_until,
            reference.valid_until,
        )
        return await self._outbox.persist(projected, evidence_valid_until=deadline)


class PostgresDecisionAdmission(DecisionAdmission):
    """D3 carrier -> existing single tenant audit/outbox TX, without engine dispatch.

    Requires the separately owned gateway decision module in the final composition.
    No local persistence fallback or dependency inference is permitted.
    """

    def __init__(self, outbox: PostgresHumanOutbox) -> None:
        self.scope = outbox.scope
        self._outbox = outbox

    async def admit(self, command: ClassifiedDecision, *, valid_until: datetime) -> PendingDecisionAdmission:

        try:
            if not isinstance(command, ClassifiedDecision):
                raise HumanOutboxError("human decision shape unavailable")
            value = ClassifiedDecision.model_validate(command.model_dump(by_alias=True))
            if value.scope != self.scope:
                raise HumanOutboxError("human decision admission scope mismatch")
            wire = HumanDecisionCommand(value.canonical)
            admitted = await self._outbox.persist(wire, evidence_valid_until=valid_until)
            return PendingDecisionAdmission(
                **admitted.model_dump(), payload_digest=wire.digest, request_digest=value.request_digest
            )
        except Exception:
            # Includes uncertain commit: retry identical identity, never infer rollback.
            raise HumanOutboxError("human decision admission unavailable") from None
