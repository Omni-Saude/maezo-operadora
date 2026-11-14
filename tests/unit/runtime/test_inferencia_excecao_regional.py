"""EXCEÇÃO REGIONAL da zona PHI do dev autorizada pelo DPO (21/10/2026): desligada por padrão; ligada só com o
rótulo literal; a região atestada é a verdadeira. Fixtures sintéticas."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.unit.runtime.test_bedrock_br_pre_egress import Client, request

from maezo.runtime.inference import excecao_regional as ex
from maezo.runtime.inference.br_regional import (
    BR_REGIONAL_ATTESTED_REGION,
    BedrockBrRegionalTransport,
    br_endpoint_denial_reasons,
)
from maezo.runtime.inference.errors import BrRegionalTransportUnavailableError


def _cliente_us() -> Client:
    c = Client(endpoint=ex.ENDPOINT_EXCECAO, region=ex.REGIAO_EXCECAO)
    c.meta = SimpleNamespace(endpoint_url=ex.ENDPOINT_EXCECAO, region_name=ex.REGIAO_EXCECAO)
    return c


def test_desligada_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ex.ENV_PHI_EXCECAO_REGIONAL, raising=False)
    assert not ex.excecao_ativa()
    assert br_endpoint_denial_reasons(ex.ENDPOINT_EXCECAO), "us-east-1 recusado sem a excecao"
    with pytest.raises(BrRegionalTransportUnavailableError, match="fora_da_excecao"):
        BedrockBrRegionalTransport(client=_cliente_us(), regiao=ex.REGIAO_EXCECAO)


@pytest.mark.parametrize("valor", ["1", "true", "us-east-1", ex.ROTA_EXCECAO_US + "x"])
def test_so_o_rotulo_literal_liga(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(ex.ENV_PHI_EXCECAO_REGIONAL, valor)
    assert not ex.excecao_ativa()


async def test_ligada_fala_com_us_east_1_e_atesta_a_regiao_verdadeira(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ex.ENV_PHI_EXCECAO_REGIONAL, ex.ROTA_EXCECAO_US)
    assert br_endpoint_denial_reasons(ex.ENDPOINT_EXCECAO) == ()
    cliente = _cliente_us()
    t = BedrockBrRegionalTransport(client=cliente, regiao=ex.REGIAO_EXCECAO)
    resposta = await t.send(request(endpoint_url=ex.ENDPOINT_EXCECAO))
    assert resposta.served_region == ex.REGIAO_EXCECAO != BR_REGIONAL_ATTESTED_REGION
    assert cliente.calls[0]["modelId"].startswith("arn:aws:bedrock:us-east-1::foundation-model/")


def test_ligada_nao_abre_outra_regiao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ex.ENV_PHI_EXCECAO_REGIONAL, ex.ROTA_EXCECAO_US)
    assert br_endpoint_denial_reasons("https://bedrock-runtime.us-west-2.amazonaws.com")
    with pytest.raises(BrRegionalTransportUnavailableError):
        BedrockBrRegionalTransport(client=Client(), regiao="us-west-2")


async def test_provedor_aceita_a_regiao_da_excecao_so_ligada(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.inference.br_resident_provider import BrResidentInferenceProvider
    from maezo.runtime.inference.errors import BrEndpointNotApprovedError, InferenceProviderError

    monkeypatch.setenv("MAEZO_PHI_ENDPOINT_URL", ex.ENDPOINT_EXCECAO)
    monkeypatch.setenv("MAEZO_PHI_VENDOR_DPA_REF", "test-only-operator-reference")
    monkeypatch.setenv("MAEZO_PHI_API_KEY", "test-only")
    monkeypatch.setenv(ex.ENV_PHI_EXCECAO_REGIONAL, ex.ROTA_EXCECAO_US)
    t = BedrockBrRegionalTransport(client=_cliente_us(), regiao=ex.REGIAO_EXCECAO)
    provedor = BrResidentInferenceProvider(model="example.fixture-v1:0", transport=t)
    assert await provedor.generate("SYNTHETIC") == "SYNTHETIC completion"
    monkeypatch.delenv(ex.ENV_PHI_EXCECAO_REGIONAL)
    with pytest.raises((BrEndpointNotApprovedError, InferenceProviderError)):
        await provedor.generate("SYNTHETIC")
