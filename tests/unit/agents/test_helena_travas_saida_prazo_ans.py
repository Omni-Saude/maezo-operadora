"""Trava de saida `sem_prazo_da_ans` (dono, 25/10/2026): nenhum texto da Helena cita prazo da ANS
("ate 7 dias uteis pela ANS", "pelas regras da ANS ... deve ser oferecida em ate 14 dias uteis"). A frase com
o
prazo sai no ultimo passo do envio, venha de onde vier (modelo, base, texto antigo). O numero de registro da
ANS e as demais frases ficam, byte a byte."""

from __future__ import annotations

import re

import pytest

from maezo.agents.helena import helena_v4, triagem_v2
from maezo.agents.helena import travas_saida as ts
from maezo.agents.helena.textos import PADRAO
from maezo.conhecimento import jornadas, saude_gerativa

_COM_PRAZO = [
    (
        "Para marcar, use o aplicativo Austa Clínicas. Pelas regras da ANS, a consulta com pediatra deve ser "
        "oferecida em até 7 dias úteis. A UBS também atende.",
        "Para marcar, use o aplicativo Austa Clínicas. A UBS também atende.",
    ),
    (
        "Você prefere: 1) saber onde marcar consulta com clínico geral pelo plano "
        "(até 7 dias úteis pela ANS); ou 2) falar com a nossa equipe de saúde?",
        "Você prefere: 1) saber onde marcar consulta com clínico geral pelo plano; ou 2) falar com a nossa "
        "equipe de saúde?",
    ),
    (
        "Marque pelo portal do plano. A ANS determina um prazo de 14 dias úteis para a consulta com "
        "especialista.",
        "Marque pelo portal do plano.",
    ),
    (
        "Pela regra da ANS, a consulta deve ser oferecida em até 10 dias úteis.\nSe piorar, procure o "
        "pronto-atendimento.",
        "Se piorar, procure o pronto-atendimento.",
    ),
    (
        "O plano deve oferecer consulta com especialista em até 14 dias úteis (RN 566). Fale com a central.",
        "Fale com a central.",
    ),
]
_SEM_PRAZO = [
    "Nº ANS do plano: 999999",
    "O registro do plano na ANS é 123456. A vigência é desde 01/03/2024.",
    "Marque pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de atendimento do plano.",
    "Se quiser reclamar, você pode procurar a ANS ou o Procon.",
    "O boleto vence em 3 dias úteis.",
    "Consulta com cardiologista é consulta de especialista. Você marca pelo aplicativo.",
]


@pytest.mark.parametrize(("texto", "esperado"), _COM_PRAZO)
def test_frase_com_prazo_da_ans_sai(texto: str, esperado: str) -> None:
    assert ts.sem_prazo_da_ans(texto) == esperado
    assert ts.na_saida(texto) == esperado


@pytest.mark.parametrize("texto", _SEM_PRAZO)
def test_controle_negativo_sem_prazo_da_ans_fica_byte_a_byte(texto: str) -> None:
    assert ts.sem_prazo_da_ans(texto) is texto
    assert ts.na_saida(texto) == texto


def test_a_trava_e_idempotente() -> None:
    for texto, _ in _COM_PRAZO:
        uma = ts.na_saida(texto)
        assert ts.na_saida(uma) == uma


def test_nenhum_texto_fixo_da_helena_cita_prazo_da_ans() -> None:
    """Os textos do pacote, da triagem, do especialista, da jornada e das opcoes ja' nascem sem prazo: a
    trava nao tem nada a tirar deles (o MESMO texto, byte a byte)."""
    textos = [
        *PADRAO.values(),
        triagem_v2.RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
        triagem_v2.RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
        triagem_v2.RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE,
        *jornadas.TEXTOS.values(),
        saude_gerativa.opcoes_do_sintoma(False),
        saude_gerativa.opcoes_do_sintoma(True),
        helena_v4.texto_final_do_sintoma("dor nas costas"),
        helena_v4.texto_final_do_sintoma("tosse", helena_v4.POPULACAO_CRIANCA),
        helena_v4.texto_do_especialista(
            "cardiologista", basica=False, segmentacao=None, frase_cobertura=None
        ),
        helena_v4.texto_do_especialista(
            "pediatra", basica=True, segmentacao="Ambulatorial", frase_cobertura=None
        ),
    ]
    for texto in textos:
        assert not re.search(r"dias? úteis|pelas? regras? da ANS|prazo da ANS", texto, re.IGNORECASE), texto
        assert ts.sem_prazo_da_ans(texto) is texto
