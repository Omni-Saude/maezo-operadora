"""Cobertura em MODO SIMPLES (decisao do dono de 22/10/2026, DL-0140): nunca consulta catalogo/AMH por
procedimento, nunca diz "consta"/"nao consta no Rol"; explica o geral e oferece a equipe (caso so' com "sim").

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.graph import HelenaGraph
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_cobertura_assistida import _busca, _FonteGrafo, _Item, _View
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _entrada, _starts, _WhatsApp
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _Modelo
from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte

_TODOS = {"40304361": _View((_Item("HEMOGRAMA"),)), "40808041": _View((_Item("TC TORAX"),))}


def _pedido(*procs: str, exclusao: str = "nenhuma") -> str:
    return json.dumps(
        {"procedimentos": list(procs), "tipo": "procedimento", "assunto": "cobertura", "urgencia": False,
         "para_quem": "titular", "quem_texto": "", "recem_nascido": False, "exclusao_legal": exclusao,
         "discorda": False}
    )  # fmt: skip


def _grafo(modelo: _Modelo, modo: str = "simples") -> tuple[Any, _WhatsApp, FakeCibSevenTransport, Any]:
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    fonte = _FonteGrafo(_TODOS)
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 22, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        catalogo_procedimentos=_busca(),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
        cobertura_modo=modo,
    )
    return grafo.compile_graph().compile(), whatsapp, cib, fonte


@pytest.mark.parametrize(
    ("fala", "termo"),
    [
        ("o plano cobre ressonância do joelho?", "ressonância do joelho"),
        ("vocês pagam uma endoscopia pra mim?", "endoscopia"),
        ("tenho direito a fazer hemograma pelo convênio?", "hemograma"),
        ("meu plano cobre o exame qwxzk?", "qwxzk"),
    ],
)
async def test_procedimento_recebe_o_geral_e_a_oferta_sem_consulta(fala: str, termo: str) -> None:
    modelo = _Modelo([_decisao("cobertura")], [_pedido(termo)])
    grafo, whatsapp, cib, fonte = _grafo(modelo)
    resultado = await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1]
    assert fonte.pedidos == [], "modo simples nunca consulta a AMH por procedimento"
    assert "a equipe confirma para você" in texto.lower() and texto.startswith(f"Sobre {termo}:")
    assert "consta" not in texto.lower(), "nunca 'consta'/'nao consta no Rol'"
    assert resultado["coleta_pendente"] == ca.PEND_EQUIPE and _starts(cib) == 0


async def test_lista_de_procedimentos_tambem_recebe_uma_oferta_so() -> None:
    modelo = _Modelo([_decisao("cobertura")], [_pedido("hemograma", "tomografia do tórax")])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("cobre hemograma e tomografia do tórax?"))
    assert fonte.pedidos == [] and whatsapp.enviados[-1].count("Quer que eu encaminhe?") == 1


@pytest.mark.parametrize("aceite", ["sim", "quero sim", "pode encaminhar sim"])
async def test_sim_a_oferta_abre_o_caso(aceite: str) -> None:
    modelo = _Modelo([_decisao("cobertura"), _decisao("cobertura", responde=True)], [_pedido("endoscopia")])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("o convênio paga endoscopia?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa") if k in r1}
    await grafo.ainvoke(_entrada(aceite) | memoria)
    assert _starts(cib) == 1


@pytest.mark.parametrize("recusa", ["não", "não precisa", "deixa pra lá"])
async def test_recusa_a_oferta_nao_abre_caso(recusa: str) -> None:
    modelo = _Modelo([_decisao("cobertura"), _decisao("cobertura", responde=True)], [_pedido("endoscopia")])
    grafo, whatsapp, cib, _fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("o convênio paga endoscopia?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa") if k in r1}
    await grafo.ainvoke(_entrada(recusa) | memoria)
    assert _starts(cib) == 0 and "Quer que eu encaminhe?" not in whatsapp.enviados[-1]


async def test_exclusao_por_lei_continua_com_o_texto_proprio() -> None:
    modelo = _Modelo([_decisao("cobertura")], [_pedido("aplicação de botox", exclusao="estetica")])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("o plano cobre botox só por estética?"))
    assert fonte.pedidos == [] and ca.TEXTO_EXCLUSAO["estetica"] in whatsapp.enviados[-1]


async def test_modo_consulta_continua_consultando() -> None:
    modelo = _Modelo([_decisao("cobertura")], [_pedido("hemograma"), ""])
    grafo, whatsapp, _cib, fonte = _grafo(modelo, modo="consulta")
    await grafo.ainvoke(_entrada("o convênio paga um hemograma pra mim?"))
    assert fonte.pedidos == ["40304361"] and "consta no Rol" in whatsapp.enviados[-1]


@pytest.mark.parametrize("queixa", ["não entendi nada", "como assim? você não respondeu", "ué, e aí?"])
async def test_reclamacao_depois_da_oferta_nunca_refaz_consulta(queixa: str) -> None:
    modelo = _Modelo([_decisao("cobertura"), _decisao("cobertura", responde=True)], [_pedido("hemograma")])
    grafo, whatsapp, cib, fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("o convênio paga hemograma?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa") if k in r1}
    await grafo.ainvoke(_entrada(queixa) | memoria)
    assert fonte.pedidos == [] and _starts(cib) == 0
    assert "consta" not in whatsapp.enviados[-1].lower()


async def test_lista_mista_tem_uma_linha_por_item_de_regra_propria() -> None:
    pedido = json.dumps(
        {"procedimentos": ["ultrassom de mama"], "tipo": "procedimento", "assunto": "cobertura",
         "urgencia": False, "para_quem": "titular", "quem_texto": "", "recem_nascido": False,
         "exclusao_legal": "estetica", "discorda": False,
         "outros_itens": [{"nome": "omeprazol", "tipo": "remedio"},
                          {"nome": "preenchimento labial", "tipo": "estetica"}]}
    )  # fmt: skip
    modelo = _Modelo([_decisao("cobertura")], [pedido])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("o plano cobre ultrassom de mama, omeprazol e preenchimento labial?"))
    texto = whatsapp.enviados[-1].lower()
    assert fonte.pedidos == []
    assert "omeprazol" in texto and "preenchimento" in texto and "ultrassom de mama" in texto
    assert texto.count("quer que eu encaminhe") == 1


@pytest.mark.parametrize(
    "correcao", ["é pra mim mesmo, me confundi", "na verdade é para mim", "é pro meu filho"]
)
async def test_correcao_de_para_quem_depois_da_oferta_mantem_o_pedido(correcao: str) -> None:
    para_quem = "dependente" if "filho" in correcao else "titular"
    modelo = _Modelo(
        [_decisao("cobertura"), _decisao("cobertura", responde=True)],
        [_pedido("endoscopia"), json.dumps({"procedimentos": [], "tipo": "nenhum", "assunto": "cobertura",
                                            "urgencia": False, "para_quem": para_quem, "quem_texto": "",
                                            "recem_nascido": False, "exclusao_legal": "nenhuma",
                                            "discorda": False})],
    )  # fmt: skip
    grafo, whatsapp, cib, fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("o convênio paga endoscopia?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa") if k in r1}
    await grafo.ainvoke(_entrada(correcao) | memoria)
    assert fonte.pedidos == [] and _starts(cib) == 0
    assert whatsapp.enviados[-1].startswith("Sobre endoscopia:")


@pytest.mark.parametrize("queixa", ["não entendi", "não entendi nada", "como assim?"])
async def test_nao_entendi_depois_da_oferta_recebe_o_resumo_nunca_o_mesmo_bloco(queixa: str) -> None:
    modelo = _Modelo([_decisao("cobertura"), _decisao("cobertura", responde=True)], [_pedido("hemograma")])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("o convênio paga hemograma?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa") if k in r1}
    await grafo.ainvoke(_entrada(queixa) | memoria)
    assert whatsapp.enviados[-1] == ca.TEXTO_SIMPLES_RESUMO != whatsapp.enviados[-2]
    assert fonte.pedidos == []
