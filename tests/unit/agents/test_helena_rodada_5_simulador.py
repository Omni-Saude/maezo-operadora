"""DL-0092 — rodada 5: bateria 4 do simulador (60 conversas), D11 (cobranca respondida pelo Lucas) em 0,50.

As causas, medidas nos `checks.jsonl`/transcricoes da rodada e no log do receptor:

  * cobranca com caso aberto recebia o aviso de caso aberto da Helena;
  * cobranca com pedido de pessoa ("me passa pro RH... o boleto") ficava com a Helena ou escalava;
  * "a mensalidade ta em dia?" virava consulta do plano (o modelo devolveu `consulta_plano`);
  * `invalid_consulta_subtipo` do classificador virava `falha_tecnica` (cinco vezes no log);
  * D8: o texto acrescentado a resposta do plano entrava com quebra de linha logo depois do ponto, e a
    regua do simulador lia as duas frases como uma so'.
"""

from __future__ import annotations

from maezo.agents.helena.graph import (
    FRASE_PASSAGEM_COBRANCA,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_HANDOFF_RECUSADA,
    _validate_extraction,
)
from maezo.qa.simulador.checagens import frases_longas
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _caso_aberto,
    _classify,
    _entrada,
    _Fonte,
    _grafo,
    _starts,
)


async def test_cobranca_com_caso_aberto_vai_ao_lucas_e_nao_recebe_aviso_de_caso() -> None:
    """Lucas ativo, caso de cobranca aberto: a mensagem de cobranca vai ao Lucas."""
    cib = _caso_aberto("cobranca")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=cib)

    resultado = await grafo.ainvoke(
        _entrada(
            "QUERO SABER EXATAMENTE O QUE ACONTECEU COM ESSE PAGAMENTO DO MÊS PASSADO",
            [RESPOSTA_HANDOFF_RECUSADA],
            agente_ativo_conversa="lucas",
        )
    )

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert whatsapp.enviados == [], "com o Lucas ativo, quem responde e' o Lucas"
    assert not resultado.get("escalation_started")
    assert _starts(cib) == antes


async def test_rh_com_boleto_vai_ao_lucas_mesmo_com_o_lexico_de_pessoa() -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="human_request")])

    resultado = await grafo.ainvoke(
        _entrada(
            "Então me passa pro RH de vocês AGORA, porque o boleto sempre veio pra mim e preciso pagar hoje!",
            pedido_humano_lexico=True,
        )
    )

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado.get("escalation_motivo") is None
    assert whatsapp.enviados == [FRASE_PASSAGEM_COBRANCA]
    assert _starts(cib) == 0


async def test_rh_sem_cobranca_nao_escala() -> None:
    grafo, _, _, cib = _grafo([_classify(intent="human_request"), "Posso ajudar com outra coisa?"])

    resultado = await grafo.ainvoke(_entrada("me passa pro RH"))

    assert not resultado.get("escalation_started")
    assert _starts(cib) == 0


async def test_pedido_de_atendente_com_boleto_vai_ao_lucas() -> None:
    grafo, _, _, cib = _grafo([_classify(intent="human_request")])

    resultado = await grafo.ainvoke(
        _entrada(
            "Pode me passar pro atendente humano então? Só preciso do boleto de outubro",
            pedido_humano_lexico=True,
        )
    )

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert _starts(cib) == 0


async def test_mensalidade_em_dia_classificada_como_consulta_vai_ao_lucas() -> None:
    fonte = _Fonte()
    grafo, _, _, _ = _grafo(
        [_classify(intent="consulta_plano", consulta_subtipo="elegibilidade")], fonte=fonte
    )

    resultado = await grafo.ainvoke(_entrada("beleza! queria saber se a mensalidade ta em dia"))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert fonte.chamadas == []


async def test_consulta_subtipo_junto_de_cobranca_nao_vira_falha_tecnica() -> None:
    grafo, _, _, cib = _grafo(
        [_classify(intent="cobranca", cobranca_subtipo="vencimento", consulta_subtipo="elegibilidade")]
    )

    resultado = await grafo.ainvoke(
        _entrada("Primeiro, quando vence o boleto desse mês? E o dermatologista, cobre?")
    )

    assert resultado.get("escalation_motivo") != "falha_tecnica"
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == "vencimento"
    assert _starts(cib) == 0


def test_consulta_subtipo_e_normalizado_no_validador() -> None:
    import json

    dentro = json.loads(_classify(intent="consulta_plano", consulta_subtipo="cobertura"))
    assert _validate_extraction(dentro, cobranca_habilitada=True, consultas_habilitadas=True) is None
    assert dentro["consulta_subtipo"] == "elegibilidade"
    fora = json.loads(
        _classify(intent="cobranca", cobranca_subtipo="vencimento", consulta_subtipo="carencia")
    )
    assert _validate_extraction(fora, cobranca_habilitada=True, consultas_habilitadas=True) is None
    assert fora["consulta_subtipo"] is None


async def test_resposta_do_plano_com_cobertura_passa_na_regua_d8_do_simulador() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify()])

    await grafo.ainvoke(_entrada("o plano cobre dermatologista?"))

    enviado = whatsapp.enviados[0]
    assert frases_longas(enviado) == [], enviado
