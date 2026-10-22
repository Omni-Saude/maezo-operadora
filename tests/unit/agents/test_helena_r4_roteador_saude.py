"""Roteador ativo x fluxo de saude (avaliacao r4 de 22/10/2026): pedir uma pessoa no meio das perguntas de
alerta nunca vira emergencia falsa, e o pedido de especialista (habilidade `saude`) recebe o texto dele.

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.graph import HelenaGraph
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_cobertura_assistida import _busca, _FonteGrafo
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _classify, _entrada, _WhatsApp
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _Modelo
from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte


def _grafo(modelo: _Modelo) -> tuple[Any, _WhatsApp, Any]:
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        coleta_enabled=True,
        helena_v4_enabled=True,
        consultas_plano=_FonteGrafo({}),  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 22, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        catalogo_procedimentos=_busca(),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
    )
    return grafo.compile_graph().compile(), whatsapp, cib


class _ModeloPorTexto(_Modelo):
    """O classificador responde pelo TEXTO da mensagem atual (nao pela ordem da fila)."""

    def __init__(self, roteador: list[str], pedido: str) -> None:
        super().__init__(roteador, [])
        self.pedido = pedido

    async def generate(self, prompt: str, **kw: Any) -> str:
        if "roteador de um assistente" in prompt:
            return await super().generate(prompt, **kw)
        if self.pedido in prompt:
            return _sintoma_do_historico()
        return _classify(intent="symptom")


def _sintoma_do_historico() -> str:
    # O modelo devolve o codigo do sintoma do turno anterior (le' o historico), sem conteudo clinico no texto.
    return _classify(
        intent="human_request", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
    )


@pytest.mark.parametrize(
    "pedido",
    ["Quero falar com uma pessoa", "me passa para um atendente", "prefiro falar com alguém da equipe"],
)
async def test_pedido_de_pessoa_depois_do_sintoma_nunca_vira_emergencia(pedido: str) -> None:
    modelo = _ModeloPorTexto([_decisao("sintoma"), _decisao("equipe")], pedido)
    grafo, whatsapp, cib = _grafo(modelo)
    r1 = await grafo.ainvoke(_entrada("Estou com dor de cabeça"))
    memoria = {
        k: r1[k]
        for k in ("coleta_pendente", "coleta_contexto", "historico_conversa", "memoria_clinica")
        if k in r1
    }
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(pedido) | memoria)
    # O sintoma que o modelo trouxe do historico nao vai a tabela de alarme (era o 192 falso).
    assert any(e.get("event") == "helena_habilidade_sem_sintoma_do_historico" for e in logs)
    assert not resultado.get("sintoma_codigo")
    assert resultado.get("escalation_motivo") != "red_flag_clinico"
    assert "ligue 192 (samu) agora" not in whatsapp.enviados[-1].lower()


@pytest.mark.parametrize(
    "fala",
    [
        "Preciso consultar com um cardiologista",
        "quero marcar um dermatologista",
        "Onde tem ortopedista pelo plano?",
    ],
)
async def test_pedido_de_especialista_na_habilidade_saude_recebe_o_texto_do_especialista(fala: str) -> None:
    modelo = _Modelo([_decisao("saude")], [_classify(intent="information"), "", ""])
    grafo, whatsapp, cib = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1].lower()
    assert "a equipe confirma para você" not in texto, "nunca a oferta da cobertura"
    assert "aplicativo" in texto or "portal" in texto
