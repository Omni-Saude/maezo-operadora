"""Falas curtas logo depois do Lucas (avaliacao independente r5b de 25/10/2026, itens D e E2).

D: com o Lucas com a conversa, "ok, entendi" foi lido pelo roteador como `lucas_cobranca` e o Lucas repetiu a
resposta INTEIRA da mensalidade, com nova consulta (26 s). Agora o fechamento recebe a resposta curta, sem
modelo, sem consulta e sem passar ao Lucas.

E2: logo depois do pedido de cancelamento (a conversa voltou para a Helena: `lucas_encerrou`), "qual meu
protocolo mesmo?" foi para a habilidade `pedidos`, o classificador disse `information` e a cerca D7 trocou o
rascunho pelo texto generico. Agora a pergunta do protocolo vai ao Lucas como `cancelamento` + foco
`protocolo` (ele le' a instancia viva do pedido: `test_lucas_protocolo_do_pedido.py`).
"""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.graph import (
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DESPEDIDA_CASO_ABERTO,
)
from maezo.agents.helena.habilidades.conversa import leitura_curta as lc
from maezo.agents.helena.habilidades.conversa.depois_do_lucas import e_fechamento, e_pergunta_do_protocolo
from maezo.domain.conversa.cobranca_foco import FOCO_PROTOCOLO
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _grafo
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _Modelo
from tests.unit.agents.test_helena_roteador_ativo import _grafo as _grafo_ativo

_FECHAMENTOS = ["ok", "ok, entendi", "beleza", "valeu", "certo", "entendi", "👍", "Ok, entendi. Obrigada!"]
_PROTOCOLOS = [
    "qual meu protocolo mesmo?",
    "me passa o protocolo de novo",
    "esqueci o número do protocolo",
    "e o protocolo?",
    "qual o protocolo do meu pedido de cancelamento?",
    "não anotei o protocolo, pode repetir?",
]


#: As respostas curtas de cortesia que ja' existem (a da leitura curta e a despedida do `inform`).
_CURTAS = (lc.TEXTO_COMBINADO, lc.TEXTO_DE_NADA, RESPOSTA_DESPEDIDA, RESPOSTA_DESPEDIDA_CASO_ABERTO)


def _eventos(logs: list[dict[str, Any]], nome: str) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == nome]


# ---------------------------------------------------------------------------------------------------------
# D: fechamento depois do Lucas
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mensagem", _FECHAMENTOS)
async def test_fechamento_com_o_lucas_ativo_e_resposta_curta_sem_modelo_e_sem_passar(mensagem: str) -> None:
    grafo, whatsapp, modelo, _ = _grafo([_classify(intent="cobranca", cobranca_subtipo="consulta_valores")])
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))
    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF
    assert not resultado.get("handoff")
    assert len(whatsapp.enviados) == 1 and whatsapp.enviados[0] in _CURTAS
    assert "R$" not in whatsapp.enviados[0]
    assert modelo.prompts == [], "o fechamento nunca chama o modelo"
    assert _eventos(logs, "helena_fechamento_depois_do_lucas")


@pytest.mark.parametrize("mensagem", ["ok, entendi", "beleza", "👍"])
async def test_roteador_ativo_lendo_cobranca_nao_repete_o_lucas(mensagem: str) -> None:
    """O caso medido: o roteador leu `lucas_cobranca` para o fechamento. Ele nem e' chamado agora."""
    modelo = _Modelo(
        [_decisao("cobranca")], [_classify(intent="cobranca", cobranca_subtipo="consulta_valores")]
    )
    grafo, whatsapp, _cib, fonte = _grafo_ativo(modelo)
    resultado = await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))
    assert not resultado.get("handoff")
    assert whatsapp.enviados[-1] in _CURTAS
    assert modelo.roteador, "o modelo do roteador nao foi chamado"
    assert modelo.outras == 0 and fonte.pedidos == []


@pytest.mark.parametrize(
    "mensagem",
    ["sim", "certo?", "ok, e o boleto de novembro?", "ok, quanto foi a coparticipação de setembro?"],
)
async def test_controle_nao_e_fechamento_segue_o_caminho_de_sempre(mensagem: str) -> None:
    grafo, _whatsapp, _modelo, _ = _grafo([_classify(intent="cobranca", cobranca_subtipo="consulta_valores")])
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))
    assert not _eventos(logs, "helena_fechamento_depois_do_lucas")


async def test_controle_sem_o_lucas_ativo_o_fechamento_nao_e_deste_portao() -> None:
    grafo, _whatsapp, _modelo, _ = _grafo([_classify(intent="greeting")])
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("ok, entendi"))
    assert not _eventos(logs, "helena_fechamento_depois_do_lucas")


async def test_controle_com_sinal_de_saude_nao_e_fechamento() -> None:
    grafo, _whatsapp, _modelo, _ = _grafo([_classify(intent="information")])
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("ok", agente_ativo_conversa="lucas", sinal_saude_lexico=True))
    assert not _eventos(logs, "helena_fechamento_depois_do_lucas")


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [(t, True) for t in _FECHAMENTOS]
    + [("sim", False), ("ok?", False), ("tudo bem?", False), ("ok, e o boleto?", False), ("", False)],
)
def test_e_fechamento(texto: str, esperado: bool) -> None:
    assert e_fechamento(texto) is esperado


# ---------------------------------------------------------------------------------------------------------
# E2: a pergunta do protocolo do pedido
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mensagem", _PROTOCOLOS)
async def test_pergunta_do_protocolo_vai_ao_lucas_como_cancelamento_com_foco_protocolo(mensagem: str) -> None:
    grafo, whatsapp, modelo, _ = _grafo([_classify(intent="information")])
    resultado = await grafo.ainvoke(_entrada(mensagem))
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == "cancelamento"
    assert resultado["handoff"]["cobranca_foco"] == FOCO_PROTOCOLO
    assert resultado["handoff"]["competencia"] is None
    assert modelo.prompts == [], "nenhum modelo decide nem redige o protocolo"
    assert not any("CAN-" in t for t in whatsapp.enviados), "a Helena nunca escreve um protocolo"


@pytest.mark.parametrize("mensagem", ["qual meu protocolo mesmo?", "e o protocolo?"])
async def test_pergunta_do_protocolo_com_o_lucas_ativo_tambem_vai_ao_lucas(mensagem: str) -> None:
    grafo, whatsapp, _modelo, _ = _grafo([])
    resultado = await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))
    assert resultado["handoff"]["cobranca_foco"] == FOCO_PROTOCOLO
    assert whatsapp.enviados == [] or "Lucas" in whatsapp.enviados[0]


async def test_roteador_ativo_lendo_pedidos_passa_o_protocolo_ao_lucas() -> None:
    """O caso medido: roteador `pedidos`, classificador `information`, texto generico. Agora: o Lucas."""
    modelo = _Modelo([_decisao("pedidos")], [_classify(intent="information")])
    grafo, whatsapp, _cib, _fonte = _grafo_ativo(modelo)
    resultado = await grafo.ainvoke(_entrada("qual meu protocolo mesmo?"))
    assert resultado["handoff"]["cobranca_subtipo"] == "cancelamento"
    assert resultado["handoff"]["cobranca_foco"] == FOCO_PROTOCOLO
    assert not any("mensalidade ou pagamentos" in t for t in whatsapp.enviados)
    assert modelo.outras == 0


@pytest.mark.parametrize(
    "mensagem",
    [
        "qual o protocolo de tratamento da dengue?",
        "o protocolo de manchester é usado no pronto-socorro?",
        "quero cancelar meu plano",
    ],
)
async def test_controle_protocolo_clinico_ou_pedido_novo_nao_e_a_pergunta_do_protocolo(mensagem: str) -> None:
    grafo, _whatsapp, _modelo, _ = _grafo([_classify(intent="information")])
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(mensagem))
    assert not _eventos(logs, "helena_protocolo_ao_lucas")
    assert (resultado.get("handoff") or {}).get("cobranca_foco") != FOCO_PROTOCOLO


async def test_controle_protocolo_com_pedido_de_pessoa_nao_vai_ao_lucas() -> None:
    grafo, _whatsapp, _modelo, _ = _grafo([_classify(intent="human_request")])
    with capture_logs() as logs:
        await grafo.ainvoke(
            _entrada("qual meu protocolo? quero falar com um atendente", pedido_humano_lexico=True)
        )
    assert not _eventos(logs, "helena_protocolo_ao_lucas")


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [(t, True) for t in _PROTOCOLOS]
    + [
        ("qual o protocolo de tratamento da dengue?", False),
        ("protocolo de manchester", False),
        ("quero cancelar meu plano", False),
        ("qual o valor da mensalidade?", False),
        (None, False),
    ],
)
def test_e_pergunta_do_protocolo(texto: object, esperado: bool) -> None:
    assert e_pergunta_do_protocolo(texto) is esperado


@pytest.mark.parametrize("mensagem", ["qual meu protocolo mesmo?", "esqueci o número do protocolo"])
async def test_a_passagem_do_protocolo_e_aceita_pelo_turno_do_lucas_e_chega_com_o_foco(mensagem: str) -> None:
    """A passagem que a Helena escreve passa na fronteira do `LucasTurno` e chega ao Lucas com o foco."""
    from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_intencao
    from maezo.platform.webhooks.whatsapp.lucas_turno import validar_handoff

    grafo, _whatsapp, _modelo, _ = _grafo([])
    resultado = await grafo.ainvoke(_entrada(mensagem))
    handoff = _handoff_com_intencao(mensagem, resultado["handoff"], "cancelamento")
    assert "cobranca_confirmacao" not in handoff, "a pergunta do protocolo nunca confirma um pedido"
    validado = validar_handoff(handoff)
    assert validado.cobranca_subtipo == "cancelamento" and validado.cobranca_foco == FOCO_PROTOCOLO
