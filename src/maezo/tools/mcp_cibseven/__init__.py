"""MCP CIB Seven — agent-directed transport for the Camunda/CIB Seven process engine.

Exposes the transport primitives an agent graph uses to direct BPMN process instances
(ADR-0001, T1.11):
- start_process_idempotent(...) -> ProcessInstance (the SOLE, ADR-0007-audited process-start
  chokepoint — idempotent by business key, requires audit_sink + provenance)
- CibSevenHttpTransport.correlate_message(...) / .get_process_status(...)

Uses httpx.AsyncClient for REST calls. Configuration is injected by each runtime's own
Settings (CIBSEVEN_BASE_URL), not by this package.

T2.4 co-requisite note: the earlier `CibSevenServer` tool-registration wrapper (a raw,
un-audited `start_process` with no idempotency/businessKey, meant to plug into a
`ToolRegistry` per ADR-0022) was removed — grep-confirmed dead in `src/**` (no
`ToolRegistry`/`tool_wiring.py`/`build_tool_invoker` exists anywhere in this v2 tree; ADR-0022's
claim that `register_tools` is load-bearing describes that unbuilt mechanism, not v2's actual
`src/`). Left wired, it would become a LIVE un-audited process-start the moment a future tool
registry called it directly instead of `start_process_idempotent` below. When T2.4 builds a
real ToolRegistry, any CIB Seven start tool it exposes MUST wrap `start_process_idempotent`
(never resurrect a raw, un-fenced call) — see `tests/unit/tools/test_mcp_cibseven.py`'s
structural guard.
"""

from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenHttpTransport,
    CibSevenTransport,
    CibSevenVariableDecodeError,
    DedupReportingAuditSink,
    FakeCibSevenTransport,
    HistoricProcessVariables,
    HistoricVariableReadingTransport,
    HistoryQueryingTransport,
    ProcessInstance,
    ProcessNotFoundError,
    ProcessStatus,
    StartClaimWithoutInstanceError,
    StartDedupGateUnavailableError,
    StartDedupPosture,
    StartOutcome,
    build_start_audit_record,
    is_strict_start_dedup,
    start_dedup_key,
    start_dedup_posture,
    start_process_idempotent,
)

__all__ = [
    "AgentDecisionProvenance",
    "AuditStartSink",
    "CibSevenError",
    "CibSevenHttpTransport",
    "CibSevenTransport",
    "CibSevenVariableDecodeError",
    "DedupReportingAuditSink",
    "FakeCibSevenTransport",
    "HistoricProcessVariables",
    "HistoricVariableReadingTransport",
    "HistoryQueryingTransport",
    "ProcessInstance",
    "ProcessNotFoundError",
    "ProcessStatus",
    "StartClaimWithoutInstanceError",
    "StartDedupGateUnavailableError",
    "StartDedupPosture",
    "StartOutcome",
    "build_start_audit_record",
    "is_strict_start_dedup",
    "start_dedup_key",
    "start_dedup_posture",
    "start_process_idempotent",
]
