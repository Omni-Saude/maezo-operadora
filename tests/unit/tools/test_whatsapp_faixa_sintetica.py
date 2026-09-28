"""Cerca de SAIDA da faixa sintetica `5511900000xxx` (27/09/2026).

A `FAIXA_TESTE` do Canal de Teste so barrava a ENTRADA; a resposta da Helena (receptor) e a
retomada (`agent-resume`) iam para a Cloud API da Meta mesmo para esses numeros — medido no dev
(`whatsapp_message_sent`). O ponto unico de envio (`WhatsAppServer.send_message`) agora recusa o
envio real para a faixa, em todo ambiente, sem numero no log.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from maezo.tools.mcp_whatsapp.server import (
    FAIXA_SINTETICA,
    WhatsAppServer,
    WhatsAppSettings,
    destino_sintetico,
)

_CONFIGURADO = {"whatsapp_token": "tok", "phone_number_id": "1234567890"}


def _cliente_http() -> AsyncMock:
    resposta = MagicMock()
    resposta.raise_for_status = MagicMock()
    resposta.json = MagicMock(return_value={"messages": [{"id": "wamid.x"}]})
    cliente = AsyncMock()
    cliente.__aenter__ = AsyncMock(return_value=cliente)
    cliente.__aexit__ = AsyncMock(return_value=None)
    cliente.post = AsyncMock(return_value=resposta)
    return cliente


@pytest.mark.parametrize("destino", ["5511900000000", "5511900000123", "5511900000999", "+55 11 90000-0123"])
async def test_destino_sintetico_nao_chama_a_meta(destino: str) -> None:
    server = WhatsAppServer(settings=WhatsAppSettings(**_CONFIGURADO))
    cliente = _cliente_http()
    with patch("httpx.AsyncClient", return_value=cliente) as construtor, capture_logs() as logs:
        resultado = await server.send_message(destino, "Ola")

    assert resultado == {"suppressed_synthetic": True}
    construtor.assert_not_called()
    cliente.post.assert_not_called()
    eventos = [log["event"] for log in logs]
    assert "whatsapp_envio_sintetico_suprimido" in eventos
    assert "whatsapp_message_sent" not in eventos
    # Nenhum pedaco do numero no log — nem o final: dentro da faixa, os 3 ultimos digitos SAO ele.
    sufixo = "".join(ch for ch in destino if ch.isdigit())[-3:]
    for log in logs:
        for valor in log.values():
            if isinstance(valor, str):
                assert destino not in valor
                assert not valor.endswith(sufixo) or valor == "5511900000xxx"


@pytest.mark.parametrize("destino", ["5511999999999", "551190000012", "55119000001234", "5521900000123"])
async def test_fora_da_faixa_segue_para_a_meta(destino: str) -> None:
    # Ancorada nas duas pontas e com comprimento exato: 12 ou 14 digitos, ou outro DDD, NAO sao
    # a faixa — um numero real mais longo que comece com o prefixo continua sendo entregue.
    server = WhatsAppServer(settings=WhatsAppSettings(**_CONFIGURADO))
    cliente = _cliente_http()
    with patch("httpx.AsyncClient", return_value=cliente):
        resultado = await server.send_message(destino, "Ola")
    assert resultado == {"messages": [{"id": "wamid.x"}]}
    cliente.post.assert_awaited_once()


async def test_suprime_antes_da_credencial_e_do_claim_de_idempotencia() -> None:
    # Em ambiente SEM credencial o envio real levantaria `ValueError`; a faixa sintetica e'
    # decidida antes, e nao reclama chave duravel nenhuma (nao houve entrega a proteger).
    registro: Any = MagicMock()
    registro.claim = AsyncMock(return_value=True)
    server = WhatsAppServer(settings=WhatsAppSettings(), dedup=registro)
    resultado = await server.send_message("5511900000042", "Ola", idempotency_key="k1")
    assert resultado == {"suppressed_synthetic": True}
    registro.claim.assert_not_called()


def test_faixa_de_saida_e_a_mesma_da_entrada() -> None:
    # O Canal de Teste e' stdlib pura, entao a expressao e' repetida la; esta e' a cerca de deriva.
    from maezo.platform.testchannel.server import FAIXA_TESTE

    assert FAIXA_SINTETICA.pattern == FAIXA_TESTE.pattern


def test_destino_vazio_nao_e_sintetico() -> None:
    assert destino_sintetico("") is False
