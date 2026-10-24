"""Seguranca da Helena (avaliacao r4 de 22/10/2026): negacao lida como negacao, batida na cabeca reconhecida
pelas formas comuns e o alarme de um sintoma citado antes valendo nos turnos seguintes.

Frases de teste escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.helena import graph as g
from maezo.conhecimento import base_saude as bs
from tests.unit.agents.test_helena_base_saude import _classify, _ConversaDe21, _texto


def _base() -> bs.BaseSaude:
    return bs.base_aplicavel(bs.carregar_base(bs.raiz_padrao()), hoje=date(2026, 10, 22))


# --- (c) a negacao do sinal de alarme e' negacao ------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "não, sem falta de ar, sem febre alta",
        "ela está tossindo, mas não tem falta de ar nem febre alta",
        "nada de falta de ar; febre baixinha só",
        "nenhuma falta de ar até agora",
        "não teve desmaio, nem convulsão",
    ],
)
def test_sinal_negado_nao_e_alarme(fala: str) -> None:
    base = _base()
    assert not g._sinal_forte_de_emergencia(fala)
    assert not bs.alarme_no_texto(base, f"tosse {fala}")
    assert not bs.alarme_do_assunto(base, "sin-tosse", fala)


@pytest.mark.parametrize(
    "fala",
    [
        "não, falta de ar sim, bastante",
        "não sei, mas agora está com falta de ar",
        "febre baixa, porém falta de ar forte",
        "ele desmaiou faz pouco",
    ],
)
def test_sinal_afirmado_continua_alarme(fala: str) -> None:
    assert g._sinal_forte_de_emergencia(fala)


@pytest.mark.parametrize(
    "fala", ["não para de vomitar", "não, mas vomitou de novo", "não sei se é normal, está vomitando"]
)
def test_alarme_da_batida_com_nao_que_nao_nega_o_sinal(fala: str) -> None:
    assert bs.alarme_do_assunto(_base(), "sin-batida-na-cabeca", fala)


@pytest.mark.parametrize(
    "resposta",
    [
        "não, sem falta de ar, sem febre alta",
        "nada disso, sem falta de ar",
        "não tem falta de ar nem febre alta",
    ],
)
async def test_negar_os_sinais_da_pergunta_de_alerta_nao_da_192_nem_abre_caso(resposta: str) -> None:
    conversa = _ConversaDe21()
    await conversa.diz("estou com tosse faz uns dias", _classify(intent="symptom", population="adult"))
    resultado = await conversa.diz(resposta, _classify(intent="symptom", population="adult"))
    # Sem o encaminhamento de emergencia (a linha de seguranca "se for grave, ligue 192" pode ficar).
    assert "Ligue 192 (SAMU) agora" not in _texto(resultado)
    assert conversa.motivos == [] and conversa.cib.inicios == []


# --- (b) batida na cabeca de crianca: as perguntas da batida, nunca as de febre ----------------------------
@pytest.mark.parametrize(
    "fala",
    [
        # (O bebe COM MENOS DE 1 ANO dito -> 192 na hora: lista de alarmes reais do dono, DL-0154 —
        # `test_helena_saude_seguranca.py`.)
        "O menino levou uma pancada forte na testa no parquinho",
        "minha filha tomou um tombo e bateu a nuca no chão",
        "meu neném caiu da cama e bateu a cabecinha",
        "meu filho de 5 anos levou uma batida na cabeça jogando bola",
    ],
)
async def test_batida_na_cabeca_de_crianca_recebe_as_perguntas_da_batida(fala: str) -> None:
    conversa = _ConversaDe21()
    resultado = await conversa.diz(fala, _classify(intent="symptom", population="pediatric"))
    texto = _texto(resultado)
    assert conversa.memoria["coleta_pendente"].startswith("V4_ALERTA:sin-batida-na-cabeca:")
    assert "vômito" in texto and "febre" not in texto.lower()


# --- (a) o alarme da batida vale nos turnos seguintes, mesmo depois da pergunta de alerta ------------------
@pytest.mark.parametrize(
    "depois",
    [
        "agora à noite ele vomitou duas vezes",
        "ela está muito sonolenta, não quer acordar",
        "teve uma convulsão agora",
        "está saindo um líquido pelo ouvido",
    ],
)
async def test_sinal_da_batida_depois_da_pergunta_respondida_vai_ao_192(depois: str) -> None:
    conversa = _ConversaDe21()
    await conversa.diz(
        "meu filho caiu do escorregador e bateu a cabeça", _classify(intent="symptom", population="pediatric")
    )
    await conversa.diz("não teve nada disso", _classify(intent="symptom", population="pediatric"))
    await conversa.diz("2", _classify(intent="symptom", population="pediatric"))
    resultado = await conversa.diz(depois, _classify(intent="symptom", population="pediatric"))
    assert "red_flag_clinico" in conversa.motivos
    assert "Ligue 192 (SAMU) agora" in _texto(resultado)


def test_combinacao_exige_as_duas_listas_perto() -> None:
    base = _base()
    citados: Any = {
        a.id
        for a in bs.sintomas_citados(base, "minha pressão caiu de manhã e à tarde fiquei com dor de cabeça")
    }
    assert "sin-batida-na-cabeca" not in citados
    assert "sin-batida-na-cabeca" in {a.id for a in bs.sintomas_citados(base, "caiu e bateu forte a testa")}
