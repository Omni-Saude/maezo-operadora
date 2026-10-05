"""DUR1 reference-only PostgreSQL journal, default disabled.

Six own tables, tenant SET LOCAL on every acquire (DL-0017), one locked aggregate
CAS per accepted mutation. No source/send call, credential/DSN factory, migration
or grant occurs here. Independent qualified proof ports must revalidate admission
and protected references, including duplicates, inside the transaction. Unknown
commit never exposes a dispatch/delivery token or broker acknowledgement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from pydantic import BaseModel, TypeAdapter, ValidationError

from maezo.gateway.audit_postgres import schema_for_tenant
from maezo.gateway.capabilities.admission import AdmissionDTO
from maezo.gateway.capabilities.models import CapabilityContractError, Ref, parse_envelope, parse_request
from maezo.portal.engine.profile import canonicalize

from .models import (
    AwareUTCInstant,
    CommandDescriptor,
    CommandHandle,
    CommandSnapshot,
    DispatchEvidence,
    DispatchToken,
    JournalBinding,
    JournalCallResult,
    JourneySnapshot,
    LocalRevision,
    OutboxAckEvidence,
    OutboxDescriptor,
    OutboxSnapshot,
    PreDispatchRefusal,
    RecoverySnapshot,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
    WaitSnapshot,
)
from .models import (
    CommandTechnicalState as CommandState,
)
from .models import (
    JournalCallTechnicalStatus as Status,
)
from .models import (
    JournalRefusalReason as Reason,
)
from .models import (
    OutboxTechnicalState as OutboxState,
)
from .models import (
    WaitTechnicalState as WaitState,
)
from .ports import JournalProofPort


class _RefusedError(Exception):
    def __init__(self, reason: Reason, status: Status = Status.CONFLICT) -> None:
        self.reason, self.status = reason, status
        super().__init__(reason.value)


@dataclass
class _Work:
    connection: Any
    binding: JournalBinding
    revision: int
    checks: list[Callable[[], Awaitable[bool]]] = field(default_factory=list)
    time_bounds: list[Callable[[], bool]] = field(default_factory=list)


def _json(value: BaseModel) -> str:
    return value.model_dump_json()


def _digest(value: BaseModel) -> str:
    return hashlib.sha256(canonicalize(value.model_dump(mode="json"))).hexdigest()


def _accepted[T: AdmissionDTO](snapshot: T, *, unchanged: bool = False) -> JournalCallResult[T]:
    # Specialize on the actual closed record, not the unresolved function TypeVar.
    result_type: Any = JournalCallResult.__class_getitem__(type(snapshot))
    return cast(
        JournalCallResult[T],
        result_type(
            technical_status=Status.UNCHANGED if unchanged else Status.RECORDED,
            snapshot=snapshot,
        ),
    )


def _result_identity(value: VerifiedResultObservation) -> tuple[Any, ...]:
    source = value.source_result
    # Re-authentication may refresh timestamps/currentness; identity stays source-owned.
    return (
        source.binding,
        source.request_sha256,
        source.result_sha256,
        source.source_revision_ref,
        source.source_receipt_ref,
        value.result_ref,
        value.result_sha256,
        value.observer_binding_ref,
        value.provenance_ref,
    )


def _inbox_identity(value: VerifiedInboxObservation) -> tuple[Any, ...]:
    return (
        value.event_ref,
        value.source_authority_ref,
        value.producer_ref,
        value.source_contract_revision_ref,
        value.event_sha256,
        value.handle,
        value.correlation_ref,
        _result_identity(value.observation),
    )


class PostgresDurabilityJournal:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        binding: JournalBinding,
        proofs: JournalProofPort | None = None,
        enabled: bool = False,
        max_recovery_limit: int = 100,
    ) -> None:
        self._pool = pool
        self._binding = JournalBinding.model_validate(binding)
        self._schema = schema_for_tenant(binding.tenant_ref)
        self._proofs = proofs
        if type(enabled) is not bool or type(max_recovery_limit) is not int or max_recovery_limit < 1:
            raise ValueError("invalid journal configuration")
        self._enabled = enabled
        self._max_limit = max_recovery_limit
        self._ns = (binding.environment_ref, binding.tenant_ref, binding.legal_entity_ref)
        self._aggregate = (*self._ns, binding.journey_ref, binding.principal_ref, binding.task_ref)

    async def _proof(
        self, work: _Work, check: Callable[[], Awaitable[bool]], *, bounds: Callable[[], bool] | None = None
    ) -> None:
        try:
            if bounds is not None and bounds() is not True:
                raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
            if await check() is not True:
                raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
            if bounds is not None and bounds() is not True:
                raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
        except _RefusedError:
            raise
        except Exception:
            raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE) from None
        work.checks.append(check)
        if bounds is not None:
            work.time_bounds.append(bounds)

    @staticmethod
    def _check_time_bounds(work: _Work) -> None:
        # Synchronous sweep after the last await: a later verifier must not leave
        # an earlier source validity/lease bound stale at commit or disclosure.
        try:
            if any(check() is not True for check in work.time_bounds):
                raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
        except _RefusedError:
            raise
        except Exception:
            raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE) from None

    def _proof_port(self) -> JournalProofPort:
        if self._proofs is None:
            raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
        return self._proofs

    async def _run[T: AdmissionDTO](
        self,
        binding: JournalBinding,
        method: str,
        action: Callable[[_Work], Awaitable[JournalCallResult[T]]],
        *,
        create: bool = False,
        expected: int | None = None,
    ) -> JournalCallResult[T]:
        phase = "work"
        try:
            binding = JournalBinding.model_validate(binding)
            if method not in {"observe_command", "observe_journey", "recover"}:
                TypeAdapter(LocalRevision).validate_python(expected)
            if binding != self._binding:
                raise _RefusedError(Reason.BINDING_DENIED)
            if not self._enabled:
                raise _RefusedError(Reason.DATA_GATE_CLOSED, Status.UNAVAILABLE)
            port = self._proof_port()
            if await port.authorize(binding, method) is not True:
                raise _RefusedError(Reason.DATA_GATE_CLOSED, Status.UNAVAILABLE)
            async with self._pool.acquire() as connection:
                transaction = connection.transaction(isolation="read_committed")
                await transaction.start()
                try:
                    await connection.execute(f'SET LOCAL search_path TO "{self._schema}",pg_catalog')
                    if await connection.fetchval("SELECT current_schema()") != self._schema:
                        raise _RefusedError(Reason.STORE_UNAVAILABLE, Status.UNAVAILABLE)
                    if create:
                        await connection.execute(
                            "INSERT INTO v21_journey_journal "
                            "(environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
                            "principal_ref,task_ref,binding) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT DO NOTHING",
                            *self._aggregate,
                            _json(binding),
                        )
                    row = await connection.fetchrow(
                        "SELECT binding,journal_revision FROM v21_journey_journal WHERE "
                        "environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND journey_ref=$4 "
                        "AND principal_ref=$5 AND task_ref=$6 FOR UPDATE",
                        *self._aggregate,
                    )
                    if row and JournalBinding.model_validate_json(row["binding"]) != binding:
                        raise _RefusedError(Reason.IDENTITY_CONFLICT)
                    work = _Work(connection, binding, row["journal_revision"] if row else 0)
                    result = await action(work)
                    # Recheck every qualified proof after all database/port awaits.
                    for check in work.checks:
                        if await check() is not True:
                            raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
                    if await port.authorize(binding, method) is not True:
                        raise _RefusedError(Reason.DATA_GATE_CLOSED, Status.UNAVAILABLE)
                    self._check_time_bounds(work)
                    phase = "commit"
                    await transaction.commit()
                    phase = "acknowledged"
                except BaseException:
                    with suppress(Exception):
                        await asyncio.shield(transaction.rollback())
                    raise
            # COMMIT and pool release are awaits too. A revoked data lease or
            # expired evidence after a known commit cannot disclose a token/ref.
            # The committed fence remains in storage for qualified recovery.
            if await port.authorize(binding, method) is not True:
                raise _RefusedError(Reason.DATA_GATE_CLOSED, Status.UNAVAILABLE)
            for check in work.checks:
                try:
                    if await check() is not True:
                        raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE)
                except _RefusedError:
                    raise
                except Exception:
                    raise _RefusedError(Reason.PROOF_UNAVAILABLE, Status.UNAVAILABLE) from None
            self._check_time_bounds(work)
            return result
        except _RefusedError as refusal:
            return JournalCallResult[T](technical_status=refusal.status, refusal_reason=refusal.reason)
        except (ValidationError, CapabilityContractError):
            return JournalCallResult[T](
                technical_status=Status.CONFLICT, refusal_reason=Reason.CONTRACT_MISMATCH
            )
        except asyncpg.IntegrityConstraintViolationError:
            return JournalCallResult[T](
                technical_status=Status.CONFLICT, refusal_reason=Reason.IDENTITY_CONFLICT
            )
        except asyncio.CancelledError:
            if phase == "work":
                raise
            return JournalCallResult[T](
                technical_status=Status.UNCERTAIN, refusal_reason=Reason.COMMIT_UNCERTAIN
            )
        except Exception:
            unknown = phase != "work"
            return JournalCallResult[T](
                technical_status=Status.UNCERTAIN if unknown else Status.UNAVAILABLE,
                refusal_reason=Reason.COMMIT_UNCERTAIN if unknown else Reason.STORE_UNAVAILABLE,
            )

    async def _bump(self, work: _Work, expected: int) -> int:
        try:
            expected = TypeAdapter(LocalRevision).validate_python(expected)
        except Exception:
            raise _RefusedError(Reason.CONTRACT_MISMATCH) from None
        if work.revision != expected:
            raise _RefusedError(Reason.CAS_CONFLICT)
        revision = await work.connection.fetchval(
            "UPDATE v21_journey_journal SET journal_revision=journal_revision+1,updated_at=clock_timestamp() "
            "WHERE environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND journey_ref=$4 "
            "AND principal_ref=$5 AND task_ref=$6 AND journal_revision=$7 RETURNING journal_revision",
            *self._aggregate,
            expected,
        )
        if revision is None:
            raise _RefusedError(Reason.CAS_CONFLICT)
        work.revision = revision
        return int(revision)

    def _handle(self, handle: CommandHandle, descriptor: CommandDescriptor | None = None) -> None:
        handle = CommandHandle.model_validate(handle)
        if handle.binding != self._binding or (
            descriptor and handle.request_sha256 != descriptor.request_sha256
        ):
            raise _RefusedError(Reason.IDENTITY_CONFLICT)

    def _descriptor(self, descriptor: CommandDescriptor) -> None:
        parse_envelope(descriptor.envelope)
        e, b = descriptor.envelope, self._binding
        if (e.tenant_ref, e.legal_entity_ref, e.journey_ref) != (
            b.tenant_ref,
            b.legal_entity_ref,
            b.journey_ref,
        ):
            raise _RefusedError(Reason.BINDING_DENIED)
        if len(set(descriptor.predecessor_command_refs)) != len(descriptor.predecessor_command_refs):
            raise _RefusedError(Reason.CONTRACT_MISMATCH)

    async def _command(self, work: _Work, handle: CommandHandle) -> tuple[CommandDescriptor, CommandSnapshot]:
        self._handle(handle)
        row = await work.connection.fetchrow(
            "SELECT descriptor,snapshot FROM v21_capability_command WHERE "
            "environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND command_ref=$4 FOR UPDATE",
            *self._ns,
            handle.command_ref,
        )
        if row is None:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        descriptor = CommandDescriptor.model_validate_json(row["descriptor"])
        snapshot = CommandSnapshot.model_validate_json(row["snapshot"])
        if snapshot.handle != handle:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        self._handle(handle, descriptor)
        self._descriptor(descriptor)
        return descriptor, snapshot.model_copy(update={"journal_revision": work.revision})

    async def _save_command(
        self,
        work: _Work,
        snapshot: CommandSnapshot,
        *,
        evidence: DispatchEvidence | None = None,
        refusal: PreDispatchRefusal | None = None,
    ) -> None:
        snapshot = CommandSnapshot.model_validate(snapshot)
        await work.connection.execute(
            "UPDATE v21_capability_command SET snapshot=$5::jsonb,head_ref=$6,"
            "dispatch_evidence=COALESCE($7::jsonb,dispatch_evidence),"
            "pre_dispatch_refusal=COALESCE($8::jsonb,pre_dispatch_refusal) WHERE "
            "environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND command_ref=$4",
            *self._ns,
            snapshot.handle.command_ref,
            _json(snapshot),
            snapshot.verified_result_observation_ref,
            _json(evidence) if evidence else None,
            _json(refusal) if refusal else None,
        )

    def _bound_source(self, descriptor: CommandDescriptor, source: Any) -> None:
        binding = source.binding
        env = descriptor.envelope
        if (
            (
                binding.principal_ref,
                binding.task_ref,
                binding.tenant_ref,
                binding.legal_entity_ref,
                binding.operation_name,
                binding.schema_version,
                binding.source_authority_ref,
                binding.policy_revision,
                binding.data_classification,
            )
            != (
                self._binding.principal_ref,
                self._binding.task_ref,
                env.tenant_ref,
                env.legal_entity_ref,
                env.operation_name,
                env.schema_version,
                env.source_authority_ref,
                env.policy_revision,
                env.data_classification,
            )
            or source.request_sha256 != descriptor.request_sha256
            or _digest(binding) != descriptor.admission_binding_sha256
        ):
            raise _RefusedError(Reason.BINDING_DENIED)

    async def record_command(
        self, binding: JournalBinding, descriptor: CommandDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            d = CommandDescriptor.model_validate(descriptor)
            self._descriptor(d)
            port = self._proof_port()
            await self._proof(w, lambda: port.verify_command(binding, d))
            row = await w.connection.fetchrow(
                "SELECT descriptor,snapshot FROM v21_capability_command WHERE "
                "environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND operation_name=$4 "
                "AND idempotency_key=$5 FOR UPDATE",
                *self._ns,
                d.envelope.operation_name,
                d.envelope.idempotency_key,
            )
            if row:
                existing = CommandDescriptor.model_validate_json(row["descriptor"])
                snapshot = CommandSnapshot.model_validate_json(row["snapshot"])
                if existing != d or snapshot.handle.binding != binding:
                    raise _RefusedError(Reason.IDENTITY_CONFLICT)
                return _accepted(snapshot.model_copy(update={"journal_revision": w.revision}), unchanged=True)
            for predecessor in d.predecessor_command_refs:
                row = await w.connection.fetchrow(
                    "SELECT snapshot FROM v21_capability_command WHERE environment_ref=$1 AND tenant_ref=$2 "
                    "AND legal_entity_ref=$3 AND command_ref=$4",
                    *self._ns,
                    predecessor,
                )
                if (
                    row is None
                    or CommandSnapshot.model_validate_json(row["snapshot"]).handle.binding != binding
                ):
                    raise _RefusedError(Reason.IDENTITY_CONFLICT)
            revision = await self._bump(w, expected_journal_revision)
            now = datetime.now(UTC)
            snapshot = CommandSnapshot(
                handle=CommandHandle(
                    binding=binding, command_ref=uuid4().hex, request_sha256=d.request_sha256
                ),
                journal_revision=revision,
                technical_state=CommandState.RECORDED,
                recorded_at=now,
                last_observed_at=now,
            )
            await w.connection.execute(
                "INSERT INTO v21_capability_command "
                "(environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref,"
                "command_ref,operation_name,idempotency_key,descriptor,snapshot) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb)",
                *self._aggregate,
                snapshot.handle.command_ref,
                d.envelope.operation_name,
                d.envelope.idempotency_key,
                _json(d),
                _json(snapshot),
            )
            return _accepted(snapshot)

        return await self._run(
            binding, "record_command", action, create=True, expected=expected_journal_revision
        )

    async def observe_command(
        self, binding: JournalBinding, handle: CommandHandle
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            _, snapshot = await self._command(w, handle)
            return _accepted(snapshot, unchanged=True)

        return await self._run(binding, "observe_command", action)

    async def observe_journey(self, binding: JournalBinding) -> JournalCallResult[JourneySnapshot]:
        async def action(w: _Work) -> JournalCallResult[JourneySnapshot]:
            refs = []
            for table, column in (
                ("v21_capability_command", "command_ref"),
                ("v21_external_wait", "wait_ref"),
                ("v21_journal_outbox", "outbox_ref"),
            ):
                rows = await w.connection.fetch(
                    f"SELECT {column} FROM {table} WHERE environment_ref=$1 AND tenant_ref=$2 "
                    "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6 "
                    f"ORDER BY {column}",
                    *self._aggregate,
                )
                refs.append(tuple(row[column] for row in rows))
            return _accepted(
                JourneySnapshot(
                    binding=binding,
                    journal_revision=w.revision,
                    command_refs=refs[0],
                    wait_refs=refs[1],
                    outbox_refs=refs[2],
                ),
                unchanged=True,
            )

        return await self._run(binding, "observe_journey", action)

    async def begin_dispatch(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        evidence: DispatchEvidence,
        expected_journal_revision: int,
    ) -> JournalCallResult[DispatchToken]:
        async def action(w: _Work) -> JournalCallResult[DispatchToken]:
            d, snapshot = await self._command(w, handle)
            e = DispatchEvidence.model_validate(evidence)
            self._bound_source(d, e.authority)
            self._bound_source(d, e.currentness)
            if (
                e.currentness.binding != e.authority.binding
                or e.currentness.authorization_ref != e.authority.authorization_ref
            ):
                raise _RefusedError(Reason.BINDING_DENIED)

            def bounds() -> bool:
                now = datetime.now(UTC)
                return (
                    e.authority.verified_at <= now < e.authority.valid_until
                    and e.currentness.checked_at <= now < e.currentness.valid_until
                )

            await self._proof(w, lambda: self._proof_port().verify_dispatch(binding, d, e), bounds=bounds)
            if snapshot.technical_state != CommandState.RECORDED:
                raise _RefusedError(Reason.INVALID_TRANSITION)
            revision = await self._bump(w, expected_journal_revision)
            token = DispatchToken(
                handle=handle, dispatch_ref=uuid4().hex, fence_version=1, journal_revision=revision
            )
            await self._save_command(
                w,
                snapshot.model_copy(
                    update={
                        "technical_state": CommandState.DISPATCH_FENCED,
                        "dispatch_ref": token.dispatch_ref,
                        "fence_version": token.fence_version,
                        "journal_revision": revision,
                        "last_observed_at": datetime.now(UTC),
                    }
                ),
                evidence=e,
            )
            return _accepted(token)

        return await self._run(binding, "begin_dispatch", action, expected=expected_journal_revision)

    async def record_pre_dispatch_refusal(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        refusal: PreDispatchRefusal,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            _, snapshot = await self._command(w, handle)
            refusal_value = PreDispatchRefusal.model_validate(refusal)
            await self._proof(w, lambda: self._proof_port().verify_clock(binding, refusal_value.observed_at))
            if snapshot.technical_state != CommandState.RECORDED:
                raise _RefusedError(Reason.INVALID_TRANSITION)
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": CommandState.REFUSED_BEFORE_DISPATCH,
                    "journal_revision": revision,
                    "last_observed_at": refusal_value.observed_at,
                }
            )
            await self._save_command(w, snapshot, refusal=refusal_value)
            return _accepted(snapshot)

        return await self._run(
            binding, "record_pre_dispatch_refusal", action, expected=expected_journal_revision
        )

    async def mark_uncertain(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        dispatch_ref: str,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            _, snapshot = await self._command(w, handle)
            if (
                snapshot.dispatch_ref != dispatch_ref
                or snapshot.technical_state != CommandState.DISPATCH_FENCED
            ):
                raise _RefusedError(Reason.INVALID_TRANSITION)
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": CommandState.UNCERTAIN,
                    "journal_revision": revision,
                    "last_observed_at": datetime.now(UTC),
                }
            )
            await self._save_command(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "mark_uncertain", action, expected=expected_journal_revision)

    @staticmethod
    def _intents(
        outbox: tuple[OutboxDescriptor, ...], waits: tuple[WaitDescriptor, ...], wait_ref: str | None
    ) -> str:
        if type(outbox) is not tuple or type(waits) is not tuple:
            raise _RefusedError(Reason.CONTRACT_MISMATCH)
        # Order is significant: preserve exact prepared causal intents, never merge silently.
        return canonicalize(
            {
                "outbox": [v.model_dump(mode="json") for v in outbox],
                "waits": [v.model_dump(mode="json") for v in waits],
                "wait_ref": wait_ref,
            }
        ).decode()

    async def _previous(
        self, w: _Work, snapshot: CommandSnapshot
    ) -> tuple[VerifiedResultObservation, str] | None:
        if snapshot.verified_result_observation_ref is None:
            return None
        row = await w.connection.fetchrow(
            "SELECT observation,causal_intents FROM v21_journal_observation WHERE "
            "environment_ref=$1 AND tenant_ref=$2 AND legal_entity_ref=$3 AND command_ref=$4 "
            "AND observation_ref=$5",
            *self._ns,
            snapshot.handle.command_ref,
            snapshot.verified_result_observation_ref,
        )
        if row is None:
            raise _RefusedError(Reason.PROOF_UNAVAILABLE)
        return VerifiedResultObservation.model_validate_json(row["observation"]), row["causal_intents"]

    async def _verify_result(
        self,
        w: _Work,
        d: CommandDescriptor,
        dispatch_ref: str,
        previous: VerifiedResultObservation | None,
        observation: VerifiedResultObservation,
    ) -> None:
        source = observation.source_result
        self._bound_source(d, source)
        if source.result_sha256 != observation.result_sha256:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)

        def bounds() -> bool:
            now = datetime.now(UTC)
            return source.checked_at <= now < source.valid_until

        await self._proof(
            w,
            lambda: self._proof_port().verify_result(w.binding, d, dispatch_ref, previous, observation),
            bounds=bounds,
        )

    async def _arrival(
        self,
        w: _Work,
        handle: CommandHandle,
        dispatch_ref: str,
        observation: VerifiedResultObservation,
        outbox: tuple[OutboxDescriptor, ...],
        waits: tuple[WaitDescriptor, ...],
        expected: int,
        inbox: VerifiedInboxObservation | None = None,
        wait_ref: str | None = None,
    ) -> JournalCallResult[CommandSnapshot]:
        d, snapshot = await self._command(w, handle)
        if snapshot.dispatch_ref != dispatch_ref or snapshot.technical_state not in {
            CommandState.DISPATCH_FENCED,
            CommandState.UNCERTAIN,
            CommandState.RESPONSE_RECORDED,
        }:
            raise _RefusedError(Reason.INVALID_TRANSITION)
        obs = VerifiedResultObservation.model_validate(observation)
        previous_pair = await self._previous(w, snapshot)
        previous = previous_pair[0] if previous_pair else None
        await self._verify_result(w, d, dispatch_ref, previous, obs)
        intents = self._intents(outbox, waits, wait_ref)
        if previous_pair is not None:
            previous = previous_pair[0]
            if previous.source_result.source_revision_ref == obs.source_result.source_revision_ref:
                if _result_identity(previous) != _result_identity(obs) or json.loads(
                    previous_pair[1]
                ) != json.loads(intents):
                    raise _RefusedError(Reason.IDENTITY_CONFLICT)
                return _accepted(snapshot, unchanged=True)
            if obs.source_revision_order_witness_ref is None:
                raise _RefusedError(Reason.PROOF_UNAVAILABLE)
        # A settled wait is existing at transaction entry and cannot also be newly prepared.
        settled = None
        if wait_ref is not None:
            if inbox is None or any(v.intent.wait_ref == wait_ref for v in waits):
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
            settled = await self._wait(w, wait_ref)
            settled_descriptor = settled.descriptor
            await self._proof(w, lambda: self._proof_port().verify_wait(w.binding, settled_descriptor, inbox))
            intent = settled.descriptor.intent
            if (
                settled.descriptor.handle != handle
                or intent.expected_producer_ref != inbox.producer_ref
                or intent.correlation_ref != inbox.correlation_ref
                or settled.technical_state == WaitState.OBSERVATION_RECORDED
            ):
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
        # Validate and qualify the full preparation set before bump/write. Helpers reject divergence.
        for descriptor in waits:
            if descriptor.handle != handle:
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
            await self._check_wait(w, descriptor)
        for outbox_descriptor in outbox:
            if outbox_descriptor.command_ref != handle.command_ref:
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
            await self._check_outbox(w, outbox_descriptor)
        revision = await self._bump(w, expected)
        observation_ref = uuid4().hex
        await w.connection.execute(
            "INSERT INTO v21_journal_observation "
            "(environment_ref,tenant_ref,legal_entity_ref,command_ref,"
            "observation_ref,observation,causal_intents) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)",
            *self._ns,
            handle.command_ref,
            observation_ref,
            _json(obs),
            intents,
        )
        for descriptor in waits:
            await self._put_wait(w, descriptor, revision)
        for outbox_descriptor in outbox:
            await self._put_outbox(w, outbox_descriptor, revision)
        if settled:
            settled = settled.model_copy(
                update={
                    "technical_state": WaitState.OBSERVATION_RECORDED,
                    "source_observation_ref": observation_ref,
                    "journal_revision": revision,
                }
            )
            await self._save_wait(w, settled)
        snapshot = snapshot.model_copy(
            update={
                "technical_state": CommandState.RESPONSE_RECORDED,
                "journal_revision": revision,
                "verified_result_observation_ref": observation_ref,
                "result_ref": obs.result_ref,
                "result_sha256": obs.result_sha256,
                "source_receipt_ref": obs.source_result.source_receipt_ref,
                "source_revision_ref": obs.source_result.source_revision_ref,
                "last_observed_at": datetime.now(UTC),
            }
        )
        await self._save_command(w, snapshot)
        return _accepted(snapshot)

    async def record_verified_result(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        dispatch_ref: str,
        observation: VerifiedResultObservation,
        outbox_intents: tuple[OutboxDescriptor, ...],
        wait_intents: tuple[WaitDescriptor, ...],
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            return await self._arrival(
                w, handle, dispatch_ref, observation, outbox_intents, wait_intents, expected_journal_revision
            )

        return await self._run(binding, "record_verified_result", action, expected=expected_journal_revision)

    async def ingest_verified_observation(
        self,
        binding: JournalBinding,
        observation: VerifiedInboxObservation,
        wait_ref: str | None,
        outbox_intents: tuple[OutboxDescriptor, ...],
        wait_intents: tuple[WaitDescriptor, ...],
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]:
        async def action(w: _Work) -> JournalCallResult[CommandSnapshot]:
            obs = VerifiedInboxObservation.model_validate(observation)
            d, snapshot = await self._command(w, obs.handle)
            if (
                obs.correlation_ref != d.envelope.correlation_ref
                or obs.source_authority_ref != d.envelope.source_authority_ref
            ):
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
            await self._proof(w, lambda: self._proof_port().verify_inbox(binding, d, obs))
            key = (
                *self._ns,
                obs.source_authority_ref,
                obs.producer_ref,
                obs.source_contract_revision_ref,
                obs.event_ref,
            )
            existing = await w.connection.fetchrow(
                "SELECT observation,causal_intents FROM v21_journal_inbox WHERE environment_ref=$1 "
                "AND tenant_ref=$2 AND legal_entity_ref=$3 AND source_authority_ref=$4 AND producer_ref=$5 "
                "AND source_contract_revision_ref=$6 AND event_ref=$7",
                *key,
            )
            intents = self._intents(outbox_intents, wait_intents, wait_ref)
            if existing:
                old = VerifiedInboxObservation.model_validate_json(existing["observation"])
                if _inbox_identity(old) != _inbox_identity(obs) or json.loads(
                    existing["causal_intents"]
                ) != json.loads(intents):
                    raise _RefusedError(Reason.IDENTITY_CONFLICT)
                if snapshot.dispatch_ref is None:
                    raise _RefusedError(Reason.INVALID_TRANSITION)
                # Re-authenticate the original evidence, never reapply an old event over a newer head.
                await self._verify_result(w, d, snapshot.dispatch_ref, None, obs.observation)
                return _accepted(snapshot, unchanged=True)
            if snapshot.dispatch_ref is None:
                raise _RefusedError(Reason.INVALID_TRANSITION)
            # Only an existing exact inbox replay is read-only. A new event
            # identity must satisfy CAS even when its source head is unchanged.
            if w.revision != expected_journal_revision:
                raise _RefusedError(Reason.CAS_CONFLICT)
            result = await self._arrival(
                w,
                obs.handle,
                snapshot.dispatch_ref,
                obs.observation,
                outbox_intents,
                wait_intents,
                expected_journal_revision,
                obs,
                wait_ref,
            )
            if result.technical_status == Status.UNCHANGED:
                if result.snapshot is None:
                    raise _RefusedError(Reason.PROOF_UNAVAILABLE)
                revision = await self._bump(w, expected_journal_revision)
                result = _accepted(result.snapshot.model_copy(update={"journal_revision": revision}))
            await w.connection.execute(
                "INSERT INTO v21_journal_inbox (environment_ref,tenant_ref,legal_entity_ref,"
                "source_authority_ref,producer_ref,source_contract_revision_ref,event_ref,command_ref,"
                "observation,causal_intents,applied_journal_revision) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11)",
                *key,
                obs.handle.command_ref,
                _json(obs),
                intents,
                w.revision,
            )
            return result

        return await self._run(
            binding, "ingest_verified_observation", action, expected=expected_journal_revision
        )

    async def _wait(self, w: _Work, wait_ref: str) -> WaitSnapshot:
        row = await w.connection.fetchrow(
            "SELECT snapshot FROM v21_external_wait WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6 AND wait_ref=$7",
            *self._aggregate,
            wait_ref,
        )
        if row is None:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        result = WaitSnapshot.model_validate_json(row["snapshot"])
        self._handle(result.descriptor.handle)
        return result.model_copy(update={"journal_revision": w.revision})

    async def _check_wait(self, w: _Work, descriptor: WaitDescriptor) -> WaitSnapshot | None:
        descriptor = WaitDescriptor.model_validate(descriptor)
        parse_request("external_wait.settle", descriptor.intent)
        d, _ = await self._command(w, descriptor.handle)
        if descriptor.intent.correlation_ref != d.envelope.correlation_ref:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        await self._proof(w, lambda: self._proof_port().verify_wait(w.binding, descriptor, None))
        row = await w.connection.fetchrow(
            "SELECT snapshot FROM v21_external_wait WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 AND task_ref=$5 AND wait_ref=$6",
            *self._ns,
            self._binding.journey_ref,
            self._binding.task_ref,
            descriptor.intent.wait_ref,
        )
        if row is None:
            return None
        existing = WaitSnapshot.model_validate_json(row["snapshot"])
        if existing.descriptor != descriptor:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        return existing.model_copy(update={"journal_revision": w.revision})

    async def _put_wait(self, w: _Work, descriptor: WaitDescriptor, revision: int) -> WaitSnapshot:
        existing = await self._check_wait(w, descriptor)
        if existing:
            return existing
        snapshot = WaitSnapshot(
            descriptor=descriptor, journal_revision=revision, technical_state=WaitState.OPEN
        )
        await w.connection.execute(
            "INSERT INTO v21_external_wait (environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,wait_ref,command_ref,descriptor,snapshot) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)",
            *self._aggregate,
            descriptor.intent.wait_ref,
            descriptor.handle.command_ref,
            _json(descriptor),
            _json(snapshot),
        )
        return snapshot

    async def _save_wait(self, w: _Work, snapshot: WaitSnapshot) -> None:
        await w.connection.execute(
            "UPDATE v21_external_wait SET snapshot=$8::jsonb WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6 AND wait_ref=$7",
            *self._aggregate,
            snapshot.descriptor.intent.wait_ref,
            _json(snapshot),
        )

    async def record_wait(
        self, binding: JournalBinding, descriptor: WaitDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[WaitSnapshot]:
        async def action(w: _Work) -> JournalCallResult[WaitSnapshot]:
            existing = await self._check_wait(w, descriptor)
            if existing:
                return _accepted(existing, unchanged=True)
            revision = await self._bump(w, expected_journal_revision)
            return _accepted(await self._put_wait(w, descriptor, revision))

        return await self._run(binding, "record_wait", action, expected=expected_journal_revision)

    async def mark_wait_elapsed(
        self, binding: JournalBinding, wait_ref: str, observed_at: datetime, expected_journal_revision: int
    ) -> JournalCallResult[WaitSnapshot]:
        async def action(w: _Work) -> JournalCallResult[WaitSnapshot]:
            observed = TypeAdapter(AwareUTCInstant).validate_python(observed_at, strict=True)
            snapshot = await self._wait(w, wait_ref)
            await self._proof(w, lambda: self._proof_port().verify_clock(binding, observed))
            wakeup = snapshot.descriptor.operational_wakeup_at
            if (
                snapshot.technical_state != WaitState.OPEN
                or wakeup is None
                or not wakeup <= observed <= datetime.now(UTC)
            ):
                raise _RefusedError(Reason.INVALID_TRANSITION)
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": WaitState.ELAPSED,
                    "journal_revision": revision,
                    "elapsed_observed_at": observed,
                }
            )
            await self._save_wait(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "mark_wait_elapsed", action, expected=expected_journal_revision)

    def _stored_outbox(self, w: _Work, row: Any) -> OutboxSnapshot:
        descriptor = OutboxDescriptor.model_validate_json(row["descriptor"])
        snapshot = OutboxSnapshot.model_validate_json(row["snapshot"])
        scope = tuple(
            row[key]
            for key in (
                "environment_ref",
                "tenant_ref",
                "legal_entity_ref",
                "journey_ref",
                "principal_ref",
                "task_ref",
            )
        )
        if (
            scope != self._aggregate
            or snapshot.descriptor != descriptor
            or row["outbox_ref"] != descriptor.outbox_ref
            or row["command_ref"] != descriptor.command_ref
        ):
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        return snapshot.model_copy(update={"journal_revision": w.revision})

    async def _outbox(self, w: _Work, outbox_ref: str) -> OutboxSnapshot:
        row = await w.connection.fetchrow(
            "SELECT descriptor,snapshot,environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,outbox_ref,command_ref FROM v21_journal_outbox "
            "WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND journey_ref=$4 "
            "AND principal_ref=$5 AND task_ref=$6 AND outbox_ref=$7",
            *self._aggregate,
            outbox_ref,
        )
        if row is None:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        snapshot = self._stored_outbox(w, row)
        command = await w.connection.fetchrow(
            "SELECT snapshot FROM v21_capability_command WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND command_ref=$4",
            *self._ns,
            snapshot.descriptor.command_ref,
        )
        if (
            command is None
            or CommandSnapshot.model_validate_json(command["snapshot"]).handle.binding != w.binding
        ):
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        return snapshot

    async def _check_outbox(self, w: _Work, descriptor: OutboxDescriptor) -> OutboxSnapshot | None:
        descriptor = OutboxDescriptor.model_validate(descriptor)
        row = await w.connection.fetchrow(
            "SELECT snapshot FROM v21_capability_command WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND command_ref=$4",
            *self._ns,
            descriptor.command_ref,
        )
        if row is None or CommandSnapshot.model_validate_json(row["snapshot"]).handle.binding != w.binding:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        await self._proof(w, lambda: self._proof_port().verify_outbox(w.binding, descriptor, None, None))
        row = await w.connection.fetchrow(
            "SELECT descriptor,snapshot,environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,outbox_ref,command_ref FROM v21_journal_outbox "
            "WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND outbox_ref=$4",
            *self._ns,
            descriptor.outbox_ref,
        )
        if row is None:
            return None
        existing = self._stored_outbox(w, row)
        if existing.descriptor != descriptor:
            raise _RefusedError(Reason.IDENTITY_CONFLICT)
        return existing.model_copy(update={"journal_revision": w.revision})

    async def _put_outbox(self, w: _Work, descriptor: OutboxDescriptor, revision: int) -> OutboxSnapshot:
        existing = await self._check_outbox(w, descriptor)
        if existing:
            return existing
        snapshot = OutboxSnapshot(
            descriptor=descriptor, journal_revision=revision, technical_state=OutboxState.RECORDED
        )
        await w.connection.execute(
            "INSERT INTO v21_journal_outbox (environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,outbox_ref,command_ref,descriptor,snapshot) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)",
            *self._aggregate,
            descriptor.outbox_ref,
            descriptor.command_ref,
            _json(descriptor),
            _json(snapshot),
        )
        return snapshot

    async def _save_outbox(self, w: _Work, snapshot: OutboxSnapshot) -> None:
        snapshot = OutboxSnapshot.model_validate(snapshot)
        await w.connection.execute(
            "UPDATE v21_journal_outbox SET snapshot=$5::jsonb WHERE environment_ref=$1 AND tenant_ref=$2 "
            "AND legal_entity_ref=$3 AND outbox_ref=$4",
            *self._ns,
            snapshot.descriptor.outbox_ref,
            _json(snapshot),
        )

    async def enqueue_outbox(
        self, binding: JournalBinding, descriptor: OutboxDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]:
        async def action(w: _Work) -> JournalCallResult[OutboxSnapshot]:
            existing = await self._check_outbox(w, descriptor)
            if existing:
                return _accepted(existing, unchanged=True)
            revision = await self._bump(w, expected_journal_revision)
            return _accepted(await self._put_outbox(w, descriptor, revision))

        return await self._run(binding, "enqueue_outbox", action, expected=expected_journal_revision)

    async def claim_outbox(
        self,
        binding: JournalBinding,
        outbox_ref: str,
        worker_ref: str,
        lease_until: datetime,
        expected_journal_revision: int,
    ) -> JournalCallResult[OutboxSnapshot]:
        async def action(w: _Work) -> JournalCallResult[OutboxSnapshot]:
            worker = TypeAdapter(Ref).validate_python(worker_ref, strict=True)
            lease = TypeAdapter(AwareUTCInstant).validate_python(lease_until, strict=True)
            snapshot = await self._outbox(w, outbox_ref)
            if (
                snapshot.technical_state not in {OutboxState.RECORDED, OutboxState.CLAIMED}
                or snapshot.delivery_ref is not None
                or (snapshot.lease_until is not None and snapshot.lease_until > datetime.now(UTC))
            ):
                raise _RefusedError(Reason.INVALID_TRANSITION)

            await self._proof(
                w,
                lambda: self._proof_port().verify_outbox(binding, snapshot.descriptor, worker, None),
                bounds=lambda: lease > datetime.now(UTC),
            )
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": OutboxState.CLAIMED,
                    "journal_revision": revision,
                    "claim_ref": uuid4().hex,
                    "worker_ref": worker,
                    "lease_until": lease,
                    "fence_version": (snapshot.fence_version or 0) + 1,
                }
            )
            await self._save_outbox(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "claim_outbox", action, expected=expected_journal_revision)

    async def begin_outbox_delivery(
        self, binding: JournalBinding, outbox_ref: str, claim_ref: str, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]:
        async def action(w: _Work) -> JournalCallResult[OutboxSnapshot]:
            snapshot = await self._outbox(w, outbox_ref)
            if (
                snapshot.technical_state != OutboxState.CLAIMED
                or snapshot.claim_ref != claim_ref
                or snapshot.delivery_ref is not None
            ):
                raise _RefusedError(Reason.INVALID_TRANSITION)

            def bounds() -> bool:
                return snapshot.lease_until is not None and snapshot.lease_until > datetime.now(UTC)

            await self._proof(
                w,
                lambda: self._proof_port().verify_outbox(
                    binding, snapshot.descriptor, snapshot.worker_ref, None
                ),
                bounds=bounds,
            )
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": OutboxState.DELIVERY_FENCED,
                    "journal_revision": revision,
                    "delivery_ref": uuid4().hex,
                }
            )
            await self._save_outbox(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "begin_outbox_delivery", action, expected=expected_journal_revision)

    async def record_outbox_ack(
        self,
        binding: JournalBinding,
        outbox_ref: str,
        delivery_ref: str,
        evidence: OutboxAckEvidence,
        expected_journal_revision: int,
    ) -> JournalCallResult[OutboxSnapshot]:
        async def action(w: _Work) -> JournalCallResult[OutboxSnapshot]:
            snapshot = await self._outbox(w, outbox_ref)
            e = OutboxAckEvidence.model_validate(evidence)
            if (
                (e.outbox_ref, e.delivery_ref, e.target_binding_ref, e.payload_sha256)
                != (
                    outbox_ref,
                    delivery_ref,
                    snapshot.descriptor.target_binding_ref,
                    snapshot.descriptor.payload_sha256,
                )
                or snapshot.delivery_ref != delivery_ref
                or snapshot.technical_state
                not in {OutboxState.DELIVERY_FENCED, OutboxState.UNCERTAIN, OutboxState.ACK_RECORDED}
            ):
                raise _RefusedError(Reason.IDENTITY_CONFLICT)
            await self._proof(
                w,
                lambda: self._proof_port().verify_outbox(
                    binding, snapshot.descriptor, snapshot.worker_ref, e
                ),
            )
            if e.observed_at > datetime.now(UTC):
                raise _RefusedError(Reason.PROOF_UNAVAILABLE)
            if snapshot.technical_state == OutboxState.ACK_RECORDED:
                if (snapshot.acknowledgement_ref, snapshot.acknowledgement_sha256) != (
                    e.acknowledgement_ref,
                    e.acknowledgement_sha256,
                ):
                    raise _RefusedError(Reason.IDENTITY_CONFLICT)
                return _accepted(snapshot, unchanged=True)
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={
                    "technical_state": OutboxState.ACK_RECORDED,
                    "journal_revision": revision,
                    "acknowledgement_ref": e.acknowledgement_ref,
                    "acknowledgement_sha256": e.acknowledgement_sha256,
                }
            )
            await self._save_outbox(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "record_outbox_ack", action, expected=expected_journal_revision)

    async def mark_outbox_uncertain(
        self, binding: JournalBinding, outbox_ref: str, delivery_ref: str, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]:
        async def action(w: _Work) -> JournalCallResult[OutboxSnapshot]:
            snapshot = await self._outbox(w, outbox_ref)
            if (
                snapshot.delivery_ref != delivery_ref
                or snapshot.technical_state != OutboxState.DELIVERY_FENCED
            ):
                raise _RefusedError(Reason.INVALID_TRANSITION)
            revision = await self._bump(w, expected_journal_revision)
            snapshot = snapshot.model_copy(
                update={"technical_state": OutboxState.UNCERTAIN, "journal_revision": revision}
            )
            await self._save_outbox(w, snapshot)
            return _accepted(snapshot)

        return await self._run(binding, "mark_outbox_uncertain", action, expected=expected_journal_revision)

    async def recover(
        self, binding: JournalBinding, limit: int, cursor_ref: str | None
    ) -> JournalCallResult[RecoverySnapshot]:
        async def action(w: _Work) -> JournalCallResult[RecoverySnapshot]:
            if type(limit) is not int or not 1 <= limit <= self._max_limit:
                raise _RefusedError(Reason.CONTRACT_MISMATCH)
            candidates: list[tuple[str, str]] = []
            entities = (
                ("command", "v21_capability_command", "command_ref", {"DISPATCH_FENCED", "UNCERTAIN"}),
                ("outbox", "v21_journal_outbox", "outbox_ref", {"DELIVERY_FENCED", "UNCERTAIN"}),
                ("wait", "v21_external_wait", "wait_ref", {"OPEN", "ELAPSED"}),
            )
            anchors = set()
            for kind, table, column, states in entities:
                rows = await w.connection.fetch(
                    f"SELECT {column},snapshot FROM {table} WHERE environment_ref=$1 AND tenant_ref=$2 "
                    "AND legal_entity_ref=$3 AND journey_ref=$4 AND principal_ref=$5 AND task_ref=$6",
                    *self._aggregate,
                )
                for row in rows:
                    anchor = kind + ":" + row[column]
                    anchors.add(anchor)
                    if json.loads(row["snapshot"])["technical_state"] in states:
                        candidates.append((kind, row[column]))
            if cursor_ref is not None and cursor_ref not in anchors:
                raise _RefusedError(Reason.BINDING_DENIED)
            candidates.sort()  # Technical pagination only; never source revision ordering.
            selected = [v for v in candidates if cursor_ref is None or v[0] + ":" + v[1] > cursor_ref][
                : limit + 1
            ]
            more, selected = len(selected) > limit, selected[:limit]
            return _accepted(
                RecoverySnapshot(
                    binding=binding,
                    journal_revision=w.revision,
                    command_refs_for_reconciliation=tuple(ref for kind, ref in selected if kind == "command"),
                    outbox_refs_for_reconciliation=tuple(ref for kind, ref in selected if kind == "outbox"),
                    wait_refs_for_observation=tuple(ref for kind, ref in selected if kind == "wait"),
                    next_cursor_ref=(selected[-1][0] + ":" + selected[-1][1]) if more else None,
                ),
                unchanged=True,
            )

        return await self._run(binding, "recover", action)
