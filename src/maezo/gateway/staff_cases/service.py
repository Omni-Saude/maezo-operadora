"""SC1 exact AUTH staff detail: observe, freeze, lock identity, finalize, release.

List/search/checkpoint pagination are required later construction. This first
entry point refuses non-null cursors or an over-limit native result explicitly.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from maezo.gateway.external_cases.models import Digest, Ref, timestamp
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.staff_cases import StaffDetail
from maezo.portal.engine.profile import canonicalize

from .models import Closed, N, T, Proof, StaffCaseError
from .postgres import StaffSessionLease
from .publisher import StaffNativeClient, StaffWitnessSource


class ReadPin(Closed):
    kind: Literal["designation", "identity", "membership", "case_grant", "native_case", "native_task",
                  "task_disclosure", "source_key", "policy_head", "native_task_created_at"]
    ref: Ref
    revision: N
    digest: Digest
    valid_until: T


class NativeObservation(Closed):
    schema_: Literal["staff-case-observation.v1"] = Field(alias="schema")
    request_digest: Digest
    projection: StaffDetail
    continuity_ref: Ref
    continuity_digest: Digest
    source_pins: tuple[ReadPin, ...]
    observed_at: T
    valid_until: T
    proof: Proof


def observation(value: dict[str, Any], *, request_digest: str, case_ref: str,
                limit: int, now: datetime) -> NativeObservation:
    result = parse_model(NativeObservation, value)
    public = result.projection
    keys = [(pin.kind, pin.ref) for pin in result.source_pins]
    if (result.request_digest != request_digest or public.case.case_ref != case_ref
            or not public.tasks_complete or public.next_task_cursor is not None
            or len(public.active_tasks) > limit or len(keys) != len(set(keys))
            or result.proof.purpose != "native_result"
            or timestamp(result.observed_at) > now or now >= timestamp(result.valid_until)
            or public.freshness.observed_at != result.observed_at
            or public.freshness.valid_until != result.valid_until
            or any(timestamp(pin.valid_until) < timestamp(result.valid_until) for pin in result.source_pins)
            or not {"designation", "identity", "membership", "case_grant", "native_case", "policy_head"}
                   <= {pin.kind for pin in result.source_pins}):
        raise StaffCaseError("unavailable")
    task_ids = {task.task_id for task in public.active_tasks}
    for kind in ("native_task", "native_task_created_at"):
        if {pin.ref for pin in result.source_pins if pin.kind == kind} != task_ids:
            raise StaffCaseError("unavailable")
    return result


class StaffCaseService:
    def __init__(self, resolver: HumanSessionResolver, lease: StaffSessionLease,
                 witnesses: StaffWitnessSource, native: StaffNativeClient):
        if (resolver.settings.tenant != lease.tenant or resolver.settings.issuer != lease.issuer
                or native.authority.designation.scope != witnesses.source.scope
                or lease.tenant != witnesses.source.scope.tenant or native.signer.role != "read_requester"):
            raise StaffCaseError("unavailable")
        self.resolver, self.lease, self.witnesses, self.native = resolver, lease, witnesses, native

    async def read(self, secret: str, *, case_ref: str | None = None,
                   task_limit: str = "25", task_cursor: str | None = None) -> bytes:
        try:
            return await self._read(secret, case_ref=case_ref, task_limit=task_limit, task_cursor=task_cursor)
        except asyncio.CancelledError:
            raise
        except StaffCaseError:
            raise
        except Exception:
            raise StaffCaseError("unavailable") from None

    async def _read(self, secret: str, *, case_ref: str | None = None,
                   task_limit: str = "25", task_cursor: str | None = None) -> bytes:
        # Validate exact native grammar locally before any request; no arbitrary query.
        from .models import CaseRef
        from pydantic import TypeAdapter
        try:
            ref = TypeAdapter(CaseRef).validate_python(case_ref, strict=True)
            if not task_limit.isascii() or not task_limit.isdecimal() or str(int(task_limit)) != task_limit or not 1 <= int(task_limit) <= 100:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise StaffCaseError("invalid") from None
        if task_cursor is not None:
            raise StaffCaseError("unavailable")
        resolved = await self.resolver.resolve(secret)
        if resolved.membership.audience != "staff":
            raise StaffCaseError("denied")
        principal = resolved.principal
        session_until = min(resolved.record.expires_at, resolved.membership.reviewed_until)
        witness = await self.witnesses.observe(principal, session_until)
        value, request_digest = await self.native.read(principal=principal, witness=witness,
            session_until=session_until, operation="detail",
            query={"case_ref": ref, "task_limit": task_limit, "task_cursor": None})
        observed = observation(value, request_digest=request_digest, case_ref=ref,
                               limit=int(task_limit), now=self.lease.clock())
        frozen = canonicalize(wire(observed.projection))
        # An identity transaction holds the real session and membership through the
        # native final check. It does not call the resolver or replace the witness.
        async with self.lease.acquire(secret, principal) as locked:
            locked.current(self.lease.clock())
            value, final_digest = await self.native.read(principal=principal, witness=witness,
                session_until=locked.valid_until, operation="finalize",
                query={"continuity_ref": observed.continuity_ref,
                       "frozen_projection_digest": hashlib.sha256(frozen).hexdigest(),
                       "subordinate_continuities": []})
            final = observation(value, request_digest=final_digest, case_ref=ref,
                                limit=int(task_limit), now=self.lease.clock())
            if (final.continuity_ref != observed.continuity_ref or final.continuity_digest != observed.continuity_digest
                    or final.projection != observed.projection or final.source_pins != observed.source_pins
                    or final.observed_at != observed.observed_at or final.valid_until != observed.valid_until):
                raise StaffCaseError("conflict")
            locked.current(self.lease.clock())
        # Both native response cleanup and identity transaction release have completed.
        # Return the original frozen bytes, with no reserialization or awaited work.
        if self.lease.clock() >= min(session_until, timestamp(observed.valid_until)):
            raise StaffCaseError("unavailable")
        return frozen
