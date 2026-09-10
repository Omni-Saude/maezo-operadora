"""SC1 exact AUTH staff detail: observe, freeze, lock identity, finalize, release.

List/search/checkpoint pagination are required later construction. This first
entry point refuses non-null cursors or an over-limit native result explicitly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from maezo.gateway.external_cases.models import CaseRef, Digest, Ref, timestamp
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.staff_cases import (
    ObservationTimes,
    StaffDetail,
    StaffDetailShape,
    StaffPage,
    StaffPageShape,
)
from maezo.portal.engine.profile import canonicalize

from .models import Closed, N, Proof, StaffCaseError, T
from .postgres import StaffSessionLease
from .publisher import StaffNativeClient, StaffWitnessSource


class ReadPin(Closed):
    kind: Literal[
        "designation",
        "identity",
        "membership",
        "case_grant",
        "native_case",
        "native_task",
        "task_disclosure",
        "source_key",
        "policy_head",
        "native_task_created_at",
    ]
    ref: Ref
    revision: N
    digest: Digest
    valid_until: T


class NativeFreshness(ObservationTimes):
    refresh_after_seconds: Literal["10"]


class NativeStaffDetail(StaffDetailShape[NativeFreshness]):
    """Exact number-free native projection, retained unchanged for finalization."""


class NativeStaffPage(StaffPageShape[NativeFreshness]):
    """Exact number-free native list projection retained for finalization."""


class NativeObservation(Closed):
    schema_: Literal["staff-case-observation.v1"] = Field(alias="schema")
    request_digest: Digest
    projection: NativeStaffDetail | NativeStaffPage
    continuity_ref: Ref
    continuity_digest: Digest
    source_pins: tuple[ReadPin, ...]
    observed_at: T
    valid_until: T
    proof: Proof


def observation(
    value: dict[str, Any],
    *,
    request_digest: str,
    limit: int,
    now: datetime,
    case_ref: str | None = None,
) -> NativeObservation:
    result = parse_model(NativeObservation, value)
    public = result.projection
    keys = [(pin.kind, pin.ref) for pin in result.source_pins]
    if (
        result.request_digest != request_digest
        or (isinstance(public, NativeStaffDetail) and public.case.case_ref != case_ref)
        or (isinstance(public, NativeStaffDetail) and not public.tasks_complete)
        or (isinstance(public, NativeStaffDetail) and public.next_task_cursor is not None)
        or (isinstance(public, NativeStaffDetail) and len(public.active_tasks) > limit)
        or (isinstance(public, NativeStaffPage) and len(public.items) > limit)
        or len(keys) != len(set(keys))
        or result.proof.purpose != "native_result"
        or timestamp(result.observed_at) > now
        or now >= timestamp(result.valid_until)
        or public.freshness.observed_at != result.observed_at
        or public.freshness.valid_until != result.valid_until
        or any(timestamp(pin.valid_until) < timestamp(result.valid_until) for pin in result.source_pins)
        or not (
            {"designation", "identity", "membership", "case_grant", "native_case", "policy_head"}
            if isinstance(public, NativeStaffDetail)
            else {"designation", "membership", "checkpoint", "source_key"}
        )
        <= {pin.kind for pin in result.source_pins}
    ):
        raise StaffCaseError("unavailable")
    if isinstance(public, NativeStaffDetail):
        task_ids = {task.task_id for task in public.active_tasks}
        for kind in ("native_task", "native_task_created_at"):
            if {pin.ref for pin in result.source_pins if pin.kind == kind} != task_ids:
                raise StaffCaseError("unavailable")
    else:
        refs = {item.case_ref for item in public.items}
        for kind in ("case_grant", "native_case"):
            if {pin.ref for pin in result.source_pins if pin.kind == kind} != refs:
                raise StaffCaseError("unavailable")
    return result


class StaffCaseService:
    def __init__(
        self,
        resolver: HumanSessionResolver,
        lease: StaffSessionLease,
        witnesses: StaffWitnessSource,
        native: StaffNativeClient,
    ):
        if (
            resolver.settings.tenant != lease.tenant
            or resolver.settings.issuer != lease.issuer
            or native.authority.designation.scope != witnesses.source.scope
            or lease.tenant != witnesses.source.scope.tenant
            or native.signer.role != "read_requester"
        ):
            raise StaffCaseError("unavailable")
        self.resolver, self.lease, self.witnesses, self.native = resolver, lease, witnesses, native

    async def read(
        self,
        secret: str,
        *,
        case_ref: str | None = None,
        task_limit: str = "25",
        task_cursor: str | None = None,
    ) -> bytes:
        try:
            return await self._read(secret, case_ref=case_ref, task_limit=task_limit, task_cursor=task_cursor)
        except (asyncio.CancelledError, AuthenticationError):
            raise
        except StaffCaseError:
            raise
        except Exception:
            raise StaffCaseError("unavailable") from None

    async def list(
        self,
        secret: str,
        *,
        kind: str = "authorization",
        limit: str = "25",
        cursor: str | None = None,
    ) -> bytes:
        try:
            return await self._list(secret, kind=kind, limit=limit, cursor=cursor)
        except (asyncio.CancelledError, AuthenticationError):
            raise
        except StaffCaseError:
            raise
        except Exception:
            raise StaffCaseError("unavailable") from None

    async def _list(self, secret: str, *, kind: str, limit: str, cursor: str | None) -> bytes:
        if (
            kind != "authorization"
            or not limit.isascii()
            or not limit.isdecimal()
            or str(int(limit)) != limit
            or not 1 <= int(limit) <= 100
            or (cursor is not None and not 1 <= len(cursor) <= 255)
        ):
            raise StaffCaseError("invalid")
        resolved = await self.resolver.resolve(secret)
        if resolved.membership.audience != "staff":
            raise StaffCaseError("denied")
        principal = resolved.principal
        session_until = min(resolved.record.expires_at, resolved.membership.reviewed_until)
        witness = await self.witnesses.observe(principal, session_until)
        value, request_digest = await self.native.read(
            principal=principal,
            witness=witness,
            session_until=session_until,
            operation="list",
            query={"kind": kind, "limit": limit, "cursor": cursor},
        )
        observed = observation(value, request_digest=request_digest, limit=int(limit), now=self.lease.clock())
        if not isinstance(observed.projection, NativeStaffPage):
            raise StaffCaseError("unavailable")
        frozen = canonicalize(wire(observed.projection))
        public = wire(observed.projection)
        public["freshness"]["refresh_after_seconds"] = 10
        public_frozen = (
            StaffPage.model_validate_json(json.dumps(public)).model_dump_json(by_alias=True).encode("utf-8")
        )
        async with self.lease.acquire(secret, principal) as locked:
            locked.current(self.lease.clock())
            value, final_digest = await self.native.read(
                principal=principal,
                witness=witness,
                session_until=locked.valid_until,
                operation="finalize",
                query={
                    "continuity_ref": observed.continuity_ref,
                    "frozen_projection_digest": hashlib.sha256(frozen).hexdigest(),
                    "subordinate_continuities": [],
                },
            )
            final = observation(value, request_digest=final_digest, limit=int(limit), now=self.lease.clock())
            if (
                not isinstance(final.projection, NativeStaffPage)
                or final.continuity_ref != observed.continuity_ref
                or final.continuity_digest != observed.continuity_digest
                or final.projection != observed.projection
                or final.source_pins != observed.source_pins
                or final.observed_at != observed.observed_at
                or final.valid_until != observed.valid_until
            ):
                raise StaffCaseError("conflict")
            locked.current(self.lease.clock())
        if self.lease.clock() >= min(session_until, timestamp(observed.valid_until)):
            raise StaffCaseError("unavailable")
        return public_frozen

    async def _read(
        self,
        secret: str,
        *,
        case_ref: str | None = None,
        task_limit: str = "25",
        task_cursor: str | None = None,
    ) -> bytes:
        # Validate exact native grammar locally before any request; no arbitrary query.
        from pydantic import TypeAdapter

        try:
            ref = TypeAdapter(CaseRef).validate_python(case_ref, strict=True)
            if (
                not task_limit.isascii()
                or not task_limit.isdecimal()
                or str(int(task_limit)) != task_limit
                or not 1 <= int(task_limit) <= 100
            ):
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
        value, request_digest = await self.native.read(
            principal=principal,
            witness=witness,
            session_until=session_until,
            operation="detail",
            query={"case_ref": ref, "task_limit": task_limit, "task_cursor": None},
        )
        observed = observation(
            value, request_digest=request_digest, case_ref=ref, limit=int(task_limit), now=self.lease.clock()
        )
        frozen = canonicalize(wire(observed.projection))
        # Only the already validated fixed cadence crosses representations. Keep
        # the original native bytes/digest for finalization and freeze public
        # bytes before any final authority or session-release checks.
        public = wire(observed.projection)
        public["freshness"]["refresh_after_seconds"] = 10
        public_frozen = (
            StaffDetail.model_validate_json(json.dumps(public)).model_dump_json(by_alias=True).encode("utf-8")
        )
        # An identity transaction holds the real session and membership through the
        # native final check. It does not call the resolver or replace the witness.
        async with self.lease.acquire(secret, principal) as locked:
            locked.current(self.lease.clock())
            value, final_digest = await self.native.read(
                principal=principal,
                witness=witness,
                session_until=locked.valid_until,
                operation="finalize",
                query={
                    "continuity_ref": observed.continuity_ref,
                    "frozen_projection_digest": hashlib.sha256(frozen).hexdigest(),
                    "subordinate_continuities": [],
                },
            )
            final = observation(
                value,
                request_digest=final_digest,
                case_ref=ref,
                limit=int(task_limit),
                now=self.lease.clock(),
            )
            if (
                final.continuity_ref != observed.continuity_ref
                or final.continuity_digest != observed.continuity_digest
                or final.projection != observed.projection
                or final.source_pins != observed.source_pins
                or final.observed_at != observed.observed_at
                or final.valid_until != observed.valid_until
            ):
                raise StaffCaseError("conflict")
            locked.current(self.lease.clock())
        # Both native response cleanup and identity transaction release have completed.
        # Return the pre-frozen public bytes, with no reserialization or awaited work.
        if self.lease.clock() >= min(session_until, timestamp(observed.valid_until)):
            raise StaffCaseError("unavailable")
        return public_frozen
