"""Melhorias inspiradas na TINA (decisao do dono, 18/10/2026): jornadas do plano, resposta direta a "isso e'
normal?", emergencia mais forte e o contexto do que a PROPRIA pessoa disse (DL-0125)."""

from __future__ import annotations

import re

import pytest

from maezo.agents.helena import helena_v4
from maezo.agents.helena.graph import RESPOSTA_ESCALONAMENTO_URGENTE
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.conhecimento import base_saude as b
from maezo.conhecimento import jornadas as j
from maezo.conhecimento import saude_gerativa as sg
from tests.unit.agents.test_helena_saude_gerativa import (
    _CLINICA,
    _DONO,
    _OUTRA,
    _classify,
    _Conversa,
    _texto,
    _Transporte,
)

_RESPOSTA = (
    "Os exames de sangue mais pedidos para o metabolismo incluem glicemia, colesterol e função da tireoide."
)
_NOMES_PROIBIDOS = re.compile(r"\b(?:dr|dra|doutor|hospital|clinica [A-Z]|laboratorio [A-Z])\b")


def test_as_jornadas_passam_nas_cercas_e_nao_citam_prestador_nem_agenda() -> None:
    for nome, texto in j.TEXTOS.items():
        assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None, nome
        assert motivo_de_canal_nao_confirmado(texto) is None, nome
        plano = b.normalizar(texto)
        assert not re.search(r"\b(?:agendo|agendar|vou marcar|pesquiso|vou pesquisar)\b", plano), nome
        assert "cobre" not in plano and "cobertura" not in plano, nome
        for frase in re.split(r"(?<=[.!?])\s+", texto):
            assert len(frase.split()) <= 20, (nome, frase)
    assert j.PRAZO_ESPECIALISTA_DIAS_UTEIS == helena_v4.PRAZO_ESPECIALISTA_DIAS_UTEIS


@pytest.mark.parametrize(
    ("pergunta", "categoria", "esperada"),
    [
        ("Quais exames de sangue avaliam meu metabolismo? Como faço pelo plano?", None, j.JORNADA_EXAME),
        ("O que é colonoscopia?", "exame", j.JORNADA_EXAME),
        ("Preciso fazer uma cirurgia de hérnia, como faço?", None, j.JORNADA_CIRURGIA),
        ("Cólica intensa é normal?", None, None),
        ("O que é endometriose?", "condicao", None),
    ],
)
def test_qual_jornada(pergunta: str, categoria: str | None, esperada: str | None) -> None:
    assert j.jornada_da_pergunta(b.normalizar(pergunta), categoria) == esperada


async def test_a_jornada_entra_no_fim_da_resposta_redigida() -> None:
    conversa = _Conversa(_Transporte(_RESPOSTA))
    resultado = await conversa.diz(
        "Quais exames de sangue avaliam meu metabolismo? Como faço pelo plano?", _CLINICA
    )
    texto = _texto(resultado)
    assert texto.startswith(_RESPOSTA) and texto.endswith(j.TEXTOS[j.JORNADA_EXAME])
    assert conversa.motivos == []
    prompt = conversa.transporte.chamadas[-1][1][0]["content"][0]["text"]
    assert "o sistema acrescenta, no fim, o passo de como usar o plano" in prompt


def test_isso_e_normal_mais_direto_no_prompt_e_a_checagem_continua() -> None:
    assert "não costuma ser considerada normal e merece avaliação" in sg.PROMPT_SISTEMA
    pedido = sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="x")
    assert not [
        v
        for v in sg.verificar(
            "Cólica forte não costuma ser considerada normal e merece avaliação.", pedido, cercas=lambda t: []
        )
    ]
    regras = [v.regra for v in sg.verificar("Você tem endometriose.", pedido, cercas=lambda t: [])]
    assert "diagnostico" in regras


def test_emergencia_mais_forte_e_neutra() -> None:
    assert RESPOSTA_ESCALONAMENTO_URGENTE.startswith("Ligue 192 (SAMU) agora ou vá ao pronto-socorro.")
    assert "Não dirija: peça para alguém te levar ou chame o SAMU." in RESPOSTA_ESCALONAMENTO_URGENTE
    for frase in re.split(r"(?<=[.!?])\s+", RESPOSTA_ESCALONAMENTO_URGENTE):
        assert len(frase.split()) <= 20


def test_fatos_ditos_pela_propria_pessoa() -> None:
    assert sg.fatos_ditos_pela_pessoa(["Eu fumo um maço por dia", "tenho pressão alta"]) == (
        "fuma",
        "tem pressão alta",
    )
    assert sg.fatos_ditos_pela_pessoa(["meu marido fuma muito", "minha mãe tem diabetes"]) == ()


async def test_o_redator_usa_o_que_a_pessoa_disse_e_nao_herda_populacao() -> None:
    conversa = _Conversa(_Transporte(_RESPOSTA, _RESPOSTA, _RESPOSTA))
    await conversa.diz("Eu fumo desde os 20 anos, isso faz mal?", _CLINICA)
    await conversa.diz(
        "O que é febre em bebê?", _classify(intent="clinical_question", population="pediatric")
    )
    await conversa.diz("Qual meu risco de infarto?", _CLINICA)
    ultimo = conversa.transporte.chamadas[-1][1][0]["content"][0]["text"]
    assert "a própria pessoa contou antes nesta conversa que fuma" in ultimo
    assert "A conversa é sobre um adulto." in ultimo


async def test_sem_fato_dito_nao_ha_linha_de_contexto_e_fora_da_allowlist_nada() -> None:
    conversa = _Conversa(_Transporte(_RESPOSTA))
    await conversa.diz("Qual meu risco de infarto?", _CLINICA)
    assert "contou antes" not in conversa.transporte.chamadas[-1][1][0]["content"][0]["text"]
    fora = _Conversa(conversa=_OUTRA, allowlist=(_DONO,))
    await fora.diz("Quais exames de sangue avaliam meu metabolismo? Como faço pelo plano?", _CLINICA)
    assert fora.transporte.chamadas == []
