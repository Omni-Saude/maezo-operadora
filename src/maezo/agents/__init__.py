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

WAVE-1 Q-6 — `MAEZO_SPEC_DIR` FAILS CLOSED IN PRODUCTION (owner ratification, 2026-08-12)
-----------------------------------------------------------------------------------------
`resolve_spec_dir()` is the ONE mechanism every policy loader in this repo routes through: the
autonomy matrix (`gateway/pep.py`), the MZO-040 approval manifest
(`gateway/action_execution.py`), the L-0 ceilings (`tools/workers/ceilings.py`), the DMN
ratification records, the TISS schema pin, the PHI business-key policy and every `agent.yaml`.
So `MAEZO_SPEC_DIR` does not override *a file* — it substitutes the ENTIRE POLICY PLANE in one
environment variable, and it does so INVISIBLY to the `MAEZO_ACTION_APPROVALS_PATH` override
fence (adversary A-6, `docs/design/wave1-effect-chokepoint.md` §5.7 / C-B4).

The owner ratified the STRONG form of the design's two options on 2026-08-12 (PLANS §0.8,
"Ratificação Q-6"): *"produção deve recusar `MAEZO_SPEC_DIR`, permitindo-o apenas em runtime
local explícito; remova o companion bypass e aceite futuras exceções somente por bundle imutável
com digest permitido."* Therefore:

* **Production (the DEFAULT, including an ABSENT runtime mode)** — the variable is REFUSED:
  `resolve_spec_dir()` raises :class:`SpecDirOverrideRefusedError`. Absent means production
  because of the ADR-0039 Q7 / Train-F fail-closed default (`agent_runtime/settings.py`,
  `a2a_composition.worker_runtime_mode_from_env`): a deployment that forgets to declare its mode
  is never granted the permissive branch.
* **Explicitly local runtime** — honored exactly as before, so every dev box and every test run
  that declares `RUNTIME_MODE=local` (or `AGENT_RUNTIME_MODE=local`) keeps working.
* **Operator tools** that legitimately need a different tree pass it as an EXPLICIT flag, never
  the ambient variable — `maezo.platform.deploy.cli --spec-dir` is the shipped example, and it
  reaches `engine_deploy.resolve_spec_processes_dir(spec_dir=…)` without touching this function.

THE ONLY SANCTIONED FUTURE EXCEPTION, AND IT IS **NOT BUILT** (design note, owner's words): an
IMMUTABLE bundle whose content DIGEST is on an allowlist. Nothing here reads such an allowlist and
nothing should be added until that mechanism is designed and ratified on its own review. There is
deliberately **no environment escape hatch** — the companion flag that used to soften this pin
(`MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT`, which lifted the WEAK evaluate-only form in
`action_execution._parse`) was REMOVED by the same ratification. A second variable that re-opens a
one-variable total policy substitution is the bypass, not the control.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

#: Environment variable that overrides the resolved `spec/` directory.
#: Deployment environments that lay `spec/` out at a location none of the
#: default candidates cover (e.g. a container image mounting `spec/` at an
#: arbitrary path) MUST set this — see docs/reports/T0.3-agent-yaml-drift.md
#: for the packaging note. REFUSED in production since the Q-6 ratification
#: (module docstring): a production deployment lays `spec/` out where the
#: default candidates already look, or it does not deploy.
MAEZO_SPEC_DIR_ENV = "MAEZO_SPEC_DIR"

#: BOTH spellings the deployment vocabulary uses for the runtime mode, in `key_scrubber.py:117`'s
#: order. THE SINGLE DEFINITION — `gateway/action_execution.py` re-exports these two names rather
#: than restating them, because the Q-6 refusal below and the gateway's provenance line must never
#: disagree about which variables name the runtime mode.
#:
#: LAYERING: this module cannot import `maezo.runtime.agent_runtime.a2a_composition`'s
#: `is_production_runtime_mode` (that package imports THIS one — a cycle), so the discriminator is
#: restated here, for the same reason `action_execution` restated it. The two are pinned to the
#: same verdict by a test that imports both.
RUNTIME_MODE_ENVS: Final[tuple[str, str]] = ("RUNTIME_MODE", "AGENT_RUNTIME_MODE")

#: The ONLY non-production runtime mode, and it must be declared EXPLICITLY. Matches
#: `a2a_composition._LOCAL_RUNTIME_MODE`, `agent_runtime/service.py:86` and
#: `webhooks/service.py:69` — one literal, four surfaces.
LOCAL_RUNTIME_MODE: Final[str] = "local"


class SpecDirOverrideRefusedError(RuntimeError):
    """`MAEZO_SPEC_DIR` was set outside an EXPLICITLY local runtime (Wave-1 Q-6, fail-closed).

    A distinct, NAMED type — not `FileNotFoundError`, not a bare `RuntimeError` — because this is
    a governance refusal, not a configuration-lookup miss: the directory may well exist and be
    readable, and refusing it anyway is the whole point. Callers that deliberately soften
    unresolvable-spec failures into an empty, approve-nothing result (`load_action_approvals`)
    catch the lookup failures and let THIS one propagate.
    """


def is_explicit_local_runtime() -> bool:
    """True iff the runtime mode is DECLARED and every declaration says exactly ``local``.

    THE FAIL-CLOSED SHAPE, and the one thing that differs from the (now removed) weak pin's
    ``_is_production_runtime``: **nothing declared is PRODUCTION**, not local. The weak pin could
    afford ``unset => local`` because its consequence was withholding enforcement on a manifest
    that was already globally `shadow`; the consequence here is refusing to boot, and a production
    pod that forgets to inject a mode variable must land on the SAFE side of that. This now agrees
    with both real resolvers — `worker_runtime_mode_from_env()` (absent/blank -> "production") and
    `AgentRuntimeSettings.agent_runtime_mode` (absent -> "production", ADR-0039 Q7 / Train F).

    A DISJUNCTION, not first-set-wins: with ``RUNTIME_MODE=local`` AND
    ``AGENT_RUNTIME_MODE=kubernetes`` — reachable for a pod that inherits a base-image default and
    is then labelled by Helm — first-set-wins would read ``local`` and DISARM the refusal. So a
    single non-local declaration is enough to make this False; ALL declared values must be
    ``local`` for the override to be honored.

    NO NORMALISATION, deliberately (the `modo`/`enforcement` pin idiom, and the same choice
    `is_production_runtime_mode` makes): ``" Local "`` and ``"LOCAL"`` are NOT ``local``, so a
    mistyped mode is production and the override is refused. Guessing which mode an operator meant
    is how a fail-closed pin becomes fail-open. An EMPTY string is treated as UNDECLARED (matching
    `key_scrubber`'s `or` chain and `worker_runtime_mode_from_env`'s strip-to-default), so
    ``RUNTIME_MODE=""`` is production.
    """
    declared = [value for name in RUNTIME_MODE_ENVS if (value := os.environ.get(name))]
    return bool(declared) and all(value == LOCAL_RUNTIME_MODE for value in declared)


def _refuse_spec_dir_override_outside_local(override: str) -> None:
    """Raise :class:`SpecDirOverrideRefusedError` unless the runtime is explicitly local (Q-6).

    Error style mirrors `_require_envelope_signing_or_fail_closed` (ADR-0039 §4.4): name the
    variable, name the mode that was resolved and where it came from, state the rationale with its
    citation, and name the SANCTIONED alternative — so the operator reading this at 3am can act on
    it without opening the design doc.
    """
    if is_explicit_local_runtime():
        return
    declared = {name: os.environ.get(name) for name in RUNTIME_MODE_ENVS}
    raise SpecDirOverrideRefusedError(
        f"refusing to resolve the policy plane through {MAEZO_SPEC_DIR_ENV}: the runtime mode is "
        f"PRODUCTION (declared={declared!r}; an ABSENT or blank mode is PRODUCTION — the "
        f"ADR-0039 Q7 / Train-F fail-closed default), and "
        f"{MAEZO_SPEC_DIR_ENV}={override!r} substitutes the ENTIRE policy plane — "
        "action-approvals.yaml, L0-core.yaml, _hard_frozen.yaml and every agent.yaml — in ONE "
        "environment variable, invisibly to the MAEZO_ACTION_APPROVALS_PATH override fence "
        "(adversary A-6, docs/design/wave1-effect-chokepoint.md §5.7/C-B4). Ratified fail-closed "
        "by the owner on 2026-08-12 (Wave-1 Q-6, PLANS.md §0.8): production must REFUSE this "
        "variable. SANCTIONED ALTERNATIVES: (a) production ships the policy plane INSIDE the "
        "artifact — the repo checkout or the wheel's package-adjacent maezo/spec/, which is what "
        "the default resolution already reads; (b) an operator TOOL passes the directory as an "
        "EXPLICIT command-line flag (`python -m maezo.platform.deploy.cli --spec-dir <path>`), "
        "never the ambient variable; (c) local development declares the runtime EXPLICITLY local "
        f"({' or '.join(RUNTIME_MODE_ENVS)}={LOCAL_RUNTIME_MODE}). There is NO environment escape "
        "hatch and none may be added: the ONLY sanctioned future exception is an IMMUTABLE bundle "
        "with an allowlisted content digest, and that mechanism is NOT BUILT."
    )


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

    0. **The Q-6 refusal (see the module docstring).** If ``MAEZO_SPEC_DIR`` is
       set and the runtime is NOT explicitly local, this raises
       :class:`SpecDirOverrideRefusedError` — before the directory is even
       looked at, because the refusal is about the variable being SET, not
       about where it points. Placing it HERE rather than at each composition
       root is deliberate: this is the single T0.3 chokepoint every policy
       loader already routes through, so the closure is by CONSTRUCTION and a
       future production entrypoint cannot forget to opt in.
    1. ``MAEZO_SPEC_DIR`` env var — authoritative when set (explicit local
       runtime only): if it points at a nonexistent directory this raises
       immediately (no fallback to any default; an explicit-but-wrong override
       is a configuration error).
    2. The default candidates from :func:`_default_spec_dir_candidates`
       (repo checkout, then installed-wheel package-adjacent) — the first one
       that exists as a directory wins.

    Fail-closed: raises `FileNotFoundError` if nothing resolves. Never falls
    back to an empty listing silently — an unresolvable spec directory is a
    configuration error, not a warn-and-continue condition.

    Returns:
        The resolved, absolute `spec/` directory path.

    Raises:
        SpecDirOverrideRefusedError: If ``MAEZO_SPEC_DIR`` is set outside an
            explicitly local runtime (Wave-1 Q-6, ratified 2026-08-12).
        FileNotFoundError: If the env override points at a missing directory,
            or no default candidate exists.
    """
    override = os.environ.get(MAEZO_SPEC_DIR_ENV)
    if override:
        _refuse_spec_dir_override_outside_local(override)
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
        SpecDirOverrideRefusedError: Propagated from :func:`resolve_spec_dir`
            when ``MAEZO_SPEC_DIR`` is set outside an explicitly local runtime
            (Wave-1 Q-6) — an `agent.yaml` is a policy artefact too.
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
    # ^ CONSUMED since AF-12 — see `model_tiers()` below. Until then this field was parsed
    #   (`AgentLoader._parse`) and read by nothing: `spec/agents/*/agent.yaml` has declared
    #   `task_default: {tier: fast}` / `reasoning: {tier: frontier}` for all 10 agents plus the
    #   `_template` since Phase 0, ADR-0009 §2/§3 mandate the routing, and no code path ever
    #   looked at it. Dead config that LOOKS live is worse than absent config, which is why this
    #   is either consumed or deleted — it is now consumed.
    autonomy_policy: str | None = Field(default=None, description="Path to autonomy policy directory")
    autonomy_actions: list[str] = Field(
        default_factory=list, description="Autonomy actions this agent exercises"
    )
    kpis: list[dict[str, str]] = Field(default_factory=list, description="KPI targets for this agent")
    escalation: dict[str, Any] = Field(default_factory=dict, description="Escalation configuration")
    memory: dict[str, Any] = Field(default_factory=dict, description="Memory configuration (ADR-0002)")
    a2a: dict[str, Any] = Field(default_factory=dict, description="Agent-to-Agent configuration (ADR-0003)")
    ingress: dict[str, Any] = Field(
        default_factory=dict, description="HTTP ingress channel declared by this agent (NEW-02)"
    )
    # ^ SPEC-FIRST half of the agent-scoped ingress invariant. `runtime/agent_runtime/ingress.py`
    #   mounts a route whose body is validated by ONE agent's typed state constructor; until
    #   05/09/2026 `service.py` mounted it under `MAEZO_AGENT_INGRESS_ENABLED` alone, so any
    #   agent_id with the flag on got a route carrying a FOREIGN contract (NEW-02, reproduced
    #   live: `agent_id="helena"` answered 200 and fed a Rafael-shaped state to Helena's harness).
    #   Declared HERE and mirrored by `ingress.py::INGRESS_BY_AGENT`; a parity fence over all 11
    #   `agent.yaml` (`tests/unit/runtime/test_agent_ingress_agent_scoped.py`) refuses either half
    #   alone. PARSED, not merely accepted: without this field pydantic's default `extra="ignore"`
    #   would DROP the block, and the yaml would look declarative while computing nothing — the
    #   same dead-config-that-looks-live failure the `model:` field's own comment describes.

    def model_tiers(self) -> dict[str, str]:
        """The declared `task_kind -> tier` map from this agent's `model:` block (AF-12, ADR-0009).

        Reads `agent.yaml`'s shipped shape and nothing else::

            model:
              task_default: { tier: fast }
              reasoning:    { tier: frontier }

        returns ``{"task_default": "fast", "reasoning": "frontier"}``.

        SCOPE, DELIBERATELY NARROW: this reports what the SPEC declares. It does not validate the
        tier vocabulary and does not decide which model a tier means — both belong to
        :class:`maezo.runtime.inference.InferenceProvider`, which is where a bad tier has to fail
        closed (at provider construction, isolated by each composition root into a red readiness
        check). Splitting it this way keeps `maezo.agents` free of a dependency on the runtime
        package, and keeps ONE place that can refuse a tier.

        A malformed entry (a non-mapping, or a mapping with no string `tier`) is SKIPPED here and
        surfaces as an UNDECLARED task_kind downstream, which the provider counts explicitly on
        `maezo_llm_tier_resolution_total`. Raising here would turn a cosmetic YAML slip into a
        failure to load the agent at all, and the loader is the one thing that must keep working.
        """
        tiers: dict[str, str] = {}
        for task_kind, block in self.model.items():
            if not isinstance(task_kind, str) or not isinstance(block, dict):
                continue
            tier = block.get("tier")
            if isinstance(tier, str) and tier:
                tiers[task_kind] = tier
        return tiers


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
            # NEW-02: the declared HTTP ingress channel. This loader enumerates fields
            # EXPLICITLY, so a new model field that is not listed here stays empty no matter
            # what the yaml says — the block would parse and compute nothing.
            ingress=data.get("ingress", {}),
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
    "LOCAL_RUNTIME_MODE",
    "MAEZO_SPEC_DIR_ENV",
    "RUNTIME_MODE_ENVS",
    "AgentDefinition",
    "AgentLoader",
    "AgentRegistry",
    "SpecDirOverrideRefusedError",
    "is_explicit_local_runtime",
    "resolve_spec_agents_dir",
    "resolve_spec_dir",
]
