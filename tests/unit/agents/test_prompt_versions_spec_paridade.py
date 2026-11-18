"""`prompt_versions` de `spec/agents/<id>/agent.yaml` == `PROMPT_VERSIONS` do grafo, para TODO agente.

Generaliza o que `test_andre.py`, `test_beatriz.py`, `test_lucas_dono_declarado.py` e
`test_helena_dono_declarado.py` ja' provavam um a um (esses continuam: cada um tem asserts proprios).
A versao e' o que diz QUAL texto falou com o modelo ou com a pessoa; quando o yaml e o codigo
divergem, a auditoria e o eval apontam para o texto errado. Ate' a DL-0191 nada amarrava os dois
para marina (o yaml ficou em v2 quando o codigo foi a v3) nem para fernando (o yaml nunca declarou
o `dossier` que o codigo versiona).

Agente cujo grafo nao exporta `PROMPT_VERSIONS` conta como `{}` (ex.: `comercial`, que nao tem
LLM e declara `prompt_versions: {}`). A UNICA excecao e' `_EXCECOES`, com motivo.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

_RAIZ = Path(__file__).resolve().parents[3]
_SPECS = sorted((_RAIZ / "spec" / "agents").glob("*/agent.yaml"))

#: Agente -> motivo. Fechada: entrada nova e' edicao revisada aqui.
_EXCECOES: dict[str, str] = {
    "_template": (
        "HEL-14: o template declara `classify-v1` sem modulo de prompts (src/maezo/agents/_template/graph.py)"
    ),
}


def _declarado(spec: Path) -> dict[str, Any]:
    dados = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
    return dict(dados.get("prompt_versions") or {})


@pytest.mark.parametrize("spec", _SPECS, ids=lambda p: p.parent.name)
def test_o_yaml_declara_as_versoes_que_o_codigo_carrega(spec: Path) -> None:
    agente = spec.parent.name
    if agente in _EXCECOES:
        pytest.skip(_EXCECOES[agente])
    grafo = importlib.import_module(f"maezo.agents.{agente}.graph")
    no_codigo = dict(getattr(grafo, "PROMPT_VERSIONS", {}))

    assert _declarado(spec) == no_codigo, (
        f"{agente}: agent.yaml e PROMPT_VERSIONS divergiram. Suba a versao no codigo e atualize o "
        "yaml na MESMA entrega."
    )


def test_a_varredura_cobre_os_agentes() -> None:
    """Guarda contra varredura vazia: os agentes com prompt versionado estao todos aqui."""
    nomes = {spec.parent.name for spec in _SPECS}
    assert {"marina", "rafael", "andre", "beatriz", "lucas", "helena", "fernando"} <= nomes
    assert set(_EXCECOES) <= nomes
