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
    CibSevenError,
    CibSevenHttpTransport,
    CibSevenTransport,
    FakeCibSevenTransport,
    ProcessInstance,
    ProcessNotFoundError,
    ProcessStatus,
    start_process_idempotent,
)

__all__ = [
    "CibSevenServer",
    "CibSevenError",
    "CibSevenHttpTransport",
    "CibSevenTransport",
    "FakeCibSevenTransport",
    "ProcessInstance",
    "ProcessNotFoundError",
    "ProcessStatus",
    "start_process_idempotent",
]
