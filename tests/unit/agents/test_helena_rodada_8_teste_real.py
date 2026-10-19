"""DL-0095 — rodada 8: o TESTE REAL do dono no WhatsApp (dev, imagem baa1f503, o CPF dele, contrato pago pela
empresa), com a TINA ligada. Logs do receptor de 13/10/2026, 16:04-16:08 UTC.

O roteiro do dono, na ordem: tipo do plano, carencia, "meu plano cobre parto?", carteirinha, nome e a
mensagem mista "quando vence o boleto? e meu plano cobre dermatologista?". Cada bloco abaixo e' uma das
mensagens, com o LLM simulado e a fonte do plano simulada:

  1. "Estou em carencia?" -> `not_found` em carencias = a pessoa NAO TEM carencia ("No seu cadastro nao consta
     nenhuma carencia"); so' falha de verdade (prazo, erro, recusa) fica na familia "falha", e esta nunca
     diz "ainda nao estao disponiveis por este chat" (a fonte esta' LIGADA e a pessoa verificada).
  2. "Meu plano cobre parto?" -> a segmentacao registrada (com obstetricia) + onde confirmar; nunca promessa.
  3. Mensagem mista -> a parte do plano vai JUNTO da frase de passagem; o Lucas responde a cobranca depois.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from maezo.agents.helena.consultas_plano import (
    RESPOSTA_CONSULTA_SEM_IDENTIDADE,
    fatos_da_consulta,
    resposta_deterministica,
)
from maezo.agents.helena.graph import (
    FRASE_PASSAGEM_COBRANCA,
    MODELO_SEGMENTACAO_DO_CADASTRO,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_COBERTURA_ESPECIFICA,
    RESPOSTA_COBERTURA_ESPECIFICA_2,
    RESPOSTA_PLANO_FALHA_1,
    RESPOSTA_PLANO_FALHA_2,
    RESPOSTA_PLANO_FALHA_3,
    RESPOSTA_PLANO_FALHA_NA_PASSAGEM,
    RESPOSTA_PLANO_POR_CANAIS,
)
from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO, MARCADOR_RESPOSTA_DE_COBERTURA
from maezo.platform.webhooks.whatsapp import acesso_cadastro as cad
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import CarenciasView, ElegibilidadeView, VinculoPlano
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _classify,
    _entrada,
    _grafo,
    _starts,
)
from tests.unit.platform.webhooks.whatsapp.test_dispatch_acesso import (
    _GrafoMarcador,
    _m,
    _montar,
    _verificar,
)

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
SEGMENTACAO = "Ambulatorial + Hospitalar com Obstetrícia"
ELEGIBILIDADE = ElegibilidadeView(
    portable_subject_ref=REF,
    ativo=True,
    vinculos=(
        VinculoPlano(
            carteirinha_mascarada="******4567",
            plano="ADESAO MEDIDA CERTA 50 STD",
            registro_ans_plano="999999",
            segmentacao=SEGMENTACAO,
            acomodacao="Enfermaria",
            tipo_contratacao="Coletivo empresarial",
            vigencia_inicio="2024-03-01",
            cancelamento=None,
            atendimento_liberado=True,
            situacao_vinculo="Ativo",
            situacao_contrato="Ativo",
            titular=True,
            relacao_dependencia=None,
            titular_ref=None,
        ),
    ),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
    campos_ausentes=frozenset(),
)
SEM_CARENCIAS = CarenciasView(
    portable_subject_ref=REF, pendentes=0, carencias=(), fonte_atualizada_em="2026-10-06T10:00:00Z"
)


class _Fonte:
    """A fonte do plano do GRAFO: devolve a view por subtipo (ou `None`, a falha)."""

    def __init__(self, views: dict[str, Any] | None = None, *, falha: bool = False) -> None:
        self.chamadas: list[str] = []
        self._views = {"elegibilidade": ELEGIBILIDADE, "carteirinha": ELEGIBILIDADE, **(views or {})}
        self._falha = falha

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append(subtipo)
        return None if self._falha else self._views.get(subtipo)


# =================================================================================================
# 1. "Estou em carencia?" — o adaptador da TINA
# =================================================================================================


class _Consentimento:
    async def decisao(self, ref: str, finalidade: str) -> str | None:
        return "base-legal-do-teste"


class _PortaTina:
    """O `TinaPort` simulado: o que o contrato devolve para carencias e elegibilidade."""

    def __init__(self, *, carencias: PortResult[Any], elegibilidade: PortResult[Any]) -> None:
        self._carencias = carencias
        self._elegibilidade = elegibilidade
        self.chamadas: list[str] = []

    async def get_carencias(self, ref: str, **_: Any) -> PortResult[Any]:
        self.chamadas.append("carencias")
        return self._carencias

    async def get_elegibilidade(self, ref: str, **_: Any) -> PortResult[Any]:
        self.chamadas.append("elegibilidade")
        return self._elegibilidade

    async def get_requisicoes(self, ref: str, **_: Any) -> PortResult[Any]:  # pragma: no cover
        raise AssertionError("nao consultado")


def _adaptador(porta: _PortaTina) -> ConsultasPlanoAmh:
    return ConsultasPlanoAmh(
        port=cast(Any, porta),
        consentimento=cast(Any, _Consentimento()),
        purpose_of_use="atendimento_whatsapp",
    )


def _recusa(motivo: PortFailureReason) -> PortResult[Any]:
    return PortResult.refused(motivo)


async def test_not_found_em_carencias_com_elegibilidade_confirmada_e_nenhuma_carencia() -> None:
    """O log real: `audit tina_get_carencias ALLOW` + `motivo=not_found` com a elegibilidade OK."""
    porta = _PortaTina(
        carencias=_recusa(PortFailureReason.NOT_FOUND), elegibilidade=PortResult.ok(ELEGIBILIDADE)
    )

    view = await _adaptador(porta).consultar(REF, "carencia")

    assert isinstance(view, CarenciasView)
    assert view.carencias == () and view.pendentes == 0
    assert view.fonte_atualizada_em == "2026-10-06T10:00:00Z"
    assert porta.chamadas == ["carencias", "elegibilidade"]


@pytest.mark.parametrize(
    "carencias",
    [
        _recusa(PortFailureReason.UPSTREAM_UNAVAILABLE),
        _recusa(PortFailureReason.NOT_AUTHENTICATED),
        _recusa(PortFailureReason.RATE_LIMITED),
        _recusa(PortFailureReason.CONSENT_REQUIRED),
    ],
)
async def test_falha_de_verdade_em_carencias_continua_falha(carencias: PortResult[Any]) -> None:
    porta = _PortaTina(carencias=carencias, elegibilidade=PortResult.ok(ELEGIBILIDADE))

    assert await _adaptador(porta).consultar(REF, "carencia") is None
    assert porta.chamadas == ["carencias"], "so' o `not_found` confirma o sujeito"


async def test_not_found_sem_o_sujeito_confirmado_nao_vira_nenhuma_carencia() -> None:
    porta = _PortaTina(
        carencias=_recusa(PortFailureReason.NOT_FOUND), elegibilidade=_recusa(PortFailureReason.NOT_FOUND)
    )

    assert await _adaptador(porta).consultar(REF, "carencia") is None


def test_o_texto_de_nenhuma_carencia_e_so_o_que_o_cadastro_diz() -> None:
    fatos = fatos_da_consulta("carencia", SEM_CARENCIAS)
    assert fatos is not None

    texto = resposta_deterministica("carencia", fatos)

    assert texto.startswith("No seu cadastro não consta nenhuma carência (atualizado em 06/10/2026). ")
    assert "não confirmam cobertura nem autorização" in texto
    assert "não estão disponíveis" not in texto


async def test_estou_em_carencia_responde_nenhuma_carencia() -> None:
    fonte = _Fonte({"carencia": SEM_CARENCIAS})
    grafo, whatsapp, _, cib = _grafo([_classify(intent="information")], fonte=cast(Any, fonte))

    resultado = await grafo.ainvoke(_entrada("Estou em carência?"))

    assert fonte.chamadas == ["carencia"]
    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados[0].startswith("No seu cadastro não consta nenhuma carência")
    assert (
        "indisponíveis" not in whatsapp.enviados[0]
        and "disponíveis por este chat" not in whatsapp.enviados[0]
    )
    assert _starts(cib) == 0


# =================================================================================================
# 1b. A falha de verdade (fonte LIGADA): texto de falha que avanca, nunca "por este chat"
# =================================================================================================


@pytest.mark.parametrize(
    ("falas", "esperado"),
    [
        ([], RESPOSTA_PLANO_FALHA_1),
        ([RESPOSTA_PLANO_FALHA_1], RESPOSTA_PLANO_FALHA_2),
        ([RESPOSTA_PLANO_FALHA_1, RESPOSTA_PLANO_FALHA_2], RESPOSTA_PLANO_FALHA_3),
    ],
)
async def test_falha_real_com_a_fonte_ligada_nao_diz_por_este_chat_e_nao_repete(
    falas: list[str], esperado: str
) -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="information")], fonte=cast(Any, _Fonte(falha=True)))

    await grafo.ainvoke(_entrada("Estou em carência?", falas))

    assert whatsapp.enviados == [esperado]
    assert whatsapp.enviados[0] not in falas
    assert "por este chat" not in whatsapp.enviados[0]
    assert RESPOSTA_PLANO_POR_CANAIS not in whatsapp.enviados
    assert _starts(cib) == 0


def test_a_segunda_falha_oferece_uma_pessoa() -> None:
    assert "pessoa da equipe" in RESPOSTA_PLANO_FALHA_2


# =================================================================================================
# 2. "Meu plano cobre parto?"
# =================================================================================================


async def test_cobre_parto_diz_a_segmentacao_com_obstetricia_e_nao_promete() -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, _ = _grafo([_classify(intent="clinical_question")], fonte=cast(Any, fonte))

    resultado = await grafo.ainvoke(_entrada("Meu plano cobre parto?"))

    assert resultado["response_kind"] == "inform"
    assert fonte.chamadas == ["elegibilidade"]
    enviado = whatsapp.enviados[0]
    assert enviado == (
        f"{MODELO_SEGMENTACAO_DO_CADASTRO.format(segmentacao=SEGMENTACAO)} {RESPOSTA_COBERTURA_ESPECIFICA}"
    )
    assert "Obstetrícia" in enviado
    for promessa in ("está coberto", "cobre o parto", "tem cobertura para", "garant"):
        assert promessa not in enviado.lower()


async def test_cobre_parto_depois_de_outras_respostas_do_plano_ainda_recebe_a_segmentacao() -> None:
    """O roteiro do dono: tipo do plano, carencia e so' depois "cobre parto"."""
    fonte = _Fonte()
    grafo, whatsapp, _, _ = _grafo([_classify(intent="clinical_question")], fonte=cast(Any, fonte))

    await grafo.ainvoke(
        _entrada(
            "Meu plano cobre parto?",
            [MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO, MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO],
        )
    )

    assert fonte.chamadas == ["elegibilidade"]
    assert SEGMENTACAO in whatsapp.enviados[0]


async def test_insistir_na_cobertura_nao_reenvia_a_segmentacao_nem_repete() -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, _ = _grafo([_classify(intent="clinical_question")], fonte=cast(Any, fonte))

    await grafo.ainvoke(_entrada("mas cobre parto ou não?", [MARCADOR_RESPOSTA_DE_COBERTURA]))

    assert fonte.chamadas == []
    assert whatsapp.enviados == [RESPOSTA_COBERTURA_ESPECIFICA_2]


async def test_cobertura_sem_segmentacao_so_diz_onde_confirmar() -> None:
    from dataclasses import replace

    sem = replace(ELEGIBILIDADE, vinculos=(replace(ELEGIBILIDADE.vinculos[0], segmentacao=None),))
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="clinical_question")],
        fonte=cast(Any, _Fonte({"elegibilidade": sem})),
    )

    await grafo.ainvoke(_entrada("Meu plano cobre parto?"))

    assert whatsapp.enviados == [RESPOSTA_COBERTURA_ESPECIFICA]


# =================================================================================================
# 3. Mensagem mista: vencimento + dermatologista
# =================================================================================================

MISTA = "Quando vence o boleto? E meu plano cobre dermatologista?"


async def test_mista_entrega_a_parte_do_plano_junto_da_frase_de_passagem() -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, cib = _grafo([_classify(intent="information")], fonte=cast(Any, fonte))

    resultado = await grafo.ainvoke(_entrada(MISTA))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == "vencimento"
    assert fonte.chamadas == ["elegibilidade"]
    esperado = (
        f"{FRASE_PASSAGEM_COBRANCA} "
        f"{MODELO_SEGMENTACAO_DO_CADASTRO.format(segmentacao=SEGMENTACAO)} {RESPOSTA_COBERTURA_ESPECIFICA}"
    )
    assert whatsapp.enviados == [esperado]
    assert "perguntar de novo" not in whatsapp.enviados[0]
    assert _starts(cib) == 0


async def test_mista_com_o_lucas_ja_ativo_so_a_parte_do_plano() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=cast(Any, _Fonte()))

    resultado = await grafo.ainvoke(_entrada(MISTA, agente_ativo_conversa="lucas"))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert len(whatsapp.enviados) == 1 and whatsapp.enviados[0].startswith("No cadastro, seu plano consta")
    assert FRASE_PASSAGEM_COBRANCA not in whatsapp.enviados[0]


async def test_mista_com_a_fonte_falhando_diz_onde_confirmar_sem_pedir_reenvio() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=cast(Any, _Fonte(falha=True)))

    await grafo.ainvoke(_entrada(MISTA))

    assert whatsapp.enviados == [f"{FRASE_PASSAGEM_COBRANCA} {RESPOSTA_COBERTURA_ESPECIFICA}"]


async def test_mista_de_cobranca_com_carencia_e_a_falha_nao_pede_reenvio() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=cast(Any, _Fonte(falha=True)))

    await grafo.ainvoke(_entrada("Quando vence o boleto? E tenho carência?"))

    assert whatsapp.enviados == [f"{FRASE_PASSAGEM_COBRANCA} {RESPOSTA_PLANO_FALHA_NA_PASSAGEM}"]
    assert "de novo" not in whatsapp.enviados[0]


# =================================================================================================
# 4. O resto do roteiro: tipo do plano, carteirinha e nome
# =================================================================================================


async def test_tipo_do_plano_usa_os_fatos() -> None:
    fonte = _Fonte()
    rascunho = "Seu plano é o ADESAO MEDIDA CERTA 50 STD, com acomodação Enfermaria."
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information"), rascunho], fonte=cast(Any, fonte))

    await grafo.ainvoke(_entrada("Qual o tipo do meu plano?"))

    assert fonte.chamadas == ["elegibilidade"]
    assert "ADESAO MEDIDA CERTA 50 STD" in whatsapp.enviados[0]


async def test_numero_da_carteirinha_usa_a_consulta_de_carteirinha() -> None:
    fonte = _Fonte()
    rascunho = "A sua carteirinha termina em ******4567."
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information"), rascunho], fonte=cast(Any, fonte))

    await grafo.ainvoke(_entrada("Qual o número da minha carteirinha?"))

    assert fonte.chamadas == ["carteirinha"]
    assert "******4567" in whatsapp.enviados[0]


@pytest.mark.parametrize("mensagem", ["Qual é o meu nome?", "qual meu nome?"])
def test_nome_e_respondido_na_conversa_verificada_sem_mostrar_o_nome(mensagem: str) -> None:
    assert cad.tipo_de_pergunta(mensagem) == cad.TIPO_NOME
    texto = cad.resposta_de_cadastro(cad.TIPO_NOME, None)
    assert texto == cad.RESPOSTA_NOME
    assert "identificado" in texto and "não consigo" not in texto.lower()


# =================================================================================================
# 5. O despachante nunca diz "por este chat" com a fonte ligada
# =================================================================================================


async def test_com_a_fonte_ligada_o_fato_ausente_do_perfil_vai_ao_grafo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    identidade = {
        "portable_subject_ref": REF,
        "faixa_etaria": "adulto",
        "plano_ativo": None,
        "vigencia_inicio": None,
        "vigencia_fim": None,
        "carencia_vigente": None,
        "titular_ref": None,
    }
    d, cliente, _ = _montar(identidade)
    d.consultas_plano = cast(Any, _Fonte())
    await _verificar(d)

    await d.dispatch(_m("meu plano esta ativo?", 9))

    assert _GrafoMarcador.rodou == ["meu plano esta ativo?"]
    assert RESPOSTA_PLANO_POR_CANAIS not in [t for _, t in cliente.sent]


def test_a_falha_sem_identidade_continua_com_o_texto_proprio() -> None:
    assert "Não consegui confirmar seus dados" in RESPOSTA_CONSULTA_SEM_IDENTIDADE


async def test_com_a_fonte_ligada_o_aviso_de_terceiro_nao_esconde_o_fato_ausente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revisao do #785: o aviso de terceiro (D-01) era colado ANTES da comparacao com
    `RESPOSTA_PLANO_POR_CANAIS`; o texto deixava de ser igual e o "nao estao disponiveis por este chat" saia
    ao beneficiario mesmo com a fonte do plano ligada (contra a RODADA 8)."""
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    identidade = {
        "portable_subject_ref": REF,
        "faixa_etaria": "adulto",
        "plano_ativo": None,
        "vigencia_inicio": None,
        "vigencia_fim": None,
        "carencia_vigente": None,
        "titular_ref": None,
    }
    d, cliente, _ = _montar(identidade)
    d.consultas_plano = cast(Any, _Fonte())
    await _verificar(d)

    await d.dispatch(_m("o plano dele esta ativo?", 9))

    assert _GrafoMarcador.rodou == ["o plano dele esta ativo?"]
    enviados = [t for _, t in cliente.sent]
    assert not any(RESPOSTA_PLANO_POR_CANAIS in t for t in enviados)
    assert not any("por este chat" in t for t in enviados)
