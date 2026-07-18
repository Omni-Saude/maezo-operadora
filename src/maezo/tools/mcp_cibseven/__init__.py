"""MCP CIB Seven — in-process MCP server for Camunda/CIB Seven process engine.

Exposes 3 tools:
- start_process(process_key, variables) -> process_instance_id
- get_task(task_id) -> task_dict
- complete_task(task_id, variables) -> None

Uses httpx.AsyncClient for REST calls. Configuration via settings (CIBSEVEN_REST_URL).

Implements ADR-0022 (MCP in-process boot).
"""

from maezo.tools.mcp_cibseven.server import CibSevenServer
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenHttpTransport,
    CibSevenTransport,
    CibSevenVariableDecodeError,
    FakeCibSevenTransport,
    ProcessInstance,
    ProcessNotFoundError,
    ProcessStatus,
    build_start_audit_record,
    start_dedup_key,
    start_process_idempotent,
)

__all__ = [
    "AgentDecisionProvenance",
    "AuditStartSink",
    "CibSevenServer",
    "CibSevenError",
    "CibSevenHttpTransport",
    "CibSevenTransport",
    "CibSevenVariableDecodeError",
    "FakeCibSevenTransport",
    "ProcessInstance",
    "ProcessNotFoundError",
    "ProcessStatus",
    "build_start_audit_record",
    "start_dedup_key",
    "start_process_idempotent",
]
