"""Cerca: todo `record_agent_error(...)` em `agents/<id>/delegation.py` declara o `agent` certo.

VERIFY-TRAIN2 FINDING 2 (trem `r5/train-2`, PR #336). A merge que rotulou o contador
(`record_agent_error(*, agent, error_type)`) converteu oito sitios de delegacao A2A
(`agents/{andre,beatriz,carolina,fernando,gustavo,marina,rafael,valentina}/delegation.py`) de
`record_agent_error()` para `record_agent_error(agent="<id>", error_type=classify_agent_error_type(exc))`.
Nenhum teste pinava o LITERAL do rotulo `agent=`: revertendo um sitio para a forma sem argumento,
ou trocando o literal por outro id, a suite unitaria inteira ficava byte-a-byte identica (so
`make type` capturava a forma sem argumento; NADA capturava o rotulo errado) -- prova por mutacao
no relatorio do gatekeeper.

O QUE ESTA CERCA ASSERTA, por AST (nunca `substring in file`, pela mesma razao que
`test_alert_metrics_fence.py::_uninstrumented_graph_invocations` usa AST): para CADA
`agents/*/delegation.py` que contem uma chamada a `record_agent_error`, essa chamada
  1. passa `agent=` como keyword cujo valor e' uma STRING LITERAL, e
  2. esse literal e' EXATAMENTE o nome do diretorio do agente (`andre`, nao `Andre` nem o nome de
     outro agente), e
  3. passa `error_type=` como keyword (qualquer expressao -- normalmente
     `classify_agent_error_type(exc)`, nunca posicional e nunca ausente).
Nao valida COMO `error_type` e calculado (isso e' `test_start_failure_yields_error_desfecho_...`
e o proprio mypy via a assinatura keyword-only); valida apenas que o SITIO declara o AGENTE
CERTO -- o rotulo que faz `MaezoSLAAgentErrorRateHigh`/`MaezoAgentCrashLoop` apontarem para o
agente que realmente falhou, nao para um vizinho.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_DIR: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"

#: Todo `agents/<id>/delegation.py` no repo, ordenado -- inclui `helena`, que ORIGINA delegacoes
#: (nunca as recebe) e por isso legitimamente nao tem nenhuma chamada a `record_agent_error` aqui;
#: seu proprio sitio de contagem vive em `platform/webhooks/whatsapp/dispatch.py`, fora do escopo
#: desta cerca (coberto por `test_every_graph_invocation_in_src_counts_agent_errors`).
_DELEGATION_FILES: Final[list[Path]] = sorted(_AGENTS_DIR.glob("*/delegation.py"))

assert _DELEGATION_FILES, f"nenhum agents/*/delegation.py encontrado sob {_AGENTS_DIR}"


def _record_agent_error_calls(tree: ast.Module) -> list[ast.Call]:
    """Toda CHAMADA a uma funcao/metodo cujo nome final e' `record_agent_error`."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "record_agent_error":
            calls.append(node)
    return calls


@pytest.mark.parametrize("path", _DELEGATION_FILES, ids=lambda p: p.parent.name)
def test_delegation_record_agent_error_calls_declare_the_owning_agent(path: Path) -> None:
    """Cada chamada `record_agent_error(...)` em `agents/<id>/delegation.py` declara `agent="<id>"`
    e passa `error_type=` como keyword. Arquivos sem nenhuma chamada (hoje so `helena`) passam
    vazios -- o dono de origem da delegacao nao conta erro aqui, por design (ver docstring do
    modulo)."""
    agent_id = path.parent.name
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = _record_agent_error_calls(tree)

    for call in calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        assert not call.args, (
            f"{path.relative_to(_REPO_ROOT)}:{call.lineno}: record_agent_error chamado com "
            f"argumento posicional ({ast.dump(call)}) -- a assinatura e' keyword-only "
            "(*, agent, error_type); um argumento posicional e' sinal de uma forma zero-arg "
            "revertida por engano."
        )
        assert "agent" in kwargs, (
            f"{path.relative_to(_REPO_ROOT)}:{call.lineno}: record_agent_error chamado sem o "
            f"keyword `agent=` -- MaezoAgentCrashLoop/MaezoSLAAgentErrorRateHigh ficam sem saber "
            f"qual agente falhou neste sitio (agente esperado: {agent_id!r})"
        )
        agent_value = kwargs["agent"]
        assert isinstance(agent_value, ast.Constant) and isinstance(agent_value.value, str), (
            f"{path.relative_to(_REPO_ROOT)}:{call.lineno}: `agent=` nao e' uma string literal "
            f"({ast.dump(agent_value)}) -- o rotulo do contador tem de ser um valor fixo e "
            "auditavel no codigo-fonte, nao computado em tempo de execucao"
        )
        assert agent_value.value == agent_id, (
            f"{path.relative_to(_REPO_ROOT)}:{call.lineno}: record_agent_error(agent="
            f"{agent_value.value!r}, ...) dentro de agents/{agent_id}/delegation.py -- o rotulo "
            f"deveria ser {agent_id!r}. Um rotulo trocado faz o alerta apontar para o agente "
            "errado sem que nenhum teste de comportamento perceba (o turno ainda falha, o "
            "contador ainda incrementa -- so o `agent` no rotulo mente)."
        )
        assert "error_type" in kwargs, (
            f"{path.relative_to(_REPO_ROOT)}:{call.lineno}: record_agent_error chamado sem o "
            "keyword `error_type=` -- a assinatura e' keyword-only para os dois argumentos."
        )


def test_exactly_the_known_delegation_targets_count_their_own_agent_error() -> None:
    """Rede de seguranca alem do teste por arquivo acima: EXATAMENTE os oito handlers de
    delegacao A2A que sao ALVO (recebem, nao originam) contam `record_agent_error` -- nem um a
    menos (um sitio apagado silenciosamente, a propria classe de defeito ALERTS-WITHOUT-METRICS-a
    de novo) nem um a mais sem que este arquivo seja atualizado para descreve-lo."""
    ids_with_calls = {
        path.parent.name
        for path in _DELEGATION_FILES
        if _record_agent_error_calls(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    }
    assert ids_with_calls == {
        "andre",
        "beatriz",
        "carolina",
        "fernando",
        "gustavo",
        "marina",
        "rafael",
        "valentina",
    }
