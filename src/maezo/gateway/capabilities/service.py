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
from typing import Protocol

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS

from .admission import AdmissionDeniedError, CapabilityAdmission
from .models import (
    OPERATION_NAMES,
    AnyCapabilityEnvelope,
    CandidateDTO,
    CapabilityContractError,
    CapabilityOutcome,
    CapabilityRefusalReason,
    ContractResolutionUnavailable,
    DeclaredMemberships,
    parse_envelope,
    parse_request,
    parse_result,
)


class CapabilitySourcePort(Protocol):
    """A contracted authority adapter, injected by trusted composition.

    The adapter must preserve original command identity on replay, return only
    source facts, and retain uncertainty for reconciliation. A transport response
    alone is insufficient: admission independently attests the parsed result.
    """

    async def execute(
        self,
        envelope: AnyCapabilityEnvelope,
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
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid capability timeout")
        if sources and any(operation not in OPERATION_NAMES for operation in sources):
            raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
        if admission is not None and admissions is not None:
            raise ValueError("supply exact admission mapping or single admission")
        bindings = dict(admissions or {})
        if admission is not None:
            bindings[admission.binding.operation_name] = admission
        if any(
            operation not in OPERATION_NAMES or guard.binding.operation_name != operation
            for operation, guard in bindings.items()
        ):
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        for operation, source in (sources or {}).items():
            source_binding = getattr(source, "binding", None)
            guard = bindings.get(operation)
            if source_binding is not None and (guard is None or source_binding != guard.binding):
                raise CapabilityContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        self.sources = MappingProxyType(dict(sources or {}))
        self.admission = admission
        self.admissions = MappingProxyType(bindings)
        self.memberships = memberships or DeclaredMemberships()
        self.timeout_seconds = float(timeout_seconds)

    def is_bound_to_task(self, task_ref: str) -> bool:
        """Consumer construction fence; caller input can never select a task binding."""
        return bool(self.admissions) and all(
            guard.binding.task_ref == task_ref for guard in self.admissions.values()
        )

    @staticmethod
    def _refused(envelope: AnyCapabilityEnvelope, reason: CapabilityRefusalReason) -> CapabilityOutcome:
        if envelope.operation_name == "contract.authority.resolve" and reason in {
            CapabilityRefusalReason.SOURCE_UNAVAILABLE,
            CapabilityRefusalReason.AUTHORITY_UNPROVEN,
        }:
            return CapabilityOutcome(refusal=reason, unavailable_observation=ContractResolutionUnavailable())
        return CapabilityOutcome.refused(reason)

    async def execute(self, envelope: AnyCapabilityEnvelope, payload: object) -> CapabilityOutcome:
        try:
            # Reparse even constructed models. model_construct/model_copy are not admission.
            envelope = parse_envelope(envelope)
            request = parse_request(
                envelope.operation_name,
                payload,
                memberships=self.memberships,
                schema_version=envelope.schema_version,
            )
        except CapabilityContractError as exc:
            return CapabilityOutcome.refused(exc.reason)
        source = self.sources.get(envelope.operation_name)
        admission = self.admissions.get(envelope.operation_name)
        if admission is None:
            return self._refused(envelope, CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if source is None:
            return self._refused(envelope, CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        lease = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                lease = await admission.authorize(envelope, request)
                await admission.revalidate(lease, phase="before_source")
                raw_result = await source.execute(envelope, request, timeout_seconds=self.timeout_seconds)
                result = parse_result(
                    envelope.operation_name,
                    raw_result,
                    memberships=self.memberships,
                    schema_version=envelope.schema_version,
                    request=request,
                )
                await admission.verify_result(lease, result)
                await admission.revalidate(lease, phase="before_disclosure")
                return CapabilityOutcome(result=result)
        except (CapabilityContractError, AdmissionDeniedError) as exc:
            return self._refused(envelope, exc.reason)
        except Exception:
            # No exception/payload text leaves the port. Cancellation remains cancellation.
            # No retry, receipt fabrication, business completion or release of uncertain work.
            return self._refused(envelope, CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        finally:
            if lease is not None:
                # Local invocation lifetime only; source claims and uncertain effects stay fenced.
                admission.release(lease)
