"""ASSUNTOS ABERTOS (Plano de ajustes da Helena v2, etapa 9; DL-0154): a lista no codigo, a partir dos
`topic_events` do roteador v7; a retomada "Sobre X, ficou alguma duvida?" no maximo UMA vez por assunto,
depois do Lucas ou no lugar da cortesia curta; nunca do assunto que acabou de ser tratado.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena import roteador_v7 as rv7
from maezo.agents.helena.graph import _HELENA_MEMORIA_DE_CONVERSA
from maezo.agents.helena.habilidades.conversa import assuntos as a
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_roteador_v7 import _ConversaAtiva
from tests.unit.agents.test_helena_saude_gerativa import _INFO, _texto


def _v7(**campos: Any) -> rv7.DecisaoV7:
    import json

    bruto = json.loads(decisao_v7(**campos))
    bruto["topic_events"] = campos.get("eventos", [])
    v7, ruins = rv7.ler_decisao(json.dumps(bruto))
    assert v7 is not None, ruins
    return v7


def test_a_habilidade_do_turno_abre_e_fica_por_ultimo() -> None:
    lista = a.depois_do_turno([], _v7(habilidade="saude"))
    lista = a.depois_do_turno(lista, _v7(habilidade="plano"))
    assert [i["t"] for i in lista] == ["saude", "plano"]
    lista = a.depois_do_turno(lista, _v7(habilidade="saude"))
    assert [i["t"] for i in lista] == ["plano", "saude"]


def test_fecha_tira_e_retoma_marca() -> None:
    lista = a.depois_do_turno([], _v7(habilidade="saude"))
    lista = a.depois_do_turno(
        lista, _v7(habilidade="plano", eventos=[{"evento": "fecha", "topico": "saude"}])
    )
    assert [i["t"] for i in lista] == ["plano"]
    lista = a.depois_do_turno(lista, _v7(habilidade="cobranca"))
    lista = a.depois_do_turno(
        lista, _v7(habilidade="conversa", eventos=[{"evento": "retoma", "topico": "plano"}])
    )
    assert next(i for i in lista if i["t"] == "plano")["r"] is True


def test_nunca_retoma_o_assunto_mais_recente_nem_o_ja_retomado() -> None:
    lista = a.depois_do_turno([], _v7(habilidade="saude"))
    assert a.a_retomar(lista, exceto=None) is None, "o assunto que acabou de ser tratado"
    lista = a.depois_do_turno(lista, _v7(habilidade="plano"))
    assert a.a_retomar(lista, exceto=None) == "saude"
    lista = a.marcar_retomado(lista, "saude")
    assert a.a_retomar(lista, exceto=None) is None, "no maximo uma vez"


@pytest.mark.parametrize("lixo", [None, "saude", [{"t": "jardinagem", "r": False}], [{"t": "saude"}]])
def test_a_lista_do_estado_e_validada(lixo: Any) -> None:
    assert a.validos(lixo) == []


@pytest.mark.parametrize("cortesia", ["ok, obrigada", "valeu", "beleza"])
async def test_cortesia_depois_de_outro_assunto_retoma_o_que_ficou_uma_vez(cortesia: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o que é colesterol alto?", decisao_v7("saude"), _INFO)
    await conversa.fala("e qual o telefone da central do plano?", decisao_v7("plano"), _INFO)
    retomada = await conversa.fala(cortesia, decisao_v7("conversa"))
    assert _texto(retomada) == "Sobre o assunto de saúde, ficou alguma dúvida?"
    de_novo = await conversa.fala(cortesia, decisao_v7("conversa"))
    assert _texto(de_novo) != "Sobre o assunto de saúde, ficou alguma dúvida?", "no maximo uma vez"


async def test_cortesia_logo_depois_do_mesmo_assunto_nao_retoma() -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o que é colesterol alto?", decisao_v7("saude"), _INFO)
    fim = await conversa.fala("ok, entendi", decisao_v7("conversa"))
    assert "ficou alguma dúvida" not in _texto(fim)


@pytest.mark.parametrize(
    "fala", ["e o boleto desse mês?", "me manda a segunda via", "quando vence a mensalidade?"]
)
async def test_passagem_ao_lucas_leva_a_retomada_do_assunto_de_saude(fala: str) -> None:
    from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada
    from tests.unit.agents.test_helena_roteador_ativo import _decisao, _grafo, _Modelo

    info = _classify(intent="information")
    modelo = _Modelo(
        [_decisao("saude"), _decisao("plano"), _decisao("cobranca")],
        [info, "", "", info, "", "", info],
    )
    grafo, _whatsapp, _cib, _fonte = _grafo(modelo)
    memoria: dict[str, Any] = {}
    for texto in ("o que é colesterol alto?", "e qual o telefone da central?"):
        r = await grafo.ainvoke(_entrada(texto) | memoria)
        memoria = {k: r[k] for k in _HELENA_MEMORIA_DE_CONVERSA if k in r}
    r3 = await grafo.ainvoke(_entrada(fala) | memoria)
    assert r3.get("handoff")
    assert r3.get("texto_de_retomada") == "Sobre o assunto de saúde, ficou alguma dúvida?"
