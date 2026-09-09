"""Bedrock regional: admissao ANTES do envio e proveniencia explicita.

Clientes controlados locais, sem AWS. Regiao elegivel deriva do contrato de
roteamento do recurso regional; client.meta nao observa execucao do fornecedor.
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

    def __init__(
        self, *, regiao: str = "sa-east-1", endpoint: str = ENDPOINT, erro: Exception | None = None
    ) -> None:
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

    A admissao lexical nao e prova de execucao regional do fornecedor; outra
    regiao nao casa, e o transporte ainda exige igualdade do endpoint completo.
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

    assert "not_direct_regional_foundation_model" in str(exc.value)
    assert cliente.chamadas == [], "transmitiu PHI para um perfil que roteia fora da região"


@pytest.mark.asyncio
async def test_regiao_elegivel_tem_proveniencia_contratual_explicita() -> None:
    cliente = _ClienteFalso(regiao="sa-east-1")
    r = await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert r.served_region == BR_REGIONAL_ATTESTED_REGION
    assert r.region_evidence_source == "regional_direct_model_contract"
    assert r.endpoint_evidence_source == "sdk_resolved_endpoint"
    assert r.synthetic is False
    assert r.usage.input_tokens == 130
    assert r.usage.output_tokens == 150


@pytest.mark.asyncio
async def test_cliente_em_outra_regiao_recusa_antes_do_envio() -> None:
    cliente = _ClienteFalso(regiao="us-east-1")
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert cliente.chamadas == []
    assert exc.value.committed is False


@pytest.mark.asyncio
async def test_endpoint_divergente_recusa_antes_do_envio() -> None:
    cliente = _ClienteFalso(endpoint="https://bedrock-runtime.us-east-1.amazonaws.com")
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=cliente).send(_pedido())
    assert cliente.chamadas == []
    assert exc.value.committed is False


@pytest.mark.asyncio
async def test_as_confirmacoes_vem_do_operador_ter_nomeado_o_contrato() -> None:
    """O Bedrock não devolve cabeçalho de retenção; a confirmação é do operador.

    Sem `MAEZO_PHI_VENDOR_DPA_REF` o provedor nem constrói — mas se o header não chegar ao
    transporte, ele NÃO inventa a confirmação.
    """
    com = await BedrockBrRegionalTransport(client=_ClienteFalso()).send(_pedido(com_dpa=True))
    assert com.zero_retention_acknowledged is True
    assert com.training_prohibited_acknowledged is True
    assert com.retention_evidence_source == "operator_contract_reference"

    cliente = _ClienteFalso()
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=cliente).send(_pedido(com_dpa=False))
    assert cliente.chamadas == []
    assert exc.value.committed is False


@pytest.mark.asyncio
async def test_falha_do_bedrock_e_committed_para_nao_reenviar_phi() -> None:
    """O prompt foi transmitido: re-enviar é exposição de dado + gasto duplicado."""

    class _ThrottlingError(Exception):
        pass

    _ThrottlingError.__name__ = "ThrottlingException"
    cliente = _ClienteFalso(erro=_ThrottlingError("slow down"))

    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=cliente).send(_pedido())

    assert exc.value.committed is True
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_erro_nao_transitorio_nao_e_retryable() -> None:
    class _NegadoError(Exception):
        pass

    _NegadoError.__name__ = "AccessDeniedException"
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=_ClienteFalso(erro=_NegadoError("no"))).send(_pedido())

    assert exc.value.retryable is False


def test_o_transporte_declara_que_traz_a_propria_credencial() -> None:
    """É o que dispensa `MAEZO_PHI_API_KEY` — SigV4 não usa bearer token.

    Os outros dois portões (endpoint na allowlist e referência ao contrato) continuam.
    """
    assert BedrockBrRegionalTransport.usa_credencial_propria is True
