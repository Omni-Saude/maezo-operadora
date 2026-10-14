"""Rodada 12 (14/10/2026): os dois sinais de texto que o despachante soma ao handoff do Lucas."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_intencao

_BASE: dict[str, Any] = {"para": "lucas", "cobranca_subtipo": "consulta_valores", "cobranca_foco": "geral"}


def test_texto_comum_nao_muda_o_handoff() -> None:
    assert _handoff_com_intencao("Qual o valor de setembro?", _BASE, None) is _BASE


def test_pedido_de_negociacao_marca_o_handoff() -> None:
    saida = _handoff_com_intencao("Quero parcelar a mensalidade", _BASE, None)
    assert saida["cobranca_negociacao"] is True and "cobranca_negociacao" not in _BASE


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("SIM, QUERO CANCELAR", "confirma"), ("não, deixa pra lá", "nega")],
)
def test_resposta_a_pergunta_de_cancelamento_volta_ao_lucas_como_cancelamento(
    texto: str, esperado: str
) -> None:
    saida = _handoff_com_intencao(texto, {**_BASE, "cobranca_subtipo": "outro"}, "cancelamento")
    assert saida["cobranca_subtipo"] == "cancelamento" and saida["cobranca_confirmacao"] == esperado


def test_sim_sem_assunto_de_cancelamento_nao_vira_confirmacao() -> None:
    saida = _handoff_com_intencao("sim", _BASE, "consulta_valores")
    assert "cobranca_confirmacao" not in saida and saida["cobranca_subtipo"] == "consulta_valores"


def test_contestacao_nunca_e_reclassificada_como_cancelamento() -> None:
    saida = _handoff_com_intencao(
        "sim, foi cobrado errado", {**_BASE, "cobranca_subtipo": "contestacao"}, "cancelamento"
    )
    assert saida["cobranca_subtipo"] == "contestacao" and "cobranca_confirmacao" not in saida
