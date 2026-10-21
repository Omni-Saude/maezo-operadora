"""O despachante reaproveita assunto e mes da resposta anterior do Lucas numa pergunta de esclarecimento
(teste real do dono, 12/10/2026: "De qual ano?" virou o mes mais recente, e a insistencia virou
contestacao e um caso P2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher


class _Roteador:
    def __init__(self, contexto: Any = None, falha: bool = False) -> None:
        self._contexto = contexto
        self._falha = falha
        self.lidas = 0

    async def contexto_do_lucas(self, conversation_id: str) -> Any:
        self.lidas += 1
        if self._falha:
            raise RuntimeError("banco fora")
        return self._contexto


def _handoff(subtipo: str = "consulta_valores", competencia: str | None = None) -> dict[str, Any]:
    return {
        "para": "lucas",
        "cobranca_subtipo": subtipo,
        "competencia": competencia,
        "cobranca_foco": "geral",
        "message_ref": "hk1_ref",
    }


async def _ajustar(roteador: _Roteador | None, texto: str, handoff: dict[str, Any]) -> Any:
    eu = SimpleNamespace(roteador=roteador, tenant_id="amh")
    return await HelenaDispatcher._handoff_com_esclarecimento(eu, texto, "wa:amh:hk1_ab", handoff)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_de_qual_ano_reaproveita_o_mes_da_resposta_anterior() -> None:
    ajustado = await _ajustar(_Roteador(("consulta_valores", "2026-07")), "De qual ano?", _handoff())
    assert ajustado["competencia"] == "2026-07"
    assert ajustado["cobranca_subtipo"] == "consulta_valores"


@pytest.mark.asyncio
async def test_insistencia_lida_como_contestacao_volta_a_ser_consulta() -> None:
    ajustado = await _ajustar(
        _Roteador(("consulta_valores", "2026-07")),
        "Você disse que paguei em 29/07, mas de que ano?",
        _handoff("contestacao", "2026-07"),
    )
    assert ajustado["cobranca_subtipo"] == "consulta_valores"
    assert ajustado["competencia"] == "2026-07"


@pytest.mark.asyncio
async def test_o_mes_da_mensagem_vence_o_lembrado() -> None:
    ajustado = await _ajustar(
        _Roteador(("consulta_valores", "2026-07")),
        "Como assim? E a de maio?",
        _handoff(competencia="2026-05"),
    )
    assert ajustado["competencia"] == "2026-05"


@pytest.mark.asyncio
async def test_contestacao_explicita_nao_e_reaproveitada() -> None:
    roteador = _Roteador(("consulta_valores", "2026-07"))
    original = _handoff("contestacao", "2026-07")
    assert await _ajustar(roteador, "Como assim? Esse valor está errado", original) is original
    assert roteador.lidas == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("subtipo", ["cancelamento", "cobranca_recebida"])
async def test_cancelamento_e_aviso_de_cobranca_nao_se_sobrescrevem(subtipo: str) -> None:
    ajustado = await _ajustar(_Roteador(("consulta_valores", "2026-07")), "Como assim?", _handoff(subtipo))
    assert ajustado["cobranca_subtipo"] == subtipo


@pytest.mark.asyncio
async def test_assunto_anterior_nao_informativo_nao_e_reaproveitado() -> None:
    ajustado = await _ajustar(_Roteador(("outro", "2026-07")), "De qual ano?", _handoff("contestacao"))
    assert ajustado["cobranca_subtipo"] == "contestacao"


@pytest.mark.asyncio
async def test_sem_lucas_com_a_conversa_nada_muda() -> None:
    original = _handoff()
    assert await _ajustar(_Roteador(None), "De qual ano?", original) is original
    assert await _ajustar(None, "De qual ano?", original) is original


@pytest.mark.asyncio
async def test_falha_de_leitura_mantem_o_handoff_da_helena() -> None:
    original = _handoff()
    assert await _ajustar(_Roteador(falha=True), "De qual ano?", original) is original


@pytest.mark.asyncio
async def test_pergunta_nova_nao_le_o_roteador() -> None:
    roteador = _Roteador(("consulta_valores", "2026-07"))
    original = _handoff()
    assert await _ajustar(roteador, "Qual o valor da minha última mensalidade?", original) is original
    assert roteador.lidas == 0
