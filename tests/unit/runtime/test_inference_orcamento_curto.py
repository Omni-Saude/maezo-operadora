"""Orçamento CURTO da inferência regional (ADR-0066, roteador da Helena): pool próprio, teto de saída próprio.

Prompts, modelos e referências são fixtures sintéticas.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.unit.runtime.test_bedrock_br_pre_egress import Client, request

from maezo.runtime.inference.br_regional import BedrockBrRegionalTransport
from maezo.runtime.inference.br_resident_provider import BrResidentInferenceProvider
from maezo.runtime.inference.errors import InferenceConfigError
from maezo.runtime.inference.settings import InferenceSettings


async def test_transporte_com_pool_proprio_roda_no_pool_proprio() -> None:
    nomes: list[str] = []

    class _Cliente(Client):
        def converse(self, **kwargs: object) -> dict[str, object]:
            nomes.append(threading.current_thread().name)
            return super().converse(**kwargs)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phi-curta")
    try:
        await BedrockBrRegionalTransport(client=_Cliente(), executor=pool).send(request())
    finally:
        pool.shutdown(wait=True)
    assert nomes and nomes[0].startswith("phi-curta")


async def test_transporte_sem_pool_e_o_de_sempre() -> None:
    nomes: list[str] = []

    class _Cliente(Client):
        def converse(self, **kwargs: object) -> dict[str, object]:
            nomes.append(threading.current_thread().name)
            return super().converse(**kwargs)

    await BedrockBrRegionalTransport(client=_Cliente()).send(request())
    assert nomes and not nomes[0].startswith("phi-curta")


def test_settings_do_orcamento_curto_nascem_inertes() -> None:
    s = InferenceSettings()
    assert s.max_tokens is None and s.dedicated_workers == 0


@pytest.mark.parametrize("teto", [0, 5000])
def test_teto_de_saida_fora_da_faixa_reprova(monkeypatch: pytest.MonkeyPatch, teto: int) -> None:
    monkeypatch.setenv("MAEZO_PHI_ENDPOINT_URL", "https://bedrock-runtime.sa-east-1.amazonaws.com")
    monkeypatch.setenv("MAEZO_PHI_VENDOR_DPA_REF", "test-only-operator-reference")
    monkeypatch.setenv("MAEZO_PHI_API_KEY", "test-only")
    with pytest.raises(InferenceConfigError, match="max_tokens"):
        BrResidentInferenceProvider(model="example.fixture-v1:0", max_tokens=teto)


def test_teto_de_saida_vai_no_pedido(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.prompt_format import FormattedPrompt

    monkeypatch.setenv("MAEZO_PHI_ENDPOINT_URL", "https://bedrock-runtime.sa-east-1.amazonaws.com")
    monkeypatch.setenv("MAEZO_PHI_VENDOR_DPA_REF", "test-only-operator-reference")
    monkeypatch.setenv("MAEZO_PHI_API_KEY", "test-only")
    curto = BrResidentInferenceProvider(model="example.fixture-v1:0", max_tokens=200)
    padrao = BrResidentInferenceProvider(model="example.fixture-v1:0")
    pedido = FormattedPrompt(stable_prefix="", variable_suffix="SYNTHETIC")
    assert curto._build_request(pedido).max_tokens == 200
    assert padrao._build_request(pedido).max_tokens == BrResidentInferenceProvider.MAX_TOKENS
