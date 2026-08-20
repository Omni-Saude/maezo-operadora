"""Transporte BR-regional sobre Bedrock: o que ele OBSERVA e o que ele RECUSA.

O valor deste transporte está em não afirmar nada que não tenha visto. `served_region` e
`endpoint_url` saem do cliente boto3 REAL; se divergirem do esperado, o transporte devolve o
valor divergente para que `BrResidentInferenceProvider._validate_response` recuse — em vez de
normalizar a divergência e deixar PHI passar.

Estes testes travam exatamente isso, com um cliente falso: nenhum deles fala com a AWS.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.runtime.inference import (
    BR_REGIONAL_ATTESTED_REGION,
    HEADER_VENDOR_DPA_REF,
    BedrockBrRegionalTransport,
    BrRegionalRequest,
    BrRegionalTransportUnavailableError,
    br_endpoint_denial_reasons,
)

ENDPOINT = "https://bedrock-runtime.sa-east-1.amazonaws.com"
MODELO = "mistral.mistral-large-3-675b-instruct"


class _ClienteFalso:
    """Imita o `bedrock-runtime` do boto3: `converse` + `meta.{region_name,endpoint_url}`."""

    def __init__(self, *, regiao: str = "sa-east-1", endpoint: str = ENDPOINT, erro: Exception | None = None) -> None:
        self.meta = SimpleNamespace(region_name=regiao, endpoint_url=endpoint)
        self._erro = erro
        self.chamadas: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        if self._erro is not None:
            raise self._erro
        self.chamadas.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": "parecer instruído"}]}},
            "usage": {"inputTokens": 130, "outputTokens": 150},
            "stopReason": "end_turn",
        }


def _pedido(*, modelo: str = MODELO, endpoint: str = ENDPOINT, com_dpa: bool = True) -> BrRegionalRequest:
    return BrRegionalRequest(
        endpoint_url=endpoint,
        model=modelo,
        stable_prefix="Voce e Rafael. ",
        variable_suffix="Guia 173505.",
        max_tokens=400,
        credential="",  # SigV4: sem bearer token
        headers={HEADER_VENDOR_DPA_REF: "AWS-SERVICE-TERMS-2026"} if com_dpa else {},
    )


def test_o_endpoint_do_bedrock_esta_na_allowlist() -> None:
    """Sem isto o provedor recusaria na construção, antes de qualquer chamada.

    A região está no PRÓPRIO nome do host, o que faz o allowlist ser uma verificação de
    residência e não uma concessão: outra região não casa.
    """
    assert br_endpoint_denial_reasons(ENDPOINT) == ()
    assert br_endpoint_denial_reasons("https://bedrock-runtime.us-east-1.amazonaws.com") != ()
    assert br_endpoint_denial_reasons("https://s3.sa-east-1.amazonaws.com") != ()


@pytest.mark.asyncio
async def test_recusa_perfil_global_antes_de_transmitir() -> None:
    """Um perfil `global.*` roteia entre regiões por desenho — a recusa vem ANTES do envio."""
    cliente = _ClienteFalso()
    transporte = BedrockBrRegionalTransport(client=cliente)

    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await transporte.send(_pedido(modelo="global.anthropic.claude-opus-5"))

    assert "global." in str(exc.value)
    assert cliente.chamadas == [], "transmitiu PHI para um perfil que roteia fora da região"


@pytest.mark.asyncio
async def test_regiao_servida_e_observada_nao_declarada() -> None:
    cliente = _ClienteFalso(regiao="sa-east-1")
    r = await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert r.served_region == BR_REGIONAL_ATTESTED_REGION
    assert r.synthetic is False
    assert r.usage.input_tokens == 130
    assert r.usage.output_tokens == 150


@pytest.mark.asyncio
async def test_cliente_em_outra_regiao_devolve_a_regiao_real() -> None:
    """O ponto central: NÃO afirmamos São Paulo quando o cliente está noutro lugar.

    O provedor então recusa com "attested served_region=..." e descarta a completion.
    """
    cliente = _ClienteFalso(regiao="us-east-1")
    r = await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert r.served_region == "us-east-1"
    assert r.served_region != BR_REGIONAL_ATTESTED_REGION


@pytest.mark.asyncio
async def test_endpoint_divergente_devolve_o_resolvido_e_nao_o_discado() -> None:
    """Divergência de host tem de VIRAR recusa, não ser normalizada."""
    cliente = _ClienteFalso(endpoint="https://bedrock-runtime.us-east-1.amazonaws.com")
    r = await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert r.endpoint_url == "https://bedrock-runtime.us-east-1.amazonaws.com"
    assert br_endpoint_denial_reasons(r.endpoint_url) != ()


@pytest.mark.asyncio
async def test_as_confirmacoes_vem_do_operador_ter_nomeado_o_contrato() -> None:
    """O Bedrock não devolve cabeçalho de retenção; a confirmação é do operador.

    Sem `MAEZO_PHI_VENDOR_DPA_REF` o provedor nem constrói — mas se o header não chegar ao
    transporte, ele NÃO inventa a confirmação.
    """
    com = await BedrockBrRegionalTransport(client=_ClienteFalso()).send(_pedido(com_dpa=True))
    assert com.zero_retention_acknowledged is True
    assert com.training_prohibited_acknowledged is True

    sem = await BedrockBrRegionalTransport(client=_ClienteFalso()).send(_pedido(com_dpa=False))
    assert sem.zero_retention_acknowledged is False
    assert sem.training_prohibited_acknowledged is False


@pytest.mark.asyncio
async def test_falha_do_bedrock_e_committed_para_nao_reenviar_phi() -> None:
    """O prompt foi transmitido: re-enviar é exposição de dado + gasto duplicado."""

    class _Throttling(Exception):
        pass

    _Throttling.__name__ = "ThrottlingException"
    cliente = _ClienteFalso(erro=_Throttling("slow down"))

    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=cliente).send(_pedido())

    assert exc.value.committed is True
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_erro_nao_transitorio_nao_e_retryable() -> None:
    class _Negado(Exception):
        pass

    _Negado.__name__ = "AccessDeniedException"
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=_ClienteFalso(erro=_Negado("no"))).send(_pedido())

    assert exc.value.retryable is False


def test_o_transporte_declara_que_traz_a_propria_credencial() -> None:
    """É o que dispensa `MAEZO_PHI_API_KEY` — SigV4 não usa bearer token.

    Os outros dois portões (endpoint na allowlist e referência ao contrato) continuam.
    """
    assert BedrockBrRegionalTransport.usa_credencial_propria is True
