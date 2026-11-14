"""Teste do dono de 25/10/2026, caso d: "consigo agendar uma consulta por aqui?" ia para o roteador `plano` e
recebia o cadastro inteiro. A Helena nao agenda: a orientacao dos canais (texto do pacote), sem consultar o
plano, qualquer que seja a habilidade que o roteador disser. O plano so' responde o cadastro se perguntado."""

from __future__ import annotations

import pytest
import yaml

from maezo.agents import resolve_spec_dir
from maezo.agents.helena.habilidades.conversa import agendar
from maezo.agents.helena.textos import PADRAO, TextosDaHelena, erros_dos_textos
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada
from tests.unit.agents.test_helena_rodada_3_simulador import _grafo as _grafo_sem_roteador
from tests.unit.agents.test_helena_roteador_ativo import _Modelo
from tests.unit.agents.test_helena_teste_dono_25_10_plano import _CLASSIFY_ELEG, _grafo

AGENDAR = [
    "quero agendar",
    "consigo agendar uma consulta por aqui?",
    "como marco consulta?",
    "dá pra marcar pelo whatsapp?",
    "queria marcar um exame de sangue, como faço?",
    "você marca pra mim uma consulta com cardiologista?",
    "onde eu marco consulta com ortopedista?",
]
NAO_E_AGENDAR = [
    "qual o número da minha carteirinha?",
    "meu plano está ativo?",
    "preciso de autorização para marcar a cirurgia?",
    "onde posso marcar o exame na rede credenciada?",
    "como peço reembolso da consulta que paguei?",
    "quero cancelar a consulta de amanhã",
    "qual a marca do aparelho que o plano usa?",
    "quais são os dados do meu plano?",
]
COM_QUEIXA = [
    "estou com dor nas costas, como marco consulta?",
    "to com febre faz dois dias e quero agendar",
    "sinto tontura, consigo marcar uma consulta por aqui?",
]


@pytest.mark.parametrize("mensagem", AGENDAR)
def test_pergunta_de_agendamento_e_reconhecida(mensagem: str) -> None:
    assert agendar.pergunta_de_agendamento(mensagem)


@pytest.mark.parametrize("mensagem", NAO_E_AGENDAR)
def test_outra_pergunta_nao_e_agendamento(mensagem: str) -> None:
    assert not agendar.pergunta_de_agendamento(mensagem)


def test_o_texto_sem_queixa_e_so_a_orientacao_dos_canais() -> None:
    textos = TextosDaHelena()
    for mensagem in AGENDAR[:4]:
        assert agendar.texto_do_agendamento(textos, mensagem) == (
            "Por aqui eu não agendo. Você marca pelo aplicativo Austa Clínicas, pelo portal do plano "
            "ou pela central de atendimento do plano."
        )


@pytest.mark.parametrize("mensagem", COM_QUEIXA)
def test_com_queixa_a_orientacao_leva_a_oferta_do_especialista(mensagem: str) -> None:
    texto = agendar.texto_do_agendamento(TextosDaHelena(), mensagem)
    assert texto.startswith("Por aqui eu não agendo.")
    assert texto.endswith("Se me disser o que está sentindo, eu digo qual especialista procurar.")


def test_os_textos_do_pacote_sao_os_do_codigo_e_sem_link() -> None:
    dados = yaml.safe_load(
        (resolve_spec_dir() / "agents/helena/pacote/textos.yaml").read_text(encoding="utf-8")
    )["textos"]
    assert dados[agendar.CHAVE_AGENDAR_ORIENTACAO] == agendar.TEXTO_AGENDAR_ORIENTACAO
    assert dados[agendar.CHAVE_AGENDAR_OFERTA] == agendar.TEXTO_AGENDAR_OFERTA
    assert PADRAO[agendar.CHAVE_AGENDAR_ORIENTACAO] == agendar.TEXTO_AGENDAR_ORIENTACAO
    assert erros_dos_textos(dados) == []
    assert erros_dos_textos({agendar.CHAVE_AGENDAR_ORIENTACAO: "Marque em www.exemplo.com.br"})


def test_o_roteador_do_pacote_nao_manda_agendamento_para_plano() -> None:
    instrucoes = yaml.safe_load(
        (resolve_spec_dir() / "agents/helena/pacote/roteador.yaml").read_text(encoding="utf-8")
    )["instrucoes"]
    plano = next(linha for linha in instrucoes.splitlines() if linha.strip().startswith("- plano:"))
    fora = next(linha for linha in instrucoes.splitlines() if linha.strip().startswith("- fora_do_escopo:"))
    assert "COMO marcar ou agendar consulta, exame, cirurgia ou procedimento pelo plano" not in plano
    assert "NAO e' plano" in plano and "carteirinha" in plano and "rede credenciada" in plano
    assert "consigo agendar uma consulta por aqui?" in fora


@pytest.mark.parametrize("mensagem", AGENDAR[:4])
async def test_agendar_com_roteador_dizendo_plano_nao_recebe_o_cadastro(mensagem: str) -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados[-1] == agendar.TEXTO_AGENDAR_ORIENTACAO
    assert fonte.pedidos == [] and modelo.outras == 0


@pytest.mark.parametrize("mensagem", AGENDAR[:4])
async def test_agendar_com_roteador_dizendo_conversa_e_a_orientacao(mensagem: str) -> None:
    modelo = _Modelo([decisao_v7("conversa")], [])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados[-1] == agendar.TEXTO_AGENDAR_ORIENTACAO
    assert fonte.pedidos == []


async def test_agendar_com_queixa_no_grafo_leva_a_oferta() -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(COM_QUEIXA[0]))
    assert whatsapp.enviados[-1].endswith(agendar.TEXTO_AGENDAR_OFERTA)
    assert fonte.pedidos == []


@pytest.mark.parametrize(
    "classificacao",
    [_classify(intent="information"), _classify(intent="consulta_plano", consulta_subtipo="elegibilidade")],
)
async def test_agendar_sem_o_roteador_tambem_e_a_orientacao(classificacao: str) -> None:
    grafo, whatsapp, _modelo, _cib = _grafo_sem_roteador([classificacao])
    await grafo.ainvoke(_entrada("consigo agendar uma consulta por aqui?"))
    assert whatsapp.enviados[-1] == agendar.TEXTO_AGENDAR_ORIENTACAO


async def test_controle_pergunta_do_plano_continua_com_o_cadastro() -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG, ""])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("quais são os dados do meu plano?"))
    assert "segmentação" in whatsapp.enviados[-1]
    assert fonte.pedidos == ["elegibilidade"]
