"""Trava de entrada SO' com frase AFIRMADA clara (teste do dono de 25/10/2026, caso b; complemento ADR-0012)
e o codigo do historico fora da tabela depois do alerta respondido (avaliacao r5b, 25/10/2026).

Caso b: depois da pergunta de alerta, "nao, mas o que dor no peito ... tem a ver" fez a trava lexica achar os
sinais e abrir P1 + 192 sem o roteador. Sinal dentro de PERGUNTA, depois de "nao" ou copiado da pergunta da
Helena nao e' sinal relatado. Alarme real (afirmado, presente, de terceiro, com erro de digitacao) continua
192 na hora.

r5b: depois do "nao" ao alerta da dor de cabeca (opcoes pendentes), "posso tomar dipirona?" recebeu 192: o
classificador devolveu o codigo do HISTORICO e a tabela escalou.

Frases escritas para cada comportamento (nenhuma copiada do teste do dono nem do conjunto da avaliacao)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import helena_v4 as v4
from maezo.agents.helena.frase_afirmada import texto_afirmado
from maezo.agents.helena.graph import HelenaGraph, mensagem_urgente
from maezo.conhecimento import base_saude as bs
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _classify, _entrada, _WhatsApp
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _FonteGrafo, _Modelo
from tests.unit.agents.test_helena_saude_gerativa import _Transporte
from tests.unit.agents.test_helena_v4 import _DmnReal

_BASE_REAL = Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "saude"
_PERGUNTA_GERAL = v4.texto_da_pergunta_de_alerta(v4.GRUPO_GERAL, "x")
_PERGUNTA_CABECA = v4.texto_da_pergunta_de_alerta(v4.GRUPO_CABECA, "x")
_FINAL_ADULTO = v4.texto_final_do_sintoma("x", v4.POPULACAO_ADULTO)


def _grafo(modelo: _Modelo, *, ativo: bool = True) -> tuple[Any, _WhatsApp]:
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        coleta_enabled=True,
        helena_v4_enabled=True,
        consultas_plano=_FonteGrafo(),  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 25, 20, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        helena_base_saude_enabled=True,
        base_saude=bs.carregar_base(_BASE_REAL),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}) if ativo else frozenset(),
    )
    return grafo.compile_graph().compile(), whatsapp


def _tem_192(texto: str) -> bool:
    """O 192 de URGENCIA (o texto do alarme), nao o aviso de sempre "se for grave, ligue 192" das opcoes."""
    baixo = texto.lower()
    return "ligue 192 (samu) agora" in baixo or "ligue agora para o 192" in baixo


# --- A regra, frase a frase (sem estado) ------------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "estou com dor no peito agora",
        "meu pai ta cm dor no peito e falta de ar",
        "to com falta de ar desde cedo, o que eu faço?",
        "ela desmaiou agora pouco",
        "estou com dor no peito, é grave?",
        "minha mãe está com a boca torta e a fala enrolada",
        "dor no peito? to sentindo agora",
        "sim, dor no peito",
    ],
)
def test_alarme_afirmado_continua_urgente(fala: str) -> None:
    assert texto_afirmado(fala, _PERGUNTA_GERAL) == fala
    assert mensagem_urgente(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "não, mas o que dor no peito… tem a ver",
        "o que falta de ar tem a ver com isso?",
        "falta de ar pode ser isso?",
        "dor no peito é normal nesses casos?",
        "fala enrolada significa o que",
    ],
)
def test_sinal_dentro_de_pergunta_nao_e_relato(fala: str) -> None:
    assert texto_afirmado(fala, _PERGUNTA_GERAL) == ""
    assert not mensagem_urgente(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "dor no peito, falta de ar, desmaio, boca torta ou fraqueza de um lado do corpo",
        "boca torta ou fraqueza de um lado do corpo?",
        "não, dor no peito, falta de ar, nada disso",
        "nao. dor no peito falta de ar desmaio nenhum",
    ],
)
def test_eco_da_pergunta_da_helena_nao_e_relato(fala: str) -> None:
    assert not _travas_alarme(fala, _PERGUNTA_GERAL)


@pytest.mark.parametrize(
    "fala",
    [
        "não tenho dor no peito nem falta de ar",
        "sem falta de ar, sem desmaio",
        "nada de dor no peito",
    ],
)
def test_sinal_negado_nao_e_relato(fala: str) -> None:
    assert not _travas_alarme(fala, _PERGUNTA_GERAL)


@pytest.mark.parametrize(
    "fala",
    [
        "não, mas o que isso tem a ver?",
        "nao, mas e se for algo mais serio?",
        "não, mas fiquei preocupada com essa pergunta",
    ],
)
def test_nao_mas_vago_nao_trava(fala: str) -> None:
    assert not _travas_alarme(fala, _PERGUNTA_GERAL)


@pytest.mark.parametrize(
    "fala",
    [
        "não, mas agora estou com falta de ar",
        "nao, mas ele desmaiou faz pouco",
        "não, mas falta de ar",
    ],
)
def test_nao_seguido_de_sinal_afirmado_continua_alarme(fala: str) -> None:
    assert _travas_alarme(fala, _PERGUNTA_GERAL)


def _travas_alarme(fala: str, helena: str) -> bool:
    grafo = HelenaGraph(
        inference=_Modelo([], []),  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=bs.carregar_base(_BASE_REAL),
    )
    return grafo._roteador_alarme(fala, None, (), helena)


# --- O caso b do dono, parafraseado: o grafo inteiro ------------------------------------------------------
_PENDENTE_GERAL = {
    "coleta_pendente": v4.codigo_pendente(v4.ETAPA_ALERTA, v4.GRUPO_GERAL, v4.POPULACAO_ADULTO),
    "coleta_contexto": "estou com dificuldade para fazer xixi",
}


@pytest.mark.parametrize("ativo", [True, False])
@pytest.mark.parametrize(
    "fala",
    [
        "não, mas o que dor no peito… tem a ver com isso",
        "nao. mas o que falta de ar e fraqueza de um lado do corpo tem a ver com o xixi?",
        "Não, mas dor no peito, falta de ar e boca torta tem relação com urinar?",
    ],
)
async def test_caso_b_pergunta_sobre_os_sinais_nao_abre_p1(fala: str, ativo: bool) -> None:
    # O classificador antigo le' os sinais citados e devolve o codigo deles (como no teste do dono).
    modelo = _Modelo(
        [_decisao("sintoma", responde=True, sim_nao="nao")],
        [_classify(intent="symptom", sintoma_codigo="dor_toracica", intensidade="desconhecida")] * 3,
    )
    grafo, whatsapp = _grafo(modelo, ativo=ativo)
    with capture_logs() as logs:
        final = await grafo.ainvoke(_entrada(fala, [_PERGUNTA_GERAL]) | _PENDENTE_GERAL)
    assert final.get("escalation_motivo") != "red_flag_clinico"
    assert not _tem_192(whatsapp.enviados[-1])
    assert not any(e.get("event") == "helena_alarme_deterministico" for e in logs)


@pytest.mark.parametrize("ativo", [True, False])
@pytest.mark.parametrize(
    "fala",
    [
        "sim, to com dor no peito agora",
        "meu marido ta cm falta de ar e dor no peito",
        "não, mas agora começou uma fraqueza de um lado do corpo",
    ],
)
async def test_caso_b_controle_alarme_real_continua_192(fala: str, ativo: bool) -> None:
    modelo = _Modelo(
        [_decisao("sintoma", responde=True, sim_nao="sim", sinais=["dor no peito"], alarme="clear")],
        [_classify(intent="symptom", sintoma_codigo="dor_toracica", intensidade="desconhecida")] * 3,
    )
    grafo, whatsapp = _grafo(modelo, ativo=ativo)
    final = await grafo.ainvoke(_entrada(fala, [_PERGUNTA_GERAL]) | _PENDENTE_GERAL)
    assert final.get("escalation_motivo") == "red_flag_clinico"
    assert _tem_192(whatsapp.enviados[-1])


# --- r5b: depois do "nao" ao alerta, a pergunta de remedio nunca vira 192 ---------------------------------
_PENDENTE_ESCOLHA = {
    "coleta_pendente": v4.codigo_pendente(v4.ETAPA_ESCOLHA, "sin-dor-de-cabeca", v4.POPULACAO_ADULTO),
    "coleta_contexto": "estou com dor de cabeca",
}


@pytest.mark.parametrize("ativo", [True, False])
@pytest.mark.parametrize(
    "fala",
    ["posso tomar dipirona?", "quantos mg de paracetamol eu tomo?", "posso tomar um dorflex?"],
)
async def test_r5b_pergunta_de_remedio_depois_do_nao_nunca_e_192(fala: str, ativo: bool) -> None:
    # O classificador devolve o codigo da dor de cabeca do HISTORICO (o log da r5b).
    modelo = _Modelo(
        [_decisao("sintoma", responde=True)],
        [_classify(intent="clinical_question", sintoma_codigo="cefaleia_subita_intensa", intensidade="grave")]
        * 3,
    )
    grafo, whatsapp = _grafo(modelo, ativo=ativo)
    with capture_logs() as logs:
        final = await grafo.ainvoke(_entrada(fala, [_PERGUNTA_CABECA, _FINAL_ADULTO]) | _PENDENTE_ESCOLHA)
    texto = whatsapp.enviados[-1]
    assert final.get("escalation_motivo") != "red_flag_clinico"
    assert not _tem_192(texto)
    assert any(
        e.get("event") == "helena_habilidade_sem_sintoma_do_historico"
        and e.get("motivo") == "alerta_ja_respondido"
        for e in logs
    )


@pytest.mark.parametrize("ativo", [True, False])
@pytest.mark.parametrize(
    "fala",
    [
        "agora a dor ficou muito forte, a pior da vida",
        "a dor de cabeça piorou muito de repente, é a pior da minha vida",
        "começou uma dor de cabeça explosiva agora",
    ],
)
async def test_r5b_controle_piora_real_depois_do_nao_e_192(fala: str, ativo: bool) -> None:
    modelo = _Modelo(
        [_decisao("sintoma", responde=True, sinais=["pior dor da vida"], alarme="clear")],
        [_classify(intent="symptom", sintoma_codigo="cefaleia_subita_intensa", intensidade="grave")] * 3,
    )
    grafo, whatsapp = _grafo(modelo, ativo=ativo)
    final = await grafo.ainvoke(_entrada(fala, [_PERGUNTA_CABECA, _FINAL_ADULTO]) | _PENDENTE_ESCOLHA)
    assert final.get("escalation_motivo") == "red_flag_clinico"
    assert _tem_192(whatsapp.enviados[-1])
