"""Porta de EMBEDDINGS da inferencia: o unico lugar do repo que chama o modelo de embeddings (R-040, DEC-20).

DE ONDE VEM. Ate' 31/10/2026 o transporte vivia em `maezo.conhecimento.busca_semantica` e importava
`boto3`/`bedrock-runtime` fora de `runtime.inference` (achado F-051; AGENTS.md regra 6: SDK de modelo so'
em `runtime.inference`, credencial so' no gateway). O dono decidiu (DEC-20, opcao (a), issue #1084, em
31/10/2026) MOVER a chamada para ca'. Movido sem redefinir: mesmos modelos, mesma regiao, mesmo corpo do
pedido, mesmas tentativas — quem chama (`BuscaSemantica`, `BuscaDeProcedimentos`) nao muda de comportamento.

ZONA. O modelo de embeddings e' REGIONAL, em sa-east-1, chamado pelo id do modelo de base (sem perfil de
inferencia, sem perfil global): a MESMA regiao padrao do transporte da zona PHI
(`BedrockBrRegionalTransport.REGIAO`); a excecao regional do dev (DPO, 21/10/2026) nao o alcanca. Nao e' uma
zona do roteador (`MAEZO_INFERENCE_PROVIDER`/`phi=True`) e nao gera texto: so' vetores. O texto enviado e' o
que quem chama ja' minimizou (`saude_gerativa.minimizar`).

FALSOS. Todo falso de `TransporteDeEmbeddings` em `tests/` e' conferido contra este Protocol
(`tests/unit/runtime/test_inference_fakes_match_protocol.py`, familia `TransporteDeEmbeddings`).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from typing import Any, Final, Protocol

import structlog

from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

#: Os modelos REGIONAIS admitidos (sa-east-1, ON_DEMAND, sem perfil de inferencia).
MODELO_COHERE_MULTILINGUE: Final[str] = "cohere.embed-multilingual-v3"
MODELO_TITAN_V2: Final[str] = "amazon.titan-embed-text-v2:0"
MODELOS_ADMITIDOS: Final[frozenset[str]] = frozenset({MODELO_COHERE_MULTILINGUE, MODELO_TITAN_V2})
REGIAO: Final[str] = "sa-east-1"
_LOTE_COHERE: Final[int] = 90
_PARALELO_TITAN: Final[int] = 8
#: Tentativas de CADA chamada ao modelo (25/10/2026). O cliente fica parado entre um uso e outro; passado o
#: tempo ocioso do NAT (~350 s), a conexao guardada no pool ja' caiu e a PRIMEIRA chamada falha (conexao
#: fechada / prazo de leitura). Antes, uma falha dessas derrubava o indice inteiro (os ~300 assuntos) e a
#: busca ficava 5 min sem significado. Agora a chamada e' repetida (em conexao nova) antes de falhar.
TENTATIVAS_POR_CHAMADA: Final[int] = 3
_ESPERAS_ENTRE_TENTATIVAS_S: Final[tuple[float, ...]] = (0.2, 0.6)
#: As tentativas do PROPRIO cliente (botocore, modo `standard`: conexao fechada, prazo, limite de cota).
TENTATIVAS_DO_CLIENTE: Final[int] = 3


class TransporteDeEmbeddings(Protocol):
    @property
    def modelo(self) -> str: ...

    async def vetores(self, textos: Sequence[str], *, consulta: bool) -> list[list[float]]: ...


class FalhaDeEmbeddingsError(RuntimeError):
    """O modelo de embeddings nao rendeu vetores (`RuntimeError`: cai em `EXTERNAL_DEPENDENCY_FAILURES`)."""


class TransporteBedrockRegional:
    """InvokeModel no modelo de embeddings REGIONAL (sa-east-1). `client` injetavel.

    Cada chamada tem ate' `TENTATIVAS_POR_CHAMADA` tentativas (alem das do proprio cliente). O MESMO
    transporte serve a busca da base de saude e o catalogo de procedimentos; o catalogo continua com as
    tentativas por lote dele (`_lote_com_tentativas`) por cima destas, e a consulta continua com o prazo
    curto de quem chama (`asyncio.wait_for`)."""

    def __init__(
        self,
        *,
        modelo: str = MODELO_COHERE_MULTILINGUE,
        client: Any = None,
        prazo_s: float = 10.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if modelo not in MODELOS_ADMITIDOS:
            raise ValueError(f"busca semantica: modelo {modelo!r} fora da lista regional admitida")
        self.modelo = modelo
        self._dormir = dormir
        if client is not None:
            self._client = client
        else:
            import boto3  # type: ignore[import-untyped]  # extra [bedrock] opcional
            from botocore.config import Config  # type: ignore[import-untyped]

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=REGIAO,
                config=Config(
                    read_timeout=prazo_s,
                    connect_timeout=3.0,
                    retries={"max_attempts": TENTATIVAS_DO_CLIENTE, "mode": "standard"},
                ),
            )

    def _invocar(self, corpo: dict[str, Any]) -> dict[str, Any]:
        bruto = self._client.invoke_model(
            modelId=self.modelo,
            body=json.dumps(corpo),
            contentType="application/json",
            accept="application/json",
        )
        corpo_resp = bruto["body"].read() if hasattr(bruto.get("body"), "read") else bruto.get("body")
        resposta: dict[str, Any] = json.loads(corpo_resp)
        return resposta

    def _invocar_com_tentativas(self, corpo: dict[str, Any]) -> dict[str, Any]:
        """`_invocar` com ate' `TENTATIVAS_POR_CHAMADA` tentativas: a conexao derrubada pelo tempo ocioso so'
        falha a primeira chamada. Erro de programacao nunca e' repetido; a ultima falha sobe como veio."""
        for tentativa in range(TENTATIVAS_POR_CHAMADA):
            try:
                return self._invocar(corpo)
            except PROGRAMMING_ERRORS:
                raise
            except Exception as exc:
                if tentativa == TENTATIVAS_POR_CHAMADA - 1:
                    raise
                # So' a CLASSE do erro: a mensagem do SDK pode trazer o texto enviado.
                logger.info(
                    "busca_semantica_chamada_repetida",
                    modelo=self.modelo,
                    tentativa=tentativa + 1,
                    causa=type(exc).__name__,
                )
                esperas = _ESPERAS_ENTRE_TENTATIVAS_S
                self._dormir(esperas[min(tentativa, len(esperas) - 1)])
        raise AssertionError("inalcancavel")  # pragma: no cover

    def _vetores_sync(self, textos: Sequence[str], consulta: bool) -> list[list[float]]:
        saida: list[list[float]] = []
        if self.modelo == MODELO_COHERE_MULTILINGUE:
            tipo = "search_query" if consulta else "search_document"
            for i in range(0, len(textos), _LOTE_COHERE):
                lote = [t[:2000] for t in textos[i : i + _LOTE_COHERE]]
                resp = self._invocar_com_tentativas({"texts": lote, "input_type": tipo, "truncate": "END"})
                saida.extend([float(x) for x in v] for v in resp.get("embeddings") or [])
        else:
            # Titan: um texto por chamada; o indice (~300 assuntos) vai em paralelo (medido em dev em
            # 18/10/2026:
            # ~21 s em serie).
            from concurrent.futures import ThreadPoolExecutor

            def _um(t: str) -> list[float]:
                corpo = {"inputText": t[:8000], "dimensions": 512, "normalize": True}
                resp = self._invocar_com_tentativas(corpo)
                return [float(x) for x in resp.get("embedding") or []]

            if len(textos) == 1:
                saida.append(_um(textos[0]))
            else:
                with ThreadPoolExecutor(max_workers=_PARALELO_TITAN) as pool:
                    saida.extend(pool.map(_um, textos))
        if len(saida) != len(textos) or any(not v for v in saida):
            raise FalhaDeEmbeddingsError("resposta_incompleta")
        return saida

    async def vetores(self, textos: Sequence[str], *, consulta: bool) -> list[list[float]]:
        try:
            return await asyncio.to_thread(self._vetores_sync, list(textos), consulta)
        except PROGRAMMING_ERRORS:
            raise
        except FalhaDeEmbeddingsError:
            raise
        except Exception as exc:  # nenhum erro do SDK escapa (so' a classe, nunca a mensagem)
            raise FalhaDeEmbeddingsError(f"servico:{type(exc).__name__}") from exc


__all__ = [
    "MODELOS_ADMITIDOS",
    "MODELO_COHERE_MULTILINGUE",
    "MODELO_TITAN_V2",
    "REGIAO",
    "TENTATIVAS_DO_CLIENTE",
    "TENTATIVAS_POR_CHAMADA",
    "FalhaDeEmbeddingsError",
    "TransporteBedrockRegional",
    "TransporteDeEmbeddings",
]
