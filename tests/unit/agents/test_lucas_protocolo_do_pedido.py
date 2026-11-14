"""O PROTOCOLO do pedido de cancelamento JA' feito (avaliacao independente r5b de 25/10/2026, item E2).

Logo depois de "Protocolo: CAN-...", a pessoa perguntou "qual meu protocolo mesmo?" e leu um texto generico.
A Helena agora passa a pergunta ao Lucas como `cancelamento` com o foco `protocolo`; o Lucas le' a instancia
VIVA do pedido no motor (a mesma chave do start) e responde o protocolo dela — o MESMO do recebimento. Sem
instancia: "nao encontrei". Falha de leitura ou chave desligada: "nao consegui consultar agora". Nunca a
pergunta de confirmacao de novo, nunca um processo novo, nunca um protocolo inventado.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas import cancelamento_pf as cpf
from maezo.agents.lucas.graph import TEXTO_CONFIRMA_CANCELAMENTO, LucasState, new_lucas_state
from maezo.domain.conversa.cobranca_foco import FOCO_PROTOCOLO, foco_valido
from maezo.qa.simulador.checagens import frases_longas, palavras_proibidas, problemas_de_tom
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from tests.unit.agents.test_lucas_cancelamento_pf_v1 import (
    _confirmado,
    _fatos_pf,
    _titular_verificado,
    _turno,
)


def _pergunta_do_protocolo(**extra: Any) -> LucasState:
    entrada: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_cancelpf",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-cancelpf",
        "to_hash": "hash-cancelpf",
        "intencao": "cancelamento",
        "tipo_solicitacao": "",
        "pedido_cancelamento": True,
        "cobranca_foco": FOCO_PROTOCOLO,
        **_fatos_pf(),
        **_titular_verificado(),
        **extra,
    }
    return new_lucas_state(entrada)


class _MotorQueFalhaNaLeitura(FakeCibSevenTransport):
    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise CibSevenError("motor fora")


def test_o_foco_protocolo_esta_no_dominio_fechado() -> None:
    assert foco_valido(FOCO_PROTOCOLO)


async def test_depois_do_pedido_a_pergunta_do_protocolo_devolve_o_mesmo_protocolo() -> None:
    motor = FakeCibSevenTransport()
    pedido, envio_pedido, _ = await _turno(
        _confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, motor=motor
    )
    protocolo = cpf.protocolo_do_cancelamento(pedido["process_ref"]["instance_id"])
    assert protocolo in envio_pedido.textos[0]
    starts = len(motor._history)

    final, envio, _ = await _turno(_pergunta_do_protocolo(), v1=True, motor=motor)

    assert envio.textos == [cpf.TEXTO_PROTOCOLO_DO_PEDIDO.format(protocolo=protocolo)]
    assert final["route"] == "respond_member" and final["process_started"] is not True
    assert len(motor._history) == starts, "a pergunta do protocolo nunca inicia processo"
    assert TEXTO_CONFIRMA_CANCELAMENTO not in envio.textos


@pytest.mark.parametrize("vezes", [2, 3])
async def test_perguntar_de_novo_continua_com_o_mesmo_protocolo(vezes: int) -> None:
    motor = FakeCibSevenTransport()
    pedido, _, _ = await _turno(_confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, motor=motor)
    protocolo = cpf.protocolo_do_cancelamento(pedido["process_ref"]["instance_id"])
    for _ in range(vezes):
        _, envio, _ = await _turno(_pergunta_do_protocolo(), v1=True, motor=motor)
        assert len(envio.textos) == 1 and protocolo in envio.textos[0]


async def test_sem_pedido_vivo_diz_que_nao_encontrou_e_nao_inventa() -> None:
    final, envio, motor = await _turno(_pergunta_do_protocolo(), v1=True)
    assert envio.textos == [cpf.TEXTO_PROTOCOLO_SEM_PEDIDO]
    assert "CAN-" not in envio.textos[0]
    assert not motor._history and final["process_started"] is not True


async def test_falha_de_leitura_diz_a_verdade_e_os_canais() -> None:
    final, envio, motor = await _turno(_pergunta_do_protocolo(), v1=True, motor=_MotorQueFalhaNaLeitura())
    assert envio.textos == [cpf.TEXTO_PROTOCOLO_INDISPONIVEL]
    assert "CAN-" not in envio.textos[0]
    assert not motor._history and final["process_started"] is not True


async def test_chave_desligada_nao_le_o_motor_e_nao_inventa() -> None:
    _, envio, motor = await _turno(_pergunta_do_protocolo(), v1=False)
    assert envio.textos == [cpf.TEXTO_PROTOCOLO_INDISPONIVEL]
    assert not motor._history


async def test_controle_sem_o_foco_o_cancelamento_segue_pedindo_a_confirmacao() -> None:
    """Controle negativo: o pedido de cancelamento de sempre (foco geral) continua com a pergunta unica."""
    _, envio, motor = await _turno(_pergunta_do_protocolo(cobranca_foco="geral"), v1=True)
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO]
    assert not motor._history


def test_protocolo_plantado_pelo_chamador_e_recusado_na_fronteira() -> None:
    """`cancelamento_protocolo` e' campo de SAIDA: o chamador nunca o planta (a fronteira T1.11 recusa)."""
    estado_plantado = dict(_pergunta_do_protocolo())
    estado_plantado["cancelamento_protocolo"] = "CAN-AAAA-BBBB"
    with pytest.raises(ValueError, match="cancelamento_protocolo"):
        new_lucas_state(estado_plantado)


@pytest.mark.parametrize(
    "texto",
    [
        cpf.TEXTO_PROTOCOLO_DO_PEDIDO.format(protocolo="CAN-1A2B-3C4D"),
        cpf.TEXTO_PROTOCOLO_SEM_PEDIDO,
        cpf.TEXTO_PROTOCOLO_INDISPONIVEL,
    ],
)
def test_textos_do_protocolo_na_regua_de_tom(texto: str) -> None:
    assert frases_longas(texto) == []
    assert problemas_de_tom(texto) == []
    assert palavras_proibidas(texto) == []
    assert "cancelado" not in texto.lower()
    assert "http" not in texto and "www" not in texto
