"""Agent-definition artifact validation (`<agents-root>/<id>/agent.yaml`).

Reuses `maezo.agents.AgentLoader` / `AgentDefinition` (T0.3) as the schema
authority instead of re-implementing a parallel schema here — the runtime and
this CI gate must agree on what a valid `agent.yaml` looks like.

Additionally enforces what several `agent.yaml` files already document as a
hard requirement (see e.g. `spec/agents/andre/agent.yaml`'s `tools:` comment:
"validate-artifacts exige o dir mcp_<server>"): every `mcp-<server>.<action>`
tool id in an agent's allowlist must reference an MCP server that actually
exists in the repo (`src/maezo/tools/mcp_<server>/`) — an agent declaring a
tool for a server that was never built is a silent contract violation the
old stub never caught.

The `_template` directory (the contract template itself, with placeholder
values) is intentionally skipped — it is not a real agent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from maezo.agents import AgentLoader

from .result import Report

TEMPLATE_DIR_NAME = "_template"


def known_mcp_servers(tools_root: Path) -> frozenset[str]:
    """MCP server names discoverable at `src/maezo/tools/mcp_<name>/`."""
    if not tools_root.exists():
        return frozenset()
    return frozenset(
        d.name[len("mcp_") :] for d in tools_root.iterdir() if d.is_dir() and d.name.startswith("mcp_")
    )


def _tool_server(tool: str) -> str | None:
    """`mcp-fhir.read_patient` -> `fhir`. None if `tool` isn't `mcp-<server>.<action>`."""
    if not tool.startswith("mcp-"):
        return None
    head = tool.split(".", 1)[0]  # mcp-fhir
    return head[len("mcp-") :] or None


def validate_file(path: Path, mcp_servers: frozenset[str], report: Report) -> None:
    """Validate one `agent.yaml` file, accumulating findings into `report`."""
    try:
        definition = AgentLoader().load(path)
    except FileNotFoundError as exc:
        report.error(path, str(exc))
        return
    except ValueError as exc:
        report.error(path, str(exc))
        return
    except yaml.YAMLError as exc:
        report.error(path, f"malformed YAML: {exc}")
        return

    if not definition.tools:
        report.error(path, "'tools' must be a non-empty allowlist")

    for tool in definition.tools:
        server = _tool_server(tool)
        if server is None:
            report.error(path, f"tool '{tool}' does not follow the mcp-<server>.<action> pattern")
            continue
        if server not in mcp_servers:
            report.error(
                path,
                f"tool '{tool}' references an unknown MCP server "
                f"(expected src/maezo/tools/mcp_{server}/ to exist)",
            )


def validate_dir(agents_root: Path, tools_root: Path, report: Report) -> None:
    """Validate every `<agents_root>/<id>/agent.yaml` (skips `_template/`)."""
    if not agents_root.exists() or not agents_root.is_dir():
        report.error(agents_root, "agent-definitions directory does not exist")
        return

    mcp_servers = known_mcp_servers(tools_root)
    agent_dirs = sorted(
        p
        for p in agents_root.iterdir()
        if p.is_dir() and p.name != TEMPLATE_DIR_NAME and (p / "agent.yaml").exists()
    )
    if not agent_dirs:
        report.error(agents_root, "no agent.yaml files found under any subdirectory")

    for agent_dir in agent_dirs:
        validate_file(agent_dir / "agent.yaml", mcp_servers, report)
