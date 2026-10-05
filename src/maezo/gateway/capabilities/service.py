"""Shared candidate dispatcher; no source, authority or production bindings default.

The qualified source owns atomic business revision, idempotency, durable receipts
and uncertainty recovery. This dispatcher never retries a possibly applied command
or interprets a technical timeout as a domain settlement.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS

from .admission import AdmissionDeniedError, CapabilityAdmission
from .models import (
    REQUEST_MODELS,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityOutcome,
    CapabilityRefusalReason,
    DeclaredMemberships,
    parse_envelope,
    parse_request,
    parse_result,
)

if TYPE_CHECKING:
    from .durability.models import VerifiedInboxObservation
    from .durable_execution import DurableCapabilityExecutor, DurableOutcome
    from .journeys.contracts import JourneyCapabilitySourcePort, JourneyEffectAuthority


class CapabilitySourcePort(Protocol):
    """A contracted authority adapter, injected by trusted composition.

    The adapter must preserve original command identity on replay, return only
    source facts, and retain uncertainty for reconciliation. A transport response
    alone is insufficient: admission independently attests the parsed result.
    """

    async def execute(
        self,
        envelope: CapabilityEnvelope,
        request: CandidateDTO,
        *,
        timeout_seconds: float,
    ) -> object: ...


class CapabilityService:
    def __init__(
        self,
        *,
        sources: Mapping[str, CapabilitySourcePort] | None = None,
        admission: CapabilityAdmission | None = None,
        admissions: Mapping[str, CapabilityAdmission] | None = None,
        memberships: DeclaredMemberships | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
        durable: DurableCapabilityExecutor | None = None,
        journey_sources: Mapping[str, JourneyCapabilitySourcePort] | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid capability timeout")
        if sources and any(operation not in REQUEST_MODELS for operation in sources):
            raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
        if journey_sources and any(operation not in REQUEST_MODELS for operation in journey_sources):
            raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
        if admission is not None and admissions is not None:
            raise ValueError("supply exact admission mapping or single admission")
        bindings = dict(admissions or {})
        if admission is not None:
            bindings[admission.binding.operation_name] = admission
        if any(
            operation not in REQUEST_MODELS or guard.binding.operation_name != operation
            for operation, guard in bindings.items()
        ):
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        self.sources = MappingProxyType(dict(sources or {}))
        self.journey_sources = MappingProxyType(dict(journey_sources or {}))
        self.admission = admission
        self.admissions = MappingProxyType(bindings)
        self.memberships = memberships or DeclaredMemberships()
        self.timeout_seconds = float(timeout_seconds)
        self.durable = durable

    def is_bound_to_task(self, task_ref: str) -> bool:
        """Consumer construction fence; caller input can never select a task binding."""
        return bool(self.admissions) and all(
            guard.binding.task_ref == task_ref for guard in self.admissions.values()
        )

    async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
        try:
            # Reparse even constructed models. model_construct/model_copy are not admission.
            envelope = parse_envelope(envelope)
            request = parse_request(envelope.operation_name, payload, memberships=self.memberships)
        except CapabilityContractError as exc:
            return CapabilityOutcome.refused(exc.reason)
        source = self.sources.get(envelope.operation_name)
        admission = self.admissions.get(envelope.operation_name)
        if admission is None:
            return CapabilityOutcome.refused(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if source is None:
            return CapabilityOutcome.refused(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        lease = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                lease = await admission.authorize(envelope, request)
                await admission.revalidate(lease, phase="before_source")
                raw_result = await source.execute(envelope, request, timeout_seconds=self.timeout_seconds)
                result = parse_result(envelope.operation_name, raw_result, memberships=self.memberships)
                await admission.verify_result(lease, result)
                await admission.revalidate(lease, phase="before_disclosure")
                return CapabilityOutcome(result=result)
        except (CapabilityContractError, AdmissionDeniedError) as exc:
            return CapabilityOutcome.refused(exc.reason)
        except Exception:
            # No exception/payload text leaves the port. Cancellation remains cancellation.
            # No retry, receipt fabrication, business completion or release of uncertain work.
            return CapabilityOutcome.refused(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        finally:
            if lease is not None:
                # Local invocation lifetime only; source claims and uncertain effects stay fenced.
                admission.release(lease)

    async def execute_durable(
        self,
        envelope: CapabilityEnvelope,
        payload: object,
        *,
        predecessor_command_refs: tuple[str, ...],
        expected_journal_revision: int,
        effect_authority: JourneyEffectAuthority | None,
    ) -> DurableOutcome:
        """Reference-only complete-journey adapter; no legacy/source fallback if durability is absent."""
        if self.durable is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        try:
            envelope = parse_envelope(envelope)
            request = parse_request(envelope.operation_name, payload, memberships=self.memberships)
        except CapabilityContractError as exc:
            return exc.reason
        admission = self.admissions.get(envelope.operation_name)
        if admission is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if (
            admission.binding.task_ref in {"journey.compras.step", "journey.suporte.step"}
            and effect_authority is None
        ):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        source = (
            self.journey_sources.get(envelope.operation_name)
            if effect_authority is not None
            else self.sources.get(envelope.operation_name)
        )
        if source is None:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        return await self.durable.execute(
            envelope,
            request,
            admission=admission,
            source=source,
            predecessor_command_refs=predecessor_command_refs,
            expected_journal_revision=expected_journal_revision,
            timeout_seconds=self.timeout_seconds,
            memberships=self.memberships,
            effect_authority=effect_authority,
        )

    async def observe_durable(self, command_ref: str) -> DurableOutcome:
        if self.durable is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.durable.observe(
                    command_ref, admissions=self.admissions, memberships=self.memberships
                )
        except Exception:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE

    async def reconcile_durable(self, command_ref: str, *, expected_journal_revision: int) -> DurableOutcome:
        """Independently authorized source receipt lookup; never execute or allocate another key."""
        if self.durable is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.durable.reconcile(
            command_ref,
            admissions=self.admissions,
            memberships=self.memberships,
            expected_journal_revision=expected_journal_revision,
            timeout_seconds=self.timeout_seconds,
        )

    async def ingest_durable(
        self, observation: VerifiedInboxObservation, *, expected_journal_revision: int
    ) -> DurableOutcome:
        if self.durable is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.durable.ingest(
            observation,
            admissions=self.admissions,
            memberships=self.memberships,
            expected_journal_revision=expected_journal_revision,
            timeout_seconds=self.timeout_seconds,
        )
