"""Camada 1 "regra geral" da cobertura (DL-0167, HEL-3b), com a AMOSTRA SINTETICA do K2 e a chave ligada.

Recuperacao por id de regra (exata), citacao interna do id (log, nunca o texto), verificador antes do
envio, e as travas do esqueleto por cima. Beneficiario sintetico; conteudo do K2 ficticio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.habilidades import cobertura as cob
from maezo.agents.helena.habilidades.cobertura import procedimento as proc
from maezo.agents.helena.habilidades.cobertura.manual import ManualK2, PinDoK2, RegraDoManual, carregar
from maezo.agents.helena.habilidades.cobertura.regra_geral import CamadaRegraGeral, texto_deterministico
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.ports.tina import ProcedimentoCatalogo
from tests.unit.agents.test_helena_cobertura_ampla import _entrada, _grafo, _ligada, _Modelo

_AMOSTRA = Path(__file__).parent / "helena_k2_amostra" / "pacote"
_PIN = PinDoK2(
    versao="amostra-0.1.0",
    sha256_manifesto="9af9c38159fc23b4b73e576bab19afbb87cb7562839906a67ff660320e6dd22a",
)


@pytest.fixture(scope="module")
def manual() -> ManualK2:
    return carregar(_AMOSTRA, pin=_PIN)


def _pergunta(texto: str, *antes: str) -> cob.PerguntaDeCobertura:
    return cob.PerguntaDeCobertura(texto=texto, falas_anteriores=antes)


async def test_recupera_a_regra_pelo_sinonimo_e_cita_o_id(manual: ManualK2) -> None:
    r = await CamadaRegraGeral(manual).responder(_pergunta("precisa de autorização pra fazer ressonância?"))
    assert r is not None and r.desfecho == "resposta"
    assert r.fontes == (cob.Fonte("regra_k2", "AMOSTRA-AUT-001", "amostra-0.1.0", _PIN.sha256_manifesto),)
    assert r.veredito_ok and r.trechos
    assert r.texto == texto_deterministico(manual.regra("AMOSTRA-AUT-001"))  # type: ignore[arg-type]
    assert "AMOSTRA" not in r.texto and "http" not in r.texto and r.texto.endswith(cob.FORMULA_FIXA)


@pytest.mark.parametrize(
    ("pergunta", "lacuna"),
    [
        ("o plano cobre transplante de medula?", "sem_regra"),
        ("quero saber de exame de imagem", "regras_multiplas"),  # sinonimo em DUAS regras: nada se escolhe
    ],
)
async def test_sem_regra_unica_transfere_com_a_lacuna(manual: ManualK2, pergunta: str, lacuna: str) -> None:
    r = await CamadaRegraGeral(manual).responder(_pergunta(pergunta))
    assert r == cob.RespostaDaCobertura("transferir", lacuna=lacuna)


@pytest.mark.parametrize("seguinte", ["e o transplante?", "e a tomografia?", "e o prazo?"])
async def test_a_fala_anterior_nao_puxa_a_regra_antiga(manual: ManualK2, seguinte: str) -> None:
    r = await CamadaRegraGeral(manual).responder(
        _pergunta(seguinte, "precisa de autorização pra ressonância?")
    )
    assert r == cob.RespostaDaCobertura("transferir", lacuna="sem_regra")


class _Redator:
    def __init__(self, texto: str) -> None:
        self.texto = texto

    async def redigir(self, pergunta: cob.PerguntaDeCobertura, regra: RegraDoManual) -> str | None:
        return self.texto


async def test_redacao_livre_que_confere_sai(manual: ManualK2) -> None:
    livre = (
        f"Para a ressonância marcada com antecedência, é preciso pedir autorização prévia. {cob.FORMULA_FIXA}"
    )
    r = await CamadaRegraGeral(manual, redator=_Redator(livre)).responder(
        _pergunta("ressonância precisa de guia?")
    )
    assert r is not None and r.texto == livre


@pytest.mark.parametrize(
    "inventado",
    [
        f"A fisioterapia tem até 30 sessões por pedido médico. {cob.FORMULA_FIXA}",  # quantidade inventada
        f"A fisioterapia não precisa de autorização. {cob.FORMULA_FIXA}",  # autorizacao sem fonte
        f"Seu plano cobre a fisioterapia. {cob.FORMULA_FIXA}",  # decide o caso
    ],
)
async def test_redacao_que_inventa_fato_e_trocada_pelo_texto_da_regra(
    manual: ManualK2, inventado: str
) -> None:
    with capture_logs() as logs:
        r = await CamadaRegraGeral(manual, redator=_Redator(inventado)).responder(
            _pergunta("e a fisioterapia?")
        )
    assert r is not None and r.texto == texto_deterministico(manual.regra("AMOSTRA-QTD-003"))  # type: ignore[arg-type]
    assert any(e.get("event") == "helena_cobertura_rascunho_barrado" for e in logs)


_CATALOGO = (
    ProcedimentoCatalogo("99000001", "Ressonância magnética de joelho", "ressonancia magnetica joelho", "RM"),
    ProcedimentoCatalogo("99000002", "Ressonância magnética de crânio", "ressonancia magnetica cranio", "RM"),
)


async def test_procedimento_ambiguo_gera_uma_pergunta_de_esclarecimento(manual: ManualK2) -> None:
    camada = CamadaRegraGeral(manual, catalogo=_CATALOGO, sinonimos=proc.carregar_sinonimos())
    r = await camada.responder(_pergunta("o plano cobre ressonância?"))
    assert r is not None and r.desfecho == "esclarecer" and r.texto and r.texto.count("?") == 1


# --- Pelo grafo inteiro, chave ligada -----------------------------------------------------------------


async def test_pelo_grafo_a_regra_geral_sai_literal_e_o_id_vai_ao_log(manual: ManualK2) -> None:
    grafo, whatsapp, cib = _grafo(_Modelo(), _ligada(CamadaRegraGeral(manual)))
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("precisa de autorização pra fazer ressonância?"))
    assert whatsapp.enviados[-1] == texto_deterministico(manual.regra("AMOSTRA-AUT-001"))  # type: ignore[arg-type]
    [log] = [e for e in logs if e.get("event") == "helena_cobertura_respondeu"]
    assert log["fontes"] == ["regra_k2:AMOSTRA-AUT-001@amostra-0.1.0"]


async def test_pelo_grafo_prazo_ratificado_do_k2_sai_inteiro(manual: ManualK2) -> None:
    """HEL-5b (ROOT, 16/11/2026): o prazo em "dias uteis" da regra do K2, com o hash do pacote e o veredito ok
    do verificador, passa pela trava `sem_prazo_da_ans`; antes caia no aviso de canais."""
    grafo, whatsapp, _cib = _grafo(
        _Modelo(), _ligada(CamadaRegraGeral(manual), hash_do_k2=_PIN.sha256_manifesto)
    )
    await grafo.ainvoke(_entrada("como faço pra recorrer?"))
    assert whatsapp.enviados[-1] == texto_deterministico(manual.regra("AMOSTRA-REC-005"))  # type: ignore[arg-type]
    assert "10 dias úteis" in whatsapp.enviados[-1]


class _CamadaComPrazo:
    """Camada que devolve um texto com prazo da ANS, com e sem as condicoes da isencao (testes negativos)."""

    def __init__(self, resposta: cob.RespostaDaCobertura) -> None:
        self._resposta = resposta

    @property
    def camada(self) -> cob.Camada:
        return "regra_geral"

    async def responder(self, pergunta: cob.PerguntaDeCobertura) -> cob.RespostaDaCobertura:
        return self._resposta


_COM_PRAZO = f"Pela regra geral: a nova análise pode ser pedida em até 10 dias úteis. {cob.FORMULA_FIXA}"
_TRECHO = "Texto fictício: a nova análise pode ser pedida em até 10 dias úteis."
_K2 = cob.Fonte("regra_k2", "R-1", "v", _PIN.sha256_manifesto)


def _resp(**kw: Any) -> cob.RespostaDaCobertura:
    base: dict[str, Any] = {"fontes": (_K2,), "veredito_ok": True, "trechos": (_TRECHO,)}
    desfecho = kw.pop("desfecho", "resposta")
    return cob.RespostaDaCobertura(desfecho, _COM_PRAZO, **{**base, **kw})


async def test_prazo_com_todas_as_condicoes_da_isencao_sai() -> None:
    """Controle positivo dos negativos abaixo: veredito ok, fonte K2 com o hash PINADO e trechos que batem."""
    config = _ligada(_CamadaComPrazo(_resp()), hash_do_k2=_PIN.sha256_manifesto)
    grafo, whatsapp, _cib = _grafo(_Modelo(), config)
    await grafo.ainvoke(_entrada("como faço pra recorrer?"))
    assert whatsapp.enviados[-1] == _COM_PRAZO


_PINADO = _PIN.sha256_manifesto
_LAGO = cob.Fonte("lago", "/cobertura", "v1.9")


@pytest.mark.parametrize(
    ("resposta", "hash_pinado"),
    [
        (_resp(veredito_ok=False), _PINADO),  # sem veredito do verificador
        (_resp(fontes=(cob.Fonte("regra_k2", "R-1", "v"),)), _PINADO),  # fonte K2 sem hash
        (_resp(fontes=(cob.Fonte("regra_k2", "R-1", "v", "f" * 64),)), _PINADO),  # hash diferente do pin
        (_resp(), None),  # composicao sem o hash do K2 pinado
        (_resp(fontes=(_LAGO,)), _PINADO),  # fonte do lago
        (_resp(fontes=(_K2, _LAGO)), _PINADO),  # fontes mistas
        (_resp(trechos=()), _PINADO),  # sem trechos para reconferir
        (_resp(trechos=("Texto fictício: em até 30 dias úteis.",)), _PINADO),  # trecho que nao confere
        (_resp(desfecho="esclarecer"), _PINADO),  # esclarecimento com prazo
    ],
)
async def test_prazo_sem_as_condicoes_da_isencao_continua_barrado(
    resposta: cob.RespostaDaCobertura, hash_pinado: str | None
) -> None:
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(_CamadaComPrazo(resposta), hash_do_k2=hash_pinado))
    await grafo.ainvoke(_entrada("como faço pra recorrer?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA


def test_prazo_vindo_do_modelo_continua_cortado_na_saida() -> None:
    """A trava do `respond` corta o prazo de qualquer texto sem a marca (o do modelo, inclusive)."""
    from maezo.agents.helena import travas_saida

    assert "dias úteis" not in travas_saida.na_saida(_COM_PRAZO)
    assert cob.prazo_ratificado({"conversation_id": "c-1"}, _COM_PRAZO) is False  # sem marca: nada e' isento
    assert travas_saida.na_saida(_COM_PRAZO, prazo_ratificado=True) == _COM_PRAZO


def test_a_marca_e_por_conversa_de_uso_unico_e_nunca_fica_pendurada() -> None:
    """Revisao independente da HEL-5b: a marca vale so' na conversa e no turno em que foi dada."""
    assert cob.prazo_ratificado({"conversation_id": ""}, _COM_PRAZO) is False  # sem id: nunca isenta
    cob._guardar(_COM_PRAZO, prazo_da_conversa=":c-1")
    assert cob.prazo_ratificado({"conversation_id": "c-2"}, _COM_PRAZO) is False  # outra conversa
    assert cob.prazo_ratificado({"conversation_id": "c-1"}, _COM_PRAZO) is True
    assert cob.prazo_ratificado({"conversation_id": "c-1"}, _COM_PRAZO) is False  # uso unico
    cob._guardar(_COM_PRAZO, prazo_da_conversa=":c-1")
    assert cob.prazo_ratificado({"conversation_id": "c-1"}, "Entendi. " + _COM_PRAZO) is False  # transformado
    assert cob.prazo_ratificado({"conversation_id": "c-1"}, _COM_PRAZO) is False  # e a marca nao ficou
