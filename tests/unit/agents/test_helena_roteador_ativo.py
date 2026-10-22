"""Roteador de habilidades ATIVO (ADR-0066, DL-0136, F2): decide só na allowlist; travas antes; pendência
só vale quando a mensagem responde a ela."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from structlog.testing import capture_logs

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena import roteador as rt
from maezo.agents.helena.cobertura_fluxo import PEND_RESPONDIDO
from maezo.agents.helena.graph import HelenaGraph
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_cobertura_assistida import _busca, _FonteGrafo, _Item, _json_pedido, _View
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _classify, _entrada, _WhatsApp
from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte

_HEMO = {"40304361": _View((_Item("HEMOGRAMA"),))}


def _decisao(habilidade: str, *, confianca: str = "alta", responde: bool = False, **extra: Any) -> str:
    return json.dumps(
        {"habilidade": habilidade, "confianca": confianca, "responde_a_pendencia": responde, **extra}
    )


class _Modelo:
    """O roteador recebe as decisões dele em ordem; as outras chamadas seguem a fila de sempre."""

    def __init__(self, roteador: list[str], respostas: list[str]) -> None:
        self.roteador = list(roteador)
        self.respostas = list(respostas)
        self.outras = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            return self.roteador.pop(0) if self.roteador else ""
        self.outras += 1
        return self.respostas.pop(0) if self.respostas else ""


def _grafo(
    modelo: _Modelo, *, na_allowlist: bool = True
) -> tuple[Any, _WhatsApp, FakeCibSevenTransport, Any]:
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    fonte = _FonteGrafo(_HEMO)
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 21, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        catalogo_procedimentos=_busca(),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}) if na_allowlist else frozenset(),
    )
    return grafo.compile_graph().compile(), whatsapp, cib, fonte


async def test_cobertura_vai_direto_a_consulta_sem_o_classificador() -> None:
    modelo = _Modelo([_decisao("cobertura")], [_json_pedido("hemograma"), ""])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    resultado = await grafo.ainvoke(_entrada("o convênio paga um hemograma pra mim?"))
    assert fonte.pedidos == ["40304361"] and resultado["coleta_pendente"] == PEND_RESPONDIDO
    assert "consta no Rol" in whatsapp.enviados[-1]


async def test_pendencia_que_nao_e_respondida_e_descartada_e_nao_sequestra() -> None:
    modelo = _Modelo([_decisao("plano", responde=False)], [_classify(intent="information")])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    contexto = ca.Contexto(termos=["hemograma"], quem="titular", respondidos=["Hemograma"]).codificar()
    resultado = await grafo.ainvoke(
        _entrada("e quanto tempo de carência falta?")
        | {"coleta_pendente": PEND_RESPONDIDO, "coleta_contexto": contexto}
    )
    assert fonte.pedidos == [], "a pendencia da cobertura nao pegou a mensagem"
    assert resultado.get("coleta_pendente") in (None, "")
    assert ca.TEXTO_PEDE_LISTA not in whatsapp.enviados[-1]


async def test_resposta_a_pendencia_mantem_a_pendencia_da_habilidade() -> None:
    modelo = _Modelo([_decisao("cobertura", responde=True)], [])
    grafo, whatsapp, _cib, _fonte = _grafo(modelo)
    contexto = ca.Contexto(termos=["hemograma", "glicose"], quem="titular").codificar()
    await grafo.ainvoke(
        _entrada("faltou algum?") | {"coleta_pendente": PEND_RESPONDIDO, "coleta_contexto": contexto}
    )
    assert whatsapp.enviados[-1] == ca.texto_do_que_respondi(["hemograma", "glicose"])


async def test_cortesia_sem_pendencia_e_resposta_curta() -> None:
    modelo = _Modelo([_decisao("conversa")], [])
    grafo, whatsapp, _cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("valeu!"))
    assert whatsapp.enviados[-1] == ca.TEXTO_DE_NADA and modelo.outras == 0


async def test_duvida_pergunta_x_ou_y_e_a_resposta_volta_ao_roteador() -> None:
    modelo = _Modelo(
        [
            _decisao("duvida", habilidade_provavel="cobertura", alternativa="cobranca"),
            _decisao("cobertura", responde=True),
        ],
        [_json_pedido("hemograma"), ""],
    )
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("e a coparticipação do hemograma?"))
    assert "cobertura de exames e procedimentos" in whatsapp.enviados[-1]
    assert "mensalidade e pagamentos" in whatsapp.enviados[-1]
    assert r1["coleta_pendente"] == f"{rt.PREFIXO_DUVIDA}cobertura:cobranca"
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    r2 = await grafo.ainvoke(_entrada("hemograma, se cobre") | memoria)
    assert fonte.pedidos == ["40304361"] and not str(r2.get("coleta_pendente") or "").startswith("ROT_")


async def test_trava_de_alarme_vence_o_roteador() -> None:
    modelo = _Modelo([_decisao("cobertura")], [_classify(intent="symptom")])
    grafo, whatsapp, cib, fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("estou com dor no peito forte e falta de ar, o plano cobre?"))
    assert fonte.pedidos == []
    assert "192" in whatsapp.enviados[-1]
    assert any(
        e.get("event") == "helena_roteador_caminho_de_sempre" and e.get("motivo") == "trava" for e in logs
    )


async def test_roteador_que_falha_cai_no_caminho_de_sempre() -> None:
    modelo = _Modelo(["isto nao e json"], [_classify(intent="greeting")])
    grafo, whatsapp, _cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("bom dia"))
    assert whatsapp.enviados
    assert any(e.get("event") == "helena_roteador_caminho_de_sempre" for e in logs)


async def test_fora_da_allowlist_do_roteador_e_sombra() -> None:
    modelo = _Modelo([_decisao("conversa")], [_classify(intent="greeting")])
    grafo, whatsapp, _cib, _fonte = _grafo(modelo, na_allowlist=False)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("valeu!"))
    assert whatsapp.enviados[-1] != ca.TEXTO_DE_NADA or modelo.outras == 1
    assert not any(e.get("event") == "helena_roteador_decidiu" for e in logs)


def test_opcoes_da_duvida() -> None:
    d = rt.DecisaoDoRoteador("duvida", "baixa", "cobranca", False, provavel="cobertura")
    assert d.opcoes_da_duvida() == ("cobertura", "cobranca")
    assert rt.DecisaoDoRoteador("cobertura", "baixa", "plano", False).opcoes_da_duvida() == (
        "cobertura",
        "plano",
    )
    assert rt.DecisaoDoRoteador("cobertura", "media", "plano", False).opcoes_da_duvida() is None
    assert rt.DecisaoDoRoteador("cobertura", "baixa", None, False).opcoes_da_duvida() is None


async def test_reclamacao_sobre_a_resposta_fica_com_a_dona_da_pendencia() -> None:
    modelo = _Modelo([_decisao("equipe", responde=False)], [])
    grafo, whatsapp, cib, _fonte = _grafo(modelo)
    contexto = ca.Contexto(termos=["hemograma"], quem="titular", respondidos=["Hemograma"]).codificar()
    await grafo.ainvoke(
        _entrada("Ué, como assim? Você acabou de verificar e falou outra coisa com a equipe")
        | {"coleta_pendente": PEND_RESPONDIDO, "coleta_contexto": contexto}
    )
    from tests.unit.agents.test_helena_rodada_3_simulador import _starts

    assert _starts(cib) == 0, "reclamacao sem pedido nao abre caso"
    assert whatsapp.enviados[-1].startswith(ca.TEXTO_DESCULPA)


async def test_roteador_plano_nao_cai_na_cobertura_assistida() -> None:
    modelo = _Modelo(
        [_decisao("plano")],
        [_classify(intent="consulta_plano", consulta_subtipo="elegibilidade")],
    )
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("beleza. e eu ainda estou em carência de alguma coisa?"))
    assert not any(e.get("event") == "helena_cobertura_extraida" for e in logs)
    assert ca.TEXTO_PEDE_LISTA not in (whatsapp.enviados[-1] if whatsapp.enviados else "")
    assert fonte.pedidos == []


class _ModeloQueEspera(_Modelo):
    """O roteador so' responde DEPOIS que a extracao da cobertura comecou (prova o paralelo)."""

    def __init__(self, roteador: list[str], respostas: list[str]) -> None:
        super().__init__(roteador, respostas)
        self.extracao_comecou = asyncio.Event()
        self.extracoes = 0

    async def generate(self, prompt: str, **kw: Any) -> str:
        if "roteador de um assistente" in prompt:
            await asyncio.wait_for(self.extracao_comecou.wait(), timeout=2)
        elif prompt.startswith(ca.PROMPT_EXTRACAO):
            self.extracoes += 1
            self.extracao_comecou.set()
        return await super().generate(prompt, **kw)


async def test_extracao_da_cobertura_roda_junto_com_o_roteador_e_e_reaproveitada() -> None:
    modelo = _ModeloQueEspera([_decisao("cobertura")], [_json_pedido("hemograma"), ""])
    grafo, whatsapp, _cib, fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("o convênio paga um hemograma pra mim?"))
    assert modelo.extracoes == 1, "a extracao antecipada e' a MESMA (nunca duas)"
    assert any(e.get("event") == "helena_cobertura_extracao_antecipada" for e in logs)
    assert fonte.pedidos == ["40304361"] and "consta no Rol" in whatsapp.enviados[-1]


async def test_outra_habilidade_descarta_a_extracao_antecipada() -> None:
    modelo = _Modelo([_decisao("plano")], [_json_pedido("hemograma"), _classify(intent="information")])
    grafo, _whatsapp, _cib, fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("o convênio paga um hemograma pra mim?"))
    assert fonte.pedidos == [], "a decisao do roteador vale: nada de consulta de cobertura"
    assert not any(e.get("event") == "helena_cobertura_extracao_antecipada" for e in logs)


async def test_trava_de_entrada_nao_antecipa_extracao() -> None:
    modelo = _ModeloQueEspera([], [_classify(intent="symptom")])
    modelo.extracao_comecou.set()
    grafo, whatsapp, _cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada("estou com dor no peito forte e falta de ar, o plano cobre?"))
    assert modelo.extracoes == 0 and "192" in whatsapp.enviados[-1]
