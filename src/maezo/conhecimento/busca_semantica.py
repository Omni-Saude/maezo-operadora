"""BUSCA POR SIGNIFICADO na base de saude (fase 3 de 18/10/2026) — atras de `MAEZO_HELENA_BUSCA_SEMANTICA`,
so'
para a allowlist da saude gerativa (ADR-0065, DL-0119).

O QUE E'. Um indice EM MEMORIA dos assuntos da base (`spec/conhecimento/saude`, ~220 arquivos) com o vetor de
embeddings de cada um (titulo + sinonimos + inicio do texto), gerado NA SUBIDA do processo, na primeira busca,
e
guardado enquanto a base for a mesma (impressao digital dos textos). A pergunta da pessoa (ja' MINIMIZADA)
vira
um vetor e o assunto mais parecido (cosseno) e' o candidato. O lexico atual (`base_saude.buscar`,
exato/contido)
DESEMPATA quando o lexico e o significado ficam proximos.

O QUE NAO E'. Nunca e' portao de recusa nem decide rota: so' escolhe o APOIO (o trecho da fonte que vai junto
para a redacao). Abaixo do limiar, nenhum apoio — a redacao segue sem ele. Falha/prazo do modelo de embeddings
-> o lexico de antes (nunca erro para a pessoa).

RESIDENCIA. O modelo de embeddings e' REGIONAL, em sa-east-1, chamado pelo id do modelo de base (sem perfil de
inferencia, sem perfil global): a mesma regiao da zona PHI do GLM-5. O texto enviado e' o `minimizar` da saude
gerativa (CPF, nome dito, carteirinha, telefone, e-mail e datas trocados por `[dado removido]`).

LIMIAR. Calibrado em dev (18/10/2026) contra os casos de aceite: ver `LIMIAR_PADRAO` e o relatorio da fase 3.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import structlog

from maezo.conhecimento import base_saude as bs
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

#: Os modelos REGIONAIS admitidos (sa-east-1, ON_DEMAND, sem perfil de inferencia).
MODELO_COHERE_MULTILINGUE: Final[str] = "cohere.embed-multilingual-v3"
MODELO_TITAN_V2: Final[str] = "amazon.titan-embed-text-v2:0"
MODELOS_ADMITIDOS: Final[frozenset[str]] = frozenset({MODELO_COHERE_MULTILINGUE, MODELO_TITAN_V2})
REGIAO: Final[str] = "sa-east-1"
#: Similaridade minima (cosseno) para o assunto virar APOIO. Calibrado em dev; ver o relatorio da fase 3.
LIMIAR_PADRAO: Final[float] = 0.45
#: Diferenca de similaridade em que o LEXICO (exato/contido) desempata contra o melhor do significado.
MARGEM_DO_LEXICO: Final[float] = 0.05
PRAZO_CONSULTA_S: Final[float] = 3.0
PRAZO_INDICE_S: Final[float] = 60.0
_LOTE_COHERE: Final[int] = 90
_CARACTERES_DO_TEXTO: Final[int] = 240
_TEMPO_DE_ESPERA_APOS_FALHA_S: Final[float] = 300.0


class TransporteDeEmbeddings(Protocol):
    @property
    def modelo(self) -> str: ...

    async def vetores(self, textos: Sequence[str], *, consulta: bool) -> list[list[float]]: ...


class FalhaDeEmbeddingsError(RuntimeError):
    """O modelo de embeddings nao rendeu vetores (`RuntimeError`: cai em `EXTERNAL_DEPENDENCY_FAILURES`)."""


class TransporteBedrockRegional:
    """InvokeModel no modelo de embeddings REGIONAL (sa-east-1), sem repeticao. `client` injetavel."""

    def __init__(
        self, *, modelo: str = MODELO_COHERE_MULTILINGUE, client: Any = None, prazo_s: float = 10.0
    ) -> None:
        if modelo not in MODELOS_ADMITIDOS:
            raise ValueError(f"busca semantica: modelo {modelo!r} fora da lista regional admitida")
        self.modelo = modelo
        if client is not None:
            self._client = client
        else:
            import boto3  # type: ignore[import-untyped]  # extra [bedrock] opcional
            from botocore.config import Config  # type: ignore[import-untyped]

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=REGIAO,
                config=Config(
                    read_timeout=prazo_s, connect_timeout=3.0, retries={"max_attempts": 1, "mode": "standard"}
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

    def _vetores_sync(self, textos: Sequence[str], consulta: bool) -> list[list[float]]:
        saida: list[list[float]] = []
        if self.modelo == MODELO_COHERE_MULTILINGUE:
            tipo = "search_query" if consulta else "search_document"
            for i in range(0, len(textos), _LOTE_COHERE):
                lote = [t[:2000] for t in textos[i : i + _LOTE_COHERE]]
                resp = self._invocar({"texts": lote, "input_type": tipo, "truncate": "END"})
                saida.extend([float(x) for x in v] for v in resp.get("embeddings") or [])
        else:
            for t in textos:
                resp = self._invocar({"inputText": t[:8000], "dimensions": 512, "normalize": True})
                saida.append([float(x) for x in resp.get("embedding") or []])
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


def _normalizado(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cosseno(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def documento_do_assunto(assunto: bs.Assunto) -> str:
    """O texto que representa o assunto no indice: titulo, sinonimos e o inicio do texto aprovado."""
    sinonimos = ", ".join(assunto.sinonimos)
    return f"{assunto.titulo}. {sinonimos}. {assunto.texto[:_CARACTERES_DO_TEXTO]}"


def impressao_da_base(base: bs.BaseSaude) -> str:
    h = hashlib.sha256()
    for i in sorted(base.assuntos):
        h.update(i.encode())
        h.update(documento_do_assunto(base.assuntos[i]).encode())
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Achado:
    assunto_id: str
    similaridade: float
    como: str  # "significado" | "lexico_desempate"
    latencia_ms: int


@dataclass
class BuscaSemantica:
    """UM por processo (composicao do receptor), so' com a chave ligada. Indice montado na primeira busca."""

    transporte: TransporteDeEmbeddings
    limiar: float = LIMIAR_PADRAO
    margem_do_lexico: float = MARGEM_DO_LEXICO
    prazo_consulta_s: float = PRAZO_CONSULTA_S
    relogio: Callable[[], float] = time.monotonic
    _indice: dict[str, list[float]] = field(default_factory=dict)
    _impressao: str | None = None
    _trava: asyncio.Lock | None = None
    _falhou_em: float | None = None

    async def _garantir_indice(self, base: bs.BaseSaude) -> bool:
        impressao = impressao_da_base(base)
        if self._impressao == impressao and self._indice:
            return True
        if self._falhou_em is not None and self.relogio() - self._falhou_em < _TEMPO_DE_ESPERA_APOS_FALHA_S:
            return False
        if self._trava is None:
            self._trava = asyncio.Lock()
        async with self._trava:
            if self._impressao == impressao and self._indice:
                return True
            ids = sorted(base.assuntos)
            inicio = self.relogio()
            try:
                vetores = await asyncio.wait_for(
                    self.transporte.vetores(
                        [documento_do_assunto(base.assuntos[i]) for i in ids], consulta=False
                    ),
                    timeout=PRAZO_INDICE_S,
                )
            except PROGRAMMING_ERRORS:
                raise
            except EXTERNAL_DEPENDENCY_FAILURES as exc:
                self._falhou_em = self.relogio()
                logger.warning(
                    "helena_busca_semantica_indice_falhou",
                    modelo=self.transporte.modelo,
                    falha=type(exc).__name__,
                    assuntos=len(ids),
                )
                return False
            self._indice = {i: _normalizado(v) for i, v in zip(ids, vetores, strict=True)}
            self._impressao = impressao
            self._falhou_em = None
            logger.info(
                "helena_busca_semantica_indice",
                modelo=self.transporte.modelo,
                assuntos=len(ids),
                latencia_ms=int((self.relogio() - inicio) * 1000),
                impressao=impressao,
            )
            return True

    async def buscar(
        self,
        base: bs.BaseSaude,
        consulta: str,
        *,
        filtro: Callable[[bs.Assunto], bool] | None = None,
    ) -> Achado | None:
        """O assunto mais parecido ACIMA do limiar, ou `None` (abaixo do limiar, indice indisponivel, falha ou
        prazo). `consulta` ja' minimizada. `filtro` exclui assuntos (ex.: populacao incompativel)."""
        if not consulta.strip() or not await self._garantir_indice(base):
            return None
        inicio = self.relogio()
        try:
            vetor = await asyncio.wait_for(
                self.transporte.vetores([consulta], consulta=True), timeout=self.prazo_consulta_s
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            logger.warning(
                "helena_busca_semantica_falhou", modelo=self.transporte.modelo, falha=type(exc).__name__
            )
            return None
        q = _normalizado(vetor[0])
        notas = {
            i: _cosseno(q, v)
            for i, v in self._indice.items()
            if (a := base.get(i)) is not None and (filtro is None or filtro(a))
        }
        latencia = int((self.relogio() - inicio) * 1000)
        if not notas:
            return None
        melhor = max(notas, key=lambda i: (notas[i], i))
        achado = Achado(melhor, notas[melhor], "significado", latencia)
        lexico = bs.buscar(base, consulta)
        if (
            lexico.achado is not None
            and lexico.como in ("exato", "contido")
            and lexico.achado in notas
            and lexico.achado != melhor
            and notas[melhor] - notas[lexico.achado] <= self.margem_do_lexico
        ):
            achado = Achado(lexico.achado, notas[lexico.achado], "lexico_desempate", latencia)
        acima = achado.similaridade >= self.limiar
        logger.info(
            "helena_busca_semantica",
            modelo=self.transporte.modelo,
            assunto=achado.assunto_id if acima else None,
            similaridade=round(achado.similaridade, 3),
            como=achado.como,
            acima_do_limiar=acima,
            latencia_ms=latencia,
        )
        return achado if acima else None


__all__ = [
    "LIMIAR_PADRAO",
    "MARGEM_DO_LEXICO",
    "MODELOS_ADMITIDOS",
    "MODELO_COHERE_MULTILINGUE",
    "MODELO_TITAN_V2",
    "Achado",
    "BuscaSemantica",
    "FalhaDeEmbeddingsError",
    "TransporteBedrockRegional",
    "TransporteDeEmbeddings",
    "documento_do_assunto",
    "impressao_da_base",
]
