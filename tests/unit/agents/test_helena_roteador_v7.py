"""CONTRATO v7 DO ROTEADOR (dono, 23/10/2026, DL-0154; Plano de ajustes da Helena v2, etapa 7).

O modelo le' a mensagem NO CONTEXTO; o codigo valida e protege:
- o "sim" que nao e' palavra exata ("pode ser", "pode passar pra equipe sim, mas rapido") abre o caso;
- a recusa ("nao, deixa, desisto", "ja' resolvi") fecha P2/P3; P1 nunca se encerra por recusa;
- o alarme dito SO' pelo modelo so' e' "claro" com o sinal no vocabulario da tabela ("vomito" sozinho nao
  e'): claro -> 192 na hora; sem clareza -> a pergunta de alerta; o mesmo alarme ja' tratado nao da' 192 de
  novo;
- cortesia curta ("okk", "kkk", "valeuu") com a conversa em curso -> resposta curta, sem o classificador;
- JSON fora do vocabulario -> UMA re-pergunta; continuou ruim -> o caminho de sempre.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import roteador_v7 as rv7
from maezo.agents.helena.graph import RESPOSTA_ESCALONAMENTO_URGENTE, HelenaGraph
from maezo.agents.helena.habilidades.conversa import leitura_curta as lc
from maezo.agents.helena.habilidades.sintoma.textos import TEXTO_OFERTA_EQUIPE, TEXTO_OFERTA_RECUSADA
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_saude_gerativa import (
    _DONO,
    _INFO,
    _PESSOA,
    _SINTOMA,
    _Conversa,
    _DmnReal,
    _texto,
)


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
            coleta_enabled=True,
            helena_v4_enabled=True,
            helena_base_saude_enabled=True,
            base_saude=self.base,
            saude_gerativa=self.redator,
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


async def _ate_a_oferta(conversa: _ConversaAtiva) -> None:
    """Sintoma -> pergunta de alerta -> sem clareza -> de novo -> sem clareza -> a oferta da equipe."""
    alerta = await conversa.fala("ando com uma dor na lombar faz dois dias", decisao_v7("sintoma"), _SINTOMA)
    assert "responda sim ou não" in _texto(alerta).lower()
    await conversa.fala("hmm", decisao_v7("sintoma", responde=True, sim_nao="sem_clareza"))
    oferta = await conversa.fala("difícil dizer", decisao_v7("sintoma", responde=True, sim_nao="sem_clareza"))
    assert _texto(oferta) == TEXTO_OFERTA_EQUIPE


# =================================================================================================
# 1. O "sim" e a recusa lidos no contexto
# =================================================================================================


@pytest.mark.parametrize(
    "aceite", ["pode ser", "pode passar pra equipe sim, mas rápido", "manda pra eles, por gentileza"]
)
async def test_sim_no_contexto_a_oferta_abre_o_caso_de_saude(aceite: str) -> None:
    conversa = _ConversaAtiva()
    await _ate_a_oferta(conversa)
    caso = await conversa.fala(aceite, decisao_v7("equipe", responde=True, sim_nao="sim"))
    assert conversa.motivos == ["intencao_clinica"]
    assert RESPOSTA_ESCALONAMENTO_URGENTE not in _texto(caso), "caso de saude, nunca o 192 urgente"


@pytest.mark.parametrize("recusa", ["não, deixa, desisto", "já resolvi aqui", "melhor não, obrigada"])
async def test_recusa_no_contexto_a_oferta_fecha_sem_caso(recusa: str) -> None:
    conversa = _ConversaAtiva()
    await _ate_a_oferta(conversa)
    fim = await conversa.fala(recusa, decisao_v7("equipe", responde=True, sim_nao="nao", recusa=True))
    assert _texto(fim) == TEXTO_OFERTA_RECUSADA and conversa.cib.inicios == []


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
# 2. O alarme dito so' pelo modelo: claro so' com o sinal da tabela
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "sinal"),
    [
        ("tô passando muito mal, socorro", "falta de ar"),
        ("minha mãe tá estranha aqui", "desmaiou"),
        ("não tô legal não", "dor no peito"),
    ],
)
async def test_alarme_claro_do_modelo_no_vocabulario_da_tabela_da_192(mensagem: str, sinal: str) -> None:
    conversa = _ConversaAtiva()
    resposta = await conversa.fala(mensagem, decisao_v7("sintoma", sinais=[sinal], alarme="clear"))
    assert _e_192(_texto(resposta))
    assert conversa.motivos == ["red_flag_clinico"]


@pytest.mark.parametrize(
    ("mensagem", "sinal"),
    [
        ("vomitei duas vezes hoje", "vômito"),
        ("tô com febre desde ontem", "febre"),
        ("dor nas costas que não passa", "dor nas costas"),
    ],
)
async def test_alarme_claro_do_modelo_fora_da_tabela_nunca_da_192(mensagem: str, sinal: str) -> None:
    conversa = _ConversaAtiva()
    resposta = await conversa.fala(mensagem, decisao_v7("sintoma", sinais=[sinal], alarme="clear"), _SINTOMA)
    assert not _e_192(_texto(resposta).split("se for grave")[0])
    assert "red_flag_clinico" not in conversa.motivos


@pytest.mark.parametrize("sinal", ["falta de ar", "dor no peito", "desmaio"])
async def test_sinal_da_tabela_sem_clareza_e_a_pergunta_de_alerta(sinal: str) -> None:
    conversa = _ConversaAtiva()
    resposta = await conversa.fala(
        "acho que tô meio estranho", decisao_v7("conversa", sinais=[sinal], alarme="uncertain"), _SINTOMA
    )
    assert conversa.motivos == []
    assert "responda sim ou não" in _texto(resposta).lower()


async def test_o_mesmo_alarme_do_modelo_ja_tratado_nao_da_192_de_novo() -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("não tô legal não", decisao_v7("sintoma", sinais=["dor no peito"], alarme="clear"))
    assert conversa.motivos == ["red_flag_clinico"]
    de_novo = await conversa.fala(
        "ainda tá doendo aquilo", decisao_v7("sintoma", sinais=["dor no peito"], alarme="clear")
    )
    assert conversa.motivos == ["red_flag_clinico"], "nenhum caso novo"
    assert not _texto(de_novo).startswith("Ligue")


def test_vocabulario_da_tabela() -> None:
    grafo = HelenaGraph(
        inference=_ModeloAtivo(),  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=_Conversa().cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_Conversa().whatsapp,
    )
    for sinal in ("dor no peito", "vômito com sangue", "febre em bebê de 2 meses", "garganta inchando"):
        assert grafo._sinal_do_modelo_na_tabela(sinal), sinal
    for sinal in ("vômito", "febre", "dor nas costas", "enjoo"):
        assert not grafo._sinal_do_modelo_na_tabela(sinal), sinal


# =================================================================================================
# 3. Cortesia curta com a conversa em curso
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
# 4. O contrato: vocabulario fechado e UMA re-pergunta
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
