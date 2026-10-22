"""Contador e alarme das chamadas à zona PHI (22/10/2026): uma linha `inferencia_chamada` por chamada
(ok, falha, prazo) e o filtro de métrica do CloudWatch que conta falha + prazo por modelo. Sintético."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs
from tests.unit.runtime.test_bedrock_br_pre_egress import Client, request

from maezo.runtime.inference import br_regional as br
from maezo.runtime.inference.br_regional import BedrockBrRegionalTransport
from maezo.runtime.inference.errors import BrRegionalTransportUnavailableError

_TF = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "aws-ecs"
    / "envs"
    / "dev-sa-east-1"
    / "inferencia-alarmes.tf"
)


def _linha(evento: dict[str, Any]) -> str:
    dados = dict(evento)
    dados["level"] = dados.pop("log_level")
    dados["timestamp"] = "2026-10-22T00:00:00Z"
    return str(structlog.dev.ConsoleRenderer(colors=False)(None, dados["level"], dados))


def _padrao(modelo: str) -> tuple[list[str], list[str]]:
    texto = _TF.read_text(encoding="utf-8")
    m = re.search(r'pattern\s*=\s*"(.*)"\n', texto)
    assert m, "o filtro de metrica sumiu"
    bruto = m.group(1).replace("${each.value}", modelo).replace('\\"', '"')
    incluir = re.findall(r'(?<!-)"([^"]+)"', bruto)
    excluir = re.findall(r'-"([^"]+)"', bruto)
    return incluir, excluir


def _casa(modelo: str, linha: str) -> bool:
    incluir, excluir = _padrao(modelo)
    return all(t in linha for t in incluir) and not any(t in linha for t in excluir)


async def test_chamada_ok_registra_tokens_e_nao_conta_no_alarme() -> None:
    with capture_logs() as logs:
        await BedrockBrRegionalTransport(client=Client()).send(request())
    [evento] = [e for e in logs if e["event"] == br.EVENTO_INFERENCIA_CHAMADA]
    assert (
        evento["resultado"] == br.RESULTADO_OK
        and evento["tokens_entrada"] == 2
        and evento["tokens_saida"] == 3
    )
    assert not _casa(evento["modelo"], _linha(evento))


@pytest.mark.parametrize(
    ("erro", "resultado"),
    [
        (type("ReadTimeoutError", (Exception,), {})("x"), br.RESULTADO_PRAZO),
        (RuntimeError("x"), br.RESULTADO_FALHA),
    ],
)
async def test_falha_e_prazo_contam_no_alarme_do_modelo(erro: Exception, resultado: str) -> None:
    with capture_logs() as logs, pytest.raises(BrRegionalTransportUnavailableError):
        await BedrockBrRegionalTransport(client=Client(error=erro)).send(request())
    [evento] = [e for e in logs if e["event"] == br.EVENTO_INFERENCIA_CHAMADA]
    assert evento["resultado"] == resultado
    assert _casa(evento["modelo"], _linha(evento))
    assert not _casa("outro.modelo-v1", _linha(evento)), "o alarme e' por modelo"


async def test_linha_do_contador_sem_texto_da_conversa() -> None:
    with capture_logs() as logs, pytest.raises(BrRegionalTransportUnavailableError):
        await BedrockBrRegionalTransport(client=Client(error=RuntimeError("SYNTHETIC prompt eco"))).send(
            request()
        )
    [evento] = [e for e in logs if e["event"] == br.EVENTO_INFERENCIA_CHAMADA]
    assert "SYNTHETIC" not in _linha(evento)


async def test_prazo_total_e_respeitado_mesmo_com_o_cliente_pendurado() -> None:
    import time as _time

    class _Pendurado(Client):
        def converse(self, **kwargs: Any) -> dict[str, Any]:
            _time.sleep(3)
            return super().converse(**kwargs)

    inicio = _time.monotonic()
    with capture_logs() as logs, pytest.raises(BrRegionalTransportUnavailableError):
        await BedrockBrRegionalTransport(client=_Pendurado(), timeout_s=0.5).send(request())
    assert _time.monotonic() - inicio < 2.0, "o prazo total (0,5 s + folga) foi respeitado"
    [evento] = [e for e in logs if e["event"] == br.EVENTO_INFERENCIA_CHAMADA]
    assert evento["resultado"] == br.RESULTADO_PRAZO
