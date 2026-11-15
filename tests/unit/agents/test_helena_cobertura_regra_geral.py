"""Camada 1 "regra geral" da cobertura (DL-0167, HEL-3b), com a AMOSTRA SINTETICA do K2 e a chave ligada.

Recuperacao por id de regra (exata), citacao interna do id (log, nunca o texto), verificador antes do
envio, e as travas do esqueleto por cima. Beneficiario sintetico; conteudo do K2 ficticio."""

from __future__ import annotations

from pathlib import Path

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
    sha256_manifesto="b299c4d561d956d894a9c86bc01630ef1fea265083b49719d53a63ea7b388176",
)


@pytest.fixture(scope="module")
def manual() -> ManualK2:
    return carregar(_AMOSTRA, pin=_PIN)


def _pergunta(texto: str, *antes: str) -> cob.PerguntaDeCobertura:
    return cob.PerguntaDeCobertura(texto=texto, falas_anteriores=antes)


async def test_recupera_a_regra_pelo_sinonimo_e_cita_o_id(manual: ManualK2) -> None:
    r = await CamadaRegraGeral(manual).responder(_pergunta("precisa de autorização pra fazer ressonância?"))
    assert r is not None and r.desfecho == "resposta"
    assert r.fontes == (cob.Fonte("regra_k2", "AMOSTRA-AUT-001", "amostra-0.1.0"),)
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


async def test_resposta_curta_usa_a_fala_anterior(manual: ManualK2) -> None:
    r = await CamadaRegraGeral(manual).responder(_pergunta("e a fisio?", "o plano cobre umas coisas"))
    assert r is not None and r.fontes[0].id == "AMOSTRA-QTD-003"


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


async def test_pelo_grafo_prazo_em_dias_uteis_ainda_cai_no_aviso(manual: ManualK2) -> None:
    """PENDENCIA DO DONO (DL-0167): a trava de saida `sem_prazo_da_ans` corta frase com "dias uteis"; o
    esqueleto recusa o texto que ela mudaria. Ate' o dono decidir, prazo do K2 nao sai (falha fechada)."""
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(CamadaRegraGeral(manual)))
    await grafo.ainvoke(_entrada("como faço pra recorrer?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
