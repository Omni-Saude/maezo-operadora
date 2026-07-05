"""MAEZO Agents Framework — Agent definition, loader, and registry.

Provides the foundational types and infrastructure for agent management:
- AgentDefinition: pydantic model for agent.yaml contracts
- AgentLoader: loads agent.yaml files into AgentDefinition instances
- AgentRegistry: registers and retrieves agents by id
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class AgentDefinition(BaseModel):
    """Pydantic model representing an agent contract (agent.yaml).

    Every agent MUST have a corresponding agent.yaml defining its identity,
    capabilities, and operational constraints. This model is the canonical
    in-memory representation used by the AgentRegistry, A2A dispatcher,
    and artifact validation (validate-artifacts CI gate).
    """

    id: str = Field(..., description="Unique agent identifier (e.g., 'helena', 'rafael')")
    name: str = Field(..., description="Human-readable agent name (e.g., 'Helena Moreira')")
    role: str = Field(..., description="Agent's role description")
    phase: int = Field(default=0, description="Phase/sprint number (0-3)")
    autonomy_level: str = Field(default="L3", description="Autonomy level L0-L3 (ADR-0008)")
    process_keys: list[str] = Field(
        default_factory=list, description="BPMN process keys this agent exercises"
    )
    tools: list[str] = Field(default_factory=list, description="MCP tool ids registered for this agent")

    # Optional extended fields (present in full spec agent.yaml files)
    reports_to: str | None = Field(default=None, description="Human manager / team responsible")
    security_zone: str = Field(default="general", description="Security zone: general, phi (ADR-0006)")
    graph: str = Field(default="graph.py:build", description="Module path for the build() function")
    prompt_versions: dict[str, str] = Field(default_factory=dict, description="Prompt version map (ADR-0009)")
    model: dict[str, Any] = Field(default_factory=dict, description="Model tier configuration")
    autonomy_policy: str | None = Field(default=None, description="Path to autonomy policy directory")
    autonomy_actions: list[str] = Field(
        default_factory=list, description="Autonomy actions this agent exercises"
    )
    kpis: list[dict[str, str]] = Field(default_factory=list, description="KPI targets for this agent")
    escalation: dict[str, Any] = Field(default_factory=dict, description="Escalation configuration")
    memory: dict[str, Any] = Field(default_factory=dict, description="Memory configuration (ADR-0002)")
    a2a: dict[str, Any] = Field(default_factory=dict, description="Agent-to-Agent configuration (ADR-0003)")


class AgentLoader:
    """Loads agent.yaml files into AgentDefinition pydantic models.

    Supports loading from file paths or from raw dicts (programmatic).

    Typical usage:
        loader = AgentLoader()
        definition = loader.load(Path("src/maezo/agents/helena/agent.yaml"))
    """

    def load(self, path: Path) -> AgentDefinition:
        """Load an agent.yaml file from disk and return an AgentDefinition.

        Args:
            path: Path to the agent.yaml file.

        Returns:
            An AgentDefinition instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the YAML is invalid or required fields are missing.
        """
        if not path.exists():
            raise FileNotFoundError(f"Agent definition file not found: {path}")

        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f)

        if data is None:
            raise ValueError(f"Agent definition file is empty: {path}")

        return self.load_from_dict(data, source_path=path)

    def load_from_dict(self, data: dict[str, Any], source_path: Path | None = None) -> AgentDefinition:
        """Parse a dict (from YAML or programmatic) into an AgentDefinition.

        Args:
            data: Dict with agent definition fields.
            source_path: Optional source path for logging.

        Returns:
            An AgentDefinition instance.

        Raises:
            ValueError: If required fields are missing.
        """
        agent_id = data.get("id")
        logger.info("agent_loader_parsing", agent_id=agent_id)

        # Validate required non-empty string fields
        for field_name in ("id", "name", "role"):
            value = data.get(field_name, "")
            if not value or not str(value).strip():
                raise ValueError(
                    f"Agent definition missing required non-empty field '{field_name}'"
                    + (f" in {source_path}" if source_path else "")
                )

        # The pydantic model will validate required fields
        definition = AgentDefinition(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            phase=data.get("phase", 0),
            autonomy_level=data.get("autonomy_level", "L3"),
            process_keys=data.get("process_keys", []),
            tools=data.get("tools", []),
            reports_to=data.get("reports_to"),
            security_zone=data.get("security_zone", "general"),
            graph=data.get("graph", "graph.py:build"),
            prompt_versions=data.get("prompt_versions", {}),
            model=data.get("model", {}),
            autonomy_policy=data.get("autonomy_policy"),
            autonomy_actions=data.get("autonomy_actions", []),
            kpis=data.get("kpis", []),
            escalation=data.get("escalation", {}),
            memory=data.get("memory", {}),
            a2a=data.get("a2a", {}),
        )

        logger.info("agent_loader_parsed", agent_id=definition.id, name=definition.name)
        return definition


class AgentRegistry:
    """In-memory registry of AgentDefinitions, keyed by agent id.

    Provides lookup, listing, and duplicate-detection for agent definitions.
    Used by the A2A dispatcher, artifact validation, and the agent runtime.

    Typical usage:
        registry = AgentRegistry()
        registry.register(definition)
        agent = registry.get("helena")
        all_ids = registry.list_ids()
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        logger.info("agent_registry_initialized")

    def register(self, definition: AgentDefinition) -> None:
        """Register an agent definition.

        Args:
            definition: The AgentDefinition to register.

        Raises:
            ValueError: If an agent with the same id is already registered.
        """
        agent_id = definition.id
        if agent_id in self._agents:
            raise ValueError(f"Agent '{agent_id}' is already registered")
        self._agents[agent_id] = definition
        logger.info("agent_registered", agent_id=agent_id, name=definition.name)

    def get(self, agent_id: str) -> AgentDefinition | None:
        """Retrieve an agent definition by id.

        Args:
            agent_id: The agent identifier.

        Returns:
            The AgentDefinition if found, None otherwise.
        """
        return self._agents.get(agent_id)

    def list_ids(self) -> list[str]:
        """Return all registered agent ids.

        Returns:
            A list of agent id strings.
        """
        return list(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents


__all__ = ["AgentDefinition", "AgentLoader", "AgentRegistry"]
