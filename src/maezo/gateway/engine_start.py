"""Pre-claim D7-A port in the existing typed gateway, not an authenticated HTTP transport.

Only the preflight is implemented. B/C must bind exact definition ids and current authenticated
read/write capabilities in their transport. Legacy forwarding below is intentionally preserved;
this adapter must not be used to claim D7 production readiness or server enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineOperation,
    EngineOperationAuthorizer,
    EngineRefusalCode,
    EngineRequest,
    canonical_json,
)
from maezo.gateway.seams.cibseven import (
    GatedCibSevenTransport,
    GatedHistoryQueryingCibSevenTransport,
)

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance


class EngineStartAuthorizer(Protocol):
    async def authorize_start(
        self,
        *,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
        provenance: AgentDecisionProvenance,
    ) -> bytes: ...


class ProfileStartAuthorizer:
    """Authorize the start and its dedup read capabilities before durable intent/claim.

    This consumes deployment-bound identity, not an agent argument. The audit actor must match
    it too. No source can attest a field absent from the reviewed schema.
    """

    def __init__(self, operations: EngineOperationAuthorizer) -> None:
        self._operations = operations

    async def authorize_start(
        self,
        *,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
        provenance: AgentDecisionProvenance,
    ) -> bytes:
        from maezo.tools.mcp_cibseven.transport import is_strict_start_dedup

        request = EngineRequest(EngineOperation.START, process_key, business_key, canonical_json(variables))
        await self._operations.authorize_start_identity(
            request,
            tenant=provenance.tenant_id,
            actor=provenance.agent_id,
            actor_version=provenance.agent_version,
        )
        operations = [EngineOperation.READ_ACTIVE]
        if is_strict_start_dedup(process_key):
            operations.append(EngineOperation.READ_HISTORY)
        for operation in operations:
            await self._operations.authorize(EngineRequest(operation, process_key, business_key, b"{}"))
        # Return the exact immutable start snapshot, never re-read caller-owned variables after
        # any authority await. The helper detaches its audit/effect payload from these bytes.
        return request.variables_json


class PreflightCibSevenTransport(GatedCibSevenTransport):
    """Adds the pre-claim protocol without pretending to secure legacy HTTP/read methods."""

    __slots__ = ("_start_authorizer",)

    def __init__(self, inner: Any, *, seam: Any, authorizer: EngineStartAuthorizer | None) -> None:
        super().__init__(inner, seam=seam)
        self._start_authorizer = authorizer

    async def authorize_process_start(
        self,
        *,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
        provenance: AgentDecisionProvenance,
    ) -> bytes:
        if self._start_authorizer is None:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        return await self._start_authorizer.authorize_start(
            process_key=process_key,
            business_key=business_key,
            variables=variables,
            provenance=provenance,
        )


class PreflightHistoryCibSevenTransport(PreflightCibSevenTransport, GatedHistoryQueryingCibSevenTransport):
    """History support remains conditional on the real inner capability (DL-0046)."""

    __slots__ = ()
