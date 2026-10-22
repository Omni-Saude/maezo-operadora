"""Recusa depois da oferta/abertura do caso ENCERRA o caso P2/P3 (avaliacao r4 de 22/10/2026, regra do dono).

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import pytest

import tests.unit.agents.test_helena_saude_gerativa as sg_tests
from maezo.agents.helena import cobertura_assistida as ca
from tests.unit.agents.test_helena_base_saude import _classify, _Motor


def _vivos(motor: _Motor) -> int:
    return sum(1 for linhas in motor._history.values() for i in linhas if i.state == "ACTIVE")


async def _com_caso_da_equipe() -> sg_tests._Conversa:
    conversa = sg_tests._Conversa()
    conversa.cib = _Motor()
    await conversa.diz(
        "estou com dor no joelho faz uns dias", _classify(intent="symptom", population="adult")
    )
    await conversa.diz("não", _classify(intent="symptom", population="adult"))
    await conversa.diz("2", _classify(intent="symptom", population="adult"))
    assert _vivos(conversa.cib) == 1, "o caso da equipe de saude ficou aberto"
    return conversa


@pytest.mark.parametrize(
    "recusa",
    [
        "não quero falar com ninguém",
        "não, deixa, desisto",
        "pode cancelar, já resolvi",
        "esquece, não precisa mais",
        "não precisa me encaminhar não",
    ],
)
async def test_recusa_depois_do_caso_aberto_encerra_o_caso(recusa: str) -> None:
    conversa = await _com_caso_da_equipe()
    resultado = await conversa.diz(recusa, _classify(intent="information"))
    assert _vivos(conversa.cib) == 0
    assert "encerrei" in str(resultado.get("response_text") or "")


async def test_desistencia_sem_caso_aberto_nao_fala_de_encerrar_caso() -> None:
    conversa = sg_tests._Conversa()
    conversa.cib = _Motor()
    resultado = await conversa.diz("deixa pra lá, desisto", _classify(intent="information"))
    assert "encerrei" not in str(resultado.get("response_text") or "")
    assert conversa.cib.inicios == []


async def test_caso_grave_nao_e_encerrado_pela_recusa() -> None:
    from maezo.agents.helena.graph import chave_do_escalonamento

    conversa = sg_tests._Conversa()
    conversa.cib = _Motor()
    conversa.cib.abrir(
        chave_do_escalonamento("amh", conversa.conversa),
        motivo_categoria="red_flag_clinico",
        severidade="grave",
    )
    await conversa.diz("não, deixa, desisto", _classify(intent="information"))
    assert _vivos(conversa.cib) == 1, "caso grave (192) nunca e' encerrado pela recusa"


@pytest.mark.parametrize("fala", ["pode cancelar", "já resolvi, obrigado", "não precisa me ligar"])
def test_leitor_de_desistencia_reconhece_as_formas_gerais(fala: str) -> None:
    assert ca.ler_resposta(fala) == "encerrar"
