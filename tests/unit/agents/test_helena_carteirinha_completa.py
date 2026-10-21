"""VINCULO ATIVO E CARTEIRINHA COMPLETA na Helena (decisao do dono de 17/10/2026, "modelo de teste" so' em
dev).

Chave `MAEZO_HELENA_CARTEIRINHA_COMPLETA` (settings `helena_carteirinha_completa`, default `False`). O que
fica
preso aqui:

  1. a regra de VINCULO ATIVO (cancelado em 2014 + ativo = so' o ativo; dois ativos = os dois; nenhum = texto
     fixo; cancelamento futuro = ativo com "cancelamento previsto"; "Suspenso"/"Adesao futura"/"Inapto" =
     nao);
  2. CHAVE DESLIGADA = os textos de antes, byte a byte (elegibilidade e carteirinha, v4 ligada e desligada);
  3. o NUMERO COMPLETO so' com a chave + identidade VERIFICADA + casamento pela mascara; qualquer falha da
  rota
     cai no mascarado; o numero nunca no prompt do modelo, nos fatos, em `consulta_literais`, no estado do
     grafo, no historico nem em log — so' no texto ENVIADO, trocado no ultimo passo de `respond`;
  4. as cercas de saida nao quebram a resposta e o MODELO nao consegue escrever (nem puxar) o numero.

Numeros 100% sinteticos.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
import structlog
from structlog.testing import capture_logs

from maezo.agents.helena import consultas_plano as cp
from maezo.agents.helena import helena_v4 as v4
from maezo.agents.helena.consultas_plano import (
    INTENT_CONSULTA_PLANO,
    RESPOSTA_SEM_VINCULO_ATIVO,
    CarteirinhasDoTurno,
    fatos_da_consulta,
    literais_dos_fatos,
    motivo_de_recusa_da_consulta,
    numeros_por_mascara,
    resposta_deterministica,
    vinculo_ativo,
)
from maezo.agents.helena.graph import IDENTIDADE_RECONHECIDA, HelenaGraph, new_helena_state
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.ports.tina import CarteirinhaCompleta, CarteirinhasView, ElegibilidadeView, VinculoPlano
from maezo.qa.simulador import aceite
from maezo.qa.simulador.mascaramento import TOKEN_CARTEIRINHA, Mascarador
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink

_REPO = Path(__file__).resolve().parents[3]
_REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_OUTRO_REF = "amh:psr:v1:9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f"
_HOJE = date(2026, 10, 17)
_RELOGIO = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
#: Numeros SINTETICOS (nenhuma carteirinha real).
_NUMERO = "900000000000017"
_MASCARA = "***********0017"
_NUMERO_2 = "900000000000025"
_MASCARA_2 = "***********0025"
_MASCARA_ANTIGA = "***********0009"
_PLANO_ATIVO = "PLANO SINTETICO ATUAL ENFERMARIA"
_PLANO_ANTIGO = "PLANO SINTETICO ANTIGO 2014"
_IDENTIDADE = {
    "portable_subject_ref": _REF,
    "faixa_etaria": "adulto",
    "plano_ativo": True,
    "vigencia_inicio": "2020-03-01",
    "vigencia_fim": None,
    "carencia_vigente": False,
    "titular_ref": None,
}


def _vinculo(**sobre: Any) -> VinculoPlano:
    base: dict[str, Any] = {
        "carteirinha_mascarada": _MASCARA,
        "plano": _PLANO_ATIVO,
        "registro_ans_plano": "123456789",
        "segmentacao": "Ambulatorial + Hospitalar com obstetricia",
        "acomodacao": "Enfermaria",
        "tipo_contratacao": "Coletivo empresarial",
        "vigencia_inicio": "2020-03-01",
        "cancelamento": None,
        "atendimento_liberado": True,
        "situacao_vinculo": "Apto",
        "situacao_contrato": "Ativo",
        "titular": True,
        "relacao_dependencia": None,
        "titular_ref": None,
    }
    base.update(sobre)
    return VinculoPlano(**base)


#: O caso medido: plano antigo CANCELADO em 2014 + o plano atual.
_CANCELADO_2014 = _vinculo(
    carteirinha_mascarada=_MASCARA_ANTIGA,
    plano=_PLANO_ANTIGO,
    vigencia_inicio="2010-01-01",
    cancelamento="2014-05-31",
    atendimento_liberado=False,
    situacao_vinculo="Inapto",
)


def _view(*vinculos: VinculoPlano, ref: str = _REF) -> ElegibilidadeView:
    return ElegibilidadeView(
        portable_subject_ref=ref,
        ativo=True,
        vinculos=vinculos or (_CANCELADO_2014, _vinculo()),
        fonte_atualizada_em="2026-10-17T06:00:00Z",
        campos_ausentes=frozenset(),
    )


def _carteirinhas(*itens: tuple[str, str], ref: str = _REF) -> CarteirinhasView:
    return CarteirinhasView(
        portable_subject_ref=ref,
        carteirinhas=tuple(
            CarteirinhaCompleta(carteirinha_numero=n, carteirinha_mascarada=m, vinculo_principal=i == 0)
            for i, (n, m) in enumerate(itens or ((_NUMERO, _MASCARA),))
        ),
        fonte_atualizada_em="2026-10-17T12:00:00Z",
    )


# =================================================================================================
# 1. A regra de vinculo ativo (pura)
# =================================================================================================


@pytest.mark.parametrize(
    ("sobre", "ativo"),
    [
        ({}, True),
        ({"cancelamento": "2014-05-31"}, False),  # o plano antigo cancelado
        ({"cancelamento": "2026-10-17"}, False),  # cancelado HOJE ja' nao e' ativo (> hoje)
        ({"cancelamento": "2026-12-31"}, True),  # cancelamento AGENDADO: ativo
        ({"cancelamento": "data invalida"}, False),  # ilegivel = cancelado (fail-closed)
        ({"cancelamento": ""}, True),  # vazio = ausente
        ({"situacao_vinculo": "ATIVO"}, True),
        ({"situacao_vinculo": " ápto "}, True),  # sem acento/caixa
        ({"situacao_vinculo": "Suspenso"}, False),
        ({"situacao_vinculo": "Adesão futura"}, False),
        ({"situacao_vinculo": "Inapto"}, False),
        ({"situacao_vinculo": None, "atendimento_liberado": True}, True),
        ({"situacao_vinculo": None, "atendimento_liberado": False}, False),
        ({"situacao_vinculo": None, "atendimento_liberado": None}, False),
        # Com a situacao presente, ela decide (o atendimento liberado nao a contradiz).
        ({"situacao_vinculo": "Suspenso", "atendimento_liberado": True}, False),
    ],
)
def test_regra_de_vinculo_ativo(sobre: dict[str, Any], ativo: bool) -> None:
    assert vinculo_ativo(_vinculo(**sobre), _HOJE) is ativo


def test_cancelado_em_2014_mais_o_ativo_mostra_so_o_ativo() -> None:
    for subtipo in ("elegibilidade", "carteirinha"):
        fatos = fatos_da_consulta(subtipo, _view(), so_ativos=True, hoje=_HOJE)
        assert fatos is not None
        assert [v["plano"] for v in fatos["vinculos"]] == [_PLANO_ATIVO]
        assert fatos["ativo"] is True
        texto = resposta_deterministica(subtipo, fatos, so_ativos=True)
        assert _PLANO_ANTIGO not in texto and _MASCARA_ANTIGA not in texto and "2014" not in texto
        assert _PLANO_ATIVO in texto
    fatos_v4 = v4.fatos_da_carteirinha(_view(), so_ativos=True, hoje=_HOJE)
    texto_v4 = v4.texto_da_carteirinha(fatos_v4, so_ativos=True)
    assert _PLANO_ANTIGO not in texto_v4 and "Cancelamento" not in texto_v4 and _MASCARA in texto_v4


def test_dois_vinculos_ativos_aparecem_os_dois() -> None:
    view = _view(_vinculo(), _vinculo(carteirinha_mascarada=_MASCARA_2, plano="PLANO SINTETICO DOIS"))
    fatos = fatos_da_consulta("carteirinha", view, so_ativos=True, hoje=_HOJE)
    assert fatos is not None and len(fatos["vinculos"]) == 2
    texto = v4.texto_da_carteirinha(v4.fatos_da_carteirinha(view, so_ativos=True, hoje=_HOJE), so_ativos=True)
    assert _MASCARA in texto and _MASCARA_2 in texto


def test_nenhum_vinculo_ativo_e_o_texto_fixo() -> None:
    view = _view(_CANCELADO_2014, _vinculo(situacao_vinculo="Suspenso"))
    for subtipo in ("elegibilidade", "carteirinha"):
        fatos = fatos_da_consulta(subtipo, view, so_ativos=True, hoje=_HOJE)
        assert fatos is not None and fatos["vinculos"] == [] and fatos["ativo"] is False
        assert resposta_deterministica(subtipo, fatos, so_ativos=True) == RESPOSTA_SEM_VINCULO_ATIVO
    fatos_v4 = v4.fatos_da_carteirinha(view, so_ativos=True, hoje=_HOJE)
    assert v4.texto_da_carteirinha(fatos_v4, so_ativos=True) == RESPOSTA_SEM_VINCULO_ATIVO
    assert "pessoa da equipe" in RESPOSTA_SEM_VINCULO_ATIVO and "vínculo ativo" in RESPOSTA_SEM_VINCULO_ATIVO


def test_cancelamento_futuro_e_ativo_e_sai_como_cancelamento_previsto() -> None:
    view = _view(_vinculo(cancelamento="2026-12-31"))
    fatos = fatos_da_consulta("elegibilidade", view, so_ativos=True, hoje=_HOJE)
    assert fatos is not None
    [item] = fatos["vinculos"]
    assert item["cancelamento_previsto"] == "2026-12-31" and "cancelamento" not in item
    assert "cancelamento previsto: 31/12/2026" in resposta_deterministica(
        "elegibilidade", fatos, so_ativos=True
    )
    texto_v4 = v4.texto_da_carteirinha(
        v4.fatos_da_carteirinha(view, so_ativos=True, hoje=_HOJE), so_ativos=True
    )
    assert "- Cancelamento previsto: 31/12/2026" in texto_v4
    assert "- Cancelamento: " not in texto_v4


# =================================================================================================
# 2. Chave DESLIGADA = os textos de antes, byte a byte (funcoes puras)
# =================================================================================================


@pytest.mark.parametrize("subtipo", ["elegibilidade", "carteirinha"])
def test_desligada_as_funcoes_puras_sao_as_de_antes(subtipo: str) -> None:
    view = _view()
    antes = fatos_da_consulta(subtipo, view)
    assert antes == fatos_da_consulta(subtipo, view, so_ativos=False, hoje=_HOJE)
    assert antes is not None and len(antes["vinculos"]) == 2  # o cancelado CONTINUA (comportamento de hoje)
    assert resposta_deterministica(subtipo, antes) == resposta_deterministica(
        subtipo, antes, completos=frozenset(), so_ativos=False
    )
    v4_antes = v4.fatos_da_carteirinha(view)
    assert v4_antes == v4.fatos_da_carteirinha(view, so_ativos=False, hoje=_HOJE)
    texto = v4.texto_da_carteirinha(v4_antes)
    assert texto == v4.texto_da_carteirinha(v4_antes, completos=frozenset(), so_ativos=False)
    assert "Por segurança, não mostro o código inteiro nem outros dados pessoais por aqui." in texto
    assert "- Cancelamento: 31/05/2014" in texto  # hoje a Helena lista o cancelado


# =================================================================================================
# 3. Numero completo: casamento pela mascara (puro)
# =================================================================================================


def test_casamento_pela_mascara_so_o_inequivoco() -> None:
    assert numeros_por_mascara([_MASCARA], _carteirinhas(), _REF).mascaras == {_MASCARA}
    # A rota de OUTRO sujeito nunca casa.
    assert not numeros_por_mascara([_MASCARA], _carteirinhas(ref=_OUTRO_REF), _REF)
    # Mascara que a elegibilidade nao tem: nao casa.
    assert not numeros_por_mascara([_MASCARA_2], _carteirinhas(), _REF)
    # Mascara repetida (nos vinculos ou na rota): ambiguo, nao casa.
    assert not numeros_por_mascara([_MASCARA, _MASCARA], _carteirinhas(), _REF)
    assert not numeros_por_mascara(
        [_MASCARA], _carteirinhas((_NUMERO, _MASCARA), (_NUMERO_2, _MASCARA)), _REF
    )
    # Numero fora do padrao do contrato: nao usado.
    assert not numeros_por_mascara([_MASCARA], _carteirinhas(("90-00", _MASCARA)), _REF)
    assert not numeros_por_mascara([_MASCARA], None, _REF)


def test_o_numero_vive_so_em_memoria_e_nao_serializa() -> None:
    numeros = numeros_por_mascara([_MASCARA], _carteirinhas(), _REF)
    assert _NUMERO not in repr(numeros) and _NUMERO not in str(numeros)
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(numeros)
    texto = f"- Número da carteirinha: {cp.marcador_da_carteirinha(_MASCARA)}"
    assert _NUMERO not in texto
    assert numeros.preencher(texto) == f"- Número da carteirinha: {_NUMERO}"
    assert cp.sem_marcador_de_carteirinha(texto) == f"- Número da carteirinha: {_MASCARA}"
    assert CarteirinhasDoTurno({}).preencher(texto) == f"- Número da carteirinha: {_MASCARA}"


def test_ref_da_solicitacao_e_opaca() -> None:
    assert cp.ref_da_solicitacao("hk1_msg_0123456789") == "hk1_msg_0123456789"
    for ruim in (None, "", "curto", "+55 11 90000-0000", "x" * 65):
        gerado = cp.ref_da_solicitacao(ruim)
        assert re.fullmatch(r"hel_[0-9a-f]{32}", gerado)


# =================================================================================================
# 4. O grafo inteiro (consultar_plano -> respond)
# =================================================================================================


class _Modelo:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


class _Dmn:
    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> Any:
        raise AssertionError(f"consulta do plano nao avalia DMN ({decision_key})")


class _Fonte:
    """Fonte de fatos do plano que TAMBEM le' a carteirinha completa (`FonteDeCarteirinhaCompleta`)."""

    def __init__(
        self, view: Any = None, carteirinhas: Any = None, falha: BaseException | None = None
    ) -> None:
        self.view = view if view is not None else _view()
        self.carteirinhas = carteirinhas if carteirinhas is not None else _carteirinhas()
        self.falha = falha
        self.chamadas: list[tuple[str, str]] = []
        self.chamadas_carteirinha: list[tuple[str, str]] = []

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append((portable_subject_ref, subtipo))
        return self.view

    async def consultar_carteirinha(self, portable_subject_ref: str, request_ref: str) -> Any:
        self.chamadas_carteirinha.append((portable_subject_ref, request_ref))
        if self.falha is not None:
            raise self.falha
        return self.carteirinhas


class _FonteQueNaoResponde(_Fonte):
    async def consultar_carteirinha(self, portable_subject_ref: str, request_ref: str) -> Any:
        self.chamadas_carteirinha.append((portable_subject_ref, request_ref))
        return None


class _FonteSemCarteirinha:
    """A fonte de sempre (sem `consultar_carteirinha`): a carteirinha sai mascarada."""

    def __init__(self) -> None:
        self.view = _view()

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        return self.view


def _classify(subtipo: str) -> str:
    return json.dumps(
        {
            "intent": INTENT_CONSULTA_PLANO,
            "consulta_subtipo": subtipo,
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
        }
    )


_MENSAGEM = {
    "carteirinha": "Qual o número da minha carteirinha?",
    "elegibilidade": "Qual é o tipo do meu plano?",
}


async def _turno(
    subtipo: str,
    *,
    chave: bool | None,
    v4_ligada: bool,
    fonte: Any = None,
    rascunho: str | None = None,
    verificada: bool = True,
) -> tuple[dict[str, Any], _WhatsApp, _Modelo, Any]:
    modelo = _Modelo([_classify(subtipo), *([rascunho] if rascunho is not None else [])])
    whatsapp = _WhatsApp()
    fonte = fonte if fonte is not None else _Fonte()
    kwargs: dict[str, Any] = {}
    if chave is not None:
        kwargs["helena_carteirinha_completa_enabled"] = chave
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_Dmn(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        historico_enabled=True,
        helena_v4_enabled=v4_ligada,
        consultas_plano=fonte,
        relogio=lambda: _RELOGIO,
        **kwargs,
    )
    estado = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id="wa:amh:hk1_carteirinha_completa",
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-CARTEIRINHA",
            message_body=_MENSAGEM[subtipo],
            message_ref="hk1_msg_carteirinha_completa",
            **(
                {"identidade_beneficiario": _IDENTIDADE, "identidade_desfecho": IDENTIDADE_RECONHECIDA}
                if verificada
                else {}
            ),
        )
    )
    estado["aviso_identidade_dado"] = True  # o aviso de identidade de primeiro contato ja' saiu
    resultado = await grafo.compile_graph().compile().ainvoke(estado)
    return cast(dict[str, Any], resultado), whatsapp, modelo, fonte


@pytest.mark.parametrize("v4_ligada", [True, False])
@pytest.mark.parametrize("subtipo", ["elegibilidade", "carteirinha"])
async def test_chave_desligada_e_o_grafo_de_antes_byte_a_byte(v4_ligada: bool, subtipo: str) -> None:
    """Argumento AUSENTE (o grafo de hoje) e `False` explicito: o MESMO texto enviado e o MESMO estado, a
    rota da
    carteirinha nunca chamada e o vinculo cancelado de 2014 ainda listado (o comportamento de hoje)."""
    rascunho = f"Seu plano consta como {_PLANO_ATIVO}." if subtipo == "elegibilidade" else None
    ausente, zap_a, modelo_a, fonte_a = await _turno(
        subtipo, chave=None, v4_ligada=v4_ligada, rascunho=rascunho
    )
    desligada, zap_d, modelo_d, fonte_d = await _turno(
        subtipo, chave=False, v4_ligada=v4_ligada, rascunho=rascunho
    )
    assert zap_a.enviados == zap_d.enviados and len(zap_a.enviados) == 1
    for chave_estado in (
        "response_text",
        "consulta_literais",
        "resposta_com_dados_do_plano",
        "historico_conversa",
    ):
        assert _sem_carimbo(ausente.get(chave_estado)) == _sem_carimbo(desligada.get(chave_estado)), (
            chave_estado
        )
    assert modelo_a.prompts == modelo_d.prompts
    assert fonte_a.chamadas_carteirinha == [] and fonte_d.chamadas_carteirinha == []
    if subtipo == "carteirinha":
        enviado = zap_a.enviados[0]
        assert _MASCARA_ANTIGA in enviado  # hoje: os dois vinculos, inclusive o cancelado
        assert _NUMERO not in enviado
        if v4_ligada:
            assert enviado == v4.texto_da_carteirinha(v4.fatos_da_carteirinha(_view()))
        else:
            fatos = fatos_da_consulta("carteirinha", _view())
            assert fatos is not None
            assert enviado == resposta_deterministica("carteirinha", fatos)
    else:
        # Elegibilidade: os fatos que foram ao modelo sao os de hoje (os dois vinculos).
        [prompt] = [p for p in modelo_a.prompts if "fatos_do_plano" in p]
        assert _PLANO_ANTIGO in prompt


def _sem_carimbo(valor: Any) -> Any:
    """O historico guarda a hora do turno: compara so' o conteudo."""
    if isinstance(valor, list):
        return [{k: v for k, v in item.items() if k not in ("em", "quando", "ts")} for item in valor]
    return valor


@pytest.mark.parametrize("v4_ligada", [True, False])
async def test_chave_ligada_carteirinha_mostra_o_numero_completo_so_do_vinculo_ativo(v4_ligada: bool) -> None:
    with capture_logs() as logs:
        resultado, zap, modelo, fonte = await _turno("carteirinha", chave=True, v4_ligada=v4_ligada)
    [enviado] = zap.enviados
    rotulo = "- Número da carteirinha: " if v4_ligada else "- número da carteirinha: "
    assert f"{rotulo}{_NUMERO}" in enviado
    assert _MASCARA_ANTIGA not in enviado and _PLANO_ANTIGO not in enviado
    assert "{{" not in enviado
    if v4_ligada:
        assert "O cartão completo está no aplicativo Austa Clínicas e no portal do plano." in enviado
        assert "Por segurança, não mostro outros dados pessoais por aqui." in enviado
        assert "não mostro o código inteiro" not in enviado
    # A rota foi lida SO' para o proprio sujeito, com um id opaco.
    assert fonte.chamadas_carteirinha == [(_REF, "hk1_msg_carteirinha_completa")]
    # O NUMERO SO' no texto enviado: nunca no estado do grafo (checkpoint), nos literais, no historico, no
    # prompt do modelo nem em log.
    assert _NUMERO not in json.dumps(resultado, default=str, ensure_ascii=False)
    assert cp.tem_marcador_de_carteirinha(resultado["response_text"])
    assert _NUMERO not in json.dumps(resultado.get("consulta_literais"))
    assert resultado.get("resposta_com_dados_do_plano") is True
    assert _NUMERO not in json.dumps(resultado.get("historico_conversa"), default=str)
    assert all(_NUMERO not in p for p in modelo.prompts)
    assert not [p for p in modelo.prompts if "fatos_do_plano" in p]  # carteirinha: SEMPRE deterministica
    assert _NUMERO not in json.dumps(logs, default=str)


async def test_chave_ligada_sem_identidade_verificada_nunca_le_a_rota() -> None:
    resultado, zap, _, fonte = await _turno("carteirinha", chave=True, v4_ligada=True, verificada=False)
    assert fonte.chamadas_carteirinha == [] and fonte.chamadas == []
    assert _NUMERO not in zap.enviados[0]


@pytest.mark.parametrize(
    "fonte",
    [
        pytest.param(_FonteQueNaoResponde(), id="rota_sem_resposta"),
        pytest.param(_Fonte(falha=RuntimeError("rota fora")), id="rota_com_erro"),
        pytest.param(_Fonte(falha=TimeoutError()), id="rota_com_prazo_estourado"),
        pytest.param(_Fonte(carteirinhas=_carteirinhas((_NUMERO_2, _MASCARA_2))), id="sem_casamento"),
        pytest.param(_Fonte(carteirinhas=_carteirinhas(ref=_OUTRO_REF)), id="outro_sujeito"),
        pytest.param(_FonteSemCarteirinha(), id="fonte_sem_a_rota"),
    ],
)
async def test_falha_da_rota_cai_no_mascarado_sem_quebrar(fonte: Any) -> None:
    _, zap, _, _ = await _turno("carteirinha", chave=True, v4_ligada=True, fonte=fonte)
    [enviado] = zap.enviados
    assert f"- Código: {_MASCARA}" in enviado
    assert _NUMERO not in enviado and _NUMERO_2 not in enviado and "{{" not in enviado
    assert "Por segurança, não mostro o código inteiro nem outros dados pessoais por aqui." in enviado
    assert _MASCARA_ANTIGA not in enviado  # o filtro de vinculo ativo vale mesmo sem o numero


async def test_chave_ligada_elegibilidade_ve_so_o_ativo_e_nao_le_a_rota() -> None:
    rascunho = (
        f"Seu plano consta como {_PLANO_ATIVO}, segmentação Ambulatorial + Hospitalar com obstetricia, "
        "acomodação Enfermaria. Se quiser falar com uma pessoa da equipe, é só me pedir."
    )
    resultado, zap, modelo, fonte = await _turno(
        "elegibilidade", chave=True, v4_ligada=True, rascunho=rascunho
    )
    [prompt] = [p for p in modelo.prompts if "fatos_do_plano" in p]
    assert _PLANO_ANTIGO not in prompt and _PLANO_ATIVO in prompt
    assert fonte.chamadas_carteirinha == []
    assert _PLANO_ANTIGO not in zap.enviados[0]


@pytest.mark.parametrize("subtipo", ["elegibilidade", "carteirinha"])
@pytest.mark.parametrize("v4_ligada", [True, False])
async def test_chave_ligada_sem_vinculo_ativo_responde_o_texto_fixo(subtipo: str, v4_ligada: bool) -> None:
    fonte = _Fonte(view=_view(_CANCELADO_2014))
    _, zap, modelo, _ = await _turno(subtipo, chave=True, v4_ligada=v4_ligada, fonte=fonte)
    assert zap.enviados == [RESPOSTA_SEM_VINCULO_ATIVO]
    assert not [p for p in modelo.prompts if "fatos_do_plano" in p]  # sem modelo


async def test_o_modelo_nao_escreve_nem_puxa_o_numero() -> None:
    """Elegibilidade (redigida pelo modelo) com a chave ligada: (a) um rascunho com um numero longo e'
    recusado
    pela cerca e vira o texto deterministico; (b) um rascunho que IMITA o marcador nao puxa numero nenhum (a
    rota
    nem e' lida fora da carteirinha) — o envio troca o marcador pela propria mascara."""
    _, zap, _, fonte = await _turno(
        "elegibilidade", chave=True, v4_ligada=False, rascunho=f"Sua carteirinha e' {_NUMERO}."
    )
    assert _NUMERO not in zap.enviados[0] and fonte.chamadas_carteirinha == []
    _, zap, _, fonte = await _turno(
        "elegibilidade",
        chave=True,
        v4_ligada=False,
        rascunho=f"Seu plano e' {_PLANO_ATIVO}. Carteirinha {cp.marcador_da_carteirinha(_MASCARA)}.",
    )
    assert _NUMERO not in zap.enviados[0] and "{{" not in zap.enviados[0]
    assert fonte.chamadas_carteirinha == []


def test_as_cercas_rodam_antes_do_numero_e_nao_quebram_a_resposta() -> None:
    """As cercas de saida (`respond`) leem o texto com o MARCADOR, que nao tem digitos longos: passam. O
    numero
    entra so' no envio. Se entrasse antes, a cerca de documento da consulta (`_ONZE_DIGITOS`) o barraria — por
    isso a troca e' o ultimo passo."""
    fatos = v4.fatos_da_carteirinha(_view(), so_ativos=True, hoje=_HOJE)
    texto = v4.texto_da_carteirinha(fatos, completos=frozenset({_MASCARA}), so_ativos=True)
    literais = literais_dos_fatos(fatos)
    assert _NUMERO not in json.dumps(literais)
    cercado = cp.texto_para_cerca(texto, literais)
    assert motivo_de_canal_nao_confirmado(cercado) is None
    assert motivo_de_recusa(cercado, "inform", start_aconteceu=False) is None
    preenchido = numeros_por_mascara([_MASCARA], _carteirinhas(), _REF).preencher(texto)
    assert cp._ONZE_DIGITOS.search(preenchido) is not None  # a cerca de documento barraria o numero
    assert motivo_de_recusa_da_consulta(preenchido, {"vinculos": []}) is not None
    assert cp._ONZE_DIGITOS.search(texto) is None


async def test_o_numero_nunca_vai_a_log_nem_ao_stdlib(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    structlog.reset_defaults()
    with capture_logs() as logs:
        await _turno("carteirinha", chave=True, v4_ligada=True)
    assert _NUMERO not in caplog.text
    assert _NUMERO not in json.dumps(logs, default=str)
    assert any(e.get("event") == "helena_carteirinha_completa" and e.get("com_numero") is True for e in logs)


# =================================================================================================
# 5. A chave: settings, despachante, servico e Terraform
# =================================================================================================


def test_a_chave_nasce_desligada_no_grafo_na_fabrica_e_nas_settings() -> None:
    from maezo.agents.helena.graph import build
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    assert (
        inspect.signature(HelenaGraph.__init__).parameters["helena_carteirinha_completa_enabled"].default
        is False
    )
    assert 'cfg.get("helena_carteirinha_completa_enabled", False) is True' in inspect.getsource(build)
    campo = WhatsAppWebhookSettings.model_fields["helena_carteirinha_completa"]
    assert campo.default is False
    assert "MAEZO_HELENA_CARTEIRINHA_COMPLETA" in getattr(campo.validation_alias, "choices", ())
    caminho = WhatsAppWebhookSettings.model_fields["amh_tina_carteirinha_openapi_path"]
    assert caminho.default is None
    assert "MAEZO_AMH_TINA_CARTEIRINHA_OPENAPI_PATH" in getattr(caminho.validation_alias, "choices", ())
    assert HelenaDispatcher.__dataclass_fields__["helena_carteirinha_completa_enabled"].default is False
    servico = (_REPO / "src/maezo/platform/webhooks/service.py").read_text(encoding="utf-8")
    assert "helena_carteirinha_completa_enabled=settings.helena_carteirinha_completa" in servico
    assert "incluir_tina_carteirinha=_carteirinha_completa_configurada(settings)" in servico


@pytest.mark.parametrize("ligado", [True, False])
def test_o_despachante_passa_a_chave_ao_grafo(ligado: bool) -> None:
    from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module

    capturado: dict[str, Any] = {}

    class _Grafo:
        def compile(self, **_: Any) -> str:
            return "compilado"

    def _build(cfg: dict[str, Any]) -> _Grafo:
        capturado.update(cfg)
        return _Grafo()

    nada: Any = None
    with mock.patch.object(dispatch_module, "build", _build):
        despachante = dispatch_module.HelenaDispatcher(
            tenant_id="amh",
            inference=nada,
            dmn=nada,
            cibseven=nada,
            whatsapp_client=nada,
            pseudonymizer=nada,
            audit_sink=nada,
            helena_carteirinha_completa_enabled=ligado,
        )
        despachante._compile_turn_graph(nada)
    assert capturado["helena_carteirinha_completa_enabled"] is ligado


def test_a_rota_so_entra_na_composicao_com_consultas_chave_e_caminho() -> None:
    from types import SimpleNamespace

    from maezo.platform.webhooks.service import _carteirinha_completa_configurada

    def _s(**sobre: Any) -> Any:
        base = {
            "helena_consultas_amh": True,
            "helena_carteirinha_completa": True,
            "amh_tina_carteirinha_openapi_path": "/app/x.yaml",
        }
        base.update(sobre)
        return SimpleNamespace(**base)

    assert _carteirinha_completa_configurada(_s()) is True
    assert _carteirinha_completa_configurada(_s(helena_consultas_amh=False)) is False
    assert _carteirinha_completa_configurada(_s(helena_carteirinha_completa=False)) is False
    assert _carteirinha_completa_configurada(_s(amh_tina_carteirinha_openapi_path=None)) is False


def test_a_chave_so_e_ligada_no_dev_pelo_terraform() -> None:
    envs = _REPO / "deploy" / "aws-ecs" / "envs"
    dev = envs / "dev-sa-east-1"
    tfvars = (dev / "amh-interop.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^helena_carteirinha_completa = true$", tfvars, re.MULTILINE)
    receptor = (dev / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    assert (
        '{ name = "MAEZO_HELENA_CARTEIRINHA_COMPLETA", value = tostring(var.helena_carteirinha_completa) }'
        in receptor
    )
    variaveis = (dev / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "helena_carteirinha_completa"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco
    caminho = variaveis.split('variable "amh_tina_carteirinha_openapi_path"', 1)[1].split("\n}\n", 1)[0]
    assert '"/app/config/integrations/amh/openapi/tina-carteirinha.openapi.yaml"' in caminho
    interop = (dev / "amh-interop.tf").read_text(encoding="utf-8")
    assert (
        "amh_tina_carteirinha_ligada = var.helena_consultas_amh && var.helena_carteirinha_completa" in interop
    )
    assert 'local.amh_tina_carteirinha_ligada ? ["interop/tina.carteirinha"] : []' in interop
    assert "MAEZO_AMH_TINA_CARTEIRINHA_OPENAPI_PATH" in interop
    # O simulador herda a MESMA lista de ambiente do receptor.
    simulador = (dev / "task-simulador.tf").read_text(encoding="utf-8")
    assert "local.webhook_receiver_contrato.environment_app" in simulador
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "helena_carteirinha_completa" not in arquivo.read_text(encoding="utf-8"), arquivo


# =================================================================================================
# 6. Simulador: o mascarador e o roteiro de aceite
# =================================================================================================


@pytest.mark.parametrize(
    "texto",
    [
        f"- Número da carteirinha: {_NUMERO}",
        f"- número da carteirinha: {_NUMERO}; plano: X",
        "- Número da carteirinha: ABC12345XYZ",
        "sua carteira 90000017 esta ok",  # 8 digitos soltos
        f"o cartao {_NUMERO} esta ativo",
        "numero 9000000000000000000017",  # 22 digitos
    ],
)
def test_o_mascarador_do_relatorio_nunca_mostra_o_numero(texto: str) -> None:
    mascarado = Mascarador().texto(texto)
    assert not re.search(r"\d{8,}", mascarado), mascarado
    assert "ABC12345XYZ" not in mascarado
    assert TOKEN_CARTEIRINHA in mascarado or "[" in mascarado


def test_o_mascarador_preserva_o_numero_ans_e_a_mascara() -> None:
    texto = f"- Código: {_MASCARA}\n- Nº ANS do plano: 123456789"
    mascarado = Mascarador().texto(texto)
    assert "Nº ANS do plano: 123456789" in mascarado
    assert "0017" not in mascarado  # a mascara continua sem os ultimos digitos no relatorio


def test_o_roteiro_de_aceite_da_carteirinha_completa() -> None:
    assert "helena_carteirinha_completa" in aceite.ROTEIROS
    roteiro = aceite.ROTEIROS["helena_carteirinha_completa"]
    aceite.validar(roteiro)
    assert all(s.resolveu for s in roteiro)
    carteirinha = roteiro[0].passos[0]
    texto_bom = v4.texto_da_carteirinha(
        v4.fatos_da_carteirinha(_view(), so_ativos=True, hoje=_HOJE),
        completos=frozenset({_MASCARA}),
        so_ativos=True,
    )
    enviado = numeros_por_mascara([_MASCARA], _carteirinhas(), _REF).preencher(texto_bom)
    assert aceite.avaliar_passo(carteirinha, [("helena", enviado)], [], None) == []
    # Com o vinculo cancelado listado (o defeito), o passo reprova — sem ecoar o numero.
    ruim = enviado + "\n- Cancelamento: 31/05/2014"
    falhas = aceite.avaliar_passo(carteirinha, [("helena", ruim)], [], None)
    assert falhas and all(_NUMERO not in f for f in falhas)
    # O passo mascarado de hoje (dono_v1 / helena_v4) so' roda com a chave DESLIGADA.
    mascarados = [
        p
        for r in (aceite.DONO_V1, aceite.HELENA_V4)
        for s in r
        for p in s.passos
        if p.texto == "Qual o número da minha carteirinha?"
    ]
    assert mascarados and all(p.condicao == "sem_carteirinha_completa" for p in mascarados)
    assert aceite._condicao_atendida(mascarados[0], False, carteirinha_completa=True) is False
    assert aceite._condicao_atendida(mascarados[0], False, carteirinha_completa=False) is True
    assert aceite._condicao_atendida(aceite.Passo("x", condicao="com_carteirinha_completa"), False) is False
