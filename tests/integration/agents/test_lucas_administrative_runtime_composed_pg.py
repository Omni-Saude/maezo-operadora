"""R2-v2 composed PG mechanics, twenty original controls plus seven reply controls.

Actual kernel/admission/PEP/audit/DUR3/adapter/full driver/runtime/typed roots,
AsyncPostgresSaver and journal. Explicit synthetic owner registries qualify only
technical fixture issuance, never providers/native authority/human acts/business
completion. ROOT owns the sole PG lane. No engine call/mock, skip or xfail.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA, AdministrativeHandoff
from maezo.agents.lucas.administrative.runtime import (
    RUNTIME_STIMULUS_SCHEMA,
    RuntimeCheckpointSnapshot,
    RuntimeResumeStimulus,
    RuntimeTurnStimulus,
)
from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, schema_for_tenant
from maezo.gateway.capabilities.admission import (
    AdmissionBinding,
    AdmissionDeniedError,
    CapabilityAdmission,
    VerifiedAuthority,
    VerifiedCurrentness,
    VerifiedSourceResult,
    result_digest,
)
from maezo.gateway.capabilities.durability.models import (
    CommandDescriptor,
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
    DispatchEvidence,
    JournalCallTechnicalStatus,
    JournalRefusalReason,
    OutboxAckEvidence,
    OutboxDescriptor,
    OutboxSnapshot,
    OutboxTechnicalKind,
    OutboxTechnicalState,
    PreDispatchRefusal,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.durability.postgres import PostgresDurabilityJournal
from maezo.gateway.capabilities.durable_execution import (
    DurableCapabilityExecutor,
    PreparedResultCommit,
    QualifiedInbox,
    QualifiedLookup,
    RestoredCommand,
    SourceReadEvidence,
    admission_binding_digest,
)
from maezo.gateway.capabilities.journeys.contracts import (
    CurrentJourneyTurn,
    JourneyBinding,
    JourneyContractError,
    JourneyDispatchOutcome,
    JourneyEffectGuard,
    JourneyInvocationCheckpoint,
    PreparedCapabilityAction,
    VerifiedJourneyContinuation,
    VerifiedJourneyEffectCurrentness,
    action_digest,
    effect_authority_digest,
)
from maezo.gateway.capabilities.journeys.driver import CapabilityServiceExecutionAdapter, JourneyDriver
from maezo.gateway.capabilities.journeys.returns import JourneyReplyBoundary
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    ContextAccessIntent,
    ContextAccessResult,
    DeliveryEvidenceReceipt,
    ExternalWaitIntent,
    NoticeIntent,
    OfferOptionsIntent,
    VersionedOfferOptions,
    request_digest,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pep import build_pep
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.integrations.agent_resume import ResumeHandler
from maezo.platform.webhooks.service import WebhookState, _attach_administrative_runtime
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.runtime.checkpoint import Checkpointer
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_ABSENT: Any = None  # Admitted transparent absence of unrelated legacy transports.
_TABLES = (
    "v21_capability_command",
    "v21_journal_observation",
    "v21_external_wait",
    "v21_journal_outbox",
    "v21_journal_inbox",
)
R = CapabilityRefusalReason


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable() -> None:
    """PG-only assembly; no engine reachability contract or engine double."""


@pytest.fixture(scope="session", autouse=True)
def _deploy_spec_artifacts() -> None:
    """No BPMN/DMN deployment occurs in this synthetic technical assembly."""


def pins(value: Any) -> str:
    return value.model_dump_json()


async def turn_of_loop() -> None:
    release = asyncio.Event()
    asyncio.get_running_loop().call_soon(release.set)
    await release.wait()


@dataclass
class Faults:
    lost_ack: str | None = None
    lost_ack_fired: bool = False
    source_reply_lost: bool = False
    metadata_postcommit: bool = False
    transition_disclosure_revoked: bool = False
    narrow: str | None = None
    collision_outbox: str | None = None
    foreign_wait: bool = False
    last_commit_phase: str | None = None
    held_digest: str | None = None
    cas_initial_reads: bool = False
    initial_revisions: dict[str, int] = field(default_factory=dict)
    both_initial_observed: asyncio.Event = field(default_factory=asyncio.Event)
    winner_pid: int | None = None
    loser_pid: int | None = None
    winner_locked: asyncio.Event = field(default_factory=asyncio.Event)
    release_winner: asyncio.Event = field(default_factory=asyncio.Event)
    loser_attempted: asyncio.Event = field(default_factory=asyncio.Event)


class RealTransaction:
    """Transparent real transaction, optional loss only AFTER actual successful COMMIT."""

    def __init__(self, transaction: Any, connection: RealConnection) -> None:
        self.transaction, self.connection = transaction, connection

    async def start(self) -> None:
        await self.transaction.start()

    async def rollback(self) -> None:
        await self.transaction.rollback()

    async def commit(self) -> None:
        await self.transaction.commit()
        fault = self.connection.fault
        fault.last_commit_phase = self.connection.phase
        if (
            fault.lost_ack is not None
            and fault.lost_ack == self.connection.phase
            and not fault.lost_ack_fired
        ):
            fault.lost_ack_fired = True
            raise ConnectionResetError("SYNTHETIC.known.PG.commit.ack.lost")
        if fault.cas_initial_reads and self.connection.initial_revision is not None:
            # The actual observation transaction has COMMITTED, releasing its
            # FOR UPDATE lock. Waiting before COMMIT would deadlock the readers.
            fault.initial_revisions[self.connection.role] = self.connection.initial_revision
            self.connection.initial_revision = None
            if set(fault.initial_revisions) == {"winner", "loser"}:
                fault.both_initial_observed.set()
            await fault.both_initial_observed.wait()


class RealConnection:
    def __init__(self, connection: Any, fault: Faults, *, track_loser: bool = False) -> None:
        self.connection, self.fault, self.track_loser = connection, fault, track_loser
        self.phase: str | None = None
        self.role = "loser" if track_loser else "winner"
        self.aggregate_write = False
        self.initial_revision: int | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def transaction(self, **kwargs: Any) -> RealTransaction:
        return RealTransaction(self.connection.transaction(**kwargs), self)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        aggregate_lock = "FROM v21_journey_journal WHERE" in query and query.endswith("FOR UPDATE")
        if self.fault.cas_initial_reads and self.aggregate_write and aggregate_lock and self.track_loser:
            self.fault.loser_pid = self.connection.get_server_pid()
            self.fault.loser_attempted.set()
        result = await self.connection.fetchrow(query, *args, **kwargs)
        if self.fault.cas_initial_reads and aggregate_lock:
            if self.aggregate_write and not self.track_loser:
                self.fault.winner_pid = self.connection.get_server_pid()
            elif (
                not self.aggregate_write
                and self.role not in self.fault.initial_revisions
                and result is not None
            ):
                self.initial_revision = result["journal_revision"]
        return result

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        if self.fault.cas_initial_reads and query.startswith("INSERT INTO v21_journey_journal"):
            assert self.fault.initial_revisions == {"winner": 0, "loser": 0}
            if self.track_loser:
                await self.fault.winner_locked.wait()
            self.aggregate_write = True
        result = await self.connection.execute(query, *args, **kwargs)
        if query.startswith("INSERT INTO v21_capability_command"):
            self.phase = "command"
        elif query.startswith("INSERT INTO v21_journal_observation"):
            self.phase = "result"
        elif query.startswith("UPDATE v21_capability_command"):
            for item in args:
                if isinstance(item, str) and item.startswith("{"):
                    value = json.loads(item)
                    if value.get("technical_state") == "DISPATCH_FENCED":
                        self.phase = "fence"
        return result


class RealPool:
    def __init__(self, pool: Any, fault: Faults, *, track_loser: bool = False) -> None:
        self.pool, self.fault, self.track_loser = pool, fault, track_loser

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[RealConnection]:
        async with self.pool.acquire() as connection:
            yield RealConnection(connection, self.fault, track_loser=self.track_loser)


@dataclass
class SourceRecord:
    action: PreparedCapabilityAction
    result: Any
    source: VerifiedSourceResult


class Registry:
    """Protected synthetic issuance/custody; no record presence implies product authority."""

    def __init__(self, db: Database, binding: JourneyBinding) -> None:
        self.db, self.binding = db, binding
        self.now = datetime.now(UTC)
        self.original_until = self.now + timedelta(minutes=10)
        self.tick = self.now
        self.metadata_allowed = True
        self.ingress_allowed = True
        self.turn_allowed = True
        self.read_allowed = True
        self.operation_revoked = False
        self.transition_revoked = False
        self.ingress_calls = 0
        self.source_calls = self.source_commits = self.read_calls = 0
        self.fault = Faults()
        self.actions: dict[str, PreparedCapabilityAction] = {}
        self.authorities: dict[str, VerifiedAuthority] = {}
        self.operation_issuances: dict[str, tuple[str, str, str, str | None]] = {}
        self.originals: dict[str, VerifiedJourneyContinuation] = {}
        self.contexts: dict[str, Any] = {}
        self.records: dict[str, SourceRecord] = {}
        self.descriptors: dict[str, CommandDescriptor] = {}
        self.requests: dict[str, Any] = {}
        self.commits: dict[str, PreparedResultCommit] = {}
        self.attestations: set[str] = set()
        self.audits: dict[str, str] = {}
        self.events: dict[str, VerifiedInboxObservation] = {}
        self.resume_refs: dict[str, tuple[tuple[str, str | None, str | None], ...]] = {}
        self.reply_acks: dict[str, OutboxAckEvidence] = {}
        self.reply_receipts: dict[str, tuple[str, DeliveryEvidenceReceipt]] = {}
        self.transport_calls = self.delivery_checks = self.ack_checks = 0
        self.adapter_results: list[Any] = []
        self.resume_cursor: str | None = None
        self.key_suffix = ""
        self.deadline = self.original_until
        self.op_deadline = self.original_until

    def clock(self) -> datetime:
        return self.tick

    def scope(self, operation: str) -> AdmissionBinding:
        b = self.binding
        return AdmissionBinding(
            principal_ref=b.principal_ref,
            task_ref=b.task_ref,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            purpose_ref="SYNTHETIC.purpose",
            operation_name=operation,
            schema_version=CANDIDATE_SCHEMA_VERSION,
            contract_revision="SYNTHETIC.contract",
            source_authority_ref="SYNTHETIC.authority",
            policy_revision="SYNTHETIC.policy",
            data_classification="SYNTHETIC.protected.refs",
            autonomy_action="scheduling",
            security_zone="general",
        )

    def bound(self, b: Any) -> bool:
        return b == self.binding.journal_binding() and self.metadata_allowed

    async def snapshots(self) -> list[CommandSnapshot]:
        rows = await self.db.query(
            "SELECT snapshot FROM v21_capability_command WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6 "
            "ORDER BY snapshot->>'recorded_at'",
            self.binding.environment_ref,
            self.binding.tenant_ref,
            self.binding.legal_entity_ref,
            self.binding.journey_ref,
            self.binding.principal_ref,
            self.binding.task_ref,
        )
        return [CommandSnapshot.model_validate_json(row["snapshot"]) for row in rows]

    async def verify(self, b: JourneyBinding, current: Any) -> Any:
        self.ingress_calls += 1
        if b != self.binding or not self.ingress_allowed:
            return R.AUTHORITY_UNPROVEN
        if isinstance(current, str):
            issuance = self.resume_refs.get(current)
            if issuance is None:
                return R.AUTHORITY_UNPROVEN
            commands = await self.snapshots()
            lineage = tuple(
                (s.handle.command_ref, s.verified_result_observation_ref, s.source_receipt_ref)
                for s in commands
            )
            if lineage != issuance or not commands or current != commands[-1].verified_result_observation_ref:
                return R.AUTHORITY_UNPROVEN
            for command in commands:
                record = self.records.get(command.handle.request_sha256)
                if record is None or command.source_receipt_ref != record.source.source_receipt_ref:
                    return R.AUTHORITY_UNPROVEN
            return "administrative"
        expected = self.stimulus(current.expected_journal_revision).turn
        if not self.turn_allowed or current != expected:
            return R.AUTHORITY_UNPROVEN
        return "administrative"

    def stimulus(self, revision: int = 0) -> RuntimeTurnStimulus:
        b = self.binding
        message = "hk1_" + "a" * 64
        value = CurrentJourneyTurn(
            handoff=AdministrativeHandoff(HANDOFF_SCHEMA, b.task_ref, b.tenant_ref, b.journey_ref, message),
            current_message_ref=message,
            manifestation_evidence_ref="SYNTHETIC.turn.issued",
            safety_observation_ref="SYNTHETIC.safety.administrative",
            expected_journal_revision=revision,
        )
        return RuntimeTurnStimulus(schema_version=RUNTIME_STIMULUS_SCHEMA, kind="turn", turn=value)

    def make_action(
        self, index: int, predecessors: tuple[CommandSnapshot, ...], *, key_suffix: str = ""
    ) -> PreparedCapabilityAction:
        b = self.binding
        key_suffix = key_suffix or self.key_suffix
        support = b.task_ref == "journey.suporte.step"
        cursor = (
            ("S1_case" if support else "C1_need") if index == 0 else ("S1_ack" if support else "C1_options")
        )
        operation = (
            "access.resolve" if index == 0 else ("notice.prepare_or_send" if support else "offer.compose")
        )
        area = "suporte" if support else "compras"
        kind = "access" if index == 0 else "notice" if support else "offer"
        key = f"SYNTHETIC.{area}.{kind}.{index + 1}{key_suffix}"
        env = CapabilityEnvelope(
            schema_version=CANDIDATE_SCHEMA_VERSION,
            operation_name=operation,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            journey_ref=b.journey_ref,
            correlation_ref="SYNTHETIC.correlation",
            causation_ref=f"SYNTHETIC.cause.{index}",
            idempotency_key=key,
            expected_business_revision=f"SYNTHETIC.opaque.business.{index}",
            source_authority_ref="SYNTHETIC.authority",
            policy_revision="SYNTHETIC.policy",
            data_classification="SYNTHETIC.protected.refs",
        )
        if index == 0:
            request: Any = ContextAccessIntent(
                subject_type="prospect",
                subject_or_prospect_ref="SYNTHETIC.prospect",
                operation_scope_ref="SYNTHETIC.scope",
                purpose_policy_ref="SYNTHETIC.purpose",
                source_context_refs=(),
            )
        elif support:
            request = NoticeIntent(
                notice_ref="SYNTHETIC.notice",
                notice_class="facultative",
                authorized_content_ref="SYNTHETIC.content",
                recipient_authority_ref="SYNTHETIC.recipient",
                confirmed_channel_ref="SYNTHETIC.channel",
                delivery_policy_ref="SYNTHETIC.delivery.policy",
            )
        else:
            request = OfferOptionsIntent(
                catalogue_ref="SYNTHETIC.catalogue",
                catalogue_version="SYNTHETIC.catalogue.version",
                administrative_preferences_ref="SYNTHETIC.preferences",
                availability_refs=(),
                purpose_policy_ref="SYNTHETIC.purpose",
            )
        action = PreparedCapabilityAction(
            schema_version="v21-journey-action.proposed.v1",
            task_ref=b.task_ref,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            journey_ref=b.journey_ref,
            topology_contract_ref=b.topology_contract_ref,
            topology_version_ref=b.topology_version_ref,
            cursor_ref=cursor,
            predecessor_command_refs=tuple(s.handle.command_ref for s in predecessors),
            predecessor_source_receipt_refs=tuple(
                s.source_receipt_ref for s in predecessors if s.source_receipt_ref
            ),
            preparation_authority_ref=key,
            transition_contract_ref=b.source_transition_contract_ref,
            envelope=env,
            request=request,
        )
        self.actions[request_digest(env, request)] = deepcopy(action)
        return action

    async def prepare(self, b: JourneyBinding, snapshot: Any, current: Any) -> Any:
        if b != self.binding or not self.bound(snapshot.binding):
            return R.AUTHORITY_UNPROVEN
        committed = [
            s for s in await self.snapshots() if s.technical_state == CommandTechnicalState.RESPONSE_RECORDED
        ]
        for s in committed:
            record = self.records.get(s.handle.request_sha256)
            if record is None or (s.result_sha256, s.source_receipt_ref) != (
                record.source.result_sha256,
                record.source.source_receipt_ref,
            ):
                return R.AUTHORITY_UNPROVEN
        if len(committed) >= 2:
            support = b.task_ref == "journey.suporte.step"
            self.resume_cursor = "S2_route" if support else "C1_present"
            return R.SOURCE_UNAVAILABLE
        return self.make_action(len(committed), tuple(committed))

    async def source_case_refs(self, b: Any, snapshot: Any) -> tuple[str, ...]:
        assert b == self.binding and snapshot.binding == b.journal_binding()
        return ()

    async def prepare_wait(self, *args: Any) -> Any:
        raise AssertionError("SYNTHETIC.fixture.no.additional.wait.action")

    async def prepare_continuation(self, *args: Any) -> Any:
        raise AssertionError("SYNTHETIC.fixture.no.additional.manifestation.action")

    async def transition(self, b: Any, snapshot: Any, action: Any, current: Any, *, phase: str) -> Any:
        if b != self.binding or self.transition_revoked or not self.bound(snapshot.binding):
            return R.AUTHORITY_UNPROVEN
        original_action = self.actions.get(request_digest(action.envelope, action.request))
        if original_action is None or pins(original_action) != pins(action):
            return R.AUTHORITY_UNPROVEN
        commands = await self.snapshots()
        if phase == "result":
            digest = request_digest(action.envelope, action.request)
            if digest not in self.records or not any(
                s.handle.request_sha256 == digest
                and s.technical_state == CommandTechnicalState.RESPONSE_RECORDED
                for s in commands
            ):
                return R.AUTHORITY_UNPROVEN
        support = b.task_ref == "journey.suporte.step"
        first_cursor = "S1_case" if support else "C1_need"
        second_cursor = "S1_ack" if support else "C1_options"
        successor = (
            second_cursor if action.cursor_ref == first_cursor else "S2_route" if support else "C1_present"
        )
        proof = VerifiedJourneyContinuation(
            schema_version="v21-journey-continuation.proposed.v1",
            task_ref=b.task_ref,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            journey_ref=b.journey_ref,
            topology_contract_ref=b.topology_contract_ref,
            topology_version_ref=b.topology_version_ref,
            from_cursor_ref=first_cursor
            if phase == "entry" and action.cursor_ref == second_cursor
            else action.cursor_ref,
            successor_cursor_ref=action.cursor_ref if phase == "entry" and commands else successor,
            predecessor_command_refs=tuple(s.handle.command_ref for s in commands),
            source_receipt_refs=tuple(s.source_receipt_ref for s in commands if s.source_receipt_ref),
            source_decision_ref=f"SYNTHETIC.transition.{action.envelope.idempotency_key}.{phase}",
            source_observation_ref=f"SYNTHETIC.transition.observation.{action.envelope.idempotency_key}",
            transition_contract_ref=b.source_transition_contract_ref,
            policy_revision_ref="SYNTHETIC.policy",
            valid_until=self.original_until,
        )
        self.originals[proof.source_decision_ref] = deepcopy(proof)
        return proof

    async def register_resume(self, ref: str, expected_commands: int) -> RuntimeResumeStimulus:
        commands = await self.snapshots()
        assert len(commands) == expected_commands
        for command in commands:
            assert command.technical_state == CommandTechnicalState.RESPONSE_RECORDED
            assert command.verified_result_observation_ref is not None
            source = self.records[command.handle.request_sha256].source
            assert command.source_receipt_ref == source.source_receipt_ref
            observation = await self.db.query(
                "SELECT observation FROM v21_journal_observation WHERE observation_ref=$1",
                command.verified_result_observation_ref,
            )
            assert (
                len(observation) == 1
                and json.loads(observation[0]["observation"])["source_result"]["source_receipt_ref"]
                == source.source_receipt_ref
            )
        assert ref == commands[-1].verified_result_observation_ref
        self.resume_refs[ref] = tuple(
            (s.handle.command_ref, s.verified_result_observation_ref, s.source_receipt_ref) for s in commands
        )
        return RuntimeResumeStimulus(
            schema_version=RUNTIME_STIMULUS_SCHEMA,
            kind="resume",
            observation_ref=ref,
            expected_journal_revision=await self.db.revision(self.binding),
        )


class TransitionOwner:
    def __init__(self, registry: Registry) -> None:
        self.r = registry

    async def verify(self, *args: Any, **kwargs: Any) -> Any:
        return await self.r.transition(*args, **kwargs)

    async def check_current(self, context: Any, *, phase: str) -> Any:
        r = self.r
        await turn_of_loop()
        proof = r.originals.get(context.original_transition.source_decision_ref)
        if (
            proof is None
            or pins(proof) != pins(context.original_transition)
            or context.journey_binding != r.binding
        ):
            return R.AUTHORITY_UNPROVEN
        if r.transition_revoked or (r.fault.transition_disclosure_revoked and phase == "before_disclosure"):
            return R.AUTHORITY_UNPROVEN
        digest = effect_authority_digest(context)
        previous = r.contexts.get(digest)
        if previous is not None and pins(previous) != pins(context):
            return R.AUTHORITY_UNPROVEN
        r.contexts[digest] = deepcopy(context)
        return VerifiedJourneyEffectCurrentness(
            schema_version="v21-journey-effect-currentness.proposed.v1",
            effect_authority_sha256=digest,
            source_decision_ref=proof.source_decision_ref,
            source_observation_ref=proof.source_observation_ref,
            transition_contract_ref=proof.transition_contract_ref,
            policy_revision_ref=proof.policy_revision_ref,
            currentness_ref="SYNTHETIC.transition.anchor." + digest,
            checked_at=r.clock(),
            valid_until=r.deadline,
        )


class OperationOwner:
    def __init__(self, registry: Registry) -> None:
        self.r = registry
        self.issuances = registry.operation_issuances

    async def authorize(self, scope: Any, env: Any, request: Any) -> VerifiedAuthority:
        r = self.r
        await turn_of_loop()
        digest = request_digest(env, request)
        action = r.actions.get(digest)
        if (
            r.operation_revoked
            or action is None
            or action.envelope != env
            or action.request != request
            or scope != r.scope(env.operation_name)
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        value = VerifiedAuthority(
            binding=scope,
            request_sha256=digest,
            authorization_ref="SYNTHETIC.operation." + digest,
            signed_task_ref="SYNTHETIC.signed.task",
            agent_card_sha256="a" * 64,
            source_contract_publication_ref="SYNTHETIC.source.publication",
            policy_ratification_ref="SYNTHETIC.policy.issuance",
            currentness_ref="SYNTHETIC.operation.anchor." + digest,
            enforcement="enforcing",
            decision="allow",
            verified_at=r.now,
            valid_until=r.original_until,
        )
        custody = r.descriptors.get(digest)
        original = (
            pins(value),
            pins(action),
            pins(r.binding),
            pins(custody) if custody is not None else None,
        )
        if value.authorization_ref in self.issuances and self.issuances[value.authorization_ref] != original:
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        self.issuances[value.authorization_ref] = original
        r.authorities[value.authorization_ref] = deepcopy(value)
        return deepcopy(value)

    async def check_current(self, authority: Any, *, source_result: Any = None) -> VerifiedCurrentness:
        r = self.r
        if (
            type(authority) is not VerifiedAuthority
            or set(authority.__dict__) != set(VerifiedAuthority.model_fields)
            or (
                source_result is not None
                and (
                    type(source_result) is not VerifiedSourceResult
                    or set(source_result.__dict__) != set(VerifiedSourceResult.model_fields)
                )
            )
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        original_authority = pins(authority)
        original_result = pins(source_result) if source_result is not None else None
        await turn_of_loop()
        issued = r.authorities.get(authority.authorization_ref)
        issuance = self.issuances.get(authority.authorization_ref)
        if (
            r.operation_revoked
            or issued is None
            or issuance is None
            or original_authority != pins(authority)
            or pins(issued) != original_authority
            or issuance[0] != original_authority
            or issuance[2] != pins(r.binding)
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        qualified_digest = None
        if source_result is not None:
            record = r.records.get(issued.request_sha256)
            custody = r.descriptors.get(issued.request_sha256)
            request = r.requests.get(issued.request_sha256)
            if (
                record is None
                or custody is None
                or request is None
                or original_result != pins(source_result)
                or original_result not in r.attestations
                or original_result != pins(record.source)
                or pins(record.action) != issuance[1]
                or pins(custody) != issuance[3]
                or custody.request_sha256 != issued.request_sha256
                or custody.admission_binding_sha256 != admission_binding_digest(issued.binding)
                or custody.envelope != record.action.envelope
                or pins(request) != pins(record.action.request)
                or request_digest(custody.envelope, request) != issued.request_sha256
                or record.source.binding != issued.binding
                or record.source.request_sha256 != issued.request_sha256
                or record.source.authorization_ref != issued.authorization_ref
                or record.source.currentness_ref != issued.currentness_ref
                or record.source.result_sha256 != result_digest(record.result)
                or not record.source.checked_at
                <= r.clock()
                < min(record.source.valid_until, issued.valid_until)
            ):
                raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
            qualified_digest = record.source.result_sha256
        return VerifiedCurrentness(
            binding=issued.binding,
            request_sha256=issued.request_sha256,
            authorization_ref=issued.authorization_ref,
            currentness_ref=issued.currentness_ref,
            source_result_sha256=qualified_digest,
            checked_at=r.clock(),
            valid_until=r.op_deadline,
        )

    async def verify_source_result(self, authority: Any, result: Any) -> VerifiedSourceResult:
        r = self.r
        issued = r.authorities.get(authority.authorization_ref)
        record = r.records.get(authority.request_sha256)
        if (
            issued is None
            or pins(issued) != pins(authority)
            or record is None
            or pins(record.result) != pins(result)
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        assert pins(record.source) in r.attestations
        return deepcopy(record.source)


class AuditPort:
    """Actual PG audit commit and receipt linkage; never a fabricated hash ACK."""

    def __init__(self, registry: Registry, sink: PostgresAuditSink) -> None:
        self.r, self.sink = registry, sink

    async def emit_intent(self, intent: Any) -> str:
        issued = self.r.authorities.get(intent.authorization_ref)
        if (
            issued is None
            or intent.binding != issued.binding
            or intent.request_sha256 != issued.request_sha256
        ):
            raise AdmissionDeniedError(R.AUDIT_UNAVAILABLE)
        record = AuditRecord(
            agent_id=intent.binding.principal_ref,
            tenant_id=intent.binding.tenant_ref,
            agent_version="SYNTHETIC.fixture",
            action="capability.admission",
            decision="ALLOW",
            details=intent.model_dump(mode="json"),
            timestamp=self.r.clock(),
        )
        receipt = await self.sink.emit(record)
        assert receipt == record.record_hash
        self.r.audits[receipt] = pins(intent)
        return receipt


class Source:
    """R5 original checkpoint enforcement immediately before a synthetic local effect witness."""

    def __init__(self, registry: Registry, transition: TransitionOwner, operation: OperationOwner) -> None:
        self.r, self.transition, self.operation = registry, transition, operation
        self.received: list[JourneyInvocationCheckpoint] = []

    async def execute_under_authority(
        self, env: Any, request: Any, *, invocation_checkpoint: Any, timeout_seconds: float
    ) -> Any:
        r = self.r
        r.source_calls += 1
        incoming = JourneyInvocationCheckpoint.model_validate(deepcopy(invocation_checkpoint.__dict__))
        self.received.append(deepcopy(incoming))
        original_pins = pins(incoming)
        context = incoming.original_effect_authority
        digest = request_digest(env, request)
        admitted_context = r.contexts.get(effect_authority_digest(context))
        authority = incoming.invocation_authority.original_authority
        issued = r.authorities.get(authority.authorization_ref)
        if (
            admitted_context is None
            or pins(admitted_context) != pins(context)
            or issued is None
            or pins(issued) != pins(authority)
            or context.action.envelope != env
            or context.action.request != request
            or context.request_sha256 != digest
        ):
            raise JourneyContractError(R.AUTHORITY_UNPROVEN)
        guard = JourneyEffectGuard(context, self.transition, r.clock)
        guard.first = deepcopy(incoming.original_transition_currentness)
        guard.latest = deepcopy(incoming.latest_transition_currentness)
        guard.ceiling = incoming.transition_accumulated_valid_until
        await turn_of_loop()
        if r.fault.narrow is not None:
            r.tick = r.now + timedelta(seconds=6)
            r.deadline = r.op_deadline = r.original_until
        await guard.check("before_effect")
        provided = deepcopy(authority)
        current = await self.operation.check_current(provided)
        operation = incoming.invocation_authority
        if (
            pins(provided) != pins(authority)
            or pins(incoming) != original_pins
            or pins(authority) != pins(r.authorities[authority.authorization_ref])
        ):
            raise JourneyContractError(R.AUTHORITY_UNPROVEN)
        if (
            current.binding != authority.binding
            or current.request_sha256 != digest
            or current.authorization_ref != authority.authorization_ref
            or current.currentness_ref != authority.currentness_ref
            or current.checked_at < operation.last_checked_at
            or current.valid_until > operation.accumulated_valid_until
        ):
            raise JourneyContractError(R.AUTHORITY_UNPROVEN)
        guard.assert_current()
        if not current.checked_at <= r.clock() < min(current.valid_until, incoming.effective_valid_until):
            raise JourneyContractError(R.AUTHORITY_UNPROVEN)
        # No await after final BOTH check and before this registered technical commit.
        if digest in r.records:
            raise AssertionError("source execute cannot be resubmitted")
        action = deepcopy(r.actions[digest])
        if env.operation_name == "access.resolve":
            result: Any = ContextAccessResult(
                access_status="available",
                context_refs=("SYNTHETIC.context",),
                source_revision_ref="SYNTHETIC.source.revision." + digest,
                access_decision_ref="SYNTHETIC.access.decision",
            )
        elif env.operation_name == "offer.compose":
            result = VersionedOfferOptions(status="unavailable", option_refs=(), explanation_refs=())
        else:
            result = DeliveryEvidenceReceipt(
                delivery_status="pending",
                attempt_revision="SYNTHETIC.notice.attempt.1",
                provider_delivery_receipt_ref=None,
                metadata_publication_receipt_ref=None,
            )
        proof = VerifiedSourceResult(
            binding=authority.binding,
            request_sha256=digest,
            result_sha256=result_digest(result),
            authorization_ref=authority.authorization_ref,
            source_receipt_ref="SYNTHETIC.source.receipt." + digest,
            source_revision_ref="SYNTHETIC.source.revision." + digest,
            currentness_ref=authority.currentness_ref,
            checked_at=r.clock(),
            valid_until=min(authority.valid_until, current.valid_until),
        )
        r.records[digest] = SourceRecord(action, deepcopy(result), deepcopy(proof))
        r.attestations.add(pins(proof))
        r.source_commits += 1
        if r.fault.source_reply_lost:
            raise TimeoutError("SYNTHETIC.source.commit.known.response.lost")
        return deepcopy(result)


class Custody:
    def __init__(self, registry: Registry) -> None:
        self.r = registry

    async def prepare_command(
        self, b: Any, scope: Any, env: Any, request: Any, predecessors: Any, source: Any
    ) -> CommandDescriptor:
        digest = request_digest(env, request)
        action = self.r.actions.get(digest)
        if (
            not self.r.bound(b)
            or action is None
            or pins(action.envelope) != pins(env)
            or pins(action.request) != pins(request)
            or action.predecessor_command_refs != predecessors
            or scope != self.r.scope(env.operation_name)
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        value = CommandDescriptor(
            envelope=env,
            request_ref="SYNTHETIC.request." + digest,
            request_sha256=digest,
            admission_binding_sha256=admission_binding_digest(scope),
            predecessor_command_refs=predecessors,
        )
        self.r.descriptors[digest] = deepcopy(value)
        self.r.requests[digest] = deepcopy(request)
        return value

    async def dispatch_evidence(self, b: Any, private: Any) -> DispatchEvidence:
        if not self.r.bound(b) or private.audit_receipt_sha256 not in self.r.audits:
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        return DispatchEvidence(
            authority=private.authority,
            currentness=private.currentness,
            audit_receipt_sha256=private.audit_receipt_sha256,
            audit_intent_ref="SYNTHETIC.audit." + private.audit_receipt_sha256,
        )

    async def pre_dispatch_refusal(self, b: Any, handle: Any, reason: Any) -> PreDispatchRefusal:
        assert self.r.bound(b) and handle.binding == b
        return PreDispatchRefusal(
            reason=reason, evidence_ref="SYNTHETIC.pre.dispatch.refusal", observed_at=self.r.clock()
        )

    async def restore_command(self, b: Any, command_ref: str) -> RestoredCommand:
        if not self.r.bound(b):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        commands = await self.r.snapshots()
        command = next((s for s in commands if s.handle.command_ref == command_ref), None)
        if command is None:
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        digest = command.handle.request_sha256
        return RestoredCommand(
            command.handle, deepcopy(self.r.descriptors[digest]), deepcopy(self.r.requests[digest])
        )


class Preparation:
    def __init__(self, registry: Registry) -> None:
        self.r = registry

    async def prepare(
        self, b: Any, descriptor: Any, handle: Any, result: Any, source_result: Any
    ) -> PreparedResultCommit:
        r = self.r
        digest = descriptor.request_sha256
        record = r.records.get(digest)
        if (
            not r.bound(b)
            or record is None
            or handle.binding != b
            or pins(result) != pins(record.result)
            or pins(source_result) not in r.attestations
            or source_result.source_receipt_ref != record.source.source_receipt_ref
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        short = digest[:24]
        w = WaitDescriptor(
            handle=handle,
            operational_wakeup_at=None,
            intent=ExternalWaitIntent(
                wait_ref="SYNTHETIC.wait." + short,
                expected_producer_ref="SYNTHETIC.producer",
                correlation_ref=descriptor.envelope.correlation_ref,
                authoritative_deadline_ref=None,
            ),
        )
        if r.fault.foreign_wait:
            w = w.model_copy(
                update={
                    "handle": handle.model_copy(
                        update={"binding": b.model_copy(update={"journey_ref": "SYNTHETIC.foreign.journey"})}
                    )
                }
            )
        o = OutboxDescriptor(
            outbox_ref="SYNTHETIC.outbox." + short,
            command_ref=handle.command_ref,
            kind=OutboxTechnicalKind.RETURN_INTENT,
            target_binding_ref="SYNTHETIC.target",
            payload_ref="SYNTHETIC.protected.payload." + short,
            payload_sha256=action_digest(record.action),
            causation_ref=descriptor.envelope.causation_ref,
            dedupe_identity_ref="SYNTHETIC.return." + short,
        )
        if r.fault.collision_outbox:
            seed = await r.db.query(
                "SELECT descriptor FROM v21_journal_outbox WHERE outbox_ref=$1", r.fault.collision_outbox
            )
            original = OutboxDescriptor.model_validate_json(seed[0]["descriptor"])
            o = o.model_copy(
                update={
                    "outbox_ref": original.outbox_ref,
                    "dedupe_identity_ref": original.dedupe_identity_ref,
                }
            )
        value = PreparedResultCommit(
            VerifiedResultObservation(
                source_result=source_result,
                result_ref="SYNTHETIC.result." + digest,
                result_sha256=source_result.result_sha256,
                observer_binding_ref="SYNTHETIC.source.observer",
                provenance_ref="SYNTHETIC.provenance." + digest,
            ),
            (o,),
            (w,),
        )
        r.commits[digest] = deepcopy(value)
        return value


class Reads:
    def __init__(self, registry: Registry, custody: Custody, preparation: Preparation) -> None:
        self.r, self.custody, self.preparation = registry, custody, preparation

    def proof(self, digest: str) -> VerifiedSourceResult:
        r = self.r
        if not r.metadata_allowed or not r.read_allowed or digest not in r.records:
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        original = r.records[digest].source
        proof = original.model_copy(
            update={
                "authorization_ref": "SYNTHETIC.independent.read." + digest,
                "currentness_ref": "SYNTHETIC.read.anchor." + digest,
                "checked_at": r.clock(),
            }
        )
        r.attestations.add(pins(proof))
        return proof

    async def lookup(self, b: Any, command: Any, snapshot: Any, *, timeout_seconds: float) -> Any:
        self.r.read_calls += 1
        if not self.r.bound(b) or command.descriptor.request_sha256 not in self.r.records:
            return R.SOURCE_UNAVAILABLE
        digest = command.descriptor.request_sha256
        record = self.r.records[digest]
        commit = await self.preparation.prepare(
            b, command.descriptor, command.handle, record.result, self.proof(digest)
        )
        return QualifiedLookup(deepcopy(record.result), commit)

    async def current(self, b: Any, descriptor: Any, source: Any) -> SourceReadEvidence:
        r = self.r
        if not r.bound(b) or not r.read_allowed or pins(source) not in r.attestations:
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        record = r.records[source.request_sha256]
        if (
            source.binding != r.scope(descriptor.envelope.operation_name)
            or source.result_sha256 != result_digest(record.result)
            or source.source_receipt_ref != record.source.source_receipt_ref
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        return SourceReadEvidence(
            journal_binding=b,
            admission_binding=source.binding,
            request_sha256=source.request_sha256,
            result_sha256=source.result_sha256,
            source_receipt_ref=source.source_receipt_ref,
            source_revision_ref=source.source_revision_ref,
            currentness_ref=source.currentness_ref,
            read_authority_ref=source.authorization_ref,
            source_contract_publication_ref="SYNTHETIC.read.publication",
            data_policy_ref=b.data_policy_ref,
            checked_at=r.clock(),
            valid_until=source.valid_until,
        )

    async def current_head(self, b: Any, descriptor: Any, snapshot: Any) -> VerifiedSourceResult:
        assert b == self.r.binding.journal_binding()
        proof = self.proof(descriptor.request_sha256)
        if (snapshot.source_revision_ref, snapshot.source_receipt_ref, snapshot.result_sha256) != (
            proof.source_revision_ref,
            proof.source_receipt_ref,
            proof.result_sha256,
        ):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        return proof

    async def verify_inbox(self, b: Any, event: Any) -> QualifiedInbox:
        registered = self.r.events.get(event.event_ref)
        if not self.r.bound(b) or registered is None or pins(registered) != pins(event):
            raise AdmissionDeniedError(R.AUTHORITY_UNPROVEN)
        command = await self.custody.restore_command(b, event.handle.command_ref)
        digest = command.descriptor.request_sha256
        record = self.r.records[digest]
        commit = self.r.commits[digest]
        return QualifiedInbox(command, deepcopy(record.result), event, deepcopy(commit), None)


class JournalProofs:
    """Each exact protected issuance is checked again by the real PG runner."""

    def __init__(self, registry: Registry) -> None:
        self.r = registry

    async def authorize(self, b: Any, method: str) -> bool:
        return self.r.bound(b)

    async def verify_command(self, b: Any, d: Any) -> bool:
        r = self.r
        accepted = (
            r.bound(b)
            and d.request_sha256 in r.descriptors
            and pins(d) == pins(r.descriptors[d.request_sha256])
        )
        if accepted and r.fault.held_digest == d.request_sha256 and not r.fault.winner_locked.is_set():
            r.fault.winner_locked.set()
            await r.fault.release_winner.wait()
        return accepted

    async def verify_dispatch(self, b: Any, d: Any, evidence: Any) -> bool:
        issued = self.r.authorities.get(evidence.authority.authorization_ref)
        return (
            self.r.bound(b)
            and issued is not None
            and pins(issued) == pins(evidence.authority)
            and evidence.currentness.binding == issued.binding
            and evidence.currentness.authorization_ref == issued.authorization_ref
            and evidence.currentness.currentness_ref == issued.currentness_ref
            and evidence.audit_receipt_sha256 in self.r.audits
            and evidence.audit_intent_ref == "SYNTHETIC.audit." + evidence.audit_receipt_sha256
        )

    async def verify_result(self, b: Any, d: Any, dispatch_ref: str, previous: Any, candidate: Any) -> bool:
        r = self.r
        record = r.records.get(d.request_sha256)
        commit = r.commits.get(d.request_sha256)
        accepted = (
            b == r.binding.journal_binding()
            and record is not None
            and commit is not None
            and pins(candidate.source_result) in r.attestations
            and candidate.source_result.source_receipt_ref == record.source.source_receipt_ref
            and candidate.source_result.result_sha256 == result_digest(record.result)
            and candidate.result_ref == commit.observation.result_ref
            and candidate.provenance_ref == commit.observation.provenance_ref
            and candidate.observer_binding_ref == commit.observation.observer_binding_ref
            and (
                previous is None
                or previous.source_result.source_revision_ref == candidate.source_result.source_revision_ref
            )
        )
        if accepted and r.fault.metadata_postcommit and r.fault.last_commit_phase == "result":
            await turn_of_loop()
            r.metadata_allowed = False
        return accepted

    async def verify_inbox(self, b: Any, d: Any, event: Any) -> bool:
        registered = self.r.events.get(event.event_ref)
        return (
            self.r.bound(b)
            and registered is not None
            and pins(registered) == pins(event)
            and event.handle.binding == b
            and event.producer_ref == "SYNTHETIC.producer"
            and event.correlation_ref == d.envelope.correlation_ref
        )

    async def verify_wait(self, b: Any, descriptor: Any, observation: Any) -> bool:
        return (
            self.r.bound(b)
            and any(descriptor == w for c in self.r.commits.values() for w in c.wait_intents)
            and descriptor.handle.binding == b
            and (observation is None or observation.producer_ref == descriptor.intent.expected_producer_ref)
        )

    async def verify_outbox(self, b: Any, descriptor: Any, worker_ref: Any, evidence: Any) -> bool:
        registered = any(descriptor == o for c in self.r.commits.values() for o in c.outbox_intents)
        ack = self.r.reply_acks.get(descriptor.outbox_ref)
        return (
            self.r.bound(b)
            and registered
            and descriptor.target_binding_ref == "SYNTHETIC.target"
            and (worker_ref is None or worker_ref == "SYNTHETIC.worker")
            and (evidence is None or ack is not None and pins(ack) == pins(evidence))
        )

    async def verify_clock(self, b: Any, observed_at: datetime) -> bool:
        return self.r.bound(b) and observed_at <= self.r.clock()


class TracingAdapter(CapabilityServiceExecutionAdapter):
    """Observational inherited actual adapter; same returned object, no added await."""

    def __init__(self, service: CapabilityService, registry: Registry) -> None:
        super().__init__(service)
        self.r = registry

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        result = await super().execute(*args, **kwargs)
        self.r.adapter_results.append(result)
        return result


class Reply:
    def __init__(self, registry: Registry, *, mode: str = "ack_only") -> None:
        self.r, self.mode = registry, mode
        self.authorized: dict[str, tuple[VerifiedAuthority, VerifiedCurrentness]] = {}
        self.invocations: dict[str, tuple[str, str, str, str, str]] = {}
        self.revoked = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def _original(self, b: Any, snapshot: Any, auth: Any, current: Any) -> bool:
        issued = self.invocations.get(snapshot.delivery_ref)
        if issued is None or self.revoked or not self.r.bound(b):
            return False
        if (
            type(snapshot) is not OutboxSnapshot
            or set(snapshot.__dict__) != set(OutboxSnapshot.model_fields)
            or type(snapshot.descriptor) is not OutboxDescriptor
            or set(snapshot.descriptor.__dict__) != set(OutboxDescriptor.model_fields)
            or type(auth) is not VerifiedAuthority
            or set(auth.__dict__) != set(VerifiedAuthority.model_fields)
            or type(current) is not VerifiedCurrentness
            or set(current.__dict__) != set(VerifiedCurrentness.model_fields)
            or (pins(b), pins(self.r.binding), pins(snapshot), pins(auth)) != issued[:4]
        ):
            return False
        initial = VerifiedCurrentness.model_validate_json(issued[4])
        return (
            pins(current.model_copy(update={"checked_at": initial.checked_at})) == issued[4]
            and initial.checked_at <= current.checked_at <= self.r.clock()
            and snapshot.lease_until is not None
            and self.r.clock() < min(auth.valid_until, initial.valid_until, snapshot.lease_until)
        )

    async def authorize(self, b: Any, snapshot: Any) -> Any:
        r = self.r
        if self.revoked or not r.bound(b) or snapshot.descriptor.target_binding_ref != "SYNTHETIC.target":
            return R.AUTHORITY_UNPROVEN
        if not any(snapshot.descriptor == o for c in r.commits.values() for o in c.outbox_intents):
            return R.AUTHORITY_UNPROVEN
        if (
            type(snapshot) is not OutboxSnapshot
            or set(snapshot.__dict__) != set(OutboxSnapshot.model_fields)
            or type(snapshot.descriptor) is not OutboxDescriptor
            or set(snapshot.descriptor.__dict__) != set(OutboxDescriptor.model_fields)
            or snapshot.technical_state is not OutboxTechnicalState.DELIVERY_FENCED
            or snapshot.claim_ref is None
            or snapshot.delivery_ref is None
            or snapshot.worker_ref != "SYNTHETIC.worker"
            or snapshot.fence_version is None
            or snapshot.lease_until is None
            or r.clock() >= snapshot.lease_until
        ):
            return R.AUTHORITY_UNPROVEN
        if snapshot.delivery_ref in self.authorized:
            auth, current = self.authorized[snapshot.delivery_ref]
            return (
                deepcopy((auth, current))
                if self._original(b, snapshot, auth, current)
                else R.AUTHORITY_UNPROVEN
            )
        scope = r.scope("notice.prepare_or_send")
        digest = snapshot.descriptor.payload_sha256
        auth = VerifiedAuthority(
            binding=scope,
            request_sha256=digest,
            authorization_ref="SYNTHETIC.reply.authority." + snapshot.delivery_ref,
            signed_task_ref="SYNTHETIC.reply.signed.task",
            agent_card_sha256="a" * 64,
            source_contract_publication_ref="SYNTHETIC.reply.publication",
            policy_ratification_ref="SYNTHETIC.reply.policy.issuance",
            currentness_ref="SYNTHETIC.reply.anchor." + snapshot.delivery_ref,
            enforcement="enforcing",
            decision="allow",
            verified_at=r.now,
            valid_until=r.original_until,
        )
        current = VerifiedCurrentness(
            binding=scope,
            request_sha256=digest,
            authorization_ref=auth.authorization_ref,
            currentness_ref=auth.currentness_ref,
            checked_at=r.clock(),
            valid_until=auth.valid_until,
        )
        self.authorized[snapshot.delivery_ref] = deepcopy((auth, current))
        self.invocations[snapshot.delivery_ref] = (
            pins(b),
            pins(r.binding),
            pins(snapshot),
            pins(auth),
            pins(current),
        )
        return auth, current

    async def revalidate(self, b: Any, snapshot: Any, auth: Any, current: Any) -> Any:
        if not self._original(b, snapshot, auth, current):
            return R.AUTHORITY_UNPROVEN
        return deepcopy(auth), current.model_copy(update={"checked_at": self.r.clock()})

    async def deliver(self, b: Any, snapshot: Any, auth: Any, current: Any) -> Any:
        r = self.r
        if not self._original(b, snapshot, auth, current):
            return R.AUTHORITY_UNPROVEN
        assert snapshot.technical_state == OutboxTechnicalState.DELIVERY_FENCED
        r.transport_calls += 1
        self.entered.set()
        if self.mode in {"cancel_during_targetawait", "held_ack_only"}:
            await self.release.wait()
        else:
            await turn_of_loop()
        if self.mode == "revoked":
            self.revoked = True
        # Callback-capable target preparation is complete. Original immutable
        # issuance pins must still match before any receipt/ACK effect, with no
        # intervening await or newly manufactured authority.
        if not self._original(b, snapshot, auth, current):
            return R.AUTHORITY_UNPROVEN
        receipt: Any = None
        if self.mode not in {
            "ack_only",
            "held_ack_only",
            "no_evidence_no_ack",
            "cancel_during_targetawait",
            "revoked",
        }:
            status = (
                "pending"
                if self.mode.startswith("pending")
                else "attempted"
                if self.mode.startswith("attempted")
                else "unknown"
            )
            receipt = DeliveryEvidenceReceipt(
                delivery_status=status,
                attempt_revision="SYNTHETIC.reply.attempt.revision.1",
                provider_delivery_receipt_ref=None,
                metadata_publication_receipt_ref=None,
            )
            if self.mode != "unregistered_receipt":
                r.reply_receipts[snapshot.delivery_ref] = (
                    snapshot.descriptor.payload_sha256,
                    deepcopy(receipt),
                )
        ack: Any = None
        if self.mode in {
            "ack_only",
            "held_ack_only",
            "pending_receipt_with_ack",
            "attempted_receipt_with_ack",
            "unknown_receipt_with_ack",
            "revoked",
        }:
            ack = OutboxAckEvidence(
                outbox_ref=snapshot.descriptor.outbox_ref,
                delivery_ref=snapshot.delivery_ref,
                target_binding_ref=snapshot.descriptor.target_binding_ref,
                payload_sha256=snapshot.descriptor.payload_sha256,
                acknowledgement_ref="SYNTHETIC.reply.ack." + snapshot.delivery_ref,
                acknowledgement_sha256=action_digest(snapshot.descriptor),
                verifier_binding_ref="SYNTHETIC.reply.verifier",
                observed_at=r.clock(),
            )
            r.reply_acks[ack.outbox_ref] = deepcopy(ack)
        return ack, receipt

    async def verify_ack(self, b: Any, snapshot: Any, evidence: Any) -> bool:
        self.r.ack_checks += 1
        registered = self.r.reply_acks.get(snapshot.descriptor.outbox_ref)
        return (
            not self.revoked
            and self.r.bound(b)
            and registered is not None
            and pins(registered) == pins(evidence)
            and evidence.delivery_ref == snapshot.delivery_ref
        )

    async def verify_delivery(self, b: Any, snapshot: Any, receipt: Any) -> bool:
        self.r.delivery_checks += 1
        registered = self.r.reply_receipts.get(snapshot.delivery_ref)
        return (
            not self.revoked
            and self.r.bound(b)
            and registered is not None
            and registered[0] == snapshot.descriptor.payload_sha256
            and pins(registered[1]) == pins(receipt)
            and receipt.provider_delivery_receipt_ref is None
            and receipt.metadata_publication_receipt_ref is None
        )


@dataclass
class Root:
    registry: Registry
    journal: PostgresDurabilityJournal
    service: CapabilityService
    driver: JourneyDriver
    runner: Any
    dispatcher: HelenaDispatcher
    resume_handler: ResumeHandler
    saver: AsyncPostgresSaver
    source: Source
    admissions: dict[str, CapabilityAdmission]


class Database:
    def __init__(self, dsn: str, tenant: str, checkpoint_schema: str) -> None:
        self.dsn, self.tenant, self.schema, self.cp_schema = (
            dsn,
            tenant,
            schema_for_tenant(tenant),
            checkpoint_schema,
        )
        self.admin: Any = None
        self.pools: list[Any] = []
        self.query_lock = asyncio.Lock()
        self.extra: list[Database] = []

    async def open(self, *, install: bool = False) -> None:
        self.admin = await asyncpg.connect(self.dsn, server_settings={"search_path": "pg_catalog"})
        if install:
            await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
            await self.admin.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.cp_schema}"')
            engine = create_async_engine(
                make_url(self.dsn).set(drivername="postgresql+asyncpg"),
                connect_args={"server_settings": {"search_path": f"{self.schema},pg_catalog"}},
                hide_parameters=True,
            )
            try:
                async with engine.begin() as connection:

                    def apply(sync: Any) -> None:
                        with Operations.context(MigrationContext.configure(sync)):
                            for name in (
                                "0002_audit_chain",
                                "0005_audit_emit_dedup",
                                "0018_capability_durability_journal",
                            ):
                                importlib.import_module(
                                    "maezo.platform.migrations.versions." + name
                                ).upgrade()

                    await connection.run_sync(apply)
            finally:
                await engine.dispose()
        for _ in range(2):
            self.pools.append(
                await asyncpg.create_pool(
                    self.dsn, min_size=1, max_size=4, server_settings={"search_path": "pg_catalog"}
                )
            )

    async def query(self, sql: str, *args: Any) -> Any:
        async with self.query_lock:
            await self.admin.execute(f'SET search_path TO "{self.schema}",pg_catalog')
            return await self.admin.fetch(sql, *args)

    async def revision(self, binding: JourneyBinding) -> int:
        rows = await self.query(
            "SELECT journal_revision FROM v21_journey_journal WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6",
            binding.environment_ref,
            binding.tenant_ref,
            binding.legal_entity_ref,
            binding.journey_ref,
            binding.principal_ref,
            binding.task_ref,
        )
        return rows[0]["journal_revision"] if rows else 0

    async def counts(self) -> tuple[int, ...]:
        return tuple([(await self.query(f"SELECT count(*) AS n FROM {table}"))[0]["n"] for table in _TABLES])

    async def close_connections(self) -> None:
        for pool in self.pools:
            await pool.close()
        self.pools.clear()
        if self.admin is not None:
            await self.admin.close()
            self.admin = None

    async def close(self) -> None:
        for child in self.extra:
            await child.close_connections()
        await self.close_connections()
        cleanup = await asyncpg.connect(self.dsn, server_settings={"search_path": "pg_catalog"})
        try:
            for schema in {self.cp_schema, self.schema, *(child.schema for child in self.extra)}:
                await cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await cleanup.close()

    async def new_tenant(self) -> Database:
        child = Database(self.dsn, "pgcmp_" + uuid4().hex, self.cp_schema)
        self.extra.append(child)
        await child.open(install=True)
        return child

    @asynccontextmanager
    async def root(
        self, registry: Registry, *, budget: int = 1, pool_index: int = 0, loser: bool = False
    ) -> AsyncIterator[Root]:
        b = registry.binding
        journal = PostgresDurabilityJournal(
            pool=RealPool(self.pools[pool_index], registry.fault, track_loser=loser),
            binding=b.journal_binding(),
            proofs=JournalProofs(registry),
            enabled=True,
        )
        operation, transition = OperationOwner(registry), TransitionOwner(registry)
        source = Source(registry, transition, operation)
        custody, preparation = Custody(registry), Preparation(registry)
        reads = Reads(registry, custody, preparation)
        sink = PostgresAuditSink(self.dsn, b.tenant_ref, pool=self.pools[pool_index])
        audit = AuditPort(registry, sink)
        admissions = {
            op: CapabilityAdmission(
                binding=registry.scope(op),
                authority=operation,
                audit=audit,
                autonomy=build_pep(tenant=b.tenant_ref),
                clock=registry.clock,
            )
            for op in ("access.resolve", "offer.compose", "notice.prepare_or_send")
        }
        executor = DurableCapabilityExecutor(
            binding=b.journal_binding(),
            journal=journal,
            custody=custody,
            preparation=preparation,
            reads=reads,
            enabled=True,
            effect_authority_port=transition,
            clock=registry.clock,
        )
        service = CapabilityService(
            admissions=admissions, journey_sources={op: source for op in admissions}, durable=executor
        )
        adapter = TracingAdapter(service, registry)
        driver = JourneyDriver(
            b,
            journal=journal,
            preparation=registry,
            transitions=transition,
            currentness=registry,
            execution=adapter,
            max_actions_per_turn=budget,
            clock=registry.clock,
        )
        cp_dsn = make_conninfo(self.dsn, options=f"-c search_path={self.cp_schema},pg_catalog")
        async with AsyncPostgresSaver.from_conn_string(cp_dsn) as saver:
            await saver.setup()
            checkpoint = Checkpointer(saver=saver)
            pseudo = Pseudonymizer(key=b"SYNTHETIC.fixture.key.not.a.credential")
            dispatcher = HelenaDispatcher(
                tenant_id=b.tenant_ref,
                inference=_ABSENT,
                dmn=_ABSENT,
                cibseven=_ABSENT,
                whatsapp_client=_ABSENT,
                pseudonymizer=pseudo,
                audit_sink=_ABSENT,
                checkpointer=checkpoint,
            )
            settings = WhatsAppWebhookSettings(
                _env_file=None,
                tenant_id=b.tenant_ref,
                app_secret="SYNTHETIC.unused",
                verify_token="SYNTHETIC.unused",
                whatsapp_token=None,
            )
            state = WebhookState(settings=settings, dispatcher=dispatcher, checkpointer=checkpoint)
            runner = _attach_administrative_runtime(
                state, binding=b, driver=driver, currentness=registry, enabled=True
            )
            assert (
                runner is not None
                and runner is state.administrative_runtime is dispatcher.administrative_runtime
            )
            assert runner.is_bound_to(b)
            resume = ResumeHandler(
                tenant_id=b.tenant_ref,
                instructions=_ABSENT,
                recipients=_ABSENT,
                resumers={},
                alerter=_ABSENT,
                administrative_runtime=runner,
                administrative_binding=b.model_copy(deep=True),
            )
            yield Root(
                registry, journal, service, driver, runner, dispatcher, resume, saver, source, admissions
            )


def binding(
    db: Database, *, task: str = "journey.compras.step", journey: str = "SYNTHETIC.journey"
) -> JourneyBinding:
    return JourneyBinding(
        task_ref=task,
        tenant_ref=db.tenant,
        legal_entity_ref="SYNTHETIC.entity",
        topology_contract_ref="SYNTHETIC.topology.contract",
        topology_version_ref="SYNTHETIC.topology.version",
        source_transition_contract_ref="SYNTHETIC.transition.contract",
        enabled=True,
        environment_ref="SYNTHETIC.environment",
        principal_ref="lucas",
        data_policy_ref="SYNTHETIC.protected.metadata",
        journey_ref=journey,
    )


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    selected = Database(normalize_dsn(_default_test_dsn()), "pgcmp_" + uuid4().hex, "pgcmp_cp_" + uuid4().hex)
    try:
        await selected.open(install=True)
        yield selected
    finally:
        await selected.close()


async def deliver(root: Root, *, mode: str = "ack_only", outbox_ref: str | None = None) -> tuple[Any, Reply]:
    r = root.registry
    selected = outbox_ref or next(iter(r.commits.values())).outbox_intents[0].outbox_ref
    reply = Reply(r, mode=mode)
    boundary = JourneyReplyBoundary(journal=root.journal, reply=reply, authority=reply, clock=r.clock)
    result = await boundary.deliver(
        r.binding.journal_binding(),
        outbox_ref=selected,
        worker_ref="SYNTHETIC.worker",
        lease_until=r.clock() + timedelta(minutes=1),
        expected_journal_revision=await r.db.revision(r.binding),
    )
    return result, reply


async def raw_footprint(db: Database) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table in _TABLES:
        result[table] = [dict(row) for row in await db.query(f"SELECT * FROM {table} ORDER BY 1,2,3,4")]
    return result


async def checkpoint(root: Root) -> Any:
    value = await root.saver.aget_tuple(root.runner._config())
    assert value is not None
    snapshot = root.runner._snapshot(value.checkpoint["channel_values"])
    assert type(snapshot) is RuntimeCheckpointSnapshot
    assert set(snapshot.__dict__) == set(RuntimeCheckpointSnapshot.model_fields)
    assert "cursor_ref" not in RuntimeCheckpointSnapshot.model_fields
    return snapshot


@pytest.mark.parametrize("task", ["journey.compras.step", "journey.suporte.step"])
async def test_compg01_two_actual_commands_root_reconnect_fresh_resume(db: Database, task: str) -> None:
    r = Registry(db, binding(db, task=task))
    async with db.root(r, budget=2) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert isinstance(result, JourneyDispatchOutcome) and result.technical_refusal is None
        assert result.journal_revision == 6 and result.source_completion_receipt_ref is None
        assert r.source_calls == r.source_commits == 2
        assert await db.counts() == (2, 2, 2, 2, 0)
        commands = await r.snapshots()
        assert [s.technical_state for s in commands] == [CommandTechnicalState.RESPONSE_RECORDED] * 2
        first, second = (r.records[s.handle.request_sha256] for s in commands)
        assert second.action.predecessor_command_refs == (commands[0].handle.command_ref,)
        assert second.action.predecessor_source_receipt_refs == (commands[0].source_receipt_ref,)
        assert first.action.envelope.idempotency_key != second.action.envelope.idempotency_key
        assert (
            result.verified_transition_ref
            == "SYNTHETIC.transition." + second.action.envelope.idempotency_key + ".result"
        )
        audit_rows = await db.query("SELECT record_hash FROM audit_chain")
        assert {row["record_hash"] for row in audit_rows} == set(r.audits)
        assert len(audit_rows) >= 2
        before = await checkpoint(root)
        assert before.stimulus.kind == "turn" and before.outcome == result
        sent, _ = await deliver(
            root, outbox_ref=r.commits[commands[0].handle.request_sha256].outbox_intents[0].outbox_ref
        )
        assert type(sent) is tuple and sent[0] is not None and sent[1] is None
        assert await db.revision(r.binding) == 9 and r.transport_calls == 1
        boxes = await db.query("SELECT snapshot->>'technical_state' AS state FROM v21_journal_outbox")
        assert sorted(row["state"] for row in boxes) == ["ACK_RECORDED", "RECORDED"]
        footprint = await raw_footprint(db)
    await db.close_connections()
    await db.open()
    r.turn_allowed = False
    async with db.root(r, budget=2) as restarted:
        saved = await checkpoint(restarted)
        assert saved.stimulus.kind == "turn" and saved.outcome == before.outcome
        rejected = await restarted.dispatcher.accept_administrative_turn(r.stimulus(9))
        assert rejected is R.AUTHORITY_UNPROVEN
        unknown = RuntimeResumeStimulus(
            schema_version=RUNTIME_STIMULUS_SCHEMA,
            kind="resume",
            observation_ref="SYNTHETIC.unissued.observation",
            expected_journal_revision=9,
        )
        assert await restarted.resume_handler.resume_administrative(unknown) is R.AUTHORITY_UNPROVEN
        assert await checkpoint(restarted) == saved
        committed = await r.snapshots()
        assert committed[-1].verified_result_observation_ref is not None
        fresh = await r.register_resume(committed[-1].verified_result_observation_ref, 2)
        outcome = await restarted.resume_handler.resume_administrative(fresh)
        assert (
            isinstance(outcome, JourneyDispatchOutcome) and outcome.technical_refusal is R.SOURCE_UNAVAILABLE
        )
        assert outcome.journal_revision == 9 and outcome.source_completion_receipt_ref is None
        after = await checkpoint(restarted)
        assert after.stimulus.kind == "resume" and after.stimulus == fresh
        assert after.outcome == outcome and after.outcome != saved.outcome
        assert "turn" not in after.stimulus.model_dump()
        assert r.resume_cursor == ("S2_route" if task == "journey.suporte.step" else "C1_present")
        assert await raw_footprint(db) == footprint
        assert r.source_calls == r.source_commits == 2 and r.transport_calls == 1


@pytest.mark.parametrize("fault", ["new_outbox_dedupe_collision", "foreign_wait_binding"])
async def test_compg02_result_atomic_refusal_preserves_seed_and_fence(db: Database, fault: str) -> None:
    r = Registry(db, binding(db))
    async with db.root(r) as root:
        if fault == "new_outbox_dedupe_collision":
            seed = await root.dispatcher.accept_administrative_turn(r.stimulus())
            assert seed.technical_refusal is None and await db.revision(r.binding) == 3
            before = await raw_footprint(db)
            r.fault.collision_outbox = next(iter(r.commits.values())).outbox_intents[0].outbox_ref
            revision = 3
        else:
            r.fault.foreign_wait = True
            revision = 0
        result = await root.dispatcher.accept_administrative_turn(r.stimulus(revision))
        if fault == "new_outbox_dedupe_collision":
            assert result.technical_refusal is R.SOURCE_UNAVAILABLE
            inner = r.adapter_results[-1]
            assert inner.technical_status is JournalCallTechnicalStatus.CONFLICT
            assert inner.refusal_reason is JournalRefusalReason.IDENTITY_CONFLICT and inner.snapshot is None
            assert await db.revision(r.binding) == 6
            assert await db.counts() == (2, 1, 1, 1, 0)
            after = await raw_footprint(db)
            for table in _TABLES[1:]:
                assert after[table] == before[table]
            assert r.source_calls == r.source_commits == 2
        else:
            assert result.technical_refusal is R.CONTRACT_MISMATCH
            assert r.adapter_results[-1] is R.CONTRACT_MISMATCH
            assert await db.counts() == (1, 0, 0, 0, 0) and await db.revision(r.binding) == 3
            assert r.source_calls == r.source_commits == 1
        latest = (await r.snapshots())[-1]
        assert latest.technical_state is CommandTechnicalState.UNCERTAIN
        assert latest.dispatch_ref is not None and latest.fence_version == 1
        assert r.transport_calls == 0


@pytest.mark.parametrize("which", ["transition_ceiling", "operation_ceiling"])
async def test_compg03_source_internal_await_enforces_received_minimum(db: Database, which: str) -> None:
    r = Registry(db, binding(db))
    r.fault.narrow = which
    if which == "transition_ceiling":
        r.deadline = r.now + timedelta(seconds=5)
    else:
        r.op_deadline = r.now + timedelta(seconds=5)
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is R.AUTHORITY_UNPROVEN
        assert r.source_calls == 1 and r.source_commits == 0
        assert root.source.received[0].effective_valid_until == r.now + timedelta(seconds=5)
        assert await db.counts() == (1, 0, 0, 0, 0)
        assert (await r.snapshots())[0].technical_state is CommandTechnicalState.UNCERTAIN


async def test_compg04_metadata_revoked_after_real_result_commit_hides_snapshot(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.metadata_postcommit = True
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert isinstance(result, JourneyDispatchOutcome)
        assert result.technical_refusal is R.SOURCE_UNAVAILABLE
        inner = r.adapter_results[-1]
        assert inner.technical_status is JournalCallTechnicalStatus.UNAVAILABLE
        assert inner.refusal_reason is JournalRefusalReason.DATA_GATE_CLOSED and inner.snapshot is None
        assert not r.metadata_allowed and r.ingress_allowed
        assert result.source_completion_receipt_ref is None and result.verified_transition_ref is None
        assert result.source_case_refs == () and r.transport_calls == 0
        assert await db.counts() == (1, 1, 1, 1, 0) and await db.revision(r.binding) == 3
        commands = await r.snapshots()
        assert commands[0].technical_state is CommandTechnicalState.RESPONSE_RECORDED
        assert r.source_calls == r.source_commits == 1


async def test_compg05_transition_revocation_after_commit_preserves_fact(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.transition_disclosure_revoked = True
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is R.AUTHORITY_UNPROVEN
        assert await db.counts() == (1, 1, 1, 1, 0)
        assert (await r.snapshots())[0].technical_state is CommandTechnicalState.RESPONSE_RECORDED
        assert result.source_completion_receipt_ref is None and r.transport_calls == 0
        assert r.source_calls == r.source_commits == 1


async def test_compg06_source_commit_lost_reply_recovers_receipt_without_execute(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.source_reply_lost = True
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is R.SOURCE_UNAVAILABLE
        before = (await r.snapshots())[0]
        assert before.technical_state is CommandTechnicalState.UNCERTAIN
        assert r.source_calls == r.source_commits == 1 and await db.counts() == (1, 0, 0, 0, 0)
    await db.close_connections()
    await db.open()
    async with db.root(r) as restarted:
        recovered = await restarted.driver.recover(limit=10, cursor_ref=None)
        assert recovered.technical_refusal is None and r.read_calls == 1
        after = (await r.snapshots())[0]
        assert after.technical_state is CommandTechnicalState.RESPONSE_RECORDED
        assert (after.handle, after.dispatch_ref, after.fence_version) == (
            before.handle,
            before.dispatch_ref,
            before.fence_version,
        )
        assert await db.counts() == (1, 1, 1, 1, 0) and await db.revision(r.binding) == 4
        assert r.source_calls == r.source_commits == 1


async def test_compg07_actual_fence_commit_lost_ack_never_invokes_source(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.lost_ack = "fence"
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is R.SOURCE_UNAVAILABLE and r.fault.lost_ack_fired
        original = (await r.snapshots())[0]
        assert original.technical_state is CommandTechnicalState.DISPATCH_FENCED
        assert await db.revision(r.binding) == 2 and await db.counts() == (1, 0, 0, 0, 0)
        recovered = await root.driver.recover(limit=10, cursor_ref=None)
        assert recovered.technical_refusal is R.SOURCE_UNAVAILABLE
        assert (await r.snapshots())[0] == original
        assert r.source_calls == r.source_commits == 0 and r.read_calls == 1


async def test_compg08_registered_inbox_and_duplicate_commit_once(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.source_reply_lost = True
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is R.SOURCE_UNAVAILABLE
        command = (await r.snapshots())[0]
        digest = command.handle.request_sha256
        reads = root.service.durable.reads
        restored = await root.service.durable.custody.restore_command(
            r.binding.journal_binding(), command.handle.command_ref
        )
        lookup = await reads.lookup(r.binding.journal_binding(), restored, command, timeout_seconds=10)
        event = VerifiedInboxObservation(
            event_ref="SYNTHETIC.event",
            source_authority_ref="SYNTHETIC.authority",
            producer_ref="SYNTHETIC.producer",
            source_contract_revision_ref="SYNTHETIC.contract",
            event_sha256=action_digest(lookup.commit.observation),
            handle=command.handle,
            correlation_ref=restored.descriptor.envelope.correlation_ref,
            observation=lookup.commit.observation,
        )
        r.events[event.event_ref] = deepcopy(event)
        accepted = await root.driver.accept_observation(event, 3)
        assert accepted.technical_status is JournalCallTechnicalStatus.RECORDED
        assert accepted.snapshot.journal_revision == 4 and await db.counts() == (1, 1, 1, 1, 1)
        footprint = await raw_footprint(db)
        duplicate = await root.driver.accept_observation(event, 0)
        assert duplicate.technical_status is JournalCallTechnicalStatus.UNCHANGED
        assert duplicate.snapshot.journal_revision == 4 and await raw_footprint(db) == footprint
        assert r.source_calls == r.source_commits == 1 and digest in r.records


async def test_compg09_actual_result_commit_lost_ack_lookup_is_unchanged(db: Database) -> None:
    r = Registry(db, binding(db))
    r.fault.lost_ack = "result"
    async with db.root(r) as root:
        outcome = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert outcome.technical_refusal is R.SOURCE_UNAVAILABLE and r.fault.lost_ack_fired
        command = (await r.snapshots())[0]
        assert command.technical_state is CommandTechnicalState.RESPONSE_RECORDED
        footprint = await raw_footprint(db)
        result = await root.service.reconcile_durable(command.handle.command_ref, expected_journal_revision=3)
        assert result.technical_status is JournalCallTechnicalStatus.UNCHANGED
        assert (
            result.snapshot.handle == command.handle and result.snapshot.dispatch_ref == command.dispatch_ref
        )
        assert await raw_footprint(db) == footprint and await db.revision(r.binding) == 3
        assert r.source_calls == r.source_commits == 1 and r.read_calls == 1


@pytest.mark.parametrize("winner", ["writer_A_holds_aggregate_first", "writer_B_holds_aggregate_first"])
async def test_compg10_two_real_pool_forced_cas_has_one_effect(db: Database, winner: str) -> None:
    b = binding(db)
    # Explicit test-only aggregate metadata seed at revision0 avoids blocking the
    # loser's INSERT ON CONFLICT before its observable aggregate lock query.
    await db.query(
        "INSERT INTO v21_journey_journal "
        "(environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref,binding) "
        "VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)",
        b.environment_ref,
        b.tenant_ref,
        b.legal_entity_ref,
        b.journey_ref,
        b.principal_ref,
        b.task_ref,
        b.journal_binding().model_dump_json(),
    )
    a, z = Registry(db, b), Registry(db, b)
    a.key_suffix, z.key_suffix = ".writer_A", ".writer_B"
    selected, loser = (a, z) if winner.startswith("writer_A") else (z, a)
    loser.fault = selected.fault
    selected.fault.cas_initial_reads = True
    initial = selected.make_action(0, ())
    selected.fault.held_digest = request_digest(initial.envelope, initial.request)
    # One-action technical CAS fixture; COMPG01 independently retains budget2
    # success. The unchanged real driver budget stops the winner, not a post-check.
    async with (
        db.root(selected, budget=1) as winning,
        db.root(loser, budget=1, pool_index=1, loser=True) as losing,
    ):
        tasks: list[asyncio.Task[Any]] = []
        try:
            async with asyncio.timeout(15):
                first = asyncio.create_task(
                    winning.dispatcher.accept_administrative_turn(selected.stimulus())
                )
                tasks.append(first)
                second = asyncio.create_task(losing.dispatcher.accept_administrative_turn(loser.stimulus()))
                tasks.append(second)
                await selected.fault.both_initial_observed.wait()
                assert selected.fault.initial_revisions == {"winner": 0, "loser": 0}
                await selected.fault.winner_locked.wait()
                await selected.fault.loser_attempted.wait()
                assert not first.done() and not second.done()
                assert selected.fault.winner_pid is not None and selected.fault.loser_pid is not None
                assert selected.fault.winner_pid != selected.fault.loser_pid
                # Observe the actual owned PostgreSQL lock condition before
                # releasing the winner. This retries no source/transaction and
                # relies on no sleep or scheduler-assumed lock arrival.
                while True:
                    blocked = await db.query(
                        "SELECT $1::int = ANY(pg_blocking_pids($2::int)) AS blocked",
                        selected.fault.winner_pid,
                        selected.fault.loser_pid,
                    )
                    if blocked[0]["blocked"]:
                        break
                    assert not first.done() and not second.done()
                selected.fault.release_winner.set()
                positive, rejected = await asyncio.gather(first, second)
        finally:
            selected.fault.release_winner.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert positive.technical_refusal is None and rejected.technical_refusal is R.SOURCE_UNAVAILABLE
        inner = loser.adapter_results[-1]
        assert inner.technical_status is JournalCallTechnicalStatus.CONFLICT
        assert inner.refusal_reason is JournalRefusalReason.CAS_CONFLICT and inner.snapshot is None
        assert await db.revision(b) == 3 and await db.counts() == (1, 1, 1, 1, 0)
        assert selected.source_calls == selected.source_commits == 1
        assert loser.source_calls == loser.source_commits == 0 and loser.audits == {}
        command = (await selected.snapshots())[0]
        assert command.handle.request_sha256 == selected.fault.held_digest
        assert command.technical_state is CommandTechnicalState.RESPONSE_RECORDED


@pytest.mark.parametrize("scope", ["tenant", "task", "journey"])
async def test_compg11_real_saver_and_journal_scope_isolation(db: Database, scope: str) -> None:
    first = Registry(db, binding(db))
    other_db = await db.new_tenant() if scope == "tenant" else db
    second = Registry(
        other_db,
        binding(
            other_db,
            task="journey.suporte.step" if scope == "task" else "journey.compras.step",
            journey="SYNTHETIC.other.journey" if scope == "journey" else "SYNTHETIC.journey",
        ),
    )
    if scope == "journey":
        # A separately issued literal original command identity, never retry/key
        # reconstruction from the first effect or an alias across journeys.
        second.key_suffix = ".other_journey_original"
    async with db.root(first) as one:
        result = await one.dispatcher.accept_administrative_turn(first.stimulus())
        assert result.technical_refusal is None
        async with other_db.root(second) as two:
            assert one.runner._config() != two.runner._config()
            assert await one.saver.aget_tuple(two.runner._config()) is None
            positive = await two.dispatcher.accept_administrative_turn(second.stimulus())
            assert positive.technical_refusal is None
            before_one, before_two = await checkpoint(one), await checkpoint(two)
            assert before_one.binding == first.binding and before_two.binding == second.binding
            assert (
                result.journey_ref == first.binding.journey_ref
                and positive.journey_ref == second.binding.journey_ref
            )
            assert result.outbox_refs != positive.outbox_refs
            assert (
                first.source_calls
                == first.source_commits
                == second.source_calls
                == second.source_commits
                == 1
            )
            commands_one, commands_two = await first.snapshots(), await second.snapshots()
            assert len(commands_one) == len(commands_two) == 1
            assert commands_one[0].handle.binding == first.binding.journal_binding()
            assert commands_two[0].handle.binding == second.binding.journal_binding()
            assert commands_one[0].handle != commands_two[0].handle


@pytest.mark.parametrize("fault", ["hidden_extra_runtime_stimulus", "wrong_full_binding"])
async def test_compg12_actual_typed_roots_deny_before_authority_and_checkpoint(
    db: Database, fault: str
) -> None:
    r = Registry(db, binding(db))
    async with db.root(r) as root:
        turn = r.stimulus()
        resume = RuntimeResumeStimulus(
            schema_version=RUNTIME_STIMULUS_SCHEMA,
            kind="resume",
            observation_ref="SYNTHETIC.unissued",
            expected_journal_revision=0,
        )
        if fault == "hidden_extra_runtime_stimulus":
            turn = turn.model_copy(update={"untrusted_extra": "SYNTHETIC.private.marker"})
            resume = resume.model_copy(update={"untrusted_extra": "SYNTHETIC.private.marker"})
        else:
            wrong = r.binding.model_copy(
                update={"source_transition_contract_ref": "SYNTHETIC.other.transition"}
            )
            root.dispatcher.administrative_binding = wrong
            root.resume_handler.administrative_binding = wrong
        assert await root.dispatcher.accept_administrative_turn(turn) is R.AUTHORITY_UNPROVEN
        assert await root.resume_handler.resume_administrative(resume) is R.AUTHORITY_UNPROVEN
        assert r.ingress_calls == r.source_calls == r.source_commits == r.transport_calls == 0
        assert await db.counts() == (0, 0, 0, 0, 0) and await db.revision(r.binding) == 0
        assert await root.saver.aget_tuple(root.runner._config()) is None
        assert (await db.query("SELECT count(*) AS n FROM audit_chain"))[0]["n"] == 0


async def test_compg13_reply_revocation_after_target_await_preserves_uncertain_fence(db: Database) -> None:
    r = Registry(db, binding(db))
    async with db.root(r) as root:
        result = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert result.technical_refusal is None
        rejected, reply = await deliver(root, mode="revoked")
        assert rejected is R.AUTHORITY_UNPROVEN and reply.revoked
        row = (await db.query("SELECT snapshot FROM v21_journal_outbox"))[0]
        snapshot = json.loads(row["snapshot"])
        assert snapshot["technical_state"] == "UNCERTAIN" and snapshot["delivery_ref"] is not None
        assert snapshot["acknowledgement_ref"] is None
        assert await db.revision(r.binding) == 6 and r.transport_calls == 1
        again, _ = await deliver(root)
        assert again is R.SOURCE_UNAVAILABLE and r.transport_calls == 1
        assert r.source_calls == r.source_commits == 1


_REPLY_MODES = [
    "pending_receipt_with_ack",
    "attempted_receipt_with_ack",
    "unknown_receipt_with_ack",
    "unknown_receipt_no_ack",
    "no_evidence_no_ack",
    "cancel_during_targetawait",
    "unregistered_receipt",
]


@pytest.mark.parametrize("mode", _REPLY_MODES)
async def test_compg14_independent_delivery_evidence_is_separate_from_ack(db: Database, mode: str) -> None:
    r = Registry(db, binding(db))
    async with db.root(r) as root:
        initial = await root.dispatcher.accept_administrative_turn(r.stimulus())
        assert initial.technical_refusal is None and initial.source_completion_receipt_ref is None
        original = (await r.snapshots())[0]
        if mode == "cancel_during_targetawait":
            reply = Reply(r, mode=mode)
            ref = next(iter(r.commits.values())).outbox_intents[0].outbox_ref
            boundary = JourneyReplyBoundary(journal=root.journal, reply=reply, authority=reply, clock=r.clock)
            task = asyncio.create_task(
                boundary.deliver(
                    r.binding.journal_binding(),
                    outbox_ref=ref,
                    worker_ref="SYNTHETIC.worker",
                    lease_until=r.clock() + timedelta(minutes=1),
                    expected_journal_revision=3,
                )
            )
            try:
                async with asyncio.timeout(15):
                    await reply.entered.wait()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            returned: Any = None
        else:
            returned, reply = await deliver(root, mode=mode)
        state = json.loads((await db.query("SELECT snapshot FROM v21_journal_outbox"))[0]["snapshot"])
        assert await db.revision(r.binding) == 6
        assert r.source_calls == r.source_commits == r.transport_calls == 1
        assert (await r.snapshots())[0] == original
        if mode.endswith("with_ack"):
            assert type(returned) is tuple and returned[0] is not None
            receipt = returned[1]
            expected = (
                "pending"
                if mode.startswith("pending")
                else "attempted"
                if mode.startswith("attempted")
                else "unknown"
            )
            assert receipt.delivery_status == expected
            assert receipt.provider_delivery_receipt_ref is receipt.metadata_publication_receipt_ref is None
            assert receipt.attempt_revision == "SYNTHETIC.reply.attempt.revision.1"
            assert r.delivery_checks == r.ack_checks == 1
            assert state["technical_state"] == "ACK_RECORDED"
        elif mode == "unknown_receipt_no_ack":
            assert returned[0] is None and returned[1].delivery_status == "unknown"
            assert (
                returned[1].provider_delivery_receipt_ref is None
                and returned[1].metadata_publication_receipt_ref is None
            )
            assert r.delivery_checks == 1 and r.ack_checks == 0
            assert state["technical_state"] == "UNCERTAIN"
        elif mode == "no_evidence_no_ack":
            assert returned == (None, None) and r.delivery_checks == r.ack_checks == 0
            assert state["technical_state"] == "UNCERTAIN"
        elif mode == "unregistered_receipt":
            assert returned is R.AUTHORITY_UNPROVEN and r.delivery_checks == 1 and r.ack_checks == 0
            assert state["technical_state"] == "UNCERTAIN"
        else:
            assert mode == "cancel_during_targetawait" and task.done()
            assert r.delivery_checks == r.ack_checks == 0 and state["technical_state"] == "UNCERTAIN"
        repeat, _ = await deliver(root)
        assert repeat is R.SOURCE_UNAVAILABLE
        assert r.transport_calls == 1 and await db.revision(r.binding) == 6
        assert initial.source_completion_receipt_ref is None


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("target_binding_ref", "SYNTHETIC.unregistered.target"),
        ("payload_sha256", "b" * 64),
        ("outbox_ref", "SYNTHETIC.unregistered.outbox"),
        ("payload_ref", "SYNTHETIC.unregistered.payload"),
        ("claim_ref", "SYNTHETIC.unissued.claim"),
        ("fence_version", 999),
        ("worker_ref", "SYNTHETIC.unissued.worker"),
    ],
)
@pytest.mark.parametrize("phase", ["before_target", "after_target_await"])
async def test_compg15_reply_owner_rejects_original_fence_substitution(
    field_name: str, replacement: Any, phase: str
) -> None:
    """Pure registry control; constructed technical inputs do not qualify PG facts.

    The original27 cases separately retain the actual real-journal/root path.
    These controls reproduce the exact fixture-owner defect without opening DB.
    """
    db = Database("UNCONNECTED.SOURCE.CONTROL", "pgcmp_private_control", "pgcmp_cp_private_control")
    r = Registry(db, binding(db))
    scope = r.binding.journal_binding()
    action = r.make_action(0, ())
    digest = request_digest(action.envelope, action.request)
    authority = await OperationOwner(r).authorize(
        r.scope(action.envelope.operation_name), action.envelope, action.request
    )
    result = ContextAccessResult(
        access_status="available",
        context_refs=("SYNTHETIC.context",),
        source_revision_ref="SYNTHETIC.source.rev",
        access_decision_ref="SYNTHETIC.decision",
    )
    proof = VerifiedSourceResult(
        binding=authority.binding,
        request_sha256=digest,
        result_sha256=result_digest(result),
        authorization_ref=authority.authorization_ref,
        source_receipt_ref="SYNTHETIC.source.receipt",
        source_revision_ref="SYNTHETIC.source.rev",
        currentness_ref=authority.currentness_ref,
        checked_at=r.clock(),
        valid_until=authority.valid_until,
    )
    r.records[digest] = SourceRecord(action, result, proof)
    r.attestations.add(pins(proof))
    descriptor = await Custody(r).prepare_command(
        scope, r.scope(action.envelope.operation_name), action.envelope, action.request, (), None
    )
    handle = CommandHandle(binding=scope, command_ref="SYNTHETIC.command", request_sha256=digest)
    commit = await Preparation(r).prepare(scope, descriptor, handle, result, proof)
    snapshot = OutboxSnapshot(
        descriptor=commit.outbox_intents[0],
        journal_revision=5,
        technical_state=OutboxTechnicalState.DELIVERY_FENCED,
        claim_ref="SYNTHETIC.claim",
        worker_ref="SYNTHETIC.worker",
        lease_until=r.clock() + timedelta(minutes=1),
        fence_version=1,
        delivery_ref="SYNTHETIC.delivery",
    )
    # The exact registered positive must still execute, so rejection cannot be
    # supplied by a disabled target or an always-refusing fixture port.
    positive = Reply(r, mode="held_ack_only" if phase == "after_target_await" else "ack_only")
    positive.release.set()
    auth, current = await positive.authorize(scope, snapshot)
    accepted = await positive.deliver(scope, snapshot, auth, current)
    assert type(accepted) is tuple and accepted[0] is not None and accepted[1] is None
    assert r.transport_calls == 1 and await positive.verify_ack(scope, snapshot, accepted[0])
    r.reply_acks.clear()
    original_snapshot = pins(snapshot)
    reply = Reply(r, mode="held_ack_only" if phase == "after_target_await" else "ack_only")
    auth, current = await reply.authorize(scope, snapshot)
    original_auth, original_current = pins(auth), pins(current)
    invocation = reply.invocations[snapshot.delivery_ref]

    def substitute() -> None:
        if field_name in {"target_binding_ref", "payload_sha256", "outbox_ref", "payload_ref"}:
            snapshot.__dict__["descriptor"] = snapshot.descriptor.model_copy(update={field_name: replacement})
        else:
            snapshot.__dict__[field_name] = replacement

    before = r.transport_calls
    if phase == "before_target":
        substitute()
        answer = await reply.deliver(scope, snapshot, auth, current)
        assert r.transport_calls == before
    else:
        task = asyncio.create_task(reply.deliver(scope, snapshot, auth, current))
        try:
            await asyncio.wait_for(reply.entered.wait(), 10)
            substitute()
            reply.release.set()
            answer = await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert r.transport_calls == before + 1  # Entry happened, no target effect.
    assert answer is R.AUTHORITY_UNPROVEN
    assert pins(auth) == original_auth and pins(current) == original_current
    assert invocation == reply.invocations[snapshot.delivery_ref]
    assert invocation[2] == original_snapshot and pins(snapshot) != original_snapshot
    assert r.reply_acks == {} and r.reply_receipts == {}
    assert r.source_calls == r.source_commits == 0
    assert await reply.revalidate(scope, snapshot, auth, current) is R.AUTHORITY_UNPROVEN
    assert await reply.authorize(scope, snapshot) is R.AUTHORITY_UNPROVEN


async def test_compg16_unselected_fault_preserves_real_commit_ack(db: Database) -> None:
    """A normal real SQL commit with phase=None must not inject ACK loss.

    The table/row belong only to this owned test schema, not to source/journal
    authority or an operational fact. The independent admin read proves COMMIT.
    """
    await db.admin.execute(
        f'CREATE TABLE "{db.schema}".synthetic_unfaulted_commit (technical_ref text PRIMARY KEY)'
    )
    fault = Faults()
    async with db.pools[0].acquire() as connection:
        wrapped = RealConnection(connection, fault)
        transaction = wrapped.transaction()
        await transaction.start()
        await wrapped.execute(
            f'INSERT INTO "{db.schema}".synthetic_unfaulted_commit (technical_ref) VALUES ($1)',
            "SYNTHETIC.unfaulted.commit",
        )
        assert fault.lost_ack is None and wrapped.phase is None
        await transaction.commit()
        assert fault.lost_ack_fired is False and fault.last_commit_phase is None
    rows = await db.query("SELECT technical_ref FROM synthetic_unfaulted_commit")
    assert [row["technical_ref"] for row in rows] == ["SYNTHETIC.unfaulted.commit"]


@pytest.mark.parametrize(
    "fault",
    [
        "result_digest",
        "authorization_ref",
        "request_sha256",
        "binding",
        "currentness_ref",
        "source_receipt_ref",
        "source_revision_ref",
        "result_body",
        "action",
        "custody",
        "issuer_authority",
        "registered_cross_authority",
        "registered_cross_binding",
        "after_await_result",
    ],
)
async def test_compg17_operation_currentness_binds_only_original_registered_result(fault: str) -> None:
    """Pure fixture proof relation; these constructed controls qualify no PG/provider.

    Registered positive and pre-result absence precede each hostile case. Custody
    and issuer use the same existing fixture methods as real DUR3, not a digest
    computed from arbitrary caller fields as an approval.
    """
    db = Database("UNCONNECTED.RESULT.CONTROL", "pgcmp_result_control", "pgcmp_cp_result_control")
    r = Registry(db, binding(db))
    action = r.make_action(0, ())
    digest = request_digest(action.envelope, action.request)
    await Custody(r).prepare_command(
        r.binding.journal_binding(),
        r.scope(action.envelope.operation_name),
        action.envelope,
        action.request,
        (),
        None,
    )
    owner = OperationOwner(r)
    auth = await owner.authorize(r.scope(action.envelope.operation_name), action.envelope, action.request)
    before = await owner.check_current(auth)
    assert before.source_result_sha256 is None
    result = ContextAccessResult(
        access_status="available",
        context_refs=("SYNTHETIC.context",),
        source_revision_ref="SYNTHETIC.source.rev",
        access_decision_ref="SYNTHETIC.decision",
    )
    proof = VerifiedSourceResult(
        binding=auth.binding,
        request_sha256=digest,
        result_sha256=result_digest(result),
        authorization_ref=auth.authorization_ref,
        source_receipt_ref="SYNTHETIC.registered.source.receipt",
        source_revision_ref="SYNTHETIC.source.rev",
        currentness_ref=auth.currentness_ref,
        checked_at=r.clock(),
        valid_until=auth.valid_until,
    )
    r.records[digest] = SourceRecord(deepcopy(action), deepcopy(result), deepcopy(proof))
    r.attestations.add(pins(proof))
    after = await owner.check_current(auth, source_result=deepcopy(proof))
    assert after.source_result_sha256 == proof.result_sha256
    assert after.binding == before.binding == auth.binding
    assert after.authorization_ref == before.authorization_ref == auth.authorization_ref
    assert after.currentness_ref == before.currentness_ref == auth.currentness_ref
    # New owner objects after reconnect retain the original immutable issuer
    # registry; restoring a fixture owner cannot silently manufacture a grant.
    assert (
        await OperationOwner(r).check_current(auth, source_result=proof)
    ).source_result_sha256 == proof.result_sha256
    original_issuance = r.operation_issuances[auth.authorization_ref]
    if fault in {
        "result_digest",
        "authorization_ref",
        "request_sha256",
        "binding",
        "currentness_ref",
        "source_receipt_ref",
        "source_revision_ref",
    }:
        field_name = "result_sha256" if fault == "result_digest" else fault
        replacement = (
            auth.binding.model_copy(update={"tenant_ref": "SYNTHETIC.other.tenant"})
            if fault == "binding"
            else "b" * 64
            if fault in {"result_digest", "request_sha256"}
            else "SYNTHETIC.unissued." + fault
        )
        proof = proof.model_copy(update={field_name: replacement})
        # Attestation membership alone must not authorize a changed or
        # cross-authority full proof; the original protected record also binds it.
        r.attestations.add(pins(proof))
    elif fault == "result_body":
        r.records[digest].result = result.model_copy(update={"context_refs": ("SYNTHETIC.changed.body",)})
    elif fault == "action":
        r.records[digest].action = action.model_copy(update={"cursor_ref": "C_recovery"})
    elif fault == "custody":
        r.descriptors[digest] = r.descriptors[digest].model_copy(
            update={"request_ref": "SYNTHETIC.changed.custody"}
        )
    elif fault == "issuer_authority":
        auth = auth.model_copy(update={"signed_task_ref": "SYNTHETIC.changed.issuer"})
        r.authorities[auth.authorization_ref] = deepcopy(auth)
    elif fault in {"registered_cross_authority", "registered_cross_binding"}:
        proof = proof.model_copy(
            update={"authorization_ref": "SYNTHETIC.other.authority"}
            if fault == "registered_cross_authority"
            else {"binding": auth.binding.model_copy(update={"tenant_ref": "SYNTHETIC.other.tenant"})}
        )
        r.records[digest].source = deepcopy(proof)
        r.attestations.add(pins(proof))
        # Even a complete registered source-record pair cannot be attached to
        # a different original admission authority or scope.
    else:
        asyncio.get_running_loop().call_soon(
            proof.__dict__.__setitem__, "source_revision_ref", "SYNTHETIC.changed.during.await"
        )
    with pytest.raises(AdmissionDeniedError) as refused:
        await owner.check_current(auth, source_result=proof)
    assert refused.value.reason is R.AUTHORITY_UNPROVEN
    assert original_issuance == r.operation_issuances[auth.authorization_ref]
    assert r.source_calls == r.source_commits == 0


@pytest.mark.parametrize(
    ("metadata_allowed", "ingress_allowed"),
    [(False, True), (True, False), (False, False)],
)
async def test_compg18_ingress_and_journal_data_grants_are_independent(
    metadata_allowed: bool, ingress_allowed: bool
) -> None:
    """Pure grant separation; no constructed input qualifies a PG/source fact."""
    db = Database("UNCONNECTED.GRANT.CONTROL", "pgcmp_grant_control", "pgcmp_cp_grant_control")
    r = Registry(db, binding(db))
    original_turn = r.stimulus().turn
    assert await r.verify(r.binding, original_turn) == "administrative"
    assert await JournalProofs(r).authorize(r.binding.journal_binding(), "observe_journey") is True
    r.metadata_allowed, r.ingress_allowed = metadata_allowed, ingress_allowed
    admission = await r.verify(r.binding, original_turn)
    if ingress_allowed:
        assert admission == "administrative"
    else:
        assert admission is R.AUTHORITY_UNPROVEN
    assert (
        await JournalProofs(r).authorize(r.binding.journal_binding(), "observe_journey") is metadata_allowed
    )
    assert r.source_calls == r.source_commits == r.transport_calls == 0
