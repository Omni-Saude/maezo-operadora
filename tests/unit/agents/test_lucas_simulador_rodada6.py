"""Simulador de beneficiario no dev (rodada 6, 13/10/2026; bateria 5, 60 conversas: D11 0,72, D6 0,68).

Contra o grafo REAL do Lucas, a DMN DRAFT e o motor em memoria, com a memoria curta do roteador passando de
um turno ao outro (o `_Conversa` da rodada 4). Hoje e' 13/10/2026 (Brasilia).

  1. "Valor ultima mensalidade e vencimento" com outubro sem pagamento: a escalacao nao dava valor nem data;
     "Qual valor e data exata?" recebeu setembro.
  2. "Hoje e' 13/10, vence 15/10, como assim ainda nao foi paga?" -> "E' outubro de 2026: vence em 15/10."
  3. "Como faco pra pagar depois do vencimento?" -> uma frase pronta da escada.
  4. "Ja' paguei dia 06. Comprovante?" -> "sem pagamento confirmado" de novo, depois a escada.
  5. A escada de repeticao respondendo pergunta NOVA, so' porque o texto saia igual.
"""

from __future__ import annotations

from typing import Any

from maezo.agents.lucas.graph import NAO_CONSIGO, TEXTO_COMO_PAGAR, TEXTO_COMO_PAGAR_CURTO
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_mes_do_texto
from maezo.runtime.cobranca_foco import decidir_foco_da_cobranca
from tests.unit.agents.test_lucas_simulador_rodada4 import _FECHO, _Conversa


def _mes(competencia: str, situacao: str, vencimento: str, **extra: str) -> dict[str, str]:
    return {
        "competencia": competencia,
        "situacao": situacao,
        "vencimento": vencimento,
        "valor_total": "552.54",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00" if situacao == "paga" else "552.54",
        "boleto": "****0042",
        **extra,
    }


_SETEMBRO_PAGO = _mes("2026-09", "paga", "2026-09-05", liquidado_em="2026-09-08", boleto="****0041")

#: Outubro SEM pagamento confirmado (venceu em 05/10) e ATRASO CRITICO (> 30 dias, decisao do dono de
#: 14/10/2026): a DMN escala.
_COM_OUTUBRO_VENCIDO: dict[str, Any] = {
    "status_conciliado": False,
    "ciclos_sem_conciliacao": 1,
    "numero_boleto": "****0042",
    "valor_em_aberto": "552.54",
    "dias_atraso_max": 31,
    "competencias_cobranca": [_mes("2026-10", "vencida", "2026-10-05"), _SETEMBRO_PAGO],
}
#: O mesmo mes, com 8 dias de atraso: NAO e' critico — resposta com os fatos, sem caso.
_COM_OUTUBRO_VENCIDO_HA_8_DIAS: dict[str, Any] = {**_COM_OUTUBRO_VENCIDO, "dias_atraso_max": 8}
#: Outubro em aberto e NO PRAZO (vence em 15/10): nada escala.
_COM_OUTUBRO_NO_PRAZO: dict[str, Any] = {
    "status_conciliado": True,
    "ciclos_sem_conciliacao": 0,
    "numero_boleto": "****0042",
    "valor_em_aberto": "552.54",
    "dias_atraso_max": 0,
    "competencias_cobranca": [_mes("2026-10", "em_aberto", "2026-10-15"), _SETEMBRO_PAGO],
}


def _sem_repeticao(textos: list[str]) -> None:
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos


# --- 1. A escalacao por atraso com valor e vencimento; o "e a data exata?" fica no mesmo mes -------------


async def test_escalacao_da_valor_e_vencimento_e_o_seguinte_fica_no_mes_em_discussao() -> None:
    conversa = _Conversa(_COM_OUTUBRO_VENCIDO)
    # "Valor ultima mensalidade e vencimento"
    final, primeiro = await conversa.mensagem(cobranca_foco="valor")
    assert final["route"] == "escalate_human"
    assert primeiro.startswith(
        "A mensalidade de outubro de 2026 (boleto final 0042) ainda está sem pagamento confirmado. "
        "Ela é de R$ 552,54, com vencimento em 05/10. Vou passar para alguém da nossa equipe"
    )
    fatos = {"competencias": final["competencias_cobranca"], "dados_de": "2026-10-12"}
    assert motivo_de_recusa(primeiro, "ack_escalacao", fatos) is None
    # "Qual valor e data exata?"
    _, seguinte = await conversa.mensagem(cobranca_foco="valor")
    assert seguinte.startswith(
        "A mensalidade de outubro de 2026 (R$ 552,54, vencimento em 05/10) ainda está sem pagamento "
        "confirmado."
    )
    assert "setembro" not in seguinte


# --- 2. Ainda no prazo ---------------------------------------------------------------------------------


async def test_ainda_no_prazo_dito_com_clareza_e_quantos_dias_faltam() -> None:
    conversa = _Conversa(_COM_OUTUBRO_NO_PRAZO)
    # "hoje e' 13/10, vence 15/10, como assim ainda nao foi paga?"
    final, texto = await conversa.mensagem(competencia="2026-10", cobranca_foco="esclarecimento")
    assert texto == "Ainda está no prazo: a de outubro de 2026 vence em 15/10 (daqui a 2 dias)."
    assert motivo_de_recusa(texto, "mensagem", final["mensagem"]["fatos"]) is None


# --- 3 e 5. Como pagar depois do vencimento: resposta propria, nunca a escada ------------------------------


def test_como_pagar_depois_do_vencimento_e_foco_proprio_com_a_resposta_da_2a_via() -> None:
    pergunta = "como faço pra pagar depois do vencimento?"
    assert decidir_foco_da_cobranca(pergunta) == "como_pagar"
    handoff = {"cobranca_subtipo": "consulta_valores", "competencia": None, "cobranca_foco": "como_pagar"}
    assert _handoff_com_mes_do_texto(pergunta, handoff)["cobranca_subtipo"] == "boleto_2via"


async def test_pergunta_nova_depois_do_prazo_tem_resposta_propria() -> None:
    conversa = _Conversa(_COM_OUTUBRO_NO_PRAZO)
    await conversa.mensagem(competencia="2026-10", cobranca_foco="esclarecimento")
    _, como = await conversa.mensagem(tipo="2a_via", cobranca_foco="como_pagar")
    assert como == TEXTO_COMO_PAGAR
    _, de_novo = await conversa.mensagem(tipo="2a_via", cobranca_foco="como_pagar")
    assert de_novo == TEXTO_COMO_PAGAR_CURTO
    _sem_repeticao(conversa.textos)


async def test_a_escada_e_da_mesma_pergunta_nao_do_mesmo_texto() -> None:
    """Valor de outubro duas vezes -> a curta; depois a pergunta do VENCIMENTO de outubro tem a dela."""
    conversa = _Conversa(_COM_OUTUBRO_NO_PRAZO)
    valor = {"competencia": "2026-10", "cobranca_foco": "valor"}
    _, primeiro = await conversa.mensagem(**valor)
    _, curta = await conversa.mensagem(**valor)
    assert curta != primeiro and curta not in NAO_CONSIGO
    _, vencimento = await conversa.mensagem(competencia="2026-10", cobranca_foco="vencimento")
    assert vencimento not in NAO_CONSIGO and "15/10" in vencimento
    _sem_repeticao(conversa.textos)


# --- 4. "Ja' paguei dia 06. Comprovante?" --------------------------------------------------------------


def test_ja_paguei_dia_e_comprovante_sao_foco_proprio() -> None:
    for pergunta in ("Já paguei dia 06. Comprovante?", "Enviar comprovante agora?"):
        assert decidir_foco_da_cobranca(pergunta) == "ja_paguei"
    assert decidir_foco_da_cobranca("ja paguei julho?") == "pagamento"


async def test_ja_paguei_no_prazo_diz_os_dados_o_comprovante_e_oferece_uma_vez() -> None:
    conversa = _Conversa(_COM_OUTUBRO_NO_PRAZO)
    pergunta = {"tipo": "status_pagamento", "intencao": "confirmacao_pagamento", "cobranca_foco": "ja_paguei"}
    final, primeiro = await conversa.mensagem(**pergunta)
    assert primeiro == (
        "Nos dados que tenho (dados de 12/10), o pagamento da mensalidade de outubro de 2026 ainda não "
        "aparece. Um pagamento feito perto dessa data, ou depois, pode ainda não constar. Por aqui eu não "
        "consigo receber comprovante. Se quiser, posso te passar para alguém da nossa equipe conferir o "
        "comprovante."
    )
    assert (
        motivo_de_recusa(primeiro, "mensagem", final["mensagem"]["fatos"] | {"dados_de": "2026-10-12"})
        is None
    )
    textos = [primeiro] + [(await conversa.mensagem(**pergunta))[1] for _ in range(3)]
    assert textos[1].startswith("Por aqui eu não recebo comprovante")
    assert textos[2:] == list(NAO_CONSIGO)
    assert sum("nossa equipe" in t for t in textos) == 1
    _sem_repeticao(textos)


async def test_ja_paguei_com_atraso_escala_sem_repetir_so_sem_pagamento() -> None:
    conversa = _Conversa(_COM_OUTUBRO_VENCIDO)
    pergunta = {"tipo": "status_pagamento", "intencao": "confirmacao_pagamento", "cobranca_foco": "ja_paguei"}
    final, texto = await conversa.mensagem(**pergunta)
    assert final["route"] == "escalate_human"
    assert "Por aqui eu não consigo receber comprovante." in texto
    assert "pode ainda não constar" in texto and "Vou passar para alguém da nossa equipe" in texto
    assert "Se quiser, posso te passar" not in texto
    _, seguinte = await conversa.mensagem(**pergunta)
    assert "receber comprovante" in seguinte and seguinte != texto
    assert _FECHO not in seguinte or seguinte != texto


async def test_um_mes_sem_pagamento_ha_8_dias_responde_valor_e_data_sem_abrir_caso() -> None:
    """14/10/2026 (decisao do dono): "Valor ultima mensalidade e vencimento" com outubro vencida ha' 8 dias —
    os FATOS (valor e vencimento), a oferta de uma pessoa e nenhum caso; o seguinte fica no mesmo mes."""
    conversa = _Conversa(_COM_OUTUBRO_VENCIDO_HA_8_DIAS)
    final, primeiro = await conversa.mensagem(cobranca_foco="valor")
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert primeiro == (
        "A mensalidade de outubro de 2026 (R$ 552,54, vencimento em 05/10) ainda está sem pagamento "
        f"confirmado. Se quiser, posso te passar para alguém da nossa equipe. {_FECHO}"
    )
    _, seguinte = await conversa.mensagem(cobranca_foco="esclarecimento")
    assert "setembro" not in seguinte and "outubro de 2026" in seguinte
