"""Teste do dono de 27/10/2026 (11:04-11:15 UTC, imagem e79e0f71) e a decisao dele (opcao A).

Causas nos logs: "Faz sentido um cardiologista?" logo depois do protocolo foi ao classify/DMN (192 + caso);
"pq nao me orientou a procurar um urologista?" recebeu o texto da v4 ("nao pesquiso"); "Quem disse que e'
depressao?" foi lido como resposta sem clareza (re-pergunta + CVV); "cansaco, perda de libido e desanimo"
virou depressao. Decisao: quem pergunta QUAL profissional recebe a resposta direta (especialista do mapa,
clinico, canais e UMA linha de sinais); as perguntas so' com sinal afirmado na mensagem.
"""

from __future__ import annotations

import json
from typing import Any

from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.agents.helena.habilidades.protocolo import sinais
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_protocolo_especialista import _com_historico
from tests.unit.agents.test_helena_protocolos import _CATALOGO, _TEXTOS, _escolha, _grafo, _mapa, _Modelo
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

_PEITO = _CATALOGO.protocolos["tri-adulto-dor-no-peito"]
_URINAR = _CATALOGO.protocolos["tri-adulto-dor-ao-urinar"]
_DEPRESSAO = _CATALOGO.protocolos["mental-depressao"]
_FALA_PEITO = (
    "Estou sentindo dores leves no peito ou tonturas ao fazer exercícios, que tipo de profissional eu devo "
    "procurar?"
)
_FALA_URINA = (
    "Estou sentindo jato urinário fraco e preciso acordar muitas vezes à noite para urinar, que tipo de "
    "profissional eu devo procurar?"
)
_FALA_LIBIDO = (
    "Estou sentindo cansaço constante, perda de libido e desânimo persistente, que tipo de profissional eu "
    "devo procurar?"
)
_NAO_PESQUISO = "não pesquiso"
_CVV = "188"


def _pre(*respostas: str) -> str:
    return json.dumps({"respostas": list(respostas)})


def _sem_sinal(protocolo: Any) -> str:
    return _pre(*("nao_dito",) * len(protocolo.perguntas_de_alerta))


# --- A resposta direta ---------------------------------------------------------------------------------
async def test_jato_fraco_qual_profissional_recebe_o_urologista_sem_perguntas() -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_URINAR.id)],
        pre=[_sem_sinal(_URINAR)],
        mapa=[_mapa("esp-queixa-dificuldade-para-urinar")],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(_FALA_URINA))
    texto = whatsapp.enviados[-1]
    assert "urologista" in texto and "clínico geral" in texto and "1)" not in texto
    assert texto.rstrip().endswith(sinais.linha(_URINAR, "adulto"))
    assert not final.get("coleta_pendente") and not _starts(cib)


async def test_libido_nao_vira_depressao_nem_cvv() -> None:
    """Mesmo se a escolha cair na depressao, o mapa da queixa fisica decide e nao ha' CVV."""
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_DEPRESSAO.id)],
        pre=[_sem_sinal(_DEPRESSAO)],
        mapa=[_mapa("esp-queixa-cansaco-libido-baixa-energia")],
    )
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_entrada(_FALA_LIBIDO))
    texto = whatsapp.enviados[-1]
    assert "urologista" in texto and "ginecologista" in texto and "endocrinologista" in texto
    assert _CVV not in texto and "depress" not in texto.lower()


def test_o_prompt_da_escolha_diz_que_desanimo_com_sintoma_fisico_nao_e_saude_mental() -> None:
    prompt = n.prompt_da_escolha(_CATALOGO, bloco_conversa="", bloco_mensagem="x")
    assert "perda de libido" in prompt and "NAO e' saude mental" in prompt


# --- Os seguimentos: nunca 192/caso sem sinal novo, nunca "nao pesquiso" -------------------------------
_CONVERSA_DO_PEITO = [(_FALA_PEITO, "Para dor no peito, o ideal é ser avaliado ainda hoje.")]


async def test_faz_sentido_um_cardiologista_nunca_vai_ao_192() -> None:
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-dor-no-peito-nao-aguda")])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_com_historico("Faz sentido um cardiologista?", _CONVERSA_DO_PEITO))
    texto = whatsapp.enviados[-1]
    assert "cardiologista" in texto and "192 (SAMU) agora" not in texto
    assert not final.get("escalation_motivo") and not _starts(cib)


async def test_faz_sentido_um_cardiologista_depois_do_protocolo_nunca_vai_ao_192() -> None:
    conversa = [
        (_FALA_PEITO, n.texto_das_perguntas(_PEITO)),
        ("nenhuma", n.texto_sem_sinal(_PEITO, _TEXTOS)),
    ]
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-dor-no-peito-nao-aguda")])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_com_historico("Faz sentido um cardiologista?", conversa))
    assert "cardiologista" in whatsapp.enviados[-1]
    assert not final.get("escalation_motivo") and not _starts(cib)


async def test_pq_nao_me_orientou_um_urologista_nao_recebe_o_texto_da_v4() -> None:
    conversa = [(_FALA_URINA, "Para dificuldade para urinar, o especialista costuma ser o urologista.")]
    fala = "Pq não me orientou a procurar um urologista logo de cara?"
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-dificuldade-para-urinar")])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_com_historico(fala, conversa))
    texto = whatsapp.enviados[-1]
    assert "urologista" in texto and _NAO_PESQUISO not in texto
    assert not final.get("escalation_motivo") and not _starts(cib)


async def test_contestacao_quem_disse_que_e_depressao_pede_desculpa_e_reorienta_sem_cvv() -> None:
    perguntas = n.texto_das_perguntas(_DEPRESSAO)
    fala = (
        "Quem disse que é depressão? Eu disse que estou sentindo cansaço constante, perda de libido e "
        "desânimo persistente."
    )
    modelo = _Modelo(
        [decisao_v7("saude")],
        escolha=[_escolha("nenhum")],
        mapa=[_mapa("esp-queixa-cansaco-libido-baixa-energia")],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _com_historico(fala, [(_FALA_LIBIDO, perguntas)], n.codigo_do_alerta(_DEPRESSAO.id))
    )
    texto = whatsapp.enviados[-1]
    assert texto.startswith("Desculpe") and "urologista" in texto
    assert _CVV not in texto and "1)" not in texto and modelo.prompts["leitura"] == []
    assert not final.get("coleta_pendente") and not final.get("escalation_motivo") and not _starts(cib)


# --- Historia sem "qual profissional": as perguntas continuam; todo final diz o especialista ------------
async def test_historia_sem_pergunta_de_profissional_continua_com_as_perguntas() -> None:
    palp = _CATALOGO.protocolos["tri-adulto-palpitacoes"]
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(palp.id)], pre=[_sem_sinal(palp)])
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(_entrada("estou sentindo o coração disparado desde cedo"))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(palp)
    assert str(final.get("coleta_pendente")).startswith(n.PENDENTE_ALERTA)


def test_final_de_atendimento_hoje_sem_sinal_diz_o_especialista() -> None:
    texto = n.texto_sem_sinal(_PEITO, _TEXTOS)
    assert "cardiologista" in texto and "ainda hoje" in texto


async def test_mapa_no_prazo_usa_a_queixa_pelo_jeito_de_falar() -> None:
    """Rodada de 27/10/2026 (d0530503): o mapa caiu no prazo e a libido recebeu o clinico generico."""
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha("nenhum")],
        mapa=["", ""],
    )
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_entrada(_FALA_LIBIDO))
    texto = whatsapp.enviados[-1]
    assert "urologista" in texto and "endocrinologista" in texto and _CVV not in texto


def test_a_queixa_sem_modelo_nunca_chuta_um_especialista_errado() -> None:
    from maezo.agents.helena.habilidades.protocolo import evidencia as ev

    assert ev.queixa_do_mapa(_FALA_LIBIDO, _CATALOGO) == "esp-queixa-cansaco-libido-baixa-energia"
    assert ev.queixa_do_mapa(_FALA_URINA, _CATALOGO) == "esp-queixa-dificuldade-para-urinar"
    lombar = "Estou com dores constantes na lombar e perdi força física, que profissional procuro?"
    assert ev.queixa_do_mapa(lombar, _CATALOGO) in (None, "esp-queixa-dor-nas-costas")
    assert ev.queixa_do_mapa("estou passando mal, que médico procuro?", _CATALOGO) is None
