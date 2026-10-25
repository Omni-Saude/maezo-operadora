"""Busca semantica: a conexao derrubada pelo tempo ocioso nao derruba mais o indice (25/10/2026).

Em dev o indice da base de saude (~300 assuntos, Titan em sa-east-1) falhou sempre que o cliente ficou
parado mais que o tempo ocioso do NAT: a primeira chamada pela conexao velha caia, o `pool.map` abortava o
indice inteiro e o log dizia so' `FalhaDeEmbeddingsError`. Agora cada chamada e' repetida e o log traz a
CLASSE da causa (nunca a mensagem)."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from typing import Any

import pytest
import structlog

from maezo.conhecimento import busca_semantica as bsem
from tests.unit.agents.test_helena_fases_18_10 import _BASE_BUSCA

botocore_exc = pytest.importorskip("botocore.exceptions")


def _conexao_fechada() -> Exception:
    return botocore_exc.ConnectionClosedError(endpoint_url="https://bedrock-runtime.sa-east-1.amazonaws.com")


def _prazo_de_leitura() -> Exception:
    return botocore_exc.ReadTimeoutError(endpoint_url="https://bedrock-runtime.sa-east-1.amazonaws.com")


def _sem_conexao() -> Exception:
    return botocore_exc.EndpointConnectionError(
        endpoint_url="https://bedrock-runtime.sa-east-1.amazonaws.com"
    )


_FALHAS_DE_CONEXAO: list[Callable[[], Exception]] = [_conexao_fechada, _prazo_de_leitura, _sem_conexao]


class _Cliente:
    """`invoke_model` falso: as `falhas_iniciais` primeiras chamadas levantam `erro()`; depois, vetores."""

    def __init__(self, erro: Callable[[], Exception] | None = None, falhas_iniciais: int = 0) -> None:
        self.erro = erro
        self.falhas_iniciais = falhas_iniciais
        self.chamadas = 0
        self._trava = threading.Lock()

    def invoke_model(self, **kw: Any) -> dict[str, Any]:
        with self._trava:
            self.chamadas += 1
            n = self.chamadas
        if self.erro is not None and n <= self.falhas_iniciais:
            raise self.erro()
        corpo = json.loads(kw["body"])
        if "texts" in corpo:
            saida = {"embeddings": [[1.0, 0.5] for _ in corpo["texts"]]}
        else:
            saida = {"embedding": [1.0, 0.5]}
        return {"body": io.BytesIO(json.dumps(saida).encode())}


def _transporte(cliente: _Cliente, modelo: str = bsem.MODELO_TITAN_V2) -> bsem.TransporteBedrockRegional:
    return bsem.TransporteBedrockRegional(modelo=modelo, client=cliente, dormir=lambda _s: None)


@pytest.mark.parametrize("erro", _FALHAS_DE_CONEXAO)
async def test_indice_titan_sobrevive_a_primeira_chamada_pela_conexao_velha(
    erro: Callable[[], Exception],
) -> None:
    cliente = _Cliente(erro, falhas_iniciais=1)
    textos = [f"assunto {i}" for i in range(40)]
    vetores = await _transporte(cliente).vetores(textos, consulta=False)
    assert len(vetores) == 40 and all(vetores)
    assert cliente.chamadas == 41  # a que caiu foi repetida; nenhuma outra


@pytest.mark.parametrize("erro", _FALHAS_DE_CONEXAO)
async def test_consulta_e_lote_cohere_tambem_repetem(erro: Callable[[], Exception]) -> None:
    titan = _Cliente(erro, falhas_iniciais=2)
    assert await _transporte(titan).vetores(["dor ao urinar"], consulta=True) == [[1.0, 0.5]]
    assert titan.chamadas == 3
    cohere = _Cliente(erro, falhas_iniciais=1)
    vetores = await _transporte(cohere, bsem.MODELO_COHERE_MULTILINGUE).vetores(
        ["a", "b", "c"], consulta=False
    )
    assert len(vetores) == 3 and cohere.chamadas == 2


@pytest.mark.parametrize("erro", _FALHAS_DE_CONEXAO)
async def test_controle_negativo_falha_persistente_sobe_com_a_causa(erro: Callable[[], Exception]) -> None:
    cliente = _Cliente(erro, falhas_iniciais=10**6)
    with pytest.raises(bsem.FalhaDeEmbeddingsError) as caught:
        await _transporte(cliente).vetores(["febre"], consulta=True)
    assert cliente.chamadas == bsem.TENTATIVAS_POR_CHAMADA
    assert bsem.causa_da_falha(caught.value) == type(erro()).__name__


async def test_erro_de_programacao_nunca_e_repetido() -> None:
    cliente = _Cliente(lambda: TypeError("bug"), falhas_iniciais=10**6)
    with pytest.raises(TypeError):
        await _transporte(cliente).vetores(["febre"], consulta=True)
    assert cliente.chamadas == 1


def test_causa_da_falha_sem_causa_e_none() -> None:
    assert bsem.causa_da_falha(bsem.FalhaDeEmbeddingsError("resposta_incompleta")) is None
    assert bsem.causa_da_falha(TimeoutError()) is None


async def test_indice_montado_apesar_da_conexao_velha_e_log_de_falha_traz_a_causa() -> None:
    ok = bsem.BuscaSemantica(transporte=_transporte(_Cliente(_conexao_fechada, 1)), bloquear_no_indice=True)
    with structlog.testing.capture_logs() as logs:
        assert await ok._garantir_indice(_BASE_BUSCA) is True
    assert [e["event"] for e in logs if e["event"].endswith("indice")] == ["helena_busca_semantica_indice"]

    ruim = bsem.BuscaSemantica(
        transporte=_transporte(_Cliente(_conexao_fechada, 10**6)), bloquear_no_indice=True
    )
    with structlog.testing.capture_logs() as logs:
        assert await ruim._garantir_indice(_BASE_BUSCA) is False
    falhou = [e for e in logs if e["event"] == "helena_busca_semantica_indice_falhou"]
    assert falhou and falhou[0]["falha"] == "FalhaDeEmbeddingsError"
    assert falhou[0]["causa"] == "ConnectionClosedError"
    # So' classes no log: nenhum texto de assunto, nenhum endereco.
    assert all("http" not in str(v) for e in logs for v in e.values())


def test_cliente_padrao_usa_tentativas_standard(monkeypatch: pytest.MonkeyPatch) -> None:
    boto3 = pytest.importorskip("boto3")
    vistos: dict[str, Any] = {}

    def _client(servico: str, **kw: Any) -> object:
        vistos.update(kw, servico=servico)
        return object()

    monkeypatch.setattr(boto3, "client", _client)
    bsem.TransporteBedrockRegional(modelo=bsem.MODELO_TITAN_V2)
    cfg = vistos["config"]
    assert vistos["servico"] == "bedrock-runtime" and vistos["region_name"] == "sa-east-1"
    assert cfg.retries == {"max_attempts": bsem.TENTATIVAS_DO_CLIENTE, "mode": "standard"}
    assert bsem.TENTATIVAS_DO_CLIENTE == 3
