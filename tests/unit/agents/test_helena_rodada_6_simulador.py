"""DL-0093 — rodada 6: bateria 5 do simulador (60 conversas, 13/10/2026).

1. Falha passageira do provedor/classificador: pede o reenvio e NAO escala; a segunda falha seguida
   escala; com sinal de saude escala como sempre (com o 192).
2. Caso aberto + pedido de pessoa com cobranca: o Lucas ja' reconhece — a Helena nao acrescenta o aviso;
   "ninguem veio me ligar / to esperando" nao e' pedido novo.
3. "Passa pra equipe AGORA" com caso aberto: o caso ja' esta' com a equipe e o atendente responde aqui;
   nunca "vou levar isso para a equipe" nem "Sou Helena" no meio da conversa.
"""

from __future__ import annotations

from maezo.agents.helena.graph import (
    RESPOSTA_HANDOFF_EQUIPE_EMERGENCIA,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_JA_ABERTO_COBRANCA,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_INFORM_CASO_ABERTO,
    RESPOSTA_PECA_REENVIO,
    eh_pedido_explicito_de_pessoa,
)
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _caso_aberto,
    _classify,
    _entrada,
    _grafo,
    _starts,
)
from tests.unit.platform.webhooks.whatsapp.test_dispatch_cobranca import _classify as _classify_cobranca
from tests.unit.platform.webhooks.whatsapp.test_dispatch_cobranca import _montar, _msg

# =================================================================================================
# 1. Falha do classificador
# =================================================================================================


async def test_primeira_falha_do_classificador_pede_reenvio_e_nao_escala() -> None:
    grafo, whatsapp, _, cib = _grafo(["isto nao e json", "nem isto"])

    resultado = await grafo.ainvoke(_entrada("qual o horário da central?"))

    assert whatsapp.enviados == [RESPOSTA_PECA_REENVIO]
    assert not resultado.get("escalation_started")
    assert resultado.get("escalation_motivo") is None
    assert _starts(cib) == 0


async def test_segunda_falha_seguida_escala_falha_tecnica() -> None:
    grafo, whatsapp, _, cib = _grafo(["isto nao e json", "nem isto"])

    resultado = await grafo.ainvoke(_entrada("qual o horário da central?", [RESPOSTA_PECA_REENVIO]))

    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert resultado["escalation_started"] is True
    assert whatsapp.enviados == [RESPOSTA_HANDOFF_RECUSADA]
    assert _starts(cib) == 1


async def test_falha_com_sinal_de_saude_escala_como_sempre_com_192() -> None:
    grafo, whatsapp, _, _ = _grafo(["isto nao e json", "nem isto"])

    resultado = await grafo.ainvoke(_entrada("estou com dor no peito e falta de ar", sinal_saude_lexico=True))

    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert whatsapp.enviados == [RESPOSTA_HANDOFF_EQUIPE_EMERGENCIA]
    assert "192" in whatsapp.enviados[0]


# =================================================================================================
# 2. Caso aberto + cobranca + pedido de pessoa: sem aviso duplicado
# =================================================================================================


def test_queixa_de_espera_nao_e_pedido_novo_de_pessoa() -> None:
    assert eh_pedido_explicito_de_pessoa("to aqui esperando desde cedo e ninguém veio me ligar") is False
    assert eh_pedido_explicito_de_pessoa("estou esperando ainda, ninguém veio me ajudar") is False
    assert eh_pedido_explicito_de_pessoa("ninguém veio me ajudar, quero falar com um atendente") is True


async def test_com_caso_aberto_o_pedido_com_cobranca_nao_ganha_o_aviso_da_helena() -> None:
    dispatcher, cliente, _, espiao, _ = _montar([_classify_cobranca(), _classify_cobranca()])

    primeiro = await dispatcher.dispatch(_msg("quero falar com um atendente sobre o boleto", "wamid.R6-1"))
    assert primeiro["escalation_started"] is True  # o caso nasce no primeiro pedido (DL-0092)
    antes = len(cliente.textos)

    segundo = await dispatcher.dispatch(
        _msg("quero falar com um atendente sobre o boleto, ainda nao recebi", "wamid.R6-2")
    )

    novos = cliente.textos[antes:]
    assert espiao is not None and len(espiao.handoffs) == 2, "o Lucas respondeu os dois turnos"
    assert RESPOSTA_HANDOFF_JA_ABERTO not in novos
    assert RESPOSTA_HANDOFF_RECUSADA not in novos
    assert not segundo.get("escalation_started")


# =================================================================================================
# 3. "Passa pra equipe AGORA" com caso aberto
# =================================================================================================


async def test_passa_pra_equipe_com_caso_aberto_diz_que_o_caso_ja_esta_com_a_equipe() -> None:
    cib = _caso_aberto("cobranca")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=cib)

    await grafo.ainvoke(
        _entrada("Passa pra equipe AGORA", ["Vou te passar para o Lucas, nosso assistente financeiro."])
    )

    assert whatsapp.enviados == [RESPOSTA_HANDOFF_JA_ABERTO_COBRANCA]
    texto = whatsapp.enviados[0].lower()
    assert "vou levar" not in texto and "sou helena" not in texto
    assert _starts(cib) == antes, "o caso aberto e' reaproveitado"


async def test_promessa_de_repasse_do_modelo_e_trocada_e_sem_apresentacao_no_meio() -> None:
    rascunho = (
        "Sou Helena. Entendi que você quer enviar o comprovante agora. "
        "Vou levar isso para a equipe financeira."
    )
    # DL-0116: "o caso continua" so' com o caso ATIVO no motor (o historico sozinho e' so' indicio).
    grafo, whatsapp, _, _ = _grafo([_classify(), rascunho], cib=_caso_aberto("cobranca"))

    await grafo.ainvoke(_entrada("posso mandar o print aqui?", [RESPOSTA_HANDOFF_RECUSADA]))

    assert whatsapp.enviados == [RESPOSTA_INFORM_CASO_ABERTO]
