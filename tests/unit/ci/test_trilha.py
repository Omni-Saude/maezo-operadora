"""`scripts/ci/trilha.py` (ADR-0067 fase 4): a trilha de CI pelo que o PR toca; na duvida, plataforma."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ci.trilha import agentes_existentes, classificar
from scripts.ci.trilha import testes_do_agente as _testes_do_agente

_RAIZ = Path(__file__).resolve().parents[3]
_AGENTES = {"helena", "lucas"}


@pytest.mark.parametrize(
    ("arquivos", "esperado"),
    [
        (
            ["spec/agents/helena/pacote/configuracao.yaml"],
            {"plataforma": False, "agentes": [], "conteudo": ["helena"]},
        ),
        (
            ["src/maezo/agents/helena/graph.py", "tests/unit/agents/test_helena_v4.py"],
            {"plataforma": False, "agentes": ["helena"], "conteudo": []},
        ),
        (
            ["src/maezo/agents/helena/graph.py", "spec/agents/helena/pacote/configuracao.yaml"],
            {"plataforma": False, "agentes": ["helena"], "conteudo": []},
        ),
        (
            ["src/maezo/agents/lucas/graph.py", "src/maezo/agents/helena/graph.py"],
            {"plataforma": False, "agentes": ["helena", "lucas"], "conteudo": []},
        ),
        # contrato, framework de agentes, plataforma, spec fora do pacote, CI: a suite inteira
        (["src/maezo/contratos/conversa.py"], {"plataforma": True, "agentes": [], "conteudo": []}),
        (["src/maezo/agents/__init__.py"], {"plataforma": True, "agentes": [], "conteudo": []}),
        (["spec/agents/helena/agent.yaml"], {"plataforma": True, "agentes": [], "conteudo": []}),
        (
            ["src/maezo/agents/helena/graph.py", "src/maezo/platform/webhooks/whatsapp/dispatch.py"],
            {"plataforma": True, "agentes": ["helena"], "conteudo": []},
        ),
        ([".github/workflows/ci.yml"], {"plataforma": True, "agentes": [], "conteudo": []}),
        # agente que nao existe, teste de outra pasta: plataforma
        (["src/maezo/agents/fantasma/x.py"], {"plataforma": True, "agentes": [], "conteudo": []}),
        (["tests/unit/platform/test_helena_algo.py"], {"plataforma": True, "agentes": [], "conteudo": []}),
        # sem diff: a suite inteira
        ([], {"plataforma": True, "agentes": [], "conteudo": []}),
    ],
)
def test_classificacao(arquivos: list[str], esperado: dict[str, object]) -> None:
    assert classificar(arquivos, _AGENTES) == esperado


def test_caminho_do_windows_e_linhas_vazias() -> None:
    assert classificar(["src\\maezo\\agents\\helena\\graph.py", "", "  "], _AGENTES)["plataforma"] is False


def test_os_agentes_reais_e_os_testes_da_helena() -> None:
    assert {"helena", "lucas"} <= agentes_existentes(_RAIZ)
    testes = _testes_do_agente("helena", _RAIZ)
    assert "tests/unit/agents/test_helena_pacote.py" in testes
    assert "tests/unit/ci/test_check_fronteira_agentes.py" in testes
    assert all(t.startswith(("tests/unit/", "tests/contract/")) for t in testes)
