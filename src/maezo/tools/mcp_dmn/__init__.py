"""MCP DMN — in-process MCP server for DMN decision evaluation (ADR-0012).

Exposes 1 tool:
- evaluate_decision(dmn_key, inputs) -> outputs

Parses DMN decision table XML files from spec/processes/dmn/.
Implements ADR-0012 (DMN as deterministic tool) and ADR-0022 (MCP in-process boot).
"""

from maezo.tools.mcp_dmn.server import DmnServer

__all__ = ["DmnServer"]
