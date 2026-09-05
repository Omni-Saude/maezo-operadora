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


class TestA2AHandlerDisclosure:
    """WP A2A-YAML-DISCLOSURE (CC-02 residual / NEW-B2 / NEW-B3 / NEW-B4): `a2a.handler_status`
    is a structured, closed-vocabulary field a validator can enforce the SHAPE of. The deeper
    cross-check — that the declared value matches the real delegation.py/registration state — is
    `tests/unit/a2a/test_agent_card_handlers_parity.py`'s job, not this validator's."""

    def test_a2a_absent_entirely_is_not_required_to_disclose(self, tmp_path: Path) -> None:
        # `VALID_AGENT_YAML` has no `a2a:` key at all — an agent that does not participate in
        # A2A has nothing to disclose.
        path = _agent_file(tmp_path, VALID_AGENT_YAML)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert report.ok, [f.message for f in report.findings]

    def test_a2a_block_without_handler_status_fails(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: []\n"
            "  queue_ref: agents.tasks.test-agent\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("handler_status" in f.message for f in report.findings)

    def test_unknown_handler_status_value_fails(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: []\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: pronto\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("handler_status" in f.message for f in report.findings)

    def test_ausente_with_handler_symbol_is_a_contradiction(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: []\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: ausente\n"
            "  handler_symbol: maezo.agents.rafael.delegation::make_rafael_handler\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("cannot also name" in f.message for f in report.findings)

    def test_ausente_without_handler_symbol_passes(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: []\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: ausente\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert report.ok, [f.message for f in report.findings]

    def test_registrado_without_handler_symbol_fails(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: [x]\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: registrado\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("handler_symbol is missing" in f.message for f in report.findings)

    def test_pronto_sem_registro_also_requires_handler_symbol(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: [x]\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: pronto_sem_registro\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("handler_symbol is missing" in f.message for f in report.findings)

    def test_handler_symbol_must_name_this_agent(self, tmp_path: Path) -> None:
        # id is "test-agent" but the symbol claims to be rafael's handler.
        content = VALID_AGENT_YAML + (
            "a2a:\n"
            "  accepted_task_types: [x]\n"
            "  queue_ref: agents.tasks.test-agent\n"
            "  handler_status: registrado\n"
            "  handler_symbol: maezo.agents.rafael.delegation::make_rafael_handler\n"
        )
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any(
            "handler_symbol must be" in f.message for f in report.findings
        )

    def test_handler_symbol_that_does_not_resolve_fails(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML.replace("id: test-agent", "id: rafael") + (
            "a2a:\n"
            "  accepted_task_types: [x]\n"
            "  queue_ref: agents.tasks.rafael\n"
            "  handler_status: registrado\n"
            "  handler_symbol: maezo.agents.rafael.delegation::make_nonexistent_handler\n"
        )
        path = _agent_file(tmp_path, content, agent_id="rafael")
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok

    def test_handler_symbol_that_resolves_passes(self, tmp_path: Path) -> None:
        content = VALID_AGENT_YAML.replace("id: test-agent", "id: rafael") + (
            "a2a:\n"
            "  accepted_task_types: [authorization.analyze]\n"
            "  queue_ref: agents.tasks.rafael\n"
            "  handler_status: registrado\n"
            "  handler_symbol: maezo.agents.rafael.delegation::make_rafael_handler\n"
        )
        path = _agent_file(tmp_path, content, agent_id="rafael")
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert report.ok, [f.message for f in report.findings]
