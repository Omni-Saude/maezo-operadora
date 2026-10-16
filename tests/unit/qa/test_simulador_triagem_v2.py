"""Simulador e roteiro de aceite da NOVA TRIAGEM DA HELENA (DL-0104).

* o roteiro `triagem_v2` existe ao lado do `dono_v1` (intocado), todo padrao compila, e os TEXTOS FIXOS
  que a Helena manda com a chave ligada passam nos passos que os esperam (o roteiro nao reprova a
  resposta certa);
* D13 "caso evitavel": so' se aplica com a chave ligada e nas personas marcadas; alarme, risco, falha
  tecnica e caso depois de pedido explicito de pessoa nunca contam.
"""

from __future__ import annotations

from maezo.agents.helena.graph import RESPOSTA_DUVIDA_DE_SAUDE, RESPOSTA_HANDOFF_RECUSADA, RESPOSTA_NAVEGACAO
from maezo.agents.helena.triagem_v2 import (
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_OFERTA_PESSOA,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
    RESPOSTA_TRIAGEM_SEM_CONDUTA,
    pergunta_da_ficha,
)
from maezo.qa.simulador import aceite as ac
from maezo.qa.simulador import checagens as ch
from maezo.qa.simulador.personas import por_id

from .apoio_simulador import conversa, escalonamento, turno


def test_triagem_v2_existe_ao_lado_do_dono_v1_e_todo_padrao_compila() -> None:
    assert set(ac.ROTEIROS) == {
        "dono_v1",
        "triagem_v2",
        "cancelamento_v1",  # DL-0111
        "lucas_v4",  # DL-0114
        "lucas_v4_pagador",
        "lucas_v4_nao_pagador",
        "helena_v4",  # DL-0115
    }
    ac.validar(ac.ROTEIROS["triagem_v2"])
    nomes = [s.nome for s in ac.TRIAGEM_V2]
    assert nomes == [
        "1-duvida-de-saude",
        "2-pergunta-clinica-sem-sintoma",
        "3-dor-ao-urinar",
        "4-filha-com-dor",
        "5-dor-nas-costas",
        "6-filho-com-tosse",
        "7-necessidade-nao-clara",
        "8-pedido-de-pessoa",
        "9-alarme-durante-a-ficha",
        "10-dor-nas-costas-nao-pergunta-o-local",
        "11-filha-de-6-anos-nao-pergunta-a-idade",
    ]
    textos = [p.texto for s in ac.TRIAGEM_V2 for p in s.passos]
    for exemplo in (
        "Quero tirar uma dúvida sobre minha saúde",
        "O que vc acha que pode ser, uma dor muito forte ao urinar?",
        "Minha filha está com dor",
        "Estou com dor nas costas",
        "Meu filho está com tosse",
        "Preciso ir ao médico?",
        "Quero falar com uma pessoa",
    ):
        assert exemplo in textos


def test_os_textos_fixos_da_chave_ligada_passam_nos_passos_do_roteiro() -> None:
    """A resposta CERTA de cada passo (os textos fixos da Helena com a chave ligada) nao reprova."""
    certos = {
        "Quero tirar uma dúvida sobre minha saúde": (RESPOSTA_DUVIDA_DE_SAUDE, None),
        "Posso tomar dipirona?": (RESPOSTA_TRIAGEM_SEM_CONDUTA, None),
        "Preciso ir ao médico?": (RESPOSTA_NAVEGACAO, None),
        "O que vc acha que pode ser, uma dor muito forte ao urinar?": (
            pergunta_da_ficha("PERGUNTAR_FICHA_URINARIO:1,3"),
            None,
        ),
        "Minha filha está com dor": (pergunta_da_ficha("PERGUNTAR_FICHA_CRIANCA@filha"), None),
        "Minha filha de 6 anos está com dor": (pergunta_da_ficha("PERGUNTAR_FICHA_CRIANCA:2,3@filha"), None),
        "Ela tem 6 anos, está comendo e fazendo xixi normal, não está mole": (
            RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
            None,
        ),
        "Estou com dor nas costas": (pergunta_da_ficha("PERGUNTAR_FICHA_DOR:2,3"), None),
        "É na lombar, média, começou ontem": (RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO, None),
        "Meu filho está com tosse": (pergunta_da_ficha("PERGUNTAR_FICHA_CRIANCA@filho"), None),
        "Oi, preciso de ajuda": (RESPOSTA_TRIAGEM_ESCLARECER, None),
        "preciso de ajuda": (RESPOSTA_TRIAGEM_OFERTA_PESSOA, None),
        "4": (RESPOSTA_HANDOFF_RECUSADA, "solicitacao_humano"),
        "Quero falar com uma pessoa": (RESPOSTA_HANDOFF_RECUSADA, "solicitacao_humano"),
        "agora estou com dor no peito e falta de ar": (
            "Recebemos o seu relato e encaminhamos o seu caso. Ligue agora para o 192 (SAMU).",
            "red_flag_clinico",
        ),
    }
    for segmento in ac.TRIAGEM_V2:
        anterior = None
        for passo in segmento.passos:
            texto, motivo = certos[passo.texto]
            assert texto is not None, passo.texto
            escalacoes = [{"agente": "helena", "motivo": motivo, "severidade": None}] if motivo else []
            assert ac.avaliar_passo(passo, [("helena", texto)], escalacoes, anterior) == [], passo.texto


def test_o_roteiro_reprova_o_caso_aberto_onde_ele_e_evitavel() -> None:
    passo = ac.TRIAGEM_V2[1].passos[0]  # "Posso tomar dipirona?"
    falhas = ac.avaliar_passo(
        passo,
        [("helena", "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe de saúde.")],
        [{"agente": "helena", "motivo": "intencao_clinica", "severidade": "moderada"}],
        None,
    )
    assert any(f.startswith("escalacao: esperava nenhuma") for f in falhas)


def test_o_roteiro_reprova_diagnostico() -> None:
    passo = ac.TRIAGEM_V2[2].passos[0]  # dor ao urinar
    falhas = ac.avaliar_passo(passo, [("helena", "Pode ser uma infecção urinária, tem febre?")], [], None)
    assert any(f.startswith("nao_deve") for f in falhas)


# --- D13: caso evitavel ------------------------------------------------------------------------------
def test_d13_nao_se_aplica_com_a_chave_desligada() -> None:
    c = conversa(
        turno(1, helena=["ok"], escalonamentos=[escalonamento("helena", "intencao_clinica")]),
        persona_id="sintoma_leve",
    )
    r = ch.d13_caso_evitavel(c, por_id("sintoma_leve"))
    assert not r.aplicavel and r.passou
    assert [x.checagem for x in ch.checar_conversa(c, por_id("sintoma_leve"))] == list(ch.CHECAGENS)


def test_d13_conta_caso_evitavel_com_a_chave_ligada_na_persona_marcada() -> None:
    c = conversa(
        turno(1, helena=["ok"], escalonamentos=[escalonamento("helena", "intencao_clinica")]),
        persona_id="sintoma_leve",
    )
    r = ch.d13_caso_evitavel(c, por_id("sintoma_leve"), triagem_v2=True)
    assert r.aplicavel and not r.passou
    assert "intencao_clinica" in r.achados[0].motivo


def test_d13_nunca_conta_alarme_risco_falha_ou_pedido_de_pessoa() -> None:
    c = conversa(
        turno(1, helena=["ok"], escalonamentos=[escalonamento("helena", "red_flag_clinico")]),
        turno(2, helena=["ok"], escalonamentos=[escalonamento("helena", "risco_psicossocial")]),
        turno(3, helena=["ok"], escalonamentos=[escalonamento("helena", "falha_tecnica")]),
        turno(
            4,
            texto="quero falar com um atendente",
            helena=["ok"],
            escalonamentos=[escalonamento("helena", "solicitacao_humano")],
        ),
        persona_id="sintoma_leve",
    )
    assert ch.d13_caso_evitavel(c, por_id("sintoma_leve"), triagem_v2=True).passou


def test_d13_so_nas_personas_marcadas() -> None:
    assert por_id("sintoma_leve").caso_evitavel_com_triagem_v2 is True
    assert por_id("duvida_saude").caso_evitavel_com_triagem_v2 is True
    assert por_id("emergencia").caso_evitavel_com_triagem_v2 is False
    c = conversa(
        turno(1, helena=["ok"], escalonamentos=[escalonamento("helena", "solicitacao_humano")]),
        persona_id="emergencia",
    )
    assert not ch.d13_caso_evitavel(c, por_id("emergencia"), triagem_v2=True).aplicavel
