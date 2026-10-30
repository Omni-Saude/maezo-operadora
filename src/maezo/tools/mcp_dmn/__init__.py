"""MCP server `dmn` — catalog anchor for `mcp-dmn.evaluate` (ADR-0012, ADR-0028).

This package directory is what registers the `dmn` server in the closed MCP catalog
(`maezo.platform.validation.agent_def.known_mcp_servers` derives it from
`src/maezo/tools/mcp_<name>/`); the agents' `mcp-dmn.evaluate` tool declarations validate
against it.

There is NO DMN evaluator here. Runtime DMN evaluation is ENGINE-SIDE ONLY (ADR-0028): the
CIB Seven engine evaluates every decision table through
`maezo.tools.workers.dmn_transport.DmnTransport` (`CibSevenDmnTransport` in production,
`FakeDmnTransport` — fail-closed on unregistered keys — in tests). The former in-process XML
`DmnServer` (deprecated by T1.5, unable to evaluate FEEL comparison/range/list tests) finished
its deprecation cycle and was removed (R-097 / F-115); `tests/unit/tools/test_dmn_engine_side_only.py`
fences it from coming back.
"""
