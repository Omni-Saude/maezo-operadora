"""Provas de comportamento do motor do pacote K1 (contrato-k1.md §3 `provas[]`, §11 item 12).

O rco publica, no `formato_manifesto` 2.1.0, provas: um DMN de teste e casos (`entradas` + `esperado`)
sob `k1/provas/<id>/`. Antes de pinar o pacote o consumidor roda cada caso no PROPRIO motor (CIB Seven)
e recusa o pin se algum caso divergir do `esperado`. Este modulo faz so isso: carrega os casos pelos
arquivos que o manifesto lista, avalia cada um num `Avaliador` e devolve as divergencias. Nao ha
efeito em runtime: ninguem o chama no caminho de decisao.

O QUE "casa" SIGNIFICA (fixture `teste-unario-booleano`, README do rco): cada decisao de teste tem
hit policy FIRST com duas linhas. A linha 1 e a celula testada e emite `resultado = "casa"`; a linha 2
e `-` (qualquer entrada) e emite `resultado = "nao_casa"`. Logo "casa" = a celula testada casou com a
entrada (a linha 1 foi a escolhida); "nao_casa" = so a linha de fechamento casou. Operacionalmente:
a avaliacao devolve EXATAMENTE uma linha e o texto da sua coluna `resultado` e igual a
`esperado.resultado`. Zero linhas, mais de uma, coluna ausente ou valor diferente e divergencia.

Fail-closed: caso malformado, decisao ausente do DMN, erro do motor ou resultado fora de
{casa, nao_casa} contam como divergencia; nunca se ajusta o `esperado` ao que o motor devolveu.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

from maezo.platform.rco_manifesto import ManifestoK1InvalidoError, ProvaK1, json_estrito
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport

RESULTADOS: Final[frozenset[str]] = frozenset({"casa", "nao_casa"})
SAIDA_RESULTADO: Final[str] = "resultado"


class Avaliador(Protocol):
    """Avalia uma decisao (id dentro do DMN da prova) com as entradas; devolve as linhas da tabela."""

    async def avaliar(self, decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CasoProva:
    prova: str
    arquivo: str  # caminho do arquivo de casos, relativo a `k1/`
    dmn: str  # `dmn` do arquivo de casos (relativo a `provas/<id>/`)
    decisao: str
    id_caso: str
    entradas: Mapping[str, Any]
    esperado: str  # `casa` | `nao_casa`


@dataclass(frozen=True, slots=True)
class Divergencia:
    prova: str
    caso: str  # `<arquivo>#<id do caso>`
    esperado: str
    obtido: str  # `casa`, `nao_casa`, ou o motivo (`erro: ...`, `0 linhas`, ...)

    def render(self) -> str:
        return f"prova {self.prova} {self.caso}: esperado {self.esperado!r}, obtido {self.obtido!r}"


def carregar_casos(prova: ProvaK1, *, k1_dir: Path) -> list[CasoProva]:
    """Casos de `provas/<id>/validacao/casos/*.json`, so dos arquivos que o manifesto lista."""
    casos: list[CasoProva] = []
    problemas: list[str] = []
    pasta = prova.esperado + "/"
    for arq in prova.arquivos:
        if not (arq.caminho.startswith(pasta) and arq.caminho.endswith(".json")):
            continue
        try:
            dados = json_estrito((k1_dir.joinpath(*arq.caminho.split("/"))).read_bytes())
        except (OSError, ValueError) as exc:
            problemas.append(f"{arq.caminho}: ilegivel ({type(exc).__name__})")
            continue
        if not (
            isinstance(dados, dict)
            and isinstance(dados.get("dmn"), str)
            and isinstance(dados.get("decisao"), str)
            and isinstance(dados.get("casos"), list)
        ):
            problemas.append(f"{arq.caminho}: faltam dmn, decisao ou casos[]")
            continue
        for i, caso in enumerate(dados["casos"]):
            entradas = caso.get("entradas") if isinstance(caso, dict) else None
            esperado = caso.get("esperado") if isinstance(caso, dict) else None
            resultado = esperado.get("resultado") if isinstance(esperado, dict) else None
            if not isinstance(entradas, dict) or resultado not in RESULTADOS:
                problemas.append(f"{arq.caminho}: casos[{i}] sem entradas ou esperado.resultado valido")
                continue
            id_caso = str(caso.get("id", i))
            casos.append(
                CasoProva(prova.id, arq.caminho, dados["dmn"], dados["decisao"], id_caso, entradas, resultado)
            )
    if problemas:
        raise ManifestoK1InvalidoError(problemas)
    if not casos:
        raise ManifestoK1InvalidoError([f"prova {prova.id}: nenhum caso em {prova.esperado}"])
    return casos


def resultado_da_avaliacao(linhas: Sequence[Mapping[str, Any]]) -> str:
    """`casa`/`nao_casa` da linha unica devolvida; qualquer outra forma devolve o motivo (divergente)."""
    if len(linhas) != 1:
        return f"{len(linhas)} linhas"
    valor = linhas[0].get(SAIDA_RESULTADO)
    return valor if isinstance(valor, str) and valor in RESULTADOS else f"{SAIDA_RESULTADO}={valor!r}"


async def executar_prova(prova: ProvaK1, *, k1_dir: Path, avaliador: Avaliador) -> list[Divergencia]:
    """Roda todos os casos da prova; devolve as divergencias (vazia = a prova passou)."""
    divergencias: list[Divergencia] = []
    for caso in carregar_casos(prova, k1_dir=k1_dir):
        onde = f"{caso.arquivo}#{caso.id_caso}"
        try:
            obtido = resultado_da_avaliacao(await avaliador.avaliar(caso.decisao, caso.entradas))
        except Exception as exc:  # o motor pode falhar de varias formas; todas recusam o pin
            obtido = f"erro: {type(exc).__name__}"
        if obtido != caso.esperado:
            divergencias.append(Divergencia(caso.prova, onde, caso.esperado, obtido))
    return divergencias


async def executar_provas(
    provas: Sequence[ProvaK1], *, k1_dir: Path, avaliador: Avaliador
) -> list[Divergencia]:
    """Todas as provas do manifesto. Lista vazia = o pin pode seguir (contrato §11 item 12)."""
    divergencias: list[Divergencia] = []
    for prova in provas:
        divergencias.extend(await executar_prova(prova, k1_dir=k1_dir, avaliador=avaliador))
    return divergencias


class AvaliadorCibSeven:
    """`Avaliador` sobre o transporte real do CIB Seven (a decisao ja implantada no motor)."""

    def __init__(self, transporte: CibSevenDmnTransport) -> None:
        self._transporte = transporte

    async def avaliar(self, decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        linhas, _versao = await self._transporte.evaluate(decisao, dict(entradas))
        return linhas


async def implantar_prova(client: httpx.AsyncClient, prova: ProvaK1, *, k1_dir: Path) -> None:
    """Implanta no motor os DMN da prova (`*.dmn` listados), sob o nome `rco-prova-<id>`."""
    arquivos: dict[str, tuple[str, bytes, str]] = {}
    for arq in prova.arquivos:
        if arq.caminho.endswith(".dmn"):
            nome = arq.caminho.rsplit("/", 1)[-1]
            arquivos[nome] = (nome, k1_dir.joinpath(*arq.caminho.split("/")).read_bytes(), "text/xml")
    if not arquivos:
        raise ManifestoK1InvalidoError([f"prova {prova.id}: nenhum DMN listado"])
    resp = await client.post(
        "/deployment/create",
        data={
            "deployment-name": f"rco-prova-{prova.id}",
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files=arquivos,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"implantacao da prova {prova.id} recusada: {resp.status_code}")


__all__ = [
    "RESULTADOS",
    "Avaliador",
    "AvaliadorCibSeven",
    "CasoProva",
    "Divergencia",
    "carregar_casos",
    "executar_prova",
    "executar_provas",
    "implantar_prova",
    "resultado_da_avaliacao",
]
