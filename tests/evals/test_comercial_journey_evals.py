"""Goldens do agente `comercial` (E-SF-17 / GTM-E-12), rodados pelo grafo REAL (`build`).

O agente nao chama LLM nem DMN: cada golden fixa a rota, o desfecho e a acao de envio que o turno
produz a partir da entrada tipada (passagem por referencia + sinais do receptor). Os `triggers`
(`signal:sinal_saude`, `signal:pedido_humano`) sao provados aqui, comportamentalmente, e cobrem os
gatilhos declarados em `spec/agents/comercial/agent.yaml` (cerca `test_trigger_coverage.py`).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.comercial.graph import build, new_comercial_state

from .conftest import load_golden

COMERCIAL_CASES = load_golden("comercial")


async def _rodar(case: dict[str, Any]) -> dict[str, Any]:
    compilado = build({"comercial_ligado": True}).compile()
    return dict(await compilado.ainvoke(new_comercial_state(case["input"]["state"])))


def _confere(case: dict[str, Any], saida: dict[str, Any]) -> list[str]:
    return [
        f"{campo}: esperado {valor!r}, obtido {saida.get(campo)!r}"
        for campo, valor in case["expect"]["fields"].items()
        if saida.get(campo) != valor
    ]


@pytest.mark.eval
@pytest.mark.parametrize("case", COMERCIAL_CASES, ids=[c["id"] for c in COMERCIAL_CASES])
async def test_golden_comercial(case: dict[str, Any]) -> None:
    assert _confere(case, await _rodar(case)) == []


@pytest.mark.eval
async def test_golden_comercial_nao_e_vacuo() -> None:
    """Mutacao: trocar a rota esperada de cada golden faz a conferencia falhar."""
    for case in COMERCIAL_CASES:
        mutado = copy.deepcopy(case)
        mutado["expect"]["fields"]["rota"] = "rota_inexistente"
        assert _confere(mutado, await _rodar(case)), case["id"]
