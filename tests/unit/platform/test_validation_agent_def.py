"""Unit tests for maezo.platform.validation.agent_def — agent.yaml validation."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.agent_def import known_mcp_servers, validate_dir, validate_file
from maezo.platform.validation.result import Report

VALID_AGENT_YAML = """\
id: test-agent
name: "Test Agent"
role: "Testing"
phase: 0
autonomy_level: L3
tools:
  - mcp-dmn.evaluate
  - mcp-memory.read_write
"""


def _tools_root(tmp_path: Path, servers: tuple[str, ...] = ("dmn", "memory")) -> Path:
    root = tmp_path / "tools"
    root.mkdir()
    for name in servers:
        (root / f"mcp_{name}").mkdir()
    return root


def _agent_file(tmp_path: Path, content: str, agent_id: str = "test-agent") -> Path:
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir()
    path = agent_dir / "agent.yaml"
    path.write_text(content)
    return path


class TestValidateFileHappyPath:
    def test_valid_agent_yaml_passes(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, VALID_AGENT_YAML)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert report.ok, [f.message for f in report.findings]


class TestMissingRequiredField:
    def test_missing_role_fails(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, VALID_AGENT_YAML.replace('role: "Testing"\n', ""))
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok

    def test_nonexistent_file_fails(self, tmp_path: Path) -> None:
        report = Report()
        validate_file(tmp_path / "nope" / "agent.yaml", frozenset(), report)
        assert not report.ok


class TestUnknownMcpServer:
    def test_tool_referencing_unknown_server_fails(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, VALID_AGENT_YAML)
        report = Report()
        validate_file(path, frozenset({"memory"}), report)  # "dmn" is missing
        assert not report.ok
        assert any("unknown MCP server" in f.message for f in report.findings)

    def test_malformed_tool_id_fails(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, VALID_AGENT_YAML.replace("mcp-dmn.evaluate", "not-a-valid-tool-id"))
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("does not follow the mcp-<server>.<action> pattern" in f.message for f in report.findings)


class TestEmptyToolsAllowlist:
    def test_empty_tools_fails(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML.replace(
            "tools:\n  - mcp-dmn.evaluate\n  - mcp-memory.read_write\n", "tools: []\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok


class TestMalformedYaml:
    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, "id: test\nname: [unterminated\n")
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok


class TestKnownMcpServers:
    def test_discovers_mcp_dirs(self, tmp_path: Path) -> None:
        root = _tools_root(tmp_path)
        assert known_mcp_servers(root) == frozenset({"dmn", "memory"})

    def test_missing_tools_root_returns_empty(self, tmp_path: Path) -> None:
        assert known_mcp_servers(tmp_path / "does-not-exist") == frozenset()


class TestValidateDir:
    def test_missing_agents_root_is_an_error(self, tmp_path: Path) -> None:
        report = Report()
        validate_dir(tmp_path / "nope", tmp_path / "tools", report)
        assert not report.ok

    def test_no_agent_yaml_anywhere_is_an_error(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        (agents_root / "empty-dir").mkdir()
        report = Report()
        validate_dir(agents_root, tmp_path / "tools", report)
        assert not report.ok

    def test_template_directory_is_skipped(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        template_dir = agents_root / "_template"
        template_dir.mkdir()
        (template_dir / "agent.yaml").write_text(
            VALID_AGENT_YAML.replace("mcp-dmn.evaluate", "mcp-nonexistent.action")
        )
        real_dir = agents_root / "real-agent"
        real_dir.mkdir()
        (real_dir / "agent.yaml").write_text(VALID_AGENT_YAML)

        tools_root = _tools_root(tmp_path)
        report = Report()
        validate_dir(agents_root, tools_root, report)
        assert report.ok, [f.message for f in report.findings]

    def test_real_repo_spec_agents_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        agents_root = repo_root / "spec" / "agents"
        tools_root = repo_root / "src" / "maezo" / "tools"
        report = Report()
        validate_dir(agents_root, tools_root, report)
        assert report.ok, [f.message for f in report.findings]
