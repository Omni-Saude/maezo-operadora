"""Teste do dono de 25/10/2026 (conversa de teste, 12:50-12:56 UTC): tres correcoes da Helena.

3. "Meu plano esta' apto?" recebia o cadastro inteiro (duas vezes): agora a CONCLUSAO direta, e a pergunta
   repetida so' a conclusao curta.
4. "Nao entendi" depois da resposta do plano saia uma frase sem sentido do modelo: agora reexplica a ULTIMA
   resposta em palavras simples, sem modelo e sem mudar de assunto.
6. "Onde eu baixo esse aplicativo?" recebia "nao tenho um link...": agora o texto do pacote, sem link.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
import yaml

from maezo.agents import resolve_spec_dir
from maezo.agents.helena.consultas_plano import _FECHO, RESPOSTA_SEM_VINCULO_ATIVO
from maezo.agents.helena.graph import HelenaGraph
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.agents.helena.habilidades.conversa import app as app_mod
from maezo.agents.helena.habilidades.conversa import reexplicar as rx
from maezo.agents.helena.habilidades.plano import apto
from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
from maezo.agents.helena.textos import LINK, PADRAO, erros_dos_textos
from maezo.conhecimento import saude_gerativa as sg
from maezo.ports.tina import ElegibilidadeView
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _CONVERSA,
    ELEGIBILIDADE,
    _classify,
    _entrada,
    _WhatsApp,
)
from tests.unit.agents.test_helena_rodada_3_simulador import _grafo as _grafo_sem_roteador
from tests.unit.agents.test_helena_roteador_ativo import _Modelo
from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte

HOJE = date(2026, 10, 21)
_V = ELEGIBILIDADE.vinculos[0]


def _view(**campos: Any) -> ElegibilidadeView:
    return replace(ELEGIBILIDADE, vinculos=(replace(_V, **campos),))


APTO = _view(situacao_vinculo="Apto")
SUSPENSO = _view(situacao_vinculo="Suspenso", atendimento_liberado=False)
CANCELADO = _view(situacao_vinculo="Cancelado", cancelamento="2025-01-31", atendimento_liberado=False)

TEXTO_DO_DONO_APTO = (
    "Sim. Pelo cadastro, o seu plano está ativo e liberado para atendimento (situação: Apto)."
)
PERGUNTAS_DE_APTO = [
    "Você não consegue saber se meu plano está apto pra eu utilizar?",
    "Eu quero saber se, ao procurar um médico, meu plano estará apto",
    "posso usar o meu plano?",
    "o convênio tá liberado pra consulta?",
    "consigo utilizar o plano amanhã?",
]
NAO_E_APTO = [
    "quais são os dados do meu plano?",
    "qual é o tipo do meu plano?",
    "meu plano está ativo?",
    "qual o número da minha carteirinha?",
    "desde quando vale a vigência do meu plano?",
]
NAO_ENTENDI = [
    "Não entendi",
    "como assim?",
    "não compreendi",
    "ué, como assim?",
    "não entendi nada",
    "pode explicar melhor?",
]
NAO_E_REEXPLICACAO = [
    "como assim não tem cobertura para parto?",
    "não entendi, quero falar com uma pessoa",
    "não entendi o valor do boleto",
    "obrigada",
    "meu plano está apto?",
]
ONDE_BAIXAR = [
    "Onde eu baixo esse aplicativo?",
    "como instalo o app?",
    "qual o nome do aplicativo?",
    "não acho o app pra baixar",
    "me manda o link do aplicativo",
]
NAO_E_ONDE_BAIXAR = [
    "o aplicativo não abre a carteirinha",
    "onde baixo o app do SUS?",
    "qual o link do portal?",
    "meu plano está apto?",
]


class _FonteView:
    def __init__(self, view: Any) -> None:
        self.view = view
        self.pedidos: list[str] = []

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.pedidos.append(subtipo)
        return self.view


def _grafo(modelo: _Modelo, view: Any = APTO) -> tuple[Any, _WhatsApp, _FonteView]:
    whatsapp = _WhatsApp()
    fonte = _FonteView(view)
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 21, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
    )
    return grafo.compile_graph().compile(), whatsapp, fonte


def _memoria(resultado: dict[str, Any]) -> dict[str, Any]:
    return {k: resultado[k] for k in ("historico_conversa", "ultima_mensagem_em") if k in resultado}


_CLASSIFY_ELEG = _classify(intent="consulta_plano", consulta_subtipo="elegibilidade")
_RASCUNHO_RECUSADO = "Sim, você pode usar o seu plano para consultas."


# --- 3. Pergunta de apto: leitura pura ------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", PERGUNTAS_DE_APTO)
def test_pergunta_de_apto_e_reconhecida(pergunta: str) -> None:
    assert apto.pergunta_se_esta_apto(pergunta)


@pytest.mark.parametrize("pergunta", NAO_E_APTO)
def test_quem_pede_os_dados_nao_e_pergunta_de_apto(pergunta: str) -> None:
    assert not apto.pergunta_se_esta_apto(pergunta)


def test_resposta_de_apto_e_o_texto_do_dono_com_o_fecho() -> None:
    assert apto.resposta_de_apto(APTO, HOJE, repetida=False) == f"{TEXTO_DO_DONO_APTO} {_FECHO}"


@pytest.mark.parametrize(("view", "rotulo"), [(SUSPENSO, "Suspenso"), (CANCELADO, "Cancelado")])
def test_nao_apto_e_honesto_com_a_situacao_e_a_central(view: ElegibilidadeView, rotulo: str) -> None:
    texto = apto.resposta_de_apto(view, HOJE, repetida=False)
    assert texto.startswith(
        f"Pelo cadastro, o seu plano não está liberado para atendimento agora (situação: {rotulo})."
    )
    assert "central de atendimento do plano" in texto and "Sim." not in texto


def test_apto_com_atendimento_bloqueado_nao_e_liberado() -> None:
    bloqueado = _view(situacao_vinculo="Apto", atendimento_liberado=False)
    assert apto.leitura_de_apto(bloqueado, HOJE) == (False, " (situação: Apto)")


def test_sem_vinculo_e_o_texto_fixo_de_sem_vinculo() -> None:
    vazio = replace(ELEGIBILIDADE, vinculos=())
    assert apto.resposta_de_apto(vazio, HOJE, repetida=False) == RESPOSTA_SEM_VINCULO_ATIVO
    assert apto.resposta_de_apto(None, HOJE, repetida=False) == RESPOSTA_SEM_VINCULO_ATIVO


def test_repetida_e_so_a_conclusao_curta() -> None:
    curta = apto.resposta_de_apto(APTO, HOJE, repetida=True)
    assert (
        curta == "Como eu disse, pelo cadastro o seu plano está liberado para atendimento (situação: Apto)."
    )
    assert _FECHO not in curta


def test_ja_perguntou_apto_le_a_pergunta_e_o_marcador_seguinte() -> None:
    agora = "2026-10-21T15:00:00+00:00"
    historico = [
        {"papel": "beneficiario", "texto": PERGUNTAS_DE_APTO[0], "em": agora},
        {"papel": "helena", "texto": MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO, "em": agora},
    ]
    assert apto.ja_perguntou_apto(historico)
    # Controle: a mesma pergunta respondida com outro texto (sem dados do plano), ou outra pergunta do plano.
    assert not apto.ja_perguntou_apto([historico[0], {**historico[1], "texto": "Desculpe a confusão."}])
    assert not apto.ja_perguntou_apto([{**historico[0], "texto": "qual a minha carteirinha?"}, historico[1]])
    assert not apto.ja_perguntou_apto(None)


# --- 3. Pergunta de apto: no grafo (roteador ativo, como no teste do dono) ----------------------------------


@pytest.mark.parametrize("pergunta", PERGUNTAS_DE_APTO[:3])
async def test_apto_no_grafo_e_a_conclusao_sem_o_cadastro_inteiro(pergunta: str) -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG, _RASCUNHO_RECUSADO])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(pergunta))
    assert whatsapp.enviados[-1] == f"{TEXTO_DO_DONO_APTO} {_FECHO}"
    assert "segmentação" not in whatsapp.enviados[-1] and "acomodação" not in whatsapp.enviados[-1]
    assert fonte.pedidos == ["elegibilidade"]


async def test_nao_apto_no_grafo_aponta_a_central() -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG])
    grafo, whatsapp, _fonte = _grafo(modelo, SUSPENSO)
    await grafo.ainvoke(_entrada(PERGUNTAS_DE_APTO[1]))
    assert "não está liberado para atendimento agora (situação: Suspenso)" in whatsapp.enviados[-1]


async def test_apto_repetido_na_conversa_nao_repete_o_bloco() -> None:
    modelo = _Modelo([decisao_v7("plano")] * 2, [_CLASSIFY_ELEG, _CLASSIFY_ELEG])
    grafo, whatsapp, _fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada(PERGUNTAS_DE_APTO[0]))
    await grafo.ainvoke(_entrada(PERGUNTAS_DE_APTO[1]) | _memoria(r1))
    primeira, segunda = whatsapp.enviados[-2:]
    assert primeira.startswith(TEXTO_DO_DONO_APTO)
    assert segunda.startswith("Como eu disse") and segunda != primeira and _FECHO not in segunda


async def test_controle_pedido_dos_dados_continua_com_o_cadastro() -> None:
    modelo = _Modelo([decisao_v7("plano")], [_CLASSIFY_ELEG, ""])
    grafo, whatsapp, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("quais são os dados do meu plano?"))
    assert "segmentação" in whatsapp.enviados[-1] and not whatsapp.enviados[-1].startswith("Sim.")


# --- 4. "Nao entendi" reexplica a ultima resposta --------------------------------------------------------


@pytest.mark.parametrize("mensagem", NAO_ENTENDI)
def test_pedido_de_reexplicacao_e_reconhecido(mensagem: str) -> None:
    assert rx.pede_reexplicacao(mensagem)


@pytest.mark.parametrize("mensagem", NAO_E_REEXPLICACAO)
def test_pedido_novo_nao_e_reexplicacao(mensagem: str) -> None:
    assert not rx.pede_reexplicacao(mensagem)


@pytest.mark.parametrize("nao_entendi", NAO_ENTENDI[:3])
async def test_nao_entendi_depois_do_apto_reexplica_em_palavras_simples(nao_entendi: str) -> None:
    modelo = _Modelo([decisao_v7("plano"), decisao_v7("plano")], [_CLASSIFY_ELEG])
    grafo, whatsapp, _fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada(PERGUNTAS_DE_APTO[0]))
    outras_antes = modelo.outras
    await grafo.ainvoke(_entrada(nao_entendi) | _memoria(r1))
    texto = whatsapp.enviados[-1]
    assert texto.startswith(rx.PREFIXO) and "pode ser usado para atendimento (situação: Apto)" in texto
    assert "Sobre a situação" not in texto and modelo.outras == outras_antes  # sem modelo


@pytest.mark.parametrize("nao_entendi", NAO_ENTENDI[3:])
async def test_nao_entendi_depois_da_cobertura_reexplica_a_cobertura(nao_entendi: str) -> None:
    modelo = _Modelo([decisao_v7("cobertura")], [])
    grafo, whatsapp, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(nao_entendi, falas_da_helena=[TEXTO_COBERTURA_FORA]))
    assert whatsapp.enviados[-1] == rx.TEXTO_COBERTURA_SIMPLES


@pytest.mark.parametrize("nao_entendi", NAO_ENTENDI[:3])
async def test_nao_entendi_depois_da_saude_resume_a_propria_resposta(nao_entendi: str) -> None:
    resposta_de_saude = (
        "A febre é a temperatura do corpo acima de 37,8 graus. Ela costuma ser uma defesa do corpo contra "
        "infecções. Esta informação é geral e não substitui avaliação médica. "
        "Posso ajudar em mais alguma coisa?"
    )
    modelo = _Modelo([decisao_v7("saude")], [])
    grafo, whatsapp, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(nao_entendi, falas_da_helena=[resposta_de_saude]))
    texto = whatsapp.enviados[-1]
    assert texto == (
        f"{rx.PREFIXO}a febre é a temperatura do corpo acima de 37,8 graus. Ela costuma ser uma defesa do "
        "corpo contra infecções."
    )
    assert not LINK.search(texto)


async def test_nao_entendi_de_novo_oferece_uma_pessoa_sem_laco() -> None:
    modelo = _Modelo([decisao_v7("plano")] * 3, [_CLASSIFY_ELEG])
    grafo, whatsapp, _fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada(PERGUNTAS_DE_APTO[0]))
    r2 = await grafo.ainvoke(_entrada("não entendi") | _memoria(r1))
    await grafo.ainvoke(_entrada("como assim?") | _memoria(r2))
    assert whatsapp.enviados[-1] == rx.TEXTO_SEM_MAIS


def test_resumo_simples_sem_conteudo_e_none() -> None:
    assert rx.resumo_simples("Se quiser falar com uma pessoa da equipe, é só me pedir.") is None
    assert rx.resumo_simples("Entendi. Para isso, procure a rede do plano.") == (
        f"{rx.PREFIXO}para isso, procure a rede do plano."
    )


async def test_controle_pergunta_nova_com_como_assim_segue_o_caminho_de_sempre() -> None:
    modelo = _Modelo([decisao_v7("cobertura")], [])
    grafo, whatsapp, _fonte = _grafo(modelo)
    await grafo.ainvoke(
        _entrada("como assim não tem cobertura para parto?", falas_da_helena=[TEXTO_COBERTURA_FORA])
    )
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA


async def test_controle_sem_resposta_anterior_segue_o_caminho_de_sempre() -> None:
    modelo = _Modelo([decisao_v7("conversa")], [_classify(intent="information"), "Em que posso ajudar?"])
    grafo, whatsapp, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("não entendi"))
    assert not whatsapp.enviados[-1].startswith(rx.PREFIXO)


# --- 6. Onde baixar o aplicativo -------------------------------------------------------------------------


@pytest.mark.parametrize("mensagem", ONDE_BAIXAR)
def test_pergunta_de_onde_baixar_o_app_e_reconhecida(mensagem: str) -> None:
    assert app_mod.pergunta_onde_baixar_o_app(mensagem)


@pytest.mark.parametrize("mensagem", NAO_E_ONDE_BAIXAR)
def test_outra_pergunta_nao_e_onde_baixar(mensagem: str) -> None:
    assert not app_mod.pergunta_onde_baixar_o_app(mensagem)


def test_o_texto_do_app_esta_no_pacote_sem_link() -> None:
    dados = yaml.safe_load(
        (resolve_spec_dir() / "agents/helena/pacote/textos.yaml").read_text(encoding="utf-8")
    )
    assert dados["textos"][app_mod.CHAVE_APP_ONDE_BAIXAR] == app_mod.TEXTO_APP_ONDE_BAIXAR
    assert PADRAO[app_mod.CHAVE_APP_ONDE_BAIXAR] == app_mod.TEXTO_APP_ONDE_BAIXAR
    assert erros_dos_textos(dados["textos"]) == []
    # Controle: o validador recusa o mesmo texto com link.
    com_link = {app_mod.CHAVE_APP_ONDE_BAIXAR: "Baixe em play.google.com/store/apps"}
    assert erros_dos_textos(com_link)


@pytest.mark.parametrize("mensagem", ONDE_BAIXAR[:3])
async def test_onde_baixar_com_o_roteador_e_o_texto_do_pacote(mensagem: str) -> None:
    modelo = _Modelo([decisao_v7("plano")], [])
    grafo, whatsapp, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados[-1] == app_mod.TEXTO_APP_ONDE_BAIXAR
    assert fonte.pedidos == [] and modelo.outras == 0


@pytest.mark.parametrize(
    "classificacao",
    [_classify(intent="information"), _classify(intent="consulta_plano", consulta_subtipo="elegibilidade")],
)
async def test_onde_baixar_sem_o_roteador_tambem_e_o_texto_do_pacote(classificacao: str) -> None:
    grafo, whatsapp, _modelo, _cib = _grafo_sem_roteador([classificacao])
    await grafo.ainvoke(_entrada("Onde eu baixo esse aplicativo?"))
    assert whatsapp.enviados[-1] == app_mod.TEXTO_APP_ONDE_BAIXAR


async def test_controle_link_do_portal_continua_com_os_canais() -> None:
    grafo, whatsapp, _modelo, _cib = _grafo_sem_roteador([_classify(intent="information")])
    await grafo.ainvoke(_entrada("qual o link do portal?"))
    assert whatsapp.enviados[-1] != app_mod.TEXTO_APP_ONDE_BAIXAR
    assert "link" in whatsapp.enviados[-1]
