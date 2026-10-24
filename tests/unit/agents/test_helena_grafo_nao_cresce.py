"""TRAVA DE TAMANHO do grafo da Helena (Plano de ajustes da Helena v2, etapa 11; ADR-0067 §5).

O `graph.py` da Helena chegou a ~11 mil linhas, com a regra de cada habilidade espalhada nele. A divisao por
habilidade (etapas 5 a 10) levou o codigo para `habilidades/<nome>/`, `travas_entrada.py`, `travas_saida.py`
e `roteador.py`. Esta trava impede o caminho de volta: o `graph.py` NAO PODE CRESCER. Codigo novo vai para o
arquivo da habilidade (ou da trava); quem tirar codigo do `graph.py` desce o teto no MESMO PR.
"""

from __future__ import annotations

from pathlib import Path

from maezo.agents.helena import graph

_GRAFO = Path(graph.__file__)
#: O teto, em linhas (medido no fim da etapa 10). So' DESCE.
TETO_DE_LINHAS: int = 8202


def test_o_graph_py_da_helena_nao_cresce() -> None:
    linhas = len(_GRAFO.read_text(encoding="utf-8").splitlines())
    assert linhas <= TETO_DE_LINHAS, (
        f"graph.py da Helena com {linhas} linhas (teto {TETO_DE_LINHAS}): o codigo novo vai para "
        "`habilidades/<nome>/`, `travas_entrada.py`, `travas_saida.py` ou `roteador.py` (ADR-0067 §5)."
    )


def test_o_teto_acompanha_a_reducao() -> None:
    """Teto folgado demais deixaria o grafo crescer de novo sem ninguem ver: no maximo 200 linhas de folga."""
    linhas = len(_GRAFO.read_text(encoding="utf-8").splitlines())
    assert TETO_DE_LINHAS - linhas <= 200, (
        f"o graph.py tem {linhas} linhas e o teto e' {TETO_DE_LINHAS}: desca o teto para {linhas}"
    )
