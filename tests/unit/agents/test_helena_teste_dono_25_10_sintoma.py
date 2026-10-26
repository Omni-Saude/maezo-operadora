"""Teste do dono (25/10/2026) e avaliacao r5, itens de SINTOMA: a pergunta de alerta nao se repete para a
mesma queixa (1, 8, 12), "ja' respondi que nao" segue sem as mesmas opcoes (2), sem o eco literal (5), a queda
com as perguntas proprias (7), a falha do roteador rotulada pela causa (8) e o "mantive o seu caso" que nao
engole a pergunta de plano (14).

Frases escritas para cada comportamento (nenhuma copiada do teste do dono nem do conjunto da avaliacao)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena import helena_v4 as v4
from maezo.agents.helena import roteador as rt
from maezo.agents.helena.graph import RESPOSTA_ESCALONAMENTO_URGENTE, HelenaGraph
from maezo.agents.helena.habilidades.sintoma import ja_respondido as jr
from maezo.agents.helena.habilidades.sintoma.textos import TEXTO_AVALIAR_CAUSA, TEXTO_CASO_MANTIDO
from maezo.conhecimento import base_saude as bs
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _classify, _entrada, _WhatsApp
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _FonteGrafo, _Modelo
from tests.unit.agents.test_helena_saude_gerativa import _Transporte
from tests.unit.agents.test_helena_v4 import _DmnReal

_BASE_REAL = Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "saude"
_FINAL_ADULTO = v4.texto_final_do_sintoma("x", v4.POPULACAO_ADULTO)
_FINAL_CRIANCA = v4.texto_final_do_sintoma("x", v4.POPULACAO_CRIANCA)
_PERGUNTA_GERAL = v4.texto_da_pergunta_de_alerta(v4.GRUPO_GERAL, "x")


def _grafo(modelo: _Modelo) -> tuple[Any, _WhatsApp]:
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        coleta_enabled=True,
        helena_v4_enabled=True,
        consultas_plano=_FonteGrafo(),  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 25, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        helena_base_saude_enabled=True,
        base_saude=bs.carregar_base(_BASE_REAL),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
    )
    return grafo.compile_graph().compile(), whatsapp


def _sem_pergunta_de_alerta(texto: str) -> None:
    baixo = texto.lower()
    assert "responda sim ou não" not in baixo and "responda sim ou nao" not in baixo
    assert "antes de te orientar" not in baixo and "antes de te mostrar o caminho" not in baixo
    assert "manchinhas" not in baixo


# --- Item 5: sem o eco literal ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "relato",
    [
        "eu sofri uma queda de uma escada a alguns dias atras",
        "estou com dor nas costas desde ontem",
        "meu filho esta com tosse faz dois dias",
    ],
)
def test_os_textos_do_sintoma_nao_repetem_a_frase_da_pessoa(relato: str) -> None:
    eco = v4.eco_do_relato(relato)
    for texto in (
        v4.texto_da_pergunta_de_alerta(v4.GRUPO_GERAL, eco),
        v4.texto_final_do_sintoma(eco, v4.POPULACAO_ADULTO),
        v4.texto_final_do_sintoma(eco, v4.POPULACAO_CRIANCA),
    ):
        assert "Entendi:" not in texto and eco not in texto
        assert texto.startswith("Entendi. ")
    assert v4.e_texto_da_v4(v4.texto_final_do_sintoma(eco, v4.POPULACAO_ADULTO))
    # O formato antigo (no historico) continua reconhecido.
    assert v4.e_texto_da_v4(_FINAL_ADULTO.replace("Entendi.", f"Entendi: {eco}.", 1))


# --- Item 7: a queda tem perguntas proprias -------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "eu sofri uma queda de uma escada a alguns dias atras",
        "levei um tombo no banheiro ontem",
        "escorreguei e cai na calcada hoje cedo",
    ],
)
def test_a_queda_acha_o_arquivo_da_queda(fala: str) -> None:
    base = bs.carregar_base(_BASE_REAL)
    assert bs.buscar(base, fala, categorias=(bs.CATEGORIA_SINTOMA,)).achado == "sin-queda"
    queda = base.get("sin-queda")
    assert queda is not None and queda.revisao.revisao_medica == "pendente"
    assert all("peito" not in p for p in queda.perguntas_alerta or ())


@pytest.mark.parametrize("fala", ["estou com queda de cabelo", "tive queda de pressao", "a queda do dolar"])
def test_queda_que_nao_e_tombo_nao_acha_a_queda(fala: str) -> None:
    base = bs.carregar_base(_BASE_REAL)
    assert "sin-queda" not in {a.id for a in bs.sintomas_citados(base, fala)}


@pytest.mark.parametrize(
    ("fala", "esperado"),
    [
        ("cai da escada e bati a cabeca", "sin-batida-na-cabeca"),
        ("levei um tombo e bati a testa no chao", "sin-batida-na-cabeca"),
        ("cai da escada e estou com dor nas costas", "sin-queda"),
        ("sofri uma queda e agora estou com dor lombar", "sin-queda"),
    ],
)
def test_precedencia_queda_dor_e_batida_na_cabeca(fala: str, esperado: str) -> None:
    grafo = HelenaGraph(
        inference=_Modelo([], []),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=bs.carregar_base(_BASE_REAL),
    )
    assunto = grafo._base_sintoma_do_texto(fala, v4.POPULACAO_ADULTO)
    assert assunto is not None and assunto.id == esperado


# --- Itens 1 e 2: o alerta ja' respondido nao se repete -------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "Eu já posso procurar um médico para agendar uma consulta?",
        "e agora, onde eu marco a consulta?",
        "que médico eu procuro pra isso?",
    ],
)
async def test_pergunta_de_agendamento_depois_do_nao_recebe_o_caminho(fala: str) -> None:
    modelo = _Modelo([_decisao("sintoma")], [_classify(intent="symptom", intensidade="moderada")] * 3)
    grafo, whatsapp = _grafo(modelo)
    estado = _entrada(fala, [_PERGUNTA_GERAL, _FINAL_ADULTO])
    await grafo.ainvoke(estado)
    texto = whatsapp.enviados[-1]
    _sem_pergunta_de_alerta(texto)
    assert "aplicativo Austa Clínicas" in texto and "dias úteis" not in texto
    assert "Você prefere: 1)" not in texto


@pytest.mark.parametrize(
    "fala",
    [
        "a dor nas costas continua do mesmo jeito",
        "ainda estou com essa dor",
        "continua doendo, o que faço?",
    ],
)
async def test_mesma_queixa_depois_do_nao_nao_repete_a_pergunta(fala: str) -> None:
    modelo = _Modelo(
        [_decisao("sintoma", responde=True)], [_classify(intent="symptom", intensidade="moderada")] * 3
    )
    grafo, whatsapp = _grafo(modelo)
    estado = _entrada(fala, [_PERGUNTA_GERAL, _FINAL_ADULTO])
    await grafo.ainvoke(estado)
    _sem_pergunta_de_alerta(whatsapp.enviados[-1])


async def test_controle_sem_alerta_respondido_a_pergunta_de_alerta_sai() -> None:
    modelo = _Modelo([_decisao("sintoma")], [_classify(intent="symptom", intensidade="moderada")] * 3)
    grafo, whatsapp = _grafo(modelo)
    await grafo.ainvoke(_entrada("estou com dor nas costas desde ontem"))
    assert "sim ou não" in whatsapp.enviados[-1].lower()


@pytest.mark.parametrize(
    "fala",
    [
        "Eu já tinha te respondido que não sobre isso.",
        "já falei que não",
        "eu já te disse, não tenho nada disso",
    ],
)
async def test_nao_com_reclamacao_da_repeticao_recebe_desculpa_e_caminho(fala: str) -> None:
    modelo = _Modelo(
        [_decisao("sintoma", responde=True, sim_nao="nao")],
        [_classify(intent="symptom", intensidade="moderada")] * 3,
    )
    grafo, whatsapp = _grafo(modelo)
    estado = _entrada(fala, [_PERGUNTA_GERAL, _FINAL_ADULTO, _PERGUNTA_GERAL]) | {
        "coleta_pendente": v4.codigo_pendente(v4.ETAPA_ALERTA, v4.GRUPO_GERAL, v4.POPULACAO_ADULTO),
        "coleta_contexto": "dor nas costas depois de uma queda",
    }
    await grafo.ainvoke(estado)
    texto = whatsapp.enviados[-1]
    assert texto.startswith(v4.DESCULPA_DA_REPETICAO)
    assert "Você prefere: 1)" not in texto and "Entendi:" not in texto


def test_reclamacao_da_repeticao_e_agendamento_controles() -> None:
    assert not jr.reclama_da_repeticao("não")
    assert not jr.reclama_da_repeticao("nao tenho nada disso")
    assert not jr.pergunta_de_agendamento("estou com dor nas costas")
    assert jr.alerta_ja_respondido([_FINAL_ADULTO]) and not jr.alerta_ja_respondido([_PERGUNTA_GERAL])
    assert jr.populacao_do_alerta_respondido([_FINAL_CRIANCA], crianca="c", adulto="a") == "c"


# --- Item 12: depois do 192, nunca outro conjunto de perguntas ------------------------------------------
@pytest.mark.parametrize(
    "fala",
    ["ela tá chorando mas parece bem", "ele tá mais calmo agora", "ok ja liguei"],
)
async def test_depois_do_192_a_continuacao_mantem_o_caso(fala: str) -> None:
    modelo = _Modelo(
        [_decisao("sintoma", responde=True)],
        [_classify(intent="symptom", population="pediatric", intensidade="leve")] * 3,
    )
    grafo, whatsapp = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala, [RESPOSTA_ESCALONAMENTO_URGENTE]))
    texto = whatsapp.enviados[-1]
    _sem_pergunta_de_alerta(texto)
    assert texto == TEXTO_CASO_MANTIDO


# --- Item 8: sem a leitura do roteador, a pergunta pendente nao reinicia --------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "Começou hoje de manhã, já tomei dorflex e não melhorou",
        "tomei remédio e continua igual",
        "desde ontem, já fiz compressa e não passou",
    ],
)
async def test_falha_do_roteador_com_alerta_pendente_repergunta_e_nao_reinicia(fala: str) -> None:
    modelo = _Modelo(["", ""], [_classify(intent="symptom", intensidade="moderada")] * 3)
    grafo, whatsapp = _grafo(modelo)
    estado = _entrada(fala, [v4.texto_da_pergunta_de_alerta("sin-dor-de-cabeca", "x")]) | {
        "coleta_pendente": v4.codigo_pendente(v4.ETAPA_ALERTA, "sin-dor-de-cabeca", v4.POPULACAO_ADULTO),
        "coleta_contexto": "estou com dor de cabeca",
    }
    await grafo.ainvoke(estado)
    texto = whatsapp.enviados[-1].lower()
    assert "antes de te orientar" not in texto and "antes de te mostrar" not in texto


class ReadTimeoutError(Exception):
    pass


class ConnectTimeoutError(Exception):
    pass


@pytest.mark.parametrize("erro", [ReadTimeoutError("x"), ConnectTimeoutError("x"), TimeoutError("x")])
def test_prazo_do_provedor_e_rotulado_como_prazo(erro: Exception) -> None:
    assert rt._e_erro_de_prazo(type(erro).__name__)


@pytest.mark.parametrize("erro", [ConnectionError("recusada"), ValueError("json"), RuntimeError("provedor")])
def test_erro_que_nao_e_prazo_continua_modelo(erro: Exception) -> None:
    assert not rt._e_erro_de_prazo(type(erro).__name__)


# --- Item 14: com o caso aberto, a pergunta de plano/cobertura/conversa e' respondida -------------------
@pytest.mark.parametrize(
    ("fala", "habilidade"),
    [
        ("precisa de autorização no pronto socorro?", "plano"),
        ("o plano cobre a ambulância?", "cobertura"),
        ("obrigado por me ouvir", "conversa"),
    ],
)
async def test_caso_mantido_nao_engole_plano_cobertura_e_conversa(fala: str, habilidade: str) -> None:
    modelo = _Modelo(
        [_decisao(habilidade)],
        [_classify(intent="consulta_plano" if habilidade == "plano" else "information")] * 3,
    )
    grafo, whatsapp = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala, [RESPOSTA_ESCALONAMENTO_URGENTE]))
    assert whatsapp.enviados[-1] != TEXTO_CASO_MANTIDO


def test_avaliar_causa_continua_no_vocabulario() -> None:
    assert "médico" in TEXTO_AVALIAR_CAUSA
