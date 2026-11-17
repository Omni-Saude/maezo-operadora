"""Composicao de decisoes K1 do rco (DL-0174; contrato-k1.md §11 item 14; formato_manifesto 2.2.0).

O QUE ISTO E. Uma tabela do rco dividida em varias decisoes (para caber no orcamento do motor) volta a
ser UMA decisao pela composicao que o manifesto declara em `composicoes[]` (lido e conferido por
`maezo.platform.rco_manifesto`). Aqui fica a semantica, generica e sem motor: quem chama passa o grupo,
um avaliador `(id da decisao, entradas) -> linhas do resultado` e as entradas.

REGRA `primeira_com_idRegra` (unica do vocabulario fechado `rco_manifesto.REGRAS_COMPOSICAO`):
- TODAS as decisoes do grupo sao avaliadas, com as MESMAS entradas (cada chamada recebe uma copia
  profunda do mesmo instantaneo: um avaliador nao muda o que o seguinte ve);
- vale a primeira decisao, na ordem do grupo, cujo resultado traz `idRegra` preenchido (`None` e `""`
  contam como ausente);
- se nenhuma antes do padrao trouxer, vale o resultado do padrao (com ou sem `idRegra`);
- o padrao pode trazer `idRegra` junto de uma anterior: vale a anterior.

FAIL-CLOSED: o desfecho nunca e glosa nem negativa, e sempre `revisao_humana`, quando:
- duas ou mais decisoes antes do padrao trazem `idRegra` (`violacao_prova_rco`, codigo
  `CMP06_EXCLUSIVIDADE`: o gate `make composicao-dmn` do rco prova que isso nao acontece);
- uma decisao do grupo devolve mais de uma linha (`violacao_prova_rco`, codigo `CMP08_UNIQUE`);
- um resultado nao e lista de objetos ou traz `idRegra` que nao e texto (`resultado_invalido`);
- o avaliador falha em QUALQUER decisao do grupo (`avaliador_falhou`): nunca se usa resultado parcial.
Cada caso grava um log estruturado so com os ids das decisoes e o codigo, e conta
`maezo_rco_composicao_revisao_humana_total{motivo}`.

GUARDA. Uma decisao que esta num grupo e NAO e o padrao nunca e avaliada sozinha: sozinha ela nao diz
nada sobre o par (o rco: "consumidor que avalia so mex_ortop deixa de glosar os pares"). Quem avalia
uma decisao `rco_` chama `exigir_avaliacao_isolada(pin, id)` antes e, se a decisao for de um grupo,
`compor`. NADA aqui esta ligado a um worker ainda: ortopedia nao tem dominio no montador, e a ligacao
vem com ele (DL-0174).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import structlog

from maezo.platform.observability import record_rco_composicao_revisao
from maezo.platform.rco_manifesto import (
    REGRA_PRIMEIRA_COM_ID_REGRA,
    SAIDA_ID_REGRA,
    ComposicaoK1,
)

logger = structlog.get_logger(__name__)

#: Linhas do resultado de uma decisao (como `DmnTransport.evaluate` as devolve).
Linhas = Sequence[Mapping[str, Any]]
Avaliador = Callable[[str, Mapping[str, Any]], Linhas]
AvaliadorAsync = Callable[[str, Mapping[str, Any]], Awaitable[Linhas]]

DESFECHO_ESCOLHIDO: Final[str] = "escolhido"
DESFECHO_REVISAO_HUMANA: Final[str] = "revisao_humana"
#: Motivos de `revisao_humana` (= `observability.RCO_COMPOSICAO_MOTIVOS`, paridade em teste).
MOTIVO_VIOLACAO_PROVA: Final[str] = "violacao_prova_rco"
MOTIVO_AVALIADOR_FALHOU: Final[str] = "avaliador_falhou"
MOTIVO_RESULTADO_INVALIDO: Final[str] = "resultado_invalido"
MOTIVOS: Final[frozenset[str]] = frozenset(
    {MOTIVO_VIOLACAO_PROVA, MOTIVO_AVALIADOR_FALHOU, MOTIVO_RESULTADO_INVALIDO}
)
#: Codigos do log (os de `scripts/checar_composicao_dmn.py` do rco quando a prova dele foi violada).
CODIGO_EXCLUSIVIDADE: Final[str] = "CMP06_EXCLUSIVIDADE"
CODIGO_UNIQUE: Final[str] = "CMP08_UNIQUE"
CODIGO_RESULTADO_INVALIDO: Final[str] = "resultado_invalido"
CODIGO_AVALIADOR_FALHOU: Final[str] = "avaliador_falhou"
CODIGO_REGRA_DESCONHECIDA: Final[str] = "regra_desconhecida"


class DecisaoSoEmComposicaoError(RuntimeError):
    """Pedido de avaliar SOZINHA uma decisao que e membro nao padrao de um grupo de `composicoes[]`."""


class _ComComposicoes(Protocol):
    """O pin (`tools.workers.rco_pin.RcoRulesPin`) ou o manifesto (`rco_manifesto.ManifestoK1`)."""

    @property
    def composicoes(self) -> Sequence[ComposicaoK1]: ...


@dataclass(frozen=True, slots=True)
class ResultadoComposicao:
    """O que a composicao decidiu. Em `revisao_humana`, `linhas` e vazio e `decisao_vencedora` e None."""

    composicao: str
    desfecho: str  # DESFECHO_ESCOLHIDO | DESFECHO_REVISAO_HUMANA
    linhas: tuple[Mapping[str, Any], ...] = ()
    decisao_vencedora: str | None = None
    #: `True` quando nenhuma decisao antes do padrao trouxe `idRegra` e valeu o padrao.
    pelo_padrao: bool = False
    motivo: str | None = None  # em MOTIVOS, so em revisao_humana
    #: As decisoes avaliadas, na ordem (todas, salvo falha do avaliador).
    avaliadas: tuple[str, ...] = field(default=())

    @property
    def revisao_humana(self) -> bool:
        return self.desfecho == DESFECHO_REVISAO_HUMANA


def composicao_de(pin: _ComComposicoes, decisao_id: str) -> ComposicaoK1 | None:
    """O grupo que contem `decisao_id` (o leitor garante no maximo um), ou None."""
    return next((c for c in pin.composicoes if decisao_id in c.decisoes), None)


def membro_nao_padrao(pin: _ComComposicoes, decisao_id: str) -> bool:
    """`True` se `decisao_id` esta num grupo de `composicoes[]` e NAO e o padrao dele."""
    grupo = composicao_de(pin, decisao_id)
    return grupo is not None and decisao_id != grupo.padrao


def exigir_avaliacao_isolada(pin: _ComComposicoes, decisao_id: str) -> None:
    """Guarda de quem avalia UMA decisao `rco_`: membro nao padrao de grupo levanta
    `DecisaoSoEmComposicaoError` (avalie o grupo com `compor`)."""
    if membro_nao_padrao(pin, decisao_id):
        grupo = composicao_de(pin, decisao_id)
        nome = grupo.nome if grupo is not None else ""
        raise DecisaoSoEmComposicaoError(
            f"decisao {decisao_id!r} e membro nao padrao da composicao {nome!r}: nunca e avaliada "
            "sozinha; avalie o grupo inteiro (rco_composicao.compor, DL-0174)"
        )


def _revisao(
    composicao: ComposicaoK1, motivo: str, codigo: str, decisoes: Sequence[str], avaliadas: Sequence[str]
) -> ResultadoComposicao:
    """Desfecho `revisao_humana`: log so com ids de decisao e codigo, e a metrica. Nunca glosa."""
    evento = "rco_composicao_violacao_prova" if motivo == MOTIVO_VIOLACAO_PROVA else "rco_composicao_revisao"
    logger.warning(evento, decisoes=list(decisoes), codigo=codigo)
    record_rco_composicao_revisao(motivo)
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_REVISAO_HUMANA,
        motivo=motivo,
        avaliadas=tuple(avaliadas),
    )


def _id_regra(linhas: tuple[Mapping[str, Any], ...]) -> str | None:
    """`idRegra` da unica linha (None/"" = ausente). Levanta `TypeError` se nao for texto."""
    if not linhas:
        return None
    valor = linhas[0].get(SAIDA_ID_REGRA)
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise TypeError("idRegra nao e texto")
    return valor


def escolher(composicao: ComposicaoK1, resultados: Mapping[str, Linhas]) -> ResultadoComposicao:
    """Aplica a regra do grupo aos resultados JA avaliados de TODAS as decisoes. Pura (salvo log/metrica).

    `resultados` tem de ter uma entrada por decisao do grupo; faltar alguma e `avaliador_falhou`
    (nunca resultado parcial).
    """
    ordem = composicao.decisoes
    avaliadas = tuple(d for d in ordem if d in resultados)
    if composicao.regra != REGRA_PRIMEIRA_COM_ID_REGRA:  # o leitor ja recusa; defesa em profundidade
        return _revisao(composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_REGRA_DESCONHECIDA, ordem, avaliadas)
    faltam = [d for d in ordem if d not in resultados]
    if faltam:
        return _revisao(composicao, MOTIVO_AVALIADOR_FALHOU, CODIGO_AVALIADOR_FALHOU, faltam, avaliadas)
    lidos: dict[str, tuple[tuple[Mapping[str, Any], ...], str | None]] = {}
    for d in ordem:
        bruto = resultados[d]
        if (
            not isinstance(bruto, Sequence)
            or isinstance(bruto, str | bytes)
            or not all(isinstance(r, Mapping) for r in bruto)
        ):
            return _revisao(composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d], avaliadas)
        linhas = tuple(bruto)
        if len(linhas) > 1:
            return _revisao(composicao, MOTIVO_VIOLACAO_PROVA, CODIGO_UNIQUE, [d], avaliadas)
        try:
            lidos[d] = (linhas, _id_regra(linhas))
        except TypeError:
            return _revisao(composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d], avaliadas)
    com_id = [d for d in composicao.anteriores if lidos[d][1] is not None]
    if len(com_id) > 1:
        return _revisao(composicao, MOTIVO_VIOLACAO_PROVA, CODIGO_EXCLUSIVIDADE, com_id, avaliadas)
    vencedora = com_id[0] if com_id else composicao.padrao
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_ESCOLHIDO,
        linhas=lidos[vencedora][0],
        decisao_vencedora=vencedora,
        pelo_padrao=not com_id,
        avaliadas=avaliadas,
    )


def _falhou(
    composicao: ComposicaoK1, decisao: str, exc: Exception, avaliadas: Sequence[str]
) -> ResultadoComposicao:
    logger.warning("rco_composicao_avaliador_falhou", decisoes=[decisao], codigo=type(exc).__name__)
    record_rco_composicao_revisao(MOTIVO_AVALIADOR_FALHOU)
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_REVISAO_HUMANA,
        motivo=MOTIVO_AVALIADOR_FALHOU,
        avaliadas=tuple(avaliadas),
    )


def compor(
    composicao: ComposicaoK1, avaliador: Avaliador, entradas: Mapping[str, Any]
) -> ResultadoComposicao:
    """Avalia TODAS as decisoes do grupo, na ordem, com as MESMAS entradas, e aplica a regra.

    Falha do avaliador (qualquer `Exception`) em qualquer decisao -> `revisao_humana`, sem avaliar as
    seguintes e sem resultado parcial. `BaseException` (cancelamento, saida) propaga.
    """
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Linhas] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, tuple(resultados))
    return escolher(composicao, resultados)


async def compor_async(
    composicao: ComposicaoK1, avaliador: AvaliadorAsync, entradas: Mapping[str, Any]
) -> ResultadoComposicao:
    """`compor` para avaliador assincrono (o `DmnTransport.evaluate` e assincrono). Mesma regra; em serie,
    na ordem do grupo."""
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Linhas] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = await avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, tuple(resultados))
    return escolher(composicao, resultados)


__all__ = [
    "Avaliador",
    "AvaliadorAsync",
    "CODIGO_EXCLUSIVIDADE",
    "CODIGO_UNIQUE",
    "DESFECHO_ESCOLHIDO",
    "DESFECHO_REVISAO_HUMANA",
    "DecisaoSoEmComposicaoError",
    "MOTIVOS",
    "MOTIVO_AVALIADOR_FALHOU",
    "MOTIVO_RESULTADO_INVALIDO",
    "MOTIVO_VIOLACAO_PROVA",
    "ResultadoComposicao",
    "composicao_de",
    "compor",
    "compor_async",
    "escolher",
    "exigir_avaliacao_isolada",
    "membro_nao_padrao",
]
