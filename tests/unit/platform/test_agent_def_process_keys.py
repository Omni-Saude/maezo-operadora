"""Cerca spec-first de `process_keys` no `validate-artifacts` — HEL-12.

O achado: `spec/agents/_template/agent.yaml` declarava `SP-OP-TEMPLATE-001`, que nao existe nem
em `docs/processes/contracts/` nem em `KNOWN_PROCESS_KEYS`. A causa mecanica e que NENHUMA
validacao cruzava `process_keys` com o universo de contratos — so `tools` x diretorios `mcp_*`.
A unica cerca era o `EffectPolicy.allows_process_key` do gateway, que nega em RUNTIME (bom, mas
descoberto so no primeiro start real).

Esta cerca e ADITIVA e fail-closed: toda chave declarada por um agent.yaml NAO-template tem de
existir como `docs/processes/contracts/<chave>.md` E em
`src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS`.
"""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.agent_def import validate_dir, validate_file
from maezo.platform.validation.result import Report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_ROOT = _REPO_ROOT / "docs" / "processes" / "contracts"

_BASE_YAML = """\
id: test-agent
name: "Test Agent"
role: "Testing"
phase: 0
autonomy_level: L3
process_keys:
{keys}
tools:
  - mcp-dmn.evaluate
"""


def _agent_file(tmp_path: Path, keys: list[str], agent_id: str = "test-agent") -> Path:
    agent_dir = tmp_path / agent_id
    agent_dir.mkdir(parents=True)
    path = agent_dir / "agent.yaml"
    path.write_text(_BASE_YAML.format(keys="\n".join(f"  - {k}" for k in keys)))
    return path


def _validate(path: Path) -> Report:
    report = Report()
    validate_file(path, frozenset({"dmn"}), report)
    return report


class TestProcessKeyFence:
    def test_phantom_process_key_fails(self, tmp_path: Path) -> None:
        """Uma chave que nao tem contrato nem entrada no allowlist e um erro BLOQUEANTE."""
        report = _validate(_agent_file(tmp_path, ["SP-OP-TEMPLATE-001"]))
        assert not report.ok
        messages = " | ".join(f.message for f in report.findings)
        assert "SP-OP-TEMPLATE-001" in messages
        assert "docs/processes/contracts" in messages
        assert "KNOWN_PROCESS_KEYS" in messages

    def test_valid_process_key_passes(self, tmp_path: Path) -> None:
        report = _validate(_agent_file(tmp_path, ["SP-OP-AUTH-001"]))
        assert report.ok, [f.message for f in report.findings]

    def test_key_with_contract_but_outside_the_allowlist_fails(self, tmp_path: Path) -> None:
        """`SP-OP-ANS-CRON-001` TEM contrato mas esta deliberadamente fora de
        `KNOWN_PROCESS_KEYS` (ADR-0016/AF-05: iniciado por timer, nao por agente). A cerca exige
        as DUAS condicoes, entao declara-la num agent.yaml e um erro."""
        assert (_CONTRACTS_ROOT / "SP-OP-ANS-CRON-001.md").is_file()  # nao-vacuidade
        report = _validate(_agent_file(tmp_path, ["SP-OP-ANS-CRON-001"]))
        assert not report.ok
        assert any("KNOWN_PROCESS_KEYS" in f.message for f in report.findings)

    def test_unsubstituted_placeholder_fails_with_its_own_message(self, tmp_path: Path) -> None:
        """Um agente derivado que esqueceu de substituir o token do template falha NOMEANDO
        o placeholder — nao com a mensagem generica de 'contrato inexistente'."""
        report = _validate(_agent_file(tmp_path, ["SP-OP-<AGENT>-001"]))
        assert not report.ok
        assert any("placeholder" in f.message for f in report.findings)

    def test_escalation_process_is_fenced_too(self, tmp_path: Path) -> None:
        """`escalation.process` e a MESMA especie de referencia (o achado cita
        `spec/agents/_template/agent.yaml:11` E `:33`) e passa pela mesma cerca."""
        agent_dir = tmp_path / "esc-agent"
        agent_dir.mkdir(parents=True)
        path = agent_dir / "agent.yaml"
        path.write_text(
            _BASE_YAML.format(keys="  - SP-OP-AUTH-001") + "escalation:\n  process: SP-OP-FANTASMA-001\n"
        )
        report = _validate(path)
        assert not report.ok
        assert any("SP-OP-FANTASMA-001" in f.message for f in report.findings)

    def test_escalation_secondary_process_is_fenced_too(self, tmp_path: Path) -> None:
        """GUS-06 (Agent Fleet Audit): `escalation.secondary_process` (lucas's own convention for
        a two-process agent, `escalation.secondary_process: SP-OP-CANCEL-001`) is the SAME kind
        of process-key reference as `escalation.process` — a phantom key here must fail exactly
        as loudly, not slip through unvalidated forever."""
        agent_dir = tmp_path / "esc-agent-2"
        agent_dir.mkdir(parents=True)
        path = agent_dir / "agent.yaml"
        path.write_text(
            _BASE_YAML.format(keys="  - SP-OP-AUTH-001")
            + "escalation:\n  process: SP-OP-AUTH-001\n  secondary_process: SP-OP-FANTASMA-001\n"
        )
        report = _validate(path)
        assert not report.ok
        assert any("SP-OP-FANTASMA-001" in f.message for f in report.findings)

    def test_valid_escalation_secondary_process_passes(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "esc-agent-3"
        agent_dir.mkdir(parents=True)
        path = agent_dir / "agent.yaml"
        path.write_text(
            _BASE_YAML.format(keys="  - SP-OP-AUTH-001")
            + "escalation:\n  process: SP-OP-AUTH-001\n  secondary_process: SP-OP-CANCEL-001\n"
        )
        report = _validate(path)
        assert report.ok, [f.message for f in report.findings]


class TestTemplateStaysSkipped:
    def test_validate_dir_still_skips_the_template_directory(self, tmp_path: Path) -> None:
        """DECISAO DOCUMENTADA: `validate_dir` continua PULANDO `_template/` — ele carrega
        placeholders por desenho. A protecao para o agente derivado e o ramo
        `test_unsubstituted_placeholder_fails_with_its_own_message` acima, que dispara assim que
        o diretorio deixa de se chamar `_template`."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        template_dir = agents_root / "_template"
        template_dir.mkdir()
        (template_dir / "agent.yaml").write_text(_BASE_YAML.format(keys="  - SP-OP-<AGENT>-001"))
        real_dir = agents_root / "real-agent"
        real_dir.mkdir()
        (real_dir / "agent.yaml").write_text(_BASE_YAML.format(keys="  - SP-OP-AUTH-001"))

        tools_root = tmp_path / "tools"
        (tools_root / "mcp_dmn").mkdir(parents=True)

        report = Report()
        validate_dir(agents_root, tools_root, report)
        assert report.ok, [f.message for f in report.findings]


class TestRealRepoStaysGreen:
    def test_every_shipped_agent_survives_the_new_fence(self) -> None:
        """Os 10 agentes reais passam. Os que NAO declaram `process_keys` (andre, beatriz,
        fernando, valentina) nao sao pegos: a cerca valida as entradas DECLARADAS, e a ausencia
        de `process_keys` e o gap ANDRE-PROCESS-KEYS (owner-decision, OPEN, WP-AGENT-BINDINGS)
        do programa irmao — deliberadamente NAO tocado aqui."""
        agents_root = _REPO_ROOT / "spec" / "agents"
        tools_root = _REPO_ROOT / "src" / "maezo" / "tools"
        report = Report()
        validate_dir(agents_root, tools_root, report)
        assert report.ok, [f.message for f in report.findings]
