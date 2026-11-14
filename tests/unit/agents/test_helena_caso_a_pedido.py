"""O CASO A PEDIDO da Helena: a recusa com caso aberto pede confirmacao, o "sim" encerra o caso P2/P3 no motor
(`msg.escalation.withdrawn`, auditoria ANTES do efeito) e o caso grave nunca se encerra (192).

Limpeza da saude, etapa 3 (dono, 02/11/2026): estes testes moravam em `test_helena_base_saude.py` (o caso a
pedido nasceu atras da chave da base, e ficou sem chave na etapa 1). A base saiu do codigo com os testes dela;
o caso a pedido fica, contra o grafo real (tabelas `.dmn` reais).
"""

from __future__ import annotations

import json

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena.graph import (
    NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO,
    RESPOSTA_CASO_ENCERRADO,
    RESPOSTA_CASO_GRAVE_NAO_ENCERRA,
    RESPOSTA_CONFIRMA_ENCERRAMENTO,
    RESPOSTA_ENCERRAMENTO_MANTIDO,
    RESPOSTA_RECUSA_DE_EQUIPE,
    RESULTADO_ENCERRADO_A_PEDIDO,
    HelenaGraph,
    caso_grave_para_encerrar,
    new_helena_state,
    pede_para_encerrar_o_atendimento,
)
from maezo.tools.mcp_cibseven.transport import correlate_dedup_key
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.helena_conversa import (
    _BK,
    _BK_CLIN,
    _CONVERSA_DE_CASO,
    _INFO,
    _PESSOA,
    _DmnReal,
    _Modelo,
    _Motor,
    _texto,
    _WhatsApp,
)
from tests.support.helena_conversa import _ConversaDeCaso as _Conversa


@pytest.mark.parametrize(
    "mensagem",
    ["Não quero falar com ninguém", "Pode cancelar o atendimento", "Não preciso mais", "Cancela o caso"],
)
def test_as_formas_do_pedido_de_encerrar(mensagem: str) -> None:
    assert pede_para_encerrar_o_atendimento(mensagem)


@pytest.mark.parametrize(
    "mensagem", ["Quero cancelar meu plano", "Não quero pagar o boleto", "Cancela a consulta"]
)
def test_o_que_nao_e_pede_para_encerrar_o_atendimento(mensagem: str) -> None:
    assert not pede_para_encerrar_o_atendimento(mensagem)


def test_o_que_e_grave_nao_se_encerra() -> None:
    assert caso_grave_para_encerrar("risco_psicossocial", "moderada")
    assert caso_grave_para_encerrar("red_flag_clinico", "grave")
    assert caso_grave_para_encerrar("", None), "na duvida, grave"
    assert not caso_grave_para_encerrar("solicitacao_humano", "leve")
    assert not caso_grave_para_encerrar("intencao_clinica", "moderada")
    assert not caso_grave_para_encerrar("red_flag_clinico", "moderada")


async def test_pedido_de_pessoa_depois_recusa_e_sim_encerra_o_caso_no_motor() -> None:
    conversa = _Conversa()
    await conversa.diz("Quero falar com uma pessoa", _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]
    assert await conversa.cib.find_active_instance(_BK) is not None

    pergunta = await conversa.diz("Não quero falar com ninguém", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO
    assert conversa.cib.correlate_calls == []

    fim = await conversa.diz("SIM", _INFO)
    assert _texto(fim) == RESPOSTA_CASO_ENCERRADO
    assert conversa.whatsapp.enviados[-2:] == [RESPOSTA_CONFIRMA_ENCERRAMENTO, RESPOSTA_CASO_ENCERRADO]
    assert await conversa.cib.find_active_instance(_BK) is None, "a instancia nao esta mais ativa no motor"
    [chamada] = conversa.cib.correlate_calls
    assert chamada["message_name"] == NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO
    assert chamada["business_key"] == _BK
    assert chamada["variables"]["resultado"] == RESULTADO_ENCERRADO_A_PEDIDO
    # AUDITORIA ANTES DO EFEITO: o registro do encerramento existe, com a chave de dedup da instancia.
    registro, dedup = conversa.auditoria.calls[-1]
    acao = f"correlate_message:SP-OP-ESCALATION-001:{NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO}"
    assert registro.action == acao
    assert dedup == correlate_dedup_key(
        "amh", "SP-OP-ESCALATION-001", NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO, _BK, f"fake-{_BK}"
    )
    assert _BK not in json.dumps(registro.details), "a business key nunca vai ao registro em claro"
    assert len(conversa.cib.inicios) == 1, "encerrar nao abre caso novo"


async def test_auditoria_que_falha_impede_o_encerramento() -> None:
    from maezo.gateway.audit_postgres import AuditPersistenceError

    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor, auditoria=FakeStartAuditSink(fail=True))
    conversa.memoria = {"coleta_pendente": graph_module.PENDENTE_ENCERRAR_CASO}
    with pytest.raises(AuditPersistenceError):
        await conversa.diz("sim", _INFO)
    assert motor.correlate_calls == [], "sem registro duravel, nenhum efeito no motor"


async def test_caso_grave_nao_e_encerrado_e_recebe_o_192() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="red_flag_clinico", severidade="grave")
    conversa = _Conversa(motor=motor)
    pergunta = await conversa.diz("Pode cancelar o atendimento", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO

    resultado = await conversa.diz("sim", _INFO)
    assert _texto(resultado) == RESPOSTA_CASO_GRAVE_NAO_ENCERRA
    assert conversa.whatsapp.enviados[-1] == RESPOSTA_CASO_GRAVE_NAO_ENCERRA
    assert "192" in _texto(resultado)
    assert motor.correlate_calls == []
    assert await motor.find_active_instance(_BK) is not None


async def test_caso_clin_paralelo_segue_a_mesma_regra() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    motor.abrir(_BK_CLIN, motivo_categoria="risco_psicossocial", severidade="moderada")
    conversa = _Conversa(motor=motor)
    await conversa.diz("Não preciso mais", _INFO)
    resultado = await conversa.diz("sim", _INFO)

    assert await motor.find_active_instance(_BK) is None, "o caso administrativo foi encerrado"
    assert await motor.find_active_instance(_BK_CLIN) is not None, "o psicossocial (P1) fica"
    assert _texto(resultado) == RESPOSTA_CASO_GRAVE_NAO_ENCERRA


async def test_nao_mantem_o_caso_e_falha_do_motor_diz_a_verdade() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor)
    await conversa.diz("Cancela o caso", _INFO)
    resultado = await conversa.diz("não", _INFO)
    assert _texto(resultado) == RESPOSTA_ENCERRAMENTO_MANTIDO
    assert await motor.find_active_instance(_BK) is not None

    recusa = _Motor(recusa_correlacao=True)
    recusa.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    falha = _Conversa(motor=recusa)
    await falha.diz("Cancela o caso", _INFO)
    resultado = await falha.diz("sim", _INFO)
    assert _texto(resultado) == graph_module.RESPOSTA_ENCERRAMENTO_FALHOU


async def test_recusa_sem_caso_aberto_e_so_reconhecida() -> None:
    # Avaliacao r5 (25/10/2026): sem oferta e sem caso, a recusa e' so' reconhecida (nunca o paragrafo longo).
    conversa = _Conversa()
    resultado = await conversa.diz("Não quero falar com ninguém", _INFO)
    assert _texto(resultado) == "Combinado! Se precisar, é só chamar."
    assert _texto(resultado) != RESPOSTA_RECUSA_DE_EQUIPE
    assert conversa.memoria.get("coleta_pendente") is None


async def test_sim_sem_a_pergunta_de_confirmacao_nao_encerra_nada() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor)
    await conversa.diz("sim", _INFO)
    assert motor.correlate_calls == []


def test_a_pendencia_de_encerramento_vale_sem_chave() -> None:
    """Limpeza da saude, etapa 1 (02/11/2026): encerrar o caso e' atendimento, nao saude: a confirmacao
    pendente vale sem a chave da base (antes ela sumia)."""
    import asyncio

    grafo = HelenaGraph(
        inference=_Modelo(),  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=_Motor(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )
    estado = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA_DE_CASO,
            canal="whatsapp",
            beneficiario_pseudo_id="P",
            message_body="sim",
            message_ref="m",
        )
    )
    estado["coleta_pendente"] = graph_module.PENDENTE_ENCERRAR_CASO
    reset = asyncio.run(grafo.receive(estado))  # type: ignore[arg-type]
    assert reset["coleta_pendente"] == graph_module.PENDENTE_ENCERRAR_CASO
