"""Unit tests for maezo.agents package initialization — AgentLoader + AgentRegistry.

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import pytest

from maezo.agents import AgentLoader, AgentRegistry


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
