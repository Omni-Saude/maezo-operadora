"""Porta de embeddings em `runtime.inference` (R-040, DEC-20): o pedido ao modelo e' BYTE A BYTE o de antes.

Os literais abaixo foram tirados do transporte de `origin/main` @331f7c4f
(`src/maezo/conhecimento/busca_semantica.py::TransporteBedrockRegional`), antes da mudanca: mesmo id de
modelo, mesmo corpo JSON (mesma ordem de chaves e mesma serializacao), mesmos cabecalhos, mesmo cliente.
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from maezo.conhecimento import busca_semantica as bsem
from maezo.conhecimento import catalogo_procedimentos as cat
from maezo.runtime.inference import embeddings as emb
from maezo.runtime.inference.br_regional import BedrockBrRegionalTransport


class _Gravador:
    """`invoke_model` que GUARDA os argumentos exatos de cada chamada e devolve um vetor fixo."""

    def __init__(self) -> None:
        self.chamadas: list[dict[str, Any]] = []

    def invoke_model(self, **kw: Any) -> dict[str, Any]:
        self.chamadas.append(kw)
        corpo = json.loads(kw["body"])
        saida = (
            {"embeddings": [[0.5, 1.0] for _ in corpo["texts"]]} if "texts" in corpo else {"embedding": [0.5]}
        )
        return {"body": io.BytesIO(json.dumps(saida).encode())}


_CABECALHOS = {"contentType": "application/json", "accept": "application/json"}


async def test_cohere_consulta_pedido_byte_a_byte() -> None:
    g = _Gravador()
    t = emb.TransporteBedrockRegional(modelo=emb.MODELO_COHERE_MULTILINGUE, client=g)
    assert await t.vetores(["dor ao urinar"], consulta=True) == [[0.5, 1.0]]
    assert g.chamadas == [
        {
            "modelId": "cohere.embed-multilingual-v3",
            "body": '{"texts": ["dor ao urinar"], "input_type": "search_query", "truncate": "END"}',
            **_CABECALHOS,
        }
    ]


async def test_cohere_indice_lotes_de_90_e_corte_em_2000() -> None:
    g = _Gravador()
    t = emb.TransporteBedrockRegional(modelo=emb.MODELO_COHERE_MULTILINGUE, client=g)
    textos = ["x" * 2500] + [f"assunto {i}" for i in range(94)]
    assert len(await t.vetores(textos, consulta=False)) == 95
    assert [len(json.loads(c["body"])["texts"]) for c in g.chamadas] == [90, 5]
    primeiro = json.loads(g.chamadas[0]["body"])
    assert primeiro["texts"][0] == "x" * 2000 and primeiro["input_type"] == "search_document"
    assert g.chamadas[1]["body"] == json.dumps(
        {"texts": [f"assunto {i}" for i in range(89, 94)], "input_type": "search_document", "truncate": "END"}
    )


async def test_titan_pedido_byte_a_byte_e_corte_em_8000() -> None:
    g = _Gravador()
    t = emb.TransporteBedrockRegional(modelo=emb.MODELO_TITAN_V2, client=g)
    assert await t.vetores(["febre"], consulta=True) == [[0.5]]
    assert g.chamadas == [
        {
            "modelId": "amazon.titan-embed-text-v2:0",
            "body": '{"inputText": "febre", "dimensions": 512, "normalize": true}',
            **_CABECALHOS,
        }
    ]
    g2 = _Gravador()
    await emb.TransporteBedrockRegional(modelo=emb.MODELO_TITAN_V2, client=g2).vetores(
        ["y" * 9000], consulta=False
    )
    assert json.loads(g2.chamadas[0]["body"])["inputText"] == "y" * 8000


def test_cliente_padrao_e_o_mesmo_de_antes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `client`, o MESMO `boto3.client(...)` de antes (servico, regiao, prazos, tentativas). Modulos
    falsos em `sys.modules`: nenhuma credencial nem rede."""
    vistos: dict[str, Any] = {}

    class _Config:
        def __init__(self, **kw: Any) -> None:
            vistos["config"] = kw

    def _client(servico: str, **kw: Any) -> object:
        vistos["servico"] = servico
        vistos["kw"] = kw
        return object()

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=_client))
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", types.SimpleNamespace(Config=_Config))
    emb.TransporteBedrockRegional(modelo=emb.MODELO_TITAN_V2, prazo_s=7.5)
    assert vistos["servico"] == "bedrock-runtime"
    assert set(vistos["kw"]) == {"region_name", "config"} and vistos["kw"]["region_name"] == "sa-east-1"
    assert vistos["config"] == {
        "read_timeout": 7.5,
        "connect_timeout": 3.0,
        "retries": {"max_attempts": 3, "mode": "standard"},
    }


def test_modelos_admitidos_e_recusa_de_modelo_global() -> None:
    admitidos = frozenset({"cohere.embed-multilingual-v3", "amazon.titan-embed-text-v2:0"})
    assert admitidos == emb.MODELOS_ADMITIDOS
    assert emb.TENTATIVAS_POR_CHAMADA == 3 and emb.TENTATIVAS_DO_CLIENTE == 3
    with pytest.raises(ValueError):
        emb.TransporteBedrockRegional(modelo="global.cohere.embed-v4:0", client=object())


def test_regiao_dos_embeddings_e_a_regiao_atestada_da_zona_phi() -> None:
    """Pino de ZONA: o texto (ja' minimizado) dos embeddings vai para a regiao padrao do transporte da zona
    PHI (sa-east-1). A excecao regional do dev (DPO, 21/10/2026) NAO alcanca os embeddings — como antes."""
    assert emb.REGIAO == BedrockBrRegionalTransport.REGIAO == "sa-east-1"


def test_quem_chama_usa_a_porta_de_runtime_inference() -> None:
    """Os nomes antigos de `busca_semantica` sao a MESMA porta (sem copia); o catalogo aceita o transporte."""
    assert bsem.TransporteBedrockRegional is emb.TransporteBedrockRegional
    assert bsem.TransporteDeEmbeddings is emb.TransporteDeEmbeddings
    assert bsem.FalhaDeEmbeddingsError is emb.FalhaDeEmbeddingsError
    assert bsem.MODELOS_ADMITIDOS is emb.MODELOS_ADMITIDOS
    transporte: cat.TransporteDeEmbeddings = emb.TransporteBedrockRegional(client=object())
    assert transporte.modelo == emb.MODELO_COHERE_MULTILINGUE
    fonte = Path(bsem.__file__).read_text(encoding="utf-8")
    assert "boto3" not in fonte and "botocore" not in fonte and "bedrock-runtime" not in fonte
