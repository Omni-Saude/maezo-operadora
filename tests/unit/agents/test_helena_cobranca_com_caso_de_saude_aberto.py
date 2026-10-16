"""`dono_v1`, segmento 5 (16/10/2026): COBRANCA COM CASO DE SAUDE ABERTO VAI AO LUCAS, NAO IMPORTA O MODELO.

Falha intermitente medida no dev: depois do caso clinico do recem-nascido, "e a minha mensalidade de outubro?"
saiu, em uma de varias execucoes, `symptom` do modelo (com o sintoma do HISTORICO). A tabela de alarme abriu
`red_flag_clinico` e a pessoa leu o texto de EMERGENCIA (192) para uma pergunta de mensalidade. Decisao do dono
(12/10): caso de saude aberto NAO trava a conversa — cobranca segue com o Lucas.
"""

# ruff: noqa: E501
from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import (
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_CASO_ABERTO_EMERGENCIA,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    HelenaGraph,
)
from maezo.runtime.cobranca_esclarecimento import e_mensagem_curta_sem_ruido_de_cobranca
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_2_simulador import (
    _caso_aberto,
    _classify,
    _dmn,
    _entrada,
    _Modelo,
    _starts,
    _WhatsApp,
)

_MENSALIDADE = "e a minha mensalidade de outubro?"
_FALAS_DO_CASO_CLINICO = [RESPOSTA_ESCALONAMENTO_URGENTE, RESPOSTA_CASO_ABERTO_EMERGENCIA]

#: As chaves da Helena v4 e da triagem nova (a v4 desliga a v2): ligadas e desligadas.
_CHAVES = [
    pytest.param({}, id="v4_desligada"),
    pytest.param({"helena_v4_enabled": True}, id="v4_ligada"),
    pytest.param({"triagem_v2_enabled": True}, id="triagem_v2_ligada"),
]


def _grafo(
    respostas: list[str], *, cib: FakeCibSevenTransport | None = None, **chaves: Any
) -> tuple[Any, _WhatsApp, _Modelo, FakeCibSevenTransport]:
    whatsapp = _WhatsApp()
    modelo = _Modelo(respostas)
    cibseven = cib or FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_dmn(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        **chaves,
    )
    return grafo.compile_graph().compile(), whatsapp, modelo, cibseven


def _texto_de_emergencia(enviados: list[str]) -> bool:
    return any("192" in t or "SAMU" in t for t in enviados)


# (a) caso aberto + cobranca + modelo hostil -> Lucas, nunca emergencia ----------------------------------


@pytest.mark.parametrize("chaves", _CHAVES)
@pytest.mark.parametrize(
    "resposta_hostil",
    [
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica"),
        _classify(
            intent="symptom", population="pediatric", sintoma_codigo="dor_toracica", intensidade="grave"
        ),
        _classify(intent="emergency"),  # fora do dominio: classificador "falhou"
        _classify(intent="clinical_question", sintoma_codigo="dor_toracica"),
        "isto nao e json",
    ],
)
@pytest.mark.parametrize(
    "mensagem",
    [_MENSALIDADE, "e o boleto de outubro?", "qual o valor da mensalidade?", "quando vence a mensalidade?"],
)
async def test_cobranca_com_caso_clinico_aberto_vai_ao_lucas_com_modelo_hostil(
    chaves: dict[str, Any], resposta_hostil: str, mensagem: str
) -> None:
    cib = _caso_aberto("red_flag_clinico")
    antes = _starts(cib)
    grafo, whatsapp, modelo, _ = _grafo([resposta_hostil], cib=cib, **chaves)

    resultado = await grafo.ainvoke(
        _entrada(mensagem, _FALAS_DO_CASO_CLINICO, sinal_saude_lexico=False, pedido_humano_lexico=False)
    )

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["intent"] == "cobranca"
    assert resultado.get("handoff") is not None  # a fence `check_roteador_lucas`: o handoff literal
    assert resultado.get("escalation_motivo") in (None, "")
    assert not _texto_de_emergencia(whatsapp.enviados)
    assert modelo.prompts == [], "decidido antes do modelo"
    assert _starts(cib) == antes, "nenhum caso novo"


async def test_a_competencia_do_texto_vai_no_handoff() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="symptom", sintoma_codigo="dor_toracica")])

    resultado = await grafo.ainvoke(
        _entrada(_MENSALIDADE, _FALAS_DO_CASO_CLINICO, sinal_saude_lexico=False, pedido_humano_lexico=False)
    )

    assert resultado["handoff"]["competencia"] == "2026-10"


# (b) emergencia/saude na PROPRIA mensagem continua vencendo ---------------------------------------------


@pytest.mark.parametrize("chaves", _CHAVES)
@pytest.mark.parametrize(
    "mensagem",
    [
        "meu bebê está roxo, e a mensalidade?",
        "ele ta parando de respirar, e o boleto?",
        "liguei pro 192, e a mensalidade?",
        "meu filho está com febre, e a mensalidade?",
    ],
)
async def test_emergencia_na_propria_mensagem_continua_vencendo(
    chaves: dict[str, Any], mensagem: str
) -> None:
    cib = _caso_aberto("red_flag_clinico")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="symptom", population="pediatric", sintoma_codigo="dor_toracica")],
        cib=cib,
        **chaves,
    )

    resultado = await grafo.ainvoke(
        _entrada(mensagem, _FALAS_DO_CASO_CLINICO, sinal_saude_lexico=True, pedido_humano_lexico=False)
    )

    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF
    assert resultado.get("handoff") is None
    assert _texto_de_emergencia(whatsapp.enviados)
    assert _starts(cib) == antes


async def test_emergencia_no_texto_vence_mesmo_sem_o_lexico_do_roteador() -> None:
    """A cerca do grafo nao depende so' do lexico do roteador: o sinal forte no texto basta."""
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], cib=_caso_aberto("red_flag_clinico"))

    resultado = await grafo.ainvoke(
        _entrada(
            "ele ta roxo, e a minha mensalidade de outubro?",
            _FALAS_DO_CASO_CLINICO,
            sinal_saude_lexico=False,
            pedido_humano_lexico=False,
        )
    )

    assert resultado.get("handoff") is None
    assert whatsapp.enviados[0].startswith("Ligue agora para o 192 (SAMU)")


# (c) sem caso aberto: o fluxo normal nao muda ----------------------------------------------------------


@pytest.mark.parametrize("chaves", _CHAVES)
async def test_sem_caso_aberto_o_modelo_continua_decidindo(chaves: dict[str, Any]) -> None:
    grafo, _, modelo, _ = _grafo(
        [_classify(intent="cobranca", cobranca_subtipo="consulta_valores")], **chaves
    )

    resultado = await grafo.ainvoke(
        _entrada(_MENSALIDADE, sinal_saude_lexico=False, pedido_humano_lexico=False)
    )

    assert len(modelo.prompts) == 1, "sem caso aberto o modelo e' consultado como sempre"
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF


async def test_sem_caso_aberto_a_extracao_de_sintoma_do_modelo_segue_a_tabela() -> None:
    grafo, _, modelo, _ = _grafo([_classify(intent="symptom", sintoma_codigo="dor_toracica")])

    resultado = await grafo.ainvoke(
        _entrada(_MENSALIDADE, sinal_saude_lexico=False, pedido_humano_lexico=False)
    )

    assert len(modelo.prompts) >= 1
    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF


async def test_caso_administrativo_aberto_nao_aciona_a_regra() -> None:
    """So' o caso de SAUDE aberto aciona o caminho deterministico (o administrativo ja' tem o dele)."""
    from maezo.agents.helena.graph import RESPOSTA_HANDOFF_JA_ABERTO

    grafo, _, modelo, _ = _grafo([_classify(intent="cobranca", cobranca_subtipo="consulta_valores")])

    await grafo.ainvoke(
        _entrada(
            _MENSALIDADE,
            [RESPOSTA_HANDOFF_JA_ABERTO],
            sinal_saude_lexico=False,
            pedido_humano_lexico=False,
        )
    )

    assert len(modelo.prompts) == 1


# Os limites da regra: o que NAO e' cobranca limpa segue o caminho de sempre ----------------------------


@pytest.mark.parametrize(
    "mensagem",
    [
        "o plano cobre a consulta do bebê?",  # pista de plano
        "meu filho está com dor, e a mensalidade?",  # pessoa/saude
        "quero cancelar o plano, e a mensalidade?",  # cancelamento + plano
        "o boleto está errado, fui cobrado duas vezes",  # discordancia: contestacao e' do modelo
        "quero falar com um atendente sobre o boleto",  # pedido de pessoa
        "quanto tempo demora o meu caso? e a mensalidade?",  # pergunta sobre o caso
        "oi",  # sem lexico de cobranca
    ],
)
async def test_o_que_nao_e_cobranca_limpa_segue_o_caminho_de_sempre(mensagem: str) -> None:
    grafo, _, modelo, _ = _grafo([_classify(intent="information")], cib=_caso_aberto("red_flag_clinico"))

    await grafo.ainvoke(
        _entrada(mensagem, _FALAS_DO_CASO_CLINICO, sinal_saude_lexico=False, pedido_humano_lexico=False)
    )

    assert len(modelo.prompts) >= 1, "o modelo foi consultado: a regra deterministica nao acionou"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (_MENSALIDADE, True),
        ("qual o valor do boleto?", True),
        ("e o bebê?", False),
        ("o plano cobre?", False),
        ("está errado, quero contestar a mensalidade", False),
        ("quero cancelar a mensalidade", False),
        ("a " * 20 + "mensalidade", False),
        ("", False),
        (None, False),
    ],
)
def test_e_mensagem_curta_sem_ruido_de_cobranca(texto: object, esperado: bool) -> None:
    assert e_mensagem_curta_sem_ruido_de_cobranca(texto) is esperado
