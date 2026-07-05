"""MCP CIB Seven — in-process MCP server para governança de processos.

ADR-0001: CIB Seven como backbone de governança
ADR-0022: MCP servers registram tools IN-PROCESS no boot
"""

from maezo.tools.mcp_cibseven.server import CibSevenMcpServer

__all__ = ["CibSevenMcpServer"]
