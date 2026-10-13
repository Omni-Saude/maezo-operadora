"""DL-0091 — rodada 4: a terceira bateria do simulador (13/10/2026, TINA ligada no dev).

Cada teste reproduz um trecho de transcricao com o modelo simulado:

  1. Despedida vence o aviso de caso aberto e a consulta do plano.
  2. Caso aberto: recado ao atendente reconhecido sem prometer que ele le' a conversa (o portal nao a
     recebe); depois de dois avisos, respostas curtas e diferentes.
  3. A Helena nao fala de pagamento (papel do Lucas) no texto do plano.
  4. Insistencia na cobertura de um procedimento: sem reenviar os dados do plano.
  5. "E' urgente" sobre cobertura nao poe o 192 na frente; sinal de saude poe.
"""

from __future__ import annotations

from typing import Any

from maezo.agents.helena.graph import (
    RESPOSTA_CASO_ABERTO_CURTO_1,
    RESPOSTA_CASO_ABERTO_CURTO_2,
    RESPOSTA_CASO_ABERTO_CURTO_3,
    RESPOSTA_CASO_ABERTO_PRAZO,
    RESPOSTA_CASO_ABERTO_RECADO,
    RESPOSTA_COBERTURA_ESPECIFICA,
    RESPOSTA_COBERTURA_ESPECIFICA_2,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DESPEDIDA_CASO_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    _com_emergencia_se_preciso,
)
from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _EMERGENCIA_192,
    _caso_aberto,
    _classify,
    _entrada,
    _Fonte,
    _grafo,
    _starts,
)

# =================================================================================================
# 1. Despedida vence
# =================================================================================================


async def test_despedida_com_espera_de_atendente_nao_vira_aviso_de_caso_aberto() -> None:
    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=cib)

    resultado = await grafo.ainvoke(
        _entrada(
            "beleza, então vou esperar o atendente. mas valeu pela ajuda, tchau!",
            [RESPOSTA_HANDOFF_RECUSADA],
            pedido_humano_lexico=True,
        )
    )

    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA_CASO_ABERTO]
    assert not resultado.get("escalation_started")
    assert _starts(cib) == antes


async def test_valeu_no_fim_de_conversa_mista_nao_manda_a_lista_de_carencias() -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="consulta_plano", consulta_subtipo="carencia")], fonte=fonte
    )

    await grafo.ainvoke(_entrada("Valeu aí", [MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO]))

    assert fonte.chamadas == []
    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA]


# =================================================================================================
# 2. Caso aberto: recado e avisos curtos
# =================================================================================================


async def test_recado_ao_atendente_e_reconhecido_sem_prometer_que_ele_le_a_conversa() -> None:
    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=cib)

    await grafo.ainvoke(
        _entrada("avisa a pessoa que é pra confirmar parto e fisioterapia", [RESPOSTA_HANDOFF_RECUSADA])
    )

    assert whatsapp.enviados == [RESPOSTA_CASO_ABERTO_RECADO]
    texto = whatsapp.enviados[0].lower()
    for promessa in ("vai ler", "registrad", "anotad", "já está com o seu pedido"):
        assert promessa not in texto
    assert _starts(cib) == antes


async def test_depois_de_dois_avisos_a_resposta_e_curta_e_diferente() -> None:
    falas = [RESPOSTA_HANDOFF_RECUSADA, RESPOSTA_CASO_ABERTO_PRAZO, f"Entendi. {RESPOSTA_CASO_ABERTO_PRAZO}"]
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=_caso_aberto("solicitacao_humano"))

    await grafo.ainvoke(_entrada("e aí, quanto tempo ainda vai demorar?", falas))

    enviado = whatsapp.enviados[0]
    assert enviado in (
        RESPOSTA_CASO_ABERTO_CURTO_1,
        RESPOSTA_CASO_ABERTO_CURTO_2,
        RESPOSTA_CASO_ABERTO_CURTO_3,
    )
    assert enviado not in falas
    assert len(enviado) < 70


# =================================================================================================
# 3. Sem frase de pagamento no texto do plano
# =================================================================================================


async def test_texto_do_plano_nao_fala_de_pagamento() -> None:
    rascunho = (
        "Pelo cadastro, seu plano é o PLANO SINTETICO ENFERMARIA, com segmentação Ambulatorial + Hospitalar. "
        "Sobre o pagamento, não consta no cadastro se é por empresa ou particular."
    )
    grafo, whatsapp, _, _ = _grafo([_classify(), rascunho])

    await grafo.ainvoke(_entrada("qual o meu plano?"))

    enviado = whatsapp.enviados[0]
    assert "PLANO SINTETICO ENFERMARIA" in enviado
    assert "pagamento" not in enviado.lower() and "particular" not in enviado.lower()


# =================================================================================================
# 4. Insistencia na cobertura
# =================================================================================================


async def test_insistencia_na_cobertura_nao_reenvia_os_dados_do_plano() -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="consulta_plano", consulta_subtipo="carteirinha")], fonte=fonte
    )

    await grafo.ainvoke(_entrada("mas cobre dermatologia ou não?", [MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO]))

    assert fonte.chamadas == []
    assert whatsapp.enviados == [RESPOSTA_COBERTURA_ESPECIFICA]
    assert "não consigo confirmar cobertura" in whatsapp.enviados[0]
    assert "não consta" not in whatsapp.enviados[0]


async def test_segunda_insistencia_muda_a_redacao() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="consulta_plano", consulta_subtipo="elegibilidade")])

    await grafo.ainvoke(
        _entrada(
            "e dermatologia, cobre?",
            [MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO, RESPOSTA_COBERTURA_ESPECIFICA],
        )
    )

    assert whatsapp.enviados == [RESPOSTA_COBERTURA_ESPECIFICA_2]


# =================================================================================================
# 5. 192 na frente so' com sinal de saude
# =================================================================================================


async def test_e_urgente_sobre_cobertura_responde_a_cobertura_e_192_no_fim() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify()])

    await grafo.ainvoke(_entrada("cobre parto? to grávida, é urgente"))

    enviado = whatsapp.enviados[0]
    assert not enviado.startswith("Se for uma emergência")
    assert enviado.endswith(_EMERGENCIA_192)
    assert "Ambulatorial + Hospitalar" in enviado


def test_com_sinal_de_saude_o_192_vai_na_frente() -> None:
    estado: dict[str, Any] = {
        "message_body": "cobre parto? estou com sangramento",
        "sinal_saude_lexico": True,
    }
    assert _com_emergencia_se_preciso(estado, "Texto.").startswith(_EMERGENCIA_192)
    sem_saude: dict[str, Any] = {"message_body": "cobre parto? to grávida, é urgente"}
    assert _com_emergencia_se_preciso(sem_saude, "Texto.") == f"Texto. {_EMERGENCIA_192}"
