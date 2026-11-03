"""CONTRATO v7 DO ROTEADOR (dono, 23/10/2026, DL-0154; Plano de ajustes da Helena v2, etapa 7).

O modelo le' a mensagem NO CONTEXTO; o codigo valida e protege:
- a recusa da equipe ("nao, deixa, desisto", "ja' resolvi") sem caso aberto e' o combinado, sem caso;
- cortesia curta ("okk", "kkk", "valeuu") com a conversa em curso -> resposta curta, sem o classificador;
- JSON fora do vocabulario -> UMA re-pergunta; continuou ruim -> o caminho de sempre.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import roteador_v7 as rv7
from maezo.agents.helena.graph import HelenaGraph
from maezo.agents.helena.habilidades.conversa import leitura_curta as lc
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.helena_conversa import (
    _DONO,
    _INFO,
    _PESSOA,
    _Conversa,
    _DmnReal,
    _texto,
)
from tests.support.roteador_v7_fake import decisao_v7


class _ModeloAtivo:
    """O roteador le' a fila dele; o classificador, a fila de sempre."""

    def __init__(self) -> None:
        self.roteador: list[str] = []
        self.respostas: list[str] = []
        self.chamadas_do_roteador = 0
        self.model_id = "fake-glm"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            self.chamadas_do_roteador += 1
            return self.roteador.pop(0) if self.roteador else ""
        return self.respostas.pop(0) if self.respostas else ""


class _ConversaAtiva(_Conversa):
    """A conversa do dono com o roteador ATIVO (allowlist dele e da saude gerativa)."""

    def __init__(self) -> None:
        super().__init__()
        self.modelo = _ModeloAtivo()  # type: ignore[assignment]

    def _grafo(self) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=_DmnReal(),  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=FakeStartAuditSink(),
            whatsapp=self.whatsapp,
            historico_enabled=True,
            rota_global=self.rota,
            relogio=lambda: datetime(2026, 10, 23, 15, 0, tzinfo=UTC),
            helena_roteador="ativo",
            helena_roteador_conversas=frozenset({_DONO}),
        )
        return grafo.compile_graph().compile()

    async def fala(self, mensagem: str, roteador: str, extracao: str = _INFO) -> dict[str, Any]:
        self.modelo.roteador.append(roteador)  # type: ignore[attr-defined]
        return await self.diz(mensagem, extracao)


def _e_192(texto: str) -> bool:
    return "192" in texto


# =================================================================================================
# 1. A recusa lida no contexto
# =================================================================================================


@pytest.mark.parametrize("recusa", ["não, deixa, desisto", "já resolvi, valeu", "esquece a equipe"])
async def test_recusa_da_equipe_sem_caso_aberto_e_o_combinado(recusa: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("quero falar com alguém", decisao_v7("equipe"), _PESSOA)
    inicios = len(conversa.cib.inicios)
    with capture_logs() as logs:
        fim = await conversa.fala(recusa, decisao_v7("conversa", recusa=True))
    assert any(e.get("event") == "helena_recusa_de_equipe_pelo_roteador" for e in logs)
    assert len(conversa.cib.inicios) == inicios, "a recusa nunca abre caso"
    assert not _e_192(_texto(fim))


# =================================================================================================
# 2. Cortesia curta com a conversa em curso
# =================================================================================================


@pytest.mark.parametrize("curta", ["okk", "kkk", "valeuu"])
async def test_cortesia_curta_com_a_conversa_em_curso(curta: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("quero falar com alguém", decisao_v7("equipe"), _PESSOA)
    antes = len(conversa.modelo.respostas)  # type: ignore[attr-defined]
    resposta = await conversa.fala(curta, decisao_v7("conversa"))
    assert _texto(resposta) in (lc.TEXTO_COMBINADO, lc.TEXTO_DE_NADA)
    # A extracao enfileirada nao foi consumida: nenhum classificador para a cortesia.
    assert len(conversa.modelo.respostas) == antes + 1  # type: ignore[attr-defined]


# =================================================================================================
# 3. O contrato: vocabulario fechado e UMA re-pergunta
# =================================================================================================


def test_campo_fora_do_vocabulario_nunca_e_adivinhado() -> None:
    bom = decisao_v7("conversa")
    assert rv7.ler_decisao(bom)[0] is not None
    ruim = bom.replace('"conversa"', '"jardinagem"', 1)
    decisao, ruins = rv7.ler_decisao(ruim)
    assert decisao is None and ruins == ["skill"]
    assert rv7.ler_decisao("isto nao e json") == (None, ["(json)"])


async def test_uma_re_pergunta_com_o_aviso_dos_campos() -> None:
    conversa = _ConversaAtiva()
    conversa.modelo.roteador.append(decisao_v7("conversa").replace('"conversa"', '"x"', 1))  # type: ignore[attr-defined]
    with capture_logs() as logs:
        resposta = await conversa.fala("bom dia", decisao_v7("conversa"))
    assert conversa.modelo.chamadas_do_roteador == 2  # type: ignore[attr-defined]
    assert any(e.get("event") == "helena_roteador_reenvio" for e in logs)
    assert _texto(resposta)
