"""Simulador de beneficiario no dev (rodada 5, 13/10/2026; bateria 4, 60 conversas).

Contra o grafo REAL do Lucas, a DMN DRAFT e o motor em memoria, com a memoria curta do roteador passando de
um turno ao outro (o `_Conversa` da rodada 4).

  1. "foi cobrado duas vezes no mes passado", repetido 8 vezes: o Lucas alternava dois reconhecimentos.
  2. Contrato de empresa: a frase da empresa em toda resposta de coparticipacao, e o texto da empresa
     respondendo "paguei a consulta direto ao medico".
  3. "manda o extrato detalhado": a mesma resposta repetida.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO,
    NAO_CONSIGO,
    TEXTO_EXTRATO,
    TEXTO_EXTRATO_CURTO,
    TEXTO_PAGADOR_EMPRESA,
    TEXTO_PAGAMENTO_AO_PRESTADOR,
    TEXTO_PAGAMENTO_AO_PRESTADOR_CURTO,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_mes_do_texto
from maezo.runtime.cobranca_foco import decidir_foco_da_cobranca
from tests.unit.agents.test_lucas_simulador_rodada4 import _EM_DIA, _EMPRESA, _FECHO, _Conversa


def _sem_repeticao(textos: list[str]) -> None:
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos


# --- 1. Reclamacao repetida com o caso aberto ---------------------------------------------------------


async def test_reclamacao_da_os_fatos_do_mes_uma_vez_e_depois_diz_que_nao_confirma() -> None:
    conversa = _Conversa(_EM_DIA)
    reclamacao: dict[str, Any] = {
        "tipo": "",
        "contesta_cobranca": True,
        "competencia": "2026-09",
        "cobranca_reclamacao": "em_dobro",
    }
    _, primeiro = await conversa.mensagem(**reclamacao)
    assert primeiro == ACK_ESCALACAO
    final, com_fatos = await conversa.mensagem(**reclamacao)
    assert com_fatos == (
        "Sua reclamação sobre a cobrança em dobro de setembro de 2026 já está com a nossa equipe. No que eu "
        "vejo aqui, setembro de 2026 tem uma mensalidade de R$ 966,52, paga em 14/09. Eu não consigo "
        "confirmar se houve cobrança em dobro. Quem confirma é a nossa equipe, por aqui. Fique tranquilo: "
        "nenhuma decisão sobre o seu plano foi tomada."
    )
    fatos = {"competencias": final["competencias_cobranca"], "dados_de": "2026-10-12"}
    assert motivo_de_recusa(com_fatos, "ack_escalacao", fatos) is None
    seguintes = [(await conversa.mensagem(**reclamacao))[1] for _ in range(6)]
    for texto in seguintes:
        assert "No que eu vejo" not in texto, "os fatos do mes saem UMA vez"
        assert "não consigo confirmar" in texto or "consegue confirmar" in texto
        assert "outra dúvida" not in texto and "prazo" not in texto
        assert len(texto) < len(com_fatos) / 2
    assert len(set(seguintes)) == 2
    _sem_repeticao(conversa.textos)


async def test_reclamacao_de_empresa_nao_mostra_o_valor_da_fatura_da_empresa() -> None:
    conversa = _Conversa(_EMPRESA)
    reclamacao: dict[str, Any] = {
        "tipo": "",
        "contesta_cobranca": True,
        "competencia": "2026-07",
        "cobranca_reclamacao": "valor_errado",
    }
    await conversa.mensagem(**reclamacao)
    _, texto = await conversa.mensagem(**reclamacao)
    assert "R$" not in texto and "Eu não consigo confirmar se o valor está errado." in texto


# --- 2. Coparticipacao e pagamento direto ao prestador ------------------------------------------------


@pytest.mark.parametrize(
    "pergunta",
    [
        "e o mês de setembro que fui no médico dia 15? paguei a consulta",
        "paguei R$ 45 direto ao médico e não aparece",
    ],
)
def test_pagamento_ao_prestador_e_coparticipacao_e_vai_a_consulta_de_valores(pergunta: str) -> None:
    assert decidir_foco_da_cobranca(pergunta) == "pagamento_ao_prestador"
    handoff = {
        "cobranca_subtipo": "confirmacao_pagamento",
        "competencia": None,
        "cobranca_foco": "pagamento_ao_prestador",
    }
    assert _handoff_com_mes_do_texto(pergunta, handoff)["cobranca_subtipo"] == "consulta_valores"


async def test_coparticipacao_da_empresa_e_so_o_fato_e_o_prestador_nao_vira_texto_da_empresa() -> None:
    conversa = _Conversa(_EMPRESA)
    _, copart = await conversa.mensagem(competencia="2026-07", cobranca_foco="coparticipacao")
    assert copart == f"Em julho de 2026 sua coparticipação foi de R$ 35,20. {_FECHO}"
    assert TEXTO_PAGADOR_EMPRESA not in copart
    prestador = {"competencia": "2026-07", "cobranca_foco": "pagamento_ao_prestador"}
    final, texto = await conversa.mensagem(**prestador)
    assert texto == f"Em julho de 2026 sua coparticipação foi de R$ 35,20. {TEXTO_PAGAMENTO_AO_PRESTADOR}"
    assert "empresa" not in texto
    assert motivo_de_recusa(texto, "mensagem", final["mensagem"]["fatos"]) is None
    _, de_novo = await conversa.mensagem(**prestador)
    assert de_novo == TEXTO_PAGAMENTO_AO_PRESTADOR_CURTO
    _, mais_uma = await conversa.mensagem(**prestador)
    assert mais_uma in NAO_CONSIGO
    _sem_repeticao(conversa.textos)


# --- 3. Extrato detalhado -----------------------------------------------------------------------------


def test_extrato_vai_a_resposta_fixa() -> None:
    assert decidir_foco_da_cobranca("manda o extrato detalhado") == "extrato"
    handoff = {"cobranca_subtipo": "outro", "competencia": None, "cobranca_foco": "extrato"}
    assert (
        _handoff_com_mes_do_texto("manda o extrato detalhado", handoff)["cobranca_subtipo"] == "boleto_2via"
    )


async def test_extrato_diz_onde_sai_oferece_pessoa_uma_vez_e_nao_repete() -> None:
    conversa = _Conversa(_EM_DIA)
    textos = [(await conversa.mensagem(tipo="2a_via", cobranca_foco="extrato"))[1] for _ in range(5)]
    assert textos[0] == TEXTO_EXTRATO
    assert textos[1] == TEXTO_EXTRATO_CURTO
    assert textos[2:] == [NAO_CONSIGO[0], NAO_CONSIGO[1], NAO_CONSIGO[0]]
    assert sum("nossa equipe" in t for t in textos) == 1
    _sem_repeticao(textos)
