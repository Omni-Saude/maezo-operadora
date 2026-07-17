"""Unit tests for maezo.agents package initialization — AgentLoader + AgentRegistry.

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import pytest

from maezo.agents import (
    MAEZO_SPEC_DIR_ENV,
    AgentLoader,
    AgentRegistry,
    resolve_spec_agents_dir,
    resolve_spec_dir,
)

#: Repo root, computed independently of maezo.agents._default_spec_dir_candidates()
#: so these tests don't tautologically validate against the same math.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent


class TestAgentLoader:
    """Tests for AgentLoader — loads agent.yaml → AgentDefinition."""

    def test_load_agent_definition_from_yaml(self, tmp_path: Path) -> None:
        """AgentLoader should parse a minimal agent.yaml into an AgentDefinition."""
        yaml_content = """\
id: test-agent
name: "Test Agent"
role: "Testing framework"
phase: 0
autonomy_level: L3
process_keys:
  - SP-OP-TEST-001
tools:
  - mcp-dmn.evaluate
  - mcp-memory.read_write
"""
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(yaml_content)

        loader = AgentLoader()
        definition = loader.load(yaml_path)

        assert definition.id == "test-agent"
        assert definition.name == "Test Agent"
        assert definition.role == "Testing framework"
        assert definition.phase == 0
        assert definition.autonomy_level == "L3"
        assert definition.process_keys == ["SP-OP-TEST-001"]
        assert definition.tools == ["mcp-dmn.evaluate", "mcp-memory.read_write"]

    def test_load_agent_definition_from_dict(self) -> None:
        """AgentLoader should accept a dict directly (for programmatic loading)."""
        data = {
            "id": "dict-agent",
            "name": "Dict Agent",
            "role": "Dict role",
            "phase": 1,
            "autonomy_level": "L2",
            "process_keys": ["SP-OP-DICT-001"],
            "tools": ["mcp-test.tool"],
        }

        loader = AgentLoader()
        definition = loader.load_from_dict(data)

        assert definition.id == "dict-agent"
        assert definition.autonomy_level == "L2"

    def test_load_agent_definition_missing_required_raises(self, tmp_path: Path) -> None:
        """AgentLoader should raise ValueError when required fields are missing."""
        yaml_content = """\
id: incomplete-agent
# name is missing
"""
        agent_dir = tmp_path / "incomplete-agent"
        agent_dir.mkdir()
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(yaml_content)

        loader = AgentLoader()
        with pytest.raises((ValueError, TypeError)):
            loader.load(yaml_path)


class TestResolveSpecDir:
    """Tests for resolve_spec_dir()/resolve_spec_agents_dir() (T0.3/B14 single source of truth)."""

    def test_default_resolves_to_repo_spec_directory(self) -> None:
        """With no MAEZO_SPEC_DIR override, resolution finds <repo_root>/spec."""
        assert resolve_spec_dir() == _REPO_ROOT / "spec"

    def test_default_spec_agents_dir_contains_known_agents(self) -> None:
        """resolve_spec_agents_dir() must resolve to the real spec/agents/ directory."""
        agents_dir = resolve_spec_agents_dir()
        assert agents_dir == _REPO_ROOT / "spec" / "agents"
        assert (agents_dir / "rafael" / "agent.yaml").exists()
        assert (agents_dir / "helena" / "agent.yaml").exists()

    def test_env_override_is_honored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAEZO_SPEC_DIR, when set, takes priority over the package-relative default."""
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))
        assert resolve_spec_dir() == tmp_path.resolve()

    def test_env_override_missing_directory_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed: a MAEZO_SPEC_DIR pointing nowhere must raise, never fall back silently."""
        bogus = tmp_path / "does-not-exist"
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(bogus))

        with pytest.raises(FileNotFoundError, match=MAEZO_SPEC_DIR_ENV):
            resolve_spec_dir()

    def test_spec_agents_missing_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: a resolved spec/ dir without an agents/ subdirectory must raise."""
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(FileNotFoundError, match="agents"):
            resolve_spec_agents_dir()

    def test_installed_wheel_package_adjacent_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1.8 REVISE-1: in an installed wheel (no repo layout), resolution falls back to
        the package-adjacent `<site-packages>/maezo/spec` laid down by the hatch
        force-include — without requiring MAEZO_SPEC_DIR."""
        import maezo.agents as agents_pkg

        site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        fake_init = site / "maezo" / "agents" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated installed module file\n")
        pkg_spec = site / "maezo" / "spec"
        (pkg_spec / "policies" / "autonomy").mkdir(parents=True)

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        # repo-layout candidate (parents[3]/spec = .../python3.12/spec) does not
        # exist here, so the package-adjacent fallback must win.
        assert resolve_spec_dir() == pkg_spec

    def test_repo_layout_wins_over_package_adjacent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Candidate order is pinned: the repo checkout layout takes precedence over the
        installed-wheel package-adjacent fallback when both exist."""
        import maezo.agents as agents_pkg

        fake_init = tmp_path / "src" / "maezo" / "agents" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated checkout module file\n")
        repo_spec = tmp_path / "spec"
        repo_spec.mkdir()
        pkg_spec = tmp_path / "src" / "maezo" / "spec"
        pkg_spec.mkdir(parents=True)

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        assert resolve_spec_dir() == repo_spec

    def test_no_candidate_raises_fail_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: with no env override and NO existing default candidate (neither
        repo layout nor package-adjacent), resolution must raise — never silently
        accept a nonexistent path."""
        import maezo.agents as agents_pkg

        fake_init = tmp_path / "a" / "b" / "c" / "d" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated module file with no spec/ anywhere\n")

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        with pytest.raises(FileNotFoundError, match=MAEZO_SPEC_DIR_ENV):
            resolve_spec_dir()


class TestAgentLoaderLoadById:
    """Tests for AgentLoader.load_by_id() — reads spec/agents/<id>/agent.yaml (T0.3/B14)."""

    def test_load_by_id_reads_real_agent_from_spec_agents(self) -> None:
        """load_by_id resolves spec/agents/ by default and loads a real agent."""
        loader = AgentLoader()
        definition = loader.load_by_id("rafael")

        assert definition.id == "rafael"
        assert definition.name == "Rafael Nogueira"

    def test_load_by_id_missing_agent_raises(self) -> None:
        """Fail-closed: an unknown agent id must raise, never return a default/empty definition."""
        loader = AgentLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_by_id("this-agent-does-not-exist")

    def test_load_by_id_honors_explicit_spec_agents_dir_override(self, tmp_path: Path) -> None:
        """An explicit spec_agents_dir argument overrides the resolved default."""
        agent_dir = tmp_path / "custom-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text('id: custom-agent\nname: "Custom Agent"\nrole: "Custom role"\n')

        loader = AgentLoader()
        definition = loader.load_by_id("custom-agent", spec_agents_dir=tmp_path)

        assert definition.id == "custom-agent"
        assert definition.name == "Custom Agent"


class TestAgentRegistry:
    """Tests for AgentRegistry — registers agents by id."""

    def test_register_agent(self) -> None:
        """AgentRegistry.register should store and retrieve an AgentDefinition by id."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        definition = AgentDefinition(
            id="rafael",
            name="Rafael Nogueira",
            role="Analista de Autorizacao",
            phase=1,
            autonomy_level="L2",
            process_keys=["SP-OP-AUTH-001"],
            tools=["mcp-dmn.evaluate"],
        )

        registry.register(definition)

        retrieved = registry.get("rafael")
        assert retrieved is not None
        assert retrieved.id == "rafael"
        assert retrieved.name == "Rafael Nogueira"

    def test_register_duplicate_raises(self) -> None:
        """AgentRegistry.register should raise ValueError on duplicate agent id."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        definition = AgentDefinition(
            id="helena",
            name="Helena Moreira",
            role="Health Navigator",
            phase=0,
            autonomy_level="L3",
            process_keys=["SP-OP-ESCALATION-001"],
            tools=["mcp-whatsapp.send_message"],
        )

        registry.register(definition)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(definition)

    def test_get_nonexistent_returns_none(self) -> None:
        """AgentRegistry.get should return None for unregistered agents."""
        registry = AgentRegistry()

        result = registry.get("nonexistent")
        assert result is None

    def test_list_agents(self) -> None:
        """AgentRegistry.list should return all registered agent ids."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                id="agent-1",
                name="One",
                role="test",
                phase=0,
                autonomy_level="L3",
                process_keys=[],
                tools=[],
            )
        )
        registry.register(
            AgentDefinition(
                id="agent-2",
                name="Two",
                role="test",
                phase=1,
                autonomy_level="L2",
                process_keys=[],
                tools=[],
            )
        )

        ids = registry.list_ids()
        assert set(ids) == {"agent-1", "agent-2"}
