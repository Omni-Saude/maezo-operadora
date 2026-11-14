"""COBERTURA FORA da Helena (decisao do dono de 23/10/2026, DL-0154; Plano de ajustes v2, etapa 5).

Toda pergunta de cobertura recebe SO' o aviso com os canais da empresa: sem consulta ao cadastro, sem Rol nem
carencia, sem oferta da equipe e sem caso. As travas continuam vencendo (emergencia, risco) e a cobranca
continua com o Lucas. Os DADOS do plano (carencia, carteirinha, dependentes) continuam respondidos.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador nem da avaliacao)."""

from __future__ import annotations

import json

import pytest

from maezo.agents.helena import graph
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.agents.helena.textos import PADRAO
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _Fonte, _grafo, _starts
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _Modelo
from tests.unit.agents.test_helena_roteador_ativo import _grafo as _grafo_roteador


def test_o_aviso_e_o_texto_do_dono_palavra_por_palavra() -> None:
    assert TEXTO_COBERTURA_FORA == (
        "Por aqui eu não trato de cobertura. Você consegue essa informação pelo aplicativo Austa Clínicas, "
        "pelo portal do plano ou pela central de atendimento do plano."
    )
    assert PADRAO["cobertura_fora"] == TEXTO_COBERTURA_FORA
    for texto in (graph.RESPOSTA_COBERTURA_ESPECIFICA, graph.RESPOSTA_COBERTURA_ESPECIFICA_2):
        assert texto == TEXTO_COBERTURA_FORA
    assert "Rol" not in TEXTO_COBERTURA_FORA and "carência" not in TEXTO_COBERTURA_FORA
    assert "equipe" not in TEXTO_COBERTURA_FORA, "sem oferta da equipe"


@pytest.mark.parametrize(
    "mensagem",
    [
        "o plano libera fisioterapia pra joelho?",
        "tenho direito a fazer uma ressonância pelo convênio?",
        "vocês cobrem cirurgia de catarata?",
        "meu convenio paga exame de vista?",
    ],
)
async def test_pergunta_de_cobertura_recebe_so_o_aviso_sem_consulta_nem_caso(mensagem: str) -> None:
    fonte = _Fonte()
    grafo, whatsapp, modelo, cib = _grafo([_classify(intent="consulta_plano")], fonte=fonte)
    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
    assert fonte.chamadas == [], "nenhuma consulta ao cadastro"
    assert _starts(cib) == 0, "nenhum caso"
    assert modelo.prompts == [], "o aviso sai sem chamar o modelo"


@pytest.mark.parametrize(
    "mensagem",
    [
        "o plano cobre? estou com dor no peito forte e falta de ar agora",
        "meu filho desmaiou aqui, o convenio paga o pronto socorro?",
        "to sangrando muito, tenho direito a atendimento pelo plano?",
    ],
)
async def test_emergencia_vence_o_aviso_de_cobertura(mensagem: str) -> None:
    grafo, whatsapp, _modelo, _cib = _grafo([_classify(intent="symptom", intensidade="grave")])
    await grafo.ainvoke(_entrada(mensagem))
    assert "192" in whatsapp.enviados[-1]
    assert whatsapp.enviados[-1] != TEXTO_COBERTURA_FORA


@pytest.mark.parametrize(
    "mensagem",
    [
        "quero a segunda via do boleto e saber se o plano cobre acupuntura",
        "quando vence a mensalidade? e voces cobrem ortodontia?",
        "manda o boleto desse mes, e o convenio cobre exame de sangue?",
    ],
)
async def test_cobranca_com_cobertura_vai_ao_lucas_com_o_aviso(mensagem: str) -> None:
    grafo, whatsapp, _modelo, _cib = _grafo([_classify(intent="cobranca", cobranca_subtipo="segunda_via")])
    resultado = await grafo.ainvoke(_entrada(mensagem))
    assert resultado.get("handoff"), "a cobranca continua com o Lucas"
    assert TEXTO_COBERTURA_FORA in (resultado.get("response_text") or "")


@pytest.mark.parametrize(
    "mensagem",
    [
        "e o plano autoriza endoscopia?",
        "voces pagam a vacina da gripe?",
        "consigo fazer o pré-natal coberto?",
    ],
)
async def test_roteador_ativo_escolhendo_cobertura_e_o_aviso(mensagem: str) -> None:
    modelo = _Modelo([_decisao("cobertura")], [])
    grafo, whatsapp, cib, fonte = _grafo_roteador(modelo)
    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
    assert fonte.pedidos == [] and _starts(cib) == 0 and modelo.outras == 0


async def test_o_texto_do_pacote_vence_o_padrao() -> None:
    from datetime import UTC, datetime

    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink
    from tests.unit.agents.test_helena_rodada_3_simulador import _Modelo as _ModeloFila
    from tests.unit.agents.test_helena_rodada_3_simulador import _WhatsApp

    whatsapp = _WhatsApp()
    grafo = graph.HelenaGraph(
        inference=_ModeloFila([]),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=_Fonte(),
        relogio=lambda: datetime(2026, 10, 23, 15, 0, tzinfo=UTC),
        textos={"cobertura_fora": "Aviso do pacote sobre cobertura."},
    )
    await grafo.compile_graph().compile().ainvoke(_entrada("o plano cobre implante dentário?"))
    assert whatsapp.enviados[-1] == "Aviso do pacote sobre cobertura."


async def test_dado_do_plano_continua_respondido() -> None:
    """Carencia do proprio titular e' dado do plano, nao cobertura: continua com a consulta."""
    fonte = _Fonte()
    resposta = json.loads(_classify(intent="consulta_plano", consulta_subtipo="carencia"))
    grafo, whatsapp, _modelo, _cib = _grafo([json.dumps(resposta)], fonte=fonte)
    await grafo.ainvoke(_entrada("ainda estou em carencia de alguma coisa?"))
    assert fonte.chamadas, "a carencia continua consultada no cadastro"
    assert whatsapp.enviados[-1] != TEXTO_COBERTURA_FORA


def test_os_modulos_da_cobertura_sairam() -> None:
    import importlib.util

    for modulo in ("cobertura_assistida", "cobertura_fluxo", "cobertura_rol"):
        assert importlib.util.find_spec(f"maezo.agents.helena.{modulo}") is None, modulo
