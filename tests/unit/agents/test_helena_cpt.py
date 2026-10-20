"""Aviso GERAL de CPT na cobertura assistida (caso C1 do dono e DPO de 20/10/2026, DL-0130).

So' para o TITULAR com CPT vigente no cadastro (rota `tina-cpt`, manifest v1.15): um aviso por resposta, com a
data DD/MM/AAAA quando existe. Sem CPT, sem leitura, falha, dependente ou terceiro: NADA sobre CPT. A leitura
fica em memoria por titular por 5 min (limite por sujeito compartilhado com a cobertura) e nunca vai ao
estado, ao contexto nem a log. Dados 100% sinteticos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_fluxo import PEND_RESPONDIDO, Dependencias, FluxoDeCobertura
from maezo.conhecimento.catalogo_procedimentos import BuscaDeProcedimentos, carregar_sinonimos
from tests.unit.agents.test_helena_cobertura_assistida import (
    _CATALOGO,
    _FonteGrafo,
    _Item,
    _json_pedido,
    _Mundo,
    _pedido,
    _View,
)

pytestmark = pytest.mark.anyio

_COM_DATA = (
    "Pelo cadastro, o seu plano tem uma cobertura parcial temporária (CPT) válida até 01/03/2027. Enquanto "
    "ela vale, cirurgias, leitos de alta tecnologia e procedimentos de alta complexidade ligados à doença ou "
    "lesão preexistente declarada na contratação podem não ter cobertura. Quem confirma, caso a caso, é a "
    "análise da autorização."
)


@dataclass(frozen=True)
class _Cpt:
    tem_cpt_vigente: bool = True
    cpt_vigentes: int = 1
    termina_em: str | None = "2027-03-01"
    portable_subject_ref: str = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
    fonte_atualizada_em: str | None = "2026-10-20T12:00:00Z"


class _LeitorCpt:
    def __init__(self, resposta: Any = None, erro: BaseException | None = None) -> None:
        self.resposta = resposta
        self.erro = erro
        self.chamadas = 0

    async def __call__(self) -> Any:
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        return self.resposta


async def _por_codigo(termos: Sequence[str], termo: str, nomes: Sequence[str]) -> ca.Escolha:
    """Escolhe com confianca alta o candidato do codigo que casa com o termo (como o modelo faria)."""
    alvos = {"hemograma": "40304361", "tomografia de torax": "40808041", "ultrassom do abdome": "40901122"}
    alvo = next(c for t, c in alvos.items() if t in termo)
    por_nome = {p.nome: p.codigo for p in _CATALOGO}
    return ca.Escolha(next(i for i, n in enumerate(nomes) if por_nome.get(n) == alvo), "alta")


def _fluxo(m: _Mundo, cpt: Any) -> FluxoDeCobertura:
    busca = BuscaDeProcedimentos(transporte=None, sinonimos=carregar_sinonimos())
    busca.carregar(_CATALOGO, "v1")
    return FluxoDeCobertura(
        Dependencias(
            extrair=m.extrair,
            cobertura=m.cobertura,
            dependentes=m.dependentes,
            elegibilidade=m.eleg,
            busca=busca,
            pergunta_para_quem=lambda graus, com_nome: "Para quem é?",
            texto_geral=m.geral,
            escolher=_por_codigo,
            cpt=cpt,
        )
    )


def _mundo(*procs: str, para_quem: str = "titular", graus: list[str] | None = None) -> _Mundo:
    return _Mundo(
        pedido=_pedido(*procs, para_quem=para_quem),
        coberturas={
            "40808041": _View((_Item("TC TORAX"),)),
            "40304361": _View((_Item("HEMOGRAMA"),)),
            "40901122": _View((_Item("US ABDOME TOTAL"),)),
        },
        graus=graus or [],
    )


# --- texto puro --------------------------------------------------------------------------------------------
def test_texto_com_data_em_dd_mm_aaaa() -> None:
    assert ca.texto_cpt(_Cpt()) == _COM_DATA


def test_texto_sem_data_diz_vigente() -> None:
    texto = ca.texto_cpt(_Cpt(termina_em=None, cpt_vigentes=2))
    assert "cobertura parcial temporária (CPT) vigente. Enquanto ela vale" in texto
    assert "até" not in texto.split(".")[0]


@pytest.mark.parametrize(
    "view",
    [None, _Cpt(tem_cpt_vigente=False, cpt_vigentes=0, termina_em=None), object(), {"tem_cpt_vigente": True}],
)
def test_sem_cpt_vigente_nenhum_texto(view: Any) -> None:
    assert ca.texto_cpt(view) == ""


def test_data_fora_do_formato_vira_vigente() -> None:
    assert "(CPT) vigente." in ca.texto_cpt(_Cpt(termina_em="01/03/2027"))


def test_o_texto_nunca_cita_doenca_nem_cid() -> None:
    texto = ca.normalizar(_COM_DATA)
    for palavra in ("cid", "diagnostico", "cancer", "diabetes"):
        assert palavra not in texto.split()


# --- fluxo -------------------------------------------------------------------------------------------------
async def test_titular_com_cpt_recebe_o_aviso_uma_vez() -> None:
    leitor = _LeitorCpt(_Cpt())
    with structlog.testing.capture_logs() as logs:
        up = await _fluxo(_mundo("tomografia de torax"), leitor).responder(
            "cobre tomografia de torax?", "", ""
        )
    assert up["coleta_pendente"] == PEND_RESPONDIDO
    assert up["response_text"].count("cobertura parcial temporária") == 1
    assert up["response_text"].endswith(_COM_DATA)
    assert leitor.chamadas == 1
    # nada da CPT no contexto/estado nem em log (so' o token fechado)
    assert "CPT" not in str(up.get("coleta_contexto")) and "2027" not in str(up.get("coleta_contexto"))
    [linha] = [e for e in logs if e["event"] == "helena_cobertura_cpt"]
    assert linha == {"event": "helena_cobertura_cpt", "log_level": "info", "aviso": True}
    assert "2027" not in repr(logs)


@pytest.mark.parametrize(
    "leitor",
    [
        _LeitorCpt(_Cpt(tem_cpt_vigente=False, cpt_vigentes=0, termina_em=None)),
        _LeitorCpt(None),
        _LeitorCpt(erro=TimeoutError()),
        _LeitorCpt(erro=ConnectionError()),
    ],
)
async def test_sem_cpt_ou_falha_nada_sobre_cpt(leitor: _LeitorCpt) -> None:
    up = await _fluxo(_mundo("tomografia de torax"), leitor).responder("cobre tomografia de torax?", "", "")
    assert "consta no Rol" in up["response_text"]
    assert "CPT" not in up["response_text"] and "parcial" not in up["response_text"]


async def test_sem_a_dependencia_nada_sobre_cpt() -> None:
    up = await _fluxo(_mundo("tomografia de torax"), None).responder("cobre tomografia de torax?", "", "")
    assert "consta no Rol" in up["response_text"] and "CPT" not in up["response_text"]


async def test_dependente_nunca_recebe_o_aviso_nem_le_a_cpt() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _mundo("tomografia de torax", para_quem="dependente", graus=["filho ou filha"])
    up = await _fluxo(m, leitor).responder("cobre tomografia de torax pro meu filho?", "", "")
    assert "consta no Rol" in up["response_text"] and "CPT" not in up["response_text"]
    assert leitor.chamadas == 0


async def test_terceiro_nunca_le_a_cpt() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _mundo("tomografia de torax", para_quem="terceiro")
    up = await _fluxo(m, leitor).responder("cobre tomografia de torax pra minha mae?", "", "")
    assert "CPT" not in up["response_text"] and leitor.chamadas == 0


async def test_varios_procedimentos_o_aviso_sai_uma_vez() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _mundo("hemograma", "tomografia de torax")
    up = await _fluxo(m, leitor).responder("cobre hemograma e tomografia de torax?", "", "")
    assert "1) " in up["response_text"] and "2) " in up["response_text"]
    assert up["response_text"].count("cobertura parcial temporária") == 1
    assert leitor.chamadas == 1


async def test_varios_sem_nenhum_respondido_nao_le_a_cpt() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _mundo("hemograma", "tomografia de torax")
    m.falhar = True
    up = await _fluxo(m, leitor).responder("cobre hemograma e tomografia de torax?", "", "")
    assert "CPT" not in up["response_text"] and leitor.chamadas == 0


async def test_mesma_resposta_dos_parecidos_leva_o_aviso_uma_vez() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _Mundo(
        coberturas={
            "40901122": _View((_Item("US ABDOME TOTAL"),)),
            "40901114": _View((_Item("US ABDOME SUPERIOR"),)),
        }
    )
    fluxo = _fluxo(m, leitor)
    ctx = ca.Contexto(termos=["ultrassom do abdome"], quem="titular")
    up = await fluxo._mesma_resposta(
        ctx, [("40901122", "US ABDOME TOTAL"), ("40901114", "US ABDOME SUPERIOR")], prefixo=""
    )
    assert up is not None
    assert up["response_text"].count("cobertura parcial temporária") == 1 and leitor.chamadas == 1


async def test_falha_da_cobertura_nao_le_a_cpt() -> None:
    leitor = _LeitorCpt(_Cpt())
    m = _mundo("tomografia de torax")
    m.falhar = True
    up = await _fluxo(m, leitor).responder("cobre tomografia de torax?", "", "")
    assert up["coleta_pendente"] == ca.PEND_FALHA and leitor.chamadas == 0


# --- grafo: memoria de 5 min por titular -----------------------------------------------------------------
class _FonteComCpt(_FonteGrafo):
    def __init__(self, coberturas: dict[str, Any], cpt: Any) -> None:
        super().__init__(coberturas)
        self.cpt = cpt
        self.leituras_cpt: list[str] = []

    async def consultar_cpt(self, ref: str) -> Any:
        self.leituras_cpt.append(ref)
        return self.cpt


def _grafo(fonte: _FonteComCpt, respostas: list[str], agora: list[datetime]) -> tuple[Any, Any, Any]:
    from maezo.agents.helena.graph import HelenaGraph
    from maezo.conhecimento import saude_gerativa as sg
    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink
    from tests.unit.agents.test_helena_cobertura_assistida import _busca
    from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _Modelo, _WhatsApp
    from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte

    whatsapp = _WhatsApp()
    objeto = HelenaGraph(
        inference=_Modelo(respostas),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: agora[0],
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        catalogo_procedimentos=_busca(),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
    )
    return objeto.compile_graph().compile(), whatsapp, objeto


async def test_grafo_le_a_cpt_uma_vez_em_5_minutos_e_nada_vai_ao_estado() -> None:
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    agora = [datetime(2026, 10, 20, 15, 0, tzinfo=UTC)]
    fonte = _FonteComCpt({"40808041": _View((_Item("TC TORAX"),))}, _Cpt())
    respostas = [_json_pedido("tomografia computadorizada de tórax"), ""] * 3
    grafo, whatsapp, objeto = _grafo(fonte, respostas, agora)
    r1 = await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert fonte.pedidos == ["40808041"]
    assert whatsapp.enviados[-1].count("cobertura parcial temporária") == 1
    assert "01/03/2027" in whatsapp.enviados[-1]
    assert len(fonte.leituras_cpt) == 1
    estado = repr({k: v for k, v in r1.items() if k != "response_text"})
    assert "parcial temporária" not in estado and "2027-03-01" not in estado
    # 2a resposta dentro de 5 min: a memoria responde, a rota nao e' chamada de novo
    agora[0] += timedelta(minutes=4)
    objeto._cobertura_em_memoria.clear()
    await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert len(fonte.leituras_cpt) == 1
    assert whatsapp.enviados[-1].count("cobertura parcial temporária") == 1
    # depois de 5 min, uma leitura nova
    agora[0] += timedelta(minutes=2)
    await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert len(fonte.leituras_cpt) == 2


async def test_grafo_sem_cpt_nada_sobre_cpt_e_a_memoria_vale_tambem() -> None:
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    agora = [datetime(2026, 10, 20, 15, 0, tzinfo=UTC)]
    fonte = _FonteComCpt({"40808041": _View((_Item("TC TORAX"),))}, None)
    grafo, whatsapp, _ = _grafo(fonte, [_json_pedido("tomografia computadorizada de tórax"), ""] * 2, agora)
    await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert "consta no Rol da ANS" in whatsapp.enviados[-1] and "CPT" not in whatsapp.enviados[-1]
    await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert len(fonte.leituras_cpt) == 1


def test_a_memoria_da_cpt_dura_5_minutos() -> None:
    from maezo.agents.helena import graph

    assert graph.CPT_EM_MEMORIA_S == 300.0
