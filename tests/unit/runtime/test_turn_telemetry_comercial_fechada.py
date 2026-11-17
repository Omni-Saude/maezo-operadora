"""Correcao de telemetria do agente comercial (onda 2, E-SF-17): emissao ligada, gravacao fechada.

O DEFEITO: o vocabulario de desfecho do agente `comercial` estava declarado em
`runtime.turn_telemetry`, mas nenhum no do grafo chamava `emit_turn_desfecho` e a cerca de frota
(`tests/unit/agents/test_terminal_nodes_emit_desfecho.py`) nao cobria o agente.

A CORRECAO tem duas metades, e este arquivo trava as duas:

1. O no terminal `finalize` chama `emit_turn_desfecho` com o desfecho e a rota do turno; os
   valores passam pelo vocabulario fechado (fora dele -> `"outro"`).
2. A gravacao no contador continua FECHADA: o agente esta' em `_AGENTES_SEM_MEDICAO_ADMITIDA`. A
   medicao do funil nao e' executavel (sem admissao) e `saida_clinica` e' um sinal de saude, que
   nao vira rotulo de metrica sem decisao do encarregado. Nenhum turno grava nada.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.comercial import graph as comercial_graph
from maezo.platform import observability
from maezo.runtime import turn_telemetry
from maezo.runtime.turn_telemetry import emit_turn_desfecho

_REF = "hk1_" + "a" * 64
_PASSAGEM = {
    "schema_version": "esf17-passagem-comercial.proposed.v1",
    "para": "comercial",
    "motivo": "intencao_contratar",
    "message_ref": _REF,
}


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(observability, "record_agent_desfecho", _record)
    return calls


def _turno(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "tenant-sintetico",
        "conversation_id": "conv-sintetica",
        "modo": "prospect",
        "passagem": dict(_PASSAGEM),
        "primeiro_turno": False,
        "sinal_saude": False,
        "pedido_humano": False,
    }
    base.update(extra)
    return base


_CASOS = [
    pytest.param(_turno(sinal_saude=True), "saida_clinica", "devolver_helena", id="saude"),
    pytest.param(
        _turno(pedido_humano=True),
        "encaminhado_especialista_a_pedido",
        "fila_humana_sales_operations",
        id="humano",
    ),
    pytest.param(_turno(primeiro_turno=True), "identificacao_enviada", "conversa_comercial", id="primeiro"),
    pytest.param(_turno(), "encaminhado_especialista", "fila_humana_sales_operations", id="encaminha"),
    pytest.param(
        {k: v for k, v in _turno(modo="staff_assist").items() if k != "passagem"},
        "nota_interna",
        "nota_interna_especialista",
        id="staff",
    ),
]


def test_conjunto_sem_medicao_admitida_e_exatamente_comercial() -> None:
    """Estado fechado: retirar o agente do conjunto (ou alargar) e' PR proprio, nunca deriva."""
    assert frozenset({"comercial"}) == turn_telemetry._AGENTES_SEM_MEDICAO_ADMITIDA


def test_vocabularios_do_comercial_cobrem_o_que_o_grafo_escreve() -> None:
    rotas = {
        comercial_graph.ROTA_SAIDA_CLINICA,
        comercial_graph.ROTA_FILA_HUMANA,
        comercial_graph.ROTA_CONVERSA,
        comercial_graph.ROTA_NOTA_INTERNA,
    }
    assert rotas == turn_telemetry._ROUTE_VOCAB["comercial"]


@pytest.mark.parametrize(("entrada", "desfecho", "rota"), _CASOS)
async def test_turno_comercial_chega_a_telemetria_e_nao_grava_nada(
    monkeypatch: pytest.MonkeyPatch, entrada: dict[str, object], desfecho: str, rota: str
) -> None:
    """Todo caminho do grafo passa por `finalize` -> `emit_turn_desfecho`; zero gravacao."""
    gravados = _spy(monkeypatch)
    entregues: list[dict[str, Any]] = []
    original = comercial_graph.emit_turn_desfecho

    def _espiao(state: Any, **kwargs: Any) -> None:
        entregues.append({"desfecho": state.get("desfecho"), **kwargs})
        original(state, **kwargs)

    monkeypatch.setattr(comercial_graph, "emit_turn_desfecho", _espiao)
    app = comercial_graph.build({"comercial_ligado": True}).compile()
    final = await app.ainvoke(entrada)

    assert final["desfecho"] == desfecho
    assert final["rota"] == rota
    assert entregues == [{"desfecho": desfecho, "agent_id": "comercial", "route": rota}]
    assert gravados == []


def test_emissao_direta_do_comercial_nao_grava(monkeypatch: pytest.MonkeyPatch) -> None:
    gravados = _spy(monkeypatch)
    emit_turn_desfecho({"desfecho": "saida_clinica"}, agent_id="comercial", route="devolver_helena")
    assert gravados == []


def test_sem_o_fecho_o_comercial_gravaria_rotulos_normalizados(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prova de que o fecho e' o unico motivo do zero: sem ele, o caminho grava, normalizado."""
    gravados = _spy(monkeypatch)
    monkeypatch.setattr(turn_telemetry, "_AGENTES_SEM_MEDICAO_ADMITIDA", frozenset())
    emit_turn_desfecho({"desfecho": "texto livre do prospect"}, agent_id="comercial", route="rota-inventada")
    assert len(gravados) == 1
    assert gravados[0]["agent_id"] == "comercial"
    assert gravados[0]["desfecho"] == "outro"
    assert gravados[0]["route"] == "outro"


def test_outros_agentes_continuam_gravando(monkeypatch: pytest.MonkeyPatch) -> None:
    gravados = _spy(monkeypatch)
    emit_turn_desfecho({"desfecho": "analise_humana"}, agent_id="carolina", route="human_review")
    assert len(gravados) == 1
