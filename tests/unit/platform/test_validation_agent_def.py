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


WHATSAPP_AGENT_YAML = """\
id: test-agent
name: "Test Agent"
role: "Testing"
phase: 0
autonomy_level: L3
tools:
  - mcp-whatsapp.send_message
"""


class TestWhatsappChannelPosture:
    """GAP 11.7: an agent granting `mcp-whatsapp.send_message` must declare
    `channels.whatsapp.inbound` explicitly (true/false) — never silently assumed."""

    def test_missing_channels_block_fails(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, WHATSAPP_AGENT_YAML)
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert not report.ok
        assert any("channels.whatsapp.inbound" in f.message for f in report.findings)

    def test_non_boolean_inbound_fails(self, tmp_path: Path) -> None:
        content = WHATSAPP_AGENT_YAML + 'channels:\n  whatsapp:\n    inbound: "no"\n'
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert not report.ok
        assert any("channels.whatsapp.inbound" in f.message for f in report.findings)

    def test_explicit_inbound_false_passes(self, tmp_path: Path) -> None:
        content = WHATSAPP_AGENT_YAML + "channels:\n  whatsapp:\n    outbound: true\n    inbound: false\n"
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert report.ok, [f.message for f in report.findings]

    def test_explicit_inbound_true_passes(self, tmp_path: Path) -> None:
        # True is truthful only for the graph the actual WhatsApp dispatcher invokes.
        content = WHATSAPP_AGENT_YAML.replace("id: test-agent", "id: helena") + (
            "channels:\n  whatsapp:\n    outbound: true\n    inbound: true\n"
        )
        path = _agent_file(tmp_path, content, agent_id="helena")
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert report.ok, [f.message for f in report.findings]

    def test_agent_without_the_tool_is_unaffected(self, tmp_path: Path) -> None:
        path = _agent_file(tmp_path, VALID_AGENT_YAML)  # no mcp-whatsapp.send_message
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert report.ok, [f.message for f in report.findings]


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
            "a2a:\n  accepted_task_types: []\n  queue_ref: agents.tasks.test-agent\n"
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
        assert any("handler_symbol must be" in f.message for f in report.findings)

    def test_handler_symbol_with_wrong_factory_name_fails_the_naming_check(self, tmp_path: Path) -> None:
        # §Delta A2A-YAML-DISCLOSURE F2: this test is named `..._that_does_not_resolve_fails` in
        # its pre-repair form, but `make_nonexistent_handler` != the canonical
        # `make_rafael_handler` it must equal — it never reaches `importlib.import_module` at all,
        # it trips the EARLIER `expected_symbol` naming check (same branch as
        # `test_handler_symbol_must_name_this_agent` above, different wrong value). Renamed to say
        # what it actually checks; `test_handler_symbol_naming_a_module_that_does_not_import_fails`
        # below is the one that reaches importlib.
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
        assert any("handler_symbol must be" in f.message for f in report.findings)

    def test_handler_symbol_naming_a_module_that_does_not_import_fails(self, tmp_path: Path) -> None:
        # §Delta A2A-YAML-DISCLOSURE F2: the ONLY way to reach the `importlib.import_module` tail
        # of `_validate_a2a_handler_disclosure` is a symbol that is ALREADY canonical (so it
        # passes the `expected_symbol` naming check above) but whose module genuinely does not
        # exist — an agent id with no `agents/<id>/delegation.py` at all. "novato" is not a real
        # agent (`spec/agents/` has no such directory), so
        # `maezo.agents.novato.delegation::make_novato_handler` is canonical-shaped for id=novato
        # yet unimportable.
        content = VALID_AGENT_YAML.replace("id: test-agent", "id: novato") + (
            "a2a:\n"
            "  accepted_task_types: [x]\n"
            "  queue_ref: agents.tasks.novato\n"
            "  handler_status: registrado\n"
            "  handler_symbol: maezo.agents.novato.delegation::make_novato_handler\n"
        )
        path = _agent_file(tmp_path, content, agent_id="novato")
        report = Report()
        validate_file(path, frozenset({"dmn", "memory"}), report)
        assert not report.ok
        assert any("names a module that does not import" in f.message for f in report.findings), [
            f.message for f in report.findings
        ]

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


class TestWhatsappPostureRuntimeBinding:
    def test_declared_postures_match_actual_helena_dispatcher(self) -> None:
        import ast
        import inspect

        from maezo.agents import AgentLoader
        from maezo.agents.helena import graph
        from maezo.platform.webhooks.whatsapp import dispatch

        # The symbols actually invoked by dispatch belong to Helena; HTTP ingress is separate.
        assert dispatch.build is graph.build
        assert dispatch.new_helena_state is graph.new_helena_state
        tree = ast.parse(inspect.getsource(dispatch.HelenaDispatcher))
        called = {
            n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert {"build", "new_helena_state"} <= called
        root = Path(__file__).resolve().parents[3] / "spec" / "agents"
        senders = {}
        loader = AgentLoader()
        for path in root.glob("*/agent.yaml"):
            if path.parent.name == "_template":
                continue
            definition = loader.load(path)
            if "mcp-whatsapp.send_message" in definition.tools:
                senders[definition.id] = definition.channels["whatsapp"]
        assert senders == {
            "helena": {"outbound": True, "inbound": True},
            "lucas": {"outbound": True, "inbound": False},
            "fernando": {"outbound": True, "inbound": False},
        }

    def test_non_helena_cannot_claim_whatsapp_inbound(self, tmp_path: Path) -> None:
        content = WHATSAPP_AGENT_YAML + "channels:\n  whatsapp:\n    inbound: true\n"
        path = _agent_file(tmp_path, content)
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert not report.ok
        assert any("HelenaDispatcher" in item.message for item in report.findings)

    def test_helena_cannot_deny_whatsapp_inbound(self, tmp_path: Path) -> None:
        content = WHATSAPP_AGENT_YAML.replace("id: test-agent", "id: helena") + (
            "channels:\n  whatsapp:\n    inbound: false\n"
        )
        path = _agent_file(tmp_path, content, agent_id="helena")
        report = Report()
        validate_file(path, frozenset({"whatsapp"}), report)
        assert not report.ok
        assert any("HelenaDispatcher" in item.message for item in report.findings)
