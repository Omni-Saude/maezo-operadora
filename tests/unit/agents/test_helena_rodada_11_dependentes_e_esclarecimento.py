"""DL-0098 — rodada 11.

1. SEGUIMENTO CURTO COM O LUCAS ATIVO VAI AO LUCAS, NAO IMPORTA O MODELO. No teste real do dono, "de qual
   ano?" logo depois do Lucas foi ao Lucas em duas execucoes e, na terceira, o modelo devolveu
   `consulta_plano`/`elegibilidade` e a Helena respondeu a vigencia do plano.
2. DEPENDENTES: o quinto subtipo da consulta do plano (`interop.tina.get_dependentes`), resposta
   deterministica (contagem e relacao; nunca nome, CPF ou nascimento).
"""

# ruff: noqa: E501
from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.consultas_plano import (
    RESPOSTA_DEPENDENTES_FALHA,
    RESPOSTA_DEPENDENTES_NAO_TITULAR,
    RESPOSTA_DEPENDENTES_NENHUM,
    FonteIndisponivel,
    fatos_da_consulta,
    resposta_deterministica,
)
from maezo.agents.helena.graph import RESPONSE_KIND_HANDOFF, RESPOSTA_DEPENDENTES, HelenaGraph
from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import DependenteAnonimo, DependentesView
from maezo.runtime.cobranca_esclarecimento import e_seguimento_de_cobranca
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_3_simulador import (
    REF,
    _classify,
    _entrada,
    _grafo,
    _Modelo,
    _starts,
    _WhatsApp,
)

_SEM_NOMES = "Por segurança, este canal não mostra nomes nem outros dados deles."


class _FonteDependentes:
    def __init__(self, resultado: Any) -> None:
        self._resultado = resultado
        self.chamadas: list[str] = []

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append(subtipo)
        return self._resultado


def _visao(
    *relacoes: tuple[str, bool], titular: bool = True, total: int | None = None, truncada: bool = False
):
    return DependentesView(
        portable_subject_ref=REF,
        eh_titular=titular,
        total_ativos=len(relacoes) if total is None else total,
        lista_truncada=truncada,
        dependentes=tuple(
            DependenteAnonimo(relacao_dependencia=r, situacao_vinculo="Apto", rn_no_plano=rn)
            for r, rn in relacoes
        ),
        fonte_atualizada_em="2026-10-13",
    )


# =================================================================================================
# 1. Seguimento curto com o Lucas ativo
# =================================================================================================

SEGUIMENTOS = [
    "de qual ano?",
    "como assim?",
    "não entendi",
    "e a de outubro?",
    "quanto foi?",
    "e o vencimento?",
    "foi paga?",
]


@pytest.mark.parametrize("mensagem", SEGUIMENTOS)
@pytest.mark.parametrize(
    "resposta_do_modelo",
    [
        _classify(intent="consulta_plano", consulta_subtipo="elegibilidade"),
        _classify(intent="information"),
        _classify(intent="greeting"),
        _classify(intent="outside_channel"),
        _classify(intent="outro"),  # fora do dominio: classificador falhou
        "isto nao e json",
    ],
)
async def test_seguimento_com_o_lucas_ativo_vai_ao_lucas_qualquer_que_seja_a_intencao(
    mensagem: str, resposta_do_modelo: str
) -> None:
    grafo, whatsapp, _, _ = _grafo([resposta_do_modelo])

    resultado = await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["intent"] == "cobranca"
    assert resultado.get("handoff") is not None
    assert whatsapp.enviados == [] or "vigência" not in whatsapp.enviados[0]


async def test_seguimento_com_mes_leva_a_competencia_do_texto() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="consulta_plano", consulta_subtipo="elegibilidade")])

    resultado = await grafo.ainvoke(_entrada("e a de agosto?", agente_ativo_conversa="lucas"))

    assert resultado["handoff"]["competencia"] == "2026-08"


@pytest.mark.parametrize(
    "mensagem",
    [
        "de qual ano começa o meu plano?",
        "como assim minha carência?",
        "e a carteirinha?",
        "e a cobertura de parto?",
        "e meus dependentes?",
        "de qual ano é a autorização?",
    ],
)
async def test_seguimento_com_pista_de_plano_segue_o_caminho_normal(mensagem: str) -> None:
    fonte = _FonteDependentes(None)
    grafo, _, _, _ = _grafo(
        [_classify(intent="consulta_plano", consulta_subtipo="elegibilidade")], fonte=fonte
    )  # type: ignore[arg-type]

    resultado = await grafo.ainvoke(_entrada(mensagem, agente_ativo_conversa="lucas"))

    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF


async def test_sem_o_lucas_ativo_de_qual_ano_nao_e_seguimento() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="information")])

    resultado = await grafo.ainvoke(_entrada("de qual ano?"))

    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF


async def test_com_sinal_de_saude_o_seguimento_nao_vai_ao_lucas() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="information")])

    resultado = await grafo.ainvoke(
        _entrada(
            "como assim? meu filho está com febre", agente_ativo_conversa="lucas", sinal_saude_lexico=True
        )
    )

    assert resultado["response_kind"] != RESPONSE_KIND_HANDOFF


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("de qual ano?", True),
        ("De qual mês?", True),
        ("e a de outubro?", True),
        ("quanto foi?", True),
        ("foi paga?", True),
        ("e o vencimento?", True),
        ("e a minha filha?", False),
        ("e o bebê?", False),
        ("quanto foi o plano?", False),
        ("não entendi, está errado, quero contestar", False),
        ("oi", False),
        (None, False),
    ],
)
def test_e_seguimento_de_cobranca(texto: object, esperado: bool) -> None:
    assert e_seguimento_de_cobranca(texto) is esperado


# =================================================================================================
# 2. Dependentes
# =================================================================================================


@pytest.mark.parametrize(
    ("view", "esperado"),
    [
        (
            _visao(("Filho(a) até 23 anos", True)),
            f"No seu cadastro consta 1 dependente ativo: filho(a) até 23 anos (recém-nascido no plano). {_SEM_NOMES}",
        ),
        (
            _visao(("Filho(a) até 23 anos", False), ("Cônjuge", False)),
            f"No seu cadastro constam 2 dependentes ativos: 1 filho(a) até 23 anos e 1 cônjuge. {_SEM_NOMES}",
        ),
        (
            _visao(("Filho(a) até 23 anos", False), ("Filho(a) até 23 anos", False)),
            "No seu cadastro constam 2 dependentes ativos: 2 com relação filho(a) até 23 anos. " + _SEM_NOMES,
        ),
        (_visao(), RESPOSTA_DEPENDENTES_NENHUM),
        (_visao(titular=False), RESPOSTA_DEPENDENTES_NAO_TITULAR),
        (
            _visao(*[("Filho(a) até 23 anos", False)] * 20, total=25, truncada=True),
            f"No seu cadastro constam 25 dependentes ativos. {_SEM_NOMES}",
        ),
    ],
)
async def test_dependentes_resposta_deterministica_por_caso(view: DependentesView, esperado: str) -> None:
    fonte = _FonteDependentes(view)
    grafo, whatsapp, _, cib = _grafo([_classify(intent="information")], fonte=fonte)  # type: ignore[arg-type]

    resultado = await grafo.ainvoke(_entrada("tenho dependentes?"))

    assert fonte.chamadas == ["dependentes"]
    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [esperado]
    assert "não consta essa informação" not in whatsapp.enviados[0]
    assert _starts(cib) == 0


@pytest.mark.parametrize(
    "mensagem", ["tenho dependentes?", "quem são meus dependentes?", "minha filha está no plano?"]
)
@pytest.mark.parametrize("intent", ["information", "outside_channel", "consulta_plano", "greeting"])
async def test_a_mesma_forma_para_qualquer_frase_e_intencao(mensagem: str, intent: str) -> None:
    fonte = _FonteDependentes(_visao(("Filho(a) até 23 anos", True)))
    extra = {"consulta_subtipo": "elegibilidade"} if intent == "consulta_plano" else {}
    grafo, whatsapp, _, _ = _grafo([_classify(intent=intent, **extra)], fonte=fonte)  # type: ignore[arg-type]

    await grafo.ainvoke(_entrada(mensagem))

    assert fonte.chamadas == ["dependentes"]
    texto = whatsapp.enviados[0]
    assert "1 dependente ativo" in texto and _SEM_NOMES in texto


async def test_transcricao_do_dono_antes_vc_consegue_olhar_sobre_meus_dependentes() -> None:
    fonte = _FonteDependentes(_visao(("Filho(a) até 23 anos", True)))
    grafo, whatsapp, _, _ = _grafo([_classify(intent="outside_channel")], fonte=fonte)  # type: ignore[arg-type]

    await grafo.ainvoke(_entrada("Antes, vc consegue olhar sobre meus dependentes?"))

    assert whatsapp.enviados[0].startswith("No seu cadastro consta 1 dependente ativo")
    assert "não consta" not in whatsapp.enviados[0].lower()


async def test_falha_da_consulta_de_dependentes_nunca_diz_que_nao_consta() -> None:
    fonte = _FonteDependentes(None)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=fonte)  # type: ignore[arg-type]

    await grafo.ainvoke(_entrada("tenho dependentes?"))

    assert whatsapp.enviados == [RESPOSTA_DEPENDENTES_FALHA]
    assert "não consta" not in whatsapp.enviados[0].lower()


async def test_rota_de_dependentes_nao_carregada_mantem_o_texto_provisorio() -> None:
    fonte = _FonteDependentes(FonteIndisponivel("dependentes"))
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=fonte)  # type: ignore[arg-type]

    await grafo.ainvoke(_entrada("tenho dependentes?"))

    assert whatsapp.enviados == [RESPOSTA_DEPENDENTES]


async def test_sem_a_fonte_ligada_o_texto_provisorio_continua() -> None:
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=_Modelo([_classify(intent="information")]),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        historico_enabled=True,
    )

    await grafo.compile_graph().compile().ainvoke(_entrada("tenho dependentes?"))

    assert whatsapp.enviados == [RESPOSTA_DEPENDENTES]


def test_os_fatos_de_dependentes_nao_carregam_nada_identificavel() -> None:
    fatos = fatos_da_consulta("dependentes", _visao(("Filho(a) até 23 anos", True)))

    assert fatos is not None
    assert set(fatos) == {
        "eh_titular",
        "total_ativos",
        "lista_truncada",
        "relacoes",
        "recem_nascidos",
        "fonte_atualizada_em",
    }
    assert resposta_deterministica("dependentes", fatos).count(":") == 1


# --- o adaptador: a fonte do plano com o port de dependentes ----------------------------------------


class _Consentimento:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return "base-legal-1"


class _PortDependentes:
    def __init__(self, resultado: PortResult[DependentesView]) -> None:
        self._resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_dependentes(self, portable_subject_ref: str, **kw: Any) -> PortResult[DependentesView]:
        self.chamadas.append({"ref": portable_subject_ref, **kw})
        return self._resultado


def _fonte(resultado: PortResult[DependentesView]) -> tuple[ConsultasPlanoAmh, _PortDependentes]:
    port = _PortDependentes(resultado)
    return (
        ConsultasPlanoAmh(port=port, consentimento=_Consentimento(), purpose_of_use="execucao_contrato"),  # type: ignore[arg-type]
        port,
    )


async def test_adaptador_le_os_dependentes_com_a_base_legal() -> None:
    view = _visao(("Cônjuge", False))
    fonte, port = _fonte(PortResult.ok(view))

    assert await fonte.consultar(REF, "dependentes") == view
    assert port.chamadas[0]["purpose_of_use"] == "execucao_contrato"
    assert port.chamadas[0]["consent_decision_ref"] == "base-legal-1"


async def test_adaptador_escopo_nao_suportado_e_fonte_indisponivel() -> None:
    fonte, _ = _fonte(PortResult.refused(PortFailureReason.SCOPE_NOT_SUPPORTED))

    assert await fonte.consultar(REF, "dependentes") == FonteIndisponivel("dependentes")


@pytest.mark.parametrize("motivo", [PortFailureReason.UPSTREAM_UNAVAILABLE, PortFailureReason.TIMEOUT])
async def test_adaptador_falha_da_consulta_e_none(motivo: PortFailureReason) -> None:
    fonte, _ = _fonte(PortResult.refused(motivo))

    assert await fonte.consultar(REF, "dependentes") is None


async def test_adaptador_view_de_outro_sujeito_e_recusada() -> None:
    outra = DependentesView(
        portable_subject_ref="amh:psr:v1:outra",
        eh_titular=True,
        total_ativos=1,
        lista_truncada=False,
        dependentes=(),
        fonte_atualizada_em="2026-10-13",
    )
    fonte, _ = _fonte(PortResult.ok(outra))

    assert await fonte.consultar(REF, "dependentes") is None
