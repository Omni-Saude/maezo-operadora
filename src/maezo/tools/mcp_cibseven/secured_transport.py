"""Exact-process D7-C adapter for the existing audited/idempotent start entrypoint.

The adapter borrows the client's local lifetime. Bare source pointers are refused until
the separately reviewed authenticated/native acquisition adapters are implemented.
Read status has no registered variable projection in B v1 and is explicitly unavailable.
"""

from __future__ import annotations

from typing import Any

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineOperation,
    EngineRefusalCode,
    EngineRequest,
    canonical_json,
)
from maezo.gateway.engine_transport import EngineOperationsClient
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenStartAuthorizationError,
    ProcessInstance,
    ProcessNotFoundError,
    ProcessStatus,
    historic_instance_is_live,
    is_strict_start_dedup,
)


class SecuredCibSevenTransport:
    """No native transport inheritance, credentials or raw-route fallback."""

    def __init__(self, client: EngineOperationsClient, *, process_key: str, source_ref: str = "") -> None:
        if type(source_ref) is not str or source_ref:
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        self._client = client.borrow(process_key)
        self._process_key = process_key

    def _request(
        self,
        operation: EngineOperation,
        business_key: str,
        variables: dict[str, Any],
        *,
        message: str = "",
        correlation: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> EngineRequest:
        return EngineRequest(
            operation,
            self._process_key,
            business_key,
            canonical_json(variables),
            correlation_json=canonical_json(correlation if correlation is not None else {}),
            all_matching=all_matching,
            message=message,
        )

    def _source(self, request: EngineRequest) -> str:
        self._client.profile(request.operation, request.process_key, request.message)
        return ""

    async def authorize_start(
        self,
        *,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
        provenance: AgentDecisionProvenance,
    ) -> bytes:
        if process_key != self._process_key:
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)
        request = self._request(EngineOperation.START, business_key, variables)
        profile, _ = self._client.project(request, source_ref=self._source(request))
        if (
            provenance.tenant_id != profile.identity.tenant
            or provenance.agent_id != (profile.schema.audit_actor or profile.identity.workload)
            or provenance.agent_version != profile.identity.workload_version
        ):
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
        operations = [EngineOperation.READ_ACTIVE]
        if is_strict_start_dedup(process_key):
            operations.append(EngineOperation.READ_HISTORY)
        for operation in operations:
            read = self._request(operation, business_key, {})
            self._client.project(read, source_ref=self._source(read))
        await self._client.readiness()
        return request.variables_json

    async def _execute(self, request: EngineRequest) -> Any:
        try:
            return await self._client.execute(request, source_ref=self._source(request))
        except EngineCapabilityError as exc:
            # Preserve the existing CibSevenError retry/claim-orphan handling without leaking
            # provider bodies or turning denied/inconclusive reads into absence.
            raise CibSevenStartAuthorizationError(exc.code) from None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        if process_key != self._process_key:
            raise CibSevenStartAuthorizationError(EngineRefusalCode.RESOURCE_MISMATCH)
        row = await self._execute(self._request(EngineOperation.START, business_key, variables))
        return ProcessInstance(row["id"], process_key, business_key, "ACTIVE")

    async def _find(self, operation: EngineOperation, business_key: str) -> ProcessInstance | None:
        rows = await self._execute(self._request(operation, business_key, {}))
        # All rows were validated before choosing one. A live generation always outranks a
        # finished one (DL-0046); the complete bounded response is never truncated locally.
        instances = [
            ProcessInstance(row["id"], self._process_key, business_key, row["state"], already_existed=True)
            for row in rows
        ]
        return next(
            (row for row in instances if historic_instance_is_live(row.state)),
            instances[0] if instances else None,
        )

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return await self._find(EngineOperation.READ_ACTIVE, business_key)

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        if process_key and process_key != self._process_key:
            raise CibSevenStartAuthorizationError(EngineRefusalCode.RESOURCE_MISMATCH)
        return await self._find(EngineOperation.READ_HISTORY, business_key)

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        result = await self._execute(
            self._request(
                EngineOperation.CORRELATE,
                business_key,
                variables,
                message=message_name,
                correlation=correlation_keys,
                all_matching=all_matching,
            )
        )
        if result["correlated"] == 0:
            raise ProcessNotFoundError("engine_correlation_not_found")

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        # Existing status consumers expect process variables. Returning {} here would silently
        # change their behavior; B has no registered READ_STATUS profile in this delivery.
        raise CibSevenStartAuthorizationError(EngineRefusalCode.OPERATION_DENIED)

    async def close(self) -> None:
        await self._client.close()
