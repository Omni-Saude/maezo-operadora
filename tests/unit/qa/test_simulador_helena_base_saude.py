"""Simulador da BASE DE SAUDE (ADR-0064, DL-0118): o roteiro de aceite `helena_base_saude` e o motor simulado
que ENCERRA o caso pela mensagem `msg.escalation.withdrawn`.

O roteiro usa a base REAL (escrita por outra frente) e roda no dev, nunca no CI. Aqui se prova: (a) o roteiro
existe, todo padrao compila e cobre os cenarios pedidos pelo dono; (b) as respostas CERTAS da Helena (montadas
da base de fixture e dos textos fixos do grafo) passam nos passos e as erradas reprovam; (c) o interceptador
do simulador encerra a instancia viva e registra o encerramento no turno so' quando ela nao esta' mais ativa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.agents.helena.graph import (
    NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO,
    RESPOSTA_CASO_ENCERRADO,
    RESPOSTA_CONFIRMA_ENCERRAMENTO,
)
from maezo.conhecimento import base_saude as b
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.qa.simulador import aceite as ac
from maezo.qa.simulador import composicao as comp
from maezo.qa.simulador.registro import MensagemEnviada, Turno
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "base_saude"
_CID = "wa:amh:hk1_base_saude"


def test_o_roteiro_existe_e_todo_padrao_compila() -> None:
    assert ac.ROTEIROS["helena_base_saude"] is ac.HELENA_BASE_SAUDE
    ac.validar(ac.HELENA_BASE_SAUDE)
    assert all(s.resolveu for s in ac.HELENA_BASE_SAUDE), "todo segmento declara o criterio RESOLVEU?"
    textos = [p.texto for s in ac.HELENA_BASE_SAUDE for p in s.passos]
    for cenario in (
        "Pra que serve dipirona?",
        "O que é PSA?",
        "Meu PSA deu fora dos parâmetros, o que me sugere?",
        "O que é cardiologista?",
        "O que é angioplastia?",
        "O que é stent?",
        "O que é depressão?",
        "Estou com muita dor de cabeça desde ontem",
        "Estou com dor no peito e falta de ar",
        "Quero falar com uma pessoa",
        "Não quero falar com ninguém",
        "SIM",
        "O que é xyzabc?",
    ):
        assert cenario in textos, cenario
    [encerra] = [p for s in ac.HELENA_BASE_SAUDE for p in s.passos if p.caso_encerrado]
    assert encerra.texto == "SIM"


def _passo(nome: str, indice: int = 0) -> ac.Passo:
    return {s.nome: s for s in ac.HELENA_BASE_SAUDE}[nome].passos[indice]


def _base() -> b.BaseSaude:
    return b.carregar_base(_FIXTURE)


def test_as_respostas_certas_da_base_passam_e_as_erradas_reprovam() -> None:
    base = _base()
    dipirona, psa = base.get("med-dipirona"), base.get("exa-psa")
    assert dipirona is not None and psa is not None
    assert (
        ac.avaliar_passo(
            _passo("1-pra-que-serve-dipirona"), [("helena", b.resposta_informativa(dipirona))], [], None
        )
        == []
    )
    assert (
        ac.avaliar_passo(_passo("2-o-que-e-psa"), [("helena", b.resposta_informativa(psa))], [], None) == []
    )
    assert (
        ac.avaliar_passo(
            _passo("3-resultado-pessoal-de-psa"), [("helena", b.resposta_de_resultado_pessoal(psa))], [], None
        )
        == []
    )
    assert (
        ac.avaliar_passo(_passo("11-assunto-inexistente"), [("helena", b.RESPOSTA_NAO_ENCONTRADO)], [], None)
        == []
    )
    com_dose = "A dipirona alivia dor. Tome 500 mg de 6 em 6 horas. Fonte: x. " + b.AVISO_PADRAO
    assert ac.avaliar_passo(_passo("1-pra-que-serve-dipirona"), [("helena", com_dose)], [], None)
    caso = [{"agente": "helena", "motivo": "intencao_clinica", "severidade": "moderada"}]
    assert ac.avaliar_passo(_passo("2-o-que-e-psa"), [("helena", b.resposta_informativa(psa))], caso, None)


def test_a_confirmacao_e_o_encerramento_passam_nos_passos() -> None:
    assert (
        ac.avaliar_passo(
            _passo("10-pede-pessoa-recusa-e-encerra", 1),
            [("helena", RESPOSTA_CONFIRMA_ENCERRAMENTO)],
            [],
            None,
        )
        == []
    )
    assert (
        ac.avaliar_passo(
            _passo("10-pede-pessoa-recusa-e-encerra", 2), [("helena", RESPOSTA_CASO_ENCERRADO)], [], None
        )
        == []
    )


def _seam() -> SeamContext:
    return SeamContext(tenant="amh", principal="helena", decision=DecisionContext())


def _contexto() -> comp.ContextoDoTurno:
    return comp.ContextoDoTurno(
        turno=Turno(1, "persona", "SIM", "outro"), message_id="m1", conversation_id=_CID
    )


async def test_o_motor_simulado_encerra_a_instancia_e_registra_no_turno() -> None:
    assert comp.MENSAGEM_ENCERRAMENTO_A_PEDIDO == NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO
    registro = comp.RegistroDeEscalonamentos()
    real = FakeCibSevenTransport()
    motor = gate_cibseven(comp.InterceptorDeEscalonamento(real, registro), _seam())
    chave = f"ESC-amh-{_CID}"
    registro.registrar(chave, {"motivo_categoria": "solicitacao_humano", "severidade": "leve"})
    contexto = _contexto()
    with comp.no_turno(contexto):
        await motor.correlate_message(
            NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO, chave, {"resultado": "encerrado_a_pedido_beneficiario"}
        )
    assert registro.ativa(chave) is None, "a instancia nao esta mais ativa"
    assert contexto.turno.encerramentos == [chave]
    assert real.correlate_calls == [], "o motor real nunca ve a conversa simulada"
    with comp.no_turno(_contexto()), pytest.raises(CibSevenError):
        await motor.correlate_message(NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO, chave, {})


async def test_o_passo_caso_encerrado_reprova_sem_encerramento_e_o_inverso() -> None:
    passo = _passo("10-pede-pessoa-recusa-e-encerra", 2)

    async def sem_encerrar(texto: str) -> Turno:
        t = Turno(1, "persona", texto, "outro")
        t.mensagens.append(MensagemEnviada(agente="helena", texto=RESPOSTA_CASO_ENCERRADO))
        return t

    segmento = ac.Segmento("x", (passo,))
    [resultado] = await ac.executar_passos(segmento, sem_encerrar)
    assert any("caso_encerrado" in f for f in resultado.falhas)

    async def encerrando(texto: str) -> Turno:
        t = await sem_encerrar(texto)
        t.encerramentos.append("ESC-amh-x")
        return t

    [ok] = await ac.executar_passos(segmento, encerrando)
    assert ok.falhas == []
    [indevido] = await ac.executar_passos(ac.Segmento("y", (_passo("2-o-que-e-psa"),)), encerrando)
    assert any("caso_encerrado" in f for f in indevido.falhas)
