"""MAEZO Agents Framework — Agent definition, loader, and registry.

Provides the foundational types and infrastructure for agent management:
- AgentDefinition: pydantic model for agent.yaml contracts
- AgentLoader: loads agent.yaml files into AgentDefinition instances
- AgentRegistry: registers and retrieves agents by id

T0.3 / defect B14 (single source of truth): `agent.yaml` lives ONLY under
`spec/agents/<id>/agent.yaml` — the copies formerly duplicated under
`src/maezo/agents/<id>/agent.yaml` have been deleted. `resolve_spec_dir()` /
`resolve_spec_agents_dir()` resolve that directory, honoring the
`MAEZO_SPEC_DIR` environment variable override, and FAIL CLOSED (raise) if
the resolved directory does not exist — there is no silent fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

#: Environment variable that overrides the resolved `spec/` directory.
#: Deployment environments that lay `spec/` out at a location none of the
#: default candidates cover (e.g. a container image mounting `spec/` at an
#: arbitrary path) MUST set this — see docs/reports/T0.3-agent-yaml-drift.md
#: for the packaging note.
MAEZO_SPEC_DIR_ENV = "MAEZO_SPEC_DIR"


def _default_spec_dir_candidates() -> tuple[Path, ...]:
    """Compute the ordered default `spec/` directory candidates.

    Resolution order (T1.8 REVISE-1 — each candidate is validated with
    ``is_dir()`` by the caller before use; there is no silent acceptance of a
    nonexistent path):

    1. **Repo checkout** (`<repo_root>/spec`): this file lives at
       `<repo_root>/src/maezo/agents/__init__.py`, so `<repo_root>` is three
       parents up. The dev/CI case.
    2. **Installed wheel** (`<site-packages>/maezo/spec`): the wheel's hatch
       ``force-include`` (pyproject.toml, ADR-0025 D2 Rev 1) lays
       `spec/policies/autonomy/*.yaml` under `maezo/spec/`, i.e. one parent up
       from this file's package directory. Without this candidate the shipped
       policy files would be inert and the fail-closed PEP factory would
       refuse to start on every packaged deployment.

    A path too shallow for a candidate (defensive; not the case in any real
    layout) simply omits that candidate rather than raising ``IndexError``.
    """
    parents = Path(__file__).resolve().parents
    candidates: list[Path] = []
    if len(parents) > 3:
        candidates.append(parents[3] / "spec")  # repo checkout: <repo_root>/spec
    if len(parents) > 1:
        candidates.append(parents[1] / "spec")  # installed wheel: <site-packages>/maezo/spec
    return tuple(candidates)


def resolve_spec_dir() -> Path:
    """Resolve the `spec/` directory root, honoring `MAEZO_SPEC_DIR`.

    Resolution order:

    1. ``MAEZO_SPEC_DIR`` env var — authoritative when set: if it points at a
       nonexistent directory this raises immediately (no fallback to any
       default; an explicit-but-wrong override is a configuration error).
    2. The default candidates from :func:`_default_spec_dir_candidates`
       (repo checkout, then installed-wheel package-adjacent) — the first one
       that exists as a directory wins.

    Fail-closed: raises `FileNotFoundError` if nothing resolves. Never falls
    back to an empty listing silently — an unresolvable spec directory is a
    configuration error, not a warn-and-continue condition.

    Returns:
        The resolved, absolute `spec/` directory path.

    Raises:
        FileNotFoundError: If the env override points at a missing directory,
            or no default candidate exists.
    """
    override = os.environ.get(MAEZO_SPEC_DIR_ENV)
    if override:
        spec_dir = Path(override).expanduser().resolve()
        if not spec_dir.is_dir():
            raise FileNotFoundError(
                f"spec/ directory not found at {spec_dir} "
                f"(resolved from env var {MAEZO_SPEC_DIR_ENV}). "
                f"Set the {MAEZO_SPEC_DIR_ENV} environment variable to the directory containing "
                f"spec/agents/, spec/policies/, spec/processes/."
            )
        return spec_dir

    candidates = _default_spec_dir_candidates()
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "spec/ directory not found at any default candidate: "
        f"{', '.join(str(c) for c in candidates)} (package-relative defaults). "
        f"Set the {MAEZO_SPEC_DIR_ENV} environment variable to the directory containing "
        f"spec/agents/, spec/policies/, spec/processes/."
    )


def resolve_spec_agents_dir() -> Path:
    """Resolve the `spec/agents/` directory (single source of truth, T0.3/B14).

    Fail-closed: raises `FileNotFoundError` if `spec/agents/` does not exist
    under the resolved spec directory.

    Returns:
        The resolved, absolute `spec/agents/` directory path.

    Raises:
        FileNotFoundError: If `spec/agents/` does not exist.
    """
    agents_dir = resolve_spec_dir() / "agents"
    if not agents_dir.is_dir():
        raise FileNotFoundError(f"spec/agents/ directory not found at {agents_dir}")
    return agents_dir


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

    Supports loading from file paths, from raw dicts (programmatic), or by
    agent id from the canonical `spec/agents/` source of truth (T0.3/B14).

    Typical usage:
        loader = AgentLoader()
        definition = loader.load(Path("spec/agents/helena/agent.yaml"))

        # or, resolving spec/agents/ automatically (honors MAEZO_SPEC_DIR):
        definition = loader.load_by_id("helena")
    """

    def load_by_id(self, agent_id: str, spec_agents_dir: Path | None = None) -> AgentDefinition:
        """Load an agent's definition by id from `spec/agents/<agent_id>/agent.yaml`.

        Fail-closed: this delegates to `load()`, which raises if the file is
        missing or unparseable — there is no silent fallback to a default
        definition.

        Args:
            agent_id: The agent identifier (matches the directory name under
                `spec/agents/`, e.g. "helena", "rafael").
            spec_agents_dir: Optional override for the `spec/agents/`
                directory. Defaults to `resolve_spec_agents_dir()`.

        Returns:
            An AgentDefinition instance.

        Raises:
            FileNotFoundError: If `spec/agents/` (or the override) cannot be
                resolved, or the agent's `agent.yaml` does not exist.
            ValueError: If the YAML is invalid or required fields are missing.
        """
        base_dir = spec_agents_dir if spec_agents_dir is not None else resolve_spec_agents_dir()
        return self.load(base_dir / agent_id / "agent.yaml")

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


__all__ = [
    "MAEZO_SPEC_DIR_ENV",
    "AgentDefinition",
    "AgentLoader",
    "AgentRegistry",
    "resolve_spec_agents_dir",
    "resolve_spec_dir",
]
